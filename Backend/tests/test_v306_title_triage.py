"""V3.0.6 title-only admission contract.

These fakes exercise the pure protocol.  They intentionally do not emulate
DeepSeek quality; live validation belongs to the isolated runtime boundary.
"""
from __future__ import annotations

import json
from pathlib import Path
import threading
from concurrent.futures import ALL_COMPLETED, wait as wait_for_futures

import pytest

import neckline.k10.title_triage as title_triage_module
from neckline.k10.discovery import (DiscoveryDocument, EvidenceRef, EventDraft,
                                    Verification, deduplicate_documents, run_discovery)
from neckline.k10.title_triage import (
    TitleDTO,
    TitleSelectionItem,
    TitleTriageProtocolError,
    TitleTriageResult,
    TitleSelection,
    apply_title_review,
    batch_request_spec,
    normalize_batch_result,
    normalize_reconcile_result,
    normalize_review_result,
    reconcile_request_spec,
    review_request_spec,
    title_batch_payload,
    triage_titles,
    validate_batch_result,
    validate_reconcile_result,
)


POLICY = {"policyId": "titles-v1", "revision": 1, "contentSha256": "a" * 64, "batchSize": 3,
          "content": {"retainCorrections": True, "keepUncertain": True,
                      "mergeOnlyNoNewFacts": True, "forbidHardExclusions": True}}


def _run_configuration() -> dict:
    return json.loads((Path(__file__).parents[1] / "neckline/config/k10-v1.4.json").read_text())


def _items(count: int) -> tuple[TitleDTO, ...]:
    return tuple(TitleDTO(f"doc-{index}", 1, "source", "2026-09-08T12:00:00+00:00", f"标题 {index}")
                 for index in range(count))


def _batch(items):
    return tuple(TitleTriageResult(item.document_id, item.revision, "candidate", item.document_id,
                                   "initial", "可能影响公司基本面") for item in items)


def _choose(items, results, limit):
    by_ref = {result.ref: result for result in results}
    return tuple(TitleSelectionItem(item.document_id, item.revision,
                                    "selected" if index < limit else "not_selected",
                                    by_ref[item.ref].matter_key, by_ref[item.ref].stage_key,
                                    "全局排序" if index < limit else "超出文章上限",
                                    selected_rank=index + 1 if index < limit else None)
                 for index, item in enumerate(items))


def _review_fixture():
    items = _items(5)
    results = (
        TitleTriageResult("doc-0", 1, "candidate", "a", "initial", "主事实"),
        TitleTriageResult("doc-1", 1, "candidate", "b", "initial", "同类报道"),
        TitleTriageResult("doc-2", 1, "correction_or_denial", "c", "denial", "重大否认"),
        TitleTriageResult("doc-3", 1, "same_matter", "b", "initial", "转载"),
        TitleTriageResult("doc-4", 1, "no_value", "d", "none", "无关"),
    )
    selection_items = (
        TitleSelectionItem("doc-0", 1, "selected", "a", "initial", "拟深读 A", selected_rank=1),
        TitleSelectionItem("doc-1", 1, "selected", "b", "initial", "拟深读 B", selected_rank=2),
        TitleSelectionItem("doc-2", 1, "selected", "c", "denial", "拟深读反证", selected_rank=3),
        TitleSelectionItem("doc-3", 1, "merged", "b", "initial", "原合并", merged_into=("doc-1", 1)),
        TitleSelectionItem("doc-4", 1, "no_value", "d", "none", "无关"),
    )
    selection = TitleSelection("frozen", "evening", 80, tuple(item.ref for item in items), results,
                               selection_items, (("doc-0", 1), ("doc-1", 1), ("doc-2", 1)), "f" * 64)
    return items, results, selection


def test_title_payload_is_a_sealed_five_field_contract():
    item = _items(1)[0]
    assert tuple(title_batch_payload((item,))[0]) == ("documentId", "revision", "sourceKey", "publishedAt", "title")
    assert not hasattr(item, "metadata") and not hasattr(item, "excerpt") and not hasattr(item, "original_text")
    _, payload = batch_request_spec((item,), POLICY)
    assert payload["items"] == [title_batch_payload((item,))[0]]
    assert set(payload["output"]["items"][0]) == {"i", "status", "matterKey", "stageKey", "reason"}
    assert "text" not in str(payload) and "metadata" not in str(payload)


