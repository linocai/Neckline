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
_K7_PACK_FILE = _REPO_ROOT / "packs" / "K7-pack.json"


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


# ══════════════════════════════════════════════════════════════════════════
# `config.tier.stage_scores`(V2-③-K7 新增可选键,K7 需求 1b)
# ══════════════════════════════════════════════════════════════════════════

def test_validate_config_accepts_missing_stage_scores_key():
    """`stage_scores` 是可选键——K4-pack-v1 这类完全不写它的包必须照常通过
    (纯增量扩容,回滚锚硬判据的前提)。"""
    config = _minimal_pack()["config"]
    assert "stage_scores" not in config["tier"]
    assert pack.validate_config(config) == []


def test_validate_config_accepts_well_formed_stage_scores():
    config = _minimal_pack()["config"]
    config["tier"]["stage_scores"] = {"ignition": 0.6, "fermentation": 1.0, "overheat": 0.0}
    assert pack.validate_config(config) == []


def test_validate_config_accepts_stage_scores_covering_all_six_codes():
    config = _minimal_pack()["config"]
    config["tier"]["stage_scores"] = {
        "ignition": 0.6, "fermentation": 1.0, "overheat": 0.0,
        "divergence": 0.4, "ebb": 0.2, "none": 0.2,
    }
    assert pack.validate_config(config) == []


def test_validate_config_rejects_non_dict_stage_scores():
    config = _minimal_pack()["config"]
    config["tier"]["stage_scores"] = ["ignition", 0.6]
    errors = pack.validate_config(config)
    assert any("config.tier.stage_scores 必须是对象" in e for e in errors)


def test_validate_config_rejects_chinese_stage_score_keys():
    """③-K7-D 定案:中文键(交接稿草案原文)已被淘汰,配置键必须与库列值同源
    (英文枚举码)。"""
    config = _minimal_pack()["config"]
    config["tier"]["stage_scores"] = {"发酵": 1.0, "启动": 0.6}
    errors = pack.validate_config(config)
    assert any("未知阶段码" in e and "发酵" in e and "启动" in e for e in errors)


def test_validate_config_rejects_unknown_stage_score_key():
    config = _minimal_pack()["config"]
    config["tier"]["stage_scores"] = {"ignition": 0.6, "typo_stage": 0.1}
    errors = pack.validate_config(config)
    assert any("未知阶段码" in e and "typo_stage" in e for e in errors)


def test_validate_config_rejects_non_numeric_stage_score_value():
    config = _minimal_pack()["config"]
    config["tier"]["stage_scores"] = {"ignition": "high"}
    errors = pack.validate_config(config)
    assert any("非数值分数" in e and "ignition" in e for e in errors)


def test_validate_config_rejects_bool_as_stage_score_value():
    """`isinstance(True, int)` 为 `True`——同 `primitives.py::_TYPE_CHECKERS` 的
    既有纪律,`bool` 不该被数值校验悄悄接纳。"""
    config = _minimal_pack()["config"]
    config["tier"]["stage_scores"] = {"ignition": True}
    errors = pack.validate_config(config)
    assert any("非数值分数" in e and "ignition" in e for e in errors)


# ══════════════════════════════════════════════════════════════════════════
# `config.tier.quality_lines`(V2-⑥-b 新增可选键,档位质量线进包,2026-08-02
# planner 裁定)
# ══════════════════════════════════════════════════════════════════════════

def test_validate_config_accepts_missing_quality_lines_key():
    """整段可选——K4-pack-v1(回滚锚)完全不写这个键必须照常通过。"""
    config = _minimal_pack()["config"]
    assert "quality_lines" not in config["tier"]
    assert pack.validate_config(config) == []


def test_validate_config_accepts_well_formed_quality_lines():
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = {"tier1_min": 0.60, "tier2_min": 0.40, "tier3_min": 0.25}
    assert pack.validate_config(config) == []


