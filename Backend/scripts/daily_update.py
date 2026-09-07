#!/usr/bin/env python3
"""每交易日盘后增量更新(plan 0.4)。复用 backfill.py 的落盘函数,只跑一天
(默认今天,可传参指定其它交易日)+ 尾部窗口重算 limit_derived(连板计数跨批次
边界需要窗口,见 backfill.run_limit_derived 的 30 自然日缓冲说明)。

日更维护 K10 的日频行情、停牌、申万成员快照和涨跌停派生数据。数据源失败时不以旧数据
冒充成功；运行方可通过 `--retry-incomplete` 补拉不完整分区。

用法:
    python scripts/daily_update.py                # 今天(若非交易日则报错退出)
    python scripts/daily_update.py 20260717        # 指定某交易日补更新
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backfill  # noqa: E402  (同目录 scripts/backfill.py)

from neckline.calendar import CN_TZ, official_is_trading_day, reset_cache  # noqa: E402
from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.data.eod_validation import daily_basic_gaps  # noqa: E402
from neckline.data.market_data import day_file_exists, day_file_path  # noqa: E402
from neckline.db import init_schema, readonly_tables  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("daily_update")

LIMIT_DERIVED_TRAILING_DAYS = 15  # 尾部重算窗口(交易日),覆盖连板计数跨批次边界


def _read_day_partition(table: str, target: date) -> pl.DataFrame | None:
    path = day_file_path(table, target)
    if not path.exists():
        return None
    try:
        return pl.read_parquet(path)
    except Exception:  # noqa: BLE001
        logger.warning("[%s] %s 现役分区不可读,将重新拉取", table, target, exc_info=True)
        return None


def validate_daily_basic_payload(target: date, frame: pl.DataFrame) -> tuple[str, ...]:
    """Validate a response before it can replace the active daily_basic file."""
    daily = _read_day_partition("daily", target)
    if daily is None or daily.is_empty() or "ts_code" not in daily.columns:
        return ("daily_basic 无法对账:当日 daily 尚未就绪",)
    expected_codes = daily["ts_code"].drop_nulls().cast(pl.String).to_list()
    return daily_basic_gaps(target, frame, expected_daily_codes=expected_codes)


def _partition_needs_retry(table: str, target: date) -> bool:
    frame = _read_day_partition(table, target)
    if frame is None or frame.is_empty():
        return True
    if table == "daily_basic":
        return bool(validate_daily_basic_payload(target, frame))
    return False


def _day_tables_for_run(target: date, *, retry_incomplete: bool) -> list[str]:
    if not retry_incomplete:
        return list(backfill.DAY_TABLES)
    return [table for table in backfill.DAY_TABLES if _partition_needs_retry(table, target)]


def _has_recorded_sw_snapshot(target: date) -> bool:
    """Avoid refetching the current-only SW source when today's snapshot exists."""
    with readonly_tables(
        "sw_industry_member_snapshots",
        "sw_industry_snapshot_manifests",
    ) as conn:
        if conn is None:
            return False
        row = conn.execute(
            "SELECT m.row_count,COUNT(s.ts_code) "
            "FROM sw_industry_snapshot_manifests m "
            "LEFT JOIN sw_industry_member_snapshots s ON s.trade_date=m.trade_date "
            "WHERE m.trade_date=? GROUP BY m.row_count",
            (target.strftime("%Y%m%d"),),
        ).fetchone()
    return row is not None and int(row[0]) > 0 and int(row[0]) == int(row[1])


def update_sw_industry(target: date) -> bool:
    """Refresh the immutable SW2021 membership snapshot used by K10."""
    from neckline.data import sw_industry

    stats = sw_industry.refresh(target_date=target)
    if not stats.ok:
        logger.error(
            "[sw_industry] 申万分类日更未通过，K10 行情身份资料不完整。原因:%s。补算:"
            "python -c \"from datetime import date; from neckline.data import sw_industry; print(sw_industry.refresh(target_date=date.today()))\"",
            stats.reason,
        )
        return False
    logger.info("[sw_industry] %s", stats.summary())
    return True


