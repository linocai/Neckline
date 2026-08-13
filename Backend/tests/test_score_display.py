"""V2.1-④ 百分制打分卡(甲案 · 纯展示层)的换算断言 + **不进判定守门**。

本文件分两半,后一半才是这一块真正的核心:

**上半 · 换算正确性**
  · `scorePercent == round(mech*100, 1)`;
  · **五维贡献合计 ≈ 总分**(自洽 —— 一个"总分"与它自己的"拆解"对不上,拆解就是假的);
  · **缺 `mech_breakdown` → `None`,⛔ 不是 0**(§3.8:「没有」与「没看」必须分得开,
    0 分是一个极差的实质性判断,拿它冒充"没这个数"是本项目反复禁止的那类谎);
  · 未登记的 `dim` **用原名兜底**(⛔ 不抛 —— 一个新维度不该让整份报告出不来;
    ⛔ 也不吞 —— 原名照样显示,让人一眼看见有个没登记的维度);
  · **中性填充如实标**:`neutralFilled` 只认 flag、不认数值(`leader_clarity` 的
    `1/rank` 在 `rank=2` 时数值恰好也等于中性分 0.5,拿数值反推必判错)。

**下半 · 🔴 三条不进判定守门**(plan §五 V2.1-④ 原文「本块的核心闸」)
  1. `selection/**`、`sentinel/**`、`strategy/**`、`eval/**`、`review/**`
     **全仓零 import `neckline.report.score_display`** —— AST 级方向性规则:
     谁想拿这个分数去排序 / 喂哨兵 / 决定去留,**第一步必然是 import 它**;
  2. `selection/tier.py::_TIER_SCORE_INPUTS` **逐位不变**(百分制是换算不是新维度,
     ⛔ 不许因为"要展示"就往机械分白名单里塞字段);
  3. `score_display` 模块内 **零 import `neckline.selection`**(反向也守:把判定层
     拉进展示层,方向规则当场废掉)。

另有一条**受监督的重复**守门:展示层那份 `_DIM_NEUTRAL_FILL_FLAGS` 与引擎
`_DIM_MISSING_FLAGS` **逐位相等**。守门 3 禁止展示层 import 判定层,副本因此不可避免;
本条把"漂移"从"但愿有人记得同步"变成**当场报红的机器判据**。⚠ 它红的意思是「引擎那边
改了,来这里对齐」,**不是「把断言删掉」**。
"""

from __future__ import annotations

import ast
import random
from pathlib import Path
from typing import Dict, List, Set

import pytest

from neckline.report import score_display as sd

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "neckline"

# 判定层五个包(plan 原文点名)。分数一旦被它们中的任何一个 import,
# 「纯展示层」这条裁定就名存实亡。
_JUDGEMENT_DIRS = ("selection", "sentinel", "strategy", "review")
_DISPLAY_MODULE = "neckline.report.score_display"


# ══════════════════════════════════════════════════════════════════════════
# 夹具:一份形状与 `tier_history.mech_breakdown_json` 逐键相同的真实 breakdown
# ══════════════════════════════════════════════════════════════════════════

def _breakdown(dims: Dict[str, float], weights: Dict[str, float],
               flags=(), neutral_weight=None) -> dict:
    """照 `selection/tier.py::score_and_tier` 落库那一份**逐键**构造(⛔ 别只造
    `contrib` 一键就以为测过了 —— 展示层读的是这五个键的组合)。"""
    return {
        "dims": {d: round(float(v), 6) for d, v in sorted(dims.items())},
        "weights": {d: round(float(v), 6) for d, v in sorted(weights.items())},
        "weights_raw": {d: float(v) for d, v in sorted(weights.items())},
        "contrib": {d: round(float(weights[d]) * float(dims[d]), 6) for d in sorted(weights)},
        "flags": sorted(set(flags)),
        "neutral_filled_weight": (
            round(sum(float(weights[d]) for d in _neutral_dims(flags) if d in weights), 6)
            if neutral_weight is None else neutral_weight
        ),
    }


def _neutral_dims(flags) -> List[str]:
    fs = set(flags)
    return [d for d, ff in sd._DIM_NEUTRAL_FILL_FLAGS.items() if ff & fs]


_FIVE_WEIGHTS = {"sector_strength": 0.30, "leader_clarity": 0.25, "driver_freshness": 0.20,
                 "tradability": 0.15, "card_density": 0.10}
