"""V2.5.1 的去敏 LLM 用量账。

这个模块只保存厂商实际回传的用量和 Tavily credits；不估算 Token，不保存 prompt、
密钥、请求头、请求 ID 或搜索原文。写入只由批处理调用点触发，所有读路径零 DDL。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from neckline.db import connection, readonly_tables
from neckline.llm.base import LLMResult


TASKS = ("market_direction", "news_scan", "explain", "playbook")


def _day(value: Optional[date]) -> Optional[str]:
    return value.strftime("%Y%m%d") if value is not None else None


def record(
    *,
    task: str,
    result: Optional[LLMResult] = None,
    trade_date: Optional[date] = None,
    report_date: Optional[date] = None,
    pack_id: Optional[str] = None,
    outcome: Optional[str] = None,
    tavily_credits: Optional[int] = None,
    searched: bool = False,
    duration_ms: Optional[int] = None,
    failure_reason: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> None:
    """追加一条调用事实。调用失败/未配置也可记录，但绝不伪装为成功。"""
    if task not in TASKS:
        raise ValueError(f"未知 LLM 任务:{task}")
    # 领域函数的纯单测可不注入交易日/数据库；它们不应意外写默认工作库。
    if trade_date is None and report_date is None and pack_id is None and db_path is None:
        return
    # 计量是附加审计，不能让旧测试库或未迁移的只读场景因一张新表而中断既有链路。
    # 这里仅探测表是否存在，不执行 DDL；正式启动迁移后的库一定会进入下面的写入。
    with readonly_tables("llm_usage_events", db_path=db_path) as read_conn:
        if read_conn is None:
            return
    resolved_outcome = outcome or ("success" if result is not None and result.ok else "failed")
    reason = failure_reason or (None if result is None or result.ok else result.reason)
    usage_unavailable = True if result is None else bool(result.usage_unavailable)
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO llm_usage_events (trade_date, report_date, pack_id, task, provider, model, "
            "outcome, prompt_tokens, completion_tokens, total_tokens, usage_unavailable, tavily_credits, "
            "searched, duration_ms, failure_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _day(trade_date), _day(report_date), pack_id, task,
                None if result is None else result.provider or None,
                None if result is None else result.model or None,
                resolved_outcome,
                None if result is None else result.prompt_tokens,
                None if result is None else result.completion_tokens,
                None if result is None else result.total_tokens,
                int(usage_unavailable), tavily_credits, int(searched), duration_ms, reason,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )


def summary(*, days: int = 5, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """只读聚合最近 1–35 个有记录的交易日；不泄漏调用材料。"""
    n = max(1, min(int(days or 5), 35))
    with readonly_tables("llm_usage_events", db_path=db_path) as conn:
        if conn is None:
            return {"days": [], "totals": _empty_totals()}
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT COALESCE(report_date, trade_date) FROM llm_usage_events "
            "WHERE COALESCE(report_date, trade_date) IS NOT NULL "
            "ORDER BY COALESCE(report_date, trade_date) DESC LIMIT ?", (n,)
        ).fetchall()]
        if not dates:
            return {"days": [], "totals": _empty_totals()}
        placeholders = ",".join("?" for _ in dates)
        rows = conn.execute(
            "SELECT COALESCE(report_date, trade_date) AS day, task, COUNT(*) AS calls, "
            "SUM(CASE WHEN outcome='failed' THEN 1 ELSE 0 END) AS failures, "
            "SUM(CASE WHEN usage_unavailable=1 THEN 1 ELSE 0 END) AS unavailable, "
            "SUM(prompt_tokens) AS prompt_tokens, SUM(completion_tokens) AS completion_tokens, "
            "SUM(total_tokens) AS total_tokens, SUM(tavily_credits) AS tavily_credits, "
            "SUM(duration_ms) AS duration_ms FROM llm_usage_events "
            f"WHERE COALESCE(report_date, trade_date) IN ({placeholders}) "
            "GROUP BY day, task ORDER BY day DESC, task ASC", tuple(dates),
        ).fetchall()
    by_day: Dict[str, Dict[str, Any]] = {d: {"date": d, "tasks": [], "totals": _empty_totals()} for d in dates}
    totals = _empty_totals()
    for row in rows:
        item = {
            "task": row[1], "calls": int(row[2] or 0), "failed": int(row[3] or 0),
            "usageUnavailable": int(row[4] or 0), "promptTokens": row[5],
            "completionTokens": row[6], "totalTokens": row[7], "tavilyCredits": row[8],
            "durationMs": row[9],
        }
        by_day[row[0]]["tasks"].append(item)
        _add(by_day[row[0]]["totals"], item)
        _add(totals, item)
    return {"days": [by_day[d] for d in dates], "totals": totals}


def _empty_totals() -> Dict[str, Any]:
    return {"calls": 0, "failed": 0, "usageUnavailable": 0, "promptTokens": None,
            "completionTokens": None, "totalTokens": None, "tavilyCredits": None, "durationMs": None}


def _add(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    for key in ("calls", "failed", "usageUnavailable"):
        target[key] = int(target[key] or 0) + int(source[key] or 0)
    for key in ("promptTokens", "completionTokens", "totalTokens", "tavilyCredits", "durationMs"):
        if source[key] is None:
            continue
        target[key] = int(target[key] or 0) + int(source[key])


__all__ = ["TASKS", "record", "summary"]
