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
from typing import Dict, List, Optional, Set, Tuple

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

#: 正当的「没有就是 X」写法,连同理由。
#: 🔴 **按表达式而不是行号做键** —— 行号会随上面任何一次编辑漂,而漂了之后白名单
#: 要么误放行、要么误报,两种都会逼着后来者把守门放宽。
_CONST_FALLBACK_ALLOW: Dict[str, str] = {
    "boundary.py:got.get(r, 0)":
        "逐条排除原因的**计数**:某条今天一个都没排除 = 0,不是「默认排除 0 只」",
    "ranking.py:strength.get((code, p), 0.0)":
        "这只票没命中这个形态 → 形态强度 0(K9 §五-4 取 max 的输入),不是标定值",
    "ranking.py:strength.get((code, best), 0.0)":
        "同上,取 max 之后再读一次同一张表",
    "ranking.py:relay_scores.get(code, 0.0)":
        "这只票过去 N 天没被选过 → 接力分 0,是**算出来的缺席**,不是默认",
    "params.py:prefix or '<根>'":
        "校验失败时**报错文案**里的路径前缀(根层没有前缀),不是任何参数的值",
    "store.py:strategy: str = 'K9'":
        "`k9_runs.strategy` 的署名字段(裁定 8:本版只有 K9 一条策略线,"
        "K10 进来时这个字段就是分辨依据),不是待标定参数",
}


def _constant_kind(node: ast.AST) -> Optional[str]:
    """这是一个**常量**吗;是的话是哪一类。

    ⚠ **`None` 与布尔刻意不算**:`None` 是「缺席」本身(它往下传播成
    `ParamsUnavailable`,不是一个能顶替标定值的数),布尔在本仓里遍地是开关。
    把它们算进来会产生一大批假阳性,而假阳性会逼着后来者把守门放宽。
    ⚠ 这条豁免本身就是这个黑名单的**已知盲区**之一 —— 见下面那条 docstring。
    """
    if isinstance(node, ast.Constant):
        value = node.value
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return "数字"
        if isinstance(value, str):
            return "字符串"
        return type(value).__name__
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _constant_kind(node.operand)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return "序列字面量" if node.elts else None
    if isinstance(node, ast.Dict):
        return "字典字面量" if node.keys else None
    if isinstance(node, ast.Name) and node.id.isupper():
        return "模块常量名"
    return None


