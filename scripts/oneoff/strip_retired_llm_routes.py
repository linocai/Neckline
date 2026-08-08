#!/usr/bin/env python3
"""一次性清理生产库 `app_settings.llm_task_routes` 里的**已退役任务名**
(plan §五 V2.1-①「问询台整链退役」)。🟡 碰 `app_settings` 单行表,风险远低于
`strategy_versions`(不是纪律章程,不影响任何判定路径),但仍是改生产配置,走
标准双备份 + 默认演练体例。

**为什么需要这个脚本**(不只是代码删干净就够,§五①原文写死的两件套之二):
`neckline.llm.router.ALL_TASKS` 删掉 `TASK_INQUIRY` 之后,`settings_store.
set_llm_routes()` 对不在 `ALL_TASKS` 里的任务名一律 `ValueError`(严格校验,不静默
吞拼写错误)。若生产库 `app_settings.llm_task_routes` 里还留着 `"inquiry"` 这个键
(问询台退役前写入的路由配置),`GET /settings/llm-routes` 会读侧过滤掉它
(`settings_store.get_llm_routes()` 已同步加固,见该函数 V2.1-① 起的 docstring),
但**这只是运行时兜底**——生产库里那个死键仍然存在,是需要人工清一次的历史残留。
两件套的另一半 = 本脚本:直接把这个已确认退役的键从生产库里删掉,让
`app_settings.llm_task_routes` 这张审计视图本身也如实反映"问询台已退役"这件事,
不必永远依赖读侧过滤兜底。

**只改一件事,不扩**:`app_settings`(`id=1` 单行表)的 `llm_task_routes` JSON 列里,
**删掉 `RETIRED_TASK_KEYS` 里点名的键**(默认只有 `"inquiry"` 一个)。**不碰**
`llm_default_provider`(即便它当前恰好等于某个已退役 provider 名字,那是另一件事,
不归本脚本管)、不碰 `app_settings` 的任何其它列、不碰任何其它表。

**幂等语义(两态,比 `retire_k4_b3.py` 简单——这里没有"基线是否吻合"的顾虑,
JSON 字典删键操作本身天然幂等)**:
  1. 目标键仍在 `llm_task_routes` 里 → 演练打 diff;`--confirm` 才写。
  2. 目标键已不在(此前跑过一次,或库本来就没有)→ 报"无需变更",`--confirm` 也是
     **0 改动**,exit 0。

**留痕**:写前 `.backup`(在线一致性快照)+ `cp -p` 双备份(默认落库同目录,
可 `--backup-dir` 改);写前写后各打印一份 `llm_task_routes` 全文,供审计留痕
(不落单独 dump 文件——JSON 本身就一两行,终端输出即完整记录,不必仿 K4 脚本
的 `--dump-dir` 那一套)。

用法:
    python scripts/oneoff/strip_retired_llm_routes.py --db /path/neckline.db                # 演练(默认,不写)
    python scripts/oneoff/strip_retired_llm_routes.py --db /path/neckline.db --confirm       # 双备份 + 单事务写
    python scripts/oneoff/strip_retired_llm_routes.py --db /path/neckline.db --key inquiry --key ghost_task  # 显式指定多个键(默认只有 inquiry)

核心函数 `strip_keys`/`apply_strip` 可导入,单测见
`tests/test_strip_retired_llm_routes.py`(承阶段 0 教训「改脚本级写库代码先补一层单测」)。
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("strip_retired_llm_routes")

# 本次退役事件点名的任务键(plan §五 V2.1-①原文:「清生产库 `app_settings.
# llm_task_routes` 里的 `inquiry` 键」)。⚠ 这是**已确认退役**的键名单,不是
# 「凡不在 ALL_TASKS 里就删」的通用清理器——本脚本 stdlib-only、不 import
# `neckline`,没有(也不需要)在运行时反查当前 `ALL_TASKS`;真正兜底"任何未知任务名"
# 的是 `settings_store.get_llm_routes()` 的读侧过滤,本脚本只负责把这一个已知死键
# 从库里物理清掉。将来若有新的任务退役,照本脚本体例另开一个同类一次性脚本
# (同项目 `charter_v1_2.py`/`charter_v1_3.py`/`retire_k4_b3.py` 的「一次性脚本对应
# 一个退役事件」惯例),不要把这个改成万能工具。
RETIRED_TASK_KEYS: Tuple[str, ...] = ("inquiry",)


@dataclass
class StripReport:
    before: Dict[str, str] = field(default_factory=dict)
    after: Dict[str, str] = field(default_factory=dict)
    removed: Dict[str, str] = field(default_factory=dict)          # 键 -> 被删前的值
    default_provider: Optional[str] = None
    integrity: str = ""
    dry_run: bool = True

    @property
    def changed(self) -> bool:
        return bool(self.removed)


def strip_keys(routes: Dict[str, str], keys: Sequence[str]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """纯函数:从 `routes` 里摘掉 `keys` 点名的那几个键。返回 `(新字典, 被删条目)`。
    `keys` 不在 `routes` 里 → 该键不出现在被删条目里(幂等态的机器判据)。"""
    removed = {k: routes[k] for k in keys if k in routes}
    new_routes = {k: v for k, v in routes.items() if k not in removed}
    return new_routes, removed


def _now() -> str:
    """同 `settings_store._now()` 口径(UTC ISO8601,秒精度)——本脚本对
    `app_settings` 的写入语义等价于「脚本代人跑了一次 `set_llm_routes`」,
    `updated_at` 理应刷新,与该函数保持同一时间格式。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_row(conn: sqlite3.Connection) -> Optional[Tuple[str, Optional[str]]]:
    row = conn.execute(
        "SELECT llm_task_routes, llm_default_provider FROM app_settings WHERE id=1"
    ).fetchone()
    return (row[0], row[1]) if row is not None else None


