"""B101: real B92 producer, then isolated derived-material read faults."""
import hashlib
import json
from pathlib import Path
import socket
import sqlite3

from fastapi.testclient import TestClient
import pytest

from tests import test_b92_report_loopback as b92

from tests.test_b100_source_isolation import emit


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("B100 delivery review forbids real network")
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)


def state(conn, task_id, report_id):
    return {
        "task": conn.execute("SELECT task_id,status,stage FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone(),
        "attempts": conn.execute("SELECT state,count(*) FROM k10_external_attempts GROUP BY state").fetchall(),
        "notificationCount": conn.execute("SELECT count(*) FROM k10_task_notifications WHERE task_id=?", (task_id,)).fetchone()[0],
        "sourceVersionsSha256": hashlib.sha256(repr(conn.execute("SELECT * FROM k10_source_document_versions ORDER BY document_id,revision").fetchall()).encode()).hexdigest(),
        "reportCoverageSha256": hashlib.sha256(repr(conn.execute("SELECT * FROM k10_v2_report_coverage WHERE report_id=?", (report_id,)).fetchall()).encode()).hexdigest(),
    }


def reply(client, path, **params):
    r = client.get(path, params=params)
    result = {"status": r.status_code}
    if r.status_code == 200:
        value = r.json()
        result.update({"items": [item["materialId"] for item in value.get("items", [])],
                       "readGaps": value.get("readGaps", []),
                       "nextCursor": value.get("page", {}).get("nextCursor"),
                       "cards": len(value.get("report", {}).get("eveningCards", []))})
    else:
        result["body"] = r.text[:160]
    return result


@pytest.mark.parametrize("case,revision", [("control", None), ("unknown_revision", 2**31-1), ("max_revision", 2**63-1), ("overflow_revision", 2**63), ("bad_ref_unicode", None)])
def test_material_reference_range(case, revision, tmp_path, monkeypatch):
    flow = b92._make_evening(tmp_path, monkeypatch)
    run_id, run_rev, exec_id, exec_rev, _, _ = flow["bindings"]
    db = flow["dbPath"]
    report_id = flow["eveningReportId"]
    app = b92.base.actual_api(db, config_id=run_id, config_revision=run_rev,
                              execution_id=exec_id, execution_revision=exec_rev).app
    url = f"/api/v1/k10/v2/reports/{report_id}/materials"
    with sqlite3.connect(db) as conn:
        before_state = state(conn, flow["eveningTaskId"], report_id)
        rows = conn.execute("SELECT * FROM k10_v2_report_materials WHERE report_id=? ORDER BY material_id", (report_id,)).fetchall()
        assert len(rows) == 2
        copy = list(rows[0])
        copy[1] = "zz-independent-tail"
        conn.execute("INSERT INTO k10_v2_report_materials VALUES(?,?,?,?,?,?,?,?,?)", copy)
        ref = json.loads(rows[1][7])[0]
        if revision is not None:
            ref["revision"] = revision
        if case == 'bad_ref_unicode':
            ref['documentId'] = 'derived-bad-id\ud800'
        if case != 'control':
            conn.execute("UPDATE k10_v2_report_materials SET source_refs_json=? WHERE report_id=? AND material_id=?",
                         (json.dumps([ref]), report_id, rows[1][1]))
        after_state = state(conn, flow["eveningTaskId"], report_id)
    assert before_state == after_state, "Only the derived material table may change"
    before_hash = hashlib.sha256(db.read_bytes()).hexdigest()
    with TestClient(app, raise_server_exceptions=False) as client:
        report = reply(client, f"/api/v1/k10/v2/reports/{report_id}")
        whole = reply(client, url)
        first = reply(client, url, limit=1)
        middle = reply(client, url, limit=1, cursor=first["nextCursor"])
        retried = reply(client, url, limit=1, cursor=first["nextCursor"])
        forced_tail = reply(client, url, limit=1, cursor=middle['nextCursor'])
    exception = None
    with TestClient(app) as client:
        try:
            client.get(url)
        except Exception as exc:
            import traceback
            exception = {"type": type(exc).__name__, "message": str(exc), "stack": traceback.format_exc()[-4400:]}
    after_hash = hashlib.sha256(db.read_bytes()).hexdigest()
    result = {"case": case, "faultBoundary": "stored derived source_refs_json only; source versions, report/receipt identities untouched",
              "injectedRevision": revision, "stateUnchanged": before_state == after_state,
              "state": after_state, "databaseReadOnly": before_hash == after_hash,
              "report": report, "wholePage": whole, "firstPage": first, "middlePage": middle,
              "middleRetry": retried, "forcedTail": forced_tail, "exception": exception}
    emit("b101-material-" + case + ".json", result)
    assert before_hash == after_hash
    assert report["status"] == 200 and report["cards"] == 1
    assert first["status"] == forced_tail["status"] == 200
    assert all(value[0] not in {"started", "running", "unknown"} for value in after_state["attempts"])
    assert whole["status"] == middle["status"] == retried["status"] == 200
    assert exception is None
    if case != 'control':
        assert middle["items"] == [] and middle["readGaps"] and middle["nextCursor"]
        assert forced_tail["items"] == ["zz-independent-tail"]
    else:
        assert len(whole["items"]) == 3
