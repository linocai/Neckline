from tests.test_k10_end_to_end import _debate_result
"""Second independent review: missing bindings, morning failure/replay and closed restore."""
import json
import sqlite3
from dataclasses import replace
from datetime import datetime,timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neckline.api.k10 import create_router
from neckline.k10 import store,pipeline,runtime,morning_runtime
from neckline.k10.providers import ProviderResolution
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
from neckline.k10.windows import SHANGHAI
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for,later_scan
from tests.test_k10_api import _freeze_k10_clocks
from tests.test_k10_end_to_end import FakeProvider,_result


def test_missing_runtime_binding_rejects_keep_without_any_writes(tmp_path,monkeypatch):
    db,_,_,_,_=e2e._run(tmp_path,monkeypatch,v2=True)
    card=read_report(db_path=db)['eveningCards'][0]
    app=FastAPI();app.include_router(create_router(lambda:db,lambda:None,lambda:tmp_path/'parquet',
        current_config_binding_provider=lambda:('b39',1,None),current_execution_config_binding_provider=lambda:(None,None,'missing')))
    before=db.read_bytes()
    with TestClient(app) as client:
        response=client.post('/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection',json={'action':'keep','idempotencyKey':'missing'})
        assert response.status_code==409
    assert db.read_bytes()==before


