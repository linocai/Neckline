"""TuShare ``major_news``（新闻通讯长文）按时间段取数。

这个接口没有可靠的翻页游标：一次返回满 400 条就视为可能截断，把时间段对半切开再取，
切点落在整秒上，同一秒的消息不会悄悄漏掉。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

import httpx

from .model import SourceDocument, SourceOutcome

API_URL = "https://api.tushare.pro"
FIELDS = "pub_time,src,title,content"
SATURATION = 400
SOURCE_KEY = "tushare-major-news"
CST = timezone(timedelta(hours=8))

Post = Callable[[Mapping[str, object]], Mapping[str, object]]


class TuShareError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def http_post(timeout_seconds: float) -> Post:
    def post(payload: Mapping[str, object]) -> Mapping[str, object]:
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=5.0), follow_redirects=False) as client:
                response = client.post(API_URL, json=dict(payload))
        except httpx.HTTPError as exc:
            raise TuShareError("transport_error") from exc
        if response.status_code != 200:
            raise TuShareError("http_error")
        try:
            body = response.json()
        except ValueError as exc:
            raise TuShareError("response_invalid") from exc
        if not isinstance(body, Mapping):
            raise TuShareError("response_invalid")
        return body
    return post


def _wire_time(value: datetime) -> str:
    return value.astimezone(CST).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _parse(raw: str) -> tuple[datetime | None, str]:
    text = raw.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None, "unknown"
    if len(text) == 10:
        return parsed.replace(tzinfo=CST), "date"
    return (parsed.replace(tzinfo=CST) if parsed.tzinfo is None else parsed), "exact"


def _items(post: Post, token: str, start: datetime, end: datetime) -> Sequence[Any]:
    response = post({"api_name": "major_news", "token": token, "fields": FIELDS,
                     "params": {"start_date": _wire_time(start), "end_date": _wire_time(end)}})
    if response.get("code") != 0:
        raise TuShareError("upstream_rejected")
    data = response.get("data")
    if not isinstance(data, Mapping) or not isinstance(data.get("items"), list):
        raise TuShareError("response_invalid")
    fields = data.get("fields")
    if fields is not None and list(fields) != FIELDS.split(","):
        raise TuShareError("unexpected_fields")
    return data["items"]


def _document(item: Any, fetched_at: datetime) -> SourceDocument | None:
    if isinstance(item, Mapping):
        values = {key: item.get(key) for key in FIELDS.split(",")}
    elif isinstance(item, list) and len(item) == 4:
        values = dict(zip(FIELDS.split(","), item))
    else:
        return None
    if not all(isinstance(values[k], str) for k in values) or not values["title"].strip() or not values["content"].strip():
        return None
    raw_time, source, title = values["pub_time"].strip(), values["src"].strip(), values["title"].strip()
    published, precision = _parse(raw_time)
    external_id = hashlib.sha256("\x1f".join((SOURCE_KEY, source, raw_time, title)).encode()).hexdigest()
    return SourceDocument(source_key=SOURCE_KEY, external_id=f"major_news_{external_id[:32]}", url=None, title=title,
                          original_text=values["content"].strip(), excerpt=None, published_at=published,
                          published_precision=precision, fetched_at=fetched_at,
                          metadata={"provider": "tushare", "source": source or None, "rawPubTime": raw_time})


def fetch_major_news(post: Post, token: str | None, *, start: datetime, end: datetime, max_requests: int,
                     clock: Callable[[], datetime]) -> SourceOutcome:
    outcome = SourceOutcome(source_key=SOURCE_KEY, requested_start=start, requested_end=end)
    if not token:
        outcome.error = "credential_missing"
        return outcome
    pending = [(start.replace(microsecond=0), end.replace(microsecond=0))]
    while pending:
        if outcome.requests >= max_requests:
            outcome.error = "request_bound_reached"
            return outcome
        lo, hi = pending.pop()
        try:
            items = _items(post, token, lo, hi)
        except TuShareError as exc:
            outcome.error = exc.code
            return outcome
        outcome.requests += 1
        if len(items) >= SATURATION:
            # 满额就可能被截断：这次的结果不要，切成两半重取，两半合起来覆盖同一段时间。
            seconds = int((hi - lo).total_seconds())
            if seconds < 1:
                outcome.error = "same_second_saturated"
                return outcome
            mid = lo + timedelta(seconds=seconds // 2)
            pending.append((mid + timedelta(seconds=1), hi))
            pending.append((lo, mid))
            continue
        fetched_at = clock()
        for item in items:
            document = _document(item, fetched_at)
            if document is None:
                outcome.rejected += 1
                continue
            outcome.documents.append(document)
    outcome.complete = True  # 个别字段缺失的记录只计入 rejected，不挡住整段覆盖
    return outcome
