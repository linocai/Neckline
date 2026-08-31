"""9:26 竞价核对与 10:00 结算任务的跨进程防重台账。

记录落在中性的 ``job_events`` 表，仅覆盖当前核对任务。
"""

from __future__ import annotations

import json
import hashlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from neckline.db import connection, init_schema, readonly_tables


def _d(trade_date: date) -> str:
    return trade_date.strftime("%Y%m%d")


def already_pushed(
    trade_date: date, scope: str, ts_code: str, event_key: str, db_path: Optional[Path] = None
) -> bool:
    with readonly_tables("job_events.trade_date", db_path=db_path) as conn:
        row = None if conn is None else conn.execute(
            "SELECT 1 FROM job_events WHERE trade_date=? AND scope=? AND ts_code=? AND event_key=?",
            (_d(trade_date), scope, ts_code, event_key),
        ).fetchone()
    return row is not None


def device_delivery_key(device_token: str) -> str:
    """Stable non-secret identity for a device token in the delivery ledger."""
    return hashlib.sha256(device_token.encode("utf-8")).hexdigest()


def delivered_device_keys(
    trade_date: date, scope: str, ts_code: str, event_key: str, db_path: Optional[Path] = None
) -> set[str]:
    """Read the successful fanout members without triggering schema writes."""
    with readonly_tables("job_event_deliveries.device_key", db_path=db_path) as conn:
        rows = [] if conn is None else conn.execute(
            "SELECT device_key FROM job_event_deliveries "
            "WHERE trade_date=? AND scope=? AND ts_code=? AND event_key=?",
            (_d(trade_date), scope, ts_code, event_key),
        ).fetchall()
    return {str(row[0]) for row in rows}


def record_device_delivered(
    trade_date: date,
    scope: str,
    ts_code: str,
    event_key: str,
    device_key: str,
    db_path: Optional[Path] = None,
) -> None:
    """Append one successful device delivery; raw APNs tokens are never copied."""
    init_schema(db_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connection(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO job_event_deliveries "
            "(trade_date,scope,ts_code,event_key,device_key,delivered_at) VALUES (?,?,?,?,?,?)",
            (_d(trade_date), scope, ts_code, event_key, device_key, now),
        )


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
    "already_pushed", "record_pushed", "device_delivery_key",
    "delivered_device_keys", "record_device_delivered",
]
