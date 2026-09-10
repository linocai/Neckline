"""B56 independent counterexamples, using real producers and offline transports."""
import json
import sqlite3
from datetime import date, datetime, timedelta

import httpx
import pytest

from neckline.k10 import morning_runtime, pipeline, store
from neckline.k10.cli import enqueue_scan
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from neckline.k10.windows import SHANGHAI
from tests.test_b54_review_regressions import client_for, later_scan
import tests.test_v310_pipeline_e2e as e2e


@pytest.mark.parametrize('status', [402, 429])
def test_parent_recovery_preserves_child_terminal_and_retry_budget(tmp_path, monkeypatch, status):
    calls = []
    def resolve(**kwargs):
        def respond(request):
            calls.append(request.content)
            return httpx.Response(status, headers={'Retry-After': '60'}, json={'error': {'message': 'offline failure'}})
        transport = httpx.MockTransport(respond)
        monkeypatch.setattr(httpx, 'Client', lambda **opts: e2e._HTTPX_CLIENT(**{**opts, 'transport': transport}))
        provider = MeteredProvider(ledger_db=kwargs['db_path'], ledger_task='morning', api_key='fixture',
            model='deepseek-v4-pro', name='fixture', api_url='https://api.deepseek.com/chat/completions',
            read_timeout=1, use_streaming=False)
        return ProviderResolution('configured', provider, 'fixture', None)
    monkeypatch.setattr(morning_runtime, 'resolve_deepseek_v4_pro', resolve)
    actual_finish = store.finish_task
    def interrupt(**kwargs):
        task = store.get_task(task_id=kwargs['task_id'], db_path=kwargs['db_path'])
        if task.kind == 'morning_scan':
            raise SystemExit('before parent terminal commit')
        actual_finish(**kwargs)
        if task.kind == 'morning_review' and kwargs['status'] == 'failed':
            raise SystemExit('after child terminal commit')
    monkeypatch.setattr(store, 'finish_task', interrupt)
    with pytest.raises(SystemExit):
        later_scan(tmp_path, monkeypatch, lambda payload, value: None)
    db = tmp_path / 'b39-e2e.sqlite'
    with sqlite3.connect(db) as conn:
        parent_id = conn.execute("SELECT task_id FROM k10_tasks WHERE kind='morning_scan'").fetchone()[0]
        child_id = conn.execute("SELECT task_id FROM k10_tasks WHERE kind='morning_review'").fetchone()[0]
        frozen_windows = conn.execute('SELECT * FROM k10_company_windows').fetchall()
    def reclaim():
        with sqlite3.connect(db) as conn:
            lease = conn.execute('SELECT lease_until FROM k10_tasks WHERE task_id=?', (parent_id,)).fetchone()[0]
        at = datetime.fromisoformat(lease) + timedelta(seconds=1)
        monkeypatch.setattr(pipeline, '_now', lambda: at)
        return run_once(db_path=db, worker_id='b57-parent', lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet'),
            clock=lambda: at, task_id=parent_id)
    for _ in range(3):
        with pytest.raises(SystemExit):
            reclaim()
    monkeypatch.setattr(store, 'finish_task', actual_finish)
    parent = reclaim()
    child = store.get_task(task_id=child_id, db_path=db)
    expected = 1 if status == 402 else 2
    assert child.status == 'failed' and child.attempt_count == expected
    assert len(calls) == expected, 'parent recovery must not authorize additional failed-child calls'
    assert parent.status == 'completed'
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT * FROM k10_company_windows').fetchall() == frozen_windows
        assert conn.execute("SELECT count(*) FROM k10_tasks WHERE kind='morning_review'").fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM k10_task_retry_schedules WHERE task_id=?', (child_id,)).fetchone()[0] == 0
    with client_for(db) as client:
        report = client.get('/api/v1/k10/v2/reports/latest?window=morning').json()
    assert report['report']['status'] == 'partial'
    assert report['report']['incompleteReviews'][0]['status'] == 'failed'
    assert ('余额不足' if status == 402 else '重试上限') in report['report']['incompleteReviews'][0]['reason']
    (tmp_path / f'b57_parent_{status}.json').write_text(json.dumps(report, ensure_ascii=False))


