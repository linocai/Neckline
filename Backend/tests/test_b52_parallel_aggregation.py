import pytest
from tests import k10_v306_fixture
from tests.test_v310_pipeline_e2e import _run,_api
from neckline.k10 import store


def test_six_way_research_preserves_candidate_pending_and_excluded_collections(tmp_path,monkeypatch):
    old=k10_v306_fixture.execution_payload
    def parallel(**kwargs):
        policy,payload=old(**kwargs)
        payload['discovery']['deepReadConcurrency']=6
        return policy,payload
    monkeypatch.setattr(k10_v306_fixture,'execution_payload',parallel)
    db,task_id,task,calls,gateway=_run(tmp_path,monkeypatch)
    assert task.status=='completed'
    scan=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['scanId']
    with _api(db) as client:
        response=client.get('/api/v1/k10/scans/'+scan+'/assessments').json()
    assert {row['role'] for row in response['items']}=={'primary','pending','excluded'}
    assert len(store.list_candidates(scan_id=scan,state='offered',db_path=db))==1
    assert calls.count('understand')==1 and len(gateway.search_paths)==2


@pytest.mark.parametrize("paused_operation", ["classify", "prioritize"])
def test_paused_finalization_recovers_without_repeating_completed_research(tmp_path,monkeypatch,paused_operation):
    from datetime import timedelta
    from neckline.k10 import pipeline
    from neckline.k10.cli import recover_scan,frozen_scan_input_sha256
    from neckline.k10.worker import run_once
    from tests.test_v310_pipeline_e2e import RUN_AT,_http_transport
    original=pipeline._CheckpointedDiscoveryModel._run
    stopped=[False]
    def pause_before_classification(self,**kwargs):
        if kwargs['operation']==paused_operation and not stopped[0]:
            stopped[0]=True
            store.set_run_control(state='closed',reason_code='fixture',changed_at=RUN_AT.isoformat(),changed_by='test',db_path=self._db_path)
        return original(self,**kwargs)
    monkeypatch.setattr(pipeline._CheckpointedDiscoveryModel,'_run',pause_before_classification)
    db,task_id,first,calls,gateway=_run(tmp_path,monkeypatch)
    assert stopped[0]
    import sqlite3
    with sqlite3.connect(db) as conn:
        rows=conn.execute("SELECT stage,status,safe_error_code FROM k10_execution_item_checkpoints WHERE stage=?", ("model:"+paused_operation,)).fetchall()
    assert first.status=='failed'
    assert ('model:'+paused_operation,'failed','execution_paused') in rows
    scan=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['scanId']
    with _api(db) as client:
        assert client.get('/api/v1/k10/scans/'+scan).json()['status']=='failed'
    assert not store.list_candidates(scan_id=scan,state='offered',db_path=db)
    store.set_run_control(state='open',reason_code='fixture-recover',changed_at=RUN_AT.isoformat(),changed_by='test',db_path=db)
    recover_scan(db_path=db,scan_id=scan,execution_config_id='b39-execution',execution_config_revision=1,
        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan,db_path=db),now=RUN_AT)
    calls=_http_transport(monkeypatch)
    done=run_once(db_path=db,worker_id='classify-resume',lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:RUN_AT)
    assert done.status=='completed'
    assert calls==(['classify','classify','classify','prioritize'] if paused_operation=='classify' else ['prioritize'])
    assert len(gateway.search_paths)==2
    assert len(store.list_candidates(scan_id=scan,state='offered',db_path=db))==1
