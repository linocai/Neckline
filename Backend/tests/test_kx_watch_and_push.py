"""KX V4 20:00 盘后轮询检测与仅推 iPhone 的 APNs 通知测试。"""
from __future__ import annotations

import copy
from datetime import date, datetime, time, timezone
from pathlib import Path

import httpx
import pytest

from neckline.kx import services as kx_services
from neckline.kx.watch import run_watch
from neckline.morning import db
from neckline.morning.schedule import CST
from neckline.notify_kinds import CATEGORY_DIGEST, CATEGORY_IMPORTANT
from neckline.push.apns import PushResult
from tests.kx_support import READ_KEY, Clock, FakeKx, at, load_overview, run, services_for


@pytest.fixture(autouse=True)
def _configured_read_key(monkeypatch):
    monkeypatch.setenv("KX_READ_KEY", READ_KEY)


def _setup_db(tmp_path: Path) -> Path:
    path = tmp_path / "v4.db"
    db.init_schema(path)
    with db.write(path) as conn:
        db.upsert_device(conn, token="ios-phone-1", platform="ios", at=datetime(2026, 11, 20, tzinfo=timezone.utc))
        db.upsert_device(conn, token="mac-laptop-1", platform="macos", at=datetime(2026, 11, 20, tzinfo=timezone.utc))
    return path


def _make_doc(*, as_of: str, version: str, deepseek_status: str = "ok", refresh_result: str = "updated",
              notice: str | None = None, push_summary: str | None = None) -> dict:
    doc = load_overview()
    doc["as_of"] = as_of
    doc["version"] = version
    doc["deepseek_status"] = deepseek_status
    doc["status"] = "ok" if (deepseek_status == "ok" and refresh_result != "failed") else "partial"
    doc["deepseek_notice"] = notice if deepseek_status != "ok" else None
    if push_summary is not None:
        doc["push_summary"] = push_summary
    doc["refresh"] = {
        "date": as_of if refresh_result != "failed" else "2026-11-23",
        "result": refresh_result,
        "pool_version": version,
        "as_of": as_of,
        "deepseek_status": deepseek_status,
        "finished_at": f"{as_of}T20:03:00+08:00",
        "message": notice or "20:00 盘后整理完成。",
        "notice": notice,
        "elapsed_seconds": 38.2,
    }
    return doc


def test_watch_skips_non_trading_day(tmp_path):
    db_path = _setup_db(tmp_path)
    clock = Clock(at("2026-11-22", "20:02"))  # 周日
    fake = FakeKx(load_overview())
    sent: list[dict] = []

    result = run(run_watch(
        services_for(fake, clock),
        db_path=db_path,
        send_push=lambda *a, **kw: sent.append({"a": a, "kw": kw}) or PushResult(True, 200, "ok"),
    ))
    assert result["status"] == "skipped"
    assert result["reason"] == "non_trading_day"
    assert fake.calls == [] and sent == []


def test_watch_polls_until_new_version_then_pushes_only_iphone_and_deduplicates(tmp_path):
    db_path = _setup_db(tmp_path)
    clock = Clock(at("2026-11-23", "20:02"))  # 周一交易日
    old_doc = _make_doc(as_of="2026-11-20", version="kx-20261120-003")
    new_doc = _make_doc(
        as_of="2026-11-23",
        version="kx-20261123-001",
        push_summary="11-23 盘后整理完成｜冰点修复｜候选 20/25（新进 2 · 升龙头 1）",
    )
    fake = FakeKx(new_doc, sequence=[old_doc, new_doc])
    sent: list[dict] = []

    def fake_send(token, title, body, **kw):
        sent.append({"token": token, "title": title, "body": body, **kw})
        return PushResult(True, 200, "ok")

    res = run(run_watch(
        services_for(fake, clock),
        db_path=db_path,
        poll_seconds=60.0,
        send_push=fake_send,
    ))
    assert res["status"] == "ready"
    assert res["version"] == "kx-20261123-001"
    assert res["attempts"] == 2
    assert clock.sleeps == [60.0]
    # 仅推 iPhone，绝不推 macOS
    assert len(sent) == 1
    assert sent[0]["token"] == "ios-phone-1"
    assert sent[0]["category"] == CATEGORY_DIGEST
    assert sent[0]["thread_id"] == "kx"
    assert sent[0]["custom"]["target"] == "kx"
    assert sent[0]["body"] == "11-23 盘后整理完成｜冰点修复｜候选 20/25（新进 2 · 升龙头 1）"

    # 缓存已落库
    with db.read(db_path) as conn:
        cached = db.get_kx_board_cache(conn)
        assert cached is not None and cached["boardVersion"] == "kx-20261123-001"

    # 同一版本重复运行绝不重复推送
    res_again = run(run_watch(
        services_for(FakeKx(new_doc), clock),
        db_path=db_path,
        once=True,
        send_push=fake_send,
    ))
    assert res_again["status"] == "ready"
    assert res_again["push"]["skipped"] == 1
    assert len(sent) == 1


