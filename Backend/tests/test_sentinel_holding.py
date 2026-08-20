"""持仓哨兵单测(plan §2.4 第3条)。三条子检查(止损逼近/回落止盈/板块跳水预警)
各自独立断言,以及 `evaluate_holding` 合一编排的组合场景。另含 2026-08-03 新增
第四函数 `check_exit_reference_reached`(触达离场参考区间,APNs `take_profit`
kind 的触发源——**不是**上面的回落止盈,两者刻意不同源,见该函数 docstring)。"""

from __future__ import annotations

from neckline.sentinel.holding import (
    check_exit_reference_reached,
    check_sector_dive,
    check_stop_approach,
    check_take_profit,
    evaluate_holding,
)
from neckline.sentinel.positions import Position
from neckline.data.realtime import Quote


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


class TestExitReferenceReached:
    """2026-08-03 用户拍板新增。触达来源篮子卡经 `position_plans` 继承的离场参考
    区间——**与回落止盈刻意不同源**(不读 `take_profit_retrace`,只吃调用方给的
    `exit_low`/`exit_high` 两个数),文案不建议卖出。"""

    def test_not_yet_reached_returns_none(self):
        pos = _position(buy_price=10.0)
        assert check_exit_reference_reached(pos, _quote(12.0), exit_low=13.0, exit_high=15.0) is None

    def test_reached_at_low_bound_triggers(self):
        pos = _position(buy_price=10.0)
        reason = check_exit_reference_reached(pos, _quote(13.0), exit_low=13.0, exit_high=15.0)
        assert reason is not None
        assert "触达" in reason and "离场参考区间" in reason
        assert "[13.00, 15.00]" in reason

    def test_reached_above_high_bound_still_triggers(self):
        """越过区间上沿仍算"触达"——不要求价格停在区间内才算数。"""
        pos = _position(buy_price=10.0)
        reason = check_exit_reference_reached(pos, _quote(16.0), exit_low=13.0, exit_high=15.0)
        assert reason is not None

    def test_wording_is_neutral_not_a_sell_suggestion(self):
        """语义红线(2026-08-03 定向任务书要求③):不许写成"该卖了/建议止盈"。"""
        pos = _position(buy_price=10.0)
        reason = check_exit_reference_reached(pos, _quote(14.0), exit_low=13.0, exit_high=15.0)
        assert reason is not None
        for banned in ("建议", "该卖", "止盈信号", "推荐"):
            if banned == "止盈信号":
                assert "不是止盈信号" in reason   # 唯一允许的出现方式:否定句
                continue
            assert banned not in reason

    def test_invalid_or_missing_zone_returns_none(self):
        pos = _position(buy_price=10.0)
        assert check_exit_reference_reached(pos, _quote(20.0), exit_low=0.0, exit_high=0.0) is None
        assert check_exit_reference_reached(pos, _quote(20.0), exit_low=15.0, exit_high=13.0) is None  # low>high 畸形

    def test_non_positive_price_returns_none(self):
        pos = _position(buy_price=10.0)
        assert check_exit_reference_reached(pos, _quote(0.0), exit_low=13.0, exit_high=15.0) is None


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
