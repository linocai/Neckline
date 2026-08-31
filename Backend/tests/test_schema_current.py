"""数据库只保留现役 K9、复盘、设置与基础数据表。"""

from __future__ import annotations

import sqlite3

from neckline.db import init_schema


ACTIVE_TABLES = {
    "app_settings", "backfill_log", "devices", "fact_packs", "fact_direction_briefings",
    "llm_providers", "llm_usage_events", "namechange",
    "k9_selection_batches", "k9_selection_candidates", "k9_playbook_revisions", "k9_playbook_freezes", "k9_selection_d1", "k9_selection_d1_close", "k9_selection_d2", "k9_package_checklists", "k9_package_reports",
    "k9_d0_run_markers", "k9_lifecycle_attempts", "k9_lifecycle_stages", "k9_intraday_capture_audit",
    "review_conclusions", "reviews", "job_events", "job_event_deliveries", "stock_basic",
    "sw_industry_classify", "sw_industry_daily", "sw_industry_member", "sw_industry_member_snapshots", "sw_industry_snapshot_imports", "sw_industry_snapshot_manifests", "trade_cal",
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


def test_v3_batches_freeze_full_strategy_provenance(tmp_path):
    db = tmp_path / "provenance.db"
    init_schema(db)
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(k9_selection_batches)")}
    assert {
        "strategy_version", "label_contract_version", "params_package_version",
        "pack_id", "pack_version",
    } <= columns
