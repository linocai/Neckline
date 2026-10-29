"""看板接口 GET /api/v4/kx/board：鉴权、错误、health 就绪项，以及固定的示例响应（喂 Swift 模型解码用）。

示例响应由隔离库和确定性假 transport 真实产出，和 tests/fixtures 里的文件逐字段比对；
设置环境变量 NK_WRITE_FIXTURES=1 时改写这两份文件。
  kx_board_sample.json    盘中、名单已核对、行情与日线统计齐全，有当天推荐、前一交易日推荐和普通行。
  kx_board_degraded.json  名单未能核对、一只行情没取到、一只报价不是当日、TuShare 失败：各种 null 的形状。
  kx_board_schema2.json   KX 名单 schema 2：industry 为 null，题材、首次入选日、热了第几天齐全。
  kx_board_schema3.json   KX 名单 schema 3，10:30 盘中。
  kx_board_call_auction.json  schema 3，交易日 09:18：多数行竞价中，一行停牌，一行今日推荐。
  kx_board_pre_open.json  schema 3，交易日 09:27：开盘价。
"""
from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest
import neckline.mcp.server  # noqa: F401  先于离线子进程守卫导入（mcp 的 win32 模块在守卫改掉 Popen 后导入会失败）
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.api import deps
from neckline.api.app import app as production_app
from neckline.api.deps import require_token
from neckline.kx.api import create_router
from neckline.morning import db
from tests.kx_support import PICKS_TOKEN, READ_KEY, at, build
from tests.test_kx_picks import _board, _put

API_TOKEN = "t" * 24
FIXTURES = Path(__file__).parent / "fixtures"


def _client(harness, monkeypatch, *, path=True):
    monkeypatch.setattr(deps, "settings", dataclasses.replace(deps.settings, api_token=API_TOKEN))
    app = FastAPI()
    app.include_router(create_router(require_token_dependency=require_token,
                                     db_path_provider=lambda: harness.path if path else None))
    return TestClient(app)


AUTH = {"Authorization": f"Bearer {API_TOKEN}"}


def test_the_board_needs_the_api_token_and_not_the_picks_token(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=5)
    monkeypatch.setenv("KX_PICKS_TOKEN", PICKS_TOKEN)
    with _client(harness, monkeypatch) as client:
        for headers in ({}, {"Authorization": "Bearer " + "x" * 24}, {"Authorization": f"Bearer {PICKS_TOKEN}"}):
            assert client.get("/api/v4/kx/board", headers=headers).status_code == 401
        assert harness.kx.calls == [] and harness.mairui.calls == []  # 未授权不碰任何外部服务
        assert client.get("/api/v4/kx/board", headers=AUTH).status_code == 200
        # 推荐接口不收晨报的 API_TOKEN
        denied = client.post("/api/v4/kx/picks", headers=AUTH, json={})
        assert denied.status_code == 401 and denied.json() == {"status": "unauthorized"}


def test_board_errors_say_why(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=5)
    with _client(harness, monkeypatch, path=False) as client:
        response = client.get("/api/v4/kx/board", headers=AUTH)
        assert response.status_code == 503 and response.json() == {"detail": {"reason": "v4_not_configured"}}
    with _client(harness, monkeypatch) as client:
        harness.kx.down = True
        response = client.get("/api/v4/kx/board", headers=AUTH)
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["reason"] == "pool_unverified" and "KX 暂时连不上" in detail["message"]
        assert READ_KEY not in response.text
        harness.kx.down = False
        harness.clock.advance(30)
        harness.clock.set(at("2027-01-05", "10:30"))
        response = client.get("/api/v4/kx/board", headers=AUTH)
        assert response.status_code == 503 and response.json()["detail"]["reason"] == "calendar_gap"
        harness.clock.set(at("2026-10-20", "10:30"))
        harness.path.rename(harness.path.with_name("moved.db"))
        response = client.get("/api/v4/kx/board", headers=AUTH)
        assert response.status_code == 503 and response.json()["detail"]["reason"] == "v4_db_not_ready"


