"""策略包 schema 校验 + `selection_packs` 读写单测(plan §五 V2-③)。

**全部用 `tmp_path` 隔离库,显式传 `db_path=`**——本文件不需要 `isolated_env`
夹具(它只重写 `market_data`/`trading_calendar`/`tushare_client` 三处
`settings` 绑定,不含 `neckline.db`;本文件测的函数从不裸调用,不存在那个坑,
见项目 CLAUDE.md「测试隔离」条)。真实 `data/neckline.db` 全程不碰。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from neckline.selection import pack, primitives

_REPO_ROOT = Path(__file__).resolve().parent.parent
_K4_PACK_FILE = _REPO_ROOT / "packs" / "K4-pack.json"


def _minimal_pack(pack_version: str = "test-pack-v1", **overrides: Any) -> Dict[str, Any]:
    manifest = {
        "pack_version": pack_version,
        "name": "测试包",
        "date": "2026-08-02",
        "engine_api_version": 1,
        "evidence_ref": [],
    }
    config = {
        "seeds": {"non_new_stock": {"min_days": 60}},
        "tier": {"weights": {"sector_strength": 1.0}, "dims": ["sector_strength"]},
    }
    manifest.update(overrides.get("manifest", {}))
    config.update(overrides.get("config", {}))
    return {"manifest": manifest, "config": config}


# ══════════════════════════════════════════════════════════════════════════
# manifest / config schema 校验
# ══════════════════════════════════════════════════════════════════════════

def test_validate_manifest_accepts_well_formed():
    doc = _minimal_pack()
    assert pack.validate_manifest(doc["manifest"]) == []


@pytest.mark.parametrize("mutate,expected_substr", [
    ({"pack_version": ""}, "pack_version"),
    ({"pack_version": 123}, "pack_version"),
    ({"name": "  "}, "name"),
    ({"date": "2026/08/02"}, "date"),
    ({"date": "2026-08-XX"}, "date"),   # plan 例文里的占位符本身不是合法日期
    ({"engine_api_version": "1"}, "engine_api_version"),
    ({"engine_api_version": 1.0}, "engine_api_version"),
    ({"evidence_ref": "research/x.md"}, "evidence_ref"),   # 必须是数组不是裸字符串
    ({"evidence_ref": [1, 2]}, "evidence_ref"),
])
def test_validate_manifest_rejects_malformed_fields(mutate, expected_substr):
    manifest = _minimal_pack()["manifest"]
    manifest.update(mutate)
    errors = pack.validate_manifest(manifest)
    assert any(expected_substr in e for e in errors), errors


def test_validate_manifest_allows_empty_evidence_ref_list():
    """空证据链数组在格式层是合法的(是否该非空是产品判断,不是格式判断,
    见 `validate_manifest` docstring)。"""
    manifest = _minimal_pack()["manifest"]
    manifest["evidence_ref"] = []
    assert pack.validate_manifest(manifest) == []


def test_validate_manifest_rejects_non_dict():
    assert pack.validate_manifest([1, 2, 3]) == ["manifest 必须是 JSON 对象"]
    assert pack.validate_manifest(None) == ["manifest 必须是 JSON 对象"]


def test_validate_config_accepts_well_formed():
    assert pack.validate_config(_minimal_pack()["config"]) == []


def test_validate_config_rejects_unregistered_primitive():
    config = _minimal_pack()["config"]
    config["seeds"] = {"totally_made_up_primitive": {}}
    errors = pack.validate_config(config)
    assert any("未注册的原语" in e and "totally_made_up_primitive" in e for e in errors)


def test_validate_config_rejects_bad_primitive_params():
    config = _minimal_pack()["config"]
    config["seeds"] = {"non_new_stock": {"min_days": "not-an-int"}}
    errors = pack.validate_config(config)
    assert any("non_new_stock" in e for e in errors)


def test_validate_config_rejects_empty_or_missing_tier_weights():
    config = _minimal_pack()["config"]
    config["tier"] = {"weights": {}, "dims": []}
    errors = pack.validate_config(config)
    assert any("config.tier.weights" in e for e in errors)
    assert any("config.tier.dims" in e for e in errors)


def test_validate_config_rejects_dims_referencing_unknown_weight():
    config = _minimal_pack()["config"]
    config["tier"] = {"weights": {"sector_strength": 1.0}, "dims": ["sector_strength", "ghost_dim"]}
    errors = pack.validate_config(config)
    assert any("ghost_dim" in e for e in errors)


def test_validate_config_rejects_non_numeric_weight():
    config = _minimal_pack()["config"]
    config["tier"] = {"weights": {"sector_strength": "high"}, "dims": ["sector_strength"]}
    errors = pack.validate_config(config)
    assert any("非数值权重" in e for e in errors)


def test_validate_pack_doc_checks_engine_api_compat_only_after_structure_passes():
    doc = _minimal_pack()
    doc["manifest"]["engine_api_version"] = 2
    errors = pack.validate_pack_doc(doc)
    assert any("engine_api_version 不兼容" in e for e in errors)


def test_validate_pack_doc_rejects_non_dict_top_level():
    assert pack.validate_pack_doc([1, 2]) == ["包文件顶层必须是 JSON 对象(含 manifest / config 两个键)"]


# ══════════════════════════════════════════════════════════════════════════
# 包文件装载
# ══════════════════════════════════════════════════════════════════════════

def test_load_pack_file_reads_valid_json(tmp_path: Path):
    file = tmp_path / "p.json"
    file.write_text(json.dumps(_minimal_pack()), encoding="utf-8")
    doc = pack.load_pack_file(file)
    assert doc["manifest"]["pack_version"] == "test-pack-v1"


def test_load_pack_file_rejects_missing_top_level_keys(tmp_path: Path):
    file = tmp_path / "p.json"
    file.write_text(json.dumps({"manifest": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest/config"):
        pack.load_pack_file(file)


def test_load_pack_file_raises_on_malformed_json(tmp_path: Path):
    file = tmp_path / "p.json"
    file.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        pack.load_pack_file(file)


def test_load_pack_file_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(OSError):
        pack.load_pack_file(tmp_path / "does_not_exist.json")


def test_real_k4_pack_file_is_schema_valid_and_matches_d6_d7_decisions():
    """D7 定案:`packs/K4-pack.json`,`pack_version = "K4-pack-v1"`。本测试直接读
    仓库里那份真实文件(不是测试夹具里另造的一份),防止它腐化成"能过 schema 但
    其实字段值早就漂移"的僵尸文件。"""
    doc = pack.load_pack_file(_K4_PACK_FILE)
    assert pack.validate_pack_doc(doc) == []
    assert doc["manifest"]["pack_version"] == "K4-pack-v1"
    assert doc["manifest"]["engine_api_version"] == 1
    assert doc["manifest"]["evidence_ref"] == ["research/k4_assembly_report.md"]
    assert set(doc["config"]["seeds"]) == set(primitives.PRIMITIVES)   # 五个原语一个不多一个不少


