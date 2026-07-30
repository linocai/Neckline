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
    watchlist: Optional[List[Dict[str, Any]]] = None,
    intel: Optional[Dict[str, Any]] = None,
    sector_moneyflow: Optional[Dict[str, Any]] = None,
    news_alerts_scan: Optional[List[Dict[str, Any]]] = None,
    data_freshness: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> None:
    """`watchlist`(v1.1-C.3 自选体检快照,`WatchlistCheckItem.public_dict()` 列表):
    默认 `None` → 落 `'[]'`(旧调用点/自选池为空时零改动落库形状)。
    `intel`/`sector_moneyflow`(v1.3-③ C1/C2,`IntelReport.to_public_dict()` /
    `SectorMoneyflowReport.to_public_dict()` 的字典,均为**单个对象**而非数组——
    已是 camelCase JSON-safe 形状,`sector_moneyflow` 携带 available/
    unavailableReason 等元信息,不是裸榜单):默认 `None` → 落 `'{}'`(旧调用点零
    改动落库形状,同 watchlist 惯例)。
    `news_alerts_scan`(v1.3-③-C4,`NewsAlertsReport.scan_statuses_public()` 的
    JSON 数组快照——**只是扫描状态元信息,不含命中告警本身**〔告警条目落独立
    `news_alerts` 表,见 `report/news_alerts_store.py`〕):默认 `None` → 落
    `'[]'`,同 watchlist 惯例。
    `data_freshness`(v1.4-①-C,`SectorDataFreshness.to_public_dict()`:
    `{sectorDataDate, sectorLagDays, stale}`):板块数据相对本报告日落后几个交易日 ——
    **随报告一起冻住**,不在读时重算(读一份三天前的报告时,该看到的是**当时**的新鲜度,
    不是今天的)。默认 `None` → 落 `'{}'`,同 intel 惯例。"""
    init_schema(db_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connection(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reports "
            "(trade_date, generated_at, strategy_version, sentiment_json, sectors_json, candidates_json, markdown, "
            "watchlist_json, intel_json, sector_moneyflow_json, news_alerts_scan_json, data_freshness_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                _d(trade_date),
                now,
                strategy_version,
                json.dumps(sentiment, ensure_ascii=False),
                json.dumps(sectors, ensure_ascii=False),
                json.dumps(candidates, ensure_ascii=False),
                markdown,
                json.dumps(watchlist or [], ensure_ascii=False),
                json.dumps(intel or {}, ensure_ascii=False),
                json.dumps(sector_moneyflow or {}, ensure_ascii=False),
                json.dumps(news_alerts_scan or [], ensure_ascii=False),
                json.dumps(data_freshness or {}, ensure_ascii=False),
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
            "data_freshness_json FROM reports WHERE trade_date=?",
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
            "data_freshness_json FROM reports WHERE trade_date=?",
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
    }


def load_watchlist_snapshot_before(trade_date: date, db_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """`trade_date` **之前**(严格早于,`<`)最近一份已生成报告的自选体检快照,
    按 `ts_code` 建索引(供 `watchlist_check.apply_llm_review` 的「状态变化」diff
    用,§v1.1-C.3)。**用 `<` 而非「上一个交易日」**——同日补跑报告(`build_report`
    对同一天重新生成)时,「上一份」仍应是更早交易日的快照,不能拿"即将被本次
    覆盖的同一天旧值"当基准,否则同日重复生成会让所有票的 diff 基准变成"自己
    生成前的自己",从而永远判定"未变化"、漏掉真实状态变化的 LLM 审视。查无 →
    空 dict(视为「首次出现」,`_is_changed` 据此把首次出现按"已变化"处理)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT watchlist_json FROM reports WHERE trade_date < ? ORDER BY trade_date DESC LIMIT 1",
            (_d(trade_date),),
        ).fetchone()
    if row is None:
        return {}
    items = _parse_watchlist_json(row[0])
    return {it["ts_code"]: it for it in items if isinstance(it, dict) and it.get("ts_code")}


def save_llm_judgment(trade_date: date, result: JudgeResult, db_path: Optional[Path] = None) -> None:
    init_schema(db_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    hits_json = json.dumps([asdict(h) for h in result.search_hits], ensure_ascii=False)
    with connection(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO llm_judgments "
            "(trade_date, ts_code, provider, model, verdict, narrative, degraded, degrade_reason, "
            "search_hits_json, search_engine, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                _d(trade_date),
                result.ts_code,
                result.provider,
                result.model,
                result.verdict,
                result.narrative,
                1 if result.degraded else 0,
                result.degrade_reason,
                hits_json,
                result.search_engine,   # v1.5-④-A3(§七 P1-7):None=未记录,不回填猜测
                now,
            ),
        )


def delete_llm_judgments(trade_date: date, ts_codes: List[str], db_path: Optional[Path] = None) -> int:
    """删掉当日这批码的审判行,返回删除行数(v1.5.1,契约线 review 🟡-1 的写侧收口)。

    **为什么需要**:`/report` 端点的审判是**从本表现连**的(`app.py::_shape_candidate`
    拿 `load_llm_judgments` 的结果),而 `judge_skipped` 只是打在候选快照上的标。同日
    重跑(第一跑审完 20 只 → 补跑时预算在第 10 只耗尽)会让 11~20 号同时带着「第一跑
    的审判结论」和「本次预算耗尽未发起」——`judgeSkipped` 的全部价值就是诚实分辨"为
    什么没审",两句话打架时它恰好在撒谎。收口在**写侧**(本函数)而不是读侧遮蔽:
    藏真数据不是诚实,该没有的行就该真没有。

    幂等(删不存在的行 = 0 行,不报错);空名单直接返回 0、不建连。批量一条 DELETE
    语句 = 单事务,不逐码开事务。"""
    codes = [c for c in dict.fromkeys(ts_codes) if c]
    if not codes:
        return 0
    init_schema(db_path)
    with connection(db_path) as conn:
        cur = conn.execute(
            f"DELETE FROM llm_judgments WHERE trade_date=? AND ts_code IN ({','.join('?' * len(codes))})",
            (_d(trade_date), *codes),
        )
        return cur.rowcount


def load_llm_judgments(trade_date: date, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
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
    "save_report", "load_report", "load_report_by_str", "latest_report_date",
    "load_watchlist_snapshot_before",
    "save_llm_judgment", "load_llm_judgments", "delete_llm_judgments",
]
