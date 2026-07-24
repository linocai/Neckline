"""母战法信号定义（plan 1.1/1.2，§2.2 母战法）。

三类东西，全部实现为**作用在 `features.build_research_panel` 面板上的 polars 布尔
表达式**（`pl.Expr`），事件研究与组合回测策略共用同一份定义 = 同码（§2.6）：

    1. 强势定义（P3 三候选，赛马选胜）：涨停基因 / 20 日涨幅分位 / 量价结构。
    2. 买点（§2.2 两类）：① 强势票回调低吸；② 平台放量突破。
    3. 禁买过滤（P4/P5/P6）：绿盘大阴线 / 距前高过远 / 票型黑名单（次新·高弹·ST）。

**参数化但用粗网格**（防过拟合）：阈值集中在此处，研究以粗网格赛马，样本内定值、
样本外验证。表达式引用的列全部来自 `features.add_features/merge_*`（后向窗口，无
前视）；**绝不引用 `fwd_*` 前瞻列**。
"""

from __future__ import annotations

from typing import List, Optional

import polars as pl

# 高弹题材板块（20% 涨跌幅，易跌停）——票型黑名单 P6 候选
HIGH_ELASTICITY_BOARDS = ("GEM", "STAR", "BSE")


# ======================================================================
#  1. 强势定义（P3 三候选）
# ======================================================================

def strength_limitup_gene(min_count: int = 1) -> pl.Expr:
    """涨停基因：过去 20 日涨停次数 ≥ min_count。追「近期有涨停记录」的强势票。"""
    return pl.col("limitup_count_20d") >= min_count


def strength_ret_rank(min_ret_20d: float = 0.15) -> pl.Expr:
    """20 日涨幅（绝对阈值版）：ret_20d ≥ 阈值。粗口径「近 20 日涨幅达标即强」。

    分位数版见 `add_ret_rank_column` + `strength_ret_rank_pct`（需横截面排名列）。
    """
    return pl.col("ret_20d") >= min_ret_20d


def add_ret_rank_column(panel: pl.DataFrame) -> pl.DataFrame:
    """加「当日横截面 ret_20d 分位」列 `ret_20d_pct`（0~1，1=当日涨幅最高）。
    分位在**当日全市场**内算（只用当日数据，无前视）。ret_20d 为 null 的行分位为 null。"""
    return panel.with_columns(
        (
            (pl.col("ret_20d").rank(method="average").over("trade_date") - 1)
            / (pl.col("ret_20d").count().over("trade_date") - 1).clip(lower_bound=1)
        ).alias("ret_20d_pct")
    )


def strength_ret_rank_pct(min_pct: float = 0.90) -> pl.Expr:
    """20 日涨幅分位 ≥ min_pct（需先 `add_ret_rank_column` 注入 `ret_20d_pct`）。"""
    return pl.col("ret_20d_pct") >= min_pct


def strength_volprice() -> pl.Expr:
    """量价结构：均线多头（close>ma20 且 ma5>ma20）且近 5 日放量（vol_ratio_5≥1）。"""
    return pl.col("above_ma20_bullish") & (pl.col("vol_ratio_5") >= 1.0)


STRENGTH_DEFS = {
    "limitup_gene": strength_limitup_gene,
    "ret_20d": strength_ret_rank,
    "volprice": strength_volprice,
}


# ======================================================================
#  2. 买点（§2.2 两类）
# ======================================================================

def buy_pullback(pullback_pct: float = -0.0) -> pl.Expr:
    """① 强势票回调低吸：当日回调（ret_1d ≤ pullback_pct，默认收阴/走平）
    且仍站在 10 日线上方（close ≥ ma10，回调不破位）。与强势定义 AND 使用。"""
    return (pl.col("ret_1d") <= pullback_pct) & (pl.col("close") >= pl.col("ma10"))


def buy_breakout(vol_expand: float = 1.5) -> pl.Expr:
    """② 平台放量突破：今日收盘创前 20 日（不含今日)收盘新高，且放量
    （vol_ratio_5 ≥ vol_expand）。平台 = 前期未创新高的整理，突破即买点。"""
    return (pl.col("close") > pl.col("prev_close_max_20d")) & (pl.col("vol_ratio_5") >= vol_expand)


