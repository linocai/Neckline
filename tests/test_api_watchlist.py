"""v1.1-C 自选池端点单测(plan C 验收:≤30 上限拒绝 / 增删只由显式调用 / pin 切换 /
同花顺 txt 对账 + 导出往返 / GET 附带最近体检快照)。"""

from __future__ import annotations

from datetime import date

from neckline.report import store as report_store
from neckline.watchlist import MAX_WATCHLIST_SIZE


def test_add_list_delete_roundtrip(client, AUTH):
    r = client.post("/api/v1/watchlist", headers=AUTH, json={"code": "600001.SH", "name": "示例甲"})
    assert r.status_code == 200
    body = r.json()
    assert body["item"]["code"] == "600001.SH" and body["item"]["name"] == "示例甲"
    assert body["item"]["pinned"] is False

    lst = client.get("/api/v1/watchlist", headers=AUTH).json()
    assert lst["maxSize"] == MAX_WATCHLIST_SIZE
    assert [i["code"] for i in lst["items"]] == ["600001.SH"]
    assert lst["items"][0]["check"] is None   # 从未跑过报告 → 无最近体检快照

    d = client.delete("/api/v1/watchlist/600001.SH", headers=AUTH)
    assert d.status_code == 200 and d.json()["ok"] is True
    assert client.get("/api/v1/watchlist", headers=AUTH).json()["items"] == []


def test_add_normalizes_bare_code(client, AUTH):
    r = client.post("/api/v1/watchlist", headers=AUTH, json={"code": "600001"})
    assert r.json()["item"]["code"] == "600001.SH"