# ══════════════════════════════════════════════════════════════════════════
# `selection_packs` 读写(空库 / 首次激活 / 切换 / 幂等 / 内容冲突)
# ══════════════════════════════════════════════════════════════════════════

def test_get_active_pack_none_on_empty_db(tmp_path: Path):
    db_path = tmp_path / "n.db"
    assert pack.get_active_pack(db_path=db_path) is None
    assert pack.get_pack("anything", db_path=db_path) is None
    assert pack.list_packs(db_path=db_path) == []


def test_activate_pack_first_activation_writes_single_activate_event(tmp_path: Path):
    db_path = tmp_path / "n.db"
    doc = _minimal_pack("first-v1")
    activated = pack.activate_pack(doc["manifest"], doc["config"], via="seed", db_path=db_path)
    assert activated.pack_version == "first-v1"
    assert activated.is_active is True

    active = pack.get_active_pack(db_path=db_path)
    assert active is not None and active.pack_version == "first-v1"

    with_conn_rows = _activation_log_rows(db_path)
    assert with_conn_rows == [("first-v1", "activate", "seed")]


def test_activate_pack_switch_writes_deactivate_and_activate_events(tmp_path: Path):
    db_path = tmp_path / "n.db"
    doc_a = _minimal_pack("switch-a")
    doc_b = _minimal_pack("switch-b")
    pack.activate_pack(doc_a["manifest"], doc_a["config"], db_path=db_path)
    pack.activate_pack(doc_b["manifest"], doc_b["config"], db_path=db_path)

    packs = {p.pack_version: p.is_active for p in pack.list_packs(db_path=db_path)}
    assert packs == {"switch-a": False, "switch-b": True}

    rows = _activation_log_rows(db_path)
    assert [(r[0], r[1]) for r in rows] == [
        ("switch-a", "activate"),      # 首次激活 a:无旧现役可关,只有一条 activate
        ("switch-a", "deactivate"),    # 切到 b 时:先给 a 追加 deactivate
        ("switch-b", "activate"),      # 再给 b 追加 activate ——两条事件(plan 原文「追加两条事件」)
    ]


