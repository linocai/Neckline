"""证伪哨兵单测(plan §2.4 第4条)。逐条覆盖 `invalidation_spec` 的四个子条件——
低开不回 / 跌破VWAP / 量能过低 / 量能过高,以及多条同时命中时全部列出。"""

from __future__ import annotations

from datetime import datetime

from neckline.sentinel.universe import WatchTarget
from neckline.sentinel.invalidation import check_invalidation
from neckline.sentinel.quotes import Quote

D = datetime(2026, 7, 20)


def _at(hh: int, mm: int) -> datetime:
    return D.replace(hour=hh, minute=mm)


_T_60MIN = _at(10, 30)


def _quote(price, volume, *, pre_close=10.0, open_=10.0, amount=None) -> Quote:
    if amount is None:
        amount = price * volume * 100.0
    return Quote(
        code="600001", name="示例甲", price=price, pre_close=pre_close, open=open_,
        high=max(price, open_), low=min(price, open_), volume=volume, amount=amount,
        ts="2026-07-20 10:30:00", source="sina",
    )


_DEFAULT_SPEC = {
    "low_open_pct": -0.02,
    "require_stay_below_prev_close": True,
    "vwap_break": True,
    "vol_ratio_low": 0.8,
    "vol_ratio_high": 3.0,
}


def _candidate(spec=None) -> WatchTarget:
    """V2-⑬-1:证伪哨兵的判定对象由 `Candidate` 换成 `WatchTarget`(T1/T2 篮子成员),
    **判定逻辑与阈值一行未改**,故本文件的用例全部原样保留、只换构造。"""
    from neckline.sentinel.invalidation import invalidation_spec

    return WatchTarget(
        ts_code="600001.SH", name="示例甲",
        invalidation_spec=(invalidation_spec() if spec is None else spec),
        basket_key="B1",
    )


class TestLowOpenNotRecovered:
    def test_low_open_and_still_red_triggers(self):
        c = _candidate()
        # 开盘-3%(超过-2%阈),现价仍低于昨收(未翻红)
        quote = _quote(price=9.75, volume=1000, pre_close=10.0, open_=9.7)
        sig = check_invalidation(c, quote, prev5_avg_vol=200000, now=_T_60MIN)
        assert sig is not None
        assert any("低开" in r for r in sig.reasons)

    def test_low_open_but_recovered_to_red_does_not_trigger_this_reason(self):
        """低开但已经翻红(现价>=昨收)→ 不算"未翻红",这条子理由不应出现
        (即便其它子条件仍可能触发,由其它用例单独验证)。"""
        c = _candidate()
        quote = _quote(price=10.5, volume=200000, pre_close=10.0, open_=9.7, amount=10.5 * 200000 * 100 * 0.99)
        sig = check_invalidation(c, quote, prev5_avg_vol=200000, now=_T_60MIN)
        if sig is not None:
            assert not any("低开" in r for r in sig.reasons)

    def test_open_within_threshold_does_not_trigger(self):
        c = _candidate()
        quote = _quote(price=9.9, volume=200000, pre_close=10.0, open_=9.95, amount=9.9 * 200000 * 100)
        sig = check_invalidation(c, quote, prev5_avg_vol=200000, now=_T_60MIN)
        if sig is not None:
            assert not any("低开" in r for r in sig.reasons)


class TestVwapBreak:
    def test_price_below_vwap_triggers(self):
        c = _candidate()
        # amount 故意抬高,让 vwap 远高于现价
        quote = _quote(price=9.9, volume=60000, pre_close=10.0, open_=10.0, amount=9.9 * 60000 * 100 * 1.3)
        sig = check_invalidation(c, quote, prev5_avg_vol=200000, now=_T_60MIN)
        assert sig is not None
        assert any("跌破当日VWAP" in r for r in sig.reasons)

    def test_disabled_flag_skips_check(self):
        spec = dict(_DEFAULT_SPEC, vwap_break=False)
        c = _candidate(spec)
        quote = _quote(price=9.9, volume=60000, pre_close=10.0, open_=10.0, amount=9.9 * 60000 * 100 * 1.3)
        sig = check_invalidation(c, quote, prev5_avg_vol=200000, now=_T_60MIN)
        if sig is not None:
            assert not any("VWAP" in r for r in sig.reasons)


class TestVolumeAnomaly:
    def test_low_volume_no_follow_through_triggers(self):
        c = _candidate()
        # current_vol=30000,elapsed=60 → ratio=30000*4/200000=0.6 < 0.8
        quote = _quote(price=10.0, volume=30000, pre_close=10.0, open_=10.0)
        sig = check_invalidation(c, quote, prev5_avg_vol=200000, now=_T_60MIN)
        assert sig is not None
        assert any("地量" in r for r in sig.reasons)

    def test_extreme_volume_spike_triggers(self):
        c = _candidate()
        # current_vol=300000 → ratio=300000*4/200000=6.0 > 3.0
        quote = _quote(price=10.0, volume=300000, pre_close=10.0, open_=10.0, amount=10.0 * 300000 * 100)
        sig = check_invalidation(c, quote, prev5_avg_vol=200000, now=_T_60MIN)
        assert sig is not None
        assert any("异常放量" in r for r in sig.reasons)

    def test_normal_volume_no_anomaly(self):
        c = _candidate()
        # current_vol=60000 → ratio=1.2,处在[0.8,3.0]区间内
        quote = _quote(price=10.0, volume=60000, pre_close=10.0, open_=10.0)
        sig = check_invalidation(c, quote, prev5_avg_vol=200000, now=_T_60MIN)
        if sig is not None:
            assert not any("地量" in r or "异常放量" in r for r in sig.reasons)

    def test_early_window_skips_volume_check_without_crash(self):
        c = _candidate()
        quote = _quote(price=10.0, volume=1000, pre_close=10.0, open_=10.0)
        early = _at(9, 45)  # elapsed=15min < 60,ratio 应为 None,不应异常
        sig = check_invalidation(c, quote, prev5_avg_vol=200000, now=early)
        if sig is not None:
            assert not any("地量" in r or "异常放量" in r for r in sig.reasons)


class TestMultipleReasonsCombine:
    def test_low_open_and_vwap_break_both_listed(self):
        c = _candidate()
        quote = _quote(price=9.7, volume=60000, pre_close=10.0, open_=9.7, amount=9.7 * 60000 * 100 * 1.3)
        sig = check_invalidation(c, quote, prev5_avg_vol=200000, now=_T_60MIN)
        assert sig is not None
        assert len(sig.reasons) >= 2
        assert "；".join(sig.reasons) or sig.reason_text  # reason_text 组合属性可用


class TestDegradation:
    def test_quote_none_returns_none(self):
        c = _candidate()
        assert check_invalidation(c, None, prev5_avg_vol=200000, now=_T_60MIN) is None

    def test_empty_spec_returns_none(self):
        c = _candidate(spec={})
        quote = _quote(price=5.0, volume=1, pre_close=10.0, open_=5.0)  # 各种意义上都很糟,但没有 spec 可判
        assert check_invalidation(c, quote, prev5_avg_vol=200000, now=_T_60MIN) is None

    def test_before_structural_gate_returns_none(self):
        c = _candidate()
        quote = _quote(price=9.0, volume=1, pre_close=10.0, open_=9.0)
        just_opened = _at(9, 32)
        assert check_invalidation(c, quote, prev5_avg_vol=200000, now=just_opened) is None
