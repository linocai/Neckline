from datetime import datetime, timedelta, timezone
import json

import pytest

from neckline.k10 import store
from neckline.k10.schema import initialize_schema, read_connection
from neckline.k10.worker import TaskResult, run_once


NOW = datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def task_db(tmp_path):
    path = tmp_path / "worker.sqlite"
    initialize_schema(path)
    return path


def enqueue(path, *, budget):
    return store.enqueue_task(
        task_id="task-1", kind="analysis", idempotency_key="analysis-1",
        input_version="synthetic-config-1", input_cutoff_at=NOW.isoformat(),
        payload={"observationId": "synthetic-observation"}, budget=budget,
        created_at=NOW.isoformat(), db_path=path,
    )


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
    assert json.loads(checkpoint) == {"analysisId": "analysis-1"}
    assert run_once(db_path=task_db, worker_id="worker-a", lease_for=timedelta(seconds=30),
                    handlers={"analysis": handler}, clock=lambda: NOW) is None


def test_parent_executes_only_its_named_child_using_the_same_worker_fence(task_db):
    enqueue(task_db, budget={"maxAttempts": 1})
    store.enqueue_task(task_id="child", kind="morning_review", idempotency_key="child",
                       input_version="frozen", input_cutoff_at=NOW.isoformat(), payload={},
                       budget={"maxAttempts": 1}, created_at=NOW.isoformat(), db_path=task_db)
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
def test_missing_attempt_policy_never_calls_model(task_db, budget):
    enqueue(task_db, budget=budget)
    calls = []
    task = run_once(db_path=task_db, worker_id="worker-a", lease_for=timedelta(seconds=30),
                    handlers={"analysis": lambda _: calls.append(True)}, clock=lambda: NOW)
    assert task.status == "not_configured"
    assert calls == []


def test_unregistered_task_is_visible_as_unconfigured(task_db):
    enqueue(task_db, budget={"maxAttempts": 1})
    task = run_once(db_path=task_db, worker_id="worker-a", lease_for=timedelta(seconds=30),
                    handlers={}, clock=lambda: NOW)
    assert task.status == "not_configured"


def test_crash_recovery_does_not_exceed_approved_attempts(task_db):
    enqueue(task_db, budget={"maxAttempts": 1})
    store.claim_tasks(worker_id="crashed-worker", now=NOW, lease_for=timedelta(seconds=1),
                      limit=1, db_path=task_db)
    calls = []
    task = run_once(db_path=task_db, worker_id="recovery-worker", lease_for=timedelta(seconds=30),
                    handlers={"analysis": lambda _: calls.append(True)},
                    clock=lambda: NOW + timedelta(seconds=2))
    assert task.status == "failed"
    assert calls == []
    with read_connection(task_db) as conn:
        assert conn.execute("SELECT stage FROM k10_tasks").fetchone()[0] == "attempt_limit"


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
