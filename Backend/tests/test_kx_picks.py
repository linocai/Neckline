"""KX 推荐接收与高亮、排序。接口契约见 archive/v4.1.0_execution.md §3.3。"""
from __future__ import annotations

import asyncio
import dataclasses
import hmac
import json
import logging
import uuid
from datetime import date, datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.api import deps
from neckline.api.deps import require_token
from neckline.kx import store
from neckline.kx.api import create_router
from neckline.kx.board import build_board
from neckline.kx.days import board_clock, highlight, pick_trade_date
from neckline.morning import db
from tests.kx_support import PICKS_TOKEN, READ_KEY, at, build, run

API_TOKEN = "a" * 20
AUTH = {"Authorization": f"Bearer {PICKS_TOKEN}"}


def _app(harness, monkeypatch, *, picks_token=PICKS_TOKEN):
    monkeypatch.setattr(deps, "settings", dataclasses.replace(deps.settings, api_token=API_TOKEN))
    if picks_token:
        monkeypatch.setenv("KX_PICKS_TOKEN", picks_token)
    app = FastAPI()
    app.include_router(create_router(require_token_dependency=require_token, db_path_provider=lambda: harness.path))
    return app


@pytest.fixture
def setup(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=45)
    with TestClient(_app(harness, monkeypatch)) as client:
        yield harness, client


def _post(client, codes, *, when=None, request_id=None, headers=AUTH, **extra):
    body = {"requestId": request_id or str(uuid.uuid4()), "pickedAt": when or "2026-10-20T10:30:00+08:00",
            "stockCodes": codes, **extra}
    return client.post("/api/v4/kx/picks", json=body, headers=headers)


def _board(harness):
    return run(build_board(harness.services, harness.path))


# ------------------------------------------------------------------ 鉴权

def test_picks_require_the_bearer_token_and_compare_it_in_constant_time(setup, monkeypatch):
    harness, client = setup
    seen = []
    original = hmac.compare_digest
    monkeypatch.setattr(hmac, "compare_digest", lambda a, b: seen.append((a, b)) or original(a, b))
    for headers in ({}, {"Authorization": "Bearer " + "x" * 32}, {"Authorization": PICKS_TOKEN},
                    {"Authorization": "Basic " + PICKS_TOKEN}, {"Authorization": f"Bearer {API_TOKEN}"},
                    {"Authorization": b"Bearer \xe4\xb8\xad\xe6\x96\x87"}):
        response = _post(client, ["000000"], headers=headers)
        assert response.status_code == 401 and response.json() == {"status": "unauthorized"}, headers
    assert seen and all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in seen)
    with db.read(harness.path) as conn:
        assert conn.execute("SELECT count(*) FROM kx_picks").fetchone()[0] == 0
    assert _post(client, ["000000"]).status_code == 200


@pytest.mark.parametrize("token", [None, "", "short"])
def test_picks_are_refused_when_no_usable_token_is_configured(tmp_path, monkeypatch, token):
    harness = build(tmp_path, monkeypatch)
    monkeypatch.delenv("KX_PICKS_TOKEN", raising=False)
    if token is not None:
        monkeypatch.setenv("KX_PICKS_TOKEN", token)
    with TestClient(_app(harness, monkeypatch, picks_token=None)) as client:
        for headers in (AUTH, {"Authorization": "Bearer " + token} if token else {}, {"Authorization": "Bearer "}):
            response = _post(client, ["000000"], headers=headers)
            assert response.status_code == 401 and response.json() == {"status": "unauthorized"}
    assert harness.kx.calls == []  # 未授权时不碰 KX


# ------------------------------------------------------------------ 请求体校验

GOOD_ID = "0b0c6d4e-2f4c-4a63-9d47-6d5c1a8c1f01"


