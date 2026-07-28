"""持仓票价格陈旧度判定单测(v1.4-①-B / §七 P0-2,`neckline/data/price_stale.py`)。

锁四件事:① 锚点是「全市场最近一个有 EOD 的交易日」而**不是今天**(否则盘中每只票都
被误报陈旧);② 停牌 vs 数据缺口 vs 不知道三个 reason 各走各的;③ 陈旧天数按**交易日**
数(不是自然日);④ 全市场无数据 / 空分区时**不产假警报**。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from neckline.data.price_stale import (
    REASON_DATA_GAP,
    REASON_SUSPENDED,
    REASON_UNKNOWN,
    market_anchor_date,
    resolve_price_stale,
)
from tests.conftest import business_days, insert_trade_cal, write_daily_fixture


@pytest.fixture
def env(isolated_env):
    """铺 12 个交易日的全市场行情:`600001.SH` 全程有行;`002036.SZ` 最后 3 个交易日缺行
    (模拟 0723 起停牌);`600002.SH` 也在最后 1 天缺行(用来分辨 reason)。"""
    s = isolated_env
    days = business_days(date(2026, 7, 10), 12)
    insert_trade_cal(s, days)
    for i, d in enumerate(days):
        rows = [{"ts_code": "600001.SH", "close": 10.0 + i, "open": 10.0, "high": 10.0,
                 "low": 10.0, "pre_close": 10.0, "vol": 1.0, "amount": 1.0}]
        if i < len(days) - 3:
            rows.append({"ts_code": "002036.SZ", "close": 7.2, "open": 7.0, "high": 7.3,
                         "low": 7.0, "pre_close": 7.1, "vol": 1.0, "amount": 1.0})
        if i < len(days) - 1:
            rows.append({"ts_code": "600002.SH", "close": 5.0, "open": 5.0, "high": 5.0,
                         "low": 5.0, "pre_close": 5.0, "vol": 1.0, "amount": 1.0})
        write_daily_fixture(s, "daily", d, rows)
    return s, days


def _write_suspend(settings, d: date, codes):
    from neckline.data.market_data import write_table_day

    write_table_day("suspend_d", d, pl.DataFrame({
        "ts_code": list(codes),
        "trade_date": [d] * len(codes),
        "suspend_type": ["S"] * len(codes),
    }), parquet_dir=settings.parquet_dir)


# —— 锚点 ——————————————————————————————————————————————————————————————

def test_anchor_is_last_market_day_not_today(env):
    """as_of 比最后一个有数据的交易日晚(= 盘中、EOD 还没落盘)→ 锚点回退到最后有数据那天。"""
    s, days = env
    assert market_anchor_date(days[-1], s.parquet_dir) == days[-1]
    # 往后推几天(还没落 EOD)→ 锚点仍是最后有数据的那天
    from datetime import timedelta
    insert_trade_cal(s, days + business_days(days[-1] + timedelta(days=1), 3))
    assert market_anchor_date(days[-1] + timedelta(days=3), s.parquet_dir) == days[-1]


def test_empty_partition_is_not_a_valid_anchor(env):
    """0 行空分区不算「有数据」(v1.3.5 脏基准同源教训:只判 exists() 会被空文件骗)。"""
    s, days = env
    from datetime import timedelta

    nxt = days[-1] + timedelta(days=7)          # 与既有窗口不重叠的将来交易日
    insert_trade_cal(s, days + [nxt])
    write_daily_fixture(s, "daily", nxt, [])    # 空分区
    assert market_anchor_date(nxt, s.parquet_dir) == days[-1]


def test_no_market_data_at_all_returns_empty(isolated_env):
    """全市场一天数据都没有 → 不产任何陈旧警报(不知道就别报)。"""
    days = business_days(date(2026, 7, 10), 5)
    insert_trade_cal(isolated_env, days)
    assert resolve_price_stale(["002036.SZ"], days[-1], isolated_env.parquet_dir) == {}


# —— 主判定 ——————————————————————————————————————————————————————————————

def test_fresh_code_not_in_result(env):
    """当日有 EOD 行的票压根不进返回集(调用方据此填 priceStale=null)。"""
    s, days = env
    out = resolve_price_stale(["600001.SH"], days[-1], s.parquet_dir)
    assert out == {}


def test_stale_days_counted_in_trading_days(env):
    """陈旧天数 = 最后成交日之后的**交易日**个数(含锚点日),不是自然日。"""
    s, days = env
    out = resolve_price_stale(["002036.SZ"], days[-1], s.parquet_dir)
    ps = out["002036.SZ"]
    assert ps.last_close_date == days[-4].strftime("%Y%m%d")
    assert ps.stale_days == 3                     # days[-3], days[-2], days[-1]


def test_reason_suspended_when_in_suspend_list(env):
    s, days = env
    _write_suspend(s, days[-1], ["002036.SZ"])
    out = resolve_price_stale(["002036.SZ", "600002.SH"], days[-1], s.parquet_dir)
    assert out["002036.SZ"].reason == REASON_SUSPENDED
    # 同日缺行但不在停牌名单 → 数据缺口,不冒充停牌
    assert out["600002.SH"].reason == REASON_DATA_GAP
    assert out["600002.SH"].stale_days == 1


def test_reason_unknown_when_suspend_table_missing(env):
    """停牌名单没落盘 → reason=unknown(如实说不知道,**不猜成 suspended**——猜错方向会
    让「时间退出判向挂起」这个豁免建立在臆测上)。"""
    s, days = env
    out = resolve_price_stale(["002036.SZ"], days[-1], s.parquet_dir)
    assert out["002036.SZ"].reason == REASON_UNKNOWN


def test_public_dict_shape_matches_contract(env):
    """契约三字段名逐字对齐 `PositionOut.priceStale`。"""
    s, days = env
    _write_suspend(s, days[-1], ["002036.SZ"])
    d = resolve_price_stale(["002036.SZ"], days[-1], s.parquet_dir)["002036.SZ"].to_public_dict()
    assert set(d) == {"staleDays", "lastCloseDate", "reason"}
    assert d["reason"] == "suspended" and isinstance(d["staleDays"], int)


def test_code_never_seen_reports_empty_last_close(env):
    """回看窗口内一行都没有的票 → lastCloseDate 留空(**不臆造**),staleDays 给下界。"""
    s, days = env
    ps = resolve_price_stale(["999999.SZ"], days[-1], s.parquet_dir)["999999.SZ"]
    assert ps.last_close_date == ""
    assert ps.stale_days >= 1
