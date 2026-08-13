"""龙虎榜(top_list)数据访问单测(plan 2.4)。核心断言:① 缓存命中不重复调用
TuShare;② 缺失时现拉现落盘,落盘后二次读命中缓存;③ 无 token/拉取失败/该日无
数据 → 优雅降级为空表,不崩;④ `top_list_lookup` 只透出已核实单位的列。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import polars as pl
import pytest

import neckline.data.top_list as top_list_mod
from neckline.data.tushare_client import TushareResult

pytestmark = pytest.mark.usefixtures("isolated_env")

D = date(2026, 3, 4)


def _fake_pandas_row(**overrides) -> pd.DataFrame:
    row = {
        "trade_date": "20260304",
        "ts_code": "600001.SH",
        "name": "示例股份",
        "close": 12.34,
        "pct_change": 9.98,
        "turnover_rate": 15.2,
        "amount": 123456.0,  # 单位未确认,不应出现在 lookup 结果里
        "l_sell": 800.0,
        "l_buy": 1500.0,
        "l_amount": 2300.0,
        "net_amount": 700.0,
        "net_rate": 5.6,
        "amount_rate": 18.7,
        "float_values": 500000.0,
        "reason": "日涨幅偏离值达7%",
    }
    row.update(overrides)
    return pd.DataFrame([row])


class TestLoadTopList:
    def test_fetch_and_cache_when_missing(self, monkeypatch):
        calls = {"n": 0}

        def fake_ts_top_list(trade_date: str) -> TushareResult:
            calls["n"] += 1
            assert trade_date == "20260304"
            return TushareResult.success(_fake_pandas_row())

        monkeypatch.setattr(top_list_mod, "ts_top_list", fake_ts_top_list)

        out = top_list_mod.load_top_list(D)
        assert calls["n"] == 1
        assert out.height == 1
        assert out["ts_code"][0] == "600001.SH"
        assert out["trade_date"][0] == D

    def test_cache_hit_skips_tushare_call(self, monkeypatch):
        def fake_first(trade_date: str) -> TushareResult:
            return TushareResult.success(_fake_pandas_row())

        monkeypatch.setattr(top_list_mod, "ts_top_list", fake_first)
        top_list_mod.load_top_list(D)  # 首次拉取落盘

        def boom(trade_date: str) -> TushareResult:
            raise AssertionError("缓存命中时不应再调用 TuShare")

        monkeypatch.setattr(top_list_mod, "ts_top_list", boom)
        out = top_list_mod.load_top_list(D)  # 第二次应直接读缓存
        assert out.height == 1

    def test_missing_token_or_failure_degrades_to_empty(self, monkeypatch):
        def fake_fail(trade_date: str) -> TushareResult:
            return TushareResult.fail("token 缺失")

        monkeypatch.setattr(top_list_mod, "ts_top_list", fake_fail)
        out = top_list_mod.load_top_list(D)
        assert isinstance(out, pl.DataFrame)
        assert out.is_empty()

    def test_no_data_that_day_degrades_to_empty(self, monkeypatch):
        """该交易日本就没有股票上龙虎榜(正常情况,非失败)→ 空表。"""

        def fake_empty(trade_date: str) -> TushareResult:
            return TushareResult.success(pd.DataFrame())

        monkeypatch.setattr(top_list_mod, "ts_top_list", fake_empty)
        out = top_list_mod.load_top_list(D)
        assert out.is_empty()

    def test_fetch_if_missing_false_never_calls_tushare(self, monkeypatch):
        def boom(trade_date: str) -> TushareResult:
            raise AssertionError("fetch_if_missing=False 不应调用 TuShare")

        monkeypatch.setattr(top_list_mod, "ts_top_list", boom)
        out = top_list_mod.load_top_list(D, fetch_if_missing=False)
        assert out.is_empty()


class TestTopListLookup:
    def test_lookup_keyed_by_ts_code_and_column_whitelist(self, monkeypatch):
        def fake(trade_date: str) -> TushareResult:
            return TushareResult.success(_fake_pandas_row())

        monkeypatch.setattr(top_list_mod, "ts_top_list", fake)
        lut = top_list_mod.top_list_lookup(D)
        assert "600001.SH" in lut
        row = lut["600001.SH"]
        assert row["net_amount"] == pytest.approx(700.0)
        assert row["reason"] == "日涨幅偏离值达7%"
        # amount/float_values 单位未确认,不应透出
        assert "amount" not in row
        assert "float_values" not in row

    def test_lookup_empty_when_no_data(self, monkeypatch):
        def fake_fail(trade_date: str) -> TushareResult:
            return TushareResult.fail("网络异常")

        monkeypatch.setattr(top_list_mod, "ts_top_list", fake_fail)
        assert top_list_mod.top_list_lookup(D) == {}
