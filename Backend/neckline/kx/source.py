"""读 KX：公开健康检查与只读页面里的名单。网络调用都走可注入的 httpx 客户端，错误信息不带 URL（访问码在路径里）。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

from neckline.kx import settings
from neckline.kx.store import Member
from neckline.market.util import silence_http_logs

_VERSION = re.compile(r"[A-Za-z0-9._-]{1,80}")
_CODE = re.compile(r"[036]\d{5}")
MAX_BYTES = 3_000_000
MAX_MEMBERS = 500


class KxError(Exception):
    """KX 不可达、返回了不合格的内容或还没取完数。message 是给人看的中文，不含 URL 与访问码。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class Health:
    version: str
    count: int


@dataclass(frozen=True)
class PoolDocument:
    version: str
    members: tuple[Member, ...]


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_MEMBERS


def parse_health(body: object) -> Health:
    if not isinstance(body, dict) or body.get("status") != "ok":
        raise KxError("kx_unhealthy", "KX 健康检查没有报告正常。")
    version, count = body.get("pool_version"), body.get("pool_count")
    if not isinstance(version, str) or not _VERSION.fullmatch(version) or not _is_count(count):
        raise KxError("kx_bad_health", "KX 健康检查的名单版本或数量不合格。")
    return Health(version, count)


def _industry(value: object) -> str | None:
    if not isinstance(value, list) or not value:
        return None
    names: list[str] = []
    for item in value:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            return None
        if name.strip() not in names:
            names.append(name.strip())
    return "、".join(names)


def parse_pool(body: object, health: Health) -> PoolDocument:
    """严格校验：代码格式、不重复、名称与行业齐全，数量和版本号与健康检查一致。"""
    bad = KxError("kx_bad_pool", "KX 名单格式不合格，没有采用。")
    if not isinstance(body, dict) or body.get("status") not in ("ok", "partial"):
        raise bad
    meta, rows = body.get("pool"), body.get("rows")
    if not isinstance(meta, dict) or not isinstance(rows, list):
        raise bad
    version, count = meta.get("version"), meta.get("count")
    if version != health.version or count != health.count or isinstance(count, bool):
        raise KxError("kx_pool_changed", "读取 KX 名单时版本或数量与健康检查不一致，稍后再核对。")
    if len(rows) != count:
        raise bad
    members: list[Member] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise bad
        code, name, industry = row.get("stock_code"), row.get("name"), _industry(row.get("sw_l2"))
        if (not isinstance(code, str) or not _CODE.fullmatch(code) or code in seen or not isinstance(name, str)
                or not name.strip() or len(name.strip()) > 80 or industry is None):
            raise bad
        seen.add(code)
        members.append(Member(code, name.strip(), industry, index))
    return PoolDocument(version, tuple(members))


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
                chunks, size = [], 0
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

    async def health(self) -> Health:
        status, body = await self._get_json(settings.base_url() + "/kx/health")
        if status != 200:
            raise KxError("kx_unhealthy", f"KX 健康检查返回 HTTP {status}。")
        return parse_health(body)

    async def pool_page(self) -> object:
        """读 KX 只读页面的 JSON。返回原始内容；盘中可能先给「正在取数」，由调用方识别后重试。"""
        key = settings.read_key()
        if key is None:
            raise KxError("kx_read_key_missing", "没有配置 KX 只读页面的访问码，无法读取名单。")
        status, body = await self._get_json(f"{settings.base_url()}/kx/read/{key}", {"format": "json"})
        if status == 404:
            raise KxError("kx_read_key_rejected", "KX 不认这枚只读访问码。")
        if status != 200 and not (isinstance(body, dict) and body.get("status") == "pending"):
            raise KxError("kx_unavailable", f"KX 只读页面返回 HTTP {status}。")
        return body


def is_pending(body: object) -> bool:
    """KX 只读页面「正在取数」的返回：status 是 pending，不是名单。"""
    return isinstance(body, dict) and (body.get("status") == "pending" or body.get("code") == "refresh_pending")


def retry_delay(body: object) -> float:
    raw = body.get("retry_after_seconds") if isinstance(body, dict) else None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return float(min(max(raw, 1), 5))
    return 3.0


__all__ = ["Health", "KxError", "KxSource", "PoolDocument", "is_pending", "parse_health", "parse_pool", "retry_delay"]
