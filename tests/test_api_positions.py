"""4A.4 持仓端点单测(plan 4A 验收:开清仓走台账;派生止损线读现役 config)
+ v1.1-B.1/B.3 持仓生命周期派生字段 + 一键补录预填。"""

from __future__ import annotations

import pytest


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


# —— v1.1-B.1 持仓生命周期派生字段 ——————————————————————————————————————

def test_position_lifecycle_fields_present(client, AUTH, api_env):
    """GET /positions 带 dCount / maxHoldDays / distToStopPct / retraceState / todayAction。
    maxHoldDays 读现役 config(seed rule v1 = 5),不硬编。"""
    from tests.conftest import seed_active_rule_v1
    seed_active_rule_v1(api_env)   # max_hold_days=5, stop_pct=0.05

    client.post("/api/v1/positions", headers=AUTH, json={"code": "600001.SH", "buy_price": 10.0, "qty": 100})
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["maxHoldDays"] == 5
    assert isinstance(h["dCount"], int) and h["dCount"] >= 0
    assert h["stopLine"] == 9.5                      # 10×(1−0.05),读 config 非硬编 0.95
    assert h["distToStopPct"] is None                # _QUOTES_FN 空 → 无实时价 → null
    assert h["retraceState"] is None
    assert isinstance(h["todayAction"], str) and h["todayAction"]


def test_position_derived_with_live_price(client, AUTH, api_env, monkeypatch):
    """有实时价时 distToStopPct / retraceState 算出;price 高于买入且未回落 → 未触发。"""
    import neckline.api.app as app_mod
    from tests.conftest import seed_active_rule_v1
    from neckline.sentinel.quotes import Quote
    seed_active_rule_v1(api_env)

    q = Quote(code="600001.SH", name="甲", price=11.0, pre_close=10.0, open=10.0, high=11.2,
              low=9.9, volume=1000.0, amount=1_000_000.0, ts="", source="test")
    monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {"600001.SH": q})
    client.post("/api/v1/positions", headers=AUTH, json={"code": "600001.SH", "buy_price": 10.0, "qty": 100})
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    # stopLine=9.5,price=11.0 → dist=(11-9.5)/11≈0.1364
    assert h["distToStopPct"] == pytest.approx((11.0 - 9.5) / 11.0, abs=1e-4)
    assert h["retraceState"] is not None
    assert h["retraceState"]["triggered"] is False   # 峰值 11 未回落 5%


# —— v1.1-B.3 一键补录预填推荐 ————————————————————————————————————————

def test_entry_suggestion_rounds_to_lots(client, AUTH):
    """qty = floor(single_cap/price/100)*100(single_cap=20000 兜底默认);stopLine 读 config。"""
    r = client.get("/api/v1/positions/entry-suggestion", headers=AUTH, params={"code": "600001.SH", "price": 50.0}).json()
    assert r["qty"] == 400                # floor(20000/50/100)*100 = 400
    assert r["stopLine"] == 47.5          # 50×0.95
    assert r["code"] == "600001.SH" and r["price"] == 50.0


def test_entry_suggestion_high_price_zero_lots(client, AUTH):
    """现价过高、单笔上限买不起一手 → qty=0(不虚报)。"""
    r = client.get("/api/v1/positions/entry-suggestion", headers=AUTH, params={"price": 1500.0}).json()
    assert r["qty"] == 0                  # floor(20000/1500/100)=0

    r0 = client.get("/api/v1/positions/entry-suggestion", headers=AUTH, params={"price": 0}).json()
    assert r0["qty"] == 0 and r0["stopLine"] == 0.0   # price≤0 防除零
