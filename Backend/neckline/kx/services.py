"""进程内的 KX 服务。测试用 use() 换成假 transport，避免打到真的 KX。"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from neckline.kx.source import KxSource
from neckline.morning.schedule import CST


def cst_now() -> datetime:
    return datetime.now(CST)


@dataclass
class KxServices:
    source: KxSource
    now: Callable[[], datetime] = cst_now
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def aclose(self) -> None:
        await self.source.aclose()


_default: KxServices | None = None
_override: KxServices | None = None


def default_services() -> KxServices:
    global _default
    if _default is None:
        _default = KxServices(source=KxSource())
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
