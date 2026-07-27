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


class TestSearchQueryOptIn:
    """v1.3.4 护栏:`search_query` 是**纯可选加法**,不传时线上行为逐字节不变。

    这组测试的存在理由是一次真实误判——有人(合理地)怀疑 GLM payload 里
    `enable`/`search_result` 发字符串 `"True"`、`count` 发 `"5"` 是类型笔误导致搜索
    从未启动。2026-07-27 用真 key A/B 实证:**接口会正确解析字符串**(判别式是
    `enable="False"` 字符串同样能把搜索关掉),类型不是判别式,真因是检索词里没有
    股票身份。所以这里**锁死的是"不传 search_query 时 payload 一字不改"**,而不是
    锁死某种类型——把那次实证的结论钉在测试里,免得后人再"顺手修"一遍。"""

    # v1.3.3 线上实际发出的 web_search 参数块,逐字节基线。
    BASELINE = {
        "enable": "True",
        "search_engine": "search_pro",
        "search_result": "True",
        "count": "5",
    }

    def test_glm_payload_byte_identical_when_search_query_absent(self):
        p = GLMProvider(api_key="sk-xxx")
        for tools in (p._search_tools(), p._search_tools(None), p._search_tools(""), p._search_tools("   ")):
            assert tools == [{"type": "web_search", "web_search": dict(self.BASELINE)}]
            # 连键的插入顺序都不许变(json.dumps 逐字节比对)
            assert json.dumps(tools[0]["web_search"], ensure_ascii=False) == json.dumps(
                self.BASELINE, ensure_ascii=False
            )

    def test_glm_payload_gains_only_search_query_when_provided(self):
        ws = GLMProvider(api_key="sk-xxx")._search_tools("康龙化成(300759.SZ) 最近业绩")[0]["web_search"]
        assert ws["search_query"] == "康龙化成(300759.SZ) 最近业绩"
        # 其余四个字段一个不改(含取值与类型)
        assert {k: v for k, v in ws.items() if k != "search_query"} == self.BASELINE

    def test_glm_truncates_overlong_search_query(self):
        p = GLMProvider(api_key="sk-xxx")
        ws = p._search_tools("康" * 500)[0]["web_search"]
        assert len(ws["search_query"]) == p.max_search_query_chars

    def test_kimi_payload_unchanged_regardless_of_search_query(self):
        """Kimi `$web_search` 是内置函数,协议上没有注入检索词的参数位——传不传都一样。"""
        p = KimiProvider(api_key="sk-xxx")
        assert p._search_tools("康龙化成 业绩") == p._search_tools() == [
            {"type": "builtin_function", "function": {"name": "$web_search"}}
        ]

    def test_search_query_reaches_the_wire(self):
        seen: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=_openai_success_body("好的。"))

        GLMProvider(api_key="sk-xxx").chat(
            [ChatMessage(role="user", content="这只票怎么样")],
            search_query="康龙化成(300759.SZ) 这只票怎么样",
            transport=httpx.MockTransport(handler),
        )
        assert seen["tools"][0]["web_search"]["search_query"] == "康龙化成(300759.SZ) 这只票怎么样"

    def test_search_query_ignored_when_search_disabled(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "tools" not in json.loads(request.content)
            return httpx.Response(200, json=_openai_success_body("好的。"))

        r = GLMProvider(api_key="sk-xxx").chat(
            [ChatMessage(role="user", content="hi")], enable_search=False,
            search_query="不该出现", transport=httpx.MockTransport(handler),
        )
        assert r.ok


class TestZeroHitTelemetry:
    """v1.3.4:开了搜索却 0 命中 = 静默失效,必须留痕 + 对用户露出。"""

    def test_zero_hits_logs_warning(self, caplog):
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_openai_success_body("分析。")))
        with caplog.at_level("WARNING"):
            r = GLMProvider(api_key="sk-xxx").chat([ChatMessage(role="user", content="hi")], transport=transport)
        assert r.ok and not r.search_hits
        assert any("命中 0 条" in rec.getMessage() for rec in caplog.records)

    def test_no_warning_when_hits_present(self, caplog):
        body = _openai_success_body("分析。")
        body["web_search"] = [{"title": "t", "link": "https://a.com"}]
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        with caplog.at_level("WARNING"):
            r = GLMProvider(api_key="sk-xxx").chat([ChatMessage(role="user", content="hi")], transport=transport)
        assert r.ok and len(r.search_hits) == 1
        assert not [rec for rec in caplog.records if "命中 0 条" in rec.getMessage()]

    def test_no_warning_when_search_disabled(self, caplog):
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_openai_success_body("分析。")))
        with caplog.at_level("WARNING"):
            GLMProvider(api_key="sk-xxx").chat(
                [ChatMessage(role="user", content="hi")], enable_search=False, transport=transport,
            )
        assert not [rec for rec in caplog.records if "命中 0 条" in rec.getMessage()]

    def test_coverage_line_distinguishes_zero_from_some(self):
        from neckline.llm.base import search_coverage_line

        assert "0 条" in search_coverage_line(0)
        assert "不等于该标的无消息" in search_coverage_line(0)   # 「没有」≠「没看」
        assert search_coverage_line(5) == "联网搜索:本次命中 5 条"


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
