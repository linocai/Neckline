"""数据访问层(plan 0.6)。polars `scan_parquet` 惰性接口 + SQLite 元数据读取。

存储布局(plan §3.3,按年分区):
    data/parquet/<table>/year=YYYY/<trade_date>.parquet   —— 一日一文件
    table ∈ {daily, daily_basic, adj_factor, moneyflow_dc, index_daily, limit_derived}

【铁律】前视截断:任何查询不得返回 > 请求日的数据(§3.8,回测第 T 日任何计算都
不得读到 > T 的数据)。本层两个查询函数的截断保证:
    · `get_market_slice(trade_date)`  —— 只精确匹配 trade_date 这一天,结构上不可能
      多拿。
    · `get_stock_history(code, start, end, as_of=...)` —— 返回行严格满足
      `trade_date <= end`;若调用方传了 `as_of` 且 `end > as_of`,视为疑似前视 bug,
      截到 `as_of` 并打 warning。
单测见 `tests/test_market_data.py`(锁死"查询不得返回越界数据")。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Union

import polars as pl

from neckline.config import settings
from neckline.data.tushare_client import to_ts_code

logger = logging.getLogger(__name__)

DateLike = Union[date, datetime, str]

_VALID_TABLES = {
    "daily",
    "daily_basic",
    "adj_factor",
    "moneyflow_dc",
    "index_daily",
    "limit_derived",
}


def _to_date(d: DateLike) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    s = str(d).strip()
    if "-" in s:
        return datetime.strptime(s, "%Y-%m-%d").date()
    return datetime.strptime(s, "%Y%m%d").date()


def table_dir(table: str, parquet_dir: Optional[Path] = None) -> Path:
    if table not in _VALID_TABLES:
        raise ValueError(f"未知表名 {table!r},合法值:{sorted(_VALID_TABLES)}")
    return (parquet_dir or settings.parquet_dir) / table


def day_file_path(table: str, trade_date: DateLike, parquet_dir: Optional[Path] = None) -> Path:
    dt = _to_date(trade_date)
    return table_dir(table, parquet_dir) / f"year={dt.year}" / f"{dt.strftime('%Y%m%d')}.parquet"


def write_table_day(table: str, trade_date: DateLike, df: pl.DataFrame, parquet_dir: Optional[Path] = None) -> Path:
    """写一天一表的 Parquet 文件(backfill / daily_update 落盘统一入口,幂等覆盖)。"""
    path = day_file_path(table, trade_date, parquet_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    return path


def day_file_exists(table: str, trade_date: DateLike, parquet_dir: Optional[Path] = None) -> bool:
    return day_file_path(table, trade_date, parquet_dir).exists()


def _scan_table(table: str, parquet_dir: Optional[Path] = None) -> Optional[pl.LazyFrame]:
    """惰性 scan 某表全部年份分区。表目录不存在 / 无文件 → None(优雅降级,调用方
    返回空 DataFrame 而非报错——回测早期区间某表可能尚未 backfill 完整属正常态)。
    """
    d = table_dir(table, parquet_dir)
    if not d.exists():
        return None
    pattern = str(d / "year=*" / "*.parquet")
    import glob

    if not glob.glob(pattern):
        return None
    return pl.scan_parquet(pattern)


def get_market_slice(trade_date: DateLike, table: str = "daily", parquet_dir: Optional[Path] = None) -> pl.DataFrame:
    """全市场某交易日横截面。只精确匹配 trade_date,结构上不可能拿到 >请求日 的数据。"""
    dt = _to_date(trade_date)
    lf = _scan_table(table, parquet_dir)
    if lf is None:
        return pl.DataFrame()
    return lf.filter(pl.col("trade_date") == dt).collect()


def get_stock_history(
    code: str,
    start: DateLike,
    end: DateLike,
    table: str = "daily",
    as_of: Optional[DateLike] = None,
    parquet_dir: Optional[Path] = None,
) -> pl.DataFrame:
    """单票历史区间 [start, end](闭区间)。`as_of` 是当前模拟日上限保护:
    `end > as_of` 会被截断到 `as_of` 并打 warning(疑似调用方前视 bug)。"""
    sd, ed = _to_date(start), _to_date(end)
    if as_of is not None:
        as_of_d = _to_date(as_of)
        if ed > as_of_d:
            logger.warning(
                "get_stock_history(%s): end(%s) > as_of(%s),已截断到 as_of(疑似前视 bug,请检查调用方)",
                code, ed, as_of_d,
            )
            ed = as_of_d
    lf = _scan_table(table, parquet_dir)
    if lf is None or sd > ed:
        return pl.DataFrame()
    ts_code = to_ts_code(code)
    return (
        lf.filter((pl.col("ts_code") == ts_code) & (pl.col("trade_date") >= sd) & (pl.col("trade_date") <= ed))
        .sort("trade_date")
        .collect()
    )


def scan_table_range(table: str, start: DateLike, end: DateLike, parquet_dir: Optional[Path] = None) -> pl.DataFrame:
    """整表按 [start, end] 读(不按 ts_code 过滤,全市场该区间所有行)。供批量运算
    (如 backfill 侧的 limit_derived 连板计算)用,区别于按单票取历史的
    `get_stock_history`。同样满足"不返回 > end 的数据"。"""
    sd, ed = _to_date(start), _to_date(end)
    lf = _scan_table(table, parquet_dir)
    if lf is None:
        return pl.DataFrame()
    return lf.filter((pl.col("trade_date") >= sd) & (pl.col("trade_date") <= ed)).collect()


def get_index_history(
    index_code: str, start: DateLike, end: DateLike, as_of: Optional[DateLike] = None, parquet_dir: Optional[Path] = None
) -> pl.DataFrame:
    """指数区间日线(与 get_stock_history 同款前视保护,index_daily 表 ts_code 即指数代码)。"""
    return get_stock_history(index_code, start, end, table="index_daily", as_of=as_of, parquet_dir=parquet_dir)


# —— SQLite 元数据读取(stock_basic / namechange / trade_cal;小表,可整表读)——————

def load_stock_basic(db_path: Optional[Path] = None) -> pl.DataFrame:
    conn = sqlite3.connect(str(db_path or settings.db_path))
    try:
        rows = conn.execute(
            "SELECT ts_code, symbol, name, industry, market, list_date, delist_date, list_status FROM stock_basic"
        ).fetchall()
    finally:
        conn.close()
    df = pl.DataFrame(
        rows,
        schema={
            "ts_code": pl.Utf8,
            "symbol": pl.Utf8,
            "name": pl.Utf8,
            "industry": pl.Utf8,
            "market": pl.Utf8,
            "list_date": pl.Utf8,
            "delist_date": pl.Utf8,
            "list_status": pl.Utf8,
        },
        orient="row",
    )
    return df.with_columns(
        pl.col("list_date").str.strptime(pl.Date, "%Y%m%d", strict=False),
        pl.col("delist_date").str.strptime(pl.Date, "%Y%m%d", strict=False),
    )


def load_namechange(db_path: Optional[Path] = None) -> pl.DataFrame:
    conn = sqlite3.connect(str(db_path or settings.db_path))
    try:
        rows = conn.execute(
            "SELECT ts_code, name, start_date, end_date, ann_date, change_reason FROM namechange"
        ).fetchall()
    finally:
        conn.close()
    df = pl.DataFrame(
        rows,
        schema={
            "ts_code": pl.Utf8,
            "name": pl.Utf8,
            "start_date": pl.Utf8,
            "end_date": pl.Utf8,
            "ann_date": pl.Utf8,
            "change_reason": pl.Utf8,
        },
        orient="row",
    )
    return df.with_columns(
        pl.col("start_date").str.strptime(pl.Date, "%Y%m%d", strict=False),
        pl.col("end_date").str.strptime(pl.Date, "%Y%m%d", strict=False),
    )


def load_trade_cal_days(exchange: str = "SSE", db_path: Optional[Path] = None) -> List[date]:
    """全部交易日(is_open=1),升序。供 limit_derived / calendar 缓存使用。"""
    conn = sqlite3.connect(str(db_path or settings.db_path))
    try:
        rows = conn.execute(
            "SELECT cal_date FROM trade_cal WHERE exchange=? AND is_open=1 ORDER BY cal_date", (exchange,)
        ).fetchall()
    finally:
        conn.close()
    return [datetime.strptime(r[0], "%Y%m%d").date() for r in rows]


__all__ = [
    "table_dir",
    "day_file_path",
    "write_table_day",
    "day_file_exists",
    "get_market_slice",
    "get_stock_history",
    "scan_table_range",
    "get_index_history",
    "load_stock_basic",
    "load_namechange",
    "load_trade_cal_days",
]
