"""Historical research decoding and evidence validation; new execution uses direct rounds."""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping, Protocol, Sequence

from .research_contracts import (
    MERGED_RESEARCH_ACTIONS, RESEARCH_ACTIONS, RESEARCH_STATUSES, QUERY_PURPOSE_KINDS, Claim, FullTextRequest, QueryPath, Question,
    MergedResearchResult, ResearchContractError, ResearchStageResult,
    validate_company_assessment, validate_company_mapping,
)


class InvestigationError(RuntimeError):
    """Safe investigation boundary failure; model bodies never leave this layer."""

    def __init__(self, message: str, *, code: str = "investigation_contract_invalid") -> None:
        super().__init__(message)
        self.code = code


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


def _require_result(action: str, result: ResearchStageResult, packet: Mapping[str, Any]) -> None:
    if not isinstance(result, ResearchStageResult):
        raise InvestigationError("研究模型返回未通过 typed contract", code="investigation_result_invalid")
    if result.action != action:
        raise InvestigationError("研究模型返回了错误阶段", code="investigation_action_mismatch")
    if result.safe_error_code:
        raise InvestigationError("研究阶段执行失败", code=result.safe_error_code)
    if result.context_requests:
        return  # local reads continue this action; no research state advances
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
            # This is a new context-protocol request. Legacy persisted paths
            # may still be inspected, but a freshly proposed paid route must
            # name the current-event question target before the runtime can
            # bind its durable scope.
            if packet.get("contextProtocol"):
                if path.purpose_kind not in QUERY_PURPOSE_KINDS or not path.target_refs:
                    raise InvestigationError("查询路径缺少问题范围目标", code="investigation_path_scope_invalid")
    elif action == "close_research":
        if not isinstance(result.conclusion, Mapping):
            raise InvestigationError("研究收口缺少结论", code="investigation_conclusion_missing")
        status = result.conclusion.get("researchStatus")
        if status not in RESEARCH_STATUSES - {"comparison_complete"}:
            raise InvestigationError("研究收口状态无效", code="investigation_conclusion_invalid")
    elif action == "compare_companies":
        if packet.get("publicationAllowed") is False and any(
                row.get("role") not in {"pending", "excluded"} for row in result.company_assessments):
            raise InvestigationError("关键缺口未解除，比较不得给出正式推荐", code="investigation_unresolved_ranking") from ResearchContractError(
                "本次比较只允许待核或排除", field_name="companyAssessments[].role", expected="enum", allowed=("pending", "excluded"))
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


def validate_stage_result(*, action: str, result: ResearchStageResult, evidence_packet: Mapping[str, Any]) -> None:
    _require_result(action, result, evidence_packet)


def _merged_context_requests(value: Mapping[str, Any], *, action: str,
                             evidence_packet: Mapping[str, Any] | None = None) -> MergedResearchResult | None:
    requests = value.get("contextRequests")
    if requests is None:
        return None
    if not isinstance(requests, list) or any(not isinstance(item, Mapping) for item in requests):
        raise InvestigationError("局部读取请求必须为对象列表", code="investigation_result_invalid")
    if requests:
        # Context-only responses intentionally have no derived semantic stage.
        # They are still retained by the existing receipt/checkpoint path before
        # the local read and same-action retry.
        local = (evidence_packet or {}).get("_localState", evidence_packet or {})
        known = {row.get("questionId") for row in local.get("questions", ())
                 if isinstance(row, Mapping) and isinstance(row.get("questionId"), str)}
        drafts = value.get("questions", ())
        draft_ids = {row.get("questionId") for row in drafts
                     if isinstance(row, Mapping) and isinstance(row.get("questionId"), str)} if isinstance(drafts, list) else set()
        normalized = []
        for request in requests:
            clean = dict(request)
            # A draft question in a context-only answer has no durable scope.
            # It must not prevent a valid company-local field read or become a
            # new hidden question merely because a provider bundled it beside
            # the read request.
            if clean.get("questionId") in draft_ids - known:
                clean.pop("questionId", None)
            normalized.append(clean)
        try:
            return MergedResearchResult(action=action, context_requests=tuple(normalized))
        except ResearchContractError as exc:
            raise InvestigationError("局部读取请求不符合 typed contract",
                                     code="investigation_result_invalid") from exc
    return None