def test_validate_config_accepts_partial_quality_lines():
    """三键各自独立可选——同 `stage_scores` "不要求六态全部出现"同一纪律
    (⑥-b-A schema 定死:与 weights/dims/stage_scores 平级、可选键)。"""
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = {"tier3_min": 0.25}
    assert pack.validate_config(config) == []


def test_validate_config_rejects_non_dict_quality_lines():
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = [0.6, 0.4, 0.25]
    errors = pack.validate_config(config)
    assert any("config.tier.quality_lines 必须是对象" in e for e in errors)


def test_validate_config_rejects_unknown_quality_line_key():
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = {"tier1_min": 0.6, "tier4_min": 0.1}
    errors = pack.validate_config(config)
    assert any("未知键" in e and "tier4_min" in e for e in errors)


def test_validate_config_rejects_non_numeric_quality_line_value():
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = {"tier1_min": "high"}
    errors = pack.validate_config(config)
    assert any("非数值分数" in e and "tier1_min" in e for e in errors)


def test_validate_config_rejects_bool_as_quality_line_value():
    """同 stage_scores 那条既有陷阱防线:`bool` 不该被数值校验悄悄接纳。"""
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = {"tier1_min": True}
    errors = pack.validate_config(config)
    assert any("非数值分数" in e and "tier1_min" in e for e in errors)


def test_validate_config_rejects_non_monotonic_quality_lines_tier1_tier2():
    """⑥-b-A 验收原文例句:`tier1_min < tier2_min` → fail loud。"""
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = {"tier1_min": 0.30, "tier2_min": 0.40, "tier3_min": 0.25}
    errors = pack.validate_config(config)
    assert any("单调" in e for e in errors)


def test_validate_config_rejects_non_monotonic_quality_lines_tier2_tier3():
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = {"tier1_min": 0.60, "tier2_min": 0.20, "tier3_min": 0.40}
    errors = pack.validate_config(config)
    assert any("单调" in e for e in errors)


def test_validate_config_rejects_non_monotonic_quality_lines_across_a_missing_middle_key():
    """中间那一档缺省也不能蒙混过关——靠传递性比较两个"字面给出"的键
    (`_QUALITY_LINE_ORDER` 顺序,不是相邻 DB 列)。"""
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = {"tier1_min": 0.20, "tier3_min": 0.40}   # 缺 tier2_min
    errors = pack.validate_config(config)
    assert any("单调" in e for e in errors)


def test_validate_config_accepts_equal_adjacent_quality_lines():
    """相邻两线相等不算"不单调"——只拒绝严格倒挂(⑥-b-A 例句用的是 `<`)。"""
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = {"tier1_min": 0.40, "tier2_min": 0.40, "tier3_min": 0.25}
    assert pack.validate_config(config) == []


def test_validate_config_does_not_require_defaults_merged_for_monotonicity():
    """单调性只比较**字面给出**的键,不合并引擎默认值——只给一个极端的
    `tier3_min` 且没有别的键可比时,不会被这条拒绝(即便与引擎默认组合后
    可能"看起来"不单调,那不是本文件的职责,见 `_validate_quality_lines`
    docstring)。"""
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = {"tier3_min": 0.99}
    assert pack.validate_config(config) == []


def test_pack_accessor_tier_quality_lines_reflects_config(tmp_path: Path):
    db_path = tmp_path / "n.db"
    doc = _minimal_pack(
        "quality-lines-v1",
        config={
            "tier": {
                "weights": {"sector_strength": 1.0},
                "dims": ["sector_strength"],
                "quality_lines": {"tier1_min": 0.6, "tier2_min": 0.4, "tier3_min": 0.25},
            },
        },
    )
    pack.activate_pack(doc["manifest"], doc["config"], db_path=db_path)
    active = pack.get_active_pack(db_path=db_path)
    assert active.tier_quality_lines() == {"tier1_min": 0.6, "tier2_min": 0.4, "tier3_min": 0.25}


