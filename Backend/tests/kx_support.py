"""KX 测试的共用夹具：假 KX 总览、可推进的时钟，以及 v2/v4 的冻结 DDL。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from neckline.kx.services import KxServices
from neckline.kx.source import KxSource
from neckline.morning import db

READ_KEY = "k" * 40
OVERVIEW_PATH = Path(__file__).resolve().parent / "fixtures" / "kx_v3_overview_20261104.json"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
APP_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "App" / "NecklineTests" / "Fixtures"

# 库结构版本 2（B121、B122）的三张 KX 表：kx_pool 没有 theme / first_seen / buyable。
KX_V2_DDL = """
CREATE TABLE IF NOT EXISTS kx_pool (
  code TEXT PRIMARY KEY CHECK(length(code) = 6),
  position INTEGER NOT NULL,
  name TEXT NOT NULL,
  industry TEXT NOT NULL,
  version TEXT NOT NULL,
  verified_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kx_picks (
  request_id TEXT NOT NULL,
  position INTEGER NOT NULL CHECK(position >= 0),
  code TEXT NOT NULL CHECK(length(code) = 6),
  picked_at TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  received_at TEXT NOT NULL,
  PRIMARY KEY(request_id, position),
  UNIQUE(request_id, code)
);
CREATE INDEX IF NOT EXISTS idx_kx_picks_code ON kx_picks(code, picked_at);
CREATE TABLE IF NOT EXISTS kx_daily_stats (
  base_date TEXT NOT NULL,
  pool_version TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('daily','basic')),
  fetched_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(base_date, pool_version, kind)
);
"""

# 冻结自 v4.2.1（B130）的 morning/db.py。kx_pool 带 theme、first_seen、buyable。
KX_V4_DDL = """
CREATE TABLE IF NOT EXISTS kx_pool (
  code TEXT PRIMARY KEY CHECK(length(code) = 6),
  position INTEGER NOT NULL,
  name TEXT NOT NULL,
  industry TEXT NOT NULL,
  version TEXT NOT NULL,
  verified_at TEXT NOT NULL,
  theme TEXT,
  first_seen TEXT,
  buyable INTEGER
);
CREATE TABLE IF NOT EXISTS kx_picks (
  request_id TEXT NOT NULL,
  position INTEGER NOT NULL CHECK(position >= 0),
  code TEXT NOT NULL CHECK(length(code) = 6),
  picked_at TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  received_at TEXT NOT NULL,
  PRIMARY KEY(request_id, position),
  UNIQUE(request_id, code)
);
CREATE INDEX IF NOT EXISTS idx_kx_picks_code ON kx_picks(code, picked_at);
CREATE TABLE IF NOT EXISTS kx_daily_stats (
  base_date TEXT NOT NULL,
  pool_version TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('daily','basic')),
  fetched_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(base_date, pool_version, kind)
);
"""

KX_TABLES = frozenset({"kx_pool", "kx_picks", "kx_daily_stats"})
POOL_V4_COLUMNS = ["code", "position", "name", "industry", "version", "verified_at", "theme", "first_seen", "buyable"]
POOL_V2_COLUMNS = ["code", "position", "name", "industry", "version", "verified_at"]
PICKS_COLUMNS = ["request_id", "position", "code", "picked_at", "trade_date", "received_at"]
STATS_COLUMNS = ["base_date", "pool_version", "kind", "fetched_at", "payload_json"]


def load_overview() -> dict:
    return json.loads(OVERVIEW_PATH.read_text(encoding="utf-8"))


def at(day: str, hm: str = "10:30", seconds: int = 0) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:{seconds:02d}+08:00")


def run(awaitable):
    return asyncio.run(awaitable)


@dataclass
class Clock:
    now: datetime
    mono: float = 1000.0
    sleeps: list[float] = field(default_factory=list)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.mono += seconds

    def set_now(self, moment: datetime) -> None:
        self.now = moment

    def monotonic(self) -> float:
        return self.mono

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


@dataclass
class FakeKx:
    """按真实页面的形态返回总览。pending、HTTP 状态、unavailable、连不上都可以单独设置。"""

    body: dict
    pending: int = 0
    status: int = 200
    unavailable: str | None = None
    down: bool = False
    retry_after: float = 3
    calls: list[str] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if self.down:
            raise httpx.ConnectError("down")
        if self.pending > 0:
            self.pending -= 1
            return httpx.Response(200, json={
                "status": "pending", "code": "refresh_pending", "retry_after_seconds": self.retry_after,
            })
        if self.status == 404:
            return httpx.Response(404, json={"status": "missing"})
        if self.unavailable is not None:
            return httpx.Response(200, json={
                "status": "unavailable", "code": "refresh_unavailable", "message": self.unavailable,
            })
        if self.status != 200:
            return httpx.Response(self.status, json={"status": "error"})
        return httpx.Response(200, json=self.body)


def services_for(fake: FakeKx, clock: Clock) -> KxServices:
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), trust_env=False, follow_redirects=False)
    source = KxSource(client)
    source._owns = True
    return KxServices(source=source, now=lambda: clock.now, monotonic=clock.monotonic, sleep=clock.sleep)


def make_legacy_db(path: Path, version: int) -> None:
    """按旧库结构建库并放一份有内容的数据。版本 4 另有一行 filing_reads，用来确认升级不碰其他表。"""
    assert version in (1, 2, 3, 4)
    script = db.DDL
    if version == 2:
        script += KX_V2_DDL
    elif version in (3, 4):
        script += KX_V4_DDL
    if version >= 4:
        script += db.V4_DDL
    conn = sqlite3.connect(path)
    conn.executescript(script)
    conn.execute(f"PRAGMA application_id={db.APPLICATION_ID}")
    conn.execute(f"PRAGMA user_version={version}")
    conn.execute("INSERT INTO devices VALUES ('abcd','ios','2026-10-19T00:00:00+00:00','2026-10-19T00:00:00+00:00')")
    conn.execute("""INSERT INTO reports(report_id, trade_date, window_start, window_end, status, config_version,
                    config_sha256, started_at, available_at, content_json)
                    VALUES ('r1','2026-10-19','a','b','available','screening-v2','0','2026-10-19T00:00:00+00:00',
                            '2026-10-19T00:01:00+00:00','{"entries":[]}')""")
    if version >= 2:
        conn.execute("INSERT INTO kx_pool(code, position, name, industry, version, verified_at) VALUES (?,?,?,?,?,?)",
                     ("600000", 0, "浦发银行", "银行", "kx-20261017-001", "2026-10-19T01:00:00+00:00"))
        conn.execute("INSERT INTO kx_pool(code, position, name, industry, version, verified_at) VALUES (?,?,?,?,?,?)",
                     ("000001", 1, "平安银行", "银行", "kx-20261017-001", "2026-10-19T01:00:00+00:00"))
        conn.execute("INSERT INTO kx_picks VALUES ('req-1', 0, '600000', '2026-10-19T02:00:00+00:00', '2026-10-19', "
                     "'2026-10-19T02:00:01+00:00')")
        conn.execute("INSERT INTO kx_daily_stats VALUES ('2026-10-16', 'kx-20261017-001', 'daily', "
                     "'2026-10-19T01:00:00+00:00', '{\"600000\":1}')")
    if version >= 4:
        conn.execute("INSERT INTO filing_reads(document_id, revision, url, kind, state, read_at) "
                     "VALUES ('doc-1', 1, 'https://example.test/a.pdf', 'pdf', 'ok', '2026-10-18T00:00:00+00:00')")
    conn.commit()
    conn.close()
