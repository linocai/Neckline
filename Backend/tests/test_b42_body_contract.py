"""Body-field feedback and bounded recovery of the first formal report."""
from datetime import timedelta
import sqlite3

import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
from neckline.k10.worker import run_once
from tests.test_v310_pipeline_e2e import _run, _http_transport, RUN_AT


def test_blank_impact_gets_specific_field_feedback_and_repairs_before_publication(tmp_path, monkeypatch):
    db, task_id, task, calls, _ = _run(tmp_path, monkeypatch, body_impact="repair")
    assert task.status == "completed"
    assert calls.count("understand") == 2
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    assert len(store.list_candidates(scan_id=scan_id, state="offered", db_path=db)) == 1
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT count(*) FROM k10_v2_article_admissions WHERE task_id=?", (task_id,)).fetchone() == (1,)


def test_null_facts_gets_exact_field_feedback_and_repair(tmp_path, monkeypatch):
    db, task_id, task, calls, _ = _run(tmp_path, monkeypatch, body_impact="repair_facts")
    assert task.status == "completed" and calls.count("understand") == 2


def test_settled_body_failure_publishes_gap_without_reopening_paid_input(tmp_path, monkeypatch):
    db, task_id, first, first_calls, _ = _run(tmp_path, monkeypatch, body_impact="")
    assert first.status == "completed" and first_calls.count("understand") == 2
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    selected = store.read_title_selection_manifest(task_id=task_id, db_path=db)
    with sqlite3.connect(db) as conn:
        admission = conn.execute("SELECT state,reason_code FROM k10_v2_article_admissions WHERE task_id=?", (task_id,)).fetchone()
    assert admission == ("failed", "model_json_repair_exhausted")
    assert not store.list_candidates(scan_id=scan_id, state="offered", db_path=db)
    calls = _http_transport(monkeypatch)
    with pytest.raises(RuntimeError):
        recover_scan(db_path=db, scan_id=scan_id, execution_config_id="b39-execution", execution_config_revision=1,
            confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan_id, db_path=db), now=RUN_AT)
    assert run_once(db_path=db, worker_id="no-repeat", lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path / "parquet", now=lambda: RUN_AT),
        clock=lambda: RUN_AT) is None
    assert calls == [] and store.read_title_selection_manifest(task_id=task_id, db_path=db) == selected


def test_pause_after_paid_body_checkpoint_recovers_without_new_body_call(tmp_path, monkeypatch):
    original = pipeline._CheckpointedDiscoveryModel.understand
    did_pause = False
    def pause_after_response(self, **kwargs):
        nonlocal did_pause
        result = original(self, **kwargs)
        if not did_pause:
            did_pause = True
            store.set_run_control(state="closed", reason_code="fixture", changed_at=RUN_AT.isoformat(),
                changed_by="test", db_path=tmp_path / "b39-e2e.sqlite")
            raise pipeline.DiscoverySliceYield()
        return result
    monkeypatch.setattr(pipeline._CheckpointedDiscoveryModel, "understand", pause_after_response)
    db, task_id, first, calls, _ = _run(tmp_path, monkeypatch)
    assert did_pause and first.status == "failed"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT stage FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone() == ("paused",)
    assert calls.count("understand") == 1
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    selected = store.read_title_selection_manifest(task_id=task_id, db_path=db)
    assert store.get_scan(scan_id=scan_id, db_path=db)["status"] == "running"
    store.set_run_control(state="open", reason_code="fixture", changed_at=RUN_AT.isoformat(), changed_by="test", db_path=db)
    assert recover_scan(db_path=db, scan_id=scan_id, execution_config_id="b39-execution", execution_config_revision=1,
        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan_id, db_path=db), now=RUN_AT) == task_id
    monkeypatch.setattr(pipeline._CheckpointedDiscoveryModel, "understand", original)
    resumed_calls = _http_transport(monkeypatch)
    done = run_once(db_path=db, worker_id="resume", lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path / "parquet", now=lambda: RUN_AT),
        clock=lambda: RUN_AT)
    assert done.status == "completed" and resumed_calls.count("understand") == 0
    assert not ({"titleBatch", "titleGlobal", "titleReview"} & set(resumed_calls))
    assert store.read_title_selection_manifest(task_id=task_id, db_path=db) == selected
    assert len(store.list_candidates(scan_id=scan_id, state="offered", db_path=db)) == 1
