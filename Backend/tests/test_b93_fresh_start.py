"""Fresh storage isolation: synthetic databases, no report or network."""
from datetime import datetime, timezone
from hashlib import sha256
import sqlite3
import pytest
from fastapi.testclient import TestClient
from neckline import db
from neckline.fresh_start import RetiredDataError, initialize_fresh_database
from neckline.k10 import schema, store


def old_database(path):
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE retired_secret (body TEXT)')
        conn.execute("INSERT INTO retired_secret VALUES ('old private original')")
    return path


@pytest.mark.parametrize('entry', [db.init_schema, schema.initialize_schema, db.get_connection,
    db.readonly_connection, schema.read_connection, schema.write_connection])
def test_old_database_rejected_without_business_read_or_mutation(tmp_path, monkeypatch, entry):
    path = old_database(tmp_path / 'old.sqlite')
    before = sha256(path.read_bytes()).hexdigest()
    statements = []
    real_connect = sqlite3.connect
    def traced(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(sqlite3, 'connect', traced)
    with pytest.raises(RetiredDataError):
        result = entry(path)
        if hasattr(result, '__enter__'):
            with result:
                pytest.fail('retired connection exposed')
    assert not any('retired_secret' in sql.lower() for sql in statements)
    assert sha256(path.read_bytes()).hexdigest() == before


def test_new_database_empty_and_cannot_overwrite(tmp_path):
    path = tmp_path / 'new.sqlite'
    assert initialize_fresh_database(target=path)['dataStart'] == 'B92'
    with schema.read_connection(path) as conn:
        schema.require_schema(conn)
        for table in ['k10_tasks', 'k10_scans', 'k10_source_documents', 'k10_external_attempts', 'devices']:
            assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    db.init_schema(path)
    schema.initialize_schema(path)
    with pytest.raises(FileExistsError):
        initialize_fresh_database(target=path)


def test_initializer_refuses_orphan_sidecars(tmp_path):
    path = tmp_path / 'new.sqlite'
    sidecar = tmp_path / 'new.sqlite-wal'
    sidecar.write_bytes(b'old wal')
    with pytest.raises(ValueError):
        initialize_fresh_database(target=path)
    assert not path.exists()
    assert sidecar.read_bytes() == b'old wal'


def test_api_refuses_old_database(tmp_path, monkeypatch):
    from neckline.api import app as module
    from neckline.api.deps import require_token
    monkeypatch.setattr(module, '_DB_PATH_OVERRIDE', old_database(tmp_path / 'old.sqlite'))
    module.app.dependency_overrides[require_token] = lambda: None
    try:
        client = TestClient(module.app)
        for url in ['/api/v1/settings', '/api/v1/k10/v2/reports/latest']:
            response = client.get(url)
            assert response.status_code == 503, response.text
            assert response.json()['detail']['reason'] == 'retired_database'
    finally:
        module.app.dependency_overrides.pop(require_token, None)


def test_restore_rejects_old_backup_before_hash_or_integrity(tmp_path, monkeypatch):
    from neckline.k10 import migration
    target = tmp_path / 'new.sqlite'
    initialize_fresh_database(target=target)
    backup = old_database(tmp_path / 'old.sqlite')
    monkeypatch.setattr(migration, 'file_sha256', lambda _: pytest.fail('old backup bytes read'))
    with pytest.raises(RetiredDataError):
        migration.restore_backup(target=target, confirmed_target=target, backup=backup,
                                 expected_sha256='unused', writers_stopped=True)


def test_old_market_directory_rejected_new_writes_readable(tmp_path):
    import polars as pl
    from neckline.data.market_data import get_market_slice, write_table_day
    old = tmp_path / 'old-market'
    (old / 'daily').mkdir(parents=True)
    with pytest.raises(RetiredDataError):
        get_market_slice('2026-09-26', parquet_dir=old)
    new = tmp_path / 'new-market'
    write_table_day('daily', '2026-09-26', pl.DataFrame({'ts_code': ['000001.SZ'],
        'trade_date': [datetime(2026, 9, 26).date()], 'close': [10.0]}), parquet_dir=new)
    assert get_market_slice('2026-09-26', parquet_dir=new).height == 1


def test_first_freeze_bootstrap_does_not_gate_message_age(tmp_path):
    path = tmp_path / 'new.sqlite'
    initialize_fresh_database(target=path)
    store.create_scan(scan_id='new', window_kind='evening', cutoff_at='2026-09-26T21:00:00+08:00',
        config_id=None, config_revision=None, status='running', coverage={},
        created_at='2026-09-26T21:00:00+08:00', completed_at=None, db_path=path)
    with schema.write_connection(path) as conn:
        conn.execute("INSERT INTO k10_source_documents VALUES ('doc','jin10-flash','remote',NULL,'2026-09-26T12:00:00Z')")
        conn.execute("INSERT INTO k10_source_document_versions VALUES ('doc',1,'hash','2026-01-01T00:00:00Z','exact','2026-09-26T12:00:00Z','still relevant original',NULL,'fixture','{}','2026-09-26T12:00:00Z')")
    args = dict(db_path=path, scan_id='new', window='evening', source_keys=['jin10-flash'],
                frozen_at=datetime(2026, 9, 26, 13, tzinfo=timezone.utc))
    with pytest.raises(store.K10Conflict, match='bootstrap'):
        store.freeze_collected_input(**args)
    result = store.freeze_collected_input(**args, bootstrap_at='2026-09-26T00:00:00Z')
    assert result['inputDocumentRefs'][0]['publishedAt'] == '2026-01-01T00:00:00Z'


@pytest.mark.parametrize('task_state', ['failed', 'not_configured', 'running', 'queued', 'retry_pending'])
def test_fresh_failure_persistence_api_preserves_actual_reason(tmp_path, monkeypatch, task_state):
    import json
    import os
    from pathlib import Path
    from neckline.api import app as module
    from neckline.api.deps import require_token
    from neckline.k10.v2_store import record_incomplete_report
    from tests.test_b92_report_loopback import _bindings
    path = tmp_path / 'failure.sqlite'
    _bindings(path)  # configuration and pool only; never executes a report.
    store.create_scan(scan_id='fresh_failure', window_kind='evening', cutoff_at='2026-09-26T21:00:00+08:00',
        config_id=None, config_revision=None, status='failed', coverage={},
        created_at='2026-09-26T21:00:00+08:00', completed_at='2026-09-26T21:00:01+08:00', db_path=path)
    record_incomplete_report(db_path=path, scan_id='fresh_failure', snapshot_id='k10-v2-b92-isolated',
        state=task_state, error_code='fixture_execution_failed', created_at='2026-09-26T21:00:01+08:00')
    monkeypatch.setattr(module, '_DB_PATH_OVERRIDE', path)
    module.app.dependency_overrides[require_token] = lambda: None
    try:
        response = TestClient(module.app).get('/api/v1/k10/v2/reports/latest')
        assert response.status_code == 200, response.text
        data = response.json()
        processing = task_state in {'queued', 'running', 'retry_pending'}
        assert data['state'] == ('processing' if processing else task_state)
        assert data['reason']['reason'] == ('report_processing' if task_state in {'running','queued'} else 'fixture_execution_failed')
        assert data['report']['status'] == ('queued' if task_state == 'retry_pending' else task_state)
        assert not data['report']['eveningCards']
        if os.environ.get('NK_B93_FAILURE_API_OUTPUT'):
            output = Path(os.environ['NK_B93_FAILURE_API_OUTPUT'])
            if task_state == 'failed':
                output.write_text(json.dumps(data, ensure_ascii=False))
            elif task_state == 'running':
                output.with_name('processing-api.json').write_text(json.dumps(data, ensure_ascii=False))
    finally:
        module.app.dependency_overrides.pop(require_token, None)


def test_morning_freeze_omits_backlog_but_keeps_newly_collected_older_original(tmp_path):
    import json
    from neckline.k10.v2_store import record_incomplete_report
    from tests.test_b92_report_loopback import _bindings
    path = tmp_path / 'morning-input.sqlite'
    _bindings(path)
    for scan_id, kind, cutoff in [('parent','evening','2026-09-26T13:00:00Z'),
                                  ('morning','morning','2026-09-27T00:30:00Z')]:
        coverage = {'collectedInput': {'inputFrozenAt': cutoff, 'inputDocumentRefs': []}} if kind=='evening' else {}
        store.create_scan(scan_id=scan_id, window_kind=kind, cutoff_at=cutoff, config_id=None,
            config_revision=None, status='running', coverage=coverage,
            created_at=cutoff, completed_at=None, db_path=path)
    record_incomplete_report(db_path=path, scan_id='parent', snapshot_id='k10-v2-b92-isolated',
        state='failed', error_code='fixture', created_at='2026-09-26T13:00:00Z')
    with schema.write_connection(path) as conn:
        conn.execute("UPDATE k10_v2_report_runs SET status='completed',available_at='2026-09-26T14:00:00Z' WHERE scan_id='parent'")
        for doc, fetched in [('backlog','2026-09-26T12:00:00Z'),('new','2026-09-27T00:00:00Z')]:
            conn.execute("INSERT INTO k10_source_documents VALUES (?, 'jin10-flash', ?, NULL, ?)", (doc, doc, fetched))
            conn.execute("INSERT INTO k10_source_document_versions VALUES (?,1,?,'2026-01-01T00:00:00Z','exact',?,'original',NULL,'fixture','{}',?)", (doc, doc, fetched, fetched))
    data = store.freeze_collected_input(db_path=path, scan_id='morning', window='morning',
        source_keys=['jin10-flash'], frozen_at=datetime(2026,9,27,0,30,tzinfo=timezone.utc))
    assert data['sourceVersionRowidCeiling'] == 2
    assert [r['documentId'] for r in data['inputDocumentRefs']] == ['new']
    assert data['inputDocumentRefs'][0]['publishedAt'] == '2026-01-01T00:00:00Z'
    # New-generation prior context remains addressable for an explicit question.
    assert store.load_document_versions(refs=[{'documentId':'backlog','revision':1}],
                                        db_path=path, source_keys=('jin10-flash',))


def test_offline_corpus_export_cannot_bypass_old_database_guard(tmp_path):
    import importlib.util
    from pathlib import Path
    script = Path(__file__).parents[1] / "scripts/export_v340_task_corpus.py"
    spec = importlib.util.spec_from_file_location("b93_export_guard", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="禁止读取或导出"):
        module.export_corpus(old_database(tmp_path / "retired.sqlite"), "task_old")
