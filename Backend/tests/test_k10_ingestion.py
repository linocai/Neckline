from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256

from neckline.k10.ingestion import IngestionOrchestrator, ingest_to_sqlite
from neckline.k10.schema import initialize_schema, read_connection
from neckline.k10.sources import (
    SourceCoverage,
    SourceDocumentInput,
    SourceFetchResult,
)
from neckline.k10.types import DocumentVersion
from neckline.k10.windows import SHANGHAI, morning_window


def _moment(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 7, hour, minute, tzinfo=SHANGHAI)


def _document(
    external_id: str = "doc-1", *, precision: str = "exact", original_text: str | None = "完整原文",
    excerpt: str | None = None, published_at: datetime | None = None, fetched_at: datetime | None = None,
) -> SourceDocumentInput:
    return SourceDocumentInput(
        external_id=external_id,
        canonical_url=f"https://example.invalid/{external_id}",
        original_text=original_text,
        excerpt=excerpt,
        published_at=published_at or _moment(8, 30),
        published_precision=precision,
        fetched_at=fetched_at or _moment(8, 40),
        fetch_version="fixture-v1",
        metadata={"fixture": True},
    )


@dataclass
class _Adapter:
    coverage: SourceCoverage
    result: SourceFetchResult | None = None
    error: Exception | None = None

    def fetch_incremental(self, request):
        self.request = request
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class _Writer:
    def __init__(self):
        self.versions: dict[tuple[str, str, str], DocumentVersion] = {}
        self.outcomes = []
        self.watermarks = []

    def append_document_version(self, *, source_key, document):
        digest = sha256(((document.original_text or "") + (document.excerpt or "")).encode()).hexdigest()
        key = (source_key, document.external_id, digest)
        if key not in self.versions:
            self.versions[key] = DocumentVersion(f"{source_key}:{document.external_id}", 1, digest)
        return self.versions[key]

    def record_source_outcome(self, **kwargs):
        self.outcomes.append(kwargs)

    def append_source_watermark(self, **kwargs):
        self.watermarks.append(kwargs)


def _coverage(source_key: str, *, market_wide: bool = True) -> SourceCoverage:
    return SourceCoverage(
        source_key=source_key,
        scope="authorised public disclosures" if market_wide else "selected-company verification",
        authorization="pending fixture approval",
        pagination="cursor",
        watermark_field="published_at",
        publication_time_field="published_at",
        is_market_wide=market_wide,
        limitations=("fixture only",),
    )


def _complete(documents, *, watermark: datetime = _moment(9)) -> SourceFetchResult:
    return SourceFetchResult(
        documents=documents,
        next_cursor="cursor-2",
        success_watermark=watermark,
        pages_fetched=2,
        pages_expected=2,
        exhausted=True,
    )


def _window():
    return morning_window(previous_trading_day=date(2026, 9, 4), observation_day=date(2026, 9, 7))


def test_no_source_config_is_not_configured_not_an_empty_market():
    writer = _Writer()
    result = IngestionOrchestrator(adapters=(), writer=writer).ingest(
        window=_window(), source_watermarks={}, source_cursors={}, scan_id="scan-none"
    )
    assert result.state == "not_configured"
    assert result.missing_configuration == ("sourceAdapters",)
    assert writer.outcomes == []
    assert writer.versions == {}


def test_partial_source_keeps_received_docs_records_scope_and_does_not_advance_watermark():
    writer = _Writer()
    duplicate = _document("same")
    partial = SourceFetchResult(
        documents=(duplicate, duplicate, _document("date-only", precision="date", original_text=None, excerpt="只有摘要")),
        next_cursor="cursor-3",
        success_watermark=_moment(9),
        pages_fetched=2,
        pages_expected=3,
        exhausted=False,
        errors=("page 3 timeout",),
        unknown_publication_time_count=1,
    )
    result = IngestionOrchestrator(
        adapters=(_Adapter(_coverage("disclosure"), partial),), writer=writer
    ).ingest(window=_window(), source_watermarks={"disclosure": _moment(7)}, source_cursors={}, scan_id="scan-partial")

    assert result.state == "partial"
    outcome = result.outcomes[0]
    assert outcome.stored_documents == 2
    assert outcome.duplicate_documents == 1
    assert outcome.uncertain_publication_time_documents == 1
    assert outcome.coverage["complete"] is False
    assert outcome.coverage["errors"] == ["page 3 timeout"]
    assert writer.watermarks == []
    assert len(writer.versions) == 2


