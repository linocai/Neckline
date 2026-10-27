"""把一条候选收成评估数据包。纯代码：不调模型，不剔除条目，不按消息类型改口径。

同样的输入得到同样的 dict（键顺序固定）。下载或读取失败写进 gaps，数据包照常返回。
联网搜索本次不做，search 只留位置。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Callable, Mapping

from neckline.evaluation.market import (
    KINDS, RawMarket, as_date, clip_announcements, company_snapshot, price_background, select_end, split_code,
)
from neckline.filings.fetch import FetchResult
from neckline.filings.financials import parse_express, parse_forecast, parse_periodic, summary_text
from neckline.filings.reader import headings, opening, read_pdf
from neckline.market.util import num
from neckline.morning import db
from neckline.morning.schedule import CST
from neckline.screening.classify import Event
from neckline.screening.enrich import FilingRecord, filing_title_kind
from neckline.screening.pool import CompanyPool
from neckline.screening.prefilter import Message

_PACKAGE_KEYS = (
    "pdfMaxPages", "originalChars", "openingChars", "headingsMax", "mediaChars", "maxMedia", "pdfMaxBytes",
)
_FIELDS = ("revenue", "netProfit", "deducted")
_ENDS = {"q1": "0331", "h1": "0630", "q3": "0930", "annual": "1231"}
_PARSERS = {"periodic": parse_periodic, "forecast": parse_forecast, "express": parse_express}
_YEAR = re.compile(r"20\d{2}")
_FORECAST_SPAN = re.compile(
    r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*[至到]\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)
_EXPRESS = re.compile(r"(20\d{2})年(前三季度|半年度|第一季度|第三季度|一季度|三季度|年度)")
_EXPRESS_KEY = {
    "第一季度": "q1", "一季度": "q1", "半年度": "h1",
    "前三季度": "q3", "第三季度": "q3", "三季度": "q3", "年度": "annual",
}
# 预告是区间，其余是点值。减的是上一档累计，不是估算。
_POINT_NOTE = {"h1": "累计减去一季报累计", "q3": "累计减去半年报累计", "annual": "累计减去三季报累计"}
_RANGE_NOTE = {"h1": "累计区间减去一季报累计", "q3": "累计区间减去半年报累计", "annual": "累计区间减去三季报累计"}
_SUBTRACT_END = {"h1": "q1", "q3": "h1", "annual": "q3"}


def _round2(value: object) -> float | None:
    number = num(value)
    if number is None:
        return None
    return float(round(number, 2))


def _metric(value: object = None, *, low: object = None, high: object = None,
            source: str | None = None, missing: str | None = None, note: str | None = None) -> dict:
    point = low is None and high is None
    return {
        "value": _round2(value) if point else None,
        "low": _round2(low),
        "high": _round2(high),
        "source": source,
        "missing": missing,
        "note": note,
    }


def _settings(settings: Mapping) -> dict[str, int]:
    if not isinstance(settings, Mapping):
        raise ValueError("evaluation.package 类型不对")
    checked: dict[str, int] = {}
    for key in _PACKAGE_KEYS:
        if key not in settings:
            raise ValueError(f"配置缺少 evaluation.package.{key}")
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"evaluation.package.{key} 须为正整数")
        checked[key] = value
    return checked


def _check_window(window: object) -> None:
    start, end = getattr(window, "start", None), getattr(window, "end", None)
    if start is not None and end is not None and end < start:
        raise ValueError("window 的终点早于起点")


def _published(ts: datetime) -> str:
    if ts.tzinfo is None:
        raise ValueError("消息时间必须带时区")
    return ts.astimezone(CST).isoformat(timespec="seconds")


def _message_key(message: Message) -> str:
    return f"{message.document_id}@{message.revision}"


def _ids(event: object) -> list:
    if isinstance(event, Event):
        return list(event.ids)
    if isinstance(event, Mapping):
        return list(event.get("ids") or [])
    return list(getattr(event, "ids", None) or [])


def _match(event: object, message: Message, by_id: Mapping[str, Message] | None) -> bool:
    keys = {_message_key(message), message.ref, message.document_id}
    if any(item in keys for item in _ids(event)):
        return True
    if not by_id:
        return False
    for item in _ids(event):
        found = by_id.get(item)
        if found is not None and (found is message or _message_key(found) == _message_key(message)):
            return True
    return False


def _all_messages(candidate: object) -> list[Message]:
    found: dict[str, Message] = {}

    def add(message: object) -> None:
        if isinstance(message, Message):
            found.setdefault(_message_key(message), message)

    for message in list(getattr(candidate, "messages", None) or []):
        add(message)
    by_id = getattr(candidate, "by_id", None) or {}
    for message in by_id.values():
        add(message)
    return sorted(found.values(), key=lambda message: (message.published_at, _message_key(message)))


def _event_view(event: object, mids: list[str]) -> dict:
    if isinstance(event, Event):
        return {
            "type": event.type, "direction": event.direction, "headline": event.headline, "reason": event.reason,
            "firstDisclosed": event.first_disclosed, "amountYuan": event.amount_yuan, "channel": event.channel,
            "messages": mids,
        }
    if isinstance(event, Mapping):
        return {
            "type": event.get("type"), "direction": event.get("direction"), "headline": event.get("headline"),
            "reason": event.get("reason"), "firstDisclosed": event.get("firstDisclosed", event.get("first_disclosed")),
            "amountYuan": event.get("amountYuan", event.get("amount_yuan")), "channel": event.get("channel"),
            "messages": mids,
        }
    return {
        "type": getattr(event, "type", None), "direction": getattr(event, "direction", None),
        "headline": getattr(event, "headline", None), "reason": getattr(event, "reason", None),
        "firstDisclosed": getattr(event, "first_disclosed", None), "amountYuan": getattr(event, "amount_yuan", None),
        "channel": getattr(event, "channel", None), "messages": mids,
    }


def _unwrap_pool(pool: object) -> tuple[CompanyPool | None, Mapping]:
    if hasattr(pool, "pool") and hasattr(pool, "profiles"):
        return pool.pool, pool.profiles or {}
    if isinstance(pool, CompanyPool) or hasattr(pool, "companies"):
        return pool, getattr(pool, "profiles", None) or {}  # type: ignore[return-value]
    return None, {}


def _company(pool: object, code: str) -> tuple[dict, list[str]]:
    short, full = split_code(str(code))
    companies, profiles = _unwrap_pool(pool)
    company = None
    if companies is not None:
        company = companies.companies.get(full) or companies.companies.get(short)
        if company is None:
            for item in companies.companies.values():
                if item.short_code == short:
                    company = item
                    full = item.code
                    break
    summary = None
    if profiles:
        summary = profiles.get(full)
        if summary is None:
            summary = profiles.get(short)
    gaps = []
    if company is None:
        gaps.append("公司池里没有这只股票")
    elif summary is None:
        gaps.append("公司池里没有主营摘要")
    return {"code": short, "name": None if company is None else company.name, "summary": summary}, gaps


def _market_gaps(raw: RawMarket) -> list[str]:
    if all(raw.errors.get(kind) == "not_in_snapshot" for kind in KINDS):
        return ["行情快照没有这只股票"]
    return [f"行情 {kind}：{raw.errors[kind]}" for kind in KINDS if raw.errors.get(kind)]


def _fetch(fetcher: Callable[[str], FetchResult], url: str) -> FetchResult:
    try:
        result = fetcher(url)
    except Exception:
        return FetchResult(None, "transport_error", False)
    if result is None or not isinstance(result, FetchResult):
        return FetchResult(None, "transport_error", False)
    return result


def _read_pdf(message: Message, fetcher: Callable[[str], FetchResult], settings: Mapping[str, int]) -> dict:
    if not message.url:
        return {"text": None, "pages": None, "pageTruncated": False, "error": "url_missing"}
    fetched = _fetch(fetcher, message.url)
    if fetched.error or fetched.path is None:
        return {"text": None, "pages": None, "pageTruncated": False, "error": fetched.error or "transport_error"}
    document = read_pdf(fetched.path, max_pages=settings["pdfMaxPages"])
    text = "\n".join(document.pages)
    if document.error and not text.strip():
        return {"text": None, "pages": document.page_count, "pageTruncated": document.truncated, "error": document.error}
    return {"text": text, "pages": document.page_count, "pageTruncated": document.truncated, "error": None}


def _present(body: str | None, pages: int | None, page_truncated: bool, error: str | None,
             settings: Mapping[str, int], identity: dict) -> dict:
    if error and not (body or "").strip():
        return {**identity, "truncated": False, "chars": None, "pages": pages, "text": None,
                "headings": [], "opening": "", "error": error}
    text = body or ""
    chars = len(text)
    if chars > settings["originalChars"]:
        return {
            **identity, "truncated": True, "chars": chars, "pages": pages, "text": None,
            "headings": headings(text)[:settings["headingsMax"]],
            "opening": opening(text, settings["openingChars"]), "error": None,
        }
    return {
        **identity, "truncated": bool(page_truncated), "chars": chars, "pages": pages, "text": text,
        "headings": [], "opening": "", "error": None,
    }


def _g(obj: object, key: str, default: object = None) -> object:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _first_year(text: str | None) -> int | None:
    found = _YEAR.search(text or "")
    return None if found is None else int(found.group(0))


def _quarter_key(month: int, day: int) -> str | None:
    return { (3, 31): "q1", (6, 30): "h1", (9, 30): "q3", (12, 31): "annual" }.get((month, day))


def _resolve_period(kind: str, figures: Mapping | None, title: str) -> tuple[object, str | None, int | None]:
    label = _g(figures, "period") if figures else None
    if kind == "periodic":
        key = label if isinstance(label, str) and label in _ENDS else None
        return label, key, _first_year(title)
    if kind == "forecast":
        text = str(label or title or "")
        matched = _FORECAST_SPAN.search(text)
        if matched is None:
            return label, None, _first_year(text)
        return label, _quarter_key(int(matched.group(5)), int(matched.group(6))), int(matched.group(4))
    text = re.sub(r"\s+", "", str(label or title or ""))
    matched = _EXPRESS.search(text)
    if matched is None:
        return label, None, _first_year(text)
    return label, _EXPRESS_KEY.get(matched.group(2)), int(matched.group(1))


def _end_date(year: int, key: str) -> str:
    return f"{year}{_ENDS[key]}"


def _cum(income: list[dict], fina: list[dict], year: int | None, key: str | None) -> dict | None:
    if year is None or key is None:
        return None
    end = _end_date(year, key)
    inc = select_end(income, end)
    fin = select_end(fina, end)
    if inc is None and fin is None:
        return None
    return {
        "revenue": _round2(inc.get("revenue")) if inc else None,
        "netProfit": _round2(inc.get("n_income_attr_p")) if inc else None,
        "deducted": _round2(fin.get("profit_dedt")) if fin else None,
        "incomeFound": inc is not None,
        "finaFound": fin is not None,
    }


def _blank_metrics(missing: str) -> dict[str, dict]:
    return {name: _metric(missing=missing) for name in _FIELDS}


def _point_metrics(values: Mapping[str, object], *, source: str, note: str | None,
                   missing: str) -> dict[str, dict]:
    result = {}
    for name in _FIELDS:
        value = values.get(name)
        if value is None:
            result[name] = _metric(missing=missing, note=note)
        else:
            result[name] = _metric(value, source=source, note=note)
    return result


def _subtract_point(later: dict | None, earlier: dict | None, note: str) -> dict[str, dict]:
    result = {}
    for name in _FIELDS:
        found = "finaFound" if name == "deducted" else "incomeFound"
        if later is None or earlier is None or not later[found] or not earlier[found]:
            result[name] = _metric(missing="缺上一期", note=note)
            continue
        left, right = later[name], earlier[name]
        if left is None or right is None:
            result[name] = _metric(missing="缺上一期", note=note)
        else:
            result[name] = _metric(left - right, source="计算", note=note)
    return result


def _yoy_metric(value: object, *, missing: str = "未能读出同比") -> dict:
    if isinstance(value, Mapping) and ("low" in value or "high" in value):
        if value.get("low") is None and value.get("high") is None:
            return _metric(missing=missing)
        return _metric(low=value.get("low"), high=value.get("high"), source="pdf")
    if value is None:
        return _metric(missing=missing)
    return _metric(value, source="pdf")


def _line_attr(figures: Mapping, line: str, attr: str) -> object:
    return _g(_g(figures, line), attr)


def _pdf_points(figures: Mapping, attr: str) -> dict[str, object]:
    return {
        "revenue": _line_attr(figures, "revenue", attr),
        "netProfit": _line_attr(figures, "net_profit", attr),
        "deducted": _line_attr(figures, "deducted", attr),
    }


def _range_of(figures: Mapping, line: str) -> tuple[object, object]:
    current = _g(_g(figures, line), "current")
    return _g(current, "low"), _g(current, "high")


def _prev_quarter(key: str | None, year: int | None, income: list[dict], fina: list[dict]) -> dict[str, dict]:
    if key is None or year is None:
        return _blank_metrics("报告年份不明")
    if key == "h1":
        current = _cum(income, fina, year, "q1")
        if current is None:
            return _blank_metrics("缺上一期")
        values = {
            "revenue": current["revenue"] if current["incomeFound"] else None,
            "netProfit": current["netProfit"] if current["incomeFound"] else None,
            "deducted": current["deducted"] if current["finaFound"] else None,
        }
        return _point_metrics(values, source="tushare", note="一季度无需相减", missing="缺上一期")
    if key == "q1":
        later = _cum(income, fina, year - 1, "annual")
        earlier = _cum(income, fina, year - 1, "q3")
        return _subtract_point(later, earlier, "年报累计减去上年三季报累计")
    later_key = "h1" if key == "q3" else "q3"
    earlier_key = "q1" if key == "q3" else "h1"
    note = "半年报累计减去一季报累计" if key == "q3" else "三季报累计减去半年报累计"
    return _subtract_point(_cum(income, fina, year, later_key), _cum(income, fina, year, earlier_key), note)


def _direct_points(figures: Mapping, note: str) -> dict[str, dict]:
    return _point_metrics(_pdf_points(figures, "current"), source="pdf", note=note, missing="未能读出")


def _minus_point(values: Mapping[str, object], base: dict | None, note: str) -> dict[str, dict]:
    result = {}
    for name in _FIELDS:
        value = values.get(name)
        if value is None:
            result[name] = _metric(missing="未能读出", note=note)
            continue
        found = "finaFound" if name == "deducted" else "incomeFound"
        if base is None or not base[found] or base[name] is None:
            result[name] = _metric(missing="缺上一期", note=note)
        else:
            result[name] = _metric(num(value) - base[name], source="计算", note=note)
    return result


def _minus_range(figures: Mapping, base: dict | None, note: str) -> dict[str, dict]:
    result = {}
    lines = {"revenue": "revenue", "netProfit": "net_profit", "deducted": "deducted"}
    for name, line in lines.items():
        if name == "revenue":
            result[name] = _metric(missing="预告没有营业收入", note=note)
            continue
        low, high = _range_of(figures, line)
        if low is None or high is None:
            result[name] = _metric(missing="未能读出", note=note)
            continue
        found = "finaFound" if name == "deducted" else "incomeFound"
        if base is None or not base[found] or base[name] is None:
            result[name] = _metric(missing="缺上一期", note=note)
        else:
            result[name] = _metric(low=num(low) - base[name], high=num(high) - base[name], source="计算", note=note)
    return result


def _forecast_quarter(figures: Mapping, note: str) -> dict[str, dict]:
    """一季度预告的区间本身就是单季，不再减累计。"""
    result = {"revenue": _metric(missing="预告没有营业收入", note=note)}
    for name, line in (("netProfit", "net_profit"), ("deducted", "deducted")):
        low, high = _range_of(figures, line)
        if low is None or high is None:
            result[name] = _metric(missing="未能读出", note=note)
        else:
            result[name] = _metric(low=low, high=high, source="pdf", note=note)
    return result


def _current_quarter(kind: str, key: str | None, figures: Mapping, income: list[dict], fina: list[dict],
                     year: int | None) -> dict[str, dict]:
    if key is None:
        return _blank_metrics("报告期不明")
    if kind == "periodic" and key in {"q1", "q3"}:
        return _direct_points(figures, "本报告期" if key == "q3" else "一季度无需相减")
    if kind == "forecast" and key == "q1":
        return _forecast_quarter(figures, "一季度无需相减")
    if kind == "express" and key == "q1":
        return _direct_points(figures, "一季度无需相减")
    if year is None:
        return _blank_metrics("报告年份不明")
    base = _cum(income, fina, year, _SUBTRACT_END.get(key))
    if kind == "forecast":
        subtracted = _minus_range(figures, base, _RANGE_NOTE.get(key, "累计区间相减"))
        return subtracted
    return _minus_point(_pdf_points(figures, "current"), base, _POINT_NOTE.get(key, "累计相减"))


def _empty_metric(item: Mapping) -> bool:
    return item.get("value") is None and item.get("low") is None and item.get("high") is None


def _qoq(current: Mapping, prior: Mapping) -> dict:
    note = "本季对上一季"
    if _empty_metric(prior):
        return _metric(missing=prior.get("missing") or "缺上一期", note=note)
    if _empty_metric(current):
        return _metric(missing=current.get("missing") or "未能读出", note=note)
    prior_value = prior.get("value")
    if prior_value == 0:
        return _metric(missing="上一季为零", note=note)
    if current.get("value") is not None and prior_value is not None:
        return _metric(current["value"] / prior_value * 100 - 100, source="计算", note=note)
    if current.get("low") is not None and current.get("high") is not None and prior_value is not None:
        return _metric(low=current["low"] / prior_value * 100 - 100, high=current["high"] / prior_value * 100 - 100,
                       source="计算", note=note)
    return _metric(missing="缺上一期", note=note)


def _yoy_block(kind: str, figures: Mapping, *, ytd: bool = False) -> dict[str, dict]:
    attr = "ytd_yoy" if ytd else "yoy"
    if kind == "forecast":
        return {
            "revenue": _metric(missing="预告没有营业收入"),
            "netProfit": _yoy_metric(_line_attr(figures, "net_profit", "yoy")),
            "deducted": _yoy_metric(_line_attr(figures, "deducted", "yoy")),
        }
    if kind == "express":
        return {name: _yoy_metric(_line_attr(figures, line, "yoy"))
                for name, line in (("revenue", "revenue"), ("netProfit", "net_profit"), ("deducted", "deducted"))}
    return {name: _yoy_metric(_line_attr(figures, line, attr))
            for name, line in (("revenue", "revenue"), ("netProfit", "net_profit"), ("deducted", "deducted"))}


def _gap_between(figures: Mapping, kind: str) -> dict:
    if kind == "forecast":
        return _metric(missing="区间不能合成单值差额", note="归母减扣非")
    net = _line_attr(figures, "net_profit", "current")
    deducted = _line_attr(figures, "deducted", "current")
    if net is None or deducted is None:
        return _metric(missing="未能读出归母或扣非", note="归母减扣非")
    return _metric(num(net) - num(deducted), source="计算", note="归母减扣非")


def _with_ratio(value: object, net: object, *, missing: str) -> dict:
    if value is None:
        item = _metric(missing=missing)
        item["ratio"] = None
        return item
    item = _metric(value, source="pdf")
    if net is None:
        item["ratio"] = None
        item["note"] = "缺归母净利"
    elif num(net) == 0:
        item["ratio"] = None
        item["note"] = "归母净利为零"
    else:
        item["ratio"] = _round2(num(value) / num(net) * 100)
    return item


def _non_recurring(kind: str, figures: Mapping) -> dict:
    if kind != "periodic":
        blank = _metric(missing="公告没有非经常性损益合计")
        blank["ratio"] = None
        return {"current": blank, "ytd": dict(blank)}
    current_net = _line_attr(figures, "net_profit", "current")
    ytd_net = _line_attr(figures, "net_profit", "ytd")
    return {
        "current": _with_ratio(_g(figures, "non_recurring_current"), current_net, missing="未能读出非经常性损益合计"),
        "ytd": _with_ratio(_g(figures, "non_recurring_ytd"), ytd_net, missing="未能读出非经常性损益合计"),
    }


def _base(kind: str, key: str | None, year: int | None, figures: Mapping, income: list[dict]) -> dict:
    prior = _line_attr(figures, "net_profit", "prior")
    # 三季报主要会计数据没有上年同期金额这一列，基数用截取后的 TuShare，不从同比倒推。
    if prior is not None and not (kind == "periodic" and key == "q3"):
        return {"netProfit": _metric(prior, source="pdf", note="上年同期")}
    if year is None or key is None:
        return {"netProfit": _metric(missing="报告年份不明", note="上年同期")}
    row = select_end(income, _end_date(year - 1, key))
    if row is None or num(row.get("n_income_attr_p")) is None:
        return {"netProfit": _metric(missing="缺上年同期", note="上年同期累计")}
    return {"netProfit": _metric(row.get("n_income_attr_p"), source="tushare", note="上年同期累计")}


def _earnings_from(kind: str, figures: Mapping | None, *, title: str, message: str, error: str | None,
                   income: list[dict], fina: list[dict]) -> dict:
    if figures is None:
        missing = error or "未能读出"
        blank = _blank_metrics(missing)
        failed = _metric(missing=missing)
        nonrec = _metric(missing=missing)
        nonrec["ratio"] = None
        return {
            "kind": kind, "period": None, "message": message,
            "yoy": blank, "netMinusDeducted": failed, "quarter": blank, "priorQuarter": blank,
            "quarterOnQuarter": blank, "nonRecurring": {"current": nonrec, "ytd": dict(nonrec)},
            "base": {"netProfit": failed},
        }
    label, key, year = _resolve_period(kind, figures, title)
    quarter = _current_quarter(kind, key, figures, income, fina, year)
    prior = _prev_quarter(key, year, income, fina)
    block = {
        "kind": kind,
        "period": label,
        "message": message,
        "yoy": _yoy_block(kind, figures),
        "netMinusDeducted": _gap_between(figures, kind),
        "quarter": quarter,
        "priorQuarter": prior,
        "quarterOnQuarter": {name: _qoq(quarter[name], prior[name]) for name in _FIELDS},
        "nonRecurring": _non_recurring(kind, figures),
        "base": _base(kind, key, year, figures, income),
    }
    if kind == "periodic" and key == "q3":
        block["ytdYoy"] = _yoy_block(kind, figures, ytd=True)
    return block


def _load_figures(message: Message, kind: str, *, store: object, pdf: dict, clock: Callable[[], datetime],
                  gaps: list[str]) -> tuple[dict | None, str | None]:
    cached = None
    if store is not None:
        try:
            cached = store.get(message.document_id, message.revision)
        except Exception:
            gaps.append(f"读数缓存读取失败（{_message_key(message)}）")
            cached = None
    if cached is not None:
        if cached.state == "ok" and cached.figures_json:
            try:
                payload = json.loads(cached.figures_json)
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                return payload, None
            gaps.append(f"读数缓存损坏（{_message_key(message)}）")
        return None, cached.error or "未能读出"
    if pdf.get("error") and not (pdf.get("text") or "").strip():
        reason = pdf.get("error") or "未能读出"
        _remember(store, message, kind, state="error", error=reason, figures=None, summary=None, clock=clock, gaps=gaps)
        return None, reason
    figures = _PARSERS[kind](pdf.get("text") or "")
    _remember(store, message, kind, state="ok", error=None, figures=figures, summary=summary_text(figures),
              clock=clock, gaps=gaps)
    return asdict(figures), None


def _remember(store: object, message: Message, kind: str, *, state: str, error: str | None,
              figures: object, summary: str | None, clock: Callable[[], datetime], gaps: list[str]) -> None:
    if store is None:
        return
    try:
        read_at = db.utc(clock())
    except Exception:
        gaps.append(f"读数没有写入缓存（{_message_key(message)}）")
        return
    text = summary if summary is not None else f"【公告 PDF 读数】未能读出（{error or '未知'}）"
    payload = None if figures is None else json.dumps(asdict(figures), ensure_ascii=False, sort_keys=True)
    try:
        store.put(FilingRecord(
            document_id=message.document_id, revision=message.revision, url=message.url or "", kind=kind,
            state=state, error=error, figures_json=payload, summary=text, read_at=read_at))
    except Exception:
        gaps.append(f"读数没有写入缓存（{_message_key(message)}）")


def _financial_message(ordered: list[Message], primary: list[Message]) -> tuple[Message, str] | None:
    def kind_of(message: Message) -> str | None:
        if message.source_key != "cninfo-announcement":
            return None
        return filing_title_kind(message.title)

    for group in (primary, ordered):
        found = [(message, kind) for message in group if (kind := kind_of(message))]
        if found:
            found.sort(key=lambda item: (item[0].published_at, _message_key(item[0])))
            return found[-1]
        if group is primary and primary:
            continue
    return None


def _sizes(package: dict) -> dict[str, int]:
    keys = ("company", "events", "messages", "original", "financials", "price", "earnings", "search", "limits", "gaps")

    def size(key: str) -> int:
        if key not in package:
            return 0
        return len(json.dumps(package[key], ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    sizes = {key: size(key) for key in keys}
    sizes["total"] = sum(sizes.values())
    return sizes


def build_package(candidate: object, *, trade_date: date | datetime | str, window: object, pool: object,
                  market: object, filings_store: object, fetcher: Callable[[str], FetchResult],
                  settings: Mapping, clock: Callable[[], datetime]) -> dict:
    """一条候选的评估数据包。market.raw 只取原始行，日期截取在快照和价格里做。"""
    checked = _settings(settings)
    _check_window(window)
    day = as_date(trade_date)
    code = getattr(candidate, "company", None) or getattr(candidate, "code", None) or ""
    company, gaps = _company(pool, str(code))
    short, _full = split_code(str(code))
    try:
        raw = market.raw(short)
    except Exception:
        raw = RawMarket(code=short, fetched_at=None, errors={kind: "transport_error" for kind in KINDS})
    if not isinstance(raw, RawMarket):
        raw = RawMarket(code=short, fetched_at=None, errors={kind: "transport_error" for kind in KINDS})
    gaps.extend(_market_gaps(raw))
    ordered = _all_messages(candidate)
    by_id = getattr(candidate, "by_id", None)
    numbers = {_message_key(message): f"m{index}" for index, message in enumerate(ordered, start=1)}
    primary_event = getattr(candidate, "primary", None)
    others = list(getattr(candidate, "others", None) or [])
    primary_messages = [message for message in ordered if primary_event is not None and _match(primary_event, message, by_id)]
    if primary_event is not None and _ids(primary_event) and not primary_messages:
        gaps.append("主事件没有对上消息")

    def mids(event: object) -> list[str]:
        return [numbers[_message_key(message)] for message in ordered if _match(event, message, by_id)]

    events = {
        "primary": None if primary_event is None else _event_view(primary_event, mids(primary_event)),
        "others": [_event_view(event, mids(event)) for event in others],
    }
    messages = [
        {
            "id": numbers[_message_key(message)], "ref": _message_key(message), "source": message.source_label,
            "publishedAt": _published(message.published_at), "title": message.title, "url": message.url,
            "cninfo": message.source_key == "cninfo-announcement",
        }
        for message in ordered
    ]
    pdfs: dict[str, dict] = {}

    def ensure_pdf(message: Message) -> dict:
        key = _message_key(message)
        if key not in pdfs:
            pdfs[key] = _read_pdf(message, fetcher, checked)
        return pdfs[key]

    announcements = [message for message in primary_messages if message.source_key == "cninfo-announcement"]
    news = [message for message in primary_messages if message.source_key != "cninfo-announcement"]
    documents = []
    if announcements:
        kind = "announcement"
        for index, message in enumerate(announcements, start=1):
            pdf = ensure_pdf(message)
            if pdf.get("error") and not (pdf.get("text") or "").strip():
                gaps.append(f"公告下载失败（{numbers[_message_key(message)]}）：{pdf.get('error')}"
                            if pdf.get("error") in {"timeout", "http_error", "transport_error", "url_not_allowed", "url_missing", "too_large"}
                            else f"公告读取失败（a{index}）：{pdf.get('error')}")
            identity = {
                "id": f"a{index}", "message": numbers[_message_key(message)], "source": message.source_label,
                "title": message.title, "url": message.url, "publishedAt": _published(message.published_at),
            }
            documents.append(_present(pdf.get("text"), pdf.get("pages"), bool(pdf.get("pageTruncated")), pdf.get("error"),
                                      checked, identity))
        media_messages = news
    else:
        kind = "news"
        if news:
            message = news[0]
            identity = {
                "id": numbers[_message_key(message)], "message": numbers[_message_key(message)],
                "source": message.source_label, "title": message.title, "url": message.url,
                "publishedAt": _published(message.published_at),
            }
            documents.append(_present(message.text or "", None, False, None, checked, identity))
        media_messages = news[1:]
    limit = checked["maxMedia"]
    if len(media_messages) > limit:
        gaps.append(f"媒体报道超过 {limit} 条，只附了前 {limit} 条")
    media = []
    for message in media_messages[:limit]:
        body = message.text or ""
        media.append({
            "message": numbers[_message_key(message)], "source": message.source_label, "title": message.title,
            "publishedAt": _published(message.published_at), "text": body[:checked["mediaChars"]],
            "chars": len(body), "truncated": len(body) > checked["mediaChars"],
        })
    original = {"kind": kind, "documents": documents, "media": media}
    financials = company_snapshot(raw, day)
    price = price_background(raw, day)
    earnings = None
    picked = _financial_message(ordered, primary_messages)
    if picked is not None:
        message, filing_kind = picked
        pdf = ensure_pdf(message)
        if pdf.get("error") and not (pdf.get("text") or "").strip() and message not in announcements:
            gaps.append(f"公告下载失败（{numbers[_message_key(message)]}）：{pdf.get('error')}")
        figures, error = _load_figures(message, filing_kind, store=filings_store, pdf=pdf, clock=clock, gaps=gaps)
        income = clip_announcements(raw.income, day)
        fina = clip_announcements(raw.fina_indicator, day)
        earnings = _earnings_from(filing_kind, figures, title=message.title, message=numbers[_message_key(message)],
                                  error=error, income=income, fina=fina)
    package: dict[str, Any] = {
        "company": company,
        "events": events,
        "messages": messages,
        "original": original,
        "financials": financials,
        "price": price,
    }
    if earnings is not None:
        package["earnings"] = earnings
    package["search"] = {"enabled": False, "results": []}
    package["limits"] = {key: checked[key] for key in _PACKAGE_KEYS}
    package["gaps"] = gaps
    package["sizes"] = _sizes(package)
    return package
