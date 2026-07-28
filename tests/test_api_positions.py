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

def _seed_two_tier(api_env, *, dcount: int, lock_state=None, lock_nf=None, buy_price=10.0,
                   lock_dcount=None):
    """建一笔 d_count=dcount 的两档持仓;`lock_state` 非空则落一份带定格判向的 EOD 快照。

    `lock_dcount`(v1.4-⑥-C):定格发生在 D 几。缺省沿用旧行为(定格日 = 买入日,
    即 D1);传值则把 `time_exit_locked_date` 落在「买入日 + (lock_dcount−1) 个交易日」,
    用来造「定格于 D7、晚于 D5 两天」这类断跑场景。"""
    from tests.conftest import seed_active_rule_v1
    from neckline.calendar import trading_days_between
    from neckline.report import holding_store
    from neckline.sentinel.positions import open_position
    seed_active_rule_v1(api_env, extra_config={
        "take_profit_retrace": 0.08, "time_exit_only_if_unprofitable": True, "max_hold_days_profit": 15,
    })
    buy = _buy_date_for_dcount(dcount)
    pid = open_position("600001.SH", buy_price, 1000, buy, buy_fees=5.0, db_path=api_env.db_path)
    if lock_state is not None:
        from datetime import date as _date
        lock_day = buy
        if lock_dcount is not None:
            days = trading_days_between(buy, _date.today())
            lock_day = days[min(lock_dcount, len(days)) - 1]
        class _Snap:
            position_id, d_count, net_float = pid, dcount, lock_nf
            time_exit_state, max_hold_effective = lock_state, (15 if lock_state == "profit_exempt" else 5)
            has_strong = scenario_review = False
            time_exit_locked_state, time_exit_locked_date = lock_state, lock_day.strftime("%Y%m%d")
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


# —— v1.4-①-A 补录真实买入日(§七 P0-1,🔴 碰持仓判定)——————————————————————

def _seed_cal_around_today(api_env):
    """在隔离库铺一段稠密 trade_cal:今天 ± 10 个自然日,其中**工作日 = 交易日**。
    刻意含今天(默认路径要走得通)与至少一个非交易日(周末)。"""
    from datetime import date, timedelta

    from tests.conftest import insert_trade_cal

    today = date.today()
    days = [today + timedelta(days=i) for i in range(-10, 11)]
    insert_trade_cal(api_env, [d for d in days if d.weekday() < 5],
                     range_start=days[0], range_end=days[-1])
    return today


def _recent_trading_day(today, back: int = 1):
    """今天往前数第 `back` 个工作日(与 `_seed_cal_around_today` 的口径一致)。"""
    from datetime import timedelta

    d, seen = today, 0
    while seen < back:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            seen += 1
    return d


def test_buy_date_omitted_defaults_to_today(client, AUTH, api_env):
    """**老客户端不传 buyDate → 行为与 v1.4 之前逐位一致**(buy_date=今天)。
    这是 ①-A 的向后兼容红线,先锁死它再谈新能力。"""
    from datetime import date

    _seed_cal_around_today(api_env)
    client.post("/api/v1/positions", headers=AUTH,
                json={"code": "600001.SH", "buy_price": 10.0, "qty": 100})
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["buyDate"] == date.today().strftime("%Y%m%d")


def test_buy_date_historical_trading_day_written_through(client, AUTH, api_env):
    """传历史交易日 → 原样落库,且 dCount 按真实买入日算(≥2,不是刚开仓的 1)。"""
    today = _seed_cal_around_today(api_env)
    target = _recent_trading_day(today, back=3)

    client.post("/api/v1/positions", headers=AUTH, json={
        "code": "600001.SH", "buy_price": 10.0, "qty": 100,
        "buyDate": target.strftime("%Y%m%d"),
    })
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["buyDate"] == target.strftime("%Y%m%d")
    # D 计数是本条 P0 的核心受益者:买入日对了,D 才对(闭区间交易日数 = 4)。
    assert h["dCount"] == 4


