"""读 KX V3 总览。网络调用走可注入的 httpx 客户端，错误信息不带 URL（访问码在路径里）。"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import httpx

from neckline.kx import settings
from neckline.market.util import silence_http_logs

MAX_BYTES = 3_000_000
_PENDING_TRIES = 3


class KxError(Exception):
    """KX 不可达、返回了不合格的内容或还没取完数。message 是给人看的中文，不含 URL 与访问码。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def is_pending(body: object) -> bool:
    """正在取数：status 是 pending，或 code 是 refresh_pending。不是总览。"""
    return isinstance(body, dict) and (body.get("status") == "pending" or body.get("code") == "refresh_pending")


def retry_delay(body: object) -> float:
    """retry_after_seconds 夹在 1～5 秒；缺、非正数或不是数字时用 3 秒。布尔不算数字。"""
    raw = body.get("retry_after_seconds") if isinstance(body, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
        return 3.0
    return float(min(max(float(raw), 1.0), 5.0))


def _unavailable_message(body: object) -> str:
    raw = body.get("message") if isinstance(body, dict) else None
    text = raw.strip() if isinstance(raw, str) else ""
    return (text[:200] or "KX 看板暂时不可用。")


class KxSource:
    """真实的 KX 读取。client 可注入（测试用 MockTransport），默认自己建并在 aclose 时关掉。"""

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
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), trust_env=False,
                                            follow_redirects=False)
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

    async def _read_once(self) -> object:
        key = settings.read_key()
        if key is None:
            raise KxError("kx_read_key_missing", "没有配置 KX 只读页面的访问码，无法读取看板。")
        status, body = await self._get_json(f"{settings.base_url()}/kx/read/{key}", {"format": "json"})
        if status == 404:
            raise KxError("kx_read_key_rejected", "KX 不认这枚只读访问码。")
        if is_pending(body):
            return body
        if isinstance(body, dict) and body.get("status") == "unavailable":
            raise KxError("kx_unavailable", _unavailable_message(body))
        if status != 200:
            raise KxError("kx_unavailable", f"KX 只读页面返回 HTTP {status}。")
        return body

    async def overview(self, *, sleep: Callable[[float], Awaitable[None]] | None = None) -> object:
        """读总览。pending 时按间隔再读，最多再读 3 次；仍然 pending 算这次失败。"""
        pause = sleep or _asleep
        body = await self._read_once()
        extra = 0
        while is_pending(body) and extra < _PENDING_TRIES:
            await pause(retry_delay(body))
            body = await self._read_once()
            extra += 1
        if is_pending(body):
            raise KxError("kx_pending", "KX 还在取数，这次没有读到看板。")
        return body


async def _asleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


__all__ = ["KxError", "KxSource", "is_pending", "retry_delay"]
