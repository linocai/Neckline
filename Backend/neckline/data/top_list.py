"""龙虎榜(top_list)数据访问(plan 2.4 审判信息源之一)。

体量小(某日通常几十到二百来只票上榜),与阶段 0 全市场大表按年批量 backfill 的
负载不同——报告管线只需要"某一天"的龙虎榜,采用**现拉现落盘**:命中缓存直接读,
未命中且有 token 则调 TuShare 补一次,落盘复用 `market_data` 既有的"一天一文件"
Parquet 惯例(`data/parquet/top_list/year=YYYY/<trade_date>.parquet`)。无 token /
拉取失败 / 该日本就无票上榜 → 空表优雅降级,不崩(继承全项目 TushareResult 姿势)。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Dict, Optional

import polars as pl

from neckline.data.market_data import day_file_exists, get_market_slice, write_table_day
from neckline.data.tushare_client import ts_top_list

# 只消费官方文档 + 网页交叉核对确认过单位的列(§3.7 铁律,见 tushare_client.ts_top_list
# 的字段单位说明)。amount/float_values 单位未确认,不纳入。
_KEEP_COLS = [
    "ts_code",
    "name",
    "close",
    "pct_change",
    "turnover_rate",
    "l_buy",
    "l_sell",
    "net_amount",
    "net_rate",
    "reason",
]


def load_top_list(
    trade_date: date, parquet_dir: Optional[Path] = None, fetch_if_missing: bool = True
) -> pl.DataFrame:
    """读某交易日的龙虎榜缓存;缺失且 `fetch_if_missing` → 现拉现落盘。

    `trade_date` 须是 `date` 对象(报告管线内部统一用 date,不做字符串猜测解析)。
    """
    if day_file_exists("top_list", trade_date, parquet_dir=parquet_dir):
        return get_market_slice(trade_date, table="top_list", parquet_dir=parquet_dir)
    if not fetch_if_missing:
        return pl.DataFrame()

    res = ts_top_list(trade_date.strftime("%Y%m%d"))
    if not res.ok or res.data is None or len(res.data) == 0:
        return pl.DataFrame()

    df = pl.from_pandas(res.data)
    if "trade_date" in df.columns and df.schema.get("trade_date") != pl.Date:
        df = df.with_columns(pl.col("trade_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False))
    write_table_day("top_list", trade_date, df, parquet_dir=parquet_dir)
    return df


def top_list_lookup(trade_date: date, parquet_dir: Optional[Path] = None, fetch_if_missing: bool = True) -> Dict[str, dict]:
    """`ts_code -> 当日龙虎榜行(dict)`,供候选审判查某票是否上榜 + 净买卖情况。
    该日无龙虎榜数据 → 空 dict(优雅降级,不代表出错)。"""
    df = load_top_list(trade_date, parquet_dir=parquet_dir, fetch_if_missing=fetch_if_missing)
    if df.is_empty():
        return {}
    cols = [c for c in _KEEP_COLS if c in df.columns]
    out: Dict[str, dict] = {}
    for row in df.select(cols).iter_rows(named=True):
        out[row["ts_code"]] = row
    return out


__all__ = ["load_top_list", "top_list_lookup"]
