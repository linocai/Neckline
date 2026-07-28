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


# ————————————————————————————————————————————————————————————————
# canonical schema 对齐(v1.3.5;2026-07-27 生产事故的反向证伪)
# ————————————————————————————————————————————————————————————————

class TestCanonicalSchemaAlignment:
    """`_align_to_table_schema` 从「向既有分区的第一个文件看齐」改为「向
    `TABLE_FLOAT_COLS` 显式声明看齐」。**最要紧的是 `test_dirty_first_partition_
    does_not_drag_new_write`** —— 它锁死的正是 2026-07-27 生产事故的根因。"""

    @staticmethod
    def _write_raw(tmp_path, table, d, df):
        """绕开 `write_table_day`(即绕开对齐防线)直接落一个分区文件,用来合成
        「历史遗留的脏分区」——防线上线前的历史数据就是这么躺在盘上的。"""
        p = tmp_path / table / f"year={d.year}" / f"{d.strftime('%Y%m%d')}.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(p)

    def test_dirty_first_partition_does_not_drag_new_write(self, tmp_path):
        """**反向证伪(本次事故根因)**:首个分区是「12 数值列全 String 的 0 行空文件」
        (= 生产 moneyflow_dc 2020-01-02 的真实形态)时,新落的干净 Float64 数据**不得**
        被它带偏成 String。

        旧实现拿 `scan_parquet` 的第一个文件当 target,此处必然把新数据 cast 成 String
        —— 生产上正是这样让 2026-07-21..07-27 五天真数据全落成 String、读侧整表
        SchemaError、07-27 的 16:35 报告当场崩掉。新实现向 canonical 声明看齐,不看基准。
        """
        import polars as pl
        from datetime import date
        from neckline.data.market_data import write_table_day, get_market_slice

        # ① 脏基准:2020-01-02 的 0 行空文件,12 数值列全 String
        empty_str = pl.DataFrame(
            {"trade_date": [], "ts_code": [], "name": [],
             "pct_change": [], "close": [], "net_amount": []},
            schema={"trade_date": pl.Date, "ts_code": pl.String, "name": pl.String,
                    "pct_change": pl.String, "close": pl.String, "net_amount": pl.String},
        )
        self._write_raw(tmp_path, "moneyflow_dc", date(2020, 1, 2), empty_str)

        # ② 今天的真数据是干净 Float64
        fresh = pl.DataFrame({
            "trade_date": [date(2026, 7, 27)], "ts_code": ["600176.SH"], "name": ["中国巨石"],
            "pct_change": [9.99], "close": [41.07], "net_amount": [140214.98],
        })
        write_table_day("moneyflow_dc", date(2026, 7, 27), fresh, parquet_dir=tmp_path)

        # ③ 新文件必须仍是 Float64(没被脏基准带偏)——这一条挂 = 事故根因复活
        newp = tmp_path / "moneyflow_dc" / "year=2026" / "20260727.parquet"
        got = pl.read_parquet_schema(newp)
        assert got["pct_change"] == pl.Float64
        assert got["close"] == pl.Float64
        assert got["net_amount"] == pl.Float64

        # ④ 数值逐位不变(直接读新文件——此时整表还读不了,见 ⑤)
        assert pl.read_parquet(newp)["net_amount"][0] == 140214.98

        # ⑤ **如实锁死互补契约**:写侧防线只保证「今后不再产生脏分区」,**historical
        # 脏分区不会自愈** —— 混着脏基准整表 scan 依旧 SchemaError。要让读侧恢复,
        # 必须另跑 `scripts/fix_moneyflow_schema.py`(其单测见 test_fix_moneyflow_schema.py)。
        # 两者是互补的两半,谁也替代不了谁;生产 2026-07-28 正是「先修数据、后修写侧」。
        with pytest.raises(pl.exceptions.SchemaError):
            get_market_slice(date(2026, 7, 27), table="moneyflow_dc", parquet_dir=tmp_path)

    def test_string_incoming_cast_to_canonical_even_without_partitions(self, tmp_path):
        """表**尚无任何分区**时,TuShare 漂成 String 的数值列也要按声明落成 Float64。
        (旧实现此处直接 `return df` 原样通过 → 落一个 String 分区,就是脏基准的诞生方式。)"""
        import polars as pl
        from datetime import date
        from neckline.data.market_data import write_table_day

        drifted = pl.DataFrame({
            "trade_date": [date(2026, 7, 21)], "ts_code": ["600176.SH"], "name": ["中国巨石"],
            "pct_change": ["9.99"], "close": ["41.07"], "net_amount": [""],
        })
        write_table_day("moneyflow_dc", date(2026, 7, 21), drifted, parquet_dir=tmp_path)
        p = tmp_path / "moneyflow_dc" / "year=2026" / "20260721.parquet"
        df = pl.read_parquet(p)
        assert df.schema["pct_change"] == pl.Float64
        assert df["pct_change"][0] == 9.99
        assert df["net_amount"][0] is None      # 空串 → null,不臆造 0

    def test_undeclared_column_still_aligns_to_existing_partition(self, tmp_path):
        """声明覆盖不到的列(如 TuShare 新增列)仍走「向既有分区看齐」兜底,不被丢下。"""
        import polars as pl
        from datetime import date
        from neckline.data.market_data import write_table_day, scan_table_range

        first = pl.DataFrame({
            "trade_date": [date(2026, 7, 20)], "ts_code": ["600176.SH"], "name": ["巨石"],
            "pct_change": [1.0], "brand_new_col": [7.5],
        })
        write_table_day("moneyflow_dc", date(2026, 7, 20), first, parquet_dir=tmp_path)
        second = pl.DataFrame({
            "trade_date": [date(2026, 7, 21)], "ts_code": ["600176.SH"], "name": ["巨石"],
            "pct_change": [2.0], "brand_new_col": ["8.5"],      # 未声明列漂成 String
        })
        write_table_day("moneyflow_dc", date(2026, 7, 21), second, parquet_dir=tmp_path)
        out = scan_table_range("moneyflow_dc", date(2026, 7, 20), date(2026, 7, 21), parquet_dir=tmp_path)
        assert out.schema["brand_new_col"] == pl.Float64      # 兜底生效
        assert out.height == 2

    def test_missing_declaration_warns_and_falls_back(self, tmp_path, monkeypatch, caplog):
        """表未声明 → **不静默**:打 WARNING 且退回旧行为(向既有分区看齐)。"""
        import logging
        import polars as pl
        from datetime import date
        import neckline.data.market_data as md

        monkeypatch.delitem(md.TABLE_FLOAT_COLS, "daily")
        df = pl.DataFrame({"trade_date": [date(2026, 7, 21)], "ts_code": ["600176.SH"], "close": ["4.5"]})
        with caplog.at_level(logging.WARNING, logger="neckline.data.market_data"):
            md.write_table_day("daily", date(2026, 7, 21), df, parquet_dir=tmp_path)
        assert any("未在 TABLE_FLOAT_COLS 声明" in r.getMessage() for r in caplog.records)
        # 无既有分区 + 无声明 = 旧行为原样通过
        assert pl.read_parquet(tmp_path / "daily" / "year=2026" / "20260721.parquet").schema["close"] == pl.String

    def test_every_valid_table_has_a_declaration(self):
        """守门:往 `_VALID_TABLES` 加了新表却忘了补 canonical 声明,这条直接挂。"""
        from neckline.data.market_data import TABLE_FLOAT_COLS, _VALID_TABLES

        assert set(_VALID_TABLES) - set(TABLE_FLOAT_COLS) == set()