def _merged_cached_stage(value: Mapping[str, Any], *, field: str, action: str,
                         evidence_packet: Mapping[str, Any]) -> ResearchStageResult:
    raw = value.get(field)
    if not isinstance(raw, Mapping):
        raise InvestigationError("合并研究缓存缺少派生阶段", code="investigation_result_invalid")
    return decode_stage_result(raw, action=action, evidence_packet=evidence_packet)


def decode_merged_stage_result(value: Mapping[str, Any], *, action: str,
                               evidence_packet: Mapping[str, Any] | None = None) -> MergedResearchResult:
    """Normalize one B76 reply into two old, persistable research actions.

    Provider responses keep a compact root shape; cached checkpoint derivatives
    carry named stage objects.  In both forms no new action reaches Schema 9.
    """
    if action not in MERGED_RESEARCH_ACTIONS:
        raise InvestigationError("未知合并研究阶段", code="investigation_action_invalid")
    if isinstance(value, Mapping) and set(value) == {"outputContract"} and isinstance(value["outputContract"], Mapping):
        value = value["outputContract"]
    root_required = ({"questions", "queryPaths"} if action == "plan_research" else
                     {"claims", "questions", "evidenceUpdates", "fulltextRequests", "queryPaths", "conclusion"})
    root_permitted = root_required | {"contextRequests"}
    if (isinstance(value, Mapping) and "action" not in value
            and set(value) <= root_permitted and root_required <= set(value)):
        # Match the legacy single-stage decoder's narrow repair for a provider
        # that omitted only routing metadata.  The caller has already fixed
        # the compound action, every root field uniquely identifies it, and
        # later typed validation still checks every submitted fact and source.
        value = {"action": action, **value}
    if not isinstance(value, Mapping) or value.get("action") != action:
        raise InvestigationError("研究输出 action 无效", code="investigation_action_mismatch") from ResearchContractError(
            "根 action 必须匹配请求", field_name="action", expected="enum", allowed=(action,))
    cached = (("planGaps", "planQueries") if action == "plan_research"
              else ("assessEvidence", "closeResearch"))
    if any(field in value for field in cached):
        if set(key for key in value if key not in {"action", "contextRequests", *cached}):
            raise InvestigationError("合并研究缓存字段无效", code="investigation_result_invalid")
        context = _merged_context_requests(value, action=action, evidence_packet=evidence_packet)
        if context is not None:
            return context
        first = _merged_cached_stage(value, field=cached[0],
                                     action=("plan_gaps" if action == "plan_research" else "assess_evidence"),
                                     evidence_packet=evidence_packet or {})
        second = _merged_cached_stage(value, field=cached[1],
                                      action=("plan_queries" if action == "plan_research" else "close_research"),
                                      evidence_packet=evidence_packet or {})
        return (MergedResearchResult(action=action, plan_gaps=first, plan_queries=second)
                if action == "plan_research" else
                MergedResearchResult(action=action, assess_evidence=first, close_research=second))
    context = _merged_context_requests(value, action=action, evidence_packet=evidence_packet)
    if context is not None:
        return context
    required = {"action", *root_required}
    permitted = {"action", *root_permitted}
    if set(value) - permitted or not required <= set(value):
        raise InvestigationError("合并研究根字段不符合契约", code="investigation_result_invalid")
    packet = evidence_packet or {}
    try:
        if action == "plan_research":
            plan = decode_stage_result({"action": "plan_gaps", "questions": value.get("questions", [])},
                                       action="plan_gaps", evidence_packet=packet)
            # A pool-outside optional question is locally removed by the
            # existing plan_gaps normalizer.  Its paired route is an optional
            # hint too; retaining it would turn that recoverable hint into a
            # whole-receipt path/question mismatch.
            planned_ids = {question.question_id for question in plan.questions}
            raw_paths = value.get("queryPaths", [])
            if isinstance(raw_paths, list):
                raw_paths = [row for row in raw_paths if not isinstance(row, Mapping)
                             or row.get("questionId") in planned_ids]
            # The route normalizer needs the same-receipt questions to decide
            # whether an optional mixed-company path has a valid, narrower
            # companion.  They are only a transient local view for this
            # normalization: persistence and runtime scope binding still use
            # the two derived legacy stages below.
            local = packet.get("_localState") if isinstance(packet.get("_localState"), Mapping) else packet
            current_questions = local.get("questions", ()) if isinstance(local.get("questions", ()), (list, tuple)) else ()
            query_packet = {**packet,
                "_localState": {**local, "questions": [*current_questions,
                                                            *(item.to_dict() for item in plan.questions)]},
                "openQuestionIds": [*packet.get("openQuestionIds", ()), *planned_ids]}
            paths = decode_stage_result({"action": "plan_queries", "queryPaths": raw_paths},
                                        action="plan_queries", evidence_packet=query_packet)
            return MergedResearchResult(action=action, plan_gaps=plan, plan_queries=paths)
        assess = decode_stage_result({
            "action": "assess_evidence", "claims": value.get("claims", []),
            "questions": value.get("questions", []), "queryPaths": value.get("queryPaths", []),
            "evidenceUpdates": value.get("evidenceUpdates", []),
            "fulltextRequests": value.get("fulltextRequests", []),
        }, action="assess_evidence", evidence_packet=packet)
        raw_close_questions = value.get("questions", [])
        if not isinstance(raw_close_questions, list):
            raw_close_questions = value.get("questions", [])
        close = decode_stage_result({
            "action": "close_research", "questions": raw_close_questions,
            # New routes are the assessment/decision derivative.  Recording
            # them again in close_research would conceal which old semantic
            # action owns the planned work on recovery.
            "queryPaths": [],
            # The same applies to admitted body requests.  Duplicating one
            # root request in both old actions would make _fulltexts fetch it
            # twice after recovery.  The assessment derivative owns every
            # concrete incremental request; the close derivative owns only
            # the resulting decision.
            "fulltextRequests": [], "conclusion": value.get("conclusion"),
        }, action="close_research", evidence_packet=packet)
        return MergedResearchResult(action=action, assess_evidence=assess, close_research=close)
    except InvestigationError:
        raise


