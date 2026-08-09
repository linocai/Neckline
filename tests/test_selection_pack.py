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

from neckline.selection import engine_api, pack, primitives

_REPO_ROOT = Path(__file__).resolve().parent.parent
_K4_PACK_FILE = _REPO_ROOT / "packs" / "K4-pack.json"
_K7_PACK_FILE = _REPO_ROOT / "packs" / "K7-pack.json"
_K8_SKELETON_FILE = _REPO_ROOT / "packs" / "K8-skeleton.json"
_ENGINE_PACK_FILES = {
    "C": _REPO_ROOT / "packs" / "C1.json",
    "Z": _REPO_ROOT / "packs" / "Z1.json",
    "Y": _REPO_ROOT / "packs" / "Y1.json",
}


def _minimal_pack(pack_version: str = "test-pack-v1", **overrides: Any) -> Dict[str, Any]:
    """合成最小骨架线包(V2.2-① 起缺省 `line_code='V'` —— 本文件绝大多数用例经
    `get_active_pack()` 读回,而它已是骨架线薄封装;LEGACY 线专属用例自行覆盖)。"""
    manifest = {
        "pack_version": pack_version,
        "name": "测试包",
        "date": "2026-08-02",
        "engine_api_version": engine_api.ENGINE_API_VERSION,
        "line_code": "V",
        "evidence_ref": [],
    }
    config = {
        "seeds": {"non_new_stock": {"min_days": 60}},
        "tier": {"weights": {"sector_strength": 1.0}, "dims": ["sector_strength"]},
    }
    manifest.update(overrides.get("manifest", {}))
    config.update(overrides.get("config", {}))
    return {"manifest": manifest, "config": config}


def _engine_leaf(value: Any, *, basis: str = "测试:从 K8 某句翻译") -> Dict[str, Any]:
    return {"value": value,
            "provenance": {"source": "engineering_v1", "basis": basis, "calibration": "pending"}}


