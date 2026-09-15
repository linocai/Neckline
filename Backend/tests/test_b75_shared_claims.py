"""References to visible shared facts must survive validation and restart."""
import copy
import json
import sqlite3
from datetime import timedelta
import pytest
from neckline.k10 import research_runtime as runtime, store, pipeline
from neckline.k10.research_contracts import Claim, Question, ResearchStageResult
from neckline.k10.investigation import InvestigationError
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b60_pool_filtering import edit_responses
from tests.test_b61_output_recovery import api_for


def shared():
    return {'claimId':'shared-c1','text':'Current source says project remains in validation','kind':'factual_assertion','novelty':'new_fact',
            'verificationStatus':'verified','decisionImpact':'Confirm project stage','sourceRef':{'documentId':'d','revision':1},
            'location':'line:1','provenance':{'snapshotId':'peer','snapshotRevision':2}}


def test_registration_uses_visible_values_preserves_origin_and_does_not_invent_support():
    obj=object.__new__(runtime._Investigation);obj.state={'claims':[]}
    row=shared();packet={'reusableSourceEvidence':{'claims':[row]},'allowedEvidenceRefs':[row['sourceRef']]}
    question=Question.from_dict({'questionId':'q','claimIds':['shared-c1'],'question':'Confirm stage','priority':'critical',
        'knownEvidence':[row['sourceRef']],'companyCodes':['300002.SZ'],'state':'open','supportCondition':'Source confirms','refuteCondition':'Source refutes','decisionImpact':'Material fact','missingEvidence':['Project notice']})
    result=ResearchStageResult('plan_gaps',questions=(question,));before=copy.deepcopy(packet)
    bound=obj._bind_shared_claims(result,packet)
    assert bound.claims[0].claim_id=='shared-c1' and bound.claims[0].verification_status=='unverified'
    assert bound.claims[0].source_ref==row['sourceRef'] and bound.claims[0].location=='line:1'
    assert bound.conclusion['runtimeImportedSourceClaims'][0]['sharedFact']==row
    assert not bound.evidence_updates and result.claims==() and packet==before
    assert obj._bind_shared_claims(bound,packet)==bound
    packet['reusableSourceEvidence']['claims'].append({**row,'text':'Contradictory different fact'})
    assert not obj._bind_shared_claims(result,packet).claims
    packet['reusableSourceEvidence']['claims']=[row];packet['allowedEvidenceRefs']=[]
    assert not obj._bind_shared_claims(result,packet).claims


def test_real_cli_reuses_paid_gap_reply_then_persists_shared_fact_and_readable_report(tmp_path,monkeypatch):
    prior=runtime.load_prior_research_evidence
    def peer(**kw):
        value=prior(**kw)
        with sqlite3.connect(kw['db_path']) as c:
            row=c.execute('select claim_json from k10_research_claims order by snapshot_revision limit 1').fetchone()
        if row:
            fact=json.loads(row[0]);fact.update(claimId='shared-c1',text=fact['text']+' Source also states the validation stage.',
                provenance={'snapshotId':'deterministic-peer','snapshotRevision':1})
            value['claims']=[fact]
        return value
    monkeypatch.setattr(runtime,'load_prior_research_evidence',peer)
    def edit(value):
        if value.get('action')=='plan_gaps':
            value['questions'][0]['claimIds'].append('shared-c1')
    edit_responses(monkeypatch,edit)
    bind=runtime._Investigation._bind_shared_claims
    with monkeypatch.context() as old:
        old.setattr(runtime._Investigation,'_bind_shared_claims',lambda self,result,packet:result)
        db,tid,failed,calls,_=e2e._run(tmp_path,monkeypatch,v2=True,cli_entry=True)
    assert failed.status=='failed'
    before=store.task_execution_input(task_id=tid,db_path=db);scan=before['checkpoint']['scanId']
    frozen=frozen_scan_input_sha256(scan_id=scan,db_path=db)
    with sqlite3.connect(db) as c:
        completed=c.execute("select * from k10_execution_item_checkpoints where task_id=? and status='completed'",(tid,)).fetchall()
        paid=c.execute('select * from k10_external_attempts where task_id=?',(tid,)).fetchall()
    resumed=e2e._http_transport(monkeypatch,v2=True)
    assert recover_scan(db_path=db,scan_id=scan,execution_config_id='b39-execution',execution_config_revision=1,
        confirmed_input_sha256=frozen,now=e2e.RUN_AT)==tid
    result=run_once(db_path=db,task_id=tid,worker_id='b75',lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:e2e.RUN_AT)
    assert result.status=='completed'
    assert 'research:plan_gaps' not in resumed and 'titleBatch' not in resumed and 'understand' not in resumed
    with sqlite3.connect(db) as c:
        assert all(r in c.execute('select * from k10_execution_item_checkpoints where task_id=?',(tid,)).fetchall() for r in completed)
        assert all(r in c.execute('select * from k10_external_attempts where task_id=?',(tid,)).fetchall() for r in paid)
        claim=c.execute("select claim_json from k10_research_claims where claim_id='shared-c1' order by snapshot_revision limit 1").fetchone()
        assert claim and json.loads(claim[0])['sourceRef']
        questions=[json.loads(r[0]) for r in c.execute('select question_json from k10_research_questions')]
        assert any('shared-c1' in q['claimIds'] for q in questions)
    assert api_for(db).get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']['status']=='completed'
    assert frozen_scan_input_sha256(scan_id=scan,db_path=db)==frozen
    assert store.task_execution_input(task_id=tid,db_path=db)['checkpoint']['executionStartedAt']==before['checkpoint']['executionStartedAt']


def test_shared_id_collisions_are_namespaced_before_model_sees_them():
    from neckline.k10.research_context import project_packet
    from tests.test_b75_navigation import packet
    p=packet();ref=p['allowedEvidenceRefs'][0]
    own={**shared(),'sourceRef':ref}
    p['claims']=[own]
    p['reusableSourceEvidence']={'claims':[{**own,'text':'Different shared fact'},
        {**own,'claimId':'duplicate','text':'First peer'}, {**own,'claimId':'duplicate','text':'Second peer'}]}
    projected=project_packet('plan_gaps',p)
    ids=[row['claimId'] for row in projected['reusableSourceEvidence']['claims']]
    assert len(ids)==len(set(ids))==3 and all(i.startswith('shared_') for i in ids)
    assert own['claimId'] not in ids and project_packet('plan_gaps',projected)==projected
