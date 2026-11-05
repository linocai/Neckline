"""KX 看板 v2：真实总览投影、校验、缓存、盘中窗口，以及 Bearer 接口。"""
from __future__ import annotations

import copy
import dataclasses
import json
import logging
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.api import deps
from neckline.api.deps import require_token
from neckline.kx import services as kx_services
from neckline.kx import settings
from neckline.kx.api import create_router
from neckline.kx.board import BoardUnavailable, build_board
from neckline.morning import db
from neckline.morning.api import create_router as create_morning_router
from tests.kx_support import (
    APP_FIXTURE_DIR, FIXTURE_DIR, READ_KEY, Clock, FakeKx, at, load_overview, run, services_for,
)

TOKEN = "a" * 16


@pytest.fixture(autouse=True)
def _configured_read_key(monkeypatch):
    """看板读取要有访问码。个别用例再删掉它，验证没配置时的失败。"""
    monkeypatch.setenv("KX_READ_KEY", READ_KEY)


def _use(fake: FakeKx, clock: Clock):
    kx_services.use(services_for(fake, clock))


def _board(fake: FakeKx, clock: Clock) -> dict:
    _use(fake, clock)
    return run(build_board(kx_services.current()))


def _overview(**changes) -> dict:
    body = load_overview()
    body.update(changes)
    return body


def test_the_real_overview_projects_field_by_field():
    clock = Clock(at("2026-11-04", "18:30", 10))
    board = _board(FakeKx(load_overview()), clock)
    leader = board["rooms"]["leader"][0]
    stock = leader["leaders"][0]
    assert leader["id"] == "g-600261-20261103"
    assert leader["theme"].startswith("羊字辈")
    assert leader["roomSince"] == "2026-11-03"
    assert leader["brokeOn"] is None and leader["observedTradingDays"] is None
    assert leader["judgment"]["verdict"] == "晋升"
    assert leader["judgment"]["reason"].startswith("9:35封板")
    assert [item["code"] for item in leader["leaders"]] == ["600261", "002086"]
    assert leader["leaders"][1]["name"] == "东方海洋"
    assert stock["code"] == "600261" and stock["name"] == "阳光照明" and stock["role"] == "龙头"
    assert stock["reason"] is None and stock["divergent"] is False
    assert stock["quote"]["price"] == 4.46
    assert stock["quote"]["changePercent"] == -9.899
    assert stock["quote"]["turnoverAmount"] == 669120700.0
    assert stock["quote"]["amplitudePercent"] == 9.899
    assert stock["quote"]["turnoverPercent"] == 10.89
    assert stock["quote"]["sourceTime"] == "2026-11-04T15:00:01+08:00"
    assert stock["quote"]["sessionPhase"] is None
    assert stock["limitUp"]["inLimitUp"] is False and stock["limitUp"]["inLimitDown"] is True
    assert stock["limitUp"]["consecutiveBoards"] is None and stock["limitUp"]["sealAmount"] is None
    assert stock["orderBook"]["status"] == "partial"
    assert stock["orderBook"]["bid1Volume"] == 0.0 and stock["orderBook"]["bid1Price"] is None
    assert stock["orderBook"]["sealed"] is None
    assert stock["orderBook"]["warnings"] == ["买一价无效。"]
    assert stock["orderBook"]["sealChange5m"] == {
        "amount": None, "baselineAmount": None, "baselineAgeSeconds": None}
    assert "history" not in stock
    follower = leader["followers"][0]
    assert follower["code"] == "002696" and follower["role"] == "小弟" and follower["orderBook"] is None
    assert follower["reason"].startswith("18天10板")
    assert all(item["orderBook"] is None for item in leader["followers"])
    waiting = board["rooms"]["waiting"][0]
    assert waiting["leaders"][0]["code"] == "000995"
    assert waiting["leaders"][0]["orderBook"]["status"] == "ok"
    assert waiting["leaders"][0]["orderBook"]["sealed"] is False
    assert waiting["leaders"][0]["orderBook"]["bid1Amount"] == 399184
    assert waiting["followers"] == []
    eliminated = board["rooms"]["eliminated"][0]
    assert eliminated["brokeOn"] == "2026-10-26"
    assert eliminated["observedTradingDays"] == 6
    assert eliminated["observedText"] == "已观察 6 个交易日（满 10 个交易日踢出）"
    assert eliminated["flags"] == [
        {"kind": "断板", "code": None, "text": "断板"},
        {"kind": "二波迹象", "code": None, "text": "二波迹象"},
    ]
    assert eliminated["leaders"][0]["orderBook"] is None
    divergent = board["rooms"]["eliminated"][1]["flags"][1]
    assert divergent == {"kind": "背离", "code": "000678", "text": "背离:000678"}
    assert [item["code"] for item in board["rooms"]["eliminated"][1]["followers"]] == [
        "002708", "000678", "603028", "603528"]
    sentiment = board["sentiment"]
    assert sentiment["tradingDay"] == "2026-11-04" and sentiment["basis"] == "今日收盘"
    assert sentiment["boardPhrases"] == "收盘"
    assert sentiment["limitUpCount"] == 96 and sentiment["brokenCount"] == 30
    assert sentiment["brokenRate"] == 0.2381 and sentiment["limitDownCount"] == 10
    assert sentiment["highestBoard"]["boards"] == 3
    assert sentiment["highestBoard"]["names"][0] == "紫竹高科"
    assert sentiment["highestBoard"]["codes"] == ["002058", "002578", "002827", "600892", "601599", "605577"]
    assert sentiment["yesterdayConsecutive"] == 15 and sentiment["yesterdayContinued"] == 6
    assert sentiment["fetchedAt"]["limitUp"] == "2026-11-04T18:30:10.249358+08:00"
    assert "excludedNonMain" not in sentiment
    structure = board["structure"]
    assert structure["version"] == "kx-20261104-003" and structure["asOf"] == "2026-11-03"
    assert structure["generatedBy"] == "deepseek" and structure["deepseekStatus"] == "ok"
    assert structure["deepseekNotice"] is None and structure["membershipLocked"] is False
    assert structure["refresh"]["result"] == "updated" and structure["refresh"]["notice"] is None
    assert structure["refresh"]["date"] == "2026-11-04"
    assert "programVersion" not in structure["refresh"]
    assert board["status"] == "partial" and board["errors"] == [] and board["warnings"] == []
    assert board["factWarnings"] == []
    assert board["rooms"]["leader"][0]["followers"][2]["limitUp"]["firstSealTime"] == "09:30:12"
    assert board["rooms"]["leader"][0]["followers"][2]["limitUp"]["lastSealTime"] == "10:07:39"
    assert board["rooms"]["leader"][0]["followers"][3]["limitUp"]["firstSealTime"] == "09:25:00"
    assert board["rooms"]["waiting"][0]["leaders"][0]["limitUp"]["firstSealTime"] == "09:30:06"
    assert board["gaps"] == []
    assert board["source"]["checkedAt"] == "2026-11-04T18:30:10.318216+08:00"
    assert board["source"]["stale"] is False and board["source"]["reason"] is None
    assert board["source"]["lastReadAt"] == "2026-11-04T18:30:10+08:00"
    assert board["generatedAt"] == "2026-11-04T18:30:10+08:00"
    assert board["session"] == {"inSession": False, "refreshSeconds": None}
    assert "rules" not in board and "history" not in board and "note" not in board
    raw = load_overview()
    for room in ("leader", "waiting", "eliminated"):
        assert [item["id"] for item in board["rooms"][room]] == [item["id"] for item in raw["rooms"][room]]