@pytest.mark.parametrize("label,body", [
    ("缺 requestId", {"pickedAt": "2026-10-20T10:30:00+08:00", "stockCodes": ["000000"]}),
    ("缺 pickedAt", {"requestId": GOOD_ID, "stockCodes": ["000000"]}),
    ("缺 stockCodes", {"requestId": GOOD_ID, "pickedAt": "2026-10-20T10:30:00+08:00"}),
    ("多余字段", {"requestId": GOOD_ID, "pickedAt": "2026-10-20T10:30:00+08:00", "stockCodes": ["000000"], "x": 1}),
    ("requestId 不是 UUID", {"requestId": "abc", "pickedAt": "2026-10-20T10:30:00+08:00", "stockCodes": ["000000"]}),
    ("requestId 无连字符", {"requestId": GOOD_ID.replace("-", "") + "----", "pickedAt": "2026-10-20T10:30:00+08:00",
                          "stockCodes": ["000000"]}),
    ("requestId 非字符串", {"requestId": 5, "pickedAt": "2026-10-20T10:30:00+08:00", "stockCodes": ["000000"]}),
    ("pickedAt 无时区", {"requestId": GOOD_ID, "pickedAt": "2026-10-20T10:30:00", "stockCodes": ["000000"]}),
    ("pickedAt 只有日期", {"requestId": GOOD_ID, "pickedAt": "2026-10-20", "stockCodes": ["000000"]}),
    ("pickedAt 乱写", {"requestId": GOOD_ID, "pickedAt": "昨天", "stockCodes": ["000000"]}),
    ("pickedAt 非字符串", {"requestId": GOOD_ID, "pickedAt": 1760927400, "stockCodes": ["000000"]}),
    ("代码为空列表", {"requestId": GOOD_ID, "pickedAt": "2026-10-20T10:30:00+08:00", "stockCodes": []}),
    ("代码超过 20 只", {"requestId": GOOD_ID, "pickedAt": "2026-10-20T10:30:00+08:00",
                       "stockCodes": [f"60{i:04d}" for i in range(21)]}),
    ("代码重复", {"requestId": GOOD_ID, "pickedAt": "2026-10-20T10:30:00+08:00", "stockCodes": ["000000", "000000"]}),
    ("代码不是 6 位", {"requestId": GOOD_ID, "pickedAt": "2026-10-20T10:30:00+08:00", "stockCodes": ["00000"]}),
    ("代码带后缀", {"requestId": GOOD_ID, "pickedAt": "2026-10-20T10:30:00+08:00", "stockCodes": ["000000.SZ"]}),
    ("代码含全角数字", {"requestId": GOOD_ID, "pickedAt": "2026-10-20T10:30:00+08:00", "stockCodes": ["００００００"]}),
    ("代码是数字", {"requestId": GOOD_ID, "pickedAt": "2026-10-20T10:30:00+08:00", "stockCodes": [600000]}),
    ("stockCodes 是字符串", {"requestId": GOOD_ID, "pickedAt": "2026-10-20T10:30:00+08:00", "stockCodes": "000000"}),
    ("请求体是数组", [1, 2]),
])
def test_invalid_request_bodies_are_rejected_with_400(setup, label, body):
    harness, client = setup
    response = client.post("/api/v4/kx/picks", json=body, headers=AUTH)
    assert response.status_code == 400, label
    assert response.json()["status"] == "invalid" and response.json()["reason"], label
    with db.read(harness.path) as conn:
        assert conn.execute("SELECT count(*) FROM kx_picks").fetchone()[0] == 0
    assert harness.kx.calls == []  # 校验不过，不去核对名单


def test_malformed_bodies_and_content_types(setup):
    harness, client = setup
    for content in (b"{not json", b"", b"\xff\xfe", b"null"):
        response = client.post("/api/v4/kx/picks", content=content, headers={**AUTH, "Content-Type": "application/json"})
        assert response.status_code == 400 and response.json()["status"] == "invalid", content
    form = client.post("/api/v4/kx/picks", data={"requestId": GOOD_ID}, headers=AUTH)
    assert form.status_code == 400 and "application/json" in form.json()["reason"]
    big = client.post("/api/v4/kx/picks", content=b" " * 20000, headers={**AUTH, "Content-Type": "application/json"})
    assert big.status_code == 400 and big.json()["reason"] == "请求体过大"
    ok = client.post("/api/v4/kx/picks", content=json.dumps({"requestId": GOOD_ID.upper(),
                     "pickedAt": "2026-10-20T10:30:00+08:00", "stockCodes": ["000000"]}),
                     headers={**AUTH, "Content-Type": "application/json; charset=utf-8"})
    assert ok.status_code == 200 and ok.json()["requestId"] == GOOD_ID  # 大小写不敏感，统一成小写


