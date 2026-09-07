from __future__ import annotations

import pandas as pd

from neckline.data.realtime import DualQuote, Quote
from neckline.data.tushare_client import TushareResult
from neckline.k10 import store
from neckline.k10.market_observation import fetch_market_day_fact, record_market_day_fact
from neckline.k10.schema import initialize_schema


def _result(rows):
    return TushareResult.success(pd.DataFrame(rows))


def _quote(source, *, code="300001", ts="2026-09-08 15:01:00", open=10.2, high=11.0,
           low=10.0, close=11.0, pre_close=10.0, traded_price=11.0):
    return Quote(code=code, name="样例", price=close, pre_close=pre_close, open=open, high=high,
                 low=low, volume=100.0, amount=100_000.0, ts=ts, source=source,
                 traded_price=traded_price)


def _daily_fact(*, quote_fetcher=None):
    return fetch_market_day_fact(
        company_code="300001.SZ", trade_date="2026-09-08", obtained_at="2026-09-08T16:00:00+08:00",
        daily_fetcher=lambda *_: _result([{"ts_code": "300001.SZ", "trade_date": "20260908", "open": 10.2, "high": 11.0, "low": 10.0, "close": 11.0, "pre_close": 10.0}]),
        limit_fetcher=lambda *_: _result([{"ts_code": "300001.SZ", "trade_date": "20260908", "up_limit": 11.0}]),
        adj_factor_fetcher=lambda *_: _result([{"ts_code": "300001.SZ", "trade_date": "20260908", "adj_factor": 1.0}]),
        suspend_fetcher=lambda *_: _result([]), quote_fetcher=quote_fetcher,
    )


def test_collector_binds_exchange_limit_to_one_company_and_day():
    fact = fetch_market_day_fact(
        company_code="300001.SZ", trade_date="2026-09-08", obtained_at="2026-09-08T16:00:00+08:00",
        daily_fetcher=lambda *_: _result([{"ts_code": "300001.SZ", "trade_date": "20260908", "open": 10.2, "high": 11.0, "low": 10.0, "close": 11.0, "pre_close": 10.0}]),
        limit_fetcher=lambda *_: _result([{"ts_code": "300001.SZ", "trade_date": "20260908", "up_limit": 11.0}]),
        adj_factor_fetcher=lambda *_: _result([{"ts_code": "300001.SZ", "trade_date": "20260908", "adj_factor": 1.0}]),
        suspend_fetcher=lambda *_: _result([]),
    )
    assert fact["availability"] == "available"
    assert fact["tradeDate"] == "2026-09-08"
    assert fact["closeLimitUp"] is True and fact["touchedLimitUp"] is True
    assert fact["limitUpPrice"] == 11.0


def test_missing_limit_row_is_unknown_not_a_false_non_hit():
    fact = fetch_market_day_fact(
        company_code="300001.SZ", trade_date="20260908", obtained_at="2026-09-08T16:00:00+08:00",
        daily_fetcher=lambda *_: _result([{"ts_code": "300001.SZ", "trade_date": "20260908", "open": 10.2, "high": 11.0, "low": 10.0, "close": 11.0, "pre_close": 10.0}]),
        limit_fetcher=lambda *_: _result([]), adj_factor_fetcher=lambda *_: _result([]), suspend_fetcher=lambda *_: _result([]),
    )
    assert fact["availability"] == "available"
    assert fact["limitUpPrice"] is None
    assert fact["closeLimitUp"] is None and fact["touchedLimitUp"] is None


def test_empty_daily_is_suspended_only_with_independent_suspend_evidence():
    fact = fetch_market_day_fact(
        company_code="300001.SZ", trade_date="20260908", obtained_at="2026-09-08T16:00:00+08:00",
        daily_fetcher=lambda *_: _result([]), limit_fetcher=lambda *_: _result([]), adj_factor_fetcher=lambda *_: _result([]),
        suspend_fetcher=lambda *_: _result([{"ts_code": "300001.SZ", "trade_date": "20260908", "suspend_type": "S", "suspend_timing": None}]),
    )
    assert fact["availability"] == "suspended"


def test_intraday_halt_or_resumption_does_not_explain_a_missing_daily_bar():
    for kind, timing in (("S", "09:30-10:00"), ("R", None)):
        fact = fetch_market_day_fact(
            company_code="300001.SZ", trade_date="20260908", obtained_at="2026-09-08T16:00:00+08:00",
            daily_fetcher=lambda *_: _result([]), limit_fetcher=lambda *_: _result([]),
            adj_factor_fetcher=lambda *_: _result([]),
            suspend_fetcher=lambda *_: _result([{"ts_code": "300001.SZ", "trade_date": "20260908",
                                               "suspend_type": kind, "suspend_timing": timing}]),
        )
        assert fact["availability"] == "data_gap"