def test_seal_times_keep_a_clock_or_a_full_timestamp_and_other_times_do_not():
    body = load_overview()
    stock = body["rooms"]["leader"][0]["leaders"][0]
    stock["limit_up"]["first_seal_time"] = "09:35"
    stock["limit_up"]["last_seal_time"] = "2026-11-04T10:07:39+08:00"
    stock["quote"]["source_time"] = "09:30:12"
    board = _board(FakeKx(body), Clock(at("2026-11-04", "18:30")))
    got = board["rooms"]["leader"][0]["leaders"][0]
    assert got["limitUp"]["firstSealTime"] == "09:35"
    assert got["limitUp"]["lastSealTime"] == "2026-11-04T10:07:39+08:00"
    assert got["quote"]["sourceTime"] is None
    assert board["gaps"] == ["1 处时间不合格，已留空"]

    from neckline.kx.board import reset_board_state
    reset_board_state()
    stock["limit_up"]["first_seal_time"] = "25:99:00"
    stock["limit_up"]["last_seal_time"] = "  09:30:12  "
    board = _board(FakeKx(body), Clock(at("2026-11-04", "18:30")))
    got = board["rooms"]["leader"][0]["leaders"][0]
    assert got["limitUp"]["firstSealTime"] is None
    assert got["limitUp"]["lastSealTime"] == "09:30:12"


def test_flags_keep_unknown_kinds_and_drop_a_bad_code():
    body = load_overview()
    body["rooms"]["waiting"][0]["flags"] = ["反包", "并列龙头断板:002001", "并列龙头断板:12", "二波候选", "别的标记"]
    board = _board(FakeKx(body), Clock(at("2026-11-04", "18:30")))
    assert board["rooms"]["waiting"][0]["flags"] == [
        {"kind": "反包", "code": None, "text": "反包"},
        {"kind": "并列龙头断板", "code": "002001", "text": "并列龙头断板:002001"},
        {"kind": "并列龙头断板", "code": None, "text": "并列龙头断板:12"},
        {"kind": "二波候选", "code": None, "text": "二波候选"},
        {"kind": "别的标记", "code": None, "text": "别的标记"},
    ]