def test_indexed_batch_output_normalizes_to_the_same_real_refs_as_its_canonical_checkpoint():
    items = _items(2)
    indexed = {"items": [
        {"i": 1, "status": "uncertain", "matterKey": "matter-1", "stageKey": "initial", "reason": "待正文确认"},
        {"i": 0, "status": "candidate", "matterKey": "matter-0", "stageKey": "initial", "reason": "值得深读"},
    ]}
    legacy = {"items": [
        {"documentId": "doc-0", "revision": 1, "status": "candidate", "matterKey": "matter-0", "stageKey": "initial", "reason": "值得深读"},
        {"documentId": "doc-1", "revision": 1, "status": "uncertain", "matterKey": "matter-1", "stageKey": "initial", "reason": "待正文确认"},
    ]}
    normalized = normalize_batch_result(indexed, items)
    assert normalized == legacy
    assert validate_batch_result(indexed, items) == validate_batch_result(legacy, items)



@pytest.mark.parametrize("rows, message", [
    ([{"i": 0, "status": "candidate", "matterKey": "a", "stageKey": "initial", "reason": "a"}], "未完整覆盖"),
    ([{"i": 0, "status": "candidate", "matterKey": "a", "stageKey": "initial", "reason": "a"},
      {"i": 0, "status": "candidate", "matterKey": "b", "stageKey": "initial", "reason": "b"}], "重复"),
    ([{"i": 2, "status": "candidate", "matterKey": "a", "stageKey": "initial", "reason": "a"},
      {"i": 1, "status": "candidate", "matterKey": "b", "stageKey": "initial", "reason": "b"}], "越界"),
    ([{"i": True, "status": "candidate", "matterKey": "a", "stageKey": "initial", "reason": "a"},
      {"i": 1, "status": "candidate", "matterKey": "b", "stageKey": "initial", "reason": "b"}], "越界"),
])
def test_indexed_batch_result_rejects_missing_duplicate_out_of_range_or_boolean_indices(rows, message):
    with pytest.raises(TitleTriageProtocolError, match=message):
        validate_batch_result({"items": rows}, _items(2))


def test_all_batches_complete_before_one_global_selection_and_no_per_batch_top_n():
    calls = []
    checkpoints = []

    def batch(items):
        calls.append(tuple(item.ref for item in items))
        return _batch(items)

    selected = triage_titles(_items(9), window_kind="morning", policy=POLICY,
                             batch_call=batch, reconcile_call=_choose, batch_concurrency=1,
                             checkpoint=checkpoints.append)

    assert len(calls) == 3
    assert sum(len(call) for call in calls) == 9
    assert len(selected.batch_results) == 9
    assert len(selected.selected_refs) == 9  # morning is 40; no hidden per-batch quota
    assert checkpoints[-1]["stage"] == "title_selection"
    assert checkpoints[-1]["state"] == "frozen"


def test_title_batches_use_only_the_explicit_concurrency_and_reconcile_after_all_complete():
    items = _items(8)
    lock = threading.Lock()
    release = threading.Event()
    reached_parallelism = threading.Event()
    active = 0
    maximum_active = 0
    reconciled = False
    observed_results = ()

    def blocking_batch(batch):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
            if active == 2:
                reached_parallelism.set()
        assert release.wait(timeout=2), "test release was not signalled"
        with lock:
            active -= 1
        return _batch(batch)

    def reconcile(all_items, results, limit):
        nonlocal reconciled, observed_results
        reconciled = True
        observed_results = tuple(result.ref for result in results)
        return _choose(all_items, results, limit)

    outcome = []
    failures = []

    def run():
        try:
            outcome.append(triage_titles(items, window_kind="evening", policy={**POLICY, "batchSize": 2},
                                         batch_call=blocking_batch, reconcile_call=reconcile,
                                         batch_concurrency=2))
        except BaseException as exc:  # Thread target must retain any assertion failure.
            failures.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert reached_parallelism.wait(timeout=1), "two title batches did not run concurrently"
    assert reconciled is False
    release.set()
    thread.join(timeout=3)
    assert not thread.is_alive() and not failures
    assert maximum_active == 2
    assert observed_results == tuple(item.ref for item in items)
    assert outcome[0].selected_refs == tuple(item.ref for item in items)


