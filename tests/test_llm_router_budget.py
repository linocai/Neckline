"""V2-② 新增模块单测(plan §五 V2-②「测试与守门」六条里,不在 `test_llm.py`/
`test_api_settings.py` 覆盖范围内的部分):
    ① `neckline.llm.router` 任务常量 + 默认路由纯函数(路由解析四态已在
       `test_llm.py::TestFactory` 端到端覆盖,本文件补纯函数层面的边界)。
    ② `neckline.llm.budget` 预算三本账互不透支 + 降级次序定死。
    ③ 全仓守门:任一"真的调用 LLM"的模块必须 import `prompt_context`
      (`neckline/CLAUDE.md`「所有 LLM 调用点必须 import prompt_context.py」)。
    ④ key 不外泄:`GET /settings*` 全部响应 grep 断言不含 api_key 值。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List, NamedTuple

import pytest

from neckline.llm import budget
from neckline.llm import router


# ══════════════════════════════════════════════════════════════════════════
# router.py:任务常量 + 默认路由(纯函数)
# ══════════════════════════════════════════════════════════════════════════

class _Row(NamedTuple):
    name: str
    enabled: bool
    has_web_search: bool


def test_all_tasks_declared_count_and_uniqueness():
    """九项任务常量(plan 原文逐字对应),不许漏项也不许有重复字符串。"""
    assert len(router.ALL_TASKS) == 9
    assert len(set(router.ALL_TASKS)) == 9


def test_explicit_route_always_wins_even_if_provider_nonexistent():
    """路由是用户显式配置,配错了要如实反映——不悄悄跳过到默认值。"""
    name = router.resolve_task_provider_name(
        router.TASK_SCRIPT, routes={router.TASK_SCRIPT: "ghost"}, default_provider="deepseek",
        rows=[_Row("deepseek", True, False)],
    )
    assert name == "ghost"


def test_search_task_without_route_picks_first_enabled_has_web_search_row():
    rows = [_Row("deepseek", True, False), _Row("kimi-like", False, True), _Row("glm-like", True, True)]
    name = router.resolve_task_provider_name(
        router.TASK_DRIVER_SEARCH, routes={}, default_provider="deepseek", rows=rows,
    )
    assert name == "glm-like"  # kimi-like 虽 has_web_search 但 enabled=False,跳过


def test_search_task_without_any_has_web_search_row_falls_back_to_default():
    rows = [_Row("deepseek", True, False)]
    name = router.resolve_task_provider_name(
        router.TASK_NEWS_SCAN, routes={}, default_provider="deepseek", rows=rows,
    )
    assert name == "deepseek"


def test_non_search_task_without_route_uses_default_directly_ignoring_search_rows():
    rows = [_Row("glm-like", True, True)]
    name = router.resolve_task_provider_name(
        router.TASK_TIER_RANK, routes={}, default_provider="deepseek", rows=rows,
    )
    assert name == "deepseek"  # 非检索类,不看 has_web_search,直接回退默认


def test_task_none_falls_back_to_default():
    name = router.resolve_task_provider_name(None, routes={}, default_provider="deepseek", rows=[])
    assert name == "deepseek"


def test_unrecognized_task_string_degrades_to_default_not_crash():
    """垃圾输入(拼写错误的任务名)不崩、不神秘拦截——退化成"没有这个任务的路由",
    走默认 provider(§2.0/§3.8 全链路优雅降级铁律的纯函数层体现)。"""
    name = router.resolve_task_provider_name(
        "typo_task", routes={}, default_provider="deepseek", rows=[],
    )
    assert name == "deepseek"


# ══════════════════════════════════════════════════════════════════════════
# budget.py:预算三本账互不透支 + 降级次序定死
# ══════════════════════════════════════════════════════════════════════════

def test_three_ledgers_are_independent_by_default():
    ledger = budget.BudgetLedger()
    assert ledger.limits == {
        "search": budget.SEARCH_BUDGET_SECONDS,
        "reason": budget.REASON_BUDGET_SECONDS,
        "review": budget.REVIEW_BUDGET_SECONDS,
    }
    assert not ledger.exhausted("search")
    assert not ledger.exhausted("reason")
    assert not ledger.exhausted("review")


def test_search_budget_change_does_not_affect_other_two_ledgers(monkeypatch):
    """把检索预算调到 1s,断言推理与复盘预算不受影响(plan §五 V2-②「测试与守门」
    原文场景)。"""
    monkeypatch.setattr(budget, "SEARCH_BUDGET_SECONDS", 1.0)
    ledger = budget.BudgetLedger()
    assert ledger.limits["search"] == 1.0
    assert ledger.limits["reason"] == budget.REASON_BUDGET_SECONDS
    assert ledger.limits["review"] == budget.REVIEW_BUDGET_SECONDS

    ledger.spend("search", 1.0)
    assert ledger.exhausted("search")
    assert not ledger.exhausted("reason")   # 未被"借用"或"透支"
    assert not ledger.exhausted("review")
    assert ledger.remaining("reason") == budget.REASON_BUDGET_SECONDS
    assert ledger.remaining("review") == budget.REVIEW_BUDGET_SECONDS


def test_spend_never_goes_negative_remaining():
    ledger = budget.BudgetLedger()
    ledger.spend("search", ledger.limits["search"] * 10)  # 狂超支
    assert ledger.remaining("search") == 0.0
    assert ledger.exhausted("search")


def test_spend_unknown_ledger_raises():
    ledger = budget.BudgetLedger()
    with pytest.raises(ValueError):
        ledger.spend("bogus_ledger", 1.0)


def test_degrade_order_is_t3_brief_then_t2_review_detail():
    assert budget.DEGRADE_ORDER == (budget.DROP_T3_BRIEF, budget.DROP_T2_REVIEW_DETAIL)
    assert budget.next_to_drop([]) == budget.DROP_T3_BRIEF
    assert budget.next_to_drop([budget.DROP_T3_BRIEF]) == budget.DROP_T2_REVIEW_DETAIL
    assert budget.next_to_drop([budget.DROP_T3_BRIEF, budget.DROP_T2_REVIEW_DETAIL]) is None


def test_basket_card_freeze_and_discipline_never_in_degrade_order():
    """篮子卡冻结与纪律外壳永不被丢——机器判据:`NEVER_DROPPED` 里的每一项都不许
    出现在 `DEGRADE_ORDER` 里。"""
    for item in budget.NEVER_DROPPED:
        assert item not in budget.DEGRADE_ORDER


# ══════════════════════════════════════════════════════════════════════════
# 全仓守门:真的调用 LLM 的模块必须 import prompt_context
# ══════════════════════════════════════════════════════════════════════════

_NECKLINE_DIR = Path(__file__).resolve().parent.parent / "neckline"


def _calls_provider_chat(path: Path) -> bool:
    """AST 扫描:是否存在形如 `xxx.chat(...)` 的调用(`xxx` 名字不限,只要方法名
    是 `chat`——`provider.chat(...)` 这个调用点本身就是"真的在跟 LLM 说话"的
    唯一入口,`ChatMessage`/`chat completions` 等字面量提及不算)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "chat":
            return True
    return False


