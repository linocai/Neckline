"""K10 SQLite schema 的显式、可回滚迁移入口。"""

from __future__ import annotations

import sqlite3
from hashlib import sha256
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 3


class K10SchemaError(RuntimeError):
    pass


class SchemaUnavailable(K10SchemaError):
    """读取了尚未经 K10 受控迁移的数据库。"""


_V1 = """
CREATE TABLE k10_run_config_revisions (
  config_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  payload_json TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (config_id, revision),
  UNIQUE (config_id, content_sha256)
);

CREATE TABLE k10_source_documents (
  document_id TEXT PRIMARY KEY,
  source_key TEXT NOT NULL,
  external_id TEXT NOT NULL,
  canonical_url TEXT,
  first_seen_at TEXT NOT NULL,
  UNIQUE(source_key, external_id)
);
CREATE TABLE k10_source_document_versions (
  document_id TEXT NOT NULL REFERENCES k10_source_documents(document_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  content_sha256 TEXT NOT NULL,
  published_at TEXT,
  published_precision TEXT NOT NULL CHECK(published_precision IN ('exact','date','unknown')),
  fetched_at TEXT NOT NULL,
  original_text TEXT,
  excerpt TEXT,
  fetch_version TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(document_id, revision),
  UNIQUE(document_id, content_sha256)
);
CREATE INDEX idx_k10_document_versions_fetched ON k10_source_document_versions(fetched_at);

CREATE TABLE k10_events (
  event_id TEXT PRIMARY KEY,
  stable_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);
CREATE TABLE k10_event_revisions (
  event_id TEXT NOT NULL REFERENCES k10_events(event_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  headline TEXT NOT NULL,
  event_kind TEXT NOT NULL,
  facts_json TEXT NOT NULL,
  source_refs_json TEXT NOT NULL,
  supersedes_revision INTEGER,
  created_at TEXT NOT NULL,
  PRIMARY KEY(event_id, revision)
);
CREATE TABLE k10_company_mappings (
  mapping_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  event_revision INTEGER NOT NULL,
  company_code TEXT NOT NULL,
  affected_stage TEXT NOT NULL,
  relation_evidence_json TEXT NOT NULL,
  inference_json TEXT NOT NULL,
  uncertainty TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(event_id, event_revision) REFERENCES k10_event_revisions(event_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_mappings_event ON k10_company_mappings(event_id, event_revision);

CREATE TABLE k10_scans (
  scan_id TEXT PRIMARY KEY,
  window_kind TEXT NOT NULL CHECK(window_kind IN ('evening','morning')),
  cutoff_at TEXT NOT NULL,
  config_id TEXT,
  config_revision INTEGER,
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','partial','failed','not_configured')),
  coverage_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(config_id, config_revision) REFERENCES k10_run_config_revisions(config_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_scans_window ON k10_scans(window_kind, cutoff_at DESC);
CREATE TABLE k10_source_watermark_updates (
  watermark_id TEXT PRIMARY KEY,
  source_key TEXT NOT NULL,
  cursor_value TEXT,
  success_cutoff_at TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  scan_id TEXT REFERENCES k10_scans(scan_id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_k10_watermark_source ON k10_source_watermark_updates(source_key, created_at DESC);

CREATE TABLE k10_candidates (
  candidate_id TEXT PRIMARY KEY,
  scan_id TEXT NOT NULL REFERENCES k10_scans(scan_id) ON DELETE RESTRICT,
  event_id TEXT NOT NULL,
  event_revision INTEGER NOT NULL,
  company_code TEXT NOT NULL,
  comparison_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(scan_id, event_id, event_revision, company_code),
  FOREIGN KEY(event_id, event_revision) REFERENCES k10_event_revisions(event_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_candidates_scan ON k10_candidates(scan_id, created_at);
CREATE TABLE k10_candidate_actions (
  action_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES k10_candidates(candidate_id) ON DELETE RESTRICT,
  action TEXT NOT NULL CHECK(action IN ('observe','skip','restore','withdraw')),
  idempotency_key TEXT NOT NULL UNIQUE,
  reason TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_k10_actions_candidate ON k10_candidate_actions(candidate_id, created_at);
CREATE TABLE k10_observations (
  observation_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES k10_candidates(candidate_id) ON DELETE RESTRICT,
  created_from_action_id TEXT NOT NULL UNIQUE REFERENCES k10_candidate_actions(action_id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_k10_observations_candidate ON k10_observations(candidate_id, created_at);

CREATE TABLE k10_analysis_revisions (
  analysis_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  analysis_kind TEXT NOT NULL CHECK(analysis_kind IN ('pro','con','morning')),
  input_cutoff_at TEXT NOT NULL,
  input_lineage_json TEXT NOT NULL,
  content_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','completed','partial','failed','not_configured')),
  created_at TEXT NOT NULL,
  UNIQUE(observation_id, revision, analysis_kind)
);
CREATE TABLE k10_plan_revisions (
  plan_revision_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  intent TEXT NOT NULL CHECK(intent IN ('draft','confirm','amend','abandon')),
  plan_json TEXT NOT NULL,
  data_cutoff_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(observation_id, revision)
);
CREATE TABLE k10_plan_commands (
  command_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
  idempotency_key TEXT NOT NULL UNIQUE,
  request_json TEXT NOT NULL,
  plan_revision_id TEXT NOT NULL UNIQUE REFERENCES k10_plan_revisions(plan_revision_id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL
);
CREATE TABLE k10_morning_updates (
  update_id TEXT PRIMARY KEY,
  observation_id TEXT REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
  candidate_id TEXT REFERENCES k10_candidates(candidate_id) ON DELETE RESTRICT,
  cutoff_at TEXT NOT NULL,
  content_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  CHECK(observation_id IS NOT NULL OR candidate_id IS NOT NULL)
);
CREATE TABLE k10_evaluation_records (
  evaluation_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
  policy_version TEXT NOT NULL,
  content_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE k10_tasks (
  task_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  input_version TEXT NOT NULL,
  input_cutoff_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','not_configured','cancelled')),
  stage TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
  error_text TEXT,
  budget_json TEXT NOT NULL,
  checkpoint_json TEXT NOT NULL,
  lease_owner TEXT,
  lease_until TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_k10_tasks_claim ON k10_tasks(status, lease_until, created_at);
CREATE TABLE k10_task_outbox (
  outbox_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL UNIQUE REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL,
  dispatched_at TEXT
);
"""


