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


def test_labeled_unverified_comparison_can_publish_without_extra_repair(tmp_path, monkeypatch):
    db,task_id,task,calls,gateway=_run(tmp_path,monkeypatch,pending_ranking='legacy_wrong')
    assert task.status=='completed'
    assert calls.count('research:compare_companies')==1
    assert len(gateway.search_paths)==1
    scan=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['scanId']
    assert store.list_candidates(scan_id=scan,state='offered',db_path=db)
    with _api(db) as client:
        rows=client.get('/api/v1/k10/scans/'+scan+'/assessments').json()['items']
    assert len(rows)==3
    assert any(row['role'] in {'primary','alternative','tied'} for row in rows)
