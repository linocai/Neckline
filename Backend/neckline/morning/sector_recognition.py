"""涨停数据与题材辨识度。

生产用 TuShare ``limit_list_d`` 补缺的交易日。回放只读库或写入快照，不联网。
某一天返回 0 行不算取全（当天可能还没出），不写 ``limit_up_days``。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Callable, Mapping, Sequence

from neckline.morning.schedule import CST, is_trading_day, previous_trading_day
from neckline.morning.sectors import (
    _PAGE_LIMIT, SectorFetchError, _Stopped, _check_interval, _fetch_page, _pause, _sender, _symbol,
    trading_days_ending,
)

LIMIT_INTERVAL_SECONDS = 0.35
_FIELDS = "trade_date,ts_code"
_API = "limit_list_d"


def recognition_window(day: date, lookback: int) -> list[date]:
    """报告日 D 之前（不含 D）最近 lookback 个交易日，从早到晚。"""
    return trading_days_ending(previous_trading_day(day), lookback)


def collect_limit_days(now: datetime, lookback: int) -> list[date]:
    """20:00 采集：今天是交易日就补到今天，否则补到上个交易日。含截止日。"""
    today = now.astimezone(CST).date()
    end = today if is_trading_day(today) else previous_trading_day(today)
    return trading_days_ending(end, lookback)


def closed_limit_days(now: datetime, previous_close: str, lookback: int) -> list[date]:
    """手工补齐：截至最近一个已收盘交易日，含截止日。"""
    current = now.astimezone(CST)
    today = current.date()
    hour, minute = previous_close.split(":")
    mark = current.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    if is_trading_day(today) and current >= mark:
        end = today
    else:
        end = previous_trading_day(today)
    return trading_days_ending(end, lookback)


def score_recognition(candidates: Sequence[str], full: set[str], limit_ups: Mapping[str, set[str]],
                      days: Sequence[date], *, min_others: int, min_share: float) -> list[dict]:
    """纯函数。count 为 0 的不进名单。排序：次数降序、最近一次降序、代码升序。"""
    found = []
    for code in candidates:
        hits = [
            item.isoformat() for item in days
            if _hot(code, full, limit_ups.get(item.isoformat()) or set(), min_others, min_share)
        ]
        if not hits:
            continue
        found.append({"code": code, "count": len(hits), "last": max(hits)})
    found.sort(key=lambda item: item["code"])
    found.sort(key=lambda item: item["last"], reverse=True)
    found.sort(key=lambda item: item["count"], reverse=True)
    return found


def _hot(code: str, full: set[str], day_limits: set[str], min_others: int, min_share: float) -> bool:
    if code not in day_limits:
        return False
    denom = len(full) - 1
    if denom <= 0:
        return False
    others = len((full & day_limits) - {code})
    if others < min_others:
        return False
    return _share_met(others, denom, min_share)


def _share_met(others: int, denom: int, minimum: float) -> bool:
    """others / denom >= minimum。用十进制比较，使 2/100 与 0.02 算作达到。"""
    text = format(float(minimum), ".12f").rstrip("0").rstrip(".") or "0"
    return Decimal(others) / Decimal(denom) >= Decimal(text)


def stored_limit_days(conn) -> set[str]:
    return {row[0] for row in conn.execute("SELECT trade_date FROM limit_up_days")}


def load_limit_ups(conn, days: Sequence[date]) -> dict[str, set[str]]:
    wanted = [item.isoformat() for item in days]
    found = {iso: set() for iso in wanted}
    if not wanted:
        return found
    marks = ",".join("?" for _ in wanted)
    for trade_date, code in conn.execute(
            f"SELECT trade_date, code FROM limit_ups WHERE trade_date IN ({marks})", wanted):
        found.setdefault(trade_date, set()).add(code)
    return found


def load_members_all(conn) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for ts_code, code in conn.execute("SELECT ts_code, code FROM sector_members_all"):
        found.setdefault(ts_code, set()).add(code)
    return found


def ensure_limit_ups(conn, days: Sequence[date], *, post=None, environ=None, sleep: Callable[[float], None],
                     interval_seconds: float, before_call: Callable[[], bool] | None = None) -> dict:
    """只取 ``limit_up_days`` 里还没有的那些天。返回还缺的日期、这次写入的天数和错误。

    调用方负责事务。已经写入的天会随事务提交；中途失败不再继续后面的天，也不留下半页。
    """
    ordered = list(days)
    missing = [item for item in ordered if item.isoformat() not in stored_limit_days(conn)]
    if not missing:
        return {"missing": [], "fetched": 0, "error": None}
    _check_interval(interval_seconds)
    try:
        token, sender = _sender(post, environ)
    except SectorFetchError as exc:
        return {"missing": [item.isoformat() for item in missing], "fetched": 0, "error": str(exc)}
    pause = {"started": False}
    fetched = 0
    error = None
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for day in missing:
        try:
            codes = _fetch_day(sender, token, day, sleep=sleep, interval_seconds=interval_seconds,
                               pause=pause, before_call=before_call)
        except _Stopped:
            error = "收口时刻前没取完"
            break
        except SectorFetchError as exc:
            error = str(exc)
            break
        except Exception as exc:
            text = str(exc)
            error = "limit_list_d 请求失败" if token and token in text else f"limit_list_d 请求失败：{type(exc).__name__}"
            break
        if not codes:
            continue
        _store_day(conn, day, codes, stamp)
        fetched += 1
    still = [item.isoformat() for item in ordered if item.isoformat() not in stored_limit_days(conn)]
    return {"missing": still, "fetched": fetched, "error": error}


def install_limit_snapshot(conn, rows, trade_date: str) -> int:
    """把涨停快照写入工作库。只写交易日早于 trade_date、且至少有一只股票的天。"""
    if not isinstance(rows, list):
        raise ValueError("涨停快照不是列表")
    grouped: dict[date, set[str]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        day = _parse_day(row.get("trade_date"))
        if day is None or not is_trading_day(day) or day.isoformat() >= trade_date:
            continue
        code = _symbol(row.get("ts_code"))
        if not code:
            continue
        grouped.setdefault(day, set()).add(code)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    for day in sorted(grouped):
        codes = grouped[day]
        if not codes:
            continue
        _store_day(conn, day, codes, stamp)
        written += 1
    return written


def _fetch_day(post, token: str, day: date, *, sleep, interval_seconds: float, pause: dict,
               before_call: Callable[[], bool] | None) -> set[str]:
    compact = day.strftime("%Y%m%d")
    found: set[str] = set()
    offset = 0
    while True:
        _pause(pause, sleep, interval_seconds)
        if before_call is not None and before_call():
            raise _Stopped()
        page, more = _fetch_page(
            post, token, _API,
            {"trade_date": compact, "limit_type": "U", "limit": _PAGE_LIMIT, "offset": offset}, _FIELDS)
        for row in page:
            if _compact(row.get("trade_date")) != compact:
                raise SectorFetchError(f"{day.isoformat()} 的 limit_list_d 交易日对不上")
            code = _symbol(row.get("ts_code"))
            if code:
                found.add(code)
        if not more:
            return found
        if not page:
            raise SectorFetchError("limit_list_d 没有一次取全")
        offset += len(page)


def _store_day(conn, day: date, codes: set[str], fetched_at: str) -> None:
    iso = day.isoformat()
    conn.execute("DELETE FROM limit_ups WHERE trade_date=?", (iso,))
    conn.executemany(
        "INSERT INTO limit_ups(trade_date, code) VALUES (?, ?)",
        [(iso, code) for code in sorted(codes)])
    conn.execute(
        "INSERT INTO limit_up_days(trade_date, count, fetched_at) VALUES (?, ?, ?) "
        "ON CONFLICT(trade_date) DO UPDATE SET count=excluded.count, fetched_at=excluded.fetched_at",
        (iso, len(codes), fetched_at))


def _compact(value: object) -> str:
    return str(value or "").strip().replace("-", "")


def _parse_day(value: object) -> date | None:
    text = _compact(value)
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        return None
