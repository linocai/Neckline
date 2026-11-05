"""看板缓存：成功 60 秒、失败 15 秒，同一时刻只读一次 KX。不落库。"""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
from datetime import datetime

from neckline.kx.project import project_board, require_overview
from neckline.kx.services import KxServices
from neckline.kx.source import KxError, redact_upstream

SUCCESS_SECONDS = 60
FAILURE_SECONDS = 15


class BoardUnavailable(Exception):
    """看板出不来：reason 是给程序看的短码，message 是如实说明的中文原因。"""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)


@dataclass
class _State:
    lock: asyncio.Lock | None = None
    loop: asyncio.AbstractEventLoop | None = None
    document: dict | None = None
    read_at: datetime | None = None
    read_mono: float | None = None
    fail_mono: float | None = None
    fail_reason: str | None = None


_state = _State()


def reset_board_state() -> None:
    """测试隔离：清掉进程内的上一份总览和失败缓存。"""
    global _state
    _state = _State()


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if _state.lock is None or _state.loop is not loop:
        _state.lock = asyncio.Lock()
        _state.loop = loop
    return _state.lock


def _project(kx: KxServices, *, stale: bool, reason: str | None) -> dict:
    assert _state.document is not None and _state.read_at is not None
    return project_board(_state.document, now=kx.now(), last_read_at=_state.read_at, stale=stale, reason=reason)


def _failed(kx: KxServices) -> dict:
    reason = _state.fail_reason or "KX 暂时没有读到看板。"
    if _state.document is None:
        raise BoardUnavailable("kx_unavailable", f"KX 看板还没读到过：{reason}")
    return _project(kx, stale=True, reason=reason)


async def build_board(kx: KxServices) -> dict:
    """读 KX 总览并投影。60 秒内的成功和 15 秒内的失败都不再读 KX。"""
    async with _lock():
        now_mono = kx.monotonic()
        if (_state.document is not None and _state.read_mono is not None
                and now_mono - _state.read_mono < SUCCESS_SECONDS):
            return _project(kx, stale=False, reason=None)
        if _state.fail_mono is not None and now_mono - _state.fail_mono < FAILURE_SECONDS:
            return _failed(kx)
        try:
            body = require_overview(await kx.source.overview(sleep=kx.sleep))
            moment = kx.now()
            try:
                projected = project_board(body, now=moment, last_read_at=moment, stale=False, reason=None)
            except KxError:
                raise
            except Exception:
                raise KxError("kx_bad_board", "KX 看板数据无法处理，没有采用。") from None
        except KxError as error:
            _state.fail_mono = kx.monotonic()
            _state.fail_reason = redact_upstream(error.message)
            return _failed(kx)
        _state.document = copy.deepcopy(body)
        _state.read_at = moment
        _state.read_mono = kx.monotonic()
        _state.fail_mono = None
        _state.fail_reason = None
        return projected
