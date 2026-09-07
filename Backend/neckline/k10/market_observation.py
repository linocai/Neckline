"""Source-bound K10 D1/D2 market fact collection.

The collector intentionally queries only the frozen company/date pair.  It
does not infer a percentage limit from a board or turn a missing limit record
into a negative hit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from neckline.data.tushare_client import TushareResult, to_ts_code, ts_adj_factor, ts_daily, ts_stk_limit, ts_suspend_d_all

from . import store


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rows(result: TushareResult | None) -> list[Mapping[str, Any]]:
    if result is None or not result.ok or result.data is None:
        return []
    data = result.data
    try:
        values = data.to_dict("records")
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
    return next((item for item in rows if str(item.get("ts_code", "")).upper() == normalized_code and _date_text(str(item.get("trade_date", ""))) == normalized_day), None)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    candidate = float(value)
    return candidate if candidate == candidate and candidate not in {float("inf"), float("-inf")} else None


def fetch_market_day_fact(
    *, company_code: str, trade_date: str,
    daily_fetcher: Callable[[str, str, str], TushareResult] = ts_daily,
    adj_factor_fetcher: Callable[[str, str, str], TushareResult] = ts_adj_factor,
    limit_fetcher: Callable[[str, str], TushareResult] = ts_stk_limit,
    suspend_fetcher: Callable[[str], TushareResult] = ts_suspend_d_all,
    obtained_at: str | None = None,
) -> dict[str, Any]:
    """Fetch one exchange session and yield an appendable fact payload.

    ``daily`` failure is a source gap.  An empty daily response is called
    suspended only when the independently fetched suspension list confirms the
    same code; otherwise it remains a gap.  A missing ``stk_limit`` leaves the
    limit fields ``None`` while retaining available OHLC facts.
    """
    if not isinstance(company_code, str) or not company_code.strip() or not isinstance(trade_date, str) or not trade_date.strip():
        raise ValueError("公司代码和交易日必须明确")
    day, canonical_day = _date_text(trade_date), _canonical_date(trade_date)
    daily_result = daily_fetcher(company_code, day, day)
    daily = _row(_rows(daily_result), code=company_code, trade_date=day)
    source_refs: list[dict[str, Any]] = [{"source": "tushare.daily", "companyCode": to_ts_code(company_code), "tradeDate": day, "obtainedAt": obtained_at or _utc_now()}]
    if daily is None:
        suspend_result = suspend_fetcher(day)
        suspension = _row(_rows(suspend_result), code=company_code, trade_date=day)
        # A resumption or an intraday halt cannot prove that a missing daily bar
        # is an entire suspended session.
        suspended = (suspension is not None and suspension.get("suspend_type") == "S"
                     and suspension.get("suspend_timing") in (None, ""))
        source_refs.append({"source": "tushare.suspend_d", "companyCode": to_ts_code(company_code), "tradeDate": day, "obtainedAt": obtained_at or _utc_now()})
        return {"companyCode": company_code, "tradeDate": canonical_day, "availability": "suspended" if suspended else "data_gap",
                "openPrice": None, "highPrice": None, "lowPrice": None, "closePrice": None, "preClose": None,
                "limitUpPrice": None, "closeLimitUp": None, "touchedLimitUp": None, "sourceRefs": source_refs,
                "obtainedAt": obtained_at or _utc_now()}
    limit_result = limit_fetcher(company_code, day)
    adj_result = adj_factor_fetcher(company_code, day, day)
    limit = _row(_rows(limit_result), code=company_code, trade_date=day)
    limit_up = _number(limit.get("up_limit")) if limit is not None else None
    adj = _row(_rows(adj_result), code=company_code, trade_date=day)
    close, high = _number(daily.get("close")), _number(daily.get("high"))
    source_refs.append({"source": "tushare.stk_limit", "companyCode": to_ts_code(company_code), "tradeDate": day, "obtainedAt": obtained_at or _utc_now()})
    source_refs.append({"source": "tushare.adj_factor", "companyCode": to_ts_code(company_code), "tradeDate": day, "obtainedAt": obtained_at or _utc_now()})
    return {"companyCode": company_code, "tradeDate": canonical_day, "availability": "available",
            "openPrice": _number(daily.get("open")), "highPrice": high, "lowPrice": _number(daily.get("low")),
            "closePrice": close, "preClose": _number(daily.get("pre_close")), "limitUpPrice": limit_up,
            "adjFactor": _number(adj.get("adj_factor")) if adj is not None else None, "metadata": {},
            "closeLimitUp": close == limit_up if close is not None and limit_up is not None else None,
            "touchedLimitUp": high >= limit_up if high is not None and limit_up is not None else None,
            "sourceRefs": source_refs, "obtainedAt": obtained_at or _utc_now()}


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
