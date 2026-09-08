from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import httpx
import pytest

from neckline.k10.discovery import EvidenceRef, EventDraft
from neckline.k10.schema import initialize_schema
from neckline.k10.source_metadata import PublicationMetadata
from neckline.k10 import store
from neckline.k10.store import list_source_document_versions
from neckline.k10.verification_checkpoints import VerificationCheckpointError
from neckline.k10.verification import TavilyEvidenceGateway
from neckline.search.tavily import TavilySearchClient, TavilySearchResponse
from neckline.llm.base import SearchHit
from tests.k10_v306_fixture import append_approved_execution_profile


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


def _bound_task(path, *, task_id="task-verification", opened=True):
    created = NOW.isoformat()
    store.enqueue_task(task_id=task_id, kind="evening_scan", idempotency_key=task_id,
                       input_version="input-v1", input_cutoff_at=created, payload={}, budget={},
                       created_at=created, db_path=path)
    config_id, revision = append_approved_execution_profile(
        db_path=path, created_at=created, config_id=f"{task_id}-execution",
    )
    store.bind_task_execution(task_id=task_id, execution_config_id=config_id, execution_config_revision=revision,
                              binding_kind="scheduled", bound_at=created, db_path=path)
    if opened:
        store.set_run_control(state="open", reason_code="fixture", changed_at=created, changed_by="test", db_path=path)
    return task_id



def test_tavily_verification_persists_exact_documents_and_actual_credits(tmp_path):
    path = tmp_path / "verify.sqlite"
    initialize_schema(path)
    search = _Search()
    gateway = TavilyEvidenceGateway(db_path=path, client=search, clock=lambda: COMPLETED_AT)
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
    repeated = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=datetime(2026, 9, 7, tzinfo=timezone.utc))
    assert repeated.state == "available" and search.calls == 2


def test_unbound_verification_has_no_aggregate_request_cap(tmp_path):
    path = tmp_path / "missing.sqlite"
    initialize_schema(path)
    search = _Search()
    bundle = TavilyEvidenceGateway(db_path=path, client=search).fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert bundle.state == "available" and search.calls == 1


def test_tavily_attempt_refuses_a_paused_v3_task_before_http(tmp_path):
    path = tmp_path / "strict-paused.sqlite"
    initialize_schema(path)
    task_id = _bound_task(path, opened=False)
    search = _Search()
    gateway = TavilyEvidenceGateway(
        db_path=path, client=search, task_id=task_id,
        leaseguard=lambda: None, network_max_attempts=1,
    )
    bundle = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert bundle.state == "pending"
    assert bundle.coverage["reason"] == "execution_paused"
    assert search.calls == 0
    assert store.external_attempt_summary(task_id=task_id, db_path=path)["started"] == 0
    # A control-plane rejection preceded HTTP.  Reopening must leave the
    # event's sole allowed provider attempt intact.
    store.set_run_control(state="open", reason_code="fixture_resume", changed_at=COMPLETED_AT.isoformat(),
                          changed_by="test", db_path=path)
    resumed = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert resumed.state == "available" and search.calls == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT status,attempt_count,network_attempt_count FROM k10_execution_item_checkpoints"
        ).fetchone() == ("completed", 1, 1)


def test_task_bound_tavily_reuses_frozen_bundle_across_restart(tmp_path):
    path = tmp_path / "task-verify.sqlite"
    initialize_schema(path)
    task_id = _bound_task(path)
    first_search = _Search()
    first = TavilyEvidenceGateway(db_path=path, client=first_search, clock=lambda: COMPLETED_AT,
                                  task_id=task_id, leaseguard=lambda: None, network_max_attempts=1)
    bundle = first.fetch(event=_event(), retrieved_at=NOW, cutoff_at=datetime(2026, 9, 7, tzinfo=timezone.utc))
    assert bundle.state == "available" and first_search.calls == 1
    restarted_search = _Search()
    restarted = TavilyEvidenceGateway(db_path=path, client=restarted_search, task_id=task_id,
                                      leaseguard=lambda: None, network_max_attempts=1)
    recovered = restarted.fetch(event=_event(), retrieved_at=NOW, cutoff_at=datetime(2026, 9, 7, tzinfo=timezone.utc))
    assert recovered.state == "available" and restarted_search.calls == 0
    assert recovered.coverage["requestState"] == "reused"
    assert [(item.document_id, item.revision) for item in recovered.documents] == [
        (item.document_id, item.revision) for item in bundle.documents
    ]
    other = EventDraft("historical:event", "stage", "confirmed", "历史事件", "disclosure", {}, (EvidenceRef("doc-origin", 1),))
    independent = restarted.fetch(event=other, retrieved_at=NOW, cutoff_at=datetime(2026, 9, 7, tzinfo=timezone.utc))
    assert independent.state == "available" and restarted_search.calls == 1
    drifted = restarted.fetch(event=_event(), retrieved_at=NOW, cutoff_at=datetime(2026, 9, 8, tzinfo=timezone.utc))
    assert drifted.state == "pending" and drifted.coverage["reason"] == "checkpoint_input_mismatch"
    assert restarted_search.calls == 1