def _minimal_engine_pack(line_code: str = "C", pack_version: str = "C-test-v1",
                         **overrides: Any) -> Dict[str, Any]:
    """合成最小引擎线包(五关一段不少、每叶带 provenance —— 闸 1 的正例底座)。"""
    manifest = {
        "pack_version": pack_version,
        "name": "测试引擎包",
        "date": "2026-08-09",
        "engine_api_version": engine_api.ENGINE_API_VERSION,
        "line_code": line_code,
        "evidence_ref": [],
    }
    config = {
        "engine": {
            "engine_code": line_code,
            "applies_to": "测试用引擎",
            "gates": {
                "market": {"primary_regimes": _engine_leaf(["trend_continuation"])},
                "sector": {"industry_rank_max": _engine_leaf(10)},
                # 🔴 裁定 #11:位置关零阈值,只剩一条**定性文本**键(⛔ 不走 provenance 闸)。
                "position": {"guidance": "测试用的定性位置准则"},
                "core": {"leader_rs_rank_max": _engine_leaf(3)},
                "evidence": {"independent_evidence_min": _engine_leaf(3)},
            },
            "tier_evidence": {
                "t1": {"max_evidence_degrades": _engine_leaf(0)},
                "t2": {"max_evidence_degrades": _engine_leaf(1)},
            },
        },
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
    """子键各自独立可选——同 `stage_scores` "不要求六态全部出现"同一纪律
    (⑥-b-A schema 定死:与 weights/dims/stage_scores 平级、可选键)。"""
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = {"tier2_min": 0.40}
    assert pack.validate_config(config) == []


def test_v1_pack_with_retired_tier3_min_still_parses_but_can_no_longer_activate(tmp_path: Path):
    """V2.1-② 的「受理退役键」宽容**保留**(历史行读回要能解析,schema 层不报
    未知键);但 V2.2-① 起 `K7-pack-v1` **不再是回滚锚** —— `ENGINE_API_VERSION`
    1→2 后它被闸 2 硬拒,⛔ 全项目不许再写「回滚 = 激活旧包」(回滚绳 = 代码
    commit + DB 备份还原,plan §五 ① 原文)。本测试直接吃仓库里那份真包文件。"""
    doc = json.loads(_K7_PACK_FILE.read_text(encoding="utf-8"))
    assert "tier3_min" in doc["config"]["tier"]["quality_lines"]     # 前提:真包里真的有
    assert pack.validate_manifest(doc["manifest"]) == []             # schema 层仍逐字受理
    assert pack.validate_config(doc["config"]) == []
    # 但组合校验(含 engine_api 兼容)必须拒 —— 且是**只有**这一条错误(说明拒因
    # 恰是版本闸,不是 schema 被顺手改坏)。
    errors = pack.validate_pack_doc(doc)
    assert len(errors) == 1 and "engine_api_version 不兼容" in errors[0]
    db_path = tmp_path / "rollback.db"
    with pytest.raises(ValueError, match="engine_api_version 不兼容"):
        pack.activate_pack(doc["manifest"], doc["config"], via="test", db_path=db_path)
    assert pack.get_active_pack(db_path=db_path) is None             # 一行都没写进去


def test_retired_quality_line_key_is_accepted_but_not_active():
    """V2.1-②:`tier3_min` **受理**(不报未知键)但**不是现役键**,
    ⛔ 别把它并回 `_ACTIVE_QUALITY_LINE_KEYS`(那等于把 T3 复活)。"""
    assert pack._ACTIVE_QUALITY_LINE_KEYS == {"tier1_min", "tier2_min"}
    assert pack._RETIRED_QUALITY_LINE_KEYS == {"tier3_min"}
    assert pack._QUALITY_LINE_ORDER == ("tier1_min", "tier2_min")
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = {"tier1_min": 0.60, "tier2_min": 0.40, "tier3_min": 0.25}
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


def test_monotonicity_only_compares_active_keys():
    """V2.1-②:单调性**只比现役两键**。退役的 `tier3_min` 无论多离谱都不参与
    —— 它已经不表达任何档位,拿它当比较项等于让一个不生效的旋钮否决合法的包
    (进而作废回滚锚)。"""
    config = _minimal_pack()["config"]
    # 现役两键单调,退役键倒挂到天上去也照样过
    config["tier"]["quality_lines"] = {"tier1_min": 0.60, "tier2_min": 0.40, "tier3_min": 0.99}
    assert pack.validate_config(config) == []
    # 只给退役键 + 一个现役键,同样无对可比
    config["tier"]["quality_lines"] = {"tier2_min": 0.20, "tier3_min": 0.90}
    assert pack.validate_config(config) == []


def test_validate_config_accepts_equal_adjacent_quality_lines():
    """相邻两线相等不算"不单调"——只拒绝严格倒挂(⑥-b-A 例句用的是 `<`)。"""
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = {"tier1_min": 0.40, "tier2_min": 0.40, "tier3_min": 0.25}
    assert pack.validate_config(config) == []


def test_validate_config_does_not_require_defaults_merged_for_monotonicity():
    """单调性只比较**字面给出**的现役键,不合并引擎默认值——只给一个极端的
    `tier1_min` 且没有别的现役键可比时,不会被这条拒绝(即便与引擎默认组合后
    可能"看起来"不单调,那不是本文件的职责,见 `_validate_quality_lines`
    docstring)。"""
    config = _minimal_pack()["config"]
    config["tier"]["quality_lines"] = {"tier1_min": 0.01}
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
    """K4-pack-v1 文件冻结留档(⛔ 一字不改),schema 层仍能逐字解析——直接从
    "没有这个键"验证,而不是去读文件 diff。"""
    doc = pack.load_pack_file(_K4_PACK_FILE)
    assert "quality_lines" not in doc["config"]["tier"]
    assert pack.validate_manifest(doc["manifest"]) == []   # schema 层仍逐字受理
    assert pack.validate_config(doc["config"]) == []


def test_real_k7_pack_file_has_quality_lines_matching_plan_decision():
    """⑥-b-A/⑥-b-B 裁定的三个数:0.60/0.40/0.25(文件冻结留档,内容不许漂)。"""
    doc = pack.load_pack_file(_K7_PACK_FILE)
    assert pack.validate_manifest(doc["manifest"]) == []
    assert pack.validate_config(doc["config"]) == []
    assert doc["config"]["tier"]["quality_lines"] == {
        "tier1_min": 0.60, "tier2_min": 0.40, "tier3_min": 0.25,
    }
    assert doc["manifest"]["engine_api_version"] == 1   # 历史事实,文件冻结不改


# ══════════════════════════════════════════════════════════════════════════
# V2.2-① 反向守门(plan §五 ① 测试与守门原文,三条一条不少):版本闸真的剪断了
# 老回滚绳 —— ⛔ 不留一条自己都不信的绳。
# ══════════════════════════════════════════════════════════════════════════

def test_engine_api_version_is_2_after_v22_line_split():
    """`ENGINE_API_VERSION == 2`(V2.2-① 裁定):`get_active_pack()` 语义改为取
    骨架线现役行,LEGACY 包不再会被返回 → 判定规则第二条不成立 → 必须 bump。
    理由全文见 `engine_api.py` 模块头。"""
    assert engine_api.ENGINE_API_VERSION == 2


def test_v1_manifests_are_incompatible_now():
    """`is_compatible({"engine_api_version": 1})` 必须为 **False**(逐位相等判据)。"""
    assert engine_api.is_compatible({"engine_api_version": 1}) is False
    assert engine_api.is_compatible({"engine_api_version": 2}) is True


def test_legacy_rollback_anchors_are_dead_and_gate_says_so():
    """仓库里真的 `packs/K4-pack.json` / `packs/K7-pack.json` 走组合校验**必须被拒**
    —— 把「回滚锚已作废」钉成机器判据(⛔ 有人把这两个文件"顺手升级"到
    engine_api_version=2 来让测试变绿,就把这道守门连同历史档案一起销毁了,
    所以同时断言文件内容仍是历史原样)。"""
    for pack_file in (_K4_PACK_FILE, _K7_PACK_FILE):
        doc = pack.load_pack_file(pack_file)
        assert doc["manifest"]["engine_api_version"] == 1, f"{pack_file.name} 被改动过!历史文件必须冻结"
        assert "line_code" not in doc["manifest"], f"{pack_file.name} 被改动过!历史文件必须冻结"
        assert engine_api.is_compatible(doc["manifest"]) is False
        errors = pack.validate_pack_doc(doc)
        assert any("engine_api_version 不兼容" in e for e in errors)


def test_validate_pack_doc_checks_engine_api_compat_only_after_structure_passes():
    doc = _minimal_pack()
    doc["manifest"]["engine_api_version"] = 1   # V2.2 起 1 = 不兼容的那一侧
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
    仓库里那份真实文件(不是测试夹具里另造的一份),防止它腐化成"字段值早就漂移"
    的僵尸文件。V2.2-① 起它是**冻结历史档案**:schema 层仍逐字受理,组合校验被
    engine_api 闸拒(那是另一条守门的职责,见 `test_legacy_rollback_anchors_...`)。"""
    doc = pack.load_pack_file(_K4_PACK_FILE)
    assert pack.validate_manifest(doc["manifest"]) == []
    assert pack.validate_config(doc["config"]) == []
    assert doc["manifest"]["pack_version"] == "K4-pack-v1"
    assert doc["manifest"]["engine_api_version"] == 1
    assert doc["manifest"]["evidence_ref"] == ["research/k4_assembly_report.md"]
    # 历史文件冻结在它写成那天的原语集(当时 9 个);V2.2-① 新增的 industry_blacklist
    # 不在其中且**不许**被补进去(文件一字不改),但它引用的每个原语必须仍在注册表。
    assert "industry_blacklist" not in doc["config"]["seeds"]
    assert set(doc["config"]["seeds"]) <= set(primitives.PRIMITIVES)
    assert set(doc["config"]["seeds"]) == set(primitives.PRIMITIVES) - {"industry_blacklist"}


def test_k4_pack_config_still_drives_primitives_identically_without_activation(tmp_path: Path):
    """K4-pack-v1 已不可激活(engine_api 闸),但它的 config **作为历史归因资料**
    仍要能逐位驱动原语(历史报告按 `pack_version` 指纹回查参数时,解释不能变)。
    排序键行为逐位不变:三个既有维度全部 asc,与扩容前数值等价。"""
    doc = pack.load_pack_file(_K4_PACK_FILE)
    assert doc["config"]["seeds"]["intel_rank_priority"] == {
        "dims": ["industry_rank", "industry_persist_days", "yellow_card_count"]
    }
    row_a = {"industry_rank": 1, "industry_persist_days": 3, "yellow_card_count": 0}
    row_b = {"industry_rank": 1, "industry_persist_days": 1, "yellow_card_count": 9}
    dims_param = doc["config"]["seeds"]["intel_rank_priority"]
    key_a = primitives.PRIMITIVES["intel_rank_priority"].run(row_a, dims_param)
    key_b = primitives.PRIMITIVES["intel_rank_priority"].run(row_b, dims_param)
    assert key_a == (1.0, 3.0, 0.0)
    assert key_b == (1.0, 1.0, 9.0)
    assert key_b < key_a   # persist_days 更小的排前面(asc 语义不变)


def test_real_k7_pack_file_is_schema_valid_and_matches_plan_decisions():
    """③-K7-E 定案:`packs/K7-pack.json`,`pack_version = "K7-pack-v1"`。本测试
    直接读仓库里那份真实文件,防止它腐化成"字段值早就漂移"的僵尸文件(同
    K4-pack 那条测试的既有纪律;V2.2-① 起同为冻结历史档案,不再走组合校验)。"""
    doc = pack.load_pack_file(_K7_PACK_FILE)
    assert pack.validate_manifest(doc["manifest"]) == []
    assert pack.validate_config(doc["config"]) == []
    assert doc["manifest"]["pack_version"] == "K7-pack-v1"
    assert doc["manifest"]["engine_api_version"] == 1   # 历史事实,文件冻结不改
    assert doc["manifest"]["evidence_ref"] == [
        "research/k7_pre_report.md", "research/k7_pre2_report.md",
    ]
    assert set(doc["config"]["seeds"]) == set(primitives.PRIMITIVES) - {"industry_blacklist"}
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
    逐字节相同,防止未来有人顺手在"承 K4-pack 不变"的部分悄悄夹带改动。
    (V2.2-① 起遍历基准 = K4 文件自己的 seeds 键,不再是 `PRIMITIVES` 全集——
    注册表已长出两个历史文件没有的新原语。)"""
    k4_doc = pack.load_pack_file(_K4_PACK_FILE)
    k7_doc = pack.load_pack_file(_K7_PACK_FILE)
    assert set(k4_doc["config"]["seeds"]) == set(k7_doc["config"]["seeds"])
    for prim_name in set(k4_doc["config"]["seeds"]) - {"intel_rank_priority"}:
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


def test_db_refuses_two_active_packs(tmp_path: Path):
    """契约线审计 🔵 B3(2026-08-03):「任一时刻至多一个现役包」**上库级约束**。

    此前这条不变量只由 `activate_pack()` 的写入顺序保证,库本身拦不住手工 SQL 造出两行
    `is_active=1`;而 `get_active_pack` 会**静默**取一行 —— 于是"今天用的是哪个包"变成
    看运气的事,而包版本是 ⑤⑥ 的判定输入、⑨ 的归因分层键。"""
    import sqlite3

    from neckline.db import connection

    db_path = tmp_path / "n.db"
    doc_a, doc_b = _minimal_pack("dual-a"), _minimal_pack("dual-b")
    pack.activate_pack(doc_a["manifest"], doc_a["config"], db_path=db_path)
    pack.activate_pack(doc_b["manifest"], doc_b["config"], db_path=db_path)   # a 已被置 0
    with pytest.raises(sqlite3.IntegrityError):
        with connection(db_path) as conn:
            conn.execute("UPDATE selection_packs SET is_active=1 WHERE pack_version='dual-a'")
    # 阴性方向:非现役行想留多少留多少(部分索引只约束 is_active=1 那些行)
    with connection(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM selection_packs WHERE is_active=0").fetchone()[0] == 1


def test_get_active_pack_warns_when_legacy_db_has_two_active_rows(tmp_path: Path, caplog):
    """🔵 B3 读侧:老库(索引加入之前遗留)真出现两行现役时,**必须吵**,不许静默择一。
    取值仍是确定性的(`created_at, pack_version` 双键降序),但沉默才是真正的问题。"""
    import logging

    from neckline.db import connection

    db_path = tmp_path / "n.db"
    doc_a, doc_b = _minimal_pack("legacy-a"), _minimal_pack("legacy-b")
    pack.activate_pack(doc_a["manifest"], doc_a["config"], db_path=db_path)
    pack.activate_pack(doc_b["manifest"], doc_b["config"], db_path=db_path)
    # 模拟"索引存在之前就留下的脏数据":绕过索引直接造第二行现役(两行同为 V 线)
    with connection(db_path) as conn:
        conn.execute("DROP INDEX IF EXISTS idx_selection_packs_single_active_per_line")
        conn.execute("UPDATE selection_packs SET is_active=1 WHERE pack_version='legacy-a'")
    pack._ACTIVE_PACK_CACHE.clear()

    with caplog.at_level(logging.WARNING):
        active = pack.get_active_pack(db_path=db_path)
    assert active is not None
    assert any("is_active=1" in rec.message for rec in caplog.records), "两行现役必须告警"


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

def test_real_k4_pack_vs_k7_pack_intel_rank_priority_dims_and_ranking_differ():
    """装载两份真实历史包文件(非合成迷你包),证明 `intel_rank_priority` 的
    排序维度配置真的不同,且用同一份合成候选数据跑出的排序结果真的翻转——
    不是"配置字符串不同"这种表面差异,而是"排序行为真的变了"。
    (V2.2-① 起两包已不可激活〔engine_api 闸〕,改为直接吃文件 config —— 本测试
    验的本来就是「配置驱动排序行为」这条纽带,不依赖激活。)"""
    k4_doc = pack.load_pack_file(_K4_PACK_FILE)
    k7_doc = pack.load_pack_file(_K7_PACK_FILE)

    k4_dims_params = k4_doc["config"]["seeds"]["intel_rank_priority"]
    k7_dims_params = k7_doc["config"]["seeds"]["intel_rank_priority"]
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


# ══════════════════════════════════════════════════════════════════════════
# V2.2-① 多版本线注册表(plan §五 ① 测试与守门,逐条):每线各自唯一现役 /
# 跨线可并存 / stopped 不出现在 get_active_engines / get_active_pack ≡ V 线。
# ══════════════════════════════════════════════════════════════════════════

def test_same_line_two_active_rows_is_a_db_level_integrity_error(tmp_path: Path):
    """同线两行 `is_active=1` → 库级 IntegrityError(per-line partial unique index)。"""
    import sqlite3

    from neckline.db import connection

    db_path = tmp_path / "n.db"
    doc_v1, doc_v2 = _minimal_pack("line-v-1"), _minimal_pack("line-v-2")
    pack.activate_pack(doc_v1["manifest"], doc_v1["config"], db_path=db_path)
    pack.activate_pack(doc_v2["manifest"], doc_v2["config"], db_path=db_path)   # v1 已被置 0
    with pytest.raises(sqlite3.IntegrityError):
        with connection(db_path) as conn:
            conn.execute("UPDATE selection_packs SET is_active=1 WHERE pack_version='line-v-1'")


def test_cross_line_active_rows_coexist(tmp_path: Path):
    """跨线各一行现役**必须**能并存(单包制的全表唯一约束已废)——四条线各自唯一。"""
    db_path = tmp_path / "n.db"
    docs = [_minimal_pack("skel-v1")]
    for line in ("C", "Z", "Y"):
        docs.append(_minimal_engine_pack(line, pack_version=f"{line}-eng-v1"))
    for d in docs:
        pack.activate_pack(d["manifest"], d["config"], via="seed", db_path=db_path)

    actives = {p.line_code: p.pack_version for p in pack.list_packs(db_path=db_path) if p.is_active}
    assert actives == {"V": "skel-v1", "C": "C-eng-v1", "Z": "Z-eng-v1", "Y": "Y-eng-v1"}


def test_activating_engine_line_does_not_kick_skeleton_line(tmp_path: Path):
    """plan 陷阱 #1 的机器判据:激活引擎线**不许**把骨架线现役行踢下去(全表口径
    的 prior 查找会静默干这件事,而且闸全过、库不报错)。事件流里也不许出现骨架
    包被 deactivate 的伪事件。"""
    import sqlite3

    db_path = tmp_path / "n.db"
    skel = _minimal_pack("skel-stay")
    pack.activate_pack(skel["manifest"], skel["config"], db_path=db_path)
    eng = _minimal_engine_pack("C", pack_version="C-newcomer")
    pack.activate_pack(eng["manifest"], eng["config"], db_path=db_path)

    assert pack.get_active_skeleton(db_path).pack_version == "skel-stay"   # 骨架线还在
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT pack_version, action FROM selection_pack_activation_log ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("skel-stay", "activate"), ("C-newcomer", "activate")]   # 零 deactivate


def test_switching_within_engine_line_deactivates_only_that_line(tmp_path: Path):
    db_path = tmp_path / "n.db"
    for d in (_minimal_pack("skel-v1"),
              _minimal_engine_pack("C", pack_version="C-old"),
              _minimal_engine_pack("C", pack_version="C-new")):
        pack.activate_pack(d["manifest"], d["config"], db_path=db_path)
    actives = {p.line_code: p.pack_version for p in pack.list_packs(db_path=db_path) if p.is_active}
    assert actives == {"V": "skel-v1", "C": "C-new"}


def test_get_active_engines_orders_c_z_y_and_filters_stopped(tmp_path: Path):
    """`get_active_engines()`:C→Z→Y 确定性排序(⛔ 不靠 SQL 行序 —— 刻意乱序激活);
    `status='stopped'` 的线不出现在返回值,但 `get_active_line` 照常返回它(「现役
    版本是谁」与「现在产不产候选」是两个问题)。status 本版无切换入口(用户裁定),
    测试按 plan 授权直接 UPDATE 验读侧。"""
    import sqlite3

    db_path = tmp_path / "n.db"
    for line in ("Y", "C", "Z"):   # 刻意乱序插入
        d = _minimal_engine_pack(line, pack_version=f"{line}-eng")
        pack.activate_pack(d["manifest"], d["config"], db_path=db_path)

    engines = pack.get_active_engines(db_path=db_path)
    assert list(engines) == ["C", "Z", "Y"]
    assert [p.pack_version for p in engines.values()] == ["C-eng", "Z-eng", "Y-eng"]
    assert all(p.status == "running" for p in engines.values())   # DEFAULT 落位

    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE selection_packs SET status='stopped' WHERE line_code='Z'")
    conn.commit(); conn.close()
    pack._ACTIVE_PACK_CACHE.clear()

    engines = pack.get_active_engines(db_path=db_path)
    assert list(engines) == ["C", "Y"]                        # Z 不产候选
    z = pack.get_active_line("Z", db_path)
    assert z is not None and z.pack_version == "Z-eng" and z.status == "stopped"   # 但仍是现役版本


def test_get_active_pack_is_the_skeleton_line_view(tmp_path: Path):
    """`get_active_pack()` ≡ `get_active_line("V")` ≡ `get_active_skeleton()` 行为
    等价;库里只有 LEGACY 现役行(割接前生产形状)时三者一致返回 None——⛔ 不许
    拿 LEGACY 行冒充骨架线。"""
    import sqlite3

    from neckline.db import init_schema

    db_path = tmp_path / "n.db"
    init_schema(db_path)
    # 裸 SQL 造一行 LEGACY 现役(activate_pack 已被 engine_api 闸挡死,这正是老库形状)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO selection_packs (pack_version,name,engine_api_version,manifest_json,"
        "config_json,is_active,created_at) VALUES ('K7-pack-v1','k7',1,'{}','{}',1,'2026')"
    )
    conn.commit(); conn.close()
    assert pack.get_active_pack(db_path=db_path) is None
    assert pack.get_active_skeleton(db_path=db_path) is None
    assert pack.get_active_line("LEGACY", db_path).pack_version == "K7-pack-v1"   # 留档可查

    doc = _minimal_pack("skel-eq")
    pack.activate_pack(doc["manifest"], doc["config"], db_path=db_path)
    a, b, c = (pack.get_active_pack(db_path=db_path),
               pack.get_active_skeleton(db_path=db_path),
               pack.get_active_line("V", db_path=db_path))
    assert a.pack_version == b.pack_version == c.pack_version == "skel-eq"
    assert a is b is c   # 同一缓存对象:薄封装没有另走一条路