def _constant_default_offenders(path: Path) -> List[str]:
    """「悄悄退回某个**常量**」的几种写法(⚠ **黑名单**,见下)。

    🔴 **这是一份黑名单,它一定会漏下一种。** 已知会漏的:
      · `None` 与布尔兜底(刻意豁免,理由见 `_constant_kind`);
      · 算出来的默认(`raw.get(k, n * 2)`、`raw.get(k, _pick())`);
      · `if k not in d: d[k] = X` 这种语句形态(⛔ 不在本层);
      · 任何明天才被发明出来的写法。
    **真牙齿在上面那层白名单**(`_raw_access_offenders`):它管的是**参数构造路径**,
    判据是「只许做这四件事」,漏不掉。本层的作用是把网撒到**整个策略层** ——
    构造路径之外的地方(打分、计数、落库)也不该凭空冒出一个标定值。
    ⛔ 两层都要,别把哪一层删掉去换「判据只有一条」的整洁。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: List[str] = []

    def note(node: ast.AST, what: str) -> None:
        if f"{path.name}:{ast.unparse(node)}" in _CONST_FALLBACK_ALLOW:
            return
        out.append(f"{path.name}:{node.lineno} {what} —— {ast.unparse(node)[:90]}")

    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("get", "setdefault", "pop")
                and len(node.args) == 2):
            kind = _constant_kind(node.args[1])
            if kind:
                note(node, f"`.{node.func.attr}` 兜底成一个{kind}")
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            kinds = [k for k in (_constant_kind(v) for v in node.values) if k]
            if kinds:
                note(node, f"`or` 短路兜底成一个{kinds[0]}")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            named = ([a.arg for a in args.posonlyargs + args.args][-len(args.defaults):]
                     if args.defaults else [])
            pairs = list(zip(named, args.defaults)) + [
                (a.arg, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d]
            for name, default in pairs:
                kind = _constant_kind(default)
                if not kind:
                    continue
                key = f"{path.name}:{name}: " \
                      f"{ast.unparse(_annotation_of(node, name))} = {ast.unparse(default)}"
                if key in _CONST_FALLBACK_ALLOW:
                    continue
                out.append(f"{path.name}:{node.lineno} 形参默认值是一个{kind} —— "
                           f"def {node.name}(… {name}={ast.unparse(default)})")
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                for sub in ast.walk(handler):
                    if isinstance(sub, ast.Return) and sub.value is not None \
                            and _constant_kind(sub.value):
                        note(sub, "except 里返回一个常量")
    return out


def _annotation_of(fn: ast.AST, name: str) -> ast.AST:
    args = fn.args  # type: ignore[attr-defined]
    for a in args.posonlyargs + args.args + args.kwonlyargs:
        if a.arg == name and a.annotation is not None:
            return a.annotation
    return ast.Name(id="")


def test_the_constant_default_detector_actually_detects(tmp_path):
    """扫描器自检 —— 各种常量类型、各种写法都要看得见;`None` / 布尔⛔ 不许误报。"""
    sample = tmp_path / "s.py"
    sample.write_text(
        "def f(raw, ma_days=20, mode='zero', tiers=('a',)):\n"
        "    a = raw['industry'].get('minMembers', 10)\n"
        "    b = raw.get('x') or 0.3\n"
        "    c = raw.setdefault('y', 'zero')\n"
        "    d = raw.pop('z', LIMIT)\n"
        "    try:\n"
        "        return int(raw['y'])\n"
        "    except KeyError:\n"
        "        return 5\n",
        encoding="utf-8")
    got = _constant_default_offenders(sample)
    assert len(got) == 8, got
    ok = tmp_path / "ok.py"
    ok.write_text(
        "def g(flag=True, sink=None):\n"
        "    return (flag or False), (sink or None)\n", encoding="utf-8")
    assert _constant_default_offenders(ok) == [], "`None` / 布尔被当成常量兜底了"


def test_no_constant_default_anywhere_in_the_strategy_layer():
    """🔴 §7.6 / 裁定 5:「降为参数位」⛔ 不等于「可以先挑一个用」,而
    「⛔ 不使用任何默认值」里的**任何**包括数字、字符串、序列、模块常量。

    ⚠ 扫描域是 `neckline/k9/**`(策略层)——⛔ 不是全仓:`settings_store` / `api`
    那些层里「没配就用 N」是正当的产品行为,把它们一起判红,这条守门第二天就会被放宽。
    """
    offenders: List[str] = []
    for path in _K9:
        offenders.extend(_constant_default_offenders(path))
    assert offenders == [], (
        "策略层里出现了常量兜底 —— 待标定的数只能来自参数包:\n" + "\n".join(offenders))


def test_the_constant_fallback_allowlist_stays_justified():
    """白名单每条都要有理由,且它指的那个表达式**真的还在**。

    ⛔ 一条指向空气的豁免比没有豁免更糟:它会让下一个人以为那里被想过。
    """
    seen: Set[str] = set()
    for path in _K9:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Call, ast.BoolOp, ast.Return)):
                seen.add(f"{path.name}:{ast.unparse(node)}")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                named = ([a.arg for a in args.posonlyargs + args.args][-len(args.defaults):]
                         if args.defaults else [])
                for name, default in list(zip(named, args.defaults)) + [
                        (a.arg, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d]:
                    seen.add(f"{path.name}:{name}: "
                             f"{ast.unparse(_annotation_of(node, name))} = "
                             f"{ast.unparse(default)}")
    for key, reason in _CONST_FALLBACK_ALLOW.items():
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


# ══════════════════════════════════════════════════════════════════════════
# G22 的**真牙齿**:参数读取路径上,⛔ 不许有任何形式的兜底(白名单判据)
# ══════════════════════════════════════════════════════════════════════════
#
# 🔴 **为什么把黑名单换成白名单**(2026-08-21 第二波复审 F-A 退回):
# 上一版补了「数字兜底」之后,`.get(k, 10)` 确实红了 —— 但实测另外五种形状照绿:
#
#   | 注入到 `params.py::_build` | 上一版 |
#   |---|---|
#   | `raw["industry"].get("minMembers", "zero")`(**字符串**常量) | 🔴 绿 |
#   | `raw["industry"].get("minMembers", _NK_DEFAULT_MIN)`(**模块常量名**) | 🔴 绿 |
#   | `raw["industry"].setdefault("minMembers", 10)` | 🔴 绿 |
#   | `... if "minMembers" in raw["industry"] else 10`(**三元**) | 🔴 绿 |
#   | `raw["industry"]["minMembers"] = 10`(**缺键就塞**) | 🔴 绿 |
#
# 逐个列举可疑写法的判据**永远漏下一种** —— Python 给一个 dict 加兜底的方法有十几种,
# 而裁定 5 的原文是「⛔ 不使用**任何**默认值」。所以这一层反过来写:
#
#   **参数包是一份只读输入。对它只许做四件事** ——
#     ① `raw["k"]` 下标读(缺键 → `KeyError` → `ParamsUnavailable`,这正是要的行为);
#     ② `k in raw` / `k not in raw` 成员检查(校验层要**报告**缺了什么);
#     ③ `.items()` / `.keys()` / `.values()` 遍历;
#     ④ 原样传给另一个函数。
#   **其余一切操作一律算违规,包括还没被想到的那些。** 想给参数一个默认值,
#   无论用哪种写法,都必然落在这四件之外。
#
# ⚠ 这一层与下面那个「常量兜底黑名单」是**两层**:白名单管参数读取路径(强,不漏),
# 黑名单管整个策略层(弱,会漏,见它自己的 docstring)。⛔ 别把哪一层删掉。
# ══════════════════════════════════════════════════════════════════════════

#: 对参数包**唯一允许**的方法调用(纯遍历,不产生值)。
#: ⛔ 这是白名单:没列在这里的方法名一律算违规 —— 包括 `.get` / `.setdefault` /
#: `.pop`,也包括明天才被发明出来的那个。
_ALLOWED_RAW_ACCESSORS = frozenset({"items", "keys", "values"})


def _params_schema_files() -> List[Path]:
    """定义了 `REQUIRED_SCHEMA` 的文件 = 把原始 JSON 变成 `K9Params` 的那一层。

    ⚠ 走**内容**而不是写死文件名:哪天参数解析被拆成两个文件,新的那个自动进来。
    """
    out = []
    for path in _K9:
        names: Set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            # ⚠ 两种都要收:`X = {...}` 与 **`X: Dict[...] = {...}`**(带注解的赋值是
            # `AnnAssign`,不是 `Assign`)。只收前者的话本层扫描面直接归零 ——
            # 而归零之后所有断言都照绿,这正是 `test_..._is_seeded_and_not_vacuous`
            # 存在的理由(它当场抓到了这个 bug)。
            if isinstance(node, ast.Assign):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
        if "REQUIRED_SCHEMA" in names:
            out.append(path)
    return out


def _direct_calls(node: ast.AST) -> Set[str]:
    return {c.func.id for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}


def _construction_path(tree: ast.Module) -> Set[str]:
    """**构造路径** = 「真的造出参数对象的函数」及其(本文件内)全部被调函数。

    🔴 **为什么是这个域,不是整个模块**:模块里还有**校验层**(`_walk` / `_dig` /
    `_check_*`),它们的产物是 `missing` / `invalid` 两张清单,**造不出参数值** ——
    而它们合法地去摸叶子值(`value.strip()`、`value not in allowed`),把它们圈进来
    会产生一批假阳性,而假阳性会逼着后来者把守门放宽。

    ⚠ 闭包是**传递**的:有人把兜底挪进 `_build` 调的某个 helper(`_pick(raw, k, 10)`),
    那个 helper 自动进域 —— ⛔ 这条不许退化成「只看 `_build` 一个函数」。
    """
    dataclasses_here = {
        n.name for n in tree.body
        if isinstance(n, ast.ClassDef) and any(
            getattr(d, "id", getattr(getattr(d, "func", None), "id", None)) == "dataclass"
            for d in n.decorator_list)
    }
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    closure = {name for name, fn in funcs.items()
               if _direct_calls(fn) & dataclasses_here}
    frontier = set(closure)
    while frontier:
        nxt: Set[str] = set()
        for name in frontier:
            for callee in _direct_calls(funcs[name]):
                if callee in funcs and callee not in closure:
                    nxt.add(callee)
        closure |= nxt
        frontier = nxt
    return closure


def _raw_seeds(fn: ast.AST) -> Set[str]:
    """这个函数的哪些形参**装着参数包**(或它的某一层子树)。

    判据:注解是 `Mapping[...]` / `Dict[...]` / `dict`,或形参就叫 `raw`。
    ⚠ 宁可**多认**几个 —— 在构造路径上,对**任何**输入映射做兜底都是错的,
    多认一个不会误伤,少认一个就是一个洞。
    """
    args = fn.args  # type: ignore[attr-defined]
    seeds: Set[str] = set()
    for a in args.posonlyargs + args.args + args.kwonlyargs:
        ann = ast.unparse(a.annotation) if a.annotation else ""
        if a.arg == "raw" or ann.startswith(("Mapping", "Dict", "dict")):
            seeds.add(a.arg)
    return seeds


def _local_taint(fn: ast.AST, seeds: Set[str]) -> Set[str]:
    """函数体内的污点传播,跑到不动点。

    ⛔ 不许只传一层:`cur = cur[part]` 这种逐层下钻的写法一层就断了。
    """
    tainted = set(seeds)
    for _ in range(8):
        before = set(tainted)
        for node in ast.walk(fn):
            targets: List[ast.AST] = []
            if isinstance(node, ast.Assign) and _is_tainted(node.value, tainted):
                targets = list(node.targets)
            elif isinstance(node, (ast.For, ast.AsyncFor)) \
                    and _is_tainted(node.iter, tainted):
                targets = [node.target]
            for tgt in targets:
                for leaf in ast.walk(tgt):
                    if isinstance(leaf, ast.Name):
                        tainted.add(leaf.id)
        if tainted == before:
            return tainted
    return tainted


def _is_tainted(node: ast.AST, tainted: Set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in tainted
    if isinstance(node, ast.Subscript):
        return _is_tainted(node.value, tainted)
    if isinstance(node, ast.Call):
        if (isinstance(node.func, ast.Attribute)
                and node.func.attr in _ALLOWED_RAW_ACCESSORS):
            return _is_tainted(node.func.value, tainted)
        # `dict(raw["industry"])` —— 参数包的副本仍然是参数包,⛔ 不许靠拷一份逃出去。
        if isinstance(node.func, ast.Name) and node.func.id in ("dict", "deepcopy", "copy"):
            return any(_is_tainted(a, tainted) for a in node.args)
    return False


def _taint_fixpoint(tree: ast.Module, closure: Set[str]) -> Dict[str, Set[str]]:
    """**跨函数**的污点传播:谁把参数包传给了谁。

    🔴 少了这一步就有一个大洞(实测):把兜底挪进一个 helper ——
    `_build` 里 `min_members=_nk_fill(raw["industry"])`,而
    `def _nk_fill(sub): sub["minMembers"] = 10` —— `sub` 既不叫 `raw` 也没注解,
    按形参判据认不出来,整条白名单对它失明。现在按**调用点**传:
    传进去的实参是污点,被调函数对应的形参就是污点。
    """
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    taint = {name: _raw_seeds(funcs[name]) for name in closure if name in funcs}
    for _ in range(8):
        before = {k: set(v) for k, v in taint.items()}
        for name in list(taint):
            local = _local_taint(funcs[name], taint[name])
            for call in ast.walk(funcs[name]):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
                    continue
                callee = call.func.id
                if callee not in taint:
                    continue
                params = [a.arg for a in (funcs[callee].args.posonlyargs
                                          + funcs[callee].args.args)]
                for i, arg in enumerate(call.args):
                    if _is_tainted(arg, local) and i < len(params):
                        taint[callee].add(params[i])
                for kw in call.keywords:
                    if kw.arg and _is_tainted(kw.value, local):
                        taint[callee].add(kw.arg)
        if taint == before:
            break
    return taint


def _raw_access_offenders(path: Path) -> List[str]:
    """构造路径上,**四件允许的事之外**的一切操作。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    closure = _construction_path(tree)
    taint = _taint_fixpoint(tree, closure)
    offenders: List[str] = []

    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name in closure]:
        tainted = _local_taint(fn, taint.get(fn.name, set()))
        if not tainted:
            continue
        parents = {c: n for n in ast.walk(fn) for c in ast.iter_child_nodes(n)}

        def note(node: ast.AST, what: str, _fn=fn) -> None:
            offenders.append(
                f"{path.name}:{getattr(node, 'lineno', 0)} {_fn.name}(): "
                f"{what} —— {ast.unparse(node)[:90]}")

        # 允许面:被当作 `.items()/.keys()/.values()` 调用的那些 Attribute 节点。
        allowed_attr = {
            id(n.func) for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in _ALLOWED_RAW_ACCESSORS
            and _is_tainted(n.func.value, tainted)
        }

        for node in ast.walk(fn):
            # ① 除四件事之外的任何属性访问 —— 白名单的落点
            if isinstance(node, ast.Attribute) and _is_tainted(node.value, tainted) \
                    and id(node) not in allowed_attr:
                note(node, f"对参数包做了 `.{node.attr}` —— 只许 "
                           f"下标 / in / {sorted(_ALLOWED_RAW_ACCESSORS)} / 原样传参")
            # ② 往参数包里写 —— 它是**只读输入**
            if isinstance(node, ast.Subscript) and _is_tainted(node.value, tainted) \
                    and isinstance(node.ctx, (ast.Store, ast.Del)):
                note(node, "往参数包里写值(「缺键就塞一个」的形状)")
            # ③ / ④ 读出来的值挂上 `or` 兜底 或 落进三元的一支
            if isinstance(node, (ast.Name, ast.Subscript)) \
                    and _is_tainted(node, tainted) \
                    and isinstance(node.ctx, ast.Load):
                parent = parents.get(node)
                if isinstance(parent, ast.BoolOp) and isinstance(parent.op, ast.Or):
                    note(parent, "参数包的读数挂上了 `or` 兜底")
                if isinstance(parent, ast.IfExp) and node in (parent.body, parent.orelse):
                    note(parent, "参数包的读数落进三元的一支(另一支就是默认值)")
            # ⑤ `**raw` 合并 —— `{"k": 10, **raw}` 正是「缺键就用 10」
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if key is None and _is_tainted(value, tainted):
                        note(node, "`**参数包` 字典合并(缺键就落到旁边那个常量上)")
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg is None and _is_tainted(kw.value, tainted):
                        note(node, "`**参数包` 展开进调用(同上)")
            # ⑥ try 包住读数、except 里给出一个值
            if isinstance(node, ast.Try):
                reads = any(
                    _is_tainted(s, tainted)
                    and isinstance(getattr(s, "ctx", None), ast.Load)
                    for b in node.body for s in ast.walk(b)
                    if isinstance(s, (ast.Name, ast.Subscript)))
                yields = any(
                    (isinstance(s, ast.Return) and s.value is not None)
                    or isinstance(s, (ast.Assign, ast.AugAssign))
                    for h in node.handlers for s in ast.walk(h))
                if reads and yields:
                    note(node, "`try:` 读参数包 / `except:` 里给出一个值")
    return offenders


