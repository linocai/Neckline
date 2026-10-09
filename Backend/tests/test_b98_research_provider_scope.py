"""B92 research must not turn one search refusal into a model-wide stop."""
from __future__ import annotations

import json
import sqlite3
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from neckline.k10 import pipeline, research_runtime
from neckline.k10.investigation import InvestigationError
from tests import test_b92_report_loopback as current
from tests import v340_acceptance_fixture as base
from tests.test_b98_report_isolation import make_collected_case
from tests.test_b92_mcp_protocol import rpc
from tests.test_v350_cli_api import DirectRoundTransport


class _SearchThenIndependent(current._FlashReportTransport):
    research_mode = "tavily"

    def respond(self, request):
        packet = self._packet(request)
        if packet.get("action") == "research_round":
            event = packet["evidencePacket"]["event"]["canonicalKey"]
            if event == "event-002":
                return DirectRoundTransport.respond(self, request)
            self._record("research:research_round", event)
        return super().respond(request)


def _three_flash_wire():
    wire = current._news_wire()
    original = wire.reply

    def reply(request, body):
        if body["params"]["name"] != "list_flash":
            return original(request, body)
        rows = [{"id": f"flash-{number:03d}",
                 "url": f"https://flash.jin10.com/detail/flash-{number:03d}",
                 "time": "2026-09-26T07:00:00+08:00", "title": None,
                 "content": f"独立原件 {number}：样品已送达测试客户，订单尚未确认。"}
                for number in range(3)]
        return rpc(body, {"structuredContent": {"status": 200,
            "data": {"items": rows, "next_cursor": None, "has_more": False}}})

    wire.reply = reply
    return wire


@pytest.mark.parametrize("http_status,code", [
    (432, "insufficient_balance"), (401, "provider_authorization_failed"),
])
def test_b92_tavily_refusal_keeps_independent_model_and_report(
        tmp_path, monkeypatch, http_status, code):
    refusal_visible = Event()
    tavily_queries = []

    def refused_search(self, request):
        assert request.url.host == "api.tavily.com" and request.url.path == "/search"
        query = json.loads(request.content)["query"]
        tavily_queries.append(query)
        return httpx.Response(http_status, json={"detail": "isolated provider refusal"})

    monkeypatch.setattr(base._TavilyWire, "respond", refused_search)
    accept = research_runtime._Investigation._b78_accept_bundle

    def accept_then_signal(self, bundle):
        try:
            return accept(self, bundle)
        finally:
            # A settled refusal is deliberately surfaced as a pending bundle
            # by the real gateway. Signal after the runtime has consumed that
            # metadata even when its event-local error boundary raises.
            if bundle.coverage.get("reason") == code:
                refusal_visible.set()

    monkeypatch.setattr(research_runtime._Investigation, "_b78_accept_bundle", accept_then_signal)
    research = pipeline._research_outcome

    def after_settled_refusal(**kwargs):
        # Force the valid adversarial ordering within the configured six-slot
        # executor: one settled refusal precedes independent research.  All
        # actual model/search requests still pass their production guards.
        if kwargs["event"].canonical_key != "event-000":
            assert refusal_visible.wait(10), "first real Tavily refusal did not settle"
        return research(**kwargs)

    monkeypatch.setattr(pipeline, "_research_outcome", after_settled_refusal)
    db, task, report, materials, model_wire, _ = make_collected_case(
        tmp_path, monkeypatch, wire=_three_flash_wire(), transport_type=_SearchThenIndependent)
    model_events = [event for kind, event in model_wire.calls if kind == "research:research_round"]
    assert set(model_events) == {"event-000", "event-001", "event-002"}, model_events
    assert len(model_events) == 3  # no synthetic close call after an empty refused search
    assert len(tavily_queries) == 1 and "event-000" in tavily_queries[0]
    assert task[1] == "completed" and report["status"] == "partial"
    assert report["resultAvailableAt"] and report["eveningCards"] and materials["items"]
    assert {card["companyCode"] for card in report["eveningCards"]} == {model_wire.company_codes[1]}
    assert report["delivery"]["rankingScope"] == "completed_subset"
    assert any(gap["reasonCode"] == code for gap in report["delivery"]["gaps"])
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT stage,state,error_code FROM k10_external_attempts WHERE task_id=?",
                            (task[0],)).fetchall()
        assert [(state, error) for stage, state, error in rows if stage == "search"] == [("failed", code)]
        assert not any(state in {"started", "unknown"} for _, state, _ in rows)
        assert all(state == "succeeded" for stage, state, _ in rows if stage == "investigation")


@pytest.mark.parametrize("code", ["insufficient_balance", "provider_authorization_failed"])
def test_restored_refused_research_remains_local_to_its_snapshot(monkeypatch, code):
    runtime = research_runtime._Investigation.__new__(research_runtime._Investigation)
    shared_base = SimpleNamespace()
    shared_model = SimpleNamespace(_base=shared_base)
    runtime.model = shared_model
    runtime.state = {"snapshot": SimpleNamespace(execution_status="failed")}
    monkeypatch.setattr(runtime, "_b78_failed_round_error", lambda: code)
    with pytest.raises(InvestigationError) as caught:
        runtime._run_b78()
    assert caught.value.code == code
    assert not hasattr(shared_model, "_terminal_provider_error")
    assert not hasattr(shared_base, "_terminal_provider_error")
