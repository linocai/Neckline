"""Export one closed report task as private offline evidence, never a runnable DB.

Standard library only: this file can run over SSH stdin and emit gzip on stdout.
It never loads application settings, credentials, current provider configuration,
or network clients. References without an exact version are reported as missing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TASK_TABLES = (
    "k10_tasks", "k10_task_execution_bindings", "k10_scan_execution_bindings",
    "k10_execution_item_checkpoints", "k10_external_attempts", "k10_model_response_receipts",
    "k10_v2_stage_input_usage", "k10_v2_title_triage_manifests", "k10_v2_title_triage_items",
    "k10_v2_title_selection_manifests", "k10_v2_article_admissions", "k10_v2_title_company_hints",
)
RESEARCH_TABLES = (
    "k10_research_stage_results", "k10_research_claims", "k10_research_questions",
    "k10_research_evidence_links", "k10_research_query_paths", "k10_research_fulltext_requests",
    "k10_research_company_assessments",
)
REPORT_TABLES = ("k10_v2_report_coverage", "k10_v2_report_cards")
ALLOWED_TABLES = frozenset((*TASK_TABLES, *RESEARCH_TABLES, *REPORT_TABLES,
    "k10_schema_migrations", "k10_research_snapshot_revisions", "k10_scans",
    "k10_v2_report_runs", "k10_events", "k10_event_revisions", "k10_source_documents",
    "k10_source_document_versions", "k10_v2_strategy_snapshots", "k10_v2_universe_snapshots",
    "k10_v2_universe_members", "k10_v2_profile_snapshots", "k10_v2_company_profiles",
    "k10_v2_company_profile_evidence", "k10_run_config_revisions",
    "k10_execution_config_revisions", "k10_title_triage_policy_revisions"))
SECRET_KEYS = frozenset({"apikey", "token", "accesstoken", "refreshtoken", "password",
    "secret", "clientsecret", "privatekey", "authorization", "credentials", "credential",
    "encryptedkey", "encryptedapikey", "tusharetoken", "tavilyapikey", "apnskey"})


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sanitize(value: Any, path: str, redactions: list[str]) -> Any:
    """Redactions invalidate exact replay and are explicitly included in metadata."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            child = f"{path}.{key}"
            if normalized in SECRET_KEYS and item != "[REDACTED]":
                redactions.append(child)
                out[key] = "[REDACTED]"
            else:
                out[key] = sanitize(item, child, redactions)
        return out
    if isinstance(value, list):
        return [sanitize(item, f"{path}[{i}]", redactions) for i, item in enumerate(value)]
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except ValueError:
                pass
            else:
                before = len(redactions)
                cleaned = sanitize(parsed, path + ".json", redactions)
                return value if len(redactions) == before else encoded(cleaned).decode()
        if value.startswith(("https://", "http://")):
            url = urlsplit(value)
            query = []
            changed = False
            for key, item in parse_qsl(url.query, keep_blank_values=True):
                if re.sub(r"[^a-z0-9]", "", key.lower()) in SECRET_KEYS:
                    redactions.append(path + ".urlQuery." + key)
                    item, changed = "[REDACTED]", True
                query.append((key, item))
            host = url.netloc
            if url.username is not None or url.password is not None:
                redactions.append(path + ".urlCredentials")
                host, changed = host.rsplit("@", 1)[-1], True
            if changed:
                return urlunsplit((url.scheme, host, url.path, urlencode(query), url.fragment))
    return value


def objects(value: Any):
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                yield from objects(json.loads(value))
            except (ValueError, RecursionError):
                pass
    elif isinstance(value, dict):
        yield value
        for item in value.values():
            yield from objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from objects(item)


