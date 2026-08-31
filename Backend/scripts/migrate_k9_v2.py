#!/usr/bin/env python3
"""Explicit V2.6/K9-v2 -> V2.7/K9-v3 database cutover.

Never import this release-only utility from an API, startup, or scheduled job.
``preflight`` is the default, read-only action. ``apply`` requires an explicit
plan and creates an online SQLite backup before dropping only the reviewed
legacy runtime tables. ``restore`` brings back both database and reports.

Plan A requires all V2 records to be settled. Plan C archives unfinished V2
records as ``superseded_by_k9-v3``. Plan B (a dedicated V2 history UI) remains
an explicit product decision and is intentionally not implemented here.
Facts/Parquet snapshots and formal parameter originals are never touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from neckline.db import init_schema  # noqa: E402

ARCHIVE_SCHEMA = "neckline-k9-v2-to-v3-cutover-v1"
TARGET_RELEASE = "v2.7.0-b19"
TARGET_STRATEGY = "K9-v3"
LEGACY_TABLES = frozenset({
    "k9_channel_hits", "k9_checklists", "k9_coverage_daily", "k9_coverage_misses",
    "k9_d1_verdicts", "k9_explain_audit", "k9_explain_notes", "k9_listing_entries",
    "k9_playbooks", "k9_predictions", "k9_reports", "k9_runs",
})
V3_TABLES = frozenset({
    "k9_selection_batches", "k9_selection_candidates", "k9_playbook_revisions", "k9_playbook_freezes", "k9_selection_d1",
    "k9_selection_d1_close", "k9_selection_d2", "k9_package_checklists",
    "k9_package_reports", "k9_d0_run_markers", "k9_lifecycle_attempts", "k9_lifecycle_stages", "k9_intraday_capture_audit",
})
PROTECTED_TABLES = frozenset({
    "app_settings", "backfill_log", "devices", "fact_packs", "fact_pack_revisions", "fact_direction_briefings",
    "job_events", "llm_providers", "llm_usage_events", "namechange", "reviews",
    "review_conclusions", "stock_basic", "sw_industry_classify", "sw_industry_daily",
    "sw_industry_member", "sw_industry_member_snapshots", "sw_industry_snapshot_imports", "sw_industry_snapshot_manifests", "trade_cal",
})
# These are the only protected tables introduced for V2.7 and therefore the
# only protected absences a genuine V2.6 database may acquire during cutover.
NEW_V270_PROTECTED_TABLES = frozenset({
    "sw_industry_member_snapshots", "sw_industry_snapshot_imports",
    "sw_industry_snapshot_manifests", "fact_pack_revisions",
})


class MigrationRefused(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _integrity(path: Path) -> str:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    quoted = table.replace('"', '""')
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{quoted}")')]


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    columns = _columns(conn, table)
    quoted = table.replace('"', '""')
    return [dict(zip(columns, row)) for row in conn.execute(f'SELECT * FROM "{quoted}" ORDER BY rowid')]


def _fingerprint(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    if table not in _tables(conn):
        return {"exists": False, "rowCount": 0, "sha256": None}
    payload = json.dumps(_rows(conn, table), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {"exists": True, "rowCount": len(json.loads(payload)), "sha256": hashlib.sha256(payload).hexdigest()}


def _protected_fingerprints(conn: sqlite3.Connection, *, existing_only: bool = True) -> dict[str, dict[str, Any]]:
    """Fingerprint only facts that existed before cutover.

    V2.6 databases legitimately predate V2.7's empty protected metadata
    tables (for example the immutable SW snapshot ledger).  Those tables may
    be created by ``init_schema``; any protected table already present remains
    byte/row-order hash immutable.
    """
    names = _tables(conn)
    selected = sorted(PROTECTED_TABLES & names) if existing_only else sorted(PROTECTED_TABLES)
    return {name: _fingerprint(conn, name) for name in selected}


def _online_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
    if _integrity(destination) != "ok":
        raise RuntimeError("online backup failed integrity_check")


def _legacy_summary(conn: sqlite3.Connection, legacy: Iterable[str]) -> dict[str, Any]:
    legacy = sorted(legacy)
    counts = {name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in legacy}
    predictions: list[dict[str, Any]] = []
    if "k9_predictions" in legacy:
        fields = [field for field in ("d0_trade_date", "trade_date", "ts_code", "d1_verdict", "d2_outcome", "state", "strategy_version", "params_package_version") if field in _columns(conn, "k9_predictions")]
        if fields:
            predictions = [dict(zip(fields, row)) for row in conn.execute(f'SELECT {",".join(fields)} FROM k9_predictions ORDER BY rowid')]
    settled = {"settled", "complete", "completed"}
    in_flight = [row for row in predictions if not (row.get("d2_outcome") or row.get("state") in settled)]
    return {"legacyTables": legacy, "rowCounts": counts, "predictionRows": predictions,
            "inFlightPredictionCount": len(in_flight),
            "parameterIdentities": sorted({str(row["params_package_version"]) for row in predictions if row.get("params_package_version")})}


def preflight(*, db_path: Path, reports_dir: Path | None = None) -> dict[str, Any]:
    """Read-only inventory; it rejects unknown or mixed K9 schemas."""
    if not db_path.is_file():
        raise MigrationRefused(f"database does not exist: {db_path}")
    if _integrity(db_path) != "ok":
        raise MigrationRefused("source database failed integrity_check")
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        names = _tables(conn); k9 = {name for name in names if name.startswith("k9_")}
        missing_protected = PROTECTED_TABLES - names - NEW_V270_PROTECTED_TABLES
        if missing_protected:
            raise MigrationRefused(f"legacy database is missing reviewed protected tables: {sorted(missing_protected)}")
        unknown = k9 - LEGACY_TABLES - V3_TABLES
        if unknown:
            raise MigrationRefused(f"unknown K9 tables require a reviewed migration list: {sorted(unknown)}")
        legacy, v3 = k9 & LEGACY_TABLES, k9 & V3_TABLES
        if v3:
            raise MigrationRefused("database already contains V3 tables; never apply legacy cutover onto a mixed schema")
        if not legacy:
            raise MigrationRefused("no V2 runtime tables found; refusing ambiguous no-op")
        report_files: list[dict[str, str]] = []
        if reports_dir is not None and "k9_reports" in legacy:
            for row in _rows(conn, "k9_reports"):
                report_date = str(row.get("report_date") or row.get("trade_date") or "")
                if report_date:
                    path = reports_dir / f"{report_date}.md"
                    report_files.append({"reportDate": report_date, "livePath": str(path), "exists": str(path.is_file())})
        return {"schemaVersion": ARCHIVE_SCHEMA, "releaseSet": TARGET_RELEASE, "sourceDatabase": str(db_path),
                "databaseIntegrity": "ok", "legacy": _legacy_summary(conn, legacy),
                "protected": _protected_fingerprints(conn), "reportFiles": report_files}


def _require_plan(summary: Mapping[str, Any], plan: str, confirm: bool, expected_inflight: int | None) -> None:
    if plan not in {"a", "c"}:
        raise MigrationRefused("select --plan a (settled) or --plan c (supersede); plan B is not implemented")
    if not confirm:
        raise MigrationRefused("apply needs --confirm-cutover after reviewing preflight")
    in_flight = int(summary["legacy"]["inFlightPredictionCount"])
    if expected_inflight is None or expected_inflight != in_flight:
        raise MigrationRefused(f"apply requires --expected-inflight {in_flight}; preflight inventory must be acknowledged exactly")
    if plan == "a" and in_flight:
        raise MigrationRefused(f"plan A requires every V2 record D2 settled; found {in_flight} in-flight")


def _archive_bundle(conn: sqlite3.Connection, plan: str) -> dict[str, Any]:
    tables = {name: _rows(conn, name) for name in sorted(_tables(conn) & LEGACY_TABLES)}
    if plan == "c":
        settled = {"settled", "complete", "completed"}
        marked: list[dict[str, Any]] = []
        for row in tables.get("k9_predictions", []):
            if not (row.get("d2_outcome") or row.get("state") in settled):
                marked.append({**row, "archive_status": "superseded",
                               "archive_reason": "superseded_by_k9-v3",
                               "superseded_by": TARGET_STRATEGY})
            else:
                marked.append(row)
        tables["k9_predictions"] = marked
    return {"schemaVersion": ARCHIVE_SCHEMA, "status": "archived" if plan == "a" else "superseded",
            "reason": None if plan == "a" else "superseded_by_k9-v3", "sourceStrategy": "K9-v2",
            "targetStrategy": TARGET_STRATEGY, "archivedAt": _now(),
            "tables": tables}


def _copy_reports(reports_dir: Path | None, archive_dir: Path, bundle: Mapping[str, Any]) -> list[dict[str, str]]:
    if reports_dir is None:
        return []
    dates = sorted({str(row.get("report_date") or row.get("trade_date")) for row in bundle["tables"].get("k9_reports", []) if row.get("report_date") or row.get("trade_date")})
    out: list[dict[str, str]] = []
    for date_value in dates:
        live = reports_dir / f"{date_value}.md"
        if live.is_file():
            target = archive_dir / "reports" / live.name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(live, target)
            out.append({"livePath": str(live), "archivePath": str(target.relative_to(archive_dir)), "sha256": _sha256(target)})
    return out


def _drop_legacy(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON"); conn.execute("BEGIN IMMEDIATE")
        try:
            for name in sorted(_tables(conn) & LEGACY_TABLES, reverse=True):
                conn.execute(f'DROP TABLE "{name.replace(chr(34), chr(34) * 2)}"')
            conn.commit()
        except Exception:
            conn.rollback(); raise
    init_schema(db_path)


def _verify_v3(db_path: Path, protected: Mapping[str, Any]) -> dict[str, Any]:
    if _integrity(db_path) != "ok":
        raise RuntimeError("cutover database failed integrity_check")
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        names = _tables(conn); legacy, v3 = names & LEGACY_TABLES, names & V3_TABLES
        if legacy or v3 != V3_TABLES:
            raise RuntimeError(f"unexpected K9 tables legacy={sorted(legacy)} v3={sorted(v3)}")
        after = {name: _fingerprint(conn, name) for name in sorted(protected)}
        if after != protected:
            raise RuntimeError("protected facts/settings changed during cutover")
        return {"databaseIntegrity": "ok", "v3Tables": sorted(v3), "protected": after}


def _restore_database(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.restore")
    shutil.copy2(source, temporary)
    if _integrity(temporary) != "ok":
        temporary.unlink(missing_ok=True); raise RuntimeError("rollback database copy failed integrity_check")
    os.replace(temporary, destination)
    for suffix in ("-wal", "-shm"):
        Path(f"{destination}{suffix}").unlink(missing_ok=True)


def apply(*, db_path: Path, archive_dir: Path, reports_dir: Path | None, plan: str, confirm: bool,
          expected_inflight: int | None = None) -> dict[str, Any]:
    summary = preflight(db_path=db_path, reports_dir=reports_dir); _require_plan(summary, plan, confirm, expected_inflight)
    if archive_dir.exists():
        raise MigrationRefused(f"archive directory already exists: {archive_dir}")
    archive_dir.mkdir(parents=True); rollback = archive_dir / "rollback" / "neckline.pre-v270.db"
    try:
        _online_backup(db_path, rollback)
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            bundle = _archive_bundle(conn, plan)
        bundle_path = archive_dir / "legacy-k9-v2.json"; _write_json(bundle_path, bundle)
        manifest: dict[str, Any] = {**summary, "plan": plan, "status": "prepared", "createdAt": _now(),
            "rollbackDatabase": str(rollback.relative_to(archive_dir)), "rollbackDatabaseSha256": _sha256(rollback),
            "legacyBundle": str(bundle_path.relative_to(archive_dir)), "legacyBundleSha256": _sha256(bundle_path),
            "reports": _copy_reports(reports_dir, archive_dir, bundle)}
        _write_json(archive_dir / "manifest.json", manifest)
        _drop_legacy(db_path)
        manifest.update({"status": "applied", "appliedAt": _now(), "activeState": _verify_v3(db_path, summary["protected"])})
        _write_json(archive_dir / "manifest.json", manifest)
        return manifest
    except Exception:
        if rollback.is_file() and _integrity(rollback) == "ok":
            _restore_database(rollback, db_path)
        raise


def restore(*, db_path: Path, archive_dir: Path, reports_dir: Path | None) -> dict[str, Any]:
    manifest_path = archive_dir / "manifest.json"
    if not manifest_path.is_file(): raise MigrationRefused("missing migration manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rollback = archive_dir / str(manifest.get("rollbackDatabase", ""))
    if manifest.get("schemaVersion") != ARCHIVE_SCHEMA or manifest.get("status") not in {"applied", "restored"}:
        raise MigrationRefused("manifest is not an applied V2->V3 cutover")
    if not rollback.is_file() or _sha256(rollback) != manifest.get("rollbackDatabaseSha256") or _integrity(rollback) != "ok":
        raise MigrationRefused("rollback database is missing, changed, or corrupt")
    if db_path.is_file(): _online_backup(db_path, archive_dir / "rollback" / "neckline.before-restore.db")
    _restore_database(rollback, db_path)
    if reports_dir is not None:
        reports_dir.mkdir(parents=True, exist_ok=True)
        for item in manifest.get("reports", []):
            source = archive_dir / item["archivePath"]
            if not source.is_file() or _sha256(source) != item["sha256"]: raise MigrationRefused(f"archived report failed sha256: {source}")
            shutil.copy2(source, reports_dir / Path(item["livePath"]).name)
    manifest["status"] = "restored"; manifest["restoredAt"] = _now(); _write_json(manifest_path, manifest)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("preflight", "apply", "restore"), default="preflight")
    parser.add_argument("--db", type=Path, required=True); parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--reports-dir", type=Path); parser.add_argument("--plan", choices=("a", "c"))
    parser.add_argument("--confirm-cutover", action="store_true")
    parser.add_argument("--expected-inflight", type=int); return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "preflight": result = preflight(db_path=args.db, reports_dir=args.reports_dir)
        elif args.action == "apply":
            if args.archive_dir is None or args.plan is None: raise MigrationRefused("apply requires --archive-dir and --plan")
            result = apply(db_path=args.db, archive_dir=args.archive_dir, reports_dir=args.reports_dir, plan=args.plan,
                           confirm=args.confirm_cutover, expected_inflight=args.expected_inflight)
        else:
            if args.archive_dir is None: raise MigrationRefused("restore requires --archive-dir")
            result = restore(db_path=args.db, archive_dir=args.archive_dir, reports_dir=args.reports_dir)
    except (MigrationRefused, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"[k9-v2-to-v3] REFUSED: {exc}", file=sys.stderr); return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