def validate_merged_stage_result(*, action: str, result: MergedResearchResult,
                                 evidence_packet: Mapping[str, Any]) -> None:
    if not isinstance(result, MergedResearchResult) or result.action != action:
        raise InvestigationError("合并研究模型返回无效", code="investigation_result_invalid")
    if result.context_requests:
        return
    if action == "plan_research":
        assert result.plan_gaps is not None and result.plan_queries is not None
        _require_result("plan_gaps", result.plan_gaps, evidence_packet)
        # Initial paths may target questions created by this same receipt.  The
        # packet extends only those declared questions; no hidden company or
        # source facts are added.
        open_ids = [*evidence_packet.get("openQuestionIds", ()),
                    *(item.question_id for item in result.plan_gaps.questions)]
        query_packet = {**evidence_packet, "openQuestionIds": list(dict.fromkeys(open_ids))}
        _require_result("plan_queries", result.plan_queries, query_packet)
        return
    assert result.assess_evidence is not None and result.close_research is not None
    _require_result("assess_evidence", result.assess_evidence, evidence_packet)
    _require_result("close_research", result.close_research, evidence_packet)
    if result.assess_evidence.query_paths:
        open_ids = [item.get("questionId") for item in evidence_packet.get("questions", ())
                    if isinstance(item, Mapping) and item.get("state") == "open"]
        open_ids.extend(item.question_id for item in result.assess_evidence.questions if item.state == "open")
        _require_result("plan_queries", ResearchStageResult("plan_queries",
            query_paths=result.assess_evidence.query_paths),
            {**evidence_packet, "openQuestionIds": list(dict.fromkeys(item for item in open_ids if isinstance(item, str)))})
    conclusion = result.close_research.conclusion or {}
    if conclusion.get("researchStatus") == "continue_research":
        if evidence_packet.get("pathsExhausted") or not (
                result.assess_evidence.query_paths or result.assess_evidence.fulltext_requests):
            raise InvestigationError("继续研究必须带来可执行的新路径或全文定位", code="investigation_closure_required")


