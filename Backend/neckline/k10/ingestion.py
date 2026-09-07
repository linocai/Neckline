"""K10 来源摄取编排。

真实来源尚未获准接入，因此本模块只接收注入的适配器和写入器。它不会读取环境变量、
不会联网，也不会因为来源空缺或局部失败而声称“全市场无变化”。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .sources import (
    SourceAdapter,
    SourceCoverage,
    SourceDocumentInput,
    SourceFetchRequest,
    SourceFetchResult,
    validate_source_adapters,
)
from .types import DocumentVersion
from .windows import ScanWindow


@dataclass(frozen=True)
class SourceIngestionOutcome:
    source_key: str
    state: str
    stored_documents: int
    duplicate_documents: int
    late_documents: int
    uncertain_publication_time_documents: int
    coverage: dict[str, object]


@dataclass(frozen=True)
class IngestionRun:
    state: str
    scan_id: str | None
    window: ScanWindow
    missing_configuration: tuple[str, ...]
    outcomes: tuple[SourceIngestionOutcome, ...]

    @property
    def complete(self) -> bool:
        return self.state == "completed"


class IngestionWriter(Protocol):
    """追加式存储边界；具体 SQLite store 由核心层实现。

    适配器实现时，文档同一 ``source_key/external_id/content`` 重跑必须返回既有版本，
    不得创建第二条版本；新内容才可追加新 revision。
    """

    def append_document_version(
        self, *, source_key: str, document: SourceDocumentInput
    ) -> DocumentVersion:
        ...

    def record_source_outcome(
        self,
        *,
        scan_id: str | None,
        coverage: SourceCoverage,
        result: SourceFetchResult | None,
        state: str,
        error: str | None,
    ) -> None:
        ...

    def append_source_watermark(
        self,
        *,
        source_key: str,
        success_watermark: datetime,
        cursor: str | None,
        scan_id: str | None,
        recorded_at: datetime,
    ) -> None:
        ...


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("K10 时间必须带时区")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


class SqliteIngestionWriter:
    """把来源摄取协议接到 K10 追加式 SQLite store 的薄适配器。

    每个文档及水位 ID 都由稳定来源 identity 派生；重跑相同扫描只命中既有版本。
    逐源结果汇总在 ``ingest_to_sqlite`` 的 scan coverage 内统一冻结。
    """

    def __init__(self, *, db_path: Path) -> None:
        self._db_path = db_path

    def append_document_version(
        self, *, source_key: str, document: SourceDocumentInput
    ) -> DocumentVersion:
        from .store import append_document_version

        document_id = _stable_id("doc", source_key, document.external_id)
        content_payload = {
            "url": document.canonical_url,
            "originalText": document.original_text,
            "excerpt": document.excerpt,
            "publishedAt": _utc_text(document.published_at) if document.published_at else None,
            "publishedPrecision": document.published_precision,
            "metadata": document.metadata,
            "fetchVersion": document.fetch_version,
        }
        content_sha256 = sha256(
            json.dumps(content_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return append_document_version(
            document_id=document_id,
            source_key=source_key,
            external_id=document.external_id,
            canonical_url=document.canonical_url,
            content_sha256=content_sha256,
            published_at=_utc_text(document.published_at) if document.published_at else None,
            published_precision=document.published_precision,
            fetched_at=_utc_text(document.fetched_at),
            original_text=document.original_text,
            excerpt=document.excerpt,
            fetch_version=document.fetch_version,
            metadata=document.metadata,
            created_at=_utc_text(document.fetched_at),
            db_path=self._db_path,
        )

    def record_source_outcome(
        self,
        *,
        scan_id: str | None,
        coverage: SourceCoverage,
        result: SourceFetchResult | None,
        state: str,
        error: str | None,
    ) -> None:
        # The schema deliberately stores the completed multi-source picture on k10_scans.
        # `ingest_to_sqlite` immediately freezes that picture after all adapters return.
        return None

    def append_source_watermark(
        self,
        *,
        source_key: str,
        success_watermark: datetime,
        cursor: str | None,
        scan_id: str | None,
        recorded_at: datetime,
    ) -> None:
        from .store import append_source_watermark

        if scan_id is None:
            raise ValueError("持久 K10 来源水位必须绑定 scan_id")
        success_text = _utc_text(success_watermark)
        watermark_id = _stable_id("watermark", scan_id, source_key, success_text, cursor or "")
        append_source_watermark(
            watermark_id=watermark_id,
            source_key=source_key,
            cursor_value=cursor,
            success_cutoff_at=success_text,
            fetched_at=_utc_text(recorded_at),
            scan_id=scan_id,
            created_at=_utc_text(recorded_at),
            db_path=self._db_path,
        )


def ingest_to_sqlite(
    *,
    db_path: Path,
    scan_id: str,
    window: ScanWindow,
    adapters: Sequence[SourceAdapter],
    source_watermarks: dict[str, datetime | None],
    source_cursors: dict[str, str | None],
    config_id: str | None,
    config_revision: int | None,
    created_at: datetime,
    completed_at: datetime,
    finalize: bool = True,
) -> IngestionRun:
    """执行一轮摄取；编排器可延后冻结 scan 直到下游发现已落库。"""
    from .store import create_scan

    created_text = _utc_text(created_at)
    create_scan(
        scan_id=scan_id,
        window_kind=window.kind,
        cutoff_at=_utc_text(window.cutoff_at),
        config_id=config_id,
        config_revision=config_revision,
        status="running",
        coverage={"sourceOutcomes": [], "state": "running",
                  "window": {"kind": window.kind, "startAt": _utc_text(window.start_at) if window.start_at else None,
                             "cutoffAt": _utc_text(window.cutoff_at), "startInclusive": window.start_inclusive,
                             "cutoffInclusive": window.cutoff_inclusive},
                  "sourceInputs": {key: {"watermark": _utc_text(value) if value else None, "cursor": source_cursors.get(key)}
                                   for key, value in source_watermarks.items()}},
        created_at=created_text,
        completed_at=None,
        db_path=db_path,
    )
    run = IngestionOrchestrator(
        adapters=adapters, writer=SqliteIngestionWriter(db_path=db_path)
    ).ingest(
        window=window,
        source_watermarks=source_watermarks,
        source_cursors=source_cursors,
        scan_id=scan_id,
    )
    if finalize:
        finalize_ingestion_scan(run=run, scan_id=scan_id, completed_at=completed_at, db_path=db_path)
    return run


def ingestion_coverage(run: IngestionRun) -> dict[str, object]:
    """Build the immutable source coverage record used when the parent scan is finalized."""
    return {
        "sourceOutcomes": [outcome.coverage for outcome in run.outcomes],
        "state": run.state,
        "missingConfiguration": list(run.missing_configuration),
        "window": {
            "kind": run.window.kind,
            "startAt": _utc_text(run.window.start_at) if run.window.start_at else None,
            "cutoffAt": _utc_text(run.window.cutoff_at),
            "startInclusive": run.window.start_inclusive,
            "cutoffInclusive": run.window.cutoff_inclusive,
        },
    }


def finalize_ingestion_scan(*, run: IngestionRun, scan_id: str, completed_at: datetime, db_path: Path,
                            status: str | None = None, pipeline_state: str | None = None,
                            coverage_extra: Mapping[str, object] | None = None) -> None:
    """Freeze a still-running scan after all append-only downstream artifacts are persisted."""
    from .store import finalize_scan

    coverage = ingestion_coverage(run)
    if pipeline_state is not None:
        coverage["pipelineState"] = pipeline_state
    if coverage_extra:
        coverage.update(coverage_extra)
    finalize_scan(scan_id=scan_id, status=status or run.state, coverage=coverage,
                  completed_at=_utc_text(completed_at), db_path=db_path)


def _document_fingerprint(source_key: str, document: SourceDocumentInput) -> str:
    """仅消除同批完全相同的转载结果，绝不按利好/栏目删掉疑难资料。"""
    payload = "\x1f".join(
        (
            source_key,
            document.external_id,
            document.original_text or "",
            document.excerpt or "",
            document.canonical_url or "",
            document.fetch_version,
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _outcome_state(result: SourceFetchResult) -> str:
    return "completed" if result.complete else "partial"


class IngestionOrchestrator:
    """按来源独立执行，不以某个来源的失败清空其他来源成果或推进其水位。"""

    def __init__(self, *, adapters: Sequence[SourceAdapter], writer: IngestionWriter) -> None:
        self._adapters = tuple(adapters)
        self._writer = writer

    def ingest(
        self,
        *,
        window: ScanWindow,
        source_watermarks: dict[str, datetime | None],
        source_cursors: dict[str, str | None],
        scan_id: str | None = None,
    ) -> IngestionRun:
        missing = validate_source_adapters(self._adapters)
        if missing:
            return IngestionRun(
                state="not_configured",
                scan_id=scan_id,
                window=window,
                missing_configuration=missing,
                outcomes=(),
            )

        outcomes: list[SourceIngestionOutcome] = []
        completed_count = 0
        failed_count = 0
        for adapter in self._adapters:
            coverage = adapter.coverage
            request = SourceFetchRequest(
                window=window,
                previous_cursor=source_cursors.get(coverage.source_key),
                source_success_watermark=source_watermarks.get(coverage.source_key),
            )
            try:
                result = adapter.fetch_incremental(request)
            except Exception as exc:  # adapter must be isolated from other approved sources
                error = f"{type(exc).__name__}: {exc}"
                self._writer.record_source_outcome(
                    scan_id=scan_id, coverage=coverage, result=None, state="failed", error=error
                )
                outcomes.append(
                    SourceIngestionOutcome(
                        source_key=coverage.source_key,
                        state="failed",
                        stored_documents=0,
                        duplicate_documents=0,
                        late_documents=0,
                        uncertain_publication_time_documents=0,
                        coverage={**_failed_coverage(coverage), "errors": [error]},
                    )
                )
                failed_count += 1
                continue

            stored, duplicates, late, uncertain = self._append_documents(coverage, result, window)
            state = _outcome_state(result)
            self._writer.record_source_outcome(
                scan_id=scan_id, coverage=coverage, result=result, state=state, error=None
            )
            if result.can_advance_watermark:
                self._writer.append_source_watermark(
                    source_key=coverage.source_key,
                    success_watermark=result.success_watermark,  # guarded above
                    cursor=result.next_cursor,
                    scan_id=scan_id,
                    recorded_at=max((d.fetched_at for d in result.documents), default=result.success_watermark),
                )
            outcomes.append(
                SourceIngestionOutcome(
                    source_key=coverage.source_key,
                    state=state,
                    stored_documents=stored,
                    duplicate_documents=duplicates,
                    late_documents=late,
                    uncertain_publication_time_documents=uncertain,
                    coverage={
                        **result.coverage_record(coverage),
                        "lateDocumentCount": late,
                        "unknownPublicationTimeCount": uncertain,
                    },
                )
            )
            if state == "completed":
                completed_count += 1

        if failed_count == len(self._adapters):
            state = "failed"
        elif failed_count or completed_count != len(self._adapters):
            state = "partial"
        else:
            state = "completed"
        return IngestionRun(
            state=state,
            scan_id=scan_id,
            window=window,
            missing_configuration=(),
            outcomes=tuple(outcomes),
        )

    def _append_documents(
        self, coverage: SourceCoverage, result: SourceFetchResult, window: ScanWindow
    ) -> tuple[int, int, int, int]:
        seen: set[str] = set()
        stored = duplicates = late = uncertain = 0
        for document in result.documents:
            fingerprint = _document_fingerprint(coverage.source_key, document)
            if fingerprint in seen:
                duplicates += 1
                continue
            seen.add(fingerprint)
            self._writer.append_document_version(source_key=coverage.source_key, document=document)
            stored += 1
            if document.published_precision != "exact":
                uncertain += 1
            elif (
                document.published_at is not None
                and document.published_at <= window.cutoff_at
                and document.fetched_at > window.cutoff_at
            ):
                # A version published before cutoff but obtained later is never discarded.
                late += 1
        return stored, duplicates, max(late, result.late_document_count), max(
            uncertain, result.unknown_publication_time_count
        )


def _failed_coverage(coverage: SourceCoverage) -> dict[str, object]:
    return {
        "sourceKey": coverage.source_key,
        "scope": coverage.scope,
        "authorization": coverage.authorization,
        "isMarketWide": coverage.is_market_wide,
        "pagination": coverage.pagination,
        "watermarkField": coverage.watermark_field,
        "publicationTimeField": coverage.publication_time_field,
        "limitations": list(coverage.limitations),
        "complete": False,
    }


__all__ = [
    "IngestionOrchestrator", "IngestionRun", "IngestionWriter", "SourceIngestionOutcome",
    "SqliteIngestionWriter", "finalize_ingestion_scan", "ingestion_coverage", "ingest_to_sqlite",
]
