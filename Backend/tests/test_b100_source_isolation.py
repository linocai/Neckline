"""B100: source failures stay local through real B92 producers and API."""
import json
import socket
import sqlite3
import traceback
from pathlib import Path

import httpx
import pytest

from neckline.k10 import pipeline, store
from tests import test_b92_report_loopback as current
from tests import test_b98_report_isolation as b98

def emit(name, value):
    import os
    folder = os.environ.get("NK_B100_OUTPUT_DIR")
    if folder:
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_text(json.dumps(value, ensure_ascii=True, indent=2))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Source review forbids real network")
    monkeypatch.setattr(socket, "create_connection", deny)


def summarize(db, task, report, materials, wire, transport, results, handler_results):
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        collect = conn.execute("SELECT task_id,status,checkpoint_json FROM k10_tasks WHERE kind='collect_news'").fetchone()
        attempts = conn.execute("SELECT stage,state,count(*) FROM k10_external_attempts GROUP BY 1,2 ORDER BY 1,2").fetchall()
        sources = conn.execute("SELECT d.source_key,v.document_id,v.revision,json_extract(v.metadata_json,'$.title'),v.original_text,v.excerpt FROM k10_source_documents d JOIN k10_source_document_versions v ON v.document_id=d.document_id ORDER BY 1,2,3").fetchall()
        checkpoints = conn.execute("SELECT stage,status,safe_error_code,count(*) FROM k10_execution_item_checkpoints WHERE task_id=? GROUP BY 1,2,3 ORDER BY 1,2", (task[0],)).fetchall()
    execution = store.task_execution_input(task_id=task[0], db_path=db)
    scan = store.get_scan(scan_id=execution['checkpoint'].get('scanId'), db_path=db)
    return {'collectionTaskId':collect[0], 'collectionStatus':collect[1],
            'collectionSources':json.loads(collect[2]).get('sources'),
            'reportTaskId':task[0], 'taskStatus':task[1], 'handlerResults':handler_results,
            'workerResults':results, 'reportStatus':report['status'],
            'cardCount':len(report.get('eveningCards', [])),
            'materialCount':len(materials.get('items', [])),
            'materialEventTitles':[row.get('eventTitle') for row in materials.get('items', [])],
            'publicGaps':report.get('delivery', {}).get('gaps', []),
            'apiStatus':200, 'sourceVersions':sources,
            'attempts':attempts, 'checkpoints':checkpoints,
            'modelCalls':transport.calls, 'toolCalls':[call['params']['name'] for call in wire.tool_calls],
            'frozenRefs':scan['coverage'].get('inputDocumentRefs'),
            'reportInputContract':execution['executionProfile']['payload']['discovery']['reportInputContract']}


@pytest.mark.parametrize('case', ['control', 'missing_article_title', 'blank_article_title'])
def test_title_admission_pair(case, tmp_path, monkeypatch):
    wire = current._news_wire(article=True)
    original_reply = wire.reply
    injected = []
    def reply(request, body):
        response = original_reply(request, body)
        name = body['params']['name']
        if name not in {'list_news', 'get_news'}:
            return response
        value = response.json()
        data = value['result']['structuredContent']['data']
        for row in data.get('items', [data]):
            row['title'] = '离线验收标题 0000：公司新增经营事实'
            if name == 'list_news' and case != 'control':
                row['title'] = None if case == 'missing_article_title' else '  \n'
                injected.append({'tool':name, 'id':row['id'], 'title':row['title'], 'intro':row['intro']})
        return httpx.Response(200, json=value)
    wire.reply = reply
    original_handler = pipeline.production_scan_handler
    handler_results = []
    def handler(*args, **kwargs):
        result = original_handler(*args, **kwargs)
        handler_results.append({'status':result.status, 'stage':result.stage, 'error':result.error,
                                'checkpoint':{k:v for k,v in result.checkpoint.items()
                                              if k in ['scanId','safeErrorCode']}})
        return result
    monkeypatch.setattr(pipeline, 'production_scan_handler', handler)
    db, task, report, materials, transport, results = b98.make_collected_case(tmp_path, monkeypatch,
        wire=wire, expected_status='completed')
    outcome = summarize(db, task, report, materials, wire, transport, results, handler_results)
    outcome.update(case=case, injected=injected)
    emit(f'title-admission-{case}.json', outcome)
    assert outcome['reportInputContract'] == 'k10-collected-input-3.6.1-b92'
    assert all(state not in {'started','unknown','running'} for _, state, _ in outcome['attempts'])
    assert any(row[0] == 'jin10-flash' and row[4] for row in outcome['sourceVersions'])
    assert any(row[0] == 'jin10-news' and row[5] for row in outcome['sourceVersions'])
    if case == 'control':
        assert task[1] == 'completed' and report['status'] == 'partial'
        assert report['eveningCards'] and len(materials['items']) == 2
        assert any(name == 'understand' for name, _ in transport.calls)
    else:
        assert injected and task[1] == 'completed' and report['status'] == 'partial'
        assert len(materials['items']) == 1
        assert any(name == 'understand' for name, _ in transport.calls)
        gap = next(g for g in outcome['publicGaps'] if g['reasonCode'] == 'source_title_unavailable')
        assert gap['sourceRefs']
        assert any(row[0]=='jin10-news' and row[3] is None and row[5] for row in outcome['sourceVersions'])
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT count(*) FROM k10_execution_item_checkpoints WHERE stage='title_source_gap'").fetchone()[0] == 1