def test_failed_parallel_batch_never_starts_global_reconciliation():
    reconciled = False

    def batch(batch_items):
        if batch_items[0].document_id == "doc-2":
            raise TitleTriageProtocolError("批次模型输出无效")
        return _batch(batch_items)

    def never(*args):
        nonlocal reconciled
        reconciled = True
        return ()

    with pytest.raises(TitleTriageProtocolError, match="批次模型输出无效"):
        triage_titles(_items(6), window_kind="evening", policy={**POLICY, "batchSize": 2},
                      batch_call=batch, reconcile_call=never, batch_concurrency=2)
    assert reconciled is False


def test_parallel_failure_stops_the_sliding_window_before_later_title_batches(monkeypatch):
    """A completed success/failure wave must be inspected before replenishing it."""
    called = []
    first_wave = threading.Barrier(2)
    reconciled = False

    def batch(batch_items):
        index = int(batch_items[0].document_id.removeprefix("doc-")) // 2
        called.append(index)
        if index in {0, 1}:
            first_wave.wait(timeout=1)
        if index == 0:
            raise TitleTriageProtocolError("first wave failed")
        return _batch(batch_items)

    # Make the scheduler observe the initial two completions as one event. This
    # deterministically covers a provider race where one completed batch succeeds
    # while its sibling has already failed.
    original_wait = title_triage_module.wait

    def observe_complete_first_wave(futures, *, return_when):
        if len(futures) == 2:
            return wait_for_futures(futures, return_when=ALL_COMPLETED)
        return original_wait(futures, return_when=return_when)

    monkeypatch.setattr(title_triage_module, "wait", observe_complete_first_wave)

    def never(*args):
        nonlocal reconciled
        reconciled = True
        return ()

    with pytest.raises(TitleTriageProtocolError, match="first wave failed"):
        triage_titles(_items(10), window_kind="evening", policy={**POLICY, "batchSize": 2},
                      batch_call=batch, reconcile_call=never, batch_concurrency=2)
    assert set(called) == {0, 1}
    assert reconciled is False


def test_cross_batch_repost_merges_but_correction_remains_a_real_selected_article():
    items = _items(5)

    def batch(batch_items):
        rows = []
        for item in batch_items:
            if item.document_id in {"doc-0", "doc-3"}:
                rows.append(TitleTriageResult(item.document_id, 1, "same_matter", "same", "initial", "转载"))
            elif item.document_id == "doc-4":
                rows.append(TitleTriageResult(item.document_id, 1, "correction_or_denial", "same", "denial", "公司否认"))
            else:
                rows.append(TitleTriageResult(item.document_id, 1, "no_value", item.document_id, "none", "无关"))
        return tuple(rows)

    def reconcile(all_items, results, limit):
        by_ref = {row.ref: row for row in results}
        return tuple(
            TitleSelectionItem(item.document_id, 1,
                               "selected" if item.document_id in {"doc-0", "doc-4"}
                               else "merged" if item.document_id == "doc-3" else "no_value",
                               by_ref[item.ref].matter_key, by_ref[item.ref].stage_key,
                               "入选" if item.document_id in {"doc-0", "doc-4"} else "同事项转载" if item.document_id == "doc-3" else "无关",
                               selected_rank={"doc-0": 1, "doc-4": 2}.get(item.document_id),
                               merged_into=("doc-0", 1) if item.document_id == "doc-3" else None)
            for item in all_items)

    selection = triage_titles(items, window_kind="evening", policy=POLICY,
                               batch_call=batch, reconcile_call=reconcile, batch_concurrency=1)
    assert selection.selected_refs == (("doc-0", 1), ("doc-4", 1))
    assert next(item for item in selection.items if item.document_id == "doc-3").merged_into == ("doc-0", 1)


@pytest.mark.parametrize(("window", "count", "expected"), [("evening", 103, 80), ("morning", 103, 40)])
def test_window_article_limits_are_article_counts_and_do_not_fill_missing(window, count, expected):
    selection = triage_titles(_items(count), window_kind=window, policy=POLICY,
                               batch_call=_batch, reconcile_call=_choose, batch_concurrency=1)
    assert len(selection.selected_refs) == expected
    assert selection.article_limit == expected

    sparse = triage_titles(_items(3), window_kind=window, policy=POLICY,
                           batch_call=_batch, reconcile_call=_choose, batch_concurrency=1)
    assert len(sparse.selected_refs) == 3


