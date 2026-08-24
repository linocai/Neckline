"""K9-v2 有效活跃度：典型成交额 + 参与密度，横截面分位加权。"""

from __future__ import annotations

from datetime import date

import polars as pl

from neckline.k9 import ranks
from neckline.k9.contract import PackRange
from neckline.k9.params import BoundaryParams

AMOUNT_MEDIAN = "_activity_amount_median"
PARTICIPATION_MEDIAN = "_activity_participation_median"
AMOUNT_PERCENTILE = "_activity_amount_percentile"
PARTICIPATION_PERCENTILE = "_activity_participation_percentile"
SCORE = "_activity_score"


def _window(frame: pl.DataFrame, target: date, days: int) -> pl.DataFrame:
    pool = frame.filter(pl.col("trade_date") <= target)
    if pool.is_empty():
        return pool
    wanted = sorted(pool["trade_date"].unique().to_list())[-days:]
    return pool.filter(pl.col("trade_date").is_in(wanted))


def compute(pack: PackRange, *, target: date, params: BoundaryParams) -> pl.DataFrame:
    """返回每只票在 ``target`` 的有效活跃度；窗口不满则读数为空。"""
    amount_win = _window(pack.frame, target, params.activity_amount_window_days)
    part_win = _window(pack.frame, target, params.activity_participation_window_days)
    schema = {
        "ts_code": pl.String, AMOUNT_MEDIAN: pl.Float64,
        PARTICIPATION_MEDIAN: pl.Float64, AMOUNT_PERCENTILE: pl.Float64,
        PARTICIPATION_PERCENTILE: pl.Float64, SCORE: pl.Float64,
    }
    if amount_win.is_empty() or part_win.is_empty():
        return pl.DataFrame(schema=schema)

    amount = (
        amount_win.select(["ts_code", "amount"])
        .filter(pl.col("amount").is_not_null())
        .group_by("ts_code")
        .agg(pl.col("amount").median().alias(AMOUNT_MEDIAN), pl.len().alias("_n"))
        .filter(pl.col("_n") >= params.activity_minimum_valid_days)
        .select(["ts_code", AMOUNT_MEDIAN])
    )
    # 正式包明确指定 turnover_rate_median；不允许施工侧回退到成交额/流通市值代理。
    part_expr = pl.col("turnover_rate")
    participation = (
        part_win.select(["ts_code", part_expr.alias("_participation")])
        .filter(pl.col("_participation").is_not_null())
        .group_by("ts_code")
        .agg(pl.col("_participation").median().alias(PARTICIPATION_MEDIAN),
             pl.len().alias("_n"))
        .filter(pl.col("_n") >= params.activity_minimum_valid_days)
        .select(["ts_code", PARTICIPATION_MEDIAN])
    )
    merged = amount.join(participation, on="ts_code", how="inner")
    if merged.is_empty():
        return pl.DataFrame(schema=schema)
    amount_rank = ranks.pct_rank(dict(merged.select(["ts_code", AMOUNT_MEDIAN]).iter_rows()))
    part_rank = ranks.pct_rank(
        dict(merged.select(["ts_code", PARTICIPATION_MEDIAN]).iter_rows()))
    return merged.with_columns(
        pl.col("ts_code").replace_strict(amount_rank, default=None).alias(AMOUNT_PERCENTILE),
        pl.col("ts_code").replace_strict(part_rank, default=None).alias(PARTICIPATION_PERCENTILE),
    ).with_columns(
        (params.activity_amount_weight * pl.col(AMOUNT_PERCENTILE)
         + params.activity_participation_weight * pl.col(PARTICIPATION_PERCENTILE)).alias(SCORE)
    ).select(list(schema))


__all__ = [
    "AMOUNT_MEDIAN", "PARTICIPATION_MEDIAN", "AMOUNT_PERCENTILE",
    "PARTICIPATION_PERCENTILE", "SCORE", "compute",
]
