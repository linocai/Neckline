"""退潮逐拍指标台账单测(`sentinel/retreat_store.py`,v1.1-H2)。覆盖:落库幂等、
持续性上一拍读取、同时段基线(±窗命中 / 窗外静默 / 无昨日数据静默)。"""

from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import business_days, insert_trade_cal

from neckline.sentinel.retreat import RetreatMetrics
from neckline.sentinel.retreat_store import (
    load_prev_tick_triggered,
    load_same_time_zaban_baseline,
    record_retreat_metrics,
)

pytestmark = pytest.mark.usefixtures("isolated_env")


def _metrics(d: date, hhmm: str, *, zaban_rate=0.0, limit_down=0, hot=None) -> RetreatMetrics:
    return RetreatMetrics(
        trade_date=d, hhmm=hhmm, sample_size=100, limit_up_count=10,
        limit_down_count=limit_down, zaban_count=int(round(zaban_rate * 10)),
        zaban_rate=zaban_rate, hot_sector_avg_chg=hot,
    )


class TestRecordAndPrevTick:
    def test_prev_tick_reads_most_recent_earlier_row(self, isolated_env):
        d = date(2026, 7, 17)
        record_retreat_metrics(_metrics(d, "0945"), triggered=["zaban"], tier="yellow", red_via=[], db_path=isolated_env.db_path)
        record_retreat_metrics(_metrics(d, "1000"), triggered=["limit_down"], tier="yellow", red_via=[], db_path=isolated_env.db_path)
        # 查 1035 之前的最近一拍 → 1000 的触发集
        assert load_prev_tick_triggered(d, "1035", db_path=isolated_env.db_path) == ["limit_down"]
        # 查 0945 之前 → 无更早行
        assert load_prev_tick_triggered(d, "0945", db_path=isolated_env.db_path) == []

    def test_first_tick_of_day_has_no_prev(self, isolated_env):
        d = date(2026, 7, 17)
        assert load_prev_tick_triggered(d, "0931", db_path=isolated_env.db_path) == []

    def test_insert_or_replace_is_idempotent(self, isolated_env):
        d = date(2026, 7, 17)
        record_retreat_metrics(_metrics(d, "0945"), triggered=["zaban"], tier="yellow", red_via=[], db_path=isolated_env.db_path)
        # 同分钟重复(如重试)→ 覆盖,不新增行、取最新触发集
        record_retreat_metrics(_metrics(d, "0945"), triggered=["zaban", "limit_down"], tier="red", red_via=["multi_condition"], db_path=isolated_env.db_path)
        assert load_prev_tick_triggered(d, "1000", db_path=isolated_env.db_path) == ["zaban", "limit_down"]


class TestSameTimeBaseline:
    def test_closest_within_window_returned(self, isolated_env):
        days = business_days(date(2026, 7, 1), 10)
        insert_trade_cal(isolated_env, days)
        today, prev = days[-1], days[-2]
        # 昨日在 0948 / 0952 / 1010 各一拍
        record_retreat_metrics(_metrics(prev, "0948", zaban_rate=0.30), triggered=[], tier="none", red_via=[], db_path=isolated_env.db_path)
        record_retreat_metrics(_metrics(prev, "0952", zaban_rate=0.40), triggered=[], tier="none", red_via=[], db_path=isolated_env.db_path)
        record_retreat_metrics(_metrics(prev, "1010", zaban_rate=0.99), triggered=[], tier="none", red_via=[], db_path=isolated_env.db_path)
        # 今日 0950 → 窗内(±5min)最近的是 0948(距2)与 0952(距2)——0948 先命中(dist 相等取先扫到)
        b = load_same_time_zaban_baseline(today, "0950", window_min=5, db_path=isolated_env.db_path)
        assert b in (0.30, 0.40)  # 二者距离相等,取其一即可(都在窗内,均为合理基线)

    def test_out_of_window_is_none(self, isolated_env):
        days = business_days(date(2026, 7, 1), 10)
        insert_trade_cal(isolated_env, days)
        today, prev = days[-1], days[-2]
        record_retreat_metrics(_metrics(prev, "1010", zaban_rate=0.50), triggered=[], tier="none", red_via=[], db_path=isolated_env.db_path)
        # 今日 0950 距 1010 达 20min > 5min 窗 → 无基线
        assert load_same_time_zaban_baseline(today, "0950", window_min=5, db_path=isolated_env.db_path) is None

    def test_no_prev_day_data_is_none(self, isolated_env):
        days = business_days(date(2026, 7, 1), 10)
        insert_trade_cal(isolated_env, days)
        today = days[-1]
        # 昨日无任何 retreat_metrics 行(部署首日语境)→ 静默失效
        assert load_same_time_zaban_baseline(today, "0950", window_min=5, db_path=isolated_env.db_path) is None

    def test_only_previous_trading_day_not_older(self, isolated_env):
        days = business_days(date(2026, 7, 1), 10)
        insert_trade_cal(isolated_env, days)
        today, prev, older = days[-1], days[-2], days[-3]
        # 只有更早那天(days[-3])有数据,昨日(days[-2])没有 → 不回退到更早的历史日
        record_retreat_metrics(_metrics(older, "0950", zaban_rate=0.77), triggered=[], tier="none", red_via=[], db_path=isolated_env.db_path)
        assert load_same_time_zaban_baseline(today, "0950", window_min=5, db_path=isolated_env.db_path) is None
