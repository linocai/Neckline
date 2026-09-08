from datetime import datetime, timedelta, timezone
import json

import pytest

from neckline.k10 import store
from neckline.k10.schema import initialize_schema, read_connection
from neckline.k10.worker import TaskResult, run_once
from tests.k10_v306_fixture import append_approved_execution_profile


NOW = datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def task_db(tmp_path):
    path = tmp_path / "worker.sqlite"
    initialize_schema(path)
    store.set_run_control(state="open", reason_code="fixture_worker", changed_at=NOW.isoformat(), changed_by="test", db_path=path)
    return path


def enqueue(path, *, budget, kind="analysis", bind_execution=True):
    task = store.enqueue_task(
        task_id="task-1", kind=kind, idempotency_key=f"{kind}-1",
        input_version="synthetic-config-1", input_cutoff_at=NOW.isoformat(),
        payload={"observationId": "synthetic-observation"}, budget=budget,
        created_at=NOW.isoformat(), db_path=path,
    )
    if bind_execution:
        config_id, revision = append_approved_execution_profile(
            db_path=path, created_at=NOW.isoformat(), config_id="worker-fixture",
        )
        store.bind_task_execution(task_id=task.task_id, execution_config_id=config_id, execution_config_revision=revision,
                                  binding_kind="scheduled", bound_at=NOW.isoformat(), db_path=path)
    return task.task_id


def test_worker_publishes_result_with_frozen_context(task_db):
    enqueue(task_db, budget={"maxAttempts": 1})
    seen = []

    def handler(context):
        context.require_lease()
        seen.append((context.input_version, context.input_cutoff_at, context.task.payload))
        return TaskResult("completed", "analysis_saved", {"analysisId": "analysis-1"})

    task = run_once(db_path=task_db, worker_id="worker-a", lease_for=timedelta(seconds=30),
                    handlers={"analysis": handler}, clock=lambda: NOW)
    assert task.status == "completed"
    assert task.attempt_count == 1
    assert task.lease_owner is None
    assert seen == [("synthetic-config-1", NOW.isoformat(), {"observationId": "synthetic-observation"})]
    with read_connection(task_db) as conn:
        checkpoint = conn.execute("SELECT checkpoint_json FROM k10_tasks").fetchone()[0]
    checkpoint_payload = json.loads(checkpoint)
    assert checkpoint_payload["analysisId"] == "analysis-1"
    assert checkpoint_payload["executionStartedAt"] == NOW.isoformat()
    assert run_once(db_path=task_db, worker_id="worker-a", lease_for=timedelta(seconds=30),
                    handlers={"analysis": handler}, clock=lambda: NOW) is None


def test_parent_executes_only_its_named_child_using_the_same_worker_fence(task_db):
    enqueue(task_db, budget={"maxAttempts": 1})
    store.enqueue_task(task_id="child", kind="morning_review", idempotency_key="child",
                       input_version="frozen", input_cutoff_at=NOW.isoformat(), payload={},
                       budget={"maxAttempts": 1}, created_at=NOW.isoformat(), db_path=task_db)
    config_id, revision = append_approved_execution_profile(db_path=task_db, created_at=NOW.isoformat(), config_id="worker-child-fixture")
    store.bind_task_execution(task_id="child", execution_config_id=config_id, execution_config_revision=revision,
                              binding_kind="scheduled", bound_at=NOW.isoformat(), db_path=task_db)
    seen = []
    def child_handler(context):
        seen.append(context.task.task_id)
        return TaskResult("completed", "review_saved")
    result = run_once(db_path=task_db, worker_id="parent-child-worker", lease_for=timedelta(seconds=30),
                      handlers={"morning_review": child_handler}, clock=lambda: NOW, task_id="child")
    assert result.status == "completed" and seen == ["child"]
    assert store.get_task(task_id="task-1", db_path=task_db).status == "queued"
    assert run_once(db_path=task_db, worker_id="parent-child-worker", lease_for=timedelta(seconds=30),
                    handlers={"morning_review": child_handler}, clock=lambda: NOW, task_id="child") is None


def test_handler_cannot_publish_after_lease_expires_between_heartbeats(task_db):
    enqueue(task_db, budget={"maxAttempts": 1})
    current = [NOW]
    def handler(context):
        current[0] += timedelta(seconds=31)
        context.require_lease()
        pytest.fail("Expired owner must not publish an artifact")
    with pytest.raises(store.K10Conflict):
        run_once(db_path=task_db, worker_id="worker-a", lease_for=timedelta(seconds=30),
                 handlers={"analysis": handler}, clock=lambda: current[0])
    assert store.get_task(task_id="task-1", db_path=task_db).status == "running"


@pytest.mark.parametrize("budget", [{}, {"maxAttempts": True}, {"maxAttempts": 0}])
def test_legacy_budget_shape_does_not_block_a_bound_v3_task(task_db, budget):
    enqueue(task_db, budget=budget)
    calls = []
    task = run_once(db_path=task_db, worker_id="worker-a", lease_for=timedelta(seconds=30),
                    handlers={"analysis": lambda _: (calls.append(True), TaskResult("completed", "saved"))[1]}, clock=lambda: NOW)
    assert task.status == "completed"
    assert calls == [True]


