"""行业强度单一源(plan §五 v1.4-②,需求 8 排序键① / K2 验证的拥挤探测器)。

**背景**:需求 8 排序键第①级 = 行业强度排名,第②级 = 题材持续天数升序(H6 单调证据)。
这两个量在 v1.3 之前的生产代码里都不存在——排名从未实现;A2/B3(`holding_k4_check.py`)
的「题材持续天数」判据当时借用**概念板块** `board_age`(`report/sectors.py`)当代理,
而 advisory 规格档 A2/B3 原文是「**行业强度(top20%中位数)连续 N 天成员**」(对齐
`research/k4p_h6_theme.py`)。两者不是同一个量:概念板块是多对多(一票挂多个板块,
取 max)、行业是一对一(`stock_basic.industry`);H6 审计的是**行业**口径、不是
board_age 代理。本模块把「行业强度」做成**唯一源**,`report/holding_k4_check.py`
(A2/B3)、`report/intel_candidates.py`(候选安检 hard_cut/avoid_flag)、
`api/inquiry.py`(问询台 K4 提示)三处判据入口一并回归——**下游只许 import 本模块,
不得各自再算一份**。

**口径逐条对齐 `research/k4p_h6_theme.py::industry_persistence`**(独立实现,不
import 研究代码——研究侧代码是既定研究产出物,不因产品化回改;生产侧另起一份小函数
带自己的单测,同 `sectors.py::_add_board_age` 与 `research/p2_sector_age.py` 的分野
先例):

    · 行业 = `stock_basic.industry`(一票一行业、无沾边,与 `intel_candidates` 行业闸
      同源;110 个行业静态当前快照,回填偏差已声明——行业变更股按当前行业回溯)。
    · 行业当日强度 = 该行业当日成员 `ret_1d` **中位数**(over 全体有 `ret_1d` 且有
      `industry` 的票,**非域限**——不受 base_universe/卫生线约束)。
    · 成员数 < `_MIN_MEMBERS`(=5)的行业当日**不参与排名**(中位数不稳)。
    · 强度排名 `industry_rank` = 当日中位数(仅计入达标行业间)**降序名次**(1=最强)。
      不参与排名的行业本模块直接**不产出该行的记录**(调用方按"查无该行业"处理,
      语义上等价 `industry_rank=None`,同 `sectors.py::sector_hot_lookup` 的
      "缺省即弱"惯例,不必每天为 110 个行业都产出占位行)。
    · 强度日 = 当日中位数 ∈ 达标行业间前 20%(逐日 `quantile(_STRENGTH_QUANTILE)` 阈,
      默认 0.80)。
    · 持续天数 `industry_persist_days` = 连续处于强度日的天数,**计到当日为止,
      断裂重置**;**非强度日 = 0**(这一点比 research 脚本多产出——research 脚本
      只返回强度日的行,非强度日不出现;本模块对达标行业的每一天都产出一行,
      非强度日显式给 0,不留空,方便调用方按日查询)。

**无前视**:只读 ≤T 数据(§3.8)。`ret_1d` 直接用**原始**(未复权)`daily.close /
daily.pre_close - 1` 算,不走 `apply_qfq`——qfq 对 `close`/`pre_close` 用同一行同一
标量(`adj_factor/latest_adj_factor`)缩放,比值精确抵消(见 `data/adjust.py::qfq_expr`),
故与 `strategy/features.py::add_features` 在 qfq 面板上算出的 `ret_1d` **数值相同**,
但不必装配 `build_research_panel` 的全特征集(ma5/10/20/vol_ma/limit_derived/
daily_basic 等)——省去无关列的 I/O 与内存(全市场多年历史只取 4 列,见
`_load_ret1d_panel`),这也是"持续天数需要看多远历史"允许**不设人为下限窗口**
(只有上界 `<=trade_date`)的前提:本地实测全历史(2020-2026,~780 万行)4 列加载 +
全量分组/排名/分位/连续天数计算合计 < 1 秒,详见 ②-C 对拍报告。

**A2/B3 回归规格档(v1.4-②生效)**:`report/holding_k4_check.py` 的
`A2_theme_persist_ge_4`(hard_cut)/`B3_theme_persist_2_3`(avoid_flag)、
`report/intel_candidates.py` 的候选安检、`api/inquiry.py` 的问询台 K4 提示,
三处均改读 `stock_persist_days`(本模块),不再用概念板块 `board_age` 代理。
**概念板块 `board_age`/`SectorScore` 本身不退役**——仍用于板块展示("所属热门
板块"文案、常驻/暴起板块拥挤度排序等展示用途),只是不再充当"题材持续天数"
判据的数据源。
"""

