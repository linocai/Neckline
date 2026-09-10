from datetime import datetime,timedelta
from types import SimpleNamespace
import sqlite3
from neckline.k10 import pipeline,store,research_runtime
from neckline.k10.worker import run_once
from tests.test_v310_pipeline_e2e import _run


def test_resume_after_assessment_persistence_closes_before_another_search(tmp_path,monkeypatch):
    tick=[0.0];triggered=[False]
    monkeypatch.setattr(pipeline,'time',SimpleNamespace(monotonic=lambda:tick[0]))
    original=research_runtime._Investigation._record
    def persist_then_expire(self,result,**kwargs):
        original(self,result,**kwargs)
        if result.action=='assess_evidence' and not (result.conclusion or {}).get('runtimeEvidence') and not triggered[0]:
            triggered[0]=True;tick[0]=10_000.0
    monkeypatch.setattr(research_runtime._Investigation,'_record',persist_then_expire)
    db,task_id,first,calls,gateway=_run(tmp_path,monkeypatch)
    assert first.status=='queued' and calls.count('research:assess_evidence')==1
    boundary=len(calls);before=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['executionStartedAt']
    tick[0]=0
    with sqlite3.connect(db) as conn:next_run=datetime.fromisoformat(conn.execute('select not_before_at from k10_task_retry_schedules where task_id=?',(task_id,)).fetchone()[0])
    done=run_once(db_path=db,worker_id='phase-resume',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:next_run+timedelta(seconds=1))
    assert done.status=='completed'
    assert calls[boundary]=='research:close_research'
    assert len(gateway.search_paths)==2 and calls.count('research:assess_evidence')==2
    assert calls.count('understand')==1
    assert store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['executionStartedAt']==before