def test_missing_or_duplicate_batch_output_stops_before_global_selection():
    reconciled = False

    def missing(batch):
        return _batch(batch[:-1])

    def never(*args):
        nonlocal reconciled
        reconciled = True
        return ()

    with pytest.raises(TitleTriageProtocolError, match="完整且唯一"):
        triage_titles(_items(3), window_kind="evening", policy=POLICY,
                      batch_call=missing, reconcile_call=never, batch_concurrency=1)
    assert reconciled is False


def test_global_contract_rejects_forged_ids_and_correction_merge():
    items = _items(2)
    results = _batch(items)
    with pytest.raises(TitleTriageProtocolError, match="陌生 refIndex"):
        validate_reconcile_result({"selected": [{"i": 99, "selectedRank": 1, "reason": "x"}],
                                   "merged": [], "notSelected": [1]}, items, results, 80)
    correction = (TitleTriageResult("doc-0", 1, "correction_or_denial", "same", "denial", "否认"),
                  TitleTriageResult("doc-1", 1, "candidate", "same", "initial", "原报道"))
    with pytest.raises(TitleTriageProtocolError, match="更正/否认"):
        validate_reconcile_result({"selected": [{"i": 1, "selectedRank": 1, "reason": "入选"}],
                                   "merged": [{"i": 0, "into": 1, "reason": "转载"}], "notSelected": []},
                                  items, correction, 80)


def test_raw_decoders_and_global_request_keep_only_schema_fields():
    items = _items(2)
    batch = validate_batch_result({"items": [
        {"documentId": "doc-0", "revision": 1, "status": "candidate", "matterKey": "a", "stageKey": "initial", "reason": "a"},
        {"documentId": "doc-1", "revision": 1, "status": "uncertain", "matterKey": "b", "stageKey": "initial", "reason": "b"},
    ]}, items)
    instruction, payload = reconcile_request_spec(items, batch, 40, POLICY)
    assert payload["articleLimit"] == 40
    assert len(payload["items"]) == 2
    assert "references" not in payload
    assert all(set(row) == {"i", "sourceKey", "publishedAt", "title", "status", "matterKey", "stageKey"}
               for row in payload["items"])
    assert "selectionComplete=true" in instruction
    assert set(payload["output"]) == {"selectionComplete", "reviewedCount", "selected", "merged"}
    assert payload["output"]["merged"][0]["into"] == 0
    assert "originalText" not in str(payload) and "excerpt" not in str(payload)
    selection = validate_reconcile_result({"selected": [{"i": 0, "selectedRank": 1, "reason": "保留"}],
                                           "merged": [], "notSelected": [1]}, items, batch, 40)
    assert [item.disposition for item in selection] == ["selected", "not_selected"]


def test_compact_global_declaration_generates_the_full_not_selected_complement_without_filling_slots():
    items = _items(1430)
    batch = _batch(items)
    compact = {"selectionComplete": True, "reviewedCount": 1430,
               "selected": [{"i": 5, "selectedRank": 1, "reason": "唯一保留"}],
               "merged": [{"i": 6, "into": 5, "reason": "同一事项转载"}]}
    canonical = normalize_reconcile_result(compact, items, batch, 80)
    assert canonical["notSelected"] == [index for index in range(1430) if index not in {5, 6}]
    selection = validate_reconcile_result(compact, items, batch, 80)
    assert len(selection) == 1430
    assert len([item for item in selection if item.disposition == "not_selected"]) == 1428
    assert [item.ref for item in selection if item.disposition == "selected"] == [("doc-5", 1)]
    assert next(item for item in selection if item.ref == ("doc-6", 1)).merged_into == ("doc-5", 1)

    empty = validate_reconcile_result({"selectionComplete": True, "reviewedCount": 1430,
                                       "selected": [], "merged": []}, items, batch, 80)
    assert len(empty) == 1430
    assert not [item for item in empty if item.disposition == "selected"]
    assert len([item for item in empty if item.disposition == "not_selected"]) == 1430


@pytest.mark.parametrize("raw, message", [
    ({"reviewedCount": 2, "selected": [], "merged": []}, "字段无效"),
    ({"selectionComplete": False, "reviewedCount": 2, "selected": [], "merged": []}, "未明确完成"),
    ({"selectionComplete": True, "reviewedCount": True, "selected": [], "merged": []}, "reviewedCount"),
    ({"selectionComplete": True, "reviewedCount": 1, "selected": [], "merged": []}, "reviewedCount"),
    ({"selectionComplete": True, "reviewedCount": 2,
      "selected": [{"i": 7, "selectedRank": 1, "reason": "越界"}], "merged": []}, "陌生"),
    ({"selectionComplete": True, "reviewedCount": 2,
      "selected": [{"i": 0, "selectedRank": 1, "reason": "保留"}],
      "merged": [{"i": 1, "into": 1, "reason": "自合并"}]}, "合并"),
])
def test_compact_global_declaration_rejects_missing_completion_count_or_invalid_selection(raw, message):
    with pytest.raises(TitleTriageProtocolError, match=message):
        validate_reconcile_result(raw, _items(2), _batch(_items(2)), 80)


