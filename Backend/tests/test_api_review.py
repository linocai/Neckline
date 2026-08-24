"""现役复盘 API：交割单解析、材料装订、结论存档与两段概览。"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

import openpyxl
import pytest

from .conftest import insert_stock_basic


HEADER = ["交易日期", "券商来源", "业务名称", "证券名称", "成交数量", "股份余额",
          "费用", "发生金额", "资金余额", "备注"]


def _xlsx(rows) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "对账单"
    for row in rows:
        sheet.append(row)
    buf = BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def _sample() -> bytes:
    return _xlsx([
        HEADER,
        [datetime(2026, 7, 14), "国泰君安", "证券买入清算", "贵州茅台", 100, 100,
         15.0, -150015.0, 100000.0, ""],
        [datetime(2026, 7, 16), "国泰君安", "证券卖出清算", "贵州茅台", 100, 0,
         15.0, 142485.0, 242485.0, ""],
    ])


@pytest.fixture
def review_env(api_env):
    insert_stock_basic(api_env, [
        {"ts_code": "600519.SH", "name": "贵州茅台", "market": "主板"},
    ])
    return api_env


def _upload(client, auth) -> str:
    response = client.post(
        "/api/v1/review/upload", headers=auth,
        files={"files": ("交割单.xlsx", _sample(), "application/octet-stream")})
    assert response.status_code == 200
    return response.json()["weeks"][0]["week"]


def test_upload_requires_auth(client):
    assert client.post("/api/v1/review/upload",
                       files={"files": ("t.xlsx", _sample())}).status_code == 401


def test_upload_rejects_more_than_five_files_before_parsing(client, AUTH, review_env):
    files = [("files", (f"{i}.xlsx", b"x")) for i in range(6)]
    response = client.post("/api/v1/review/upload", headers=AUTH, files=files)
    assert response.status_code == 413
    assert "最多上传 5" in response.json()["detail"]


def test_upload_rejects_single_file_over_ten_megabytes_before_parsing(client, AUTH, review_env):
    response = client.post(
        "/api/v1/review/upload", headers=AUTH,
        files={"files": ("large.xlsx", b"x" * (10 * 1024 * 1024 + 1))},
    )
    assert response.status_code == 413
    assert "10 MB" in response.json()["detail"]


def test_upload_and_read_round_trip(client, AUTH, review_env):
    week = _upload(client, AUTH)
    body = client.get(f"/api/v1/review?week={week}", headers=AUTH).json()
    assert body["found"] is True
    assert body["result"]["stats"]["closedCount"] == 1
    assert body["result"]["roundTrips"][0]["tsCode"] == "600519.SH"
    assert body["material"]


def test_invalid_upload_degrades_without_crashing(client, AUTH, review_env):
    body = client.post(
        "/api/v1/review/upload", headers=AUTH,
        files={"files": ("bad.xlsx", b"not an xlsx")}).json()
    assert body["ok"] is True and body["weeks"] == []
    assert any("bad.xlsx" in item for item in body["parseWarnings"])


def test_overview_has_only_the_two_live_segments(client, AUTH, review_env):
    body = client.get("/api/v1/review/overview?week=20260805", headers=AUTH).json()
    assert set(body) >= {"reconcile", "conclusions"}
    assert not ({"calibration", "observations"} & set(body))


def test_bindery_empty_and_populated_states(client, AUTH, review_env):
    empty = client.get("/api/v1/review/bindery?week=2099-W01", headers=AUTH).json()
    assert empty["found"] is False and empty["binding"] is None
    week = _upload(client, AUTH)
    body = client.get(f"/api/v1/review/bindery?week={week}", headers=AUTH).json()
    assert body["found"] is True
    assert body["binding"]["roundTrips"][0]["tsCode"] == "600519.SH"
    assert "回看材料" in body["markdown"] and "不是判断" in body["markdown"]


def test_conclusions_are_append_only_and_searchable(client, AUTH, review_env):
    base = {"week": "2026-W29", "title": "第一次", "body": "第一次判断。"}
    first = client.post("/api/v1/review/conclusions", headers=AUTH, json=base).json()
    second = client.post(
        "/api/v1/review/conclusions", headers=AUTH,
        json={**base, "title": "修订", "body": "复看后修订。"}).json()
    assert first["latest"]["version"] == 1 and second["latest"]["version"] == 2
    saved = client.get("/api/v1/review/conclusions?week=2026-W29", headers=AUTH).json()
    assert [item["version"] for item in saved["versions"]] == [1, 2]
    assert saved["versions"][0]["title"] == "第一次"
    hits = client.get("/api/v1/review/conclusions?q=修订", headers=AUTH).json()["matches"]
    assert [item["week"] for item in hits] == ["2026-W29"]


def test_invalid_conclusion_is_422_not_truncated(client, AUTH, review_env):
    response = client.post(
        "/api/v1/review/conclusions", headers=AUTH,
        json={"week": "bad", "title": "", "body": ""})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "ISO 周" in detail and "title" in detail and "body" in detail


def test_overview_carries_latest_conclusion(client, AUTH, review_env):
    first = client.get("/api/v1/review/overview?week=20260805", headers=AUTH).json()
    week = first["weekKey"]
    client.post("/api/v1/review/conclusions", headers=AUTH,
                json={"week": week, "title": "本周结论", "body": "已经写下。"})
    body = client.get("/api/v1/review/overview?week=20260805", headers=AUTH).json()
    assert body["conclusions"]["detail"]["latest"]["title"] == "本周结论"
