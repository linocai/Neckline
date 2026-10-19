"""KX 三张表的读写：名单、推荐、日线统计缓存。时间存 UTC ISO（到秒），交易日存 YYYY-MM-DD。"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Sequence

from neckline.morning.db import utc


@dataclass(frozen=True)
class Member:
    code: str
    name: str
    industry: str
    position: int


@dataclass(frozen=True)
class PoolSnapshot:
    version: str
    members: tuple[Member, ...]
    verified_at: datetime


@dataclass(frozen=True)
class PickRecord:
    request_id: str
    codes: tuple[str, ...]
    picked_at: datetime
    trade_date: date


# ------------------------------------------------------------------ 名单

def load_pool(conn: sqlite3.Connection) -> PoolSnapshot | None:
    rows = conn.execute("SELECT code, name, industry, position, version, verified_at FROM kx_pool "
                        "ORDER BY position").fetchall()
    if not rows:
        return None
    versions = {row["version"] for row in rows}
    if len(versions) != 1:  # 整体替换在同一个事务里，理论上不会出现；出现就当名单不可信
        return None
    return PoolSnapshot(
        version=rows[0]["version"],
        members=tuple(Member(r["code"], r["name"], r["industry"], r["position"]) for r in rows),
        verified_at=max(datetime.fromisoformat(r["verified_at"]) for r in rows))


def replace_pool(conn: sqlite3.Connection, *, version: str, members: Sequence[Member], verified_at: datetime) -> None:
    """同一个事务里整体替换名单（调用方给的是 db.write 的连接，退出时一起提交）。"""
    conn.execute("DELETE FROM kx_pool")
    conn.executemany("INSERT INTO kx_pool(code, position, name, industry, version, verified_at) VALUES (?,?,?,?,?,?)",
                     [(m.code, index, m.name, m.industry, version, utc(verified_at))
                      for index, m in enumerate(members)])


def touch_pool(conn: sqlite3.Connection, *, version: str, verified_at: datetime) -> int:
    """名单与 KX 核对一致时只更新核对时间；版本对不上返回 0。"""
    return conn.execute("UPDATE kx_pool SET verified_at=? WHERE version=?", (utc(verified_at), version)).rowcount


# ------------------------------------------------------------------ 推荐

def find_request(conn: sqlite3.Connection, request_id: str) -> PickRecord | None:
    rows = conn.execute("SELECT code, picked_at, trade_date FROM kx_picks WHERE request_id=? ORDER BY position",
                        (request_id,)).fetchall()
    if not rows:
        return None
    return PickRecord(request_id, tuple(r["code"] for r in rows), datetime.fromisoformat(rows[0]["picked_at"]),
                      date.fromisoformat(rows[0]["trade_date"]))


def insert_picks(conn: sqlite3.Connection, *, request_id: str, codes: Sequence[str], picked_at: datetime,
                 trade_date: date, received_at: datetime) -> None:
    conn.executemany(
        "INSERT INTO kx_picks(request_id, position, code, picked_at, trade_date, received_at) VALUES (?,?,?,?,?,?)",
        [(request_id, index, code, utc(picked_at), trade_date.isoformat(), utc(received_at))
         for index, code in enumerate(codes)])


@dataclass(frozen=True)
class LatestPick:
    picked_at: datetime
    trade_date: date
    position: int  # 在那次请求里的次序，同一时刻的几只按它排


def latest_picks(conn: sqlite3.Connection) -> tuple[dict[str, LatestPick], datetime | None]:
    """每只票最近一次推荐，以及最后一次收到推荐的时间。"""
    latest: dict[str, LatestPick] = {}
    last_at: datetime | None = None
    rows = conn.execute("SELECT code, picked_at, trade_date, position FROM kx_picks "
                        "ORDER BY picked_at, request_id, position")
    for row in rows:
        picked = datetime.fromisoformat(row["picked_at"])
        latest[row["code"]] = LatestPick(picked, date.fromisoformat(row["trade_date"]), row["position"])
        last_at = picked
    return latest, last_at


# ------------------------------------------------------------------ 日线统计缓存

def get_stats(conn: sqlite3.Connection, *, base_date: date, version: str, kind: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT payload_json FROM kx_daily_stats WHERE base_date=? AND pool_version=? AND kind=?",
                       (base_date.isoformat(), version, kind)).fetchone()
    return json.loads(row["payload_json"]) if row else None


def put_stats(conn: sqlite3.Connection, *, base_date: date, version: str, kind: str, fetched_at: datetime,
              payload: dict[str, Any]) -> None:
    conn.execute("INSERT OR REPLACE INTO kx_daily_stats(base_date, pool_version, kind, fetched_at, payload_json) "
                 "VALUES (?,?,?,?,?)",
                 (base_date.isoformat(), version, kind, utc(fetched_at),
                  json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
    conn.execute("DELETE FROM kx_daily_stats WHERE base_date < ?", ((base_date - timedelta(days=14)).isoformat(),))
