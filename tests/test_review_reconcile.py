"""对账引擎单测(plan 4D.2 验收:FIFO 闭合 / 三查各分支 / 强制复盘触发线边界)。"""

from __future__ import annotations

from datetime import date, time

import pytest

from neckline.review.parse import RawTrade
from neckline.review.reconcile import (
    FORCED_REVIEW_LOSS_FRAC,
    STOP_BREACHED,
    STOP_KEPT,
    STOP_NOT_APPLICABLE,
    STOP_NOT_TRIGGERED,
    RoundTrip,
    build_round_trips,
    check_cooldown,
    check_entry_screens,
    check_plan_and_ledger,
    check_position_count_and_exposure,
    check_single_cap,
    classify_stop_discipline,
    compute_weekly_stats,
    is_forced_review,
    iso_week_key,
    run_weekly_review,
    week_range,
    weekly_review_dict,
)

from .conftest import (
    business_days,
    insert_stock_basic,
    insert_trade_cal,
    seed_active_rule_v1,
    seed_synthetic_market,
)


def _trade(trade_date, ts_code, side, price, qty, fee=0.0, name="示例票", cash_flow=None):
    return RawTrade(
        trade_date=trade_date, ts_code=ts_code, name=name, side=side,
        price=price, qty=qty, fee=fee, cash_flow=cash_flow if cash_flow is not None else 0.0,
    )


# ======================================================================
#  FIFO 闭合回合
# ======================================================================

class TestBuildRoundTrips:
    def test_simple_full_close(self):
        trades = [
            _trade(date(2026, 7, 14), "600519.SH", "buy", 100.0, 100, fee=10.0),
            _trade(date(2026, 7, 16), "600519.SH", "sell", 110.0, 100, fee=12.0),
        ]
        rts, warnings = build_round_trips(trades)
        assert warnings == []
        assert len(rts) == 1
        rt = rts[0]
        assert rt.closed and rt.buy_price == 100.0 and rt.sell_price == 110.0 and rt.qty == 100
        assert rt.fees == pytest.approx(22.0)
        assert rt.net_pnl == pytest.approx((110 - 100) * 100 - 22.0)

    def test_partial_sells_across_two_transactions_same_lot(self):
        trades = [
            _trade(date(2026, 7, 14), "600519.SH", "buy", 10.0, 200, fee=20.0),
            _trade(date(2026, 7, 15), "600519.SH", "sell", 11.0, 100, fee=5.0),
            _trade(date(2026, 7, 16), "600519.SH", "sell", 12.0, 100, fee=6.0),
        ]
        rts, warnings = build_round_trips(trades)
        assert warnings == []
        assert len(rts) == 2
        rt1, rt2 = rts
        assert rt1.qty == 100 and rt1.sell_price == 11.0
        assert rt1.fees == pytest.approx((20 / 200 + 5 / 100) * 100)   # 0.1+0.05 每股 * 100 = 15
        assert rt2.qty == 100 and rt2.sell_price == 12.0
        assert rt2.fees == pytest.approx((20 / 200 + 6 / 100) * 100)   # 0.1+0.06 每股 * 100 = 16

    def test_fifo_order_across_two_lots(self):
        """两笔不同价格买入,卖出应先消耗最早买入的那批(FIFO);第二笔买入 100 股
        只被消耗 50 股,剩余 50 股应成为一笔未平仓回合(不是"凭空消失")。"""
        trades = [
            _trade(date(2026, 7, 10), "600519.SH", "buy", 10.0, 100, fee=0.0),
            _trade(date(2026, 7, 12), "600519.SH", "buy", 12.0, 100, fee=0.0),
            _trade(date(2026, 7, 14), "600519.SH", "sell", 15.0, 150, fee=0.0),
        ]
        rts, warnings = build_round_trips(trades)
        assert warnings == []
        assert len(rts) == 3
        closed = [rt for rt in rts if rt.closed]
        open_ = [rt for rt in rts if not rt.closed]
        assert len(closed) == 2 and len(open_) == 1
        assert closed[0].buy_price == 10.0 and closed[0].qty == 100   # 先消耗最早买入的 100 股
        assert closed[1].buy_price == 12.0 and closed[1].qty == 50    # 第二笔买入被消耗 50 股
        assert open_[0].buy_price == 12.0 and open_[0].qty == 50      # 第二笔买入剩余 50 股仍持仓

    def test_oversell_produces_warning_not_crash(self):
        trades = [
            _trade(date(2026, 7, 14), "600519.SH", "buy", 10.0, 100, fee=0.0),
            _trade(date(2026, 7, 16), "600519.SH", "sell", 11.0, 150, fee=0.0),
        ]
        rts, warnings = build_round_trips(trades)
        assert len(rts) == 1 and rts[0].qty == 100
        assert len(warnings) == 1
        assert "差 50 股" in warnings[0]

    def test_unmatched_buy_remains_open(self):
        trades = [_trade(date(2026, 7, 14), "600519.SH", "buy", 10.0, 100, fee=1.0)]
        rts, warnings = build_round_trips(trades)
        assert warnings == []
        assert len(rts) == 1
        rt = rts[0]
        assert rt.closed is False
        assert rt.sell_date is None and rt.sell_price is None
        assert rt.net_pnl is None and rt.pnl_pct is None
        assert rt.buy_amount == pytest.approx(1000.0)

    def test_multiple_codes_independent(self):
        trades = [
            _trade(date(2026, 7, 14), "600519.SH", "buy", 10.0, 100),
            _trade(date(2026, 7, 14), "300750.SZ", "buy", 20.0, 100),
            _trade(date(2026, 7, 16), "600519.SH", "sell", 11.0, 100),
        ]
        rts, warnings = build_round_trips(trades)
        assert warnings == []
        by_code = {rt.ts_code: rt for rt in rts}
        assert by_code["600519.SH"].closed is True
        assert by_code["300750.SZ"].closed is False


# ======================================================================
#  止损纪律(对账三查②)
# ======================================================================

class TestStopDiscipline:
    def _rt(self, buy=100.0, sell=94.0):
        return RoundTrip(ts_code="600519.SH", name="贵州茅台", buy_date=date(2026, 7, 14),
                          buy_price=buy, qty=100, fees=0.0, sell_date=date(2026, 7, 16),
                          sell_price=sell, closed=True)

    def test_breached_at_boundary(self):
        kind, _ = classify_stop_discipline(self._rt(sell=94.0), stop_pct=0.05)   # -6% 恰好
        assert kind == STOP_BREACHED

    def test_breached_worse_than_boundary(self):
        kind, _ = classify_stop_discipline(self._rt(sell=93.0), stop_pct=0.05)   # -7%
        assert kind == STOP_BREACHED

    def test_kept_stop_within_band(self):
        kind, _ = classify_stop_discipline(self._rt(sell=95.5), stop_pct=0.05)   # -4.5%
        assert kind == STOP_KEPT

    def test_kept_stop_at_hi_boundary(self):
        kind, _ = classify_stop_discipline(self._rt(sell=96.0), stop_pct=0.05)   # -4% 恰好
        assert kind == STOP_KEPT

    def test_not_triggered_shallow_loss(self):
        kind, _ = classify_stop_discipline(self._rt(sell=98.0), stop_pct=0.05)   # -2%
        assert kind == STOP_NOT_TRIGGERED

    def test_not_triggered_profit(self):
        kind, _ = classify_stop_discipline(self._rt(sell=103.0), stop_pct=0.05)  # +3%
        assert kind == STOP_NOT_TRIGGERED

    def test_not_applicable_when_no_stop_rule(self):
        kind, _ = classify_stop_discipline(self._rt(sell=50.0), stop_pct=None)
        assert kind == STOP_NOT_APPLICABLE

    def test_not_applicable_when_open(self):
        rt = RoundTrip(ts_code="600519.SH", name="x", buy_date=date(2026, 7, 14), buy_price=100.0,
                       qty=100, fees=0.0, closed=False)
        kind, _ = classify_stop_discipline(rt, stop_pct=0.05)
        assert kind == STOP_NOT_APPLICABLE


# ======================================================================
#  章程执行(对账三查③)
# ======================================================================

class TestSingleCap:
    def test_over_cap_flagged(self):
        trades = [_trade(date(2026, 7, 14), "600519.SH", "buy", 300.0, 100)]  # ¥30,000 > 2万
        out = check_single_cap(trades, single_cap=20000.0)
        assert len(out) == 1 and "600519.SH" in out[0]

    def test_within_cap_not_flagged(self):
        trades = [_trade(date(2026, 7, 14), "600519.SH", "buy", 100.0, 100)]  # ¥10,000
        assert check_single_cap(trades, single_cap=20000.0) == []

    def test_at_boundary_not_flagged(self):
        trades = [_trade(date(2026, 7, 14), "600519.SH", "buy", 200.0, 100)]  # 恰好 ¥20,000
        assert check_single_cap(trades, single_cap=20000.0) == []


