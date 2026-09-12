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
    payloads = []
    def wrap(handler):
        def respond(request):
            response = handler(request)
            body = response.json()
            payload = json.loads(json.loads(request.content)['messages'][-1]['content'].split(
                '<untrusted-k10-evidence>\n', 1)[1].split('\n</untrusted-k10-evidence>', 1)[0])
            value = json.loads(body['choices'][0]['message']['content'])
            if 'events' in value:
                value['events'].append({**copy.deepcopy(value['events'][0]),
                    'canonicalKey':'project-supplier-capacity', 'headline':'同份消息的另一产能影响'})
            if value.get('action') == 'plan_queries':
                value['queryPaths'][0].update(pathId='shared-path', query='项目 送样 公告',
                    intent='确认送样', targetSource='公司公告')
                if concurrency == 2 and any(item.get('action') == 'plan_queries' for item in payloads):
                    assert started.wait(10)
                    release.set()  # Production serializes the gateway, while event research overlaps.
            if value.get('action') == 'close_research':
                value['conclusion']['researchStatus'] = 'pending_verification'
            if payload.get('action'):
                payloads.append(payload)
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
    assert calls.count('research:compare_companies') == 2  # Facts shared, judgments remain per-event.
    report = read_report(db_path=db)
    assert len(report['eveningCards']) == 1 and not report['incompleteReviews']
    with client_for(db) as api:
        actual = api.get('/api/v1/k10/v2/reports/latest?window=evening')
    assert actual.status_code == 200 and len(actual.json()['report']['eveningCards']) == 1
    if interrupt:
        assert tripped[0]
    else:
        assert release.is_set()  # Event research actually overlapped under the frozen concurrency=2 profile.
