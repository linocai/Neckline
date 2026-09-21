import json
import sqlite3
from datetime import datetime, timedelta
import httpx
import pytest
from neckline.k10 import morning_runtime, pipeline, runtime, store
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from neckline.k10.v2_store import read_report
import tests.test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for, later_scan
from tests.test_k10_api import _freeze_k10_clocks

@pytest.mark.parametrize('status,retry_succeeds', [(402,False), (429,True), (429,False)])
@pytest.mark.parametrize('failed_role', ['pro', 'con'])
def test_paid_analysis_error_contract(tmp_path, monkeypatch, status, retry_succeeds, failed_role, late_retry=False, interrupt_after_failure=False):
    db, _, original_task, _, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    assert original_task.status == 'completed'
    _freeze_k10_clocks(monkeypatch, e2e.RUN_AT.isoformat())
    card = read_report(db_path=db)['eveningCards'][0]
    with client_for(db) as client:
        response = client.post('/api/v1/k10/company-windows/' + card['companyWindowId'] + '/selection', json={'action':'keep','idempotencyKey':'independent-review-keep'})
        assert response.status_code == 200, response.text
        task_id = response.json()['analysisJobId']
    calls=[]
    recover=[False]
    def respond(request):
        calls.append(json.loads(request.content))
        if recover[0] or (failed_role == 'con' and len(calls) == 1):
            content = {'fullText':'正方完整讨论','summary':{'commonFacts':['已有公开消息'], 'disagreements':[], 'unknowns':['公司未确认']}}
            return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':json.dumps(content)},'finish_reason':'stop'}], 'usage':{'prompt_tokens':3,'completion_tokens':3,'total_tokens':6}})
        return httpx.Response(status,headers={'Retry-After':'900' if interrupt_after_failure else '60'},json={'error':{'message':'deterministic provider failure'}})
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: e2e._HTTPX_CLIENT(**{**kwargs,'transport':transport}))
    provider = MeteredProvider(ledger_db=db,ledger_task='analysis',api_key='fixture',model='deepseek-flash',name='fixture',api_url='https://api.deepseek.com/chat/completions',read_timeout=1,use_streaming=False)
    monkeypatch.setattr(runtime, 'resolve_deepseek_v4_pro', lambda **_:ProviderResolution('configured',provider,'fixture',None))
    def initial_work(at):
        return run_once(db_path=db,worker_id='independent-review',lease_for=timedelta(minutes=5), handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'), clock=lambda:at,task_id=task_id)
    if interrupt_after_failure:
        record=runtime.record_analysis_artifact
        def crash(**kwargs):
            record(**kwargs)
            if kwargs['artifact'].status=='failed':raise SystemExit('after failure artifact commit')
        monkeypatch.setattr(runtime,'record_analysis_artifact',crash)
        with pytest.raises(SystemExit):initial_work(e2e.RUN_AT)
        assert store.get_task(task_id=task_id,db_path=db).status=='running'
        receipt=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['providerFailureReceipt']
        assert receipt['errorCode']==('rate_limited' if status==429 else 'insufficient_balance')
        assert datetime.fromisoformat(receipt['receivedAt'])==e2e.RUN_AT
        monkeypatch.setattr(runtime,'record_analysis_artifact',record)
        task=initial_work(e2e.RUN_AT+timedelta(seconds=301))
        assert 'providerFailureReceipt' not in store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']
    else:
        task=initial_work(e2e.RUN_AT)
    with sqlite3.connect(db) as conn:
        retries=conn.execute('SELECT count(*) FROM k10_task_retry_schedules WHERE task_id=?',(task_id,)).fetchone()[0]
        external=conn.execute('SELECT error_code FROM k10_external_attempts WHERE task_id=? ORDER BY rowid',(task_id,)).fetchall()
    execution = store.task_execution_input(task_id=task_id,db_path=db)
    with client_for(db) as client:
        chain=client.get('/api/v1/k10/company-windows/' + card['companyWindowId'] + '/analysis-chain').json()
    print(json.dumps({'providerStatus':status,'failedRole':failed_role,'taskStatus':task.status,'httpCalls':len(calls),'retries':retries,'attemptErrors':external,'taskError':execution.get('error'),'stage':execution.get('stage'),'analysisErrors':[a.get('error') for item in chain.get('items',[]) for a in item.get('analyses',[])]}, ensure_ascii=False))
    (tmp_path / f'b56_analysis_{failed_role}_{status}.json').write_text(json.dumps(chain,ensure_ascii=False))
    expected_calls = 1 if failed_role == 'pro' else 2
    assert len(calls) == expected_calls
    assert external[-1][0] == ('rate_limited' if status == 429 else 'insufficient_balance')
    before_payload = store.get_task(task_id=task_id,db_path=db).payload
    before_pro = [a for item in chain['items'] for a in item['analyses'] if a['role']=='pro' and a['status']=='completed']
    def work(at):
        return run_once(db_path=db,worker_id='review-recovery',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:at,task_id=task_id)
    if status==429:
        assert chain['items'][0]['job']['error']['message']=='模型服务限流，等待延后重试'
        assert task.status=='queued' and retries==1, '429 must retain the failed role and schedule Retry-After'
        with sqlite3.connect(db) as conn:
            due = datetime.fromisoformat(conn.execute('SELECT not_before_at FROM k10_task_retry_schedules WHERE task_id=?',(task_id,)).fetchone()[0])
        assert due == e2e.RUN_AT + timedelta(seconds=900 if interrupt_after_failure else 60)
        assert work(due-timedelta(seconds=1)) is None and len(calls)==expected_calls
        recover[0] = retry_succeeds
        if late_retry:
            retried=work(due+timedelta(hours=7))
            assert retried.status=='failed' and len(calls)==expected_calls
            with client_for(db) as client:
                expired=client.get('/api/v1/k10/company-windows/' + card['companyWindowId'] + '/analysis-chain').json()
            assert '完成时限' in expired['items'][0]['job']['error']['message']
            if before_pro:
                assert [a for item in expired['items'] for a in item['analyses'] if a['role']=='pro' and a['status']=='completed']==before_pro
            assert store.get_task(task_id=task_id,db_path=db).payload==before_payload
            with sqlite3.connect(db) as conn:
                assert conn.execute('SELECT count(*) FROM k10_task_retry_schedules WHERE task_id=?',(task_id,)).fetchone()[0]==0
            return
        retried=work(due)
        assert retried.status == ('completed' if retry_succeeds else 'failed')
        assert calls[expected_calls] == calls[expected_calls-1], 'only the failed role retries with identical frozen input'
        assert len(calls)==expected_calls + (2 if failed_role=='pro' and retry_succeeds else 1)
        with client_for(db) as client:
            recovered=client.get('/api/v1/k10/company-windows/' + card['companyWindowId'] + '/analysis-chain').json()
        if before_pro:
            assert [a for item in recovered['items'] for a in item['analyses'] if a['role']=='pro' and a['status']=='completed']==before_pro
        assert store.get_task(task_id=task_id,db_path=db).payload == before_payload
        if not retry_succeeds:
            with sqlite3.connect(db) as conn:
                assert '重试上限' in conn.execute('SELECT error_text FROM k10_tasks WHERE task_id=?',(task_id,)).fetchone()[0]
            assert work(due+timedelta(minutes=5)) is None
        else:
            (tmp_path / f'b56_analysis_{failed_role}_recovered.json').write_text(json.dumps(recovered,ensure_ascii=False))
    else:
        assert '余额不足' in json.dumps(chain,ensure_ascii=False), '402 must reach the actual client-readable analysis failure'
        assert task.status=='failed' and retries==0
        assert work(e2e.RUN_AT+timedelta(minutes=5)) is None and len(calls)==expected_calls

@pytest.mark.parametrize('status', [402, 429])
def test_paid_morning_work_item_failure_is_terminal_and_readable(tmp_path, monkeypatch, status):
    """A parent-owned review may fail once, but it never becomes a child retry."""
    calls=[]
    def resolve(**kwargs):
        def respond(request):
            calls.append(json.loads(request.content))
            return httpx.Response(status,headers={'Retry-After':'60'},json={'error':{'message':'deterministic morning failure'}})
        transport=httpx.MockTransport(respond)
        monkeypatch.setattr(httpx,'Client',lambda **opts:e2e._HTTPX_CLIENT(**{**opts,'transport':transport}))
        provider=MeteredProvider(ledger_db=kwargs['db_path'],ledger_task='morning',api_key='fixture',model='deepseek-flash',name='fixture',api_url='https://api.deepseek.com/chat/completions',read_timeout=1,use_streaming=False)
        return ProviderResolution('configured',provider,'fixture',None)
    monkeypatch.setattr(morning_runtime,'resolve_deepseek_v4_pro',resolve)
    db, old, parent, _=later_scan(tmp_path,monkeypatch,lambda payload,value:None)
    assert parent.status=='completed'
    with sqlite3.connect(db) as conn:
        work_items=conn.execute(
            'SELECT work_item_id,status,result_json,report_item_json,safe_error_code FROM k10_morning_review_work_items'
        ).fetchall()
        child_count=conn.execute("SELECT count(*) FROM k10_tasks WHERE kind='morning_review'").fetchone()[0]
        retries=conn.execute('SELECT count(*) FROM k10_task_retry_schedules').fetchone()[0]
        unsettled=conn.execute("SELECT count(*) FROM k10_external_attempts WHERE state IN ('started','unknown')").fetchone()[0]
        external_errors=conn.execute('SELECT error_code FROM k10_external_attempts WHERE error_code IS NOT NULL').fetchall()
    assert len(work_items)==1
    work_item=work_items[0]
    assert work_item[1]=='failed' and work_item[3] is not None
    assert external_errors[-1][0]==('rate_limited' if status==429 else 'insufficient_balance')
    assert child_count==0 and retries==0 and unsettled==0 and len(calls)==1
    with client_for(db) as client:
        report=client.get('/api/v1/k10/v2/reports/latest?window=morning').json()
    assert report['report']['status']=='partial'
    assert report['reason']['reason']=='partial_delivery'
    assert report['report']['delivery']['outcome']=='partial'
    assert 'morning_review_failed' in report['report']['coverageGaps']
    assert any(gap['reasonCode']==('rate_limited' if status==429 else 'insufficient_balance')
               for gap in report['report']['delivery']['gaps'])
    assert any(row['taskId']==work_item[0] and row['status']=='failed'
               for row in report['report']['incompleteReviews'])
    with client_for(db) as client:
        assert client.post('/api/v1/k10/jobs/'+work_item[0]+'/retry',json={'expectedAttemptCount':1}).status_code==409

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
    _freeze_k10_clocks(monkeypatch,datetime(2026,9,10,22,0,tzinfo=SHANGHAI).isoformat())
    with client_for(db) as client:
        actual=client.get('/api/v1/k10/v2/reports/latest?window=evening')
        assert actual.status_code==200
        api_card=actual.json()['report']['eveningCards'][0]
        assert api_card['companyWindowId']==third['companyWindowId'] and api_card['canSelect'] is True
        blocked=client.post('/api/v1/k10/company-windows/'+api_card['companyWindowId']+'/selection',json={'action':'keep','idempotencyKey':'correct-stage-window'})
        assert blocked.status_code==200
        print(json.dumps({'apiCardWindow':api_card['companyWindowId'],'apiCanSelect':api_card['canSelect'],'selectStatus':blocked.status_code,'selectMessage':blocked.json(),'actualMaterialState':store.get_opportunity(opportunity_id=material['opportunityId'],db_path=db)['state']},ensure_ascii=False))
    print(json.dumps({'initialWindow':initial['companyWindowId'],'initialDates':[initial['d1TradeDate'],initial['d2TradeDate']],'materialOpportunity':material['opportunityId'],'materialWindow':material['companyWindowId'],'materialDates':[material['d1TradeDate'],material['d2TradeDate']],'actualThirdWindow':third['companyWindowId'],'actualDates':[third['d1TradeDate'],third['d2TradeDate']],'thirdCanSelect':third['canSelect'],'thirdCatalysts':third['catalysts'],'opportunityCount':len(store.list_opportunities(company_code='300002.SZ',db_path=db))},ensure_ascii=False))
    assert third['companyWindowId']==material['companyWindowId'], 'Continuing material-stage card must use the related material window, not the original catalyst window'

    assert len(store.list_opportunities(company_code='300002.SZ',db_path=db))==2
    assert [third['d1TradeDate'],third['d2TradeDate']]==[material['d1TradeDate'],material['d2TradeDate']]
    assert third['catalysts'][0]['opportunityId']==material['opportunityId']
    (tmp_path / 'b56_stage_evening.json').write_text(json.dumps(actual.json(),ensure_ascii=False))


@pytest.mark.parametrize('kind', ['continuation','needs_review','invalidated'])
def test_explicit_material_predecessor_owns_update_identity(kind):
    from neckline.k10.opportunity_discovery import validate_classification
    previous=[{'opportunityId':'initial','opportunityKey':'event\x1f300002.SZ','companyCode':'300002.SZ','canonicalKey':'event','catalystStage':'sample'},
              {'opportunityId':'material','opportunityKey':'event\x1f300002.SZ\x1forder','companyCode':'300002.SZ','canonicalKey':'event','catalystStage':'order'}]
    decision=validate_classification({'kind':kind,'relatedOpportunityId':'material','reason':'本次材料更新订单阶段'},canonical_key='event',stage_key='order',company_code='300002.SZ',previous=previous)
    assert decision['opportunityKey']==previous[1]['opportunityKey']
    with pytest.raises(ValueError,match='不同公司'):
        validate_classification({'kind':kind,'relatedOpportunityId':'material','reason':'错误关联'},canonical_key='event',stage_key='order',company_code='300001.SZ',previous=previous)


@pytest.mark.parametrize('retry_after,expected', [(None,60),(-1,60),(float('nan'),60),(0,0),(37,37),(10**30,None)])
def test_rate_limit_uses_only_frozen_backoff_and_deadline(retry_after,expected):
    from dataclasses import replace
    from threading import Event
    from neckline.k10.types import Task
    from neckline.k10.worker import TaskContext
    from neckline.k10.provider_failures import provider_failure_result
    task=Task('isolated','analysis','running',1,'worker',None,{})
    profile={'payload':{'discovery':{'networkMaxAttempts':2,'retryBackoffSeconds':[60,300,900]}}}
    context=TaskContext(task,{}, {},'fixture',e2e.RUN_AT.isoformat(),None,Event(),execution_profile=profile,
                        execution_deadline_at=e2e.RUN_AT+timedelta(hours=6),clock=lambda:e2e.RUN_AT)
    result=provider_failure_result(context=context,stage='con_failed',code='rate_limited',retry_after_seconds=retry_after)
    assert result.retry_at==(e2e.RUN_AT+timedelta(seconds=expected) if expected is not None else None)
    exhausted=provider_failure_result(context=replace(context,failure_attempt_count=1),stage='con_failed',code='rate_limited',retry_after_seconds=0)
    assert exhausted.retry_at is None and '重试上限' in exhausted.error


@pytest.mark.parametrize('status',[402,429])
def test_pro_commit_interruption_does_not_repeat_successful_provider_call(tmp_path,monkeypatch,status,completed_roles="pro",late_resume=False):
    db,_,_,_,_=e2e._run(tmp_path,monkeypatch,v2=True)
    _freeze_k10_clocks(monkeypatch,e2e.RUN_AT.isoformat())
    card=read_report(db_path=db)['eveningCards'][0]
    with client_for(db) as client:
        job=client.post('/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection',json={'action':'keep','idempotencyKey':'interrupted-pro'}).json()['analysisJobId']
    calls=[]
    def respond(request):
        calls.append(json.loads(request.content))
        if len(calls)==1 or completed_roles=="both":
            from tests.debate_fixture import debate_text
            return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':debate_text('持久正方全文')},'finish_reason':'stop'}]})
        return httpx.Response(status,headers={'Retry-After':'60'},json={'error':{'message':'synthetic error'}})
    transport=httpx.MockTransport(respond)
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:e2e._HTTPX_CLIENT(**{**kwargs,'transport':transport}))
    provider=MeteredProvider(ledger_db=db,ledger_task='analysis',api_key='fixture',model='deepseek-flash',name='fixture',api_url='https://api.deepseek.com/chat/completions',read_timeout=1,use_streaming=False)
    monkeypatch.setattr(runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'fixture',None))
    record=runtime.record_analysis_artifact
    def interrupted(**kwargs):
        record(**kwargs)
        if kwargs['artifact'].role==('con' if completed_roles=='both' else 'pro'):
            raise RuntimeError('synthetic interruption after durable role')
    monkeypatch.setattr(runtime,'record_analysis_artifact',interrupted)
    def work(at=e2e.RUN_AT):
        return run_once(db_path=db,worker_id='interruption',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture',parquet_dir=tmp_path/'parquet'),clock=lambda:at,task_id=job)
    first=work()
    assert first.status=='failed' and len(calls)==(2 if completed_roles=='both' else 1)
    monkeypatch.setattr(runtime,'record_analysis_artifact',record)
    resumed_at=e2e.RUN_AT+timedelta(hours=7) if late_resume else e2e.RUN_AT
    _freeze_k10_clocks(monkeypatch,resumed_at.isoformat())
    with client_for(db) as client:
        response=client.post('/api/v1/k10/jobs/'+job+'/retry',json={'expectedAttemptCount':first.attempt_count})
        assert response.status_code==200
    second=work(resumed_at)
    assert second.status==('completed' if completed_roles=='both' else 'queued' if status==429 else 'failed') and len(calls)==2
    assert '持久正方全文' in json.dumps(calls[1],ensure_ascii=False)
    with client_for(db) as client:
        chain=client.get('/api/v1/k10/company-windows/'+card['companyWindowId']+'/analysis-chain').json()
    pro=[a for item in chain['items'] for a in item['analyses'] if a['role']=='pro']
    assert len(pro)==1 and pro[0]['status']=='completed'


def test_build56_health_and_operator_config_paths():
    from pathlib import Path
    from fastapi.testclient import TestClient
    from neckline.api.app import app, VERSION, RELEASE_SET
    # No startup or operational database access is needed by this public endpoint.
    response=TestClient(app).get('/api/v1/health')
    import re
    project = (Path(__file__).parents[2]/'App/project.yml').read_text()
    version = re.search(r'MARKETING_VERSION:\s*"([^"]+)"', project).group(1)
    # A backend-only hotfix deliberately keeps the installed client build.
    assert VERSION == f'v{version}'
    assert response.status_code == 200 and response.json()['releaseSet'] == RELEASE_SET
    assert re.fullmatch(rf'v{re.escape(version)}-b[1-9][0-9]*', RELEASE_SET)
    root=Path(__file__).parents[2]
    for name in ('k10-v2.json','k10-execution-v4.json'):
        assert (root/'Backend/neckline/config'/name).is_file()
    readme=(root/'README.md').read_text()
    assert '[k10-execution-v3.json]' not in readme
    assert '[k10-execution-v4.json]' in readme and '[k10-v2.json]' in readme


@pytest.mark.parametrize('role',['pro','con'])
def test_late_claim_of_analysis_retry_cannot_spend_after_frozen_deadline(tmp_path,monkeypatch,role):
    test_paid_analysis_error_contract(tmp_path,monkeypatch,429,True,role,late_retry=True)


def test_completed_analysis_can_finalize_after_deadline_without_any_provider_call(tmp_path,monkeypatch):
    test_pro_commit_interruption_does_not_repeat_successful_provider_call(tmp_path,monkeypatch,429,completed_roles='both',late_resume=True)


@pytest.mark.parametrize('status',[402,429])
@pytest.mark.parametrize('role',['pro','con'])
def test_failure_artifact_crash_restores_terminal_or_original_retry_without_new_call(tmp_path,monkeypatch,status,role):
    test_paid_analysis_error_contract(tmp_path,monkeypatch,status,True,role,interrupt_after_failure=True)


def test_failure_receipt_rolls_back_with_attempt_and_unknown_outcome_blocks_new_attempt(tmp_path,monkeypatch):
    db,_,_,_,_=e2e._run(tmp_path,monkeypatch,v2=True)
    _freeze_k10_clocks(monkeypatch,e2e.RUN_AT.isoformat())
    card=read_report(db_path=db)['eveningCards'][0]
    with client_for(db) as client:
        job=client.post('/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection',json={'action':'keep','idempotencyKey':'receipt-atomic'}).json()['analysisJobId']
    args=dict(task_id=job,stage='analysisPro',item_key='same-frozen-role',input_sha256='a'*64,started_at=e2e.RUN_AT.isoformat(),db_path=db)
    attempt=store.begin_external_attempt(attempt_key='first',**args)
    assert attempt['state']=='started'
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TRIGGER reject_receipt BEFORE UPDATE OF checkpoint_json ON k10_tasks WHEN json_extract(NEW.checkpoint_json,'$.providerFailureReceipt') IS NOT NULL BEGIN SELECT RAISE(ABORT,'synthetic receipt failure'); END")
    with pytest.raises(sqlite3.IntegrityError,match='receipt failure'):
        store.settle_external_attempt(attempt_id=attempt['attemptId'],outcome='failed',usage=None,settled_at=e2e.RUN_AT.isoformat(),error_code='rate_limited',db_path=db,record_provider_failure=True,retry_after_seconds=900)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT state,error_code FROM k10_external_attempts WHERE attempt_id=?',(attempt['attemptId'],)).fetchone()==('started',None)
    assert 'providerFailureReceipt' not in store.task_execution_input(task_id=job,db_path=db)['checkpoint']
    assert store.begin_external_attempt(attempt_key='different-worker-attempt',**args)['state']=='pending_outcome'
