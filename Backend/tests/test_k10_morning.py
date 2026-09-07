from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest

from neckline.k10.morning import MorningReportError, MorningUpdateError, build_morning_report_item, build_morning_update, group_morning_report_items, record_morning_update
from neckline.k10.providers import ProviderResolution
from neckline.k10.types import Task
from neckline.k10.worker import TaskContext
from neckline.llm.base import LLMResult


class FakeRepository:
    def __init__(self):
        self.appended = []

    def append_opportunity_update(self, **kwargs):
        self.appended.append(kwargs)


def _refs():
    return [{"documentId": "doc-2", "revision": 1, "publishedAt": "2026-09-07T08:30:00+08:00"}]


def test_unselected_candidate_gets_morning_update_without_auto_debate_or_replacement():
    update = build_morning_update(
        cutoff_at="2026-09-07T09:00:00+08:00", candidate_id="candidate-1", observation_id=None,
        reason_status="current", source_status="complete", observation_status="needs_review",
        material_contrary_evidence=[], source_refs=_refs(), summary="新增资料尚未触发深析。",
    )
    wire = update.to_dict()
    assert wire["observationId"] is None
    assert wire["automaticDebateStarted"] is False
    assert wire["requiresReview"] is True


def test_major_contrary_evidence_cannot_be_marked_current():
    with pytest.raises(MorningUpdateError, match="重大反证"):
        build_morning_update(
            cutoff_at="2026-09-07T09:00:00+08:00", candidate_id="candidate-1", observation_id="obs-1",
            reason_status="current", source_status="complete", observation_status="current",
            material_contrary_evidence=[{"documentId": "doc-2", "revision": 1, "claim": "公司否认关系"}],
            source_refs=_refs(), summary="出现反证。",
        )


def test_invalidated_reason_requires_a_concrete_material_contrary_record():
    with pytest.raises(MorningUpdateError, match="理由失效"):
        build_morning_update(
            cutoff_at="2026-09-07T09:00:00+08:00", candidate_id="candidate-1", observation_id="obs-1",
            reason_status="invalidated", source_status="complete", observation_status="current",
            material_contrary_evidence=[], source_refs=_refs(), summary="理由失效。",
        )


def test_verified_contrary_evidence_appends_withdrawal_without_window_mutation(tmp_path: Path):
    repository = FakeRepository()
    update = build_morning_update(
        cutoff_at="2026-09-07T09:00:00+08:00", candidate_id="candidate-1", observation_id=None,
        reason_status="invalidated", source_status="complete", observation_status="current",
        material_contrary_evidence=[{"documentId": "doc-2", "revision": 1, "claim": "公司明确否认"}],
        source_refs=_refs(), summary="已核反证，撤回推荐。",
    )
    record_morning_update(repository=repository, db_path=tmp_path / "k10.db", update=update,
                          opportunity_id="opportunity-1", update_id="morning-withdraw", created_at="2026-09-07T09:01:00+08:00")
    assert repository.appended[0]["kind"] == "withdrawal"
    assert "d1TradeDate" not in repository.appended[0]["content"]


def test_recording_morning_update_appends_a_lifecycle_event(tmp_path: Path):
    repository = FakeRepository()
    update = build_morning_update(
        cutoff_at="2026-09-07T09:00:00+08:00", candidate_id="candidate-1", observation_id="obs-1",
        reason_status="needs_review", source_status="partial", observation_status="unavailable",
        material_contrary_evidence=[{"documentId": "doc-2", "revision": 1, "claim": "待核反证"}],
        source_refs=_refs(), summary="资料范围不完整，等待人工查看。",
    )
    update_id = record_morning_update(
        repository=repository, db_path=tmp_path / "k10.db", update=update,
        opportunity_id="opportunity-1", update_id="morning-1", created_at="2026-09-07T09:01:00+08:00",
    )
    assert update_id == "morning-1"
    assert repository.appended[0]["content"]["reasonStatus"] == "needs_review"
    assert repository.appended[0]["kind"] == "risk"
    assert set(repository.appended[0]) == {"lifecycle_event_id", "opportunity_id", "kind", "reason", "source_refs", "content", "occurred_at", "created_at", "db_path"}


