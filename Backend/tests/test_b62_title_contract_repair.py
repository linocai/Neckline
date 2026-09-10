import json
import sqlite3
from datetime import timedelta

from tests import test_v310_pipeline_e2e as e2e
from tests.test_b60_pool_filtering import edit_responses
from neckline.k10 import store
from neckline.k10.v2_store import read_report
from neckline.k10 import title_runtime, pipeline
from neckline.k10.title_triage import TitleTriageProtocolError
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.worker import run_once


def test_complete_json_with_bad_review_count_is_repaired_and_preserved_privately(tmp_path, monkeypatch):
    changed=False
    feedback=[]
    def edit(value):
        nonlocal changed
        if 'selectionComplete' in value and not changed:
            value['reviewedCount'] += 100
            changed=True
    def observe(request):
        text=json.loads(request.content)['messages'][-1]['content']
        if '上次输出未通过校验' in text:
            feedback.append(text)
    edit_responses(monkeypatch,edit)
    db,task_id,task,calls,_=e2e._run(tmp_path,monkeypatch,v2=True,request_observer=observe)
    assert task.status=='completed'
    assert calls.count('titleBatch')==1 and calls.count('titleGlobal')==2
    assert feedback and 'reviewedCount' in feedback[0]
    artifacts=list((tmp_path/'model-diagnostics').rglob('*.json'))
    assert len(artifacts)==1
    saved=json.loads(artifacts[0].read_text())
    assert saved['taskId']==task_id and saved['response']['reviewedCount']==101
    assert saved['errorCode']=='title_json_contract_invalid'
    assert artifacts[0].stat().st_mode & 0o777 == 0o600
    assert read_report(db_path=db)['eveningCards']


def test_repeated_bad_output_retains_each_distinct_paid_response(tmp_path,monkeypatch):
    index=0
    def edit(value):
        nonlocal index
        if 'selectionComplete' in value:
            index+=1
            value['reviewedCount']=100+index
    edit_responses(monkeypatch,edit)
    db,task_id,task,calls,_=e2e._run(tmp_path,monkeypatch,v2=True)
    assert task.status=='failed' and calls.count('titleGlobal')==2
    rows=[json.loads(p.read_text()) for p in (tmp_path/'model-diagnostics').rglob('*.json')]
    assert {r['response']['reviewedCount'] for r in rows}=={101,102}
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:titleReconcile' AND status='completed'",(task_id,)).fetchone()[0]==0


def test_authorized_recovery_revalidates_saved_response_before_spending_again(tmp_path,monkeypatch):
    original=title_runtime.normalize_reconcile_result
    def old_bug(*args,**kwargs):
        raise TitleTriageProtocolError('全局标题旧校验误判')
    monkeypatch.setattr(title_runtime,'normalize_reconcile_result',old_bug)
    db,task_id,task,calls,_=e2e._run(tmp_path,monkeypatch,v2=True)
    assert task.status=='failed' and calls.count('titleGlobal')==2
    monkeypatch.setattr(title_runtime,'normalize_reconcile_result',original)
    scan_id=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['scanId']
    assert recover_scan(db_path=db,scan_id=scan_id,execution_config_id='b39-execution',execution_config_revision=1,
        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan_id,db_path=db),now=e2e.RUN_AT)==task_id
    resumed=e2e._http_transport(monkeypatch,v2=True)
    done=run_once(db_path=db,task_id=task_id,worker_id='b62',lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:e2e.RUN_AT)
    assert done.status=='completed' and 'titleBatch' not in resumed and 'titleGlobal' not in resumed
    assert read_report(db_path=db)['eveningCards']
