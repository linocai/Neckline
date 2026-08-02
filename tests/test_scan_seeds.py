"""驱动种子生成 `neckline/scan/seeds.py` 单测(plan §五 V2-④)。

覆盖:①无现役包 → `None` + WARNING,不造默认阈值;②四类种子各自的资格判断
真的读包配置(阈值来自 `get_active_pack()`,不是模块字面量);③换包 → 种子集
跟着变(插槽真被消费的机器判据,同 V2-③ 验收原文);④成员范围 = 未筛选原始
成分;⑤确定性(同一天算两次逐位相同,`seed_key` 稳定)。
"""

from __future__ import annotations

import json
import logging
from datetime import date

import pytest

from neckline.scan import seeds
from neckline.selection.pack import activate_pack
from tests.conftest import (
    insert_stock_basic,
    insert_trade_cal,
    seed_industry_strength,
    write_daily_fixture,
    write_flat_parquet,
)

D0 = date(2024, 4, 8)

_MANIFEST = {
    "pack_version": "test-pack-v1",
    "name": "测试包",
    "date": "2024-01-01",
    "engine_api_version": 1,
    "evidence_ref": [],
}


def _config(**overrides) -> dict:
    seeds_cfg = {
        "hot_industry_seed": {"max_rank": 10, "require_strength_day": True},
        "surging_concept_seed": {"min_pct_change": 5.0},
        "limit_cluster_seed": {"min_cluster_size": 2, "min_consecutive_days": 2},
        "anomaly_cluster_seed": {"min_volume_ratio": 3.0, "min_cluster_members": 2},
    }
    seeds_cfg.update(overrides)
    return {
        "seeds": seeds_cfg,
        "tier": {"weights": {"a": 1.0}, "dims": ["a"]},
    }


def _activate(env, *, pack_version: str = "test-pack-v1", **overrides) -> None:
    manifest = dict(_MANIFEST, pack_version=pack_version)
    activate_pack(manifest, _config(**overrides), via="seed", db_path=env.db_path)


# ══════════════════════════════════════════════════════════════════════════
# ① 无现役包
# ══════════════════════════════════════════════════════════════════════════

