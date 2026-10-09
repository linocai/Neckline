"""Recheck the private-response serializer using an untouched, valid model content object."""
import hashlib
import json
import socket
import sqlite3
import traceback

import httpx
import pytest

from neckline.k10 import store
from neckline.k10.schema import SqliteWriteBusy
from tests import test_b92_report_loopback as current
from tests import test_b98_report_isolation as existing
from tests import test_b101_wire_boundaries as mixed
from tests import test_b102_report_boundaries as boundary


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("reviewer forbids external network")
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)


@pytest.mark.parametrize("case", ["model_envelope_control", "model_envelope_unused_surrogate", "model_envelope_unused_surrogate_after_receipt"])
def test_received_model_envelope_keeps_valid_report(case, tmp_path, monkeypatch):
    original = current._FlashReportTransport.respond
    injected = []
    wire_hashes = []
    settle_errors = []
    interrupted = []
    if case.endswith('after_receipt'):
        original_checkpoint = store.record_execution_checkpoint
        def checkpoint(**kwargs):
            if kwargs['stage'] == 'model:understand' and injected and not interrupted:
                interrupted.append(True)
                raise SqliteWriteBusy('B102 interrupted after exact model envelope receipt')
            return original_checkpoint(**kwargs)
        monkeypatch.setattr(store, 'record_execution_checkpoint', checkpoint)

    def respond(self, request):
        wire_hashes.append(hashlib.sha256(request.content).hexdigest())
        response = original(self, request)
        packet = self._packet(request)
        if (case != "model_envelope_control" and "documentId" in packet
                and self.document_numbers[packet["documentId"]] == 0):
            value = response.json()
            value["optionalComment"] = "unused escaped provider string\ud800"
            injected.append(packet["documentId"])
            return httpx.Response(200, content=json.dumps(value, ensure_ascii=True).encode("ascii"),
                                  headers={"Content-Type": "application/json"})
        return response

    settle = store.settle_model_response_attempt

    def observed_settle(**kwargs):
        try:
            return settle(**kwargs)
        except Exception as exc:
            settle_errors.append({"type": type(exc).__name__, "message": str(exc),
                "frames": [{"path": frame.filename, "line": frame.lineno, "function": frame.name}
                           for frame in traceback.extract_tb(exc.__traceback__)]})
            raise

    monkeypatch.setattr(current._FlashReportTransport, "respond", respond)
    monkeypatch.setattr(store, "settle_model_response_attempt", observed_settle)
    wire = mixed.mixed_wire("control")
    error = None
    try:
        _, _, _, _, _, slices = existing.make_collected_case(tmp_path, monkeypatch, wire=wire)
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
    result = boundary.observe(tmp_path / "b92-flash.sqlite", case=case, handlers=[], wire=wire,
                              expectation_error=error)
    result.update(injected=injected, settlementErrors=settle_errors,
                  modelWireCount=len(wire_hashes), modelDistinctWireCount=len(set(wire_hashes)))
    boundary.emit(case + ".json", result)
    assert error is None and not settle_errors, result
    assert len(result["report"]["eveningCards"]) == 3 and len(result["materials"]["items"]) == 4
    assert not any(row[2] in {"started", "unknown"} for row in result["externalAttempts"])
    if case != 'model_envelope_control':
        assert injected
        with sqlite3.connect(tmp_path / 'b92-flash.sqlite') as conn:
            receipts = conn.execute('SELECT payload_json FROM k10_model_response_receipts').fetchall()
        assert any('\\ud800' in raw for (raw,) in receipts)
    if case.endswith('after_receipt'):
        assert interrupted == [True] and slices[0] == 'queued' and slices[-1] == 'completed'
        assert len(wire_hashes) == len(set(wire_hashes))
