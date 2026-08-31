"""Append-only D1 close and D2 settlement for K9-v3 packages.

Collectors supply an explicit, frozen reading mapping.  This module never
falls back to a live quote or yesterday's close: absent readings become an
honest ``unavailable`` conclusion and eventually a partial/unavailable
coverage state.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

from neckline.k9 import v3_settlement
from neckline.scorecard import packages


def _number(raw: Mapping[str, Any], key: str) -> Optional[float]:
    try:
        value = raw.get(key)
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _settlement(package: Mapping[str, Any]) -> Mapping[str, object]:
    value = package.get("frozen_contract", {}).get("parameters", {}).get("settlement")
    if not isinstance(value, Mapping):
        raise ValueError("成绩包缺少冻结 settlement 合同")
    return value


def _playbook_result(candidate: Mapping[str, Any], *, high: Optional[float], close: Optional[float]) -> Optional[str]:
    playbook = candidate.get("playbook") or {}
    invalidation = _number(playbook, "invalidation")
    first_resistance = _number(playbook, "firstResistance")
    if high is None or close is None or invalidation is None or first_resistance is None:
        return "unavailable"
    if high >= first_resistance:
        return "target_reached"
    if close < invalidation:
        return "invalidated"
    return "not_reached"


def settle_d1_close_for_due(*, trade_date: date, readings: Mapping[str, Mapping[str, Any]],
                            db_path: Optional[Path] = None) -> int:
    """Append D1 close states for every package due today; retries are idempotent."""
    count = 0
    for summary in packages.list_packages(state="active", db_path=db_path):
        if summary["d1_trade_date"] != trade_date.strftime("%Y%m%d"):
            continue
        package = packages.load_package(summary["batch_id"], db_path=db_path)
        if package is None:
            continue
        settlement = _settlement(package)
        for candidate in package["candidates"]:
            d1 = candidate.get("d1") or {}
            if d1.get("openVerdict") is None:
                continue
            if d1.get("closeState") is not None:
                continue
            raw = dict(readings.get(candidate["tsCode"], {}))
            d1_raw = d1.get("raw") or {}
            # Rejected at 9:29 is non-tradable, but a captured 10:00
            # sub-limit print remains the diagnostic baseline for the
            # correct-reject / false-reject pair.  It never marks the item
            # tradable or changes the frozen opening verdict.
            reference = d1.get("referencePrice") or _number(d1_raw, "diagnosticReferencePrice")
            close = _number(raw, "close")
            high = _number(raw, "postOpenHigh")
            low = _number(raw, "postOpenLow")
            d1_return = None if reference in (None, 0) or close is None else close / float(reference) - 1
            # A flat valid path has a defined neutral close location.  Only
            # missing/inverted fields are unavailable.
            location = (None if high is None or low is None or close is None or high < low
                        else (0.5 if high == low else (close - low) / (high - low)))
            invalidation = _number(candidate.get("playbook") or {}, "invalidation")
            below = None if close is None or invalidation is None else close < invalidation
            state = v3_settlement.d1_close_state(
                d1_return=d1_return, post_open_close_location=location,
                close_below_invalidation=below, settlement=settlement,
            )
            raw.update({"d1ReferencePrice": reference, "d1Close": close, "postOpenLow": low, "d1Return": d1_return,
                        "postOpenCloseLocation": location, "closeBelowInvalidation": below})
            packages.append_d1_close(batch_id=summary["batch_id"], ts_code=candidate["tsCode"],
                                     close_state=state, raw=raw, db_path=db_path)
            count += 1
    return count


def settle_d2_for_due(*, trade_date: date, readings: Mapping[str, Mapping[str, Any]],
                      db_path: Optional[Path] = None) -> int:
    """Append D2 final rows.  Missing source values permanently settle unavailable."""
    count = 0
    for summary in packages.list_packages(state="active", db_path=db_path):
        if summary["d2_trade_date"] != trade_date.strftime("%Y%m%d"):
            continue
        package = packages.load_package(summary["batch_id"], db_path=db_path)
        if package is None:
            continue
        settlement = _settlement(package)
        for candidate in package["candidates"]:
            if candidate.get("d2") is not None:
                continue
            d1 = candidate.get("d1") or {}
            d1_raw = d1.get("raw") or {}
            reference = d1.get("referencePrice") or _number(d1_raw, "diagnosticReferencePrice")
            raw = dict(readings.get(candidate["tsCode"], {}))
            d1_close_raw = (candidate.get("d1") or {}).get("closeRaw") or {}
            d1_close = _number(d1_close_raw, "d1Close")
            d1_low = _number(d1_close_raw, "postOpenLow")
            d2_high, d2_low, d2_close = _number(raw, "postOpenHigh"), _number(raw, "postOpenLow"), _number(raw, "close")
            if reference in (None, 0):
                max_return = close_return = max_drawdown = None
            else:
                ref = float(reference)
                max_return = None if d2_high is None else d2_high / ref - 1
                close_return = None if d2_close is None else d2_close / ref - 1
                low_values = [v for v in (d1_low, d1_close, d2_low) if v is not None]
                max_drawdown = None if not low_values else min(low_values) / ref - 1
            benchmark_return = relative_benchmark = None
            try:
                benchmark_reference = _number(d1_close_raw, "benchmarkReferencePrice")
                benchmark_close = _number(raw, "benchmarkClosePrice")
                benchmark_return = (None if benchmark_reference in (None, 0) or benchmark_close is None
                                    else benchmark_close / float(benchmark_reference) - 1)
                relative_benchmark = None if close_return is None or benchmark_return is None else close_return - benchmark_return
                if raw.get("feedStatus") == "unavailable" or relative_benchmark is None:
                    raise ValueError("分时路径或同口径基准缺失")
                selection = v3_settlement.d2_result(open_verdict=d1.get("openVerdict"),
                                                     d2_max_return=max_return,
                                                     d2_close_return=close_return,
                                                     settlement=settlement)
                risk = v3_settlement.risk_tag(max_drawdown=max_drawdown, settlement=settlement)
            except (KeyError, TypeError, ValueError):
                # A malformed frozen package is not repaired at settlement time.
                selection, risk = "unavailable", None
            raw.update({"d1ReferencePrice": reference, "d1TenToCloseReturn": None if reference in (None, 0) or d1_close is None else d1_close / float(reference) - 1,
                        "d2MaxReturn": max_return, "d2CloseReturn": close_return,
                        "maxDrawdown": max_drawdown, "benchmarkReturn": benchmark_return, "relativeBenchmark": relative_benchmark,
                        "tradable": d1.get("openVerdict") in {"confirmed", "observed"} and reference not in (None, 0)})
            packages.append_d2(batch_id=summary["batch_id"], ts_code=candidate["tsCode"],
                               selection_result=selection,
                               playbook_result=_playbook_result(candidate, high=d2_high, close=d2_close),
                               risk_tag=risk, raw=raw, db_path=db_path)
            count += 1
    return count


__all__ = ["settle_d1_close_for_due", "settle_d2_for_due"]