def test_get_active_line_rejects_unknown_line_code(tmp_path: Path):
    with pytest.raises(ValueError, match="line_code"):
        pack.get_active_line("v", tmp_path / "n.db")   # 小写手滑必须炸,不许静默查空


def test_active_pack_cache_is_bucketed_per_line_not_per_db(tmp_path: Path):
    """plan 陷阱 #2 的机器判据:读 V 之后读 C **不许**互相顶掉缓存(只按 db_path
    分桶时,交替读两条线每次都 cache miss 且可能拿错线)。"""
    db_path = tmp_path / "n.db"
    skel = _minimal_pack("cache-skel")
    eng = _minimal_engine_pack("C", pack_version="cache-eng")
    pack.activate_pack(skel["manifest"], skel["config"], db_path=db_path)
    pack.activate_pack(eng["manifest"], eng["config"], db_path=db_path)

    v1 = pack.get_active_line("V", db_path)
    c1 = pack.get_active_line("C", db_path)
    v2 = pack.get_active_line("V", db_path)
    c2 = pack.get_active_line("C", db_path)
    assert v1.pack_version == "cache-skel" and c1.pack_version == "cache-eng"
    assert v2 is v1 and c2 is c1   # 交替读不互踢(同一对象 = 缓存真的命中)


# ══════════════════════════════════════════════════════════════════════════
# V2.2-① schema 交叉校验 + provenance 闸(闸 1 的机器判据,正反两例齐)
# ══════════════════════════════════════════════════════════════════════════

