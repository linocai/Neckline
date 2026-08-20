"""形态 3 · 中等生转强(K9 §3.4)。

> **画像**:平时跟着行业走,不突出也不垃圾;这两天开始不一样了,有突破的形式。
> 趁它还没爆发提前埋伏。

| 类型 | 判据 | 参数 |
|---|---|---|
| **定义性** | 长窗 `longWindow` 日相对强度 **≈ 0**(\\|累计\\| ≤ `flatBand`) | 待标定(K9 原文约 60 日) |
| | 短窗 `shortWindow` 日相对强度 **> 0 且在改善** | 待标定(K9 原文约 5-10 日) |
| | 🔴 **当日尚未放量爆发**(裁定 14:放量倍数 < `volume.eruptionMultiple`) | 待标定(与形态 1 **共用同一个 V**) |
| **强度性** | 短窗改善幅度;上方机械空间(**反向**) | ⛔ 不设门槛,→ 第三层打分 |

🔴 **与形态 1 的互斥由判据本身保证**(裁定 15):形态 1 要求放量倍数 **≥ V**,
本形态要求 **< V**,同一个量、同一个 V,两个半区互补 —— 「今天爆了归形态 1,今天还
没爆归形态 3」(K9 §3.4)因此是一条**恒真**的话,不是一句需要事后仲裁的约定。
⛔ 「尚未爆发」**只看量,不加涨幅门槛**(裁定 14):加了涨幅就会出现「量没起来但涨幅
超标」的票两边都不中 —— 那不是互斥,是漏斗。

⚠ **两个窗口量的形状**(K9 与 Plan 都只写了「长窗相对强度 ≈ 0」「短窗转正且在改善」,
未给公式;本片取**逐日相对强度的累计和**,零新增参数,已登记 §14):

    长窗相对强度 = Σ rel_strength_1d(最近 longWindow 个交易日,含当日)
    短窗相对强度 = Σ rel_strength_1d(最近 shortWindow 个交易日,含当日)
    「在改善」    = 短窗相对强度 > 紧邻的**上一个**等长窗口的相对强度

理由:`rel_strength_1d` 是**逐日**的超额收益(裁定 2 口径),把一段时间「相对行业走得
如何」讲清楚的最省事读法就是把它加起来;「在改善」要的是**趋势**,与紧邻的等长窗口
比是唯一不引入新参数的比法。⛔ 没有引入「改善幅度门槛」这种新数 —— 改善幅度是
**强度性**,按 K9 §3.6 只进打分,不设门槛。

⚠ 「在改善」需要 **2 × shortWindow** 天历史;不够 → 该票不通过(⛔ 缺数不放行)。
"""

from __future__ import annotations

from typing import Dict, List, Optional

import polars as pl

from neckline.k9 import upside_room as upside_room_mod
from neckline.k9 import volume as volume_mod
from neckline.k9.contract import ChannelHit, PackRange, Pattern, Tier
from neckline.k9.params import K9Params, P3Tier

PATTERN = Pattern.P3

#: 强度项的键 —— 必须逐字对上 `params.ranking.patternSubWeights.p3`。
STRENGTH_KEYS = ("shortWindowImprovement", "upsideRoomNear")

_LONG = "_p3_long_rel"
_SHORT = "_p3_short_rel"
_PREV_SHORT = "_p3_prev_short_rel"
_IMPROVE = "_p3_improvement"


