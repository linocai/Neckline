"""消息面扫描 LLM 调用单测(plan §五 v1.3-③-C4)。锁死:① 无 provider(缺 key)
→「未激活」占位,不发起任何调用;② provider 调用失败 → 同样降级,不假装扫描过;
③ 结论标签解析(未发现/单类命中/多类命中/多次出现取全部命中〔与 judge.py"取最后
一次"不同,消息面场景多个类别本就该并存,不是互斥的单一结论〕);④ 格式缺失 →
`degraded=True`(不是"确认无消息",也不是硬造某个类别命中);⑤ 与真 GLMProvider +
MockTransport 端到端。"""

from __future__ import annotations

import httpx
import pytest

from neckline.llm.base import LLMResult
from neckline.llm.news_scan import (
    CATEGORY_BLOWUP,
    CATEGORY_INVESTIGATION,
    CATEGORY_REGULATORY,
    NEWS_SCAN_SYSTEM_PROMPT,
    scan_news_for_code,
)
from neckline.llm.providers.glm import GLMProvider


class _StubProvider:
    name = "stub"
    model = "stub-model"

    def __init__(self, result: LLMResult) -> None:
        self._result = result
        self.calls = 0
        self.captured_messages = []

    def chat(self, messages, *, enable_search=True, transport=None):
        self.calls += 1
        self.captured_messages = list(messages)
        return self._result


class TestNoProviderDegradation:
    def test_none_provider_returns_degraded_without_call(self):
        r = scan_news_for_code("600001.SH", "示例甲", provider=None)
        assert r.degraded is True
        assert r.hits == []
        assert "LLM_PROVIDER" in r.degrade_reason
        assert "未扫描" in r.narrative or "未激活" in r.narrative


class TestSystemPromptWiring:
    def test_uses_news_scan_system_prompt(self):
        stub = _StubProvider(LLMResult(ok=True, content="一切正常。\n结论:未发现", provider="glm", model="glm-5.2"))
        scan_news_for_code("600001.SH", "示例甲", provider=stub)
        assert stub.captured_messages[0].role == "system"
        assert stub.captured_messages[0].content == NEWS_SCAN_SYSTEM_PROMPT

    def test_user_message_includes_code_and_name(self):
        stub = _StubProvider(LLMResult(ok=True, content="正文\n结论:未发现", provider="glm", model="glm-5.2"))
        scan_news_for_code("600001.SH", "示例甲", provider=stub)
        assert "示例甲" in stub.captured_messages[1].content
        assert "600001.SH" in stub.captured_messages[1].content


class TestProviderFailureDegradation:
    def test_call_failure_returns_degraded_placeholder(self):
        stub = _StubProvider(LLMResult(ok=False, reason="上游 500"))
        r = scan_news_for_code("600001.SH", "示例甲", provider=stub)
        assert stub.calls == 1
        assert r.degraded is True
        assert r.degrade_reason == "上游 500"
        assert r.hits == []


class TestVerdictParsing:
    def test_none_found_yields_empty_hits_not_degraded(self):
        stub = _StubProvider(LLMResult(ok=True, content="搜了一圈没有异常。\n结论:未发现", provider="glm", model="glm-5.2"))
        r = scan_news_for_code("600001.SH", "示例甲", provider=stub)
        assert r.degraded is False
        assert r.hits == []

    def test_single_category_hit_parsed(self):
        content = "查到一则减持公告以外,还有一则交易所监管函。\n结论-监管:收到交易所监管函,要求说明关联交易事项。"
        stub = _StubProvider(LLMResult(ok=True, content=content, provider="glm", model="glm-5.2"))
        r = scan_news_for_code("600001.SH", "示例甲", provider=stub)
        assert r.degraded is False
        assert r.hits == [(CATEGORY_REGULATORY, "收到交易所监管函,要求说明关联交易事项。")]

    def test_multiple_category_hits_all_parsed(self):
        content = (
            "情况比较严重。\n"
            "结论-立案:因涉嫌信息披露违规被证监会立案调查。\n"
            "结论-暴雷:年报审计意见被出具无法表示意见。"
        )
        stub = _StubProvider(LLMResult(ok=True, content=content, provider="glm", model="glm-5.2"))
        r = scan_news_for_code("600001.SH", "示例甲", provider=stub)
        assert r.degraded is False
        codes = {c for c, _ in r.hits}
        assert codes == {CATEGORY_INVESTIGATION, CATEGORY_BLOWUP}

    def test_missing_format_is_degraded_not_confirmed_clean(self):
        """§硬要求「没扫到 vs 扫了没有必须能区分」的反面情形:模型没按格式收尾时,
        既不能假装"确认无消息"(hits=[]非degraded),也不能硬造一个不知道具体是
        哪类的命中——按 degraded 处理,原文保留供人工复核。"""
        stub = _StubProvider(LLMResult(ok=True, content="模型只写了一段话,忘记按格式收尾。", provider="glm", model="glm-5.2"))
        r = scan_news_for_code("600001.SH", "示例甲", provider=stub)
        assert r.degraded is True
        assert r.degrade_reason == "模型未按格式给出结论标签"
        assert r.hits == []
        assert "忘记按格式收尾" in r.narrative

    def test_tag_stripped_narrative_still_contains_full_content(self):
        """本模块的收尾标签不像 judge.py 那样从叙述中剥离(消息面摘要本身就在
        标签行里,叙述与标签并存不冲突,§2.7 边界见模块 docstring)。"""
        content = "分析正文。\n结论-暴雷:财务数据存在重大疑点。"
        stub = _StubProvider(LLMResult(ok=True, content=content, provider="glm", model="glm-5.2"))
        r = scan_news_for_code("600001.SH", "示例甲", provider=stub)
        assert "分析正文" in r.narrative


class TestEndToEndWithRealProviderMockTransport:
    def test_glm_provider_end_to_end_hit(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {
                    "role": "assistant",
                    "content": "搜索发现该公司股东有减持计划公告以外的消息,交易所对其下发了问询函。\n结论-监管:交易所下发问询函,要求说明经营异常波动原因。",
                }}],
            })

        provider = GLMProvider(api_key="sk-xxx")
        r = scan_news_for_code("600001.SH", "示例甲", provider=provider, transport=httpx.MockTransport(handler))
        assert r.degraded is False
        assert r.provider == "glm"
        assert r.hits[0][0] == CATEGORY_REGULATORY

    def test_glm_provider_end_to_end_none_found(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {
                    "role": "assistant", "content": "未搜到相关消息。\n结论:未发现",
                }}],
            })

        provider = GLMProvider(api_key="sk-xxx")
        r = scan_news_for_code("600001.SH", "示例甲", provider=provider, transport=httpx.MockTransport(handler))
        assert r.degraded is False
        assert r.hits == []
