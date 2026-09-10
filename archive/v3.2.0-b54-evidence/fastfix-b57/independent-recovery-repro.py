import json, socket, sqlite3, tempfile, os
from pathlib import Path
from datetime import date, datetime, timedelta
import pytest
from neckline.k10 import pipeline, store
from neckline.k10.cli import enqueue_scan
from neckline.k10.providers import ProviderResolution
from neckline.k10.sources import SourceFetchResult
from neckline.k10.worker import run_once
from neckline.k10.windows import SHANGHAI
from tests.test_b54_review_regressions import client_for
from neckline.api import k10 as k10_api
import tests.test_v310_pipeline_e2e as e2e
assert socket.getaddrinfo.__module__ == 'sitecustomize'

for kind in os.environ.get('NK_REVIEW_KINDS', 'morning,evening').split(','):
    with tempfile.TemporaryDirectory(prefix='b57-independent-retry-') as tmp, pytest.MonkeyPatch.context() as mp:
        root = Path(tmp)
        db, _, _, calls, _ = e2e._run(root, mp, v2=True)
        with sqlite3.connect(db) as conn:
            conn.executemany("INSERT INTO trade_cal VALUES ('SSE',?,1)", [('20260911',),('20260914',),('20260915',)])
        restored_resolver = pipeline.resolve_deepseek_v4_pro
        at = datetime(2026,9,9,9 if kind == 'morning' else 22,10,tzinfo=SHANGHAI)
        mp.setattr(k10_api, '_now', lambda: at.isoformat())
        mp.setattr(pipeline, '_now', lambda: at)
        mp.setattr(pipeline, 'resolve_deepseek_v4_pro', lambda **_:ProviderResolution('not_configured',None,None,'fixture no provider'))
        tid = enqueue_scan(db_path=db,kind=kind,trading_day=date(2026,9,9),config_id='b39',config_revision=1,
            execution_config_id='b39-execution',execution_config_revision=1,now=at)
        def handle(ctx):
            try:
                return pipeline.production_scan_handler(ctx,tushare_token='fixture-token',parquet_dir=root/'parquet',now=lambda:at)
            except ValueError as exc:
                import traceback
                traceback.print_exc()
                raise
        def work():
            return run_once(db_path=db,worker_id='independent-recovery',lease_for=timedelta(minutes=5),
                handlers={kind+'_scan':handle},
                clock=lambda:at,task_id=tid)
        failed = work()
        assert failed.status == 'not_configured'
        with client_for(db) as client:
            before = client.get('/api/v1/k10/v2/reports/latest?window='+kind).json()
            response = client.post('/api/v1/k10/jobs/'+tid+'/retry',json={'expectedAttemptCount':failed.attempt_count})
            assert response.status_code == 200, response.text
        mp.setattr(pipeline,'resolve_deepseek_v4_pro',restored_resolver)
        class EmptyNews(e2e._News):
            def fetch_incremental(self, request):
                return SourceFetchResult(documents=(),next_cursor=None,success_watermark=request.window.cutoff_at,
                    pages_fetched=1,pages_expected=1,exhausted=True)
        mp.setattr(pipeline,'TuShareMajorNewsAdapter',EmptyNews)
        try:
            restored = work()
        except store.K10Conflict as exc:
            with sqlite3.connect(db) as conn:
                cutoff = conn.execute('SELECT cutoff_at FROM k10_scans WHERE scan_id=?', (before['report']['reportId'].removeprefix('report_'),)).fetchone()[0]
            task = store.get_task(task_id=tid,db_path=db)
            print(json.dumps({'case':kind,'retryApiStatus':response.status_code,'exception':str(exc),'storedCutoff':cutoff,'taskStatusAfterRetry':task.status},ensure_ascii=False))
            continue
        with client_for(db) as client:
            after = client.get('/api/v1/k10/v2/reports/latest?window='+kind).json()
        assert restored.status == 'completed', restored.status
        with sqlite3.connect(db) as conn:
            task_cutoff = conn.execute('SELECT input_cutoff_at FROM k10_tasks WHERE task_id=?',(tid,)).fetchone()[0]
        expected_scan_id = pipeline._scan_id(kind=kind,cutoff_at=datetime.fromisoformat(task_cutoff),identity=tid)
        assert after['report']['reportId'] == 'report_' + expected_scan_id
        assert after['report']['status'] == 'completed' and after['report']['availableAt'] is not None
        assert after['reason'] is None, after
        with sqlite3.connect(db) as conn:
            assert conn.execute('SELECT count(*) FROM k10_v2_report_runs').fetchone()[0] == 2
        print(json.dumps({'case':kind,'before':before['report']['status'],'after':after['report']['status'],
            'sameTaskScanId':True,'clearedFailureReason':True,'reportCount':2}))
