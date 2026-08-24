"""API 层设备台账存取。极简 CRUD、幂等，使用 stdlib sqlite3，不引入 ORM。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from neckline.db import connection, init_schema, readonly_connection


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
    """全部已注册 device token。"""
    try:
        with readonly_connection(db_path) as conn:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='devices'"
            ).fetchone():
                return []
            rows = conn.execute("SELECT token FROM devices ORDER BY created_at").fetchall()
    except FileNotFoundError:
        return []
    return [r[0] for r in rows]


def delete_device(token: str, db_path: Optional[Path] = None) -> bool:
    """只供 APNs 明确永久失效响应调用；删除是幂等的。"""
    with connection(db_path) as conn:
        cur = conn.execute("DELETE FROM devices WHERE token=?", (token,))
        return cur.rowcount > 0


__all__ = [
    "upsert_device", "list_device_tokens", "delete_device",
]