def query_path_signature(path: QueryPath) -> str:
    """Stable semantic identity used to reject a synonym/repost retry loop."""
    value = {"questionId": path.question_id, "query": path.query.strip().casefold(),
             "intent": path.intent.strip().casefold(),
             "targetSource": path.target_source.strip().casefold(),
             "expectedInformationGain": path.expected_information_gain.strip().casefold(),
             "expectedJudgmentChange": path.expected_judgment_change.strip().casefold(),
             "purposeKind": path.purpose_kind,
             "targetRefs": [dict(item) for item in path.target_refs]}
    if path.source_locator is not None:
        value["sourceLocator"] = dict(path.source_locator)
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _prune_assessment_references(value: Mapping[str, Any], packet: Mapping[str, Any]) -> dict[str, Any]:
    """Keep usable assessment updates without admitting invented evidence.

    Counts and diagnostics describe local filtering, never a research verdict.
    Unsupported status upgrades are removed along with their unusable links.
    """
    allowed = _packet_refs(packet)
    def ref_key(ref):
        if not isinstance(ref, Mapping):
            return None
        doc, revision = ref.get("documentId"), ref.get("revision")
        return (doc, revision) if isinstance(doc, str) and isinstance(revision, int) and not isinstance(revision, bool) else None
    collections = ("claims", "questions", "evidenceUpdates", "fulltextRequests")
    if any(not isinstance(value.get(key, ()), (list, tuple)) or
           any(not isinstance(row, Mapping) for row in value.get(key, ())) for key in collections):
        return dict(value)  # keep malformed structures subject to typed repair
    if (any(not isinstance(row.get("claimId"), str) for row in value.get("claims", ()))
            or any(not isinstance(row.get("claimId"), str) for row in value.get("evidenceUpdates", ()))
            or any(not isinstance(row.get("questionId"), str)
                   or not isinstance(row.get("claimIds", ()), (list, tuple))
                   or any(not isinstance(claim_id, str) for claim_id in row.get("claimIds", ()))
                   or not isinstance(row.get("missingEvidence", ()), (list, tuple))
                   for row in value.get("questions", ()))
            or (value.get("conclusion") is not None and not isinstance(value["conclusion"], Mapping))):
        return dict(value)
    original_claims = {row["claimId"]: row for row in packet.get("_localState", packet).get("claims", ())}
    original_questions = {row["questionId"]: row for row in packet.get("_localState", packet).get("questions", ())}
    diagnostics = {"discardedClaims": 0, "discardedReferences": 0, "discardedQuestions": 0,
                   "discardedFulltextRequests": 0}
    ignored_claims, claims = set(), []
    immutable = ("text", "kind", "novelty", "sourceRef", "location", "speaker", "subject", "object", "action", "stageOrCondition", "timeText")
    for row in value.get("claims", ()):
        old = original_claims.get(row.get("claimId"))
        if ref_key(row.get("sourceRef")) not in allowed or (old and any(row.get(key) != old.get(key) for key in immutable)):
            ignored_claims.add(row.get("claimId"))
            diagnostics["discardedClaims"] += 1
        else:
            claims.append(dict(row))
    known_claims = set(original_claims) | {row.get("claimId") for row in claims}
    updates, affected_claims = [], set()
    for row in value.get("evidenceUpdates", ()):
        claim_id = row.get("claimId")
        if claim_id not in known_claims or claim_id in ignored_claims or ref_key(row.get("sourceRef")) not in allowed:
            diagnostics["discardedReferences"] += 1
            affected_claims.add(claim_id)
        else:
            updates.append(dict(row))
    links = [*packet.get("evidenceUpdates", ()), *updates]
    for claim in claims:
        if claim.get("claimId") not in affected_claims:
            continue
        required = "contradicts" if claim.get("verificationStatus") == "contradicted" else "supports"
        if not any(link.get("claimId") == claim.get("claimId") and link.get("relation") == required
                   and ref_key(link.get("sourceRef")) in allowed for link in links):
            claim["verificationStatus"] = "unverified"
    questions = []
    for row in value.get("questions", ()):
        old = original_questions.get(row.get("questionId"))
        claim_ids, refs = row.get("claimIds", ()), row.get("knownEvidence", ())
        if not isinstance(claim_ids, (list, tuple)) or not isinstance(refs, (list, tuple)):
            questions.append(dict(row))
            continue
        if not set(claim_ids) <= known_claims or (old and any(row.get(key) != old.get(key) for key in ("claimIds", "question"))):
            diagnostics["discardedQuestions"] += 1
            continue
        clean = [dict(ref) for ref in refs if ref_key(ref) in allowed]
        removed = len(refs) - len(clean)
        item = {**row, "knownEvidence": clean}
        if removed or set(claim_ids) & ignored_claims:
            diagnostics["discardedReferences"] += removed
            # An answer relying on a rejected version cannot silently close a
            # question. Keep the gap visible for closure/company disclosure.
            item["missingEvidence"] = list(dict.fromkeys([*row.get("missingEvidence", ()),
                "部分引用或命题更新未通过当前证据校验，相关判断仍待核查。"] ))
            if item.get("state") == "answered":
                item["state"] = "open"
        questions.append(item)
    requestable = allowed | {ref_key(ref) for ref in packet.get("fulltextRequestRefs", ())}
    handled = {(row.get("questionId"), ref_key(row.get("sourceRef")))
               for row in packet.get("_localState", packet).get("fulltextRequests", ()) if row.get("state") in {"fulfilled", "rejected"}}
    requests = []
    for row in value.get("fulltextRequests", ()):
        identity = (row.get("questionId"), ref_key(row.get("sourceRef")))
        if (identity in handled or identity[0] not in original_questions
                or identity[1] not in requestable):
            diagnostics["discardedFulltextRequests"] += 1
        else:
            requests.append(dict(row))
    result = {**value, "claims": claims, "questions": questions, "evidenceUpdates": updates,
              "fulltextRequests": requests}
    if any(diagnostics.values()):
        result["conclusion"] = {**(value.get("conclusion") or {}), "runtimeOutputSanitization": diagnostics}
    return result


