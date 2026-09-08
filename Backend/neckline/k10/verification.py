"""Optional Tavily evidence for K10 event verification; never a market-wide source."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from neckline.search.tavily import TavilySearchClient, TavilySearchResponse, TavilyExtractResponse
from neckline.settings_store import get_tavily_api_key
from neckline.llm.usage import record as record_usage

from .discovery import DiscoveryDocument, EventDraft
from .source_metadata import PublicationMetadataResolver
from .types import DocumentVersion
from .schema import read_connection, require_schema
from . import store
from .verification_checkpoints import VerificationCheckpointError, VerificationCheckpointStore


@dataclass(frozen=True)
class VerificationEvidenceBundle:
    state: str
    documents: tuple[DiscoveryDocument, ...]
    eligible_documents: tuple[DiscoveryDocument, ...]
    coverage: Mapping[str, Any]


class SearchClient(Protocol):
    def search(self, query: str) -> TavilySearchResponse: ...


def _text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("核验取得时间必须带时区")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _published(value: str) -> tuple[str | None, str]:
    raw = (value or "").strip()
    if len(raw) == 10 and raw[4:5] == "-" and raw[7:8] == "-":
        try:
            return datetime.fromisoformat(raw + "T00:00:00+00:00").isoformat(timespec="seconds"), "date"
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.isoformat(timespec="seconds"), "exact"
    except ValueError:
        pass
    try:
        # Tavily uses RFC 2822 for some real publisher timestamps.  A date
        # without a concrete offset is not safe for an intraday cutoff.
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is not None:
            return parsed.isoformat(timespec="seconds"), "exact"
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    return None, "unknown"


def _aware_instant(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


class TavilyEvidenceGateway:
    """Question-bound evidence requests with durable, finite network retries."""
    source_key = "tavily_verification"

    def __init__(self, *, db_path: Path, client: SearchClient | None = None,
                 clock: Callable[[], datetime] | None = None,
                 metadata_resolver: PublicationMetadataResolver | None = None, task_id: str | None = None,
                 leaseguard: Callable[[], None] | None = None,
                 checkpoint_store: VerificationCheckpointStore | None = None,
                 network_max_attempts: int | None = None, lease_owner: str | None = None) -> None:
        self.db_path = db_path
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.metadata_resolver = metadata_resolver
        if checkpoint_store is not None and task_id is not None and checkpoint_store.task_id != task_id:
            raise ValueError("Tavily 核验 task_id 与检查点不一致")
        self.checkpoint_store = checkpoint_store or (
            VerificationCheckpointStore(db_path=db_path, task_id=task_id, leaseguard=leaseguard, lease_owner=lease_owner) if task_id else None
        )
        self.network_max_attempts = network_max_attempts
        self.requests, self.credits = self.checkpoint_store.attempt_snapshot() if self.checkpoint_store else (0, 0)

    def _reserve_search(self, *, item_key: str, input_sha256: str, attempt: int) -> tuple[str | None, str | None]:
        """Start one external attempt after the event checkpoint claim."""
        if self.checkpoint_store is None:
            return None, None
        key = "tavily:" + sha256(f"{self.checkpoint_store.task_id}\x1f{item_key}\x1f{attempt}".encode()).hexdigest()
        admitted = store.begin_external_attempt(
            task_id=self.checkpoint_store.task_id, item_key=item_key, stage="search", attempt_key=key,
            input_sha256=input_sha256, started_at=_text(self.clock()), db_path=self.db_path,
        )
        if admitted.get("state") != "started":
            return None, {
                "paused": "execution_paused", "not_configured": "execution_not_configured",
                "pending_outcome": "tavily_request_outcome_unknown", "retired": "execution_retired_by_user",
                "reused": "tavily_attempt_reused",
            }.get(str(admitted.get("state")), "tavily_attempt_admission_failed")
        attempt_id = admitted.get("attemptId")
        return (str(attempt_id), None) if isinstance(attempt_id, str) else (None, "tavily_attempt_admission_failed")

    def _settle_search(self, *, attempt_id: str, response: TavilySearchResponse | TavilyExtractResponse | None) -> None:
        outcome = "unknown" if response is None else ("succeeded" if response.ok else "failed")
        usage = None if response is None else {
            "promptTokens": None, "completionTokens": None, "totalTokens": None,
            "searchRequests": 1, "searchCredits": response.credits if isinstance(response.credits, int) else None,
        }
        store.settle_external_attempt(attempt_id=attempt_id, outcome=outcome, usage=usage,
                                      error_code=None if response is None or response.ok else "tavily_response_unavailable",
                                      settled_at=_text(self.clock()), db_path=self.db_path)

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        if not isinstance(value, Mapping):
            raise VerificationCheckpointError("research_query_contract_invalid")
        return value

    @classmethod
    def _query_context(cls, question: Any, query_path: Any) -> dict[str, Any] | None:
        if question is None and query_path is None:
            return None
        question, path = cls._mapping(question), cls._mapping(query_path)
        required = ("questionId", "pathId", "query", "intent", "newPathReason",
                    "expectedInformationGain", "expectedJudgmentChange")
        if any(not isinstance(path.get(key), str) or not path[key].strip() for key in required):
            raise VerificationCheckpointError("research_query_contract_invalid")
        if path["questionId"] != question.get("questionId") or len(path["query"].strip()) > 400:
            raise VerificationCheckpointError("research_query_scope_invalid")
        return {key: path[key] for key in required} | {"targetSource": path.get("targetSource")}

    def fetch(self, *, event: EventDraft, retrieved_at: datetime, cutoff_at: datetime,
              cutoff_inclusive: bool = False, question: Any = None,
              query_path: Any = None) -> VerificationEvidenceBundle:
        if cutoff_at.tzinfo is None:
            raise ValueError("核验 cutoff 必须带时区")
        query_context = self._query_context(question, query_path)
        query = query_context["query"].strip() if query_context else event.headline[:400]
        if self.checkpoint_store is not None and (
            not isinstance(self.network_max_attempts, int) or isinstance(self.network_max_attempts, bool)
            or self.network_max_attempts < 1
        ):
            return self._pending("networkMaxAttempts_missing")
        client = self.client
        if client is None:
            key = get_tavily_api_key(db_path=self.db_path)
            if not key:
                return VerificationEvidenceBundle("pending", (), (), {"provider": "tavily", "state": "pending", "reason": "tavily_api_key_missing", "requests": self.requests})
            client = TavilySearchClient(key)
        checkpoint_key: str | None = None
        checkpoint_input: str | None = None
        if self.checkpoint_store is not None:
            source_refs = sorted(
                ({"documentId": ref.document_id, "revision": ref.revision} for ref in event.source_refs),
                key=lambda item: (item["documentId"], item["revision"]),
            )
            checkpoint_key = self.checkpoint_store.item_key(
                canonical_key=event.canonical_key, stage_key=event.stage_key, event_state=event.event_state,
                question_id=query_context["questionId"] if query_context else None,
                path_id=query_context["pathId"] if query_context else None,
            )
            checkpoint_input = self.checkpoint_store.input_sha256(
                canonical_key=event.canonical_key, stage_key=event.stage_key, event_state=event.event_state,
                headline=event.headline, event_kind=event.event_kind, facts=event.facts, source_refs=source_refs,
                cutoff_at=_text(cutoff_at), cutoff_inclusive=cutoff_inclusive,
                investigation_path=query_context,
            )
            claim = self.checkpoint_store.claim(item_key=checkpoint_key, input_sha256=checkpoint_input,
                                                network_max_attempts=self.network_max_attempts)
            self.requests = claim.requests
            if claim.state == "reused":
                return self._restore_checkpoint_bundle(claim.result)
            if claim.state == "pending":
                return self._pending(claim.reason or "verification_checkpoint_pending")
        attempt_id, blocked = self._reserve_search(
            item_key=checkpoint_key or event.canonical_key, input_sha256=checkpoint_input or "unbound",
            attempt=self.requests,
        )
        if blocked is not None:
            if self.checkpoint_store is not None and checkpoint_key is not None and checkpoint_input is not None:
                # These rejections happen before an HTTP request leaves the
                # process, so they cannot consume a per-event retry.  A
                # reused external attempt remains conservatively charged:
                # retrying it could duplicate a paid request.
                if blocked in {"execution_paused", "execution_not_configured", "execution_retired_by_user"}:
                    self.checkpoint_store.defer_without_request(
                        item_key=checkpoint_key, input_sha256=checkpoint_input, safe_error_code=blocked,
                    )
                else:
                    self.checkpoint_store.fail_retryable(
                        item_key=checkpoint_key, input_sha256=checkpoint_input, safe_error_code=blocked,
                    )
            return self._pending(blocked)
        # This is user-readable event language only.  Internal stage keys are
        # not query terms and materially reduce search precision in practice.
        if self.checkpoint_store is None:
            self.requests += 1
        # The checkpoint layer owns K10 retries.  A client-internal retry would
        # otherwise create billable HTTP attempts without separate admission.
        if self.checkpoint_store is not None and isinstance(client, TavilySearchClient):
            client.max_attempts = 1
        response: TavilySearchResponse | None = None
        try:
            response = client.search(query)
        except Exception:
            if self.checkpoint_store is not None:
                self.checkpoint_store.fail_retryable(item_key=checkpoint_key, input_sha256=checkpoint_input,
                                                     safe_error_code="tavily_request_outcome_unknown")
                if attempt_id is not None:
                    self._settle_search(attempt_id=attempt_id, response=None)
                return self._pending("tavily_request_outcome_unknown")
            raise
        if attempt_id is not None:
            self._settle_search(attempt_id=attempt_id, response=response)
        # The caller's retrieved_at was captured before the blocking request.
        # Persist the actual completion time instead, so fetchedAt never
        # pretends that source material was available before it arrived.
        obtained_at = self.clock()
        obtained_at_text = _text(obtained_at)
        # Tavily returns an actual credit count independently of model tokens.  The usage
        # writer is a no-op when the target has no migrated usage table and never creates DDL.
        record_usage(task="discovery", result=None, trade_date=obtained_at.date(), outcome="search_success" if response.ok else "search_failed",
                     tavily_credits=response.credits, searched=True, duration_ms=response.wall_ms,
                     failure_reason=None if response.ok else response.reason, db_path=self.db_path)
        response_reason = response.reason if not self.checkpoint_store else (
            "tavily_response_unavailable" if not response.ok else "coverage_pending"
        )
        coverage: dict[str, Any] = {"provider": "tavily", "query": query, "requests": self.requests,
                                    "state": "pending", "credits": response.credits,
                                    "reason": response_reason}
        if query_context:
            coverage.update({"questionId": query_context["questionId"], "pathId": query_context["pathId"],
                             "operation": "search", "queryIntent": query_context["intent"]})
        if response.credits is not None:
            self.credits += response.credits
        if self.checkpoint_store is not None and not response.ok:
            self.checkpoint_store.fail_retryable(item_key=checkpoint_key, input_sha256=checkpoint_input,
                                                 safe_error_code="tavily_response_unavailable")
            return self._pending("tavily_response_unavailable")
        docs: list[DiscoveryDocument] = []
        eligible: list[DiscoveryDocument] = []
        for index, hit in enumerate(response.hits):
            excerpt = (hit.content or hit.title).strip()
            if not excerpt:
                continue
            external_id = hit.link or sha256(f"{query}\x1f{index}\x1f{excerpt}".encode()).hexdigest()
            document_id = "doc_" + sha256(f"{self.source_key}\x1f{external_id}".encode()).hexdigest()[:32]
            published_at, precision = _published(hit.publish_date)
            metadata = {"provider": "tavily", "query": query, "title": hit.title, "media": hit.media,
                        "requestId": response.request_id, "publishedDateRaw": hit.publish_date or None,
                        "publisher": hit.media or None, "originStatus": "unknown", "originEvidenceRef": None}
            if query_context:
                metadata["investigationPath"] = dict(query_context)
            final_obtained_at = obtained_at
            # Date-only Tavily values retain their previous conservative
            # behavior.  Only a wholly unparseable timestamp gets a bounded
            # direct-page metadata lookup, and only a precise, zoned result can
            # become intraday evidence.
            if precision == "unknown" and hit.link and self.metadata_resolver is not None:
                resolved = self.metadata_resolver.resolve(hit.link)
                resolved_published = _aware_instant(resolved.published_at)
                resolved_fetched = _aware_instant(resolved.fetched_at)
                if resolved_fetched is not None and resolved_fetched > final_obtained_at:
                    final_obtained_at = resolved_fetched
                metadata["metadataResolution"] = {
                    "provider": "source_metadata", "publishedAt": resolved.published_at,
                    "precision": resolved.precision, "fetchedAt": resolved.fetched_at,
                    "details": dict(resolved.metadata),
                }
                if resolved.precision == "exact" and resolved_published is not None:
                    published_at, precision = resolved_published.isoformat(timespec="seconds"), "exact"
            final_obtained_text = _text(final_obtained_at)
            with read_connection(self.db_path) as conn:
                require_schema(conn)
                first_seen = conn.execute("SELECT MIN(created_at) FROM k10_source_document_versions WHERE document_id=?",
                                          (document_id,)).fetchone()[0]
            metadata["firstSeenAt"] = first_seen or final_obtained_text
            metadata["publicationPrecision"] = precision
            metadata["afterCutoff"] = bool(published_at and precision == "exact" and
                (datetime.fromisoformat(published_at) > cutoff_at or
                 (not cutoff_inclusive and datetime.fromisoformat(published_at) == cutoff_at)))
            payload = {"url": hit.link or None, "excerpt": excerpt, "publishedAt": published_at, "precision": precision, "metadata": metadata}
            version: DocumentVersion = store.append_document_version(document_id=document_id, source_key=self.source_key,
                external_id=external_id, canonical_url=hit.link or None,
                content_sha256=sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                published_at=published_at, published_precision=precision, fetched_at=final_obtained_text,
                original_text=None, excerpt=excerpt, fetch_version="tavily-basic-general-v2", metadata=metadata,
                created_at=final_obtained_text, db_path=self.db_path)
            document = DiscoveryDocument(version.document_id, version.revision, published_at, final_obtained_text, None, excerpt, metadata)
            docs.append(document)
            # A date-only source can prove it predates an earlier day, never a particular
            # intraday cutoff; unknown and late material stays archived/pending.
            if precision == "exact" and published_at:
                published = datetime.fromisoformat(published_at)
                if published < cutoff_at or (cutoff_inclusive and published == cutoff_at):
                    eligible.append(document)
            elif precision == "date" and published_at and datetime.fromisoformat(published_at).date() < cutoff_at.date():
                eligible.append(document)
        state = "available" if response.ok and response.credits is not None and eligible else "pending"
        reason = "ok" if state == "available" else (
            "tavily_response_unavailable" if self.checkpoint_store and not response.ok
            else (response.reason if not response.ok else "coverage_gap_or_usage_unavailable")
        )
        coverage.update({"state": state, "reason": reason, "documents": len(docs), "eligibleDocuments": len(eligible), "creditsTotal": self.credits})
        bundle = VerificationEvidenceBundle(state, tuple(docs), tuple(eligible), coverage)
        if self.checkpoint_store is not None:
            safe_coverage = {
                "provider": "tavily", "state": state, "reason": reason, "requestState": "completed",
                "requests": self.requests, "credits": response.credits,
                "creditsTotal": self.credits, "documents": len(docs), "eligibleDocuments": len(eligible),
            }
            if query_context:
                safe_coverage.update({"questionId": query_context["questionId"], "pathId": query_context["pathId"],
                                      "operation": "search", "queryIntent": query_context["intent"]})
            result = {
                "state": state,
                "documentRefs": [self._document_ref(document) for document in docs],
                "eligibleDocumentRefs": [self._document_ref(document) for document in eligible],
                "coverage": safe_coverage,
            }
            self.checkpoint_store.complete(item_key=checkpoint_key, input_sha256=checkpoint_input, result=result)
            return VerificationEvidenceBundle(state, tuple(docs), tuple(eligible), safe_coverage)
        return bundle

    def fetch_fulltext(self, *, event: EventDraft, document: DiscoveryDocument, question: Any,
                       request: Any, cutoff_at: datetime,
                       cutoff_inclusive: bool = False) -> VerificationEvidenceBundle:
        """Admit a real stored source before Extract, preserving the frozen slots.

        A source's earlier publication timestamp is provenance, not proof that
        a page fetched now had this exact body at the cutoff.  The evidence
        assessor receives that distinction explicitly.
        """
        if cutoff_at.tzinfo is None:
            raise ValueError("核验 cutoff 必须带时区")
        question, request = self._mapping(question), self._mapping(request)
        ref = self._document_ref(document)
        if (request.get("sourceRef") != ref or not question.get("questionId")
                or request.get("questionId") != question.get("questionId")
                or any(not isinstance(request.get(key), str) or not request[key].strip()
                       for key in ("reasonExcerptInsufficient", "expectedJudgmentChange"))):
            raise VerificationCheckpointError("research_fulltext_contract_invalid")
        checkpoint = self.checkpoint_store
        if checkpoint is None:
            return self._pending("fulltext_task_binding_required")
        if not isinstance(self.network_max_attempts, int) or isinstance(self.network_max_attempts, bool) or self.network_max_attempts < 1:
            return self._pending("networkMaxAttempts_missing")
        if checkpoint.leaseguard is not None:
            checkpoint.leaseguard()
        stored = store.load_document_versions(refs=[ref], db_path=self.db_path, source_keys=(self.source_key,))
        if len(stored) != 1:
            raise VerificationCheckpointError("research_fulltext_source_unknown")
        source = stored[0]
        parent_ref = (source.get("metadata") or {}).get("fulltextSourceRef")
        if source.get("fetchVersion") == "tavily-extract-fulltext-v1" and isinstance(parent_ref, Mapping):
            if (parent_ref.get("documentId") != document.document_id
                    or not isinstance(parent_ref.get("revision"), int)
                    or isinstance(parent_ref["revision"], bool) or not 1 <= parent_ref["revision"] < document.revision):
                raise VerificationCheckpointError("research_fulltext_parent_invalid")
            parent = store.load_document_versions(refs=[parent_ref], db_path=self.db_path, source_keys=(self.source_key,))
            if len(parent) != 1:
                raise VerificationCheckpointError("research_fulltext_source_unknown")
            item = parent[0]
            original = DiscoveryDocument(item["documentId"], item["revision"], item.get("publishedAt"), item["fetchedAt"],
                item.get("originalText"), item.get("excerpt"), item.get("metadata") or {})
            return self.fetch_fulltext(event=event, document=original, question=question,
                request=dict(request) | {"sourceRef": dict(parent_ref)}, cutoff_at=cutoff_at, cutoff_inclusive=cutoff_inclusive)
        with read_connection(self.db_path) as conn:
            require_schema(conn)
            identity = conn.execute("SELECT canonical_url,external_id FROM k10_source_documents WHERE document_id=? AND source_key=?",
                                    (document.document_id, self.source_key)).fetchone()
        if identity is None:
            raise VerificationCheckpointError("research_fulltext_source_unknown")
        url, external_id = identity
        # Only a URL actually returned and durably saved by the evidence
        # gateway may reach Extract.  Model-authored URLs are never accepted.
        from urllib.parse import urlparse
        from ipaddress import ip_address
        parsed = urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            return self._pending("fulltext_source_url_unavailable")
        hostname = parsed.hostname.lower().rstrip(".")
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".local", ".internal", ".localhost")):
            return self._pending("fulltext_source_url_unavailable")
        try:
            if not ip_address(hostname).is_global:
                return self._pending("fulltext_source_url_unavailable")
        except ValueError:
            pass
        client = self.client
        if client is None:
            key = get_tavily_api_key(db_path=self.db_path)
            if not key:
                return self._pending("tavily_api_key_missing")
            client = TavilySearchClient(key)
        if not callable(getattr(client, "extract", None)):
            return self._pending("tavily_extract_unavailable")

        path_id = "fulltext:" + sha256(json.dumps(ref, sort_keys=True).encode()).hexdigest()
        item_key = checkpoint.item_key(canonical_key=event.canonical_key, stage_key=event.stage_key,
            event_state=event.event_state, question_id=question["questionId"], path_id=path_id, operation="extract")
        context = {"questionId": question["questionId"], "sourceRef": ref,
                   "reasonExcerptInsufficient": request["reasonExcerptInsufficient"],
                   "expectedJudgmentChange": request["expectedJudgmentChange"], "operation": "extract"}
        digest = checkpoint.input_sha256(canonical_key=event.canonical_key, stage_key=event.stage_key,
            event_state=event.event_state, headline=event.headline, event_kind=event.event_kind,
            facts=event.facts, source_refs=[ref], cutoff_at=_text(cutoff_at),
            cutoff_inclusive=cutoff_inclusive, investigation_path=context)
        admission = store.admit_article(task_id=checkpoint.task_id, document_id=document.document_id,
            revision=document.revision, admission_kind="tavily_full_article", created_at=_text(self.clock()), db_path=self.db_path)
        admission_ref = {"taskId": checkpoint.task_id, **ref}
        if admission.get("state") not in {"admitted", "reused"}:
            pending = self._pending(str(admission.get("reason") or "fulltext_admission_failed"))
            return VerificationEvidenceBundle(pending.state, (), (), dict(pending.coverage) | {
                "operation": "extract", "admissionState": "rejected", "admissionRef": admission_ref,
                "questionId": question["questionId"], "requestState": "completed",
            })
        # Reuse the same admitted body across questions.  This also closes a
        # restart between saving a provider result and completing its article
        # or request checkpoint, without sending the paid request again.
        with read_connection(self.db_path) as conn:
            require_schema(conn)
            cached_rows = conn.execute(
                "SELECT revision,metadata_json FROM k10_source_document_versions WHERE document_id=? "
                "AND fetch_version='tavily-extract-fulltext-v1' AND original_text IS NOT NULL ORDER BY revision DESC",
                (document.document_id,),
            ).fetchall()
            old_checkpoint = conn.execute(
                "SELECT status,input_sha256 FROM k10_execution_item_checkpoints "
                "WHERE task_id=? AND item_kind='event' AND item_key=? AND stage='tavily_evidence'",
                (checkpoint.task_id, item_key),
            ).fetchone()
        if admission.get("articleState") == "missing_body":
            missing_coverage = {"provider": "tavily", "operation": "extract", "questionId": question["questionId"],
                "pathId": path_id, "admissionRef": admission_ref, "admissionState": "admitted",
                "state": "pending", "reason": "tavily_fulltext_unavailable", "requestState": "reused",
                "requests": self.requests, "creditsTotal": self.credits}
            missing_result = {"state": "pending", "documentRefs": [], "eligibleDocumentRefs": [], "coverage": missing_coverage}
            if old_checkpoint is not None and old_checkpoint[1] != digest:
                return self._pending("checkpoint_input_mismatch")
            if old_checkpoint is not None and old_checkpoint[0] == "running":
                checkpoint.complete(item_key=item_key, input_sha256=digest, result=missing_result)
            return VerificationEvidenceBundle("pending", (), (), missing_coverage)
        for cached_revision, raw_metadata in cached_rows:
            metadata = json.loads(raw_metadata)
            if metadata.get("articleAdmissionRef") != admission_ref:
                continue
            cached_ref = {"documentId": document.document_id, "revision": cached_revision}
            cached_coverage = {"provider": "tavily", "operation": "extract", "questionId": question["questionId"],
                "pathId": path_id, "admissionRef": admission_ref, "admissionState": "fulfilled",
                "state": metadata["fulltextCoverageState"], "reason": metadata["fulltextCoverageReason"],
                "credits": metadata.get("extractCredits"), "requestState": "reused",
                "requests": self.requests, "creditsTotal": self.credits}
            cached_result = {"state": cached_coverage["state"], "documentRefs": [cached_ref],
                "eligibleDocumentRefs": [cached_ref] if cached_coverage["state"] == "available" else [],
                "coverage": cached_coverage}
            if old_checkpoint is not None and old_checkpoint[1] != digest:
                return self._pending("checkpoint_input_mismatch")
            store.record_article_outcome(task_id=checkpoint.task_id, document_id=document.document_id,
                revision=document.revision, state="completed", reason_code=None, updated_at=_text(self.clock()), db_path=self.db_path)
            if old_checkpoint is not None and old_checkpoint[0] == "running":
                checkpoint.complete(item_key=item_key, input_sha256=digest, result=cached_result)
            return self._restore_checkpoint_bundle(cached_result)
        claim = checkpoint.claim(item_key=item_key, input_sha256=digest, network_max_attempts=self.network_max_attempts)
        self.requests = claim.requests
        if claim.state == "reused":
            return self._restore_checkpoint_bundle(claim.result)
        if claim.state == "pending":
            return self._pending(claim.reason or "verification_checkpoint_pending")
        attempt_id, blocked = self._reserve_search(item_key=item_key, input_sha256=digest, attempt=self.requests)
        if blocked:
            if blocked in {"execution_paused", "execution_not_configured", "execution_retired_by_user"}:
                checkpoint.defer_without_request(item_key=item_key, input_sha256=digest, safe_error_code=blocked)
            else:
                checkpoint.fail_retryable(item_key=item_key, input_sha256=digest, safe_error_code=blocked)
            return self._pending(blocked)
        response: TavilyExtractResponse | None = None
        try:
            response = client.extract(url)
        except Exception:
            pass
        if attempt_id is not None:
            if response is not None and response.reason == "tavily_extract_outcome_unknown":
                self._settle_search(attempt_id=attempt_id, response=None)
            else:
                self._settle_search(attempt_id=attempt_id, response=response)
        if response is None or (not response.ok and response.reason != "tavily_fulltext_unavailable"):
            code = "tavily_extract_outcome_unknown" if response is None else response.reason
            checkpoint.fail_retryable(item_key=item_key, input_sha256=digest, safe_error_code=code)
            store.record_article_outcome(task_id=checkpoint.task_id, document_id=document.document_id,
                revision=document.revision, state="failed", reason_code=code, updated_at=_text(self.clock()), db_path=self.db_path)
            return self._pending(code)
        obtained_at = self.clock()
        obtained_text = _text(obtained_at)
        if response.credits is not None:
            self.credits += response.credits
        record_usage(task="discovery", result=None, trade_date=obtained_at.date(),
            outcome="search_success" if response.ok else "search_failed", tavily_credits=response.credits,
            searched=True, duration_ms=response.wall_ms,
            failure_reason=None if response.ok else response.reason, db_path=self.db_path)
        coverage: dict[str, Any] = {
            "provider": "tavily", "operation": "extract", "questionId": question["questionId"], "pathId": path_id,
            "admissionRef": admission_ref, "admissionState": "fulfilled" if response.ok else "admitted",
            "state": "pending", "reason": response.reason, "requestState": "completed",
            "requests": self.requests, "credits": response.credits, "creditsTotal": self.credits,
        }
        documents: tuple[DiscoveryDocument, ...] = ()
        eligible: tuple[DiscoveryDocument, ...] = ()
        if response.ok and response.raw_content:
            published = _aware_instant(source.get("publishedAt"))
            precision = source.get("publishedPrecision")
            time_eligible = bool(published and ((precision == "exact" and
                (published < cutoff_at or (cutoff_inclusive and published == cutoff_at)))
                or (precision == "date" and published.date() < cutoff_at.date())))
            metadata = dict(source.get("metadata") or {})
            metadata.update({"fulltextSourceRef": ref, "articleAdmissionRef": admission_ref,
                "questionId": question["questionId"], "bodyObservedAt": obtained_text,
                "contentVersionAtCutoff": "unconfirmed", "requestId": response.request_id,
                "firstSeenAt": (source.get("metadata") or {}).get("firstSeenAt") or source.get("fetchedAt"),
                "sourcePublicationInherited": True, "extractCredits": response.credits,
                "fulltextCoverageState": "available" if time_eligible else "pending",
                "fulltextCoverageReason": "ok" if time_eligible else "fulltext_publication_time_unconfirmed"})
            payload = {"body": response.raw_content, "sourceRef": ref, "metadata": metadata}
            version = store.append_document_version(document_id=document.document_id, source_key=self.source_key,
                external_id=external_id, canonical_url=url,
                content_sha256=sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                published_at=source.get("publishedAt"), published_precision=source["publishedPrecision"],
                fetched_at=obtained_text, original_text=response.raw_content, excerpt=source.get("excerpt"),
                fetch_version="tavily-extract-fulltext-v1", metadata=metadata, created_at=obtained_text, db_path=self.db_path)
            full = DiscoveryDocument(version.document_id, version.revision, source.get("publishedAt"),
                obtained_text, response.raw_content, source.get("excerpt"), metadata)
            documents = (full,)
            if time_eligible:
                eligible = documents
            coverage.update({"state": "available" if eligible else "pending",
                "reason": "ok" if eligible else "fulltext_publication_time_unconfirmed"})
        store.record_article_outcome(task_id=checkpoint.task_id, document_id=document.document_id,
            revision=document.revision, state="completed" if documents else "missing_body",
            reason_code=None if documents else "tavily_fulltext_unavailable", updated_at=obtained_text, db_path=self.db_path)
        coverage.update({"documents": len(documents), "eligibleDocuments": len(eligible)})
        checkpoint.complete(item_key=item_key, input_sha256=digest, result={
            "state": coverage["state"], "documentRefs": [self._document_ref(item) for item in documents],
            "eligibleDocumentRefs": [self._document_ref(item) for item in eligible], "coverage": coverage,
        })
        return VerificationEvidenceBundle(str(coverage["state"]), documents, eligible, coverage)

    @staticmethod
    def _document_ref(document: DiscoveryDocument) -> dict[str, Any]:
        return {"documentId": document.document_id, "revision": document.revision}

    def _pending(self, reason: str) -> VerificationEvidenceBundle:
        return VerificationEvidenceBundle("pending", (), (), {
            "provider": "tavily", "state": "pending", "reason": reason,
            "requestState": "pending", "requests": self.requests,
            "credits": self.credits,
        })

    def _restore_checkpoint_bundle(self, result: Mapping[str, Any] | None) -> VerificationEvidenceBundle:
        if not isinstance(result, Mapping):
            raise VerificationCheckpointError("verification_checkpoint_corrupt")
        raw_refs = result.get("documentRefs")
        raw_eligible = result.get("eligibleDocumentRefs")
        coverage = result.get("coverage")
        if not isinstance(raw_refs, list) or not isinstance(raw_eligible, list) or not isinstance(coverage, Mapping):
            raise VerificationCheckpointError("verification_checkpoint_corrupt")
        docs = store.load_document_versions(refs=raw_refs, db_path=self.db_path, source_keys=(self.source_key,))
        if len(docs) != len(raw_refs):
            return self._pending("checkpoint_documents_unavailable")
        restored = tuple(DiscoveryDocument(
            str(item["documentId"]), int(item["revision"]), item.get("publishedAt"), str(item["fetchedAt"]),
            item.get("originalText"), item.get("excerpt"), item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {},
        ) for item in docs)
        index = {(item.document_id, item.revision): item for item in restored}
        try:
            eligible = tuple(index[(str(ref["documentId"]), int(ref["revision"]))] for ref in raw_eligible)
        except (KeyError, TypeError, ValueError):
            return self._pending("checkpoint_documents_unavailable")
        safe_coverage = dict(coverage)
        safe_coverage.update({"requestState": "reused", "requests": self.requests, "creditsTotal": self.credits})
        state = result.get("state")
        if state not in {"available", "pending"}:
            raise VerificationCheckpointError("verification_checkpoint_corrupt")
        return VerificationEvidenceBundle(state, restored, eligible, safe_coverage)


__all__ = ["SearchClient", "TavilyEvidenceGateway", "VerificationEvidenceBundle"]
