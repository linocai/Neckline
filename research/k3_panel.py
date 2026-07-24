"""K3 扩展研究面板(B1.0b)。

在既有研究面板(`build_research_panel`)之上叠加 K3 超跌研究所需的新特征——趋势背景
(ma60/ma250/斜率/距年线)、跌法(连续阴线数)、深度横截面分位、跌停暴露前向标。
**只进 K3 研究面板**(落 `research/_cache/k3_panel.parquet`),不改生产报告读的既有列
语义,不改 `panel_full.parquet`(K1/K2 冻结可比件)。

全部后向窗口 / 前向标仅供事件研究度量、信号列绝不引用(无前视,承 features.py 铁律)。

**窗口**:载数据到**最新交易日**(让样本外冻结窗 [.., 2026-07-17] 的末端前瞻收益完整,
优于 K2 冻结件的截断尾);冻结/延展窗口在事件研究里用日期过滤切,不在此写死常量。

**ma250 硬数据下限**:本地 daily 仅 2020-01-02 起、无 2019 数据,ma250(需 250 交易日)
在最早约 1 年为 null,趋势背景维度有效窗 ≈ 2021-01 起(~5.5 年)——数据边界,非设计选择。
"""

from __future__ import annotations

import glob
from datetime import date
from pathlib import Path
from typing import Optional

import polars as pl

from neckline.strategy import signals as S
from neckline.strategy.features import build_research_panel

K3_PANEL_CACHE = Path(__file__).resolve().parent / "_cache" / "k3_panel.parquet"
K3_PANEL_START = date(2020, 1, 1)


def latest_data_date(parquet_dir: Optional[Path] = None) -> date:
    """data/parquet/daily 里最新交易日(按文件名)。"""
    from neckline.config import settings

    root = (parquet_dir or settings.parquet_dir) / "daily"
    files = sorted(glob.glob(str(root / "year=*" / "*.parquet")))
    if not files:
        raise RuntimeError("daily 无分区文件")
    stem = Path(files[-1]).stem  # YYYYMMDD
    return date(int(stem[:4]), int(stem[4:6]), int(stem[6:8]))


def _pct_rank_expr(col: str, out: str) -> pl.Expr:
    """当日横截面分位(0~1,1=当日该值最高;null 值分位为 null)。承 add_ret_rank_column 姿势。"""
    return (
        (pl.col(col).rank(method="average").over("trade_date") - 1)
        / (pl.col(col).count().over("trade_date") - 1).clip(lower_bound=1)
    ).alias(out)


def add_k3_features(panel: pl.DataFrame) -> pl.DataFrame:
    """在研究面板上加 K3 新特征。输入需含 add_features 产出(close/ret_1d/ret_5d/ret_10d/
    ret_20d/is_limit_down 等)。"""
    if panel.is_empty():
        return panel
    df = panel.sort(["ts_code", "trade_date"])

    over = "ts_code"
    # —— 趋势背景:半年线 / 年线 + 斜率 + 距线 ——
    df = df.with_columns(
        pl.col("close").rolling_mean(60, min_samples=60).over(over).alias("ma60"),
        pl.col("close").rolling_mean(250, min_samples=250).over(over).alias("ma250"),
    )
    df = df.with_columns(
        (pl.col("ma60") > pl.col("ma60").shift(10).over(over)).alias("ma60_slope_up"),
        (pl.col("ma250") > pl.col("ma250").shift(20).over(over)).alias("ma250_slope_up"),
        (pl.col("close") / pl.col("ma60") - 1).alias("dist_from_ma60"),
        (pl.col("close") / pl.col("ma250") - 1).alias("dist_from_ma250"),
    )

    # —— 跌法:连续阴线(ret_1d<0)run-length ——
    is_down = pl.col("ret_1d") < 0
    df = df.with_columns((~is_down).cast(pl.Int64).cum_sum().over(over).alias("_updown_blk"))
    df = df.with_columns(
        is_down.cast(pl.Int64).cum_sum().over([over, "_updown_blk"]).alias("consec_down_days")
    ).drop("_updown_blk")

    # —— 深度横截面分位(低分位=当日跌得最狠一档)——
    df = df.with_columns(
        _pct_rank_expr("ret_5d", "ret_5d_pct"),
        _pct_rank_expr("ret_10d", "ret_10d_pct"),
    )

    # —— 跌停暴露前向标(仅供事件研究,信号列不引用):T+1 / T+1~T+3 任一跌停 ——
    ld1 = pl.col("is_limit_down").shift(-1).over(over).fill_null(False)
    ld2 = pl.col("is_limit_down").shift(-2).over(over).fill_null(False)
    ld3 = pl.col("is_limit_down").shift(-3).over(over).fill_null(False)
    df = df.with_columns(
        ld1.alias("fwd_ld_next"),
        (ld1 | ld2 | ld3).alias("fwd_ld_hold3"),
    )

    return df.sort(["trade_date", "ts_code"])


def build_k3_panel(rebuild: bool = False, cache_path: Optional[Path] = None) -> pl.DataFrame:
    """加载/构建 K3 扩展面板(2020-01 ~ 最新交易日,含 fwd_* + K3 新特征)。"""
    cache = cache_path or K3_PANEL_CACHE
    if cache.exists() and not rebuild:
        return pl.read_parquet(cache)
    end = latest_data_date()
    base = build_research_panel(K3_PANEL_START, end, with_forward=True, max_hold=5)
    base = S.add_ret_rank_column(base)  # ret_20d_pct(与既有研究一致)
    panel = add_k3_features(base)
    cache.parent.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(cache)
    return panel


__all__ = ["build_k3_panel", "add_k3_features", "latest_data_date", "K3_PANEL_CACHE", "K3_PANEL_START"]
