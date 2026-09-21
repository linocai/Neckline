"""B77 receipt-only review edges: no invented network attempts or lost receipts."""
import json
import sqlite3

import pytest

from neckline.k10 import store
from tests.test_v341_review_repairs import _research_adapter


@pytest.mark.parametrize("receipt_shape", ["semantic_only", "tool_calls", "invalid_json"])
def test_receipt_reconciliation_preserves_unavailable_evidence_and_does_not_retry_local_validation(
    tmp_path, monkeypatch, receipt_shape,
):
    path, adapter, snapshot, packet, calls = _research_adapter(tmp_path, monkeypatch)
    original = store.record_execution_checkpoint

    def interrupt(**kwargs):
        if kwargs.get("stage") == "model:investigation_research_round" and kwargs.get("status") == "completed":
            raise sqlite3.OperationalError("interrupted after durable receipt")
        return original(**kwargs)

    monkeypatch.setattr(store, "record_execution_checkpoint", interrupt)
    with pytest.raises(sqlite3.OperationalError, match="durable receipt"):
        adapter.advance_research_round(snapshot=snapshot, evidence_packet=packet)
    monkeypatch.setattr(store, "record_execution_checkpoint", original)
    # These represent supported historical receipt shapes, not untrusted hash
    # corruption. Keep the real task/wire/ledger from the interrupted request.
    with sqlite3.connect(path) as conn:
        receipt = json.loads(conn.execute("SELECT payload_json FROM k10_model_response_receipts").fetchone()[0])
        if receipt_shape == "semantic_only":
            receipt["rawResponses"] = []
        elif receipt_shape == "tool_calls":
            receipt["rawResponses"][0]["choices"][0]["message"]["tool_calls"] = [
                {"id": "historical-call", "type": "function", "function": {"name": "historical", "arguments": "{}"}},
            ]
        else:
            receipt["rawResponses"][0]["choices"][0]["message"]["content"] = "{broken-json"
        payload, checksum = store._receipt_payload(receipt)
        conn.execute("UPDATE k10_model_response_receipts SET payload_json=?,payload_sha256=?", (payload, checksum))
        checkpoint_before = conn.execute("SELECT * FROM k10_execution_item_checkpoints").fetchall()
        receipts_before = conn.execute("SELECT * FROM k10_model_response_receipts").fetchall()
        attempts_before = conn.execute("SELECT * FROM k10_external_attempts").fetchall()

    with pytest.raises(Exception) as raised:
        adapter.advance_research_round(snapshot=snapshot, evidence_packet=packet)
    assert calls == ["/chat"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM k10_model_response_receipts").fetchall() == receipts_before
        assert conn.execute("SELECT * FROM k10_external_attempts").fetchall() == attempts_before
        state, attempts, network, repairs, code = conn.execute(
            "SELECT status,attempt_count,network_attempt_count,repair_attempt_count,safe_error_code "
            "FROM k10_execution_item_checkpoints"
        ).fetchone()
        assert (attempts, network, repairs) == (1, 1, 0)
        if receipt_shape != "invalid_json":
            assert state == "running"
            assert getattr(raised.value, "code", None) == "model_request_outcome_unknown"
            assert conn.execute("SELECT * FROM k10_execution_item_checkpoints").fetchall() == checkpoint_before
        else:
            # A replayable reply that fails JSON validation is a real local
            # failure, not an unknown wire outcome or a fresh repair request.
            assert state == "failed" and "json" in code and not code.endswith("exhausted")