def _imports_prompt_context(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "prompt_context" in node.module:
            return True
        if isinstance(node, ast.Import) and any("prompt_context" in a.name for a in node.names):
            return True
    return False


# **已知缺口(如实登记,不在本块修复范围)**:`neckline/llm/news_scan.py::
# scan_news_for_code` 调用 `provider.chat(...)`(消息面立案/暴雷/监管扫描,
# v1.3-③-C4)但未 import `prompt_context`——早于本块存在、与本块改动无关。若
# 这里不豁免,下面这条新增的全仓守门会把这个既有缺口变成"本块新增的失败",
# 与任务纪律「不新增失败」冲突;如实登记 + 已 `spawn_task` 记账跟进,不在此处
# 顺手修(改 news_scan 的 system prompt 属于另一块工作量,不做静默无关改动)。
_GRANDFATHERED_MISSING_PROMPT_CONTEXT = ("neckline/llm/news_scan.py",)


def test_every_provider_chat_call_site_imports_prompt_context():
    offenders: List[str] = []
    for path in sorted(_NECKLINE_DIR.rglob("*.py")):
        rel = str(path.relative_to(_NECKLINE_DIR.parent))
        if rel in _GRANDFATHERED_MISSING_PROMPT_CONTEXT:
            continue
        if _calls_provider_chat(path) and not _imports_prompt_context(path):
            offenders.append(rel)
    assert not offenders, f"以下模块调用了 provider.chat(...) 但未 import prompt_context:{offenders}"


def test_grandfather_list_is_still_accurate():
    """豁免名单本身要被看住(同 `test_v2_schema_guard.py` 的"扫描范围不能漂"精神)
    ——如果哪天 `news_scan.py` 被修好了却忘了从豁免名单摘掉,这条测试会失败提醒。
    """
    for rel in _GRANDFATHERED_MISSING_PROMPT_CONTEXT:
        path = _NECKLINE_DIR.parent / rel
        assert path.exists(), f"豁免名单里的文件不存在,名单已过期:{rel}"
        assert _calls_provider_chat(path), f"{rel} 已不再调用 provider.chat(...),豁免项可以摘掉了"
        assert not _imports_prompt_context(path), f"{rel} 已经 import 了 prompt_context,豁免项可以摘掉了"


# ══════════════════════════════════════════════════════════════════════════
# key 不外泄:GET /settings* 全部响应不含 api_key 明文
# ══════════════════════════════════════════════════════════════════════════

_SECRET = "sk-guard-test-leaksentinel-12345"


def test_get_settings_family_never_leaks_provider_key(client, AUTH):
    r = client.post("/api/v1/settings/providers", headers=AUTH, json={
        "name": "leak-probe", "baseUrl": "https://x.invalid", "model": "m",
        "apiKey": _SECRET, "hasWebSearch": True, "searchEngine": "search_pro",
        "notes": "probe row for key-leak guard test",
    })
    assert r.status_code == 201
    assert _SECRET not in r.text  # 创建响应本身也不能回传明文

    for path in (
        "/api/v1/settings",
        "/api/v1/settings/providers",
        "/api/v1/settings/llm-routes",
    ):
        resp = client.get(path, headers=AUTH)
        assert resp.status_code == 200, path
        assert _SECRET not in resp.text, f"{path} 响应里出现了明文 key"
