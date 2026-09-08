"""Typed, persisted contracts for the B39 proposition-investigation flow."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


RESEARCH_STATUSES = frozenset({
    "ready_for_comparison", "continue_research", "pending_verification",
    "abandon_recommendation", "background_only", "comparison_complete",
})
EXECUTION_STATUSES = frozenset({"ok", "paused", "failed"})
CLAIM_KINDS = frozenset({"factual_assertion", "forecast", "opinion", "promotion", "rumor"})
CLAIM_NOVELTIES = frozenset({"new_fact", "new_stage", "background", "republication", "uncertain"})
VERIFICATION_STATUSES = frozenset({"verified", "partially_supported", "unverified", "contradicted"})
COMPANY_ROLES = frozenset({"primary", "alternative", "tied", "pending", "excluded"})
EVIDENCE_RELATIONS = frozenset({"supports", "partially_supports", "contradicts", "duplicate", "irrelevant", "conflicts"})
QUESTION_STATES = frozenset({"open", "answered", "blocked", "abandoned"})
QUERY_STATES = frozenset({"planned", "searched", "no_result", "blocked"})
FULLTEXT_STATES = frozenset({"requested", "admitted", "rejected", "fulfilled"})
RESEARCH_ACTIONS = frozenset({
    "extract_claims", "plan_gaps", "plan_queries", "assess_evidence", "close_research", "compare_companies",
})


class ResearchContractError(ValueError):
    """A model result or persisted payload violated the investigation contract."""


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchContractError(f"{field_name} 必须为非空字符串")
    return value


def _positive(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ResearchContractError(f"{field_name} 必须为正整数")
    return value


def _enum(value: Any, allowed: frozenset[str], field_name: str) -> str:
    if value not in allowed:
        raise ResearchContractError(f"{field_name} 不在允许范围")
    return str(value)


def _refs(value: Any, field_name: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ResearchContractError(f"{field_name} 必须为真实来源引用列表")
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ResearchContractError(f"{field_name} 包含无效来源引用")
        document_id = _text(item.get("documentId"), f"{field_name}.documentId")
        revision = _positive(item.get("revision"), f"{field_name}.revision")
        key = (document_id, revision)
        if key in seen:
            continue
        seen.add(key)
        refs.append({"documentId": document_id, "revision": revision})
    return tuple(refs)


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name)


@dataclass(frozen=True)
class EvidenceDisclosure:
    verification_status: str
    is_rumor: bool
    origin_status: str
    origin_evidence_ref: Mapping[str, Any] | None
    unverified_reasons: tuple[str, ...] = ()
    conditional_analysis: str | None = None

    def __post_init__(self) -> None:
        _enum(self.verification_status, VERIFICATION_STATUSES, "verificationStatus")
        if not isinstance(self.is_rumor, bool):
            raise ResearchContractError("isRumor 必须是 bool")
        _enum(self.origin_status, frozenset({"identified", "unknown"}), "originStatus")
        if self.origin_status == "identified":
            if len(_refs([self.origin_evidence_ref], "originEvidenceRef")) != 1:
                raise ResearchContractError("identified origin 必须有真实引用")
        elif self.origin_evidence_ref is not None:
            raise ResearchContractError("unknown origin 不得伪造来源引用")
        if self.verification_status == "unverified" and not self.unverified_reasons:
            raise ResearchContractError("未核实披露必须说明未证实环节")
        if self.is_rumor:
            if self.verification_status == "verified":
                raise ResearchContractError("传闻不能标记为已核实")
            if not _text(self.conditional_analysis, "conditionalAnalysis"):
                raise ResearchContractError("传闻披露必须有条件化分析")
        if any(not isinstance(reason, str) or not reason.strip() for reason in self.unverified_reasons):
            raise ResearchContractError("unverifiedReasons 必须是非空字符串")

    def to_dict(self) -> dict[str, Any]:
        return {
            "verificationStatus": self.verification_status, "isRumor": self.is_rumor,
            "originStatus": self.origin_status, "originEvidenceRef": None if self.origin_evidence_ref is None
            else dict(_refs([self.origin_evidence_ref], "originEvidenceRef")[0]),
            "unverifiedReasons": list(self.unverified_reasons), "conditionalAnalysis": self.conditional_analysis,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceDisclosure":
        if not isinstance(value, Mapping):
            raise ResearchContractError("EvidenceDisclosure 必须为对象")
        reasons = value.get("unverifiedReasons", ())
        if not isinstance(reasons, Sequence) or isinstance(reasons, (str, bytes)):
            raise ResearchContractError("unverifiedReasons 必须为列表")
        clean_reasons = tuple(_text(item, "unverifiedReasons") for item in reasons)
        return cls(
            verification_status=_enum(value.get("verificationStatus"), VERIFICATION_STATUSES, "verificationStatus"),
            is_rumor=value.get("isRumor"),
            origin_status=_enum(value.get("originStatus"), frozenset({"identified", "unknown"}), "originStatus"),
            origin_evidence_ref=value.get("originEvidenceRef"),
            unverified_reasons=clean_reasons,
            conditional_analysis=value.get("conditionalAnalysis"),
        )


@dataclass(frozen=True)
class Claim:
    claim_id: str
    text: str
    kind: str
    novelty: str
    speaker: str | None
    subject: str | None
    object: str | None
    action: str | None
    stage_or_condition: str | None
    time_text: str | None
    verification_status: str
    decision_impact: str
    source_ref: Mapping[str, Any]
    location: str

    def __post_init__(self) -> None:
        _text(self.claim_id, "claimId"); _text(self.text, "claimText")
        _enum(self.kind, CLAIM_KINDS, "claimKind")
        _enum(self.novelty, CLAIM_NOVELTIES, "novelty")
        _enum(self.verification_status, VERIFICATION_STATUSES, "verificationStatus")
        _text(self.decision_impact, "decisionImpact"); _text(self.location, "location")
        _refs([self.source_ref], "sourceRef")

    def to_dict(self) -> dict[str, Any]:
        return {
            "claimId": self.claim_id, "text": self.text, "kind": self.kind, "novelty": self.novelty, "speaker": self.speaker,
            "subject": self.subject, "object": self.object, "action": self.action,
            "stageOrCondition": self.stage_or_condition, "timeText": self.time_text,
            "verificationStatus": self.verification_status, "decisionImpact": self.decision_impact,
            "sourceRef": dict(_refs([self.source_ref], "sourceRef")[0]), "location": self.location,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Claim":
        return cls(value.get("claimId"), value.get("text"), value.get("kind"), value.get("novelty"), value.get("speaker"),
                   value.get("subject"), value.get("object"), value.get("action"), value.get("stageOrCondition"),
                   value.get("timeText"), value.get("verificationStatus"), value.get("decisionImpact"),
                   value.get("sourceRef"), value.get("location"))


@dataclass(frozen=True)
class Question:
    question_id: str
    claim_ids: tuple[str, ...]
    company_codes: tuple[str, ...]
    question: str
    known_evidence: tuple[Mapping[str, Any], ...]
    missing_evidence: tuple[str, ...]
    support_condition: str
    refute_condition: str
    decision_impact: str
    state: str
    resume_condition: str | None

    def __post_init__(self) -> None:
        _text(self.question_id, "questionId"); _text(self.question, "question")
        _text(self.support_condition, "supportCondition"); _text(self.refute_condition, "refuteCondition")
        _text(self.decision_impact, "decisionImpact"); _enum(self.state, QUESTION_STATES, "state")
        if not self.claim_ids or any(not isinstance(item, str) or not item for item in self.claim_ids):
            raise ResearchContractError("question 必须关联命题")
        if any(not isinstance(item, str) or not item.strip() for item in self.company_codes):
            raise ResearchContractError("companyCodes 必须是非空代码组成的列表")
        _refs(self.known_evidence, "knownEvidence")
        if not self.missing_evidence or any(not isinstance(item, str) or not item for item in self.missing_evidence):
            raise ResearchContractError("question 必须声明证据缺口")

    def to_dict(self) -> dict[str, Any]:
        return {"questionId": self.question_id, "claimIds": list(self.claim_ids), "companyCodes": list(self.company_codes),
                "question": self.question, "knownEvidence": [dict(item) for item in _refs(self.known_evidence, "knownEvidence")],
                "missingEvidence": list(self.missing_evidence), "supportCondition": self.support_condition,
                "refuteCondition": self.refute_condition, "decisionImpact": self.decision_impact,
                "state": self.state, "resumeCondition": self.resume_condition}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Question":
        return cls(value.get("questionId"), tuple(value.get("claimIds", ())), tuple(value.get("companyCodes", ())),
                   value.get("question"), tuple(value.get("knownEvidence", ())), tuple(value.get("missingEvidence", ())),
                   value.get("supportCondition"), value.get("refuteCondition"), value.get("decisionImpact"),
                   value.get("state"), value.get("resumeCondition"))


@dataclass(frozen=True)
class QueryPath:
    path_id: str
    question_id: str
    query: str
    intent: str
    target_source: str
    new_path_reason: str
    expected_information_gain: str
    expected_judgment_change: str
    state: str
    result_summary: str | None = None

    def __post_init__(self) -> None:
        _text(self.path_id, "pathId"); _text(self.question_id, "questionId"); _text(self.query, "query")
        _text(self.intent, "intent"); _text(self.target_source, "targetSource")
        _text(self.new_path_reason, "newPathReason")
        _text(self.expected_information_gain, "expectedInformationGain")
        _text(self.expected_judgment_change, "expectedJudgmentChange")
        _enum(self.state, QUERY_STATES, "queryState")

    def to_dict(self) -> dict[str, Any]:
        return {"pathId": self.path_id, "questionId": self.question_id, "query": self.query, "intent": self.intent,
                "targetSource": self.target_source, "newPathReason": self.new_path_reason,
                "expectedInformationGain": self.expected_information_gain,
                "expectedJudgmentChange": self.expected_judgment_change,
                "state": self.state, "resultSummary": self.result_summary}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "QueryPath":
        return cls(value.get("pathId"), value.get("questionId"), value.get("query"), value.get("intent"),
                   value.get("targetSource"), value.get("newPathReason"), value.get("expectedInformationGain"),
                   value.get("expectedJudgmentChange"), value.get("state"), value.get("resultSummary"))


@dataclass(frozen=True)
class FullTextRequest:
    request_id: str
    question_id: str
    source_ref: Mapping[str, Any]
    reason_excerpt_insufficient: str
    expected_judgment_change: str
    state: str
    admission_ref: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _text(self.request_id, "requestId"); _text(self.question_id, "questionId")
        _refs([self.source_ref], "sourceRef")
        _text(self.reason_excerpt_insufficient, "reasonExcerptInsufficient")
        _text(self.expected_judgment_change, "expectedJudgmentChange")
        _enum(self.state, FULLTEXT_STATES, "fulltextState")
        if self.admission_ref is not None:
            _refs([self.admission_ref], "admissionRef")

    def to_dict(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id, "questionId": self.question_id,
            "sourceRef": dict(_refs([self.source_ref], "sourceRef")[0]),
            "reasonExcerptInsufficient": self.reason_excerpt_insufficient,
            "expectedJudgmentChange": self.expected_judgment_change, "state": self.state,
            "admissionRef": None if self.admission_ref is None else dict(_refs([self.admission_ref], "admissionRef")[0]),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FullTextRequest":
        return cls(value.get("requestId"), value.get("questionId"), value.get("sourceRef"),
                   value.get("reasonExcerptInsufficient"), value.get("expectedJudgmentChange"),
                   value.get("state"), value.get("admissionRef"))


@dataclass(frozen=True)
class ResearchSnapshot:
    snapshot_id: str
    task_id: str
    event_id: str
    event_revision: int
    news_cutoff_at: str
    verification_cutoff_at: str
    context_sha256: str
    prompt_contract_revision: str
    model_parameters_sha256: str
    research_status: str
    execution_status: str
    revision: int
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        for field_name in ("snapshot_id", "task_id", "event_id", "news_cutoff_at", "verification_cutoff_at",
                           "context_sha256", "prompt_contract_revision", "model_parameters_sha256", "created_at", "updated_at"):
            _text(getattr(self, field_name), field_name)
        _positive(self.event_revision, "eventRevision"); _positive(self.revision, "revision")
        _enum(self.research_status, RESEARCH_STATUSES, "researchStatus")
        _enum(self.execution_status, EXECUTION_STATUSES, "executionStatus")

    def to_dict(self) -> dict[str, Any]:
        return {"snapshotId": self.snapshot_id, "taskId": self.task_id, "eventId": self.event_id,
                "eventRevision": self.event_revision, "newsCutoffAt": self.news_cutoff_at,
                "verificationCutoffAt": self.verification_cutoff_at, "contextSha256": self.context_sha256,
                "promptContractRevision": self.prompt_contract_revision,
                "modelParametersSha256": self.model_parameters_sha256, "researchStatus": self.research_status,
                "executionStatus": self.execution_status, "revision": self.revision, "createdAt": self.created_at,
                "updatedAt": self.updated_at}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResearchSnapshot":
        return cls(value.get("snapshotId"), value.get("taskId"), value.get("eventId"), value.get("eventRevision"),
                   value.get("newsCutoffAt"), value.get("verificationCutoffAt"), value.get("contextSha256"),
                   value.get("promptContractRevision"), value.get("modelParametersSha256"), value.get("researchStatus"),
                   value.get("executionStatus"), value.get("revision"), value.get("createdAt"), value.get("updatedAt"))


@dataclass(frozen=True)
class ResearchStageResult:
    action: str
    safe_error_code: str | None = None
    claims: tuple[Claim, ...] = ()
    questions: tuple[Question, ...] = ()
    query_paths: tuple[QueryPath, ...] = ()
    evidence_updates: tuple[Mapping[str, Any], ...] = ()
    fulltext_requests: tuple[FullTextRequest, ...] = ()
    conclusion: Mapping[str, Any] | None = None
    company_assessments: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        _enum(self.action, RESEARCH_ACTIONS, "action")
        _optional_text(self.safe_error_code, "safeErrorCode")

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "safeErrorCode": self.safe_error_code, "claims": [item.to_dict() for item in self.claims],
                "questions": [item.to_dict() for item in self.questions],
                "queryPaths": [item.to_dict() for item in self.query_paths],
                "evidenceUpdates": [dict(item) for item in self.evidence_updates],
                "fulltextRequests": [item.to_dict() for item in self.fulltext_requests],
                "conclusion": None if self.conclusion is None else dict(self.conclusion),
                "companyAssessments": [dict(item) for item in self.company_assessments]}


def validate_company_assessment(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchContractError("company assessment 必须为对象")
    company_code = _text(value.get("companyCode"), "companyCode")
    role = _enum(value.get("role"), COMPANY_ROLES, "role")
    rank = value.get("rank")
    if role in {"primary", "alternative", "tied"}:
        _positive(rank, "rank")
    elif rank is not None:
        raise ResearchContractError("pending/excluded 不得有 rank")
    disclosure = EvidenceDisclosure.from_dict(value.get("evidenceDisclosure"))
    return {
        "companyCode": company_code, "role": role, "rank": rank, "summary": _text(value.get("summary"), "summary"),
        "priorityReason": _text(value.get("priorityReason"), "priorityReason"),
        "gap": _text(value.get("gap"), "gap"),
        "rankChangeConditions": _text(value.get("rankChangeConditions"), "rankChangeConditions"),
        "twoDayReason": _text(value.get("twoDayReason"), "twoDayReason"),
        "evidenceDisclosure": disclosure.to_dict(),
    }


__all__ = [
    "CLAIM_KINDS", "CLAIM_NOVELTIES", "COMPANY_ROLES", "EVIDENCE_RELATIONS", "EXECUTION_STATUSES", "FULLTEXT_STATES",
    "QUESTION_STATES", "QUERY_STATES", "RESEARCH_ACTIONS", "RESEARCH_STATUSES", "VERIFICATION_STATUSES",
    "Claim", "EvidenceDisclosure", "FullTextRequest", "QueryPath", "Question", "ResearchContractError", "ResearchSnapshot",
    "ResearchStageResult", "validate_company_assessment",
]
