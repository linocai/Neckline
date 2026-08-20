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
from typing import Dict, List, Set, Tuple

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


# ══════════════════════════════════════════════════════════════════════════
# G22 续:**数字兜底**也是兜底(裁定 5「⛔ 不使用任何默认值」)
# ══════════════════════════════════════════════════════════════════════════
#
# 🔴 复审 M2 实测:上面那四种形状全部以 `is_enum_member` 收口 —— 只认「兜底成某个
# 枚举成员」。把 `raw["industry"]["minMembers"]` 改成 `raw["industry"].get(
# "minMembers", 10)`,`test_v250_s5_params_guard.py` + `test_k9_params.py`
# **90 passed 全绿**。而裁定 5 的红线是「⛔ 不使用**任何**默认值」,不只是那三个枚举位。
# ══════════════════════════════════════════════════════════════════════════

#: 计数 / 打分里「没有就是 0」的正当写法,连同理由。
#: 🔴 **按表达式而不是行号做键** —— 行号会随上面任何一次编辑漂,而漂了之后白名单
#: 要么误放行、要么误报,两种都会逼着后来者把守门放宽。
_NUMERIC_FALLBACK_ALLOW: Dict[str, str] = {
    "boundary.py:got.get(r, 0)":
        "逐条排除原因的**计数**:某条今天一个都没排除 = 0,不是「默认排除 0 只」",
    "ranking.py:strength.get((code, p), 0.0)":
        "这只票没命中这个形态 → 形态强度 0(K9 §五-4 取 max 的输入),不是标定值",
    "ranking.py:strength.get((code, best), 0.0)":
        "同上,取 max 之后再读一次同一张表",
    "ranking.py:relay_scores.get(code, 0.0)":
        "这只票过去 N 天没被选过 → 接力分 0,是**算出来的缺席**,不是默认",
}


def _is_number(node: ast.AST) -> bool:
    """数字字面量(含 `-1` 这种一元负号)。⚠ `True` / `False` 在 Python 里是 int 的
    子类,⛔ 别把布尔当数字算进去 —— 那会把一大批正当的开关误报成兜底。"""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _is_number(node.operand)
    return False


