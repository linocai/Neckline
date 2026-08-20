"""当日只跑一次的防重台账(V2.5.0 S1:自 `sentinel/dedup.py` 原样搬入)。

🔴 **包没了,表名留着**:`neckline/sentinel/` 整包已在 V2.5.0 S1 物理删除,本模块
搬到 `neckline/` 顶层。SQLite 表名**仍是 `sentinel_events`,⛔ 不改名** ——
改名 = 一次迁移风险换零产品价值(PROJECT_PLAN §3.2 定死)。读到"sentinel"这个词
只应联想到"这张表历史上叫这个名字",不再有任何盘中哨兵语义。

现役消费方(V2.5.0):`auction/pipeline.py` 的 9:26 竞价核对表当日防重(市场级 key
`(trade_date, "auction", "", "tick")`),以及 S8 将接入的 10:00 结算拍。

落 SQLite 而非内存态:天然跨进程重启存活 —— 内存态在服务重启后清零,达不到
"进程重启不重复跑当日已跑的那一拍"的要求。

去重粒度 `(trade_date, sentinel, ts_code, event_key)`。历史上的四类盘中哨兵取值
(买点 / 证伪 / 持仓 / 退潮)已随整包退役,不再产生新行;`sentinel_events` 里的
历史行按裁定 6 保留、只读、不迁移、不回填。
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
