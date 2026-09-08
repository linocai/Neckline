from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import enqueue_scan
from neckline.k10.schema import initialize_schema
from neckline.k10.sources import SourceCoverage, SourceDocumentInput, SourceFetchResult
from neckline.k10.types import OpportunityPublicationInput
from neckline.k10.windows import SHANGHAI
from neckline.k10.worker import TaskContext, TaskResult, run_once


MORNING = datetime(2026, 9, 8, 9, tzinfo=SHANGHAI)
STARTED = datetime(2026, 9, 8, 9, 5, tzinfo=SHANGHAI)


def _config() -> dict:
    return json.loads((Path(__file__).parents[1] / "neckline/config/k10-v1.4.json").read_text())


def _bind_execution(path: Path, task_id: str) -> None:
    """Legacy scheduler regressions must use the same explicit B36 binding as production."""
    profile = json.loads((Path(__file__).parents[1] / "neckline/config/k10-execution-v1.json").read_text())
    revision = store.append_execution_config(
        config_id="fixture-execution", payload=profile, created_at=STARTED.isoformat(), db_path=path,
    )
    store.bind_task_execution(
        task_id=task_id, execution_config_id="fixture-execution", execution_config_revision=revision,
        binding_kind="scheduled", bound_at=STARTED.isoformat(), db_path=path,
    )


def _seed(path: Path, *, formal_target: bool) -> tuple[str, int]:
    initialize_schema(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES('SSE', ?, ?)", [
            ("20260904", 1), ("20260905", 0), ("20260906", 0), ("20260907", 1),
            ("20260908", 1), ("20260909", 1),
        ])
    config = _config()
    revision = store.append_run_config(config_id="cfg-v303", payload=config, created_at=STARTED.isoformat(), db_path=path)
    if not formal_target:
        return "cfg-v303", revision
    evidence = [{"documentId": "seed-doc", "revision": 1}]
    published_at = "2026-09-06T20:00:00+08:00"
    store.append_document_version(
        document_id="seed-doc", source_key="fixture", external_id="seed-doc", canonical_url="https://example.test/seed",
        content_sha256=sha256(b"seed").hexdigest(), published_at=published_at, published_precision="exact",
        fetched_at=published_at, original_text="正式首发资料", excerpt=None, fetch_version="fixture", metadata={},
        created_at=published_at, db_path=path,
    )
    store.append_event_revision(event_id="seed-event", stable_key="seed-event", headline="正式首发", event_kind="news",
        facts={}, source_refs=evidence, supersedes_revision=None, created_at=published_at, db_path=path)
    store.create_scan(scan_id="seed-evening", window_kind="evening", cutoff_at=published_at, config_id="cfg-v303",
        config_revision=revision, status="completed", coverage={}, created_at=published_at, completed_at=published_at, db_path=path)
    comparison = {
        "summary": "正式比较",
        "differences": {"role": "primary", "priorityReason": "直接受益", "gap": "无", "rankChangeConditions": "反证", "twoDayReason": "两日"},
        "evidenceRefs": evidence, "rank": 1,
        "classification": {"kind": "initial", "opportunityKey": "seed-key", "reason": "首发", "newFacts": "披露",
                           "changedJudgment": None, "twoDayReason": "两日", "relatedOpportunityId": None},
    }
    store.create_candidate(candidate_id="seed-candidate", scan_id="seed-evening", event_id="seed-event", event_revision=1,
        company_code="300001.SZ", comparison=comparison, evidence=evidence, created_at=published_at, db_path=path)
    store.publish_opportunities(batch_id="seed-batch", scan_id="seed-evening", publication_kind="evening", inputs=[
        OpportunityPublicationInput(candidate_id="seed-candidate", company_code="300001.SZ", event_id="seed-event", event_revision=1,
            opportunity_key="seed-key", catalyst_stage="initial", category="primary", comparison=comparison,
            evidence_refs=tuple(evidence), source_marker="evening")
    ], db_path=path, clock=lambda: datetime(2026, 9, 6, 20, tzinfo=SHANGHAI))
    return "cfg-v303", revision


class _EmptyAdapter:
    coverage = SourceCoverage("tushare-major-news", "fixture major news", "fixture", "single", "publishedAt", "publishedAt", True)

    def fetch_incremental(self, request):
        return SourceFetchResult(documents=(), next_cursor=None, success_watermark=request.window.cutoff_at,
            pages_fetched=1, pages_expected=1, exhausted=True)


