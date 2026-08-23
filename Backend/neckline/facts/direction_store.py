"""冻结事实包的方向背景 sidecar；读写均不接触 K9。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from neckline.db import connection, readonly_tables


TERMINAL_STATES = ("ready", "unavailable", "not_attempted")
RUNNING_STATE = "running"


def _now(*, precise: bool = False) -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="microseconds" if precise else "seconds")


def load(pack_id: str, *, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    with readonly_tables("fact_direction_briefings", db_path=db_path) as conn:
        if conn is None:
            return None
        row = conn.execute(
            "SELECT pack_id, trade_date, state, summary, themes_json, provider, model, evidence_count, failure_reason, created_at "
            "FROM fact_direction_briefings WHERE pack_id=?", (pack_id,)
        ).fetchone()
    if row is None:
        return None
    return {"packId": row[0], "tradeDate": row[1], "state": row[2], "summary": row[3],
            "themes": json.loads(row[4] or "[]"), "provider": row[5], "model": row[6],
            "evidenceCount": int(row[7] or 0), "failureReason": row[8], "createdAt": row[9]}


def save_once(
    *, pack_id: str, trade_date: str, state: str, summary: str = "", themes: list[dict] | None = None,
    provider: Optional[str] = None, model: Optional[str] = None, evidence_count: int = 0,
    failure_reason: Optional[str] = None, db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    if state not in TERMINAL_STATES:
        raise ValueError(f"非法 direction state:{state}")
    with connection(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO fact_direction_briefings "
            "(pack_id, trade_date, state, summary, themes_json, provider, model, evidence_count, failure_reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pack_id, trade_date, state, summary.strip(), json.dumps(themes or [], ensure_ascii=False),
             provider, model, int(evidence_count), failure_reason,
             _now()),
        )
    return load(pack_id, db_path=db_path) or {}


def claim(
    *, pack_id: str, trade_date: str, db_path: Optional[Path] = None,
) -> tuple[bool, Dict[str, Any]]:
    """原子取得一份方向 sidecar 的唯一外部调用权。

    这不是普通的「先读再写」幂等：领取记录在 provider/Tavily 调用**之前**落库，
    因此两个晚间任务并发时，只有成功插入 ``running`` 的那个任务可以发出外部请求。
    已有 ``running`` 代表前一个任务尚未完成或异常中断；后来者保守地复读，不替它
    自动重试，以免一个不确定的崩溃边界演变成重复收费。
    """
    with connection(db_path) as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO fact_direction_briefings "
            "(pack_id, trade_date, state, summary, themes_json, provider, model, evidence_count, failure_reason, created_at) "
            "VALUES (?, ?, ?, '', '[]', NULL, NULL, 0, NULL, ?)",
            (pack_id, trade_date, RUNNING_STATE, _now()),
        )
        claimed = cursor.rowcount == 1
    return claimed, load(pack_id, db_path=db_path) or {}


def complete_claim(
    *, pack_id: str, claim_created_at: str, state: str, summary: str = "",
    themes: list[dict] | None = None,
    provider: Optional[str] = None, model: Optional[str] = None, evidence_count: int = 0,
    failure_reason: Optional[str] = None, db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """完成已领取的 sidecar；只允许持有当前 fencing token 的进程结案。"""
    if state not in TERMINAL_STATES:
        raise ValueError(f"非法 direction terminal state:{state}")
    with connection(db_path) as conn:
        conn.execute(
            "UPDATE fact_direction_briefings SET state=?, summary=?, themes_json=?, provider=?, model=?, "
            "evidence_count=?, failure_reason=? WHERE pack_id=? AND state=? AND created_at=?",
            (state, summary.strip(), json.dumps(themes or [], ensure_ascii=False), provider, model,
             int(evidence_count), failure_reason, pack_id, RUNNING_STATE, claim_created_at),
        )
    return load(pack_id, db_path=db_path) or {}


def settle_running(
    *, pack_id: str, expected_created_at: str, reason: str,
    db_path: Optional[Path] = None,
) -> tuple[bool, Dict[str, Any]]:
    """把一个精确指认的崩溃遗留 claim 人工结案为不可用；不触发外部调用。"""
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValueError("人工结案必须填写原因")
    with connection(db_path) as conn:
        cursor = conn.execute(
            "UPDATE fact_direction_briefings SET state='unavailable', failure_reason=? "
            "WHERE pack_id=? AND state=? AND created_at=?",
            (f"人工结案：{clean_reason}", pack_id, RUNNING_STATE, expected_created_at),
        )
        changed = cursor.rowcount == 1
    return changed, load(pack_id, db_path=db_path) or {}


def reclaim_running(
    *, pack_id: str, expected_created_at: str, reason: str,
    db_path: Optional[Path] = None,
) -> tuple[bool, Dict[str, Any]]:
    """显式接管一个精确指认的 running claim，给人工授权重试建立新的 fencing token。

    ``created_at`` 同时充当本次 claim token：旧进程即使稍后返回，也无法用旧 token
    覆盖人工重试的结果。这里只接管、不调用 provider；外部费用只能由管理 CLI 的二次
    确认路径触发。
    """
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValueError("人工重试必须填写原因")
    new_created_at = _now(precise=True)
    with connection(db_path) as conn:
        cursor = conn.execute(
            "UPDATE fact_direction_briefings SET summary='', themes_json='[]', provider=NULL, "
            "model=NULL, evidence_count=0, failure_reason=?, created_at=? "
            "WHERE pack_id=? AND state=? AND created_at=?",
            (f"人工授权重试：{clean_reason}", new_created_at, pack_id,
             RUNNING_STATE, expected_created_at),
        )
        changed = cursor.rowcount == 1
    return changed, load(pack_id, db_path=db_path) or {}


__all__ = [
    "TERMINAL_STATES", "RUNNING_STATE", "claim", "complete_claim", "load", "save_once",
    "settle_running", "reclaim_running",
]
