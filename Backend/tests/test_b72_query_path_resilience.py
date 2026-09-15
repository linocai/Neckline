"""Mixed cross-question optional routes must not invalidate usable research."""
import copy
import json
import sqlite3
from datetime import timedelta
from contextlib import contextmanager

import pytest
from neckline.k10 import investigation, pipeline, store
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b60_pool_filtering import edit_responses
from tests.test_b61_output_recovery import api_for


def path(name, question, targets):
    return {'pathId':name,'questionId':question,'purposeKind':'counterevidence','query':name,
        'intent':'Check current event','targetSource':'Company notice','newPathReason':'New necessary fact',
        'expectedInformationGain':'Confirm event link','expectedJudgmentChange':'Resolve uncertainty',
        'targetRefs':targets,'state':'planned','resultSummary':None}


def fixture():
    packet={'contextProtocol':'k10-v2-context-3.3.0-b70','openQuestionIds':['q1','q2'],
        '_localState':{'questions':[{'questionId':qid,'companyCodes':[code],'claimIds':['c1'],'state':'open'}
            for qid,code in [('q1','300001.SZ'),('q2','300002.SZ')]]}}
    valid=path('valid','q1',[{'kind':'company','companyCode':'300001.SZ'}])
    mixed=path('mixed','q1',[{'kind':'company','companyCode':'300001.SZ'},{'kind':'company','companyCode':'300002.SZ'},{'kind':'claim','claimId':'c1'}])
    return packet,{'action':'plan_queries','queryPaths':[valid,mixed]}


def test_only_redundant_cross_question_path_removed_without_mutating_paid_reply():
    packet,raw=fixture();before=copy.deepcopy(raw)
    result=investigation.decode_stage_result(raw,action='plan_queries',evidence_packet=packet)
    assert [p.path_id for p in result.query_paths]==['valid']
    assert result.conclusion['runtimeOutputSanitization']['discardedCrossQuestionPaths']==1
    assert raw==before


@pytest.mark.parametrize('mode',['unknown_claim','unknown_company','no_alternative','closed_question','supplied_scope','missing_target'])
def test_invalid_uncovered_or_unknown_routes_are_not_hidden(mode):
    packet,raw=fixture()
    if mode=='unknown_claim':raw['queryPaths'][1]['targetRefs'].append({'kind':'claim','claimId':'invented'})
    elif mode=='unknown_company':raw['queryPaths'][1]['targetRefs'][1]['companyCode']='999999.SZ'
    elif mode=='no_alternative':raw['queryPaths']=raw['queryPaths'][1:]
    elif mode=='closed_question':packet['_localState']['questions'][1]['state']='resolved'
    elif mode=='supplied_scope':raw['queryPaths'][1]['questionScope']={'scopeSha256':'model-invented'}
    elif mode=='missing_target':raw['queryPaths'][1]['targetRefs']=[]
    if mode in {'supplied_scope','missing_target'}:
        with pytest.raises(investigation.InvestigationError):
            investigation.decode_stage_result(raw,action='plan_queries',evidence_packet=packet)
        return
    result=investigation.decode_stage_result(raw,action='plan_queries',evidence_packet=packet)
    assert len(result.query_paths)==len(raw['queryPaths'])
    from neckline.k10.research_runtime import _Investigation
    runtime=object.__new__(_Investigation)
    runtime.context_protocol=True
    runtime.state={'questions':packet['_localState']['questions']}
    runtime.context={'canonicalKey':'event','stageKey':'stage','eventState':'reported'}
    with pytest.raises(investigation.InvestigationError):
        runtime._bind_query_paths('plan_queries',result,packet)


def edit_mixed_plan(value):
    if value.get('action')=='plan_gaps':
        value['questions'].append({**copy.deepcopy(value['questions'][0]),'questionId':'q-2','companyCodes':['300004.SZ']})
    elif value.get('action')=='plan_queries':
        first=value['queryPaths'][0]
        other={**copy.deepcopy(first),'pathId':first['pathId']+'-other','questionId':'q-2','query':first['query']+' 二公司'}
        mixed={**copy.deepcopy(first),'pathId':first['pathId']+'-mixed','query':'Forbidden mixed search',
               'targetRefs':[{'kind':'company','companyCode':'300002.SZ'},{'kind':'company','companyCode':'300004.SZ'}]}
        value['queryPaths'] += [other,mixed]