def test_pack_accessor_tier_quality_lines_defaults_to_empty_dict(tmp_path: Path):
    db_path = tmp_path / "n.db"
    doc = _minimal_pack("no-quality-lines-v1")
    pack.activate_pack(doc["manifest"], doc["config"], db_path=db_path)
    active = pack.get_active_pack(db_path=db_path)
    assert active.tier_quality_lines() == {}


def test_real_k4_pack_still_has_no_quality_lines_key():
    """K4-pack-v1 是回滚锚,⑥-b 零改动——直接从"没有这个键"验证,而不是去读
    文件 diff。"""
    doc = pack.load_pack_file(_K4_PACK_FILE)
    assert "quality_lines" not in doc["config"]["tier"]
    assert pack.validate_pack_doc(doc) == []   # 重新校验仍通过


def test_real_k7_pack_file_has_quality_lines_matching_plan_decision():
    """⑥-b-A/⑥-b-B 裁定的三个数:0.60/0.40/0.25。"""
    doc = pack.load_pack_file(_K7_PACK_FILE)
    assert pack.validate_pack_doc(doc) == []
    assert doc["config"]["tier"]["quality_lines"] == {
        "tier1_min": 0.60, "tier2_min": 0.40, "tier3_min": 0.25,
    }
    assert doc["manifest"]["engine_api_version"] == 1   # ⑥-b-A 判定:纯增量,不 bump


def test_engine_api_version_not_bumped_by_quality_lines_addition():
    from neckline.selection import engine_api

    assert engine_api.ENGINE_API_VERSION == 1


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


def test_k4_pack_v1_still_validates_and_activates_identically_after_k7_extension(tmp_path: Path):
    """回滚锚硬判据(V2-③-K7 验收原文,不许含糊):K4-pack-v1 逐字节重新校验
    仍通过,`get_active_pack()` 对它的输出逐位不变——白名单新增
    `industry_stage_daily.*`、`intel_rank_priority.dims` 扩容、
    `config.tier.stage_scores` 新增可选键,这三处改动对 K4-pack-v1 必须是零
    影响的纯增量,`ENGINE_API_VERSION` 因此不 bump(仍是 1)。"""
    doc = pack.load_pack_file(_K4_PACK_FILE)
    assert pack.validate_pack_doc(doc) == []   # 逐字节重新校验仍通过

    db_path = tmp_path / "rollback_anchor.db"
    pack.activate_pack(doc["manifest"], doc["config"], via="seed", db_path=db_path)
    active = pack.get_active_pack(db_path=db_path)

    assert active is not None
    assert active.pack_version == "K4-pack-v1"
    assert active.engine_api_version == 1   # 未 bump
    assert active.manifest == doc["manifest"]
    assert active.config == doc["config"]
    assert active.tier_weights() == {
        "sector_strength": 0.25, "driver_freshness": 0.20, "leader_clarity": 0.25,
        "tradability": 0.20, "card_density": 0.10,
    }
    assert active.tier_stage_scores() == {}   # K4-pack-v1 没有这一段,新访问器缺省空字典
    assert active.seeds_config("intel_rank_priority") == {
        "dims": ["industry_rank", "industry_persist_days", "yellow_card_count"]
    }

    # 排序键行为逐位不变:K4-pack-v1 的三个既有维度全部是 asc,与扩容前"直接
    # 取值参与字典序比较"数值等价,排序结果不变。
    row_a = {"industry_rank": 1, "industry_persist_days": 3, "yellow_card_count": 0}
    row_b = {"industry_rank": 1, "industry_persist_days": 1, "yellow_card_count": 9}
    dims_param = active.seeds_config("intel_rank_priority")
    key_a = primitives.PRIMITIVES["intel_rank_priority"].run(row_a, dims_param)
    key_b = primitives.PRIMITIVES["intel_rank_priority"].run(row_b, dims_param)
    assert key_a == (1.0, 3.0, 0.0)
    assert key_b == (1.0, 1.0, 9.0)
    assert key_b < key_a   # persist_days 更小的排前面(asc 语义不变)


