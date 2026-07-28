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


def _seed_report(db, d: date, *, intel=None, sector_moneyflow=None, news_alerts_scan=None):
    report_store.save_report(
        d, strategy_version="v1",
        sentiment={"trade_date": d.isoformat(), "limit_up_count": 48, "limit_down_count": 41,
                   "zaban_rate": 0.37, "max_consec_limit_up": 3, "position_quota": "半额",
                   "quota_reason": "情绪中性"},
        sectors=[{"index_code": "AI", "name": "AI", "board_age": 3, "ret_20d": 0.12, "bonus": 3.0, "rank": 1}],
        candidates=[_candidate(1, "600001.SH", "示例甲"), _candidate(2, "600002.SH", "示例乙")],
        markdown="# 报告", intel=intel, sector_moneyflow=sector_moneyflow,
        news_alerts_scan=news_alerts_scan, db_path=db,
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


def test_report_latest_intel_rank_carries_source_industry_permanent_board_status(client, AUTH, api_env):
    """v1.3-⑥ 后端补齐①:`IntelRankOut` 补 `source`/`industry`/`permanentBoardStatus` 三
    字段——数据早在 v1.3-③-C3 落 `intel_rank` 字典/报告落库快照,此前 pydantic 未声明
    这三键 → 默认丢弃(`CandidateOut.intelRank = IntelRankOut(**(c.get("intel_rank") or {}))`
    静默吞掉未声明的额外键),本次补字段透出、生成逻辑零改动。"""
    board_status = [{
        "board": "稀土永磁", "surviveCount": 9, "industryGatePass": 1, "industryGateBlocked": 8,
        "hardCutBlocked": 1, "quotaFilled": 0,
        "note": "稀土永磁:保底 0 只 —— 9 只过卫生线成员中 8 只行业不属本板块主导行业、"
                "1 只过闸但命中 K4 安检拦截,宁缺毋滥、非静默空白",
    }]
    c = _candidate(1, "600001.SH", "示例甲")
    c["k4_flags"] = ["B2_double_gold_cross"]
    c["intel_rank"] = {
        "sectorFlow": 1234.5, "themePersistDays": 1, "highElasticity": True,
        "source": "quota", "industry": "小金属", "permanentBoardStatus": board_status,
    }
    report_store.save_report(
        date(2026, 7, 22), strategy_version="v1.3",
        sentiment={"trade_date": "20260722"}, sectors=[], candidates=[c],
        markdown="# 报告", db_path=api_env.db_path,
    )
    rank = client.get("/api/v1/report/latest", headers=AUTH).json()["candidates"][0]["intelRank"]
    assert rank["source"] == "quota"
    assert rank["industry"] == "小金属"
    assert rank["sectorFlow"] == 1234.5
    assert len(rank["permanentBoardStatus"]) == 1
    status0 = rank["permanentBoardStatus"][0]
    assert status0["board"] == "稀土永磁"
    assert status0["quotaFilled"] == 0
    assert status0["industryGateBlocked"] == 8
    assert "宁缺毋滥" in status0["note"]


def test_report_latest_intel_rank_carries_v143_sort_key_fields(client, AUTH, api_env):
    """v1.4-③-E:`IntelRankOut` 补 `industryRank`/`industryPersistDays`/`yellowCardCount`
    三个新字段(需求 8 排序键三级键原样透出)——报告落库快照 → API 读回往返不丢字段,
    `industryRank=None`(未参与排名)与 `0` 显式区分(不得混淆)。"""
    c = _candidate(1, "600001.SH", "示例甲")
    c["intel_rank"] = {
        "sectorFlow": 1234.5, "themePersistDays": 2, "highElasticity": False,
        "source": "competition", "industry": "半导体", "permanentBoardStatus": [],
        "industryRank": 7, "industryPersistDays": 2, "yellowCardCount": 1,
    }
    c2 = _candidate(2, "600002.SH", "示例乙")
    c2["intel_rank"] = {
        "sectorFlow": None, "themePersistDays": 0, "highElasticity": False,
        "source": "quota", "industry": "", "permanentBoardStatus": [],
        "industryRank": None, "industryPersistDays": 0, "yellowCardCount": 0,
    }
    report_store.save_report(
        date(2026, 7, 28), strategy_version="v1.4.0",
        sentiment={"trade_date": "20260728"}, sectors=[], candidates=[c, c2],
        markdown="# 报告", db_path=api_env.db_path,
    )
    cands = client.get("/api/v1/report/latest", headers=AUTH).json()["candidates"]
    rank1 = {r["code"]: r["intelRank"] for r in cands}["600001.SH"]
    rank2 = {r["code"]: r["intelRank"] for r in cands}["600002.SH"]
    assert rank1["industryRank"] == 7
    assert rank1["industryPersistDays"] == 2
    assert rank1["yellowCardCount"] == 1
    # 600002:industry_rank=None(未参与排名)不得读回 0(0 会被误读成"最强"),显式 None 往返。
    assert rank2["industryRank"] is None
    assert rank2["industryPersistDays"] == 0
    assert rank2["yellowCardCount"] == 0


def test_report_latest_intel_rank_defaults_when_old_snapshot(client, AUTH, api_env):
    """旧报告(建于本三字段前,`intel_rank` 无 source/industry/permanentBoardStatus 键,
    也无 v1.4-③ 的 industryRank/industryPersistDays/yellowCardCount 三键)读回默认——
    `source`/`industry` 空串、`permanentBoardStatus` 空数组、`industryRank` None、
    `industryPersistDays`/`yellowCardCount` 0,前向兼容不崩、不冒充"quota/competition/
    forced"三值之一(客户端未识别值原样透传),也不把 `industryRank=None` 冒充"参与过排名
    但查无"(旧报告压根没算过这件事)。"""
    _seed_report(api_env.db_path, date(2026, 7, 17))
    rank = client.get("/api/v1/report/latest", headers=AUTH).json()["candidates"][0]["intelRank"]
    assert rank == {
        "sectorFlow": None, "themePersistDays": 0, "highElasticity": False,
        "source": "", "industry": "", "permanentBoardStatus": [],
        "industryRank": None, "industryPersistDays": 0, "yellowCardCount": 0,
    }


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


# —— v1.4-④-B `CandidateOut.infoCard`(信息卡摘要,不含 60 日序列)——————————————

def test_report_candidate_carries_info_card_summary(client, AUTH, api_env):
    """`Candidate.info_card_summary` 存档 → `CandidateOut.infoCard` 往返不丢字段。"""
    c = _candidate(1, "600001.SH", "示例甲")
    c["info_card_summary"] = {
        "snapshot": {"volRatio5": 1.23, "turnoverRate": 5.6, "industryRank": 3,
                     "industryPersistDays": 1, "aboveMa250": True, "distFromMa250Pct": 0.05,
                     "distFromHigh20dPct": -0.02, "consecLimitUpDays": 0},
        "mildBand": True,
        "news": {"scanned": True, "items": [{"category": "REDUCTION", "summary": "x", "source": "tushare_holdertrade"}],
                  "unavailableReason": None},
        "topList": {"onListToday": False, "reason": None, "netAmount": None, "netRate": None,
                     "lookbackDaysCovered": 4, "lookbackHitDays": 0},
    }
    report_store.save_report(
        date(2026, 7, 28), strategy_version="v1.4.0",
        sentiment={"trade_date": "20260728"}, sectors=[], candidates=[c],
        markdown="# 报告", db_path=api_env.db_path,
    )
    body = client.get("/api/v1/report/latest", headers=AUTH).json()
    info = body["candidates"][0]["infoCard"]
    assert info["snapshot"]["industryRank"] == 3
    assert info["snapshot"]["aboveMa250"] is True
    assert info["mildBand"] is True
    assert info["news"]["scanned"] is True
    assert info["news"]["items"][0]["summary"] == "x"
    assert info["topList"]["lookbackDaysCovered"] == 4
    # 摘要位不含 60 日序列(键集断言,④ 验收原话)。
    assert set(info.keys()) == {"snapshot", "mildBand", "news", "topList"}


def test_report_candidate_info_card_none_for_old_snapshot(client, AUTH, api_env):
    """老报告(建于本字段之前,`candidates_json` 里没有 `info_card_summary` 键)→
    `infoCard=None`,不冒充"确认无内容"(与 `intelRank` 用默认空 dict 的处理方式
    刻意不同——`infoCard` 整体缺失时用 `None` 更诚实,因为它没有天然的"空但合法"态)。"""
    _seed_report(api_env.db_path, date(2026, 7, 17))
    body = client.get("/api/v1/report/latest", headers=AUTH).json()
    assert body["candidates"][0]["infoCard"] is None