class TestPositionCountAndExposure:
    def test_concurrent_count_exceeds(self):
        codes = ["A", "B", "C", "D", "E", "F"]
        rts = [
            RoundTrip(ts_code=c, name=c, buy_date=date(2026, 7, 13), buy_price=10.0, qty=100,
                      fees=0.0, sell_date=date(2026, 7, 17), sell_price=11.0, closed=True)
            for c in codes
        ]
        out = check_position_count_and_exposure(
            rts, week_start=date(2026, 7, 13), week_end=date(2026, 7, 17), asof=date(2026, 7, 17),
            max_positions=5, max_exposure_frac=1.0, total_capital=1_000_000.0,
        )
        assert any("并发持仓最多达 6 只" in msg for msg in out)

    def test_concurrent_count_within_limit(self):
        codes = ["A", "B", "C"]
        rts = [
            RoundTrip(ts_code=c, name=c, buy_date=date(2026, 7, 13), buy_price=10.0, qty=100,
                      fees=0.0, sell_date=date(2026, 7, 17), sell_price=11.0, closed=True)
            for c in codes
        ]
        out = check_position_count_and_exposure(
            rts, week_start=date(2026, 7, 13), week_end=date(2026, 7, 17), asof=date(2026, 7, 17),
            max_positions=5, max_exposure_frac=1.0, total_capital=1_000_000.0,
        )
        assert out == []

    def test_exposure_exceeds(self):
        rt = RoundTrip(ts_code="A", name="A", buy_date=date(2026, 7, 13), buy_price=1000.0, qty=100,
                       fees=0.0, sell_date=date(2026, 7, 17), sell_price=1100.0, closed=True)  # ¥100,000
        out = check_position_count_and_exposure(
            [rt], week_start=date(2026, 7, 13), week_end=date(2026, 7, 17), asof=date(2026, 7, 17),
            max_positions=5, max_exposure_frac=0.6, total_capital=120000.0,   # 上限 ¥72,000
        )
        assert any("持仓总敞口最高达" in msg for msg in out)

    def test_exposure_within_limit(self):
        rt = RoundTrip(ts_code="A", name="A", buy_date=date(2026, 7, 13), buy_price=100.0, qty=100,
                       fees=0.0, sell_date=date(2026, 7, 17), sell_price=110.0, closed=True)  # ¥10,000
        out = check_position_count_and_exposure(
            [rt], week_start=date(2026, 7, 13), week_end=date(2026, 7, 17), asof=date(2026, 7, 17),
            max_positions=5, max_exposure_frac=0.6, total_capital=120000.0,
        )
        assert out == []

    def test_open_round_trip_counted_through_asof(self):
        """未平仓回合应按"数据截止日"计入敞口/并发计数(见模块 docstring 已知简化1)。"""
        rt = RoundTrip(ts_code="A", name="A", buy_date=date(2026, 7, 13), buy_price=1000.0, qty=100,
                       fees=0.0, closed=False)   # 未平仓,买入 ¥100,000
        out = check_position_count_and_exposure(
            [rt], week_start=date(2026, 7, 13), week_end=date(2026, 7, 17), asof=date(2026, 7, 17),
            max_positions=5, max_exposure_frac=0.6, total_capital=120000.0,
        )
        assert any("持仓总敞口最高达" in msg for msg in out)

    def test_non_overlapping_week_not_counted(self):
        rt = RoundTrip(ts_code="A", name="A", buy_date=date(2026, 6, 1), buy_price=1000.0, qty=100,
                       fees=0.0, sell_date=date(2026, 6, 2), sell_price=1100.0, closed=True)
        out = check_position_count_and_exposure(
            [rt], week_start=date(2026, 7, 13), week_end=date(2026, 7, 17), asof=date(2026, 7, 17),
            max_positions=5, max_exposure_frac=0.6, total_capital=120000.0,
        )
        assert out == []


