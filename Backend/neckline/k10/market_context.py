"""Frozen, descriptive price-reaction input for K10-v1.4 debate.

This is deliberately not a price plan.  It carries only source-backed past
market reactions (or an explicit unavailable state) so the pro and con reads
have identical market evidence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from neckline.calendar.trading_calendar import CN_TZ, MARKET_CLOSE_TIME
from neckline.data.market_data import get_stock_history
from neckline.data.tushare_client import to_ts_code


class MarketContextError(ValueError):
    pass


def _cutoff(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise MarketContextError("行情上下文截止必须是带时区 ISO 时间") from exc
    if parsed.tzinfo is None:
        raise MarketContextError("行情上下文截止必须带时区")
    return parsed.astimezone(CN_TZ)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if converted == converted and converted not in {float("inf"), float("-inf")} else None


def collect_market_context(
    *, company_code: str, cutoff_at: str, parquet_dir: Path,
    clock: Callable[[], datetime] = _now,
) -> dict[str, Any]:
    """Read the recent, already-visible daily/adj-factor history from an explicit path.

    The latest date is clipped before an intraday cutoff, preventing today's
    eventual close from leaking into morning analysis.  This helper is read
    only and intentionally has no fallback to the operational data directory.
    """
    if not isinstance(company_code, str) or not company_code.strip() or not isinstance(parquet_dir, Path):
        raise MarketContextError("公司代码和行情路径必须明确")
    cutoff = _cutoff(cutoff_at)
    collected = clock()
    if not isinstance(collected, datetime) or collected.tzinfo is None:
        raise MarketContextError("行情收集时间必须带时区")
    collected_at = collected.astimezone(timezone.utc).isoformat(timespec="seconds")
    visible_end = cutoff.date() if cutoff.timetz().replace(tzinfo=None) >= MARKET_CLOSE_TIME else cutoff.date() - timedelta(days=1)
    start = visible_end - timedelta(days=21)
    try:
        daily_rows = get_stock_history(company_code, start, visible_end, table="daily", as_of=visible_end, parquet_dir=parquet_dir).to_dicts()
        factor_rows = get_stock_history(company_code, start, visible_end, table="adj_factor", as_of=visible_end, parquet_dir=parquet_dir).to_dicts()
    except Exception:
        return {"status": "unavailable", "reason": "market_history_read_failed", "asOf": cutoff_at, "collectedAt": collected_at, "sourceRefs": [], "recentDays": []}
    if not daily_rows:
        return {"status": "unavailable", "reason": "daily_history_unavailable", "asOf": cutoff_at, "collectedAt": collected_at, "sourceRefs": [], "recentDays": []}
    factors = {str(row.get("trade_date")): _number(row.get("adj_factor")) for row in factor_rows}
    code = to_ts_code(company_code)
    days: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    for row in daily_rows[-10:]:
        trade_date = row.get("trade_date")
        date_text = trade_date.isoformat() if hasattr(trade_date, "isoformat") else str(trade_date)
        days.append({"tradeDate": date_text, "open": _number(row.get("open")), "high": _number(row.get("high")),
                     "low": _number(row.get("low")), "close": _number(row.get("close")),
                     "preClose": _number(row.get("pre_close")), "pctChg": _number(row.get("pct_chg")),
                     "adjFactor": factors.get(str(trade_date))})
        refs.append({"url": f"market-data://daily/{date_text}/{code}", "dataFetchedAt": "unknown", "collectedAt": collected_at, "tradeDate": date_text})
    if factor_rows:
        refs.append({"url": f"market-data://adj_factor/{code}", "dataFetchedAt": "unknown", "collectedAt": collected_at})
    return {"status": "available" if factor_rows else "partial", "asOf": cutoff_at, "collectedAt": collected_at,
            "sourceRefs": refs, "recentDays": days,
            **({"reason": "adj_factor_history_unavailable"} if not factor_rows else {})}


def frozen_analysis_market_context(value: Any, *, cutoff_at: str) -> dict[str, Any]:
    """Validate one already-fetched market snapshot for both debate roles.

    No lookup happens here.  Missing input becomes an explicit fact about
    coverage rather than an implied all-clear or a generated price number.
    """
    if not isinstance(value, Mapping):
        return {"status": "unavailable", "reason": "market_context_not_collected", "asOf": cutoff_at, "sourceRefs": [], "recentDays": []}
    status = value.get("status")
    if status not in {"available", "unavailable", "partial"}:
        raise MarketContextError("行情上下文状态无效")
    as_of = value.get("asOf")
    if not isinstance(as_of, str) or not as_of:
        raise MarketContextError("行情上下文截止无效或晚于分析截止")
    try:
        as_of_time, cutoff_time = _cutoff(as_of), _cutoff(cutoff_at)
    except MarketContextError:
        raise MarketContextError("行情上下文截止无效或晚于分析截止") from None
    if as_of_time > cutoff_time:
        raise MarketContextError("行情上下文截止无效或晚于分析截止")
    refs = value.get("sourceRefs")
    days = value.get("recentDays")
    if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)) or not all(isinstance(item, Mapping) for item in refs):
        raise MarketContextError("行情上下文 sourceRefs 无效")
    if not isinstance(days, Sequence) or isinstance(days, (str, bytes)) or not all(isinstance(item, Mapping) for item in days):
        raise MarketContextError("行情上下文 recentDays 无效")
    if status == "available" and (not refs or not days):
        raise MarketContextError("可用行情上下文必须有来源和实际历史行情")
    collected_at = value.get("collectedAt")
    if collected_at is not None:
        try:
            _cutoff(collected_at)
        except MarketContextError:
            raise MarketContextError("行情上下文 collectedAt 无效") from None
    normalized = {"status": status, "asOf": as_of, "sourceRefs": [dict(item) for item in refs], "recentDays": [dict(item) for item in days]}
    if isinstance(collected_at, str):
        normalized["collectedAt"] = collected_at
    if isinstance(value.get("reason"), str) and value["reason"].strip():
        normalized["reason"] = value["reason"].strip()
    return normalized


def attach_frozen_market_context(*, observation_context: Mapping[str, Any], market_context: Any, cutoff_at: str) -> dict[str, Any]:
    result = dict(observation_context)
    result["marketContext"] = frozen_analysis_market_context(market_context, cutoff_at=cutoff_at)
    return result


__all__ = ["MarketContextError", "attach_frozen_market_context", "collect_market_context", "frozen_analysis_market_context"]


def card_price_context(values, *, company_code: str, cutoff_at: str):
    """Readable publication-time projection; never fetch or backfill on GET."""
    snapshots = []
    for value in values:
        try:
            snapshot = frozen_analysis_market_context(value, cutoff_at=cutoff_at)
        except MarketContextError:
            continue
        if snapshot['status'] not in {'available', 'partial'} or not snapshot['sourceRefs']:
            continue
        as_of = _cutoff(snapshot['asOf'])
        days = []
        for day in snapshot['recentDays']:
            try:
                close_at = datetime.fromisoformat(str(day['tradeDate']) + 'T15:00:00+08:00')
            except (KeyError, ValueError):
                continue
            pct = _number(day.get('pctChg'))
            close, prior = _number(day.get('close')), _number(day.get('preClose'))
            if pct is None and close is not None and prior is not None and prior > 0:
                pct = (close / prior - 1) * 100
            if close_at <= as_of and pct is not None:
                days.append({**day, 'pctChg': pct})
        if days:
            day = max(days, key=lambda item: item['tradeDate'])
            snapshots.append((day['tradeDate'], as_of, snapshot, day))
    if not snapshots:
        return '已有价格反应：暂无截止时点前可核的涨跌资料。', None
    _, _, snapshot, day = max(snapshots, key=lambda item: (item[0], item[1]))
    context = {'asOf': snapshot['asOf'], 'collectedAt': snapshot.get('collectedAt'),
               'tradeDate': day['tradeDate'], 'pctChg': day['pctChg'],
               'sourceRefs': snapshot['sourceRefs']}
    text = f"已有价格反应：{day['tradeDate']} 收盘较前收盘 {day['pctChg']:+.2f}%。仅为行情观察，不能确认消息或归因。"
    return text, context
