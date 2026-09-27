"""Narrow Jin10 Streamable HTTP client for the five B92 evidence tools.

The provider result schema is deliberately validated by callers. This module
never treats an HTTP 200 or an empty text block as a successful business page.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.parse import urlsplit
from typing import Any, Callable

import httpx


TOOLS = frozenset({"list_flash", "list_news", "get_news", "search_flash", "search_news"})
PROTOCOL_VERSION = "2025-11-25"
ENDPOINT = "https://mcp.jin10.com/mcp"


class Jin10Error(RuntimeError):
    def __init__(self, code: str, *, unknown: bool = False, pre_send: bool = False):
        super().__init__(code)
        self.code = code
        self.unknown = unknown
        self.pre_send = pre_send


def _allowed_endpoint(url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname == "mcp.jin10.com"
            and parsed.port in (None, 443) and parsed.path == "/mcp"
            and not parsed.username and not parsed.password and not parsed.query
            and not parsed.fragment)


class Jin10Client:
    def __init__(self, *, token: str | None, endpoint: str = ENDPOINT,
                 protocol_version: str = PROTOCOL_VERSION, timeout_seconds: float,
                 transport: httpx.BaseTransport | None = None):
        if not _allowed_endpoint(endpoint):
            raise ValueError("Jin10 endpoint 必须是获准的 HTTPS MCP origin")
        if protocol_version != PROTOCOL_VERSION:
            raise ValueError("Jin10 MCP 协议版本未经实测")
        if token is not None and (not isinstance(token, str) or not token.strip()):
            raise ValueError("Jin10 token 未配置")
        if timeout_seconds <= 0:
            raise ValueError("Jin10 timeout 必须为正数")
        # A missing credential still permits an exact durable paid-reply
        # lookup by endpoint/protocol/wire identity. Any new HTTP request is
        # rejected before headers are constructed.
        self._token = token.strip() if token is not None else None
        self.endpoint = endpoint
        self.protocol_version = protocol_version
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._session: str | None = None
        self._next_id = 0
        self._schemas: dict[str, Mapping[str, Any]] | None = None
        self._admission_guard: Callable[[], None] | None = None
        self._client = httpx.Client(transport=transport, timeout=timeout_seconds, follow_redirects=False)

    def close(self) -> None:
        # Session IDs and bearer tokens never enter persisted results or logs.
        self._client.close()

    @property
    def has_credential(self) -> bool:
        return self._token is not None

    def __enter__(self) -> "Jin10Client":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _headers(self, *, initialized: bool) -> dict[str, str]:
        if self._token is None:
            raise Jin10Error("credential_missing", pre_send=True)
        headers = {"Authorization": f"Bearer {self._token}",
                   "Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        if initialized:
            headers["MCP-Protocol-Version"] = self.protocol_version
            if self._session is not None:
                headers["MCP-Session-Id"] = self._session
        return headers

    def _post(self, body: Mapping[str, Any], *, initialized: bool) -> tuple[dict[str, Any] | None, Mapping[str, str]]:
        # A pause can win between MCP initialize, tools/list, and tools/call.
        # Recheck immediately before *each* new HTTP request, including the
        # read-only schema restoration after an already paid reply is replayed.
        if self._admission_guard is not None:
            self._admission_guard()
        try:
            with self._client.stream("POST", self.endpoint, json=dict(body),
                                     headers=self._headers(initialized=initialized)) as response:
                headers = dict(response.headers)
                if response.is_redirect:
                    raise Jin10Error("redirect_rejected")
                if response.status_code == 401:
                    raise Jin10Error("unauthorized")
                if response.status_code == 429:
                    raise Jin10Error("rate_limited")
                if response.status_code == 404 and initialized and self._session:
                    self._session = None
                    self._schemas = None
                    raise Jin10Error("session_expired")
                if response.status_code >= 400:
                    raise Jin10Error(f"http_{response.status_code}")
                if "id" not in body:
                    if response.status_code != 202 or response.read():
                        raise Jin10Error("notification_response_invalid")
                    return None, headers
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type == "application/json":
                    try:
                        message = json.loads(response.read())
                    except ValueError as exc:
                        raise Jin10Error("json_invalid", unknown=body.get("method") == "tools/call") from exc
                    if not isinstance(message, dict):
                        raise Jin10Error("jsonrpc_response_invalid", unknown=body.get("method") == "tools/call")
                    return message, headers
                if content_type == "text/event-stream":
                    data: list[str] = []
                    for line in response.iter_lines():
                        if line.startswith("data:"):
                            data.append(line[5:].lstrip())
                        elif not line.strip() and data:
                            if not any(part.strip() for part in data):
                                data.clear()
                                continue
                            try:
                                event = json.loads("\n".join(data))
                            except ValueError as exc:
                                raise Jin10Error("sse_json_invalid", unknown=body.get("method") == "tools/call") from exc
                            data.clear()
                            if isinstance(event, dict) and event.get("id") == body["id"]:
                                return event, headers
                    raise Jin10Error("sse_response_unknown", unknown=body.get("method") == "tools/call")
                raise Jin10Error("content_type_invalid", unknown=body.get("method") == "tools/call")
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # A POST may have reached the server. Never auto-replay a paid tool.
            raise Jin10Error("transport_unknown", unknown=body.get("method") == "tools/call") from exc

    def _request(self, method: str, params: Mapping[str, Any] | None = None, *,
                 initialized: bool = True) -> tuple[Mapping[str, Any], Mapping[str, str]]:
        self._next_id += 1
        request_id = self._next_id
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            body["params"] = dict(params)
        message, headers = self._post(body, initialized=initialized)
        if message is None or message.get("jsonrpc") != "2.0" or message.get("id") != request_id:
            raise Jin10Error("jsonrpc_id_mismatch", unknown=method == "tools/call")
        if "error" in message:
            raise Jin10Error("jsonrpc_error")
        result = message.get("result")
        if not isinstance(result, Mapping):
            raise Jin10Error("jsonrpc_result_invalid", unknown=method == "tools/call")
        return result, headers

    def _initialize(self) -> None:
        result, headers = self._request(
            "initialize",
            {"protocolVersion": self.protocol_version,
             "capabilities": {},
             "clientInfo": {"name": "Neckline", "version": "3.6.1"}},
            initialized=False)
        if result.get("protocolVersion") != self.protocol_version:
            raise Jin10Error("protocol_version_mismatch")
        session = headers.get("mcp-session-id")
        if session is not None and (not session or any(ord(char) < 0x21 or ord(char) > 0x7e for char in session)):
            raise Jin10Error("session_id_invalid")
        self._session = session
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, initialized=True)

    def _discover(self) -> None:
        if self._schemas is not None:
            return
        self._initialize()
        schemas: dict[str, Mapping[str, Any]] = {}
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(20):
            result, _ = self._request("tools/list", {"cursor": cursor} if cursor else {})
            tools = result.get("tools")
            if not isinstance(tools, list):
                raise Jin10Error("tools_list_invalid")
            for item in tools:
                if not isinstance(item, Mapping) or not isinstance(item.get("name"), str) or not isinstance(item.get("inputSchema"), Mapping):
                    raise Jin10Error("tool_schema_invalid")
                if item["name"] in TOOLS:
                    schemas[item["name"]] = item["inputSchema"]
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                self._schemas = schemas
                return
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
                raise Jin10Error("tools_list_cursor_invalid")
            seen.add(next_cursor)
            cursor = next_cursor
        raise Jin10Error("tools_list_limit")

    def call_tool_raw(self, name: str, arguments: Mapping[str, Any], *,
                      admission_guard: Callable[[], None] | None = None) -> Mapping[str, Any]:
        if name not in TOOLS:
            raise ValueError("Jin10 工具不在 B92 allowlist")
        if not isinstance(arguments, Mapping):
            raise ValueError("Jin10 工具参数必须是对象")
        previous_guard, self._admission_guard = self._admission_guard, admission_guard
        try:
            try:
                self._discover()
            except Jin10Error as exc:
                exc.pre_send = True
                raise
            assert self._schemas is not None
            if name not in self._schemas:
                raise Jin10Error("tool_unavailable", pre_send=True)
            schema = self._schemas[name]
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            if (schema.get("type") != "object" or not isinstance(properties, Mapping)
                    or not isinstance(required, list)
                    or any(not isinstance(item, str) for item in required)
                    or any(item not in arguments for item in required)):
                raise Jin10Error("tool_schema_incompatible", pre_send=True)
            for key, value in arguments.items():
                definition = properties.get(key)
                if not isinstance(definition, Mapping) or definition.get("type") != "string" or not isinstance(value, str):
                    raise Jin10Error("tool_schema_incompatible", pre_send=True)
            result, _ = self._request("tools/call", {"name": name, "arguments": dict(arguments)})
            return result
        finally:
            self._admission_guard = previous_guard

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        result = self.call_tool_raw(name, arguments)
        if result.get("isError") is True:
            raise Jin10Error("tool_is_error")
        structured = result.get("structuredContent")
        if not isinstance(structured, Mapping):
            raise Jin10Error("structured_content_missing")
        status = structured.get("status")
        if status is not None and status not in {"success", "ok", 200, "200"}:
            raise Jin10Error("business_status_error")
        return structured

    def pagination_argument(self, name: str, *,
                            admission_guard: Callable[[], None] | None = None) -> str | None:
        """Use the input schema, restoring it after a durable paid-reply replay.

        The replay path deliberately skips ``call_tool_raw`` and therefore
        starts with no in-memory schema. Discovery is MCP control traffic and
        never resends the paid ``tools/call`` whose reply is already settled.
        """
        if name not in TOOLS:
            raise ValueError("Jin10 工具不在 B92 allowlist")
        if self._schemas is None:
            previous_guard, self._admission_guard = self._admission_guard, admission_guard
            try:
                self._discover()
            finally:
                self._admission_guard = previous_guard
        schema = self._schemas.get(name) if self._schemas is not None else None
        properties = schema.get("properties") if isinstance(schema, Mapping) else None
        if not isinstance(properties, Mapping):
            return None
        for field in ("cursor", "offset"):
            definition = properties.get(field)
            if isinstance(definition, Mapping) and definition.get("type") == "string":
                return field
        return None


__all__ = ["ENDPOINT", "PROTOCOL_VERSION", "TOOLS", "Jin10Client", "Jin10Error"]
