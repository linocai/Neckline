"""测试库隔离守门(§七 P4-25 / P4-48,v1.5-④-A4)。

🔴 **2026-08-21 换过一次判据,原因写在这里**。原来这份文件守的是五个**裸调用**名字:
`active_config` / `get_active` / `get_active_pack` / `get_active_skeleton` /
`get_active_engines`。它们随 `neckline.strategy.brain` 与 `neckline.selection.pack`
在 V2.5.0 S1 被**物理删除** —— 全仓定义数归零之后,这份按测试文件参数化的守门变成了
**71 条恒绿的断言**。三路复审逐条实测过:它已经在守一个空集。

⚠ 而它要防的那种病今天规模更大:`db_path: Optional[Path] = None → settings.db_path`
这个兜底签名,在新八个 store 里有 **60 多处**。名字一个都不在原来那份黑名单里。

**新判据(结构性,不是名字清单)**:

    ① 风险面**从生产源码算出来** —— `neckline/**/store.py` 与 `settings_store.py` 里
       每个带 `db_path=None` 兜底的公开函数。⛔ 不手写名单:手写名单会随退役归零,
       那正是上一版的死法。
    ② 测试文件调这些函数时必须**显式给库**(`db_path=` 关键字,或位置参数给到那一位)。
    ③ 判据自带**自检**:风险面非空 + 一个诱饵文件必须被抓到。

**为什么还要这条(conftest 已经全局重定向了)**:P4-48 那段兜底保证的是「漏传打不到
真实开发库」,它治的是**灾难**;这条治的是**污染** —— 漏传的测试会去共用那一个进程级
临时库,于是「这条用例看见的行是谁写的」变成一道要现场推理的题。两条都要。

**另一半风险面在别处**:那 60 多个 store 函数里,`load_*` / `latest_*` / `list_*` /
`get_*` 有 43 个会顺带 `init_schema()`(一次读就把库迁移了)。那一条闸在
`test_v250_s14_release_gate.py::test_no_read_helper_triggers_a_schema_migration`,
目前 `xfail(strict=True)` 挂着(§13.1-B13)。

⚠ **AST 精确匹配,不是纯文本 grep**:只抓「代码里真的发起了调用」,不误伤 docstring /
注释里提到这些名字的散文引用 —— 本仓的模块头习惯把「⛔ 不许做 X」连同 X 的名字一起
写进 docstring,纯文本 substring 会把它们错误命中。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_ROOT = _TESTS_DIR.parent
_PKG = _ROOT / "neckline"


# ══════════════════════════════════════════════════════════════════════════
# 风险面:从**生产源码**算出来,⛔ 不手写名单
# ══════════════════════════════════════════════════════════════════════════

def _store_modules() -> Dict[str, Path]:
    """落库层的模块全名 → 文件。

    ⚠ 用**文件名规则**而不是写死清单:S17 新加一个 `scorecard/listing_store.py`
    也会自动进来。上一版守门的死因就是清单会过期,而过期之后它仍然是绿的。
    """
    out: Dict[str, Path] = {}
    for path in sorted(_PKG.rglob("*.py")):
        if path.name == "store.py" or path.name.endswith("_store.py"):
            out[".".join(path.relative_to(_ROOT).with_suffix("").parts)] = path
    return out


def _db_path_default_index(node: ast.AST) -> Tuple[bool, Optional[int]]:
    """这个函数带 `db_path=None` 兜底吗;带的话它是第几个位置参数。"""
    args = node.args  # type: ignore[attr-defined]
    positional = [a.arg for a in args.posonlyargs + args.args]
    kwonly = [a.arg for a in args.kwonlyargs]
    if "db_path" not in positional + kwonly:
        return False, None
    defaults: Dict[str, ast.AST] = {}
    if args.defaults:
        defaults.update(dict(zip(positional[-len(args.defaults):], args.defaults)))
    for name, default in zip(args.kwonlyargs, args.kw_defaults):
        if default is not None:
            defaults[name.arg] = default
    got = defaults.get("db_path")
    if not (isinstance(got, ast.Constant) and got.value is None):
        return False, None
    return True, positional.index("db_path") if "db_path" in positional else None


def risk_surface() -> Dict[str, Tuple[str, Optional[int]]]:
    """`函数名 → (模块全名, db_path 的位置序号)`。"""
    out: Dict[str, Tuple[str, Optional[int]]] = {}
    for module, path in _store_modules().items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            has, index = _db_path_default_index(node)
            if has:
                out[node.name] = (module, index)
    return out


_RISK = risk_surface()


def _store_bindings(tree: ast.AST) -> Tuple[Set[str], Set[str]]:
    """一个文件里,哪些名字绑到了 store **模块**、哪些绑到了 store **函数**。

    🔴 **必须解析绑定,⛔ 不能只比函数名**:`re.search(...)` 与
    `settings_store.search(...)` 同名,只比名字会把前者判红 —— 实测 27 条假阳性,
    其中 24 条是 `re.search`。而假阳性会逼着后来者把守门放宽。
    """
    modules = _store_modules()
    mod_names: Set[str] = set()
    func_names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in modules:
                    mod_names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if f"{node.module}.{alias.name}" in modules:
                    mod_names.add(alias.asname or alias.name)
                elif node.module in modules:
                    func_names.add(alias.asname or alias.name)
    return mod_names, func_names


def implicit_db_calls(path: Path) -> List[Tuple[int, str]]:
    """这个文件里「调了落库层函数、却没说用哪个库」的 `(行号, 函数名)`。

    ⚠ `f(**payload)` 这种展开算**通过** —— 静态上证不出它漏了(`test_report_store.py`
    的 `_save()` 就是把 `db_path=db` 装在 payload 里传进去的,那是合法写法)。
    这是这条判据已知的残余口子,写在这里免得下一个人以为它滴水不漏。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mod_names, func_names = _store_bindings(tree)
    hits: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id in mod_names):
            name = func.attr
        elif isinstance(func, ast.Name) and func.id in func_names:
            name = func.id
        else:
            continue
        if name not in _RISK:
            continue
        if any(kw.arg == "db_path" for kw in node.keywords):
            continue
        if any(kw.arg is None for kw in node.keywords):
            continue                      # `**payload` —— 证不出它漏了
        index = _RISK[name][1]
        if index is not None and len(node.args) > index:
            continue                      # 位置参数给到了那一位
        hits.append((node.lineno, name))
    return hits


