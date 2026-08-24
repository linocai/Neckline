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

CREATE TABLE IF NOT EXISTS k9_coverage_daily (
  trade_date TEXT PRIMARY KEY,
  pack_id TEXT NOT NULL,
  pack_version TEXT NOT NULL,
  limit_up_count INTEGER NOT NULL,
  limit_down_count INTEGER NOT NULL,
  zaban_count INTEGER NOT NULL,
  zaban_rate REAL,
  max_consec_days INTEGER,
  cluster_count INTEGER NOT NULL,
  listing_trade_date TEXT,
  listing_size INTEGER,
  covered_count INTEGER,
  coverage_all REAL,
  in_pool_denominator INTEGER,
  covered_in_pool INTEGER,
  coverage_in_pool REAL,
  census_json TEXT NOT NULL,
  computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS k9_coverage_misses (
  trade_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  name TEXT,
  sw_l2_code TEXT,
  sw_l2_name TEXT,
  board TEXT,
  consec_limit_up_days INTEGER,
  reason TEXT NOT NULL,
  detail TEXT,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_k9_coverage_misses_reason
  ON k9_coverage_misses(trade_date, reason);

CREATE TABLE IF NOT EXISTS k9_runs (
  run_id TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  strategy TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  label_contract_version TEXT NOT NULL,
  params_package_version TEXT NOT NULL,
  pack_id TEXT NOT NULL,
  pack_version TEXT NOT NULL,
  tier_used TEXT NOT NULL,
  strict_candidates INTEGER NOT NULL,
  relaxed_candidates INTEGER NOT NULL,
  seated_count INTEGER NOT NULL,
  capacity_short INTEGER NOT NULL,
  over_strict INTEGER NOT NULL,
  relaxed_streak INTEGER NOT NULL,
  channel_counts_json TEXT NOT NULL,
  boundary_counts_json TEXT NOT NULL,
  absent_patterns_json TEXT NOT NULL,
  dropped_heat_absent_json TEXT NOT NULL,
  listing_finalized_by TEXT NOT NULL,
  scoring_contract_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, strategy, strategy_version)
);
CREATE INDEX IF NOT EXISTS idx_k9_runs_run_id ON k9_runs(run_id);

CREATE TABLE IF NOT EXISTS k9_channel_hits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  pattern TEXT NOT NULL,
  tier TEXT NOT NULL,
  seated INTEGER NOT NULL,
  strength_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  risks_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_k9_channel_hits_day
  ON k9_channel_hits(trade_date, pattern);
CREATE INDEX IF NOT EXISTS idx_k9_channel_hits_code
  ON k9_channel_hits(ts_code, trade_date);

CREATE TABLE IF NOT EXISTS k9_listing_entries (
  trade_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  run_id TEXT NOT NULL,
  strategy TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  name TEXT,
  sw_l2_code TEXT,
  sw_l2_name TEXT,
  patterns_json TEXT NOT NULL,
  primary_pattern TEXT NOT NULL,
  tier TEXT NOT NULL,
  seat_kind TEXT,
  rank INTEGER NOT NULL,
  score REAL NOT NULL,
  industry_heat_score REAL,
  pattern_strength_score REAL NOT NULL,
  relay_score REAL NOT NULL,
  evidence_json TEXT NOT NULL,
  risks_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, ts_code, strategy_version)
);
CREATE INDEX IF NOT EXISTS idx_k9_listing_day
  ON k9_listing_entries(trade_date, strategy);

