"""V2.5.0 S3 的**结构性**守门(PROJECT_PLAN §5.2 边界① / §5.3.2 / §10 G1、G16)。

体例照 `test_v250_s1_retirement_guard.py`:AST 扫描而不是整文件 grep —— grep 会把
docstring 里的历史说明也判红,一个总是误报的守门等于没有守门。

本文件锁六件事:

| # | 断言 | 出处 |
|---|---|---|
| G1-a | `facts/**` 零 import `k9` / `explain` / `playbook` / `scorecard` | 架构 §二 边界① |
| G1-b | `FactPack` 列名不含 `pattern`/`channel`/`recall`/`k9`/`rank`/`score` 词根 | §5.2 边界① |
| G16-a | `write_table_day("fact_pack", ...)` 与 `INSERT INTO fact_packs` **只在** `facts/store.py` | §5.3.2 第 5 条 |
| G16-b | 应用层写清单用 `INSERT`,⛔ 全仓无 `INSERT OR REPLACE INTO fact_packs` | §5.3.2 第 3 条 |
| 类型级 | `freeze_pack` 的形参注解逐字是 `CompletePack`(⛔ 不是 Union / Pack / Any) | §5.3.2 第 1 条 |
| 遗留 1/2 | `report/{industry_strength,board_pool,sectors}.py` 物理消失、零 import;全仓无 `_MIN_MEMBERS` | 本片交接单 |

⚠ **「类型级」那一条本来该由 mypy 证**(§6 S3 验收写的是「mypy/单测双证」),
但本仓 `.venv` 里**没有装 mypy**,也没有任何 mypy 配置 —— 现引入一个静态检查工具
不在本片范围内。改用**注解断言**替代:直接读 `freeze_pack` 的 AST,断言它的形参
注解就是 `CompletePack` 这个名字。少了这一条断言,把签名放宽成 `Pack` 就没人拦得住。
已登记进 PROJECT_PLAN §14。
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import List, Set, Tuple

import pytest

from neckline.facts import pack as fact_pack
from neckline.facts import store as fact_store

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "neckline"
_SCRIPTS = _ROOT / "scripts"
_SCANNED = sorted(_PKG.rglob("*.py")) + sorted(_SCRIPTS.rglob("*.py"))
_FACTS = sorted((_PKG / "facts").rglob("*.py"))

#: 事实层不许知道下游有哪些策略(架构 §二 边界①)。
DOWNSTREAM_PACKAGES: Tuple[str, ...] = (
    "neckline.k9", "neckline.explain", "neckline.playbook", "neckline.scorecard",
)


def _imported_modules(path: Path) -> Set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


def test_scan_covers_the_facts_package():
    """扫描范围本身要被看住:glob 失效会让本守门整体形同虚设。"""
    names = {p.name for p in _FACTS}
    assert {"pack.py", "store.py", "industry.py", "limitmap.py", "completeness.py"} <= names


# ══════════════════════════════════════════════════════════════════════════
# G1 边界①:事实层不知道下游有哪些策略
# ══════════════════════════════════════════════════════════════════════════

def test_facts_never_import_any_downstream_package():
    hits: List[str] = []
    for path in _FACTS:
        for mod in sorted(_imported_modules(path)):
            for p in DOWNSTREAM_PACKAGES:
                if mod == p or mod.startswith(p + "."):
                    hits.append(f"{path.relative_to(_ROOT)} → {mod}")
    assert hits == [], "事实层开始知道下游有哪些策略了:\n" + "\n".join(hits)


def test_fact_pack_column_names_carry_no_strategy_word_roots():
    bad = [
        c for c in fact_pack.PACK_COLUMNS
        if any(root in c.lower() for root in fact_pack.FORBIDDEN_COLUMN_ROOTS)
    ]
    assert bad == [], f"事实包列名带上了策略词根:{bad}"


def test_fact_pack_column_set_is_frozen_at_forty_one():
    """§5.3.1 逐组数出来是 40 列 + `trade_date`(分区键)= 41。

    加列必须先改这条断言 —— 让它成为一次自觉行为,而不是某天悄悄多出一列
    「顺手也算一下」的窗口量(事实层**只装一天的事实**)。"""
    assert len(fact_pack.PACK_COLUMNS) == 41
    assert len(set(fact_pack.PACK_COLUMNS)) == 41
    assert fact_pack.PACK_COLUMNS[0] == "trade_date"


def test_declared_float_columns_cover_every_numeric_pack_column():
    """§12 坑 2:新 parquet 表必须显式声明数值列。声明漏一列 = 那一列将来会
    「向既有分区看齐」,而基准分区本身可能是脏的(2026-07-27 生产真踩)。"""
    from neckline.data.market_data import TABLE_FLOAT_COLS

    declared = set(TABLE_FLOAT_COLS["fact_pack"])
    assert declared <= set(fact_pack.PACK_COLUMNS)
    # 未声明的必须**恰好**是本项目自算的非浮点列(⛔ 不许悄悄多出一个漏声明的浮点列)
    undeclared = set(fact_pack.PACK_COLUMNS) - declared
    assert undeclared == {
        "trade_date", "ts_code", "name", "board", "list_date", "is_st", "suspend_flag",
        "sw_l1_code", "sw_l1_name", "sw_l2_code", "sw_l2_name", "sw_l3_code",
        "is_limit_up", "is_limit_down", "is_limit_open", "consec_limit_up_days",
    }


# ══════════════════════════════════════════════════════════════════════════
# G16 唯一写入口
# ══════════════════════════════════════════════════════════════════════════

def _write_table_day_targets(path: Path) -> List[str]:
    """该文件里每个 `write_table_day(<表名>, ...)` 调用点的表名。

    ⚠ 第一个实参既可能是字面量 `"fact_pack"`,也可能是同文件里的模块级常量
    (`store.py` 用的就是 `PARQUET_TABLE`)。只认字面量会让这条守门**永远是绿的**
    ——「一个永远绿的闸门等于没有闸门」,所以这里顺带把同文件的模块级字符串常量
    解析一次。跨文件引用的常量解析不了 → 返回 `<非字面量>` 让它显式落在结果里。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    consts: dict = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            consts[node.targets[0].id] = node.value.value

    out: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name != "write_table_day" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.append(first.value)
        elif isinstance(first, ast.Name) and first.id in consts:
            out.append(consts[first.id])
        else:
            out.append("<非字面量>")
    return out


