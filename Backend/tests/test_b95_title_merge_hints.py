"""Sep 28: redundant merge hints must not discard usable paid selections."""
import copy
import socket

import pytest

from neckline.k10.title_triage import (
    TitleDTO, TitleTriageResult, TitleTriageProtocolError, normalize_reconcile_result,
)
from neckline.k10 import store
from tests import v340_acceptance_fixture as acceptance
from tests.test_v350_cli_api import DirectRoundTransport, read_actual_api


def test_paid_repair_topology_preserves_selection_and_separate_audits():
    items = tuple(TitleDTO(f"doc-{i}", 1, "fixture", None, "title") for i in range(2005))
    batch = tuple(TitleTriageResult(t.document_id, 1, "candidate", "matter", "new", "batch")
                  for t in items)
    raw = {"selected": [{"i": i, "reason": "新增事实值得核查"} for i in (687, 198, 26)],
           "merged": [{"i": 687, "into": 198, "reason": "重复转载"},
                      {"i": 26, "into": 26, "reason": "占位错误不可用"},
                      {"i": 1921, "into": 1921, "reason": "占位"},
                      {"i": 533, "into": 198, "reason": "真实有效合并"}]}
    before = copy.deepcopy(raw)
    result = normalize_reconcile_result(raw, items, batch, len(items))
    assert raw == before
    assert [r["i"] for r in result["selected"]] == [687, 198, 26]
    assert result["merged"] == [{"i": 533, "into": 198, "reason": "真实有效合并"}]
    assert 1921 in result["notSelected"]
    assert len(result["selected"]) + len(result["merged"]) + len(result["notSelected"]) == 2005


@pytest.mark.parametrize("edge", [(0, 99), (99, 99), (True, True)])
def test_ignored_edge_still_requires_both_real_references(edge):
    items = (TitleDTO("d", 1, "fixture", None, "title"),)
    batch = (TitleTriageResult("d", 1, "candidate", "matter", "new", "batch"),)
    with pytest.raises(TitleTriageProtocolError):
        normalize_reconcile_result({"selected": [{"i": 0, "reason": "valid"}],
            "merged": [{"i": edge[0], "into": edge[1], "reason": "ignored"}]}, items, batch, 1)


def test_real_cli_worker_publishes_with_redundant_merge_hints(tmp_path, monkeypatch):
    class MergeHintTransport(DirectRoundTransport):
        def respond(self, request):
            payload = self._packet(request)
            if "inputCount" in payload:
                self._record("titleGlobal")
                chosen = sorted((r for r in payload["items"]
                    if self._number_from_title(r["title"]) < self.selected_event_count),
                    key=lambda r: self._number_from_title(r["title"]))
                a, b = chosen[:2]
                unused = next(r for r in payload["items"] if r not in chosen)
                return self._ok({"selected": [{"i": r["i"], "reason": "新增事件值得核查"}
                                               for r in chosen],
                    "merged": [{"i": a["i"], "into": b["i"], "reason": "重复提示"},
                               {"i": a["i"], "into": a["i"], "reason": "占位错误不可用"},
                               {"i": unused["i"], "into": unused["i"], "reason": "占位"}]})
            return super().respond(request)

    monkeypatch.setattr(acceptance, "TITLE_COUNT", 130)
    monkeypatch.setattr(acceptance, "DeterministicTransport", MergeHintTransport)
    monkeypatch.setattr(socket.socket, "connect", acceptance._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", acceptance._deny_network)
    flow = acceptance.run_full_scale_flow(tmp_path, monkeypatch, name="b95-title-merge", selected_event_count=3)
    assert flow.task_status == "completed"
    assert flow.calls["titleGlobal"] == 1
    envelope, _, _, _ = read_actual_api(flow.db_path)
    report = envelope["report"]
    assert report["delivery"]["outcome"] == "complete"
    assert report["delivery"]["counts"]["titleProcessed"] == 130
    # The fixture's three title events produce two comparable companies.
    # Verify title admission independently of downstream company selection.
    selection = store.read_title_selection_manifest(task_id=flow.task_id, db_path=flow.db_path)
    assert len(selection["selectedRefs"]) == 3
    assert len(report["eveningCards"]) == 2
