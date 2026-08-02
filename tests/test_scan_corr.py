"""滚动相关预计算表 `corr_matrix_daily` 单测(plan §五 V2-④)。

覆盖:①候选对只来自簇成员∪概念成分(不做全市场 N²);②相关系数正确性
(完全同向≈1、完全反向≈-1、常数序列→NULL);③样本不足→NULL(禁写 0);
④`code_a<code_b` 排序不重复存;⑤规模上限跳过超大 scope;⑥三路等价
(批 vs 逐日 vs 落表读回)+ 幂等;⑦窗口值等于声明常量。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import List

import polars as pl
import pytest

from neckline.config import Settings
from neckline.db import connection, init_schema
from neckline.scan import corr
from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, write_daily_fixture


def _insert_cluster_row(
    env: Settings, trade_date: date, cluster_key: str, ts_code: str,
    *, cluster_size: int, cluster_kind: str = "same_day",
) -> None:
    init_schema(env.db_path)
    with connection(env.db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO limit_cluster_daily "
            "(trade_date, cluster_key, ts_code, cluster_kind, cluster_size, consecutive_days, "
            " anchor_industry, anchor_concept, computed_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (trade_date.strftime("%Y%m%d"), cluster_key, ts_code, cluster_kind, cluster_size, 1,
             "测试行业", None, "2024-01-01T00:00:00+00:00"),
        )


def _seed_price_series(env: Settings, codes_rets: dict, dates: List[date]) -> None:
    """`codes_rets = {ts_code: [ret_1d, ...]}`(长度须 == len(dates)),生成对应
    close/pre_close 序列并落 `daily` 分区(第 0 天 pre_close 取 10.0 起点)。"""
    prices = {}
    for code, rets in codes_rets.items():
        series = [10.0]
        for r in rets:
            series.append(series[-1] * (1 + r))
        prices[code] = series   # series[i] = 第 i 天 close(series[0] 是虚拟起点,当第 0 天 pre_close)

    for i, d in enumerate(dates):
        rows = []
        for code in codes_rets:
            pre = prices[code][i]
            close = prices[code][i + 1]
            rows.append({"ts_code": code, "open": close, "high": close, "low": close,
                         "close": close, "pre_close": pre, "vol": 1000.0, "amount": 1000.0})
        write_daily_fixture(env, "daily", d, rows)


@pytest.fixture
def price_env(isolated_env):
    env = isolated_env
    dates = business_days(date(2024, 1, 2), 25)
    insert_trade_cal(env, dates)
    insert_stock_basic(env, [
        {"ts_code": c} for c in ["600001.SH", "600002.SH", "600003.SH", "600004.SH"]
    ])
    rets_a = [0.01, -0.02, 0.03, 0.015, -0.01, 0.02, -0.005, 0.01, 0.0, -0.02,
              0.03, -0.01, 0.02, 0.01, -0.03, 0.02, 0.01, -0.01, 0.02, -0.01,
              0.01, -0.02, 0.03, 0.01, -0.01][: len(dates)]
    _seed_price_series(env, {
        "600001.SH": rets_a,
        "600002.SH": list(rets_a),               # 完全同向 → corr≈1
        "600003.SH": [-r for r in rets_a],        # 完全反向 → corr≈-1
        "600004.SH": [0.0 for _ in rets_a],       # 常数序列(恒定收盘)→ std=0
    }, dates)
    return env, dates


# ══════════════════════════════════════════════════════════════════════════
# ① 候选对只来自簇成员 ∪ 概念成分
# ══════════════════════════════════════════════════════════════════════════

def test_scope_membership_from_cluster_and_concept_only(price_env):
    env, dates = price_env
    d = dates[-1]
    _insert_cluster_row(env, d, "clusterkeyabc", "600001.SH", cluster_size=2)
    _insert_cluster_row(env, d, "clusterkeyabc", "600002.SH", cluster_size=2)

    scopes = corr.build_scope_membership(d, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert scopes == {"clusterkeyabc": ["600001.SH", "600002.SH"]}   # 无概念数据,只有簇一路


# ══════════════════════════════════════════════════════════════════════════
# ② 相关系数正确性 + ③ 常数序列/样本不足 → NULL
# ══════════════════════════════════════════════════════════════════════════

def test_perfectly_aligned_and_inverted_and_constant_series(price_env):
    env, dates = price_env
    d = dates[-1]
    for code in ["600001.SH", "600002.SH", "600003.SH", "600004.SH"]:
        _insert_cluster_row(env, d, "K1", code, cluster_size=4)

    stats = corr.refresh_corr_matrix([d], db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert stats["rows"] == 6   # C(4,2)

    df = corr.load_corr_matrix(d, db_path=env.db_path)
    by_pair = {(r["code_a"], r["code_b"]): r for r in df.iter_rows(named=True)}

    r_12 = by_pair[("600001.SH", "600002.SH")]
    assert r_12["corr"] == pytest.approx(1.0, abs=1e-6)
    assert r_12["n_obs"] == corr.PRICE_WINDOW_DAYS   # 只取滚动窗口内的观测,不是全部 25 天历史
    assert r_12["window"] == corr.PRICE_WINDOW_DAYS

    r_13 = by_pair[("600001.SH", "600003.SH")]
    assert r_13["corr"] == pytest.approx(-1.0, abs=1e-6)

    # 600004(常数序列)与任何票配对 → corr=NULL,不是 0(std=0,数学上未定义)
    r_14 = by_pair[("600001.SH", "600004.SH")]
    assert r_14["corr"] is None
    assert r_14["n_obs"] == corr.PRICE_WINDOW_DAYS   # 样本量本身是够的,是"算不出"不是"样本不足"


def test_insufficient_observations_yields_null_not_zero(isolated_env):
    """窗口内两票重叠有效观测 < MIN_OBS_FOR_CORR → corr=NULL(禁写 0)。"""
    env = isolated_env
    dates = business_days(date(2024, 1, 2), corr.MIN_OBS_FOR_CORR - 2)   # 明显不足窗口的短历史
    insert_trade_cal(env, dates)
    insert_stock_basic(env, [{"ts_code": "600001.SH"}, {"ts_code": "600002.SH"}])
    _seed_price_series(env, {
        "600001.SH": [0.01] * len(dates),
        "600002.SH": [0.02] * len(dates),
    }, dates)
    d = dates[-1]
    _insert_cluster_row(env, d, "K1", "600001.SH", cluster_size=2)
    _insert_cluster_row(env, d, "K1", "600002.SH", cluster_size=2)

    corr.refresh_corr_matrix([d], db_path=env.db_path, parquet_dir=env.parquet_dir)
    df = corr.load_corr_matrix(d, db_path=env.db_path)
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["corr"] is None
    assert row["n_obs"] < corr.MIN_OBS_FOR_CORR


# ══════════════════════════════════════════════════════════════════════════
# ④ code_a<code_b,不存两遍
# ══════════════════════════════════════════════════════════════════════════

def test_pairs_are_stored_once_with_code_a_less_than_code_b(price_env):
    env, dates = price_env
    d = dates[-1]
    _insert_cluster_row(env, d, "K1", "600002.SH", cluster_size=2)
    _insert_cluster_row(env, d, "K1", "600001.SH", cluster_size=2)   # 故意倒序插入

    corr.refresh_corr_matrix([d], db_path=env.db_path, parquet_dir=env.parquet_dir)
    df = corr.load_corr_matrix(d, db_path=env.db_path)
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["code_a"] == "600001.SH" and row["code_b"] == "600002.SH"


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 规模上限:超大 scope 直接跳过
# ══════════════════════════════════════════════════════════════════════════

def test_oversized_scope_is_skipped(price_env):
    env, dates = price_env
    d = dates[-1]
    codes = [f"60{i:04d}.SH" for i in range(corr.MAX_SCOPE_MEMBERS_FOR_CORR + 1)]
    for c in codes:
        _insert_cluster_row(env, d, "HUGE", c, cluster_size=len(codes))
    scopes = corr.build_scope_membership(d, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert "HUGE" not in scopes


def test_scope_with_no_price_data_is_counted_not_silently_dropped(price_env):
    """scope 成员在候选对集合里,但价格窗口完全没有它们的数据(极端数据缺口)——
    `compute_corr_for_day` 会跳过这个 scope,`refresh_corr_matrix` 必须在
    `scopes_skipped_no_price` 里记一笔,不能无声无息地消失。"""
    env, dates = price_env
    d = dates[-1]
    _insert_cluster_row(env, d, "GHOST", "900001.SH", cluster_size=2)
    _insert_cluster_row(env, d, "GHOST", "900002.SH", cluster_size=2)   # 两票均无 daily 行情

    stats = corr.refresh_corr_matrix([d], db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert stats["scopes"] >= 1
    assert stats["scopes_skipped_no_price"] == 1
    assert corr.load_corr_matrix(d, db_path=env.db_path).filter(pl.col("scope_key") == "GHOST").is_empty()


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 三路等价 + 幂等
# ══════════════════════════════════════════════════════════════════════════

def test_bulk_vs_day_by_day_vs_readback(price_env):
    """三路等价:全量批算(一次调用 3 天)≡ 逐日循环 ≡ 落表读回,随机 3 日
    (plan §五 V2-④ 验收原文)——第三天故意不给任何簇,验证批量路径对"合法零
    scope"的一天不出岔子(不会串到前后两天的结果里)。"""
    env, dates = price_env
    d0, d1, d2 = dates[-3], dates[-2], dates[-1]
    for d in (d0, d1):
        _insert_cluster_row(env, d, f"K_{d.isoformat()}", "600001.SH", cluster_size=2)
        _insert_cluster_row(env, d, f"K_{d.isoformat()}", "600002.SH", cluster_size=2)
    # d2:不插入任何簇 → 候选对集合为空 → 合法零行

    days = [d0, d1, d2]
    corr.refresh_corr_matrix(days, db_path=env.db_path, parquet_dir=env.parquet_dir)
    bulk = {d: corr.load_corr_matrix(d, db_path=env.db_path).sort(["scope_key", "code_a", "code_b"]) for d in days}

    with connection(env.db_path) as conn:
        conn.execute("DELETE FROM corr_matrix_daily")
    for d in days:
        corr.refresh_corr_matrix([d], db_path=env.db_path, parquet_dir=env.parquet_dir)
    daybyday = {d: corr.load_corr_matrix(d, db_path=env.db_path).sort(["scope_key", "code_a", "code_b"]) for d in days}

    for d in days:
        assert bulk[d].equals(daybyday[d]), f"{d} 批算与逐日结果不一致"
    assert bulk[d2].is_empty()

    # 幂等:再跑一次不产生重复行
    corr.refresh_corr_matrix([d1], db_path=env.db_path, parquet_dir=env.parquet_dir)
    with connection(env.db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM corr_matrix_daily WHERE trade_date=?", (d1.strftime("%Y%m%d"),)
        ).fetchone()[0]
    assert n == daybyday[d1].height


def test_no_scopes_is_empty_not_error(isolated_env):
    env = isolated_env
    d = date(2024, 1, 2)
    insert_trade_cal(env, [d])
    stats = corr.refresh_corr_matrix([d], db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert stats["rows"] == 0
    assert corr.load_corr_matrix(d, db_path=env.db_path).is_empty()
