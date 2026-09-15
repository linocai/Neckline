"""Paid assessments may cite two locations for one claim/source relationship."""
import copy
import json
import sqlite3
from datetime import timedelta

import pytest
from neckline.k10 import research_store, store, pipeline
from neckline.k10.research_contracts import ResearchContractError, ResearchStageResult
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b60_pool_filtering import edit_responses
from tests.test_b61_output_recovery import api_for


def updates():
    first={'claimId':'c2','sourceRef':{'documentId':'doc','revision':1},'relation':'supports','location':'paragraph:11','applicability':{}}
    return [first,{**first,'location':'paragraph:15'}]


@pytest.mark.parametrize('field,value',[('relation','contradicts'),('applicability',{'companyCode':'300001.SZ'})])
def test_conflicting_same_source_relationship_is_not_silently_selected(field,value):
    rows=updates();rows[1][field]=value
    with pytest.raises(ResearchContractError,match='冲突'):
        ResearchStageResult('assess_evidence',evidence_updates=tuple(rows))


@pytest.mark.parametrize('recover',[False,True])
def test_real_cli_preserves_both_locations_and_reuses_paid_assessment(tmp_path,monkeypatch,recover):
    packet={}
    def observe(request):
        body=json.loads(request.content);s=body['messages'][-1]['content']
        data=json.loads(s.split('<untrusted-k10-evidence>\n',1)[1].split('\n</untrusted-k10-evidence>',1)[0])
        if data.get('action')=='assess_evidence':packet.update(data['evidencePacket'])
    def edit(value):
        if value.get('action')=='assess_evidence':
            value['evidenceUpdates']=[{**r,'claimId':packet['claims'][0]['claimId'],'sourceRef':packet['allowedEvidenceRefs'][0]} for r in updates()]
    from neckline.k10.discovery import DiscoveryDocument
    from neckline.k10.verification import VerificationEvidenceBundle
    from hashlib import sha256
    def fetch(gateway, **kw):
        path=kw['query_path'];gateway.search_paths.append(path.path_id)
        name='source-'+path.path_id
        text='公司公告说明同一事项的两个有效证据段落。'
        stamp=e2e.RUN_AT.isoformat();published=(kw['cutoff_at']-timedelta(minutes=1)).isoformat()
        store.append_document_version(document_id=name,source_key='fixture-search',external_id=name,canonical_url=None,
            content_sha256=sha256(text.encode()).hexdigest(),published_at=published,published_precision='exact',fetched_at=stamp,
            original_text=text,excerpt=text,fetch_version='fixture',metadata={},created_at=stamp,db_path=tmp_path/'b39-e2e.sqlite')
        doc=DiscoveryDocument(name,1,published,stamp,text,text,{})
        return VerificationEvidenceBundle('available',(doc,),(doc,),{'state':'available','requestState':'completed'})
    monkeypatch.setattr(e2e._Gateway,'fetch',fetch)
    edit_responses(monkeypatch,edit)
    group=research_store.group_evidence_updates
    if recover:monkeypatch.setattr(research_store,'group_evidence_updates',lambda rows:[[r] for r in rows])
    db,tid,task,calls,gateway=e2e._run(tmp_path,monkeypatch,v2=True,cli_entry=True,request_observer=observe)
    if recover:
        assert task.status=='failed'
        with sqlite3.connect(db) as c:
            completed=c.execute("select * from k10_execution_item_checkpoints where task_id=? and stage='model:investigation_assess_evidence' and status='completed'",(tid,)).fetchall()
        assert len(completed)==1
        scan=store.task_execution_input(task_id=tid,db_path=db)['checkpoint']['scanId']
        frozen=frozen_scan_input_sha256(scan_id=scan,db_path=db)
        monkeypatch.setattr(research_store,'group_evidence_updates',group)
        assert recover_scan(db_path=db,scan_id=scan,execution_config_id='b39-execution',execution_config_revision=1,confirmed_input_sha256=frozen,now=e2e.RUN_AT)==tid
        resumed=e2e._http_transport(monkeypatch,v2=True,request_observer=observe,initial_query_round=1)
        task=run_once(db_path=db,task_id=tid,worker_id='b73',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:e2e.RUN_AT)
        assert resumed.count('research:assess_evidence')==1
        assert 'understand' not in resumed and 'titleBatch' not in resumed
        with sqlite3.connect(db) as c:
            after=c.execute("select * from k10_execution_item_checkpoints where task_id=? and stage='model:investigation_assess_evidence' and status='completed'",(tid,)).fetchall()
        assert all(r in after for r in completed)
        assert frozen_scan_input_sha256(scan_id=scan,db_path=db)==frozen
    assert task.status=='completed'
    with sqlite3.connect(db) as c:
        sid=c.execute('select snapshot_id from k10_research_snapshot_revisions where task_id=? limit 1',(tid,)).fetchone()[0]
        raw=[json.loads(r[0]) for r in c.execute("select result_json from k10_research_stage_results where snapshot_id=? and action='assess_evidence'",(sid,))]
        assert any(len(r['evidenceUpdates'])==2 for r in raw)
        assert not c.execute('select 1 from k10_research_evidence_links group by snapshot_id,snapshot_revision,claim_id,document_id,document_revision having count(*)>1').fetchone()
    state=research_store.read_research_state(snapshot_id=sid,db_path=db)
    assert [r['location'] for r in state['evidenceUpdates']]==['paragraph:11','paragraph:15']
    report=api_for(db).get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']
    assert report['status']=='completed' and report['availableAt']


