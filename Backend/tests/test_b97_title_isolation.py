"""A bad model judgement must not erase independent report work (Oct 7)."""
import copy
import json
import sqlite3
from dataclasses import replace

import pytest

from neckline.k10 import store
from neckline.k10.title_triage import (
    TitleDTO, TitleTriageResult, TitleTriageProtocolError,
    isolate_reconcile_response, normalize_reconcile_result,
)
from tests import v340_acceptance_fixture as base
from tests.test_v350_cli_api import DirectRoundTransport, read_actual_api


def test_reconcile_bad_rows_preserve_valid_choices_at_incident_scale():
    items = tuple(TitleDTO(f"doc-{i:05d}", 1, "fixture", None, "真实标题") for i in range(14469))
    results = tuple(TitleTriageResult(t.document_id, 1, "candidate", "matter", "new", "批次判断") for t in items)
    results = (*results[:2], replace(results[2], status="correction_or_denial"), *results[3:])
    results = (*results[:5], replace(results[5], status="no_value"), *results[6:])
    raw = {"selected": [{"i": 0, "reason": "已知有效事实"}, {"i": 99999, "reason": "陌生编号"},
                        {"i": 3, "reason": ""}, {"i": True, "reason": "错误类型"},
                        {"i": 5, "reason": "不在协调可见列表中的真实资料"}],
           "merged": [{"i": 1, "into": 0, "reason": "合法同事件"},
                      {"i": 2, "into": 0, "reason": "错误吞并反证"},
                      {"i": 4, "into": 99999, "reason": "坏引用"}]}
    before = copy.deepcopy(raw)
    # The deployed strict parser reproduces the global failure on this topology.
    with pytest.raises(TitleTriageProtocolError):
        normalize_reconcile_result(raw, items, results, len(items))
    clean, gaps = isolate_reconcile_response(raw, items, results, len(items))
    assert raw == before
    assert [r["i"] for r in clean["selected"]] == [0]
    assert [r["i"] for r in clean["merged"]] == [1]
    assert len(clean["notSelected"]) == len(items) - 3
    assert len(gaps) == 6
    assert {r["documentId"] for g in gaps for r in g["inputRefs"]} == {items[i].document_id for i in (2, 3, 4, 5)}
    assert normalize_reconcile_result(clean, items, results, len(items)) == clean


@pytest.mark.parametrize("fault", ["broken_merged_array", "incomplete"])
def test_readable_choices_survive_broken_sibling_array_or_incomplete_review(fault):
    items = tuple(TitleDTO(f"d{i}", 1, "fixture", None, "标题") for i in range(3))
    results = tuple(TitleTriageResult(t.document_id, 1, "candidate", "m", "s", "初筛") for t in items)
    raw = {"selected": [{"i": 0, "reason": "有效新事实"}], "merged": []}
    if fault == "broken_merged_array":
        raw["merged"] = "bad array"
    else:
        raw["selectionComplete"] = False
    clean, gaps = isolate_reconcile_response(raw, items, results, 3)
    assert clean["selected"] == [{"i": 0, "selectedRank": 1, "reason": "有效新事实"}]
    assert gaps
    if fault == "incomplete":
        assert gaps[0]["inputRefs"] == [{"documentId": "d1", "revision": 1}, {"documentId": "d2", "revision": 1}]


