"""Durable, question-driven investigation of already admitted K10 articles.

The source-understanding call has already read each article and extracted its
claims. This coordinator never reads that body again. It moves only structured
claims, attributable excerpts and explicitly admitted additional full texts.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Mapping, Sequence

from .research_context import (
    canonical_body_identity, canonical_context_request, normalized_query, project_packet, question_dependency, read_context, route_identity,
)
from .research_navigation import navigation_view, sina_source
from . import store
from .discovery import (
    CandidateComparison, CompanyMappingDraft, DiscoveryDocument, DiscoverySliceYield,
    EvidenceRef, EventComparison, EventDraft, InvestigationOutcome,
    SqliteDiscoveryWriter, Verification, reject_uncalibrated_prediction,
)
from .historical_cases import apply_historical_assessments
from .investigation import InvestigationError
from .opportunity_discovery import validate_event_comparison
from .research_contracts import (Claim, FullTextRequest, Question, QueryPath,
    RESEARCH_ROUND_ACTION, ResearchContractError, ResearchRoundResult, ResearchSnapshot,
    validate_company_assessment, validate_company_mapping)
from .investigation_prompts import request_spec as investigation_request_spec
from .research_material import admit_material
from .research_store import (append_research_round, create_research_snapshot, load_prior_research_evidence,
                             load_research_round_state, mark_research_round_failed, read_research_state)

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: datetime) -> str:
    if value.tzinfo is None:
        raise InvestigationError("调查时间必须含时区", code="investigation_time_invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()




def _ref(value: EvidenceRef | DiscoveryDocument) -> dict[str, Any]:
    return {"documentId": value.document_id, "revision": value.revision}


def _key(value: Mapping[str, Any]) -> EvidenceRef:
    doc, revision = value.get("documentId"), value.get("revision")
    if not isinstance(doc, str) or not doc or isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise InvestigationError("调查引用无效", code="investigation_reference_invalid")
    return EvidenceRef(doc, revision)


def _refs(value: Any) -> tuple[EvidenceRef, ...]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, Mapping) for item in value):
        raise InvestigationError("调查引用列表无效", code="investigation_reference_invalid")
    return tuple(dict.fromkeys(_key(item) for item in value))


def build_research_round_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Build the B78 model packet from program-owned, visible evidence only.

    The caller owns the durable input manifest and receipt.  Keeping this
    builder free of storage is intentional: recovering a paid reply uses the
    exact frozen packet rather than reading mutable research state and issuing
    a second POST.
    """
    if not isinstance(packet, Mapping):
        raise InvestigationError("研究资料包必须为对象", code="investigation_packet_invalid")
    return project_packet(RESEARCH_ROUND_ACTION, packet)


