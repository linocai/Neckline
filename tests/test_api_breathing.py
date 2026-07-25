"""v1.2-G 呼吸试验仓台账端点单测(plan §五 v1.2-G 验收②/③,契约清单逐字段核对)。

覆盖:`GET/POST /breathing/{position_id}/trades` 契约形状(camelCase 字段、`tPnl`
派生、`baseCostAdj`/`edgeToPrice`)、底仓不存在 404、无实时价时 `edgeToPrice` 为
null 不崩、有实时价时正确算出、`DELETE /breathing/trades/{id}` 幂等 + 404、鉴权、
审计件非下单件(不触发任何 positions 写入)。
"""

from __future__ import annotations

import pytest


def _open_position(client, AUTH, code="600001.SH", buy=10.0, qty=1000):
    r = client.post("/api/v1/positions", headers=AUTH, json={
        "code": code, "buy_price": buy, "qty": qty,
    })
    return r.json()["position_id"]


# —— 契约形状 + 基本往返 ——————————————————————————————————————————————————

def test_post_and_get_roundtrip_contract_shape(client, AUTH):
    pid = _open_position(client, AUTH)
    r = client.post(f"/api/v1/breathing/{pid}/trades", headers=AUTH, json={
        "buyPrice": 10.0, "sellPrice": 10.3, "qty": 500, "fees": 20.0,
        "tDate": "20260702", "note": "日内回踩低吸",
    })
    assert r.status_code == 200
    out = r.json()
    assert out["id"] >= 1
    assert out["positionId"] == pid
    assert out["buyPrice"] == 10.0 and out["sellPrice"] == 10.3
    assert out["qty"] == 500 and out["fees"] == 20.0
    assert out["tDate"] == "20260702"
    assert out["note"] == "日内回踩低吸"
    assert out["tPnl"] == pytest.approx((10.3 - 10.0) * 500 - 20.0)

    g = client.get(f"/api/v1/breathing/{pid}/trades", headers=AUTH)
    assert g.status_code == 200
    body = g.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == out["id"]
    assert "baseCostAdj" in body and "edgeToPrice" in body


def test_note_defaults_empty_string_not_null(client, AUTH):
    pid = _open_position(client, AUTH)
    r = client.post(f"/api/v1/breathing/{pid}/trades", headers=AUTH, json={
        "buyPrice": 10.0, "sellPrice": 10.1, "qty": 100, "fees": 5.0,
    })
    assert r.json()["note"] == ""


def test_t_date_optional_defaults_to_today(client, AUTH):
    from datetime import date
    pid = _open_position(client, AUTH)
    r = client.post(f"/api/v1/breathing/{pid}/trades", headers=AUTH, json={
        "buyPrice": 10.0, "sellPrice": 10.1, "qty": 100, "fees": 5.0,
    })
    assert r.json()["tDate"] == date.today().strftime("%Y%m%d")


def test_fees_required_field_missing_422(client, AUTH):
    """契约:`fees` 无 `?`,必填(不像 `tDate`/`note` 可省)。"""
    pid = _open_position(client, AUTH)
    r = client.post(f"/api/v1/breathing/{pid}/trades", headers=AUTH, json={
        "buyPrice": 10.0, "sellPrice": 10.1, "qty": 100,
    })
    assert r.status_code == 422


def test_multiple_trades_list_ordered(client, AUTH):
    pid = _open_position(client, AUTH)
    client.post(f"/api/v1/breathing/{pid}/trades", headers=AUTH, json={
        "buyPrice": 10.0, "sellPrice": 10.1, "qty": 100, "fees": 5.0, "tDate": "20260705",
    })
    client.post(f"/api/v1/breathing/{pid}/trades", headers=AUTH, json={
        "buyPrice": 10.0, "sellPrice": 10.2, "qty": 100, "fees": 5.0, "tDate": "20260702",
    })
    items = client.get(f"/api/v1/breathing/{pid}/trades", headers=AUTH).json()["items"]
    assert [i["tDate"] for i in items] == ["20260702", "20260705"]


# —— 底仓不存在 → 404 ——————————————————————————————————————————————————————

