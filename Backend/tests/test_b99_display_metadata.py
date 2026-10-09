"""B92 real producer -> worker -> API regression for optional display corruption."""
import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from tests import test_b92_report_loopback as b92
from tests.test_b99_failure_scope import emit


@pytest.mark.parametrize('case,metadata', [
    ('invalid_json', '{bad-display'), ('wrong_title_type', '{"title":123}'),
    ('array_metadata', '[]'), ('wrong_event_time', '{"eventTime":{"value":123}}'),
    ('wrong_original_title', '{"originalTitle":123}')])
def test_source_display_metadata_keeps_report_and_original_readable(tmp_path, monkeypatch, case, metadata):
    flow = b92._make_evening(tmp_path, monkeypatch)
    run_id, run_rev, exec_id, exec_rev, _, _ = flow['bindings']
    db = flow['dbPath']
    app = b92.base.actual_api(db, config_id=run_id, config_revision=run_rev,
        execution_id=exec_id, execution_revision=exec_rev).app
    report_url = f"/api/v1/k10/v2/reports/{flow['eveningReportId']}"
    with TestClient(app) as client:
        control = client.get(report_url).json()
        original_materials = client.get(report_url + '/materials').json()
        ref = original_materials['items'][0]['sourceRefs'][0]
        key = (ref['documentId'], ref['revision'])
        document_url = f"/api/v1/k10/documents/{key[0]}?revision={key[1]}"
        original = client.get(document_url).json()
    independent = [item['materialId'] for item in original_materials['items']
        if key not in {(source['documentId'], source['revision']) for source in item['sourceRefs']}]
    assert independent and len(original_materials['items']) == 2
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT status FROM k10_tasks WHERE task_id=?',
            (flow['eveningTaskId'],)).fetchone()[0] == 'completed'
        conn.execute('UPDATE k10_source_document_versions SET metadata_json=? WHERE document_id=? AND revision=?',
                     (metadata, *key))
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    with TestClient(app) as client:
        detail = client.get(report_url)
        latest = client.get('/api/v1/k10/v2/reports/latest?window=evening')
        document_response = client.get(document_url)
        material_response = client.get(report_url + '/materials')
        assert [detail.status_code, latest.status_code, document_response.status_code, material_response.status_code] == [200]*4
        report, document, materials = detail.json(), document_response.json(), material_response.json()
        assert report['report']['coverageGaps'] and latest.json()['report']['coverageGaps']
        assert len(report['report']['eveningCards']) == len(control['report']['eveningCards'])
        assert report['report']['delivery'] == control['report']['delivery']
        assert document['readWarnings'] and document['body'] == original['body']
        assert document['contentKind'] == original['contentKind'] == 'original'
        assert document['documentId'] == key[0] and document['revision'] == key[1]
        chunks, offset = [], 0
        for _ in range(200):
            page = client.get(document_url, params={'offset': offset, 'limit': 12}).json()
            assert page['readWarnings'] == document['readWarnings']
            chunks.append(page['body'])
            cursor = page['page']['nextCursor']
            if cursor is None:
                break
            assert int(cursor) > offset
            offset = int(cursor)
        else:
            pytest.fail('original pagination did not terminate')
        assert ''.join(chunks) == original['body']
        if case in {'invalid_json', 'wrong_title_type', 'array_metadata'}:
            assert [item['materialId'] for item in materials['items']] == independent
            assert len(materials['readGaps']) == 1
        else:
            assert len(materials['items']) == 2
        if case == 'invalid_json':
            emit('metadata-report.json', report)
            emit('metadata-document.json', document)
            emit('metadata-document-page.json', client.get(document_url, params={'offset': 0, 'limit': 12}).json())
            emit('metadata-document-next.json', client.get(document_url, params={'offset': 12, 'limit': 24000}).json())
            emit('metadata-materials.json', materials)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_display_metadata_does_not_hide_database_failure(monkeypatch):
    from neckline.api.k10 import _document_reference_map
    class BrokenReader:
        def execute(self, *args):
            raise sqlite3.DatabaseError('unreadable database')
    with pytest.raises(sqlite3.DatabaseError):
        _document_reference_map(BrokenReader(), [{'documentId': 'source', 'revision': 1}], read_warnings=[])
