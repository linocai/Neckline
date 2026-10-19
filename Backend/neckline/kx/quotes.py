"""麦蕊 hsrl/ssjy_more 批量行情：每组最多 20 只，每组成功缓存 60 秒、失败组 10 秒后再试，部分失败只标对应行。

缓存在 Mairui.request 里（同一组、同一参数的请求合并，成功 60 秒、失败 10 秒）；这里只管分组、取数与字段投影，
只留看板要用的字段，不保存整块响应。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime

from neckline.market.mairui import Mairui
from neckline.market.util import MarketError, SHANGHAI, num

BATCH_SIZE = 20
PATH = "hsrl/ssjy_more"
SUCCESS_TTL = 60
FAILURE_TTL = 10


@dataclass(frozen=True)
class Quote:
    price: float | None
    previous_close: float | None
    open: float | None
    high: float | None
    low: float | None
    change_percent: float | None
    change_amount: float | None
    amplitude_percent: float | None
    turnover_rate: float | None
    amount_yuan: float | None
    volume_lots: float | None
    at: datetime | None

    @property
    def day(self) -> date | None:
        return self.at.date() if self.at else None


@dataclass(frozen=True)
class QuoteBook:
    quotes: dict[str, Quote]       # 取到的行情，按六位代码
    failed: dict[str, str]         # 整组没取到的代码 -> 原因（中文）
    duplicated: frozenset[str]     # 响应里出现多次、不敢采用的代码


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace(" ", "T", 1))
    except ValueError:
        return None
    return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed.astimezone(SHANGHAI)


def _positive(value: object) -> float | None:
    result = num(value)
    return result if result is not None and result > 0 else None


def _non_negative(value: object) -> float | None:
    result = num(value)
    return result if result is not None and result >= 0 else None


def project(row: dict) -> Quote:
    """只取看板用的字段。价格必须为正，金额、成交量、换手率不能为负，其余原样（涨跌幅可以为负）。"""
    return Quote(
        price=_positive(row.get("p")), previous_close=_positive(row.get("yc")), open=_positive(row.get("o")),
        high=_positive(row.get("h")), low=_positive(row.get("l")), change_percent=num(row.get("pc")),
        change_amount=num(row.get("ud")), amplitude_percent=_non_negative(row.get("zf")),
        turnover_rate=_non_negative(row.get("tr")), amount_yuan=_non_negative(row.get("cje")),
        volume_lots=_non_negative(row.get("v")), at=_moment(row.get("t")))


def _code(value: object) -> str | None:
    text = str(value or "").strip()
    return text[:6] if len(text) >= 6 and text[:6].isdigit() else None


async def _group(mairui: Mairui, codes: list[str]) -> tuple[dict[str, Quote], set[str]]:
    data, _fetched, _cached = await mairui.request(
        PATH, {"stock_codes": ",".join(codes)}, ttl=SUCCESS_TTL, failure_ttl=FAILURE_TTL)
    if not isinstance(data, list):
        raise MarketError("invalid_batch", "批量行情没有返回数据序列。")
    wanted = set(codes)
    found: dict[str, Quote] = {}
    repeated: set[str] = set()
    for row in data:
        if not isinstance(row, dict):
            continue
        code = _code(row.get("dm"))
        if code not in wanted:
            continue
        if code in found or code in repeated:
            found.pop(code, None)
            repeated.add(code)
            continue
        found[code] = project(row)
    return found, repeated


async def fetch_quotes(mairui: Mairui, codes: list[str]) -> QuoteBook:
    groups = [codes[index:index + BATCH_SIZE] for index in range(0, len(codes), BATCH_SIZE)]
    results = await asyncio.gather(*(_group(mairui, group) for group in groups), return_exceptions=True)
    quotes: dict[str, Quote] = {}
    failed: dict[str, str] = {}
    duplicated: set[str] = set()
    for group, result in zip(groups, results):
        if isinstance(result, MarketError):
            failed.update({code: result.message for code in group})
        elif isinstance(result, BaseException):
            raise result
        else:
            quotes.update(result[0])
            duplicated |= result[1]
    return QuoteBook(quotes, failed, frozenset(duplicated))
