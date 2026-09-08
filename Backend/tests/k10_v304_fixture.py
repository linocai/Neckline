"""Build 35 producer/API/native acceptance; all state belongs to the caller's temp DB."""
from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pandas as pd

from neckline.data.tushare_client import TushareResult
from neckline.k10 import pipeline, store
from neckline.k10.ingestion import ingest_to_sqlite, finalize_ingestion_scan
from neckline.k10.market_observation import fetch_market_day_fact, record_market_day_fact
from neckline.k10.morning import MORNING_SECTIONS
from neckline.k10.morning_runtime import morning_review_handler
from neckline.k10.providers import ProviderResolution
from neckline.k10.sources import SourceCoverage, SourceDocumentInput, SourceFetchResult
from neckline.k10.types import OpportunityPublicationInput
from neckline.k10.windows import ScanWindow
from neckline.k10.worker import run_once

from .k10_v302_fixture import _comparison, _config, _Provider
from .k10_v303_fixture import build_fixture as build_previous_fixture
from .k10_v306_fixture import append_approved_execution_profile


def _publish(path: Path, *, marker: str, config_id: str, codes: tuple[str, ...], available: str) -> list[dict]:
    cutoff = available.replace("21:05", "21:00")
    refs = [{"documentId": "doc-fixture", "revision": 1}]
    event_id, scan_id = "event-v304-" + marker, "scan-v304-" + marker
    store.append_event_revision(event_id=event_id, stable_key=event_id,
        headline="合成验收：同事件并列公司" if len(codes) > 1 else "合成验收：评价配置缺口",
        event_kind="policy", facts={"summary": "固定的两日催化与公司比较"}, source_refs=refs,
        supersedes_revision=None, created_at=cutoff, db_path=path)
    store.create_scan(scan_id=scan_id, window_kind="evening", cutoff_at=cutoff, config_id=config_id,
        config_revision=1, status="completed", coverage={"status": "complete"},
        created_at=cutoff, completed_at=cutoff, db_path=path)
    inputs = []
    for index, code in enumerate(codes, start=1):
        candidate_id, key = f"candidate-v304-{marker}-{code}", f"{event_id}:{code}"
        role = "tied" if len(codes) > 1 else "primary"
        comparison = _comparison(code=code, opportunity_key=key, role=role, rank=1, refs=refs)
        comparison.update(rankNamespace="event", eventRank=1)
        store.create_candidate(candidate_id=candidate_id, scan_id=scan_id, event_id=event_id,
            event_revision=1, company_code=code, comparison=comparison, evidence=refs,
            created_at=cutoff, db_path=path)
        inputs.append(OpportunityPublicationInput(candidate_id=candidate_id, company_code=code,
            event_id=event_id, event_revision=1, opportunity_key=key, catalyst_stage="approval",
            category=role, comparison=comparison, evidence_refs=tuple(refs), source_marker="evening",
            related_opportunity_id=None, display_rank=index))
    store.publish_opportunities(batch_id="batch-v304-" + marker, scan_id=scan_id,
        publication_kind="evening", inputs=inputs, db_path=path, clock=lambda: datetime.fromisoformat(available))
    return store.list_opportunities(batch_id="batch-v304-" + marker, db_path=path)


def _market_days(path: Path, window: dict, *, missing_limit: bool) -> None:
    def result(rows):
        return TushareResult.success(pd.DataFrame(rows))
    for day in (window["d1TradeDate"], window["d2TradeDate"]):
        compact = day.replace("-", "")
        fact = fetch_market_day_fact(company_code=window["companyCode"], trade_date=day,
            obtained_at=f"{day}T16:00:00+08:00",
            daily_fetcher=lambda *_: result([{"ts_code": window["companyCode"], "trade_date": compact,
                "open": 10, "high": 12, "low": 10, "close": 12, "pre_close": 10}]),
            limit_fetcher=lambda *_: result([] if missing_limit else [{"ts_code": window["companyCode"],
                "trade_date": compact, "up_limit": 12}]),
            adj_factor_fetcher=lambda *_: result([{"ts_code": window["companyCode"], "trade_date": compact, "adj_factor": 1}]),
            suspend_fetcher=lambda *_: result([]), quote_fetcher=None)
        record_market_day_fact(fact=fact, db_path=path, created_at=fact["obtainedAt"])


