"""数据访问层单测(plan 0.6)。核心是【铁律】前视截断锁死:任何查询不得返回
> 请求日的数据。用隔离 Parquet 目录灌入跨多天的数据,断言查询边界。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from tests.conftest import write_daily_fixture

pytestmark = pytest.mark.usefixtures("isolated_env")


def _seed_multi_day(settings):
    # 【坑】write_table_day 是整天覆盖写(不是追加);同一天多只票必须一次性传入同
    # 一个 rows 列表写一次,分两次调用会互相覆盖(第二次覆盖第一次)。
    for d, close in [
        (date(2024, 1, 2), 10.0),
        (date(2024, 1, 3), 10.5),
        (date(2024, 1, 4), 11.0),
        (date(2024, 1, 5), 11.5),
        (date(2024, 1, 8), 12.0),
    ]:
        write_daily_fixture(
            settings,
            "daily",
            d,
            [
                {"ts_code": "600001.SH", "open": close, "high": close, "low": close, "close": close, "pre_close": close},
                {
                    "ts_code": "000001.SZ",
                    "open": close * 2,
                    "high": close * 2,
                    "low": close * 2,
                    "close": close * 2,
                    "pre_close": close * 2,
                },
            ],
        )


class TestGetMarketSliceNeverLeaksFuture:
    def test_returns_only_exact_requested_day(self, isolated_env):
        _seed_multi_day(isolated_env)
        from neckline.data.market_data import get_market_slice

        out = get_market_slice(date(2024, 1, 3))
        assert set(out["trade_date"].to_list()) == {date(2024, 1, 3)}
        assert len(out) == 2  # 两只票

    def test_nonexistent_day_returns_empty_not_nearby_day(self, isolated_env):
        """请求一个没有文件的日期(如周末),不应"就近"拿到别的日子的数据。"""
        _seed_multi_day(isolated_env)
        from neckline.data.market_data import get_market_slice

        out = get_market_slice(date(2024, 1, 6))  # 周六,没写过数据
        assert len(out) == 0

    def test_missing_table_returns_empty_dataframe_not_error(self, isolated_env):
        from neckline.data.market_data import get_market_slice

        out = get_market_slice(date(2024, 1, 3), table="moneyflow_dc")  # 从没写过这张表
        assert isinstance(out, pl.DataFrame)
        assert len(out) == 0


class TestGetStockHistoryNeverReturnsBeyondEnd:
    def test_end_boundary_excludes_later_rows(self, isolated_env):
        _seed_multi_day(isolated_env)
        from neckline.data.market_data import get_stock_history

        out = get_stock_history("600001.SH", date(2024, 1, 2), date(2024, 1, 4))
        dates = out["trade_date"].to_list()
        assert dates == [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        assert max(dates) <= date(2024, 1, 4)
        # 铁证:数据库里明明有 01-05、01-08 更晚的数据,但查询 end=01-04 绝不该拿到
        assert date(2024, 1, 5) not in dates
        assert date(2024, 1, 8) not in dates

    def test_as_of_clamps_end_and_warns(self, isolated_env, caplog):
        _seed_multi_day(isolated_env)
        from neckline.data.market_data import get_stock_history

        with caplog.at_level("WARNING"):
            out = get_stock_history("600001.SH", date(2024, 1, 2), date(2024, 1, 8), as_of=date(2024, 1, 4))
        dates = out["trade_date"].to_list()
        assert max(dates) <= date(2024, 1, 4)
        assert date(2024, 1, 5) not in dates
        assert date(2024, 1, 8) not in dates
        assert any("疑似前视 bug" in rec.message for rec in caplog.records)

    def test_as_of_not_exceeded_no_warning(self, isolated_env, caplog):
        _seed_multi_day(isolated_env)
        from neckline.data.market_data import get_stock_history

        with caplog.at_level("WARNING"):
            out = get_stock_history("600001.SH", date(2024, 1, 2), date(2024, 1, 3), as_of=date(2024, 1, 8))
        assert len(out) == 2
        assert not any("前视" in rec.message for rec in caplog.records)

    def test_start_after_end_returns_empty(self, isolated_env):
        _seed_multi_day(isolated_env)
        from neckline.data.market_data import get_stock_history

        out = get_stock_history("600001.SH", date(2024, 1, 8), date(2024, 1, 2))
        assert len(out) == 0

    def test_only_requested_ts_code_returned(self, isolated_env):
        _seed_multi_day(isolated_env)
        from neckline.data.market_data import get_stock_history

        out = get_stock_history("600001.SH", date(2024, 1, 2), date(2024, 1, 8))
        assert set(out["ts_code"].to_list()) == {"600001.SH"}


class TestScanTableRangeAlsoBounded:
    def test_range_scan_excludes_outside_window(self, isolated_env):
        _seed_multi_day(isolated_env)
        from neckline.data.market_data import scan_table_range

        out = scan_table_range("daily", date(2024, 1, 3), date(2024, 1, 4))
        dates = sorted(set(out["trade_date"].to_list()))
        assert dates == [date(2024, 1, 3), date(2024, 1, 4)]


class TestWriteReadRoundTrip:
    def test_day_file_exists_after_write(self, isolated_env):
        from neckline.data.market_data import day_file_exists

        assert day_file_exists("daily", date(2024, 1, 2)) is False
        write_daily_fixture(
            isolated_env, "daily", date(2024, 1, 2),
            [{"ts_code": "600001.SH", "open": 1, "high": 1, "low": 1, "close": 1, "pre_close": 1}],
        )
        assert day_file_exists("daily", date(2024, 1, 2)) is True

    def test_invalid_table_name_raises(self, isolated_env):
        from neckline.data.market_data import table_dir

        with pytest.raises(ValueError):
            table_dir("not_a_real_table")


class TestSchemaAlignmentOnWrite:
    """TuShare 类型漂移防线(2026-07-21 生产真踩:turnover_rate_f 全空日落成 String)。"""

    def test_string_column_cast_to_existing_float(self, tmp_path):
        import polars as pl
        from neckline.data.market_data import write_table_day, scan_table_range
        from datetime import date
        good = pl.DataFrame({
            "ts_code": ["000001.SZ"], "trade_date": [date(2026, 7, 20)],
            "turnover_rate_f": [1.23],
        })
        write_table_day("daily_basic", date(2026, 7, 20), good, parquet_dir=tmp_path)
        drifted = pl.DataFrame({
            "ts_code": ["000001.SZ"], "trade_date": [date(2026, 7, 21)],
            "turnover_rate_f": ["", None, "2.5"][0:1],
        })
        write_table_day("daily_basic", date(2026, 7, 21), drifted, parquet_dir=tmp_path)
        out = scan_table_range("daily_basic", date(2026, 7, 20), date(2026, 7, 21), parquet_dir=tmp_path)
        assert out.schema["turnover_rate_f"] == pl.Float64
        assert out.height == 2
        assert out.sort("trade_date")["turnover_rate_f"][1] is None  # 空串 → null 而非炸

    def test_first_write_no_existing_partition_passthrough(self, tmp_path):
        import polars as pl
        from neckline.data.market_data import write_table_day, get_market_slice
        from datetime import date
        df = pl.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [date(2026, 7, 21)], "x": ["s"]})
        write_table_day("daily_basic", date(2026, 7, 21), df, parquet_dir=tmp_path)
        out = get_market_slice(date(2026, 7, 21), table="daily_basic", parquet_dir=tmp_path)
        assert out.schema["x"] == pl.String and out.height == 1
