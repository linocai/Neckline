"""Independent investigations overlap; durable action results survive slices."""
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace

from neckline.k10 import pipeline, store
from neckline.k10.discovery import DiscoveryDocument, EventDraft, InvestigationOutcome, Verification, run_discovery
from neckline.k10.worker import run_once
from tests.test_v310_pipeline_e2e import _run, RUN_AT


def test_independent_research_is_bounded_and_results_keep_input_order():
    cutoff = datetime(2026, 9, 8, 13, tzinfo=timezone.utc)
    documents = tuple(DiscoveryDocument(f"source-{i}", 1, cutoff.isoformat(), cutoff.isoformat(),
                                       f"unique body {i}", None, {"title": f"event {i}"}) for i in range(6))
    events = {d.document_id: EventDraft(d.document_id, "new", "reported", d.document_id, "fact", {}, (d.evidence_ref,)) for d in documents}
    class Model:
        def understand(self, *, document): return (events[document.document_id],)
    barrier, lock = Barrier(3), Lock()
    active = peak = completed = 0
    def investigate(event):
        nonlocal active, peak, completed
        with lock:
            active += 1; peak = max(peak, active)
        barrier.wait(timeout=5)
        with lock:
            active -= 1; completed += 1
        return InvestigationOutcome(Verification("needs_review", "background", event.source_refs, {"state": "available"}), (), None, event.canonical_key)
    config = json.loads((Path(__file__).parents[1] / "neckline/config/k10-v1.4.json").read_text())
    run = run_discovery(documents=documents, configuration=config, model=Model(), verify=lambda _: None,
                        metadata=None, cutoff_at=cutoff, investigate=investigate, investigation_concurrency=3)
    assert peak == 3 and active == 0 and completed == 6
    assert [event.canonical_key for event in run.events] == list(events)
    assert not run.issues


def test_truncated_assessment_gets_one_concise_delta_repair_without_repeating_search(tmp_path, monkeypatch):
    db, task_id, task, calls, gateway = _run(tmp_path, monkeypatch, truncate_action="assess_evidence")
    assert task.status == "completed"
    assert calls.count("research:assess_evidence") == 3
    assert len(gateway.search_paths) == 2
    assert calls.count("understand") == 1


def test_real_worker_reuses_paid_research_action_after_slice_before_snapshot_write(tmp_path, monkeypatch):
    tick = [0.0]
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(monotonic=lambda: tick[0]))
    original = pipeline._CheckpointedDiscoveryModel.advance_research
    triggered = [False]
    def complete_then_expire(self, **kwargs):
        result = original(self, **kwargs)
        if kwargs["action"] == "plan_gaps" and not triggered[0]:
            triggered[0] = True
            tick[0] = 10_000.0
        return result
    monkeypatch.setattr(pipeline._CheckpointedDiscoveryModel, "advance_research", complete_then_expire)
    db, task_id, first, calls, _ = _run(tmp_path, monkeypatch)
    assert first.status == "queued"
    assert calls.count("research:plan_gaps") == 1
    assert "research:plan_queries" not in calls
    before = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["executionStartedAt"]
    tick[0] = 0.0
    with sqlite3.connect(db) as conn:
        next_run = datetime.fromisoformat(conn.execute("SELECT not_before_at FROM k10_task_retry_schedules WHERE task_id=?", (task_id,)).fetchone()[0])
    done = run_once(db_path=db, worker_id="research-resume", lease_for=timedelta(minutes=5),
                    handlers=pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path / "parquet"),
                    clock=lambda: next_run + timedelta(seconds=1))
    assert done.status == "completed"
    assert calls.count("research:plan_gaps") == 1 and calls.count("understand") == 1
    assert store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["executionStartedAt"] == before
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    assert len(store.list_candidates(scan_id=scan_id, state="offered", db_path=db)) == 1