def test_title_final_review_is_title_only_and_can_only_prune_or_rewire_normal_duplicates():
    items, results, proposed = _review_fixture()
    operation, payload = review_request_spec(items, results, proposed, POLICY)
    assert payload["operation"] == "titleSelectionReview"
    assert len(payload["items"]) == 3
    assert set(payload["items"][0]) == {"i", "sourceKey", "publishedAt", "title", "status", "proposedReason"}
    assert "documentId" not in str(payload) and "正文" not in str(payload)
    assert "只能保留或删除" in operation and "proposedReason 当作证据" in operation

    review = {"complete": True,
              "kept": [{"i": 0, "reason": "标题明确新增实质事实"}],
              "removed": [
                  {"i": 1, "reason": "同次报道无新增事实", "duplicateOf": 0},
                  {"i": 2, "reason": "低价值更正，单独保留审计但不深读", "duplicateOf": None},
              ]}
    normalized = normalize_review_result(review, items, results, proposed)
    assert normalized == review
    final = apply_title_review(items, results, proposed, review)
    by_ref = {item.ref: item for item in final}
    assert by_ref[("doc-0", 1)].disposition == "selected"
    assert by_ref[("doc-0", 1)].selected_rank == 1
    assert by_ref[("doc-1", 1)].disposition == "merged"
    assert by_ref[("doc-1", 1)].merged_into == ("doc-0", 1)
    assert by_ref[("doc-2", 1)].disposition == "not_selected"  # correction cannot be swallowed
    assert by_ref[("doc-3", 1)].disposition == "merged"  # old merged target relinks through doc-1
    assert by_ref[("doc-3", 1)].merged_into == ("doc-0", 1)
    assert by_ref[("doc-4", 1)] == proposed.items[4]  # no_value is untouched


def test_title_final_review_allows_two_protected_corrections_to_reduce_without_merging_them():
    items = _items(2)
    results = tuple(TitleTriageResult(item.document_id, 1, "correction_or_denial", item.document_id,
                                      "denial", "同一低价值更正") for item in items)
    proposed_items = tuple(TitleSelectionItem(item.document_id, 1, "selected", item.document_id, "denial",
                                              "拟深读反证", selected_rank=index + 1)
                           for index, item in enumerate(items))
    proposed = TitleSelection("frozen", "evening", 80, tuple(item.ref for item in items), results,
                              proposed_items, tuple(item.ref for item in items), "e" * 64)
    review = {"complete": True, "kept": [{"i": 0, "reason": "保留一条反证"}],
              "removed": [{"i": 1, "reason": "同一更正不重复深读", "duplicateOf": 0}]}
    final = apply_title_review(items, results, proposed, review)
    assert next(item for item in final if item.ref == ("doc-0", 1)).disposition == "selected"
    assert next(item for item in final if item.ref == ("doc-1", 1)).disposition == "not_selected"


@pytest.mark.parametrize("review, message", [
    ({"complete": True, "kept": [{"i": 0, "reason": "保留"}],
      "removed": [{"i": 1, "reason": "删除", "duplicateOf": None}]}, "未完整覆盖"),
    ({"complete": True, "kept": [{"i": 0, "reason": "保留"}],
      "removed": [{"i": 0, "reason": "重复", "duplicateOf": None},
                  {"i": 1, "reason": "删除", "duplicateOf": None},
                  {"i": 2, "reason": "删除", "duplicateOf": None}]}, "重复"),
    ({"complete": True, "kept": [{"i": 0, "reason": "保留"}],
      "removed": [{"i": 1, "reason": "删除", "duplicateOf": 2},
                  {"i": 2, "reason": "删除", "duplicateOf": None}]}, "指向 kept"),
    ({"complete": True, "kept": [{"i": 0, "reason": "保留"}],
      "removed": [{"i": 1, "reason": "删除", "duplicateOf": 0},
                  {"i": 2, "reason": "反证被吞", "duplicateOf": 0}]}, "反证并入普通"),
])
def test_title_final_review_rejects_incomplete_duplicate_or_nonkept_duplicate_target(review, message):
    items, results, proposed = _review_fixture()
    with pytest.raises(TitleTriageProtocolError, match=message):
        normalize_review_result(review, items, results, proposed)


