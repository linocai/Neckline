"""V4 晨报：库、回执、窗口、采集、出报真实入口（采集 → 出报 → 接口/网页 → 推送）、中断续跑与截止兜底。

来源、DeepSeek、APNs 全部是确定性的假实现；业务时钟显式传入；只用临时库。
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.mcp.brief import BRIEF
from neckline.morning import db
from neckline.morning.api import create_router as api_router
from neckline.morning.collect import run_collection
from neckline.morning.report import LoadedConfig, Pusher, finalize, load_config, note_error, run_morning
from neckline.morning.schedule import CalendarGap, deadline, is_trading_day, morning_window
from neckline.morning.web import create_router as web_router
from neckline.news import jin10
from neckline.news.model import SourceDocument
from neckline.screening.deepseek import ChatError, DeepSeekClient
from neckline.screening.pool import Company, CompanyPool

CST = timezone(timedelta(hours=8))
UTC = timezone.utc
READ_KEY = "r" * 32
SCHEDULE = {"previousClose": "15:00", "windowEnd": "08:00", "deadline": "09:15"}


def at(day: str, hm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:00+08:00")


def doc(key: str, ext: str, text: str, published: datetime, fetched: datetime, title: str | None = None) -> SourceDocument:
    return SourceDocument(source_key=key, external_id=ext, url=f"https://flash.jin10.com/detail/{ext}" if key != "tushare-major-news" else None,
                          title=title, original_text=text, excerpt=None, published_at=published, published_precision="exact",
                          fetched_at=fetched, metadata={"source": "同花顺"} if key == "tushare-major-news" else {})


@pytest.fixture
def path(tmp_path):
    target = tmp_path / "v4.db"
    db.init_schema(target)
    return target


# ------------------------------------------------------------------ 库与回执

def test_schema_is_explicit_idempotent_and_guarded(tmp_path, path):
    db.init_schema(path)
    with db.read(path) as conn:
        assert conn.execute("PRAGMA application_id").fetchone()[0] == db.APPLICATION_ID
    with pytest.raises(db.NotReady):
        with db.read(tmp_path / "missing.db"):
            pass
    other = tmp_path / "other.db"
    with sqlite3.connect(other) as conn:
        conn.execute("CREATE TABLE x (a)")
    with pytest.raises(db.NotReady):
        db.init_schema(other)
    with pytest.raises(sqlite3.OperationalError):
        with db.read(path) as conn:
            conn.execute("INSERT INTO devices VALUES ('t','ios','a','b')")


def test_documents_keep_revisions_and_window_reads_latest_seen(path):
    first = doc("jin10-flash", "a", "震裕科技预增", at("2026-10-12", "16:00"), at("2026-10-12", "20:00"))
    same = doc("jin10-flash", "a", "震裕科技预增", at("2026-10-12", "16:00"), at("2026-10-13", "08:00"))
    edited = doc("jin10-flash", "a", "震裕科技预增（更正）", at("2026-10-12", "16:00"), at("2026-10-13", "09:00"))
    with db.write(path) as conn:
        assert db.store_documents(conn, [first, same]) == 1
        assert db.store_documents(conn, [edited]) == 1
    with db.read(path) as conn:
        rows = list(db.iter_documents(conn, published_from=at("2026-10-12", "15:00"), published_to=at("2026-10-13", "08:00"),
                                      fetched_by=at("2026-10-13", "08:05"), include_end=True))
        later = list(db.iter_documents(conn, published_from=at("2026-10-12", "15:00"), published_to=at("2026-10-13", "08:00"),
                                       fetched_by=at("2026-10-13", "10:00"), include_end=True))
    assert [(r["revision"], r["text"]) for r in rows] == [(1, "震裕科技预增")]
    assert [r["revision"] for r in later] == [2]


def _client(path, handler, report_id="morning-x", attempts=2):
    return DeepSeekClient(endpoint="https://example.invalid/chat/completions", model="m", api_key="k",
                          receipts=db.DbReceipts(path, report_id), timeout_seconds=5, max_attempts=attempts,
                          transport=httpx.MockTransport(handler))


def test_receipts_bill_once_retry_unsent_and_never_resend_unknown(path, monkeypatch):
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda s: None)
    hits = []

    def ok(request):
        hits.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})

    client = _client(path, ok)
    assert not client.complete([{"role": "user", "content": "a"}], {}).from_receipt
    assert client.complete([{"role": "user", "content": "a"}], {}).from_receipt and len(hits) == 1

    state = {"n": 0}

    def flaky_connect(request):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})

    assert _client(path, flaky_connect).complete([{"role": "user", "content": "b"}], {}).content == "{}"

    def timeout(request):
        raise httpx.ReadTimeout("sent but no answer")

    with pytest.raises(ChatError, match="不重发"):
        _client(path, timeout).complete([{"role": "user", "content": "c"}], {})
    with pytest.raises(ChatError, match="不明"):
        _client(path, ok).complete([{"role": "user", "content": "c"}], {})
    with db.read(path) as conn:
        states = dict(conn.execute("SELECT state, count(*) FROM model_receipts GROUP BY state").fetchall())
    assert states == {"succeeded": 2, "unknown": 1}


# ------------------------------------------------------------------ 窗口

def test_morning_window_spans_weekends_and_holidays_and_fails_closed():
    assert morning_window(date(2026, 10, 13), SCHEDULE) == (at("2026-10-12", "15:00"), at("2026-10-13", "08:00"))
    assert morning_window(date(2026, 10, 19), SCHEDULE)[0] == at("2026-10-16", "15:00")
    assert morning_window(date(2026, 10, 8), SCHEDULE)[0] == at("2026-09-30", "15:00")
    assert not is_trading_day(date(2026, 10, 7)) and is_trading_day(date(2026, 10, 9))
    assert deadline(date(2026, 10, 13), SCHEDULE) == at("2026-10-13", "09:15")
    with pytest.raises(CalendarGap):
        is_trading_day(date(2027, 1, 4))


# ------------------------------------------------------------------ 采集

class FakeJin10:
    """按时间倒序分页的假金十：pages 是 [(items, has_more)]。"""

    def __init__(self, pages, **_):
        self.pages = pages
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def call_tool(self, name, arguments):
        items, more = self.pages[self.calls]
        self.calls += 1
        data = {"items": items, "has_more": more}
        if more:
            data["next_cursor"] = f"c{self.calls}"
        return {"data": data}

    def pagination_argument(self, name):
        return "cursor"


def _flash(i: int, when: datetime) -> dict:
    return {"id": f"f{i}", "url": f"https://flash.jin10.com/detail/f{i}", "content": f"快讯{i}", "time": when.isoformat()}


COLLECTION = {"jin10": {"endpoint": jin10.ENDPOINT, "protocolVersion": jin10.PROTOCOL_VERSION},
              "sources": {"tushare-major-news": {"lookbackSeconds": 3600, "sliceSeconds": 10800, "maxRequests": 8,
                                              "timeoutSeconds": 5},
                          "jin10-flash": {"lookbackSeconds": 3600, "maxPages": 4, "timeoutSeconds": 5},
                          "jin10-news": {"lookbackSeconds": 3600, "maxPages": 4, "timeoutSeconds": 5}}}


def test_collection_needs_explicit_bootstrap_then_advances_watermark(path):
    now = at("2026-10-12", "20:00")
    tokens = {"TUSHARE_TOKEN": "t", "JIN10_MCP_TOKEN": "j"}
    first = run_collection(path, now=now, config=COLLECTION, tokens=tokens, clock=lambda: now)
    assert first["status"] == "failed" and {s["error"] for s in first["sources"]} == {"bootstrap_start_missing"}

    pages = {"jin10-flash": [([_flash(1, at("2026-10-12", "19:00")), _flash(2, at("2026-10-12", "16:00"))], True),
                             ([_flash(3, at("2026-10-12", "14:00"))], True)],
             "jin10-news": [([], False)]}
    made = []

    def factory(**kwargs):
        key = "jin10-flash" if not made else "jin10-news"
        made.append(key)
        return FakeJin10(pages[key])

    def tushare_post(payload):
        return {"code": 0, "data": {"fields": ["pub_time", "src", "title", "content"],
                                    "items": [["2026-10-12 17:00:00", "同花顺", "震裕科技公告", "震裕科技预增"]]}}

    second = run_collection(path, now=now, config=COLLECTION, tokens=tokens, bootstrap_start=at("2026-10-12", "15:00"),
                            jin10_factory=factory, tushare_post=tushare_post, clock=lambda: now)
    by_key = {s["sourceKey"]: s for s in second["sources"]}
    assert by_key["tushare-major-news"]["complete"] and by_key["tushare-major-news"]["newVersions"] == 1
    assert by_key["jin10-flash"]["complete"] and by_key["jin10-flash"]["requests"] == 2
    assert by_key["jin10-news"]["error"] == "history_unavailable" and second["status"] == "partial"
    with db.read(path) as conn:
        assert db.source_watermark(conn, "jin10-flash") == now
        assert db.source_watermark(conn, "jin10-news") is None

    # 同一业务时刻再跑一次（重启续跑、彩排）不能覆盖前一次的覆盖记录
    pages["jin10-flash"] = [([_flash(4, at("2026-10-12", "19:30"))], False)]
    made.clear()
    run_collection(path, now=now, config=COLLECTION, tokens=tokens, jin10_factory=factory,
                   tushare_post=tushare_post, clock=lambda: now)
    with db.read(path) as conn:
        assert db.source_watermark(conn, "jin10-flash") == now
        assert db.source_watermark(conn, "tushare-major-news") == now
        assert conn.execute("SELECT count(*) FROM collection_runs").fetchone()[0] == 3


def test_tushare_splits_saturated_intervals():
    from neckline.news import tushare
    seen = []

    def post(payload):
        params = payload["params"]
        seen.append((params["start_date"], params["end_date"]))
        count = 400 if len(seen) == 1 else 2
        rows = [[f"2026-10-12 1{len(seen)}:00:0{i % 10}", "src", f"t{len(seen)}-{i}", "c"] for i in range(count)]
        return {"code": 0, "data": {"items": rows}}

    outcome = tushare.fetch_major_news(post, "token", start=at("2026-10-12", "15:00"), end=at("2026-10-12", "17:00"),
                                       slice_seconds=10800, max_requests=8, clock=lambda: at("2026-10-12", "17:00"))
    assert outcome.complete and seen == [
        ("2026-10-12 15:00:00", "2026-10-12 17:00:00"),
        ("2026-10-12 16:00:01", "2026-10-12 17:00:00"),
        ("2026-10-12 15:00:00", "2026-10-12 16:00:00"),
    ]
    assert len(outcome.documents) == 4  # 满额那次的 400 条不留，只留两半各自的结果
    assert tushare.fetch_major_news(post, None, start=at("2026-10-12", "15:00"), end=at("2026-10-12", "17:00"),
                                    slice_seconds=10800, max_requests=8,
                                    clock=lambda: at("2026-10-12", "17:00")).error == "credential_missing"


def test_tushare_requests_fixed_slices_in_order():
    from neckline.news import tushare
    seen = []

    def post(payload):
        params = payload["params"]
        seen.append((params["start_date"], params["end_date"]))
        return {"code": 0, "data": {"items": []}}

    outcome = tushare.fetch_major_news(post, "token", start=at("2026-10-12", "15:00"), end=at("2026-10-12", "22:00"),
                                       slice_seconds=10800, max_requests=8, clock=lambda: at("2026-10-12", "22:00"))
    assert outcome.complete and outcome.error is None and outcome.requests == 3
    assert seen == [
        ("2026-10-12 21:00:02", "2026-10-12 22:00:00"),
        ("2026-10-12 18:00:01", "2026-10-12 21:00:01"),
        ("2026-10-12 15:00:00", "2026-10-12 18:00:00"),
    ]


def test_tushare_bisects_only_the_saturated_slice():
    from neckline.news import tushare
    seen = []
    saturated = ("2026-10-12 18:00:01", "2026-10-12 21:00:01")

    def post(payload):
        params = payload["params"]
        key = (params["start_date"], params["end_date"])
        seen.append(key)
        count = 400 if key == saturated else 1
        rows = [[f"2026-10-12 16:10:{i % 60:02d}", "src", f"t{len(seen)}-{i}", "正文"] for i in range(count)]
        return {"code": 0, "data": {"items": rows}}

    outcome = tushare.fetch_major_news(post, "token", start=at("2026-10-12", "15:00"), end=at("2026-10-12", "22:00"),
                                       slice_seconds=10800, max_requests=8, clock=lambda: at("2026-10-12", "22:00"))
    assert seen == [
        ("2026-10-12 21:00:02", "2026-10-12 22:00:00"),
        saturated,
        ("2026-10-12 19:30:02", "2026-10-12 21:00:01"),
        ("2026-10-12 18:00:01", "2026-10-12 19:30:01"),
        ("2026-10-12 15:00:00", "2026-10-12 18:00:00"),
    ]
    assert outcome.complete and outcome.requests == 5 and len(outcome.documents) == 4


def test_tushare_stops_on_daily_quota_and_keeps_earlier_documents():
    from neckline.news import tushare
    calls = []

    def post(payload):
        calls.append(payload["params"]["start_date"])
        if len(calls) == 2:
            return {"code": 40203, "msg": "抱歉，您访问接口(major_news)频率超限(40次/天)，token=secret-token"}
        return {"code": 0, "data": {"items": [["2026-10-12 16:00:00", "同花顺", "标题甲", "正文甲"]]}}

    outcome = tushare.fetch_major_news(post, "secret-token", start=at("2026-10-12", "15:00"), end=at("2026-10-12", "22:00"),
                                       slice_seconds=10800, max_requests=8, clock=lambda: at("2026-10-12", "22:00"))
    assert outcome.error == "quota_exceeded" and outcome.complete is False
    assert [doc.title for doc in outcome.documents] == ["标题甲"] and outcome.requests == 1
    assert calls == ["2026-10-12 21:00:02", "2026-10-12 18:00:01"]
    assert "secret-token" not in outcome.error and "频率超限" not in outcome.error

    other = []

    def rejected(payload):
        other.append(1)
        if len(other) == 1:
            return {"code": 0, "data": {"items": [["2026-10-12 16:00:00", "同花顺", "标题乙", "正文乙"]]}}
        return {"code": 40101, "msg": "其它错误 secret-token"}

    denied = tushare.fetch_major_news(rejected, "secret-token", start=at("2026-10-12", "15:00"),
                                      end=at("2026-10-12", "22:00"), slice_seconds=10800, max_requests=8,
                                      clock=lambda: at("2026-10-12", "22:00"))
    assert denied.error == "upstream_rejected" and len(denied.documents) == 1 and len(other) == 2
    assert "secret-token" not in denied.error

    limited = []

    def by_msg(payload):
        limited.append(1)
        if len(limited) == 1:
            return {"code": 0, "data": {"items": [["2026-10-12 16:00:00", "同花顺", "标题丙", "正文丙"]]}}
        return {"code": 50000, "msg": "调用频率超限"}

    capped = tushare.fetch_major_news(by_msg, "secret-token", start=at("2026-10-12", "15:00"),
                                      end=at("2026-10-12", "22:00"), slice_seconds=10800, max_requests=8,
                                      clock=lambda: at("2026-10-12", "22:00"))
    assert capped.error == "quota_exceeded" and len(capped.documents) == 1 and len(limited) == 2


def test_incomplete_collection_resumes_from_the_first_attempt(path):
    bootstrap = at("2026-10-12", "15:00")
    first_now = at("2026-10-12", "22:00")
    second_now = at("2026-10-13", "08:00")
    third_now = at("2026-10-13", "20:00")
    tokens = {"TUSHARE_TOKEN": "secret-token", "JIN10_MCP_TOKEN": "j"}
    calls = []

    def post(payload):
        params = payload["params"]
        calls.append((params["start_date"], params["end_date"]))
        if len(calls) == 2:
            return {"code": 40203, "msg": "频率超限 token=secret-token"}
        return {"code": 0, "data": {"items": [["2026-10-12 16:30:00", "同花顺", f"标题{len(calls)}", "正文"]]}}

    def factory(**_):
        return FakeJin10([([_flash(1, bootstrap)], False)])

    first = run_collection(path, now=first_now, config=COLLECTION, tokens=tokens, bootstrap_start=bootstrap,
                           jin10_factory=factory, tushare_post=post, clock=lambda: first_now)
    tushare_first = next(s for s in first["sources"] if s["sourceKey"] == "tushare-major-news")
    assert tushare_first["error"] == "quota_exceeded" and tushare_first["complete"] is False
    assert tushare_first["documents"] == 1 and tushare_first["newVersions"] == 1
    assert tushare_first["requestedStart"] == bootstrap.isoformat()
    blob = json.dumps(first["sources"], ensure_ascii=False)
    assert "secret-token" not in blob and "频率超限" not in blob
    assert len(calls) == 2
    with db.read(path) as conn:
        assert db.source_watermark(conn, "tushare-major-news") is None

    second = run_collection(path, now=second_now, config=COLLECTION, tokens=tokens, jin10_factory=factory,
                            tushare_post=post, clock=lambda: second_now)
    tushare_second = next(s for s in second["sources"] if s["sourceKey"] == "tushare-major-news")
    assert tushare_first["covered"] == [["2026-10-12T21:00:02+08:00", "2026-10-12T22:00:00+08:00"]]
    assert tushare_second["requestedStart"] == bootstrap.isoformat() and tushare_second["complete"]
    # 第一轮那片取到时还在回看里，续跑要重新覆盖 21:00 之后，不再按「整片已覆盖」跳过。
    assert ("2026-10-12 21:00:02", "2026-10-13 00:00:02") in calls[2:]
    assert calls[2] == ("2026-10-13 06:00:05", "2026-10-13 08:00:00")
    with db.read(path) as conn:
        assert db.source_watermark(conn, "tushare-major-news") == second_now

    third = run_collection(path, now=third_now, config=COLLECTION, tokens=tokens, jin10_factory=factory,
                           tushare_post=post, clock=lambda: third_now)
    tushare_third = next(s for s in third["sources"] if s["sourceKey"] == "tushare-major-news")
    assert tushare_third["requestedStart"] == at("2026-10-13", "07:00").isoformat() and tushare_third["complete"]
    with db.read(path) as conn:
        assert db.source_watermark(conn, "tushare-major-news") == third_now


def test_collection_without_an_attempt_still_needs_bootstrap_and_makes_no_requests(path):
    now = at("2026-10-12", "20:00")
    tokens = {"TUSHARE_TOKEN": "t", "JIN10_MCP_TOKEN": "j"}
    calls = {"tushare": 0, "jin10": 0}

    def post(payload):
        calls["tushare"] += 1
        return {"code": 0, "data": {"items": []}}

    def factory(**_):
        calls["jin10"] += 1
        return FakeJin10([([], False)])

    for _ in range(2):
        result = run_collection(path, now=now, config=COLLECTION, tokens=tokens, tushare_post=post,
                                jin10_factory=factory, clock=lambda: now)
        assert result["status"] == "failed"
        assert {s["error"] for s in result["sources"]} == {"bootstrap_start_missing"}
    assert calls == {"tushare": 0, "jin10": 0}


# ------------------------------------------------------------------ 出报真实入口

def _config(**rules_overrides) -> LoadedConfig:
    base = load_config()
    pool = CompanyPool([Company("300953.SZ", "震裕科技", ("震裕科技",)), Company("300683.SZ", "海特生物", ("海特生物",)),
                        Company("300082.SZ", "奥克股份", ("奥克股份",))])
    rules = json.loads(json.dumps(base.rules))
    rules.update(rules_overrides)
    return LoadedConfig(rules=rules, sha256=base.sha256, pool=pool, profiles={})


def _seed_window(path, day="2026-10-13"):
    """模拟 10/12 20:00 与 10/13 08:00 两次完整采集：窗口内三条点名消息、一条窗口前的盘中消息。"""
    prev = "2026-10-12"
    docs = [
        doc("jin10-flash", "z1", "【震裕科技：预计前三季度净利增长58%】震裕科技公告", at(prev, "17:05"), at(prev, "20:00")),
        doc("jin10-flash", "h1", "【海特生物：签署授权协议】海特生物公告首付款700万美元", at(prev, "16:45"), at(prev, "20:00")),
        doc("tushare-major-news", "w1", "收评：奥克股份涨停，板块走强", at(day, "07:30"), at(day, "08:00"), title="收评"),
        doc("jin10-flash", "old", "【海特生物：盘中公告】海特生物早前披露", at(prev, "10:00"), at(prev, "20:00")),
    ]
    with db.write(path) as conn:
        db.store_documents(conn, docs)
        for run_id, slot, start in (("collect-a", at(prev, "20:00"), at(prev, "08:00")),
                                    ("collect-b", at(day, "08:00"), at(prev, "19:00"))):
            db.begin_collection(conn, run_id=run_id, slot_at=slot, started_at=slot)
            db.finish_collection(conn, run_id=run_id, status="completed", finished_at=slot, sources=[
                {"sourceKey": key, "requestedStart": start.isoformat(), "requestedEnd": slot.isoformat(),
                 "complete": True, "error": None, "documents": 1} for key in ("tushare-major-news", "jin10-flash", "jin10-news")])


@dataclass
class FakeModel:
    """按提示词里的公司分组给出确定的判断：震裕、海特是主体利好新消息，其余是顺带提及。"""
    hits: int = 0
    crash_companies: tuple[str, ...] = ()
    timeout_companies: tuple[str, ...] = ()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][1]["content"]
        blocks = re.split(r"^### ", prompt, flags=re.M)[1:]
        codes = [b.split()[0] for b in blocks]
        if any(c in self.timeout_companies for c in codes):
            raise httpx.ReadTimeout("no answer")
        self.hits += 1
        if any(c in self.crash_companies for c in codes):
            raise RuntimeError("worker crashed while the request was in flight")
        companies = []
        for block, code in zip(blocks, codes):
            body_part = block.split("窗口开始前的相关消息")[0]
            ids = re.findall(r"^\[(m\d+)\]", body_part, flags=re.M)
            if code in ("300953", "300683"):
                companies.append({"company": code, "events": [{"ids": ids, "relation": "主体", "type": "业绩预增",
                                  "direction": "利好", "channel": "公司公告", "amountYuan": None,
                                  "firstDisclosed": None, "fresh": "新", "importance": "高",
                                  "headline": f"{code} 利好", "reason": "公告"}], "mentions": []})
            else:
                companies.append({"company": code, "events": [], "mentions": ids})
        content = json.dumps({"companies": companies}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})


class FakeApns:
    def __init__(self):
        self.sent = []

    def __call__(self, token, title, body, **kwargs):
        self.sent.append((token, title, body))
        return type("R", (), {"ok": True, "reason": "ok"})()


def _run(path, model: FakeModel, apns: FakeApns, now: datetime, config: LoadedConfig | None = None):
    config = config or _config()
    return run_morning(path, now=now, config=config, clock=lambda: now, collector=lambda _: {"status": "completed"},
                       client_factory=lambda rid: _client(path, model, rid), pusher=Pusher(path, apns, lambda: now))


def _with_device(path):
    with db.write(path) as conn:
        db.upsert_device(conn, token="ab" * 32, platform="ios", at=at("2026-10-12", "12:00"))


def test_read_key_never_reaches_access_log():
    import logging
    from neckline.morning.web import HideReadKey
    def record(target):
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                 ("1.2.3.4:5", "GET", target, "1.1", 200), None)
    assert not HideReadKey().filter(record(f"/read/{READ_KEY}"))
    assert not HideReadKey().filter(record(f"/read/{READ_KEY}/stock/300002"))
    assert HideReadKey().filter(record("/api/v4/reports/latest"))
    assert any(isinstance(f, HideReadKey) for f in logging.getLogger("uvicorn.access").filters)


def test_api_reports_503_when_the_v4_db_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(tmp_path / "missing.db"))
    app = FastAPI()
    app.include_router(api_router(require_token_dependency=lambda: None))
    client = TestClient(app)
    for target in ("/api/v4/reports/latest", "/api/v4/reports/2026-10-19", "/api/v4/reports", "/api/v4/documents/x"):
        response = client.get(target)
        assert response.status_code == 503 and response.json() == {"detail": {"reason": "v4_db_not_ready"}}
    monkeypatch.delenv("NECKLINE_V4_DB_PATH")
    assert client.get("/api/v4/reports/latest").json() == {"detail": {"reason": "v4_not_configured"}}


def test_read_page_before_first_report(path, monkeypatch):
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    monkeypatch.setenv("NECKLINE_READ_KEY", READ_KEY)
    app = FastAPI()
    app.include_router(web_router())
    client = TestClient(app)
    assert "还没有晨报" in client.get(f"/read/{READ_KEY}").text
    assert client.get(f"/read/{READ_KEY}?format=json").json() == {"status": "no_report", "brief": BRIEF}
    monkeypatch.setenv("NECKLINE_READ_KEY", "short")
    assert client.get("/read/short").status_code == 404


def test_morning_report_end_to_end_api_web_and_single_push(path, monkeypatch):
    _seed_window(path)
    _with_device(path)
    model, apns = FakeModel(), FakeApns()
    result = _run(path, model, apns, at("2026-10-13", "08:02"))
    assert result["state"] == "available" and result["entries"] == 2 and result["gapSummary"] is None
    assert apns.sent == [("ab" * 32, "晨报已出", "2 家公司")]
    calls = model.hits
    again = _run(path, model, apns, at("2026-10-13", "08:10"))
    assert again["state"] == "available" and model.hits == calls and len(apns.sent) == 1

    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    monkeypatch.setenv("NECKLINE_READ_KEY", READ_KEY)
    app = FastAPI()
    app.include_router(api_router(require_token_dependency=lambda: None))
    app.include_router(web_router())
    client = TestClient(app)
    latest = client.get("/api/v4/reports/latest").json()
    assert latest["status"] == "available" and [e["companyName"] for e in latest["content"]["entries"]] == ["海特生物", "震裕科技"]
    first_source = latest["content"]["entries"][0]["sources"][0]
    assert first_source["publishedAt"] == "2026-10-12T16:45+08:00"
    assert client.get(f"/api/v4/documents/{first_source['documentId']}").json()["text"].startswith("【海特生物")
    page = client.get(f"/read/{READ_KEY}")
    assert page.status_code == 200 and "海特生物 300683" in page.text and "简要提示词" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert client.get(f"/read/{READ_KEY}?format=json").json()["content"] == latest["content"]
    assert client.get("/read/" + "x" * 32).status_code == 404
    assert client.get("/api/v4/health").json() == {"db": "ready", "latestReport": {"tradeDate": "2026-10-13", "status": "available"},
                                                   "readPage": True, "modelKey": False, "mcp": False, "tushare": False, "mairui": False,
                                                   "kxPool": False, "kxPicks": False}


def _one_company_per_call() -> LoadedConfig:
    config = _config()
    config.rules["classification"]["batchMaxCompanies"] = 1
    return config


def test_crash_mid_report_resumes_without_rebilling(path):
    _seed_window(path)
    config = _one_company_per_call()
    model, apns = FakeModel(crash_companies=("300953",)), FakeApns()
    now = at("2026-10-13", "08:02")
    with pytest.raises(RuntimeError):
        _run(path, model, apns, now, config)
    note_error(path, day=now.date(), message="出报中途出错：RuntimeError")
    with db.read(path) as conn:
        assert db.get_report(conn, "2026-10-13")["status"] == "running"
        states = dict(conn.execute("SELECT state, count(*) FROM model_receipts GROUP BY state").fetchall())
    assert states == {"succeeded": 2, "started": 1}
    before = model.hits
    model.crash_companies = ()
    result = _run(path, model, apns, at("2026-10-13", "08:05"), config)
    # 已付费的两次读回执；崩溃时在途的那次结果不明，不重发、不换请求重做，震裕科技记为缺口。
    assert model.hits == before and result["stats"]["fromReceipt"] == 2
    assert result["state"] == "available" and result["entries"] == 1
    assert "震裕科技没能完成判断" in result["gapSummary"]


def test_unknown_outcome_becomes_gap_and_is_never_resent(path):
    _seed_window(path)
    config = _one_company_per_call()
    model, apns = FakeModel(timeout_companies=("300683",)), FakeApns()
    result = _run(path, model, apns, at("2026-10-13", "08:02"), config)
    assert result["state"] == "available" and result["entries"] == 1 and result["stats"]["gaps"] == [
        {"company": "300683.SZ", "error": "调用失败：请求可能已送达但没收到结果，不重发"}]
    assert "海特生物没能完成判断" in result["gapSummary"]
    model.timeout_companies = ()
    hits = model.hits
    with db.write(path) as conn:  # 删掉晨报记录强制重跑：结果不明的那次请求仍不重发
        conn.execute("DELETE FROM reports")
    again = _run(path, model, apns, at("2026-10-13", "08:06"), config)
    assert model.hits == hits and again["stats"]["gaps"] == [
        {"company": "300683.SZ", "error": "调用失败：上次调用结果不明，不重发"}]


def test_deadline_unapproved_rules_and_non_trading_days(path):
    _with_device(path)
    model, apns = FakeModel(), FakeApns()
    late = _run(path, model, apns, at("2026-10-13", "09:20"))
    assert late["state"] == "failed" and late["reason"] == "09:15 前没能出报" and apns.sent[-1][1] == "晨报出问题了"
    assert _run(path, model, apns, at("2026-10-13", "09:30"))["state"] == "failed" and len(apns.sent) == 1
    draft = _run(path, model, apns, at("2026-10-14", "08:02"), _config(approval="draft"))
    assert draft["state"] == "failed" and draft["reason"] == "筛选规则未经批准"
    assert _run(path, model, apns, at("2026-10-17", "08:02"))["state"] == "not_trading_day"
    assert model.hits == 0


def test_finalize_marks_stuck_report_failed_with_reason(path):
    _with_device(path)
    apns = FakeApns()
    config = _config()
    now = at("2026-10-13", "08:02")
    with db.write(path) as conn:
        start, end = morning_window(now.date(), SCHEDULE)
        db.begin_report(conn, report_id="morning-2026-10-13", trade_date="2026-10-13", window_start=start, window_end=end,
                        config_version="screening-v1", config_sha256="x", started_at=now)
    note_error(path, day=now.date(), message="出报中途出错：OSError")
    result = finalize(path, now=at("2026-10-13", "09:15"), config=config, pusher=Pusher(path, apns, lambda: now))
    assert result["state"] == "failed" and result["reason"] == "09:15 前没能出报（出报中途出错：OSError）"
    assert apns.sent == [("ab" * 32, "晨报出问题了", result["reason"])]
    assert finalize(path, now=at("2026-10-13", "09:16"), config=config, pusher=Pusher(path, apns, lambda: now))["state"] == "failed"
    nothing = finalize(path, now=at("2026-10-14", "09:15"), config=config, pusher=Pusher(path, apns, lambda: now))
    assert nothing["reason"] == "09:15 前没能出报（出报任务没有运行）"


def test_shipped_config_is_approved_and_pool_verified():
    config = load_config()
    assert config.rules["approval"] == "approved" and config.rules["configVersion"] == "screening-v2"
    assert len(config.pool) == 1089 and config.rules["schedule"] == SCHEDULE