def test_fact_warnings_project_group_text_and_plain_strings():
    body = load_overview()
    body["fact_warnings"] = [
        {"group_id": "g-600261-20261103", "text": "连板数对不上"},
        {"group_id": 3, "text": "没有组号"},
        "整段都是字符串",
        {"group_id": "g-1", "text": 5},
        {"text": "  只有文字  "},
    ]
    board = _board(FakeKx(body), Clock(at("2026-11-04", "18:30")))
    assert board["factWarnings"] == [
        {"groupId": "g-600261-20261103", "text": "连板数对不上"},
        {"groupId": None, "text": "没有组号"},
        {"groupId": None, "text": "整段都是字符串"},
        {"groupId": None, "text": "只有文字"},
    ]


def test_warning_objects_become_one_line_and_are_not_dropped():
    body = load_overview()
    body["warnings"] = [{"message": "  情绪条少了跌停数  "}, {"code": "x"}]
    body["errors"] = ["取数失败"]
    board = _board(FakeKx(body), Clock(at("2026-11-04", "18:30")))
    assert board["warnings"][0] == "情绪条少了跌停数"
    assert board["warnings"][1].startswith("{") and "code" in board["warnings"][1]
    assert board["errors"] == ["取数失败"]


@pytest.mark.parametrize("value,label", [
    (3, "3"), (5, "5"), ("4", "'4'"), (4.0, "4.0"), (True, "True"),
])
def test_a_schema_version_other_than_integer_four_rejects_the_whole_document(value, label):
    body = load_overview()
    body["schema_version"] = value
    with pytest.raises(BoardUnavailable) as caught:
        _board(FakeKx(body), Clock(at("2026-11-04", "18:30")))
    assert caught.value.reason == "kx_unavailable"
    assert f"schema_version={label}" in caught.value.message
    assert "格式变了" in caught.value.message


def test_a_missing_schema_version_rejects_the_whole_document():
    body = load_overview()
    del body["schema_version"]
    with pytest.raises(BoardUnavailable) as caught:
        _board(FakeKx(body), Clock(at("2026-11-04", "18:30")))
    assert "schema_version=缺失" in caught.value.message


@pytest.mark.parametrize("mutate", [
    lambda body: body.pop("rooms"),
    lambda body: body["rooms"].pop("waiting"),
    lambda body: body["rooms"].__setitem__("leader", {}),
    lambda body: body.__setitem__("rooms", []),
])
def test_bad_rooms_reject_the_whole_document(mutate):
    body = load_overview()
    mutate(body)
    with pytest.raises(BoardUnavailable) as caught:
        _board(FakeKx(body), Clock(at("2026-11-04", "18:30")))
    assert "房间数据不合格" in caught.value.message


def test_a_bad_group_and_a_bad_follower_are_dropped_with_gap_lines():
    body = load_overview()
    body["rooms"]["eliminated"][0]["id"] = "  "
    body["rooms"]["leader"][0]["followers"][0]["stock_code"] = "123456"
    body["rooms"]["waiting"][0]["leaders"] += [
        {"stock_code": "600000", "name": "多余一"}, {"stock_code": "600001", "name": "多余二"}]
    board = _board(FakeKx(body), Clock(at("2026-11-04", "18:30")))
    assert board["gaps"][:2] == ["2 组数据不合格，没有显示", "1 只股票数据不合格，没有显示"]
    assert all(item["id"] != "g-002724-20261026" for item in board["rooms"]["eliminated"])
    assert board["rooms"]["waiting"] == []
    codes = [item["code"] for item in board["rooms"]["leader"][0]["followers"]]
    assert "002696" not in codes and "002172" in codes


def test_bad_field_values_become_null_and_null_stays_null():
    body = load_overview()
    quote = body["rooms"]["leader"][0]["leaders"][0]["quote"]
    quote["price"] = True
    quote["open"] = None
    quote["source_time"] = "昨天"
    quote["high"] = "坏了"
    body["rooms"]["leader"][0]["note"] = "字" * 4001
    body["rooms"]["leader"][0]["leaders"][0]["divergent"] = "yes"
    board = _board(FakeKx(body), Clock(at("2026-11-04", "18:30")))
    stock = board["rooms"]["leader"][0]["leaders"][0]
    assert stock["quote"]["price"] is None and stock["quote"]["open"] is None
    assert stock["quote"]["sourceTime"] is None and stock["quote"]["high"] is None
    assert stock["quote"]["low"] == 4.46
    assert stock["divergent"] is False
    assert board["rooms"]["leader"][0]["note"] is None
    assert "1 处文字超过 4000 字，没有显示" in board["gaps"]
    assert any("数字不合格" in line for line in board["gaps"])
    assert any("时间不合格" in line for line in board["gaps"])
    assert stock["limitUp"]["consecutiveBoards"] is None


