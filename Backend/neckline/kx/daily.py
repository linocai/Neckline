"""TuShare 日线统计：daily（收盘价、成交额）和 daily_basic（市值）每天各取一次，缓存到下一个完整交易日。

统计的基准日是「行情所在交易日的前一个交易日」（最近一个完整交易日）：5 日、20 日涨跌、20 日日均成交额、
5/10/20 日均线都由到基准日为止的连续交易日收盘价算出，缺任何一天就留空，不估算。日线不复权。
单位：daily.amount 千元，daily_basic 的市值万元，这里一律换成元存放。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Sequence

import httpx

from neckline.kx import store
from neckline.kx.days import trading_days_ending
from neckline.morning import db
from neckline.market.tushare_daily import API_URL, _parse
from neckline.market.util import MarketError, num, silence_http_logs
from neckline.morning.schedule import CalendarGap

DAILY_FIELDS = "ts_code,trade_date,close,amount"
BASIC_FIELDS = "ts_code,trade_date,total_mv,circ_mv"
ROW_LIMIT = 6000        # TuShare 单次请求的行数上限，到了就当没取全
RETRY_AFTER = 300.0     # 取数失败后的最短重试间隔
_QIAN = 1_000.0         # 千元 -> 元
_WAN = 10_000.0         # 万元 -> 元


def ts_code(code: str) -> str:
    return f"{code}.{'SH' if code.startswith('6') else 'SZ'}"


def compact(day: date) -> str:
    return day.strftime("%Y%m%d")


class TuShareBoardSource:
    """多只股票一次取：daily 用逗号分隔的 ts_code 加日期区间，daily_basic 用交易日取全市场后只留池内。"""

    def __init__(self, token: str | None, client: httpx.AsyncClient | None = None) -> None:
        self.token = (token or "").strip()
        self.client = client
        self._owns = client is None
        silence_http_logs()

    async def aclose(self) -> None:
        if self._owns and self.client is not None:
            await self.client.aclose()
            self.client = None

    async def _http(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False)
            self._owns = True
        return self.client

    async def _post(self, api_name: str, fields: str, params: dict) -> tuple[list[dict], bool]:
        if not self.token:
            raise MarketError("credential_missing", "未配置 TuShare，没有日线统计。")
        client = await self._http()
        payload = {"api_name": api_name, "token": self.token, "fields": fields, "params": params}
        try:
            response = await client.post(API_URL, json=payload)
        except httpx.HTTPError:
            raise MarketError("tushare", "TuShare 暂时连不上，没有日线统计。") from None
        if response.status_code != 200:
            raise MarketError("tushare", "TuShare 暂时连不上，没有日线统计。")
        try:
            body = response.json()
        except ValueError:
            raise MarketError("tushare", "TuShare 没有返回可解析的数据。") from None
        rows, more = _parse(body, fields)
        return rows, more or len(rows) >= ROW_LIMIT

    async def daily(self, codes: Sequence[str], start: date, end: date) -> tuple[list[dict], bool]:
        return await self._post("daily", DAILY_FIELDS, {
            "ts_code": ",".join(ts_code(code) for code in codes), "start_date": compact(start), "end_date": compact(end)})

    async def daily_basic(self, trade_date: date) -> tuple[list[dict], bool]:
        return await self._post("daily_basic", BASIC_FIELDS, {"trade_date": compact(trade_date)})


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def derive_daily(rows: list[dict], codes: Sequence[str], days: list[date]) -> dict[str, dict]:
    """每只票一份：5/20 个交易日前的收盘价、5/10/20 日均线、20 日日均成交额（元）。缺任何一天对应项留空。"""
    wanted = {ts_code(code): code for code in codes}
    expected = {compact(day): day for day in days}
    bars: dict[str, dict[str, tuple[float, float | None]]] = {code: {} for code in codes}
    for row in rows:
        code = wanted.get(str(row.get("ts_code") or "").strip().upper())
        trade = str(row.get("trade_date") or "").strip()
        close, amount = num(row.get("close")), num(row.get("amount"))
        if code is None or trade not in expected or close is None or close <= 0:
            continue
        bars[code][trade] = (close, amount * _QIAN if amount is not None and amount >= 0 else None)
    keys = [compact(day) for day in days]
    result: dict[str, dict] = {}
    for code in codes:
        have = bars[code]

        def closes(width: int) -> list[float] | None:
            picked = [have.get(key) for key in keys[-width:]]
            return None if any(item is None for item in picked) else [item[0] for item in picked]

        amounts = [have[key][1] if key in have else None for key in keys]
        ma = {width: closes(width) for width in (5, 10, 20)}
        result[code] = {
            "close5": have[keys[-5]][0] if keys[-5] in have else None,
            "close20": have[keys[0]][0] if keys[0] in have else None,
            "ma5": None if ma[5] is None else _mean(ma[5]),
            "ma10": None if ma[10] is None else _mean(ma[10]),
            "ma20": None if ma[20] is None else _mean(ma[20]),
            "avgAmount20": None if any(item is None for item in amounts) else _mean(amounts),
        }
    return result


def derive_basic(rows: list[dict], codes: Sequence[str], base_date: date) -> dict[str, dict]:
    """基准日的总市值、流通市值（元）。日期对不上的行不用。"""
    wanted = {ts_code(code): code for code in codes}
    result: dict[str, dict] = {code: {"total": None, "circ": None} for code in codes}
    for row in rows:
        code = wanted.get(str(row.get("ts_code") or "").strip().upper())
        if code is None or str(row.get("trade_date") or "").strip() != compact(base_date):
            continue
        total, circ = num(row.get("total_mv")), num(row.get("circ_mv"))
        result[code] = {"total": total * _WAN if total is not None and total > 0 else None,
                        "circ": circ * _WAN if circ is not None and circ > 0 else None}
    return result


@dataclass
class StatsBundle:
    base_date: date
    daily: dict[str, dict] | None = None
    basic: dict[str, dict] | None = None
    gaps: list[str] = field(default_factory=list)


class DailyStats:
    def __init__(self, source: TuShareBoardSource, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.source = source
        self._clock = clock
        self._lock: asyncio.Lock | None = None
        self._failed: dict[tuple[str, str, str], tuple[float, str]] = {}

    def _guard(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _fetch(self, kind: str, codes: Sequence[str], base_date: date, days: list[date]) -> dict[str, Any]:
        if kind == "daily":
            rows, truncated = await self.source.daily(codes, days[0], base_date)
            if not rows:
                raise MarketError("tushare_daily", "TuShare 没有返回日线数据。")
            return {"asOf": base_date.isoformat(), "truncated": truncated, "stats": derive_daily(rows, codes, days)}
        rows, truncated = await self.source.daily_basic(base_date)
        if not rows:
            raise MarketError("tushare_daily", "TuShare 没有返回市值数据。")
        return {"asOf": base_date.isoformat(), "truncated": truncated, "mv": derive_basic(rows, codes, base_date)}

    async def load(self, path: Path, *, base_date: date, version: str, codes: Sequence[str], now: datetime) -> StatsBundle:
        bundle = StatsBundle(base_date)
        try:
            days = trading_days_ending(base_date, 20)
        except CalendarGap as error:
            bundle.gaps.append(f"交易日历不够，日线统计没有计算：{error}")
            return bundle
        async with self._guard():
            kinds = ("daily", "basic")
            with db.read(path) as conn:
                cached = {kind: store.get_stats(conn, base_date=base_date, version=version, kind=kind) for kind in kinds}
            missing = []
            for kind in kinds:
                if cached[kind] is not None:
                    continue
                blocked = self._failed.get((base_date.isoformat(), version, kind))
                if blocked and self._clock() - blocked[0] < RETRY_AFTER:
                    bundle.gaps.append(blocked[1])
                else:
                    missing.append(kind)
            fetched = await asyncio.gather(*(self._fetch(kind, codes, base_date, days) for kind in missing),
                                           return_exceptions=True)
            for kind, outcome in zip(missing, fetched):
                key = (base_date.isoformat(), version, kind)
                if isinstance(outcome, MarketError):
                    self._failed[key] = (self._clock(), outcome.message)
                    bundle.gaps.append(outcome.message)
                elif isinstance(outcome, BaseException):
                    raise outcome
                else:
                    self._failed.pop(key, None)
                    with db.write(path) as conn:
                        store.put_stats(conn, base_date=base_date, version=version, kind=kind, fetched_at=now,
                                        payload=outcome)
                    cached[kind] = outcome
        if cached["daily"] is not None:
            bundle.daily = cached["daily"]["stats"]
            if cached["daily"].get("truncated"):
                bundle.gaps.append("TuShare 日线可能没有取全。")
        if cached["basic"] is not None:
            bundle.basic = cached["basic"]["mv"]
            if cached["basic"].get("truncated"):
                bundle.gaps.append("TuShare 市值可能没有取全。")
        return bundle

    async def aclose(self) -> None:
        await self.source.aclose()