def test_pickedat_must_be_close_to_server_time(setup):
    harness, client = setup
    future = _post(client, ["000000"], when="2026-10-20T10:36:00+08:00")
    old = _post(client, ["000000"], when="2026-10-19T10:29:00+08:00")
    assert future.status_code == 400 and "未来" in future.json()["reason"]
    assert old.status_code == 400 and "一天前" in old.json()["reason"]
    assert _post(client, ["000000"], when="2026-10-20T10:34:59+08:00").status_code == 200
    assert _post(client, ["000001"], when="2026-10-19T10:31:00+08:00").status_code == 200


# ------------------------------------------------------------------ 入库、幂等、池外

def test_a_pick_is_recorded_with_its_trade_date_and_a_duplicate_is_not(setup):
    harness, client = setup
    request_id = str(uuid.uuid4())
    first = _post(client, ["000003", "600030"], request_id=request_id)
    assert first.status_code == 200
    assert first.json() == {"status": "recorded", "requestId": request_id, "tradeDate": "2026-10-20",
                            "stockCodes": ["000003", "600030"]}
    second = _post(client, ["000003", "600030"], request_id=request_id)
    assert second.status_code == 200 and second.json() == {**first.json(), "status": "duplicate"}
    with db.read(harness.path) as conn:
        rows = conn.execute("SELECT code, trade_date, picked_at FROM kx_picks ORDER BY position").fetchall()
    assert [tuple(row) for row in rows] == [("000003", "2026-10-20", "2026-10-20T02:30:00+00:00"),
                                            ("600030", "2026-10-20", "2026-10-20T02:30:00+00:00")]
    # 同一个 requestId、内容不同：400，原记录不变
    for changed in ({"codes": ["000003"]}, {"codes": ["600030", "000003"]}, {"codes": ["000003", "600030"],
                                                                             "when": "2026-10-20T10:31:00+08:00"}):
        response = _post(client, changed["codes"], request_id=request_id, when=changed.get("when"))
        assert response.status_code == 400 and response.json()["status"] == "invalid"
    # 同一时刻换一种写法（UTC）算同一个请求
    same = _post(client, ["000003", "600030"], request_id=request_id, when="2026-10-20T02:30:00Z")
    assert same.json()["status"] == "duplicate"
    with db.read(harness.path) as conn:
        assert conn.execute("SELECT count(*) FROM kx_picks").fetchone()[0] == 2


def test_a_duplicate_is_answered_from_the_record_even_after_the_pool_or_clock_moved(setup):
    harness, client = setup
    request_id = str(uuid.uuid4())
    first = _post(client, ["600044"], request_id=request_id)
    assert first.json()["status"] == "recorded"
    harness.kx.version = "kx-20261024"
    harness.kx.members = harness.kx.members[:40]  # 600044 之后被换出名单
    harness.clock.advance(3 * 24 * 3600)           # 重发时 pickedAt 已经是三天前
    again = _post(client, ["600044"], request_id=request_id)
    assert again.status_code == 200 and again.json() == {**first.json(), "status": "duplicate"}
    assert harness.kx.count("/kx/health") == 1  # 重复请求不需要核对名单


def test_a_code_outside_the_pool_rejects_the_whole_batch(setup):
    harness, client = setup
    request_id = str(uuid.uuid4())
    response = _post(client, ["000003", "300750", "688001", "600030"], request_id=request_id)
    assert response.status_code == 409
    body = response.json()
    assert body["status"] == "out_of_pool" and body["codes"] == ["300750", "688001"]
    assert "300750" in body["reason"] and "整批没有记录" in body["reason"]
    with db.read(harness.path) as conn:
        assert conn.execute("SELECT count(*) FROM kx_picks").fetchone()[0] == 0
    # 被拒绝的 requestId 没有被占用：改对之后可以用同一个编号重发
    assert _post(client, ["000003", "600030"], request_id=request_id).json()["status"] == "recorded"


