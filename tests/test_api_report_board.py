"""4A.2 报告端点 + 4A.3 盘中看板端点单测。报告读 `reports` 表(不重算)、看板读
当日 `sentinel_events` 聚合(买点/证伪/持仓进看板,退潮进红条不进事件列表)。"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.report import store as report_store
from neckline.sentinel import dedup


def _basket_daily(**overrides) -> dict:
    """一份 ⑭-A 篮子日报快照(`reports.basket_daily_json` 的形状,已是 camelCase)。"""
    base = {
        "tradeDate": "20260717",
        "baskets": [{
            "basketId": 7, "basketKey": "abc123", "name": "固态电池", "tier": 1,
            "memberCodes": ["600001.SH"], "card": None, "cardVersion": None,
            "cardUnavailableReason": "card_not_ready", "execHints": {},
        }],
        "basketsAvailable": True, "basketsUnavailableReason": None,
        "droppedBaskets": [], "droppedBasketsAvailable": True,
        "droppedBasketsUnavailableReason": None,
        "reviews": [], "reviewsAvailable": True, "reviewsUnavailableReason": None,
        "reviewD0": None, "packVersion": "K4-pack-v1", "notes": [],
    }
    base.update(overrides)
    return base


def _seed_report(db, d: date, *, intel=None, sector_moneyflow=None, news_alerts_scan=None,
                 basket_daily=None):
    report_store.save_report(
        d, strategy_version="v1",
        sentiment={"trade_date": d.isoformat(), "limit_up_count": 48, "limit_down_count": 41,
                   "zaban_rate": 0.37, "max_consec_limit_up": 3, "position_quota": "半额",
                   "quota_reason": "情绪中性"},
        sectors=[{"index_code": "AI", "name": "AI", "board_age": 3, "ret_20d": 0.12, "bonus": 3.0, "rank": 1}],
        candidates=[],   # ⑬-1 候选榜已删;⑭-B 起契约面也没有 `candidates` 键了
        markdown="# 报告", intel=intel, sector_moneyflow=sector_moneyflow,
        news_alerts_scan=news_alerts_scan,
        basket_daily=(basket_daily if basket_daily is not None else _basket_daily()),
        db_path=db,
    )


def test_report_latest(client, AUTH, api_env):
    _seed_report(api_env.db_path, date(2026, 7, 17))
    r = client.get("/api/v1/report/latest", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["tradeDate"] == "20260717"
    assert body["strategyVersion"] == "v1"
    assert body["sentiment"]["position_quota"] == "半额"
    assert body["sectors"][0]["board_age"] == 3
    # ⑭-B:候选榜整族契约已退役,取而代之的是篮子日报三段。
    assert "candidates" not in body, "`candidates` 键应已随 ⑭-B 契约总装删除"
    bd = body["basketDaily"]
    assert bd["basketsAvailable"] is True and len(bd["baskets"]) == 1
    b0 = bd["baskets"][0]
    assert b0["basketId"] == 7 and b0["tier"] == 1 and b0["name"] == "固态电池"
    # 有篮子无卡是**合法中间态**:`card=null` + 明确的原因码,⛔ 不是"篮子不存在"。
    assert b0["card"] is None and b0["cardUnavailableReason"] == "card_not_ready"
    assert bd["packVersion"] == "K4-pack-v1"


def test_report_basket_daily_empty_snapshot_is_honest_about_not_looked(client, AUTH, api_env):
    """老报告(建于 `basket_daily_json` 列之前)→ 三段全 `available=false` + 原因,
    ⛔ 不冒充「那天没有篮子」——「没有」与「没看」必须能分开(§3.8)。"""
    _seed_report(api_env.db_path, date(2026, 7, 18), basket_daily={})
    bd = client.get("/api/v1/report/latest", headers=AUTH).json()["basketDaily"]
    assert bd["basketsAvailable"] is False
    assert bd["basketsUnavailableReason"]
    assert bd["droppedBasketsAvailable"] is False
    assert bd["reviewsAvailable"] is False
    assert bd["baskets"] == [] and bd["droppedBaskets"] == [] and bd["reviews"] == []


def test_report_dropped_baskets_two_reason_codes_stay_separate(client, AUTH, api_env):
    """③b 两个原因码指向**相反的市场结论**,契约面必须原样透出、⛔ 不许在服务端
    合并成一句「未入选」(⑥-b-C)。"""
    _seed_report(api_env.db_path, date(2026, 7, 19), basket_daily=_basket_daily(
        droppedBaskets=[
            {"name": "机器人", "mechScore": 0.71, "reason": "capacity_overflow"},
            {"name": "白酒", "mechScore": 0.12, "reason": "below_quality_line"},
        ],
    ))
    bd = client.get("/api/v1/report/latest", headers=AUTH).json()["basketDaily"]
    assert bd["droppedBasketsAvailable"] is True
    reasons = {d["reason"] for d in bd["droppedBaskets"]}
    assert reasons == {"capacity_overflow", "below_quality_line"}
    # ⛔ 溢出篮**没有** basketId(它没进 `baskets` 表)
    assert all("basketId" not in d for d in bd["droppedBaskets"])


def test_report_dropped_baskets_empty_array_needs_available_flag_to_mean_none(client, AUTH, api_env):
    """空数组 + `available=true` = **今天真的零溢出**(算过了);
    空数组 + `available=false` = **本次没算**。两者在契约面必须能分开。"""
    _seed_report(api_env.db_path, date(2026, 7, 20), basket_daily=_basket_daily(
        droppedBaskets=[], droppedBasketsAvailable=False,
        droppedBasketsUnavailableReason="本次未运行 Tier 分层引擎。",
    ))
    bd = client.get("/api/v1/report/latest", headers=AUTH).json()["basketDaily"]
    assert bd["droppedBaskets"] == [] and bd["droppedBasketsAvailable"] is False
    assert bd["droppedBasketsUnavailableReason"]



def test_report_latest_carries_intel_and_sector_moneyflow(client, AUTH, api_env):
    """v1.3-③ C1/C2 契约(`ReportOut.intel`/`sectorMoneyflow`)——透传报告落库快照,
    同 sentiment/sectors 惯例(schemas.py 顶部约定),不在 API 层重抄字段定义。"""
    _seed_report(
        api_env.db_path, date(2026, 7, 17),
        intel={"tradeDate": "2026-07-17", "gainers": [{"code": "600001.SH", "pctChg": 9.9}], "warnings": []},
        sector_moneyflow={"available": True, "topInflow": [{"code": "AAA.TI", "netInflowWan": 1234.5}]},
    )
    body = client.get("/api/v1/report/latest", headers=AUTH).json()
    assert body["intel"]["gainers"][0]["code"] == "600001.SH"
    assert body["sectorMoneyflow"]["available"] is True
    assert body["sectorMoneyflow"]["topInflow"][0]["code"] == "AAA.TI"


def test_report_latest_intel_defaults_to_empty_dict_when_not_seeded(client, AUTH, api_env):
    """旧报告行(intel/sectorMoneyflow 建列前生成,或 v1.3-③ 之前的历史报告)读回
    来是空字典,不是 null——客户端前向兼容不必对 null 特判。"""
    _seed_report(api_env.db_path, date(2026, 7, 17))
    body = client.get("/api/v1/report/latest", headers=AUTH).json()
    assert body["intel"] == {}
    assert body["sectorMoneyflow"] == {}


def test_report_latest_carries_news_alerts_and_scan_status(client, AUTH, api_env):
    """v1.3-③-C4 契约:`ReportOut.newsAlerts`(命中告警,独立表实时查,契约字面
    字段 code/category/summary/source + 附加 name)+ `newsAlertsScan`(扫描状态,
    "没扫到 vs 扫了没有"透明度字段,v1.3-⑥ 后端补齐 `codesSkipped` 透出——领域层
    〔`report/news_alerts.py`〕早已产出该键,此前 `_shape_report` 未读取转发)。"""
    from neckline.report.news_alerts_store import save_news_alerts

    d = date(2026, 7, 17)
    _seed_report(
        api_env.db_path, d,
        news_alerts_scan=[
            {"source": "tushare_holdertrade", "scanned": True, "reason": "", "codesTotal": 0, "codesFailed": 0, "codesSkipped": 0},
            {"source": "llm", "scanned": True, "reason": "墙钟预算耗尽,部分标的未及扫描",
             "codesTotal": 5, "codesFailed": 1, "codesSkipped": 2},
        ],
    )

    class _Item:
        def __init__(self, ts_code, category, summary, source):
            self.ts_code, self.category, self.summary, self.source = ts_code, category, summary, source

    save_news_alerts(d, [_Item("600001.SH", "REDUCTION", "张三减持 5万股", "tushare_holdertrade")], db_path=api_env.db_path)

    body = client.get("/api/v1/report/latest", headers=AUTH).json()
    assert body["tradeDate"] == "20260717"
    assert len(body["newsAlerts"]) == 1
    alert = body["newsAlerts"][0]
    assert alert["code"] == "600001.SH"
    assert alert["category"] == "REDUCTION"
    assert alert["summary"] == "张三减持 5万股"
    assert alert["source"] == "tushare_holdertrade"

    scan = {s["source"]: s for s in body["newsAlertsScan"]}
    assert scan["tushare_holdertrade"]["scanned"] is True
    assert scan["tushare_holdertrade"]["codesSkipped"] == 0
    assert scan["llm"]["scanned"] is True
    assert "预算耗尽" in scan["llm"]["reason"]
    # codesSkipped(墙钟预算耗尽、根本没发起调用就跳过)与 codesFailed(调用了但失败)
    # 语义不同、两者都要透出,不能合并成一个数字。
    assert scan["llm"]["codesFailed"] == 1
    assert scan["llm"]["codesSkipped"] == 2


def test_report_latest_news_alerts_scan_codes_skipped_defaults_to_zero_for_old_snapshot(client, AUTH, api_env):
    """旧报告(建于 `codesSkipped` 字段前的 `news_alerts_scan_json` 快照,无该键)读回
    默认 0——不冒充"预算未耗尽"以外的任何含义,只是诚实的"该信息在旧快照里不存在"。"""
    _seed_report(
        api_env.db_path, date(2026, 7, 17),
        news_alerts_scan=[{"source": "llm", "scanned": True, "reason": "", "codesTotal": 3, "codesFailed": 0}],
    )
    scan = client.get("/api/v1/report/latest", headers=AUTH).json()["newsAlertsScan"][0]
    assert scan["codesSkipped"] == 0


def test_report_latest_news_alerts_default_to_empty_when_not_seeded(client, AUTH, api_env):
    _seed_report(api_env.db_path, date(2026, 7, 17))
    body = client.get("/api/v1/report/latest", headers=AUTH).json()
    assert body["newsAlerts"] == []
    assert body["newsAlertsScan"] == []


def test_report_latest_picks_newest(client, AUTH, api_env):
    _seed_report(api_env.db_path, date(2026, 7, 16))
    _seed_report(api_env.db_path, date(2026, 7, 17))
    assert client.get("/api/v1/report/latest", headers=AUTH).json()["tradeDate"] == "20260717"


def test_report_by_date_historical(client, AUTH, api_env):
    _seed_report(api_env.db_path, date(2026, 7, 16))
    _seed_report(api_env.db_path, date(2026, 7, 17))
    body = client.get("/api/v1/report?date=20260716", headers=AUTH).json()
    assert body["tradeDate"] == "20260716" and not body["degraded"]


def test_report_latest_empty_degraded(client, AUTH):
    body = client.get("/api/v1/report/latest", headers=AUTH).json()
    assert body["degraded"] is True and body["reason"] == "no_report"
    # ⑭-B:空态也是三段全 `available=false`(⛔ 不冒充「那天没有篮子」)。
    assert body["basketDaily"]["basketsAvailable"] is False


def test_report_by_date_notfound_degraded(client, AUTH):
    body = client.get("/api/v1/report?date=20200101", headers=AUTH).json()
    assert body["degraded"] is True and body["reason"] == "no_report"


def test_report_bad_date_degraded(client, AUTH):
    assert client.get("/api/v1/report?date=abc", headers=AUTH).json()["reason"] == "bad_date"


# —— 4A.3 看板 ————————————————————————————————————————————————————————

def test_board_aggregates_events(client, AUTH, api_env, monkeypatch):
    db = api_env.db_path
    today = date.today()
    # 退潮红条
    dedup.record_pushed(today, "retreat", "", "brake", payload={"body": "炸板率飙升,今日计划作废"}, db_path=db)
    # 买点/证伪/持仓事件
    dedup.record_pushed(today, "entry", "600001.SH", "trigger", payload={"body": "买点确认:站稳VWAP"}, db_path=db)
    dedup.record_pushed(today, "invalidation", "600002.SH", "trigger", payload={"body": "跌破VWAP,剔除勿进"}, db_path=db)
    dedup.record_pushed(today, "holding", "600003.SH", "stop_approach", payload={"body": "逼近止损线"}, db_path=db)

    body = client.get("/api/v1/board", headers=AUTH).json()
    assert body["tradeDate"] == today.strftime("%Y%m%d")
    assert body["retreatBrake"]["active"] is True
    assert "炸板率飙升" in body["retreatBrake"]["reason"]
    # 退潮不进事件列表;其余三类进
    sentinels = {e["sentinel"] for e in body["events"]}
    assert sentinels == {"买点", "证伪", "持仓"}
    codes = {e["code"] for e in body["events"]}
    assert codes == {"600001.SH", "600002.SH", "600003.SH"}
    entry_ev = next(e for e in body["events"] if e["sentinel"] == "买点")
    assert "站稳VWAP" in entry_ev["verdict"]


def test_board_empty(client, AUTH):
    body = client.get("/api/v1/board", headers=AUTH).json()
    assert body["retreatBrake"]["active"] is False
    assert body["events"] == []


def test_board_labels_precall_and_d5exit_events(client, AUTH, api_env):
    """v1.1-G.3:看板中文标签覆盖新两类哨兵事件(客户端 `SentinelKind` 枚举依赖此
    契约——`_SENTINEL_LABEL` 把 `precall`/`d5exit` 翻成中文,不是原样透传英文键)。"""
    db = api_env.db_path
    today = date.today()
    dedup.record_pushed(
        today, "precall", "600004.SH", "gap_up_invalidate",
        payload={"body": "集合竞价开盘12.00高于买点参考位11.00 9.1%(超阈3%),今日买点已变形失效。"},
        db_path=db,
    )
    dedup.record_pushed(
        today, "d5exit", "600005.SH", "trigger",
        payload={"body": "示例丙 今日 D5 时间退出日,按计划离场。"},
        db_path=db,
    )
    # 市场级「盘前 tick 已跑」标记(空 ts_code)不应进事件列表——同退潮红条一个道理。
    dedup.record_pushed(today, "precall", "", "tick", payload={"counts": {}}, db_path=db)

    body = client.get("/api/v1/board", headers=AUTH).json()
    sentinels = {e["sentinel"] for e in body["events"]}
    assert sentinels == {"盘前校准", "D5退出"}
    codes = {e["code"] for e in body["events"]}
    assert codes == {"600004.SH", "600005.SH"}
    precall_ev = next(e for e in body["events"] if e["sentinel"] == "盘前校准")
    assert "买点已变形失效" in precall_ev["verdict"]
    d5_ev = next(e for e in body["events"] if e["sentinel"] == "D5退出")
    assert "D5 时间退出日" in d5_ev["verdict"]


def test_board_yellow_retreat_warning_surfaces_but_brake_stays_in_red_bar(client, AUTH, api_env):
    """v1.1-H2 双级制:退潮**黄色预警**(retreat/warn,市场级空 ts_code)是唯一进事件
    列表的市场级事件——不走 retreatBrake 红条(active 仍 False)、verdict 带「黄色预警」
    前缀、标签「退潮」。红色刹车(retreat/brake)仍只走红条不进列表。"""
    db = api_env.db_path
    today = date.today()
    dedup.record_pushed(
        today, "retreat", "", "warn",
        payload={"body": "【黄色预警】热门板块可比个股平均跌幅-4.3%(样本11只),疑似主线跳水"},
        db_path=db,
    )
    body = client.get("/api/v1/board", headers=AUTH).json()
    # 黄色不是刹车 → 红条不亮
    assert body["retreatBrake"]["active"] is False
    # 黄色进事件列表
    warn_ev = next(e for e in body["events"] if e["sentinel"] == "退潮")
    assert "【黄色预警】" in warn_ev["verdict"] and "主线跳水" in warn_ev["verdict"]
    assert warn_ev["eventKey"] == "warn"
    assert warn_ev["code"] == ""  # 市场级,无单票


def test_board_yellow_and_red_coexist(client, AUTH, api_env):
    """同日先黄后红:红条亮(刹车),黄色事件仍在列表里(升级留痕,前晚→盘中叙事完整)。"""
    db = api_env.db_path
    today = date.today()
    dedup.record_pushed(today, "retreat", "", "warn", payload={"body": "【黄色预警】关注池跌停6只"}, db_path=db)
    dedup.record_pushed(today, "retreat", "", "brake", payload={"body": "关注池跌停8只;主线跳水-5%"}, db_path=db)
    body = client.get("/api/v1/board", headers=AUTH).json()
    assert body["retreatBrake"]["active"] is True
    assert "跌停8只" in body["retreatBrake"]["reason"]
    warn_ev = next(e for e in body["events"] if e["sentinel"] == "退潮")
    assert "【黄色预警】" in warn_ev["verdict"]


# —— v1.4-①-C `ReportOut.dataFreshness`(§七 P0-3)——————————————————————————

def test_report_carries_data_freshness_snapshot(client, AUTH, api_env):
    """透传落库快照(**随报告冻住**,不在读时重算——读三天前的报告该看到当时的新鲜度)。"""
    d = date(2026, 7, 27)
    report_store.save_report(
        d, strategy_version="v1.3.3", sentiment={}, sectors=[], candidates=[], markdown="# 报告",
        data_freshness={"sectorDataDate": "20260722", "sectorLagDays": 3, "stale": True},
        db_path=api_env.db_path,
    )
    r = client.get("/api/v1/report/latest", headers=AUTH).json()
    assert r["dataFreshness"] == {"sectorDataDate": "20260722", "sectorLagDays": 3, "stale": True}


def test_report_data_freshness_empty_for_old_snapshot(client, AUTH, api_env):
    """老报告(建于本字段之前)→ 空 dict。**空 ≠ 新鲜**,客户端按「该版本还没有新鲜度
    概念」处理(契约注释已写死这条口径)。"""
    _seed_report(api_env.db_path, date(2026, 7, 24))
    assert client.get("/api/v1/report/latest", headers=AUTH).json()["dataFreshness"] == {}


# —— v1.4-⑥-B 自选隔日轮扫披露:领域层算了必须真的抵达客户端 ——————————————————
#    (pydantic 丢弃未声明字段的老坑:v1.3-⑥ 的 codesSkipped、v1.3.4 的 codesNoSearch
#     都因为只补了领域层没补 schemas/_shape_report 而"算了没送到"。)

def test_report_latest_carries_rotation_disclosure(client, AUTH, api_env):
    from datetime import date

    _seed_report(
        api_env.db_path, date(2026, 7, 17),
        news_alerts_scan=[{
            "source": "llm", "scanned": True, "reason": "自选隔日轮扫:本次扫的是 A 组",
            "codesTotal": 11, "codesFailed": 0, "codesSkipped": 0, "codesNoSearch": 1,
            "rotationGroup": "A", "codesRotationDeferred": 8,
        }],
    )
    scan = client.get("/api/v1/report/latest", headers=AUTH).json()["newsAlertsScan"][0]
    assert scan["rotationGroup"] == "A"
    assert scan["codesRotationDeferred"] == 8
    # 四个计数各是各的(轮空 / 预算跳过 / 失败 / 搜索 0 命中),不许合并
    assert (scan["codesSkipped"], scan["codesFailed"], scan["codesNoSearch"]) == (0, 0, 1)


def test_report_latest_rotation_fields_default_for_old_snapshot(client, AUTH, api_env):
    """⑥-B 之前的老快照没有这两个键 → 缺省 ""/0,前向兼容不崩、也不冒充"扫了全部"。"""
    from datetime import date

    _seed_report(
        api_env.db_path, date(2026, 7, 17),
        news_alerts_scan=[{"source": "llm", "scanned": True, "reason": "", "codesTotal": 3, "codesFailed": 0}],
    )
    scan = client.get("/api/v1/report/latest", headers=AUTH).json()["newsAlertsScan"][0]
    assert scan["rotationGroup"] == ""
    assert scan["codesRotationDeferred"] == 0
