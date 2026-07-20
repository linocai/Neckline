"""事件研究单测(plan 1.1)。用手工面板(已知前瞻收益)断言统计口径正确:
胜率/毛净收益/盈利因子/可买过滤/分层。"""

from __future__ import annotations

from datetime import date

import polars as pl

from neckline.research import eventstudy as ES


def _panel():
    """5 行信号,fwd_ret_1 已知:三涨两跌;fwd_ret_2 同。含一行 fwd_buyable=False
    (应被剔除)与一行 fwd_ret null(区间末尾,应被剔除)。"""
    return pl.DataFrame(
        {
            "ts_code": ["A", "B", "C", "D", "E", "F"],
            "trade_date": [date(2024, 1, 2)] * 3 + [date(2024, 1, 3)] * 3,
            "year": [2024] * 6,
            "sig": [True, True, True, True, True, True],
            "fwd_buyable": [True, True, True, True, False, True],
            "fwd_ret_1": [0.10, 0.05, -0.04, -0.02, 0.50, None],
            "fwd_ret_2": [0.12, 0.06, -0.05, -0.03, 0.50, None],
        }
    )


class TestEventStudy:
    def test_counts_exclude_unbuyable_and_null(self):
        # 6 行里 E 不可买、F 前瞻 null → 有效 4 行
        res = ES.event_study(_panel(), pl.col("sig"), hold_days=(1,), cost_oneside=0.0)
        assert res.filter(pl.col("hold_days") == 1)["n"][0] == 4

    def test_win_rate_and_means_zero_cost(self):
        res = ES.event_study(_panel(), pl.col("sig"), hold_days=(1,), cost_oneside=0.0).row(0, named=True)
        # 有效收益 [0.10,0.05,-0.04,-0.02] → 2 胜 → 胜率 0.5
        assert abs(res["win_rate"] - 0.5) < 1e-9
        assert abs(res["mean_gross"] - (0.10 + 0.05 - 0.04 - 0.02) / 4) < 1e-9
        # PF = (0.10+0.05)/(0.04+0.02) = 0.15/0.06 = 2.5
        assert abs(res["profit_factor"] - 2.5) < 1e-9

    def test_cost_shifts_net_and_winrate(self):
        # 单边成本 0.03 → 双边 0.06;净 = 毛-0.06 → [0.04,-0.01,-0.10,-0.08] → 1 胜
        res = ES.event_study(_panel(), pl.col("sig"), hold_days=(1,), cost_oneside=0.03).row(0, named=True)
        assert abs(res["win_rate"] - 0.25) < 1e-9
        assert abs(res["mean_net"] - (res["mean_gross"] - 0.06)) < 1e-9

    def test_grouped_by_date_partitions(self):
        # A/B/C 在 1-02,D 在 1-03(E 不可买剔除)→ 分层计数 3 与 1
        g = ES.event_study_grouped(_panel(), pl.col("sig"), "trade_date", hold_days=(1,))
        counts = dict(zip(g["trade_date"].to_list(), g["n"].to_list()))
        assert counts[date(2024, 1, 2)] == 3
        assert counts[date(2024, 1, 3)] == 1

    def test_compare_signals_shape(self):
        res = ES.compare_signals(_panel(), {"s1": pl.col("sig")}, hold_days=(1, 2))
        assert set(res["signal"].to_list()) == {"s1"}
        assert sorted(res["hold_days"].to_list()) == [1, 2]