def test_picks_are_checked_against_the_pool_that_was_just_verified(setup):
    harness, client = setup
    assert _post(client, ["600044"]).status_code == 200
    harness.kx.version = "kx-20261024"
    harness.kx.members = harness.kx.members[:40]  # 换池：600044 被换出
    harness.clock.advance(61)
    response = _post(client, ["600044"])
    assert response.status_code == 409 and response.json()["codes"] == ["600044"]
    # KX 连不上时按上一份名单判断，并在原因里说明
    harness.kx.down = True
    harness.clock.advance(61)
    stale = _post(client, ["600044"])
    assert stale.status_code == 409 and "名单未能与 KX 核对" in stale.json()["reason"]
    assert _post(client, ["000001"]).status_code == 200


def test_picks_get_503_when_the_pool_was_never_synced(setup):
    harness, client = setup
    harness.kx.down = True
    response = _post(client, ["000003"])
    assert response.status_code == 503 and response.json()["status"] == "pool_unverified"
    assert "KX 暂时连不上" in response.json()["reason"] and READ_KEY not in response.text
    with db.read(harness.path) as conn:
        assert conn.execute("SELECT count(*) FROM kx_picks").fetchone()[0] == 0
    harness.kx.down = False
    harness.clock.advance(20)
    assert _post(client, ["000003"]).status_code == 200


def test_picks_get_503_when_the_database_is_missing(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch)
    harness.path.unlink()
    with TestClient(_app(harness, monkeypatch)) as client:
        response = _post(client, ["000003"])
        assert response.status_code == 503 and response.json()["status"] == "db_not_ready"


def test_the_log_has_codes_and_times_but_never_a_secret(setup, caplog):
    harness, client = setup
    caplog.set_level(logging.DEBUG)
    response = _post(client, ["000003", "600030"])
    assert response.status_code == 200
    _post(client, ["000003"], headers={"Authorization": "Bearer wrong-wrong-wrong-wrong"})
    assert "000003,600030" in caplog.text and "2026-10-20" in caplog.text
    for secret in (PICKS_TOKEN, API_TOKEN, READ_KEY, "wrong-wrong"):
        assert secret not in caplog.text and secret not in response.text


def test_concurrent_requests_with_one_request_id_are_recorded_once(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch)
    app = _app(harness, monkeypatch)
    request_id = str(uuid.uuid4())
    body = {"requestId": request_id, "pickedAt": "2026-10-20T10:30:00+08:00", "stockCodes": ["000003", "600030"]}

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://kx.test") as client:
            return await asyncio.gather(*(client.post("/api/v4/kx/picks", json=body, headers=AUTH) for _ in range(6)))

    responses = asyncio.run(go())
    assert sorted(item.json()["status"] for item in responses) == ["duplicate"] * 5 + ["recorded"]
    with db.read(harness.path) as conn:
        assert conn.execute("SELECT count(*) FROM kx_picks").fetchone()[0] == 2


# ------------------------------------------------------------------ 交易日归属

@pytest.mark.parametrize("when,expected", [
    ("2026-10-20T09:00:00+08:00", "2026-10-20"),   # 交易日开盘前：当天
    ("2026-10-20T14:59:59+08:00", "2026-10-20"),   # 15:00 之前：当天
    ("2026-10-20T15:00:00+08:00", "2026-10-21"),   # 15:00 起：下一个交易日
    ("2026-10-20T23:30:00+08:00", "2026-10-21"),
    ("2026-10-16T16:00:00+08:00", "2026-10-19"),   # 周五 15:00 之后：下周一
    ("2026-10-17T10:00:00+08:00", "2026-10-19"),   # 周六：下周一
    ("2026-10-18T22:00:00+08:00", "2026-10-19"),   # 周日：下周一
    ("2026-10-02T10:00:00+08:00", "2026-10-08"),   # 国庆休市日：节后第一个交易日
    ("2026-10-10T10:00:00+08:00", "2026-10-12"),   # 调休补班的周六（股市休市）：周一
    ("2026-09-24T15:30:00+08:00", "2026-09-28"),   # 中秋前最后一个交易日收盘后：节后第一个交易日
    ("2026-10-20T06:59:59+00:00", "2026-10-20"),   # UTC 写法按北京时间算：14:59:59
    ("2026-10-20T07:00:00+00:00", "2026-10-21"),   # 北京时间 15:00:00
])
def test_trade_date_attribution(when, expected):
    assert pick_trade_date(datetime.fromisoformat(when)) == date.fromisoformat(expected)


