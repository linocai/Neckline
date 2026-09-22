import json
import sqlite3
import shutil
from datetime import timedelta

from tests import test_v310_pipeline_e2e as e2e
from tests.test_b60_pool_filtering import edit_responses
from neckline.k10 import store
from neckline.k10.v2_store import read_report
from neckline.k10 import title_runtime, pipeline
from neckline.k10.title_triage import TitleTriageProtocolError
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.worker import run_once


def test_complete_json_with_bad_review_count_is_accepted_without_a_paid_repair(tmp_path, monkeypatch):
    changed=False
    def edit(value):
        nonlocal changed
        if 'selectionComplete' in value and not changed:
            value['reviewedCount'] += 100
            changed=True
    edit_responses(monkeypatch,edit)
    db,task_id,task,calls,_=e2e._run(tmp_path,monkeypatch,v2=True)
    assert task.status=='completed'
    assert calls.count('titleBatch')==1 and calls.count('titleGlobal')==1
    artifacts=list((tmp_path/'model-diagnostics').rglob('*.json'))
    assert artifacts == []
    assert read_report(db_path=db)['eveningCards']


def test_legacy_review_count_is_not_an_additional_title_global_call(tmp_path,monkeypatch):
    index=0
    def edit(value):
        nonlocal index
        if 'selectionComplete' in value:
            index+=1
            value['reviewedCount']=100+index
    edit_responses(monkeypatch,edit)
    db,task_id,task,calls,_=e2e._run(tmp_path,monkeypatch,v2=True)
    assert task.status=='completed' and calls.count('titleGlobal')==1
    rows=[json.loads(p.read_text()) for p in (tmp_path/'model-diagnostics').rglob('*.json')]
    assert rows == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:titleReconcile' AND status='completed'",(task_id,)).fetchone()[0]==1


def test_authorized_recovery_revalidates_saved_response_before_spending_again(tmp_path,monkeypatch):
    original=title_runtime.normalize_reconcile_result
    def old_bug(*args,**kwargs):
        raise TitleTriageProtocolError('全局标题旧校验误判')
    monkeypatch.setattr(title_runtime,'normalize_reconcile_result',old_bug)
    db,task_id,task,calls,_=e2e._run(tmp_path,monkeypatch,v2=True)
    assert task.status=='failed' and calls.count('titleGlobal')==2
    monkeypatch.setattr(title_runtime,'normalize_reconcile_result',original)
    # B82 must recover from the immutable SQLite receipt, never a private
    # ``model-diagnostics`` sidecar.  Delete the entire optional directory
    # before the real recovery producer/worker entry point runs.
    shutil.rmtree(tmp_path / 'model-diagnostics', ignore_errors=True)
    scan_id=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['scanId']
    assert recover_scan(db_path=db,scan_id=scan_id,execution_config_id='b39-execution',execution_config_revision=1,
        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan_id,db_path=db),now=e2e.RUN_AT)==task_id
    resumed=e2e._http_transport(monkeypatch,v2=True)
    done=run_once(db_path=db,task_id=task_id,worker_id='b62',lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:e2e.RUN_AT)
    assert done.status=='completed' and 'titleBatch' not in resumed and 'titleGlobal' not in resumed
    assert read_report(db_path=db)['eveningCards']
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM k10_model_response_receipts WHERE task_id=?", (task_id,)).fetchone()[0] >= 1


def test_authorized_title_recovery_rejects_wrong_scope_without_a_new_post(tmp_path, monkeypatch):
    """A same-item title receipt with a different scope cannot be borrowed."""
    original = title_runtime.normalize_reconcile_result
    monkeypatch.setattr(title_runtime, "normalize_reconcile_result",
                        lambda *args, **kwargs: (_ for _ in ()).throw(TitleTriageProtocolError("旧全局校验误判")))
    db, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    assert task.status == "failed" and calls.count("titleGlobal") == 2
    monkeypatch.setattr(title_runtime, "normalize_reconcile_result", original)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE k10_model_response_receipts SET reuse_scope_sha256=? WHERE task_id=? AND stage='titleReconcile'",
                     ("0" * 64, task_id))
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    assert recover_scan(db_path=db, scan_id=scan_id, execution_config_id="b39-execution", execution_config_revision=1,
                        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan_id, db_path=db), now=e2e.RUN_AT) == task_id
    resumed = e2e._http_transport(monkeypatch, v2=True)
    done = run_once(db_path=db, task_id=task_id, worker_id="b82-title-scope", lease_for=timedelta(minutes=5),
                    handlers=pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path / "parquet"),
                    clock=lambda: e2e.RUN_AT)
    assert done.status == "failed"
    assert "titleGlobal" not in resumed
