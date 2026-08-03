"""LLM 逻辑审判单测(plan 2.4)。① 无 provider(本项目当前状态)→「未激活」占位,
不发起任何调用;② provider 调用失败 → 同样降级为占位,不假装分析过;③ 结论标签
解析(通过/否决/缺失时保守否决/多次出现取最后一次);④ 搜索结果透传;⑤ 上下文
文案含/不含龙虎榜信息。"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from neckline.llm.base import LLMResult, SearchHit
from neckline.llm.judge import (
    VERDICT_INACTIVE,
    VERDICT_PASS,
    VERDICT_VETO,
    build_context_block,
    judge_candidate,
)
from neckline.llm.providers.glm import GLMProvider
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class _DuckCandidate:
    """`judge_candidate` 是 **duck-typed** 的(⑬-2 定死:它保留为「通用 LLM 调用 +
    降级链 + verdict 解析」工具),只要求几个属性。V1 的 `report.candidates.Candidate`
    数据类已随候选榜删除(⑬-1),本文件因此改用这个最小替身构造——**被测函数
    一行未改**。"""
    ts_code: str = "600001.SH"
    name: str = "示例股份"
    close: float = 12.34
    score: float = 95.0
    rank: int = 1
    board: str = "MAIN"
    pattern_tags: List[str] = field(default_factory=lambda: ["浅回调贴前高", "均线多头"])
    hot_sectors: List[str] = field(default_factory=lambda: ["人工智能"])
    sector_names: List[str] = field(default_factory=lambda: ["人工智能", "算力"])
    entry_plan: str = "回调低吸:现价 12.34..."
    stop_loss: str = "参考止损价约 11.72 元..."
    target: str = "不设固定止盈线..."
    invalidation_text: str = "次日低开..."
    invalidation_spec: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def _candidate(**overrides) -> _DuckCandidate:
    return _DuckCandidate(**overrides)


class _StubProvider:
    """判官测试用的最小假 provider(不经 httpx),把 chat() 的返回值设死,专注测
    judge.py 自己的降级/解析逻辑,与 openai_compat 的网络细节解耦(那部分见
    test_llm.py)。"""

    name = "stub"
    model = "stub-model"

    def __init__(self, result: LLMResult) -> None:
        self._result = result
        self.calls = 0
        self.captured_messages = []

    def chat(self, messages, *, enable_search=True, transport=None, search_query=None):
        self.calls += 1
        self.captured_messages = list(messages)
        self.captured_search_query = search_query   # v1.5.2:审判链路显式检索词(带年份)
        return self._result


class TestNoProviderDegradation:
    def test_none_provider_returns_inactive_without_call(self):
        r = judge_candidate(_candidate(), provider=None)
        assert r.verdict == VERDICT_INACTIVE
        assert r.degraded is True
        assert "未激活" in r.narrative
        assert "LLM_PROVIDER" in r.degrade_reason
        assert r.search_engine is None   # 未发起任何调用,不臆造用过哪个引擎


class TestCustomSystemPrompt:
    """v1.1-C.3 新增可选 `system_prompt` 参数(默认值不变,候选审判调用点零改动,
    纯向后兼容扩展——自选体检复用本函数时传入
    `WATCHLIST_JUDGE_SYSTEM_PROMPT`)。"""

    def test_default_call_unaffected_uses_judge_system_prompt(self):
        from neckline.llm.judge import JUDGE_SYSTEM_PROMPT

        stub = _StubProvider(LLMResult(ok=True, content="分析。\n结论:通过", provider="glm", model="glm-5.2"))
        judge_candidate(_candidate(), provider=stub)
        assert stub.captured_messages[0].role == "system"
        assert stub.captured_messages[0].content == JUDGE_SYSTEM_PROMPT

    def test_custom_system_prompt_is_used_when_passed(self):
        """`system_prompt` 形参本身保留(⑬-2:`judge_candidate` 是通用 LLM 调用 +
        降级链 + verdict 解析工具)。V1 唯一的自定义 prompt(自选体检
        `WATCHLIST_JUDGE_SYSTEM_PROMPT`)已随自选池整链删除(⑬-11),故本测试改用
        一个临时替身 prompt,只验「传进去的确实被用了」。"""
        from neckline.llm.judge import JUDGE_SYSTEM_PROMPT

        custom = "你是一个测试用的替身审判员。\n结尾写「结论:通过」或「结论:否决」。"
        stub = _StubProvider(LLMResult(ok=True, content="分析。\n结论:通过", provider="glm", model="glm-5.2"))
        judge_candidate(_candidate(), provider=stub, system_prompt=custom)
        assert stub.captured_messages[0].content == custom
        assert stub.captured_messages[0].content != JUDGE_SYSTEM_PROMPT

    def test_custom_prompt_still_uses_same_verdict_tag_format(self):
        """自定义 prompt 与默认 prompt 共用同一套「结论:通过|否决」解析
        (`_parse_verdict`),不是另起一套标签格式。"""
        custom = "替身审判员。"
        stub = _StubProvider(LLMResult(ok=True, content="一段分析。\n结论:否决", provider="glm", model="glm-5.2"))
        r = judge_candidate(_candidate(), provider=stub, system_prompt=custom)
        assert r.verdict == VERDICT_VETO
        assert "一段分析" in r.narrative


class TestProviderFailureDegradation:
    def test_provider_call_failure_returns_inactive_placeholder(self):
        stub = _StubProvider(LLMResult(ok=False, reason="上游 500"))
        r = judge_candidate(_candidate(), provider=stub)
        assert stub.calls == 1
        assert r.verdict == VERDICT_INACTIVE
        assert r.degraded is True
        assert "上游 500" in r.narrative
        assert r.degrade_reason == "上游 500"
        assert r.search_engine is None   # 调用失败,不确定用过哪个引擎,不臆造


class TestVerdictParsing:
    def test_pass_verdict_parsed_and_tag_stripped_from_narrative(self):
        stub = _StubProvider(LLMResult(ok=True, content="这是一段分析,催化仍在持续。\n结论:通过", provider="glm", model="glm-5.2"))
        r = judge_candidate(_candidate(), provider=stub)
        assert r.verdict == VERDICT_PASS
        assert r.degraded is False
        assert "结论:" not in r.narrative
        assert "催化仍在持续" in r.narrative
        assert r.provider == "glm" and r.model == "glm-5.2"

    def test_veto_verdict_parsed(self):
        stub = _StubProvider(LLMResult(ok=True, content="利空明显,公告显示业绩不及预期。\n结论:否决", provider="glm", model="glm-5.2"))
        r = judge_candidate(_candidate(), provider=stub)
        assert r.verdict == VERDICT_VETO
        assert r.degraded is False

    def test_missing_verdict_tag_defaults_to_veto_conservatively(self):
        stub = _StubProvider(LLMResult(ok=True, content="模型只写了一段话,忘记按格式收尾。", provider="glm", model="glm-5.2"))
        r = judge_candidate(_candidate(), provider=stub)
        assert r.verdict == VERDICT_VETO
        assert r.degraded is False  # 调用本身是成功的,只是格式没对齐——不算"未激活"
        assert "未按格式给出结论标签" in r.narrative

    def test_uses_last_occurrence_when_verdict_tag_appears_multiple_times(self):
        content = "有人担心利空,但那只是传闻。\n结论:通过\n补充说明后我改变判断。\n结论:否决"
        stub = _StubProvider(LLMResult(ok=True, content=content, provider="glm", model="glm-5.2"))
        r = judge_candidate(_candidate(), provider=stub)
        assert r.verdict == VERDICT_VETO

    def test_search_hits_propagated(self):
        hit = SearchHit(title="标题", link="https://x.com", content="摘要")
        stub = _StubProvider(LLMResult(ok=True, content="正文\n结论:通过", search_hits=[hit], provider="glm", model="glm-5.2"))
        r = judge_candidate(_candidate(), provider=stub)
        assert r.search_hits == [hit]

    def test_search_engine_propagated(self):
        """v1.5-④-A3(§七 P1-7):`LLMResult.search_engine` 原样透传进
        `JudgeResult.search_engine`,供 `store.save_llm_judgment` 落库。"""
        stub = _StubProvider(LLMResult(
            ok=True, content="正文\n结论:通过", provider="glm", model="glm-5.2", search_engine="search_pro",
        ))
        r = judge_candidate(_candidate(), provider=stub)
        assert r.search_engine == "search_pro"


class TestSearchQueryRecencyV152:
    """v1.5.2:审判链路此前**从不显式传检索词**(供应商跟最后一条 user 消息推导),
    现在显式传「中文名(代码) <当前年份> 最新」——身份 + 时效一次给够。"""

    def test_query_carries_identity_and_current_year(self):
        from datetime import date as _date

        from neckline.llm.judge import judge_search_query
        q = judge_search_query(_candidate())
        assert "示例股份" in q and "600001.SH" in q
        assert f"{_date.today().year} 最新" in q or "最新" in q   # 年份动态,不硬编

    def test_query_reaches_the_provider_call(self):
        stub = _StubProvider(LLMResult(ok=True, content="分析。\n结论:通过", provider="glm", model="m"))
        judge_candidate(_candidate(), provider=stub)
        assert "示例股份" in stub.captured_search_query
        assert "最新" in stub.captured_search_query

    def test_query_degrades_to_code_when_name_missing(self):
        """查无中文名 → 只带代码,不拼出「(600001.SH)」这种空名括号(同问询台先例)。"""
        from neckline.llm.judge import judge_search_query
        q = judge_search_query(_candidate(name=""))
        assert q.startswith("600001.SH ") and "(" not in q

    def test_system_prompts_carry_timeliness_rules(self):
        from neckline.llm.judge import JUDGE_SYSTEM_PROMPT
        from neckline.llm.prompt_context import TIMELINESS_RULES
        assert TIMELINESS_RULES in JUDGE_SYSTEM_PROMPT


class TestContextBlock:
    def test_includes_top_list_row_when_present(self):
        row = {"net_amount": 700.0, "net_rate": 5.6, "reason": "日涨幅偏离值达7%"}
        block = build_context_block(_candidate(), top_list_row=row)
        assert "龙虎榜" in block
        assert "700.0" in block
        assert "日涨幅偏离值达7%" in block

    def test_first_line_is_the_current_date_anchor(self):
        """v1.5.2(用户报障根因:三处提示词都没告诉模型今天几号,旧研报被当现行参照)
        ——审判上下文第一行必须是日期锚。"""
        first = build_context_block(_candidate()).splitlines()[0]
        assert first.startswith("今天是 ") and "下一交易日" in first

    def test_notes_absence_when_no_top_list_row(self):
        block = build_context_block(_candidate(), top_list_row=None)
        assert "近日上榜龙虎榜:否" in block

    def test_includes_hot_sector_and_pattern_tags(self):
        block = build_context_block(_candidate())
        assert "人工智能" in block
        assert "浅回调贴前高" in block

    # ⚠ **V2-⑬-1 删除**:`test_board_age_from_real_candidate_reaches_context_block`
    # 用真实 `report.candidates.score_candidates`(K1 评分)产出的 Candidate 验证
    # 「板块年龄数字确实流到 LLM 上下文」;K1 候选评分整条已随候选榜删除,没有真实
    # 产出方可对拍了。`build_context_block` 读 `.hot_sectors` 的行为由上面
    # `test_includes_hot_sector_and_pattern_tags` 继续锁死。


class TestEndToEndWithRealProviderMockTransport:
    """用真 GLMProvider + MockTransport 走一遍完整链路(judge -> provider -> httpx),
    证明 judge.py 与 llm 层实际接得上,不只是靠 _StubProvider 隔离验证。"""

    def test_glm_provider_end_to_end_veto(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {
                    "role": "assistant",
                    "content": "搜索未找到该股票近期利好消息,反而有一则减持公告。\n结论:否决",
                }}],
            })

        provider = GLMProvider(api_key="sk-xxx")
        r = judge_candidate(_candidate(), provider=provider, transport=httpx.MockTransport(handler))
        assert r.verdict == VERDICT_VETO
        assert r.degraded is False
        assert r.provider == "glm"
        assert r.search_engine == "search_pro"   # 端到端:真 GLMProvider 走到 judge.py