def test_the_write_entry_scanner_can_actually_see_the_call():
    """扫描器本身要被看住:解析不出 `PARQUET_TABLE` 就会让上面那条守门永远绿。"""
    assert _write_table_day_targets(_PKG / "facts" / "store.py") == ["fact_pack"]


def test_only_facts_store_writes_the_fact_pack_parquet():
    hits = [
        str(p.relative_to(_ROOT))
        for p in _SCANNED
        if "fact_pack" in _write_table_day_targets(p)
    ]
    assert hits == ["neckline/facts/store.py"], (
        "`write_table_day(\"fact_pack\", ...)` 出现在了 store.py 以外的地方:" + str(hits))


def _sql_literals(path: Path) -> List[str]:
    """该文件里所有字符串字面量(含 f-string 的静态片段),用于扫 SQL 语句。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


def test_only_facts_store_inserts_into_the_manifest():
    hits = []
    for p in _SCANNED:
        if any("INSERT INTO fact_packs" in s for s in _sql_literals(p)):
            hits.append(str(p.relative_to(_ROOT)))
    assert hits == ["neckline/facts/store.py"], hits


#: 覆盖后门的**语句形状**(⛔ 不是关键词邻近匹配 —— 那会被 docstring 里
#: 「⛔ 不是 `INSERT OR REPLACE`」这类自我说明打成永久红灯)。
_OVERWRITE_SHAPES = (
    re.compile(r"INSERT\s+OR\s+\w+\s+INTO\s+fact_packs", re.I),
    re.compile(r"UPDATE\s+fact_packs", re.I),
    re.compile(r"INTO\s+fact_packs\b[\s\S]{0,200}?ON\s+CONFLICT", re.I),
)


def test_nobody_ever_overwrites_a_manifest_row():
    """🔴 §5.3.2 纪律 3:同一 `(trade_date, pack_version)` 二次冻结必须抛错。
    `INSERT OR REPLACE` / `UPSERT` 会把那条纪律变成一句空话。"""
    hits = []
    for p in _SCANNED:
        for s in _sql_literals(p):
            flat = " ".join(s.split())
            for shape in _OVERWRITE_SHAPES:
                if shape.search(flat):
                    hits.append(f"{p.relative_to(_ROOT)}:{flat[:80]}")
    assert hits == [], "有人给事实包清单开了覆盖后门:\n" + "\n".join(hits)


# ══════════════════════════════════════════════════════════════════════════
# 类型级:freeze_pack 只接受 CompletePack
# ══════════════════════════════════════════════════════════════════════════

def test_freeze_pack_annotation_is_literally_complete_pack():
    src = Path(inspect.getsourcefile(fact_store)).read_text(encoding="utf-8")
    fn = next(
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.FunctionDef) and n.name == "freeze_pack"
    )
    first = fn.args.args[0]
    assert first.arg == "pack"
    assert isinstance(first.annotation, ast.Name), ast.dump(first.annotation)
    assert first.annotation.id == "CompletePack", (
        "`freeze_pack` 的形参注解被放宽了 —— 「数据未到齐 → 不冻结」是**类型错误**"
        "这条承诺就此作废(§5.3.2 纪律 1)")


def test_incomplete_pack_is_not_a_subclass_of_complete_pack():
    assert not issubclass(fact_pack.IncompletePack, fact_pack.CompletePack)
    assert not issubclass(fact_pack.CompletePack, fact_pack.IncompletePack)


# ══════════════════════════════════════════════════════════════════════════
# 遗留 1 / 遗留 2:三个 K8 报告件退役,`_MIN_MEMBERS` 物理消失
# ══════════════════════════════════════════════════════════════════════════

RETIRED_S3_MODULES: Tuple[str, ...] = (
    "neckline.report.industry_strength",
    "neckline.report.board_pool",
    "neckline.report.sectors",
)


@pytest.mark.parametrize("mod", RETIRED_S3_MODULES)
def test_retired_report_modules_are_physically_gone(mod):
    f = _ROOT / (mod.replace(".", "/") + ".py")
    assert not f.exists(), f"{f} 还在磁盘上 —— 退役是物理删除,不是停用"


@pytest.mark.parametrize("mod", RETIRED_S3_MODULES)
def test_retired_report_modules_are_not_importable(mod):
    """⛔ 不许留兼容 shim(同 S1 纪律)。"""
    with pytest.raises(ModuleNotFoundError):
        __import__(mod)


def test_retired_report_modules_have_zero_import_sites():
    hits = []
    for path in _SCANNED:
        for m in sorted(_imported_modules(path)):
            if any(m == r or m.startswith(r + ".") for r in RETIRED_S3_MODULES):
                hits.append(f"{path.relative_to(_ROOT)} → {m}")
    assert hits == [], "退役模块仍被 import:\n" + "\n".join(hits)


def _identifiers(path: Path) -> Set[str]:
    """该文件里出现的全部 Python 标识符(变量名 / 属性名 / 形参名 / 函数与类名)。

    ⛔ **刻意不扫 docstring 与注释**:本仓的模块头习惯把「⛔ 不许做 X」连同 X 的
    名字一起写进 docstring,裸文本 grep 会把这些**解释为什么它没了**的文字判红,
    而「一个对自己的注释报警的闸门等于没有闸门」(同 `conftest.source_code_only`
    的立论)。这里要禁的是**那个常量真的存在**,不是有人提起过它。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.keyword) and node.arg:
            out.add(node.arg)
    return out