def test_skeleton_line_config_must_not_carry_engine_section():
    doc = _minimal_pack()
    doc["config"]["engine"] = {"engine_code": "V"}
    errors = pack.validate_pack_doc(doc)
    assert any("不许出现 engine 段" in e for e in errors)


def test_engine_line_config_must_not_carry_seeds_or_tier():
    doc = _minimal_engine_pack("C")
    doc["config"]["seeds"] = {"non_new_stock": {"min_days": 60}}
    doc["config"]["tier"] = {"weights": {"x": 1.0}, "dims": ["x"]}
    errors = pack.validate_pack_doc(doc)
    assert any("不许出现 seeds 段" in e for e in errors)
    assert any("不许出现 tier 段" in e for e in errors)


def test_engine_code_must_equal_line_code_bit_for_bit():
    doc = _minimal_engine_pack("C")
    doc["config"]["engine"]["engine_code"] = "Z"
    errors = pack.validate_pack_doc(doc)
    assert any("逐位相等" in e for e in errors)


def test_unknown_config_top_level_keys_are_tolerated_on_both_line_kinds(tmp_path: Path):
    """② 要给骨架包加 `config.regime` —— 本版交叉校验
    只管 seeds/tier/engine 三个段的归属,**其他顶层键一律不管**(plan §五 ① 原文),
    拒了它们就是给后续块挖坑。

    ⚠ V2.2-② 落地后 `config.regime` 已是**注册段**(有键名白名单 + provenance 形状
    校验,见 `_validate_regime`)—— 不再能当"未知顶层键"的样例,样例换成尚未注册的
    未来段名;「未知顶层键宽容」这条性质本身原样受测。"""
    skel = _minimal_pack("tolerant-v")
    skel["config"]["future_section"] = {"future": True}
    assert pack.validate_pack_doc(skel) == []
    eng = _minimal_engine_pack("C", pack_version="tolerant-c")
    eng["config"]["future_engine_section"] = {"future": True}
    assert pack.validate_pack_doc(eng) == []
    # 而且真的能激活(不是只有校验层宽容)
    db_path = tmp_path / "n.db"
    pack.activate_pack(skel["manifest"], skel["config"], db_path=db_path)
    pack.activate_pack(eng["manifest"], eng["config"], db_path=db_path)


