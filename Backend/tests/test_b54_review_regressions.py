from tests.test_k10_end_to_end import _debate_result
"""Original independent B54 counterexamples, through real API/CLI/worker entries."""
import json
import sqlite3
from datetime import date, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tests.test_v310_pipeline_e2e as e2e
from neckline.api.k10 import create_router
from neckline.k10 import pipeline, store
from neckline.k10.cli import enqueue_scan
from neckline.k10.worker import run_once
from neckline.k10.v2_store import read_report
from neckline.k10.windows import SHANGHAI
from tests.test_k10_api import _freeze_k10_clocks


def client_for(db):
    app = FastAPI()
    app.include_router(create_router(lambda:db,lambda:None,lambda:db.parent/'parquet',
        current_config_binding_provider=lambda:('b39',1,None),
        current_execution_config_binding_provider=lambda:('b39-execution',1,None)))
    return TestClient(app)


def intercept(mp, change):
    original = httpx.Client()._transport
    def respond(request):
        response = original.handle_request(request)
        body = response.json()
        message = json.loads(request.content)['messages'][-1]['content']
        payload = json.loads(message.split('<untrusted-k10-evidence>\n',1)[1].split('\n</untrusted-k10-evidence>',1)[0])
        value = json.loads(body['choices'][0]['message']['content'])
        change(payload,value)
        body['choices'][0]['message']['content'] = json.dumps(value)
        return httpx.Response(200,json=body)
    transport = httpx.MockTransport(respond)
    mp.setattr(httpx,'Client',lambda **kwargs:e2e._HTTPX_CLIENT(**{**kwargs,'transport':transport}))


def test_r1_real_keep_creates_bound_analysis_without_test_binding(tmp_path,monkeypatch):
    db,_,_,_,_ = e2e._run(tmp_path,monkeypatch,v2=True)
    _freeze_k10_clocks(monkeypatch,e2e.RUN_AT.isoformat())
    card = read_report(db_path=db)['eveningCards'][0]
    with client_for(db) as client:
        assert all(row['state']=='configured' for row in client.get('/api/v1/k10/configuration').json()['scopes'])
        response = client.post('/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection',json={'action':'keep','idempotencyKey':'review-keep'})
        assert response.status_code == 200
        job = response.json()['analysisJobId']
        with sqlite3.connect(db) as conn:
            assert conn.execute('SELECT execution_config_id,execution_config_revision FROM k10_task_execution_bindings WHERE task_id=?',(job,)).fetchone() == ('b39-execution',1)
        from neckline.k10 import runtime
        from neckline.k10.providers import ProviderResolution
        from tests.test_k10_end_to_end import FakeProvider, _result
        provider = FakeProvider([_debate_result('正方全文'),_debate_result('反方全文')])
        monkeypatch.setattr(runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'deepseek',None))
        result = run_once(db_path=db,worker_id='real-keep',lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:e2e.RUN_AT,task_id=job)
        assert result.status == 'completed' and provider.results == []
        response=client.post('/api/v1/k10/company-windows/'+card['companyWindowId']+'/analysis-requests',json={'kind':'user_question','question':'哪些公开事实会推翻原判断？','sourceRefs':[],'idempotencyKey':'review-followup'})
        assert response.status_code in {200,201},response.text
        followup=response.json()['analysisJobId']
        assert store.task_execution_profile(task_id=followup,db_path=db)['configId']=='b39-execution'
        provider.results=[_debate_result('追问正方'),_debate_result('追问反方')]
        resumed=run_once(db_path=db,worker_id='real-followup',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:e2e.RUN_AT,task_id=followup)
        assert resumed.status=='completed' and provider.results==[]



def later_scan(tmp_path,mp,change,*,kind='morning'):
    db,_,_,_,_ = e2e._run(tmp_path,mp,v2=True)
    old = store.list_opportunities(db_path=db)[0]
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO trade_cal VALUES ('SSE',?,1)", [('20260911',),('20260914',),('20260915',)])
    later = datetime(2026,9,9,9,10,tzinfo=SHANGHAI) if kind=='morning' else datetime(2026,9,9,22,0,tzinfo=SHANGHAI)
    mp.setattr(pipeline,'_now',lambda:later)
    calls = e2e._http_transport(mp,v2=True)
    if kind=='morning':
        from neckline.k10.sources import SourceDocumentInput,SourceFetchResult
        class News(e2e._News):
            def fetch_incremental(self,request):
                published = datetime(2026,9,9,8,0,tzinfo=SHANGHAI)
                return SourceFetchResult(documents=(SourceDocumentInput('denial-news',None,'公司发布澄清公告：从未进入所述项目送样阶段，供应商之前的说法错误。',None,published,'exact',published+timedelta(minutes=1),'review-denial',{'title':'公司否认项目送样传闻'}),),next_cursor='denial',success_watermark=request.window.cutoff_at,pages_fetched=1,pages_expected=1,exhausted=True)
        mp.setattr(pipeline,'TuShareMajorNewsAdapter',News)
    intercept(mp,change)
    task_id = enqueue_scan(db_path=db,kind=kind,trading_day=date(2026,9,9),config_id='b39',config_revision=1,
        execution_config_id='b39-execution',execution_config_revision=1,now=later)
    result = run_once(db_path=db,worker_id='review-'+kind,lease_for=timedelta(minutes=5),
        handlers={kind+'_scan':lambda ctx:pipeline.production_scan_handler(ctx,tushare_token='fixture-token',parquet_dir=tmp_path/'parquet',now=lambda:later)},clock=lambda:later,task_id=task_id)
    return db,old,result,calls


