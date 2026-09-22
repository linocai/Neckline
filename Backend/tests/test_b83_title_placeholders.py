"""September 22: empty schema echoes must not discard a usable paid selection."""
import copy
import socket

import pytest

from neckline.k10.title_triage import (
    TitleDTO, TitleTriageResult, TitleTriageProtocolError, normalize_reconcile_result,
)
from tests import v340_acceptance_fixture as acceptance
from tests.test_v350_cli_api import DirectRoundTransport, read_actual_api


def inputs():
    items = tuple(TitleDTO(f"doc-{i}", 1, "fixture", None, f"title {i}") for i in range(3))
    batch = tuple(TitleTriageResult(item.document_id, 1,
        "no_value" if i == 1 else "candidate", f"matter-{i}", "new", "原始批次判断")
        for i, item in enumerate(items))
    return items, batch


def test_original_paid_reply_schema_echo_keeps_selection_and_excluded_audit():
    items, batch = inputs()
    raw = {"reason": "short string", "selected": [
        {"i": 2, "reason": "新增订单值得核查"},
        {"i": 1, "reason": "", "reason_placeholder": ""},
    ], "merged": []}
    before = copy.deepcopy(raw)
    result = normalize_reconcile_result(raw, items, batch, len(items))
    assert raw == before
    assert result == {"selected": [{"i": 2, "selectedRank": 1, "reason": "新增订单值得核查"}],
                      "merged": [], "notSelected": [0]}
    assert batch[1].status == "no_value"


@pytest.mark.parametrize("row", [
    {"i": 3, "reason": "", "reason_placeholder": ""},
    {"i": 1, "reason": "错引到未提供的标题", "reason_placeholder": ""},
    {"i": 0, "reason": "", "reason_placeholder": ""},
    {"i": True, "reason": "", "reason_placeholder": ""},
    {"i": 1, "reason": "", "reason_placeholder": "有实质内容"},
])
def test_empty_echo_tolerance_never_accepts_unknown_or_meaningful_wrong_references(row):
    items, batch = inputs()
    with pytest.raises(TitleTriageProtocolError):
        normalize_reconcile_result({"selected": [row], "merged": []}, items, batch, len(items))


def test_canonical_checkpoint_cannot_gain_an_ignored_field():
    items, batch = inputs()
    with pytest.raises(TitleTriageProtocolError):
        normalize_reconcile_result({"selected": [], "merged": [], "notSelected": [0, 2],
                                    "reason": "not a wire response"}, items, batch, len(items))


def test_real_cli_worker_delivers_formal_report_with_empty_title_schema_echo(tmp_path, monkeypatch):
    class PlaceholderTransport(DirectRoundTransport):
        def respond(self, request):
            payload = self._packet(request)
            if "items" in payload and "inputCount" not in payload:
                self._record("titleBatch")
                return self._ok({"items": [{"i": i,
                    "status": "no_value" if self._number_from_title(row["title"]) == 129 else "candidate",
                    "matterKey": f"matter-{self._title_number(row)}",
                    "stageKey": "new", "reason": "固定批次判断"}
                    for i, row in enumerate(payload["items"])]})
            if "inputCount" in payload:
                self._record("titleGlobal")
                visible = {row["i"] for row in payload["items"]}
                excluded = set(range(payload["inputCount"])) - visible
                assert len(excluded) == 1
                chosen = sorted((row for row in payload["items"]
                    if self._number_from_title(row["title"]) < self.selected_event_count),
                    key=lambda row: self._number_from_title(row["title"]))
                return self._ok({"reason": "short string", "selected": [
                    *[{"i": row["i"], "reason": "新增事件值得核查"} for row in chosen],
                    {"i": excluded.pop(), "reason": "", "reason_placeholder": ""}], "merged": []})
            return super().respond(request)

    monkeypatch.setattr(acceptance, "TITLE_COUNT", 130)
    monkeypatch.setattr(acceptance, "DeterministicTransport", PlaceholderTransport)
    monkeypatch.setattr(socket.socket, "connect", acceptance._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", acceptance._deny_network)
    flow = acceptance.run_full_scale_flow(tmp_path, monkeypatch, name="b83-title-placeholder", selected_event_count=3)
    assert flow.task_status == "completed"
    assert flow.calls["titleGlobal"] == 1
    envelope, _, _, _ = read_actual_api(flow.db_path)
    report = envelope["report"]
    assert report["delivery"]["outcome"] == "complete"
    assert report["delivery"]["counts"]["titleInput"] == 130
    assert report["delivery"]["counts"]["titleProcessed"] == 130
    assert report["eveningCards"]
