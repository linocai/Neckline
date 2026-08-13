"""熔断整体退役后的 API 面守门(V2.2-⑤-B,用户裁定 #8;前身 = v1.2-A2 熔断端点单测)。

🔴 **裁定 #8**:「我不需要你替我做决定;这个程序永远是提醒 —— 连续三笔止损真的发生了,
那也是提醒」。本文件因此从「验熔断能不能锁上/解开」**整体翻面**成「验它确实锁不上了」:

  · `GET /circuit` / `POST /circuit/unlock` → **404**(⑤-B 第 4 项:两条端点删,`GET /circuit`
    **没有替代端点** —— 提醒走推送与看板事件,不走状态查询);
  · `PositionsOut` **已无 `circuit` 键**(v2.3.0 两步淘汰第二步;V2.2 曾恒发空态一版,老客户端解码不炸的
    机器判据;真删键排 v2.3);
  · 连续 3 笔止损 → `POST /positions/{id}/close` 的**返回值逐字段不变**、`circuit_breaker`
    表**零新增行**(⑤-B 第 9 项:只推提醒,⛔ 不建行、不锁、不改任何返回值语义);
  · 周复盘上传后**不再有任何自动解锁**(⑤-B 第 10 项),而 §2.1 **第 4 条**强制复盘
    **一字不动、照常判**(⚠ 它不是熔断,⛔ 别连坐删)。

`closeReason` 落库 / 422 两条与熔断无关,原样保留。
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

import openpyxl
import pytest

from neckline.db import connection
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

def test_circuit_endpoints_are_gone_404(client, AUTH):
    """⑤-B 第 4 项:两条端点**删掉**,不是返空态 —— 由 FastAPI 天然 404。

    ⛔ **别加一条返空态的兼容路由**:那等于把"这个机制已退役"讲成"查得到、恰好没锁",
    又是一个看不出来的状态位(§五 〇b-7)。"""
    assert client.get("/api/v1/circuit", headers=AUTH).status_code == 404
    assert client.post("/api/v1/circuit/unlock", headers=AUTH).status_code == 404


def test_positions_out_has_no_circuit_key_anymore(client, AUTH):
    """**v2.3.0 两步淘汰第二步**:`circuit` 键已物理删除。

    ⚠ 与 V2.2 那一版**方向相反**(当时断言"键必须还在、恒 false")。改判据的依据是逐版
    核实:历代客户端 `/positions` 一律解进 `PositionsListResponse {holdings}`,**从没有
    一版声明过 `circuit`** —— 2.0.0 那台 iPhone 读的是独立端点 `GET /circuit`(自 V2.2 起
    404,与本键无关)。⛔ 别把它读成「零删键铁律可以不守」。"""
    body = client.get("/api/v1/positions", headers=AUTH).json()
    assert "circuit" not in body
    assert "holdings" in body


def test_close_with_reason_round_trips_to_db(client, AUTH, api_env):
    pid = _open(client, AUTH)
    assert _close(client, AUTH, pid, 95.0, "20260722", reason="TAKE_PROFIT").json()["ok"] is True
    assert get_position(pid, db_path=api_env.db_path).close_reason == "TAKE_PROFIT"


def test_close_reason_invalid_code_422(client, AUTH):
    pid = _open(client, AUTH)
    r = _close(client, AUTH, pid, 95.0, "20260722", reason="BOGUS")
    assert r.status_code == 422


def test_close_reason_optional(client, AUTH, api_env):
    """不传 closeReason → 落库 NULL(与熔断无关的既有契约,原样保留)。"""
    pid = _open(client, AUTH)
    assert _close(client, AUTH, pid, 105.0, "20260722").json()["ok"] is True
    assert get_position(pid, db_path=api_env.db_path).close_reason is None


def test_three_stop_losses_change_nothing_observable(client, AUTH, api_env):
    """🔴 **零状态**:连续 3 笔止损跑完 —— 清仓返回值逐字段不变、`circuit_breaker` 零新增行、
    `PositionsOut` 仍无 `circuit` 键、`GET /circuit` 仍 404。**留下的只有一条事件与一条推送**。"""
    for i, d in enumerate(("20260720", "20260721", "20260722")):
        pid = _open(client, AUTH, code=f"60000{i + 1}.SH")
        r = _close(client, AUTH, pid, 90.0, d, reason="STOP_LOSS")
        assert r.status_code == 200
        assert r.json() == {"ok": True}          # 返回值逐字段不变(⑤-B 第 9 项)

    with connection(api_env.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM circuit_breaker").fetchone()[0] == 0

    assert "circuit" not in client.get("/api/v1/positions", headers=AUTH).json()
    assert client.get("/api/v1/circuit", headers=AUTH).status_code == 404


def test_three_stop_losses_leave_exactly_one_board_event(client, AUTH, api_env):
    """「一条看板事件」的落点:`sentinel_events` 里 sentinel='circuit' 恰一行(第 3 笔那只)。
    ⛔ 它是**事件**不是**状态** —— 看板上没有、也不许有任何锁定横幅。"""
    for i, d in enumerate(("20260720", "20260721", "20260722")):
        pid = _open(client, AUTH, code=f"60000{i + 1}.SH")
        _close(client, AUTH, pid, 90.0, d, reason="STOP_LOSS")
    with connection(api_env.db_path) as conn:
        rows = conn.execute(
            "SELECT ts_code, event_key FROM sentinel_events WHERE sentinel='circuit'"
        ).fetchall()
    assert rows == [("600003.SH", "consecutive_stops")]


def test_deleted_circuit_endpoints_are_404_even_without_auth(client):
    """路由不存在 → 404 先于鉴权(**这正是"端点删了"而不是"401 挡住了"的判据**)。"""
    assert client.get("/api/v1/circuit").status_code == 404
    assert client.post("/api/v1/circuit/unlock").status_code == 404


# ————————————————————————————————————————————————————————————————
# 2) 周复盘:熔断自动解锁已删;§2.1 第 4 条强制复盘**一字不动**(⚠ 反向锁)
# ————————————————————————————————————————————————————————————————

def test_weekly_review_still_flags_forced_review_and_unlocks_nothing(client, AUTH, api_env):
    """⑤-B 第 10 项 + 反向锁一条:周复盘照常判 §2.1 第 4 条强制复盘线(它不是熔断),
    但**不再有任何解锁动作**,`circuit_breaker` 依旧零行。"""
    insert_stock_basic(api_env, [{"ts_code": "600519.SH", "name": "贵州茅台", "market": "主板"}])
    seed_active_rule_v1(api_env)

    for i, d in enumerate(("20260720", "20260721", "20260722")):
        pid = _open(client, AUTH, code=f"60000{i + 1}.SH")
        _close(client, AUTH, pid, 90.0, d, reason="STOP_LOSS")

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
    assert any(w["result"]["forcedReview"] for w in r.json()["weeks"]), (
        "§2.1 第 4 条强制复盘线被连坐删了 —— 它**不是熔断**(§五 ⑤-B「反向锁一条」)。"
    )
    with connection(api_env.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM circuit_breaker").fetchone()[0] == 0
