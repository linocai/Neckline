"""API 层业务台账存取(plan 4A.4/4A.5):`devices`(APNs 注册)+ `inquiry_pool`
(问询台海选票)。极简 CRUD,幂等——沿本项目既有 store 姿势(`report/store.py`/
`sentinel/dedup.py`),stdlib sqlite3 直连,不引 ORM。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from neckline.db import connection, init_schema


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


# —— devices(APNs 设备注册,plan 4A.5 / 4B.5)——————————————————————————————

def upsert_device(token: str, platform: str = "ios", db_path: Optional[Path] = None) -> None:
    """注册/更新一个 APNs device token(复用 LinoN `upsert_device_token` 语义)。同一
    token 再注册只刷新 `updated_at`,不重复建行。空 token → 静默忽略(客户端首次授权前
    可能拿不到 token,不应因此报错)。"""
    token = (token or "").strip()
    if not token:
        return
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO devices (token, platform, created_at, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(token) DO UPDATE SET platform=excluded.platform, updated_at=excluded.updated_at",
            (token, (platform or "ios").strip() or "ios", now, now),
        )


def list_device_tokens(db_path: Optional[Path] = None) -> List[str]:
    """全部已注册 device token(16:00 报告 / 退潮刹车推送时遍历)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute("SELECT token FROM devices ORDER BY created_at").fetchall()
    return [r[0] for r in rows]


# —— inquiry_pool(问询台海选池,plan §2.5 / 4A.5)——————————————————————————

def add_to_inquiry_pool(
    trade_date: date, ts_code: str, name: Optional[str] = None,
    reason: Optional[str] = None, db_path: Optional[Path] = None,
) -> None:
    """把「初审通过」的票纳入某交易日的海选池(供当晚 `report.py` 扩候选 universe,§2.5)。
    `INSERT OR IGNORE`——同日同票复问不重复入池(UNIQUE(trade_date, ts_code) 幂等)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO inquiry_pool (trade_date, ts_code, name, reason, created_at) "
            "VALUES (?,?,?,?,?)",
            (_d(trade_date), ts_code, name, reason, _now()),
        )


def load_inquiry_pool(trade_date: date, db_path: Optional[Path] = None) -> List[dict]:
    """某交易日海选池的票(供当晚报告扩 universe / 审计)。返回 `{ts_code, name, reason,
    created_at}` 列表,按入池时间升序。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT ts_code, name, reason, created_at FROM inquiry_pool "
            "WHERE trade_date=? ORDER BY created_at, ts_code",
            (_d(trade_date),),
        ).fetchall()
    return [{"ts_code": r[0], "name": r[1], "reason": r[2], "created_at": r[3]} for r in rows]


__all__ = [
    "upsert_device", "list_device_tokens",
    "add_to_inquiry_pool", "load_inquiry_pool",
]
