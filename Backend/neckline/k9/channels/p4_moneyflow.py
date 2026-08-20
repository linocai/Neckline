"""形态 4 · 资金异动(K9 §3.5)。

> **画像**:钱在持续进场,价格还没跟上。四种形态里唯一从资金出发的。

| 类型 | 判据 | 参数 |
|---|---|---|
| **定义性** | 单日主力净流入 **> 0** 且排名前 `dailyInflowRankPct` | 待标定 |
| | `cumDays` 日累计净流入 **> 0** 且排名前 `cumInflowRankPct` | 待标定(K9 原文 5 日累计) |
| | 资金流入排名 − 涨跌幅排名 ≥ `lagRankGap`(价格明显落后于资金) | 待标定 |
| **强度性** | 净流入排名;**量比排名**(自算 `vol/vol_ma5`) | ⛔ 不设门槛,→ 第三层打分 |

**「5 日累计也要为正」用于区分建仓与噪声**(K9 §3.5):单日大额可能只是一笔大单,
连续多日流入说明有人在**系统性建仓**。

⚠ **量比一律用盘后口径**(K9 §3.5 / §4.7):全天成交量 ÷ 过去 **5** 日均量。
它与形态 1/2/3 用的**放量倍数**(÷ `volume.maDays` 日均量)是**两个不同的量**,
⛔ 别混 —— 两者的唯一实现都在 `k9/volume.py`,那里有对照说明。
排名**自算**而不读 `daily_basic.volume_ratio`:后者只有 2 位小数,做排名会大量并列
(§12 坑 4)。

⚠ **排名口径 = 百分位 ∈ [0,1],1 = 最强**(⛔ 不是「第几名」的绝对名次;已登记 §14):
Plan §5.4.5 写的是「资金流入排名 − 涨跌幅排名 ≥ `lagRankGap`」,这个减法的方向要求
**大 = 强**;而绝对名次会随当日票数漂移(全市场 5526 只 vs `moneyflow_dc` 的 5749 只,
§4.4),同一个 `lagRankGap` 在不同日子意思不一样。故一律用百分位,`lagRankGap`
也是 0~1 的差值。
"""

from __future__ import annotations

from typing import Dict, List, Optional

import polars as pl

from neckline.k9 import ranks as ranks_mod
from neckline.k9 import volume as volume_mod
from neckline.k9.contract import ChannelHit, PackRange, Pattern, Tier
from neckline.k9.params import K9Params, P4Tier

PATTERN = Pattern.P4

#: 强度项的键 —— 必须逐字对上 `params.ranking.patternSubWeights.p4`。
STRENGTH_KEYS = ("inflowRank", "volumeRatioRank")


def _cum_inflow(pack: PackRange, *, days: int) -> Dict[str, Optional[float]]:
    """最近 `days` 个交易日(含当日)的 `net_amount` 累计。

    窗口内有缺日的票不给读数(⛔ 缺数不当 0:那会让一只只有 1 天数据的票拿着
    「5 日累计」的名义参与排名)。
    """
    pool = pack.history(days=days, include_today=True)
    if pool.is_empty():
        return {}
    sessions = sorted(pool["trade_date"].unique().to_list())
    if len(sessions) < days:
        return {}
    agg = (
        pool.select(["ts_code", "net_amount"])
        .group_by("ts_code")
        .agg(
            pl.col("net_amount").sum().alias("_cum"),
            pl.col("net_amount").is_not_null().sum().alias("_n"),
        )
    )
    return {
        r["ts_code"]: (float(r["_cum"]) if r["_n"] >= days else None)
        for r in agg.iter_rows(named=True)
    }


def run(pack: PackRange, params: K9Params) -> List[ChannelHit]:
    today = pack.today
    if today.is_empty():
        return []

    strict, relaxed = params.channels.p4.strict, params.channels.p4.relaxed
    ratio = volume_mod.volume_ratio(pack)
    base = today.join(ratio, on="ts_code", how="left")

    daily_inflow: Dict[str, Optional[float]] = {
        r["ts_code"]: r["net_amount"]
        for r in base.select(["ts_code", "net_amount"]).iter_rows(named=True)
    }
    ret_of: Dict[str, Optional[float]] = {
        r["ts_code"]: r["ret_1d"]
        for r in base.select(["ts_code", "ret_1d"]).iter_rows(named=True)
    }
    ratio_of: Dict[str, Optional[float]] = {
        r["ts_code"]: r[volume_mod.RATIO_COLUMN]
        for r in base.select(["ts_code", volume_mod.RATIO_COLUMN]).iter_rows(named=True)
    }

    # 🔴 三个百分位都在**当日全市场**上取(K9 §3.5「居前」说的是全市场里居前)。
    inflow_rank = ranks_mod.pct_rank(daily_inflow)
    ret_rank = ranks_mod.pct_rank(ret_of)
    ratio_rank = ranks_mod.pct_rank(ratio_of)
    cum_ranks = {
        tier: ranks_mod.pct_rank(_cum_inflow(pack, days=cfg.cum_days))
        for tier, cfg in ((Tier.STRICT, strict), (Tier.RELAXED, relaxed))
    }
    cum_values = {
        tier: _cum_inflow(pack, days=cfg.cum_days)
        for tier, cfg in ((Tier.STRICT, strict), (Tier.RELAXED, relaxed))
    }

    def passes(code: str, cfg: P4Tier, tier: Tier) -> bool:
        daily = daily_inflow.get(code)
        cum = cum_values[tier].get(code)
        if daily is None or cum is None or daily <= 0 or cum <= 0:
            return False
        if not ranks_mod.in_top_fraction(inflow_rank.get(code), cfg.daily_inflow_rank_pct):
            return False
        if not ranks_mod.in_top_fraction(cum_ranks[tier].get(code), cfg.cum_inflow_rank_pct):
            return False
        ir, rr = inflow_rank.get(code), ret_rank.get(code)
        if ir is None or rr is None:
            return False
        # 价格表现明显落后于资金强度(K9 §3.5)
        return (ir - rr) >= cfg.lag_rank_gap

    picked: Dict[str, Tier] = {}
    codes = sorted(daily_inflow)
    for tier, cfg in ((Tier.STRICT, strict), (Tier.RELAXED, relaxed)):
        for code in codes:
            if code not in picked and passes(code, cfg, tier):
                picked[code] = tier

    return [
        ChannelHit(
            ts_code=code, pattern=PATTERN, tier=picked[code],
            strength={
                "inflowRank": inflow_rank.get(code),
                "volumeRatioRank": ratio_rank.get(code),
            },
        )
        for code in sorted(picked)
    ]


__all__ = ["PATTERN", "STRENGTH_KEYS", "run"]
