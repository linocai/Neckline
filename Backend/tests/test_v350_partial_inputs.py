"""Title and article failures must not recreate the whole-report failure gate."""
import socket

import httpx
import pytest

from . import v340_acceptance_fixture as base
from .test_v350_cli_api import DirectRoundTransport, read_actual_api


@pytest.mark.parametrize("all_failed", (False, True))
def test_title_reconciliation_never_invents_results_for_failed_batches(all_failed):
    from neckline.k10.title_triage import triage_titles
    from .test_v306_title_triage import _items, _batch, _choose, POLICY
    titles = _items(7)
    failures, reconciled = [], []

    def batch(items):
        if all_failed or items[0].document_id == "doc-3":
            raise RuntimeError("settled fixture refusal")
        return _batch(items)

    def isolate(index, items, exc):
        failures.extend(item.ref for item in items)
        return True

    def reconcile(items, results, count):
        reconciled.extend(item.ref for item in items)
        assert len(items) == len(results) == count
        return _choose(items, results, count)

    selected = triage_titles(titles, window_kind="evening", policy=POLICY,
        batch_call=batch, reconcile_call=reconcile, batch_concurrency=2,
        isolate_batch_failure=isolate)
    assert selected.input_count == 7
    assert set(failures).isdisjoint(selected.selected_refs)
    assert set(failures) | set(selected.selected_refs) == {item.ref for item in titles}
    assert set(reconciled) == set(selected.selected_refs)
    assert bool(selected.selected_refs) != all_failed


def test_unknown_paid_outcome_is_not_a_local_title_gap():
    from neckline.k10.title_runtime import _local_failure_code

    class Unsettled(Exception):
        code = "provider_request_outcome_unknown"

    assert _local_failure_code(Unsettled()) is None


@pytest.mark.parametrize("failure_stage", ("title_batch", "article"))
def test_local_input_failure_keeps_safe_completed_report(tmp_path, monkeypatch, failure_stage):
    class InputFailureTransport(DirectRoundTransport):
        def respond(self, request):
            packet = self._packet(request)
            refuse = False
            if failure_stage == "title_batch" and "items" in packet and "inputCount" not in packet:
                refuse = any(self._number_from_title(row["title"]) == 1 for row in packet["items"])
            if failure_stage == "article" and isinstance(packet.get("documentId"), str):
                refuse = self.document_numbers.get(packet["documentId"]) == 1
            if refuse:
                self._record("refused:" + failure_stage)
                return httpx.Response(400, json={"error": {
                    "code": "invalid_request_error", "message": "Content Exists Risk",
                }})
            if (failure_stage == "article" and "candidates" in packet
                    and isinstance(packet.get("output"), dict) and "choices" in packet["output"]):
                if {row["companyCode"] for row in packet["candidates"]} != {self.company_codes[1]}:
                    self._record("forbidden:failed_article_company_in_ranking")
                    raise AssertionError("failed article's known company must not enter final ranking")
            return super().respond(request)

    monkeypatch.setattr(base, "TITLE_COUNT", 130)
    monkeypatch.setattr(base, "DeterministicTransport", InputFailureTransport)
    monkeypatch.setattr(socket.socket, "connect", base._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", base._deny_network)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name=failure_stage,
        selected_event_count=12 if failure_stage == "title_batch" else 3)
    assert flow.task_status == "completed", {"status": flow.task_status, "calls": flow.calls}
    assert flow.calls.get("refused:" + failure_stage, 0) == 1
    assert not any(name.startswith("forbidden:") for name in flow.calls), flow.calls
    envelope, _, _, _ = read_actual_api(flow.db_path)
    report = envelope["report"]
    delivery = report["delivery"]
    assert delivery["outcome"] == "partial" and delivery["gaps"]
    assert report["availableAt"] and report["eveningCards"]
    assert delivery["counts"]["titleInput"] == 130
    if failure_stage == "title_batch":
        assert 0 < delivery["counts"]["titleProcessed"] < 130
        assert delivery["counts"]["titleFailed"] > 0
    else:
        assert delivery["counts"]["titleProcessed"] == 130
        assert {card["companyCode"] for card in report["eveningCards"]} == {flow.company_codes[1]}
