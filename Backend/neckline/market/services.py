"""进程内的行情服务。测试用 use() 换掉，避免打到真的 TuShare 或麦蕊。"""
from __future__ import annotations

import os
from dataclasses import dataclass

from neckline.market.mairui import Mairui
from neckline.market.tushare_daily import TuShareDaily
from neckline.market.util import shanghai_now
from neckline.screening.pool import CompanyPool


@dataclass
class Services:
    pool: CompanyPool
    tushare: TuShareDaily
    mairui: Mairui
    now: object
    # 进程默认服务每次按库里的当前池子判断。测试注入的服务仍用构造时给的池子。
    follow_database: bool = False

    async def aclose(self) -> None:
        await self.tushare.aclose()
        await self.mairui.aclose()


_default: Services | None = None
_override: Services | None = None


def default_services() -> Services:
    global _default
    if _default is None:
        _default = Services(
            pool=CompanyPool(()),
            tushare=TuShareDaily(os.environ.get("TUSHARE_TOKEN"), now=shanghai_now),
            mairui=Mairui(os.environ.get("MAIRUI_LICENCE"), now=shanghai_now),
            now=shanghai_now,
            follow_database=True,
        )
    return _default


def current() -> Services:
    return _override or default_services()


def use(services: Services | None) -> None:
    global _override
    _override = services


async def close_default_services() -> None:
    global _default
    if _default is not None:
        await _default.aclose()
        _default = None
