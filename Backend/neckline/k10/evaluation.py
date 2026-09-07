"""Deterministic, source-bound K10-v1.4 D1/D2 observation evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from neckline.calendar.trading_calendar import CN_TZ, MARKET_CLOSE_TIME

_AVAILABILITY = frozenset({"available", "suspended", "data_gap", "anomaly"})
_TICK = Decimal("0.01")


class EvaluationInputError(ValueError): pass


def _time(value: Any) -> datetime:
    try: parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc: raise EvaluationInputError("评价时间必须是带时区 ISO 时间") from exc
    if parsed.tzinfo is None: raise EvaluationInputError("评价时间必须带时区")
    return parsed


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0 or result.quantize(_TICK) != result:
            return None
    except (InvalidOperation, ValueError):
        return None
    return result


def _factor(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


def _float(value: Decimal | None) -> float | None: return float(value) if value is not None else None


def _select(facts: Sequence[Mapping[str, Any]], trade_date: str) -> Mapping[str, Any] | None:
    rows = [item for item in facts if isinstance(item, Mapping) and item.get("tradeDate") == trade_date]
    if not rows: return None
    revisions = [item.get("revision") for item in rows]
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in revisions):
        raise EvaluationInputError("行情事实 revision 无效")
    newest = max(revisions)
    same = [item for item in rows if item["revision"] == newest]
    if len(same) > 1 and any(dict(item) != dict(same[0]) for item in same[1:]):
        return {"tradeDate": trade_date, "availability": "anomaly", "sourceRefs": [], "anomaly": "conflicting_revisions"}
    return same[0]


def _field_audit(fact: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    metadata = fact.get("metadata")
    if not isinstance(metadata, Mapping):
        return [], None
    checks = metadata.get("fieldChecks")
    safe_checks = [dict(item) for item in checks if isinstance(item, Mapping)] if isinstance(checks, list) else []
    reason = metadata.get("anomalyReason")
    return safe_checks, str(reason) if isinstance(reason, str) and reason else None


def _day(fact: Mapping[str, Any] | None, trade_date: str) -> dict[str, Any]:
    if fact is None: return {"tradeDate": trade_date, "availability": "data_gap", "open":None,"high":None,"low":None,"close":None,"preClose":None,"limitUpPrice":None,"closeLimitUp":None,"touchedLimitUp":None,"sourceRefs":[],"fieldChecks":[],"anomalyReason":None}
    availability = fact.get("availability")
    if availability not in _AVAILABILITY: raise EvaluationInputError("行情事实 availability 无效")
    refs = [dict(x) for x in fact.get("sourceRefs", []) if isinstance(x, Mapping)]
    checks, anomaly_reason = _field_audit(fact)
    if availability == "available" and any(check.get("state") == "conflict" for check in checks):
        availability, anomaly_reason = "anomaly", anomaly_reason or "cross_source_conflict"
    out = {"tradeDate": trade_date, "availability": availability, "sourceRefs": refs, "revision": fact.get("revision"), "factId": fact.get("factId"), "obtainedAt": fact.get("obtainedAt"), "fieldChecks": checks, "anomalyReason": anomaly_reason}
    if availability != "available":
        return {**out, "open":None,"high":None,"low":None,"close":None,"preClose":None,"limitUpPrice":None,"closeLimitUp":None,"touchedLimitUp":None}
    op, hi, lo, close, pre, limit = (_decimal(fact.get(key)) for key in ("open","high","low","close","preClose","limitUpPrice"))
    if (None in {op,hi,lo,close} or lo > min(op, close) or hi < max(op, close)
            or (fact.get("limitUpPrice") is not None and limit is None)
            or (limit is not None and hi > limit)):
        # Raw prices remain in the source fact revision. Invalid data must never
        # flow into gap/price-change calculations as if it were verified.
        return {**out, "availability":"anomaly", "anomaly":"invalid_ohlc_or_limit", "anomalyReason": anomaly_reason or "invalid_ohlc_or_limit", "open":None,"high":None,"low":None,"close":None,"preClose":None,"limitUpPrice":None,"closeLimitUp":None,"touchedLimitUp":None}
    return {**out, "open":_float(op),"high":_float(hi),"low":_float(lo),"close":_float(close),"preClose":_float(pre),"limitUpPrice":_float(limit),
            "closeLimitUp": close == limit if limit is not None else None, "touchedLimitUp": hi >= limit if limit is not None else None,
            "adjFactor": _float(_factor(fact.get("adjFactor"))), "metadata": dict(fact.get("metadata") or {}) if isinstance(fact.get("metadata"), Mapping) else {}}


def _change(value: float | None, base: float | None) -> float | None:
    if value is None or base is None or base <= 0: return None
    return float((Decimal(str(value)) / Decimal(str(base))) - 1)


@dataclass(frozen=True)
class CompanyWindowEvaluation:
    company_window_id: str
    company_code: str
    sample_class: str
    selection: Mapping[str, Any] | None
    d1: Mapping[str, Any]
    d2: Mapping[str, Any]
    primary_eligible: bool
    close_limit_hit_any: bool | None
    first_touch_day: str | None
    first_touch_status: str
    known_touch_days: tuple[str, ...]
    d1_open_gap: float | None
    d1_price_changes: Mapping[str, float | None]
    d2_price_changes: Mapping[str, float | None]
    window_price_changes: Mapping[str, float | None]
    comparability: str
    gaps: tuple[Mapping[str, str], ...]
    fact_refs: tuple[Mapping[str, Any], ...]
    due: bool
    def to_dict(self) -> dict[str, Any]:
        return {
            "companyWindowId": self.company_window_id, "companyCode": self.company_code,
            "sampleClass": self.sample_class, "selection": dict(self.selection) if self.selection else None,
            "d1": dict(self.d1), "d2": dict(self.d2), "primaryEligible": self.primary_eligible,
            "closeLimitHitAny": self.close_limit_hit_any, "firstTouchDay": self.first_touch_day,
            "firstTouchStatus": self.first_touch_status, "knownTouchDays": list(self.known_touch_days),
            "d1OpenGap": self.d1_open_gap, "d1PriceChanges": dict(self.d1_price_changes),
            "d2PriceChanges": dict(self.d2_price_changes), "windowPriceChanges": dict(self.window_price_changes),
            "comparability": self.comparability, "gaps": [dict(item) for item in self.gaps],
            "factRefs": [dict(item) for item in self.fact_refs], "due": self.due,
        }


def _session_closed(trade_date: str, now: datetime) -> bool:
    try:
        day = datetime.strptime(trade_date.replace("-", ""), "%Y%m%d").date()
    except ValueError as exc:
        raise EvaluationInputError("固定观察交易日无效") from exc
    return now >= datetime.combine(day, MARKET_CLOSE_TIME, tzinfo=CN_TZ)


def evaluate_company_window(*, window: Mapping[str,Any], market_facts: Sequence[Mapping[str,Any]], as_of: str, d2_close_at: str | None = None) -> CompanyWindowEvaluation:
    required=("companyWindowId","companyCode","d1TradeDate","d2TradeDate","sampleClass")
    if any(not isinstance(window.get(x),str) or not window[x] for x in required) or window["sampleClass"] not in {"primary","overlap"}: raise EvaluationInputError("公司观察窗口不完整")
    now=_time(as_of); close_at=_time(d2_close_at or window.get("d2CloseAt")); due=now>=close_at
    if any(fact.get("companyCode", window["companyCode"]) != window["companyCode"] for fact in market_facts):
        raise EvaluationInputError("行情事实与公司窗口不匹配")
    # Never reveal a future close even if a bad import put that row in storage.
    d1, d2 = (
        _day(_select(market_facts, window[key]) if _session_closed(window[key], now) else None, window[key])
        for key in ("d1TradeDate", "d2TradeDate")
    )
    for day in (d1, d2):
        day["marketClosed"] = _session_closed(day["tradeDate"], now)
    gaps=[]
    for label,day in (("D1",d1),("D2",d2)):
        if not day["marketClosed"]:
            continue  # A future session is not a failed or missing data fetch.
        if day["availability"]!="available": gaps.append({"day":label,"reason":str(day["availability"])})
        elif day["closeLimitUp"] is None or day["touchedLimitUp"] is None: gaps.append({"day":label,"reason":"limit_data_unavailable"})
    known_hits=[day["closeLimitUp"] for day in (d1,d2) if day["closeLimitUp"] is not None]
    close_hit=True if True in known_hits else (False if due and not gaps else None)
    known_touch=tuple(label for label,day in (("D1",d1),("D2",d2)) if day["touchedLimitUp"] is True)
    if d1["touchedLimitUp"] is True: first,status="D1","confirmed"
    elif d1["touchedLimitUp"] is False and d2["touchedLimitUp"] is True: first,status="D2","confirmed"
    elif d2["touchedLimitUp"] is True: first,status=None,"unknown_due_to_d1"
    else: first,status=None,"not_touched" if d1["touchedLimitUp"] is False and d2["touchedLimitUp"] is False else "unknown"
    d1_changes={"high":_change(d1["high"],d1["open"]),"low":_change(d1["low"],d1["open"]),"close":_change(d1["close"],d1["open"])}
    factors_known = d1.get("adjFactor") is not None and d2.get("adjFactor") is not None
    cross_evidence = d2.get("metadata",{}).get("corporateAction") == "ex_right"
    if factors_known:
        base = d1["open"] * d1["adjFactor"]
        d2_changes={key:_change(d2[key] * d2["adjFactor"] if d2.get(key) is not None else None, base) for key in ("high","low","close")}
        comparability="adjusted_comparable" if d1["adjFactor"] != d2["adjFactor"] else "raw_comparable"
    elif cross_evidence:
        d2_changes={"high":None,"low":None,"close":None}; comparability="not_comparable"
    else:
        d2_changes={"high":None,"low":None,"close":None}; comparability="unknown"
    # The daily changes already share D1's opening reference and adjustment
    # basis. Comparing raw D2 prices here would mix incompatible price bases.
    window_changes={
        "high": max(d1_changes["high"], d2_changes["high"])
        if d1_changes["high"] is not None and d2_changes["high"] is not None else None,
        "low": min(d1_changes["low"], d2_changes["low"])
        if d1_changes["low"] is not None and d2_changes["low"] is not None else None,
        "close": d2_changes["close"],
    }
    refs=[]
    for day in (d1,d2):
        if isinstance(day.get("revision"),int) and isinstance(day.get("factId"), str): refs.append({"factId":day["factId"],"companyCode":window["companyCode"],"tradeDate":day["tradeDate"],"revision":day["revision"]})
    return CompanyWindowEvaluation(
        company_window_id=window["companyWindowId"], company_code=window["companyCode"],
        sample_class=window["sampleClass"],
        selection=dict(window["selection"]) if isinstance(window.get("selection"), Mapping) else None,
        d1=d1, d2=d2, primary_eligible=due and window["sampleClass"] == "primary" and not gaps,
        close_limit_hit_any=close_hit, first_touch_day=first, first_touch_status=status,
        known_touch_days=known_touch, d1_open_gap=_change(d1["open"], d1["preClose"]),
        d1_price_changes=d1_changes, d2_price_changes=d2_changes, window_price_changes=window_changes,
        comparability=comparability, gaps=tuple(gaps), fact_refs=tuple(refs), due=due,
    )


def evaluation_state(result: CompanyWindowEvaluation) -> str:
    return "pending" if not result.due else ("completed" if not result.gaps else "incomplete")

__all__=["CompanyWindowEvaluation","EvaluationInputError","evaluate_company_window","evaluation_state"]
