"""Offline B76 compound-research contract and atomic-projection regressions."""
from __future__ import annotations

from dataclasses import replace

import pytest

from neckline.k10 import research_store
from neckline.k10.investigation import InvestigationError, decode_merged_stage_result, validate_merged_stage_result
from neckline.k10.research_contracts import Claim, QueryPath, Question, ResearchStageResult
from neckline.k10.research_store import (
    advance_research_snapshot_batch,
    create_research_snapshot,
    read_research_snapshot,
    read_research_state,
)
from neckline.k10.research_context import project_packet, public_packet
from neckline.k10.research_runtime import _Investigation
from tests.test_v310_research_storage import LATER, _seed


_REF = {"documentId": "source-1", "revision": 1}


def _claim() -> Claim:
    return Claim("claim-1", "供应商称项目进入送样", "rumor", "new_fact", "供应商", "项目", "产品", "送样",
                 "需确认", "今日", "unverified", "会影响阶段判断", _REF, "p:2")


def _question(question_id: str = "question-1", *, codes: tuple[str, ...] = ("300001.SZ",)) -> Question:
    return Question(question_id, ("claim-1",), codes, "项目是否进入送样", (_REF,), ("独立项目方确认",),
                    "项目方确认送样", "项目方否认", "会改变公司比较", "open", "取得独立来源")


def _path(path_id: str = "path-1", question_id: str = "question-1") -> QueryPath:
    return QueryPath(path_id, question_id, "项目 送样 公告", "确认送样", "公司公告", "没有已尝试路径",
                     "确认主体", "改变比较", "planned", purpose_kind="event_fact",
                     target_refs=({"kind": "claim", "claimId": "claim-1"},))


def _packet(*, question_rows=(), pool=()) -> dict:
    return {
        "contextProtocol": True,
        "allowedEvidenceRefs": [_REF],
        "claims": [_claim().to_dict()],
        "questions": list(question_rows),
        "openQuestionIds": [row["questionId"] for row in question_rows if row.get("state") == "open"],
        "attemptedPathSignatures": [],
        "companyScope": {"fixedPool": [{"companyCode": code} for code in pool]},
    }


def _question_payload(question_id: str, code: str) -> dict:
    return _question(question_id, codes=(code,)).to_dict()


def _path_payload(path_id: str, question_id: str) -> dict:
    return _path(path_id, question_id).to_dict()


def test_plan_research_prunes_pool_outside_optional_question_and_its_route():
    packet = _packet(pool=("300001.SZ",))
    result = decode_merged_stage_result({
        "action": "plan_research",
        "questions": [_question_payload("in-pool", "300001.SZ"), _question_payload("outside", "600000.SH")],
        "queryPaths": [_path_payload("path-in", "in-pool"), _path_payload("path-out", "outside")],
    }, action="plan_research", evidence_packet=packet)

    validate_merged_stage_result(action="plan_research", result=result, evidence_packet=packet)
    assert [row.question_id for row in result.plan_gaps.questions] == ["in-pool"]
    assert [row.path_id for row in result.plan_queries.query_paths] == ["path-in"]

    with pytest.raises(InvestigationError, match="根字段") as raised:
        decode_merged_stage_result({"action": "plan_research", "claims": "unexpected"},
                                    action="plan_research", evidence_packet=packet)
    assert raised.value.code == "investigation_result_invalid"


def test_compound_decoder_restores_only_an_unmistakable_omitted_root_action():
    packet = _packet(pool=("300001.SZ",))
    plan_root = {
        "questions": [_question_payload("in-pool", "300001.SZ")],
        "queryPaths": [_path_payload("path-in", "in-pool")],
    }
    restored_plan = decode_merged_stage_result(plan_root, action="plan_research", evidence_packet=packet)
    assert restored_plan.action == "plan_research"
    assert [row.question_id for row in restored_plan.plan_gaps.questions] == ["in-pool"]

    question = _question().to_dict()
    assess_packet = _packet(question_rows=(question,), pool=("300001.SZ",))
    assess_root = {
        "claims": [], "questions": [], "evidenceUpdates": [], "fulltextRequests": [], "queryPaths": [],
        "conclusion": {"researchStatus": "pending_verification", "companyMappings": []},
    }
    restored_assess = decode_merged_stage_result(assess_root, action="assess_and_decide",
                                                  evidence_packet=assess_packet)
    assert restored_assess.action == "assess_and_decide"
    assert restored_assess.close_research.conclusion["researchStatus"] == "pending_verification"

    with pytest.raises(InvestigationError) as raised:
        decode_merged_stage_result({"action": "assess_and_decide", **plan_root},
                                    action="plan_research", evidence_packet=packet)
    assert raised.value.code == "investigation_action_mismatch"


def test_assess_and_decide_owns_fulltext_once_and_refuses_empty_continue_path():
    question = _question().to_dict()
    packet = _packet(question_rows=(question,), pool=("300001.SZ",))
    root = {
        "action": "assess_and_decide", "claims": [], "questions": [], "evidenceUpdates": [], "queryPaths": [],
        "fulltextRequests": [{"requestId": "body-1", "questionId": "question-1", "sourceRef": _REF,
                              "reasonExcerptInsufficient": "摘要不足以判断阶段", "expectedJudgmentChange": "改变比较",
                              "state": "requested", "admissionRef": None}],
        "conclusion": {"researchStatus": "continue_research", "companyMappings": []},
    }
    result = decode_merged_stage_result(root, action="assess_and_decide", evidence_packet=packet)
    validate_merged_stage_result(action="assess_and_decide", result=result, evidence_packet=packet)
    assert [row.request_id for row in result.assess_evidence.fulltext_requests] == ["body-1"]
    assert result.close_research.fulltext_requests == ()

    no_increment = {**root, "fulltextRequests": []}
    result = decode_merged_stage_result(no_increment, action="assess_and_decide", evidence_packet=packet)
    with pytest.raises(InvestigationError, match="继续研究") as raised:
        validate_merged_stage_result(action="assess_and_decide", result=result, evidence_packet=packet)
    assert raised.value.code == "investigation_closure_required"


