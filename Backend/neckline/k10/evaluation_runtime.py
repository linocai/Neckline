"""Worker handler for K10-v1.4's fixed D1/D2 market observation."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping

from neckline.calendar.trading_calendar import CN_TZ, MARKET_CLOSE_TIME

from . import store
from .config import validate_run_config
from .evaluation import EvaluationInputError, evaluate_company_window, evaluation_state
from .market_observation import fetch_market_day_fact, record_market_day_fact
from .worker import TaskContext, TaskResult


def _window(payload: Mapping[str, Any], db_path: Any) -> Mapping[str, Any] | None:
    window_id = payload.get("companyWindowId")
    company_code = payload.get("companyCode")
    d1, d2 = payload.get("d1TradeDate"), payload.get("d2TradeDate")
    if not all(isinstance(value, str) and value for value in (window_id, company_code, d1, d2)):
        return None
    for item in store.list_company_windows(company_code=company_code, db_path=db_path):
        if item.get("companyWindowId") == window_id and item.get("d1TradeDate") == d1 and item.get("d2TradeDate") == d2:
            return item
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def evaluation_handler(context: TaskContext, *, clock: Callable[[], datetime] = _now) -> TaskResult:
    """Persist a revision from already-recorded source facts; never fetches or invents prices."""
    context.require_lease()
    config_id, config_revision = context.task.payload.get("configId"), context.task.payload.get("configRevision")
    config_row = store.read_run_config(config_id=config_id, revision=config_revision, db_path=context.db_path) if isinstance(config_id, str) and isinstance(config_revision, int) else None
    configuration = config_row.get("payload") if isinstance(config_row, Mapping) else None
    configured = validate_run_config(configuration, scope="evaluation")
    if not configured.ready:
        return TaskResult("not_configured", "configuration", error="两日评价参数未配置")
    window = _window(context.task.payload, context.db_path)
    if window is None:
        return TaskResult("not_configured", "configuration", error="评价任务缺少冻结公司观察窗口")
    frozen_refs = context.task.payload.get("marketFactRevisions")
    if frozen_refs is not None and not isinstance(frozen_refs, list):
        return TaskResult("failed", "input", error="冻结行情事实版本无效")
    if isinstance(frozen_refs, list):
        if any(not isinstance(item, Mapping) or item.get("companyCode") != window["companyCode"] or item.get("tradeDate") not in {window["d1TradeDate"], window["d2TradeDate"]} or not isinstance(item.get("revision"), int) or not isinstance(item.get("factId"), str) for item in frozen_refs):
            return TaskResult("failed", "input", error="冻结行情事实版本无效")
        all_facts = store.list_market_day_facts(company_code=window["companyCode"], db_path=context.db_path)
        facts = []
        for ref in frozen_refs:
            found = next((fact for fact in all_facts if fact.get("tradeDate") == ref["tradeDate"] and fact.get("revision") == ref["revision"] and fact.get("factId") == ref["factId"]), None)
            if found is None:
                return TaskResult("failed", "input", error="冻结行情事实版本不存在")
            facts.append(found)
    else:
        facts = store.latest_market_day_facts(company_code=window["companyCode"], trade_dates=[window["d1TradeDate"], window["d2TradeDate"]], db_path=context.db_path)
    try:
        completed_at = clock()
        if completed_at.tzinfo is None:
            raise EvaluationInputError("实际评价时间必须带时区")
        result = evaluate_company_window(window=window, market_facts=facts, as_of=completed_at.isoformat(), d2_close_at=window.get("d2CloseAt"))
    except EvaluationInputError:
        return TaskResult("failed", "input", error="固定观察窗口或行情事实无效")
    context.require_lease()
    fact_refs = list(result.fact_refs)
    revision = store.append_company_window_evaluation(
        company_window_id=result.company_window_id, state=evaluation_state(result), fact_refs=fact_refs,
        result=result.to_dict(), evaluated_at=completed_at.isoformat(), created_at=completed_at.isoformat(),
        db_path=context.db_path,
    )
    return TaskResult("completed", evaluation_state(result), {"companyWindowId": result.company_window_id, "revision": revision, "result": result.to_dict()})


def market_day_fact_handler(context: TaskContext, *, clock: Callable[[], datetime] = _now) -> TaskResult:
    """Fetch and persist exactly one payload-frozen company/date market fact."""
    context.require_lease()
    company_code, trade_date = context.task.payload.get("companyCode"), context.task.payload.get("tradeDate")
    if not isinstance(company_code, str) or not isinstance(trade_date, str):
        return TaskResult("not_configured", "configuration", error="行情任务缺少冻结公司代码或交易日")
    try:
        target = date.fromisoformat(trade_date)
        started = clock()
        if started.tzinfo is None:
            raise ValueError("行情取得时间必须带时区")
        started = started.astimezone(CN_TZ)
        if target > started.date() or (target == started.date() and started.timetz().replace(tzinfo=None) < MARKET_CLOSE_TIME):
            return TaskResult("failed", "market_not_closed", error="目标交易日尚未收盘，拒绝采集收盘行情")
        fact = fetch_market_day_fact(company_code=company_code, trade_date=trade_date)
        obtained = clock()
        if obtained.tzinfo is None:
            raise ValueError("行情完成时间必须带时区")
        obtained = obtained.astimezone(CN_TZ)
        fact["obtainedAt"] = obtained.isoformat()
        for ref in fact.get("sourceRefs", []):
            if isinstance(ref, dict): ref["obtainedAt"] = obtained.isoformat()
    except (TypeError, ValueError):
        return TaskResult("failed", "input", error="冻结行情输入无效")
    context.require_lease()
    revision = record_market_day_fact(fact=fact, db_path=context.db_path, created_at=obtained.isoformat())
    checkpoint = {"companyCode": company_code, "tradeDate": fact["tradeDate"], "revision": revision, "availability": fact["availability"]}
    if fact["availability"] != "suspended" and (
        fact["availability"] in {"data_gap", "anomaly"}
        or any(fact.get(key) is None for key in ("openPrice", "highPrice", "lowPrice", "closePrice", "limitUpPrice", "adjFactor"))
    ):
        return TaskResult("failed", "market_data_gap", checkpoint, "行情或涨停/复权资料缺失，等待有界补采")
    return TaskResult("completed", "market_fact_recorded", checkpoint)


__all__ = ["evaluation_handler", "market_day_fact_handler"]
