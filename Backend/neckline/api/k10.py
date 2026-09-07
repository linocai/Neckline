"""Authenticated, read-first K10-v1.4 API.

The API projects immutable publications and fixed company windows.  GET paths
only open the explicitly supplied database read-only and never start a task or
freeze a selection; the two scheduled writers own those transitions.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import date, datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status

from neckline.k10 import SchemaUnavailable, validate_run_config
from neckline.k10 import store
from neckline.k10.market_context import MarketContextError, collect_market_context
from neckline.k10.evaluation import EvaluationInputError, evaluate_company_window, evaluation_state
from neckline.k10.schema import read_connection, require_schema

from .k10_schemas import (
    AnalysisArtifactOut,
    AnalysisEventLineage,
    AnalysisInputLineage,
    ApiFailure,
    CandidateComparison,
    CommonFactOut,
    CompanyWindowEvaluationOut,
    CompanyWindowListOut,
    CompanyWindowOut,
    ConfigurationOut,
    ConfigurationScopeOut,
    EvaluationMetricsOut,
    Evidence,
    JobOut,
    JobRetryIn,
    LifecycleEventOut,
    MarketDayOut,
    OpportunityDetail,
    OpportunityListOut,
    OpportunityOut,
    PageMeta,
    PublicationListOut,
    PublicationOut,
    PublicationSampleOut,
    ResultsOut,
    ResultsCohortOut,
    ResultsEventGroupOut,
    SCHEMA_VERSION,
    ScanOut,
    SelectionActionIn,
    SelectionActionOut,
    SelectionDetailOut,
    SelectionListOut,
    SelectionSnapshotOut,
    SourceDocumentPageOut,
    SourceReference,
)


DbPathProvider = Callable[[], Path]
TokenDependency = Callable[..., None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("K10 时间必须带时区")
    return parsed


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{sha256(chr(31).join(parts).encode('utf-8')).hexdigest()[:32]}"


def _json(value: str | None, default: Any) -> Any:
    return default if not value else json.loads(value)


def _unavailable(exc: SchemaUnavailable) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={
        "reason": "not_configured", "message": str(exc), "missing": ["k10Schema"],
    })


@contextmanager
def _reader(path: Path) -> Iterator[Any]:
    try:
        with read_connection(path) as conn:
            require_schema(conn)
            yield conn
    except SchemaUnavailable as exc:
        raise _unavailable(exc) from exc


def _not_found(label: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"reason": "not_found", "message": label})


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"reason": "conflict", "message": message})


def _page(values: list[Any], cursor: str | None, limit: int, identity: Callable[[Any], str]) -> tuple[list[Any], str | None]:
    start = 0
    if cursor is not None:
        start_at = next((index for index, item in enumerate(values) if identity(item) == cursor), None)
        if start_at is None:
            raise HTTPException(status_code=422, detail={"reason": "invalid_cursor", "message": "分页游标不存在于当前结果"})
        start = start_at + 1
    page = values[start:start + limit]
    next_cursor = identity(page[-1]) if page and start + limit < len(values) else None
    return page, next_cursor


def _source_ref(value: Mapping[str, Any]) -> SourceReference:
    market_snapshot = _market_snapshot_source_ref(value)
    if market_snapshot is not None:
        return market_snapshot
    raw_url = value.get("url") or value.get("canonicalUrl")
    # market-data is an internal snapshot identifier, not an external link.
    # Invalid identifiers remain in frozen task input but get no public source
    # projection that could look authoritative to a reader.
    if isinstance(raw_url, str) and raw_url.startswith("market-data:"):
        return SourceReference()
    revision = value.get("revision") or value.get("documentRevision")
    return SourceReference(
        documentId=value.get("documentId") or value.get("document_id"),
        factId=value.get("factId") or value.get("fact_id"), companyCode=value.get("companyCode") or value.get("company_code"),
        tradeDate=value.get("tradeDate") or value.get("trade_date"),
        revision=int(revision) if isinstance(revision, int | str) and str(revision).isdigit() else None,
        sourceKey=value.get("sourceKey") or value.get("source_key") or value.get("source"), title=value.get("title"),
        url=value.get("url") or value.get("canonicalUrl"), excerpt=value.get("excerpt"),
        publishedAt=value.get("publishedAt") or value.get("published_at"),
        publishedPrecision=value.get("publishedPrecision") or value.get("published_precision") or "unknown",
        fetchedAt=value.get("fetchedAt") or value.get("fetched_at") or value.get("obtainedAt") or value.get("obtained_at"),
    )


_MARKET_SNAPSHOT_TABLES = {"daily": "日行情快照", "adj_factor": "复权因子快照"}
_TS_CODE = re.compile(r"^[0-9]{6}\.(?:SZ|SH|BJ)$")


def _aware_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if parsed.tzinfo is not None else None


def _iso_date(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return value if date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def _market_snapshot_source_ref(value: Mapping[str, Any]) -> SourceReference | None:
    """Project only well-formed frozen parquet snapshot identities for readers."""
    raw_url = value.get("url") or value.get("canonicalUrl")
    if not isinstance(raw_url, str) or not raw_url.startswith("market-data:"):
        return None
    try:
        parsed = urlsplit(raw_url)
        port = parsed.port
    except ValueError:
        return None
    table, parts = parsed.netloc, [part for part in parsed.path.split("/") if part]
    if (parsed.scheme != "market-data" or parsed.username or parsed.password or port is not None
            or parsed.query or parsed.fragment or table not in _MARKET_SNAPSHOT_TABLES):
        return None
    if table == "daily":
        if len(parts) != 2:
            return None
        url_date, code = _iso_date(parts[0]), parts[1]
        if url_date is None:
            return None
    else:  # adj_factor snapshots identify a company, not one daily bar.
        if len(parts) != 1:
            return None
        url_date, code = None, parts[0]
    if not _TS_CODE.fullmatch(code):
        return None
    explicit_code = value.get("companyCode") or value.get("company_code")
    if explicit_code is not None and (not isinstance(explicit_code, str) or explicit_code.upper() != code):
        return None
    explicit_date = value.get("tradeDate") or value.get("trade_date")
    if explicit_date is not None:
        explicit_date = _iso_date(explicit_date)
        if explicit_date is None or (url_date is not None and explicit_date != url_date):
            return None
    trade_date = url_date or explicit_date
    label = _MARKET_SNAPSHOT_TABLES[table]
    title = f"{label} · {code}" + (f" · {trade_date}" if trade_date else "")
    return SourceReference(
        companyCode=code, tradeDate=trade_date, sourceKey="market_snapshot", title=title, url=raw_url,
        fetchedAt=_aware_text(value.get("dataFetchedAt")), collectedAt=_aware_text(value.get("collectedAt")),
    )


def _document_reference_map(conn: Any, refs: list[Mapping[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    """Load only the document revisions named by a presentation reference."""
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for ref in refs:
        document_id, revision = ref.get("documentId") or ref.get("document_id"), ref.get("revision") or ref.get("documentRevision")
        if not isinstance(document_id, str) or not isinstance(revision, int):
            continue
        row = conn.execute(
            "SELECT d.source_key,d.canonical_url,v.published_at,v.published_precision,v.fetched_at,v.excerpt,v.metadata_json "
            "FROM k10_source_documents d JOIN k10_source_document_versions v ON v.document_id=d.document_id "
            "WHERE d.document_id=? AND v.revision=?", (document_id, revision),
        ).fetchone()
        if row is None:
            continue
        metadata = _json(row[6], {})
        result[(document_id, revision)] = {"documentId": document_id, "revision": revision, "sourceKey": row[0],
            "url": row[1], "publishedAt": row[2], "publishedPrecision": row[3], "fetchedAt": row[4],
            "excerpt": row[5], "title": metadata.get("title") if isinstance(metadata, Mapping) else None}
    return result


def _hydrate_source_ref(value: Mapping[str, Any], documents: Mapping[tuple[str, int], Mapping[str, Any]]) -> SourceReference:
    document_id, revision = value.get("documentId") or value.get("document_id"), value.get("revision") or value.get("documentRevision")
    exact = documents.get((document_id, revision)) if isinstance(document_id, str) and isinstance(revision, int) else None
    return _source_ref({**dict(exact or {}), **dict(value)})


def _evidence(value: Mapping[str, Any], documents: Mapping[tuple[str, int], Mapping[str, Any]] | None = None) -> Evidence:
    raw_source = value.get("sourceRef") or value.get("source_ref") or value
    source = raw_source if isinstance(raw_source, Mapping) else {}
    return Evidence(sourceRef=_hydrate_source_ref(source, documents or {}),
                    claim=str(value.get("claim") or value.get("summary") or ""),
                    relation=value.get("relation"), uncertainty=value.get("uncertainty"))


def _comparison(value: Any) -> CandidateComparison:
    payload = value if isinstance(value, Mapping) else {}
    differences = payload.get("differences") if isinstance(payload.get("differences"), Mapping) else {}
    rank = payload.get("rank")
    return CandidateComparison(summary=payload.get("summary"), rationale=payload.get("rationale") or payload.get("reason"),
                               rank=rank if isinstance(rank, int) and not isinstance(rank, bool) else None,
                               priorityReason=differences.get("priorityReason"), gap=differences.get("gap"),
                               rankChangeConditions=differences.get("rankChangeConditions"),
                               twoDayReason=differences.get("twoDayReason"))


def _common_facts(value: Any) -> list[CommonFactOut]:
    """Turn variable event facts into a stable, display-first API list."""
    if not isinstance(value, Mapping):
        return []
    verification = value.get("verification")
    summary = verification.get("summary") if isinstance(verification, Mapping) else None
    if isinstance(summary, str) and summary.strip():
        # Production discovery facts include a verified natural-language
        # conclusion alongside machine-facing design/stage/coverage fields.
        # The card needs that conclusion once; the frozen detail remains
        # inspectable in rawDetail without presenting implementation keys.
        state = verification.get("state")
        label = "待核事实" if state == "needs_review" else "共同事实"
        return [CommonFactOut(key=label, text=summary.strip(), rawDetail=dict(value))]
    facts: list[CommonFactOut] = []
    for key, raw in value.items():
        detail = dict(raw) if isinstance(raw, Mapping) else None
        if isinstance(raw, str):
            text = raw
        elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
            text = str(raw)
        elif isinstance(raw, Mapping):
            candidate = next((raw.get(field) for field in ("text", "summary", "claim", "headline", "value")
                              if isinstance(raw.get(field), str) and raw.get(field).strip()), None)
            text = candidate if isinstance(candidate, str) else json.dumps(raw, ensure_ascii=False, sort_keys=True)
        elif isinstance(raw, list):
            texts = [item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, sort_keys=True) for item in raw]
            text = "；".join(texts)
        else:
            text = json.dumps(raw, ensure_ascii=False, sort_keys=True)
        if text.strip():
            facts.append(CommonFactOut(key=str(key), text=text, rawDetail=detail))
    return facts


class _TuShareHtmlText(HTMLParser):
    """Presentation-only HTML to readable paragraphs, without fetching anything."""

    _BLOCKS = {"p", "div", "br", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}
    _IGNORED = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0

    def _paragraph_break(self) -> None:
        if self._parts and not self._parts[-1].endswith("\n\n"):
            self._parts.append("\n\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in self._IGNORED:
            self._ignored_depth += 1
        elif self._ignored_depth == 0 and lowered in self._BLOCKS:
            self._paragraph_break()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._ignored_depth == 0 and tag.lower() in self._BLOCKS:
            self._paragraph_break()

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self._IGNORED:
            self._ignored_depth = max(0, self._ignored_depth - 1)
        elif self._ignored_depth == 0 and lowered in self._BLOCKS:
            self._paragraph_break()

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self._parts.append(data)

    def readable_text(self) -> str:
        paragraphs = [" ".join(value.split()) for value in "".join(self._parts).split("\n\n")]
        return "\n\n".join(value for value in paragraphs if value)


def _document_body(source_key: str, original_text: str | None) -> str:
    """Keep frozen source bytes intact; sanitize only TuShare HTML for reading."""
    text = original_text or ""
    if source_key != "tushare-major-news" or "<" not in text or ">" not in text:
        return text
    parser = _TuShareHtmlText()
    parser.feed(text)
    parser.close()
    return parser.readable_text()


def _company_name(conn: Any, company_code: str) -> str | None:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_basic'").fetchone() is None:
        return None
    row = conn.execute("SELECT name FROM stock_basic WHERE ts_code=?", (company_code,)).fetchone()
    return str(row[0]) if row and row[0] else None


def _windows(path: Path) -> dict[str, dict[str, Any]]:
    return {item["companyWindowId"]: item for item in store.list_company_windows(db_path=path)}


def _opportunity_lifecycle(path: Path, value: Mapping[str, Any]) -> str:
    state = str(value.get("state") or "active")
    if state == "withdrawn":
        return "withdrawal"
    if state == "expired":
        return "expired"
    events = store.list_opportunity_lifecycle_events(opportunity_id=str(value["opportunityId"]), db_path=path)
    # A terminal lifecycle fact always wins over a display-only update.  Store
    # state is intentionally append-only and may still say active here.
    terminals = [str(item.get("kind")) for item in events if item.get("kind") in {"withdrawal", "expired"}]
    if "withdrawal" in terminals:
        return "withdrawal"
    if "expired" in terminals:
        return "expired"

    lifecycle = "published"
    risk_active = False
    for event in events:
        kind = str(event.get("kind") or "")
        if kind == "risk":
            risk_active = True
            lifecycle = "risk"
            continue
        if kind != "evidence_update":
            continue
        content = event.get("content")
        verified_current = (
            isinstance(content, Mapping)
            and content.get("reasonStatus") == "current"
            and content.get("sourceStatus") == "complete"
        )
        # An ordinary continuation must not implicitly clear a material risk.
        # Only the validated morning-review outcome says the reason is current
        # and its source coverage is complete.
        if risk_active and not verified_current:
            continue
        risk_active = False
        lifecycle = "evidence_update"
    return lifecycle


def _opportunity(value: Mapping[str, Any], windows: Mapping[str, Mapping[str, Any]], company_name: str | None = None,
                 path: Path | None = None) -> OpportunityOut:
    window = windows.get(str(value["companyWindowId"]), {})
    lifecycle = _opportunity_lifecycle(path, value) if path is not None else {"active": "published", "withdrawn": "withdrawal", "expired": "expired"}.get(value.get("state"), "published")
    return OpportunityOut(opportunityId=str(value["opportunityId"]), opportunityKey=str(value["opportunityKey"]),
                          companyCode=str(value["companyCode"]), companyName=company_name,
                          eventId=str(value["eventId"]), eventRevision=int(value["eventRevision"]),
                          catalystStage=str(value["catalystStage"]), relatedOpportunityId=value.get("relatedOpportunityId"),
                          companyWindowId=str(value["companyWindowId"]), firstBatchId=str(value["firstBatchId"]),
                          availableAt=str(value["availableAt"]), sourceMarker=value.get("sourceMarker"),
                          latePublication=value.get("latePublication"), d0TradeDate=str(value["d0TradeDate"]),
                          d1TradeDate=str(value["d1TradeDate"]), d2TradeDate=str(value["d2TradeDate"]),
                          sampleClass=str(value["sampleClass"]), overlapsWindowId=window.get("overlapsWindowId"),
                          lifecycle=lifecycle, createdAt=str(value["createdAt"]))


def _sample(value: Mapping[str, Any], batch_id: str, company_name: str | None = None,
            documents: Mapping[tuple[str, int], Mapping[str, Any]] | None = None) -> PublicationSampleOut:
    evidence = value.get("evidenceRefs")
    return PublicationSampleOut(sampleId=str(value["sampleId"]), batchId=batch_id,
                                companyWindowId=str(value["companyWindowId"]), opportunityId=str(value["opportunityId"]),
                                companyCandidateId=str(value["candidateId"]), companyCode=str(value["companyCode"]),
                                companyName=company_name, eventId=str(value["eventId"]), eventRevision=int(value["eventRevision"]),
                                category=str(value["category"]), sourceMarker=str(value["sourceMarker"]),
                                comparison=_comparison(value.get("comparison")),
                                evidence=[_evidence(item, documents) for item in evidence if isinstance(item, Mapping)] if isinstance(evidence, list) else [],
                                rank=value.get("rank") if isinstance(value.get("rank"), int) else None,
                                createdAt=str(value["createdAt"]))


def _all_samples(path: Path) -> list[PublicationSampleOut]:
    batches = store.list_publication_batches(db_path=path)
    with _reader(path) as conn:
        rows = [(sample, str(batch["batchId"]))
                for batch in batches for sample in store.list_publication_samples(batch_id=str(batch["batchId"]), db_path=path)]
        refs = [ref for sample, _ in rows for ref in sample.get("evidenceRefs", []) if isinstance(ref, Mapping)]
        documents = _document_reference_map(conn, refs)
        return [_sample(sample, batch_id, _company_name(conn, str(sample["companyCode"])), documents)
                for sample, batch_id in rows]


def _publication(value: Mapping[str, Any], path: Path) -> PublicationOut:
    return PublicationOut(batchId=str(value["batchId"]), scanId=str(value["scanId"]), publicationKind=str(value["publicationKind"]),
                          availableAt=str(value["availableAt"]), createdAt=str(value["createdAt"]),
                          sampleCount=len(store.list_publication_samples(batch_id=str(value["batchId"]), db_path=path)))


def _window_selection(path: Path, company_window_id: str) -> Mapping[str, Any] | None:
    """Project one company-window decision; this read never creates its D1 snapshot."""
    selection = store.get_company_window_selection(company_window_id=company_window_id, db_path=path)
    if selection is None or "lastActionAt" in selection:
        return selection
    # Keep presentation timing derived from the append-only command itself;
    # this is deliberately not inferred from the immutable freeze snapshot.
    action_id = selection.get("lastActionId")
    if not isinstance(action_id, str):
        return {**selection, "lastActionAt": None, "postFreeze": False}
    with _reader(path) as conn:
        row = conn.execute(
            "SELECT created_at FROM k10_company_window_actions WHERE action_id=? "
            "UNION ALL SELECT created_at FROM k10_candidate_actions WHERE action_id=? LIMIT 1",
            (action_id, action_id),
        ).fetchone()
        window = conn.execute("SELECT d1_selection_at FROM k10_company_windows WHERE company_window_id=?", (company_window_id,)).fetchone()
    action_at = str(row[0]) if row and row[0] else None
    try:
        post_freeze = bool(action_at and window and _instant(action_at) >= _instant(str(window[0])))
    except ValueError:
        post_freeze = False
    return {**selection, "lastActionAt": action_at, "postFreeze": post_freeze}


def _job_out(conn: Any, task_id: str | None) -> JobOut | None:
    if not task_id:
        return None
    row = conn.execute("SELECT task_id,kind,status,stage,attempt_count,input_version,input_cutoff_at,created_at,updated_at,error_text FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()
    if row is None:
        return None
    failure = ApiFailure(reason="task_failed", message=str(row[9])) if row[9] else None
    return JobOut(jobId=str(row[0]), kind=str(row[1]), status=str(row[2]), stage=str(row[3]), attemptCount=int(row[4]),
                  inputVersion=str(row[5]), inputCutoffAt=str(row[6]), createdAt=str(row[7]), updatedAt=str(row[8]), error=failure)


def _analyses(conn: Any, observation_id: str) -> list[AnalysisArtifactOut]:
    rows = conn.execute("SELECT analysis_id,revision,analysis_kind,input_cutoff_at,input_lineage_json,content_json,status FROM k10_analysis_revisions WHERE observation_id=? ORDER BY revision,analysis_kind", (observation_id,)).fetchall()
    references: list[Mapping[str, Any]] = []
    for row in rows:
        lineage, content = _json(row[4], {}), _json(row[5], {})
        if isinstance(content, Mapping): references.extend(item for item in content.get("sourceRefs", []) if isinstance(item, Mapping))
        raw = content.get("inputLineage", lineage) if isinstance(content, Mapping) else lineage
        if isinstance(raw, Mapping): references.extend(item for item in raw.get("documentVersions", []) if isinstance(item, Mapping))
    documents = _document_reference_map(conn, references)
    values: list[AnalysisArtifactOut] = []
    for row in rows:
        lineage, content = _json(row[4], {}), _json(row[5], {})
        raw_lineage = content.get("inputLineage", lineage) if isinstance(content, Mapping) else lineage
        raw_lineage = raw_lineage if isinstance(raw_lineage, Mapping) else {}
        event = raw_lineage.get("event")
        usage = content.get("usage") if isinstance(content, Mapping) else None
        values.append(AnalysisArtifactOut(
            analysisId=str(row[0]), observationId=observation_id, revision=int(row[1]), role=str(row[2]), status=str(row[6]),
            inputCutoffAt=str(row[3]), sourceRefs=[_hydrate_source_ref(item, documents) for item in content.get("sourceRefs", []) if isinstance(item, Mapping)] if isinstance(content, Mapping) else [],
            inputLineage=AnalysisInputLineage(candidateId=raw_lineage.get("candidateId"),
                event=AnalysisEventLineage.model_validate(event) if isinstance(event, Mapping) else None,
                mappingIds=[str(item) for item in raw_lineage.get("mappingIds", [])],
                documentVersions=[_hydrate_source_ref(item, documents) for item in raw_lineage.get("documentVersions", []) if isinstance(item, Mapping)],
                inputCutoffAt=raw_lineage.get("inputCutoffAt") or str(row[3]),
                marketContext=raw_lineage.get("marketContext") if isinstance(raw_lineage.get("marketContext"), Mapping) else None,
                proAnalysis=raw_lineage.get("proAnalysis") if isinstance(raw_lineage.get("proAnalysis"), Mapping) else None),
            fullText=content.get("fullText") if isinstance(content, Mapping) else None,
            provider=content.get("provider") if isinstance(content, Mapping) else None,
            model=content.get("model") if isinstance(content, Mapping) else None,
            promptVersion=content.get("promptVersion") if isinstance(content, Mapping) else None,
            usage=usage if isinstance(usage, Mapping) else None,
            error=str(content.get("error")) if isinstance(content, Mapping) and content.get("error") else None,
        ))
    return values


def _selection_detail(path: Path, company_window_id: str) -> SelectionDetailOut:
    selection = _window_selection(path, company_window_id)
    if selection is None:
        raise _not_found("公司观察窗口不存在")
    windows = _windows(path)
    with _reader(path) as conn:
        opportunities = [_opportunity(item, windows, _company_name(conn, str(item["companyCode"])), path)
                         for item in store.list_opportunities(db_path=path) if item["companyWindowId"] == company_window_id]
        observation_id = selection["observationId"]
        task_id = selection["taskId"]
        analyses = _analyses(conn, observation_id) if observation_id else []
        job = _job_out(conn, task_id)
    return SelectionDetailOut(companyWindowId=company_window_id,
                              representativeCandidateId=selection["representativeCandidateId"],
                              state=selection["currentState"], observationId=observation_id,
                              lastActionAt=selection.get("lastActionAt"), postFreeze=bool(selection.get("postFreeze", False)),
                              opportunities=opportunities, analyses=analyses,
                              analysisJobId=task_id, latestJob=job)


def _lifecycle_events(path: Path, opportunity_id: str) -> list[LifecycleEventOut]:
    rows = store.list_opportunity_lifecycle_events(opportunity_id=opportunity_id, db_path=path)
    with _reader(path) as conn:
        documents = _document_reference_map(conn, [item for row in rows for item in row.get("sourceRefs", []) if isinstance(item, Mapping)])
    return [LifecycleEventOut(lifecycleEventId=str(row["lifecycleEventId"]), kind=str(row["kind"]), reason=row.get("reason"),
                              sourceRefs=[_hydrate_source_ref(item, documents) for item in row.get("sourceRefs", []) if isinstance(item, Mapping)],
                              content=row["content"] if isinstance(row.get("content"), Mapping) else {},
                              occurredAt=str(row["occurredAt"]), createdAt=str(row["createdAt"])) for row in rows]


def _market_day(value: Mapping[str, Any]) -> MarketDayOut | None:
    if value.get("marketClosed") is False:
        return None
    return MarketDayOut(tradeDate=str(value["tradeDate"]), availability=str(value["availability"]),
                        closeLimitUp=value.get("closeLimitUp"), touchedLimitUp=value.get("touchedLimitUp"),
                        firstTouchedAt=value.get("firstTouchedAt"), open=value.get("open"), high=value.get("high"),
                        low=value.get("low"), close=value.get("close"), preClose=value.get("preClose"),
                        limitUpPrice=value.get("limitUpPrice"),
                        sourceRefs=[_source_ref(item) for item in value.get("sourceRefs", []) if isinstance(item, Mapping)],
                        obtainedAt=value.get("obtainedAt"))


def _evaluation_fact_refs(path: Path, company_code: str, refs: list[Mapping[str, Any]]) -> list[SourceReference]:
    facts = {(str(item["factId"]), int(item["revision"])): item
             for item in store.list_market_day_facts(company_code=company_code, db_path=path)}
    values: list[SourceReference] = []
    for ref in refs:
        fact_id, revision = ref.get("factId"), ref.get("revision")
        exact = facts.get((fact_id, revision)) if isinstance(fact_id, str) and isinstance(revision, int) else None
        # Fact references are market identities, never source documents.  Keep
        # source metadata only as context; no document URL is invented.
        values.append(_source_ref({**dict(ref), **({"source": "market", "obtainedAt": exact.get("obtainedAt"),
            "companyCode": exact.get("companyCode"), "tradeDate": exact.get("tradeDate"), "factId": exact.get("factId"),
            "revision": exact.get("revision")} if exact else {"source": "market"})}))
    return values


def _evaluation_records(path: Path) -> list[CompanyWindowEvaluationOut]:
    rows = store.list_company_window_evaluations(db_path=path)
    windows = _windows(path)
    latest_rows = {str(row["companyWindowId"]): dict(row) for row in rows}
    # Results are a read model over every published company window.  A delayed
    # writer cannot erase a live sample: pending rows and absent rows are
    # recomputed from immutable windows plus the latest persisted day facts.
    for window_id, window in windows.items():
        row = latest_rows.get(window_id)
        if row is not None and row.get("state") not in {"pending", "due"}:
            continue
        try:
            projected = evaluate_company_window(window=window,
                                                 market_facts=store.list_market_day_facts(company_code=str(window["companyCode"]), db_path=path),
                                                 as_of=_now())
        except EvaluationInputError:
            continue
        result = projected.to_dict()
        latest_rows[window_id] = {
            "companyWindowId": window_id, "revision": int(row["revision"]) if row else 0,
            "state": evaluation_state(projected), "factRefs": result.get("factRefs", []), "result": result,
            "evaluatedAt": _now(), "createdAt": row.get("createdAt") if row else _now(),
        }
    rows = list(latest_rows.values())
    opportunity_ids = {window_id: [item["opportunityId"] for item in store.list_opportunities(db_path=path) if item["companyWindowId"] == window_id]
                       for window_id in {str(row["companyWindowId"]) for row in rows}}
    records = []
    for row in rows:
        result = row["result"] if isinstance(row.get("result"), Mapping) else {}
        window = windows.get(str(row["companyWindowId"]), {})
        frozen = _window_selection(path, str(row["companyWindowId"]))
        selection = None if frozen is None or frozen["snapshotState"] is None else SelectionSnapshotOut(
            state={"kept": "selected", "skipped": "skipped", "unhandled": "unhandled"}[frozen["snapshotState"]],
            actionIds=[str(item) for item in frozen["snapshotActionIds"]], frozenAt=str(frozen["frozenAt"]))
        records.append(CompanyWindowEvaluationOut(companyWindowId=str(row["companyWindowId"]), opportunityIds=opportunity_ids[str(row["companyWindowId"])], companyCode=str(window.get("companyCode") or result.get("companyCode") or ""),
            sampleClass=str(window.get("sampleClass") or result.get("sampleClass") or "primary"), selection=selection, state=str(row["state"]), revision=int(row["revision"]), updatedAt=str(row["evaluatedAt"]),
            d1=_market_day(result["d1"]) if isinstance(result.get("d1"), Mapping) else None,
            d2=_market_day(result["d2"]) if isinstance(result.get("d2"), Mapping) else None,
            primaryEligible=bool(result.get("primaryEligible", False)), closeLimitHitAny=result.get("closeLimitHitAny"),
            firstTouchDay=result.get("firstTouchDay"), firstTouchStatus=result.get("firstTouchStatus"),
            knownTouchDays=[item for item in result.get("knownTouchDays", []) if item in {"D1", "D2"}],
            d1OpenGap=result.get("d1OpenGap"), d1PriceChanges=result.get("d1PriceChanges", {}) if isinstance(result.get("d1PriceChanges"), Mapping) else {},
            d2PriceChanges=result.get("d2PriceChanges", {}) if isinstance(result.get("d2PriceChanges"), Mapping) else {},
            windowPriceChanges=result.get("windowPriceChanges", {}) if isinstance(result.get("windowPriceChanges"), Mapping) else {},
            comparability=result.get("comparability"),
            gaps=[str(item.get("reason") or item) if isinstance(item, Mapping) else str(item) for item in result.get("gaps", [])],
            factRefs=_evaluation_fact_refs(path, str(window.get("companyCode") or result.get("companyCode") or ""),
                                           [item for item in row.get("factRefs", []) if isinstance(item, Mapping)])))
    return records


def _metrics(records: list[CompanyWindowEvaluationOut], *, windows: Mapping[str, Mapping[str, Any]] | None = None,
             touch_denominator: str = "eligible") -> EvaluationMetricsOut:
    now = datetime.now(timezone.utc)
    def complete_observation(item: CompanyWindowEvaluationOut) -> bool:
        return (item.d1 is not None and item.d2 is not None and
                item.d1.availability == item.d2.availability == "available" and
                all(isinstance(value, bool) for value in (item.d1.closeLimitUp, item.d1.touchedLimitUp,
                                                           item.d2.closeLimitUp, item.d2.touchedLimitUp)))
    def eligible(item: CompanyWindowEvaluationOut) -> bool:
        if item.state != "completed" or not item.primaryEligible:
            return False
        if not complete_observation(item):
            return False
        if windows is not None:
            close = windows.get(item.companyWindowId, {}).get("d2CloseAt")
            try:
                if not isinstance(close, str) or _instant(close) > now:
                    return False
            except ValueError:
                return False
        return True
    eligible = [item for item in records if eligible(item)]
    hits = [item for item in eligible if item.closeLimitHitAny is True]
    def has_day(item: CompanyWindowEvaluationOut, state: str) -> bool:
        return any(day is not None and day.availability == state for day in (item.d1, item.d2))
    touch_count = sum(any(day is not None and day.touchedLimitUp is True for day in (item.d1, item.d2)) for item in records)
    touch_base = len(eligible) if touch_denominator == "eligible" else sum(complete_observation(item) for item in records)
    return EvaluationMetricsOut(sampleCount=len(records), eligibleCount=len(eligible), hitCount=len(hits),
                                hitRate=len(hits) / len(eligible) if eligible else None,
                                touchRate=touch_count / touch_base if touch_base else None,
                                incompleteCount=sum(item.state == "incomplete" for item in records),
                                pendingCount=sum(item.state in {"pending", "due"} for item in records),
                                observedCompleteCount=sum(complete_observation(item) for item in records),
                                knownHitCount=sum(item.closeLimitHitAny is True for item in records),
                                touchCount=touch_count,
                                suspendedCount=sum(has_day(item, "suspended") for item in records),
                                dataGapCount=sum(has_day(item, "data_gap") for item in records),
                                anomalyCount=sum(has_day(item, "anomaly") for item in records),
                                selectionPendingCount=sum(item.selection is None for item in records))


def _selection_group(item: CompanyWindowEvaluationOut, group: str) -> bool:
    if group == "all":
        return True
    if item.selection is None:
        return False
    return {"selected": "selected", "skipped": "skipped", "unhandled": "unhandled"}[group] == item.selection.state


def _result_groups(records: list[CompanyWindowEvaluationOut], *, path: Path) -> tuple[list[ResultsCohortOut], list[ResultsEventGroupOut]]:
    windows = _windows(path)
    opportunities = store.list_opportunities(db_path=path)
    by_window: dict[str, list[Mapping[str, Any]]] = {}
    for opportunity in opportunities:
        by_window.setdefault(str(opportunity["companyWindowId"]), []).append(opportunity)
    batches = {str(item["batchId"]): item for item in store.list_publication_batches(db_path=path)}
    configs: dict[str, str | None] = {}
    for window in windows.values():
        batch = batches.get(str(window["firstBatchId"]))
        scan = store.get_scan(scan_id=str(batch["scanId"]), db_path=path) if batch else None
        config = (store.read_run_config(config_id=str(scan["configId"]), revision=int(scan["configRevision"]), db_path=path)
                  if scan and isinstance(scan.get("configId"), str) and isinstance(scan.get("configRevision"), int) else None)
        policy = config.get("payload", {}).get("evaluationPolicy") if isinstance(config, Mapping) else None
        configs[str(window["companyWindowId"])] = policy.get("version") if isinstance(policy, Mapping) and isinstance(policy.get("version"), str) else None
    cohort_rows: dict[tuple[str, str, str, str | None], list[CompanyWindowEvaluationOut]] = {}
    for record in records:
        window = windows.get(record.companyWindowId)
        if window is None:
            continue
        key = (str(window["firstBatchId"]), str(window["d1TradeDate"]), str(window["d2TradeDate"]), configs.get(record.companyWindowId))
        cohort_rows.setdefault(key, []).append(record)
    cohorts = []
    for (batch_id, d1, d2, version), items in sorted(cohort_rows.items()):
        primary = [item for item in items if item.sampleClass == "primary"]
        overlap = [item for item in items if item.sampleClass == "overlap"]
        cohorts.append(ResultsCohortOut(batchId=batch_id, d1TradeDate=d1, d2TradeDate=d2, evaluationVersion=version,
            companySampleCount=len({item.companyWindowId for item in items}),
            catalystEventCount=len({opportunity["eventId"] for item in items for opportunity in by_window.get(item.companyWindowId, [])}),
            primary={group: _metrics([item for item in primary if _selection_group(item, group)], windows=windows)
                     for group in ("all", "selected", "skipped", "unhandled")}, overlap=_metrics(overlap, windows=windows, touch_denominator="observed")))
    event_rows: dict[str, list[CompanyWindowEvaluationOut]] = {}
    event_opportunities: dict[str, list[Mapping[str, Any]]] = {}
    record_by_window = {item.companyWindowId: item for item in records}
    for window_id, values in by_window.items():
        record = record_by_window.get(window_id)
        if record is None:
            continue
        for opportunity in values:
            event_id = str(opportunity["eventId"])
            event_rows.setdefault(event_id, []).append(record)
            event_opportunities.setdefault(event_id, []).append(opportunity)
    event_groups = []
    for event_id, raw_items in sorted(event_rows.items()):
        items = list({item.companyWindowId: item for item in raw_items}.values())
        primary = [item for item in items if item.sampleClass == "primary"]
        with _reader(path) as conn:
            event_row = conn.execute("SELECT headline FROM k10_event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1", (event_id,)).fetchone()
        event_groups.append(ResultsEventGroupOut(eventId=event_id, headline=str(event_row[0]) if event_row else None,
            companyWindowIds=sorted(item.companyWindowId for item in items),
            opportunityIds=sorted({str(item["opportunityId"]) for item in event_opportunities[event_id]}),
            companySampleCount=len(items), catalystCount=len(event_opportunities[event_id]),
            primary={group: _metrics([item for item in primary if _selection_group(item, group)], windows=windows)
                     for group in ("all", "selected", "skipped", "unhandled")}))
    return cohorts, event_groups


def create_router(db_path_provider: DbPathProvider, require_token_dependency: TokenDependency,
                  parquet_dir_provider: Callable[[], Path]) -> APIRouter:
    router = APIRouter(prefix="/api/v1/k10", tags=["k10"], dependencies=[Depends(require_token_dependency)])

    def db_path() -> Path:
        path = db_path_provider()
        if not isinstance(path, Path):
            raise RuntimeError("K10 API db_path provider 必须返回 pathlib.Path")
        # Validate every request before a public store read.  Store reads also
        # reject an unavailable schema, but converting that boundary here keeps
        # the HTTP contract deterministic and never creates a database.
        with _reader(path):
            pass
        return path

    def parquet_dir() -> Path:
        path = parquet_dir_provider()
        if not isinstance(path, Path):
            raise RuntimeError("K10 API parquet_dir provider 必须返回 pathlib.Path")
        return path

    @router.get("/scans/latest", response_model=ScanOut)
    def latest_scan(window: str = Query(..., pattern="^(evening|morning)$")) -> ScanOut:
        path = db_path()
        scans = store.list_scans(window_kind=window, db_path=path)
        if not scans:
            raise _not_found("没有该窗口 K10 扫描")
        return _scan(scans[0], store.list_publication_batches(db_path=path))

    @router.get("/scans/{scan_id}", response_model=ScanOut)
    def get_scan(scan_id: str) -> ScanOut:
        path = db_path()
        value = store.get_scan(scan_id=scan_id, db_path=path)
        if value is None:
            raise _not_found("K10 扫描不存在")
        return _scan(value, store.list_publication_batches(db_path=path))

    @router.get("/publications", response_model=PublicationListOut)
    def list_publications(limit: int = Query(30, ge=1, le=100), cursor: str | None = None) -> PublicationListOut:
        values = [_publication(item, db_path()) for item in store.list_publication_batches(db_path=db_path())]
        page, next_cursor = _page(values, cursor, limit, lambda item: item.batchId)
        return PublicationListOut(items=page, page=PageMeta(nextCursor=next_cursor))

    @router.get("/publications/{batch_id}", response_model=PublicationOut)
    def get_publication(batch_id: str) -> PublicationOut:
        value = store.get_publication_batch(batch_id=batch_id, db_path=db_path())
        if value is None:
            raise _not_found("发布批次不存在")
        return _publication(value, db_path())

    @router.get("/opportunities", response_model=OpportunityListOut)
    def list_opportunities(batchId: str | None = None, companyCode: str | None = None,
                           lifecycle: str | None = Query(None, pattern="^(active|withdrawn|expired)$"),
                           limit: int = Query(30, ge=1, le=100), cursor: str | None = None) -> OpportunityListOut:
        path = db_path(); windows = _windows(path)
        with _reader(path) as conn:
            values = [_opportunity(item, windows, _company_name(conn, str(item["companyCode"])), path) for item in store.list_opportunities(company_code=companyCode, state=lifecycle, batch_id=batchId, db_path=path)]
        page, next_cursor = _page(values, cursor, limit, lambda item: item.opportunityId)
        return OpportunityListOut(items=page, page=PageMeta(nextCursor=next_cursor))

    @router.get("/opportunities/{opportunity_id}", response_model=OpportunityDetail)
    def get_opportunity(opportunity_id: str) -> OpportunityDetail:
        path = db_path(); value = store.get_opportunity(opportunity_id=opportunity_id, db_path=path)
        if value is None:
            raise _not_found("机会不存在")
        windows = _windows(path)
        with _reader(path) as conn:
            base = _opportunity(value, windows, _company_name(conn, str(value["companyCode"])), path)
            event = conn.execute(
                "SELECT headline,facts_json FROM k10_event_revisions WHERE event_id=? AND revision=?",
                (str(value["eventId"]), int(value["eventRevision"])),
            ).fetchone()
        # The comparison set is bounded to the same visible publication batch
        # and exact event revision.  New stages or later batches never leak in.
        samples = [sample for sample in _all_samples(path)
                   if sample.batchId == value["firstBatchId"] and sample.eventId == value["eventId"] and sample.eventRevision == value["eventRevision"]]
        facts = _json(event[1], {}) if event else {}
        return OpportunityDetail(**base.model_dump(), eventHeadline=str(event[0]) if event and event[0] else None,
                                 commonFacts=_common_facts(facts), samples=samples,
                                 lifecycleEvents=_lifecycle_events(path, opportunity_id))

    @router.get("/company-windows", response_model=CompanyWindowListOut)
    def list_company_windows(companyCode: str | None = None, sampleClass: str | None = Query(None, pattern="^(primary|overlap)$"),
                             limit: int = Query(30, ge=1, le=100), cursor: str | None = None) -> CompanyWindowListOut:
        path = db_path(); windows = store.list_company_windows(company_code=companyCode, db_path=path); all_windows = {item["companyWindowId"]: item for item in windows}
        with _reader(path) as conn:
            opportunities = [_opportunity(item, all_windows, _company_name(conn, str(item["companyCode"])), path) for item in store.list_opportunities(company_code=companyCode, db_path=path)]
            samples = _all_samples(path)
            values = [_company_window(item, _company_name(conn, str(item["companyCode"])), opportunities, samples,
                                      _window_selection(path, str(item["companyWindowId"])))
                      for item in windows if sampleClass is None or item["sampleClass"] == sampleClass]
        page, next_cursor = _page(values, cursor, limit, lambda item: item.companyWindowId)
        return CompanyWindowListOut(items=page, page=PageMeta(nextCursor=next_cursor))

    @router.get("/company-windows/{company_window_id}", response_model=CompanyWindowOut)
    def get_company_window(company_window_id: str) -> CompanyWindowOut:
        path = db_path(); windows = _windows(path); value = windows.get(company_window_id)
        if value is None:
            raise _not_found("公司观察窗口不存在")
        with _reader(path) as conn:
            opportunities = [_opportunity(item, windows, _company_name(conn, str(item["companyCode"])), path) for item in store.list_opportunities(company_code=value["companyCode"], db_path=path)]
            return _company_window(value, _company_name(conn, str(value["companyCode"])), opportunities, _all_samples(path),
                                   _window_selection(path, company_window_id))

    @router.post("/company-windows/{company_window_id}/selection", response_model=SelectionActionOut)
    def select_company_window(company_window_id: str, command: SelectionActionIn) -> SelectionActionOut:
        """Append a company-window decision and create at most one shared analysis chain.

        A window can hold several catalysts.  The public action is deliberately
        never attached to one catalyst or its discovery candidate.
        """
        path = db_path()
        window = _windows(path).get(company_window_id)
        if window is None:
            raise _not_found("公司观察窗口不存在")
        opportunities = [item for item in store.list_opportunities(db_path=path)
                         if item["companyWindowId"] == company_window_id]
        if command.action == "keep" and (not opportunities or all(item["state"] in {"withdrawn", "expired"} for item in opportunities)):
            raise _conflict("已撤回或到期公司窗口不能新留意")
        batch = store.get_publication_batch(batch_id=str(window["firstBatchId"]), db_path=path)
        scan = store.get_scan(scan_id=str(batch["scanId"]), db_path=path) if batch else None
        config = (store.read_run_config(config_id=str(scan["configId"]), revision=int(scan["configRevision"]), db_path=path)
                  if scan and scan.get("configId") and scan.get("configRevision") is not None else None)
        action_id = _stable_id("window-action", company_window_id, command.idempotencyKey)
        replayed = False
        try:
            if command.action == "keep":
                policy = config["payload"].get("taskPolicies", {}).get("analysis", {}) if config else {}
                cutoff_at = str(batch["availableAt"]) if batch else _now()
                try:
                    market_context = collect_market_context(company_code=str(window["companyCode"]), cutoff_at=cutoff_at,
                                                            parquet_dir=parquet_dir())
                except MarketContextError:
                    market_context = {"status": "unavailable", "reason": "market_context_input_invalid",
                                      "asOf": cutoff_at, "sourceRefs": [], "recentDays": []}
                samples = [sample.model_dump(mode="json") for sample in _all_samples(path)
                           if sample.companyWindowId == company_window_id]
                task_payload = {
                    "companyWindow": dict(window),
                    "opportunities": [dict(item) for item in opportunities],
                    "publicationSamples": samples,
                    "marketContext": market_context,
                    "configId": config["configId"] if config else None,
                    "configRevision": config["revision"] if config else None,
                }
                result = store.observe_company_window(
                    action_id=action_id, observation_id=_stable_id("observation", "window", company_window_id),
                    task_id=_stable_id("task", "analysis", company_window_id),
                    outbox_id=_stable_id("outbox", "analysis", company_window_id), company_window_id=company_window_id,
                    idempotency_key=command.idempotencyKey,
                    task_input_version=str(config["contentSha256"]) if config else "not_configured",
                    task_input_cutoff_at=cutoff_at, task_payload=task_payload,
                    task_budget=dict(policy) if isinstance(policy, Mapping) else {}, created_at=_now(), db_path=path,
                )
                action_id, replayed = result.action_id, result.replayed
            else:
                result_id = store.append_company_window_action(
                    action_id=action_id, company_window_id=company_window_id, action=command.action,
                    idempotency_key=command.idempotencyKey, reason=command.reason, created_at=_now(), db_path=path,
                )
                replayed = result_id != action_id
                action_id = result_id
        except store.K10Conflict as exc:
            raise _conflict(str(exc)) from exc
        selection = _window_selection(path, company_window_id)
        if selection is None:
            raise HTTPException(status_code=500, detail={"reason": "integrity_error", "message": "窗口动作未能投影"})
        return SelectionActionOut(actionId=action_id, companyWindowId=company_window_id,
                                  representativeCandidateId=selection["representativeCandidateId"],
                                  state=selection["currentState"], observationId=selection["observationId"],
                                  analysisJobId=selection["taskId"], lastActionAt=selection.get("lastActionAt"),
                                  postFreeze=bool(selection.get("postFreeze", False)), replayed=replayed)

    @router.get("/selections", response_model=SelectionListOut)
    def list_selections(state: str | None = Query(None, pattern="^(kept|skipped|unhandled)$"), limit: int = Query(30, ge=1, le=100), cursor: str | None = None) -> SelectionListOut:
        path = db_path(); values = [_selection_detail(path, str(window["companyWindowId"])) for window in store.list_company_windows(db_path=path)]
        if state is not None:
            values = [item for item in values if item.state == state]
        page, next_cursor = _page(values, cursor, limit, lambda item: item.companyWindowId)
        return SelectionListOut(items=page, page=PageMeta(nextCursor=next_cursor))

    @router.get("/selections/{company_window_id}", response_model=SelectionDetailOut)
    def get_selection(company_window_id: str) -> SelectionDetailOut:
        path = db_path()
        if company_window_id not in _windows(path):
            raise _not_found("公司观察窗口不存在")
        return _selection_detail(path, company_window_id)

    @router.get("/jobs/{job_id}", response_model=JobOut)
    def get_job(job_id: str) -> JobOut:
        with _reader(db_path()) as conn:
            job = _job_out(conn, job_id)
        if job is None:
            raise _not_found("任务不存在")
        return job

    @router.post("/jobs/{job_id}/retry", response_model=JobOut)
    def retry_job(job_id: str, command: JobRetryIn) -> JobOut:
        try:
            store.retry_task(task_id=job_id, expected_attempt_count=command.expectedAttemptCount, retried_at=_now(), db_path=db_path())
        except store.K10Conflict as exc:
            raise _conflict(str(exc)) from exc
        with _reader(db_path()) as conn:
            job = _job_out(conn, job_id)
        if job is None:
            raise _not_found("任务不存在")
        return job

    @router.get("/results", response_model=ResultsOut)
    def results() -> ResultsOut:
        path = db_path(); records = _evaluation_records(path); primary = [item for item in records if item.sampleClass == "primary"]; overlap = [item for item in records if item.sampleClass == "overlap"]
        windows = _windows(path); cohorts, event_groups = _result_groups(records, path=path)
        configuration_missing = False
        for window in windows.values():
            batch = store.get_publication_batch(batch_id=str(window["firstBatchId"]), db_path=path)
            scan = store.get_scan(scan_id=str(batch["scanId"]), db_path=path) if batch else None
            config = (store.read_run_config(config_id=str(scan["configId"]), revision=int(scan["configRevision"]), db_path=path)
                      if scan and isinstance(scan.get("configId"), str) and isinstance(scan.get("configRevision"), int) else None)
            if not validate_run_config(config.get("payload") if isinstance(config, Mapping) else None, scope="evaluation").ready:
                configuration_missing = True
        return ResultsOut(state="not_configured" if configuration_missing and not records else "available",
                          reason=ApiFailure(reason="not_configured", message="两日行情采集或评价参数未配置") if configuration_missing else None,
                          asOf=max((item.updatedAt for item in records), default=None),
                          primary={group: _metrics([item for item in primary if _selection_group(item, group)], windows=windows) for group in ("all", "selected", "skipped", "unhandled")},
                          overlap=_metrics(overlap, windows=windows, touch_denominator="observed"), records=records, cohorts=cohorts, eventGroups=event_groups)

    @router.get("/documents/{document_id}", response_model=SourceDocumentPageOut)
    def get_document(document_id: str, revision: int | None = Query(None, ge=1), offset: int = Query(0, ge=0), limit: int = Query(8000, ge=1, le=24000)) -> SourceDocumentPageOut:
        with _reader(db_path()) as conn:
            row = conn.execute("SELECT d.source_key,d.external_id,d.canonical_url,v.revision,v.published_at,v.published_precision,v.fetched_at,v.original_text,v.excerpt,v.metadata_json FROM k10_source_documents d JOIN k10_source_document_versions v ON v.document_id=d.document_id WHERE d.document_id=? " + ("AND v.revision=? " if revision else "") + "ORDER BY v.revision DESC LIMIT 1", (document_id, revision) if revision else (document_id,)).fetchone()
        if row is None:
            raise _not_found("原始资料不存在")
        if row[7] is None:
            body, next_cursor = None, None
        else:
            text = _document_body(str(row[0]), row[7])
            body, next_cursor = text[offset:offset + limit], str(offset + limit) if offset + limit < len(text) else None
        return SourceDocumentPageOut(documentId=document_id, revision=int(row[3]), sourceKey=str(row[0]), externalId=str(row[1]), canonicalUrl=row[2],
                                     title=_json(row[9], {}).get("title"), publishedAt=row[4], publishedPrecision=str(row[5]), fetchedAt=str(row[6]),
                                     excerpt=row[8], body=body, page=PageMeta(nextCursor=next_cursor))

    @router.get("/configuration", response_model=ConfigurationOut)
    def configuration() -> ConfigurationOut:
        scans = store.list_scans(window_kind=None, db_path=db_path()); latest = scans[0] if scans else None
        config = store.read_run_config(config_id=str(latest["configId"]), revision=int(latest["configRevision"]), db_path=db_path()) if latest and latest.get("configId") and latest.get("configRevision") is not None else None
        scopes = []
        for scope in ("candidate", "analysis", "evaluation"):
            result = validate_run_config(config["payload"] if config else None, scope=scope)
            scopes.append(ConfigurationScopeOut(scope=scope, state="configured" if result.ready else "not_configured", missing=list(result.missing), errors=list(result.errors)))
        return ConfigurationOut(configId=config["configId"] if config else None, configRevision=config["revision"] if config else None, scopes=scopes)

    return router


def _scan(value: Mapping[str, Any], publications: list[Mapping[str, Any]]) -> ScanOut:
    coverage = value.get("coverage") if isinstance(value.get("coverage"), Mapping) else {}
    outcomes = coverage.get("sourceOutcomes", []) if isinstance(coverage, Mapping) else []
    gaps = coverage.get("gaps", []) if isinstance(coverage, Mapping) else []
    publication = next((item for item in publications if item["scanId"] == value["scanId"]), None)
    return ScanOut(scanId=str(value["scanId"]), window=str(value["windowKind"]), cutoffAt=str(value["cutoffAt"]), status=str(value["status"]),
                   coverageStatus=str(coverage.get("status", value["status"])), coverageGaps=[str(item) for item in gaps],
                   sourceCoverage=[dict(item) for item in outcomes if isinstance(item, Mapping)],
                   publicationStatus="published" if publication else "not_published",
                   publicationBatchId=publication["batchId"] if publication else None,
                   availableAt=publication["availableAt"] if publication else None, configId=value.get("configId"),
                   configRevision=value.get("configRevision"), createdAt=str(value["createdAt"]), completedAt=value.get("completedAt"))


def _company_window(value: Mapping[str, Any], company_name: str | None, opportunities: list[OpportunityOut], samples: list[PublicationSampleOut],
                    current_selection: Mapping[str, Any] | None = None) -> CompanyWindowOut:
    selection = value.get("selection")
    snapshot = SelectionSnapshotOut.model_validate(selection) if isinstance(selection, Mapping) else None
    window_id = str(value["companyWindowId"])
    return CompanyWindowOut(companyWindowId=window_id, companyCode=str(value["companyCode"]), companyName=company_name,
                            firstBatchId=str(value["firstBatchId"]), d0TradeDate=str(value["d0TradeDate"]), d1TradeDate=str(value["d1TradeDate"]),
                            d2TradeDate=str(value["d2TradeDate"]), d1SelectionAt=str(value["d1SelectionAt"]), d2CloseAt=str(value["d2CloseAt"]),
                            sampleClass=str(value["sampleClass"]), overlapsWindowId=value.get("overlapsWindowId"), selection=snapshot,
                            currentSelectionState=(current_selection or {}).get("currentState", "unhandled"),
                            lastActionAt=(current_selection or {}).get("lastActionAt"), postFreeze=bool((current_selection or {}).get("postFreeze", False)),
                            opportunities=[item for item in opportunities if item.companyWindowId == window_id],
                            samples=[item for item in samples if item.companyWindowId == window_id],
                            createdAt=next((item.createdAt for item in opportunities if item.companyWindowId == window_id), str(value["d1SelectionAt"])))


__all__ = ["SCHEMA_VERSION", "create_router"]
