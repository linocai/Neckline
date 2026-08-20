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
from neckline.llm.base import LLMResult


# ══════════════════════════════════════════════════════════════════════════
# router.py:任务常量 + 默认路由(纯函数)
# ══════════════════════════════════════════════════════════════════════════

class _Row(NamedTuple):
    name: str
    enabled: bool
    has_web_search: bool


def test_all_tasks_declared_count_and_uniqueness():
    """plan 原文九项任务常量,**V2.1-① 起 `TASK_INQUIRY` 退役 → 八项**(问询台整链
    退役,§五①原文四处摘除清单之一);**V2.3.3-③ 新增 `TASK_AUCTION` → 九项**
    (D1 集合竞价确认层,出处 K8.md **§二十**:9:26—9:29 一次调用覆盖全部篮子)。
    不许漏项也不许有重复字符串。

    **V2.5.0 S9/S10 新增 `TASK_EXPLAIN` / `TASK_PLAYBOOK` → 十三项**(架构 §八
    「LLM 的三个岗位」里的后两个;第一个「方向解读」在事实层,本版未建)。
    三处都想过了:① 预算账 —— 逐票调用、上下文很小;② 流式分级 —— **不进**
    `LONG_CONTEXT_TASKS`(不开流式 + 基类 90s 读超时,两项同路);
    ③ prompt_context —— 两条 system prompt 都嵌了时效纪律,
    已加进 `test_prompt_context.py` 的清单。

    ⛔ **别改成 `>= 8`** —— 这条闸的意义全在"加不进来":新增一个 LLM 任务是要
    连预算账、流式分级、prompt_context 三处一起想清楚的事,不是顺手加个字符串。"""
    assert len(router.ALL_TASKS) == 13
    assert len(set(router.ALL_TASKS)) == 13
    assert router.TASK_EXPLAIN in router.ALL_TASKS
    assert router.TASK_PLAYBOOK in router.ALL_TASKS
    # 🔴 两个新岗位**都不开流式、都不联网**(见上;各自的理由写在 router.py)。
    for t in (router.TASK_EXPLAIN, router.TASK_PLAYBOOK):
        assert t not in router.LONG_CONTEXT_TASKS
        assert router.use_streaming_for_task(t) is False
        assert t not in router.DEFAULT_SEARCH_TASKS
    assert router.TASK_AUCTION in router.ALL_TASKS
    assert router.TASK_DIRECTION_TRIAGE in router.ALL_TASKS
    assert router.TASK_DEEP_REASON in router.ALL_TASKS
    assert router.SELECTION_PIPELINE_TASKS == (
        router.TASK_DRIVER_SEARCH, router.TASK_DIRECTION_TRIAGE, router.TASK_DEEP_REASON,
    )
    assert router.TASK_TIER_RANK not in router.SELECTION_PIPELINE_TASKS
    assert router.TASK_SCRIPT not in router.SELECTION_PIPELINE_TASKS
    # 🔴 两项**同路接线**的正面断言(只接一半 = §七 P0-40/P0-44 原病复发):
    assert router.TASK_AUCTION in router.LONG_CONTEXT_TASKS
    assert router.use_streaming_for_task(router.TASK_AUCTION) is True
    assert router.read_timeout_for_task(router.TASK_AUCTION) == \
        router.STREAM_CHUNK_GAP_TIMEOUT_SECONDS


def test_explicit_route_always_wins_even_if_provider_nonexistent():
    """路由是用户显式配置,配错了要如实反映——不悄悄跳过到默认值。"""
    name = router.resolve_task_provider_name(
        router.TASK_SCRIPT, routes={router.TASK_SCRIPT: "ghost"}, default_provider="deepseek",
        rows=[_Row("deepseek", True, False)],
    )
    assert name == "ghost"


def test_search_task_without_route_uses_default_provider_not_legacy_search_flag():
    rows = [_Row("deepseek", True, False), _Row("kimi-like", False, True), _Row("glm-like", True, True)]
    name = router.resolve_task_provider_name(
        router.TASK_DRIVER_SEARCH, routes={}, default_provider="deepseek", rows=rows,
    )
    assert name == "deepseek"


def test_search_task_without_legacy_search_row_still_uses_default():
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


