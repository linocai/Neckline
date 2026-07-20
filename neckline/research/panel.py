"""研究面板的加载与共享设定(plan 阶段 1)。所有 P1–P10 研究读同一面板、用同一
选股域 = 可比(任务纪律 3「所有研究在同一设定下跑」)。

样本内 / 样本外(防过拟合纪律):
    · 样本内(定参)：2020-01-01 ~ 2024-12-31
    · 样本外(验证)：2025-01-01 ~ 2026-07-17（只许看有限次）
    · moneyflow_dc 仅 2023-09-11 起 → 资金面研究窗口对齐时另建同窗基线（任务纪律 4）
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import polars as pl

from neckline.strategy import signals as S
from neckline.strategy.features import build_research_panel

SAMPLE_IN_START = date(2020, 1, 1)
SAMPLE_IN_END = date(2024, 12, 31)
SAMPLE_OUT_START = date(2025, 1, 1)
SAMPLE_OUT_END = date(2026, 7, 17)

MONEYFLOW_START = date(2023, 9, 11)  # 资金面窗口对齐用


def load_or_build_panel(cache_path: Optional[Path] = None, rebuild: bool = False) -> pl.DataFrame:
    """加载缓存面板(全期 2020-2026,含 fwd + ret_20d_pct);缺失/rebuild 则重建并落缓存。"""
    if cache_path is not None and cache_path.exists() and not rebuild:
        return pl.read_parquet(cache_path)
    panel = build_research_panel(SAMPLE_IN_START, SAMPLE_OUT_END, with_forward=True, max_hold=5)
    panel = S.add_ret_rank_column(panel)
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        panel.write_parquet(cache_path)
    return panel


def base_universe_expr() -> pl.Expr:
    """全研究通用的选股域清洗(不属于「被研究的参数」,常开保证可比):
        · 非 ST/*ST（退市风险与特殊涨跌幅,清洗掉）
        · 非北交所（数据有新三板回填瑕疵/流动性薄,阶段 0 遗留问题#2,整体排除）
        · qfq 收盘 ≥ 2 元（规避低价股/面值退市区,注:qfq 价历史偏低=更保守）
        · 20 日均额 ≥ 2000 万元（amount 单位千元 → ≥20000;滤掉流动性差/滑点失真的票）
        · ma20 非空（至少 20 交易日历史,新上市未成形的排除;次新精细过滤见 P6）
    """
    return (
        (~S.forbid_st())
        & (pl.col("board") != "BSE")
        & (pl.col("close") >= 2.0)
        & (pl.col("amount_ma20") >= 20000)
        & pl.col("ma20").is_not_null()
    )


def in_sample(panel: pl.DataFrame) -> pl.DataFrame:
    return panel.filter((pl.col("trade_date") >= SAMPLE_IN_START) & (pl.col("trade_date") <= SAMPLE_IN_END))


def out_sample(panel: pl.DataFrame) -> pl.DataFrame:
    return panel.filter((pl.col("trade_date") >= SAMPLE_OUT_START) & (pl.col("trade_date") <= SAMPLE_OUT_END))


__all__ = [
    "load_or_build_panel",
    "base_universe_expr",
    "in_sample",
    "out_sample",
    "SAMPLE_IN_START",
    "SAMPLE_IN_END",
    "SAMPLE_OUT_START",
    "SAMPLE_OUT_END",
    "MONEYFLOW_START",
]
