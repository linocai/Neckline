"""哨兵 lifespan 循环的盘前分支接线单测(plan v1.1-A.1)。不真等 30s、不联网:注入
`datetime.now` 返回盘前窗口时刻,stub `run_precall_tick` / notify,跑循环单次迭代,
断言:①盘前段真调 run_precall_tick;②ran 时按 summary_actionable / d5_exits 各走
notify 白名单入口;③盘前一拍异常被吞、不掀翻主循环(异常路径也能优雅退出)。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time

import pytest

import neckline.api.app as app_mod
from neckline.sentinel.precall import D5Exit, PrecallResult


class _FixedNow:
    """把 `datetime.now()` 钉在盘前窗口;其余 datetime 行为透传。"""

    def __init__(self, dt):
        self._dt = dt

    def now(self, *a, **k):
        return self._dt

    def __getattr__(self, name):
        return getattr(datetime, name)


def _run_one_iteration(monkeypatch, *, now, precall_result=None, precall_exc=None):
    """驱动 `_sentinel_loop` 恰好一次迭代:stub 令 run_precall_tick 被调后置位 stop。"""
    monkeypatch.setattr(app_mod, "datetime", _FixedNow(now))
    monkeypatch.setattr(app_mod, "is_intraday_now", lambda n: False)   # 强制走 elif 盘前分支
    monkeypatch.setattr(app_mod, "is_trading_day", lambda d: True)

    calls = {"precall": 0, "summary": 0, "d5": []}
    stop = asyncio.Event()

    def _precall_stub(n, **kw):
        calls["precall"] += 1
        stop.set()                       # 跑完这一拍就让循环退出
        if precall_exc is not None:
            raise precall_exc
        return precall_result

    def _summary_stub(counts, **kw):
        calls["summary"] += 1

    def _d5_stub(name, code, d, **kw):
        calls["d5"].append((name, code, d))

    # run_precall_tick 是循环内 `from ... import run_precall_tick` 的局部名,故 patch 源模块
    import neckline.sentinel.precall as precall_mod
    monkeypatch.setattr(precall_mod, "run_precall_tick", _precall_stub)
    monkeypatch.setattr(app_mod.notify, "push_precall_summary", _summary_stub)
    monkeypatch.setattr(app_mod.notify, "push_d5_exit", _d5_stub)

    asyncio.run(asyncio.wait_for(app_mod._sentinel_loop(stop), timeout=5.0))
    return calls


PREOPEN = datetime.combine(date(2026, 7, 21), time(9, 25, 30))


def test_preopen_boundaries():
    import neckline.calendar.trading_calendar as tc
    # 直接用 _is_preopen(纯时间判定 + is_trading_day);此处只验时间边界,交易日另测
    assert app_mod._PREOPEN_START == time(9, 20)
    assert app_mod._PREOPEN_END == time(9, 30)


def test_loop_calls_precall_and_pushes(monkeypatch):
    res = PrecallResult(trade_date=PREOPEN.date(), now=PREOPEN, ran=True)
    res.gap_up = ["600001.SH", "600002.SH"]        # summary_actionable = 2 > 0 → 推汇总
    res.d5_exits = [D5Exit(position_id=1, ts_code="600900.SH", name="持仓票", d=5)]
    calls = _run_one_iteration(monkeypatch, now=PREOPEN, precall_result=res)
    assert calls["precall"] == 1
    assert calls["summary"] == 1
    assert calls["d5"] == [("持仓票", "600900.SH", 5)]


def test_loop_no_summary_when_nothing_actionable(monkeypatch):
    res = PrecallResult(trade_date=PREOPEN.date(), now=PREOPEN, ran=True)
    res.auction = ["600001.SH"]                    # 只有竞价异常附注 → actionable=0 → 不推汇总
    calls = _run_one_iteration(monkeypatch, now=PREOPEN, precall_result=res)
    assert calls["precall"] == 1
    assert calls["summary"] == 0
    assert calls["d5"] == []


def test_loop_skipped_precall_no_push(monkeypatch):
    res = PrecallResult(trade_date=PREOPEN.date(), now=PREOPEN, ran=False, skipped_reason="already_ran")
    calls = _run_one_iteration(monkeypatch, now=PREOPEN, precall_result=res)
    assert calls["precall"] == 1
    assert calls["summary"] == 0 and calls["d5"] == []


def test_loop_swallows_precall_exception(monkeypatch):
    """盘前一拍异常必须被吞、不掀翻轮询主循环(高危中的高危:哨兵常驻稳定性)。"""
    calls = _run_one_iteration(monkeypatch, now=PREOPEN, precall_exc=RuntimeError("boom"))
    assert calls["precall"] == 1        # 调用发生
    assert calls["summary"] == 0        # 异常后不推;循环正常退出(未抛出)