def test_the_hardcoded_min_members_threshold_is_gone_from_the_whole_tree():
    """🔴 遗留 1:`report/industry_strength.py::_MIN_MEMBERS = 5` 是 §8.2 第 16 项
    **待标定参数**的硬编码值。⛔ 它绝不允许活进 K9 路径 —— 「行业成员数不足则不产出
    强度」直接决定哪些票拿不到相对强度、进不了形态召回,是**策略主张**,必须走参数包
    (`params.industry.minMembers`,住 `k9/industry_heat.py`)。

    ⚠ 与 `limitmap.MIN_CLUSTER_SIZE` 的分工:那一个判「这个簇**存不存在**」(孤身
    涨停按字面不构成共振),是工程不变量;本条判「哪些票拿不到行业强度」,是策略主张。
    ⛔ 别把这条豁免推广开。"""
    hits = [
        str(p.relative_to(_ROOT))
        for p in _SCANNED
        if any("MIN_MEMBERS" in ident.upper() for ident in _identifiers(p))
    ]
    assert hits == [], f"最小成员数门槛又回到了生产代码里:{hits}"


def test_the_identifier_scanner_is_not_vacuously_green():
    """扫描器自检:它必须真的能看见一个模块级常量名。"""
    assert "MIN_CLUSTER_SIZE" in _identifiers(_PKG / "facts" / "limitmap.py")
