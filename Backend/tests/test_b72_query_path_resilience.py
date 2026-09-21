"""Mixed cross-question optional routes must not invalidate usable research."""
import copy
import json
import sqlite3
from datetime import timedelta
from contextlib import contextmanager

import pytest
from neckline.k10 import investigation, pipeline
from neckline.k10.research_contracts import Question
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
    assert result.conclusion['runtimeOutputSanitization']['discardedUnusableQueryPaths']==1
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
        for route in result.query_paths:
            row=next((q for q in packet['_localState']['questions'] if q['questionId']==route.question_id),None)
            question=Question.from_dict({**row,'question':'Confirm current event','knownEvidence':[],
                'missingEvidence':['Company notice'],'supportCondition':'Confirmed','refuteCondition':'Refuted',
                'decisionImpact':'Changes comparison','resumeCondition':'New notice'}) if row else None
            runtime._b78_bound_query_path(route,question)


def assert_failed_round_without_search(connection):
    rows = connection.execute(
        'SELECT r.result_json,r.tool_evidence_json,s.execution_status FROM k10_research_round_results r '
        'JOIN k10_research_snapshot_revisions s USING(snapshot_id,revision)'
    ).fetchall()
    assert len(rows) == 1
    result = json.loads(rows[0][0])
    assert result['safeErrorCode'] == 'investigation_path_scope_invalid'
    assert result['comparison'] is None and result['companyAssessments'] == []
    assert json.loads(rows[0][1]) == [] and rows[0][2] == 'failed'


def edit_mixed_plan(value):
    if value.get('action')!='research_round': return
    ref=value['conclusion']['companyMappings'][0]['relationEvidence'][0]
    questions=[{'questionId':qid,'claimIds':['article-claim-1'],'companyCodes':[code],
        'question':'Confirm current event '+qid,'knownEvidence':[ref],'missingEvidence':['Company notice'],
        'supportCondition':'Confirmed','refuteCondition':'Refuted','decisionImpact':'Changes comparison',
        'state':'open','resumeCondition':'New company notice'} for qid,code in [('q-1','300002.SZ'),('q-2','300004.SZ')]]
    first=path('path-1','q-1',[{'kind':'claim','claimId':'article-claim-1'}])
    other=path('path-1-other','q-2',[{'kind':'company','companyCode':'300004.SZ'}])
    mixed=path('path-1-mixed','q-1',[{'kind':'company','companyCode':'300002.SZ'},{'kind':'company','companyCode':'300004.SZ'}])
    value.clear(); value.update(action='research_round',questions=questions,queryPaths=[first,other,mixed],
        conclusion={'researchStatus':'continue_research','companyMappings':[],
            'stopReason':'Necessary event confirmation','resumeCondition':'New material'})


@pytest.mark.parametrize('force_invalid',[False,True])
def test_real_cli_worker_prunes_mixed_routes_and_isolates_a_paid_invalid_plan(tmp_path,monkeypatch,force_invalid):
    serial=[0]
    def edit(value):
        edit_mixed_plan(value)
        if value.get('action')=='research_round':
            serial[0]+=1
            for row in value['queryPaths']:
                row['pathId']+=f'-reply-{serial[0]}'
                row['query']+=f' {serial[0]}'
            if force_invalid:
                value['queryPaths'][-1]['targetRefs']=[{'kind':'claim','claimId':'unknown-claim'}]
    edit_responses(monkeypatch,edit)
    db,tid,first,calls,gateway=e2e._run(tmp_path,monkeypatch,v2=True,cli_entry=True)
    if force_invalid:
        # B76 turns an event-local contract refusal into an honest partial
        # delivery. The paid typed reply stays intact; its invalid scope is
        # rejected locally without publishing it or issuing another request.
        assert first.status=='completed'
        with sqlite3.connect(db) as c:
            prior=c.execute("select * from k10_execution_item_checkpoints where task_id=? and stage='model:investigation_research_round' and status='completed'",(tid,)).fetchall()
            assert len(prior)==1
            assert_failed_round_without_search(c)
        assert calls.count('research:research_round') == 1
        assert gateway.search_paths == []
    else:
        assert first.status=='completed'
        e2e.assert_search_routes(gateway, [('path-1 1','Company notice','q-1'), ('path-1-other 1','Company notice','q-2')])
        assert gateway.search_routes[1]['targetRefs'] == [{'kind':'company','companyCode':'300004.SZ'}]
    report=api_for(db).get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']
    if force_invalid:
        assert report['status']=='partial' and report['availableAt']
    else:
        assert report['status']=='completed' and report['availableAt']
        with sqlite3.connect(db) as c:
            assert {r[0] for r in c.execute('SELECT execution_status FROM k10_research_snapshot_revisions')}=={'ok'}

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


def test_real_cli_local_scope_refusal_is_not_a_search_result(tmp_path, monkeypatch):
    from neckline.k10.research_runtime import _Investigation
    def denied(*args, **kwargs):
        raise investigation.InvestigationError('scope changed',code='investigation_path_scope_invalid')
    monkeypatch.setattr(_Investigation,'_b78_bound_query_path',denied)
    edit_responses(monkeypatch,edit_mixed_plan)
    db,tid,task,calls,gateway=e2e._run(tmp_path,monkeypatch,v2=True,cli_entry=True)
    assert task.status=='completed' and gateway.search_paths==[]
    report=api_for(db).get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']
    assert report['status']=='partial' and report['availableAt'] and report['coverageGaps']
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM k10_external_attempts WHERE stage='search'").fetchone()==(0,)
        assert_failed_round_without_search(c)
    assert calls.count('research:research_round')==1
