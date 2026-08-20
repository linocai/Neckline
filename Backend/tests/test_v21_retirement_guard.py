"""V2.1-①「问询台整链退役」的全仓守门(plan §五 V2.1-① 测试与守门条款)。

体例照 `test_v1_retirement_guard.py` 的三类断言 —— 两份守门文件各自独立、各自
对应各自的退役事件,同项目 `retire_k4_b3.py` 类一次性脚本"一个事件一个文件"惯例。

🔴 **import 型判据不再自带抄本**(V2.5.0 收敛):本文件原来复制了一份
`_imported_names`,它的 `ast.ImportFrom` 分支写着 `node.level == 0`
—— **对相对 import 全盲**。一行 `from ..api import inquiry` 就能让整条退役守门
看不见,而它照绿。现在统一走 `tests/guard_scan.py::import_hits()`(全仓唯一实现,
相对 import 会被解析成绝对名;解析不出来的那种由 `test_v250_scanner_guard.py`
另有一条全仓守门当场点名)。⚠ `_sql_literal` / `_write_sql_hits` 两个是**写 SQL**
判据,不在这次收敛范围内。

本文件每条断言都是某条老断言的**反转**,docstring 逐条写明反转自哪里:

  · `test_inquiry_desk_is_gone` —— 反转自
    `test_v1_retirement_guard.py::test_13_10_inquiry_desk_itself_survives`
    (老断言"问询台主体必须还在",新断言"问询台必须零残留");老文件里那条已删除,
    只留一行留痕注释指回本文件。
  · `test_discipline_checks_dies_with_its_last_consumer` —— 反转自
    `test_v1_retirement_guard.py::
    test_13_11_discipline_checks_moved_out_instead_of_dying_with_it`
    (老断言"纪律判定项搬家到独立模块,因为问询台还在用它";新断言"问询台没了,
    它唯一的消费方也随之陪葬"——老断言的前提"问询台是保留件"已被 V2.1-① 用户
    裁定 #1 推翻,老文件里那条已删除)。这条测试真正要保的不是"文件消失"本身,
    而是**单一源 `research/panel.py::base_universe_expr` 还在、还被原来那两个
    消费方读**——⛔ 全仓不许因为 `discipline_checks.py` 死了就有人在别处手写第二份
    等价表达式(CLAUDE.md「纪律红绿灯」一条的教训)。
  · `test_task_inquiry_is_retired` —— 反向守门(防复活),体例同 P0-44 删
    `LONG_CONTEXT_READ_TIMEOUT_SECONDS` 的 `hasattr` 反向断言。
  · `test_retired_route_key_is_filtered_on_read` —— 承 plan §五 V2.1-①原文
    "必须同时做的两件,少一件生产会炸"之一:`settings_store.get_llm_routes()`
    读侧过滤未知任务名,让"库里残留 `inquiry` 键"这个历史场景不会挡住
    `set_llm_routes()` 把读回来的 routes 原样 PUT 回去。
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from typing import List, Tuple

from tests import guard_scan

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "neckline"
_PY_FILES = sorted(_PKG.rglob("*.py"))
# 停写守门的扫描域 = `neckline/` + `scripts/`(同 test_v1_retirement_guard.py 体例)。
_WRITE_SCAN_FILES = sorted(_PKG.rglob("*.py")) + sorted((_ROOT / "scripts").rglob("*.py"))
_EXEC_METHODS = {"execute", "executemany", "executescript"}


# ======================================================================
#  共用扫描器(逐字照抄 test_v1_retirement_guard.py,两份守门文件各自独立)
# ======================================================================

def _import_hits(needle: str) -> List[str]:
    """哪些文件 import 了 `needle` 或它的子模块(`文件 → 模块` 清单)。

    🔴 判据的**唯一实现**在 `guard_scan.import_hits()`。本文件从前自带一份抄本,
    它写着 `node.level == 0` —— 对**相对 import 全盲**,`from ..api import inquiry`
    一行就能绕过整条退役守门而它照绿。⛔ 不许再抄回来。
    **只看 import 语句**,注释与字符串里的旧名字不计 —— 历史说明不是残留。
    """
    return guard_scan.import_hits(_PY_FILES, [needle], root=_ROOT)


def _sql_literal(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else ""
                       for v in node.values)
    return None


def _write_sql_hits(table: str) -> List[Tuple[str, int, str]]:
    """`neckline/` + `scripts/` 全域针对该表的任何写入调用点(宁可漏报不许误报)。"""
    forbidden = (
        f"INSERT INTO {table}", f"UPDATE {table}", f"DELETE FROM {table}",
        f"REPLACE INTO {table}",
        f"INSERT OR IGNORE INTO {table}",
        f"INSERT OR ABORT INTO {table}", f"INSERT OR FAIL INTO {table}",
        f"INSERT OR ROLLBACK INTO {table}",
    )
    hits: List[Tuple[str, int, str]] = []
    for path in _WRITE_SCAN_FILES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else None)
            if name not in _EXEC_METHODS or not node.args:
                continue
            sql = _sql_literal(node.args[0])
            if sql is None:
                continue
            upper = " ".join(sql.upper().split())
            for f in forbidden:
                if f.upper() in upper:
                    hits.append((str(path.relative_to(_ROOT)), node.lineno, f))
    return hits


# ======================================================================
#  ① 问询台整链退役:产品面 + 代码面零残留
# ======================================================================

def test_inquiry_desk_is_gone():
    """`api/inquiry.py` 物理删除 + 全仓零 import + 路由面零 `/inquiry*` + `inquiry_log`
    零写入调用点(表停写留档不 DROP,§七 P4-31 七张之一)。"""
    assert not (_PKG / "api" / "inquiry.py").exists()
    assert _import_hits("neckline.api.inquiry") == []

    from neckline.api.app import app

    paths = {r.path for r in app.routes}
    assert not any("/inquiry" in p or "/inquiries" in p for p in paths), \
        f"路由面残留问询台端点:{[p for p in paths if 'inquir' in p]}"

    assert _write_sql_hits("inquiry_log") == [], "inquiry_log 应已停写(§七 P4-31)"


def test_inquiry_pool_is_untouched_by_this_retirement():
    """🔴 反向防呆:`inquiry_pool` 与 `inquiry_log` 名字像、处置刻意不同——前者是
    周复盘 `review/reconcile.py` 仍在读的历史队列表,本次退役**零接触**。锁住
    `load_inquiry_pool` 这个唯一只读函数没有被误伤连坐删除。"""
    from neckline.api import stores

    assert hasattr(stores, "load_inquiry_pool")
    assert not hasattr(stores, "create_inquiry_log")
    assert not hasattr(stores, "list_inquiry_logs")
    assert not hasattr(stores, "get_inquiry_log")


# ======================================================================
#  ① discipline_checks 死于最后一个消费方
# ======================================================================

def test_discipline_checks_dies_with_its_last_consumer():
    """退役模块和整个研究实现都不应在生产包中复活。"""
    assert not (_PKG / "report" / "discipline_checks.py").exists()
    assert _import_hits("neckline.report.discipline_checks") == []
    assert not (_PKG / "research").exists()
    assert _import_hits("neckline.research") == []


# ======================================================================
#  ① TASK_INQUIRY 退役(防复活)
# ======================================================================

def test_task_inquiry_is_retired():
    """反向守门(防复活,同 §七 P0-44 删 `LONG_CONTEXT_READ_TIMEOUT_SECONDS` 的
    `hasattr` 体例):`TASK_INQUIRY` 从常量本体 / `ALL_TASKS` / `DEFAULT_SEARCH_TASKS`
    / `__all__` 四处一并移除。"""
    from neckline.llm import router

    assert not hasattr(router, "TASK_INQUIRY")
    assert "inquiry" not in router.ALL_TASKS
    assert "inquiry" not in router.DEFAULT_SEARCH_TASKS
    assert "TASK_INQUIRY" not in router.__all__


# ======================================================================
#  ① 退役任务名读侧过滤(两件套之一,另一件是 scripts/oneoff/strip_retired_llm_routes.py)
# ======================================================================

def test_retired_route_key_is_filtered_on_read(isolated_env):
    """库里塞一个含已退役任务名的路由表(模拟"问询台退役前写入过、或从老备份恢复"
    这个场景)——`get_llm_routes()` 只回现役任务的键,且**读回来的那份**再喂给
    `set_llm_routes()` 不抛(否则用户在设置屏按一次保存就 400,见 plan §五 V2.1-①
    原文"为什么两件都要")。"""
    from neckline import settings_store
    from neckline.db import init_schema

    db_path = isolated_env.db_path
    init_schema(db_path)
    settings_store.create_provider(
        "GLM", "https://example.test/chat/completions", "glm-test",
        api_key="test-key", db_path=db_path,
    )
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (id, push_report, push_retreat, review_col_map) "
            "VALUES (1, 1, 1, '{}')"
        )
        conn.execute(
            "UPDATE app_settings SET llm_task_routes=? WHERE id=1",
            ('{"inquiry": "GLM", "review": "GLM"}',),
        )
        conn.commit()
    finally:
        conn.close()

    routes, _default = settings_store.get_llm_routes(db_path=db_path)
    assert routes == {"review": "GLM"}, "未知任务名 inquiry 应被读侧过滤掉"

    # 读回来的那份原样 PUT 回去不许抛(§五①原文点名的生产炸法)。
    settings_store.set_llm_routes(routes, None, db_path=db_path)
    roundtrip, _ = settings_store.get_llm_routes(db_path=db_path)
    assert roundtrip == {"review": "GLM"}


def test_retired_route_key_filter_logs_a_warning(isolated_env, caplog):
    """过滤动作必须留痕(WARNING),不是静默丢弃——同项目"诚实披露"纪律。"""
    import logging

    from neckline import settings_store
    from neckline.db import init_schema

    db_path = isolated_env.db_path
    init_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (id, push_report, push_retreat, review_col_map) "
            "VALUES (1, 1, 1, '{}')"
        )
        conn.execute(
            "UPDATE app_settings SET llm_task_routes=? WHERE id=1", ('{"inquiry": "GLM"}',),
        )
        conn.commit()
    finally:
        conn.close()

    with caplog.at_level(logging.WARNING, logger="neckline.settings_store"):
        settings_store.get_llm_routes(db_path=db_path)
    assert any("inquiry" in r.message for r in caplog.records)


def test_the_import_scanner_is_not_blind_to_relative_imports(tmp_path: Path):
    """🔴 **收敛后的反例自检**(V2.5.0):一行相对 import 不许绕过整条退役守门。

    本文件从前自带一份 `_imported_names` 抄本,它的 `ImportFrom` 分支写着
    `node.level == 0` —— `from ..api import inquiry` 在它眼里**不存在**,
    于是「问询台零残留」这条断言可以在问询台被复活的情况下照绿。
    ⚠ 诱饵必须放在**真包**里(`guard_scan` 靠 `__init__.py` 认包边界),
    拿裸文件当诱饵会测到另一条路径 —— 那种自检比没有更糟。
    """
    for rel in ("neckline", "neckline/api", "neckline/report"):
        d = tmp_path / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text("", encoding="utf-8")
    bait = tmp_path / "neckline" / "report" / "bait.py"
    bait.write_text("from ..api import inquiry\n", encoding="utf-8")
    assert guard_scan.import_hits([bait], ["neckline.api.inquiry"]), (
        "`from ..api import inquiry` 绕过了退役守门 —— 扫描器又对相对 import 瞎了")
    # 反向:不相干的 import 不许被判红。
    clean = tmp_path / "neckline" / "report" / "clean.py"
    clean.write_text("from ..api import notify\nimport json\n", encoding="utf-8")
    assert guard_scan.import_hits([clean], ["neckline.api.inquiry"]) == []