def test_activate_pack_reactivating_current_active_is_noop(tmp_path: Path):
    db_path = tmp_path / "n.db"
    doc = _minimal_pack("noop-v1")
    pack.activate_pack(doc["manifest"], doc["config"], db_path=db_path)
    before = _activation_log_rows(db_path)
    pack.activate_pack(doc["manifest"], doc["config"], db_path=db_path)   # 重复调用,内容相同
    after = _activation_log_rows(db_path)
    assert before == after   # 没有多出任何事件


def test_activate_pack_rejects_same_version_different_content_and_writes_nothing(tmp_path: Path):
    db_path = tmp_path / "n.db"
    doc = _minimal_pack("conflict-v1")
    pack.activate_pack(doc["manifest"], doc["config"], db_path=db_path)

    tampered = _minimal_pack("conflict-v1", manifest={"name": "TAMPERED"})
    with pytest.raises(ValueError, match="内容不同"):
        pack.activate_pack(tampered["manifest"], tampered["config"], db_path=db_path)

    # 事务必须整体回滚:包表与日志表都不应留下这次失败尝试的痕迹。
    stored = pack.get_pack("conflict-v1", db_path=db_path)
    assert stored is not None and stored.manifest["name"] == "测试包"   # 未被 TAMPERED 覆盖
    assert len(_activation_log_rows(db_path)) == 1                      # 仍只有最初那一条 activate


def test_activate_pack_rejects_invalid_schema():
    with pytest.raises(ValueError, match="schema"):
        pack.activate_pack({"pack_version": "no-name"}, {}, db_path=Path("unused.db"))


def _activation_log_rows(db_path: Path):
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT pack_version, action, via FROM selection_pack_activation_log ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 现役包缓存:按 (db_path, pack_version) 失效,不跨库串味
# ══════════════════════════════════════════════════════════════════════════

def test_active_pack_cache_does_not_leak_across_different_db_paths(tmp_path: Path):
    """两个不同的 DB 文件里恰好装了**同名** pack_version,不应互相污染缓存——
    若缓存只按 `pack_version` 分桶(不含 db_path),A 库先读一次会把 Pack 对象
    缓存住,B 库后读同名版本会错误地拿到 A 库那份对象。"""
    db_a, db_b = tmp_path / "a.db", tmp_path / "b.db"
    doc_a = _minimal_pack("shared-name", manifest={"name": "来自 A 库"})
    doc_b = _minimal_pack("shared-name", manifest={"name": "来自 B 库"}, config={"tier": {"weights": {"x": 1.0}, "dims": ["x"]}})

    pack.activate_pack(doc_a["manifest"], doc_a["config"], db_path=db_a)
    pack.activate_pack(doc_b["manifest"], doc_b["config"], db_path=db_b)

    active_a = pack.get_active_pack(db_path=db_a)
    active_b = pack.get_active_pack(db_path=db_b)
    assert active_a is not None and active_a.name == "来自 A 库"
    assert active_b is not None and active_b.name == "来自 B 库"
    assert active_a.tier_weights() != active_b.tier_weights()


def test_active_pack_cache_invalidates_when_active_version_changes(tmp_path: Path):
    db_path = tmp_path / "n.db"
    doc_a = _minimal_pack("cache-a")
    doc_b = _minimal_pack("cache-b")
    pack.activate_pack(doc_a["manifest"], doc_a["config"], db_path=db_path)
    first_read = pack.get_active_pack(db_path=db_path)
    assert first_read.pack_version == "cache-a"

    pack.activate_pack(doc_b["manifest"], doc_b["config"], db_path=db_path)
    second_read = pack.get_active_pack(db_path=db_path)
    assert second_read.pack_version == "cache-b"   # 不是缓存住的 cache-a 陈旧对象