def _parse_routes(raw: Optional[str]) -> Dict[str, str]:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise ValueError(f"llm_task_routes 不是合法 JSON:{raw!r}") from None
    if not isinstance(obj, dict):
        raise ValueError(f"llm_task_routes 不是 JSON 对象:{raw!r}")
    return {str(k): str(v) for k, v in obj.items()}


def _double_backup(db_path: Path, backup_dir: Path, tag: str) -> Tuple[Path, Path, str]:
    """`.backup`(在线一致性快照)+ `cp -p` 双备份,同项目既有一次性脚本体例
    (`fix_position_buy_dates.py::backup_db` / `retire_k4_b3.py::_double_backup`)。
    返回 `(bak, cpbak, integrity_check 结果)`。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = backup_dir / f"{db_path.name}.bak-{tag}-{stamp}"
    cpbak = backup_dir / f"{db_path.name}.cpbak-{tag}-{stamp}"
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(bak))
        try:
            src.backup(dst)
            integ = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()
    if integ != "ok":
        raise ValueError(f".backup 产物 integrity_check={integ},拒绝继续。")
    shutil.copy2(db_path, cpbak)          # cp -p 等价(保留 mtime/权限)
    return bak, cpbak, integ


def apply_strip(
    db_path: Path, keys: Sequence[str], *, confirm: bool,
    backup_dir: Optional[Path] = None, tag: str = "striproutes",
) -> StripReport:
    """核心orchestration:读→算→(演练止步 / --confirm 才双备份+单事务写)→写后复核。
    不存在 `app_settings.id=1` 行 → 抛(库未初始化或不是预期库,不是本脚本能安全处理
    的场景,故意不静默兜底)。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        row = _read_row(conn)
        if row is None:
            raise ValueError("app_settings 无 id=1 行——库未初始化,或不是预期的 Neckline 库。")
        raw_before, default_provider = row
        before = _parse_routes(raw_before)
        new_routes, removed = strip_keys(before, keys)

        rep = StripReport(
            before=before, after=new_routes, removed=removed,
            default_provider=default_provider, dry_run=not confirm,
        )
        logger.info("写前 llm_task_routes = %s", json.dumps(before, ensure_ascii=False, sort_keys=True))
        logger.info("llm_default_provider = %r(本脚本不碰这一列)", default_provider)

        if not removed:
            logger.info("✓ %s 均已不在 llm_task_routes 里——**无需变更**(幂等,0 改动)。", list(keys))
            rep.integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            return rep

        logger.info("将删除的键 = %s", json.dumps(removed, ensure_ascii=False, sort_keys=True))
        logger.info("写后 llm_task_routes = %s", json.dumps(new_routes, ensure_ascii=False, sort_keys=True))

        if not confirm:
            logger.info("[dry-run] 未带 --confirm,**未写库**。确认无误后加 --confirm 重跑。")
            rep.integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            return rep

        bdir = backup_dir or db_path.parent
        bak, cpbak, integ = _double_backup(db_path, bdir, tag)
        logger.info("[backup] .backup → %s(integrity=%s)", bak, integ)
        logger.info("[backup] cp -p   → %s", cpbak)

        payload = json.dumps(new_routes, ensure_ascii=False)
        with conn:  # 单事务:成功即 commit,异常自动 rollback
            cur = conn.execute(
                "UPDATE app_settings SET llm_task_routes=?, updated_at=? WHERE id=1",
                (payload, _now()),
            )
            if cur.rowcount != 1:
                raise ValueError(f"UPDATE 影响 {cur.rowcount} 行(期望 1)——已回滚,库未改动。")

        # —— 写后复核(只读)——
        after_row = _read_row(conn)
        assert after_row is not None
        after_raw, after_default = after_row
        after_routes = _parse_routes(after_raw)
        checks = [
            (f"llm_task_routes 已不含 {list(removed)}", not any(k in after_routes for k in removed)),
            (f"其余键值不变 = {json.dumps({k: v for k, v in before.items() if k not in removed}, ensure_ascii=False, sort_keys=True)}",
             {k: v for k, v in after_routes.items()} == new_routes),
            (f"llm_default_provider 未动 = {after_default!r}", after_default == default_provider),
        ]
        integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
        checks.append((f"PRAGMA integrity_check = {integ}", integ == "ok"))
        bad = [text for text, ok in checks if not ok]
        for text, ok in checks:
            logger.info("  %s %s", "✓" if ok else "✗", text)
        if bad:
            raise ValueError(f"写后复核 {len(bad)} 条未通过(回滚绳 = 本次双备份 {bak}):{bad}")

        rep.after = after_routes
        rep.integrity = integ
        logger.info("✓ 已写入(单事务,1 行)并复核通过。")
        return rep
    finally:
        conn.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(
        description="清理 app_settings.llm_task_routes 里的已退役任务名(V2.1-①,默认 dry-run)"
    )
    ap.add_argument("--db", type=Path, required=True, help="目标 SQLite 库(生产 /opt/neckline/data/neckline.db)")
    ap.add_argument("--key", action="append", dest="keys", metavar="TASK_NAME",
                    help=f"要清除的任务键(可重复;默认 {list(RETIRED_TASK_KEYS)}）")
    ap.add_argument("--confirm", action="store_true", help="真写(不加 = 只演练打印)")
    ap.add_argument("--backup-dir", type=Path, default=None, help="双备份落点(默认 = 库同目录)")
    ap.add_argument("--tag", default="striproutes", help="备份文件名标签")
    args = ap.parse_args(argv)

    db_path: Path = args.db
    if not db_path.exists():
        logger.error("库文件不存在:%s", db_path)
        return 1
    keys: List[str] = args.keys or list(RETIRED_TASK_KEYS)

    try:
        rep = apply_strip(db_path, keys, confirm=args.confirm, backup_dir=args.backup_dir, tag=args.tag)
    except (ValueError, OSError, sqlite3.Error) as e:
        logger.error("错误(已中止):%s", e)
        return 2

    logger.info(
        "完成:删 %d 键 / integrity=%s / %s",
        len(rep.removed), rep.integrity,
        "已落盘" if rep.changed and not rep.dry_run else ("幂等 0 改动" if not rep.changed else "演练(未落盘)"),
    )
    return 0 if rep.integrity == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