def test_a_pick_made_after_the_close_is_recorded_for_the_next_trading_day(setup):
    harness, client = setup
    harness.clock.set(at("2026-10-20", "15:30"))
    after = _post(client, ["000003"], when="2026-10-20T15:30:00+08:00")
    assert after.json()["tradeDate"] == "2026-10-21"
    harness.clock.set(at("2026-10-24", "11:00"))
    weekend = _post(client, ["000004"], when="2026-10-24T11:00:00+08:00")
    assert weekend.json()["tradeDate"] == "2026-10-26"


def test_a_calendar_gap_is_reported_not_guessed(setup):
    harness, client = setup
    harness.clock.set(at("2026-12-31", "16:00"))  # 2027 年的休市表还没有
    response = _post(client, ["000003"], when="2026-12-31T16:00:00+08:00")
    assert response.status_code == 503 and response.json()["status"] == "calendar_gap"


# ------------------------------------------------------------------ 高亮规则

def _states(pick_day: str, hm: str, check_days: list[str]) -> list[str]:
    trade = pick_trade_date(at(pick_day, hm))
    return [highlight(trade, board_clock(at(day, "10:00"))) for day in check_days]


def test_highlight_decays_by_trading_day():
    # 周二上午推荐：当天加粗，周三变淡，周四恢复普通
    assert _states("2026-10-20", "10:00", ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]) == [
        "today", "faded", "normal", "normal"]
    # 周二 15:00 之后推荐：归周三；周二晚上就已经加粗，周三仍加粗，周四变淡，周五恢复
    after_close = pick_trade_date(at("2026-10-20", "15:00"))
    assert after_close == date(2026, 10, 21)
    assert [highlight(after_close, board_clock(at("2026-10-20", hm))) for hm in ("15:00", "20:00", "23:59")] == ["today"] * 3
    assert [highlight(after_close, board_clock(at(day, "10:00"))) for day in ("2026-10-21", "2026-10-22", "2026-10-23")] == [
        "today", "faded", "normal"]


