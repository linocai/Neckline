"""D1 10:00 settlement attached to K9-v3 package candidates.

The input is an already captured market-reading contract.  We never invent a
reference price: a candidate is tradable only when at least one valid 9:30–10:00
trade was strictly below its limit-up price, and the *last* such valid trade is
the frozen reference.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping, Optional
from neckline.auction import SETTLE_WINDOW_END, SETTLE_WINDOW_START, SKIP_ALREADY_RAN, SKIP_NO_LISTING, SKIP_NOT_WINDOW
from neckline.auction import store
from neckline.auction.readings import parse_local_clock
from neckline.calendar import CN_TZ, is_trading_day
from neckline.dedup import already_pushed, record_pushed
from neckline.scorecard import packages

SETTLE_SCOPE, EVENT_SETTLE = "auction", "v3_settle"

@dataclass
class SettleRunResult:
    trade_date: object; now: datetime; ran: bool = False; skipped_reason: str = ""; settled: int = 0
    confirmed: int = 0; rejected: int = 0; observed: int = 0; unbuyable: int = 0; unavailable: int = 0
    @property
    def counts(self) -> dict[str, int]:
        return {"settled": self.settled, "confirmed": self.confirmed, "rejected": self.rejected,
                "observed": self.observed, "unbuyable": self.unbuyable, "unavailable": self.unavailable}

def is_settle_window(now: datetime, *, db_path: Optional[Path] = None) -> bool:
    local = now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)
    try:
        open_day = is_trading_day(local.date(), **({"db_path": db_path} if db_path is not None else {}))
    except RuntimeError:
        return False
    return open_day and SETTLE_WINDOW_START <= local.time().replace(tzinfo=None) < SETTLE_WINDOW_END


def _valid_reference(reading: Mapping[str, Any]) -> Optional[float]:
    """Return the last valid sub-limit trade, or ``None`` without guessing.

    ``trades`` is deliberately a small stable adapter boundary for the realtime
    collector: each item supplies ``price`` and a local ``time``/``timestamp``.
    Tests and alternative collectors can pass ``referencePrice`` together with
    ``hasSubLimitTrade=True`` when the raw tick list is retained elsewhere.
    """
    limit = reading.get("limitUpPrice")
    try:
        limit_value = float(limit) if limit is not None else None
    except (TypeError, ValueError):
        limit_value = None
    if limit_value is None or limit_value <= 0:
        return None
    last: Optional[float] = None
    for trade in reading.get("trades") or ():
        if not isinstance(trade, Mapping):
            continue
        stamp = parse_local_clock(trade.get("time") or trade.get("timestamp"))
        # Unknown timestamps and 10:00:00.001/10:00:30 are never coerced into
        # the legal inclusive 10:00:00 endpoint.
        if stamp is None or not (time(9, 30) <= stamp <= time(10, 0)):
            continue
        try:
            price = float(trade.get("price"))
        except (TypeError, ValueError):
            continue
        if 0 < price < limit_value:
            last = price
    if last is not None:
        return last
    if reading.get("hasSubLimitTrade") is True:
        try:
            price = float(reading.get("referencePrice"))
        except (TypeError, ValueError):
            return None
        return price if 0 < price < limit_value else None
    return None


def _number(value: object) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _signed_number(value: object) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def _open_rules(candidate: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    playbook = candidate.get("playbook") if isinstance(candidate, Mapping) else None
    rules = playbook.get("openVerdict") if isinstance(playbook, Mapping) else None
    if not isinstance(rules, Mapping):
        return None
    confirm = rules.get("confirmRange")
    if not isinstance(confirm, Mapping):
        return None
    if _number(rules.get("rejectBelow")) is None:
        return None
    if _number(confirm.get("minimum")) is None or _number(confirm.get("maximum")) is None:
        return None
    return rules


def _all_valid_trades_at_limit(reading: Mapping[str, Any], limit: float) -> bool:
    prices = [_number(item.get("price")) for item in reading.get("trades") or () if isinstance(item, Mapping)]
    valid = [price for price in prices if price is not None]
    return bool(valid) and all(price >= limit for price in valid)


def _open_verdict(checklist_verdict: str, reading: Mapping[str, Any],
                  candidate: Optional[Mapping[str, Any]] = None) -> tuple[str, Optional[float], Optional[float]]:
    """Evaluate the frozen D0 mechanics; no branch owns a hidden threshold.

    A candidate without its frozen mechanical contract is unavailable rather
    than silently evaluated using a generic entry/stop rule.
    """
    if reading.get("feedStatus") == "unavailable":
        # Missing tick evidence is neither an observation nor a confirmed
        # unbuyable state.  It must remain auditable as unavailable.
        return "unavailable", None, None
    rules = _open_rules(candidate or {})
    if rules is None:
        return "unavailable", None, None
    limit = _number(rules.get("unbuyableAtOrAbove")) or _number(reading.get("limitUpPrice"))
    if limit is None:
        return "unavailable", None, None
    # The limit used for tradability must be the frozen candidate value.  A
    # collector-provided value is only accepted when it agrees exactly enough
    # to avoid mixing an unknown security rule into the package.
    feed_limit = _number(reading.get("limitUpPrice"))
    if feed_limit is not None and abs(feed_limit - limit) > 1e-6:
        return "unavailable", None, None
    materialized = dict(reading)
    materialized["limitUpPrice"] = limit
    diagnostic_reference = _valid_reference(materialized)
    if checklist_verdict == "rejected":
        # The D1 price remains a diagnostic baseline for judging whether the
        # rejection was right.  It is never a tradable K9 reference.
        return "rejected", None, diagnostic_reference
    if checklist_verdict == "unbuyable":
        return "unbuyable", None, None
    if diagnostic_reference is not None:
        reject_below = _number(rules.get("rejectBelow"))
        confirm = rules["confirmRange"]
        floor, ceiling = _number(confirm.get("minimum")), _number(confirm.get("maximum"))
        if reject_below is None or floor is None or ceiling is None or floor > ceiling:
            return "unavailable", None, None
        if diagnostic_reference < reject_below or diagnostic_reference >= (_number(rules.get("overextendedAtOrAbove")) or limit):
            return "rejected", None, diagnostic_reference
        conditions = (candidate or {}).get("playbook", {}).get("conditions") or {}
        if not isinstance(conditions, Mapping):
            return "unavailable", None, None
        rejected = False
        unavailable = False
        confirmed_conditions = True
        for channel in (candidate or {}).get("channels") or ():
            condition = conditions.get(channel)
            if not isinstance(condition, Mapping):
                unavailable = True
                continue
            if channel in {"p2", "p3"}:
                hold = _number(condition.get("holdAbove"))
                if hold is None:
                    unavailable = True
                    continue
                if diagnostic_reference < hold:
                    rejected = True
            elif channel == "p4":
                stock = condition.get("stock")
                industry = condition.get("industry")
                evidence = reading.get("industry")
                if not isinstance(stock, Mapping) or not isinstance(industry, Mapping) or not isinstance(evidence, Mapping):
                    unavailable = True
                    continue
                coverage = _signed_number(evidence.get("coverage"))
                stock_hold = _number(stock.get("holdAbove"))
                members = _signed_number(evidence.get("memberCount"))
                evaluated = _signed_number(evidence.get("evaluatedCount"))
                median = _signed_number(evidence.get("medianReturn"))
                breadth = _signed_number(evidence.get("breadth"))
                relative = _signed_number(evidence.get("relativeReturn"))
                stock_relative = _signed_number(evidence.get("stockRelativeIndustryReturn"))
                required = {"minimumMemberCoverage": _signed_number(industry.get("minimumMemberCoverage")),
                            "median": _signed_number(industry.get("medianReturnAtOrAbove")),
                            "breadth": _signed_number(industry.get("breadthAtOrAbove")),
                            "relative": _signed_number(industry.get("relativeBenchmarkReturnAtOrAbove")),
                            "failMedian": _signed_number(industry.get("failBelowMedianReturn")),
                            "failBreadth": _signed_number(industry.get("failBelowBreadth")),
                            "failRelative": _signed_number(industry.get("failBelowRelativeBenchmarkReturn")),
                            "stockRelative": _signed_number(stock.get("relativeIndustryReturnAtOrAbove"))}
                if (evidence.get("feedStatus") == "unavailable" or stock_hold is None or members is None or evaluated is None
                        or coverage is None or median is None or breadth is None or relative is None or stock_relative is None
                        or any(value is None for value in required.values())):
                    unavailable = True
                    continue
                if diagnostic_reference < stock_hold or median < required["failMedian"] or breadth < required["failBreadth"] or relative < required["failRelative"]:
                    rejected = True
                if not (coverage >= required["minimumMemberCoverage"] and median >= required["median"]
                        and breadth >= required["breadth"] and relative >= required["relative"]
                        and stock_relative >= required["stockRelative"]):
                    confirmed_conditions = False
        # A definite rejection from any channel always wins.  If no channel
        # rejects, incomplete P4 evidence cannot be promoted to confirmation.
        if rejected:
            return "rejected", None, diagnostic_reference
        if unavailable:
            return "unavailable", None, None
        if floor <= diagnostic_reference <= ceiling and confirmed_conditions:
            return "confirmed", diagnostic_reference, diagnostic_reference
        return "observed", diagnostic_reference, diagnostic_reference
    price = _number(reading.get("referencePrice"))
    at_limit = price is not None and price >= limit
    # An explicit at-limit read or an explicit "all ticks unbuyable" statement
    # is different from a missing/invalid market feed.
    if at_limit or reading.get("allValidTradesAtLimit") is True or _all_valid_trades_at_limit(reading, limit):
        return "unbuyable", None, None
    return "unavailable", None, None

def run_settle_tick(now: datetime, *, db_path: Optional[Path] = None, parquet_dir: Optional[Path] = None,
                    readings: Mapping[str, Mapping[str, Any]] = {}) -> SettleRunResult:
    now = now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)
    result = SettleRunResult(now.date(), now)
    if not is_settle_window(now, db_path=db_path): result.skipped_reason = SKIP_NOT_WINDOW; return result
    if already_pushed(now.date(), SETTLE_SCOPE, "", EVENT_SETTLE, db_path=db_path):
        result.skipped_reason = SKIP_ALREADY_RAN; return result
    due = [p for p in packages.list_packages(state="active", db_path=db_path) if p["d1_trade_date"] == now.strftime("%Y%m%d")]
    if not due: result.skipped_reason = SKIP_NO_LISTING; return result
    for summary in due:
        package = packages.load_package(summary["batch_id"], db_path=db_path)
        snapshot = store.load_checklist(summary["batch_id"], db_path=db_path)
        if package is None or snapshot is None: continue
        verdicts = {r["tsCode"]: r for seg in snapshot["segments"] for r in seg["rows"]}
        for item in package["candidates"]:
            # D1 rows are immutable.  A retry must only settle the missing
            # candidates, never compare frozen evidence with a newer tick.
            if (item.get("d1") or {}).get("openVerdict") is not None:
                continue
            frozen = verdicts.get(item["tsCode"], {"verdict": "pending_open"})
            check = frozen["verdict"]
            # Do not load a later revision when settling: D1 is defined by the
            # 9:26 snapshot, including every channel/industry condition.
            item = {**item, "playbook": dict(frozen.get("frozenPlaybook") or item.get("playbook") or {})}
            raw = dict(readings.get(item["tsCode"], {}))
            open_verdict, reference, diagnostic_reference = _open_verdict(check, raw, item)
            if diagnostic_reference is not None and reference is None:
                raw["diagnosticReferencePrice"] = diagnostic_reference
            packages.append_d1(batch_id=summary["batch_id"], ts_code=item["tsCode"], checklist_verdict=check,
                               open_verdict=open_verdict, reference_price=reference, raw=raw, db_path=db_path)
            result.settled += 1; setattr(result, open_verdict, getattr(result, open_verdict) + 1)
    # Mark the 10:00 event only after every due candidate has a frozen D1 row.
    # This also repairs the crash window after the final append but before the
    # event write: a retry has zero new rows yet still records completion.
    completed = True
    for summary in due:
        package = packages.load_package(summary["batch_id"], db_path=db_path)
        if package is None or any((candidate.get("d1") or {}).get("openVerdict") is None
                                  for candidate in package["candidates"]):
            completed = False
            break
    result.ran = completed
    if completed:
        record_pushed(now.date(), SETTLE_SCOPE, "", EVENT_SETTLE, payload=result.counts, db_path=db_path)
    return result

__all__ = ["SETTLE_SCOPE", "EVENT_SETTLE", "SettleRunResult", "is_settle_window", "run_settle_tick", "_valid_reference", "_open_verdict"]