def test_real_k7_pack_file_is_schema_valid_and_matches_plan_decisions():
    """③-K7-E 定案:`packs/K7-pack.json`,`pack_version = "K7-pack-v1"`。本测试
    直接读仓库里那份真实文件,防止它腐化成"能过 schema 但字段值早就漂移"的
    僵尸文件(同 K4-pack 那条测试的既有纪律)。"""
    doc = pack.load_pack_file(_K7_PACK_FILE)
    assert pack.validate_pack_doc(doc) == []
    assert doc["manifest"]["pack_version"] == "K7-pack-v1"
    assert doc["manifest"]["engine_api_version"] == 1   # ③-K7-C 判定:纯增量,不 bump
    assert doc["manifest"]["evidence_ref"] == [
        "research/k7_pre_report.md", "research/k7_pre2_report.md",
    ]
    assert set(doc["config"]["seeds"]) == set(primitives.PRIMITIVES)   # 九个原语一个不多一个不少
    assert doc["config"]["seeds"]["intel_rank_priority"]["dims"] == [
        "industry_rank", "industry_stage_score", "leader_rs_rank", "yellow_card_count",
    ]
    assert doc["config"]["tier"]["weights"] == {   # K7 需求 2 证据化初值
        "sector_strength": 0.30, "leader_clarity": 0.30, "driver_freshness": 0.10,
        "tradability": 0.20, "card_density": 0.10,
    }
    # ③-K7-D 定案:stage_scores 键一律英文枚举码,六态全覆盖,禁中文键。
    assert set(doc["config"]["tier"]["stage_scores"]) == {
        "ignition", "fermentation", "overheat", "divergence", "ebb", "none",
    }
    assert doc["config"]["tier"]["stage_scores"]["fermentation"] == 1.0   # 需求 2:发酵态最高分


def test_k7_pack_carries_k4_pack_hygiene_gate_and_scan_seed_params_unchanged():
    """③-K7-E 定案:「K7 的变更集中在排序键、Tier 权重与两个新维度」——本测试
    交叉断言 K7-pack 里**除** `intel_rank_priority`(排序键,预期不同)与
    `tier`(权重/stage_scores,预期不同)之外的全部 8 个原语参数与 K4-pack
    逐字节相同,防止未来有人顺手在"承 K4-pack 不变"的部分悄悄夹带改动。"""
    k4_doc = pack.load_pack_file(_K4_PACK_FILE)
    k7_doc = pack.load_pack_file(_K7_PACK_FILE)
    for prim_name in set(primitives.PRIMITIVES) - {"intel_rank_priority"}:
        assert k4_doc["config"]["seeds"][prim_name] == k7_doc["config"]["seeds"][prim_name], (
            f"{prim_name} 的参数在 K7-pack 与 K4-pack 之间不一致,但该原语不在"
            "「K7 变更集中在排序键/Tier 权重/stage_scores」的允许范围内"
        )


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
    assert active.tier_stage_scores() == {}   # 未声明 stage_scores → 缺省空字典


def test_pack_accessor_tier_stage_scores_reflects_config(tmp_path: Path):
    """V2-③-K7 新增访问器:`config.tier.stage_scores` 声明了就原样透出
    (与 `tier_weights`/`tier_dims` 同一套读法)。"""
    db_path = tmp_path / "n.db"
    doc = _minimal_pack(
        "stage-scores-v1",
        config={
            "tier": {
                "weights": {"sector_strength": 1.0},
                "dims": ["sector_strength"],
                "stage_scores": {"ignition": 0.6, "fermentation": 1.0},
            },
        },
    )
    pack.activate_pack(doc["manifest"], doc["config"], db_path=db_path)
    active = pack.get_active_pack(db_path=db_path)
    assert active.tier_stage_scores() == {"ignition": 0.6, "fermentation": 1.0}


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


