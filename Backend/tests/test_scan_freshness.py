"""市场扫描层新鲜度 `neckline/scan/freshness.py` 单测(plan §五 V2-④「保险丝」节)。

覆盖:①完全无数据 → unknown 哨兵 + stale;②当日已有数据 → lag=0 不 stale;
③落后 N 个交易日 → lag=N 且 stale;④`to_public_dict()` 三键契约(键名/None
语义)。
"""

from __future__ import annotations

from datetime import date

from neckline.db import connection, init_schema
from neckline.scan import freshness as fr
from tests.conftest import business_days, insert_trade_cal


def _insert_corr_row(env, trade_date: date) -> None:
    init_schema(env.db_path)
    with connection(env.db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO corr_matrix_daily "
            "(trade_date, scope_key, code_a, code_b, window, corr, n_obs, computed_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (trade_date.strftime("%Y%m%d"), "K1", "600001.SH", "600002.SH", 20, 0.5, 20, "2024-01-01T00:00:00+00:00"),
        )


def test_no_data_is_unknown_and_stale(isolated_env):
    env = isolated_env
    status = fr.scan_layer_status(date(2024, 4, 8), db_path=env.db_path)
    assert status.unavailable
    assert status.lag_days == fr.SCAN_LAYER_LAG_UNKNOWN
    assert status.stale is True
    assert status.to_public_dict() == {"scanLayerDate": None, "scanLayerLagDays": -1, "scanLayerStale": True}


def test_same_day_data_is_fresh(isolated_env):
    env = isolated_env
    d = date(2024, 4, 8)
    insert_trade_cal(env, [d])
    _insert_corr_row(env, d)
    status = fr.scan_layer_status(d, db_path=env.db_path)
    assert status.lag_days == 0
    assert status.stale is False
    assert status.to_public_dict()["scanLayerDate"] == "20240408"


def test_lagging_data_is_stale_with_zero_tolerance(isolated_env):
    env = isolated_env
    days = business_days(date(2024, 4, 1), 10)
    insert_trade_cal(env, days)
    _insert_corr_row(env, days[-3])
    status = fr.scan_layer_status(days[-1], db_path=env.db_path)
    assert status.lag_days == 2
    assert status.stale is True   # 零容忍:>0 即 stale
