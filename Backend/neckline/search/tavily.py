"""Tavily-only external research adapter for DeepSeek and other pure LLMs.

The key is read from ``app_settings.tavily_api_key`` by the factory and is never
returned, logged, or embedded in an LLM prompt.  Search and reasoning stay two
separate billable operations: Tavily returns evidence; the wrapped LLM receives
that evidence with its own native search explicitly disabled.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from neckline.llm.base import ChatMessage, LLMProvider, LLMResult, SearchHit
# Repository-wide LLM call-site guard: grounding still belongs to the shared
# prompt-context discipline even though external retrieval is a separate API.
import neckline.llm.prompt_context as _prompt_context  # noqa: F401

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


@dataclass(frozen=True)
class TavilySearchResponse:
    ok: bool
    query: str
    hits: Tuple[SearchHit, ...] = ()
    credits: Optional[int] = None
    response_time: Optional[float] = None
    request_id: Optional[str] = None
    reason: str = "ok"
    wall_ms: int = 0

    def evidence_payload(self) -> dict:
        return {
            "provider": "tavily",
            "query": self.query,
            "credits": self.credits,
            "requestId": self.request_id,
            "evidence": [
                {
                    "claim": hit.content.strip() or hit.title.strip(),
                    "source": hit.media.strip() or hit.title.strip(),
                    "date": hit.publish_date.strip(),
                    "url": hit.link.strip(),
                    "title": hit.title.strip(),
                }
                for hit in self.hits
                if hit.content.strip() or hit.title.strip()
            ],
        }


_DATE_PATTERNS = (
    re.compile(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b"),
    re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日"),
)


def _published_date(item: dict) -> str:
    explicit = item.get("published_date", item.get("publishedDate", item.get("date")))
    if explicit:
        raw = str(explicit).strip()
        for pattern in _DATE_PATTERNS:
            match = pattern.search(raw)
            if match:
                y, m, d = (int(part) for part in match.groups())
                return f"{y:04d}-{m:02d}-{d:02d}"
        return raw[:32]
    haystack = f"{item.get('title', '')} {item.get('content', '')}"
    for pattern in _DATE_PATTERNS:
        match = pattern.search(haystack)
        if match:
            y, m, d = (int(part) for part in match.groups())
            return f"{y:04d}-{m:02d}-{d:02d}"
    return ""


class TavilySearchClient:
    """Small synchronous client using Tavily's documented Bearer endpoint.

    The free-account test path intentionally uses Basic search, one credit per
    request, five results, no Tavily-generated answer, and no raw full-page
    body.  This avoids paying a second answer model and keeps evidence injected
    into DeepSeek bounded while the real Chinese A-share recall is evaluated.
    """

    provider = "tavily"
    search_depth = "basic"
    max_results = 5
    topic = "finance"
    request_timeout = 30.0
    max_attempts = 3

    def __init__(self, api_key: Optional[str], *, transport: Optional[Any] = None) -> None:
        self.api_key = (api_key or "").strip()
        self.transport = transport

    def search(self, query: str, *, transport: Optional[Any] = None) -> TavilySearchResponse:
        clean_query = (query or "").strip()[:400]
        if not self.api_key:
            return TavilySearchResponse(False, clean_query, reason="tavily_api_key_missing")
        if not clean_query:
            return TavilySearchResponse(False, clean_query, reason="empty_search_query")
        try:
            import httpx
        except ImportError:
            return TavilySearchResponse(False, clean_query, reason="httpx_not_installed")

        payload = {
            "query": clean_query,
            "search_depth": self.search_depth,
            "max_results": self.max_results,
            "topic": self.topic,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "auto_parameters": False,
            "include_usage": True,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        started_all = time.monotonic()
        last_reason = "tavily_call_failed"
        chosen_transport = transport if transport is not None else self.transport
        for attempt in range(1, self.max_attempts + 1):
            try:
                kwargs = {"timeout": self.request_timeout}
                if chosen_transport is not None:
                    kwargs["transport"] = chosen_transport
                with httpx.Client(**kwargs) as client:
                    response = client.post(TAVILY_SEARCH_URL, json=payload, headers=headers)
                if response.status_code == 200:
                    try:
                        body = response.json()
                    except Exception:
                        last_reason = "tavily_invalid_json"
                        break
                    if not isinstance(body, dict):
                        last_reason = "tavily_invalid_response"
                        break
                    raw_usage = body.get("usage")
                    credits = raw_usage.get("credits") if isinstance(raw_usage, dict) else None
                    if isinstance(credits, bool) or not isinstance(credits, int) or credits < 0:
                        return TavilySearchResponse(
                            False, clean_query, reason="tavily_usage_unavailable",
                            wall_ms=max(0, int((time.monotonic() - started_all) * 1000)),
                        )
                    hits: List[SearchHit] = []
                    for raw in body.get("results") or []:
                        if not isinstance(raw, dict):
                            continue
                        link = str(raw.get("url") or "").strip()
                        hits.append(SearchHit(
                            title=str(raw.get("title") or "").strip(),
                            link=link,
                            content=str(raw.get("content") or "").strip(),
                            media=urlparse(link).netloc,
                            publish_date=_published_date(raw),
                            raw={
                                "score": raw.get("score"),
                                "id": raw.get("id"),
                            },
                        ))
                    response_time = body.get("response_time")
                    try:
                        response_time = float(response_time) if response_time is not None else None
                    except (TypeError, ValueError):
                        response_time = None
                    return TavilySearchResponse(
                        True, clean_query, hits=tuple(hits), credits=credits,
                        response_time=response_time,
                        request_id=str(body.get("request_id") or "") or None,
                        wall_ms=max(0, int((time.monotonic() - started_all) * 1000)),
                    )
                last_reason = f"tavily_http_{response.status_code}"
                if response.status_code not in {429, 500, 502, 503, 504}:
                    break
            except Exception as exc:  # noqa: BLE001 - network errors are retried without bodies/keys
                last_reason = f"tavily_{type(exc).__name__}"
            if attempt < self.max_attempts:
                logger.warning("Tavily 检索第 %d/%d 次未成功(%s),将重试", attempt, self.max_attempts, last_reason)
        return TavilySearchResponse(
            False, clean_query, reason=last_reason,
            wall_ms=max(0, int((time.monotonic() - started_all) * 1000)),
        )


def _grounding_message(response: TavilySearchResponse) -> ChatMessage:
    sources = [
        {
            "title": hit.title,
            "url": hit.link,
            "publishedDate": hit.publish_date or None,
            "content": hit.content,
        }
        for hit in response.hits
    ]
    return ChatMessage(role="user", content=(
        "Tavily 已完成本次联网检索。下面 JSON 是唯一允许使用的联网证据；"
        "没有命中或字段缺失时必须明确说不知道，不得用训练记忆补写新闻。\n"
        + json.dumps({"query": response.query, "sources": sources}, ensure_ascii=False)
    ))


class TavilyGroundedProvider(LLMProvider):
    """Wrap a pure reasoning provider with Tavily evidence for legacy search tasks."""

    def __init__(self, inner: LLMProvider, search_client: TavilySearchClient) -> None:
        self.inner = inner
        self.search_client = search_client
        self.name = inner.name
        self.model = getattr(inner, "model", getattr(inner, "default_model", ""))

    def chat(
        self,
        messages: List[ChatMessage],
        *,
        enable_search: bool = True,
        search_query: Optional[str] = None,
        transport: Optional[Any] = None,
    ) -> LLMResult:
        if not enable_search:
            return self.inner.chat(messages, enable_search=False, transport=transport)
        query = (search_query or "").strip()
        if not query:
            query = next((str(m.content or "") for m in reversed(messages) if m.role == "user"), "")[:400]
        searched = self.search_client.search(query)
        if not searched.ok:
            return LLMResult(ok=False, reason=searched.reason, provider=self.name, model=self.model)
        result = self.inner.chat(messages + [_grounding_message(searched)], enable_search=False, transport=transport)
        if not result.ok:
            return result
        result.search_hits = list(searched.hits)
        result.search_engine = "tavily_basic"
        return result


__all__ = [
    "TAVILY_SEARCH_URL",
    "TavilySearchResponse",
    "TavilySearchClient",
    "TavilyGroundedProvider",
]
