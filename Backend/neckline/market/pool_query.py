"""在 K10 池里查一只股票。池子来自晨报库里的当前股票池。没有返回整份名单的函数。"""
from __future__ import annotations

import re
import sqlite3

from neckline.morning import db
from neckline.morning.api import v4_db_path
from neckline.morning.report import load_config
from neckline.morning.stock_pool import pool_from_rows
from neckline.screening.pool import Company, CompanyPool

_CODE = re.compile(r"^(?:(SH|SZ|BJ))?(\d{6})(?:\.(SH|SZ|BJ))?$", re.IGNORECASE)
_NOT_IN_POOL = "不在 K10 股票池"
_POOL_CACHE: dict[tuple[str, str], CompanyPool] = {}
_STRICT_CACHE: dict[str, tuple[str, ...]] = {}


class PoolIndex:
    def __init__(self, pool: CompanyPool) -> None:
        self.by_short: dict[str, Company] = {}
        self.by_name: dict[str, list[Company]] = {}
        for company in pool.companies.values():
            self.by_short[company.short_code] = company
            for label in {company.name, *company.aliases}:
                if not label:
                    continue
                bucket = self.by_name.setdefault(label, [])
                if all(item.code != company.code for item in bucket):
                    bucket.append(company)

    def lookup(self, query: str) -> dict:
        text = (query or "").strip()
        matched = _CODE.fullmatch(text)
        if matched:
            # 任何六位代码都按代码查；池内只有深市创业板，带了别的市场前后缀就是池外。
            markets = {item.upper() for item in (matched.group(1), matched.group(3)) if item}
            company = self.by_short.get(matched.group(2)) if markets <= {"SZ"} else None
            if company is None:
                return {"inPool": False, "message": _NOT_IN_POOL}
            return {"inPool": True, "code": company.code, "name": company.name}
        found = self.by_name.get(text) or []
        if not found:
            # 名称只认简称、全称和别名的原样写法；写法不同不等于池外，提示用代码确认。
            return {"inPool": False, "message": _NOT_IN_POOL + "（按名称没找到；名称写法可能不同，请用六位代码再查一次）"}
        if len(found) == 1:
            company = found[0]
            return {"inPool": True, "code": company.code, "name": company.name}
        chosen = sorted(found, key=lambda item: item.code)[:5]
        payload: dict = {
            "inPool": True,
            "candidates": [{"code": item.code, "name": item.name} for item in chosen],
            "message": "名称对应多家公司，请用六位代码指定",
        }
        if len(found) > 5:
            payload["truncated"] = True
        return payload


def _strict_names() -> tuple[str, ...]:
    loaded = load_config()
    cached = _STRICT_CACHE.get(loaded.sha256)
    if cached is None:
        cached = tuple(loaded.rules["matching"]["strictShortNames"])
        _STRICT_CACHE[loaded.sha256] = cached
    return cached


def _load_pool() -> CompanyPool:
    """API 进程只读晨报库。没有库、库还没升级、或还没刷新过，都当作空池。"""
    path = v4_db_path()
    if path is None or not path.is_file():
        return CompanyPool(())
    try:
        with db.read(path) as conn:
            stored = db.read_stock_pool(conn)
    except (db.NotReady, sqlite3.OperationalError):
        return CompanyPool(())
    if stored is None:
        return CompanyPool(())
    key = (str(path), stored["sha256"])
    cached = _POOL_CACHE.get(key)
    if cached is not None:
        return cached
    pool, _profiles = pool_from_rows(stored["rows"], strict_names=_strict_names())
    _POOL_CACHE[key] = pool
    return pool


def lookup(query: str, pool: CompanyPool | None = None) -> dict:
    index = PoolIndex(pool) if pool is not None else PoolIndex(_load_pool())
    return index.lookup(query)


def _from_match(pool: CompanyPool, found: dict[str, list[str]]) -> dict:
    if len(found) == 1:
        code = next(iter(found))
        company = pool.companies[code]
        return {"inPool": True, "code": company.code, "name": company.name}
    chosen = sorted((pool.companies[code] for code in found), key=lambda item: item.code)[:5]
    payload: dict = {
        "inPool": True,
        "candidates": [{"code": item.code, "name": item.name} for item in chosen],
        "message": "名称对应多家公司，请用六位代码指定",
    }
    if len(found) > 5:
        payload["truncated"] = True
    return payload


def assess(query: str, pool: CompanyPool | None = None) -> dict:
    """和晨报同一套点名规则。整段就是代码时仍按市场后缀判定。"""
    pool = pool if pool is not None else _load_pool()
    text = (query or "").strip()
    if _CODE.fullmatch(text):
        return lookup(text, pool)
    found = pool.match(text)
    if found:
        return _from_match(pool, found)
    return lookup(text, pool)