_BAIT_HEAD = (
    "from dataclasses import dataclass\n"
    "from typing import Any, Mapping\n"
    "REQUIRED_SCHEMA = {}\n"
    "_DEFAULT_MIN = 10\n"
    "@dataclass(frozen=True)\n"
    "class IndustryParams:\n"
    "    min_members: int\n"
)


def _write_bait(root: Path, body: str) -> Path:
    """诱饵必须**真的造一个参数 dataclass** —— 否则它根本不在构造路径上,
    自检就测到了另一条路径(那种自检比没有更糟)。"""
    bait = root / "params.py"
    bait.write_text(
        _BAIT_HEAD
        + "def _fill(sub):\n"
          "    sub['minMembers'] = 10\n"
          "    return sub['minMembers']\n"
          "def _pick(sub):\n"
          "    return sub.get('minMembers', 10)\n"
          "def _build(raw: Mapping[str, Any]):\n"
        + "".join(f"    {line}\n" for line in body.splitlines()),
        encoding="utf-8")
    return bait


def test_the_raw_access_scanner_is_seeded_and_not_vacuous():
    """扫描器自检:**先证明它扫得到东西**。

    ⛔ 上一版守门的死法就是这个 —— 扫描面悄悄归零之后,一切照绿。
    (本条在写这一层的当天就抓到一次:`REQUIRED_SCHEMA` 是 `AnnAssign`,
    只收 `Assign` 的第一版让 `_params_schema_files()` 返回空表。)
    """
    files = _params_schema_files()
    assert [p.name for p in files] == ["params.py"], files
    tree = ast.parse(files[0].read_text(encoding="utf-8"))
    closure = _construction_path(tree)
    assert {"_build", "_tier"} <= closure, f"构造路径认出来的是 {sorted(closure)}"
    seeded = {
        fn.name for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and fn.name in closure and _raw_seeds(fn)
    }
    assert {"_build", "_tier"} <= seeded, (
        f"构造路径上真正被打上污点的只有 {sorted(seeded)} —— 白名单对其余的失明")


