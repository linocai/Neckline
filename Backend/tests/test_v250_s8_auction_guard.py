"""V2.5.0 S8 次日核对与 D1 结算的**结构性**守门(PROJECT_PLAN §10 G7/G20/G21 + 裁定 10)。

| # | 断言 |
|---|---|
| G7  | `auction/**`(含 `checklist.py` / `settle.py`)与 `playbook/evaluate.py` 零 import `neckline.llm` / `neckline.search` |
| G20 | 🔴 `ChecklistVerdict` **恰好两个**枚举成员;9:26 那一拍的代码里「成立」结构上够不着;`/api/checklist/{date}` 响应 schema 无「成立」取值 |
| G21 | 🔴 10:00 结算拍**零推送**:`settle.py` 零 import `notify` / `push`;`SettleRunResult` 没有 `should_push` |
| §5.7.3 | 两拍都跑在 `_morning_loop` 里;⛔ **零新增 systemd unit** |
| §5.6.3 | `playbook/evaluate.py` 是**唯一**求值实现(两拍共用) |
| K8 语义 | `auction/llm.py` / `auction/mech.py` / `auction/observation.py` **⛔ 没有被取回** |

⚠ 本文件是**结构**判据;行为判据在 `test_auction_checklist.py`。
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from typing import List, Set

import pytest

from neckline.auction import checklist as checklist_mod
from neckline.auction import settle as settle_mod
from neckline.playbook import evaluate as evaluate_mod
from neckline.playbook import model as pb_model
from tests import guard_scan

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "neckline"
_AUCTION = _PKG / "auction"
_PLAYBOOK = _PKG / "playbook"
_AUCTION_FILES = sorted(_AUCTION.glob("*.py"))
_PLAYBOOK_FILES = sorted(_PLAYBOOK.glob("*.py"))


def _imports(path: Path) -> Set[str]:
    return guard_scan.imports(path)


def test_scanner_sees_the_files_it_claims_to_guard():
    """扫描器自检:一个扫不到东西的闸门等于没有闸门。"""
    assert {p.name for p in _AUCTION_FILES} == {
        "__init__.py", "checklist.py", "collect.py", "pipeline.py",
        "quality.py", "settle.py", "store.py"}
    assert {"evaluate.py", "model.py", "store.py"} <= {p.name for p in _PLAYBOOK_FILES}


# ══════════════════════════════════════════════════════════════════════════
# G7 零 LLM(架构 §四:次日核对是「零 LLM,纯条件求值」)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("forbidden", [
    "neckline.llm", "neckline.search", "httpx", "openai", "requests",
])
def test_the_whole_auction_package_never_touches_an_llm(forbidden):
    hits: List[str] = []
    for path in _AUCTION_FILES + [_PLAYBOOK / "evaluate.py", _PLAYBOOK / "model.py"]:
        for mod in sorted(_imports(path)):
            if mod == forbidden or mod.startswith(forbidden + "."):
                hits.append(f"{path.name} → {mod}")
    assert hits == [], (
        "次日核对与 D1 结算**零 LLM**(架构 §四 / 裁定 10),这条边界被破了:\n"
        + "\n".join(hits))


def test_the_retired_k8_auction_modules_were_not_recovered():
    """🔴 `auction/llm.py`(489 行)与 `auction/mech.py`(1651 行,Z1/Y1/C1 三道
    夹逼闸)是 **K8 语义**,S8 明令⛔ 不许取回;`observation.py`(K8 的独立观察池)同。"""
    for gone in ("llm.py", "mech.py", "observation.py"):
        assert not (_AUCTION / gone).exists(), f"auction/{gone} 是 K8 语义,⛔ 不许取回"
    blob = "\n".join(p.read_text(encoding="utf-8") for p in _AUCTION_FILES)
    for banned in ("VERDICT_CONFIRM", "VERDICT_VETO", "VERDICT_NEUTRAL",
                   "clamped_by", "basket_id", "BasketRef"):
        assert banned not in blob, f"K8 竞价语义 `{banned}` 混进了 K9 的 auction 包"


# ══════════════════════════════════════════════════════════════════════════
# G20 🔴 「成立」在 9:26 那一拍**结构上不存在**(裁定 10)
# ══════════════════════════════════════════════════════════════════════════

def test_checklist_verdict_is_a_two_member_enum():
    """第一重锁:**类型层**。加第三个成员 = `checklist.py` 里那句 `assert` 让
    **import 就炸**,不是等到有人跑到某条分支才发现。"""
    assert len(checklist_mod.ChecklistVerdict) == 2
    assert [v.name for v in checklist_mod.ChecklistVerdict] == ["REJECTED", "PENDING_OPEN"]
    assert "成立" not in {v.value for v in checklist_mod.ChecklistVerdict}
    assert "成立" not in set(checklist_mod.CHECKLIST_SEGMENT_LABEL.values())


def test_checklist_module_can_never_reach_the_confirmation_branch():
    """第二重锁:**求值层**。`checklist.py` 的源码里
    `confirmation_branch` / `settle_verdict` / `Verdict.CONFIRMED` **零命中** ——
    它够不着那条分支,不是「记得别碰」。"""
    body = _code_without_docstrings(_AUCTION / "checklist.py")
    assert "rejection_branch" in body, "扫描器自检:放弃分支那一句得扫得到"
    for name in ("confirmation_branch", "settle_verdict", "CONFIRMED"):
        assert name not in body, (
            f"`{name}` 出现在 auction/checklist.py 里 —— 9:26 那一拍⛔ 不许碰成立分支(裁定 10)")


def test_the_auction_write_path_cannot_construct_confirmed():
    """第三重锁:**落库层**。二值 → 终值的映射是**两键全映射**,
    `Verdict.CONFIRMED` 在这条写路径上根本构造不出来。"""
    from neckline.auction import store as auction_store

    assert set(auction_store._AUCTION_FINAL) == set(checklist_mod.ChecklistVerdict)
    assert evaluate_mod.Verdict.CONFIRMED not in set(auction_store._AUCTION_FINAL.values())


def test_checklist_response_schema_has_no_confirmed_value():
    """`GET /api/checklist/{date}` 的响应体里**不存在「成立」这个取值**(G20)。
    这里直接对一张空表的 canonical 形状断言 —— 段是由**二值枚举**逐个生成的。"""
    from datetime import date, datetime, time

    from neckline.auction.checklist import Checklist

    cl = Checklist(trade_date=date(2024, 4, 30), d0_date=date(2024, 4, 29),
                   captured_at=datetime(2024, 4, 30, 9, 26, 30),
                   data_quality="ok", rows=())
    payload = cl.to_dict()
    assert [s["verdict"] for s in payload["segments"]] == ["rejected", "pending_open"]
    # 「成立」只准出现在那句脚注里(它是**说明**:成立由 10:00 结算)。
    assert json.dumps(payload, ensure_ascii=False).count("成立") == 1
    assert "成立由 10:00 结算" in payload["footnote"]


def test_auction_stage_readings_omit_everything_that_needs_the_open():
    """9:26 那一拍的读数表里**没有** `open_price` / `gap_pct` / `first30_high`
    —— K9 §6.3 四个成立分支全含「前 30 分钟」合取项,那时它还没发生。"""
    src = (_AUCTION / "checklist.py").read_text(encoding="utf-8")
    fn = src[src.index("def auction_readings"):src.index("@dataclass(frozen=True)\nclass ChecklistRow")]
    for absent in ("MetricRef.OPEN_PRICE", "MetricRef.GAP_PCT", "MetricRef.FIRST30_HIGH"):
        assert absent not in fn, f"9:26 的读数表⛔ 不许提供 {absent}"


# ══════════════════════════════════════════════════════════════════════════
# G21 🔴 10:00 结算拍零推送、不进 App 首屏(裁定 10)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("forbidden", ["neckline.api.notify", "neckline.push",
                                       "neckline.push.apns"])
def test_settle_never_imports_a_push_path(forbidden):
    hits = [m for m in sorted(_imports(_AUCTION / "settle.py"))
            if m == forbidden or m.startswith(forbidden + ".")]
    assert hits == [], f"结算拍**零推送**(裁定 10),但 settle.py import 了 {hits}"


def test_settle_result_has_no_push_gate():
    """`SettleRunResult` 里**没有** `should_push` —— 一个不存在的门槛不可能被误判。"""
    assert not hasattr(settle_mod.SettleRunResult, "should_push")
    fields = set(settle_mod.SettleRunResult.__dataclass_fields__)
    assert not any("push" in f for f in fields), fields


def test_verdicts_live_under_the_scoreboard_route_not_the_checklist_route():
    """🔴 结算拍的产物挂在 `scoreboard` 下 —— 「它属于成绩线、不属于早盘首屏」
    在**路由上**就看得出来(裁定 10)。"""
    from neckline.api.app import app

    paths = {r.path for r in app.routes}
    assert "/api/v1/scoreboard/verdicts/{trade_date}" in paths
    assert "/api/v1/checklist/{trade_date}/verdicts" not in paths
    assert not any(p.startswith("/api/v1/checklist") and "verdict" in p for p in paths)


# ══════════════════════════════════════════════════════════════════════════
# §5.7.3 两拍都在 `_morning_loop` 里,⛔ 零新增 systemd unit
# ══════════════════════════════════════════════════════════════════════════

def test_both_ticks_hang_off_the_morning_loop():
    """两拍**各自独立 `try/except`**;一拍炸了不影响另一拍。"""
    import neckline.api.app as app_mod

    src = (_PKG / "api" / "app.py").read_text(encoding="utf-8")
    loop = src[src.index("async def _morning_loop"):src.index("def _morning_checklist_tick")]
    assert loop.count("try:") >= 3          # 两拍各一个 + 轮询等待那一个
    assert "_morning_checklist_tick(now)" in loop
    assert "_morning_settle_tick(now)" in loop
    assert hasattr(app_mod, "_morning_checklist_tick")
    assert hasattr(app_mod, "_morning_settle_tick")


def test_the_settle_tick_helper_never_pushes():
    """`_morning_settle_tick` 里**一行 `notify` 都没有**(G21 的接线侧)。"""
    src = (_PKG / "api" / "app.py").read_text(encoding="utf-8")
    body = src[src.index("def _morning_settle_tick"):]
    body = body[:body.index("\n\n\n")] if "\n\n\n" in body else body
    assert "notify." not in body


def test_no_new_systemd_unit_was_added():
    """🔴 §9.3:本版**零新增 systemd unit**。两拍都跑在既有常驻 `neckline.service` 里
    —— 多一个 unit 就多一条双触发路径,而「今天跑没跑过」应当是查台账、不是现场推理。"""
    deploy = _ROOT / "deploy"
    units = sorted(p.name for p in deploy.glob("*.service")) + \
        sorted(p.name for p in deploy.glob("*.timer")) + \
        sorted(p.name for p in deploy.glob("*.target"))
    assert set(units) == {
        "neckline.service",
        "neckline-daily.service", "neckline-daily.timer",
        "neckline-scan.service", "neckline-basket.service", "neckline-report.service",
        "neckline-evening.target", "neckline-evening.timer",
    }, units
    blob = "\n".join((deploy / u).read_text(encoding="utf-8") for u in units)
    for banned in ("checklist", "settle", "auction"):
        assert banned not in blob.lower(), (
            f"deploy/ 里出现了 `{banned}` —— 两拍⛔ 不许有自己的 unit(§9.3)")


# ══════════════════════════════════════════════════════════════════════════
# §5.6.3 求值器唯一实现(两拍共用)
# ══════════════════════════════════════════════════════════════════════════

def test_there_is_exactly_one_evaluator():
    """全仓「代入预案条件求值」只有 `playbook/evaluate.py` 一处实现:
    两拍都 import 它,⛔ 没有第二个 `evaluate_branch` 定义。"""
    defs: List[str] = []
    for path in sorted(_PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in (
                    "evaluate_branch", "evaluate_condition", "settle_verdict"):
                defs.append(f"{path.relative_to(_PKG)}::{node.name}")
    assert sorted(defs) == [
        "playbook/evaluate.py::evaluate_branch",
        "playbook/evaluate.py::evaluate_condition",
        "playbook/evaluate.py::settle_verdict",
    ], defs
    # 两拍都真的在用它。
    assert "neckline.playbook.evaluate" in _imports(_AUCTION / "checklist.py")
    assert "neckline.playbook.evaluate" in _imports(_AUCTION / "settle.py")


def test_metric_ref_is_a_closed_enum_of_exactly_the_nine_documented_values():
    """§5.6.3 逐字给的九个量。加一个成员必须同时回答「两拍各自从哪里读到它」。"""
    assert {m.value for m in pb_model.MetricRef} == {
        "auction_price", "auction_gap_pct", "open_price", "gap_pct",
        "first30_low", "first30_high", "prev_close", "prev_low", "prev_high"}
    assert {o.value for o in pb_model.Op} == {"<=", ">=", "<", ">"}
    assert {b.value for b in pb_model.BranchName} == {"成立", "放弃"}
    assert pb_model.DEFAULT_BRANCH == "观察"


def test_the_three_way_verdict_is_closed_and_fully_labelled():
    assert {v.value for v in evaluate_mod.Verdict} == {"confirmed", "rejected", "observed"}
    assert set(evaluate_mod.VERDICT_LABEL) == set(evaluate_mod.Verdict)


def _code_without_docstrings(path: Path) -> str:
    return guard_scan.code_without_docstrings(path)


def test_playbook_store_has_no_update_or_delete():
    """append-only:用户改动写**新版本**,⛔ 不覆盖原冻结版本(K9 §6.4)。"""
    lowered = _code_without_docstrings(_PLAYBOOK / "store.py").lower()
    assert "insert into k9_playbooks" in lowered or "insert into {table}" in lowered \
        or "insert into" in lowered, "扫描器自检:落库语句得扫得到"
    for banned in ("update k9_playbooks", "delete from k9_playbooks",
                   "insert or replace into k9_playbooks", f"update {{table}}",
                   f"delete from {{table}}"):
        assert banned not in lowered, f"预案表是 append-only,⛔ 不许 `{banned}`"


def test_evaluator_is_a_total_function_over_the_closed_enum():
    """闭合枚举 + 三值 → **全函数**:九个量随便缺哪些,求值都不抛。"""
    from neckline.playbook.model import Branch, BranchName, Condition, Op

    b = Branch(name=BranchName.REJECTED, all=tuple(
        Condition(op=Op.LT, lhs=m, rhs=1.0) for m in pb_model.MetricRef))
    out = evaluate_mod.evaluate_branch(b, {})
    assert out.truth is evaluate_mod.Truth.UNKNOWN
    assert len(out.missing) == len(pb_model.MetricRef)