# ══════════════════════════════════════════════════════════════════════════
# V2-③-K7 补的「真实版本」单测(承 V2-③ 完工记录登记的测试局限:「插槽真被
# 消费」当时只能在原语 + 包层面用**合成**迷你包代理验证,待 ④/⑥ 落地后应在
# 各自块内用真实种子集/Tier 序重跑——④ 已完工,`generate_seeds()` 已存在;
# 本测试改用仓库里两份**真实**包文件 `packs/K4-pack.json`/`packs/K7-pack.json`
# 驱动 `intel_rank_priority`,证明"排序 dims/参数确实不同"这一半不再是代理
# 验证。「换包 → 种子集跟着变」那半已在 `tests/test_scan_seeds.py::
# test_generate_seeds_identical_under_real_k4_and_k7_pack_files` 用同样两份
# 真实文件验证(结论:种子生成参数按 ③-K7-E 设计保持不变,不是本测试的重复)。
# "Tier 序" 那半留给 ⑥ 落地后用真实 Tier 引擎重跑,不在本测试范围内。
# ══════════════════════════════════════════════════════════════════════════

def test_real_k4_pack_vs_k7_pack_intel_rank_priority_dims_and_ranking_differ(tmp_path: Path):
    """装载并激活两份真实包文件(非合成迷你包),证明 `intel_rank_priority` 的
    排序维度配置真的不同,且用同一份合成候选数据跑出的排序结果真的翻转——
    不是"配置字符串不同"这种表面差异,而是"排序行为真的变了"。"""
    db_path = tmp_path / "real_packs.db"
    k4_doc = pack.load_pack_file(_K4_PACK_FILE)
    k7_doc = pack.load_pack_file(_K7_PACK_FILE)

    pack.activate_pack(k4_doc["manifest"], k4_doc["config"], via="seed", db_path=db_path)
    active_k4 = pack.get_active_pack(db_path=db_path)
    pack.activate_pack(k7_doc["manifest"], k7_doc["config"], via="seed", db_path=db_path)
    active_k7 = pack.get_active_pack(db_path=db_path)

    k4_dims_params = active_k4.seeds_config("intel_rank_priority")
    k7_dims_params = active_k7.seeds_config("intel_rank_priority")
    assert k4_dims_params["dims"] != k7_dims_params["dims"]   # 真实配置确实不同

    # 两只候选:industry_rank 打平(第一维分不出胜负)。候选 A 的
    # industry_persist_days 更小(K4-pack 旧单调函数把"刚启动"排更靠前)、
    # industry_stage_score 更低;候选 B 相反——同一份数据,两份真实包应判出
    # 相反的先后顺序。
    candidate_a = {
        "industry_rank": 1, "industry_persist_days": 1, "yellow_card_count": 0,
        "industry_stage_score": 0.2, "leader_rs_rank": 3,
    }
    candidate_b = {
        "industry_rank": 1, "industry_persist_days": 5, "yellow_card_count": 0,
        "industry_stage_score": 1.0, "leader_rs_rank": 3,
    }

    prim = primitives.PRIMITIVES["intel_rank_priority"]
    key_a_under_k4 = prim.run(candidate_a, k4_dims_params)
    key_b_under_k4 = prim.run(candidate_b, k4_dims_params)
    key_a_under_k7 = prim.run(candidate_a, k7_dims_params)
    key_b_under_k7 = prim.run(candidate_b, k7_dims_params)

    assert key_a_under_k4 < key_b_under_k4   # K4-pack:A 的 persist_days 更小 → A 排前面
    assert key_b_under_k7 < key_a_under_k7   # K7-pack:B 的 stage_score 更高 → B 排前面(顺序翻转)