def research_round_request_spec(*, snapshot: ResearchSnapshot,
                                evidence_packet: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return the direct B78 prompt contract without selecting a provider."""
    return investigation_request_spec(snapshot=snapshot, action=RESEARCH_ROUND_ACTION,
                                      evidence_packet=evidence_packet)


def _round_ref_keys(values: Any, *, field: str) -> set[tuple[str, int]]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise InvestigationError(f"{field} 必须为来源引用列表", code="investigation_reference_invalid")
    keys: set[tuple[str, int]] = set()
    for value in values:
        if not isinstance(value, Mapping):
            raise InvestigationError(f"{field} 包含无效来源引用", code="investigation_reference_invalid")
        document_id, revision = value.get("documentId"), value.get("revision")
        if (not isinstance(document_id, str) or not document_id or isinstance(revision, bool)
                or not isinstance(revision, int) or revision < 1):
            raise InvestigationError(f"{field} 包含无效来源引用", code="investigation_reference_invalid")
        keys.add((document_id, revision))
    return keys


def _round_mapping_codes(conclusion: Mapping[str, Any]) -> set[str]:
    mappings = conclusion.get("companyMappings", ())
    if not isinstance(mappings, list):
        raise InvestigationError("研究轮次公司映射无效", code="investigation_mapping_invalid")
    return {validate_company_mapping(row)["companyCode"] for row in mappings}


def _b78_visible_shared_claims(evidence_packet: Mapping[str, Any]) -> tuple[Claim, ...]:
    """Return typed source facts that the direct packet made visible.

    Shared source facts are evidence, not a transfer of a peer's conclusion.
    They therefore retain their short claim text, source and locator but are
    always reintroduced as ``unverified``.  ``project_packet`` namespaces an
    ID collision before this boundary, so an imported source fact cannot
    overwrite an event's own claim.
    """
    reusable = evidence_packet.get("reusableSourceEvidence")
    rows = reusable.get("claims", ()) if isinstance(reusable, Mapping) else ()
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return ()
    result: list[Claim] = []
    known: dict[str, Claim] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        try:
            claim = Claim.from_dict({**raw, "verificationStatus": "unverified"})
        except ResearchContractError:
            continue
        prior = known.get(claim.claim_id)
        # A packet with a duplicated shared ID does not have a stable claim
        # identity.  Do not make either row available to a model question.
        if prior is not None:
            known.pop(claim.claim_id, None)
            continue
        known[claim.claim_id] = claim
        result.append(claim)
    return tuple(claim for claim in result if known.get(claim.claim_id) == claim)


def _b78_packet_claims(evidence_packet: Mapping[str, Any]) -> dict[str, Claim]:
    """Read the immutable claim identities exposed by one direct packet."""
    visible: dict[str, Claim] = {}
    rows = evidence_packet.get("claims", ())
    if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            try:
                claim = Claim.from_dict(raw)
            except ResearchContractError:
                continue
            visible.setdefault(claim.claim_id, claim)
    for claim in _b78_visible_shared_claims(evidence_packet):
        visible.setdefault(claim.claim_id, claim)
    return visible


def _b78_reconcile_result_claims(*, result: ResearchRoundResult,
                                 evidence_packet: Mapping[str, Any]) -> ResearchRoundResult:
    """Keep the packet's canonical claim when a reply reuses its ID.

    A model may repeat a visible claim while changing its text, source or
    verification state.  That is not a new fact and must not replace the
    program-owned source identity.  Independent new IDs remain part of the
    direct result for the next round.
    """
    known = _b78_packet_claims(evidence_packet)
    accepted: list[Claim] = []
    for claim in result.claims:
        if claim.claim_id in known:
            continue
        known[claim.claim_id] = claim
        accepted.append(claim)
    return result if tuple(accepted) == result.claims else replace(result, claims=tuple(accepted))


def _b78_filter_pool_result(*, result: ResearchRoundResult,
                            evidence_packet: Mapping[str, Any]) -> ResearchRoundResult:
    """Discard model-only out-of-pool companies without losing valid cards."""
    local = evidence_packet.get("_localState")
    pool = local.get("fixedPool", ()) if isinstance(local, Mapping) else ()
    if not isinstance(pool, Sequence) or isinstance(pool, (str, bytes)):
        return result
    allowed = {
        row.get("companyCode") for row in pool
        if isinstance(row, Mapping) and isinstance(row.get("companyCode"), str) and row["companyCode"]
    }
    # Unit packets and a deliberately unconstrained direct context use an
    # empty selector.  Only a real frozen pool is a filtering instruction.
    if not allowed or not isinstance(result.conclusion, Mapping):
        return result
    conclusion = dict(result.conclusion)
    mappings = conclusion.get("companyMappings")
    if not isinstance(mappings, list):
        return result
    retained_mappings = [dict(row) for row in mappings
                         if isinstance(row, Mapping) and row.get("companyCode") in allowed]
    retained_codes = {row["companyCode"] for row in retained_mappings}
    retained_assessments = tuple(dict(row) for row in result.company_assessments
                                  if isinstance(row, Mapping) and row.get("companyCode") in retained_codes)
    if retained_mappings == mappings and retained_assessments == result.company_assessments:
        return result
    conclusion["companyMappings"] = retained_mappings
    return replace(result, conclusion=conclusion, company_assessments=retained_assessments)


def validate_research_round_result(*, result: ResearchRoundResult,
                                   evidence_packet: Mapping[str, Any]) -> None:
    """Validate a direct result against the packet actually shown to the model.

    This is deliberately separate from the historical action validators.  A
    new B78 round cannot obtain compatibility by being decoded as two old
    stage results, and a caller cannot make a source reference true merely by
    carrying its ID in a mutable local cache.
    """
    if not isinstance(result, ResearchRoundResult):
        raise InvestigationError("研究轮次输出无效", code="investigation_result_invalid")
    if not isinstance(evidence_packet, Mapping):
        raise InvestigationError("研究资料包必须为对象", code="investigation_packet_invalid")
    forbidden = {"originalText", "original_text", "analysisText", "analysis_text"}
    if forbidden & set(evidence_packet):
        raise InvestigationError("研究轮次不得重新传入冻结正文", code="investigation_body_reuse_forbidden")
    allowed = _round_ref_keys(evidence_packet.get("allowedEvidenceRefs", ()),
                              field="allowedEvidenceRefs")
    if result.safe_error_code:
        raise InvestigationError("研究轮次执行失败", code=result.safe_error_code)
    if result.context_requests:
        return
    if not isinstance(result.conclusion, Mapping):
        raise InvestigationError("研究轮次缺少结论", code="investigation_conclusion_missing")
    known_claims = set(_b78_packet_claims(evidence_packet))
    known_claims.update(item.claim_id for item in result.claims)
    for claim in result.claims:
        ref = claim.source_ref
        if (ref.get("documentId"), ref.get("revision")) not in allowed:
            raise InvestigationError("命题引用未输入资料", code="investigation_reference_invalid")
    known_questions = {item.get("questionId") for item in evidence_packet.get("questions", ())
                       if isinstance(item, Mapping) and isinstance(item.get("questionId"), str)}
    known_questions.update(item.question_id for item in result.questions)
    for question in result.questions:
        if not set(question.claim_ids) <= known_claims:
            raise InvestigationError("问题引用了未知命题", code="investigation_question_claim_invalid")
        if not {(ref["documentId"], ref["revision"]) for ref in question.known_evidence} <= allowed:
            raise InvestigationError("问题引用未输入资料", code="investigation_reference_invalid")
    seen_paths: set[str] = set()
    for path in result.query_paths:
        if path.question_id not in known_questions:
            raise InvestigationError("查询路径未关联当前问题", code="investigation_path_question_invalid")
        identity = _hash({"questionId": path.question_id, "query": path.query.strip().casefold(),
                          "intent": path.intent.strip().casefold(),
                          "targetSource": path.target_source.strip().casefold(),
                          "purposeKind": path.purpose_kind,
                          "targetRefs": [dict(ref) for ref in path.target_refs]})
        if identity in seen_paths:
            # A repeated route is model-output noise.  It is not a reason to
            # reject the paid round (and induce an avoidable repair POST).
            # `_b78_followups` records this sibling as blocked before any
            # duplicate gateway call; distinct query/source semantics retain
            # their own identities there.
            continue
        seen_paths.add(identity)
        if path.source_locator is not None:
            ref = path.source_locator
            if (ref.get("documentId"), ref.get("revision")) not in allowed:
                raise InvestigationError("查询定位未在可见资料中", code="investigation_reference_invalid")
    for update in result.evidence_updates:
        if update.get("claimId") not in known_claims:
            raise InvestigationError("证据更新引用未知命题", code="investigation_claim_invalid")
        ref = update.get("sourceRef", {})
        if (ref.get("documentId"), ref.get("revision")) not in allowed:
            raise InvestigationError("证据更新引用未输入资料", code="investigation_reference_invalid")
    for claim in result.claims:
        if claim.verification_status != "verified":
            continue
        # A direct round may add a new claim, but it cannot prove that claim
        # by merely changing its label.  Its supports link must name a source
        # visible in this packet and explain the applicable locator.  This is
        # deliberately one applicable source, not an invented source-count
        # threshold or an extra search requirement.
        supports = any(
            update.get("claimId") == claim.claim_id
            and update.get("relation") == "supports"
            and isinstance(update.get("location"), str) and bool(update["location"].strip())
            and isinstance(update.get("applicability"), Mapping) and bool(update["applicability"])
            for update in result.evidence_updates
        )
        if not supports:
            raise InvestigationError("命题已核实却没有适用的支持证据关联", code="investigation_support_missing")
    for request in result.fulltext_requests:
        if request.question_id not in known_questions:
            raise InvestigationError("全文请求未关联当前问题", code="investigation_question_invalid")
        if request.state != "requested":
            raise InvestigationError("全文请求状态无效", code="investigation_fulltext_scope_invalid")
        # Terminal request state is program bookkeeping and deliberately not
        # repeated in the model-visible direct packet.  Keep it in local
        # state so a fresh request ID cannot reopen the same question/source
        # after a fulfilled or rejected read.
        local = evidence_packet.get("_localState")
        prior = (local.get("fulltextRequests", ())
                 if isinstance(local, Mapping) else evidence_packet.get("fulltextRequests", ()))
        if isinstance(prior, Sequence) and not isinstance(prior, (str, bytes)):
            for raw in prior:
                if not isinstance(raw, Mapping):
                    continue
                try:
                    previous = FullTextRequest.from_dict(raw)
                except ResearchContractError:
                    continue
                if (previous.question_id == request.question_id
                        and _key(previous.source_ref) == _key(request.source_ref)
                        and previous.state in {"fulfilled", "rejected"}):
                    raise InvestigationError("同一问题重复申请已处理的全文", code="investigation_fulltext_duplicate")
    codes = _round_mapping_codes(result.conclusion)
    if result.company_assessments:
        if not isinstance(result.comparison, Mapping):
            raise InvestigationError("公司比较缺少共同事实", code="investigation_comparison_summary_missing")
        summary = result.comparison.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise InvestigationError("公司比较缺少共同事实", code="investigation_comparison_summary_missing")
        comparison_refs = _round_ref_keys(result.comparison.get("evidenceRefs", ()),
                                           field="comparison.evidenceRefs")
        if not comparison_refs <= allowed:
            raise InvestigationError("公司比较引用未输入资料", code="investigation_reference_invalid")
        assessed = {validate_company_assessment(row)["companyCode"] for row in result.company_assessments}
        if assessed != codes:
            raise InvestigationError("公司比较覆盖与映射不一致", code="investigation_company_coverage_invalid")


def run_research_round(model: Any, *, snapshot: ResearchSnapshot,
                       evidence_packet: Mapping[str, Any],
                       restored_receipt: Mapping[str, Any] | ResearchRoundResult | None = None) -> ResearchRoundResult:
    """Run or exactly replay one B78 research round.

    Receipt persistence and provider accounting intentionally belong to the
    task-bound pipeline adapter.  Passing a durable ``restored_receipt``
    performs only local decoding/validation, so a derivation restart cannot
    make another provider request.
    """
    if not isinstance(snapshot, ResearchSnapshot):
        raise InvestigationError("研究快照无效", code="investigation_snapshot_invalid")
    if restored_receipt is None:
        invoke = getattr(model, "advance_research_round", None)
        if not callable(invoke):
            raise InvestigationError("模型未提供 B78 研究轮次能力", code="investigation_model_unavailable")
        raw: Mapping[str, Any] | ResearchRoundResult = invoke(
            snapshot=snapshot, evidence_packet=evidence_packet)
    else:
        raw = restored_receipt
    try:
        result = raw if isinstance(raw, ResearchRoundResult) else ResearchRoundResult.from_dict(
            raw, model_reply=restored_receipt is None)
        # Reconcile before validation: an altered duplicate may itself carry
        # an invalid source/status, but it is not an independent model fact.
        # The immutable packet claim remains the only identity visible to the
        # rest of this direct round.
        result = _b78_reconcile_result_claims(result=result, evidence_packet=evidence_packet)
        validate_research_round_result(result=result, evidence_packet=evidence_packet)
        return result
    except ResearchContractError as exc:
        raise InvestigationError("研究轮次不符合 typed contract", code="investigation_result_invalid") from exc


class _Investigation:
    def __init__(self, *, model: Any, verifier: Any, task_id: str, event: EventDraft,
                 documents: Mapping[EvidenceRef, DiscoveryDocument], execution_profile: Mapping[str, Any],
                 cutoff_at: datetime, db_path: Path, created_at: datetime,
                 leaseguard: Callable[[], None] | None, cutoff_inclusive: bool,
                 snapshot_created: Callable[[str], None] | None, clock: Callable[[], datetime],
                 allow_failed_resume: bool = False, runtime_contract: Mapping[str, Any] | None = None) -> None:
        self.model, self.verifier, self.event, self.db_path = model, verifier, event, db_path
        self.task_id, self.guard, self.clock = task_id, leaseguard, clock
        self.allow_failed_resume = allow_failed_resume
        from .delivery import RESEARCH_CONTRACT
        # This marker owns the B78 wire migration.  Its direct result is not
        # a compatibility projection of the historic plan/assess/close loop.
        self.b78_research = (isinstance(runtime_contract, Mapping)
                             and runtime_contract.get("research") == RESEARCH_CONTRACT)
        if not self.b78_research:
            # Historical receipts are decoded by the store/read APIs.  A
            # frozen pre-B78 task is never upgraded into a new paid workflow,
            # nor may this runtime create another old-stage mutation for it.
            raise InvestigationError("历史研究任务仅可读取，不能继续执行", code="research_legacy_read_only")
        # Set immediately before a paid direct call.  If typed validation of
        # its completed receipt fails, the outer boundary can write a safe
        # terminal disposition using this exact visible input.  Raw replies
        # remain in the model checkpoint and never enter this runtime state.
        self._b78_active_packet: Mapping[str, Any] | None = None
        self.cutoff, self.cutoff_inclusive = cutoff_at, cutoff_inclusive
        self.documents = dict(documents)
        self.allowed = set(event.source_refs)
        self.policy = execution_profile["payload"]["discovery"]
        self.identity = "research_" + _hash([task_id, event.canonical_key, event.stage_key,
                                             event.event_state, [_ref(ref) for ref in event.source_refs]])[:32]
        self.context = {"canonicalKey": event.canonical_key, "stageKey": event.stage_key,
            "eventState": event.event_state, "headline": event.headline, "eventKind": event.event_kind,
            "sourceRefs": [_ref(ref) for ref in event.source_refs], "facts": dict(event.facts),
            "newsCutoffAt": _text(cutoff_at), "cutoffInclusive": cutoff_inclusive}
        self.context_digest = _hash(self.context)
        self.state = read_research_state(snapshot_id=self.identity, db_path=db_path)
        if self.state is None:
            self._guard()
            writer = SqliteDiscoveryWriter(scan_id="research-input", db_path=db_path, created_at=_text(created_at))
            event_revision = writer.append_event(event=event, verification=Verification(
                "needs_review", "调查输入，尚未完成比较。", event.source_refs, {"state": "available"}))
            create_research_snapshot(snapshot=ResearchSnapshot(self.identity, task_id, event_revision.event_id,
                event_revision.revision, _text(cutoff_at), _text(created_at), self.context_digest,
                self.policy["investigationPromptContractRevision"],
                _hash({"model": self.policy["model"], "options": self.policy["modelOptions"]["investigation"],
                       **({"runtimeProvider": execution_profile["runtimeProvider"]} if "runtimeProvider" in execution_profile else {})}),
                "continue_research", "ok", 1, _text(created_at), _text(created_at)), db_path=db_path, lease_guard=leaseguard)
            self._refresh()
        if self.snapshot.context_sha256 != self.context_digest:
            raise InvestigationError("调查输入与恢复快照不一致", code="investigation_context_mismatch")
        if snapshot_created is not None:
            snapshot_created(self.identity)
        self._restore_sources()

    @property
    def snapshot(self) -> ResearchSnapshot:
        return self.state["snapshot"]

    def _guard(self) -> None:
        if self.guard:
            self.guard()

    def _external_guard(self) -> None:
        self._guard()
        terminal = getattr(self.model, '_terminal_provider_error', None)
        if terminal in {'insufficient_balance', 'provider_authorization_failed'}:
            message = ('余额不足，停止后续模型及搜索步骤' if terminal == 'insufficient_balance'
                       else '供应商授权失败，停止后续模型及搜索步骤')
            raise InvestigationError(message, code=terminal)

    def _refresh(self) -> None:
        self.state = read_research_state(snapshot_id=self.identity, db_path=self.db_path)
        if self.state is None:
            raise InvestigationError("调查快照丢失", code="investigation_snapshot_missing")

    def _restore_sources(self) -> None:
        # New direct rounds start only from the supplied frozen sources.  Later
        # search/fulltext documents restore through the B78 round receipt, not
        # through historic stage records.
        if not set(self.event.source_refs) <= set(self.documents):
            raise InvestigationError("调查引用未提供文档", code="investigation_source_missing")
        self.allowed = {
            ref for ref in self.event.source_refs
            if ref not in self.documents or admit_material(self.documents[ref]).state != "excluded"
        }

    def _company_scope(self) -> dict[str, Any]:
        """Load only title-bound company context for the first direct round."""
        binding = getattr(getattr(self, "model", None), "_company_profiles_binding", None)
        if binding is None:
            binding = getattr(getattr(getattr(self, "model", None), "_base", None),
                              "_company_profiles_binding", None)
        if binding is None:
            return {}
        from .schema import read_connection
        from .v2_profiles import retrieve_company_context

        hints: set[str] = set()
        with read_connection(self.db_path) as conn:
            for ref in self.event.source_refs:
                row = conn.execute(
                    "SELECT company_codes_json FROM k10_v2_title_company_hints "
                    "WHERE task_id=? AND document_id=? AND revision=?",
                    (self.task_id, ref.document_id, ref.revision),
                ).fetchone()
                if row:
                    hints.update(json.loads(row[0]))
        # The direct receipt itself holds later questions.  `read_research_state`
        # exposes this field only for historic stage rows, so it may be empty.
        open_questions = self.state.get("questions", ())
        if isinstance(open_questions, Sequence) and not isinstance(open_questions, (str, bytes)):
            hints.update(
                code for question in open_questions
                if isinstance(question, Mapping) and question.get("state") == "open"
                for code in question.get("companyCodes", ()) if isinstance(code, str)
            )
        query = {
            "headline": self.event.headline,
            "facts": self.event.facts,
            "questions": [item for item in open_questions
                          if isinstance(item, Mapping) and item.get("state") == "open"],
        }
        return retrieve_company_context(
            db_path=binding[0], profiles_id=binding[1], query=query, hinted_codes=sorted(hints),
        )

    def _mappings(self, conclusion: Mapping[str, Any]) -> tuple[CompanyMappingDraft, ...]:
        rows = conclusion.get("companyMappings", [])
        if not isinstance(rows, list):
            raise InvestigationError("公司映射格式无效", code="investigation_mapping_invalid")
        result = []
        for raw in rows:
            item = validate_company_mapping(raw)
            refs = _refs(item.get("relationEvidence"))
            if not refs or not set(refs) <= self.allowed or not isinstance(item.get("inference"), Mapping):
                raise InvestigationError("公司关系缺少适用证据", code="investigation_mapping_invalid")
            result.append(CompanyMappingDraft(item["companyCode"], item["affectedStage"], refs, item["inference"], item["uncertainty"]))
        if len({item.company_code for item in result}) != len(result):
            raise InvestigationError("公司映射重复", code="investigation_mapping_invalid")
        return tuple(result)

    def _outcome(self, conclusion: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
                 mappings: Sequence[CompanyMappingDraft], context: Mapping[str, Any], comparison: Mapping[str, Any]) -> InvestigationOutcome:
        common = comparison.get("summary") if rows else conclusion.get("stopReason")
        if not isinstance(common, str) or not common.strip():
            raise InvestigationError("缺少事件共同事实或停止原因", code="investigation_comparison_summary_missing")
        event_refs = _refs(comparison.get("evidenceRefs", [])) if rows else self.event.source_refs
        if rows and (not event_refs or not set(event_refs) <= self.allowed):
            raise InvestigationError("共同事实引用无效", code="investigation_reference_invalid")
        candidates = {}
        mapping_by_code = {item.company_code: item for item in mappings}
        for row in rows:
            mapping = mapping_by_code[row["companyCode"]]
            differences = {key: row[key] for key in ("role", "priorityReason", "gap", "rankChangeConditions", "twoDayReason", "evidenceDisclosure")}
            candidates[row["companyCode"]] = CandidateComparison(row["summary"], differences,
                tuple(dict.fromkeys((*mapping.relation_evidence, *event_refs))), row["rank"],
                market_context=context.get("marketContext"), historical_cases=tuple(context.get("historicalCases", [])),
                historical_coverage=context.get("historicalCoverage", {}),
                research_snapshot_id=self.identity, research_revision=self.snapshot.revision)
        validate_event_comparison(summary=common, comparisons={code: {"summary": item.summary,
            "differences": item.differences, "rank": item.rank} for code, item in candidates.items()},
            company_codes=tuple(mapping_by_code)) if rows else None
        reject_uncalibrated_prediction({"summary": common, "companies": list(rows)}, path="researchComparison")
        statuses = {row["evidenceDisclosure"]["verificationStatus"] for row in rows}
        verified = next(iter(statuses)) if len(statuses) == 1 and statuses <= {"verified", "contradicted"} else "needs_review"
        return InvestigationOutcome(Verification(verified, common, event_refs,
            {"state": "available", "researchSnapshotId": self.identity, "researchRevision": self.snapshot.revision,
             "researchStatus": self.snapshot.research_status, "verificationCutoffAt": self.snapshot.verification_cutoff_at},
            tuple(self.documents[ref] for ref in self.allowed if ref not in self.event.source_refs)),
            tuple(mappings), EventComparison(common, candidates, event_refs), self.identity)

    @staticmethod
    def _b78_rows(values: Sequence[Any]) -> list[dict[str, Any]]:
        """Convert typed direct-round values without introducing stage records."""
        return [value.to_dict() if hasattr(value, "to_dict") else dict(value) for value in values]

    @staticmethod
    def _b78_question_scope(question: Question) -> dict[str, Any]:
        """Derive the gateway scope from the accepted direct-round question."""
        row = question.to_dict()
        projection = {key: row.get(key) for key in (
            "questionId", "question", "claimIds", "companyCodes", "supportCondition", "refuteCondition",
            "missingEvidence",
        )}
        scope: dict[str, Any] = {
            "version": "k10-v2-question-scope-1", "questionId": question.question_id,
            "claimIds": list(question.claim_ids), "companyCodes": list(question.company_codes),
            "questionSha256": _hash(projection),
        }
        scope["scopeSha256"] = _hash(scope)
        return scope

    def _b78_bound_query_path(self, path: QueryPath, question: Question | None) -> QueryPath:
        """Bind a model-declared route to one accepted question before search."""
        if question is None or path.question_id != question.question_id:
            raise InvestigationError("查询路径缺少当前问题", code="investigation_path_question_invalid")
        # Scope is a program hash of the frozen question, never a model claim.
        if path.question_scope is not None:
            raise InvestigationError("查询路径不得自带问题范围", code="investigation_path_scope_invalid")
        targets = tuple(path.target_refs)
        if path.purpose_kind not in {"event_fact", "company_event_link", "counterevidence"} or not targets:
            raise InvestigationError("查询路径缺少问题范围", code="investigation_path_scope_invalid")
        claim_ids, company_codes = set(question.claim_ids), set(question.company_codes)
        has_company = False
        for target in targets:
            if target.get("kind") == "claim" and target.get("claimId") in claim_ids:
                continue
            if target.get("kind") == "company" and target.get("companyCode") in company_codes:
                has_company = True
                continue
            raise InvestigationError("查询路径越过问题范围", code="investigation_path_scope_invalid")
        if path.purpose_kind == "company_event_link" and not has_company:
            raise InvestigationError("公司关联查询缺少公司目标", code="investigation_path_scope_invalid")
        return replace(path, question_scope=self._b78_question_scope(question))

    def _b78_prune_optional_query_paths(self, result: ResearchRoundResult, *,
                                        claims: Mapping[str, Claim],
                                        questions: Mapping[str, Question]) -> ResearchRoundResult:
        """Keep a necessary route when a redundant sibling route is malformed.

        The direct result is still rejected for an invented claim/company or a
        model-supplied scope.  Those defects cannot be classified as harmless
        optional noise.  A path that only fails because it crosses another
        accepted question (or lacks the company target required by its own
        purpose) may be discarded when that exact question retains a valid
        necessary route.
        """
        if not result.query_paths:
            return result
        known_claim_ids = set(claims)
        known_company_codes = {code for question in questions.values() for code in question.company_codes}
        valid: dict[str, list[QueryPath]] = {}
        optional_invalid: dict[str, list[QueryPath]] = {}
        for path in result.query_paths:
            if path.state != "planned":
                valid.setdefault(path.question_id, []).append(path)
                continue
            question = questions.get(path.question_id)
            if question is None:
                raise InvestigationError("查询路径缺少当前问题", code="investigation_path_question_invalid")
            if path.question_scope is not None:
                raise InvestigationError("查询路径不得自带问题范围", code="investigation_path_scope_invalid")
            if path.purpose_kind not in {"event_fact", "company_event_link", "counterevidence"} or not path.target_refs:
                optional_invalid.setdefault(path.question_id, []).append(path)
                continue
            local_claims, local_companies = set(question.claim_ids), set(question.company_codes)
            cross_question = False
            has_company = False
            for target in path.target_refs:
                if target.get("kind") == "claim":
                    claim_id = target.get("claimId")
                    if claim_id not in known_claim_ids:
                        raise InvestigationError("查询路径引用未知命题", code="investigation_path_scope_invalid")
                    cross_question = cross_question or claim_id not in local_claims
                    continue
                if target.get("kind") == "company":
                    company_code = target.get("companyCode")
                    if company_code not in known_company_codes:
                        raise InvestigationError("查询路径引用未知公司", code="investigation_path_scope_invalid")
                    has_company = has_company or company_code in local_companies
                    cross_question = cross_question or company_code not in local_companies
                    continue
                raise InvestigationError("查询路径目标无效", code="investigation_path_scope_invalid")
            if cross_question or (path.purpose_kind == "company_event_link" and not has_company):
                optional_invalid.setdefault(path.question_id, []).append(path)
                continue
            valid.setdefault(path.question_id, []).append(path)
        for question_id, rejected in optional_invalid.items():
            if not valid.get(question_id):
                raise InvestigationError("问题没有有效查证路径", code="investigation_path_scope_invalid")
        accepted = [path for path in result.query_paths
                    if path.state != "planned" or path in valid.get(path.question_id, ())]
        return replace(result, query_paths=tuple(accepted))

    def _b78_path_identity(self, path: QueryPath, question: Question | None) -> str:
        """Deduplicate a paid route by its semantic question and visible body.

        Path and question IDs are model output.  They cannot make a syndicated
        copy of the same visible text eligible for another search; a changed
        body remains an actual new dependency and can justify one.
        """
        if question is None:
            raise InvestigationError("查询路径缺少当前问题", code="investigation_path_question_invalid")
        question_row = question.to_dict()
        hashes: dict[tuple[str, int], str] = {}
        missing: list[dict[str, Any]] = []
        for ref in question.known_evidence:
            key = _key(ref)
            document = self.documents.get(key)
            identity = canonical_body_identity(document) if document is not None else None
            if identity is not None:
                hashes[(key.document_id, key.revision)] = identity
            else:
                missing.append(_ref(key))
        if missing and self.db_path.exists():
            for row in store.load_document_versions(refs=missing, db_path=self.db_path):
                identity = canonical_body_identity(row)
                if identity is not None:
                    hashes[(str(row["documentId"]), int(row["revision"]))] = identity
        # `route_identity` binds the question, semantic purpose, targets and
        # visible body.  A single question can nevertheless require several
        # independent probes (for example an issuer notice and an industry
        # report).  Collapse only the same normalized query/source/intent;
        # path IDs and arbitrary URLs remain outside the identity.
        base = route_identity(path.to_dict(), question_row,
                              question_dependency(question_row, content_sha256_by_ref=hashes))
        return _hash({
            "base": base,
            "query": normalized_query(path.query),
            "intent": normalized_query(path.intent),
            "targetSource": normalized_query(path.target_source),
        })

    def _b78_context_candidates(self, scope: Mapping[str, Any]) -> tuple[CompanyMappingDraft, ...]:
        """Make local-only candidate handles for initial market/history reads.

        A persisted title hint is a retrieval hint, never a reported company
        mapping.  The objects below are passed only to the existing local
        comparison loader so it can resolve business, market and historical
        material before the first paid research judgment.
        """
        codes: list[str] = []
        raw_codes = scope.get("candidateCompanyCodes", ()) if isinstance(scope, Mapping) else ()
        for code in raw_codes if isinstance(raw_codes, Sequence) and not isinstance(raw_codes, (str, bytes)) else ():
            if isinstance(code, str) and re.fullmatch(r"\d{6}\.(?:SZ|SH)", code):
                codes.append(code)
        profiles = scope.get("companyProfiles", ()) if isinstance(scope, Mapping) else ()
        for profile in profiles if isinstance(profiles, Sequence) and not isinstance(profiles, (str, bytes)) else ():
            identity = profile.get("identity") if isinstance(profile, Mapping) else None
            code = identity.get("ts_code") if isinstance(identity, Mapping) else None
            if isinstance(code, str) and re.fullmatch(r"\d{6}\.(?:SZ|SH)", code):
                codes.append(code)
        return tuple(CompanyMappingDraft(code, "program_context_candidate", self.event.source_refs,
                                         {"source": "persisted_title_hint"}, "待本轮研究判断")
                     for code in dict.fromkeys(codes))

    @staticmethod
    def _b78_merge_scope(scope: Mapping[str, Any], comparison_context: Mapping[str, Any]) -> dict[str, Any]:
        """Keep program-projected business fields with the initial local context."""
        merged = dict(scope)
        profiles = [row for row in scope.get("companyProfiles", ()) if isinstance(row, Mapping)]
        more_profiles = comparison_context.get("companyProfiles", ()) if isinstance(comparison_context, Mapping) else ()
        by_code = {
            row.get("identity", {}).get("ts_code"): dict(row)
            for row in [*profiles, *(row for row in more_profiles if isinstance(row, Mapping))]
            if isinstance(row.get("identity"), Mapping) and isinstance(row["identity"].get("ts_code"), str)
        }
        if by_code:
            merged["companyProfiles"] = list(by_code.values())
        candidates = [code for code in scope.get("candidateCompanyCodes", ()) if isinstance(code, str)]
        candidates.extend(code for code in comparison_context.get("candidateCompanyCodes", ())
                          if isinstance(code, str))
        if candidates:
            merged["candidateCompanyCodes"] = list(dict.fromkeys(candidates))
        return merged

    def _b78_direct_shared_source_evidence(self) -> dict[str, list[dict[str, Any]]]:
        """Read same-task peer facts from committed B78 rounds only.

        Direct rounds deliberately do not project results into the retired
        stage/claim tables.  A later event sharing an already frozen source
        must therefore read the peer's accepted direct packet/result instead
        of seeing an empty reusable section.  This stays source-fact-only:
        peer mappings, rankings, model receipts, and article bodies remain
        private to the peer round.
        """
        from .schema import read_connection

        task_id = getattr(self, "task_id", self.snapshot.task_id)
        current_sources = {_ref(ref)["documentId"] + "@" + str(ref.revision) for ref in self.allowed}
        if not current_sources:
            return {"claims": [], "isolated": []}
        try:
            news_cutoff = datetime.fromisoformat(self.snapshot.news_cutoff_at)
            verification_cutoff = datetime.fromisoformat(self.snapshot.verification_cutoff_at)
        except ValueError:
            return {"claims": [], "isolated": [{"kind": "snapshot", "reason": "current_cutoff_invalid"}]}
        if news_cutoff.tzinfo is None or verification_cutoff.tzinfo is None:
            return {"claims": [], "isolated": [{"kind": "snapshot", "reason": "current_cutoff_invalid"}]}

        with read_connection(self.db_path) as conn:
            peers = conn.execute(
                "SELECT r.snapshot_id,r.revision,r.event_id,r.event_revision,r.news_cutoff_at,"
                "r.verification_cutoff_at,rr.input_packet_json,rr.result_json,er.source_refs_json "
                "FROM k10_research_snapshot_revisions r "
                "JOIN k10_research_round_results rr ON rr.snapshot_id=r.snapshot_id AND rr.revision=r.revision "
                "JOIN k10_event_revisions er ON er.event_id=r.event_id AND er.revision=r.event_revision "
                "WHERE r.task_id=? AND r.snapshot_id<>? AND r.prompt_contract_revision=? "
                "AND r.model_parameters_sha256=? ORDER BY r.updated_at,r.snapshot_id,r.revision",
                (task_id, self.identity, self.snapshot.prompt_contract_revision,
                 self.snapshot.model_parameters_sha256),
            ).fetchall()

            claims: list[dict[str, Any]] = []
            isolated: list[dict[str, Any]] = []
            seen: set[tuple[str, str, int]] = set()
            for (snapshot_id, revision, event_id, event_revision, peer_news, peer_verification,
                 packet_json, result_json, source_refs_json) in peers:
                provenance = {
                    "taskId": task_id, "snapshotId": str(snapshot_id), "snapshotRevision": int(revision),
                    "eventId": str(event_id), "eventRevision": int(event_revision),
                    "newsCutoffAt": str(peer_news), "verificationCutoffAt": str(peer_verification),
                }
                try:
                    peer_sources = {
                        str(item["documentId"]) + "@" + str(item["revision"])
                        for item in json.loads(source_refs_json)
                        if isinstance(item, Mapping) and isinstance(item.get("documentId"), str)
                        and isinstance(item.get("revision"), int) and not isinstance(item.get("revision"), bool)
                    }
                    peer_news_at = datetime.fromisoformat(str(peer_news))
                    peer_verification_at = datetime.fromisoformat(str(peer_verification))
                    packet, result = json.loads(packet_json), json.loads(result_json)
                except (TypeError, ValueError, json.JSONDecodeError):
                    isolated.append({"kind": "snapshot", "reason": "direct_round_state_invalid", "provenance": provenance})
                    continue
                if not peer_sources.intersection(current_sources):
                    continue
                if (peer_news_at.tzinfo is None or peer_verification_at.tzinfo is None
                        or peer_news_at > news_cutoff or peer_verification_at > verification_cutoff):
                    isolated.append({"kind": "snapshot", "reason": "snapshot_after_current_cutoff", "provenance": provenance})
                    continue
                if not isinstance(packet, Mapping) or not isinstance(result, Mapping):
                    isolated.append({"kind": "snapshot", "reason": "direct_round_state_invalid", "provenance": provenance})
                    continue
                source_claims = [*packet.get("claims", ()), *result.get("claims", ())]
                for raw in source_claims:
                    if not isinstance(raw, Mapping):
                        continue
                    try:
                        claim = Claim.from_dict(raw)
                        ref = _key(claim.source_ref)
                    except (ResearchContractError, InvestigationError):
                        continue
                    source_key = ref.document_id + "@" + str(ref.revision)
                    if source_key not in current_sources:
                        continue
                    row = conn.execute(
                        "SELECT published_at,published_precision,fetched_at FROM k10_source_document_versions "
                        "WHERE document_id=? AND revision=?", (ref.document_id, ref.revision),
                    ).fetchone()
                    if row is None or row[1] != "exact" or row[0] is None:
                        isolated.append({"kind": "claim", "reason": "unknown_published_time", "provenance": provenance,
                                         "sourceRef": _ref(ref)})
                        continue
                    try:
                        published_at, fetched_at = datetime.fromisoformat(str(row[0])), datetime.fromisoformat(str(row[2]))
                    except ValueError:
                        isolated.append({"kind": "claim", "reason": "unknown_source_time", "provenance": provenance,
                                         "sourceRef": _ref(ref)})
                        continue
                    if (published_at.tzinfo is None or fetched_at.tzinfo is None or published_at > news_cutoff
                            or fetched_at > verification_cutoff):
                        isolated.append({"kind": "claim", "reason": "source_outside_current_cutoff", "provenance": provenance,
                                         "sourceRef": _ref(ref)})
                        continue
                    key = (claim.text, ref.document_id, ref.revision)
                    if key in seen:
                        continue
                    seen.add(key)
                    claims.append({
                        **claim.to_dict(), "sourceTiming": {"publishedAt": str(row[0]), "fetchedAt": str(row[2])},
                        "provenance": provenance,
                    })
        return {"claims": claims, "isolated": isolated}

    def _b78_reusable_source_evidence(self) -> dict[str, Any]:
        """Combine readable historical facts with B78's direct peer facts."""
        # Unit-level direct packet construction is storage-free by design.
        # Production always has the snapshot database, while an absent local
        # database cannot contain a committed peer fact to share.
        if not self.db_path.exists():
            return {"claims": [], "companyRelations": [], "isolated": []}
        task_id = getattr(self, "task_id", self.snapshot.task_id)
        legacy = load_prior_research_evidence(
            task_id=task_id, input_source_refs=[_ref(ref) for ref in self.allowed],
            news_cutoff_at=self.snapshot.news_cutoff_at,
            verification_cutoff_at=self.snapshot.verification_cutoff_at,
            prompt_contract_revision=self.snapshot.prompt_contract_revision,
            model_parameters_sha256=self.snapshot.model_parameters_sha256,
            db_path=self.db_path, canonical_key=self.event.canonical_key,
            exclude_snapshot_id=self.identity,
        )
        direct = self._b78_direct_shared_source_evidence()
        combined: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int]] = set()
        for raw in [*legacy.get("claims", ()), *direct["claims"]]:
            if not isinstance(raw, Mapping):
                continue
            try:
                # A peer's verification is evidence about that peer's work,
                # not proof that this event's question is verified.  Only the
                # visible source fact may cross the boundary.
                claim = Claim.from_dict({**raw, "verificationStatus": "unverified"})
                ref = _key(claim.source_ref)
            except (ResearchContractError, InvestigationError):
                continue
            key = (claim.text, ref.document_id, ref.revision)
            if key in seen:
                continue
            seen.add(key)
            row = claim.to_dict()
            timing = raw.get("sourceTiming")
            if isinstance(timing, Mapping) and all(isinstance(timing.get(key), str)
                                                   for key in ("publishedAt", "fetchedAt")):
                row["sourceTiming"] = {key: timing[key] for key in ("publishedAt", "fetchedAt")}
            provenance = raw.get("provenance")
            if isinstance(provenance, Mapping):
                safe_provenance = {
                    key: provenance[key] for key in (
                        "taskId", "snapshotId", "snapshotRevision", "eventId", "eventRevision",
                        "newsCutoffAt", "verificationCutoffAt",
                    ) if isinstance(provenance.get(key), (str, int)) and not isinstance(provenance.get(key), bool)
                }
                if safe_provenance:
                    row["provenance"] = safe_provenance
            combined.append(row)
        return {
            "claims": combined,
            # Same-task direct facts must never smuggle a peer's company
            # inference; old, readable cross-task relations retain their
            # existing evidence/time checks in the store helper.
            "companyRelations": list(legacy.get("companyRelations", ())),
            "isolated": [*legacy.get("isolated", ()), *direct["isolated"]],
        }

    def _b78_packet(self, *, claims: Sequence[Claim] | None = None,
                    questions: Sequence[Question] = (), query_paths: Sequence[QueryPath] = (),
                    evidence_updates: Sequence[Mapping[str, Any]] = (),
                    fulltext_requests: Sequence[FullTextRequest] = (),
                    context_results: Sequence[Mapping[str, Any]] = (),
                    fulltext_refs: Sequence[EvidenceRef] = (),
                    company_scope: Mapping[str, Any] | None = None,
                    comparison_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Prepare one visible, program-owned packet for a direct B78 result.

        The frozen source body is never forwarded again.  Supplemental search
        excerpts and exact local source reads have distinct packet fields, so
        a subsequent direct result can rely only on material it can actually
        see.
        """
        if claims is None:
            raw_claims = self.event.facts.get("researchClaims", [])
            if not isinstance(raw_claims, list):
                raise InvestigationError("已读原文缺少命题提取结果", code="investigation_claims_missing")
            claims = tuple(Claim.from_dict(item) for item in raw_claims)
        if any(_key(item.source_ref) not in self.allowed for item in claims):
            raise InvestigationError("原文命题引用错误", code="investigation_reference_invalid")
        cards: list[dict[str, Any]] = []
        for ref in sorted(self.allowed, key=lambda item: (item.document_id, item.revision)):
            document = self.documents.get(ref)
            if document is None or admit_material(document).state == "excluded":
                continue
            # The source-understanding boundary already produced attributed
            # claims for the event article.  Repeating its body would make a
            # fresh direct round look like a new evidence acquisition.
            excerpt = None if ref in self.event.source_refs else document.excerpt
            card = {
                **_ref(ref), "publishedAt": document.published_at, "fetchedAt": document.fetched_at,
                "excerpt": excerpt,
                "sourceStatements": [claim.to_dict() for claim in claims if _key(claim.source_ref) == ref],
            }
            # The navigation projection is a hash/range manifest.  Preserve
            # it even when an event-source card deliberately omits its body,
            # so a restored stale local read cannot smuggle a publisher widget
            # merely because this packet has no visible excerpt.
            raw_source = document.original_text or document.analysis_text
            if isinstance(raw_source, str):
                _visible, projection = navigation_view(raw_source, enabled=sina_source(document.metadata))
                if projection:
                    card["sourceViewProjection"] = projection
            cards.append(card)
        fulltext_cards: list[dict[str, Any]] = []
        for ref in dict.fromkeys(fulltext_refs):
            document = self.documents.get(ref)
            if document is None or admit_material(document).state == "excluded":
                continue
            # project_packet removes bodies from this field and exposes only a
            # bounded excerpt/locator.  A follow-up local source read is the
            # sole way that a full document becomes textual model evidence.
            card = {
                **_ref(ref), "publishedAt": document.published_at, "fetchedAt": document.fetched_at,
                "excerpt": document.excerpt, "eligibleAtNewsCutoff": ref in self.allowed,
                "contentVersionAtCutoff": document.metadata.get("contentVersionAtCutoff"),
            }
            raw_source = document.original_text or document.analysis_text
            if isinstance(raw_source, str):
                _visible, projection = navigation_view(raw_source, enabled=sina_source(document.metadata))
                if projection:
                    card["sourceViewProjection"] = projection
            fulltext_cards.append(card)
        context = comparison_context or {}
        packet_context = self._b78_packet_context(context)
        scope = self._b78_merge_scope(company_scope or self._company_scope(), context)
        return build_research_round_packet({
            "event": {key: self.context[key] for key in ("canonicalKey", "stageKey", "eventState", "headline", "eventKind")},
            "companyScope": scope,
            # These are local, frozen inputs to the first comparison.  They
            # are supplied before the model decides whether it has a gap.
            "marketContext": packet_context.get("marketContext", {}),
            "historicalCases": packet_context.get("historicalCases", []),
            "historicalCoverage": packet_context.get("historicalCoverage", {}),
            "newsCutoffAt": self.snapshot.news_cutoff_at,
            "allowedEvidenceRefs": [_ref(ref) for ref in sorted(self.allowed, key=lambda item: (item.document_id, item.revision))],
            "claims": self._b78_rows(claims), "questions": self._b78_rows(questions),
            "queryPaths": self._b78_rows(query_paths),
            "evidenceUpdates": [dict(item) for item in evidence_updates],
            "fulltextRequests": self._b78_rows(fulltext_requests),
            "evidenceCards": cards, "fullTextDocuments": fulltext_cards,
            "contextResults": [dict(item) for item in context_results],
            "newEvidenceRefs": [_ref(ref) for ref in fulltext_refs if ref in self.allowed],
            "reusableSourceEvidence": self._b78_reusable_source_evidence(),
            "availableArticlePolicy": {"fullTextQuota": None, "eventShared": True},
        })

    def _b78_comparison_context(self, mappings: Sequence[CompanyMappingDraft]) -> Mapping[str, Any]:
        loader = getattr(self.model, "research_comparison_context", None)
        if not callable(loader):
            raise InvestigationError("缺少公司市场和历史上下文", code="investigation_comparison_context_missing")
        context = loader(event=self.event, mappings=mappings)
        if not isinstance(context, Mapping) or any(key not in context for key in ("marketContext", "historicalCases", "historicalCoverage")):
            raise InvestigationError("公司市场和历史上下文无效", code="investigation_comparison_context_invalid")
        return context

    @staticmethod
    def _b78_packet_context(value: Any) -> Any:
        """Remove non-evidentiary collection clocks from a paid packet.

        ``collectedAt`` says when the local loader happened to run.  The
        actual market/history fact remains bound by its ``asOf`` field.  A
        recovery must not create a new paid input merely because collection
        bookkeeping advanced between worker slices.
        """
        if isinstance(value, Mapping):
            return {key: _Investigation._b78_packet_context(item)
                    for key, item in value.items() if key != "collectedAt"}
        if isinstance(value, (list, tuple)):
            return [_Investigation._b78_packet_context(item) for item in value]
        return value

    @staticmethod
    def _b78_context_is_visible(value: Mapping[str, Any]) -> bool:
        """A local read advances a round only when it adds safe visible facts."""
        if value.get("status") != "found" or not isinstance(value.get("value"), Mapping):
            return False
        request, material = value.get("request", {}), value["value"]
        kind = request.get("kind") if isinstance(request, Mapping) else None
        if kind == "source":
            return (material.get("eligibleAtNewsCutoff") is True
                    and isinstance(material.get("text"), str) and bool(material["text"].strip()))
        if kind == "company_search":
            return bool(material.get("companyProfiles"))
        if kind == "company_fields":
            return isinstance(material.get("fields"), Mapping) and bool(material["fields"])
        # Claims and questions were already in the packet; returning their ID
        # again cannot justify another paid round.
        return False

    def _b78_context_binding(self) -> Any:
        binding = getattr(self.model, "_company_profiles_binding", None)
        return binding if binding is not None else getattr(getattr(self.model, "_base", None),
                                                            "_company_profiles_binding", None)

    def _b78_read_context(self, requests: Sequence[Mapping[str, Any]], *, packet: Mapping[str, Any],
                          claims: Sequence[Claim], questions: Sequence[Question],
                          prior: Sequence[Mapping[str, Any]], seen: set[str]) -> list[dict[str, Any]]:
        """Resolve direct-round local reads without re-entering old actions."""
        checker = getattr(self.model, "source_context_request_fits", None)
        state = {"claims": self._b78_rows(claims), "questions": self._b78_rows(questions)}
        results: list[dict[str, Any]] = []
        def material_key(item):
            return _hash({"request": canonical_context_request(item["request"]),
                          "value": item.get("value")})
        for item in prior:
            if isinstance(item, Mapping) and isinstance(item.get("request"), Mapping):
                try:
                    seen.add(material_key(item))
                except ValueError:
                    pass
        for request in requests:
            if not isinstance(request, Mapping):
                continue
            # Do not repeatedly invoke an otherwise safe local reader merely
            # because a model changed its purpose prose.
            try:
                request = {**canonical_context_request(request), "purpose": request.get("purpose")}
            except ValueError:
                continue
            request_identity = _hash(canonical_context_request(request))
            if request_identity in seen:
                continue
            def fits(candidate: Mapping[str, Any]) -> bool:
                if not callable(checker):
                    return True
                return bool(checker(snapshot=self.snapshot, action=RESEARCH_ROUND_ACTION,
                                    evidence_packet=packet, candidate=candidate))
            try:
                result = read_context(dict(request), state=state, documents=self.documents,
                                      binding=self._b78_context_binding(),
                                      eligible_refs={(ref.document_id, ref.revision) for ref in self.allowed},
                                      request_fits=fits, visible_packet=packet)
            except (TypeError, ValueError):
                # A malformed local-read request is not an evidence increment
                # and cannot make the direct runner manufacture a second call.
                seen.add(request_identity)
                continue
            seen.add(request_identity)
            if not self._b78_context_is_visible(result):
                continue
            material_identity = material_key(result)
            if material_identity in seen:
                continue
            seen.add(material_identity)
            results.append(dict(result))
        return results

    def _b78_accept_bundle(self, bundle: Any) -> tuple[tuple[EvidenceRef, ...], dict[str, Any]]:
        """Admit a completed local/search result into the next visible packet.

        This keeps Tavily receipts and provider state on their own boundary.
        The direct research runner retains only document references and safe
        coverage metadata; it never emits an assess-evidence stage.
        """
        coverage = getattr(bundle, "coverage", {})
        if not isinstance(coverage, Mapping):
            raise InvestigationError("补查覆盖信息无效", code="investigation_tool_failed")
        reason = coverage.get("reason")
        if reason in {"insufficient_balance", "provider_authorization_failed"}:
            self.model._terminal_provider_error = reason
        if coverage.get("requestState") == "pending":
            raise InvestigationError("资料调用尚未完成",
                                     code=str(reason or "investigation_tool_failed"))
        documents = getattr(bundle, "documents", ())
        eligible = getattr(bundle, "eligible_documents", ())
        if not isinstance(documents, Sequence) or isinstance(documents, (str, bytes)):
            raise InvestigationError("补查文档无效", code="investigation_tool_failed")
        if not isinstance(eligible, Sequence) or isinstance(eligible, (str, bytes)):
            raise InvestigationError("补查可用文档无效", code="investigation_tool_failed")
        before = set(self.allowed)
        for document in documents:
            if not isinstance(document, DiscoveryDocument):
                raise InvestigationError("补查文档无效", code="investigation_tool_failed")
            self.documents[document.evidence_ref] = document
        for document in eligible:
            if not isinstance(document, DiscoveryDocument):
                raise InvestigationError("补查可用文档无效", code="investigation_tool_failed")
            if admit_material(document).state != "excluded":
                self.documents[document.evidence_ref] = document
                self.allowed.add(document.evidence_ref)
        new_visible = tuple(ref for ref in self.allowed - before
                            if (doc := self.documents.get(ref)) is not None
                            and isinstance(doc.excerpt, str) and bool(doc.excerpt.strip()))
        safe_coverage = {key: value for key, value in coverage.items()
                         if key not in {"rawResponse", "raw_response", "originalText", "original_text",
                                        "analysisText", "analysis_text", "body"}}
        return new_visible, {
            "documentRefs": [_ref(document) for document in documents if isinstance(document, DiscoveryDocument)],
            "eligibleDocumentRefs": [_ref(document) for document in eligible if isinstance(document, DiscoveryDocument)],
            "coverage": safe_coverage,
        }

    def _b78_followups(self, *, result: ResearchRoundResult, packet: Mapping[str, Any],
                       claims: Sequence[Claim], questions: dict[str, Question],
                       paths: dict[str, QueryPath], fulltexts: dict[str, FullTextRequest],
                       context_results: list[Mapping[str, Any]], fulltext_refs: list[EvidenceRef],
                       seen_paths: set[str], seen_fulltexts: set[tuple[str, EvidenceRef]],
                       seen_context: set[str], tool_evidence: list[Mapping[str, Any]]) -> bool:
        """Execute necessary searches/extracts and expose only new safe evidence.

        A fresh direct model call is permitted only when this method (or a
        context read) has produced an actual visible increment.  Empty,
        duplicated, pending and out-of-window results therefore end as a
        pending research outcome instead of creating a synthetic close call.
        """
        changed = False
        for path in result.query_paths:
            if path.state != "planned":
                paths[path.path_id] = path
                continue
            question = questions.get(path.question_id)
            path = self._b78_bound_query_path(path, question)
            identity = self._b78_path_identity(path, question)
            if identity in seen_paths:
                paths[path.path_id] = replace(path, state="blocked", result_summary="重复的已执行查证路径")
                continue
            seen_paths.add(identity)
            self._external_guard()
            bundle = self.verifier.fetch(event=self.event, retrieved_at=self.clock(), cutoff_at=self.cutoff,
                                         cutoff_inclusive=self.cutoff_inclusive, question=question,
                                         query_path=path)
            new_refs, receipt = self._b78_accept_bundle(bundle)
            tool_evidence.append({"kind": "query", "pathId": path.path_id,
                                  "questionScope": dict(path.question_scope or {}), **receipt})
            paths[path.path_id] = replace(path, state="searched" if new_refs else "no_result",
                                          result_summary=str(receipt["coverage"].get("reason") or
                                                             ("已取得新资料" if new_refs else "未取得可见新资料")))
            changed = changed or bool(new_refs)
        for request in result.fulltext_requests:
            if request.state not in {"requested", "admitted"}:
                fulltexts[request.request_id] = request
                continue
            ref = _key(request.source_ref)
            identity = (request.question_id, ref)
            if identity in seen_fulltexts:
                fulltexts[request.request_id] = replace(request, state="rejected")
                continue
            question = questions.get(request.question_id)
            document = self.documents.get(ref)
            if question is None or document is None:
                raise InvestigationError("全文申请的调查上下文丢失", code="investigation_fulltext_scope_invalid")
            seen_fulltexts.add(identity)
            self._external_guard()
            if ref in self.event.source_refs:
                # Original event bodies were consumed at source-understanding.
                # A direct round may not turn the same source into a second
                # evidence acquisition merely by asking for its full text.
                from .verification import VerificationEvidenceBundle
                admitted = admit_material(document)
                local = (replace(document, analysis_text=document.original_text),) if (
                    admitted.state == "admit" and isinstance(document.original_text, str)
                    and bool(document.original_text.strip())) else ()
                bundle = VerificationEvidenceBundle("available" if local else "pending", local,
                                                    local if ref in self.allowed else (), {
                    "provider": "local", "operation": "extract", "requestState": "reused",
                    "state": "available" if local else "pending",
                    "reason": "original_article_reread" if local else "original_fulltext_unavailable",
                    "independentVerification": False,
                })
            else:
                bundle = self.verifier.fetch_fulltext(event=self.event, document=document, question=question,
                                                       request=request, cutoff_at=self.cutoff,
                                                       cutoff_inclusive=self.cutoff_inclusive)
            new_refs, receipt = self._b78_accept_bundle(bundle)
            tool_evidence.append({"kind": "fulltext", "requestId": request.request_id, **receipt})
            fulltexts[request.request_id] = replace(request,
                state="fulfilled" if receipt["documentRefs"] else "rejected",
                admission_ref=receipt["coverage"].get("admissionRef"))
            for ref_row in receipt["documentRefs"]:
                fulltext_ref = _key(ref_row)
                if fulltext_ref not in fulltext_refs:
                    fulltext_refs.append(fulltext_ref)
            changed = changed or bool(new_refs)
            # Extract returns a durable body but the B78 packet may expose it
            # only through a bounded local read.  Use the model's already
            # stated reason and the stored excerpt; do not invent a body dump
            # or re-read the original event article.
            if ref not in self.event.source_refs and receipt["documentRefs"]:
                local_reads = self._b78_read_context(({
                    "kind": "source", "purpose": request.reason_excerpt_insufficient,
                    "questionId": question.question_id, "sourceRef": request.source_ref,
                    "location": "excerpt",
                },), packet={**packet, "questions": self._b78_rows(tuple(questions.values()))},
                    claims=claims, questions=tuple(questions.values()), prior=context_results,
                    seen=seen_context)
                if local_reads:
                    context_results.extend(local_reads)
                    changed = True
        return changed

    def _b78_pending(self, reason: str) -> InvestigationOutcome:
        return InvestigationOutcome(
            Verification("needs_review", reason, self.event.source_refs,
                {"state": "pending_context", "researchSnapshotId": self.identity,
                 "researchRevision": self.snapshot.revision,
                 "verificationCutoffAt": self.snapshot.verification_cutoff_at}, ()),
            (), EventComparison(reason, {}, self.event.source_refs), self.identity,
        )

    @staticmethod
    def _b78_terminal_pending_result(result: ResearchRoundResult, reason: str) -> ResearchRoundResult:
        """Turn an exhausted local gap into a readable direct-round state.

        The paid response remains exact in the model checkpoint.  This is the
        program's local disposition after its requested read/search produced
        no visible evidence: it must stop automatic research and make the
        snapshot readable as pending rather than claim it is still running.
        """
        conclusion = dict(result.conclusion or {})
        # A necessary search can be exhausted without disproving a mapping
        # already grounded in the visible packet.  Keep that factual mapping
        # in the durable pending receipt, while deliberately clearing the
        # assessment/ranking that would turn it into a recommendation.
        conclusion.setdefault("companyMappings", [])
        conclusion.update({
            "researchStatus": "pending_verification",
            "stopReason": reason,
            "resumeCondition": reason,
        })
        return replace(
            result,
            context_requests=(),
            conclusion=conclusion,
            company_assessments=(), comparison=None,
        )

    def _b78_append(self, *, packet: Mapping[str, Any], result: ResearchRoundResult,
                    context_results: Sequence[Mapping[str, Any]],
                    tool_evidence: Sequence[Mapping[str, Any]], research_status: str) -> None:
        """Append one direct receipt and its program work, never an old stage.

        A caller invokes this only after the receipt's local reads/searches
        have completed.  Thus recovery can rebuild the exact next packet from
        the durable result plus visible increments, while the checkpointed
        model adapter replays an interrupted paid reply without a POST.
        """
        if not getattr(self, "b78_research", False):
            # Narrow unit tests can exercise pure runtime logic without an
            # initialized Schema10 database. Production B78 always takes the
            # durable branch created in __init__.
            return
        next_snapshot = append_research_round(
            snapshot_id=self.identity, expected_revision=self.snapshot.revision,
            input_packet=packet, result=result, local_context_results=context_results,
            local_tool_evidence=tool_evidence, research_status=research_status,
            updated_at=_text(max(self.clock(), datetime.fromisoformat(self.snapshot.verification_cutoff_at))),
            db_path=self.db_path, lease_guard=self.guard,
        )
        self.state["snapshot"] = next_snapshot

    def _b78_mark_failed(self, *, packet: Mapping[str, Any], safe_error_code: str) -> None:
        """Append a safe direct-only failure after a paid receipt is rejected.

        A malformed B78 response is an event-local settled outcome, not an
        invitation to replay the same model call.  The storage helper records
        only the packet digest and a safe error code; exact paid material is
        still owned by the model-operation checkpoint.
        """
        if not getattr(self, "b78_research", False):
            return
        next_snapshot = mark_research_round_failed(
            snapshot_id=self.identity, expected_revision=self.snapshot.revision,
            input_packet=packet, safe_error_code=safe_error_code,
            updated_at=_text(max(self.clock(), datetime.fromisoformat(self.snapshot.verification_cutoff_at))),
            db_path=self.db_path, lease_guard=self.guard,
        )
        self.state["snapshot"] = next_snapshot

    def _b78_failed_round_error(self) -> str | None:
        """Read the safe terminal code of the current direct receipt only."""
        if not getattr(self, "b78_research", False):
            return None
        restored = load_research_round_state(snapshot_id=self.identity, db_path=self.db_path)
        rounds = restored.get("rounds") if isinstance(restored, Mapping) else None
        if not isinstance(rounds, list) or not rounds:
            return None
        raw = rounds[-1].get("result") if isinstance(rounds[-1], Mapping) else None
        code = raw.get("safeErrorCode") if isinstance(raw, Mapping) else None
        return code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{3,80}", code) else None

    def _b78_terminal_provider_failure(self, code: str) -> None:
        """Make a restored account failure visible to both model wrappers."""
        for model in (self.model, getattr(self.model, "_base", None)):
            if model is not None:
                try:
                    setattr(model, "_terminal_provider_error", code)
                except (AttributeError, TypeError):
                    pass

    def _b78_restore(self, *, initial_claims: Mapping[str, Claim]) -> dict[str, Any] | None:
        """Restore direct-round packet state without consulting legacy stages."""
        if not getattr(self, "b78_research", False):
            return None
        restored = load_research_round_state(snapshot_id=self.identity, db_path=self.db_path)
        if restored is None:
            raise InvestigationError("研究快照丢失", code="investigation_snapshot_missing")
        snapshot = restored.get("snapshot")
        rounds = restored.get("rounds")
        if not isinstance(snapshot, ResearchSnapshot) or not isinstance(rounds, list):
            raise InvestigationError("研究轮次状态不可读取", code="investigation_round_state_invalid")
        self.state["snapshot"] = snapshot
        if not rounds:
            return None
        claims = dict(initial_claims)
        questions: dict[str, Question] = {}
        paths: dict[str, QueryPath] = {}
        fulltexts: dict[str, FullTextRequest] = {}
        updates: list[Mapping[str, Any]] = []
        contexts: list[Mapping[str, Any]] = []
        fulltext_refs: list[EvidenceRef] = []
        seen_paths: set[str] = set()
        seen_fulltexts: set[tuple[str, EvidenceRef]] = set()
        seen_context: set[str] = set()
        tool_evidence: list[Mapping[str, Any]] = []
        refs: set[EvidenceRef] = set()
        allowed: set[EvidenceRef] = set(self.event.source_refs)
        first_packet: Mapping[str, Any] | None = None
        last_result: ResearchRoundResult | None = None
        last_packet: Mapping[str, Any] | None = None
        last_contexts: Sequence[Mapping[str, Any]] = ()
        last_tools: Sequence[Mapping[str, Any]] = ()
        for round_state in rounds:
            if not isinstance(round_state, Mapping):
                raise InvestigationError("研究轮次状态不可读取", code="investigation_round_state_invalid")
            packet = round_state.get("inputPacket")
            raw_result = round_state.get("result")
            saved_contexts = round_state.get("contextResults", ())
            saved_tools = round_state.get("toolEvidence", ())
            if not isinstance(packet, Mapping) or not isinstance(raw_result, Mapping):
                raise InvestigationError("研究轮次状态不可读取", code="investigation_round_state_invalid")
            try:
                result = ResearchRoundResult.from_dict(raw_result)
            except ResearchContractError as exc:
                raise InvestigationError("研究轮次状态不可读取", code="investigation_round_state_invalid") from exc
            first_packet = packet if first_packet is None else first_packet
            last_result = result
            last_packet = packet
            # A restored question may name a source fact that was visible in
            # its paid packet but was never copied to an old stage table.
            # Recover the typed, already-namespaced source identity from that
            # packet; a local event claim always wins a collision.
            for claim in _b78_visible_shared_claims(packet):
                claims.setdefault(claim.claim_id, claim)
            claims.update({item.claim_id: item for item in result.claims})
            questions.update({item.question_id: item for item in result.questions})
            updates.extend(item for item in result.evidence_updates if isinstance(item, Mapping))
            for path in result.query_paths:
                paths[path.path_id] = path
                seen_paths.add(self._b78_path_identity(path, questions.get(path.question_id)))
            for request in result.fulltext_requests:
                fulltexts[request.request_id] = request
                seen_fulltexts.add((request.question_id, _key(request.source_ref)))
            if not isinstance(saved_contexts, Sequence) or isinstance(saved_contexts, (str, bytes)):
                raise InvestigationError("研究轮次本地读取不可恢复", code="investigation_round_state_invalid")
            last_contexts = saved_contexts
            for item in saved_contexts:
                if not isinstance(item, Mapping):
                    raise InvestigationError("研究轮次本地读取不可恢复", code="investigation_round_state_invalid")
                contexts.append(dict(item))
                request = item.get("request")
                if isinstance(request, Mapping):
                    seen_context.add(_hash(dict(request)))
                    seen_context.add(_hash({"request": request, "contentSha256": item.get("contentSha256")}))
            if not isinstance(saved_tools, Sequence) or isinstance(saved_tools, (str, bytes)):
                raise InvestigationError("研究轮次补查不可恢复", code="investigation_round_state_invalid")
            last_tools = saved_tools
            for item in saved_tools:
                if not isinstance(item, Mapping):
                    raise InvestigationError("研究轮次补查不可恢复", code="investigation_round_state_invalid")
                tool = dict(item)
                tool_evidence.append(tool)
                kind = tool.get("kind")
                if kind == "query" and isinstance(tool.get("pathId"), str) and tool["pathId"] in paths:
                    path = paths[tool["pathId"]]
                    paths[path.path_id] = replace(path, state="searched" if tool.get("eligibleDocumentRefs") else "no_result",
                                                  result_summary=str((tool.get("coverage") or {}).get("reason") or
                                                                     "已恢复查证结果"))
                if kind == "fulltext" and isinstance(tool.get("requestId"), str) and tool["requestId"] in fulltexts:
                    request = fulltexts[tool["requestId"]]
                    fulltexts[request.request_id] = replace(request,
                        state="fulfilled" if tool.get("documentRefs") else "rejected",
                        admission_ref=(tool.get("coverage") or {}).get("admissionRef"))
                for row in tool.get("documentRefs", ()) if isinstance(tool.get("documentRefs"), Sequence) else ():
                    refs.add(_key(row))
                    if kind == "fulltext":
                        ref = _key(row)
                        if ref not in fulltext_refs:
                            fulltext_refs.append(ref)
                for row in tool.get("eligibleDocumentRefs", ()) if isinstance(tool.get("eligibleDocumentRefs"), Sequence) else ():
                    allowed.add(_key(row))
        if refs:
            for row in store.load_document_versions(refs=[_ref(ref) for ref in refs], db_path=self.db_path):
                key = _key(row)
                self.documents[key] = DiscoveryDocument(row["documentId"], row["revision"], row.get("publishedAt"),
                    row["fetchedAt"], row.get("originalText"), row.get("excerpt"), row.get("metadata") or {})
            if not refs <= set(self.documents):
                raise InvestigationError("已保存研究补查来源不可读取", code="investigation_source_missing")
        self.allowed = {ref for ref in allowed
                        if ref not in self.documents or admit_material(self.documents[ref]).state != "excluded"}
        seen_paths = {
            self._b78_path_identity(path, questions.get(path.question_id))
            for path in paths.values()
        }
        last_visible_increment = any(
            isinstance(item, Mapping) and self._b78_context_is_visible(item)
            for item in last_contexts
        )
        if isinstance(last_packet, Mapping):
            visible_before = {_key(row) for row in last_packet.get("allowedEvidenceRefs", ())
                              if isinstance(row, Mapping)}
            for tool in last_tools:
                if not isinstance(tool, Mapping):
                    continue
                for row in tool.get("eligibleDocumentRefs", ()) if isinstance(tool.get("eligibleDocumentRefs"), Sequence) else ():
                    try:
                        ref = _key(row)
                    except InvestigationError:
                        continue
                    document = self.documents.get(ref)
                    if (ref not in visible_before and document is not None
                            and isinstance(document.excerpt, str) and document.excerpt.strip()
                            and admit_material(document).state != "excluded"):
                        last_visible_increment = True
        return {
            "claims": claims, "questions": questions, "paths": paths, "fulltexts": fulltexts,
            "evidenceUpdates": updates, "contextResults": contexts, "fulltextRefs": fulltext_refs,
            "seenPaths": seen_paths, "seenFulltexts": seen_fulltexts, "seenContext": seen_context,
            "toolEvidence": tool_evidence, "firstPacket": first_packet, "lastResult": last_result,
            "lastRoundNoVisibleIncrement": bool(last_result is not None
                and (last_result.context_requests or last_result.query_paths or last_result.fulltext_requests
                     or (isinstance(last_result.conclusion, Mapping)
                         and last_result.conclusion.get("researchStatus") == "continue_research"))
                and not last_visible_increment),
        }

    def _run_b78(self) -> InvestigationOutcome:
        """Run direct rounds around actual local evidence increments only."""
        if self.snapshot.execution_status == "failed":
            # A recovery is allowed to resume a settled event-local failure,
            # never a provider account failure.  The latter has no executable
            # remediation in this frozen task; treating it as a partial event
            # would let a later recovery exhaust a fresh paid attempt.
            failure_code = self._b78_failed_round_error()
            if failure_code in {"insufficient_balance", "provider_authorization_failed"}:
                self._b78_terminal_provider_failure(failure_code)
                message = ("余额不足，停止后续模型及搜索步骤" if failure_code == "insufficient_balance"
                           else "供应商授权失败，停止后续模型及搜索步骤")
                raise InvestigationError(message, code=failure_code)
            if not self.allow_failed_resume:
                raise InvestigationError("调查执行失败，等待任务恢复", code="investigation_previously_failed")
        if self.snapshot.execution_status == "paused":
            raise InvestigationError("调查已暂停，等待同一任务恢复", code="investigation_execution_paused")
        company_scope = self._company_scope()
        initial_context = self._b78_comparison_context(self._b78_context_candidates(company_scope))
        raw_claims = self.event.facts.get("researchClaims", [])
        if not isinstance(raw_claims, list):
            raise InvestigationError("已读原文缺少命题提取结果", code="investigation_claims_missing")
        claims = {item.claim_id: item for item in (Claim.from_dict(row) for row in raw_claims)}
        restored = self._b78_restore(initial_claims=claims)
        if restored is None:
            questions: dict[str, Question] = {}
            paths: dict[str, QueryPath] = {}
            fulltexts: dict[str, FullTextRequest] = {}
            evidence_updates: list[Mapping[str, Any]] = []
            context_results: list[Mapping[str, Any]] = []
            fulltext_refs: list[EvidenceRef] = []
            seen_paths: set[str] = set()
            seen_fulltexts: set[tuple[str, EvidenceRef]] = set()
            seen_context: set[str] = set()
            tool_evidence: list[Mapping[str, Any]] = []
            packet = self._b78_packet(claims=tuple(claims.values()), company_scope=company_scope,
                                      comparison_context=initial_context)
            claims.update({claim.claim_id: claim for claim in _b78_visible_shared_claims(packet)
                           if claim.claim_id not in claims})
            result: ResearchRoundResult | None = None
        else:
            claims = restored["claims"]
            questions = restored["questions"]
            paths = restored["paths"]
            fulltexts = restored["fulltexts"]
            evidence_updates = restored["evidenceUpdates"]
            context_results = restored["contextResults"]
            fulltext_refs = restored["fulltextRefs"]
            seen_paths = restored["seenPaths"]
            seen_fulltexts = restored["seenFulltexts"]
            seen_context = restored["seenContext"]
            tool_evidence = restored["toolEvidence"]
            if restored["lastRoundNoVisibleIncrement"]:
                return self._b78_pending("必要补查没有带来新的可见资料，保留资料缺口。")
            first_packet = restored["firstPacket"]
            if isinstance(first_packet, Mapping):
                first_scope = first_packet.get("companyScope")
                company_scope = dict(first_scope) if isinstance(first_scope, Mapping) else company_scope
                initial_context = {key: first_packet.get(key, fallback) for key, fallback in (
                    ("marketContext", {}), ("historicalCases", []), ("historicalCoverage", {}),
                )}
            packet = self._b78_packet(claims=tuple(claims.values()), questions=tuple(questions.values()),
                                      query_paths=tuple(paths.values()), evidence_updates=evidence_updates,
                                      fulltext_requests=tuple(fulltexts.values()), context_results=context_results,
                                      fulltext_refs=fulltext_refs, company_scope=company_scope,
                                      comparison_context=initial_context)
            claims.update({claim.claim_id: claim for claim in _b78_visible_shared_claims(packet)
                           if claim.claim_id not in claims})
            result = restored["lastResult"] if self.snapshot.research_status != "continue_research" else None
        while result is None:
            self._external_guard()
            # Keep the exact program-built packet available to the outer
            # error boundary.  `run_research_round` can raise after a paid
            # checkpoint has completed but before a usable result exists.
            self._b78_active_packet = packet
            result = run_research_round(self.model, snapshot=self.snapshot, evidence_packet=packet)
            result = _b78_reconcile_result_claims(result=result, evidence_packet=packet)
            result = _b78_filter_pool_result(result=result, evidence_packet=packet)
            round_context_results: list[Mapping[str, Any]] = []
            round_tool_evidence: list[Mapping[str, Any]] = []
            if result.context_requests:
                reads = self._b78_read_context(result.context_requests, packet=packet,
                                                claims=tuple(claims.values()),
                                                questions=tuple(questions.values()),
                                                prior=context_results, seen=seen_context)
                round_context_results.extend(reads)
                if not reads:
                    reason = "所需本地资料没有形成新的可见证据，保留资料缺口。"
                    self._b78_append(packet=packet, result=self._b78_terminal_pending_result(result, reason),
                                     context_results=(), tool_evidence=round_tool_evidence,
                                     research_status="pending_verification")
                    return self._b78_pending(reason)
                self._b78_append(packet=packet, result=result, context_results=round_context_results,
                                 tool_evidence=round_tool_evidence, research_status="continue_research")
                context_results.extend(reads)
            elif result.query_paths or result.fulltext_requests:
                conclusion = result.conclusion or {}
                if conclusion.get("researchStatus") != "continue_research":
                    raise InvestigationError("补查结果必须保持研究中状态", code="investigation_round_followup_status_invalid")
                claims.update({item.claim_id: item for item in result.claims})
                questions.update({item.question_id: item for item in result.questions})
                result = self._b78_prune_optional_query_paths(result, claims=claims, questions=questions)
                evidence_updates.extend(result.evidence_updates)
                context_count = len(context_results)
                changed = self._b78_followups(result=result, packet=packet, claims=tuple(claims.values()),
                                               questions=questions, paths=paths, fulltexts=fulltexts,
                                               context_results=context_results, fulltext_refs=fulltext_refs,
                                               seen_paths=seen_paths, seen_fulltexts=seen_fulltexts,
                                               seen_context=seen_context, tool_evidence=round_tool_evidence)
                newly_read = context_results[context_count:]
                if not changed:
                    reason = "必要补查没有带来新的可见资料，保留资料缺口。"
                    self._b78_append(packet=packet, result=self._b78_terminal_pending_result(result, reason),
                                     context_results=newly_read, tool_evidence=round_tool_evidence,
                                     research_status="pending_verification")
                    tool_evidence.extend(round_tool_evidence)
                    return self._b78_pending(reason)
                # _b78_followups can add a local safe read after an Extract.
                # Persist only that receipt's delta, not prior round reads.
                self._b78_append(packet=packet, result=result, context_results=newly_read,
                                 tool_evidence=round_tool_evidence, research_status="continue_research")
                tool_evidence.extend(round_tool_evidence)
            else:
                conclusion = result.conclusion or {}
                research_status = conclusion.get("researchStatus")
                if not isinstance(research_status, str):
                    raise InvestigationError("研究轮次缺少结论", code="investigation_conclusion_missing")
                if research_status == "continue_research":
                    reason = "研究未声明可执行补查，保留资料缺口。"
                    self._b78_append(packet=packet, result=self._b78_terminal_pending_result(result, reason),
                                     context_results=(), tool_evidence=(), research_status="pending_verification")
                    return self._b78_pending(reason)
                self._b78_append(packet=packet, result=result, context_results=(), tool_evidence=(),
                                 research_status=research_status)
                break
            packet = self._b78_packet(claims=tuple(claims.values()), questions=tuple(questions.values()),
                                      query_paths=tuple(paths.values()), evidence_updates=evidence_updates,
                                      fulltext_requests=tuple(fulltexts.values()), context_results=context_results,
                                      fulltext_refs=fulltext_refs, company_scope=company_scope,
                                      comparison_context=initial_context)
            claims.update({claim.claim_id: claim for claim in _b78_visible_shared_claims(packet)
                           if claim.claim_id not in claims})
            result = None
        if result is None:
            raise InvestigationError("研究轮次结果丢失", code="investigation_round_state_invalid")
        conclusion = dict(result.conclusion or {})
        mappings = self._mappings(conclusion)
        rows = tuple(result.company_assessments)
        comparison = dict(result.comparison or {})
        context: Mapping[str, Any] = {}
        if mappings:
            if not rows or not comparison:
                raise InvestigationError("研究轮次缺少公司比较", code="investigation_comparison_missing")
            context = self._b78_comparison_context(mappings)
            comparison = {**comparison, **apply_historical_assessments(context={
                "historicalCases": context["historicalCases"],
                "historicalCoverage": context["historicalCoverage"],
            }, assessments=comparison.get("historicalAssessments", []))}
        elif conclusion.get("researchStatus") == "ready_for_comparison":
            conclusion["researchStatus"] = "background_only"
        return self._outcome(conclusion, rows, mappings, context, comparison)

    def run(self) -> InvestigationOutcome:
        return self._run_b78()


def research_outcome(*, model: Any, verifier: Any, task_id: str, event: EventDraft,
                     documents: Mapping[EvidenceRef, DiscoveryDocument], execution_profile: Mapping[str, Any],
                     cutoff_at: datetime, db_path: Path, created_at: datetime,
                     leaseguard: Callable[[], None] | None = None, cutoff_inclusive: bool = False,
                     claim_cache: Any = None, snapshot_created: Callable[[str], None] | None = None,
                     clock: Callable[[], datetime] | None = None,
                     allow_failed_resume: bool = False,
                     runtime_contract: Mapping[str, Any] | None = None) -> InvestigationOutcome:
    runtime = _Investigation(model=model, verifier=verifier, task_id=task_id, event=event, documents=documents,
        execution_profile=execution_profile, cutoff_at=cutoff_at, db_path=db_path, created_at=created_at,
        leaseguard=leaseguard, cutoff_inclusive=cutoff_inclusive, snapshot_created=snapshot_created, clock=clock or _now,
        allow_failed_resume=allow_failed_resume, runtime_contract=runtime_contract)
    try:
        return runtime.run()
    except DiscoverySliceYield:
        raise
    except Exception as exc:
        # A SQLite/ledger failure can leave a model checkpoint in-flight, so
        # it must remain a task-wide stop rather than an event-local gap.
        # The log deliberately contains only exception classification, never
        # request, provider, response, or source content.
        if isinstance(exc, sqlite3.Error):
            code = "research_storage_unavailable"
            logging.getLogger(__name__).warning(
                "k10_research_storage_error type=%s sqlite_errorcode=%r sqlite_errorname=%r",
                type(exc).__name__, getattr(exc, "sqlite_errorcode", None),
                getattr(exc, "sqlite_errorname", None),
            )
        else:
            code = getattr(exc, "code", None)
            if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_]{3,80}", code):
                code = "investigation_contract_invalid" if isinstance(exc, (ValueError, KeyError, TypeError)) else "investigation_execution_failed"
        if code == "research_storage_unavailable":
            raise InvestigationError("研究账本无法持久化", code=code) from exc
        packet = getattr(runtime, "_b78_active_packet", None)
        if (getattr(runtime, "b78_research", False) and isinstance(packet, Mapping)
                and runtime.snapshot.execution_status != "failed"):
            try:
                runtime._b78_mark_failed(packet=packet, safe_error_code=code)
            except (sqlite3.Error, store.K10Conflict) as persist_exc:
                logging.getLogger(__name__).warning(
                    "k10_research_failure_disposition_error type=%s", type(persist_exc).__name__)
                raise InvestigationError("研究失败状态无法持久化", code="research_storage_unavailable") from persist_exc
        raise


__all__ = [
    "build_research_round_packet", "research_outcome", "research_round_request_spec",
    "run_research_round", "validate_research_round_result",
]
