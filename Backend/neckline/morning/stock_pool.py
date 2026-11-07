"""当天的创业板股票池：按配置规则从 TuShare 行构造，整份写入库。

构造是纯函数。刷新在晨报开工时做一次；失败沿用库里上一份，库里从没有过就如实失败。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Iterable, Mapping, Sequence

from ..screening.pool import Company, CompanyPool
from . import db
from .schedule import CalendarGap, previous_trading_day

API_URL = "https://api.tushare.pro"
_BASIC_FIELDS = "ts_code,name,fullname,list_date,list_status"
_COMPANY_FIELDS = "ts_code,main_business"
_MV_FIELDS = "ts_code,trade_date,circ_mv,total_mv"
_TIMEOUT_SECONDS = 30
_PAGE_LIMIT = 6000


class PoolFetchError(RuntimeError):
    """TuShare 没能给出一份可用的股票池。消息里不含 token。"""


def content_sha256(rows: Sequence[Mapping]) -> str:
    """池子内容哈希。不含刷新时间和来源，同一批公司重复刷新哈希不变。"""
    payload = [{
        "aliases": list(row.get("aliases") or ()),
        "circMv": row.get("circMv"),
        "code": row["code"],
        "fullname": row.get("fullname") or "",
        "listDate": row.get("listDate") or "",
        "mvDate": row.get("mvDate") or "",
        "name": row["name"],
        "summary": row.get("summary"),
        "totalMv": row.get("totalMv"),
    } for row in sorted(rows, key=lambda item: item["code"])]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def build_pool_rows(basic_rows: Iterable[Mapping], company_rows: Iterable[Mapping], *,
                    namechange_rows: Iterable[Mapping] = (), seed_rows: Iterable[Mapping] = (),
                    as_of: date, today: date, prefixes: Sequence[str], exclude_name_contains: str,
                    summary_max_chars: int, previous_summaries: Mapping[str, str | None] | None = None,
                    mv_rows: Iterable[Mapping] = (), exclude_top_share: float | None = None,
                    mv_date: date | None = None, excluded: list | None = None) -> list[dict]:
    """按当天规则筛出池子行。

    as_of 早于 today 时用 namechange 还原当天简称，并据此判断当时是否 ST。
    主营摘要优先用别名种子，其次本次 stock_company，再次库里上一份。
    给了 exclude_top_share 时，在去掉 ST 之后按流通市值去掉最大的那一截。
    没有 circ_mv 的留下。被去掉的写入 excluded（代码、简称、circ_mv）。
    """
    if summary_max_chars < 1:
        raise ValueError("主营摘要截短长度无效")
    seeds = {_symbol(row.get("code")): row for row in seed_rows if _symbol(row.get("code"))}
    businesses = {_symbol(row.get("ts_code") or row.get("code")): _text(row.get("main_business"))
                  for row in company_rows}
    names_on = _names_covering(namechange_rows, as_of) if as_of < today else {}
    previous = previous_summaries or {}
    built: list[dict] = []
    for raw in basic_rows:
        code = _symbol(raw.get("ts_code") or raw.get("code"))
        if code is None or not any(code.startswith(prefix) for prefix in prefixes):
            continue
        if _text(raw.get("list_status")) != "L":
            continue
        listed = _parse_date(raw.get("list_date"))
        if listed is None or listed > as_of:
            continue
        current = _text(raw.get("name"))
        name = names_on.get(code, current)
        if not name or exclude_name_contains in name:
            continue
        seed = seeds.get(code)
        aliases = _aliases(name, _text(raw.get("fullname")), seed)
        summary, source = _summary(code, seed, businesses, previous, summary_max_chars)
        built.append({
            "code": f"{code}.SZ", "name": name, "fullname": _text(raw.get("fullname")),
            "listDate": listed.isoformat(), "aliases": aliases, "summary": summary,
            "summarySource": source,
        })
    built.sort(key=lambda row: row["code"])
    if exclude_top_share is None:
        return built
    return _drop_largest_circ(built, mv_rows, exclude_top_share=exclude_top_share, mv_date=mv_date,
                              excluded=excluded)


def _drop_largest_circ(rows: list[dict], mv_rows: Iterable[Mapping], *, exclude_top_share: float,
                       mv_date: date | None, excluded: list | None) -> list[dict]:
    """N 是去掉 ST 之后的只数，k = floor(N × excludeTopShare)。有市值的按从大到小、代码从小到大去掉前 k 只。"""
    if isinstance(exclude_top_share, bool) or not isinstance(exclude_top_share, (int, float)):
        raise ValueError("excludeTopShare 无效")
    share = float(exclude_top_share)
    if not math.isfinite(share) or not 0 < share < 1:
        raise ValueError("excludeTopShare 无效")
    indexed = _mv_index(mv_rows, mv_date)
    stamp = mv_date.isoformat() if mv_date is not None else None
    for row in rows:
        circ, total = indexed.get(row["code"].split(".", 1)[0], (None, None))
        row["circMv"] = circ
        row["totalMv"] = total
        row["mvDate"] = stamp
    ranked = [row for row in rows if row["circMv"] is not None]
    ranked.sort(key=lambda row: (-row["circMv"], row["code"]))
    k = math.floor(len(rows) * share)
    dropped = ranked[:k]
    if excluded is not None:
        excluded.clear()
        excluded.extend({"code": row["code"], "name": row["name"], "circMv": row["circMv"]} for row in dropped)
    gone = {row["code"] for row in dropped}
    return [row for row in rows if row["code"] not in gone]


def _mv_index(mv_rows: Iterable[Mapping], mv_date: date | None) -> dict[str, tuple[float | None, float | None]]:
    found: dict[str, tuple[float | None, float | None]] = {}
    for raw in mv_rows:
        code = _symbol(raw.get("ts_code") or raw.get("code"))
        if code is None:
            continue
        seen = _parse_date(raw.get("trade_date") if raw.get("trade_date") is not None else raw.get("tradeDate"))
        if mv_date is not None and seen is not None and seen != mv_date:
            continue
        circ = _mv_number(raw.get("circ_mv") if "circ_mv" in raw else raw.get("circMv"))
        total = _mv_number(raw.get("total_mv") if "total_mv" in raw else raw.get("totalMv"))
        found[code] = (circ, total)
    return found


def _mv_number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value < 0:
        return None
    return float(value)


def rows_from_seed(seed_rows: Iterable[Mapping], *, summary_max_chars: int) -> list[dict]:
    """把别名种子原样收成池子行（对照组用，不再按 ST 或上市状态重筛）。"""
    rows = []
    for raw in seed_rows:
        code = _text(raw.get("code"))
        if not code:
            continue
        index = raw.get("index") or {}
        summary = _text(index.get("summary")) or None
        if summary and len(summary) > summary_max_chars:
            summary = summary[:summary_max_chars]
        aliases = [alias for alias in index.get("aliases") or () if isinstance(alias, str) and len(alias) >= 2]
        rows.append({
            "code": code, "name": _text(raw.get("name")), "fullname": "", "listDate": "",
            "aliases": aliases, "summary": summary, "summarySource": "alias-seed" if summary else "none",
        })
    rows.sort(key=lambda row: row["code"])
    return rows


def rows_from_pool(pool: CompanyPool, profiles: Mapping[str, str]) -> list[dict]:
    """测试注入的公司池。生产配置的池子是空的，不走这里。"""
    rows = []
    for company in sorted(pool.companies.values(), key=lambda item: item.code):
        summary = profiles.get(company.code) or None
        rows.append({
            "code": company.code, "name": company.name, "fullname": "", "listDate": "",
            "aliases": [alias for alias in company.aliases if len(alias) >= 2],
            "summary": summary, "summarySource": "given" if summary else "none",
        })
    return rows


def pool_from_rows(rows: Sequence[Mapping], *, strict_names: Sequence[str] = ()) -> tuple[CompanyPool, dict[str, str]]:
    companies = []
    profiles: dict[str, str] = {}
    for row in rows:
        aliases = tuple(alias for alias in row.get("aliases") or () if isinstance(alias, str))
        companies.append(Company(code=row["code"], name=row["name"], aliases=aliases))
        if row.get("summary"):
            profiles[row["code"]] = row["summary"]
    return CompanyPool(companies, strict_names=strict_names), profiles


def load_seed_rows(path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


@dataclass(frozen=True)
class RefreshResult:
    rows: tuple[dict, ...]
    sha256: str
    refreshed_at: str
    source: str
    reused: bool
    gap: str | None
    excluded: int = 0
    mv_date: str | None = None


def refresh_stock_pool(conn, *, rules: Mapping, seed_rows: Sequence[Mapping], as_of: date, today: date,
                       now: datetime, post: Callable[..., dict] | None = None,
                       environ: Mapping[str, str] | None = None) -> RefreshResult:
    """拉 TuShare 并整表替换。失败时不改库，由调用方决定沿用还是失败。"""
    env = environ if environ is not None else os.environ
    token = env.get("TUSHARE_TOKEN", "")
    if not token:
        raise PoolFetchError("未配置 TUSHARE_TOKEN")
    sender = post or http_post
    try:
        mv_day = previous_trading_day(as_of)
    except CalendarGap as exc:
        raise PoolFetchError(str(exc)) from None
    share = _exclude_share(rules)
    basic = _fetch(sender, token, "stock_basic", {"exchange": "SZSE", "list_status": "L"}, _BASIC_FIELDS)
    prefixes = tuple(rules["prefixes"])
    included = []
    for raw in basic:
        code = _symbol(raw.get("ts_code"))
        if code and any(code.startswith(prefix) for prefix in prefixes) and _text(raw.get("list_status")) == "L":
            listed = _parse_date(raw.get("list_date"))
            if listed is not None and listed <= as_of and rules["excludeNameContains"] not in _text(raw.get("name")):
                included.append(code)
    seed_summaries = _seed_summaries(seed_rows)
    stored = _stored_summaries(conn)
    missing = [code for code in included if not seed_summaries.get(code) and not stored.get(code)]
    company_rows: list[dict] = []
    if missing:
        company_rows = _fetch(sender, token, "stock_company", {"exchange": "SZSE"}, _COMPANY_FIELDS)
    if not included:
        raise PoolFetchError("stock_basic 没有返回在市的创业板股票")
    mv_rows = _fetch_daily_basic(sender, token, mv_day, prefixes)
    dropped: list[dict] = []
    rows = build_pool_rows(
        basic, company_rows, seed_rows=seed_rows, as_of=as_of, today=today, prefixes=prefixes,
        exclude_name_contains=rules["excludeNameContains"], summary_max_chars=int(rules["summaryMaxChars"]),
        previous_summaries=stored, mv_rows=mv_rows, exclude_top_share=share, mv_date=mv_day, excluded=dropped)
    if not rows:
        # 接口偶尔回空表而不报错；空池子会让整期没有候选，按刷新失败处理
        raise PoolFetchError("stock_basic 没有返回在市的创业板股票")
    return _store(conn, rows, now=now, source="tushare", excluded=dropped, mv_date=mv_day.isoformat(),
                  exclude_top_share=share, excluded_count=len(dropped))


def store_rows(conn, rows: Sequence[Mapping], *, now: datetime, source: str, excluded: Sequence[Mapping] = (),
               mv_date: str | None = None, exclude_top_share: float | None = None) -> RefreshResult:
    return _store(conn, list(rows), now=now, source=source, excluded=excluded, mv_date=mv_date,
                  exclude_top_share=exclude_top_share)


def _store(conn, rows: list[dict], *, now: datetime, source: str, excluded: Sequence[Mapping] = (),
           mv_date: str | None = None, exclude_top_share: float | None = None,
           excluded_count: int | None = None) -> RefreshResult:
    digest = content_sha256(rows)
    refreshed_at = db.utc(now)
    db.replace_stock_pool(conn, rows, refreshed_at=refreshed_at, sha256=digest, source=source,
                          excluded=excluded, mv_date=mv_date, exclude_top_share=exclude_top_share)
    count = len(excluded) if excluded_count is None else excluded_count
    return RefreshResult(tuple(rows), digest, refreshed_at, source, False, None, count, mv_date)


def _exclude_share(rules: Mapping) -> float:
    circ = rules.get("circMv")
    if not isinstance(circ, Mapping) or "excludeTopShare" not in circ:
        raise PoolFetchError("companyPool.circMv 无效")
    share = circ.get("excludeTopShare")
    if isinstance(share, bool) or not isinstance(share, (int, float)):
        raise PoolFetchError("companyPool.circMv.excludeTopShare 无效")
    value = float(share)
    if not math.isfinite(value) or not 0 < value < 1:
        raise PoolFetchError("companyPool.circMv.excludeTopShare 无效")
    return value


def _fetch_daily_basic(post: Callable[..., dict], token: str, day: date, prefixes: Sequence[str]) -> list[dict]:
    """取上一个交易日的全市场日指标，只留池子前缀。取不到、报错或 0 行都是刷新失败。"""
    stamp = day.strftime("%Y%m%d")
    rows: list[dict] = []
    offset = 0
    while True:
        page, more = _fetch_page(post, token, "daily_basic",
                                 {"trade_date": stamp, "limit": _PAGE_LIMIT, "offset": offset}, _MV_FIELDS)
        rows.extend(page)
        if not more:
            break
        if not page:
            raise PoolFetchError("daily_basic 没有一次取全")
        offset += len(page)
    matched = []
    for row in rows:
        seen = _parse_date(row.get("trade_date"))
        code = _symbol(row.get("ts_code"))
        if seen != day or code is None or not any(code.startswith(prefix) for prefix in prefixes):
            continue
        matched.append(row)
    if not rows or not matched:
        raise PoolFetchError("daily_basic 没有返回市值")
    return matched


def _seed_summaries(seed_rows: Iterable[Mapping]) -> dict[str, str]:
    found = {}
    for row in seed_rows:
        code = _symbol(row.get("code"))
        text = _text((row.get("index") or {}).get("summary"))
        if code and text:
            found[code] = text
    return found


def _stored_summaries(conn) -> dict[str, str | None]:
    try:
        current = db.read_stock_pool(conn)
    except db.NotReady:
        return {}
    if current is None:
        return {}
    return {_symbol(row["code"]): row.get("summary") for row in current["rows"] if _symbol(row["code"])}


def _fetch(post: Callable[..., dict], token: str, api: str, params: Mapping, fields: str) -> list[dict]:
    rows, more = _fetch_page(post, token, api, params, fields)
    if more:
        raise PoolFetchError(f"{api} 没有一次取全")
    return rows


def _fetch_page(post: Callable[..., dict], token: str, api: str, params: Mapping, fields: str
                ) -> tuple[list[dict], bool]:
    try:
        body = post(API_URL, {"api_name": api, "token": token, "params": dict(params), "fields": fields},
                    _TIMEOUT_SECONDS)
    except PoolFetchError as exc:
        if token and token in str(exc):
            raise PoolFetchError(f"{api} 请求失败") from None
        raise
    except Exception:
        raise PoolFetchError(f"{api} 请求失败") from None
    if not isinstance(body, Mapping) or body.get("code") != 0 or not isinstance(body.get("data"), Mapping):
        raise PoolFetchError(f"{api} 返回异常")
    data = body["data"]
    names = data.get("fields")
    items = data.get("items")
    if not isinstance(names, list) or not isinstance(items, list):
        raise PoolFetchError(f"{api} 返回异常")
    return [dict(zip(names, row)) for row in items], bool(data.get("has_more"))


def http_post(url: str, payload: Mapping, timeout: float) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise PoolFetchError(type(exc).__name__) from None


def _symbol(value) -> str | None:
    text = _text(value).split(".", 1)[0]
    return text if len(text) == 6 and text.isdigit() else None


def _parse_date(value) -> date | None:
    text = _text(value)
    if len(text) == 8 and text.isdigit():
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except ValueError:
            return None
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    return None


def _names_covering(rows: Iterable[Mapping], as_of: date) -> dict[str, str]:
    chosen: dict[str, tuple[date, str]] = {}
    for row in rows:
        code = _symbol(row.get("ts_code") or row.get("code"))
        start = _parse_date(row.get("start_date"))
        end = _parse_date(row.get("end_date"))
        name = _text(row.get("name"))
        if code is None or start is None or end is None or not name:
            continue
        if start <= as_of <= end and (code not in chosen or start >= chosen[code][0]):
            chosen[code] = (start, name)
    return {code: name for code, (_, name) in chosen.items()}


def _aliases(name: str, fullname: str, seed: Mapping | None) -> list[str]:
    found: list[str] = []
    for candidate in (name, fullname):
        if isinstance(candidate, str) and len(candidate) >= 2 and candidate not in found:
            found.append(candidate)
    if seed:
        for alias in (seed.get("index") or {}).get("aliases") or ():
            if isinstance(alias, str) and len(alias) >= 2 and alias not in found:
                found.append(alias)
    return found


def _summary(code: str, seed: Mapping | None, businesses: Mapping[str, str], previous: Mapping[str, str | None],
             limit: int) -> tuple[str | None, str]:
    text = ""
    source = "none"
    if seed:
        text = _text((seed.get("index") or {}).get("summary"))
        if text:
            source = "alias-seed"
    if not text:
        text = businesses.get(code, "")
        if text:
            source = "stock_company"
    if not text:
        earlier = previous.get(code)
        text = earlier or ""
        if text:
            source = "stock_company"
    if not text:
        return None, "none"
    return text[:limit], source


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""
