"""Independent read-only review reproductions; temporary DBs, offline transports."""
import json
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from neckline.k10 import pipeline, store
from neckline.k10.research_runtime import _Investigation
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e


def payload_of(request):
    text = json.loads(request.content)['messages'][-1]['content']
    return json.loads(text.split('<untrusted-k10-evidence>\n', 1)[1].split('\n</untrusted-k10-evidence>', 1)[0])


def reply(value):
    return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(value)}, 'finish_reason': 'stop'}],
                                   'usage': {'prompt_tokens': 3, 'completion_tokens': 3, 'total_tokens': 6}})


@pytest.mark.parametrize('kind,pause', [('question', False), ('question', True), ('claim', False), ('claim', True)])
def test_question_local_read_tracks_current_revision(tmp_path, monkeypatch, kind, pause):
    tick, tripped = [0.0], [False]
    monkeypatch.setattr(pipeline, 'time', SimpleNamespace(monotonic=lambda: tick[0]))
    original_record = _Investigation._record
    def save_then_pause(self, result, **kwargs):
        original_record(self, result, **kwargs)
        if pause and not tripped[0] and (result.conclusion or {}).get('runtimeContextRead'):
            tripped[0] = True
            tick[0] = 10000.0
    monkeypatch.setattr(_Investigation, '_record', save_then_pause)
    collection, field, object_id = (('questions', 'missingEvidence', 'q-1') if kind == 'question'
        else ('claims', 'decisionImpact', 'article-claim-1'))
    transport = httpx.MockTransport
    seen = []
    closed = []
    def wrap(handler):
        def respond(request):
            payload = payload_of(request)
            action = payload.get('action')
            packet = payload.get('evidencePacket', {})
            if action == 'plan_queries':
                if not packet.get('contextResults'):
                    return reply({'action': action, 'contextRequests': [
                        {'kind': kind, 'id': object_id, 'purpose': '读取当前剩余证据缺口'}]})
                seen.append({'current': packet[collection][0][field],
                             'localRead': packet['contextResults'][0]['value'][field]})
            response = handler(request)
            body = response.json()
            value = json.loads(body['choices'][0]['message']['content'])
            if action == 'close_research' and not closed:
                closed.append(True)
                value['questions'] = [{'questionId': 'q-1', 'state': 'open',
                    'missingEvidence': ['新增实质缺口：当前送样适用范围已变化'],
                    'knownEvidence': packet['questions'][0]['knownEvidence'],
                    'resumeCondition': '取得适用范围说明'}]
                if kind == 'claim':
                    value['claims'] = [{'claimId': object_id, 'decisionImpact': '适用范围变化导致影响判断改变'}]
                body['choices'][0]['message']['content'] = json.dumps(value)
                return httpx.Response(response.status_code, json=body)
            return response
        return transport(respond)
    monkeypatch.setattr(httpx, 'MockTransport', wrap)
    db, task_id, task, calls, gateway = e2e._run(tmp_path, monkeypatch, v2=True)
    if pause:
        assert task.status == 'queued'
        tick[0] = 0.0
        task = resume_task(db, task_id, tmp_path)
    print('question-read', json.dumps({'task': task.status, 'seen': seen}, ensure_ascii=False))
    assert task.status == 'completed'
    assert len(seen) == 2
    assert seen[-1]['localRead'] == seen[-1]['current']
    assert seen[0]['localRead'] != seen[-1]['localRead']
    with sqlite3.connect(db) as conn:
        reads = [json.loads(row[0])['conclusion']['runtimeContextRead'] for row in conn.execute(
            "SELECT result_json FROM k10_research_stage_results WHERE json_extract(result_json,'$.conclusion.runtimeContextRead') IS NOT NULL")]
    assert len(reads) == 2  # Restart reuses the exact already-paid input/read.
    assert reads[0]['contentSha256'] != reads[1]['contentSha256']