def test_frozen_incident_2472_titles_are_all_audited_without_body_or_search_calls():
    frozen_path = Path("/tmp/neckline-b36-validation/first-run-frozen.json")
    if not frozen_path.exists():
        pytest.skip("frozen incident input is unavailable")
    payload = json.loads(frozen_path.read_text())
    calls = []
    body_or_search_calls = 0
    items = []
    for row in payload["documents"]:
        metadata = json.loads(row["metadata_json"])
        items.append(TitleDTO(row["document_id"], int(row["revision"]), row["source_key"], row["published_at"], metadata["title"]))

    def fake_batch(batch):
        calls.append(tuple(item.ref for item in batch))
        # This fake receives sealed TitleDTO values; deliberately no article
        # fields are loaded from the frozen fixture after title extraction.
        assert all(not hasattr(item, "original_text") for item in batch)
        return tuple(TitleTriageResult(item.document_id, item.revision, "no_value", item.document_id,
                                       "none", "离线协议假数据") for item in batch)

    def fake_global(all_items, results, limit):
        by_ref = {result.ref: result for result in results}
        return tuple(TitleSelectionItem(item.document_id, item.revision, "no_value", by_ref[item.ref].matter_key,
                                        by_ref[item.ref].stage_key, "离线协议假数据")
                     for item in all_items)

    selection = triage_titles(tuple(items), window_kind="evening", policy={**POLICY, "batchSize": 97},
                               batch_call=fake_batch, reconcile_call=fake_global, batch_concurrency=1)
    assert len(selection.input_refs) == 2472
    assert sum(len(batch) for batch in calls) == 2472
    assert not selection.selected_refs
    assert body_or_search_calls == 0


@pytest.mark.parametrize(("window_kind", "expected_limit"), [("evening", 80), ("morning", 40)])
def test_frozen_incident_global_selection_enforces_80_40_with_cross_batch_merge_and_correction(window_kind, expected_limit):
    frozen_path = Path("/tmp/neckline-b36-validation/first-run-frozen.json")
    if not frozen_path.exists():
        pytest.skip("frozen incident input is unavailable")
    payload = json.loads(frozen_path.read_text())
    items = tuple(TitleDTO(row["document_id"], int(row["revision"]), row["source_key"], row["published_at"],
                           json.loads(row["metadata_json"])["title"])
                  for row in payload["documents"])
    positions = {item.ref: index for index, item in enumerate(items)}

    def fake_batch(batch):
        rows = []
        for item in batch:
            index = positions[item.ref]
            if index == 97:  # second 97-title batch: same event as item 0
                rows.append(TitleTriageResult(item.document_id, item.revision, "same_matter", "fixture-same", "initial", "跨批转载"))
            elif index == 98:  # same matter but a distinct correction stage
                rows.append(TitleTriageResult(item.document_id, item.revision, "correction_or_denial", "fixture-same", "denial", "跨批否认"))
            else:
                rows.append(TitleTriageResult(item.document_id, item.revision, "candidate", f"fixture-{index}", "initial", "离线候选"))
        return tuple(rows)

    def fake_global(all_items, results, limit):
        by_ref = {result.ref: result for result in results}
        selected_candidates = set(range(limit - 1))
        selected_candidates.add(98)
        output = []
        rank = 1
        for index, item in enumerate(all_items):
            result = by_ref[item.ref]
            if index in selected_candidates:
                output.append(TitleSelectionItem(item.document_id, item.revision, "selected", result.matter_key,
                                                 result.stage_key, "冻结入选", selected_rank=rank))
                rank += 1
            elif index == 97:
                output.append(TitleSelectionItem(item.document_id, item.revision, "merged", result.matter_key,
                                                 result.stage_key, "跨批转载合并", merged_into=all_items[0].ref))
            else:
                output.append(TitleSelectionItem(item.document_id, item.revision, "not_selected", result.matter_key,
                                                 result.stage_key, "全局未入选"))
        return tuple(output)

    selection = triage_titles(items, window_kind=window_kind, policy={**POLICY, "batchSize": 97},
                               batch_call=fake_batch, reconcile_call=fake_global, batch_concurrency=1)
    assert len(selection.input_refs) == len(selection.batch_results) == 2472
    assert len(selection.selected_refs) == selection.article_limit == expected_limit
    assert selection.selected_refs[-1] == items[98].ref
    assert next(item for item in selection.items if item.ref == items[97].ref).merged_into == items[0].ref