from __future__ import annotations

import glob
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import polars as pl

logger = logging.getLogger(__name__)

# —— 常量(单一源;plan §五 v1.4-②-A 逐字点名的两个常量名)——————————————————————
_MIN_MEMBERS = 5           # 当日行业成员数下限(< 此值不参与排名/强度判定,中位数不稳)
_STRENGTH_QUANTILE = 0.80  # 强度日阈值:当日中位数 top 20%(逐日 quantile(0.80));
                           # 敏感性 ±1 格 0.85/0.70 只作 `compute_industry_strength(quantile=)`
                           # 参数化能力,生产默认仍 0.80(同 research `GRID_Q` 精神,不切默认值)


@dataclass
class IndustryStrength:
    """一个 (trade_date, industry) 的强度快照(同 `sectors.SectorScore` 的扁平风格,
    trade_date 不入字段——整份列表隐含"以调用时传入的 trade_date 为准",同 SectorScore
    先例)。只有达标(成员数 >= `_MIN_MEMBERS`)的行业才会出现在 `compute_industry_strength`
    的返回列表里,故 `industry_rank` 恒有值(不参与排名的行业直接不产出记录)。"""

    industry: str
    median_ret: float
    member_count: int
    industry_rank: int        # 1 = 当日最强
    is_strength_day: bool     # 当日中位数是否 ∈ 达标行业间前 20%
    persist_days: int         # 连续强度日天数(计到当日为止,断裂重置;非强度日=0)


def load_industry_map(db_path: Optional[Path] = None) -> Dict[str, str]:
    """`ts_code -> industry`(`stock_basic.industry`,一票一行业、非空过滤;口径同
    `research/k4p_h6_theme.py::load_industry_map`,经生产 `load_stock_basic` 单一源
    读取而非另开 sqlite3 连接——同一张表同一组列,逐行结果相同)。"""
    from neckline.data.market_data import load_stock_basic

    sb = load_stock_basic(db_path)
    if sb.is_empty() or "industry" not in sb.columns:
        return {}
    out: Dict[str, str] = {}
    for tc, ind in zip(sb["ts_code"].to_list(), sb["industry"].to_list()):
        if ind and str(ind).strip():
            out[tc] = str(ind).strip()
    return out


def _load_ret1d_panel(end: date, parquet_dir: Optional[Path], start: Optional[date] = None) -> pl.DataFrame:
    """全市场 [start, end] 的 `ts_code`/`trade_date`/`ret_1d`。只选 4 列做谓词下推,免去
    `build_research_panel` 全特征装配(qfq/adj_factor join/ma5-20/涨跌停/daily_basic)
    的 I/O 与内存开销——本函数只服务"行业当日中位数"这一个量,详见模块 docstring
    「无前视」节的 qfq 不变性证明。`start=None`(默认,`compute_industry_strength`/
    持续天数走这条路)只有上界(`<=end`)不设下界:持续天数的连续强度日计数要看多远
    历史,由"上次断裂"天然决定,人为下限窗口有截断真实持续天数、把长streak 低报的
    风险(§3.8 宁可多算不可少算);本地实测全历史加载廉价(<1s),不需要用窗口换性能。
    `start` 非空(v1.4-④ `industry_median_return_series` 的固定窗口用法,如"信息卡
    60日行业分歧线")时额外过滤 `>=start`,避免为一个几十天的窗口也去扫全历史
    (2020-2026,~780万行)——这条路径不需要"任意长回溯",加下界是纯粹的 I/O 节省,
    不改变"只用 ≤end 数据"的无前视语义。"""
    from neckline.data.market_data import table_dir

    d = table_dir("daily", parquet_dir)
    if not d.exists():
        return pl.DataFrame()
    pattern = str(d / "year=*" / "*.parquet")
    if not glob.glob(pattern):
        return pl.DataFrame()
    lf = pl.scan_parquet(pattern).select(["ts_code", "trade_date", "close", "pre_close"])
    lf = lf.filter(pl.col("trade_date") <= end)
    if start is not None:
        lf = lf.filter(pl.col("trade_date") >= start)
    df = lf.collect()
    if df.is_empty():
        return df
    df = df.filter(
        pl.col("close").is_not_null() & pl.col("pre_close").is_not_null() & (pl.col("pre_close") != 0)
    )
    return df.with_columns((pl.col("close") / pl.col("pre_close") - 1).alias("ret_1d"))


