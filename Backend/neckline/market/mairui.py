"""麦蕊报价和当前交易时段的分时。证书只放在请求路径里，错误信息不带 URL。

报价和分时共用 60 秒成功缓存、15 秒失败缓存（可按调用调整，KX 看板的批量行情失败缓存 10 秒）。
一分钟 Pro 返回 108 时改取五分钟。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time as clock_time
import json
import re
import time

import httpx

from neckline.market.cache import RequestCache
from neckline.market.util import MarketError, SHANGHAI, num, silence_http_logs

_CODE = re.compile(r"(30[01]\d{3})\.SZ")
_VENDOR = re.compile(r"\s*(10\d)\s*[:：]")


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace(" ", "T", 1))
    except ValueError:
        return None
    return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed.astimezone(SHANGHAI)


def _vendor(code: int) -> None:
    messages = {
        101: ("quota_exhausted", "麦蕊当日调用额度已用完。"),
        102: ("invalid_licence", "麦蕊证书无效，请检查服务器配置。"),
        103: ("source_ip_limit", "麦蕊限制了当前请求来源或频率，请稍后再试。"),
        108: ("pro_required", "尚未开通或已过期的 Quant Pro 权限，无法获取一分钟数据。"),
    }
    name, message = messages.get(code, ("vendor_error", f"麦蕊返回错误代码 {code}。"))
    raise MarketError(name, message)


def _in_session(moment: datetime) -> bool:
    clock = moment.time()
    return clock_time(9, 30) <= clock <= clock_time(11, 30) or clock_time(13) <= clock <= clock_time(15)


def _bucket_start(moment: datetime) -> clock_time | None:
    """一分钟线按结束时刻计：09:31–09:35 归到 09:30 这根五分钟。"""
    minutes = moment.hour * 60 + moment.minute

    def start_of(session_start: int) -> int:
        shifted = minutes if minutes == session_start else minutes - 1
        if shifted < session_start:
            shifted = session_start
        return session_start + ((shifted - session_start) // 5) * 5

    if 9 * 60 + 30 <= minutes <= 11 * 60 + 30:
        start = start_of(9 * 60 + 30)
    elif 13 * 60 <= minutes <= 15 * 60:
        start = start_of(13 * 60)
    else:
        return None
    return clock_time(start // 60, start % 60)


def _bucket_end(moment: datetime) -> clock_time | None:
    """和麦蕊五分钟线一样按结束时刻标：09:30–09:35 这根标 09:35。"""
    start = _bucket_start(moment)
    if start is None:
        return None
    minutes = start.hour * 60 + start.minute + 5
    return clock_time(minutes // 60, minutes % 60)


def _clean(raw, session: date) -> tuple[list[dict], int]:
    if not isinstance(raw, list):
        raise MarketError("invalid_bars", "分时接口未返回数据序列。")
    bars, rejected, seen = [], 0, set()
    for row in raw:
        if not isinstance(row, dict):
            rejected += 1
            continue
        moment = _timestamp(row.get("t"))
        values = {key: num(row.get(key)) for key in ("o", "h", "l", "c", "a")}
        prices_ok = all(values[key] is not None and values[key] > 0 for key in ("o", "h", "l", "c"))
        ordered = prices_ok and values["h"] >= max(values["o"], values["c"], values["l"]) and values["l"] <= min(values["o"], values["c"], values["h"])
        amount_ok = values["a"] is None or values["a"] >= 0
        if not moment or moment.date() != session or not _in_session(moment) or not ordered or not amount_ok or moment in seen:
            rejected += 1
            continue
        seen.add(moment)
        bars.append({"time": moment, "open": values["o"], "high": values["h"], "low": values["l"],
                     "close": values["c"], "amount": values["a"]})
    bars.sort(key=lambda item: item["time"])
    return bars, rejected


def _compact(moment_label: clock_time, open_, high, low, close, amount_yuan: float | None) -> list:
    amount = None if amount_yuan is None else round(amount_yuan / 10_000, 2)
    return [moment_label.strftime("%H:%M"), round(open_, 4), round(high, 4), round(low, 4), round(close, 4), amount]


def _aggregate(bars: list[dict]) -> list[list]:
    groups: dict[clock_time, list[dict]] = {}
    order: list[clock_time] = []
    for bar in bars:
        label = _bucket_end(bar["time"])
        if label is None:
            continue
        if label not in groups:
            order.append(label)
            groups[label] = []
        groups[label].append(bar)
    rows = []
    for label in order:
        chunk = groups[label]
        amount = None if any(item["amount"] is None for item in chunk) else sum(item["amount"] for item in chunk)
        rows.append(_compact(label, chunk[0]["open"], max(item["high"] for item in chunk),
                             min(item["low"] for item in chunk), chunk[-1]["close"], amount))
    return rows


def _native(bars: list[dict]) -> list[list]:
    return [_compact(bar["time"].time().replace(second=0, microsecond=0), bar["open"], bar["high"], bar["low"],
                     bar["close"], bar["amount"]) for bar in bars]


class Mairui:
    def __init__(self, licence: str | None, client: httpx.AsyncClient | None = None, now=None) -> None:
        self.licence = (licence or "").strip()
        self.client = client
        self._owns = client is None
        self.now = now or (lambda: datetime.now(SHANGHAI))
        self.cache = RequestCache(256)
        self._connections: asyncio.Semaphore | None = None
        self.pro_unavailable_until = 0.0
        silence_http_logs()

    async def aclose(self) -> None:
        await self.cache.close()
        if self._owns and self.client is not None:
            await self.client.aclose()

    def _limit(self) -> asyncio.Semaphore:
        if self._connections is None:
            self._connections = asyncio.Semaphore(6)
        return self._connections

    async def _http(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False)
            self._owns = True
        return self.client

    def _symbol(self, symbol: str) -> str:
        matched = _CODE.fullmatch((symbol or "").strip().upper())
        if not matched:
            raise MarketError("unsupported_symbol", "只接受创业板代码，例如 300002.SZ。")
        return matched.group(1)

    async def request(self, path: str, params: dict | None = None, *, pro: bool = False, ttl: int = 60,
                      failure_ttl: int = 15):
        cache_key = (pro, path, tuple(sorted((params or {}).items())))

        async def fetch():
            try:
                async with asyncio.timeout(20), self._limit():
                    return await self._request(path, params, pro)
            except TimeoutError:
                raise MarketError("upstream_timeout", "麦蕊取数超过20秒，本次未取得新数据。") from None

        result, cached = await self.cache.get(cache_key, fetch, ttl, failure_ttl=failure_ttl)
        data, fetched = result
        return data, fetched, cached

    async def _request(self, path: str, params: dict | None, pro: bool):
        if not self.licence:
            raise MarketError("credential_missing", "未配置麦蕊，没有盘中数据。")
        origin = "https://pro.mairuiapi.com/" if pro else "https://api.mairuiapi.com/"
        url = origin + path + "/" + self.licence
        client = await self._http()
        try:
            async with client.stream("GET", url, params=params) as response:
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 2_000_000:
                        raise MarketError("oversized_response", "上游返回过大，已停止读取。")
                    chunks.append(chunk)
                body = b"".join(chunks).decode("utf-8", errors="replace")
                status = response.status_code
        except MarketError:
            raise
        except httpx.HTTPError:
            raise MarketError("upstream_unavailable", "麦蕊接口暂时无法连接，本次未取得新行情。") from None
        vendor = _VENDOR.match(body)
        if vendor:
            _vendor(int(vendor.group(1)))
        if status >= 300:
            raise MarketError("upstream_http_error", f"麦蕊接口返回 HTTP {status}，本次未取得行情。")
        try:
            data = json.loads(body)
        except ValueError:
            raise MarketError("invalid_response", "麦蕊未返回可解析的数据。") from None
        if isinstance(data, dict) and (data.get("code") is not None or data.get("error") is not None):
            value = data.get("code", data.get("error"))
            try:
                _vendor(int(value))
            except (TypeError, ValueError):
                pass
            raise MarketError("vendor_error", "麦蕊返回错误，未将错误内容当作行情。")
        return data, self.now()

    async def _quote(self, code: str) -> dict:
        data, _fetched, _cached = await self.request("hsstock/real/time/" + code)
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        if not isinstance(data, dict):
            raise MarketError("missing_quote", "上游未返回有效的当前价格。")
        price = num(data.get("p"))
        if price is None or price <= 0:
            raise MarketError("missing_quote", "上游未返回有效的当前价格。")
        previous = num(data.get("yc"))
        change = num(data.get("pc"))
        if change is None and previous:
            change = (price / previous - 1) * 100
        amount = num(data.get("cje"))
        turnover = num(data.get("tr"))
        moment = _timestamp(data.get("t"))
        now = self.now()
        same_day = bool(moment and moment.date() == now.date())
        note = None if same_day else "不是实时行情"
        warning = None
        if previous and change is not None:
            calculated = (price / previous - 1) * 100
            if abs(calculated - change) > 0.05:
                warning = "最新价、昨收和涨跌幅对不上。"
        return {
            "latestPrice": round(price, 4),
            "previousClose": None if previous is None else round(previous, 4),
            "changePercent": None if change is None else round(change, 2),
            "turnoverYi": None if amount is None else round(amount / 1e8, 4),
            "turnoverRate": None if turnover is None else round(turnover, 2),
            "asOf": moment.isoformat(timespec="seconds") if moment else None,
            "note": note,
            "warning": warning,
            "source": "麦蕊",
        }

    async def _bars(self, symbol: str, period: str, params: dict):
        pro = period == "1"
        path = f"hsstock/{'pro/' if pro else ''}history/{symbol}/{period}/n"
        return await self.request(path, params, pro=pro)

    async def _series(self, code: str, session: date) -> dict:
        symbol = code + ".SZ"
        params = {"st": session.strftime("%Y%m%d") + "093000", "et": session.strftime("%Y%m%d") + "150000", "lt": "300"}
        selected = "5" if time.monotonic() < self.pro_unavailable_until else "1"
        try:
            raw, _fetched, _cached = await self._bars(symbol, selected, params)
        except MarketError as error:
            if error.code != "pro_required" or selected != "1":
                raise
            self.pro_unavailable_until = time.monotonic() + 300
            selected = "5"
            raw, _fetched, _cached = await self._bars(symbol, selected, params)
        cleaned, rejected = _clean(raw, session)
        if selected == "1":
            rows, note, source = _aggregate(cleaned), "由一分钟数据聚合", "麦蕊一分钟 Pro"
        else:
            rows, note, source = _native(cleaned), "麦蕊五分钟分时", "麦蕊五分钟分时"
        warning = None if cleaned else "这一天没有有效分时，不能据此判断一定停牌。"
        if rejected:
            warning = (warning + " " if warning else "") + f"已剔除 {rejected} 条重复、跨日、非交易时段或格式异常记录。"
        last = cleaned[-1]["time"].isoformat(timespec="seconds") if cleaned else None
        return {"intervalMinutes": 5, "note": note, "columns": ["结束时间", "开", "高", "低", "收", "成交额万元"],
                "rows": rows, "rejectedRows": rejected, "asOf": last, "warning": warning, "source": source}

    async def block(self, symbol: str, session: date) -> dict:
        code = self._symbol(symbol)
        if not self.licence:
            raise MarketError("credential_missing", "未配置麦蕊，没有盘中数据。")
        quote, series = await asyncio.gather(self._quote(code), self._series(code, session), return_exceptions=True)
        errors, quote_out, series_out = [], None, None
        for label, value, slot in (("报价", quote, "quote"), ("分时", series, "series")):
            if isinstance(value, MarketError):
                errors.append(f"{label}：{value.message}")
            elif isinstance(value, Exception):
                raise value
            elif slot == "quote":
                quote_out = value
            else:
                series_out = value
        return {"sessionDate": session.isoformat(), "quote": quote_out, "series": series_out, "errors": errors}
