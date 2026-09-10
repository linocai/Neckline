"""K10 晚间发现链：全市场资料 → 理解 → 核验 → 公司映射 → 比较。

没有来源或模型路由配置时本模块只返回 ``not_configured``。所有模型和核验能力
均由调用方显式注入；没有真实来源账户时，fake provider 可在临时库完整验证流程。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from .config import ConfigurationStatus, validate_run_config
from .types import EventRevision
from .universe import CompanyMetadataProvider, Eligibility, evaluate_company
from .opportunity_discovery import (
    NEW_KINDS,
    normalize_catalyst_stage,
    validate_classification,
    validate_evidence_disclosure,
    validate_event_comparison,
)


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
    # The append-only source record remains the evidence source.  This optional derived
    # text is only the safe, local prompt representation for this particular frozen
    # revision; it must never be mistaken for a newly fetched source version.
    analysis_text: Optional[str] = None
    extraction: Mapping[str, Any] = field(default_factory=dict)

    @property
    def evidence_ref(self) -> EvidenceRef:
        return EvidenceRef(self.document_id, self.revision)


_READABLE_TEXT_EXTRACTION_VERSION = "html-readable-v1"


class _ReadableTextParser(HTMLParser):
    """Extract visible text without following resources or interpreting markup.

    The source feed contains publisher HTML, including scripts and advertising widgets.
    ``HTMLParser`` is used only as a local tokenizer: it neither evaluates attributes nor
    resolves URLs.  Tables become line-oriented text so quantities and labels stay paired.
    """

    _DROP_CONTAINERS = frozenset({"script", "style", "template", "iframe", "object", "svg", "canvas"})
    _DROP_VOID = frozenset({"embed"})
    _BREAK = frozenset({"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._dropped = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in self._DROP_CONTAINERS:
            self._dropped += 1
            return
        if lowered in self._DROP_VOID:
            return
        if self._dropped:
            return
        if lowered in self._BREAK:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() not in self._DROP_VOID:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self._DROP_CONTAINERS:
            if self._dropped:
                self._dropped -= 1
            return
        if not self._dropped and lowered in self._BREAK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._dropped:
            self._parts.append(data)

    def text(self) -> str:
        # Keep paragraph boundaries but make arbitrary markup whitespace deterministic.
        lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in "".join(self._parts).splitlines()]
        return "\n".join(line for line in lines if line)


def prepare_document_for_analysis(document: DiscoveryDocument) -> DiscoveryDocument:
    """Return a frozen document with a safe local readable-text representation.

    It is deliberately content-neutral: no ticker, sector, title, sentiment or benefit
    keyword determines whether a document is retained.  The caller still decides any
    explicitly configured template filtering separately.
    """
    if document.extraction.get("version") == _READABLE_TEXT_EXTRACTION_VERSION:
        return document
    raw = document.original_text or document.excerpt or ""
    parser = _ReadableTextParser()
    try:
        parser.feed(raw)
        parser.close()
        readable = parser.text()
    except (AssertionError, ValueError):
        # HTMLParser can reject pathological declarations.  Preserve the literal text as
        # plain input rather than interpreting it or silently losing a frozen document.
        readable = re.sub(r"\s+", " ", unescape(raw)).strip()
    if not readable:
        readable = re.sub(r"\s+", " ", unescape(raw)).strip()
    return replace(document, analysis_text=readable, extraction={
        "version": _READABLE_TEXT_EXTRACTION_VERSION,
        "sourceCharacters": len(raw),
        "readableCharacters": len(readable),
    })


@dataclass(frozen=True)
class DiscoveryIssue:
    """Safe, durable status for an independently failed unit; never model output."""

    stage: str
    code: str
    document_ref: EvidenceRef | None = None
    canonical_key: str | None = None

    def __post_init__(self) -> None:
        if not self.stage or not self.code:
            raise ValueError("发现失败状态缺少阶段或错误码")


class DiscoveryUnderstandingIncomplete(RuntimeError):
    """Selected bodies did not all produce a valid, durable understanding."""


class DiscoverySliceYield(RuntimeError):
    """Cooperative execution boundary; never a model/data failure."""


class DiscoveryDeadlineExceeded(RuntimeError):
    """The task's one persisted completion deadline elapsed; do not publish partial work."""


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
    historical_cases: tuple[Mapping[str, Any], ...] = ()
    historical_coverage: Mapping[str, Any] = field(default_factory=lambda: {
        "state": "unavailable", "requestedOutcomes": ["success", "flat", "failure"],
        "presentOutcomes": [], "missingOutcomes": ["success", "flat", "failure"],
        "reason": "historical_context_not_configured", "sourceRefs": [],
    })
    # ``rank`` is the event-local editorial order. The publication list position is
    # stored independently on DiscoveryCandidate and publication_samples.
    rank_namespace: str | None = None
    event_rank: int | None = None
    # B39 provenance is deliberately outside ``differences``. Differences are
    # the editorial comparison contract; the immutable research snapshot is a
    # separate bridge required by the publication/store validator.
    research_snapshot_id: str | None = None
    research_revision: int | None = None


@dataclass(frozen=True)
class EventComparison:
    """One event-wide comparison, with every mapped company evaluated together."""

    summary: str
    candidates: Mapping[str, CandidateComparison]
    evidence_refs: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class InvestigationOutcome:
    """The complete B39 research result consumed by discovery classification."""
    verification: Verification
    mappings: tuple[CompanyMappingDraft, ...]
    comparison: EventComparison
    snapshot_id: str


InvestigationFunction = Callable[[EventDraft], InvestigationOutcome]


class DiscoveryModel(Protocol):
    """轻量模型职责；不生成概率，也不承担完整正反辩论。"""

    def understand(self, *, document: DiscoveryDocument) -> Sequence[EventDraft]:
        ...

    def map_companies(
        self, *, event: EventDraft, verification: Verification
    ) -> Sequence[CompanyMappingDraft]:
        ...

    def compare_event(
        self, *, event: EventDraft, verification: Verification,
        mappings: Sequence[CompanyMappingDraft],
    ) -> EventComparison:
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
    display_rank: int | None = None


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
    issues: tuple[DiscoveryIssue, ...] = ()
    document_counts: Mapping[str, int] = field(default_factory=dict)


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


class FrozenDiscoveryDraftCompatibilityError(ValueError):
    """A pre-B35 draft has lost information needed for an honest publication."""


