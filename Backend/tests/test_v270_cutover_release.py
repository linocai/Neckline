"""V2.7 release boundary: explicit legacy migration, identity, and no-DDL reads."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from neckline.api.app import RELEASE_SET, VERSION, app
from neckline.db import init_schema


ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "Backend" / "scripts" / "migrate_k9_v2.py"


def _migration_module():
    spec = importlib.util.spec_from_file_location("v270_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_db(path: Path, *, settled: bool) -> None:
    # Make every protected table pre-exist.  The release tool may establish V3
    # tables, but it is forbidden to change these facts/settings rows or identity.
    module = _migration_module()
    init_schema(path)
    with sqlite3.connect(path) as conn:
        for table in module.V3_TABLES:
            conn.execute(f'DROP TABLE "{table}"')
        conn.execute("CREATE TABLE k9_predictions (d0_trade_date TEXT, ts_code TEXT, state TEXT, params_package_version TEXT)")
        conn.execute("INSERT INTO k9_predictions VALUES ('20260828','000001.SZ',?, 'k9-params-v2')", ("settled" if settled else "d1",))
        conn.execute("CREATE TABLE k9_reports (trade_date TEXT, report_date TEXT, markdown TEXT)")
        conn.execute("INSERT INTO k9_reports VALUES ('20260828','20260828','legacy report')")
        conn.execute("CREATE TABLE k9_runs (trade_date TEXT, run_id TEXT)")


def _real_v26_db(path: Path, *, settled: bool) -> None:
    """A true old schema: no current init_schema and no V2.7-only tables."""
    with sqlite3.connect(path) as conn:
        # All V2.6 protected tables exist, but none of the three V2.7 SW
        # snapshot ledgers or V3 runtime tables are pre-created.
        for table, columns in {
            "backfill_log": "table_name TEXT, trade_date TEXT", "devices": "token TEXT",
            "fact_direction_briefings": "trade_date TEXT", "job_events": "trade_date TEXT",
            "llm_providers": "id INTEGER", "llm_usage_events": "trade_date TEXT, task TEXT",
            "namechange": "ts_code TEXT", "reviews": "week TEXT", "review_conclusions": "week TEXT",
            "stock_basic": "ts_code TEXT, market TEXT", "sw_industry_classify": "level TEXT",
            "sw_industry_daily": "trade_date TEXT", "sw_industry_member": "l2_code TEXT, is_current INTEGER",
            "trade_cal": "cal_date TEXT",
        }.items():
            conn.execute(f'CREATE TABLE "{table}" ({columns})')
        conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY, updated_at TEXT)")
        conn.execute("INSERT INTO app_settings VALUES (1, 'v26')")
        conn.execute("CREATE TABLE fact_packs (pack_id TEXT PRIMARY KEY, trade_date TEXT, pack_version TEXT)")
        conn.execute("INSERT INTO fact_packs VALUES ('legacy-fp', '20260828', 'fp-3')")
        conn.execute("CREATE TABLE k9_predictions (d0_trade_date TEXT, ts_code TEXT, state TEXT, params_package_version TEXT)")
        conn.execute("INSERT INTO k9_predictions VALUES ('20260828','000001.SZ',?, 'k9-params-v2')", ("settled" if settled else "d1",))
        conn.execute("CREATE TABLE k9_reports (trade_date TEXT, report_date TEXT, markdown TEXT)")
        conn.execute("INSERT INTO k9_reports VALUES ('20260828','20260828','legacy report')")
        conn.execute("CREATE TABLE k9_runs (trade_date TEXT, run_id TEXT)")


def _names(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_preflight_is_read_only_and_refuses_mixed_schema(tmp_path):
    module = _migration_module(); db = tmp_path / "legacy.db"; _legacy_db(db, settled=True)
    before = db.read_bytes()
    result = module.preflight(db_path=db)
    assert result["releaseSet"] == "v2.7.0-b19"
    assert result["legacy"]["inFlightPredictionCount"] == 0
    assert db.read_bytes() == before
    init_schema(tmp_path / "mixed.db")
    with pytest.raises(module.MigrationRefused, match="V3 tables"):
        module.preflight(db_path=tmp_path / "mixed.db")


def test_plan_a_cutover_archives_then_restore_is_paired_and_preserves_facts(tmp_path):
    module = _migration_module(); db = tmp_path / "legacy.db"; _legacy_db(db, settled=True)
    reports = tmp_path / "reports"; reports.mkdir(); (reports / "20260828.md").write_text("original report", encoding="utf-8")
    archive = tmp_path / "archive"
    result = module.apply(db_path=db, archive_dir=archive, reports_dir=reports, plan="a", confirm=True, expected_inflight=0)
    assert result["status"] == "applied" and result["activeState"]["v3Tables"] == sorted(module.V3_TABLES)
    assert not (_names(db) & module.LEGACY_TABLES)
    assert module.V3_TABLES <= _names(db)
    assert json.loads((archive / "legacy-k9-v2.json").read_text())["status"] == "archived"
    assert "fact_packs" in _names(db), "migration may not delete frozen fact metadata"
    (reports / "20260828.md").unlink()
    restored = module.restore(db_path=db, archive_dir=archive, reports_dir=reports)
    assert restored["status"] == "restored" and module.LEGACY_TABLES & _names(db)
    assert (reports / "20260828.md").read_text(encoding="utf-8") == "original report"


def test_plan_a_refuses_inflight_and_plan_c_marks_the_archive(tmp_path):
    module = _migration_module(); db = tmp_path / "legacy.db"; _legacy_db(db, settled=False)
    with pytest.raises(module.MigrationRefused, match="D2 settled"):
        module.apply(db_path=db, archive_dir=tmp_path / "a", reports_dir=None, plan="a", confirm=True, expected_inflight=1)
    with pytest.raises(module.MigrationRefused, match="expected-inflight 1"):
        module.apply(db_path=db, archive_dir=tmp_path / "ambiguous", reports_dir=None, plan="c", confirm=True)
    result = module.apply(db_path=db, archive_dir=tmp_path / "c", reports_dir=None, plan="c", confirm=True, expected_inflight=1)
    bundle = json.loads((tmp_path / "c" / "legacy-k9-v2.json").read_text())
    assert result["plan"] == "c" and bundle["status"] == "superseded"
    assert bundle["reason"] == "superseded_by_k9-v3"
    assert bundle["tables"]["k9_predictions"][0]["archive_status"] == "superseded"


@pytest.mark.parametrize("plan,settled,expected", [("a", True, 0), ("c", False, 1)])
def test_real_v26_schema_creates_new_empty_protected_tables_without_mutating_old_ones(tmp_path, plan, settled, expected):
    module = _migration_module(); db = tmp_path / f"legacy-{plan}.db"; _real_v26_db(db, settled=settled)
    with sqlite3.connect(db) as conn:
        before = {name: module._fingerprint(conn, name) for name in ("app_settings", "fact_packs")}
    archive = tmp_path / f"archive-{plan}"
    module.apply(db_path=db, archive_dir=archive, reports_dir=None, plan=plan, confirm=True, expected_inflight=expected)
    with sqlite3.connect(db) as conn:
        assert {name: module._fingerprint(conn, name) for name in before} == before
    assert "sw_industry_member_snapshots" in _names(db)
    restored = module.restore(db_path=db, archive_dir=archive, reports_dir=None)
    assert restored["status"] == "restored"
    assert "sw_industry_member_snapshots" not in _names(db)


def test_cutover_refuses_any_non_v270_protected_table_absence(tmp_path):
    module = _migration_module(); db = tmp_path / "broken-v26.db"; _real_v26_db(db, settled=True)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE stock_basic")
    with pytest.raises(module.MigrationRefused, match="missing reviewed protected"):
        module.preflight(db_path=db)


def _schema_snapshot(path: Path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()


def test_v3_read_paths_do_not_create_schema_on_legacy_or_missing_database(tmp_path):
    from neckline.auction import store as auction_store
    from neckline.report import store as report_store
    from neckline.scorecard import packages

    legacy = tmp_path / "unmigrated.db"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE retained_history (id INTEGER PRIMARY KEY)")
    before = _schema_snapshot(legacy)
    assert report_store.latest_k9_report(db_path=legacy) is None
    assert packages.list_packages(state="active", db_path=legacy) == []
    assert packages.load_package("missing", db_path=legacy) is None
    assert auction_store.load_checklist("missing", db_path=legacy) is None
    assert _schema_snapshot(legacy) == before
    absent = tmp_path / "nested" / "absent.db"
    assert packages.list_packages(state="settled", db_path=absent) == []
    assert not absent.exists() and not absent.parent.exists()


def test_release_identity_routes_and_build19_compatibility():
    project_yml = (ROOT / "App" / "project.yml").read_text(encoding="utf-8")
    pbxproj = (ROOT / "App" / "Neckline.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")
    pyproject = (ROOT / "Backend" / "pyproject.toml").read_text(encoding="utf-8")
    icon = ROOT / "App" / "Neckline" / "Resources" / "Assets.xcassets" / "AppIconV270B19.appiconset"
    assert VERSION == "v2.7.0" and RELEASE_SET == "v2.7.0-b24"
    assert 'version = "2.7.0"' in pyproject and 'MARKETING_VERSION: "2.7.0"' in project_yml
    assert 'CURRENT_PROJECT_VERSION: "19"' in project_yml and "AppIconV270B19" in project_yml
    assert "MARKETING_VERSION = 2.7.0;" in pbxproj and "CURRENT_PROJECT_VERSION = 19;" in pbxproj
    assert "AppIconV260B18" not in pbxproj and icon.is_dir()
    for image in json.loads((icon / "Contents.json").read_text())["images"]:
        assert (icon / image["filename"]).is_file()
    routes = {route.path for route in app.routes}
    assert "/api/v1/scoreboard/packages" in routes and "/api/v1/scoreboard/listing" not in routes


def test_runtime_has_no_v2_strategy_or_default_parameter_path():
    runtime = ROOT / "Backend" / "neckline"
    scanned = [*runtime.glob("k9/*.py"), runtime / "api" / "app.py", runtime / "report" / "evening.py"]
    text = "\n".join(path.read_text(encoding="utf-8") for path in scanned if path.exists())
    for forbidden in ("K9-v2", "k9-params-v2", "fp-3", "p1_breakout", "strict", "relaxed", "activeQueueLimit"):
        assert forbidden not in text
    script = (ROOT / "Backend" / "scripts" / "evening.py").read_text(encoding="utf-8")
    assert "default=None" in script and "config/k9-params.json" not in script
