"""Source-bound K10 D1/D2 market fact collection and audit.

TuShare remains the immutable EOD source. A same-day, post-close Sina/Tencent
pair can corroborate its OHLC/pre-close fields, but real-time quotes are never
used to fill historical bars or to choose a winner when sources disagree.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from neckline.calendar.trading_calendar import CN_TZ, MARKET_CLOSE_TIME
from neckline.data.realtime import DualQuote, Quote
from neckline.data.tushare_client import TushareResult, to_ts_code, ts_adj_factor, ts_daily, ts_stk_limit, ts_suspend_d_all

from . import store

_TICK = Decimal("0.01")
_PRICE_FIELDS = (("open", "open"), ("high", "high"), ("low", "low"),
                 ("close", "close"), ("preClose", "pre_close"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rows(result: TushareResult | None) -> list[Mapping[str, Any]]:
    if result is None or not result.ok or result.data is None:
        return []
    try:
        values = result.data.to_dict("records")
    except (AttributeError, TypeError, ValueError):
        return []
    return [row for row in values if isinstance(row, Mapping)]


def _date_text(value: str) -> str:
    return value.replace("-", "")


def _canonical_date(value: str) -> str:
    compact = _date_text(value)
    try:
        return datetime.strptime(compact, "%Y%m%d").date().isoformat()
    except ValueError:
        return value


def _row(rows: list[Mapping[str, Any]], *, code: str, trade_date: str) -> Mapping[str, Any] | None:
    normalized_code, normalized_day = to_ts_code(code), _date_text(trade_date)
    return next((item for item in rows if str(item.get("ts_code", "")).upper() == normalized_code
                 and _date_text(str(item.get("trade_date", ""))) == normalized_day), None)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    candidate = float(value)
    return candidate if candidate == candidate and candidate not in {float("inf"), float("-inf")} else None


def _price(value: Any) -> float | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    try:
        decimal = Decimal(str(number))
    except InvalidOperation:
        return None
    return number if decimal.quantize(_TICK) == decimal else None


def _digits(value: Any) -> str:
    return "".join(char for char in str(value) if char.isdigit())


def _quote_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=CN_TZ)
        except ValueError:
            pass
    return None


def _quote_observation(quote: Quote | None, *, company_code: str, trade_date: str,
                       expected_source: str) -> tuple[datetime | None, str | None]:
    if quote is None:
        return None, "source_unavailable"
    if quote.source != expected_source:
        return None, "source_identity_invalid"
    if _digits(quote.code) != _digits(company_code):
        return None, "company_code_mismatch"
    observed = _quote_time(quote.ts)
    if observed is None:
        return None, "source_timestamp_unparseable"
    if observed.date().isoformat() != _canonical_date(trade_date):
        return None, "source_trade_date_mismatch"
    if observed.timetz().replace(tzinfo=None) < MARKET_CLOSE_TIME:
        return None, "source_timestamp_before_shanghai_close"
    return observed, None


def _quote_value(quote: Quote, field: str) -> float | None:
    # ``Quote.price`` intentionally falls back to pre_close. Settlement
    # corroboration must use only the direct traded price.
    attr = "pre_close" if field == "preClose" else field
    value = quote.traded_price if field == "close" else getattr(quote, attr, None)
    return _price(value)


def _source_value(source: str, value: Any, observed_at: str | None) -> dict[str, Any]:
    return {"source": source, "value": value, "observedAt": observed_at}


def _same_tick(values: list[float]) -> bool:
    baseline = Decimal(str(values[0])).quantize(_TICK)
    return all(Decimal(str(value)).quantize(_TICK) == baseline for value in values[1:])


def _quote_audit(*, company_code: str, trade_date: str, dual: DualQuote | None,
                 obtained_at: str) -> tuple[dict[str, tuple[Quote, datetime]], list[dict[str, Any]], list[dict[str, Any]]]:
    dual = dual if isinstance(dual, DualQuote) else None
    valid: dict[str, tuple[Quote, datetime]] = {}
    refs: list[dict[str, Any]] = []
    reasons: list[dict[str, Any]] = []
    for source, quote in (("sina", dual.primary if dual else None), ("tencent", dual.backup if dual else None)):
        observed, reason = _quote_observation(quote, company_code=company_code, trade_date=trade_date,
                                              expected_source=source)
        ref = {"source": f"realtime.{source}", "companyCode": to_ts_code(company_code),
               "tradeDate": _date_text(trade_date), "obtainedAt": obtained_at}
        if observed is not None and quote is not None:
            valid[source] = (quote, observed)
            ref["observedAt"] = observed.isoformat()
            ref["status"] = "same_day_post_close"
        else:
            ref["status"] = "not_comparable"
            ref["reason"] = reason
            if quote is not None:
                ref["observedAt"] = str(quote.ts or "")
            reasons.append({"source": source, "reason": reason})
        refs.append(ref)
    return valid, refs, reasons


def _field_check(*, field: str, tushare_value: Any, tushare_observed_at: str,
                 quotes: Mapping[str, tuple[Quote, datetime]], unavailable_reason: str | None = None) -> dict[str, Any]:
    daily = _price(tushare_value)
    values: list[dict[str, Any]] = []
    numeric: list[float] = []
    if daily is not None:
        values.append(_source_value("tushare.daily", daily, tushare_observed_at))
        numeric.append(daily)
    missing: list[str] = []
    for source in ("sina", "tencent"):
        quote_observed = quotes.get(source)
        if quote_observed is None:
            missing.append(f"realtime.{source}_not_comparable")
            continue
        quote, observed = quote_observed
        value = _quote_value(quote, field)
        if value is None:
            missing.append(f"realtime.{source}_{'no_traded_price' if field == 'close' else 'field_unavailable'}")
            continue
        values.append(_source_value(f"realtime.{source}", value, observed.isoformat()))
        numeric.append(value)
    if daily is None:
        return {"field": field, "state": "unavailable", "reason": unavailable_reason or "tushare_daily_field_unavailable", "sourceValues": values}
    realtime_values = [item for item in values if item["source"].startswith("realtime.")]
    if len(realtime_values) == 2:
        agreeing = _same_tick(numeric)
        return {"field": field, "state": "verified" if agreeing else "conflict",
                "reason": "same_day_post_close_sources_agree" if agreeing else "same_day_post_close_sources_disagree",
                "sourceValues": values}
    reason = unavailable_reason or (";".join(missing) if missing else "second_realtime_source_unavailable")
    return {"field": field, "state": "single_source", "reason": reason, "sourceValues": values}


def _non_price_check(field: str, value: float | None, *, source: str, observed_at: str,
                     reason: str) -> dict[str, Any]:
    return {"field": field, "state": "single_source" if value is not None else "unavailable",
            "reason": reason if value is not None else f"{source}_field_unavailable",
            "sourceValues": [] if value is None else [_source_value(source, value, observed_at)]}


def _derived_limit_check(field: str, value: bool | None, *, source_values: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    return {"field": field, "state": "single_source" if value is not None else "unavailable",
            "reason": reason if value is not None else "price_or_limit_reference_unavailable",
            "sourceValues": source_values}


def fetch_market_day_fact(
    *, company_code: str, trade_date: str,
    daily_fetcher: Callable[[str, str, str], TushareResult] = ts_daily,
    adj_factor_fetcher: Callable[[str, str, str], TushareResult] = ts_adj_factor,
    limit_fetcher: Callable[[str, str], TushareResult] = ts_stk_limit,
    suspend_fetcher: Callable[[str], TushareResult] = ts_suspend_d_all,
    quote_fetcher: Callable[[list[str]], Mapping[str, DualQuote]] | None = None,
    obtained_at: str | None = None,
) -> dict[str, Any]:
    """Fetch a frozen company/day fact without a hidden real-time fallback.

    ``quote_fetcher`` is deliberately opt-in. Production injects
    ``get_quotes_dual`` at the worker boundary; offline callers using fake
    TuShare responses therefore never make an accidental network call.
    """
    if not isinstance(company_code, str) or not company_code.strip() or not isinstance(trade_date, str) or not trade_date.strip():
        raise ValueError("公司代码和交易日必须明确")
    day, canonical_day, observed_at = _date_text(trade_date), _canonical_date(trade_date), obtained_at or _utc_now()
    daily_result = daily_fetcher(company_code, day, day)
    daily = _row(_rows(daily_result), code=company_code, trade_date=day)
    source_refs: list[dict[str, Any]] = [{"source": "tushare.daily", "companyCode": to_ts_code(company_code), "tradeDate": day, "obtainedAt": observed_at}]
    dual: DualQuote | None = None
    quote_reason: str | None = None
    if quote_fetcher is not None:
        quote_rows = quote_fetcher([company_code])
        dual = quote_rows.get(company_code) if isinstance(quote_rows, Mapping) else None
    else:
        quote_reason = "realtime_dual_verification_not_requested"
    quotes, quote_refs, quote_reasons = _quote_audit(company_code=company_code, trade_date=day, dual=dual, obtained_at=observed_at)
    source_refs.extend(quote_refs)
    if quote_reason is None and quote_reasons:
        quote_reason = ";".join(f"{item['source']}:{item['reason']}" for item in quote_reasons)

    if daily is None:
        suspend_result = suspend_fetcher(day)
        suspension = _row(_rows(suspend_result), code=company_code, trade_date=day)
        suspended = (suspension is not None and suspension.get("suspend_type") == "S"
                     and suspension.get("suspend_timing") in (None, ""))
        source_refs.append({"source": "tushare.suspend_d", "companyCode": to_ts_code(company_code), "tradeDate": day, "obtainedAt": observed_at})
        checks = [{"field": field, "state": "unavailable", "reason": "tushare_daily_row_unavailable", "sourceValues": []}
                  for field, _ in _PRICE_FIELDS]
        checks.append({"field": "suspension", "state": "single_source" if suspended else "unavailable",
                       "reason": "tushare_suspend_confirmed_without_independent_quote_proof" if suspended else "suspension_not_confirmed",
                       "sourceValues": [_source_value("tushare.suspend_d", True, observed_at)] if suspended else []})
        checks.extend([_non_price_check("limitUpPrice", None, source="tushare.stk_limit", observed_at=observed_at,
                                        reason="not_applicable_without_daily_bar"),
                       _non_price_check("adjFactor", None, source="tushare.adj_factor", observed_at=observed_at,
                                        reason="not_applicable_without_daily_bar")])
        return {"companyCode": company_code, "tradeDate": canonical_day,
                "availability": "suspended" if suspended else "data_gap",
                "openPrice": None, "highPrice": None, "lowPrice": None, "closePrice": None, "preClose": None,
                "limitUpPrice": None, "closeLimitUp": None, "touchedLimitUp": None,
                "metadata": {"fieldChecks": checks, "anomalyReason": None},
                "sourceRefs": source_refs, "obtainedAt": observed_at}

    limit_result, adj_result = limit_fetcher(company_code, day), adj_factor_fetcher(company_code, day, day)
    limit, adj = _row(_rows(limit_result), code=company_code, trade_date=day), _row(_rows(adj_result), code=company_code, trade_date=day)
    limit_up, adj_factor = _price(limit.get("up_limit")) if limit is not None else None, _number(adj.get("adj_factor")) if adj is not None else None
    source_refs.extend([
        {"source": "tushare.stk_limit", "companyCode": to_ts_code(company_code), "tradeDate": day, "obtainedAt": observed_at},
        {"source": "tushare.adj_factor", "companyCode": to_ts_code(company_code), "tradeDate": day, "obtainedAt": observed_at},
    ])
    checks = [_field_check(field=field, tushare_value=daily.get(daily_key), tushare_observed_at=observed_at,
                           quotes=quotes, unavailable_reason=quote_reason)
              for field, daily_key in _PRICE_FIELDS]
    checks.extend([
        _non_price_check("limitUpPrice", limit_up, source="tushare.stk_limit", observed_at=observed_at,
                         reason="exchange_limit_has_no_independent_realtime_equivalent"),
        _non_price_check("adjFactor", adj_factor, source="tushare.adj_factor", observed_at=observed_at,
                         reason="adjustment_factor_has_no_independent_realtime_equivalent"),
    ])
    check_by_field = {item["field"]: item for item in checks}
    close, high = _price(daily.get("close")), _price(daily.get("high"))
    close_ok = close is not None and limit_up is not None and check_by_field["close"]["state"] != "conflict"
    high_ok = high is not None and limit_up is not None and check_by_field["high"]["state"] != "conflict"
    close_limit_up = close == limit_up if close_ok else None
    touched_limit_up = high >= limit_up if high_ok else None
    checks.extend([
        _derived_limit_check("closeLimitUp", close_limit_up,
                             source_values=[_source_value("tushare.daily", close, observed_at), _source_value("tushare.stk_limit", limit_up, observed_at)] if close_ok else [],
                             reason="derived_from_tushare_daily_close_and_exchange_limit"),
        _derived_limit_check("touchedLimitUp", touched_limit_up,
                             source_values=[_source_value("tushare.daily", high, observed_at), _source_value("tushare.stk_limit", limit_up, observed_at)] if high_ok else [],
                             reason="derived_from_tushare_daily_high_and_exchange_limit"),
    ])
    conflicts = [item["field"] for item in checks if item["state"] == "conflict"]
    anomaly_reason = None if not conflicts else "cross_source_conflict:" + ",".join(conflicts)
    return {"companyCode": company_code, "tradeDate": canonical_day,
            "availability": "anomaly" if conflicts else "available",
            "openPrice": _price(daily.get("open")), "highPrice": high, "lowPrice": _price(daily.get("low")),
            "closePrice": close, "preClose": _price(daily.get("pre_close")), "limitUpPrice": limit_up,
            "adjFactor": adj_factor, "closeLimitUp": close_limit_up, "touchedLimitUp": touched_limit_up,
            "metadata": {"fieldChecks": checks, "anomalyReason": anomaly_reason},
            "sourceRefs": source_refs, "obtainedAt": observed_at}


def record_market_day_fact(*, fact: Mapping[str, Any], db_path: Any, created_at: str | None = None) -> int:
    """Append an immutable fact revision without any local-data fallback."""
    return store.append_market_day_fact(
        company_code=str(fact["companyCode"]), trade_date=str(fact["tradeDate"]), availability=str(fact["availability"]),
        open_price=fact.get("openPrice"), high_price=fact.get("highPrice"), low_price=fact.get("lowPrice"),
        close_price=fact.get("closePrice"), pre_close=fact.get("preClose"), limit_up_price=fact.get("limitUpPrice"),
        close_limit_up=fact.get("closeLimitUp"), touched_limit_up=fact.get("touchedLimitUp"),
        adj_factor=fact.get("adjFactor"), metadata=fact.get("metadata") or {},
        source_refs=fact.get("sourceRefs") or (), obtained_at=str(fact["obtainedAt"]),
        created_at=created_at or _utc_now(), db_path=db_path,
    )


__all__ = ["fetch_market_day_fact", "record_market_day_fact"]