def test_source_failure_does_not_discard_other_source_or_claim_full_coverage():
    writer = _Writer()
    good = _Adapter(_coverage("approved-feed"), _complete((_document(),)))
    broken = _Adapter(_coverage("secondary-feed"), error=RuntimeError("permission denied"))
    result = IngestionOrchestrator(adapters=(good, broken), writer=writer).ingest(
        window=_window(), source_watermarks={}, source_cursors={}, scan_id="scan-mixed"
    )

    assert result.state == "partial"
    assert [outcome.state for outcome in result.outcomes] == ["completed", "failed"]
    assert len(writer.versions) == 1
    assert [watermark["source_key"] for watermark in writer.watermarks] == ["approved-feed"]
    failed = result.outcomes[1]
    assert failed.coverage["complete"] is False
    assert "permission denied" in failed.coverage["errors"][0]


def test_rerun_is_content_idempotent_and_late_document_is_retained_for_review():
    writer = _Writer()
    late = _document(published_at=_moment(8, 30), fetched_at=_moment(10, 0))
    adapter = _Adapter(_coverage("approved-feed"), _complete((late,)))
    orchestrator = IngestionOrchestrator(adapters=(adapter,), writer=writer)
    first = orchestrator.ingest(window=_window(), source_watermarks={}, source_cursors={}, scan_id="scan-first")
    second = orchestrator.ingest(window=_window(), source_watermarks={}, source_cursors={}, scan_id="scan-second")

    assert first.state == second.state == "completed"
    assert first.outcomes[0].late_documents == 1
    assert len(writer.versions) == 1  # writer's stable content identity prevents a new stored version
    assert len(writer.watermarks) == 2  # every independently successful scan records its own watermark


def test_sqlite_adapter_freezes_coverage_and_reuses_document_version_on_later_scan(tmp_path):
    from neckline.k10.store import get_scan, latest_source_watermark

    db_path = tmp_path / "k10-ingestion.db"
    initialize_schema(db_path)
    adapter = _Adapter(_coverage("approved-feed"), _complete((_document(),)))

    first = ingest_to_sqlite(
        db_path=db_path,
        scan_id="scan-sqlite-1",
        window=_window(),
        adapters=(adapter,),
        source_watermarks={},
        source_cursors={},
        config_id=None,
        config_revision=None,
        created_at=_moment(9),
        completed_at=_moment(9),
    )
    second = ingest_to_sqlite(
        db_path=db_path,
        scan_id="scan-sqlite-2",
        window=_window(),
        adapters=(adapter,),
        source_watermarks={"approved-feed": _moment(9)},
        source_cursors={"approved-feed": "cursor-2"},
        config_id=None,
        config_revision=None,
        created_at=_moment(10),
        completed_at=_moment(10),
    )

    assert first.state == second.state == "completed"
    stored_scan = get_scan(scan_id="scan-sqlite-1", db_path=db_path)
    assert stored_scan is not None
    assert stored_scan["status"] == "completed"
    assert stored_scan["coverage"]["sourceOutcomes"][0]["complete"] is True
    latest = latest_source_watermark(source_key="approved-feed", db_path=db_path)
    assert latest is not None and latest["scanId"] == "scan-sqlite-2"
    with read_connection(db_path) as conn:
        versions = conn.execute("SELECT COUNT(*) FROM k10_source_document_versions").fetchone()[0]
    assert versions == 1


def test_pipeline_can_delay_scan_finalization_without_losing_partial_documents(tmp_path):
    from neckline.k10.ingestion import finalize_ingestion_scan
    from neckline.k10.store import get_scan

    db_path = tmp_path / "deferred-finalize.sqlite"
    initialize_schema(db_path)
    partial = SourceFetchResult(documents=(_document(),), next_cursor=None, success_watermark=None,
                                pages_fetched=1, pages_expected=2, exhausted=False, errors=("bounded",))
    run = ingest_to_sqlite(db_path=db_path, scan_id="scan-deferred", window=_window(),
                           adapters=(_Adapter(_coverage("approved-feed"), partial),), source_watermarks={}, source_cursors={},
                           config_id=None, config_revision=None, created_at=_moment(9), completed_at=_moment(9), finalize=False)
    assert get_scan(scan_id="scan-deferred", db_path=db_path)["status"] == "running"
    with read_connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_source_document_versions").fetchone()[0] == 1
    finalize_ingestion_scan(run=run, scan_id="scan-deferred", completed_at=_moment(10), db_path=db_path)
    assert get_scan(scan_id="scan-deferred", db_path=db_path)["status"] == "partial"
