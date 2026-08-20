"""复盘材料生成单测(plan 4D.3,本次任务范围内只做确定性材料)。"""

from __future__ import annotations

from datetime import date

from neckline.review.material import build_material_text
from neckline.review.parse import RawTrade
from neckline.review.reconcile import run_weekly_review


def _trade(trade_date, ts_code, side, price, qty, name="示例票"):
    return RawTrade(trade_date=trade_date, ts_code=ts_code, name=name, side=side, price=price, qty=qty, fee=0.0, cash_flow=0.0)


def test_material_handles_no_closed_trades():
    from neckline.review.reconcile import WeeklyReview, compute_weekly_stats

    review = WeeklyReview(week="2026-W29", week_start=date(2026, 7, 13), week_end=date(2026, 7, 19))
    review.stats = compute_weekly_stats([], open_count=1)
    text = build_material_text(review)
    assert "没有已平仓的回合" in text
