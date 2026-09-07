"""Bounded, allowlisted publication-time metadata reads for K10 sources."""
from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable, Mapping, Protocol
from urllib.parse import urlsplit


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class MetadataTransport(Protocol):
    def __call__(self, url: str, *, timeout_seconds: float, max_bytes: int) -> TransportResponse: ...


@dataclass(frozen=True)
class PublicationMetadata:
    published_at: str | None
    precision: str
    fetched_at: str
    metadata: Mapping[str, object]


class _MetadataHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: list[tuple[str, str]] = []
        self._json_ld = False
        self._script: list[str] = []
        self.json_ld: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {str(key).lower(): str(value) for key, value in attrs if value is not None}
        if tag.lower() == "meta":
            name = values.get("property", values.get("name", "")).lower()
            content = values.get("content")
            if name in {"article:published_time", "bytedance:published_time"} and content:
                self.meta.append((name, content))
        elif tag.lower() == "script" and values.get("type", "").lower() == "application/ld+json":
            self._json_ld, self._script = True, []

    def handle_data(self, data: str) -> None:
        if self._json_ld:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._json_ld:
            self.json_ld.append("".join(self._script))
            self._json_ld, self._script = False, []


def _json_dates(value: object) -> list[str]:
    if isinstance(value, Mapping):
        output = [item for key, item in value.items() if key == "datePublished" and isinstance(item, str)]
        for item in value.values(): output.extend(_json_dates(item))
        return output
    if isinstance(value, list):
        return [date for item in value for date in _json_dates(item)]
    return []


def _timestamp(value: str) -> datetime | None:
    try: parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError: return None
    return parsed if parsed.tzinfo is not None else None


class PublicationMetadataResolver:
    def __init__(self, *, allowed_https_hosts: set[str], max_requests: int, timeout_seconds: float,
                 max_bytes: int, transport: MetadataTransport, clock: Callable[[], datetime]) -> None:
        if not allowed_https_hosts or max_requests < 1 or timeout_seconds <= 0 or max_bytes < 1:
            raise ValueError("元数据读取器边界参数无效")
        self._hosts = {host.lower().strip() for host in allowed_https_hosts if host.strip()}
        self._max_requests, self._timeout, self._max_bytes = max_requests, timeout_seconds, max_bytes
        self._transport, self._clock, self._requests = transport, clock, 0

    def _result(self, *, url: str, reason: str, status: int | None = None, values: list[str] | None = None) -> PublicationMetadata:
        now = self._clock()
        if now.tzinfo is None: raise ValueError("clock 必须返回带时区时间")
        parsed = [(value, _timestamp(value)) for value in values or []]
        exact = [(value, item) for value, item in parsed if item is not None]
        selected = max(exact, key=lambda item: item[1])[1] if exact else None
        return PublicationMetadata(
            published_at=selected.isoformat() if selected else None,
            precision="exact" if selected else "unknown", fetched_at=now.astimezone(timezone.utc).isoformat(timespec="seconds"),
            metadata={"url": url, "httpStatus": status, "reason": reason, "publishedValues": list(values or [])},
        )

    def resolve(self, url: str) -> PublicationMetadata:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError: return self._result(url=url, reason="invalid_url")
        host = (parsed.hostname or "").lower()
        try:
            address = ipaddress.ip_address(host)
            private = address.is_private or address.is_loopback or address.is_link_local
        except ValueError: private = False
        if parsed.scheme != "https" or parsed.username or parsed.password or port not in {None, 443} or private or host not in self._hosts:
            return self._result(url=url, reason="target_rejected")
        if self._requests >= self._max_requests:
            return self._result(url=url, reason="request_limit")
        self._requests += 1
        try: response = self._transport(url, timeout_seconds=self._timeout, max_bytes=self._max_bytes)
        except Exception:
            return self._result(url=url, reason="transport_failed")
        if response.status in {301, 302, 303, 307, 308}:
            return self._result(url=url, status=response.status, reason="redirect_rejected")
        if response.status != 200:
            return self._result(url=url, status=response.status, reason="http_status")
        if len(response.body) > self._max_bytes:
            return self._result(url=url, status=response.status, reason="byte_limit")
        parser = _MetadataHTML()
        try: parser.feed(response.body.decode("utf-8", errors="replace"))
        except Exception: return self._result(url=url, status=response.status, reason="html_parse_failed")
        values = [value for _, value in parser.meta]
        for script in parser.json_ld:
            try: values.extend(_json_dates(json.loads(script)))
            except json.JSONDecodeError: continue
        return self._result(url=url, status=response.status, reason="published_time_found" if any(_timestamp(value) for value in values) else "published_time_unavailable", values=values)


__all__ = ["MetadataTransport", "PublicationMetadata", "PublicationMetadataResolver", "TransportResponse"]
