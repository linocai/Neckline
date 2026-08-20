"""上方机械空间 —— **全仓唯一实现**(裁定 1,PROJECT_PLAN §5.4.5)。

> `upside_room_mech_high = max(high[-N:])`,N = `params.ranking.upsideRoomMechDays`
> `upside_room_mech_pct = (high_N − close) / close`

**两个打分器都调它,⛔ 不许各写一份**(§5.4.5 末 + 守门 G12):

| 打分器 | 用在 | K9 出处 | 方向 |
|---|---|---|---|
| `score_room_far` | 形态 1 | §3.2「上方还剩多少,空间大更好」 | **正向**:空间大 → 分高 |
| `score_room_near` | 形态 3 | §3.4「贴着那个位置还没捅破的状态最好」 | **反向**:空间小 → 分高 |

🔴 **命名铁律(裁定 1)**:本模块算的是**上方机械空间**(机械、排序用、K9 第三层)。
它与**第一压力位**(LLM 逐票判断、预案用、K9 第四层)是**两个不同的量**,名字分开、
**永不互相顶替**,⛔ 也不另造第三个量。预案层那个价位的标识符⛔ 不许出现在
`k9/**` 的任何一行里(守门 G11 全仓扫描)。把排序用的机械空间喂给预案 LLM,等于
邀请它把那个数原样吐回来当预期离场价 —— 循环依赖当场复活
(§5.2 的工程决定,已定死)。

⚠ **窗口含当日**:`max(high[-N:])` 的 N 天里包含 `as_of` 当天 —— 「上方还剩多少」
问的是**站在今天收盘往上看**,今天自己冲到过的高点当然算数(否则一只今天创了新高
的票会被算出一个已经被它自己捅破的空间)。
"""

from __future__ import annotations

from typing import Optional

import polars as pl

from neckline.k9.contract import PackRange

HIGH_COLUMN = "upside_room_mech_high"
PCT_COLUMN = "upside_room_mech_pct"


def compute(pack: PackRange, *, days: int) -> pl.DataFrame:
    """`ts_code → (upside_room_mech_high, upside_room_mech_pct)`。

    历史一天都没有 / `close <= 0` → 两列为 null(⛔ 不填 0:「算不出来」不是
    「上方没有空间」)。
    """
    if days < 1:
        raise ValueError(f"ranking.upsideRoomMechDays 必须 >= 1,收到 {days}")
    schema = {"ts_code": pl.String, HIGH_COLUMN: pl.Float64, PCT_COLUMN: pl.Float64}
    if pack.frame.is_empty():
        return pl.DataFrame(schema=schema)

    window = pack.history(days=days, include_today=True)
    highs = (
        window.select(["ts_code", "high"])
        .filter(pl.col("high").is_not_null())
        .group_by("ts_code")
        .agg(pl.col("high").max().alias(HIGH_COLUMN))
    )
    today = pack.today.select(["ts_code", "close"])
    out = today.join(highs, on="ts_code", how="left").with_columns(
        pl.when(pl.col("close").is_not_null() & (pl.col("close") > 0)
                & pl.col(HIGH_COLUMN).is_not_null())
        .then((pl.col(HIGH_COLUMN) - pl.col("close")) / pl.col("close"))
        .otherwise(None)
        .alias(PCT_COLUMN)
    )
    return out.select(["ts_code", HIGH_COLUMN, PCT_COLUMN])


def score_room_far(pct: Optional[float]) -> Optional[float]:
    """形态 1 的**正向**读法:上方机械空间越大越好(K9 §3.2)。

    直接返回原值 —— 排序层会在**本形态候选集内**取百分位(§5.4.6),
    这里只负责说清「哪个方向算好」。`None` 原样传下去(⛔ 不补 0)。
    """
    return pct


def score_room_near(pct: Optional[float]) -> Optional[float]:
    """形态 3 的**反向**读法:贴着那个位置还没捅破最好(K9 §3.4)。

    取负号即可 —— 百分位是**序**的函数,取负就是把序反过来,与「另写一套
    倒序打分」逐位等价,却不多出第二份实现(裁定 1:⛔ 不另造第三个量)。
    """
    return None if pct is None else -pct


__all__ = ["HIGH_COLUMN", "PCT_COLUMN", "compute", "score_room_far", "score_room_near"]