def _numeric_default_offenders(path: Path) -> List[str]:
    """「悄悄退回某个**数字**」的四种写法(形状与枚举版逐条对应)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: List[str] = []

    def note(node: ast.AST, what: str) -> None:
        key = f"{path.name}:{ast.unparse(node)}"
        if key in _NUMERIC_FALLBACK_ALLOW:
            return
        out.append(f"{path.name}:{node.lineno} {what} —— {ast.unparse(node)}")

    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and len(node.args) == 2
                and _is_number(node.args[1])):
            note(node, "dict.get 兜底成一个数字")
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            if any(_is_number(v) for v in node.values):
                note(node, "`or` 短路兜底成一个数字")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in list(node.args.defaults) + [x for x in node.args.kw_defaults if x]:
                if _is_number(d):
                    out.append(f"{path.name}:{node.lineno} "
                               f"形参默认值是数字 —— def {node.name}(… = {ast.unparse(d)})")
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                for sub in ast.walk(handler):
                    if isinstance(sub, ast.Return) and sub.value is not None \
                            and _is_number(sub.value):
                        note(sub, "except 里返回一个数字")
    return out


def test_the_numeric_default_detector_actually_detects(tmp_path):
    """扫描器自检 —— 四种形状都要看得见,布尔⛔ 不许被当成数字。"""
    sample = tmp_path / "s.py"
    sample.write_text(
        "def f(raw, ma_days=20):\n"
        "    a = raw['industry'].get('minMembers', 10)\n"
        "    b = raw.get('x') or 0.3\n"
        "    try:\n"
        "        return int(raw['y'])\n"
        "    except KeyError:\n"
        "        return 5\n",
        encoding="utf-8")
    got = _numeric_default_offenders(sample)
    assert len(got) == 4, got
    ok = tmp_path / "ok.py"
    ok.write_text("def g(flag=True):\n    return flag or False\n", encoding="utf-8")
    assert _numeric_default_offenders(ok) == [], "布尔被当成数字了"


def test_no_numeric_default_anywhere_in_the_strategy_layer():
    """🔴 §7.6 / 裁定 5:「降为参数位」⛔ 不等于「可以先挑一个用」,而
    「⛔ 不使用任何默认值」里的**任何**包括数字。

    ⚠ 扫描域是 `neckline/k9/**`(策略层)——⛔ 不是全仓:`settings_store` / `api`
    那些层里「没配就用 N」是正当的产品行为,把它们一起判红,这条守门第二天就会被放宽。
    """
    offenders: List[str] = []
    for path in _K9:
        offenders.extend(_numeric_default_offenders(path))
    assert offenders == [], (
        "策略层里出现了数字兜底 —— 待标定的数只能来自参数包:\n" + "\n".join(offenders))


def test_the_numeric_fallback_allowlist_stays_justified():
    """白名单每条都要有理由,且那个表达式**真的还在**(⛔ 不许留指向空气的例外)。"""
    seen = {
        f"{path.name}:{ast.unparse(node)}"
        for path in _K9
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, (ast.Call, ast.BoolOp, ast.Return))
    }
    for key, reason in _NUMERIC_FALLBACK_ALLOW.items():
        assert key in seen, f"白名单条目已不存在,可以删了:{key}"
        assert len(reason) > 10, f"{key} 的理由太短,说不清为什么"


# ══════════════════════════════════════════════════════════════════════════
# G8 续:「无默认值」那条递归**是活的**吗
# ══════════════════════════════════════════════════════════════════════════

def test_the_no_default_walk_is_actually_alive(tmp_path):
    """🔴 复审 M1:`assert_no_field_defaults` 的递归写的是 `is_dataclass(f.type)`,
    而模块顶上有 `from __future__ import annotations` —— `f.type` 是**字符串**,
    `is_dataclass("BoundaryParams")` 恒为 `False`,那条递归**一次都没走过**。
    真正起作用的是一张写死 14 个类的清单;新增一个带默认值的嵌套 dataclass 而没加进
    清单,守门 15 passed 全绿。

    诱饵必须**也带 `from __future__ import annotations`** —— 少了这一句,注解不是
    字符串,死递归照样能走,自检就测不到那个失效点。
    """
    import sys  # noqa: PLC0415
    import types  # noqa: PLC0415

    name = "nk_bait_params"
    source = (
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True)\n"
        "class NewsParams:\n"
        "    lookback_days: int = 3\n"
        "@dataclass(frozen=True)\n"
        "class Outer:\n"
        "    news: NewsParams\n"
    )
    module = types.ModuleType(name)
    sys.modules[name] = module
    try:
        exec(compile(source, name, "exec"), module.__dict__)  # noqa: S102
        assert P.assert_no_field_defaults(module.Outer) == ["Outer.news.lookback_days"], (
            "递归没走进嵌套 dataclass —— G8 又变回「清单维护得好就守得住」了")
        assert len(P.param_dataclass_closure(module.Outer)) == 2
    finally:
        sys.modules.pop(name, None)


def test_the_no_default_walk_reaches_every_param_dataclass():
    """🔴 **闭包必须盖住本模块里每一个参数 dataclass**。

    这条是 `PARAM_DATACLASS_ROOTS` 那张小清单的保险:`ChannelTiers.strict/relaxed`
    声明成 `Any`,四个 `PNTier` 只能从根清单进来 —— 而清单会过期。有人新加一个
    注解够不到的参数类而忘了挂进去,这里当场红。
    """
    import dataclasses  # noqa: PLC0415

    declared = {
        obj for obj in vars(P).values()
        if isinstance(obj, type) and dataclasses.is_dataclass(obj)
        and obj.__module__ == P.__name__
    }
    reached = set(P.param_dataclass_closure(*P.PARAM_DATACLASS_ROOTS))
    assert declared == reached, (
        f"这些参数 dataclass 没被「无默认值」的遍历走到:{sorted(c.__name__ for c in declared - reached)}"
    )
    assert len(reached) >= 14, f"只走到 {len(reached)} 个类 —— 闭包怕是断了"
