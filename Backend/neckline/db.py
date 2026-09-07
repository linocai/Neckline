"""Shared SQLite contracts for market metadata, connection settings and devices.

K10 migrations are explicit and separate. Read helpers never execute DDL.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional, Set

from neckline.config import settings

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS trade_cal (
  exchange TEXT NOT NULL,
  cal_date TEXT NOT NULL,
  is_open INTEGER NOT NULL,
  pretrade_date TEXT,
  PRIMARY KEY (exchange, cal_date)
);

CREATE INDEX IF NOT EXISTS idx_trade_cal_date ON trade_cal(cal_date);

CREATE TABLE IF NOT EXISTS stock_basic (
  ts_code TEXT PRIMARY KEY,
  symbol TEXT,
  name TEXT,
  industry TEXT,
  market TEXT,
  list_date TEXT,
  delist_date TEXT,
  list_status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stock_basic_market ON stock_basic(market);

CREATE TABLE IF NOT EXISTS namechange (
  ts_code TEXT NOT NULL,
  name TEXT NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT,
  ann_date TEXT,
  change_reason TEXT,
  PRIMARY KEY (ts_code, start_date, name)
);

CREATE INDEX IF NOT EXISTS idx_namechange_code ON namechange(ts_code);

CREATE TABLE IF NOT EXISTS backfill_log (
  table_name TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  status TEXT NOT NULL,
  row_count INTEGER NOT NULL DEFAULT 0,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (table_name, trade_date)
);

CREATE TABLE IF NOT EXISTS app_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  tavily_api_key TEXT,
  updated_at TEXT,
  push_kinds TEXT
);

CREATE TABLE IF NOT EXISTS devices (
  token TEXT PRIMARY KEY,
  platform TEXT NOT NULL DEFAULT 'ios',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_providers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  base_url TEXT NOT NULL,
  model TEXT NOT NULL,
  api_key TEXT,
  has_web_search INTEGER NOT NULL DEFAULT 0,
  search_engine TEXT,
  notes TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sw_industry_classify (
  index_code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  level TEXT NOT NULL,
  parent_code TEXT,
  src TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sw_classify_level ON sw_industry_classify(level);

CREATE TABLE IF NOT EXISTS sw_industry_member (
  ts_code TEXT PRIMARY KEY,
  name TEXT,
  l1_code TEXT NOT NULL,
  l1_name TEXT NOT NULL,
  l2_code TEXT NOT NULL,
  l2_name TEXT NOT NULL,
  l3_code TEXT NOT NULL,
  l3_name TEXT NOT NULL,
  in_date TEXT,
  out_date TEXT,
  is_current INTEGER NOT NULL DEFAULT 1,
  fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sw_member_l2 ON sw_industry_member(l2_code);

CREATE INDEX IF NOT EXISTS idx_sw_member_current ON sw_industry_member(is_current);

CREATE TABLE IF NOT EXISTS sw_industry_member_snapshots (
  trade_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  name TEXT,
  l1_code TEXT NOT NULL, l1_name TEXT NOT NULL,
  l2_code TEXT NOT NULL, l2_name TEXT NOT NULL,
  l3_code TEXT NOT NULL, l3_name TEXT NOT NULL,
  source_fetched_at TEXT NOT NULL,
  PRIMARY KEY(trade_date, ts_code)
);

CREATE INDEX IF NOT EXISTS idx_sw_member_snapshot_date_l2 ON sw_industry_member_snapshots(trade_date,l2_code);

CREATE TABLE IF NOT EXISTS sw_industry_snapshot_manifests (
  trade_date TEXT PRIMARY KEY,
  content_sha256 TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_generated_at TEXT NOT NULL,
  source_fetched_at TEXT NOT NULL,
  raw_file_sha256 TEXT,
  row_count INTEGER NOT NULL,
  imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sw_industry_snapshot_imports (
  raw_file_sha256 TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_generated_at TEXT NOT NULL,
  source_fetched_at TEXT NOT NULL,
  row_count INTEGER NOT NULL,
  start_trade_date TEXT NOT NULL,
  end_trade_date TEXT NOT NULL,
  imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_usage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT,
  report_date TEXT,
  pack_id TEXT,
  task TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  outcome TEXT NOT NULL,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  total_tokens INTEGER,
  usage_unavailable INTEGER NOT NULL DEFAULT 1,
  tavily_credits INTEGER,
  searched INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER,
  failure_reason TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_events_date_task
  ON llm_usage_events(trade_date, task);
"""


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns_of(conn: sqlite3.Connection, table: str) -> Set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path or settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def connection(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def readonly_connection(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """打开既有数据库的只读连接，不建目录、不建表、不迁移。"""
    path = Path(db_path or settings.db_path)
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def readonly_tables(
    *required: str, db_path: Optional[Path] = None
) -> Iterator[Optional[sqlite3.Connection]]:
    """只读打开数据库；缺文件、表或列时返回 ``None``，绝不顺手迁移。"""
    try:
        with readonly_connection(db_path) as conn:
            columns: Dict[str, Set[str]] = {}
            for item in required:
                table, _, column = item.partition(".")
                if table not in columns:
                    if not _table_exists(conn, table):
                        yield None
                        return
                    columns[table] = _columns_of(conn, table)
                if column and column not in columns[table]:
                    yield None
                    return
            yield conn
    except FileNotFoundError:
        yield None


def init_schema(db_path: Optional[Path] = None) -> None:
    """Explicit write entry point for shared infrastructure tables only."""
    with connection(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)


__all__ = ["get_connection", "connection", "readonly_connection", "readonly_tables", "init_schema"]