def test_read_timeout_grading_is_by_task_class_and_defaults_to_none():
    """§七 P0-40 → P0-44:`None` = **不覆盖**(用 provider 类属性 90.0),与"分级后
    恰好等于 90.0"分得开 —— 前者让 provider 子类保留自己的默认值,后者会把它按死。

    ⚠ 长上下文那一档的数字**在 P0-44 后又回到 90.0,但语义完全不同**:流式下它是
    **chunk 间隔**上限,不是整段墙钟。别看见 90 就以为回退了。"""
    for t in router.LONG_CONTEXT_TASKS:
        assert router.read_timeout_for_task(t) == router.STREAM_CHUNK_GAP_TIMEOUT_SECONDS
    for t in router.DEFAULT_SEARCH_TASKS:
        assert router.read_timeout_for_task(t) is None
    assert router.read_timeout_for_task(None) is None
    assert router.read_timeout_for_task("some_future_task") is None


def test_streaming_is_switched_on_for_exactly_the_long_context_set():
    """§七 P0-44:流式与 chunk 间隔超时是**同一个判据的两半**,必须同进同出 ——
    只开一半 = 语义与数字对不上(给非流式任务配 chunk 间隔的数字 = 悄悄变成 90s
    整段墙钟 = P0-40 原病复发)。"""
    for t in router.ALL_TASKS:
        assert router.use_streaming_for_task(t) is (t in router.LONG_CONTEXT_TASKS)
        assert router.use_streaming_for_task(t) is (router.read_timeout_for_task(t) is not None)
    assert router.use_streaming_for_task(None) is False
    assert router.use_streaming_for_task("some_future_task") is False


def test_the_disproven_fixed_wall_constant_is_gone():
    """⛔ P0-40 的 `LONG_CONTEXT_READ_TIMEOUT_SECONDS=240.0` **已被生产证伪并删除**
    (当晚 3/3 次精确撞满 240s)。留着它 = 后人会"顺手"再抬一次那个数字,重走死路;
    这条把"已证伪的常量不许复活"变成机器可查。"""
    assert not hasattr(router, "LONG_CONTEXT_READ_TIMEOUT_SECONDS")


def test_long_context_and_search_task_sets_never_overlap():
    """⛔ 一个任务不能同时"走流式"又"是检索类" —— `web_search` tools 协议 × 流式
    是本项目从未验证过的组合(v1.3.4 案底:坏起来是静默的);真出现这种任务,
    先回 planner 定口径。"""
    assert not (set(router.LONG_CONTEXT_TASKS) & set(router.DEFAULT_SEARCH_TASKS))
    assert set(router.LONG_CONTEXT_TASKS) <= set(router.ALL_TASKS)


def test_worst_case_streaming_generation_fits_inside_the_reason_budget():
    """算术守门:**流式下单次调用的墙钟没有固定上限**(生成多长都合法,这正是
    P0-44 要治的),故这里守的是给它留的**悲观额度**必须仍在推理账之内 —— 否则
    一次慢调用就能把整本账吃穿,后面的 Tier/剧本全成 `budget_exhausted`。"""
    allowance = router.STREAM_GENERATION_BUDGET_ALLOWANCE_SECONDS
    assert allowance < budget.REASON_BUDGET_SECONDS
    assert allowance < budget.REVIEW_BUDGET_SECONDS
    # 悲观额度必须**明显宽于**已实测过的最慢一次(中午 173s;晚高峰更慢)。
    assert allowance >= 173.0 * 3, "额度没给够,遇上比中午慢三倍的晚高峰就会低估"


def test_basket_unit_has_no_aggregate_wall_timeout():
    """晚间选股不再因累计用时被 systemd 截断；单次调用仍有 provider 保险丝。"""
    unit = (Path(__file__).resolve().parent.parent / "deploy" / "neckline-basket.service").read_text(
        encoding="utf-8")
    assert "TimeoutStartSec=infinity" in unit
    assert "TimeoutStartSec=5400" not in unit


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


def test_record_call_uses_only_actual_provider_tokens_and_wall_time():
    ledger = budget.BudgetLedger(token_limits={"search": 50, "reason": 100, "review": None})
    result = LLMResult(ok=True, total_tokens=24, prompt_tokens=10, completion_tokens=14,
                       usage_unavailable=False)
    assert ledger.record_call("reason", result, 1.25) is True
    assert ledger.spent["reason"] == pytest.approx(1.25)
    assert ledger.token_spent["reason"] == 24
    assert ledger.token_remaining("reason") == 76
    assert ledger.token_exhausted("reason") is False


def test_record_call_marks_missing_usage_without_estimating_tokens():
    ledger = budget.BudgetLedger()
    assert ledger.record_call("reason", LLMResult(ok=True), 0.5) is False
    assert ledger.usage_unavailable["reason"] is True
    assert ledger.token_spent["reason"] == 0


