"""Paid body repair + interrupted checkpoint must finish through the real worker."""
import json
import socket
import sqlite3

from neckline.k10 import store
from neckline.k10.schema import SqliteWriteBusy
from tests import v340_acceptance_fixture as acceptance
from tests.test_v350_cli_api import DirectRoundTransport, read_actual_api


def test_cli_worker_replays_paid_body_repair_after_checkpoint_interruption(tmp_path, monkeypatch):
    class RepairTransport(DirectRoundTransport):
        def respond(self, request):
            payload = self._packet(request)
            response = super().respond(request)
            if isinstance(payload.get("documentId"), str):
                count = sum(kind == "understand" for kind, _ in self.calls)
                if count == 1:
                    raw = json.loads(response.json()["choices"][0]["message"]["content"])
                    del raw["events"][0]["claims"][0]["novelty"]
                    return self._ok(raw)
            return response

    interrupted = []
    original = store.record_execution_checkpoint

    def persist(**kwargs):
        if kwargs["stage"] == "model:understand" and kwargs["status"] == "completed" and not interrupted:
            with sqlite3.connect(kwargs["db_path"]) as connection:
                rows = connection.execute(
                    "SELECT e.attempt_id,r.request_sha256 FROM k10_external_attempts e "
                    "JOIN k10_model_response_receipts r USING(attempt_id) "
                    "WHERE e.task_id=? AND e.stage='fullText' AND e.state='succeeded'",
                    (kwargs["task_id"],),
                ).fetchall()
            assert len(rows) == 2 and len({row[1] for row in rows}) == 2
            interrupted.extend(rows)
            raise SqliteWriteBusy("database is locked")
        return original(**kwargs)

    monkeypatch.setattr(acceptance, "TITLE_COUNT", 2)
    monkeypatch.setattr(acceptance, "DeterministicTransport", RepairTransport)
    monkeypatch.setattr(socket.socket, "connect", acceptance._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", acceptance._deny_network)
    monkeypatch.setattr(store, "record_execution_checkpoint", persist)
    flow = acceptance.run_full_scale_flow(
        tmp_path, monkeypatch, name="b84-body-receipt", selected_event_count=1,
        expected_continuation_codes=("sqlite_busy", "DISCOVERY_SLICE"))
    assert interrupted and flow.task_status == "completed"
    assert flow.calls["understand"] == 2
    envelope, _, _, _ = read_actual_api(flow.db_path)
    assert envelope["report"]["delivery"]["outcome"] == "complete"
    assert len(envelope["report"]["eveningCards"]) == 1
    with sqlite3.connect(flow.db_path) as connection:
        assert connection.execute(
            "SELECT e.attempt_id,r.request_sha256 FROM k10_external_attempts e "
            "JOIN k10_model_response_receipts r USING(attempt_id) "
            "WHERE e.stage='fullText' AND e.state='succeeded' ORDER BY e.attempt_id"
        ).fetchall() == sorted(interrupted)
        assert not connection.execute(
            "SELECT 1 FROM k10_external_attempts WHERE state IN ('started','running','unknown')").fetchone()
