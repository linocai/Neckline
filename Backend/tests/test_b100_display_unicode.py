"""Stored optional display corruption must not make independent reports unreadable."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests import test_b92_report_loopback as b92

from tests.test_b100_source_isolation import emit, no_network

@pytest.mark.parametrize('case,field,text', [
    ('control_valid_unicode', 'title', '标题😀'),
    ('title_unpaired_surrogate', 'title', '\ud800'),
    ('original_time_unpaired_surrogate', 'originalPublishedText', '\udfff'),
])
def test_display_unicode_response(case, field, text, tmp_path, monkeypatch):
    flow = b92._make_evening(tmp_path, monkeypatch)
    run_id,run_rev,exec_id,exec_rev,_,_ = flow['bindings']
    db=flow['dbPath']
    app=b92.base.actual_api(db,config_id=run_id,config_revision=run_rev,
        execution_id=exec_id,execution_revision=exec_rev).app
    url=f"/api/v1/k10/v2/reports/{flow['eveningReportId']}"
    with TestClient(app,raise_server_exceptions=False) as client:
        control=client.get(url)
        assert control.status_code==200
        materials=client.get(url+'/materials').json()
        assert len(materials['items'])==2
        source=materials['items'][0]['sourceRefs'][0]
        document_url=f"/api/v1/k10/documents/{source['documentId']}?revision={source['revision']}"
        assert client.get(document_url).status_code==200
    with sqlite3.connect(db) as conn:
        row=conn.execute('SELECT metadata_json FROM k10_source_document_versions WHERE document_id=? AND revision=?',
                         (source['documentId'],source['revision'])).fetchone()
        original=json.loads(row[0]); original[field]=text
        # JSON text uses ASCII escapes. SQLite UTF-8 identity and original body remain unchanged.
        conn.execute('UPDATE k10_source_document_versions SET metadata_json=? WHERE document_id=? AND revision=?',
            (json.dumps(original,ensure_ascii=True),source['documentId'],source['revision']))
        task=conn.execute('SELECT task_id,status,stage FROM k10_tasks WHERE task_id=?',(flow['eveningTaskId'],)).fetchone()
        attempts=conn.execute('SELECT state,count(*) FROM k10_external_attempts GROUP BY state').fetchall()
        notifications=conn.execute('SELECT kind,terminal_status,status,deep_link_json FROM k10_task_notifications WHERE task_id=?',
                                  (flow['eveningTaskId'],)).fetchall()
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    responses={}
    with TestClient(app,raise_server_exceptions=False) as client:
        for name,path in [('detail',url),('latest','/api/v1/k10/v2/reports/latest?window=evening'),
                          ('materials',url+'/materials'),('document',document_url)]:
            r=client.get(path)
            responses[name]={'status':r.status_code}
            if r.status_code==200:
                body=r.json()
                responses[name].update({'items':len(body.get('items',[])),'readGaps':len(body.get('readGaps',[])),
                                       'readWarnings':body.get('readWarnings',[]),
                                       'cards':len(body.get('report',{}).get('eveningCards',[]))})
            else:
                responses[name]['body']=r.text[:160]
    exceptions={}
    with TestClient(app) as client:
        for name,path in [('detail',url),('materials',url+'/materials'),('document',document_url)]:
            try: client.get(path)
            except Exception as exc:
                exceptions[name]={'type':type(exc).__name__,'message':str(exc),'stack':__import__('traceback').format_exc()[-4200:]}
    after=hashlib.sha256(db.read_bytes()).hexdigest()
    result={'case':case,'injection':'optional display metadata only','field':field,'task':task,'attempts':attempts,
            'notification_count':len(notifications),'responses':responses,'exceptions':exceptions,'database_unchanged_by_reads':before==after,
            'db_sha256_before':before,'db_sha256_after':after}
    emit(case + '.json', result)
    assert before==after
    assert task[1]=='completed'
    assert all(state not in {'started','running','unknown'} for state,_ in attempts)
    assert all(v['status'] == 200 for v in responses.values())
    assert not exceptions
    if case != 'control_valid_unicode':
        assert responses['document']['readWarnings']
        if field == 'title':
            assert responses['materials']['items'] == 1
            assert responses['materials']['readGaps'] == 1
    else:
        assert responses['materials']['items'] == 2
        assert not responses['document']['readWarnings']
