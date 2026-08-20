"""V2.5.0 S5 参数包契约的**结构性**守门(PROJECT_PLAN §10 G2/G3/G8/G9/G22)。

本文件锁五件事:

| # | 断言 | 出处 |
|---|---|---|
| G8 | `K9Params`(含嵌套)**每个字段无默认值**;⛔ 无默认路径 | §5.4.3 校验 4 |
| G22 | 三个「取值待标定」的参数位:候选取值全部实现,**代码里不存在「哪个是默认」的分支** | §8.3 #18–#20 / §7.6 |
| G9 | 参数缺失 → `ReportState.not_run`,⛔ 不是 `empty`;首行**全映射**渲染无 fallback | §5.10 |
| G2 | `k9/**` ⛔ 不 import `llm` / `search` / `httpx` / `openai` / `requests` / `urllib` / `socket` | §5.4.1 |
| G3 | `k9/**` ⛔ 不 import `tushare_client` / `market_data`(取数唯一来源是事实包) | §5.4.1 |

⚠ **G2 / G3 本来是 §6 S6 的验收项**,本片提前落位:`k9/params.py` 已经存在,
一条现在就成立的边界没有理由等到下一片才立起来。S6 加通道时**扩充**本文件,
⛔ 不要另起一份。

⚠ **「无默认分支」怎么机器判**:三个参数位都是**闭合枚举**,解析走 `Enum(value)`
的全映射。守门扫的是那些真正能造出「悄悄退回某个取值」的写法 ——
`.get(x, <枚举成员>)`、`or <枚举成员>`、`= <枚举成员>` 当默认实参、
`if 取值不认识: 用某个成员`。⛔ 不做裸文本 grep(枚举成员名本来就要在定义处出现)。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import List, Set, Tuple

import pytest

from neckline.k9 import params as P
from neckline.report.state import ReportState
from tests import guard_scan

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "neckline"
_K9 = sorted((_PKG / "k9").rglob("*.py"))

#: §5.4.1 第 1 组:策略层内**没有 LLM 调用**,也没有任何联网能力。
FORBIDDEN_NETWORK: Tuple[str, ...] = (
    "neckline.llm", "neckline.search", "httpx", "openai", "requests", "urllib", "socket",
)
#: §5.4.1 第 2 组:「取数唯一来源是事实包」的**真牙齿**。
FORBIDDEN_DATA: Tuple[str, ...] = (
    "neckline.data.tushare_client", "neckline.data.market_data",
)

#: 三个枚举类的全部成员名(用来找「谁被当成默认」)。
_ENUM_MEMBER_NAMES: Set[str] = {
    m.name for cls in (P.HeatAbsentPolicy, P.RelaySource, P.RelayScoring) for m in cls
}
_ENUM_CLASS_NAMES: Set[str] = {"HeatAbsentPolicy", "RelaySource", "RelayScoring"}


def test_scan_covers_the_k9_package():
    assert {p.name for p in _K9} >= {"params.py"}


# ══════════════════════════════════════════════════════════════════════════
# G2 / G3 策略层的两条取数边界
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("group,label", [
    (FORBIDDEN_NETWORK, "策略层内没有 LLM 调用,也没有联网能力"),
    (FORBIDDEN_DATA, "取数唯一来源是事实包"),
])
def test_k9_never_imports_the_forbidden_groups(group, label):
    """🔴 扫描器走 `tests/guard_scan.py`(S15 收敛)。

    本文件原来抄了一份跳过相对 import 的 `_imported_modules` —— 复审实测
    `from ..llm import factory`(CE1)与 `from ..data import market_data`(CE3)
    双双穿过,而这两条正是 G2 / G3「取数唯一来源是事实包 / 策略层零 LLM」的**全部**
    牙齿。现在相对 import 与字面量动态 import 一并被看见。
    """
    hits = guard_scan.import_hits(_K9, group, root=_ROOT)
    assert hits == [], f"{label} —— 这条边界被破了:\n" + "\n".join(hits)


def test_params_reads_the_fact_layer_constant_not_a_copy_of_it():
    """§5.4.3 校验 3 要求 `factPackVersion` 等于**事实层常量**。
    在 `k9/params.py` 里抄一份 `"fp-1"` 会让这条校验在口径升版那天悄悄失效。"""
    src = (_PKG / "k9" / "params.py").read_text(encoding="utf-8")
    assert "from neckline.facts.pack import" in src
    assert '"fp-' not in src and "'fp-" not in src, "参数模块里抄了一份 pack_version 字面量"


# ══════════════════════════════════════════════════════════════════════════
# G8 无默认值 / 无默认路径
# ══════════════════════════════════════════════════════════════════════════

def test_no_param_dataclass_field_has_a_default():
    offenders = P.assert_no_field_defaults(P.K9Params)
    assert offenders == [], (
        "这些参数字段有了默认值 —— 「少一个值就构造不出对象」的结构性保证作废,"
        f"裁定 5「⛔ 不使用任何默认值」就此只剩一句注释:{offenders}")


def test_load_has_no_default_path():
    sig = inspect.signature(P.load)
    assert sig.parameters["path"].default is inspect.Parameter.empty


def test_no_module_level_default_params_path_constant():
    """⛔ **无默认路径**(§5.4.3):`k9/params.py` 里不许出现一个「找不到就用它」的路径。"""
    tree = ast.parse((_PKG / "k9" / "params.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                name = getattr(t, "id", "")
                assert not (("PATH" in name.upper() or "CONFIG" in name.upper())
                            and "SCHEMA" not in name.upper()), name


# ══════════════════════════════════════════════════════════════════════════
# G22 三个参数位:全取值实现,⛔ 无默认分支
# ══════════════════════════════════════════════════════════════════════════

def _default_branch_offenders(path: Path) -> List[str]:
    """找出真正能造出「悄悄退回某个取值」的写法。

    四种形状:
      ① `<...>.get(x, HeatAbsentPolicy.ZERO)` —— dict 兜底
      ② `x or RelaySource.RECALLED`          —— 短路兜底
      ③ `def f(policy=HeatAbsentPolicy.ZERO)` —— 默认实参
      ④ `Enum.__missing__` / `try: Enum(x) except: 某成员` —— 解析兜底
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: List[str] = []

    def is_enum_member(node: ast.AST) -> bool:
        return (isinstance(node, ast.Attribute)
                and node.attr in _ENUM_MEMBER_NAMES
                and isinstance(node.value, ast.Name)
                and node.value.id in _ENUM_CLASS_NAMES)

    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and len(node.args) == 2
                and is_enum_member(node.args[1])):
            out.append(f"{path.name}:{node.lineno} dict.get 兜底成某个枚举成员")
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            if any(is_enum_member(v) for v in node.values):
                out.append(f"{path.name}:{node.lineno} `or` 短路兜底成某个枚举成员")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in list(node.args.defaults) + list(node.args.kw_defaults):
                if d is not None and is_enum_member(d):
                    out.append(f"{path.name}:{node.lineno} 形参默认值是某个枚举成员")
        if isinstance(node, ast.FunctionDef) and node.name == "_missing_":
            out.append(f"{path.name}:{node.lineno} 枚举定义了 `_missing_` 解析兜底")
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                for sub in ast.walk(handler):
                    if isinstance(sub, ast.Return) and is_enum_member(sub.value):
                        out.append(f"{path.name}:{sub.lineno} except 里返回某个枚举成员")
    return out


