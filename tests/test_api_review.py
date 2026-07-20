"""4D 周复盘 API 端点单测(plan 4D 验收:上传解析对账/历史回放/col_map 可配/鉴权)。"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

import openpyxl
import pytest

from .conftest import insert_stock_basic, seed_active_rule_v1

FORMAT1_HEADER = ["交易日期", "券商来源", "业务名称", "证券名称", "成交数量", "股份余额", "费用", "发生金额", "资金余额", "备注"]


def _xlsx_bytes(rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "对账单"
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sample_workbook() -> bytes:
    return _xlsx_bytes([
        FORMAT1_HEADER,
        [datetime(2026, 7, 14), "国泰君安", "证券买入清算", "贵州茅台", 100, 100, 15.0, -150015.0, 100000.0, ""],
        [datetime(2026, 7, 16), "国泰君安", "证券卖出清算", "贵州茅台", 100, 0, 15.0, 142485.0, 242485.0, ""],
    ])


@pytest.fixture
def review_env(api_env):
    insert_stock_basic(api_env, [{"ts_code": "600519.SH", "name": "贵州茅台", "market": "主板"}])
    seed_active_rule_v1(api_env)
    return api_env


def test_upload_requires_auth(client):
    r = client.post("/api/v1/review/upload", files={"files": ("t.xlsx", _sample_workbook(), "application/octet-stream")})
    assert r.status_code == 401


def test_upload_and_get_roundtrip(client, AUTH, review_env):
    r = client.post(
        "/api/v1/review/upload", headers=AUTH,
        files={"files": ("交割单.xlsx", _sample_workbook(), "application/octet-stream")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["weeks"]) == 1
    week = body["weeks"][0]
    assert week["week"].startswith("2026-W")
    result = week["result"]
    assert result["stats"]["closedCount"] == 1
    assert result["roundTrips"][0]["tsCode"] == "600519.SH"
    assert week["material"]   # 确定性材料非空

    # GET /review?week= 应能读到刚落库的结果
    g = client.get(f"/api/v1/review?week={week['week']}", headers=AUTH)
    assert g.status_code == 200
    gbody = g.json()
    assert gbody["found"] is True
    assert gbody["result"]["stats"]["closedCount"] == 1
    assert gbody["material"]


def test_get_unknown_week_returns_not_found(client, AUTH, review_env):
    r = client.get("/api/v1/review?week=2099-W01", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["found"] is False


def test_get_missing_week_param_returns_not_found(client, AUTH, review_env):
    r = client.get("/api/v1/review", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["found"] is False


def test_upload_multiple_files_combined(client, AUTH, review_env):
    """两份文件(如分批导出)应合并解析、FIFO 跨文件闭合。"""
    file1 = _xlsx_bytes([FORMAT1_HEADER, [datetime(2026, 7, 14), "国泰君安", "证券买入清算", "贵州茅台", 100, 100, 15.0, -150015.0, 100000.0, ""]])
    file2 = _xlsx_bytes([FORMAT1_HEADER, [datetime(2026, 7, 16), "国泰君安", "证券卖出清算", "贵州茅台", 100, 0, 15.0, 142485.0, 242485.0, ""]])
    r = client.post(
        "/api/v1/review/upload", headers=AUTH,
        files=[("files", ("a.xlsx", file1, "application/octet-stream")), ("files", ("b.xlsx", file2, "application/octet-stream"))],
    )
    body = r.json()
    assert len(body["weeks"]) == 1
    assert body["weeks"][0]["result"]["stats"]["closedCount"] == 1


def test_upload_invalid_file_degrades_not_crashes(client, AUTH, review_env):
    r = client.post(
        "/api/v1/review/upload", headers=AUTH,
        files={"files": ("bad.xlsx", b"not a real xlsx", "application/octet-stream")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["weeks"] == []
    assert any("bad.xlsx" in w for w in body["parseWarnings"])


def test_upload_off_plan_and_ledger_gap_surface_in_result(client, AUTH, review_env):
    """未经报告候选放行、也未录入持仓台账的买入应正确标记(§4D 对账三查①)。"""
    r = client.post(
        "/api/v1/review/upload", headers=AUTH,
        files={"files": ("t.xlsx", _sample_workbook(), "application/octet-stream")},
    )
    check = r.json()["weeks"][0]["result"]["planChecks"][0]
    assert check["planStatus"].startswith("无报告数据") or check["planStatus"].startswith("计划外")
    assert check["ledgerStatus"].startswith("台账缺失")


def test_review_col_map_setting_roundtrip(client, AUTH, review_env):
    put = client.put("/api/v1/settings/review-col-map", headers=AUTH, json={"colMap": {"手续费": "费用合计"}})
    assert put.status_code == 200 and put.json()["ok"] is True
    got = client.get("/api/v1/settings", headers=AUTH).json()
    assert got["reviewColMap"] == {"手续费": "费用合计"}


def test_upload_uses_stored_col_map(client, AUTH, review_env):
    """存进 `review_col_map` 的映射应在下一次上传时生效(4D 验收:「改 review_col_map
    能吃另一种列名」)。"""
    custom_header = ["交易日期", "券商来源", "业务名称", "证券名称", "成交数量", "股份余额", "费用合计", "发生金额", "资金余额", "备注"]
    data = _xlsx_bytes([
        custom_header,
        [datetime(2026, 7, 14), "国泰君安", "证券买入清算", "贵州茅台", 100, 100, 15.0, -150015.0, 100000.0, ""],
    ])
    # 未配置 col_map 前:内置默认列名"费用"找不到 → 该行反推价格失败,解析出 0 笔交易
    baseline = client.post("/api/v1/review/upload", headers=AUTH, files={"files": ("t.xlsx", data, "application/octet-stream")})
    assert baseline.json()["weeks"] == []

    client.put("/api/v1/settings/review-col-map", headers=AUTH, json={"colMap": {"手续费": "费用合计"}})
    result = client.post("/api/v1/review/upload", headers=AUTH, files={"files": ("t.xlsx", data, "application/octet-stream")})
    body = result.json()
    assert len(body["weeks"]) == 1
    assert body["weeks"][0]["result"]["roundTrips"][0]["buyPrice"] == pytest.approx(1500.0)