def test_buy_date_non_trading_day_400_not_trading_day(client, AUTH, api_env):
    """非交易日(周末)→ 400 + reason=not_trading_day,**且一笔都不落库**。"""
    from datetime import timedelta

    today = _seed_cal_around_today(api_env)
    weekend = today - timedelta(days=1)
    while weekend.weekday() < 5:          # 往回找最近的周末日(必在铺好的日历窗口内)
        weekend -= timedelta(days=1)

    r = client.post("/api/v1/positions", headers=AUTH, json={
        "code": "600001.SH", "buy_price": 10.0, "qty": 100,
        "buyDate": weekend.strftime("%Y%m%d"),
    })
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "not_trading_day"
    assert client.get("/api/v1/positions", headers=AUTH).json()["holdings"] == []


def test_buy_date_future_400_future_buy_date(client, AUTH, api_env):
    """未来日 → 400 + reason=future_buy_date。**即便那天也是交易日**——校验顺序必须
    「先判未来、再判交易日」,否则 reason 会说谎(明天多半正好是交易日)。"""
    from datetime import timedelta

    today = _seed_cal_around_today(api_env)
    future = today + timedelta(days=1)
    while future.weekday() >= 5:          # 取一个确实是交易日的未来日
        future += timedelta(days=1)

    r = client.post("/api/v1/positions", headers=AUTH, json={
        "code": "600001.SH", "buy_price": 10.0, "qty": 100,
        "buyDate": future.strftime("%Y%m%d"),
    })
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "future_buy_date"
    assert client.get("/api/v1/positions", headers=AUTH).json()["holdings"] == []


def test_buy_date_malformed_400_not_silently_today(client, AUTH, api_env):
    """格式非法 → 400(**不静默吞成今天**):「没给」与「给错了」必须能分开(§3.8)。"""
    _seed_cal_around_today(api_env)
    for bad in ("2026-07-22", "20260732", "abc", "202607"):
        r = client.post("/api/v1/positions", headers=AUTH, json={
            "code": "600001.SH", "buy_price": 10.0, "qty": 100, "buyDate": bad,
        })
        assert r.status_code == 400, bad
        assert r.json()["detail"]["reason"] == "not_trading_day", bad
    assert client.get("/api/v1/positions", headers=AUTH).json()["holdings"] == []


def test_buy_date_empty_string_treated_as_omitted(client, AUTH, api_env):
    """空串 = 没填(客户端表单常见形态)→ 走缺省今天,不报 400。"""
    from datetime import date

    _seed_cal_around_today(api_env)
    r = client.post("/api/v1/positions", headers=AUTH, json={
        "code": "600001.SH", "buy_price": 10.0, "qty": 100, "buyDate": "",
    })
    assert r.status_code == 200
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["buyDate"] == date.today().strftime("%Y%m%d")


def test_buy_date_reason_codes_are_named_constants(client, AUTH):
    """两个 reason 字面量住命名常量(客户端 mapReason 的 case 与之对齐,不各写一份)。"""
    from neckline.api.app import REASON_FUTURE_BUY_DATE, REASON_NOT_TRADING_DAY

    assert REASON_NOT_TRADING_DAY == "not_trading_day"
    assert REASON_FUTURE_BUY_DATE == "future_buy_date"


# —— v1.4-①-B 停牌 / 无当日 EOD 行的持仓票在 GET /positions 的显式标注(§七 P0-2)————