def test_watch_pushes_partial_notice_when_deepseek_falls_back(tmp_path):
    db_path = _setup_db(tmp_path)
    clock = Clock(at("2026-11-23", "20:05"))
    doc = _make_doc(
        as_of="2026-11-23",
        version="kx-20261123-001",
        deepseek_status="failed",
        notice="DeepSeek 超时，已刷新当日程序事实并沿用旧结构。",
        push_summary="11-23 盘后事实已更新｜DeepSeek 判断未完成（沿用旧结构 19/25）",
    )
    sent: list[dict] = []
    res = run(run_watch(
        services_for(FakeKx(doc), clock),
        db_path=db_path,
        send_push=lambda *a, **kw: sent.append({"a": a, "kw": kw}) or PushResult(True, 200, "ok"),
    ))
    assert res["status"] == "partial"
    assert len(sent) == 1
    assert sent[0]["a"][1] == "KX 盘后事实已更新（判断未完成）"
    assert sent[0]["kw"]["category"] == CATEGORY_DIGEST


def test_watch_pushes_important_when_upstream_refresh_fails(tmp_path):
    db_path = _setup_db(tmp_path)
    clock = Clock(at("2026-11-23", "20:06"))
    doc = _make_doc(
        as_of="2026-11-20",
        version="kx-20261120-003",
        refresh_result="failed",
        notice="今日未更新：上游 TuShare 取数失败，沿用上一版结构。",
    )
    sent: list[dict] = []
    res = run(run_watch(
        services_for(FakeKx(doc), clock),
        db_path=db_path,
        send_push=lambda *a, **kw: sent.append({"a": a, "kw": kw}) or PushResult(True, 200, "ok"),
    ))
    assert res["status"] == "failed"
    assert len(sent) == 1
    assert sent[0]["a"][1] == "KX 盘后整理失败"
    assert "TuShare" in sent[0]["a"][2]
    assert sent[0]["kw"]["category"] == CATEGORY_IMPORTANT


def test_watch_pushes_important_on_timeout_at_2045(tmp_path):
    db_path = _setup_db(tmp_path)
    clock = Clock(at("2026-11-23", "20:43"))
    old_doc = _make_doc(as_of="2026-11-20", version="kx-20261120-003")
    sent: list[dict] = []

    res = run(run_watch(
        services_for(FakeKx(old_doc), clock),
        db_path=db_path,
        deadline_clock=time(20, 45, tzinfo=CST),
        poll_seconds=60.0,
        send_push=lambda *a, **kw: sent.append({"a": a, "kw": kw}) or PushResult(True, 200, "ok"),
    ))
    assert res["status"] == "timeout"
    assert len(sent) == 1
    assert sent[0]["a"][1] == "KX 盘后整理超时未更新"
    assert "20:45" in sent[0]["a"][2]
    assert sent[0]["kw"]["category"] == CATEGORY_IMPORTANT


def test_watch_pushes_important_immediately_on_unsupported_schema(tmp_path):
    db_path = _setup_db(tmp_path)
    clock = Clock(at("2026-11-23", "20:03"))
    bad_doc = load_overview()
    bad_doc["schema_version"] = 6
    sent: list[dict] = []

    res = run(run_watch(
        services_for(FakeKx(bad_doc), clock),
        db_path=db_path,
        send_push=lambda *a, **kw: sent.append({"a": a, "kw": kw}) or PushResult(True, 200, "ok"),
    ))
    assert res["status"] == "failed"
    assert res["reason"] == "kx_unsupported_schema"
    assert res["attempts"] == 1
    assert len(sent) == 1
    assert sent[0]["kw"]["category"] == CATEGORY_IMPORTANT


