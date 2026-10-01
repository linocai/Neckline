"""Local gaps stay visible without erasing independent completed research."""
from types import SimpleNamespace
import json
import sqlite3

import pytest

from neckline.k10 import pipeline
from neckline.k10.discovery import EvidenceRef
from neckline.k10.schema import initialize_schema
from tests import v340_acceptance_fixture as base
from tests.test_v350_cli_api import DirectRoundTransport, read_actual_api


@pytest.mark.parametrize("outcome,has_gap", [
    ({"state": "completed"}, False),
    ({"state": "partial", "complete": True}, True),
    ({"state": "failed"}, True),
    ({"complete": True}, False),
    ({"complete": False}, True),
])
def test_partial_collection_does_not_invent_gap_for_completed_source(tmp_path, outcome, has_gap):
    db = tmp_path / "sources.sqlite"
    initialize_schema(db)
    run = SimpleNamespace(events=(), candidates=(), deferred=(), metadata_pending=(),
                          excluded=(), updates=(), background=(), issues=())
    delivery, published = pipeline._b76_delivery_for_run(run=run,
        coverage={"ingestionState": "partial", "sourceOutcomes": [
            {"sourceKey": "tushare-major-news", **outcome},
            {"sourceKey": "jin10-flash", "state": "partial"}]},
        failed_snapshots=(), task_id="test", db_path=db)
    assert ("tushare-major-news" in {g["unitId"] for g in delivery["gaps"]}) is has_gap
    assert delivery["outcome"] == "partial" and not published


@pytest.mark.parametrize("field", ["event", "mapping", "comparison", "assessment"])
def test_unknown_body_scope_excludes_only_actual_source_consumers(tmp_path, field):
    db = tmp_path / "dependencies.sqlite"
    initialize_schema(db)
    broken = EvidenceRef("broken", 2)
    independent = EvidenceRef("peer", 1)
    def candidate(code, ref):
        return SimpleNamespace(
            event=SimpleNamespace(source_refs=(ref,) if field == "event" else (independent,)),
            mapping=SimpleNamespace(company_code=code, relation_evidence=(ref,) if field == "mapping" else ()),
            comparison=SimpleNamespace(evidence_refs=(ref,) if field == "comparison" else (),
                differences={"sourceRefs": [{"documentId": ref.document_id, "revision": ref.revision}]}
                    if field == "assessment" else {}))
    candidates = [candidate("000001.SZ", broken), candidate("000002.SZ", independent),
                  candidate("000003.SZ", EvidenceRef("broken", 1))]
    excluded = pipeline._document_dependency_codes(task_id="test",
        refs=[{"documentId": "broken", "revision": 2}], candidates=candidates, db_path=db)
    assert excluded == {"000001.SZ"}, "unknown scope is not global, but shared exact evidence must be removed"


def test_real_cli_unknown_failed_research_preserves_independent_ranked_cards(tmp_path, monkeypatch):
    class UnknownResearchScope(DirectRoundTransport):
        def _company_for_event(self, event):
            return self.company_codes[base._event_number(event)]

        def respond(self, request):
            packet = self._packet(request)
            if "companies" in packet and "choices" in packet.get("output", {}):
                assert {row["companyCode"] for row in packet["companies"]} == {
                    self.company_codes[0], self.company_codes[2]}
                return self._base_response(self, request)
            response = super().respond(request)
            if "items" in packet and "inputCount" not in packet:
                value = response.json()["choices"][0]["message"]["content"]
                value = json.loads(value)
                for item in value["items"]:
                    if self._number_from_title(packet["items"][item["i"]]["title"]) == 1:
                        item["companyCodes"] = []
                return self._ok(value)
            return response
    # Keep the parent class stable after installing this fixture transport.
    original_response = base.DeterministicTransport.respond
    monkeypatch.setattr(base, "TITLE_COUNT", 8)
    monkeypatch.setattr(base, "DeterministicTransport", UnknownResearchScope)
    # Avoid resolving the dynamically replaced fixture class inside respond.
    UnknownResearchScope._base_response = staticmethod(original_response)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name="b96-unknown-research",
                                    selected_event_count=3, refusal_event=1)
    envelope, materials, _, _ = read_actual_api(flow.db_path)
    report = envelope["report"]
    assert flow.task_status == "completed"
    assert report["delivery"]["rankingScope"] == "completed_subset"
    assert report["delivery"]["counts"]["publishedCompanies"] == 2
    assert len(report["eveningCards"]) == 2 and materials["items"]
    assert any(g["unitKind"] == "event" and not g["companyScopeKnown"] for g in report["delivery"]["gaps"])
    assert any("影响公司范围尚未确认" in g["message"] for g in report["delivery"]["gaps"])
    with sqlite3.connect(flow.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_external_attempts WHERE state IN ('started','running','unknown')").fetchone() == (0,)