def test_unregistered_task_is_visible_as_unconfigured(task_db):
    enqueue(task_db, budget={"maxAttempts": 1})
    task = run_once(db_path=task_db, worker_id="worker-a", lease_for=timedelta(seconds=30),
                    handlers={}, clock=lambda: NOW)
    assert task.status == "not_configured"


def test_pause_after_claim_never_enters_the_handler(task_db, monkeypatch):
    enqueue(task_db, budget={"maxAttempts": 1})
    original = store.run_control_status
    checks = 0

    def control(**kwargs):
        nonlocal checks
        checks += 1
        return original(**kwargs) if checks == 1 else {"state": "closed", "reasonCode": "operator_pause"}

    monkeypatch.setattr(store, "run_control_status", control)
    task = run_once(
        db_path=task_db, worker_id="worker-a", lease_for=timedelta(seconds=30),
        handlers={"analysis": lambda _: pytest.fail("paused task entered handler")}, clock=lambda: NOW,
    )
    assert task is not None and task.status == "failed"
    with read_connection(task_db) as conn:
        assert conn.execute("SELECT stage FROM k10_tasks WHERE task_id=?", (task.task_id,)).fetchone()[0] == "paused"


def test_pause_after_handler_prevents_retry_scheduling(task_db, monkeypatch):
    enqueue(task_db, budget={"maxAttempts": 2})
    original = store.run_control_status
    checks = 0

    def control(**kwargs):
        nonlocal checks
        checks += 1
        return original(**kwargs) if checks < 3 else {"state": "closed", "reasonCode": "operator_pause"}

    monkeypatch.setattr(store, "run_control_status", control)
    task = run_once(
        db_path=task_db, worker_id="worker-a", lease_for=timedelta(seconds=30),
        handlers={"analysis": lambda _: TaskResult(
            "failed", "continuation", retry_at=NOW + timedelta(minutes=1),
            retry_kind="continuation", safe_error_code="DISCOVERY_SLICE",
        )}, clock=lambda: NOW,
    )
    assert task is not None and task.status == "failed"
    with read_connection(task_db) as conn:
        assert conn.execute("SELECT stage FROM k10_tasks WHERE task_id=?", (task.task_id,)).fetchone()[0] == "paused"
        assert conn.execute("SELECT COUNT(*) FROM k10_task_retry_schedules").fetchone()[0] == 0


def test_store_retry_write_is_closed_by_durable_pause(task_db):
    task_id = enqueue(task_db, budget={"maxAttempts": 2})
    claimed = store.claim_task_by_id(task_id=task_id, worker_id="worker-a", now=NOW,
                                     lease_for=timedelta(seconds=30), db_path=task_db)
    assert claimed is not None
    store.set_run_control(state="closed", reason_code="operator_pause", changed_at=NOW.isoformat(),
                          changed_by="test", db_path=task_db)
    assert not store.schedule_task_retry(
        task_id=task_id, worker_id="worker-a", stage="continuation", checkpoint={},
        safe_error_code="DISCOVERY_SLICE", not_before_at=NOW + timedelta(minutes=1), scheduled_at=NOW,
        retry_kind="continuation", max_failure_attempts=2, db_path=task_db,
    )
    with read_connection(task_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_task_retry_schedules").fetchone()[0] == 0


def test_crash_recovery_retries_one_unfinished_generic_task(task_db):
    # A lease crash proves no handler result was durably recorded, so the
    # generic task may receive its one finite failure attempt on recovery.
    enqueue(task_db, budget={"maxAttempts": 1}, kind="collect_market_day_fact", bind_execution=False)
    store.claim_tasks(worker_id="crashed-worker", now=NOW, lease_for=timedelta(seconds=1),
                      limit=1, db_path=task_db)
    calls = []
    task = run_once(db_path=task_db, worker_id="recovery-worker", lease_for=timedelta(seconds=30),
                    handlers={"collect_market_day_fact": lambda _: (calls.append(True), TaskResult("completed", "collected"))[1]},
                    clock=lambda: NOW + timedelta(seconds=2))
    assert task.status == "completed"
    assert calls == [True]
    with read_connection(task_db) as conn:
        assert conn.execute("SELECT stage FROM k10_tasks").fetchone()[0] == "collected"


def test_untrusted_provider_error_body_is_not_stored(task_db):
    enqueue(task_db, budget={"maxAttempts": 1})

    def handler(_):
        raise RuntimeError("synthetic-secret-in-upstream-url")

    task = run_once(db_path=task_db, worker_id="worker-a", lease_for=timedelta(seconds=30),
                    handlers={"analysis": handler}, clock=lambda: NOW)
    assert task.status == "failed"
    with read_connection(task_db) as conn:
        error = conn.execute("SELECT error_text FROM k10_tasks").fetchone()[0]
    assert "synthetic-secret" not in error
    assert "重试" in error


def test_expired_worker_cannot_acknowledge_result(task_db):
    enqueue(task_db, budget={"maxAttempts": 2})
    moments = iter((NOW, NOW + timedelta(seconds=31)))
    with pytest.raises(store.K10Conflict):
        run_once(db_path=task_db, worker_id="worker-a", lease_for=timedelta(seconds=30),
                 handlers={"analysis": lambda _: TaskResult("completed", "done")},
                 clock=lambda: next(moments))
    assert store.get_task(task_id="task-1", db_path=task_db).status == "running"
