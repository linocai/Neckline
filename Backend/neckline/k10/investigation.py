"""Typed, evidence-bounded K10 investigation stages.

The investigation is deliberately action based.  A completed model call is not a
research conclusion: the caller advances only the stage which the persisted
snapshot still needs, and persists the returned derivative before considering a
later stage.  This keeps retries/checkpoints outside the semantic contract while
making it impossible to turn a failed lookup into a completed comparison.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Protocol, Sequence

from .research_contracts import (
    RESEARCH_ACTIONS, RESEARCH_STATUSES, Claim, FullTextRequest, QueryPath, Question,
    ResearchContractError, ResearchSnapshot, ResearchStageResult,
    validate_company_assessment,
)


class InvestigationError(RuntimeError):
    """Safe investigation boundary failure; model bodies never leave this layer."""

    def __init__(self, message: str, *, code: str = "investigation_contract_invalid") -> None:
        super().__init__(message)
        self.code = code


class InvestigationModel(Protocol):
    def advance_research(self, *, snapshot: ResearchSnapshot, action: str,
                         evidence_packet: Mapping[str, Any]) -> ResearchStageResult:
        ...


class InvestigationGateway(Protocol):
    """The gateway is intentionally narrow: search and Extract remain external.

    Implementations own checkpoint/attempt records and article admission.  The
    semantic layer only asks for an already approved path/request and never reads
    provider response bodies directly.
    """

    def fetch(self, *, event: Any, retrieved_at: Any, cutoff_at: Any,
              cutoff_inclusive: bool = False, question: Mapping[str, Any] | Question | None = None,
              query_path: Mapping[str, Any] | QueryPath | None = None) -> Any:
        ...

    def fetch_fulltext(self, *, event: Any, document: Any, question: Mapping[str, Any],
                       request: Mapping[str, Any], cutoff_at: Any,
                       cutoff_inclusive: bool = False) -> Any:
        ...


@dataclass(frozen=True)
class InvestigationStep:
    """A validated, persistable result of exactly one investigation action."""

    snapshot_id: str
    input_sha256: str
    result: ResearchStageResult


def _digest(action: str, packet: Mapping[str, Any]) -> str:
    return sha256(json.dumps({"action": action, "packet": packet}, ensure_ascii=False,
                             sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _ref_keys(value: Any, *, field: str) -> set[tuple[str, int]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise InvestigationError(f"{field} 必须是来源引用列表")
    result: set[tuple[str, int]] = set()
    for item in value:
        if not isinstance(item, Mapping) or not isinstance(item.get("documentId"), str) or not item["documentId"]:
            raise InvestigationError(f"{field} 包含无效来源引用")
        revision = item.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise InvestigationError(f"{field} 包含无效来源修订")
        result.add((item["documentId"], revision))
    return result


def _packet_refs(packet: Mapping[str, Any]) -> set[tuple[str, int]]:
    refs = packet.get("allowedEvidenceRefs", ())
    return _ref_keys(refs, field="allowedEvidenceRefs")


def _assert_packet_boundary(*, action: str, packet: Mapping[str, Any]) -> None:
    if not isinstance(packet, Mapping):
        raise InvestigationError("研究证据包必须是对象")
    _packet_refs(packet)
    # Original frozen bodies are only needed to identify claims.  Every later
    # prompt works from real references, excerpts and structured derivatives.
    forbidden_body_keys = {"originalText", "original_text", "analysisText", "analysis_text"}
    if action != "extract_claims" and forbidden_body_keys & set(packet):
        raise InvestigationError("后续研究阶段不得重复传入冻结正文", code="investigation_body_reuse_forbidden")
    if action == "assess_evidence" and "fullTextDocuments" in packet:
        admitted = _ref_keys(packet.get("admittedFulltextRefs", ()), field="admittedFulltextRefs")
        fulltext = packet["fullTextDocuments"]
        if not isinstance(fulltext, Sequence) or isinstance(fulltext, (str, bytes)):
            raise InvestigationError("受控全文必须是文档列表")
        for item in fulltext:
            if not isinstance(item, Mapping):
                raise InvestigationError("受控全文文档无效")
            ref = (item.get("documentId"), item.get("revision"))
            if not isinstance(ref[0], str) or isinstance(ref[1], bool) or not isinstance(ref[1], int) or ref not in admitted:
                raise InvestigationError("全文未获准入", code="investigation_fulltext_unadmitted")


def _require_result(action: str, result: ResearchStageResult, packet: Mapping[str, Any]) -> None:
    if not isinstance(result, ResearchStageResult):
        raise InvestigationError("研究模型返回未通过 typed contract", code="investigation_result_invalid")
    if result.action != action:
        raise InvestigationError("研究模型返回了错误阶段", code="investigation_action_mismatch")
    if result.safe_error_code:
        raise InvestigationError("研究阶段执行失败", code=result.safe_error_code)
    allowed = _packet_refs(packet)
    if action == "extract_claims":
        seen: set[str] = set()
        for claim in result.claims:
            if claim.claim_id in seen:
                raise InvestigationError("命题 ID 重复", code="investigation_claim_duplicate")
            seen.add(claim.claim_id)
            ref = claim.source_ref
            if (ref.get("documentId"), ref.get("revision")) not in allowed:
                raise InvestigationError("命题引用未输入资料", code="investigation_reference_invalid")
    elif action == "plan_gaps":
        if any(question.state != "open" for question in result.questions):
            raise InvestigationError("缺口规划只能新建开放问题", code="investigation_question_state_invalid")
    elif action == "plan_queries":
        question_ids = {str(item) for item in packet.get("openQuestionIds", ())}
        seen_paths = {str(item) for item in packet.get("attemptedPathSignatures", ())}
        for path in result.query_paths:
            if path.question_id not in question_ids:
                raise InvestigationError("查询路径未关联开放问题", code="investigation_path_question_invalid")
            signature = query_path_signature(path)
            if signature in seen_paths:
                raise InvestigationError("查询路径没有新增证据路径", code="investigation_path_duplicate")
            seen_paths.add(signature)
    elif action == "close_research":
        if not isinstance(result.conclusion, Mapping):
            raise InvestigationError("研究收口缺少结论", code="investigation_conclusion_missing")
        status = result.conclusion.get("researchStatus")
        if status not in RESEARCH_STATUSES - {"comparison_complete"}:
            raise InvestigationError("研究收口状态无效", code="investigation_conclusion_invalid")
    elif action == "compare_companies":
        codes = {str(code) for code in packet.get("companyCodes", ())}
        seen_codes: set[str] = set()
        for assessment in result.company_assessments:
            clean = validate_company_assessment(assessment)
            code = clean["companyCode"]
            if code not in codes or code in seen_codes:
                raise InvestigationError("公司比较覆盖不完整或重复", code="investigation_company_coverage_invalid")
            seen_codes.add(code)
        if seen_codes != codes:
            raise InvestigationError("公司比较遗漏输入公司", code="investigation_company_coverage_invalid")


def query_path_signature(path: QueryPath) -> str:
    """Stable semantic identity used to reject a synonym/repost retry loop."""
    value = {"questionId": path.question_id, "query": path.query.strip().casefold(),
             "intent": path.intent.strip().casefold(),
             "targetSource": path.target_source.strip().casefold(),
             "expectedInformationGain": path.expected_information_gain.strip().casefold(),
             "expectedJudgmentChange": path.expected_judgment_change.strip().casefold()}
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def decode_stage_result(value: Mapping[str, Any], *, action: str) -> ResearchStageResult:
    """Decode a provider JSON derivative without retaining its original response."""
    if not isinstance(value, Mapping) or value.get("action") != action:
        raise InvestigationError("研究输出 action 无效", code="investigation_action_mismatch")
    try:
        claims = tuple(Claim.from_dict(item) for item in value.get("claims", ()))
        questions = tuple(Question.from_dict(item) for item in value.get("questions", ()))
        paths = tuple(QueryPath.from_dict(item) for item in value.get("queryPaths", ()))
        updates = value.get("evidenceUpdates", ())
        requests = value.get("fulltextRequests", ())
        assessments = value.get("companyAssessments", ())
        if (not isinstance(updates, Sequence) or isinstance(updates, (str, bytes))
                or not isinstance(requests, Sequence) or isinstance(requests, (str, bytes))
                or not isinstance(assessments, Sequence) or isinstance(assessments, (str, bytes))):
            raise ResearchContractError("研究阶段集合必须为列表")
        if any(not isinstance(item, Mapping) for item in (*updates, *requests, *assessments)):
            raise ResearchContractError("研究阶段集合项必须为对象")
        conclusion = value.get("conclusion")
        if conclusion is not None and not isinstance(conclusion, Mapping):
            raise ResearchContractError("研究结论必须为对象")
        return ResearchStageResult(action=action, safe_error_code=value.get("safeErrorCode"), claims=claims, questions=questions, query_paths=paths,
                                   evidence_updates=tuple(dict(item) for item in updates),
                                   fulltext_requests=tuple(FullTextRequest.from_dict(item) for item in requests),
                                   conclusion=None if conclusion is None else dict(conclusion),
                                   company_assessments=tuple(dict(item) for item in assessments))
    except ResearchContractError as exc:
        raise InvestigationError("研究输出不符合 typed contract", code="investigation_result_invalid") from exc


def advance_research(*, model: InvestigationModel, snapshot: ResearchSnapshot, action: str,
                     evidence_packet: Mapping[str, Any]) -> InvestigationStep:
    """Run one required research action and return its safe typed derivative.

    The caller owns durable snapshot advancement.  It must not call the next
    action after an exception, a paused attempt, or a failed persistence write.
    """
    if action not in RESEARCH_ACTIONS:
        raise InvestigationError("未知研究阶段", code="investigation_action_invalid")
    if snapshot.execution_status != "ok":
        raise InvestigationError("非正常执行状态不得推进研究", code="investigation_execution_not_ready")
    try:
        _assert_packet_boundary(action=action, packet=evidence_packet)
        result = model.advance_research(snapshot=snapshot, action=action, evidence_packet=evidence_packet)
        _require_result(action, result, evidence_packet)
    except ResearchContractError as exc:
        raise InvestigationError("研究结构不符合契约", code="investigation_contract_invalid") from exc
    return InvestigationStep(snapshot.snapshot_id, _digest(action, evidence_packet), result)


__all__ = [
    "InvestigationError", "InvestigationGateway", "InvestigationModel", "InvestigationStep",
    "advance_research", "decode_stage_result", "query_path_signature",
]
