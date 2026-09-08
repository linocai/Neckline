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
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from . import store
from .discovery import (
    CandidateComparison, CompanyMappingDraft, DiscoveryDocument, DiscoverySliceYield,
    DiscoveryDeadlineExceeded, EvidenceRef, EventComparison, EventDraft, InvestigationOutcome,
    SqliteDiscoveryWriter, Verification, reject_uncalibrated_prediction,
)
from .historical_cases import apply_historical_assessments
from .investigation import InvestigationError, advance_research, decode_stage_result, query_path_signature
from .opportunity_discovery import validate_event_comparison
from .research_contracts import Claim, FullTextRequest, Question, QueryPath, ResearchSnapshot, ResearchStageResult, validate_company_mapping
from .research_store import (create_research_snapshot, advance_research_snapshot,
                             read_research_state, load_prior_research_evidence)


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


def _path_identity(path: QueryPath) -> str:
    # Query wording and freshly invented path IDs cannot turn a retry of the
    # same question/source/intent into a new route. The model must describe an
    # actually different source path, not merely synonyms for the same query.
    normalize = lambda value: re.sub(r"[\W_]+", "", value.casefold())
    return _hash([path.question_id, normalize(path.intent), normalize(path.target_source)])


class _Investigation:
    def __init__(self, *, model: Any, verifier: Any, task_id: str, event: EventDraft,
                 documents: Mapping[EvidenceRef, DiscoveryDocument], execution_profile: Mapping[str, Any],
                 cutoff_at: datetime, db_path: Path, created_at: datetime,
                 leaseguard: Callable[[], None] | None, cutoff_inclusive: bool,
                 snapshot_created: Callable[[str], None] | None, clock: Callable[[], datetime],
                 allow_failed_resume: bool = False) -> None:
        self.model, self.verifier, self.event, self.db_path = model, verifier, event, db_path
        self.task_id, self.guard, self.clock = task_id, leaseguard, clock
        self.allow_failed_resume = allow_failed_resume
        self.pending_model_action: dict[str, Any] | None = None
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
                _hash({"model": self.policy["model"], "options": self.policy["modelOptions"]["investigation"]}),
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

    def _refresh(self) -> None:
        self.state = read_research_state(snapshot_id=self.identity, db_path=self.db_path)
        if self.state is None:
            raise InvestigationError("调查快照丢失", code="investigation_snapshot_missing")

    def _restore_sources(self) -> None:
        refs: set[EvidenceRef] = set()
        for stage in self.state["stageResults"]:
            conclusion = stage["result"].get("conclusion") or {}
            tool = conclusion.get("runtimeEvidence")
            if isinstance(tool, Mapping):
                refs.update(_refs(tool.get("documentRefs", [])))
                self.allowed.update(_refs(tool.get("eligibleDocumentRefs", [])))
        if refs:
            for row in store.load_document_versions(refs=[_ref(ref) for ref in refs], db_path=self.db_path):
                key = _key(row)
                self.documents[key] = DiscoveryDocument(row["documentId"], row["revision"], row.get("publishedAt"),
                    row["fetchedAt"], row.get("originalText"), row.get("excerpt"), row.get("metadata") or {})
            if not refs <= set(self.documents):
                raise InvestigationError("已保存调查来源不可读取", code="investigation_source_missing")

    def _record(self, result: ResearchStageResult, *, digest: str, research_status: str | None = None,
                execution_status: str = "ok") -> None:
        self._guard()
        stamp = max(self.clock(), datetime.fromisoformat(self.snapshot.verification_cutoff_at))
        advance_research_snapshot(snapshot_id=self.identity, expected_revision=self.snapshot.revision,
            research_status=research_status or self.snapshot.research_status, execution_status=execution_status,
            stage_result=result, input_sha256=digest, updated_at=_text(stamp), verification_cutoff_at=_text(stamp),
            db_path=self.db_path, lease_guard=self.guard)
        self._refresh()

    def _cards(self) -> list[dict[str, Any]]:
        cards = []
        claims = self.state["claims"]
        for ref in sorted(self.allowed, key=lambda item: (item.document_id, item.revision)):
            doc = self.documents.get(ref)
            if doc is None:
                raise InvestigationError("调查引用未提供文档", code="investigation_source_missing")
            metadata = doc.metadata
            # Metadata and publisher bodies are never forwarded wholesale.
            provenance = {key: metadata[key] for key in ("publisher", "media", "originStatus", "originEvidenceRef",
                "firstSeenAt", "publicationPrecision", "afterCutoff", "bodyObservedAt", "contentVersionAtCutoff",
                "sourcePublicationInherited") if key in metadata}
            statements = [{"claimId": item["claimId"], "statement": item["text"], "location": item["location"],
                           "kind": item["kind"], "verificationStatus": item["verificationStatus"]}
                          for item in claims if _key(item["sourceRef"]) == ref]
            # Original sources already have typed, attributed statements.
            # Only query-result snippets, not frozen article bodies, recur.
            excerpt = doc.excerpt if ref not in self.event.source_refs else None
            cards.append({**_ref(ref), "publishedAt": doc.published_at, "fetchedAt": doc.fetched_at,
                          "excerpt": excerpt, "sourceStatements": statements, "provenance": provenance})
        return cards

    def _packet(self) -> dict[str, Any]:
        tool_outcomes = [(item["result"].get("conclusion") or {}).get("runtimeEvidence")
                         for item in self.state["stageResults"]]
        reusable = next(((item["result"].get("conclusion") or {})["runtimePriorEvidence"]
            for item in self.state["stageResults"] if "runtimePriorEvidence" in (item["result"].get("conclusion") or {})), {})
        fulltext_leads = [ref for item in tool_outcomes if item for ref in item.get("documentRefs", [])
                         if _key(ref) not in self.allowed]
        return {"event": {key: self.context[key] for key in ("canonicalKey", "stageKey", "eventState", "headline", "eventKind")},
            "newsCutoffAt": self.snapshot.news_cutoff_at,
            "allowedEvidenceRefs": [_ref(ref) for ref in sorted(self.allowed, key=lambda item: (item.document_id, item.revision))],
            "claims": self.state["claims"], "questions": self.state["questions"], "queryPaths": self.state["paths"],
            "evidenceCards": self._cards(), "evidenceUpdates": self.state["evidenceUpdates"],
            "fulltextRequests": self.state["fulltextRequests"], "toolOutcomes": [item for item in tool_outcomes if item],
            "availableArticlePolicy": {"limits": self.policy["articleLimits"], "reserve": False},
            # A returned search hit with unknown publication time is a valid
            # fulltext lead, not yet usable evidence for a factual conclusion.
            **({"fulltextRequestRefs": fulltext_leads} if fulltext_leads else {}),
            "reusableSourceEvidence": reusable,
        }

    def _freeze_prior_evidence(self) -> None:
        if any("runtimePriorEvidence" in (item["result"].get("conclusion") or {}) for item in self.state["stageResults"]):
            return
        value = load_prior_research_evidence(task_id=self.task_id, event_id=self.snapshot.event_id,
            input_source_refs=[_ref(ref) for ref in self.event.source_refs],
            news_cutoff_at=self.snapshot.news_cutoff_at, verification_cutoff_at=self.snapshot.verification_cutoff_at,
            prompt_contract_revision=self.snapshot.prompt_contract_revision,
            model_parameters_sha256=self.snapshot.model_parameters_sha256, db_path=self.db_path)
        self._record(ResearchStageResult("close_research", conclusion={
            "researchStatus": self.snapshot.research_status, "runtimePriorEvidence": value}), digest=_hash(value))

    def _validate_result(self, action: str, result: ResearchStageResult, packet: Mapping[str, Any]) -> None:
        known_claims = {item["claimId"]: item for item in self.state["claims"]}
        known_questions = {item["questionId"]: item for item in self.state["questions"]}
        permitted = set(_refs(packet["allowedEvidenceRefs"]))
        for claim in result.claims:
            if _key(claim.source_ref) not in permitted:
                raise InvestigationError("审读改写了命题范围", code="investigation_claim_scope_invalid")
            original = known_claims.get(claim.claim_id)
            if original and any(claim.to_dict()[key] != original[key] for key in ("text", "kind", "novelty", "sourceRef", "location")):
                raise InvestigationError("审读不能改写原始命题", code="investigation_claim_scope_invalid")
            known_claims[claim.claim_id] = claim.to_dict()
        for update in result.evidence_updates:
            if update.get("claimId") not in known_claims or _key(update["sourceRef"]) not in permitted:
                raise InvestigationError("证据关联越出当前命题或来源", code="investigation_reference_invalid")
        links = [*self.state["evidenceUpdates"], *result.evidence_updates]
        for claim in result.claims:
            if claim.verification_status == "verified" and not any(
                    link.get("claimId") == claim.claim_id and link.get("relation") == "supports"
                    for link in links):
                raise InvestigationError("命题已核实却没有支持该命题的证据关联", code="investigation_support_missing")
        for question in result.questions:
            if not set(question.claim_ids) <= set(known_claims):
                raise InvestigationError("问题关联了未知命题", code="investigation_question_scope_invalid")
            if not set(_refs(list(question.known_evidence))) <= permitted:
                raise InvestigationError("问题引用了未提供证据", code="investigation_reference_invalid")
            old = known_questions.get(question.question_id)
            if old and (list(question.claim_ids) != old["claimIds"] or question.question != old["question"]):
                raise InvestigationError("同一问题 ID 不可变更含义", code="investigation_question_scope_invalid")
        requestable = permitted | set(_refs(packet.get("fulltextRequestRefs", [])))
        for request in result.fulltext_requests:
            if request.question_id not in known_questions or _key(request.source_ref) not in requestable or request.state != "requested":
                raise InvestigationError("全文申请缺少真实问题或来源", code="investigation_fulltext_scope_invalid")
            if any(old["questionId"] == request.question_id and old["sourceRef"] == request.source_ref
                   and old["state"] in {"fulfilled", "rejected"} for old in self.state["fulltextRequests"]):
                raise InvestigationError("同一问题重复申请已处理的全文", code="investigation_fulltext_duplicate")
        for path in result.query_paths:
            old = next((item for item in self.state["paths"] if item["pathId"] == path.path_id), None)
            if action == "plan_queries" and (old is not None or path.state != "planned"):
                raise InvestigationError("新查询复用了旧路径或已经执行的状态", code="investigation_path_duplicate")
            if old and any(path.to_dict()[key] != old[key] for key in
                           ("questionId", "query", "intent", "targetSource", "newPathReason",
                            "expectedInformationGain", "expectedJudgmentChange")):
                raise InvestigationError("已保存查询路径不可改写", code="investigation_path_scope_invalid")
        for row in result.company_assessments:
            disclosure = row["evidenceDisclosure"]
            origin = disclosure.get("originEvidenceRef")
            if origin is not None and _key(origin) not in permitted:
                raise InvestigationError("公司披露引用未知源头", code="investigation_reference_invalid")
            if row["role"] in {"primary", "alternative", "tied"} and any(
                    item["kind"] == "rumor" and item["verificationStatus"] == "unverified" for item in self.state["claims"]):
                # The judgment can narrow which claim affects each company in
                # future contracts; this release never drops the rumor label.
                if not disclosure["isRumor"] or disclosure["verificationStatus"] == "verified":
                    raise InvestigationError("传闻推荐漏掉未核实标记", code="investigation_rumor_disclosure_missing")
        if result.conclusion:
            mappings = result.conclusion.get("companyMappings", [])
            if not isinstance(mappings, list):
                raise InvestigationError("公司映射格式无效", code="investigation_mapping_invalid")
            for mapping in mappings:
                if not isinstance(mapping, Mapping) or not set(_refs(mapping.get("relationEvidence"))) <= permitted:
                    raise InvestigationError("公司映射引用未提供资料", code="investigation_reference_invalid")

    def _call(self, action: str, extra: Mapping[str, Any] | None = None) -> ResearchStageResult:
        self._guard()
        packet = self._packet()
        if extra:
            packet.update(extra)
        digest = _hash({"action": action, "packet": packet})
        for stage in reversed(self.state["stageResults"]):
            if stage["action"] == action and stage["inputSha256"] == digest and not stage["result"].get("safeErrorCode"):
                return decode_stage_result(stage["result"], action=action)
        self.pending_model_action = {"action": action, "extra": dict(extra or {})}
        try:
            step = advance_research(model=self.model, snapshot=self.snapshot, action=action, evidence_packet=packet)
            self._validate_result(action, step.result, packet)
        except (InvestigationError, ValueError, KeyError, TypeError) as exc:
            reject = getattr(self.model, "reject_research_result", None)
            if callable(reject):
                reject(snapshot=self.snapshot, action=action, evidence_packet=packet,
                       safe_error_code=getattr(exc, "code", "investigation_contract_invalid"))
            raise
        status = None
        if action == "close_research":
            status = step.result.conclusion["researchStatus"]
        self._record(step.result, digest=digest, research_status=status)
        self.pending_model_action = None
        return step.result

    def _tool(self, bundle: Any, *, path: QueryPath | None = None, request: FullTextRequest | None = None) -> None:
        if bundle.coverage.get("requestState") == "pending":
            raise InvestigationError("资料调用尚未完成", code=str(bundle.coverage.get("reason") or "investigation_tool_failed"))
        for doc in bundle.documents:
            self.documents[doc.evidence_ref] = doc
        self.allowed.update(doc.evidence_ref for doc in bundle.eligible_documents)
        runtime = {"documentRefs": [_ref(doc) for doc in bundle.documents],
            "eligibleDocumentRefs": [_ref(doc) for doc in bundle.eligible_documents], "coverage": dict(bundle.coverage)}
        result = ResearchStageResult("assess_evidence", conclusion={"runtimeEvidence": runtime},
            query_paths=() if path is None else (replace(path, state="searched" if bundle.eligible_documents else "no_result",
                result_summary=str(bundle.coverage.get("reason") or "已取得资料")),),
            fulltext_requests=() if request is None else (replace(request,
                state="fulfilled" if bundle.documents else "rejected", admission_ref=bundle.coverage.get("admissionRef")),))
        self._record(result, digest=_hash({"runtime": runtime, "path": path.to_dict() if path else None,
            "request": request.to_dict() if request else None}), research_status="continue_research")

    def _fulltexts(self) -> bool:
        did_work = False
        while True:
            requests = [FullTextRequest.from_dict(item) for item in self.state["fulltextRequests"] if item["state"] == "requested"]
            if not requests:
                return did_work
            request = requests[0]
            question = next((Question.from_dict(item) for item in self.state["questions"] if item["questionId"] == request.question_id), None)
            document = self.documents.get(_key(request.source_ref))
            if question is None or document is None:
                raise InvestigationError("全文申请的调查上下文丢失", code="investigation_fulltext_scope_invalid")
            self._guard()
            bundle = self.verifier.fetch_fulltext(event=self.event, document=document, question=question,
                request=request, cutoff_at=self.cutoff, cutoff_inclusive=self.cutoff_inclusive)
            self._tool(bundle, request=request)
            self._assess_due()
            did_work = True

    def _assess_due(self) -> bool:
        """Finish any durably saved tool batch before planning another query."""
        last_assessed = max((stage["revision"] for stage in self.state["stageResults"]
            if stage["action"] == "assess_evidence" and not (stage["result"].get("conclusion") or {}).get("runtimeEvidence")
            and not stage["result"].get("safeErrorCode")), default=0)
        unassessed = [stage for stage in self.state["stageResults"] if stage["revision"] > last_assessed
                     and (stage["result"].get("conclusion") or {}).get("runtimeEvidence")]
        if not unassessed:
            return False
        full_refs, already_read = set(), set()
        for stage in self.state["stageResults"]:
            tool = (stage["result"].get("conclusion") or {}).get("runtimeEvidence")
            if tool and stage["revision"] <= last_assessed and tool["coverage"].get("operation") == "extract":
                already_read.update(_refs(tool["eligibleDocumentRefs"]))
        for stage in unassessed:
            tool = stage["result"]["conclusion"]["runtimeEvidence"]
            if tool["coverage"].get("operation") == "extract" and tool["coverage"].get("admissionState") == "fulfilled":
                full_refs.update(_refs(tool["eligibleDocumentRefs"]))
        full = [self.documents[ref] for ref in sorted(full_refs - already_read, key=lambda item: (item.document_id, item.revision))]
        self._call("assess_evidence", {"admittedFulltextRefs": [_ref(doc) for doc in full],
            "fullTextDocuments": [{**_ref(doc), "text": doc.analysis_text or doc.original_text or "",
                "publishedAt": doc.published_at, "fetchedAt": doc.fetched_at,
                "contentVersionAtCutoff": doc.metadata.get("contentVersionAtCutoff")} for doc in full]})
        return True

    def _pending(self, reason: str) -> Mapping[str, Any]:
        questions = tuple(replace(Question.from_dict(item), state="blocked", resume_condition=reason)
                          for item in self.state["questions"] if item["state"] == "open")
        previous_mappings = next(((stage["result"].get("conclusion") or {})["companyMappings"]
            for stage in reversed(self.state["stageResults"])
            if (stage["result"].get("conclusion") or {}).get("companyMappings")), [])
        conclusion = {"researchStatus": "pending_verification", "stopReason": reason,
                      "resumeCondition": "出现能回答上述缺口的新来源、原始材料或可执行路径后重新评估。",
                      "companyMappings": previous_mappings}
        self._record(ResearchStageResult("close_research", questions=questions, conclusion=conclusion),
            digest=_hash(conclusion), research_status="pending_verification")
        return conclusion

    def _close(self) -> ResearchStageResult:
        # A closure may discover a specific missing paragraph. Complete that
        # admitted request and assess it before accepting the closure itself.
        while True:
            result = self._call("close_research")
            if not self._fulltexts():
                return result

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

    def _comparison_context(self, mappings: Sequence[CompanyMappingDraft]) -> Mapping[str, Any]:
        for stage in self.state["stageResults"]:
            saved = (stage["result"].get("conclusion") or {}).get("runtimeComparisonContext")
            if saved is not None:
                if saved["companyCodes"] != [item.company_code for item in mappings]:
                    raise InvestigationError("恢复时公司比较范围改变", code="investigation_comparison_context_mismatch")
                return saved["context"]
        loader = getattr(self.model, "research_comparison_context", None)
        if not callable(loader):
            raise InvestigationError("缺少公司市场和历史上下文", code="investigation_comparison_context_missing")
        context = loader(event=self.event, mappings=mappings)
        if not isinstance(context, Mapping) or any(key not in context for key in ("marketContext", "historicalCases", "historicalCoverage")):
            raise InvestigationError("公司市场和历史上下文无效", code="investigation_comparison_context_invalid")
        saved = {"companyCodes": [item.company_code for item in mappings], "context": dict(context)}
        self._record(ResearchStageResult("close_research", conclusion={"researchStatus": self.snapshot.research_status,
            "runtimeComparisonContext": saved}), digest=_hash(saved))
        return context

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
        verified = "verified" if statuses == {"verified"} else "needs_review"
        return InvestigationOutcome(Verification(verified, common, event_refs,
            {"state": "available", "researchSnapshotId": self.identity, "researchRevision": self.snapshot.revision,
             "researchStatus": self.snapshot.research_status, "verificationCutoffAt": self.snapshot.verification_cutoff_at},
            tuple(self.documents[ref] for ref in self.allowed if ref not in self.event.source_refs)),
            tuple(mappings), EventComparison(common, candidates, event_refs), self.identity)

    def run(self) -> InvestigationOutcome:
        retry = None
        if self.snapshot.execution_status == "failed" and not self.allow_failed_resume:
            raise InvestigationError("调查执行失败，等待任务恢复", code="investigation_previously_failed")
        if self.snapshot.execution_status == "failed":
            retry = next(((item["result"].get("conclusion") or {}).get("runtimeFailedAction")
                for item in reversed(self.state["stageResults"]) if item["result"].get("safeErrorCode")), None)
        if self.snapshot.execution_status in {"paused", "failed"}:
            self._record(ResearchStageResult("close_research", conclusion={"runtimeResume": True,
                         "researchStatus": self.snapshot.research_status}),
                         digest=_hash({"resume": self.snapshot.revision}), execution_status="ok")
        final_records = [item["result"].get("conclusion") for item in self.state["stageResults"]
                         if isinstance(item["result"].get("conclusion"), Mapping)
                         and item["result"]["conclusion"].get("runtimeFinal")]
        if final_records:
            final = final_records[-1]["runtimeFinal"]
            return self._outcome(final["conclusion"], self.state["assessments"], self._mappings(final["conclusion"]),
                                 final["comparisonContext"], final["comparison"])
        self._freeze_prior_evidence()
        if retry:
            self._call(retry["action"], retry.get("extra"))
            if retry["action"] == "assess_evidence":
                self._fulltexts()
                self._close()
        # Even an empty claim array is a completed article extraction. Missing
        # arrays must fail, not prompt a second whole-article reading.
        if not any(stage["action"] == "extract_claims" for stage in self.state["stageResults"]):
            raw = self.event.facts.get("researchClaims")
            if not isinstance(raw, list):
                raise InvestigationError("已读原文缺少命题提取结果", code="investigation_claims_missing")
            claims = tuple(Claim.from_dict(item) for item in raw)
            if any(_key(item.source_ref) not in self.allowed for item in claims):
                raise InvestigationError("原文命题引用错误", code="investigation_reference_invalid")
            self._record(ResearchStageResult("extract_claims", claims=claims), digest=_hash(raw))
        if not self.state["claims"]:
            conclusion = {"researchStatus": "background_only", "stopReason": "本事件没有影响判断的独立命题。", "companyMappings": []}
            self._record(ResearchStageResult("close_research", conclusion=conclusion), digest=_hash(conclusion), research_status="background_only")
        else:
            if not any(stage["action"] == "plan_gaps" for stage in self.state["stageResults"]):
                self._call("plan_gaps")
            while True:
                if self.snapshot.research_status != "continue_research":
                    saved_close = next(((stage["result"].get("conclusion") or {})
                        for stage in reversed(self.state["stageResults"])
                        if stage["action"] == "close_research"
                        and "companyMappings" in (stage["result"].get("conclusion") or {})
                        and (stage["result"].get("conclusion") or {}).get("researchStatus") == self.snapshot.research_status), None)
                    if saved_close is not None:
                        conclusion = saved_close
                        break
                self._guard()
                if self._assess_due():
                    self._fulltexts()
                    closed = self._close()
                    if closed.conclusion["researchStatus"] != "continue_research":
                        conclusion = closed.conclusion
                        break
                open_questions = [item for item in self.state["questions"] if item["state"] == "open"]
                if open_questions:
                    pending_paths = [QueryPath.from_dict(item) for item in self.state["paths"] if item["state"] == "planned"]
                    if not pending_paths:
                        planned = self._call("plan_queries", {"openQuestionIds": [item["questionId"] for item in open_questions],
                            "attemptedPathSignatures": [query_path_signature(QueryPath.from_dict(item))
                                for item in self.state["paths"] if item["state"] != "planned"]})
                        pending_paths = list(planned.query_paths)
                        if not pending_paths:
                            conclusion = self._pending("当前相关查证路径已用尽，尚有影响判断的缺口。")
                            break
                    used = {_path_identity(QueryPath.from_dict(item)) for item in self.state["paths"] if item["state"] != "planned"}
                    for path in pending_paths:
                        if _path_identity(path) in used:
                            raise InvestigationError("续查只是重复旧路径", code="investigation_path_duplicate")
                        question = next((Question.from_dict(item) for item in self.state["questions"] if item["questionId"] == path.question_id), None)
                        if question is None or question.state != "open":
                            raise InvestigationError("查询没有关联开放问题", code="investigation_path_question_invalid")
                        self._guard()
                        bundle = self.verifier.fetch(event=self.event, retrieved_at=self.clock(), cutoff_at=self.cutoff,
                            cutoff_inclusive=self.cutoff_inclusive, question=question, query_path=path)
                        self._tool(bundle, path=path)
                        used.add(_path_identity(path))
                    self._assess_due()
                self._fulltexts()
                closed = self._close()
                conclusion = closed.conclusion
                if conclusion["researchStatus"] != "continue_research":
                    break
                if not any(item["state"] == "open" for item in self.state["questions"]):
                    conclusion = self._pending("要求继续调查但没有可改变判断的开放问题。")
                    break
        mappings = self._mappings(conclusion)
        context: Mapping[str, Any] = {}
        comparison: Mapping[str, Any] = {}
        rows: Sequence[Mapping[str, Any]] = ()
        if mappings:
            context = self._comparison_context(mappings)
            compared = self._call("compare_companies", {**dict(context), "companyCodes": [item.company_code for item in mappings],
                "conclusion": conclusion, "publicationAllowed": conclusion["researchStatus"] == "ready_for_comparison"})
            rows = compared.company_assessments
            if conclusion["researchStatus"] != "ready_for_comparison" and any(item["role"] in {"primary", "alternative", "tied"} for item in rows):
                raise InvestigationError("调查收口待核却强行给出主推", code="investigation_unresolved_ranking")
            comparison = compared.conclusion or {}
            context = {**dict(context), **apply_historical_assessments(context={
                "historicalCases": context["historicalCases"], "historicalCoverage": context["historicalCoverage"]},
                assessments=comparison.get("historicalAssessments", []))}
            # Validate the full result before persisting comparison_complete.
            self._outcome(conclusion, rows, mappings, context, comparison)
        elif conclusion["researchStatus"] == "ready_for_comparison":
            conclusion = {**dict(conclusion), "researchStatus": "background_only"}
        terminal = "comparison_complete" if mappings and conclusion["researchStatus"] == "ready_for_comparison" else conclusion["researchStatus"]
        self._record(ResearchStageResult("close_research", conclusion={"researchStatus": terminal, "runtimeFinal": {
            "conclusion": dict(conclusion), "comparisonContext": dict(context), "comparison": dict(comparison)}}),
            digest=_hash({"final": conclusion, "comparison": comparison}), research_status=terminal)
        return self._outcome(conclusion, rows, mappings, context, comparison)