def test_same_day_post_close_dual_quotes_verify_all_comparable_ohlc_fields():
    fact = _daily_fact(quote_fetcher=lambda _: {
        "300001.SZ": DualQuote("300001.SZ", _quote("sina"), _quote("tencent")),
    })
    checks = {item["field"]: item for item in fact["metadata"]["fieldChecks"]}
    assert fact["availability"] == "available"
    assert all(checks[field]["state"] == "verified" for field in ("open", "high", "low", "close", "preClose"))
    assert checks["close"]["sourceValues"] == [
        {"source": "tushare.daily", "value": 11.0, "observedAt": "2026-09-08T16:00:00+08:00"},
        {"source": "realtime.sina", "value": 11.0, "observedAt": "2026-09-08T15:01:00+08:00"},
        {"source": "realtime.tencent", "value": 11.0, "observedAt": "2026-09-08T15:01:00+08:00"},
    ]


def test_quote_conflict_preserves_both_values_marks_anomaly_and_does_not_derive_bad_limit_status():
    fact = _daily_fact(quote_fetcher=lambda _: {
        "300001.SZ": DualQuote("300001.SZ", _quote("sina"), _quote("tencent", high=10.8)),
    })
    checks = {item["field"]: item for item in fact["metadata"]["fieldChecks"]}
    assert fact["availability"] == "anomaly"
    assert fact["metadata"]["anomalyReason"] == "cross_source_conflict:high"
    assert checks["high"]["state"] == "conflict"
    assert [item["value"] for item in checks["high"]["sourceValues"]] == [11.0, 11.0, 10.8]
    assert fact["touchedLimitUp"] is None


def test_intraday_wrong_day_and_code_mismatch_quotes_remain_explicit_single_source_fallbacks():
    cases = [
        DualQuote("300001.SZ", _quote("sina", ts="2026-09-08 14:59:59"), _quote("tencent", ts="2026-09-08 14:59:59")),
        DualQuote("300001.SZ", _quote("sina", ts="2026-09-09 15:01:00"), _quote("tencent", ts="2026-09-09 15:01:00")),
        DualQuote("300001.SZ", _quote("sina", code="300002"), _quote("tencent", code="300002")),
    ]
    for dual in cases:
        fact = _daily_fact(quote_fetcher=lambda _, item=dual: {"300001.SZ": item})
        close = next(item for item in fact["metadata"]["fieldChecks"] if item["field"] == "close")
        assert fact["availability"] == "available"
        assert close["state"] == "single_source"
        assert "realtime.sina" in close["reason"] or "sina:" in close["reason"]


def test_historical_quote_and_quote_price_fallback_cannot_verify_settlement_close():
    historical = _daily_fact(quote_fetcher=lambda _: {
        "300001.SZ": DualQuote("300001.SZ", _quote("sina", ts="2026-09-09 15:01:00"), _quote("tencent", ts="2026-09-09 15:01:00")),
    })
    no_trade = _daily_fact(quote_fetcher=lambda _: {
        "300001.SZ": DualQuote("300001.SZ", _quote("sina", close=10.0, traded_price=None), _quote("tencent", close=10.0, traded_price=None)),
    })
    old_close = next(item for item in historical["metadata"]["fieldChecks"] if item["field"] == "close")
    no_trade_close = next(item for item in no_trade["metadata"]["fieldChecks"] if item["field"] == "close")
    assert old_close["state"] == "single_source" and "trade_date_mismatch" in old_close["reason"]
    assert no_trade_close["state"] == "single_source" and "no_traded_price" in no_trade_close["reason"]
    assert no_trade_close["sourceValues"] == [{"source": "tushare.daily", "value": 11.0, "observedAt": "2026-09-08T16:00:00+08:00"}]


def test_confirmed_suspension_and_exchange_limit_are_audited_without_fabricated_independent_proof():
    fact = fetch_market_day_fact(
        company_code="300001.SZ", trade_date="2026-09-08", obtained_at="2026-09-08T16:00:00+08:00",
        daily_fetcher=lambda *_: _result([]), limit_fetcher=lambda *_: _result([]), adj_factor_fetcher=lambda *_: _result([]),
        suspend_fetcher=lambda *_: _result([{"ts_code": "300001.SZ", "trade_date": "20260908", "suspend_type": "S", "suspend_timing": None}]),
        quote_fetcher=lambda _: {"300001.SZ": DualQuote("300001.SZ")},
    )
    checks = {item["field"]: item for item in fact["metadata"]["fieldChecks"]}
    assert fact["availability"] == "suspended"
    assert checks["suspension"]["state"] == "single_source"
    assert checks["limitUpPrice"]["state"] == "unavailable"


def test_anomaly_audit_round_trips_through_the_schema3_append_only_market_fact(tmp_path):
    path = tmp_path / "market-anomaly.sqlite"
    initialize_schema(path)
    fact = _daily_fact(quote_fetcher=lambda _: {
        "300001.SZ": DualQuote("300001.SZ", _quote("sina"), _quote("tencent", high=10.8)),
    })
    assert record_market_day_fact(fact=fact, db_path=path, created_at=fact["obtainedAt"]) == 1
    stored = store.list_market_day_facts(company_code="300001.SZ", db_path=path)
    assert stored[0]["availability"] == "anomaly"
    assert stored[0]["metadata"]["anomalyReason"] == "cross_source_conflict:high"
    assert next(item for item in stored[0]["metadata"]["fieldChecks"] if item["field"] == "high")["state"] == "conflict"