def _source_coverage(path: Path) -> None:
    cutoff = datetime.fromisoformat("2026-09-07T21:00:00+08:00")
    window = ScanWindow(kind="evening", start_at=cutoff - timedelta(days=1), cutoff_at=cutoff,
                        start_inclusive=False, cutoff_inclusive=False)

    class Adapter:
        coverage = SourceCoverage(source_key="tushare-major-news", scope="合成验收：长篇通讯",
            authorization="fixture", pagination="single_page", watermark_field="publishedAt",
            publication_time_field="publishedAt", is_market_wide=False)

        def fetch_incremental(self, request):
            document = SourceDocumentInput(external_id="v304-uncertain", canonical_url=None,
                original_text="合成资料：来源没有给出精确公开时间，必须单列待核。", excerpt=None,
                published_at=None, published_precision="unknown", fetched_at=cutoff,
                fetch_version="fixture-v304", metadata={"title": "公开时间待核的合成资料"})
            return SourceFetchResult(documents=(document,), next_cursor=None, success_watermark=cutoff,
                pages_fetched=1, pages_expected=1, exhausted=True, unknown_publication_time_count=1)

    run = ingest_to_sqlite(db_path=path, scan_id="scan-v304-coverage", window=window, adapters=(Adapter(),),
        source_watermarks={"tushare-major-news": window.start_at}, source_cursors={},
        config_id="cfg-fixture", config_revision=1, created_at=cutoff, completed_at=cutoff, finalize=False)
    finalize_ingestion_scan(run=run, scan_id="scan-v304-coverage", completed_at=cutoff, db_path=path,
        coverage_extra={"sourceReplay": {"sourceKey": "tushare-major-news",
            "nominalStartAt": (cutoff - timedelta(hours=12)).isoformat(),
            "effectiveStartAt": window.start_at.isoformat(), "replayStartAt": window.start_at.isoformat(),
            "cutoffAt": cutoff.isoformat(), "replaySeconds": 86400, "requestState": run.state}})