# V1.4 opportunity lifecycle.  These records are intentionally separate from a scan
# candidate: candidates are discovery snapshots; an opportunity starts only when a
# complete publication batch becomes visible.
_V2 = r"""
DROP TABLE IF EXISTS k10_plan_commands;
DROP TABLE IF EXISTS k10_plan_revisions;
DROP TABLE IF EXISTS k10_evaluation_records;

CREATE TABLE k10_publication_batches (
  batch_id TEXT PRIMARY KEY,
  scan_id TEXT NOT NULL REFERENCES k10_scans(scan_id) ON DELETE RESTRICT,
  publication_kind TEXT NOT NULL CHECK(publication_kind IN ('evening','morning')),
  available_at TEXT NOT NULL,
  input_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(scan_id, publication_kind)
);
CREATE INDEX idx_k10_batches_available ON k10_publication_batches(available_at, batch_id);

CREATE TABLE k10_company_windows (
  company_window_id TEXT PRIMARY KEY,
  company_code TEXT NOT NULL,
  first_batch_id TEXT NOT NULL REFERENCES k10_publication_batches(batch_id) ON DELETE RESTRICT,
  d0_trade_date TEXT NOT NULL,
  d1_trade_date TEXT NOT NULL,
  d2_trade_date TEXT NOT NULL,
  d1_selection_at TEXT NOT NULL,
  d2_close_at TEXT NOT NULL,
  sample_class TEXT NOT NULL CHECK(sample_class IN ('primary','overlap')),
  overlaps_window_id TEXT REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL,
  UNIQUE(company_code, first_batch_id),
  CHECK((sample_class='primary' AND overlaps_window_id IS NULL) OR
        (sample_class='overlap' AND overlaps_window_id IS NOT NULL))
);
CREATE INDEX idx_k10_windows_company_d1 ON k10_company_windows(company_code, d1_trade_date, d2_trade_date);

CREATE TABLE k10_opportunities (
  opportunity_id TEXT PRIMARY KEY,
  opportunity_key TEXT NOT NULL UNIQUE,
  company_code TEXT NOT NULL,
  catalyst_event_id TEXT NOT NULL,
  catalyst_event_revision INTEGER NOT NULL,
  catalyst_stage TEXT NOT NULL,
  related_opportunity_id TEXT REFERENCES k10_opportunities(opportunity_id) ON DELETE RESTRICT,
  first_batch_id TEXT NOT NULL REFERENCES k10_publication_batches(batch_id) ON DELETE RESTRICT,
  company_window_id TEXT NOT NULL REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(catalyst_event_id, catalyst_event_revision)
    REFERENCES k10_event_revisions(event_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_opportunities_company ON k10_opportunities(company_code, first_batch_id);

CREATE TABLE k10_publication_samples (
  sample_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES k10_publication_batches(batch_id) ON DELETE RESTRICT,
  candidate_id TEXT NOT NULL REFERENCES k10_candidates(candidate_id) ON DELETE RESTRICT,
  opportunity_id TEXT NOT NULL REFERENCES k10_opportunities(opportunity_id) ON DELETE RESTRICT,
  company_window_id TEXT NOT NULL REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  event_id TEXT NOT NULL,
  event_revision INTEGER NOT NULL,
  company_code TEXT NOT NULL,
  category TEXT NOT NULL,
  source_marker TEXT NOT NULL,
  comparison_json TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL,
  rank INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE(batch_id, candidate_id),
  UNIQUE(batch_id, opportunity_id),
  FOREIGN KEY(event_id, event_revision) REFERENCES k10_event_revisions(event_id, revision) ON DELETE RESTRICT
);
CREATE INDEX idx_k10_samples_window ON k10_publication_samples(company_window_id, batch_id);

CREATE TABLE k10_opportunity_lifecycle_events (
  lifecycle_event_id TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL REFERENCES k10_opportunities(opportunity_id) ON DELETE RESTRICT,
  kind TEXT NOT NULL CHECK(kind IN ('published','evidence_update','risk','withdrawal','expired')),
  reason TEXT,
  source_refs_json TEXT NOT NULL,
  content_json TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_k10_lifecycle_opportunity ON k10_opportunity_lifecycle_events(opportunity_id, occurred_at);

CREATE TABLE k10_company_window_selection_snapshots (
  company_window_id TEXT PRIMARY KEY REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  state TEXT NOT NULL CHECK(state IN ('selected','skipped','unhandled')),
  action_ids_json TEXT NOT NULL,
  frozen_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE k10_company_window_actions (
  action_id TEXT PRIMARY KEY,
  company_window_id TEXT NOT NULL REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  candidate_id TEXT NOT NULL REFERENCES k10_candidates(candidate_id) ON DELETE RESTRICT,
  candidate_action_id TEXT NOT NULL UNIQUE REFERENCES k10_candidate_actions(action_id) ON DELETE RESTRICT,
  action TEXT NOT NULL CHECK(action IN ('observe','skip','restore','withdraw')),
  idempotency_key TEXT NOT NULL UNIQUE,
  reason TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_k10_window_actions_window ON k10_company_window_actions(company_window_id, created_at);

CREATE TABLE k10_company_window_observations (
  company_window_id TEXT PRIMARY KEY REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  observation_id TEXT NOT NULL UNIQUE REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
  candidate_id TEXT NOT NULL REFERENCES k10_candidates(candidate_id) ON DELETE RESTRICT,
  task_id TEXT NOT NULL UNIQUE REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL
);

CREATE TABLE k10_market_day_fact_revisions (
  company_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  availability TEXT NOT NULL CHECK(availability IN ('available','suspended','data_gap')),
  open REAL,
  high REAL,
  low REAL,
  close REAL,
  pre_close REAL,
  limit_up_price REAL,
  close_limit_up INTEGER,
  touched_limit_up INTEGER,
  adj_factor REAL,
  metadata_json TEXT NOT NULL,
  source_refs_json TEXT NOT NULL,
  obtained_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(company_code, trade_date, revision)
);
CREATE INDEX idx_k10_market_facts_latest ON k10_market_day_fact_revisions(company_code, trade_date, revision DESC);

CREATE TABLE k10_company_window_evaluation_revisions (
  company_window_id TEXT NOT NULL REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  state TEXT NOT NULL CHECK(state IN ('pending','due','completed','incomplete')),
  fact_refs_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  evaluated_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(company_window_id, revision)
);
"""