def test_discovery_refuses_nonselected_body_before_preparation_or_model(monkeypatch):
    documents = tuple(DiscoveryDocument(f"source-{index}", 1, "2026-09-08T12:00:00+00:00",
                                        "2026-09-08T12:01:00+00:00", "正文不应被读取", None,
                                        {"title": f"标题{index}"}) for index in range(2))
    prepared = 0

    def forbidden(document):
        nonlocal prepared
        prepared += 1
        return document

    monkeypatch.setattr("neckline.k10.discovery.prepare_document_for_analysis", forbidden)

    class Model:
        def understand(self, *, document):
            raise AssertionError("非入选文章不能进入正文模型")

    with pytest.raises(ValueError, match="恰好等于已冻结"):
        run_discovery(documents=documents, configuration=_run_configuration(), model=Model(),
                      verify=lambda event: None, metadata=object(),
                      cutoff_at=__import__("datetime").datetime.fromisoformat("2026-09-08T13:00:00+00:00"),
                      selected_source_refs=(EvidenceRef("source-0", 1),), article_limit=80)
    assert prepared == 0


def test_discovery_article_admission_preserves_frozen_order_and_count():
    documents = tuple(DiscoveryDocument(f"source-{index}", 1, "2026-09-08T12:00:00+00:00",
                                        "2026-09-08T12:01:00+00:00", "已准入正文", None,
                                        {"title": f"标题{index}"}) for index in range(2))
    understood = []

    class Model:
        def understand(self, *, document):
            understood.append(document.evidence_ref)
            return ()

    run = run_discovery(documents=documents, configuration=_run_configuration(), model=Model(),
                        verify=lambda event: None, metadata=object(),
                        cutoff_at=__import__("datetime").datetime.fromisoformat("2026-09-08T13:00:00+00:00"),
                        selected_source_refs=(EvidenceRef("source-1", 1), EvidenceRef("source-0", 1)), article_limit=80)
    assert run.state == "completed"
    assert understood == [EvidenceRef("source-1", 1), EvidenceRef("source-0", 1)]
    assert run.document_counts["articleLimit"] == 80
    assert run.document_counts["articleAdmitted"] == 2


def test_exact_body_dedup_preserves_all_source_refs_without_title_similarity_merging():
    duplicate_a = DiscoveryDocument("source-a", 1, "2026-09-08T12:00:00+00:00", "2026-09-08T12:01:00+00:00",
                                    "完全相同正文", None, {"title": "完全相同标题"})
    duplicate_b = DiscoveryDocument("source-b", 1, "2026-09-08T12:00:00+00:00", "2026-09-08T12:01:00+00:00",
                                    "完全相同正文", None, {"title": "完全相同标题"})
    same_title_different_body = DiscoveryDocument("source-c", 1, "2026-09-08T12:00:00+00:00", "2026-09-08T12:01:00+00:00",
                                                   "不同正文", None, {"title": "完全相同标题"})
    dedup = deduplicate_documents((duplicate_b, same_title_different_body, duplicate_a))
    assert {document.document_id for document in dedup.retained} == {"source-a", "source-c"}
    assert dedup.duplicates == {EvidenceRef("source-b", 1): EvidenceRef("source-a", 1)}

    class Model:
        def understand(self, *, document):
            if document.document_id == "source-a":
                return (EventDraft("same", "initial", "confirmed", "同一事项", "disclosure", {},
                                   (EvidenceRef("source-a", 1),)),)
            return ()

        def map_companies(self, *, event, verification):
            return ()

    run = run_discovery(documents=(duplicate_b, same_title_different_body, duplicate_a),
                        configuration=_run_configuration(), model=Model(),
                        verify=lambda event: Verification("verified", "核验", event.source_refs), metadata=object(),
                        cutoff_at=__import__("datetime").datetime.fromisoformat("2026-09-08T13:00:00+00:00"))
    assert run.document_counts["exactDeduplicated"] == 1
    assert set(run.events[0].source_refs) == {EvidenceRef("source-a", 1), EvidenceRef("source-b", 1)}
