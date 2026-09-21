"""B76 P04 durable-control boundaries through production-facing entry points.

These tests deliberately use the real scan CLI to obtain task IDs and V3
bindings.  Provider calls stop at the durable model/Tavily ledgers; no socket,
APNs transport, timer, or production database is involved.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import sqlite3
from threading import Barrier, Event, Lock, Thread

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from neckline.api.k10 import create_router
from neckline.k10 import store
from neckline.k10.cli import enqueue_scan, main as cli_main
from neckline.k10.notification_runtime import create_notification_maintenance
from neckline.k10.notifications import initialize_notifications_schema
from neckline.k10.schema import initialize_schema, write_connection
from neckline.k10.windows import evening_cutoff
from neckline.k10.worker import run_once
from tests.k10_v306_fixture import append_approved_execution_profile


DAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _prepare_producer_database(path: Path) -> tuple[str, int, str, int]:
    """Set up the smallest approved non-strategy input accepted by the real CLI."""
    initialize_schema(path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        connection.executemany(
            "INSERT INTO trade_cal VALUES ('SSE', ?, 1)",
            [
                (DAY.strftime("%Y%m%d"),), ((DAY + timedelta(days=1)).strftime("%Y%m%d"),),
                ((DAY + timedelta(days=2)).strftime("%Y%m%d"),),
            ],
        )
    config_payload = json.loads(
        (Path(__file__).parents[1] / "neckline" / "config" / "k10-v1.4.json").read_text(encoding="utf-8")
    )
    config_id = "v340-control-run"
    config_revision = store.append_run_config(
        config_id=config_id, payload=config_payload, created_at=NOW.isoformat(), db_path=path,
    )
    execution_id, execution_revision = append_approved_execution_profile(
        db_path=path, created_at=NOW.isoformat(), config_id="v340-control-execution",
    )
    store.set_run_control(
        state="open", reason_code="isolated_test_open", changed_at=NOW.isoformat(),
        changed_by="test_v340_execution_control", db_path=path,
    )
    return config_id, config_revision, execution_id, execution_revision


def _enqueue_real_scan(
    path: Path, *, kind: str, config_id: str, config_revision: int,
    execution_id: str, execution_revision: int,
) -> str:
    output = StringIO()
    arguments = [
        "enqueue", "--db", str(path), "--kind", kind, "--trading-day", DAY.isoformat(),
        "--config-id", config_id, "--config-revision", str(config_revision),
        "--execution-config-id", execution_id, "--execution-config-revision", str(execution_revision),
    ]
    if kind == "evening":
        arguments.extend(["--bootstrap-cutoff", (evening_cutoff(DAY) - timedelta(hours=2)).isoformat()])
    with redirect_stdout(output):
        assert cli_main(arguments) == 0
    task_id = output.getvalue().strip()
    assert task_id.startswith("task_")
    profile = store.task_execution_profile(task_id=task_id, db_path=path)
    assert profile is not None
    assert (profile["configId"], profile["revision"], profile["bindingKind"]) == (
        execution_id, execution_revision, "scheduled",
    )
    return task_id


def _control_api(path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(lambda: path, lambda: None, lambda: path.parent / "parquet"))
    return TestClient(app)


def _receipt() -> dict[str, object]:
    return {
        "receiptVersion": "k10-provider-receipt-v1", "ok": True, "content": "{\"fixture\":true}",
        "provider": "fixture", "model": "deepseek-v4-pro", "promptTokens": 3,
        "completionTokens": 2, "totalTokens": 5, "usageUnavailable": False,
        "errorCode": None, "retryAfterSeconds": None, "finishReason": "stop",
        "rawResponses": [{"fixture": "receipt"}], "responseReceived": True, "rawReceiptOnly": True,
    }


def _usage() -> dict[str, int]:
    return {
        "promptTokens": 3, "completionTokens": 2, "totalTokens": 5,
        "searchRequests": 0, "searchCredits": 0,
    }


def _enqueue_active_maintenance_tasks(path: Path) -> tuple[str, ...]:
    """Use the durable producer API, never SQL, for every non-scan active kind."""
    kinds = ("analysis", "morning_review", "collect_market_day_fact", "evaluate_company_window")
    task_ids: list[str] = []
    for kind in kinds:
        task_id = f"task-control-{kind}"
        task = store.enqueue_task(
            task_id=task_id, kind=kind, idempotency_key=f"control:{kind}", input_version="control-fixture",
            input_cutoff_at=NOW.isoformat(), payload={}, budget={"maxAttempts": 1},
            created_at=NOW.isoformat(), db_path=path,
        )
        assert task.task_id == task_id
        task_ids.append(task_id)
    return tuple(task_ids)


def test_closed_real_cli_producer_cannot_enqueue_or_create_a_binding(tmp_path: Path) -> None:
    path = tmp_path / "closed-producer.sqlite"
    config_id, config_revision, execution_id, execution_revision = _prepare_producer_database(path)
    store.set_run_control(
        state="closed", reason_code="user_paused", changed_at=(NOW + timedelta(seconds=1)).isoformat(),
        changed_by="test_v340_execution_control", db_path=path,
    )

    with pytest.raises(RuntimeError, match="运行已暂停"):
        _enqueue_real_scan(
            path, kind="evening", config_id=config_id, config_revision=config_revision,
            execution_id=execution_id, execution_revision=execution_revision,
        )

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM k10_tasks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM k10_task_execution_bindings").fetchone()[0] == 0
    with _control_api(path) as client:
        readiness = client.get("/api/v1/k10/operations/readiness")
    assert readiness.status_code == 200
    assert readiness.json()["runControl"] == {
        "state": "paused", "reasonCode": "user_paused",
        "changedAt": (NOW + timedelta(seconds=1)).isoformat(), "executionState": "paused",
        "inFlightCount": 0, "unknownCount": 0, "activeTasks": [],
    }


def test_committed_close_wins_racing_producer_claim_and_ledger_admissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make four real write paths wait behind the uncommitted close transaction.

    The wrapper only records that each production store write has reached its
    real SQLite transaction.  It never substitutes a control state or a store
    result.  Releasing the close is therefore a deterministic transaction
    order, rather than timing a sleep and hoping an admission loses.
    """
    path = tmp_path / "close-wins-race.sqlite"
    config_id, config_revision, execution_id, execution_revision = _prepare_producer_database(path)
    evening_task = _enqueue_real_scan(
        path, kind="evening", config_id=config_id, config_revision=config_revision,
        execution_id=execution_id, execution_revision=execution_revision,
    )
    morning_task = _enqueue_real_scan(
        path, kind="morning", config_id=config_id, config_revision=config_revision,
        execution_id=execution_id, execution_revision=execution_revision,
    )

    original_write_connection = store.write_connection
    writers_entered, start = Event(), Barrier(5)
    count_lock, outcome_lock = Lock(), Lock()
    writer_count = 0
    outcomes: dict[str, object] = {}

    @contextmanager
    def observed_write_connection(db_path: Path):
        nonlocal writer_count
        with count_lock:
            writer_count += 1
            if writer_count == 4:
                writers_entered.set()
        with original_write_connection(db_path) as connection:
            yield connection

    monkeypatch.setattr(store, "write_connection", observed_write_connection)

    def race(label: str, operation) -> None:
        try:
            start.wait(timeout=5)
            value: object = operation()
        except BaseException as exc:  # Assertions below retain exact public failures.
            value = exc
        with outcome_lock:
            outcomes[label] = value

    next_day = DAY + timedelta(days=1)
    operations = {
        "model": lambda: store.begin_model_external_attempt(
            task_id=evening_task, stage="titleBatch", item_key="race-batch", attempt_key="race-model",
            input_sha256=_HASH_A, reuse_scope_sha256=_HASH_B, started_at=NOW.isoformat(), db_path=path,
        ),
        "tavily": lambda: store.begin_external_attempt(
            task_id=evening_task, stage="verify", item_key="race-event", attempt_key="race-tavily",
            input_sha256=_HASH_B, started_at=NOW.isoformat(), db_path=path,
        ),
        "claim": lambda: store.claim_task_by_id(
            task_id=morning_task, worker_id="race-worker", now=NOW, lease_for=timedelta(minutes=5),
            db_path=path, require_b76_contract=True,
        ),
        "producer": lambda: enqueue_scan(
            db_path=path, kind="evening", trading_day=next_day, config_id=config_id,
            config_revision=config_revision, now=NOW,
            bootstrap_cutoff=(evening_cutoff(next_day) - timedelta(hours=2)).isoformat(),
            execution_config_id=execution_id, execution_config_revision=execution_revision,
        ),
    }
    threads = [Thread(target=race, args=(label, operation), daemon=True) for label, operation in operations.items()]

    # This is the actual control row and the same `BEGIN IMMEDIATE` mechanism
    # used by the store.  The public setter cannot expose a pre-commit hook,
    # so the test owns only this transaction boundary; every competing action
    # remains its production producer/claim/ledger implementation.
    with write_connection(path) as connection:
        connection.execute(
            "INSERT INTO k10_run_controls(control_key,state,reason_code,changed_at,changed_by) VALUES('k10_discovery',?,?,?,?) "
            "ON CONFLICT(control_key) DO UPDATE SET state=excluded.state,reason_code=excluded.reason_code,"
            "changed_at=excluded.changed_at,changed_by=excluded.changed_by",
            ("closed", "transaction_close", (NOW + timedelta(seconds=1)).isoformat(), "race-test"),
        )
        for thread in threads:
            thread.start()
        start.wait(timeout=5)
        assert writers_entered.wait(timeout=5), "all competing store writers must reach the held SQLite boundary"
        assert outcomes == {}
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive(), "the committed close must release every blocked writer"

    assert writer_count == 4
    assert outcomes["model"] == {"state": "paused", "reason": "transaction_close", "attemptId": None}
    assert outcomes["tavily"] == {"state": "paused", "reason": "transaction_close", "attemptId": None}
    assert outcomes["claim"] is None
    assert isinstance(outcomes["producer"], RuntimeError)
    assert "运行已暂停" in str(outcomes["producer"])
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM k10_tasks").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM k10_external_attempts").fetchone()[0] == 0
    assert store.get_task(task_id=morning_task, db_path=path).status == "queued"