def _morning(path: Path, targets: list[dict]) -> None:
    cutoff = "2026-09-07T09:00:00+08:00"
    now = datetime.fromisoformat(cutoff)
    scan_id = "scan-v304-morning"
    store.create_scan(scan_id=scan_id, window_kind="morning", cutoff_at=cutoff, config_id="cfg-fixture",
        config_revision=1, status="completed", coverage={"status": "complete"},
        created_at=cutoff, completed_at=cutoff, db_path=path)
    for document_id, title in (("doc-v304-morning", "晨间风险资料"), ("doc-v304-independent", "独立核验：核心理由失效")):
        store.append_document_version(document_id=document_id, source_key="fixture_verification",
            external_id=document_id, canonical_url=f"https://example.invalid/{document_id}",
            content_sha256=sha256(title.encode()).hexdigest(), published_at=cutoff, published_precision="exact",
            fetched_at=cutoff, original_text=title, excerpt=title, fetch_version="fixture-v304",
            metadata={"title": title}, created_at=cutoff, db_path=path)
    rows = {item["opportunityId"]: item for item in pipeline._morning_target_items(
        scan_id=scan_id, cutoff_at=now, db_path=path, morning_refs=[], review_matches=[])}
    target = rows[targets[0]["opportunityId"]]
    payload = {"candidateId": target["candidateId"], "observationId": None,
        "originalCutoffAt": targets[0]["availableAt"],
        "morningEvidenceRefs": [{"documentId": "doc-v304-morning", "revision": 1}],
        "independentVerificationRefs": [{"documentId": "doc-v304-independent", "revision": 1}],
        "companyWindowId": target["companyWindowId"], "displayRank": target["displayRank"],
        "selectionState": target["selectionState"], "lifecycle": target["lifecycle"],
        "isNew": False, "sourceStatus": "complete", "configId": "cfg-fixture", "configRevision": 1}
    task_id = "morning-v304-withdraw"
    store.enqueue_task(task_id=task_id, kind="morning_review", idempotency_key=task_id,
        input_version="fixture-v304", input_cutoff_at=cutoff, payload=payload,
        budget=_config()["taskPolicies"]["morning"], created_at=cutoff, db_path=path)
    execution_id, execution_revision = append_approved_execution_profile(db_path=path, created_at=cutoff)
    store.bind_task_execution(task_id=task_id, execution_config_id=execution_id,
        execution_config_revision=execution_revision, binding_kind="scheduled", bound_at=cutoff, db_path=path)
    store.set_run_control(state="open", reason_code="offline_fixture", changed_at=cutoff, changed_by="test", db_path=path)
    raw = {"material": True, "reasonStatus": "invalidated", "observationStatus": "needs_review",
        "summary": "合成验收：独立核验确认核心理由失效，撤回推荐。",
        "materialContraryEvidence": [{"documentId": "doc-v304-independent", "revision": 1, "claim": "核心理由失效"}]}
    provider = _Provider([json.dumps(raw, ensure_ascii=False)])
    with patch("neckline.k10.morning_runtime.resolve_deepseek_v4_pro",
               lambda **_: ProviderResolution("configured", provider, "fixture", None)):
        result = run_once(db_path=path, worker_id="v304-qa", lease_for=timedelta(minutes=5),
            handlers={"morning_review": lambda context: morning_review_handler(context, clock=lambda: cutoff)},
            clock=lambda: now, task_id=task_id)
    if result is None or result.status != "completed":
        raise RuntimeError("Build 35 fixture withdrawal producer failed")
    with sqlite3.connect(path) as connection:
        checkpoint = json.loads(connection.execute("SELECT checkpoint_json FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()[0])
    groups = {section: [] for section in MORNING_SECTIONS}
    groups[checkpoint["reportSection"]].append(checkpoint["reportItem"])
    fallback = pipeline._morning_fallback_item(scan_id=scan_id, target=rows[targets[1]["opportunityId"]],
        cutoff_at=cutoff, source_status="complete", summary="完整覆盖下无实质变化，继续观察。",
        task_status="completed", reason_status="current", material=False, is_new=False)
    if fallback is None:
        raise RuntimeError("Build 35 fixture fallback producer failed")
    groups[fallback["content"]["section"]].append(fallback)
    store.append_morning_report(report_id="report-v304", scan_id=scan_id, cutoff_at=cutoff,
        generated_at=cutoff, status="completed", coverage={"status": "complete"}, groups=groups,
        created_at=cutoff, db_path=path)


def build_fixture(path: Path) -> dict[str, str]:
    ids = build_previous_fixture(path)
    bad = _config()
    bad.pop("evaluationPolicy")
    store.append_run_config(config_id="cfg-v304-incomplete", payload=bad,
        created_at="2026-09-01T20:00:00+08:00", db_path=path)
    missing = _publish(path, marker="unconfigured", config_id="cfg-v304-incomplete", codes=("300006.SZ",),
        available="2026-09-01T21:05:00+08:00")[0]
    tied = _publish(path, marker="tied", config_id="cfg-fixture", codes=("300007.SZ", "300008.SZ"),
        available="2026-09-06T21:05:00+08:00")
    windows = {item["companyWindowId"]: item for item in store.list_company_windows(db_path=path)}
    _market_days(path, windows[missing["companyWindowId"]], missing_limit=False)
    _market_days(path, windows[ids["unhandledWindowId"]], missing_limit=True)
    store.freeze_company_window_selection(company_window_id=missing["companyWindowId"],
        frozen_at="2026-09-04T16:00:00+08:00", db_path=path)
    _source_coverage(path)
    _morning(path, tied)
    return {**ids, "unconfiguredWindowId": missing["companyWindowId"],
            "tiedWindowId": tied[0]["companyWindowId"], "withdrawnOpportunityId": tied[0]["opportunityId"]}
