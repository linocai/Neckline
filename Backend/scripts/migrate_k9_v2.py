#!/usr/bin/env python3
"""K9-v1 → K9-v2 production-state cutover with an auditable rollback bundle.

This is intentionally an explicit release command, never an API/startup migration.  It
archives the named K9-v1 runs and their derived state, removes them from the active
business database, recreates the current K9-v2 tables empty, and leaves frozen market
facts untouched.

The two supported actions are paired:

    python scripts/migrate_k9_v2.py apply ...
    python scripts/migrate_k9_v2.py restore --db ... --archive-dir ... --reports-dir ...

`apply` fails closed unless the caller names every expected run and report.  The archive
directory must not exist, so neither a prior audit bundle nor rollback database can be
overwritten accidentally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.db import init_schema  # noqa: E402


ARCHIVE_SCHEMA = "neckline-k9-invalidation-v1"
INVALIDATED = "invalidated"
REASON = "superseded_by_k9-v2"
TARGET_STRATEGY_VERSION = "K9-v2"
PRESERVED_HISTORY = (
    {"kind": "strategy", "identity": "K9-v1", "disposition": "formal_history_untouched"},
    {
        "kind": "parameter_package",
        "identity": "k9-params-20260822-r1",
        "disposition": "formal_history_untouched",
    },
)
CURRENT_K9_TABLES = {
    "k9_channel_hits",
    "k9_checklists",
    "k9_coverage_daily",
    "k9_coverage_misses",
    "k9_d1_verdicts",
    "k9_explain_audit",
    "k9_explain_notes",
    "k9_predictions",
    "k9_listing_entries",
    "k9_playbooks",
    "k9_reports",
    "k9_runs",
}
PROTECTED_TABLES = (
    "app_settings",
    "backfill_log",
    "devices",
    "fact_packs",
    "llm_providers",
    "namechange",
    "stock_basic",
    "sw_industry_classify",
    "sw_industry_daily",
    "sw_industry_member",
    "trade_cal",
)


class MigrationRefused(RuntimeError):
    """Preconditions do not identify the exact authorized K9-v1 state."""


@dataclass(frozen=True)
class ExpectedRun:
    trade_date: str
    report_date: str
    run_id: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n").encode("utf-8")


def _atomic_json(path: Path, value: Any) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(_json_bytes(value))
    os.replace(tmp, path)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE ?",
        ("table", "sqlite_%"),
    )}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    safe = table.replace('"', '""')
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{safe}")')]


def _records(conn: sqlite3.Connection, table: str, *, where: str = "",
             args: Sequence[Any] = ()) -> list[dict[str, Any]]:
    safe = table.replace('"', '""')
    columns = _columns(conn, table)
    if not columns:
        return []
    sql = f'SELECT * FROM "{safe}"'
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY rowid"
    return [dict(zip(columns, row)) for row in conn.execute(sql, tuple(args)).fetchall()]


def _integrity(path: Path) -> str:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])


def _database_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
    if _integrity(destination) != "ok":
        raise RuntimeError(f"rollback database failed integrity_check: {destination}")


def _table_fingerprint(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    if table not in _table_names(conn):
        return {"exists": False, "rowCount": 0, "sha256": None}
    payload = _json_bytes(_records(conn, table))
    return {
        "exists": True,
        "rowCount": len(json.loads(payload)),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _protected_fingerprints(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {table: _table_fingerprint(conn, table) for table in PROTECTED_TABLES}


def _parse_run(raw: str) -> ExpectedRun:
    parts = raw.split(":", 2)
    if len(parts) != 3 or any(not part for part in parts):
        raise argparse.ArgumentTypeError(
            "--expected-run must be TRADE_DATE:REPORT_DATE:RUN_ID")
    trade_date, report_date, run_id = parts
    if not (len(trade_date) == len(report_date) == 8
            and trade_date.isdigit() and report_date.isdigit()):
        raise argparse.ArgumentTypeError("trade/report dates must be YYYYMMDD")
    return ExpectedRun(trade_date, report_date, run_id)


def _validate_source(
    conn: sqlite3.Connection,
    *,
    expected_runs: Sequence[ExpectedRun],
    expected_params_package: str,
    reports_dir: Path,
) -> dict[str, Any]:
    if len(expected_runs) != 2:
        raise MigrationRefused("exactly two --expected-run values are required")
    if len({item.trade_date for item in expected_runs}) != len(expected_runs):
        raise MigrationRefused("expected trade dates must be unique")
    names = _table_names(conn)
    required = {"k9_runs", "k9_reports"}
    missing = sorted(required - names)
    if missing:
        raise MigrationRefused(f"source database is missing old K9 tables: {missing}")
    run_columns = set(_columns(conn, "k9_runs"))
    if "strategy_version" in run_columns:
        raise MigrationRefused(
            "source k9_runs already has strategy_version; refusing to invalidate a migrated/K9-v2 database")
    rows = _records(conn, "k9_runs")
    expected_by_date = {item.trade_date: item for item in expected_runs}
    if {str(row.get("trade_date")) for row in rows} != set(expected_by_date):
        raise MigrationRefused(
            f"active k9_runs dates do not exactly match authorization: "
            f"{sorted(str(row.get('trade_date')) for row in rows)}")
    for row in rows:
        expected = expected_by_date[str(row["trade_date"])]
        if str(row.get("run_id")) != expected.run_id:
            raise MigrationRefused(
                f"run id mismatch for {expected.trade_date}: {row.get('run_id')}")
        if str(row.get("params_package_version")) != expected_params_package:
            raise MigrationRefused(
                f"params package mismatch for {expected.trade_date}: "
                f"{row.get('params_package_version')}")
    reports = _records(conn, "k9_reports")
    if {str(row.get("trade_date")) for row in reports} != set(expected_by_date):
        raise MigrationRefused("active k9_reports do not exactly match the two authorized runs")
    for row in reports:
        expected = expected_by_date[str(row["trade_date"])]
        if str(row.get("report_date")) != expected.report_date:
            raise MigrationRefused(
                f"report date mismatch for {expected.trade_date}: {row.get('report_date')}")
        report_file = reports_dir / f"{expected.report_date}.md"
        if not report_file.is_file():
            raise MigrationRefused(f"original report file is missing: {report_file}")
    return {
        "runs": rows,
        "reports": reports,
        "packIds": sorted({str(row["pack_id"]) for row in rows if row.get("pack_id")}),
        "tradeDates": sorted(expected_by_date),
        "reportDates": sorted(item.report_date for item in expected_runs),
    }


def _archive_bundle(
    conn: sqlite3.Connection,
    *,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in sorted(name for name in _table_names(conn) if name.startswith("k9_")):
        rows = _records(conn, table)
        if table in {"k9_runs", "k9_reports"}:
            rows = [{
                **row,
                "archive_status": INVALIDATED,
                "archive_reason": REASON,
                "superseded_by": TARGET_STRATEGY_VERSION,
            } for row in rows]
        tables[table] = rows

    trade_dates = list(source["tradeDates"])
    report_dates = list(source["reportDates"])
    pack_ids = list(source["packIds"])
    date_marks = sorted(set(trade_dates + report_dates))
    placeholders = ",".join("?" for _ in date_marks)
    pack_placeholders = ",".join("?" for _ in pack_ids)
    names = _table_names(conn)
    associated: dict[str, list[dict[str, Any]]] = {}
    if "job_events" in names and date_marks:
        associated["job_events"] = _records(
            conn, "job_events", where=f"trade_date IN ({placeholders})", args=date_marks)
    if "llm_usage_events" in names:
        clauses: list[str] = []
        args: list[Any] = []
        if date_marks:
            clauses.extend([
                f"trade_date IN ({placeholders})",
                f"report_date IN ({placeholders})",
            ])
            args.extend(date_marks)
            args.extend(date_marks)
        if pack_ids:
            clauses.append(f"pack_id IN ({pack_placeholders})")
            args.extend(pack_ids)
        associated["llm_usage_events"] = _records(
            conn, "llm_usage_events", where=" OR ".join(clauses), args=args)
    if "fact_direction_briefings" in names and pack_ids:
        associated["fact_direction_briefings"] = _records(
            conn, "fact_direction_briefings",
            where=f"pack_id IN ({pack_placeholders})", args=pack_ids)

    # A generated weekly review can materialize report text.  Archive/remove only an
    # exact textual date match; unrelated historical reviews remain active.
    if "reviews" in names and date_marks:
        reviews = _records(conn, "reviews")
        associated["reviews"] = [row for row in reviews if any(
            date_mark in str(row.get("result_json") or "")
            or date_mark in str(row.get("material") or "")
            for date_mark in date_marks
        )]
        weeks = [str(row["week"]) for row in associated["reviews"]]
        if weeks and "review_conclusions" in names:
            week_marks = ",".join("?" for _ in weeks)
            associated["review_conclusions"] = _records(
                conn, "review_conclusions", where=f"week IN ({week_marks})", args=weeks)

    return {
        "schemaVersion": ARCHIVE_SCHEMA,
        "status": INVALIDATED,
        "reason": REASON,
        "supersededBy": TARGET_STRATEGY_VERSION,
        "archivedAt": _utc_now(),
        "preservedFormalHistory": list(PRESERVED_HISTORY),
        "source": {
            "strategyVersion": "K9-v1",
            "tradeDates": trade_dates,
            "reportDates": report_dates,
            "packIds": pack_ids,
        },
        "tables": tables,
        "associatedOperationalRows": associated,
    }


def _delete_associated(conn: sqlite3.Connection, bundle: Mapping[str, Any]) -> None:
    associated = bundle["associatedOperationalRows"]
    for row in associated.get("job_events", []):
        conn.execute("DELETE FROM job_events WHERE id=?", (row["id"],))
    for row in associated.get("llm_usage_events", []):
        conn.execute("DELETE FROM llm_usage_events WHERE id=?", (row["id"],))
    for row in associated.get("fact_direction_briefings", []):
        conn.execute("DELETE FROM fact_direction_briefings WHERE pack_id=?", (row["pack_id"],))
    for row in associated.get("review_conclusions", []):
        conn.execute(
            "DELETE FROM review_conclusions WHERE week=? AND version=?",
            (row["week"], row["version"]),
        )
    for row in associated.get("reviews", []):
        conn.execute("DELETE FROM reviews WHERE week=?", (row["week"],))


def _clear_and_recreate(db_path: Path, bundle: Mapping[str, Any]) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            _delete_associated(conn, bundle)
            for table in sorted(
                (name for name in _table_names(conn) if name.startswith("k9_")),
                reverse=True,
            ):
                safe = table.replace('"', '""')
                conn.execute(f'DROP TABLE "{safe}"')
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    init_schema(db_path)


def _verify_empty_current_state(db_path: Path) -> dict[str, Any]:
    if _integrity(db_path) != "ok":
        raise RuntimeError("migrated database failed integrity_check")
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        names = _table_names(conn)
        k9_tables = {name for name in names if name.startswith("k9_")}
        if k9_tables != CURRENT_K9_TABLES:
            raise RuntimeError(
                f"current K9 table set mismatch: {sorted(k9_tables)}")
        counts = {
            table: int(conn.execute(
                f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in sorted(k9_tables)
        }
        if any(counts.values()):
            raise RuntimeError(f"K9-v2 active state is not empty: {counts}")
        return {"k9TableCounts": counts, "protected": _protected_fingerprints(conn)}


def inspect(
    *,
    db_path: Path,
    reports_dir: Path,
    expected_runs: Sequence[ExpectedRun],
    expected_params_package: str,
) -> dict[str, Any]:
    if not db_path.is_file():
        raise MigrationRefused(f"database does not exist: {db_path}")
    if _integrity(db_path) != "ok":
        raise MigrationRefused("source database failed integrity_check")
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        source = _validate_source(
            conn,
            expected_runs=expected_runs,
            expected_params_package=expected_params_package,
            reports_dir=reports_dir,
        )
        return {
            "source": source,
            "protected": _protected_fingerprints(conn),
            "k9Tables": sorted(name for name in _table_names(conn) if name.startswith("k9_")),
        }


def apply(
    *,
    db_path: Path,
    archive_dir: Path,
    reports_dir: Path,
    expected_runs: Sequence[ExpectedRun],
    expected_params_package: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    preflight = inspect(
        db_path=db_path,
        reports_dir=reports_dir,
        expected_runs=expected_runs,
        expected_params_package=expected_params_package,
    )
    if dry_run:
        return {"action": "dry-run", **preflight}
    if archive_dir.exists():
        raise MigrationRefused(f"archive directory already exists: {archive_dir}")

    archive_dir.mkdir(parents=True)
    rollback_path = archive_dir / "rollback" / "neckline.pre-k9-v2.db"
    reports_archive = archive_dir / "reports"
    reports_archive.mkdir(parents=True)
    bundle_path = archive_dir / "invalidated-k9-v1.json"
    manifest_path = archive_dir / "manifest.json"

    try:
        _database_backup(db_path, rollback_path)
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            bundle = _archive_bundle(conn, source=preflight["source"])
        bundle_path.write_bytes(_json_bytes(bundle))

        report_files: list[dict[str, Any]] = []
        for item in expected_runs:
            source_file = reports_dir / f"{item.report_date}.md"
            target_file = reports_archive / source_file.name
            shutil.copy2(source_file, target_file)
            report_files.append({
                "livePath": str(source_file),
                "archivePath": str(target_file.relative_to(archive_dir)),
                "sha256": _sha256(target_file),
            })

        manifest = {
            "schemaVersion": ARCHIVE_SCHEMA,
            "status": "prepared",
            "reason": REASON,
            "supersededBy": TARGET_STRATEGY_VERSION,
            "createdAt": _utc_now(),
            "sourceDatabase": str(db_path),
            "rollbackDatabase": str(rollback_path.relative_to(archive_dir)),
            "rollbackDatabaseSha256": _sha256(rollback_path),
            "invalidatedBundle": str(bundle_path.relative_to(archive_dir)),
            "invalidatedBundleSha256": _sha256(bundle_path),
            "reports": report_files,
            "expectedRuns": [item.__dict__ for item in expected_runs],
            "expectedParamsPackage": expected_params_package,
            "protectedBefore": preflight["protected"],
            "preservedFormalHistory": list(PRESERVED_HISTORY),
        }
        _atomic_json(manifest_path, manifest)

        _clear_and_recreate(db_path, bundle)
        after = _verify_empty_current_state(db_path)
        if after["protected"] != preflight["protected"]:
            raise RuntimeError("a protected non-K9 table changed during migration")
        for item in report_files:
            live = Path(item["livePath"])
            if _sha256(live) != item["sha256"]:
                raise RuntimeError(f"live report changed during migration: {live}")
            live.unlink()

        manifest.update({
            "status": "applied",
            "appliedAt": _utc_now(),
            "databaseIntegrity": "ok",
            "activeState": after,
            "rawMarketSnapshots": "untouched",
        })
        _atomic_json(manifest_path, manifest)
        return manifest
    except Exception:
        # The rollback database is created before any business-state mutation.  If a
        # postcondition fails, restore the source immediately and put report files back.
        if rollback_path.is_file() and _integrity(rollback_path) == "ok":
            _restore_database(rollback_path, db_path)
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for item in manifest.get("reports", []):
                    archived = archive_dir / item["archivePath"]
                    if archived.is_file():
                        live = Path(item["livePath"])
                        live.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(archived, live)
                manifest["status"] = "rolled_back_after_failed_apply"
                manifest["rolledBackAt"] = _utc_now()
                _atomic_json(manifest_path, manifest)
            except Exception:
                pass
        raise


def _restore_database(source: Path, destination: Path) -> None:
    tmp = destination.with_name(f".{destination.name}.{os.getpid()}.restore")
    shutil.copy2(source, tmp)
    if _integrity(tmp) != "ok":
        tmp.unlink(missing_ok=True)
        raise RuntimeError("rollback database copy failed integrity_check")
    os.replace(tmp, destination)
    # Release/restore is only run with writers stopped.  A WAL that belonged to the
    # replaced main file must never be replayed onto the rollback database.
    for suffix in ("-wal", "-shm"):
        Path(f"{destination}{suffix}").unlink(missing_ok=True)


def restore(*, db_path: Path, archive_dir: Path, reports_dir: Path) -> dict[str, Any]:
    manifest_path = archive_dir / "manifest.json"
    if not manifest_path.is_file():
        raise MigrationRefused(f"missing migration manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != ARCHIVE_SCHEMA:
        raise MigrationRefused("unsupported rollback manifest")
    rollback_path = archive_dir / str(manifest["rollbackDatabase"])
    if not rollback_path.is_file() or _sha256(rollback_path) != manifest.get(
        "rollbackDatabaseSha256"
    ):
        raise MigrationRefused("rollback database is missing or failed sha256 verification")
    if _integrity(rollback_path) != "ok":
        raise MigrationRefused("rollback database failed integrity_check")

    failed_state = archive_dir / "rollback" / "neckline.before-restore.db"
    if db_path.is_file() and not failed_state.exists():
        _database_backup(db_path, failed_state)
    _restore_database(rollback_path, db_path)
    reports_dir.mkdir(parents=True, exist_ok=True)
    for item in manifest.get("reports", []):
        archived = archive_dir / item["archivePath"]
        if _sha256(archived) != item["sha256"]:
            raise MigrationRefused(f"archived report failed sha256 verification: {archived}")
        shutil.copy2(archived, reports_dir / Path(item["livePath"]).name)
    manifest.update({"status": "restored", "restoredAt": _utc_now()})
    _atomic_json(manifest_path, manifest)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--db", type=Path, required=True)
    apply_parser.add_argument("--archive-dir", type=Path, required=True)
    apply_parser.add_argument("--reports-dir", type=Path, required=True)
    apply_parser.add_argument(
        "--expected-run", type=_parse_run, action="append", required=True,
        help="repeat exactly twice: TRADE_DATE:REPORT_DATE:RUN_ID",
    )
    apply_parser.add_argument("--expected-params-package", required=True)
    apply_parser.add_argument("--dry-run", action="store_true")
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("--db", type=Path, required=True)
    restore_parser.add_argument("--archive-dir", type=Path, required=True)
    restore_parser.add_argument("--reports-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "apply":
            result = apply(
                db_path=args.db,
                archive_dir=args.archive_dir,
                reports_dir=args.reports_dir,
                expected_runs=args.expected_run,
                expected_params_package=args.expected_params_package,
                dry_run=args.dry_run,
            )
        else:
            result = restore(
                db_path=args.db, archive_dir=args.archive_dir, reports_dir=args.reports_dir)
    except (MigrationRefused, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"[k9-v2-migration] REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
