"""Frozen, evidence-led historical context for K10 comparison and analysis.

Historical material is never a discovery source and never creates a candidate, window,
or sample.  Local cases reuse already-published K10 facts.  If no comparable local case
exists, an explicitly injected event-scoped evidence gateway may retrieve public historical
reports; its documents remain separate evidence with their original publication and fetch
times, rather than becoming a model-memory substitute.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from . import store
from .discovery import CompanyMappingDraft, DiscoveryDocument, EventDraft, EvidenceRef
from .schema import read_connection


class HistoricalEvidenceGateway(Protocol):
    """The existing Tavily verification gateway satisfies this narrow interface."""

    def fetch(
        self, *, event: EventDraft, retrieved_at: datetime, cutoff_at: datetime,
        cutoff_inclusive: bool = False,
    ) -> Any:
        ...


def _text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("历史案例 asOf 必须带时区")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _refs(refs: Sequence[EvidenceRef]) -> list[dict[str, Any]]:
    return [{"documentId": ref.document_id, "revision": ref.revision} for ref in refs]


def _labels(event: EventDraft) -> set[str]:
    """Use only explicit, structured topic/mechanism labels; never infer them from prose."""
    labels: set[str] = set()
    for key in ("topic", "topics", "theme", "themes", "mechanism", "mechanisms"):
        value = event.facts.get(key)
        values = value if isinstance(value, (list, tuple, set)) else (value,)
        for item in values:
            if isinstance(item, str) and item.strip():
                labels.add(item.strip().casefold())
    return labels


def _outcome(evaluation: Mapping[str, Any] | None) -> dict[str, Any]:
    if evaluation is None:
        return {"state": "unclassified", "closeLimitHitAny": None,
                "evaluationState": None, "gaps": [], "facts": {}}
    result = evaluation.get("result") if isinstance(evaluation.get("result"), Mapping) else {}
    state = str(evaluation.get("state"))
    hit = result.get("closeLimitHitAny")
    d1, d2 = result.get("d1"), result.get("d2")
    touched = [day.get("touchedLimitUp") for day in (d1, d2) if isinstance(day, Mapping)]
    if state == "incomplete" or not bool(result.get("due")):
        outcome = "incomplete"
    elif hit is True:
        outcome = "close_limit_hit"
    elif hit is False and True in touched:
        outcome = "touched_not_closed"
    elif hit is False and touched and all(value is False for value in touched):
        outcome = "not_touched"
    else:
        outcome = "unclassified"
    facts = {key: result.get(key) for key in (
        "d1", "d2", "d1OpenGap", "d1PriceChanges", "d2PriceChanges", "windowPriceChanges",
        "comparability", "firstTouchDay", "firstTouchStatus",
    )}
    return {"state": outcome, "closeLimitHitAny": hit if isinstance(hit, bool) else None,
            "evaluationState": state, "gaps": list(result.get("gaps") or []), "facts": facts}


def apply_historical_assessments(*, context: Mapping[str, Any], assessments: Any) -> dict[str, Any]:
    """Apply only source-quoted model classifications to already frozen historical cases.

    The comparison model may classify a public case, but it cannot invent a case, change a
    document reference, or turn a price movement into a conclusion without a verbatim source
    quote.  Omitted/invalid assessments deliberately remain ``unclassified``.
    """
    frozen = freeze_historical_context(context)
    if assessments is None:
        return frozen
    if isinstance(assessments, (str, bytes)) or not isinstance(assessments, Sequence):
        raise ValueError("historicalAssessments 必须是列表")
    by_id = {str(case["caseId"]): dict(case) for case in frozen["historicalCases"]}
    seen: set[str] = set()
    for raw in assessments:
        if not isinstance(raw, Mapping):
            raise ValueError("historicalAssessments 每项必须是对象")
        case_id, outcome, summary, quote = raw.get("caseId"), raw.get("outcome"), raw.get("summary"), raw.get("sourceQuote")
        if (not isinstance(case_id, str) or case_id not in by_id or case_id in seen
                or outcome not in {"success", "flat", "failure"}
                or not isinstance(summary, str) or not summary.strip()
                or not isinstance(quote, str) or not quote.strip()):
            raise ValueError("historicalAssessments 身份、结论或引文无效")
        case = by_id[case_id]
        description = case.get("sourceBasedDescription")
        if not isinstance(description, str) or quote not in description:
            raise ValueError("历史分类引文必须逐字存在于冻结来源说明")
        supplied_refs = raw.get("sourceRefs")
        if isinstance(supplied_refs, (str, bytes)) or not isinstance(supplied_refs, Sequence) or not supplied_refs:
            raise ValueError("历史分类必须引用冻结来源")
        allowed = {(ref.get("documentId"), ref.get("revision")) for ref in case.get("sourceRefs", ()) if isinstance(ref, Mapping)}
        refs: list[dict[str, Any]] = []
        for ref in supplied_refs:
            if not isinstance(ref, Mapping) or (ref.get("documentId"), ref.get("revision")) not in allowed:
                raise ValueError("历史分类引用超出冻结案例来源")
            refs.append({"documentId": ref["documentId"], "revision": ref["revision"]})
        case["outcome"] = outcome
        case["summary"] = summary.strip()
        case["categoryEvidence"] = [{"documentId": ref["documentId"], "revision": ref["revision"], "quote": quote} for ref in refs]
        by_id[case_id] = case
        seen.add(case_id)
    cases = [by_id[str(case["caseId"])] for case in frozen["historicalCases"]]
    requested = ["success", "flat", "failure"]
    present = sorted({str(case["outcome"]) for case in cases if case.get("outcome") in requested})
    coverage = dict(frozen["historicalCoverage"])
    coverage["presentOutcomes"] = present
    coverage["missingOutcomes"] = [outcome for outcome in requested if outcome not in present]
    coverage["state"] = "complete" if cases and not coverage["missingOutcomes"] else ("partial" if cases else "unavailable")
    coverage["reason"] = "all_requested_outcomes_covered" if coverage["state"] == "complete" else "historical_cases_found_but_requested_outcomes_missing"
    return freeze_historical_context({"historicalCases": cases, "historicalCoverage": coverage})


@dataclass
class HistoricalCaseLoader:
    db_path: Path
    gateway: HistoricalEvidenceGateway | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def _event_details(self, *, event_id: str, revision: int) -> dict[str, Any] | None:
        with read_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT e.stable_key,r.headline,r.event_kind,r.facts_json,r.source_refs_json,r.created_at "
                "FROM k10_events e JOIN k10_event_revisions r ON r.event_id=e.event_id "
                "WHERE r.event_id=? AND r.revision=?", (event_id, revision),
            ).fetchone()
        if row is None:
            return None
        return {"canonicalKey": row[0], "headline": row[1], "eventKind": row[2],
                "facts": json.loads(row[3]), "sourceRefs": json.loads(row[4]), "createdAt": row[5]}

    def _visible_evaluation(self, *, company_window_id: str, as_of: datetime) -> Mapping[str, Any] | None:
        """Return the latest evaluation and every referenced fact visible at ``as_of``.

        Read projections select current rows, which is unsafe for historical comparison.  This
        direct read is deliberately bounded to one immutable window and never performs DDL.
        """
        with read_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT revision,state,fact_refs_json,result_json,evaluated_at,created_at "
                "FROM k10_company_window_evaluation_revisions WHERE company_window_id=?",
                (company_window_id,),
            ).fetchall()
            visible: list[tuple[datetime, datetime, int, Any]] = []
            for row in rows:
                evaluated_at, created_at = _instant(row[4]), _instant(row[5])
                if evaluated_at is not None and created_at is not None and evaluated_at <= as_of and created_at <= as_of:
                    visible.append((evaluated_at, created_at, int(row[0]), row))
            if not visible:
                return None
            row = max(visible, key=lambda item: (item[0], item[1], item[2]))[3]
            fact_refs = json.loads(row[2])
            if not isinstance(fact_refs, list):
                return None
            result = json.loads(row[3])
            if not isinstance(result, Mapping) or ("factRefs" in result and result["factRefs"] != fact_refs):
                return None
            for ref in fact_refs:
                if not isinstance(ref, Mapping) or not isinstance(ref.get("companyCode"), str) or not isinstance(ref.get("tradeDate"), str) or not isinstance(ref.get("revision"), int):
                    return None
                fact = conn.execute(
                    "SELECT obtained_at,created_at FROM k10_market_day_fact_revisions WHERE company_code=? AND trade_date=? AND revision=?",
                    (ref["companyCode"], ref["tradeDate"], ref["revision"]),
                ).fetchone()
                if fact is None:
                    return None
                obtained_at, created_at = _instant(fact[0]), _instant(fact[1])
                if obtained_at is None or created_at is None or obtained_at > as_of or created_at > as_of:
                    return None
        return {"revision": int(row[0]), "state": row[1], "factRefs": fact_refs,
                "result": result, "evaluatedAt": row[4], "createdAt": row[5]}

    def _local_cases(self, *, event: EventDraft, as_of: datetime) -> list[dict[str, Any]]:
        current_labels = _labels(event)
        cases: list[dict[str, Any]] = []
        for opportunity in store.list_opportunities(db_path=self.db_path):
            available_at = _instant(opportunity.get("availableAt"))
            if available_at is None or available_at >= as_of:
                continue
            details = self._event_details(event_id=str(opportunity["eventId"]), revision=int(opportunity["eventRevision"]))
            if details is None:
                continue
            try:
                frozen_documents = store.load_document_versions(refs=details["sourceRefs"], db_path=self.db_path)
            except (TypeError, ValueError):
                continue
            if len(frozen_documents) != len(details["sourceRefs"]) or any(
                (fetched := _instant(document.get("fetchedAt"))) is None or fetched > as_of
                for document in frozen_documents
            ):
                # Never let a later document revision or late acquisition rewrite the
                # evidence available to an earlier historical comparison.
                continue
            historic_labels = _labels(EventDraft(
                canonical_key=str(details["canonicalKey"]), stage_key=str(opportunity["catalystStage"]), event_state="historical",
                headline=str(details["headline"]), event_kind=str(details["eventKind"]), facts=dict(details["facts"]),
                source_refs=tuple(EvidenceRef(str(ref["documentId"]), int(ref["revision"])) for ref in details["sourceRefs"]),
            ))
            canonical_match = details["canonicalKey"] == event.canonical_key
            mechanism_match = bool(current_labels and historic_labels and current_labels & historic_labels)
            # Stage means only a position in a chain; by itself it is never a comparable
            # mechanism.  A canonical identity or explicit topic/mechanism match is required.
            if not (canonical_match or mechanism_match):
                continue
            relation: list[str] = []
            if canonical_match:
                relation.append("sameCanonicalKey")
            if opportunity["catalystStage"] == event.stage_key:
                relation.append("sameStage")
            if mechanism_match:
                relation.append("sameTopicOrMechanism")
            evaluation = self._visible_evaluation(company_window_id=str(opportunity["companyWindowId"]), as_of=as_of)
            market_facts = []
            if evaluation is not None and isinstance(evaluation.get("result"), Mapping):
                market_facts = list(evaluation["factRefs"])
            refs = [dict(ref) for ref in details["sourceRefs"]]
            cases.append({
                "caseId": f"{opportunity['opportunityId']}@{opportunity['eventRevision']}",
                "outcome": "unclassified", "summary": details["headline"], "observedAt": opportunity["availableAt"],
                "sourceRefs": refs, "marketFacts": market_facts,
                "sourceBasedDescription": details["headline"],
                "categoryEvidence": refs,
                "observedFacts": {"relation": relation, "stage": opportunity["catalystStage"],
                                  "companyCode": opportunity["companyCode"], "outcome": _outcome(evaluation)},
            })
        return cases

    def _directed_evidence(self, *, event: EventDraft, as_of: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if self.gateway is None:
            return [], {"state": "not_requested", "reason": "historical_gateway_not_configured"}
        labels = sorted(_labels(event))
        terms = " ".join((event.headline, *labels, event.stage_key, "历史案例 成功 失败 平淡 公开报道"))[:400]
        query_event = EventDraft(
            canonical_key=f"historical:{event.canonical_key}", stage_key=event.stage_key,
            event_state="historical_lookup", headline=terms, event_kind=event.event_kind,
            facts={"historicalFor": event.canonical_key, "topicMechanismLabels": labels}, source_refs=event.source_refs,
        )
        bundle = self.gateway.fetch(event=query_event, retrieved_at=self.clock(), cutoff_at=as_of, cutoff_inclusive=False)
        documents = tuple(getattr(bundle, "eligible_documents", ()))
        coverage = dict(getattr(bundle, "coverage", {}))
        cases: list[dict[str, Any]] = []
        for document in documents:
            published = _instant(document.published_at)
            if published is None or published >= as_of:
                continue
            ref = document.evidence_ref
            description = (document.excerpt or document.original_text or "").strip()
            if not description:
                continue
            cases.append({
                "caseId": f"external:{ref.document_id}@{ref.revision}", "outcome": "unclassified",
                "summary": str(document.metadata.get("title") or event.headline),
                "observedAt": document.published_at, "sourceRefs": _refs((ref,)), "marketFacts": [],
                "sourceBasedDescription": description, "categoryEvidence": [{"documentId": ref.document_id,
                    "revision": ref.revision, "text": description}],
                "observedFacts": {"relation": ["directedEvidence"], "stage": "external_unclassified"},
            })
        coverage["query"] = terms
        coverage["directedDocuments"] = len(cases)
        return cases, coverage

    def load(self, *, event: EventDraft, mappings: Sequence[CompanyMappingDraft], as_of: datetime) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise ValueError("历史案例 asOf 必须带时区")
        local = self._local_cases(event=event, as_of=as_of)
        external: list[dict[str, Any]] = []
        requested = ["success", "flat", "failure"]
        local_present = {str(item["outcome"]) for item in local if item["outcome"] in requested}
        coverage: dict[str, Any] = {"localCases": len(local), "gateway": "not_needed"}
        # Even one local case is not sufficient evidence for every requested outcome.
        if set(requested) - local_present:
            external, gateway_coverage = self._directed_evidence(event=event, as_of=as_of)
            coverage = {"localCases": len(local), "gateway": gateway_coverage.pop("state", "unknown"), **gateway_coverage}
        cases = sorted([*local, *external], key=lambda item: (str(item["observedAt"]), str(item["caseId"])))
        present = sorted({str(item["outcome"]) for item in cases if item["outcome"] in requested})
        missing = [outcome for outcome in requested if outcome not in present]
        # Coverage describes the evidence set as a whole. Several comparable companies may
        # legitimately point at the same event document. Keep every case's own refs, but
        # dedupe the aggregate coverage list in first-seen order.
        refs: list[dict[str, Any]] = []
        seen_refs: set[tuple[str, int]] = set()
        for item in cases:
            for ref in item["sourceRefs"]:
                key = (str(ref["documentId"]), int(ref["revision"]))
                if key not in seen_refs:
                    seen_refs.add(key)
                    refs.append(dict(ref))
        status = "complete" if cases and not missing else ("partial" if cases else "unavailable")
        reason = "all_requested_outcomes_covered" if status == "complete" else (
            "historical_cases_found_but_requested_outcomes_missing" if cases else str(coverage.get("reason", "no_historical_cases"))
        )
        return freeze_historical_context({"historicalCases": cases, "historicalCoverage": {
            "state": status, "requestedOutcomes": requested, "presentOutcomes": present,
            "missingOutcomes": missing, "reason": reason, "sourceRefs": refs,
        }})


def freeze_historical_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Return a minimal immutable, traceable context suitable for a frozen comparison."""
    required = {"historicalCases", "historicalCoverage"}
    if not isinstance(context, Mapping) or set(context) != required:
        raise ValueError("历史案例上下文字段不完整")
    coverage = context["historicalCoverage"]
    if not isinstance(context["historicalCases"], list) or not isinstance(coverage, Mapping):
        raise ValueError("历史案例上下文结构无效")
    if set(coverage) != {"state", "requestedOutcomes", "presentOutcomes", "missingOutcomes", "reason", "sourceRefs"} or coverage.get("state") not in {"complete", "partial", "unavailable"}:
        raise ValueError("历史案例覆盖面无效")
    refs: list[dict[str, Any]] = []
    seen_refs: set[tuple[str, int]] = set()
    for item in coverage["sourceRefs"]:
        if not isinstance(item, Mapping) or not isinstance(item.get("documentId"), str) or not isinstance(item.get("revision"), int):
            raise ValueError("历史案例包含不可追溯资料")
        key = (item["documentId"], item["revision"])
        if key not in seen_refs:
            seen_refs.add(key)
            refs.append({"documentId": item["documentId"], "revision": item["revision"]})
    case_ids: set[str] = set()
    for case in context["historicalCases"]:
        if not isinstance(case, Mapping) or not isinstance(case.get("caseId"), str) or case["caseId"] in case_ids:
            raise ValueError("历史案例身份无效")
        case_ids.add(case["caseId"])
        if case.get("outcome") not in {"success", "flat", "failure", "unclassified"} or _instant(case.get("observedAt")) is None or not isinstance(case.get("sourceRefs"), list):
            raise ValueError("历史案例时间或来源无效")
        if not isinstance(case.get("sourceBasedDescription"), str) or not case["sourceBasedDescription"].strip():
            raise ValueError("历史案例缺少基于来源的说明")
    return {"historicalCases": [dict(item) for item in context["historicalCases"]],
            "historicalCoverage": {"state": coverage["state"],
                "requestedOutcomes": [str(item) for item in coverage["requestedOutcomes"]],
                "presentOutcomes": [str(item) for item in coverage["presentOutcomes"]],
                "missingOutcomes": [str(item) for item in coverage["missingOutcomes"]],
                "reason": str(coverage["reason"]), "sourceRefs": refs}}


def make_historical_context_loader(*, db_path: Path, gateway: HistoricalEvidenceGateway | None = None,
                                   clock: Callable[[], datetime] | None = None) -> HistoricalCaseLoader:
    return HistoricalCaseLoader(db_path=db_path, gateway=gateway, clock=clock or (lambda: datetime.now(timezone.utc)))


__all__ = ["HistoricalCaseLoader", "HistoricalEvidenceGateway", "apply_historical_assessments", "freeze_historical_context", "make_historical_context_loader"]