def export_corpus(db_path: Path, task_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"task_[A-Za-z0-9_-]+", task_id):
        raise ValueError("Explicit task ID required")
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    tables: dict[str, list[dict[str, Any]]] = {}
    missing: list[dict[str, Any]] = []
    redactions: list[str] = []
    seen_rows: dict[str, set[str]] = {}
    try:
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        def take(table: str, where: str = "", args: tuple = ()) -> list[dict[str, Any]]:
            if table not in ALLOWED_TABLES:
                raise ValueError("Table is outside the export allowlist")
            if table not in existing:
                item = {"kind": "table", "table": table}
                if item not in missing:
                    missing.append(item)
                return []
            rows = [dict(r) for r in conn.execute(f'SELECT * FROM "{table}"' + where, args)]
            bucket = tables.setdefault(table, [])
            seen = seen_rows.setdefault(table, set())
            for raw in rows:
                identity = hashlib.sha256(encoded(raw)).hexdigest()
                if identity in seen:
                    continue
                seen.add(identity)
                clean = {}
                for key, value in raw.items():
                    path = f"{table}[{len(bucket)}].{key}"
                    if key.endswith("_json") and value is not None:
                        try:
                            parsed = json.loads(value)
                        except ValueError:
                            missing.append({"kind": "invalid_json", "table": table, "column": key,
                                            "rowSha256": identity})
                            # Malformed blobs cannot be reliably checked for embedded secrets.
                            clean[key] = None
                            continue
                        before = len(redactions)
                        cleaned = sanitize(parsed, path, redactions)
                        # Keep original byte representation when unchanged: paid hashes matter.
                        clean[key] = value if len(redactions) == before else encoded(cleaned).decode()
                    else:
                        clean[key] = sanitize({key: value}, path, redactions)[key]
                bucket.append(clean)
            return rows

        task_rows = take("k10_tasks", " WHERE task_id=?", (task_id,))
        if len(task_rows) != 1:
            raise ValueError("Task not found")
        if task_rows[0]["status"] not in {"completed", "failed", "cancelled", "not_configured"}:
            raise ValueError("Only terminal tasks may be exported")
        for name in TASK_TABLES[1:]:
            take(name, " WHERE task_id=?", (task_id,))
        take("k10_schema_migrations")
        scans = {row["scan_id"] for row in tables.get("k10_scan_execution_bindings", [])}
        for scan in sorted(scans):
            take("k10_scans", " WHERE scan_id=?", (scan,))
            for report in take("k10_v2_report_runs", " WHERE scan_id=?", (scan,)):
                for name in REPORT_TABLES:
                    take(name, " WHERE report_id=?", (report["report_id"],))
                take("k10_v2_strategy_snapshots", " WHERE snapshot_id=?", (report["strategy_snapshot_id"],))
        if not scans:
            missing.append({"kind": "scan_binding", "taskId": task_id})
        snapshots = take("k10_research_snapshot_revisions", " WHERE task_id=? AND revision=("
            "SELECT MAX(r.revision) FROM k10_research_snapshot_revisions r "
            "WHERE r.snapshot_id=k10_research_snapshot_revisions.snapshot_id)", (task_id,))
        for snapshot in snapshots:
            for name in RESEARCH_TABLES:
                take(name, " WHERE snapshot_id=?", (snapshot["snapshot_id"],))
            take("k10_events", " WHERE event_id=?", (snapshot["event_id"],))
            event = take("k10_event_revisions", " WHERE event_id=? AND revision=?",
                         (snapshot["event_id"], snapshot["event_revision"]))
            if not event:
                missing.append({"kind": "event_revision", "eventId": snapshot["event_id"],
                                "revision": snapshot["event_revision"]})
        for strategy in tables.get("k10_v2_strategy_snapshots", []):
            universe, profile = strategy["universe_snapshot_id"], strategy["profile_snapshot_id"]
            for name in ("k10_v2_universe_snapshots", "k10_v2_universe_members"):
                take(name, " WHERE snapshot_id=?", (universe,))
            for name in ("k10_v2_profile_snapshots", "k10_v2_company_profiles", "k10_v2_company_profile_evidence"):
                take(name, " WHERE snapshot_id=?", (profile,))
            take("k10_run_config_revisions", " WHERE config_id=? AND revision=?",
                 (strategy["config_id"], strategy["config_revision"]))
        for binding in tables.get("k10_task_execution_bindings", []):
            take("k10_execution_config_revisions", " WHERE config_id=? AND revision=?",
                 (binding["execution_config_id"], binding["execution_config_revision"]))
        for manifest in tables.get("k10_v2_title_triage_manifests", []):
            take("k10_title_triage_policy_revisions", " WHERE policy_id=? AND revision=?",
                 (manifest["policy_id"], manifest["policy_revision"]))

        refs: set[tuple[str, int]] = set()
        unversioned: set[str] = set()
        # A model can invent a document ID in a rejected paid reply. That reply
        # is evidence to retain, not authority to expand the input whitelist.
        input_tables = {name: rows for name, rows in tables.items() if name != "k10_model_response_receipts"}
        for obj in objects(input_tables):
            doc = obj.get("documentId", obj.get("document_id"))
            rev = obj.get("documentRevision", obj.get("document_revision", obj.get("revision")))
            if isinstance(doc, str):
                if isinstance(rev, int) and not isinstance(rev, bool) and rev > 0:
                    refs.add((doc, rev))
                else:
                    unversioned.add(doc)
        response_only: set[tuple[str, int]] = set()
        for obj in objects(tables.get("k10_model_response_receipts", [])):
            doc = obj.get("documentId", obj.get("document_id"))
            rev = obj.get("documentRevision", obj.get("document_revision", obj.get("revision")))
            if (isinstance(doc, str) and isinstance(rev, int) and not isinstance(rev, bool)
                    and rev > 0 and (doc, rev) not in refs):
                response_only.add((doc, rev))
        for doc, rev in sorted(refs):
            take("k10_source_documents", " WHERE document_id=?", (doc,))
            found = take("k10_source_document_versions", " WHERE document_id=? AND revision=?", (doc, rev))
            if not found:
                missing.append({"kind": "source_version", "documentId": doc, "revision": rev})
        for doc in sorted(unversioned):
            missing.append({"kind": "unversioned_reference", "documentId": doc,
                            "note": "Not resolved using current/latest data"})
        receipts = {r["attempt_id"] for r in tables.get("k10_model_response_receipts", [])}
        for attempt in tables.get("k10_external_attempts", []):
            if attempt["stage"] != "search" and attempt["attempt_id"] not in receipts:
                missing.append({"kind": "receipt", "attemptId": attempt["attempt_id"]})
        return {
            "formatVersion": "neckline-task-corpus-1", "taskId": task_id,
            "provenance": {"kind": "read_only_production_task_export",
                "exportedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                "databaseReadOnly": True, "networkClientsImported": False,
                "snapshotHistory": "latest_snapshot_per_event_plus_all_stage_results",
                "runnableDatabase": False, "newProtocolReplayProven": False},
            "tables": tables, "missing": missing, "redactions": redactions,
            "responseOnlyReferences": [{"documentId": doc, "revision": rev,
                "reason": "Reference appears only in a raw model reply, not in frozen/validated input"}
                for doc, rev in sorted(response_only)],
            "summary": {"rowCounts": {name: len(rows) for name, rows in tables.items()},
                "tableSha256": {name: hashlib.sha256(encoded(rows)).hexdigest() for name, rows in tables.items()},
                "recordedTokens": sum(row.get("total_tokens") or 0 for row in tables.get("k10_external_attempts", [])),
                "recordedSearchCredits": sum(row.get("search_credits") or 0 for row in tables.get("k10_external_attempts", []))},
        }
    finally:
        conn.rollback()
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    value = export_corpus(args.db, args.task)
    sys.stdout.buffer.write(gzip.compress(encoded(value), mtime=0))


if __name__ == "__main__":
    main()
