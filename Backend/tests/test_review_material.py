"""复盘材料生成单测(plan 4D.3,本次任务范围内只做确定性材料)。"""

from __future__ import annotations

from datetime import date

from neckline.review.material import build_material_text
from neckline.review.parse import RawTrade
from neckline.review.reconcile import run_weekly_review

from .conftest import seed_active_rule_v1


def _trade(trade_date, ts_code, side, price, qty, name="示例票"):
    return RawTrade(trade_date=trade_date, ts_code=ts_code, name=name, side=side, price=price, qty=qty, fee=0.0, cash_flow=0.0)


def test_material_mentions_forced_review_when_triggered(isolated_env):
    seed_active_rule_v1(isolated_env)
    trades = [
        _trade(date(2026, 7, 14), "600519.SH", "buy", 100.0, 100),
        _trade(date(2026, 7, 16), "600519.SH", "sell", 70.0, 100),
    ]
    reviews, _ = run_weekly_review(
        trades, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir, total_capital=120000.0,
    )
    text = build_material_text(reviews[0])
    assert "强制复盘" in text
    assert reviews[0].week in text


def test_material_no_violations_reads_clean(isolated_env):
    seed_active_rule_v1(isolated_env)
    trades = [
        _trade(date(2026, 7, 14), "600519.SH", "buy", 100.0, 100),
        _trade(date(2026, 7, 16), "600519.SH", "sell", 105.0, 100),
    ]
    reviews, _ = run_weekly_review(trades, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    text = build_material_text(reviews[0])
    assert "未发现仓位纪律" in text
    assert "Infinity" not in text


def test_material_handles_no_closed_trades():
    from neckline.review.reconcile import WeeklyReview, compute_weekly_stats

    review = WeeklyReview(week="2026-W29", week_start=date(2026, 7, 13), week_end=date(2026, 7, 19))
    review.stats = compute_weekly_stats([], open_count=1)
    text = build_material_text(review)
    assert "没有已平仓的回合" in text
