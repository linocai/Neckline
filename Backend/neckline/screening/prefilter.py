"""规则粗筛：按晨报窗口取消息、精确去重、只留直接点名公司池公司的消息；另取窗口开始前的相关消息供核对。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence

from .pool import CompanyPool
from .text import plain_text

_SOURCE_LABELS = {"jin10-flash": "金十快讯", "jin10-news": "金十资讯", "cninfo-announcement": "巨潮公告"}
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
    history: list[Message]           # 窗口开始前、点名候选公司的消息（只供核对）
    input_count: int
    duplicate_count: int
    unmatched_count: int


def to_message(doc: Mapping) -> Message:
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


def prefilter_window(docs: Iterable[Mapping], pool: CompanyPool) -> tuple[list[Message], int, int, int]:
    """窗口内的原件（每个文档一个版本）→ 去重、匹配公司池。返回（消息、窗口内条数、重复、未点名）。"""
    in_window = sorted((to_message(d) for d in docs), key=lambda m: (m.published_at, m.ref))
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
    return kept, len(in_window), duplicates, unmatched


def match_history(docs: Iterable[Mapping], candidates: CompanyPool) -> list[Message]:
    """窗口开始前的原件逐条匹配候选公司，只留下点名了候选公司的；可以边读边丢，不必整批进内存。"""
    history = []
    for doc in docs:
        message = to_message(doc)
        message.companies = candidates.match(message.title + "\n" + message.text)
        if message.companies:
            history.append(message)
    return sorted(history, key=lambda m: (m.published_at, m.ref))


def prefilter(docs: Sequence[Mapping], pool: CompanyPool, window: Window, *, history_days: int) -> PrefilterResult:
    """离线用：一次给全部原件，按窗口拆成本期与核对两部分。"""
    visible = as_of(docs, window.collected_by)
    in_window = [d for d in visible if window.start <= datetime.fromisoformat(d["publishedAt"]) <= window.end]
    kept, count, duplicates, unmatched = prefilter_window(in_window, pool)
    floor = window.start - timedelta(days=history_days)
    earlier = (d for d in visible if floor <= datetime.fromisoformat(d["publishedAt"]) < window.start)
    history = match_history(earlier, pool.subset({code for m in kept for code in m.companies}))
    return PrefilterResult(messages=kept, history=history, input_count=count,
                           duplicate_count=duplicates, unmatched_count=unmatched)
