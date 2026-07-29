"""测试库隔离守门(§七 P4-25,v1.5-④-A4)。`brain.active_config()`/`brain.get_active()`
**裸调用**(零参数)会绕过所有测试隔离夹具、直接命中 `neckline/db.py` 自己的模块级
`settings.db_path`(= 真实项目 `data/neckline.db`,项目 CLAUDE.md「测试隔离」条:
`isolated_env`/`api_env` 不覆盖这一路径),顺带让每次调用触发的 `init_schema()`
(`_migrate_columns`)把新表/新列的幂等迁移悄悄写进开发者的工作库——2026-07-29 已
实测复现:`llm_judgments.search_engine` 这一列就是这样在本机被提前建出来的。

真需要真库 K1 现役行的 guardrail 用例(K2/K3/v13 三个「刻意读真库」的护栏文件)
须改用 `conftest.py::real_db_readonly_copy` 夹具、显式传 `db_path=`。

**AST 精确匹配,不是纯文本 grep**(与 v1.4-⑩-B `test_industry_strength_store.py::
test_online_paths_never_reference_full_scan_entrypoints` 的字符串禁用体例不同,
理由见下):只抓「代码里真的发起了零参数方法调用」,不误伤 docstring / 注释里
提到这两个名字的散文引用——`test_brain.py` 就有 4 处这样的无害提及(描述
`brain.py` 内部退化行为的 docstring),纯文本 substring 匹配会把它们错误命中;
AST 只看真实语法树,天然不会被注释/字符串字面量污染。"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Tuple

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_BANNED_BARE_CALLS = {"active_config", "get_active"}

# 确有正当理由裸调用的文件 + 理由(目前应为空——三个「刻意读真库」的护栏文件已全部
# 改用 `real_db_readonly_copy` 显式传参,不需要例外)。加白名单前先问自己:这个
# 调用真的需要绕开所有测试隔离直接读写真实开发库吗?
_WHITELIST: dict = {}


def _bare_banned_calls(path: Path) -> List[Tuple[int, str]]:
    """扫一个文件,返回其中「零参数调用 `X.active_config()` / `X.get_active()`」的
    `(行号, 函数名)` 列表(`X.` 前缀是 attribute 调用,如 `brain.active_config()`;
    裸名调用 `active_config()`/`get_active()` 同样命中,防御性覆盖 `from ... import`
    写法)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        else:
            continue
        if name in _BANNED_BARE_CALLS and not node.args and not node.keywords:
            hits.append((node.lineno, name))
    return hits


_TEST_FILES = sorted(_TESTS_DIR.glob("test_*.py"))


@pytest.mark.parametrize("path", _TEST_FILES, ids=lambda p: p.name)
def test_no_bare_brain_db_reads_in_tests(path: Path):
    hits = _bare_banned_calls(path)
    reason = _WHITELIST.get(path.name)
    if reason is not None:
        pytest.skip(f"{path.name} 在白名单(理由:{reason}),跳过本断言")
    assert not hits, (
        f"{path.name} 出现无参数裸调用 {hits}(§七 P4-25:会绕过测试隔离直接读写真实"
        f"开发库,须显式传 db_path=;真需要真库数据用 real_db_readonly_copy 夹具,"
        f"见 conftest.py)"
    )


def test_whitelist_is_currently_empty():
    """本条不是必须永远为空——若未来确有正当例外,加进 `_WHITELIST` 并写理由即可
    (见模块头);本测试只是防止例外被悄悄加了却没人注意到,倒逼加白名单时至少
    改一次这里。"""
    assert _WHITELIST == {}


def test_scan_actually_covers_the_three_known_guardrail_files():
    """防止 glob 模式本身失效导致本守门形同虚设(如目录结构变化、文件改名)——
    确认三个已知「刻意读真库」的护栏文件确实在扫描范围内。"""
    names = {p.name for p in _TEST_FILES}
    for expected in (
        "test_k3_oversold_guardrail.py",
        "test_k2_mainline_guardrail.py",
        "test_v13_exit_6y_baseline.py",
    ):
        assert expected in names
