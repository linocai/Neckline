from __future__ import annotations

import json

import httpx

from neckline.llm.base import ChatMessage, LLMProvider, LLMResult
from neckline.search.tavily import TavilyGroundedProvider, TavilySearchClient


def test_basic_search_uses_documented_shape_and_records_credits_without_leaking_key(caplog):
    secret = "tvly-secret-never-log"
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "results": [{
                "title": "公司公告 2026-08-14", "url": "https://example.test/a",
                "content": "公司披露新订单", "score": 0.9,
            }],
            "usage": {"credits": 1}, "response_time": 0.12, "request_id": "req-1",
        })

    result = TavilySearchClient(secret, transport=httpx.MockTransport(handler)).search("A股 公司公告")

    assert result.ok is True and result.credits == 1 and result.request_id == "req-1"
    assert result.hits[0].publish_date == "2026-08-14"
    assert result.hits[0].media == "example.test"
    assert seen["authorization"] == f"Bearer {secret}"
    assert seen["body"] == {
        "query": "A股 公司公告", "search_depth": "basic", "max_results": 5,
        "topic": "finance", "include_answer": False, "include_raw_content": False,
        "include_images": False, "auto_parameters": False, "include_usage": True,
    }
    assert secret not in caplog.text


def test_missing_credit_usage_is_explicit_failure():
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={"results": []}))
    result = TavilySearchClient("tvly-test", transport=transport).search("query")
    assert result.ok is False and result.reason == "tavily_usage_unavailable"


def test_retryable_status_retries_but_auth_failure_does_not():
    attempts = {"retry": 0, "auth": 0}

    def retry_handler(_request: httpx.Request) -> httpx.Response:
        attempts["retry"] += 1
        if attempts["retry"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"results": [], "usage": {"credits": 1}})

    assert TavilySearchClient("k", transport=httpx.MockTransport(retry_handler)).search("q").ok
    assert attempts["retry"] == 3

    def auth_handler(_request: httpx.Request) -> httpx.Response:
        attempts["auth"] += 1
        return httpx.Response(401)

    result = TavilySearchClient("k", transport=httpx.MockTransport(auth_handler)).search("q")
    assert result.ok is False and result.reason == "tavily_http_401"
    assert attempts["auth"] == 1


class _Reasoner(LLMProvider):
    name = "deepseek"
    model = "deepseek-chat"

    def __init__(self):
        self.calls = []

    def chat(self, messages, *, enable_search=True, search_query=None, transport=None):
        self.calls.append((messages, enable_search, search_query, transport))
        return LLMResult(
            ok=True, content="基于证据完成", provider=self.name, model=self.model,
            prompt_tokens=10, completion_tokens=5, total_tokens=15,
            raw_usage={"responses": [{"total_tokens": 15}]}, usage_unavailable=False,
        )


def test_grounded_provider_searches_first_then_calls_reasoner_without_native_search():
    search_transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={
        "results": [{
            "title": "交易所公告", "url": "https://example.test/notice",
            "content": "已确认公告", "published_date": "2026-08-14",
        }],
        "usage": {"credits": 1}, "request_id": "req-ground",
    }))
    inner = _Reasoner()
    provider = TavilyGroundedProvider(inner, TavilySearchClient("tvly-test", transport=search_transport))
    result = provider.chat(
        [ChatMessage(role="user", content="研究这家公司")],
        enable_search=True, search_query="公司名 交易所公告",
    )

    assert result.ok is True and result.search_engine == "tavily_basic"
    assert len(result.search_hits) == 1
    messages, enable_search, _query, _transport = inner.calls[0]
    assert enable_search is False
    assert "Tavily 已完成本次联网检索" in messages[-1].content
    assert "已确认公告" in messages[-1].content
