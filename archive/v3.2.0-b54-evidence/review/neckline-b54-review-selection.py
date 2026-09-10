import tempfile, os, sqlite3
from pathlib import Path
from datetime import timedelta
root=Path(tempfile.mkdtemp(prefix='neckline-b54-review-selection-'))
os.environ['PYTHON_DOTENV_DISABLED']='1';os.environ['DB_PATH']=str(root/'unused.sqlite')
import pytest
from tests.test_v310_pipeline_e2e import _run,RUN_AT
from neckline.k10 import pipeline,store
from neckline.k10.worker import run_once
from neckline.k10.v2_store import read_report
from neckline.api.k10 import create_router
from fastapi import FastAPI
from fastapi.testclient import TestClient
with pytest.MonkeyPatch.context() as mp:
    db,task_id,task,calls,gateway=_run(root,mp,v2=True)
    card=read_report(db_path=db)['eveningCards'][0]
    app=FastAPI();app.include_router(create_router(lambda:db,lambda:None,lambda:root/'parquet',current_config_binding_provider=lambda:('b39',1,None),current_execution_config_binding_provider=lambda:('b39-execution',1,None)))
    with TestClient(app) as client:
        ready=client.get('/api/v1/k10/configuration').json()
        selected=client.post('/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection',json={'action':'keep','idempotencyKey':'review-keep','reason':None})
        print('SCOPES',[(s['scope'],s['state']) for s in ready['scopes']])
        print('KEEP',selected.status_code,selected.json())
        job=selected.json()['analysisJobId']
        with sqlite3.connect(db) as conn:print('BINDING',conn.execute('SELECT * FROM k10_task_execution_bindings WHERE task_id=?',(job,)).fetchall())
        old_count=len(calls)
        result=run_once(db_path=db,worker_id='review-analysis',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=root/'parquet'),clock=lambda:RUN_AT,task_id=job)
        print('WORKER',result.status,'MODEL_NEW_CALLS',calls[old_count:])
        print('JOB',client.get('/api/v1/k10/jobs/'+job).json())
print('ROOT',root)
