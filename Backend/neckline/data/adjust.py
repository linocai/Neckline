"""前复权(plan 0.5)。回测统一用前复权价(不改今日价,只调历史价,便于跨期比较)。

公式(TuShare 标准口径):
    qfq_price = raw_price × (adj_factor / latest_adj_factor)

`latest_adj_factor` = 该股票复权因子数列中【最新一条】(通常是当前/最近交易日)。
用最新因子做基准,历史价格向后统一到"以今日为基准"的口径——今日价格 qfq 后
与原始价格相等(latest_adj_factor / latest_adj_factor == 1)。
"""

from __future__ import annotations

from typing import Union

import polars as pl

Numeric = Union[float, int]

_PRICE_COLS = ("open", "high", "low", "close", "pre_close")


def qfq(price: Numeric, adj_factor: Numeric, latest_adj_factor: Numeric) -> float:
    """单值前复权。`latest_adj_factor` 为 0/None 时按未复权(比例 1.0)处理,不崩。"""
    if not latest_adj_factor:
        return float(price)
    return float(price) * (float(adj_factor) / float(latest_adj_factor))


def qfq_expr(price_col: str, adj_factor_col: str = "adj_factor", latest_col: str = "_latest_adj_factor") -> pl.Expr:
    """polars 表达式版:`df.with_columns(qfq_expr("close").alias("close_qfq"))`。"""
    return (
        pl.when(pl.col(latest_col).is_not_null() & (pl.col(latest_col) != 0))
        .then(pl.col(price_col) * (pl.col(adj_factor_col) / pl.col(latest_col)))
        .otherwise(pl.col(price_col))
    )


def apply_qfq(
    df: pl.DataFrame,
    price_cols: tuple = _PRICE_COLS,
    ts_code_col: str = "ts_code",
    trade_date_col: str = "trade_date",
    adj_factor_col: str = "adj_factor",
) -> pl.DataFrame:
    """给一个含 `ts_code/trade_date/adj_factor/<price_cols>` 的 DataFrame 加前复权列
    (`<col>_qfq`)。`latest_adj_factor` 按每个 ts_code 在传入数据范围内的最新
    trade_date 取值——调用方若只传部分区间,基准是该区间内最新一条,不是全历史
    最新一条(数据访问层做前视截断查询时天然满足"不用到未来因子"的约束)。

    列缺失 `adj_factor` 的行(左连接后 null)→ qfq 直接等于原始价(优雅降级,不崩)。
    """
    if df.is_empty():
        return df.with_columns([pl.col(c).alias(f"{c}_qfq") for c in price_cols if c in df.columns])

    latest = (
        df.sort([ts_code_col, trade_date_col])
        .group_by(ts_code_col, maintain_order=True)
        .agg(pl.col(adj_factor_col).last().alias("_latest_adj_factor"))
    )
    out = df.join(latest, on=ts_code_col, how="left")
    exprs = [qfq_expr(c, adj_factor_col=adj_factor_col).alias(f"{c}_qfq") for c in price_cols if c in df.columns]
    out = out.with_columns(exprs)
    return out.drop("_latest_adj_factor")


__all__ = ["qfq", "qfq_expr", "apply_qfq"]
