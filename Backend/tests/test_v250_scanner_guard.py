"""V2.5.0 S15 · **扫描器自己的守门**(`tests/guard_scan.py`)。

🔴 **为什么单起一个文件**:本版八条「结构性锁死」的边界(G1/G2/G3/G4/G5/G7/G18/
G19/G21)全部压在一个 AST import 扫描器上。2026-08-21 三路复审实测:一行
`from ..llm import factory` 就能同时穿过它们**全部**,而且穿过去之后测试是绿的 ——
根因是 `guard_scan.imports()` 那句 `and not node.level` 把相对 import 整类跳过了。

**一个扫不到东西的闸门等于没有闸门,而它永远是绿的。** 所以扫描器本身必须有一组
**反例自检**:喂给它一份应当被拦下的源码,断言它真的看得见。下面每一条都是这样写的。

| 组 | 断言 |
|---|---|
| A · 模块路径 | `module_parts` / `package_parts` 在真实仓库文件上算得对 |
| B · 相对 import | `from ..llm import`、`from . import`、`from .x import` 三种写法都解析成绝对名 |
| C · 动态 import | `import_module("httpx")`、`import_module('why'+'notme')`、f-string 三种都命中 |
| D · 全仓兜底 | 解析不出的相对 import 恒空;模块名不是字面量的动态 import 恒空 |
| E · 调用图 | `reaches()` 真的顺着本文件内的调用链往下走 |
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import List

import pytest

from tests import guard_scan

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "neckline"
_SCRIPTS = _ROOT / "scripts"
_SCANNED = sorted(_PKG.rglob("*.py")) + sorted(_SCRIPTS.rglob("*.py"))


def _bait_tree(root: Path) -> Path:
    """造一棵**真包**(带 `__init__.py`)的诱饵树。

    ⚠ 必须是真包:`module_parts()` 靠 `__init__.py` 认包边界,拿一个裸文件当诱饵
    会让自检测的是另一条路径 —— 那种自检比没有更糟。
    """
    for rel in ("neckline", "neckline/k9", "neckline/k9/channels", "neckline/llm"):
        d = root / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text("", encoding="utf-8")
    return root


# ══════════════════════════════════════════════════════════════════════════
# A. 模块路径解析
# ══════════════════════════════════════════════════════════════════════════

def test_module_parts_reads_the_real_package_layout():
    assert guard_scan.module_parts(_PKG / "k9" / "ranking.py") == \
        ["neckline", "k9", "ranking"]
    assert guard_scan.module_parts(_PKG / "search" / "__init__.py") == \
        ["neckline", "search"]
    # `scripts/` 不是包 —— 它的模块路径只有文件名那一段。
    assert guard_scan.module_parts(_SCRIPTS / "evening.py") == ["evening"]


def test_package_parts_puts_an_init_file_in_its_own_package():
    """`__init__.py` 里的 `from .x import y` 落在**本包**,不是上一层。"""
    assert guard_scan.package_parts(_PKG / "k9" / "ranking.py") == ["neckline", "k9"]
    assert guard_scan.package_parts(_PKG / "search" / "__init__.py") == \
        ["neckline", "search"]


def test_resolve_relative_refuses_to_climb_past_the_top_package():
    """`from ... import` 写在只有两层包的模块里,运行期本来就会炸 ——
    解析器⛔ 不许假装它解析出了什么。"""
    p = _PKG / "k9" / "ranking.py"
    assert guard_scan.resolve_relative(p, 1, "volume") == "neckline.k9.volume"
    assert guard_scan.resolve_relative(p, 2, "llm") == "neckline.llm"
    assert guard_scan.resolve_relative(p, 3, "llm") is None


# ══════════════════════════════════════════════════════════════════════════
# B. 相对 import 的三种写法(🔴 复审 CE1/CE3/CE5/CE11/CE20/CE21/CE22/CE23 的根因)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("source,prefix,expected", [
    # CE1 / CE21 / CE23:策略层、核对表、复盘层零 LLM
    ("from ..llm import factory\n", "neckline.llm", "neckline.llm.factory"),
    # CE3:取数唯一来源是事实包
    ("from ..data import market_data\n", "neckline.data",
     "neckline.data.market_data"),
    # CE11:退役包零残留
    ("from ..sentinel import quotes\n", "neckline.sentinel",
     "neckline.sentinel.quotes"),
    # 点名子模块的写法
    ("from ..llm.factory import build\n", "neckline.llm",
     "neckline.llm.factory.build"),
])
def test_relative_imports_resolve_to_absolute_names(tmp_path, source, prefix, expected):
    """🔴 **本文件最要紧的一条**:`not node.level` 那个洞的直接反例。

    修之前这四条全部零命中 —— 八条边界一起对最自然的包内写法失明。
    """
    root = _bait_tree(tmp_path)
    bait = root / "neckline" / "k9" / "ranking.py"
    bait.write_text(source, encoding="utf-8")
    assert expected in guard_scan.imports(bait)
    assert guard_scan.imports_any(bait, prefix), (
        f"扫描器看不见 `{source.strip()}` —— import 型守门又变成纸糊的了")


def test_a_bare_from_dot_import_names_the_sibling_module(tmp_path):
    """CE5:`from . import p2_rebound` —— 通道互不知道那条边界的直接反例。"""
    root = _bait_tree(tmp_path)
    bait = root / "neckline" / "k9" / "channels" / "p1_breakout.py"
    bait.write_text("from . import p2_rebound\n", encoding="utf-8")
    got = guard_scan.imports(bait)
    assert "neckline.k9.channels.p2_rebound" in got, got
    assert "neckline.k9.channels" in got


def test_absolute_imports_are_still_seen_exactly_as_before(tmp_path):
    """对照组:修相对 import 不许把绝对 import 那条路弄坏。"""
    root = _bait_tree(tmp_path)
    bait = root / "neckline" / "k9" / "ranking.py"
    bait.write_text("import httpx\nfrom neckline.llm import factory\n", encoding="utf-8")
    got = guard_scan.imports(bait)
    assert {"httpx", "neckline.llm", "neckline.llm.factory"} <= got


# ══════════════════════════════════════════════════════════════════════════
# C. 动态 import(🔴 复审 CE4 与 `'why'+'notme'` 那条)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("source,expected", [
    ('import importlib\nx = importlib.import_module("httpx")\n', "httpx"),
    ('from importlib import import_module\nx = import_module("httpx")\n', "httpx"),
    ("import importlib\nx = importlib.import_module('why'+'notme')\n", "whynotme"),
    ('x = __import__("httpx")\n', "httpx"),
    ("import importlib\nx = importlib.import_module(f\"why{'not'}me\")\n", "whynotme"),
])
def test_dynamic_imports_with_a_computable_module_name_are_seen(
    tmp_path, source, expected,
):
    """字面量、常量拼接、全常量 f-string —— 三种「把模块名藏一藏」的写法都要看得见。"""
    root = _bait_tree(tmp_path)
    bait = root / "neckline" / "k9" / "ranking.py"
    bait.write_text(source, encoding="utf-8")
    assert expected in guard_scan.imports(bait), guard_scan.imports(bait)


def test_a_dynamic_import_of_a_relative_target_resolves_too(tmp_path):
    root = _bait_tree(tmp_path)
    bait = root / "neckline" / "k9" / "ranking.py"
    bait.write_text(
        'import importlib\nx = importlib.import_module("..llm", __package__)\n',
        encoding="utf-8")
    assert "neckline.llm" in guard_scan.imports(bait)


def test_an_opaque_dynamic_import_is_reported_instead_of_ignored(tmp_path):
    """模块名只有运行期才知道 = 任何静态判据都够不着 —— ⛔ 不许当没看见。"""
    root = _bait_tree(tmp_path)
    bait = root / "neckline" / "k9" / "ranking.py"
    bait.write_text(
        "import importlib\ndef f(name):\n    return importlib.import_module(name)\n",
        encoding="utf-8")
    assert guard_scan.opaque_dynamic_imports(bait)


# ══════════════════════════════════════════════════════════════════════════
# D. 全仓兜底:两类「扫描器够不着」的写法必须恒空
# ══════════════════════════════════════════════════════════════════════════

def test_the_scan_actually_covers_both_trees():
    rel = {str(p.relative_to(_ROOT)) for p in _SCANNED}
    assert any(p.startswith("neckline/") for p in rel)
    assert any(p.startswith("scripts/") for p in rel)
    assert len(_SCANNED) > 80, f"扫描域只有 {len(_SCANNED)} 个文件 —— glob 怕是瞎了"


def test_no_relative_import_in_the_repo_escapes_resolution():
    """解析不出来的相对 import = 所有 import 判据对它失明。"""
    hits: List[str] = []
    for path in _SCANNED:
        hits.extend(guard_scan.unresolvable_relative_imports(path))
    assert hits == [], "这些相对 import 解析不成绝对模块名:\n" + "\n".join(hits)


def test_no_dynamic_import_in_the_repo_hides_its_module_name():
    """⛔ 要动态 import,模块名就得写成静态算得出来的常量。

    否则 G2 / G3 / G18 / G19 那几条「零 import」的断言可以被一个变量绕开,
    而它们仍然是绿的。
    """
    hits: List[str] = []
    for path in _SCANNED:
        hits.extend(guard_scan.opaque_dynamic_imports(path))
    assert hits == [], "动态 import 的模块名藏起来了:\n" + "\n".join(hits)


# ══════════════════════════════════════════════════════════════════════════
# E. 调用图闭包(「读路径不执行 DDL」那条闸的底座)
# ══════════════════════════════════════════════════════════════════════════

def test_reaches_follows_a_call_chain_not_just_the_direct_call(tmp_path):
    """自检:间接调用也要看得见 —— 只查直接调用的版本,套一层 helper 就绕过去了。"""
    bait = tmp_path / "s.py"
    bait.write_text(textwrap.dedent("""
        def init_schema(p): ...
        def _open(p):
            init_schema(p)
        def load_thing(p):
            return _open(p)
        def compute(p):
            return _open(p)
    """), encoding="utf-8")
    hits = guard_scan.reaches(bait, ("load_",), "init_schema")
    assert len(hits) == 1 and "load_thing" in hits[0], hits
    assert "_open → init_schema" in hits[0]


def test_reaches_says_nothing_when_the_chain_does_not_get_there(tmp_path):
    bait = tmp_path / "s.py"
    bait.write_text(textwrap.dedent("""
        def _open(p): ...
        def load_thing(p):
            return _open(p)
    """), encoding="utf-8")
    assert guard_scan.reaches(bait, ("load_",), "init_schema") == []


def test_reaches_survives_mutual_recursion(tmp_path):
    """闭包要能收敛 —— 一个会栈溢出的扫描器等于一个会红的守门。"""
    bait = tmp_path / "s.py"
    bait.write_text(textwrap.dedent("""
        def a(): b()
        def b(): a()
        def load_x(): a()
    """), encoding="utf-8")
    assert guard_scan.reaches(bait, ("load_",), "init_schema") == []
