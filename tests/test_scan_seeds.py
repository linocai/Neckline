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
from pathlib import Path

import pytest

from neckline.scan import seeds
from neckline.selection.pack import activate_pack, load_pack_file
from tests.conftest import (
    insert_stock_basic,
    insert_trade_cal,
    seed_industry_strength,
    write_daily_fixture,
    write_flat_parquet,
)

D0 = date(2024, 4, 8)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_K4_PACK_FILE = _REPO_ROOT / "packs" / "K4-pack.json"
_K7_PACK_FILE = _REPO_ROOT / "packs" / "K7-pack.json"

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
# V2-③-K7 补的「真实版本」单测(承 V2-③ 完工记录登记的测试局限:「插槽真被
# 消费」当时只能用合成迷你包代理验证,待 ④ 落地后应在真实种子集上重跑——④
# 已完工,`generate_seeds()` 已存在。本测试改用仓库里两份**真实**包文件
# `packs/K4-pack.json`/`packs/K7-pack.json`,不再是合成配置)。
#
# ③-K7-E 定案:「K7 的变更集中在排序键(`intel_rank_priority`)、Tier 权重与
# `stage_scores`」——四类驱动种子的资格判断阈值(四个 scan-seed 原语)承
# K4-pack **逐字不变**。本测试因此断言 `generate_seeds()` 在两份真实包下产出
# 逐位相同(这是"没有悄悄改变"的证明);"排序 dims 确实不同"那半是
# `intel_rank_priority` 的职责(`generate_seeds()` 从不读它),覆盖在
# `tests/test_selection_pack.py::
# test_real_k4_pack_vs_k7_pack_intel_rank_priority_dims_and_ranking_differ`。
# "Tier 序" 那半留给 ⑥ 落地后用真实 Tier 引擎重跑。
# ══════════════════════════════════════════════════════════════════════════