@pytest.mark.parametrize('continue_research', [False, True])
def test_empty_search_pause_resumes_closure_before_another_query(tmp_path, monkeypatch, continue_research):
    tick, tripped = [0.0], [False]
    monkeypatch.setattr(pipeline, 'time', SimpleNamespace(monotonic=lambda: tick[0]))
    original = _Investigation._tool
    def save_then_yield(self, bundle, **kwargs):
        original(self, bundle, **kwargs)
        if not tripped[0] and kwargs.get('path') is not None and not bundle.documents:
            tripped[0] = True
            tick[0] = 10000.0
    monkeypatch.setattr(_Investigation, '_tool', save_then_yield)
    db, task_id, task, calls, gateway = e2e._run(tmp_path, monkeypatch, v2=True, pending_ranking=None if continue_research else 'legacy_wrong')
    assert task.status == 'queued', task
    before = list(calls)
    tick[0] = 0.0
    with sqlite3.connect(db) as conn:
        due = datetime.fromisoformat(conn.execute('SELECT not_before_at FROM k10_task_retry_schedules WHERE task_id=?', (task_id,)).fetchone()[0])
    resumed = run_once(db_path=db, task_id=task_id, worker_id='review-empty-resume', lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet'),
        clock=lambda: due + timedelta(seconds=1))
    after = calls[len(before):]
    with sqlite3.connect(db) as conn:
        physical = conn.execute('SELECT stage,count(*) FROM k10_external_attempts WHERE task_id=? GROUP BY stage', (task_id,)).fetchall()
    print('empty-search-resume', json.dumps({'taskId': task_id, 'task': resumed.status, 'before': before, 'after': after,
        'queries': gateway.search_paths, 'physicalAttempts': physical}, ensure_ascii=False))
    assert resumed.status == 'completed'
    assert after[0] == 'research:close_research'
    assert len(gateway.search_paths) == (2 if continue_research else 1)
    assert dict(physical)['investigation'] == (6 if continue_research else 4)


def test_control_empty_search_without_pause_closes_after_one_query(tmp_path, monkeypatch):
    db, task_id, task, calls, gateway = e2e._run(tmp_path, monkeypatch, v2=True, pending_ranking='legacy_wrong')
    with sqlite3.connect(db) as conn:
        physical = conn.execute('SELECT stage,count(*) FROM k10_external_attempts WHERE task_id=? GROUP BY stage', (task_id,)).fetchall()
    print('empty-search-control', json.dumps({'taskId': task_id, 'task': task.status, 'calls': calls,
        'queries': gateway.search_paths, 'physicalAttempts': physical}, ensure_ascii=False))
    assert task.status == 'completed'
    assert gateway.search_paths == ['path-1']
    assert dict(physical)['investigation'] == 4


