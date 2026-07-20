"""强势板块(plan 2.2/1.6,§2.3)。板块**只加分不圈死**——全市场强势形态票均可入池
(见 `neckline.strategy.momentum.build_entry_mask` 的选股域,不受本模块影响);本模块
只产出【报告层软加权展示】与【候选评分的板块热度加分】(`neckline.report.candidates`
消费),不进任何硬评分门槛、不改变候选是否入池的资格。

**诚实口径(P2 待观察,弱证据,见 `research/stage1_report.md` P2 节)**:板块级早期
动量微弱(启动 1-5 天前瞻 3 日约 +0.2~0.37%,量级≈单边成本)、"4-5 天后降权"的假设
**不成立**(6-10 天回落但 11+ 天又回升,非单调衰减)。本模块因此**只给早期(1-5 天)
一个小额加分,不做衰减曲线**(回测没看到这条曲线真实存在,不能凭直觉编一条)。

**数据口径澄清(与阶段1 研究报告的限制不同)**:`ths_member` 是【当前】成分快照,
阶段1 报告已说明这一限制**只影响历史回测**(用当前成分反推历史归属,带前视/幸存者
偏差)——本模块是给"今天"生成报告,用当前成分做"这只票现在属于哪个概念板块"
是**完全正确**的口径,不存在同样的前视问题。

板块年龄的计算(`_add_board_age`)与 `research/p2_sector_age.py::add_board_age`
算法一致,但**独立实现**而非直接 import——研究侧代码是阶段1 结论的既定产出物,
不应因产品化而回改;生产侧另起一份小函数、带自己的单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import polars as pl

from neckline.config import settings as _default_settings

# 板块启动早期(1-5 个交易日连续站上 MA20)的报告层软加分(启发式常量,非回测拟合值;
# 呼应 stage1 P2 结论量级,只做展示排序的轻度加权,不是 alpha 主张)。
EARLY_AGE_BONUS = 3.0
EARLY_AGE_MIN_DAYS = 1
EARLY_AGE_MAX_DAYS = 5
DEFAULT_TOP_N = 10


@dataclass
class SectorScore:
    index_code: str          # 同花顺概念指数代码,如 "883300.TI"
    name: str
    board_age: int           # 连续站上 MA20 的交易日数(0=当前未站上/未启动)
    ret_20d: float
    bonus: float
    rank: int


def _ths_path(filename: str, parquet_dir: Optional[Path]) -> Path:
    return (parquet_dir or _default_settings.parquet_dir) / filename


def _load_board_daily(parquet_dir: Optional[Path] = None) -> pl.DataFrame:
    p = _ths_path("ths_daily.parquet", parquet_dir)
    if not p.exists():
        return pl.DataFrame()
    return pl.read_parquet(p).sort(["ts_code", "trade_date"])


def load_index_names(parquet_dir: Optional[Path] = None) -> Dict[str, str]:
    """`index_code -> 概念板块中文名`,全量(不限热榜)。`pipeline.py` 用它给候选的
    `sector_names`(所属全部概念板块,不限当前是否"热")做展示解析。"""
    p = _ths_path("ths_index.parquet", parquet_dir)
    if not p.exists():
        return {}
    df = pl.read_parquet(p).select(["ts_code", "name"])
    return dict(zip(df["ts_code"].to_list(), df["name"].to_list()))


def load_member_map(parquet_dir: Optional[Path] = None) -> Dict[str, List[str]]:
    """`con_code(股票代码) -> [index_code(所属概念板块代码), ...]`,来自 `ths_member`
    当前成分快照。缺文件 → 空 dict(优雅降级,候选评分的板块加分退化为 0,不崩)。"""
    p = _ths_path("ths_member.parquet", parquet_dir)
    if not p.exists():
        return {}
    df = pl.read_parquet(p).select(["index_code", "con_code"])
    out: Dict[str, List[str]] = {}
    for idx, code in zip(df["index_code"].to_list(), df["con_code"].to_list()):
        out.setdefault(code, []).append(idx)
    return out


def _add_board_age(bd: pl.DataFrame) -> pl.DataFrame:
    """板块年龄 = 板块指数连续站上 MA20 的交易日数(regime streak)。纯后向窗口,
    无前视——`ma20`/`above`/streak 分组全部只用当前行及更早行。"""
    bd = bd.with_columns(pl.col("close").rolling_mean(20, min_samples=20).over("ts_code").alias("ma20"))
    bd = bd.with_columns((pl.col("close") > pl.col("ma20")).alias("above"))
    bd = bd.with_columns(
        (pl.col("above") != pl.col("above").shift(1).over("ts_code")).fill_null(True).alias("_flip")
    )
    bd = bd.with_columns(pl.col("_flip").cast(pl.Int32).cum_sum().over("ts_code").alias("_grp"))
    bd = bd.with_columns(
        pl.when(pl.col("above"))
        .then(pl.int_range(0, pl.len()).over(["ts_code", "_grp"]) + 1)
        .otherwise(0)
        .alias("board_age")
    )
    bd = bd.with_columns((pl.col("close") / pl.col("close").shift(20).over("ts_code") - 1).alias("board_ret_20d"))
    return bd.drop(["_flip", "_grp"])


def compute_sector_strength(
    trade_date: date, parquet_dir: Optional[Path] = None, top_n: int = DEFAULT_TOP_N
) -> List[SectorScore]:
    """当日全部概念板块按「20 日动量 + 早期年龄软加分」排序,取前 `top_n`。
    `ths_daily.parquet` 缺失 / 当日无数据 → 空列表(优雅降级)。"""
    bd = _load_board_daily(parquet_dir)
    if bd.is_empty():
        return []
    # 前视保护(哪怕源文件本就只到某个历史截止日,显式截断更诚实,不依赖"文件本来就没有未来数据"这一隐含假设)
    bd = bd.filter(pl.col("trade_date") <= trade_date)
    if bd.is_empty():
        return []
    bd = _add_board_age(bd)
    today = bd.filter((pl.col("trade_date") == trade_date) & pl.col("ma20").is_not_null())
    if today.is_empty():
        return []
    today = today.with_columns(
        pl.when((pl.col("board_age") >= EARLY_AGE_MIN_DAYS) & (pl.col("board_age") <= EARLY_AGE_MAX_DAYS))
        .then(EARLY_AGE_BONUS)
        .otherwise(0.0)
        .alias("bonus")
    )
    today = today.sort("board_ret_20d", descending=True, nulls_last=True)

    names = load_index_names(parquet_dir)
    out: List[SectorScore] = []
    for i, r in enumerate(today.head(top_n).iter_rows(named=True), start=1):
        out.append(
            SectorScore(
                index_code=r["ts_code"],
                name=names.get(r["ts_code"], r["ts_code"]),
                board_age=int(r["board_age"] or 0),
                ret_20d=float(r["board_ret_20d"]) if r["board_ret_20d"] is not None else 0.0,
                bonus=float(r["bonus"]),
                rank=i,
            )
        )
    return out


def sector_hot_lookup(scores: List[SectorScore]) -> Dict[str, SectorScore]:
    """`index_code -> SectorScore`,供 `candidates.py` O(1) 查某板块是否在今日热榜。"""
    return {s.index_code: s for s in scores}


__all__ = [
    "SectorScore",
    "compute_sector_strength",
    "load_member_map",
    "load_index_names",
    "sector_hot_lookup",
    "EARLY_AGE_BONUS",
    "EARLY_AGE_MIN_DAYS",
    "EARLY_AGE_MAX_DAYS",
    "DEFAULT_TOP_N",
]
