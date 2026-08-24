"""P1/P3 共用的逐日热度与持续热门身份。

正式热度不是 60 日活跃度：每个交易日分别对成交额和换手率取横截面分位，再按
批准包权重合成。任一分量缺失的票当天没有热度读数，不伪造为 0。
"""

from __future__ import annotations

from typing import Dict

import polars as pl

from neckline.k9 import ranks
from neckline.k9.contract import PackRange
from neckline.k9.params import DailyHeatParams, HotIdentityParams, P3Tier


def daily_heat_scores(
    pack: PackRange, *, daily_heat: DailyHeatParams, days: int,
) -> pl.DataFrame:
    sessions = sorted(pack.frame["trade_date"].unique().to_list())[-days:]
    rows = []
    for day in sessions:
        frame = pack.frame.filter(pl.col("trade_date") == day)
        amount: Dict[str, float] = {
            str(row["ts_code"]): float(row["amount"])
            for row in frame.select("ts_code", "amount").iter_rows(named=True)
            if row["amount"] is not None
        }
        turnover: Dict[str, float] = {
            str(row["ts_code"]): float(row["turnover_rate"])
            for row in frame.select("ts_code", "turnover_rate").iter_rows(named=True)
            if row["turnover_rate"] is not None
        }
        amount_rank, turnover_rank = ranks.pct_rank(amount), ranks.pct_rank(turnover)
        codes = sorted(set(amount_rank) & set(turnover_rank))
        if not codes:
            continue
        rows.append(pl.DataFrame({
            "trade_date": [day] * len(codes),
            "ts_code": codes,
            "hot_score": [
                daily_heat.amount_weight * amount_rank[code]
                + daily_heat.turnover_weight * turnover_rank[code]
                for code in codes
            ],
        }, schema={"trade_date": pl.Date, "ts_code": pl.String, "hot_score": pl.Float64}))
    if not rows:
        return pl.DataFrame(schema={
            "trade_date": pl.Date, "ts_code": pl.String, "hot_score": pl.Float64})
    return pl.concat(rows, how="vertical").sort(["trade_date", "ts_code"])


def persistent_hot(
    pack: PackRange, *, daily_heat: DailyHeatParams, tier: P3Tier,
) -> pl.DataFrame:
    scores = daily_heat_scores(pack, daily_heat=daily_heat, days=tier.hot_lookback_days)
    schema = {"ts_code": pl.String, "hot_days": pl.Int64,
              "hot_persistence": pl.Float64}
    if scores.is_empty():
        return pl.DataFrame(schema=schema)
    threshold = 1.0 - tier.daily_heat_top_pct
    return (
        scores.group_by("ts_code")
        .agg((pl.col("hot_score") >= threshold).sum().alias("hot_days"))
        .filter(pl.col("hot_days") >= tier.min_hot_days)
        .with_columns((pl.col("hot_days") / tier.hot_lookback_days).alias("hot_persistence"))
        .select(list(schema))
    )


def persistent_identity(
    pack: PackRange, *, daily_heat: DailyHeatParams, identity: HotIdentityParams,
) -> pl.DataFrame:
    scores = daily_heat_scores(pack, daily_heat=daily_heat, days=identity.lookback_days)
    schema = {"ts_code": pl.String, "hot_days": pl.Int64, "recent_hot_days": pl.Int64}
    if scores.is_empty():
        return pl.DataFrame(schema=schema)
    sessions = sorted(scores["trade_date"].unique().to_list())
    recent = sessions[-identity.recent_window_days:]
    threshold = 1.0 - identity.daily_heat_top_pct
    return (
        scores.group_by("ts_code")
        .agg(
            (pl.col("hot_score") >= threshold).sum().alias("hot_days"),
            ((pl.col("hot_score") >= threshold)
             & pl.col("trade_date").is_in(recent)).sum().alias("recent_hot_days"),
        )
        .filter(
            (pl.col("hot_days") >= identity.min_hot_days)
            & (pl.col("recent_hot_days") >= identity.min_recent_hot_days)
        )
        .select(list(schema))
    )


__all__ = ["daily_heat_scores", "persistent_hot", "persistent_identity"]