def test_health_reports_the_kx_flags_without_calling_kx(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=5)
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(harness.path))
    monkeypatch.setattr(deps, "settings", dataclasses.replace(deps.settings, api_token=API_TOKEN))
    harness.kx.down = True  # KX 不可达也不能拖垮 health
    with TestClient(production_app) as client:
        body = client.get("/api/v4/health").json()
        assert body["kxPool"] is True and body["kxPicks"] is False
        assert body["db"] == "ready" and body["releaseSet"] and body["readPage"] in (True, False)
        monkeypatch.setenv("KX_PICKS_TOKEN", PICKS_TOKEN)
        assert client.get("/api/v4/health").json()["kxPicks"] is True
        monkeypatch.setenv("KX_PICKS_TOKEN", "short")
        monkeypatch.setenv("KX_READ_KEY", "short")
        body = client.get("/api/v4/health").json()
        assert body["kxPool"] is False and body["kxPicks"] is False
        assert READ_KEY not in json.dumps(body) and PICKS_TOKEN not in json.dumps(body)
    assert harness.kx.calls == []


def test_the_production_app_mounts_the_kx_routes(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=5)
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(harness.path))
    monkeypatch.setenv("KX_PICKS_TOKEN", PICKS_TOKEN)
    monkeypatch.setattr(deps, "settings", dataclasses.replace(deps.settings, api_token=API_TOKEN))
    with TestClient(production_app) as client:
        assert client.get("/api/v4/kx/board").status_code == 401
        board = client.get("/api/v4/kx/board", headers=AUTH)
        assert board.status_code == 200 and len(board.json()["rows"]) == 5
        body = {"requestId": "0b0c6d4e-2f4c-4a63-9d47-6d5c1a8c1f01", "pickedAt": "2026-10-20T10:30:00+08:00",
                "stockCodes": ["000001"]}
        assert client.post("/api/v4/kx/picks", json=body).status_code == 401
        recorded = client.post("/api/v4/kx/picks", json=body, headers={"Authorization": f"Bearer {PICKS_TOKEN}"})
        assert recorded.status_code == 200 and recorded.json()["status"] == "recorded"
        assert client.get("/api/v4/kx/board", headers=AUTH).json()["rows"][0]["code"] == "000001"


def test_board_response_has_the_documented_shape(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=5)
    with _client(harness, monkeypatch) as client:
        body = client.get("/api/v4/kx/board", headers=AUTH).json()
    assert set(body) == {"generatedAt", "pool", "session", "stats", "picks", "gaps", "rows"}
    assert set(body["pool"]) == {"version", "count", "checkedAt", "stale", "reason"}
    assert set(body["session"]) == {"tradeDate", "inSession", "refreshSeconds", "quoteTime"}
    assert set(body["rows"][0]) == {
        "code", "name", "industry", "theme", "firstSeen", "heatDay", "highlight", "lastPickedAt",
        "quoteStatus", "sessionPhase", "note", "quoteTime", "price",
        "changePercent", "changeAmount", "open", "high", "low", "previousClose", "amplitudePercent",
        "turnoverRatePercent", "turnoverYi", "volumeLots", "openChangePercent", "rangePosition",
        "pullbackFromHighPercent", "change5dPercent", "change20dPercent", "avgTurnover20Yi", "volumeRatio",
        "totalMvYi", "circMvYi", "averages"}


# ------------------------------------------------------------------ 示例响应

def _sample(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=12, now=at("2026-10-20", "10:31", 7))
    _put(harness, ["000003", "600008"], "2026-10-20T09:50:00")     # 当天：较早的一次
    _put(harness, ["600010", "000001"], "2026-10-20T10:20:00")     # 当天：最近一次，先 600010 后 000001
    _put(harness, ["600007"], "2026-10-19T14:20:00")               # 前一交易日：变淡
    _put(harness, ["600011"], "2026-10-16T10:00:00")               # 更早：恢复普通样式，但留着最近推荐时间
    return harness


def _degraded(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=6, now=at("2026-10-20", "10:31", 7))
    harness.tushare.fail = True
    _board(harness)  # 名单核对成功；TuShare 失败
    harness.clock.set(at("2026-10-20", "10:33", 7))
    harness.kx.down = True  # 之后 KX 失联：名单沿用上一份，标明未核对
    harness.mairui.drop_codes = {"000001"}
    harness.mairui.stale_codes = {"000002": "2026-10-16"}
    _put(harness, ["000003"], "2026-10-20T10:20:00")
    return harness


