"""读 Qihuo V2 单文件只读 JSON（schema_version: 3），支持 ETag 条件请求；错误文案绝不带 URL 与访问码。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

from neckline.market.util import silence_http_logs
from neckline.qihuo import settings

MAX_BYTES = 3_000_000
_UNAVAILABLE = "Q 期货数据暂时不可用。"
_URL = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", re.IGNORECASE)
_READ_SEGMENT = re.compile(r"/qihuo/read/[A-Za-z0-9_-]+")
_HIDDEN = "…"


class QihuoError(Exception):
    """Qihuo 不可达或返回不合格内容。message 为脱敏中文，不含 URL 与访问码。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class QihuoFetchResult:
    not_modified: bool
    body: object | None
    etag: str | None


def redact_upstream(text: str) -> str:
    """去掉读取地址、`/qihuo/read/` 路径段与当前访问码，洗空时回退固定说明。"""
    cleaned = _READ_SEGMENT.sub("", _URL.sub("", text))
    key = settings.read_key()
    if key:
        cleaned = cleaned.replace(key, _HIDDEN)
        if key in cleaned:
            return _UNAVAILABLE
    cleaned = cleaned.strip()
    return cleaned[:200] or _UNAVAILABLE


class QihuoSource:
    """真实的 Qihuo 只读 JSON 客户端，支持 ETag / If-None-Match 与可注入 httpx.AsyncClient。"""

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

    async def fetch(self, *, etag: str | None = None) -> QihuoFetchResult:
        if settings.validated_base_url() is None:
            raise QihuoError("qihuo_config_invalid", "Qihuo 服务地址配置无效，无法读取期货看板。")
        url = settings.read_url()
        if url is None:
            raise QihuoError("qihuo_read_key_missing", "没有配置 Qihuo 只读访问码，无法读取期货看板。")
        headers = {"If-None-Match": etag} if etag else {}
        client = await self._http()
        try:
            async with client.stream("GET", url, headers=headers) as response:
                status = response.status_code
                resp_etag = (response.headers.get("etag") or "").strip() or None
                if status == 304:
                    return QihuoFetchResult(not_modified=True, body=None, etag=resp_etag or etag)
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise QihuoError("qihuo_oversized", "Qihuo 返回的数据包过大，已停止读取。")
                    chunks.append(chunk)
        except QihuoError:
            raise
        except (httpx.HTTPError, ValueError):
            raise QihuoError("qihuo_unreachable", "Qihuo 暂时连不上。") from None

        if status == 404:
            raise QihuoError("qihuo_read_key_rejected", "Qihuo 不认这枚只读访问码。")
        if status != 200:
            raise QihuoError("qihuo_unavailable", f"Qihuo 只读接口返回 HTTP {status}。")
        try:
            body = json.loads(b"".join(chunks).decode("utf-8", errors="replace"))
        except ValueError:
            raise QihuoError("qihuo_bad_json", "Qihuo 没有返回可解析的 JSON。") from None
        return QihuoFetchResult(not_modified=False, body=body, etag=resp_etag)


__all__ = ["MAX_BYTES", "QihuoError", "QihuoFetchResult", "QihuoSource", "redact_upstream"]
