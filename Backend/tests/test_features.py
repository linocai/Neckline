"""母战法信号源单测(plan 1.1)。特征/前瞻收益是纯函数,用手工构造的合成面板
断言:① 特征算对;② 无前视(截断未来不改变过去行的特征);③ 前瞻收益正确引用未来
且区间末尾为 null。"""

from __future__ import annotations

from datetime import date

import polars as pl

from neckline.strategy.features import add_features, add_forward_returns, merge_limit_features


def _series(ts_code: str, closes, opens=None, highs=None, lows=None, vols=None, start=date(2024, 1, 1)):
    """构造单票日线序列(交易日用连续自然日近似,特征只关心顺序不关心日历)。"""
    from tests.conftest import business_days

    n = len(closes)
    days = business_days(start, n)
    opens = opens or closes
    highs = highs or [max(o, c) for o, c in zip(opens, closes)]
    lows = lows or [min(o, c) for o, c in zip(opens, closes)]
    vols = vols or [1000.0] * n
    pre = [closes[0]] + closes[:-1]
    return pl.DataFrame(
        {
            "ts_code": [ts_code] * n,
            "trade_date": days,
            "open": [float(x) for x in opens],
            "high": [float(x) for x in highs],
            "low": [float(x) for x in lows],
            "close": [float(x) for x in closes],
            "pre_close": [float(x) for x in pre],
            "vol": [float(x) for x in vols],
            "amount": [float(x) * 10 for x in vols],
        }
    )


class TestAddFeatures:
    def test_ma_and_returns(self):
        closes = list(range(1, 26))  # 1..25 递增
        df = add_features(_series("600000.SH", closes))
        last = df.sort("trade_date").row(-1, named=True)
        # ma5 = mean(21..25)=23, ma20 = mean(6..25)=15.5
        assert abs(last["ma5"] - 23.0) < 1e-9
        assert abs(last["ma20"] - 15.5) < 1e-9
        # ret_20d = 25/5 - 1 = 4.0(20 日前收盘=5)
        assert abs(last["ret_20d"] - (25 / 5 - 1)) < 1e-9
        # high_20d = 25(含当日),dist_from_high = 0
        assert abs(last["dist_from_high_20d"] - 0.0) < 1e-9
        assert last["above_ma20_bullish"] is True

    def test_no_lookahead_truncation_invariance(self):
        """截断未来行不改变过去行的特征值(后向窗口铁律)。"""
        closes = [10 + i * 0.5 for i in range(30)]
        full = add_features(_series("600000.SH", closes)).sort("trade_date")
        trunc = add_features(_series("600000.SH", closes[:25])).sort("trade_date")
        # 第 24 行(index 23)在两种输入下特征必须一致
        for col in ["ma5", "ma10", "ma20", "ret_20d", "dist_from_high_20d", "vol_ratio_5"]:
            a = full[col][23]
            b = trunc[col][23]
            assert (a is None and b is None) or abs(a - b) < 1e-9, f"{col} 前视泄漏: {a} != {b}"

    def test_breakout_platform_high_excludes_today(self):
        # 前 20 日在 10 附近盘整,今日跳到 12 → 突破前 20 日收盘高点
        closes = [10.0] * 22 + [12.0]
        df = add_features(_series("600000.SH", closes)).sort("trade_date")
        last = df.row(-1, named=True)
        assert last["prev_close_max_20d"] == 10.0  # 不含今日
        assert last["close"] > last["prev_close_max_20d"]


class TestForwardReturns:
    def test_forward_ret_references_future_open(self):
        opens = [10.0, 11.0, 12.0, 13.0, 14.0]
        panel = merge_limit_features(add_features(_series("600000.SH", opens, opens=opens)), pl.DataFrame())
        fwd = add_forward_returns(panel, max_hold=3).sort("trade_date")
        r0 = fwd.row(0, named=True)
        # d0: 买 T+1 开(=11),持 1 日卖 T+2 开(=12)→ 12/11-1
        assert abs(r0["fwd_entry_open"] - 11.0) < 1e-9
        assert abs(r0["fwd_ret_1"] - (12 / 11 - 1)) < 1e-9
        assert abs(r0["fwd_ret_2"] - (13 / 11 - 1)) < 1e-9

    def test_tail_rows_null(self):
        opens = [10.0, 11.0, 12.0, 13.0, 14.0]
        panel = merge_limit_features(add_features(_series("600000.SH", opens, opens=opens)), pl.DataFrame())
        fwd = add_forward_returns(panel, max_hold=3).sort("trade_date")
        # 最后一行无 T+1 → fwd_entry_open null,fwd_buyable False
        last = fwd.row(-1, named=True)
        assert last["fwd_entry_open"] is None
        assert last["fwd_buyable"] is False

    def test_not_buyable_when_next_limit_up(self):
        opens = [10.0, 11.0, 12.0]
        base = add_features(_series("600000.SH", opens, opens=opens)).sort("trade_date")
        # 手工标 T+1(第 2 行)为涨停 → T0 行 fwd_buyable 应为 False
        ld = pl.DataFrame(
            {
                "ts_code": ["600000.SH"],
                "trade_date": [base["trade_date"][1]],
                "is_limit_up": [True],
                "is_limit_down": [False],
                "consec_limit_up_days": [1],
            }
        )
        panel = merge_limit_features(base, ld)
        fwd = add_forward_returns(panel, max_hold=1).sort("trade_date")
        assert fwd.row(0, named=True)["fwd_buyable"] is False


class TestLimitFeatures:
    def test_limitup_count_20d(self):
        base = add_features(_series("600000.SH", [10.0] * 5))
        ld = pl.DataFrame(
            {
                "ts_code": ["600000.SH", "600000.SH"],
                "trade_date": [base.sort("trade_date")["trade_date"][1], base.sort("trade_date")["trade_date"][3]],
                "is_limit_up": [True, True],
                "is_limit_down": [False, False],
                "consec_limit_up_days": [1, 1],
            }
        )
        out = merge_limit_features(base, ld).sort("trade_date")
        # 到第 4 行(index 3)累计涨停 2 次
        assert out["limitup_count_20d"][3] == 2
        assert out["limitup_count_20d"][0] == 0