def test_sentiment_bad_fields_are_counted_in_gaps():
    body = load_overview()
    body["sentiment"]["limit_up_count"] = "很多"
    body["sentiment"]["fetched_at"]["limit_up"] = "昨天"
    board = _board(FakeKx(body), Clock(at("2026-11-04", "18:30")))
    assert board["sentiment"]["limitUpCount"] is None
    assert board["sentiment"]["fetchedAt"]["limitUp"] is None
    assert board["gaps"] == ["1 处数字不合格，已留空", "1 处时间不合格，已留空"]


def test_pending_is_retried_and_then_gives_up(monkeypatch):
    monkeypatch.setenv("KX_READ_KEY", READ_KEY)
    clock = Clock(at("2026-11-04", "18:30"))
    fake = FakeKx(load_overview(), pending=2, retry_after=3)
    board = _board(fake, clock)
    assert board["source"]["stale"] is False and len(fake.calls) == 3 and clock.sleeps == [3.0, 3.0]

    from neckline.kx.board import reset_board_state
    reset_board_state()
    clock = Clock(at("2026-11-04", "18:30"))
    fake = FakeKx(load_overview(), pending=99, retry_after=9)
    with pytest.raises(BoardUnavailable) as caught:
        _board(fake, clock)
    assert len(fake.calls) == 4 and clock.sleeps == [5.0, 5.0, 5.0]
    assert "还在取数" in caught.value.message
    calls = len(fake.calls)
    with pytest.raises(BoardUnavailable):
        _board(fake, clock)
    assert len(fake.calls) == calls  # 失败后 15 秒内不再读


def test_retry_delay_defaults_when_the_hint_is_missing_or_not_a_number():
    from neckline.kx.source import retry_delay
    assert retry_delay({"retry_after_seconds": 0}) == 3
    assert retry_delay({"retry_after_seconds": True}) == 3
    assert retry_delay({}) == 3
    assert retry_delay({"retry_after_seconds": 2}) == 2
    assert retry_delay({"retry_after_seconds": 0.2}) == 1


def test_success_is_cached_for_sixty_seconds_and_failure_for_fifteen(monkeypatch):
    monkeypatch.setenv("KX_READ_KEY", READ_KEY)
    clock = Clock(at("2026-11-04", "18:30"))
    fake = FakeKx(load_overview())
    first = _board(fake, clock)
    assert _board(fake, clock)["generatedAt"] == first["generatedAt"]
    assert len(fake.calls) == 1
    clock.advance(59)
    assert len(fake.calls) == 1 and _board(fake, clock)["source"]["stale"] is False
    assert len(fake.calls) == 1
    clock.advance(1)
    refreshed = _board(fake, clock)
    assert len(fake.calls) == 2

    fake.down = True
    clock.advance(60)
    stale = _board(fake, clock)
    assert stale["source"]["stale"] is True and "连不上" in stale["source"]["reason"]
    assert stale["source"]["lastReadAt"] == refreshed["source"]["lastReadAt"]
    assert stale["rooms"]["leader"][0]["leaders"][0]["code"] == "600261"
    calls = len(fake.calls)
    clock.advance(14)
    again = _board(fake, clock)
    assert len(fake.calls) == calls and again["source"]["stale"] is True
    clock.advance(1)
    _board(fake, clock)
    assert len(fake.calls) == calls + 1


def test_unreachable_without_a_previous_board_is_503(monkeypatch):
    monkeypatch.setenv("KX_READ_KEY", READ_KEY)
    fake = FakeKx(load_overview(), down=True)
    with pytest.raises(BoardUnavailable) as caught:
        _board(fake, Clock(at("2026-11-04", "18:30")))
    assert caught.value.reason == "kx_unavailable"
    assert caught.value.message == "KX 看板还没读到过：KX 暂时连不上。"


def test_unavailable_uses_the_kx_message_clipped_to_200_characters(monkeypatch):
    monkeypatch.setenv("KX_READ_KEY", READ_KEY)
    fake = FakeKx(load_overview(), unavailable="  维护中  ")
    with pytest.raises(BoardUnavailable) as caught:
        _board(fake, Clock(at("2026-11-04", "18:30")))
    assert caught.value.message.endswith("维护中")
    from neckline.kx.board import reset_board_state
    reset_board_state()
    fake = FakeKx(load_overview(), unavailable="字" * 250)
    with pytest.raises(BoardUnavailable) as caught:
        _board(fake, Clock(at("2026-11-04", "18:30")))
    reason = caught.value.message.split("：", 1)[1]
    assert len(reason) == 200 and READ_KEY not in caught.value.message


