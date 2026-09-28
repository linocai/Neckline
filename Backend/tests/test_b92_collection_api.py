"""Real collection configuration producer -> isolated SQLite -> FastAPI.

No report, strategy binding, live provider or fabricated response is needed
to expose the independently configured collection state.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
import json
from pathlib import Path
import sqlite3

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
import pytest

from neckline.db import init_schema
from neckline.k10 import store
from neckline.k10.cli import main
from neckline.k10.schema import initialize_schema


CONFIG = Path(__file__).resolve().parents[1] / "neckline/config/k10-collection-v1.json"
AUTH = {"Authorization": "Bearer b92-isolated-api-token"}


@dataclass(frozen=True)
class EmptyCollectionFixture:
    database: Path
    config_id: str
    revision: int


def generate_empty_collection(root: Path) -> EmptyCollectionFixture:
    """Reusable native QA fixture: the CLI owns the actual configuration revision."""
    root.mkdir(parents=True, exist_ok=True)
    database = root / "collection-empty.sqlite"
    init_schema(database)
    initialize_schema(database)
    store.set_run_control(state="closed", reason_code="user_paused",
                          changed_at="2026-09-26T08:00:00+08:00", changed_by="isolated-acceptance",
                          db_path=database)
    output = StringIO()
    with redirect_stdout(output):
        assert main(["configure-collection", "--db", str(database), "--config-id", "b92-fixture-collection",
                     "--file", str(CONFIG)]) == 0
    binding = json.loads(output.getvalue())
    return EmptyCollectionFixture(database, binding["configId"], binding["revision"])


def client_for(database, binding):
    from neckline.api.collection import create_router
    def require_token(authorization: str = Header(default="")):
        if authorization != AUTH["Authorization"]:
            raise HTTPException(status_code=401, detail="unauthorized")
    app = FastAPI()
    app.include_router(create_router(db_path_provider=lambda: database,
                                    require_token_dependency=require_token,
                                    current_collection_binding_provider=lambda: binding))
    return TestClient(app)


def dump(database):
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return "\n".join(connection.iterdump())


def count(database, table):
    assert table in {"k10_tasks", "k10_scans", "k10_run_config_revisions", "k10_external_attempts"}
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]


def test_configured_collection_without_report_or_strategy_has_read_only_status(tmp_path, monkeypatch):
    fixture = generate_empty_collection(tmp_path)
    monkeypatch.setenv("TUSHARE_TOKEN", "b92-test-only-tushare")
    monkeypatch.setenv("JIN10_MCP_TOKEN", "b92-test-only-jin10")
    before = dump(fixture.database)
    with client_for(fixture.database, (fixture.config_id, fixture.revision, None)) as client:
        response = client.get("/api/v1/k10/collection/status", headers=AUTH)
        assert response.status_code == 200
        state = response.json()
    assert state["schemaVersion"] == "10"
    assert state["configuration"] == {"state": "configured", "configId": fixture.config_id,
                                       "revision": fixture.revision, "missing": []}
    assert state["control"]["state"] == "closed"
    assert state["activeTasks"] == state["latestRuns"] == []
    assert {source["sourceKey"] for source in state["sources"]} == {
        "tushare-major-news", "jin10-flash", "jin10-news"}
    assert all(source["credentialConfigured"] for source in state["sources"])
    assert all(source["coverageThrough"] is None for source in state["sources"])
    assert "b92-test-only" not in response.text
    assert dump(fixture.database) == before, "Status GET must not migrate, create controls or record runs"
    for table in ("k10_tasks", "k10_scans", "k10_run_config_revisions", "k10_external_attempts"):
        assert count(fixture.database, table) == 0


def test_collection_open_close_never_changes_report_pause_or_enqueues_work(tmp_path):
    fixture = generate_empty_collection(tmp_path)
    report_before = store.run_control_status(db_path=fixture.database)
    with client_for(fixture.database, (fixture.config_id, fixture.revision, None)) as client:
        for requested in ("open", "closed"):
            response = client.post("/api/v1/k10/collection/control", headers=AUTH, json={"state": requested})
            assert response.status_code == 200
            assert response.json()["control"]["state"] == requested
            assert store.run_control_status(db_path=fixture.database) == report_before
    assert count(fixture.database, "k10_tasks") == count(fixture.database, "k10_scans") == 0
    assert report_before["state"] == "closed"


@pytest.mark.parametrize("binding", [(None, None, None), ("missing", 1, None),
                                      ("b92-fixture-collection", 999, None),
                                      (None, None, "invalid_collection_binding")])
def test_missing_explicit_binding_does_not_fall_back_to_latest_config(tmp_path, binding):
    fixture = generate_empty_collection(tmp_path)
    with client_for(fixture.database, binding) as client:
        response = client.get("/api/v1/k10/collection/status", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["configuration"]["state"] == "not_configured"
        assert response.json()["configuration"]["missing"]
        before = dump(fixture.database)
        opened = client.post("/api/v1/k10/collection/control", headers=AUTH, json={"state": "open"})
        assert opened.status_code in {400, 409, 422, 503}
        assert dump(fixture.database) == before


@pytest.mark.parametrize("method,path,payload", [
    ("get", "/api/v1/k10/collection/status", None),
    ("post", "/api/v1/k10/collection/control", {"state": "open"}),
])
def test_collection_routes_keep_authentication_before_reads_and_writes(tmp_path, method, path, payload):
    fixture = generate_empty_collection(tmp_path)
    before = dump(fixture.database)
    with client_for(fixture.database, (fixture.config_id, fixture.revision, None)) as client:
        response = client.request(method, path, **({"json": payload} if payload else {}))
    assert response.status_code == 401 and dump(fixture.database) == before


def test_missing_database_get_cannot_bootstrap_storage(tmp_path):
    database = tmp_path / "absent.sqlite"
    with client_for(database, (None, None, None)) as client:
        response = client.get("/api/v1/k10/collection/status", headers=AUTH)
    assert response.status_code == 503
    assert not database.exists(), "GET must never create the target database"


def test_credentials_absent_are_visible_without_disabling_saved_configuration(tmp_path, monkeypatch):
    fixture = generate_empty_collection(tmp_path)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("JIN10_MCP_TOKEN", raising=False)
    with client_for(fixture.database, (fixture.config_id, fixture.revision, None)) as client:
        response = client.get("/api/v1/k10/collection/status", headers=AUTH)
    assert response.status_code == 200
    state = response.json()
    assert state["configuration"]["state"] == "configured"
    assert all(not source["credentialConfigured"] for source in state["sources"])
    assert all(source["coverageThrough"] is None for source in state["sources"])
    assert state["latestRuns"] == []


def test_b94_status_reads_project_large_paid_receipts_without_decoding_them(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from neckline.k10.schema import write_connection
    fixture = generate_empty_collection(tmp_path)
    output = StringIO()
    with redirect_stdout(output):
        assert main(['collection-control', '--db', str(fixture.database), '--state', 'open',
                     '--config-id', fixture.config_id, '--config-revision', str(fixture.revision)]) == 0
    with redirect_stdout(output := StringIO()):
        assert main(['enqueue-collection', '--db', str(fixture.database),
                     '--slot', '2026-09-28T20:00:00+08:00', '--config-id', fixture.config_id,
                     '--config-revision', str(fixture.revision)]) == 0
    task_id = output.getvalue().strip()
    checkpoint = {'executionStartedAt': '2026-09-28T12:00:00+00:00',
        'sources': {'jin10-flash': {'state': 'partial', 'coverageThrough': None,
            'observedStartAt': '2026-09-26T20:00:00+08:00',
            'observedEndAt': '2026-09-28T19:00:00+08:00',
            'limitations': ['page_cursor_invalid'], 'documentRefs': [{'private': 'reference'}]}},
        'toolReceipts': {'paid-reply': {'body': 'private-paid-reply' * 500000}}}
    raw = json.dumps(checkpoint)
    with write_connection(fixture.database) as conn:
        conn.execute('UPDATE k10_tasks SET checkpoint_json=? WHERE task_id=?', (raw, task_id))
    original_loads = json.loads
    def bounded_loads(value, *args, **kwargs):
        # Python must never expand the receipt or document-ref tree to render
        # public task status; the old API and active-control readers fail here.
        if isinstance(value, (str, bytes)):
            assert len(value) < 65536, 'status read decoded the full paid checkpoint'
        return original_loads(value, *args, **kwargs)
    monkeypatch.setattr(json, 'loads', bounded_loads)
    with client_for(fixture.database, (fixture.config_id, fixture.revision, None)) as client:
        def read(_):
            response = client.get('/api/v1/k10/collection/status', headers=AUTH)
            assert response.status_code == 200
            body = response.json()
            source = next(s for s in body['sources'] if s['sourceKey'] == 'jin10-flash')
            assert source['state'] == 'partial' and source['limitations'] == ['page_cursor_invalid']
            assert 'private-paid-reply' not in response.text
            control = store.run_control_execution_status(db_path=fixture.database)
            assert control['activeTasks'][0]['executionStartedAt'] == checkpoint['executionStartedAt']
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(read, range(8)))
    with sqlite3.connect(f'file:{fixture.database}?mode=ro', uri=True) as conn:
        assert conn.execute('SELECT checkpoint_json FROM k10_tasks WHERE task_id=?', (task_id,)).fetchone()[0] == raw
