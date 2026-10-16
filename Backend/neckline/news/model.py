"""来源原件与一次取数结果。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

PRECISIONS = frozenset({"exact", "date", "unknown"})


@dataclass(frozen=True)
class SourceDocument:
    source_key: str
    external_id: str
    url: str | None
    title: str | None
    original_text: str | None
    excerpt: str | None
    published_at: datetime | None
    published_precision: str
    fetched_at: datetime
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.external_id.strip():
            raise ValueError("原件必须有 external_id")
        if self.published_precision not in PRECISIONS:
            raise ValueError("published_precision 必须是 exact、date 或 unknown")
        if not (self.original_text or self.excerpt or self.title):
            raise ValueError("原件至少要有正文、摘要或标题之一")
        if self.fetched_at.tzinfo is None or (self.published_at is not None and self.published_at.tzinfo is None):
            raise ValueError("时间必须带时区")


@dataclass
class SourceOutcome:
    """一个来源一次取数的如实陈述：取到了什么、覆盖到哪、为什么没取全。"""
    source_key: str
    requested_start: datetime
    requested_end: datetime
    documents: list[SourceDocument] = field(default_factory=list)
    requests: int = 0
    complete: bool = False
    error: str | None = None
    rejected: int = 0
    oldest_seen: datetime | None = None
