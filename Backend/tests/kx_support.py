"""KX 测试的共用夹具：假 KX、假麦蕊、假 TuShare 都是 httpx.MockTransport，业务时钟与缓存时钟都由测试推进。

没有任何真实网络；假服务只按请求内容确定性地造数据，并把每次调用记下来，供测试数调用次数。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx

from neckline.kx import services as kx_services
from neckline.kx.daily import DailyStats, TuShareBoardSource
from neckline.kx.pool import PoolSync
from neckline.kx.services import KxServices
from neckline.kx.source import KxSource
from neckline.market.mairui import Mairui
from neckline.morning import db
from neckline.morning.schedule import is_trading_day

READ_KEY = "k" * 40
PICKS_TOKEN = "p" * 32
LICENCE = "test-licence"
TUSHARE_TOKEN = "test-tushare-token"
VERSION = "kx-20261017"


# 库结构版本 2（B121、B122 线上）的 KX 三张表原样：kx_pool 没有 theme / first_seen / buyable。
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


def make_legacy_db(path: Path, version: int) -> None:
    """按旧库结构建库并放一份有内容的数据：版本 1 只有晨报表，版本 2 再加旧结构的三张 KX 表（kx_pool 带一份名单）。"""
    assert version in (1, 2)
    conn = sqlite3.connect(path)
    conn.executescript(db.DDL + (KX_V2_DDL if version == 2 else ""))
    conn.execute(f"PRAGMA application_id={db.APPLICATION_ID}")
    conn.execute(f"PRAGMA user_version={version}")
    conn.execute("INSERT INTO devices VALUES ('abcd','ios','2026-10-19T00:00:00+00:00','2026-10-19T00:00:00+00:00')")
    conn.execute("""INSERT INTO reports(report_id, trade_date, window_start, window_end, status, config_version,
                    config_sha256, started_at, available_at, content_json)
                    VALUES ('r1','2026-10-19','a','b','available','screening-v2','0','2026-10-19T00:00:00+00:00',
                            '2026-10-19T00:01:00+00:00','{"entries":[]}')""")
    if version == 2:
        conn.executemany("INSERT INTO kx_pool VALUES (?,?,?,?,?,?)", [
            ("600000", 0, "浦发银行", "银行", "kx-20261017-001", "2026-10-19T01:00:00+00:00"),
            ("000001", 1, "平安银行", "银行、保险", "kx-20261017-001", "2026-10-19T01:00:00+00:00")])
        conn.execute("INSERT INTO kx_picks VALUES ('req-1', 0, '600000', '2026-10-19T02:00:00+00:00', '2026-10-19', "
                     "'2026-10-19T02:00:01+00:00')")
        conn.execute("INSERT INTO kx_daily_stats VALUES ('2026-10-16', 'kx-20261017-001', 'daily', "
                     "'2026-10-19T01:00:00+00:00', '{\"600000\":1}')")
    conn.commit()
    conn.close()


def at(day: str, hm: str = "10:30", seconds: int = 0) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:{seconds:02d}+08:00")


def make_members(count: int) -> list[tuple[str, str, str]]:
    """(代码, 名称, 行业)。前几只是深市，其余是沪市，覆盖 .SZ 与 .SH 两种后缀。"""
    rows = []
    for index in range(count):
        code = f"00{index:04d}" if index < 5 else f"60{index:04d}"
        rows.append((code, f"股票{index:02d}", f"行业{index % 4}"))
    return rows


@dataclass
class Clock:
    now: datetime
    mono: float = 1000.0

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.mono += seconds

    def set(self, moment: datetime) -> None:
        self.mono += abs((moment - self.now).total_seconds())
        self.now = moment

    def monotonic(self) -> float:
        return self.mono


FIRST_SEEN = "2026-10-19"  # schema 2 名单里成员默认的 first_seen


@dataclass
class FakeKx:
    members: list[tuple[str, str, str]]
    version: str = VERSION
    schema: int = 1                   # 名单格式：1 带 sw_l2；2 与 3 带 theme、first_seen（不读 buyable）
    first_seen: dict = field(default_factory=dict)  # schema 2/3：代码 -> first_seen，缺省 FIRST_SEEN
    down: bool = False
    pending: int = 0                  # 前几次读名单先返回「正在取数」
    page_patch: object = None         # 可调用：改写名单页面 JSON，用来造坏数据
    health_patch: object = None
    calls: list[str] = field(default_factory=list)

    def _row(self, code: str, name: str, industry: str) -> dict:
        if self.schema == 1:
            return {"stock_code": code, "name": name, "sw_l2": [{"code": "801010.SI", "name": industry}],
                    "price": 1.0, "history": {"note": "x" * 50}}
        # schema 2 与 3 的行：页面上还有 KX 自己的行情与 session_phase，Neckline 只读代码、名称、题材、首次入选日
        return {"stock_code": code, "name": name, "theme": industry.replace("行业", "题材"),
                "first_seen": self.first_seen.get(code, FIRST_SEEN), "heat_day": 99,
                "price": 1.0, "previous_close": 0.99, "change_percent": 1.01,
                "open": 1.0, "high": 1.02, "low": 0.98, "turnover_amount": 1.0,
                "amplitude_percent": 2.0, "turnover_percent": 1.5,
                "source_time": "2026-10-29 10:30:00", "quote_status": "ok",
                "missing_fields": [], "warnings": [], "session_phase": None, "under_200": True,
                "history": {"note": "x" * 20}}

    def _page(self) -> dict:
        """schema 3 对齐 KX 0.3.0 的只读页；schema 1、2 多出来的键解析时忽略。"""
        return {
            "checked_at": "2026-10-29T01:30:00+08:00", "errors": [],
            "fetched_at": "2026-10-29T01:30:00+08:00", "history_last_error": None,
            "history_pending_count": 0, "history_target_date": "2026-10-28", "note": None,
            "pool": {"status": "ok", "schema_version": self.schema, "version": self.version, "name": "KX热门池",
                     "count": len(self.members), "updated_at": "2026-10-29T01:00:00+08:00",
                     "checked_at": "2026-10-29T01:00:00+08:00", "read_only": True, "membership_locked": False,
                     "calendar_warning": None, "generated_by": "kx", "generated_at": "2026-10-29T01:00:00+08:00"},
            "refresh": {"date": "2026-10-29", "result": "updated"},
            "rows": [self._row(code, name, industry) for code, name, industry in self.members],
            "rules": "...", "source": "kx", "status": "partial",
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if self.down:
            raise httpx.ConnectError("down")
        if request.url.path == "/kx/health":
            body = {"status": "ok", "service": "kx-market", "version": "1", "build": "1",
                    "pool_version": self.version, "pool_count": len(self.members)}
            if self.health_patch:
                body = self.health_patch(body)
            return httpx.Response(200, json=body)
        if request.url.path == f"/kx/read/{READ_KEY}":
            assert request.url.params.get("format") == "json"
            if self.pending > 0:
                self.pending -= 1
                return httpx.Response(200, json={"status": "pending", "code": "refresh_pending",
                                                 "retry_after_seconds": 3})
            body = self._page()
            if self.page_patch:
                body = self.page_patch(body)
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    def count(self, path: str) -> int:
        return sum(1 for item in self.calls if item == path)


def quote_values(index: int) -> dict:
    """第 index 只的行情：昨收 10+0.35*index，现价比昨收高 (index%7-3)%，成交额是 20 日日均的 1+0.5*(index%5) 倍。"""
    yc = round(10 + index * 0.35, 2)
    price = round(yc * (1 + (index % 7 - 3) / 100), 2)
    open_ = round(yc * 1.01, 2)
    high = round(max(price, open_) * 1.02, 2)
    low = round(min(price, open_) * 0.99, 2)
    amount = (1 + 0.5 * (index % 5)) * (index + 1) * 1e7
    return {"p": price, "yc": yc, "o": open_, "h": high, "l": low, "pc": round((price / yc - 1) * 100, 2),
            "ud": round(price - yc, 2), "zf": round((high - low) / yc * 100, 2), "tr": round(1 + index * 0.1, 2),
            "cje": amount, "v": round(amount / price / 100)}


@dataclass
class FakeMairui:
    members: list[tuple[str, str, str]]
    clock: Clock
    day: str = "2026-10-20"
    quote_clock: str = "10:29:58"                        # 报价时刻，上海时间 HH:MM:SS
    quote_shape: str | None = None                       # call_auction：o/h/l/cje/v 为 0；pre_open：o=h=l=p
    fail_groups: set = field(default_factory=set)       # 含有这些代码的批次返回错误
    stale_codes: dict = field(default_factory=dict)     # 代码 -> 报价日期（旧日期）
    drop_codes: set = field(default_factory=set)
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/hsrl/ssjy_more/{LICENCE}", request.url.path
        codes = tuple(request.url.params["stock_codes"].split(","))
        assert len(codes) <= 20
        self.calls.append(codes)
        if self.fail_groups & set(codes):
            return httpx.Response(200, text="103: " + LICENCE)
        index = {code: number for number, (code, _n, _i) in enumerate(self.members)}
        rows = []
        for code in codes:
            if code in self.drop_codes:
                continue
            row = quote_values(index[code])
            day = self.stale_codes.get(code, self.day)
            if code not in self.stale_codes and self.quote_shape == "call_auction":
                row.update(o=0, h=0, l=0, cje=0, v=0)  # zf、tr 保持非零，看板不能拿它们当依据
            elif code not in self.stale_codes and self.quote_shape == "pre_open":
                row.update(o=row["p"], h=row["p"], l=row["p"])
            rows.append({"dm": code, **row, "pv": 1, "tv": 2, "pe": 3, "pb_ratio": 4,
                         "t": f"{day} {self.quote_clock}"})
        return httpx.Response(200, json=rows)


def trading_days_between(start: str, end: str) -> list[str]:
    cursor, stop, days = datetime.fromisoformat(start).date(), datetime.fromisoformat(end).date(), []
    while cursor <= stop:
        if is_trading_day(cursor):
            days.append(cursor.strftime("%Y%m%d"))
        cursor += timedelta(days=1)
    return days


@dataclass
class FakeTuShare:
    members: list[tuple[str, str, str]]
    fail: bool = False
    skip_bar: dict = field(default_factory=dict)  # ts_code -> 要缺的 trade_date（YYYYMMDD）
    calls: list[dict] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["token"] == TUSHARE_TOKEN
        self.calls.append(payload)
        if self.fail:
            return httpx.Response(200, json={"code": 40203, "msg": "频率", "data": None})
        params, fields = payload["params"], payload["fields"].split(",")
        names = {f"{code}.{'SH' if code.startswith('6') else 'SZ'}": number
                 for number, (code, _n, _i) in enumerate(self.members)}
        rows = []
        if payload["api_name"] == "daily":
            days = trading_days_between(f"{params['start_date'][:4]}-{params['start_date'][4:6]}-{params['start_date'][6:]}",
                                        f"{params['end_date'][:4]}-{params['end_date'][4:6]}-{params['end_date'][6:]}")
            for ts_code in params["ts_code"].split(","):
                for position, day in enumerate(days):
                    if self.skip_bar.get(ts_code) == day:
                        continue
                    # 最后一天的收盘价等于行情里的昨收；往前每个交易日低 0.05
                    last = quote_values(names[ts_code])["yc"]
                    rows.append({"ts_code": ts_code, "trade_date": day,
                                 "close": round(last - 0.05 * (len(days) - 1 - position), 4),
                                 "amount": (names[ts_code] + 1) * 1e4})
        elif payload["api_name"] == "daily_basic":
            day = params["trade_date"]
            for ts_code, number in names.items():
                rows.append({"ts_code": ts_code, "trade_date": day, "total_mv": 100000 + number * 1000,
                             "circ_mv": 60000 + number * 500})
            for filler in range(200):  # 全市场其余股票：必须被忽略
                rows.append({"ts_code": f"{300000 + filler}.SZ", "trade_date": day, "total_mv": 1, "circ_mv": 1})
        else:
            raise AssertionError(payload["api_name"])
        items = [[row[name] for name in fields] for row in rows]
        return httpx.Response(200, json={"code": 0, "data": {"fields": fields, "items": items, "has_more": False}})

    def count(self, api_name: str) -> int:
        return sum(1 for item in self.calls if item["api_name"] == api_name)


def make_stats(handler, clock: Clock, token: str | None = TUSHARE_TOKEN) -> DailyStats:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return DailyStats(TuShareBoardSource(token, client), clock=clock.monotonic)


def make_mairui(handler, clock: Clock) -> Mairui:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Mairui(LICENCE, client=client, now=lambda: clock.now)


@dataclass
class Harness:
    services: KxServices
    clock: Clock
    kx: FakeKx
    mairui: FakeMairui
    tushare: FakeTuShare
    path: Path
    sleeps: list[float]


def build(tmp_path: Path, monkeypatch, *, count: int = 45, now: datetime | None = None,
          day: str | None = None) -> Harness:
    clock = Clock(now or at("2026-10-20"))
    members = make_members(count)
    fake_kx, fake_mairui, fake_tushare = FakeKx(members), FakeMairui(members, clock), FakeTuShare(members)
    if day:
        fake_mairui.day = day
    elif now is not None:
        fake_mairui.day = now.date().isoformat()
    monkeypatch.setenv("KX_READ_KEY", READ_KEY)
    monkeypatch.setattr("neckline.market.cache.time", SimpleNamespace(monotonic=clock.monotonic))
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    def client(handler) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    services = KxServices(
        pool=PoolSync(KxSource(client(fake_kx.handler)), clock=clock.monotonic, sleep=sleep),
        mairui=make_mairui(fake_mairui.handler, clock),
        stats=make_stats(fake_tushare.handler, clock),
        now=lambda: clock.now)
    path = tmp_path / "v4.db"
    db.init_schema(path)
    kx_services.use(services)
    return Harness(services, clock, fake_kx, fake_mairui, fake_tushare, path, sleeps)


def run(coro):
    return asyncio.run(coro)


__all__ = ["Harness", "KX_V2_DDL", "build", "make_legacy_db", "run", "at", "quote_values", "make_members", "make_stats", "make_mairui",
           "FIRST_SEEN", "READ_KEY", "PICKS_TOKEN", "LICENCE", "TUSHARE_TOKEN", "VERSION"]
