"""LLM 供应商抽象层单测(plan 2.4/§3.4)。姿势沿用 LinoN `test_llm.py` 的
`httpx.MockTransport` 免联网套路,覆盖:① 有 key 成功路径(GLM 顶层 web_search /
Kimi `$web_search` 工具调用回合);② 无 key / 无 provider;③ 超时重试后降级;
④ 非法响应(非200 / 非法JSON / 结构缺字段 / 空内容);⑤ 工具调用轮数封顶。
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, List, Optional

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
from neckline.search.tavily import TavilyGroundedProvider


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


def test_chat_normalizes_provider_reported_usage_without_estimation():
    body = _openai_success_body("ok")
    body["usage"] = {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}
    provider = OpenAICompatProvider(
        api_key="k", model="m", name="test", api_url="https://example.invalid/chat",
    )
    result = provider.chat(
        [ChatMessage(role="user", content="hi")], enable_search=False,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    )
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (12, 8, 20)
    assert result.usage_unavailable is False
    assert result.raw_usage == {"responses": [body["usage"]]}


def test_chat_marks_missing_provider_usage_unavailable():
    provider = OpenAICompatProvider(
        api_key="k", model="m", name="test", api_url="https://example.invalid/chat",
    )
    result = provider.chat(
        [ChatMessage(role="user", content="hi")], enable_search=False,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=_openai_success_body("ok"))),
    )
    assert result.usage_unavailable is True
    assert result.total_tokens is None


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
        """写侧拒绝幽灵 Provider，避免默认模型看似保存、实际不可点击。"""
        db = self._db(tmp_path)
        with pytest.raises(LookupError):
            settings_store.set_llm_routes({"review": "ghost"}, None, db_path=db)

    def test_default_provider_without_key_returns_none(self, tmp_path):
        db = self._db(tmp_path)
        settings_store.create_provider("glm", "https://x", "glm-5.2", db_path=db)  # 未填 key
        with pytest.raises(LookupError):
            settings_store.set_llm_routes({}, "glm", db_path=db)

    def test_disabled_provider_returns_none_even_with_key(self, tmp_path):
        db = self._db(tmp_path)
        settings_store.create_provider(
            "glm", "https://x", "glm-5.2", api_key="sk-xxx", enabled=False, db_path=db,
        )
        with pytest.raises(LookupError):
            settings_store.set_llm_routes({}, "glm", db_path=db)

    def test_explicit_route_builds_generic_openai_compat_provider(self, tmp_path):
        """自填制:任意名字(不要求是"glm"/"kimi")、任意端点都能配成可用 provider,
        构造出来的是裸 `OpenAICompatProvider`,不是 `GLMProvider`/`KimiProvider`
        ——这两个具体类不再是解析链路的一部分(见 `factory.py` 模块头)。"""
        db = self._db(tmp_path)
        settings_store.create_provider(
            "my-custom-glm", "https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-5.2",
            api_key="sk-xxx", has_web_search=True, search_engine="search_pro", db_path=db,
        )
        settings_store.set_llm_routes({"review": "my-custom-glm"}, None, db_path=db)
        p = get_provider("review", db_path=db)
        assert type(p) is OpenAICompatProvider  # 不是 GLMProvider/KimiProvider 子类
        assert p.name == "my-custom-glm" and p.model == "glm-5.2"
        assert p.has_web_search is False and p.search_engine is None

    def test_search_task_uses_default_llm_wrapped_by_tavily(self, tmp_path):
        """默认模型负责读证据和推理，联网证据统一由 Tavily 独立取得。"""
        db = self._db(tmp_path)
        settings_store.create_provider("deepseek", "https://api.deepseek.com/x", "deepseek-chat",
                                        api_key="k1", db_path=db)
        settings_store.create_provider("glm", "https://open.bigmodel.cn/x", "glm-5.2", api_key="k2",
                                        has_web_search=True, search_engine="search_pro", db_path=db)
        settings_store.set_llm_routes({}, "deepseek", db_path=db)
        settings_store.set_tavily_api_key("tvly-test", db_path=db)
        p = get_provider(TASK_DRIVER_SEARCH, db_path=db)
        assert isinstance(p, TavilyGroundedProvider)
        assert p.name == "deepseek"
        assert p.inner.has_web_search is False and p.inner.search_engine is None

    def test_non_search_task_without_route_falls_back_to_default_provider(self, tmp_path):
        db = self._db(tmp_path)
        settings_store.create_provider("deepseek", "https://api.deepseek.com/x", "deepseek-chat",
                                        api_key="k1", db_path=db)
        settings_store.set_llm_routes({}, "deepseek", db_path=db)
        p = get_provider(TASK_BASKET_REASON, db_path=db)
        assert p is not None and p.name == "deepseek"

    # —— §七 P0-40 → P0-44:按 task 类别分级(流式 + chunk 间隔超时)——————————
    def _provider_for(self, tmp_path, task):
        db = self._db(tmp_path)
        settings_store.create_provider("glm", "https://open.bigmodel.cn/x", "glm-5.2", api_key="k",
                                        has_web_search=True, search_engine="search_pro", db_path=db)
        settings_store.set_llm_routes({}, "glm", db_path=db)
        if task in ("driver_search", "news_scan"):
            settings_store.set_tavily_api_key("tvly-test", db_path=db)
        provider = get_provider(task, db_path=db)
        return provider.inner if isinstance(provider, TavilyGroundedProvider) else provider

    @pytest.mark.parametrize("task", ["basket_reason", "deep_reason", "review"])
    def test_long_context_tasks_stream_with_chunk_gap_timeout(self, tmp_path, task):
        """⑤ 篮子聚合一次塞 20 颗种子 + 成员机械数据:2026-08-05 中午 3/3 次撞满 90s、
        当晚 3/3 次撞满 240s —— **抬数字这条路已被证伪**,推理类必须走流式,读超时
        语义随之变成 **chunk 间隔**(判「还在不在吐字」,与上游吞吐无关)。"""
        p = self._provider_for(tmp_path, task)
        assert p.use_streaming is True
        assert p.read_timeout == 90.0   # ⚠ 语义 = chunk 间隔,不是整段墙钟

    @pytest.mark.parametrize("task", ["driver_search", "news_scan", "direction_triage", "tier_rank", "script", "nl_alert",
                                      "profile", None])
    def test_search_and_light_tasks_stay_non_streaming_at_the_validated_90s(self, tmp_path, task):
        """⛔ 检索类**刻意不开流式** —— GLM `web_search` tools 协议与流式的组合本项目
        从未验证过(v1.3.4 案底:不被认识的组合会 `ok=True` 静默返 0 条)。90s 整段
        墙钟是有实测背书的,一字不动。"""
        p = self._provider_for(tmp_path, task)
        assert p.use_streaming is False
        assert p.read_timeout == 90.0

    def test_directly_constructed_providers_are_untouched(self):
        """分级只发生在工厂里;直接 new 出来的(单测替身 / `providers/{glm,kimi}.py`
        参考实现)行为**逐字节不变** —— 含"默认不流式"。"""
        for p in (OpenAICompatProvider(api_key="k"), GLMProvider(api_key="k"), KimiProvider(api_key="k")):
            assert p.read_timeout == 90.0 and p.use_streaming is False
        assert OpenAICompatProvider(api_key="k", read_timeout=240).read_timeout == 240.0
        assert OpenAICompatProvider(api_key="k", use_streaming=True).use_streaming is True

    def test_the_number_actually_reaches_the_wire(self, tmp_path, monkeypatch):
        """⚠ 光断言属性不够 —— 要证明它**真的进了 httpx 的 timeout**。`_post` 里
        `httpx.Timeout(self.read_timeout, connect=...)` 是唯一构造点,这里把
        `httpx.Client` 换成探针,捕获实际传下去的 timeout。"""
        seen: List[Any] = []

        class _Probe:
            def __init__(self, **kw):
                seen.append(kw.get("timeout"))

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **k):
                raise RuntimeError("探针不发真请求")

            def stream(self, *a, **k):
                raise RuntimeError("探针不发真请求")

        monkeypatch.setattr(httpx, "Client", _Probe)
        p = self._provider_for(tmp_path, "basket_reason")
        p.max_attempts = 1
        body, reason = p._post({"x": 1, "stream": True}, None)
        assert body is None and reason == "调用异常 RuntimeError"
        assert seen and seen[0].read == 90.0 and seen[0].connect == 6.0

    def test_fresh_isolated_db_has_no_configured_provider(self, tmp_path):
        """替代 V1"真实 `.env` 现状必解析为 None"的断言:V2 起没有 `.env` 单
        provider 兜底这个概念,`get_provider()` 完全由 DB 驱动——一份全新/空库
        天然等价于旧断言想验证的"当前无可用 LLM"现状(§2.0/§3.8「全链路必须在
        无 key 下优雅降级跑通」)。"""
        assert get_provider(db_path=self._db(tmp_path)) is None


class TestStreamingAssembly:
    """§七 P0-44:大上下文推理改 SSE 流式。**这一组的核心断言是「拼出来的东西与非
    流式逐字节等价」** —— 上层(`chat()` 的 tool 循环 / 空内容判定 / 搜索命中提取 /
    所有调用方)一行都没改,靠的就是这条等价性。"""

    CONTENT = "这是一段较长的分析。\n结论:通过"

    def _sse(self, *chunks: Dict[str, Any], done: bool = True) -> bytes:
        out = b""
        for c in chunks:
            out += b"data: " + json.dumps(c, ensure_ascii=False).encode("utf-8") + b"\n\n"
        if done:
            out += b"data: [DONE]\n\n"
        return out

    def _delta_chunks(self, content: str, model: str = "glm-5.2") -> List[Dict[str, Any]]:
        """把一段内容切成逐字 delta,首块带 role、末块带 finish_reason —— GLM/OpenAI
        标准形状。"""
        chunks: List[Dict[str, Any]] = []
        for i, ch in enumerate(content):
            delta: Dict[str, Any] = {"content": ch}
            if i == 0:
                delta["role"] = "assistant"
            chunks.append({"id": "abc", "model": model,
                           "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
        chunks.append({"id": "abc", "model": model,
                       "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        return chunks

    def _streaming_provider(self, **kw) -> OpenAICompatProvider:
        kw.setdefault("api_key", "sk-xxx")
        kw.setdefault("model", "glm-5.2")
        kw.setdefault("name", "glm")
        kw.setdefault("api_url", "https://example.invalid/chat/completions")
        kw.setdefault("use_streaming", True)
        return OpenAICompatProvider(**kw)

    def _transport(self, payload: bytes, ctype: str = "text/event-stream",
                   seen: Optional[Dict[str, Any]] = None) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if seen is not None:
                seen.update(json.loads(request.content))
            return httpx.Response(200, content=iter([payload]), headers={"content-type": ctype})

        return httpx.MockTransport(handler)

    # —— 等价性(本组的主命题)——————————————————————————————————————————
    def test_assembled_body_is_byte_identical_to_the_non_streaming_body(self):
        """**逐字节**:拼出来的 dict 连键序都要和非流式响应体一样 —— 不是"字段差不多
        齐",是 `json.dumps` 完全相等。"""
        p = self._streaming_provider()
        body, err = p._assemble_stream(
            self._sse(*self._delta_chunks(self.CONTENT)).decode("utf-8").split("\n")
        )
        assert err is None
        expected = _openai_success_body(self.CONTENT)
        assert json.dumps(body, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)

    def test_streaming_and_non_streaming_yield_the_same_llm_result(self):
        """端到端:同一段内容,走流式与走非流式,`LLMResult` 的每个字段都一样
        (`raw_responses` 里那份 body 也一样,上面那条已逐字节锁死)。"""
        streamed = self._streaming_provider().chat(
            [ChatMessage(role="user", content="hi")], enable_search=False,
            transport=self._transport(self._sse(*self._delta_chunks(self.CONTENT))),
        )
        plain = self._streaming_provider(use_streaming=False).chat(
            [ChatMessage(role="user", content="hi")], enable_search=False,
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=_openai_success_body(self.CONTENT))),
        )
        assert streamed.ok is plain.ok is True
        assert streamed.content == plain.content == self.CONTENT
        assert (streamed.provider, streamed.model) == (plain.provider, plain.model)
        assert streamed.search_hits == plain.search_hits == []
        assert streamed.raw_responses == plain.raw_responses

    def test_stream_true_actually_reaches_the_wire(self):
        seen: Dict[str, Any] = {}
        self._streaming_provider().chat(
            [ChatMessage(role="user", content="hi")], enable_search=False,
            transport=self._transport(self._sse(*self._delta_chunks("好")), seen=seen),
        )
        assert seen["stream"] is True

    # —— 守门:检索类绝不走流式 ————————————————————————————————————————
    def test_non_streaming_payload_is_unchanged_byte_for_byte(self):
        """P0-44 的**零回归断言**:不开流式时 payload 仍是 `stream: False`,与改动前
        逐字节相同 —— 检索链路(GLM `web_search` tools 协议)一个字节都没被碰。"""
        seen: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=_openai_success_body("ok"))

        GLMProvider(api_key="sk-xxx").chat([ChatMessage(role="user", content="hi")],
                                            transport=httpx.MockTransport(handler))
        assert seen["stream"] is False
        assert seen["tools"][0]["type"] == "web_search"      # 搜索声明原样发出
        assert set(seen) == {"model", "messages", "stream", "tools"}

    def test_search_tasks_wrap_non_streaming_llm_without_vendor_tools(self, tmp_path):
        """检索类由 Tavily 包装，内部 LLM 非流式且不带厂商私有搜索工具。"""
        from neckline.llm import router

        for task in router.DEFAULT_SEARCH_TASKS:
            assert router.use_streaming_for_task(task) is False
        db = tmp_path / "n.db"
        init_schema(db)
        settings_store.create_provider("glm", "https://x/chat/completions", "glm-5.2", api_key="k",
                                        has_web_search=True, search_engine="search_pro", db_path=db)
        settings_store.set_llm_routes({}, "glm", db_path=db)
        settings_store.set_tavily_api_key("tvly-test", db_path=db)
        provider = get_provider(TASK_DRIVER_SEARCH, db_path=db)
        assert isinstance(provider, TavilyGroundedProvider)
        assert provider.inner.use_streaming is False
        assert provider.inner.has_web_search is False

    # —— chunk 间隔超时:本次修复的**判据本身** ——————————————————————————
    def test_chunk_gap_timeout_retries_then_degrades_without_partial_content(self):
        """流到一半静默超时 → 重试 N 次后干净降级。**⛔ 半截内容绝不当成品返回**:
        半截 JSON 解出来可能正好是个"看着合法"的残缺篮子,比干净失败危险得多。"""
        attempts = {"n": 0}
        head = self._sse(*self._delta_chunks("这是开头")[:3], done=False)

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1

            def gen():
                yield head
                raise httpx.ReadTimeout("chunk 间隔超时", request=request)

            return httpx.Response(200, content=gen(), headers={"content-type": "text/event-stream"})

        p = self._streaming_provider()
        p.max_attempts = 2
        r = p.chat([ChatMessage(role="user", content="hi")], enable_search=False,
                   transport=httpx.MockTransport(handler))
        assert attempts["n"] == 2
        assert r.ok is False and "调用异常 ReadTimeout" in r.reason
        assert not r.content

    def test_a_generation_longer_than_the_old_240s_wall_still_completes(self):
        """**本次修复的核心命题**:非流式下"整段生成 > read_timeout"必死(P0-40 的
        240s 当晚 3/3 撞满);流式下只要 chunk 不断,**整段多长都活**。这里用一个
        「每块之间的间隔都在容忍内、但总时长远超单块容忍」的流来证明它。"""
        p = self._streaming_provider()
        p.read_timeout = 0.05                     # chunk 间隔容忍 50ms
        chunks = self._delta_chunks("很长的一段生成")

        def handler(request: httpx.Request) -> httpx.Response:
            def gen():
                for c in chunks:                  # 每块间隔 10ms < 50ms,总时长 > 50ms
                    time.sleep(0.01)
                    yield self._sse(c, done=False)
                yield b"data: [DONE]\n\n"

            return httpx.Response(200, content=gen(), headers={"content-type": "text/event-stream"})

        started = time.monotonic()
        r = p.chat([ChatMessage(role="user", content="hi")], enable_search=False,
                   transport=httpx.MockTransport(handler))
        elapsed = time.monotonic() - started
        assert r.ok is True and r.content == "很长的一段生成"
        assert elapsed > p.read_timeout, "整段耗时必须超过单个 chunk 间隔容忍,否则这条没验到东西"

    # —— 容错三条 ——————————————————————————————————————————————————
    def test_missing_done_sentinel_is_tolerated(self):
        """`[DONE]` 缺失不算错(不少实现直接关连接了事)。"""
        p = self._streaming_provider()
        body, err = p._assemble_stream(
            self._sse(*self._delta_chunks(self.CONTENT), done=False).decode("utf-8").split("\n"))
        assert err is None
        assert body["choices"][0]["message"]["content"] == self.CONTENT

    def test_malformed_chunk_is_skipped_not_fatal(self):
        """半截 / 非法 JSON 的**单条** chunk 只跳过它自己,不丢整段生成。"""
        p = self._streaming_provider()
        good = self._sse(*self._delta_chunks(self.CONTENT), done=False).decode("utf-8").split("\n")
        polluted = good[:4] + ['data: {"id":"abc","choi', "data: 不是 JSON"] + good[4:]
        body, err = p._assemble_stream(polluted)
        assert err is None
        assert body["choices"][0]["message"]["content"] == self.CONTENT

    def test_every_chunk_malformed_degrades_explicitly_not_silently_empty(self):
        """⚠ 全坏 ≠ 空回答:必须如实报错,**不许静默返一个空内容的成功体**。"""
        p = self._streaming_provider()
        body, err = p._assemble_stream(["data: {坏", "data: 也坏", ""])
        assert body is None and "流式响应为空" in err

    def test_upstream_that_ignores_stream_true_falls_back_to_whole_json(self):
        """自填制下完全可能碰到不认 `stream:true` 的端点 —— 它原样回一整份 JSON。
        判据取**数据本身**(有没有 `data:` 行),不看 `Content-Type`(缺头/写错头的
        实现太多)。"""
        plain = json.dumps(_openai_success_body(self.CONTENT), ensure_ascii=False).encode("utf-8")
        r = self._streaming_provider().chat(
            [ChatMessage(role="user", content="hi")], enable_search=False,
            transport=self._transport(plain, ctype="application/json"),
        )
        assert r.ok is True and r.content == self.CONTENT

    # —— 形状保真的其余两处 ————————————————————————————————————————
    def test_reasoning_content_is_not_merged_into_content(self):
        """思考型模型的 `reasoning_content` **不并入 content**(非流式返回的也只有
        `message.content`,并进来就不等价了);但它照样算一个 chunk —— 「还在思考」
        不会被 chunk 间隔超时误杀。"""
        p = self._streaming_provider()
        chunks = [
            {"id": "abc", "model": "glm-5.2",
             "choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "先想想…"}}]},
            {"id": "abc", "model": "glm-5.2",
             "choices": [{"index": 0, "delta": {"content": "答案"}, "finish_reason": "stop"}]},
        ]
        body, err = p._assemble_stream(self._sse(*chunks).decode("utf-8").split("\n"))
        assert err is None
        assert body["choices"][0]["message"]["content"] == "答案"

    def test_tool_call_deltas_are_accumulated_by_index(self):
        """`tool_calls` 的分片 `arguments` 要按 `index` 拼回完整串 —— 否则上层
        `chat()` 的工具循环会拿到半截 arguments(当前不给检索类开流式,这里是形状
        保真的保险,不是已验证过的生产路径)。"""
        p = self._streaming_provider()
        chunks = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [
                {"index": 0, "id": "call_1", "type": "function",
                 "function": {"name": "$web_search", "arguments": '{"que'}}]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": 'ry":"x"}'}}]}, "finish_reason": "tool_calls"}]},
        ]
        body, err = p._assemble_stream(self._sse(*chunks).decode("utf-8").split("\n"))
        assert err is None
        tc = body["choices"][0]["message"]["tool_calls"]
        assert len(tc) == 1 and tc[0]["id"] == "call_1"
        assert json.loads(tc[0]["function"]["arguments"]) == {"query": "x"}
        assert body["choices"][0]["finish_reason"] == "tool_calls"

    def test_non_200_on_stream_degrades_same_as_non_streaming(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        r = self._streaming_provider().chat([ChatMessage(role="user", content="hi")],
                                             enable_search=False,
                                             transport=httpx.MockTransport(handler))
        assert r.ok is False and "500" in r.reason

    def test_stream_completion_logs_the_wall_clock_evidence(self, caplog):
        """生产判据埋点:journal 里必须留下"这次流了多久 / 多少 chunk" —— 它是
        「生成超过旧 240s 固定墙也照样活着」的唯一现场证据。"""
        with caplog.at_level("INFO"):
            self._streaming_provider().chat(
                [ChatMessage(role="user", content="hi")], enable_search=False,
                transport=self._transport(self._sse(*self._delta_chunks(self.CONTENT))),
            )
        assert any("流式生成完成" in rec.getMessage() for rec in caplog.records)


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

    def test_429_retries_on_existing_attempt_budget_then_succeeds(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    429, headers={"Retry-After": "0"},
                    json={"error": {"code": "1302", "message": "rate limited"}},
                )
            body = _openai_success_body("ok")
            body["usage"] = {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}
            return httpx.Response(200, json=body)

        p = GLMProvider(api_key="sk-xxx")
        result = p.chat(
            [ChatMessage(role="user", content="hi")],
            enable_search=False,
            transport=httpx.MockTransport(handler),
        )
        assert result.ok is True
        assert result.total_tokens == 5
        assert calls["n"] == 2

    def test_1113_balance_error_does_not_retry(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(
                429, json={"error": {"code": "1113", "message": "balance exhausted"}},
            )

        p = GLMProvider(api_key="sk-xxx")
        result = p.chat(
            [ChatMessage(role="user", content="hi")],
            enable_search=False,
            transport=httpx.MockTransport(handler),
        )
        assert result.ok is False
        assert result.reason == "上游 429/1113"
        assert calls["n"] == 1

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
