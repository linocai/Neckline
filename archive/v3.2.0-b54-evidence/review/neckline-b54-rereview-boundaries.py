import json, os, sqlite3, tempfile
from pathlib import Path
from datetime import datetime, timedelta
root=Path(tempfile.mkdtemp(prefix='neckline-b54-rereview-'))
os.environ['PYTHON_DOTENV_DISABLED']='1'
os.environ['DB_PATH']=str(root/'unused.sqlite')
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import later_scan, client_for
from tests.test_k10_api import _freeze_k10_clocks
from tests.test_k10_end_to_end import FakeProvider, _result
from neckline.api.k10 import create_router
from neckline.k10 import store,pipeline,morning_runtime
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from neckline.k10.v2_store import read_report
from neckline.k10.windows import SHANGHAI
from dataclasses import replace

with pytest.MonkeyPatch.context() as mp:
    work=root/'missing';work.mkdir()
    db,_,_,_,_=e2e._run(work,mp,v2=True)
    now=e2e.RUN_AT+timedelta(minutes=1)
    _freeze_k10_clocks(mp,now.isoformat())
    card=read_report(db_path=db)['eveningCards'][0]
    binding=[(None,None,'missing execution binding')]
    app=FastAPI();app.include_router(create_router(lambda:db,lambda:None,lambda:work/'parquet',current_config_binding_provider=lambda:('b39',1,None),current_execution_config_binding_provider=lambda:binding[0]))
    with TestClient(app) as client:
        route='/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection'
        first=client.post(route,json={'action':'keep','idempotencyKey':'missing-config'})
        job=first.json()['analysisJobId']
        run=run_once(db_path=db,worker_id='missing',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=work/'parquet'),clock=lambda:now,task_id=job)
        binding[0]=('b39-execution',1,None)
        scopes=client.get('/api/v1/k10/configuration').json()['scopes']
        second=client.post(route,json={'action':'keep','idempotencyKey':'restored-config'})
        retry=client.post('/api/v1/k10/jobs/'+job+'/retry',json={'expectedAttemptCount':run.attempt_count})
        rerun=run_once(db_path=db,worker_id='restored',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=work/'parquet'),clock=lambda:now,task_id=job)
        print('R1_MISSING',{'initialHTTP':first.status_code,'initialState':first.json()['state'],'initialWorker':run.status,'restoredScopes':[x['state'] for x in scopes],'restoredKeepHTTP':second.status_code,'sameJob':second.json()['analysisJobId']==job,'bindingAfterRestore':store.task_execution_profile(task_id=job,db_path=db),'retryHTTP':retry.status_code,'retryWorker':rerun.status})

with pytest.MonkeyPatch.context() as mp:
    work=root/'morning-failure';work.mkdir()
    provider=FakeProvider([replace(_result(''),ok=False)])
    mp.setattr(morning_runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'deepseek',None))
    db,old,task,calls=later_scan(work,mp,lambda payload,value:None)
    with client_for(db) as client:
        new=client.get('/api/v1/k10/v2/reports/latest?window=morning').json()
        legacy=client.get('/api/v1/k10/morning-reports/latest').json()
    with sqlite3.connect(db) as conn:
        children=conn.execute("SELECT status,error_text FROM k10_tasks WHERE kind='morning_review'").fetchall()
    print('R4_MORNING_FAILURE',{'worker':task.status,'children':children,'dailyStatus':new['report']['status'],'dailyReason':new['reason'],'lifecycleUpdates':new['report']['lifecycleUpdates'],'legacyStatus':legacy['status'],'legacyGaps':legacy['coverageGaps']})

with pytest.MonkeyPatch.context() as mp:
    work=root/'morning-interrupt';work.mkdir()
    now=[datetime(2026,9,9,9,10,tzinfo=SHANGHAI)]
    class ClockDateTime(datetime):
        @classmethod
        def now(cls,tz=None):
            return now[0].astimezone(tz) if tz else now[0].replace(tzinfo=None)
    mp.setattr(morning_runtime,'datetime',ClockDateTime)
    response=_result(json.dumps({'material':True,'reasonStatus':'needs_review','observationStatus':'needs_review','summary':'新增资料尚有不确定性','materialContraryEvidence':[]}))
    provider=FakeProvider([response,response])
    mp.setattr(morning_runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'deepseek',None))
    real=morning_runtime._report_checkpoint
    def interrupted(**kwargs):
        raise morning_runtime.MorningReportError('synthetic interruption after lifecycle commit')
    mp.setattr(morning_runtime,'_report_checkpoint',interrupted)
    db,old,task,calls=later_scan(work,mp,lambda payload,value:None)
    with sqlite3.connect(db) as conn:
        child=conn.execute("SELECT task_id,status,attempt_count FROM k10_tasks WHERE kind='morning_review'").fetchone()
        before=conn.execute("SELECT lifecycle_event_id,created_at FROM k10_opportunity_lifecycle_events WHERE kind='risk'").fetchall()
    mp.setattr(morning_runtime,'_report_checkpoint',real)
    now[0]+=timedelta(minutes=1)
    _freeze_k10_clocks(mp,now[0].isoformat())
    with client_for(db) as client:
        retry=client.post('/api/v1/k10/jobs/'+child[0]+'/retry',json={'expectedAttemptCount':child[2]})
    error=None
    try:
        run_once(db_path=db,worker_id='interrupt-retry',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=work/'parquet'),clock=lambda:now[0],task_id=child[0])
    except store.K10Conflict as exc:
        error=str(exc)
    print('R4_INTERRUPTION',{'firstChild':child,'durableRisk':before,'retryHTTP':retry.status_code,'retryError':error,'retryTaskState':store.get_task(task_id=child[0],db_path=db).status,'providerCalls':2-len(provider.results)})
print('ROOT',root)