def research_outcome(*, model: Any, verifier: Any, task_id: str, event: EventDraft,
                     documents: Mapping[EvidenceRef, DiscoveryDocument], execution_profile: Mapping[str, Any],
                     cutoff_at: datetime, db_path: Path, created_at: datetime,
                     leaseguard: Callable[[], None] | None = None, cutoff_inclusive: bool = False,
                     claim_cache: Any = None, snapshot_created: Callable[[str], None] | None = None,
                     clock: Callable[[], datetime] | None = None,
                     allow_failed_resume: bool = False) -> InvestigationOutcome:
    runtime = _Investigation(model=model, verifier=verifier, task_id=task_id, event=event, documents=documents,
        execution_profile=execution_profile, cutoff_at=cutoff_at, db_path=db_path, created_at=created_at,
        leaseguard=leaseguard, cutoff_inclusive=cutoff_inclusive, snapshot_created=snapshot_created, clock=clock or _now,
        allow_failed_resume=allow_failed_resume)
    try:
        return runtime.run()
    except DiscoverySliceYield:
        raise
    except Exception as exc:
        code = getattr(exc, "code", None)
        if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_]{3,80}", code):
            code = "investigation_contract_invalid" if isinstance(exc, (ValueError, KeyError, TypeError)) else "investigation_execution_failed"
        if runtime.snapshot.execution_status != "failed":
            runtime._record(ResearchStageResult("close_research", safe_error_code=code,
                conclusion={"runtimeFailedAction": runtime.pending_model_action}),
                digest=_hash({"failure": code, "revision": runtime.snapshot.revision}), execution_status="failed")
        raise


__all__ = ["research_outcome"]
