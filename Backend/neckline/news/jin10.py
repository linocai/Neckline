"""金十 MCP（Streamable HTTP）客户端与快讯、资讯列表的翻页取数。

列表按时间倒序分页：从最新一页往回翻，直到最旧一条早于起点、没有下一页，或碰到翻页上限。
没翻到起点就停下的，如实记为不完整（history_unavailable / page_limit），不当作「没有消息」。
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

import neckline

from .model import SourceDocument, SourceOutcome

TOOLS = frozenset({"list_flash", "list_news"})
PROTOCOL_VERSION = "2025-11-25"
ENDPOINT = "https://mcp.jin10.com/mcp"
SOURCE_TOOLS = {"jin10-flash": "list_flash", "jin10-news": "list_news"}


class Jin10Error(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _allowed_endpoint(url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname == "mcp.jin10.com" and parsed.port in (None, 443)
            and parsed.path == "/mcp" and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment)


class Jin10Client:
    def __init__(self, *, token: str, endpoint: str = ENDPOINT, protocol_version: str = PROTOCOL_VERSION,
                 timeout_seconds: float, transport: httpx.BaseTransport | None = None) -> None:
        if not _allowed_endpoint(endpoint):
            raise ValueError("金十 endpoint 必须是获准的 HTTPS MCP 地址")
        if protocol_version != PROTOCOL_VERSION:
            raise ValueError("金十 MCP 协议版本未经实测")
        if not isinstance(token, str) or not token.strip():
            raise Jin10Error("credential_missing")
        self._token = token.strip()
        self.endpoint = endpoint
        self.protocol_version = protocol_version
        self._session: str | None = None
        self._next_id = 0
        self._schemas: dict[str, Mapping[str, Any]] | None = None
        self._client = httpx.Client(transport=transport, timeout=timeout_seconds, follow_redirects=False)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Jin10Client":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _headers(self, *, initialized: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        if initialized:
            headers["MCP-Protocol-Version"] = self.protocol_version
            if self._session is not None:
                headers["MCP-Session-Id"] = self._session
        return headers

    def _post(self, body: Mapping[str, Any], *, initialized: bool) -> tuple[dict[str, Any] | None, Mapping[str, str]]:
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
                        raise Jin10Error("json_invalid") from exc
                    if not isinstance(message, dict):
                        raise Jin10Error("jsonrpc_response_invalid")
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
                                raise Jin10Error("sse_json_invalid") from exc
                            data.clear()
                            if isinstance(event, dict) and event.get("id") == body["id"]:
                                return event, headers
                    raise Jin10Error("sse_response_unknown")
                raise Jin10Error("content_type_invalid")
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise Jin10Error("transport_error") from exc

    def _request(self, method: str, params: Mapping[str, Any] | None = None, *,
                 initialized: bool = True) -> tuple[Mapping[str, Any], Mapping[str, str]]:
        self._next_id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            body["params"] = dict(params)
        message, headers = self._post(body, initialized=initialized)
        if message is None or message.get("jsonrpc") != "2.0" or message.get("id") != self._next_id:
            raise Jin10Error("jsonrpc_id_mismatch")
        if "error" in message:
            raise Jin10Error("jsonrpc_error")
        result = message.get("result")
        if not isinstance(result, Mapping):
            raise Jin10Error("jsonrpc_result_invalid")
        return result, headers

    def _discover(self) -> None:
        if self._schemas is not None:
            return
        result, headers = self._request("initialize", {"protocolVersion": self.protocol_version, "capabilities": {},
                                                       "clientInfo": {"name": "Neckline", "version": neckline.__version__}},
                                        initialized=False)
        if result.get("protocolVersion") != self.protocol_version:
            raise Jin10Error("protocol_version_mismatch")
        session = headers.get("mcp-session-id")
        if session is not None and (not session or any(ord(c) < 0x21 or ord(c) > 0x7e for c in session)):
            raise Jin10Error("session_id_invalid")
        self._session = session
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, initialized=True)
        schemas: dict[str, Mapping[str, Any]] = {}
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(20):
            listed, _ = self._request("tools/list", {"cursor": cursor} if cursor else {})
            tools = listed.get("tools")
            if not isinstance(tools, list):
                raise Jin10Error("tools_list_invalid")
            for item in tools:
                if isinstance(item, Mapping) and item.get("name") in TOOLS and isinstance(item.get("inputSchema"), Mapping):
                    schemas[item["name"]] = item["inputSchema"]
            next_cursor = listed.get("nextCursor")
            if next_cursor is None:
                self._schemas = schemas
                return
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
                raise Jin10Error("tools_list_cursor_invalid")
            seen.add(next_cursor)
            cursor = next_cursor
        raise Jin10Error("tools_list_limit")

    def pagination_argument(self, name: str) -> str | None:
        self._discover()
        schema = (self._schemas or {}).get(name)
        properties = schema.get("properties") if isinstance(schema, Mapping) else None
        if isinstance(properties, Mapping):
            for field in ("cursor", "offset"):
                definition = properties.get(field)
                if isinstance(definition, Mapping) and definition.get("type") == "string":
                    return field
        return None

    def call_tool(self, name: str, arguments: Mapping[str, str]) -> Mapping[str, Any]:
        if name not in TOOLS:
            raise ValueError("金十工具不在允许清单内")
        self._discover()
        if name not in (self._schemas or {}):
            raise Jin10Error("tool_unavailable")
        result, _ = self._request("tools/call", {"name": name, "arguments": dict(arguments)})
        if result.get("isError") is True:
            raise Jin10Error("tool_is_error")
        structured = result.get("structuredContent")
        if not isinstance(structured, Mapping):
            raise Jin10Error("structured_content_missing")
        status = structured.get("status")
        if status is not None and status not in {"success", "ok", 200, "200"}:
            raise Jin10Error("business_status_error")
        return structured


def page_rows(structured: Mapping[str, Any]) -> tuple[list[Any], str | None, bool]:
    data = structured.get("data")
    if not isinstance(data, Mapping) or not isinstance(data.get("items"), list):
        raise Jin10Error("page_shape_invalid")
    more = data.get("has_more")
    if not isinstance(more, bool):
        raise Jin10Error("page_has_more_missing")
    # 最后一页的游标是空串，只在 has_more 明确为 false 时才算翻页结束。
    cursors = [data[f] for f in ("next_offset", "next_cursor", "cursor")
               if f in data and data[f] is not None and not (more is False and data[f] == "")]
    if any(not isinstance(v, str) or not v for v in cursors) or len(set(cursors)) > 1:
        raise Jin10Error("page_cursor_invalid")
    cursor = cursors[0] if cursors else None
    if more and cursor is None:
        raise Jin10Error("page_cursor_missing")
    return data["items"], cursor, more


def normalize_item(source_key: str, item: Mapping[str, Any], fetched_at: datetime) -> SourceDocument:
    url = item.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise Jin10Error("item_url_missing")
    provider_id = item.get("id") if isinstance(item.get("id"), str) and item.get("id") else None
    title = item.get("title")
    if title is not None and not isinstance(title, str):
        raise Jin10Error("item_title_invalid")
    title = title.strip() if isinstance(title, str) and title.strip() else None
    text = item.get("content")
    intro = item.get("intro") or item.get("introduction")
    if (text is not None and not isinstance(text, str)) or (intro is not None and not isinstance(intro, str)):
        raise Jin10Error("item_text_invalid")
    if source_key == "jin10-flash" and not (text and text.strip()):
        raise Jin10Error("flash_content_missing")
    if source_key == "jin10-news" and not (text or intro or title):
        raise Jin10Error("article_summary_missing")
    raw_time = item.get("time")
    if not isinstance(raw_time, str) or not raw_time:
        raise Jin10Error("item_time_missing")
    try:
        parsed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    published = parsed if parsed is not None and parsed.tzinfo is not None else None
    body = text.strip() if text and text.strip() else None
    return SourceDocument(
        source_key=source_key, external_id=provider_id or url, url=url, title=title, original_text=body,
        excerpt=(intro.strip() if isinstance(intro, str) and intro.strip() else None),
        published_at=published, published_precision="exact" if published else "unknown", fetched_at=fetched_at,
        metadata={"provider": "Jin10", "providerId": provider_id, "sourceKind": "flash" if source_key == "jin10-flash" else "article",
                  "originalPublishedText": raw_time})


def fetch_list(client: Jin10Client, source_key: str, *, start: datetime, end: datetime, max_pages: int,
               clock: Callable[[], datetime]) -> SourceOutcome:
    tool = SOURCE_TOOLS[source_key]
    outcome = SourceOutcome(source_key=source_key, requested_start=start, requested_end=end)
    cursor: str | None = None
    field: str | None = None
    try:
        while True:
            if outcome.requests >= max_pages:
                outcome.error = "page_limit"
                return outcome
            structured = client.call_tool(tool, {field: cursor} if cursor and field else {})
            outcome.requests += 1
            rows, next_cursor, has_more = page_rows(structured)
            fetched_at = clock()
            for row in rows:
                try:
                    if not isinstance(row, Mapping):
                        raise Jin10Error("page_item_invalid")
                    document = normalize_item(source_key, row, fetched_at)
                except Jin10Error:
                    outcome.rejected += 1
                    continue
                outcome.documents.append(document)
                if document.published_at and (outcome.oldest_seen is None or document.published_at < outcome.oldest_seen):
                    outcome.oldest_seen = document.published_at
            if outcome.oldest_seen is not None and outcome.oldest_seen <= start:
                outcome.complete = True
                return outcome
            if not has_more:
                outcome.error = "history_unavailable"  # 来源没有更早的可见内容，起点之前的部分取不到
                return outcome
            field = field or client.pagination_argument(tool)
            if field not in ("cursor", "offset"):
                outcome.error = "pagination_parameter_unavailable"
                return outcome
            if next_cursor == cursor:
                outcome.error = "cursor_not_advanced"
                return outcome
            cursor = next_cursor
    except Jin10Error as exc:
        outcome.error = exc.code
        return outcome


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
