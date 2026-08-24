#!/usr/bin/env python3
"""交易日历落库(plan 0.3)。拉 TuShare `trade_cal` 落 SQLite,与静态休市表交叉核对。

用法:
    python scripts/init_calendar.py [--start 20150101] [--end 20271231]

幂等:INSERT OR REPLACE,重复跑安全。backfill.py / daily_update.py 依赖本脚本已跑过
(trade_cal 表非空)才能正确枚举交易日;若表为空,calendar 模块会退化为静态表兜底
并打 warning(不崩,但覆盖年份外精度打折)。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.data.tushare_client import ts_trade_cal  # noqa: E402
from neckline.db import connection, init_schema  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("init_calendar")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="20150101", help="起始日 YYYYMMDD")
    parser.add_argument("--end", default=None, help="截止日 YYYYMMDD；缺省=下一自然年年末")
    parser.add_argument("--exchange", default="SSE")
    args = parser.parse_args()

    end = args.end or f"{date.today().year + 1}1231"
    if not settings.tushare_token:
        logger.error("TUSHARE_TOKEN 缺失(.env),无法拉 trade_cal。")
        return 1

    ensure_data_dirs()
    init_schema()

    logger.info("拉取 trade_cal %s ~ %s(exchange=%s)…", args.start, end, args.exchange)
    res = ts_trade_cal(args.start, end, exchange=args.exchange)
    if not res.ok or res.data is None:
        logger.error("trade_cal 拉取失败:%s", res.reason)
        return 1

    df = res.data
    rows = list(
        zip(
            [args.exchange] * len(df),
            df["cal_date"].astype(str),
            df["is_open"].astype(int),
            df["pretrade_date"].astype(str),
        )
    )
    with connection() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO trade_cal (exchange, cal_date, is_open, pretrade_date) VALUES (?,?,?,?)",
            rows,
        )
    logger.info("trade_cal 落库完成:%d 行(%s ~ %s)", len(rows), args.start, end)

    open_days = int(df["is_open"].sum())
    logger.info("其中交易日(is_open=1):%d 天", open_days)

    # 交叉核对(重置缓存,确保读到刚写的库)
    from neckline.calendar import reset_cache, verify_against_static

    reset_cache()
    report = verify_against_static()
    if report["ok"]:
        n = len(report["mismatches"])
        if n == 0:
            logger.info("与静态休市表交叉核对:完全一致(覆盖年份内)。")
        else:
            logger.warning("与静态休市表交叉核对:%d 处不一致(以 DB/trade_cal 为准),详见上方 warning。", n)
    else:
        logger.warning("交叉核对未执行:%s", report["reason"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
