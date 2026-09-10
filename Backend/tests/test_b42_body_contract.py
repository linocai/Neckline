"""Body-field feedback and bounded recovery of the first formal report."""
from datetime import timedelta
import sqlite3

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


def test_second_explicit_recovery_can_repair_failed_recovery_without_automatic_extra_calls(tmp_path, monkeypatch):
    db, task_id, first, _, _ = _run(tmp_path, monkeypatch, body_impact="")
    assert first.status == "failed"
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    selected = store.read_title_selection_manifest(task_id=task_id, db_path=db)
    def recover():
        return recover_scan(db_path=db, scan_id=scan_id, execution_config_id="b39-execution", execution_config_revision=1,
                            confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan_id, db_path=db), now=RUN_AT)
    handlers = pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path / "parquet")
    recover()
    failed_calls = _http_transport(monkeypatch, body_impact="")
    second = run_once(db_path=db, worker_id="still-invalid", lease_for=timedelta(minutes=5), handlers=handlers, clock=lambda: RUN_AT)
    assert second.status == "failed" and failed_calls.count("understand") == 2
    with sqlite3.connect(db) as c:
        reason = c.execute("SELECT reason_code FROM k10_v2_article_admissions WHERE task_id=?", (task_id,)).fetchone()[0]
    assert reason == "model_json_repair_exhausted"
    # A normal worker tick has no new recovery grant and makes no paid calls.
    assert run_once(db_path=db, worker_id="no-grant", lease_for=timedelta(minutes=5), handlers=handlers, clock=lambda: RUN_AT) is None
    assert failed_calls.count("understand") == 2
    recover()
    calls = _http_transport(monkeypatch)
    done = run_once(db_path=db, worker_id="corrected", lease_for=timedelta(minutes=5), handlers=handlers, clock=lambda: RUN_AT)
    assert done.status == "completed" and calls.count("understand") == 1
    assert not ({"titleBatch", "titleGlobal", "titleReview"} & set(calls))
    assert store.read_title_selection_manifest(task_id=task_id, db_path=db) == selected
    assert len(store.list_candidates(scan_id=scan_id, state="offered", db_path=db)) == 1


def test_pause_after_body_checkpoint_can_recover_same_running_scan_without_new_call(tmp_path, monkeypatch):
    db, task_id, first, _, _ = _run(tmp_path, monkeypatch, body_impact="")
    assert first.status == "failed"
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    digest = frozen_scan_input_sha256(scan_id=scan_id, db_path=db)
    recover = lambda: recover_scan(db_path=db, scan_id=scan_id, execution_config_id="b39-execution", execution_config_revision=1,
                                   confirmed_input_sha256=digest, now=RUN_AT)
    recover()
    calls = _http_transport(monkeypatch)
    original = pipeline._CheckpointedDiscoveryModel.understand
    def pause_after_response(self, **kwargs):
        original(self, **kwargs)
        store.set_run_control(state="closed", reason_code="fixture", changed_at=RUN_AT.isoformat(), changed_by="test", db_path=db)
        raise pipeline.DiscoverySliceYield()
    monkeypatch.setattr(pipeline._CheckpointedDiscoveryModel, "understand", pause_after_response)
    handlers = pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path / "parquet")
    paused = run_once(db_path=db, worker_id="pause", lease_for=timedelta(minutes=5), handlers=handlers, clock=lambda: RUN_AT)
    assert paused.status == "failed"
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT stage FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone() == ("paused",)
    assert store.get_scan(scan_id=scan_id, db_path=db)["status"] == "running"
    store.set_run_control(state="open", reason_code="fixture", changed_at=RUN_AT.isoformat(), changed_by="test", db_path=db)
    assert recover() == task_id
    monkeypatch.setattr(pipeline._CheckpointedDiscoveryModel, "understand", original)
    done = run_once(db_path=db, worker_id="resume", lease_for=timedelta(minutes=5), handlers=handlers, clock=lambda: RUN_AT)
    assert done.status == "completed" and calls.count("understand") == 1
    assert len(store.list_candidates(scan_id=scan_id, state="offered", db_path=db)) == 1
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT count(*) FROM k10_v2_article_admissions WHERE task_id=?", (task_id,)).fetchone() == (1,)


def test_failed_body_recovery_keeps_selection_and_retries_only_rejected_body(tmp_path, monkeypatch):
    db, task_id, first, _, _ = _run(tmp_path, monkeypatch, body_impact="")
    assert first.status == "failed"
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    selected = store.read_title_selection_manifest(task_id=task_id, db_path=db)
    assert recover_scan(db_path=db, scan_id=scan_id, execution_config_id="b39-execution", execution_config_revision=1,
        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan_id, db_path=db), now=RUN_AT) == task_id
    calls = _http_transport(monkeypatch)
    done = run_once(db_path=db, worker_id="body-recovery", lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path / "parquet"), clock=lambda: RUN_AT)
    assert done.status == "completed"
    assert not ({"titleBatch", "titleGlobal", "titleReview"} & set(calls))
    assert calls.count("understand") == 1
    assert store.read_title_selection_manifest(task_id=task_id, db_path=db) == selected
    assert len(store.list_candidates(scan_id=scan_id, state="offered", db_path=db)) == 1