@pytest.mark.parametrize("label,body", [
    ("数字兜底",          'return IndustryParams(raw["industry"].get("minMembers", 10))'),
    ("字符串常量兜底",     'return IndustryParams(raw["industry"].get("minMembers", "zero"))'),
    ("模块常量兜底",       'return IndustryParams(raw["industry"].get("minMembers", _DEFAULT_MIN))'),
    ("setdefault",        'return IndustryParams(raw["industry"].setdefault("minMembers", 10))'),
    ("pop 带默认",         'return IndustryParams(raw["industry"].pop("minMembers", 10))'),
    ("单参 get(悄悄 None)", 'return IndustryParams(raw["industry"].get("minMembers"))'),
    ("缺键就塞",           'raw["industry"]["minMembers"] = 10\nreturn IndustryParams(raw["industry"]["minMembers"])'),
    ("or 兜底",            'return IndustryParams(raw["industry"]["minMembers"] or 10)'),
    ("三元",              'return IndustryParams(raw["industry"]["minMembers"] if "minMembers" in raw["industry"] else 10)'),
    ("** 字典合并",         'return IndustryParams({"minMembers": 10, **raw["industry"]}["minMembers"])'),
    ("** 展开进调用",       'return IndustryParams(**{"min_members": 10}, **raw["industry"])'),
    ("try/except",        'try:\n    return IndustryParams(raw["industry"]["minMembers"])\n'
                          'except KeyError:\n    return IndustryParams(10)'),
    ("下钻一层再兜底",      'cur = raw["industry"]\nreturn IndustryParams(cur.get("minMembers", 10))'),
    ("兜底藏进 helper(缺键就塞)",
     'return IndustryParams(_fill(raw["industry"]))'),
    ("兜底藏进 helper(.get)",
     'return IndustryParams(_pick(raw["industry"]))'),
    ("拷一份再兜底",       'return IndustryParams(dict(raw["industry"]).get("minMembers", 10))'),
    ("⚠ 还没被想到的写法",   'return IndustryParams(raw["industry"].nk_default("minMembers", 10))'),
])
def test_the_raw_access_whitelist_catches_every_fallback_shape(tmp_path, label, body):
    """🔴 **白名单的证明**:十七种给参数加默认值的写法,一种都不许漏。

    最后一条尤其要紧 —— `.nk_default(...)` 是一个**不存在**的方法。逐个列举可疑写法的
    黑名单判据对它必然失明,白名单判据必然抓到。这就是这一层换写法的全部理由。
    """
    assert _raw_access_offenders(_write_bait(tmp_path, body)), (
        f"白名单看不见「{label}」—— 这条判据又变回黑名单了")


