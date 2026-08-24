"""数据库只保留现役 K9、复盘、设置与基础数据表。"""

from __future__ import annotations

import sqlite3

from neckline.db import init_schema


ACTIVE_TABLES = {
    "app_settings", "backfill_log", "devices", "fact_packs", "fact_direction_briefings", "k9_channel_hits",
    "k9_checklists", "k9_coverage_daily", "k9_coverage_misses", "k9_d1_verdicts",
    "k9_explain_audit", "k9_explain_notes", "k9_predictions", "k9_listing_entries",
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


def test_predictions_freeze_full_strategy_provenance(tmp_path):
    db = tmp_path / "provenance.db"
    init_schema(db)
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(k9_predictions)")}
    assert {
        "strategy_version", "label_contract_version", "params_package_version",
        "pack_id", "pack_version",
    } <= columns
