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


# —— v1.3-① 两档时间退出派生 + 费用录入 —————————————————————————————————————

def _buy_date_for_dcount(target: int):
    """回推使 d_count(buy, today)==target 的买入日(交易日历口径)。"""
    from datetime import date, timedelta

    from neckline.sentinel.positions import d_count
    today = date.today()
    d = today
    while d_count(d, today) < target:
        d -= timedelta(days=1)
    return d


def test_position_v13_fields_default_single_tier(client, AUTH, api_env):
    """K1 现役(单档):新持仓 dCount=1 → timeExitState=holding、maxHoldDaysEffective=maxHoldDays。"""
    from tests.conftest import seed_active_rule_v1
    seed_active_rule_v1(api_env)   # 无两档字段 → 单档
    client.post("/api/v1/positions", headers=AUTH, json={"code": "600001.SH", "buy_price": 10.0, "qty": 100})
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["timeExitState"] == "holding"
    assert h["maxHoldDaysEffective"] == 5 == h["maxHoldDays"]
    assert h["buyFees"] is None and h["sellFees"] is None


def test_buy_fees_roundtrip(client, AUTH, api_env):
    """POST /positions 带 buyFees → GET 回显。"""
    from tests.conftest import seed_active_rule_v1
    seed_active_rule_v1(api_env)
    client.post("/api/v1/positions", headers=AUTH,
                json={"code": "600001.SH", "buy_price": 10.0, "qty": 1000, "buyFees": 8.5})
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["buyFees"] == 8.5


def test_sell_fees_recorded_on_close(client, AUTH, api_env):
    """清仓带 sellFees → 落库(周复盘对账用真数)。"""
    from neckline.sentinel.positions import get_position
    pid = client.post("/api/v1/positions", headers=AUTH,
                      json={"code": "600001.SH", "buy_price": 10.0, "qty": 1000}).json()["position_id"]
    assert client.post(f"/api/v1/positions/{pid}/close", headers=AUTH,
                       json={"sell_price": 11.0, "sellFees": 12.3}).json()["ok"]
    assert get_position(pid, db_path=api_env.db_path).sell_fees == 12.3


# —— v1.3 两档时间退出:`GET /positions` 读**定格判向**(审计 🔴-1,用户拍板方案 A)——
# 修复前本端点用**实时价**现算净浮盈重判(刷新一次就可能翻向,且与 16:35 EOD / precall
# 三处数据源各不相同);现在只读 `holding_eod_check.time_exit_locked_state`。

def _seed_two_tier(api_env, *, dcount: int, lock_state=None, lock_nf=None, buy_price=10.0):
    """建一笔 d_count=dcount 的两档持仓;`lock_state` 非空则落一份带定格判向的 EOD 快照。"""
    from tests.conftest import seed_active_rule_v1
    from neckline.report import holding_store
    from neckline.sentinel.positions import open_position
    seed_active_rule_v1(api_env, extra_config={
        "take_profit_retrace": 0.08, "time_exit_only_if_unprofitable": True, "max_hold_days_profit": 15,
    })
    buy = _buy_date_for_dcount(dcount)
    pid = open_position("600001.SH", buy_price, 1000, buy, buy_fees=5.0, db_path=api_env.db_path)
    if lock_state is not None:
        class _Snap:
            position_id, d_count, net_float = pid, dcount, lock_nf
            time_exit_state, max_hold_effective = lock_state, (15 if lock_state == "profit_exempt" else 5)
            has_strong = scenario_review = False
            time_exit_locked_state, time_exit_locked_date = lock_state, buy.strftime("%Y%m%d")
            time_exit_locked_net_float = lock_nf
            def hits_public(self):
                return []
        holding_store.save_holding_eod_checks(buy, [_Snap()], db_path=api_env.db_path)
    return pid


def _quote(price: float):
    from neckline.sentinel.quotes import Quote
    return Quote(code="600001.SH", name="甲", price=price, pre_close=price, open=price,
                 high=price, low=price, volume=1000.0, amount=1e6, ts="", source="test")


def test_position_two_tier_profit_exempt(client, AUTH, api_env, monkeypatch):
    """两档启用 + D5 **定格**豁免 → timeExitState=profit_exempt、maxHoldDaysEffective=15。"""
    import neckline.api.app as app_mod
    _seed_two_tier(api_env, dcount=5, lock_state="profit_exempt", lock_nf=920.0)
    monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {"600001.SH": _quote(11.0)})
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["dCount"] == 5
    assert h["timeExitState"] == "profit_exempt"
    assert h["maxHoldDaysEffective"] == 15


def test_position_two_tier_time_exit_on_loss(client, AUTH, api_env, monkeypatch):
    """两档启用 + D5 **定格**非浮盈 → timeExitState=time_exit_next_day、maxHoldDaysEffective=5。"""
    import neckline.api.app as app_mod
    _seed_two_tier(api_env, dcount=5, lock_state="time_exit_next_day", lock_nf=-40.0)
    monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {"600001.SH": _quote(9.6)})
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["timeExitState"] == "time_exit_next_day"
    assert h["maxHoldDaysEffective"] == 5
    assert "时间退出日" in h["todayAction"]


