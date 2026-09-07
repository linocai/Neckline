"""TuShare ``major_news`` adapter for K10's authorised long-form news feed.

TuShare does not give this endpoint an offset cursor that can safely cover a
whole market window.  A response at the documented 400-record page boundary
is therefore treated as saturated and split into smaller *inclusive* time
intervals.  The split is made at a second boundary, never by moving the last
returned ``pub_time`` back a second, so messages sharing one timestamp cannot
silently disappear.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Callable, Mapping, Sequence

import httpx

from .sources import SourceCoverage, SourceDocumentInput, SourceFetchRequest, SourceFetchResult
from .windows import SHANGHAI


TUSHARE_API_URL = "https://api.tushare.pro"
MAJOR_NEWS_FIELDS = "pub_time,src,title,content"
SATURATION_RECORDS = 400
FETCH_VERSION = "tushare-major-news-v1"

Clock = Callable[[], datetime]
RequestCallable = Callable[[Mapping[str, object]], Mapping[str, object]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _request_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("TuShare major_news 时间必须带时区")
    if value.microsecond:
        raise ValueError("TuShare major_news 窗口必须精确到整秒，不能静默截断微秒")
    return value.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")


def _parse_pub_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed.astimezone(SHANGHAI)


def _stable_external_id(*, source: str, raw_pub_time: str, title: str) -> str:
    """Provider has no document id; text is deliberately absent from this identity.

    A later correction with the same provider/source/time/title becomes a new
    version of this document rather than a new logical document.
    """
    raw = "\x1f".join(("tushare-major-news", source, raw_pub_time, title))
    return "major_news_" + sha256(raw.encode("utf-8")).hexdigest()[:32]


class TuShareMajorNewsAdapter:
    """Market-wide TuShare news通讯 source with bounded, evidence-preserving fetches.

    ``token`` is supplied by the runtime integration.  This adapter never
    reads settings, environment variables or a local data directory.
    """

    coverage = SourceCoverage(
        source_key="tushare-major-news",
        scope="TuShare major_news long-form communications",
        authorization="runtime-injected TuShare token",
        pagination="recursive time partitions when a request returns >=400 records",
        watermark_field="successful request cutoff_at",
        publication_time_field="pub_time",
        is_market_wide=True,
        limitations=(
            "only TuShare major_news communications",
            "announcements, flashes and public-reply sources are not connected",
            "a same-second saturated response is partial and does not advance the watermark",
        ),
    )

    def __init__(
        self,
        *,
        token: str,
        src: str | None = None,
        request_bound: int = 128,
        transport: httpx.BaseTransport | None = None,
        request_callable: RequestCallable | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        if not isinstance(token, str) or not token.strip():
            raise ValueError("TuShare token 必须由运行时显式注入")
        if src is not None and (not isinstance(src, str) or not src.strip()):
            raise ValueError("src 缺省或为非空来源名")
        if not isinstance(request_bound, int) or isinstance(request_bound, bool) or request_bound < 1:
            raise ValueError("request_bound 必须是正整数")
        if transport is not None and request_callable is not None:
            raise ValueError("transport 与 request_callable 只能提供其一")
        self._token = token.strip()
        self._src = src.strip() if src else None
        self._request_bound = request_bound
        self._transport = transport
        self._request_callable = request_callable
        self._clock = clock

    def fetch_incremental(self, request: SourceFetchRequest) -> SourceFetchResult:
        window = request.window
        start = window.start_at
        cutoff = window.cutoff_at
        if start is None:
            return self._partial((), 0, "window_start_missing")
        if start.tzinfo is None or cutoff.tzinfo is None:
            raise ValueError("K10 SourceFetchRequest window 必须带时区")
        if start == cutoff and not (window.start_inclusive and window.cutoff_inclusive):
            return self._complete((), 0, cutoff)

        fetched_at = self._clock()
        if fetched_at.tzinfo is None:
            raise ValueError("TuShare major_news clock 必须返回带时区时间")
        pending: list[tuple[datetime, datetime]] = [(start, cutoff)]
        documents: list[SourceDocumentInput] = []
        errors: list[str] = []
        requests_made = 0
        unknown_publication_time_count = 0
        late_document_count = 0

        while pending:
            if requests_made >= self._request_bound:
                errors.append("request_bound_reached")
                break
            interval_start, interval_end = pending.pop()
            try:
                items = self._request_items(interval_start, interval_end)
            except _UpstreamFailure as exc:
                errors.append(exc.code)
                break
            requests_made += 1
            converted, unknown, invalid = self._documents_from_items(items, fetched_at=fetched_at, request=request)
            documents.extend(converted)
            unknown_publication_time_count += unknown
            if invalid:
                errors.append("invalid_major_news_record")
                break
            late_document_count += sum(
                1 for document in converted
                if document.published_at is not None
                and document.published_at <= cutoff
                and document.fetched_at > cutoff
            )
            if len(items) < SATURATION_RECORDS:
                continue
            split = self._split_interval(interval_start, interval_end)
            if split is None:
                errors.append("same_second_saturated")
                break
            left, right = split
            # LIFO: right is queued first so the earlier half is fetched first.
            pending.append(right)
            pending.append(left)

        if errors:
            return SourceFetchResult(
                documents=tuple(documents), next_cursor=None, success_watermark=None,
                pages_fetched=requests_made, pages_expected=None, exhausted=False, errors=tuple(errors),
                late_document_count=late_document_count,
                unknown_publication_time_count=unknown_publication_time_count,
            )
        return self._complete(
            tuple(documents), requests_made, cutoff, late_document_count=late_document_count,
            unknown_publication_time_count=unknown_publication_time_count,
        )

    def _request_items(self, start: datetime, end: datetime) -> Sequence[object]:
        params: dict[str, object] = {"start_date": _request_time(start), "end_date": _request_time(end)}
        if self._src is not None:
            params["src"] = self._src
        payload: dict[str, object] = {
            "api_name": "major_news", "token": self._token, "params": params, "fields": MAJOR_NEWS_FIELDS,
        }
        if self._request_callable is not None:
            try:
                response = self._request_callable(payload)
            except Exception as exc:  # never leak provider exceptions or request bodies into coverage
                raise _UpstreamFailure("transport_error") from exc
        else:
            try:
                with httpx.Client(transport=self._transport, timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                    http_response = client.post(TUSHARE_API_URL, json=payload)
            except httpx.HTTPError as exc:
                raise _UpstreamFailure("transport_error") from exc
            if http_response.status_code != 200:
                raise _UpstreamFailure("upstream_http_error")
            try:
                response = http_response.json()
            except ValueError as exc:
                raise _UpstreamFailure("invalid_upstream_response") from exc
        if not isinstance(response, Mapping):
            raise _UpstreamFailure("invalid_upstream_response")
        if response.get("code") != 0:
            raise _UpstreamFailure("upstream_rejected")
        data = response.get("data")
        if not isinstance(data, Mapping):
            raise _UpstreamFailure("invalid_upstream_response")
        items = data.get("items")
        if not isinstance(items, list):
            raise _UpstreamFailure("invalid_upstream_response")
        fields = data.get("fields")
        if fields is not None and list(fields) != MAJOR_NEWS_FIELDS.split(","):
            raise _UpstreamFailure("unexpected_upstream_fields")
        return items

    def _documents_from_items(
        self, items: Sequence[object], *, fetched_at: datetime, request: SourceFetchRequest,
    ) -> tuple[list[SourceDocumentInput], int, bool]:
        documents: list[SourceDocumentInput] = []
        unknown_count = 0
        invalid = False
        for item in items:
            record = self._record(item)
            if record is None:
                invalid = True
                continue
            raw_pub_time = record["pub_time"]
            published_at = _parse_pub_time(raw_pub_time)
            if published_at is not None and not request.window.contains(published_at):
                # The API interval is inclusive, whereas K10 windows may have an open boundary.
                continue
            if published_at is None:
                unknown_count += 1
            source = record["src"]
            title = record["title"]
            documents.append(SourceDocumentInput(
                external_id=_stable_external_id(source=source, raw_pub_time=raw_pub_time, title=title),
                canonical_url=None, original_text=record["content"], excerpt=None,
                published_at=published_at, published_precision="exact" if published_at is not None else "unknown",
                fetched_at=fetched_at, fetch_version=FETCH_VERSION,
                metadata={
                    "provider": "tushare", "source": source or None, "title": title,
                    "rawPubTime": raw_pub_time,
                },
            ))
        return documents, unknown_count, invalid

    @staticmethod
    def _record(item: object) -> dict[str, str] | None:
        if isinstance(item, Mapping):
            values = {key: item.get(key) for key in MAJOR_NEWS_FIELDS.split(",")}
        elif isinstance(item, list) and len(item) == 4:
            values = dict(zip(MAJOR_NEWS_FIELDS.split(","), item, strict=True))
        else:
            return None
        if not all(isinstance(values[key], str) for key in ("pub_time", "src", "title", "content")):
            return None
        if not values["title"].strip() or not values["content"].strip():
            return None
        return {key: values[key].strip() for key in MAJOR_NEWS_FIELDS.split(",")}

    @staticmethod
    def _split_interval(start: datetime, end: datetime) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]] | None:
        start_second = start.replace(microsecond=0)
        end_second = end.replace(microsecond=0)
        if start_second >= end_second:
            return None
        seconds = int((end_second - start_second).total_seconds())
        midpoint = start_second + timedelta(seconds=seconds // 2)
        if midpoint < start_second or midpoint >= end_second:
            return None
        return (start, midpoint), (midpoint + timedelta(seconds=1), end)

    @staticmethod
    def _complete(
        documents: Sequence[SourceDocumentInput], pages_fetched: int, cutoff: datetime, *,
        late_document_count: int = 0, unknown_publication_time_count: int = 0,
    ) -> SourceFetchResult:
        return SourceFetchResult(
            documents=tuple(documents), next_cursor=None, success_watermark=cutoff,
            pages_fetched=pages_fetched, pages_expected=pages_fetched, exhausted=True,
            late_document_count=late_document_count,
            unknown_publication_time_count=unknown_publication_time_count,
        )

    @staticmethod
    def _partial(documents: Sequence[SourceDocumentInput], pages_fetched: int, error: str) -> SourceFetchResult:
        return SourceFetchResult(
            documents=tuple(documents), next_cursor=None, success_watermark=None,
            pages_fetched=pages_fetched, pages_expected=None, exhausted=False, errors=(error,),
        )


class _UpstreamFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


__all__ = [
    "FETCH_VERSION", "MAJOR_NEWS_FIELDS", "SATURATION_RECORDS", "TUSHARE_API_URL",
    "TuShareMajorNewsAdapter",
]