def test_morning_runtime_records_complete_no_change_as_continuing_report_item(tmp_path, monkeypatch):
    from neckline.k10 import morning_runtime

    class Provider:
        def chat(self, *_args, **_kwargs):
            return LLMResult(ok=True, content='{"material":false,"reasonStatus":"current","observationStatus":"current","summary":"无实质变化。","materialContraryEvidence":[]}', provider="deepseek", model="deepseek-v4-pro")
    monkeypatch.setattr(morning_runtime.store, "read_run_config", lambda **_: {"payload": {"modelRoutes": {"morning": "deepseek-v4-pro"}, "taskPolicies": {"morning": {"timeoutSeconds": 90, "modelMaxAttempts": 3, "costLimit": None}}}})
    monkeypatch.setattr(morning_runtime, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution("configured", Provider(), "deepseek", None))
    monkeypatch.setattr(morning_runtime.store, "load_candidate_context", lambda **_: {"observationIds": [], "candidate": {"candidateId": "candidate-1"}, "opportunity": {"opportunityId": "opp-1", "state": "active"}})
    monkeypatch.setattr(morning_runtime.store, "load_document_versions", lambda *, refs, **_: [
        {"documentId": ref["documentId"], "revision": ref["revision"], "fetchedAt": "2026-09-07T08:00:00+08:00"}
        for ref in refs
    ])
    task = Task("morning-task", "morning_review", "queued", 1, None, None, {"candidateId": "candidate-1", "originalCutoffAt": "2026-09-06T21:00:00+08:00", "morningEvidenceRefs": [{"documentId": "doc-2", "revision": 1}], "independentVerificationRefs": [{"documentId": "doc-3", "revision": 1}], "companyWindowId": "window-1", "displayRank": 1, "selectionState": "kept", "lifecycle": "active", "isNew": False, "sourceStatus": "complete", "configId": "cfg", "configRevision": 1})
    context = TaskContext(task, {}, {}, "v", "2026-09-07T09:00:00+08:00", tmp_path / "isolated.db", Event())
    result = morning_runtime.morning_review_handler(context)
    assert result.status == "completed"
    assert result.stage == "continued"
    assert result.checkpoint["reportItem"]["content"]["section"] == "continuing_or_expiring"


def test_morning_report_sections_are_complete_ranked_and_partial_never_claims_no_change():
    common = {"opportunity_id": "opp-1", "company_window_id": "window-1", "selection_state": "skipped",
              "lifecycle": "active", "summary": "资料说明", "coverage": {"state": "complete"},
              "source_refs": _refs(), "independent_verification_refs": _refs()}
    continuing = build_morning_report_item(item_id="item-2", display_rank=2, source_status="complete",
        reason_status="current", material=False, is_new=False, **common)
    partial = build_morning_report_item(item_id="item-1", display_rank=1, source_status="partial",
        reason_status="needs_review", material=False, is_new=False, **common)
    groups = group_morning_report_items([continuing, partial])
    assert list(groups) == ["major_contrary", "thesis_changed", "continuing_or_expiring", "new", "needs_review"]
    assert groups["continuing_or_expiring"][0]["content"]["section"] == "continuing_or_expiring"
    assert groups["needs_review"][0]["content"]["section"] == "needs_review"
    expired = build_morning_report_item(item_id="item-0", display_rank=1, source_status="complete",
        reason_status="current", material=False, is_new=False, lifecycle="expired", **{key: value for key, value in common.items() if key != "lifecycle"})
    assert expired.section == "continuing_or_expiring"
    with pytest.raises(MorningReportError, match="独立核验"):
        build_morning_report_item(item_id="item-3", display_rank=3, source_status="complete", reason_status="needs_review",
            material=True, is_new=False, independent_verification_refs=[], **{key: value for key, value in common.items() if key != "independent_verification_refs"})