@pytest.mark.parametrize('role',['excluded','pending'])
def test_r2_continuation_must_pass_todays_recommendation_role(tmp_path,monkeypatch,role):
    def change(payload,value):
        if payload.get('action')=='compare_companies':
            for row in value['companyAssessments']:
                if row['companyCode']=='300002.SZ':row.update(role=role,rank=None,summary='本轮不推荐旧催化')
    db,old,result,_ = later_scan(tmp_path,monkeypatch,change,kind='evening')
    assert result.status == 'completed'
    assert read_report(db_path=db)['eveningCards'] == []
    assert store.list_opportunities(db_path=db)[0]['opportunityId'] == old['opportunityId']


def denial_change(observed,verified=False):
    def change(payload,value):
        action = payload.get('action')
        if action=='assess_evidence':
            ref = payload['evidencePacket']['allowedEvidenceRefs'][0]
            value['claims']=[{'claimId':'article-claim-1','verificationStatus':'contradicted','decisionImpact':'已核实否认原催化'}]
            value['evidenceUpdates']=[{'claimId':'article-claim-1','sourceRef':ref,'relation':'contradicts','location':'paragraph:1','applicability':{}}]
        if action=='compare_companies':
            for row in value['companyAssessments']:
                row.update(role='excluded',rank=None,summary='核心催化被推翻')
                row['evidenceDisclosure'].update(verificationStatus='verified' if verified else 'contradicted',isRumor=False,unverifiedReasons=[],conditionalAnalysis=None)
        if isinstance(payload.get('output'),dict) and 'kind' in payload['output']:
            observed[payload['companyCode']]=payload['verification']['state']
            prior=payload['previousOpportunities']
            value.update(kind='invalidated' if prior else 'background',relatedOpportunityId=prior[0]['opportunityId'] if prior else None,reason='公告已核实否认原送样事项')
    return change


def test_r3_contradicted_reaches_company_classifier_without_downgrade(tmp_path,monkeypatch):
    observed={}
    db,old,_,_=later_scan(tmp_path,monkeypatch,denial_change(observed))
    assert observed['300002.SZ']=='contradicted'
    assert next(row for row in store.list_opportunities(db_path=db) if row['opportunityId']==old['opportunityId'])['state']=='withdrawn'


def test_r4_durable_withdrawal_reaches_real_morning_dto_without_new_sample(tmp_path,monkeypatch):
    db,old,_,_=later_scan(tmp_path,monkeypatch,denial_change({},verified=True))
    assert store.list_opportunities(db_path=db)[0]['state']=='withdrawn'
    with client_for(db) as client:
        report=client.get('/api/v1/k10/v2/reports/latest?window=morning').json()['report']
        changes=report.get('lifecycleUpdates',[])
        assert any(row['kind']=='withdrawal' and row['opportunityId']==old['opportunityId'] for row in changes)
        assert not report['addedCards']
        evening=client.get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']
        assert evening['eveningCards'][0]['canSelect'] is False
    assert len(store.list_opportunities(db_path=db))==1


def test_r5_first_empty_paths_still_closes_and_compares_existing_evidence(tmp_path,monkeypatch):
    original=e2e._http_transport
    def no_paths(mp,**kwargs):
        calls=original(mp,**kwargs)
        intercept(mp,lambda payload,value:value.update(queryPaths=[]) if payload.get('action')=='plan_queries' else None)
        return calls
    monkeypatch.setattr(e2e,'_http_transport',no_paths)
    db,_,task,calls,gateway=e2e._run(tmp_path,monkeypatch,v2=True)
    assert task.status=='completed'
    assert 'research:close_research' in calls and 'research:compare_companies' in calls
    assert not gateway.search_paths
    assert read_report(db_path=db)['eveningCards']