@pytest.mark.parametrize("mode", ["foreign", "known_bad", "malformed_global", "interrupted", "context_exceeded"])
def test_cli_worker_bad_reconcile_continues_to_readable_partial(tmp_path, monkeypatch, mode):
    class BrokenChoices(DirectRoundTransport):
        def respond(self, request):
            payload = self._packet(request)
            if mode in {"malformed_global", "context_exceeded"} and isinstance(payload.get("documentId"), str):
                assert "离线验收正文 0002" in json.dumps(payload, ensure_ascii=False)
                self.document_numbers[payload["documentId"]] = 2
            if "inputCount" in payload:
                self._record("titleGlobal")
                if mode == "malformed_global":
                    return self._ok({"nonsense": True})
                chosen = sorted((r for r in payload["items"] if self._number_from_title(r["title"]) < 3),
                                key=lambda r: self._number_from_title(r["title"]))
                rows = [{"i": r["i"], "reason": "已知事实值得核验"} for r in chosen]
                if mode == "foreign":
                    rows.append({"i": 999999, "reason": "无效资料编号"})
                    hidden = next(i for i in range(payload["inputCount"])
                                  if i not in {r["i"] for r in payload["items"]})
                    rows.append({"i": hidden, "reason": "不在本轮可见列表内"})
                else:
                    rows[0]["reason"] = ""
                return self._ok({"selected": rows, "merged": []})
            response = super().respond(request)
            if mode == "foreign" and "items" in payload and "inputCount" not in payload:
                value = json.loads(response.json()["choices"][0]["message"]["content"])
                # Preserve all 14,469 title decisions while matching the real
                # incident's sparse global participants, not an all-candidate
                # synthetic packet exceeding the frozen context allowance.
                for row in value["items"]:
                    if self._number_from_title(payload["items"][row["i"]]["title"]) >= 1000:
                        row["status"] = "no_value"
                return self._ok(value)
            return response

    # A raw flash has independent body admission, so even total global-title
    # failure must not block its research and delivery.
    original_fetch = base._FullScaleNews.fetch_incremental
    def fetch(self, request):
        result = original_fetch(self, request)
        if mode in {"malformed_global", "context_exceeded"}:
            docs = list(result.documents)
            docs[2] = replace(docs[2], metadata={"sourceKind": "raw_flash"})
            result = replace(result, documents=tuple(docs))
        return result
    monkeypatch.setattr(base._FullScaleNews, "fetch_incremental", fetch)
    title_count = 14469 if mode == "foreign" else 130
    monkeypatch.setattr(base, "TITLE_COUNT", title_count)
    monkeypatch.setattr(base, "DeterministicTransport", BrokenChoices)
    if mode == "context_exceeded":
        from neckline.k10 import pipeline
        actual_operation = pipeline._CheckpointedDiscoveryModel.run_title_operation
        def operation(self, **kwargs):
            if kwargs["stage"] == "titleReconcile":
                raise pipeline.PipelineError("isolated over-budget title packet", code="execution_context_exceeded")
            return actual_operation(self, **kwargs)
        monkeypatch.setattr(pipeline._CheckpointedDiscoveryModel, "run_title_operation", operation)
    interrupted = []
    if mode == "interrupted":
        from neckline.k10.schema import SqliteWriteBusy
        actual_checkpoint = store.record_execution_checkpoint
        def checkpoint(**kwargs):
            result = actual_checkpoint(**kwargs)
            if kwargs.get("stage") == "title_reconcile_gap" and not interrupted:
                interrupted.append(True)
                raise SqliteWriteBusy("isolated interruption after durable title gap")
            return result
        monkeypatch.setattr(store, "record_execution_checkpoint", checkpoint)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name=f"b97-{mode}", selected_event_count=3,
                                   expected_continuation_codes=("DISCOVERY_SLICE", "sqlite_busy"))
    envelope, materials, _, _ = read_actual_api(flow.db_path)
    report = envelope["report"]
    assert flow.task_status == "completed"
    assert report["status"] == "partial"
    assert report["delivery"]["rankingScope"] == "completed_subset"
    assert report["eveningCards"] and materials["items"]
    assert any(g["reasonCode"] == "title_reconcile_partial" for g in report["delivery"]["gaps"])
    counts = report["delivery"]["counts"]
    assert counts["titleProcessed"] + counts["titleFailed"] == title_count
    assert counts["titleUnprocessed"] == 0
    assert flow.calls.get("titleGlobal", 0) == (0 if mode == "context_exceeded" else 2 if mode == "malformed_global" else 1)
    selection = store.read_title_selection_manifest(task_id=flow.task_id, db_path=flow.db_path)
    assert len(selection["selectedRefs"]) == {"foreign": 3, "known_bad": 2, "malformed_global": 1, "interrupted": 2, "context_exceeded": 1}[mode]
    if mode == "interrupted":
        assert interrupted
    with sqlite3.connect(flow.db_path) as c:
        assert c.execute("SELECT count(*) FROM k10_external_attempts WHERE state IN ('started','unknown')").fetchone()[0] == 0
        gap = json.loads(c.execute("SELECT result_json FROM k10_execution_item_checkpoints WHERE stage='title_reconcile_gap'").fetchone()[0])
        assert gap["globalFailed"] == (mode in {"malformed_global", "context_exceeded"})
