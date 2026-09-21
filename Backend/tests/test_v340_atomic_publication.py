"""B76 publication fault boundaries through the actual CLI/worker fixture."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
import threading

import pytest

from neckline.k10 import store
from neckline.k10 import model_execution
from neckline.k10 import schema as k10_schema
from neckline.k10 import worker as worker_runtime
from neckline.k10.delivery import runtime_contract
from neckline.k10.schema import initialize_schema, write_connection
from tests.k10_v306_fixture import append_approved_execution_profile
from tests.v340_acceptance_fixture import run_full_scale_flow


class _CommitInterrupted(RuntimeError):
    pass


_NOW = datetime(2026, 9, 16, 1, tzinfo=timezone.utc)


def test_write_connection_closes_when_begin_immediate_is_rejected(tmp_path, monkeypatch):
    """A failed writer admission owns no lingering SQLite connection."""
    class _BeginRejected:
        in_transaction = False

        def __init__(self) -> None:
            self.closed = False

        def execute(self, statement: str):
            if statement == "BEGIN IMMEDIATE":
                raise sqlite3.OperationalError("fixture writer busy")
            return None

        def close(self) -> None:
            self.closed = True

    connection = _BeginRejected()
    monkeypatch.setattr(k10_schema.sqlite3, "connect", lambda _path: connection)

    with pytest.raises(sqlite3.OperationalError, match="writer busy"):
        with k10_schema.write_connection(tmp_path / "busy.sqlite"):
            pytest.fail("BEGIN must reject before yielding a write connection")

    assert connection.closed is True


def test_model_reservation_rechecks_live_lease_after_writer_lock_releases(tmp_path):
    """A writer wait cannot let an expired worker reserve a provider operation.

    The reservation starts while another real SQLite writer owns ``BEGIN
    IMMEDIATE``.  Its optimistic guard sees the lease, then the live clock
    crosses that lease before the holder commits.  The second guard runs after
    the reservation transaction starts, so no model checkpoint or provider
    callback is admitted.
    """
    db_path = tmp_path / "lease-after-writer-wait.sqlite"
    initialize_schema(db_path)
    store.set_run_control(state="open", reason_code="fixture_execution", changed_at=_NOW.isoformat(),
                          changed_by="test", db_path=db_path)
    execution_id, execution_revision = append_approved_execution_profile(
        db_path=db_path, created_at=_NOW.isoformat(), config_id="writer-wait",
    )
    store.enqueue_task(
        task_id="writer-wait-task", kind="evening_scan", idempotency_key="writer-wait-task",
        input_version="fixture", input_cutoff_at=_NOW.isoformat(),
        payload={"windowKind": "evening", "runtimeContract": runtime_contract()},
        budget={"maxAttempts": 1}, created_at=_NOW.isoformat(), db_path=db_path,
    )
    store.bind_task_execution(task_id="writer-wait-task", execution_config_id=execution_id,
                              execution_config_revision=execution_revision, binding_kind="scheduled",
                              bound_at=_NOW.isoformat(), db_path=db_path)
    claimed = store.claim_task_by_id(task_id="writer-wait-task", worker_id="former", now=_NOW,
                                     lease_for=timedelta(seconds=1), db_path=db_path)
    assert claimed is not None

    clock = {"now": _NOW}
    context = worker_runtime._context(
        claimed, db_path, threading.Event(), lambda: clock["now"],
    )
    writer_held, release_writer, first_guard = threading.Event(), threading.Event(), threading.Event()
    failures: list[BaseException] = []
    provider_calls: list[str] = []
    guard_calls = 0

    def hold_writer() -> None:
        with write_connection(db_path):
            writer_held.set()
            assert release_writer.wait(timeout=3)

    def live_guard() -> None:
        nonlocal guard_calls
        context.require_lease()
        guard_calls += 1
        if guard_calls == 1:
            first_guard.set()

    def reserve() -> None:
        try:
            model_execution.execute_model_operation(
                task_id="writer-wait-task", operation="compare", item_key="event-1", input_sha256="a" * 64,
                policy={"networkMaxAttempts": 1, "jsonRepairMaxAttempts": 0},
                operation_call=lambda: provider_calls.append("called") or {}, validate=lambda value: value,
                db_path=db_path, leaseguard=live_guard, now=lambda: _NOW,
            )
        except BaseException as exc:
            failures.append(exc)

    holder = threading.Thread(target=hold_writer, name="v340-writer-holder")
    holder.start()
    assert writer_held.wait(timeout=1)
    reservation = threading.Thread(target=reserve, name="v340-model-reservation")
    reservation.start()
    assert first_guard.wait(timeout=1)
    # The guard above ran while the other write transaction was still open.
    # Advance only the live lease clock, then let the reservation obtain its
    # own SQLite writer slot and prove ownership again.
    clock["now"] = _NOW + timedelta(seconds=2)
    release_writer.set()
    holder.join(timeout=3)
    reservation.join(timeout=3)

    assert not holder.is_alive() and not reservation.is_alive()
    assert guard_calls == 1  # The second real guard rejects before incrementing.
    assert len(failures) == 1 and isinstance(failures[0], store.K10Conflict)
    assert provider_calls == []
    with sqlite3.connect(db_path) as connection:
        checkpoints = connection.execute(
            "SELECT count(*) FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:compare'",
            ("writer-wait-task",),
        ).fetchone()[0]
    assert checkpoints == 0


def test_b76_publication_transaction_interrupt_keeps_cards_and_task_terminal_together(tmp_path, monkeypatch):
    """A crash at the task finalizer rolls back every visible success write."""
    original = store.finish_task_with_publication

    def interrupt(*args, **kwargs):
        raise _CommitInterrupted("offline commit interruption")

    monkeypatch.setattr(store, "finish_task_with_publication", interrupt)
    flow = run_full_scale_flow(
        tmp_path, monkeypatch, name="commit-interrupt", selected_event_count=1,
        expect_handler_failure=True,
    )
    monkeypatch.setattr(store, "finish_task_with_publication", original)

    db_path = tmp_path / "commit-interrupt.sqlite"
    with sqlite3.connect(db_path) as connection:
        task = connection.execute("SELECT status,stage FROM k10_tasks").fetchone()
        report = connection.execute(
            "SELECT status,available_at FROM k10_v2_report_runs"
        ).fetchone()
        cards = connection.execute("SELECT count(*) FROM k10_v2_report_cards").fetchone()[0]
        batches = connection.execute("SELECT count(*) FROM k10_publication_batches").fetchone()[0]
    assert flow.task_status == "failed"
    assert task == ("failed", "execution")
    assert report == ("failed", None)
    assert cards == batches == 0


def test_b76_post_commit_worker_never_rewrites_atomic_terminal_task(tmp_path, monkeypatch):
    """After committed publication, the generic worker terminal write is forbidden."""
    calls: list[tuple[object, ...]] = []

    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("ordinary worker finish_task would split B76 publication")

    monkeypatch.setattr(store, "finish_task", forbidden)
    flow = run_full_scale_flow(tmp_path, monkeypatch, name="post-commit", selected_event_count=1)

    assert flow.task_status == "completed"
    assert calls == []
    with sqlite3.connect(flow.db_path) as connection:
        task = connection.execute("SELECT status,stage FROM k10_tasks WHERE task_id=?", (flow.task_id,)).fetchone()
        report = connection.execute(
            "SELECT status,available_at FROM k10_v2_report_runs WHERE scan_id=?", (flow.scan_id,)
        ).fetchone()
    assert task is not None and task[0] == "completed" and task[1] == "report_complete"
    assert report is not None and report[0] == "completed" and report[1]


def test_b76_storage_fault_does_not_become_a_local_partial_report(tmp_path, monkeypatch):
    """A checkpoint persistence fault stops the task before subset publication."""
    original = store.record_execution_checkpoint
    injected = False

    def fail_compare_checkpoint(**kwargs):
        nonlocal injected
        if kwargs.get("stage") == "model:investigation_research_round":
            injected = True
            raise sqlite3.OperationalError("offline fixture: database is locked")
        return original(**kwargs)

    monkeypatch.setattr(store, "record_execution_checkpoint", fail_compare_checkpoint)
    flow = run_full_scale_flow(
        tmp_path, monkeypatch, name="storage-fault", selected_event_count=1,
        expect_handler_failure=True,
    )

    assert injected is True
    assert flow.task_status == "failed"
    with sqlite3.connect(flow.db_path) as connection:
        report = connection.execute(
            "SELECT status,available_at FROM k10_v2_report_runs WHERE scan_id=?", (flow.scan_id,)
        ).fetchone()
        coverage = connection.execute(
            "SELECT content_json FROM k10_v2_report_coverage WHERE report_id=?", ("report_" + flow.scan_id,)
        ).fetchone()
        cards = connection.execute("SELECT count(*) FROM k10_v2_report_cards").fetchone()[0]
        batches = connection.execute("SELECT count(*) FROM k10_publication_batches").fetchone()[0]
    assert report == ("failed", None)
    assert coverage is not None
    assert '"outcome":"failed"' in coverage[0]
    assert '"rankingScope":"none"' in coverage[0]
    assert cards == batches == 0


@pytest.mark.parametrize("attempt_state", ("started", "unknown"))
def test_morning_publication_waits_for_parent_owned_unsettled_attempt(tmp_path, attempt_state):
    """An unresolved parent-owned request blocks formal publication without a child task."""
    db_path = tmp_path / "morning-unsettled.sqlite"
    initialize_schema(db_path)
    store.set_run_control(state="open", reason_code="fixture_execution", changed_at=_NOW.isoformat(),
                          changed_by="test", db_path=db_path)
    execution_id, execution_revision = append_approved_execution_profile(
        db_path=db_path, created_at=_NOW.isoformat(), config_id="morning-ledger",
    )
    parent_id, scan_id = "parent", "scan-morning"
    store.enqueue_task(task_id=parent_id, kind="morning_scan", idempotency_key="parent", input_version="fixture",
        input_cutoff_at=_NOW.isoformat(),
        payload={"windowKind": "morning", "runtimeContract": runtime_contract(),
                 "deliveryDeadlineAt": (_NOW + timedelta(minutes=20)).isoformat()},
        budget={"maxAttempts": 1}, created_at=_NOW.isoformat(), db_path=db_path)
    store.bind_task_execution(task_id=parent_id, execution_config_id=execution_id,
        execution_config_revision=execution_revision, binding_kind="scheduled", bound_at=_NOW.isoformat(), db_path=db_path)
    admission = store.begin_external_attempt(task_id=parent_id, stage="morning", item_key="review", attempt_key="wire",
        input_sha256="a" * 64, started_at=_NOW.isoformat(), db_path=db_path)
    assert admission["state"] == "started"
    if attempt_state == "unknown":
        settled = store.settle_external_attempt(attempt_id=str(admission["attemptId"]), outcome="unknown", usage=None,
            settled_at=_NOW.isoformat(), error_code="request_outcome_unknown", db_path=db_path)
        assert settled["state"] == "unknown"
    parent = store.claim_task_by_id(task_id=parent_id, worker_id="parent-worker", now=_NOW,
        lease_for=timedelta(minutes=1), db_path=db_path)
    assert parent is not None
    with write_connection(db_path) as conn:
        with pytest.raises(store.K10Conflict, match="未结算"):
            store.finish_task_with_publication(conn, task_id=parent_id, worker_id="parent-worker", stage="report_complete",
                checkpoint={"scanId": scan_id}, finished_at=_NOW.isoformat())
    assert store.get_task(task_id=parent_id, db_path=db_path).status == "running"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT state FROM k10_external_attempts WHERE attempt_id=?",
                                  (admission["attemptId"],)).fetchone() == (attempt_state,)
        assert connection.execute("SELECT count(*) FROM k10_tasks WHERE kind='morning_review'").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM k10_v2_report_runs").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM k10_v2_report_cards").fetchone() == (0,)