def test_r1_analysis_binding_interruption_rolls_back_keep_and_outbox(tmp_path,monkeypatch):
    db,_,_,_,_=e2e._run(tmp_path,monkeypatch,v2=True)
    _freeze_k10_clocks(monkeypatch,e2e.RUN_AT.isoformat())
    card=read_report(db_path=db)['eveningCards'][0]
    tables=('k10_company_window_actions','k10_observations','k10_tasks','k10_task_outbox','k10_task_execution_bindings')
    with sqlite3.connect(db) as conn: before={table:conn.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in tables}
    real=store._bind_task_execution_conn
    def interrupted(conn,**kwargs):
        real(conn,**kwargs)
        raise RuntimeError('synthetic binding interruption')
    monkeypatch.setattr(store,'_bind_task_execution_conn',interrupted)
    with client_for(db) as client,pytest.raises(RuntimeError,match='synthetic binding interruption'):
        client.post('/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection',json={'action':'keep','idempotencyKey':'interrupted'})
    with sqlite3.connect(db) as conn: assert {table:conn.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in tables}==before


def test_r1_morning_child_inherits_parent_binding_and_projects_late_risk(tmp_path,monkeypatch):
    from neckline.k10 import morning_runtime
    from neckline.k10.providers import ProviderResolution
    from tests.test_k10_end_to_end import FakeProvider,_result
    provider=FakeProvider([_result(json.dumps({'material':True,'reasonStatus':'needs_review','observationStatus':'needs_review','summary':'新增资料尚有不确定性','materialContraryEvidence':[]}))])
    monkeypatch.setattr(morning_runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'deepseek',None))
    db,old,result,_=later_scan(tmp_path,monkeypatch,lambda payload,value:None)
    with sqlite3.connect(db) as conn:
        children=conn.execute("SELECT t.task_id,t.status,b.execution_config_id FROM k10_tasks t LEFT JOIN k10_task_execution_bindings b ON b.task_id=t.task_id WHERE t.kind='morning_review'").fetchall()
    assert children and all(row[1:] == ('completed','b39-execution') for row in children),children
    assert provider.results==[]
    changes=read_report(db_path=db,window='morning')['lifecycleUpdates']
    assert any(row['kind']=='risk' and row['opportunityId']==old['opportunityId'] for row in changes)


def test_r3_mixed_company_disclosures_only_withdraw_the_refuted_company(tmp_path,monkeypatch):
    original=e2e._http_transport
    rounds=0
    def seeded(mp,**kwargs):
        nonlocal rounds
        calls=original(mp,**kwargs);rounds+=1
        if rounds==1:
            def include_second(payload,value):
                if payload.get('action')=='compare_companies':
                    for row in value['companyAssessments']:
                        if row['companyCode']=='300004.SZ':
                            row.update(role='alternative',rank=2)
                            row['evidenceDisclosure'].update(verificationStatus='unverified',isRumor=True,unverifiedReasons=['独立关联待核'],conditionalAnalysis='若关联成立再评估')
            intercept(mp,include_second)
        return calls
    monkeypatch.setattr(e2e,'_http_transport',seeded)
    seen={}
    deny=denial_change(seen)
    def mixed(payload,value):
        deny(payload,value)
        if payload.get('action')=='compare_companies':
            for row in value['companyAssessments']:
                if row['companyCode']=='300004.SZ':
                    row.update(role='primary',rank=1)
                    row['evidenceDisclosure'].update(verificationStatus='unverified',isRumor=True,unverifiedReasons=['另一公司关联待核'],conditionalAnalysis='仅在独立关联成立时重估')
        if isinstance(payload.get('output'),dict) and 'kind' in payload['output'] and payload['companyCode']=='300004.SZ':
            value.update(kind='continuation',reason='另一家公司原有催化未被否认')
    db,_,_,_=later_scan(tmp_path,monkeypatch,mixed)
    _freeze_k10_clocks(monkeypatch,"2026-09-09T09:10:00+08:00")
    states={row['companyCode']:row['state'] for row in store.list_opportunities(db_path=db)}
    assert states['300002.SZ']=='withdrawn' and states['300004.SZ']=='active'
    assert seen['300002.SZ']=='contradicted'
    # V2 exact-stage recommendations now reuse their identity without another
    # classifier call. The refuted company's withdrawal still uses that route.
    assert '300004.SZ' not in seen
    card = next(row for row in read_report(db_path=db,window='morning')['updatedCards'] if row['companyCode']=='300004.SZ')
    assert card['catalysts'][0]['verificationStatus']=='unverified'


