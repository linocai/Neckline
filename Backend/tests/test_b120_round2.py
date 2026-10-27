"""4.0.1 B120 第二轮：截止竞态、配置校验、回看截断、中断续采、推送互斥、超长版本号。"""
from __future__ import annotations

import json
import multiprocessing
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.morning import cli, db
from neckline.morning.api import create_router
from neckline.morning.collect import run_collection
from neckline.morning.report import Pusher, finalize, load_config
from neckline.mcp.server import _document

from tests.test_b120_block_a import _patch_sender
from tests.test_b120_block_b import _FakeJin10, _config as collection_config, _flash, at
from tests.test_morning import _config, _with_device


@pytest.fixture
def path(tmp_path):
    target = tmp_path / "v4.db"
    db.init_schema(target)
    return target


def test_finalize_reads_running_then_worker_finishes_and_fail_updates_nothing(path):
    """finalize 读到 running 后，worker 先把报告写成 available，fail 更新 0 行，不能推「出问题了」。"""
    _with_device(path)
    now = at("2026-10-13", "09:16")
    config = _config()
    report_id = "morning-2026-10-13"
    with db.write(path) as conn:
        db.begin_report(conn, report_id=report_id, trade_date="2026-10-13",
                        window_start=at("2026-10-12", "15:00"), window_end=at("2026-10-13", "08:00"),
                        config_version=config.rules["configVersion"], config_sha256=config.sha256, started_at=now)
    sent = []
    real = db.fail_report
    seen = {}

    def worker_wins(conn, **kwargs):
        content = json.dumps({"entries": [{"rank": 1}]}, ensure_ascii=False)
        conn.execute("""UPDATE reports SET status='available', content_json=?, gap_summary=NULL, available_at=?
                        WHERE report_id=? AND status='running'""", (content, db.utc(now), kwargs["report_id"]))
        real(conn, **kwargs)
        seen["changes"] = conn.execute("SELECT changes()").fetchone()[0]

    db.fail_report = worker_wins
    try:
        result = finalize(path, now=now, config=config, pusher=Pusher(path, lambda *args, **kwargs: sent.append((args, kwargs)) or SimpleNamespace(ok=True), lambda: now))
    finally:
        db.fail_report = real
    assert seen["changes"] == 0
    assert result["state"] == "available" and result["entries"] == 1
    assert sent and sent[0][0][1] == "晨报已出"
    assert all(call[0][1] != "晨报出问题了" for call in sent)


def _install_rules(monkeypatch, rules: dict) -> None:
    payload = json.dumps(rules).encode()
    from neckline.morning import report as morning_report
    original = morning_report.resources.files

    class _File:
        def read_bytes(self):
            return payload

    class _Tree:
        def __init__(self, package: str) -> None:
            self._real = original(package)

        def __truediv__(self, name: str):
            if name == "screening-v3.json":
                return _File()
            return self._real / name

    monkeypatch.setattr(morning_report.resources, "files", lambda package: _Tree(package))


@pytest.mark.parametrize("mutate,needle", [
    (lambda rules: rules.pop("schedule"), "schedule"),
    (lambda rules: rules["model"].pop("endpoint"), "model.endpoint"),
    (lambda rules: rules["schedule"].__setitem__("deadline", "25:00"), "schedule.deadline"),
])
def test_invalid_rule_shape_pushes_and_exits_zero_without_retrying_collection(path, monkeypatch, mutate, needle):
    rules = json.loads(json.dumps(load_config().rules))
    mutate(rules)
    _install_rules(monkeypatch, rules)
    with pytest.raises(ValueError, match=needle):
        load_config()
    collects = []
    monkeypatch.setattr(cli, "run_collection", lambda *_args, **_kwargs: collects.append(1) or {"status": "completed"})
    apns = _patch_sender(monkeypatch)
    _with_device(path)
    morning = ["--db", str(path), "morning", "--now", "2026-10-13T08:02:00+08:00"]
    finalize_argv = ["--db", str(path), "finalize", "--now", "2026-10-14T09:15:00+08:00"]
    assert cli.main(morning) == 0
    assert cli.main(morning) == 0
    assert collects == [1, 1]
    assert cli.main(finalize_argv) == 0
    assert collects == [1, 1]
    assert [item[1] for item in apns.sent] == ["晨报出问题了", "晨报出问题了"]
    assert [item[2] for item in apns.sent] == ["筛选规则配置无效，今天不出报", "筛选规则配置无效，今天不出报"]
    with db.read(path) as conn:
        assert conn.execute("SELECT count(*) FROM reports").fetchone()[0] == 0