def test_get_nonexistent_position_404(client, AUTH):
    r = client.get("/api/v1/breathing/999999/trades", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "not_found"


def test_post_nonexistent_position_404(client, AUTH):
    r = client.post("/api/v1/breathing/999999/trades", headers=AUTH, json={
        "buyPrice": 10.0, "sellPrice": 10.1, "qty": 100, "fees": 5.0,
    })
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "not_found"


# —— baseCostAdj / edgeToPrice 派生(无价 → null,有价 → 算出)—————————————————

def test_base_cost_adj_and_edge_without_live_price(client, AUTH):
    """`_QUOTES_FN` 默认置空(见 `api_env` 夹具)→ 无实时价 → edgeToPrice null,
    但 baseCostAdj 不依赖现价,仍应算出。"""
    pid = _open_position(client, AUTH, buy=10.0, qty=1000)
    client.post(f"/api/v1/breathing/{pid}/trades", headers=AUTH, json={
        "buyPrice": 10.0, "sellPrice": 10.3, "qty": 1000, "fees": 100.0, "tDate": "20260702",
    })
    body = client.get(f"/api/v1/breathing/{pid}/trades", headers=AUTH).json()
    # t_pnl = (10.3-10.0)*1000-100 = 200; base_cost_adj = 10.0 - 200/1000 = 9.8
    assert body["baseCostAdj"] == pytest.approx(9.8)
    assert body["edgeToPrice"] is None


def test_edge_to_price_computed_with_live_price(client, AUTH, monkeypatch):
    import neckline.api.app as app_mod
    from neckline.sentinel.quotes import Quote

    pid = _open_position(client, AUTH, code="600001.SH", buy=10.0, qty=1000)
    client.post(f"/api/v1/breathing/{pid}/trades", headers=AUTH, json={
        "buyPrice": 10.0, "sellPrice": 10.3, "qty": 1000, "fees": 100.0, "tDate": "20260702",
    })
    # baseCostAdj = 9.8(同上一测试)
    q = Quote(code="600001.SH", name="甲", price=10.0, pre_close=10.0, open=10.0, high=10.1,
              low=9.9, volume=1000.0, amount=1_000_000.0, ts="", source="test")
    monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {"600001.SH": q})

    body = client.get(f"/api/v1/breathing/{pid}/trades", headers=AUTH).json()
    assert body["baseCostAdj"] == pytest.approx(9.8)
    # edgeToPrice = (10.0-9.8)/10.0 = 0.02
    assert body["edgeToPrice"] == pytest.approx(0.02)


def test_base_cost_adj_no_trades_equals_buy_price(client, AUTH):
    pid = _open_position(client, AUTH, buy=15.5, qty=500)
    body = client.get(f"/api/v1/breathing/{pid}/trades", headers=AUTH).json()
    assert body["items"] == []
    assert body["baseCostAdj"] == pytest.approx(15.5)
    assert body["edgeToPrice"] is None


# —— DELETE:误录可删 + 幂等 + 404 ——————————————————————————————————————————

def test_delete_trade_removes_it(client, AUTH):
    pid = _open_position(client, AUTH)
    created = client.post(f"/api/v1/breathing/{pid}/trades", headers=AUTH, json={
        "buyPrice": 10.0, "sellPrice": 10.1, "qty": 100, "fees": 5.0,
    }).json()
    r = client.delete(f"/api/v1/breathing/trades/{created['id']}", headers=AUTH)
    assert r.status_code == 200 and r.json()["ok"] is True

    items = client.get(f"/api/v1/breathing/{pid}/trades", headers=AUTH).json()["items"]
    assert items == []


def test_delete_nonexistent_404(client, AUTH):
    r = client.delete("/api/v1/breathing/trades/999999", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "not_found"


def test_delete_twice_second_is_404(client, AUTH):
    pid = _open_position(client, AUTH)
    created = client.post(f"/api/v1/breathing/{pid}/trades", headers=AUTH, json={
        "buyPrice": 10.0, "sellPrice": 10.1, "qty": 100, "fees": 5.0,
    }).json()
    assert client.delete(f"/api/v1/breathing/trades/{created['id']}", headers=AUTH).status_code == 200
    assert client.delete(f"/api/v1/breathing/trades/{created['id']}", headers=AUTH).status_code == 404


# —— 鉴权 ————————————————————————————————————————————————————————————————

def test_requires_auth(client):
    """三端点均挂 `require_token`(§3.8 全端点鉴权);无 token → 401,且鉴权在路径
    处理函数之前拦下(即便 position_id 不存在也报 401,不泄漏 404 与否)。"""
    assert client.get("/api/v1/breathing/1/trades").status_code == 401
    assert client.post("/api/v1/breathing/1/trades", json={
        "buyPrice": 1.0, "sellPrice": 1.0, "qty": 1, "fees": 0.0,
    }).status_code == 401
    assert client.delete("/api/v1/breathing/trades/1").status_code == 401


# —— 审计件、非下单件(硬约束验证)———————————————————————————————————————————

def test_add_trade_does_not_open_or_close_positions(client, AUTH):
    """录入 T 子账绝不触发任何持仓写入(§3.8 铁律)——底仓台账数量/字段不变。"""
    pid = _open_position(client, AUTH, buy=10.0, qty=1000)
    before = client.get("/api/v1/positions", headers=AUTH).json()["holdings"]
    client.post(f"/api/v1/breathing/{pid}/trades", headers=AUTH, json={
        "buyPrice": 10.0, "sellPrice": 10.3, "qty": 500, "fees": 20.0,
    })
    after = client.get("/api/v1/positions", headers=AUTH).json()["holdings"]
    assert before == after