def test_active_pack_cache_clears_when_no_pack_active(tmp_path: Path):
    """激活过一个包后,若库被外部直接改成"无现役"(极端场景,正常流程不会发生,
    但缓存逻辑不该假设这不可能),`get_active_pack` 必须如实返回 `None`,不能
    从缓存里翻出上一次读到的现役包。"""
    import sqlite3

    db_path = tmp_path / "n.db"
    doc = _minimal_pack("will-be-cleared")
    pack.activate_pack(doc["manifest"], doc["config"], db_path=db_path)
    assert pack.get_active_pack(db_path=db_path) is not None

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE selection_packs SET is_active=0")
        conn.commit()
    finally:
        conn.close()

    assert pack.get_active_pack(db_path=db_path) is None


# ══════════════════════════════════════════════════════════════════════════
# Pack 访问器(seeds_config / tier_weights / tier_dims)
# ══════════════════════════════════════════════════════════════════════════

def test_pack_accessors_reflect_config(tmp_path: Path):
    db_path = tmp_path / "n.db"
    doc = _minimal_pack("accessor-v1")
    pack.activate_pack(doc["manifest"], doc["config"], db_path=db_path)
    active = pack.get_active_pack(db_path=db_path)
    assert active.seeds_config("non_new_stock") == {"min_days": 60}
    assert active.seeds_config("nonexistent") == {}
    assert active.tier_weights() == {"sector_strength": 1.0}
    assert active.tier_dims() == ["sector_strength"]


# ══════════════════════════════════════════════════════════════════════════
# 「插槽真被消费」代理验证(plan §五 V2-③「测试与守门」原文要求 ④ 的种子集与 ⑥
# 的 Tier 序跟着换包变化——但 ④/⑥ 尚未施工〔分块序列:④⑤⑥ 依赖 ③,不是反过来〕,
# 本块在唯一已存在的层面(原语 + 包)做代理验证:换一个包(改阈值/改权重)→
# 同一份合成输入数据,原语 `run()` 结果与 tier 权重检索结果确实跟着变。
# ④/⑥ 真正落地后,应在各自块内用真实种子集/Tier 序重跑一遍这条验证。
# ══════════════════════════════════════════════════════════════════════════

def test_slot_is_really_consumed_not_an_empty_shell(tmp_path: Path):
    db_path = tmp_path / "n.db"

    pack_loose = _minimal_pack(
        "slot-proof-loose",
        config={
            "seeds": {"non_new_stock": {"min_days": 30}},
            "tier": {"weights": {"sector_strength": 0.9, "tradability": 0.1},
                     "dims": ["sector_strength", "tradability"]},
        },
    )
    pack_strict = _minimal_pack(
        "slot-proof-strict",
        config={
            "seeds": {"non_new_stock": {"min_days": 200}},
            "tier": {"weights": {"sector_strength": 0.1, "tradability": 0.9},
                     "dims": ["sector_strength", "tradability"]},
        },
    )

    candidate_row = {"days_since_listing": 100}   # 100 天:在 30 与 200 之间

    pack.activate_pack(pack_loose["manifest"], pack_loose["config"], db_path=db_path)
    active_loose = pack.get_active_pack(db_path=db_path)
    result_loose = primitives.PRIMITIVES["non_new_stock"].run(
        candidate_row, active_loose.seeds_config("non_new_stock")
    )

    pack.activate_pack(pack_strict["manifest"], pack_strict["config"], db_path=db_path)
    active_strict = pack.get_active_pack(db_path=db_path)
    result_strict = primitives.PRIMITIVES["non_new_stock"].run(
        candidate_row, active_strict.seeds_config("non_new_stock")
    )

    # 同一份候选数据,换包后"是否通过种子过滤"这一判断真的翻转了——不是摆设插槽。
    assert result_loose is True    # 100 >= 30
    assert result_strict is False  # 100 < 200

    # tier 权重同理:同一组合成分维得分,换包后综合分排序会翻转。
    dim_scores = {"sector_strength": 0.2, "tradability": 0.8}   # 板块弱、可交易性强的候选
    score_loose = sum(active_loose.tier_weights()[d] * dim_scores[d] for d in active_loose.tier_dims())
    score_strict = sum(active_strict.tier_weights()[d] * dim_scores[d] for d in active_strict.tier_dims())
    assert score_loose < score_strict   # loose 包重仓 sector_strength(候选恰好弱)→ 综合分低