def _require_recoverable_formal_ranks(frozen: Mapping[str, Any]) -> None:
    """Reject old global-only draft ranks instead of inventing event-local ordering.

    B34 wrote a globally rewritten ``comparison.rank`` but did not retain either the
    event-local rank or the per-company list rank.  That is ambiguous even where a
    particular old record happens to look harmless; publishing it would make a tied
    comparison claim an order it never had.  Only formal candidates/deferred entries
    need this boundary—updates and background evidence cannot be published as samples.
    """
    for section in ("candidates", "deferred"):
        rows = frozen.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            comparison = row.get("comparison") if isinstance(row, Mapping) else None
            if (not isinstance(comparison, Mapping)
                    or comparison.get("rankNamespace") != "event"
                    or comparison.get("eventRank") != comparison.get("rank")
                    or not isinstance(row.get("displayRank"), int)):
                raise FrozenDiscoveryDraftCompatibilityError(
                    "旧冻结发现结果缺少事件内排序，已阻止发布；请基于冻结输入重新生成比较"
                )


def _validate_refs(refs: Sequence[EvidenceRef], available: set[EvidenceRef], *, label: str) -> None:
    unknown = [f"{ref.document_id}@{ref.revision}" for ref in refs if ref not in available]
    if unknown:
        raise ValueError(f"{label} 引用了未输入的原始资料：{','.join(unknown)}")


