"""Neckline 现行 SQLite 契约。

当前数据库只承载 K9 生产链、基础行情元数据、设置、通知去重和周复盘。
读路径只能使用 ``readonly_connection`` / ``readonly_tables``，不能触发迁移。
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

CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT NOT NULL,
  scope TEXT NOT NULL,
  ts_code TEXT NOT NULL DEFAULT '',
  event_key TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  pushed_at TEXT NOT NULL,
  UNIQUE(trade_date, scope, ts_code, event_key)
);
CREATE INDEX IF NOT EXISTS idx_job_events_trade_date ON job_events(trade_date);

-- A checklist notification can fan out to several devices.  The aggregate
-- job marker is written only after every currently valid device is terminal;
-- successful devices are remembered here so a transient failure on one
-- device never makes the next poll resend to all of the others.
CREATE TABLE IF NOT EXISTS job_event_deliveries (
  trade_date TEXT NOT NULL,
  scope TEXT NOT NULL,
  ts_code TEXT NOT NULL DEFAULT '',
  event_key TEXT NOT NULL,
  device_key TEXT NOT NULL,
  delivered_at TEXT NOT NULL,
  PRIMARY KEY(trade_date, scope, ts_code, event_key, device_key)
);
CREATE INDEX IF NOT EXISTS idx_job_event_deliveries_trade_date
  ON job_event_deliveries(trade_date);

CREATE TABLE IF NOT EXISTS app_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  tavily_api_key TEXT,
  review_col_map TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT,
  llm_default_provider TEXT,
  llm_task_routes TEXT NOT NULL DEFAULT '{}',
  push_kinds TEXT
);

CREATE TABLE IF NOT EXISTS devices (
  token TEXT PRIMARY KEY,
  platform TEXT NOT NULL DEFAULT 'ios',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
  week TEXT PRIMARY KEY,
  generated_at TEXT NOT NULL,
  result_json TEXT NOT NULL DEFAULT '{}',
  material TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS review_conclusions (
  week TEXT NOT NULL,
  version INTEGER NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  author TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (week, version)
);
CREATE INDEX IF NOT EXISTS idx_review_conclusions_week
  ON review_conclusions(week);

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
-- Immutable retrieval snapshots.  K9-v3 fp-4 may only read an explicit
-- target-date snapshot; fetched_at/current membership never backfills history.
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
-- One immutable manifest per effective trade date.  Snapshot rows alone never
-- claim a historical provenance: fp-4 requires this ledger as well.
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

CREATE TABLE IF NOT EXISTS sw_industry_daily (
  trade_date TEXT NOT NULL,
  l2_code TEXT NOT NULL,
  l2_name TEXT NOT NULL,
  member_count INTEGER NOT NULL,
  suspended_excluded INTEGER NOT NULL,
  median_ret REAL NOT NULL,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, l2_code)
);
CREATE INDEX IF NOT EXISTS idx_sw_industry_daily_date
  ON sw_industry_daily(trade_date);

CREATE TABLE IF NOT EXISTS fact_packs (
  pack_id TEXT PRIMARY KEY,
  trade_date TEXT NOT NULL,
  pack_version TEXT NOT NULL,
  origin TEXT NOT NULL,
  state TEXT NOT NULL,
  content_fingerprint TEXT NOT NULL,
  row_count INTEGER NOT NULL,
  sources_json TEXT NOT NULL,
  market_json TEXT NOT NULL,
  suspend_anomaly_count INTEGER NOT NULL,
  frozen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fact_packs_date ON fact_packs(trade_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fact_packs_date_version
  ON fact_packs(trade_date, pack_version);

-- An explicitly authorized correction never rewrites the original frozen
-- fact pack.  Public consumers still see the same fp-4 contract while the
-- internal revision and supersession chain preserve both byte identities.
CREATE TABLE IF NOT EXISTS fact_pack_revisions (
  pack_id TEXT PRIMARY KEY,
  trade_date TEXT NOT NULL,
  pack_version TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 2),
  supersedes_pack_id TEXT NOT NULL,
  correction_reason TEXT NOT NULL,
  origin TEXT NOT NULL,
  state TEXT NOT NULL,
  content_fingerprint TEXT NOT NULL,
  row_count INTEGER NOT NULL,
  sources_json TEXT NOT NULL,
  market_json TEXT NOT NULL,
  suspend_anomaly_count INTEGER NOT NULL,
  frozen_at TEXT NOT NULL,
  UNIQUE(trade_date, pack_version, revision)
);
CREATE INDEX IF NOT EXISTS idx_fact_pack_revisions_date
  ON fact_pack_revisions(trade_date, pack_version, revision DESC);

-- 方向解读是冻结事实包的旁路，不参与 K9 策略。
CREATE TABLE IF NOT EXISTS fact_direction_briefings (
  pack_id TEXT PRIMARY KEY,
  trade_date TEXT NOT NULL,
  state TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  themes_json TEXT NOT NULL DEFAULT '[]',
  provider TEXT,
  model TEXT,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  failure_reason TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fact_direction_briefings_date
  ON fact_direction_briefings(trade_date);

-- 每次真实 LLM/搜索调用的去敏审计账。Token 未回传必须显式为 NULL。
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

-- K9-v3：成绩包是主实体。既有旧表只由显式迁移工具处理。
CREATE TABLE IF NOT EXISTS k9_selection_batches (
  batch_id TEXT PRIMARY KEY,
  selection_date TEXT NOT NULL,
  signal_trade_date TEXT NOT NULL,
  d1_trade_date TEXT NOT NULL,
  d2_trade_date TEXT NOT NULL,
  revision INTEGER NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('d0','d1','settled')),
  coverage_state TEXT NOT NULL DEFAULT 'pending' CHECK(coverage_state IN ('pending','complete','partial','unavailable')),
  strategy_version TEXT NOT NULL CHECK(strategy_version='K9-v3'),
  params_package_version TEXT NOT NULL,
  params_sha256 TEXT NOT NULL,
  pack_id TEXT NOT NULL,
  pack_version TEXT NOT NULL CHECK(pack_version='fp-4'),
  label_contract_version TEXT NOT NULL CHECK(label_contract_version='d2-v2'),
  frozen_contract_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(selection_date, revision, strategy_version)
);
CREATE INDEX IF NOT EXISTS idx_k9_v3_batches_state ON k9_selection_batches(state, selection_date DESC);

CREATE TABLE IF NOT EXISTS k9_selection_candidates (
  batch_id TEXT NOT NULL REFERENCES k9_selection_batches(batch_id) ON DELETE RESTRICT,
  ts_code TEXT NOT NULL,
  name TEXT,
  sw_l2_code TEXT,
  sw_l2_name TEXT,
  channels_json TEXT NOT NULL,
  channel_ranks_json TEXT NOT NULL,
  frozen_playbook_json TEXT NOT NULL,
  baseline_json TEXT NOT NULL,
  thresholds_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(batch_id, ts_code)
);

-- K9-v3 pre-plan revisions are append-only.  The candidate row keeps the
-- original D0 revision; current display may point at a later user revision
-- until the 9:26 checklist freezes one for D1.
CREATE TABLE IF NOT EXISTS k9_playbook_revisions (
  batch_id TEXT NOT NULL REFERENCES k9_selection_batches(batch_id) ON DELETE RESTRICT,
  ts_code TEXT NOT NULL,
  revision INTEGER NOT NULL,
  source TEXT NOT NULL CHECK(source IN ('llm','user')),
  mechanical_json TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  confirmed_at TEXT,
  frozen_at TEXT,
  PRIMARY KEY (batch_id, ts_code, revision)
);
CREATE INDEX IF NOT EXISTS idx_k9_v3_playbook_revisions
  ON k9_playbook_revisions(batch_id, ts_code, revision DESC);
-- 9:26 is an independent, immutable D1 boundary.  It is deliberately not
-- inferred from a checklist row: a failed checklist write must not leave a
-- half-frozen package, and a missed timer must not reopen edits after 09:26.
CREATE TABLE IF NOT EXISTS k9_playbook_freezes (
  batch_id TEXT NOT NULL REFERENCES k9_selection_batches(batch_id) ON DELETE RESTRICT,
  ts_code TEXT NOT NULL,
  revision INTEGER NOT NULL,
  frozen_at TEXT NOT NULL,
  reason TEXT NOT NULL CHECK(reason='d1_0926'),
  playbook_sha256 TEXT NOT NULL,
  PRIMARY KEY(batch_id, ts_code),
  FOREIGN KEY(batch_id, ts_code, revision) REFERENCES k9_playbook_revisions(batch_id, ts_code, revision) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_k9_v3_candidates_code ON k9_selection_candidates(ts_code, batch_id);

CREATE TABLE IF NOT EXISTS k9_selection_d1 (
  batch_id TEXT NOT NULL REFERENCES k9_selection_batches(batch_id) ON DELETE RESTRICT,
  ts_code TEXT NOT NULL,
  checklist_verdict TEXT NOT NULL CHECK(checklist_verdict IN ('rejected','unbuyable','pending_open')),
  open_verdict TEXT CHECK(open_verdict IN ('confirmed','rejected','observed','unbuyable','unavailable')),
  reference_price REAL,
  close_state TEXT CHECK(close_state IN ('enhanced','held','weakened','unavailable')),
  raw_json TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  PRIMARY KEY(batch_id, ts_code),
  FOREIGN KEY(batch_id, ts_code) REFERENCES k9_selection_candidates(batch_id, ts_code) ON DELETE RESTRICT
);

-- 10:00 分支与 D1 收盘评价是两个时间点的不可变追加记录，不能互相覆盖。
CREATE TABLE IF NOT EXISTS k9_selection_d1_close (
  batch_id TEXT NOT NULL REFERENCES k9_selection_batches(batch_id) ON DELETE RESTRICT,
  ts_code TEXT NOT NULL,
  close_state TEXT NOT NULL CHECK(close_state IN ('enhanced','held','weakened','unavailable')),
  raw_json TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  PRIMARY KEY(batch_id, ts_code),
  FOREIGN KEY(batch_id, ts_code) REFERENCES k9_selection_candidates(batch_id, ts_code) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS k9_selection_d2 (
  batch_id TEXT NOT NULL REFERENCES k9_selection_batches(batch_id) ON DELETE RESTRICT,
  ts_code TEXT NOT NULL,
  selection_result TEXT NOT NULL CHECK(selection_result IN ('success_realized','opportunity_not_continued','confirmed_failed','correct_reject','false_reject','observed_realized','observed_not_realized','unavailable')),
  playbook_result TEXT,
  risk_tag TEXT,
  raw_json TEXT NOT NULL,
  settled_at TEXT NOT NULL,
  PRIMARY KEY(batch_id, ts_code),
  FOREIGN KEY(batch_id, ts_code) REFERENCES k9_selection_candidates(batch_id, ts_code) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS k9_package_checklists (
  batch_id TEXT PRIMARY KEY REFERENCES k9_selection_batches(batch_id) ON DELETE RESTRICT,
  trade_date TEXT NOT NULL,
  checklist_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- 报告是成绩包的只读投影，使用独立 V3 报告表。
CREATE TABLE IF NOT EXISTS k9_package_reports (
  trade_date TEXT NOT NULL,
  report_date TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('has_list','empty','not_run')),
  headline TEXT NOT NULL,
  gaps_json TEXT NOT NULL,
  markdown TEXT NOT NULL,
  structured_json TEXT NOT NULL,
  strategy_version TEXT NOT NULL CHECK(strategy_version='K9-v3'),
  params_package_version TEXT,
  pack_id TEXT,
  pack_version TEXT CHECK(pack_version IS NULL OR pack_version='fp-4'),
  listing_size INTEGER,
  generated_at TEXT NOT NULL,
  PRIMARY KEY(trade_date, report_date, strategy_version)
);
CREATE INDEX IF NOT EXISTS idx_k9_package_reports_trade_date
  ON k9_package_reports(trade_date DESC, report_date DESC);

-- D0 运行身份不能由“今天查不到包”猜测。只有成功冻结的空包才是 empty；
-- 参数、事实、预案或策略失败均如实保留为 not_run/failed。
CREATE TABLE IF NOT EXISTS k9_d0_run_markers (
  selection_date TEXT PRIMARY KEY,
  signal_trade_date TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('has_list','empty','not_run','failed')),
  batch_id TEXT,
  reason TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_k9_d0_run_markers_signal_date
  ON k9_d0_run_markers(signal_trade_date DESC);

-- Durable cross-process truth for the evening lifecycle.  The report unit
-- must never guess success from package rows or systemd ordering alone.
CREATE TABLE IF NOT EXISTS k9_lifecycle_attempts (
  attempt_id TEXT PRIMARY KEY,
  selection_date TEXT NOT NULL,
  signal_trade_date TEXT NOT NULL,
  strategy_version TEXT NOT NULL CHECK(strategy_version='K9-v3'),
  run_identity TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('running','ok','failed')),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  UNIQUE(selection_date, signal_trade_date, strategy_version, run_identity)
);
CREATE TABLE IF NOT EXISTS k9_lifecycle_stages (
  attempt_id TEXT NOT NULL REFERENCES k9_lifecycle_attempts(attempt_id) ON DELETE RESTRICT,
  stage TEXT NOT NULL CHECK(stage IN ('d2','d1','d0')),
  status TEXT NOT NULL CHECK(status IN ('running','ok','failed')),
  detail TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(attempt_id, stage)
);

-- 盘中采样的来源/缺失审计。Parquet 保存真实行情点；本表保存每次尝试，
-- 让“没有分时”可区分为来源缺失而不是事后猜测。
CREATE TABLE IF NOT EXISTS k9_intraday_capture_audit (
  trade_date TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  source TEXT,
  status TEXT NOT NULL CHECK(status IN ('captured','unavailable','write_failed')),
  reason TEXT,
  PRIMARY KEY(trade_date,captured_at,ts_code)
);
CREATE INDEX IF NOT EXISTS idx_k9_intraday_capture_audit_date
  ON k9_intraday_capture_audit(trade_date, ts_code);
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


def _migrate_v270_d1_unavailable(conn: sqlite3.Connection) -> None:
    """Upgrade only the pre-release V2.7 D1 CHECK under the controlled write path."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='k9_selection_d1'"
    ).fetchone()
    if row is None or "'confirmed','rejected','observed','unbuyable','unavailable'" in str(row[0] or ""):
        return
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("ALTER TABLE k9_selection_d1 RENAME TO _k9_selection_d1_pre270")
        conn.execute("""CREATE TABLE k9_selection_d1 (
          batch_id TEXT NOT NULL REFERENCES k9_selection_batches(batch_id) ON DELETE RESTRICT,
          ts_code TEXT NOT NULL,
          checklist_verdict TEXT NOT NULL CHECK(checklist_verdict IN ('rejected','unbuyable','pending_open')),
          open_verdict TEXT CHECK(open_verdict IN ('confirmed','rejected','observed','unbuyable','unavailable')),
          reference_price REAL,
          close_state TEXT CHECK(close_state IN ('enhanced','held','weakened','unavailable')),
          raw_json TEXT NOT NULL,
          captured_at TEXT NOT NULL,
          PRIMARY KEY(batch_id, ts_code),
          FOREIGN KEY(batch_id, ts_code) REFERENCES k9_selection_candidates(batch_id, ts_code) ON DELETE RESTRICT
        )""")
        conn.execute("""INSERT INTO k9_selection_d1
          (batch_id,ts_code,checklist_verdict,open_verdict,reference_price,close_state,raw_json,captured_at)
          SELECT batch_id,ts_code,checklist_verdict,open_verdict,reference_price,close_state,raw_json,captured_at
          FROM _k9_selection_d1_pre270""")
        conn.execute("DROP TABLE _k9_selection_d1_pre270")
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _migrate_v270_capture_audit(conn: sqlite3.Connection) -> None:
    """Controlled pre-release widening: failed writes must not masquerade captured."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='k9_intraday_capture_audit'").fetchone()
    if row is None or "'write_failed'" in str(row[0] or ""):
        return
    conn.execute("ALTER TABLE k9_intraday_capture_audit RENAME TO _k9_intraday_capture_audit_pre270")
    conn.execute("""CREATE TABLE k9_intraday_capture_audit (
      trade_date TEXT NOT NULL, captured_at TEXT NOT NULL, ts_code TEXT NOT NULL, source TEXT,
      status TEXT NOT NULL CHECK(status IN ('captured','unavailable','write_failed')), reason TEXT,
      PRIMARY KEY(trade_date,captured_at,ts_code))""")
    conn.execute("INSERT INTO k9_intraday_capture_audit SELECT trade_date,captured_at,ts_code,source,status,reason FROM _k9_intraday_capture_audit_pre270")
    conn.execute("DROP TABLE _k9_intraday_capture_audit_pre270")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_k9_intraday_capture_audit_date ON k9_intraday_capture_audit(trade_date, ts_code)")


def init_schema(db_path: Optional[Path] = None) -> None:
    """受控写入口：仅建立当前 K9 schema。"""
    with connection(db_path) as conn:
        conn.executescript(_SCHEMA)
        _migrate_v270_d1_unavailable(conn)
        _migrate_v270_capture_audit(conn)


__all__ = [
    "get_connection", "connection", "readonly_connection", "readonly_tables", "init_schema"
]
