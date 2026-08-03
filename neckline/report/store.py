"""报告与 LLM 审判落库(plan 2.4/2.5)。SQLite 存档:整份报告(markdown + 结构化
快照)与每次 LLM 审判(含搜索结果全文,§2.4「搜索结果全文落 SQLite 存档」的落地
点,供事后审计"当时为何否决" + 自建历史新闻快照)。幂等——同一 `trade_date`
(报告)/ `(trade_date, ts_code)`(审判)重跑会覆盖旧记录,不留重复行,支持
「同一交易日反复重跑报告脚本」这一常见操作场景。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline.db import connection, init_schema
from neckline.llm.judge import JudgeResult


def _d(trade_date: date) -> str:
    return trade_date.strftime("%Y%m%d")


def save_report(
    trade_date: date,
    *,
    strategy_version: str,
    sentiment: Dict[str, Any],
    sectors: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    markdown: str,
    intel: Optional[Dict[str, Any]] = None,
    sector_moneyflow: Optional[Dict[str, Any]] = None,
    news_alerts_scan: Optional[List[Dict[str, Any]]] = None,
    data_freshness: Optional[Dict[str, Any]] = None,
    basket_daily: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> None:
    """⚠ **V2-⑬-11 起 `watchlist_json` 列不再由本函数写入**(自选体检整节删除,裁定
    #9-a):列本身**保留不 DROP**(历史行供归因只读),新行一律吃 DDL 默认值 `'[]'`。
    `intel`/`sector_moneyflow`(v1.3-③ C1/C2,`IntelReport.to_public_dict()` /
    `SectorMoneyflowReport.to_public_dict()` 的字典,均为**单个对象**而非数组——
    已是 camelCase JSON-safe 形状,`sector_moneyflow` 携带 available/
    unavailableReason 等元信息,不是裸榜单):默认 `None` → 落 `'{}'`(旧调用点零
    改动落库形状)。
    `news_alerts_scan`(v1.3-③-C4,`NewsAlertsReport.scan_statuses_public()` 的
    JSON 数组快照——**只是扫描状态元信息,不含命中告警本身**〔告警条目落独立
    `news_alerts` 表,见 `report/news_alerts_store.py`〕):默认 `None` → 落
    `'[]'`。
    `data_freshness`(v1.4-①-C,`SectorDataFreshness.to_public_dict()`:
    `{sectorDataDate, sectorLagDays, stale}`):板块数据相对本报告日落后几个交易日 ——
    **随报告一起冻住**,不在读时重算(读一份三天前的报告时,该看到的是**当时**的新鲜度,
    不是今天的)。默认 `None` → 落 `'{}'`,同 intel 惯例。
    `basket_daily`(V2-⑭-A,`report/basket_daily.py::BasketDaily.to_public_dict()`:
    今日篮子 + ③b 未定档篮子 + 昨日篮子复盘,已是 camelCase):同上**随报告冻住**。
    ⚠ ③b 的 `droppedBaskets` 只活在这份快照里(⑥ 的溢出篮不进 `baskets` 表),
    不落 = 历史回放看不到那天有多少好货装不下。默认 `None` → 落 `'{}'`。"""
    init_schema(db_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connection(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reports "
            "(trade_date, generated_at, strategy_version, sentiment_json, sectors_json, candidates_json, markdown, "
            "intel_json, sector_moneyflow_json, news_alerts_scan_json, data_freshness_json, basket_daily_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                _d(trade_date),
                now,
                strategy_version,
                json.dumps(sentiment, ensure_ascii=False),
                json.dumps(sectors, ensure_ascii=False),
                json.dumps(candidates, ensure_ascii=False),
                markdown,
                json.dumps(intel or {}, ensure_ascii=False),
                json.dumps(sector_moneyflow or {}, ensure_ascii=False),
                json.dumps(news_alerts_scan or [], ensure_ascii=False),
                json.dumps(data_freshness or {}, ensure_ascii=False),
                json.dumps(basket_daily or {}, ensure_ascii=False),
            ),
        )


def _parse_json_field(raw: Optional[str], default: Any) -> Any:
    """幂等补列的 `*_json` 列容错解析——老报告行经 `_migrate_columns` 补列后取列
    默认值('[]'/'{}'),但防御性再兜一层(NULL / 非法 JSON → 调用方给的 `default`,
    不炸历史回放)。`watchlist_json`/`intel_json`/`sector_moneyflow_json` 三列共用。"""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _parse_watchlist_json(raw: Optional[str]) -> List[Dict[str, Any]]:
    """⚠ **V2-⑬-11 起只用于读历史行**(`watchlist_json` 已停写,列留档不 DROP)。
    保留是为了让归因/审计能把 v1.1~v1.5.2 的自选体检快照读回来。"""
    return _parse_json_field(raw, [])


def _parse_intel_json(raw: Optional[str]) -> Dict[str, Any]:
    return _parse_json_field(raw, {})


def _parse_sector_moneyflow_json(raw: Optional[str]) -> Dict[str, Any]:
    return _parse_json_field(raw, {})


def _parse_news_alerts_scan_json(raw: Optional[str]) -> List[Dict[str, Any]]:
    return _parse_json_field(raw, [])


def load_report(trade_date: date, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """查某交易日的报告。查一个"从未生成过报告"的日期是完全正常的场景(如尚未
    到 16:00、当天非交易日、或报告脚本还没跑过)——防御性 `init_schema`,免得
    在从未写过库的全新 DB 上直接炸 `OperationalError: no such table`。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT trade_date, generated_at, strategy_version, sentiment_json, sectors_json, candidates_json, markdown, "
            "watchlist_json, intel_json, sector_moneyflow_json, news_alerts_scan_json, "
            "data_freshness_json, basket_daily_json FROM reports WHERE trade_date=?",
            (_d(trade_date),),
        ).fetchone()
    if row is None:
        return None
    return {
        "trade_date": row[0],
        "generated_at": row[1],
        "strategy_version": row[2],
        "sentiment": json.loads(row[3]),
        "sectors": json.loads(row[4]),
        "candidates": json.loads(row[5]),
        "markdown": row[6],
        "watchlist": _parse_watchlist_json(row[7]),
        "intel": _parse_intel_json(row[8]),
        "sector_moneyflow": _parse_sector_moneyflow_json(row[9]),
        "news_alerts_scan": _parse_news_alerts_scan_json(row[10]),
        # v1.4-①-C:老报告行(建于本列之前)补列后取默认 '{}' → 读回空 dict,
        # 客户端按「空 = 该版本还没有新鲜度概念」处理,不是「新鲜」。
        "data_freshness": _parse_json_field(row[11], {}),
        # V2-⑭-A:篮子日报快照(老报告行补列后取默认 '{}' → 读回空 dict,
        # 客户端按「该版本还没有篮子日报概念」处理,不是「今天没有篮子」)。
        "basket_daily": _parse_json_field(row[12], {}),
    }


