#!/usr/bin/env python3
"""行业题材阶段六态状态机表 `industry_stage_daily` 的日更 / 自检 / 回填 CLI(plan
§五 V2-④b,K7 需求 1b,§七 P0-23)。体例照 `scripts/scan_layer.py`(同一批"无跨日
无界递推状态的 EOD 预计算表"CLI 族;`refresh`/`bootstrap` 共用同一实现,不做
`scripts/industry_strength.py` 那种两遍法——理由见 `neckline/scan/stage.py` 模块
docstring「两遍法的如实 departure」)。

正常日子**不需要跑本脚本**:`scripts/daily_update.py` 已挂 `update_industry_stage`
自动增量(排在 `update_industry_strength` 之后,依赖它的 `persist_days`)。本脚本
用于:①日更漏跑 / 报错后补算;②口径常量改动后重算区间;③一次性历史回填
(bootstrap);④出问题时的三项自检。

用法:
    python scripts/industry_stage.py refresh                       # 今天
    python scripts/industry_stage.py refresh --from 20260720 --to 20260724
    python scripts/industry_stage.py verify                        # 三项自检(缺省=表内全部区间)
    python scripts/industry_stage.py verify --from 20260101 --to 20260731
    python scripts/industry_stage.py bootstrap --year 2026          # 该年全部交易日
    python scripts/industry_stage.py bootstrap --from 20260101 --to 20260731
    python scripts/industry_stage.py refresh --db /path.db --parquet-dir /path/parquet

**依赖**:`industry_strength_daily` 当日行必须已就绪(先跑
`scripts/industry_strength.py refresh` 或 `scripts/daily_update.py`)。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import ensure_data_dirs, settings  # noqa: E402
from neckline.scan import stage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("industry_stage")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def resolve_days(args: argparse.Namespace) -> List[date]:
    """`--from/--to` / `--year` / 缺省(今天)→ 交易日列表(升序)。交易日历唯一源
    = `neckline.calendar`,同 `scripts/scan_layer.py::resolve_days` 体例。"""
    from neckline.calendar import trading_days_between

    if getattr(args, "year", None):
        return trading_days_between(date(args.year, 1, 1), date(args.year, 12, 31))
    if args.date_from or args.date_to:
        lo = _parse_date(args.date_from) if args.date_from else _parse_date(args.date_to)
        hi = _parse_date(args.date_to) if args.date_to else _parse_date(args.date_from)
        return trading_days_between(lo, hi)
    return [date.today()]


def run_batch(days: List[date], *, db_path: Optional[Path], parquet_dir: Optional[Path]) -> int:
    """`refresh`/`bootstrap` 共用本函数(同一实现,见模块 docstring)。"""
    t0 = datetime.now()
    stats = stage.refresh_industry_stage(days, db_path=db_path, parquet_dir=parquet_dir)
    logger.info(
        "industry_stage_daily: %d 天 / %d 行(源表 industry_strength_daily 当日无行 %d 天)",
        stats["days"], stats["rows"], stats["missing_source"],
    )
    if stats["missing_source"]:
        logger.error(
            "%d 个交易日 industry_strength_daily 当日无任何行 —— 这些天 industry_stage_daily "
            "同样不落任何行(真缺行,不猜)。先补齐行业强度:python scripts/industry_strength.py "
            "refresh --from %s --to %s",
            stats["missing_source"], days[0].strftime("%Y%m%d"), days[-1].strftime("%Y%m%d"),
        )
    fresh = stage.industry_stage_status(days[-1], db_path=db_path)
    logger.info("表内最新至 %s(相对 %s 落后 %d 个交易日)",
                fresh.latest_label(), days[-1].strftime("%Y%m%d"), fresh.lag_days)
    logger.info("批算完成,耗时 %.1fs(%s ~ %s,共 %d 个交易日)",
                (datetime.now() - t0).total_seconds(), days[0].strftime("%Y%m%d"), days[-1].strftime("%Y%m%d"), len(days))
    return 1 if stats["missing_source"] == len(days) and stats["rows"] == 0 else 0


def cmd_refresh(args: argparse.Namespace) -> int:
    days = resolve_days(args)
    if not days:
        logger.error("解析不出任何交易日(检查 --from/--to/--year 与 trade_cal 覆盖范围)")
        return 1
    return run_batch(days, db_path=args.db, parquet_dir=args.parquet_dir)


def cmd_bootstrap(args: argparse.Namespace) -> int:
    days = resolve_days(args)
    if not days:
        logger.error("解析不出任何交易日(检查 --from/--to/--year 与 trade_cal 覆盖范围)")
        return 1
    logger.warning(
        "bootstrap 即将处理 %d 个交易日(%s ~ %s)——本表无跨日无界递推状态(固定 %d/%d 日"
        "回看窗口),与 refresh 同一实现、按升序逐日处理;生产机大区间回填前请先按项目 "
        "CLAUDE.md「生产机性能探针纪律」实测。",
        len(days), days[0].strftime("%Y%m%d"), days[-1].strftime("%Y%m%d"),
        stage.DIVERGENCE_LOOKBACK_DAYS, stage.EBB_LOOKBACK_DAYS,
    )
    return run_batch(days, db_path=args.db, parquet_dir=args.parquet_dir)


def cmd_verify(args: argparse.Namespace) -> int:
    lo = _parse_date(args.date_from) if args.date_from else None
    hi = _parse_date(args.date_to) if args.date_to else None
    res = stage.verify_industry_stage(lo, hi, db_path=args.db)
    if res.get("reason"):
        logger.error("自检不通过:%s", res["reason"])
        return 1
    logger.info("自检区间 %s ~ %s:%d 行 / %d 个交易日", *res["range"], res["rows"], res["days"])
    if res["missing_days"]:
        logger.error("① 交易日有洞:%d 天缺行 —— %s%s", len(res["missing_days"]),
                     ",".join(res["missing_days"][:20]),
                     "…" if len(res["missing_days"]) > 20 else "")
    if res["extra_days"]:
        logger.error("① 表内有非交易日的行:%s", ",".join(res["extra_days"][:20]))
    for msg in res["self_consistency_errors"][:50]:
        logger.error("② 五态判据不自洽:%s", msg)
    if len(res["self_consistency_errors"]) > 50:
        logger.error("② 另有 %d 条不自洽未展开", len(res["self_consistency_errors"]) - 50)
    if res["bad_fingerprints"]:
        logger.error("③ 口径指纹与现行常量不符:%s —— 请重跑 bootstrap 整表重算",
                     res["bad_fingerprints"])
    if res["ok"]:
        logger.info("✅ 三项自检全绿(交易日无洞 / 五态判据自洽 / 口径指纹一致)")
        return 0
    return 1


def _add_common_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--db", type=Path, default=None, help="目标 SQLite 库(默认 settings.db_path)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="行业题材阶段六态状态机表(industry_stage_daily)日更/自检/回填")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("refresh", help="按交易日批算并 upsert 落表(缺省=今天)")
    r.add_argument("--from", dest="date_from", help="起始交易日 YYYYMMDD")
    r.add_argument("--to", dest="date_to", help="结束交易日 YYYYMMDD")
    r.add_argument("--year", type=int, help="整年")
    r.add_argument("--parquet-dir", type=Path, default=None, help="Parquet 根目录(默认 settings.parquet_dir)")
    _add_common_args(r)
    r.set_defaults(func=cmd_refresh)

    v = sub.add_parser("verify", help="三项自检(交易日无洞 / 五态判据自洽 / 口径指纹一致)")
    v.add_argument("--from", dest="date_from", help="起始交易日 YYYYMMDD(缺省=表内最早)")
    v.add_argument("--to", dest="date_to", help="结束交易日 YYYYMMDD(缺省=表内最晚)")
    _add_common_args(v)
    v.set_defaults(func=cmd_verify)

    b = sub.add_parser("bootstrap", help="历史回填(与 refresh 同一实现,批量入口 + 更醒目的日志)")
    b.add_argument("--from", dest="date_from", help="起始交易日 YYYYMMDD")
    b.add_argument("--to", dest="date_to", help="结束交易日 YYYYMMDD")
    b.add_argument("--year", type=int, help="整年")
    b.add_argument("--parquet-dir", type=Path, default=None, help="Parquet 根目录(默认 settings.parquet_dir)")
    _add_common_args(b)
    b.set_defaults(func=cmd_bootstrap)
    return p


def main() -> int:
    ensure_data_dirs()
    args = build_parser().parse_args()
    if args.db is None:
        args.db = settings.db_path
    if getattr(args, "parquet_dir", None) is None:
        args.parquet_dir = settings.parquet_dir if hasattr(args, "parquet_dir") else None
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
