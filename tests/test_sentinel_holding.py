"""持仓哨兵单测(plan §2.4 第3条)。三条子检查(止损逼近/回落止盈/板块跳水预警)
各自独立断言,以及 `evaluate_holding` 合一编排的组合场景。"""

from __future__ import annotations

import pytest

from neckline.sentinel.holding import (
    check_sector_dive,
    check_stop_approach,
    check_take_profit,
    evaluate_holding,
)
from neckline.sentinel.positions import Position
from neckline.sentinel.quotes import Quote


def _position(buy_price=10.0, qty=100) -> Position:
    return Position(
        id=1, ts_code="600001.SH", buy_price=buy_price, qty=qty, buy_date="20260715",
        status="open", sell_price=None, sell_date=None, note=None,
    )


def _quote(price, *, pre_close=10.0) -> Quote:
    return Quote(
        code="600001", name="示例甲", price=price, pre_close=pre_close, open=pre_close,
        high=max(price, pre_close), low=min(price, pre_close), volume=100000.0,
        amount=price * 100000.0 * 100, ts="2026-07-20 10:30:00", source="sina",
    )


class TestStopApproach:
    def test_no_warning_when_far_from_stop(self):
        pos = _position(buy_price=10.0)
        # 5% 止损线=9.5,缓冲2pp→预警起点回撤3%(9.7);现价9.9(回撤1%)远未到
        assert check_stop_approach(pos, _quote(9.9), stop_pct=0.05) is None

    def test_warns_when_approaching_within_buffer(self):
        pos = _position(buy_price=10.0)
        # 回撤3.5%(现价9.65),落在[3%,5%)预警区间内
        reason = check_stop_approach(pos, _quote(9.65), stop_pct=0.05)
        assert reason is not None
        assert "逼近止损线" in reason

    def test_warns_more_urgently_when_already_breached(self):
        pos = _position(buy_price=10.0)
        reason = check_stop_approach(pos, _quote(9.0), stop_pct=0.05)  # 回撤10%,已破位
        assert reason is not None
        assert "已跌破止损线" in reason
        assert "人工确认" in reason

    def test_custom_buffer_widens_or_narrows_warning_window(self):
        pos = _position(buy_price=10.0)
        # buffer=0 → 只有真正破位(回撤>=5%)才预警,回撤3.5%不该触发
        assert check_stop_approach(pos, _quote(9.65), stop_pct=0.05, buffer_pct=0.0) is None


class TestTakeProfit:
    def test_no_signal_when_never_in_profit(self):
        """从未浮盈过(峰值<=买入价)的下跌不算"回落止盈"——那是止损哨兵的地盘。"""
        pos = _position(buy_price=10.0)
        reason = check_take_profit(pos, _quote(9.0), historical_peak_close=10.0, take_profit_retrace=0.05)
        assert reason is None

    def test_triggers_after_meaningful_gain_then_retrace(self):
        pos = _position(buy_price=10.0)
        # 历史峰值12.0(涨20%),现价回落到11.0(较峰值回落8.3%>5%阈)
        reason = check_take_profit(pos, _quote(11.0), historical_peak_close=12.0, take_profit_retrace=0.05)
        assert reason is not None
        assert "回落止盈区间" in reason

    def test_current_price_can_itself_be_the_new_peak(self):
        """今日现价若创出新高(超过历史EOD峰值),peak 应取现价本身,不是historical_peak_close。"""
        pos = _position(buy_price=10.0)
        reason = check_take_profit(pos, _quote(13.0), historical_peak_close=12.0, take_profit_retrace=0.05)
        assert reason is None  # 现价本身就是新峰值,不该说"回落"

    def test_none_retrace_config_disables_check(self):
        pos = _position(buy_price=10.0)
        assert check_take_profit(pos, _quote(11.0), historical_peak_close=12.0, take_profit_retrace=None) is None

    def test_small_retrace_within_threshold_does_not_trigger(self):
        pos = _position(buy_price=10.0)
        # 峰值12.0,回落到11.8(仅回落1.7%<5%阈)
        assert check_take_profit(pos, _quote(11.8), historical_peak_close=12.0, take_profit_retrace=0.05) is None


class TestSectorDive:
    def test_no_data_returns_none_not_a_clean_bill_of_health(self):
        pos = _position()
        assert check_sector_dive(pos, []) is None

    def test_average_peer_drop_beyond_threshold_triggers(self):
        pos = _position()
        reason = check_sector_dive(pos, [-0.05, -0.06, -0.04], threshold=-0.03)
        assert reason is not None
        assert "板块跳水" in reason

    def test_mild_peer_moves_do_not_trigger(self):
        pos = _position()
        assert check_sector_dive(pos, [-0.01, 0.01, 0.0], threshold=-0.03) is None


class TestEvaluateHoldingCombined:
    def test_no_quote_only_sector_check_can_fire(self):
        pos = _position(buy_price=10.0)
        alert = evaluate_holding(
            pos, None, stop_pct=0.05, take_profit_retrace=0.05,
            historical_peak_close=10.0, peer_returns=[-0.05, -0.06],
        )
        assert "stop_approach" not in alert.alerts
        assert "take_profit" not in alert.alerts
        assert "sector_dive" in alert.alerts

    def test_multiple_alerts_can_fire_simultaneously(self):
        pos = _position(buy_price=10.0)
        alert = evaluate_holding(
            pos, _quote(9.0), stop_pct=0.05, take_profit_retrace=0.05,
            historical_peak_close=10.0, peer_returns=[-0.05, -0.06],
        )
        assert alert.triggered is True
        assert "stop_approach" in alert.alerts
        assert "sector_dive" in alert.alerts
        assert "take_profit" not in alert.alerts  # 从未浮盈,不该出现

    def test_no_alerts_when_everything_healthy(self):
        pos = _position(buy_price=10.0)
        alert = evaluate_holding(
            pos, _quote(10.1), stop_pct=0.05, take_profit_retrace=0.05,
            historical_peak_close=10.1, peer_returns=[0.01, 0.02],
        )
        assert alert.triggered is False
        assert alert.alerts == {}
