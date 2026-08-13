#!/usr/bin/env python3
"""一次性退役 LEGACY 策略包(plan §五 V2.2-①「表与契约变更」)。🔴 碰生产注册表。

**背景**:V2.2-① 把 `selection_packs` 从单包制升级为多版本线注册表(骨架 V +
引擎 C/Z/Y),历史两行(`K4-pack-v1` / `K7-pack-v1`)由列迁移 DEFAULT 落位
`line_code='LEGACY'`。**唯一现役约束已改为每线唯一** —— 若不先把 LEGACY 行的
`is_active` 清零就激活骨架线,会出现「LEGACY 与 V 两行同时现役」:两行都合法、
`get_active_*` 各取各的,正是 V2 契约线审计 🔵 B3 要防的「今天用的是哪个包看
运气」。**部署顺序铁律(plan §五 ① 原文):本脚本必须先于任何新线激活在生产上
跑一次。**

**为什么住一次性脚本而不是 init_schema**:`init_schema` 只建表建列建索引,⛔ 不改
业务行(既有纪律)—— 清 `is_active` 是业务动作。

**做三件事,全程幂等,⛔ 不 DELETE 任何行(停写留档,§七 P4-31 同族纪律)**:
  1. **备份**:`sqlite3 .backup`(在线一致性)+ `cp -p` 双保险(照
     `fix_position_buy_dates.py::backup_db` 姿势);
  2. **退役**:`line_code='LEGACY'` 且 `is_active=1` 的行 → `is_active=0`
     (`activated_at` 保留 —— 它是「上一次何时激活」的历史事实,不是现役标志),
     并在 `selection_pack_activation_log` 追加一条
     `action='deactivate'` / `via='cli'` / `note='V2.2 K8 换线,LEGACY 包退役留档'`;
  3. **复查**:总行数前后逐位相等(零 DELETE 的机器断言)+ LEGACY 线零现役 +
     `PRAGMA integrity_check`。

安全闸:默认演练(事务回滚,报告照出);`--confirm` 才落盘;二次运行找不到
现役 LEGACY 行 → no-op(幂等)。

用法:
    python scripts/oneoff/retire_legacy_packs.py --db /opt/neckline/data/neckline.db            # 演练
    python scripts/oneoff/retire_legacy_packs.py --db /opt/neckline/data/neckline.db --confirm  # 真写

核心 `retire_legacy_packs` 可导入,单测见 `tests/test_retire_legacy_packs.py`
(承阶段 0 教训:改脚本级写库代码先补一层单测)。
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger("retire_legacy_packs")

# 仓库根入 sys.path(同目录内其它 oneoff 脚本惯例)。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fix_position_buy_dates import backup_db  # noqa: E402(同目录既有双备份实现,不抄第二份)

RETIRE_NOTE = "V2.2 K8 换线,LEGACY 包退役留档"


@dataclass
class RetireReport:
    retired: List[Dict[str, str]] = field(default_factory=list)   # 本次被清 is_active 的行
    already_inactive: List[str] = field(default_factory=list)     # LEGACY 但本就非现役(留档,不动)
    rows_before: int = 0
    rows_after: int = 0
    integrity: str = ""
    dry_run: bool = True


def retire_legacy_packs(db_path: Path, *, confirm: bool = False) -> RetireReport:
    """核心:LEGACY 现役行退役 + 事件留痕。`confirm=False` → 全程演练(事务回滚),
    打印与真跑一致。⛔ 零 DELETE:总行数前后逐位相等是硬断言,不等直接 SystemExit
    (绝不 commit 一个行数变了的事务)。"""
    from neckline.db import init_schema

    rep = RetireReport(dry_run=not confirm)
    init_schema(db_path)   # 保证 line_code/status 两列已迁移到位(幂等)
    conn = sqlite3.connect(str(db_path))
    try:
        rep.rows_before = conn.execute("SELECT COUNT(*) FROM selection_packs").fetchone()[0]
        rep.already_inactive = [
            r[0] for r in conn.execute(
                "SELECT pack_version FROM selection_packs "
                "WHERE line_code='LEGACY' AND is_active=0 ORDER BY pack_version"
            )
        ]
        targets = conn.execute(
            "SELECT pack_version, activated_at FROM selection_packs "
            "WHERE line_code='LEGACY' AND is_active=1 ORDER BY pack_version"
        ).fetchall()
        if not targets:
            logger.info("[skip] 无现役 LEGACY 行(已退役过或库为空),no-op(幂等)")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for pack_version, activated_at in targets:
            conn.execute(
                "UPDATE selection_packs SET is_active=0 "
                "WHERE pack_version=? AND line_code='LEGACY'",
                (pack_version,),
            )
            conn.execute(
                "INSERT INTO selection_pack_activation_log (pack_version, action, via, note, at) "
                "VALUES (?,?,?,?,?)",
                (pack_version, "deactivate", "cli", RETIRE_NOTE, now),
            )
            rep.retired.append({"pack_version": pack_version, "was_activated_at": str(activated_at)})
            logger.info("[retire] %s is_active 1→0(activated_at=%s 留档不动)", pack_version, activated_at)

        rep.rows_after = conn.execute("SELECT COUNT(*) FROM selection_packs").fetchone()[0]
        if rep.rows_after != rep.rows_before:
            conn.rollback()
            raise SystemExit(
                f"[ABORT] selection_packs 行数变了({rep.rows_before} → {rep.rows_after})"
                "—— 本脚本承诺零 DELETE,回滚并中止"
            )
        still_active = conn.execute(
            "SELECT COUNT(*) FROM selection_packs WHERE line_code='LEGACY' AND is_active=1"
        ).fetchone()[0]
        if still_active:
            conn.rollback()
            raise SystemExit(f"[ABORT] 退役后仍有 {still_active} 行现役 LEGACY,回滚并中止")
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
    ap = argparse.ArgumentParser(description="退役 LEGACY 策略包(V2.2-①,停写留档不 DELETE)")
    ap.add_argument("--db", required=True, help="SQLite 库路径(生产 /opt/neckline/data/neckline.db)")
    ap.add_argument("--confirm", action="store_true", help="真写(不加 = 只演练打印)")
    ap.add_argument("--skip-backup", action="store_true",
                    help="跳过备份(**只在已由外部完成备份时用**)")
    ap.add_argument("--tag", default="retirelegacy", help="备份文件名标签")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        logger.error("库文件不存在:%s", db_path)
        return 1

    if args.confirm and not args.skip_backup:
        backup_db(db_path, args.tag)
    elif args.confirm:
        logger.warning("[backup] --skip-backup:本次不备份(确认外部已备份过)")

    rep = retire_legacy_packs(db_path, confirm=args.confirm)
    logger.info(
        "完成:退役 %d 行 / 本就非现役 %d 行 / 总行数 %d(前后不变=零 DELETE)/ "
        "integrity=%s / %s",
        len(rep.retired), len(rep.already_inactive), rep.rows_after, rep.integrity,
        "已落盘" if not rep.dry_run else "演练(未落盘)",
    )
    return 0 if rep.integrity == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
