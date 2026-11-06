"""B6：晨报只交付判「推」的条目，网页、接口、MCP、推送和离线回放用同一份。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.morning import cli, db
from neckline.morning.api import create_router as api_router
from neckline.morning.api import report_out
from neckline.morning.replay import main as replay_main
from neckline.morning.report import Pusher, _ready_body, build_content, finalize, gap_summary, run_morning
from neckline.morning.schedule import morning_window
from neckline.morning.web import render_report
from neckline.mcp.server import _report as mcp_report
from neckline.screening.classify import CompanyResult, Event
from neckline.screening.pool import Company, CompanyPool
from neckline.screening.prefilter import Message, Window
from neckline.screening.select import Entry, Selection
from tests.test_b124_evaluate import _answer, _http
from tests.test_morning import CST, READ_KEY, FakeApns, FakeModel, _config, _run, _seed_window, _with_device, at

@pytest.fixture
def path(tmp_path: Path) -> Path:
    target = tmp_path / "v4.db"
    db.init_schema(target)
    return target


DAY = date(2026, 10, 13)
WINDOW = Window(start=datetime(2026, 10, 12, 15, 0, tzinfo=CST), end=datetime(2026, 10, 13, 8, 0, tzinfo=CST),
                collected_by=datetime(2026, 10, 13, 8, 2, tzinfo=CST))
SCHEDULE = {"previousClose": "15:00", "windowEnd": "08:00", "deadline": "09:15"}


def _event(company: str) -> Event:
    return Event(company=company, ids=["m1"], relation="主体", type="业绩预增", direction="利好",
                 first_disclosed=None, fresh="新", headline="原标题", reason="原理由", channel="公司公告",
                 amount_yuan=None)


def _message(document_id: str, hour: int, minute: int = 0, *, source_key: str = "jin10-flash",
             title: str = "快讯题", url: str | None = "https://example.test/m") -> Message:
    published = datetime(2026, 10, 12, hour, minute, tzinfo=CST)
    label = "巨潮公告" if source_key == "cninfo-announcement" else "金十快讯"
    return Message(document_id=document_id, revision=1, source_key=source_key, source_label=label, url=url,
                   title=title, text="正文", published_at=published, first_seen_at=published)


def _pool(codes: list[tuple[str, str]]) -> CompanyPool:
    return CompanyPool([Company(f"{code}.SZ", name, (name,)) for code, name in codes])


def _entry(code: str, messages: list[Message]) -> Entry:
    return Entry(company=f"{code}.SZ", primary=_event(f"{code}.SZ"), messages=messages)


def _eval(code: str, *, state: str = "succeeded", verdict: str | None = "推", output: object | None = None,
          package: dict | None = None) -> dict:
    if output is None and state == "succeeded" and verdict == "推":
        output = {
            "title": f"{code}标题", "conclusion": "结论",
            "questions": {
                "value": {"pass": True, "text": "利好有量"},
                "priced": {"text": "还没反映"},
                "money": "有新增量",
                "price": {"text": "不追高"},
            },
            "risk": "可能落空",
            "views": [],
        }
    return {"company": code, "state": state, "verdict": verdict, "output": output, "package": package or {}}


def _content(pool: CompanyPool, entries: list[Entry], evaluations: list[dict], *, max_items: int = 10,
             gap: str | None = None) -> dict:
    return build_content(report_id="morning-2026-10-13", day=DAY, window=WINDOW,
                         selection=Selection(candidates=entries, reasons=Counter(), gaps=[]), pool=pool,
                         config_version="screening-v3", gap=gap, generated_at=WINDOW.collected_by, stats={},
                         evaluations=evaluations, max_items=max_items)


def _page_report(content: dict, *, status: str = "available") -> dict:
    return {"trade_date": "2026-10-13", "status": status, "content": content, "failure_reason": None}


def _checkpoint(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_delivery_keeps_ten_pushes_in_first_disclosure_order():
    specs = [
        ("300099", 8, 0),
        ("300002", 9, 0),
        ("300001", 9, 0),
        ("300003", 10, 0),
        ("300004", 11, 0),
        ("300005", 12, 0),
        ("300006", 13, 0),
        ("300007", 14, 0),
        ("300008", 15, 0),
        ("300009", 16, 0),
        ("300010", 17, 0),
        ("300011", 18, 0),
    ]
    pool = _pool([(code, f"公司{code}") for code, _hour, _minute in specs])
    flash = _message("flash1", 8, title="快讯题", url="https://example.test/m")
    announcement = _message("ann1", 18, source_key="cninfo-announcement", title="公告题", url="https://example.test/a")
    entries = []
    for code, hour, minute in specs:
        messages = [flash, announcement] if code == "300099" else [_message(f"doc-{code}", hour, minute, title=code)]
        if code != "300099":
            messages[0] = _message(f"doc-{code}", hour, minute, title=code, url=None)
        entries.append(_entry(code, messages))
    package = {
        "messages": [
            {"id": "m1", "ref": "flash1@1", "title": "快讯题", "url": "https://example.test/m"},
            {"id": "m2", "ref": "ann1@1", "title": "公告题", "url": "https://example.test/a"},
        ],
        "original": {"documents": [{"id": "a1", "message": "m2", "title": "公告原题", "url": "https://example.test/a"}]},
    }
    output = {
        "title": "值得看", "conclusion": "结论写在这里",
        "questions": {"value": {"text": "值多少"}, "priced": {"text": "反映多少"},
                      "money": {"text": "为什么买"}, "price": {"text": "划不划算"}},
        "risk": "单独的风险",
        "views": [
            {"by": "媒体", "name": "金十", "text": "转述快讯", "ref": "m1"},
            {"by": "公司", "name": "公司自己", "text": "公告这么说", "ref": "a1"},
            {"by": "券商", "name": "不知名", "text": "对不上来源", "ref": "m9"},
        ],
    }
    evaluations = [_eval(code) for code, _hour, _minute in specs]
    evaluations[0] = _eval("300099", output=output, package=package)
    content = _content(pool, entries, evaluations)
    shown = [entry["companyCode"] for entry in content["entries"]]
    assert shown == ["300099", "300001", "300002", "300003", "300004", "300005", "300006", "300007", "300008", "300009"]
    assert content["candidates"] == 12 and content["evaluated"] == 12 and content["stats"]["overflow"] == 2
    first = content["entries"][0]
    assert first["sources"][0]["documentId"] == "ann1@1"
    assert first["sources"][1]["documentId"] == "flash1@1"
    assert first["questions"] == {"value": "值多少", "priced": "反映多少", "money": "为什么买", "price": "划不划算"}
    assert all(isinstance(text, str) for text in first["questions"].values())
    by_ref = {view["by"]: view for view in first["views"]}
    assert by_ref["媒体"]["sourceTitle"] == "快讯题" and by_ref["媒体"]["sourceUrl"] == "https://example.test/m"
    assert by_ref["公司"]["sourceTitle"] == "公告题" and by_ref["公司"]["sourceUrl"] == "https://example.test/a"
    assert set(by_ref) == {"媒体", "公司"}
    assert "对不上来源" not in json.dumps(first["views"], ensure_ascii=False)
    blob = json.dumps(content, ensure_ascii=False)
    for banned in ("rank", "importance", "reason", "others", "review"):
        assert banned not in blob


def test_zero_pushes_and_unevaluated_gap_summary():
    pool = _pool([("300001", "甲公司"), ("300002", "乙公司")])
    entries = [_entry("300001", [_message("a", 9)]), _entry("300002", [_message("b", 10)])]
    evaluations = [_eval("300001", verdict="不推", output={"title": "不推", "conclusion": "先不看"}),
                   _eval("300002", verdict="不推", output={"title": "也不推", "conclusion": "先不看"})]
    content = _content(pool, entries, evaluations)
    assert content["entries"] == [] and content["candidates"] == 2 and content["evaluated"] == 2
    assert content["stats"]["pushed"] == 0 and content["stats"]["overflow"] == 0

    names = [("300001", "甲"), ("300002", "乙"), ("300003", "丙"), ("300004", "丁"), ("300005", "戊"), ("300006", "己")]
    wide_entries = [_entry(code, [_message(code, 10)]) for code, _name in names]
    wide_entries.append(_entry("300007", [_message("ok", 11)]))
    wide = _pool(names + [("300007", "庚")])
    states = [
        _eval("300001", state="gap", verdict=None, output=None),
        _eval("300002", state="invalid", verdict=None, output=None),
        _eval("300004", state="invalid", verdict=None, output=None),
        _eval("300005", state="gap", verdict=None, output=None),
        _eval("300006", state="succeeded", verdict="推", output="不是对象"),
        _eval("300007", verdict="不推", output={"title": "不推", "conclusion": "跳过"}),
    ]
    delivered = build_content(report_id="morning-2026-10-13", day=DAY, window=WINDOW,
                              selection=Selection(candidates=wide_entries, reasons=Counter(), gaps=[]), pool=wide,
                              config_version="screening-v3", gap=None, generated_at=WINDOW.collected_by, stats={},
                              evaluations=states, max_items=10)
    assert delivered["entries"] == [] and delivered["evaluated"] == 1
    summary = gap_summary(["金十快讯没取全"], Selection(candidates=[], reasons=Counter(), gaps=[]), wide,
                          unevaluated=["300006", "300001", "300002", "300003", "300004", "300005"], overflow=2)
    assert summary == "采集不完整：金十快讯没取全；6 条没能完成评估：甲、乙、丙、丁、戊等；另有 2 条判推未列入"


def test_delivery_drops_a_view_whose_ref_is_not_in_the_package():
    pool = _pool([("300001", "甲公司")])
    package = {"messages": [{"id": "m1", "title": "快讯题", "url": "https://example.test/m"}],
               "original": {"documents": [{"id": "a1", "title": "公告题"}]}}
    output = {
        "title": "值得看", "conclusion": "结论",
        "questions": {"value": {"text": "值"}, "priced": {"text": "价"}, "money": {"text": "钱"}, "price": {"text": "划"}},
        "risk": "风险",
        "views": [
            {"by": "媒体", "name": "金十", "text": "这条对得上", "ref": "m1"},
            {"by": "券商", "name": "某券商", "text": "这条引用不存在", "ref": "m9"},
        ],
    }
    content = _content(pool, [_entry("300001", [_message("a", 9)])],
                       [_eval("300001", output=output, package=package)])
    views = content["entries"][0]["views"]
    assert [view["text"] for view in views] == ["这条对得上"]
    assert "m9" not in json.dumps(content["entries"], ensure_ascii=False)
    assert "这条引用不存在" not in json.dumps(content["entries"], ensure_ascii=False)


def test_gap_summary_names_unreadable_filings_and_thin_materials():
    codes = [(f"30000{i}", name) for i, name in enumerate(("甲", "乙", "丙", "丁", "戊", "己", "庚"), start=1)]
    pool = _pool(codes)
    entries = [_entry(code, [_message(code, 10)]) for code, _name in codes]
    evaluations = [
        _eval(code, verdict="不推", output={"title": "不推", "conclusion": "跳过"},
              package={"gaps": [] if code == "300007" else [f"{code}缺材料"]})
        for code, _name in codes
    ]
    selection = Selection(candidates=entries, reasons=Counter(), gaps=[])
    summary = gap_summary([], selection, pool, filing_failures=2, evaluations=evaluations)
    assert summary == "2 份公告没读出数字；6 家材料不全：甲、乙、丙、丁、戊等"
    assert _ready_body(1, 0, summary) == "个股 1 只，板块 0 个（有缺口）"
    assert _ready_body(0, 0, summary) == "今天没有值得推的（有缺口）"
    quiet = [_eval(code, verdict="不推", output={"title": "不推", "conclusion": "跳过"}, package={"gaps": []})
             for code, _name in codes]
    assert gap_summary([], selection, pool, evaluations=quiet) is None


def test_push_body_for_count_zero_and_gap():
    assert _ready_body(1, 2) == "个股 1 只，板块 2 个"
    assert _ready_body(0, 1) == "个股 0 只，板块 1 个"
    assert _ready_body(0, 0) == "今天没有值得推的"
    assert _ready_body(3, 0) == "个股 3 只，板块 0 个"
    assert _ready_body(1, 2, "采集不完整：金十快讯没取全") == "个股 1 只，板块 2 个（有缺口）"
    assert _ready_body(0, 1, "采集不完整：金十快讯没取全") == "个股 0 只，板块 1 个（有缺口）"
    assert _ready_body(3, 0, "采集不完整：金十快讯没取全") == "个股 3 只，板块 0 个（有缺口）"
    assert _ready_body(0, 0, "另有 2 条判推未列入") == "今天没有值得推的（有缺口）"


def test_web_renders_new_entries_empty_day_and_old_report():
    pool = _pool([("300683", "海特生物")])
    message = _message("flash1", 16, 45, title="海特生物授权", url="https://example.test/h")
    output = {
        "title": "海特签协议", "conclusion": "首付款写明了",
        "questions": {"value": {"text": "利好有量"}, "priced": {"text": "还没反映"},
                      "money": {"text": "有新增量"}, "price": {"text": "不追高"}},
        "risk": "授权可能延期",
        "views": [{"by": "媒体", "name": "金十", "text": "公司签了协议", "ref": "m1"}],
    }
    package = {"messages": [{"id": "m1", "ref": "flash1@1", "title": "海特生物授权", "url": "https://example.test/h"}],
               "original": {"documents": []}}
    content = _content(pool, [_entry("300683", [message])], [_eval("300683", output=output, package=package)])
    page = render_report("/read/k", _page_report(content)).body.decode()
    assert "海特生物 300683" in page and "海特签协议" in page and "首付款写明了" in page
    assert "利好值多少" in page and "市场已反映多少" in page
    assert "资金为什么买或不买" in page and "价格划不划算" in page
    assert "风险：授权可能延期" in page and "各方观点" in page and "媒体" in page
    assert "<h2>板块热点</h2>" in page and "<p>今天没有板块热点</p>" in page
    assert "今天没有入选的个股" not in page
    assert 'class="risk"' in page and 'class="views"' in page
    assert "--risk:#ff3b30" in page and "--view:#007aff" in page
    assert "prefers-color-scheme:dark" in page and "--risk:#ff453a" in page and "--view:#0a84ff" in page
    assert "极高" not in page

    empty = dict(content)
    empty["entries"] = []
    empty["evaluated"] = 4
    quiet = render_report("/read/k", _page_report(empty)).body.decode()
    assert "<h2>个股</h2>" in quiet and "<h2>板块热点</h2>" in quiet
    assert "<p>今天没有入选的个股</p>" in quiet
    assert "<p>今天没有板块热点</p>" in quiet
    assert "评估了 4 家公司的隔夜消息，没有值得推的" in quiet
    assert '<span class="lab">利好值多少</span>' not in quiet

    legacy = {
        "window": {"start": "2026-10-12T15:00+08:00", "end": "2026-10-13T08:00+08:00"},
        "generatedAt": "2026-10-13T08:03+08:00", "configVersion": "screening-v2",
        "entries": [{"rank": 1, "companyCode": "300683", "companyName": "海特生物", "type": "订单",
                     "importance": "极高", "headline": "授权", "reason": "公告", "sources": [], "others": []}],
        "review": {"types": []},
    }
    old = render_report("/read/k", _page_report(legacy)).body.decode()
    assert "授权" in old and '<span class="lab">利好值多少</span>' not in old and "极高" not in old
    assert "今天没有符合条件的利好" not in old
    assert "<h2>板块热点</h2>" in old and "<p>今天没有板块热点</p>" in old
    assert "今天没有入选的个股" not in old


def test_cli_evaluations_lists_every_state_without_writing(path: Path, capsys):
    start, end = morning_window(DAY, SCHEDULE)
    when = at("2026-10-13", "08:02")
    with db.write(path) as conn:
        db.begin_report(conn, report_id="morning-2026-10-13", trade_date="2026-10-13", window_start=start,
                        window_end=end, config_version="screening-v3", config_sha256="0" * 64, started_at=when)
        db.complete_report(conn, report_id="morning-2026-10-13", content={"entries": []}, stats={},
                           gap_summary=None, available_at=when)
        db.insert_prepared(conn, report_id="morning-2026-10-13", company="300683", contract="evaluation-v1",
                           package={"company": {"name": "海特生物"}}, prepared_at=when)
        db.settle_evaluation(conn, report_id="morning-2026-10-13", company="300683", state="succeeded",
                             settled_at=when, verdict="推", output={"title": "签协议"}, usage={}, seconds=1)
        db.insert_prepared(conn, report_id="morning-2026-10-13", company="300953", contract="evaluation-v1",
                           package={"company": {"name": "震裕科技"}}, prepared_at=when)
        db.settle_evaluation(conn, report_id="morning-2026-10-13", company="300953", state="gap",
                             settled_at=when, error="没评完")
    digest = _checkpoint(path)
    assert cli.main(["--db", str(path), "evaluations", "--date", "2026-10-13", "--json"]) == 0
    rows = {item["company"]: item for item in json.loads(capsys.readouterr().out)}
    assert rows["300683"]["verdict"] == "推" and rows["300683"]["state"] == "succeeded"
    assert rows["300683"]["name"] == "海特生物" and rows["300683"]["output"]["title"] == "签协议"
    assert rows["300953"]["state"] == "gap" and rows["300953"]["verdict"] is None
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert cli.main(["--db", str(path), "evaluations", "--date", "2026-10-14"]) == 1
    assert "2026-10-14 没有晨报" in capsys.readouterr().err
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert cli.main(["--db", str(path), "evaluations", "--date", "20261013"]) == 1


def test_replay_copies_snapshot_and_leaves_it_unchanged(tmp_path: Path, monkeypatch):
    snapshot = tmp_path / "snapshot.db"
    db.init_schema(snapshot)
    _seed_window(snapshot)
    start, end = morning_window(DAY, SCHEDULE)
    with db.write(snapshot) as conn:
        db.begin_report(conn, report_id="morning-2026-10-13", trade_date="2026-10-13", window_start=start,
                        window_end=end, config_version="screening-v3", config_sha256="0" * 64,
                        started_at=at("2026-10-13", "08:02"))
    before = _checkpoint(snapshot)
    market = tmp_path / "market.json"
    market.write_text("{}", encoding="utf-8")
    work = tmp_path / "work"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "replay-secret-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "replay-model")
    model = FakeModel()

    def handler(request: httpx.Request) -> httpx.Response:
        user = json.loads(request.content)["messages"][1]["content"]
        if not user.startswith("晨报交易日"):
            return model(request)
        verdict = "不推" if "公司：300953" in user else "推"
        title = "震裕先不推" if verdict == "不推" else "海特签协议"
        return _http(_answer(verdict, title=title))

    code = replay_main([
        "--snapshot", str(snapshot), "--trade-date", "2026-10-13", "--work", str(work),
        "--market-snapshot", str(market), "--receipts", str(work / "receipts"),
        "--filings-cache", str(work / "filings"), "--pool-seed-only",
    ], transport=httpx.MockTransport(handler))
    assert code == 0
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == before
    with db.read(snapshot) as conn:
        assert db.get_report(conn, "2026-10-13")["status"] == "running"
    out = work / "2026-10-13"
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    evaluations = json.loads((out / "evaluations.json").read_text(encoding="utf-8"))
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    samples = (out / "samples.md").read_text(encoding="utf-8")
    entry = report["content"]["entries"][0]
    assert entry["companyCode"] == "300683" and entry["title"] == "海特签协议"
    assert "importance" not in entry and "rank" not in entry
    verdicts = {(row["company"], row["verdict"]) for row in evaluations}
    assert ("300683", "推") in verdicts and ("300953", "不推") in verdicts
    assert any(row.get("package") for row in evaluations)
    assert summary["candidates"] == 2 and summary["pushed"] == 1
    assert summary["classificationCalls"] == 1 and summary["evaluationCalls"] == 2
    assert set(summary["tokens"]) == {"输入", "输出", "缓存命中", "思考"}
    assert set(summary["seconds"]) == {"classification", "prepare", "evaluation"}
    assert "海特签协议" in samples and "不推" in samples and "震裕先不推" in samples
    blob = "\n".join(path.read_text(encoding="utf-8") for path in out.iterdir() if path.is_file())
    assert "replay-secret-key" not in blob
    assert (work / "2026-10-13.db").is_file()


def test_replay_without_started_at_stops_before_touching_the_snapshot(tmp_path: Path):
    snapshot = tmp_path / "snapshot.db"
    db.init_schema(snapshot)
    before = _checkpoint(snapshot)
    market = tmp_path / "market.json"
    market.write_text("{}", encoding="utf-8")
    code = replay_main([
        "--snapshot", str(snapshot), "--trade-date", "2026-10-13", "--work", str(tmp_path / "work"),
        "--market-snapshot", str(market), "--receipts", str(tmp_path / "receipts"),
        "--filings-cache", str(tmp_path / "filings"), "--pool-seed-only",
    ])
    assert code == 1
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == before


def test_morning_report_reaches_api_mcp_and_web_with_the_same_entries(path: Path, monkeypatch):
    _seed_window(path)
    _with_device(path)
    model, apns = FakeModel(), FakeApns()
    result = _run(path, model, apns, at("2026-10-13", "08:02"))
    assert result["state"] == "available" and result["entries"] == 2
    assert result["gapSummary"] == "2 家材料不全：海特生物、震裕科技"
    assert apns.sent == [("ab" * 32, "晨报已出", "个股 2 只，板块 0 个（有缺口）")]
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    monkeypatch.setenv("NECKLINE_READ_KEY", READ_KEY)
    app = FastAPI()
    app.include_router(api_router(require_token_dependency=lambda: None))
    from neckline.morning.web import create_router as web_router
    app.include_router(web_router())
    client = TestClient(app)
    latest = client.get("/api/v4/reports/latest").json()
    via_mcp = mcp_report(None)
    assert via_mcp["content"] == latest["content"]
    assert "evaluations" not in via_mcp and "evaluations" not in latest
    entry = latest["content"]["entries"][0]
    assert entry["companyCode"] == "300683" and entry["title"] == "值得跟踪"
    assert isinstance(entry["questions"]["value"], str) and "rank" not in entry and "importance" not in entry
    assert "importance" not in json.dumps(latest["content"], ensure_ascii=False)
    listed = client.get("/api/v4/reports").json()["reports"]
    assert listed[0]["entryCount"] == 2
    assert listed[0]["counts"] == {"stocks": 2, "sectors": len(latest["content"]["sectors"]["entries"])}
    page = client.get(f"/read/{READ_KEY}")
    assert "海特生物 300683" in page.text and "利好值多少" in page.text
    assert "<h2>板块热点</h2>" in page.text
    if latest["content"]["sectors"]["entries"]:
        assert "今天没有板块热点" not in page.text
    else:
        assert "<p>今天没有板块热点</p>" in page.text
    assert "今天没有入选的个股" not in page.text
    assert client.get(f"/read/{READ_KEY}?format=json").json()["content"] == latest["content"]
    with db.read(path) as conn:
        stored = db.get_report(conn, "2026-10-13")
    assert report_out(stored)["content"] == latest["content"]


def _logic_output(code: str, logic: str | None) -> dict:
    output = {
        "title": f"{code}标题", "conclusion": "结论",
        "questions": {
            "value": {"pass": True, "text": "利好有量"},
            "priced": {"text": "还没反映"},
            "money": {"text": "有新增量"},
            "price": {"text": "不追高"},
        },
        "risk": "可能落空",
        "views": [],
    }
    if logic is not None:
        output["moneyLogic"] = logic
    return output


def _timed(specs: list[tuple[str, int, str | None]], *, max_items: int) -> dict:
    pool = _pool([(code, f"公司{code}") for code, _hour, _logic in specs])
    entries = [_entry(code, [_message(f"doc-{code}", hour, title=code, url=None)]) for code, hour, _logic in specs]
    evaluations = [_eval(code, output=_logic_output(code, logic)) for code, _hour, logic in specs]
    return _content(pool, entries, evaluations, max_items=max_items)


def test_overflow_prefers_strong_money_logic_and_renders_by_time():
    specs = []
    for index in range(1, 13):
        code = f"300{index:03d}"
        logic = "强" if index >= 10 else "一般"
        specs.append((code, 7 + index, logic))
    content = _timed(specs, max_items=10)
    shown = [entry["companyCode"] for entry in content["entries"]]
    assert shown == ["300001", "300002", "300003", "300004", "300005", "300006", "300007", "300010", "300011", "300012"]
    assert content["stats"]["overflow"] == 2 and content["evaluated"] == 12
    assert all("moneyLogic" not in entry for entry in content["entries"])
    assert "moneyLogic" not in json.dumps(content["entries"], ensure_ascii=False)


def test_under_the_cap_money_logic_does_not_change_the_list():
    mixed = [("300002", 10, "强"), ("300001", 9, "一般"), ("300003", 11, "一般")]
    plain = [(code, hour, None) for code, hour, _logic in mixed]
    assert _timed(mixed, max_items=10)["entries"] == _timed(plain, max_items=10)["entries"]
    assert [entry["companyCode"] for entry in _timed(mixed, max_items=10)["entries"]] == ["300001", "300002", "300003"]


def test_old_output_without_money_logic_counts_as_ordinary():
    late_strong = _timed([("300001", 9, None), ("300002", 10, "强")], max_items=1)
    assert [entry["companyCode"] for entry in late_strong["entries"]] == ["300002"]
    assert late_strong["stats"]["overflow"] == 1
    both_old = _timed([("300002", 10, None), ("300001", 9, "很强")], max_items=1)
    assert [entry["companyCode"] for entry in both_old["entries"]] == ["300001"]
    assert "moneyLogic" not in json.dumps(both_old["entries"], ensure_ascii=False)


def _shell(entries: list, sectors: dict | None = None) -> dict:
    content = {
        "window": {"start": "2026-10-12T15:00+08:00", "end": "2026-10-13T08:00+08:00"},
        "generatedAt": "2026-10-13T08:02+08:00", "configVersion": "screening-v5", "entries": entries,
    }
    if sectors is not None:
        content["sectors"] = sectors
    return content


def _store_available(path: Path, day: str, content: dict, gap: str | None) -> None:
    now = at(day, "08:02")
    start, end = morning_window(date.fromisoformat(day), SCHEDULE)
    with db.write(path) as conn:
        db.begin_report(conn, report_id=f"morning-{day}", trade_date=day, window_start=start, window_end=end,
                        config_version="screening-v5", config_sha256="0" * 64, started_at=now)
        db.complete_report(conn, report_id=f"morning-{day}", content=content, stats={}, gap_summary=gap,
                           available_at=now)


def _refuse_client(_report_id: str):
    raise AssertionError("已出的晨报不应再评估")


def _push_stored(path: Path, day: str) -> str:
    apns = FakeApns()
    now = at(day, "08:10")
    run_morning(path, now=now, config=_config(), clock=lambda: now, collector=lambda _at: {"status": "completed"},
                client_factory=_refuse_client, pusher=Pusher(path, apns, lambda: now))
    assert len(apns.sent) == 1 and apns.sent[0][1] == "晨报已出"
    return apns.sent[0][2]


_SECTOR = {
    "title": "金融座谈会",
    "sector": {"name": "人工智能", "code": "885001.TI", "memberCount": 12},
    "conclusion": "资金会看一眼",
    "questions": {"value": "有政策", "priced": "还没涨透", "money": "可能接力"},
    "leaders": [
        {"companyCode": "300082", "companyName": "奥克股份", "price": "明显高开不追"},
        {"companyCode": "300001", "companyName": "甲公司", "price": "回落再看"},
        {"companyCode": "300002", "companyName": "乙公司", "price": "不追高"},
    ],
    "risk": "可能落空",
    "views": [{"by": "媒体", "name": "财联社", "text": "认为有催化", "sourceTitle": "媒体标题",
               "sourceUrl": "https://news.example/a"}],
    "sources": [{
        "documentId": "doc_forum", "sourceLabel": "金十快讯", "publishedAt": "2026-10-12T17:30+08:00",
        "title": "座谈会", "url": "https://example.test/forum",
    }],
    "firstPublished": "2026-10-12T17:30+08:00",
}


def test_web_sector_section_shows_the_event_leaders_and_plain_empty_states():
    sectors = {"candidates": 1, "evaluated": 1, "overflow": 0, "entries": [_SECTOR]}
    page = render_report("/read/k", _page_report(_shell([], sectors))).body.decode()
    assert "<h2>板块热点</h2>" in page and "<p>今天没有入选的个股</p>" in page
    assert "今天没有板块热点" not in page
    assert "金融座谈会" in page and "人工智能" in page and "创业板 12 只" in page
    assert "资金会看一眼" in page
    assert "① 利好值多少" in page and "有政策" in page
    assert "② 市场已反映多少" in page and "还没涨透" in page
    assert "③ 资金会不会借题发挥" in page and "可能接力" in page
    assert "300082 奥克股份：明显高开不追" in page
    assert "300001 甲公司：回落再看" in page and "300002 乙公司：不追高" in page
    assert "风险：可能落空" in page and "各方怎么看" in page and "财联社" in page
    assert "https://news.example/a" in page and "10-12 17:30" in page
    assert 'href="/read/k/doc/doc_forum"' in page
    assert 'class="gap">今天没有' not in page
    quiet = render_report("/read/k", _page_report(_shell([]))).body.decode()
    assert "<p>今天没有入选的个股</p>" in quiet and "<p>今天没有板块热点</p>" in quiet
    assert 'class="gap"' not in quiet


def test_stored_and_finalize_push_use_the_same_two_line_copy(path: Path, monkeypatch):
    _with_device(path)
    both = {"candidates": 2, "evaluated": 2, "overflow": 0, "entries": [_SECTOR, {**_SECTOR, "title": "另一件事"}]}
    _store_available(path, "2026-10-12", _shell([{"companyCode": "300001"}, {"companyCode": "300002"}]), None)
    _store_available(path, "2026-10-13", _shell([{"companyCode": "300683", "companyName": "海特生物"}], both), "材料不全")
    _store_available(path, "2026-10-14", _shell([], {"entries": [{"title": "只有板块"}]}), None)
    _store_available(path, "2026-10-15", _shell([]), None)
    _store_available(path, "2026-10-16", _shell([]), "采集没取全")
    assert _push_stored(path, "2026-10-12") == "个股 2 只，板块 0 个"
    assert _push_stored(path, "2026-10-13") == "个股 1 只，板块 2 个（有缺口）"
    assert _push_stored(path, "2026-10-14") == "个股 0 只，板块 1 个"
    assert _push_stored(path, "2026-10-15") == "今天没有值得推的"
    assert _push_stored(path, "2026-10-16") == "今天没有值得推的（有缺口）"
    with db.write(path) as conn:
        conn.execute("DELETE FROM push_log")
    apns = FakeApns()
    now = at("2026-10-13", "09:15")
    again = finalize(path, now=now, config=_config(), pusher=Pusher(path, apns, lambda: now))
    assert again["state"] == "available"
    assert apns.sent == [("ab" * 32, "晨报已出", "个股 1 只，板块 2 个（有缺口）")]

    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    monkeypatch.setenv("NECKLINE_READ_KEY", READ_KEY)
    app = FastAPI()
    app.include_router(api_router(require_token_dependency=lambda: None))
    from neckline.morning.web import create_router as web_router
    app.include_router(web_router())
    client = TestClient(app)
    latest = client.get("/api/v4/reports/latest").json()
    assert latest["tradeDate"] == "2026-10-16" and "sectors" not in latest["content"]
    day = client.get("/api/v4/reports/2026-10-13").json()
    assert day["content"]["sectors"] == both
    assert day["content"]["sectors"]["entries"][0]["leaders"][0]["price"] == "明显高开不追"
    old = client.get("/api/v4/reports/2026-10-12").json()
    assert "sectors" not in old["content"] and len(old["content"]["entries"]) == 2
    listed = {item["tradeDate"]: item for item in client.get("/api/v4/reports", params={"limit": 10}).json()["reports"]}
    assert listed["2026-10-13"]["counts"] == {"stocks": 1, "sectors": 2}
    assert listed["2026-10-14"]["counts"] == {"stocks": 0, "sectors": 1}
    assert listed["2026-10-12"]["counts"] == {"stocks": 2, "sectors": 0}
    assert listed["2026-10-15"]["counts"] == {"stocks": 0, "sectors": 0}
    assert listed["2026-10-16"]["counts"] == {"stocks": 0, "sectors": 0}
    empty_page = client.get(f"/read/{READ_KEY}").text
    assert "<p>今天没有入选的个股</p>" in empty_page and "<p>今天没有板块热点</p>" in empty_page
    filled = client.get(f"/read/{READ_KEY}/day/2026-10-13").text
    assert "<h2>板块热点</h2>" in filled and "金融座谈会" in filled and "创业板 12 只" in filled
    assert "今天没有板块热点" not in filled
    assert client.get(f"/read/{READ_KEY}/day/2026-10-13", params={"format": "json"}).json()["content"]["sectors"] == both
    via_mcp = mcp_report("2026-10-13")
    assert via_mcp["content"]["sectors"] == both