def _compute_daily_table(panel: pl.DataFrame, quantile: float) -> pl.DataFrame:
    """核心 polars 计算(**纯函数,无 I/O**)。`panel` 需含 `trade_date`/`industry`/
    `ret_1d` 列(其余列忽略)。返回**全部**满足 `member_count >= _MIN_MEMBERS` 的
    (trade_date, industry) 行,列 = trade_date/industry/median_ret/member_count/
    industry_rank/is_strength_day/industry_persist_days。

    口径逐条对齐 `research/k4p_h6_theme.py::industry_persistence`(排名/强度日集合/
    持续天数三项单测逐位对拍,见 `tests/test_industry_strength.py`);差异见模块
    docstring(新增 industry_rank;非强度日也保留 persist=0 行)。"""
    ind_daily = (
        panel.filter(pl.col("industry").is_not_null() & pl.col("ret_1d").is_not_null())
        .group_by(["trade_date", "industry"])
        .agg(pl.col("ret_1d").median().alias("median_ret"), pl.len().alias("member_count"))
        .filter(pl.col("member_count") >= _MIN_MEMBERS)
    )
    # 强度排名:当日中位数(仅达标行业间)降序名次,1=最强。ordinal 保证严格 1..N 无并列
    # (中位数是连续浮点值,两行业当日中位数恰好相等的概率可忽略;真撞了由行编码顺序
    # 任意打散,不影响下游——③ 排序键还有 code 兜底)。
    ind_daily = ind_daily.with_columns(
        pl.col("median_ret").rank(method="ordinal", descending=True).over("trade_date")
        .cast(pl.Int64).alias("industry_rank")
    )
    # 强度日:中位数 >= 当日(仅达标行业间)quantile(q) 阈。
    thr = pl.col("median_ret").quantile(quantile).over("trade_date")
    ind_daily = ind_daily.with_columns(thr.alias("_thr"))
    ind_daily = ind_daily.with_columns((pl.col("median_ret") >= pl.col("_thr")).alias("is_strength_day"))
    # 持续天数(连续强度日;断裂重置)——sort → flip cumsum → cum_count,与 research
    # `industry_persistence` 同一手法,仅在**达标**行业的时间线上做(未达标的日子在
    # `ind_daily` 里"不存在",不引入额外断裂,亦不贡献计数——与 research 版行为一致,
    # 这一点极端边界情形〔行业成员数当天跌破 5〕的口径分歧见模块 docstring)。
    ind_daily = ind_daily.sort(["industry", "trade_date"])
    flip = (pl.col("is_strength_day") != pl.col("is_strength_day").shift(1).fill_null(False)).over("industry")
    ind_daily = ind_daily.with_columns(flip.cum_sum().over("industry").alias("_run_id"))
    ind_daily = ind_daily.with_columns(
        pl.when(pl.col("is_strength_day"))
        .then(pl.col("trade_date").cum_count().over(["industry", "_run_id"]))
        .otherwise(0)
        .cast(pl.Int64)
        .alias("industry_persist_days")
    )
    return ind_daily.drop(["_thr", "_run_id"])


