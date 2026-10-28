"""巨潮资讯（cninfo.com.cn）公开公告查询：创业板上市公司公告，只取标题，不解析 PDF。

按 [start, end] 覆盖的北京时间日期取 seDate，用接口默认排序（按 announcementTime 倒序、并列稳定）从第 1 页往后翻（每页最多 30 条），
只留发布时间落在 [start, end] 内的公告；某一页一旦出现早于起点 floor 的公告即视为已翻过窗口起点，判完整并停止。
已经见过的 announcementId 计入重复并跳过顺序检查。只对第一次见到的条目检查页内与跨页时间不增（相等允许），无法解析时间的条目不参与比较，乱序如实记 order_unexpected（不完整，已收保留）。
跨页重复超过一页的量记 pagination_unstable；翻页途中有新公告插入（总数变化或跨页重复）时从头再核对一遍，仍在变就记 pagination_shifted。翻页上限仅防异常循环，超限记 page_limit。每页最多重试一次，页间留间隔。网络、状态码、非 JSON、结构不对只记本来源错误。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import httpx

from .model import SourceDocument, SourceOutcome

SOURCE_KEY = "cninfo-announcement"
QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
FILE_BASE = "http://static.cninfo.com.cn/"
PAGE_SIZE = 30  # 服务端上限，传更大的值也只给 30
PAGE_DELAY_SECONDS = 0.5
# 默认排序本身按时间倒序，并列时刻的先后也稳定。显式传 sortName=time 在并列时刻翻页会重复、漏项：
# 10/28 实测 10/26～10/27 共 1,708 条，按 time 倒序翻完只拿到 1,363 条不重复的，默认排序 1,708 条全齐。
# 稳定排序下跨页重复只来自翻页途中插到最前面的新公告，超过一页的量就说明分页不稳，如实记为不完整。
REPEAT_LIMIT = PAGE_SIZE
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


def _row_published_at(row: object) -> datetime | None:
    if not isinstance(row, Mapping):
        return None
    millis = _plain_number(row.get("announcementTime"))
    if millis is None:
        return None
    try:
        return datetime.fromtimestamp(int(millis) // 1000, tz=CST)
    except (OverflowError, OSError, ValueError):
        return None


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


@dataclass
class _Pass:
    error: str | None = None
    shifted: bool = False  # 这一遍里总数变了或出现跨页重复：翻页途中有新公告插进来


def _one_pass(client: httpx.Client, outcome: SourceOutcome, *, se_date: str, floor: datetime, end: datetime,
              max_pages: int, budget: dict, seen_ids: set[str], clock: Callable[[], datetime],
              sleep: Callable[[float], None], verifying: bool = False) -> _Pass:
    """从第 1 页翻到越过起点（或取完）。文档去重靠跨遍共享的 seen_ids；顺序与重复检查每遍单独做。"""
    raw_seen = repeats = 0
    totals: set[int] = set()
    row_ids: set[str] = set()  # 这一遍翻到过的全部条目（不论是否在窗口内），用来发现分页不稳
    prev_page_last_time: datetime | None = None
    page = 0
    while True:
        if budget["pages"] >= max_pages:
            return _Pass(error="page_limit")
        if budget["pages"]:
            sleep(PAGE_DELAY_SECONDS)
        form = _form(page + 1, se_date)
        for attempt in (1, 2):  # 每页最多重试一次；结构不对不重试
            outcome.requests += 1
            try:
                body = _post_page(client, form)
                break
            except CninfoError:
                if attempt == 2:
                    raise
                sleep(PAGE_DELAY_SECONDS)
        page += 1
        budget["pages"] += 1
        rows, total, has_more = _parse_page(body)
        totals.add(total)
        raw_seen += len(rows)
        fetched_at = clock()
        page_times: list[datetime] = []
        for row in rows:
            raw_id = row.get("announcementId") if isinstance(row, Mapping) else None
            if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool):
                key = str(raw_id)
                if key in row_ids:
                    repeats += 1
                    if repeats > REPEAT_LIMIT:
                        return _Pass(error="pagination_unstable")
                    continue  # 翻页途中插到前面的新公告会让下一页开头重复上一页末尾，不参与顺序比较
                row_ids.add(key)
            when = _row_published_at(row)
            if when is not None:
                if page_times and when > page_times[-1]:
                    return _Pass(error="order_unexpected")
                if prev_page_last_time is not None and not page_times and when > prev_page_last_time:
                    return _Pass(error="order_unexpected")
                page_times.append(when)
            try:
                document = normalize_item(row, fetched_at)
            except CninfoError:
                if not verifying:  # 核对那一遍会再遇到同样的坏条目，不重复计数
                    outcome.rejected += 1
                continue
            if document.external_id in seen_ids or not (floor <= document.published_at <= end):
                continue
            seen_ids.add(document.external_id)
            outcome.documents.append(document)
            if outcome.oldest_seen is None or document.published_at < outcome.oldest_seen:
                outcome.oldest_seen = document.published_at
        if page_times:
            prev_page_last_time = page_times[-1]
        # 第一遍：出现跨页重复或总数变化就要再核对；核对那一遍只看总数是否还在变（静态的重复条目不算在变）
        shifted = len(totals) > 1 or (repeats > 0 and not verifying)
        if any(when < floor for when in page_times):
            return _Pass(shifted=shifted)
        if raw_seen >= total:
            return _Pass(shifted=shifted)
        if not has_more:
            return _Pass(error="count_mismatch")  # 接口说没有下一页，却没给够它自己报的总数
        if not rows:
            return _Pass(error="pagination_stalled")


def fetch_announcements(*, start: datetime, end: datetime, max_pages: int, timeout_seconds: float,
                        clock: Callable[[], datetime], transport: httpx.BaseTransport | None = None,
                        sleep: Callable[[float], None] = time.sleep) -> SourceOutcome:
    outcome = SourceOutcome(source_key=SOURCE_KEY, requested_start=start, requested_end=end)
    floor = start.replace(microsecond=0)
    if floor > end:
        outcome.error = "window_invalid"
        return outcome
    se_date = f"{start.astimezone(CST):%Y-%m-%d}~{end.astimezone(CST):%Y-%m-%d}"
    seen_ids: set[str] = set()
    budget = {"pages": 0}  # 翻页上限对两遍合计
    args = dict(se_date=se_date, floor=floor, end=end, max_pages=max_pages, budget=budget, seen_ids=seen_ids,
                clock=clock, sleep=sleep)
    with httpx.Client(transport=transport, timeout=httpx.Timeout(timeout_seconds, connect=5.0),
                      follow_redirects=False, headers=HEADERS) as client:
        try:
            first = _one_pass(client, outcome, **args)
            if first.error is not None:
                outcome.error = first.error
                return outcome
            if first.shifted:
                # 翻页途中有新公告进来：插进已翻过位置的公告这一遍取不到，从头再核对一遍（已取到的不重复收）
                second = _one_pass(client, outcome, verifying=True, **args)
                if second.error is not None:
                    outcome.error = second.error
                    return outcome
                if second.shifted:
                    outcome.error = "pagination_shifted"  # 核对这一遍还在变，没法证明取全
                    return outcome
            outcome.complete = True
            return outcome
        except CninfoError as exc:
            outcome.error = exc.code
            return outcome