def test_degrade_order_is_only_t2_review_detail():
    """V2.1-②:T3 全链退役后可丢清单**只剩一项**(由「T3 简评 → T2 细节」两项收窄)。"""
    assert budget.DEGRADE_ORDER == (budget.DROP_T2_REVIEW_DETAIL,)
    assert budget.next_to_drop([]) == budget.DROP_T2_REVIEW_DETAIL
    assert budget.next_to_drop([budget.DROP_T2_REVIEW_DETAIL]) is None


def test_drop_t3_brief_is_retired():
    """**反向守门(防复活)**:`DROP_T3_BRIEF` 常量必须不存在 —— 同 P0-44 删
    `LONG_CONTEXT_READ_TIMEOUT_SECONDS` 的 `hasattr` 体例。V2.1-② 裁定「T3 彻底删除,
    不留影子档」,留一个常量在那儿迟早会有人把它接回 `DEGRADE_ORDER`。"""
    assert not hasattr(budget, "DROP_T3_BRIEF")
    assert "DROP_T3_BRIEF" not in budget.__all__
    assert not any("t3" in item.lower() for item in budget.DEGRADE_ORDER)


def test_basket_card_freeze_and_discipline_never_in_degrade_order():
    """篮子卡冻结与纪律外壳永不被丢——机器判据:`NEVER_DROPPED` 里的每一项都不许
    出现在 `DEGRADE_ORDER` 里。"""
    for item in budget.NEVER_DROPPED:
        assert item not in budget.DEGRADE_ORDER


# ══════════════════════════════════════════════════════════════════════════
# 全仓守门:真的调用 LLM 的模块必须 import prompt_context
# ══════════════════════════════════════════════════════════════════════════

from tests import guard_scan

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
    """这个文件 import 了 `prompt_context` 吗。

    🔴 判据的**唯一实现**在 `guard_scan.imports()`(V2.5.0 收敛)。本文件从前自带
    一份抄本,它的 `ImportFrom` 分支要求 `node.module` 非空 —— 对
    `from . import prompt_context`(`node.module is None`)**全盲**:一个真的
    接上了 `prompt_context` 的模块会被判成没接,而**反向**那条
    `test_grandfather_list_is_still_accurate` 又会因此判错。⛔ 不许再抄回来。
    """
    return any("prompt_context" in mod for mod in guard_scan.imports(path))


# **豁免名单已清空(2026-08-04,A4)**:唯一一条 `neckline/llm/news_scan.py`
# (消息面立案/暴雷/监管扫描,v1.3-③-C4)已按 `judge.py` 姿势接上 `prompt_context`
# (system prompt 内嵌 `TIMELINESS_RULES` + user 首行日期锚 + 显式检索词),欠账销账。
# ⚠ 名单**保留但为空**:下面 `test_grandfather_list_is_still_accurate` 是它的反向
# 守门(登记了却已修好会挂),将来真要再豁免一条,得连着理由一起写进来。
_GRANDFATHERED_MISSING_PROMPT_CONTEXT: tuple = ()


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


def test_the_prompt_context_scanner_is_not_blind_to_relative_imports(tmp_path: Path):
    """🔴 **收敛后的反例自检**(V2.5.0)。

    本文件从前自带一份抄本,它的 `ImportFrom` 分支要求 `node.module` 非空 ——
    `from . import prompt_context` 的 `node.module` 是 `None`,于是一个**真的**
    接上了 `prompt_context` 的模块会被判成没接;而反向那条
    `test_grandfather_list_is_still_accurate` 又会因此判错。两个方向都会错。
    ⚠ 诱饵必须放在**真包**里(`guard_scan` 靠 `__init__.py` 认包边界)。
    """
    for rel in ("neckline", "neckline/llm"):
        d = tmp_path / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text("", encoding="utf-8")
    bait = tmp_path / "neckline" / "llm" / "judge.py"
    bait.write_text("from . import prompt_context\n", encoding="utf-8")
    assert _imports_prompt_context(bait), (
        "`from . import prompt_context` 没被看见 —— 扫描器对相对 import 瞎了")
    bait2 = tmp_path / "neckline" / "llm" / "news_scan.py"
    bait2.write_text("from ..llm.prompt_context import TIMELINESS_RULES\n", encoding="utf-8")
    assert _imports_prompt_context(bait2)
    clean = tmp_path / "neckline" / "llm" / "factory.py"
    clean.write_text("from . import router\nimport json\n", encoding="utf-8")
    assert not _imports_prompt_context(clean)