def compute_industry_strength(
    trade_date: date,
    *,
    quantile: float = _STRENGTH_QUANTILE,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> List[IndustryStrength]:
    """给定交易日的全行业强度/排名/持续天数(**唯一源**;口径见模块 docstring)。

    只返回当日成员数达 `_MIN_MEMBERS` 的行业(未达标 = 不参与排名,调用方按"查无该
    行业"处理——持续天数按 0、排名按 None,同 `sectors.py::sector_hot_lookup` 的
    "缺省即弱"惯例,见 `stock_persist_days`/`stock_industry_rank`)。`daily` 表当日
    无行 / `stock_basic` 无 industry 列 → 空列表(优雅降级,同 `compute_sector_strength`
    先例)。`quantile` 默认 `_STRENGTH_QUANTILE`(=0.80),±1 格 0.85/0.70 只作敏感性
    分析的参数化能力,生产调用不传(不切默认值)。
    """
    industry_of = load_industry_map(db_path)
    if not industry_of:
        return []
    ret = _load_ret1d_panel(trade_date, parquet_dir)
    if ret.is_empty():
        return []
    ind_map = pl.DataFrame({"ts_code": list(industry_of.keys()), "industry": list(industry_of.values())})
    panel = ret.join(ind_map, on="ts_code", how="inner")
    if panel.is_empty():
        return []
    daily = _compute_daily_table(panel, quantile)
    today = daily.filter(pl.col("trade_date") == trade_date)
    if today.is_empty():
        return []
    return [
        IndustryStrength(
            industry=r["industry"],
            median_ret=float(r["median_ret"]),
            member_count=int(r["member_count"]),
            industry_rank=int(r["industry_rank"]),
            is_strength_day=bool(r["is_strength_day"]),
            persist_days=int(r["industry_persist_days"]),
        )
        for r in today.sort("industry_rank").iter_rows(named=True)
    ]


def industry_strength_lookup(scores: List[IndustryStrength]) -> Dict[str, IndustryStrength]:
    """`industry -> IndustryStrength`,供 O(1) 查某行业当日是否达标/强度如何
    (同 `sectors.py::sector_hot_lookup` 惯例)。"""
    return {s.industry: s for s in scores}


def stock_persist_days(code: str, industry_of: Dict[str, str], hot: Dict[str, IndustryStrength]) -> int:
    """票的题材持续天数(**A2/B3 判据唯一源**)= 其 `stock_basic.industry` 当日
    `industry_persist_days`。无 industry / 行业当日不达标(成员<`_MIN_MEMBERS`)/
    查无该行业 → 0(不静默当"很持续",保守)。**一票一行业**,不再需要旧 board_age
    代理版本的 `max(该票所属多个概念板块的 board_age)`——这正是切换到行业口径后
    结构上的简化(H6 审计的量本就是一对一的行业,不是多对多的概念板块)。"""
    ind = industry_of.get(code)
    if not ind:
        return 0
    s = hot.get(ind)
    return s.persist_days if s is not None else 0


def stock_industry_rank(code: str, industry_of: Dict[str, str], hot: Dict[str, IndustryStrength]) -> Optional[int]:
    """票所属行业当日强度排名(1=最强);无 industry / 行业当日不参与排名 → `None`
    (给 ③ 排序键用:调用方须把 `None` 当"排最后"处理,不得静默当 0——0 会把无行业票
    错误顶到榜首,见 plan §五 v1.4-③-A)。"""
    ind = industry_of.get(code)
    if not ind:
        return None
    s = hot.get(ind)
    return s.industry_rank if s is not None else None


def industry_median_return_series(
    industry: str,
    start: date,
    end: date,
    *,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """给定行业在 `[start, end]` 每个交易日的成员 `ret_1d` 中位数(v1.4-④ 信息卡「行业
    分歧线」合成用,plan §五 v1.4-④-A-3)。与 `compute_industry_strength` **同源**
    (同一份 `load_industry_map` + 同一口径的 `ret_1d` 中位数),但**不受 `_MIN_MEMBERS`
    排名门槛约束**——指数合成只需要"这个行业当天整体涨跌多少"这一个统计量,不需要
    判"够不够格参与强度排名"这层资格判定,两者是同一原始统计量的不同消费场景。
    **是否该把这只票的行业当"够格合成指数"仍由调用方在合成前用
    `stock_industry_rank`/`compute_industry_strength` 判定**(信息卡的口径是:T0 当天
    行业成员<5〔不达标〕→ 整条分歧线标"行业样本不足,分歧线缺省",不调用本函数);
    本函数只负责"给定一个行业,老实吐出每天的中位数",不做该不该用的判断。

    返回逐日 `[{trade_date, median_ret, member_count}]`,升序。某日该行业**零**成员有
    `ret_1d`(如数据缺口)→ 当日不出现在返回列表里(如实反映"算不出中位数",不补 0 —
    是否把"当日无这一行"当"当日不涨不跌"处理是调用方的合成策略,不是本函数的职责)。
    `industry` 不在 `stock_basic.industry` 当前取值集合里 / 无价数据 → 空列表。"""
    industry_of = load_industry_map(db_path)
    if not industry_of or industry not in set(industry_of.values()):
        return []
    ret = _load_ret1d_panel(end, parquet_dir, start=start)
    if ret.is_empty():
        return []
    ind_map = pl.DataFrame({"ts_code": list(industry_of.keys()), "industry": list(industry_of.values())})
    panel = ret.join(ind_map, on="ts_code", how="inner").filter(pl.col("industry") == industry)
    if panel.is_empty():
        return []
    daily = (
        panel.filter(pl.col("ret_1d").is_not_null())
        .group_by("trade_date")
        .agg(pl.col("ret_1d").median().alias("median_ret"), pl.len().alias("member_count"))
        .sort("trade_date")
    )
    if daily.is_empty():
        return []
    return [
        {"trade_date": r["trade_date"], "median_ret": float(r["median_ret"]), "member_count": int(r["member_count"])}
        for r in daily.iter_rows(named=True)
    ]


__all__ = [
    "IndustryStrength",
    "load_industry_map",
    "compute_industry_strength",
    "industry_strength_lookup",
    "stock_persist_days",
    "stock_industry_rank",
    "industry_median_return_series",
]