def test_same_canonical_distinct_stage_is_independently_cached_and_reused_after_restart(tmp_path):
    path = tmp_path / "staged-task-verify.sqlite"
    initialize_schema(path)
    task_id = _bound_task(path)
    first_search = _Search()
    first = TavilyEvidenceGateway(db_path=path, client=first_search, clock=lambda: COMPLETED_AT,
                                  task_id=task_id, leaseguard=lambda: None, network_max_attempts=1)
    original = _event()
    later = EventDraft("event", "implementation", "confirmed", "事件进入实施", "disclosure", {"phase": 2},
                       (EvidenceRef("doc-origin", 1),))
    assert first.fetch(event=original, retrieved_at=NOW, cutoff_at=NOW).state == "available"
    assert first.fetch(event=later, retrieved_at=NOW, cutoff_at=NOW).state == "available"
    assert first_search.calls == 2
    restarted_search = _Search()
    restarted = TavilyEvidenceGateway(db_path=path, client=restarted_search, task_id=task_id,
                                      leaseguard=lambda: None, network_max_attempts=1)
    assert restarted.fetch(event=original, retrieved_at=NOW, cutoff_at=NOW).coverage["requestState"] == "reused"
    assert restarted.fetch(event=later, retrieved_at=NOW, cutoff_at=NOW).coverage["requestState"] == "reused"
    assert restarted_search.calls == 0


def test_task_bound_network_failure_conservatively_records_unknown_attempt(tmp_path):
    path = tmp_path / "unknown-outcome.sqlite"
    initialize_schema(path)
    task_id = _bound_task(path)

    class _Fails:
        def __init__(self): self.calls = 0
        def search(self, _query):
            self.calls += 1
            raise TimeoutError("provider timeout detail must not escape")

    failed_search = _Fails()
    gateway = TavilyEvidenceGateway(db_path=path, client=failed_search, task_id=task_id,
                                    leaseguard=lambda: None, network_max_attempts=1)
    pending = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert pending.coverage["reason"] == "tavily_request_outcome_unknown" and failed_search.calls == 1
    assert "provider timeout" not in str(pending.coverage)
    retry = TavilyEvidenceGateway(db_path=path, client=_Search(), task_id=task_id,
                                  leaseguard=lambda: None, network_max_attempts=1)
    again = retry.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert again.coverage["reason"] == "network_attempts_exhausted"
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT status,network_attempt_count,safe_error_code FROM k10_execution_item_checkpoints").fetchone()
    assert row == ("failed", 1, "tavily_request_outcome_unknown")


def test_task_bound_network_retry_is_explicitly_limited_per_event(tmp_path):
    path = tmp_path / "retryable-outcome.sqlite"
    initialize_schema(path)
    task_id = _bound_task(path)

    class _Flaky:
        def __init__(self): self.calls = 0
        def search(self, query):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("temporary provider issue")
            return TavilySearchResponse(True, query, credits=1, hits=(
                SearchHit(title="核验报道", link="https://example.invalid/retry", content="可追溯摘录", publish_date="2026-09-06"),
            ))

    search = _Flaky()
    gateway = TavilyEvidenceGateway(db_path=path, client=search, clock=lambda: COMPLETED_AT,
                                    task_id=task_id, leaseguard=lambda: None, network_max_attempts=2)
    assert gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW).coverage["reason"] == "tavily_request_outcome_unknown"
    recovered = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert recovered.state == "available" and search.calls == 2
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT status,attempt_count,network_attempt_count FROM k10_execution_item_checkpoints").fetchone()
    assert row == ("completed", 2, 2)
    restarted_search = _Search()
    restarted = TavilyEvidenceGateway(db_path=path, client=restarted_search, task_id=task_id,
                                      leaseguard=lambda: None, network_max_attempts=2)
    assert restarted.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW).coverage["requestState"] == "reused"
    assert restarted_search.calls == 0