def test_covered_slices_do_not_eat_the_lookback_so_late_news_is_fetched(tmp_path):
    path = tmp_path / "v4.db"
    db.init_schema(path)
    start, filled, later = at("2026-10-12", "15:00"), at("2026-10-12", "20:00"), at("2026-10-12", "20:30")
    slices = [
        (at("2026-10-12", "15:00"), at("2026-10-12", "16:00")),
        (datetime.fromisoformat("2026-10-12T16:00:01+08:00"), datetime.fromisoformat("2026-10-12T17:00:01+08:00")),
        (datetime.fromisoformat("2026-10-12T17:00:02+08:00"), datetime.fromisoformat("2026-10-12T18:00:02+08:00")),
        (datetime.fromisoformat("2026-10-12T18:00:03+08:00"), datetime.fromisoformat("2026-10-12T19:00:03+08:00")),
        (datetime.fromisoformat("2026-10-12T19:00:04+08:00"), filled),
    ]

    def source(key, *, complete, covered=None):
        item = {"sourceKey": key, "requestedStart": start.isoformat(), "requestedEnd": filled.isoformat(),
                "complete": complete, "error": None if complete else "request_bound_reached",
                "requests": 1, "documents": 0, "newVersions": 0, "rejected": 0, "oldestSeen": None}
        if covered is not None:
            item["covered"] = [[lo.isoformat(), hi.isoformat()] for lo, hi in covered]
        return item

    with db.write(path) as conn:
        db.begin_collection(conn, run_id="partial", slot_at=filled, started_at=filled)
        db.finish_collection(conn, run_id="partial", status="partial", finished_at=filled, sources=[
            source("tushare-major-news", complete=False, covered=slices),
            source("jin10-flash", complete=True), source("jin10-news", complete=True),
        ])
        db.begin_collection(conn, run_id="done", slot_at=filled, started_at=filled)
        db.finish_collection(conn, run_id="done", status="completed", finished_at=filled, sources=[
            source("tushare-major-news", complete=True),
            source("jin10-flash", complete=True), source("jin10-news", complete=True),
        ])
    calls = []

    def post(payload):
        window = (payload["params"]["start_date"], payload["params"]["end_date"])
        calls.append(window)
        if window[0] <= "2026-10-12 19:30:00" <= window[1]:
            return {"code": 0, "data": {"items": [["2026-10-12 19:30:00", "证券时报", "迟到的19:30新闻", "迟到正文"]]}}
        return {"code": 0, "data": {"items": []}}

    result = run_collection(
        path, now=later, config=collection_config(max_requests=8), tokens={"TUSHARE_TOKEN": "t", "JIN10_MCP_TOKEN": "j"},
        jin10_factory=lambda **_kwargs: _FakeJin10([([_flash(later)], False)]), tushare_post=post, clock=lambda: later)
    tushare = next(source for source in result["sources"] if source["sourceKey"] == "tushare-major-news")
    assert tushare["requestedStart"] == at("2026-10-12", "19:00").isoformat()
    assert any(start_at <= "2026-10-12 19:30:00" <= end_at and start_at < "2026-10-12 20:00:00"
               for start_at, end_at in calls)
    with db.read(path) as conn:
        titles = [row[0] for row in conn.execute("SELECT title FROM document_versions")]
    assert "迟到的19:30新闻" in titles


class _Jin10Crash(BaseException):
    pass


