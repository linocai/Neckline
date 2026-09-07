"""Query-based Tavily evidence transport; K10 separately archives and reasons over hits."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple
from urllib.parse import urlparse

from neckline.llm.base import SearchHit

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


def _published_date(item: dict) -> str:
    # Dates mentioned in a title/body can describe earlier events. Only a source's
    # publication field is eligible for K10's fixed-time boundary checks.
    explicit = item.get("published_date", item.get("publishedDate"))
    return str(explicit).strip()[:64] if explicit else ""


class TavilySearchClient:
    """Small synchronous client using Tavily's documented Bearer endpoint.

    Basic general search keeps Chinese issuer announcements in scope; the finance
    and news verticals did not reliably retrieve those pages in the live probe.
    Five results, no generated answer and no raw full-page body keep evidence bounded.
    Publication time must be separately verified when absent from the search response.
    """

    provider = "tavily"
    search_depth = "basic"
    max_results = 5
    topic = "general"
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
                    usage_available = isinstance(credits, int) and not isinstance(credits, bool) and credits >= 0
                    if not usage_available:
                        credits = None
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
                        usage_available, clean_query, hits=tuple(hits), credits=credits,
                        reason="ok" if usage_available else "tavily_usage_unavailable",
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


__all__ = ["TAVILY_SEARCH_URL", "TavilySearchResponse", "TavilySearchClient"]
