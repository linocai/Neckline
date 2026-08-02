"""V2-⑧-B 两条存拍旁路在 lifespan 轮询循环里的**接线**单测(体例照
`test_sentinel_loop_precall.py`:不真等、不联网,把 `datetime.now` 钉在目标窗口,
stub 掉旁路函数,跑循环一次迭代)。

断言三件事:①09:25 段真调竞价快照;②15:05–15:35 段真调当日落盘;③旁路抛异常被吞、
**不掀翻主循环**(存拍是旁路,四哨兵与盘前校准的成败与它无关)。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time

import pytest

import neckline.api.app as app_mod
from neckline.sentinel import capture as capture_mod
from neckline.sentinel.precall import PrecallResult


class _FixedNow:
    def __init__(self, dt):
        self._dt = dt

    def now(self, *a, **k):
        return self._dt

    def __getattr__(self, name):
        return getattr(datetime, name)


def _drive(monkeypatch, *, now, stop_on: str, auction_exc=None, flush_exc=None):
    """跑 `_sentinel_loop` 恰好一次迭代;`stop_on` 指定由哪个 stub 置位 stop。"""
    monkeypatch.setattr(app_mod, "datetime", _FixedNow(now))
    monkeypatch.setattr(app_mod, "is_intraday_now", lambda n: False)
    monkeypatch.setattr(app_mod, "is_trading_day", lambda d: True)
    monkeypatch.setattr(capture_mod, "is_auction_capture_window",
                        lambda n: capture_mod.AUCTION_CAPTURE_START <= n.time() < capture_mod.AUCTION_CAPTURE_END)
    monkeypatch.setattr(capture_mod, "is_flush_window",
                        lambda n: capture_mod.FLUSH_WINDOW_START <= n.time() < capture_mod.FLUSH_WINDOW_END)

    calls = {"auction": 0, "flush": 0, "precall": 0}
    stop = asyncio.Event()

    def _auction_stub(trade_date, n, **kw):
        calls["auction"] += 1
        if stop_on == "auction":
            stop.set()
        if auction_exc is not None:
            raise auction_exc
        return 0

    def _flush_stub(trade_date, **kw):
        calls["flush"] += 1
        if stop_on == "flush":
            stop.set()
        if flush_exc is not None:
            raise flush_exc
        return capture_mod.CaptureFlushResult(trade_date=trade_date)

    def _precall_stub(n, **kw):
        calls["precall"] += 1
        return PrecallResult(trade_date=n.date(), now=n, ran=False, skipped_reason="already_ran")

    import neckline.sentinel.precall as precall_mod

    monkeypatch.setattr(precall_mod, "run_precall_tick", _precall_stub)
    monkeypatch.setattr(capture_mod, "run_auction_capture", _auction_stub)
    monkeypatch.setattr(capture_mod, "flush_day", _flush_stub)

    asyncio.run(asyncio.wait_for(app_mod._sentinel_loop(stop), timeout=5.0))
    return calls


PREOPEN = datetime.combine(date(2026, 7, 21), time(9, 25, 30))
POSTCLOSE = datetime.combine(date(2026, 7, 21), time(15, 6))


def test_preopen_branch_runs_auction_capture(monkeypatch):
    calls = _drive(monkeypatch, now=PREOPEN, stop_on="auction")
    assert calls["precall"] == 1 and calls["auction"] == 1 and calls["flush"] == 0


def test_auction_capture_exception_does_not_kill_loop(monkeypatch):
    calls = _drive(monkeypatch, now=PREOPEN, stop_on="auction", auction_exc=RuntimeError("源挂了"))
    assert calls["auction"] == 1        # 抛了也被吞掉,循环正常收尾


def test_postclose_branch_flushes_capture(monkeypatch):
    calls = _drive(monkeypatch, now=POSTCLOSE, stop_on="flush")
    assert calls["flush"] == 1 and calls["auction"] == 0 and calls["precall"] == 0


def test_flush_exception_does_not_kill_loop(monkeypatch):
    calls = _drive(monkeypatch, now=POSTCLOSE, stop_on="flush", flush_exc=OSError("磁盘满了"))
    assert calls["flush"] == 1


def test_flush_window_constants_match_d4_decision():
    """D4 拍板 15:05 一次落盘;窗口给到 15:35 是因为收盘后轮询 5min 一探(见 capture 模块头)。"""
    assert capture_mod.FLUSH_WINDOW_START == time(15, 5)
    assert capture_mod.FLUSH_WINDOW_END == time(15, 35)
    assert app_mod._SENTINEL_IDLE_POLL_SEC <= 300      # 窗口必须宽于一个待机轮询周期
