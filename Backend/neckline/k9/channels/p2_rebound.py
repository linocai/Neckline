"""形态 2 · 超跌反弹(K9 §3.3)。

> **画像**:它和它的同类今天行为不一致,跌得反常,而且查明并非爆雷。次日看修复。

| 类型 | 判据 | 参数 |
|---|---|---|
| **定义性** | 归一化跌幅 = **跌幅 ÷ 该板跌停幅度** ≥ `normDropMin`(主板与创业板共用同一门槛) | 待标定 |
| | 前一日收盘价 ≥ `maDays` 日均线 | 待标定(K9 原文 20 日) |
| | 🔴 **非一字跌停**(裁定 13:开、高、低、收**四价全等于当日跌停价**) | **零参数** |
| | 🔴 **有实际换手**(裁定 13:放量倍数 ≥ `minVolMultiple`) | 待标定 |
| **强度性** | 跑输行业的幅度 | ⛔ 不设门槛,→ 第三层打分 |

**「前一日收盘 ≥ 20 日均线」是这个形态的分水岭**(K9 §3.3):暴跌之前它还站在均线
上方,说明今天这一跌是**异常事件**,而不是下跌趋势的又一根阴线。

**「非一字 + 有换手」用于在召回阶段挡掉爆雷跌停**:爆雷多为一字封死无量,恐慌盘砸出
的跌停有量、有反复。漏网的交解释层查公告(K9 §二 末段:消息面排除不在第一层)。

🔴 **一字跌停是零参数的精确定义**(裁定 13):开 = 高 = 低 = 收 = 当日跌停价。
跌停价由 `data/limit_derived.py` 的板块规则算(含 2020-08-24 创业板改革与
2026-07-06 主板 ST 两个制度分界日),⛔ **不要为它造参数**。
比较走**整数分**(`round(x*100)`):两个 2 位小数从不同路径算出来,直接用浮点相等
会在 1e-13 量级上翻车 —— 这正是 `limit_derived` 自己用整数分算涨跌停价的原因。

🔴 **有实际换手 ⛔ 不用换手率、不用成交额绝对值**(裁定 13):换手率受流通盘影响,
「今天到底有没有人在交易」要跟**这只票自己平时**比,不是跟别的票比。用的是与形态
1/3 **同一个**放量倍数(K9 §3.0.1,`k9/volume.py` 唯一实现)。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import polars as pl

from neckline.data.board import Board
from neckline.data.limit_derived import resolve_limit_pct
from neckline.k9 import volume as volume_mod
from neckline.k9.contract import ChannelHit, PackRange, Pattern, Tier
from neckline.k9.params import K9Params, P2Tier

PATTERN = Pattern.P2

#: 强度项的键 —— 必须逐字对上 `params.ranking.patternSubWeights.p2`。
STRENGTH_KEYS = ("relStrengthShortfall",)

_LIMIT_PCT = "_p2_limit_pct"
_NORM_DROP = "_p2_norm_drop"
_ONE_LINE = "_p2_one_line_limit_down"
_PREV_CLOSE = "_p2_prev_close"
_MA = "_p2_ma"


def board_limit_pct(pack: PackRange) -> pl.DataFrame:
    """每只票当日的**板块涨跌停幅度**(如 0.10 / 0.20)。

    ⚠ 走 `limit_derived.resolve_limit_pct` —— 它是那套板块规则(含两个制度分界日)
    的**唯一实现**,⛔ 别在这里另写一份。板块 × ST 只有 8 种组合,当日算 8 次即可,
    ⛔ 不必逐票调 5500 次。

    ⚠ 它**不处理新股豁免窗口**(上市头几日不设涨跌幅)。那类票由 K9 第一层第 5 条
    「次新股」排除,走不到这里;真走到了,归一化跌幅只会偏小 → 不通过,方向安全。
    """
    today = pack.today.select(["ts_code", "board", "is_st"])
    if today.is_empty():
        return pl.DataFrame(schema={"ts_code": pl.String, _LIMIT_PCT: pl.Float64})
    combos: Dict[Tuple[str, bool], float] = {}
    for board_s, st in {
        (str(b), bool(s))
        for b, s in zip(today["board"].to_list(), today["is_st"].fill_null(False).to_list())
    }:
        try:
            board = Board(board_s)
        except ValueError:
            continue                      # 板块判不出来 → 该票拿不到跌停幅度 → 不通过
        combos[(board_s, st)] = resolve_limit_pct(board, st, pack.as_of)
    return today.with_columns(
        pl.struct(["board", "is_st"])
        .map_elements(
            lambda r: combos.get((str(r["board"]), bool(r["is_st"]))),
            return_dtype=pl.Float64,
        )
        .alias(_LIMIT_PCT)
    ).select(["ts_code", _LIMIT_PCT])


def _cents(col: str) -> pl.Expr:
    """价格(元,2 位小数)→ 整数分。见模块 docstring 的「整数分比较」。"""
    return (pl.col(col) * 100).round(0).cast(pl.Int64)


def one_line_limit_down() -> pl.Expr:
    """🔴 **一字跌停**(裁定 13)= 开、高、低、收**四价全等于当日跌停价**。

    `limit_down_price` 为 null → **False**:`limit_derived` 是只存「有信号」行的稀疏表,
    一只真的一字跌停的票必然是信号行、必然有跌停价;没有那一行,就说明它今天没触及
    跌停 —— 更不可能是一字跌停。
    """
    ld = _cents("limit_down_price")
    return (
        pl.col("limit_down_price").is_not_null()
        & (_cents("open") == ld)
        & (_cents("high") == ld)
        & (_cents("low") == ld)
        & (_cents("close") == ld)
    ).fill_null(False)


def _prev_close_and_ma(pack: PackRange, *, ma_days: int) -> pl.DataFrame:
    """前一日收盘价与前一日为止的 `ma_days` 日均线。

    ⚠ 两个量都**截到前一日**(⛔ 不含当日):判据问的是「**暴跌之前**它还站在均线
    上方吗」,把今天这根大阴线算进均线等于让被判的对象参与判决。
    """
    hist = pack.history(days=ma_days, include_today=False)
    schema = {"ts_code": pl.String, _PREV_CLOSE: pl.Float64, _MA: pl.Float64}
    if hist.is_empty():
        return pl.DataFrame(schema=schema)
    last_day = max(hist["trade_date"].unique().to_list())
    prev = (
        hist.filter(pl.col("trade_date") == last_day)
        .select(["ts_code", "close"])
        .rename({"close": _PREV_CLOSE})
    )
    ma = (
        hist.select(["ts_code", "close"])
        .filter(pl.col("close").is_not_null())
        .group_by("ts_code")
        .agg(pl.col("close").mean().alias(_MA), pl.len().alias("_n"))
        # 历史不足整个窗口 → 不给均线(⛔ 不拿 3 天均线冒充 20 日均线)
        .filter(pl.col("_n") >= ma_days)
        .select(["ts_code", _MA])
    )
    return prev.join(ma, on="ts_code", how="left")


def _passes(frame: pl.DataFrame, tier: P2Tier) -> List[str]:
    kept = frame.filter(
        pl.col(_NORM_DROP).is_not_null()
        & (pl.col(_NORM_DROP) >= tier.norm_drop_min)
        & pl.col(_PREV_CLOSE).is_not_null()
        & pl.col(_MA).is_not_null()
        & (pl.col(_PREV_CLOSE) >= pl.col(_MA))
        & ~pl.col(_ONE_LINE)
        & pl.col(volume_mod.COLUMN).is_not_null()
        & (pl.col(volume_mod.COLUMN) >= tier.min_vol_multiple)
    )
    return kept["ts_code"].to_list()


def run(pack: PackRange, params: K9Params) -> List[ChannelHit]:
    today = pack.today
    if today.is_empty():
        return []

    strict, relaxed = params.channels.p2.strict, params.channels.p2.relaxed
    vol = volume_mod.compute(pack, ma_days=params.volume.ma_days)
    limits = board_limit_pct(pack)
    base = (
        today.join(vol, on="ts_code", how="left")
        .join(limits, on="ts_code", how="left")
        .with_columns(one_line_limit_down().alias(_ONE_LINE))
        .with_columns(
            # 归一化跌幅 = 跌幅 ÷ 该板跌停幅度。上涨的票天然为负 → 通不过门槛。
            pl.when(pl.col(_LIMIT_PCT).is_not_null() & (pl.col(_LIMIT_PCT) > 0)
                    & pl.col("ret_1d").is_not_null())
            .then(-pl.col("ret_1d") / pl.col(_LIMIT_PCT))
            .otherwise(None)
            .alias(_NORM_DROP)
        )
    )
    ma_frames = {
        Tier.STRICT: _prev_close_and_ma(pack, ma_days=strict.ma_days),
        Tier.RELAXED: _prev_close_and_ma(pack, ma_days=relaxed.ma_days),
    }

    picked: dict = {}
    for tier, cfg in ((Tier.STRICT, strict), (Tier.RELAXED, relaxed)):
        frame = base.join(ma_frames[tier], on="ts_code", how="left")
        for code in _passes(frame, cfg):
            picked.setdefault(code, tier)

    if not picked:
        return []
    rows = {
        r["ts_code"]: r
        for r in base.select(["ts_code", "rel_strength_1d"]).iter_rows(named=True)
    }
    hits: List[ChannelHit] = []
    for code in sorted(picked):
        rel = rows[code]["rel_strength_1d"]
        hits.append(ChannelHit(
            ts_code=code, pattern=PATTERN, tier=picked[code],
            # 「跑输行业的幅度」:相对强度必然为负(它正是靠跑输行业被选出来的),
            # 取负号让「跑输得越多 = 分越高」,方向与其它强度项一致。
            strength={"relStrengthShortfall": None if rel is None else -float(rel)},
        ))
    return hits


__all__ = ["PATTERN", "STRENGTH_KEYS", "board_limit_pct", "one_line_limit_down", "run"]
