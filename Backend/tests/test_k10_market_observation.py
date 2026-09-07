from __future__ import annotations

import pandas as pd

from neckline.data.tushare_client import TushareResult
from neckline.k10.market_observation import fetch_market_day_fact


def _result(rows):
    return TushareResult.success(pd.DataFrame(rows))


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