def test_watch_waits_for_matching_refresh_receipt_before_pushing(tmp_path):
    db_path = _setup_db(tmp_path)
    clock = Clock(at("2026-11-23", "20:02"))
    # 上游刚写完 pool.json（as_of 已是当日），但 status.json 还未写或仍是 skipped/旧 version
    unconfirmed_null = _make_doc(as_of="2026-11-23", version="kx-20261123-001")
    unconfirmed_null["refresh"] = None
    unconfirmed_mismatch = _make_doc(as_of="2026-11-23", version="kx-20261123-001")
    unconfirmed_mismatch["refresh"]["pool_version"] = "kx-20261120-003"
    confirmed = _make_doc(as_of="2026-11-23", version="kx-20261123-001")
    fake = FakeKx(confirmed, sequence=[unconfirmed_null, unconfirmed_mismatch, confirmed])
    sent: list[dict] = []

    res = run(run_watch(
        services_for(fake, clock),
        db_path=db_path,
        poll_seconds=60.0,
        send_push=lambda *a, **kw: sent.append({"a": a, "kw": kw}) or PushResult(True, 200, "ok"),
    ))
    assert res["status"] == "ready"
    assert res["attempts"] == 3
    assert len(sent) == 1


def test_watch_preclaims_sending_and_retries_only_failed_device(tmp_path):
    db_path = _setup_db(tmp_path)
    with db.write(db_path) as conn:
        db.upsert_device(conn, token="ios-phone-2", platform="ios", at=datetime(2026, 11, 20, tzinfo=timezone.utc))
    clock = Clock(at("2026-11-23", "20:02"))
    doc = _make_doc(as_of="2026-11-23", version="kx-20261123-001")
    calls: list[str] = []

    def flaky_send(token, title, body, **kw):
        calls.append(token)
        if token == "ios-phone-2" and calls.count("ios-phone-2") == 1:
            return PushResult(False, 500, "apns_temp_error")
        return PushResult(True, 200, "ok")

    res = run(run_watch(
        services_for(FakeKx(doc), clock),
        db_path=db_path,
        poll_seconds=60.0,
        send_push=flaky_send,
    ))
    assert res["status"] == "ready"
    # ios-phone-1 只发一次，ios-phone-2 第一次失败后重试一次成功
    assert calls == ["ios-phone-1", "ios-phone-2", "ios-phone-2"]

    # 同一版本若切换成 partial，也不得再重复发送
    doc_partial = _make_doc(as_of="2026-11-23", version="kx-20261123-001", deepseek_status="failed")
    res_partial = run(run_watch(
        services_for(FakeKx(doc_partial), clock),
        db_path=db_path,
        once=True,
        send_push=flaky_send,
    ))
    assert res_partial["push"]["skipped"] == 2
    assert len(calls) == 3


def test_watch_unknown_apns_status_zero_keeps_sending_and_never_retries(tmp_path):
    db_path = _setup_db(tmp_path)
    clock = Clock(at("2026-11-23", "20:02"))
    doc = _make_doc(as_of="2026-11-23", version="kx-20261123-001")
    calls: list[str] = []

    def timeout_send(token, title, body, **kw):
        calls.append(token)
        return PushResult(False, 0, "apns_timeout")

    res = run(run_watch(
        services_for(FakeKx(doc), clock),
        db_path=db_path,
        poll_seconds=60.0,
        send_push=timeout_send,
    ))
    assert res["status"] == "push_failed" and res["boardStatus"] == "ready"
    assert res["push"]["unknown"] == 1
    assert calls == ["ios-phone-1"]
    assert clock.sleeps == []
    with db.read(db_path) as conn:
        state = conn.execute("SELECT state FROM kx_push_log WHERE device_token='ios-phone-1'").fetchone()[0]
        assert state == "sending"