# ══════════════════════════════════════════════════════════════════════════
# 判据自检:一个守空集的闸门永远是绿的
# ══════════════════════════════════════════════════════════════════════════

def test_the_risk_surface_is_not_empty():
    """🔴 **上一版就是死在这里**:黑名单里的五个名字全仓定义数归零之后,
    71 条参数化断言恒绿,而没有任何一条测试会告诉你这件事。

    这条断言就是那个教训:**风险面必须先证明它自己非空**。
    """
    assert len(_store_modules()) >= 8, f"落库层只找到 {list(_store_modules())}"
    assert len(_RISK) >= 40, (
        f"带 `db_path=None` 兜底的落库层函数只找到 {len(_RISK)} 个 —— "
        f"扫描器怕是失效了,而失效之后本文件全部断言恒绿")
    # 点名几个**确实存在**的入口,证明扫的是真东西。
    for expected in ("load_k9_report", "save_k9_report", "load_pack", "get_app_settings"):
        assert expected in _RISK, f"风险面里没有 `{expected}` —— 扫描规则漂了"


def test_the_implicit_db_detector_actually_detects(tmp_path: Path):
    """诱饵自检:一个应当被拦下的写法,判据必须真的看得见。"""
    bait = tmp_path / "test_bait.py"
    bait.write_text(
        "from neckline.report import store as report_store\n"
        "from datetime import date\n"
        "def test_x():\n"
        "    report_store.load_k9_report(date(2026, 8, 20))\n",
        encoding="utf-8")
    assert implicit_db_calls(bait) == [(4, "load_k9_report")]

    ok = tmp_path / "test_ok.py"
    ok.write_text(
        "from neckline.report import store as report_store\n"
        "from datetime import date\n"
        "def test_x(tmp_path):\n"
        "    report_store.load_k9_report(date(2026, 8, 20), db_path=tmp_path / 'n.db')\n",
        encoding="utf-8")
    assert implicit_db_calls(ok) == []

    # ⛔ 同名但不是落库层的调用不许被判红(`re.search` 与 `settings_store.search`)。
    unrelated = tmp_path / "test_unrelated.py"
    unrelated.write_text("import re\ndef test_x():\n    re.search('a', 'b')\n",
                         encoding="utf-8")
    assert implicit_db_calls(unrelated) == []


# ══════════════════════════════════════════════════════════════════════════
# 正题:每个测试文件都必须说清它用的是哪个库
# ══════════════════════════════════════════════════════════════════════════

_TEST_FILES = sorted(_TESTS_DIR.glob("test_*.py"))

#: 确有正当理由不显式传库的文件 + 理由(目前应为空)。加白名单前先问自己:
#: 这个调用真的需要落到进程级那个共用临时库上吗?
_WHITELIST: Dict[str, str] = {}


