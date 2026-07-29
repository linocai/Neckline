#!/usr/bin/env python3
"""行业强度预计算表 `industry_strength_daily` 的修补 / 自检 / 回填 CLI(plan §五 v1.4-⑩,
§七 **P0-23**)。放 `scripts/` **顶层**(不是 `oneoff/`)—— 它是长期修补 / 自检工具,同
`positions.py` 子命令体例。

正常日子**不需要跑本脚本**:16:05 `scripts/daily_update.py` 已挂 `update_industry_strength`
自动增量。本脚本用于:①日更漏跑 / 报错后补算;②口径常量改动后整表重算;③一次性历史
回填(bootstrap);④出问题时的三项自检。

用法:
    # 补算(缺省 = 今天;补历史日时自动向后延到库内最大交易日,不会留下失真的 streak)
    python scripts/industry_strength.py refresh
    python scripts/industry_strength.py refresh --from 20260720 --to 20260728
    python scripts/industry_strength.py refresh --year 2026          # 该年全部交易日走逐日路径

    # 自检(三项:交易日无洞 / streak 自洽 / 口径指纹一致);有问题 exit 1 并逐条打印
    python scripts/industry_strength.py verify
    python scripts/industry_strength.py verify --from 20200102 --to 20260728

    # 历史回填(两遍法;**生产机上一律逐年串行 + systemd-run --scope 隔离**,见下)
    python scripts/industry_strength.py bootstrap --year 2020 --pass1-only
    python scripts/industry_strength.py bootstrap --pass2-only
    python scripts/industry_strength.py bootstrap                     # 全年份 Pass1 + Pass2(本地/小库)
    python scripts/industry_strength.py bootstrap --recent-days 250 --end 20260728   # ⑩-D 退路

**🔴 生产机跑法(探针纪律,2026-07-29 立规,逐条守)**:只在**收盘后 15:00 之后**且
**避开 16:00–17:00**(16:05 日更 + 16:35 报告窗口);一律
`systemd-run --scope -p MemoryMax=600M -p CPUQuota=100%` 隔离**单进程、串行**,别并行开
多个、更别拿常驻 `neckline.service` 当小白鼠;每块之间看一次 `free -m` 与 `load`;
**`load > 4` 立即停手**。跑前 `sqlite3 .backup` + `cp -p` **双备份**(建表 + 批量写 =
迁移级动作);跑后 `PRAGMA integrity_check` + 业务表行数逐表比对。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import ensure_data_dirs  # noqa: E402
from neckline.report.industry_strength_store import (  # noqa: E402
    available_years,
    bootstrap_pass1_year,
    bootstrap_pass2_streak,
    bootstrap_recent_days,
    industry_strength_status,
    refresh_industry_strength,
    verify_industry_strength,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("industry_strength")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def _resolve_days(args: argparse.Namespace) -> list:
    """`--from/--to` / `--year` / 缺省(今天)→ 交易日列表(升序)。交易日历是唯一源
    (`neckline.calendar`),不在本脚本里自造工作日近似。"""
    from neckline.calendar import trading_days_between

    if args.year:
        return trading_days_between(date(args.year, 1, 1), date(args.year, 12, 31))
    if args.date_from or args.date_to:
        lo = _parse_date(args.date_from) if args.date_from else _parse_date(args.date_to)
        hi = _parse_date(args.date_to) if args.date_to else _parse_date(args.date_from)
        return trading_days_between(lo, hi)
    return [date.today()]


def cmd_refresh(args: argparse.Namespace) -> int:
    days = _resolve_days(args)
    if not days:
        logger.error("解析不出任何交易日(检查 --from/--to/--year 与 trade_cal 覆盖范围)")
        return 1
    stats = refresh_industry_strength(days)
    logger.info(
        "行业强度补算完成:处理 %d 个交易日、落 %d 行、缺分区 %d 天(请求区间 %s ~ %s)",
        stats["days"], stats["rows"], stats["missing"],
        days[0].strftime("%Y%m%d"), days[-1].strftime("%Y%m%d"),
    )
    fresh = industry_strength_status(days[-1])
    logger.info("表内最新至 %s(相对 %s 落后 %d 个交易日)",
                fresh.latest_label(), days[-1].strftime("%Y%m%d"), fresh.lag_days)
    # v1.4 review 🟡-2:补完仍有断口 → **exit 1**(store 侧已打 ERROR 带补算命令)。
    # 「跑过了」不等于「补齐了」:静默 exit 0 会让 systemd 的 Result=success 骗人。
    if stats.get("holes"):
        logger.error("补算后仍有 %d 个交易日断口 —— 退出码 1(明细见上一条 ERROR)。",
                     len(stats["holes"]))
        return 1
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    lo = _parse_date(args.date_from) if args.date_from else None
    hi = _parse_date(args.date_to) if args.date_to else None
    res = verify_industry_strength(lo, hi)
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
    for m in res["streak_mismatches"][:20]:
        logger.error("② streak 不自洽:%s %s 库内=%s 应为=%s",
                     m["trade_date"], m["industry"], m["stored"], m["expected"])
    if len(res["streak_mismatches"]) > 20:
        logger.error("② 另有 %d 条 streak 不自洽未展开", len(res["streak_mismatches"]) - 20)
    if len(res["fingerprints"]) != 1 or res["bad_fingerprints"]:
        logger.error("③ 口径指纹不一致 / 与现行常量不符:%s —— 请重跑 bootstrap 整表重算",
                     json.dumps(res["fingerprints"], ensure_ascii=False))
    if res["ok"]:
        logger.info("✅ 三项自检全绿(交易日无洞 / streak 自洽 / 口径指纹一致)")
        return 0
    return 1


def cmd_bootstrap(args: argparse.Namespace) -> int:
    if args.recent_days:
        end = _parse_date(args.end) if args.end else date.today()
        stats = bootstrap_recent_days(end, args.recent_days)
        logger.warning(
            "⑩-D 退路生效:只回填最近 %d 个交易日(截至 %s)—— 处理 %d 天 / %d 行 / 缺分区 %d 天。"
            "**早于起点的历史回放会走保险丝降级(表缺行),必须记进 §九 + ~/hz_info.md + §七 挂账**",
            args.recent_days, end.strftime("%Y%m%d"), stats["days"], stats["rows"], stats["missing"],
        )
        return 0

    years = [args.year] if args.year else available_years()
    if not years:
        logger.error("`daily` 表下无任何 year=YYYY 分区,无从回填")
        return 1
    if not args.pass2_only:
        for y in years:
            t0 = datetime.now()
            s = bootstrap_pass1_year(y)
            logger.info("Pass1 year=%d:%d 个交易日 / %d 行,耗时 %.1fs",
                        y, s["days"], s["rows"], (datetime.now() - t0).total_seconds())
    if not args.pass1_only:
        t0 = datetime.now()
        s2 = bootstrap_pass2_streak()
        logger.info("Pass2(纯表内 streak 递推):读回 %d 行、回写 %d 行,耗时 %.1fs",
                    s2["rows"], s2["updated"], (datetime.now() - t0).total_seconds())
    else:
        logger.warning("只跑了 Pass1 —— `persist_days` 仍为 NULL,**必须**再跑一次 "
                       "`python scripts/industry_strength.py bootstrap --pass2-only` 才算完")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="行业强度预计算表(industry_strength_daily)修补/自检/回填")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("refresh", help="按交易日补算并 upsert 落表(缺省=今天)")
    r.add_argument("--from", dest="date_from", help="起始交易日 YYYYMMDD")
    r.add_argument("--to", dest="date_to", help="结束交易日 YYYYMMDD")
    r.add_argument("--year", type=int, help="整年(走逐日路径,内存恒定但比 bootstrap 慢)")
    r.set_defaults(func=cmd_refresh)

    v = sub.add_parser("verify", help="三项自检(交易日无洞 / streak 自洽 / 口径指纹一致)")
    v.add_argument("--from", dest="date_from", help="起始交易日 YYYYMMDD(缺省=表内最早)")
    v.add_argument("--to", dest="date_to", help="结束交易日 YYYYMMDD(缺省=表内最晚)")
    v.set_defaults(func=cmd_verify)

    b = sub.add_parser("bootstrap", help="历史回填两遍法(Pass1 按年当日量 → Pass2 纯表内 streak)")
    b.add_argument("--year", type=int, help="只跑该年 Pass1(生产机逐年串行用)")
    b.add_argument("--pass1-only", action="store_true", help="只跑 Pass1(persist_days 留 NULL)")
    b.add_argument("--pass2-only", action="store_true", help="只跑 Pass2(纯表内,不碰 parquet)")
    b.add_argument("--recent-days", type=int, help="⑩-D 退路:只回填最近 N 个交易日(逐日路径)")
    b.add_argument("--end", help="退路模式的截止交易日 YYYYMMDD(缺省=今天)")
    b.set_defaults(func=cmd_bootstrap)
    return p


def main() -> int:
    ensure_data_dirs()
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
