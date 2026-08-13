"""前复权单测(plan 0.5)。"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from neckline.data.adjust import apply_qfq, qfq


class TestQfqScalar:
    def test_basic_formula(self):
        # qfq = raw * (adj_factor / latest_adj_factor)
        assert qfq(10.0, 100.0, 200.0) == pytest.approx(5.0)

    def test_latest_equals_current_no_change(self):
        assert qfq(10.0, 150.0, 150.0) == pytest.approx(10.0)

    def test_zero_latest_adj_factor_degrades_to_raw(self):
        assert qfq(10.0, 100.0, 0) == pytest.approx(10.0)

    def test_none_latest_adj_factor_degrades_to_raw(self):
        assert qfq(10.0, 100.0, None) == pytest.approx(10.0)


class TestApplyQfqDataFrame:
    def test_latest_day_price_unchanged_after_qfq(self):
        """前复权口径:最新一条 adj_factor 做基准,今日价格 qfq 后应与原始价相等。"""
        df = pl.DataFrame(
            {
                "ts_code": ["600001.SH"] * 3,
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "close": [10.0, 10.5, 11.0],
                "adj_factor": [100.0, 100.0, 120.0],  # 01-04 发生除权,因子跳变
            }
        )
        out = apply_qfq(df, price_cols=("close",))
        # 最新一条(01-04)qfq 后应等于原始收盘价
        last_row = out.filter(pl.col("trade_date") == date(2024, 1, 4)).row(0, named=True)
        assert last_row["close_qfq"] == pytest.approx(11.0)

    def test_historical_price_scaled_by_factor_ratio(self):
        df = pl.DataFrame(
            {
                "ts_code": ["600001.SH"] * 2,
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "close": [10.0, 12.0],
                "adj_factor": [100.0, 120.0],
            }
        )
        out = apply_qfq(df, price_cols=("close",))
        first_row = out.filter(pl.col("trade_date") == date(2024, 1, 2)).row(0, named=True)
        # latest_adj_factor = 120(最新一条);qfq = 10.0 * (100/120)
        assert first_row["close_qfq"] == pytest.approx(10.0 * 100 / 120)

    def test_multi_stock_independent_latest_factor(self):
        df = pl.DataFrame(
            {
                "ts_code": ["A", "A", "B", "B"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3)] * 2,
                "close": [10.0, 11.0, 20.0, 22.0],
                "adj_factor": [100.0, 110.0, 50.0, 55.0],
            }
        )
        out = apply_qfq(df, price_cols=("close",))
        a_last = out.filter((pl.col("ts_code") == "A") & (pl.col("trade_date") == date(2024, 1, 3))).row(0, named=True)
        b_last = out.filter((pl.col("ts_code") == "B") & (pl.col("trade_date") == date(2024, 1, 3))).row(0, named=True)
        assert a_last["close_qfq"] == pytest.approx(11.0)
        assert b_last["close_qfq"] == pytest.approx(22.0)

    def test_empty_dataframe_does_not_crash(self):
        df = pl.DataFrame(
            schema={"ts_code": pl.Utf8, "trade_date": pl.Date, "close": pl.Float64, "adj_factor": pl.Float64}
        )
        out = apply_qfq(df, price_cols=("close",))
        assert len(out) == 0
        assert "close_qfq" in out.columns
