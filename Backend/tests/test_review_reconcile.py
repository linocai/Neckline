"""对账引擎单测:FIFO 闭合 / 周统计 / 强制复盘触发线边界。

🔴 **V2.5.0 S1**:原「三查各分支」(单笔上限 / 并发与敞口 / 禁买过滤 / 冷却 /
时间退出 / 止损纪律 / 计划台账核对 / 逐笔取章程 / 章程切换分段)共 14 组、约 1000 行
断言**已随 K8 章程判据整块删除**(PROJECT_PLAN §5.9)—— 被测函数本身已不存在,
⛔ 不是把测试 skip 掉。留下的是 K9 §六 仍要的那部分:FIFO 回合闭合与 `WeeklyStats`。
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


# ======================================================================
#  止损纪律(对账三查②)
# ======================================================================

class TestStopDiscipline:
    def _rt(self, buy=100.0, sell=94.0):
        return RoundTrip(ts_code="600519.SH", name="贵州茅台", buy_date=date(2026, 7, 14),
                          buy_price=buy, qty=100, fees=0.0, sell_date=date(2026, 7, 16),
                          sell_price=sell, closed=True)


# ======================================================================
#  章程执行(对账三查③)
# ======================================================================


class TestEntryScreens:
    @pytest.fixture
    def market(self, isolated_env):
        dates = seed_synthetic_market(isolated_env)
        return isolated_env, dates


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


# ======================================================================
#  端到端:run_weekly_review()
# ======================================================================

class TestRunWeeklyReview:


    def test_empty_trades_returns_empty(self, isolated_env):
        reviews, warnings = run_weekly_review([], db_path=isolated_env.db_path)
        assert reviews == [] and warnings == []


# ======================================================================
#  v1.2-A:历史洗白修复(按周取「当时现役」config)—— 命门反例 + 时间线双向
# ======================================================================


# ======================================================================
#  审计 🟡-3(2026-07-27):激活时点不得洗白「刚结束的那一周」
#  判据改「激活日 < week_start」(方案 (a),不依赖人的操作纪律)。
#  ⚠ 时间锚:2026-07-20 是周一,2026-07-26 是周日 → 07-20~07-26 是一个完整 ISO 周;
#     下一周是 07-27(周一)~08-02(周日)。
# ======================================================================


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


# ======================================================================
#  v1.4-⑥-A(§七 P1-4):周复盘章程**按成交时刻逐笔判** 🔴 碰纪律判定
#  ⚠ 时间锚:2026-07-20 周一、07-26 周日;下一周 07-27(周一)~08-02(周日)。
#     `activated_at` 落库是 **UTC** 戳,成交时刻是**北京时间**(UTC+8)。
# ======================================================================


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


# ======================================================================
#  v1.4 review 🟡-1(2026-07-29):**回滚 / 重激活不得改写历史周的判定**
#  —— 时间轴事实源改 append-only 事件流(`strategy_activation_log`)后的端到端护栏。
#     `reviews` 是幂等覆盖表,重传交割单即重算 —— 若回滚能改判,整段历史会**静默**翻案。
# ======================================================================
