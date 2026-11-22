"""进程内的 Qihuo 服务句柄，测试可注入假源与假时钟。"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from neckline.morning.schedule import CST
from neckline.qihuo.source import QihuoSource


def cst_now() -> datetime:
    return datetime.now(CST)


@dataclass
class QihuoServices:
    source: QihuoSource
    now: Callable[[], datetime] = cst_now
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def aclose(self) -> None:
        await self.source.aclose()


_default: QihuoServices | None = None
_override: QihuoServices | None = None


def default_services() -> QihuoServices:
    global _default
    if _default is None:
        _default = QihuoServices(source=QihuoSource())
    return _default


def current() -> QihuoServices:
    return _override or default_services()


def use(services: QihuoServices | None) -> None:
    global _override
    _override = services


async def close_default_services() -> None:
    global _default
    if _default is not None:
        await _default.aclose()
        _default = None
