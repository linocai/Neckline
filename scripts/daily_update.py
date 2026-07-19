#!/usr/bin/env python3
"""每交易日盘后增量更新(plan 0.4)。复用 backfill.py 的落盘函数,只跑一天
(默认今天,可传参指定其它交易日)+ 尾部窗口重算 limit_derived(连板计数跨批次
边界需要窗口,见 backfill.run_limit_derived 的 30 自然日缓冲说明)。

用法:
    python scripts/daily_update.py                # 今天(若非交易日则报错退出)
    python scripts/daily_update.py 20260717        # 指定某交易日补更新

建议 16:00 后(A 股盘后数据稳定,§2.3)用 cron / launchd 定时跑。
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backfill  # noqa: E402  (同目录 scripts/backfill.py)

from neckline.calendar import is_trading_day, reset_cache  # noqa: E402
from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.db import init_schema  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("daily_update")

LIMIT_DERIVED_TRAILING_DAYS = 15  # 尾部重算窗口(交易日),覆盖连板计数跨批次边界


def main() -> int:
    if not settings.tushare_token:
        logger.error("TUSHARE_TOKEN 缺失(.env),无法拉取。")
        return 1

    ensure_data_dirs()
    init_schema()
    reset_cache()

    target = datetime.strptime(sys.argv[1], "%Y%m%d").date() if len(sys.argv) > 1 else date.today()

    if not is_trading_day(target):
        logger.error("%s 不是交易日,无需更新。若日历本身过期,先跑 scripts/init_calendar.py 补数据。", target)
        return 1

    logger.info("增量更新交易日:%s", target)

    backfill.bootstrap_metadata()

    stats = backfill.backfill_day_tables([target], backfill.DAY_TABLES, force=True)
    for table, s in stats.items():
        logger.info("[%s] 新拉 %d 天(%d 行)、失败 %d 天", table, s["fetched"], s["rows"], s["failed"])

    backfill.backfill_index_daily(target, target)

    from neckline.calendar import trading_days_between

    all_days = trading_days_between(date(target.year - 1, 1, 1), target)
    window_start = all_days[-LIMIT_DERIVED_TRAILING_DAYS] if len(all_days) >= LIMIT_DERIVED_TRAILING_DAYS else all_days[0]
    backfill.run_limit_derived(window_start, target)

    logger.info("增量更新完成:%s", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
