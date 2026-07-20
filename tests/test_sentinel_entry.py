"""买点哨兵单测(plan §2.4 第1条)。纯函数注入假 Quote,不联网、不碰日历/DB
——`elapsed_trading_minutes` 只吃 `datetime`,不依赖交易日历(是否交易日的判断
在更上层的 `intraday.is_intraday_now`/engine.py,不在本模块重复判断)。"""

from __future__ import annotations

from datetime import datetime

import pytest

from neckline.report.candidates import Candidate
from neckline.sentinel.entry import ENTRY_PULLBACK_MIN_VOL_RATIO, check_entry
from neckline.sentinel.quotes import Quote

D = datetime(2026, 7, 20)


def _at(hh: int, mm: int) -> datetime:
    return D.replace(hour=hh, minute=mm)


_T_60MIN = _at(10, 30)   # elapsed=60min,intraday_vol_ratio 恰好脱离 "early"


def _quote(price, volume, *, pre_close=10.0, open_=10.0, high=None, low=None, amount=None) -> Quote:
    if amount is None:
        amount = price * volume * 100.0  # 让 vwap ≈ price 附近,除非测试显式覆盖
    return Quote(
        code="600001", name="示例甲", price=price, pre_close=pre_close, open=open_,
        high=high if high is not None else max(price, open_),
        low=low if low is not None else min(price, open_),
        volume=volume, amount=amount, ts="2026-07-20 10:30:00", source="sina",
    )


def _candidate(buypoint="pullback", **spec_overrides) -> Candidate:
    spec = {"buypoint": buypoint, "ma10": 9.5, "platform_high": 11.0, "breakout_vol_expand": 1.5, "prev_close": 10.0}
    spec.update(spec_overrides)
    return Candidate(
        ts_code="600001.SH", name="示例甲", close=10.0, score=90.0, rank=1, board="MAIN",
        pattern_tags=[], hot_sectors=[], sector_names=[],
        entry_plan="", stop_loss="", target="", invalidation_text="", invalidation_spec={},
        entry_spec=spec,
    )


class TestPullbackEntry:
    def test_reaches_when_holding_ma10_with_confirming_volume_and_vwap(self):
        """站稳ma10(9.5)不破位、未追高开、量能折算达0.8下限、现价站上vwap → 触发。"""
        c = _candidate(buypoint="pullback")
        # current_vol=60000, prev5_avg_vol=200000, elapsed=60min → ratio=60000*4/200000=1.2
        quote = _quote(price=10.2, volume=60000, pre_close=10.0, open_=10.05, amount=10.2 * 60000 * 100 * 0.95)
        sig = check_entry(c, quote, prev5_avg_vol=200000, now=_T_60MIN)
        assert sig is not None
        assert sig.ts_code == "600001.SH"
        assert sig.vol_ratio == pytest.approx(1.2)
        assert "站稳10日线支撑9.50" in sig.reason

    def test_broken_ma10_does_not_reach(self):
        c = _candidate(buypoint="pullback")
        quote = _quote(price=9.0, volume=60000, pre_close=10.0, open_=9.5)  # 跌破 9.5 支撑
        assert check_entry(c, quote, prev5_avg_vol=200000, now=_T_60MIN) is None

    def test_gapping_up_too_much_is_not_a_low_buy_anymore(self):
        c = _candidate(buypoint="pullback")
        quote = _quote(price=10.5, volume=60000, pre_close=10.0, open_=10.3)  # 开盘涨3% > 2%阈
        assert check_entry(c, quote, prev5_avg_vol=200000, now=_T_60MIN) is None

    def test_volume_below_pullback_floor_blocks(self):
        assert ENTRY_PULLBACK_MIN_VOL_RATIO == pytest.approx(0.8)  # 锁死本测试依赖的启发式常量取值
        c = _candidate(buypoint="pullback")
        # current_vol=30000 → ratio=30000*4/200000=0.6 < ENTRY_PULLBACK_MIN_VOL_RATIO(0.8)
        quote = _quote(price=10.2, volume=30000, pre_close=10.0, open_=10.0, amount=10.2 * 30000 * 100 * 0.95)
        assert check_entry(c, quote, prev5_avg_vol=200000, now=_T_60MIN) is None

    def test_below_vwap_blocks(self):
        c = _candidate(buypoint="pullback")
        # amount 故意设得很大,把 vwap 抬高到现价之上
        quote = _quote(price=10.2, volume=60000, pre_close=10.0, open_=10.0, amount=10.2 * 60000 * 100 * 1.5)
        assert check_entry(c, quote, prev5_avg_vol=200000, now=_T_60MIN) is None

    def test_early_window_below_60min_no_confirmation(self):
        c = _candidate(buypoint="pullback")
        quote = _quote(price=10.2, volume=10000, pre_close=10.0, open_=10.0)
        early = _at(9, 50)  # elapsed=20min < 60
        assert check_entry(c, quote, prev5_avg_vol=200000, now=early) is None

    def test_before_structural_gate_5min_no_confirmation(self):
        c = _candidate(buypoint="pullback")
        quote = _quote(price=10.2, volume=1000, pre_close=10.0, open_=10.0)
        just_opened = _at(9, 32)  # elapsed=2min < 5min 结构门
        assert check_entry(c, quote, prev5_avg_vol=200000, now=just_opened) is None

    def test_missing_ma10_degrades_to_none_not_crash(self):
        c = _candidate(buypoint="pullback", ma10=None)
        quote = _quote(price=10.2, volume=60000, pre_close=10.0, open_=10.0)
        assert check_entry(c, quote, prev5_avg_vol=200000, now=_T_60MIN) is None