@pytest.mark.parametrize("path", _TEST_FILES, ids=lambda p: p.name)
def test_every_store_call_in_tests_names_its_database(path: Path):
    reason = _WHITELIST.get(path.name)
    if reason is not None:
        pytest.skip(f"{path.name} 在白名单(理由:{reason}),跳过本断言")
    hits = implicit_db_calls(path)
    assert not hits, (
        f"{path.name} 调了落库层函数却没说用哪个库 {hits} —— 那些函数的签名是 "
        f"`db_path: Optional[Path] = None → settings.db_path`,漏传会落到进程级共用的"
        f"那个临时库上,「这条用例看见的行是谁写的」就变成一道要现场推理的题。"
        f"显式传 `db_path=`;真需要真库数据用 `real_db_readonly_copy` 夹具(conftest.py)。"
    )


def test_whitelist_is_currently_empty():
    """本条不是必须永远为空 —— 若未来确有正当例外,加进 `_WHITELIST` 并写理由即可
    (见模块头);本测试只是防止例外被悄悄加了却没人注意到,倒逼加白名单时至少
    改一次这里。"""
    assert _WHITELIST == {}


def test_scan_actually_covers_known_database_guardrails():
    """防止 glob 模式失效导致本守门形同虚设。"""
    names = {p.name for p in _TEST_FILES}
    # 🔴 V2.5.0 S1:原来点名的 `test_brain.py` / `test_activate_pack_script.py` 随
    # `strategy/` 与 `selection/` 整包退役而删除;换几个**仍在且真的会开库**的文件顶上
    # —— 这条守门要证明的是「glob 没瞎」,不是「那两个文件还在」。
    for expected in (
        "test_review_reconcile.py",
        "test_v2_schema_guard.py",
        "test_report_store.py",
        "test_db_isolation_guardrail.py",
    ):
        assert expected in names
    assert len(_TEST_FILES) > 50, f"只收到 {len(_TEST_FILES)} 个测试文件"


# ══════════════════════════════════════════════════════════════════════════
# §七 P4-48(V2.2-① 结案):conftest **全局兜底重定向**的机器判据(治类不治例)。
# conftest.py 在 import 任何 neckline 模块之前把 `DB_PATH` 环境变量指到一次性临时
# 目录 → 全量套件里任何 `db_path=None` 兜底(`neckline/db.py::get_connection` 的
# `db_path or settings.db_path`)天然落进废弃桶,新测试怎么漏传都污染不到真实开发库
# `data/neckline.db`。下面三条一起构成「重定向真的生效」的判据 —— 有人删掉 conftest
# 顶部那段注入、或 neckline.config 的 DB_PATH 后门被移除,这里当场红。
# ⚠ 验收铁律(A8 / P4-48 案底原文):「MD5 没变」不等于没泄漏(幂等写照样是泄漏)
# ——本守门锁的是"兜底根本到不了真库",不是"真库碰巧没变化";全量探针复核
# (patch `sqlite3.connect` 记 nodeid+栈)在每次结案验收时另跑,探针本身不入仓。
# ══════════════════════════════════════════════════════════════════════════

def test_p448_conftest_redirects_default_db_away_from_real_dev_db():
    import os

    from neckline.config import DB_PATH, settings

    redirected = os.environ.get("DB_PATH")
    assert redirected, "conftest 的 DB_PATH 全局兜底重定向没生效(P4-48 修复被移除?)"
    assert Path(redirected) == settings.db_path, (
        "settings.db_path 没吃到 DB_PATH 环境变量——neckline.config 的覆盖后门被改掉了?"
    )
    assert settings.db_path.resolve() != DB_PATH.resolve(), (
        "兜底库仍指向真实开发库 data/neckline.db(重定向形同虚设)"
    )


def test_p448_default_connection_opens_redirected_db_not_real_one(tmp_path):
    """不只看配置,真开一条**零参数兜底**连接,问 SQLite 它到底打开了哪个文件。"""
    from neckline.config import DB_PATH, settings
    from neckline.db import get_connection

    conn = get_connection()          # 刻意零参数:测的就是 db_path=None 兜底
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    finally:
        conn.close()
    main_file = Path([r[2] for r in rows if r[1] == "main"][0]).resolve()
    assert main_file == settings.db_path.resolve()
    assert main_file != DB_PATH.resolve(), "零参数连接打开了真实开发库(P4-48 复发)"


def test_p448_real_db_readonly_copy_still_finds_the_real_dev_db():
    """反向确认:重定向**不**波及 `real_db_readonly_copy`(它按 `neckline.config.
    DB_PATH` 常量找真库,是三个护栏文件刻意读真库的唯一合法通道)——常量必须仍指
    项目 `data/neckline.db`,不随环境变量漂。"""
    from neckline.config import DB_PATH, PROJECT_ROOT

    assert DB_PATH == PROJECT_ROOT / "data" / "neckline.db"