@pytest.mark.parametrize('kind', ['morning', 'evening'])
@pytest.mark.parametrize('failure', ['provider', 'token', 'calendar', 'metadata', 'execution', 'exception'])
def test_early_scan_failure_is_current_daily_report(tmp_path, monkeypatch, kind, failure, interrupt=False):
    db, _, _, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    with client_for(db) as client:
        old = client.get('/api/v1/k10/v2/reports/latest?window=evening').json()
    before_calls = len(calls)
    at = datetime(2026, 9, 9, 9 if kind == 'morning' else 22, 10, tzinfo=SHANGHAI)
    monkeypatch.setattr(pipeline, '_now', lambda: at)
    tid = enqueue_scan(db_path=db, kind=kind, trading_day=date(2026, 9, 9), config_id='b39', config_revision=1,
        execution_config_id='b39-execution', execution_config_revision=1, now=at)
    token = 'fixture-token'
    if failure == 'provider':
        monkeypatch.setattr(pipeline, 'resolve_deepseek_v4_pro', lambda **_: ProviderResolution('not_configured', None, None, '模型密钥未配置'))
    elif failure == 'token':
        token = None
    elif failure == 'calendar':
        monkeypatch.setattr(pipeline, 'official_is_trading_day', lambda *args, **kwargs: None)
    elif failure == 'metadata':
        def invalid(*args, **kwargs): raise pipeline.PipelineError('资料校验配置不完整')
        monkeypatch.setattr(pipeline, '_metadata_resolver_from_configuration', invalid)
    elif failure == 'execution':
        from neckline.k10 import worker
        # Pre-handler validation failure, after the real producer has bound its inputs.
        monkeypatch.setattr(worker, '_v3_execution_ready', lambda context: False)
    else:
        def crash(*args, **kwargs): raise RuntimeError('untrusted provider body must not be exposed')
        monkeypatch.setattr(pipeline, 'execute_scan', crash)
    def work():
        return run_once(db_path=db, worker_id='b57-early', lease_for=timedelta(minutes=5),
            handlers={kind+'_scan': lambda ctx: pipeline.production_scan_handler(ctx, tushare_token=token,
                parquet_dir=tmp_path/'parquet', now=lambda: at)}, clock=lambda: at, task_id=tid)
    if interrupt:
        from neckline.k10 import v2_store
        original = v2_store.record_scan_task_failure
        def crash_after_projection(*args, **kwargs):
            original(*args, **kwargs)
            raise SystemExit('between report projection and terminal transaction commit')
        monkeypatch.setattr(v2_store, 'record_scan_task_failure', crash_after_projection)
        with pytest.raises(SystemExit): work()
        assert store.get_task(task_id=tid, db_path=db).status == 'running'
        with sqlite3.connect(db) as conn:
            assert conn.execute('SELECT count(*) FROM k10_v2_report_runs').fetchone()[0] == 1
            lease = conn.execute('SELECT lease_until FROM k10_tasks WHERE task_id=?', (tid,)).fetchone()[0]
        at = datetime.fromisoformat(lease) + timedelta(seconds=1)
        monkeypatch.setattr(v2_store, 'record_scan_task_failure', original)
    task = work()
    expected = 'failed' if failure == 'exception' else 'not_configured'
    assert task.status == expected and len(calls) == before_calls
    with client_for(db) as client:
        current = client.get('/api/v1/k10/v2/reports/latest?window='+kind).json()
        historical = client.get('/api/v1/k10/v2/reports/'+old['report']['reportId']).json()
        history = client.get('/api/v1/k10/v2/reports').json()
    assert current['state'] == 'available'
    assert current['report']['status'] == expected
    assert current['report']['cutoffAt'].startswith('2026-09-09')
    assert current['report']['availableAt'] is None
    assert all(not current['report'][key] for key in ('eveningCards', 'updatedCards', 'addedCards'))
    assert current['reason'] and '今天没跑成' in current['reason']['message']
    if expected == 'not_configured': assert '参数未配置' in current['reason']['message']
    assert 'untrusted provider body' not in json.dumps(current)
    assert historical == old
    assert len(history['items']) == 2
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM k10_v2_report_runs').fetchone()[0] == 2
    (tmp_path / f'b57_early_{kind}_{failure}.json').write_text(json.dumps(current, ensure_ascii=False))


@pytest.mark.parametrize('kind', ['morning', 'evening'])
def test_failure_report_and_terminal_task_commit_atomically(tmp_path, monkeypatch, kind):
    test_early_scan_failure_is_current_daily_report(tmp_path, monkeypatch, kind, 'provider', interrupt=True)



