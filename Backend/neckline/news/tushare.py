"""TuShare ``major_news``（新闻通讯长文）按时间段取数。

先按固定时长把窗口切成首尾相接的整秒片，再从新到旧逐片请求。已经覆盖过的区间跳过。
这个接口没有可靠的翻页游标：
某一片返回满 400 条就视为可能截断，丢掉这一片的结果，只把这一片对半切开再取。
切点落在整秒上，下一片从上一片结束后 1 秒开始，同一秒不会被两片重复计入。
每天调用次数有上限，超限立刻停止，已经取到的留下。
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


def _failure(response: Mapping[str, object]) -> str | None:
    code = response.get("code")
    if code == 0:
        return None
    msg = response.get("msg")
    # 只记错误类别。响应原文和 token 都不进入结果。
    if code == 40203 or (isinstance(msg, str) and "频率超限" in msg):
        return "quota_exceeded"
    return "upstream_rejected"


def _time_slices(start: datetime, end: datetime, slice_seconds: int) -> list[tuple[datetime, datetime]]:
    """[start, end] 切成互不重叠的整秒片，每片跨度不超过 slice_seconds，下一片从上片结束后 1 秒开始。"""
    slices: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor <= end:
        slice_end = min(cursor + timedelta(seconds=slice_seconds), end)
        slices.append((cursor, slice_end))
        cursor = slice_end + timedelta(seconds=1)
    return slices


def _items(post: Post, token: str, start: datetime, end: datetime) -> Sequence[Any]:
    response = post({"api_name": "major_news", "token": token, "fields": FIELDS,
                     "params": {"start_date": _wire_time(start), "end_date": _wire_time(end)}})
    failure = _failure(response)
    if failure:
        raise TuShareError(failure)
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


def _merged(intervals: Sequence[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[list[datetime]] = []
    for lo, hi in sorted(intervals):
        if lo > hi:
            lo, hi = hi, lo
        if not merged or lo > merged[-1][1] + timedelta(seconds=1):
            merged.append([lo, hi])
        else:
            merged[-1][1] = max(merged[-1][1], hi)
    return [(lo, hi) for lo, hi in merged]


def _uncovered(start: datetime, end: datetime, covered: Sequence[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """[start, end] 里还没被 covered 铺满的区间。相邻切片差 1 秒，这一秒不算缺口。"""
    clipped: list[tuple[datetime, datetime]] = []
    for lo, hi in covered:
        if lo > hi:
            lo, hi = hi, lo
        lo, hi = max(lo, start), min(hi, end)
        if lo <= hi:
            clipped.append((lo, hi))
    gaps: list[tuple[datetime, datetime]] = []
    cursor = start
    for lo, hi in _merged(clipped):
        if lo > cursor:
            gap_hi = lo - timedelta(seconds=1)
            if cursor <= gap_hi:
                gaps.append((cursor, gap_hi))
        cursor = max(cursor, hi + timedelta(seconds=1))
    if cursor <= end:
        gaps.append((cursor, end))
    return gaps


def clip_covered_to_lookback(intervals: Sequence[tuple[datetime, datetime]], *,
                             fetched_at: datetime, lookback_seconds: int) -> list[tuple[datetime, datetime]]:
    """复用覆盖时只留下「取到的时候已经过了回看时长」的部分。

    取数时刻是那条来源记录的 requestedEnd。回看窗里的切片下一轮还要再请求，
    否则迟到的新闻会被当成已经覆盖而漏掉。
    """
    cutoff = fetched_at - timedelta(seconds=lookback_seconds)
    kept: list[tuple[datetime, datetime]] = []
    for lo, hi in intervals:
        if lo > hi:
            lo, hi = hi, lo
        if hi > cutoff:
            hi = cutoff
        if lo <= hi:
            kept.append((lo, hi))
    return kept


def _note_covered(outcome: SourceOutcome, intervals: Sequence[tuple[datetime, datetime]]) -> None:
    if not intervals:
        return
    outcome.covered = [[lo.isoformat(), hi.isoformat()] for lo, hi in sorted(intervals)]


def fetch_major_news(post: Post, token: str | None, *, start: datetime, end: datetime, slice_seconds: int,
                     max_requests: int, clock: Callable[[], datetime],
                     covered: Sequence[tuple[datetime, datetime]] | None = None) -> SourceOutcome:
    outcome = SourceOutcome(source_key=SOURCE_KEY, requested_start=start, requested_end=end)
    if slice_seconds < 1:
        raise ValueError("slice_seconds 必须是正整数")
    if not token:
        outcome.error = "credential_missing"
        return outcome
    start_s, end_s = start.replace(microsecond=0), end.replace(microsecond=0)
    # 列表末尾是最新的一片。对半切时先压较早的一半、再压较新的一半，较新的会先被取走。
    if start_s <= end_s:
        slices: list[tuple[datetime, datetime]] = []
        for gap_lo, gap_hi in _uncovered(start_s, end_s, covered or ()):
            slices.extend(_time_slices(gap_lo, gap_hi, slice_seconds))
    else:
        slices = [(start_s, end_s)]
    pending = list(slices)
    fetched: list[tuple[datetime, datetime]] = []
    while pending:
        if outcome.requests >= max_requests:
            outcome.error = "request_bound_reached"
            _note_covered(outcome, fetched)
            return outcome
        lo, hi = pending.pop()
        try:
            items = _items(post, token, lo, hi)
        except TuShareError as exc:
            outcome.error = exc.code
            _note_covered(outcome, fetched)
            return outcome
        outcome.requests += 1
        if len(items) >= SATURATION:
            # 满额就可能被截断：这次的结果不要，切成两半重取，两半合起来覆盖同一段时间。
            seconds = int((hi - lo).total_seconds())
            if seconds < 1:
                outcome.error = "same_second_saturated"
                _note_covered(outcome, fetched)
                return outcome
            mid = lo + timedelta(seconds=seconds // 2)
            pending.append((lo, mid))
            pending.append((mid + timedelta(seconds=1), hi))
            continue
        fetched.append((lo, hi))
        fetched_at = clock()
        for item in items:
            document = _document(item, fetched_at)
            if document is None:
                outcome.rejected += 1
                continue
            outcome.documents.append(document)
    outcome.complete = True  # 个别字段缺失的记录只计入 rejected，不挡住整段覆盖
    return outcome