def test_missing_provenance_rejected_and_well_formed_accepted():
    """闸 1 provenance 正反两例(plan §五 ① 测试与守门原文)。"""
    good = _minimal_engine_pack("C")
    assert pack.validate_pack_doc(good) == []                       # 正例:全叶带 provenance

    bare = _minimal_engine_pack("C")
    bare["config"]["engine"]["gates"]["core"]["leader_rs_rank_max"] = 3   # 裸值,缺 provenance
    errors = pack.validate_pack_doc(bare)
    assert any("value/provenance" in e for e in errors)


def test_provenance_engineering_v1_requires_basis_and_pending_calibration():
    doc = _minimal_engine_pack("C")
    doc["config"]["engine"]["gates"]["core"]["leader_rs_rank_max"] = {
        "value": 3, "provenance": {"source": "engineering_v1", "calibration": "pending"},
    }
    assert any("basis" in e for e in pack.validate_pack_doc(doc))

    doc2 = _minimal_engine_pack("C")
    doc2["config"]["engine"]["gates"]["core"]["leader_rs_rank_max"] = {
        "value": 3, "provenance": {"source": "engineering_v1", "basis": "K8 某句"},
    }
    assert any("calibration" in e for e in pack.validate_pack_doc(doc2))


def test_provenance_audited_requires_ref():
    doc = _minimal_engine_pack("C")
    doc["config"]["engine"]["gates"]["core"]["leader_rs_rank_max"] = {
        "value": 3, "provenance": {"source": "audited"},
    }
    assert any("ref" in e for e in pack.validate_pack_doc(doc))


