"""LLM 供应商抽象层单测(plan 2.4/§3.4)。姿势沿用 LinoN `test_llm.py` 的
`httpx.MockTransport` 免联网套路,覆盖:① 有 key 成功路径(GLM 顶层 web_search /
Kimi `$web_search` 工具调用回合);② 无 key / 无 provider;③ 超时重试后降级;
④ 非法响应(非200 / 非法JSON / 结构缺字段 / 空内容);⑤ 工具调用轮数封顶。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List

import httpx
import pytest

from neckline.config import Settings
from neckline.llm.base import ChatMessage
from neckline.llm.factory import get_provider
from neckline.llm.providers.glm import GLMProvider
from neckline.llm.providers.kimi import KimiProvider


def _openai_success_body(content: str, model: str = "glm-5.2") -> Dict[str, Any]:
    return {
        "id": "abc",
        "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
    }


class TestChatMessageWireFormat:
    def test_to_api_filters_none_fields(self):
        m = ChatMessage(role="user", content="hi")
        assert m.to_api() == {"role": "user", "content": "hi"}

    def test_to_api_includes_tool_fields_when_present(self):
        m = ChatMessage(role="tool", content="{}", tool_call_id="call_1", name="$web_search")
        api = m.to_api()
        assert api["tool_call_id"] == "call_1"
        assert api["name"] == "$web_search"


class TestFactory:
    def _settings(self, provider, key) -> Settings:
        return Settings(tushare_token=None, llm_provider=provider, llm_api_key=key)

    def test_no_provider_configured_returns_none(self):
        assert get_provider(self._settings(None, None)) is None

    def test_provider_without_key_returns_none(self):
        assert get_provider(self._settings("glm", None)) is None

    def test_key_without_provider_name_returns_none(self):
        assert get_provider(self._settings(None, "sk-xxx")) is None

    def test_unknown_provider_name_returns_none(self):
        assert get_provider(self._settings("deepseek", "sk-xxx")) is None

    def test_glm_provider_selected_case_insensitive(self):
        p = get_provider(self._settings("GLM", "sk-xxx"))
        assert isinstance(p, GLMProvider)
        assert p.model == "glm-5.2"

    def test_kimi_provider_selected(self):
        p = get_provider(self._settings("kimi", "sk-yyy"))
        assert isinstance(p, KimiProvider)
        assert p.model == "kimi-k3"

    def test_current_env_has_no_llm_key_get_provider_is_none(self):
        """本项目现状(.env 只有 TUSHARE_TOKEN)下,真实 settings 必须解析为 None——
        这是阶段2 铁律"全链路必须在无 key 下优雅降级跑通"的直接断言。"""
        from neckline.config import settings as real_settings

        assert get_provider(real_settings) is None


class TestOpenAICompatSharedDegradation:
    def test_missing_api_key_returns_degraded_without_network(self):
        called = {"n": 0}

        def handler(request):
            called["n"] += 1
            raise AssertionError("不应发起网络请求")

        p = GLMProvider(api_key=None)
        result = p.chat([ChatMessage(role="user", content="hi")], transport=httpx.MockTransport(handler))
        assert result.ok is False
        assert "API key" in result.reason
        assert called["n"] == 0

    def test_httpx_not_installed_degrades(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "httpx", None)
        p = GLMProvider(api_key="sk-xxx")
        result = p.chat([ChatMessage(role="user", content="hi")])
        assert result.ok is False
        assert "httpx" in result.reason

    def test_timeout_retries_then_degrades(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            raise httpx.ConnectTimeout("timeout", request=request)

        p = GLMProvider(api_key="sk-xxx")
        p.max_attempts = 2  # 缩短测试等待,仍验证"重试 N 次后降级"路径
        result = p.chat([ChatMessage(role="user", content="hi")], transport=httpx.MockTransport(handler))
        assert result.ok is False
        assert calls["n"] == 2
        assert "调用异常" in result.reason

    def test_non_200_status_degrades(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(500, json={"error": "boom"}))
        p = GLMProvider(api_key="sk-xxx")
        result = p.chat([ChatMessage(role="user", content="hi")], transport=transport)
        assert result.ok is False
        assert "500" in result.reason

    def test_invalid_json_body_degrades(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200, content=b"not json at all"))
        p = GLMProvider(api_key="sk-xxx")
        result = p.chat([ChatMessage(role="user", content="hi")], transport=transport)
        assert result.ok is False
        assert "响应解析异常" in result.reason

    def test_missing_choices_field_degrades(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "x"}))
        p = GLMProvider(api_key="sk-xxx")
        result = p.chat([ChatMessage(role="user", content="hi")], transport=transport)
        assert result.ok is False
        assert "响应结构异常" in result.reason

    def test_empty_content_degrades(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_openai_success_body("   ")))
        p = GLMProvider(api_key="sk-xxx")
        result = p.chat([ChatMessage(role="user", content="hi")], transport=transport)
        assert result.ok is False
        assert "为空" in result.reason

    def test_tool_round_limit_exceeded_degrades(self):
        def handler(request):
            body = {
                "id": "x",
                "model": "kimi-k3",
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_1", "type": "function",
                            "function": {"name": "$web_search", "arguments": '{"query":"x"}'},
                        }],
                    },
                }],
            }
            return httpx.Response(200, json=body)

        p = KimiProvider(api_key="sk-xxx")
        p.max_tool_rounds = 2
        result = p.chat([ChatMessage(role="user", content="hi")], transport=httpx.MockTransport(handler))
        assert result.ok is False
        assert "工具调用轮数超过上限" in result.reason


class TestGLMHappyPathAndSearch:
    def test_successful_chat_sends_expected_payload(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["model"] == "glm-5.2"
            assert body["messages"][0]["role"] == "system"
            assert body["tools"][0]["type"] == "web_search"
            return httpx.Response(200, json=_openai_success_body("这是一段分析。\n结论:通过"))

        p = GLMProvider(api_key="sk-xxx")
        result = p.chat(
            [ChatMessage(role="system", content="sys"), ChatMessage(role="user", content="u")],
            transport=httpx.MockTransport(handler),
        )
        assert result.ok is True
        assert "结论:通过" in result.content
        assert result.provider == "glm"
        assert result.model == "glm-5.2"

    def test_top_level_web_search_hits_extracted(self):
        body = _openai_success_body("综述...\n结论:通过")
        body["web_search"] = [
            {
                "title": "标题1", "link": "https://a.com", "content": "摘要1",
                "media": "媒体A", "publish_date": "2026-07-18", "refer": "1",
            }
        ]
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        p = GLMProvider(api_key="sk-xxx")
        result = p.chat([ChatMessage(role="user", content="hi")], transport=transport)
        assert result.ok
        assert len(result.search_hits) == 1
        assert result.search_hits[0].title == "标题1"
        assert result.search_hits[0].link == "https://a.com"
        assert result.search_hits[0].raw["refer"] == "1"

    def test_no_search_tool_when_enable_search_false(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert "tools" not in body
            return httpx.Response(200, json=_openai_success_body("无需搜索。\n结论:通过"))

        p = GLMProvider(api_key="sk-xxx")
        result = p.chat([ChatMessage(role="user", content="hi")], enable_search=False, transport=httpx.MockTransport(handler))
        assert result.ok


class TestKimiToolCallRoundTrip:
    def test_web_search_round_trip_then_final_answer(self):
        calls: List[Dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            calls.append(body)
            if len(calls) == 1:
                assert body["tools"][0]["function"]["name"] == "$web_search"
                return httpx.Response(200, json={
                    "id": "r1", "model": "kimi-k3",
                    "choices": [{
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant", "content": None,
                            "tool_calls": [{
                                "id": "call_1", "type": "function",
                                "function": {"name": "$web_search", "arguments": json.dumps({"query": "示例股份 公告"})},
                            }],
                        },
                    }],
                })
            tool_msgs = [m for m in body["messages"] if m["role"] == "tool"]
            assert len(tool_msgs) == 1
            assert json.loads(tool_msgs[0]["content"]) == {"query": "示例股份 公告"}
            return httpx.Response(200, json=_openai_success_body("查到公告如下...\n结论:否决", model="kimi-k3"))

        p = KimiProvider(api_key="sk-xxx")
        result = p.chat([ChatMessage(role="user", content="hi")], transport=httpx.MockTransport(handler))
        assert result.ok
        assert len(calls) == 2
        assert "结论:否决" in result.content
        assert len(result.search_hits) == 1
        assert result.search_hits[0].raw == {"query": "示例股份 公告"}
