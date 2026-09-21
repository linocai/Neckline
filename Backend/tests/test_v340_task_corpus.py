"""A real read-only export must isolate task data, versions and credentials."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.export_v340_task_corpus import export_corpus


def database(path: Path, *, status="failed"):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
          CREATE TABLE k10_tasks(task_id TEXT PRIMARY KEY,status TEXT,payload_json TEXT,checkpoint_json TEXT);
          CREATE TABLE k10_external_attempts(attempt_id TEXT,task_id TEXT,stage TEXT,total_tokens INTEGER,search_credits INTEGER);
          CREATE TABLE k10_model_response_receipts(attempt_id TEXT,task_id TEXT,payload_json TEXT);
          CREATE TABLE k10_source_documents(document_id TEXT PRIMARY KEY,canonical_url TEXT);
          CREATE TABLE k10_source_document_versions(document_id TEXT,revision INTEGER,original_text TEXT,metadata_json TEXT);
          CREATE TABLE llm_providers(api_key TEXT);
        """)
        payload = {"sourceRefs": [{"documentId": "source-a", "revision": 1},
                    {"documentId": "missing-version", "revision": 1}, {"documentId": "unversioned"}],
                   "providerBinding": {"apiKey": "credential-never-export", "model": "fixture"},
                   "extra": json.dumps({"authorization": "Bearer private-never-export"})}
        conn.execute("INSERT INTO k10_tasks VALUES(?,?,?,?)", ("task_target", status, json.dumps(payload), "{}"))
        conn.execute("INSERT INTO k10_tasks VALUES(?,?,?,?)", ("task_other", "failed", '{"value":"other-task-private"}', "{}"))
        conn.executemany("INSERT INTO k10_external_attempts VALUES(?,?,?,?,?)", [
            ("paid-1", "task_target", "investigation", 42, None),
            ("missing-receipt", "task_target", "investigation", 7, None),
            ("search-1", "task_target", "search", None, 3),
            ("other-paid", "task_other", "investigation", 900, None)])
        # Deliberately noncanonical whitespace must survive exact-reply exports.
        receipt = '{ "content": "unchanged paid reply", "totalTokens": 42 }'
        conn.execute("INSERT INTO k10_model_response_receipts VALUES(?,?,?)", ("paid-1", "task_target", receipt))
        conn.executemany("INSERT INTO k10_source_documents VALUES(?,?)", [
            ("source-a", "https://example.invalid/a?api_key=url-private"),
            ("missing-version", "https://example.invalid/missing"),
            ("unversioned", "https://example.invalid/u")])
        conn.executemany("INSERT INTO k10_source_document_versions VALUES(?,?,?,?)", [
            ("source-a", 1, "frozen source", '{"credentials":"source-private"}'),
            ("source-a", 2, "future version must not be substituted", "{}"),
            ("missing-version", 2, "also a future version", "{}"),
            ("unversioned", 1, "must not guess latest", "{}")])
        conn.execute("INSERT INTO llm_providers VALUES(?)", ("provider-secret-not-needed",))
    return receipt


def test_closed_task_export_is_readonly_scoped_exact_and_explicit_about_missing(tmp_path):
    path = tmp_path / "source.db"
    receipt = database(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = export_corpus(path, "task_target")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    text = json.dumps(result)
    for secret in ("credential-never-export", "private-never-export", "other-task-private",
                   "provider-secret-not-needed", "url-private", "source-private"):
        assert secret not in text
    assert "future version must not be substituted" not in text
    assert "must not guess latest" not in text
    assert "llm_providers" not in result["tables"]
    assert len(result["tables"]["k10_tasks"]) == 1
    assert result["tables"]["k10_model_response_receipts"][0]["payload_json"] == receipt
    assert result["summary"]["recordedTokens"] == 49
    assert result["summary"]["recordedSearchCredits"] == 3
    assert result["redactions"]
    assert {x["kind"] for x in result["missing"]} >= {"receipt", "source_version", "unversioned_reference"}
    assert result["provenance"]["databaseReadOnly"] is True
    assert result["provenance"]["newProtocolReplayProven"] is False
    assert result["tables"]["k10_source_document_versions"][0]["revision"] == 1


@pytest.mark.parametrize("status", ["running", "queued"])
def test_active_task_is_not_exported_as_a_closed_incident(tmp_path, status):
    path = tmp_path / "active.db"
    database(path, status=status)
    with pytest.raises(ValueError, match="terminal"):
        export_corpus(path, "task_target")


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "does-not-exist.db"
    with pytest.raises(sqlite3.OperationalError):
        export_corpus(path, "task_target")
    assert not path.exists()


def test_no_fallback_to_a_different_task(tmp_path):
    path = tmp_path / "source.db"
    database(path)
    with pytest.raises(ValueError, match="not found"):
        export_corpus(path, "task_absent")


def test_nested_payload_cannot_expand_export_to_an_unbound_scan(tmp_path):
    path = tmp_path / "source.db"
    database(path)
    with sqlite3.connect(path) as conn:
        conn.executescript("""
          CREATE TABLE k10_scan_execution_bindings(task_id TEXT,scan_id TEXT);
          CREATE TABLE k10_scans(scan_id TEXT,description TEXT);
          INSERT INTO k10_scan_execution_bindings VALUES('task_target','scan_owned');
          INSERT INTO k10_scans VALUES('scan_owned','owned evidence');
          INSERT INTO k10_scans VALUES('scan_other','unrelated evidence');
        """)
        conn.execute("UPDATE k10_tasks SET checkpoint_json=? WHERE task_id=?",
                     ('{"history":{"scanId":"scan_other"}}', "task_target"))
    result = export_corpus(path, "task_target")
    assert [r["scan_id"] for r in result["tables"]["k10_scans"]] == ["scan_owned"]
    assert "unrelated evidence" not in json.dumps(result)


def test_malformed_json_is_omitted_with_an_explicit_coverage_gap(tmp_path):
    path = tmp_path / "source.db"
    database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE k10_tasks SET payload_json=? WHERE task_id=?",
                     ('{"apiKey":"malformed-secret"', "task_target"))
    result = export_corpus(path, "task_target")
    assert result["tables"]["k10_tasks"][0]["payload_json"] is None
    assert "malformed-secret" not in json.dumps(result)
    assert any(item["kind"] == "invalid_json" for item in result["missing"])


def test_paid_hallucinated_reference_is_retained_without_becoming_an_input_gap(tmp_path):
    path = tmp_path / "source.db"
    database(path)
    raw = json.dumps({"content": json.dumps({"sourceRef": {"documentId": "invented-source", "revision": 1}})})
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE k10_model_response_receipts SET payload_json=?", (raw,))
    result = export_corpus(path, "task_target")
    assert result["tables"]["k10_model_response_receipts"][0]["payload_json"] == raw
    assert any(x["documentId"] == "invented-source" for x in result["responseOnlyReferences"])
    assert not any(x.get("documentId") == "invented-source" for x in result["missing"])