def test_provenance_unknown_source_rejected():
    doc = _minimal_engine_pack("C")
    doc["config"]["engine"]["gates"]["core"]["leader_rs_rank_max"] = {
        "value": 3, "provenance": {"source": "i_swear_its_fine"},
    }
    assert any("source 取值非法" in e for e in pack.validate_pack_doc(doc))


def test_engine_gate_keys_outside_whitelist_rejected():
    """键名白名单(`_ENGINE_GATE_SCHEMA`,⛔ 不绑 PRIMITIVES,裁定见 pack.py 模块头):
    包侧自创阈值键 → 拒;要新玩法先扩白名单。"""
    doc = _minimal_engine_pack("C")
    doc["config"]["engine"]["gates"]["market"]["my_secret_knob"] = _engine_leaf(0.5)
    errors = pack.validate_pack_doc(doc)
    assert any("白名单外" in e and "my_secret_knob" in e for e in errors)


def test_engine_gates_missing_a_section_rejected():
    doc = _minimal_engine_pack("C")
    del doc["config"]["engine"]["gates"]["evidence"]
    errors = pack.validate_pack_doc(doc)
    assert any("缺关口段" in e and "evidence" in e for e in errors)


def test_manifest_line_code_must_be_one_of_five(tmp_path: Path):
    doc = _minimal_pack()
    doc["manifest"]["line_code"] = "C1"   # 引擎版本号 ≠ 线号,常见手滑
    errors = pack.validate_pack_doc(doc)
    assert any("line_code 取值非法" in e for e in errors)
    with pytest.raises(ValueError):
        pack.activate_pack(doc["manifest"], doc["config"], db_path=tmp_path / "n.db")


