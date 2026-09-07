from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from neckline.k10.discovery import EvidenceRef, EventDraft
from neckline.k10.schema import initialize_schema
from neckline.k10.source_metadata import PublicationMetadata
from neckline.k10.store import list_source_document_versions
from neckline.k10.verification import TavilyEvidenceGateway
from neckline.search.tavily import TavilySearchClient, TavilySearchResponse
from neckline.llm.base import SearchHit


NOW = datetime(2026, 9, 7, 1, tzinfo=timezone.utc)
COMPLETED_AT = datetime(2026, 9, 7, 1, 2, tzinfo=timezone.utc)


class _Search:
    def __init__(self): self.calls = 0
    def search(self, query):
        self.calls += 1
        return TavilySearchResponse(True, query, credits=2, hits=(
            SearchHit(title="核验报道", link="https://example.invalid/evidence", content="可追溯摘录", publish_date="2026-09-06"),
            SearchHit(title="无日期", link="https://example.invalid/unknown", content="日期未知摘录", publish_date=""),
        ))


def _event():
    return EventDraft("event", "stage", "confirmed", "事件", "disclosure", {}, (EvidenceRef("doc-origin", 1),))


def test_tavily_verification_persists_exact_documents_and_actual_credits(tmp_path):
    path = tmp_path / "verify.sqlite"
    initialize_schema(path)
    search = _Search()
    gateway = TavilyEvidenceGateway(db_path=path, request_limit=1, client=search, clock=lambda: COMPLETED_AT)
    bundle = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=datetime(2026, 9, 7, tzinfo=timezone.utc))
    assert bundle.state == "available"
    assert search.calls == 1 and bundle.coverage["credits"] == 2
    rows = list_source_document_versions(cutoff_at=None, db_path=path)
    assert {item["publishedPrecision"] for item in rows} == {"date", "unknown"}
    assert {item["fetchVersion"] for item in rows} == {"tavily-basic-general-v2"}
    assert {item["fetchedAt"] for item in rows} == {"2026-09-07T01:02:00+00:00"}
    assert all(document.fetched_at == "2026-09-07T01:02:00+00:00" for document in bundle.documents)
    assert len(bundle.eligible_documents) == 1
    assert all(item.document_id.startswith("doc_") and item.revision == 1 for item in bundle.documents)
    exhausted = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=datetime(2026, 9, 7, tzinfo=timezone.utc))
    assert exhausted.state == "pending"
    assert exhausted.coverage["reason"] == "request_limit_reached"


def test_missing_verification_budget_never_reads_or_calls_tavily(tmp_path):
    path = tmp_path / "missing.sqlite"
    initialize_schema(path)
    search = _Search()
    bundle = TavilyEvidenceGateway(db_path=path, request_limit=None, client=search).fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert bundle.state == "pending"
    assert bundle.coverage["reason"] == "maxVerificationRequests_missing"
    assert search.calls == 0


def test_query_uses_readable_headline_and_unknown_tavily_date_can_use_exact_metadata(tmp_path):
    path = tmp_path / "metadata.sqlite"
    initialize_schema(path)

    class _UnknownDateSearch:
        def __init__(self): self.query = None
        def search(self, query):
            self.query = query
            return TavilySearchResponse(True, query, credits=1, hits=(
                SearchHit(title="可核正文", link="https://example.test/metadata", content="摘要", publish_date="not-a-date"),
                SearchHit(title="待核正文", link="https://example.test/unknown", content="摘要", publish_date=""),
            ))

    class _MetadataResolver:
        def __init__(self): self.urls = []
        def resolve(self, url):
            self.urls.append(url)
            if url.endswith("metadata"):
                return PublicationMetadata("2026-09-06T20:00:00+08:00", "exact", "2026-09-07T01:04:00+00:00",
                                           {"reason": "published_time_found", "httpStatus": 200})
            return PublicationMetadata(None, "unknown", "2026-09-07T01:05:00+00:00",
                                       {"reason": "published_time_unavailable", "httpStatus": 200})

    search, resolver = _UnknownDateSearch(), _MetadataResolver()
    bundle = TavilyEvidenceGateway(db_path=path, request_limit=1, client=search, clock=lambda: COMPLETED_AT,
                                   metadata_resolver=resolver).fetch(
        event=_event(), retrieved_at=NOW, cutoff_at=datetime.fromisoformat("2026-09-06T21:00:00+08:00"))
    assert search.query == "事件"
    assert resolver.urls == ["https://example.test/metadata", "https://example.test/unknown"]
    assert [item.metadata["title"] for item in bundle.eligible_documents] == ["可核正文"]
    rows = {item["metadata"]["title"]: item for item in list_source_document_versions(cutoff_at=None, db_path=path)}
    assert rows["可核正文"]["publishedAt"] == "2026-09-06T20:00:00+08:00"
    assert rows["可核正文"]["fetchedAt"] == "2026-09-07T01:04:00+00:00"
    assert rows["可核正文"]["metadata"]["publishedDateRaw"] == "not-a-date"
    resolved = rows["可核正文"]["metadata"]["metadataResolution"]
    assert resolved["provider"] == "source_metadata" and resolved["fetchedAt"] == "2026-09-07T01:04:00+00:00"
    assert rows["待核正文"]["publishedAt"] is None and rows["待核正文"]["fetchedAt"] == "2026-09-07T01:05:00+00:00"


