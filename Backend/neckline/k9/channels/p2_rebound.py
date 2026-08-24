"""形态 P2：多日个股超跌，同时显著跑输行业并出现释放/承接信号。"""

from __future__ import annotations

from typing import Dict, List

import polars as pl

from neckline.k9 import volume
from neckline.k9.contract import ChannelHit, PackRange, Pattern, Tier
from neckline.k9.params import K9Params, P2Tier

PATTERN = Pattern.P2
STRENGTH_KEYS = (
    "absoluteOversoldDepth", "industryUnderperformance", "lowRecovery",
    "declineDeceleration", "effectiveTurnover",
)


def one_line_limit_down() -> pl.Expr:
    ld = (pl.col("limit_down_price") * 100).round().cast(pl.Int64)
    same = lambda name: (pl.col(name) * 100).round().cast(pl.Int64) == ld
    return (pl.col("is_limit_down").fill_null(False)
            & same("open") & same("high") & same("low") & same("close")).fill_null(False)


def _window_metrics(pack: PackRange, days: int) -> pl.DataFrame:
    win = pack.history(days=days, include_today=True)
    schema = {"ts_code": pl.String, "_cum_return_pp": pl.Float64,
              "_drawdown_pp": pl.Float64, "_industry_cum_pp": pl.Float64,
              "_underperformance_pp": pl.Float64}
    if win.is_empty():
        return pl.DataFrame(schema=schema)
    agg = win.sort("trade_date").group_by("ts_code").agg(
        pl.col("ret_1d").add(1.0).product().alias("_stock_growth"),
        pl.col("sw_l2_median_ret").add(1.0).product().alias("_industry_growth"),
        pl.col("high").max().alias("_peak"),
        pl.col("close").last().alias("_last_close"),
        pl.len().alias("_n"),
        pl.col("ret_1d").count().alias("_stock_n"),
        pl.col("sw_l2_median_ret").count().alias("_industry_n"),
    ).filter(
        (pl.col("_n") >= days) & (pl.col("_stock_n") >= days)
        & (pl.col("_industry_n") >= days)
    ).with_columns(
        ((pl.col("_stock_growth") - 1.0) * 100).alias("_cum_return_pp"),
        ((1.0 - pl.col("_last_close") / pl.col("_peak")) * 100).alias("_drawdown_pp"),
        ((pl.col("_industry_growth") - 1.0) * 100).alias("_industry_cum_pp"),
    ).with_columns(
        (pl.col("_industry_cum_pp") - pl.col("_cum_return_pp")).alias("_underperformance_pp")
    )
    return agg.select(list(schema))


def _previous_return(pack: PackRange) -> pl.DataFrame:
    hist = pack.history(days=1, include_today=False)
    if hist.is_empty():
        return pl.DataFrame(schema={"ts_code": pl.String, "_prev_ret_pp": pl.Float64})
    return hist.select("ts_code", (pl.col("ret_1d") * 100).alias("_prev_ret_pp"))


def _frame(pack: PackRange, params: K9Params, cfg: P2Tier) -> pl.DataFrame:
    return (
        pack.today.join(_window_metrics(pack, cfg.window_days),
                        on="ts_code", how="left")
        .join(_previous_return(pack), on="ts_code", how="left")
        .join(volume.compute(pack, ma_days=params.volume.ma_days), on="ts_code", how="left")
        .with_columns(
            one_line_limit_down().alias("_one_line"),
            pl.when((pl.col("high") - pl.col("low")) > 0)
            .then((pl.col("close") - pl.col("low")) / (pl.col("high") - pl.col("low")))
            .otherwise(None).alias("_low_recovery"),
            (pl.col("ret_1d") * 100 - pl.col("_prev_ret_pp")).alias("_deceleration_pp"),
        )
    )


def _oversold_expr(cfg: P2Tier) -> pl.Expr:
    cumulative = pl.col("_cum_return_pp") <= -cfg.min_cumulative_drop_pct
    drawdown = pl.col("_drawdown_pp") >= cfg.min_drawdown_from_window_high_pct
    return cumulative | drawdown


def _signals(row: dict, cfg: P2Tier) -> Dict[str, bool]:
    return {
        "closeLocationSupport": row["_low_recovery"] is not None
                                and row["_low_recovery"] >= cfg.min_close_location,
        "dailyReturnSupport": row["ret_1d"] is not None
                              and row["ret_1d"] * 100 >= cfg.min_daily_ret_pct,
    }


def run(pack: PackRange, params: K9Params) -> List[ChannelHit]:
    if pack.today.is_empty():
        return []
    picked: Dict[str, Tier] = {}
    evidence: Dict[str, Dict[str, bool]] = {}
    rows: Dict[str, dict] = {}
    for tier, cfg in ((Tier.STRICT, params.channels.p2.strict),
                      (Tier.RELAXED, params.channels.p2.relaxed)):
        frame = _frame(pack, params, cfg).filter(
            _oversold_expr(cfg)
            & (pl.col("_underperformance_pp") >= cfg.min_industry_underperformance_pct)
            & ~pl.col("_one_line")
            & (pl.col(volume.COLUMN) >= cfg.min_vol_multiple)
        )
        for row in frame.iter_rows(named=True):
            sig = _signals(row, cfg)
            if not any(sig.values()):
                continue
            code = str(row["ts_code"])
            picked.setdefault(code, tier)
            evidence.setdefault(code, sig)
            rows.setdefault(code, row)
    return [ChannelHit(
        ts_code=code, pattern=PATTERN, tier=picked[code], evidence=evidence[code],
        strength={
            "absoluteOversoldDepth": max(-float(rows[code]["_cum_return_pp"]),
                                         float(rows[code]["_drawdown_pp"])),
            "industryUnderperformance": rows[code]["_underperformance_pp"],
            "lowRecovery": rows[code]["_low_recovery"],
            "declineDeceleration": rows[code]["_deceleration_pp"],
            "effectiveTurnover": rows[code][volume.COLUMN],
        },
    ) for code in sorted(picked)]


__all__ = ["PATTERN", "STRENGTH_KEYS", "one_line_limit_down", "run"]
