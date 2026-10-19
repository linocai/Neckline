"""KX 名单同步：健康检查缓存 60 秒，版本变了才拉名单，校验后整体替换；KX 不可达时用上一份并标明未核对。"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from neckline.kx import store
from neckline.kx.source import Health, KxError, KxSource, PoolDocument, is_pending, parse_pool, retry_delay
from neckline.morning import db

HEALTH_TTL = 60.0       # 健康检查结果缓存
HEALTH_FAIL_TTL = 15.0  # 连不上时短一些，KX 恢复后尽快核对
PULL_RETRY = 15.0       # 拉名单失败后的最短重试间隔，免得每次请求都去敲 KX（它读名单时会调用麦蕊）
PENDING_RETRIES = 3     # KX 「正在取数」时按它给的间隔再读几次


@dataclass(frozen=True)
class PoolResult:
    snapshot: store.PoolSnapshot | None  # None：从来没有同步成功过
    stale: bool                          # 这一轮没能与 KX 核对
    reason: str | None                   # stale 或没有名单的原因，中文，不含 URL 与访问码


class PoolSync:
    def __init__(self, source: KxSource, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.source = source
        self._clock = clock
        self._sleep = sleep
        self._lock: asyncio.Lock | None = None
        self._health: Health | KxError | None = None
        self._health_at = 0.0
        self._pull_blocked_until = 0.0
        self._pull_error: KxError | None = None

    def _guard(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _pull(self, health: Health) -> PoolDocument:
        for attempt in range(1 + PENDING_RETRIES):
            body = await self.source.pool_page()
            if not is_pending(body):
                return parse_pool(body, health)
            if attempt < PENDING_RETRIES:
                await self._sleep(retry_delay(body))
        raise KxError("kx_pending", "KX 还在取数，名单暂时没读到。")

    async def ensure(self, path: Path, now: datetime) -> PoolResult:
        async with self._guard():
            with db.read(path) as conn:
                local = store.load_pool(conn)
            at = self._clock()
            refreshed = False
            limit = HEALTH_TTL if isinstance(self._health, Health) else HEALTH_FAIL_TTL
            if self._health is None or at - self._health_at >= limit:
                try:
                    self._health = await self.source.health()
                except KxError as error:
                    self._health = error
                self._health_at = self._clock()
                refreshed = True
            health = self._health
            if isinstance(health, KxError):
                return PoolResult(local, local is not None, health.message)
            if local is not None and local.version == health.version and len(local.members) == health.count:
                if refreshed:
                    with db.write(path) as conn:
                        store.touch_pool(conn, version=local.version, verified_at=now)
                    local = replace(local, verified_at=now)
                return PoolResult(local, False, None)
            if at < self._pull_blocked_until and self._pull_error is not None:
                return PoolResult(local, local is not None, self._pull_error.message)
            try:
                document = await self._pull(health)
            except KxError as error:
                self._pull_error = error
                self._pull_blocked_until = self._clock() + PULL_RETRY
                if error.code == "kx_pool_changed":
                    self._health = None  # 版本在两次读取之间变了：下一次请求重新取健康检查
                return PoolResult(local, local is not None, error.message)
            with db.write(path) as conn:
                store.replace_pool(conn, version=document.version, members=document.members, verified_at=now)
            self._pull_error = None
            self._pull_blocked_until = 0.0
            return PoolResult(
                store.PoolSnapshot(document.version, document.members, now), False, None)

    async def aclose(self) -> None:
        await self.source.aclose()