def _seed_market_with_gap(api_env, *, gap_days: int = 3, suspend_list=("002036.SZ",)):
    """铺 10 个交易日全市场行情:`600001.SH` 全程有行,`002036.SZ` 最后 `gap_days` 天缺行。
    `suspend_list` = 最后一天落盘的停牌名单(`None` = 该表压根没落盘 → reason 应为 unknown;
    空/不含该票 = 落了但它不在名单里 → reason 应为 data_gap)。返回交易日列表。"""
    from datetime import date

    import polars as pl

    from neckline.data.market_data import write_table_day
    from tests.conftest import business_days, insert_trade_cal, write_daily_fixture

    days = business_days(date(2026, 7, 6), 10)
    insert_trade_cal(api_env, days)
    for i, d in enumerate(days):
        rows = [{"ts_code": "600001.SH", "close": 10.0, "open": 10.0, "high": 10.0,
                 "low": 10.0, "pre_close": 10.0, "vol": 1.0, "amount": 1.0}]
        if i < len(days) - gap_days:
            rows.append({"ts_code": "002036.SZ", "close": 7.2, "open": 7.2, "high": 7.2,
                         "low": 7.2, "pre_close": 7.2, "vol": 1.0, "amount": 1.0})
        write_daily_fixture(api_env, "daily", d, rows)
    if suspend_list is not None:
        codes = list(suspend_list) or ["999999.SZ"]   # 名单落了盘,但不含被测票
        write_table_day("suspend_d", days[-1], pl.DataFrame({
            "ts_code": codes, "trade_date": [days[-1]] * len(codes),
            "suspend_type": ["S"] * len(codes),
        }), parquet_dir=api_env.parquet_dir)
    return days


def test_price_stale_absent_for_fresh_position(client, AUTH, api_env, monkeypatch):
    """当日有 EOD 行的正常票 → `priceStale` 为 null(正常票不背这个字段的负担)。"""
    import neckline.api.app as app_mod

    days = _seed_market_with_gap(api_env)
    monkeypatch.setattr(app_mod, "_resolve_price_stale",
                        lambda codes: _real_stale(api_env, codes, days[-1]))
    client.post("/api/v1/positions", headers=AUTH,
                json={"code": "600001.SH", "buy_price": 10.0, "qty": 100})
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["priceStale"] is None


def _real_stale(api_env, codes, as_of):
    """把「今天」钉到合成行情的最后一个交易日(测试里 date.today() 不在合成窗口内)。"""
    from neckline.data.price_stale import resolve_price_stale

    return resolve_price_stale(codes, as_of, api_env.parquet_dir)


def test_price_stale_reports_days_last_close_and_reason(client, AUTH, api_env, monkeypatch):
    """停牌票 → 三字段齐备且 reason=suspended(**绝不静默把老价当今日价**)。"""
    import neckline.api.app as app_mod

    days = _seed_market_with_gap(api_env, gap_days=3)
    monkeypatch.setattr(app_mod, "_resolve_price_stale",
                        lambda codes: _real_stale(api_env, codes, days[-1]))
    client.post("/api/v1/positions", headers=AUTH,
                json={"code": "002036.SZ", "buy_price": 7.184, "qty": 3000})
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["priceStale"] == {
        "staleDays": 3,
        "lastCloseDate": days[-4].strftime("%Y%m%d"),
        "reason": "suspended",
    }


def test_price_stale_reason_data_gap_vs_unknown(client, AUTH, api_env, monkeypatch):
    """缺行但**不在**停牌名单 → data_gap;名单**压根没落盘** → unknown。两者不可混同
    (「没有」与「没看」必须能分开,§3.8)。"""
    import neckline.api.app as app_mod

    days = _seed_market_with_gap(api_env, gap_days=2, suspend_list=[])
    monkeypatch.setattr(app_mod, "_resolve_price_stale",
                        lambda codes: _real_stale(api_env, codes, days[-1]))
    client.post("/api/v1/positions", headers=AUTH,
                json={"code": "002036.SZ", "buy_price": 7.184, "qty": 3000})
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["priceStale"]["reason"] == "data_gap"

    (api_env.parquet_dir / "suspend_d").rename(api_env.parquet_dir / "suspend_d_off")
    h2 = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h2["priceStale"]["reason"] == "unknown"


