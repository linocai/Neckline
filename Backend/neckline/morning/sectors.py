"""板块清单和回放快照。

概念每周从 TuShare 整份替换。回放快照留下概念、成分、流通市值和股票池要的表。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Mapping, Sequence

from neckline.morning import db, stock_pool
from neckline.morning.schedule import CST, is_trading_day

logger = logging.getLogger(__name__)

# ths_member 每分钟最多 200 次。0.35 秒大约 170 次，给同一次刷新里的其他调用留余量。
MEMBER_INTERVAL_SECONDS = 0.35
_TIMEOUT_SECONDS = 30
_PAGE_LIMIT = 5000
_DAILY_LIMIT = 6000
_INDEX_FIELDS = "ts_code,name,count,exchange,list_date,type"
_MEMBER_FIELDS = "ts_code,con_code,con_name"
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


class _Stopped(Exception):
    """收口时刻已到，这一次外呼不再发出。"""


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


def capture_sector_payload(*, start: date, end: date, prefixes: Sequence[str],
                           post: Callable[..., dict] | None = None, environ: Mapping[str, str] | None = None,
                           sleep: Callable[[float], None] = time.sleep, interval_seconds: float,
                           progress: Callable[[str], None] | None = None) -> dict:
    """给 NB 用的回放快照：概念、成分、流通市值和股票池回放要的表。start/end 仍校验交易日区间。"""
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
    basic_mv: list[dict] = []
    for index, day in enumerate(days, start=1):
        stamp = day.strftime("%Y%m%d")
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
        "daily_basic": basic_mv,
        "stock_basic": basic,
        "stock_company": company,
        "namechange": namechange,
    }


def install_sector_snapshot(conn, payload: Mapping, *, refreshed_at: str, prefixes: Sequence[str]) -> dict:
    """把回放快照写进概念表。旧快照里的日线键忽略，不写 market_daily。"""
    if not isinstance(payload, Mapping):
        raise SectorFetchError("板块快照不是对象")
    for key in ("ths_index", "ths_member"):
        if not isinstance(payload.get(key), list):
            raise SectorFetchError(f"板块快照缺少 {key}")
    concepts = concepts_from_raw(payload["ths_index"], payload["ths_member"], prefixes)
    digest = concepts_content_sha256(concepts)
    db.replace_sector_concepts(conn, concepts, refreshed_at=refreshed_at, sha256=digest)
    return {"concepts": len(concepts), "sha256": digest}


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


def _pool_index(codes: Sequence[str]) -> dict[str, str]:
    found = {}
    for _symbol_code, shown in _unique_pairs(codes):
        found.setdefault(_symbol_code, shown)
    return found


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
