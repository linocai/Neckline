"""B122：巨潮公告来源（cninfo-announcement）。

只用 httpx.MockTransport 和临时库，绝不联网；业务时钟与翻页间隔都显式注入。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from importlib import resources
from urllib.parse import parse_qs

import httpx
import pytest

from neckline.morning import api as morning_api
from neckline.morning import db
from neckline.morning.collect import SOURCES, run_collection
from neckline.morning.report import LATE_SOURCES, SOURCE_LABELS, coverage_gaps, load_config
from neckline.morning.schedule import morning_window
from neckline.news import cninfo
from neckline.screening.prefilter import prefilter_window

CST = timezone(timedelta(hours=8))
KEY = "cninfo-announcement"
TITLE = "关于持股5%以上股东拟协议转让公司股份公开征集受让方的公告"


def at(day: str, hms: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hms if len(hms) > 5 else hms + ':00'}+08:00")


def ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def ann(i: int, when: datetime, *, name: str = "某某科技", code: str = "300001", title: str = "关于召开股东大会的通知",
        **extra) -> dict:
    row = {"announcementId": str(1225610000 + i), "secCode": code, "secName": name, "orgId": f"gssz{code}",
           "announcementTitle": title, "announcementTime": ms(when),
           "adjunctUrl": f"finalpage/{when:%Y-%m-%d}/{1225610000 + i}.PDF", "adjunctType": "PDF", "adjunctSize": 321}
    row.update(extra)
    return row


class Server:
    """按 pageNum 切页的假巨潮；默认按 announcementTime 倒序（与真接口一致）。script 里按页放步骤，sort=False 可关排序。"""

    def __init__(self, rows: list[dict], *, script: dict[int, list] | None = None, sort: bool = True) -> None:
        if sort:
            def _time_key(r: object) -> int | float:
                if not isinstance(r, dict):
                    return 0
                t = r.get("announcementTime")
                return t if isinstance(t, (int, float)) and not isinstance(t, bool) else 0

            self.rows = sorted(rows, key=_time_key, reverse=True)
        else:
            self.rows = rows
        self.script = script or {}
        self.requests: list[tuple[httpx.Request, dict[str, str]]] = []

    @property
    def forms(self) -> list[dict[str, str]]:
        return [form for _, form in self.requests]

    def page(self, number: int) -> dict:
        chunk = self.rows[(number - 1) * cninfo.PAGE_SIZE:number * cninfo.PAGE_SIZE]
        return {"totalAnnouncement": len(self.rows), "hasMore": number * cninfo.PAGE_SIZE < len(self.rows),
                "announcements": chunk or None}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()}
        self.requests.append((request, form))
        number = int(form["pageNum"])
        queued = self.script.get(number)
        if queued:
            step = queued.pop(0)
            if isinstance(step, Exception):
                raise step
            return step()
        return httpx.Response(200, json=self.page(number))


def status(code: int, text: str = ""):
    return lambda: httpx.Response(code, text=text)


def body(payload):
    return lambda: httpx.Response(200, json=payload)


def fetch(server: Server, *, start: datetime, end: datetime, max_pages: int = 40, sleeps: list | None = None):
    sleeps = [] if sleeps is None else sleeps
    return cninfo.fetch_announcements(start=start, end=end, max_pages=max_pages, timeout_seconds=5,
                                      clock=lambda: end, transport=httpx.MockTransport(server), sleep=sleeps.append)


WINDOW = (at("2026-10-19", "15:00"), at("2026-10-20", "08:00"))


# ------------------------------------------------------------------ 取数

def test_request_shape_and_field_mapping():
    when = at("2026-10-19", "18:12:34")
    row = ann(7, when, name="<em>中创</em>环保", code="300056", title=f"<em>{TITLE}</em>",
              orgId="gssz0300056", adjunctSize=987)
    server = Server([row])
    sleeps: list = []
    outcome = fetch(server, start=WINDOW[0], end=WINDOW[1], sleeps=sleeps)

    assert outcome.complete and outcome.error is None and outcome.requests == 1 and sleeps == []
    request, form = server.requests[0]
    assert request.method == "POST" and str(request.url) == "http://www.cninfo.com.cn/new/hisAnnouncement/query"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded; charset=UTF-8"
    assert request.headers["x-requested-with"] == "XMLHttpRequest" and request.headers["accept"] == "application/json"
    assert request.headers["user-agent"] == "Mozilla/5.0" and request.headers["origin"] == "http://www.cninfo.com.cn"
    assert request.headers["referer"] == ("http://www.cninfo.com.cn/new/commonUrl/pageOfSearch"
                                          "?url=disclosure/list/search")
    assert form == {"pageNum": "1", "pageSize": "30", "column": "szse", "tabName": "fulltext", "plate": "szcy",
                    "stock": "", "searchkey": "", "secid": "", "category": "", "trade": "",
                    "seDate": "2026-10-19~2026-10-20", "sortName": "", "sortType": "", "isHLtitle": "true"}

    (document,) = outcome.documents
    assert document.source_key == "cninfo-announcement" and document.external_id == "1225610007"
    assert document.title == f"中创环保：{TITLE}"
    assert document.original_text == f"中创环保（300056）公告：{TITLE}"
    assert document.excerpt is None
    assert document.url == "http://static.cninfo.com.cn/finalpage/2026-10-19/1225610007.PDF"
    assert document.published_at == when and document.published_at.utcoffset() == timedelta(hours=8)
    assert document.published_precision == "exact" and document.fetched_at == WINDOW[1]
    assert dict(document.metadata) == {"provider": "cninfo", "secCode": "300056", "orgId": "gssz0300056",
                                       "adjunctType": "PDF", "adjunctSize": 987}
    assert outcome.oldest_seen == when


def test_pages_until_total_with_delay_between_pages():
    rows = [ann(i, at("2026-10-19", "18:00") + timedelta(seconds=i)) for i in range(70)]
    server, sleeps = Server(rows), []
    outcome = fetch(server, start=WINDOW[0], end=WINDOW[1], sleeps=sleeps)

    assert outcome.complete and outcome.error is None
    assert outcome.requests == 3 and [f["pageNum"] for f in server.forms] == ["1", "2", "3"]
    assert sleeps == [cninfo.PAGE_DELAY_SECONDS, cninfo.PAGE_DELAY_SECONDS] and cninfo.PAGE_DELAY_SECONDS == 0.5
    assert [d.external_id for d in outcome.documents] == [str(1225610000 + i) for i in range(69, -1, -1)]


def test_window_boundaries_and_cross_day_sedate():
    start, end = at("2026-10-19", "18:00:00"), at("2026-10-20", "08:00:00")
    rows = [ann(1, at("2026-10-20", "08:00:01")), ann(2, end), ann(3, at("2026-10-20", "03:00:00")),
            ann(4, start), ann(5, at("2026-10-19", "17:59:59")), ann(6, at("2026-10-18", "10:00:00"))]
    server = Server(rows)
    outcome = fetch(server, start=start, end=end)

    assert server.forms[0]["seDate"] == "2026-10-19~2026-10-20"
    assert [d.external_id for d in outcome.documents] == [str(1225610000 + i) for i in (2, 3, 4)]
    assert outcome.complete and outcome.rejected == 0 and outcome.oldest_seen == start


def test_sedate_uses_beijing_dates_and_ignores_subsecond_start():
    # UTC 17:00:00.5 是北京时间次日 01:00:00；起点的亚秒不能把同一秒的公告挡在外面。
    start = datetime(2026, 10, 19, 17, 0, 0, 500000, tzinfo=timezone.utc)
    end = datetime(2026, 10, 20, 0, 30, tzinfo=timezone.utc)  # 北京时间 08:30
    rows = [ann(1, at("2026-10-20", "01:00:00")), ann(2, at("2026-10-20", "00:59:59"))]
    server = Server(rows)
    outcome = fetch(server, start=start, end=end)

    assert server.forms[0]["seDate"] == "2026-10-20~2026-10-20"
    assert [d.external_id for d in outcome.documents] == ["1225610001"]


def test_empty_day_is_complete_not_an_error():
    server = Server([])
    outcome = fetch(server, start=WINDOW[0], end=WINDOW[1])
    assert outcome.complete and outcome.error is None and outcome.documents == [] and outcome.requests == 1


def test_page_limit_is_reported_and_incomplete():
    rows = [ann(i, at("2026-10-19", "18:00") + timedelta(seconds=i)) for i in range(100)]
    server = Server(rows)
    outcome = fetch(server, start=WINDOW[0], end=WINDOW[1], max_pages=2)
    assert outcome.error == "page_limit" and not outcome.complete
    assert outcome.requests == 2 and len(outcome.documents) == 60

    # 刚好翻满上限且已经取完，不算超限
    exact = fetch(Server(rows[:60]), start=WINDOW[0], end=WINDOW[1], max_pages=2)
    assert exact.complete and exact.error is None and len(exact.documents) == 60


def test_one_retry_per_page_then_success():
    rows = [ann(i, at("2026-10-19", "18:00") + timedelta(seconds=i)) for i in range(40)]
    server = Server(rows, script={2: [status(502, "bad gateway")]})
    sleeps: list = []
    outcome = fetch(server, start=WINDOW[0], end=WINDOW[1], sleeps=sleeps)

    assert outcome.complete and outcome.error is None and len(outcome.documents) == 40
    assert outcome.requests == 3 and [f["pageNum"] for f in server.forms] == ["1", "2", "2"]
    assert sleeps == [0.5, 0.5]  # 翻页间隔，加上重试前的一次间隔


@pytest.mark.parametrize(("script", "code", "requests"), [
    ([status(500, "oops")] * 2, "http_error", 2),
    ([httpx.ConnectError("down")] * 2, "transport_error", 2),
    ([httpx.ReadTimeout("slow")] * 2, "transport_error", 2),
    ([status(200, "<html>blocked</html>")] * 2, "json_invalid", 2),
    ([body([1, 2, 3])], "page_shape_invalid", 1),
    ([body({"totalAnnouncement": 3, "announcements": []})], "page_shape_invalid", 1),
    ([body({"totalAnnouncement": 3, "hasMore": False, "announcements": {"a": 1}})], "page_shape_invalid", 1),
    ([body({"totalAnnouncement": "3", "hasMore": False, "announcements": []})], "page_shape_invalid", 1),
    ([body({"totalAnnouncement": 5, "hasMore": False, "announcements": [ann(1, WINDOW[1])]})], "count_mismatch", 1),
    ([body({"totalAnnouncement": 99, "hasMore": True, "announcements": []})], "pagination_stalled", 1),
])
def test_source_errors_are_recorded_not_raised(script, code, requests):
    server = Server([], script={1: list(script)})
    sleeps: list = []
    outcome = fetch(server, start=WINDOW[0], end=WINDOW[1], sleeps=sleeps)
    assert outcome.error == code and not outcome.complete
    assert outcome.requests == requests == len(server.requests)


def test_error_on_a_later_page_keeps_earlier_documents_but_is_incomplete():
    rows = [ann(i, at("2026-10-19", "18:00") + timedelta(seconds=i)) for i in range(70)]
    server = Server(rows, script={2: [httpx.ConnectError("down")] * 2})
    outcome = fetch(server, start=WINDOW[0], end=WINDOW[1])
    assert outcome.error == "transport_error" and not outcome.complete
    assert len(outcome.documents) == 30 and outcome.requests == 3


def test_bad_items_are_rejected_and_duplicates_counted_once():
    good = ann(1, at("2026-10-19", "18:00"))
    rows = [good, ann(1, at("2026-10-19", "18:00")),                       # 跨页翻动造成的重复
            {**ann(2, at("2026-10-19", "18:01")), "announcementId": None},
            {**ann(3, at("2026-10-19", "18:02")), "announcementTitle": "  "},
            {**ann(4, at("2026-10-19", "18:03")), "secName": None},
            {**ann(5, at("2026-10-19", "18:04")), "secCode": ""},
            {**ann(6, at("2026-10-19", "18:05")), "announcementTime": None},
            {**ann(7, at("2026-10-19", "18:06")), "announcementTime": "1760000000000"},
            "not-a-dict",
            {**ann(8, at("2026-10-19", "18:07")), "adjunctUrl": "http://evil.example/x.pdf"},
            {**ann(9, at("2026-10-19", "18:08")), "adjunctUrl": None, "orgId": None, "adjunctSize": "big"}]
    outcome = fetch(Server(rows), start=WINDOW[0], end=WINDOW[1])

    assert outcome.complete and outcome.rejected == 7
    by_id = {d.external_id: d for d in outcome.documents}
    assert sorted(by_id) == ["1225610001", "1225610008", "1225610009"]
    assert by_id["1225610001"].url == "http://static.cninfo.com.cn/finalpage/2026-10-19/1225610001.PDF"
    assert by_id["1225610008"].url is None  # 不是相对路径的附件地址不会变成链接
    assert by_id["1225610009"].url is None
    assert dict(by_id["1225610009"].metadata) == {"provider": "cninfo", "secCode": "300001", "orgId": None,
                                                  "adjunctType": "PDF", "adjunctSize": None}


def test_multi_company_announcement_keeps_every_name_and_code_matchable():
    row = ann(1, at("2026-10-19", "18:00"), name="甲公司,乙公司", code="300001,300002", title="联合公告")
    (document,) = fetch(Server([row]), start=WINDOW[0], end=WINDOW[1]).documents
    assert document.original_text == "甲公司,乙公司（300001,300002）公告：联合公告"


def test_stops_on_second_page_when_floor_reached_no_third_page():
    start, end = at("2026-10-19", "18:00"), at("2026-10-20", "08:00")
    rows = [ann(i, at("2026-10-19", "20:00") + timedelta(seconds=i)) for i in range(30)]
    rows += [ann(30 + i, at("2026-10-19", "19:00") + timedelta(seconds=i)) for i in range(9)]
    rows.append(ann(39, at("2026-10-19", "17:00")))
    rows += [ann(40 + i, at("2026-10-19", "16:00") + timedelta(seconds=i)) for i in range(30)]
    server = Server(rows)
    outcome = fetch(server, start=start, end=end, max_pages=10)
    assert outcome.complete and outcome.error is None
    assert [f["pageNum"] for f in server.forms] == ["1", "2"]
    assert len(outcome.documents) == 39


def test_stops_after_first_page_when_window_is_short():
    start, end = at("2026-10-20", "08:30"), at("2026-10-20", "10:00")
    rows = [ann(1, at("2026-10-20", "10:00")), ann(2, at("2026-10-20", "09:00")),
            ann(3, at("2026-10-20", "08:00"))]
    rows += [ann(4 + i, at("2026-10-19", "20:00") + timedelta(seconds=i)) for i in range(30)]
    server = Server(rows)
    outcome = fetch(server, start=start, end=end, max_pages=5)
    assert outcome.complete and outcome.error is None
    assert [f["pageNum"] for f in server.forms] == ["1"]
    assert {d.external_id for d in outcome.documents} == {"1225610001", "1225610002"}


def test_in_page_order_violation_is_order_unexpected_and_keeps_documents():
    start, end = at("2026-10-19", "15:00"), at("2026-10-20", "08:00")
    rows = [ann(1, at("2026-10-19", "20:00")), ann(2, at("2026-10-19", "19:00")),
            ann(3, at("2026-10-19", "21:00"))]
    outcome = fetch(Server(rows, sort=False), start=start, end=end)
    assert outcome.error == "order_unexpected" and not outcome.complete
    assert len(outcome.documents) == 2 and outcome.documents[0].external_id == "1225610001"


def test_cross_page_order_violation_is_order_unexpected():
    start, end = at("2026-10-19", "15:00"), at("2026-10-20", "08:00")
    page1 = [ann(i, at("2026-10-19", "20:00") - timedelta(seconds=i)) for i in range(30)]
    page2 = [ann(30, at("2026-10-19", "21:00"))]
    server = Server(page1 + page2, sort=False)

    server.script = {2: [lambda: httpx.Response(200, json=server.page(2))]}
    outcome = fetch(server, start=start, end=end, max_pages=5)
    assert outcome.error == "order_unexpected" and not outcome.complete
    assert len(outcome.documents) == 30


def test_duplicate_across_pages_after_insertion_still_complete():
    start, end = at("2026-10-19", "15:00"), at("2026-10-20", "08:00")
    base = [ann(i, at("2026-10-19", "20:00") - timedelta(seconds=i)) for i in range(35)]
    page1 = base[:30]
    # 翻第 2 页前有新公告插入，偏移后本页开头重复上一页末尾那条（时间相同或更旧）
    page2 = [*base[29:35]]
    server = Server(page1 + base[30:35], sort=False)
    server.script = {2: [lambda: httpx.Response(200, json={"totalAnnouncement": 36, "hasMore": False,
                                                            "announcements": page2})]}
    outcome = fetch(server, start=start, end=end, max_pages=5)
    assert outcome.complete and outcome.error is None
    assert [f["pageNum"] for f in server.forms] == ["1", "2"]
    assert len(outcome.documents) == 35
    assert len({d.external_id for d in outcome.documents}) == 35


def test_page_limit_when_many_pages_before_floor():
    start, end = at("2026-10-19", "07:00"), at("2026-10-20", "08:00")
    rows = [ann(i, at("2026-10-19", "20:00") + timedelta(seconds=i)) for i in range(100)]
    outcome = fetch(Server(rows), start=start, end=end, max_pages=2)
    assert outcome.error == "page_limit" and not outcome.complete
    assert outcome.requests == 2 and len(outcome.documents) == 60


# ------------------------------------------------------------------ 接入采集

@pytest.fixture
def path(tmp_path):
    target = tmp_path / "v4.db"
    db.init_schema(target)
    return target


def packaged_collection() -> dict:
    return json.loads((resources.files("neckline.config") / "collection-v4.json").read_text(encoding="utf-8"))


def collect(path, server: Server, *, now: datetime, config: dict | None = None, **kwargs):
    return run_collection(path, now=now, config=config or packaged_collection(), tokens={}, clock=lambda: now,
                          cninfo_transport=httpx.MockTransport(server), cninfo_sleep=lambda _s: None, **kwargs)


def summary(result: dict, key: str = KEY) -> dict:
    return next(s for s in result["sources"] if s["sourceKey"] == key)


def test_packaged_config_declares_the_new_source():
    config = packaged_collection()
    assert config["collectionVersion"] == "collection-v4-3" and "巨潮" in config["note"]
    assert "按时间倒序翻到起点就停" in config["note"]
    assert set(config["sources"]) == set(SOURCES) and SOURCES[-1] == KEY
    assert config["sources"][KEY] == {"lookbackSeconds": 3600, "maxPages": 120, "timeoutSeconds": 20,
                                      "bootstrapSeconds": 172800}
    assert LATE_SOURCES == {KEY} and SOURCE_LABELS[KEY] == "巨潮公告"


def test_first_collection_bootstraps_new_source_only(path):
    now = at("2026-10-20", "08:00")
    server = Server([ann(1, at("2026-10-19", "18:12:00"), name="中创环保", code="300056", title=TITLE)])
    result = collect(path, server, now=now)

    cn = summary(result)
    assert cn["error"] is None and cn["complete"] and cn["documents"] == 1 and cn["newVersions"] == 1
    assert datetime.fromisoformat(cn["requestedStart"]) == now - timedelta(seconds=172800)
    assert server.forms[0]["seDate"] == "2026-10-18~2026-10-20"
    # 其他三个来源行为不变：没给起点就如实记为没有采集起点
    for key in SOURCES[:3]:
        assert summary(result, key)["error"] == "bootstrap_start_missing"
    assert result["status"] == "partial"


def test_second_collection_resumes_from_watermark_with_lookback(path):
    first_now, second_now = at("2026-10-20", "08:00"), at("2026-10-20", "20:00")
    collect(path, Server([ann(1, at("2026-10-19", "18:12:00"))]), now=first_now)
    with db.read(path) as conn:
        assert db.source_watermark(conn, KEY) == first_now

    server = Server([ann(2, at("2026-10-20", "19:30:00")), ann(1, at("2026-10-19", "18:12:00"))])
    result = collect(path, server, now=second_now)
    cn = summary(result)
    assert datetime.fromisoformat(cn["requestedStart"]) == first_now - timedelta(seconds=3600)
    assert server.forms[0]["seDate"] == "2026-10-20~2026-10-20"
    assert cn["documents"] == 1 and cn["newVersions"] == 1  # 10/19 那条在窗口之外，不重复入库


def test_failed_first_attempt_keeps_original_start_instead_of_restarting(path):
    first_now, second_now = at("2026-10-20", "08:00"), at("2026-10-20", "20:00")
    down = Server([], script={1: [httpx.ConnectError("down")] * 2})
    failed = summary(collect(path, down, now=first_now))
    assert failed["error"] == "transport_error" and not failed["complete"]

    server = Server([ann(1, at("2026-10-19", "18:12:00"))])
    again = summary(collect(path, server, now=second_now))
    assert datetime.fromisoformat(again["requestedStart"]) == first_now - timedelta(seconds=172800)
    assert again["complete"] and again["documents"] == 1


def test_explicit_bootstrap_start_still_wins_for_the_new_source(path):
    now, explicit = at("2026-10-20", "08:00"), at("2026-10-19", "20:00")
    result = collect(path, Server([]), now=now, bootstrap_start=explicit)
    assert datetime.fromisoformat(summary(result)["requestedStart"]) == explicit


def test_configs_without_the_new_source_do_not_collect_it(path):
    config = packaged_collection()
    del config["sources"][KEY]
    result = collect(path, Server([]), now=at("2026-10-20", "08:00"), config=config)
    assert [s["sourceKey"] for s in result["sources"]] == list(SOURCES[:3])


def test_source_failure_does_not_block_other_sources(path):
    now = at("2026-10-20", "08:00")
    config = packaged_collection()
    post_calls: list = []

    def tushare_post(payload):
        post_calls.append(payload)
        return {"code": 0, "data": {"fields": ["pub_time", "src", "title", "content"],
                                    "items": [["2026-10-19 17:00:00", "同花顺", "某公司公告", "某公司预增"]]}}

    down = Server([], script={1: [httpx.ConnectError("down")] * 2})
    result = run_collection(path, now=now, config=config, tokens={"TUSHARE_TOKEN": "t"}, clock=lambda: now,
                            bootstrap_start=at("2026-10-19", "15:00"), tushare_post=tushare_post,
                            cninfo_transport=httpx.MockTransport(down), cninfo_sleep=lambda _s: None)
    assert summary(result, "tushare-major-news")["complete"] and post_calls
    assert summary(result, "jin10-flash")["error"] == "credential_missing"
    assert summary(result)["error"] == "transport_error"


# ------------------------------------------------------------------ 晨报侧

def test_zhongchuang_announcement_reaches_the_morning_window_and_matches_300056(path):
    """10/19 18:12 的中创环保公告：采集入库 → 10/20 晨报窗口粗筛 → 点名 300056（真实公司池）。"""
    config = load_config()
    collection = packaged_collection()
    collection["sources"] = {KEY: collection["sources"][KEY]}
    day = date(2026, 10, 20)
    start, end = morning_window(day, config.rules["schedule"])
    published = at("2026-10-19", "18:12:00")
    assert start < published < end
    rows = [
        ann(1, at("2026-10-20", "07:50:00"), name="某某科技", code="688001", title="关于召开股东大会的通知"),
        ann(2, published, name="中创环保", code="300056", title=TITLE, adjunctType="PDF", adjunctSize=245760),
        ann(3, at("2026-10-19", "14:30:00"), name="中创环保", code="300056", title="收盘前的另一份公告"),
    ]
    server = Server(rows)
    now = end
    result = collect(path, server, now=now, config=collection)
    assert result["status"] == "completed" and summary(result)["documents"] == 3

    with db.read(path) as conn:
        docs = list(db.iter_documents(conn, published_from=start, published_to=end, fetched_by=now, include_end=True))
    assert len(docs) == 2  # 收盘前那条在窗口之外

    messages, in_window, duplicates, unmatched = prefilter_window(docs, config.pool)
    assert (in_window, duplicates, unmatched) == (2, 0, 1)
    (message,) = messages
    assert list(message.companies) == ["300056.SZ"]
    assert message.title == f"中创环保：{TITLE}" and message.source_label == "巨潮公告"
    assert message.published_at == published and message.source_key == KEY
    assert message.url == "http://static.cninfo.com.cn/finalpage/2026-10-19/1225610002.PDF"

    # App、MCP、网页共用的原文接口也显示同一个来源名
    with db.read(path) as conn:
        document = db.get_document(conn, message.document_id)
    assert morning_api.document_out(document)["sourceLabel"] == "巨潮公告"


def test_prefilter_matches_strict_short_name_company_through_the_title_format():
    """简称是常用词的公司只认「简称：」格式；巨潮标题格式「简称：公告名」必须能命中。"""
    config = load_config()
    strict = config.rules["matching"]["strictShortNames"]
    assert strict
    name = strict[0]
    code = next(c.short_code for c in config.pool.companies.values() if c.name == name)
    row = ann(1, at("2026-10-19", "18:12:00"), name=name, code=code, title="关于签订重大合同的公告")
    (document,) = fetch(Server([row]), start=WINDOW[0], end=WINDOW[1]).documents
    assert f"{code}.SZ" in config.pool.match(f"{document.title}\n{document.original_text}")
    assert f"{code}.SZ" not in config.pool.match(f"{name}（{code}）公告")  # 对照：没有「简称：」格式就不算点名


# ------------------------------------------------------------------ 覆盖缺口

def _run(sources: list[dict]) -> dict:
    return {"sources": sources}


def _src(key: str, start: datetime, end: datetime, *, complete: bool = True, error: str | None = None) -> dict:
    return {"sourceKey": key, "requestedStart": start.isoformat(), "requestedEnd": end.isoformat(),
            "complete": complete, "error": error, "documents": 1}


def _three(start: datetime, end: datetime) -> list[dict]:
    return [_src(key, start, end) for key in SOURCES[:3]]


def test_no_gap_for_the_new_source_before_it_went_live():
    start, end = at("2026-10-19", "15:00"), at("2026-10-20", "08:00")
    runs = [_run(_three(at("2026-10-19", "07:00"), end))]
    assert coverage_gaps(runs, start, end) == []
    assert coverage_gaps([], start, end) == ["TuShare 新闻没取全", "金十快讯没取全", "金十资讯没取全"]  # 其他来源照旧


def test_gap_reported_once_the_new_source_has_records_but_did_not_cover():
    start, end = at("2026-10-20", "15:00"), at("2026-10-21", "08:00")
    base = _three(at("2026-10-20", "07:00"), end)
    covering = _run([*base, _src(KEY, at("2026-10-19", "08:00"), end)])
    assert coverage_gaps([covering], start, end) == []

    limited = _run([*base, _src(KEY, start, end, complete=False, error="page_limit")])
    assert coverage_gaps([limited], start, end) == ["巨潮公告翻页到上限"]
    down = _run([*base, _src(KEY, start, end, complete=False, error="transport_error")])
    assert coverage_gaps([down], start, end) == ["巨潮公告网络错误"]
    odd = _run([*base, _src(KEY, start, end, complete=False, error="count_mismatch")])
    assert coverage_gaps([odd], start, end) == ["巨潮公告没取全"]
    # 有记录但只覆盖了窗口后半段
    late = _run([*base, _src(KEY, at("2026-10-20", "20:00"), end)])
    assert coverage_gaps([late], start, end) == ["巨潮公告没取全"]
    # 早先有过记录、窗口内这次没采到：照样要报，不能因为「窗口内没记录」就放过
    stale = _run([_src(KEY, at("2026-10-18", "08:00"), at("2026-10-20", "08:00"))])
    assert coverage_gaps([stale, _run(base)], start, end) == ["巨潮公告没取全"]



def test_unstable_pagination_is_reported_not_trusted():
    """并列时刻翻页不稳（每页都重复上一页的内容）时，不能当作取全。"""
    when = at("2026-10-20", "00:00")
    rows = [ann(i, when) for i in range(200)]
    server = Server(rows)
    server.page = lambda number: {"totalAnnouncement": 200, "hasMore": True,
                                  "announcements": rows[:cninfo.PAGE_SIZE]}  # 每页都给同一批
    outcome = fetch(server, start=at("2026-10-19", "15:00"), end=at("2026-10-20", "08:00"))
    assert outcome.error == "pagination_unstable" and not outcome.complete
    assert len(outcome.documents) == cninfo.PAGE_SIZE and outcome.requests == 3
