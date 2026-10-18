"""在 K10 池里查一只股票。没有返回整份名单的函数。"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from neckline.screening.pool import Company, CompanyPool

_POOL_FILE = Path(__file__).resolve().parents[1] / "config" / "company-pool-k10-v2-20260909.jsonl"
_CODE = re.compile(r"^(?:(SH|SZ|BJ))?(\d{6})(?:\.(SH|SZ|BJ))?$", re.IGNORECASE)
_NOT_IN_POOL = "不在 K10 股票池"


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


def _load_pool() -> CompanyPool:
    rows = []
    with _POOL_FILE.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return CompanyPool.from_rows(rows)


@lru_cache(maxsize=1)
def _default_index() -> PoolIndex:
    return PoolIndex(_load_pool())


def lookup(query: str, pool: CompanyPool | None = None) -> dict:
    index = PoolIndex(pool) if pool is not None else _default_index()
    return index.lookup(query)
