"""通用 SQLite schema only contains V3-shared settings and market metadata."""

from __future__ import annotations

import sqlite3

from neckline.db import init_schema


ACTIVE_TABLES = {
    "app_settings", "backfill_log", "devices", "llm_providers", "llm_usage_events", "namechange", "stock_basic",
    "sw_industry_classify", "sw_industry_member", "sw_industry_member_snapshots", "sw_industry_snapshot_imports",
    "sw_industry_snapshot_manifests", "trade_cal",
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


def test_common_schema_excludes_strategy_and_personal_review_tables(tmp_path):
    db = tmp_path / "common.db"
    init_schema(db)
    names = _tables(db)
    assert not any(name.startswith(("k9_", "fact_")) for name in names)
    assert not {"reviews", "review_conclusions", "job_events", "job_event_deliveries"} & names
