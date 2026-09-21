"""References to visible shared facts must survive validation and restart."""
import json
import sqlite3
import pytest
from neckline.k10 import research_runtime as runtime
from neckline.k10.research_contracts import Question, ResearchRoundResult
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b60_pool_filtering import edit_responses
from tests.test_b61_output_recovery import api_for


def shared():
    return {'claimId':'shared-c1','text':'Current source says project remains in validation','kind':'factual_assertion','novelty':'new_fact',
            'verificationStatus':'verified','decisionImpact':'Confirm project stage','sourceRef':{'documentId':'d','revision':1},
            'location':'line:1','provenance':{'snapshotId':'peer','snapshotRevision':2}}


def test_registration_uses_visible_values_preserves_origin_and_does_not_invent_support():
    row=shared();packet={'reusableSourceEvidence':{'claims':[row]},'allowedEvidenceRefs':[row['sourceRef']]}
    question=Question.from_dict({'questionId':'q','claimIds':['shared-c1'],'question':'Confirm stage','priority':'critical',
        'knownEvidence':[row['sourceRef']],'companyCodes':['300002.SZ'],'state':'open','supportCondition':'Source confirms','refuteCondition':'Source refutes','decisionImpact':'Material fact','missingEvidence':['Project notice']})
    result=ResearchRoundResult(questions=(question,),conclusion={'researchStatus':'continue_research',
        'companyMappings':[],'stopReason':'Need a company notice','resumeCondition':'New notice'})

    bound=runtime._b78_visible_shared_claims(packet)
    runtime.validate_research_round_result(result=result,evidence_packet=packet)

    assert bound[0].claim_id=='shared-c1' and bound[0].verification_status=='unverified'
    assert bound[0].source_ref==row['sourceRef'] and bound[0].location=='line:1'
    assert packet['reusableSourceEvidence']['claims'][0]['verificationStatus']=='verified'
    assert packet['reusableSourceEvidence']['claims'][0]['provenance']==row['provenance']
    packet['reusableSourceEvidence']['claims'].append({**row,'claimId':'shared-c1','text':'Contradictory different fact'})
    assert not runtime._b78_visible_shared_claims(packet)


def test_real_cli_binds_visible_shared_fact_in_compound_receipt_and_reads_report(tmp_path,monkeypatch):
    packets=[]
    def observe(request):
        body=json.loads(request.content); text=body['messages'][-1]['content']
        value=json.loads(text.split('<untrusted-k10-evidence>\n',1)[1].split('\n</untrusted-k10-evidence>',1)[0])
        if value.get('action')=='research_round': packets.append(value['evidencePacket'])
    db,tid,task,calls,_=e2e._run(tmp_path,monkeypatch,v2=True,cli_entry=True,
        news_adapter=e2e._FreshSharedNews,shared_same_source_events=True,request_observer=observe,
        close_status='pending_verification')
    assert task.status=='completed'
    assert calls.count('research:research_round') >= 2
    receiving=next(packet for packet in packets if packet['event']['canonicalKey']=='project-delivery-b')
    claim=receiving['reusableSourceEvidence']['claims'][0]
    assert claim['text']=='供应商称项目送样' and claim['verificationStatus']=='unverified'
    assert claim['sourceRef'] and claim['sourceTiming']['fetchedAt'] and claim['provenance']['snapshotId']
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT COUNT(*) FROM k10_research_round_results').fetchone()[0]>=2
        assert c.execute('SELECT COUNT(*) FROM k10_research_stage_results').fetchone()[0]==0
    assert api_for(db).get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']['status']=='completed'


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