def _schema2(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=12, now=at("2026-10-20", "10:31", 7))
    harness.kx.schema, harness.kx.version = 2, "kx-20261020-001"
    harness.kx.first_seen = {
        "000000": "2026-10-20",   # 今天才进：第 1 天
        "000001": "2026-10-19",   # 周一：第 2 天
        "000002": "2026-10-16",   # 上周五：第 3 天
        "000003": "2026-10-17",   # 周六进的：从周一起算，第 2 天
        "000004": "2026-10-21",   # 晚于行情日：null
        "600005": "2026-10-01",   # 国庆假期里进的：从节后 10-08 起算，第 9 天
    }
    _put(harness, ["600010"], "2026-10-20T10:20:00")  # 当天推荐
    _put(harness, ["600007"], "2026-10-19T14:20:00")  # 前一交易日：变淡
    return harness


def _schema3(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=12, now=at("2026-10-20", "10:30"))
    harness.kx.schema, harness.kx.version = 3, "kx-20261029-001"
    harness.mairui.quote_clock = "10:30:00"
    harness.kx.first_seen = {
        "000000": "2026-10-20", "000001": "2026-10-19", "000002": "2026-10-16",
        "000003": "2026-10-17", "000004": "2026-10-21", "600005": "2026-10-01",
    }
    _put(harness, ["600010"], "2026-10-20T10:20:00")
    return harness


def _call_auction(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=6, now=at("2026-10-20", "09:18"))
    harness.kx.schema, harness.kx.version = 3, "kx-20261020-auc"
    harness.mairui.quote_clock = "09:18:00"
    harness.mairui.quote_shape = "call_auction"
    harness.mairui.stale_codes = {"000002": "2026-10-19"}
    harness.kx.first_seen = {"000000": "2026-10-20", "000001": "2026-10-19"}
    _put(harness, ["000001"], "2026-10-20T09:16:00")  # 当天推荐
    return harness


def _pre_open(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=6, now=at("2026-10-20", "09:27"))
    harness.kx.schema, harness.kx.version = 3, "kx-20261020-pre"
    harness.mairui.quote_clock = "09:27:00"
    harness.mairui.quote_shape = "pre_open"
    harness.kx.first_seen = {"000000": "2026-10-20"}
    return harness


