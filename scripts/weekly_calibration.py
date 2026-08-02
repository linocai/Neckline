#!/usr/bin/env python3
"""周度校准报告 CLI(plan §五 V2-⑨-C / ⑨-C2)。把一周(或任意区间)的篮子成绩单
按 `pack_version × verification_ruleset_version` 分层算出来,连同两条安慰剂对照臂,
落成 `.md` + `.json` 两份文件。

**边界**:⛔ **不接报告管线**(那是 ⑭ 的活),⛔ 不写任何表。产出**只进周复盘工作台
与策略线迭代输入**,不进任何在线判据 —— 改权重一律走换包。

用法::

    python scripts/weekly_calibration.py                      # 含今天的那一周
    python scripts/weekly_calibration.py --week 20260724      # 含该日的那一周
    python scripts/weekly_calibration.py --from 20260701 --to 20260731
    python scripts/weekly_calibration.py --no-placebo         # 只出分层成绩单(快)
    python scripts/weekly_calibration.py --draws 50           # 对照臂抽样次数(默认 200)

⚠ **性能**:`--draws` × 每日成员数 次判分,外加一次前复权面板装配。P0-23 纪律:
**上生产机前必须在新机上单独计时 + 量峰值**(⑯-C),别拿常驻服务当小白鼠。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.eval import calibration  # noqa: E402
from neckline.eval.placebo import PLACEBO_DRAWS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("weekly_calibration")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--week", help="含该日的那一周 YYYYMMDD(缺省今天)")
    parser.add_argument("--from", dest="date_from", help="区间起 YYYYMMDD(按 D0 取)")
    parser.add_argument("--to", dest="date_to", help="区间止 YYYYMMDD")
    parser.add_argument("--out", help="输出目录(缺省 data/reports/calibration)")
    parser.add_argument("--draws", type=int, default=PLACEBO_DRAWS, help="对照臂每日抽样次数")
    parser.add_argument("--no-placebo", action="store_true", help="跳过安慰剂对照臂")
    parser.add_argument("--no-tradable", action="store_true", help="跳过可交易收益判分")
    parser.add_argument("--print", dest="do_print", action="store_true", help="同时打印 markdown")
    parser.add_argument("--db", dest="db", help="SQLite 路径(缺省 settings.db_path)")
    parser.add_argument("--parquet-dir", dest="parquet_dir", help="parquet 根目录(缺省 settings)")
    args = parser.parse_args()

    ensure_data_dirs()
    db_path = Path(args.db) if args.db else settings.db_path
    parquet_dir = Path(args.parquet_dir) if args.parquet_dir else None

    if args.date_from or args.date_to:
        lo = _parse_date(args.date_from) if args.date_from else _parse_date(args.date_to)
        hi = _parse_date(args.date_to) if args.date_to else _parse_date(args.date_from)
    else:
        anchor = _parse_date(args.week) if args.week else date.today()
        lo, hi = calibration.week_bounds(anchor)
        if lo is None:
            logger.error("%s 所在那一周没有交易日。", anchor)
            return 2

    report = calibration.build_report(
        lo, hi, db_path=db_path, parquet_dir=parquet_dir,
        with_tradable=not args.no_tradable, with_placebo=not args.no_placebo,
        draws=int(args.draws),
    )
    out_dir = Path(args.out) if args.out else (settings.data_dir / "reports" / "calibration")
    paths = calibration.write_report(report, out_dir)
    logger.info("周度校准报告已生成:%s(%d 个交易日 / %d 个篮子 / %d 层)",
                paths["markdown"], report.n_trading_days, report.n_baskets, len(report.strata))
    for n in report.notes:
        logger.warning("  ⚠ %s", n)
    if args.do_print:
        print(calibration.render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
