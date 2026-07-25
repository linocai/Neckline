"""v1.2-A2 熔断纪律 API 端点单测(plan §五 v1.2-A2 验收①/②/⑤,契约清单核对,🔴)。

覆盖:GET /circuit 初始未锁定;清仓带 closeReason 落库 + 连续 3 笔止损锁定;
PositionsOut.circuit 内嵌锁定态;POST /circuit/unlock 解锁;closeReason 非法码 422;
周复盘覆盖触发周且强制复盘口径 → /review/upload 自动解锁(端到端)。
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

import openpyxl
import pytest

from neckline.sentinel import circuit
from neckline.sentinel.positions import get_position

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


def _open(client, AUTH, code="600001.SH", buy=100.0, qty=100):
    return client.post("/api/v1/positions", headers=AUTH, json={
        "code": code, "buy_price": buy, "qty": qty,
    }).json()["position_id"]


def _close(client, AUTH, pid, sell, sell_time, reason=None):
    body = {"sell_price": sell, "sell_time": sell_time}
    if reason is not None:
        body["closeReason"] = reason
    return client.post(f"/api/v1/positions/{pid}/close", headers=AUTH, json=body)


# ————————————————————————————————————————————————————————————————
# 1) 状态端点 + 清仓接线
# ————————————————————————————————————————————————————————————————

def test_circuit_initially_unlocked(client, AUTH):
    r = client.get("/api/v1/circuit", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["locked"] is False
    assert body.get("episode") is None


def test_positions_out_embeds_circuit(client, AUTH):
    body = client.get("/api/v1/positions", headers=AUTH).json()
    assert "circuit" in body and body["circuit"]["locked"] is False


def test_close_with_reason_round_trips_to_db(client, AUTH, api_env):
    pid = _open(client, AUTH)
    assert _close(client, AUTH, pid, 95.0, "20260722", reason="TAKE_PROFIT").json()["ok"] is True
    assert get_position(pid, db_path=api_env.db_path).close_reason == "TAKE_PROFIT"


def test_close_reason_invalid_code_422(client, AUTH):
    pid = _open(client, AUTH)
    r = _close(client, AUTH, pid, 95.0, "20260722", reason="BOGUS")
    assert r.status_code == 422


def test_close_reason_optional(client, AUTH, api_env):
    """不传 closeReason → 落库 NULL,单笔非止损不锁定。"""
    pid = _open(client, AUTH)
    assert _close(client, AUTH, pid, 105.0, "20260722").json()["ok"] is True
    assert get_position(pid, db_path=api_env.db_path).close_reason is None
    assert client.get("/api/v1/circuit", headers=AUTH).json()["locked"] is False


def test_three_stop_losses_lock_then_unlock(client, AUTH):
    """连续 3 笔止损(显式 STOP_LOSS)→ 锁定;GET /circuit + PositionsOut.circuit 均反映;
    POST /circuit/unlock → 解锁。诚实边界字段(basisTradesCount/note)随状态下发。"""
    for i, d in enumerate(("20260720", "20260721", "20260722")):
        pid = _open(client, AUTH)
        _close(client, AUTH, pid, 90.0, d, reason="STOP_LOSS")

    st = client.get("/api/v1/circuit", headers=AUTH).json()
    assert st["locked"] is True
    ep = st["episode"]
    assert ep is not None
    assert ep["triggerReason"] == "consecutive_stops"
    assert ep["basisTradesCount"] >= 3
    assert ep["triggerRefDate"] == "20260722"
    assert "已补录成交" in ep["note"]        # 诚实边界文案

    # 今日计划面内嵌熔断态也锁定
    assert client.get("/api/v1/positions", headers=AUTH).json()["circuit"]["locked"] is True

    # 解锁(客户端熔断复盘按钮)
    assert client.post("/api/v1/circuit/unlock", headers=AUTH).json()["ok"] is True
    assert client.get("/api/v1/circuit", headers=AUTH).json()["locked"] is False


def test_unlock_when_not_locked_is_idempotent(client, AUTH):
    assert client.post("/api/v1/circuit/unlock", headers=AUTH).json()["ok"] is True
    assert client.get("/api/v1/circuit", headers=AUTH).json()["locked"] is False


def test_circuit_requires_auth(client):
    assert client.get("/api/v1/circuit").status_code == 401
    assert client.post("/api/v1/circuit/unlock").status_code == 401


# ————————————————————————————————————————————————————————————————
# 2) 周复盘自动解锁(端到端,plan A2.7 自动路径)
# ————————————————————————————————————————————————————————————————

def test_weekly_review_upload_auto_unlocks(client, AUTH, api_env):
    """锁定后上传覆盖触发周(2026-W30)且触发强制复盘口径的交割单 → 自动解锁
    (unlocked_via='weekly_review')。"""
    insert_stock_basic(api_env, [{"ts_code": "600519.SH", "name": "贵州茅台", "market": "主板"}])
    seed_active_rule_v1(api_env)

    # 锁定:连续 3 笔止损,触发日 20260722(落在 ISO 2026-W30:07-20~07-26)
    for d in ("20260720", "20260721", "20260722"):
        pid = _open(client, AUTH)
        _close(client, AUTH, pid, 90.0, d, reason="STOP_LOSS")
    assert client.get("/api/v1/circuit", headers=AUTH).json()["locked"] is True

    # W30 交割单:买 1500 → 卖 1425,单周实现亏损 ~¥7530 ≥ 总仓 2%(¥2400)→ 强制复盘
    wb = _xlsx_bytes([
        FORMAT1_HEADER,
        [datetime(2026, 7, 20), "国泰君安", "证券买入清算", "贵州茅台", 100, 100, 15.0, -150015.0, 100000.0, ""],
        [datetime(2026, 7, 22), "国泰君安", "证券卖出清算", "贵州茅台", 100, 0, 15.0, 142485.0, 242485.0, ""],
    ])
    r = client.post(
        "/api/v1/review/upload", headers=AUTH,
        files={"files": ("交割单.xlsx", wb, "application/octet-stream")},
    )
    assert r.status_code == 200
    weeks = r.json()["weeks"]
    assert any(w["result"]["forcedReview"] for w in weeks)   # 确有强制复盘周

    # 自动解锁到位
    assert client.get("/api/v1/circuit", headers=AUTH).json()["locked"] is False
    ep = circuit.list_episodes(db_path=api_env.db_path)[0]
    assert ep.unlocked_via == circuit.UNLOCK_VIA_WEEKLY_REVIEW


def test_weekly_review_non_forced_does_not_unlock(client, AUTH, api_env):
    """上传的周未达强制复盘口径(小额亏损)→ 即便覆盖触发日也不自动解锁。"""
    insert_stock_basic(api_env, [{"ts_code": "600519.SH", "name": "贵州茅台", "market": "主板"}])
    seed_active_rule_v1(api_env)

    for d in ("20260720", "20260721", "20260722"):
        pid = _open(client, AUTH)
        _close(client, AUTH, pid, 90.0, d, reason="STOP_LOSS")
    assert client.get("/api/v1/circuit", headers=AUTH).json()["locked"] is True

    # 小额亏损(买 15.00 → 卖 14.90,亏 ~¥40,远不到强制复盘线)
    wb = _xlsx_bytes([
        FORMAT1_HEADER,
        [datetime(2026, 7, 20), "国泰君安", "证券买入清算", "贵州茅台", 100, 100, 15.0, -1515.0, 100000.0, ""],
        [datetime(2026, 7, 22), "国泰君安", "证券卖出清算", "贵州茅台", 100, 0, 15.0, 1475.0, 2475.0, ""],
    ])
    r = client.post(
        "/api/v1/review/upload", headers=AUTH,
        files={"files": ("交割单.xlsx", wb, "application/octet-stream")},
    )
    assert r.status_code == 200
    assert not any(w["result"]["forcedReview"] for w in r.json()["weeks"])
    # 未强制复盘 → 仍锁定
    assert client.get("/api/v1/circuit", headers=AUTH).json()["locked"] is True
