#!/usr/bin/env python3
"""偏好画像 / 能力画像 CLI(plan §五 V2-⑫-B)。批算落表(EOD/周度),在线只读——
本脚本是**唯一**的批算驱动入口,⑭ 落地前手动或挂 timer 驱动(同 ⑨
`scripts/basket_review.py` 的既定分工)。

用法::

    python scripts/profile.py run                                # 今天为 as_of,回看 90 天
    python scripts/profile.py run --as-of 20260803 --window-days 30
    python scripts/profile.py run --from 20260501 --to 20260803   # 显式区间
    python scripts/profile.py show --as-of 20260803               # 只看已落库的画像,不重算
    python scripts/profile.py run --dry-run                       # 只算不落库

只写 `profile_preference` / `profile_capability` 两张表(每期一版,`as_of_date`
下重算即覆盖);不推送、不碰持仓/篮子表(全程只读)、不改任何纪律或 Tier 判定。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.profile import capability as cap  # noqa: E402
from neckline.profile import preference as pref  # noqa: E402
from neckline.profile import store as profile_store  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("profile")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def resolve_window(args: argparse.Namespace) -> Tuple[str, str, str]:
    """返回 `(window_start, window_end, as_of_date)`,全部 'YYYYMMDD'。"""
    if args.date_from or args.date_to:
        lo = _parse_date(args.date_from) if args.date_from else _parse_date(args.date_to)
        hi = _parse_date(args.date_to) if args.date_to else date.today()
        as_of = _parse_date(args.as_of) if args.as_of else hi
        return lo.strftime("%Y%m%d"), hi.strftime("%Y%m%d"), as_of.strftime("%Y%m%d")
    as_of = _parse_date(args.as_of) if args.as_of else date.today()
    lo = as_of - timedelta(days=args.window_days)
    return lo.strftime("%Y%m%d"), as_of.strftime("%Y%m%d"), as_of.strftime("%Y%m%d")


def _show(as_of: str, db_path: Optional[Path]) -> int:
    pref_rows = profile_store.load_preference(as_of, db_path=db_path)
    cap_rows = profile_store.load_capability(as_of, db_path=db_path)
    if not pref_rows and not cap_rows:
        logger.info("%s 没有已落库的画像。", as_of)
        return 0
    logger.info("=== 偏好画像(as_of=%s)===", as_of)
    for r in pref_rows:
        logger.info("  %-14s %-16s 占比 %5.1f%%  N=%-3d  置信度 %s",
                    r["dimension"], r["value"], r["share"] * 100, r["sampleN"], r["confidence"])
    logger.info("=== 能力画像(as_of=%s)===", as_of)
    for r in cap_rows:
        win = "—" if r["winRate"] is None else f"{r['winRate']:.0%}"
        delta = "—" if r["vsPeerDelta"] is None else f"{r['vsPeerDelta']:+.1%}"
        logger.info("  %-14s %-16s N=%-3d 胜率 %-6s vs同篮未选 %-8s 置信度 %s",
                    r["dimension"], r["value"], r["sampleN"], win, delta, r["confidence"])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cmd", nargs="?", default="run", choices=["run", "show"])
    parser.add_argument("--as-of", dest="as_of", help="画像落库标签 YYYYMMDD(缺省 = 区间末/今天)")
    parser.add_argument("--window-days", type=int, default=90, help="回看天数(缺省 90,`--from`/`--to` 优先)")
    parser.add_argument("--from", dest="date_from", help="区间起 YYYYMMDD")
    parser.add_argument("--to", dest="date_to", help="区间止 YYYYMMDD")
    parser.add_argument("--dry-run", action="store_true", help="只算不落库")
    parser.add_argument("--db", dest="db", help="SQLite 路径(缺省 settings.db_path)")
    parser.add_argument("--parquet-dir", dest="parquet_dir", help="parquet 根目录(缺省 settings)")
    args = parser.parse_args()

    ensure_data_dirs()
    db_path = Path(args.db) if args.db else settings.db_path
    parquet_dir = Path(args.parquet_dir) if args.parquet_dir else None

    window_start, window_end, as_of = resolve_window(args)
    if args.cmd == "show":
        return _show(as_of, db_path)

    logger.info("窗口 [%s, %s] → as_of=%s", window_start, window_end, as_of)

    pref_rows = pref.compute_preference(window_start, window_end, db_path=db_path)
    cap_rows = cap.compute_capability(window_start, window_end, db_path=db_path, parquet_dir=parquet_dir)

    if not pref_rows and not cap_rows:
        logger.info("窗口内无买入数据,两张画像本期均为空(如实反映,不是异常)。")
        return 0

    logger.info("偏好画像 %d 行(维度 %s)", len(pref_rows), sorted({r.dimension for r in pref_rows}))
    logger.info("能力画像 %d 行(维度 %s)", len(cap_rows), sorted({r.dimension for r in cap_rows}))
    for r in cap_rows:
        if r.confidence != "low":
            logger.info("  · %s=%s:%s", r.dimension, r.value, r.verdict)

    if args.dry_run:
        logger.info("(--dry-run,未落库)")
        return 0

    n1 = profile_store.save_preference(as_of, pref_rows, db_path=db_path)
    n2 = profile_store.save_capability(as_of, cap_rows, db_path=db_path)
    logger.info("落库:profile_preference +%d 行 / profile_capability +%d 行(as_of=%s)", n1, n2, as_of)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