def test_precommitted_admissions_drain_after_pause_without_reopening_or_republishing(tmp_path: Path) -> None:
    path = tmp_path / "pause-boundary.sqlite"
    config_id, config_revision, execution_id, execution_revision = _prepare_producer_database(path)
    evening_task = _enqueue_real_scan(
        path, kind="evening", config_id=config_id, config_revision=config_revision,
        execution_id=execution_id, execution_revision=execution_revision,
    )
    morning_task = _enqueue_real_scan(
        path, kind="morning", config_id=config_id, config_revision=config_revision,
        execution_id=execution_id, execution_revision=execution_revision,
    )
    maintenance_tasks = _enqueue_active_maintenance_tasks(path)

    # This is the inverse serial edge of the concurrent close-wins regression:
    # both admissions have committed before the durable close, so they drain
    # rather than being forgotten or reopened.
    claimed = store.claim_task_by_id(
        task_id=evening_task, worker_id="control-worker", now=NOW,
        lease_for=timedelta(minutes=5), db_path=path, require_b76_contract=True,
    )
    assert claimed is not None and claimed.task_id == evening_task
    model = store.begin_model_external_attempt(
        task_id=evening_task, stage="titleBatch", item_key="batch-1", attempt_key="model-before-pause",
        input_sha256=_HASH_A, reuse_scope_sha256=_HASH_B, started_at=NOW.isoformat(), db_path=path,
    )
    tavily = store.begin_external_attempt(
        task_id=evening_task, stage="verify", item_key="event-1", attempt_key="tavily-before-pause",
        input_sha256=_HASH_B, started_at=NOW.isoformat(), db_path=path,
    )
    assert model["state"] == tavily["state"] == "started"
    assert isinstance(model["attemptId"], str) and isinstance(tavily["attemptId"], str)

    store.set_run_control(
        state="closed", reason_code="user_paused", changed_at=(NOW + timedelta(seconds=1)).isoformat(),
        changed_by="test_v340_execution_control", db_path=path,
    )
    # No sleep or best-effort timing: each following admission runs after the
    # committed close transaction and must be rejected before any transport.
    assert store.begin_model_external_attempt(
        task_id=evening_task, stage="titleBatch", item_key="batch-2", attempt_key="model-after-pause",
        input_sha256="c" * 64, reuse_scope_sha256="d" * 64,
        started_at=(NOW + timedelta(seconds=2)).isoformat(), db_path=path,
    ) == {"state": "paused", "reason": "user_paused", "attemptId": None}
    assert store.begin_external_attempt(
        task_id=evening_task, stage="verify", item_key="event-2", attempt_key="tavily-after-pause",
        input_sha256="e" * 64, started_at=(NOW + timedelta(seconds=2)).isoformat(), db_path=path,
    ) == {"state": "paused", "reason": "user_paused", "attemptId": None}

    # Production worker entry must not claim the real CLI task after the close.
    called: list[str] = []
    assert run_once(
        db_path=path, worker_id="closed-worker", lease_for=timedelta(minutes=5),
        handlers={"morning_scan": lambda _context: called.append("entered")}, task_id=morning_task,
        clock=lambda: NOW, require_b76_contract=True,
    ) is None
    assert called == []
    assert store.get_task(task_id=morning_task, db_path=path).status == "queued"

    with _control_api(path) as client:
        draining = client.get("/api/v1/k10/operations/readiness")
    assert draining.status_code == 200
    draining_control = draining.json()["runControl"]
    assert draining_control["state"] == "paused"
    assert draining_control["executionState"] == "draining"
    assert draining_control["inFlightCount"] == 2
    assert draining_control["unknownCount"] == 0

    # The one already-admitted model reply may settle once even after pause;
    # exact repeat settlement cannot write a second receipt or usage record.
    first_settlement = store.settle_model_response_attempt(
        attempt_id=str(model["attemptId"]), request_sha256=_HASH_A, reuse_scope_sha256=_HASH_B,
        payload=_receipt(), outcome="succeeded", usage=_usage(), settled_at=(NOW + timedelta(seconds=3)).isoformat(),
        error_code=None, db_path=path,
    )
    assert first_settlement == {"state": "succeeded", "attemptId": model["attemptId"]}
    assert store.settle_model_response_attempt(
        attempt_id=str(model["attemptId"]), request_sha256=_HASH_A, reuse_scope_sha256=_HASH_B,
        payload=_receipt(), outcome="succeeded", usage=_usage(), settled_at=(NOW + timedelta(seconds=3)).isoformat(),
        error_code=None, db_path=path,
    ) == first_settlement
    assert store.settle_external_attempt(
        attempt_id=str(tavily["attemptId"]), outcome="unknown", usage=None,
        settled_at=(NOW + timedelta(seconds=3)).isoformat(), error_code=None, db_path=path,
    ) == {"state": "unknown", "attemptId": tavily["attemptId"]}
    assert store.external_attempt_summary(task_id=evening_task, db_path=path) == {
        "started": 0, "succeeded": 1, "failed": 0, "unknown": 1,
        "actualUsage": _usage(),
    }
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM k10_model_response_receipts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM k10_external_attempts").fetchone()[0] == 2

    # This direct terminal transition only constructs the paused maintenance
    # state; it is not a claim that a handler or report-publication transaction
    # ran.  The production worker non-claim above is the runtime boundary.
    store.finish_task(
        task_id=evening_task, worker_id="control-worker", status="failed", stage="paused", checkpoint={},
        error_text="K10 运行已暂停", finished_at=NOW + timedelta(seconds=4), db_path=path,
    )
    initialize_notifications_schema(path, applied_at=NOW)
    create_notification_maintenance(db_path=path, worker_id="control-maintenance")()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM k10_task_notifications").fetchone()[0] == 0

    with _control_api(path) as client:
        blocked = client.get("/api/v1/k10/operations/readiness")
    assert blocked.status_code == 200
    blocked_control = blocked.json()["runControl"]
    assert blocked_control["executionState"] == "blocked"
    assert blocked_control["inFlightCount"] == 0
    assert blocked_control["unknownCount"] == 1
    active_ids = {item["taskId"] for item in blocked_control["activeTasks"]}
    assert active_ids == {evening_task, morning_task, *maintenance_tasks}
    paused = next(item for item in blocked_control["activeTasks"] if item["taskId"] == evening_task)
    assert paused == {
        "taskId": evening_task, "status": "paused", "stage": None,
        "windowKind": "evening", "executionStartedAt": None,
    }
    # The reader DTO intentionally avoids a task-kind field.  Match its IDs to
    # the durable rows to prove every currently supported active kind survives
    # the projection instead of being filtered to scans.
    with sqlite3.connect(path) as connection:
        persisted_kinds = {
            row[0] for row in connection.execute(
                "SELECT kind FROM k10_tasks WHERE task_id IN ({})".format(
                    ",".join("?" for _ in active_ids)
                ), tuple(active_ids),
            )
        }
    assert persisted_kinds == {
        "evening_scan", "morning_scan", "analysis", "morning_review",
        "collect_market_day_fact", "evaluate_company_window",
    }