def test_the_offender_detector_actually_detects():
    """扫描器自检:一个永远绿的闸门等于没有闸门。"""
    import tempfile

    sample = (
        "from neckline.k9.params import HeatAbsentPolicy\n"
        "def f(policy=HeatAbsentPolicy.ZERO):\n"
        "    return policy\n"
    )
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sample.py"
        p.write_text(sample, encoding="utf-8")
        assert _default_branch_offenders(p), "扫描器看不见「形参默认值是枚举成员」"


def test_no_default_branch_for_the_three_calibrated_value_slots():
    """🔴 §7.6 / G22:「降为参数位」⛔ 不等于「可以先挑一个用」。
    代码里**不许有「哪个是默认」的分支** —— 参数包缺任一个键 = 「参数未配置」
    = 报告「今天没跑成」(裁定 5)。"""
    offenders: List[str] = []
    for path in sorted(_PKG.rglob("*.py")):
        offenders.extend(_default_branch_offenders(path))
    assert offenders == [], "三个取值待标定的参数位被人偷偷给了默认:\n" + "\n".join(offenders)


def test_all_candidate_values_are_implemented_as_closed_enums():
    assert len(P.HeatAbsentPolicy) == 3
    assert len(P.RelaySource) == 2
    assert len(P.RelayScoring) == 2
    assert len(P.ENUM_PARAM_SLOTS) == 3


def test_the_schema_declares_the_three_slots_as_enums_not_free_strings():
    """自由字符串会让「写错一个取值」变成一次静默的行为变化。"""
    assert P.REQUIRED_SCHEMA["industry"]["heatAbsentPolicy"] is P.HeatAbsentPolicy
    assert P.REQUIRED_SCHEMA["ranking"]["relaySource"] is P.RelaySource
    assert P.REQUIRED_SCHEMA["ranking"]["relayScoring"] is P.RelayScoring


def test_the_example_config_leaves_all_three_uncalibrated():
    import json
    doc = json.loads((_ROOT / "config" / "k9-params.example.json").read_text(encoding="utf-8"))
    assert doc["industry"]["heatAbsentPolicy"] == P.TO_BE_CALIBRATED
    assert doc["ranking"]["relaySource"] == P.TO_BE_CALIBRATED
    assert doc["ranking"]["relayScoring"] == P.TO_BE_CALIBRATED


# ══════════════════════════════════════════════════════════════════════════
# G9 三态:全映射,⛔ 无 fallback 分支
# ══════════════════════════════════════════════════════════════════════════

def test_report_state_has_exactly_three_members():
    assert len(ReportState) == 3


def test_headline_rendering_is_a_total_mapping_without_fallback():
    """🔴 §5.10:首行由**全映射**渲染。出现 `.get(state, …)` = 有了 fallback 分支,
    「三态每天必发其一、首行即可分辨」这条承诺就变成了「大部分时候」。"""
    src = (_PKG / "report" / "state.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and len(node.args) == 2):
            pytest.fail(f"report/state.py:{node.lineno} 出现了带兜底的 `.get(...)`")
    from neckline.report import state as st
    assert set(st._HEADLINE) == set(ReportState)


def test_the_three_states_are_never_collapsed_into_a_boolean():
    """⛔ 不许把三态压成 `ok / not ok` —— 「今天没有」与「今天没跑成」的区别
    正是被压掉的那一维(裁定 5)。"""
    src = (_PKG / "report" / "state.py").read_text(encoding="utf-8")
    assert "EMPTY" in src and "NOT_RUN" in src and "HAS_LIST" in src
    from neckline.report.state import resolve_state
    assert resolve_state(pack_frozen=True, params_ok=True, listing_count=0) \
        is not resolve_state(pack_frozen=True, params_ok=False, listing_count=0)
