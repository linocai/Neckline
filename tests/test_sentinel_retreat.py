"""退潮哨兵单测(plan §2.4 第2条)。市场宽度统计(关注池代理样本,非全市场,
见 retreat.py 模块头「设计决策说明」)+ 红色刹车判定(炸板率/跌停家数/主线板块
跳水三条独立触发条件)。"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.data.board import Board
from neckline.report.sentiment import SentimentDashboard
from neckline.sentinel.quotes import Quote
from neckline.sentinel.retreat import (
    LIMIT_DOWN_COUNT_TRIGGER,
    MarketBreadthSnapshot,
    check_retreat,
    compute_breadth_snapshot,
)
from neckline.sentinel.universe import StockMeta

D = date(2026, 7, 17)  # 阶段0/2 记录的真实大跌日,借用作测试语境(非活体数据)


def _quote(code, price, pre_close, high=None) -> Quote:
    return Quote(
        code=code, name=code, price=price, pre_close=pre_close, open=pre_close,
        high=high if high is not None else max(price, pre_close), low=min(price, pre_close),
        volume=10000.0, amount=price * 10000.0 * 100, ts="2026-07-17 10:30:00", source="sina",
    )


def _meta(code, board=Board.MAIN, is_st=False, list_date=date(2015, 1, 1)) -> StockMeta:
    return StockMeta(ts_code=code, name=code, board=board, is_st=is_st, list_date=list_date)


def _sentiment(zaban_rate=0.10) -> SentimentDashboard:
    return SentimentDashboard(
        trade_date=D, limit_up_count=40, limit_down_count=5, zaban_count=5, zaban_rate=zaban_rate,
        max_consec_limit_up=3, prev_limit_up_premium_avg=None, prev_limit_up_sample=0,
        position_quota="满额", quota_reason="",
    )


class TestComputeBreadthSnapshot:
    def test_counts_limit_up_down_and_zaban(self):
        quotes = {
            "A": _quote("A", 11.0, 10.0),   # 主板涨停(10%)
            "B": _quote("B", 9.0, 10.0),    # 主板跌停
            "C": _quote("C", 10.5, 10.0, high=11.0),  # 摸过涨停但现价未在涨停 → 炸板
            "D": _quote("D", 10.2, 10.0),   # 平淡
        }
        meta = {c: _meta(c) for c in quotes}
        snap = compute_breadth_snapshot(D, quotes, meta)
        assert snap.limit_up_count == 1
        assert snap.limit_down_count == 1
        assert snap.zaban_count == 1
        assert snap.sample_size == 4
        assert snap.zaban_rate == pytest.approx(1 / 2)  # zaban/(zaban+limit_up) = 1/(1+1)

    def test_missing_meta_is_skipped_not_crash(self):
        quotes = {"A": _quote("A", 11.0, 10.0), "UNKNOWN": _quote("UNKNOWN", 11.0, 10.0)}
        meta = {"A": _meta("A")}
        snap = compute_breadth_snapshot(D, quotes, meta)
        assert snap.sample_size == 1

    def test_new_stock_exempt_is_excluded(self):
        quotes = {"NEW": _quote("NEW", 25.0, 10.0)}  # +150%,若不豁免会被算成异常
        meta = {"NEW": _meta("NEW", board=Board.STAR, list_date=D)}  # 上市首日
        snap = compute_breadth_snapshot(D, quotes, meta)
        assert snap.sample_size == 0
        assert snap.limit_up_count == 0

    def test_empty_quotes_returns_zeroed_snapshot(self):
        snap = compute_breadth_snapshot(D, {}, {})
        assert snap.sample_size == 0
        assert snap.zaban_rate == 0.0


class TestCheckRetreatZabanRate:
    def _snapshot(self, zaban, limit_up, limit_down=0, sample=None):
        denom = zaban + limit_up
        rate = zaban / denom if denom else 0.0
        return MarketBreadthSnapshot(
            trade_date=D, sample_size=sample if sample is not None else denom + limit_down,
            limit_up_count=limit_up, limit_down_count=limit_down, zaban_count=zaban, zaban_rate=rate,
        )

    def test_absolute_zaban_rate_triggers_with_enough_sample(self):
        snap = self._snapshot(zaban=6, limit_up=4)  # rate=0.6>=0.5,denom=10>=5
        alert = check_retreat(snap)
        assert alert is not None
        assert any("炸板率" in r for r in alert.reasons)

    def test_high_rate_but_tiny_sample_does_not_trigger_absolute(self):
        snap = self._snapshot(zaban=2, limit_up=0)  # rate=1.0 但 denom=2<5(ZABAN_MIN_SAMPLE)
        alert = check_retreat(snap)
        assert alert is None

    def test_relative_spike_vs_prev_eod_triggers(self):
        snap = self._snapshot(zaban=4, limit_up=4)  # rate=0.5,denom=8>=5,但<0.5的绝对线刚好等于0.5会走绝对分支
        # 用 rate=0.45 避免撞绝对触发线,专测相对飙升分支
        snap = self._snapshot(zaban=45, limit_up=55)  # rate=0.45
        alert = check_retreat(snap, prev_eod_sentiment=_sentiment(zaban_rate=0.10))  # 差值0.35>=0.20
        assert alert is not None
        assert any("飙升" in r for r in alert.reasons)

    def test_small_change_from_prev_eod_does_not_trigger(self):
        snap = self._snapshot(zaban=20, limit_up=80)  # rate=0.20
        alert = check_retreat(snap, prev_eod_sentiment=_sentiment(zaban_rate=0.10))  # 差值仅0.10<0.20
        assert alert is None


class TestCheckRetreatLimitDown:
    def _snapshot(self, limit_down, sample_size):
        return MarketBreadthSnapshot(
            trade_date=D, sample_size=sample_size, limit_up_count=0, limit_down_count=limit_down,
            zaban_count=0, zaban_rate=0.0,
        )

    def test_absolute_count_triggers(self):
        snap = self._snapshot(limit_down=LIMIT_DOWN_COUNT_TRIGGER, sample_size=200)
        alert = check_retreat(snap)
        assert alert is not None
        assert any("跌停" in r for r in alert.reasons)

    def test_rate_triggers_even_with_low_absolute_count(self):
        snap = self._snapshot(limit_down=2, sample_size=10)  # 20% > 15%阈,但绝对数2<5
        alert = check_retreat(snap)
        assert alert is not None

    def test_below_both_thresholds_does_not_trigger(self):
        snap = self._snapshot(limit_down=1, sample_size=100)
        assert check_retreat(snap) is None


class TestCheckRetreatSectorDive:
    def _flat_snapshot(self):
        return MarketBreadthSnapshot(
            trade_date=D, sample_size=100, limit_up_count=0, limit_down_count=0, zaban_count=0, zaban_rate=0.0,
        )

    def test_sector_dive_triggers(self):
        alert = check_retreat(self._flat_snapshot(), hot_sector_peer_rets=[-0.05, -0.04, -0.03])
        assert alert is not None
        assert any("主线跳水" in r for r in alert.reasons)

    def test_mild_sector_move_does_not_trigger(self):
        assert check_retreat(self._flat_snapshot(), hot_sector_peer_rets=[-0.01, 0.01]) is None

    def test_empty_peer_returns_no_data_not_triggered(self):
        assert check_retreat(self._flat_snapshot(), hot_sector_peer_rets=[]) is None


class TestNoTriggerWhenHealthy:
    def test_healthy_market_returns_none(self):
        quotes = {f"S{i}": _quote(f"S{i}", 10.1, 10.0) for i in range(20)}
        meta = {c: _meta(c) for c in quotes}
        snap = compute_breadth_snapshot(D, quotes, meta)
        alert = check_retreat(snap, prev_eod_sentiment=_sentiment(zaban_rate=0.10), hot_sector_peer_rets=[0.01, 0.02])
        assert alert is None