def test_tushare_slices_survive_a_jin10_crash_and_are_not_requested_again(tmp_path):
    path = tmp_path / "v4.db"
    db.init_schema(path)
    now, bootstrap = at("2026-10-12", "20:00"), at("2026-10-12", "15:00")
    calls = []

    def post(payload):
        params = payload["params"]
        calls.append((params["start_date"], params["end_date"]))
        return {"code": 0, "data": {"items": []}}

    def crash(**_kwargs):
        raise _Jin10Crash("jin10")

    tokens = {"TUSHARE_TOKEN": "t", "JIN10_MCP_TOKEN": "j"}
    with pytest.raises(_Jin10Crash):
        run_collection(path, now=now, config=collection_config(max_requests=2), tokens=tokens,
                       bootstrap_start=bootstrap, jin10_factory=crash, tushare_post=post, clock=lambda: now)
    first = list(calls)
    assert first == [
        ("2026-10-12 19:00:04", "2026-10-12 20:00:00"),
        ("2026-10-12 18:00:03", "2026-10-12 19:00:03"),
    ]
    with db.read(path) as conn:
        row = conn.execute("SELECT status, sources_json FROM collection_runs").fetchone()
    assert row["status"] == "running"
    saved = json.loads(row["sources_json"])
    assert [item["sourceKey"] for item in saved] == ["tushare-major-news"]
    assert saved[0]["covered"]
    run_collection(path, now=now, config=collection_config(max_requests=2), tokens=tokens,
                   jin10_factory=lambda **_kwargs: _FakeJin10([([_flash(bootstrap)], False)]),
                   tushare_post=post, clock=lambda: now)
    second = calls[len(first):]
    assert ("2026-10-12 19:00:04", "2026-10-12 20:00:00") not in second
    assert ("2026-10-12 18:00:03", "2026-10-12 19:00:03") not in second
    assert any(start >= "2026-10-12 19:00:00" for start, _end in second)


def _record_send(token, title, body, thread_id=None, collapse_id=None):
    with open(os.environ["NECKLINE_PUSH_TEST_LOG"], "a", encoding="utf-8") as handle:
        handle.write(token + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    time.sleep(0.4)
    return SimpleNamespace(ok=True, reason="")


def _notify_child(db_path: str, log_path: str, start: float, queue) -> None:
    os.environ["NECKLINE_PUSH_TEST_LOG"] = log_path
    try:
        while time.time() < start:
            time.sleep(0.01)
        pusher = Pusher(Path(db_path), _record_send, lambda: datetime(2026, 10, 13, 1, tzinfo=timezone.utc))
        result = pusher.notify(report_id="morning-2026-10-13", kind="problem", title="晨报出问题了", body="正文")
        queue.put(("ok", result["sent"]))
    except Exception as exc:
        queue.put(("err", f"{type(exc).__name__}: {exc}"))


def test_concurrent_notify_sends_each_token_once(path):
    tokens = ("aa" * 32, "bb" * 32)
    with db.write(path) as conn:
        for token in tokens:
            db.upsert_device(conn, token=token, platform="ios", at=at("2026-10-12", "12:00"))
    log = path.parent / "sent.txt"
    log.write_text("")
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    start = time.time() + 1.5
    procs = [ctx.Process(target=_notify_child, args=(str(path), str(log), start, queue)) for _ in range(2)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(15)
        assert proc.exitcode == 0
    messages = [queue.get(timeout=5) for _ in procs]
    assert messages == [("ok", 2), ("ok", 0)] or messages == [("ok", 0), ("ok", 2)]
    assert sorted(line for line in log.read_text(encoding="utf-8").splitlines() if line) == sorted(tokens)
    with db.read(path) as conn:
        rows = conn.execute("SELECT device_token FROM push_log WHERE state='sent'").fetchall()
    assert sorted(row[0] for row in rows) == sorted(tokens)


def test_five_thousand_digit_revision_is_not_found(path, monkeypatch):
    doc_id = "doc_" + "ab" * 16
    huge = f"{doc_id}@" + "9" * 5000
    assert db.parse_document_ref(huge) is None
    assert db.parse_document_ref(f"{doc_id}@" + "1" * 19) is None
    assert db.parse_document_ref(f"{doc_id}@" + "1" * 18) == (doc_id, int("1" * 18))
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    app = FastAPI()
    app.include_router(create_router(require_token_dependency=lambda: None))
    client = TestClient(app)
    response = client.get(f"/api/v4/documents/{huge}")
    assert response.status_code == 404 and response.json() == {"detail": {"reason": "no_document"}}
    assert _document(huge) == {"status": "no_document", "message": "没有这篇原文。"}
