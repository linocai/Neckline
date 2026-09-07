from __future__ import annotations

import pytest
import polars as pl
from datetime import date, datetime, timezone

from neckline.data.market_data import write_table_day
from neckline.k10.market_context import MarketContextError, collect_market_context, frozen_analysis_market_context


def test_absent_market_snapshot_is_explicitly_unavailable():
    value = frozen_analysis_market_context(None, cutoff_at="2026-09-06T21:00:00+08:00")
    assert value == {"status": "unavailable", "reason": "market_context_not_collected", "asOf": "2026-09-06T21:00:00+08:00", "sourceRefs": [], "recentDays": []}


def test_available_market_snapshot_requires_frozen_source_backed_history():
    value = frozen_analysis_market_context({
        "status": "available", "asOf": "2026-09-06T20:00:00+08:00",
        "sourceRefs": [{"url": "market://daily/300001.SZ", "collectedAt": "2026-09-06T20:00:00+08:00"}],
        "recentDays": [{"tradeDate": "20260905", "close": 10.0, "pctChg": 1.0}],
    }, cutoff_at="2026-09-06T21:00:00+08:00")
    assert value["recentDays"][0]["close"] == 10.0
    with pytest.raises(MarketContextError, match="晚于分析截止"):
        frozen_analysis_market_context({**value, "asOf": "2026-09-07T09:00:00+08:00"}, cutoff_at="2026-09-06T21:00:00+08:00")


def test_collects_only_parquet_history_visible_at_cutoff(tmp_path):
    parquet = tmp_path / "synthetic-parquet"
    d1, d2 = date(2026, 9, 4), date(2026, 9, 7)
    for day, close in ((d1, 10.0), (d2, 11.0)):
        write_table_day("daily", day, pl.DataFrame([{"ts_code": "300001.SZ", "trade_date": day, "open": close - .1, "high": close, "low": close - .2, "close": close, "pre_close": 9.8, "pct_chg": 1.0}]), parquet_dir=parquet)
        write_table_day("adj_factor", day, pl.DataFrame([{"ts_code": "300001.SZ", "trade_date": day, "adj_factor": 1.0}]), parquet_dir=parquet)
    clock = lambda: datetime(2026, 9, 7, 14, tzinfo=timezone.utc)
    morning = collect_market_context(company_code="300001.SZ", cutoff_at="2026-09-07T09:00:00+08:00", parquet_dir=parquet, clock=clock)
    evening = collect_market_context(company_code="300001.SZ", cutoff_at="2026-09-07T21:00:00+08:00", parquet_dir=parquet, clock=clock)
    assert [row["tradeDate"] for row in morning["recentDays"]] == ["2026-09-04"]
    assert [row["tradeDate"] for row in evening["recentDays"]] == ["2026-09-04", "2026-09-07"]
    assert evening["status"] == "available"
    assert evening["collectedAt"] == "2026-09-07T14:00:00+00:00"
    assert evening["sourceRefs"][0]["dataFetchedAt"] == "unknown"


def test_missing_explicit_parquet_is_unavailable_not_a_default_data_read(tmp_path):
    value = collect_market_context(company_code="300001.SZ", cutoff_at="2026-09-07T21:00:00+08:00", parquet_dir=tmp_path / "empty", clock=lambda: datetime(2026, 9, 7, 14, tzinfo=timezone.utc))
    assert value["status"] == "unavailable"
