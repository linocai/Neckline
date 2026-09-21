"""B76 report notification boundaries; all data and transports are isolated."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from neckline.k10 import store
from neckline.k10.delivery import delivery_gap, delivery_manifest, digest, runtime_contract
from neckline.k10.notification_runtime import reconcile_terminal_notifications
from neckline.k10.notifications import (
    DeliveryResult, NotificationConflict, NotificationRetryPolicy,
    dispatch_task_notifications, enqueue_task_notification, enqueue_committed_report_notification, initialize_notifications_schema,
)
from neckline.k10.schema import initialize_schema


NOW = datetime(2026, 9, 16, 2, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat(timespec="seconds")
RETRY_POLICY = NotificationRetryPolicy(timedelta(seconds=1), timedelta(seconds=2))


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "b76-notifications.sqlite"
    initialize_schema(path)
    initialize_notifications_schema(path, applied_at=NOW)
    store.set_run_control(
        state="open", reason_code="isolated-b76-notification-test", changed_at=STAMP,
        changed_by="test", db_path=path,
    )
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        # This is the smallest valid V2 ancestry needed to emulate a committed
        # report row.  It does not import profiles or contact a provider.
        conn.execute(
            "INSERT INTO k10_run_config_revisions VALUES (?,?,?,?,?)",
            ("cfg", 1, '{"configVersion":"k10-v2","strategySnapshotId":"strategy"}', "c" * 64, STAMP),
        )
        conn.execute(
            "INSERT INTO k10_execution_config_revisions VALUES (?,?,?,?,?)",
            ("execution", 1, '{}', "e" * 64, STAMP),
        )
        conn.execute(
            "INSERT INTO k10_v2_universe_snapshots VALUES (?,?,?,?,?)",
            ("universe", "K10-v2", "u" * 64, '{"stocks":[]}', STAMP),
        )
        conn.execute(
            "INSERT INTO k10_v2_profile_snapshots VALUES (?,?,?,?,?)",
            ("profiles", "universe", '{"fixture":"only"}', "local_draft_awaiting_user", STAMP),
        )
        conn.execute(
            "INSERT INTO k10_v2_strategy_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("strategy", "K10-v2", "s" * 64, "universe", "profiles", "cfg", 1, "execution", 1, '{}', STAMP),
        )
    return path


def _delivery(*, partial: bool) -> dict:
    counts = {
        "titleInput": 1 if partial else 0,
        "titleProcessed": 0,
        "titleFailed": 0,
        "titleUnprocessed": 1 if partial else 0,
        "eventInput": 0,
        "eventProcessed": 0,
        "eventFailed": 0,
        "eventUnprocessed": 0,
        "comparableCompanies": 0,
        "publishedCompanies": 0,
    }
    gaps = [] if not partial else [delivery_gap(
        stage="research", unit_kind="event", unit_id="event-refused", reason_code="content_policy_refused",
        message="该事件的研究执行未完成，相关公司不参与本轮聚合推荐。",
        company_scope_known=False,
    )]
    return delivery_manifest(
        outcome="partial" if partial else "complete",
        ranking_scope="none" if partial else "all_processed",
        counts=counts, gaps=gaps, input_manifest=[], eligible_set=[],
        ranking_input=None if partial else [],
    )


def _terminal_task(
    path: Path, *, task_id: str, status: str, stage: str, checkpoint: dict,
    kind: str = "evening_scan", payload: dict | None = None,
) -> None:
    payload = payload or {"windowKind": "evening", "runtimeContract": runtime_contract()}
    store.enqueue_task(
        task_id=task_id, kind=kind, idempotency_key="enqueue-" + task_id, input_version="fixture",
        input_cutoff_at=STAMP, payload=payload, budget={"maxAttempts": 2}, created_at=STAMP, db_path=path,
    )
    task = store.claim_tasks(
        worker_id="fixture-worker", now=NOW, lease_for=timedelta(minutes=2), limit=1, db_path=path,
    )[0]
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE k10_tasks SET status=?,stage=?,checkpoint_json=?,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE task_id=?",
            (status, stage, json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")), STAMP, task.task_id),
        )


def _committed_report(
    path: Path, *, scan_id: str, delivery: dict, coverage_delivery: dict | None = None,
    window_kind: str = "evening", report_status: str | None = None,
    scan_status: str | None = None,
    ranking_input: object | None = None, card_identities: list[dict] | None = None,
) -> None:
    if ranking_input is None and delivery["rankingInputSha256"] == digest([]):
        ranking_input = []
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO k10_scans VALUES (?,?,?,?,?,?,?,?,?)",
            (scan_id, window_kind, STAMP, "cfg", 1,
             scan_status or ("completed" if delivery["outcome"] == "complete" else "partial"),
             json.dumps({"delivery": delivery, "rankingInput": ranking_input},
                        ensure_ascii=False, sort_keys=True, separators=(",", ":")), STAMP, STAMP),
        )
        conn.execute(
            "INSERT INTO k10_v2_report_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("report_" + scan_id, scan_id, "strategy", window_kind, None, STAMP, STAMP, STAMP,
             report_status or ("completed" if delivery["outcome"] == "complete" else "partial"), None, STAMP),
        )
        conn.execute(
            "INSERT INTO k10_v2_report_coverage VALUES (?,?)",
            ("report_" + scan_id, json.dumps({"coverageGaps": [], "incompleteReviews": [],
                                                "delivery": coverage_delivery if coverage_delivery is not None else delivery},
                                               ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        )
    # The notification reader validates the durable identity only.  These
    # fixture rows intentionally omit unrelated opportunity ancestry.
    with sqlite3.connect(path) as conn:
        for rank, identity in enumerate(card_identities or [], 1):
            company_code = str(identity["companyCode"])
            conn.execute(
                "INSERT INTO k10_v2_report_cards VALUES (?,?,?,?,?,?,?,?,?)",
                (f"card-{scan_id}-{rank}", "report_" + scan_id, company_code, "fixture", rank,
                 "evening" if window_kind == "evening" else "added", "fixture-window",
                 json.dumps({"deliveryIdentity": identity}, ensure_ascii=False, sort_keys=True, separators=(",", ":")), STAMP),
            )


def _outbox_count(path: Path, task_id: str) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute("SELECT count(*) FROM k10_task_notifications WHERE task_id=?", (task_id,)).fetchone()[0])


def _publish_notification(path, task_id):
    # Unit fixture for an already-validated publication. Real CLI transaction
    # and card/manifest corruption are tested by test_v350_publication.py.
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        return enqueue_committed_report_notification(conn, task_id=task_id, created_at=NOW)


def test_b78_atomic_report_outbox_dispatches_once_and_ignores_attempt_counter(tmp_path):
    path = _db(tmp_path)
    delivery = _delivery(partial=False)
    task_id, scan_id = "task-b78-complete", "scan-b78-complete"
    _terminal_task(path, task_id=task_id, status="completed", stage="report_complete", checkpoint={
        "scanId": scan_id, "delivery": delivery, "deliveryPublicationAtomic": True,
    })
    assert reconcile_terminal_notifications(db_path=path, now=NOW) == 0
    _committed_report(path, scan_id=scan_id, delivery=delivery)
    assert reconcile_terminal_notifications(db_path=path, now=NOW) == 0
    with pytest.raises(NotificationConflict):
        enqueue_task_notification(task_id=task_id, db_path=path, created_at=NOW)
    original = _publish_notification(path, task_id)
    assert original.title == "K10 晚间报告已就绪"
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE k10_tasks SET attempt_count=attempt_count+1 WHERE task_id=?", (task_id,))
    assert enqueue_task_notification(task_id=task_id, db_path=path, created_at=NOW).notification_id == original.notification_id
    assert reconcile_terminal_notifications(db_path=path, now=NOW) == 0
    sent = []
    assert dispatch_task_notifications(db_path=path, list_device_tokens=lambda: ("synthetic-token",),
        delete_device=lambda _token: False, sender=lambda **kwargs: sent.append(kwargs) or DeliveryResult(ok=True),
        worker_id="fixture-notifier", now=NOW, retry_policy=RETRY_POLICY) == 1
    assert sent[0]["deep_link"] == {"scanId": scan_id, "reportId": "report_" + scan_id, "windowKind": "evening"}


def test_b78_partial_notification_reads_public_report_and_rejects_corruption(tmp_path):
    path = _db(tmp_path)
    delivery = _delivery(partial=True)
    task_id, scan_id = "task-b78-partial", "scan-b78-partial"
    _terminal_task(path, task_id=task_id, status="completed", stage="report_partial", checkpoint={
        "scanId": scan_id, "delivery": delivery, "deliveryPublicationAtomic": True,
    })
    malformed = {**delivery, "counts": {**delivery["counts"], "titleUnprocessed": 0}}
    _committed_report(path, scan_id=scan_id, delivery=delivery, coverage_delivery=malformed)
    with pytest.raises(NotificationConflict):
        _publish_notification(path, task_id)
    assert _outbox_count(path, task_id) == 0
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE k10_v2_report_coverage SET content_json=? WHERE report_id=?",
            (json.dumps({"delivery": delivery}), "report_" + scan_id))
    notice = _publish_notification(path, task_id)
    assert notice.title == "K10 晚间报告已发布（含执行缺口）"
    assert "独立完成内容" in notice.body and "缺口" in notice.body
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE k10_v2_report_coverage SET content_json='{}' WHERE report_id=?", ("report_" + scan_id,))
    with pytest.raises(NotificationConflict):
        enqueue_task_notification(task_id=task_id, db_path=path, created_at=NOW)


def test_b76_morning_partial_aggregate_notifies_as_gap_with_immutable_complete_delivery(tmp_path: Path):
    path = _db(tmp_path)
    delivery = _delivery(partial=False)
    task_id, scan_id, morning_id = "task-b76-morning", "scan-b76-morning", "morning-b76-fixture"
    _terminal_task(path, task_id=task_id, kind="morning_scan", status="completed", stage="report_partial", checkpoint={
        "scanId": scan_id, "delivery": delivery, "deliveryPublicationAtomic": True,
        "morningReportId": morning_id, "morningReportRevision": 1, "morningReviewState": "partial",
        "morningAggregateStatus": "partial",
    }, payload={"windowKind": "morning", "runtimeContract": runtime_contract()})
    _committed_report(path, scan_id=scan_id, delivery=delivery, window_kind="morning",
                      report_status="partial", scan_status="partial")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO k10_morning_reports VALUES (?,?,?,?,?,?,?,?)",
            (morning_id, scan_id, 1, STAMP, STAMP, "partial", '{"gaps":["review_incomplete"]}', STAMP),
        )

    _publish_notification(path, task_id)
    assert reconcile_terminal_notifications(db_path=path, now=NOW) == 0
    with sqlite3.connect(path) as conn:
        title, body, key = conn.execute(
            "SELECT title,body,idempotency_key FROM k10_task_notifications WHERE task_id=?", (task_id,)
        ).fetchone()
    assert title == "K10 晨间报告已发布（含执行缺口）" and "缺口" in body
    assert key.endswith(":" + digest(delivery))


def test_b76_real_atomic_publication_reconciles_through_card_identity_readback(tmp_path: Path, monkeypatch):
    """Exercise the actual CLI/worker publication rows with socket-denied fixtures."""
    from .v340_acceptance_fixture import NOW as FLOW_NOW, run_full_scale_flow

    from . import v340_acceptance_fixture as base
    from .test_v350_cli_api import DirectRoundTransport
    monkeypatch.setattr(base, "TITLE_COUNT", 130)
    monkeypatch.setattr(base, "DeterministicTransport", DirectRoundTransport)
    flow = run_full_scale_flow(tmp_path, monkeypatch, name="notification-readback", selected_event_count=1)
    assert flow.task_status == "completed"
    initialize_notifications_schema(flow.db_path, applied_at=FLOW_NOW)
    assert reconcile_terminal_notifications(db_path=flow.db_path, now=FLOW_NOW) == 0
    with sqlite3.connect(flow.db_path) as conn:
        kind, title, key = conn.execute(
            "SELECT kind,title,idempotency_key FROM k10_task_notifications WHERE task_id=?", (flow.task_id,)
        ).fetchone()
    assert kind == "k10_evening" and title == "K10 晚间报告已就绪"
    assert key.startswith("k10-report-notification:report_")


def test_b76_failure_is_not_repeated_by_attempt_polling_but_explicit_recovery_is_new_incident(tmp_path: Path):
    path = _db(tmp_path)
    task_id = "task-b76-failure"
    _terminal_task(path, task_id=task_id, status="failed", stage="execution", checkpoint={})

    assert reconcile_terminal_notifications(db_path=path, now=NOW) == 1
    with sqlite3.connect(path) as conn:
        first_key = conn.execute(
            "SELECT idempotency_key FROM k10_task_notifications WHERE task_id=?", (task_id,)
        ).fetchone()[0]
        # An attempt counter by itself is not evidence of a new user-visible incident.
        conn.execute("UPDATE k10_tasks SET attempt_count=attempt_count+1,updated_at=? WHERE task_id=?", (STAMP, task_id))
    assert reconcile_terminal_notifications(db_path=path, now=NOW + timedelta(seconds=1)) == 0
    assert _outbox_count(path, task_id) == 1

    recovery = {
        "scanId": "scan-b76-failure", "frozenInputSha256": "a" * 64,
        "authorizedAt": (NOW + timedelta(minutes=1)).isoformat(timespec="seconds"), "previousAttemptCount": 1,
    }
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE k10_tasks SET checkpoint_json=? WHERE task_id=?", (json.dumps({"recoveryAuthorized": recovery}), task_id))
    assert reconcile_terminal_notifications(db_path=path, now=NOW + timedelta(minutes=1)) == 1
    assert reconcile_terminal_notifications(db_path=path, now=NOW + timedelta(minutes=1, seconds=1)) == 0
    with sqlite3.connect(path) as conn:
        keys = [row[0] for row in conn.execute(
            "SELECT idempotency_key FROM k10_task_notifications WHERE task_id=? ORDER BY notification_id", (task_id,)
        )]
    assert len(keys) == 2 and first_key in keys and any(":recovery:" in key for key in keys)


def test_b76_failed_delivery_diagnostic_allows_one_safe_failure_push(tmp_path: Path):
    path = _db(tmp_path)
    failed_delivery = delivery_manifest(
        outcome="failed", ranking_scope="none",
        counts={
            "titleInput": 1, "titleProcessed": 0, "titleFailed": 1, "titleUnprocessed": 0,
            "eventInput": 0, "eventProcessed": 0, "eventFailed": 0, "eventUnprocessed": 0,
            "comparableCompanies": 0, "publishedCompanies": 0,
        },
        gaps=[], input_manifest=["frozen"], eligible_set=[], ranking_input=None,
    )
    _terminal_task(
        path, task_id="task-b76-failed-diagnostic", status="failed", stage="execution",
        checkpoint={"scanId": "scan-b76-failed-diagnostic", "delivery": failed_delivery},
    )

    assert reconcile_terminal_notifications(db_path=path, now=NOW) == 1
    with sqlite3.connect(path) as conn:
        key, title = conn.execute(
            "SELECT idempotency_key,title FROM k10_task_notifications WHERE task_id=?",
            ("task-b76-failed-diagnostic",),
        ).fetchone()
        conn.execute("UPDATE k10_tasks SET attempt_count=attempt_count+1 WHERE task_id=?", ("task-b76-failed-diagnostic",))
    assert ":failure:failed:initial" in key and title == "K10 报告未交付"
    assert reconcile_terminal_notifications(db_path=path, now=NOW + timedelta(seconds=1)) == 0


def test_b76_analysis_keeps_its_terminal_notification_while_internal_tasks_do_not_auto_push(tmp_path: Path):
    path = _db(tmp_path)
    _terminal_task(
        path, task_id="task-b76-analysis", kind="analysis", status="completed", stage="done", checkpoint={},
        payload={"runtimeContract": runtime_contract(), "observationId": "observation-fixture"},
    )
    _terminal_task(
        path, task_id="task-b76-morning-review", kind="morning_review", status="completed", stage="done", checkpoint={},
        payload={"runtimeContract": runtime_contract(), "windowKind": "morning"},
    )
    _terminal_task(
        path, task_id="task-b76-evaluation", kind="evaluate_company_window", status="completed", stage="done", checkpoint={},
        payload={"runtimeContract": runtime_contract()},
    )

    assert reconcile_terminal_notifications(db_path=path, now=NOW) == 1
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT task_id,kind FROM k10_task_notifications ORDER BY task_id").fetchall()
    assert rows == [("task-b76-analysis", "k10_analysis")]
    assert reconcile_terminal_notifications(db_path=path, now=NOW + timedelta(seconds=1)) == 0


def test_b76_reconciliation_skips_more_than_one_page_of_already_pushed_analysis_tasks(tmp_path: Path):
    path = _db(tmp_path)
    for index in range(101):
        task_id = f"task-b76-analysis-{index:03d}"
        _terminal_task(
            path, task_id=task_id, kind="analysis", status="completed", stage="done", checkpoint={},
            payload={"runtimeContract": runtime_contract(), "observationId": task_id},
        )
        if index < 100:
            assert enqueue_task_notification(task_id=task_id, db_path=path, created_at=NOW).kind == "k10_analysis"

    assert _outbox_count(path, "task-b76-analysis-100") == 0
    assert reconcile_terminal_notifications(db_path=path, now=NOW, limit=1) == 1
    assert _outbox_count(path, "task-b76-analysis-100") == 1


def test_b77_default_limit_reconciles_the_101st_same_second_failed_scan_once(tmp_path: Path):
    """The tuple cursor must advance as (time, time, id), at the real default 100."""
    path = _db(tmp_path)
    for index in range(101):
        task_id = f"task-b77-failed-{index:03d}"
        _terminal_task(path, task_id=task_id, status="failed", stage="execution", checkpoint={})
        if index < 100:
            # Failed scans remain candidate rows even with their first failure
            # notification already present; this is the cursor's real damaged
            # table boundary, rather than a reduced-page imitation.
            enqueue_task_notification(task_id=task_id, db_path=path, created_at=NOW)
    assert _outbox_count(path, "task-b77-failed-100") == 0
    assert reconcile_terminal_notifications(db_path=path, now=NOW) == 1
    assert _outbox_count(path, "task-b77-failed-100") == 1
    assert reconcile_terminal_notifications(db_path=path, now=NOW + timedelta(seconds=1)) == 0


def test_b76_pause_and_legacy_terminal_tasks_never_enter_new_automatic_outbox(tmp_path: Path):
    path = _db(tmp_path)
    _terminal_task(path, task_id="task-paused", status="failed", stage="paused", checkpoint={})
    _terminal_task(
        path, task_id="task-legacy", status="completed", stage="done", checkpoint={},
        payload={"windowKind": "evening"},
    )
    _terminal_task(
        path, task_id="task-extra-contract-key", status="failed", stage="execution", checkpoint={},
        payload={"windowKind": "evening", "runtimeContract": {**runtime_contract(), "unexpected": "blocked"}},
    )

    assert reconcile_terminal_notifications(db_path=path, now=NOW) == 0
    assert _outbox_count(path, "task-paused") == 0
    assert _outbox_count(path, "task-legacy") == 0
    assert _outbox_count(path, "task-extra-contract-key") == 0
    with pytest.raises(NotificationConflict, match="受控暂停"):
        enqueue_task_notification(task_id="task-paused", db_path=path, created_at=NOW)
    # B69-era rows remain readable through their direct compatibility entry;
    # they simply cannot be discovered by the B76 automatic reconciler.
    legacy = enqueue_task_notification(task_id="task-legacy", db_path=path, created_at=NOW)
    assert legacy.kind == "k10_evening"
