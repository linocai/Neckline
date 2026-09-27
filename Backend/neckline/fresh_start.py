"""B92 starts with empty storage. Old storage is never promoted or imported."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# SQLite file identity, deliberately independent of the evolving SQL schema.
APPLICATION_ID = 0x4E4B3932  # NK92


class RetiredDataError(RuntimeError):
    """The selected storage predates the user-authorized fresh start."""


def require_current_database(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
        raise RetiredDataError("B92 前历史数据库已停用；请明确初始化全新空库，禁止导入或恢复旧历史")


def initialize_empty_identity(conn: sqlite3.Connection) -> None:
    """Only an explicit schema initializer can stamp an actually empty file."""
    identity = conn.execute("PRAGMA application_id").fetchone()[0]
    if identity == APPLICATION_ID:
        return
    # Inspect schema metadata only, never SELECT business rows from a retired DB.
    if identity != 0 or conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
        raise RetiredDataError("不能将既有数据库补标为 B92 新库")
    conn.execute(f"PRAGMA application_id={APPLICATION_ID}")


def initialize_fresh_database(*, target: Path) -> dict[str, str]:
    """Create a new file, with no source database, automatic import or control opening."""
    from neckline.db import init_schema
    from neckline.k10.schema import initialize_schema
    from neckline.k10.notifications import initialize_notifications_schema

    target = Path(target).absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("目标仍有 SQLite 事务旁文件，拒绝初始化")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    # A failure leaves this explicitly named new file for diagnosis; never
    # delete/replace an existing operational database as part of initialization.
    init_schema(target)
    initialize_schema(target)
    initialize_notifications_schema(target)
    return {"database": str(target), "dataStart": "B92", "status": "empty_initialized"}


def require_current_market_directory(root: Path, *, initialize: bool = False) -> None:
    root = Path(root)
    marker = root / ".neckline-b92"
    if marker.is_dir():
        return
    if root.exists() and any(root.iterdir()) and not marker.is_dir():
        raise RetiredDataError("B92 前行情目录已停用；禁止读取或复用旧行情文件")
    if initialize:
        root.mkdir(parents=True, exist_ok=True)
        # Directory creation is atomic: concurrent first writers cannot see
        # a half-written marker or treat their sibling as retired storage.
        marker.mkdir(exist_ok=True)