# ══════════════════════════════════════════════════════════════════════════
# V2.2-① 四个真实新包文件(K8-skeleton / C1 / Z1 / Y1):防僵尸文件 + ③-F 守门
# (plan ③ 测试清单第 5 条提前入列:三个引擎包阈值全部能解析出 provenance,
# engineering_v1 的条目都带 basis)。
# ══════════════════════════════════════════════════════════════════════════

def test_real_k8_skeleton_pack_matches_plan_value_changes():
    """骨架包三处值改动逐条钉死(plan §五 ① 原文):排科创(纯包配置)/ 白酒黑名单
    (实测 industry 取值 = 「白酒」)/ close_min 维持 2.0 且**刻意无价格上限**。"""
    doc = pack.load_pack_file(_K8_SKELETON_FILE)
    assert pack.validate_pack_doc(doc) == []
    assert doc["manifest"]["pack_version"] == "K8-V0.5"   # ⛔ `V0.5` 禁简写(三线命名纪律)
    assert doc["manifest"]["line_code"] == "V"
    assert doc["manifest"]["engine_api_version"] == 2
    hygiene = doc["config"]["seeds"]["stock_hygiene"]
    assert hygiene["allowed_boards"] == ["MAIN", "GEM"]           # K8 §三 排除科创板
    assert hygiene["close_min"] == 2.0
    assert "close_max" not in hygiene                             # 刻意无上限,⛔ 别顺手加
    assert doc["config"]["seeds"]["industry_blacklist"] == {"industries": ["白酒"]}
    # config 结构承 K7 的 seeds+tier 两段,tier 权重/打分映射原样;V2.2-② 追加
    # regime 段(行情状态五阈值,值与引擎默认逐位一致由 test_market_regime.py 锁);
    # ⚠ **`config.landing` 段已随裁定 #11 整体删除**(位置关不再有机械判定):
    # 骨架包 config 回到 seeds + tier + regime 三段。⛔ 不得恢复第四段。
    assert set(doc["config"]) == {"seeds", "tier", "regime"}
    k7 = pack.load_pack_file(_K7_PACK_FILE)
    assert doc["config"]["tier"]["weights"] == k7["config"]["tier"]["weights"]
    assert doc["config"]["tier"]["stage_scores"] == k7["config"]["tier"]["stage_scores"]


def test_skeleton_allowed_boards_narrowing_is_pack_config_not_code():
    """「排除科创板 = 纯包配置、零代码改动」的机器判据:原语默认值**仍含 STAR**,
    收窄只发生在骨架包的参数里(改默认值 = 悄悄改掉所有不给这个键的包的语义)。"""
    prim = primitives.PRIMITIVES["stock_hygiene"]
    assert prim.params_schema["allowed_boards"]["default"] == ["MAIN", "GEM", "STAR"]
    star_row = {"is_st": False, "board": "STAR", "close": 10.0, "ma20": 9.5, "amount_ma20": 50000.0}
    doc = pack.load_pack_file(_K8_SKELETON_FILE)
    assert prim.run(star_row) is True                                          # 默认:STAR 放行
    assert prim.run(star_row, doc["config"]["seeds"]["stock_hygiene"]) is False  # 骨架包:排除


@pytest.mark.parametrize("line", sorted(_ENGINE_PACK_FILES))
def test_real_engine_pack_files_pass_gate1_and_every_leaf_has_provenance(line: str):
    doc = pack.load_pack_file(_ENGINE_PACK_FILES[line])
    assert pack.validate_pack_doc(doc) == []
    assert doc["manifest"]["line_code"] == line
    assert doc["manifest"]["pack_version"] == f"{line}1"   # 引擎版本命名:C1/Z1/Y1
    engine = doc["config"]["engine"]
    assert engine["engine_code"] == line

    leaves = []
    for section, body in engine["gates"].items():
        qualitative = pack._QUALITATIVE_GATE_KEYS.get(section, frozenset())
        for key, leaf in body.items():
            if key in qualitative:
                # 裁定 #11:定性文本键(position.guidance)**不是阈值**,不走
                # provenance 闸 —— ⛔ 别把它算进"每叶都要有 provenance"里。
                assert isinstance(leaf, str) and leaf.strip(), f"gates.{section}.{key}"
                continue
            leaves.append((f"gates.{section}.{key}", leaf))
    for tier_key, body in engine["tier_evidence"].items():
        for key, leaf in body.items():
            leaves.append((f"tier_evidence.{tier_key}.{key}", leaf))
    assert leaves, "引擎包至少得有阈值叶子"
    for path, leaf in leaves:
        prov = leaf["provenance"]                          # 全部能解析出 provenance
        assert prov["source"] in ("audited", "engineering_v1"), path
        if prov["source"] == "engineering_v1":
            assert prov["basis"].strip(), f"{path}: engineering_v1 必须带 basis"
            assert prov["calibration"] == "pending", path
        else:
            assert prov["ref"].strip(), f"{path}: audited 必须带 ref"


