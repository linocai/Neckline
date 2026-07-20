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
    db_path: Optional[Path] = None,
) -> None:
    init_schema(db_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connection(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reports "
            "(trade_date, generated_at, strategy_version, sentiment_json, sectors_json, candidates_json, markdown) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                _d(trade_date),
                now,
                strategy_version,
                json.dumps(sentiment, ensure_ascii=False),
                json.dumps(sectors, ensure_ascii=False),
                json.dumps(candidates, ensure_ascii=False),
                markdown,
            ),
        )


def load_report(trade_date: date, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """查某交易日的报告。查一个"从未生成过报告"的日期是完全正常的场景(如尚未
    到 16:00、当天非交易日、或报告脚本还没跑过)——防御性 `init_schema`,免得
    在从未写过库的全新 DB 上直接炸 `OperationalError: no such table`。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT trade_date, generated_at, strategy_version, sentiment_json, sectors_json, candidates_json, markdown "
            "FROM reports WHERE trade_date=?",
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
            "SELECT trade_date, generated_at, strategy_version, sentiment_json, sectors_json, candidates_json, markdown "
            "FROM reports WHERE trade_date=?",
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
    }


def save_llm_judgment(trade_date: date, result: JudgeResult, db_path: Optional[Path] = None) -> None:
    init_schema(db_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    hits_json = json.dumps([asdict(h) for h in result.search_hits], ensure_ascii=False)
    with connection(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO llm_judgments "
            "(trade_date, ts_code, provider, model, verdict, narrative, degraded, degrade_reason, search_hits_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
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
                now,
            ),
        )


def load_llm_judgments(trade_date: date, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """同 `load_report` 的防御性 `init_schema` 理由:查询一个还没审判过的交易日
    是正常场景,不应因表未建过而崩。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT ts_code, provider, model, verdict, narrative, degraded, degrade_reason, search_hits_json, created_at "
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
            "created_at": r[8],
        })
    return out


__all__ = [
    "save_report", "load_report", "load_report_by_str", "latest_report_date",
    "save_llm_judgment", "load_llm_judgments",
]