def test_actual_paginated_morning_api_preserves_selection_and_window(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tests.k10_v320_fixture import build_fixture, create_app
    from tests.test_k10_api import _freeze_k10_clocks
    db = tmp_path / 'pagination.sqlite'
    build_fixture(db, morning_additions=31)
    _freeze_k10_clocks(monkeypatch, '2026-09-09T09:20:00+08:00')
    with TestClient(create_app(db)) as client:
        first = client.get('/api/v1/k10/v2/reports/latest?window=morning').json()
        report_id = first['report']['reportId']
        cursor = first['report']['nextCursor']
        path = '/api/v1/k10/v2/reports/' + report_id
        next_page = client.get(path, params={'cursor': cursor}).json()
        assert len(first['report']['addedCards']) == 30 and len(next_page['report']['addedCards']) == 1
        tail = next_page['report']['addedCards'][0]
        skipped = client.post('/api/v1/k10/company-windows/'+tail['companyWindowId']+'/selection',
            json={'action': 'skip', 'idempotencyKey': 'b57-tail-skip'})
        assert skipped.status_code == 200, skipped.text
        refreshed = client.get(path, params={'cursor': cursor}).json()
        updated = refreshed['report']['addedCards'][0]
        assert updated['cardId'] == tail['cardId'] and updated['currentSelectionState'] == 'skipped'
        assert (updated['companyWindowId'], updated['d1TradeDate'], updated['d2TradeDate']) == (tail['companyWindowId'], tail['d1TradeDate'], tail['d2TradeDate'])
        for name, response in [('first', first), ('next', next_page), ('selected', refreshed), ('action', skipped.json())]:
            (tmp_path/f'b57_pages_{name}.json').write_text(json.dumps(response, ensure_ascii=False))


@pytest.mark.parametrize('kind', ['morning', 'evening'])
def test_early_failure_then_real_api_retry_reuses_same_scan(tmp_path, monkeypatch, kind):
    from neckline.k10.sources import SourceFetchResult
    import neckline.api.k10 as api
    db, _, _, _, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO trade_cal VALUES ('SSE',?,1)", [('20260911',), ('20260914',), ('20260915',)])
    original = pipeline.resolve_deepseek_v4_pro
    at = datetime(2026, 9, 9, 9 if kind == 'morning' else 22, 10, tzinfo=SHANGHAI)
    monkeypatch.setattr(api, '_now', lambda: at.isoformat())
    monkeypatch.setattr(pipeline, '_now', lambda: at)
    monkeypatch.setattr(pipeline, 'resolve_deepseek_v4_pro', lambda **_: ProviderResolution('not_configured', None, None, '模型密钥未配置'))
    tid = enqueue_scan(db_path=db, kind=kind, trading_day=date(2026, 9, 9), config_id='b39', config_revision=1,
        execution_config_id='b39-execution', execution_config_revision=1, now=at)
    def work():
        return run_once(db_path=db, worker_id='b57-explicit-retry', lease_for=timedelta(minutes=5),
            handlers={kind+'_scan': lambda ctx: pipeline.production_scan_handler(ctx, tushare_token='fixture-token',
                parquet_dir=tmp_path/'parquet', now=lambda: at)}, clock=lambda: at, task_id=tid)
    failed = work()
    assert failed.status == 'not_configured'
    with client_for(db) as client:
        before = client.get('/api/v1/k10/v2/reports/latest?window='+kind).json()
        retried = client.post('/api/v1/k10/jobs/'+tid+'/retry', json={'expectedAttemptCount': failed.attempt_count})
        assert retried.status_code == 200, retried.text
    with sqlite3.connect(db) as conn:
        failure_history = conn.execute('SELECT * FROM k10_morning_reports ORDER BY revision').fetchall()
    monkeypatch.setattr(pipeline, 'resolve_deepseek_v4_pro', original)
    class EmptyNews(e2e._News):
        def fetch_incremental(self, request):
            return SourceFetchResult(documents=(), next_cursor=None, success_watermark=request.window.cutoff_at,
                pages_fetched=1, pages_expected=1, exhausted=True)
    monkeypatch.setattr(pipeline, 'TuShareMajorNewsAdapter', EmptyNews)
    recovered = work()
    with sqlite3.connect(db) as conn:
        terminal = conn.execute('SELECT status,stage,error_text FROM k10_tasks WHERE task_id=?', (tid,)).fetchone()
    assert recovered.status == 'completed', terminal
    with client_for(db) as client:
        after = client.get('/api/v1/k10/v2/reports/latest?window='+kind).json()
    assert before['report']['reportId'] == after['report']['reportId']
    assert after['report']['status'] == 'completed' and after['report']['availableAt'] is not None
    assert after['reason'] is None
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM k10_v2_report_runs').fetchone()[0] == 2
        if kind == 'morning':
            history = conn.execute('SELECT * FROM k10_morning_reports ORDER BY revision').fetchall()
            assert len(failure_history) == 1 and len(history) == 2
            assert history[0] == failure_history[0], 'same-second recovery must append, not rewrite failure history'
    (tmp_path/f'b57_early_{kind}_recovered.json').write_text(json.dumps(after, ensure_ascii=False))


@pytest.mark.parametrize('equivalent', ['2026-09-09T13:00:00+00:00', '2026-09-09T13:00:00Z'])
def test_scan_cutoff_identity_uses_instant_but_preserves_other_inputs(tmp_path, equivalent):
    from neckline.k10.schema import initialize_schema
    db = tmp_path/'identity.sqlite'
    initialize_schema(db)
    args = dict(scan_id='same-scan', window_kind='evening', cutoff_at='2026-09-09T21:00:00+08:00',
        config_id=None, config_revision=None, status='running', coverage={}, created_at='2026-09-09T21:01:00+08:00',
        completed_at=None, db_path=db)
    store.create_scan(**args)
    original = store.get_scan(scan_id='same-scan', db_path=db)
    store.create_scan(**{**args, 'cutoff_at': equivalent})
    assert store.get_scan(scan_id='same-scan', db_path=db) == original
    for change in ({'cutoff_at': '2026-09-09T13:00:01Z'}, {'cutoff_at': '2026-09-09T13:00:00'},
                   {'window_kind': 'morning'}, {'config_id': 'different'}, {'config_revision': 2}):
        with pytest.raises(store.K10Conflict):
            store.create_scan(**{**args, **change})
        assert store.get_scan(scan_id='same-scan', db_path=db) == original