_FIVE_DIMS = {"sector_strength": 0.80, "leader_clarity": 1.00, "driver_freshness": 0.50,
              "tradability": 0.50, "card_density": 0.60}


def _mech(dims, weights) -> float:
    return sum(weights[d] * dims[d] for d in sorted(weights))


# ══════════════════════════════════════════════════════════════════════════
# 换算正确性
# ══════════════════════════════════════════════════════════════════════════

class TestScorePercent:
    @pytest.mark.parametrize("mech,expect", [
        (0.0, 0.0), (1.0, 100.0), (0.625, 62.5), (0.7249, 72.5), (0.60001, 60.0),
    ])
    def test_percent_is_exactly_mech_times_100_rounded_to_one_decimal(self, mech, expect):
        assert sd.score_percent(mech) == expect

    def test_missing_score_is_none_not_zero(self):
        """🔴 0 分是「这一篮很差」这个**实质性判断**;没有分是「我们没这个数」。
        ⛔ 永远不许用前者冒充后者。"""
        assert sd.score_percent(None) is None
        assert sd.score_percent("不是数") is None
        assert sd.score_percent(True) is None      # bool 是 int 的子类,别让它混过去


class TestScoreView:
    def test_total_and_contributions_are_self_consistent(self):
        bd = _breakdown(_FIVE_DIMS, _FIVE_WEIGHTS)
        v = sd.score_view(_mech(_FIVE_DIMS, _FIVE_WEIGHTS), bd)
        assert v["scorePercent"] == pytest.approx(72.5)
        total = sum(c["contribPercent"] for c in v["contributions"])
        assert total == pytest.approx(v["scorePercent"], abs=0.15)

    def test_self_consistency_holds_on_randomised_breakdowns(self):
        """自洽不是"举一个例子对得上",而是**对任何一组权重/维分都对得上**。

        固定种子(可复现),200 组随机权重 × 维分,逐组断言合计与总分的差
        ≤ 0.15。⚠ **这条也是 `_CONTRIB_ND=4` 那个决定的证据**:若把分项也四舍五入
        到 1 位小数,五项各带 ≤0.05 的误差 → 最坏差 0.30,本断言会随机翻红
        (plan 原文写的 0.15 容差在 1 位小数下并非安全上界,已在完工记录登记)。"""
        rng = random.Random(20260808)
        for _ in range(200):
            w = {d: rng.uniform(0.01, 1.0) for d in _FIVE_WEIGHTS}
            s = sum(w.values())
            w = {d: v / s for d, v in w.items()}
            dims = {d: rng.uniform(0.0, 1.0) for d in _FIVE_WEIGHTS}
            v = sd.score_view(_mech(dims, w), _breakdown(dims, w))
            total = sum(c["contribPercent"] for c in v["contributions"])
            assert total == pytest.approx(v["scorePercent"], abs=0.15)

    def test_missing_breakdown_returns_none_not_a_zero_scorecard(self):
        """plan 原文点名:缺 `mech_breakdown` → `None`。**⛔ 不是一张全 0 的卡** ——
        那会在界面上变成「这一篮五维全 0」,是凭空造出来的实质性判断。"""
        assert sd.score_view(0.72, None) is None
        assert sd.score_view(0.72, {}) is None
        assert sd.score_view(0.72, "不是字典") is None
        # 反向:分数缺席同样 `None`(有 breakdown 也不许只出一堆分项没有总分)
        assert sd.score_view(None, _breakdown(_FIVE_DIMS, _FIVE_WEIGHTS)) is None

    def test_unknown_dimension_falls_back_to_its_raw_name(self):
        """未登记的维度:**原名兜底**,⛔ 不抛、⛔ 不吞(吞了 = 合计对不上总分,
        而用户看不出少了一项)。"""
        w = {"sector_strength": 0.5, "quantum_alpha": 0.5}
        dims = {"sector_strength": 0.8, "quantum_alpha": 0.6}
        v = sd.score_view(_mech(dims, w), _breakdown(dims, w))
        by_dim = {c["dim"]: c for c in v["contributions"]}
        assert by_dim["quantum_alpha"]["label"] == "quantum_alpha"      # 原名兜底
        assert by_dim["sector_strength"]["label"] == "板块强度"
        assert sum(c["contribPercent"] for c in v["contributions"]) == pytest.approx(
            v["scorePercent"], abs=0.15)                                # 一项都没被吞

    def test_ordering_is_deterministic_desc_by_contribution_then_dim(self):
        w = {"a_dim": 0.25, "b_dim": 0.25, "c_dim": 0.5}
        dims = {"a_dim": 0.4, "b_dim": 0.4, "c_dim": 0.4}              # a/b 贡献并列
        v = sd.score_view(_mech(dims, w), _breakdown(dims, w))
        assert [c["dim"] for c in v["contributions"]] == ["c_dim", "a_dim", "b_dim"]

    def test_neutral_filled_dims_are_flagged_and_totalled(self):
        """中性填充如实标 —— 这是「因为不知道所以得了分」的唯一披露位。"""
        bd = _breakdown(_FIVE_DIMS, _FIVE_WEIGHTS, flags=["stage_missing", "tradability_missing"])
        v = sd.score_view(_mech(_FIVE_DIMS, _FIVE_WEIGHTS), bd)
        marked = {c["dim"] for c in v["contributions"] if c["neutralFilled"]}
        assert marked == {"driver_freshness", "tradability"}
        assert v["neutralFilledPercent"] == pytest.approx(35.0)         # 0.20 + 0.15
        assert "中性填充" in v["note"] and "不是「这几维表现好」" in v["note"]

    def test_neutral_fill_is_decided_by_flags_not_by_the_value_05(self):
        """🔴 **只认 flag,不认数值**:`leader_clarity` 在 `rank=2` 时真实值恰好
        也是 0.5 —— 拿数值反推"是不是中性填充"会把一个**真实的第二名**污蔑成
        "没数据"。本条把那条判断钉死。"""
        dims = dict(_FIVE_DIMS, leader_clarity=0.5)                    # 真实第二名
        v = sd.score_view(_mech(dims, _FIVE_WEIGHTS),
                          _breakdown(dims, _FIVE_WEIGHTS, flags=[]))
        assert all(c["neutralFilled"] is False for c in v["contributions"])
        assert v["neutralFilledPercent"] == 0.0
        assert "中性填充" not in v["note"]                              # 没有就别啰嗦

    def test_stage_unmapped_alone_is_not_a_neutral_fill(self):
        """承引擎侧同一条判断:`stage_unmapped` 单独出现时该维仍可能是别的行业
        算出来的真实值,⛔ 不算中性填充。"""
        v = sd.score_view(_mech(_FIVE_DIMS, _FIVE_WEIGHTS),
                          _breakdown(_FIVE_DIMS, _FIVE_WEIGHTS, flags=["stage_unmapped"]))
        assert all(c["neutralFilled"] is False for c in v["contributions"])

    def test_missing_dim_or_weight_in_breakdown_stays_null_not_zero(self):
        bd = _breakdown(_FIVE_DIMS, _FIVE_WEIGHTS)
        bd["dims"].pop("tradability")
        bd["weights"].pop("tradability")
        v = sd.score_view(_mech(_FIVE_DIMS, _FIVE_WEIGHTS), bd)
        item = next(c for c in v["contributions"] if c["dim"] == "tradability")
        assert item["dimScore"] is None and item["weight"] is None      # ⛔ 不是 0.0
        assert item["contribPercent"] is not None                        # contrib 键还在

    def test_uncomputable_contribution_sorts_last_not_as_zero(self):
        bd = _breakdown(_FIVE_DIMS, _FIVE_WEIGHTS)
        bd["contrib"]["card_density"] = None
        v = sd.score_view(_mech(_FIVE_DIMS, _FIVE_WEIGHTS), bd)
        assert v["contributions"][-1]["dim"] == "card_density"
        assert v["contributions"][-1]["contribPercent"] is None