# V3 is additive: it records morning reports and user-requested analysis follow-ups without
# rewriting V2 publication, fixed-window, selection, or analysis history.
_V3 = r"""
CREATE TABLE k10_morning_reports (
  report_id TEXT PRIMARY KEY,
  scan_id TEXT NOT NULL REFERENCES k10_scans(scan_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  cutoff_at TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('completed','partial','failed','not_configured')),
  coverage_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(scan_id, revision)
);
CREATE INDEX idx_k10_morning_reports_generated ON k10_morning_reports(scan_id, revision DESC, generated_at DESC, report_id);
CREATE TABLE k10_morning_report_items (
  report_id TEXT NOT NULL REFERENCES k10_morning_reports(report_id) ON DELETE RESTRICT,
  item_id TEXT NOT NULL,
  group_key TEXT NOT NULL CHECK(group_key IN ('major_contrary','thesis_changed','continuing_or_expiring','new','needs_review')),
  position INTEGER NOT NULL CHECK(position >= 0),
  opportunity_id TEXT REFERENCES k10_opportunities(opportunity_id) ON DELETE RESTRICT,
  company_window_id TEXT REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  status TEXT NOT NULL,
  content_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(report_id, item_id),
  UNIQUE(report_id, group_key, position)
);
CREATE INDEX idx_k10_morning_items_report ON k10_morning_report_items(report_id, group_key, position);

CREATE TABLE k10_analysis_requests (
  request_id TEXT PRIMARY KEY,
  company_window_id TEXT NOT NULL REFERENCES k10_company_windows(company_window_id) ON DELETE RESTRICT,
  observation_id TEXT NOT NULL REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
  task_id TEXT NOT NULL UNIQUE REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  idempotency_key TEXT NOT NULL UNIQUE,
  global_revision INTEGER NOT NULL CHECK(global_revision >= 1),
  parent_revision INTEGER CHECK(parent_revision >= 1),
  kind TEXT NOT NULL CHECK(kind IN ('user_question','evidence_update')),
  question TEXT,
  source_refs_json TEXT NOT NULL,
  task_input_version TEXT NOT NULL,
  task_payload_json TEXT NOT NULL,
  task_budget_json TEXT NOT NULL,
  input_cutoff_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(observation_id, global_revision),
  CHECK((kind='user_question' AND question IS NOT NULL AND length(trim(question)) > 0) OR kind='evidence_update')
);
CREATE INDEX idx_k10_analysis_requests_window ON k10_analysis_requests(company_window_id, global_revision);
"""