def test_no_active_pack_returns_none_and_warns(isolated_env, caplog):
    env = isolated_env
    insert_trade_cal(env, [D0])
    with caplog.at_level(logging.WARNING):
        result = seeds.generate_seeds(D0, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert result is None
    assert any("无现役策略包" in r.message for r in caplog.records)


# ══════════════════════════════════════════════════════════════════════════
# ② 四类种子各自的资格判断
# ══════════════════════════════════════════════════════════════════════════

def test_hot_industry_seed_gated_by_pack_and_member_codes_from_stock_basic(isolated_env):
    env = isolated_env
    dates = insert_trade_cal(env, [D0]) or [D0]
    insert_stock_basic(env, [
        {"ts_code": f"60000{i}.SH", "industry": "半导体"} for i in range(1, 6)
    ] + [
        {"ts_code": f"60001{i}.SH", "industry": "白酒"} for i in range(1, 6)
    ])
    write_daily_fixture(env, "daily", D0, [
        {"ts_code": c, "open": 10, "high": 10, "low": 10, "close": 10 + i * 0.3, "pre_close": 10, "vol": 1.0, "amount": 1.0}
        for i, c in enumerate([f"60000{i}.SH" for i in range(1, 6)])
    ] + [
        {"ts_code": c, "open": 10, "high": 10, "low": 10, "close": 10 - 0.01, "pre_close": 10, "vol": 1.0, "amount": 1.0}
        for c in [f"60001{i}.SH" for i in range(1, 6)]
    ])
    seed_industry_strength(env, [D0])
    _activate(env)

    result = seeds.generate_seeds(D0, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert result is not None
    labels = {s.label for s in result.hot_industry}
    # 半导体当日中位数远高于白酒 → 是当日强度日,白酒不是(require_strength_day=True
    # 挡住);只有前者应达标,不是"两个行业都进"或"两个都不进"。
    assert labels == {"半导体"}
    hot = next(s for s in result.hot_industry if s.label == "半导体")
    assert set(hot.member_codes) == {f"60000{i}.SH" for i in range(1, 6)}


def test_surging_concept_seed_gated_by_pct_change_and_hygiene(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    insert_stock_basic(env, [{"ts_code": "600001.SH"}, {"ts_code": "600002.SH"}])
    write_flat_parquet(env, "ths_index.parquet", [
        {"ts_code": "885001.TI", "name": "国产替代"},
        {"ts_code": "885002.TI", "name": "融资融券"},   # 宽基标签,应被卫生线剔除
    ])
    write_flat_parquet(env, "ths_member.parquet", [
        {"index_code": "885001.TI", "con_code": "600001.SH"},
        {"index_code": "885002.TI", "con_code": "600002.SH"},
    ])
    write_flat_parquet(env, "ths_daily.parquet", [
        {"ts_code": "885001.TI", "trade_date": D0, "pct_change": 8.0},
        {"ts_code": "885002.TI", "trade_date": D0, "pct_change": 9.0},   # 涨幅更高但被卫生线剔除
    ])
    _activate(env)

    result = seeds.generate_seeds(D0, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert [s.label for s in result.surging_concept] == ["国产替代"]
    assert result.surging_concept[0].member_codes == ("600001.SH",)


def test_limit_cluster_seed_uses_max_consecutive_days_for_gating(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    from neckline.db import connection

    with connection(env.db_path) as conn:
        for code, cdays in [("600001.SH", 3), ("600002.SH", 1)]:
            conn.execute(
                "INSERT OR REPLACE INTO limit_cluster_daily "
                "(trade_date, cluster_key, ts_code, cluster_kind, cluster_size, consecutive_days, "
                " anchor_industry, anchor_concept, computed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (D0.strftime("%Y%m%d"), "K1", code, "same_day", 2, cdays, "半导体", None, "2024-01-01T00:00:00+00:00"),
            )
    _activate(env)
    result = seeds.generate_seeds(D0, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert len(result.limit_cluster) == 1
    seed = result.limit_cluster[0]
    assert seed.evidence["consecutive_days_max"] == 3
    assert set(seed.member_codes) == {"600001.SH", "600002.SH"}


def test_anomaly_cluster_seed_clusters_qualifying_stocks_by_industry(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    insert_stock_basic(env, [
        {"ts_code": "600001.SH", "industry": "半导体"},
        {"ts_code": "600002.SH", "industry": "半导体"},
        {"ts_code": "600003.SH", "industry": "白酒"},   # 孤身异动,凑不够 min_cluster_members
    ])
    write_daily_fixture(env, "daily_basic", D0, [
        {"ts_code": "600001.SH", "volume_ratio": 5.0},
        {"ts_code": "600002.SH", "volume_ratio": 4.0},
        {"ts_code": "600003.SH", "volume_ratio": 6.0},
    ])
    _activate(env)
    result = seeds.generate_seeds(D0, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert len(result.anomaly_cluster) == 1
    assert result.anomaly_cluster[0].label == "半导体"
    assert set(result.anomaly_cluster[0].member_codes) == {"600001.SH", "600002.SH"}


# ══════════════════════════════════════════════════════════════════════════
# ③ 插槽真被消费:换包 → 种子集跟着变
# ══════════════════════════════════════════════════════════════════════════

def test_switching_pack_changes_seed_set(isolated_env):
    """同一份输入数据,换一个包(改一个阈值)→ 种子集跟着变(V2-③/④ 共同验收
    判据:插槽不是空架子)。"""
    env = isolated_env
    insert_trade_cal(env, [D0])
    insert_stock_basic(env, [{"ts_code": "600001.SH"}, {"ts_code": "600002.SH"}])
    write_flat_parquet(env, "ths_index.parquet", [{"ts_code": "885001.TI", "name": "国产替代"}])
    write_flat_parquet(env, "ths_member.parquet", [{"index_code": "885001.TI", "con_code": "600001.SH"}])
    write_flat_parquet(env, "ths_daily.parquet", [{"ts_code": "885001.TI", "trade_date": D0, "pct_change": 6.0}])

    _activate(env, pack_version="pack-strict", **{"surging_concept_seed": {"min_pct_change": 5.0}})
    strict_result = seeds.generate_seeds(D0, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert len(strict_result.surging_concept) == 1

    _activate(env, pack_version="pack-loose", **{"surging_concept_seed": {"min_pct_change": 20.0}})
    loose_result = seeds.generate_seeds(D0, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert len(loose_result.surging_concept) == 0
    assert loose_result.pack_version == "pack-loose"
    assert strict_result.pack_version == "pack-strict"


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 确定性
# ══════════════════════════════════════════════════════════════════════════

def test_generate_seeds_is_deterministic(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    insert_stock_basic(env, [{"ts_code": "600001.SH", "industry": "半导体"}])
    seed_industry_strength(env, [D0])
    write_daily_fixture(env, "daily", D0, [
        {"ts_code": "600001.SH", "open": 10, "high": 10, "low": 10, "close": 10.5, "pre_close": 10, "vol": 1.0, "amount": 1.0}
    ])
    _activate(env)

    first = seeds.generate_seeds(D0, db_path=env.db_path, parquet_dir=env.parquet_dir)
    second = seeds.generate_seeds(D0, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert first.counts() == second.counts()
    assert [s.seed_key for s in first.all_seeds()] == [s.seed_key for s in second.all_seeds()]