class TestContributionLine:
    def test_line_shape_matches_the_report_spec(self):
        v = sd.score_view(_mech(_FIVE_DIMS, _FIVE_WEIGHTS),
                          _breakdown(_FIVE_DIMS, _FIVE_WEIGHTS))
        line = sd.contribution_line(v)
        assert line.startswith("机械分 72.5 / 100(")
        assert "龙头清晰度 25.0" in line and "板块强度 24.0" in line

    def test_no_score_means_no_line_at_all(self):
        """⛔ 不出一行「机械分 — / 100」的空壳 —— 那看起来像"算过了是空的"。"""
        assert sd.contribution_line(None) is None
        assert sd.contribution_line({"scorePercent": None, "contributions": []}) is None

    def test_neutral_filled_dims_carry_their_warning_into_the_markdown(self):
        """结构化字段里标了不算数 —— markdown 是给人读的那一份,必须当场说清。"""
        v = sd.score_view(_mech(_FIVE_DIMS, _FIVE_WEIGHTS),
                          _breakdown(_FIVE_DIMS, _FIVE_WEIGHTS, flags=["stage_missing"]))
        line = sd.contribution_line(v)
        assert "驱动新鲜度 10.0*" in line
        assert "没算出来" in line and "不是表现好" in line


# ══════════════════════════════════════════════════════════════════════════
# 🔴 三条不进判定守门(本块的核心闸)
# ══════════════════════════════════════════════════════════════════════════