def test_highlight_crosses_weekends_and_holidays_by_trading_days():
    # 周五上午推荐：周六、周日参考日是下周一，周五已是前一个交易日 -> 变淡；周一仍变淡；周二恢复
    assert _states("2026-10-16", "10:00", ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"]) == [
        "today", "faded", "faded", "faded", "normal"]
    # 周五 15:00 之后推荐：归周一，周末一直加粗，周一加粗，周二变淡，周三恢复
    assert _states("2026-10-16", "15:30", ["2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21"]) == [
        "today", "today", "today", "faded", "normal"]
    # 中秋（9/25–9/27）前最后一个交易日 9/24 推荐：假期里和 9/28 都是变淡，9/29 恢复
    assert _states("2026-09-24", "10:00", ["2026-09-24", "2026-09-25", "2026-09-27", "2026-09-28", "2026-09-29"]) == [
        "today", "faded", "faded", "faded", "normal"]
    # 国庆休市期间推荐：归节后第一个交易日 10/8，休市期间和 10/8 加粗，10/9 变淡，10/12 恢复
    assert _states("2026-10-02", "10:00", ["2026-10-03", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12"]) == [
        "today", "today", "today", "faded", "normal"]


def _put(harness, stocks: list[str], moment: str) -> str:
    """直接写一次推荐（业务时钟不动），返回归属交易日。"""
    picked = at(moment[:10], moment[11:16], int(moment[17:19]) if len(moment) > 16 else 0)
    trade = pick_trade_date(picked)
    with db.write(harness.path) as conn:
        store.insert_picks(conn, request_id=str(uuid.uuid4()), codes=stocks, picked_at=picked, trade_date=trade,
                           received_at=picked)
    return trade.isoformat()


def _highlights(board):
    return {row["code"]: (row["highlight"], row["lastPickedAt"]) for row in board["rows"]}


def test_board_highlights_follow_the_clock_and_a_repick_resets_the_day(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=10)
    _board(harness)
    _put(harness, ["000003"], "2026-10-20T10:00:00")
    for moment, expected in (("2026-10-20", "today"), ("2026-10-21", "faded"), ("2026-10-22", "normal")):
        harness.clock.set(at(moment, "10:30"))
        harness.mairui.day = moment
        row = _highlights(_board(harness))["000003"]
        assert row[0] == expected and row[1] == "2026-10-20T10:00:00+08:00", moment
    # 中途再次被推荐：重新从那一天算起
    _put(harness, ["000003"], "2026-10-22T10:30:00")
    row = _highlights(_board(harness))["000003"]
    assert row == ("today", "2026-10-22T10:30:00+08:00")
    harness.clock.set(at("2026-10-23", "10:30"))
    harness.mairui.day = "2026-10-23"
    assert _highlights(_board(harness))["000003"][0] == "faded"


def test_the_latest_pick_decides_and_picks_for_a_removed_stock_are_ignored(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=10)
    _put(harness, ["000003"], "2026-10-19T10:00:00")
    _put(harness, ["000003", "009999"], "2026-10-20T10:00:00")  # 009999 不在名单里（后来被换出）
    board = _board(harness)
    assert _highlights(board)["000003"] == ("today", "2026-10-20T10:00:00+08:00")
    assert "009999" not in {row["code"] for row in board["rows"]}
    assert board["picks"]["lastPickAt"] == "2026-10-20T10:00:00+08:00"


def test_board_order_pinned_by_recency_then_change_percent(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=45)
    # 前一个交易日（周一）推荐 600040、600020；今天（周二）三次推荐，每次 3 只，累计 9 只
    _put(harness, ["600040", "600020"], "2026-10-19T10:00:00")
    _put(harness, ["600030", "000001", "600011"], "2026-10-20T09:40:00")
    _put(harness, ["600031", "600012", "600013"], "2026-10-20T10:10:00")
    _put(harness, ["000004", "600015", "600014"], "2026-10-20T10:20:00")
    board = _board(harness)
    codes = [row["code"] for row in board["rows"]]
    states = [row["highlight"] for row in board["rows"]]
    assert states == ["today"] * 9 + ["faded"] * 2 + ["normal"] * 34
    # 当天推荐：最近一次在前，同一次推荐里按推荐的次序；前一交易日的在后，同样最近在前
    assert codes[:11] == ["000004", "600015", "600014", "600031", "600012", "600013", "600030", "000001", "600011",
                          "600040", "600020"]
    changes = [row["changePercent"] for row in board["rows"][11:]]
    assert changes == sorted(changes, reverse=True)
    # 同一涨跌幅的并列按名单次序
    normal = board["rows"][11:]
    for left, right in zip(normal, normal[1:]):
        if left["changePercent"] == right["changePercent"]:
            assert left["code"] < right["code"] or left["code"].startswith("00") and right["code"].startswith("60")


def test_rows_without_a_current_quote_sort_last_among_the_normal_rows(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=25)
    harness.mairui.drop_codes = {"600010", "600011"}
    harness.mairui.stale_codes = {"600012": "2026-10-16", "600013": "2026-10-16"}  # 停牌：旧报价
    _put(harness, ["600011"], "2026-10-20T10:00:00")
    board = _board(harness)
    rows = board["rows"]
    assert rows[0]["code"] == "600011" and rows[0]["highlight"] == "today" and rows[0]["quoteStatus"] == "missing"
    normal = rows[1:]
    assert [row["quoteStatus"] for row in normal[-3:]] == ["missing", "stale", "stale"]
    assert all(row["quoteStatus"] == "ok" for row in normal[:-3])
    known = [row["changePercent"] for row in normal[:-3]]
    assert known == sorted(known, reverse=True)
    # 旧报价的涨跌幅仍然显示（并标注日期），只是不参与排序
    stale = {row["code"]: row for row in normal if row["quoteStatus"] == "stale"}
    assert set(stale) == {"600012", "600013"} and all(row["changePercent"] is not None for row in stale.values())
    assert [row["code"] for row in normal[-3:]] == ["600010", "600012", "600013"]
