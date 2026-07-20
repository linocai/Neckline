"""信号定义单测(plan 1.1/1.2)。强势/买点/禁买过滤是纯 polars 表达式,用手工面板
断言选中/剔除的行符合定义。"""

from __future__ import annotations

from datetime import date

import polars as pl

from neckline.strategy import signals as S


def _panel(rows):
    """构造带信号所需列的最小面板。"""
    return pl.DataFrame(rows)


class TestStrengthDefs:
    def test_limitup_gene(self):
        p = _panel(
            [
                {"ts_code": "A", "limitup_count_20d": 0},
                {"ts_code": "B", "limitup_count_20d": 1},
                {"ts_code": "C", "limitup_count_20d": 3},
            ]
        )
        sel = p.filter(S.strength_limitup_gene(min_count=1))["ts_code"].to_list()
        assert sel == ["B", "C"]
        sel2 = p.filter(S.strength_limitup_gene(min_count=3))["ts_code"].to_list()
        assert sel2 == ["C"]

    def test_ret_rank_absolute(self):
        p = _panel([{"ts_code": "A", "ret_20d": 0.05}, {"ts_code": "B", "ret_20d": 0.20}])
        assert p.filter(S.strength_ret_rank(0.15))["ts_code"].to_list() == ["B"]

    def test_ret_rank_percentile(self):
        p = _panel(
            [
                {"ts_code": c, "trade_date": date(2024, 1, 2), "ret_20d": r}
                for c, r in [("A", -0.1), ("B", 0.0), ("C", 0.1), ("D", 0.2), ("E", 0.3)]
            ]
        )
        ranked = S.add_ret_rank_column(p)
        # E 最高分位 = 1.0,A 最低 = 0.0
        assert abs(ranked.filter(pl.col("ts_code") == "E")["ret_20d_pct"][0] - 1.0) < 1e-9
        assert abs(ranked.filter(pl.col("ts_code") == "A")["ret_20d_pct"][0] - 0.0) < 1e-9
        top = ranked.filter(S.strength_ret_rank_pct(0.75))["ts_code"].to_list()
        assert set(top) == {"D", "E"}

    def test_volprice(self):
        p = _panel(
            [
                {"ts_code": "A", "above_ma20_bullish": True, "vol_ratio_5": 1.2},
                {"ts_code": "B", "above_ma20_bullish": True, "vol_ratio_5": 0.8},  # 缩量
                {"ts_code": "C", "above_ma20_bullish": False, "vol_ratio_5": 2.0},  # 空头
            ]
        )
        assert p.filter(S.strength_volprice())["ts_code"].to_list() == ["A"]


class TestBuyPoints:
    def test_pullback(self):
        p = _panel(
            [
                {"ts_code": "A", "ret_1d": -0.01, "close": 10.0, "ma10": 9.5},  # 回调不破位
                {"ts_code": "B", "ret_1d": 0.03, "close": 10.0, "ma10": 9.5},  # 上涨,非回调
                {"ts_code": "C", "ret_1d": -0.02, "close": 9.0, "ma10": 9.5},  # 回调破位
            ]
        )
        assert p.filter(S.buy_pullback())["ts_code"].to_list() == ["A"]

    def test_breakout(self):
        p = _panel(
            [
                {"ts_code": "A", "close": 11.0, "prev_close_max_20d": 10.0, "vol_ratio_5": 2.0},  # 放量突破
                {"ts_code": "B", "close": 11.0, "prev_close_max_20d": 10.0, "vol_ratio_5": 1.0},  # 突破但缩量
                {"ts_code": "C", "close": 9.0, "prev_close_max_20d": 10.0, "vol_ratio_5": 2.0},  # 未突破
            ]
        )
        assert p.filter(S.buy_breakout(vol_expand=1.5))["ts_code"].to_list() == ["A"]


class TestForbidFilters:
    def test_green_bigdown(self):
        p = _panel([{"ts_code": "A", "ret_1d": -0.05}, {"ts_code": "B", "ret_1d": -0.01}])
        assert p.filter(S.forbid_green_bigdown(-0.03))["ts_code"].to_list() == ["A"]

    def test_far_from_high(self):
        p = _panel([{"ts_code": "A", "dist_from_high_20d": -0.20}, {"ts_code": "B", "dist_from_high_20d": -0.05}])
        assert p.filter(S.forbid_far_from_high(-0.15))["ts_code"].to_list() == ["A"]

    def test_new_stock(self):
        p = _panel([{"ts_code": "A", "days_since_listing": 30}, {"ts_code": "B", "days_since_listing": 300}])
        assert p.filter(S.forbid_new_stock(120))["ts_code"].to_list() == ["A"]

    def test_st(self):
        p = _panel([{"ts_code": "A", "is_st": True}, {"ts_code": "B", "is_st": False}])
        assert p.filter(S.forbid_st())["ts_code"].to_list() == ["A"]

    def test_high_elasticity(self):
        p = _panel(
            [
                {"ts_code": "A", "board": "GEM"},
                {"ts_code": "B", "board": "MAIN"},
                {"ts_code": "C", "board": "STAR"},
            ]
        )
        assert set(p.filter(S.forbid_high_elasticity())["ts_code"].to_list()) == {"A", "C"}