def test_delete_nonexistent_404(client, AUTH):
    r = client.delete("/api/v1/watchlist/999999.SH", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "not_found"


def test_add_over_cap_returns_422(client, AUTH):
    for i in range(MAX_WATCHLIST_SIZE):
        r = client.post("/api/v1/watchlist", headers=AUTH, json={"code": f"{600000 + i:06d}.SH"})
        assert r.status_code == 200
    r = client.post("/api/v1/watchlist", headers=AUTH, json={"code": "999999.SH"})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "watchlist_full"
    assert len(client.get("/api/v1/watchlist", headers=AUTH).json()["items"]) == MAX_WATCHLIST_SIZE


def test_readd_existing_code_at_full_capacity_does_not_422(client, AUTH):
    """已存在的代码重新提交(改名/改备注)不算新增,已满时也不应报 422。"""
    for i in range(MAX_WATCHLIST_SIZE):
        client.post("/api/v1/watchlist", headers=AUTH, json={"code": f"{600000 + i:06d}.SH"})
    r = client.post("/api/v1/watchlist", headers=AUTH, json={"code": "600000.SH", "note": "改备注"})
    assert r.status_code == 200
    assert r.json()["item"]["note"] == "改备注"


def test_pin_toggle_roundtrip(client, AUTH):
    client.post("/api/v1/watchlist", headers=AUTH, json={"code": "600001.SH"})
    r = client.put("/api/v1/watchlist/600001.SH/pin", headers=AUTH, json={"pinned": True})
    assert r.status_code == 200 and r.json()["ok"] is True
    item = client.get("/api/v1/watchlist", headers=AUTH).json()["items"][0]
    assert item["pinned"] is True

    client.put("/api/v1/watchlist/600001.SH/pin", headers=AUTH, json={"pinned": False})
    item = client.get("/api/v1/watchlist", headers=AUTH).json()["items"][0]
    assert item["pinned"] is False


def test_pin_nonexistent_404(client, AUTH):
    r = client.put("/api/v1/watchlist/999999.SH/pin", headers=AUTH, json={"pinned": True})
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "not_found"


def test_get_watchlist_includes_latest_check_snapshot(client, AUTH, api_env):
    """GET /watchlist「列表 + 各只体检最近快照」(plan C.1)——附带最近一份报告的
    自选体检结果(读已落库快照,不现场重算)。"""
    client.post("/api/v1/watchlist", headers=AUTH, json={"code": "600001.SH"})
    report_store.save_report(
        date(2026, 7, 20), strategy_version="v1", sentiment={}, sectors=[], candidates=[],
        markdown="# t", db_path=api_env.db_path,
        watchlist=[{
            "ts_code": "600001.SH", "name": "示例甲", "pinned": False, "source": "manual", "has_data": True,
            "close": 12.34, "board": "MAIN", "score": 77.7, "pattern_tags": ["均线多头"],
            "hot_sectors": [], "sector_names": [], "green_light": True, "disqualifiers": [],
            "buy_point_triggered": True, "entry_plan": "回调低吸...", "stop_loss": "止损...",
            "target": "目标...", "invalidation_text": "证伪...",
            "invalidation_spec": {"low_open_pct": -0.02}, "entry_spec": {"buypoint": "pullback", "ma10": 9.5},
            "status_changed": True, "llm_judgment": {"verdict": "通过", "narrative": "分析...", "degraded": False},
        }],
    )
    item = client.get("/api/v1/watchlist", headers=AUTH).json()["items"][0]
    check = item["check"]
    assert check is not None
    assert check["close"] == 12.34 and check["score"] == 77.7
    assert check["greenLight"] is True
    assert check["buyPointTriggered"] is True
    assert check["buyPoint"] == "回调低吸..."
    assert check["patternTags"] == ["均线多头"]
    assert check["statusChanged"] is True
    assert check["llmJudgment"]["verdict"] == "通过"


def test_report_latest_includes_watchlist_check(client, AUTH, api_env):
    """`ReportOut.watchlistCheck`(§C「体检节进 _shape_report」验收)。"""
    report_store.save_report(
        date(2026, 7, 20), strategy_version="v1", sentiment={}, sectors=[], candidates=[],
        markdown="# t", db_path=api_env.db_path,
        watchlist=[{
            "ts_code": "600002.SH", "name": "示例乙", "pinned": True, "source": "manual", "has_data": True,
            "close": 5.0, "board": "MAIN", "score": 60.0, "pattern_tags": [], "hot_sectors": [],
            "sector_names": [], "green_light": False, "disqualifiers": ["ST/*ST(选股域清洗,禁买)"],
            "buy_point_triggered": False, "entry_plan": "无", "stop_loss": "无", "target": "无",
            "invalidation_text": "无", "invalidation_spec": {}, "entry_spec": {},
            "status_changed": False, "llm_judgment": None,
        }],
    )
    rep = client.get("/api/v1/report/latest", headers=AUTH).json()
    assert len(rep["watchlistCheck"]) == 1
    wc = rep["watchlistCheck"][0]
    assert wc["code"] == "600002.SH" and wc["greenLight"] is False
    assert wc["pinned"] is True
    assert wc["llmJudgment"] is None


def test_old_report_without_watchlist_json_defaults_to_empty_list(client, AUTH, api_env):
    """前向兼容:旧报告行(建这节之前生成的)读回来 `watchlistCheck` 是空列表,
    不是 null/报错(见 `reports.watchlist_json` 列默认值 `'[]'`)。"""
    report_store.save_report(
        date(2026, 7, 20), strategy_version="v1", sentiment={}, sectors=[], candidates=[],
        markdown="# t", db_path=api_env.db_path,   # 不传 watchlist,走默认值
    )
    rep = client.get("/api/v1/report/latest", headers=AUTH).json()
    assert rep["watchlistCheck"] == []


def test_report_latest_watchlist_check_dispatch_alerts_roundtrip(client, AUTH, api_env):
    """v1.5-④-A1:`WatchlistCheckOut.dispatchAlerts` 落库快照 → API 契约往返
    (`level` 不透传,见 `DispatchAlertOut` docstring)。"""
    report_store.save_report(
        date(2026, 7, 20), strategy_version="v1", sentiment={}, sectors=[], candidates=[],
        markdown="# t", db_path=api_env.db_path,
        watchlist=[{
            "ts_code": "600003.SH", "name": "示例丙", "pinned": False, "source": "manual", "has_data": True,
            "close": 8.0, "board": "MAIN", "score": 50.0, "pattern_tags": [], "hot_sectors": [],
            "sector_names": [], "green_light": True, "disqualifiers": [],
            "buy_point_triggered": False, "entry_plan": "无", "stop_loss": "无", "target": "无",
            "invalidation_text": "无", "invalidation_spec": {}, "entry_spec": {},
            "status_changed": False, "llm_judgment": None,
            "dispatch_alerts": [{
                "code": "A3_belowyear_limitup", "label": "年线下涨停(疑似诱多做局派发)",
                "level": "strong", "evidence": "年线下涨停=诱多域,2026 -3.96%、左尾肥",
                "evidence_strength": "price_volume",
            }],
        }],
    )
    rep = client.get("/api/v1/report/latest", headers=AUTH).json()
    wc = next(w for w in rep["watchlistCheck"] if w["code"] == "600003.SH")
    assert wc["dispatchAlerts"] == [{
        "code": "A3_belowyear_limitup", "label": "年线下涨停(疑似诱多做局派发)",
        "evidence": "年线下涨停=诱多域,2026 -3.96%、左尾肥", "evidenceStrength": "price_volume",
    }]


def test_report_latest_watchlist_check_dispatch_alerts_default_empty_for_old_snapshot(client, AUTH, api_env):
    """老报告快照(建于本字段前,无 `dispatch_alerts` 键)→ `dispatchAlerts` 默认空
    列表,不报错(前向兼容)。"""
    report_store.save_report(
        date(2026, 7, 21), strategy_version="v1", sentiment={}, sectors=[], candidates=[],
        markdown="# t", db_path=api_env.db_path,
        watchlist=[{
            "ts_code": "600004.SH", "name": "示例丁", "pinned": False, "source": "manual", "has_data": True,
            "close": 8.0, "board": "MAIN", "score": 50.0, "pattern_tags": [], "hot_sectors": [],
            "sector_names": [], "green_light": True, "disqualifiers": [],
            "buy_point_triggered": False, "entry_plan": "无", "stop_loss": "无", "target": "无",
            "invalidation_text": "无", "invalidation_spec": {}, "entry_spec": {},
            "status_changed": False, "llm_judgment": None,
            # 故意不传 dispatch_alerts 键,模拟本字段上线前的老快照。
        }],
    )
    rep = client.get("/api/v1/report/latest", headers=AUTH).json()
    wc = next(w for w in rep["watchlistCheck"] if w["code"] == "600004.SH")
    assert wc["dispatchAlerts"] == []


# —— 同花顺 txt 对账 / 导出(plan C.4)——————————————————————————————————————

def test_reconcile_ths_endpoint_diff(client, AUTH):
    client.post("/api/v1/watchlist", headers=AUTH, json={"code": "000001.SZ"})
    client.post("/api/v1/watchlist", headers=AUTH, json={"code": "600519.SH"})
    txt = "600000\n000001\n"   # 同花顺侧:600000.SH(Neckline没有)+ 000001.SZ(两边都有)
    r = client.post(
        "/api/v1/watchlist/reconcile-ths", headers=AUTH,
        files={"file": ("自选股.txt", txt.encode("utf-8"), "text/plain")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["onlyInThs"] == ["600000.SH"]
    assert body["onlyInNeckline"] == ["600519.SH"]
    assert body["both"] == ["000001.SZ"]
    # 对账端点本身不写入,自选池维持原样
    assert len(client.get("/api/v1/watchlist", headers=AUTH).json()["items"]) == 2


def test_export_ths_endpoint(client, AUTH):
    client.post("/api/v1/watchlist", headers=AUTH, json={"code": "600001.SH"})
    client.post("/api/v1/watchlist", headers=AUTH, json={"code": "000001.SZ"})
    r = client.get("/api/v1/watchlist/export-ths", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert "600001.SH" in body["text"] and "000001.SZ" in body["text"]


def test_export_ths_empty_watchlist(client, AUTH):
    r = client.get("/api/v1/watchlist/export-ths", headers=AUTH)
    assert r.json() == {"text": "", "count": 0}
