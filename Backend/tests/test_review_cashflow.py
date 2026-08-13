"""⑫-A 资金流水四分类聚合单测(`neckline/review/cashflow.py`)。

覆盖:转入转出/分红/税费/其他四类按周分桶互不混同(在造数上分得开);转入转出
拆分正确;`trading_pnl` 直接取 `WeeklyReview.stats.realized_pnl`(不重算)且该周
无复盘数据时如实 `None`(不是 0);跨周分桶正确;`CashFlowSummary` 不提供任何
"四类合计"字段(蓝图 5.3「账户金额增加不得直接视为策略收益」的结构性落地)。
"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.review.cashflow import (
    CashFlowSummary,
    build_all_weekly_summaries,
    build_weekly_cash_flow_summary,
    bucket_events_by_week,
)
from neckline.review.parse import (
    CASH_FLOW_DIVIDEND,
    CASH_FLOW_OTHER,
    CASH_FLOW_TAX,
    CASH_FLOW_TRANSFER,
    CashFlowEvent,
)
from neckline.review.reconcile import WeeklyReview, WeeklyStats


def _event(d: date, business: str, kind: str, amount: float, **kw) -> CashFlowEvent:
    return CashFlowEvent(trade_date=d, business_name=business, kind=kind, amount=amount, **kw)


def _stats(realized_pnl: float) -> WeeklyStats:
    return WeeklyStats(
        closed_count=1, open_count=0, win_rate=1.0, profit_factor=float("inf"),
        profit_loss_ratio=float("inf"), total_fees=10.0, gross_pnl=realized_pnl + 10.0,
        realized_pnl=realized_pnl, realized_loss=0.0 if realized_pnl >= 0 else realized_pnl,
    )


def _review(week: str, realized_pnl: float) -> WeeklyReview:
    r = WeeklyReview(week=week, week_start=date(2026, 7, 13), week_end=date(2026, 7, 19))
    r.stats = _stats(realized_pnl)
    return r


class TestBucketing:
    def test_events_bucketed_by_iso_week(self):
        events = [
            _event(date(2026, 7, 14), "银证转入", CASH_FLOW_TRANSFER, 50000.0),   # 2026-W29
            _event(date(2026, 7, 21), "银证转出", CASH_FLOW_TRANSFER, -1000.0),   # 2026-W30
        ]
        buckets = bucket_events_by_week(events)
        assert set(buckets) == {"2026-W29", "2026-W30"}
        assert len(buckets["2026-W29"]) == 1 and len(buckets["2026-W30"]) == 1


class TestFourWayClassification:
    """验收条款「资金四分类在造数上分得开」的核心断言。"""

    def _mixed_week_events(self):
        d = date(2026, 7, 14)
        return [
            _event(d, "银证转入", CASH_FLOW_TRANSFER, 50000.0),
            _event(d, "银证转出", CASH_FLOW_TRANSFER, -20000.0),
            _event(d, "红股派息", CASH_FLOW_DIVIDEND, 200.0),
            _event(d, "股息红利个人所得税扣款", CASH_FLOW_TAX, -40.0),
            _event(d, "利息归本", CASH_FLOW_OTHER, 0.5),
        ]

    def test_four_categories_are_independently_summed(self):
        events = self._mixed_week_events()
        summary = build_weekly_cash_flow_summary("2026-W29", events, review=None)
        assert summary.transfer_in == pytest.approx(50000.0)
        assert summary.transfer_out == pytest.approx(20000.0)   # 非负数(绝对值)
        assert summary.transfer_net == pytest.approx(30000.0)
        assert summary.dividend == pytest.approx(200.0)
        assert summary.tax == pytest.approx(-40.0)
        assert summary.other == pytest.approx(0.5)
        assert summary.other_event_count == 1
        assert summary.event_count == 5
        # 四类互不相等、互不混同(不是同一个数字换个名字)
        values = {summary.transfer_net, summary.dividend, summary.tax, summary.other}
        assert len(values) == 4

    def test_no_combined_total_field_exists(self):
        """蓝图 5.3「账户金额增加不得直接视为策略收益」的结构性落地:
        `CashFlowSummary` 不提供任何"四类合计"字段。"""
        summary = build_weekly_cash_flow_summary("2026-W29", self._mixed_week_events(), review=None)
        field_names = set(summary.__dataclass_fields__)
        assert not any("total" in f.lower() for f in field_names)
        assert hasattr(summary, "transfer_net")   # 转入转出净额是合法的一类内合并,不是跨类合计

    def test_account_balance_increase_is_not_trading_pnl(self):
        """账户金额增加(转入 + 分红)不得被读成交易盈亏——两者是完全独立的字段,
        即使转入转出/分红金额很大,`trading_pnl` 也只反映真实平仓回合的净盈亏。"""
        events = [
            _event(date(2026, 7, 14), "银证转入", CASH_FLOW_TRANSFER, 100000.0),
            _event(date(2026, 7, 14), "红股派息", CASH_FLOW_DIVIDEND, 5000.0),
        ]
        review = _review("2026-W29", realized_pnl=-300.0)   # 真实交易是亏的
        summary = build_weekly_cash_flow_summary("2026-W29", events, review=review)
        assert summary.transfer_in == pytest.approx(100000.0)
        assert summary.dividend == pytest.approx(5000.0)
        assert summary.trading_pnl == pytest.approx(-300.0)   # 与转入/分红金额完全无关


class TestTradingPnlHonesty:
    def test_trading_pnl_comes_from_reconcile_stats_verbatim(self):
        review = _review("2026-W29", realized_pnl=1234.56)
        summary = build_weekly_cash_flow_summary("2026-W29", [], review=review)
        assert summary.trading_pnl == pytest.approx(1234.56)

    def test_missing_review_is_none_not_zero(self):
        """没有复盘数据(该周没有已平仓回合)→ `trading_pnl=None`,不是 0
        ——「没有」与「算出来是 0」必须分开(项目铁律)。"""
        summary = build_weekly_cash_flow_summary("2026-W29", [], review=None)
        assert summary.trading_pnl is None

    def test_review_without_stats_is_also_none(self):
        r = WeeklyReview(week="2026-W29", week_start=date(2026, 7, 13), week_end=date(2026, 7, 19))
        assert r.stats is None
        summary = build_weekly_cash_flow_summary("2026-W29", [], review=r)
        assert summary.trading_pnl is None


class TestBuildAllWeeklySummaries:
    def test_each_week_gets_its_own_summary_sorted(self):
        events = [
            _event(date(2026, 7, 21), "银证转出", CASH_FLOW_TRANSFER, -500.0),   # W30
            _event(date(2026, 7, 14), "银证转入", CASH_FLOW_TRANSFER, 1000.0),   # W29
        ]
        summaries = build_all_weekly_summaries(events, reviews_by_week={
            "2026-W29": _review("2026-W29", realized_pnl=10.0),
        })
        assert [s.week for s in summaries] == ["2026-W29", "2026-W30"]   # 升序
        by_week = {s.week: s for s in summaries}
        assert by_week["2026-W29"].trading_pnl == pytest.approx(10.0)
        assert by_week["2026-W30"].trading_pnl is None   # 该周没传复盘数据,如实 None

    def test_empty_events_yields_no_summaries(self):
        assert build_all_weekly_summaries([]) == []


class TestToDict:
    def test_to_dict_is_camel_case_and_carries_note(self):
        summary = build_weekly_cash_flow_summary(
            "2026-W29",
            [_event(date(2026, 7, 14), "银证转入", CASH_FLOW_TRANSFER, 1000.0)],
            review=None,
        )
        d = summary.to_dict()
        assert d["transferIn"] == pytest.approx(1000.0)
        assert d["tradingPnl"] is None
        assert "策略收益" in d["note"]