@pytest.mark.parametrize("name,make", [
    ("kx_board_sample.json", _sample), ("kx_board_degraded.json", _degraded),
    ("kx_board_schema2.json", _schema2), ("kx_board_schema3.json", _schema3),
    ("kx_board_call_auction.json", _call_auction), ("kx_board_pre_open.json", _pre_open),
])
def test_board_sample_responses_match_the_fixtures(tmp_path, monkeypatch, name, make):
    harness = make(tmp_path, monkeypatch)
    with _client(harness, monkeypatch) as client:
        response = client.get("/api/v4/kx/board", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    blob = response.text
    for secret in (API_TOKEN, PICKS_TOKEN, READ_KEY, "test-licence", "test-tushare-token"):
        assert secret not in blob
    if os.environ.get("NK_WRITE_FIXTURES") == "1":
        FIXTURES.mkdir(parents=True, exist_ok=True)
        (FIXTURES / name).write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert json.loads((FIXTURES / name).read_text(encoding="utf-8")) == body


def test_sample_fixture_shapes():
    sample = json.loads((FIXTURES / "kx_board_sample.json").read_text(encoding="utf-8"))
    assert sample["pool"]["stale"] is False and sample["session"]["inSession"] is True
    assert [row["highlight"] for row in sample["rows"]] == ["today"] * 4 + ["faded"] + ["normal"] * 7
    assert [row["code"] for row in sample["rows"][:5]] == ["600010", "000001", "000003", "600008", "600007"]
    rows = {row["code"]: row for row in sample["rows"]}
    assert rows["600011"]["highlight"] == "normal" and rows["600011"]["lastPickedAt"] == "2026-10-16T10:00:00+08:00"
    assert rows["600010"]["lastPickedAt"] == "2026-10-20T10:20:00+08:00"
    assert all(row["quoteStatus"] == "ok" and row["note"] is None for row in sample["rows"])
    assert sample["gaps"] == [] and sample["stats"]["asOf"] == "2026-10-19"
    degraded = json.loads((FIXTURES / "kx_board_degraded.json").read_text(encoding="utf-8"))
    assert degraded["pool"]["stale"] is True and degraded["stats"]["asOf"] is None
    statuses = {row["code"]: row["quoteStatus"] for row in degraded["rows"]}
    assert statuses["000001"] == "missing" and statuses["000002"] == "stale" and statuses["000003"] == "ok"
    assert degraded["rows"][0]["code"] == "000003" and degraded["rows"][0]["highlight"] == "today"
    for board in (sample, degraded):  # schema 1 的名单：行业在，题材与热了第几天都是 null；10:30 没有竞价阶段
        assert "schemaVersion" not in board["pool"] and "buyable" not in board["rows"][0]
        assert all(row["industry"] and row["sessionPhase"] is None and
                   (row["theme"], row["firstSeen"], row["heatDay"]) == (None, None, None) for row in board["rows"])


def test_schema_2_fixture_shapes():
    board = json.loads((FIXTURES / "kx_board_schema2.json").read_text(encoding="utf-8"))
    assert "schemaVersion" not in board["pool"] and board["pool"]["count"] == 12
    assert board["session"]["tradeDate"] == "2026-10-20"
    assert [row["highlight"] for row in board["rows"]] == ["today", "faded"] + ["normal"] * 10
    assert all(row["industry"] is None and row["theme"].startswith("题材") and "buyable" not in row
               and row["sessionPhase"] is None for row in board["rows"])
    rows = {row["code"]: row for row in board["rows"]}
    assert {code: (row["firstSeen"], row["heatDay"]) for code, row in rows.items()
            if code in ("000000", "000001", "000002", "000003", "000004", "600005", "600007")} == {
        "000000": ("2026-10-20", 1), "000001": ("2026-10-19", 2), "000002": ("2026-10-16", 3),
        "000003": ("2026-10-17", 2), "000004": ("2026-10-21", None), "600005": ("2026-10-01", 9),
        "600007": ("2026-10-19", 2)}
    assert all(row["quoteStatus"] == "ok" for row in board["rows"])


_AUCTION_BLANK = ("open", "high", "low", "amplitudePercent", "turnoverRatePercent", "turnoverYi", "volumeLots",
                  "openChangePercent", "rangePosition", "pullbackFromHighPercent", "volumeRatio")


def test_schema_3_and_auction_fixture_shapes():
    intraday = json.loads((FIXTURES / "kx_board_schema3.json").read_text(encoding="utf-8"))
    assert intraday["pool"]["version"] == "kx-20261029-001" and intraday["session"]["quoteTime"].endswith("10:30:00+08:00")
    assert all(row["sessionPhase"] is None and row["quoteStatus"] == "ok" and row["theme"] for row in intraday["rows"])
    assert intraday["rows"][0]["highlight"] == "today"

    call = json.loads((FIXTURES / "kx_board_call_auction.json").read_text(encoding="utf-8"))
    assert call["session"]["tradeDate"] == "2026-10-20" and call["gaps"] == []
    rows = {row["code"]: row for row in call["rows"]}
    assert rows["000001"]["highlight"] == "today" and rows["000001"]["sessionPhase"] == "call_auction"
    assert rows["000002"]["quoteStatus"] == "stale" and rows["000002"]["sessionPhase"] is None
    live = [row for row in call["rows"] if row["code"] != "000002"]
    assert live and all(row["sessionPhase"] == "call_auction" and row["quoteStatus"] == "ok" and row["note"] is None
                        for row in live)
    assert all(row["price"] is not None and row["changePercent"] is not None and row["previousClose"] is not None
               and row["change5dPercent"] is not None and all(row[key] is None for key in _AUCTION_BLANK)
               for row in live)

    pre = json.loads((FIXTURES / "kx_board_pre_open.json").read_text(encoding="utf-8"))
    assert all(row["sessionPhase"] == "pre_open" and row["open"] == row["high"] == row["low"] == row["price"]
               and row["rangePosition"] is None and row["volumeRatio"] is not None and row["note"] is None
               for row in pre["rows"])
