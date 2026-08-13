#!/usr/bin/env python3
"""篮子 D+1 验证的 **EOD 那一拍** CLI(plan §五 V2-⑧-C / ⑧-C2)。

盘中那一拍由哨兵 `run_tick` 自动跑(旁路,无需人管);**收盘定论那一拍**照 ⑧-C2 第 4 条
必须每天落一行,它吃的是当日真实收盘价(EOD `daily` 面板,16:05 日更之后才有)。

**谁在生产上调它(⚠ Plan 未定死,如实登记)**:⑭-A 的 16:35 报告链(`report/pipeline.py`)
会在 ⑨ 复盘之前调 `basket_verify.run_eod_verification(...)`;⑭ 落地之前,用本脚本手动
或挂 timer 驱动。**⑧ 不擅自改 `daily_update.py` 的编排**(那是 ⑯-D 的分段职责)。

用法::

    python scripts/basket_verify.py                      # 今天(= D+1)
    python scripts/basket_verify.py --date 20260725
    python scripts/basket_verify.py --from 20260721 --to 20260725
    python scripts/basket_verify.py show --date 20260725     # 只看当前状态,不写库

**只写 `basket_verification` 一张表,append-only**;不推送、不碰持仓、不改任何纪律
判定(篮子被证伪 ≠ 持仓该走,两回事)。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.calendar import trading_days_between  # noqa: E402
from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.selection.basket_store import load_baskets_for_date  # noqa: E402
from neckline.sentinel import basket_verify  # noqa: E402
from neckline.sentinel import basket_verify_store as bvs  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("basket_verify")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def resolve_days(args: argparse.Namespace) -> List[date]:
    if args.date_from or args.date_to:
        lo = _parse_date(args.date_from) if args.date_from else _parse_date(args.date_to)
        hi = _parse_date(args.date_to) if args.date_to else _parse_date(args.date_from)
        return trading_days_between(lo, hi)
    if args.date:
        return [_parse_date(args.date)]
    return [date.today()]


def _show(day: date, db_path: Optional[Path]) -> int:
    from neckline.calendar import prev_trading_day

    d0 = prev_trading_day(day)
    refs = load_baskets_for_date(d0, db_path=db_path)
    if not refs:
        logger.info("%s(D0=%s)无已落库篮子,无可展示状态。", day, d0)
        return 0
    for ref in refs:
        cur = bvs.current_state(ref.basket_id, day, db_path=db_path)
        rows = bvs.list_rows(ref.basket_id, day, db_path=db_path)
        logger.info(
            "T%d %s(id=%d):当前 **%s**(%s;流水 %d 行:%s)",
            ref.tier, ref.name, ref.basket_id, cur.state, cur.label, len(rows),
            " → ".join(f"{r.source}:{r.state}" for r in rows) or "—",
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cmd", nargs="?", default="run", choices=["run", "show"])
    parser.add_argument("--date", help="被判定的那个交易日 YYYYMMDD(= D+1,缺省今天)")
    parser.add_argument("--from", dest="date_from", help="区间起 YYYYMMDD(补算用)")
    parser.add_argument("--to", dest="date_to", help="区间止 YYYYMMDD")
    parser.add_argument("--db", dest="db", help="SQLite 路径(缺省 settings.db_path)")
    parser.add_argument("--parquet-dir", dest="parquet_dir", help="parquet 根目录(缺省 settings)")
    args = parser.parse_args()

    ensure_data_dirs()
    db_path = Path(args.db) if args.db else settings.db_path
    parquet_dir = Path(args.parquet_dir) if args.parquet_dir else None

    days = resolve_days(args)
    if args.cmd == "show":
        return _show(days[-1], db_path)

    rc = 0
    for day in days:
        res = basket_verify.run_eod_verification(
            day, db_path=db_path, parquet_dir=parquet_dir)
        if res.evaluated == 0:
            logger.info("%s(D0=%s):无篮子可判(引擎未跑 / 今日无定档篮子),不落行。",
                        day, res.d0)
            continue
        counts: dict = {}
        for state in res.states.values():
            counts[state] = counts.get(state, 0) + 1
        logger.info(
            "%s(D0=%s):判定 %d 篮 → %s;落 %d 行(其中 %d 行是已定格 falsified 的定论行)",
            day, res.d0, res.evaluated,
            ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
            res.rows_written, res.skipped_latched,
        )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
