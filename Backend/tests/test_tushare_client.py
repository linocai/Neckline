"""TuShare 封装单测(plan 0.2)。不联网——只测「无 token 优雅降级」「to_ts_code
转换」「TushareResult 语义」;真实 API 联调见 backfill 脚本手工跑的验收记录。
"""

from __future__ import annotations

import pytest

from neckline.data.tushare_client import TushareResult, reset_client_cache, to_ts_code


@pytest.fixture(autouse=True)
def _reset_ts_client_cache():
    reset_client_cache()
    yield
    reset_client_cache()


class TestToTsCode:
    def test_already_full_code_passthrough(self):
        assert to_ts_code("600000.SH") == "600000.SH"
        assert to_ts_code("000001.sz") == "000001.SZ"

    def test_sse_main_board(self):
        assert to_ts_code("600519") == "600519.SH"

    def test_szse_main_and_gem(self):
        assert to_ts_code("000001") == "000001.SZ"
        assert to_ts_code("300750") == "300750.SZ"

    def test_star_market_prefixed_6_falls_to_sh(self):
        assert to_ts_code("688981") == "688981.SH"

    def test_bse_prefixes(self):
        assert to_ts_code("830799") == "830799.BJ"
        assert to_ts_code("430047") == "430047.BJ"
        assert to_ts_code("920099") == "920099.BJ"

    def test_non_6_digit_passthrough(self):
        assert to_ts_code("abc") == "ABC"


class TestTushareResult:
    def test_success_factory(self):
        r = TushareResult.success({"a": 1})
        assert r.ok is True
        assert r.data == {"a": 1}
        assert r.reason == "ok"

    def test_fail_factory(self):
        r = TushareResult.fail("token 缺失")
        assert r.ok is False
        assert r.data is None
        assert r.reason == "token 缺失"


class TestNoTokenGracefulDegradation:
    """铁律:token 缺失时任何调用都不抛异常,ok=False + 可读 reason。"""

    def test_missing_token_returns_fail_not_raise(self, monkeypatch):
        import neckline.data.tushare_client as ts_mod
        from dataclasses import replace

        # Settings 是 frozen dataclass,不能直接 setattr 单字段——按 LinoN 教训用
        # 替身对象 + monkeypatch 模块级 settings 名字。
        fake = replace(ts_mod.settings, tushare_token=None)
        monkeypatch.setattr(ts_mod, "settings", fake)

        res = ts_mod.ts_daily_all("20260101")
        assert res.ok is False
        assert res.data is None
        assert "token" in res.reason

    def test_missing_token_all_batch_functions_degrade(self, monkeypatch):
        import neckline.data.tushare_client as ts_mod
        from dataclasses import replace

        fake = replace(ts_mod.settings, tushare_token=None)
        monkeypatch.setattr(ts_mod, "settings", fake)

        for fn, kwargs in [
            (ts_mod.ts_daily_all, dict(trade_date="20260101")),
            (ts_mod.ts_daily_basic_all, dict(trade_date="20260101")),
            (ts_mod.ts_adj_factor_all, dict(trade_date="20260101")),
            (ts_mod.ts_moneyflow_dc_all, dict(trade_date="20260101")),
            (ts_mod.ts_trade_cal, dict(start="20260101", end="20260110")),
            (ts_mod.ts_stock_basic, dict(list_status="L")),
            (ts_mod.ts_namechange_page, dict()),
            (ts_mod.ts_top_list, dict(trade_date="20260101")),
            (ts_mod.ts_stk_holdertrade, dict(start="20260101", end="20260110")),
        ]:
            res = fn(**kwargs)
            assert res.ok is False, f"{fn.__name__} 应在无 token 时优雅降级"
            assert res.data is None
