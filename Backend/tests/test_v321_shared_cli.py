"""Shared fact acquisition through the real producer, worker and resume boundary."""
import copy
from datetime import datetime, timedelta
import json
import sqlite3
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from neckline.k10 import pipeline, store
from neckline.k10.verification import TavilyEvidenceGateway
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
from tests import k10_v306_fixture, test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for
from tests.test_v310_tavily import _SearchExtract


def _direct_query_round(payload):
    """Return one necessary shared-source query under the B78 direct contract."""
    packet = payload["evidencePacket"]
    ref = packet["allowedEvidenceRefs"][0]
    claim_id = packet["claims"][0]["claimId"]
    code = packet["companyScope"]["candidateCompanyCodes"][0]
    return {
        "action": "research_round",
        "questions": [{
            "questionId": "shared-question", "claimIds": [claim_id], "companyCodes": [code],
            "question": "送样是否获公司确认", "knownEvidence": [ref], "missingEvidence": ["公司确认"],
            "supportCondition": "公司确认", "refuteCondition": "公司否认", "decisionImpact": "影响比较",
            "state": "open", "resumeCondition": "出现公司公告",
        }],
        "queryPaths": [{
            "pathId": "shared-path", "questionId": "shared-question", "query": "项目 送样 公告",
            "intent": "确认送样", "targetSource": "公司公告", "newPathReason": "首批必要来源",
            "expectedInformationGain": "确认主体", "expectedJudgmentChange": "改变比较",
            "purposeKind": "event_fact", "targetRefs": [{"kind": "claim", "claimId": claim_id}],
            "state": "planned", "resultSummary": None,
        }],
        "conclusion": {
            "researchStatus": "continue_research", "companyMappings": [], "materialGaps": ["公司确认"],
            "stopReason": "需要必要公开资料", "resumeCondition": "取得公司公告",
        },
        "companyAssessments": [],
    }


@pytest.mark.parametrize('concurrency,interrupt', [(1, True), (2, False)])
def test_real_cli_related_events_share_search_and_resume_without_rebilling(tmp_path, monkeypatch, concurrency, interrupt):
    db = tmp_path/'b39-e2e.sqlite'
    original_profile = k10_v306_fixture.execution_payload
    def profile(**kwargs):
        policy, payload = original_profile(**kwargs)
        payload['discovery']['deepReadConcurrency'] = concurrency
        return policy, payload
    monkeypatch.setattr(k10_v306_fixture, 'execution_payload', profile)
    tick, tripped = [0.0], [False]
    monkeypatch.setattr(pipeline, 'time', SimpleNamespace(monotonic=lambda: tick[0]))
    started, release = Event(), Event()
    bundles, searches = [], []
    class Client(_SearchExtract):
        def search(self, query):
            if concurrency == 2:
                started.set()
                assert release.wait(10), 'The sibling must plan while acquisition is in flight'
            return super().search(query)
    client = Client()
    class Gateway:
        def __init__(self):
            with sqlite3.connect(db) as conn:
                ids = conn.execute('SELECT task_id FROM k10_tasks').fetchall()
            assert len(ids) == 1  # The real enqueue owns the binding and checkpoint.
            self.gateway = TavilyEvidenceGateway(db_path=db, client=client, clock=lambda:e2e.RUN_AT,
                task_id=ids[0][0], network_max_attempts=2)
        def fetch(self, **kwargs):
            searches.append(kwargs['event'].canonical_key)
            value = self.gateway.fetch(**kwargs)
            bundles.append(value)
            if concurrency == 2 and not value.documents:
                release.set()
            if interrupt and not tripped[0] and value.documents:
                tripped[0] = True
                tick[0] = 10000.0  # Persisted paid reply, before its model assessment.
            return value
        def fetch_fulltext(self, **kwargs):
            return self.gateway.fetch_fulltext(**kwargs)
    monkeypatch.setattr(e2e, '_Gateway', Gateway)
    transport = httpx.MockTransport
    rounds = []
    initial_events = set()
    def wrap(handler):
        def respond(request):
            payload = json.loads(json.loads(request.content)['messages'][-1]['content'].split(
                '<untrusted-k10-evidence>\n', 1)[1].split('\n</untrusted-k10-evidence>', 1)[0])
            if payload.get('action') == 'research_round':
                event_key = payload['evidencePacket']['event']['canonicalKey']
                rounds.append((event_key, payload))
                if event_key not in initial_events:
                    initial_events.add(event_key)
                    if concurrency == 2 and len(initial_events) == 2:
                        assert started.wait(10)
                        release.set()  # The second event reaches its own direct round before the shared fetch resolves.
                    return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(
                        _direct_query_round(payload))}, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 3, 'completion_tokens': 3, 'total_tokens': 6}})
            response = handler(request)
            body = response.json()
            value = json.loads(body['choices'][0]['message']['content'])
            if 'events' in value:
                value['events'].append({**copy.deepcopy(value['events'][0]),
                    'canonicalKey':'project-supplier-capacity', 'headline':'同份消息的另一产能影响'})
            body['choices'][0]['message']['content'] = json.dumps(value)
            return httpx.Response(response.status_code, json=body)
        return transport(respond)
    monkeypatch.setattr(httpx, 'MockTransport', wrap)
    _, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True, pending_ranking='legacy_wrong')
    assert task.status in {'queued','completed'}, task
    if task.status == 'queued':
        tick[0] = 0.0
        release.set()
        with sqlite3.connect(db) as conn:
            due = datetime.fromisoformat(conn.execute('SELECT not_before_at FROM k10_task_retry_schedules WHERE task_id=?',
                (task_id,)).fetchone()[0])
        task = run_once(db_path=db, task_id=task_id, worker_id='v321-shared-resume', lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet'),
            clock=lambda:due+timedelta(seconds=1))
    assert task.status == 'completed', task
    assert client.calls == 1
    assert set(searches) == {'project-delivery','project-supplier-capacity'}
    assert calls.count('understand') == 1 and calls.count('titleBatch') == 1
    assert len(initial_events) == 2
    assert [key for key, _payload in rounds].count('project-delivery') >= 1
    assert [key for key, _payload in rounds].count('project-supplier-capacity') >= 1
    # The search was shared, but each event gets a fresh direct result that
    # can see only its own packet plus the same durable source fact.
    assert calls.count('research:research_round') == 2
    report = read_report(db_path=db)
    assert len(report['eveningCards']) == 1 and not report['incompleteReviews']
    with client_for(db) as api:
        actual = api.get('/api/v1/k10/v2/reports/latest?window=evening')
    assert actual.status_code == 200 and len(actual.json()['report']['eveningCards']) == 1
    if interrupt:
        assert tripped[0]
    else:
        assert release.is_set()  # Event research actually overlapped under the frozen concurrency=2 profile.
