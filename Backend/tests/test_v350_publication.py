"""The visible report and its notification must commit as one result."""
import json
import socket
import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

from neckline.k10 import store
from neckline.k10.notifications import enqueue_task_notification, NotificationConflict
from neckline.k10.notification_runtime import reconcile_terminal_notifications
from . import v340_acceptance_fixture as base
from .test_v350_cli_api import DirectRoundTransport, read_actual_api


@pytest.mark.parametrize("interrupt_notification", (False, True))
def test_report_and_notification_share_publication_commit(tmp_path, monkeypatch, interrupt_notification):
    monkeypatch.setattr(base, "TITLE_COUNT", 130)
    monkeypatch.setattr(base, "DeterministicTransport", DirectRoundTransport)
    monkeypatch.setattr(socket.socket, "connect", base._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", base._deny_network)
    original = store.finish_task_with_publication
    seen = []

    def inspect_transaction(conn, **kwargs):
        if interrupt_notification:
            conn.execute("CREATE TEMP TRIGGER interrupt_notification BEFORE INSERT ON k10_task_notifications "
                "WHEN NEW.kind IN ('k10_evening','k10_morning') "
                "BEGIN SELECT RAISE(ABORT, 'fixture publication interruption'); END")
        original(conn, **kwargs)
        row = conn.execute("SELECT deep_link_json FROM k10_task_notifications WHERE task_id=? AND kind='k10_evening'",
                           (kwargs["task_id"],)).fetchone()
        assert row is not None, "notification must exist before the report transaction commits"
        seen.append(json.loads(row[0]))

    monkeypatch.setattr(store, "finish_task_with_publication", inspect_transaction)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name="atomic-notification", selected_event_count=1,
        expect_handler_failure=interrupt_notification)
    report = read_actual_api(flow.db_path)[0]["report"]
    with sqlite3.connect(flow.db_path) as conn:
        cards = conn.execute("SELECT COUNT(*) FROM k10_v2_report_cards").fetchone()[0]
        samples = conn.execute("SELECT COUNT(*) FROM k10_publication_samples").fetchone()[0]
        notifications = conn.execute("SELECT deep_link_json FROM k10_task_notifications WHERE kind='k10_evening'").fetchall()
    if interrupt_notification:
        assert flow.task_status == "failed" and report["availableAt"] is None
        assert cards == samples == len(notifications) == 0 and seen == []
    else:
        assert flow.task_status == "completed" and report["availableAt"]
        assert cards > 0 and samples > 0 and len(notifications) == len(seen) == 1
        link = json.loads(notifications[0][0])
        assert link["reportId"] == report["reportId"] and link["windowKind"] == "evening"
        stamp = datetime.now(timezone.utc)
        first = enqueue_task_notification(task_id=flow.task_id, db_path=flow.db_path, created_at=stamp)
        second = enqueue_task_notification(task_id=flow.task_id, db_path=flow.db_path, created_at=stamp)
        assert first.notification_id == second.notification_id
        # A missing new-version success outbox means the atomic publication
        # needs explicit repair; background polling must not create another
        # authority for whether a report was published successfully.
        with sqlite3.connect(flow.db_path) as conn:
            conn.execute("DELETE FROM k10_task_notifications WHERE task_id=?", (flow.task_id,))
        assert reconcile_terminal_notifications(db_path=flow.db_path, now=stamp) == 0
        with pytest.raises(NotificationConflict):
            enqueue_task_notification(task_id=flow.task_id, db_path=flow.db_path, created_at=stamp)


@pytest.mark.parametrize("corruption", ("catalyst_revision", "ranking_input"))
def test_publication_rejects_corrupt_identity_before_outbox(tmp_path, monkeypatch, corruption):
    """Move the former notification recheck to the actual publication owner."""
    monkeypatch.setattr(base, "TITLE_COUNT", 130)
    monkeypatch.setattr(base, "DeterministicTransport", DirectRoundTransport)
    monkeypatch.setattr(socket.socket, "connect", base._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", base._deny_network)
    original = store.finish_task_with_publication

    def corrupt(conn, **kwargs):
        scan_id = kwargs["checkpoint"]["scanId"]
        if corruption == "catalyst_revision":
            card_id, raw = conn.execute("SELECT card_id,content_json FROM k10_v2_report_cards LIMIT 1").fetchone()
            value = json.loads(raw)
            value["deliveryIdentity"]["catalysts"][0]["eventRevision"] += 1
            conn.execute("UPDATE k10_v2_report_cards SET content_json=? WHERE card_id=?", (json.dumps(value), card_id))
        else:
            raw = conn.execute("SELECT coverage_json FROM k10_scans WHERE scan_id=?", (scan_id,)).fetchone()[0]
            value = json.loads(raw)
            value["rankingInput"] = {"companies": []}
            conn.execute("UPDATE k10_scans SET coverage_json=? WHERE scan_id=?", (json.dumps(value), scan_id))
        original(conn, **kwargs)

    monkeypatch.setattr(store, "finish_task_with_publication", corrupt)
    with pytest.raises(store.K10Conflict, match="公开交付公司集合与清单不一致"):
        base.run_full_scale_flow(tmp_path, monkeypatch, name="invalid-publication", selected_event_count=1,
            expect_handler_failure=True)
    with sqlite3.connect(tmp_path / "invalid-publication.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_v2_report_cards").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM k10_publication_samples").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM k10_task_notifications WHERE kind='k10_evening'").fetchone()[0] == 0


def test_evening_final_publication_is_not_rejected_by_retired_duration_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "TITLE_COUNT", 130)
    monkeypatch.setattr(base, "DeterministicTransport", DirectRoundTransport)
    monkeypatch.setattr(socket.socket, "connect", base._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", base._deny_network)
    original = store.finish_task_with_publication

    def long_running_evening(conn, **kwargs):
        task_id = kwargs["task_id"]
        raw = conn.execute("SELECT c.payload_json FROM k10_task_execution_bindings b "
            "JOIN k10_execution_config_revisions c ON c.config_id=b.execution_config_id "
            "AND c.revision=b.execution_config_revision WHERE b.task_id=?", (task_id,)).fetchone()[0]
        retired_limit = json.loads(raw)["discovery"]["completionDeadlineSeconds"]
        checkpoint = json.loads(conn.execute("SELECT checkpoint_json FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()[0])
        checkpoint["executionStartedAt"] = (datetime.fromisoformat(kwargs["finished_at"]) -
            timedelta(seconds=retired_limit + 1)).isoformat()
        conn.execute("UPDATE k10_tasks SET checkpoint_json=? WHERE task_id=?", (json.dumps(checkpoint), task_id))
        original(conn, **kwargs)

    monkeypatch.setattr(store, "finish_task_with_publication", long_running_evening)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name="long-evening", selected_event_count=1)
    assert flow.task_status == "completed"
    report = read_actual_api(flow.db_path)[0]["report"]
    assert report["availableAt"] and report["eveningCards"] and report["deliveryDeadlineAt"] is None
