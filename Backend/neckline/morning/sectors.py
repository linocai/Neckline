"""板块清单、日线和成分股材料。

概念每周从 TuShare 整份替换；日线按交易日补缺。纯函数只吃行和日期，不读库、不联网。
多日涨跌用涨跌幅连乘。均线上下用主线价格背景的同一条判定（差额四舍五入到四位小数后看符号）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import statistics
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Mapping, Sequence

from neckline.morning import db, stock_pool
from neckline.morning.schedule import CST, CalendarGap, is_trading_day

logger = logging.getLogger(__name__)

# ths_member 每分钟最多 200 次。0.35 秒大约 170 次，给同一次刷新里的其他调用留余量。
MEMBER_INTERVAL_SECONDS = 0.35
_TIMEOUT_SECONDS = 30
_PAGE_LIMIT = 5000
_DAILY_LIMIT = 6000
_INDEX_FIELDS = "ts_code,name,count,exchange,list_date,type"
_MEMBER_FIELDS = "ts_code,con_code,con_name"
_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,pct_chg,amount"
_LIMIT_FIELDS = "ts_code,trade_date"
_BASIC_FIELDS = "ts_code,name,fullname,list_date,list_status"
_COMPANY_FIELDS = "ts_code,main_business"
_NAMECHANGE_FIELDS = "ts_code,name,start_date,end_date,ann_date,change_reason"
_MV_FIELDS = "ts_code,trade_date,circ_mv,total_mv"


class SectorFetchError(RuntimeError):
    """TuShare 没能给出可用的板块数据。消息里不含 token。"""


@dataclass(frozen=True)
class ConceptRefresh:
    count: int
    sha256: str | None
    refreshed_at: str | None
    error: str | None
    replaced: bool


# 外呼前的预算用尽。调用方把它写成板块线缺口，不当成行情源故障。
FETCH_CUTOFF = "cutoff"


class _Stopped(Exception):
    """收口时刻已到，这一次外呼不再发出。"""


@dataclass(frozen=True)
class DailyFill:
    """error 交给板块线记缺口。主线不看这个结果。"""

    filled: tuple[str, ...]
    deleted: int
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None


def concepts_content_sha256(concepts: Sequence[Mapping]) -> str:
    """概念内容哈希。不含刷新时间，同一份清单重复刷新哈希不变。"""
    payload = []
    for item in sorted(concepts, key=lambda row: row["tsCode"]):
        payload.append({
            "memberCount": int(item["memberCount"]),
            "members": sorted(item["members"]),
            "name": item["name"],
            "tsCode": item["tsCode"],
        })
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def concepts_refresh_due(meta: Mapping | None, *, today: date, refresh_days: int) -> bool:
    """距上次成功刷新满 refresh_days 个自然日，或还从没成功过。失败不改成功时间。"""
    if isinstance(refresh_days, bool) or not isinstance(refresh_days, int) or refresh_days < 1:
        raise ValueError("refreshDays 无效")
    refreshed = None if meta is None else meta.get("refreshedAt")
    if not refreshed:
        return True
    stamp = datetime.fromisoformat(str(refreshed))
    if stamp.tzinfo is None:
        raise ValueError("刷新时间没有时区")
    return (today - stamp.astimezone(CST).date()).days >= refresh_days


def trading_days_ending(as_of: date, count: int) -> list[date]:
    """含 as_of 在内、往前数 count 个交易日。as_of 本身必须是交易日。"""
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("交易日数量无效")
    if not is_trading_day(as_of):
        raise ValueError("as_of 不是交易日")
    found: list[date] = []
    cursor = as_of
    while len(found) < count:
        if is_trading_day(cursor):
            found.append(cursor)
        cursor -= timedelta(days=1)
    found.reverse()
    return found


def trading_days_between(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("日期范围无效")
    found = []
    cursor = start
    while cursor <= end:
        if is_trading_day(cursor):
            found.append(cursor)
        cursor += timedelta(days=1)
    return found


def concept_list(concepts: Sequence[Mapping], members: Sequence[Mapping], pool_codes: Sequence[str],
                 rules: Mapping) -> dict:
    """当期可用的板块。成员数按池内代码算，名称套用剔除名单和正则，再套 150/3。"""
    names = set(rules["excludeNames"])
    patterns = [re.compile(item) for item in rules["excludePatterns"]]
    minimum, maximum = int(rules["minMembers"]), int(rules["maxMembers"])
    pool = _pool_index(pool_codes)
    by_concept: dict[str, list[str]] = {}
    for row in members:
        ts_code = _text(row.get("tsCode") or row.get("ts_code"))
        symbol = _symbol(row.get("code") if row.get("code") is not None else row.get("con_code"))
        if not ts_code or symbol is None or symbol not in pool:
            continue
        chosen = pool[symbol]
        bucket = by_concept.setdefault(ts_code, [])
        if chosen not in bucket:
            bucket.append(chosen)
    kept = []
    for concept in concepts:
        ts_code = _text(concept.get("tsCode") or concept.get("ts_code"))
        name = _text(concept.get("name"))
        if not ts_code or not name or name in names or any(pattern.search(name) for pattern in patterns):
            continue
        codes = sorted(by_concept.get(ts_code, []))
        if not minimum <= len(codes) <= maximum:
            continue
        kept.append({"tsCode": ts_code, "name": name, "members": codes, "count": len(codes)})
    kept.sort(key=lambda item: (item["name"], item["tsCode"]))
    return {"concepts": kept}


def board_material(member_codes: Sequence[str], daily_rows: Sequence[Mapping], as_of: date,
                   windows: Sequence[int], limit_window: int, baseline_codes: Sequence[str]) -> dict:
    """板块和创业板全体在各窗口的等权涨跌中位数、上涨占比，以及近几日涨停家数。

    多日涨跌是涨跌幅连乘。缺任何一天的成员不计入，并单独计数。不用 as_of 之后的行。
    """
    if isinstance(limit_window, bool) or not isinstance(limit_window, int) or limit_window < 1:
        raise ValueError("limitWindow 无效")
    if not windows or any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in windows):
        raise ValueError("窗口无效")
    indexed = _index_rows(daily_rows, as_of)
    members = _unique_symbols(member_codes)
    baseline = _unique_symbols(baseline_codes)
    span = max(max(windows), limit_window)
    calendar = {width: trading_days_ending(as_of, width) for width in set(windows) | {limit_window, span}}
    blocks = []
    for width in windows:
        sector = _window_stats(members, indexed, calendar[width])
        base = _window_stats(baseline, indexed, calendar[width])
        blocks.append({
            "days": width,
            "median": sector["median"], "upRatio": sector["upRatio"], "up": sector["up"],
            "counted": sector["counted"], "missing": sector["missing"],
            "baselineMedian": base["median"], "baselineUpRatio": base["upRatio"], "baselineUp": base["up"],
            "baselineCounted": base["counted"], "baselineMissing": base["missing"],
        })
    limit_days = calendar[limit_window]
    limit_ups, limit_missing = _limit_stats(members, indexed, limit_days)
    base_ups, base_missing = _limit_stats(baseline, indexed, limit_days)
    return {
        "asOf": as_of.isoformat(),
        "memberCount": len(members),
        "baselineCount": len(baseline),
        "windows": blocks,
        "limitWindow": limit_window,
        "limitUps": limit_ups,
        "limitMissing": limit_missing,
        "baselineLimitUps": base_ups,
        "baselineLimitMissing": base_missing,
    }


def member_facts(code: str, daily_rows: Sequence[Mapping], as_of: date, *, amount_window: int,
                 limit_window: int, change_window: int) -> dict:
    """近 amount_window 日日均成交额、近 limit_window 日涨停次数、前 change_window 日涨跌。

    口径与原来的龙头材料一致：窗口里缺任何一天，这一项留空。涨跌用涨跌幅连乘。
    不用 as_of 之后的行。成交额沿用库里的元。
    """
    symbol = _symbol(code)
    if symbol is None:
        raise ValueError("代码无效")
    for label, window in (("amountWindow", amount_window), ("limitWindow", limit_window),
                          ("changeWindow", change_window)):
        if isinstance(window, bool) or not isinstance(window, int) or window < 1:
            raise ValueError(f"{label} 无效")
    days = trading_days_ending(as_of, max(amount_window, limit_window, change_window))
    series = _index_rows(daily_rows, as_of).get(symbol, {})
    complete = {day for day in days if _complete_bar(series.get(day))}

    def ready(width: int) -> list[date] | None:
        chosen = days[-width:]
        if all(day in complete for day in chosen):
            return chosen
        return None

    amount_days = ready(amount_window)
    limit_days = ready(limit_window)
    change_days = ready(change_window)
    average = None if amount_days is None else sum(_num(series[day]["amount"]) for day in amount_days) / amount_window
    limits = None if limit_days is None else sum(1 for day in limit_days if series[day].get("limitUp"))
    change = None if change_days is None else _compound([_num(series[day]["pctChg"]) for day in change_days])  # type: ignore[misc]
    return {"averageAmountYuan": average, "limitUps": limits, "changePercent": change}


def refresh_concepts(conn, *, rules: Mapping, prefixes: Sequence[str], now: datetime,
                     post: Callable[..., dict] | None = None, environ: Mapping[str, str] | None = None,
                     sleep: Callable[[float], None] = time.sleep,
                     interval_seconds: float, before_call: Callable[[], bool] | None = None) -> ConceptRefresh:
    """拉同花顺概念并在调用方的事务里整表替换。失败只写错误，概念行保持原样。

    before_call 在每次外呼前调用，返回真就停，不替换清单。
    """
    _check_interval(interval_seconds)
    previous = db.read_sector_concepts(conn)
    try:
        concepts = _pull_concepts(prefixes=prefixes, post=post, environ=environ, sleep=sleep,
                                  interval_seconds=interval_seconds, before_call=before_call)
    except _Stopped:
        message = "收口时刻前没刷新完"
        db.set_sector_refresh_error(conn, message)
        logger.info("板块清单刷新停在收口时刻")
        return ConceptRefresh(
            count=0 if previous is None else previous["count"],
            sha256=None if previous is None else previous["sha256"],
            refreshed_at=None if previous is None else previous["refreshedAt"],
            error=message, replaced=False)
    except SectorFetchError as exc:
        message = str(exc)
        db.set_sector_refresh_error(conn, message)
        logger.error("板块清单没能刷新：%s", message)
        return ConceptRefresh(
            count=0 if previous is None else previous["count"],
            sha256=None if previous is None else previous["sha256"],
            refreshed_at=None if previous is None else previous["refreshedAt"],
            error=message, replaced=False)
    digest = concepts_content_sha256(concepts)
    refreshed_at = db.utc(now)
    db.replace_sector_concepts(conn, concepts, refreshed_at=refreshed_at, sha256=digest)
    return ConceptRefresh(len(concepts), digest, refreshed_at, None, True)


@dataclass(frozen=True)
class MarketDailyPlan:
    """缺哪些交易日。known 是取数时要留下的创业板代码，取数期间不再读库。"""

    window: tuple[date, ...]
    missing: tuple[date, ...]
    known: frozenset[str]
    prefixes: tuple[str, ...]
    error: str | None = None


def plan_market_daily(conn, *, as_of: date, lookback_days: int, prefixes: Sequence[str]) -> MarketDailyPlan:
    """只读：算出还缺哪些交易日。不联网。"""
    try:
        window = tuple(trading_days_ending(as_of, lookback_days))
    except (CalendarGap, ValueError) as exc:
        logger.error("日线没能补齐：%s", exc)
        return MarketDailyPlan((), (), frozenset(), tuple(prefixes), str(exc))
    known = frozenset(_known_codes(conn, prefixes))
    present = set(db.market_dates(conn))
    missing = tuple(day for day in window if day.isoformat() not in present)
    return MarketDailyPlan(window, missing, known, tuple(prefixes), None)


def pull_market_days(plan: MarketDailyPlan, *, post: Callable[..., dict] | None = None,
                     environ: Mapping[str, str] | None = None,
                     sleep: Callable[[float], None] = time.sleep,
                     interval_seconds: float,
                     before_call: Callable[[], bool] | None = None,
                     ) -> tuple[tuple[tuple[str, list[dict]], ...], str | None]:
    """只取数，不写库。收口或失败时带回已经取全的交易日。"""
    _check_interval(interval_seconds)
    if plan.error:
        return (), plan.error
    if not plan.missing or not plan.known:
        return (), None
    try:
        token, sender = _sender(post, environ)
    except SectorFetchError as exc:
        logger.error("日线没能补齐：%s", exc)
        return (), str(exc)
    pause = {"started": False}
    rows: list[tuple[str, list[dict]]] = []
    codes = set(plan.known)
    for day in plan.missing:
        try:
            bars = _pull_day(sender, token, day, codes, plan.prefixes, sleep=sleep,
                             interval_seconds=interval_seconds, pause=pause, before_call=before_call)
        except _Stopped:
            return tuple(rows), FETCH_CUTOFF
        except SectorFetchError as exc:
            logger.error("日线没能补齐：%s", exc)
            return tuple(rows), str(exc)
        rows.append((day.isoformat(), bars))
    return tuple(rows), None


def commit_market_daily(conn, plan: MarketDailyPlan, rows: Sequence[tuple[str, Sequence[Mapping]]], *,
                        error: str | None) -> DailyFill:
    """写入已经取到的交易日。失败时保留这些日子，不删窗口外的旧行。"""
    filled: list[str] = []
    for trade_date, bars in rows:
        db.write_market_day(conn, trade_date, bars)
        filled.append(trade_date)
    if error or not plan.window:
        return DailyFill(tuple(filled), 0, error)
    deleted = db.delete_market_daily_outside(conn, plan.window[0].isoformat(), plan.window[-1].isoformat())
    return DailyFill(tuple(filled), deleted, None)


def fill_market_daily(conn, *, as_of: date, lookback_days: int, prefixes: Sequence[str],
                      post: Callable[..., dict] | None = None, environ: Mapping[str, str] | None = None,
                      sleep: Callable[[float], None] = time.sleep,
                      interval_seconds: float, before_call: Callable[[], bool] | None = None) -> DailyFill:
    """补齐 as_of 往前 lookback_days 个交易日里还没有日线的日子，并删掉窗口外的行。

    只存当前池子或库里已经有过的创业板代码。失败返回 error，已经补上的日子保留；
    这种失败不抛给主线。晨报里的板块线不走这里：它把取数放到事务外，只等到收口。
    """
    _check_interval(interval_seconds)
    plan = plan_market_daily(conn, as_of=as_of, lookback_days=lookback_days, prefixes=prefixes)
    if plan.error:
        return DailyFill((), 0, plan.error)
    rows, error = pull_market_days(
        plan, post=post, environ=environ, sleep=sleep, interval_seconds=interval_seconds, before_call=before_call)
    return commit_market_daily(conn, plan, rows, error=error)


def capture_sector_payload(*, start: date, end: date, prefixes: Sequence[str],
                           post: Callable[..., dict] | None = None, environ: Mapping[str, str] | None = None,
                           sleep: Callable[[float], None] = time.sleep, interval_seconds: float,
                           progress: Callable[[str], None] | None = None) -> dict:
    """给 NB 用的回放快照：概念、成分、区间日线（只留创业板）、流通市值和股票池回放要的表。"""
    _check_interval(interval_seconds)
    token, sender = _sender(post, environ)
    pause = {"started": False}
    days = trading_days_between(start, end)
    index_rows = _call(sender, token, "ths_index", {"exchange": "A", "type": "N", "limit": _PAGE_LIMIT},
                       _INDEX_FIELDS, sleep=sleep, interval_seconds=interval_seconds, pause=pause)
    if not index_rows:
        raise SectorFetchError("ths_index 没有返回概念")
    members: list[dict] = []
    total = len(index_rows)
    for index, concept in enumerate(index_rows, start=1):
        ts_code = _text(concept.get("ts_code"))
        if not ts_code:
            raise SectorFetchError("ths_index 返回异常")
        page = _call(sender, token, "ths_member", {"ts_code": ts_code, "limit": _PAGE_LIMIT}, _MEMBER_FIELDS,
                     sleep=sleep, interval_seconds=interval_seconds, pause=pause)
        members.extend(page)
        if progress is not None:
            progress(f"concepts {index}/{total}")
    daily: list[dict] = []
    limits: list[dict] = []
    basic_mv: list[dict] = []
    for index, day in enumerate(days, start=1):
        stamp = day.strftime("%Y%m%d")
        bars = _call(sender, token, "daily", {"trade_date": stamp, "limit": _DAILY_LIMIT}, _DAILY_FIELDS,
                     sleep=sleep, interval_seconds=interval_seconds, pause=pause)
        _require_same_day(bars, day, "daily")
        daily.extend(row for row in bars if _normalize(row.get("ts_code"), prefixes))
        ups = _call(sender, token, "limit_list_d",
                    {"trade_date": stamp, "limit_type": "U", "limit": _PAGE_LIMIT}, _LIMIT_FIELDS,
                    sleep=sleep, interval_seconds=interval_seconds, pause=pause)
        _require_same_day(ups, day, "limit_list_d")
        limits.extend(row for row in ups if _normalize(row.get("ts_code"), prefixes))
        mv = _call(sender, token, "daily_basic", {"trade_date": stamp, "limit": _DAILY_LIMIT}, _MV_FIELDS,
                   sleep=sleep, interval_seconds=interval_seconds, pause=pause)
        _require_same_day(mv, day, "daily_basic")
        basic_mv.extend(row for row in mv if _normalize(row.get("ts_code"), prefixes))
        if progress is not None:
            progress(f"days {index}/{len(days)}")
    basic = _call(sender, token, "stock_basic",
                  {"exchange": "SZSE", "list_status": "L", "limit": _PAGE_LIMIT}, _BASIC_FIELDS,
                  sleep=sleep, interval_seconds=interval_seconds, pause=pause)
    company = _call(sender, token, "stock_company", {"exchange": "SZSE", "limit": _PAGE_LIMIT}, _COMPANY_FIELDS,
                    sleep=sleep, interval_seconds=interval_seconds, pause=pause)
    namechange = _call_paged(sender, token, "namechange", {}, _NAMECHANGE_FIELDS, sleep=sleep,
                             interval_seconds=interval_seconds, pause=pause)
    return {
        "ths_index": index_rows,
        "ths_member": members,
        "daily": daily,
        "limit_list_d": limits,
        "daily_basic": basic_mv,
        "stock_basic": basic,
        "stock_company": company,
        "namechange": namechange,
    }


def install_sector_snapshot(conn, payload: Mapping, *, refreshed_at: str, prefixes: Sequence[str]) -> dict:
    """把回放快照写进概念表和日线。日线只留创业板，成交额换成元。"""
    if not isinstance(payload, Mapping):
        raise SectorFetchError("板块快照不是对象")
    for key in ("ths_index", "ths_member", "daily", "limit_list_d"):
        if not isinstance(payload.get(key), list):
            raise SectorFetchError(f"板块快照缺少 {key}")
    concepts = concepts_from_raw(payload["ths_index"], payload["ths_member"], prefixes)
    bars = market_rows_from_raw(payload["daily"], payload["limit_list_d"], prefixes, codes=None)
    digest = concepts_content_sha256(concepts)
    db.replace_sector_concepts(conn, concepts, refreshed_at=refreshed_at, sha256=digest)
    conn.execute("DELETE FROM market_daily")
    grouped: dict[str, list[dict]] = {}
    for row in bars:
        grouped.setdefault(row["tradeDate"], []).append(row)
    for trade_date, rows in grouped.items():
        db.write_market_day(conn, trade_date, rows)
    return {"concepts": len(concepts), "days": len(grouped), "bars": len(bars), "sha256": digest}


def concepts_from_raw(index_rows: Sequence[Mapping], member_rows: Sequence[Mapping],
                      prefixes: Sequence[str]) -> list[dict]:
    grouped: dict[str, list[str]] = {}
    for row in member_rows:
        ts_code = _text(row.get("ts_code") or row.get("tsCode"))
        code = _normalize(row.get("con_code") or row.get("code"), prefixes)
        if not ts_code or code is None:
            continue
        bucket = grouped.setdefault(ts_code, [])
        if code not in bucket:
            bucket.append(code)
    concepts = []
    seen: set[str] = set()
    for row in index_rows:
        ts_code = _text(row.get("ts_code") or row.get("tsCode"))
        name = _text(row.get("name"))
        if not ts_code or not name or ts_code in seen:
            continue
        count = _whole(row.get("count") if "count" in row else row.get("memberCount"))
        if count is None:
            raise SectorFetchError("ths_index 返回异常")
        seen.add(ts_code)
        concepts.append({
            "tsCode": ts_code, "name": name, "memberCount": count,
            "members": sorted(grouped.get(ts_code, [])),
        })
    concepts.sort(key=lambda item: item["tsCode"])
    return concepts


def market_rows_from_raw(daily_rows: Sequence[Mapping], limit_rows: Sequence[Mapping], prefixes: Sequence[str],
                         codes: set[str] | None) -> list[dict]:
    limited = set()
    for row in limit_rows:
        code = _normalize(row.get("ts_code") or row.get("code"), prefixes)
        day = _as_date(row.get("trade_date") or row.get("tradeDate"))
        if code is None or day is None:
            continue
        if _text(row.get("limit")) not in ("", "U"):
            continue
        limited.add((code, day.isoformat()))
    bars = []
    seen: set[tuple[str, str]] = set()
    for row in daily_rows:
        code = _normalize(row.get("ts_code") or row.get("code"), prefixes)
        day = _as_date(row.get("trade_date") or row.get("tradeDate"))
        if code is None or day is None or (codes is not None and code not in codes):
            continue
        bar = _bar(row, code, day, (code, day.isoformat()) in limited)
        if bar is None or (code, bar["tradeDate"]) in seen:
            continue
        seen.add((code, bar["tradeDate"]))
        bars.append(bar)
    bars.sort(key=lambda item: (item["tradeDate"], item["code"]))
    return bars


def _pull_concepts(*, prefixes: Sequence[str], post: Callable[..., dict] | None,
                   environ: Mapping[str, str] | None, sleep: Callable[[float], None],
                   interval_seconds: float, before_call: Callable[[], bool] | None = None) -> list[dict]:
    token, sender = _sender(post, environ)
    pause = {"started": False}
    index_rows = _call(sender, token, "ths_index", {"exchange": "A", "type": "N", "limit": _PAGE_LIMIT},
                       _INDEX_FIELDS, sleep=sleep, interval_seconds=interval_seconds, pause=pause,
                       before_call=before_call)
    if not index_rows:
        raise SectorFetchError("ths_index 没有返回概念")
    members: list[dict] = []
    for concept in index_rows:
        ts_code = _text(concept.get("ts_code"))
        if not ts_code:
            raise SectorFetchError("ths_index 返回异常")
        page = _call(sender, token, "ths_member", {"ts_code": ts_code, "limit": _PAGE_LIMIT}, _MEMBER_FIELDS,
                     sleep=sleep, interval_seconds=interval_seconds, pause=pause, before_call=before_call)
        members.extend(page)
    concepts = concepts_from_raw(index_rows, members, prefixes)
    if not concepts:
        raise SectorFetchError("ths_index 没有返回概念")
    return concepts


def _pull_day(post: Callable[..., dict], token: str, day: date, codes: set[str], prefixes: Sequence[str], *,
              sleep: Callable[[float], None], interval_seconds: float, pause: dict,
              before_call: Callable[[], bool] | None = None) -> list[dict]:
    stamp = day.strftime("%Y%m%d")
    daily_rows = _call(post, token, "daily", {"trade_date": stamp, "limit": _DAILY_LIMIT}, _DAILY_FIELDS,
                       sleep=sleep, interval_seconds=interval_seconds, pause=pause, before_call=before_call)
    _require_same_day(daily_rows, day, "daily")
    if not daily_rows:
        raise SectorFetchError(f"{day.isoformat()} 没有返回日线")
    limit_rows = _call(post, token, "limit_list_d",
                       {"trade_date": stamp, "limit_type": "U", "limit": _PAGE_LIMIT}, _LIMIT_FIELDS,
                       sleep=sleep, interval_seconds=interval_seconds, pause=pause, before_call=before_call)
    _require_same_day(limit_rows, day, "limit_list_d")
    bars = market_rows_from_raw(daily_rows, limit_rows, prefixes, codes)
    if not bars:
        raise SectorFetchError(f"{day.isoformat()} 没有可写入的创业板日线")
    return bars


def _known_codes(conn, prefixes: Sequence[str]) -> set[str]:
    found: set[str] = set()
    stored = db.read_stock_pool(conn)
    if stored is not None:
        for row in stored["rows"]:
            code = _normalize(row.get("code"), prefixes)
            if code is not None:
                found.add(code)
    for code in db.market_codes(conn):
        normalized = _normalize(code, prefixes)
        if normalized is not None:
            found.add(normalized)
    return found


def _sender(post: Callable[..., dict] | None, environ: Mapping[str, str] | None) -> tuple[str, Callable[..., dict]]:
    env = environ if environ is not None else os.environ
    token = env.get("TUSHARE_TOKEN", "")
    if not token:
        raise SectorFetchError("未配置 TUSHARE_TOKEN")
    return token, post or stock_pool.http_post


def _call(post: Callable[..., dict], token: str, api: str, params: Mapping, fields: str, *,
          sleep: Callable[[float], None], interval_seconds: float, pause: dict,
          before_call: Callable[[], bool] | None = None) -> list[dict]:
    _pause(pause, sleep, interval_seconds)
    if before_call is not None and before_call():
        raise _Stopped()
    rows, more = _fetch_page(post, token, api, params, fields)
    if more:
        raise SectorFetchError(f"{api} 没有一次取全")
    return rows


def _call_paged(post: Callable[..., dict], token: str, api: str, params: Mapping, fields: str, *,
                sleep: Callable[[float], None], interval_seconds: float, pause: dict,
                before_call: Callable[[], bool] | None = None) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        _pause(pause, sleep, interval_seconds)
        if before_call is not None and before_call():
            raise _Stopped()
        page, more = _fetch_page(post, token, api, {**params, "limit": _PAGE_LIMIT, "offset": offset}, fields)
        rows.extend(page)
        if not more:
            return rows
        if not page:
            raise SectorFetchError(f"{api} 没有一次取全")
        offset += len(page)


def _fetch_page(post: Callable[..., dict], token: str, api: str, params: Mapping, fields: str
                ) -> tuple[list[dict], bool]:
    try:
        body = post(stock_pool.API_URL, {"api_name": api, "token": token, "params": dict(params), "fields": fields},
                    _TIMEOUT_SECONDS)
    except Exception as exc:
        text = str(exc)
        if token and token in text:
            raise SectorFetchError(f"{api} 请求失败") from None
        raise SectorFetchError(f"{api} 请求失败") from None
    if not isinstance(body, Mapping) or body.get("code") != 0 or not isinstance(body.get("data"), Mapping):
        raise SectorFetchError(f"{api} 返回异常")
    data = body["data"]
    names = data.get("fields")
    items = data.get("items")
    if not isinstance(names, list) or not isinstance(items, list):
        raise SectorFetchError(f"{api} 返回异常")
    return [dict(zip(names, row)) for row in items], bool(data.get("has_more"))


def _pause(pause: dict, sleep: Callable[[float], None], interval_seconds: float) -> None:
    if pause["started"] and interval_seconds > 0:
        sleep(interval_seconds)
    pause["started"] = True


def _require_same_day(rows: Sequence[Mapping], day: date, api: str) -> None:
    for row in rows:
        seen = _as_date(row.get("trade_date") or row.get("tradeDate"))
        if seen != day:
            raise SectorFetchError(f"{day.isoformat()} 的{api}交易日对不上")


def _bar(row: Mapping, code: str, day: date, limit_up: bool) -> dict | None:
    open_ = _num(row.get("open"))
    high = _num(row.get("high"))
    low = _num(row.get("low"))
    close = _num(row.get("close"))
    pre_close = _num(row.get("pre_close") if "pre_close" in row else row.get("preClose"))
    pct = _num(row.get("pct_chg") if "pct_chg" in row else row.get("pctChg"))
    amount = _num(row.get("amount"))
    if None in (open_, high, low, close, pre_close, pct, amount):
        return None
    if close <= 0 or amount < 0:
        return None
    # TuShare daily.amount 是千元，库里记元。
    return {
        "code": code, "tradeDate": day.isoformat(), "open": open_, "high": high, "low": low, "close": close,
        "preClose": pre_close, "pctChg": pct, "amount": amount * 1000, "limitUp": limit_up,
    }


def _window_stats(symbols: Sequence[str], indexed: Mapping[str, Mapping[date, Mapping]],
                  dates: Sequence[date]) -> dict:
    returns = []
    missing = 0
    for symbol in symbols:
        series = indexed.get(symbol, {})
        pcts = []
        complete = True
        for day in dates:
            pct = None if day not in series else _num(series[day].get("pctChg"))
            if pct is None:
                complete = False
                break
            pcts.append(pct)
        if not complete:
            missing += 1
            continue
        returns.append(_compound(pcts))
    up = sum(1 for value in returns if value > 0)
    counted = len(returns)
    return {
        "median": None if not returns else float(statistics.median(returns)),
        "upRatio": None if counted == 0 else up / counted,
        "up": up, "counted": counted, "missing": missing,
    }


def _limit_stats(symbols: Sequence[str], indexed: Mapping[str, Mapping[date, Mapping]],
                 dates: Sequence[date]) -> tuple[int, int]:
    ups = 0
    missing = 0
    for symbol in symbols:
        series = indexed.get(symbol, {})
        hit = False
        complete = True
        for day in dates:
            row = series.get(day)
            if row is None or _num(row.get("pctChg")) is None:
                complete = False
                break
            if row.get("limitUp"):
                hit = True
        if not complete:
            missing += 1
        elif hit:
            ups += 1
    return ups, missing


def _compound(pcts: Sequence[float]) -> float:
    factor = 1.0
    for pct in pcts:
        factor *= 1 + pct / 100
    return (factor - 1) * 100


def _index_rows(daily_rows: Sequence[Mapping], as_of: date) -> dict[str, dict[date, Mapping]]:
    indexed: dict[str, dict[date, Mapping]] = {}
    for raw in daily_rows:
        symbol = _symbol(raw.get("code"))
        day = _as_date(raw.get("tradeDate") or raw.get("trade_date"))
        if symbol is None or day is None or day > as_of:
            continue
        indexed.setdefault(symbol, {})[day] = raw
    return indexed


def _complete_bar(row: Mapping | None) -> bool:
    if row is None:
        return False
    return _num(row.get("close")) is not None and _num(row.get("pctChg")) is not None and _num(row.get("amount")) is not None


def _pool_index(codes: Sequence[str]) -> dict[str, str]:
    found = {}
    for _symbol_code, shown in _unique_pairs(codes):
        found.setdefault(_symbol_code, shown)
    return found


def _unique_symbols(codes: Sequence[str]) -> list[str]:
    return [symbol for symbol, _shown in _unique_pairs(codes)]


def _unique_pairs(codes: Sequence[str]) -> list[tuple[str, str]]:
    found = []
    seen: set[str] = set()
    for code in codes:
        symbol = _symbol(code)
        if symbol is None or symbol in seen:
            continue
        seen.add(symbol)
        text = _text(code)
        found.append((symbol, text if "." in text else f"{symbol}.SZ"))
    return found


def _normalize(value, prefixes: Sequence[str]) -> str | None:
    symbol = _symbol(value)
    if symbol is None or not any(symbol.startswith(prefix) for prefix in prefixes):
        return None
    text = _text(value).upper()
    suffix = text.split(".", 1)[1] if "." in text else "SZ"
    if suffix != "SZ":
        return None
    return f"{symbol}.SZ"


def _symbol(value) -> str | None:
    text = _text(value).upper().split(".", 1)[0]
    return text if len(text) == 6 and text.isdigit() else None


def _whole(value) -> int | None:
    number = _num(value)
    if number is None or not number.is_integer() or number < 0:
        return None
    return int(number)


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    if len(text) == 8 and text.isdigit():
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except ValueError:
            return None
    return None


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _check_interval(interval_seconds: float) -> None:
    if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, (int, float)):
        raise ValueError("调用间隔无效")
    if interval_seconds < 0 or not math.isfinite(interval_seconds):
        raise ValueError("调用间隔无效")
