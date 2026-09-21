"""B77 public producer/retry gates; self-contained, network-denied databases."""
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from neckline.k10 import store
from neckline.k10.delivery import runtime_contract
from neckline.k10.schema import initialize_schema
from neckline.k10.worker import TaskResult, run_once
from tests.k10_v306_fixture import append_approved_execution_profile

NOW = datetime(2026, 9, 20, 4, 10, tzinfo=timezone.utc)
STAMP = NOW.isoformat()
PAID_KINDS = ("analysis", "morning_review", "morning_scan", "evening_scan")


def _db(tmp_path):
    path = tmp_path / "producer.sqlite"
    initialize_schema(path)
    store.set_run_control(state="open", reason_code="offline_test", changed_at=STAMP,
                          changed_by="test_v341", db_path=path)
    config_id, revision = append_approved_execution_profile(db_path=path, created_at=STAMP)
    return path, {"configId": config_id, "revision": revision}


def _command(kind="analysis", payload=None):
    return dict(task_id="original-task", kind=kind, idempotency_key="original-key", input_version="B77-test",
                input_cutoff_at=STAMP, payload={"runtimeContract": runtime_contract()} if payload is None else payload,
                budget={"maxAttempts": 2}, created_at=STAMP)


def _dump(path):
    with sqlite3.connect(path) as conn:
        return "\n".join(conn.iterdump())


def _close(path, state="closed"):
    if state == "missing":
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM k10_run_controls WHERE control_key='k10_discovery'")
    else:
        store.set_run_control(state="closed", reason_code="offline_pause", changed_at=STAMP,
                              changed_by="test_v341", db_path=path)


def _failed(path, binding, payload=None):
    task = store.enqueue_task(**_command(payload=payload), execution_binding=binding, db_path=path)
    claimed = store.claim_task_by_id(task_id=task.task_id, worker_id="fixture", now=NOW,
                                     lease_for=timedelta(minutes=1), db_path=path)
    assert claimed is not None
    store.finish_task(task_id=task.task_id, worker_id="fixture", status="failed", stage="fixture",
                      checkpoint={"paidEvidence": "preserve"}, error_text="original failure",
                      finished_at=NOW, db_path=path)
    return task.task_id


@pytest.mark.parametrize("kind", PAID_KINDS)
@pytest.mark.parametrize("state", ["closed", "missing"])
def test_default_enqueue_and_transaction_insert_cannot_bypass_control(tmp_path, kind, state):
    path, binding = _db(tmp_path)
    _close(path, state)
    before = _dump(path)
    # No caller opt-in flag: this is also the morning child producer entry.
    with pytest.raises(store.K10Conflict, match="非 open"):
        store.enqueue_task(**_command(kind), execution_binding=binding, db_path=path)
    assert _dump(path) == before
    # Internal producers use the same mandatory gate while holding the write transaction.
    with sqlite3.connect(path) as conn, pytest.raises(store.K10Conflict, match="非 open"):
        conn.execute("BEGIN IMMEDIATE")
        store._insert_task(conn, **_command(kind))
    assert _dump(path) == before


def test_paused_enqueue_replay_keeps_identity_without_attaching_or_changing_binding(tmp_path):
    path, binding = _db(tmp_path)
    task = store.enqueue_task(**_command(), execution_binding=binding, db_path=path)
    _close(path)
    before = _dump(path)
    replay = store.enqueue_task(**{**_command(), "task_id": "ignored-new-id"}, execution_binding=binding, db_path=path)
    assert replay.task_id == task.task_id
    with pytest.raises(store.K10Conflict, match="绑定"):
        store.enqueue_task(**_command(), execution_binding={**binding, "revision": binding["revision"] + 1}, db_path=path)
    assert _dump(path) == before
    # Missing original binding cannot be repaired while paused.
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM k10_task_execution_bindings WHERE task_id=?", (task.task_id,))
    before = _dump(path)
    with pytest.raises(store.K10Conflict, match="绑定"):
        store.enqueue_task(**_command(), execution_binding=binding, db_path=path)
    assert _dump(path) == before


@pytest.mark.parametrize("contract", [None, {}, {"reportDelivery": "old", "research": "old"},
                                      {**runtime_contract(), "extra": "unknown"}])
@pytest.mark.parametrize("user_requested", [False, True])
def test_all_retry_callers_reject_legacy_contract_without_mutation(tmp_path, contract, user_requested):
    path, binding = _db(tmp_path)
    task_id = _failed(path, binding, {} if contract is None else {"runtimeContract": contract})
    before = _dump(path)
    with pytest.raises(store.K10Conflict, match="旧协议"):
        store.retry_task(task_id=task_id, expected_attempt_count=1, retried_at=STAMP,
                         execution_binding=binding, user_requested=user_requested, db_path=path)
    assert _dump(path) == before


def test_b76_retry_requires_open_control_and_original_binding_then_strict_worker_can_claim(tmp_path):
    path, binding = _db(tmp_path)
    task_id = _failed(path, binding)
    _close(path)
    before = _dump(path)
    with pytest.raises(store.K10Conflict, match="非 open"):
        store.retry_task(task_id=task_id, expected_attempt_count=1, retried_at=STAMP, user_requested=True, db_path=path)
    assert _dump(path) == before
    store.set_run_control(state="open", reason_code="offline_test", changed_at=STAMP,
                          changed_by="test_v341", db_path=path)
    retry = store.retry_task(task_id=task_id, expected_attempt_count=1, retried_at=STAMP, user_requested=True, db_path=path)
    assert retry.task_id == task_id and retry.status == "queued"
    # A strict worker accepts the producer's unchanged binding and contract.
    completed = run_once(db_path=path, worker_id="after-retry", clock=lambda: NOW,
                         lease_for=timedelta(minutes=1), require_b76_contract=True,
                         handlers={"analysis": lambda context: TaskResult("completed", "fixture")})
    assert completed is not None and completed.task_id == task_id and completed.status == "completed"
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE k10_tasks SET status='failed' WHERE task_id=?", (task_id,))
        conn.execute("DELETE FROM k10_task_execution_bindings WHERE task_id=?", (task_id,))
    before = _dump(path)
    with pytest.raises(store.K10Conflict, match="原执行绑定"):
        store.retry_task(task_id=task_id, expected_attempt_count=2, retried_at=STAMP,
                         execution_binding=binding, user_requested=True, db_path=path)
    assert _dump(path) == before