def test_generate_seeds_identical_under_real_k4_and_k7_pack_files(isolated_env):
    """同一份合成市场数据,依次激活仓库里两份真实包文件,`generate_seeds()`
    产出的四类种子集合(含 label / member_codes)逐位相同——证明 K7-pack 确实
    没有悄悄改变市场扫描层的种子生成行为,不是只在配置文件层面"看起来一样"。"""
    env = isolated_env
    insert_trade_cal(env, [D0])

    # 热点行业:半导体 5 只上涨(强度日),供 hot_industry_seed 达标。
    insert_stock_basic(env, [
        {"ts_code": f"60000{i}.SH", "industry": "半导体"} for i in range(1, 6)
    ])
    write_daily_fixture(env, "daily", D0, [
        {"ts_code": f"60000{i}.SH", "open": 10, "high": 10, "low": 10,
         "close": 10 + i * 0.3, "pre_close": 10, "vol": 1.0, "amount": 1.0}
        for i in range(1, 6)
    ])
    seed_industry_strength(env, [D0])

    # 暴起概念:独立代码,只走 ths_index/ths_member/ths_daily 三张概念表。
    insert_stock_basic(env, [{"ts_code": "600101.SH"}])
    write_flat_parquet(env, "ths_index.parquet", [{"ts_code": "885001.TI", "name": "国产替代"}])
    write_flat_parquet(env, "ths_member.parquet", [{"index_code": "885001.TI", "con_code": "600101.SH"}])
    write_flat_parquet(env, "ths_daily.parquet", [{"ts_code": "885001.TI", "trade_date": D0, "pct_change": 8.0}])

    # 涨停簇:独立代码,直接写 limit_cluster_daily 事实表。
    from neckline.db import connection

    with connection(env.db_path) as conn:
        for code, cdays in [("600201.SH", 3), ("600202.SH", 1)]:
            conn.execute(
                "INSERT OR REPLACE INTO limit_cluster_daily "
                "(trade_date, cluster_key, ts_code, cluster_kind, cluster_size, consecutive_days, "
                " anchor_industry, anchor_concept, computed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (D0.strftime("%Y%m%d"), "K1", code, "same_day", 2, cdays, "医药", None, "2024-01-01T00:00:00+00:00"),
            )

    # 异动簇:独立代码 + 独立行业,量比达标且凑够 min_cluster_members。
    insert_stock_basic(env, [
        {"ts_code": "600301.SH", "industry": "通信"},
        {"ts_code": "600302.SH", "industry": "通信"},
    ])
    write_daily_fixture(env, "daily_basic", D0, [
        {"ts_code": "600301.SH", "volume_ratio": 5.0},
        {"ts_code": "600302.SH", "volume_ratio": 4.0},
    ])

    k4_doc = load_pack_file(_K4_PACK_FILE)
    activate_pack(k4_doc["manifest"], k4_doc["config"], via="seed", db_path=env.db_path)
    k4_result = seeds.generate_seeds(D0, db_path=env.db_path, parquet_dir=env.parquet_dir)

    k7_doc = load_pack_file(_K7_PACK_FILE)
    activate_pack(k7_doc["manifest"], k7_doc["config"], via="seed", db_path=env.db_path)
    k7_result = seeds.generate_seeds(D0, db_path=env.db_path, parquet_dir=env.parquet_dir)

    assert k4_result is not None and k7_result is not None
    assert k4_result.pack_version == "K4-pack-v1"
    assert k7_result.pack_version == "K7-pack-v1"   # 两份真实包确实都被真实激活过

    counts = k4_result.counts()
    assert counts == k7_result.counts()
    assert all(n >= 1 for n in counts.values()), counts   # 四类都真的产出了种子,不是空对空

    def _fingerprint(result: "seeds.SeedSet"):
        return [
            (s.seed_key, s.label, tuple(sorted(s.member_codes)))
            for s in result.all_seeds()
        ]

    assert _fingerprint(k4_result) == _fingerprint(k7_result)


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


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 2026-08-02 定向快修回归锁:四类种子在**每类多个合格项**时,顺序必须
# 确定 —— ⑨ 完工时实证发现涨停簇/异动簇走 `frame.group_by(["cluster_key"])`
# 迭代(polars 不保证顺序)+ 上游 SQL `SELECT` 未加 `ORDER BY`,同一 D0 同一库
# `generate_seeds()` 同进程内连调三次,第 3 颗起 `seed_key` 就不一样。本测试
# 用**四类各自 ≥4 个合格项**(且 DB/构造顺序刻意与 `seed_key` 升序不一致)
# 复现"够多分组"的场景,断言:①同一天连跑三次逐位相同;②顺序恰好等于按
# `seed_key` 升序(锁死具体 tie-break,不只是"跟自己一致")。四类**全部**覆盖,
# 不只覆盖被点名的涨停簇/异动簇两类。
# ══════════════════════════════════════════════════════════════════════════