def test_watch_freezes_ready_event_during_retry_without_repolling_kx(tmp_path):
    db_path = _setup_db(tmp_path)
    with db.write(db_path) as conn:
        db.upsert_device(conn, token="ios-phone-2", platform="ios", at=datetime(2026, 11, 20, tzinfo=timezone.utc))
    clock = Clock(at("2026-11-23", "20:02"))
    doc = _make_doc(as_of="2026-11-23", version="kx-20261123-001")
    fake = FakeKx(doc)
    calls: list[str] = []

    def retry_while_kx_down(token, title, body, **kw):
        calls.append(token)
        if token == "ios-phone-2" and calls.count("ios-phone-2") == 1:
            fake.down = True  # 首轮判定 ready 后上游宕机：重试绝不再拉 KX，也不改报 timeout
            return PushResult(False, 500, "apns_temp_error")
        return PushResult(True, 200, "ok")

    res = run(run_watch(
        services_for(fake, clock),
        db_path=db_path,
        poll_seconds=60.0,
        send_push=retry_while_kx_down,
    ))
    assert res["status"] == "ready"
    assert len(fake.calls) == 1
    assert calls == ["ios-phone-1", "ios-phone-2", "ios-phone-2"]
    assert res["push"]["sent"] == 2 and res["push"]["failed"] == 0


def test_watch_stops_retry_cleanly_when_slow_apns_crosses_deadline(tmp_path):
    db_path = _setup_db(tmp_path)
    clock = Clock(at("2026-11-23", "20:44", 30))
    doc = _make_doc(as_of="2026-11-23", version="kx-20261123-001")
    calls: list[str] = []

    def slow_503(token, title, body, **kw):
        calls.append(token)
        clock.advance(45)  # 耗时跨过 20:45 截止线
        return PushResult(False, 503, "apns_busy")

    res = run(run_watch(
        services_for(FakeKx(doc), clock),
        db_path=db_path,
        deadline_clock=time(20, 45, tzinfo=CST),
        poll_seconds=60.0,
        send_push=slow_503,
    ))
    assert res["status"] == "push_failed" and res["boardStatus"] == "ready"
    assert calls == ["ios-phone-1"]
    assert clock.sleeps == []
    assert res["push"]["failed"] == 1


def test_watch_hot_cache_does_not_swallow_next_day_upstream_failure(tmp_path):
    """P1-1: 热缓存已有上一交易日 confirmed updated，次日上游整理失败沿用旧板时必须立刻识别 failed，不得吞掉变 timeout。"""
    db_path = _setup_db(tmp_path)
    # 1. 先预热周五已确认成功快照
    fri_clock = Clock(at("2026-11-20", "20:05"))
    fri_doc = _make_doc(as_of="2026-11-20", version="kx-20261120-003")
    res_fri = run(run_watch(
        services_for(FakeKx(fri_doc), fri_clock),
        db_path=db_path,
        once=True,
        send_push=lambda *a, **kw: PushResult(True, 200, "ok"),
    ))
    assert res_fri["status"] == "ready"

    # 2. 周一整理失败：看板仍是周五旧版，但 refresh.date=2026-11-23, result=failed
    mon_clock = Clock(at("2026-11-23", "20:06"))
    mon_fail = _make_doc(
        as_of="2026-11-20",
        version="kx-20261120-003",
        refresh_result="failed",
        notice="今日未更新：上游 TuShare 失败，沿用上一版。",
    )
    mon_fail["refresh"]["finished_at"] = "2026-11-23T20:05:30+08:00"
    sent: list[dict] = []
    res_mon = run(run_watch(
        services_for(FakeKx(mon_fail), mon_clock),
        db_path=db_path,
        poll_seconds=60.0,
        send_push=lambda *a, **kw: sent.append({"a": a, "kw": kw}) or PushResult(True, 200, "ok"),
    ))
    assert res_mon["status"] == "failed"
    assert res_mon["attempts"] == 1
    assert len(sent) == 1 and sent[0]["kw"]["category"] == CATEGORY_IMPORTANT
    with db.read(db_path) as conn:
        cached = db.get_kx_board_cache(conn)
        assert cached["raw"]["refresh"]["result"] == "failed"
        assert cached["raw"]["refresh"]["date"] == "2026-11-23"


