"""Real producer/worker regressions for truncated output and resumed status."""
from datetime import timedelta
import json
import sqlite3

import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e
from tests.k10_v320_fixture import create_app
from fastapi.testclient import TestClient


def api_for(db):
    return TestClient(create_app(db, config_id='b39', execution_config_id='b39-execution'))


@pytest.mark.parametrize('stage', ['titleGlobal', 'titleReview', 'understand'])
def test_output_truncation_uses_compact_repair_within_bound_policy(tmp_path, monkeypatch, stage):
    seen=[]
    def observe(request):
        wire=json.loads(request.content)
        if '上次达到输出长度限制' in wire['messages'][-1]['content']:
            seen.append(wire)
    db, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True,
        finalization_truncate=stage, request_observer=observe)
    assert task.status == 'completed'
    assert calls.count(stage) == 2
    assert seen and all(row['thinking']=={'type':'disabled'} and 'reasoning_effort' not in row for row in seen)
    assert api_for(db).get('/api/v1/k10/v2/reports/latest').json()['report']['eveningCards']


def test_legacy_truncated_recovery_preserves_titles_and_projects_current_status(tmp_path, monkeypatch):
    execute=pipeline.execute_model_operation
    with monkeypatch.context() as legacy:
        def old_terminal(**kwargs):
            original=kwargs['operation_call']
            def old_call():
                try:
                    return original()
                except pipeline.JsonRepairError as exc:
                    if kwargs['operation']=='titleReconcile' and 'truncated' in exc.code:
                        raise pipeline.SemanticValidationError(code='response_truncated', input_tokens=exc.input_tokens,
                            output_tokens=exc.output_tokens,total_tokens=exc.total_tokens) from exc
                    raise
            return execute(**{**kwargs,'operation_call':old_call})
        legacy.setattr(pipeline,'execute_model_operation',old_terminal)
        db, task_id, first, calls, _ = e2e._run(tmp_path, legacy, v2=True, finalization_truncate='titleGlobal')
        assert first.status=='failed' and calls==['titleBatch','titleGlobal']
        scan_id=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['scanId']
        with sqlite3.connect(db) as conn:
            before=conn.execute("SELECT * FROM k10_execution_item_checkpoints WHERE stage='model:titleBatch' AND task_id=?",(task_id,)).fetchall()
        assert api_for(db).get('/api/v1/k10/v2/reports/latest').json()['report']['status']=='failed'
        legacy.setattr(pipeline,'execute_model_operation',execute)
        assert recover_scan(db_path=db,scan_id=scan_id,execution_config_id='b39-execution',execution_config_revision=1,
            confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan_id,db_path=db),now=e2e.RUN_AT)==task_id
        api=api_for(db)
        pending=api.get('/api/v1/k10/v2/reports/latest').json()
        assert pending['report']['status']=='queued' and '没跑成' not in pending['reason']['message']
        observed=[]
        def observe(request):
            wire=json.loads(request.content)
            if not observed:
                assert wire['thinking']=={'type':'disabled'}
                current=api.get('/api/v1/k10/v2/reports/latest').json()
                assert current['report']['status']=='running'
                assert '没跑成' not in current['reason']['message']
            observed.append(wire)
        resumed=e2e._http_transport(legacy,v2=True,request_observer=observe)
        second=run_once(db_path=db,task_id=task_id,worker_id='b61',lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:e2e.RUN_AT)
        assert second.status=='completed' and 'titleBatch' not in resumed
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT * FROM k10_execution_item_checkpoints WHERE stage='model:titleBatch' AND task_id=?",(task_id,)).fetchall()==before
        complete=api.get('/api/v1/k10/v2/reports/latest').json()
        assert complete['reason'] is None and complete['report']['eveningCards']