@pytest.mark.parametrize("correction", ["reject", "visible", "read"])
def test_comparison_cannot_publish_a_source_hidden_from_this_request(tmp_path, monkeypatch, correction):
    from dataclasses import replace
    from neckline.k10.v2_store import read_report
    from tests.test_b54_review_regressions import client_for
    original_news = e2e._News
    class News(original_news):
        def fetch_incremental(self, request):
            result = super().fetch_incremental(request)
            first = result.documents[0]
            extra = replace(first, external_id='news-2', original_text='隐含来源：仅附旧项目背景，无本次独立命题。',
                metadata={'title': '项目补充背景说明'})
            return replace(result, documents=(first, extra))
    monkeypatch.setattr(e2e, '_News', News)
    transport = httpx.MockTransport
    hidden, observed = [], []
    def wrap(handler):
        def respond(request):
            payload = payload_of(request)
            response = handler(request)
            body = response.json()
            value = json.loads(body['choices'][0]['message']['content'])
            if 'items' in value and all('matterKey' in row for row in value['items']):
                for index, row in enumerate(value['items']):
                    row['matterKey'] = f'project-{index}'
            if 'events' in value and '隐含来源' in json.dumps(payload, ensure_ascii=False):
                value['events'][0]['claims'] = []
                hidden.append(value['events'][0]['sourceRefs'][0])
            if payload.get('action') == 'compare_companies':
                assert hidden
                packet = payload['evidencePacket']
                observed.append({'hidden': hidden[0], 'visible': packet['allowedEvidenceRefs'],
                    'packetContainsHiddenID': hidden[0]['documentId'] in json.dumps(packet)})
                if not packet.get('contextResults'):
                    assert hidden[0] not in packet['allowedEvidenceRefs']
                if len(observed) > 1 and correction != 'reject':
                    if not packet.get('contextResults'):
                        assert 'investigation_reference_invalid' in json.loads(request.content)['messages'][-1]['content']
                    if correction == 'read' and not packet.get('contextResults'):
                        return reply({'action': 'compare_companies', 'contextRequests': [
                            {'kind': 'source', 'sourceRef': hidden[0], 'location': 'paragraph:1', 'purpose': '核实补充背景'}]})
                    if correction == 'read':
                        assert hidden[0] in packet['allowedEvidenceRefs']
                        assert '仅附旧项目背景' in packet['contextResults'][0]['value']['text']
                    # The old fixture downgrades its second comparison to pending;
                    # this correction scenario keeps the same valid recommendation.
                    value['companyAssessments'][0].update(role='primary', rank=1)
                    value['conclusion']['evidenceRefs'] = [hidden[0] if correction == 'read' else packet['allowedEvidenceRefs'][0]]
                    value['conclusion']['summary'] = '已读资料仅补充旧背景，当前推荐仍保留未核实标记。'
                else:
                    value['conclusion']['evidenceRefs'] = [hidden[0]]
                    value['conclusion']['summary'] = '未在本次请求展示的来源被用作共同事实依据。'
            body['choices'][0]['message']['content'] = json.dumps(value)
            return httpx.Response(response.status_code, json=body)
        return transport(respond)
    monkeypatch.setattr(httpx, 'MockTransport', wrap)
    db, task_id, task, calls, gateway = e2e._run(tmp_path, monkeypatch, v2=True, pending_ranking='legacy_wrong')
    with sqlite3.connect(db) as conn:
        comparisons = [json.loads(row[0])['conclusion'] for row in conn.execute(
            "SELECT result_json FROM k10_research_stage_results WHERE action='compare_companies'")]
    with client_for(db) as client:
        actual = client.get('/api/v1/k10/v2/reports/latest?window=evening')
    report = read_report(db_path=db)
    print('hidden-comparison-source', json.dumps({'task': task.status, 'observed': observed,
        'persistedComparisons': comparisons, 'apiStatus': actual.status_code,
        'publishedCardCount': len(report['eveningCards']) if report else 0}, ensure_ascii=False))
    assert observed
    if correction == 'reject':
        assert not comparisons or hidden[0] not in comparisons[0]['evidenceRefs']
        assert not report or not report['eveningCards']
    else:
        assert task.status == 'completed'
        assert len(report['eveningCards']) == 1
        assert actual.status_code == 200
        assert len(observed) == (3 if correction == 'read' else 2)


def resume_task(db, task_id, tmp_path):
    with sqlite3.connect(db) as conn:
        due = datetime.fromisoformat(conn.execute('SELECT not_before_at FROM k10_task_retry_schedules WHERE task_id=?', (task_id,)).fetchone()[0])
    return run_once(db_path=db, task_id=task_id, worker_id='review-resume', lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet'),
        clock=lambda: due + timedelta(seconds=1))


@pytest.mark.parametrize('pause', [False, True])
def test_admitted_empty_search_batch_is_finished_before_closure(tmp_path, monkeypatch, pause):
    from tests.test_b60_pool_filtering import edit_responses
    tick, tripped = [0.0], [False]
    monkeypatch.setattr(pipeline, 'time', SimpleNamespace(monotonic=lambda: tick[0]))
    def add_second_path(value):
        if value.get('action') == 'plan_queries':
            first = value['queryPaths'][0]
            value['queryPaths'].append({**first, 'pathId': 'path-2', 'query': '项目更正公告编号核实'})
    edit_responses(monkeypatch, add_second_path)
    original = _Investigation._tool
    def save_then_pause(self, bundle, **kwargs):
        original(self, bundle, **kwargs)
        if pause and not tripped[0] and kwargs.get('path'):
            tripped[0] = True
            tick[0] = 10000.0
    monkeypatch.setattr(_Investigation, '_tool', save_then_pause)
    db, task_id, task, calls, gateway = e2e._run(tmp_path, monkeypatch, v2=True, pending_ranking='legacy_wrong')
    if pause:
        assert task.status == 'queued'
        tick[0] = 0.0
        task = resume_task(db, task_id, tmp_path)
    assert task.status == 'completed'
    assert gateway.search_paths == ['path-1', 'path-2']
    assert calls.count('research:plan_queries') == 1
    assert calls.count('research:close_research') == 1