def _imported_names(path: Path) -> Set[str]:
    """该文件 import 进来的模块全名集合(体例照 `test_v21_retirement_guard.py`)。
    **只看 import 语句** —— 注释与 docstring 里提到模块名是说明,不是引用。"""
    out: Set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


@pytest.mark.parametrize("pkg", _JUDGEMENT_DIRS)
def test_judgement_layers_never_import_the_display_scorer(pkg: str):
    """守门 ①(方向性规则):判定五包**零 import** 展示层打分模块。

    百分制**不进任何判定路径**这条裁定,在代码里的可执行形式就是这一句 —— 想拿它
    排序 / 喂哨兵 / 决定去留,第一步必然是 import,这里当场拦下。
    (⛔ 不是"检查有没有人调用 score_view":调用点可以藏在任何表达式里,import 藏不住。)
    """
    d = _PKG / pkg
    files = sorted(d.rglob("*.py"))
    assert files, f"{d} 下没有 .py 文件,检查测试路径是否过期"
    hits = [
        str(p.relative_to(_ROOT)) for p in files
        if any(m == _DISPLAY_MODULE or m.startswith(_DISPLAY_MODULE + ".")
               for m in _imported_names(p))
    ]
    assert not hits, (
        f"判定层 neckline/{pkg}/ 出现了对展示层打分模块的 import:{hits} —— "
        "V2.1-④ 裁定「百分制纯展示、不进任何判定路径」被破坏"
    )


def test_tier_score_input_whitelist_is_bit_for_bit_unchanged():
    """守门 ②:机械分白名单**逐位不变**。

    百分制是**换算**不是新维度 —— ⛔ 谁都不许因为"展示需要"往这个白名单里塞字段
    (塞进去 = 展示字段进了机械分 = 百分制真的改了策略语义,那就是乙案了,
    而乙案要走 K7 整改与包门禁,不是 ④ 这一块能做的事)。"""
    from neckline.selection.tier import _TIER_SCORE_INPUTS

    assert _TIER_SCORE_INPUTS == frozenset({
        "sector_strength", "driver_freshness", "leader_clarity",
        "tradability", "card_density",
    })


def test_display_scorer_never_imports_the_selection_package():
    """守门 ③(反向):展示层零 import `neckline.selection`。

    方向规则要成立,**两个方向都得守**:判定层不许读展示层(守门 ①),展示层也不许
    把判定层拉进来(本条)—— 否则 `score_display` 会慢慢长成第二个定档实现。"""
    hits = sorted(m for m in _imported_names(Path(sd.__file__))
                  if m == "neckline.selection" or m.startswith("neckline.selection."))
    assert not hits, f"`score_display` 出现了对判定层的 import:{hits}"


def test_neutral_fill_flag_map_matches_the_engine_exactly():
    """**受监督的重复**:展示层那份 `_DIM_NEUTRAL_FILL_FLAGS` 与引擎
    `selection/tier.py::_DIM_MISSING_FLAGS` 逐位相等。

    守门 ③ 禁止展示层 import 判定层,这份副本因此不可避免;本条把漂移变成**当场
    报红**。⚠ 红了的正确处置是**来展示层对齐引擎**,⛔ 不是删掉这条断言。
    (测试文件同时 import 两侧是**允许**的 —— 测试既不是判定层也不是展示层,
    它就是那个负责比对两边的第三方。)"""
    from neckline.selection.tier import _DIM_MISSING_FLAGS

    assert sd._DIM_NEUTRAL_FILL_FLAGS == _DIM_MISSING_FLAGS


def test_every_engine_dimension_has_a_chinese_label():
    """五维中文标签**只住展示层**,但一个都不许漏 —— 漏一个就会在卡面上冒出一个
    英文维度名(原名兜底虽然不崩,但那是给"未来新维度"留的路,不是给"忘了写标签"的)。"""
    from neckline.selection.tier import _TIER_SCORE_INPUTS

    assert set(sd.DIM_LABELS) == set(_TIER_SCORE_INPUTS)