def test_suspended_hold_state_and_action_text(client, AUTH, api_env, monkeypatch):
    """到判定点 + 无定格 + 当日无 EOD 行 → timeExitState=suspended_hold,
    `todayAction` 说的是「判向挂起」而不是「按计划离场」(P0-2 的病根就是那句离场)。"""
    import neckline.api.app as app_mod
    from neckline.data.price_stale import PriceStale
    from tests.conftest import seed_active_rule_v1

    seed_active_rule_v1(api_env, {"time_exit_only_if_unprofitable": True, "max_hold_days_profit": 15})
    today = _seed_cal_around_today(api_env)
    monkeypatch.setattr(app_mod, "_resolve_price_stale", lambda codes: {
        "002036.SZ": PriceStale(stale_days=4, last_close_date="20260722", reason="suspended"),
    })
    # 买入日往前推 6 个交易日 → dCount ≥ 5(到判定点)
    client.post("/api/v1/positions", headers=AUTH, json={
        "code": "002036.SZ", "buy_price": 7.184, "qty": 3000,
        "buyDate": _recent_trading_day(today, back=6).strftime("%Y%m%d"),
    })
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["dCount"] >= 5                    # D 计数照常累计并展示
    assert h["timeExitState"] == "suspended_hold"
    assert "挂起" in h["todayAction"] and "离场" not in h["todayAction"]


def test_k4_data_unavailable_null_when_no_snapshot(client, AUTH, api_env):
    """刚开仓未体检 → k4DataUnavailable 为 null(没有快照 = 不知道,不冒充 false)。"""
    _seed_cal_around_today(api_env)
    client.post("/api/v1/positions", headers=AUTH,
                json={"code": "600001.SH", "buy_price": 10.0, "qty": 100})
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["k4DataUnavailable"] is None


def test_k4_data_unavailable_true_from_snapshot(client, AUTH, api_env):
    """16:35 体检记了「当日无 EOD 行」→ 客户端拿到 true(空 k4Advisory 不再等于「没问题」)。"""
    from datetime import date

    from neckline.report import holding_store

    _seed_cal_around_today(api_env)
    pid = client.post("/api/v1/positions", headers=AUTH, json={
        "code": "002036.SZ", "buy_price": 7.184, "qty": 3000,
    }).json()["position_id"]

    class _It:
        position_id, d_count, net_float = pid, 5, None
        time_exit_state, max_hold_effective = "suspended_hold", 5
        has_strong = scenario_review = False
        has_data = False
        time_exit_locked_state = time_exit_locked_date = time_exit_locked_net_float = None

        def hits_public(self):
            return []

    holding_store.save_holding_eod_checks(date.today(), [_It()], db_path=api_env.db_path)
    h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
    assert h["k4DataUnavailable"] is True
    assert h["k4Advisory"] == []


# ======================================================================
#  v1.4-⑥-C(§七 P1-6):定格日 ≠ D5 的显式标注
#  ⛔ **只提示,不改判定逻辑** —— 定格语义是审计 🔴-1 的结论,不得回退。
# ======================================================================

def _holdings(client, AUTH):
    return client.get("/api/v1/positions", headers=AUTH).json()["holdings"]


def test_locked_day_and_late_days_when_pipeline_lagged(client, AUTH, api_env, monkeypatch):
    """EOD 管线断跑 → 定格发生在 D7(而不是 D5):`timeExitLockedDay=7`、
    `timeExitLockedLateDays=2`(= 7 − maxHoldDays 5)。"""
    import neckline.api.app as app_mod
    _seed_two_tier(api_env, dcount=8, lock_state="time_exit_next_day", lock_nf=-40.0, lock_dcount=7)
    monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {"600001.SH": _quote(9.6)})
    h = _holdings(client, AUTH)[0]
    assert h["timeExitLockedDay"] == 7
    assert h["timeExitLockedLateDays"] == 2
    assert h["maxHoldDays"] == 5


def test_locked_on_time_reports_zero_late_days(client, AUTH, api_env, monkeypatch):
    """准时在 D5 定格 → lateDays=0(客户端 >0 才展示,故正常单子不背这句提示)。"""
    import neckline.api.app as app_mod
    _seed_two_tier(api_env, dcount=6, lock_state="profit_exempt", lock_nf=920.0, lock_dcount=5)
    monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {"600001.SH": _quote(11.0)})
    h = _holdings(client, AUTH)[0]
    assert h["timeExitLockedDay"] == 5
    assert h["timeExitLockedLateDays"] == 0


