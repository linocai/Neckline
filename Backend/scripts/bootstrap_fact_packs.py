#!/usr/bin/env python3
"""事实包回填(PROJECT_PLAN §5.3.5)。按日重跑 `build + freeze`,`origin='backfill'`。

**为什么需要**:策略层要读历史包(形态 3 的长窗),上线首日不能等 120 天。
生产回填量 = `MAX_LOOKBACK_PACKS` + 余量(建议 150 个交易日)。

历史 fp-4 的 SW2021 L2 成员必须已通过
``scripts/import_sw_industry_history.py`` 导入为目标日完整快照；没有可靠快照的
日期会明确 incomplete 并以非零退出，绝不使用今天的成员归属回填历史。

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
from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.db import init_schema  # noqa: E402
from neckline.facts import v4 as fact_pack  # noqa: E402
from neckline.facts.pack import IncompletePack  # noqa: E402
from neckline.facts import store as fact_store  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bootstrap_fact_packs")


def _day(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def run(start: date, end: date, *, dry_run: bool = False, db_path: Path | None = None,
        parquet_dir: Path | None = None) -> int:
    try:
        days = trading_days_between(start, end, db_path=db_path if db_path is not None else settings.db_path)
    except RuntimeError as exc:
        logger.error("交易日历未就绪:%s", exc)
        return 1
    if not days:
        logger.error("%s~%s 之间没有交易日(或交易日历未覆盖该区间)", start, end)
        return 1

    frozen = skipped = incomplete = failed = 0
    covered: set[date] = set()
    t0 = time.monotonic()
    for d in days:
        try:
            built = fact_pack.build(d, db_path=db_path, parquet_dir=parquet_dir)
        except Exception:  # noqa: BLE001
            failed += 1
            logger.error("[%s] 构建异常", d, exc_info=True)
            continue
        if isinstance(built, IncompletePack):
            incomplete += 1
            logger.warning("[%s] 数据未到齐,不冻结:%s", d, built.describe())
            continue
        if dry_run:
            logger.info("[%s] (dry-run)可冻结:%d 行、%d 个二级行业",
                        d, built.row_count, len(built.industry_rows))
            continue
        try:
            fp = fact_store.freeze_pack(built, origin=fact_store.ORIGIN_BACKFILL,
                                        db_path=db_path, parquet_dir=parquet_dir)
        except fact_store.PackAlreadyFrozen:
            try:
                fact_store.load_pack(d, pack_version=fact_pack.PACK_VERSION,
                                     db_path=db_path, parquet_dir=parquet_dir)
            except Exception:  # noqa: BLE001
                failed += 1
                logger.error("[%s] 已冻结记录不可读取，不能计为回填覆盖", d, exc_info=True)
                continue
            skipped += 1; covered.add(d)
            logger.info("[%s] 已冻结过且完整,跳过", d)
            continue
        frozen += 1; covered.add(d)
        logger.info("[%s] 冻结 %d 行,sha256=%s…", d, fp.row_count, fp.content_fingerprint[:12])

    wall = time.monotonic() - t0
    logger.info(
        "回填完毕:%d 个交易日 → 冻结 %d / 跳过 %d / 数据未到齐 %d / 异常 %d,墙钟 %.1fs",
        len(days), frozen, skipped, incomplete, failed, wall,
    )
    # A requested backfill is complete only when every requested trading day
    # is either newly frozen or already has the immutable fp-4.  Incomplete
    # data is not a successful no-op.
    missing = [] if dry_run else sorted(set(days) - covered)
    if missing:
        logger.error("请求 fp-4 未覆盖交易日:%s", ",".join(d.strftime("%Y%m%d") for d in missing))
    return 0 if incomplete == 0 and failed == 0 and not missing else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="事实包回填(PROJECT_PLAN §5.3.5)")
    ap.add_argument("--start", required=True, help="起始交易日 YYYYMMDD")
    ap.add_argument("--end", required=True, help="结束交易日 YYYYMMDD(含)")
    ap.add_argument("--dry-run", action="store_true", help="只构建不冻结,用于实测墙钟 / 峰值内存")
    ap.add_argument("--db", default=None, help="目标 SQLite（交易日历与冻结清单同库）")
    ap.add_argument("--parquet-dir", default=None, help="目标 parquet 根目录")
    args = ap.parse_args()

    ensure_data_dirs()
    db_path = Path(args.db) if args.db else None
    parquet_dir = Path(args.parquet_dir) if args.parquet_dir else None
    init_schema(db_path)
    reset_cache()
    return run(_day(args.start), _day(args.end), dry_run=args.dry_run,
               db_path=db_path, parquet_dir=parquet_dir)


if __name__ == "__main__":
    raise SystemExit(main())
