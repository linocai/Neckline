#!/usr/bin/env python3
"""事实包回填(PROJECT_PLAN §5.3.5)。按日重跑 `build + freeze`,`origin='backfill'`。

**为什么需要**:策略层要读历史包(形态 3 的长窗),上线首日不能等 120 天。
生产回填量 = `MAX_LOOKBACK_PACKS` + 余量(建议 150 个交易日)。

⚠ **已知语义差,写在明处(§12 坑 12)**:
回填包用的是**今天的** `stock_basic` / 申万归属快照,**不是那天的**。
`index_member_all` 只给当前归属,历史归属要逐 L1 拉 31 次(Backlog §13,生产不需要
—— 成绩线在写入时冻结 `sw_l2_code`)。
⛔ **别写「自动检测行业变更并回改历史」的机灵代码**;要重置就整段重跑。

**冻结不可覆盖**(§5.3.2 纪律 3):已冻结过的日子默认**跳过**(幂等)。真要重来,
先手工删掉那些 `fact_packs` 行与对应 parquet —— 那是一次自觉行为,不给它开关。

用法:
    python scripts/bootstrap_fact_packs.py --start 20260101 --end 20260724
    python scripts/bootstrap_fact_packs.py --start 20260101 --end 20260724 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.calendar import reset_cache, trading_days_between  # noqa: E402
from neckline.config import ensure_data_dirs  # noqa: E402
from neckline.db import init_schema  # noqa: E402
from neckline.facts import pack as fact_pack  # noqa: E402
from neckline.facts import store as fact_store  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bootstrap_fact_packs")


def _day(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def run(start: date, end: date, *, dry_run: bool = False) -> int:
    days = trading_days_between(start, end)
    if not days:
        logger.error("%s~%s 之间没有交易日(或交易日历未覆盖该区间)", start, end)
        return 1

    frozen = skipped = incomplete = failed = 0
    t0 = time.monotonic()
    for d in days:
        try:
            built = fact_pack.build(d)
        except Exception:  # noqa: BLE001
            failed += 1
            logger.error("[%s] 构建异常", d, exc_info=True)
            continue
        if isinstance(built, fact_pack.IncompletePack):
            incomplete += 1
            logger.warning("[%s] 数据未到齐,不冻结:%s", d, built.describe())
            continue
        if dry_run:
            logger.info("[%s] (dry-run)可冻结:%d 行、%d 个二级行业",
                        d, built.row_count, len(built.industry_rows))
            continue
        try:
            fp = fact_store.freeze_pack(built, origin=fact_store.ORIGIN_BACKFILL)
        except fact_store.PackAlreadyFrozen:
            skipped += 1
            logger.info("[%s] 已冻结过,跳过", d)
            continue
        frozen += 1
        logger.info("[%s] 冻结 %d 行,sha256=%s…", d, fp.row_count, fp.content_fingerprint[:12])

    wall = time.monotonic() - t0
    logger.info(
        "回填完毕:%d 个交易日 → 冻结 %d / 跳过 %d / 数据未到齐 %d / 异常 %d,墙钟 %.1fs",
        len(days), frozen, skipped, incomplete, failed, wall,
    )
    return 0 if failed == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="事实包回填(PROJECT_PLAN §5.3.5)")
    ap.add_argument("--start", required=True, help="起始交易日 YYYYMMDD")
    ap.add_argument("--end", required=True, help="结束交易日 YYYYMMDD(含)")
    ap.add_argument("--dry-run", action="store_true", help="只构建不冻结,用于实测墙钟 / 峰值内存")
    args = ap.parse_args()

    ensure_data_dirs()
    init_schema()
    reset_cache()
    return run(_day(args.start), _day(args.end), dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
