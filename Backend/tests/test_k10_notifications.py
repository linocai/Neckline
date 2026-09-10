"""K10 notification outbox: explicit migration, idempotency, and recovery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from neckline.k10 import store
from neckline.k10.notifications import (
    DeliveryResult,
    NotificationConflict,
    NotificationRetryPolicy,
    NotificationSchemaUnavailable,
    dispatch_task_notifications,
    enqueue_task_notification,
    get_notification,
    initialize_notifications_schema,
)
from neckline.k10.schema import initialize_schema, read_connection
from neckline.k10.types import OpportunityPublicationInput
from neckline.k10.windows import SHANGHAI


NOW = datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc)
TEST_RETRY_POLICY = NotificationRetryPolicy(timedelta(seconds=30), timedelta(minutes=15))


def test_targeted_report_push_does_not_dispatch_old_failure_notifications(tmp_path):
    db = _db(tmp_path)
    _finish_task(db, task_id='old-failure', status='failed')
    old = enqueue_task_notification(task_id='old-failure', db_path=db, created_at=NOW)
    _finish_task(db, task_id='tonight', kind='evening_scan')
    target = enqueue_task_notification(task_id='tonight', db_path=db, created_at=NOW)
    calls = []
    def send(**kwargs):
        calls.append(kwargs['collapse_id'])
        return DeliveryResult(ok=True)
    args = dict(db_path=db, list_device_tokens=lambda:('device-a',), delete_device=lambda token:False,
        sender=send, worker_id='targeted', now=NOW+timedelta(minutes=2), retry_policy=TEST_RETRY_POLICY,
        notification_id=target.notification_id)
    assert dispatch_task_notifications(**args) == 1
    assert dispatch_task_notifications(**args) == 0
    assert calls == [target.notification_id]
    assert get_notification(notification_id=old.notification_id, db_path=db).status == 'queued'


def test_worker_maintenance_recovers_missing_terminal_hook_and_uses_device_preferences(tmp_path, monkeypatch):
    from neckline.api.stores import upsert_device
    from neckline.k10 import notification_runtime
    from neckline.push.apns import PushResult
    db = _db(tmp_path)
    monkeypatch.setattr(notification_runtime, "apns_readiness", lambda: SimpleNamespace(ready=True, code="ready"))
    _finish_task(db)
    upsert_device("synthetic-device", db_path=db)
    calls = []

    def fake_send(*args, **kwargs):
        calls.append((args, kwargs))
        return PushResult(ok=True, status=200, reason="ok")

    monkeypatch.setattr(notification_runtime, "send_push", fake_send)
    maintain = notification_runtime.create_notification_maintenance(db_path=db, worker_id="fixture-worker")
    maintain()
    maintain()
    assert len(calls) == 1
    assert calls[0][1]["custom"]["companyWindowId"] == "window-1"
    assert "untrusted" not in calls[0][1]["custom"]


def test_disabled_notification_never_calls_apns(tmp_path, monkeypatch):
    from neckline.api.stores import upsert_device
    from neckline.k10 import notification_runtime
    from neckline.settings_store import set_push_kinds
    from neckline.notify_kinds import ALL_KINDS
    db = _db(tmp_path)
    monkeypatch.setattr(notification_runtime, "apns_readiness", lambda: SimpleNamespace(ready=True, code="ready"))
    _finish_task(db)
    upsert_device("synthetic-device", db_path=db)
    set_push_kinds({kind: False for kind in ALL_KINDS}, db_path=db)
    calls = []
    monkeypatch.setattr(notification_runtime, "send_push", lambda *args, **kwargs: calls.append(True))
    notification_runtime.create_notification_maintenance(db_path=db, worker_id="fixture-worker")()
    assert calls == []


def test_morning_child_task_does_not_flood_notifications(tmp_path):
    from neckline.k10.notification_runtime import reconcile_terminal_notifications
    db = _db(tmp_path)
    _finish_task(db, kind="morning_review")
    assert reconcile_terminal_notifications(db_path=db, now=NOW) == 0


def _stamp(value: datetime = NOW) -> str:
    return value.isoformat(timespec="seconds")


def _db(tmp_path: Path) -> Path:
    db_path = tmp_path / "k10-notifications.sqlite3"
    initialize_schema(db_path)
    initialize_notifications_schema(db_path, applied_at=NOW)
    return db_path


def _finish_task(
    db_path: Path, *, task_id: str = "task-1", kind: str = "analysis", status: str = "completed",
    stage: str = "done", payload: dict[str, object] | None = None, error_text: str | None = None,
) -> int:
    store.enqueue_task(
        task_id=task_id,
        kind=kind,
        idempotency_key=f"enqueue-{task_id}",
        input_version="K10-v1.3",
        input_cutoff_at=_stamp(),
        payload=payload or {
            "companyWindowId": "window-1",
            "opportunityId": "opportunity-1",
            "batchId": "batch-1",
            "scanId": "scan-1",
            "untrusted": "not-a-deep-link",
        },
        budget={"maxAttempts": 2},
        created_at=_stamp(),
        db_path=db_path,
    )
    task = store.claim_tasks(
        worker_id="task-worker", now=NOW, lease_for=timedelta(minutes=5), limit=1, db_path=db_path,
    )[0]
    store.finish_task(
        task_id=task.task_id,
        worker_id="task-worker",
        status=status,
        stage=stage,
        checkpoint={},
        error_text=error_text,
        finished_at=NOW + timedelta(minutes=1),
        db_path=db_path,
    )
    return task.attempt_count


def _finish_real_window_analysis_task(db_path: Path) -> tuple[str, str]:
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES ('SSE',?,?)", [("20260906", 1), ("20260907", 1), ("20260908", 1)])
    stamp = "2026-09-07T09:20:00+08:00"
    store.create_scan(scan_id="scan-real", window_kind="morning", cutoff_at=stamp, config_id=None,
                      config_revision=None, status="completed", coverage={}, created_at=stamp,
                      completed_at=stamp, db_path=db_path)
    event = store.append_event_revision(event_id="event-real", stable_key="event-real", headline="真实窗口测试",
                                        event_kind="news", facts={}, source_refs=[], supersedes_revision=None,
                                        created_at=stamp, db_path=db_path)
    store.create_candidate(candidate_id="candidate-real", scan_id="scan-real", event_id=event.event_id,
                           event_revision=event.revision, company_code="300001.SZ", comparison={"rank": 1},
                           evidence=[], created_at=stamp, db_path=db_path)
    comparison = {
        "summary": "完整比较", "differences": {"role": "primary", "priorityReason": "资料", "gap": "差异",
        "rankChangeConditions": "反证条件", "twoDayReason": "新事实"}, "evidenceRefs": [], "rank": 1,
        "classification": {"kind": "initial", "opportunityKey": "real", "reason": "首发", "newFacts": "资料",
        "changedJudgment": None, "twoDayReason": "新事实", "relatedOpportunityId": None},
    }
    store.publish_opportunities(
        batch_id="batch-real", scan_id="scan-real", publication_kind="morning",
        inputs=(OpportunityPublicationInput(candidate_id="candidate-real", company_code="300001.SZ", event_id="event-real",
            event_revision=1, opportunity_key="real", catalyst_stage="initial", category="primary",
            comparison=comparison, evidence_refs=(), source_marker="morning"),), db_path=db_path,
        clock=lambda: datetime(2026, 9, 7, 9, 29, tzinfo=SHANGHAI),
    )
    window = store.list_company_windows(db_path=db_path)[0]
    opportunity = store.list_opportunities(batch_id="batch-real", db_path=db_path)[0]
    store.observe_company_window(
        action_id="keep-real", observation_id="observation-real", task_id="task-real", outbox_id="outbox-real",
        company_window_id=window["companyWindowId"], idempotency_key="keep-real", task_input_version="cfg@1",
        task_input_cutoff_at=stamp, task_payload={"opportunityId": opportunity["opportunityId"], "batchId": "batch-real",
        "scanId": "scan-real"}, task_budget={"maxAttempts": 1}, created_at=stamp, db_path=db_path,
    )
    task = store.claim_tasks(worker_id="task-worker", now=NOW, lease_for=timedelta(minutes=5), limit=1, db_path=db_path)[0]
    store.finish_task(task_id=task.task_id, worker_id="task-worker", status="completed", stage="done", checkpoint={},
                      error_text=None, finished_at=NOW + timedelta(minutes=1), db_path=db_path)
    return window["companyWindowId"], opportunity["opportunityId"]


def test_terminal_hook_is_idempotent_and_payload_is_whitelisted(tmp_path: Path):
    db_path = _db(tmp_path)
    _finish_task(db_path)

    first = enqueue_task_notification(task_id="task-1", db_path=db_path, created_at=NOW)
    duplicate = enqueue_task_notification(task_id="task-1", db_path=db_path, created_at=NOW + timedelta(minutes=1))

    assert first.notification_id == duplicate.notification_id
    assert first.kind == "k10_analysis"
    assert first.task_attempt_count == 1
    assert first.deep_link == {
        "companyWindowId": "window-1",
        "opportunityId": "opportunity-1",
        "batchId": "batch-1",
        "scanId": "scan-1",
    }

    sent: list[tuple[str, str]] = []

    def sender(**kwargs):
        sent.append((kwargs["token"], kwargs["collapse_id"]))
        return DeliveryResult(ok=True)

    assert dispatch_task_notifications(
        db_path=db_path, list_device_tokens=lambda: ("device-a", "device-a"), delete_device=lambda _: False,
        sender=sender, worker_id="push-worker", now=NOW + timedelta(minutes=2), retry_policy=TEST_RETRY_POLICY,
    ) == 1
    assert sent == [("device-a", first.notification_id)]
    assert get_notification(notification_id=first.notification_id, db_path=db_path).status == "sent"


def test_real_company_window_analysis_task_produces_only_v14_push_ids(tmp_path, monkeypatch):
    from neckline.api.stores import upsert_device
    from neckline.k10 import notification_runtime

    db_path = _db(tmp_path)
    monkeypatch.setattr(notification_runtime, "apns_readiness", lambda: SimpleNamespace(ready=True, code="ready"))
    company_window_id, opportunity_id = _finish_real_window_analysis_task(db_path)
    upsert_device("synthetic-device", db_path=db_path)
    calls = []
    monkeypatch.setattr(notification_runtime, "send_push", lambda *args, **kwargs: calls.append((args, kwargs)) or type("Result", (), {"ok": True, "reason": "ok"})())

    notification_runtime.create_notification_maintenance(db_path=db_path, worker_id="fixture-worker")()

    assert len(calls) == 1
    assert calls[0][1]["custom"] == {"kind": "k10_analysis", "companyWindowId": company_window_id,
                                      "opportunityId": opportunity_id, "batchId": "batch-real", "scanId": "scan-real"}
    assert "价位" not in calls[0][0][2] and "预案" not in calls[0][0][2]


def test_legacy_queued_and_expired_sending_rows_are_normalized_only_at_push_boundary(tmp_path):
    db_path = _db(tmp_path)
    _finish_task(db_path, task_id="queued-task")
    _finish_task(db_path, task_id="expired-task")
    queued = enqueue_task_notification(task_id="queued-task", db_path=db_path, created_at=NOW)
    expired = enqueue_task_notification(task_id="expired-task", db_path=db_path, created_at=NOW)
    legacy_link = json.dumps({"observationId": "obsolete-observation", "companyCandidateId": "obsolete-candidate",
                              "scanId": "scan-1"})
    legacy_body = "可查看正反全文，以及资料与价位草案的完成状态。"
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE k10_task_notifications SET deep_link_json=?,body=? WHERE notification_id=?",
                     (legacy_link, legacy_body, queued.notification_id))
        conn.execute("UPDATE k10_task_notifications SET deep_link_json=?,body=?,status='sending',lease_owner='old-worker',lease_until=? WHERE notification_id=?",
                     (legacy_link, legacy_body, _stamp(NOW - timedelta(minutes=1)), expired.notification_id))

    # A duplicate terminal hook after the upgrade must accept a recognizable B31
    # row and expose the current IDs from the immutable task payload.
    replayed = enqueue_task_notification(task_id="queued-task", db_path=db_path, created_at=NOW + timedelta(minutes=1))
    assert replayed.deep_link == {"companyWindowId": "window-1", "opportunityId": "opportunity-1",
                                  "batchId": "batch-1", "scanId": "scan-1"}

    outbound: list[tuple[dict[str, str], str]] = []
    assert dispatch_task_notifications(
        db_path=db_path, list_device_tokens=lambda: ("device-a",), delete_device=lambda _: False,
        sender=lambda **kwargs: outbound.append((kwargs["deep_link"], kwargs["body"])) or DeliveryResult(ok=True),
        worker_id="push-worker", now=NOW + timedelta(minutes=2), retry_policy=TEST_RETRY_POLICY,
    ) == 2
    assert [deep_link for deep_link, _body in outbound] == [{"scanId": "scan-1"}, {"scanId": "scan-1"}]
    assert all("observationId" not in deep_link and "companyCandidateId" not in deep_link for deep_link, _body in outbound)
    assert all("价位" not in body and "预案" not in body for _deep_link, body in outbound)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", "another-task"),
        ("kind", "k10_morning"),
        ("terminal_status", "failed"),
        ("task_attempt_count", 2),
        ("title", "已被篡改"),
        ("deep_link_json", json.dumps({"observationId": "old", "companyCandidateId": "old", "scanId": "wrong-scan"})),
    ],
)
def test_legacy_notification_idempotency_does_not_hide_non_migration_field_changes(tmp_path, field, value):
    case = tmp_path / field
    case.mkdir()
    db_path = _db(case)
    _finish_task(db_path)
    notification = enqueue_task_notification(task_id="task-1", db_path=db_path, created_at=NOW)
    legacy_link = json.dumps({"observationId": "old", "companyCandidateId": "old", "scanId": "scan-1"})
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE k10_task_notifications SET deep_link_json=?,body=?,{field}=? WHERE notification_id=?",
            (legacy_link, "可查看正反全文，以及资料与价位草案的完成状态。", value, notification.notification_id),
        )

    with pytest.raises(NotificationConflict):
        enqueue_task_notification(task_id="task-1", db_path=db_path, created_at=NOW + timedelta(minutes=1))


def test_failure_after_retry_creates_a_new_attempt_notification(tmp_path: Path):
    db_path = _db(tmp_path)
    first_attempt = _finish_task(db_path, status="failed", stage="execution", error_text="Bearer private-value")
    first = enqueue_task_notification(task_id="task-1", db_path=db_path, created_at=NOW)
    assert first_attempt == first.task_attempt_count == 1
    assert "private-value" not in first.body
    assert "任何交易操作" in first.body

    store.retry_task(
        task_id="task-1", expected_attempt_count=first_attempt, retried_at=_stamp(NOW + timedelta(minutes=2)), db_path=db_path,
    )
    task = store.claim_tasks(
        worker_id="task-worker", now=NOW + timedelta(minutes=3), lease_for=timedelta(minutes=5), limit=1, db_path=db_path,
    )[0]
    assert task.attempt_count == 2
    store.finish_task(
        task_id=task.task_id, worker_id="task-worker", status="failed", stage="execution", checkpoint={},
        error_text="another private failure", finished_at=NOW + timedelta(minutes=4), db_path=db_path,
    )
    second = enqueue_task_notification(task_id="task-1", db_path=db_path, created_at=NOW + timedelta(minutes=4))

    assert second.notification_id != first.notification_id
    assert second.task_attempt_count == 2
    assert second.kind == "k10_failure"


def test_transient_delivery_retries_and_invalid_token_is_removed(tmp_path: Path):
    db_path = _db(tmp_path)
    _finish_task(db_path)
    notification = enqueue_task_notification(task_id="task-1", db_path=db_path, created_at=NOW)
    calls: list[str] = []
    deleted: list[str] = []
    devices = ["active", "retired"]

    def flaky_sender(**kwargs):
        calls.append(kwargs["token"])
        if len(calls) == 1:
            return DeliveryResult(ok=False, reason="temporary outage")
        if kwargs["token"] == "retired":
            return DeliveryResult(ok=False, permanent_invalid=True, reason="Unregistered")
        return DeliveryResult(ok=True)

    def delete_device(token: str) -> bool:
        deleted.append(token)
        devices.remove(token)
        return True

    common = dict(
        db_path=db_path, list_device_tokens=lambda: tuple(devices), delete_device=delete_device,
        sender=flaky_sender, worker_id="push-worker", retry_policy=TEST_RETRY_POLICY,
    )
    assert dispatch_task_notifications(**common, now=NOW + timedelta(minutes=2)) == 1
    assert get_notification(notification_id=notification.notification_id, db_path=db_path).status == "queued"
    assert dispatch_task_notifications(**common, now=NOW + timedelta(minutes=3)) == 1
    assert calls == ["active", "retired", "active"]
    assert deleted == ["retired"]
    assert get_notification(notification_id=notification.notification_id, db_path=db_path).status == "sent"


def test_no_devices_marks_the_logical_notification_sent(tmp_path: Path):
    db_path = _db(tmp_path)
    _finish_task(db_path, kind="scan", payload={"windowKind": "morning", "scanId": "scan-1"})
    notification = enqueue_task_notification(task_id="task-1", db_path=db_path, created_at=NOW)

    assert dispatch_task_notifications(
        db_path=db_path, list_device_tokens=lambda: (), delete_device=lambda _: False,
        sender=lambda **_: pytest.fail("no device means no APNs call"), worker_id="push-worker", now=NOW + timedelta(minutes=2),
        retry_policy=TEST_RETRY_POLICY,
    ) == 1
    assert notification.kind == "k10_morning"
    assert get_notification(notification_id=notification.notification_id, db_path=db_path).status == "sent"


def test_crash_recovery_reuses_stable_collapse_id(tmp_path: Path):
    db_path = _db(tmp_path)
    _finish_task(db_path)
    notification = enqueue_task_notification(task_id="task-1", db_path=db_path, created_at=NOW)
    collapse_ids: list[str] = []

    def crashed_sender(**kwargs):
        collapse_ids.append(kwargs["collapse_id"])
        raise SystemExit("simulated process crash")

    with pytest.raises(SystemExit):
        dispatch_task_notifications(
            db_path=db_path, list_device_tokens=lambda: ("device-a",), delete_device=lambda _: False,
            sender=crashed_sender, worker_id="push-worker", now=NOW + timedelta(minutes=2), lease_for=timedelta(seconds=5),
            retry_policy=TEST_RETRY_POLICY,
        )

    def recovered_sender(**kwargs):
        collapse_ids.append(kwargs["collapse_id"])
        return DeliveryResult(ok=True)

    assert dispatch_task_notifications(
        db_path=db_path, list_device_tokens=lambda: ("device-a",), delete_device=lambda _: False,
        sender=recovered_sender, worker_id="recovery-worker", now=NOW + timedelta(minutes=3), lease_for=timedelta(seconds=5),
        retry_policy=TEST_RETRY_POLICY,
    ) == 1
    assert collapse_ids == [notification.notification_id, notification.notification_id]
    assert get_notification(notification_id=notification.notification_id, db_path=db_path).status == "sent"


def test_read_does_not_create_notification_schema(tmp_path: Path):
    db_path = tmp_path / "base-k10.sqlite3"
    initialize_schema(db_path)

    with pytest.raises(NotificationSchemaUnavailable):
        get_notification(notification_id="missing", db_path=db_path)

    with read_connection(db_path) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='k10_task_notifications'"
        ).fetchone() is None


def test_missing_apns_blocks_without_attempt_then_recovers(tmp_path, monkeypatch):
    from neckline.api.stores import upsert_device
    from neckline.k10 import notification_runtime

    db_path = _db(tmp_path)
    _finish_task(db_path)
    notification = enqueue_task_notification(task_id="task-1", db_path=db_path, created_at=NOW)
    upsert_device("synthetic-device", db_path=db_path)
    calls: list[object] = []
    monkeypatch.setattr(notification_runtime, "send_push", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(notification_runtime, "apns_readiness", lambda: SimpleNamespace(ready=False, code="key_unreadable"))
    maintain = notification_runtime.create_notification_maintenance(db_path=db_path, worker_id="fixture-worker")

    maintain(); maintain()
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT attempt_count,next_attempt_at,blocked_reason FROM k10_task_notifications WHERE notification_id=?",
                           (notification.notification_id,)).fetchone()
    assert row == (0, None, "key_unreadable")
    health = notification_runtime.notification_readiness(db_path=db_path, now=NOW)
    assert health.state == "blocked" and health.reason_code == "key_unreadable"
    assert calls == []

    monkeypatch.setattr(notification_runtime, "apns_readiness", lambda: SimpleNamespace(ready=True, code="ready"))
    monkeypatch.setattr(notification_runtime, "send_push", lambda *a, **k: calls.append((a, k)) or type("Result", (), {"ok": True, "reason": "ok"})())
    maintain()
    assert len(calls) == 1
    assert get_notification(notification_id=notification.notification_id, db_path=db_path).status == "sent"


def test_configuration_recovery_releases_only_bounded_outbox_batch(tmp_path, monkeypatch):
    from neckline.api.stores import upsert_device
    from neckline.k10 import notification_runtime

    db_path = _db(tmp_path)
    notifications = []
    for index in range(5):
        task_id = f"task-{index}"
        _finish_task(db_path, task_id=task_id)
        notifications.append(enqueue_task_notification(task_id=task_id, db_path=db_path, created_at=NOW))
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE k10_task_notifications SET attempt_count=20448 WHERE notification_id=?",
                     (notifications[0].notification_id,))
    upsert_device("synthetic-device", db_path=db_path)
    sent: list[object] = []
    monkeypatch.setattr(notification_runtime, "send_push", lambda *a, **k: sent.append((a, k)) or type("Result", (), {"ok": True, "reason": "ok"})())
    monkeypatch.setattr(notification_runtime, "apns_readiness", lambda: SimpleNamespace(ready=False, code="key_unreadable"))
    maintain = notification_runtime.create_notification_maintenance(db_path=db_path, worker_id="fixture-worker")
    maintain()
    assert sent == []

    monkeypatch.setattr(notification_runtime, "apns_readiness", lambda: SimpleNamespace(ready=True, code="ready"))
    maintain()
    assert len(sent) == 4  # recovery never drains an accumulated backlog in one worker tick
    maintain()
    assert len(sent) == 5


def test_transient_backoff_is_persisted_and_capped_for_large_legacy_attempt_count(tmp_path: Path):
    db_path = _db(tmp_path)
    _finish_task(db_path)
    notification = enqueue_task_notification(task_id="task-1", db_path=db_path, created_at=NOW)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE k10_task_notifications SET attempt_count=20448 WHERE notification_id=?", (notification.notification_id,))
    sender = lambda **_: DeliveryResult(ok=False, reason="network unavailable")
    assert dispatch_task_notifications(
        db_path=db_path, list_device_tokens=lambda: ("device-a",), delete_device=lambda _: False,
        sender=sender, worker_id="push-worker", now=NOW, retry_policy=TEST_RETRY_POLICY,
    ) == 1
    with sqlite3.connect(db_path) as conn:
        retry_at, error, attempts = conn.execute(
            "SELECT next_attempt_at,last_error,attempt_count FROM k10_task_notifications WHERE notification_id=?",
            (notification.notification_id,),
        ).fetchone()
    assert error == "delivery_retry_needed" and attempts == 20449
    assert datetime.fromisoformat(retry_at) == NOW + timedelta(minutes=15)
    assert dispatch_task_notifications(
        db_path=db_path, list_device_tokens=lambda: ("device-a",), delete_device=lambda _: False,
        sender=sender, worker_id="push-worker", now=NOW + timedelta(minutes=14, seconds=59), retry_policy=TEST_RETRY_POLICY,
    ) == 0


def test_v1_outbox_migration_preserves_queued_row_and_marks_it_due(tmp_path: Path):
    from neckline.k10 import notifications as notification_module

    db_path = tmp_path / "v1.sqlite3"
    initialize_schema(db_path)
    _finish_task(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(notification_module._V1)
        conn.execute("INSERT INTO k10_notification_schema_migrations(version,applied_at) VALUES(1,?)", (_stamp(),))
        conn.execute(
            "INSERT INTO k10_task_notifications(notification_id,idempotency_key,task_id,task_attempt_count,kind,terminal_status,"
            "title,body,deep_link_json,status,attempt_count,last_error,lease_owner,lease_until,created_at,updated_at) "
            "VALUES('old-notification','old-key','task-1',1,'k10_analysis','completed','t','b','{}','queued',7,NULL,NULL,NULL,?,?)",
            (_stamp(), _stamp()),
        )
    assert initialize_notifications_schema(db_path, applied_at=NOW) == 2
    with sqlite3.connect(db_path) as conn:
        version = conn.execute("SELECT MAX(version) FROM k10_notification_schema_migrations").fetchone()[0]
        row = conn.execute("SELECT attempt_count,next_attempt_at,blocked_reason FROM k10_task_notifications WHERE notification_id='old-notification'").fetchone()
    assert version == 2 and row == (7, _stamp(), None)


def test_api_and_worker_startup_gate_main_schema_and_notification_schema(tmp_path, monkeypatch):
    """A release must not start an API/worker on main schema 4 with stale notification schema."""
    import dataclasses
    from fastapi.testclient import TestClient
    import neckline.api.app as app_module
    import neckline.api.deps as deps_module
    from neckline.config import Settings
    from neckline.db import init_schema

    db_path = tmp_path / "startup-gate.sqlite"
    isolated = dataclasses.replace(Settings(tushare_token=None), api_token="test-api-token-1234", db_path=db_path)
    monkeypatch.setattr(app_module, "_DB_PATH_OVERRIDE", db_path)
    monkeypatch.setattr(deps_module, "settings", isolated)
    init_schema(db_path)
    initialize_schema(db_path)

    with pytest.raises(NotificationSchemaUnavailable):
        with TestClient(app_module.app):
            pass

    initialize_notifications_schema(db_path)
    with TestClient(app_module.app) as client:
        assert client.get("/api/v1/health").status_code == 200


def test_delivery_runtime_config_is_explicit_and_reports_missing_file(tmp_path, monkeypatch):
    from neckline.k10 import notification_runtime

    config = notification_runtime.load_notification_delivery_config()
    assert config.retry_policy.initial_delay == timedelta(seconds=30)
    assert config.retry_policy.maximum_delay == timedelta(minutes=15)
    assert config.dispatch_batch_size == 4

    db_path = _db(tmp_path)
    monkeypatch.setattr(notification_runtime, "_NOTIFICATION_RUNTIME_CONFIG_PATH", tmp_path / "missing.json")
    state = notification_runtime.notification_readiness(db_path=db_path, now=NOW)
    assert state.state == "notConfigured" and state.reason_code == "notification_runtime_config_missing"
