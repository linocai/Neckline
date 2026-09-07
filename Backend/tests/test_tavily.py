from __future__ import annotations

import json

import httpx

from neckline.search.tavily import TavilySearchClient


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
    assert result.hits[0].publish_date == ""  # A date in the title does not prove publication.
    assert result.hits[0].media == "example.test"
    assert seen["authorization"] == f"Bearer {secret}"
    assert seen["body"] == {
        "query": "A股 公司公告", "search_depth": "basic", "max_results": 5,
        "topic": "general", "include_answer": False, "include_raw_content": False,
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


def test_missing_credits_preserves_hits_and_explicit_publication_time():
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={
        "results": [{"title":"报道", "url":"https://example.test/source", "content":"有效摘录",
                     "published_date":"2026-09-06T20:59:59+08:00"}]}))
    result = TavilySearchClient("fixture-key", transport=transport).search("query")
    assert result.ok is False and result.credits is None
    assert result.hits[0].content == "有效摘录"
    assert result.hits[0].publish_date == "2026-09-06T20:59:59+08:00"
