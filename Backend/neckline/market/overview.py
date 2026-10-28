"""单票技术面概览。池外不调用 TuShare 或麦蕊。"""
from __future__ import annotations

import asyncio

from neckline.market.calendar import intraday_session_date
from neckline.market.pool_query import lookup
from neckline.market.services import Services, current
from neckline.market.util import MarketError
from neckline.morning.api import v4_db_path
from neckline.morning import db
from neckline.screening.pool import CompanyPool


def _morning(short_code: str) -> tuple[dict | None, str | None]:
    path = v4_db_path()
    if path is None:
        return None, "晨报库还没配置，没有对照名单。"
    try:
        with db.read(path) as conn:
            report = db.latest_available_report(conn)
    except db.NotReady:
        return None, "晨报库还没准备好，没有对照名单。"
    if report is None or not report.get("content"):
        return None, None
    for entry in report["content"].get("entries") or []:
        if entry.get("companyCode") != short_code:
            continue
        return {
            "reportId": report["report_id"],
            "tradeDate": report["trade_date"],
            # v5 晨报带标题、结论、风险；v4 旧晨报只有 headline、reason，按同样的位置给出。
            "title": entry.get("title") or entry.get("headline"),
            "conclusion": entry.get("conclusion") or entry.get("reason"),
            "risk": entry.get("risk"),
            "documentIds": [item.get("documentId") for item in entry.get("sources") or [] if item.get("documentId")],
        }, None
    return None, None


async def _intraday(services: Services, code: str, now):
    session = intraday_session_date(now)
    if session is None:
        raise MarketError("calendar", "交易日历没有覆盖这一天，没有盘中数据。")
    return await services.mairui.block(code, session)


async def overview(stock: str, *, services: Services | None = None, pool: CompanyPool | None = None) -> dict:
    services = services or current()
    found = lookup(stock, pool if pool is not None else services.pool)
    if not found.get("inPool") or "candidates" in found:
        return found
    now = services.now()
    morning, morning_gap = _morning(found["code"].split(".")[0])
    gaps = [morning_gap] if morning_gap else []
    daily_result, intra_result = await asyncio.gather(
        services.tushare.load(found["code"]), _intraday(services, found["code"], now), return_exceptions=True)
    daily = None
    if isinstance(daily_result, MarketError):
        gaps.append(daily_result.message)
    elif isinstance(daily_result, Exception):
        raise daily_result
    else:
        daily, extra = daily_result
        gaps.extend(extra)
    intraday = None
    if isinstance(intra_result, MarketError):
        gaps.append(intra_result.message)
    elif isinstance(intra_result, Exception):
        raise intra_result
    elif intra_result["quote"] is None and intra_result["series"] is None:
        gaps.extend(intra_result["errors"] or ["没有盘中数据。"])
    else:
        gaps.extend(intra_result["errors"])
        intraday = {"sessionDate": intra_result["sessionDate"], "quote": intra_result["quote"],
                    "series": intra_result["series"]}
    return {"code": found["code"], "name": found["name"], "morningReport": morning, "daily": daily,
            "intraday": intraday, "gaps": gaps, "generatedAt": now.isoformat(timespec="seconds")}
