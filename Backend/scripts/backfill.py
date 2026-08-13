#!/usr/bin/env python3
"""全市场历史数据落地(plan 0.4)。daily / daily_basic / adj_factor / moneyflow_dc /
index_daily 按交易日落 Parquet(`data/parquet/<表>/year=YYYY/<trade_date>.parquet`),
外加 stock_basic / namechange 落 SQLite,末尾跑 0.4b 涨跌停衍生表。

断点续跑:每张表每天一个文件,写文件前先查文件是否已存在——已存在即跳过。
中途失败(限频/网络重试耗尽)的日子不会留下文件,下次重跑自动补齐,无需额外
状态文件。`backfill_log`(SQLite)只做审计留痕(行数/耗时),不参与续跑判断。

用法:
    python scripts/backfill.py                          # 全量 2020-01-01 ~ 今天
    python scripts/backfill.py --start 20260601 --end 20260630   # 小范围验证
    python scripts/backfill.py --skip-metadata           # 跳过 stock_basic/namechange 刷新
    python scripts/backfill.py --skip-limit-derived       # 跳过 0.4b 衍生表(单独再跑)
    python scripts/backfill.py --only limit_derived        # 只重算衍生表(用已落地的 daily 等)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import polars as pl  # noqa: E402

from neckline.calendar import is_trading_day, reset_cache, trading_days_between  # noqa: E402
from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.data.limit_derived import compute_limit_derived  # noqa: E402
from neckline.data.market_data import (  # noqa: E402
    day_file_exists,
    load_namechange,
    load_stock_basic,
    load_trade_cal_days,
    write_table_day,
)
from neckline.data.tushare_client import (  # noqa: E402
    ts_adj_factor_all,
    ts_daily_all,
    ts_daily_basic_all,
    ts_index_daily,
    ts_moneyflow_dc_all,
    ts_namechange_page,
    ts_stock_basic,
)
from neckline.db import connection, init_schema  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill")

# 全市场按 trade_date 批量拉取的四张日频大表(index_daily 单独处理:按指数代码一次
# 拿全区间,不是按日)。
DAY_TABLES = ["daily", "daily_basic", "adj_factor", "moneyflow_dc"]

# 追踪的指数(§2.6 母战法市场过滤器 P1 用上证;其余供后续板块/情绪面参考)。
INDEX_CODES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    "899050.BJ": "北证50",
}

_PANDAS_TO_POLARS_DATE_COLS = {"trade_date", "list_date", "delist_date", "start_date", "end_date", "ann_date"}


def _pdf_to_pl(pdf: "pd.DataFrame") -> pl.DataFrame:
    """pandas(TuShare 原始返回)→ polars,日期类字符串列统一转 pl.Date。

    plan §3.3:「pandas 仅用于边缘衔接」——TuShare 返回 pandas,这里是唯一转换点,
    之后全链路(Parquet 落地 / 数据访问层 / 回测)只碰 polars。
    """
    df = pl.from_pandas(pdf)
    exprs = []
    for c in df.columns:
        if c in _PANDAS_TO_POLARS_DATE_COLS and df.schema[c] != pl.Date:
            exprs.append(pl.col(c).cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False).alias(c))
    if exprs:
        df = df.with_columns(exprs)
    return df


def _log_audit(table: str, trade_date: date, status: str, row_count: int) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO backfill_log (table_name, trade_date, status, row_count, fetched_at) "
            "VALUES (?,?,?,?,?)",
            (table, trade_date.strftime("%Y%m%d"), status, row_count, datetime.now().isoformat()),
        )


def fetch_namechange_all() -> pd.DataFrame:
    """`namechange` 全量分页拉取 + 现存 ST 代码单票补拉兜底(见 tushare_client.py
    `ts_namechange_page` 的分页边界坑注释)。"""
    pages: List[pd.DataFrame] = []
    offset = 0
    limit = 8000
    while True:
        res = ts_namechange_page(limit=limit, offset=offset)
        if not res.ok or res.data is None or len(res.data) == 0:
            if not res.ok:
                logger.warning("namechange 分页(offset=%d)失败:%s", offset, res.reason)
            break
        pages.append(res.data)
        logger.info("namechange 分页:offset=%d 拿到 %d 行", offset, len(res.data))
        if len(res.data) < limit:
            break
        offset += limit
    if not pages:
        return pd.DataFrame(columns=["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"])
    all_nc = pd.concat(pages, ignore_index=True).drop_duplicates(subset=["ts_code", "start_date", "name"])
    return all_nc


def reconcile_current_st_names(nc_df: pd.DataFrame, stock_basic_df: pd.DataFrame) -> pd.DataFrame:
    """分页拉取存在边界漏行风险(已知坑,见 tushare_client 注释)。对【当前名称带
    ST/*ST】的代码单独补拉一次完整单票历史,覆盖分页可能漏掉的行——这批代码是
    "现在 ST 状态是否正确"验收(0.4b 抽样核对涨停家数窗口在近期)最敏感的子集,
    优先保正确性,不做全市场 5000+ 票逐一补拉(性价比低,阶段 0 可接受的范围)。
    """
    from neckline.data.tushare_client import ts_namechange

    cur_st_codes = stock_basic_df.loc[
        stock_basic_df["name"].fillna("").str.lstrip("*").str.startswith("ST"), "ts_code"
    ].tolist()
    if not cur_st_codes:
        return nc_df
    logger.info("补拉当前 ST/*ST 代码(%d 个)完整曾用名历史,兜底分页边界漏行…", len(cur_st_codes))
    extra = []
    for code in cur_st_codes:
        res = ts_namechange(code)
        if res.ok and res.data is not None and len(res.data):
            extra.append(res.data)
    if not extra:
        return nc_df
    merged = pd.concat([nc_df] + extra, ignore_index=True).drop_duplicates(subset=["ts_code", "start_date", "name"])
    return merged


def bootstrap_metadata() -> None:
    """stock_basic(L+D+P)+ namechange → SQLite。幂等(INSERT OR REPLACE)。"""
    logger.info("拉取 stock_basic(L/D/P)…")
    frames = []
    for status in ("L", "D", "P"):
        res = ts_stock_basic(status)
        if res.ok and res.data is not None:
            frames.append(res.data)
            logger.info("  list_status=%s: %d 行", status, len(res.data))
        else:
            logger.warning("  list_status=%s 拉取失败:%s", status, res.reason if not res.ok else "空")
    if not frames:
        raise RuntimeError("stock_basic 全部拉取失败,无法继续(检查 token / 网络)")
    stock_basic_df = pd.concat(frames, ignore_index=True)

    logger.info("拉取 namechange(全量分页)…")
    nc_df = fetch_namechange_all()
    nc_df = reconcile_current_st_names(nc_df, stock_basic_df)
    logger.info("namechange 最终 %d 行", len(nc_df))

    with connection() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO stock_basic "
            "(ts_code,symbol,name,industry,market,list_date,delist_date,list_status) VALUES (?,?,?,?,?,?,?,?)",
            list(
                stock_basic_df[
                    ["ts_code", "symbol", "name", "industry", "market", "list_date", "delist_date", "list_status"]
                ]
                .astype(object)
                .where(pd.notnull(stock_basic_df), None)
                .itertuples(index=False, name=None)
            ),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO namechange (ts_code,name,start_date,end_date,ann_date,change_reason) "
            "VALUES (?,?,?,?,?,?)",
            list(
                nc_df[["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"]]
                .astype(object)
                .where(pd.notnull(nc_df), None)
                .itertuples(index=False, name=None)
            ),
        )
    logger.info("元数据落库完成:stock_basic=%d, namechange=%d", len(stock_basic_df), len(nc_df))


def backfill_day_tables(days: List[date], tables: List[str], force: bool) -> dict:
    """day-major 循环:每个交易日依次拉 4 张全市场表,写 Parquet。返回统计 dict。"""
    stats = {t: {"fetched": 0, "skipped": 0, "failed": 0, "rows": 0} for t in tables}
    fetch_fns = {
        "daily": ts_daily_all,
        "daily_basic": ts_daily_basic_all,
        "adj_factor": ts_adj_factor_all,
        "moneyflow_dc": ts_moneyflow_dc_all,
    }
    t0 = time.time()
    for i, d in enumerate(days):
        td_str = d.strftime("%Y%m%d")
        for table in tables:
            if not force and day_file_exists(table, d):
                stats[table]["skipped"] += 1
                continue
            res = fetch_fns[table](td_str)
            if not res.ok or res.data is None:
                stats[table]["failed"] += 1
                logger.error("%s %s 拉取失败:%s(留空,下次重跑自动补)", table, td_str, res.reason)
                continue
            pdf = res.data
            pldf = _pdf_to_pl(pdf)
            write_table_day(table, d, pldf)
            stats[table]["fetched"] += 1
            stats[table]["rows"] += len(pldf)
            _log_audit(table, d, "ok" if len(pldf) else "empty", len(pldf))
        if (i + 1) % 20 == 0 or (i + 1) == len(days):
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (len(days) - i - 1) / rate if rate > 0 else float("nan")
            logger.info(
                "进度 %d/%d(%s) 已用 %.0fs,预计剩余 %.0fs",
                i + 1, len(days), td_str, elapsed, eta,
            )
    return stats


def backfill_index_daily(start: date, end: date) -> int:
    """指数日线:一次拿全区间(实测 6 年一把拿完,不必分批)。

    【坑,已修】write_table_day 是整天覆盖写,不是追加——早期版本按"指数外层
    循环、每个指数各自把自己的全部日子写一遍"的顺序写,同一天被后一个指数的
    写入覆盖掉前一个指数的数据,最终每天的 index_daily 文件只剩【最后处理的那
    个指数】(北证50,而且只在北证所开业后才有数据,之前的日子会被倒数第二个
    处理的指数即科创50覆盖)。改法:先把 5 个指数的全区间数据【拼接成一个大表】,
    再统一按 trade_date group_by 分天写,同一天的文件天然包含当天全部指数
    (与 daily/daily_basic 等表"一天一文件、一次性含全部代码"的约定一致)。
    """
    frames = []
    for code, name in INDEX_CODES.items():
        res = ts_index_daily(code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        if not res.ok or res.data is None:
            logger.error("index_daily %s(%s) 拉取失败:%s", code, name, res.reason)
            continue
        pldf = _pdf_to_pl(res.data)
        if pldf.is_empty():
            continue
        frames.append(pldf)
        logger.info("index_daily %s(%s): %d 行", code, name, len(pldf))
    if not frames:
        return 0
    combined = pl.concat(frames, how="vertical_relaxed")
    total_rows = 0
    for (d,), sub in combined.group_by(["trade_date"]):
        write_table_day("index_daily", d, sub)
        total_rows += len(sub)
    return total_rows


def run_limit_derived(start: date, end: date) -> int:
    """0.4b 涨跌停衍生表。读全量 daily(覆盖 start~end,连板计数需要连续历史,
    故读取范围比 [start,end] 前置 30 个自然日缓冲,避免窗口边界连板计数被截断)。
    """
    from datetime import timedelta

    from neckline.data.market_data import scan_table_range

    buffer_start = start - timedelta(days=30)
    daily = scan_table_range("daily", buffer_start, end)
    if daily.is_empty():
        logger.warning("limit_derived: [%s,%s] 区间 daily 为空,跳过。", start, end)
        return 0

    stock_basic = load_stock_basic()
    namechange = load_namechange()
    calendar_days = load_trade_cal_days()

    logger.info("计算 limit_derived:daily 输入 %d 行(含缓冲窗口)…", len(daily))
    t0 = time.time()
    out = compute_limit_derived(daily, stock_basic, namechange, calendar_days)
    logger.info("limit_derived 计算完成:%.1fs,命中 %d 行", time.time() - t0, len(out))

    # 只落 [start, end] 区间内的命中行(缓冲窗口只为连板计数正确性服务,不重复落盘)
    out = out.filter((pl.col("trade_date") >= start) & (pl.col("trade_date") <= end))
    n = 0
    for (d,), sub in out.group_by(["trade_date"]):
        write_table_day("limit_derived", d, sub)
        n += len(sub)
    logger.info("limit_derived 落盘完成:%d 行(%s ~ %s)", n, start, end)
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="20200101")
    parser.add_argument("--end", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--skip-metadata", action="store_true", help="跳过 stock_basic/namechange 刷新")
    parser.add_argument("--skip-limit-derived", action="store_true", help="跳过 0.4b 衍生表计算")
    parser.add_argument("--only", choices=["metadata", "daily_tables", "index", "limit_derived"], default=None)
    parser.add_argument("--force", action="store_true", help="忽略已存在文件,强制重拉(默认断点续跑跳过已存在)")
    args = parser.parse_args()

    if not settings.tushare_token:
        logger.error("TUSHARE_TOKEN 缺失(.env),无法拉取。")
        return 1

    ensure_data_dirs()
    init_schema()

    start_d = datetime.strptime(args.start, "%Y%m%d").date()
    end_d = datetime.strptime(args.end, "%Y%m%d").date()

    reset_cache()
    if not is_trading_day(start_d) and not _calendar_loaded():
        logger.error("交易日历未落库,先跑:python scripts/init_calendar.py")
        return 1

    t_start = time.time()

    if args.only in (None, "metadata") and not args.skip_metadata:
        bootstrap_metadata()

    days = trading_days_between(start_d, end_d)
    logger.info("回填区间 %s ~ %s,共 %d 个交易日", start_d, end_d, len(days))

    if args.only in (None, "daily_tables"):
        stats = backfill_day_tables(days, DAY_TABLES, force=args.force)
        for table, s in stats.items():
            logger.info(
                "[%s] 新拉 %d 天(%d 行)、跳过(已存在) %d 天、失败 %d 天",
                table, s["fetched"], s["rows"], s["skipped"], s["failed"],
            )

    if args.only in (None, "index"):
        idx_rows = backfill_index_daily(start_d, end_d)
        logger.info("[index_daily] 共 %d 行", idx_rows)

    if args.only in (None, "limit_derived") and not args.skip_limit_derived:
        run_limit_derived(start_d, end_d)

    logger.info("全部完成,总耗时 %.0fs", time.time() - t_start)
    return 0


def _calendar_loaded() -> bool:
    from neckline.data.market_data import load_trade_cal_days

    return len(load_trade_cal_days()) > 0


if __name__ == "__main__":
    raise SystemExit(main())