def test_mid_run_pause_does_not_push_failure_and_same_task_later_pushes_report(tmp_path,monkeypatch):
    from neckline.k10.notifications import (initialize_notifications_schema, enqueue_task_notification,
        dispatch_task_notifications, DeliveryResult, NotificationRetryPolicy, NotificationConflict)
    from neckline.k10.notification_runtime import reconcile_terminal_notifications
    paused=[False]
    def observe(request):
        if not paused[0]:
            paused[0]=True
            store.set_run_control(state='closed',reason_code='b73-release-pause',changed_at=e2e.RUN_AT.isoformat(),changed_by='fixture',db_path=tmp_path/'b39-e2e.sqlite')
    db,tid,task,calls,gateway=e2e._run(tmp_path,monkeypatch,v2=True,cli_entry=True,request_observer=observe)
    assert task.status=='failed'
    with sqlite3.connect(db) as c:assert c.execute('select stage from k10_tasks where task_id=?',(tid,)).fetchone()==('paused',)
    initialize_notifications_schema(db)
    assert reconcile_terminal_notifications(db_path=db,now=e2e.RUN_AT)==0
    with pytest.raises(NotificationConflict,match='受控暂停'):
        enqueue_task_notification(task_id=tid,db_path=db,created_at=e2e.RUN_AT)
    with sqlite3.connect(db) as c:assert c.execute('select count(*) from k10_task_notifications').fetchone()==(0,)
    store.set_run_control(state='open',reason_code='fixture-resume',changed_at=e2e.RUN_AT.isoformat(),changed_by='fixture',db_path=db)
    scan=store.task_execution_input(task_id=tid,db_path=db)['checkpoint']['scanId']
    assert recover_scan(db_path=db,scan_id=scan,execution_config_id='b39-execution',execution_config_revision=1,confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan,db_path=db),now=e2e.RUN_AT)==tid
    e2e._http_transport(monkeypatch,v2=True)
    done=run_once(db_path=db,task_id=tid,worker_id='b73-resume',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:e2e.RUN_AT)
    assert done.status=='completed'
    assert reconcile_terminal_notifications(db_path=db,now=e2e.RUN_AT)==1
    sent=[]
    args=dict(db_path=db,list_device_tokens=lambda:('ios-fixture','mac-fixture'),delete_device=lambda _:False,
        sender=lambda **kw:(sent.append(kw['token']) or DeliveryResult(ok=True)),worker_id='b73-push',now=e2e.RUN_AT,
        retry_policy=NotificationRetryPolicy(timedelta(seconds=30),timedelta(minutes=15)))
    assert dispatch_task_notifications(**args)==1 and dispatch_task_notifications(**args)==0
    assert sorted(sent)==['ios-fixture','mac-fixture']
    assert api_for(db).get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']['status']=='completed'
