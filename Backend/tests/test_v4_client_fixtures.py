"""V4 客户端夹具：合成隔离库的真实接口响应，与 App 测试夹具逐份对齐。

业务时钟固定。令牌只用明显的假值，夹具里不出现它，也不出现进程里的 API_TOKEN。
设置环境变量 NK_WRITE_FIXTURES=1 时改写 App/NecklineTests/Fixtures。
"""
from __future__ import annotations

import dataclasses
import json
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.api import deps
from neckline.api.deps import require_token
from neckline.config import settings
from neckline.morning import db
from neckline.morning.api import create_router
from neckline.morning.report import build_content, gap_summary
from neckline.morning.schedule import morning_window
from neckline.news.model import SourceDocument
from neckline.screening.classify import Event
from neckline.screening.pool import Company, CompanyPool
from neckline.screening.prefilter import Message, Window
from neckline.screening.select import Entry, Selection

FIXTURES = Path(__file__).resolve().parents[2] / "App" / "NecklineTests" / "Fixtures"
# KX 看板的夹具由 KX 测试从隔离库产出（Backend/tests/fixtures），App 原样复制；极限值夹具是 App 自己的。
KX_BACKEND_FIXTURES = Path(__file__).resolve().parent / "fixtures"
KX_COPIED = {"kx_board_sample.json", "kx_board_degraded.json", "kx_board_schema2.json"}
KX_APP_ONLY = {"kx_board_extreme.json"}
FIXTURE_TOKEN = "fixture-token-not-real"
WRONG_TOKEN = "not-the-fixture-token"
DEVICE_TOKEN = "0123456789abcdef" * 4
SCHEDULE = {"previousClose": "15:00", "windowEnd": "08:00", "deadline": "09:15"}
CONFIG_VERSION = "screening-v1"
# 夹具里的版本号用固定占位，发布时不必改 App 夹具；真实发布号由 test_api_app.py 断言。
FIXTURE_VERSION = "v0.0.0"
FIXTURE_RELEASE_SET = "v0.0.0-b0"
CONFIG_SHA = "0" * 64