def test_position_frozen_exempt_survives_price_crash(client, AUTH, api_env, monkeypatch):
    """审计 🔴-1 ①:D5 定格豁免的单子,D7 实时价跌回浮亏(未破止损)**不得**改推时间退出。"""
    import neckline.api.app as app_mod
    _seed_two_tier(api_env, dcount=7, lock_state="profit_exempt", lock_nf=920.0)
    monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {"600001.SH": _quote(9.7)})  # 浮亏但未破 -5%
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["dCount"] == 7
    assert h["timeExitState"] == "profit_exempt" and h["maxHoldDaysEffective"] == 15
    assert "浮盈豁免" in h["todayAction"]


def test_position_frozen_exit_not_laundered_by_price_rally(client, AUTH, api_env, monkeypatch):
    """审计 🔴-1 ②(反向漏洞,更重):D5 定格「该走」的单子,D7 实时价转浮盈**不得**改口豁免
    ——违纪不被系统事后合法化。"""
    import neckline.api.app as app_mod
    _seed_two_tier(api_env, dcount=7, lock_state="time_exit_next_day", lock_nf=-40.0)
    monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {"600001.SH": _quote(12.0)})  # 大幅转浮盈
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["timeExitState"] == "time_exit_next_day" and h["maxHoldDaysEffective"] == 5
    assert "按计划离场" in h["todayAction"]


def test_position_hard_cap_still_by_dcount(client, AUTH, api_env, monkeypatch):
    """审计 🔴-1 ③:D15 硬上限仍按 d_count 判(定格豁免挡不住硬上限)。"""
    import neckline.api.app as app_mod
    _seed_two_tier(api_env, dcount=15, lock_state="profit_exempt", lock_nf=920.0)
    monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {"600001.SH": _quote(12.0)})
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["timeExitState"] == "hard_cap_exit" and h["maxHoldDaysEffective"] == 15


def test_position_two_tier_no_snapshot_is_conservative(client, AUTH, api_env, monkeypatch):
    """尚无定格快照(EOD 管线断跑)→ 保守判 time_exit_next_day,绝不因实时价浮盈默认豁免。"""
    import neckline.api.app as app_mod
    _seed_two_tier(api_env, dcount=6, lock_state=None)
    monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {"600001.SH": _quote(13.0)})
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["timeExitState"] == "time_exit_next_day"


# —— v1.2-E.5 一键补录预填**区间**(v1.1-B.3 单 qty 已被替换)——————————————

def test_entry_suggestion_returns_range_not_single_qty(client, AUTH):
    """两档手数各自向下取整;上限档 = single_cap(违纪判定上限,20000 兜底默认),
    下限档 = single_cap × `_ENTRY_SUGGESTION_FLOOR_FRAC`;stopLine 读 config。
    **不再返单个 `qty`**——v1.2 章程起 single_cap 是违纪上限而非推荐值,系统不替
    用户拍板单笔金额(§2.1 第 3 条)。"""
    r = client.get("/api/v1/positions/entry-suggestion", headers=AUTH, params={"code": "600001.SH", "price": 50.0}).json()
    assert r["qtyHigh"] == 400            # floor(20000/50/100)*100
    assert r["qtyLow"] == 200             # floor(10000/50/100)*100
    assert r["capCeil"] == 20000.0 and r["capFloor"] == 10000.0
    assert r["stopLine"] == 47.5          # 50×0.95
    assert r["code"] == "600001.SH" and r["price"] == 50.0
    assert "qty" not in r                 # 旧单值字段已移除,客户端不得再依赖


def test_entry_suggestion_floor_frac_matches_named_constant(client, AUTH):
    """下限档金额随命名常量走,不是散落的 0.5 字面量(单一处可调,不影响纪律判定)。"""
    from neckline.api.app import _ENTRY_SUGGESTION_FLOOR_FRAC
    r = client.get("/api/v1/positions/entry-suggestion", headers=AUTH, params={"price": 50.0}).json()
    assert r["capFloor"] == pytest.approx(r["capCeil"] * _ENTRY_SUGGESTION_FLOOR_FRAC)


def test_entry_suggestion_high_price_zero_lots(client, AUTH):
    """现价过高、连下限档都买不起一手 → 两档均 0(不虚报)。"""
    r = client.get("/api/v1/positions/entry-suggestion", headers=AUTH, params={"price": 1500.0}).json()
    assert r["qtyHigh"] == 0 and r["qtyLow"] == 0     # floor(20000/1500/100)=0

    # 上限档买得起、下限档买不起 → 只有下限档为 0,上限档如实给(不把区间硬凑成两档相等)
    r1 = client.get("/api/v1/positions/entry-suggestion", headers=AUTH, params={"price": 150.0}).json()
    assert r1["qtyHigh"] == 100 and r1["qtyLow"] == 0

    r0 = client.get("/api/v1/positions/entry-suggestion", headers=AUTH, params={"price": 0}).json()
    assert r0["qtyLow"] == 0 and r0["qtyHigh"] == 0 and r0["stopLine"] == 0.0   # price≤0 防除零
