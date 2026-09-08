"""Reusable, isolated K10 3.0.2 cross-layer acceptance fixture.

It deliberately writes only a caller-owned temporary SQLite database.  Every
publication, window, market fact, report, and analysis artifact is created by
the production schema/store/runtime code; fake providers only replace remote
LLM/search responses.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd

from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.api.k10 import create_router
from neckline.k10 import runtime, store
from neckline.k10.evaluation import evaluate_company_window, evaluation_state
from neckline.k10.market_observation import fetch_market_day_fact, record_market_day_fact
from neckline.k10.morning_runtime import morning_review_handler
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from neckline.data.realtime import DualQuote, Quote
from neckline.data.tushare_client import TushareResult
from neckline.k10.schema import initialize_schema
from neckline.k10.types import OpportunityPublicationInput
from neckline.llm.base import LLMResult

from .k10_v305_fixture import append_approved_execution_profile


TZ = "+08:00"
FIRST_CUTOFF = f"2026-08-28T21:00:00{TZ}"
FIRST_AVAILABLE = f"2026-08-28T21:05:00{TZ}"
SECOND_CUTOFF = f"2026-08-31T21:00:00{TZ}"
SECOND_AVAILABLE = f"2026-08-31T21:05:00{TZ}"
MORNING_AT = f"2026-09-01T08:40:00{TZ}"


class _Provider:
    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)

    def chat(self, _messages, **_kwargs):
        return LLMResult(ok=True, content=self._texts.pop(0), provider="fixture", model="deepseek-v4-pro",
                         prompt_tokens=7, completion_tokens=5, total_tokens=12, usage_unavailable=False)


def _config() -> dict[str, Any]:
    return {
        "configVersion": "k10-v1.4", "universe": "chinext", "excludeBaijiu": True,
        "hardExclusions": {"approved": True, "board": "chinext", "priceLimit": "none", "st": "exclude", "swL2Exclusions": ["801125.SI"]},
        "sourceAdapters": [{"key": "fixture", "lateArrivalReplaySeconds": 86400}], "modelRoutes": {"discovery": "deepseek-v4-pro", "analysis": "deepseek-v4-pro", "morning": "deepseek-v4-pro"},
        "taskPolicies": {"analysis": {"maxAttempts": 2, "modelMaxAttempts": 2, "timeoutSeconds": 30, "costLimit": None}, "morning": {"maxAttempts": 2, "modelMaxAttempts": 2, "timeoutSeconds": 30, "costLimit": None}, "evaluation": {"maxAttempts": 2, "costLimit": None}},
        "evaluationPolicy": {"version": "k10-evaluation-v1.4", "selectionFreeze": "d1_open_0930", "window": "d1_d2", "primaryMetric": "close_limit_up_any_d1_d2"},
        "marketCollection": {"retryIntervalSeconds": 300, "retryUntilMinutesAfterClose": 120},
    }


def _stable(prefix: str, value: str) -> str:
    return f"{prefix}_{sha256(value.encode()).hexdigest()[:20]}"


def _calendar(path: Path) -> None:
    import sqlite3
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES('SSE',?,?)", [
            ("20260827", 1), ("20260828", 1), ("20260829", 0), ("20260830", 0),
            ("20260831", 1), ("20260901", 1), ("20260902", 1), ("20260903", 1),
        ])


def _comparison(*, code: str, opportunity_key: str, role: str, rank: int, refs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "summary": f"{code} 的事件内比较", "differences": {"role": role,
        "priorityReason": "直接业务证据" if role == "primary" else "证据仍待补充",
        "gap": "与同事件公司相比的可核差异", "rankChangeConditions": "后续订单或反证披露",
        "twoDayReason": "固定两日内有公开催化"}, "evidenceRefs": refs, "rank": rank,
        "classification": {"kind": "initial", "opportunityKey": opportunity_key, "reason": "首发冻结",
        "newFacts": "本轮正式披露", "changedJudgment": None, "twoDayReason": "固定窗口", "relatedOpportunityId": None},
        "historicalCases": [{"caseId": "historic-success", "outcome": "success", "summary": "同机制公告后已有可核封板记录",
          "sourceBasedDescription": "离线验收资料明确写明该历史事件在首个观察日收盘封板。",
          "categoryEvidence": [{"outcome": "success", "sourceRef": refs[0], "claim": "首个观察日收盘封板"}],
          "observedAt": "2026-05-08T15:00:00+08:00", "sourceRefs": refs, "marketFacts": []},
          {"caseId": "historic-unclassified", "outcome": "unclassified", "summary": "公开材料有事件事实但未声明结果分类",
          "sourceBasedDescription": "离线验收资料只确认历史公告时间与机制，未把市场表现归类为成功、平淡或失败。",
          "categoryEvidence": [], "observedAt": "2026-05-12T15:00:00+08:00", "sourceRefs": refs, "marketFacts": []}],
        "historicalCoverage": {"state": "partial", "requestedOutcomes": ["success", "flat", "failure"],
          "presentOutcomes": ["success"], "missingOutcomes": ["flat", "failure"],
          "reason": "公开资料缺少可追溯平淡与失败分类", "sourceRefs": refs},
    }


def _publish(path: Path, *, scan_id: str, batch_id: str, cutoff: str, available: str,
             event_id: str, candidates: list[tuple[str, str, int]]) -> list[dict[str, Any]]:
    refs = [{"documentId": "doc-fixture", "revision": 1}]
    store.append_event_revision(event_id=event_id, stable_key=event_id, headline="跨层验收催化", event_kind="policy",
        facts={"eventComparison": {"summary": "同事件共同事实一次说明", "evidenceRefs": refs}}, source_refs=refs,
        supersedes_revision=None, created_at=cutoff, db_path=path)
    store.create_scan(scan_id=scan_id, window_kind="evening", cutoff_at=cutoff, config_id="cfg-fixture", config_revision=1,
        status="completed", coverage={"status": "complete"}, created_at=cutoff, completed_at=cutoff, db_path=path)
    inputs = []
    for code, role, rank in candidates:
        candidate_id = _stable("candidate", f"{scan_id}:{code}")
        opportunity_key = f"{event_id}:{code}"
        comparison = _comparison(code=code, opportunity_key=opportunity_key, role=role, rank=rank, refs=refs)
        store.create_candidate(candidate_id=candidate_id, scan_id=scan_id, event_id=event_id, event_revision=1,
            company_code=code, comparison=comparison, evidence=refs, created_at=cutoff, db_path=path)
        inputs.append(OpportunityPublicationInput(candidate_id=candidate_id, company_code=code, event_id=event_id,
            event_revision=1, opportunity_key=opportunity_key, catalyst_stage="approval", category=role,
            comparison=comparison, evidence_refs=tuple(refs), source_marker="evening", related_opportunity_id=None))
    store.publish_opportunities(batch_id=batch_id, scan_id=scan_id, publication_kind="evening", inputs=inputs,
        db_path=path, clock=lambda: datetime.fromisoformat(available))
    return store.list_opportunities(batch_id=batch_id, db_path=path)


def _evaluate(path: Path, window: dict[str, Any], *, touch: bool) -> None:
    refs = [{"documentId": "doc-fixture", "revision": 1}]
    for day in (window["d1TradeDate"], window["d2TradeDate"]):
        store.append_market_day_fact(company_code=window["companyCode"], trade_date=day, availability="available",
            open_price=10, high_price=11, low_price=9.8, close_price=11 if touch else 10.1, pre_close=10,
            limit_up_price=11, close_limit_up=touch, touched_limit_up=touch, source_refs=refs,
            obtained_at="2026-09-04T16:00:00+08:00", created_at="2026-09-04T16:00:00+08:00", db_path=path)
    facts = store.latest_market_day_facts(company_code=window["companyCode"], db_path=path)
    result = evaluate_company_window(window=window, market_facts=facts, as_of="2026-09-04T16:00:00+08:00")
    store.append_company_window_evaluation(company_window_id=window["companyWindowId"], state=evaluation_state(result),
        fact_refs=result.fact_refs, result=result.to_dict(), evaluated_at="2026-09-04T16:00:00+08:00",
        created_at="2026-09-04T16:00:00+08:00", db_path=path)


def _market_conflict_and_re_evaluate(path: Path, window: dict[str, Any]) -> None:
    """Use the production dual-source collector, then persist the resulting anomaly revision."""
    trade_date = window["d1TradeDate"]
    compact = trade_date.replace("-", "")
    def result(rows: list[dict[str, Any]]) -> TushareResult:
        return TushareResult.success(pd.DataFrame(rows))
    def quote(source: str, *, high: float) -> Quote:
        return Quote(code=window["companyCode"].split(".")[0], name="离线核验", price=11.0, pre_close=10.0,
                     open=10.0, high=high, low=9.8, volume=1.0, amount=1.0,
                     ts=f"{trade_date} 15:01:00", source=source, traded_price=11.0)
    fact = fetch_market_day_fact(
        company_code=window["companyCode"], trade_date=trade_date, obtained_at="2026-09-04T16:01:00+08:00",
        daily_fetcher=lambda *_: result([{"ts_code": window["companyCode"], "trade_date": compact, "open": 10.0, "high": 11.0, "low": 9.8, "close": 11.0, "pre_close": 10.0}]),
        limit_fetcher=lambda *_: result([{"ts_code": window["companyCode"], "trade_date": compact, "up_limit": 11.0}]),
        adj_factor_fetcher=lambda *_: result([{"ts_code": window["companyCode"], "trade_date": compact, "adj_factor": 1.0}]),
        suspend_fetcher=lambda *_: result([]),
        quote_fetcher=lambda _: {window["companyCode"]: DualQuote(window["companyCode"], quote("sina", high=11.0), quote("tencent", high=10.8))},
    )
    if fact["availability"] != "anomaly":
        raise RuntimeError("fixture dual-source conflict was not retained")
    record_market_day_fact(fact=fact, db_path=path, created_at=fact["obtainedAt"])
    facts = store.latest_market_day_facts(company_code=window["companyCode"], db_path=path)
    result_value = evaluate_company_window(window=window, market_facts=facts, as_of="2026-09-04T16:02:00+08:00")
    store.append_company_window_evaluation(company_window_id=window["companyWindowId"], state=evaluation_state(result_value),
        fact_refs=result_value.fact_refs, result=result_value.to_dict(), evaluated_at="2026-09-04T16:02:00+08:00",
        created_at="2026-09-04T16:02:00+08:00", db_path=path)


def _append_morning_document(path: Path, *, document_id: str, source_key: str, text: str) -> list[dict[str, Any]]:
    store.append_document_version(document_id=document_id, source_key=source_key, external_id=document_id,
        canonical_url=f"https://example.invalid/{document_id}", content_sha256=sha256(text.encode()).hexdigest(),
        published_at=MORNING_AT, published_precision="exact", fetched_at=MORNING_AT, original_text=text,
        excerpt=text[:80], fetch_version="fixture-morning", metadata={"title": "离线晨间核验资料"},
        created_at=MORNING_AT, db_path=path)
    return [{"documentId": document_id, "revision": 1}]


def _bind_v305_execution(path: Path, *, task_id: str, bound_at: str) -> None:
    """Bind each paid fixture task to the test-only approved V2 profile.

    The worker must exercise the same fail-closed V3.0.5 gate as production;
    fake providers replace only the network seam, never the durable approval
    or run-control boundary.
    """
    config_id, revision = append_approved_execution_profile(
        # The immutable approval timestamp is fixed.  Later task bindings are
        # distinct scheduled actions, not attempts to rewrite that approval.
        db_path=path, created_at=FIRST_CUTOFF, config_id="v302-fixture-execution",
    )
    store.bind_task_execution(
        task_id=task_id, execution_config_id=config_id,
        execution_config_revision=revision, binding_kind="scheduled",
        bound_at=bound_at, db_path=path,
    )


def _run_morning_reviews(path: Path, *, targets: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Run C's production handler once per unique opportunity and retain its report items."""
    outcomes = [
        (True, "invalidated", "needs_review", "complete", "晨间独立核验推翻核心理由"),
        (True, "current", "current", "complete", "新增资料改变了原有论据强度"),
        (False, "current", "current", "complete", "冻结资料与原判断一致"),
        (False, "needs_review", "needs_review", "partial", "资料范围不足，等待人工复核"),
        (False, "current", "current", "complete", "新增正式候选已完成初步核验"),
    ]
    groups = {section: [] for section in ("major_contrary", "thesis_changed", "continuing_or_expiring", "new", "needs_review")}
    for index, ((target, descriptor), (material, reason_status, observation_status, source_status, summary)) in enumerate(zip(targets, outcomes), start=1):
        candidate_id = target["candidateId"]
        morning_refs = _append_morning_document(path, document_id=f"doc-morning-{index}", source_key=f"fixture_morning_{index}", text=f"{target['companyCode']} 的晨间事实核验：{summary}")
        independent_refs = _append_morning_document(path, document_id=f"doc-independent-{index}", source_key=f"fixture_independent_{index}", text=f"{target['companyCode']} 的独立来源复核：{summary}")
        contrary = (
            [{"documentId": independent_refs[0]["documentId"], "revision": 1, "claim": summary}]
            if reason_status == "invalidated" else
            ([{"documentId": morning_refs[0]["documentId"], "revision": 1, "claim": summary}]
             if material and reason_status == "needs_review" else [])
        )
        response = json.dumps({"material": material, "reasonStatus": reason_status, "observationStatus": observation_status,
                               "summary": summary, "materialContraryEvidence": contrary}, ensure_ascii=False)
        payload = {"candidateId": candidate_id, "observationId": None, "originalCutoffAt": target["availableAt"],
            "morningEvidenceRefs": morning_refs, "independentVerificationRefs": independent_refs,
            "companyWindowId": target["companyWindowId"], "displayRank": index,
            "selectionState": descriptor["selectionState"], "lifecycle": target["state"], "isNew": descriptor["isNew"],
            "sourceStatus": source_status, "configId": "cfg-fixture", "configRevision": 1}
        task_id = f"morning-review-{index}"
        store.enqueue_task(task_id=task_id, kind="morning_review", idempotency_key=task_id, input_version="fixture-config",
            input_cutoff_at=MORNING_AT, payload=payload, budget=_config()["taskPolicies"]["morning"], created_at=MORNING_AT, db_path=path)
        _bind_v305_execution(path, task_id=task_id, bound_at=MORNING_AT)
        provider = _Provider([response])
        resolution = lambda **_: ProviderResolution("configured", provider, "fixture", None)
        with patch("neckline.k10.morning_runtime.resolve_deepseek_v4_pro", resolution):
            task = run_once(db_path=path, worker_id=f"fixture-morning-{index}", lease_for=timedelta(minutes=5),
                handlers={"morning_review": morning_review_handler}, clock=lambda: datetime.fromisoformat("2026-09-01T01:00:00+00:00"), task_id=task_id)
        if task is None or task.status != "completed":
            raise RuntimeError(f"fixture morning handler failed for {target['companyCode']}")
        import sqlite3
        with sqlite3.connect(path) as connection:
            row = connection.execute("SELECT checkpoint_json FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()
        checkpoint_data = json.loads(row[0]) if row else {}
        item, section = checkpoint_data.get("reportItem"), checkpoint_data.get("reportSection")
        if not isinstance(item, dict) or section not in groups:
            raise RuntimeError("fixture morning handler did not checkpoint a report item")
        groups[section].append(item)
    if any(len(items) != 1 for items in groups.values()):
        raise RuntimeError("fixture must exercise each morning section with one unique opportunity")
    return groups


def _run_analyses(path: Path, window: dict[str, Any]) -> None:
    payload = {"configId": "cfg-fixture", "configRevision": 1, "companyWindow": window,
        "opportunities": [item for item in store.list_opportunities(db_path=path) if item["companyWindowId"] == window["companyWindowId"]],
        "publicationSamples": [], "marketContext": {"status": "unavailable", "reason": "fixture_no_parquet", "asOf": FIRST_AVAILABLE, "sourceRefs": [], "recentDays": []}}
    observed = store.observe_company_window(action_id="keep-action", observation_id="fixture-observation", task_id="fixture-analysis-1",
        outbox_id="fixture-outbox", company_window_id=window["companyWindowId"], idempotency_key="fixture-keep",
        task_input_version="fixture-config", task_input_cutoff_at=FIRST_AVAILABLE, task_payload=payload,
        task_budget=_config()["taskPolicies"]["analysis"], created_at="2026-08-31T09:00:00+08:00", db_path=path)
    _bind_v305_execution(path, task_id=observed.task_id, bound_at="2026-08-31T09:00:00+08:00")
    resolver = lambda **_: ProviderResolution("configured", _Provider(["第一版正方全文", "第一版反方全文"]), "fixture", None)
    completed = run_once(db_path=path, worker_id="fixture-analysis-1", lease_for=timedelta(minutes=5),
        handlers={"analysis": runtime.production_analysis_handler(provider_resolver=resolver)},
        clock=lambda: datetime.fromisoformat("2026-08-31T01:01:00+00:00"), task_id=observed.task_id)
    if completed is None or completed.status != "completed" or completed.attempt_count != 1:
        raise RuntimeError("fixture initial analysis worker did not finish exactly once")
    store.append_document_version(document_id="doc-fixture", source_key="fixture_verification", external_id="fixture-source",
        canonical_url="https://example.invalid/k10-fixture", content_sha256="e" * 64,
        published_at="2026-09-04T15:30:00+08:00", published_precision="exact", fetched_at="2026-09-04T15:35:00+08:00",
        original_text="第二版追加核验资料", excerpt="追加资料摘要", fetch_version="fixture-v2", metadata={"title": "追加核验资料"},
        created_at="2026-09-04T15:35:00+08:00", db_path=path)
    opportunity_id = next(item["opportunityId"] for item in payload["opportunities"])
    store.append_opportunity_update(lifecycle_event_id="fixture-evidence-update", opportunity_id=opportunity_id,
        kind="evidence_update", reason="追加可追溯核验资料", source_refs=[{"documentId": "doc-fixture", "revision": 2}],
        content={"fixture": True}, occurred_at="2026-09-04T15:35:00+08:00", created_at="2026-09-04T15:35:00+08:00", db_path=path)
    request = store.create_analysis_request(request_id="fixture-request-2", task_id="fixture-analysis-2", company_window_id=window["companyWindowId"],
        kind="evidence_update", question=None, source_refs=[{"documentId": "doc-fixture", "revision": 2}], idempotency_key="fixture-update",
        input_cutoff_at="2026-09-04T16:00:00+08:00", task_input_version="fixture-config", task_payload=payload,
        task_budget=_config()["taskPolicies"]["analysis"], created_at="2026-09-04T16:00:00+08:00", db_path=path)
    _bind_v305_execution(path, task_id=str(request["taskId"]), bound_at="2026-09-04T16:00:00+08:00")
    resolver = lambda **_: ProviderResolution("configured", _Provider(["第二版正方全文", "第二版反方全文"]), "fixture", None)
    completed = run_once(db_path=path, worker_id="fixture-analysis-2", lease_for=timedelta(minutes=5),
        handlers={"analysis": runtime.production_analysis_handler(provider_resolver=resolver)},
        clock=lambda: datetime.fromisoformat("2026-09-04T08:01:00+00:00"), task_id=request["taskId"])
    if completed is None or completed.status != "completed" or completed.attempt_count != 1:
        raise RuntimeError("fixture evidence-update analysis worker did not finish exactly once")


def build_fixture(path: Path) -> dict[str, str]:
    """Build a complete temporary Schema 5 database and return stable UI identifiers."""
    initialize_schema(path); _calendar(path)
    store.set_run_control(state="open", reason_code="fixture_approved", changed_at=FIRST_CUTOFF,
                          changed_by="test", db_path=path)
    store.append_run_config(config_id="cfg-fixture", payload=_config(), created_at=FIRST_CUTOFF, db_path=path)
    store.append_document_version(document_id="doc-fixture", source_key="fixture_verification", external_id="fixture-source",
        canonical_url="https://example.invalid/k10-fixture", content_sha256="f" * 64, published_at=FIRST_CUTOFF,
        published_precision="exact", fetched_at=FIRST_CUTOFF,
        original_text="离线验收资料：同机制历史事件在首个观察日收盘封板；另一公开记录仅确认公告机制，未声明市场结果分类。",
        excerpt="可追溯的历史案例与比较资料", fetch_version="fixture-v1", metadata={"title": "离线验收资料"},
        created_at=FIRST_CUTOFF, db_path=path)
    _publish(path, scan_id="scan-fixture-1", batch_id="batch-fixture-1", cutoff=FIRST_CUTOFF, available=FIRST_AVAILABLE,
        event_id="event-fixture-1", candidates=[("300001.SZ", "primary", 1), ("300002.SZ", "alternative", 2),
                                                  ("300003.SZ", "tied", 3), ("300004.SZ", "tied", 3)])
    _publish(path, scan_id="scan-fixture-2", batch_id="batch-fixture-2", cutoff=SECOND_CUTOFF, available=SECOND_AVAILABLE,
        event_id="event-fixture-2", candidates=[("300001.SZ", "primary", 1)])
    windows = {item["companyCode"] + ":" + item["firstBatchId"]: item for item in store.list_company_windows(db_path=path)}
    kept = windows["300001.SZ:batch-fixture-1"]
    skipped = windows["300002.SZ:batch-fixture-1"]
    unhandled = windows["300003.SZ:batch-fixture-1"]
    needs_review = windows["300004.SZ:batch-fixture-1"]
    overlap = windows["300001.SZ:batch-fixture-2"]
    _evaluate(path, kept, touch=True); _evaluate(path, overlap, touch=True); _evaluate(path, skipped, touch=False)
    _market_conflict_and_re_evaluate(path, skipped)
    _run_analyses(path, kept)
    store.append_company_window_action(action_id="skip-action", company_window_id=skipped["companyWindowId"], action="skip",
        idempotency_key="fixture-skip", reason="用户明确略过", created_at="2026-08-31T09:00:00+08:00", db_path=path)
    for window in (kept, skipped, unhandled, needs_review, overlap):
        store.freeze_company_window_selection(company_window_id=window["companyWindowId"], frozen_at="2026-09-04T16:00:00+08:00", db_path=path)
    store.create_scan(scan_id="scan-fixture-morning", window_kind="morning", cutoff_at=MORNING_AT, config_id="cfg-fixture", config_revision=1,
        status="completed", coverage={"status": "partial"}, created_at=MORNING_AT, completed_at=MORNING_AT, db_path=path)
    target_rows = {item["companyWindowId"]: item for item in store.list_morning_report_targets(
        as_of=datetime.fromisoformat(MORNING_AT), scan_id="scan-fixture-morning", db_path=path)}
    groups = _run_morning_reviews(path, targets=[
        (target_rows[overlap["companyWindowId"]], {"selectionState": "unhandled", "isNew": False}),
        (target_rows[skipped["companyWindowId"]], {"selectionState": "skipped", "isNew": False}),
        (target_rows[kept["companyWindowId"]], {"selectionState": "kept", "isNew": False}),
        (target_rows[needs_review["companyWindowId"]], {"selectionState": "unhandled", "isNew": False}),
        (target_rows[unhandled["companyWindowId"]], {"selectionState": "unhandled", "isNew": True}),
    ])
    store.append_morning_report(report_id="report-fixture", scan_id="scan-fixture-morning", cutoff_at=MORNING_AT, generated_at=MORNING_AT,
        status="partial", coverage={"status": "partial", "gaps": ["historical_failure_case_missing"]}, groups=groups, created_at=MORNING_AT, db_path=path)
    return {"primaryWindowId": kept["companyWindowId"], "overlapWindowId": overlap["companyWindowId"],
            "skippedWindowId": skipped["companyWindowId"], "unhandledWindowId": unhandled["companyWindowId"],
            "needsReviewWindowId": needs_review["companyWindowId"], "reportId": "report-fixture"}


def export_api_json(path: Path, output_dir: Path) -> dict[str, Path]:
    """Export actual FastAPI endpoint payloads for the Swift decoding acceptance pass."""
    if not path.exists():
        ids = build_fixture(path)
    else:
        windows = store.list_company_windows(db_path=path)
        primary = next((item for item in windows if store.list_analysis_chain(company_window_id=item["companyWindowId"], db_path=path)["items"]), windows[0])
        ids = {"primaryWindowId": primary["companyWindowId"]}
    app = FastAPI(); app.include_router(create_router(lambda: path, lambda: None, lambda: output_dir / "parquet"))
    client = TestClient(app)
    routes = {"windows": "/api/v1/k10/company-windows", "morning": "/api/v1/k10/morning-reports/latest",
              "analysis": f"/api/v1/k10/company-windows/{ids['primaryWindowId']}/analysis-chain", "opportunities": "/api/v1/k10/opportunities"}
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    for name, route in routes.items():
        response = client.get(route); response.raise_for_status()
        target = output_dir / f"{name}.json"; target.write_text(json.dumps(response.json(), ensure_ascii=False, indent=2), encoding="utf-8")
        result[name] = target
    return result
