"""TuShare 收盘后数据。每只股票最多三次调用：daily、daily_basic、moneyflow。

单位：daily.amount 是千元，daily_basic 的市值和 moneyflow.net_mf_amount 是万元，这里换成亿元。
成功结果按「交易日盘中 / 15:00 后」各留一份；失败只短缓存，不立刻重试。
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta

import httpx

from neckline.market.cache import RequestCache
from neckline.market.calendar import is_trading_day
from neckline.market.util import MarketError, num, silence_http_logs

API_URL = "https://api.tushare.pro"
DAILY_FIELDS = "ts_code,trade_date,close,pre_close,pct_chg,high,low,amount"
BASIC_FIELDS = "ts_code,trade_date,turnover_rate,pe_ttm,pb,total_mv,circ_mv"
FLOW_FIELDS = "ts_code,trade_date,net_mf_amount"
BASIS = "TuShare moneyflow 净流入额"
_DATE = re.compile(r"\d{8}")
# 1 亿元 = 100_000 千元 = 10_000 万元
_QIAN_PER_YI = 100_000
_WAN_PER_YI = 10_000


def cache_phase(now: datetime) -> str:
    """15:00 后单独一档，早上取过的收盘数据不会挡住当天收盘。"""
    day = now.date()
    mark = "close" if is_trading_day(day) and now.time() >= time(15, 0) else "session"
    return f"{day.isoformat()}#{mark}"


def _ttl(phase: str) -> int:
    return 2 * 3600 if phase.endswith("#close") else 16 * 3600


def _iso(compact: str) -> str:
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


def _trade_date(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text if _DATE.fullmatch(text) else None


def _yi_from_wan(value: float | None) -> float | None:
    return None if value is None else round(value / _WAN_PER_YI, 4)


def _position(close: float, average: float) -> str:
    if round(close - average, 4) == 0:
        return "持平"
    return "上方" if close > average else "下方"


def _failure(body: dict) -> str | None:
    if body.get("code") == 0:
        return None
    msg = body.get("msg")
    if body.get("code") == 40203 or (isinstance(msg, str) and "频率" in msg):
        return "TuShare 行情频率超限，这次没有重试。"
    return "TuShare 没有返回可用的收盘数据。"


def _parse(body: object, fields: str) -> tuple[list[dict], bool]:
    if not isinstance(body, dict):
        raise MarketError("tushare", "TuShare 没有返回可解析的数据。")
    failure = _failure(body)
    if failure:
        raise MarketError("tushare", failure)
    data = body.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise MarketError("tushare", "TuShare 没有返回可解析的数据。")
    expected = fields.split(",")
    names = data.get("fields")
    if not isinstance(names, list) or any(name not in names for name in expected):
        raise MarketError("tushare", "TuShare 返回的字段和请求不一致。")
    rows = []
    for item in data["items"]:
        if isinstance(item, dict):
            values = item
        elif isinstance(item, list) and len(item) == len(names):
            values = dict(zip(names, item))
        else:
            continue
        rows.append({name: values.get(name) for name in expected})
    return rows, bool(data.get("has_more"))


def _bars(rows: list[dict]) -> list[dict]:
    parsed: dict[str, dict] = {}
    for row in rows:
        trade = _trade_date(row.get("trade_date"))
        close = num(row.get("close"))
        if trade is None or close is None or close <= 0:
            continue
        parsed[trade] = {
            "tradeDate": trade,
            "close": close,
            "preClose": num(row.get("pre_close")),
            "high": num(row.get("high")),
            "low": num(row.get("low")),
            "amount": num(row.get("amount")),
            "pct": num(row.get("pct_chg")),
        }
    return [parsed[key] for key in sorted(parsed)]


def _complete(bar: dict) -> bool:
    pre, high, low, amount = bar["preClose"], bar["high"], bar["low"], bar["amount"]
    return (pre is not None and pre > 0 and high is not None and low is not None
            and amount is not None and amount >= 0 and high >= low)


def _window(bars: list[dict]) -> dict:
    empty = {"days": 0, "missingDays": 20, "changePercent": None, "averageTurnoverYi": None,
             "averageAmplitudePercent": None}
    if len(bars) < 20 or any(not _complete(bar) for bar in bars[-20:]):
        have = len(bars) if len(bars) < 20 else sum(1 for bar in bars[-20:] if _complete(bar))
        missing = 20 - have
        return {**empty, "days": have, "missingDays": missing,
                "note": f"近20个交易日只有{have}天，缺{missing}天，累计涨跌幅、日均成交额、日均振幅没有计算。"}
    tail = bars[-20:]
    first, last = tail[0], tail[-1]
    cumulative = (last["close"] / first["preClose"] - 1) * 100
    average_amount = sum(bar["amount"] for bar in tail) / 20 / _QIAN_PER_YI
    average_amplitude = sum((bar["high"] - bar["low"]) / bar["preClose"] * 100 for bar in tail) / 20
    return {"days": 20, "missingDays": 0, "changePercent": round(cumulative, 2),
            "averageTurnoverYi": round(average_amount, 4),
            "averageAmplitudePercent": round(average_amplitude, 2), "note": None}


def _averages(bars: list[dict]) -> tuple[dict, str | None]:
    closes = [bar["close"] for bar in bars]
    latest = closes[-1]
    result = {}
    for width in (5, 10, 20):
        if len(closes) < width:
            result[f"ma{width}"] = None
            continue
        value = sum(closes[-width:]) / width
        result[f"ma{width}"] = {"value": round(value, 4), "position": _position(latest, value)}
    missing = [name for name, value in result.items() if value is None]
    note = None
    if missing:
        label = "、".join(item[2:] + "日" for item in missing)
        note = f"{label}均线天数不够（现有{len(closes)}个收盘价），没有计算。"
    return result, note


def _by_date(rows: list[dict] | None) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for row in rows or []:
        trade = _trade_date(row.get("trade_date"))
        if trade:
            indexed[trade] = row
    return indexed


def _valuation(rows: list[dict] | None, error: str | None, daily_date: str) -> tuple[dict | None, str | None]:
    if error:
        return None, error
    indexed = _by_date(rows)
    if not indexed:
        return None, "TuShare 没有返回市值数据。"
    latest = max(indexed)
    if latest != daily_date:
        return {"tradeDate": _iso(latest), "totalMvYi": None, "circMvYi": None, "peTtm": None, "pb": None,
                "turnoverRate": None,
                "note": f"daily_basic 最新交易日是 {_iso(latest)}，与收盘价 {_iso(daily_date)} 不一致，未混用。"}, None
    row = indexed[latest]
    return {"tradeDate": _iso(latest), "totalMvYi": _yi_from_wan(num(row.get("total_mv"))),
            "circMvYi": _yi_from_wan(num(row.get("circ_mv"))),
            "peTtm": None if num(row.get("pe_ttm")) is None else round(num(row.get("pe_ttm")), 2),
            "pb": None if num(row.get("pb")) is None else round(num(row.get("pb")), 2),
            "turnoverRate": None if num(row.get("turnover_rate")) is None else round(num(row.get("turnover_rate")), 2),
            "note": None}, None


def _moneyflow(rows: list[dict] | None, error: str | None, daily_date: str) -> tuple[dict | None, str | None]:
    if error:
        return None, error
    indexed = _by_date(rows)
    if not indexed:
        return {"tradeDate": None, "netInflowYi": None, "basis": BASIS, "note": "TuShare 没有返回资金净流入。"}, None
    latest = max(indexed)
    if latest != daily_date:
        return {"tradeDate": _iso(latest), "netInflowYi": None, "basis": BASIS,
                "note": f"moneyflow 最新交易日是 {_iso(latest)}，与收盘价 {_iso(daily_date)} 不一致，未混用。"}, None
    amount = num(indexed[latest].get("net_mf_amount"))
    return {"tradeDate": _iso(latest), "netInflowYi": _yi_from_wan(amount), "basis": BASIS, "note": None}, None


def assemble(daily_rows: list[dict], basic: tuple[list[dict] | None, str | None],
             flow: tuple[list[dict] | None, str | None], truncated: bool) -> tuple[dict, list[str]]:
    bars = _bars(daily_rows)
    if not bars:
        raise MarketError("tushare_daily", "TuShare 没有返回收盘数据。")
    last = bars[-1]
    averages, average_note = _averages(bars)
    valuation, valuation_gap = _valuation(basic[0], basic[1], last["tradeDate"])
    moneyflow, flow_gap = _moneyflow(flow[0], flow[1], last["tradeDate"])
    turnover_rate = None
    if valuation and valuation.get("note") is None:
        turnover_rate = valuation.get("turnoverRate")
    gaps = [item for item in (valuation_gap, flow_gap) if item]
    if truncated:
        gaps.append("TuShare 行情可能没有取全。")
    amount = last["amount"]
    payload = {
        "tradeDate": _iso(last["tradeDate"]),
        "close": round(last["close"], 4),
        "changePercent": None if last["pct"] is None else round(last["pct"], 2),
        "turnoverYi": None if amount is None or amount < 0 else round(amount / _QIAN_PER_YI, 4),
        "turnoverRate": turnover_rate,
        "window20": _window(bars),
        "averages": averages,
        "averageNote": average_note,
        "valuation": valuation,
        "moneyflow": moneyflow,
        "adjustment": "不复权：除权除息日前后的累计涨跌幅和均线会失真",
        "source": "TuShare",
    }
    return payload, gaps


class TuShareDaily:
    def __init__(self, token: str | None, client: httpx.AsyncClient | None = None, now=None) -> None:
        self.token = (token or "").strip()
        self.client = client
        self._owns = client is None
        self.now = now or (lambda: datetime.now().astimezone())
        self.cache = RequestCache(256)
        silence_http_logs()

    async def aclose(self) -> None:
        await self.cache.close()
        if self._owns and self.client is not None:
            await self.client.aclose()

    async def _http(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False)
            self._owns = True
        return self.client

    async def _post(self, api_name: str, fields: str, ts_code: str, start: str, end: str) -> tuple[list[dict], bool]:
        client = await self._http()
        payload = {"api_name": api_name, "token": self.token, "fields": fields,
                   "params": {"ts_code": ts_code, "start_date": start, "end_date": end}}
        try:
            response = await client.post(API_URL, json=payload)
        except httpx.HTTPError:
            raise MarketError("tushare", "TuShare 暂时连不上，没有收盘数据。") from None
        if response.status_code != 200:
            raise MarketError("tushare", "TuShare 暂时连不上，没有收盘数据。") from None
        try:
            body = response.json()
        except ValueError:
            raise MarketError("tushare", "TuShare 没有返回可解析的数据。") from None
        return _parse(body, fields)

    async def _cached(self, api_name: str, fields: str, ts_code: str, start: str, end: str, phase: str):
        async def fetch():
            rows, truncated = await self._post(api_name, fields, ts_code, start, end)
            if api_name == "daily" and not rows:
                raise MarketError("tushare_daily", "TuShare 没有返回收盘数据。")
            return rows, truncated

        result, _cached_hit = await self.cache.get((phase, ts_code, api_name), fetch, _ttl(phase), failure_ttl=60)
        return result

    async def _one(self, api_name: str, fields: str, ts_code: str, start: str, end: str, phase: str):
        try:
            rows, truncated = await self._cached(api_name, fields, ts_code, start, end, phase)
            return rows, truncated, None
        except MarketError as exc:
            return None, False, exc.message

    async def load(self, ts_code: str) -> tuple[dict, list[str]]:
        if not self.token:
            raise MarketError("credential_missing", "未配置 TuShare，没有收盘数据。")
        now = self.now()
        phase = cache_phase(now)
        end = now.date().strftime("%Y%m%d")
        start = (now.date() - timedelta(days=120)).strftime("%Y%m%d")
        import asyncio
        daily, basic, flow = await asyncio.gather(
            self._one("daily", DAILY_FIELDS, ts_code, start, end, phase),
            self._one("daily_basic", BASIC_FIELDS, ts_code, start, end, phase),
            self._one("moneyflow", FLOW_FIELDS, ts_code, start, end, phase),
        )
        if daily[0] is None:
            raise MarketError("tushare_daily", daily[2] or "TuShare 没有返回收盘数据。")
        truncated = bool(daily[1] or basic[1] or flow[1])
        return assemble(daily[0], (basic[0], basic[2]), (flow[0], flow[2]), truncated)
