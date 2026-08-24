"""P3：持续热门、近五日强博弈、D0 仍热门；可选证据只作封顶附加分。"""

from __future__ import annotations

from typing import Dict, List, Optional

import polars as pl

from neckline.facts.pack import TOP_LIST_AVAILABLE
from neckline.k9 import hotness
from neckline.k9.contract import ChannelHit, PackRange, Pattern, Tier
from neckline.k9.params import K9Params, P3Bonuses, P3Tier

PATTERN = Pattern.P3
STRENGTH_KEYS = (
    "hotPersistence", "maxAmplitude", "maxAbsoluteMove", "hugeTurnover",
    "conflictRecency", "directionAndIndustryRelativeStrength",
)


def _with_mechanics(pack: PackRange, ma_days: int) -> pl.DataFrame:
    return (
        pack.frame.sort(["ts_code", "trade_date"])
        .with_columns(
            pl.col("vol").shift(1).rolling_mean(window_size=ma_days).over("ts_code")
            .alias("_prior_vol_ma")
        )
        .with_columns(
            pl.when(pl.col("_prior_vol_ma") > 0)
            .then(pl.col("vol") / pl.col("_prior_vol_ma"))
            .otherwise(None).alias("_vol_multiple"),
            pl.when((pl.col("pre_close") > 0) & (pl.col("limit_up_price") > 0))
            .then(((pl.col("limit_up_price") / pl.col("pre_close") - 1.0).abs() * 100))
            .otherwise(None).alias("_limit_width_pp"),
        )
        .with_columns(
            pl.when(pl.col("_limit_width_pp") > 0)
            .then((pl.col("ret_1d").abs() * 100) / pl.col("_limit_width_pp"))
            .otherwise(None).alias("_move_limit_ratio"),
            pl.when(pl.col("_limit_width_pp") > 0)
            .then((pl.col("amp_1d") * 100) / pl.col("_limit_width_pp"))
            .otherwise(None).alias("_amplitude_limit_ratio"),
        )
    )


def _contest(pack: PackRange, cfg: P3Tier, ma_days: int) -> pl.DataFrame:
    full = _with_mechanics(pack, ma_days)
    days = sorted(full["trade_date"].unique().to_list())[-cfg.conflict_lookback_days:]
    win = full.filter(pl.col("trade_date").is_in(days))
    if win.is_empty():
        return pl.DataFrame(schema={"ts_code": pl.String})
    day_index = {day: index for index, day in enumerate(days)}
    win = win.with_columns(
        pl.col("trade_date").replace_strict(day_index).alias("_contest_day_index"),
        ((pl.col("_move_limit_ratio") >= cfg.min_absolute_move_as_limit_ratio)
         | (pl.col("_amplitude_limit_ratio") >= cfg.min_amplitude_as_limit_ratio)
         | (pl.col("_vol_multiple") >= cfg.min_huge_vol_multiple)).alias("_contest_event"),
    )
    return (
        win.group_by("ts_code").agg(
            pl.col("_move_limit_ratio").max().alias("_max_move_ratio"),
            pl.col("_amplitude_limit_ratio").max().alias("_max_amplitude_ratio"),
            pl.col("_vol_multiple").max().alias("_max_vol_multiple"),
            pl.col("is_limit_up").fill_null(False).any().alias("_limit_up"),
            pl.col("is_limit_down").fill_null(False).any().alias("_limit_down"),
            pl.when(pl.col("top_list_state") == TOP_LIST_AVAILABLE)
            .then(pl.col("top_list_hit").cast(pl.Int8)).otherwise(None)
            .max().alias("_top_list"),
            pl.when(pl.col("_contest_event")).then(pl.col("_contest_day_index"))
            .otherwise(None).max().alias("_latest_contest_index"),
        )
        .with_columns(
            ((pl.col("_latest_contest_index") + 1) / len(days)).alias("_freshness")
        )
    )


def _evidence(row: dict) -> Dict[str, Optional[bool]]:
    return {
        "limitUp": bool(row.get("_limit_up")),
        "limitDown": bool(row.get("_limit_down")),
        "topList": None if row.get("_top_list") is None else bool(row["_top_list"]),
        # 正式事实包尚无可审计的控异动/反包结构字段；按批准包明确记 unavailable。
        "controlPause": None,
        "reversalSecondWave": None,
    }


def _bonus(evidence: Dict[str, Optional[bool]], params: P3Bonuses) -> float:
    value = 0.0
    value += params.recent_limit_up if evidence["limitUp"] else 0.0
    value += params.recent_limit_down_heat if evidence["limitDown"] else 0.0
    value += params.dragon_tiger_list if evidence["topList"] else 0.0
    value += params.controlled_anomaly if evidence["controlPause"] else 0.0
    value += params.reversal_or_second_wave if evidence["reversalSecondWave"] else 0.0
    return min(value, params.bonus_cap)


def _frame(pack: PackRange, params: K9Params, cfg: P3Tier) -> pl.DataFrame:
    persistent = hotness.persistent_hot(
        pack, daily_heat=params.channels.p3.daily_heat, tier=cfg)
    current = hotness.daily_heat_scores(
        pack, daily_heat=params.channels.p3.daily_heat, days=1
    ).filter(pl.col("trade_date") == pack.as_of).select("ts_code", "hot_score")
    contest = _contest(pack, cfg, params.volume.ma_days)
    return (
        pack.today.join(persistent, on="ts_code", how="left")
        .join(current, on="ts_code", how="left")
        .join(contest, on="ts_code", how="left")
        .filter(
            pl.col("hot_days").is_not_null()
            & (pl.col("hot_score") >= 1.0 - cfg.current_heat_top_pct)
            & ((pl.col("_max_move_ratio") >= cfg.min_absolute_move_as_limit_ratio)
               | (pl.col("_max_amplitude_ratio") >= cfg.min_amplitude_as_limit_ratio)
               | (pl.col("_max_vol_multiple") >= cfg.min_huge_vol_multiple))
        )
    )


def run(pack: PackRange, params: K9Params) -> List[ChannelHit]:
    if pack.today.is_empty():
        return []
    picked: Dict[str, Tier] = {}
    rows: Dict[str, dict] = {}
    for tier, cfg in ((Tier.STRICT, params.channels.p3.strict),
                      (Tier.RELAXED, params.channels.p3.relaxed)):
        for row in _frame(pack, params, cfg).iter_rows(named=True):
            code = str(row["ts_code"])
            if code not in picked:
                picked[code], rows[code] = tier, row
    result = []
    for code in sorted(picked):
        row = rows[code]
        evidence = _evidence(row)
        risks = ("limit_down_contest",) if evidence["limitDown"] else ()
        result.append(ChannelHit(
            ts_code=code, pattern=PATTERN, tier=picked[code], evidence=evidence,
            risks=risks, bonus_score=_bonus(evidence, params.channels.p3.bonuses),
            strength={
                "hotPersistence": row["hot_persistence"],
                "maxAmplitude": row["_max_amplitude_ratio"],
                "maxAbsoluteMove": row["_max_move_ratio"],
                "hugeTurnover": row["_max_vol_multiple"],
                "conflictRecency": row["_freshness"],
                "directionAndIndustryRelativeStrength": (
                    None if row.get("ret_1d") is None or row.get("rel_strength_1d") is None
                    else (float(row["ret_1d"]) + float(row["rel_strength_1d"])) / 2.0
                ),
            },
        ))
    return result


__all__ = ["PATTERN", "STRENGTH_KEYS", "run"]