def test_a_rejected_read_key_and_other_http_errors_fail_without_the_url(monkeypatch):
    monkeypatch.setenv("KX_READ_KEY", READ_KEY)
    fake = FakeKx(load_overview(), status=404)
    with pytest.raises(BoardUnavailable) as caught:
        _board(fake, Clock(at("2026-11-04", "18:30")))
    assert "不认这枚只读访问码" in caught.value.message and READ_KEY not in caught.value.message
    from neckline.kx.board import reset_board_state
    reset_board_state()
    fake = FakeKx(load_overview(), status=500)
    with pytest.raises(BoardUnavailable) as caught:
        _board(fake, Clock(at("2026-11-04", "18:30")))
    assert "HTTP 500" in caught.value.message and READ_KEY not in caught.value.message


def test_a_missing_read_key_does_not_call_kx(monkeypatch):
    monkeypatch.delenv("KX_READ_KEY", raising=False)
    fake = FakeKx(load_overview())
    with pytest.raises(BoardUnavailable) as caught:
        _board(fake, Clock(at("2026-11-04", "18:30")))
    assert "没有配置" in caught.value.message and fake.calls == []


def test_the_read_key_never_appears_in_the_log(monkeypatch, caplog):
    monkeypatch.setenv("KX_READ_KEY", READ_KEY)
    caplog.set_level(logging.DEBUG)
    fake = FakeKx(load_overview(), status=404)
    with pytest.raises(BoardUnavailable):
        _board(fake, Clock(at("2026-11-04", "18:30")))
    assert READ_KEY not in caplog.text


def test_session_window_follows_the_trading_clock():
    clock = Clock(at("2026-11-04", "09:14"))
    fake = FakeKx(load_overview())
    _use(fake, clock)
    def session():
        return run(build_board(kx_services.current()))["session"]

    assert session() == {"inSession": False, "refreshSeconds": None}
    clock.set_now(at("2026-11-04", "09:15"))
    assert session() == {"inSession": True, "refreshSeconds": 60}
    clock.set_now(at("2026-11-04", "15:04"))
    assert session() == {"inSession": True, "refreshSeconds": 60}
    clock.set_now(at("2026-11-04", "15:05"))
    assert session() == {"inSession": False, "refreshSeconds": None}
    clock.set_now(at("2026-11-07", "10:30"))  # 周六
    assert session() == {"inSession": False, "refreshSeconds": None}
    assert len(fake.calls) == 1


def test_a_calendar_gap_still_serves_the_board():
    clock = Clock(at("2027-03-02", "10:30"))
    board = _board(FakeKx(load_overview()), clock)
    assert board["session"] == {"inSession": False, "refreshSeconds": None}
    assert board["gaps"] == ["交易日历没有覆盖今天，盘中不自动刷新"]
    assert board["rooms"]["leader"]


def test_base_url_rejects_a_url_with_a_path(monkeypatch):
    monkeypatch.setenv("KX_BASE_URL", "https://kx.example.test/extra")
    assert settings.base_url() == "https://linotsai.top"
    monkeypatch.setenv("KX_BASE_URL", "https://kx.example.test/")
    assert settings.base_url() == "https://kx.example.test"


def _client(tmp_path, monkeypatch):
    path = tmp_path / "v4.db"
    db.init_schema(path)
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    monkeypatch.setenv("KX_READ_KEY", READ_KEY)
    monkeypatch.setenv("KX_BASE_URL", "https://kx.example.test")
    monkeypatch.setattr(deps, "settings", dataclasses.replace(deps.settings, api_token=TOKEN))

    def db_path():
        return path

    app = FastAPI()
    app.include_router(create_morning_router(require_token_dependency=require_token))
    app.include_router(create_router(require_token_dependency=require_token, db_path_provider=db_path))
    return TestClient(app), path


