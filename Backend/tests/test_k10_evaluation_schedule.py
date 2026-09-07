from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

from neckline.k10 import store
from neckline.k10.evaluation_schedule import maintain_evaluations
from neckline.k10.schema import initialize_schema
from neckline.k10.types import OpportunityPublicationInput
from neckline.k10.windows import SHANGHAI
from neckline.k10.worker import TaskResult, run_once


def _config() -> dict:
    return {
        "configVersion": "k10-v1.4", "universe": "chinext", "excludeBaijiu": True,
        "hardExclusions": {"approved": True, "board": "chinext", "priceLimit": "none", "st": "exclude", "swL2Exclusions": ["801125.SI"]},
        "sourceAdapters": ["fixture"], "modelRoutes": {"analysis": "deepseek-v4-pro"},
        "taskPolicies": {"evaluation": {"maxAttempts": 2, "costLimit": None}},
        "marketCollection": {"retryIntervalSeconds": 300, "retryUntilMinutesAfterClose": 120},
        "evaluationPolicy": {"version": "k10-evaluation-v1.4", "selectionFreeze": "d1_open_0930", "window": "d1_d2", "primaryMetric": "close_limit_up_any_d1_d2"},
    }


def _seed(path: Path) -> str:
    initialize_schema(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES ('SSE', ?, 1)", [("20260906",), ("20260907",), ("20260908",), ("20260909",)])
    revision = store.append_run_config(config_id="cfg", payload=_config(), created_at="2026-09-07T09:00:00+08:00", db_path=path)
    event = store.append_event_revision(event_id="event", stable_key="event", headline="催化", event_kind="news", facts={}, source_refs=[], supersedes_revision=None, created_at="2026-09-07T09:00:00+08:00", db_path=path)
    store.create_scan(scan_id="scan", window_kind="morning", cutoff_at="2026-09-07T09:00:00+08:00", config_id="cfg", config_revision=revision, status="completed", coverage={"status": "complete"}, created_at="2026-09-07T09:00:00+08:00", completed_at="2026-09-07T09:00:00+08:00", db_path=path)
    comparison = {"summary": "比较", "differences": {"role": "primary", "priorityReason": "受益", "gap": "差距", "rankChangeConditions": "反证", "twoDayReason": "两日"}, "evidenceRefs": [], "rank": 1, "classification": {"kind": "initial", "opportunityKey": "key", "reason": "首发", "newFacts": "资料", "changedJudgment": None, "twoDayReason": "两日", "relatedOpportunityId": None}}
    store.create_candidate(candidate_id="candidate", scan_id="scan", event_id=event.event_id, event_revision=event.revision, company_code="300001.SZ", comparison=comparison, evidence=[], created_at="2026-09-07T09:00:00+08:00", db_path=path)
    store.publish_opportunities(batch_id="batch", scan_id="scan", publication_kind="morning", inputs=(OpportunityPublicationInput(candidate_id="candidate", company_code="300001.SZ", event_id="event", event_revision=1, opportunity_key="key", catalyst_stage="initial", category="primary", comparison=comparison, evidence_refs=(), source_marker="morning"),), db_path=path, clock=lambda: datetime(2026, 9, 7, 9, 29, tzinfo=SHANGHAI))
    return store.list_company_windows(db_path=path)[0]["companyWindowId"]


def _complete(task_id: str, path: Path, at: datetime) -> None:
    task = run_once(db_path=path, worker_id="test", lease_for=timedelta(minutes=1), task_id=task_id,
                    handlers={"collect_market_day_fact": lambda _: TaskResult("completed", "fixture"),
                              "evaluate_company_window": lambda _: TaskResult("completed", "fixture")}, clock=lambda: at)
    assert task is not None and task.status == "completed"


def _fact(path: Path, day: str, revision_value: float = 10.0) -> None:
    store.append_market_day_fact(company_code="300001.SZ", trade_date=day, availability="available", open_price=10.0,
                                 high_price=revision_value, low_price=9.9, close_price=revision_value, pre_close=10.0,
                                 limit_up_price=11.0, close_limit_up=False, touched_limit_up=False, source_refs=[],
                                 adj_factor=1.0, obtained_at=f"{day}T15:01:00+08:00", created_at=f"{day}T15:01:00+08:00", db_path=path)


def test_schedule_freezes_collects_once_and_re_evaluates_on_fact_revision(tmp_path: Path) -> None:
    path = tmp_path / "schedule.sqlite"; window_id = _seed(path)
    d1_open = datetime(2026, 9, 7, 9, 31, tzinfo=SHANGHAI)
    first = maintain_evaluations(db_path=path, now=d1_open)
    assert first.frozen_windows == 1
    assert store.get_company_window_selection(company_window_id=window_id, db_path=path)["snapshotState"] == "unhandled"

    d1_close = datetime(2026, 9, 7, 15, 5, tzinfo=SHANGHAI)
    scheduled = maintain_evaluations(db_path=path, now=d1_close)
    assert scheduled.market_tasks == 1
    assert maintain_evaluations(db_path=path, now=d1_close).market_tasks == 0
    d1_task = "task_" + __import__("hashlib").sha256("market-day\x1f300001.SZ\x1f2026-09-07\x1fregular".encode()).hexdigest()[:32]
    _fact(path, "2026-09-07")
    _complete(d1_task, path, d1_close)
    assert maintain_evaluations(db_path=path, now=d1_close).evaluation_tasks == 1  # known D1 facts are visible before D2

    d2_close = datetime(2026, 9, 8, 15, 5, tzinfo=SHANGHAI)
    due = maintain_evaluations(db_path=path, now=d2_close)
    assert due.market_tasks == 1 and due.evaluation_tasks == 1  # D2 does not wait for queued market collection
    d2_task = "task_" + __import__("hashlib").sha256("market-day\x1f300001.SZ\x1f2026-09-08\x1fregular".encode()).hexdigest()[:32]
    _fact(path, "2026-09-08")
    _complete(d2_task, path, d2_close)
    queued = maintain_evaluations(db_path=path, now=d2_close)
    assert queued.evaluation_tasks == 1
    evaluation = next(task for task_id in (
        "task_" + __import__("hashlib").sha256(f"evaluate-window\x1f{window_id}\x1fd2\x1f2026-09-07@1,2026-09-08@1\x1fcfg\x1f1".encode()).hexdigest()[:32],
    ) if (task := store.get_task(task_id=task_id, db_path=path)))
    assert evaluation.payload["marketFactRevisions"] == [
        {"companyCode": "300001.SZ", "tradeDate": "2026-09-07", "revision": 1, "factId": store.market_day_fact_id(company_code="300001.SZ", trade_date="2026-09-07")},
        {"companyCode": "300001.SZ", "tradeDate": "2026-09-08", "revision": 1, "factId": store.market_day_fact_id(company_code="300001.SZ", trade_date="2026-09-08")},
    ]
    _complete(evaluation.task_id, path, d2_close)
    _fact(path, "2026-09-08", 10.5)
    assert maintain_evaluations(db_path=path, now=d2_close).evaluation_tasks == 1


def test_schedule_retries_market_failure_only_to_frozen_limit(tmp_path: Path) -> None:
    path = tmp_path / "retry.sqlite"; _seed(path)
    now = datetime(2026, 9, 7, 15, 5, tzinfo=SHANGHAI)
    maintain_evaluations(db_path=path, now=now)
    task_id = "task_" + __import__("hashlib").sha256("market-day\x1f300001.SZ\x1f2026-09-07\x1fregular".encode()).hexdigest()[:32]
    for attempt in (1, 2):
        task = run_once(db_path=path, worker_id=f"fail-{attempt}", lease_for=timedelta(minutes=1), task_id=task_id,
                        handlers={"collect_market_day_fact": lambda _: TaskResult("failed", "fixture", error="临时来源失败")}, clock=lambda: now)
        assert task and task.status == "failed" and task.attempt_count == attempt
        scheduled = maintain_evaluations(db_path=path, now=now + timedelta(minutes=5))
        if attempt == 1:
            assert scheduled.retried_market_tasks == 1 and store.get_task(task_id=task_id, db_path=path).status == "queued"
        else:
            assert scheduled.retried_market_tasks == 0 and store.get_task(task_id=task_id, db_path=path).status == "failed"


def test_invalid_evaluation_policy_cannot_start_market_calls():
    from neckline.k10.evaluation_schedule import _collection_policy
    config = _config()
    config["evaluationPolicy"]["primaryMetric"] = "unapproved_metric"
    assert _collection_policy({"payload": config}) is None


def test_real_handlers_preserve_due_gap_then_append_fixed_window_correction(tmp_path, monkeypatch):
    from neckline.k10 import evaluation_runtime

    path = tmp_path / "automatic-flow.sqlite"
    window_id = _seed(path)
    now = datetime(2026, 9, 7, 15, 1, tzinfo=SHANGHAI)
    handlers = {
        "collect_market_day_fact": lambda context: evaluation_runtime.market_day_fact_handler(context, clock=lambda: now),
        "evaluate_company_window": lambda context: evaluation_runtime.evaluation_handler(context, clock=lambda: now),
    }

    def fetch(**kwargs):
        day = kwargs["trade_date"]
        hit = day == "2026-09-08"
        return {
            "companyCode": "300001.SZ", "tradeDate": day, "availability": "available",
            "openPrice": 10, "highPrice": 11 if hit else 10.5, "lowPrice": 9.9,
            "closePrice": 11 if hit else 10.2, "preClose": 10, "limitUpPrice": 11,
            "adjFactor": 1, "closeLimitUp": hit, "touchedLimitUp": hit,
            "sourceRefs": [{"source": "synthetic"}], "obtainedAt": now.isoformat(),
        }

    monkeypatch.setattr(evaluation_runtime, "fetch_market_day_fact", fetch)

    def run_kind(kind):
        with sqlite3.connect(path) as conn:
            row = conn.execute("SELECT task_id FROM k10_tasks WHERE status='queued' AND kind=? ORDER BY created_at,task_id LIMIT 1", (kind,)).fetchone()
        assert row is not None
        task = run_once(db_path=path, worker_id="flow", lease_for=timedelta(minutes=1), task_id=row[0],
                        handlers=handlers, clock=lambda: now)
        assert task.status == "completed"

    maintain_evaluations(db_path=path, now=now)
    run_kind("collect_market_day_fact")
    maintain_evaluations(db_path=path, now=now)
    run_kind("evaluate_company_window")
    pending = store.list_company_window_evaluations(db_path=path)[0]
    assert pending["state"] == "pending" and not pending["result"]["primaryEligible"]
    assert pending["result"]["d1"]["close"] == 10.2

    now = datetime(2026, 9, 8, 15, 1, tzinfo=SHANGHAI)
    maintain_evaluations(db_path=path, now=now)
    # The due evaluation is executable before the D2 source fetch finishes.
    run_kind("evaluate_company_window")
    missing = store.list_company_window_evaluations(db_path=path)[0]
    assert missing["state"] == "incomplete"
    assert missing["result"]["d2"]["availability"] == "data_gap"
    run_kind("collect_market_day_fact")
    maintain_evaluations(db_path=path, now=now)
    run_kind("evaluate_company_window")
    hit = store.list_company_window_evaluations(db_path=path)[0]
    assert hit["state"] == "completed" and hit["result"]["closeLimitHitAny"]
    assert hit["result"]["primaryEligible"]
    assert hit["result"]["selection"]["state"] == "unhandled"

    _fact(path, "2026-09-08", 10.5)  # Later source correction, not a new opportunity.
    maintain_evaluations(db_path=path, now=now)
    run_kind("evaluate_company_window")
    corrected = store.list_company_window_evaluations(db_path=path)[0]
    assert corrected["revision"] > hit["revision"]
    assert corrected["result"]["closeLimitHitAny"] is False
    assert corrected["result"]["factRefs"][1]["revision"] == 2
    windows = store.list_company_windows(db_path=path)
    assert len(windows) == 1 and windows[0]["companyWindowId"] == window_id
    assert (windows[0]["d1TradeDate"], windows[0]["d2TradeDate"]) == ("2026-09-07", "2026-09-08")