@pytest.mark.parametrize('case', ['control', 'known_scalar_error', 'object_status', 'array_status'])
def test_article_business_shape_pair(case, tmp_path, monkeypatch):
    wire = current._news_wire(article=True)
    original_reply = wire.reply
    injected = []
    def reply(request, body):
        response = original_reply(request, body)
        name = body['params']['name']
        if name not in {'list_news', 'get_news'}:
            return response
        value = response.json()
        structured = value['result']['structuredContent']
        data = structured['data']
        for row in data.get('items', [data]):
            row['title'] = '离线验收标题 0000：公司新增经营事实'
        if name == 'get_news' and case != 'control':
            structured['status'] = ({'bad':'external_shape'} if case == 'object_status' else
                                    [] if case == 'array_status' else 'supplier_error')
            injected.append({'tool':name, 'status':structured['status']})
        return httpx.Response(200, json=value)
    wire.reply = reply
    original_handler = pipeline.production_scan_handler
    handler_results = []
    def handler(*args, **kwargs):
        try:
            result = original_handler(*args, **kwargs)
        except Exception as exc:
            handler_results.append({'raised':type(exc).__name__, 'safeMessage':str(exc),
                                    'frames':[[item.filename,item.lineno,item.name]
                                              for item in traceback.extract_tb(exc.__traceback__)]})
            raise
        handler_results.append({'status':result.status, 'stage':result.stage, 'error':result.error,
                                'checkpoint':{k:v for k,v in result.checkpoint.items()
                                              if k in ['scanId','safeErrorCode']}})
        return result
    monkeypatch.setattr(pipeline, 'production_scan_handler', handler)
    db, task, report, materials, transport, results = b98.make_collected_case(tmp_path, monkeypatch,
        wire=wire, expected_status='completed')
    outcome = summarize(db, task, report, materials, wire, transport, results, handler_results)
    outcome.update(case=case, injected=injected)
    emit(f'article-business-shape-{case}.json', outcome)
    assert outcome['reportInputContract'] == 'k10-collected-input-3.6.1-b92'
    assert all(state not in {'started','unknown','running'} for _, state, _ in outcome['attempts'])
    assert any(row[0] == 'jin10-flash' and row[4] for row in outcome['sourceVersions'])
    if case in {'control','known_scalar_error'}:
        assert task[1] == 'completed' and report['status'] == 'partial'
        assert len(materials['items']) == (2 if case == 'control' else 1)
        if case == 'control':
            assert report['eveningCards']
        if case == 'known_scalar_error':
            # The inherited fixture intentionally maps event 0/1 to one
            # company. The article gap excludes that company, while the
            # independent flash's completed material remains readable.
            assert not report['eveningCards']
            assert any(gap['reasonCode']=='business_status_error'
                       and gap['companyCodes'] == ['300002.SZ'] for gap in outcome['publicGaps'])
            assert any(name == 'understand' for name, _ in transport.calls)
    else:
        assert injected and task[1] == 'completed' and report['status'] == 'partial'
        assert len(materials['items']) == 1
        assert any(row[0] == 'jin10:get_news' and row[1] == 'succeeded' for row in outcome['attempts'])
        assert any(name == 'understand' for name, _ in transport.calls)
        assert any(g['reasonCode'] == 'business_status_error' for g in outcome['publicGaps'])
        assert not any('raised' in item for item in handler_results)
