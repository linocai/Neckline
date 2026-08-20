"""对账引擎单测:FIFO 闭合 / 周统计 / 强制复盘触发线边界。

🔴 **V2.5.0 S1**:原「三查各分支」(单笔上限 / 并发与敞口 / 禁买过滤 / 冷却 /
时间退出 / 止损纪律 / 计划台账核对 / 逐笔取章程 / 章程切换分段)共 14 组、约 1000 行
断言**已随 K8 章程判据整块删除**(PROJECT_PLAN §5.9)—— 被测函数本身已不存在,
⛔ 不是把测试 skip 掉。留下的是 K9 §六 仍要的那部分:FIFO 回合闭合与 `WeeklyStats`。

🔴 **V2.5.0 S11 收尾**:S1 那次删除**留下了空壳**——`TestStopDiscipline` /
`TestEntryScreens` / `TestCharterSwitchReporting` 三个类只剩 helper 没有用例,
外加 `_DueItem` / `_30k_trades` / `_week_of` 三个零引用 helper 与七块指向已退役概念的
段旗。它们**一条断言都不跑**却长得像在跑,下一个人会以为止损纪律还在被测。
本片一并清掉(⛔ 不是放宽:被测的东西早已不存在)。
"""

from __future__ import annotations

from datetime import date, time

import pytest

from neckline.review.parse import RawTrade
from neckline.review.reconcile import (
    FORCED_REVIEW_LOSS_FRAC,
    RoundTrip,
    build_round_trips,
    compute_weekly_stats,
    is_forced_review,
    iso_week_key,
    run_weekly_review,
    week_range,
    weekly_review_dict,
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
#  端到端:run_weekly_review()
# ======================================================================

class TestRunWeeklyReview:
    def test_empty_trades_returns_empty(self, isolated_env):
        reviews, warnings = run_weekly_review([], db_path=isolated_env.db_path)
        assert reviews == [] and warnings == []


class TestTradeInstant:
    """`trade_instant`:交割单只有日期时按**该日收盘时刻**(北京时间)——全包唯一时刻口径。"""

    def test_date_only_falls_back_to_market_close(self):
        from neckline.calendar import CN_TZ, MARKET_CLOSE_TIME
        from neckline.review.reconcile import trade_instant

        ts = trade_instant(date(2026, 7, 27))
        assert ts.time() == MARKET_CLOSE_TIME == time(15, 0)
        assert ts.utcoffset() == CN_TZ.utcoffset(None)      # 北京时间 aware,不是 naive

    def test_real_time_wins_over_fallback(self):
        from neckline.review.reconcile import trade_instant

        assert trade_instant(date(2026, 7, 27), time(10, 30)).time() == time(10, 30)

    def test_the_retired_charter_anchor_helper_is_gone(self):
        """🔴 S11:`day_close_instant()` 已随 K8 日粒度章程判据删除(它只是
        `trade_instant(d, None)` 的别名)。⛔ 不留没有调用方的别名 —— 它 docstring 里
        那句「归属哪版章程」会让下一个人以为这里还在判章程。"""
        from neckline.review import reconcile

        assert not hasattr(reconcile, "day_close_instant")