def test_not_locked_yet_reports_null_not_fake_today(client, AUTH, api_env, monkeypatch):
    """尚未定格 → `timeExitLockedDay=null`(**不拿今天冒充定格日**,那会编出一个从没
    发生过的"准时定格");`lateDays` 退 0。判向仍保守判 time_exit_next_day,一字不改。"""
    import neckline.api.app as app_mod
    _seed_two_tier(api_env, dcount=6, lock_state=None)
    monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {"600001.SH": _quote(13.0)})
    h = _holdings(client, AUTH)[0]
    assert h["timeExitLockedDay"] is None
    assert h["timeExitLockedLateDays"] == 0
    assert h["timeExitState"] == "time_exit_next_day"


def test_fresh_position_defaults(client, AUTH, api_env):
    """刚开仓(单档 K1、无任何快照)→ 两个字段取缺省,老客户端不传/不认也不崩。"""
    from tests.conftest import seed_active_rule_v1
    seed_active_rule_v1(api_env)
    client.post("/api/v1/positions", headers=AUTH, json={
        "code": "600001.SH", "buy_price": 10.0, "qty": 1000,
    })
    h = _holdings(client, AUTH)[0]
    assert h["timeExitLockedDay"] is None and h["timeExitLockedLateDays"] == 0


def test_annotation_does_not_change_any_verdict(client, AUTH, api_env, monkeypatch):
    """**判向逐位不变**(⑥-C 验收):同一批场景下,加了标注之后 `timeExitState` /
    `maxHoldDaysEffective` / `todayAction` 与 ⑥-C 之前的期望完全一致 —— 标注是纯派生
    展示位,不参与任何判定。"""
    import neckline.api.app as app_mod

    cases = [
        # (dcount, lock_state, lock_nf, lock_dcount, price, 期望 state, 期望 eff_max)
        (5, "profit_exempt", 920.0, 5, 11.0, "profit_exempt", 15),
        (7, "profit_exempt", 920.0, 7, 9.7, "profit_exempt", 15),      # 晚定格也不改判向
        (7, "time_exit_next_day", -40.0, 7, 12.0, "time_exit_next_day", 5),
        (15, "profit_exempt", 920.0, 9, 12.0, "hard_cap_exit", 15),    # 硬上限仍按 d_count
    ]
    for dcount, lock_state, nf, lock_d, price, want_state, want_eff in cases:
        from neckline.db import connection
        with connection(api_env.db_path) as conn:      # 每轮清干净,单笔持仓单独判
            conn.execute("DELETE FROM positions")
            conn.execute("DELETE FROM holding_eod_check")
        _seed_two_tier(api_env, dcount=dcount, lock_state=lock_state, lock_nf=nf, lock_dcount=lock_d)
        monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes, p=price: {"600001.SH": _quote(p)})
        h = _holdings(client, AUTH)[0]
        assert (h["timeExitState"], h["maxHoldDaysEffective"]) == (want_state, want_eff), (dcount, lock_state)
        assert h["timeExitLockedDay"] == lock_d       # 标注如实,但没影响上面两项


def test_suspended_hold_can_lock_late_after_resumption(client, AUTH, api_env, monkeypatch):
    """①-B 停牌票复牌后**晚定格**是常态路径,与本标注共存:复牌当日(D8)定格 →
    判向读定格值(不再挂起),同时标注"晚于 D5 三天"。"""
    import neckline.api.app as app_mod
    _seed_two_tier(api_env, dcount=8, lock_state="time_exit_next_day", lock_nf=-40.0, lock_dcount=8)
    monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {"600001.SH": _quote(9.6)})
    h = _holdings(client, AUTH)[0]
    assert h["timeExitState"] == "time_exit_next_day"   # 已定格 → 不是 suspended_hold
    assert h["timeExitLockedDay"] == 8 and h["timeExitLockedLateDays"] == 3


def test_locked_day_helper_rejects_garbage_date(api_env):
    """定格日串坏了 / 老快照没这一格 → None(如实说不知道,不猜)。"""
    from datetime import date as _date

    from neckline.api.app import _locked_time_exit_day

    buy = _date(2026, 7, 20)
    assert _locked_time_exit_day(buy, None) is None
    assert _locked_time_exit_day(buy, "") is None
    assert _locked_time_exit_day(buy, "not-a-date") is None
