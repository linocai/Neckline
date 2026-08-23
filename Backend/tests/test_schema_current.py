"""数据库只保留现役 K9、复盘、设置与基础数据表。"""

from __future__ import annotations

import sqlite3

from neckline.db import init_schema


ACTIVE_TABLES = {
    "app_settings", "backfill_log", "devices", "fact_packs", "fact_direction_briefings", "k9_channel_hits",
    "k9_checklists", "k9_coverage_daily", "k9_coverage_misses", "k9_d1_verdicts",
    "k9_explain_audit", "k9_explain_notes", "k9_followups", "k9_listing_entries",
    "k9_playbooks", "k9_reports", "k9_runs", "llm_providers", "llm_usage_events", "namechange",
    "review_conclusions", "reviews", "job_events", "stock_basic",
    "sw_industry_classify", "sw_industry_daily", "sw_industry_member", "trade_cal",
}


def _tables(path):
    with sqlite3.connect(path) as conn:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def test_fresh_schema_contains_exactly_the_active_tables(tmp_path):
    db = tmp_path / "fresh.db"
    init_schema(db)
    assert _tables(db) == ACTIVE_TABLES


def test_init_schema_is_idempotent(tmp_path):
    db = tmp_path / "repeat.db"
    init_schema(db)
    first = _tables(db)
    init_schema(db)
    assert _tables(db) == first == ACTIVE_TABLES


def test_migration_physically_drops_retired_tables_and_columns(tmp_path):
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE baskets(id TEXT);
            CREATE TABLE reports(trade_date TEXT);
            CREATE TABLE positions(id INTEGER);
            CREATE TABLE selection_runs(run_id TEXT PRIMARY KEY);
            CREATE TABLE selection_directions(
              run_id TEXT REFERENCES selection_runs(run_id));
            CREATE TABLE selection_direction_events(
              run_id TEXT REFERENCES selection_runs(run_id));
            CREATE TABLE selection_llm_calls(
              run_id TEXT REFERENCES selection_runs(run_id));
            CREATE TABLE selection_search_calls(
              run_id TEXT REFERENCES selection_runs(run_id));
            CREATE TABLE app_settings(
              id INTEGER PRIMARY KEY, push_report INTEGER, push_retreat INTEGER,
              tavily_api_key TEXT, review_col_map TEXT, updated_at TEXT,
              llm_default_provider TEXT, llm_task_routes TEXT, push_kinds TEXT
            );
            INSERT INTO app_settings VALUES
              (1,1,1,'tvly','{}','now','deepseek','{}','{}');
        """)
    init_schema(db)
    assert _tables(db) == ACTIVE_TABLES
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(app_settings)")}
        row = conn.execute(
            "SELECT tavily_api_key,llm_default_provider FROM app_settings WHERE id=1"
        ).fetchone()
    assert columns == {"id", "tavily_api_key", "review_col_map", "updated_at",
                       "llm_default_provider", "llm_task_routes", "push_kinds"}
    assert row == ("tvly", "deepseek")
