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

from neckline import settings_store
from neckline.db import init_schema
from neckline.llm.base import ChatMessage
from neckline.llm.factory import get_provider
from neckline.llm.openai_compat import OpenAICompatProvider
from neckline.llm.providers.glm import GLMProvider
from neckline.llm.providers.kimi import KimiProvider
from neckline.llm.router import TASK_BASKET_REASON, TASK_DRIVER_SEARCH


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
    """V2-②(plan §五 V2-②/§3.10-B):`_PROVIDERS` 枚举退役,`get_provider()` 改为
    纯 DB 驱动(`llm_providers` 表自填制 + `app_settings.llm_task_routes`/
    `llm_default_provider` 路由)。原先基于 `.env`/`Settings(llm_provider=...)`
    的用例整批改写——V2 起不存在"单 provider 的 .env 兜底"这个概念,`settings_obj`
    参数只为兼容既有调用方签名保留,不驱动任何解析逻辑(见 `factory.py` 模块头)。
    """

    def _db(self, tmp_path):
        db_path = tmp_path / "n.db"
        init_schema(db_path)
        return db_path

    def test_no_provider_configured_returns_none(self, tmp_path):
        assert get_provider(db_path=self._db(tmp_path)) is None

    def test_route_to_nonexistent_provider_name_returns_none(self, tmp_path):
        """路由永远优先(即便指向的名字当前不存在)——不悄悄跳过到默认值,见
        `router.resolve_task_provider_name` 文档。"""
        db = self._db(tmp_path)
        settings_store.set_llm_routes({"inquiry": "ghost"}, None, db_path=db)
        assert get_provider("inquiry", db_path=db) is None

    def test_default_provider_without_key_returns_none(self, tmp_path):
        db = self._db(tmp_path)
        settings_store.create_provider("glm", "https://x", "glm-5.2", db_path=db)  # 未填 key
        settings_store.set_llm_routes({}, "glm", db_path=db)
        assert get_provider(db_path=db) is None

    def test_disabled_provider_returns_none_even_with_key(self, tmp_path):
        db = self._db(tmp_path)
        settings_store.create_provider(
            "glm", "https://x", "glm-5.2", api_key="sk-xxx", enabled=False, db_path=db,
        )
        settings_store.set_llm_routes({}, "glm", db_path=db)
        assert get_provider(db_path=db) is None

    def test_explicit_route_builds_generic_openai_compat_provider(self, tmp_path):
        """自填制:任意名字(不要求是"glm"/"kimi")、任意端点都能配成可用 provider,
        构造出来的是裸 `OpenAICompatProvider`,不是 `GLMProvider`/`KimiProvider`
        ——这两个具体类不再是解析链路的一部分(见 `factory.py` 模块头)。"""
        db = self._db(tmp_path)
        settings_store.create_provider(
            "my-custom-glm", "https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-5.2",
            api_key="sk-xxx", has_web_search=True, search_engine="search_pro", db_path=db,
        )
        settings_store.set_llm_routes({"inquiry": "my-custom-glm"}, None, db_path=db)
        p = get_provider("inquiry", db_path=db)
        assert type(p) is OpenAICompatProvider  # 不是 GLMProvider/KimiProvider 子类
        assert p.name == "my-custom-glm" and p.model == "glm-5.2"
        assert p.has_web_search is True and p.search_engine == "search_pro"

    def test_search_task_without_route_falls_back_to_has_web_search_provider(self, tmp_path):
        """默认路由(§3.10-B):检索类任务缺路由 → 挑一个 has_web_search 的启用行,
        不是无脑用 `llm_default_provider`(那一行可能是纯推理 provider)。"""
        db = self._db(tmp_path)
        settings_store.create_provider("deepseek", "https://api.deepseek.com/x", "deepseek-chat",
                                        api_key="k1", db_path=db)
        settings_store.create_provider("glm", "https://open.bigmodel.cn/x", "glm-5.2", api_key="k2",
                                        has_web_search=True, search_engine="search_pro", db_path=db)
        settings_store.set_llm_routes({}, "deepseek", db_path=db)
        p = get_provider(TASK_DRIVER_SEARCH, db_path=db)
        assert p is not None and p.name == "glm"

    def test_non_search_task_without_route_falls_back_to_default_provider(self, tmp_path):
        db = self._db(tmp_path)
        settings_store.create_provider("deepseek", "https://api.deepseek.com/x", "deepseek-chat",
                                        api_key="k1", db_path=db)
        settings_store.set_llm_routes({}, "deepseek", db_path=db)
        p = get_provider(TASK_BASKET_REASON, db_path=db)
        assert p is not None and p.name == "deepseek"

    def test_fresh_isolated_db_has_no_configured_provider(self, tmp_path):
        """替代 V1"真实 `.env` 现状必解析为 None"的断言:V2 起没有 `.env` 单
        provider 兜底这个概念,`get_provider()` 完全由 DB 驱动——一份全新/空库
        天然等价于旧断言想验证的"当前无可用 LLM"现状(§2.0/§3.8「全链路必须在
        无 key 下优雅降级跑通」)。"""
        assert get_provider(db_path=self._db(tmp_path)) is None


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