def _window_sum(pack: PackRange, *, days: int, skip_recent: int, alias: str) -> pl.DataFrame:
    """最近 `days` 个交易日(可先跳过最近 `skip_recent` 天)的 `rel_strength_1d` 累计和。

    历史不足整段窗口 → 该票**没有这一行**(⛔ 不拿半段窗口冒充整段:那会让上线首几天
    每只票的「长窗相对强度」都恰好 ≈ 0,整个形态当场失真)。
    """
    need = days + skip_recent
    pool = pack.history(days=need, include_today=True)
    schema = {"ts_code": pl.String, alias: pl.Float64}
    if pool.is_empty():
        return pl.DataFrame(schema=schema)
    sessions = sorted(pool["trade_date"].unique().to_list())
    if len(sessions) < need:
        return pl.DataFrame(schema=schema)
    window = sessions[:len(sessions) - skip_recent][-days:]
    return (
        pool.filter(pl.col("trade_date").is_in(window))
        .select(["ts_code", "rel_strength_1d"])
        .group_by("ts_code")
        .agg(
            pl.col("rel_strength_1d").sum().alias(alias),
            pl.col("rel_strength_1d").is_not_null().sum().alias("_n"),
        )
        # 窗口内有缺日的票不给读数(⛔ 缺数不当 0)
        .filter(pl.col("_n") >= days)
        .select(["ts_code", alias])
    )


def _passes(frame: pl.DataFrame, tier: P3Tier, eruption_v: float) -> List[str]:
    kept = frame.filter(
        pl.col(_LONG).is_not_null()
        & (pl.col(_LONG).abs() <= tier.flat_band)
        & pl.col(_SHORT).is_not_null()
        & (pl.col(_SHORT) > 0)
        & pl.col(_IMPROVE).is_not_null()
        & (pl.col(_IMPROVE) > 0)
        # 裁定 14 / 15:尚未放量爆发 = 放量倍数 < V(与形态 1 的 ≥ V 互补)
        & pl.col(volume_mod.COLUMN).is_not_null()
        & (pl.col(volume_mod.COLUMN) < eruption_v)
    )
    return kept["ts_code"].to_list()


def _tier_frame(pack: PackRange, base: pl.DataFrame, tier: P3Tier) -> pl.DataFrame:
    long_rel = _window_sum(pack, days=tier.long_window, skip_recent=0, alias=_LONG)
    short_rel = _window_sum(pack, days=tier.short_window, skip_recent=0, alias=_SHORT)
    prev_short = _window_sum(
        pack, days=tier.short_window, skip_recent=tier.short_window, alias=_PREV_SHORT)
    return (
        base.join(long_rel, on="ts_code", how="left")
        .join(short_rel, on="ts_code", how="left")
        .join(prev_short, on="ts_code", how="left")
        .with_columns((pl.col(_SHORT) - pl.col(_PREV_SHORT)).alias(_IMPROVE))
    )


def run(pack: PackRange, params: K9Params) -> List[ChannelHit]:
    today = pack.today
    if today.is_empty():
        return []

    strict, relaxed = params.channels.p3.strict, params.channels.p3.relaxed
    vol = volume_mod.compute(pack, ma_days=params.volume.ma_days)
    room = upside_room_mod.compute(pack, days=params.ranking.upside_room_mech_days)
    base = today.join(vol, on="ts_code", how="left").join(room, on="ts_code", how="left")

    v = params.volume.eruption_multiple
    frames = {
        Tier.STRICT: _tier_frame(pack, base, strict),
        Tier.RELAXED: _tier_frame(pack, base, relaxed),
    }
    picked: Dict[str, Tier] = {}
    for tier, cfg in ((Tier.STRICT, strict), (Tier.RELAXED, relaxed)):
        for code in _passes(frames[tier], cfg, v):
            picked.setdefault(code, tier)

    if not picked:
        return []
    hits: List[ChannelHit] = []
    for code in sorted(picked):
        tier = picked[code]
        row = {
            r["ts_code"]: r
            for r in frames[tier].select(
                ["ts_code", _IMPROVE, upside_room_mod.PCT_COLUMN]
            ).iter_rows(named=True)
        }[code]
        hits.append(ChannelHit(
            ts_code=code, pattern=PATTERN, tier=tier,
            strength={
                "shortWindowImprovement": row[_IMPROVE],
                # **反向**:贴着那个位置还没捅破最好(K9 §3.4)
                "upsideRoomNear": upside_room_mod.score_room_near(
                    row[upside_room_mod.PCT_COLUMN]),
            },
        ))
    return hits


__all__ = ["PATTERN", "STRENGTH_KEYS", "run"]