def at(day: str, hm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:00+08:00")


def canon(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pretty(raw: bytes) -> str:
    return json.dumps(json.loads(raw), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _contains_process_token(blob: str) -> bool:
    secret = settings.api_token or ""
    if len(secret) < 16:
        return False
    return secret in blob


def _event(company: str, **overrides) -> Event:
    fields = dict(ids=["m1"], relation="主体", type="业绩向好", direction="利好", first_disclosed=None,
                  fresh="新", headline="事件", reason="公告写明了事实",
                  channel="公司公告", amount_yuan=None)
    fields.update(overrides)
    return Event(company=company, **fields)


def _message(document_id: str, source_key: str, label: str, published: datetime, url: str | None,
             title: str) -> Message:
    return Message(document_id=document_id, revision=1, source_key=source_key, source_label=label, url=url,
                   title=title, text="正文", published_at=published, first_seen_at=published)


def _pool() -> CompanyPool:
    return CompanyPool([
        Company("300953.SZ", "震裕科技", ("震裕科技",)),
        Company("300683.SZ", "海特生物", ("海特生物",)),
        Company("300082.SZ", "奥克股份", ("奥克股份",)),
        Company("300124.SZ", "汇川技术", ("汇川技术",)),
        Company("300014.SZ", "亿纬锂能", ("亿纬锂能",)),
    ])


def _selection(pool: CompanyPool, flash_id: str, news_id: str, wire_id: str) -> Selection:
    zhenyu = Entry(
        company="300953.SZ",
        primary=_event("300953.SZ", headline="前三季度净利预增", reason="公告预计净利增长 58%"),
        messages=[_message(flash_id, "jin10-flash", "金十快讯", at("2026-10-16", "17:05"),
                           "https://example.invalid/flash/z1", "震裕科技：前三季度净利预增")],
        others=[_event("300953.SZ", type="订单合同", headline="签订设备采购合同", reason="公告披露采购金额")],
    )
    haite = Entry(
        company="300683.SZ",
        primary=_event("300683.SZ", type="订单合同", first_disclosed="2026-10-16", headline="签署海外授权协议",
                       reason="公告写明首付款"),
        messages=[_message(wire_id, "tushare-major-news", "TuShare 新闻", at("2026-10-19", "07:40"), None, "")],
    )
    aoke = Entry(
        company="300082.SZ",
        primary=_event("300082.SZ", type="商业化进展", headline="新产品进入量产", reason="公告写明开始量产"),
        messages=[_message(news_id, "jin10-news", "金十资讯", at("2026-10-18", "21:10"),
                           "https://example.invalid/news/o1", "奥克股份：新产品进入量产")],
    )
    return Selection(candidates=[zhenyu, haite, aoke], reasons=Counter(), gaps=[])


def _empty_selection() -> Selection:
    return Selection(candidates=[], reasons=Counter(), gaps=[])


def _content(pool: CompanyPool, selection: Selection, day: str, gap: str | None, generated: datetime, messages: int) -> dict:
    start, end = morning_window(date.fromisoformat(day), SCHEDULE)
    return build_content(report_id=f"morning-{day}", day=date.fromisoformat(day),
                         window=Window(start=start, end=end, collected_by=generated), selection=selection, pool=pool,
                         config_version=CONFIG_VERSION, gap=gap, generated_at=generated, stats={"messages": messages})


def _seed(path: Path) -> str:
    flash_id = db.document_id("jin10-flash", "z1")
    news_id = db.document_id("jin10-news", "o1")
    wire_id = db.document_id("tushare-major-news", "w1")
    docs = [
        SourceDocument(source_key="jin10-flash", external_id="z1", url="https://example.invalid/flash/z1",
                       title="震裕科技：前三季度净利预增", original_text="震裕科技预计前三季度净利增长 58%。", excerpt=None,
                       published_at=at("2026-10-16", "17:05"), published_precision="exact",
                       fetched_at=at("2026-10-16", "20:00")),
        SourceDocument(source_key="jin10-news", external_id="o1", url="https://example.invalid/news/o1",
                       title="奥克股份：新产品进入量产", original_text="奥克股份公告新产品开始量产。", excerpt=None,
                       published_at=at("2026-10-18", "21:10"), published_precision="exact",
                       fetched_at=at("2026-10-18", "22:00")),
        SourceDocument(source_key="tushare-major-news", external_id="w1", url=None, title="海特生物授权",
                       original_text="海特生物签署海外授权协议，首付款 700 万美元。", excerpt=None,
                       published_at=at("2026-10-19", "07:40"), published_precision="exact",
                       fetched_at=at("2026-10-19", "08:00")),
    ]
    pool = _pool()
    selected = _selection(pool, flash_id, news_id, wire_id)
    full = _content(pool, selected, "2026-10-19", gap_summary(["金十资讯没取全"], selected, pool),
                    at("2026-10-19", "08:03"), 3)
    empty = _content(pool, _empty_selection(), "2026-10-14", None, at("2026-10-14", "08:01"), 0)
    with db.write(path) as conn:
        db.store_documents(conn, docs)
        start, end = morning_window(date(2026, 10, 15), SCHEDULE)
        db.begin_report(conn, report_id="morning-2026-10-15", trade_date="2026-10-15", window_start=start,
                        window_end=end, config_version=CONFIG_VERSION, config_sha256=CONFIG_SHA,
                        started_at=at("2026-10-15", "08:00"))
        start, end = morning_window(date(2026, 10, 14), SCHEDULE)
        db.begin_report(conn, report_id="morning-2026-10-14", trade_date="2026-10-14", window_start=start,
                        window_end=end, config_version=CONFIG_VERSION, config_sha256=CONFIG_SHA,
                        started_at=at("2026-10-14", "08:00"))
        db.complete_report(conn, report_id="morning-2026-10-14", content=empty, stats={"messages": 0},
                           gap_summary=None, available_at=at("2026-10-14", "08:01"))
        start, end = morning_window(date(2026, 10, 16), SCHEDULE)
        db.fail_report(conn, report_id="morning-2026-10-16", trade_date="2026-10-16", window_start=start,
                       window_end=end, config_version=CONFIG_VERSION, config_sha256=CONFIG_SHA,
                       reason="09:15 前没能出报", at=at("2026-10-16", "09:20"))
        start, end = morning_window(date(2026, 10, 19), SCHEDULE)
        db.begin_report(conn, report_id="morning-2026-10-19", trade_date="2026-10-19", window_start=start,
                        window_end=end, config_version=CONFIG_VERSION, config_sha256=CONFIG_SHA,
                        started_at=at("2026-10-19", "08:00"))
        db.complete_report(conn, report_id="morning-2026-10-19", content=full, stats={"messages": 3},
                           gap_summary=full["gapSummary"], available_at=at("2026-10-19", "08:03"))
    return flash_id


def _assert_shapes(bodies: dict[str, bytes]) -> None:
    available = json.loads(bodies["report-available.json"])
    entries = available["content"]["entries"]
    sources = [source for entry in entries for source in entry["sources"]]
    assert available["status"] == "available" and available["tradeDate"] == "2026-10-19"
    assert "review" not in available["content"] and "importance" not in json.dumps(entries, ensure_ascii=False)
    assert len(entries) >= 2 and any(entry["others"] for entry in entries)
    assert any(entry["firstDisclosed"] is None for entry in entries)
    assert any(entry["firstDisclosed"] for entry in entries)
    assert any(source["url"] is None for source in sources) and any(source["url"] for source in sources)
    assert available["gapSummary"]
    assert available["content"]["window"] == {"start": "2026-10-16T15:00+08:00", "end": "2026-10-19T08:00+08:00"}
    assert available["content"]["generatedAt"] == "2026-10-19T08:03+08:00"
    assert available["windowStart"] == "2026-10-16T07:00:00+00:00"
    assert available["windowEnd"] == "2026-10-19T00:00:00+00:00"

    empty = json.loads(bodies["report-empty.json"])
    assert empty["content"]["entries"] == [] and "review" not in empty["content"]

    failed = json.loads(bodies["report-failed.json"])
    assert failed["status"] == "failed" and failed["content"] is None and failed["failureReason"]
    assert failed["windowStart"] == "2026-10-15T07:00:00+00:00"
    assert failed["windowEnd"] == "2026-10-16T00:00:00+00:00"
    assert failed["failedAt"] == "2026-10-16T01:20:00+00:00"

    history = json.loads(bodies["reports-history.json"])["reports"]
    assert [item["status"] for item in history] == ["available", "failed", "running", "available"]
    assert [item["tradeDate"] for item in history] == ["2026-10-19", "2026-10-16", "2026-10-15", "2026-10-14"]
    assert history[0]["entryCount"] == 3 and history[1]["failureReason"] == "09:15 前没能出报"
    assert json.loads(bodies["document.json"])["url"]
    assert json.loads(bodies["document-not-found.json"]) == {"detail": {"reason": "no_document"}}
    assert json.loads(bodies["report-not-found.json"]) == {"detail": {"reason": "no_report"}}
    assert json.loads(bodies["not-ready.json"]) == {"detail": {"reason": "v4_db_not_ready"}}
    assert json.loads(bodies["not-configured.json"]) == {"detail": {"reason": "v4_not_configured"}}
    assert json.loads(bodies["unauthorized.json"]) == {"detail": "未授权:缺失或错误的 Bearer token"}
    assert json.loads(bodies["devices-ok.json"]) == {"ok": True}
    health = json.loads(bodies["health.json"])
    assert health["db"] == "ready" and health["readPage"] is False and health["modelKey"] is False
    assert health["latestReport"] == {"tradeDate": "2026-10-19", "status": "available"}
    assert health["version"] == FIXTURE_VERSION and health["releaseSet"] == FIXTURE_RELEASE_SET


def test_v4_client_fixtures_match_isolated_api(tmp_path, monkeypatch):
    monkeypatch.delenv("NECKLINE_READ_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(deps, "settings", dataclasses.replace(settings, api_token=FIXTURE_TOKEN))

    full = tmp_path / "v4.db"
    empty = tmp_path / "empty.db"
    db.init_schema(full)
    db.init_schema(empty)
    flash_id = _seed(full)
    state: dict[str, Path | None] = {"path": full}

    app = FastAPI()
    app.include_router(create_router(
        require_token_dependency=require_token, db_path_provider=lambda: state["path"],
        version=FIXTURE_VERSION, release_set=FIXTURE_RELEASE_SET,
    ))
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {FIXTURE_TOKEN}"}
    wrong = {"Authorization": f"Bearer {WRONG_TOKEN}"}

    def take(method: str, path: str, status: int, **kwargs) -> bytes:
        response = client.request(method, path, **kwargs)
        assert response.status_code == status, (path, response.status_code, response.text)
        return response.content

    bodies = {
        "report-available.json": take("GET", "/api/v4/reports/latest", 200, headers=auth),
        "report-empty.json": take("GET", "/api/v4/reports/2026-10-14", 200, headers=auth),
        "report-failed.json": take("GET", "/api/v4/reports/2026-10-16", 200, headers=auth),
        "reports-history.json": take("GET", "/api/v4/reports", 200, headers=auth, params={"limit": 60}),
        "document.json": take("GET", f"/api/v4/documents/{flash_id}", 200, headers=auth),
        "document-not-found.json": take("GET", "/api/v4/documents/doc_missing", 404, headers=auth),
        "health.json": take("GET", "/api/v4/health", 200),
        "unauthorized.json": take("GET", "/api/v4/reports/latest", 401, headers=wrong),
        "devices-ok.json": take("POST", "/api/v4/devices", 200, headers=auth,
                                json={"token": DEVICE_TOKEN, "platform": "ios"}),
    }
    state["path"] = empty
    bodies["report-not-found.json"] = take("GET", "/api/v4/reports/latest", 404, headers=auth)
    state["path"] = tmp_path / "absent.db"
    # GET 在库文件不存在时，NotReady 发生在 contextmanager 进入阶段，_open 的 try 包不住，会变成 500。
    # POST /devices 把 with db.write 放在 try 里，库不存在时的真实响应才是 503 v4_db_not_ready。
    bodies["not-ready.json"] = take("POST", "/api/v4/devices", 503, headers=auth,
                                    json={"token": DEVICE_TOKEN, "platform": "ios"})
    state["path"] = None
    bodies["not-configured.json"] = take("GET", "/api/v4/reports/latest", 503, headers=auth)

    health = json.loads(bodies["health.json"])
    assert health["mcp"] is False and health["tushare"] is False and health["mairui"] is False
    for key in ("mcp", "tushare", "mairui", "kxPool", "kxPicks"):
        health.pop(key)
    bodies["health.json"] = json.dumps(health, ensure_ascii=False).encode()

    _assert_shapes(bodies)
    blob = b"\n".join(bodies.values()).decode()
    for secret in (FIXTURE_TOKEN, WRONG_TOKEN, DEVICE_TOKEN):
        if secret in blob:
            raise AssertionError("夹具含有测试令牌")
    if _contains_process_token(blob):
        raise AssertionError("夹具含有进程里的 API_TOKEN")

    if os.environ.get("NK_WRITE_FIXTURES") == "1":
        FIXTURES.mkdir(parents=True, exist_ok=True)
        for name, raw in bodies.items():
            (FIXTURES / name).write_text(pretty(raw), encoding="utf-8")
        for path in FIXTURES.glob("*.json"):
            if path.name not in bodies and path.name not in KX_COPIED | KX_APP_ONLY:
                path.unlink()

    missing = [name for name in bodies if not (FIXTURES / name).is_file()]
    assert not missing, missing
    for name, raw in bodies.items():
        assert canon((FIXTURES / name).read_text(encoding="utf-8")) == canon(raw), name
    for name in KX_COPIED:
        assert canon((FIXTURES / name).read_text(encoding="utf-8")) == canon(
            (KX_BACKEND_FIXTURES / name).read_text(encoding="utf-8")), name
    assert {path.name for path in FIXTURES.glob("*.json")} == set(bodies) | KX_COPIED | KX_APP_ONLY