def _prune_cross_question_paths(paths: tuple[QueryPath, ...], packet: Mapping[str, Any]) -> tuple[tuple[QueryPath, ...], int]:
    """Drop an optional unusable route, never narrow or execute it.

    Require another fully scoped route for the same open question. Unknown
    claims/companies, missing targets and an entirely invalid plan stay errors.
    A company-link route naming only known claims is redundant only when a
    different valid route still covers that same question.
    """
    if not packet.get("contextProtocol"):
        return paths, 0
    local = packet.get("_localState", packet)
    questions = {q["questionId"]: q for q in local.get("questions", ())
                 if isinstance(q, Mapping) and q.get("state") == "open"}
    companies = {code for q in questions.values() for code in q.get("companyCodes", ())}

    def scope(path: QueryPath) -> str:
        question = questions.get(path.question_id)
        if question is None or path.question_scope is not None or not path.target_refs or path.state != "planned":
            return "invalid"
        if path.purpose_kind not in QUERY_PURPOSE_KINDS:
            return "invalid"
        outside = False
        has_company = False
        for target in path.target_refs:
            if target.get("kind") == "claim" and target.get("claimId") in question.get("claimIds", ()):
                continue
            if target.get("kind") == "company" and target.get("companyCode") in companies:
                has_company = True
                outside |= target["companyCode"] not in question.get("companyCodes", ())
                continue
            return "invalid"
        if path.purpose_kind == "company_event_link" and not has_company:
            return "redundant"
        return "mixed" if outside else "valid"

    kinds = [scope(path) for path in paths]
    covered = {path.question_id for path, kind in zip(paths, kinds) if kind == "valid"}
    kept = tuple(path for path, kind in zip(paths, kinds) if kind not in {"mixed", "redundant"} or path.question_id not in covered)
    return kept, len(paths) - len(kept)


