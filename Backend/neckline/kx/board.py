"""看板：把名单、实时行情、日线统计和推荐高亮合成一份响应，排序在服务端排好。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from neckline.kx import store
from neckline.kx.days import BoardClock, board_clock, highlight
from neckline.kx.quotes import Quote, QuoteBook, fetch_quotes
from neckline.kx.daily import StatsBundle
from neckline.kx.services import KxServices
from neckline.morning import db
from neckline.morning.schedule import CST, CalendarGap

REFRESH_SECONDS = 60
_YI = 1e8


class BoardUnavailable(Exception):
    """看板出不来：reason 是给程序看的短码，message 是如实说明的中文原因。"""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(CST).isoformat(timespec="seconds") if value else None


def _round(value: float | None, places: int) -> float | None:
    return None if value is None else round(value, places) + 0.0  # + 0.0 把 -0.0 变成 0.0


def _change(price: float, base: float | None) -> float | None:
    return None if not base else (price / base - 1) * 100


def _position(price: float, average: float) -> str:
    if round(price - average, 4) == 0:
        return "flat"
    return "above" if price > average else "below"


def _average(price: float, value: float | None) -> dict | None:
    if value is None:
        return None
    return {"value": round(value, 4), "position": _position(price, value)}


_EMPTY_QUOTE: dict[str, Any] = {
    "price": None, "changePercent": None, "changeAmount": None, "open": None, "high": None, "low": None,
    "previousClose": None, "amplitudePercent": None, "turnoverRatePercent": None, "turnoverYi": None,
    "volumeLots": None, "openChangePercent": None, "rangePosition": None, "pullbackFromHighPercent": None,
    "quoteTime": None,
}


def _empty_stats() -> dict[str, Any]:
    return {"change5dPercent": None, "change20dPercent": None, "avgTurnover20Yi": None, "volumeRatio": None,
            "totalMvYi": None, "circMvYi": None, "averages": {"ma5": None, "ma10": None, "ma20": None}}


def _quote_fields(quote: Quote, notes: list[str]) -> dict[str, Any]:
    price, high, low, previous = quote.price, quote.high, quote.low, quote.previous_close
    consistent = (price is not None and high is not None and low is not None and high >= low
                  and low <= price <= high and (quote.open is None or low <= quote.open <= high))
    if price is not None and high is not None and low is not None and not consistent:
        notes.append("价格区间不一致，没有计算区间位置")
    position = None
    if consistent and high > low:
        position = (price - low) / (high - low)
    return {
        "price": _round(price, 4), "changePercent": _round(quote.change_percent, 2),
        "changeAmount": _round(quote.change_amount, 4), "open": _round(quote.open, 4), "high": _round(high, 4),
        "low": _round(low, 4), "previousClose": _round(previous, 4),
        "amplitudePercent": _round(quote.amplitude_percent, 2), "turnoverRatePercent": _round(quote.turnover_rate, 2),
        "turnoverYi": _round(None if quote.amount_yuan is None else quote.amount_yuan / _YI, 4),
        "volumeLots": _round(quote.volume_lots, 2),
        "openChangePercent": _round(_change(quote.open, previous) if quote.open else None, 2),
        "rangePosition": _round(position, 4),
        "pullbackFromHighPercent": _round(_change(price, high) if consistent else None, 2),
        "quoteTime": _iso(quote.at),
    }


def _stats_fields(code: str, quote: Quote, bundle: StatsBundle, notes: list[str]) -> dict[str, Any]:
    fields = _empty_stats()
    price = quote.price
    daily = None if bundle.daily is None else bundle.daily.get(code)
    basic = None if bundle.basic is None else bundle.basic.get(code)
    incomplete = False
    if daily is not None:
        fields["change5dPercent"] = _round(_change(price, daily["close5"]), 2)
        fields["change20dPercent"] = _round(_change(price, daily["close20"]), 2)
        average = daily["avgAmount20"]
        if average and average > 0:
            fields["avgTurnover20Yi"] = _round(average / _YI, 4)
            if quote.amount_yuan is not None:
                fields["volumeRatio"] = _round(quote.amount_yuan / average, 2)
        fields["averages"] = {name: _average(price, daily[name]) for name in ("ma5", "ma10", "ma20")}
        incomplete = any(daily[key] is None for key in ("close5", "close20", "ma5", "ma10", "ma20", "avgAmount20"))
    if basic is not None and quote.previous_close:
        ratio = price / quote.previous_close
        for source, target in (("total", "totalMvYi"), ("circ", "circMvYi")):
            if basic[source] is not None:
                fields[target] = _round(basic[source] * ratio / _YI, 2)
    if daily is not None and incomplete:
        notes.append("日线数据不全，部分统计没有计算")
    if basic is not None and (basic["total"] is None or basic["circ"] is None):
        notes.append("没有取得市值")
    return fields


@dataclass
class _Entry:
    member: store.Member
    row: dict[str, Any]
    pick: store.LatestPick | None


def _row(member: store.Member, book: QuoteBook, bundle: StatsBundle, clock: BoardClock,
         pick: store.LatestPick | None) -> dict[str, Any]:
    code = member.code
    notes: list[str] = []
    quote = book.quotes.get(code)
    if code in book.failed:
        status = "missing"
        notes.append("行情未取得")
    elif code in book.duplicated:
        status = "missing"
        notes.append("行情数据重复，没有采用")
    elif quote is None or quote.price is None:
        status = "missing"
        notes.append("没有取得这只的行情")
    elif quote.day is None:
        status = "stale"
        notes.append("报价时间缺失")
    elif quote.day != clock.quote_day:
        status = "stale"
        notes.append(f"报价日期是 {quote.day.isoformat()}，不是 {clock.quote_day.isoformat()}")
    else:
        status = "ok"
    row: dict[str, Any] = {
        "code": code, "name": member.name, "industry": member.industry,
        "highlight": highlight(pick.trade_date, clock) if pick else "normal",
        "lastPickedAt": _iso(pick.picked_at) if pick else None,
        "quoteStatus": status,
    }
    if status == "missing":
        row.update(_EMPTY_QUOTE)
        row.update(_empty_stats())
    else:
        row.update(_quote_fields(quote, notes))
        if status == "ok":
            row.update(_stats_fields(code, quote, bundle, notes))
        else:
            row.update(_empty_stats())  # 报价不是当日，日线统计的基准日对不上，不硬算
    row["note"] = "；".join(notes) or None
    return row


def _order(entries: list[_Entry]) -> list[dict[str, Any]]:
    def pinned(state: str) -> list[_Entry]:
        chosen = [item for item in entries if item.row["highlight"] == state]
        # 推荐时间从新到旧；同一时刻（同一次推荐里的几只）按推荐里的次序，再按名单次序
        return sorted(chosen, key=lambda item: (-item.pick.picked_at.timestamp(), item.pick.position,
                                                 item.member.position))

    def change(item: _Entry) -> float | None:
        # 报价不是当日的行（停牌等）涨跌幅是旧的，不拿它参与排序，和没有行情的一起排在最后
        return item.row["changePercent"] if item.row["quoteStatus"] == "ok" else None

    normal = [item for item in entries if item.row["highlight"] == "normal"]
    normal.sort(key=lambda item: (change(item) is None, -(change(item) or 0.0), item.member.position))
    return [item.row for item in pinned("today") + pinned("faded") + normal]


async def build_board(kx: KxServices, path: Path) -> dict[str, Any]:
    now = kx.now()
    try:
        clock = board_clock(now)
    except CalendarGap as error:
        raise BoardUnavailable("calendar_gap", f"交易日历没有覆盖今天，暂不出看板：{error}") from None
    try:
        result = await kx.pool.ensure(path, now)
    except db.NotReady:
        raise BoardUnavailable("v4_db_not_ready", "库还没准备好。") from None
    snapshot = result.snapshot
    if snapshot is None:
        raise BoardUnavailable("pool_unverified", f"KX 名单还没有成功同步过，暂不出看板：{result.reason}")
    codes = [member.code for member in snapshot.members]
    book_task = fetch_quotes(kx.mairui, codes)
    stats_task = kx.stats.load(path, base_date=clock.stats_base_day, version=snapshot.version, codes=codes, now=now)
    book, bundle = await asyncio.gather(book_task, stats_task)
    with db.read(path) as conn:
        picks, last_pick_at = store.latest_picks(conn)
    entries = [_Entry(member, _row(member, book, bundle, clock, picks.get(member.code)), picks.get(member.code))
               for member in snapshot.members]
    gaps = list(dict.fromkeys(bundle.gaps))  # 两个接口同一个原因时只写一次
    if book.failed:
        gaps.insert(0, f"{len(book.failed)} 只行情未取得：{'；'.join(sorted(set(book.failed.values())))}")
    times = [quote.at for quote in book.quotes.values() if quote.at is not None]
    return {
        "generatedAt": _iso(now),
        "pool": {"version": snapshot.version, "count": len(snapshot.members), "checkedAt": _iso(snapshot.verified_at),
                 "stale": result.stale, "reason": result.reason if result.stale else None},
        "session": {"tradeDate": clock.quote_day.isoformat(), "inSession": clock.in_session,
                    "refreshSeconds": REFRESH_SECONDS if clock.in_session else None,
                    "quoteTime": _iso(max(times)) if times else None},
        "stats": {"asOf": bundle.base_date.isoformat() if bundle.daily is not None else None},
        "picks": {"referenceDate": clock.reference_day.isoformat(), "lastPickAt": _iso(last_pick_at)},
        "gaps": gaps,
        "rows": _order(entries),
    }

