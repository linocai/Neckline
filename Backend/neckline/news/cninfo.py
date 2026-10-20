"""巨潮资讯（cninfo.com.cn）公开公告查询：创业板上市公司公告，只取标题，不解析 PDF。

按 [start, end] 覆盖的北京时间日期取 seDate，从第 1 页翻到取完（服务端每页最多 30 条），再只留发布时间落在
[start, end] 内的公告。翻页上限由配置给出，超限如实记为 page_limit（不完整）。每页最多重试一次，
页与页之间留间隔，不给公开接口压力。网络、状态码、非 JSON、结构不对都只记为这个来源的错误，不影响别的来源。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import httpx

from .model import SourceDocument, SourceOutcome

SOURCE_KEY = "cninfo-announcement"
QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
FILE_BASE = "http://static.cninfo.com.cn/"
PAGE_SIZE = 30  # 服务端上限，传更大的值也只给 30
PAGE_DELAY_SECONDS = 0.5
CST = timezone(timedelta(hours=8))

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "http://www.cninfo.com.cn",
    "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
}

_EM = re.compile(r"</?em\s*>", re.IGNORECASE)
_ADJUNCT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-./]*")


class CninfoError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _form(page: int, se_date: str) -> dict[str, str]:
    return {"pageNum": str(page), "pageSize": str(PAGE_SIZE), "column": "szse", "tabName": "fulltext",
            "plate": "szcy", "stock": "", "searchkey": "", "secid": "", "category": "", "trade": "",
            "seDate": se_date, "sortName": "", "sortType": "", "isHLtitle": "true"}


def _clean(value: object) -> str | None:
    """去掉命中高亮的 <em> 标签并规整空白；不是非空字符串返回 None。"""
    if not isinstance(value, str):
        return None
    text = " ".join(_EM.sub("", value).split())
    return text or None


def _plain_number(value: object) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _plain_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _file_url(adjunct: object) -> str | None:
    if not isinstance(adjunct, str) or not _ADJUNCT.fullmatch(adjunct) or ".." in adjunct:
        return None
    return FILE_BASE + adjunct


def normalize_item(item: object, fetched_at: datetime) -> SourceDocument:
    """一条公告转成 SourceDocument；缺少必需字段抛 CninfoError（调用方计入 rejected）。"""
    if not isinstance(item, Mapping):
        raise CninfoError("item_invalid")
    raw_id = item.get("announcementId")
    if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)) or not str(raw_id).strip():
        raise CninfoError("item_id_missing")
    code, name, title = _clean(item.get("secCode")), _clean(item.get("secName")), _clean(item.get("announcementTitle"))
    if not (code and name and title):
        raise CninfoError("item_text_missing")
    millis = _plain_number(item.get("announcementTime"))
    if millis is None:
        raise CninfoError("item_time_missing")
    try:
        published = datetime.fromtimestamp(int(millis) // 1000, tz=CST)
    except (OverflowError, OSError, ValueError) as exc:
        raise CninfoError("item_time_invalid") from exc
    return SourceDocument(
        source_key=SOURCE_KEY, external_id=str(raw_id).strip(), url=_file_url(item.get("adjunctUrl")),
        title=f"{name}：{title}", original_text=f"{name}（{code}）公告：{title}", excerpt=None,
        published_at=published, published_precision="exact", fetched_at=fetched_at,
        metadata={"provider": "cninfo", "secCode": code, "orgId": _plain_text(item.get("orgId")),
                  "adjunctType": _plain_text(item.get("adjunctType")),
                  "adjunctSize": _plain_number(item.get("adjunctSize"))})


def _post_page(client: httpx.Client, form: Mapping[str, str]) -> Any:
    try:
        response = client.post(QUERY_URL, data=dict(form))
    except httpx.HTTPError as exc:
        raise CninfoError("transport_error") from exc
    if response.status_code != 200:
        raise CninfoError("http_error")
    try:
        return json.loads(response.content)
    except ValueError as exc:
        raise CninfoError("json_invalid") from exc


def _parse_page(body: object) -> tuple[list[Any], int, bool]:
    if not isinstance(body, Mapping):
        raise CninfoError("page_shape_invalid")
    total, more, rows = body.get("totalAnnouncement"), body.get("hasMore"), body.get("announcements")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0 or not isinstance(more, bool):
        raise CninfoError("page_shape_invalid")
    if rows is None and not more:  # 当天一条都没有时，接口给 null
        rows = []
    if not isinstance(rows, list):
        raise CninfoError("page_shape_invalid")
    return rows, total, more


def fetch_announcements(*, start: datetime, end: datetime, max_pages: int, timeout_seconds: float,
                        clock: Callable[[], datetime], transport: httpx.BaseTransport | None = None,
                        sleep: Callable[[float], None] = time.sleep) -> SourceOutcome:
    outcome = SourceOutcome(source_key=SOURCE_KEY, requested_start=start, requested_end=end)
    floor = start.replace(microsecond=0)
    if floor > end:
        outcome.error = "window_invalid"
        return outcome
    se_date = f"{start.astimezone(CST):%Y-%m-%d}~{end.astimezone(CST):%Y-%m-%d}"
    pages = raw_seen = 0
    seen_ids: set[str] = set()
    with httpx.Client(transport=transport, timeout=httpx.Timeout(timeout_seconds, connect=5.0),
                      follow_redirects=False, headers=HEADERS) as client:
        try:
            while True:
                if pages >= max_pages:
                    outcome.error = "page_limit"
                    return outcome
                if pages:
                    sleep(PAGE_DELAY_SECONDS)
                form = _form(pages + 1, se_date)
                for attempt in (1, 2):  # 每页最多重试一次；结构不对不重试
                    outcome.requests += 1
                    try:
                        body = _post_page(client, form)
                        break
                    except CninfoError:
                        if attempt == 2:
                            raise
                        sleep(PAGE_DELAY_SECONDS)
                pages += 1
                rows, total, has_more = _parse_page(body)
                raw_seen += len(rows)
                fetched_at = clock()
                for row in rows:
                    try:
                        document = normalize_item(row, fetched_at)
                    except CninfoError:
                        outcome.rejected += 1
                        continue
                    if document.external_id in seen_ids or not (floor <= document.published_at <= end):
                        continue
                    seen_ids.add(document.external_id)
                    outcome.documents.append(document)
                    if outcome.oldest_seen is None or document.published_at < outcome.oldest_seen:
                        outcome.oldest_seen = document.published_at
                if raw_seen >= total:
                    outcome.complete = True
                    return outcome
                if not has_more:
                    outcome.error = "count_mismatch"  # 接口说没有下一页，却没给够它自己报的总数
                    return outcome
                if not rows:
                    outcome.error = "pagination_stalled"
                    return outcome
        except CninfoError as exc:
            outcome.error = exc.code
            return outcome
