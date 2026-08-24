"""K9 第一层 · 股票池硬边界(K9 §二,PROJECT_PLAN §5.4.4)。

9 条排除项逐条实现,每条产出一个**具名** `exclusion_reason` —— 覆盖率成绩线的漏检
归因直接读它(§5.8.1:「昨天为什么没选中这只涨停票」从此是一次查表,不是一次考古)。

| # | 排除项 | 判据 | 参数 |
|---|---|---|---|
| 1 | 科创板 | `board == 'STAR'` | 无 |
| 2 | 白酒 | `sw_l2_code ∈ industry.excludedL2Codes` | 给定(`801125.SI`) |
| 3 | ST / *ST | `is_st`(事实包按 `namechange` 当日有效名算,复用 `limit_derived.is_st_name`) | 无 |
| 4 | 北交所 | `board == 'BSE'` | 无 |
| 5 | 次新股 | `trade_date − list_date < boundary.newListingDays` | 待标定 |
| 6 | 停牌 | `suspend_flag == 'S'`(**只认全天停牌**,裁定 12)或当日无 daily 行 | 无 |
| 7 | 活跃度不足 | 滚动中位成交额与参与密度合成分低于参数包分位线 | 待标定 |
| 8 | 当日涨停 | `is_limit_up`(主板 10% / 创业板 20% 一律排除,**不设例外**) | 无 |
| 9 | 当日冲高回落 | 当日涨幅 > `spikeFadeRetPct` **且** 最高涨幅 − 收盘涨幅 ≥ `spikeFadeGapPct` | 待标定 |

🔴 **第 6 条只认 `suspend_flag == 'S'`**:`S` 专指**全天停牌**;
盘中临时停牌是 `I`,那只票当天正常交易、有完整涨跌幅,⛔ 不排除。

🔴 **第 6 条的后半句「当日无 daily 行」在这里**(2026-08-21 复审 R3-🔴-5 修复):
事实包的行就是当日 `daily` 的行,全天没交易的票压根不在包里 —— 上一版据此把后半句
称作「结构性满足」,但那只证明了**它们不会被误放进池子**,不等于它们在
`k9_disposition` 里**有行**。§6 S6 要的是「覆盖全市场每一只票且 `excluded_by`
可解释」,而那张表存在的全部理由就是回答「昨天为什么没选中这只票」。
`apply()` 因此收一个必填的 `universe`(当日在市全集,`facts/universe.py`),
把缺席的票逐只补成 `suspended` 行。
⚠ 缺席票**只判得出这一条**:它当日没有任何行情行,除了「它没交易」之外我们不知道
任何别的事,⛔ 不猜它是不是科创板 / ST / 次新。

⚠ **消息面排除不在这一层**(K9 §二 末段):爆雷 / 减持 / 立案 / 监管在**解释层**查,
只对清单上的十几只票查公告,成本远低于全市场普查。⛔ 别把它挪进来。

⚠ **一条都不许「顺手放宽」**:第 8 条 K9 原文写着「不设例外」,理由在 §二 正文
(涨停次日三种走法都无法在盘后体系内处理,整类放弃)。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

import polars as pl

from neckline.data.board import Board
from neckline.k9 import activity as activity_mod
from neckline.facts.pack import SUSPEND_HALTED
from neckline.k9.contract import PackRange, to_percent_points
from neckline.k9.params import BoundaryParams, IndustryParams

logger = logging.getLogger(__name__)

# —— 9 条排除项的**具名**理由(闭合集合,⛔ 不许现编字符串)——————————————————
EXCL_STAR = "star_board"            # 1 科创板
EXCL_BAIJIU = "excluded_industry"   # 2 白酒(按代码,§12 坑 6)
EXCL_ST = "st"                      # 3 ST / *ST
EXCL_BSE = "bse_board"              # 4 北交所
EXCL_NEW_LISTING = "new_listing"    # 5 次新股
EXCL_SUSPENDED = "suspended"        # 6 停牌(**只认全天停牌**,裁定 12)
EXCL_INACTIVE = "inactive"           # 7 有效活跃度不足
EXCL_LIMIT_UP = "limit_up"          # 8 当日涨停
EXCL_SPIKE_FADE = "spike_fade"      # 9 当日冲高回落

#: 判定**次序固定**(⛔ 不许改):一只票可能同时满足多条,归因只记**第一条**。
#: 次序 = K9 §二 表的原序 —— 报告里说「它是科创板」比说「它今天涨停了」更接近根因。
EXCLUSION_ORDER: Tuple[str, ...] = (
    EXCL_STAR, EXCL_BAIJIU, EXCL_ST, EXCL_BSE, EXCL_NEW_LISTING,
    EXCL_SUSPENDED, EXCL_INACTIVE, EXCL_LIMIT_UP, EXCL_SPIKE_FADE,
)

EXCLUSION_LABEL: Dict[str, str] = {
    EXCL_STAR: "科创板",
    EXCL_BAIJIU: "白酒(行业排除)",
    EXCL_ST: "ST / *ST",
    EXCL_BSE: "北交所",
    EXCL_NEW_LISTING: "次新股",
    EXCL_SUSPENDED: "全天停牌",
    EXCL_INACTIVE: "有效活跃度不足",
    EXCL_LIMIT_UP: "当日涨停",
    EXCL_SPIKE_FADE: "当日冲高回落",
}
assert set(EXCLUSION_LABEL) == set(EXCLUSION_ORDER)

REASON_COLUMN = "excluded_by"


def _activity_cut(
    pack: PackRange, boundary: BoundaryParams, activity_min_percentile: float,
) -> pl.DataFrame:
    """第 7 条：有效活跃度低于参数包给定的横截面门槛。"""
    scored = activity_mod.compute(pack, target=pack.as_of, params=boundary)
    if scored.is_empty():
        return pl.DataFrame(schema={"ts_code": pl.String, "_inactive": pl.Boolean})
    return scored.select(
        "ts_code",
        (pl.col(activity_mod.SCORE) < activity_min_percentile).alias("_inactive"),
    )


def _absent_rows(codes: Sequence[str]) -> pl.DataFrame:
    """当日**一行都没有**的票 → 第 6 条后半句「当日无 daily 行」= 全天停牌。"""
    picked = sorted(set(codes))
    return pl.DataFrame(
        {"ts_code": picked, REASON_COLUMN: [EXCL_SUSPENDED] * len(picked)},
        schema={"ts_code": pl.String, REASON_COLUMN: pl.String},
    )


def apply(
    pack: PackRange,
    *,
    boundary: BoundaryParams,
    industry: IndustryParams,
    universe: Sequence[str],
    activity_min_percentile: float,
) -> pl.DataFrame:
    """当日全市场 → `ts_code / excluded_by`(未被排除的票 `excluded_by` 为 null)。

    返回的行 = **当日在市的每一只票**:事实包的全部行(⛔ 不先过滤)+ `universe`
    里当日连一行行情都没有的那些(第 6 条后半句,补成 `suspended`)。
    §5.4.8 的「昨天为什么没选中这只票」要对**全市场**答得上来,包括那些一整天
    都没交易过的。

    🔴 `universe` **必填**(`facts.universe.market_universe`,⛔ 不给默认值):
    给它一个空默认等于让「覆盖全市场」这条承诺在调用方忘了传的时候安静退化成
    「覆盖事实包」—— 那正是 R3-🔴-5 查出来的那次静默降级。
    ⚠ `universe` 为空(`stock_basic` 还没抓过)时只剩事实包那一半,并打一条
    WARNING;那属于上游数据缺口,归 `facts/completeness.py` 判「今天没跑成」。
    """
    today = pack.today
    absent = sorted(set(universe) - set(today["ts_code"].to_list() if not today.is_empty() else []))
    if absent:
        logger.info(
            "[k9] %s 全市场 disposition:%d 只票当日无 daily 行(全天停牌),"
            "按第 6 条后半句补进 disposition", pack.as_of, len(absent))
    if not universe:
        logger.warning(
            "[k9] %s 拿到的全市场票池是空的 —— disposition 本次只覆盖当日事实包的行,"
            "「一只票都没交易过」的那些查不出答案", pack.as_of)
    if today.is_empty():
        return _absent_rows(absent)

    excluded_codes = set(industry.excluded_l2_codes)
    activity = _activity_cut(pack, boundary, activity_min_percentile)
    df = today.join(activity, on="ts_code", how="left")

    #: 第 9 条:当日涨幅 > A **且** 最高涨幅 − 收盘涨幅 ≥ B(单位:**百分点**)。
    #: 「最高涨幅」= `(high − pre_close) / pre_close`。
    high_ret_pp = to_percent_points((pl.col("high") - pl.col("pre_close")) / pl.col("pre_close"))
    close_ret_pp = to_percent_points(pl.col("ret_1d"))

    age_days = (pl.lit(pack.as_of) - pl.col("list_date")).dt.total_days()

    verdict = (
        pl.when(pl.col("board") == Board.STAR.value).then(pl.lit(EXCL_STAR))
        .when(pl.col("sw_l2_code").is_in(list(excluded_codes)) if excluded_codes
              else pl.lit(False)).then(pl.lit(EXCL_BAIJIU))
        .when(pl.col("is_st").fill_null(False)).then(pl.lit(EXCL_ST))
        .when(pl.col("board") == Board.BSE.value).then(pl.lit(EXCL_BSE))
        # `list_date` 未知 → **不排除**(⛔ 不拿缺数当「它很新」)。
        .when(age_days.is_not_null() & (age_days < boundary.new_listing_days))
        .then(pl.lit(EXCL_NEW_LISTING))
        # 裁定 12:只认 'S'(全天停牌);'I'(盘中临时停牌)与 'R'(复牌)照常参与。
        .when(pl.col("suspend_flag") == SUSPEND_HALTED).then(pl.lit(EXCL_SUSPENDED))
        .when(pl.col("_inactive").fill_null(True)).then(pl.lit(EXCL_INACTIVE))
        .when(pl.col("is_limit_up").fill_null(False)).then(pl.lit(EXCL_LIMIT_UP))
        # ⚠ `high` / `pre_close` 缺一 → `high_ret_pp` 为 null → **不排除**(复审 L2)。
        # 这一句上一版是**隐式**的(null 传染进 `&` 之后 `when` 当 false),现在写明:
        # 方向与第 7 条一致(「算不出来」不等于「它冲高回落了」),⛔ 但不许再靠
        # 「读的人自己看得出 null 传染」——那是一条没写下来的口径。
        .when(
            close_ret_pp.is_not_null()
            & high_ret_pp.is_not_null()
            & (close_ret_pp > boundary.spike_fade_ret_pct)
            & ((high_ret_pp - close_ret_pp) >= boundary.spike_fade_gap_pct)
        ).then(pl.lit(EXCL_SPIKE_FADE))
        .otherwise(pl.lit(None, dtype=pl.String))
        .alias(REASON_COLUMN)
    )
    scored = df.with_columns(verdict).select(["ts_code", REASON_COLUMN])
    if absent:
        scored = pl.concat([scored, _absent_rows(absent)], how="vertical")
    return scored.sort("ts_code")


def survivors(verdicts: pl.DataFrame) -> List[str]:
    """通过硬边界的票(升序)。"""
    if verdicts.is_empty():
        return []
    return sorted(
        verdicts.filter(pl.col(REASON_COLUMN).is_null())["ts_code"].to_list()
    )


def counts(verdicts: pl.DataFrame) -> Dict[str, int]:
    """逐条排除项的命中数(报告 / 归因用)。⛔ 不合并成一个总数。"""
    if verdicts.is_empty():
        return {r: 0 for r in EXCLUSION_ORDER}
    got = dict(
        verdicts.filter(pl.col(REASON_COLUMN).is_not_null())
        .group_by(REASON_COLUMN)
        .agg(pl.len().alias("n"))
        .iter_rows()
    )
    return {r: int(got.get(r, 0)) for r in EXCLUSION_ORDER}


__all__ = [
    "EXCL_STAR", "EXCL_BAIJIU", "EXCL_ST", "EXCL_BSE", "EXCL_NEW_LISTING",
    "EXCL_SUSPENDED", "EXCL_INACTIVE", "EXCL_LIMIT_UP", "EXCL_SPIKE_FADE",
    "EXCLUSION_ORDER", "EXCLUSION_LABEL", "REASON_COLUMN",
    "apply", "survivors", "counts",
]