def test_compound_packet_batches_only_question_scoped_company_manifests():
    profiles = [
        {"identity": {"ts_code": "300001.SZ", "name": "目标公司"}, "summary": "只可局部读取的业务字段",
         "review_status": "local_draft_awaiting_user", "fieldManifest": [{"field": "relationships", "contentSha256": "1" * 64, "kind": "list"}],
         "relationships": [{"private": "must not be sent"}]},
        {"identity": {"ts_code": "300002.SZ", "name": "无关公司"}, "summary": "UNRELATED_PRIVATE_HISTORY",
         "review_status": "local_draft_awaiting_user", "fieldManifest": [{"field": "relationships", "contentSha256": "2" * 64, "kind": "list"}],
         "relationships": [{"private": "must not be sent"}]},
    ]
    packet = {
        "companyScope": {"fixedPool": [{"companyCode": "300001.SZ"}, {"companyCode": "300002.SZ"}],
                         "candidateCompanyCodes": ["300001.SZ", "300002.SZ"], "companyProfiles": profiles},
        "claims": [_claim().to_dict()],
        "questions": [_question("open", codes=("300001.SZ",)).to_dict(),
                      {**_question("closed", codes=("300002.SZ",)).to_dict(), "state": "answered", "missingEvidence": []}],
        "evidenceCards": [{**_REF, "sourceStatements": [{"statement": "送样"}]}],
        "allowedEvidenceRefs": [_REF], "evidenceUpdates": [], "queryPaths": [], "fulltextRequests": [],
    }
    for action in ("plan_research", "assess_and_decide"):
        projected = public_packet(project_packet(action, packet))
        scope = projected["companyScope"]
        assert "fixedPool" not in scope
        assert [row["companyCode"] for row in scope["companyProfiles"]] == ["300001.SZ"]
        assert scope["candidateCompanyCodes"] == ["300001.SZ"]
        assert "UNRELATED_PRIVATE_HISTORY" not in str(projected)
        assert projected["visibleContext"]["companyFields"] == [{
            "companyCode": "300001.SZ", "fields": ["companyCode", "fieldManifest", "identity", "review_status", "summary"],
            "contentSha256": projected["visibleContext"]["companyFields"][0]["contentSha256"],
        }]


def test_initial_direct_path_survives_profile_manifest_refinement_only():
    """Program scope derives from the accepted B78 question, never a reply."""
    runtime = object.__new__(_Investigation)
    question = _question()
    path = _path()

    first = runtime._b78_bound_query_path(path, question)
    # Making additional fields of the same frozen company profile visible
    # does not change the direct question, so it cannot make a paid route new.
    refined = runtime._b78_bound_query_path(path, question)
    assert first.question_scope == refined.question_scope

    changed_authority = replace(question, support_condition="公司正式订单公告确认")
    rebound = runtime._b78_bound_query_path(path, changed_authority)
    assert rebound.question_scope != first.question_scope
    with pytest.raises(InvestigationError) as raised:
        runtime._b78_bound_query_path(replace(path, question_scope=first.question_scope), changed_authority)
    assert raised.value.code == "investigation_path_scope_invalid"


def test_one_compound_receipt_is_all_or_nothing_and_exact_replay_does_not_append(tmp_path, monkeypatch):
    db_path = tmp_path / "compound.sqlite"
    initial = _seed(db_path)
    create_research_snapshot(snapshot=initial, db_path=db_path)
    stages = (
        (ResearchStageResult("plan_gaps", questions=(_question(),)), "a" * 64),
        (ResearchStageResult("plan_queries", query_paths=(_path(),)), "b" * 64),
    )

    original_append = research_store._append_research_stage
    calls = 0

    def fail_between_derivatives(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic interruption between derived stages")
        return original_append(*args, **kwargs)

    monkeypatch.setattr(research_store, "_append_research_stage", fail_between_derivatives)
    with pytest.raises(RuntimeError, match="between derived"):
        advance_research_snapshot_batch(
            snapshot_id=initial.snapshot_id, expected_revision=1, research_status="continue_research",
            execution_status="ok", stage_results=stages, updated_at=LATER, db_path=db_path,
        )
    assert read_research_snapshot(snapshot_id=initial.snapshot_id, db_path=db_path).revision == 1
    assert read_research_state(snapshot_id=initial.snapshot_id, db_path=db_path)["stageResults"] == []

    monkeypatch.setattr(research_store, "_append_research_stage", original_append)
    committed = advance_research_snapshot_batch(
        snapshot_id=initial.snapshot_id, expected_revision=1, research_status="continue_research",
        execution_status="ok", stage_results=stages, updated_at=LATER, db_path=db_path,
    )
    replay = advance_research_snapshot_batch(
        snapshot_id=initial.snapshot_id, expected_revision=1, research_status="continue_research",
        execution_status="ok", stage_results=stages, updated_at=LATER, db_path=db_path,
    )
    assert (committed.revision, replay.revision) == (3, 3)
    state = read_research_state(snapshot_id=initial.snapshot_id, db_path=db_path)
    assert [(row["revision"], row["action"], row["inputSha256"]) for row in state["stageResults"]] == [
        (2, "plan_gaps", "a" * 64), (3, "plan_queries", "b" * 64),
    ]
