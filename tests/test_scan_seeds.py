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
from neckline.selection import engine_api
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
_K8_SKELETON_FILE = _REPO_ROOT / "packs" / "K8-skeleton.json"

_MANIFEST = {
    "pack_version": "test-pack-v1",
    "name": "测试包",
    "date": "2024-01-01",
    # V2.2-①:seeds 的消费入口 `get_active_pack()` 已是骨架线(V)薄封装,合成包
    # 必须声明 line_code='V' 才会被它读到;版本号跟常量走(bump 时夹具自动跟上)。
    "engine_api_version": engine_api.ENGINE_API_VERSION,
    "line_code": "V",
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
    assert any("无现役骨架线包" in r.message for r in caplog.records)   # V2.2-① 文案:只认 V 线


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
# 「真实版本」单测(V2-③-K7 立,V2.2-① 换主角:K4/K7 两份历史包已被 engine_api
# 闸作废、不可激活〔守门见 test_activate_pack_script.py〕,真实包文件改用
# `packs/K8-skeleton.json` —— 生产割接后 seeds 层真正要吃的那一份)。
#
# K8 骨架包的四个 scan-seed 原语参数承 K7-pack **逐字不变**(plan §五 ①:骨架包
# 只改 allowed_boards / industry_blacklist / close_min 注记三处,均不在 scan-seed
# 资格判断里)——本测试断言 `generate_seeds()` 在真实骨架包下四类种子照常产出,
# 且与同参数合成包逐位相同(= 三处值改动没有悄悄波及种子生成)。
# ══════════════════════════════════════════════════════════════════════════

def test_generate_seeds_under_real_k8_skeleton_pack_file(isolated_env):
    """同一份合成市场数据:激活仓库里真实 `K8-skeleton.json` → 四类种子照常产出;
    再切到 scan-seed 参数逐字相同的合成骨架包 → 产出逐位相同(证明骨架包的三处
    值改动〔排科创/白酒黑名单/close_min 注记〕不波及市场扫描层的种子生成)。"""
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

    skeleton_doc = load_pack_file(_K8_SKELETON_FILE)
    activate_pack(skeleton_doc["manifest"], skeleton_doc["config"], via="seed", db_path=env.db_path)
    skeleton_result = seeds.generate_seeds(D0, db_path=env.db_path, parquet_dir=env.parquet_dir)

    # 合成对照包:四个 scan-seed 原语参数逐字抄真实骨架包(其余用测试缺省)。
    _activate(env, pack_version="scan-seed-twin", **{
        k: dict(skeleton_doc["config"]["seeds"][k])
        for k in ("hot_industry_seed", "surging_concept_seed",
                  "limit_cluster_seed", "anomaly_cluster_seed")
    })
    twin_result = seeds.generate_seeds(D0, db_path=env.db_path, parquet_dir=env.parquet_dir)

    assert skeleton_result is not None and twin_result is not None
    assert skeleton_result.pack_version == "K8-V0.5"   # 真实骨架包确实被真实激活过
    assert twin_result.pack_version == "scan-seed-twin"

    counts = skeleton_result.counts()
    assert counts == twin_result.counts()
    assert all(n >= 1 for n in counts.values()), counts   # 四类都真的产出了种子,不是空对空

    def _fingerprint(result: "seeds.SeedSet"):
        return [
            (s.seed_key, s.label, tuple(sorted(s.member_codes)))
            for s in result.all_seeds()
        ]

    assert _fingerprint(skeleton_result) == _fingerprint(twin_result)


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
# 用**四类各自 ≥4 个合格项**(且 DB/构造顺序刻意与排序结果不一致)复现"够多
# 分组"的场景,断言:①同一天连跑三次逐位相同;②顺序恰好等于**语义主键 →
# `seed_key` 两级序**(锁死具体 tie-break,不只是"跟自己一致")。四类**全部**
# 覆盖,不只覆盖被点名的涨停簇/异动簇两类。
#
# ⚠ 2026-08-04(判定线审计 🔵-2)排序键升级:原来是 `seed_key` 单键升序,现在
# 第一级换成该类自己的强弱主键(行业名次升 / 涨幅降 / 簇大小降),`seed_key` 降为
# 并列打散键。本测试的两类构造刻意分工:热点行业 / 暴起概念**主键互不相同**
# (锁语义序真的生效),涨停簇 / 异动簇**主键全部并列**(cluster_size 恒 2 →
# 锁并列时仍退回 `seed_key` 升序,确定性没丢)。
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

    # ① 每一类的实际顺序 == 「语义主键升序 → seed_key 升序」重排一遍的结果
    for kind in categories:
        got = list(getattr(runs[0], field_of[kind]))
        want = sorted(got, key=lambda s: (seeds._semantic_primary(s), s.seed_key))
        assert [s.seed_key for s in got] == [s.seed_key for s in want], (
            f"{kind} 未按「语义主键 → seed_key」两级序排定:"
            f"{[(s.label, seeds._semantic_primary(s), s.seed_key) for s in got]}"
        )

    # ②-a 语义序真的生效:热点行业按 industry_rank 升序、暴起概念按涨幅降序
    hot_ranks = [s.evidence["industry_rank"] for s in runs[0].hot_industry]
    assert hot_ranks == sorted(hot_ranks) and len(set(hot_ranks)) == len(hot_ranks), hot_ranks
    concept_pcts = [s.evidence["pct_change"] for s in runs[0].surging_concept]
    assert concept_pcts == [9.0, 8.0, 7.0, 6.0], concept_pcts      # 涨得最多的排最前

    # ②-b 主键并列时退回 seed_key 升序(两类簇的 cluster_size 全是 2)
    for kind in (seeds.LIMIT_CLUSTER, seeds.ANOMALY_CLUSTER):
        sizes = {s.evidence["cluster_size"] for s in getattr(runs[0], field_of[kind])}
        assert sizes == {2}, f"{kind} 构造应全部并列以覆盖 tie-break:{sizes}"
        assert baseline_keys[kind] == sorted(baseline_keys[kind]), (
            f"{kind} 主键并列时未退回 seed_key 升序:{baseline_keys[kind]}"
        )

    # ③ 连跑三次逐位相同(确定性没因为换排序键而减弱)
    for run in runs[1:]:
        assert run.counts() == counts
        for kind in categories:
            keys = [s.seed_key for s in getattr(run, field_of[kind])]
            assert keys == baseline_keys[kind], f"{kind} 顺序在重跑间漂移:{keys} != {baseline_keys[kind]}"


def test_semantic_primary_missing_evidence_sorts_last_not_zero():
    """语义主键算不出(evidence 缺项 / 非数)→ 排同类最后,⛔ 不拿 0 冒充"最弱"
    (0 在涨幅与簇大小里都是真实取值)。"""
    strong = seeds.DriverSeed(seed_key="ffff", seed_kind=seeds.SURGING_CONCEPT, label="强",
                              member_codes=(), evidence={"pct_change": 6.0})
    unknown = seeds.DriverSeed(seed_key="0001", seed_kind=seeds.SURGING_CONCEPT, label="缺",
                               member_codes=(), evidence={})
    zero = seeds.DriverSeed(seed_key="0002", seed_kind=seeds.SURGING_CONCEPT, label="零",
                            member_codes=(), evidence={"pct_change": 0.0})
    out = seeds._sort_seeds([unknown, zero, strong])
    assert [s.label for s in out] == ["强", "零", "缺"]
    assert seeds._semantic_primary(unknown) == seeds._PRIMARY_MISSING
    assert seeds._semantic_primary(zero) == 0.0        # 真实的 0 不是"算不出"
