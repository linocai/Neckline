"""情绪仪表盘单测(plan 2.1)。锁死:① 涨停/跌停/炸板/连板计数正确;② 三态仓位
额度阈值分支(满额/半额/休息)；③ 昨日涨停股今日平均溢价计算与"触发降档"逻辑；
④ 数据缺失(该表当日无文件)优雅降级,不崩。"""

from __future__ import annotations

from datetime import date
from typing import Dict, List

import pytest

from tests.conftest import business_days, insert_trade_cal, write_daily_fixture

from neckline.report.sentiment import FULL, HALF, REST, compute_sentiment

pytestmark = pytest.mark.usefixtures("isolated_env")


def _write_limit_derived(
    settings,
    d: date,
    up_codes: List[str],
    down_codes: List[str],
    zaban_codes: List[str],
    consec: Dict[str, int] = None,
) -> None:
    consec = consec or {}
    rows = []
    for c in up_codes:
        rows.append({
            "ts_code": c, "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
            "limit_up_price": 11.0, "limit_down_price": 9.0,
            "is_limit_up": True, "is_limit_down": False, "is_zaban": False,
            "consec_limit_up_days": consec.get(c, 1),
        })
    for c in down_codes:
        rows.append({
            "ts_code": c, "board": "MAIN", "status": "limit_down", "limit_pct": 0.10,
            "limit_up_price": 11.0, "limit_down_price": 9.0,
            "is_limit_up": False, "is_limit_down": True, "is_zaban": False,
            "consec_limit_up_days": 0,
        })
    for c in zaban_codes:
        rows.append({
            "ts_code": c, "board": "MAIN", "status": "zaban", "limit_pct": 0.10,
            "limit_up_price": 11.0, "limit_down_price": 9.0,
            "is_limit_up": False, "is_limit_down": False, "is_zaban": True,
            "consec_limit_up_days": 0,
        })
    if rows:
        write_daily_fixture(settings, "limit_derived", d, rows)


def _write_daily_closes(settings, d: date, closes: Dict[str, float]) -> None:
    rows = [{"ts_code": c, "open": v, "high": v, "low": v, "close": v, "pre_close": v} for c, v in closes.items()]
    write_daily_fixture(settings, "daily", d, rows)


class TestComputeSentiment:
    def test_full_quota_strong_breadth_low_zaban(self, isolated_env):
        days = business_days(date(2026, 3, 2), 2)
        insert_trade_cal(isolated_env, days)
        d1 = days[1]
        up = [f"U{i}" for i in range(45)]
        down = [f"D{i}" for i in range(5)]
        zaban = [f"Z{i}" for i in range(5)]
        _write_limit_derived(isolated_env, d1, up, down, zaban, consec={"U0": 3})

        dash = compute_sentiment(d1, parquet_dir=isolated_env.parquet_dir)
        assert dash.limit_up_count == 45
        assert dash.limit_down_count == 5
        assert dash.zaban_count == 5
        assert dash.zaban_rate == pytest.approx(5 / 50)
        assert dash.max_consec_limit_up == 3
        assert dash.position_quota == FULL
        assert "阈值" in dash.quota_reason  # 免责声明必须出现在文案里

    def test_rest_quota_when_breadth_too_thin(self, isolated_env):
        days = business_days(date(2026, 3, 2), 2)
        insert_trade_cal(isolated_env, days)
        d1 = days[1]
        _write_limit_derived(isolated_env, d1, [f"U{i}" for i in range(8)], [f"D{i}" for i in range(2)], [])
        dash = compute_sentiment(d1, parquet_dir=isolated_env.parquet_dir)
        assert dash.position_quota == REST

    def test_rest_quota_when_zaban_rate_high(self, isolated_env):
        days = business_days(date(2026, 3, 2), 2)
        insert_trade_cal(isolated_env, days)
        d1 = days[1]
        _write_limit_derived(isolated_env, d1, [f"U{i}" for i in range(20)], [], [f"Z{i}" for i in range(25)])
        dash = compute_sentiment(d1, parquet_dir=isolated_env.parquet_dir)
        assert dash.zaban_rate == pytest.approx(25 / 45)
        assert dash.position_quota == REST

    def test_half_quota_moderate_breadth(self, isolated_env):
        days = business_days(date(2026, 3, 2), 2)
        insert_trade_cal(isolated_env, days)
        d1 = days[1]
        _write_limit_derived(isolated_env, d1, [f"U{i}" for i in range(25)], [f"D{i}" for i in range(3)], [f"Z{i}" for i in range(5)])
        dash = compute_sentiment(d1, parquet_dir=isolated_env.parquet_dir)
        assert dash.position_quota == HALF

    def test_premium_downgrade_full_to_half(self, isolated_env):
        days = business_days(date(2026, 3, 2), 2)
        insert_trade_cal(isolated_env, days)
        d0, d1 = days
        prev_up = [f"U{i}" for i in range(45)]
        _write_limit_derived(isolated_env, d0, prev_up, [], [])
        _write_daily_closes(isolated_env, d0, {c: 11.0 for c in prev_up})
        _write_daily_closes(isolated_env, d1, {c: 11.0 * 0.97 for c in prev_up})  # 今日平均 -3%
        # 今日盘面本身达到 FULL 门槛(用另一批代码,与昨日涨停股解耦)
        _write_limit_derived(isolated_env, d1, [f"V{i}" for i in range(45)], [f"D{i}" for i in range(2)], [f"Z{i}" for i in range(3)])

        dash = compute_sentiment(d1, parquet_dir=isolated_env.parquet_dir)
        assert dash.prev_limit_up_premium_avg == pytest.approx(-0.03, abs=1e-9)
        assert dash.prev_limit_up_sample == 45
        assert dash.position_quota == HALF  # 本该 FULL,被溢价警戒线下调一档

    def test_no_prev_limit_up_premium_is_none_not_zero(self, isolated_env):
        days = business_days(date(2026, 3, 2), 2)
        insert_trade_cal(isolated_env, days)
        d1 = days[1]
        _write_limit_derived(isolated_env, d1, [f"U{i}" for i in range(20)], [], [])
        dash = compute_sentiment(d1, parquet_dir=isolated_env.parquet_dir)
        assert dash.prev_limit_up_premium_avg is None
        assert dash.prev_limit_up_sample == 0

    def test_missing_data_degrades_gracefully_not_crash(self, isolated_env):
        insert_trade_cal(isolated_env, business_days(date(2026, 3, 2), 2))
        dash = compute_sentiment(date(2026, 3, 3), parquet_dir=isolated_env.parquet_dir)
        assert dash.limit_up_count == 0
        assert dash.limit_down_count == 0
        assert dash.zaban_rate == 0.0
        assert dash.position_quota == REST
