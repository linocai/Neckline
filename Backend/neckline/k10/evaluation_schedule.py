"""Bounded writer maintenance for K10-v1.4 fixed company windows.

This module owns the automatic portion of the two-day observation loop.  It is
called by the worker process, never from a GET request: D1 selection freezes at
the recorded boundary, D1/D2 facts are collected once per company/date, and an
evaluation is queued only from a frozen fact-revision signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from hashlib import sha256
from typing import Any, Mapping

from . import store
from .config import validate_run_config
from .schema import read_connection, require_schema
from .windows import SHANGHAI


def _id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{sha256(chr(31).join(parts).encode('utf-8')).hexdigest()[:32]}"


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("公司窗口时间必须带时区")
    return parsed


def _market_close(trade_date: str) -> datetime:
    return datetime.combine(datetime.fromisoformat(trade_date).date(), time(15), tzinfo=SHANGHAI)


def _window_config(window: Mapping[str, Any], *, db_path) -> Mapping[str, Any] | None:
    batch = store.get_publication_batch(batch_id=str(window["firstBatchId"]), db_path=db_path)
    if batch is None:
        return None
    scan = store.get_scan(scan_id=str(batch["scanId"]), db_path=db_path)
    if scan is None or not isinstance(scan.get("configId"), str) or not isinstance(scan.get("configRevision"), int):
        return None
    return store.read_run_config(config_id=str(scan["configId"]), revision=int(scan["configRevision"]), db_path=db_path)


def _collection_policy(config: Mapping[str, Any] | None) -> tuple[int, int, int] | None:
    payload = config.get("payload") if isinstance(config, Mapping) else None
    if not validate_run_config(payload, scope="evaluation").ready:
        return None
    policy = payload.get("taskPolicies", {}).get("evaluation") if isinstance(payload, Mapping) else None
    collection = payload.get("marketCollection") if isinstance(payload, Mapping) else None
    maximum = policy.get("maxAttempts") if isinstance(policy, Mapping) else None
    interval = collection.get("retryIntervalSeconds") if isinstance(collection, Mapping) else None
    until = collection.get("retryUntilMinutesAfterClose") if isinstance(collection, Mapping) else None
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (maximum, interval, until)):
        return None
    return maximum, interval, until


def _task_updated_at(task_id: str, *, db_path) -> datetime | None:
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute("SELECT updated_at FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()
    return _instant(str(row[0])) if row and row[0] else None


def _retry_if_bounded(task_id: str, *, maximum: int, retry_interval: int, retry_deadline: datetime, now: datetime, db_path) -> bool:
    task = store.get_task(task_id=task_id, db_path=db_path)
    if task is None or task.status != "failed" or task.attempt_count >= maximum or now > retry_deadline:
        return False
    updated = _task_updated_at(task_id, db_path=db_path)
    if updated is not None and now < updated + timedelta(seconds=retry_interval):
        return False
    store.retry_task(task_id=task_id, expected_attempt_count=task.attempt_count, retried_at=now.isoformat(), db_path=db_path)
    return True


def _fact_revisions(*, company_code: str, d1: str, d2: str, db_path) -> list[dict[str, Any]]:
    latest: dict[str, Mapping[str, Any]] = {}
    for fact in store.list_market_day_facts(company_code=company_code, db_path=db_path):
        trade_date = fact.get("tradeDate")
        revision = fact.get("revision")
        if trade_date in {d1, d2} and isinstance(revision, int):
            prior = latest.get(str(trade_date))
            if prior is None or int(prior["revision"]) < revision:
                latest[str(trade_date)] = fact
    return [{"companyCode": company_code, "tradeDate": day,
             "revision": int(latest[day]["revision"]), "factId": latest[day].get("factId")}
            for day in dict.fromkeys((d1, d2)) if day in latest]


def _collection_is_complete(fact: Mapping[str, Any]) -> bool:
    """Only stop normal backfill when a full day fact, or a confirmed suspension, exists."""
    if fact.get("availability") == "suspended":
        return True
    if fact.get("availability") != "available":
        return False
    return all(fact.get(field) is not None for field in (
        "open", "high", "low", "close", "preClose", "limitUpPrice", "adjFactor",
    ))


@dataclass(frozen=True)
class ScheduleResult:
    frozen_windows: int = 0
    market_tasks: int = 0
    retried_market_tasks: int = 0
    evaluation_tasks: int = 0
    retried_evaluation_tasks: int = 0


def maintain_evaluations(*, db_path, now: datetime) -> ScheduleResult:
    """Advance due window writers once, with no unbounded polling side effects.

    Repeated calls only re-read completed work. Failed market/evaluation tasks
    are requeued at most up to their frozen ``maxAttempts`` value.  A completed
    fact revision changes the evaluation task identity, so a late correction
    appends an evaluation revision without moving the original D1/D2 window.
    """
    if now.tzinfo is None:
        raise ValueError("evaluation maintenance now 必须带时区")
    frozen = market_created = market_retried = evaluation_created = evaluation_retried = 0
    windows = store.list_company_windows(db_path=db_path)
    due_by_day: dict[tuple[str, str], list[tuple[Mapping[str, Any], Mapping[str, Any] | None]]] = {}
    for window in windows:
        selection_at = _instant(str(window["d1SelectionAt"]))
        if now >= selection_at:
            state = store.get_company_window_selection(company_window_id=str(window["companyWindowId"]), db_path=db_path)
            if state is not None and state["snapshotState"] is None:
                store.freeze_company_window_selection(company_window_id=str(window["companyWindowId"]), frozen_at=now.isoformat(), db_path=db_path)
                frozen += 1
        config = _window_config(window, db_path=db_path)
        for trade_date in (str(window["d1TradeDate"]), str(window["d2TradeDate"])):
            if now >= _market_close(trade_date):
                due_by_day.setdefault((str(window["companyCode"]), trade_date), []).append((window, config))

    # One company/day source fetch belongs to every overlapping window.  No
    # policy means no source call: a reader can still surface the missing data.
    for (company_code, trade_date), entries in due_by_day.items():
        policies = [policy for _, config in entries if (policy := _collection_policy(config)) is not None]
        if not policies:
            continue
        maximum = max(policy[0] for policy in policies)
        retry_interval = min(policy[1] for policy in policies)
        retry_until = max(policy[2] for policy in policies)
        close_at = _market_close(trade_date)
        normal_deadline = close_at + timedelta(minutes=retry_until)
        regular_task_id = _id("task", "market-day", company_code, trade_date, "regular")
        late_task_id = _id("task", "market-day", company_code, trade_date, "late-recovery")
        latest = _fact_revisions(company_code=company_code, d1=trade_date, d2=trade_date, db_path=db_path)
        current_fact = latest[0] if latest else None
        if current_fact is not None:
            facts = store.list_market_day_facts(company_code=company_code, db_path=db_path)
            exact = next((fact for fact in facts if fact.get("tradeDate") == trade_date and fact.get("revision") == current_fact["revision"]), {})
            if _collection_is_complete(exact):
                continue
        # During the normal collection window retry only at the explicit
        # interval.  A late startup gets a separate once-only task, whose own
        # worker retries remain bounded by the same frozen attempt limit.
        late = now > normal_deadline
        task_id = late_task_id if late else regular_task_id
        existing = store.get_task(task_id=task_id, db_path=db_path)
        if existing is None:
            store.enqueue_task(task_id=task_id, kind="collect_market_day_fact",
                               idempotency_key=f"market-day:{company_code}:{trade_date}:{'late' if late else 'regular'}", input_version="k10-market-v1.4",
                               input_cutoff_at=close_at.isoformat(),
                               payload={"companyCode": company_code, "tradeDate": trade_date,
                                        "collectionMode": "late_recovery" if late else "regular"},
                               budget={"maxAttempts": maximum},
                               created_at=now.isoformat(), db_path=db_path)
            market_created += 1
        elif _retry_if_bounded(task_id, maximum=maximum, retry_interval=retry_interval,
                               retry_deadline=(now if late else normal_deadline), now=now, db_path=db_path):
            market_retried += 1

    for window in windows:
        d1, d2 = str(window["d1TradeDate"]), str(window["d2TradeDate"])
        d1_close = _market_close(d1)
        d2_close = _instant(str(window["d2CloseAt"]))
        if now < d1_close:
            continue
        config = _window_config(window, db_path=db_path)
        policy = _collection_policy(config)
        if policy is None:
            continue
        maximum = policy[0]
        stage, cutoff = ("d2", d2_close) if now >= d2_close else ("d1", d1_close)
        # D1 waits for its first collection result.  D2 writes an immediate
        # incomplete revision even while a source task is queued/running; a
        # later fact revision creates a distinct D2 evaluation task.
        if stage == "d1":
            d1_regular = store.get_task(task_id=_id("task", "market-day", str(window["companyCode"]), d1, "regular"), db_path=db_path)
            d1_late = store.get_task(task_id=_id("task", "market-day", str(window["companyCode"]), d1, "late-recovery"), db_path=db_path)
            if (d1_regular is None and d1_late is None) or any(task and task.status in {"queued", "running"} for task in (d1_regular, d1_late)):
                continue
        facts = _fact_revisions(company_code=str(window["companyCode"]), d1=d1, d2=d2, db_path=db_path)
        signature = ",".join(f"{item['tradeDate']}@{item['revision']}" for item in facts) or "missing"
        config_id = config.get("configId") if isinstance(config, Mapping) else None
        config_revision = config.get("revision") if isinstance(config, Mapping) else None
        task_id = _id("task", "evaluate-window", str(window["companyWindowId"]), stage, signature,
                      str(config_id), str(config_revision))
        existing = store.get_task(task_id=task_id, db_path=db_path)
        if existing is None:
            store.enqueue_task(task_id=task_id, kind="evaluate_company_window",
                               idempotency_key=f"evaluate-window:{window['companyWindowId']}:{stage}:{signature}:{config_id}:{config_revision}",
                               input_version=str(config.get("contentSha256")) if isinstance(config, Mapping) else "not_configured",
                               input_cutoff_at=cutoff.isoformat(),
                               payload={"companyWindowId": str(window["companyWindowId"]), "companyCode": str(window["companyCode"]),
                                        "d0TradeDate": str(window["d0TradeDate"]), "d1TradeDate": d1, "d2TradeDate": d2,
                                        "configId": config_id, "configRevision": config_revision,
                                        "evaluationStage": stage, "marketFactRevisions": facts},
                               budget={"maxAttempts": maximum}, created_at=now.isoformat(), db_path=db_path)
            evaluation_created += 1
        elif _retry_if_bounded(task_id, maximum=maximum, retry_interval=policy[1],
                               retry_deadline=cutoff + timedelta(minutes=policy[2]), now=now, db_path=db_path):
            evaluation_retried += 1
    return ScheduleResult(frozen, market_created, market_retried, evaluation_created, evaluation_retried)


__all__ = ["ScheduleResult", "maintain_evaluations"]
