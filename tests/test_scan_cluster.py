"""涨停共振簇预计算表 `limit_cluster_daily` 单测(plan §五 V2-④)。

覆盖:①同日簇按行业/概念聚类(含跨行业的概念簇、同票多簇);②连板簇是同日簇的
子集(`consecutive_days>=2`门槛);③孤身涨停不成簇(`MIN_CLUSTER_SIZE`);
④`cluster_key` 确定性(`zlib.crc32`,跨进程/跨天不冲突,`same_day`/`consecutive`
不撞 key);⑤板块池卫生线剔除宽基概念,不把"全市场"当一个簇;⑥落表读回与
批算逐位一致(三路等价的"批 vs 逐日 vs 读回");⑦空表/无现役数据的降级。
"""

from __future__ import annotations

import zlib
from datetime import date

import polars as pl
import pytest

from neckline.db import connection
from neckline.scan import cluster
from tests.conftest import (
    insert_stock_basic,
    insert_trade_cal,
    write_daily_fixture,
    write_flat_parquet,
)

D0 = date(2024, 3, 4)
D1 = date(2024, 3, 5)
D2 = date(2024, 3, 6)


def _limit_row(code: str, consec: int, is_limit_up: bool = True) -> dict:
    return {
        "ts_code": code,
        "board": "MAIN",
        "status": "limit_up" if is_limit_up else None,
        "limit_pct": 0.10,
        "limit_up_price": 11.0,
        "limit_down_price": 9.0,
        "is_limit_up": is_limit_up,
        "is_limit_down": False,
        "is_zaban": False,
        "consec_limit_up_days": consec,
    }


def _seed_basic(env) -> None:
    insert_trade_cal(env, [D0, D1, D2])
    insert_stock_basic(env, [
        {"ts_code": "600001.SH", "industry": "半导体"},
        {"ts_code": "600002.SH", "industry": "半导体"},
        {"ts_code": "600003.SH", "industry": "半导体"},
        {"ts_code": "600004.SH", "industry": "白酒"},
        {"ts_code": "600005.SH", "industry": "白酒"},
    ])
    # 概念板块「国产替代」跨行业成分 = 600001(半导体) + 600004(白酒)
    write_flat_parquet(env, "ths_index.parquet", [{"ts_code": "885001.TI", "name": "国产替代"}])
    write_flat_parquet(env, "ths_member.parquet", [
        {"index_code": "885001.TI", "con_code": "600001.SH"},
        {"index_code": "885001.TI", "con_code": "600004.SH"},
    ])


# ══════════════════════════════════════════════════════════════════════════
# ① 同日簇:行业 + 概念两个维度,同票可属多簇
# ══════════════════════════════════════════════════════════════════════════

