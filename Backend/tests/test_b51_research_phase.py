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


def test_durable_fulltext_denial_closes_without_empty_assessment_or_repeat_extract(tmp_path,monkeypatch):
    from copy import deepcopy
    from tests.test_v310_pipeline_e2e import _Gateway
    from neckline.k10.verification import VerificationEvidenceBundle
    decode=pipeline.decode_stage_result; model=pipeline.DeepSeekDiscoveryModel.advance_research
    seen={'assess':0,'close':0,'extract':0}
    def denial(self,**kwargs):
        seen['extract']+=1
        return VerificationEvidenceBundle('pending',(),(),{'state':'pending','requestState':'completed',
            'operation':'extract','admissionState':'rejected','reason':'article_limit_reached'})
    def response(raw,*,action,evidence_packet=None):
        if action in ('assess_evidence','close_research'):
            key='assess' if action=='assess_evidence' else 'close';seen[key]+=1
            if seen[key]==1:
                raw=deepcopy(raw)
                raw['fulltextRequests']=[{'requestId':'request-'+key,'questionId':'q-1',
                    'sourceRef':evidence_packet['allowedEvidenceRefs'][0],
                    'reasonExcerptInsufficient':'缺原始条款','expectedJudgmentChange':'核对主体',
                    'state':'requested','admissionRef':None}]
        return decode(raw,action=action,evidence_packet=evidence_packet)
    def check(self,**kwargs):
        if kwargs['action']=='close_research' and seen['close']==1:
            assert self._thread_usage.repair_feedback['validationErrors'][0]['expected']=='empty_array_when_article_limit_reached'
            from neckline.k10.investigation_prompts import request_spec
            instruction,_=request_spec(**kwargs)
            assert '剩余新全文名额为 0' in instruction
        return model(self,**kwargs)
    monkeypatch.setattr(_Gateway,'fetch_fulltext',denial)
    monkeypatch.setattr(pipeline,'decode_stage_result',response)
    monkeypatch.setattr(pipeline.DeepSeekDiscoveryModel,'advance_research',check)
    db,task_id,task,calls,gateway=_run(tmp_path,monkeypatch)
    assert task.status=='completed'
    assert seen['extract']==1
    assert calls.count('research:assess_evidence')==1 and calls.count('research:close_research')==2
    assert calls.count('understand')==1 and len(gateway.search_paths)==1
