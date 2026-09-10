import json
import sqlite3
from datetime import timedelta
import httpx
import pytest
from neckline.k10 import pipeline, runtime, store
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from neckline.k10.v2_store import read_report
import tests.test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for

@pytest.mark.parametrize('status', [402, 429])
@pytest.mark.parametrize('failed_role', ['pro', 'con'])
def test_paid_analysis_error_contract(tmp_path, monkeypatch, status, failed_role):
    db, _, original_task, _, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    assert original_task.status == 'completed'
    card = read_report(db_path=db)['eveningCards'][0]
    with client_for(db) as client:
        response = client.post('/api/v1/k10/company-windows/' + card['companyWindowId'] + '/selection', json={'action':'keep','idempotencyKey':'independent-review-keep'})
        assert response.status_code == 200, response.text
        task_id = response.json()['analysisJobId']
    calls=[]
    def respond(request):
        calls.append(json.loads(request.content))
        if failed_role == 'con' and len(calls) == 1:
            content = {'fullText':'正方完整讨论','summary':{'commonFacts':['已有公开消息'], 'disagreements':[], 'unknowns':['公司未确认']}}
            return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':json.dumps(content)},'finish_reason':'stop'}], 'usage':{'prompt_tokens':3,'completion_tokens':3,'total_tokens':6}})
        return httpx.Response(status,headers={'Retry-After':'60'},json={'error':{'message':'deterministic provider failure'}})
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: e2e._HTTPX_CLIENT(**{**kwargs,'transport':transport}))
    provider = MeteredProvider(ledger_db=db,ledger_task='analysis',api_key='fixture',model='deepseek-v4-pro',name='fixture',api_url='https://api.deepseek.com/chat/completions',read_timeout=1,use_streaming=False)
    monkeypatch.setattr(runtime, 'resolve_deepseek_v4_pro', lambda **_:ProviderResolution('configured',provider,'fixture',None))
    task = run_once(db_path=db,worker_id='independent-review',lease_for=timedelta(minutes=5), handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'), clock=lambda:e2e.RUN_AT,task_id=task_id)
    with sqlite3.connect(db) as conn:
        retries=conn.execute('SELECT count(*) FROM k10_task_retry_schedules WHERE task_id=?',(task_id,)).fetchone()[0]
        external=conn.execute('SELECT error_code FROM k10_external_attempts WHERE task_id=?',(task_id,)).fetchall()
    execution = store.task_execution_input(task_id=task_id,db_path=db)
    with client_for(db) as client:
        chain=client.get('/api/v1/k10/company-windows/' + card['companyWindowId'] + '/analysis-chain').json()
    print(json.dumps({'providerStatus':status,'failedRole':failed_role,'taskStatus':task.status,'httpCalls':len(calls),'retries':retries,'attemptErrors':external,'taskError':execution.get('error'),'stage':execution.get('stage'),'analysisErrors':[a.get('error') for item in chain.get('items',[]) for a in item.get('analyses',[])]}, ensure_ascii=False))
    if status==429:
        assert task.status=='queued' and retries==1, '429 must retain the failed role and schedule Retry-After'
    else:
        assert '余额不足' in json.dumps(chain,ensure_ascii=False), '402 must reach the actual client-readable analysis failure'

@pytest.mark.parametrize('status', [402, 429])
def test_paid_morning_review_error_contract(tmp_path, monkeypatch, status):
    from neckline.k10 import morning_runtime
    from tests.test_b54_review_regressions import later_scan
    calls=[]
    def resolve(**kwargs):
        def respond(request):
            calls.append(json.loads(request.content))
            return httpx.Response(status,headers={'Retry-After':'60'},json={'error':{'message':'deterministic morning failure'}})
        transport=httpx.MockTransport(respond)
        monkeypatch.setattr(httpx,'Client',lambda **opts:e2e._HTTPX_CLIENT(**{**opts,'transport':transport}))
        provider=MeteredProvider(ledger_db=kwargs['db_path'],ledger_task='morning',api_key='fixture',model='deepseek-v4-pro',name='fixture',api_url='https://api.deepseek.com/chat/completions',read_timeout=1,use_streaming=False)
        return ProviderResolution('configured',provider,'fixture',None)
    monkeypatch.setattr(morning_runtime,'resolve_deepseek_v4_pro',resolve)
    db, old, parent, _=later_scan(tmp_path,monkeypatch,lambda payload,value:None)
    with sqlite3.connect(db) as conn:
        children=conn.execute("SELECT task_id,status,error_text FROM k10_tasks WHERE kind='morning_review'").fetchall()
        assert len(children)==1
        task_id=children[0][0]
        retries=conn.execute('SELECT count(*) FROM k10_task_retry_schedules WHERE task_id=?',(task_id,)).fetchone()[0]
        external=conn.execute('SELECT error_code FROM k10_external_attempts WHERE task_id=?',(task_id,)).fetchall()
    with client_for(db) as client:
        report=client.get('/api/v1/k10/v2/reports/latest?window=morning').json()
    print(json.dumps({'providerStatus':status,'scope':'morning_review','parentStatus':parent.status,'childStatus':children[0][1],'httpCalls':len(calls),'retries':retries,'attemptErrors':external,'error':children[0][2],'clientIncomplete':report['report']['incompleteReviews']},ensure_ascii=False))
    if status==429:
        assert children[0][1]=='queued' and retries==1, '429 morning child must schedule only its failed review'
    else:
        assert '余额不足' in json.dumps(report,ensure_ascii=False), '402 must be readable in current morning report'

def test_material_stage_continuation_keeps_its_related_window(tmp_path,monkeypatch):
    from datetime import date,datetime
    from neckline.k10.cli import enqueue_scan
    from neckline.k10.sources import SourceDocumentInput,SourceFetchResult
    from neckline.k10.windows import SHANGHAI
    from tests.test_b54_review_regressions import intercept
    db,_,first_task,_,_=e2e._run(tmp_path,monkeypatch,v2=True)
    assert first_task.status=='completed'
    initial=store.list_opportunities(company_code='300002.SZ',db_path=db)[0]
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO trade_cal VALUES ('SSE',?,1)",[('20260911',),('20260914',),('20260915',)])
        conn.executemany("INSERT INTO trade_cal VALUES ('SSE',?,0)",[('20260912',),('20260913',)])
    def run_next(day, related_id, classification):
        at=datetime(2026,9,day,22,0,tzinfo=SHANGHAI)
        published=at.replace(hour=19)
        text='公司公告：项目已经客户验证，签订首张批量交付订单，原先送样阶段结束。' if day==9 else '公司重申昨日公告的同一批量订单交付安排，金额和合同条件没有新增变化。'
        class News(e2e._News):
            def fetch_incremental(self,request):
                return SourceFetchResult(documents=(SourceDocumentInput('order-'+str(day),None,text,None,published,'exact',published+timedelta(minutes=1),'order-'+str(day),{'title':text}),),next_cursor='day-'+str(day),success_watermark=request.window.cutoff_at,pages_fetched=1,pages_expected=1,exhausted=True)
        monkeypatch.setattr(pipeline,'_now',lambda:at)
        monkeypatch.setattr(pipeline,'TuShareMajorNewsAdapter',News)
        e2e._http_transport(monkeypatch,v2=True)
        def change(payload,value):
            if 'events' in value:
                for event in value['events']:
                    event.update(stageKey='order_confirmed',headline='项目正式进入批量交付订单阶段',facts={'increment':text})
                    for claim in event.get('claims',[]):
                        claim.update(text=text,stageOrCondition='订单确认',subject='公司项目',action='签订订单')
            if isinstance(payload.get('output'),dict) and 'kind' in payload['output'] and payload['companyCode']=='300002.SZ':
                value.update(kind=classification,relatedOpportunityId=related_id,reason=text,newFacts=text,changedJudgment='首张批量订单取代原先试样的不确定判断',twoDayReason='订单阶段实质改变公司业务判断')
        intercept(monkeypatch,change)
        task_id=enqueue_scan(db_path=db,kind='evening',trading_day=date(2026,9,day),config_id='b39',config_revision=1,execution_config_id='b39-execution',execution_config_revision=1,now=at)
        task=run_once(db_path=db,worker_id='stage-'+str(day),lease_for=timedelta(minutes=5),handlers={'evening_scan':lambda ctx:pipeline.production_scan_handler(ctx,tushare_token='fixture-token',parquet_dir=tmp_path/'parquet',now=lambda:at)},clock=lambda:at,task_id=task_id)
        assert task.status=='completed',store.task_execution_input(task_id=task_id,db_path=db)
        return read_report(db_path=db)['eveningCards'][0]
    second=run_next(9,initial['opportunityId'],'material_stage')
    material=next(row for row in store.list_opportunities(company_code='300002.SZ',db_path=db) if row['opportunityId']!=initial['opportunityId'])
    assert second['companyWindowId']==material['companyWindowId']
    third=run_next(10,material['opportunityId'],'continuation')
    with client_for(db) as client:
        actual=client.get('/api/v1/k10/v2/reports/latest?window=evening')
        assert actual.status_code==200
        api_card=actual.json()['report']['eveningCards'][0]
        assert api_card['companyWindowId']==third['companyWindowId'] and api_card['canSelect'] is False
        blocked=client.post('/api/v1/k10/company-windows/'+api_card['companyWindowId']+'/selection',json={'action':'keep','idempotencyKey':'wrong-stage-window'})
        assert blocked.status_code==409
        print(json.dumps({'apiCardWindow':api_card['companyWindowId'],'apiCanSelect':api_card['canSelect'],'selectStatus':blocked.status_code,'selectMessage':blocked.json(),'actualMaterialState':store.get_opportunity(opportunity_id=material['opportunityId'],db_path=db)['state']},ensure_ascii=False))
    print(json.dumps({'initialWindow':initial['companyWindowId'],'initialDates':[initial['d1TradeDate'],initial['d2TradeDate']],'materialOpportunity':material['opportunityId'],'materialWindow':material['companyWindowId'],'materialDates':[material['d1TradeDate'],material['d2TradeDate']],'actualThirdWindow':third['companyWindowId'],'actualDates':[third['d1TradeDate'],third['d2TradeDate']],'thirdCanSelect':third['canSelect'],'thirdCatalysts':third['catalysts'],'opportunityCount':len(store.list_opportunities(company_code='300002.SZ',db_path=db))},ensure_ascii=False))
    assert third['companyWindowId']==material['companyWindowId'], 'Continuing material-stage card must use the related material window, not the original catalyst window'