def test_same_day_cluster_by_industry_and_concept(isolated_env):
    env = isolated_env
    _seed_basic(env)
    write_daily_fixture(env, "limit_derived", D0, [
        _limit_row("600001.SH", 1),
        _limit_row("600002.SH", 1),
        _limit_row("600003.SH", 1),
        _limit_row("600004.SH", 1),
        _limit_row("600005.SH", 1, is_limit_up=False),   # 未涨停,不参与聚类
    ])

    stats = cluster.refresh_limit_clusters([D0], db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert stats["days"] == 1
    assert stats["same_day_clusters"] == 2   # 半导体(3只) + 国产替代(2只);白酒只1只不成簇
    assert stats["consecutive_clusters"] == 0

    df = cluster.load_limit_clusters(D0, db_path=env.db_path)
    assert set(df["cluster_kind"].unique().to_list()) == {"same_day"}

    industry_rows = df.filter(pl.col("anchor_industry") == "半导体")
    assert set(industry_rows["ts_code"].to_list()) == {"600001.SH", "600002.SH", "600003.SH"}
    assert industry_rows["cluster_size"].unique().to_list() == [3]

    concept_rows = df.filter(pl.col("anchor_concept") == "885001.TI")
    assert set(concept_rows["ts_code"].to_list()) == {"600001.SH", "600004.SH"}
    assert concept_rows["cluster_size"].unique().to_list() == [2]

    # 600001.SH 同时属于行业簇与概念簇(两个不同 cluster_key)
    rows_600001 = df.filter(pl.col("ts_code") == "600001.SH")
    assert rows_600001.height == 2
    assert rows_600001["cluster_key"].n_unique() == 2

    # 白酒行业只有 600004 涨停(600005 未涨停)→ 不成簇
    assert df.filter(pl.col("anchor_industry") == "白酒").is_empty()


def test_lone_limit_up_does_not_form_cluster(isolated_env):
    """孤身涨停(行业内只此一家、且不在任何过闸概念里)不构成"共振",不落一行。"""
    env = isolated_env
    _seed_basic(env)
    write_daily_fixture(env, "limit_derived", D0, [_limit_row("600005.SH", 1)])   # 白酒,仅此一家

    stats = cluster.refresh_limit_clusters([D0], db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert stats["rows"] == 0
    assert cluster.load_limit_clusters(D0, db_path=env.db_path).is_empty()


# ══════════════════════════════════════════════════════════════════════════
# ② 连板簇是同日簇的子集
# ══════════════════════════════════════════════════════════════════════════

def test_consecutive_cluster_is_subset_with_streak_ge_2(isolated_env):
    env = isolated_env
    _seed_basic(env)
    write_daily_fixture(env, "limit_derived", D1, [
        _limit_row("600001.SH", 2),   # 连板第2天
        _limit_row("600002.SH", 2),   # 连板第2天
        _limit_row("600003.SH", 1),   # 今天才第1天涨停,不进 consecutive 簇
    ])

    cluster.refresh_limit_clusters([D1], db_path=env.db_path, parquet_dir=env.parquet_dir)
    df = cluster.load_limit_clusters(D1, db_path=env.db_path)

    same_day = df.filter(pl.col("cluster_kind") == "same_day")
    assert set(same_day.filter(pl.col("anchor_industry") == "半导体")["ts_code"].to_list()) == {
        "600001.SH", "600002.SH", "600003.SH"
    }

    consecutive = df.filter(pl.col("cluster_kind") == "consecutive")
    consec_industry = consecutive.filter(pl.col("anchor_industry") == "半导体")
    assert set(consec_industry["ts_code"].to_list()) == {"600001.SH", "600002.SH"}
    assert consec_industry["cluster_size"].unique().to_list() == [2]
    # consecutive_days 是成员自己的量,不是簇聚合值
    per_code = dict(zip(consec_industry["ts_code"].to_list(), consec_industry["consecutive_days"].to_list()))
    assert per_code == {"600001.SH": 2, "600002.SH": 2}


# ══════════════════════════════════════════════════════════════════════════
# ③④ cluster_key 确定性:crc32、same_day/consecutive 不撞 key
# ══════════════════════════════════════════════════════════════════════════

def test_cluster_key_is_deterministic_crc32():
    k1 = cluster.make_cluster_key("20240304", "same_day", "industry", "半导体")
    k2 = cluster.make_cluster_key("20240304", "same_day", "industry", "半导体")
    assert k1 == k2
    expected = format(zlib.crc32("20240304|same_day:industry:半导体".encode("utf-8")), "08x")
    assert k1 == expected


def test_cluster_key_differs_by_cluster_kind():
    """同一 (trade_date, anchor) 下 same_day 与 consecutive 两种簇不撞 key
    (`cluster_kind` 已编进被哈希的字符串)。"""
    k_same = cluster.make_cluster_key("20240304", "same_day", "industry", "半导体")
    k_consec = cluster.make_cluster_key("20240304", "consecutive", "industry", "半导体")
    assert k_same != k_consec


def test_refresh_twice_is_deterministic_and_idempotent(isolated_env):
    """同一天跑两次批算,cluster_key 与全部列逐位相同(幂等 upsert,不重复行)。"""
    env = isolated_env
    _seed_basic(env)
    write_daily_fixture(env, "limit_derived", D0, [
        _limit_row("600001.SH", 1), _limit_row("600002.SH", 1), _limit_row("600003.SH", 1),
    ])
    cluster.refresh_limit_clusters([D0], db_path=env.db_path, parquet_dir=env.parquet_dir)
    first = cluster.load_limit_clusters(D0, db_path=env.db_path).sort(["cluster_key", "ts_code"])
    cluster.refresh_limit_clusters([D0], db_path=env.db_path, parquet_dir=env.parquet_dir)
    second = cluster.load_limit_clusters(D0, db_path=env.db_path).sort(["cluster_key", "ts_code"])
    assert first.equals(second)
    with connection(env.db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM limit_cluster_daily WHERE trade_date=?", ("20240304",)).fetchone()[0]
    assert n == first.height   # 未重复插入


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 板块池卫生线:宽基/资格类概念不进聚类候选
# ══════════════════════════════════════════════════════════════════════════

def test_broad_concept_excluded_by_board_pool_hygiene(isolated_env):
    """"融资融券"这类宽基标签板块被 `board_pool` 剔除,不会把"全市场"当一个簇。"""
    env = isolated_env
    _seed_basic(env)
    write_flat_parquet(env, "ths_index.parquet", [
        {"ts_code": "885001.TI", "name": "国产替代"},
        {"ts_code": "885002.TI", "name": "融资融券"},
    ])
    write_flat_parquet(env, "ths_member.parquet", [
        {"index_code": "885001.TI", "con_code": "600001.SH"},
        {"index_code": "885001.TI", "con_code": "600004.SH"},
        {"index_code": "885002.TI", "con_code": "600002.SH"},
        {"index_code": "885002.TI", "con_code": "600003.SH"},
    ])
    concept_of = cluster.concept_membership_map(parquet_dir=env.parquet_dir)
    assert "885002.TI" not in concept_of.get("600002.SH", [])
    assert "885001.TI" in concept_of.get("600001.SH", [])


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 三路等价:批量多日 vs 逐日循环 vs 落表读回
# ══════════════════════════════════════════════════════════════════════════

def test_bulk_vs_day_by_day_vs_readback_are_identical(isolated_env):
    """三路等价:全量批算(一次调用 3 天)≡ 逐日循环 ≡ 落表读回,随机 3 日
    (plan §五 V2-④ 验收原文),三天故意给三种不同形状——同日簇、连板簇变化、
    与"当日合法零簇"——覆盖批量路径对"稀疏输出"的处理不出岔子。

    **比较时排除 `computed_at`**(§七 P1-36 定案):该列是"这行何时算的"审计戳
    (秒精度墙钟,每次调用 `_now()` 重新生成),不是业务判据列——批算与逐日循环
    是**两次独立调用**,只要跨越了墙钟的秒边界,`computed_at` 就会合法地不同,
    与业务列(cluster_key/ts_code/cluster_size/...)是否一致无关。同一坑
    `tests/test_industry_strength_store.py` 早有先例(`{k:v for k,v in
    r.items() if k!="computed_at"}`),此处用 `.drop("computed_at")` 复刻同一
    修法,不是放宽断言(业务列仍要求逐位相同)。"""
    env = isolated_env
    _seed_basic(env)
    write_daily_fixture(env, "limit_derived", D0, [
        _limit_row("600001.SH", 1), _limit_row("600002.SH", 1), _limit_row("600003.SH", 1),
    ])
    write_daily_fixture(env, "limit_derived", D1, [
        _limit_row("600001.SH", 2), _limit_row("600002.SH", 2),
    ])
    write_daily_fixture(env, "limit_derived", D2, [
        _limit_row("600004.SH", 1, is_limit_up=False),   # 当日无涨停 → 合法零簇
    ])

    days = [D0, D1, D2]

    # 路① 全量批算(一次调用三天)
    cluster.refresh_limit_clusters(days, db_path=env.db_path, parquet_dir=env.parquet_dir)
    bulk = {d: cluster.load_limit_clusters(d, db_path=env.db_path).sort(["cluster_key", "ts_code"]) for d in days}

    # 清库重来,路② 逐日循环
    with connection(env.db_path) as conn:
        conn.execute("DELETE FROM limit_cluster_daily")
    for d in days:
        cluster.refresh_limit_clusters([d], db_path=env.db_path, parquet_dir=env.parquet_dir)
    daybyday = {d: cluster.load_limit_clusters(d, db_path=env.db_path).sort(["cluster_key", "ts_code"]) for d in days}

    # 路③ 落表读回已经是上面两次 load_limit_clusters 本身(SELECT 直读,不做二次判断)
    for d in days:
        assert bulk[d].drop("computed_at").equals(daybyday[d].drop("computed_at")), (
            f"{d} 批算与逐日结果不一致(业务列,已排除审计戳 computed_at)"
        )
    assert bulk[D2].is_empty()   # D2 合法零簇,批量路径没有把它悄悄凑出内容


# ══════════════════════════════════════════════════════════════════════════
# ⑦ 降级:无涨停数据的一天
# ══════════════════════════════════════════════════════════════════════════

def test_no_limit_derived_data_is_empty_not_error(isolated_env):
    env = isolated_env
    _seed_basic(env)
    stats = cluster.refresh_limit_clusters([D0], db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert stats == {"days": 1, "rows": 0, "same_day_clusters": 0, "consecutive_clusters": 0}
    assert cluster.load_limit_clusters(D0, db_path=env.db_path).is_empty()