def latest_report_date(db_path: Optional[Path] = None) -> Optional[str]:
    """最新一份报告的 `trade_date`('YYYYMMDD'),供 `GET /report/latest`。库里从未
    生成过报告 → None(HTTP 层据此返 degraded 空态,不 500)。防御性 `init_schema`
    同 `load_report`——查一个全新库是完全正常的场景。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute("SELECT MAX(trade_date) FROM reports").fetchone()
    return row[0] if row and row[0] else None


def load_report_by_str(trade_date_str: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """按 'YYYYMMDD' 字符串直接查报告(免调用方再拼 `date` 对象)。语义同 `load_report`。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT trade_date, generated_at, strategy_version, sentiment_json, sectors_json, candidates_json, markdown, "
            "watchlist_json, intel_json, sector_moneyflow_json, news_alerts_scan_json, "
            "data_freshness_json, basket_daily_json FROM reports WHERE trade_date=?",
            (trade_date_str,),
        ).fetchone()
    if row is None:
        return None
    return {
        "trade_date": row[0],
        "generated_at": row[1],
        "strategy_version": row[2],
        "sentiment": json.loads(row[3]),
        "sectors": json.loads(row[4]),
        "candidates": json.loads(row[5]),
        "markdown": row[6],
        "watchlist": _parse_watchlist_json(row[7]),
        "intel": _parse_intel_json(row[8]),
        "sector_moneyflow": _parse_sector_moneyflow_json(row[9]),
        "news_alerts_scan": _parse_news_alerts_scan_json(row[10]),
        # v1.4-①-C:老报告行(建于本列之前)补列后取默认 '{}' → 读回空 dict,
        # 客户端按「空 = 该版本还没有新鲜度概念」处理,不是「新鲜」。
        "data_freshness": _parse_json_field(row[11], {}),
        # V2-⑭-A:篮子日报快照(老报告行补列后取默认 '{}' → 读回空 dict,
        # 客户端按「该版本还没有篮子日报概念」处理,不是「今天没有篮子」)。
        "basket_daily": _parse_json_field(row[12], {}),
    }


def load_llm_judgments(trade_date: date, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    # ⚠ **V2-⑬-2 起本表停写留档**:写函数 `save_llm_judgment`/`delete_llm_judgments`
    # 已物理删除,本函数只服务历史行的归因只读(`/report` 读历史报告时仍会 live join)。
    """同 `load_report` 的防御性 `init_schema` 理由:查询一个还没审判过的交易日
    是正常场景,不应因表未建过而崩。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT ts_code, provider, model, verdict, narrative, degraded, degrade_reason, "
            "search_hits_json, search_engine, created_at "
            "FROM llm_judgments WHERE trade_date=? ORDER BY id",
            (_d(trade_date),),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "ts_code": r[0],
            "provider": r[1],
            "model": r[2],
            "verdict": r[3],
            "narrative": r[4],
            "degraded": bool(r[5]),
            "degrade_reason": r[6],
            "search_hits": json.loads(r[7]),
            "search_engine": r[8],   # None=老行未记录 / 未开搜索 / 调用未成功
            "created_at": r[9],
        })
    return out


__all__ = [
    "save_report", "load_report", "load_report_by_str", "latest_report_date", "load_llm_judgments",
]