class TestBreakoutEntry:
    def test_reaches_when_price_breaks_platform_high_with_expansion_volume(self):
        c = _candidate(buypoint="breakout", platform_high=11.0, breakout_vol_expand=1.5)
        # 需要 ratio>=1.5:current_vol=75000 → 75000*4/200000=1.5
        quote = _quote(price=11.2, volume=75000, pre_close=10.0, open_=10.5, amount=11.2 * 75000 * 100 * 0.9)
        sig = check_entry(c, quote, prev5_avg_vol=200000, now=_T_60MIN)
        assert sig is not None
        assert "突破前期平台高点11.00" in sig.reason

    def test_price_at_or_below_platform_high_does_not_reach(self):
        c = _candidate(buypoint="breakout", platform_high=11.0)
        quote = _quote(price=11.0, volume=75000, pre_close=10.0, open_=10.5)
        assert check_entry(c, quote, prev5_avg_vol=200000, now=_T_60MIN) is None

    def test_volume_below_breakout_specific_threshold_blocks_even_if_above_pullback_floor(self):
        """breakout 型复用报告当晚定的 breakout_vol_expand(1.5倍),不是较低的
        pullback 下限(0.8倍)——量比1.2虽然过了pullback门槛,但过不了breakout门槛。"""
        c = _candidate(buypoint="breakout", platform_high=11.0, breakout_vol_expand=1.5)
        quote = _quote(price=11.2, volume=60000, pre_close=10.0, open_=10.5, amount=11.2 * 60000 * 100 * 0.9)
        assert check_entry(c, quote, prev5_avg_vol=200000, now=_T_60MIN) is None


class TestEitherEntry:
    def test_pullback_path_satisfies_either(self):
        c = _candidate(buypoint="either", ma10=9.5, platform_high=999.0)  # breakout 故意设不可达
        quote = _quote(price=10.2, volume=60000, pre_close=10.0, open_=10.0, amount=10.2 * 60000 * 100 * 0.95)
        assert check_entry(c, quote, prev5_avg_vol=200000, now=_T_60MIN) is not None

    def test_breakout_path_satisfies_either_when_pullback_fails(self):
        c = _candidate(buypoint="either", ma10=999.0, platform_high=11.0, breakout_vol_expand=1.5)  # pullback 不可达
        quote = _quote(price=11.2, volume=75000, pre_close=10.0, open_=10.5, amount=11.2 * 75000 * 100 * 0.9)
        assert check_entry(c, quote, prev5_avg_vol=200000, now=_T_60MIN) is not None


class TestDegradation:
    def test_quote_none_returns_none(self):
        c = _candidate()
        assert check_entry(c, None, prev5_avg_vol=200000, now=_T_60MIN) is None

    def test_empty_entry_spec_degrades_to_none(self):
        c = _candidate()
        c.entry_spec = {}
        quote = _quote(price=10.2, volume=60000, pre_close=10.0, open_=10.0)
        assert check_entry(c, quote, prev5_avg_vol=200000, now=_T_60MIN) is None