@pytest.mark.parametrize('recover',[False,True])
def test_real_cli_worker_keeps_scoped_searches_and_reuses_paid_failed_plan(tmp_path,monkeypatch,recover):
    serial=[0]
    def edit(value):
        edit_mixed_plan(value)
        if value.get('action')=='plan_queries':
            serial[0]+=1
            for row in value['queryPaths']:
                row['pathId']+=f'-reply-{serial[0]}'
                row['query']+=f' {serial[0]}'
    edit_responses(monkeypatch,edit)
    prune=investigation._prune_cross_question_paths
    validation=pipeline._CheckpointedDiscoveryModel.research_validation
    if recover:
        monkeypatch.setattr(investigation,'_prune_cross_question_paths',lambda paths,packet:(paths,0))
        @contextmanager
        def legacy_validation(self,validator):
            yield
        monkeypatch.setattr(pipeline._CheckpointedDiscoveryModel,'research_validation',legacy_validation)
    db,tid,first,calls,gateway=e2e._run(tmp_path,monkeypatch,v2=True,cli_entry=True)
    if recover:
        assert first.status=='failed'
        with sqlite3.connect(db) as c:
            prior=c.execute("select * from k10_execution_item_checkpoints where task_id=? and stage='model:investigation_plan_queries' and safe_error_code='investigation_path_scope_invalid'",(tid,)).fetchall()
            completed=c.execute("select * from k10_execution_item_checkpoints where task_id=? and status='completed' order by item_key",(tid,)).fetchall()
        assert len(prior)==1 and prior[0][-1] is not None
        scan=store.task_execution_input(task_id=tid,db_path=db)['checkpoint']['scanId']
        digest=frozen_scan_input_sha256(scan_id=scan,db_path=db)
        assert recover_scan(db_path=db,scan_id=scan,execution_config_id='b39-execution',execution_config_revision=1,
            confirmed_input_sha256=digest,now=e2e.RUN_AT)==tid
        monkeypatch.setattr(investigation,'_prune_cross_question_paths',prune)
        monkeypatch.setattr(pipeline._CheckpointedDiscoveryModel,'research_validation',validation)
        resumed=e2e._http_transport(monkeypatch,v2=True)
        done=run_once(db_path=db,task_id=tid,worker_id='b72',lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:e2e.RUN_AT)
        assert done.status=='completed'
        # The first invalid plan was reused; only the genuinely new second round is paid.
        assert resumed.count('research:plan_queries')==1
        assert 'understand' not in resumed and 'titleBatch' not in resumed
        with sqlite3.connect(db) as c:
            assert c.execute("select * from k10_execution_item_checkpoints where task_id=? and stage='model:investigation_plan_queries' and safe_error_code='investigation_path_scope_invalid'",(tid,)).fetchall()==prior
            after=c.execute("select * from k10_execution_item_checkpoints where task_id=? and status='completed' order by item_key",(tid,)).fetchall()
            assert all(r in after for r in completed)
        assert frozen_scan_input_sha256(scan_id=scan,db_path=db)==digest
    else:assert first.status=='completed'
    assert gateway.search_paths and all('-mixed' not in name for name in gateway.search_paths)
    assert any('-other' in name for name in gateway.search_paths)
    report=api_for(db).get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']
    assert report['status']=='completed' and report['eveningCards']

    from neckline.k10.notifications import initialize_notifications_schema, enqueue_task_notification, dispatch_task_notifications, DeliveryResult, NotificationRetryPolicy
    initialize_notifications_schema(db)
    notification=enqueue_task_notification(task_id=tid,db_path=db,created_at=e2e.RUN_AT)
    delivered=[]
    def send(**kw):
        delivered.append(kw['token']);return DeliveryResult(ok=True)
    args=dict(db_path=db,list_device_tokens=lambda:('mac-fixture','ios-fixture'),delete_device=lambda _:False,
        sender=send,worker_id='b72-notify',now=e2e.RUN_AT,notification_id=notification.notification_id,
        retry_policy=NotificationRetryPolicy(timedelta(seconds=30),timedelta(minutes=15)))
    assert dispatch_task_notifications(**args)==1 and dispatch_task_notifications(**args)==0
    assert sorted(delivered)==['ios-fixture','mac-fixture']
