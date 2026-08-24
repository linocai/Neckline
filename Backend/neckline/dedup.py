"""9:26 竞价核对与 10:00 结算任务的跨进程防重台账。

记录落在中性的 ``job_events`` 表，仅覆盖当前核对任务。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from neckline.db import connection, init_schema


def _d(trade_date: date) -> str:
    return trade_date.strftime("%Y%m%d")


def already_pushed(
    trade_date: date, scope: str, ts_code: str, event_key: str, db_path: Optional[Path] = None
) -> bool:
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM job_events WHERE trade_date=? AND scope=? AND ts_code=? AND event_key=?",
            (_d(trade_date), scope, ts_code, event_key),
        ).fetchone()
    return row is not None


def record_pushed(
    trade_date: date,
    scope: str,
    ts_code: str,
    event_key: str,
    payload: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> None:
    """记一条已推事件。`INSERT OR IGNORE`——并发/重复调用时静默忽略(唯一约束已
    保证幂等,不需要报错)。"""
    init_schema(db_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connection(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO job_events "
            "(trade_date, scope, ts_code, event_key, payload_json, pushed_at) VALUES (?,?,?,?,?,?)",
            (_d(trade_date), scope, ts_code, event_key, json.dumps(payload or {}, ensure_ascii=False), now),
        )


__all__ = [
    "already_pushed", "record_pushed",
]