def _reject_uncalibrated_prediction(value: Any, *, path: str = "output") -> None:
    """Reject affirmative limit-up probabilities and mechanical scores anywhere.

    The product may explicitly say that it does *not* estimate a probability.  We
    therefore reject a numeric prediction attached to probability/score language,
    rather than blindly rejecting those terms themselves.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if ("probab" in lowered or "概率" in key_text or "score" in lowered or "评分" in key_text or "分数" in key_text):
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    raise ValueError(f"K10 比较不得输出未校准概率或机械预测分数：{path}.{key_text}")
                if isinstance(item, str) and re.fullmatch(r"\s*(?:\d+(?:\.\d+)?%?|0?\.\d+|\d+\s*分)\s*", item.replace("％", "%")):
                    raise ValueError(f"K10 比较不得输出未校准概率或机械预测分数：{path}.{key_text}")
            _reject_uncalibrated_prediction(item, path=f"{path}.{key_text}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_uncalibrated_prediction(item, path=f"{path}[{index}]")
        return
    if not isinstance(value, str):
        return
    text = value.replace("％", "%")
    probability = r"(?:涨停|封板)\s*(?:概率|几率)"
    score = r"(?:(?:机械|预测|模型|综合|优先)\s*)?(?:评分|分数|score)"
    probability_numeric = r"(?:\d{1,3}(?:\.\d+)?\s*%?|0?\.\d+)"
    score_numeric = r"(?:\d+(?:\.\d+)?\s*分?|0?\.\d+)"
    connective = r"(?:可能|预计|有望|或将|将|可|能|大致|约)?\s*"
    patterns = (
        rf"{probability}\s*{connective}(?:为|是|约|达(?:到)?|有|[:：=])?\s*{probability_numeric}",
        rf"{probability_numeric}\s*(?:的)?\s*(?:概率|几率)\s*(?:涨停|封板)",
        rf"{probability_numeric}\s*(?:涨停|封板)\s*(?:概率|几率)",
        rf"{score}\s*(?:为|是|约|达|有|[:：=])?\s*{score_numeric}",
    )
    refusal = re.compile(
        r"(?:不(?:能|可|会|应|予)?|未|无法|不能|难以|不可)\s*"
        r"(?:估计|判断|预测|给出|提供|输出|计算)?\s*$"
    )
    matches = (match for pattern in patterns for match in re.finditer(pattern, text, re.IGNORECASE))
    def affirmative(match: re.Match[str]) -> bool:
        # Negation must belong to this clause: "不能估计，但涨停概率70%" is
        # still an affirmative prediction. Source evidence is not sent through this
        # model-output guard; a model cannot bypass it by saying "资料显示".
        clause = re.split(r"[。！？；;，,]", text[:match.start()])[-1]
        return refusal.search(clause) is None
    if any(affirmative(match) for match in matches):
        raise ValueError(f"K10 比较不得输出未校准概率或机械预测分数：{path}")


def reject_uncalibrated_prediction(value: Any, *, path: str = "output") -> None:
    """Public boundary guard for nested model-derived comparison output."""
    _reject_uncalibrated_prediction(value, path=path)


def validate_event_comparison_rows(rows: Any) -> tuple[Mapping[str, Any], ...]:
    """Reject raw duplicate model rows before a dictionary could silently hide one."""
    if not isinstance(rows, list):
        raise ValueError("事件整体比较输出缺少 candidates")
    seen: set[str] = set()
    normalized: list[Mapping[str, Any]] = []
    for item in rows:
        if (not isinstance(item, Mapping) or not isinstance(item.get("companyCode"), str)
                or not item["companyCode"].strip()):
            raise ValueError("事件整体比较公司项无效")
        company_code = item["companyCode"]
        if company_code in seen:
            raise ValueError("事件整体比较同一公司不得重复")
        seen.add(company_code)
        normalized.append(item)
    return tuple(normalized)


def _normalized_event_state(event_state: str) -> str:
    if not isinstance(event_state, str) or not (normalized := event_state.strip().casefold()):
        raise ValueError("事件状态不能为空")
    return normalized


_EVENT_LABEL_KEYS = ("topic", "topics", "theme", "themes", "mechanism", "mechanisms")


def _merged_top_level_labels(events: Sequence[EventDraft]) -> dict[str, Any]:
    """Preserve every explicit structured label needed by historical comparison.

    ``sourceFacts`` retains the complete per-source model output.  HistoricalCaseLoader
    intentionally reads only top-level structured labels, however, so copying the first
    source or dropping the top level would silently make merged events incomparable.
    """
    merged: dict[str, Any] = {}
    for key in _EVENT_LABEL_KEYS:
        values: list[str] = []
        seen: set[str] = set()
        for event in events:
            value = event.facts.get(key)
            items = value if isinstance(value, (list, tuple, set)) else (value,)
            for item in items:
                if not isinstance(item, str) or not (text := item.strip()):
                    continue
                semantic = text.casefold()
                if semantic not in seen:
                    seen.add(semantic)
                    values.append(text)
        if values:
            # A list has the same meaning to the historical label reader as a scalar,
            # and avoids arbitrarily discarding a second source's distinct label.
            merged[key] = values
    return merged


def _merge_same_event_sources(events: Sequence[EventDraft]) -> tuple[EventDraft, ...]:
    """Combine supporting source versions before verification and comparison.

    A correction or denial has a distinct event state and is deliberately retained as a
    separate lifecycle input.  Supporting reports for the same canonical event and stage
    instead become one evidence-grounded event, so a later source cannot overwrite the
    earlier source during candidate selection.
    """
    groups: dict[tuple[str, str, str], list[EventDraft]] = {}
    for event in events:
        identity = (event.canonical_key, normalize_catalyst_stage(event.stage_key),
                    _normalized_event_state(event.event_state))
        groups.setdefault(identity, []).append(event)
    merged: list[EventDraft] = []
    for group in groups.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        first = group[0]
        refs = tuple(dict.fromkeys(ref for event in group for ref in event.source_refs))
        source_facts = [
            {
                "sourceRefs": [{"documentId": ref.document_id, "revision": ref.revision}
                               for ref in event.source_refs],
                "facts": dict(event.facts),
            }
            for event in group
        ]
        # B39 derives these claims during the one permitted read of each body.
        # Preserve that derivative when supporting reports merge into one event;
        # dropping it would tempt the research coordinator to read every source
        # again. A source-local model identifier may repeat across documents, so
        # namespace only collisions while retaining every source locator.
        claims: list[dict[str, Any]] = []
        seen_claim_ids: set[str] = set()
        for event in group:
            raw_claims = event.facts.get("researchClaims")
            if not isinstance(raw_claims, list):
                continue
            for raw_claim in raw_claims:
                if not isinstance(raw_claim, Mapping):
                    continue
                claim = dict(raw_claim)
                claim_id = claim.get("claimId")
                if not isinstance(claim_id, str) or not claim_id.strip():
                    continue
                if claim_id in seen_claim_ids:
                    source = claim.get("sourceRef")
                    if isinstance(source, Mapping) and isinstance(source.get("documentId"), str) and isinstance(source.get("revision"), int):
                        claim["claimId"] = f"{claim_id}@{source['documentId']}:{source['revision']}"
                    else:
                        claim["claimId"] = f"{claim_id}@{len(claims) + 1}"
                while claim["claimId"] in seen_claim_ids:
                    claim["claimId"] = f"{claim['claimId']}~{len(claims) + 1}"
                seen_claim_ids.add(claim["claimId"])
                claims.append(claim)
        headlines = tuple(dict.fromkeys(event.headline for event in group if event.headline))
        merged.append(EventDraft(
            canonical_key=first.canonical_key,
            stage_key=first.stage_key,
            event_state=first.event_state,
            headline="；".join(headlines),
            event_kind=first.event_kind,
            facts={**_merged_top_level_labels(group), "sourceFacts": source_facts, "researchClaims": claims},
            source_refs=refs,
        ))
    return tuple(merged)


def _open_event_opportunities(*, previous: Sequence[Mapping[str, Any]],
                              company_code: str, canonical_key: str) -> tuple[Mapping[str, Any], ...]:
    """Find every still-open formal target for a known event/company risk.

    A missing related id must never make a known risk look like first-seen metadata
    pending.  When multiple windows are still open, every one receives the conservative
    risk update instead of choosing an arbitrary predecessor.
    """
    return tuple(
        old for old in previous
        if old.get("companyCode") == company_code
        and old.get("canonicalKey") == canonical_key
        and old.get("state") not in {"withdrawn", "expired"}
    )


def freeze_event_drafts(events: Sequence[EventDraft]) -> list[dict[str, Any]]:
    """Serialize only validated event drafts for a per-document recovery record."""
    return [{"canonicalKey": event.canonical_key, "stageKey": event.stage_key,
             "eventState": event.event_state, "headline": event.headline,
             "eventKind": event.event_kind, "facts": dict(event.facts),
             "sourceRefs": [_ref_payload(ref) for ref in event.source_refs]}
            for event in events]


def thaw_event_drafts(value: Any) -> tuple[EventDraft, ...]:
    if not isinstance(value, list):
        raise ValueError("理解检查点事件无效")
    events: list[EventDraft] = []
    for row in value:
        if not isinstance(row, Mapping):
            raise ValueError("理解检查点事件无效")
        required = ("canonicalKey", "stageKey", "eventState", "headline", "eventKind")
        if (any(not isinstance(row.get(key), str) or not row[key] for key in required)
                or not isinstance(row.get("facts"), Mapping)):
            raise ValueError("理解检查点事件无效")
        events.append(EventDraft(row["canonicalKey"], row["stageKey"], row["eventState"],
                                 row["headline"], row["eventKind"], dict(row["facts"]),
                                 _refs_from_payload(row.get("sourceRefs"))))
    return tuple(events)


def _safe_issue_code(exc: Exception) -> str:
    """Map implementation/provider failures to an auditable, non-sensitive code."""
    explicit = getattr(exc, "code", None)
    if isinstance(explicit, str) and re.fullmatch(r"[a-z0-9_]{3,64}", explicit):
        return explicit
    if isinstance(exc, TimeoutError):
        return "network_timeout"
    if isinstance(exc, ConnectionError):
        return "network_failed"
    if isinstance(exc, (ValueError, TypeError)):
        return "contract_invalid"
    return "operation_failed"


_PENDING_ADMISSION_CODES = frozenset({
    "execution_paused", "execution_request_bound_missing", "execution_not_configured",
    "provider_request_outcome_unknown", "full_text_disabled",
})


@dataclass(frozen=True)
class ExactDocumentDeduplication:
    """Program-only exact duplicate result before title model admission.

    A missing body is deliberately never grouped on title similarity.  The
    duplicate relation preserves every original source reference so later event
    evidence remains traceable even when one exact copy avoids repeat work.
    """

    retained: tuple[DiscoveryDocument, ...]
    duplicates: Mapping[EvidenceRef, EvidenceRef]


def _exact_content_key(document: DiscoveryDocument) -> str | None:
    content = document.original_text if isinstance(document.original_text, str) and document.original_text.strip() else document.excerpt
    if not isinstance(content, str) or not content.strip():
        return None
    metadata = document.metadata if isinstance(document.metadata, Mapping) else {}
    title = metadata.get("title") if isinstance(metadata.get("title"), str) else ""
    if not title.strip():
        # A fixture, import, or degraded source without title context cannot
        # safely be called an exact news duplicate merely because its body is
        # equal to another record.
        return None
    return sha256((content + "\x1f" + title).encode("utf-8")).hexdigest()


def deduplicate_documents(documents: Sequence[DiscoveryDocument]) -> ExactDocumentDeduplication:
    """Deduplicate only byte-identical readable source content plus its title.

    This is not semantic title grouping: same title with a distinct body, or a
    title-only source with no body, remains an independent title-audit item.
    """
    frozen = tuple(documents)
    refs = [document.evidence_ref for document in frozen]
    if len(set(refs)) != len(refs):
        raise ValueError("发现输入 documentId/revision 不得重复")
    canonical_by_key: dict[str, DiscoveryDocument] = {}
    duplicates: dict[EvidenceRef, EvidenceRef] = {}
    for document in sorted(frozen, key=lambda item: (item.document_id, item.revision)):
        key = _exact_content_key(document)
        if key is None:
            continue
        original = canonical_by_key.get(key)
        if original is None:
            canonical_by_key[key] = document
        else:
            duplicates[document.evidence_ref] = original.evidence_ref
    duplicate_refs = set(duplicates)
    return ExactDocumentDeduplication(tuple(document for document in frozen if document.evidence_ref not in duplicate_refs), duplicates)


def run_discovery(
    *, documents: Sequence[DiscoveryDocument], configuration: Mapping[str, Any] | None,
    model: DiscoveryModel, verify: VerificationFunction, metadata: CompanyMetadataProvider,
    cutoff_at: datetime, phase: str = "evening", max_evening_candidates: int = 30,
    leaseguard: Callable[[], None] | None = None,
    previous_opportunities: Sequence[Mapping[str, Any]] = (),
    understood_by_document: Mapping[EvidenceRef, Sequence[EventDraft]] | None = None,
    checkpoint: Callable[[Mapping[str, Any]], None] | None = None,
    understand_concurrency: int | None = None,
    document_batch_size: int | None = None,
    selected_source_refs: Sequence[EvidenceRef] | None = None,
    investigate: InvestigationFunction | None = None,
    investigation_concurrency: int | None = None,
) -> DiscoveryRun:
    """执行可注入发现链；仅晚间可生成最多 30 条新候选。

    ``document_batch_size`` is deliberately a bounded scheduling window, not a
    provider payload batch.  At most that many document understand operations
    may be queued or in flight; each completed document is checkpointed and
    immediately makes room for the next one.  ``understand_concurrency`` is
    the independent upper bound on simultaneous provider calls.  Keeping the
    two separate prevents one slow document from turning a whole nominal batch
    into a barrier, while still preventing an unbounded 2,000-document submit.
    """
    if cutoff_at.tzinfo is None:
        raise ValueError("cutoff_at 必须带时区")
    if phase not in {"evening", "morning"}:
        raise ValueError("K10 discovery phase 必须是 evening 或 morning")
    if max_evening_candidates != 30:
        raise ValueError("K10 晚间候选上限固定为 30，不接受运行时策略默认或改写")
    if understand_concurrency is not None and (isinstance(understand_concurrency, bool) or understand_concurrency < 1):
        raise ValueError("理解并发必须是正整数")
    if document_batch_size is not None and (isinstance(document_batch_size, bool) or document_batch_size < 1):
        raise ValueError("理解批大小必须是正整数")
    config = validate_run_config(configuration, scope="discovery")
    if not config.ready:
        return DiscoveryRun("not_configured", config, (), (), (), (), (), (), 0)
    # V3 title triage freezes body admission before this module is permitted to
    # touch readable source text.  The caller must provide exactly those real
    # source revisions, never a larger list that this routine silently filters.
    if selected_source_refs is not None:
        selected = tuple(selected_source_refs)
        if any(not isinstance(ref, EvidenceRef) for ref in selected) or len(set(selected)) != len(selected):
            raise ValueError("正文发现 selected_source_refs 必须是唯一真实 EvidenceRef")
        by_ref = {document.evidence_ref: document for document in documents}
        if len(by_ref) != len(documents) or set(by_ref) != set(selected):
            raise ValueError("正文发现输入必须恰好等于已冻结入选文章")
        # Retain the global selection's deterministic ordering for event audit;
        # it is not a body-level re-ranking or a way to replace a missing item.
        documents = tuple(by_ref[ref] for ref in selected)
    deduplication = deduplicate_documents(documents)
    prepared_documents = tuple(prepare_document_for_analysis(document) for document in deduplication.retained)
    source_refs_by_document: dict[EvidenceRef, set[EvidenceRef]] = {
        document.evidence_ref: {document.evidence_ref} for document in prepared_documents
    }
    for duplicate_ref, retained_ref in deduplication.duplicates.items():
        source_refs_by_document[retained_ref].add(duplicate_ref)
    screened_documents = prepared_documents
    # A resumed scan can bypass already-completed document understanding.  Give
    # provider-backed event stages the exact same prepared frozen documents
    # before that bypass, without fetching or analysing anything again.
    register_documents = getattr(model, "register_documents", None)
    if callable(register_documents):
        register_documents(documents=prepared_documents)
    available = {document.evidence_ref for document in documents}
    events: list[EventDraft] = []
    verified_events: list[EventVerification] = []
    all_candidates: list[DiscoveryCandidate] = []
    pending: list[DiscoveryCandidate] = []
    excluded: list[DiscoveryCandidate] = []
    updates: list[DiscoveryCandidate] = []
    background: list[DiscoveryCandidate] = []
    # Checkpoints are intentionally written in completion order, but later
    # merge/verification must be independent of thread timing and resume shape.
    # Keep the final event stream keyed by the frozen document reference and
    # reassemble it in frozen input order after all document work is settled.
    understood_by_ref: dict[EvidenceRef, tuple[EventDraft, ...]] = {}
    issues: list[DiscoveryIssue] = []
    counts = {"input": len(documents), "exactDeduplicated": len(deduplication.duplicates),
              "understood": 0, "understandFailed": 0, "eventFailed": 0}
    if selected_source_refs is not None:
        counts.update({"articleAdmitted": len(prepared_documents)})
    recovered = understood_by_document or {}
    pending_documents: list[DiscoveryDocument] = []
    for document in screened_documents:
        if document.evidence_ref in recovered:
            recovered_events = tuple(recovered[document.evidence_ref])
            for event in recovered_events:
                _validate_refs(event.source_refs, available, label="恢复事件")
                if not set(event.source_refs).issubset(source_refs_by_document[document.evidence_ref]):
                    raise ValueError("恢复事件引用不属于已准入真实来源")
            understood_by_ref[document.evidence_ref] = recovered_events
            counts["understood"] += 1
            continue
        pending_documents.append(document)

    def complete_understanding(document: DiscoveryDocument, result: Sequence[EventDraft]) -> None:
        expanded: list[EventDraft] = []
        for event in tuple(result):
            refs: list[EvidenceRef] = []
            for ref in event.source_refs:
                refs.extend(sorted(source_refs_by_document.get(ref, {ref}), key=lambda item: (item.document_id, item.revision)))
            expanded.append(replace(event, source_refs=tuple(dict.fromkeys(refs))))
        events_for_document = tuple(expanded)
        for event in events_for_document:
            _validate_refs(event.source_refs, available, label="事件")
            if not set(event.source_refs).issubset(source_refs_by_document[document.evidence_ref]):
                raise ValueError("事件引用不属于已准入真实来源")
        understood_by_ref[document.evidence_ref] = events_for_document
        counts["understood"] += 1
        full_text_used = getattr(model, "full_text_used", None)
        # This is a display/progress derivative only.  The model operation
        # cache remains exactly the same validated event payload and input
        # digest; a marker is true solely after a key→full route succeeded.
        used_full_text = bool(full_text_used(document=document)) if callable(full_text_used) else False
        full_text_requested = getattr(model, "full_text_requested", None)
        requested_full_text = bool(full_text_requested(document=document)) if callable(full_text_requested) else used_full_text
        if checkpoint is not None:
            checkpoint({"stage": "understand", "state": "completed", "documentRef": _ref_payload(document.evidence_ref),
                        "events": freeze_event_drafts(events_for_document), "extraction": dict(document.extraction),
                        "fullTextUsed": used_full_text})

    def fail_understanding(document: DiscoveryDocument, exc: Exception) -> str | None:
        code = _safe_issue_code(exc)
        pending = code in _PENDING_ADMISSION_CODES
        full_text_requested = getattr(model, "full_text_requested", None)
        requested_full_text = bool(full_text_requested(document=document)) if callable(full_text_requested) else False
        if pending:
            counts["understandPending"] = counts.get("understandPending", 0) + 1
        else:
            counts["understandFailed"] += 1
        issue = DiscoveryIssue("understand", code, document.evidence_ref)
        issues.append(issue)
        if checkpoint is not None:
            checkpoint({"stage": issue.stage, "state": "pending" if pending else "failed", "code": issue.code,
                        "documentRef": _ref_payload(document.evidence_ref), "extraction": dict(document.extraction)})
        return code if pending else None

    def mark_unadmitted(documents: Sequence[DiscoveryDocument], *, code: str) -> None:
        """Make a closed admission boundary visible without probing every package."""
        if not documents:
            return
        counts["understandPending"] = counts.get("understandPending", 0) + len(documents)

    concurrency = understand_concurrency or 1
    # An omitted window retains the historical "all supplied docs" behaviour
    # for direct, non-production unit callers.  Bound production execution
    # always supplies documentBatchSize from its frozen execution profile.
    window = document_batch_size or len(pending_documents) or 1

    def complete_future(document: DiscoveryDocument, future) -> str | None:
        try:
            # Persist in completion order.  Candidate/event ordering remains
            # deterministic below, after all complete event work is globally
            # prioritized; a checkpoint must never wait for a slow peer.
            complete_understanding(document, future.result())
        except Exception as exc:  # independent document failures are recoverable units
            if isinstance(exc, (DiscoverySliceYield, DiscoveryDeadlineExceeded)):
                raise
            return fail_understanding(document, exc)
        return None

    if concurrency == 1 or len(pending_documents) <= 1:
        for index, document in enumerate(pending_documents):
            if leaseguard is not None:
                leaseguard()
            try:
                complete_understanding(document, model.understand(document=document))
            except Exception as exc:  # independent document failures are recoverable units
                if isinstance(exc, (DiscoverySliceYield, DiscoveryDeadlineExceeded)):
                    raise
                blocked_code = fail_understanding(document, exc)
                if blocked_code is not None:
                    mark_unadmitted(pending_documents[index + 1:], code=blocked_code)
                    break
    else:
        # Submit only a fixed window.  Once a slice boundary is reached, stop
        # admitting new work, drain the already charged requests into durable
        # checkpoints, then yield.  This avoids losing successful peers and
        # avoids enqueuing work that a fresh continuation cannot safely own.
        documents_iter = iter(pending_documents)
        yield_requested = False
        admission_blocked_code: str | None = None
        exhausted = False
        with ThreadPoolExecutor(max_workers=min(concurrency, window)) as executor:
            futures: dict[Any, DiscoveryDocument] = {}

            def refill() -> None:
                nonlocal exhausted, yield_requested
                while not exhausted and not yield_requested and admission_blocked_code is None and len(futures) < window:
                    try:
                        document = next(documents_iter)
                    except StopIteration:
                        exhausted = True
                        return
                    try:
                        if leaseguard is not None:
                            leaseguard()
                    except DiscoverySliceYield:
                        yield_requested = True
                        return
                    futures[executor.submit(model.understand, document=document)] = document

            refill()
            if yield_requested and not futures:
                raise DiscoverySliceYield()
            while futures:
                future = next(as_completed(futures))
                document = futures.pop(future)
                # A slice boundary affects only admission.  This completed
                # derivative and all already in-flight peers still need their
                # checkpoint writes before the continuation is scheduled.
                if not yield_requested:
                    try:
                        if leaseguard is not None:
                            leaseguard()
                    except DiscoverySliceYield:
                        yield_requested = True
                blocked_code = complete_future(document, future)
                if blocked_code is not None and admission_blocked_code is None:
                    admission_blocked_code = blocked_code
                if not yield_requested and admission_blocked_code is None:
                    refill()
        if yield_requested:
            raise DiscoverySliceYield()
        if admission_blocked_code is not None:
            # The iterator still owns items that were never offered to the model.
            mark_unadmitted(tuple(documents_iter), code=admission_blocked_code)

    if investigate is not None and (counts["understandFailed"] or counts.get("understandPending", 0)):
        # A failed selected body is not evidence that there are no events.
        # Keep all successful document checkpoints, but never start research
        # or publish the surviving subset as a completed B39 discovery run.
        raise DiscoveryUnderstandingIncomplete()

    def record_pending_event(*, stage: str, code: str, event: EventDraft,
                             company_code: str | None = None) -> None:
        if stage == "verify_or_map":
            counts["pendingVerification"] = counts.get("pendingVerification", 0) + 1
        issue = DiscoveryIssue(stage, code, canonical_key=event.canonical_key)
        issues.append(issue)
        if checkpoint is not None:
            payload: dict[str, Any] = {"stage": stage, "state": "pending", "code": code,
                                       "canonicalKey": event.canonical_key}
            if company_code is not None:
                payload["companyCode"] = company_code
            checkpoint(payload)
    understood = [event for document in screened_documents
                  for event in understood_by_ref.get(document.evidence_ref, ())]
    merged_events = _merge_same_event_sources(understood)
    research_results: dict[int, InvestigationOutcome | Exception] = {}
    if investigate is not None and investigation_concurrency is not None and investigation_concurrency > 1:
        # Only independent events overlap. Each event retains its sequential
        # question/evidence/comparison state machine and durable checkpoints.
        with ThreadPoolExecutor(max_workers=investigation_concurrency) as executor:
            research_futures = {}
            remaining = iter(merged_events)
            def submit_next() -> bool:
                event = next(remaining, None)
                if event is None:
                    return False
                if leaseguard is not None:
                    leaseguard()
                research_futures[executor.submit(investigate, event)] = event
                return True
            for _ in range(investigation_concurrency):
                if not submit_next():
                    break
            while research_futures:
                finished, _ = wait(research_futures, return_when=FIRST_COMPLETED)
                for future in finished:
                    event = research_futures.pop(future)
                    try:
                        research_results[id(event)] = future.result()
                    except (DiscoverySliceYield, DiscoveryDeadlineExceeded):
                        # Context manager drains active responses before yielding;
                        # no paid result is abandoned by cancelling its thread.
                        raise
                    except Exception as exc:
                        research_results[id(event)] = exc
                    submit_next()
    event_admission_closed = False
    for event in merged_events:
            events.append(event)  # an understood event remains auditable if later stages fail
            try:
                if leaseguard is not None:
                    leaseguard()
                if investigate is None:
                    verification = verify(event)
                    mappings = ()
                    event_comparison = None
                else:
                    researched = research_results[id(event)] if id(event) in research_results else investigate(event)
                    if isinstance(researched, Exception):
                        raise researched
                    if not isinstance(researched, InvestigationOutcome):
                        raise ValueError("研究回调结果无效")
                    verification, mappings, event_comparison = (researched.verification, researched.mappings,
                                                                  researched.comparison)
                available.update(document.evidence_ref for document in verification.documents)
                _validate_refs(verification.evidence_refs, available, label="重点核验")
                coverage_state = verification.coverage.get("state") if isinstance(verification.coverage, Mapping) else None
                if coverage_state == "pending":
                    # A bounded independent-evidence request that has not run is a visible
                    # pending item, not permission to spend map/compare/classify calls on a
                    # self-certified event.  The append-only event survives for retry.
                    verified_events.append(EventVerification(event, verification))
                    issue = DiscoveryIssue("verify", "verification_pending", canonical_key=event.canonical_key)
                    issues.append(issue)
                    if checkpoint is not None:
                        checkpoint({"stage": issue.stage, "state": "pending", "code": issue.code,
                                    "canonicalKey": event.canonical_key})
                    continue
                if investigate is None:
                    if leaseguard is not None:
                        leaseguard()
                    mappings = tuple(model.map_companies(event=event, verification=verification))
            except Exception as exc:
                if isinstance(exc, (DiscoverySliceYield, DiscoveryDeadlineExceeded)):
                    raise
                code = _safe_issue_code(exc)
                if code in _PENDING_ADMISSION_CODES:
                    record_pending_event(stage="verify_or_map", code=code, event=event)
                    event_admission_closed = True
                    break
                counts["eventFailed"] += 1
                issue = DiscoveryIssue("verify_or_map", code, canonical_key=event.canonical_key)
                issues.append(issue)
                if checkpoint is not None:
                    checkpoint({"stage": issue.stage, "state": "failed", "code": issue.code,
                                "canonicalKey": event.canonical_key})
                continue
            if not mappings:
                verified_events.append(EventVerification(event, verification))
                continue
            if len({mapping.company_code for mapping in mappings}) != len(mappings):
                raise ValueError("同一事件的公司映射不得重复")
            try:
                if event_comparison is None:
                    comparer = getattr(model, "compare_event", None)
                    if not callable(comparer):
                        raise ValueError("发现模型缺少同事件整体公司比较")
                    if leaseguard is not None:
                        leaseguard()
                    event_comparison = comparer(event=event, verification=verification, mappings=mappings)
                _validate_refs(event_comparison.evidence_refs, available, label="事件比较")
            except Exception as exc:
                if isinstance(exc, (DiscoverySliceYield, DiscoveryDeadlineExceeded)):
                    raise
                code = _safe_issue_code(exc)
                if code in _PENDING_ADMISSION_CODES:
                    record_pending_event(stage="compare", code=code, event=event)
                    event_admission_closed = True
                    break
                counts["eventFailed"] += 1
                issue = DiscoveryIssue("compare", code, canonical_key=event.canonical_key)
                issues.append(issue)
                if checkpoint is not None:
                    checkpoint({"stage": issue.stage, "state": "failed", "code": issue.code,
                                "canonicalKey": event.canonical_key})
                continue
            try:
                event_candidates = event_comparison.candidates
                validate_event_comparison(
                    summary=event_comparison.summary, comparisons={
                        code: {"summary": item.summary, "differences": item.differences, "rank": item.rank}
                        for code, item in event_candidates.items()
                    }, company_codes=tuple(mapping.company_code for mapping in mappings),
                )
                _reject_uncalibrated_prediction(event_comparison.summary, path="eventComparison.summary")
                _reject_uncalibrated_prediction({
                    code: {"summary": item.summary, "differences": item.differences}
                    for code, item in event_candidates.items()
                }, path="eventComparison.candidates")
                event_candidates = {
                    code: replace(item, rank_namespace="event", event_rank=item.rank)
                    for code, item in event_candidates.items()
                }
                event = replace(event, facts={**event.facts, "eventComparison": {
                    "summary": event_comparison.summary,
                    "evidenceRefs": [{"documentId": ref.document_id, "revision": ref.revision}
                                     for ref in event_comparison.evidence_refs],
                }})
            except Exception as exc:
                if isinstance(exc, (DiscoverySliceYield, DiscoveryDeadlineExceeded)):
                    raise
                counts["eventFailed"] += 1
                issue = DiscoveryIssue("compare", _safe_issue_code(exc), canonical_key=event.canonical_key)
                issues.append(issue)
                if checkpoint is not None:
                    checkpoint({"stage": issue.stage, "state": "failed", "code": issue.code,
                                "canonicalKey": event.canonical_key})
                continue
            events[-1] = event
            verified_events.append(EventVerification(event, verification))
            for mapping in mappings:
                try:
                    _validate_refs(mapping.relation_evidence, available, label="公司映射")
                    if leaseguard is not None:
                        leaseguard()
                    comparison = event_candidates[mapping.company_code]
                    company_verification = verification
                    if comparison.research_snapshot_id is not None:
                        company_state = comparison.differences.get('evidenceDisclosure', {}).get('verificationStatus')
                        company_verification = replace(verification, state=company_state if company_state in {'verified','contradicted'} else 'needs_review')

                    _validate_refs(comparison.evidence_refs, available, label="候选比较")
                    status = (metadata.eligibility(mapping.company_code) if hasattr(metadata, "eligibility") else
                              evaluate_company(metadata.lookup(company_code=mapping.company_code, as_of=cutoff_at)))
                    classifier = getattr(model, "classify_opportunity", None)
                    if not callable(classifier):
                        raise ValueError("发现模型缺少机会延续/新催化分类")
                    prior = tuple(old for old in previous_opportunities if old.get("companyCode") == mapping.company_code)
                    decision = validate_classification(
                        classifier(event=event, verification=company_verification, mapping=mapping,
                                   comparison=comparison, previous=prior),
                        canonical_key=event.canonical_key, stage_key=event.stage_key,
                        company_code=mapping.company_code, previous=prior,
                    )
                    _reject_uncalibrated_prediction(decision, path="classification")
                except Exception as exc:
                    if isinstance(exc, (DiscoverySliceYield, DiscoveryDeadlineExceeded)):
                        raise
                    code = _safe_issue_code(exc)
                    if code in _PENDING_ADMISSION_CODES:
                        record_pending_event(stage="classify", code=code, event=event,
                                             company_code=mapping.company_code)
                        event_admission_closed = True
                        break
                    counts["eventFailed"] += 1
                    issue = DiscoveryIssue("classify", code, canonical_key=event.canonical_key)
                    issues.append(issue)
                    if checkpoint is not None:
                        checkpoint({"stage": issue.stage, "state": "failed", "code": issue.code,
                                    "canonicalKey": event.canonical_key, "companyCode": mapping.company_code})
                    continue
                disclosure = (comparison.differences.get("evidenceDisclosure")
                              if isinstance(comparison.differences, Mapping) else None)
                completed_research_disclosure = False
                if (isinstance(disclosure, Mapping) and comparison.differences.get("role") in {"primary", "alternative", "tied"}
                        and company_verification.state != "contradicted"):
                    try:
                        validate_evidence_disclosure(disclosure)
                        completed_research_disclosure = True
                    except ComparisonValidationError:
                        pass
                if decision["kind"] in NEW_KINDS and company_verification.state != "verified" and not completed_research_disclosure:
                    # A model cannot promote a source document into a formal opportunity by
                    # calling it ``initial``/``material_stage``/``independent`` while the
                    # independent check is still unresolved or has found contrary evidence.
                    # For a linked old opportunity this remains an update/risk record; for a
                    # first-seen item it is retained as pending below.
                    prefix = ("重点核验已发现反证，不能作为新机会发布。"
                              if company_verification.state == "contradicted"
                              else "重点核验尚未完成，不能作为新机会发布。")
                    decision = {**decision, "kind": "needs_review", "reason": prefix + decision["reason"]}
                if decision["kind"] == "invalidated" and company_verification.state not in {"verified", "contradicted"}:
                    decision = {**decision, "kind": "needs_review", "reason": "重大反证尚待核实。" + decision["reason"]}
                candidate = DiscoveryCandidate(event, company_verification, mapping, comparison, status, decision)
                if decision["kind"] == "background":
                    background.append(candidate)
                    continue
                if decision["kind"] == "needs_review" and decision.get("relatedOpportunityId") is None:
                    related = _open_event_opportunities(
                        previous=previous_opportunities, company_code=mapping.company_code,
                        canonical_key=event.canonical_key,
                    )
                    if related:
                        # Do not guess which old window a known risk belongs to.  Each still
                        # open formal opportunity receives the same conservative lifecycle
                        # update, while the source event remains one append-only revision.
                        for old in related:
                            updates.append(replace(candidate, opportunity={
                                **decision,
                                "relatedOpportunityId": old["opportunityId"],
                                "reason": "模型未关联旧机会；按同事件同公司补记风险。" + decision["reason"],
                            }))
                        continue
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
                    if configuration.get("configVersion") == "k10-v2" and decision["kind"] == "continuation" and status.eligible and comparison.differences.get("role") in {"primary", "alternative", "tied"} and comparison.rank is not None:
                        all_candidates.append(candidate)
                    continue
                if configuration.get('configVersion') == 'k10-v2' and comparison.differences.get('role') not in {'primary','alternative','tied'}:
                    (pending if comparison.differences.get('role') == 'pending' else excluded).append(candidate)
                    continue
                if status.eligible:
                    all_candidates.append(candidate)
                elif status.state == "insufficient_metadata":
                    pending.append(candidate)
                else:
                    excluded.append(candidate)
            if event_admission_closed:
                break
    # Rank companies once, while retaining every formally recommended catalyst for each
    # admitted company. Continuations have already been separated from the new-company quota.
    # An empty formal set is a normal outcome: events, pending evidence, exclusions and
    # continuation/risk updates must still persist without spending a model call on ordering.
    ordered_keys: tuple[tuple[str, str], ...] = ()
    if event_admission_closed:
        # Ranking is itself a provider operation.  Prior verified candidates remain
        # visible as pending work rather than consuming a final unreserved call.
        pending.extend(all_candidates)
        all_candidates.clear()
    if all_candidates:
        choices = getattr(model, "prioritize", None)
        if not callable(choices):
            raise ValueError("发现模型缺少跨事件公司比较")
        if leaseguard is not None:
            leaseguard()
        try:
            ordered_keys = tuple(choices(candidates=tuple(all_candidates)))
        except Exception as exc:
            if isinstance(exc, (DiscoverySliceYield, DiscoveryDeadlineExceeded)):
                raise
            code = _safe_issue_code(exc)
            if code not in _PENDING_ADMISSION_CODES:
                raise
            # A ranking budget refusal is a visible partial state; without a
            # frozen global order none of the otherwise valid rows may publish.
            anchor = all_candidates[0].event
            record_pending_event(stage="prioritize", code=code, event=anchor)
            pending.extend(all_candidates)
            all_candidates.clear()
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
            ranked = replace(candidate, display_rank=index)
            (deferred if phase == "evening" and index > max_evening_candidates else selected).append(ranked)
    state = "partial" if issues else "completed"
    return DiscoveryRun(state, config, tuple(events), tuple(verified_events), tuple(selected), tuple(deferred),
                        tuple(pending), tuple(excluded), len({item.mapping.company_code for item in deferred}), tuple(updates), tuple(background),
                        tuple(issues), counts)


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
        from .historical_cases import freeze_historical_context

        identity = _stable_id("candidate", self._scan_id, event.event_id, str(event.revision),
                              candidate.mapping.company_code)
        historical_context = freeze_historical_context({"historicalCases": list(candidate.comparison.historical_cases),
                                                        "historicalCoverage": candidate.comparison.historical_coverage})
        comparison = {"summary": candidate.comparison.summary, "differences": candidate.comparison.differences,
                      "evidenceRefs": self._refs(candidate.comparison.evidence_refs), "rank": candidate.comparison.rank,
                      "classification": dict(candidate.opportunity),
                      **historical_context}
        if candidate.comparison.rank_namespace is not None:
            comparison["rankNamespace"] = candidate.comparison.rank_namespace
        if candidate.comparison.event_rank is not None:
            comparison["eventRank"] = candidate.comparison.event_rank
        if candidate.comparison.market_context is not None:
            comparison["marketContext"] = dict(candidate.comparison.market_context)
        if (candidate.comparison.research_snapshot_id is None) != (candidate.comparison.research_revision is None):
            raise ValueError("研究比较桥接必须同时包含快照与修订")
        if candidate.comparison.research_snapshot_id is not None:
            comparison["researchSnapshotId"] = candidate.comparison.research_snapshot_id
            comparison["researchRevision"] = candidate.comparison.research_revision
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
            display_rank=candidate.display_rank,
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
    if run.state not in {"completed", "partial"}:
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
        from .historical_cases import freeze_historical_context
        historical_context = freeze_historical_context({"historicalCases": list(candidate.comparison.historical_cases),
                                                        "historicalCoverage": candidate.comparison.historical_coverage})
        return {"eventIndex": index, "displayRank": candidate.display_rank,
                "mapping": {"companyCode": candidate.mapping.company_code,
                "affectedStage": candidate.mapping.affected_stage,
                "relationEvidence": [_ref_payload(ref) for ref in candidate.mapping.relation_evidence],
                "inference": dict(candidate.mapping.inference), "uncertainty": candidate.mapping.uncertainty},
                "comparison": {"summary": candidate.comparison.summary,
                "differences": dict(candidate.comparison.differences),
                "evidenceRefs": [_ref_payload(ref) for ref in candidate.comparison.evidence_refs],
                "rank": candidate.comparison.rank,
                "rankNamespace": candidate.comparison.rank_namespace,
                "eventRank": candidate.comparison.event_rank,
                "marketContext": candidate.comparison.market_context,
                "researchSnapshotId": candidate.comparison.research_snapshot_id,
                "researchRevision": candidate.comparison.research_revision,
                **historical_context},
                "opportunity": dict(candidate.opportunity),
                "eligibility": {"state": candidate.eligibility.state, "reason": candidate.eligibility.reason}}
    return {"version": 4, "state": run.state, "events": freeze_event_drafts(run.events),
            "verifications": [verification_payload(event) for event in run.events],
            "candidates": [candidate_payload(item) for item in run.candidates],
            "deferred": [candidate_payload(item) for item in run.deferred],
            "metadataPending": [candidate_payload(item) for item in run.metadata_pending],
            "excluded": [candidate_payload(item) for item in run.excluded],
            "updates": [candidate_payload(item) for item in run.updates],
            "background": [candidate_payload(item) for item in run.background], "deferredCount": run.deferred_count,
            "issues": [{"stage": issue.stage, "code": issue.code,
                        **({"documentRef": _ref_payload(issue.document_ref)} if issue.document_ref else {}),
                        **({"canonicalKey": issue.canonical_key} if issue.canonical_key else {})}
                       for issue in run.issues],
            "documentCounts": dict(run.document_counts)}


def thaw_discovery_run(*, frozen: Mapping[str, Any], configuration: Mapping[str, Any]) -> DiscoveryRun:
    """Rebuild a frozen draft without model or source access, for idempotent publication."""
    if frozen.get("version") not in {3, 4} or frozen.get("state") not in {"completed", "partial"}:
        raise ValueError("冻结发现结果版本或状态无效")
    _require_recoverable_formal_ranks(frozen)
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
            from .historical_cases import freeze_historical_context
            historical_context = freeze_historical_context({"historicalCases": comparison.get("historicalCases"),
                                                            "historicalCoverage": comparison.get("historicalCoverage")})
            rank = comparison.get("rank")
            if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int) or rank < 1):
                raise ValueError("冻结发现结果排序无效")
            display_rank = row.get("displayRank", comparison.get("displayRank"))
            if display_rank is not None and (isinstance(display_rank, bool) or not isinstance(display_rank, int) or display_rank < 1):
                raise ValueError("冻结发现结果清单排序无效")
            if not isinstance(eligibility.get("state"), str) or (eligibility.get("reason") is not None and not isinstance(eligibility.get("reason"), str)):
                raise ValueError("冻结发现结果资格无效")
            snapshot_id, research_revision = comparison.get("researchSnapshotId"), comparison.get("researchRevision")
            if (snapshot_id is None) != (research_revision is None):
                raise ValueError("冻结发现结果研究桥接无效")
            if snapshot_id is not None and (not isinstance(snapshot_id, str) or not snapshot_id
                                           or isinstance(research_revision, bool) or not isinstance(research_revision, int)
                                           or research_revision < 1):
                raise ValueError("冻结发现结果研究桥接无效")
            event = events[index]
            items.append(DiscoveryCandidate(event, verifications[index].verification,
                CompanyMappingDraft(mapping["companyCode"], mapping["affectedStage"], _refs_from_payload(mapping.get("relationEvidence")),
                                    dict(mapping["inference"]), mapping["uncertainty"]),
                CandidateComparison(comparison["summary"], dict(comparison["differences"]),
                                    _refs_from_payload(comparison.get("evidenceRefs")), rank, comparison.get("marketContext"),
                                    tuple(historical_context["historicalCases"]), historical_context["historicalCoverage"],
                                    comparison.get("rankNamespace"), comparison.get("eventRank"), snapshot_id, research_revision),
                Eligibility(eligibility["state"], eligibility["reason"]), dict(row["opportunity"]), display_rank))
        return tuple(items)
    selected, deferred, pending, excluded = candidates("candidates"), candidates("deferred"), candidates("metadataPending"), candidates("excluded")
    raw_issues = frozen.get("issues", [])
    if not isinstance(raw_issues, list):
        raise ValueError("冻结发现结果失败项无效")
    issues: list[DiscoveryIssue] = []
    for row in raw_issues:
        if not isinstance(row, Mapping) or not isinstance(row.get("stage"), str) or not isinstance(row.get("code"), str):
            raise ValueError("冻结发现结果失败项无效")
        raw_ref = row.get("documentRef")
        ref = _refs_from_payload([raw_ref])[0] if raw_ref is not None else None
        key = row.get("canonicalKey")
        if key is not None and not isinstance(key, str):
            raise ValueError("冻结发现结果失败项无效")
        issues.append(DiscoveryIssue(row["stage"], row["code"], ref, key))
    counts = frozen.get("documentCounts", {})
    if not isinstance(counts, Mapping) or any(not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int) or value < 0
                                              for key, value in counts.items()):
        raise ValueError("冻结发现结果覆盖计数无效")
    return DiscoveryRun(str(frozen["state"]), config, tuple(events), tuple(verifications), selected, deferred, pending, excluded,
                        int(frozen["deferredCount"]), candidates("updates"), candidates("background"), tuple(issues), dict(counts))


def load_documents_from_store(*, cutoff_at: str, db_path: Path) -> tuple[DiscoveryDocument, ...]:
    """仅将现有 store 的原始资料转为发现输入；读取不会触发迁移。"""
    from .store import list_source_document_versions

    rows = list_source_document_versions(cutoff_at=cutoff_at, db_path=db_path)
    return tuple(DiscoveryDocument(document_id=row["documentId"], revision=int(row["revision"]),
                                   published_at=row["publishedAt"], fetched_at=row["fetchedAt"],
                                   original_text=row["originalText"], excerpt=row["excerpt"],
                                   metadata=row["metadata"]) for row in rows)


__all__ = [
    "CandidateComparison", "CompanyMappingDraft", "DiscoveryCandidate", "DiscoveryDocument", "DiscoveryIssue", "DiscoveryModel", "EventComparison",
    "DiscoveryRun", "DiscoveryDeadlineExceeded", "DiscoverySliceYield", "DiscoveryWriter", "EvidenceRef", "EventDraft", "EventVerification", "SqliteDiscoveryWriter", "Verification",
    "VerificationFunction", "freeze_discovery_run", "thaw_discovery_run", "load_documents_from_store", "persist_discovery", "run_discovery",
    "freeze_event_drafts", "thaw_event_drafts", "prepare_document_for_analysis", "reject_uncalibrated_prediction",
    "ExactDocumentDeduplication", "deduplicate_documents",
    "validate_event_comparison_rows", "FrozenDiscoveryDraftCompatibilityError",
]
