"""4A.4 持仓端点单测(plan 4A 验收:开清仓走台账;派生止损线 = buy×0.95)。"""

from __future__ import annotations


def test_open_list_close_roundtrip(client, AUTH):
    o = client.post("/api/v1/positions", headers=AUTH, json={
        "code": "600519.SH", "name": "贵州茅台", "buy_price": 1500.0, "qty": 100, "entry_reason": "回调低吸",
    }).json()
    assert o["ok"] is True and o["position_id"] >= 1
    assert o["stop_line"] == 1425.0                      # 1500×0.95(§2.1 -5% 单一常量)

    lst = client.get("/api/v1/positions", headers=AUTH).json()["holdings"]
    assert len(lst) == 1
    h = lst[0]
    assert h["code"] == "600519.SH"
    assert h["buyPrice"] == 1500.0 and h["qty"] == 100
    assert h["entryReason"] == "回调低吸"
    assert h["stopLine"] == 1425.0
    assert h["price"] == 0.0                             # _QUOTES_FN 置空 → 无实时价,兜底 0
    assert h["stopOrderChecked"] is False

    pid = o["position_id"]
    assert client.post(f"/api/v1/positions/{pid}/close", headers=AUTH, json={"sell_price": 1520.0}).json()["ok"]
    assert client.get("/api/v1/positions", headers=AUTH).json()["holdings"] == []


def test_close_nonexistent_404(client, AUTH):
    r = client.post("/api/v1/positions/999/close", headers=AUTH, json={"sell_price": 1.0})
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "not_holding"


def test_close_twice_404(client, AUTH):
    pid = client.post("/api/v1/positions", headers=AUTH, json={"code": "600001.SH", "buy_price": 10.0, "qty": 100}).json()["position_id"]
    assert client.post(f"/api/v1/positions/{pid}/close", headers=AUTH, json={"sell_price": 11.0}).json()["ok"]
    assert client.post(f"/api/v1/positions/{pid}/close", headers=AUTH, json={"sell_price": 11.0}).status_code == 404


def test_price_injected_from_quotes(client, AUTH, monkeypatch):
    """持仓 price 由一拍实时价填(可注入 `_QUOTES_FN`)。"""
    import neckline.api.app as app_mod
    from neckline.sentinel.quotes import Quote

    q = Quote(code="600001.SH", name="甲", price=12.34, pre_close=12.0, open=12.0, high=12.5,
              low=11.9, volume=1000.0, amount=1_000_000.0, ts="", source="test")
    monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {"600001.SH": q})

    client.post("/api/v1/positions", headers=AUTH, json={"code": "600001.SH", "buy_price": 10.0, "qty": 100})
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["price"] == 12.34


def test_entry_reason_defaults_empty(client, AUTH):
    pid = client.post("/api/v1/positions", headers=AUTH, json={"code": "600001.SH", "buy_price": 10.0, "qty": 100}).json()["position_id"]
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["id"] == pid
    assert h["entryReason"] == ""