def test_rfc2822_timestamps_with_timezones_are_exact_but_future_or_unzoned_hits_are_not_eligible(tmp_path):
    path = tmp_path / "rfc2822.sqlite"
    initialize_schema(path)

    class _RFCSearch:
        def search(self, query):
            return TavilySearchResponse(True, query, credits=1, hits=(
                SearchHit(title="已发布", link="https://example.test/rfc", content="摘要", publish_date="Mon, 10 Nov 2025 11:09:35 GMT"),
                SearchHit(title="未来", link="https://example.test/future", content="摘要", publish_date="Mon, 10 Nov 2026 11:09:35 GMT"),
                SearchHit(title="无时区", link="https://example.test/no-zone", content="摘要", publish_date="Mon, 10 Nov 2025 11:09:35"),
            ))

    bundle = TavilyEvidenceGateway(db_path=path, request_limit=1, client=_RFCSearch(), clock=lambda: COMPLETED_AT).fetch(
        event=_event(), retrieved_at=NOW, cutoff_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert bundle.state == "available"
    assert [document.metadata["title"] for document in bundle.eligible_documents] == ["已发布"]
    rows = {item["metadata"]["title"]: item for item in list_source_document_versions(cutoff_at=None, db_path=path)}
    assert rows["已发布"]["publishedAt"] == "2025-11-10T11:09:35+00:00"
    assert rows["已发布"]["publishedPrecision"] == "exact"
    assert rows["未来"]["publishedPrecision"] == "exact"
    assert rows["无时区"]["publishedAt"] is None and rows["无时区"]["publishedPrecision"] == "unknown"


@pytest.mark.parametrize("inclusive, expected", [(False, 1), (True, 2)])
def test_real_transport_preserves_hits_without_credits_and_respects_cutoff(tmp_path, inclusive, expected):
    path = tmp_path / "real-transport.sqlite"
    initialize_schema(path)
    def response(_request):
        return httpx.Response(200, json={"results":[
            {"title":"更早报道", "content":"摘要", "url":"https://example.test/earlier", "published_date":"2026-09-06T20:59:59+08:00"},
            {"title":"整点报道", "content":"摘要", "url":"https://example.test/boundary", "published_date":"2026-09-06T21:00:00+08:00"},
            {"title":"迟后报道", "content":"摘要", "url":"https://example.test/later", "published_date":"2026-09-06T21:00:01+08:00"},
            {"title":"旧事 2026-09-01", "content":"摘要提到 2026-08-20", "url":"https://example.test/unknown"},
        ]})
    client = TavilySearchClient("fixture-key", transport=httpx.MockTransport(response))
    bundle = TavilyEvidenceGateway(db_path=path, request_limit=1, client=client).fetch(
        event=_event(), retrieved_at=NOW, cutoff_at=datetime.fromisoformat("2026-09-06T21:00:00+08:00"),
        cutoff_inclusive=inclusive)
    assert bundle.state == "pending" and bundle.coverage["credits"] is None
    assert len(bundle.documents) == 4 and len(bundle.eligible_documents) == expected
    rows = list_source_document_versions(cutoff_at=None, db_path=path)
    assert len(rows) == 4 and sum(item["publishedPrecision"] == "unknown" for item in rows) == 1