_DROP_V1 = (
    # Delete dependency children first.  This path is exercised only after a verified backup,
    # but must still work on a populated V1.4 database with foreign keys enabled.
    "k10_task_outbox", "k10_analysis_requests", "k10_morning_report_items", "k10_morning_reports", "k10_company_window_observations", "k10_tasks", "k10_evaluation_records", "k10_morning_updates",
    "k10_company_window_evaluation_revisions", "k10_market_day_fact_revisions", "k10_company_window_selection_snapshots", "k10_company_window_actions", "k10_opportunity_lifecycle_events", "k10_publication_samples", "k10_opportunities", "k10_company_windows", "k10_publication_batches", "k10_plan_commands", "k10_plan_revisions", "k10_analysis_revisions", "k10_observations", "k10_candidate_actions",
    "k10_candidates", "k10_source_watermark_updates", "k10_scans", "k10_company_mappings",
    "k10_event_revisions", "k10_events", "k10_source_document_versions", "k10_source_documents",
    "k10_run_config_revisions",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(db_path: Path) -> Path:
    if not isinstance(db_path, Path):
        raise TypeError("K10 数据库必须显式传入 pathlib.Path")
    return db_path


@contextmanager
def write_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """受控写连接；仅迁移和 store 写入口可使用。"""
    path = _path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    # sqlite3 的 executescript 会隐式提交，不能用它承载迁移。本连接先显式开启
    # 事务，所有 DDL 都逐条执行，因此任一失败会回滚整套版本而非遗留半套表。
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def read_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """严格只读：不存在或未迁移的库给调用方明确错误，零 DDL。"""
    path = _path(db_path)
    if not path.exists():
        raise SchemaUnavailable(f"K10 schema 不存在：{path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise SchemaUnavailable(f"K10 schema 无法只读打开：{path}") from exc
    try:
        yield conn
    finally:
        conn.close()


def _version(conn: sqlite3.Connection) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='k10_schema_migrations'"
    ).fetchone()
    if exists is None:
        return 0
    row = conn.execute("SELECT MAX(version) FROM k10_schema_migrations").fetchone()
    return int(row[0] or 0)


def require_schema(conn: sqlite3.Connection) -> None:
    version = _version(conn)
    if version != SCHEMA_VERSION:
        raise SchemaUnavailable(
            f"K10 schema 版本为 {version or '未建立'}，当前需要 {SCHEMA_VERSION}；请通过受控迁移建立"
        )


def schema_version(db_path: Path) -> int:
    with read_connection(db_path) as conn:
        require_schema(conn)
        return _version(conn)


def initialize_schema(db_path: Path) -> int:
    """受控前滚至当前 K10 schema；可重复执行，绝不由读取或 API GET 调用。"""
    with write_connection(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS k10_schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        version = _version(conn)
        if version > SCHEMA_VERSION:
            raise K10SchemaError(f"数据库 K10 schema {version} 比运行时 {SCHEMA_VERSION} 新")
        if version == 0:
            _apply_v1(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (1,?)", (_now(),))
            _apply_v2(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (2,?)", (_now(),))
            _apply_v3(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (3,?)", (_now(),))
        elif version == 1:
            _apply_v2(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (2,?)", (_now(),))
            _apply_v3(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (3,?)", (_now(),))
        elif version == 2:
            _apply_v3(conn)
            conn.execute("INSERT INTO k10_schema_migrations(version, applied_at) VALUES (3,?)", (_now(),))
        elif version != SCHEMA_VERSION:
            raise K10SchemaError(f"缺少从 K10 schema {version} 到 {SCHEMA_VERSION} 的迁移")
    return SCHEMA_VERSION


def _apply_v1(conn: sqlite3.Connection) -> None:
    """在调用方已开启的事务中逐条执行 V1 DDL，禁止 ``executescript`` 隐式提交。"""
    for statement in _V1.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def _apply_v2(conn: sqlite3.Connection) -> None:
    """V1.4 lifecycle migration: remove retired plan projections and add opportunity facts."""
    for statement in _V2.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def _apply_v3(conn: sqlite3.Connection) -> None:
    """Add V3 ledgers and preserve raw anomalous market facts during the CHECK upgrade."""
    _upgrade_market_fact_availability(conn)
    _upgrade_analysis_revision_attempts(conn)
    for statement in _V3.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def _upgrade_market_fact_availability(conn: sqlite3.Connection) -> None:
    """Allow the explicit ``anomaly`` raw-fact state while proving the V2 rows survive intact."""
    rows = conn.execute("SELECT * FROM k10_market_day_fact_revisions ORDER BY company_code,trade_date,revision").fetchall()
    before = sha256(repr([tuple(row) for row in rows]).encode("utf-8")).hexdigest()
    conn.execute("ALTER TABLE k10_market_day_fact_revisions RENAME TO k10_market_day_fact_revisions_v2")
    conn.execute("""
        CREATE TABLE k10_market_day_fact_revisions (
          company_code TEXT NOT NULL,
          trade_date TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK(revision >= 1),
          availability TEXT NOT NULL CHECK(availability IN ('available','suspended','data_gap','anomaly')),
          open REAL, high REAL, low REAL, close REAL, pre_close REAL, limit_up_price REAL,
          close_limit_up INTEGER, touched_limit_up INTEGER, adj_factor REAL,
          metadata_json TEXT NOT NULL, source_refs_json TEXT NOT NULL, obtained_at TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(company_code, trade_date, revision)
        )
    """)
    conn.execute("INSERT INTO k10_market_day_fact_revisions SELECT * FROM k10_market_day_fact_revisions_v2")
    restored = conn.execute("SELECT * FROM k10_market_day_fact_revisions ORDER BY company_code,trade_date,revision").fetchall()
    after = sha256(repr([tuple(row) for row in restored]).encode("utf-8")).hexdigest()
    if len(rows) != len(restored) or before != after or conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise K10SchemaError("行情事实 v2→v3 重建核验失败")
    conn.execute("DROP TABLE k10_market_day_fact_revisions_v2")
    conn.execute("CREATE INDEX idx_k10_market_facts_latest ON k10_market_day_fact_revisions(company_code, trade_date, revision DESC)")


def _downgrade_market_fact_availability(conn: sqlite3.Connection) -> None:
    """Rebuild the V2 CHECK only when it cannot discard an anomaly fact."""
    if conn.execute("SELECT 1 FROM k10_market_day_fact_revisions WHERE availability='anomaly' LIMIT 1").fetchone() is not None:
        raise K10SchemaError("schema 3 含 anomaly 行情事实，须恢复已核备份，不能降级丢失数据")
    rows = conn.execute("SELECT * FROM k10_market_day_fact_revisions ORDER BY company_code,trade_date,revision").fetchall()
    digest = sha256(repr([tuple(row) for row in rows]).encode("utf-8")).hexdigest()
    conn.execute("ALTER TABLE k10_market_day_fact_revisions RENAME TO k10_market_day_fact_revisions_v3")
    conn.execute("""
        CREATE TABLE k10_market_day_fact_revisions (
          company_code TEXT NOT NULL, trade_date TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1),
          availability TEXT NOT NULL CHECK(availability IN ('available','suspended','data_gap')),
          open REAL, high REAL, low REAL, close REAL, pre_close REAL, limit_up_price REAL,
          close_limit_up INTEGER, touched_limit_up INTEGER, adj_factor REAL, metadata_json TEXT NOT NULL,
          source_refs_json TEXT NOT NULL, obtained_at TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(company_code, trade_date, revision)
        )
    """)
    conn.execute("INSERT INTO k10_market_day_fact_revisions SELECT * FROM k10_market_day_fact_revisions_v3")
    restored = conn.execute("SELECT * FROM k10_market_day_fact_revisions ORDER BY company_code,trade_date,revision").fetchall()
    if len(rows) != len(restored) or digest != sha256(repr([tuple(row) for row in restored]).encode("utf-8")).hexdigest() or conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise K10SchemaError("行情事实 v3→v2 重建核验失败")
    conn.execute("DROP TABLE k10_market_day_fact_revisions_v3")
    conn.execute("CREATE INDEX idx_k10_market_facts_latest ON k10_market_day_fact_revisions(company_code, trade_date, revision DESC)")


def _upgrade_analysis_revision_attempts(conn: sqlite3.Connection) -> None:
    """Retain failed/successful retry attempts at one immutable global analysis revision."""
    rows = conn.execute("SELECT * FROM k10_analysis_revisions ORDER BY rowid").fetchall()
    digest = sha256(repr([tuple(row) for row in rows]).encode("utf-8")).hexdigest()
    conn.execute("ALTER TABLE k10_analysis_revisions RENAME TO k10_analysis_revisions_v2")
    conn.execute("""
        CREATE TABLE k10_analysis_revisions (
          analysis_id TEXT PRIMARY KEY,
          observation_id TEXT NOT NULL REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
          revision INTEGER NOT NULL CHECK(revision >= 1),
          analysis_kind TEXT NOT NULL CHECK(analysis_kind IN ('pro','con','morning')),
          input_cutoff_at TEXT NOT NULL, input_lineage_json TEXT NOT NULL, content_json TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('queued','completed','partial','failed','not_configured')),
          created_at TEXT NOT NULL
        )
    """)
    conn.execute("INSERT INTO k10_analysis_revisions SELECT * FROM k10_analysis_revisions_v2")
    restored = conn.execute("SELECT * FROM k10_analysis_revisions ORDER BY rowid").fetchall()
    if len(rows) != len(restored) or digest != sha256(repr([tuple(row) for row in restored]).encode("utf-8")).hexdigest() or conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise K10SchemaError("分析修订 v2→v3 重建核验失败")
    conn.execute("DROP TABLE k10_analysis_revisions_v2")
    conn.execute("CREATE INDEX idx_k10_analysis_attempts_latest ON k10_analysis_revisions(observation_id, revision, analysis_kind, created_at DESC, analysis_id DESC)")


def _downgrade_analysis_revision_attempts(conn: sqlite3.Connection) -> None:
    duplicates = conn.execute(
        "SELECT 1 FROM k10_analysis_revisions GROUP BY observation_id,revision,analysis_kind HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    if duplicates is not None:
        raise K10SchemaError("schema 3 含分析重试历史，须恢复已核备份，不能降级丢失尝试")
    rows = conn.execute("SELECT * FROM k10_analysis_revisions ORDER BY rowid").fetchall()
    digest = sha256(repr([tuple(row) for row in rows]).encode("utf-8")).hexdigest()
    conn.execute("ALTER TABLE k10_analysis_revisions RENAME TO k10_analysis_revisions_v3")
    conn.execute("""
        CREATE TABLE k10_analysis_revisions (
          analysis_id TEXT PRIMARY KEY,
          observation_id TEXT NOT NULL REFERENCES k10_observations(observation_id) ON DELETE RESTRICT,
          revision INTEGER NOT NULL CHECK(revision >= 1),
          analysis_kind TEXT NOT NULL CHECK(analysis_kind IN ('pro','con','morning')),
          input_cutoff_at TEXT NOT NULL, input_lineage_json TEXT NOT NULL, content_json TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('queued','completed','partial','failed','not_configured')),
          created_at TEXT NOT NULL,
          UNIQUE(observation_id, revision, analysis_kind)
        )
    """)
    conn.execute("INSERT INTO k10_analysis_revisions SELECT * FROM k10_analysis_revisions_v3")
    restored = conn.execute("SELECT * FROM k10_analysis_revisions ORDER BY rowid").fetchall()
    if len(rows) != len(restored) or digest != sha256(repr([tuple(row) for row in restored]).encode("utf-8")).hexdigest() or conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise K10SchemaError("分析修订 v3→v2 重建核验失败")
    conn.execute("DROP TABLE k10_analysis_revisions_v3")


def rollback_schema(db_path: Path, *, target_version: int = 0) -> int:
    """仅用于演练/受控回滚；调用方必须先完成备份验证。"""
    if target_version not in {0, 2}:
        raise ValueError("当前 K10 仅支持回滚到 schema 0 或 2")
    with write_connection(db_path) as conn:
        version = _version(conn)
        if target_version == 2:
            if version == 2:
                return 2
            if version != 3:
                raise K10SchemaError(f"不能从未知 K10 schema {version} 回滚到 2")
            # This is a controlled DDL rollback for rehearsal only. Production rollback must
            # restore the verified pre-migration backup so no V3 ledger is silently discarded.
            for table in ("k10_analysis_requests", "k10_morning_report_items", "k10_morning_reports"):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            _downgrade_analysis_revision_attempts(conn)
            _downgrade_market_fact_availability(conn)
            conn.execute("DELETE FROM k10_schema_migrations WHERE version=3")
            return 2
        if version == 0:
            return 0
        if version != SCHEMA_VERSION:
            raise K10SchemaError(f"不能回滚未知 K10 schema {version}")
        for table in _DROP_V1:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("DROP TABLE IF EXISTS k10_schema_migrations")
    return 0


__all__ = [
    "K10SchemaError", "SCHEMA_VERSION", "SchemaUnavailable", "initialize_schema",
    "read_connection", "require_schema", "rollback_schema", "schema_version", "write_connection",
]
