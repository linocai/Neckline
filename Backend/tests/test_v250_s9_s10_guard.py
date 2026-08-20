"""V2.5.0 S9/S10 解释层与预案层的**结构性**守门(PROJECT_PLAN §10 G5/G6 + §5.2 边界③④)。

| # | 断言 |
|---|---|
| G5 | `explain/**` 零 import `neckline.k9`;`ExplainInput` 字段集**逐字**相等;⛔ 不含通道身份与排序位次;输入按 `ts_code` 升序 |
| G6 | `PlaybookInput` 字段集**逐字**相等 —— **含** `patterns`,**⛔ 不含** `rank`/`score`/`seat`/`tier`/`upside_room_mech*` |
| 裁定 1 | 三个价位与 `upside_room_mech*` 在 DTO / 表 / 文案里**名称互不重叠**;`k9/**` 内 `first_resistance` 零命中 |
| §5.5 | 补位决定住在**编排器**里(解释层不知道名次)—— `explain/**` 里 `reserve` / `rank` 零命中 |
| §5.6.4 | 预案 append-only:`k9_playbooks` 只有 INSERT |

⚠ 本文件是**结构**判据;行为判据在 `test_explain_playbook.py`。
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import List, Set

import pytest

from neckline.explain import input as explain_input
from tests import guard_scan
from neckline.playbook import model as pb_model

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "neckline"
_EXPLAIN = _PKG / "explain"
_PLAYBOOK = _PKG / "playbook"
_K9 = _PKG / "k9"
_EXPLAIN_FILES = sorted(_EXPLAIN.glob("*.py"))


def _imports(path: Path) -> Set[str]:
    return guard_scan.imports(path)


def _code_without_docstrings(path: Path) -> str:
    return guard_scan.code_without_docstrings(path)


def test_scanner_sees_the_files_it_claims_to_guard():
    assert {p.name for p in _EXPLAIN_FILES} == {
        "__init__.py", "aggregate.py", "input.py", "news_exclusion.py", "store.py"}
    assert {"__init__.py", "evaluate.py", "fill.py", "model.py", "skeleton.py",
            "store.py"} == {p.name for p in _PLAYBOOK.glob("*.py")}


# ══════════════════════════════════════════════════════════════════════════
# G5 解释层的双盲
# ══════════════════════════════════════════════════════════════════════════

def test_explain_never_imports_the_strategy_layer():
    """🔴 架构 §3.3:解释层**不知道**票是哪个通道选出来的。"""
    hits: List[str] = []
    for path in _EXPLAIN_FILES:
        for mod in sorted(_imports(path)):
            if mod == "neckline.k9" or mod.startswith("neckline.k9."):
                hits.append(f"{path.name} → {mod}")
    assert hits == [], "解释层零 import `neckline.k9`,这条边界被破了:\n" + "\n".join(hits)


def test_explain_input_field_set_is_frozen_verbatim():
    """字段集冻结:加字段必须**先改那个列表** = 一次自觉行为(§5.2 边界③ 第 1 条)。"""
    actual = tuple(f.name for f in dataclasses.fields(explain_input.ExplainInput))
    assert actual == explain_input.EXPLAIN_INPUT_FIELDS, (
        "`ExplainInput` 的字段集变了却没改 `EXPLAIN_INPUT_FIELDS` —— "
        "那个列表就是双盲的自觉闸")


@pytest.mark.parametrize("root", list(explain_input.EXPLAIN_INPUT_FORBIDDEN))
def test_explain_input_carries_no_channel_identity_or_rank(root: str):
    """🔴 **⛔ 不含**通道身份(`pattern` / `channel` / `recall`)与排序位次
    (`rank` / `score` / `tier` / `seat`),也不含 `upside_room*`。"""
    bad = [f for f in explain_input.EXPLAIN_INPUT_FIELDS if root in f]
    assert bad == [], f"`ExplainInput` 里出现了 `{root}` 词根的字段:{bad}"


_RANKING_NAMES = ("reserve", "rank", "score", "seat_kind", "primary_pattern",
                  "patterns", "tier")


def test_explain_layer_never_reads_a_rank_or_a_seat():
    """🔴 §5.5:**补位决定由编排器做**(它才知道排名)。

    判据是 **AST**:解释层有没有真的去**读** `reserve` / `rank` / `seat_kind` /
    `primary_pattern` 这些名字。⚠ 刻意不用文本扫描 ——
    `EXPLAIN_INPUT_FORBIDDEN` 那个**黑名单常量**里就装着这些词,
    「声明了什么不许有」与「真的去读了它」是两件事。"""
    hits = guard_scan.touched_any(_EXPLAIN_FILES, _RANKING_NAMES)
    assert hits == [], "解释层真的读了名次 / 席位 / 形态:\n" + "\n".join(hits)


def test_the_ast_scanner_actually_catches_a_rank_read():
    """扫描器自检:一个扫不到东西的闸门等于没有闸门。
    编排器**确实**读了 `shortlist.reserve` 与 `e.rank` —— 扫得到它才算数。"""
    hits = guard_scan.touched_any([_PKG / "report" / "evening.py"], _RANKING_NAMES)
    assert any("reserve" in h for h in hits) and any("rank" in h for h in hits)


def test_the_orchestrator_is_the_one_that_knows_the_ranking():
    """双盲的另一半:知道名次的那一段**必须**待在编排器里。"""
    body = _code_without_docstrings(_PKG / "report" / "evening.py")
    assert "shortlist.reserve" in body
    assert "_run_explain" in body


def test_build_inputs_sorts_before_returning():
    """位次也会从**列表顺序**泄漏 —— 排序发生在 `build_inputs` 里,而且只此一处。"""
    body = _code_without_docstrings(_EXPLAIN / "input.py")
    assert "sorted(" in body


# ══════════════════════════════════════════════════════════════════════════
# G6 预案层的输入
# ══════════════════════════════════════════════════════════════════════════

def test_playbook_input_field_set_is_frozen_verbatim():
    actual = tuple(f.name for f in dataclasses.fields(pb_model.PlaybookInput))
    assert actual == pb_model.PLAYBOOK_INPUT_FIELDS


def test_playbook_input_contains_patterns():
    """🔴 **含** `patterns`:骨架按形态套用(架构 §四 第 4 条「预案层知道形态」)。"""
    fields = set(pb_model.PLAYBOOK_INPUT_FIELDS)
    assert "patterns" in fields and "primary_pattern" in fields


@pytest.mark.parametrize("root", list(pb_model.PLAYBOOK_INPUT_FORBIDDEN))
def test_playbook_input_carries_no_rank_score_seat_tier_or_mech_room(root: str):
    """🔴 §5.2 边界④:⛔ 不含 `rank` / `score` / `seat` / `tier` / `upside_room_mech*`。

    ⚠ 最后一项尤其要紧(裁定 1):把排序用的**机械空间**喂给预案 LLM,
    等于邀请它把那个数原样吐回来当「第一压力位」,循环依赖当场复活。"""
    bad = [f for f in pb_model.PLAYBOOK_INPUT_FIELDS if root in f]
    assert bad == [], f"`PlaybookInput` 里出现了 `{root}` 词根的字段:{bad}"


def test_the_playbook_layer_never_reads_a_rank_or_the_mechanical_room():
    """预案层有没有真的去**读**名次 / 得分 / 席位 / 机械空间(AST 判据,见上)。

    ⚠ `patterns` / `primary_pattern` **不在黑名单里** —— 预案层**知道形态**
    (架构 §四 第 4 条),骨架就是按它套用的。"""
    hits = guard_scan.touched_any(
        sorted(_PLAYBOOK.glob("*.py")),
        ("rank", "score", "seat_kind", "upside_room_mech", "upside_room_mech_pct",
         "reserve"))
    assert hits == [], "预案层真的读了排序侧的东西:\n" + "\n".join(hits)


def test_the_playbook_layer_makes_no_quality_judgement():
    """架构 §四 第 4 条:预案层**知道形态,但不做好坏评价**。

    结构性判据:`Condition` / `Branch` / `Levels` 三个 dataclass 的字段里
    **一个自由文本位都没有**(`Branch.name` 是闭合枚举)。"""
    for cls in (pb_model.Condition, pb_model.Branch, pb_model.Levels):
        for f in dataclasses.fields(cls):
            assert f.type != "str", f"{cls.__name__}.{f.name} 是自由文本位"
    # 骨架的槽位也只有两种量纲,没有「理由」这类键。
    from neckline.playbook import skeleton as sk

    for pattern in sk.SKELETONS:
        for slot in sk.all_slots(pattern):
            assert slot.kind in sk.KINDS


# ══════════════════════════════════════════════════════════════════════════
# 裁定 1 命名铁律:两个量永不互相顶替
# ══════════════════════════════════════════════════════════════════════════

def test_the_two_room_names_never_overlap():
    """🔴 裁定 1:`first_resistance`(LLM,预案)与 `upside_room_mech*`(机械,排序)
    在 DTO / 表 / 文案里**全部分开**。

    · `k9/**` 内 `first_resistance` 零命中(G11 的一半,S6 已立;这里再确认);
    · `playbook/**` 与 `explain/**` 内 `upside_room_mech` 零命中。"""
    k9_hits = [p.name for p in _K9.rglob("*.py")
               if "first_resistance" in p.read_text(encoding="utf-8")]
    assert k9_hits == [], f"策略层里出现了预案层的价位名:{k9_hits}"
    for pkg in (_PLAYBOOK, _EXPLAIN):
        hits = guard_scan.touched_any(sorted(pkg.rglob("*.py")),
                                      ("upside_room_mech", "upside_room_mech_pct"))
        assert hits == [], f"{pkg.name} 里真的读了排序用的机械空间:{hits}"


def test_the_playbook_table_has_no_odds_column():
    """三个价位**分三列存**,⛔ 不给任何「赔率」合计列 —— 赔率由收盘价现算,
    存下来只会在用户改了价位之后变成一个对不上的旧数。"""
    ddl = (_PKG / "db.py").read_text(encoding="utf-8")
    block = ddl[ddl.index("CREATE TABLE IF NOT EXISTS k9_playbooks"):]
    block = block[:block.index(");")]
    for banned in ("odds", "ratio", "reward"):
        assert banned not in block.lower(), f"k9_playbooks 里出现了合计列 `{banned}`"


# ══════════════════════════════════════════════════════════════════════════
# 三态与四类的闭合性
# ══════════════════════════════════════════════════════════════════════════

def test_news_state_is_exactly_three_and_category_exactly_four():
    from neckline.explain import news_exclusion as nm

    assert {s.value for s in nm.NewsState} == {"clean", "excluded", "unverified"}
    assert len(nm.NewsCategory) == 4
    assert set(nm.CATEGORY_LABEL) == set(nm.NewsCategory)


def test_the_llm_task_surface_grew_by_exactly_the_two_new_posts():
    """架构 §八「LLM 的三个岗位」:解释层与预案层各一个新任务常量。
    ⚠ 第一个岗位(事实层方向解读)本版仍未建。"""
    from neckline.llm import router

    assert router.TASK_EXPLAIN in router.ALL_TASKS
    assert router.TASK_PLAYBOOK in router.ALL_TASKS
    # 🔴 K9 的次日核对是**零 LLM** —— `TASK_AUCTION` 在生产链上零调用,
    # 只为让老库里存过的路由行仍解得出来。
    body = "\n".join(
        _code_without_docstrings(p)
        for p in sorted((_PKG / "auction").glob("*.py")))
    assert "TASK_AUCTION" not in body