def update_suspend_list(target: date) -> None:
    """当日全市场停牌名单落盘；这不是事实包的阻断输入。"""
    from neckline.data.market_data import write_table_day
    from neckline.data.tushare_client import ts_suspend_d_all

    try:
        res = ts_suspend_d_all(target.strftime("%Y%m%d"))
        if not res.ok or res.data is None:
            logger.warning("[suspend_d] 拉取失败:%s（不落盘）", res.reason)
            return
        # 当日零停牌是**正常且有信息量**的结果(「今天没人停牌」≠「今天没查」),照样落盘。
        # 空表也要给显式 dtype —— 全 Null dtype 列会成为下一次 `_align_to_table_schema`
        # 的脏基准(v1.3.5 事故的同一条链:空分区是脏基准的唯一来源)。
        df = backfill._pdf_to_pl(res.data) if len(res.data) else pl.DataFrame(
            schema={"ts_code": pl.String, "trade_date": pl.Date, "suspend_type": pl.String}
        )
        write_table_day("suspend_d", target, df)
        logger.info("[suspend_d] %s 停牌 %d 只", target, df.height)
    except Exception:  # noqa: BLE001
        logger.warning("[suspend_d] 日更异常(已吞,不阻断主增量)", exc_info=True)


def update_top_list(target: date) -> None:
    """Best-effort 龙虎榜 market-data refresh for K10 context."""
    from neckline.data.top_list import load_top_list

    try:
        frame = load_top_list(target, fetch_if_missing=True)
        from neckline.data.market_data import day_file_exists
        if day_file_exists("top_list", target):
            logger.info("[top_list] %s 已查，%d 行", target, frame.height)
        else:
            logger.warning("[top_list] %s 数据不可用", target)
    except Exception:  # noqa: BLE001
        logger.warning("[top_list] %s 获取异常", target,
                       exc_info=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trade_date", nargs="?", default=None, help="YYYYMMDD；缺省为今天")
    parser.add_argument(
        "--retry-incomplete",
        action="store_true",
        help="只重拉当日缺失/语义不完整的分区；供受控定时 recovery 使用",
    )
    args = parser.parse_args(argv)
    target = (
        datetime.strptime(args.trade_date, "%Y%m%d").date()
        if args.trade_date
        else datetime.now(CN_TZ).date()
    )

    ensure_data_dirs()
    init_schema()
    reset_cache()

    calendar_open = official_is_trading_day(target)
    if calendar_open is None:
        logger.error("%s 不在已落库的官方交易日历中；拒绝用工作日近似更新。先跑 scripts/init_calendar.py。", target)
        return 1
    if not calendar_open:
        logger.info("%s 不是交易日；K10 行情日更 no-op。", target)
        return 0
    if not settings.tushare_token:
        logger.error("TUSHARE_TOKEN 缺失(.env),无法拉取。")
        return 1

    logger.info("增量更新交易日:%s", target)

    if not args.retry_incomplete:
        backfill.bootstrap_metadata()

    day_tables = _day_tables_for_run(target, retry_incomplete=args.retry_incomplete)
    stats = backfill.backfill_day_tables(
        [target],
        day_tables,
        force=True,
        payload_validators={"daily_basic": validate_daily_basic_payload},
    )
    for table, s in stats.items():
        logger.info("[%s] 新拉 %d 天(%d 行)、失败 %d 天", table, s["fetched"], s["rows"], s["failed"])
    failed_tables = [table for table, item in stats.items() if item["failed"]]
    if failed_tables:
        logger.error("行情分区未完整更新:%s；保留现有分区，交由两个有界 retry 槽补拉。", ",".join(failed_tables))
        return 1

    index_needs_retry = _partition_needs_retry("index_daily", target)
    if not args.retry_incomplete or index_needs_retry:
        backfill.backfill_index_daily(target, target)

    from neckline.calendar import trading_days_between

    all_days = trading_days_between(date(target.year - 1, 1, 1), target)
    window_start = all_days[-LIMIT_DERIVED_TRAILING_DAYS] if len(all_days) >= LIMIT_DERIVED_TRAILING_DAYS else all_days[0]
    daily_refetched = "daily" in day_tables
    if not args.retry_incomplete or daily_refetched or not day_file_exists("limit_derived", target):
        backfill.run_limit_derived(window_start, target)

    # v1.4-①-B 增强项(尽力而为,失败不改退出码;放在主增量之后,免得它们的失败
    # 影响 EOD 主链路的落盘时序)。
    if not args.retry_incomplete or not day_file_exists("suspend_d", target):
        update_suspend_list(target)
    if not args.retry_incomplete or not day_file_exists("top_list", target):
        update_top_list(target)
    # V2.5.0 S2:申万二级分类日更(**判据输入**,失败打 ERROR;见函数 docstring)。
    sw_ready_for_build = args.retry_incomplete and _has_recorded_sw_snapshot(target)
    if sw_ready_for_build:
        logger.info("[sw_industry] %s 已有不可变成员快照,重试不重复请求当前源", target)
    elif not update_sw_industry(target):
        logger.error("[sw_industry] 目标日快照未就绪，拒绝完成 K10 行情日更。")
        return 1
    logger.info("增量更新完成:%s", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
