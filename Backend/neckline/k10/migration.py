"""Offline V3 cutover with an immutable, verified SQLite backup.

This command is never called by a read endpoint or application startup. The
operator must stop all database writers and explicitly identify the target.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Callable

from .schema import initialize_schema, schema_version


COMMON_TABLES = frozenset({
    "trade_cal", "stock_basic", "namechange", "backfill_log", "app_settings",
    "devices", "llm_providers", "llm_usage_events", "sw_industry_classify",
    "sw_industry_member", "sw_industry_member_snapshots",
    "sw_industry_snapshot_manifests", "sw_industry_snapshot_imports",
})
RETIRED_TABLES = frozenset({
    "reviews", "review_conclusions", "job_events", "job_event_deliveries",
    "sw_industry_daily",
})


@dataclass(frozen=True)
class MigrationReceipt:
    target: Path
    backup: Path
    backup_sha256: str
    retired_tables: tuple[str, ...]


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_target(path: Path, confirmed_target: Path, writers_stopped: bool) -> Path:
    target = path.resolve(strict=True)
    if target != confirmed_target.resolve(strict=True) or not target.is_file():
        raise ValueError("目标路径与明确确认的数据库不一致")
    if not writers_stopped:
        raise ValueError("迁移前须停止 API、worker、timer 和行情写入")
    # Replacing a database while an old WAL is present can replay stale pages
    # over the new file. A clean service shutdown/checkpoint is required first.
    if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("数据库仍有事务旁文件；请完成停写和检查点后再迁移")
    return target


def _integrity(path: Path) -> None:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("数据库完整性检查失败")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("数据库外键检查失败")


def _backup(target: Path, backup: Path) -> str:
    backup.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL protects immutable backup names, including races and symlinks.
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    with closing(sqlite3.connect(target.as_uri() + "?mode=ro&immutable=1", uri=True)) as source:
        with closing(sqlite3.connect(backup)) as destination:
            source.backup(destination)
    _integrity(backup)
    return file_sha256(backup)


def _retire_schema(path: Path) -> tuple[str, ...]:
    with closing(sqlite3.connect(path)) as conn, conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        retired = {name for name in tables if name in RETIRED_TABLES or name.startswith(("k9_", "fact_"))}
        unknown = tables - retired - COMMON_TABLES - {name for name in tables if name.startswith(("k10_", "sqlite_"))}
        if unknown:
            raise ValueError("存在尚未确定迁移归属的表：" + ", ".join(sorted(unknown)))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        for table in sorted(retired):
            conn.execute('DROP TABLE "' + table.replace('"', '""') + '"')
        if "llm_usage_events" in tables:
            conn.execute("DELETE FROM llm_usage_events WHERE task NOT IN ('discovery','analysis','price','morning')")
        if "app_settings" in tables:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(app_settings)")}
            for column in ("review_col_map", "llm_default_provider", "llm_task_routes"):
                if column in columns:
                    conn.execute(f"ALTER TABLE app_settings DROP COLUMN {column}")
            if "push_kinds" in columns:
                # Only K10 notification names survive; retain explicit matching
                # user preferences, never translate an old product notification.
                import json
                for row_id, value in conn.execute("SELECT id,push_kinds FROM app_settings").fetchall():
                    try:
                        decoded = json.loads(value or "{}")
                    except (ValueError, TypeError):
                        decoded = {}
                    clean = {key: val for key, val in decoded.items() if key.startswith("k10_") and isinstance(val, bool)} if isinstance(decoded, dict) else {}
                    conn.execute("UPDATE app_settings SET push_kinds=? WHERE id=?", (json.dumps(clean), row_id))
    return tuple(sorted(retired))


def _replace(stage: Path, target: Path) -> None:
    with stage.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(stage, target)
    descriptor = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def migrate_to_v3(
    *, target: Path, confirmed_target: Path, backup: Path, writers_stopped: bool,
    initialize_common: Callable[[Path], None] | None = None,
) -> MigrationReceipt:
    target = _check_target(target, confirmed_target, writers_stopped)
    backup = backup.absolute()
    if backup.resolve() == target:
        raise ValueError("备份不能覆盖目标数据库")
    _integrity(target)
    original_digest = file_sha256(target)
    digest = _backup(target, backup)
    descriptor, stage_name = tempfile.mkstemp(prefix=".k10-migrate-", suffix=".sqlite", dir=target.parent)
    os.close(descriptor)
    stage = Path(stage_name)
    try:
        shutil.copyfile(backup, stage)
        retired = _retire_schema(stage)
        if initialize_common is not None:
            initialize_common(stage)
        initialize_schema(stage)
        from .notifications import initialize_notifications_schema
        initialize_notifications_schema(stage)
        with closing(sqlite3.connect(stage)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA journal_mode=DELETE")
        _integrity(stage)
        schema_version(stage)
        # Verify that the rollback copy still exists and has not changed before
        # publishing the completely prepared file.
        if file_sha256(backup) != digest:
            raise ValueError("备份哈希已变化，取消迁移")
        _check_target(target, confirmed_target, writers_stopped)
        if file_sha256(target) != original_digest:
            raise ValueError("目标数据库在迁移期间变化，取消切换；请重新核验停写状态")
        _replace(stage, target)
        return MigrationReceipt(target, backup, digest, retired)
    finally:
        stage.unlink(missing_ok=True)


def restore_backup(
    *, target: Path, confirmed_target: Path, backup: Path, expected_sha256: str,
    writers_stopped: bool,
) -> None:
    target = _check_target(target, confirmed_target, writers_stopped)
    if backup.resolve() == target or file_sha256(backup) != expected_sha256:
        raise ValueError("回滚备份路径或哈希不匹配")
    _integrity(backup.resolve())
    descriptor, stage_name = tempfile.mkstemp(prefix=".k10-restore-", suffix=".sqlite", dir=target.parent)
    os.close(descriptor)
    stage = Path(stage_name)
    try:
        shutil.copyfile(backup, stage)
        _replace(stage, target)
    finally:
        stage.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="停写后执行 V3 数据库切换或备份回滚")
    parser.add_argument("operation", choices=("migrate", "restore"))
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--confirmed-target", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--backup-sha256")
    args = parser.parse_args(argv)
    common = dict(target=args.db, confirmed_target=args.confirmed_target, backup=args.backup, writers_stopped=args.writers_stopped)
    if args.operation == "migrate":
        from neckline.db import init_schema
        receipt = migrate_to_v3(**common, initialize_common=init_schema)
        print(f"V3 schema prepared; backup={receipt.backup}; sha256={receipt.backup_sha256}")
    else:
        if not args.backup_sha256:
            parser.error("restore 必须明确提供 --backup-sha256")
        restore_backup(**common, expected_sha256=args.backup_sha256)
        print("已恢复经过哈希验证的数据库备份；服务和客户端须使用匹配版本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
