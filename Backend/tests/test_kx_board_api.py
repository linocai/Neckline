"""KX V4 看板后端测试：schema 5 严格校验、真实样本投影、SQLite 持久缓存、静默与手动刷新、脱敏及跨端夹具对拍。"""
from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.api import deps
from neckline.api.deps import require_token
from neckline.kx import services as kx_services
from neckline.kx.api import create_router
from neckline.kx.board import BoardUnavailable, build_board, reset_board_state
from neckline.kx.project import project_board, require_overview
from neckline.kx.source import KxError
from neckline.morning import db
from neckline.morning.api import create_router as create_morning_router
from tests.kx_support import (
    APP_FIXTURE_DIR, FIXTURE_DIR, READ_KEY, Clock, FakeKx, at, load_overview, run, services_for,
)

TOKEN = "a" * 16


@pytest.fixture(autouse=True)
def _configured_read_key(monkeypatch):
    monkeypatch.setenv("KX_READ_KEY", READ_KEY)


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "v4.db"
    db.init_schema(path)
    return path


def _app(db_path: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(create_morning_router(
        require_token_dependency=require_token,
        db_path_provider=lambda: db_path,
        version="v4.5.0",
        release_set="v4.5.0-b134",
    ))
    app.include_router(create_router(
        require_token_dependency=require_token,
        db_path_provider=lambda: db_path,
    ))
    return app


def test_real_v4_sample_projects_field_by_field(tmp_path):
    db_path = _db(tmp_path)
    clock = Clock(at("2026-11-22", "12:40", 0))
    fake = FakeKx(load_overview())
    kx_services.use(services_for(fake, clock))

    board = run(build_board(kx_services.current(), db_path=db_path))
    assert board["schemaVersion"] == 5
    assert board["status"] == "ok"
    assert board["version"] == "kx-20261120-003"
    assert board["asOf"] == "2026-11-20"
    assert board["deepseekStatus"] == "ok"
    assert board["stale"] is False
    assert board["staleReason"] is None
    assert board["pushSummary"].startswith("11-20 盘后整理完成")
    assert board["capacity"] == {
        "used": 19, "max": 25, "leaderCount": 8, "hotCount": 11, "elimCount": 0, "nextFree": 0,
    }
    assert len(board["sentiment"]["days"]) == 5
    latest_day = board["sentiment"]["days"][-1]
    assert latest_day["date"] == "2026-11-20"
    assert latest_day["zt"] == 25 and latest_day["dt"] == 57 and latest_day["zbRate"] == 21.9
    assert latest_day["maxLbc"] == 4
    assert [h["name"] for h in latest_day["highest"]] == ["真视通", "盛剑科技"]
    assert board["sentiment"]["judgment"]["stage"] == "退潮加速 · 局部反核"
    assert len(board["factions"]) >= 5
    assert len(board["items"]) == 19
    assert [it["zone"] for it in board["items"]] == ["leader"] * 8 + ["hot"] * 11

    first = board["items"][0]
    assert first["code"] == "002771"
    assert first["name"] == "真视通"
    assert first["facts"]["boardType"] == "换手板"
    assert len(first["facts"]["traj"]) == 5
    assert first["facts"]["lhb"] is not None
    assert first["judgment"]["rhythm"]["kind"] in {"surge", "pullback", "divergence", "washout", "dead"}
    assert any(f["primary"] for f in first["judgment"]["factions"])
    # 确认没有 V3 废弃字段
    assert "rooms" not in board and "session" not in board and "refreshSeconds" not in board


@pytest.mark.parametrize("bad_schema", [None, 4, 6, "5", True])
def test_unknown_schema_version_is_explicitly_rejected_without_force_parsing(tmp_path, bad_schema):
    db_path = _db(tmp_path)
    clock = Clock(at("2026-11-22", "20:05", 0))
    doc = load_overview()
    if bad_schema is None:
        doc.pop("schema_version", None)
    else:
        doc["schema_version"] = bad_schema
    fake = FakeKx(doc)
    kx_services.use(services_for(fake, clock))

    with pytest.raises(BoardUnavailable) as exc_info:
        run(build_board(kx_services.current(), db_path=db_path))
    assert exc_info.value.reason == "kx_unsupported_schema"
    assert "协议版本暂不支持" in exc_info.value.message
    with db.read(db_path) as conn:
        assert db.get_kx_board_cache(conn) is None


def test_sqlite_cache_persists_across_process_restart_and_supports_stale_fallback(tmp_path):
    db_path = _db(tmp_path)
    clock = Clock(at("2026-11-22", "20:05", 0))
    fake = FakeKx(load_overview())
    kx_services.use(services_for(fake, clock))

    first = run(build_board(kx_services.current(), db_path=db_path))
    assert len(fake.calls) == 1
    assert first["stale"] is False

    # 模拟进程重启：清掉内存态，1 分钟后再次打开仍直接读 SQLite，不再打上游
    reset_board_state()
    clock.advance(60)
    second = run(build_board(kx_services.current(), db_path=db_path))
    assert len(fake.calls) == 1
    assert second["version"] == first["version"]
    assert second["stale"] is False

    # 超过静默窗口且上游宕机：仍展示 SQLite 持久化快照并标 stale
    clock.advance(700)
    fake.down = True
    third = run(build_board(kx_services.current(), db_path=db_path))
    assert len(fake.calls) == 2
    assert third["stale"] is True
    assert "连不上" in (third["staleReason"] or "")
    assert third["version"] == first["version"]


def test_manual_refresh_bypasses_quiet_cache_with_short_cooldown(tmp_path):
    db_path = _db(tmp_path)
    clock = Clock(at("2026-11-22", "20:05", 0))
    doc1 = load_overview()
    doc2 = copy.deepcopy(doc1)
    doc2["version"] = "kx-20261120-004"
    fake = FakeKx(doc1, sequence=[doc1, doc2])
    kx_services.use(services_for(fake, clock))

    first = run(build_board(kx_services.current(), db_path=db_path))
    assert first["version"] == "kx-20261120-003"
    assert len(fake.calls) == 1

    # 30 秒内手动刷新应绕过 10 分钟静默缓存，拿到新版本
    clock.advance(30)
    refreshed = run(build_board(kx_services.current(), db_path=db_path, force_refresh=True))
    assert refreshed["version"] == "kx-20261120-004"
    assert len(fake.calls) == 2

    # 3 秒内连续狂点手动刷新受 10 秒防抖保护，不重复打上游
    clock.advance(3)
    throttled = run(build_board(kx_services.current(), db_path=db_path, force_refresh=True))
    assert throttled["version"] == "kx-20261120-004"
    assert len(fake.calls) == 2


def test_nullable_metrics_are_preserved_as_none_never_zero():
    doc = load_overview()
    doc["sentiment"]["days"][-1]["y_open_mean"] = None
    doc["sentiment"]["days"][-1]["y_close_med"] = None
    doc["items"][0]["facts"]["ma10"] = None
    doc["items"][0]["facts"]["ma10_diff"] = None
    doc["items"][0]["facts"]["lbc"] = None
    doc["items"][0]["facts"]["zt_5d_n"] = None
    doc["items"][0]["facts"]["dt_5d_n"] = None
    doc["items"][0]["facts"]["lhb_5d_n"] = None
    doc["items"][0]["facts"]["lhb"] = None
    doc["items"][0]["facts"]["lhb_is_today"] = False
    doc["items"][0]["facts"]["suspended"] = True

    projected = project_board(doc, now=at("2026-11-22", "20:05"), last_read_at=at("2026-11-22", "20:05"))
    day = projected["sentiment"]["days"][-1]
    assert day["yOpenMean"] is None and day["yCloseMed"] is None
    facts = projected["items"][0]["facts"]
    assert facts["ma10"] is None and facts["ma10Diff"] is None
    assert facts["lbc"] is None and facts["zt5dN"] is None and facts["dt5dN"] is None and facts["lhb5dN"] is None
    assert facts["lhb"] is None and facts["lhbIsToday"] is False and facts["suspended"] is True


def test_malformed_unhashable_fields_and_monotonic_cache_and_redaction(tmp_path):
    db_path = _db(tmp_path)
    clock = Clock(at("2026-11-22", "20:05", 0))
    newer = load_overview()
    newer["as_of"] = "2026-11-23"
    newer["version"] = "kx-20261123-001"
    newer["generated_at"] = "2026-11-23T20:04:00+08:00"
    newer["fact_warnings"] = [f"参考 https://linotsai.top/kx/read/{READ_KEY}?format=json 有偏差"]
    newer["push_summary"] = f"11-23 完成 https://linotsai.top/kx/read/{READ_KEY}"

    # 1. 正常落库且脱敏覆盖 API 与持久化原始缓存
    kx_services.use(services_for(FakeKx(newer), clock))
    first = run(build_board(kx_services.current(), db_path=db_path))
    assert READ_KEY not in first["pushSummary"] and READ_KEY not in first["factWarnings"][0]
    with db.read(db_path) as conn:
        raw_stored = json.dumps(db.get_kx_board_cache(conn)["raw"], ensure_ascii=False)
        assert READ_KEY not in raw_stored and "linotsai.top" not in raw_stored

    # 2. 较晚返回的旧版快照绝不覆盖库中较新版
    older = load_overview()
    older["as_of"] = "2026-11-20"
    older["version"] = "kx-20261120-003"
    clock.advance(20)
    kx_services.use(services_for(FakeKx(older), clock))
    after_old = run(build_board(kx_services.current(), db_path=db_path, force_refresh=True))
    assert after_old["version"] == "kx-20261123-001"

    # 3. 畸形 status=[] 不抛 TypeError，安全回落缓存并标 stale
    bad = load_overview()
    bad["status"] = []
    clock.advance(20)
    kx_services.use(services_for(FakeKx(bad), clock))
    fallback = run(build_board(kx_services.current(), db_path=db_path, force_refresh=True))
    assert fallback["stale"] is True and fallback["version"] == "kx-20261123-001"


def test_timeout_notice_survives_old_snapshot_refresh_until_new_day_arrives(tmp_path):
    db_path = _db(tmp_path)
    clock = Clock(at("2026-11-23", "20:46", 0))
    old_doc = load_overview()
    kx_services.use(services_for(FakeKx(old_doc), clock))
    run(build_board(kx_services.current(), db_path=db_path))
    with db.write(db_path) as conn:
        db.record_kx_board_timeout(conn, trade_date="2026-11-23", notice="截至 20:45 未检测到今日新版本。", at=clock.now)

    # 再次手动刷新依然只拿到昨天旧版：超时提示必须保留
    clock.advance(20)
    still_old = run(build_board(kx_services.current(), db_path=db_path, force_refresh=True))
    assert still_old["stale"] is True and "20:45" in (still_old["staleReason"] or "")

    # 11-23 新版到达且 refresh 已确认后超时提示自动清除
    new_doc = copy.deepcopy(old_doc)
    new_doc["as_of"] = "2026-11-23"
    new_doc["version"] = "kx-20261123-001"
    new_doc["generated_at"] = "2026-11-23T20:50:00+08:00"
    new_doc["refresh"] = {
        "date": "2026-11-23",
        "result": "updated",
        "pool_version": "kx-20261123-001",
        "as_of": "2026-11-23",
        "deepseek_status": "ok",
        "finished_at": "2026-11-23T20:50:00+08:00",
        "message": "ok",
        "notice": None,
        "elapsed_seconds": 12.0,
    }
    clock.advance(20)
    kx_services.use(services_for(FakeKx(new_doc), clock))
    fresh = run(build_board(kx_services.current(), db_path=db_path, force_refresh=True))
    assert fresh["stale"] is False and fresh["staleReason"] is None


def test_raw_cache_allowlist_monotone_receipt_empty_timeout_and_manual_cooldown(tmp_path):
    db_path = _db(tmp_path)
    clock = Clock(at("2026-11-23", "20:46", 0))

    # 1. 空缓存下记录超时提示：首份快照仍是昨日旧版时必须保留超时提示
    with db.write_immediate(db_path) as conn:
        db.record_kx_board_timeout(conn, trade_date="2026-11-23", notice="截至 20:45 未检测到今日新版本。", at=clock.now)
    yesterday = load_overview()
    yesterday["change_reason"] = f"内部诊断 https://linotsai.top/kx/read/{READ_KEY}"
    kx_services.use(services_for(FakeKx(yesterday), clock))
    first = run(build_board(kx_services.current(), db_path=db_path))
    assert first["stale"] is True and "20:45" in (first["staleReason"] or "")
    # 白名单已丢弃无关自由文本 change_reason
    with db.read(db_path) as conn:
        stored_raw = db.get_kx_board_cache(conn)["raw"]
        assert "change_reason" not in stored_raw

    # 2. 同版本已确认 refresh 不被 refresh=None 倒退覆盖
    confirmed = copy.deepcopy(yesterday)
    confirmed["as_of"] = "2026-11-23"
    confirmed["version"] = "kx-20261123-001"
    confirmed["generated_at"] = "2026-11-23T20:50:00+08:00"
    confirmed["refresh"] = {
        "date": "2026-11-23", "result": "updated", "pool_version": "kx-20261123-001",
        "as_of": "2026-11-23", "deepseek_status": "ok", "finished_at": "2026-11-23T20:50:00+08:00",
        "message": "ok", "notice": None, "elapsed_seconds": 10.0,
    }
    clock.advance(20)
    kx_services.use(services_for(FakeKx(confirmed), clock))
    run(build_board(kx_services.current(), db_path=db_path, force_refresh=True))

    unconfirmed = copy.deepcopy(confirmed)
    unconfirmed["refresh"] = None
    clock.advance(20)
    kx_services.use(services_for(FakeKx(unconfirmed), clock))
    run(build_board(kx_services.current(), db_path=db_path, force_refresh=True))
    with db.read(db_path) as conn:
        assert db.get_kx_board_cache(conn)["raw"]["refresh"] is not None

    # 3. 空缓存下手动刷新连续触发受 10 秒防抖保护，直接复用上次错误不再回源
    reset_board_state()
    empty_db = _db(tmp_path / "empty_cooldown")
    down_fake = FakeKx(load_overview(), down=True)
    kx_services.use(services_for(down_fake, clock))
    with pytest.raises(BoardUnavailable):
        run(build_board(kx_services.current(), db_path=empty_db, force_refresh=True))
    assert len(down_fake.calls) == 1
    clock.advance(3)
    with pytest.raises(BoardUnavailable):
        run(build_board(kx_services.current(), db_path=empty_db, force_refresh=True))
    assert len(down_fake.calls) == 1


def test_upstream_errors_redact_read_key_and_urls(tmp_path):
    db_path = _db(tmp_path)
    clock = Clock(at("2026-11-22", "20:05", 0))
    fake = FakeKx(
        load_overview(),
        unavailable=f"读取 https://linotsai.top/kx/read/{READ_KEY}?format=json 失败，请稍后重试",
    )
    kx_services.use(services_for(fake, clock))

    with pytest.raises(BoardUnavailable) as exc_info:
        run(build_board(kx_services.current(), db_path=db_path))
    assert READ_KEY not in exc_info.value.message
    assert "linotsai.top" not in exc_info.value.message
    assert "/kx/read/" not in exc_info.value.message


def test_http_endpoint_requires_bearer_and_supports_refresh_param(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "settings", dataclasses.replace(deps.settings, api_token=TOKEN))
    db_path = _db(tmp_path)
    clock = Clock(at("2026-11-22", "20:05", 0))
    fake = FakeKx(load_overview())
    kx_services.use(services_for(fake, clock))

    client = TestClient(_app(db_path))
    assert client.get("/api/v4/kx/board").status_code == 401
    resp = client.get("/api/v4/kx/board", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["schemaVersion"] == 5 and body["capacity"]["used"] == 19

    # 未知 schema 返回 503 且不泄露内部实现
    reset_board_state()
    bad_db = _db(tmp_path / "bad")
    bad_doc = load_overview()
    bad_doc["schema_version"] = 4
    kx_services.use(services_for(FakeKx(bad_doc), clock))
    bad_client = TestClient(_app(bad_db))
    r503 = bad_client.get("/api/v4/kx/board?refresh=1", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r503.status_code == 503
    assert r503.json()["detail"]["reason"] == "kx_unsupported_schema"


def test_cross_end_v4_fixtures_match_between_backend_and_app():
    names = (
        "kx_board_v4.json",
        "kx_board_v4_partial.json",
        "kx_board_v4_stale.json",
        "kx_board_v4_elim.json",
        "kx_board_unavailable.json",
    )
    for name in names:
        backend_file = FIXTURE_DIR / name
        app_file = APP_FIXTURE_DIR / name
        assert backend_file.exists(), f"缺少后端夹具 {name}"
        assert app_file.exists(), f"缺少客户端夹具 {name}"
        assert json.loads(backend_file.read_text(encoding="utf-8")) == json.loads(app_file.read_text(encoding="utf-8"))
