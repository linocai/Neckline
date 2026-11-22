"""读 KX V4 总览（schema_version 5）。盘中静默、永不返回 pending；错误信息不带 URL 与访问码。"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable

import httpx

from neckline.kx import settings
from neckline.market.util import silence_http_logs

MAX_BYTES = 3_000_000
_UNAVAILABLE = "KX 看板暂时不可用。"
# 只吃 URL 里能出现的 ASCII 字符，后面紧跟的中文说明留着。
_URL = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", re.IGNORECASE)
_READ_SEGMENT = re.compile(r"/kx/read/[A-Za-z0-9_-]+")
_HIDDEN = "…"  # 访问码的替换符，不在访问码的字符集里，删掉之后前后两段拼不回访问码


class KxError(Exception):
    """KX 不可达或返回了不合格的内容。message 是给人看的中文，不含 URL 与访问码。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def redact_upstream(text: str) -> str:
    """去掉读取地址、`/kx/read/` 后的路径段和当前访问码。洗完没有字就用固定说明。"""
    cleaned = _READ_SEGMENT.sub("", _URL.sub("", text))
    key = settings.read_key()
    if key:
        cleaned = cleaned.replace(key, _HIDDEN)
        if key in cleaned:
            return _UNAVAILABLE
    cleaned = cleaned.strip()
    return (cleaned[:200] or _UNAVAILABLE)


def _unavailable_message(body: object) -> str:
    raw = body.get("message") if isinstance(body, dict) else None
    text = raw.strip() if isinstance(raw, str) else ""
    return redact_upstream(text) if text else _UNAVAILABLE


class KxSource:
    """真实的 KX 只读 JSON 读取。client 可注入（测试用 MockTransport），默认自己建并在 aclose 时关掉。"""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client
        self._owns = client is None
        silence_http_logs()

    async def aclose(self) -> None:
        if self._owns and self.client is not None:
            await self.client.aclose()
            self.client = None

    async def _http(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(15, connect=5),
                trust_env=False,
                follow_redirects=False,
            )
            self._owns = True
        return self.client

    async def _get_json(self, url: str, params: dict | None = None) -> tuple[int, object]:
        client = await self._http()
        try:
            async with client.stream("GET", url, params=params) as response:
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise KxError("kx_oversized", "KX 返回的内容过大，已停止读取。")
                    chunks.append(chunk)
                status = response.status_code
        except KxError:
            raise
        except httpx.HTTPError:
            raise KxError("kx_unreachable", "KX 暂时连不上。") from None
        try:
            return status, json.loads(b"".join(chunks).decode("utf-8", errors="replace"))
        except ValueError:
            raise KxError("kx_bad_json", "KX 没有返回可解析的内容。") from None

    async def overview(self, *, sleep: Callable[[float], Awaitable[None]] | None = None) -> object:
        """读 KX V4 总览 JSON。V4 零外调、永不返回 pending（保留 sleep 关键字参数兼容调用方）。"""
        del sleep
        key = settings.read_key()
        if key is None:
            raise KxError("kx_read_key_missing", "没有配置 KX 只读页面的访问码，无法读取看板。")
        status, body = await self._get_json(f"{settings.base_url()}/kx/read/{key}", {"format": "json"})
        if status == 404:
            raise KxError("kx_read_key_rejected", "KX 不认这枚只读访问码。")
        if isinstance(body, dict) and body.get("status") == "unavailable":
            raise KxError("kx_unavailable", _unavailable_message(body))
        if status != 200:
            raise KxError("kx_unavailable", f"KX 只读页面返回 HTTP {status}。")
        return body


__all__ = ["KxError", "KxSource", "MAX_BYTES", "redact_upstream"]