def test_the_raw_access_whitelist_lets_the_four_allowed_shapes_through(tmp_path):
    """反向自检:四件允许的事⛔ 不许被判红(误报会逼着后来者把守门放宽)。"""
    body = (
        "if 'industry' not in raw:\n"
        "    raise KeyError('industry')\n"
        "cur = raw['industry']\n"
        "names = list(raw.keys())\n"
        "return IndustryParams(cur['minMembers']), _tier(cur), names\n"
    )
    bait = _write_bait(tmp_path, body)
    bait.write_text(bait.read_text(encoding="utf-8")
                    + "def _tier(raw: Mapping[str, Any]):\n"
                      "    return IndustryParams(raw['strict'])\n",
                    encoding="utf-8")
    assert _raw_access_offenders(bait) == []


def test_the_parameter_reading_path_has_no_fallback_of_any_kind():
    """🔴 **裁定 5 的结构性落点**:参数包缺任一个键 = 「参数未配置」= 报告
    「今天没跑成」。⛔ 构造路径上不许有任何一处「取不到就用 X」。

    ⚠ 这条是**白名单**:它不问「你用了哪种可疑写法」,只问「你对参数包做的这件事
    在不在那四件允许的事里」。⛔ 想加豁免之前先问自己:这真的是一次**读取**,
    还是在给一个待标定的数悄悄兜底?
    """
    offenders: List[str] = []
    for path in _params_schema_files():
        offenders.extend(_raw_access_offenders(path))
    assert offenders == [], (
        "参数读取路径上出现了兜底 —— 裁定 5:⛔ 不使用任何默认值:\n"
        + "\n".join(offenders))
