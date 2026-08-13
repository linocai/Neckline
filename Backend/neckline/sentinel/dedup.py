"""哨兵事件防重(plan 阶段3 工程要求「状态防重推(同一事件不轰炸,推过记账;
进程重启不重复推当日已推事件)」)。SQLite `sentinel_events` 表落库,天然跨进程
重启存活——不是内存态去重(内存态在脚本重启后会清零,达不到"进程重启不重复推"
的要求)。

去重粒度 `(trade_date, sentinel, ts_code, event_key)`:
    · 买点哨兵:`ts_code` = 候选代码,`event_key` 固定 "trigger"(一天只提醒一次
      "买点条件成立",不因盘中反复满足条件而重复推)。
    · 证伪哨兵:同买点,`event_key` 固定 "trigger"。
    · 持仓哨兵:`ts_code` = 持仓代码,`event_key` ∈ {"stop_approach","take_profit",
      "sector_dive"}——同一票的三种风险是独立事件,各自最多推一次,不互相抑制。
    · 退潮哨兵:市场级事件,无单票语义,`ts_code=""`,`event_key` 固定 "brake"。
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
    trade_date: date, sentinel: str, ts_code: str, event_key: str, db_path: Optional[Path] = None
) -> bool:
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM sentinel_events WHERE trade_date=? AND sentinel=? AND ts_code=? AND event_key=?",
            (_d(trade_date), sentinel, ts_code, event_key),
        ).fetchone()
    return row is not None


def record_pushed(
    trade_date: date,
    sentinel: str,
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
            "INSERT OR IGNORE INTO sentinel_events "
            "(trade_date, sentinel, ts_code, event_key, payload_json, pushed_at) VALUES (?,?,?,?,?,?)",
            (_d(trade_date), sentinel, ts_code, event_key, json.dumps(payload or {}, ensure_ascii=False), now),
        )


def load_events_for_date(trade_date: date, db_path: Optional[Path] = None) -> list:
    """读某交易日已落库的哨兵事件(供 `GET /board` 聚合,plan 4A.3)。返回按推送时间
    升序的 dict 列表 `{sentinel, ts_code, event_key, payload, pushed_at}`——**看板只读**,
    哨兵引擎已把每次推送落进 `sentinel_events`,看板不重算、不触发任何新判断。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT sentinel, ts_code, event_key, payload_json, pushed_at "
            "FROM sentinel_events WHERE trade_date=? ORDER BY pushed_at, id",
            (_d(trade_date),),
        ).fetchall()
    out = []
    for r in rows:
        try:
            payload = json.loads(r[3]) if r[3] else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        out.append({
            "sentinel": r[0], "ts_code": r[1], "event_key": r[2],
            "payload": payload, "pushed_at": r[4],
        })
    return out


def retreat_brake_state(trade_date: date, db_path: Optional[Path] = None) -> Optional[dict]:
    """某交易日是否已触发退潮红色刹车(plan 4A.3 看板顶置红条 / 2.4 拍板)。触发过 →
    `{active:True, reason:<刹车文案>, pushed_at:...}`;未触发 → None(HTTP 层映射 active:False)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT payload_json, pushed_at FROM sentinel_events "
            "WHERE trade_date=? AND sentinel='retreat' AND ts_code='' AND event_key='brake'",
            (_d(trade_date),),
        ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row[0]) if row[0] else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return {"active": True, "reason": payload.get("body", ""), "pushed_at": row[1]}


def count_pushed_today(trade_date: date, sentinel: Optional[str] = None, db_path: Optional[Path] = None) -> int:
    """今日已推事件计数(供哨兵脚本收盘时打摘要日志用)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        if sentinel is None:
            row = conn.execute(
                "SELECT COUNT(*) FROM sentinel_events WHERE trade_date=?", (_d(trade_date),)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM sentinel_events WHERE trade_date=? AND sentinel=?",
                (_d(trade_date), sentinel),
            ).fetchone()
    return int(row[0]) if row else 0


__all__ = [
    "already_pushed", "record_pushed", "count_pushed_today",
    "load_events_for_date", "retreat_brake_state",
]