def buy_oversold(
    depth_col: Optional[str] = None,
    depth_max: Optional[float] = None,
    trend: Optional[str] = None,           # "up" | "down" | "mid"
    pullback_max: Optional[float] = None,  # dist_from_high_20d <= pullback_max
    confirm: Optional[str] = None,         # "reclaim_ma5" | "reclaim_ma10" | "stabilize"
    confirm_vol: Optional[float] = None,   # 收复类:vol_ratio_5 >= confirm_vol(放量确认)
    vol_max: Optional[float] = None,       # stabilize:vol_ratio_5 <= vol_max(缩量止跌)
) -> pl.Expr:
    """③ K3 超跌买点(§五C B2；`buypoint="oversold"` 分支专用，K1 pullback 路径绝不触及)。

    参数化组合 = B2 四臂:
      · 臂① 降势超卖:depth_col/depth_max(如 ret_5d≤-0.10)+ trend="down"
      · 臂② 深跌:depth_col="ret_20d", depth_max=-0.20(trend=None 不分趋势)
      · 臂③ 升势回撤+启动确认:trend="up" + pullback_max=-0.08 + confirm + (confirm_vol|vol_max)

    引用列全部后向窗口(ret_*/close/ma5/ma10/ma250/ma250_slope_up/dist_from_high_20d/
    vol_ratio_5/consec_down_days),无前视。趋势/企稳所需列(ma250/斜率/consec_down_days)
    仅存在于 K3 扩展面板——本函数**只在 `buypoint="oversold"` 分支被调用**,默认 pullback
    路径不引用这些列(K1 逐位不变,护栏单测锁死)。所有参数为 None 时退化为常真(no-op)。
    """
    conds: List[pl.Expr] = []
    if depth_col is not None and depth_max is not None:
        conds.append(pl.col(depth_col) <= depth_max)
    if trend == "up":
        conds.append((pl.col("close") > pl.col("ma250")) & pl.col("ma250_slope_up"))
    elif trend == "down":
        conds.append((pl.col("close") < pl.col("ma250")) & ~pl.col("ma250_slope_up"))
    elif trend == "mid":  # 站上年线但年线未升(中间态)
        conds.append((pl.col("close") > pl.col("ma250")) & ~pl.col("ma250_slope_up"))
    if pullback_max is not None:
        conds.append(pl.col("dist_from_high_20d") <= pullback_max)
    if confirm == "reclaim_ma5":
        c = (pl.col("ret_1d") > 0) & (pl.col("close") >= pl.col("ma5"))
        if confirm_vol is not None:
            c = c & (pl.col("vol_ratio_5") >= confirm_vol)
        conds.append(c)
    elif confirm == "reclaim_ma10":
        c = (pl.col("ret_1d") > 0) & (pl.col("close") >= pl.col("ma10"))
        if confirm_vol is not None:
            c = c & (pl.col("vol_ratio_5") >= confirm_vol)
        conds.append(c)
    elif confirm == "stabilize":  # 缩量止跌企稳:今日止跌(下跌 streak 中止)+ 缩量
        c = pl.col("consec_down_days") == 0
        if vol_max is not None:
            c = c & (pl.col("vol_ratio_5") <= vol_max)
        conds.append(c)
    if not conds:
        return pl.lit(True)
    expr = conds[0]
    for cc in conds[1:]:
        expr = expr & cc
    return expr


# ======================================================================
#  3. 禁买过滤（P4/P5/P6；返回「禁买」布尔，True = 该剔除）
# ======================================================================

def forbid_green_bigdown(threshold: float = -0.03) -> pl.Expr:
    """P4 绿盘大阴线：当日跌幅 ≤ threshold（默认 -3%）→ 禁买。"""
    return pl.col("ret_1d") <= threshold


def forbid_far_from_high(threshold: float = -0.15) -> pl.Expr:
    """P5 距 20 日高点过远：dist_from_high_20d ≤ threshold（默认 -15%）→ 禁买
    （下跌途中票）。"""
    return pl.col("dist_from_high_20d") <= threshold


def forbid_new_stock(min_days: int = 120) -> pl.Expr:
    """P6 次新：上市不足 min_days 自然日 → 禁买。"""
    return pl.col("days_since_listing") < min_days


def forbid_st() -> pl.Expr:
    """ST/*ST → 禁买（选股域清洗，全策略常开）。"""
    return pl.col("is_st")


def forbid_high_elasticity() -> pl.Expr:
    """P6 高弹题材：创业板/科创板/北交所（20%+ 涨跌幅，易跌停）→ 禁买。
    注意这是 P6 待验证的**可选**过滤（默认不开，回测定是否采纳）。"""
    return pl.col("board").is_in(list(HIGH_ELASTICITY_BOARDS))


__all__ = [
    "strength_limitup_gene",
    "strength_ret_rank",
    "add_ret_rank_column",
    "strength_ret_rank_pct",
    "strength_volprice",
    "STRENGTH_DEFS",
    "buy_pullback",
    "buy_breakout",
    "buy_oversold",
    "forbid_green_bigdown",
    "forbid_far_from_high",
    "forbid_new_stock",
    "forbid_st",
    "forbid_high_elasticity",
    "HIGH_ELASTICITY_BOARDS",
]
