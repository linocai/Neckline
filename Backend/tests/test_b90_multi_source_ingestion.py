"""Approved source collections retain independent coverage and original identities."""
from dataclasses import dataclass, field
from datetime import date, timedelta, timezone
import sqlite3

import pytest

from neckline.k10 import store
from neckline.k10.ingestion import ingest_to_sqlite
from neckline.k10.pipeline import _docs_for_window
from neckline.k10.schema import initialize_schema
from neckline.k10.sources import SourceCoverage, SourceDocumentInput, SourceFetchResult
from neckline.k10.windows import morning_window
from tests.v340_acceptance_fixture import actual_api


@dataclass
class Adapter:
    coverage: SourceCoverage
    result: SourceFetchResult
    fail: bool = False
    requests: list = field(default_factory=list)

    def fetch_incremental(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("isolated source unavailable")
        return self.result


def coverage(key):
    return SourceCoverage(key, "deterministic news fixture", "offline test only", "cursor",
                          "published_at", "published_at", True)


@pytest.mark.parametrize("second_state", ("complete", "partial", "failed"))
def test_two_source_requests_keep_fixed_scope_and_original_refs(tmp_path, second_state):
    database = tmp_path / "multi.sqlite"
    initialize_schema(database)
    window = morning_window(observation_day=date(2026, 9, 23))
    fetched = window.cutoff_at + timedelta(minutes=1)

    def document(key, published, *, excerpt=False, unknown=False):
        return SourceDocumentInput(
            key, "https://fixture.invalid/shared-original", None if excerpt else "原始消息的完整正文",
            "仅取得摘录" if excerpt else None, published, "unknown" if unknown else "exact",
            fetched, "b90-source-fixture", {"title": key},
        )

    original = document("same-event", window.start_at)
    documents_a = (
        original, original,  # repeat within one source must not create a new revision
        document("cutoff", window.cutoff_at.astimezone(timezone.utc)),
        document("old", window.start_at - timedelta(seconds=1)),
        document("future", window.cutoff_at + timedelta(seconds=1)),
        document("unknown", None, unknown=True),
    )
    documents_b = (document("same-event", window.start_at.astimezone(timezone.utc), excerpt=True),)
    first = Adapter(coverage("news-a"), SourceFetchResult(documents_a, "a-next", window.cutoff_at, 1, 1, True))
    second = Adapter(coverage("news-b"), SourceFetchResult(
        documents_b, "b-next", window.cutoff_at, 1, 1 if second_state == "complete" else 2,
        second_state == "complete", errors=("second page unavailable",) if second_state == "partial" else (),
    ), fail=second_state == "failed")
    watermarks = {"news-a": window.start_at - timedelta(days=13), "news-b": window.start_at - timedelta(days=1)}
    cursors = {"news-a": "a-before", "news-b": "b-before"}
    run = ingest_to_sqlite(db_path=database, scan_id="multi", window=window, adapters=(first, second),
        source_watermarks=watermarks, source_cursors=cursors, config_id=None, config_revision=None,
        created_at=window.cutoff_at, completed_at=fetched)
    assert run.state == ("completed" if second_state == "complete" else "partial")
    for adapter in (first, second):
        request, = adapter.requests
        assert request.window == window  # stale collection progress never widens overnight scope
        assert request.source_success_watermark == watermarks[adapter.coverage.source_key]
        assert request.previous_cursor == cursors[adapter.coverage.source_key]
    assert run.outcomes[0].duplicate_documents == 1
    assert run.outcomes[0].uncertain_publication_time_documents == 1
    assert run.outcomes[1].state == {"complete": "completed", "partial": "partial", "failed": "failed"}[second_state]
    assert store.latest_source_watermark(source_key="news-a", db_path=database)["cursorValue"] == "a-next"
    watermark_b = store.latest_source_watermark(source_key="news-b", db_path=database)
    assert (watermark_b["cursorValue"] if watermark_b else None) == ("b-next" if second_state == "complete" else None)
    refs = [ref for outcome in run.outcomes for ref in outcome.coverage.get("newDocumentRefs", [])]
    readable = _docs_for_window(window=window, db_path=database, completed_at=fetched,
                                source_keys=("news-a", "news-b"), current_refs=refs)
    assert sorted(item.metadata["title"] for item in readable) == (
        ["cutoff", "same-event"] if second_state == "failed" else ["cutoff", "same-event", "same-event"])
    assert len({item.document_id for item in readable}) == len(readable)
    # Same news from another source retains its own document identity and
    # excerpt/original truth. Event reconciliation may merge it later, never
    # by overwriting one source's saved raw material here.
    with actual_api(database, config_id="unused", config_revision=1, execution_id="unused", execution_revision=1) as client:
        for item in readable:
            response = client.get(f"/api/v1/k10/documents/{item.document_id}", params={"revision": item.revision})
            assert response.status_code == 200
            value = response.json()
            assert value["contentKind"] == ("original" if item.metadata["sourceKey"] == "news-a" else "excerpt")
            assert value["sourceKey"] == item.metadata["sourceKey"]
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT count(*) FROM k10_source_document_versions WHERE revision<>1").fetchone()[0] == 0
        before = conn.execute("SELECT count(*) FROM k10_source_document_versions").fetchone()[0]
    ingest_to_sqlite(db_path=database, scan_id="multi-again", window=window, adapters=(first, second),
        source_watermarks=watermarks, source_cursors=cursors, config_id=None, config_revision=None,
        created_at=window.cutoff_at, completed_at=fetched)
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT count(*) FROM k10_source_document_versions").fetchone()[0] == before


@pytest.mark.parametrize("second_state", ("complete", "partial", "failed"))
def test_real_cli_worker_reads_explicit_sources_without_sharing_cursors(tmp_path, monkeypatch, second_state):
    """A source collection is usable by the report producer, not only ingestion helpers."""
    from dataclasses import replace
    from neckline.k10 import pipeline
    from tests import v340_acceptance_fixture as base
    from tests.test_v350_cli_api import DirectRoundTransport, explicit_bindings

    monkeypatch.setattr(base, "TITLE_COUNT", 12)
    monkeypatch.setattr(base, "DeterministicTransport", DirectRoundTransport)
    first_key, second_key = "tushare-major-news", "b90-independent-news"
    keys = (first_key, second_key)
    starts = {first_key: base.NOW - timedelta(hours=2), second_key: base.NOW - timedelta(hours=3)}
    observed = {key: [] for key in keys}
    append_config = store.append_run_config

    def configured_sources(**kwargs):
        payload = dict(kwargs["payload"])
        payload["sourceAdapters"] = [{"key": key, "lateArrivalReplaySeconds": 0} for key in keys]
        return append_config(**(kwargs | {"payload": payload}))

    monkeypatch.setattr(store, "append_run_config", configured_sources)
    seed = base.seed_database

    def seeded(*args, **kwargs):
        bindings = seed(*args, **kwargs)
        database = args[0]
        for key in keys:
            store.append_source_watermark(watermark_id="before-" + key, source_key=key,
                cursor_value="cursor-" + key, success_cutoff_at=starts[key].isoformat(),
                fetched_at=starts[key].isoformat(), created_at=starts[key].isoformat(),
                scan_id=None, db_path=database)
        return bindings

    monkeypatch.setattr(base, "seed_database", seeded)

    class Source:
        def __init__(self, key, parity):
            self.coverage = coverage(key)
            self.parity = parity

        def fetch_incremental(self, request):
            key = self.coverage.source_key
            observed[key].append(request)
            if key == second_key and second_state == "failed":
                raise RuntimeError("isolated second source unavailable")
            original = base._FullScaleNews(token="fixture-token", request_bound=128).fetch_incremental(request)
            documents = tuple(item for i, item in enumerate(original.documents) if i % 2 == self.parity)
            return replace(original, documents=documents, next_cursor="done-" + key,
                pages_expected=2 if key == second_key and second_state == "partial" else 1,
                exhausted=not (key == second_key and second_state == "partial"),
                errors=("isolated second page missing",) if key == second_key and second_state == "partial" else ())

    sources = (Source(first_key, 0), Source(second_key, 1))
    handler = pipeline.production_scan_handler

    def handler_with_sources(context, **kwargs):
        return handler(context, **kwargs, source_adapter_factory=lambda _context, _bound: sources)

    monkeypatch.setattr(pipeline, "production_scan_handler", handler_with_sources)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name="two-source-worker", selected_event_count=3)
    assert flow.task_status == "completed"
    for key in keys:
        assert observed[key], key
        assert all(request.source_success_watermark == starts[key] for request in observed[key])
        assert all(request.previous_cursor == "cursor-" + key for request in observed[key])
    assert store.latest_source_watermark(source_key=first_key, db_path=flow.db_path)["cursorValue"] == "done-" + first_key
    expected_second_cursor = "done-" + second_key if second_state == "complete" else "cursor-" + second_key
    assert store.latest_source_watermark(source_key=second_key, db_path=flow.db_path)["cursorValue"] == expected_second_cursor

    scan = store.get_scan(scan_id=flow.scan_id, db_path=flow.db_path)
    assert scan["status"] == ("completed" if second_state == "complete" else "partial")
    refs = scan["coverage"]["inputDocumentRefs"]
    with actual_api(flow.db_path, **explicit_bindings(flow.db_path)) as client:
        envelope = client.get("/api/v1/k10/v2/reports/latest?window=evening").json()
        assert envelope["schemaVersion"] == 10
        report = envelope["report"]
        assert report["availableAt"] and report["eveningCards"]
        assert report["delivery"]["outcome"] == ("complete" if second_state == "complete" else "partial")
        actual_keys = set()
        for ref in refs:
            response = client.get(f"/api/v1/k10/documents/{ref['documentId']}", params={"revision": ref["revision"]})
            assert response.status_code == 200
            document = response.json()
            actual_keys.add(document["sourceKey"])
            assert document["contentKind"] == "original"
        assert actual_keys == ({first_key} if second_state == "failed" else set(keys))
    with sqlite3.connect(flow.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM k10_external_attempts WHERE state IN ('started','running','unknown')").fetchone()[0] == 0