def test_the_board_route_requires_the_bearer_and_picks_is_gone(tmp_path, monkeypatch):
    client, _path = _client(tmp_path, monkeypatch)
    _use(FakeKx(load_overview()), Clock(at("2026-11-04", "18:30", 10)))
    assert client.get("/api/v4/kx/board").status_code == 401
    denied = client.get("/api/v4/kx/board", headers={"Authorization": "Bearer wrong-token"})
    assert denied.status_code == 401
    ok = client.get("/api/v4/kx/board", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200 and ok.json()["rooms"]["leader"][0]["leaders"][0]["code"] == "600261"
    assert READ_KEY not in ok.text
    assert client.post("/api/v4/kx/picks", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404
    health = client.get("/api/v4/health").json()
    assert health["kxBoard"] is True and "kxPool" not in health and "kxPicks" not in health
    monkeypatch.delenv("KX_READ_KEY")
    assert client.get("/api/v4/health").json()["kxBoard"] is False


def test_never_read_returns_503_from_the_route(tmp_path, monkeypatch):
    client, _path = _client(tmp_path, monkeypatch)
    _use(FakeKx(load_overview(), down=True), Clock(at("2026-11-04", "18:30")))
    response = client.get("/api/v4/kx/board", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 503
    assert response.json() == {"detail": {"reason": "kx_unavailable", "message": "KX 看板还没读到过：KX 暂时连不上。"}}
    assert READ_KEY not in response.text


def _auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize("message", [
    f"详见 https://kx.example.test/kx/read/{READ_KEY}?format=json",
    f"访问码 {READ_KEY} 无效",
    f"路径 /kx/read/{READ_KEY}/stock/600000 失败",
])
def test_upstream_text_cannot_carry_the_read_key(tmp_path, monkeypatch, caplog, message):
    caplog.set_level(logging.DEBUG)
    client, _path = _client(tmp_path, monkeypatch)
    from neckline.kx.board import reset_board_state

    reset_board_state()
    _use(FakeKx(load_overview(), unavailable=message), Clock(at("2026-11-04", "18:30")))
    failed = client.get("/api/v4/kx/board", headers=_auth())
    assert failed.status_code == 503
    assert READ_KEY not in failed.text and "/kx/read/" not in failed.text and "https://" not in failed.text

    reset_board_state()
    clock = Clock(at("2026-11-04", "18:30", 10))
    fake = FakeKx(load_overview())
    _use(fake, clock)
    assert client.get("/api/v4/kx/board", headers=_auth()).status_code == 200
    fake.unavailable = message
    clock.advance(60)
    stale = client.get("/api/v4/kx/board", headers=_auth())
    assert stale.status_code == 200 and stale.json()["source"]["stale"] is True
    assert READ_KEY not in stale.text and "/kx/read/" not in stale.text and "https://" not in stale.text
    assert READ_KEY not in caplog.text and "/kx/read/" not in caplog.text


def test_an_oversized_integer_becomes_null_and_the_route_returns_200(tmp_path, monkeypatch):
    body = load_overview()
    body["rooms"]["leader"][0]["leaders"][0]["quote"]["price"] = 10**400
    client, _path = _client(tmp_path, monkeypatch)
    _use(FakeKx(body), Clock(at("2026-11-04", "18:30", 10)))
    response = client.get("/api/v4/kx/board", headers=_auth())
    assert response.status_code == 200
    board = response.json()
    assert board["rooms"]["leader"][0]["leaders"][0]["quote"]["price"] is None
    assert board["gaps"] == ["1 处数字不合格，已留空"]
    assert board["rooms"]["leader"][0]["leaders"][0]["code"] == "600261"


def test_a_projection_error_is_cached_as_a_failed_read(tmp_path, monkeypatch):
    from neckline.kx import board as board_mod
    from neckline.kx.board import reset_board_state

    real = board_mod.project_board

    def boom(body, *, now, last_read_at, stale, reason):
        if isinstance(body, dict) and body.get("boom"):
            raise RuntimeError("boom")
        return real(body, now=now, last_read_at=last_read_at, stale=stale, reason=reason)

    monkeypatch.setattr(board_mod, "project_board", boom)
    client, _path = _client(tmp_path, monkeypatch)
    broken = load_overview()
    broken["boom"] = True
    fake = FakeKx(broken)
    clock = Clock(at("2026-11-04", "18:30"))
    _use(fake, clock)
    first = client.get("/api/v4/kx/board", headers=_auth())
    assert first.status_code == 503
    assert first.json()["detail"]["message"] == "KX 看板还没读到过：KX 看板数据无法处理，没有采用。"
    assert len(fake.calls) == 1
    clock.advance(14)
    again = client.get("/api/v4/kx/board", headers=_auth())
    assert again.status_code == 503 and len(fake.calls) == 1

    reset_board_state()
    clock = Clock(at("2026-11-04", "18:30", 10))
    fake = FakeKx(load_overview())
    _use(fake, clock)
    assert client.get("/api/v4/kx/board", headers=_auth()).status_code == 200
    fake.body["boom"] = True
    clock.advance(60)
    stale = client.get("/api/v4/kx/board", headers=_auth())
    assert stale.status_code == 200
    payload = stale.json()
    assert payload["source"]["stale"] is True
    assert payload["source"]["reason"] == "KX 看板数据无法处理，没有采用。"
    assert payload["rooms"]["leader"][0]["leaders"][0]["code"] == "600261"
    calls = len(fake.calls)
    clock.advance(14)
    followed = client.get("/api/v4/kx/board", headers=_auth())
    assert followed.status_code == 200 and followed.json()["source"]["stale"] is True
    assert len(fake.calls) == calls


def _pretty(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _canon(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _save(name: str, payload: dict) -> None:
    text = _pretty(payload)
    if os.environ.get("NK_WRITE_FIXTURES") == "1":
        for folder in (FIXTURE_DIR, APP_FIXTURE_DIR):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(text, encoding="utf-8")
    for folder in (FIXTURE_DIR, APP_FIXTURE_DIR):
        path = folder / name
        if path.is_file():
            assert _canon(json.loads(path.read_text(encoding="utf-8"))) == _canon(payload), name


def _stamp(hm: str) -> str:
    return f"2026-11-04T{hm}:00+08:00"


def _move_reads_to(body: dict, hm: str) -> None:
    """把这份总览里的取数时刻改到当天的同一时刻，并标成盘中。"""
    stamp = _stamp(hm)
    body["checked_at"] = stamp
    body["membership_locked"] = True
    sentiment = body["sentiment"]
    sentiment["basis"] = "交易时段"
    sentiment["board_phrases"] = "盘中"
    fetched = sentiment.get("fetched_at")
    if isinstance(fetched, dict):
        for key in fetched:
            fetched[key] = stamp
    for groups in body["rooms"].values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for key in ("leaders", "followers"):
                stocks = group.get(key)
                if not isinstance(stocks, list):
                    continue
                for stock in stocks:
                    if not isinstance(stock, dict):
                        continue
                    for section, fields in (
                        ("quote", ("source_time",)),
                        ("limit_up", ("pool_fetched_at",)),
                        ("order_book", ("source_time", "fetched_at")),
                    ):
                        raw = stock.get(section)
                        if isinstance(raw, dict):
                            for field in fields:
                                raw[field] = stamp


def _fetch(tmp_path, monkeypatch, body: dict, clock: Clock, **fake_kwargs) -> dict:
    from neckline.kx.board import reset_board_state
    reset_board_state()
    client, _path = _client(tmp_path, monkeypatch)
    _use(FakeKx(body, **fake_kwargs), clock)
    response = client.get("/api/v4/kx/board", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200, response.text
    assert READ_KEY not in response.text
    return response.json()


def test_client_fixtures_come_from_the_real_route(tmp_path, monkeypatch):
    sample = load_overview()
    closed = _fetch(tmp_path, monkeypatch, sample, Clock(at("2026-11-04", "18:30", 10)))
    assert closed["source"]["stale"] is False and closed["session"]["inSession"] is False
    _save("kx_board_v3.json", closed)

    partial_body = copy.deepcopy(sample)
    partial_body["deepseek_status"] = "partial"
    partial_body["deepseek_notice"] = "DeepSeek 部分未完成：分组和小弟未更新，沿用上一版小弟"
    partial_body["refresh"] = {**partial_body["refresh"], "notice": "今日未更新"}
    partial_body["fact_warnings"] = [{
        "group_id": partial_body["rooms"]["leader"][0]["id"],
        "text": "依据里写“连续4板”，麦蕊涨停池当天为 3 板",
    }]
    partial = _fetch(tmp_path, monkeypatch, partial_body, Clock(at("2026-11-04", "18:30", 10)))
    assert partial["structure"]["deepseekStatus"] == "partial"
    assert partial["structure"]["deepseekNotice"].startswith("DeepSeek 部分未完成")
    assert partial["structure"]["refresh"]["notice"] == "今日未更新"
    assert partial["factWarnings"] == [{
        "groupId": "g-600261-20261103", "text": "依据里写“连续4板”，麦蕊涨停池当天为 3 板",
    }]
    _save("kx_board_v3_partial.json", partial)

    sealed_body = copy.deepcopy(sample)
    _move_reads_to(sealed_body, "10:30")
    leader = sealed_body["rooms"]["leader"][0]["leaders"][0]
    # 开盘一字封住的龙头：报价、涨停池和五档要互相对得上（真实样本里它当天是跌停）
    leader["quote"].update({"price": 5.45, "change_percent": 10.101, "open": 5.45, "high": 5.45, "low": 5.45,
                            "amplitude_percent": 0.0, "turnover_amount": 41_250_000.0, "warnings": []})
    leader["limit_up"].update({"in_limit_up": True, "in_broken": False, "in_limit_down": False,
                               "consecutive_boards": 5, "board_type_text": "开盘封死，至今没开板",
                               "first_seal_time": "09:30:00", "last_seal_time": "09:30:00",
                               "break_count": 0, "seal_amount": 199_000_000})
    leader["order_book"].update({"status": "ok", "bid1_price": 5.45, "bid1_volume": 365_138.0,
                                 "bid1_amount": 199_000_000, "asks_clear": True, "warnings": [],
                                 "missing_fields": []})
    leader["order_book"]["sealed"] = True
    leader["order_book"]["seal_amount"] = 199_000_000
    leader["order_book"]["seal_change_5m"] = {
        "amount": -12_000_000, "baseline_amount": 211_000_000, "baseline_age_seconds": 300}
    sealed = _fetch(tmp_path, monkeypatch, sealed_body, Clock(at("2026-11-04", "10:30")))
    projected = sealed["rooms"]["leader"][0]["leaders"][0]
    assert sealed["session"] == {"inSession": True, "refreshSeconds": 60}
    assert sealed["source"]["checkedAt"] == _stamp("10:30")
    assert sealed["structure"]["membershipLocked"] is True
    assert sealed["sentiment"]["basis"] == "交易时段" and sealed["sentiment"]["boardPhrases"] == "盘中"
    assert sealed["sentiment"]["fetchedAt"]["limitUp"] == _stamp("10:30")
    assert projected["quote"]["sourceTime"] == _stamp("10:30")
    assert projected["limitUp"]["poolFetchedAt"] == _stamp("10:30")
    assert projected["orderBook"]["sourceTime"] == _stamp("10:30")
    assert projected["limitUp"]["inLimitUp"] is True and projected["limitUp"]["consecutiveBoards"] == 5
    assert projected["limitUp"]["boardTypeText"] == "开盘封死，至今没开板"
    assert projected["orderBook"]["sealed"] is True
    assert projected["orderBook"]["sealAmount"] == 199_000_000
    assert projected["orderBook"]["sealChange5m"]["amount"] == -12_000_000
    _save("kx_board_v3_sealed.json", sealed)

    auction_body = copy.deepcopy(sample)
    _move_reads_to(auction_body, "09:18")
    leader = auction_body["rooms"]["leader"][0]["leaders"][0]
    leader["quote"]["session_phase"] = "call_auction"
    leader["order_book"]["sealed"] = None
    leader["order_book"]["warnings"] = ["集合竞价阶段无法判断封单"]
    auction = _fetch(tmp_path, monkeypatch, auction_body, Clock(at("2026-11-04", "09:18")))
    assert auction["session"]["inSession"] is True
    assert auction["source"]["checkedAt"] == _stamp("09:18")
    assert auction["structure"]["membershipLocked"] is True
    assert auction["sentiment"]["boardPhrases"] == "盘中" and auction["sentiment"]["basis"] == "交易时段"
    assert auction["sentiment"]["fetchedAt"]["broken"] == _stamp("09:18")
    head = auction["rooms"]["leader"][0]["leaders"][0]
    assert head["quote"]["sourceTime"] == _stamp("09:18")
    assert head["limitUp"]["poolFetchedAt"] == _stamp("09:18")
    assert head["orderBook"]["sourceTime"] == _stamp("09:18")
    assert head["quote"]["sessionPhase"] == "call_auction"
    assert head["orderBook"]["sealed"] is None
    assert head["orderBook"]["warnings"] == ["集合竞价阶段无法判断封单"]
    _save("kx_board_v3_auction.json", auction)

    empty_waiting = copy.deepcopy(sample)
    empty_waiting["rooms"]["waiting"] = []
    from neckline.kx.board import reset_board_state
    reset_board_state()
    client, _path = _client(tmp_path, monkeypatch)
    clock = Clock(at("2026-11-04", "18:30", 10))
    fake = FakeKx(empty_waiting)
    _use(fake, clock)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/api/v4/kx/board", headers=auth).status_code == 200
    fake.down = True
    clock.advance(60)
    stale_response = client.get("/api/v4/kx/board", headers=auth)
    assert stale_response.status_code == 200
    stale = stale_response.json()
    assert stale["source"]["stale"] is True and "连不上" in stale["source"]["reason"]
    assert stale["rooms"]["waiting"] == []
    assert stale["source"]["checkedAt"] == "2026-11-04T18:30:10.318216+08:00"
    assert stale["source"]["lastReadAt"] == "2026-11-04T18:30:10+08:00"
    assert READ_KEY not in stale_response.text
    _save("kx_board_v3_stale.json", stale)

    reset_board_state()
    client, _path = _client(tmp_path, monkeypatch)
    _use(FakeKx(load_overview(), down=True), Clock(at("2026-11-04", "18:30")))
    missing = client.get("/api/v4/kx/board", headers=auth)
    assert missing.status_code == 503
    _save("kx_board_unavailable.json", missing.json())
    for name in ("kx_board_v3.json", "kx_board_v3_partial.json", "kx_board_v3_sealed.json",
                 "kx_board_v3_auction.json", "kx_board_v3_stale.json", "kx_board_unavailable.json"):
        assert (FIXTURE_DIR / name).is_file(), name
        assert (APP_FIXTURE_DIR / name).is_file(), name
