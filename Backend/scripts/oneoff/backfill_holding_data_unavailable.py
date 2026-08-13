#!/usr/bin/env python3
"""一次性回填 `holding_eod_check.data_unavailable`(v1.4-①-B / §七 P0-2 部署补丁)。🔴 碰持仓判定。

**为什么需要**:`data_unavailable` 是 v1.4-①-B 新增的列 —— 建于该列之前的行读回来是
`NULL`(= 「当时没记这一位」)。而 **9:25:30 盘前的 `scan_time_exits` 正是靠这一位决定
「停牌票的 D5 提醒推不推」**(`report/holding_store.data_unavailable_provider`,查无 → 保守
返 False = 照推)。于是代码刚上云的那一天会出现一个缝:

    16:35 报告(新代码)会写对这一位 → 但**盘前那一拍先跑**,它读到的是**上一份**快照,
    也就是旧代码写的、这一位为 NULL 的那行 → 仍然会把「D5 时间退出」推给一只停牌票。

2026-07-28 v1.4.0-p1 小步上云时**实测踩到**(干跑 `scan_time_exits(2026-07-29)` 仍把
`002036.SZ` 列进推送清单)。本脚本把历史行的这一位按**可验证事实**补齐,把那个缝焊死。

**判据是推导出来的,不是拍的**:`data_unavailable = 1` **当且仅当**该持仓的 `ts_code`
在那个交易日的 `daily` 分区里**没有行**(= 当日无 EOD 行,与 `holding_k4_check` 的
`has_data` 判据同源)。**分区文件本身不存在** → **跳过该行、留 NULL**,绝不推导
——「不知道」不许冒充「知道」(§3.8)。

**幂等**:只碰 `data_unavailable IS NULL` 的行;已回填的行二次运行零改动。默认演练,
`--confirm` 才写。

用法:
    python scripts/oneoff/backfill_holding_data_unavailable.py \\
        --db /opt/neckline/data/neckline.db --parquet-dir /opt/neckline/data/parquet [--confirm]

单测见 `tests/test_backfill_holding_data_unavailable.py`。
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

logger = logging.getLogger("backfill_holding_data_unavailable")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


@dataclass
class BackfillReport:
    filled: List[Dict[str, str]] = field(default_factory=list)   # 真回填的行
    skipped_no_partition: List[Dict[str, str]] = field(default_factory=list)  # 无分区 → 留 NULL
    already_set: int = 0                                          # 已有值(幂等跳过)
    integrity: str = ""
    dry_run: bool = True


def _day_codes(parquet_dir: Path, trade_date: str) -> Optional[Set[str]]:
    """某交易日 `daily` 分区里的 `ts_code` 集合;分区不存在 / 读失败 / 空 → None(= 不可推导)。

    只读单个分区文件的 `ts_code` 一列(不整表 scan)—— 同 `data/price_stale.py::_day_codes`
    的理由:整表扫会被任何一个坏分区连坐,而这里只需要一天的成员名单。"""
    import polars as pl

    p = parquet_dir / "daily" / f"year={trade_date[:4]}" / f"{trade_date}.parquet"
    if not p.exists():
        return None
    try:
        codes = set(pl.read_parquet(p, columns=["ts_code"])["ts_code"].to_list())
    except Exception:  # noqa: BLE001
        logger.warning("读 daily 分区 %s 失败,该日不推导(留 NULL)", trade_date, exc_info=True)
        return None
    return codes or None


def backfill(db_path: Path, parquet_dir: Path, *, confirm: bool = False) -> BackfillReport:
    rep = BackfillReport(dry_run=not confirm)
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT h.position_id, h.trade_date, p.ts_code, h.data_unavailable, h.net_float "
            "FROM holding_eod_check h JOIN positions p ON p.id = h.position_id "
            "ORDER BY h.position_id, h.trade_date"
        ).fetchall()
        codes_cache: Dict[str, Optional[Set[str]]] = {}
        for pid, td, ts_code, cur, net_float in rows:
            if cur is not None:
                rep.already_set += 1
                continue
            if td not in codes_cache:
                codes_cache[td] = _day_codes(parquet_dir, td)
            present = codes_cache[td]
            if present is None:
                rep.skipped_no_partition.append({"position_id": str(pid), "trade_date": td, "ts_code": ts_code})
                logger.info("[skip] #%s %s@%s:该日 daily 分区不可读 → 留 NULL(不知道就别写)",
                            pid, ts_code, td)
                continue
            val = 0 if ts_code in present else 1
            conn.execute(
                "UPDATE holding_eod_check SET data_unavailable=? WHERE position_id=? AND trade_date=?",
                (val, pid, td),
            )
            rep.filled.append({"position_id": str(pid), "trade_date": td, "ts_code": ts_code,
                               "data_unavailable": str(val), "net_float": str(net_float)})
            logger.info("[fill] #%s %s@%s:当日 EOD 行 %s → data_unavailable=%d(净浮盈=%s)",
                        pid, ts_code, td, "有" if val == 0 else "无", val, net_float)
        rep.integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        if confirm:
            conn.commit()
            logger.info("[COMMIT] 已落盘(integrity_check=%s)", rep.integrity)
        else:
            conn.rollback()
            logger.info("[DRY-RUN] 已回滚,库未改动(加 --confirm 才真写)")
        return rep
    finally:
        conn.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="回填 holding_eod_check.data_unavailable(v1.4-①-B)")
    ap.add_argument("--db", required=True)
    ap.add_argument("--parquet-dir", required=True)
    ap.add_argument("--confirm", action="store_true", help="真写(不加 = 只演练打印)")
    ap.add_argument("--skip-backup", action="store_true", help="跳过备份(仅在外部已备份时用)")
    args = ap.parse_args(argv)

    db_path, pdir = Path(args.db), Path(args.parquet_dir)
    if not db_path.exists():
        logger.error("库文件不存在:%s", db_path)
        return 1
    if args.confirm and not args.skip_backup:
        import shutil

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = db_path.with_name(f"{db_path.name}.bak-dataunavail-{stamp}")
        src = sqlite3.connect(str(db_path))
        try:
            dst = sqlite3.connect(str(bak))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        shutil.copy2(db_path, db_path.with_name(f"{db_path.name}.cpbak-dataunavail-{stamp}"))
        logger.info("[backup] .backup + cp -p → %s", bak)

    rep = backfill(db_path, pdir, confirm=args.confirm)
    logger.info("完成:回填 %d 行 / 无分区留 NULL %d 行 / 已有值跳过 %d 行 / integrity=%s / %s",
                len(rep.filled), len(rep.skipped_no_partition), rep.already_set, rep.integrity,
                "已落盘" if not rep.dry_run else "演练(未落盘)")
    return 0 if rep.integrity == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