class TestSearchEngineField:
    """v1.5-④-A3(§七 P1-7):`LLMResult.search_engine` 只在**成功**路径填充,读
    `GLMProvider._SEARCH_ENGINE` 单一源(与 `_search_tools` payload 里的取值同一处,
    不重复写字面量);Kimi 没有「可选引擎」概念,恒 `None`。"""

    def test_glm_success_reports_search_pro(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_openai_success_body("分析。\n结论:通过")))
        r = GLMProvider(api_key="sk-xxx").chat([ChatMessage(role="user", content="hi")], transport=transport)
        assert r.ok and r.search_engine == "search_pro"

    def test_glm_value_reads_same_constant_as_search_tools_payload(self):
        """不重复硬编:`_search_engine_value()` 与 `_search_tools()` 里的
        `web_search["search_engine"]` 必须逐字节相同(同一个类常量)。"""
        p = GLMProvider(api_key="sk-xxx")
        payload_value = p._search_tools()[0]["web_search"]["search_engine"]
        assert p._search_engine_value() == payload_value == "search_pro"

    def test_glm_search_disabled_reports_none(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_openai_success_body("无需搜索。\n结论:通过")))
        r = GLMProvider(api_key="sk-xxx").chat(
            [ChatMessage(role="user", content="hi")], enable_search=False, transport=transport,
        )
        assert r.ok and r.search_engine is None

    def test_glm_failure_path_reports_none(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(500, json={"error": "boom"}))
        r = GLMProvider(api_key="sk-xxx").chat([ChatMessage(role="user", content="hi")], transport=transport)
        assert not r.ok and r.search_engine is None

    def test_glm_missing_key_reports_none_without_network(self):
        r = GLMProvider(api_key=None).chat([ChatMessage(role="user", content="hi")])
        assert not r.ok and r.search_engine is None

    def test_kimi_success_reports_none_no_engine_concept(self):
        """Kimi 内置 `$web_search` 协议层没有可选引擎参数位,恒 `None`
        (`KimiProvider` 未覆盖 `_search_engine_value`,走基类默认值)。"""
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_openai_success_body("分析。\n结论:通过", model="kimi-k3")))
        r = KimiProvider(api_key="sk-xxx").chat([ChatMessage(role="user", content="hi")], transport=transport)
        assert r.ok and r.search_engine is None


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


class TestGenericOpenAICompatProviderSearch:
    """V2-②(plan §3.10-B):裸 `OpenAICompatProvider`(自填制 provider 的构造目标,
    非 `GLMProvider`/`KimiProvider` 具体子类)的通用搜索钩子。`has_web_search=0`
    时**一律不发 `tools`/`search_query`**(锁死,§3.10-B 铁律 + v1.3.4 案底)。"""

    def _provider(self, **kwargs) -> OpenAICompatProvider:
        kwargs.setdefault("api_key", "sk-xxx")
        kwargs.setdefault("model", "generic-model")
        kwargs.setdefault("name", "custom")
        kwargs.setdefault("api_url", "https://example.invalid/chat/completions")
        return OpenAICompatProvider(**kwargs)

    def test_has_web_search_false_search_tools_returns_none(self):
        p = self._provider(has_web_search=False)
        assert p._search_tools() is None
        assert p._search_tools("随便什么检索词") is None

    def test_has_web_search_false_payload_never_contains_tools_or_search_query(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert "tools" not in body
            assert "search_query" not in json.dumps(body)
            return httpx.Response(200, json=_openai_success_body("ok", model="generic-model"))

        p = self._provider(has_web_search=False)
        r = p.chat(
            [ChatMessage(role="user", content="hi")], search_query="不该出现",
            transport=httpx.MockTransport(handler),
        )
        assert r.ok and r.search_engine is None

    def test_has_web_search_true_uses_generic_web_search_tool_shape(self):
        p = self._provider(has_web_search=True, search_engine="search_pro")
        tools = p._search_tools("康龙化成 业绩")
        assert tools == [{
            "type": "web_search",
            "web_search": {
                "enable": "True", "search_engine": "search_pro", "search_result": "True",
                "count": "5", "search_query": "康龙化成 业绩",
            },
        }]

    def test_has_web_search_true_extracts_top_level_hits_same_shape_as_glm(self):
        body = _openai_success_body("综述...", model="generic-model")
        body["web_search"] = [{"title": "t1", "link": "https://a.com", "content": "c1"}]
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        p = self._provider(has_web_search=True, search_engine="search_pro")
        r = p.chat([ChatMessage(role="user", content="hi")], transport=transport)
        assert r.ok and len(r.search_hits) == 1 and r.search_hits[0].title == "t1"
        assert r.search_engine == "search_pro"

    def test_constructor_overrides_are_optional_and_do_not_affect_glm_kimi(self):
        """新增的四个可选构造参数(`name`/`api_url`/`has_web_search`/
        `search_engine`)不传时,既有 `GLMProvider(api_key=...)`/
        `KimiProvider(api_key=...)` 调用方式逐字节不变。"""
        g = GLMProvider(api_key="sk-xxx")
        assert g.has_web_search is False and g.search_engine is None  # 基类默认值
        assert g._search_tools() == [{  # 但 GLM 自己的覆盖不读这两个属性,行为不变
            "type": "web_search",
            "web_search": {"enable": "True", "search_engine": "search_pro",
                            "search_result": "True", "count": "5"},
        }]
        k = KimiProvider(api_key="sk-xxx")
        assert k._search_tools() == [{"type": "builtin_function", "function": {"name": "$web_search"}}]
