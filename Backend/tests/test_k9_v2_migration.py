"""K9-v1 invalidation is exact, reversible, and leaves market facts untouched."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from neckline.db import init_schema


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_k9_v2.py"
SPEC = importlib.util.spec_from_file_location("migrate_k9_v2", SCRIPT)
assert SPEC and SPEC.loader
MIGRATION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MIGRATION
SPEC.loader.exec_module(MIGRATION)

RUNS = (
    MIGRATION.ExpectedRun("20260821", "20260823", "run-friday"),
    MIGRATION.ExpectedRun("20260824", "20260824", "run-monday"),
)
PARAMS = "k9-params-20260823-r2"


def _seed_old_database(root: Path) -> tuple[Path, Path]:
    db = root / "neckline.db"
    reports = root / "reports"
    reports.mkdir()
    init_schema(db)
    with sqlite3.connect(db) as conn:
        for table in sorted(MIGRATION.CURRENT_K9_TABLES, reverse=True):
            conn.execute(f'DROP TABLE "{table}"')
        conn.executescript("""
        CREATE TABLE k9_runs (
          run_id TEXT, trade_date TEXT PRIMARY KEY, strategy TEXT,
          params_package_version TEXT, pack_id TEXT, pack_version TEXT,
          created_at TEXT
        );
        CREATE TABLE k9_reports (
          trade_date TEXT PRIMARY KEY, report_date TEXT, state TEXT,
          markdown TEXT, structured_json TEXT, params_package_version TEXT,
          pack_id TEXT, pack_version TEXT, generated_at TEXT
        );
        CREATE TABLE k9_listing_entries (
          trade_date TEXT, ts_code TEXT, run_id TEXT, strategy TEXT, rank INTEGER
        );
        CREATE TABLE k9_channel_hits (
          id INTEGER PRIMARY KEY, run_id TEXT, trade_date TEXT, ts_code TEXT, pattern TEXT
        );
        CREATE TABLE k9_checklists (trade_date TEXT, d0_date TEXT, checklist_json TEXT);
        CREATE TABLE k9_d1_verdicts (trade_date TEXT, d0_date TEXT, ts_code TEXT, verdict TEXT);
        CREATE TABLE k9_followups (d0_date TEXT, d4_date TEXT, ts_code TEXT, verdict TEXT);
        CREATE TABLE k9_coverage_daily (trade_date TEXT, listing_trade_date TEXT);
        CREATE TABLE k9_coverage_misses (trade_date TEXT, ts_code TEXT, reason TEXT);
        CREATE TABLE k9_explain_notes (trade_date TEXT, ts_code TEXT, profile_json TEXT);
        CREATE TABLE k9_explain_audit (id INTEGER PRIMARY KEY, trade_date TEXT, action TEXT);
        CREATE TABLE k9_playbooks (trade_date TEXT, ts_code TEXT, version INTEGER);
        """)
        for run in RUNS:
            pack_id = f"pack-{run.trade_date}"
            conn.execute(
                "INSERT INTO k9_runs VALUES (?,?,?,?,?,?,?)",
                (run.run_id, run.trade_date, "K9", PARAMS, pack_id, "fp-2", "old"),
            )
            conn.execute(
                "INSERT INTO k9_reports VALUES (?,?,?,?,?,?,?,?,?)",
                (run.trade_date, run.report_date, "has_list", f"report {run.trade_date}",
                 "{}", PARAMS, pack_id, "fp-2", "old"),
            )
            conn.execute(
                "INSERT INTO k9_listing_entries VALUES (?,?,?,?,?)",
                (run.trade_date, f"code-{run.trade_date}", run.run_id, "K9", 1),
            )
            conn.execute(
                "INSERT INTO k9_channel_hits(run_id,trade_date,ts_code,pattern) VALUES (?,?,?,?)",
                (run.run_id, run.trade_date, f"code-{run.trade_date}", "p1"),
            )
            conn.execute(
                "INSERT INTO fact_direction_briefings "
                "(pack_id,trade_date,state,summary,themes_json,evidence_count,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (pack_id, run.trade_date, "ready", "old direction", "[]", 0, "old"),
            )
            conn.execute(
                "INSERT INTO llm_usage_events "
                "(trade_date,report_date,pack_id,task,outcome,usage_unavailable,searched,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (run.trade_date, run.report_date, pack_id, "explain", "success", 1, 0, "old"),
            )
            (reports / f"{run.report_date}.md").write_text(
                f"old report {run.trade_date}\n", encoding="utf-8")
        conn.execute(
            "INSERT INTO fact_packs "
            "(pack_id,trade_date,pack_version,origin,state,content_fingerprint,row_count,"
            "sources_json,market_json,suspend_anomaly_count,frozen_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("raw-pack", "20260824", "fp-2", "live", "frozen", "raw-sha", 2,
             "[]", "{}", 0, "old"),
        )
        conn.execute(
            "INSERT INTO job_events(trade_date,scope,ts_code,event_key,payload_json,pushed_at) "
            "VALUES (?,?,?,?,?,?)",
            ("20260824", "auction", "", "settle_tick", "{}", "old"),
        )
        conn.execute(
            "INSERT INTO job_events(trade_date,scope,ts_code,event_key,payload_json,pushed_at) "
            "VALUES (?,?,?,?,?,?)",
            ("20260820", "unrelated", "", "keep", "{}", "old"),
        )
        conn.execute(
            "INSERT INTO fact_direction_briefings "
            "(pack_id,trade_date,state,summary,themes_json,evidence_count,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("unrelated-pack", "20260820", "ready", "keep", "[]", 0, "old"),
        )
        conn.execute(
            "INSERT INTO llm_usage_events "
            "(trade_date,report_date,pack_id,task,outcome,usage_unavailable,searched,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("20260820", "20260820", "unrelated-pack", "explain", "success", 1, 0, "old"),
        )
    return db, reports


def _k9_counts(db: Path) -> dict[str, int]:
    with sqlite3.connect(db) as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'k9_%'")}
        return {table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in names}


def test_apply_archives_exact_runs_and_starts_k9_v2_empty(tmp_path: Path):
    db, reports = _seed_old_database(tmp_path)
    archive = tmp_path / "archive"

    result = MIGRATION.apply(
        db_path=db, archive_dir=archive, reports_dir=reports,
        expected_runs=RUNS, expected_params_package=PARAMS,
    )

    assert result["status"] == "applied"
    assert result["rawMarketSnapshots"] == "untouched"
    assert set(_k9_counts(db)) == MIGRATION.CURRENT_K9_TABLES
    assert all(count == 0 for count in _k9_counts(db).values())
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT content_fingerprint FROM fact_packs").fetchone()[0] == "raw-sha"
        assert conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM llm_usage_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM fact_direction_briefings").fetchone()[0] == 1
    assert not any((reports / f"{run.report_date}.md").exists() for run in RUNS)

    bundle = json.loads((archive / "invalidated-k9-v1.json").read_text(encoding="utf-8"))
    assert bundle["status"] == "invalidated"
    assert bundle["reason"] == "superseded_by_k9-v2"
    assert {row["archive_status"] for row in bundle["tables"]["k9_runs"]} == {"invalidated"}
    assert {row["archive_reason"] for row in bundle["tables"]["k9_reports"]} == {
        "superseded_by_k9-v2"}
    assert {row["identity"] for row in bundle["preservedFormalHistory"]} == {
        "K9-v1", "k9-params-20260822-r1"}
    assert (archive / "rollback" / "neckline.pre-k9-v2.db").is_file()


def test_restore_recovers_old_database_and_reports(tmp_path: Path):
    db, reports = _seed_old_database(tmp_path)
    archive = tmp_path / "archive"
    MIGRATION.apply(
        db_path=db, archive_dir=archive, reports_dir=reports,
        expected_runs=RUNS, expected_params_package=PARAMS,
    )

    result = MIGRATION.restore(db_path=db, archive_dir=archive, reports_dir=reports)

    assert result["status"] == "restored"
    with sqlite3.connect(db) as conn:
        assert {row[0] for row in conn.execute("SELECT run_id FROM k9_runs")} == {
            "run-friday", "run-monday"}
        assert "strategy_version" not in {
            row[1] for row in conn.execute("PRAGMA table_info(k9_runs)")}
        assert conn.execute("SELECT COUNT(*) FROM llm_usage_events").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM fact_packs").fetchone()[0] == 1
    assert all((reports / f"{run.report_date}.md").is_file() for run in RUNS)
    assert (archive / "rollback" / "neckline.before-restore.db").is_file()


def test_dry_run_and_wrong_identity_never_mutate(tmp_path: Path):
    db, reports = _seed_old_database(tmp_path)
    archive = tmp_path / "archive"
    result = MIGRATION.apply(
        db_path=db, archive_dir=archive, reports_dir=reports,
        expected_runs=RUNS, expected_params_package=PARAMS, dry_run=True,
    )
    assert result["action"] == "dry-run"
    assert not archive.exists()
    assert _k9_counts(db)["k9_runs"] == 2

    wrong = (*RUNS[:1], MIGRATION.ExpectedRun("20260824", "20260824", "wrong-id"))
    with pytest.raises(MIGRATION.MigrationRefused, match="run id mismatch"):
        MIGRATION.apply(
            db_path=db, archive_dir=archive, reports_dir=reports,
            expected_runs=wrong, expected_params_package=PARAMS,
        )
    assert not archive.exists()
    assert _k9_counts(db)["k9_runs"] == 2

