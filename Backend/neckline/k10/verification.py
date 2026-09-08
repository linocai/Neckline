"""Optional Tavily evidence for K10 event verification; never a market-wide source."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from neckline.search.tavily import TavilySearchClient, TavilySearchResponse
from neckline.settings_store import get_tavily_api_key
from neckline.llm.usage import record as record_usage

from .discovery import DiscoveryDocument, EventDraft
from .source_metadata import PublicationMetadataResolver
from .types import DocumentVersion
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
    """One event-specific Tavily query, bounded by the frozen discovery policy."""
    source_key = "tavily_verification"

    def __init__(self, *, db_path: Path, request_limit: int | None, client: SearchClient | None = None,
                 clock: Callable[[], datetime] | None = None,
                 metadata_resolver: PublicationMetadataResolver | None = None, task_id: str | None = None,
                 leaseguard: Callable[[], None] | None = None,
                 checkpoint_store: VerificationCheckpointStore | None = None,
                 network_max_attempts: int | None = None, lease_owner: str | None = None,
                 spend_enforced: bool = False) -> None:
        self.db_path, self.request_limit = db_path, request_limit
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.metadata_resolver = metadata_resolver
        if checkpoint_store is not None and task_id is not None and checkpoint_store.task_id != task_id:
            raise ValueError("Tavily 核验 task_id 与检查点不一致")
        self.checkpoint_store = checkpoint_store or (
            VerificationCheckpointStore(db_path=db_path, task_id=task_id, leaseguard=leaseguard, lease_owner=lease_owner) if task_id else None
        )
        self.network_max_attempts = network_max_attempts
        self.spend_enforced = spend_enforced
        self.requests, self.credits = self.checkpoint_store.budget_snapshot() if self.checkpoint_store else (0, 0)

    def _reserve_search(self, *, item_key: str, input_sha256: str, attempt: int) -> tuple[str | None, str | None]:
        """Reserve one Tavily HTTP attempt after its durable cache miss."""
        if not self.spend_enforced:
            return None, None
        if self.checkpoint_store is None:
            return None, "execution_not_configured"
        profile = store.task_execution_profile(task_id=self.checkpoint_store.task_id, db_path=self.db_path)
        payload = profile.get("payload") if isinstance(profile, Mapping) else None
        discovery = payload.get("discovery") if isinstance(payload, Mapping) else None
        budgets = discovery.get("budgets") if isinstance(discovery, Mapping) else None
        reservation = budgets.get("reservation") if isinstance(budgets, Mapping) else None
        limits = reservation.get("search") if isinstance(reservation, Mapping) else None
        fields = {
            "calls": "maxModelCalls", "inputTokens": "maxInputTokens", "outputTokens": "maxOutputTokens",
            "totalTokens": "maxTotalTokens", "fullTextCalls": "maxFullTextCalls", "retries": "maxRetries",
            "searchRequests": "maxSearchRequests", "searchCredits": "maxSearchCredits",
        }
        if (not isinstance(limits, Mapping)
                or any(isinstance(limits.get(key), bool) or not isinstance(limits.get(key), int) or limits[key] < 0
                       for key in fields.values())):
            return None, "execution_not_configured"
        # A zero cap is a valid approved way to close this external stage.  It
        # is a budget decision, not a malformed configuration, and must make
        # no HTTP attempt.  Credits are conservatively reserved before Tavily
        # can report its actual charge.
        if limits["maxSearchRequests"] < 1 or limits["maxSearchCredits"] < 1:
            return None, "pending_budget"
        if attempt > 1 and limits["maxRetries"] < 1:
            return None, "pending_budget"
        amounts = {field: 0 for field in fields}
        amounts["searchRequests"] = 1
        amounts["searchCredits"] = int(limits["maxSearchCredits"])
        if attempt > 1:
            amounts["retries"] = 1
        key = "tavily:" + sha256(f"{self.checkpoint_store.task_id}\x1f{item_key}\x1f{input_sha256}\x1f{attempt}".encode()).hexdigest()
        admitted = store.admit_execution_spend(
            task_id=self.checkpoint_store.task_id, item_key=item_key, stage="search", kind="search", reservation_key=key,
            reserved=amounts, created_at=_text(self.clock()), db_path=self.db_path,
        )
        if admitted.get("state") != "reserved":
            return None, {
                "paused": "execution_paused", "not_configured": "execution_not_configured",
                "pending_budget": "pending_budget", "pending_outcome": "provider_request_outcome_unknown",
                "reused": "tavily_reservation_reused",
            }.get(str(admitted.get("state")), "tavily_budget_admission_failed")
        reservation_id = admitted.get("reservationId")
        return (str(reservation_id), None) if isinstance(reservation_id, str) else (None, "tavily_budget_admission_failed")

    def _settle_search(self, *, reservation_id: str, response: TavilySearchResponse | None, attempt: int) -> None:
        if response is None or not response.ok or response.credits is None:
            store.settle_execution_spend(reservation_id=reservation_id, outcome="unknown", actual=None,
                                          settled_at=_text(self.clock()), db_path=self.db_path)
            return
        actual = {"calls": 0, "inputTokens": 0, "outputTokens": 0, "totalTokens": 0,
                  "fullTextCalls": 0, "retries": 1 if attempt > 1 else 0,
                  "searchRequests": 1, "searchCredits": int(response.credits)}
        store.settle_execution_spend(reservation_id=reservation_id, outcome="settled", actual=actual,
                                      settled_at=_text(self.clock()), db_path=self.db_path)

    def fetch(self, *, event: EventDraft, retrieved_at: datetime, cutoff_at: datetime,
              cutoff_inclusive: bool = False) -> VerificationEvidenceBundle:
        if cutoff_at.tzinfo is None:
            raise ValueError("核验 cutoff 必须带时区")
        if not isinstance(self.request_limit, int) or isinstance(self.request_limit, bool) or self.request_limit < 1:
            return VerificationEvidenceBundle("pending", (), (), {"provider": "tavily", "state": "pending", "reason": "maxVerificationRequests_missing", "requests": self.requests})
        if self.checkpoint_store is not None and (
            not isinstance(self.network_max_attempts, int) or isinstance(self.network_max_attempts, bool)
            or self.network_max_attempts < 1
        ):
            return self._pending("networkMaxAttempts_missing")
        if self.checkpoint_store is None and self.requests >= self.request_limit:
            return VerificationEvidenceBundle("pending", (), (), {"provider": "tavily", "state": "pending", "reason": "request_limit_reached", "requests": self.requests, "credits": self.credits})
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
            )
            checkpoint_input = self.checkpoint_store.input_sha256(
                canonical_key=event.canonical_key, stage_key=event.stage_key, event_state=event.event_state,
                headline=event.headline, event_kind=event.event_kind, facts=event.facts, source_refs=source_refs,
                cutoff_at=_text(cutoff_at), cutoff_inclusive=cutoff_inclusive,
            )
            claim = self.checkpoint_store.claim(item_key=checkpoint_key, input_sha256=checkpoint_input,
                                                request_limit=self.request_limit, network_max_attempts=self.network_max_attempts)
            self.requests = claim.requests
            if claim.state == "reused":
                return self._restore_checkpoint_bundle(claim.result)
            if claim.state == "pending":
                return self._pending(claim.reason or "verification_checkpoint_pending")
        reservation_id, blocked = self._reserve_search(
            item_key=checkpoint_key or event.canonical_key, input_sha256=checkpoint_input or "unbound",
            attempt=self.requests,
        )
        if blocked is not None:
            if self.checkpoint_store is not None and checkpoint_key is not None and checkpoint_input is not None:
                self.checkpoint_store.fail_retryable(item_key=checkpoint_key, input_sha256=checkpoint_input, safe_error_code=blocked)
            return self._pending(blocked)
        # This is user-readable event language only.  Internal stage keys are
        # not query terms and materially reduce search precision in practice.
        query = event.headline[:400]
        if self.checkpoint_store is None:
            self.requests += 1
        # The checkpoint layer owns K10 retries.  A client-internal retry would
        # otherwise create billable HTTP attempts without separate admission.
        if self.spend_enforced and isinstance(client, TavilySearchClient):
            client.max_attempts = 1
        response: TavilySearchResponse | None = None
        try:
            response = client.search(query)
        except Exception:
            if self.checkpoint_store is not None:
                self.checkpoint_store.fail_retryable(item_key=checkpoint_key, input_sha256=checkpoint_input,
                                                     safe_error_code="tavily_request_outcome_unknown")
                if reservation_id is not None:
                    self._settle_search(reservation_id=reservation_id, response=None, attempt=self.requests)
                return self._pending("tavily_request_outcome_unknown")
            raise
        if reservation_id is not None:
            self._settle_search(reservation_id=reservation_id, response=response, attempt=self.requests)
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
                                    "requestLimit": self.request_limit, "state": "pending", "credits": response.credits,
                                    "reason": response_reason}
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
                        "requestId": response.request_id, "publishedDateRaw": hit.publish_date or None}
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
                "provider": "tavily", "state": state, "reason": reason, "requestState": "reserved",
                "requests": self.requests, "requestLimit": self.request_limit, "credits": response.credits,
                "creditsTotal": self.credits, "documents": len(docs), "eligibleDocuments": len(eligible),
            }
            result = {
                "state": state,
                "documentRefs": [self._document_ref(document) for document in docs],
                "eligibleDocumentRefs": [self._document_ref(document) for document in eligible],
                "coverage": safe_coverage,
            }
            self.checkpoint_store.complete(item_key=checkpoint_key, input_sha256=checkpoint_input, result=result)
            return VerificationEvidenceBundle(state, tuple(docs), tuple(eligible), safe_coverage)
        return bundle

    @staticmethod
    def _document_ref(document: DiscoveryDocument) -> dict[str, Any]:
        return {"documentId": document.document_id, "revision": document.revision}

    def _pending(self, reason: str) -> VerificationEvidenceBundle:
        return VerificationEvidenceBundle("pending", (), (), {
            "provider": "tavily", "state": "pending", "reason": reason,
            "requestState": "pending", "requests": self.requests, "requestLimit": self.request_limit,
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