CREATE TABLE IF NOT EXISTS k9_reports (
  trade_date TEXT PRIMARY KEY,
  report_date TEXT NOT NULL,
  state TEXT NOT NULL,
  headline TEXT NOT NULL,
  gaps_json TEXT NOT NULL,
  markdown TEXT NOT NULL,
  structured_json TEXT NOT NULL,
  strategy TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  params_package_version TEXT,
  pack_id TEXT,
  pack_version TEXT,
  listing_size INTEGER,
  strict_count INTEGER,
  relaxed_count INTEGER,
  generated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_k9_reports_report_date ON k9_reports(report_date);

CREATE TABLE IF NOT EXISTS k9_playbooks (
  trade_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  version INTEGER NOT NULL,
  source TEXT NOT NULL,
  pattern TEXT NOT NULL,
  first_resistance REAL NOT NULL,
  second_resistance REAL NOT NULL,
  invalidation REAL NOT NULL,
  branches_json TEXT NOT NULL,
  filled_by TEXT NOT NULL,
  filled_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, ts_code, version)
);
CREATE INDEX IF NOT EXISTS idx_k9_playbooks_day ON k9_playbooks(trade_date);

CREATE TABLE IF NOT EXISTS k9_explain_notes (
  trade_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  profile_json TEXT NOT NULL,
  kline_comment TEXT NOT NULL,
  news_state TEXT NOT NULL,
  news_category TEXT,
  news_json TEXT NOT NULL,
  llm_ok INTEGER NOT NULL,
  filled_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, ts_code)
);

CREATE TABLE IF NOT EXISTS k9_explain_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT NOT NULL,
  seq INTEGER NOT NULL,
  round_no INTEGER NOT NULL,
  action TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_k9_explain_audit_day
  ON k9_explain_audit(trade_date, seq);

CREATE TABLE IF NOT EXISTS k9_checklists (
  trade_date TEXT NOT NULL,
  strategy TEXT NOT NULL,
  d0_date TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  data_quality TEXT NOT NULL,
  rejected_count INTEGER NOT NULL,
  pending_count INTEGER NOT NULL,
  checklist_json TEXT NOT NULL,
  notes_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, strategy)
);

CREATE TABLE IF NOT EXISTS k9_d1_verdicts (
  trade_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  strategy TEXT NOT NULL,
  d0_date TEXT NOT NULL,
  pattern TEXT NOT NULL,
  playbook_version INTEGER NOT NULL,
  auction_verdict TEXT,
  auction_readings_json TEXT,
  auction_branch_json TEXT,
  auction_at TEXT,
  verdict TEXT,
  decided_stage TEXT,
  open30_readings_json TEXT,
  open30_branches_json TEXT,
  settled_at TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, ts_code, strategy)
);
CREATE INDEX IF NOT EXISTS idx_k9_d1_verdicts_stage
  ON k9_d1_verdicts(trade_date, decided_stage);

CREATE TABLE IF NOT EXISTS k9_predictions (
  d0_date TEXT NOT NULL,
  d1_date TEXT NOT NULL,
  d2_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  strategy TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  label_contract_version TEXT NOT NULL,
  params_package_version TEXT NOT NULL,
  pack_id TEXT NOT NULL,
  pack_version TEXT NOT NULL,
  cohort TEXT NOT NULL,
  primary_pattern TEXT,
  name TEXT,
  sw_l2_code TEXT,
  sw_l2_name TEXT,
  d0_close REAL,
  d2_close REAL,
  max_high_d1_d2 REAL,
  min_low_d1_d2 REAL,
  touch_up INTEGER,
  close_win INTEGER,
  path_state TEXT,
  stock_d2_return REAL,
  industry_d2_return REAL,
  industry_excess REAL,
  max_drawdown REAL,
  d1_verdict TEXT,
  evaluable INTEGER NOT NULL,
  unavailable_reason TEXT,
  computed_at TEXT NOT NULL,
  d1_reference_price REAL,
  d1_touch_up INTEGER,
  PRIMARY KEY (d0_date, ts_code, strategy_version, cohort)
);
CREATE INDEX IF NOT EXISTS idx_k9_predictions_d2
  ON k9_predictions(d2_date, strategy, strategy_version);
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
    """受控写入口：仅建立当前 K9 schema。"""
    with connection(db_path) as conn:
        conn.executescript(_SCHEMA)


__all__ = [
    "get_connection", "connection", "readonly_connection", "readonly_tables", "init_schema"
]
