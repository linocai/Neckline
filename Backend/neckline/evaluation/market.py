"""行情原始行与按日期截取。

线上 TuShareSource 和回放 SnapshotSource 都只负责把四类原始行取回来。
晨报交易日 D 之前的截取、公司快照和价格背景是纯函数，两条路共用，避免回放看到未来数据。
财务金额 TuShare 已经是元；总市值是万元、成交额是千元，这里换成元。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol

import httpx

from neckline.kx.daily import ts_code
from neckline.market.tushare_daily import API_URL, DAILY_FIELDS, _averages, _bars, _iso, _parse, _window
from neckline.market.util import SHANGHAI, MarketError, num, silence_http_logs

KINDS = ("income", "fina_indicator", "daily", "daily_basic")
INCOME_FIELDS = "ts_code,ann_date,end_date,revenue,n_income_attr_p,update_flag"
FINA_FIELDS = "ts_code,ann_date,end_date,profit_dedt,update_flag"
BASIC_FIELDS = "ts_code,trade_date,total_mv,pe_ttm"
_WAN = 10_000.0
_QIAN = 1_000.0


def _errors(updates: dict | None = None) -> dict[str, str | None]:
    base: dict[str, str | None] = {kind: None for kind in KINDS}
    if updates:
        base.update(updates)
    return base


def _ymd(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return text
    if len(text) == 10 and text[4] == "-" and text[7] == "-" and text.replace("-", "").isdigit():
        return text.replace("-", "")
    return None


def _iso_day(compact: str | None) -> str | None:
    if compact is None or len(compact) != 8:
        return None
    return _iso(compact)


def as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(SHANGHAI)
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise TypeError("trade_date 须为日期")


def split_code(code: str) -> tuple[str, str]:
    """返回（六位代码，带交易所后缀的代码）。"""
    text = (code or "").strip().upper()
    if text.endswith(".SZ") or text.endswith(".SH"):
        short = text.split(".", 1)[0]
        return short, text
    short = text
    return short, ts_code(short) if len(short) == 6 and short.isdigit() else text


def _flag(row: dict) -> int:
    flag = row.get("update_flag")
    if flag is None or isinstance(flag, bool):
        return 0
    return 1 if str(flag).strip() in {"1", "1.0"} else 0


def _round(value: object, digits: int) -> float | None:
    number = num(value)
    if number is None:
        return None
    return float(round(number, digits))


@dataclass
class RawMarket:
    """一只股票四类原始行。errors 的键固定为四类接口名，没有错误时是 None。"""

    code: str
    fetched_at: str | None
    income: list[dict] = field(default_factory=list)
    fina_indicator: list[dict] = field(default_factory=list)
    daily: list[dict] = field(default_factory=list)
    daily_basic: list[dict] = field(default_factory=list)
    errors: dict[str, str | None] = field(default_factory=_errors)

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "fetchedAt": self.fetched_at,
            "income": self.income,
            "fina_indicator": self.fina_indicator,
            "daily": self.daily,
            "daily_basic": self.daily_basic,
            "errors": _errors(self.errors),
        }


def raw_from_dict(payload: dict, code: str) -> RawMarket:
    errors = payload.get("errors") if isinstance(payload.get("errors"), dict) else {}
    def rows(name: str) -> list[dict]:
        value = payload.get(name)
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    fetched = payload.get("fetchedAt", payload.get("fetched_at"))
    return RawMarket(
        code=str(payload.get("code") or code),
        fetched_at=fetched if isinstance(fetched, str) else None,
        income=rows("income"),
        fina_indicator=rows("fina_indicator"),
        daily=rows("daily"),
        daily_basic=rows("daily_basic"),
        errors=_errors({kind: errors.get(kind) if isinstance(errors.get(kind), str) else None for kind in KINDS}),
    )


class MarketSource(Protocol):
    def raw(self, code: str) -> RawMarket: ...


def _public_error(message: str, token: str) -> str:
    text = message or "api_error"
    if token and token in text:
        text = text.replace(token, "[redacted]")
    return text


class TuShareSource:
    """逐只股票同步取四类原始行。单类失败只记在该类 errors 里，不抛出。

    token 只放进请求体，不进日志、异常文本和 RawMarket。transport 可注入，测试不得直连。
    """

    def __init__(self, token: str | None, *, transport: httpx.BaseTransport | None = None,
                 timeout: float = 20, now: Callable[[], datetime] | None = None) -> None:
        self.token = (token or "").strip()
        self.now = now or (lambda: datetime.now(SHANGHAI))
        silence_http_logs()
        self._client = httpx.Client(transport=transport, timeout=timeout, trust_env=False, follow_redirects=False)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TuShareSource":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _stamp(self) -> str:
        current = self.now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=SHANGHAI)
        return current.astimezone(SHANGHAI).isoformat(timespec="seconds")

    def _call(self, api_name: str, fields: str, params: dict) -> tuple[list[dict], str | None]:
        payload = {"api_name": api_name, "token": self.token, "params": params, "fields": fields}
        try:
            response = self._client.post(API_URL, json=payload)
        except Exception:
            return [], "transport_error"
        if response.status_code != 200:
            return [], "http_error"
        try:
            body = response.json()
        except ValueError:
            return [], "bad_payload"
        try:
            rows, more = _parse(body, fields)
        except MarketError as exc:
            return [], _public_error(exc.message, self.token)
        if more:
            return rows, "truncated"
        return rows, None

    def raw(self, code: str) -> RawMarket:
        short, full = split_code(code)
        fetched_at = self._stamp()
        if not self.token:
            return RawMarket(code=short, fetched_at=fetched_at, errors=_errors({kind: "credential_missing" for kind in KINDS}))
        day = as_date(self.now())
        start_fin = _years_ago(day, 2).strftime("%Y%m%d")
        # 70 个自然日：国庆、春节长假时 45 天只剩二十来根 K 线，20 日涨跌和 20 日均线会不够。
        start_bar = (day - timedelta(days=70)).strftime("%Y%m%d")
        end_bar = day.strftime("%Y%m%d")
        specs = (
            ("income", INCOME_FIELDS, {"ts_code": full, "start_date": start_fin}),
            ("fina_indicator", FINA_FIELDS, {"ts_code": full, "start_date": start_fin}),
            ("daily", DAILY_FIELDS, {"ts_code": full, "start_date": start_bar, "end_date": end_bar}),
            ("daily_basic", BASIC_FIELDS, {"ts_code": full, "start_date": start_bar, "end_date": end_bar}),
        )
        loaded: dict[str, list[dict]] = {}
        errors: dict[str, str | None] = {}
        for name, fields, params in specs:
            rows, error = self._call(name, fields, params)
            loaded[name] = rows
            errors[name] = error
        return RawMarket(
            code=short, fetched_at=fetched_at, income=loaded["income"], fina_indicator=loaded["fina_indicator"],
            daily=loaded["daily"], daily_basic=loaded["daily_basic"], errors=_errors(errors))


def _years_ago(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


class SnapshotSource:
    """回放读 snapshot.py 写出的 JSON。文件里没有的代码，四类都记 not_in_snapshot。"""

    def __init__(self, path: Path | str) -> None:
        payload = json_loads(Path(path))
        if not isinstance(payload, dict):
            raise ValueError("行情快照不是对象")
        self._data = payload

    def raw(self, code: str) -> RawMarket:
        short, full = split_code(code)
        row = self._data.get(short)
        if row is None:
            row = self._data.get(full)
        if not isinstance(row, dict):
            return RawMarket(code=short, fetched_at=None,
                             errors=_errors({kind: "not_in_snapshot" for kind in KINDS}))
        parsed = raw_from_dict(row, short)
        parsed.code = short
        return parsed


def json_loads(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def clip_announcements(rows: list[dict], trade_date: date) -> list[dict]:
    """财务只用 ann_date 早于晨报交易日 D 的行。D 当天披露的期次留在原始行里，不进快照计算。"""
    cutoff = trade_date.strftime("%Y%m%d")
    kept = []
    for row in rows:
        ann = _ymd(row.get("ann_date"))
        if ann is not None and ann < cutoff:
            kept.append(row)
    return kept


def clip_trades(rows: list[dict], trade_date: date) -> list[dict]:
    """行情只用 trade_date 早于 D 的 K 线。"""
    cutoff = trade_date.strftime("%Y%m%d")
    kept = []
    for row in rows:
        trade = _ymd(row.get("trade_date"))
        if trade is not None and trade < cutoff:
            kept.append(row)
    return kept


def select_period(rows: list[dict]) -> dict | None:
    """在已截取的行里取最近一期。同一期多条：ann_date 最新，同一天 update_flag 为 1 优先。"""
    best_key: tuple[str, str, int, int] | None = None
    best: dict | None = None
    for index, row in enumerate(rows):
        end = _ymd(row.get("end_date"))
        ann = _ymd(row.get("ann_date"))
        if end is None or ann is None:
            continue
        key = (end, ann, _flag(row), index)
        if best_key is None or key > best_key:
            best_key = key
            best = row
    return best


def select_end(rows: list[dict], end: str) -> dict | None:
    """同一报告期里按 ann_date、update_flag 取一条。end 是 YYYYMMDD。"""
    return select_period([row for row in rows if _ymd(row.get("end_date")) == end])


def clip_raw(raw: RawMarket, trade_date: date | datetime | str) -> RawMarket:
    day = as_date(trade_date)
    return RawMarket(
        code=raw.code, fetched_at=raw.fetched_at,
        income=clip_announcements(raw.income, day),
        fina_indicator=clip_announcements(raw.fina_indicator, day),
        daily=clip_trades(raw.daily, day),
        daily_basic=clip_trades(raw.daily_basic, day),
        errors=_errors(raw.errors),
    )


def _missing(names: list[str]) -> list[str]:
    seen: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen


def company_snapshot(raw: RawMarket, trade_date: date | datetime | str) -> dict:
    """最近一期营收、归母、扣非，以及 D 之前最后一个交易日的总市值和市盈率 TTM。缺的写 None。"""
    day = as_date(trade_date)
    income_rows = clip_announcements(raw.income, day)
    fina_rows = clip_announcements(raw.fina_indicator, day)
    income = select_period(income_rows)
    end = _ymd(income.get("end_date")) if income else None
    fina = select_end(fina_rows, end) if end else select_period(fina_rows)
    if end is None and fina is not None:
        end = _ymd(fina.get("end_date"))
    ann = _ymd(income.get("ann_date")) if income else None
    if ann is None and fina is not None:
        ann = _ymd(fina.get("ann_date"))
    revenue = _round(income.get("revenue"), 2) if income else None
    net_profit = _round(income.get("n_income_attr_p"), 2) if income else None
    deducted = _round(fina.get("profit_dedt"), 2) if fina else None
    bars = _bars(clip_trades(raw.daily, day))
    market_date = bars[-1]["tradeDate"] if bars else None
    total_mv = None
    pe_ttm = None
    basic_rows = clip_trades(raw.daily_basic, day)
    matched = [row for row in basic_rows if market_date and _ymd(row.get("trade_date")) == market_date]
    if matched:
        basic = matched[-1]
        total = num(basic.get("total_mv"))
        total_mv = None if total is None else float(round(total * _WAN, 2))
        pe_ttm = _round(basic.get("pe_ttm"), 4)
    missing = []
    if end is None:
        missing.append("报告期")
    if ann is None:
        missing.append("公告日")
    if revenue is None:
        missing.append("营收")
    if net_profit is None:
        missing.append("归母净利")
    if deducted is None:
        missing.append("扣非净利")
    if market_date is None:
        missing.append("行情日")
    if total_mv is None:
        missing.append("总市值")
    if pe_ttm is None:
        missing.append("市盈率")
    if market_date and basic_rows and not matched:
        missing.append("行情日没有市值")
    for kind in KINDS:
        err = raw.errors.get(kind)
        if err:
            missing.append(f"{kind}：{err}")
    return {
        "reportPeriod": _iso_day(end),
        "annDate": _iso_day(ann),
        "revenue": revenue,
        "netProfit": net_profit,
        "deducted": deducted,
        "totalMv": total_mv,
        "peTtm": pe_ttm,
        "marketDate": _iso_day(market_date),
        "missing": _missing(missing),
    }


def price_background(raw: RawMarket, trade_date: date | datetime | str) -> dict:
    """截至 D 之前最后一个交易日：20 日涨跌、相对 5/10/20 日均线的位置、20 日日均成交额（元）。"""
    day = as_date(trade_date)
    bars = _bars(clip_trades(raw.daily, day))
    window = _window(bars)
    average_yuan = None
    if window["missingDays"] == 0:
        tail = bars[-20:]
        average_yuan = float(round(sum(bar["amount"] for bar in tail) / 20 * _QIAN, 2))
    if bars:
        averages, ma_note = _averages(bars)
        last = bars[-1]
        trade = _iso_day(last["tradeDate"])
        change = _round(last.get("pct"), 2)
    else:
        averages, ma_note = {}, None
        trade, change = None, None
    def position(name: str) -> str | None:
        item = averages.get(name)
        if not item:
            return None
        return item.get("position")

    return {
        "tradeDate": trade,
        "changePercent": change,
        "previous20": {
            "changePercent": window["changePercent"],
            "averageAmountYuan": average_yuan,
            "days": window["days"],
            "missingDays": window["missingDays"],
            "note": window["note"],
        },
        "ma5": position("ma5"),
        "ma10": position("ma10"),
        "ma20": position("ma20"),
        "maNote": ma_note,
    }
