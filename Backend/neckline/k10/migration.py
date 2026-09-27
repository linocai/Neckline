"""Offline restore for fresh-start databases only.

This command is never called by a read endpoint or application startup. The
operator must stop all database writers and explicitly identify the target.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from .schema import require_schema
from neckline.fresh_start import RetiredDataError


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


def _replace(stage: Path, target: Path) -> None:
    with stage.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(stage, target)
    descriptor = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def restore_backup(
    *, target: Path, confirmed_target: Path, backup: Path, expected_sha256: str,
    writers_stopped: bool,
) -> None:
    target = _check_target(target, confirmed_target, writers_stopped)
    backup = _check_target(backup, backup, writers_stopped)
    target_stat = target.stat()
    # Validate only the header before hashing, integrity reads or copying data.
    # Pre-B92 restoration is retired, including backups made by older tooling.
    for path in (target, backup):
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
            require_schema(conn)
    if backup.resolve() == target or file_sha256(backup) != expected_sha256:
        raise ValueError("回滚备份路径或哈希不匹配")
    original_digest = file_sha256(target)
    _integrity(backup.resolve())
    descriptor, stage_name = tempfile.mkstemp(prefix=".k10-restore-", suffix=".sqlite", dir=target.parent)
    os.close(descriptor)
    stage = Path(stage_name)
    try:
        shutil.copyfile(backup, stage)
        if file_sha256(stage) != expected_sha256 or file_sha256(backup) != expected_sha256:
            raise ValueError("备份内容在恢复期间变化")
        _integrity(stage)
        _check_target(target, confirmed_target, writers_stopped)
        if file_sha256(target) != original_digest:
            raise ValueError("目标数据库在恢复期间变化，取消覆盖")
        os.chown(stage, target_stat.st_uid, target_stat.st_gid)
        os.chmod(stage, target_stat.st_mode & 0o7777)
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
        raise RetiredDataError("旧库迁移已移除；使用 k10 initialize-fresh --db 新路径")
    if not args.backup_sha256:
        parser.error("restore 必须明确提供 --backup-sha256")
    restore_backup(**common, expected_sha256=args.backup_sha256)
    print("已恢复新起点内经过核验的数据库备份")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
