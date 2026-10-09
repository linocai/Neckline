"""Strict, test-fixture-labelled normalization of B92 Jin10 structured results.

The isolated provider study retained call parameters and aggregate facts, not
raw response fields. An unfamiliar live shape is an explicit source failure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .ingestion import SqliteIngestionWriter
from .sources import SourceDocumentInput
from .jin10_mcp import Jin10Error


def _page(tool_name: str, structured: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], str | None, bool, str | None]:
    data = structured.get("data")
    if tool_name == "get_news":
        if not isinstance(data, Mapping):
            raise Jin10Error("article_shape_invalid")
        return [data], None, False, None
    if not isinstance(data, Mapping) or not isinstance(data.get("items"), list):
        raise Jin10Error("page_shape_invalid")
    rows = data["items"]
    more = data.get("has_more")
    if not isinstance(more, bool):
        raise Jin10Error("page_has_more_missing")
    # The live service uses an empty cursor on its final page. It is an
    # end-of-pagination marker only when has_more is explicitly false.
    cursors = [(field, data[field]) for field in ("next_offset", "next_cursor", "cursor")
               if field in data and data[field] is not None
               and not (more is False and data[field] == "")]
    if any(not isinstance(value, str) or not value for _, value in cursors):
        raise Jin10Error("page_cursor_invalid")
    if len({value for _, value in cursors}) > 1:
        raise Jin10Error("page_cursor_conflict")
    cursor = cursors[0][1] if cursors else None
    cursor_field = "offset" if cursors and cursors[0][0] == "next_offset" else "cursor"
    if more and cursor is None:
        raise Jin10Error("page_cursor_missing")
    return rows, cursor, more, cursor_field if cursor is not None else None


def _source_key(tool_name: str) -> str:
    return "jin10-flash" if tool_name.endswith("flash") else "jin10-news"


def _document(tool_name: str, item: Mapping[str, Any], obtained_at: datetime) -> SourceDocumentInput:
    source_key = _source_key(tool_name)
    url = item.get("url")
    external_id = item.get("id")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise Jin10Error("item_url_missing")
    provider_id = external_id if isinstance(external_id, str) and external_id else None
    if provider_id is None:
        external_id = url
    title = item.get("title")
    if title is not None and not isinstance(title, str):
        raise Jin10Error("item_title_invalid")
    if isinstance(title, str) and not title.strip():
        title = None
    text = item.get("content")
    intro = item.get("intro")
    introduction = item.get("introduction")
    if introduction is not None and not isinstance(introduction, str):
        raise Jin10Error("item_introduction_invalid")
    if isinstance(intro, str) and intro.strip() and isinstance(introduction, str) and introduction.strip() and intro != introduction:
        raise Jin10Error("item_introduction_conflict")
    if not intro and isinstance(introduction, str):
        intro = introduction
    if text is not None and not isinstance(text, str):
        raise Jin10Error("item_content_invalid")
    if intro is not None and not isinstance(intro, str):
        raise Jin10Error("item_intro_invalid")
    if source_key == "jin10-flash" and (not text or not text.strip()):
        raise Jin10Error("flash_content_missing")
    if source_key == "jin10-news" and not (text or intro or title):
        raise Jin10Error("article_summary_missing")
    raw_time = item.get("time")
    if not isinstance(raw_time, str) or not raw_time:
        raise Jin10Error("item_time_missing")
    try:
        parsed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    published = parsed if parsed is not None and parsed.tzinfo is not None else None
    if published is not None:
        try:
            published.astimezone(timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise Jin10Error("item_time_invalid") from exc
    precision = "exact" if published is not None else "unknown"
    body = text if text else None
    excerpt = intro or title if body is None else intro
    # The complete paid response is already durable. Only fields consumed by
    # this source item must be UTF-8; unrelated provider fields remain private.
    try:
        for value in (external_id, url, title, body, excerpt, provider_id, raw_time):
            if isinstance(value, str):
                value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise Jin10Error("item_text_invalid") from exc
    return SourceDocumentInput(
        external_id=external_id, canonical_url=url, original_text=body,
        excerpt=excerpt, published_at=published, published_precision=precision,
        fetched_at=obtained_at, fetch_version="jin10-mcp-2025-11-25",
        metadata={"contentKind": "original" if body else "excerpt",
                  "sourceKind": "flash" if source_key == "jin10-flash" else "article",
                  "title": title, "originalTitle": title, "provider": "Jin10",
                  "providerId": provider_id,
                  "originalPublishedText": raw_time},
    )


def persist_question_tool_result(
    *, task_id: str, tool_name: str, structured: Mapping[str, Any],
    obtained_at: datetime, question: str, target: str, db_path: Path,
    parent_ref: Mapping[str, Any] | None = None,
    leaseguard: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Persist exact source versions; return separate question/coverage context."""
    if obtained_at.tzinfo is None or not question.strip() or not target.strip():
        raise ValueError("工具证据需要问题、目标和带时区取得时间")
    if tool_name == "get_news" and (
        not isinstance(parent_ref, Mapping) or not isinstance(parent_ref.get("documentId"), str)
        or not isinstance(parent_ref.get("revision"), int)
    ):
        raise ValueError("get_news 必须绑定已知文章目录 ref")
    rows, cursor, has_more, cursor_field = _page(tool_name, structured)
    if tool_name == "get_news":
        from . import store
        parents = store.load_document_versions(refs=[parent_ref], db_path=db_path,
                                               source_keys=("jin10-news",))
        provider_id = parents[0]["metadata"].get("providerId") if len(parents) == 1 else None
        article = rows[0]
        if (not isinstance(provider_id, str) or not provider_id
                or article.get("id") != provider_id or not isinstance(article.get("content"), str)
                or not article["content"].strip()):
            raise Jin10Error("parent_article_mismatch")
    writer = SqliteIngestionWriter(db_path=db_path, leaseguard=leaseguard)
    refs: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for position, row in enumerate(rows):
        try:
            if not isinstance(row, Mapping):
                raise Jin10Error("page_item_invalid")
            doc = _document(tool_name, row, obtained_at)
        except Jin10Error as exc:
            if tool_name == "get_news":
                raise
            # Page envelope/cursor remains validated above. One unavailable
            # external item cannot erase its valid siblings or next page.
            rejected.append({"itemIndex": position, "reasonCode": exc.code})
            continue
        stored = writer.append_document_version(source_key=_source_key(tool_name), document=doc)
        refs.append({"documentId": stored.version.document_id, "revision": stored.version.revision,
                     "sourceKey": _source_key(tool_name), "contentSha256": stored.version.content_hash,
                     "publishedAt": doc.published_at.isoformat() if doc.published_at else None,
                     "fetchedAt": obtained_at.astimezone(timezone.utc).isoformat(timespec="seconds")})
    truncated = tool_name == "search_flash" and len(rows) >= 150
    coverage = {"state": "partial" if truncated or has_more or rejected else "completed",
                "resultCount": len(rows), "nextCursor": cursor,
                "acceptedCount": len(refs), "rejectedItems": rejected,
                "cursorField": cursor_field,
                "hasMore": has_more, "truncated": truncated,
                "absenceProven": False}
    return {"documentRefs": refs, "coverage": coverage,
            "question": question, "target": target,
            "parentRef": dict(parent_ref) if parent_ref else None,
            "obtainedAt": obtained_at.isoformat(), "taskId": task_id}


__all__ = ["persist_question_tool_result"]