def test_generate_seeds_multi_item_categories_stay_ordered_across_repeated_calls(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])

    # 热点行业:5 个行业各 5 只成员、涨幅互不相同(拿到 5 个互不相同的
    # industry_rank);关掉 require_strength_day —— 80 分位在小样本全集下只会
    # 挑出唯一最强者,不是本测试要验证的东西(哪些行业达标由别的测试锁)。
    industries = ["半导体", "白酒", "医药", "军工", "券商"]
    basic_rows = []
    price_rows = []
    for i, ind in enumerate(industries):
        for j in range(5):
            code = f"6{i}{j}01.SH"
            basic_rows.append({"ts_code": code, "industry": ind})
            price_rows.append({
                "ts_code": code, "open": 10, "high": 10, "low": 10,
                "close": 10 + (i + 1) * 0.1, "pre_close": 10, "vol": 1.0, "amount": 1.0,
            })
    insert_stock_basic(env, basic_rows)
    write_daily_fixture(env, "daily", D0, price_rows)
    seed_industry_strength(env, [D0])

    # 暴起概念:4 个概念指数,涨幅不同但都过默认 5% 门槛;写入顺序与 ts_code
    # 字母序刻意不一致。
    concept_defs = [
        ("885104.TI", "概念丁", "710004.SH", 9.0),
        ("885101.TI", "概念甲", "710001.SH", 6.0),
        ("885103.TI", "概念丙", "710003.SH", 8.0),
        ("885102.TI", "概念乙", "710002.SH", 7.0),
    ]
    insert_stock_basic(env, [{"ts_code": c} for _, _, c, _ in concept_defs])
    write_flat_parquet(env, "ths_index.parquet", [{"ts_code": idx, "name": name} for idx, name, _, _ in concept_defs])
    write_flat_parquet(env, "ths_member.parquet", [{"index_code": idx, "con_code": c} for idx, _, c, _ in concept_defs])
    write_flat_parquet(env, "ths_daily.parquet", [
        {"ts_code": idx, "trade_date": D0, "pct_change": pct} for idx, _, _, pct in concept_defs
    ])

    # 涨停簇:6 个簇各自不同行业锚定,`cluster_key` 手工指定且写入顺序刻意
    # 打乱(K5→K1→K4→K2→K6→K3),排除"插入顺序恰好已经有序"这种巧合。
    from neckline.db import connection

    cluster_defs = [
        ("K5", "行业E"), ("K1", "行业A"), ("K4", "行业D"),
        ("K2", "行业B"), ("K6", "行业F"), ("K3", "行业C"),
    ]
    with connection(env.db_path) as conn:
        for idx, (key, ind) in enumerate(cluster_defs):
            for m in range(2):
                code = f"8{idx}{m}001.SH"
                conn.execute(
                    "INSERT OR REPLACE INTO limit_cluster_daily "
                    "(trade_date, cluster_key, ts_code, cluster_kind, cluster_size, consecutive_days, "
                    " anchor_industry, anchor_concept, computed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (D0.strftime("%Y%m%d"), key, code, "same_day", 2, 2, ind, None, "2024-01-01T00:00:00+00:00"),
                )

    # 异动簇:6 个不同行业各 2 只量比达标(6 个 cluster_key,`seeds.py` 内部
    # crc32 派生,顺序不受本测试控制,恰好覆盖"不知道具体键值、只看自洽"这条)。
    anomaly_industries = ["甲行业", "乙行业", "丙行业", "丁行业", "戊行业", "己行业"]
    anomaly_basic = []
    anomaly_rows = []
    for i, ind in enumerate(anomaly_industries):
        for m in range(2):
            code = f"9{i}{m}001.SH"
            anomaly_basic.append({"ts_code": code, "industry": ind})
            anomaly_rows.append({"ts_code": code, "volume_ratio": 5.0})
    insert_stock_basic(env, anomaly_basic)
    write_daily_fixture(env, "daily_basic", D0, anomaly_rows)

    _activate(env, **{"hot_industry_seed": {"max_rank": 10, "require_strength_day": False}})

    runs = []
    for _ in range(3):
        result = seeds.generate_seeds(D0, db_path=env.db_path, parquet_dir=env.parquet_dir)
        assert result is not None
        runs.append(result)

    counts = runs[0].counts()
    assert counts == {
        seeds.HOT_INDUSTRY: 5,
        seeds.SURGING_CONCEPT: 4,
        seeds.LIMIT_CLUSTER: 6,
        seeds.ANOMALY_CLUSTER: 6,
    }, counts

    categories = (seeds.HOT_INDUSTRY, seeds.SURGING_CONCEPT, seeds.LIMIT_CLUSTER, seeds.ANOMALY_CLUSTER)
    field_of = {
        seeds.HOT_INDUSTRY: "hot_industry",
        seeds.SURGING_CONCEPT: "surging_concept",
        seeds.LIMIT_CLUSTER: "limit_cluster",
        seeds.ANOMALY_CLUSTER: "anomaly_cluster",
    }
    baseline_keys = {
        kind: [s.seed_key for s in getattr(runs[0], field_of[kind])] for kind in categories
    }
    for kind in categories:
        assert baseline_keys[kind] == sorted(baseline_keys[kind]), (
            f"{kind} 未按 seed_key 升序排定:{baseline_keys[kind]}"
        )
    for run in runs[1:]:
        assert run.counts() == counts
        for kind in categories:
            keys = [s.seed_key for s in getattr(run, field_of[kind])]
            assert keys == baseline_keys[kind], f"{kind} 顺序在重跑间漂移:{keys} != {baseline_keys[kind]}"
