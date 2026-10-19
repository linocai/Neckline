"""KX 测试的共用夹具：假 KX、假麦蕊、假 TuShare 都是 httpx.MockTransport，业务时钟与缓存时钟都由测试推进。

没有任何真实网络；假服务只按请求内容确定性地造数据，并把每次调用记下来，供测试数调用次数。
"""
from __future__ import annotations

import asyncio
import json
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


@dataclass
class FakeKx:
    members: list[tuple[str, str, str]]
    version: str = VERSION
    down: bool = False
    pending: int = 0                  # 前几次读名单先返回「正在取数」
    page_patch: object = None         # 可调用：改写名单页面 JSON，用来造坏数据
    health_patch: object = None
    calls: list[str] = field(default_factory=list)

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
            body = {"status": "partial", "checked_at": "x",
                    "pool": {"status": "ok", "version": self.version, "name": "KX热门池", "count": len(self.members),
                             "membership_locked": False},
                    "rows": [{"stock_code": code, "name": name, "sw_l2": [{"code": "801010.SI", "name": industry}],
                              "price": 1.0, "history": {"note": "x" * 50}} for code, name, industry in self.members],
                    "rules": "..."}
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
            rows.append({"dm": code, **row, "pv": 1, "tv": 2, "pe": 3, "pb_ratio": 4, "t": f"{day} 10:29:58"})
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


__all__ = ["Harness", "build", "run", "at", "quote_values", "make_members", "make_stats", "make_mairui",
           "READ_KEY", "PICKS_TOKEN", "LICENCE", "TUSHARE_TOKEN", "VERSION"]