def test_task_bound_failed_provider_response_uses_the_same_bounded_retry_policy(tmp_path):
    path = tmp_path / "failed-response.sqlite"
    initialize_schema(path)
    task_id = _bound_task(path)

    class _ResponseThenSuccess:
        def __init__(self): self.calls = 0
        def search(self, query):
            self.calls += 1
            if self.calls == 1:
                return TavilySearchResponse(False, query, credits=1, hits=(), reason="private provider text")
            return TavilySearchResponse(True, query, credits=1, hits=(
                SearchHit(title="核验报道", link="https://example.invalid/retry-response", content="可追溯摘录", publish_date="2026-09-06"),
            ))

    search = _ResponseThenSuccess()
    gateway = TavilyEvidenceGateway(db_path=path, client=search, clock=lambda: COMPLETED_AT,
                                    task_id=task_id, leaseguard=lambda: None, network_max_attempts=2)
    first = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert first.coverage["reason"] == "tavily_response_unavailable"
    assert "private provider" not in str(first.coverage)
    assert gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW).state == "available"
    assert search.calls == 2


def test_missing_key_does_not_claim_an_external_attempt_or_discard_event(tmp_path, monkeypatch):
    path = tmp_path / "no-key.sqlite"
    initialize_schema(path)
    task_id = _bound_task(path)
    monkeypatch.setattr("neckline.k10.verification.get_tavily_api_key", lambda **_kwargs: None)
    bundle = TavilyEvidenceGateway(db_path=path, task_id=task_id, leaseguard=lambda: None,
                                   network_max_attempts=1).fetch(
        event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert bundle.state == "pending" and bundle.coverage["reason"] == "tavily_api_key_missing"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_execution_item_checkpoints").fetchone()[0] == 0


def test_lost_lease_prevents_attempt_and_provider_call(tmp_path):
    path = tmp_path / "lost-lease.sqlite"
    initialize_schema(path)
    task_id = _bound_task(path)
    search = _Search()
    gateway = TavilyEvidenceGateway(db_path=path, client=search, task_id=task_id,
                                    leaseguard=lambda: (_ for _ in ()).throw(VerificationCheckpointError("lost_lease")),
                                    network_max_attempts=1)
    with pytest.raises(VerificationCheckpointError, match="lost_lease"):
        gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert search.calls == 0
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_execution_item_checkpoints").fetchone()[0] == 0


def test_transactional_lease_check_rejects_former_owner_after_takeover(tmp_path):
    path = tmp_path / "lease-takeover.sqlite"
    initialize_schema(path)
    task_id = _bound_task(path)
    former = store.claim_task_by_id(task_id=task_id, worker_id="former", now=NOW,
                                    lease_for=timedelta(seconds=1), db_path=path)
    assert former is not None and former.lease_owner == "former"
    current = store.claim_task_by_id(task_id=task_id, worker_id="current", now=NOW + timedelta(seconds=2),
                                     lease_for=timedelta(minutes=1), db_path=path)
    assert current is not None and current.lease_owner == "current"
    search = _Search()
    stale = TavilyEvidenceGateway(db_path=path, client=search, task_id=task_id,
                                  leaseguard=lambda: None, lease_owner="former", network_max_attempts=1)
    with pytest.raises(store.K10Conflict, match="租约"):
        stale.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert search.calls == 0
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_execution_item_checkpoints").fetchone()[0] == 0


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
    bundle = TavilyEvidenceGateway(db_path=path, client=search, clock=lambda: COMPLETED_AT,
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
    # Publication metadata is a bounded timestamp read.  Neither it nor the
    # Tavily response may archive a raw page body for later model input.
    assert all(row["originalText"] is None for row in rows.values())


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

    bundle = TavilyEvidenceGateway(db_path=path, client=_RFCSearch(), clock=lambda: COMPLETED_AT).fetch(
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
    bundle = TavilyEvidenceGateway(db_path=path, client=client).fetch(
        event=_event(), retrieved_at=NOW, cutoff_at=datetime.fromisoformat("2026-09-06T21:00:00+08:00"),
        cutoff_inclusive=inclusive)
    assert bundle.state == "pending" and bundle.coverage["credits"] is None
    assert len(bundle.documents) == 4 and len(bundle.eligible_documents) == expected
    rows = list_source_document_versions(cutoff_at=None, db_path=path)
    assert len(rows) == 4 and sum(item["publishedPrecision"] == "unknown" for item in rows) == 1