class _UnknownTimeAdapter:
    coverage = _EmptyAdapter.coverage

    def fetch_incremental(self, request):
        return SourceFetchResult(documents=(SourceDocumentInput(
            external_id="unknown-time", canonical_url="https://example.test/unknown", original_text="300001.SZ 相关资料但发布时间待核。",
            excerpt=None, published_at=None, published_precision="unknown", fetched_at=STARTED, fetch_version="fixture", metadata={},
        ),), next_cursor=None, success_watermark=request.window.cutoff_at, pages_fetched=1, pages_expected=1, exhausted=True,
            unknown_publication_time_count=1)


class _UnknownCountAdapter:
    coverage = _EmptyAdapter.coverage

    def fetch_incremental(self, request):
        return SourceFetchResult(documents=(), next_cursor=None, success_watermark=request.window.cutoff_at,
            pages_fetched=1, pages_expected=1, exhausted=True, unknown_publication_time_count=1)


class _ExactAdapter:
    coverage = _EmptyAdapter.coverage

    def fetch_incremental(self, request):
        return SourceFetchResult(documents=(SourceDocumentInput(
            external_id="exact", canonical_url="https://example.test/exact", original_text="300001.SZ 晨间更新。", excerpt=None,
            published_at=datetime(2026, 9, 8, 8, 30, tzinfo=SHANGHAI), published_precision="exact", fetched_at=STARTED,
            fetch_version="fixture", metadata={},
        ),), next_cursor=None, success_watermark=request.window.cutoff_at, pages_fetched=1, pages_expected=1, exhausted=True)


class _RaisingProvider:
    def chat(self, *_args, **_kwargs):
        raise RuntimeError("fixture discovery failure")


def _run_morning_task(*, path: Path, config_id: str, revision: int, adapter, monkeypatch, provider=object(), now_at=STARTED):
    monkeypatch.setattr(pipeline, "_now", lambda: now_at)
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", lambda **_: SimpleNamespace(provider=provider, error=None))
    monkeypatch.setattr(pipeline, "TuShareMajorNewsAdapter", lambda **_: adapter)
    task_id = enqueue_scan(db_path=path, kind="morning", trading_day=date(2026, 9, 8), config_id=config_id,
        config_revision=revision, now=now_at)
    _bind_execution(path, task_id)
    task = run_once(db_path=path, worker_id="v303-fixture", lease_for=timedelta(minutes=5), clock=lambda: now_at,
        handlers={"morning_scan": lambda context: pipeline.production_scan_handler(
            context, tushare_token="fixture-token", parquet_dir=path.parent / "parquet", now=lambda: now_at,
        )}, task_id=task_id)
    assert task is not None
    return task_id, task


def test_cli_worker_handler_accepts_equivalent_cutoffs_and_canonicalizes_report(tmp_path, monkeypatch):
    path = tmp_path / "morning.sqlite"
    config_id, revision = _seed(path, formal_target=False)
    task_id, task = _run_morning_task(path=path, config_id=config_id, revision=revision, adapter=_EmptyAdapter(), monkeypatch=monkeypatch)

    assert task.status == "completed"
    report = store.list_morning_reports(db_path=path)[0]
    scan_id = store.task_execution_input(task_id=task_id, db_path=path)["checkpoint"]["scanId"]
    scan = store.get_scan(scan_id=scan_id, db_path=path)
    assert scan is not None
    assert report["cutoffAt"] == scan["cutoffAt"] == "2026-09-08T01:00:00+00:00"


def test_morning_report_rejects_a_different_cutoff_instant(tmp_path):
    path = tmp_path / "cutoff.sqlite"
    initialize_schema(path)
    scan_cutoff = "2026-09-08T01:00:00+00:00"
    store.create_scan(scan_id="morning", window_kind="morning", cutoff_at=scan_cutoff, config_id=None, config_revision=None,
        status="completed", coverage={}, created_at=scan_cutoff, completed_at=scan_cutoff, db_path=path)
    groups = {section: [] for section in ("major_contrary", "thesis_changed", "continuing_or_expiring", "new", "needs_review")}
    equivalent = store.append_morning_report(report_id="equivalent", scan_id="morning", cutoff_at="2026-09-08T01:00:00Z",
        generated_at=scan_cutoff, status="partial", coverage={}, groups=groups, created_at=scan_cutoff, db_path=path)
    assert equivalent["cutoffAt"] == scan_cutoff
    with pytest.raises(store.K10Conflict, match="同一截止时间"):
        store.append_morning_report(report_id="wrong", scan_id="morning", cutoff_at="2026-09-08T01:00:00.000001+00:00",
            generated_at=scan_cutoff, status="partial", coverage={}, groups=groups, created_at=scan_cutoff, db_path=path)


