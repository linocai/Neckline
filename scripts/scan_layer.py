#!/usr/bin/env python3
"""市场扫描层(plan §五 V2-④,P0-23)三张预计算表(`corr_matrix_daily` /
`limit_cluster_daily` / `leader_structure_daily`)的日更 / 自检 / 回填 CLI。
体例照 `scripts/industry_strength.py`(同一批"EOD 预计算表"CLI 族);`--db`/
`--parquet-dir` 显式透传体例照 `scripts/activate_pack.py`(核心函数一律要求
调用方明确传 `db_path`/`parquet_dir`,不靠隐式模块级 `settings` 兜底——同
项目 CLAUDE.md「测试隔离」条纪律,防止单测在隔离环境下意外写穿真实开发库)。

正常日子**不需要跑本脚本**:16:35 晚间链的 `scan` 段(`report/evening.py`,systemd
`neckline-scan.service`)已挂 cluster/corr/leader + 驱动种子自动增量。⚠ **⑯-D 起挂载
点从 `scripts/daily_update.py` 换到了链里**(此前是双挂载,按 plan「摘掉 daily_update
那份、留链里的」二选一 —— 留在拉数进程里会让「⑤ 拿昨天的扫描表当今天用」这类故障
静默发生;`daily_update.update_scan_layer` 函数仍在、只是不再进 `main()`,留作手动
后门)。本脚本用于:①日更漏跑 / 报错后补算;②口径常量改动后重算区间;③一次性
历史回填(bootstrap);④出问题时的三项自检。

**批算顺序固定**:cluster → corr → leader(`corr.py` 读 `cluster.py` 当日
产出的簇成员做候选对;`leader.py` 读簇成员 + 复用 corr 的价格窗口),`refresh`/
`bootstrap` 均按此顺序调用,不可调换。

用法:
    python scripts/scan_layer.py refresh                       # 今天
    python scripts/scan_layer.py refresh --from 20260720 --to 20260724
    python scripts/scan_layer.py verify                        # 三项自检(缺省=表内全部区间)
    python scripts/scan_layer.py verify --from 20260101 --to 20260731
    python scripts/scan_layer.py bootstrap --year 2026          # 该年全部交易日
    python scripts/scan_layer.py bootstrap --from 20260101 --to 20260731
    python scripts/scan_layer.py regime                         # V2.2-② 行情状态三态(今天)
    python scripts/scan_layer.py regime --from 20260720 --to 20260807   # 区间回放
    python scripts/scan_layer.py landing                        # V2.2-③-C 落地起跳读数(今天)
    python scripts/scan_layer.py landing --from 20260720 --to 20260807  # 回填 + 逐日覆盖率回放
    python scripts/scan_layer.py refresh --db /path.db --parquet-dir /path/parquet
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
from neckline.scan import cluster, corr, leader, seeds, verify  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("scan_layer")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def resolve_days(args: argparse.Namespace) -> List[date]:
    """`--from/--to` / `--year` / 缺省(今天)→ 交易日列表(升序)。交易日历唯一源
    = `neckline.calendar`,同 `scripts/industry_strength.py::_resolve_days` 体例。"""
    from neckline.calendar import trading_days_between

    if getattr(args, "year", None):
        return trading_days_between(date(args.year, 1, 1), date(args.year, 12, 31))
    if args.date_from or args.date_to:
        lo = _parse_date(args.date_from) if args.date_from else _parse_date(args.date_to)
        hi = _parse_date(args.date_to) if args.date_to else _parse_date(args.date_from)
        return trading_days_between(lo, hi)
    return [date.today()]


def run_batch(days: List[date], *, db_path: Optional[Path], parquet_dir: Optional[Path]) -> int:
    """cluster → corr → leader 固定顺序批算 + 打印统计;末尾顺带跑一次种子
    生成只为记账(健康检查,**不落盘**——种子按需现算,见 `seeds.py` 模块头)。
    `refresh`/`bootstrap` 共用本函数,两者语义上是同一件事(见两个 cmd 函数)。"""
    t0 = datetime.now()
    c_stats = cluster.refresh_limit_clusters(days, db_path=db_path, parquet_dir=parquet_dir)
    r_stats = corr.refresh_corr_matrix(days, db_path=db_path, parquet_dir=parquet_dir)
    l_stats = leader.refresh_leader_structure(days, db_path=db_path, parquet_dir=parquet_dir)
    logger.info(
        "limit_cluster_daily: %d 天 / %d 行(同日簇 %d 个、连板簇 %d 个)",
        c_stats["days"], c_stats["rows"], c_stats["same_day_clusters"], c_stats["consecutive_clusters"],
    )
    logger.info("corr_matrix_daily: %d 天 / %d scope / %d 行", r_stats["days"], r_stats["scopes"], r_stats["rows"])
    logger.info("leader_structure_daily: %d 天 / %d 行", l_stats["days"], l_stats["rows"])

    last_day = days[-1]
    seed_set = seeds.generate_seeds(last_day, db_path=db_path, parquet_dir=parquet_dir)
    if seed_set is None:
        logger.warning(
            "[seeds] %s 无现役策略包 —— 今日不产出驱动种子(不影响三张事实表的批算结果,"
            "先跑 `python scripts/activate_pack.py --file packs/K8-skeleton.json --confirm`)",
            last_day,
        )
    else:
        counts = seed_set.counts()
        logger.info(
            "[seeds] %s 驱动种子:热点行业 %d / 暴起概念 %d / 涨停簇 %d / 异动簇 %d(pack=%s)",
            last_day, counts["hot_industry"], counts["surging_concept"],
            counts["limit_cluster"], counts["anomaly_cluster"], seed_set.pack_version,
        )
    logger.info("批算完成,耗时 %.1fs(%s ~ %s,共 %d 个交易日)",
                (datetime.now() - t0).total_seconds(), days[0].strftime("%Y%m%d"), days[-1].strftime("%Y%m%d"), len(days))
    return 0


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
        "bootstrap 即将处理 %d 个交易日(%s ~ %s)——三张表均按日窗口读取,无跨日递推状态,"
        "本地/小库直接跑;生产机大区间回填前请先按项目 CLAUDE.md「生产机性能探针纪律」实测。",
        len(days), days[0].strftime("%Y%m%d"), days[-1].strftime("%Y%m%d"),
    )
    return run_batch(days, db_path=args.db, parquet_dir=args.parquet_dir)


def cmd_verify(args: argparse.Namespace) -> int:
    lo = _parse_date(args.date_from) if args.date_from else None
    hi = _parse_date(args.date_to) if args.date_to else None
    res = verify.verify_scan_layer(lo, hi, db_path=args.db)
    if res.get("reason"):
        logger.error("自检不通过:%s", res["reason"])
        return 1
    logger.info("自检区间 %s ~ %s", *res["range"])
    for table, bad_days in res["non_trading_day_rows"].items():
        logger.error("① %s 出现 %d 个非交易日/越界日期的行:%s", table, len(bad_days), ",".join(bad_days[:20]))
    for msg in res["self_consistency_errors"][:50]:
        logger.error("② 键自洽:%s", msg)
    if len(res["self_consistency_errors"]) > 50:
        logger.error("② 另有 %d 条键自洽问题未展开", len(res["self_consistency_errors"]) - 50)
    for msg in res["fingerprint_mismatches"]:
        logger.error("③ 口径指纹:%s", msg)
    if res["ok"]:
        logger.info("✅ 三项自检全绿(交易日范围健全 / 键自洽 / 口径指纹一致)")
        return 0
    return 1


def cmd_regime(args: argparse.Namespace) -> int:
    """V2.2-② 行情状态层:批算 + 逐日打印三态/原因码/五维状态(回放即验收:任取
    区间跑一遍,每天都能说出三态之一 + 逐条原因码 + 五维各自「有 / 没取到」)。"""
    from neckline.scan import regime as regime_mod
    from neckline.scan import regime_store

    days = resolve_days(args)
    if not days:
        logger.error("解析不出任何交易日(检查 --from/--to/--year 与 trade_cal 覆盖范围)")
        return 1
    stats = regime_store.refresh_market_regime(days, db_path=args.db, parquet_dir=args.parquet_dir)
    logger.info(
        "market_regime_daily: 处理 %d 天 / 落 %d 行 / 失败 %d 天",
        stats["days"], stats["rows"], stats["failed"],
    )
    for d in days:
        row = regime_store.load_market_regime(d, db_path=args.db)
        if row is None:
            logger.warning("%s 无判定行(该日批算未产出,缺行 = 不知道,读侧按 available=false 披露)",
                           d.strftime("%Y%m%d"))
            continue
        dims = " ".join(
            f"{k}={'有' if (row['inputs'].get(k) or {}).get('available') else '没取到(' + str((row['inputs'].get(k) or {}).get('unavailable_reason', '?')) + ')'}"
            for k in regime_mod.DIM_ORDER
        )
        logger.info(
            "%s → %s(%s)| skeleton=%s\n    五维:%s\n    原因码:%s",
            row["trade_date"], row["regime"],
            regime_mod.REGIME_LABELS.get(row["regime"], row["regime"]),
            row["skeleton_version"], dims, row["regime_reason"],
        )
    return 0 if not stats["failed"] else 1


def cmd_landing(args: argparse.Namespace) -> int:
    """V2.2-③-C 落地起跳位置关(🔴 裁定 #11 后 = 机械只出读数,判定交给 LLM):
    批算落 `landing_metrics_daily` + 逐日打印读数覆盖率与缺项分布(回放即验收:
    任取区间跑一遍,每天说得出全市场判定行数 + 十四项读数各自的缺失分布;
    refresh 与 bootstrap 回填共用本命令——按 `--from/--to/--year` 给区间即回填,
    同 `refresh`/`bootstrap` 一体的既有语义)。⚠ 全市场逐票 × ~145 交易日回看,
    生产机大区间回填前先按项目 CLAUDE.md「生产机性能探针纪律」隔离实测
    (§七 P4-50,⛔ 不许跳过)。⛔ 不再打印四态分布——裁定 #11 后没有态了。"""
    from neckline.scan import landing_store

    days = resolve_days(args)
    if not days:
        logger.error("解析不出任何交易日(检查 --from/--to/--year 与 trade_cal 覆盖范围)")
        return 1
    t0 = datetime.now()
    stats = landing_store.refresh_landing_metrics(days, db_path=args.db, parquet_dir=args.parquet_dir)
    logger.info(
        "landing_metrics_daily: 处理 %d 天 / 落 %d 行 / 失败 %d 天(耗时 %.1fs)",
        stats["days"], stats["rows"], stats["failed"],
        (datetime.now() - t0).total_seconds(),
    )
    for d in days:
        cov = landing_store.landing_metrics_coverage(d, db_path=args.db)
        if not cov["total"]:
            logger.warning("%s 无判定行(该日批算未产出/当日无 daily 数据,缺行 = 不知道)",
                           d.strftime("%Y%m%d"))
            continue
        if cov["missing_counts"]:
            miss = " ".join(f"{k}缺{v}" for k, v in sorted(cov["missing_counts"].items()))
        else:
            miss = "(全员齐全)"
        logger.info("%s 共 %d 票有读数 | 缺项分布:%s", d.strftime("%Y%m%d"), cov["total"], miss)
    return 0 if not stats["failed"] else 1


def _add_common_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--db", type=Path, default=None, help="目标 SQLite 库(默认 settings.db_path)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="市场扫描层三张预计算表(corr/limit_cluster/leader_structure)日更/自检/回填")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("refresh", help="按交易日批算并 upsert 落表(缺省=今天)")
    r.add_argument("--from", dest="date_from", help="起始交易日 YYYYMMDD")
    r.add_argument("--to", dest="date_to", help="结束交易日 YYYYMMDD")
    r.add_argument("--year", type=int, help="整年")
    r.add_argument("--parquet-dir", type=Path, default=None, help="Parquet 根目录(默认 settings.parquet_dir)")
    _add_common_args(r)
    r.set_defaults(func=cmd_refresh)

    v = sub.add_parser("verify", help="三项自检(交易日范围健全 / 键自洽 / 口径指纹一致)")
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

    g = sub.add_parser("regime", help="V2.2-② 行情状态层三态:批算落 market_regime_daily + 逐日回放打印")
    g.add_argument("--from", dest="date_from", help="起始交易日 YYYYMMDD")
    g.add_argument("--to", dest="date_to", help="结束交易日 YYYYMMDD")
    g.add_argument("--year", type=int, help="整年")
    g.add_argument("--parquet-dir", type=Path, default=None, help="Parquet 根目录(默认 settings.parquet_dir)")
    _add_common_args(g)
    g.set_defaults(func=cmd_regime)

    ld = sub.add_parser(
        "landing",
        help="V2.2-③-C 落地起跳读数(裁定 #11 后零判定):批算落 landing_metrics_daily + 逐日覆盖率回放(区间即回填)",
    )
    ld.add_argument("--from", dest="date_from", help="起始交易日 YYYYMMDD")
    ld.add_argument("--to", dest="date_to", help="结束交易日 YYYYMMDD")
    ld.add_argument("--year", type=int, help="整年")
    ld.add_argument("--parquet-dir", type=Path, default=None, help="Parquet 根目录(默认 settings.parquet_dir)")
    _add_common_args(ld)
    ld.set_defaults(func=cmd_landing)
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
