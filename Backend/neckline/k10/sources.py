"""K10 资讯来源契约。

这里故意不提供网络客户端。接入者必须声明资料范围、授权、时间字段、水位和分页行为；
查询式搜索（例如 Tavily）只能作为指定对象的核验来源，不能注册为全市场入口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Protocol, Sequence

from .windows import ScanWindow


PUBLISHED_PRECISIONS = frozenset({"exact", "date", "unknown"})


@dataclass(frozen=True)
class SourceCoverage:
    source_key: str
    scope: str
    authorization: str
    pagination: str
    watermark_field: str
    publication_time_field: str
    is_market_wide: bool
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        required = {
            "source_key": self.source_key,
            "scope": self.scope,
            "authorization": self.authorization,
            "pagination": self.pagination,
            "watermark_field": self.watermark_field,
            "publication_time_field": self.publication_time_field,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(f"来源覆盖声明缺少：{', '.join(missing)}")
        if self.is_market_wide and self.pagination == "not_available":
            raise ValueError("声称全市场来源必须声明分页行为")


@dataclass(frozen=True)
class SourceDocumentInput:
    """一个取得版本。原文不可得时，必须保留可用摘录及原因。"""

    external_id: str
    canonical_url: str | None
    original_text: str | None
    excerpt: str | None
    published_at: datetime | None
    published_precision: str
    fetched_at: datetime
    fetch_version: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.external_id.strip():
            raise ValueError("来源资料必须有 external_id")
        if self.published_precision not in PUBLISHED_PRECISIONS:
            raise ValueError("published_precision 必须是 exact、date 或 unknown")
        if not (self.original_text or self.excerpt):
            raise ValueError("来源资料至少保留原文或可用摘录")
        if self.fetched_at.tzinfo is None:
            raise ValueError("fetched_at 必须带时区")
        if self.published_at is not None and self.published_at.tzinfo is None:
            raise ValueError("published_at 必须带时区")
        if not self.fetch_version.strip():
            raise ValueError("来源资料必须记录 fetch_version")
        if self.published_precision == "exact" and self.published_at is None:
            raise ValueError("exact 发布时间精度必须提供 published_at")


@dataclass(frozen=True)
class SourceFetchRequest:
    window: ScanWindow
    previous_cursor: str | None
    source_success_watermark: datetime | None


@dataclass(frozen=True)
class SourceFetchResult:
    """一次来源抓取的完整陈述；partial 结果也必须保留已取得资料。"""

    documents: Sequence[SourceDocumentInput]
    next_cursor: str | None
    success_watermark: datetime | None
    pages_fetched: int
    pages_expected: int | None
    exhausted: bool
    errors: tuple[str, ...] = ()
    late_document_count: int = 0
    unknown_publication_time_count: int = 0

    def __post_init__(self) -> None:
        if self.pages_fetched < 0:
            raise ValueError("pages_fetched 不能为负数")
        if self.pages_expected is not None and self.pages_expected < self.pages_fetched:
            raise ValueError("pages_expected 不能小于 pages_fetched")
        if self.success_watermark is not None and self.success_watermark.tzinfo is None:
            raise ValueError("success_watermark 必须带时区")
        if self.late_document_count < 0 or self.unknown_publication_time_count < 0:
            raise ValueError("来源统计不能为负数")

    @property
    def complete(self) -> bool:
        return self.success_watermark is not None and not self.errors and self.exhausted and (
            self.pages_expected is None or self.pages_fetched >= self.pages_expected
        )

    @property
    def can_advance_watermark(self) -> bool:
        return self.complete and self.success_watermark is not None

    def coverage_record(self, coverage: SourceCoverage) -> dict[str, object]:
        return {
            "sourceKey": coverage.source_key,
            "scope": coverage.scope,
            "authorization": coverage.authorization,
            "isMarketWide": coverage.is_market_wide,
            "pagination": coverage.pagination,
            "watermarkField": coverage.watermark_field,
            "publicationTimeField": coverage.publication_time_field,
            "limitations": list(coverage.limitations),
            "pagesFetched": self.pages_fetched,
            "pagesExpected": self.pages_expected,
            "exhausted": self.exhausted,
            "complete": self.complete,
            "errors": list(self.errors),
            "lateDocumentCount": self.late_document_count,
            "unknownPublicationTimeCount": self.unknown_publication_time_count,
        }


class SourceAdapter(Protocol):
    """来源适配器必须可注入，不能读取 `.env` 或自行选择默认账户。"""

    coverage: SourceCoverage

    def fetch_incremental(self, request: SourceFetchRequest) -> SourceFetchResult:
        ...


def validate_source_adapters(adapters: Sequence[SourceAdapter]) -> tuple[str, ...]:
    """返回配置缺项，空来源绝不等价于“没有消息”。"""
    if not adapters:
        return ("sourceAdapters",)
    keys = [adapter.coverage.source_key for adapter in adapters]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"来源 source_key 重复：{', '.join(duplicates)}")
    return ()


__all__ = [
    "PUBLISHED_PRECISIONS", "SourceAdapter", "SourceCoverage", "SourceDocumentInput",
    "SourceFetchRequest", "SourceFetchResult", "validate_source_adapters",
]
