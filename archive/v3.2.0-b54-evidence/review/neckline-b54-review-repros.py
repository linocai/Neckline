import json, sqlite3, tempfile
from pathlib import Path
from datetime import date, datetime, timedelta
from dataclasses import replace
import os
root=Path(tempfile.mkdtemp(prefix='neckline-b54-review-'))
os.environ['PYTHON_DOTENV_DISABLED']='1'
os.environ['DB_PATH']=str(root/'unbound.sqlite')
import httpx, pytest
from tests.test_v310_pipeline_e2e import _run, _http_transport, _HTTPX_CLIENT
from neckline.k10 import pipeline, store
from neckline.k10.cli import enqueue_scan
from neckline.k10.worker import run_once
from neckline.k10.v2_store import read_report
from neckline.k10.windows import SHANGHAI
from neckline.api.k10 import create_router
from fastapi import FastAPI
from fastapi.testclient import TestClient

mp=pytest.MonkeyPatch()
db,old_task_id,old_task,old_calls,gateway=_run(root,mp,v2=True)
assert old_task.status=='completed'
base=read_report(db_path=db)
print('ROOT',root)
print('BASE',[(x['companyCode'],x['summary']) for x in base['eveningCards']])
with sqlite3.connect(db) as conn:
    conn.executemany("INSERT INTO trade_cal VALUES ('SSE',?,1)",[('20260911',),('20260914',),('20260915',)])

later=datetime(2026,9,9,22,0,tzinfo=SHANGHAI)
mp.setattr(pipeline,'_now',lambda:later)
calls=_http_transport(mp,v2=True)
mock=httpx.Client()._transport

def excluded_response(request):
    response=mock.handle_request(request)
    body=response.json()
    wire=json.loads(request.content)
    message=wire['messages'][-1]['content']
    payload=json.loads(message.split('<untrusted-k10-evidence>\n',1)[1].split('\n</untrusted-k10-evidence>',1)[0])
    if payload.get('action')=='compare_companies':
        result=json.loads(body['choices'][0]['message']['content'])
        for row in result['companyAssessments']:
            if row['companyCode']=='300002.SZ':
                row.update(role='excluded',rank=None,summary='本轮排除：旧催化不再值得推荐')
        body['choices'][0]['message']['content']=json.dumps(result)
    return httpx.Response(200,json=body)
transport=httpx.MockTransport(excluded_response)
mp.setattr(httpx,'Client',lambda **kwargs:_HTTPX_CLIENT(**{**kwargs,'transport':transport}))
task_id=enqueue_scan(db_path=db,kind='evening',trading_day=date(2026,9,9),config_id='b39',config_revision=1,
    execution_config_id='b39-execution',execution_config_revision=1,now=later)
result=run_once(db_path=db,worker_id='review-excluded',lease_for=timedelta(minutes=5),
    handlers={'evening_scan':lambda context:pipeline.production_scan_handler(context,tushare_token='fixture-token',parquet_dir=root/'parquet',now=lambda:later)},
    clock=lambda:later,task_id=task_id)
print('EXCLUDED_TASK',result.status)
print('EXCLUDED_CHECKPOINT',store.task_execution_input(task_id=task_id,db_path=db)['checkpoint'].get('safeErrorCode'))
report=read_report(db_path=db)
print('EXCLUDED_REPORT',[(x['companyCode'],x['summary'],x['catalysts'][0]['classification']) for x in report['eveningCards']])
print('EXCLUDED_CALLS',calls)
app=FastAPI(); app.include_router(create_router(lambda:db,lambda:None,lambda:root/'parquet',current_config_binding_provider=lambda:('b39',1,None),current_execution_config_binding_provider=lambda:('b39-execution',1,None)))
with TestClient(app) as client:
    response=client.get('/api/v1/k10/v2/reports/latest?window=evening')
    print('EXCLUDED_API',response.status_code,[(x['companyCode'],x['summary'],x['currentSelectionState']) for x in response.json()['report']['eveningCards']])
mp.undo()
