"""规则粗筛：按晨报窗口取消息、精确去重、只留直接点名公司池公司的消息；另取窗口开始前的相关消息供核对。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence

from .pool import CompanyPool
from .text import plain_text

_SOURCE_LABELS = {"jin10-flash": "金十快讯", "jin10-news": "金十资讯"}
_BRACKET_TITLE = re.compile(r"^【([^】]{2,80})】")


@dataclass
class Message:
    document_id: str
    revision: int
    source_key: str
    source_label: str
    url: str | None
    title: str
    text: str
    published_at: datetime
    first_seen_at: datetime
    companies: dict[str, list[str]] = field(default_factory=dict)
    duplicates: list[str] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.document_id}#{self.revision}"


@dataclass
class Window:
    """晨报窗口：上个交易日收盘（start）到当天截止（end）。collected_by 是出报时已采到的边界。"""
    start: datetime
    end: datetime
    collected_by: datetime


@dataclass
class PrefilterResult:
    messages: list[Message]          # 窗口内、点名公司池的消息
    history: list[Message]           # 窗口开始前、点名公司池的消息（只供核对）
    input_count: int
    duplicate_count: int
    unmatched_count: int


def _message(doc: Mapping) -> Message:
    meta = doc.get("meta") or {}
    text = plain_text(doc.get("text") or doc.get("excerpt"))
    title = (meta.get("title") or meta.get("originalTitle") or "").strip()
    if not title and (bracket := _BRACKET_TITLE.match(text)):
        title = bracket.group(1).strip()
    label = _SOURCE_LABELS.get(doc["sourceKey"]) or (meta.get("source") or "TuShare 新闻")
    return Message(
        document_id=doc["documentId"], revision=doc["revision"], source_key=doc["sourceKey"],
        source_label=label, url=doc.get("url") or None, title=title, text=text,
        published_at=datetime.fromisoformat(doc["publishedAt"]),
        first_seen_at=datetime.fromisoformat(doc["firstSeenAt"]))


def as_of(docs: Iterable[Mapping], collected_by: datetime) -> list[Mapping]:
    """出报时能看到的原件：每个文档取 collected_by 之前抓到的最新版本。"""
    latest: dict[str, Mapping] = {}
    for doc in docs:
        if datetime.fromisoformat(doc["fetchedAt"]) > collected_by:
            continue
        current = latest.get(doc["documentId"])
        if current is None or doc["revision"] > current["revision"]:
            latest[doc["documentId"]] = doc
    return list(latest.values())


def prefilter(docs: Sequence[Mapping], pool: CompanyPool, window: Window, *, history_days: int) -> PrefilterResult:
    visible = [_message(d) for d in as_of(docs, window.collected_by)]
    in_window = sorted((m for m in visible if window.start <= m.published_at <= window.end),
                       key=lambda m: (m.published_at, m.ref))
    seen: dict[str, Message] = {}
    kept: list[Message] = []
    duplicates = unmatched = 0
    for message in in_window:
        key = hashlib.sha256(re.sub(r"\s+", "", message.title + message.text).encode()).hexdigest()
        if key in seen:
            seen[key].duplicates.append(message.ref)
            duplicates += 1
            continue
        seen[key] = message
        message.companies = pool.match(message.title + "\n" + message.text)
        if not message.companies:
            unmatched += 1
            continue
        kept.append(message)
    candidates = pool.subset({code for m in kept for code in m.companies})
    floor = window.start - timedelta(days=history_days)
    history = []
    for message in sorted((m for m in visible if floor <= m.published_at < window.start),
                          key=lambda m: (m.published_at, m.ref)):
        message.companies = candidates.match(message.title + "\n" + message.text)
        if message.companies:
            history.append(message)
    return PrefilterResult(messages=kept, history=history, input_count=len(in_window),
                           duplicate_count=duplicates, unmatched_count=unmatched)
