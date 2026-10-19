"""进程内的 KX 服务。测试用 use() 换成假 transport，避免打到真的 KX、麦蕊或 TuShare。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from neckline.kx.daily import DailyStats, TuShareBoardSource
from neckline.kx.pool import PoolSync
from neckline.kx.source import KxSource
from neckline.market import services as market_services
from neckline.market.mairui import Mairui
from neckline.morning.schedule import CST


def cst_now() -> datetime:
    return datetime.now(CST)


@dataclass
class KxServices:
    pool: PoolSync
    mairui: Mairui
    stats: DailyStats
    now: Callable[[], datetime] = cst_now
    owns_mairui: bool = False

    async def aclose(self) -> None:
        await self.pool.aclose()
        await self.stats.aclose()
        if self.owns_mairui:
            await self.mairui.aclose()


_default: KxServices | None = None
_override: KxServices | None = None


def default_services() -> KxServices:
    global _default
    if _default is None:
        _default = KxServices(
            pool=PoolSync(KxSource()),
            mairui=market_services.current().mairui,  # 与单票概览共用麦蕊客户端、连接数限制和缓存
            stats=DailyStats(TuShareBoardSource(os.environ.get("TUSHARE_TOKEN"))),
        )
    return _default


def current() -> KxServices:
    return _override or default_services()


def use(services: KxServices | None) -> None:
    global _override
    _override = services


async def close_default_services() -> None:
    global _default
    if _default is not None:
        await _default.aclose()
        _default = None