@pytest.mark.parametrize('entry',['keep','retry'])
def test_legacy_unbound_task_recovers_through_real_post_with_explicit_binding(tmp_path,monkeypatch,entry):
    db,_,_,_,_=e2e._run(tmp_path,monkeypatch,v2=True)
    now=e2e.RUN_AT+timedelta(minutes=1);_freeze_k10_clocks(monkeypatch,now.isoformat())
    card=read_report(db_path=db)['eveningCards'][0]
    with client_for(db) as client:
        route='/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection'
        job=client.post(route,json={'action':'keep','idempotencyKey':'legacy'}).json()['analysisJobId']
        # Exact durable state left by the prior implementation; never manually bind it in a test.
        with sqlite3.connect(db) as conn:conn.execute('DELETE FROM k10_task_execution_bindings WHERE task_id=?',(job,))
        first=run_once(db_path=db,worker_id='unbound',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture',parquet_dir=tmp_path/'parquet'),clock=lambda:now,task_id=job)
        assert first.status=='not_configured'
        if entry=='keep':
            response=client.post(route,json={'action':'keep','idempotencyKey':'restored'})
            assert response.status_code==200 and response.json()['analysisJobId']==job
            assert store.task_execution_profile(task_id=job,db_path=db) is not None
        response=client.post('/api/v1/k10/jobs/'+job+'/retry',json={'expectedAttemptCount':first.attempt_count})
        assert response.status_code==200
        assert store.task_execution_profile(task_id=job,db_path=db)['configId']=='b39-execution'
        provider=FakeProvider([_debate_result('正方'),_debate_result('反方')])
        monkeypatch.setattr(runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'deepseek',None))
        result=run_once(db_path=db,worker_id='restored',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture',parquet_dir=tmp_path/'parquet'),clock=lambda:now,task_id=job)
        assert result.status=='completed' and provider.results==[]


def test_failed_morning_child_is_visible_in_current_daily_dto(tmp_path,monkeypatch):
    provider=FakeProvider([replace(_result(''),ok=False)])
    monkeypatch.setattr(morning_runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'deepseek',None))
    db,old,_,_=later_scan(tmp_path,monkeypatch,lambda payload,value:None)
    _freeze_k10_clocks(monkeypatch,"2026-09-09T09:10:00+08:00")
    with client_for(db) as client:
        envelope=client.get('/api/v1/k10/v2/reports/latest?window=morning').json()
    report=envelope['report']
    assert report['status'] in {'partial','failed'} and envelope['reason'] is not None
    assert report['updatedCards'] or report['addedCards']
    assert report['coverageGaps'] and any(row['opportunityId']==old['opportunityId'] and row['status']=='failed' for row in report['incompleteReviews'])
    child=report['incompleteReviews'][0]
    provider.results=[_result(json.dumps({'material':False,'reasonStatus':'current','observationStatus':'current','summary':'完整复核无变化','materialContraryEvidence':[]}))]
    now=datetime(2026,9,9,9,12,tzinfo=SHANGHAI);_freeze_k10_clocks(monkeypatch,now.isoformat())
    task=store.get_task(task_id=child['taskId'],db_path=db)
    with client_for(db) as client:
        assert client.post('/api/v1/k10/jobs/'+child['taskId']+'/retry',json={'expectedAttemptCount':task.attempt_count}).status_code==200
    result=run_once(db_path=db,worker_id='failure-recovered',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture',parquet_dir=tmp_path/'parquet'),clock=lambda:now,task_id=child['taskId'])
    assert result.status=='completed'
    with client_for(db) as client:
        recovered=client.get('/api/v1/k10/v2/reports/latest?window=morning').json()
    assert recovered['report']['status']=='completed' and recovered['reason'] is None
    assert recovered['report']['coverageGaps']==[] and recovered['report']['incompleteReviews']==[]
    assert recovered['report']['updatedCards']==report['updatedCards']


@pytest.mark.parametrize('status,kind', [('needs_review','risk'),('current','evidence_update')])
def test_morning_lifecycle_commit_before_checkpoint_is_reused_on_retry(tmp_path,monkeypatch,status,kind,late_retry=False):
    now=[datetime(2026,9,9,9,10,tzinfo=SHANGHAI)]
    class ClockDateTime(datetime):
        @classmethod
        def now(cls,tz=None):return now[0].astimezone(tz) if tz else now[0].replace(tzinfo=None)
    monkeypatch.setattr(morning_runtime,'datetime',ClockDateTime)
    response=_result(json.dumps({'material':True,'reasonStatus':status,'observationStatus':status,'summary':'新增资料尚有不确定性','materialContraryEvidence':[]}))
    provider=FakeProvider([response,response]);monkeypatch.setattr(morning_runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'deepseek',None))
    real=morning_runtime._report_checkpoint
    def interrupted(**kwargs):raise morning_runtime.MorningReportError('synthetic interruption after lifecycle commit')
    monkeypatch.setattr(morning_runtime,'_report_checkpoint',interrupted)
    db,_,_,_=later_scan(tmp_path,monkeypatch,lambda payload,value:None)
    with sqlite3.connect(db) as conn:
        child=conn.execute("SELECT task_id,status,attempt_count FROM k10_tasks WHERE kind='morning_review'").fetchone()
        before=conn.execute("SELECT * FROM k10_opportunity_lifecycle_events WHERE kind=?",(kind,)).fetchall()
    assert before and child[1]=='failed' and len(provider.results)==1
    monkeypatch.setattr(morning_runtime,'_report_checkpoint',real)
    now[0]+=(timedelta(hours=7) if late_retry else timedelta(minutes=1));_freeze_k10_clocks(monkeypatch,now[0].isoformat())
    with client_for(db) as client:
        assert client.post('/api/v1/k10/jobs/'+child[0]+'/retry',json={'expectedAttemptCount':child[2]}).status_code==200
    result=run_once(db_path=db,worker_id='interruption-retry',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture',parquet_dir=tmp_path/'parquet'),clock=lambda:now[0],task_id=child[0])
    assert result.status=='completed' and len(provider.results)==1
    with client_for(db) as client:
        recovered=client.get('/api/v1/k10/v2/reports/latest?window=morning').json()
    assert recovered['report']['status']=='completed' and recovered['reason'] is None
    assert recovered['report']['coverageGaps']==[] and recovered['report']['incompleteReviews']==[]
    with sqlite3.connect(db) as conn:assert conn.execute("SELECT * FROM k10_opportunity_lifecycle_events WHERE kind=?",(kind,)).fetchall()==before

    assert len([item for item in recovered['report']['lifecycleUpdates'] if item['kind']==kind])==1


def test_closed_restore_is_rejected_and_history_exposes_same_can_select(tmp_path,monkeypatch):
    db,_,_,_,_=e2e._run(tmp_path,monkeypatch,v2=True)
    now=e2e.RUN_AT+timedelta(minutes=1);_freeze_k10_clocks(monkeypatch,now.isoformat())
    card=read_report(db_path=db)['eveningCards'][0];opportunity=store.list_opportunities(db_path=db)[0]
    with client_for(db) as client:
        route='/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection'
        assert client.post(route,json={'action':'skip','idempotencyKey':'skip'}).status_code==200
        store.withdraw_opportunity(opportunity_id=opportunity['opportunityId'],reason='唯一理由已被证伪',source_refs=[],withdrawn_at=now.isoformat(),db_path=db)
        assert client.post(route,json={'action':'keep','idempotencyKey':'closed-keep'}).status_code==409
        assert client.post(route,json={'action':'restore','idempotencyKey':'closed-restore'}).status_code==409
        value=client.get('/api/v1/k10/company-windows/'+card['companyWindowId']).json()
        assert value['canSelect'] is False
        assert store.get_company_window_selection(company_window_id=card['companyWindowId'],db_path=db)['currentState']=='skipped'


def test_closed_target_does_not_disable_other_active_window_of_same_company(tmp_path,monkeypatch):
    from tests.k10_v320_repair_fixture import build_repair_fixture
    from tests.k10_v320_fixture import create_app
    db=tmp_path/'mixed.sqlite';build_repair_fixture(db)
    _freeze_k10_clocks(monkeypatch,'2026-09-09T09:35:00+08:00')
    with TestClient(create_app(db)) as client:
        windows=client.get('/api/v1/k10/company-windows').json()['items']
        same=[row for row in windows if row['companyCode']=='300002.SZ']
        closed=next(row for row in same if not row['canSelect'])
        active=next(row for row in same if row['canSelect'])
        for row in (closed,active):
            route='/api/v1/k10/company-windows/'+row['companyWindowId']+'/selection'
            response=client.post(route,json={'action':'skip','idempotencyKey':'scope-skip-'+row['companyWindowId']})
            assert response.status_code==200,response.text
            assert client.post(route,json={'action':'restore','idempotencyKey':'scope-restore-'+row['companyWindowId']}).status_code==(200 if row['canSelect'] else 409)
            assert client.post(route,json={'action':'keep','idempotencyKey':'scope-keep-'+row['companyWindowId']}).status_code==(200 if row['canSelect'] else 409)
        expired=next(row for row in windows if any(o['lifecycle']=='expired' for o in row['opportunities']))
        assert expired['canSelect'] is False
        assert client.post('/api/v1/k10/company-windows/'+expired['companyWindowId']+'/selection',json={'action':'restore','idempotencyKey':'expired-restore'}).status_code==409
