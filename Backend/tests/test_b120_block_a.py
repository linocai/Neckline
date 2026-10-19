"""4.0.1 B120 块 A：凭据、解析、配置、续跑、截止、补发、版本化原文、日志、离线回执、交易日历。

走 cli.main / run_morning 真实入口。DeepSeek、APNs、采集都是确定性假实现，只用临时库。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.morning import cli, db
from neckline.morning.api import create_router, quiet_http_loggers
from neckline.morning.report import Pusher, finalize, run_morning
from neckline.morning.web import create_router as web_router
from neckline.mcp.server import _document
from neckline.screening.classify import classify, parse_batch
from neckline.screening.deepseek import ChatRejected, ChatUnknown, DeepSeekClient, FileReceipts, canonical
from neckline.screening.pool import Company, CompanyPool
from neckline.screening.prefilter import Message, Window

from tests.test_morning import (
    CST, FakeApns, FakeModel, _config, _one_company_per_call, _seed_window, _with_device, at, doc,
)

UTC = timezone.utc
TOKEN = "ab" * 32
READ_KEY = "r" * 32


@pytest.fixture
def path(tmp_path):
    target = tmp_path / "v4.db"
    db.init_schema(target)
    return target


def _push(path, send, now):
    return Pusher(path, send, lambda: now)


def _morning(path, *, now, handler, api_key, send, attempts=3, config=None, clock=None, collector=None):
    calls = []

    def transport(request):
        calls.append(request)
        return handler(request)

    def factory(report_id):
        return DeepSeekClient(endpoint="https://example.invalid/chat/completions", model="m", api_key=api_key,
                              receipts=db.DbReceipts(path, report_id), timeout_seconds=5, max_attempts=attempts,
                              transport=httpx.MockTransport(transport))

    result = run_morning(path, now=now, config=config or _config(), clock=clock or (lambda: now),
                         collector=collector or (lambda _at: {"status": "completed"}),
                         client_factory=factory, pusher=_push(path, send, now))
    return result, calls


def _status(code, body=None):
    return lambda request: httpx.Response(code, json=body or {"error": {"message": "no"}})


# ------------------------------------------------------------------ 1. 凭据


@pytest.mark.parametrize("status, reason", [
    (401, "DeepSeek 拒绝了请求（http:401）"),
    (402, "DeepSeek 余额不足（http:402）"),
    (403, "DeepSeek 拒绝了请求（http:403）"),
])
def test_rejected_deepseek_fails_the_report(path, monkeypatch, status, reason):
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _s: None)
    _seed_window(path)
    _with_device(path)
    apns = FakeApns()
    result, calls = _morning(path, now=at("2026-10-13", "08:02"), handler=_status(status), api_key="k", send=apns)
    assert len(calls) == 1
    assert result["state"] == "failed" and result["reason"] == reason
    assert apns.sent == [(TOKEN, "晨报出问题了", reason)]
    with db.read(path) as conn:
        stored = db.get_report(conn, "2026-10-13")
    assert stored["status"] == "failed" and stored["content"] is None


def test_missing_deepseek_key_fails_instead_of_an_empty_list(path):
    _seed_window(path)
    _with_device(path)
    apns = FakeApns()
    result, calls = _morning(path, now=at("2026-10-13", "08:02"), handler=_status(200), api_key=None, send=apns)
    assert calls == []
    assert result["state"] == "failed" and result["reason"] == "DeepSeek 凭据缺失"
    assert apns.sent == [(TOKEN, "晨报出问题了", "DeepSeek 凭据缺失")]
    assert "没有符合条件的利好" not in apns.sent[0][2]


def test_all_companies_unclassified_fails_with_the_first_error(path):
    _seed_window(path)
    _with_device(path)
    apns = FakeApns()
    model = FakeModel(timeout_companies=("300953", "300683", "300082"))
    result, calls = _morning(path, now=at("2026-10-13", "08:02"), handler=model, api_key="k", send=apns,
                             config=_one_company_per_call())
    assert len(calls) == 3 and result["state"] == "failed"
    assert result["reason"] == "调用失败：请求可能已送达但没收到结果，不重发"
    assert apns.sent[-1][1] == "晨报出问题了"
    assert "没有符合条件的利好" not in apns.sent[-1][2]


def test_empty_list_with_a_gap_marks_the_push(path):
    _with_device(path)
    apns = FakeApns()
    gapped, _calls = _morning(path, now=at("2026-10-13", "08:02"), handler=FakeModel(), api_key="k", send=apns)
    assert gapped["state"] == "available" and gapped["entries"] == 0 and gapped["gapSummary"]
    assert apns.sent == [(TOKEN, "晨报已出", "今天没有符合条件的利好（有缺口）")]

    start, end = at("2026-10-13", "15:00"), at("2026-10-14", "08:00")
    with db.write(path) as conn:
        db.begin_collection(conn, run_id="cover", slot_at=end, started_at=end)
        db.finish_collection(conn, run_id="cover", status="completed", finished_at=end, sources=[
            {"sourceKey": key, "requestedStart": start.isoformat(), "requestedEnd": end.isoformat(),
             "complete": True, "error": None, "documents": 0}
            for key in ("tushare-major-news", "jin10-flash", "jin10-news")])
    quiet, _calls = _morning(path, now=at("2026-10-14", "08:02"), handler=FakeModel(), api_key="k", send=apns)
    assert quiet["entries"] == 0 and quiet["gapSummary"] is None
    assert apns.sent[-1] == (TOKEN, "晨报已出", "今天没有符合条件的利好")


# ------------------------------------------------------------------ 2. 单家解析


def test_events_scalar_gaps_only_that_company(monkeypatch):
    expected = {"300953.SZ": ["m1"], "300082.SZ": ["m2"]}
    content = json.dumps({"companies": [
        {"company": "300953", "events": 42, "mentions": ["m1"]},
        {"company": "300082", "events": None, "mentions": ["m2"]},
    ]}, ensure_ascii=False)
    results = parse_batch(content, expected)
    assert results["300953.SZ"].error == "事件字段无效" and not results["300953.SZ"].events
    assert results["300082.SZ"].error is None and results["300082.SZ"].events == [] and results["300082.SZ"].mentions == ["m2"]
    missing_events = parse_batch(json.dumps({"companies": [{"company": "300082", "mentions": ["m2"]}]}),
                                 {"300082.SZ": ["m2"]})
    assert missing_events["300082.SZ"].error is None and missing_events["300082.SZ"].events == []

    def explode(_value):
        raise RuntimeError("内部字段炸了")

    monkeypatch.setattr("neckline.screening.classify._amount", explode)
    payload = json.dumps({"companies": [
        {"company": "300953", "events": [{"ids": ["m1"], "relation": "主体", "type": "业绩预增", "direction": "利好",
                                          "channel": "公司公告", "amountYuan": 1, "fresh": "新", "importance": "高",
                                          "headline": "h", "reason": "r"}], "mentions": []},
        {"company": "300082", "events": [], "mentions": ["m2"]},
    ]}, ensure_ascii=False)
    broken = parse_batch(payload, expected)
    assert broken["300953.SZ"].error == "事件字段无效" and not broken["300953.SZ"].events
    assert broken["300082.SZ"].error is None and broken["300082.SZ"].mentions == ["m2"]


def test_parse_batch_crash_is_not_retried(tmp_path, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("炸了")

    monkeypatch.setattr("neckline.screening.classify.parse_batch", explode)
    rules = _one_company_per_call().rules["classification"]
    pool = CompanyPool([Company("300953.SZ", "震裕科技", ("震裕科技",))])
    message = Message(document_id="doc_" + "a" * 32, revision=1, source_key="jin10-flash", source_label="金十快讯",
                      url=None, title="震裕科技", text="震裕科技预增", published_at=at("2026-10-12", "17:00"),
                      first_seen_at=at("2026-10-12", "17:00"), companies={"300953.SZ": ["震裕科技"]})
    window = Window(start=at("2026-10-12", "15:00"), end=at("2026-10-13", "08:00"), collected_by=at("2026-10-13", "08:02"))
    calls = []

    def handler(_request):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    client = DeepSeekClient(endpoint="https://example.invalid/chat/completions", model="m", api_key="k",
                            receipts=FileReceipts(tmp_path), timeout_seconds=5, max_attempts=2,
                            transport=httpx.MockTransport(handler))
    first = classify([message], [], pool, {}, client, window=window, settings=rules)
    assert calls == [1]
    assert first.results["300953.SZ"].error == "输出无法解析" and first.results["300953.SZ"].retryable is False
    again = classify([message], [], pool, {}, client, window=window, settings=rules)
    assert calls == [1] and again.batches[-1].from_receipt


# ------------------------------------------------------------------ 5. 配置损坏


class _Clock:
    current = datetime(2026, 10, 13, 0, 2, tzinfo=UTC)

    @staticmethod
    def now(tz=None):
        return _Clock.current

    fromisoformat = staticmethod(datetime.fromisoformat)


def _patch_clock(monkeypatch):
    _Clock.current = datetime(2026, 10, 13, 0, 2, tzinfo=UTC)
    monkeypatch.setattr(cli, "datetime", _Clock)


def _patch_sender(monkeypatch):
    apns = FakeApns()
    monkeypatch.setattr("neckline.push.apns.send_push", apns)
    return apns


def test_invalid_config_pushes_on_weekdays_and_exits_zero(path, monkeypatch, caplog):
    secret = "pool-hash-do-not-log"
    collects = []

    def broken():
        raise ValueError(secret)

    def fake_collect(*_args, **_kwargs):
        collects.append(1)
        return {"status": "completed"}

    monkeypatch.setattr(cli, "load_config", broken)
    monkeypatch.setattr(cli, "run_collection", fake_collect)
    apns = _patch_sender(monkeypatch)
    _with_device(path)
    with caplog.at_level(logging.ERROR):
        assert cli.main(["--db", str(path), "morning", "--now", "2026-10-13T08:02:00+08:00"]) == 0
        assert cli.main(["--db", str(path), "morning", "--now", "2026-10-01T08:02:00+08:00"]) == 0
        assert cli.main(["--db", str(path), "morning", "--now", "2026-10-17T08:02:00+08:00"]) == 0
        assert cli.main(["--db", str(path), "finalize", "--now", "2026-10-14T09:15:00+08:00"]) == 0
        assert cli.main(["--db", str(path), "finalize", "--now", "2026-10-18T09:15:00+08:00"]) == 0
    assert collects == [1, 1, 1]
    bodies = [item[2] for item in apns.sent]
    assert bodies == ["筛选规则配置无效，今天不出报"] * 3
    assert secret not in caplog.text and "ValueError" in caplog.text
    with db.read(path) as conn:
        assert conn.execute("SELECT count(*) FROM reports").fetchone()[0] == 0


# ------------------------------------------------------------------ 6. 续跑


def test_resume_freezes_the_request_and_skips_collection(path, monkeypatch, capsys):
    _seed_window(path)
    _patch_clock(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _s: None)
    def tiny():
        config = _one_company_per_call()
        config.rules["classification"]["concurrency"] = 1
        return config

    monkeypatch.setattr(cli, "load_config", tiny)
    collects = []
    monkeypatch.setattr(cli, "run_collection", lambda *_a, **_k: collects.append(1) or {"status": "completed"})
    model = FakeModel(crash_companies=("300082",))
    lookups = []
    original = db.DbReceipts

    class Recording(original):
        def lookup(self, digest):
            lookups.append(digest)
            return super().lookup(digest)

    monkeypatch.setattr(db, "DbReceipts", Recording)

    class Wrapped(DeepSeekClient):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(model)
            super().__init__(**kwargs)

    monkeypatch.setattr(cli, "DeepSeekClient", Wrapped)
    argv = ["--db", str(path), "morning", "--now", "2026-10-13T08:02:00+08:00"]
    assert cli.main(argv) == 75
    assert collects == [1] and model.hits == 3
    with db.read(path) as conn:
        started = db.get_report(conn, "2026-10-13")["started_at"]
        first = sorted(row[0] for row in conn.execute("SELECT request_sha256 FROM model_receipts"))
    assert len(first) == 3 and len(lookups) == 3
    first_lookups = list(lookups)
    lookups.clear()
    _Clock.current = datetime(2026, 10, 13, 0, 10, tzinfo=UTC)
    with db.write(path) as conn:
        db.store_documents(conn, [doc("jin10-flash", "late", "【震裕科技：续跑新增】这是续跑不该看到的正文",
                                      at("2026-10-12", "18:00"), datetime(2026, 10, 13, 0, 5, tzinfo=UTC))])
    model.crash_companies = ()
    capsys.readouterr()
    assert cli.main(argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert collects == [1] and model.hits == 3
    assert payload["stats"]["collection"] == "skipped_resume" and payload["stats"]["fromReceipt"] == 2
    assert sorted(lookups) == sorted(first_lookups) == first
    with db.read(path) as conn:
        stored = db.get_report(conn, "2026-10-13")
        digests = sorted(row[0] for row in conn.execute("SELECT request_sha256 FROM model_receipts"))
    assert stored["started_at"] == started and stored["status"] == "available"
    assert digests == first
    assert "续跑不该看到" not in json.dumps(stored["content"], ensure_ascii=False)


def test_non_trading_day_still_collects(path):
    seen = []
    result = run_morning(path, now=at("2026-10-17", "08:02"), config=_config(), clock=lambda: at("2026-10-17", "08:02"),
                         collector=lambda moment: seen.append(moment) or {"status": "completed"},
                         client_factory=lambda _rid: None, pusher=_push(path, FakeApns(), at("2026-10-17", "08:02")))
    assert result["state"] == "not_trading_day" and seen == [at("2026-10-17", "08:02")]
    with db.read(path) as conn:
        assert db.get_report(conn, "2026-10-17") is None


# ------------------------------------------------------------------ 7. 截止竞态


def test_deadline_after_classification_does_not_push_ready(path):
    _seed_window(path)
    _with_device(path)
    apns = FakeApns()
    late = {"on": False}

    def clock():
        return at("2026-10-13", "09:16") if late["on"] else at("2026-10-13", "08:02")

    def handler(request):
        late["on"] = True
        return FakeModel()(request)

    result, _calls = _morning(path, now=at("2026-10-13", "08:02"), handler=handler, api_key="k", send=apns, clock=clock)
    assert result["state"] == "failed" and result["reason"] == "09:15 前没能出报"
    assert [item[1] for item in apns.sent] == ["晨报出问题了"]


def test_complete_report_zero_rows_follows_the_stored_status(path):
    _seed_window(path)
    _with_device(path)
    apns = FakeApns()
    now = at("2026-10-13", "08:02")

    def fail_first(request):
        with db.write(path) as conn:
            conn.execute("UPDATE reports SET status='failed', failure_reason=?, failed_at=? WHERE status='running'",
                         ("09:15 前没能出报", db.utc(now)))
        return FakeModel()(request)

    failed, _calls = _morning(path, now=now, handler=fail_first, api_key="k", send=apns)
    assert failed["state"] == "failed" and failed["reason"] == "09:15 前没能出报"
    assert apns.sent == []

    with db.write(path) as conn:
        conn.execute("DELETE FROM reports")
        conn.execute("DELETE FROM model_receipts")
    ready_body = json.dumps({"entries": [{"rank": 1}]}, ensure_ascii=False)

    def finish_first(request):
        with db.write(path) as conn:
            conn.execute("""UPDATE reports SET status='available', content_json=?, gap_summary=NULL, available_at=?
                            WHERE status='running'""", (ready_body, db.utc(now)))
        return FakeModel()(request)

    ready, _calls = _morning(path, now=now, handler=finish_first, api_key="k", send=apns)
    assert ready["state"] == "available" and ready["entries"] == 1
    assert apns.sent == [(TOKEN, "晨报已出", "1 家公司")]


# ------------------------------------------------------------------ 8. 推送补发


def test_finalize_redelivers_ready_and_problem(path):
    _seed_window(path)
    _with_device(path)
    now = at("2026-10-13", "08:02")
    flaky = _Flaky()
    ready, _calls = _morning(path, now=now, handler=FakeModel(), api_key="k", send=flaky)
    assert ready["state"] == "available" and flaky.sent == [(TOKEN, "晨报已出", "2 家公司")]
    again = finalize(path, now=at("2026-10-13", "09:15"), config=_config(), pusher=_push(path, flaky, now))
    assert again["state"] == "available" and len(flaky.sent) == 2 and again["push"]["sent"] == 1
    third = finalize(path, now=at("2026-10-13", "09:16"), config=_config(), pusher=_push(path, flaky, now))
    assert third["state"] == "available" and len(flaky.sent) == 2

    with db.write(path) as conn:
        conn.execute("DELETE FROM reports")
        conn.execute("DELETE FROM push_log")
    flaky.fail_next = True
    before = len(flaky.sent)
    problem, _calls = _morning(path, now=at("2026-10-14", "09:20"), handler=FakeModel(), api_key="k", send=flaky)
    assert problem["state"] == "failed" and len(flaky.sent) == before + 1
    retried = finalize(path, now=at("2026-10-14", "09:21"), config=_config(), pusher=_push(path, flaky, now))
    assert retried["state"] == "failed" and retried["push"]["sent"] == 1
    assert flaky.sent[-1][1] == "晨报出问题了"
    held = finalize(path, now=at("2026-10-14", "09:22"), config=_config(), pusher=_push(path, flaky, now))
    assert held["state"] == "failed" and len(flaky.sent) == before + 2


class _Flaky:
    def __init__(self):
        self.sent = []
        self.fail_next = True

    def __call__(self, token, title, body, **_kwargs):
        self.sent.append((token, title, body))
        ok = not self.fail_next
        self.fail_next = False
        return type("R", (), {"ok": ok, "reason": "ok" if ok else "unregistered"})()


# ------------------------------------------------------------------ 10. 版本化原文


def test_versioned_document_ref_on_api_mcp_and_web(path, monkeypatch):
    first = doc("jin10-flash", "rev", "第一版正文", at("2026-10-12", "16:00"), at("2026-10-12", "20:00"), title="标题")
    second = doc("jin10-flash", "rev", "第二版正文", at("2026-10-12", "16:00"), at("2026-10-13", "09:00"), title="标题")
    with db.write(path) as conn:
        assert db.store_documents(conn, [first, second]) == 2
        doc_id = db.document_id(first.source_key, first.external_id)
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    monkeypatch.setenv("NECKLINE_READ_KEY", READ_KEY)
    app = FastAPI()
    app.include_router(create_router(require_token_dependency=lambda: None))
    app.include_router(web_router())
    client = TestClient(app)
    latest = client.get(f"/api/v4/documents/{doc_id}").json()
    assert latest["documentId"] == doc_id and latest["revision"] == 2 and latest["text"] == "第二版正文"
    pinned = client.get(f"/api/v4/documents/{doc_id}@1").json()
    encoded = client.get(f"/api/v4/documents/{doc_id}%401").json()
    assert pinned == encoded and pinned["revision"] == 1 and pinned["text"] == "第一版正文" and pinned["documentId"] == doc_id
    for bad in (f"{doc_id}@0", f"{doc_id}@01", f"{doc_id}@", f"{doc_id}@1x", "doc_missing", doc_id.upper(), f"{doc_id}@2extra"):
        missing = client.get(f"/api/v4/documents/{bad}")
        assert missing.status_code == 404 and missing.json() == {"detail": {"reason": "no_document"}}, bad
    assert _document(f"{doc_id}@1")["text"] == "第一版正文"
    assert _document(f"{doc_id}%402")["text"] == "第二版正文" and _document(doc_id)["revision"] == 2
    assert _document(f"{doc_id}@0") == {"status": "no_document", "message": "没有这篇原文。"}
    page = client.get(f"/read/{READ_KEY}/doc/{doc_id}@1")
    assert page.status_code == 200 and "第一版正文" in page.text
    web_json = client.get(f"/read/{READ_KEY}/doc/{doc_id}%401?format=json").json()
    assert web_json["documentId"] == doc_id and web_json["revision"] == 1 and web_json["text"] == "第一版正文"
    assert client.get(f"/read/{READ_KEY}/doc/{doc_id}@01").status_code == 404


def test_cli_morning_round_trip_reads_back_through_api_and_mcp(path, monkeypatch, capsys):
    _seed_window(path)
    _with_device(path)
    _patch_clock(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    monkeypatch.setenv("NECKLINE_READ_KEY", READ_KEY)
    collects = []
    monkeypatch.setattr(cli, "run_collection", lambda *_a, **_k: collects.append(1) or {"status": "completed"})
    model = FakeModel()

    class Wrapped(DeepSeekClient):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(model)
            super().__init__(**kwargs)

    monkeypatch.setattr(cli, "DeepSeekClient", Wrapped)
    apns = _patch_sender(monkeypatch)
    assert cli.main(["--db", str(path), "morning", "--now", "2026-10-13T08:02:00+08:00"]) == 0
    assert collects == [1] and model.hits == 1 and apns.sent == [(TOKEN, "晨报已出", "2 家公司")]
    printed = json.loads(capsys.readouterr().out)
    assert printed["state"] == "available" and printed["entries"] == 2
    app = FastAPI()
    app.include_router(create_router(require_token_dependency=lambda: None))
    app.include_router(web_router())
    client = TestClient(app)
    latest = client.get("/api/v4/reports/latest").json()
    assert [entry["companyName"] for entry in latest["content"]["entries"]] == ["海特生物", "震裕科技"]
    ref = latest["content"]["entries"][0]["sources"][0]["documentId"]
    doc_id, revision = ref.split("@")
    assert len(doc_id) == 36 and doc_id.startswith("doc_") and revision.isdigit() and int(revision) >= 1
    body = client.get(f"/api/v4/documents/{ref}").json()
    via_mcp = _document(ref)
    assert body["documentId"] == via_mcp["documentId"] == doc_id
    assert body["revision"] == via_mcp["revision"] == int(revision)
    assert body["text"] == via_mcp["text"] and body["text"].startswith("【海特生物")
    page = client.get(f"/read/{READ_KEY}/doc/{ref}")
    assert page.status_code == 200 and "海特生物" in page.text


# ------------------------------------------------------------------ 13 / 14 / 16


def test_http_client_logs_are_warnings_on_cli_and_api(tmp_path, caplog):
    httpx_log, core_log = logging.getLogger("httpx"), logging.getLogger("httpcore")
    httpx_log.setLevel(logging.DEBUG)
    core_log.setLevel(logging.DEBUG)
    assert cli.main(["--db", str(tmp_path / "cli.db"), "init-db"]) == 0
    assert httpx_log.level == logging.WARNING and core_log.level == logging.WARNING
    httpx_log.setLevel(logging.DEBUG)
    core_log.setLevel(logging.DEBUG)
    quiet_http_loggers()
    create_router(require_token_dependency=lambda: None)
    assert httpx_log.level == logging.WARNING and core_log.level == logging.WARNING
    httpx_log.info("device %s", TOKEN)
    assert TOKEN not in caplog.text


def test_file_receipts_persist_started_failed_and_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr("neckline.screening.deepseek.time.sleep", lambda _s: None)
    receipts = FileReceipts(tmp_path)
    calls = []

    def handler(_request):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 2}})

    client = DeepSeekClient(endpoint="https://example.invalid/chat/completions", model="m", api_key="k",
                            receipts=receipts, timeout_seconds=5, max_attempts=3,
                            transport=httpx.MockTransport(handler))
    prompt = [{"role": "user", "content": "a"}]
    digest = hashlib.sha256(canonical(client.request_body(prompt, {})).encode()).hexdigest()
    receipts.begin(digest)
    assert receipts.lookup(digest)["state"] == "started"
    with pytest.raises(ChatUnknown):
        client.complete(prompt, {})
    receipts.unknown(digest, "transport:ReadTimeout")
    assert receipts.lookup(digest)["state"] == "unknown"
    with pytest.raises(ChatUnknown):
        client.complete(prompt, {})
    assert calls == []
    receipts.fail(digest, "http:500")
    assert receipts.lookup(digest)["state"] == "failed"
    done = client.complete(prompt, {})
    assert calls == [1] and not done.from_receipt and receipts.lookup(digest)["state"] == "succeeded"
    assert client.complete(prompt, {}).from_receipt and calls == [1]
    assert list(tmp_path.glob("*.tmp")) == []
    legacy = tmp_path / f"{'b' * 64}.json"
    legacy.write_text(json.dumps({"response": {"choices": [{"message": {"content": "old"}, "finish_reason": "stop"}],
                                               "usage": {}}}), encoding="utf-8")
    assert receipts.lookup("b" * 64)["state"] == "succeeded"
    bare = DeepSeekClient(endpoint="https://example.invalid/chat/completions", model="m", api_key=None,
                          receipts=receipts, timeout_seconds=5, max_attempts=1)
    with pytest.raises(ChatRejected, match="凭据缺失"):
        bare.complete([{"role": "user", "content": "c"}], {})
    with pytest.raises(ChatRejected, match="拒绝了请求（http:401）"):
        DeepSeekClient(endpoint="https://example.invalid/chat/completions", model="m", api_key="k",
                       receipts=receipts, timeout_seconds=5, max_attempts=3,
                       transport=httpx.MockTransport(_status(401))).complete([{"role": "user", "content": "other"}], {})


def test_calendar_gap_pushes_only_on_weekdays(path):
    _with_device(path)
    apns = FakeApns()
    seen = []
    now = datetime(2027, 1, 4, 8, 2, tzinfo=CST)
    monday = run_morning(path, now=now, config=_config(), clock=lambda: now,
                         collector=lambda moment: seen.append(moment) or {"status": "completed"},
                         client_factory=lambda _rid: None, pusher=_push(path, apns, now))
    assert monday["state"] == "calendar_gap" and seen == [now]
    assert apns.sent == [(TOKEN, "晨报出问题了", "交易日历未覆盖 2027 年，今天不出报")]
    again = run_morning(path, now=now, config=_config(), clock=lambda: now,
                        collector=lambda moment: seen.append(moment) or {"status": "completed"},
                        client_factory=lambda _rid: None, pusher=_push(path, apns, now))
    assert again["state"] == "calendar_gap" and len(apns.sent) == 1 and len(seen) == 2
    saturday = datetime(2027, 1, 2, 8, 2, tzinfo=CST)
    weekend = run_morning(path, now=saturday, config=_config(), clock=lambda: saturday,
                          collector=lambda moment: seen.append(moment) or {"status": "completed"},
                          client_factory=lambda _rid: None, pusher=_push(path, apns, saturday))
    assert weekend["state"] == "calendar_gap" and "push" not in weekend and len(apns.sent) == 1 and len(seen) == 3
    with db.read(path) as conn:
        assert conn.execute("SELECT count(*) FROM reports").fetchone()[0] == 0
    pushed = finalize(path, now=datetime(2027, 1, 5, 9, 15, tzinfo=CST), config=_config(), pusher=_push(path, apns, now))
    assert pushed["state"] == "calendar_gap" and apns.sent[-1][2] == "交易日历未覆盖 2027 年，今天不出报"
    quiet = finalize(path, now=datetime(2027, 1, 3, 9, 15, tzinfo=CST), config=_config(), pusher=_push(path, apns, now))
    assert quiet["state"] == "calendar_gap" and len(apns.sent) == 2
    with db.read(path) as conn:
        assert conn.execute("SELECT count(*) FROM reports").fetchone()[0] == 0
