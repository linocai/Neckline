"""Explicit quota recovery preserves paid work and grants one bounded retry."""
import json
import sqlite3
from datetime import timedelta

import httpx
import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.verification import TavilyEvidenceGateway
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
from neckline.search.tavily import TavilySearchClient
from tests import test_v310_pipeline_e2e as e2e
from tests.test_k10_verification import _event


@pytest.mark.parametrize('quota_restored', [True, False])
def test_explicit_recovery_after_legacy_quota_retries_keeps_original_task(tmp_path, monkeypatch, quota_restored):
    calls = []
    def respond(request):
        calls.append(json.loads(request.content)['query'])
        if len(calls) <= 2 or not quota_restored:
            return httpx.Response(432, json={})
        return httpx.Response(200, json={'results': [], 'usage': {'credits': 1}})
    transport = httpx.MockTransport(respond)
    class Client(TavilySearchClient):
        def search(self, query):
            with monkeypatch.context() as local:
                local.setattr(httpx, 'Client', lambda **kw: e2e._HTTPX_CLIENT(**{**kw, 'transport': transport}))
                return super().search(query)
    def gateway():
        db = tmp_path/'b39-e2e.sqlite'
        with sqlite3.connect(db) as conn:
            task_id = conn.execute('SELECT task_id FROM k10_tasks').fetchone()[0]
        return TavilyEvidenceGateway(db_path=db, task_id=task_id, client=Client('fixture'),
            clock=lambda: pipeline._now(), network_max_attempts=2)
    monkeypatch.setattr(e2e, '_Gateway', gateway)
    current_mapping = TavilyEvidenceGateway._response_code
    # Reproduce B67's precise mistaken mapping, using real CLI/worker requests.
    monkeypatch.setattr(TavilyEvidenceGateway, '_response_code', staticmethod(
        lambda response: 'tavily_response_unavailable' if response.reason == 'tavily_http_432' else current_mapping(response)))
    db, task_id, task, model_calls, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    assert task.status == 'failed' and len(calls) == 1
    scan = store.task_execution_input(task_id=task_id, db_path=db)['checkpoint']['scanId']
    digest = frozen_scan_input_sha256(scan_id=scan, db_path=db)
    def recover():
        assert recover_scan(db_path=db, scan_id=scan, execution_config_id='b39-execution', execution_config_revision=1,
            confirmed_input_sha256=digest, now=e2e.RUN_AT) == task_id
    def work(at=e2e.RUN_AT):
        return run_once(db_path=db, task_id=task_id, worker_id='b68-recovery', lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet'), clock=lambda:at)
    recover(); assert work().status == 'failed' and len(calls) == 2
    with sqlite3.connect(db) as conn:
        paid = conn.execute("SELECT * FROM k10_external_attempts WHERE state='succeeded'").fetchall()
        failed = conn.execute("SELECT attempt_id FROM k10_external_attempts WHERE stage='search'").fetchall()
    monkeypatch.setattr(TavilyEvidenceGateway, '_response_code', staticmethod(current_mapping))
    recover()
    grant = store.task_execution_input(task_id=task_id, db_path=db)['checkpoint']['verificationRecoveryAttempts']
    original_fetch = TavilyEvidenceGateway.fetch
    paused = []
    def pause_before_retry(self, **kwargs):
        if not paused:
            paused.append(True)
            raise pipeline.DiscoverySliceYield()
        return original_fetch(self, **kwargs)
    monkeypatch.setattr(TavilyEvidenceGateway, 'fetch', pause_before_retry)
    assert work().status == 'queued' and len(calls) == 2
    assert store.task_execution_input(task_id=task_id, db_path=db)['checkpoint']['verificationRecoveryAttempts'] == grant
    result = work(e2e.RUN_AT + timedelta(seconds=2))
    assert result.status == ('completed' if quota_restored else 'failed')
    assert len(calls) == (4 if quota_restored else 3)
    assert len(set(calls[:3])) == 1
    if quota_restored:
        assert calls[3] != calls[0]  # The next genuinely new query still runs.
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT * FROM k10_external_attempts WHERE state='succeeded'").fetchall()
        assert all(row in rows for row in paid)
        assert conn.execute('SELECT COUNT(*) FROM k10_tasks').fetchone()[0] == 1
        assert conn.execute("SELECT MAX(network_attempt_count) FROM k10_execution_item_checkpoints WHERE stage='tavily_evidence'").fetchone()[0] == 3
        assert all(row in conn.execute("SELECT attempt_id FROM k10_external_attempts WHERE stage='search'").fetchall() for row in failed)
    assert frozen_scan_input_sha256(scan_id=scan, db_path=db) == digest
    assert model_calls.count('titleBatch') == 1 and model_calls.count('research:plan_gaps') == 1
    report = read_report(db_path=db)
    assert bool(report['eveningCards']) == quota_restored
    if not quota_restored:
        # A plain restart cannot renew the consumed grant or send another call.
        assert work() is None
        assert gateway().fetch(event=_event(),
            retrieved_at=e2e.RUN_AT, cutoff_at=e2e.RUN_AT).coverage['reason'] == 'insufficient_balance'
        assert len(calls) == 3