def test_unknown_time_source_marks_formal_morning_target_needs_review(tmp_path, monkeypatch):
    path = tmp_path / "unknown.sqlite"
    config_id, revision = _seed(path, formal_target=True)
    _, task = _run_morning_task(path=path, config_id=config_id, revision=revision, adapter=_UnknownTimeAdapter(), monkeypatch=monkeypatch)

    assert task.status == "completed"
    report = store.list_morning_reports(db_path=path)[0]
    assert report["status"] == "partial"
    assert report["coverage"]["sourceStatus"] == "partial"
    assert report["coverage"]["timeCoverage"] == "partial"
    assert report["coverage"]["uncertainTimeDocumentRefs"]
    assert "morning_time_coverage_partial" in report["coverage"]["gaps"]
    assert len(report["groups"]["needs_review"]) == 1
    assert "无变化" not in report["groups"]["needs_review"][0]["content"]["summary"]


def test_unreferenced_unknown_time_count_still_marks_morning_coverage_partial(tmp_path, monkeypatch):
    path = tmp_path / "unknown-count.sqlite"
    config_id, revision = _seed(path, formal_target=True)
    _, task = _run_morning_task(path=path, config_id=config_id, revision=revision, adapter=_UnknownCountAdapter(), monkeypatch=monkeypatch)

    assert task.status == "completed"
    report = store.list_morning_reports(db_path=path)[0]
    assert report["coverage"]["timeCoverage"] == "partial"
    assert report["coverage"]["uncertainTimeDocumentRefs"] == []
    assert len(report["groups"]["needs_review"]) == 1


def test_document_failure_still_appends_a_partial_morning_report(tmp_path, monkeypatch):
    path = tmp_path / "failure.sqlite"
    config_id, revision = _seed(path, formal_target=True)
    task_id, task = _run_morning_task(path=path, config_id=config_id, revision=revision, adapter=_ExactAdapter(), monkeypatch=monkeypatch,
        provider=_RaisingProvider())

    assert task.status == "completed"
    report = store.list_morning_reports(db_path=path)[0]
    assert report["status"] == "partial"
    assert report["coverage"]["discoveryState"] == "partial"
    assert "morning_discovery_partial" in report["coverage"]["gaps"]


@pytest.mark.parametrize("failure", ("provider", "token", "budget", "calendar"))
def test_worker_lease_loss_before_unavailable_branch_writes_no_report(tmp_path, monkeypatch, failure):
    path = tmp_path / f"lost-lease-{failure}.sqlite"
    config_id, revision = _seed(path, formal_target=False)
    task_id = f"lost-lease-{failure}"
    store.enqueue_task(task_id=task_id, kind="morning_scan", idempotency_key=task_id,
        input_version=f"{config_id}@{revision}", input_cutoff_at=MORNING.isoformat(),
        payload={"windowKind": "morning", "configId": config_id, "configRevision": revision},
        budget={"maxAttempts": 1, "maxSourceRequests": 0 if failure == "budget" else 128},
        created_at=STARTED.isoformat(), db_path=path)
    _bind_execution(path, task_id)
    state = {"branchHit": False}

    def resolve(**_kwargs):
        if failure in {"provider", "token"}:
            state["branchHit"] = True
            state["context"].lease_lost.set()
        return SimpleNamespace(
            provider=None if failure == "provider" else object(),
            error="fixture provider unavailable" if failure == "provider" else None,
        )

    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", resolve)

    def calendar(*_args, **_kwargs):
        if failure == "calendar":
            state["branchHit"] = True
            state["context"].lease_lost.set()
            return False
        return True

    monkeypatch.setattr(pipeline, "official_is_trading_day", calendar)

    def handler(context):
        state["context"] = context
        if failure == "budget":
            class LosingBudget(dict):
                def get(self, key, default=None):
                    if key == "maxSourceRequests":
                        state["branchHit"] = True
                        context.lease_lost.set()
                    return super().get(key, default)
            context = replace(context, budget=LosingBudget(context.budget))
        return pipeline.production_scan_handler(
            context, tushare_token=None if failure == "token" else "fixture-token", parquet_dir=path.parent / "parquet", now=lambda: STARTED,
        )

    with pytest.raises(store.K10Conflict, match="租约"):
        run_once(db_path=path, worker_id="lost-lease", lease_for=timedelta(minutes=5), clock=lambda: STARTED,
            handlers={"morning_scan": handler}, task_id=task_id)
    assert state["branchHit"] is True
    assert store.list_scans(window_kind="morning", db_path=path) == []
    assert store.list_morning_reports(db_path=path) == []


