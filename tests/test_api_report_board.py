"""4A.2 报告端点 + 4A.3 盘中看板端点单测。报告读 `reports` 表(不重算)、看板读
当日 `sentinel_events` 聚合(买点/证伪/持仓进看板,退潮进红条不进事件列表)。"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.llm.judge import JudgeResult
from neckline.report import store as report_store
from neckline.sentinel import dedup


def _candidate(rank: int, code: str, name: str) -> dict:
    return {
        "ts_code": code, "name": name, "close": 10.0, "score": 88.0, "rank": rank,
        "board": "MAIN", "pattern_tags": ["浅回调贴前高", "放量"],
        "hot_sectors": ["AI(板块年龄3天,20日+12.0%)"], "sector_names": ["AI"],
        "entry_plan": "回调低吸:站稳10日线", "stop_loss": "参考止损价约 9.50 元(-5%)",
        "target": "不设固定止盈线;持有满5日无条件离场", "invalidation_text": "次日低开≤-2%且全天未翻红…",
        "invalidation_spec": {"low_open_pct": -0.02, "vwap_break": True},
        "entry_spec": {"buypoint": "pullback", "ma10": 9.9},
    }


def _seed_report(db, d: date):
    report_store.save_report(
        d, strategy_version="v1",
        sentiment={"trade_date": d.isoformat(), "limit_up_count": 48, "limit_down_count": 41,
                   "zaban_rate": 0.37, "max_consec_limit_up": 3, "position_quota": "半额",
                   "quota_reason": "情绪中性"},
        sectors=[{"index_code": "AI", "name": "AI", "board_age": 3, "ret_20d": 0.12, "bonus": 3.0, "rank": 1}],
        candidates=[_candidate(1, "600001.SH", "示例甲"), _candidate(2, "600002.SH", "示例乙")],
        markdown="# 报告", db_path=db,
    )
    report_store.save_llm_judgment(
        d, JudgeResult(ts_code="600001.SH", provider="glm", model="glm-5.2", verdict="通过",
                       narrative="催化站得住。", degraded=False), db_path=db,
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
    cands = body["candidates"]
    assert len(cands) == 2
    c0 = cands[0]
    assert c0["code"] == "600001.SH" and c0["rank"] == 1
    # 四件套映射
    assert "回调低吸" in c0["buyPoint"] and "-5%" in c0["stop"]
    assert c0["formTags"] == ["浅回调贴前高", "放量"]
    # 前排候选带 LLM 审判
    assert c0["llmJudgment"]["verdict"] == "通过"
    # 未审判候选无 llmJudgment
    assert cands[1]["llmJudgment"] is None


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
    assert body["candidates"] == []


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
