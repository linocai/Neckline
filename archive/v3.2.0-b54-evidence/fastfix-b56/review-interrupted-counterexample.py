"""B56 independent recovery gap after a failed role is durable but before task outcome."""
import json, tempfile, sqlite3
from pathlib import Path
from datetime import timedelta
import httpx, pytest
import tests.conftest
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for
from tests.test_k10_api import _freeze_k10_clocks
from neckline.k10 import runtime, pipeline, store
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from neckline.k10.v2_store import read_report

for status in (402, 429):
    root = Path(tempfile.mkdtemp(prefix='neckline-b56-interrupted-' + str(status) + '-'))
    with pytest.MonkeyPatch.context() as mp:
        db, *_ = e2e._run(root, mp, v2=True)
        _freeze_k10_clocks(mp, e2e.RUN_AT.isoformat())
        card = read_report(db_path=db)['eveningCards'][0]
        with client_for(db) as client:
            task_id = client.post('/api/v1/k10/company-windows/' + card['companyWindowId'] + '/selection',
                                  json={'action': 'keep', 'idempotencyKey': 'b56-interrupt-provider-failure'}).json()['analysisJobId']
        calls = []
        def respond(request):
            calls.append(json.loads(request.content))
            return httpx.Response(status, headers={'Retry-After': '900'}, json={'error': {'message': 'offline'}})
        transport = httpx.MockTransport(respond)
        mp.setattr(httpx, 'Client', lambda **kwargs: e2e._HTTPX_CLIENT(**{**kwargs, 'transport': transport}))
        provider = MeteredProvider(ledger_db=db, ledger_task='analysis', api_key='fixture', model='deepseek-v4-pro',
                                   name='fixture', api_url='https://api.deepseek.com/chat/completions', read_timeout=1, use_streaming=False)
        mp.setattr(runtime, 'resolve_deepseek_v4_pro', lambda **_: ProviderResolution('configured', provider, 'fixture', None))
        record = runtime.record_analysis_artifact
        def interrupted(**kwargs):
            record(**kwargs)
            raise SystemExit('simulated worker death after durable failure artifact')
        mp.setattr(runtime, 'record_analysis_artifact', interrupted)
        def work(at):
            return run_once(db_path=db, worker_id='b56-recovery', lease_for=timedelta(minutes=5),
                            handlers=pipeline.production_handlers(tushare_token='fixture', parquet_dir=root/'parquet'),
                            clock=lambda: at, task_id=task_id)
        try:
            work(e2e.RUN_AT)
        except SystemExit:
            pass
        first = store.get_task(task_id=task_id, db_path=db)
        mp.setattr(runtime, 'record_analysis_artifact', record)
        recovered = work(e2e.RUN_AT + timedelta(minutes=5, seconds=1))
        with sqlite3.connect(db) as conn:
            errors = conn.execute('SELECT error_code FROM k10_external_attempts WHERE task_id=? ORDER BY rowid', (task_id,)).fetchall()
        print(json.dumps({'providerStatus': status, 'db': str(db), 'firstStatus': first.status, 'recoveredStatus': recovered.status,
                          'calls': len(calls), 'retryAfterSeconds': 900, 'actualReplayAfterSeconds': 301, 'errors': errors}))
