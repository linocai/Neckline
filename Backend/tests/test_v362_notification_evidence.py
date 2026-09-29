"""Notification evidence from actual publication and injected APNs acceptance."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest

from neckline.api.stores import upsert_device, list_device_tokens, delete_device
from neckline.k10.notifications import DeliveryResult, NotificationRetryPolicy, dispatch_task_notifications
from tests import v340_acceptance_fixture as base
from tests.test_v350_cli_api import DirectRoundTransport, read_actual_api


@pytest.mark.parametrize("device_case", ["none", "accepted", "mixed"])
def test_actual_report_exposes_acceptance_and_never_device_display(tmp_path, monkeypatch, device_case):
    monkeypatch.setattr(base, "TITLE_COUNT", 8)
    monkeypatch.setattr(base, "DeterministicTransport", DirectRoundTransport)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name="v362-notify", selected_event_count=3)
    report = read_actual_api(flow.db_path)[0]["report"]
    assert report["notificationEvidence"] == {"state": "queued", "acceptedDeviceCount": 0,
        "registeredDeviceCount": 0, "deviceDisplayState": "unverified"}
    with sqlite3.connect(flow.db_path) as conn:
        notification_id = conn.execute("SELECT notification_id FROM k10_task_notifications WHERE task_id=?",
                                       (flow.task_id,)).fetchone()[0]
    tokens = [] if device_case == "none" else ["synthetic-device-one"]
    if device_case == "mixed":
        tokens.append("synthetic-device-two")
    for token in tokens:
        upsert_device(token=token, db_path=flow.db_path)
    sent = []
    def sender(**kwargs):
        sent.append(kwargs["token"])
        return DeliveryResult(ok=kwargs["token"] == "synthetic-device-one", reason="transient_test_failure")
    at = datetime.now(timezone.utc)
    assert dispatch_task_notifications(db_path=flow.db_path,
        list_device_tokens=lambda: list_device_tokens(db_path=flow.db_path),
        delete_device=lambda token: delete_device(token=token, db_path=flow.db_path), sender=sender,
        worker_id="v362-notify", now=at, notification_id=notification_id,
        retry_policy=NotificationRetryPolicy(timedelta(seconds=1), timedelta(seconds=60))) == 1
    envelope = read_actual_api(flow.db_path)[0]
    evidence = envelope["report"]["notificationEvidence"]
    assert evidence == {"state": {"none": "no_registered_devices", "accepted": "apns_accepted", "mixed": "partial"}[device_case],
        "acceptedDeviceCount": int(bool(tokens)), "registeredDeviceCount": len(tokens), "deviceDisplayState": "unverified"}
    assert sent == tokens
    if device_case == "none":
        # The older sent state has no stored device-target proof. Reading it
        # cannot repair history or quietly enqueue the old notification again.
        with sqlite3.connect(flow.db_path) as conn:
            conn.execute("UPDATE k10_task_notifications SET last_error=NULL WHERE notification_id=?", (notification_id,))
        assert read_actual_api(flow.db_path)[0]["report"]["notificationEvidence"]["state"] == "unknown"
        with sqlite3.connect(flow.db_path) as conn:
            assert conn.execute("SELECT status,attempt_count FROM k10_task_notifications WHERE notification_id=?",
                                (notification_id,)).fetchone() == ("sent", 1)
    if device_case == "accepted":
        export = os.environ.get("NK_V362_API_DIR")
        if export:
            directory = Path(export)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "complete.json").write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n")