def test_engine_pack_audited_leaves_match_plan_distribution():
    """③-F 表的 provenance 分布如实登记(plan 874 行):`leader_rs_rank` 三档 /
    `stage` 五态取值 / `industry_rank` 名次档 = audited,**其余全部 engineering_v1**。"""
    audited_paths = {}
    for line, file in _ENGINE_PACK_FILES.items():
        doc = pack.load_pack_file(file)
        for section, body in doc["config"]["engine"]["gates"].items():
            qualitative = pack._QUALITATIVE_GATE_KEYS.get(section, frozenset())
            for key, leaf in body.items():
                if key in qualitative:
                    continue                      # 定性文本键无 provenance(裁定 #11)
                if leaf["provenance"]["source"] == "audited":
                    audited_paths.setdefault(line, set()).add(f"{section}.{key}")
    assert audited_paths["C"] == {"core.leader_rs_rank_max", "sector.industry_rank_max"}
    assert audited_paths["Z"] == {"core.leader_rs_rank_max", "sector.stage_allowed",
                                  "market.trend_continuation_required_stages"}
    assert audited_paths["Y"] == {"core.leader_rs_rank_max", "sector.industry_rank_max"}


def test_engine_pack_threshold_values_match_plan_table():
    """③-F 三引擎首版阈值逐条对表(866–875 行)——文件值漂了这里当场红。"""
    def val(line, section, key):
        doc = pack.load_pack_file(_ENGINE_PACK_FILES[line])
        return doc["config"]["engine"]["gates"][section][key]["value"]

    assert val("C", "core", "leader_rs_rank_max") == 3
    assert val("Z", "core", "leader_rs_rank_max") == 2
    assert val("Y", "core", "leader_rs_rank_max") == 5
    assert val("C", "sector", "industry_rank_max") == 10
    assert val("Y", "sector", "industry_rank_max") == 30
    assert val("C", "sector", "strength_days_min_5d") == 3
    assert val("Z", "sector", "stage_allowed") == ["ignition", "fermentation"]
    assert val("Z", "sector", "cluster_members_min") == 3
    # 🔴 位置关**没有阈值可对表了**(裁定 #11:七个阈值键全删,只剩定性 guidance)
    # —— 逐引擎的定性准则由 `test_engine_pack_position_is_qualitative_only` 守。
    assert val("C", "evidence", "independent_evidence_min") == 3
    assert val("Z", "evidence", "independent_evidence_min") == 3
    assert val("Z", "evidence", "require_news_policy_source") is True
    assert val("Y", "evidence", "independent_evidence_min") == 2


# ══════════════════════════════════════════════════════════════════════════
# 🔴 裁定 #11:位置关零阈值 + `guidance` 是定性文本(不走 provenance 闸)
# ══════════════════════════════════════════════════════════════════════════

_RETIRED_POSITION_KEYS = (
    "t1_landing_states", "t2_landing_states", "pullback_depth_range", "landing_states",
    "dist_from_high_60d_min", "platform_days_min", "platform_amplitude_max",
)


def test_position_gate_schema_has_only_guidance_and_no_retired_thresholds():
    """2026-08-09 用户裁定 #11 的机器判据:位置关白名单**只剩 `guidance` 一个键**,
    七个老阈值键一个都不许回来(K8 §二 零个数字,那套翻译出来的阈值连乘交集近乎
    为空 —— 14 个 D0 回放零 T1)。⛔ 不重开。"""
    assert pack._ENGINE_GATE_SCHEMA["position"] == frozenset({"guidance"})
    for key in _RETIRED_POSITION_KEYS:
        assert key not in pack._ENGINE_GATE_SCHEMA["position"], key
    # 其余四关一字未动(裁定 11-a:范围只限位置关,⛔ 别顺手一起改)
    assert "primary_regimes" in pack._ENGINE_GATE_SCHEMA["market"]
    assert "industry_rank_max" in pack._ENGINE_GATE_SCHEMA["sector"]
    assert "leader_rs_rank_max" in pack._ENGINE_GATE_SCHEMA["core"]
    assert "independent_evidence_min" in pack._ENGINE_GATE_SCHEMA["evidence"]


def test_guidance_is_qualitative_text_and_skips_the_provenance_gate():
    """`guidance` 是定性文本不是阈值 → **不走 provenance 闸**(白名单里单列),
    但形状仍受校验:必须是非空字符串。"""
    doc = _minimal_engine_pack("C")
    assert pack.validate_pack_doc(doc) == []                       # 裸字符串,不带 provenance
    doc["config"]["engine"]["gates"]["position"]["guidance"] = "   "
    assert any("非空字符串" in e for e in pack.validate_pack_doc(doc))
    # 写成阈值叶子形状反而不对(它不是阈值,⛔ 别给它编一个 provenance)
    doc["config"]["engine"]["gates"]["position"]["guidance"] = _engine_leaf("文本")
    assert any("非空字符串" in e for e in pack.validate_pack_doc(doc))
    # 白名单仍然管用:位置段自创键照拒
    doc["config"]["engine"]["gates"]["position"] = {
        "guidance": "文本", "platform_days_min": _engine_leaf(40)}
    assert any("白名单外" in e and "platform_days_min" in e
               for e in pack.validate_pack_doc(doc))


@pytest.mark.parametrize("line", sorted(_ENGINE_PACK_FILES))
def test_real_engine_pack_position_is_guidance_only(line: str):
    """三个真实引擎包的 position 段:**恰一个 `guidance` 键、值是非空文本、零数字键**
    (文案取 plan §五 ③-F 位置关那三格)。"""
    doc = pack.load_pack_file(_ENGINE_PACK_FILES[line])
    position = doc["config"]["engine"]["gates"]["position"]
    assert set(position) == {"guidance"}
    assert isinstance(position["guidance"], str) and position["guidance"].strip()
    keyword = {"C": "健康回撤", "Z": "早期右侧启动", "Y": "中期平台"}[line]
    assert keyword in position["guidance"]
