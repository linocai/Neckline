"""Read-only engineering audit reproductions; only temporary databases and mock transports.
Run from Neckline/Backend with the offline_guard PYTHONPATH and dotenv disabled.
"""
import json, sqlite3, socket, tempfile
from pathlib import Path
from datetime import date, datetime, timedelta
import httpx, pytest
from neckline.k10 import pipeline, store, morning_runtime
from neckline.k10.cli import enqueue_scan
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from neckline.k10.windows import SHANGHAI
from tests.test_b54_review_regressions import later_scan, client_for
import tests.test_v310_pipeline_e2e as e2e

assert socket.getaddrinfo.__module__ == 'sitecustomize'
assert __import__('os').environ.get('PYTHON_DOTENV_DISABLED') == '1'


def parent_terminal_recovery():
    with tempfile.TemporaryDirectory(prefix='nk-audit-parent402-') as tmp, pytest.MonkeyPatch.context() as mp:
        root = Path(tmp); calls = []
        def resolve(**kwargs):
            def respond(request):
                calls.append('402')
                return httpx.Response(402, json={'error': {'message': 'deterministic failure'}})
            transport = httpx.MockTransport(respond)
            mp.setattr(httpx, 'Client', lambda **opts: e2e._HTTPX_CLIENT(**{**opts, 'transport': transport}))
            p = MeteredProvider(ledger_db=kwargs['db_path'], ledger_task='morning', api_key='fixture',
                model='deepseek-v4-pro', name='fixture', api_url='https://api.deepseek.com/chat/completions',
                read_timeout=1, use_streaming=False)
            return ProviderResolution('configured', p, 'fixture', None)
        mp.setattr(morning_runtime, 'resolve_deepseek_v4_pro', resolve)
        actual = store.finish_task
        def crash(**kwargs):
            actual(**kwargs)
            if kwargs['task_id'].startswith('morning_review_'):
                raise SystemExit('parent interrupted after child terminal commit')
        mp.setattr(store, 'finish_task', crash)
        try:
            later_scan(root, mp, lambda payload, value: None)
        except SystemExit:
            pass
        db = root / 'b39-e2e.sqlite'
        def rows():
            with sqlite3.connect(db) as c:
                parent, until = c.execute("SELECT task_id,lease_until FROM k10_tasks WHERE kind='morning_scan'").fetchone()
                child = c.execute("SELECT status,attempt_count,error_text FROM k10_tasks WHERE kind='morning_review'").fetchone()
            return parent, until, child
        before = rows()[2]
        def reclaim(at):
            mp.setattr(pipeline, '_now', lambda: at)
            return run_once(db_path=db, worker_id='parent-reclaim', lease_for=timedelta(minutes=5),
                handlers={'morning_scan': lambda ctx: pipeline.production_scan_handler(ctx, tushare_token='fixture-token',
                    parquet_dir=root/'parquet', now=lambda: at)}, clock=lambda: at, task_id=rows()[0])
        for _ in range(2):
            at = datetime.fromisoformat(rows()[1]) + timedelta(seconds=1)
            try: reclaim(at)
            except SystemExit: pass
        after = rows()[2]
        attempts_before_deadline = len(calls)
        mp.setattr(store, 'finish_task', actual)
        late = reclaim(datetime(2026, 9, 9, 16, 0, tzinfo=SHANGHAI))
        print(json.dumps({'case':'parent_terminal_recovery','initialChild':before,'afterTwoParentReclaims':after,
            'httpCallsBeforeDeadline':attempts_before_deadline,'httpCallsAfterDeadline':len(calls),
            'lateParentStatus':late.status}, ensure_ascii=False))
        assert before[1] == 1 and after[1] == 3
        assert attempts_before_deadline == 3 and len(calls) == 3


def early_report_failure():
    with tempfile.TemporaryDirectory(prefix='nk-audit-early-failure-') as tmp, pytest.MonkeyPatch.context() as mp:
        root = Path(tmp); db, _, _, calls, _ = e2e._run(root, mp, v2=True)
        before_calls = len(calls)
        mp.setattr(pipeline, 'resolve_deepseek_v4_pro', lambda **_: ProviderResolution('not_configured', None, None, '模型密钥未配置'))
        for kind, hour in [('morning', 9), ('evening', 22)]:
            at = datetime(2026, 9, 9, hour, 10, tzinfo=SHANGHAI)
            mp.setattr(pipeline, '_now', lambda: at)
            tid = enqueue_scan(db_path=db, kind=kind, trading_day=date(2026,9,9), config_id='b39', config_revision=1,
                execution_config_id='b39-execution', execution_config_revision=1, now=at)
            task = run_once(db_path=db, worker_id='early-failure', lease_for=timedelta(minutes=5),
                handlers={kind+'_scan':lambda ctx:pipeline.production_scan_handler(ctx,tushare_token='fixture-token',
                    parquet_dir=root/'parquet',now=lambda:at)},clock=lambda:at,task_id=tid)
            with client_for(db) as c:
                response = c.get('/api/v1/k10/v2/reports/latest?window='+kind).json()
                config = c.get('/api/v1/k10/configuration').json()
            print(json.dumps({'case':'early_report_failure','kind':kind,'taskStatus':task.status,
                'scopes':[i['state'] for i in config['scopes']],'dailyState':response['state'],'dailyReason':response['reason'],
                'dailyCutoff':response['report']['cutoffAt'] if response.get('report') else None,
                'dailyStatus':response['report']['status'] if response.get('report') else None,
                'extraCalls':len(calls)-before_calls},ensure_ascii=False))
            assert task.status == 'not_configured'
            assert all(s['state']=='configured' for s in config['scopes'])
            assert response['state'] == ('empty' if kind == 'morning' else 'available')
            if kind == 'evening': assert response['reason'] is None and response['report']['cutoffAt'].startswith('2026-09-08')

if __name__ == '__main__':
    parent_terminal_recovery()
    early_report_failure()