def decode_stage_result(value: Mapping[str, Any], *, action: str,
                        evidence_packet: Mapping[str, Any] | None = None) -> ResearchStageResult:
    """Decode a provider JSON derivative without retaining its original response."""
    if isinstance(value, Mapping) and set(value) == {"outputContract"} and isinstance(value["outputContract"], Mapping):
        value = value["outputContract"]
    required = {"extract_claims": {"claims"}, "plan_gaps": {"questions"}, "plan_queries": {"queryPaths"},
                "assess_evidence": {"claims", "questions", "evidenceUpdates", "fulltextRequests"},
                "close_research": {"conclusion"}, "compare_companies": {"conclusion", "companyAssessments"}}
    permitted = {"claims", "questions", "queryPaths", "evidenceUpdates", "fulltextRequests", "conclusion", "companyAssessments", "safeErrorCode", "contextRequests"}
    if (isinstance(value, Mapping) and "action" not in value and set(value) <= permitted
            and action in required and (bool(set(value) & required[action]) if action == "assess_evidence" else required[action] <= set(value))):
        # The caller already fixed the action. Missing routing metadata can be
        # restored only for an unmistakable stage payload; no evidence is filled.
        value = {"action": action, **value}
    if not isinstance(value, Mapping) or value.get("action") != action:
        raise InvestigationError("研究输出 action 无效", code="investigation_action_mismatch") from ResearchContractError(
            "根 action 必须匹配请求", field_name="action", expected="enum", allowed=(action,))
    try:
        requests = value.get("contextRequests")
        if requests is not None and (not isinstance(requests, list) or any(not isinstance(r, Mapping) for r in requests)):
            raise ResearchContractError("局部读取请求必须为对象列表", field_name="contextRequests", expected="array_of_objects")
        if requests:
            # Model replies may contain a draft alongside tool requests. Read first;
            # never validate/persist the premature business result as evidence.
            local = (evidence_packet or {}).get("_localState", evidence_packet or {})
            known = {q.get("questionId") for q in local.get("questions", ()) if isinstance(q, Mapping)}
            drafts = value.get("questions", ())
            draft_ids = {q["questionId"] for q in drafts if isinstance(q, Mapping) and isinstance(q.get("questionId"), str)} if isinstance(drafts, list) else set()
            normalized = []
            for request in requests:
                request = dict(request)
                # This is an advisory association, not the locator (id/sourceRef).
                # Discard only IDs belonging to the withheld draft, not unknown refs.
                if isinstance(request.get("questionId"), str) and request["questionId"] in draft_ids - known:
                    request.pop("questionId", None)
                normalized.append(request)
            return ResearchStageResult(action=action, safe_error_code=value.get("safeErrorCode"), context_requests=tuple(normalized))
        if evidence_packet is not None and action in {"assess_evidence", "close_research"}:
            # Updates may refer to known IDs instead of asking the model to copy
            # immutable source text. Only copy exact fields from this request's
            # durable input; unknown IDs still require complete typed facts.
            value = dict(value)
            for collection, key in (("claims", "claimId"), ("questions", "questionId")):
                originals = {item[key]: item for item in evidence_packet.get("_localState", evidence_packet).get(collection, ())}
                updates = value.get(collection, ())
                if isinstance(updates, list) and all(isinstance(item, Mapping) for item in updates):
                    value[collection] = [{**originals.get(item.get(key), {}), **item} for item in updates]
        scope = (evidence_packet or {}).get("companyScope")
        scope_mapping = scope if isinstance(scope, Mapping) else {}
        local_scope = (evidence_packet or {}).get("_localState", {})
        frozen_pool = local_scope.get("fixedPool", scope_mapping.get("fixedPool", [])) if isinstance(local_scope, Mapping) else []
        allowed = {row["companyCode"] for row in frozen_pool
                   if isinstance(row, Mapping) and isinstance(row.get("companyCode"), str)} if isinstance(frozen_pool, (list, tuple)) else set()
        if scope and allowed:
            # A model may mention background companies outside this selector's
            # frozen universe. Exclude those hints locally, before planning any
            # paid search; keep in-pool questions, mappings and assessments
            # intact.  A legacy/non-v2 packet can carry an empty presentation
            # scope, but is not a selector universe and must not erase every
            # otherwise valid question merely because no pool was bound.
            value = dict(value)
            questions, excluded_questions = [], set()
            for question in value.get("questions", ()):
                if not isinstance(question, Mapping):
                    questions.append(question)  # retain strict structural validation
                    continue
                codes = question.get("companyCodes", [])
                codes = [code for code in codes if isinstance(code, str) and code in allowed] if isinstance(codes, list) else []
                if codes:
                    questions.append({**question, "companyCodes": codes})
                else:
                    excluded_questions.add(question.get("questionId"))
            if "questions" in value:
                value["questions"] = questions
            for field in ("queryPaths", "fulltextRequests"):
                if isinstance(value.get(field), list):
                    value[field] = [row for row in value[field]
                        if not isinstance(row, Mapping) or row.get("questionId") not in excluded_questions]
            conclusion = value.get("conclusion")
            if isinstance(conclusion, Mapping) and isinstance(conclusion.get("companyMappings"), list):
                value["conclusion"] = {**conclusion, "companyMappings": [row for row in conclusion["companyMappings"]
                    if not isinstance(row, Mapping) or row.get("companyCode") in allowed]}
            if isinstance(value.get("companyAssessments"), list):
                value["companyAssessments"] = [row for row in value["companyAssessments"]
                    if not isinstance(row, Mapping) or row.get("companyCode") in allowed]
        if evidence_packet is not None and "allowedEvidenceRefs" in evidence_packet and action == "assess_evidence":
            value = _prune_assessment_references(value, evidence_packet)
        if action == "compare_companies" and evidence_packet is not None and "companyCodes" in evidence_packet:
            # Local retrieval may contain background companies beyond the
            # frozen comparison. Keep exactly the requested judgments, with
            # the first judgment owning a repeated company. Missing requested
            # companies still fail the coverage validator; never invent them.
            rows = value.get("companyAssessments")
            if isinstance(rows, list):
                codes = set(evidence_packet["companyCodes"])
                kept, seen, discarded = [], set(), 0
                for row in rows:
                    if not isinstance(row, Mapping) or not isinstance(row.get("companyCode"), str):
                        kept.append(row)  # retain strict structural validation
                        continue
                    code = row["companyCode"]
                    if code not in codes or code in seen:
                        discarded += 1
                        continue
                    kept.append(row)
                    seen.add(code)
                value = {**value, "companyAssessments": kept}
                if discarded and isinstance(value.get("conclusion"), Mapping):
                    value["conclusion"] = {**value["conclusion"], "runtimeOutputSanitization": {
                        "discardedCompanyAssessments": discarded}}
        # Reject malformed model collections at the normalizer boundary.  The
        # typed constructors intentionally assume one object each; letting a
        # string or scalar reach them leaks AttributeError/TypeError instead
        # of producing the durable safe contract error that the receipt and
        # recovery paths understand.
        for field in ("claims", "questions", "queryPaths"):
            rows = value.get(field, ())
            if (not isinstance(rows, Sequence) or isinstance(rows, (str, bytes))
                    or any(not isinstance(item, Mapping) for item in rows)):
                raise ResearchContractError(f"{field} 必须是对象列表")
        from .research_context import collapse_repeated_work
        local = (evidence_packet or {}).get("_localState", evidence_packet or {})
        # Only the first accepted planning receipt may retain distinct declared
        # source routes. Later labels are unverified wording and cannot widen
        # the frozen route set.
        initial_plan = (action == "plan_queries" and isinstance(local, Mapping)
                        and not local.get("queryPaths"))
        value = collapse_repeated_work(value, evidence_packet or {},
                                       admit_initial_source_labels=initial_plan)
        claims = tuple(Claim.from_dict(item) for item in value.get("claims", ()))
        questions = tuple(Question.from_dict(item) for item in value.get("questions", ()))
        paths = tuple(QueryPath.from_dict(item) for item in value.get("queryPaths", ()))
        if action == "plan_queries" and evidence_packet is not None:
            paths, discarded = _prune_cross_question_paths(paths, evidence_packet)
            if discarded:
                value = {**value, "conclusion": {**(value.get("conclusion") or {}),
                    "runtimeOutputSanitization": {"discardedUnusableQueryPaths": discarded}}}
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
        if isinstance(conclusion, Mapping) and isinstance(conclusion.get("companyMappings"), list):
            conclusion = {**conclusion, "companyMappings":[validate_company_mapping(item) for item in conclusion["companyMappings"]]}
        assessments = [validate_company_assessment(item) for item in assessments]
        return ResearchStageResult(action=action, safe_error_code=value.get("safeErrorCode"), claims=claims, questions=questions, query_paths=paths,
                                   evidence_updates=tuple(dict(item) for item in updates),
                                   fulltext_requests=tuple(FullTextRequest.from_dict(item) for item in requests),
                                   conclusion=None if conclusion is None else dict(conclusion),
                                   company_assessments=tuple(dict(item) for item in assessments),
                                   context_requests=tuple(value.get("contextRequests", ())))
    except ResearchContractError as exc:
        raise InvestigationError("研究输出不符合 typed contract", code="investigation_result_invalid") from exc


__all__ = [
    "InvestigationError", "InvestigationGateway",
    "decode_merged_stage_result", "decode_stage_result", "query_path_signature",
    "validate_merged_stage_result", "validate_stage_result",
]