def test_timeout_write_is_rejected_if_confirmed_today_board_already_committed(tmp_path):
    """P2-2: 当日确认版已落库时，并发迟到的超时写库必须被事务拒绝且不留脏告警。"""
    db_path = _setup_db(tmp_path)
    now = at("2026-11-23", "20:45")
    doc = _make_doc(as_of="2026-11-23", version="kx-20261123-001")
    with db.write_immediate(db_path) as conn:
        db.save_kx_board_cache(
            conn, schema_version=5, board_version=doc["version"], as_of=doc["as_of"],
            generated_at=doc["generated_at"], deepseek_status="ok", status="ok", raw=doc, fetched_at=now,
        )
    with db.write_immediate(db_path) as conn:
        accepted, winner = db.record_kx_board_timeout(conn, trade_date="2026-11-23", notice="迟到的超时", at=now)
    assert accepted is False and winner is not None and winner["asOf"] == "2026-11-23"
    with db.read(db_path) as conn:
        cached = db.get_kx_board_cache(conn)
        assert cached["timeoutNotice"] is None and cached["lastError"] is None


def test_permanent_410_and_unknown_preserve_failure_across_restarts_while_not_dispatched_recovers(tmp_path):
    """P2-3 & P2-4: 410 永久拒绝与 status=0 未知结果跨重启不重发且保留失败统计；本地凭据未外呼(dispatched=False)修复后可重试成功。"""
    db_path = _setup_db(tmp_path)
    with db.write(db_path) as conn:
        db.upsert_device(conn, token="ios-dead", platform="ios", at=datetime(2026, 11, 20, tzinfo=timezone.utc))
        db.upsert_device(conn, token="ios-timeout", platform="ios", at=datetime(2026, 11, 20, tzinfo=timezone.utc))
    clock = Clock(at("2026-11-23", "20:02"))
    doc = _make_doc(as_of="2026-11-23", version="kx-20261123-001")
    calls: list[str] = []
    creds_fixed = {"ok": False}

    def sender(token, title, body, **kw):
        calls.append(token)
        if token == "ios-dead":
            return PushResult(False, 410, "Unregistered")
        if token == "ios-timeout":
            return PushResult(False, 0, "transport_timeout", dispatched=True)
        if not creds_fixed["ok"]:
            return PushResult(False, 0, "apns_credentials_missing", dispatched=False)
        return PushResult(True, 200, "ok")

    first = run(run_watch(services_for(FakeKx(doc), clock), db_path=db_path, once=True, send_push=sender))
    assert first["status"] == "push_failed"
    assert first["push"]["permanentFailed"] == 1
    assert first["push"]["unknown"] == 1
    assert first["push"]["retryableFailed"] == 1
    assert calls == ["ios-dead", "ios-phone-1", "ios-timeout"]

    # 修复凭据后再次运行：仅重试未外呼的 ios-phone-1；ios-dead 与 ios-timeout 绝不再次外呼但仍计入 failed
    creds_fixed["ok"] = True
    second = run(run_watch(services_for(FakeKx(doc), clock), db_path=db_path, once=True, send_push=sender))
    assert calls == ["ios-dead", "ios-phone-1", "ios-timeout", "ios-phone-1"]
    assert second["status"] == "push_failed"
    assert second["push"]["sent"] == 1
    assert second["push"]["permanentFailed"] == 1
    assert second["push"]["unknown"] == 1
    assert second["push"]["failed"] == 2


def test_deadline_enforced_per_device_without_sending_past_2045(tmp_path):
    """P2-5: 逐设备检查 20:45 截止线，第一台耗时跨过 20:45 后第二台不再外呼并记入 deadlineMissed。"""
    db_path = _setup_db(tmp_path)
    with db.write(db_path) as conn:
        db.upsert_device(conn, token="ios-phone-2", platform="ios", at=datetime(2026, 11, 20, tzinfo=timezone.utc))
    clock = Clock(at("2026-11-23", "20:44", 55))
    doc = _make_doc(as_of="2026-11-23", version="kx-20261123-001")
    calls: list[str] = []

    def slow_first(token, title, body, **kw):
        calls.append(token)
        clock.advance(10)  # 20:44:55 -> 20:45:05
        return PushResult(True, 200, "ok")

    res = run(run_watch(
        services_for(FakeKx(doc), clock),
        db_path=db_path,
        deadline_clock=time(20, 45, tzinfo=CST),
        send_push=slow_first,
    ))
    assert calls == ["ios-phone-1"]
    assert res["status"] == "push_failed"
    assert res["push"]["sent"] == 1
    assert res["push"]["deadlineMissed"] == 1
    assert res["push"]["failed"] == 1


