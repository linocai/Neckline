"""B76 publication fault boundaries through the actual CLI/worker fixture."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading
from time import monotonic

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
    monkeypatch.setattr(k10_schema.sqlite3, "connect", lambda *_args, **_kwargs: connection)

    with pytest.raises(sqlite3.OperationalError, match="writer busy"):
        with k10_schema.write_connection(tmp_path / "busy.sqlite"):
            pytest.fail("BEGIN must reject before yielding a write connection")

    assert connection.closed is True


def test_sqlite_busy_classification_uses_primary_error_code_before_message_text():
    """An I/O error mentioning a lock is not a recoverable write contention."""
    nonbusy = sqlite3.OperationalError("database is locked while reading damaged media")
    nonbusy.sqlite_errorcode = sqlite3.SQLITE_IOERR
    busy_extended = sqlite3.OperationalError("untranslated sqlite failure")
    busy_extended.sqlite_errorcode = sqlite3.SQLITE_BUSY | (1 << 8)
    assert k10_schema._is_busy_or_locked(nonbusy) is False
    assert k10_schema._is_busy_or_locked(busy_extended) is True


def test_real_sqlite_writer_lock_has_a_bounded_exit(tmp_path):
    """A holder that outlives every bounded BEGIN retry produces no write body."""
    db_path = tmp_path / "bounded-real-lock.sqlite"
    initialize_schema(db_path)
    acquired, release = threading.Event(), threading.Event()

    def hold_writer() -> None:
        connection = sqlite3.connect(db_path, timeout=0)
        try:
            connection.execute("BEGIN IMMEDIATE")
            acquired.set()
            assert release.wait(timeout=10)
        finally:
            connection.rollback()
            connection.close()

    holder = threading.Thread(target=hold_writer, name="b82-bounded-lock-holder")
    holder.start()
    assert acquired.wait(timeout=1)
    started = monotonic()
    with pytest.raises(k10_schema.SqliteWriteBusy):
        with write_connection(db_path):
            pytest.fail("bounded lock must reject before a closure can write")
    elapsed = monotonic() - started
    release.set()
    holder.join(timeout=2)
    assert not holder.is_alive()
    # Current settings bound BEGIN waits to roughly six seconds. Leave a small
    # scheduling margin without permitting a hidden indefinitely blocked task.
    assert 1 < elapsed < 8


def _write_checkpoint_under_real_lock(original, kwargs, observations):
    """Hold a second writer until this exact write exhausts its BEGIN retries."""
    acquired, release = threading.Event(), threading.Event()
    errors: list[BaseException] = []
    started: list[float] = []

    def hold_writer():
        connection = None
        try:
            # Concurrent research may own a short transaction when we arrive.
            # Wait for it, rather than treating failure to acquire as a lock test.
            connection = sqlite3.connect(kwargs["db_path"], timeout=5)
            connection.execute("BEGIN IMMEDIATE")
            started.append(monotonic())
            acquired.set()
            if not release.wait(timeout=20):
                raise AssertionError("checkpoint did not reach its bounded busy exit")
        except BaseException as exc:
            errors.append(exc)
            acquired.set()
        finally:
            if connection is not None:
                connection.rollback()
                connection.close()

    holder = threading.Thread(target=hold_writer, name="b82-real-writer-holder")
    holder.start()
    try:
        assert acquired.wait(timeout=6), "second writer never acquired the lock"
        assert not errors, errors
        try:
            original(**kwargs)
        except k10_schema.SqliteWriteBusy:
            observations.append(monotonic() - started[0])
            raise
        raise AssertionError("checkpoint write succeeded while the second writer held its lock")
    finally:
        release.set()
        holder.join(timeout=3)
        assert not holder.is_alive()
        assert not errors, errors


def test_real_writer_lock_releases_and_the_original_cli_task_completes_once(tmp_path, monkeypatch):
    """84 concurrent events cross real sqlite_busy continuation without rebilling."""
    from tests import v340_acceptance_fixture as fixture

    original = store.record_execution_checkpoint
    injected = False
    observations: list[float] = []
    gate = threading.Lock()
    queued_counts: list[int] = []
    original_worker = fixture.run_once

    def capture_worker(**kwargs):
        result = original_worker(**kwargs)
        if result.status == "queued":
            with sqlite3.connect(kwargs["db_path"]) as connection:
                checkpoint = json.loads(connection.execute(
                    "SELECT checkpoint_json FROM k10_tasks WHERE task_id=?", (result.task_id,),
                ).fetchone()[0])
            queued_counts.append(checkpoint["sqliteBusyContinuationCount"])
            assert checkpoint["scanId"]
        return result

    def lock_one_checkpoint(**kwargs):
        nonlocal injected
        with gate:
            should_lock = (not injected and kwargs.get("stage") == "model:investigation_research_round"
                           and kwargs.get("status") == "completed")
            if should_lock:
                injected = True
        if should_lock:
            return _write_checkpoint_under_real_lock(original, kwargs, observations)
        return original(**kwargs)

    monkeypatch.setattr(store, "record_execution_checkpoint", lock_one_checkpoint)
    monkeypatch.setattr(fixture, "run_once", capture_worker)
    flow = run_full_scale_flow(tmp_path, monkeypatch, name="real-lock-resume",
                               expected_continuation_codes=("sqlite_busy",))
    assert injected and len(observations) == 1
    assert observations[0] >= sum(k10_schema._WRITE_BEGIN_BACKOFF_SECONDS)
    assert flow.task_status == "completed"
    assert flow.continuation_count == 1
    assert queued_counts == [1]
    assert flow.calls["research:research_round"] == 84 * 2
    with sqlite3.connect(flow.db_path) as connection:
        assert connection.execute("SELECT count(*) FROM k10_tasks").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM k10_task_outbox WHERE task_id=?", (flow.task_id,)).fetchone()[0] <= 1
        assert connection.execute(
            "SELECT count(*) FROM k10_external_attempts WHERE state IN ('started','running','unknown')"
        ).fetchone()[0] == 0


def test_real_research_writer_lock_reaches_the_persistent_same_task_limit(tmp_path, monkeypatch):
    """The same paid checkpoint hits real contention in two worker slices."""
    from tests import v340_acceptance_fixture as fixture

    original = store.record_execution_checkpoint
    target_key = None
    target_input = None
    injected = 0
    observations: list[float] = []
    gate = threading.Lock()
    attempts_after_worker: list[int] = []
    original_worker = fixture.run_once
    wire_inputs: list[bytes] = []
    original_respond = fixture.DeterministicTransport.respond

    def capture_wire(transport, request):
        if transport._packet(request).get("action") == "research_round":
            with gate:
                wire_inputs.append(request.content)
        return original_respond(transport, request)

    def capture_worker(**kwargs):
        result = original_worker(**kwargs)
        with sqlite3.connect(kwargs["db_path"]) as connection:
            attempts_after_worker.append(connection.execute(
                "SELECT count(*) FROM k10_external_attempts WHERE stage='investigation'"
            ).fetchone()[0])
        return result

    def lock_each_research_completion(**kwargs):
        nonlocal injected, target_key, target_input
        with gate:
            eligible = (kwargs.get("stage") == "model:investigation_research_round"
                        and kwargs.get("status") == "completed")
            if eligible and target_key is None:
                target_key = kwargs["item_key"]
                target_input = kwargs["input_sha256"]
            should_lock = eligible and kwargs["item_key"] == target_key and injected < 2
            if should_lock:
                injected += 1
        if should_lock:
            return _write_checkpoint_under_real_lock(original, kwargs, observations)
        return original(**kwargs)

    monkeypatch.setattr(fixture, "run_once", capture_worker)
    monkeypatch.setattr(fixture.DeterministicTransport, "respond", capture_wire)
    monkeypatch.setattr(store, "record_execution_checkpoint", lock_each_research_completion)
    flow = run_full_scale_flow(tmp_path, monkeypatch, name="persistent-research-lock",
                               expect_handler_failure=True, expected_continuation_codes=("sqlite_busy",))
    assert injected == len(observations) == 2
    assert all(duration >= sum(k10_schema._WRITE_BEGIN_BACKOFF_SECONDS) for duration in observations)
    assert flow.task_status == "failed"
    assert flow.continuation_count == 1
    # Concurrent siblings can legitimately advance new inputs in the second
    # slice. Prove no exact wire input was billed twice, and the blocked
    # checkpoint still has exactly its original paid attempt.
    assert len(attempts_after_worker) == 2
    assert attempts_after_worker[0] > 0
    assert len(wire_inputs) == len(set(wire_inputs)) == attempts_after_worker[-1]
    with sqlite3.connect(flow.db_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM k10_external_attempts WHERE stage='investigation' AND item_key LIKE ?",
            ("%:" + target_input,),
        ).fetchone() == (1,)
        checkpoint = json.loads(connection.execute(
            "SELECT checkpoint_json FROM k10_tasks WHERE task_id=?", (flow.task_id,),
        ).fetchone()[0])
        assert checkpoint["sqliteBusyContinuationCount"] == 2
        assert connection.execute("SELECT count(*) FROM k10_tasks").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM k10_task_retry_schedules WHERE task_id=?", (flow.task_id,)).fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM k10_task_outbox WHERE task_id=?", (flow.task_id,)).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM k10_external_attempts WHERE state IN ('started','running','unknown')"
        ).fetchone() == (0,)
        snapshot_statuses = connection.execute(
            "SELECT current.execution_status,current.research_status,count(*) FROM k10_research_snapshot_revisions current "
            "WHERE current.task_id=? AND current.revision=("
            "SELECT MAX(latest.revision) FROM k10_research_snapshot_revisions latest "
            "WHERE latest.snapshot_id=current.snapshot_id) "
            "GROUP BY current.execution_status,current.research_status",
            (flow.task_id,),
        ).fetchall()
    expected_failed = sum(count for execution, _status, count in snapshot_statuses if execution != "ok")
    expected_processed = sum(
        count for execution, research_status, count in snapshot_statuses
        if execution == "ok" and research_status != "continue_research"
    )
    expected_unprocessed = 84 - expected_failed - expected_processed

    config_id, config_revision, execution_id, execution_revision = fixture.active_bindings(flow.db_path)
    with fixture.actual_api(
        flow.db_path, config_id=config_id, config_revision=config_revision,
        execution_id=execution_id, execution_revision=execution_revision,
    ) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=evening")
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["status"] == "failed" and report["availableAt"] is None
    counts = report["delivery"]["counts"]
    assert counts["eventInput"] == 84
    assert 0 < counts["eventProcessed"] + counts["eventFailed"] < 84
    assert counts["eventUnprocessed"] > 0
    assert counts["eventProcessed"] == expected_processed
    assert counts["eventFailed"] == expected_failed
    assert counts["eventUnprocessed"] == expected_unprocessed
    assert sum(counts[key] for key in ("eventProcessed", "eventFailed", "eventUnprocessed")) == 84
    assert counts["publishedCompanies"] == 0


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
