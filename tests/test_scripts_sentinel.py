"""`scripts/sentinel.py` 主循环单测(plan 阶段3 工程要求「交易时段内轮询,非交易
时段优雅退出/待机」)。`run_tick` 本身已在 `tests/test_sentinel_engine.py` 覆盖,
这里只测本脚本独有的编排:非交易日/收盘后退出、开盘前待机、午休降频、
`--once`/`max_ticks` 停止条件——全部注入 `now_fn`/`sleep_fn`/`run_tick`,不真的
`time.sleep`、不碰真实日历数据(`isolated_env` 隔离)。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import List

import pytest

from tests.conftest import insert_trade_cal

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import sentinel as sentinel_script  # noqa: E402  (scripts/sentinel.py,裸模块名,同脚本自身的导入姿势)

pytestmark = pytest.mark.usefixtures("isolated_env")


@dataclass
class _StubResult:
    skipped_non_trading: bool = False
    watched_codes: int = 0
    quotes_fetched: int = 0
    retreat_active: bool = False
    entry_signals: list = None
    invalidation_signals: list = None
    holding_alerts: list = None
    pushed_events: list = None
    skipped_duplicate: int = 0

    def __post_init__(self):
        self.entry_signals = self.entry_signals or []
        self.invalidation_signals = self.invalidation_signals or []
        self.holding_alerts = self.holding_alerts or []
        self.pushed_events = self.pushed_events or []


class _TimeSequence:
    """依次返回给定的 datetime 序列,耗尽后重复最后一个值(避免测试因多调用
    一次而 StopIteration,更贴近"真实时间只会继续往前走"的语义)。"""

    def __init__(self, times: List[datetime]):
        self._times = list(times)
        self._i = 0

    def __call__(self) -> datetime:
        t = self._times[min(self._i, len(self._times) - 1)]
        self._i += 1
        return t


class TestNonTradingDayExitsImmediately:
    def test_weekend_exits_without_ticking_or_sleeping(self, isolated_env, monkeypatch):
        sleeps = []
        monkeypatch.setattr(sentinel_script, "run_tick", lambda *a, **k: pytest.fail("不应调用 run_tick"))
        rc = sentinel_script.run_loop(
            now_fn=_TimeSequence([datetime(2026, 7, 18, 10, 0)]),  # 周六
            sleep_fn=sleeps.append,
        )
        assert rc == 0
        assert sleeps == []


class TestClosedAfterHoursExitsImmediately:
    def test_trading_day_but_after_close(self, isolated_env, monkeypatch):
        d = date(2026, 7, 20)
        insert_trade_cal(isolated_env, [d])
        monkeypatch.setattr(sentinel_script, "run_tick", lambda *a, **k: pytest.fail("不应调用 run_tick"))
        sleeps = []
        rc = sentinel_script.run_loop(now_fn=_TimeSequence([datetime(2026, 7, 20, 15, 30)]), sleep_fn=sleeps.append)
        assert rc == 0
        assert sleeps == []


class TestWaitsUntilOpen:
    def test_sleeps_until_0930_then_ticks(self, isolated_env, monkeypatch):
        d = date(2026, 7, 20)
        insert_trade_cal(isolated_env, [d])
        calls = {"n": 0}

        def fake_run_tick(now, **kwargs):
            calls["n"] += 1
            return _StubResult()

        monkeypatch.setattr(sentinel_script, "run_tick", fake_run_tick)
        sleeps = []
        # 第一次 now_fn 调用(判断是否需要等待)给 09:00,之后每次循环体内重新取 now
        # 都推进到 09:30 之后,模拟"睡到点了"。
        seq = _TimeSequence([datetime(2026, 7, 20, 9, 0), datetime(2026, 7, 20, 9, 30)])
        rc = sentinel_script.run_loop(now_fn=seq, sleep_fn=sleeps.append, once=True)
        assert rc == 0
        assert calls["n"] == 1
        assert sleeps[0] == pytest.approx(30 * 60, abs=1)  # 09:00→09:30 等待1800秒


class TestOnceStopsAfterSingleTick:
    def test_once_flag(self, isolated_env, monkeypatch):
        d = date(2026, 7, 20)
        insert_trade_cal(isolated_env, [d])
        calls = {"n": 0}
        monkeypatch.setattr(sentinel_script, "run_tick", lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), _StubResult())[1])
        sleeps = []
        rc = sentinel_script.run_loop(
            now_fn=_TimeSequence([datetime(2026, 7, 20, 10, 0)]), sleep_fn=sleeps.append, once=True,
        )
        assert rc == 0
        assert calls["n"] == 1
        assert sleeps == []  # once 模式不该在跑完这一拍后还去 sleep


class TestMaxTicksStopsLoop:
    def test_stops_after_configured_tick_count(self, isolated_env, monkeypatch):
        d = date(2026, 7, 20)
        insert_trade_cal(isolated_env, [d])
        calls = {"n": 0}
        monkeypatch.setattr(sentinel_script, "run_tick", lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), _StubResult())[1])
        sleeps = []
        fixed_time = datetime(2026, 7, 20, 10, 0)
        rc = sentinel_script.run_loop(
            now_fn=_TimeSequence([fixed_time]), sleep_fn=sleeps.append, max_ticks=3,
        )
        assert rc == 0
        assert calls["n"] == 3
        assert len(sleeps) == 2  # 跑完第3拍即返回,不再多睡一次


class TestLunchBreakUsesReducedInterval:
    def test_next_interval_during_lunch(self):
        during_lunch = datetime(2026, 7, 20, 12, 0)
        assert sentinel_script._next_interval(during_lunch, poll_seconds=60, lunch_poll_seconds=300) == 300

    def test_next_interval_outside_lunch(self):
        morning = datetime(2026, 7, 20, 10, 0)
        assert sentinel_script._next_interval(morning, poll_seconds=60, lunch_poll_seconds=300) == 60

    def test_boundary_1130_is_lunch_1300_is_not(self):
        assert sentinel_script._next_interval(datetime(2026, 7, 20, 11, 30), 60, 300) == 300
        assert sentinel_script._next_interval(datetime(2026, 7, 20, 13, 0), 60, 300) == 60

    def test_loop_sleeps_reduced_interval_during_lunch(self, isolated_env, monkeypatch):
        d = date(2026, 7, 20)
        insert_trade_cal(isolated_env, [d])
        monkeypatch.setattr(sentinel_script, "run_tick", lambda *a, **k: _StubResult())
        sleeps = []
        # max_ticks=2:第1拍后应 sleep(降频间隔)一次,再跑第2拍后因达到上限直接
        # 返回、不再 sleep——用两拍才能观察到"拍与拍之间"的 sleep 行为。
        rc = sentinel_script.run_loop(
            now_fn=_TimeSequence([datetime(2026, 7, 20, 12, 0)]), sleep_fn=sleeps.append,
            poll_seconds=60, lunch_poll_seconds=300, max_ticks=2,
        )
        assert rc == 0
        assert sleeps == [300]


class TestSecondsUntilHelper:
    def test_zero_or_positive_never_negative(self):
        past_open = datetime(2026, 7, 20, 9, 31)
        assert sentinel_script._seconds_until(past_open, time(9, 30)) == 0.0
