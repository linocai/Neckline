from datetime import timedelta
from dataclasses import replace
from neckline.k10 import pipeline, store, investigation
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.worker import run_once
from tests.test_v310_pipeline_e2e import _run, _http_transport, _api, RUN_AT


def test_semantic_reference_error_uses_bounded_repair_before_completed_cache(tmp_path, monkeypatch):
    original=pipeline.DeepSeekDiscoveryModel.advance_research
    count=[0]
    def respond(self, **kwargs):
        if kwargs['action']=='assess_evidence':
            count[0]+=1
            if count[0]==2:
                errors=self._thread_usage.repair_feedback['validationErrors']
                assert errors[0]['expected']=='investigation_reference_invalid'
        result=original(self,**kwargs)
        if kwargs['action']=='assess_evidence' and count[0]==1:
            result=replace(result,evidence_updates=({'claimId':kwargs['evidence_packet']['claims'][0]['claimId'],
                'sourceRef':{'documentId':'not-provided','revision':1},'relation':'supports','location':'excerpt','applicability':{}},))
        return result
    monkeypatch.setattr(pipeline.DeepSeekDiscoveryModel,'advance_research',respond)
    db,task_id,task,calls,gateway=_run(tmp_path,monkeypatch)
    assert task.status=='completed' and calls.count('research:assess_evidence')==3
    assert len(gateway.search_paths)==2 and calls.count('understand')==1


def test_pending_comparison_is_repaired_before_persistence_and_never_offered(tmp_path, monkeypatch):
    db,task_id,task,calls,gateway=_run(tmp_path,monkeypatch,pending_ranking='repair')
    assert task.status=='completed'
    assert calls.count('research:compare_companies')==2
    assert len(gateway.search_paths)==1
    scan=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['scanId']
    assert not store.list_candidates(scan_id=scan,state='offered',db_path=db)
    with _api(db) as client:
        rows=client.get('/api/v1/k10/scans/'+scan+'/assessments').json()['items']
    assert len(rows)==3 and {r['role'] for r in rows}=={'pending','excluded'}


def test_legacy_saved_primary_under_pending_closure_recovers_without_source_replay(tmp_path,monkeypatch):
    original=investigation._require_result
    def old_validation(action,result,packet):
        original(action,result,{**packet,'publicationAllowed':True})
    monkeypatch.setattr(investigation,'_require_result',old_validation)
    db,task_id,first,_,gateway=_run(tmp_path,monkeypatch,pending_ranking='legacy_wrong')
    assert first.status=='failed'
    monkeypatch.setattr(investigation,'_require_result',original)
    scan=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['scanId']
    recover_scan(db_path=db,scan_id=scan,execution_config_id='b39-execution',execution_config_revision=1,
        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan,db_path=db),now=RUN_AT)
    calls=_http_transport(monkeypatch,initial_query_round=1,pending_ranking='pending')
    task=run_once(db_path=db,worker_id='pending-repair',lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:RUN_AT)
    assert task.status=='completed'
    # The corrected comparison is followed by each company's first lifecycle
    # classification; no title, body, search plan or prior closure is replayed.
    assert calls==['research:compare_companies','classify','classify','classify']
    assert len(gateway.search_paths)==1
    with _api(db) as client:
        rows=client.get('/api/v1/k10/scans/'+scan+'/assessments').json()['items']
    assert len(rows)==3 and {r['role'] for r in rows}=={'pending','excluded'}
    assert not store.list_candidates(scan_id=scan,state='offered',db_path=db)