def test_failed_alert_unknown_preserves_failure_on_rerun_and_http2_init_error_not_dispatched(tmp_path, monkeypatch):
    """Round-4 P2-3 & P2-4: 失败告警首次未知(status=0)重跑仍计 unknown/failed；httpx.Client(http2=True) 缺 h2 标 dispatched=False。"""
    from neckline.push import apns

    # 1. 失败告警（skip_if_any_today=True）首次传输异常，重跑不得被吞成 skipped=1, failed=0
    db_path = _setup_db(tmp_path)
    clock = Clock(at("2026-11-23", "20:06"))
    fail_doc = _make_doc(as_of="2026-11-20", version="kx-20261120-003", refresh_result="failed", notice="上游失败")
    calls: list[str] = []

    def unknown_send(token, title, body, **kw):
        calls.append(token)
        return PushResult(False, 0, "transport_timeout", dispatched=True)

    r1 = run(run_watch(services_for(FakeKx(fail_doc), clock), db_path=db_path, once=True, send_push=unknown_send))
    assert r1["push"]["unknown"] == 1 and r1["push"]["failed"] == 1
    r2 = run(run_watch(services_for(FakeKx(fail_doc), clock), db_path=db_path, once=True, send_push=unknown_send))
    assert calls == ["ios-phone-1"]
    assert r2["push"]["unknown"] == 1 and r2["push"]["failed"] == 1 and r2["push"]["skipped"] == 0

    # 2. httpx.Client(http2=True) 缺 h2 抛出 ImportError 时未外呼（dispatched=False）
    def broken_client(*args, **kwargs):
        raise ImportError("Using http2=True, but the 'h2' package is not installed")

    monkeypatch.setattr(httpx, "Client", broken_client)
    res_h2 = apns._http2_post("https://api.push.apple.com/3/device/tok", {}, b"{}")
    assert res_h2.ok is False and res_h2.status == 0 and res_h2.dispatched is False


def test_rejected_timeout_for_newer_day_sends_no_alert_and_confirmed_past_deadline_misses(tmp_path):
    """Round-4 P2-2 & P2-5: 缓存已有更晚交易日时旧日超时静默 superseded；并发确认版过 20:45 仍受截止线约束。"""
    db_path = _setup_db(tmp_path)
    # 缓存已有 11-24 的确认版
    tue_doc = _make_doc(as_of="2026-11-24", version="kx-20261124-001")
    with db.write_immediate(db_path) as conn:
        db.save_kx_board_cache(
            conn, schema_version=5, board_version=tue_doc["version"], as_of=tue_doc["as_of"],
            generated_at=tue_doc["generated_at"], deepseek_status="ok", status="ok",
            raw=tue_doc, fetched_at=at("2026-11-24", "20:05"),
        )
    # 针对 11-23 触发超时：不得发出旧日 timeout 告警
    sent: list[str] = []
    res = run(run_watch(
        services_for(FakeKx(_make_doc(as_of="2026-11-20", version="kx-20261120-003")), Clock(at("2026-11-23", "20:46"))),
        db_path=db_path,
        target_date=date(2026, 11, 23),
        once=True,
        send_push=lambda token, *a, **kw: sent.append(token) or PushResult(True, 200, "ok"),
    ))
    assert res["status"] == "superseded" and sent == []

    # 针对 11-24 并发确认版在 20:45:01 触发收口：正常截止线生效，不越线发 ready
    db_path2 = _setup_db(tmp_path / "p25")
    with db.write_immediate(db_path2) as conn:
        db.save_kx_board_cache(
            conn, schema_version=5, board_version=tue_doc["version"], as_of=tue_doc["as_of"],
            generated_at=tue_doc["generated_at"], deepseek_status="ok", status="ok",
            raw=tue_doc, fetched_at=at("2026-11-24", "20:45", 1),
        )
    sent2: list[str] = []
    res2 = run(run_watch(
        services_for(FakeKx(_make_doc(as_of="2026-11-20", version="kx-20261120-003"), down=True), Clock(at("2026-11-24", "20:45", 1))),
        db_path=db_path2,
        target_date=date(2026, 11, 24),
        once=True,
        send_push=lambda token, *a, **kw: sent2.append(token) or PushResult(True, 200, "ok"),
    ))
    assert res2["status"] == "push_failed" and res2["boardStatus"] == "ready"
    assert res2["push"]["deadlineMissed"] == 1 and sent2 == []
