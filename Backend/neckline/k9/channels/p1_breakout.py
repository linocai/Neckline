"""形态 1 · 放量启动(K9 §3.2)。

> **画像**:横盘很久的票,今天突然放量启动。启动次日进场。

| 类型 | 判据 | 参数 |
|---|---|---|
| **定义性** | 过去 `ampWindowDays` 天振幅 ≤ `ampMaxPct` | 待标定(K9 原文 20 天 / 25%) |
| | 当日涨幅 > `minRetPct` | 待标定(K9 原文 > 0) |
| | 🔴 **放量倍数 ≥ `volume.eruptionMultiple`**(裁定 15) | 待标定(与形态 3 **共用同一个 V**) |
| **强度性** | 放量倍数 / 上方机械空间(**正向**)/ 当日相对强度 | ⛔ 不设门槛,→ 第三层打分 |

🔴 **放量倍数是全文唯一同时是定义性与强度性的项**(裁定 15,K9 §3.6 已补明例外):
门槛 V 让「放量启动」的「放量」成立;门槛之上放得多与少仍有高低,所以同时是打分项。
V 与形态 3 的「尚未爆发」是**同一个值**,两形态因此严丝合缝互补 —— 互斥由判据本身
保证,⛔ 不靠事后仲裁(K9 §3.4)。

⚠ **「过去 N 天振幅」的形状**(Plan 与 K9 都只写了「振幅 ≤ 25%」,未给公式;
本片取**窗口极差**,已登记 §14):
    `窗口振幅 = (窗口内 max(high) − 窗口内 min(low)) / 窗口内 min(low) × 100`
理由:形态画像是「**横盘很久**」,横盘说的是整段区间的宽窄,不是每天各自的振幅平均。
⛔ 不是逐日 `amp_1d` 的均值 —— 那个量在「每天小幅震荡但一路走高」的票上照样很小,
而那种票恰恰不是横盘。
"""

from __future__ import annotations

from typing import List

import polars as pl

from neckline.k9 import upside_room as upside_room_mod
from neckline.k9 import volume as volume_mod
from neckline.k9.contract import ChannelHit, PackRange, Pattern, Tier, to_percent_points
from neckline.k9.params import K9Params, P1Tier

PATTERN = Pattern.P1

#: 强度项的键 —— 必须逐字对上 `params.ranking.patternSubWeights.p1`。
STRENGTH_KEYS = ("volMultiple", "upsideRoomFar", "relStrength")

_AMP_COLUMN = "_p1_window_amp_pp"


def window_amplitude(pack: PackRange, *, days: int) -> pl.DataFrame:
    """窗口振幅(**百分点**)。见模块 docstring 的形状说明。

    窗口含当日 —— 「过去 20 天」里今天也是一天,而且今天的启动阳线本来就该让
    这个区间变宽(所以启动幅度过大的票会被自己的振幅门槛挡掉,这是判据的本意)。
    历史一天都没有 → null → **不通过**(⛔ 「算不出来」不许当成「很横」)。
    """
    win = pack.history(days=days, include_today=True)
    if win.is_empty():
        return pl.DataFrame(schema={"ts_code": pl.String, _AMP_COLUMN: pl.Float64})
    agg = (
        win.select(["ts_code", "high", "low"])
        .group_by("ts_code")
        .agg(pl.col("high").max().alias("_hi"), pl.col("low").min().alias("_lo"))
    )
    return agg.with_columns(
        pl.when(pl.col("_lo").is_not_null() & (pl.col("_lo") > 0)
                & pl.col("_hi").is_not_null())
        .then(to_percent_points((pl.col("_hi") - pl.col("_lo")) / pl.col("_lo")))
        .otherwise(None)
        .alias(_AMP_COLUMN)
    ).select(["ts_code", _AMP_COLUMN])


def _passes(today: pl.DataFrame, tier: P1Tier, eruption_v: float) -> List[str]:
    """一档的定义性条件。任一读数为 null → 不通过(⛔ 缺数不放行)。"""
    kept = today.filter(
        pl.col(_AMP_COLUMN).is_not_null()
        & (pl.col(_AMP_COLUMN) <= tier.amp_max_pct)
        & pl.col("ret_1d").is_not_null()
        & (to_percent_points(pl.col("ret_1d")) > tier.min_ret_pct)
        # 裁定 15:放量倍数 ≥ V(与形态 3 的 < V 互补)
        & pl.col(volume_mod.COLUMN).is_not_null()
        & (pl.col(volume_mod.COLUMN) >= eruption_v)
    )
    return kept["ts_code"].to_list()


def run(pack: PackRange, params: K9Params) -> List[ChannelHit]:
    """两档都跑。同一只票两档都中 → 只留 `strict`(成色取更好的那个)。"""
    today = pack.today
    if today.is_empty():
        return []

    strict, relaxed = params.channels.p1.strict, params.channels.p1.relaxed
    # 两档的振幅窗口可以不同 → 各算各的,再按 tier 取用。
    frames = {
        Tier.STRICT: window_amplitude(pack, days=strict.amp_window_days),
        Tier.RELAXED: window_amplitude(pack, days=relaxed.amp_window_days),
    }
    vol = volume_mod.compute(pack, ma_days=params.volume.ma_days)
    room = upside_room_mod.compute(pack, days=params.ranking.upside_room_mech_days)
    base = today.join(vol, on="ts_code", how="left").join(room, on="ts_code", how="left")

    v = params.volume.eruption_multiple
    picked: dict = {}
    for tier, cfg in ((Tier.STRICT, strict), (Tier.RELAXED, relaxed)):
        frame = base.join(frames[tier], on="ts_code", how="left")
        for code in _passes(frame, cfg, v):
            picked.setdefault(code, tier)       # strict 先跑,先到先得

    if not picked:
        return []
    rows = {
        r["ts_code"]: r
        for r in base.select(
            ["ts_code", volume_mod.COLUMN, upside_room_mod.PCT_COLUMN, "rel_strength_1d"]
        ).iter_rows(named=True)
    }
    hits: List[ChannelHit] = []
    for code in sorted(picked):
        r = rows[code]
        hits.append(ChannelHit(
            ts_code=code, pattern=PATTERN, tier=picked[code],
            strength={
                "volMultiple": r[volume_mod.COLUMN],
                # **正向**:上方还剩多少,空间大更好(K9 §3.2)
                "upsideRoomFar": upside_room_mod.score_room_far(
                    r[upside_room_mod.PCT_COLUMN]),
                "relStrength": r["rel_strength_1d"],
            },
        ))
    return hits


__all__ = ["PATTERN", "STRENGTH_KEYS", "window_amplitude", "run"]