def test_worker_lease_loss_after_scan_returns_writes_no_morning_report(tmp_path, monkeypatch):
    path = tmp_path / "lost-lease-after-scan.sqlite"
    config_id, revision = _seed(path, formal_target=False)
    task_id = enqueue_scan(db_path=path, kind="morning", trading_day=date(2026, 9, 8), config_id=config_id,
        config_revision=revision, now=STARTED)
    _bind_execution(path, task_id)
    scan_id = "scan-lost-after"
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", lambda **_: SimpleNamespace(provider=object(), error=None))

    def completed_then_lose_lease(**kwargs):
        cutoff = kwargs["cutoff_at"].astimezone(timezone.utc).isoformat()
        store.create_scan(scan_id=scan_id, window_kind="morning", cutoff_at=cutoff, config_id=config_id, config_revision=revision,
            status="completed", coverage={"sourceOutcomes": [], "inputDocumentRefs": [], "inputSnapshotFrozen": True},
            created_at=cutoff, completed_at=cutoff, db_path=path)
        kwargs["leaseguard"].__self__.lease_lost.set()
        return TaskResult("completed", "discovery_completed", {"scanId": scan_id, "ingestionState": "completed"})

    monkeypatch.setattr(pipeline, "execute_scan", completed_then_lose_lease)
    with pytest.raises(store.K10Conflict, match="租约"):
        run_once(db_path=path, worker_id="lost-after-scan", lease_for=timedelta(minutes=5), clock=lambda: STARTED,
            handlers={"morning_scan": lambda context: pipeline.production_scan_handler(
                context, tushare_token="fixture-token", parquet_dir=path.parent / "parquet", now=lambda: STARTED,
            )}, task_id=task_id)
    assert store.get_scan(scan_id=scan_id, db_path=path) is not None
    assert store.list_morning_reports(db_path=path) == []


def test_worker_recovers_running_unavailable_scan_before_appending_report(tmp_path, monkeypatch):
    path = tmp_path / "recover-running-unavailable.sqlite"
    config_id, revision = _seed(path, formal_target=False)
    task_id = enqueue_scan(db_path=path, kind="morning", trading_day=date(2026, 9, 8), config_id=config_id,
        config_revision=revision, now=STARTED)
    _bind_execution(path, task_id)
    scan_id = pipeline._scan_id(kind="morning", cutoff_at=MORNING, identity=task_id)
    original_create = pipeline.store.create_scan
    first_context = {}

    def create_then_lose_lease(**kwargs):
        original_create(**kwargs)
        first_context["value"].lease_lost.set()

    monkeypatch.setattr(pipeline.store, "create_scan", create_then_lose_lease)
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", lambda **_: SimpleNamespace(provider=None, error="fixture provider unavailable"))

    def first_handler(context):
        first_context["value"] = context
        return pipeline.production_scan_handler(context, tushare_token="fixture-token", parquet_dir=path.parent / "parquet", now=lambda: STARTED)

    with pytest.raises(store.K10Conflict, match="租约"):
        run_once(db_path=path, worker_id="first-owner", lease_for=timedelta(minutes=5), clock=lambda: STARTED,
            handlers={"morning_scan": first_handler}, task_id=task_id)
    stuck = store.get_scan(scan_id=scan_id, db_path=path)
    assert stuck is not None and stuck["status"] == "running"
    assert store.list_morning_reports(db_path=path) == []

    monkeypatch.setattr(pipeline.store, "create_scan", original_create)
    recovered_at = STARTED + timedelta(minutes=6)
    recovered = run_once(db_path=path, worker_id="recovery-owner", lease_for=timedelta(minutes=5), clock=lambda: recovered_at,
        handlers={"morning_scan": lambda context: pipeline.production_scan_handler(
            context, tushare_token="fixture-token", parquet_dir=path.parent / "parquet", now=lambda: recovered_at,
        )}, task_id=task_id)
    assert recovered is not None and recovered.status == "not_configured"
    finalized = store.get_scan(scan_id=scan_id, db_path=path)
    assert finalized is not None and finalized["status"] == "not_configured"
    assert len(store.list_morning_reports(db_path=path)) == 1


def test_store_rejects_a_morning_report_on_a_running_scan(tmp_path):
    path = tmp_path / "running-scan.sqlite"
    initialize_schema(path)
    cutoff = "2026-09-08T01:00:00+00:00"
    store.create_scan(scan_id="running", window_kind="morning", cutoff_at=cutoff, config_id=None, config_revision=None,
        status="running", coverage={}, created_at=cutoff, completed_at=None, db_path=path)
    groups = {section: [] for section in ("major_contrary", "thesis_changed", "continuing_or_expiring", "new", "needs_review")}
    with pytest.raises(store.K10Conflict, match="已终态"):
        store.append_morning_report(report_id="blocked", scan_id="running", cutoff_at=cutoff, generated_at=cutoff,
            status="partial", coverage={}, groups=groups, created_at=cutoff, db_path=path)
