"""形态 P1：有效活跃池中的首次放量启动，持续热门身份由 P3 占有。"""

from __future__ import annotations

from typing import Dict, List

import polars as pl

from neckline.k9 import activity, hotness, upside_room, volume
from neckline.k9.contract import ChannelHit, PackRange, Pattern, Tier, to_percent_points
from neckline.k9.params import K9Params, P1Tier

PATTERN = Pattern.P1
STRENGTH_KEYS = (
    "volMultiple", "amountRank", "closeLocation", "breakout",
    "industryRelativeStrength", "upsideRoom",
)


def _breakout(pack: PackRange, days: int) -> pl.DataFrame:
    hist = pack.history(days=days, include_today=False)
    if hist.is_empty():
        return pl.DataFrame(schema={"ts_code": pl.String, "_breakout_pp": pl.Float64})
    prior = hist.group_by("ts_code").agg(
        pl.col("high").max().alias("_prior_high"), pl.len().alias("_n")
    ).filter(pl.col("_n") >= days)
    return pack.today.select(["ts_code", "close"]).join(
        prior, on="ts_code", how="left"
    ).with_columns(
        pl.when(pl.col("_prior_high") > 0)
        .then(to_percent_points(pl.col("close") / pl.col("_prior_high") - 1.0))
        .otherwise(None).alias("_breakout_pp")
    ).select(["ts_code", "_breakout_pp"])


def _frame(pack: PackRange, params: K9Params, cfg: P1Tier) -> pl.DataFrame:
    act = activity.compute(pack, target=pack.as_of, params=params.boundary)
    return (
        pack.today.join(act, on="ts_code", how="left")
        .join(volume.compute(pack, ma_days=params.volume.ma_days), on="ts_code", how="left")
        .join(upside_room.compute(pack, days=cfg.breakout_window_days),
              on="ts_code", how="left")
        .join(_breakout(pack, cfg.breakout_window_days), on="ts_code", how="left")
        .with_columns(
            pl.when((pl.col("high") - pl.col("low")) > 0)
            .then((pl.col("close") - pl.col("low")) / (pl.col("high") - pl.col("low")))
            .otherwise(None).alias("_close_location")
        )
    )


def _pick(frame: pl.DataFrame, cfg: P1Tier, hot_codes: set[str]) -> List[str]:
    return frame.filter(
        ~pl.col("ts_code").is_in(sorted(hot_codes))
        & pl.col(volume.COLUMN).is_not_null()
        & (pl.col(volume.COLUMN) >= cfg.min_vol_multiple)
        & pl.col("ret_1d").is_not_null() & (pl.col("ret_1d") * 100 >= cfg.min_ret_pct)
        & pl.col("rel_strength_1d").is_not_null()
        & (pl.col("rel_strength_1d") * 100 >= cfg.min_industry_excess_pct)
        & (
            (pl.col("_close_location").is_not_null()
             & (pl.col("_close_location") >= cfg.min_close_location))
            | (pl.col("_breakout_pp").is_not_null()
               & (pl.col("_breakout_pp") >= -cfg.max_distance_below_prior_high_pct))
        )
    )["ts_code"].to_list()


def run(pack: PackRange, params: K9Params) -> List[ChannelHit]:
    if pack.today.is_empty():
        return []
    picked: Dict[str, Tier] = {}
    rows: Dict[str, dict] = {}
    # P1 自己的批准阈值定义“已有热门身份”；不借用 P3 的更窄阈值。
    hot = set()
    for p1_cfg in (params.channels.p1.strict, params.channels.p1.relaxed):
        hot.update(hotness.persistent_identity(
            pack, daily_heat=params.channels.p3.daily_heat,
            identity=p1_cfg.hot_identity_exclusion,
        )["ts_code"].to_list())
    configs = ((Tier.STRICT, params.channels.p1.strict),
               (Tier.RELAXED, params.channels.p1.relaxed))
    for tier, cfg in configs:
        frame = _frame(pack, params, cfg)
        for row in frame.iter_rows(named=True):
            rows.setdefault(str(row["ts_code"]), row)
        for code in _pick(frame, cfg, hot):
            picked.setdefault(code, tier)
    out = []
    for code in sorted(picked):
        row = rows[code]
        out.append(ChannelHit(
            ts_code=code, pattern=PATTERN, tier=picked[code],
            strength={
                "volMultiple": row[volume.COLUMN],
                "amountRank": row[activity.AMOUNT_PERCENTILE],
                "closeLocation": row["_close_location"],
                "breakout": row["_breakout_pp"],
                "industryRelativeStrength": row["rel_strength_1d"],
                "upsideRoom": upside_room.score_room_far(row[upside_room.PCT_COLUMN]),
            },
        ))
    return out


__all__ = ["PATTERN", "STRENGTH_KEYS", "run"]