class TestEntryScreens:
    @pytest.fixture
    def market(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        return isolated_env, dates

    def test_st_purchase_flagged(self, market):
        settings, dates = market
        seed_active_rule_v1(settings)
        from neckline.strategy import brain
        from neckline.strategy.momentum import MomentumConfig
        cfg = MomentumConfig(**brain.get_active(db_path=settings.db_path).rule["config"])
        buy_day = dates[25]
        trades = [_trade(buy_day, "600002.SH", "buy", 10.0, 100, name="*ST示例乙")]
        out = check_entry_screens(trades, cfg, parquet_dir=settings.parquet_dir)
        assert any("ST/*ST" in msg for msg in out)

    def test_high_elasticity_flagged_when_enabled(self, market):
        settings, dates = market
        seed_active_rule_v1(settings, {"forbid_high_elasticity": True})
        from neckline.strategy import brain
        from neckline.strategy.momentum import MomentumConfig
        cfg = MomentumConfig(**brain.get_active(db_path=settings.db_path).rule["config"])
        buy_day = dates[25]
        trades = [_trade(buy_day, "300001.SZ", "buy", 10.0, 100, name="示例丙")]
        out = check_entry_screens(trades, cfg, parquet_dir=settings.parquet_dir)
        assert any("高弹题材" in msg for msg in out)

    def test_high_elasticity_not_flagged_when_disabled(self, market):
        settings, dates = market
        seed_active_rule_v1(settings, {"forbid_high_elasticity": False})
        from neckline.strategy import brain
        from neckline.strategy.momentum import MomentumConfig
        cfg = MomentumConfig(**brain.get_active(db_path=settings.db_path).rule["config"])
        buy_day = dates[25]
        trades = [_trade(buy_day, "300001.SZ", "buy", 10.0, 100, name="示例丙")]
        out = check_entry_screens(trades, cfg, parquet_dir=settings.parquet_dir)
        assert out == []

    def test_green_bigdown_flagged_when_enabled(self, market):
        settings, dates = market
        seed_active_rule_v1(settings, {"forbid_green_bigdown": -0.005})
        from neckline.strategy import brain
        from neckline.strategy.momentum import MomentumConfig
        cfg = MomentumConfig(**brain.get_active(db_path=settings.db_path).rule["config"])
        last_day = dates[-1]   # -1% 回调日(seed_synthetic_market 设计)
        trades = [_trade(last_day, "600001.SH", "buy", 10.0, 100, name="示例甲")]
        out = check_entry_screens(trades, cfg, parquet_dir=settings.parquet_dir)
        assert any("绿盘大阴线" in msg for msg in out)

    def test_far_from_high_flagged_when_enabled(self, market):
        settings, dates = market
        seed_active_rule_v1(settings, {"forbid_far_from_high": -0.005})
        from neckline.strategy import brain
        from neckline.strategy.momentum import MomentumConfig
        cfg = MomentumConfig(**brain.get_active(db_path=settings.db_path).rule["config"])
        last_day = dates[-1]
        trades = [_trade(last_day, "600001.SH", "buy", 10.0, 100, name="示例甲")]
        out = check_entry_screens(trades, cfg, parquet_dir=settings.parquet_dir)
        assert any("距 20 日高点过远" in msg for msg in out)

    def test_new_stock_flagged_when_enabled(self, market):
        settings, dates = market
        seed_active_rule_v1(settings, {"forbid_new_days": 500})   # 样本 days_since_listing≈400~406,小于500
        from neckline.strategy import brain
        from neckline.strategy.momentum import MomentumConfig
        cfg = MomentumConfig(**brain.get_active(db_path=settings.db_path).rule["config"])
        buy_day = dates[25]
        trades = [_trade(buy_day, "600001.SH", "buy", 10.0, 100, name="示例甲")]
        out = check_entry_screens(trades, cfg, parquet_dir=settings.parquet_dir)
        assert any("次新股" in msg for msg in out)

    def test_clean_day_no_violations(self, market):
        settings, dates = market
        seed_active_rule_v1(settings, {
            "forbid_green_bigdown": -0.03, "forbid_far_from_high": -0.15,
            "forbid_new_days": 120, "forbid_high_elasticity": True,
        })
        from neckline.strategy import brain
        from neckline.strategy.momentum import MomentumConfig
        cfg = MomentumConfig(**brain.get_active(db_path=settings.db_path).rule["config"])
        clean_day = dates[25]   # ret_1d=+1%, dist_from_high_20d=0,主板非ST
        trades = [_trade(clean_day, "600001.SH", "buy", 10.0, 100, name="示例甲")]
        out = check_entry_screens(trades, cfg, parquet_dir=settings.parquet_dir)
        assert out == []


class TestCooldown:
    def test_reentry_within_cooldown_flagged(self, isolated_env):
        d0 = date(2024, 1, 2)  # 周二
        days = business_days(d0, 30)
        insert_trade_cal(isolated_env, days)
        rts = [
            RoundTrip(ts_code="600519.SH", name="x", buy_date=days[0], buy_price=100.0, qty=100,
                      fees=0.0, sell_date=days[2], sell_price=90.0, closed=True),  # 亏损离场
            RoundTrip(ts_code="600519.SH", name="x", buy_date=days[3], buy_price=91.0, qty=100,
                      fees=0.0, closed=False),   # 冷却期内重新买入
        ]
        out = check_cooldown(rts, cooldown_days=5)
        assert len(out) == 1 and "冷却纪律" in out[0]

    def test_reentry_after_cooldown_not_flagged(self, isolated_env):
        d0 = date(2024, 1, 2)
        days = business_days(d0, 30)
        insert_trade_cal(isolated_env, days)
        rts = [
            RoundTrip(ts_code="600519.SH", name="x", buy_date=days[0], buy_price=100.0, qty=100,
                      fees=0.0, sell_date=days[2], sell_price=90.0, closed=True),
            RoundTrip(ts_code="600519.SH", name="x", buy_date=days[10], buy_price=91.0, qty=100,
                      fees=0.0, closed=False),
        ]
        out = check_cooldown(rts, cooldown_days=5)
        assert out == []

    def test_zero_cooldown_is_noop(self):
        rts = [
            RoundTrip(ts_code="600519.SH", name="x", buy_date=date(2026, 7, 1), buy_price=100.0, qty=100,
                      fees=0.0, sell_date=date(2026, 7, 2), sell_price=50.0, closed=True),
            RoundTrip(ts_code="600519.SH", name="x", buy_date=date(2026, 7, 3), buy_price=51.0, qty=100,
                      fees=0.0, closed=False),
        ]
        assert check_cooldown(rts, cooldown_days=0) == []

    def test_profitable_exit_does_not_trigger_cooldown(self):
        rts = [
            RoundTrip(ts_code="600519.SH", name="x", buy_date=date(2026, 7, 1), buy_price=100.0, qty=100,
                      fees=0.0, sell_date=date(2026, 7, 2), sell_price=150.0, closed=True),  # 盈利离场
            RoundTrip(ts_code="600519.SH", name="x", buy_date=date(2026, 7, 3), buy_price=151.0, qty=100,
                      fees=0.0, closed=False),
        ]
        assert check_cooldown(rts, cooldown_days=5) == []


# ======================================================================
#  周统计 + 强制复盘
# ======================================================================

class TestWeeklyStats:
    def _rt(self, buy, sell, qty=100, fees=0.0):
        return RoundTrip(ts_code="X", name="X", buy_date=date(2026, 7, 13), buy_price=buy, qty=qty,
                         fees=fees, sell_date=date(2026, 7, 15), sell_price=sell, closed=True)

    def test_win_rate_and_profit_factor(self):
        rts = [self._rt(10, 12), self._rt(10, 12), self._rt(10, 8)]  # 2 赢 1 输
        stats = compute_weekly_stats(rts, open_count=0)
        assert stats.closed_count == 3
        assert stats.win_rate == pytest.approx(2 / 3)
        # gross wins = 200*2=400, gross loss=200 -> pf=2.0
        assert stats.profit_factor == pytest.approx(2.0)

    def test_all_wins_profit_factor_infinite(self):
        rts = [self._rt(10, 12)]
        stats = compute_weekly_stats(rts, open_count=0)
        assert stats.profit_factor == float("inf")

    def test_no_closed_trades(self):
        stats = compute_weekly_stats([], open_count=2)
        assert stats.closed_count == 0 and stats.open_count == 2
        assert stats.win_rate == 0.0
        assert stats.realized_loss == 0.0

    def test_realized_loss_only_sums_losses(self):
        """§2.1 第4条口径:只累加亏损,不被同周盈利冲抵(同 momentum.py week_loss)。"""
        rts = [self._rt(10, 20), self._rt(10, 5)]   # 赢1000,输500
        stats = compute_weekly_stats(rts, open_count=0)
        assert stats.realized_loss == pytest.approx(-500.0)
        assert stats.realized_pnl == pytest.approx(500.0)   # 净盈亏是正的,但 realized_loss 仍是 -500

    def test_fees_reduce_net_pnl(self):
        rts = [self._rt(10, 12, fees=50.0)]
        stats = compute_weekly_stats(rts, open_count=0)
        assert stats.gross_pnl == pytest.approx(200.0)
        assert stats.realized_pnl == pytest.approx(150.0)
        assert stats.total_fees == pytest.approx(50.0)


class TestForcedReview:
    def _stats_with_loss(self, loss: float):
        return compute_weekly_stats(
            [RoundTrip(ts_code="X", name="X", buy_date=date(2026, 7, 13), buy_price=100.0, qty=100,
                      fees=0.0, sell_date=date(2026, 7, 15), sell_price=100.0 + loss / 100, closed=True)],
            open_count=0,
        )

    def test_exactly_at_threshold_triggers(self):
        total_capital = 120000.0
        loss = -FORCED_REVIEW_LOSS_FRAC * total_capital   # 恰好 -2%
        stats = self._stats_with_loss(loss)
        assert is_forced_review(stats, total_capital) is True

    def test_just_under_threshold_does_not_trigger(self):
        total_capital = 120000.0
        loss = -FORCED_REVIEW_LOSS_FRAC * total_capital + 50.0   # 差一点没到 2%
        stats = self._stats_with_loss(loss)
        assert is_forced_review(stats, total_capital) is False

    def test_over_threshold_triggers(self):
        total_capital = 120000.0
        loss = -FORCED_REVIEW_LOSS_FRAC * total_capital - 1000.0
        stats = self._stats_with_loss(loss)
        assert is_forced_review(stats, total_capital) is True

    def test_profit_week_never_triggers(self):
        total_capital = 120000.0
        stats = self._stats_with_loss(5000.0)   # 正数=盈利,realized_loss 应为 0
        assert stats.realized_loss == 0.0
        assert is_forced_review(stats, total_capital) is False


class TestIsoWeek:
    def test_key_and_range_roundtrip(self):
        d = date(2026, 7, 16)   # 周四
        key = iso_week_key(d)
        start, end = week_range(key)
        assert start.weekday() == 0 and end.weekday() == 6
        assert start <= d <= end

    def test_week_boundary_sunday_monday(self):
        sunday = date(2026, 7, 19)
        monday = date(2026, 7, 20)
        assert iso_week_key(sunday) != iso_week_key(monday)


# ======================================================================
#  计划内/计划外 + 持仓台账对账(对账三查①,直接单测,不经 run_weekly_review)
# ======================================================================

class TestCheckPlanAndLedger:
    def test_in_report_candidates_is_plan_in(self, isolated_env):
        from neckline.report import store as report_store

        report_store.save_report(
            date(2026, 7, 14), strategy_version="v1", sentiment={}, sectors={},
            candidates=[{"ts_code": "600519.SH", "rank": 1, "name": "贵州茅台"}],
            markdown="", db_path=isolated_env.db_path,
        )
        trades = [_trade(date(2026, 7, 14), "600519.SH", "buy", 100.0, 100)]
        out = check_plan_and_ledger(trades, db_path=isolated_env.db_path)
        assert out[0].plan_status == "计划内(当日报告候选)"

    def test_in_inquiry_pool_is_plan_in(self, isolated_env):
        from neckline.api.stores import add_to_inquiry_pool
        from neckline.report import store as report_store

        report_store.save_report(
            date(2026, 7, 14), strategy_version="v1", sentiment={}, sectors={},
            candidates=[], markdown="", db_path=isolated_env.db_path,
        )
        add_to_inquiry_pool(date(2026, 7, 14), "600519.SH", db_path=isolated_env.db_path)
        trades = [_trade(date(2026, 7, 14), "600519.SH", "buy", 100.0, 100)]
        out = check_plan_and_ledger(trades, db_path=isolated_env.db_path)
        assert out[0].plan_status == "计划内(问询台海选池)"

    def test_not_in_report_or_pool_is_off_plan(self, isolated_env):
        from neckline.report import store as report_store

        report_store.save_report(
            date(2026, 7, 14), strategy_version="v1", sentiment={}, sectors={},
            candidates=[], markdown="", db_path=isolated_env.db_path,
        )
        trades = [_trade(date(2026, 7, 14), "600519.SH", "buy", 100.0, 100)]
        out = check_plan_and_ledger(trades, db_path=isolated_env.db_path)
        assert out[0].plan_status == "计划外(未经系统候选/海选池放行的自主买入)"

    def test_no_report_generated_that_day(self, isolated_env):
        trades = [_trade(date(2026, 7, 14), "600519.SH", "buy", 100.0, 100)]
        out = check_plan_and_ledger(trades, db_path=isolated_env.db_path)
        assert out[0].plan_status.startswith("无报告数据")

    def test_ledger_matched(self, isolated_env):
        from neckline.sentinel.positions import open_position

        open_position("600519.SH", buy_price=100.0, qty=100, buy_date=date(2026, 7, 14), db_path=isolated_env.db_path)
        trades = [_trade(date(2026, 7, 14), "600519.SH", "buy", 100.0, 100)]
        out = check_plan_and_ledger(trades, db_path=isolated_env.db_path)
        assert out[0].ledger_status == "台账已录"

    def test_ledger_price_mismatch(self, isolated_env):
        from neckline.sentinel.positions import open_position

        open_position("600519.SH", buy_price=100.0, qty=100, buy_date=date(2026, 7, 14), db_path=isolated_env.db_path)
        trades = [_trade(date(2026, 7, 14), "600519.SH", "buy", 150.0, 100)]   # 台账 100 vs 交割单 150,差 50%
        out = check_plan_and_ledger(trades, db_path=isolated_env.db_path)
        assert out[0].ledger_status.startswith("台账记录价格不符")

    def test_ledger_missing(self, isolated_env):
        trades = [_trade(date(2026, 7, 14), "600519.SH", "buy", 100.0, 100)]
        out = check_plan_and_ledger(trades, db_path=isolated_env.db_path)
        assert out[0].ledger_status.startswith("台账缺失")

    def test_amount_property(self, isolated_env):
        trades = [_trade(date(2026, 7, 14), "600519.SH", "buy", 150.0, 100)]
        out = check_plan_and_ledger(trades, db_path=isolated_env.db_path)
        assert out[0].amount == pytest.approx(15000.0)


# ======================================================================
#  端到端:run_weekly_review()
# ======================================================================

class TestRunWeeklyReview:
    def test_plan_and_ledger_status(self, isolated_env):
        seed_active_rule_v1(isolated_env)
        insert_stock_basic(isolated_env, [
            {"ts_code": "600519.SH", "name": "贵州茅台", "market": "主板"},
            {"ts_code": "300750.SZ", "name": "宁德时代", "market": "创业板"},
        ])

        from neckline.report import store as report_store
        report_store.save_report(
            date(2026, 7, 14), strategy_version="v1", sentiment={}, sectors={},
            candidates=[{"ts_code": "600519.SH", "rank": 1, "name": "贵州茅台"}],
            markdown="", db_path=isolated_env.db_path,
        )

        from neckline.sentinel.positions import open_position
        open_position("600519.SH", buy_price=1500.0, qty=100, buy_date=date(2026, 7, 14),
                      db_path=isolated_env.db_path)

        trades = [
            _trade(date(2026, 7, 14), "600519.SH", "buy", 1500.0, 100, name="贵州茅台"),   # 计划内 + 台账已录
            _trade(date(2026, 7, 14), "300750.SZ", "buy", 200.0, 100, name="宁德时代"),    # 计划外 + 台账缺失
            _trade(date(2026, 7, 16), "600519.SH", "sell", 1424.7, 100, name="贵州茅台"),
        ]
        reviews, warnings = run_weekly_review(trades, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert warnings == []
        assert len(reviews) == 1
        review = reviews[0]
        by_code = {c.ts_code: c for c in review.plan_checks}
        assert by_code["600519.SH"].plan_status.startswith("计划内")
        assert by_code["600519.SH"].ledger_status == "台账已录"
        assert by_code["300750.SZ"].plan_status.startswith("计划外")
        assert by_code["300750.SZ"].ledger_status.startswith("台账缺失")

    def test_stop_breach_and_single_cap_violation(self, isolated_env):
        seed_active_rule_v1(isolated_env)  # single_cap=20000, stop_pct=0.05
        trades = [
            _trade(date(2026, 7, 14), "600519.SH", "buy", 300.0, 100, name="贵州茅台"),   # ¥30,000 > 2万
            _trade(date(2026, 7, 16), "600519.SH", "sell", 280.0, 100, name="贵州茅台"),  # -6.67%,破止损
        ]
        reviews, _ = run_weekly_review(trades, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        review = reviews[0]
        assert any("超过单笔仓位上限" in v for v in review.discipline_violations)
        assert any("疑似未按 -5% 止损离场" in v for v in review.discipline_violations)
        assert review.stats.closed_count == 1

    def test_forced_review_triggered_end_to_end(self, isolated_env):
        seed_active_rule_v1(isolated_env)
        total_capital = 120000.0
        # 单笔亏损 ¥3000(=2.5%总仓) > 2% 触发线
        trades = [
            _trade(date(2026, 7, 14), "600519.SH", "buy", 100.0, 100, name="贵州茅台"),
            _trade(date(2026, 7, 16), "600519.SH", "sell", 70.0, 100, name="贵州茅台"),
        ]
        reviews, _ = run_weekly_review(
            trades, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir, total_capital=total_capital,
        )
        review = reviews[0]
        assert review.forced_review is True
        assert "强制复盘" in review.forced_review_reason

    def test_no_active_strategy_version_still_produces_report(self, isolated_env):
        """无现役大脑版本时,止损/章程检查诚实跳过,但计划核对与统计仍应产出。"""
        trades = [
            _trade(date(2026, 7, 14), "600519.SH", "buy", 100.0, 100, name="贵州茅台"),
            _trade(date(2026, 7, 16), "600519.SH", "sell", 110.0, 100, name="贵州茅台"),
        ]
        reviews, warnings = run_weekly_review(trades, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert len(reviews) == 1
        review = reviews[0]
        assert review.stats.closed_count == 1
        assert all(kind == STOP_NOT_APPLICABLE for _, kind, _ in review.stop_discipline)

    def test_empty_trades_returns_empty(self, isolated_env):
        reviews, warnings = run_weekly_review([], db_path=isolated_env.db_path)
        assert reviews == [] and warnings == []

    def test_weekly_review_dict_json_safe(self, isolated_env):
        """profit_factor/profit_loss_ratio 为 inf 时,序列化应变 None(JSON 无 Infinity)。"""
        import json

        seed_active_rule_v1(isolated_env)
        trades = [
            _trade(date(2026, 7, 14), "600519.SH", "buy", 100.0, 100, name="贵州茅台"),
            _trade(date(2026, 7, 16), "600519.SH", "sell", 110.0, 100, name="贵州茅台"),  # 全赢 -> pf=inf
        ]
        reviews, _ = run_weekly_review(trades, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        d = weekly_review_dict(reviews[0])
        raw = json.dumps(d)   # 不应抛异常,且不应含字面 Infinity
        assert "Infinity" not in raw
        assert d["stats"]["profitFactor"] is None


# ======================================================================
#  v1.2-A:历史洗白修复(按周取「当时现役」config)—— 命门反例 + 时间线双向
# ======================================================================

def _seed_two_charter_timeline(db_path):
    """落 K1(single_cap=2万)+ v1.2(single_cap=4万,三仓制),造确定激活时间线:
    K1 激活 2026-07-20、v1.2 激活 2026-08-01(现役)。返回 (k1_cfg, v12_cfg)。"""
    from neckline.db import connection
    from neckline.strategy import brain
    from .conftest import TEST_RULE_V1_CONFIG

    k1_cfg = dict(TEST_RULE_V1_CONFIG)                       # single_cap=20000, max_positions=5
    v12_cfg = dict(TEST_RULE_V1_CONFIG)
    v12_cfg.update(single_cap=40000.0, max_positions=3, max_exposure_frac=1.0)
    brain.save_version("K1", {"config": k1_cfg}, "k1", activate=True, db_path=db_path)
    brain.save_version("v1.2", {"config": v12_cfg, "lineage": "K1"}, "v1.2", activate=False, db_path=db_path)
    with connection(db_path) as conn:
        conn.execute("UPDATE strategy_versions SET activated_at=?, is_active=0 WHERE version='K1'",
                     ("2026-07-20T00:00:00+00:00",))
        conn.execute("UPDATE strategy_versions SET activated_at=?, is_active=1 WHERE version='v1.2'",
                     ("2026-08-01T00:00:00+00:00",))
    return k1_cfg, v12_cfg


class TestHistoryWhitewashFix:
    def test_historical_violation_survives_activation(self, isolated_env):
        """命门反例(plan A 验收①点名):历史周一笔 3 万买入(K1 2万上限下违纪),
        激活 v1.2(4万上限)后重跑该历史周 → **仍报违纪,不被 4 万上限洗白**。"""
        _seed_two_charter_timeline(isolated_env.db_path)
        # 历史周(2026-07-22 周,week_end 2026-07-26,落在 K1↔v1.2 激活之间)一笔 3 万
        trades = [
            _trade(date(2026, 7, 22), "600519.SH", "buy", 300.0, 100, name="贵州茅台"),   # ¥30,000 > 2万
            _trade(date(2026, 7, 24), "600519.SH", "sell", 305.0, 100, name="贵州茅台"),
        ]
        reviews, _ = run_weekly_review(
            trades, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        hist = next(r for r in reviews if r.week == iso_week_key(date(2026, 7, 22)))
        assert hist.strategy_version == "K1"                 # 该周 governing = K1
        assert any("超过单笔仓位上限" in v for v in hist.discipline_violations)  # 未被洗白

    def test_post_activation_week_uses_new_cap(self, isolated_env):
        """对照方向:激活后的周(week_end > v1.2 激活)同样一笔 3 万 → v1.2(4万)判,
        不违纪(证明时间线解析确实按周切换 governing,不是一刀切)。"""
        _seed_two_charter_timeline(isolated_env.db_path)
        trades = [
            _trade(date(2026, 8, 5), "600519.SH", "buy", 300.0, 100, name="贵州茅台"),    # ¥30,000 ≤ 4万
            _trade(date(2026, 8, 7), "600519.SH", "sell", 305.0, 100, name="贵州茅台"),
        ]
        reviews, _ = run_weekly_review(
            trades, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        post = next(r for r in reviews if r.week == iso_week_key(date(2026, 8, 5)))
        assert post.strategy_version == "v1.2"
        assert not any("超过单笔仓位上限" in v for v in post.discipline_violations)

    def test_strategy_version_in_weekly_review_dict(self, isolated_env):
        """审计 🔵-9:API 响应/落库形状带上 `strategyVersion`(此前列已落库但客户端看不到)。"""
        _seed_two_charter_timeline(isolated_env.db_path)
        trades = [
            _trade(date(2026, 7, 22), "600519.SH", "buy", 300.0, 100, name="贵州茅台"),
            _trade(date(2026, 7, 24), "600519.SH", "sell", 305.0, 100, name="贵州茅台"),
        ]
        reviews, _ = run_weekly_review(
            trades, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        hist = next(r for r in reviews if r.week == iso_week_key(date(2026, 7, 22)))
        assert weekly_review_dict(hist)["strategyVersion"] == "K1"

    def test_strategy_version_persisted_to_reviews(self, isolated_env):
        """reviews.strategy_version 按周落库正确(governing 版本号写进列)。"""
        from neckline.review.store import save_weekly_review
        from neckline.db import connection

        _seed_two_charter_timeline(isolated_env.db_path)
        trades = [
            _trade(date(2026, 7, 22), "600519.SH", "buy", 300.0, 100, name="贵州茅台"),
            _trade(date(2026, 7, 24), "600519.SH", "sell", 305.0, 100, name="贵州茅台"),
        ]
        reviews, _ = run_weekly_review(
            trades, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        hist = next(r for r in reviews if r.week == iso_week_key(date(2026, 7, 22)))
        save_weekly_review(hist, db_path=isolated_env.db_path)
        with connection(isolated_env.db_path) as conn:
            row = conn.execute(
                "SELECT strategy_version FROM reviews WHERE week=?", (hist.week,)
            ).fetchone()
        assert row[0] == "K1"


# ======================================================================
#  审计 🟡-3(2026-07-27):激活时点不得洗白「刚结束的那一周」
#  判据改「激活日 < week_start」(方案 (a),不依赖人的操作纪律)。
#  ⚠ 时间锚:2026-07-20 是周一,2026-07-26 是周日 → 07-20~07-26 是一个完整 ISO 周;
#     下一周是 07-27(周一)~08-02(周日)。
# ======================================================================

def _seed_activated_at(db_path, activated_at: str):
    """K1(2 万上限,激活 2026-07-13)+ v1.3(4 万上限)按给定时间戳激活为现役。"""
    from neckline.db import connection
    from neckline.strategy import brain
    from .conftest import TEST_RULE_V1_CONFIG

    k1 = dict(TEST_RULE_V1_CONFIG)
    v13 = dict(TEST_RULE_V1_CONFIG)
    v13.update(single_cap=40000.0, max_positions=3, max_exposure_frac=1.0)
    brain.save_version("K1", {"config": k1}, "k1", activate=True, db_path=db_path)
    brain.save_version("v1.3", {"config": v13, "lineage": "K1"}, "v1.3", activate=False, db_path=db_path)
    with connection(db_path) as conn:
        conn.execute("UPDATE strategy_versions SET activated_at=?, is_active=0 WHERE version='K1'",
                     ("2026-07-13T00:00:00+00:00",))
        conn.execute("UPDATE strategy_versions SET activated_at=?, is_active=1 WHERE version='v1.3'",
                     (activated_at,))


def _week_of(isolated_env, trades, day: date):
    reviews, _ = run_weekly_review(
        trades, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    return next(r for r in reviews if r.week == iso_week_key(day))


def _30k_trades(buy_day: date, sell_day: date):
    """一笔 3 万买入 + 平仓(K1 2 万上限下违纪;v1.3 4 万上限下不违纪)。"""
    return [
        _trade(buy_day, "600519.SH", "buy", 300.0, 100, name="贵州茅台"),
        _trade(sell_day, "600519.SH", "sell", 305.0, 100, name="贵州茅台"),
    ]


class TestActivationTimingNoWhitewash:
    """三种激活时点(周中 / 周末 / 跨时区凌晨)下,**刚结束那一周一律仍按旧章程判**。"""

    @pytest.mark.parametrize("activated_at,label", [
        ("2026-07-22T10:00:00+00:00", "周中(周三)激活"),
        ("2026-07-25T10:00:00+00:00", "周末(周六)激活"),
        ("2026-07-26T18:00:00+00:00", "北京周一 02:00 激活(UTC 戳落周日)"),
        ("2026-07-26T23:59:59+00:00", "周日午夜(UTC)激活"),
    ])
    def test_just_ended_week_still_judged_by_old_charter(self, isolated_env, activated_at, label):
        """审计实测反例:07-20~07-26 周内一笔 3 万(K1 下违纪 1 条)。旧判据(激活日 ≤
        week_end=周日)在周末/北京周一凌晨激活时会把该周整周交给 v1.3(4 万)判 → 违纪
        1 条变 0 条。新判据(激活日 < week_start=07-20)下,四种时点一律仍报违纪。"""
        _seed_activated_at(isolated_env.db_path, activated_at)
        trades = _30k_trades(date(2026, 7, 22), date(2026, 7, 24))
        wk = _week_of(isolated_env, trades, date(2026, 7, 22))
        assert wk.strategy_version == "K1", label
        assert any("超过单笔仓位上限" in v for v in wk.discipline_violations), label

    def test_next_full_week_uses_new_charter(self, isolated_env):
        """阳性对照:激活后的**下一个完整周**(07-27~08-02)按 v1.3(4 万)判,同样一笔
        3 万不违纪——闸没有把新章程一起堵死。"""
        _seed_activated_at(isolated_env.db_path, "2026-07-25T10:00:00+00:00")   # 周六激活
        trades = _30k_trades(date(2026, 7, 28), date(2026, 7, 30))
        wk = _week_of(isolated_env, trades, date(2026, 7, 28))
        assert wk.strategy_version == "v1.3"
        assert not any("超过单笔仓位上限" in v for v in wk.discipline_violations)

    def test_activation_week_now_judged_per_trade(self, isolated_env):
        """⚠ **v1.4-⑥-A 起本用例的期望被规格取代**(§七 P1-4):激活当周的成交不再整周
        按旧章程判,而是**按成交时刻逐笔判** —— 周三北京 18:00 激活、周四买的那笔 3 万落在
        激活**之后** → 按 v1.3(4 万)判,**不违纪**(旧行为在这里报违纪 = 用户按新章程打却
        被误标的假警报,正是 P1-4 要修的病)。

        🟡-3 的保护并未削弱,而是更严:**已经结束的那一周**里的成交,其成交时刻必然早于
        之后才发生的激活 → 恒按旧章程判(同 class 上面四个参数化用例仍绿)。
        `strategy_version` 是**周初标签**,语义未变,仍是 K1。"""
        _seed_activated_at(isolated_env.db_path, "2026-07-22T10:00:00+00:00")   # 周三北京 18:00 激活
        trades = _30k_trades(date(2026, 7, 23), date(2026, 7, 24))              # 周四买、周五卖
        wk = _week_of(isolated_env, trades, date(2026, 7, 23))
        assert wk.strategy_version == "K1"                       # 周初标签口径不变
        assert not any("超过单笔仓位上限" in v for v in wk.discipline_violations)
        assert [s.version for s in wk.charter_segments] == ["K1", "v1.3"]
        assert [sw.to_version for sw in wk.charter_switches] == ["v1.3"]

    def test_governing_resolver_boundary(self, isolated_env):
        """`config_governing_for_week` 边界:激活日 == week_start 那一周仍归旧版本
        (严格 `<`),week_start 之后一周才归新版本。"""
        from neckline.strategy import brain

        _seed_activated_at(isolated_env.db_path, "2026-07-20T00:00:00+00:00")   # 恰在 week_start 当天
        db = isolated_env.db_path
        assert brain.config_governing_for_week(date(2026, 7, 20), db_path=db).version == "K1"
        assert brain.config_governing_for_week(date(2026, 7, 27), db_path=db).version == "v1.3"


# ======================================================================
#  审计 🔵-9:周复盘补「时间退出违纪」审计项(§2.1 第 2 条的周线兜底)
#  —— 配合同批 🔴-1「D5 判一次定格」:系统说该走、台账显示没走 = 违纪,事后有人兜底。
# ======================================================================

class _DueItem:
    """duck-typed HoldingK4Item(只喂 holding_store 落库需要的字段)。"""
    def __init__(self, pid, td_state, d=5):
        self.position_id, self.time_exit_state, self.d_count = pid, td_state, d
        self.net_float, self.max_hold_effective = -10.0, 5
        self.has_strong = self.scenario_review = False
        self.time_exit_locked_state = td_state if td_state == "time_exit_next_day" else None
        self.time_exit_locked_date = None
        self.time_exit_locked_net_float = None
    def hits_public(self):
        return []


def _seed_time_exit_case(env, *, decided: date, sell_date, state="time_exit_next_day"):
    """建一笔持仓 + 在 `decided` 那天落一条「系统判该走」的 EOD 体检行;`sell_date` 为 None
    表示至今未平仓。返回 position_id。"""
    from neckline.report import holding_store
    from neckline.sentinel.positions import close_position, open_position

    pid = open_position("600519.SH", 300.0, 100, date(2026, 7, 20), db_path=env.db_path)
    holding_store.save_holding_eod_checks(decided, [_DueItem(pid, state)], db_path=env.db_path)
    if sell_date is not None:
        close_position(pid, 305.0, sell_date, db_path=env.db_path)
    return pid


def _violations_for(env, day: date):
    """跑一次周复盘(交割单只用来把该周纳入统计),取该周的违纪列表。"""
    trades = [
        _trade(date(2026, 7, 22), "600519.SH", "buy", 300.0, 100, name="贵州茅台"),
        _trade(date(2026, 7, 24), "600519.SH", "sell", 305.0, 100, name="贵州茅台"),
    ]
    reviews, _ = run_weekly_review(trades, db_path=env.db_path, parquet_dir=env.parquet_dir)
    wk = next(r for r in reviews if r.week == iso_week_key(day))
    return wk.discipline_violations


class TestTimeExitDiscipline:
    """双向:该记的记(没走/晚走),不该记的不记(按时走/提前走/系统没说该走)。"""

    def test_still_open_past_due_is_violation(self, isolated_env):
        """系统 07-22 判该走 → 应 07-23 离场,台账至今未平仓 → 违纪。"""
        from tests.conftest import seed_active_rule_v1
        seed_active_rule_v1(isolated_env)
        _seed_time_exit_case(isolated_env, decided=date(2026, 7, 22), sell_date=None)
        msgs = _violations_for(isolated_env, date(2026, 7, 22))
        assert any("时间退出纪律" in m and "仍未平仓" in m for m in msgs)

    def test_sold_late_is_violation(self, isolated_env):
        """应 07-23 离场,实际 07-27 才卖 → 违纪(并报晚了几个交易日)。"""
        from tests.conftest import seed_active_rule_v1
        seed_active_rule_v1(isolated_env)
        _seed_time_exit_case(isolated_env, decided=date(2026, 7, 22), sell_date=date(2026, 7, 27))
        msgs = _violations_for(isolated_env, date(2026, 7, 22))
        assert any("时间退出纪律" in m and "实际卖出日" in m for m in msgs)

    def test_sold_on_due_day_is_clean(self, isolated_env):
        """阴性方向:恰在应离场日 07-23 卖出 → 不算违纪。"""
        from tests.conftest import seed_active_rule_v1
        seed_active_rule_v1(isolated_env)
        _seed_time_exit_case(isolated_env, decided=date(2026, 7, 22), sell_date=date(2026, 7, 23))
        assert not any("时间退出纪律" in m for m in _violations_for(isolated_env, date(2026, 7, 22)))

    def test_sold_earlier_is_clean(self, isolated_env):
        """阴性方向:比应离场日更早离场(更严的自律)→ 不罚。"""
        from tests.conftest import seed_active_rule_v1
        seed_active_rule_v1(isolated_env)
        _seed_time_exit_case(isolated_env, decided=date(2026, 7, 22), sell_date=date(2026, 7, 22))
        assert not any("时间退出纪律" in m for m in _violations_for(isolated_env, date(2026, 7, 22)))

    def test_never_flagged_when_system_never_said_exit(self, isolated_env):
        """阴性方向:系统只记了 holding/profit_exempt(没说该走)→ 永不记违纪。"""
        from neckline.report import holding_store
        from neckline.sentinel.positions import open_position
        from tests.conftest import seed_active_rule_v1
        seed_active_rule_v1(isolated_env)
        pid = open_position("600519.SH", 300.0, 100, date(2026, 7, 20), db_path=isolated_env.db_path)
        holding_store.save_holding_eod_checks(
            date(2026, 7, 22), [_DueItem(pid, "profit_exempt")], db_path=isolated_env.db_path)
        assert not any("时间退出纪律" in m for m in _violations_for(isolated_env, date(2026, 7, 22)))

    def test_hard_cap_kind_labelled(self, isolated_env):
        """D15 硬上限未走 → 违纪文案标「浮盈硬上限」(与 D5 档区分,便于归因)。"""
        from tests.conftest import seed_active_rule_v1
        seed_active_rule_v1(isolated_env)
        _seed_time_exit_case(isolated_env, decided=date(2026, 7, 22), sell_date=None,
                             state="hard_cap_exit")
        msgs = _violations_for(isolated_env, date(2026, 7, 22))
        assert any("浮盈硬上限" in m for m in msgs)

    def test_violation_belongs_to_week_of_due_date(self, isolated_env):
        """归属周:应离场日落在哪周就记在哪周(不串到别的周)。"""
        from tests.conftest import seed_active_rule_v1
        seed_active_rule_v1(isolated_env)
        _seed_time_exit_case(isolated_env, decided=date(2026, 7, 22), sell_date=None)
        trades = [
            _trade(date(2026, 7, 22), "600519.SH", "buy", 300.0, 100, name="贵州茅台"),
            _trade(date(2026, 7, 24), "600519.SH", "sell", 305.0, 100, name="贵州茅台"),
            _trade(date(2026, 7, 28), "600036.SH", "buy", 30.0, 100, name="招商银行"),
            _trade(date(2026, 7, 30), "600036.SH", "sell", 31.0, 100, name="招商银行"),
        ]
        reviews, _ = run_weekly_review(trades, db_path=isolated_env.db_path,
                                       parquet_dir=isolated_env.parquet_dir)
        by_week = {r.week: r.discipline_violations for r in reviews}
        this_week = by_week[iso_week_key(date(2026, 7, 22))]
        next_week = by_week[iso_week_key(date(2026, 7, 28))]
        assert any("时间退出纪律" in m for m in this_week)
        assert not any("时间退出纪律" in m for m in next_week)

    def test_no_trade_week_still_reported(self, isolated_env):
        """最典型的时间退出违纪就是「拿着没卖、整周没动」—— 该周交割单里一笔成交都没有,
        原来根本不会生成该周 WeeklyReview → 违纪静默丢失。现在该周也纳入(**但只在交割单
        实际覆盖区间内**,区间外沉默=诚实)。"""
        from tests.conftest import seed_active_rule_v1
        seed_active_rule_v1(isolated_env)
        # 系统 07-28(下一周,周二)判该走 → 应 07-29 离场;交割单只有 07-22 那周的成交
        # + 08-05 一笔(把覆盖区间拉到 08-05),07-27~08-02 整周零成交。
        _seed_time_exit_case(isolated_env, decided=date(2026, 7, 28), sell_date=None)
        trades = [
            _trade(date(2026, 7, 22), "600519.SH", "buy", 300.0, 100, name="贵州茅台"),
            _trade(date(2026, 8, 5), "600036.SH", "buy", 30.0, 100, name="招商银行"),
            _trade(date(2026, 8, 6), "600036.SH", "sell", 31.0, 100, name="招商银行"),
        ]
        reviews, _ = run_weekly_review(trades, db_path=isolated_env.db_path,
                                       parquet_dir=isolated_env.parquet_dir)
        by_week = {r.week: r.discipline_violations for r in reviews}
        assert iso_week_key(date(2026, 7, 29)) in by_week          # 零成交周也出报
        assert any("时间退出纪律" in m for m in by_week[iso_week_key(date(2026, 7, 29))])

    def test_due_week_outside_statement_span_stays_silent(self, isolated_env):
        """诚实边界:应离场日落在交割单覆盖区间**之外**(如未来周)→ 不凭半份数据判违纪。"""
        from tests.conftest import seed_active_rule_v1
        seed_active_rule_v1(isolated_env)
        _seed_time_exit_case(isolated_env, decided=date(2026, 9, 15), sell_date=None)  # 远在 asof 之后
        trades = [
            _trade(date(2026, 7, 22), "600519.SH", "buy", 300.0, 100, name="贵州茅台"),
            _trade(date(2026, 7, 24), "600519.SH", "sell", 305.0, 100, name="贵州茅台"),
        ]
        reviews, _ = run_weekly_review(trades, db_path=isolated_env.db_path,
                                       parquet_dir=isolated_env.parquet_dir)
        assert not any("时间退出纪律" in m for r in reviews for m in r.discipline_violations)


# ======================================================================
#  v1.4-⑥-A(§七 P1-4):周复盘章程**按成交时刻逐笔判** 🔴 碰纪律判定
#  ⚠ 时间锚:2026-07-20 周一、07-26 周日;下一周 07-27(周一)~08-02(周日)。
#     `activated_at` 落库是 **UTC** 戳,成交时刻是**北京时间**(UTC+8)。
# ======================================================================

def _seed_charter_pair(db_path, v13_activated_at: str, *, v13_extra: dict = None):
    """K1(2 万上限 / 五仓 / 敞口 60% / 禁高弹,激活 2026-07-13T00:00Z)
    + v1.3(4 万上限 / 三仓 / 敞口 100% / 不禁高弹,按给定 UTC 戳激活为现役)。"""
    from neckline.db import connection
    from neckline.strategy import brain
    from .conftest import TEST_RULE_V1_CONFIG

    k1 = dict(TEST_RULE_V1_CONFIG)
    v13 = dict(TEST_RULE_V1_CONFIG)
    v13.update(single_cap=40000.0, max_positions=3, max_exposure_frac=1.0,
               forbid_high_elasticity=False)
    v13.update(v13_extra or {})
    brain.save_version("K1", {"config": k1}, "k1", activate=True, db_path=db_path)
    brain.save_version("v1.3", {"config": v13, "lineage": "K1"}, "v1.3", activate=False, db_path=db_path)
    with connection(db_path) as conn:
        conn.execute("UPDATE strategy_versions SET activated_at=?, is_active=0 WHERE version='K1'",
                     ("2026-07-13T00:00:00+00:00",))
        conn.execute("UPDATE strategy_versions SET activated_at=?, is_active=1 WHERE version='v1.3'",
                     (v13_activated_at,))


class TestTradeInstant:
    """`trade_instant`:交割单只有日期时按**该日收盘时刻**(北京时间)——⑥-A 定死口径。"""

    def test_date_only_falls_back_to_market_close(self):
        from neckline.calendar import CN_TZ, MARKET_CLOSE_TIME
        from neckline.review.reconcile import trade_instant

        ts = trade_instant(date(2026, 7, 27))
        assert ts.time() == MARKET_CLOSE_TIME == time(15, 0)
        assert ts.utcoffset() == CN_TZ.utcoffset(None)      # 北京时间 aware,不是 naive

    def test_real_time_wins_over_fallback(self):
        from neckline.review.reconcile import trade_instant

        assert trade_instant(date(2026, 7, 27), time(10, 30)).time() == time(10, 30)

    def test_day_close_instant_shares_the_same_source(self):
        from neckline.review.reconcile import day_close_instant, trade_instant

        assert day_close_instant(date(2026, 7, 27)) == trade_instant(date(2026, 7, 27))


class TestPerTradeCharter:
    """① 跨激活时刻的一周成交:切换前后各一笔,判向各按各的章程。"""

    def test_before_and_after_activation_in_same_week(self, isolated_env):
        # 周三北京 18:00 激活(UTC 10:00)。周二买 3 万 → K1 违纪;周四买 3 万 → v1.3 不违纪。
        _seed_charter_pair(isolated_env.db_path, "2026-07-22T10:00:00+00:00")
        trades = [
            _trade(date(2026, 7, 21), "600519.SH", "buy", 300.0, 100, name="切换前"),
            _trade(date(2026, 7, 23), "600036.SH", "buy", 300.0, 100, name="切换后"),
        ]
        wk = _week_of(isolated_env, trades, date(2026, 7, 21))
        caps = [v for v in wk.discipline_violations if "超过单笔仓位上限" in v]
        assert len(caps) == 1
        assert "600519.SH" in caps[0] and "¥20,000" in caps[0]     # 切换前那笔按 K1 判
        assert not any("600036.SH" in v for v in wk.discipline_violations)

    def test_exact_activation_instant_counts_as_new_charter(self, isolated_env):
        """边界定死:成交时刻**恰好等于**激活时刻 → 新章程。激活 UTC 07:00 = 北京 15:00
        = 交割单只有日期时的兜底时刻,故当日那笔按 v1.3(4 万)判,不违纪;
        前一天同样一笔仍按 K1 判,违纪。"""
        _seed_charter_pair(isolated_env.db_path, "2026-07-22T07:00:00+00:00")
        trades = [
            _trade(date(2026, 7, 21), "600519.SH", "buy", 300.0, 100),
            _trade(date(2026, 7, 22), "600036.SH", "buy", 300.0, 100),
        ]
        wk = _week_of(isolated_env, trades, date(2026, 7, 21))
        caps = [v for v in wk.discipline_violations if "超过单笔仓位上限" in v]
        assert len(caps) == 1 and "600519.SH" in caps[0]

    def test_utc_beijing_normalization_no_8h_error(self, isolated_env):
        """**时区命门**:激活 UTC 08:00 = **北京 16:00**(收盘之后)。同日(按收盘 15:00 兜底)
        那笔 3 万必须仍判 K1 违纪 —— 把 UTC 戳当北京时间裸比(08:00 < 15:00)会错判成
        v1.3、把违纪洗掉。"""
        _seed_charter_pair(isolated_env.db_path, "2026-07-22T08:00:00+00:00")
        trades = [_trade(date(2026, 7, 22), "600519.SH", "buy", 300.0, 100)]
        wk = _week_of(isolated_env, trades, date(2026, 7, 22))
        assert any("超过单笔仓位上限" in v for v in wk.discipline_violations)
        # 次日同样一笔 → 已在激活之后 → 不违纪(证明闸没把新章程一起堵死)
        trades2 = [_trade(date(2026, 7, 23), "600036.SH", "buy", 300.0, 100)]
        wk2 = _week_of(isolated_env, trades2, date(2026, 7, 23))
        assert not any("超过单笔仓位上限" in v for v in wk2.discipline_violations)

    def test_intraday_activation_uses_close_fallback(self, isolated_env):
        """③ 只有日期没有时刻 → 按该日收盘取 config。生产真实形态:激活 UTC 06:36
        (= 北京 14:36,盘中)→ 当日成交按收盘 15:00 判 = **新**章程。"""
        _seed_charter_pair(isolated_env.db_path, "2026-07-27T06:36:10+00:00")
        trades = [_trade(date(2026, 7, 27), "600519.SH", "buy", 300.0, 100)]
        wk = _week_of(isolated_env, trades, date(2026, 7, 27))
        assert not any("超过单笔仓位上限" in v for v in wk.discipline_violations)

    def test_explicit_trade_time_before_activation_still_old_charter(self, isolated_env):
        """交割单**带真实成交时刻**时以真时刻为准(不再吃收盘兜底):同日北京 10:00 成交、
        14:36 才激活 → 仍按旧章程 K1 判,违纪不被洗掉。"""
        _seed_charter_pair(isolated_env.db_path, "2026-07-27T06:36:10+00:00")
        t = _trade(date(2026, 7, 27), "600519.SH", "buy", 300.0, 100)
        t.trade_time = time(10, 0)
        wk = _week_of(isolated_env, [t], date(2026, 7, 27))
        assert any("超过单笔仓位上限" in v for v in wk.discipline_violations)

    def test_stop_discipline_anchored_at_sell_instant(self, isolated_env):
        """止损纪律锚**卖出时刻**(审计的是离场决策,与哨兵按当时现役 stop_pct 提醒同源):
        v1.3 把 stop_pct 抬到 0.10,买在旧章程下、卖在新章程下的 -6.7% 回合按**新**线判
        → 不再算破止损。"""
        _seed_charter_pair(isolated_env.db_path, "2026-07-22T10:00:00+00:00", v13_extra={"stop_pct": 0.10})
        trades = [
            _trade(date(2026, 7, 21), "600519.SH", "buy", 100.0, 100),   # 激活前买
            _trade(date(2026, 7, 23), "600519.SH", "sell", 93.3, 100),   # 激活后卖,-6.7%
        ]
        wk = _week_of(isolated_env, trades, date(2026, 7, 21))
        assert [kind for _, kind, _ in wk.stop_discipline] == [STOP_NOT_TRIGGERED]
        assert not any("止损" in v for v in wk.discipline_violations)

    def test_exposure_and_count_use_per_day_charter(self, isolated_env):
        """并发持仓数 / 敞口是**日粒度**判据:按「该日收盘时刻现役的章程」切段判。
        周二(K1:五仓/60%)持 6 只 ¥90,000 → 违纪;周四(v1.3:三仓/100%)只剩 2 只
        ¥30,000 → 不违纪。若整周一把抓用 K1 判就会多报、用 v1.3 判就会漏报周二那天。"""
        _seed_charter_pair(isolated_env.db_path, "2026-07-22T10:00:00+00:00")   # 周三北京 18:00
        codes = ["600519.SH", "600036.SH", "601398.SH", "600030.SH", "600000.SH", "600004.SH"]
        trades = []
        for c in codes:
            trades.append(_trade(date(2026, 7, 21), c, "buy", 150.0, 100))       # 各 ¥15,000
        for c in codes[:4]:
            trades.append(_trade(date(2026, 7, 22), c, "sell", 151.0, 100))      # 周三清掉 4 只
        wk = _week_of(isolated_env, trades, date(2026, 7, 21))
        cnt = [v for v in wk.discipline_violations if "并发持仓最多达" in v]
        expo = [v for v in wk.discipline_violations if "持仓总敞口最高达" in v]
        assert len(cnt) == 1 and "6 只" in cnt[0] and "最多持 5 只" in cnt[0]     # K1 段判出来的
        assert len(expo) == 1 and "60%" in expo[0]


class TestNoSwitchWeekBitwiseEquivalence:
    """② 回归护栏:**周内无章程切换时,逐笔判据与 ⑥-A 之前的按周判据逐位等价**。
    golden 取自改造前的实现(scratchpad 对拍脚本实测,四类违纪 + 顺序 + 文案全一致)。"""

    TRADES = [
        _trade(date(2026, 7, 20), "600519.SH", "buy", 300.0, 100),   # 3 万 > K1 2 万上限
        _trade(date(2026, 7, 20), "600036.SH", "buy", 150.0, 100),
        _trade(date(2026, 7, 20), "601398.SH", "buy", 150.0, 100),
        _trade(date(2026, 7, 21), "600030.SH", "buy", 150.0, 100),
        _trade(date(2026, 7, 21), "600000.SH", "buy", 150.0, 100),
        _trade(date(2026, 7, 21), "600004.SH", "buy", 150.0, 100),   # 第 6 只 → 超 max_positions=5
        _trade(date(2026, 7, 22), "600519.SH", "sell", 280.0, 100),  # -6.7% 破止损
        _trade(date(2026, 7, 23), "600036.SH", "sell", 153.0, 100),  # +2% 未触及容差带
    ]
    GOLDEN = [
        "600519.SH(示例票) 2026-07-20买入→2026-07-22卖出:卖出价相对买入价 -6.7%,"
        "跌破止损容差带下沿(-6%),疑似未按 -5% 止损离场(§1.3 第一死因、§2.1 第1条违纪)。",
        "600519.SH(示例票)于 2026-07-20 买入金额 ¥30,000,超过单笔仓位上限 ¥20,000(§2.1 第3条)。",
        "本周并发持仓最多达 6 只(约 2026-07-21),超过最多持 5 只的仓位纪律(§2.1 第3条)。",
        "本周持仓总敞口最高达 ¥105,000(约 2026-07-21,占总仓 87.5%),超过敞口上限 60%"
        "(¥72,000,§2.1 第3条)。",
    ]

    def test_violations_bitwise_identical_to_pre_v14_behavior(self, isolated_env):
        _seed_charter_pair(isolated_env.db_path, "2026-09-01T00:00:00+00:00")   # 激活远在本周之后
        wk = _week_of(isolated_env, self.TRADES, date(2026, 7, 20))
        assert wk.strategy_version == "K1"
        assert wk.discipline_violations == self.GOLDEN            # 内容 + 顺序逐位一致
        assert [(rt.ts_code, kind) for rt, kind, _ in wk.stop_discipline] == [
            ("600519.SH", STOP_BREACHED), ("600036.SH", STOP_NOT_TRIGGERED),
        ]
        assert wk.charter_switches == []                          # 无切换
        assert [(s.version, s.start, s.trade_count) for s in wk.charter_segments] == [
            ("K1", None, len(self.TRADES)),
        ]

    def test_post_activation_week_also_bitwise_stable(self, isolated_env):
        """对照方向:整周都在激活之后 → 全按新章程,4 万上限下同一笔 3 万不违纪、
        三仓制下 6 只并发才是违纪点(证明等价性不是"两边都没判")。"""
        _seed_charter_pair(isolated_env.db_path, "2026-07-13T12:00:00+00:00")
        wk = _week_of(isolated_env, self.TRADES, date(2026, 7, 20))
        assert wk.strategy_version == "v1.3"
        assert not any("超过单笔仓位上限" in v for v in wk.discipline_violations)
        assert any("超过最多持 3 只" in v for v in wk.discipline_violations)
        assert wk.charter_switches == []


class TestCharterSwitchReporting:
    """周报呈现:注明切换时刻 + 分段计数(plan §五-⑥-A「周报呈现」)。"""

    def _switch_week(self, isolated_env):
        _seed_charter_pair(isolated_env.db_path, "2026-07-29T10:00:00+00:00")   # 周三北京 18:00
        trades = [
            _trade(date(2026, 7, 27), "600519.SH", "buy", 300.0, 100),   # 切换前
            _trade(date(2026, 7, 28), "600036.SH", "buy", 300.0, 100),   # 切换前
            _trade(date(2026, 7, 30), "601398.SH", "buy", 300.0, 100),   # 切换后
        ]
        return _week_of(isolated_env, trades, date(2026, 7, 27))

    def test_segments_and_counts(self, isolated_env):
        wk = self._switch_week(isolated_env)
        assert [(s.version, s.trade_count) for s in wk.charter_segments] == [("K1", 2), ("v1.3", 1)]
        assert wk.charter_segments[0].start is None                 # 第一段自周初起
        assert wk.charter_segments[1].start.strftime("%Y-%m-%d %H:%M") == "2026-07-29 18:00"

    def test_switch_note_text(self, isolated_env):
        wk = self._switch_week(isolated_env)
        assert len(wk.charter_switches) == 1
        sw = wk.charter_switches[0]
        assert sw.from_version == "K1" and sw.to_version == "v1.3"
        assert sw.note == (
            "本周 2026-07-29 18:00 发生章程切换 K1→v1.3"
            "(切换前 2 笔按 K1 判、切换后 1 笔按 v1.3 判)。"
        )

    def test_violations_are_segmented(self, isolated_env):
        """两笔切换前的 3 万按 K1 报违纪、切换后那笔按 v1.3 不报 —— 分段计数与判定一致。"""
        wk = self._switch_week(isolated_env)
        caps = [v for v in wk.discipline_violations if "超过单笔仓位上限" in v]
        assert len(caps) == 2
        assert all("601398.SH" not in v for v in caps)

    def test_dict_and_material_expose_switch(self, isolated_env):
        import json

        wk = self._switch_week(isolated_env)
        d = weekly_review_dict(wk)
        json.dumps(d)   # JSON 安全(datetime 已格式化成串,不裸传对象)
        assert d["strategyVersion"] == "K1"                       # 周初标签口径不变
        assert [s["tradeCount"] for s in d["charterSegments"]] == [2, 1]
        assert d["charterSegments"][0]["start"] is None
        assert d["charterSwitches"][0]["at"] == "2026-07-29 18:00"
        assert d["charterSwitches"][0]["fromVersion"] == "K1"

        from neckline.review.material import build_material_text
        text = build_material_text(wk)
        assert "发生章程切换 K1→v1.3" in text and "切换前 2 笔" in text
        assert "按成交时刻逐笔取当时现役的章程" in text

    def test_no_switch_week_material_silent(self, isolated_env):
        """无切换的周**不出**这段文案(不制造每周都在的噪音)。"""
        from neckline.review.material import build_material_text

        _seed_charter_pair(isolated_env.db_path, "2026-09-01T00:00:00+00:00")
        trades = [_trade(date(2026, 7, 20), "600519.SH", "buy", 150.0, 100)]
        wk = _week_of(isolated_env, trades, date(2026, 7, 20))
        assert wk.charter_switches == []
        assert "章程切换" not in build_material_text(wk)

    def test_switch_after_all_trades_reported_with_zero_after(self, isolated_env):
        """周六激活(本周所有成交都在切换之前)→ 仍如实注明"本周发生过切换",
        切换后 0 笔。**不静默**:发生过就说,数是 0 就写 0。"""
        _seed_charter_pair(isolated_env.db_path, "2026-08-01T02:00:00+00:00")   # 周六北京 10:00
        trades = [_trade(date(2026, 7, 28), "600519.SH", "buy", 150.0, 100)]
        wk = _week_of(isolated_env, trades, date(2026, 7, 28))
        assert len(wk.charter_switches) == 1
        assert "切换后 0 笔" in wk.charter_switches[0].note
        assert [(s.version, s.trade_count) for s in wk.charter_segments] == [("K1", 1), ("v1.3", 0)]

    def test_reactivating_same_version_is_not_a_switch(self, isolated_env):
        """同版本再激活(回滚/重激活)不算切换 —— 阈值没变,报出来只是噪音。"""
        from neckline.db import connection
        from neckline.strategy import brain
        from .conftest import TEST_RULE_V1_CONFIG

        brain.save_version("K1", {"config": dict(TEST_RULE_V1_CONFIG)}, "k1",
                           activate=True, db_path=isolated_env.db_path)
        with connection(isolated_env.db_path) as conn:
            conn.execute("UPDATE strategy_versions SET activated_at=? WHERE version='K1'",
                         ("2026-07-29T10:00:00+00:00",))   # 唯一一版,激活戳落在本周内
        trades = [_trade(date(2026, 7, 27), "600519.SH", "buy", 150.0, 100)]
        wk = _week_of(isolated_env, trades, date(2026, 7, 27))
        assert wk.charter_switches == []
        assert [s.version for s in wk.charter_segments] == ["K1"]