def test_r4_lifecycle_read_is_durable_readonly_and_does_not_reset_selection(tmp_path,monkeypatch):
    from tests.k10_v320_repair_fixture import build_repair_fixture
    db=tmp_path/'repair.sqlite';build_repair_fixture(db)
    _freeze_k10_clocks(monkeypatch,"2026-09-09T09:22:00+08:00")
    with sqlite3.connect(db) as conn:
        frozen=conn.execute('SELECT * FROM k10_company_window_selection_snapshots').fetchall()
        windows=conn.execute('SELECT * FROM k10_company_windows').fetchall()
        samples=conn.execute('SELECT * FROM k10_publication_samples').fetchall()
    before=db.read_bytes()
    from tests.k10_v320_fixture import create_app
    with TestClient(create_app(db)) as client:
        evening=client.get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']
        morning=client.get('/api/v1/k10/v2/reports/latest?window=morning').json()['report']
        assert {x['kind'] for x in evening['lifecycleUpdates']+morning['lifecycleUpdates']}=={'risk','withdrawal','expiry'}
        assert not evening['eveningCards'][0]['canSelect']
        assert morning['updatedCards'][0]['canSelect']
        assert {x['lifecycleState'] for x in morning['updatedCards'][0]['catalysts']}=={'active','withdrawn'}
        assert any(x['companyCode'] not in {c['companyCode'] for c in evening['eveningCards']} for x in evening['lifecycleUpdates'])
    assert db.read_bytes()==before
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT * FROM k10_company_window_selection_snapshots').fetchall()==frozen
        assert conn.execute('SELECT * FROM k10_company_windows').fetchall()==windows
        assert conn.execute('SELECT * FROM k10_publication_samples').fetchall()==samples


def test_r1_missing_approved_policy_is_not_reported_configured(tmp_path):
    from tests.k10_v320_fixture import build_fixture,create_app
    db=tmp_path/'policy.sqlite';build_fixture(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE k10_title_triage_policy_revisions SET approval_state='draft',approved_at=NULL")
    with TestClient(create_app(db)) as client:
        config=client.get('/api/v1/k10/configuration').json()
        assert any(row['state']=='not_configured' for row in config['scopes'])
        assert client.get('/api/v1/k10/v2/reports/latest').json()['state']=='not_configured'


def test_r4_change_link_interruption_rolls_back_lifecycle_event(tmp_path):
    from tests.k10_v320_fixture import build_fixture
    db=tmp_path/'atomic-change.sqlite';build_fixture(db)
    opportunity=next(row for row in store.list_opportunities(db_path=db) if row['firstBatchId']=='v2-batch-evening')
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TRIGGER reject_change BEFORE INSERT ON k10_v2_report_lifecycle_updates BEGIN SELECT RAISE(ABORT,'synthetic link interruption'); END")
    with pytest.raises(sqlite3.IntegrityError,match='synthetic link interruption'):
        store.append_opportunity_update(lifecycle_event_id='atomic-risk',opportunity_id=opportunity['opportunityId'],kind='risk',reason='待核',source_refs=[],content={'scanId':'v2-scan-morning'},occurred_at='2026-09-09T09:20:00+08:00',created_at='2026-09-09T09:20:00+08:00',db_path=db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM k10_opportunity_lifecycle_events WHERE lifecycle_event_id='atomic-risk'").fetchone()[0]==0


def test_r5_no_paths_can_close_without_mapping_and_does_not_force_a_recommendation(tmp_path,monkeypatch):
    original=e2e._http_transport
    def no_mapping(mp,**kwargs):
        calls=original(mp,**kwargs)
        def change(payload,value):
            if payload.get('action')=='plan_queries':value['queryPaths']=[]
            if payload.get('action')=='close_research':
                value['conclusion'].update(researchStatus='background_only',companyMappings=[],stopReason='已有证据无法合理关联池内公司，作为背景保留')
        intercept(mp,change)
        return calls
    monkeypatch.setattr(e2e,'_http_transport',no_mapping)
    db,_,task,calls,gateway=e2e._run(tmp_path,monkeypatch,v2=True)
    assert task.status=='completed' and 'research:close_research' in calls
    assert 'research:compare_companies' not in calls and not gateway.search_paths
    assert read_report(db_path=db)['eveningCards']==[]


@pytest.mark.parametrize('role',['pending','excluded'])
def test_r2_verified_nonrecommended_new_company_also_stays_out(tmp_path,monkeypatch,role):
    original=e2e._http_transport
    def nonrecommended(mp,**kwargs):
        calls=original(mp,**kwargs)
        def change(payload,value):
            if payload.get('action')=='compare_companies':
                for row in value['companyAssessments']:
                    if row['companyCode']=='300002.SZ':
                        row.update(role=role,rank=None)
                        row['evidenceDisclosure'].update(verificationStatus='verified',isRumor=False,unverifiedReasons=[],conditionalAnalysis=None)
        intercept(mp,change)
        return calls
    monkeypatch.setattr(e2e,'_http_transport',nonrecommended)
    db,_,task,_,_=e2e._run(tmp_path,monkeypatch,v2=True)
    assert task.status=='completed'
    assert read_report(db_path=db)['eveningCards']==[] and store.list_opportunities(db_path=db)==[]
