"""V3.0.3 source-time regressions, all isolated from live providers and databases."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from neckline.k10 import store
from neckline.k10.cli import enqueue_scan
from neckline.k10.discovery import DiscoveryDocument
from neckline.k10.ingestion import ingest_to_sqlite
from neckline.k10.pipeline import _docs_for_window, execute_scan as _execute_scan
from neckline.k10.sources import SourceCoverage, SourceDocumentInput, SourceFetchRequest, SourceFetchResult
from neckline.k10.tushare_news import MAJOR_NEWS_FIELDS, TuShareMajorNewsAdapter
from neckline.k10.windows import SHANGHAI, morning_window
from neckline.k10.worker import run_once
from tests.test_k10_pipeline import (
    _FixtureVerificationGateway,
    _Metadata,
    _VerifiedModel,
    _configuration,
    initialize_schema,
)
from tests.k10_v306_fixture import append_approved_execution_profile


def execute_scan(**kwargs):
    # These historical scenarios freeze visibility separately from acquisition;
    # today's wall clock must not replace their publication time.
    kwargs.setdefault('publication_clock', lambda: kwargs['completed_at'])
    return _execute_scan(**kwargs)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=SHANGHAI)


def _payload(items):
    return {"code": 0, "data": {"fields": MAJOR_NEWS_FIELDS.split(","), "items": items}}


def _configuration_for(source_key: str) -> dict:
    configuration = _configuration()
    configuration["sourceAdapters"] = [{"key": source_key, "lateArrivalReplaySeconds": 86400}]
    return configuration


@pytest.mark.parametrize("raw_time,precision", [("2026-09-08", "date"), ("不提供时间", "unknown")])
def test_v303_imprecise_publication_time_is_saved_but_not_a_precise_discovery_input(tmp_path, raw_time, precision):
    path = tmp_path / f"{precision}.sqlite"
    initialize_schema(path)
    cutoff = _at(8, 9)
    window = morning_window(previous_trading_day=date(2026, 9, 7), observation_day=date(2026, 9, 8))
    adapter = TuShareMajorNewsAdapter(
        token="offline", clock=lambda: cutoff,
        request_callable=lambda _request: _payload([[raw_time, "fixture", "时间待核", "原始资料没有精确发布时间"]]),
    )

    run = ingest_to_sqlite(
        db_path=path, scan_id=f"scan-{precision}", window=window, adapters=(adapter,),
        source_watermarks={adapter.coverage.source_key: window.start_at}, source_cursors={},
        config_id=None, config_revision=None, created_at=cutoff, completed_at=cutoff,
    )

    coverage = store.get_scan(scan_id=f"scan-{precision}", db_path=path)["coverage"]["sourceOutcomes"][0]
    assert run.state == "completed"  # transport coverage can finish and advance independently
    assert coverage["complete"] is True
    assert coverage["timeCoverage"] == "partial"
    assert len(coverage["uncertainTimeDocumentRefs"]) == 1
    assert store.latest_source_watermark(source_key=adapter.coverage.source_key, db_path=path) is not None
    rows = store.load_document_versions(refs=coverage["uncertainTimeDocumentRefs"], db_path=path)
    assert rows[0]["publishedPrecision"] == precision
    assert _docs_for_window(window=window, db_path=path, completed_at=cutoff,
                            source_keys=(adapter.coverage.source_key,),
                            current_refs=coverage["newDocumentRefs"]) == ()


def test_v303_bounded_replay_catches_late_arrival_once_without_backdating_or_reprocessing(tmp_path):
    path = tmp_path / "late-arrival.sqlite"
    initialize_schema(path)
    now = [_at(7, 21, 1)]
    available_after = _at(7, 21, 10)
    old_publication = _at(7, 20, 59)
    boundary_publication = _at(7, 21)
    requests: list[tuple[datetime, datetime]] = []

    def source(payload):
        params = payload["params"]
        start = datetime.strptime(params["start_date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI)
        end = datetime.strptime(params["end_date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI)
        requests.append((start, end))
        if now[0] < available_after:
            return _payload([])
        records = []
        for published, title in ((old_publication, "晚到资料"), (boundary_publication, "回放重复资料")):
            if start <= published <= end:
                records.append([published.strftime("%Y-%m-%d %H:%M:%S"), "fixture", title, "公司已公开披露重要订单"])
        return _payload(records)

    class RecordingModel(_VerifiedModel):
        def __init__(self):
            super().__init__()
            self.understand_calls = 0

        def understand(self, *, document):
            self.understand_calls += 1
            return super().understand(document=document)

    adapter = TuShareMajorNewsAdapter(token="offline", request_callable=source, clock=lambda: now[0])
    model = RecordingModel()
    configuration = _configuration_for(adapter.coverage.source_key)

    first = execute_scan(
        kind="evening", cutoff_at=_at(7, 21), configuration=configuration, db_path=path,
        adapter=adapter, model=model, metadata=_Metadata(), created_at=now[0], completed_at=now[0],
        bootstrap_cutoff=_at(4, 21).isoformat(), scan_identity="late-first",
        publication_clock=lambda: now[0], verification_gateway=_FixtureVerificationGateway(),
    )
    assert first.status == "completed" and model.understand_calls == 0

    now[0] = _at(8, 8, 59)
    second = execute_scan(
        kind="morning", cutoff_at=_at(8, 9), configuration=configuration, db_path=path,
        adapter=adapter, model=model, metadata=_Metadata(), created_at=now[0], completed_at=now[0],
        scan_identity="late-morning", publication_clock=lambda: now[0],
        verification_gateway=_FixtureVerificationGateway(),
    )
    assert second.status == "completed" and model.understand_calls == 2
    opportunities = store.list_opportunities(db_path=path)
    assert len(opportunities) == 1
    assert opportunities[0]["d1TradeDate"] == "2026-09-08"  # actual morning availability, never 9/7 backfill
    morning_coverage = store.get_scan(scan_id=second.checkpoint["scanId"], db_path=path)["coverage"]
    assert morning_coverage["sourceReplay"]["nominalStartAt"] == "2026-09-07T21:00:00+08:00"
    assert morning_coverage["sourceReplay"]["effectiveStartAt"] == "2026-09-07T09:00:00+08:00"

    now[0] = _at(8, 21, 1)
    third = execute_scan(
        kind="evening", cutoff_at=_at(8, 21), configuration=configuration, db_path=path,
        adapter=adapter, model=model, metadata=_Metadata(), created_at=now[0], completed_at=now[0],
        scan_identity="late-third", publication_clock=lambda: now[0],
        verification_gateway=_FixtureVerificationGateway(),
    )
    assert third.status == "completed"
    assert model.understand_calls == 2  # the 21:00 boundary replayed, but its stored version did not re-enter discovery
    assert any(start == boundary_publication for start, _end in requests)


def test_v303_each_successful_response_records_its_own_actual_fetch_time():
    clock = [_at(7, 21)]

    def source(_request):
        clock[0] = _at(7, 21, 5)
        return _payload([["2026-09-07 21:00:00", "fixture", "慢响应", "五分钟后才取得的资料"]])

    adapter = TuShareMajorNewsAdapter(token="offline", request_callable=source, clock=lambda: clock[0])
    window = morning_window(previous_trading_day=date(2026, 9, 7), observation_day=date(2026, 9, 8))
    result = adapter.fetch_incremental(SourceFetchRequest(window=window, previous_cursor=None,
                                                          source_success_watermark=window.start_at))
    assert result.documents[0].fetched_at == _at(7, 21, 5)


def test_v303_cli_worker_preserves_frozen_input_after_an_isolated_model_failure(tmp_path):
    """One failed document leaves a partial frozen scan; it never re-collects its source."""
    path = tmp_path / "retry-worker.sqlite"
    initialize_schema(path)
    store.set_run_control(state="open", reason_code="fixture_v303_sources", changed_at=_at(7, 20, 55).isoformat(),
                          changed_by="test", db_path=path)
    execution_id, execution_revision = append_approved_execution_profile(
        db_path=path, created_at=_at(7, 20, 55).isoformat(), config_id="v303-source-execution",
    )
    configuration = _configuration_for("fixture-source")
    revision = store.append_run_config(config_id="fixture", payload=configuration,
                                       created_at="2026-09-07T20:50:00+08:00", db_path=path)
    task_id = enqueue_scan(
        db_path=path, kind="evening", trading_day=date(2026, 9, 7), config_id="fixture",
        config_revision=revision, now=_at(7, 20, 55), bootstrap_cutoff=_at(4, 21).isoformat(),
        execution_config_id=execution_id, execution_config_revision=execution_revision,
    )

    class Adapter:
        coverage = SourceCoverage("fixture-source", "market-wide", "fixture", "bounded", "publishedAt", "publishedAt", True)

        def __init__(self, *, fail: bool):
            self.fail = fail
            self.calls = 0

        def fetch_incremental(self, request):
            self.calls += 1
            if self.fail:
                raise RuntimeError("source-down")
            published = request.window.start_at + timedelta(minutes=1)
            return SourceFetchResult(
                documents=(SourceDocumentInput("doc-1", None, "公司公告", None, published, "exact",
                                               published + timedelta(minutes=1), "fixture-v1", {}),),
                next_cursor=None, success_watermark=request.window.cutoff_at, pages_fetched=1,
                pages_expected=1, exhausted=True,
            )

    class FailingModel:
        usage_records = []

        def __init__(self):
            self.documents: list[DiscoveryDocument] = []

        def set_scan_cutoff(self, _cutoff):
            return None

        def understand(self, *, document: DiscoveryDocument):
            self.documents.append(document)
            raise RuntimeError("model-timeout-after-source-frozen")

    adapter = Adapter(fail=False)
    first_model = FailingModel()

    def handler(context):
        frozen = store.read_run_config(config_id=context.task.payload["configId"],
                                       revision=context.task.payload["configRevision"], db_path=context.db_path)
        return execute_scan(
            kind="evening", cutoff_at=datetime.fromisoformat(context.input_cutoff_at), configuration=frozen["payload"],
            db_path=context.db_path, adapter=adapter, model=first_model, metadata=_Metadata(),
            created_at=_at(7, 21, 5), completed_at=_at(7, 21, 5),
            bootstrap_cutoff=context.task.payload.get("sourceBootstrapCutoff"), scan_identity=context.task.task_id,
            verification_gateway=_FixtureVerificationGateway(),
        )

    first = run_once(db_path=path, worker_id="worker", lease_for=timedelta(minutes=5),
                     handlers={"evening_scan": handler}, clock=lambda: _at(7, 21, 10), task_id=task_id)
    assert first.status == "completed"
    scan = next(item for item in store.list_scans(window_kind="evening", db_path=path) if item["scanId"].startswith("scan_"))
    frozen_refs = scan["coverage"]["inputDocumentRefs"]
    assert scan["coverage"]["inputSnapshotFrozen"] is True and frozen_refs
    assert adapter.calls == 1 and len(first_model.documents) == 1
    partial = store.get_scan(scan_id=scan["scanId"], db_path=path)
    assert partial is not None and partial["status"] == "partial"
    assert partial["coverage"]["inputDocumentRefs"] == frozen_refs
    # A partial scan can still publish completed candidates; here it records an explicit
    # empty partial batch instead of silently claiming that no source item existed.
    assert len(store.list_publication_batches(db_path=path)) == 1
