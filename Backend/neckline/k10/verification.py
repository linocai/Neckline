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
                 metadata_resolver: PublicationMetadataResolver | None = None) -> None:
        self.db_path, self.request_limit = db_path, request_limit
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.metadata_resolver = metadata_resolver
        self.requests = 0
        self.credits = 0

    def fetch(self, *, event: EventDraft, retrieved_at: datetime, cutoff_at: datetime,
              cutoff_inclusive: bool = False) -> VerificationEvidenceBundle:
        if cutoff_at.tzinfo is None:
            raise ValueError("核验 cutoff 必须带时区")
        if not isinstance(self.request_limit, int) or isinstance(self.request_limit, bool) or self.request_limit < 1:
            return VerificationEvidenceBundle("pending", (), (), {"provider": "tavily", "state": "pending", "reason": "maxVerificationRequests_missing", "requests": self.requests})
        if self.requests >= self.request_limit:
            return VerificationEvidenceBundle("pending", (), (), {"provider": "tavily", "state": "pending", "reason": "request_limit_reached", "requests": self.requests, "credits": self.credits})
        client = self.client
        if client is None:
            key = get_tavily_api_key(db_path=self.db_path)
            if not key:
                return VerificationEvidenceBundle("pending", (), (), {"provider": "tavily", "state": "pending", "reason": "tavily_api_key_missing", "requests": self.requests})
            client = TavilySearchClient(key)
        # This is user-readable event language only.  Internal stage keys are
        # not query terms and materially reduce search precision in practice.
        query = event.headline[:400]
        self.requests += 1
        response = client.search(query)
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
        coverage: dict[str, Any] = {"provider": "tavily", "query": query, "requests": self.requests,
                                    "requestLimit": self.request_limit, "state": "pending", "credits": response.credits,
                                    "reason": response.reason}
        if response.credits is not None:
            self.credits += response.credits
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
        reason = "ok" if state == "available" else (response.reason if not response.ok else "coverage_gap_or_usage_unavailable")
        coverage.update({"state": state, "reason": reason, "documents": len(docs), "eligibleDocuments": len(eligible), "creditsTotal": self.credits})
        return VerificationEvidenceBundle(state, tuple(docs), tuple(eligible), coverage)


__all__ = ["SearchClient", "TavilyEvidenceGateway", "VerificationEvidenceBundle"]
