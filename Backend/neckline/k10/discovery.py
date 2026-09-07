"""K10 晚间发现链：全市场资料 → 理解 → 核验 → 公司映射 → 比较。

没有来源、模型路由或预算配置时本模块只返回 ``not_configured``。所有模型和核验能力
均由调用方显式注入；没有真实来源账户时，fake provider 可在临时库完整验证流程。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from .config import ConfigurationStatus, validate_run_config
from .types import EventRevision
from .universe import CompanyMetadataProvider, Eligibility, evaluate_company
from .opportunity_discovery import NEW_KINDS, validate_classification, validate_comparison


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{sha256(chr(31).join(parts).encode('utf-8')).hexdigest()[:32]}"


@dataclass(frozen=True)
class EvidenceRef:
    document_id: str
    revision: int


@dataclass(frozen=True)
class DiscoveryDocument:
    document_id: str
    revision: int
    published_at: Optional[str]
    fetched_at: str
    original_text: Optional[str]
    excerpt: Optional[str]
    metadata: Mapping[str, Any]

    @property
    def evidence_ref(self) -> EvidenceRef:
        return EvidenceRef(self.document_id, self.revision)


@dataclass(frozen=True)
class EventDraft:
    canonical_key: str
    stage_key: str
    event_state: str
    headline: str
    event_kind: str
    facts: Mapping[str, Any]
    source_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if not self.canonical_key or not self.stage_key or not self.event_state:
            raise ValueError("事件必须有 canonical_key、stage_key 与 event_state")
        if not self.source_refs:
            raise ValueError("事件必须引用原始资料")


@dataclass(frozen=True)
class Verification:
    state: str
    summary: str
    evidence_refs: tuple[EvidenceRef, ...]
    coverage: Mapping[str, Any] = field(default_factory=dict)
    documents: tuple[DiscoveryDocument, ...] = ()


@dataclass(frozen=True)
class EventVerification:
    event: EventDraft
    verification: Verification


@dataclass(frozen=True)
class CompanyMappingDraft:
    company_code: str
    affected_stage: str
    relation_evidence: tuple[EvidenceRef, ...]
    inference: Mapping[str, Any]
    uncertainty: str

    def __post_init__(self) -> None:
        if not self.company_code or not self.affected_stage or not self.relation_evidence:
            raise ValueError("公司映射必须有代码、受影响环节和关系证据")


@dataclass(frozen=True)
class CandidateComparison:
    summary: str
    differences: Mapping[str, Any]
    evidence_refs: tuple[EvidenceRef, ...]
    # The model's editorial ordering is persisted for presentation.  It is deliberately
    # not a score, probability, or a replacement for the explicit hard exclusions.
    rank: int | None = None
    market_context: Mapping[str, Any] | None = None


class DiscoveryModel(Protocol):
    """轻量模型职责；不生成概率，也不承担完整正反辩论。"""

    def understand(self, *, document: DiscoveryDocument) -> Sequence[EventDraft]:
        ...

    def map_companies(
        self, *, event: EventDraft, verification: Verification
    ) -> Sequence[CompanyMappingDraft]:
        ...

    def compare(
        self, *, event: EventDraft, verification: Verification, mapping: CompanyMappingDraft,
        peers: Sequence[CompanyMappingDraft],
    ) -> CandidateComparison:
        ...

    def prioritize(self, *, candidates: Sequence["DiscoveryCandidate"]) -> Sequence[tuple[str, str]]:
        """Return an evidence-grounded editorial order of ``(canonical_key, company_code)``.

        This is not a score or probability.  The returned list must cover each distinct company
        once, so no one company can consume several of the 30 evening places.
        """
        ...


VerificationFunction = Callable[[EventDraft], Verification]


@dataclass(frozen=True)
class DiscoveryCandidate:
    event: EventDraft
    verification: Verification
    mapping: CompanyMappingDraft
    comparison: CandidateComparison
    eligibility: Eligibility
    opportunity: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveryRun:
    state: str
    configuration: ConfigurationStatus
    events: tuple[EventDraft, ...]
    verifications: tuple[EventVerification, ...]
    candidates: tuple[DiscoveryCandidate, ...]
    deferred: tuple[DiscoveryCandidate, ...]
    metadata_pending: tuple[DiscoveryCandidate, ...]
    excluded: tuple[DiscoveryCandidate, ...]
    deferred_count: int
    updates: tuple[DiscoveryCandidate, ...] = ()
    background: tuple[DiscoveryCandidate, ...] = ()


class DiscoveryWriter(Protocol):
    def append_event(self, *, event: EventDraft, verification: Verification) -> EventRevision:
        ...

    def append_mapping(
        self, *, event: EventRevision, mapping: CompanyMappingDraft
    ) -> str:
        ...

    def append_candidate(
        self, *, event: EventRevision, mapping_id: str, candidate: DiscoveryCandidate
    ) -> None:
        ...


def _validate_refs(refs: Sequence[EvidenceRef], available: set[EvidenceRef], *, label: str) -> None:
    unknown = [f"{ref.document_id}@{ref.revision}" for ref in refs if ref not in available]
    if unknown:
        raise ValueError(f"{label} 引用了未输入的原始资料：{','.join(unknown)}")


def _reject_probability(value: Mapping[str, Any]) -> None:
    forbidden = [key for key in value if "probab" in key.lower() or "概率" in key]
    if forbidden:
        raise ValueError(f"K10 候选比较不得输出未校准概率：{','.join(forbidden)}")


def run_discovery(
    *, documents: Sequence[DiscoveryDocument], configuration: Mapping[str, Any] | None,
    model: DiscoveryModel, verify: VerificationFunction, metadata: CompanyMetadataProvider,
    cutoff_at: datetime, phase: str = "evening", max_evening_candidates: int = 30,
    leaseguard: Callable[[], None] | None = None,
    previous_opportunities: Sequence[Mapping[str, Any]] = (),
) -> DiscoveryRun:
    """执行可注入发现链；仅晚间可生成最多 30 条新候选。"""
    if cutoff_at.tzinfo is None:
        raise ValueError("cutoff_at 必须带时区")
    if phase not in {"evening", "morning"}:
        raise ValueError("K10 discovery phase 必须是 evening 或 morning")
    if max_evening_candidates != 30:
        raise ValueError("K10 晚间候选上限固定为 30，不接受运行时策略默认或改写")
    config = validate_run_config(configuration, scope="discovery")
    if not config.ready:
        return DiscoveryRun("not_configured", config, (), (), (), (), (), (), 0)
    available = {document.evidence_ref for document in documents}
    events: list[EventDraft] = []
    verified_events: list[EventVerification] = []
    all_candidates: list[DiscoveryCandidate] = []
    pending: list[DiscoveryCandidate] = []
    excluded: list[DiscoveryCandidate] = []
    updates: list[DiscoveryCandidate] = []
    background: list[DiscoveryCandidate] = []
    for document in documents:
        if leaseguard is not None:
            leaseguard()
        for event in model.understand(document=document):
            _validate_refs(event.source_refs, available, label="事件")
            if leaseguard is not None:
                leaseguard()
            verification = verify(event)
            available.update(document.evidence_ref for document in verification.documents)
            _validate_refs(verification.evidence_refs, available, label="重点核验")
            events.append(event)  # stage / denial always survive as an event revision input.
            verified_events.append(EventVerification(event, verification))
            if leaseguard is not None:
                leaseguard()
            mappings = tuple(model.map_companies(event=event, verification=verification))
            for mapping in mappings:
                _validate_refs(mapping.relation_evidence, available, label="公司映射")
                if leaseguard is not None:
                    leaseguard()
                comparison = model.compare(event=event, verification=verification, mapping=mapping, peers=mappings)
                _validate_refs(comparison.evidence_refs, available, label="候选比较")
                _reject_probability(comparison.differences)
                validate_comparison(comparison.differences)
                status = evaluate_company(metadata.lookup(company_code=mapping.company_code, as_of=cutoff_at))
                classifier = getattr(model, "classify_opportunity", None)
                if not callable(classifier):
                    raise ValueError("发现模型缺少机会延续/新催化分类")
                prior = tuple(old for old in previous_opportunities if old.get("companyCode") == mapping.company_code)
                decision = validate_classification(
                    classifier(event=event, verification=verification, mapping=mapping,
                               comparison=comparison, previous=prior),
                    canonical_key=event.canonical_key, stage_key=event.stage_key,
                    company_code=mapping.company_code, previous=prior,
                )
                if decision["kind"] == "invalidated" and verification.state not in {"verified", "contradicted"}:
                    decision = {**decision, "kind": "needs_review", "reason": "重大反证尚待核实。" + decision["reason"]}
                candidate = DiscoveryCandidate(event, verification, mapping, comparison, status, decision)
                if decision["kind"] == "background":
                    background.append(candidate)
                    continue
                if decision["kind"] == "needs_review" and decision.get("relatedOpportunityId") is None:
                    # First-seen unresolved evidence is retained as a pending mapping/event,
                    # never a formal candidate.  A hard universe exclusion remains an exclusion
                    # even when its event's evidence also needs review.
                    if status.eligible or status.state == "insufficient_metadata":
                        pending.append(candidate)
                    else:
                        excluded.append(candidate)
                    continue
                if decision["kind"] not in NEW_KINDS:
                    updates.append(candidate)
                    continue
                if status.eligible:
                    all_candidates.append(candidate)
                elif status.state == "insufficient_metadata":
                    pending.append(candidate)
                else:
                    excluded.append(candidate)
    # Rank companies once, while retaining every formally recommended catalyst for each
    # admitted company. Continuations have already been separated from the new-company quota.
    choices = getattr(model, "prioritize", None)
    if not callable(choices):
        raise ValueError("发现模型缺少跨事件公司比较")
    if leaseguard is not None:
        leaseguard()
    ordered_keys = tuple(choices(candidates=tuple(all_candidates)))
    unique_by_company: dict[str, list[DiscoveryCandidate]] = {}
    for candidate in all_candidates:
        unique_by_company.setdefault(candidate.mapping.company_code, []).append(candidate)
    expected = {(item.event.canonical_key, company) for company, items in unique_by_company.items() for item in items}
    selected_by_key: dict[tuple[str, str], DiscoveryCandidate] = {
        (item.event.canonical_key, item.mapping.company_code): item for item in all_candidates
    }
    selected_companies: set[str] = set()
    ordered: list[DiscoveryCandidate] = []
    for key in ordered_keys:
        if not isinstance(key, tuple) or len(key) != 2 or key not in expected:
            raise ValueError("跨事件公司比较返回了未知候选")
        candidate = selected_by_key[key]
        company = candidate.mapping.company_code
        if company in selected_companies:
            raise ValueError("跨事件公司比较为同一公司返回多次")
        selected_companies.add(company)
        ordered.append(candidate)
    if selected_companies != set(unique_by_company):
        raise ValueError("跨事件公司比较必须覆盖每个有证据公司")
    # Morning offers every distinct new company for the user to decide on; no old candidate is
    # overwritten.  Evening keeps the fixed maximum of 30 distinct companies.
    selected: list[DiscoveryCandidate] = []
    deferred: list[DiscoveryCandidate] = []
    for index, lead in enumerate(ordered, start=1):
        peers = unique_by_company[lead.mapping.company_code]
        seen_keys: set[str] = set()
        for candidate in (lead, *(item for item in peers if item is not lead)):
            key = str(candidate.opportunity["opportunityKey"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            ranked = replace(candidate, comparison=replace(candidate.comparison, rank=index))
            (deferred if phase == "evening" and index > max_evening_candidates else selected).append(ranked)
    return DiscoveryRun("completed", config, tuple(events), tuple(verified_events), tuple(selected), tuple(deferred),
                        tuple(pending), tuple(excluded), len({item.mapping.company_code for item in deferred}), tuple(updates), tuple(background))


class SqliteDiscoveryWriter:
    """将已验证发现结果追加到现有 K10 store；不调用来源或模型。"""

    def __init__(self, *, scan_id: str, db_path: Path, created_at: str) -> None:
        self._scan_id = scan_id
        self._db_path = db_path
        self._created_at = created_at
        self.publication_inputs: list[Any] = []
        self.update_inputs: list[tuple[EventRevision, DiscoveryCandidate]] = []

    @staticmethod
    def _refs(refs: Sequence[EvidenceRef]) -> list[dict[str, Any]]:
        return [{"documentId": ref.document_id, "revision": ref.revision} for ref in refs]

    def append_event(self, *, event: EventDraft, verification: Verification) -> EventRevision:
        from .store import append_event_revision, latest_event_revision

        event_id = _stable_id("event", event.canonical_key)
        prior = latest_event_revision(event_id=event_id, db_path=self._db_path)
        facts = {**event.facts, "stageKey": event.stage_key, "eventState": event.event_state,
                 "verification": {"state": verification.state, "summary": verification.summary,
                                  "evidenceRefs": self._refs(verification.evidence_refs), "coverage": dict(verification.coverage)}}
        return append_event_revision(
            event_id=event_id, stable_key=event.canonical_key, headline=event.headline,
            event_kind=event.event_kind, facts=facts, source_refs=self._refs(event.source_refs),
            supersedes_revision=None if prior is None else prior.revision, created_at=self._created_at,
            db_path=self._db_path,
        )

    def append_mapping(self, *, event: EventRevision, mapping: CompanyMappingDraft) -> str:
        from .store import append_company_mapping

        identity = _stable_id("mapping", event.event_id, str(event.revision), mapping.company_code,
                              mapping.affected_stage, json.dumps(self._refs(mapping.relation_evidence), sort_keys=True))
        append_company_mapping(
            mapping_id=identity, event_id=event.event_id, event_revision=event.revision,
            company_code=mapping.company_code, affected_stage=mapping.affected_stage,
            relation_evidence=self._refs(mapping.relation_evidence), inference=mapping.inference,
            uncertainty=mapping.uncertainty, created_at=self._created_at, db_path=self._db_path,
        )
        return identity

    def append_candidate(self, *, event: EventRevision, mapping_id: str, candidate: DiscoveryCandidate) -> None:
        from .store import create_candidate
        from .types import OpportunityPublicationInput

        identity = _stable_id("candidate", self._scan_id, event.event_id, str(event.revision),
                              candidate.mapping.company_code)
        comparison = {"summary": candidate.comparison.summary, "differences": candidate.comparison.differences,
                      "evidenceRefs": self._refs(candidate.comparison.evidence_refs), "rank": candidate.comparison.rank,
                      "classification": dict(candidate.opportunity)}
        if candidate.comparison.market_context is not None:
            comparison["marketContext"] = dict(candidate.comparison.market_context)
        create_candidate(
            candidate_id=identity, scan_id=self._scan_id, event_id=event.event_id, event_revision=event.revision,
            company_code=candidate.mapping.company_code, comparison=comparison,
            evidence=[{"mappingId": mapping_id}], created_at=self._created_at, db_path=self._db_path,
        )
        self.publication_inputs.append(OpportunityPublicationInput(
            candidate_id=identity, company_code=candidate.mapping.company_code,
            event_id=event.event_id, event_revision=event.revision,
            opportunity_key=str(candidate.opportunity["opportunityKey"]),
            catalyst_stage=candidate.event.stage_key,
            category=str(candidate.comparison.differences["role"]),
            comparison=comparison, evidence_refs=tuple(self._refs(candidate.comparison.evidence_refs)),
            source_marker="new", related_opportunity_id=candidate.opportunity.get("relatedOpportunityId"),
        ))

    def append_update(self, *, event: EventRevision, candidate: DiscoveryCandidate) -> None:
        self.update_inputs.append((event, candidate))

    def publish_updates(self, *, at: str) -> None:
        from .store import append_opportunity_update
        for event, candidate in self.update_inputs:
            decision = candidate.opportunity
            related = str(decision["relatedOpportunityId"])
            kind = {"continuation": "evidence_update", "needs_review": "risk", "invalidated": "withdrawal"}[decision["kind"]]
            append_opportunity_update(
                lifecycle_event_id=_stable_id("update", self._scan_id, related, event.event_id, str(event.revision)),
                opportunity_id=related, kind=kind, reason=str(decision["reason"]),
                source_refs=self._refs(candidate.event.source_refs),
                content={"classification": dict(decision), "eventId": event.event_id, "eventRevision": event.revision,
                         "comparison": candidate.comparison.summary, "scanId": self._scan_id},
                occurred_at=at, created_at=at, db_path=self._db_path,
            )


def persist_discovery(*, run: DiscoveryRun, writer: DiscoveryWriter,
                      leaseguard: Callable[[], None] | None = None) -> None:
    """先保存事件及全部可用映射，再保存最多 30 个合格公司候选。"""
    if run.state != "completed":
        return
    revisions: dict[int, EventRevision] = {}
    verification_by_event = {id(item.event): item.verification for item in run.verifications}
    for event in run.events:
        if leaseguard is not None:
            leaseguard()
        verification = verification_by_event.get(id(event))
        if verification is None:
            # 无公司映射也必须保留事件；使用其自身资料作为核验可追溯基线。
            verification = Verification("not_applicable", "无公司映射", event.source_refs)
        revisions[id(event)] = writer.append_event(event=event, verification=verification)
    selected = {id(candidate) for candidate in run.candidates}
    for candidate in (*run.candidates, *run.deferred, *run.metadata_pending, *run.excluded, *run.updates, *run.background):
        if leaseguard is not None:
            leaseguard()
        revision = revisions[id(candidate.event)]
        mapping_id = writer.append_mapping(event=revision, mapping=candidate.mapping)
        if id(candidate) in selected:
            writer.append_candidate(event=revision, mapping_id=mapping_id, candidate=candidate)
        elif candidate in run.updates:
            writer.append_update(event=revision, candidate=candidate)


def _ref_payload(ref: EvidenceRef) -> dict[str, Any]:
    return {"documentId": ref.document_id, "revision": ref.revision}


def _refs_from_payload(value: Any) -> tuple[EvidenceRef, ...]:
    if not isinstance(value, list):
        raise ValueError("冻结发现结果的证据引用无效")
    refs: list[EvidenceRef] = []
    for item in value:
        if not isinstance(item, Mapping) or not isinstance(item.get("documentId"), str) or not isinstance(item.get("revision"), int):
            raise ValueError("冻结发现结果的证据引用无效")
        refs.append(EvidenceRef(item["documentId"], item["revision"]))
    return tuple(refs)


def freeze_discovery_run(run: DiscoveryRun) -> dict[str, Any]:
    """Serialize model output before publication so a retry never calls the model again.

    The snapshot intentionally contains only model-derived draft data and exact document
    revisions. Source material remains in the append-only source-document store.
    """
    event_index = {id(event): index for index, event in enumerate(run.events)}
    def event_payload(event: EventDraft) -> dict[str, Any]:
        return {"canonicalKey": event.canonical_key, "stageKey": event.stage_key,
                "eventState": event.event_state, "headline": event.headline,
                "eventKind": event.event_kind, "facts": dict(event.facts),
                "sourceRefs": [_ref_payload(ref) for ref in event.source_refs]}
    verification_by_event = {id(item.event): item.verification for item in run.verifications}
    def verification_payload(event: EventDraft) -> dict[str, Any]:
        verification = verification_by_event.get(id(event))
        if verification is None:
            return {"state": "not_applicable", "summary": "无公司映射",
                    "evidenceRefs": [_ref_payload(ref) for ref in event.source_refs], "coverage": {}}
        return {"state": verification.state, "summary": verification.summary,
                "evidenceRefs": [_ref_payload(ref) for ref in verification.evidence_refs],
                "coverage": dict(verification.coverage)}
    def candidate_payload(candidate: DiscoveryCandidate) -> dict[str, Any]:
        index = event_index.get(id(candidate.event))
        if index is None:
            raise ValueError("发现候选不属于本次冻结事件")
        return {"eventIndex": index, "mapping": {"companyCode": candidate.mapping.company_code,
                "affectedStage": candidate.mapping.affected_stage,
                "relationEvidence": [_ref_payload(ref) for ref in candidate.mapping.relation_evidence],
                "inference": dict(candidate.mapping.inference), "uncertainty": candidate.mapping.uncertainty},
                "comparison": {"summary": candidate.comparison.summary,
                "differences": dict(candidate.comparison.differences),
                "evidenceRefs": [_ref_payload(ref) for ref in candidate.comparison.evidence_refs],
                "rank": candidate.comparison.rank, "marketContext": candidate.comparison.market_context},
                "opportunity": dict(candidate.opportunity),
                "eligibility": {"state": candidate.eligibility.state, "reason": candidate.eligibility.reason}}
    return {"version": 2, "state": run.state, "events": [event_payload(event) for event in run.events],
            "verifications": [verification_payload(event) for event in run.events],
            "candidates": [candidate_payload(item) for item in run.candidates],
            "deferred": [candidate_payload(item) for item in run.deferred],
            "metadataPending": [candidate_payload(item) for item in run.metadata_pending],
            "excluded": [candidate_payload(item) for item in run.excluded],
            "updates": [candidate_payload(item) for item in run.updates],
            "background": [candidate_payload(item) for item in run.background], "deferredCount": run.deferred_count}


def thaw_discovery_run(*, frozen: Mapping[str, Any], configuration: Mapping[str, Any]) -> DiscoveryRun:
    """Rebuild a frozen draft without model or source access, for idempotent publication."""
    if frozen.get("version") != 2 or frozen.get("state") != "completed":
        raise ValueError("冻结发现结果版本或状态无效")
    config = validate_run_config(configuration, scope="discovery")
    if not config.ready:
        raise ValueError("冻结发现结果所需配置不可用")
    raw_events, raw_verifications = frozen.get("events"), frozen.get("verifications")
    if not isinstance(raw_events, list) or not isinstance(raw_verifications, list) or len(raw_events) != len(raw_verifications):
        raise ValueError("冻结发现结果事件无效")
    events: list[EventDraft] = []
    verifications: list[EventVerification] = []
    for raw_event, raw_verification in zip(raw_events, raw_verifications):
        if not isinstance(raw_event, Mapping) or not isinstance(raw_verification, Mapping):
            raise ValueError("冻结发现结果事件无效")
        required = ("canonicalKey", "stageKey", "eventState", "headline", "eventKind")
        if any(not isinstance(raw_event.get(key), str) or not raw_event[key] for key in required) or not isinstance(raw_event.get("facts"), Mapping):
            raise ValueError("冻结发现结果事件无效")
        event = EventDraft(raw_event["canonicalKey"], raw_event["stageKey"], raw_event["eventState"],
                           raw_event["headline"], raw_event["eventKind"], dict(raw_event["facts"]),
                           _refs_from_payload(raw_event.get("sourceRefs")))
        if not isinstance(raw_verification.get("state"), str) or not isinstance(raw_verification.get("summary"), str):
            raise ValueError("冻结发现结果核验无效")
        coverage = raw_verification.get("coverage")
        if not isinstance(coverage, Mapping):
            raise ValueError("冻结发现结果核验覆盖面无效")
        events.append(event)
        verifications.append(EventVerification(event, Verification(raw_verification["state"], raw_verification["summary"],
                                                                   _refs_from_payload(raw_verification.get("evidenceRefs")), dict(coverage))))
    def candidates(key: str) -> tuple[DiscoveryCandidate, ...]:
        rows = frozen.get(key)
        if not isinstance(rows, list):
            raise ValueError("冻结发现结果候选无效")
        items: list[DiscoveryCandidate] = []
        for row in rows:
            if not isinstance(row, Mapping) or not isinstance(row.get("eventIndex"), int):
                raise ValueError("冻结发现结果候选无效")
            index = row["eventIndex"]
            if index < 0 or index >= len(events):
                raise ValueError("冻结发现结果事件索引无效")
            mapping, comparison, eligibility = row.get("mapping"), row.get("comparison"), row.get("eligibility")
            if not isinstance(mapping, Mapping) or not isinstance(comparison, Mapping) or not isinstance(eligibility, Mapping):
                raise ValueError("冻结发现结果候选无效")
            if not all(isinstance(mapping.get(key), str) and mapping[key] for key in ("companyCode", "affectedStage", "uncertainty")) or not isinstance(mapping.get("inference"), Mapping):
                raise ValueError("冻结发现结果映射无效")
            if not isinstance(comparison.get("summary"), str) or not isinstance(comparison.get("differences"), Mapping):
                raise ValueError("冻结发现结果比较无效")
            rank = comparison.get("rank")
            if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int) or rank < 1):
                raise ValueError("冻结发现结果排序无效")
            if not isinstance(eligibility.get("state"), str) or (eligibility.get("reason") is not None and not isinstance(eligibility.get("reason"), str)):
                raise ValueError("冻结发现结果资格无效")
            event = events[index]
            items.append(DiscoveryCandidate(event, verifications[index].verification,
                CompanyMappingDraft(mapping["companyCode"], mapping["affectedStage"], _refs_from_payload(mapping.get("relationEvidence")),
                                    dict(mapping["inference"]), mapping["uncertainty"]),
                CandidateComparison(comparison["summary"], dict(comparison["differences"]),
                                    _refs_from_payload(comparison.get("evidenceRefs")), rank, comparison.get("marketContext")),
                Eligibility(eligibility["state"], eligibility["reason"]), dict(row["opportunity"])))
        return tuple(items)
    selected, deferred, pending, excluded = candidates("candidates"), candidates("deferred"), candidates("metadataPending"), candidates("excluded")
    return DiscoveryRun("completed", config, tuple(events), tuple(verifications), selected, deferred, pending, excluded,
                        int(frozen["deferredCount"]), candidates("updates"), candidates("background"))


def load_documents_from_store(*, cutoff_at: str, db_path: Path) -> tuple[DiscoveryDocument, ...]:
    """仅将现有 store 的原始资料转为发现输入；读取不会触发迁移。"""
    from .store import list_source_document_versions

    rows = list_source_document_versions(cutoff_at=cutoff_at, db_path=db_path)
    return tuple(DiscoveryDocument(document_id=row["documentId"], revision=int(row["revision"]),
                                   published_at=row["publishedAt"], fetched_at=row["fetchedAt"],
                                   original_text=row["originalText"], excerpt=row["excerpt"],
                                   metadata=row["metadata"]) for row in rows)


__all__ = [
    "CandidateComparison", "CompanyMappingDraft", "DiscoveryCandidate", "DiscoveryDocument", "DiscoveryModel",
    "DiscoveryRun", "DiscoveryWriter", "EvidenceRef", "EventDraft", "EventVerification", "SqliteDiscoveryWriter", "Verification",
    "VerificationFunction", "freeze_discovery_run", "thaw_discovery_run", "load_documents_from_store", "persist_discovery", "run_discovery",
]
