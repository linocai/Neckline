"""K9-v3 market-reading adapters.

Realtime quotes are appropriate only for the 9:29 auction snapshot.  D1/D2
settlement uses the locally frozen ``intraday_ticks`` evidence.  A daily bar
never substitutes for a 10:00-after path: incomplete source data is returned
as an explicit unavailable reading and the append-only lifecycle records that
fact.
"""
from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Mapping, Optional

import polars as pl

from neckline.data.market_data import get_market_slice
from neckline.data.realtime import get_quotes
from neckline.scorecard import packages
from neckline.calendar import CN_TZ
from neckline.auction import p4


def _due_codes(*, trade_date: date, db_path: Optional[Path]) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    items: dict[str, Mapping[str, Any]] = {}
    for summary in packages.list_packages(state="active", db_path=db_path):
        if summary["d1_trade_date"] != trade_date.strftime("%Y%m%d"):
            continue
        package = packages.load_package(summary["batch_id"], db_path=db_path)
        if package:
            for candidate in package["candidates"]:
                item = dict(candidate)
                frozen = p4.frozen_industry(package, candidate)
                if frozen is not None:
                    item["_p4IndustryEvidence"] = frozen
                items[candidate["tsCode"]] = item
    return items, sorted(items)


def collect_auction_readings(trade_date: date, *, db_path: Optional[Path] = None,
                             parquet_dir: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Capture a bounded live auction quote for each D1-due package candidate."""
    candidates, codes = _due_codes(trade_date=trade_date, db_path=db_path)
    if not codes:
        return {}
    quotes = get_quotes(codes)
    out: dict[str, dict[str, Any]] = {}
    for code in codes:
        candidate = candidates[code]
        limit = (candidate.get("baseline") or {}).get("limit_up_price")
        quote = quotes.get(code)
        if quote is None or quote.price <= 0:
            out[code] = {"feedStatus": "unavailable", "source": "realtime_quote"}
            continue
        out[code] = {"feedStatus": "available", "source": quote.source, "capturedAt": quote.ts,
                     "auctionPrice": quote.price, "limitUpPrice": limit}
    return out


def collect_open_readings(trade_date: date, *, db_path: Optional[Path] = None,
                          parquet_dir: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Read 9:30–10:00 tick evidence for D1's real tradability reference."""
    candidates, codes = _due_codes(trade_date=trade_date, db_path=db_path)
    return _tick_readings(trade_date, candidates=candidates, codes=codes, start="09:30", end="10:00",
                          include_close=False, parquet_dir=parquet_dir)


def _time_column(frame: pl.DataFrame) -> Optional[str]:
    # recorder persists event_time separately so timezone suffixes can never be
    # mistaken for a trading clock (e.g. ``+08:00`` → 08:00).
    return next((c for c in ("event_time", "time", "timestamp", "trade_time") if c in frame.columns), None)


def parse_local_clock(value: object) -> Optional[time]:
    """Parse a source instant once, preserving seconds and its timezone."""
    if isinstance(value, datetime):
        return (value.replace(tzinfo=CN_TZ) if value.tzinfo is None else value.astimezone(CN_TZ)).timetz().replace(tzinfo=None)
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (parsed.astimezone(CN_TZ) if parsed.tzinfo else parsed.replace(tzinfo=CN_TZ)).timetz().replace(tzinfo=None)
    except ValueError:
        try:
            return time.fromisoformat(text)
        except ValueError:
            return None


def _clock(value: str | time) -> time:
    return value if isinstance(value, time) else time.fromisoformat(value)


def _tick_readings(trade_date: date, *, candidates: Mapping[str, Mapping[str, Any]], codes: list[str],
                   start: str | time, end: str | time, include_close: bool, parquet_dir: Optional[Path],
                   start_inclusive: bool = True) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}
    try:
        frame = get_market_slice(trade_date, table="intraday_ticks", parquet_dir=parquet_dir)
    except Exception:
        frame = pl.DataFrame()
    required = {"ts_code", "price"}
    clock = _time_column(frame) if not frame.is_empty() else None
    if clock is None or not required.issubset(set(frame.columns)):
        return {code: {"feedStatus": "unavailable", "source": "intraday_ticks", "reason": "minute_ticks_missing"} for code in codes}
    extra_codes, invalid_p4 = p4.target_codes(candidates.values())
    values = frame.filter(pl.col("ts_code").is_in([*codes, *sorted(extra_codes)]))
    values = values.with_columns(pl.Series("_clock", [parse_local_clock(v) for v in values[clock].to_list()]))
    # The source timestamps must be explicit local HH:MM values.  Unknown
    # timestamps are excluded rather than guessed to be in the requested span.
    start_time, end_time = _clock(start), _clock(end)
    if "valid_trade" not in values.columns or "volume_delta" not in values.columns or "amount_delta" not in values.columns:
        return {code: {"feedStatus": "unavailable", "source": "intraday_ticks", "reason": "trade_delta_missing"} for code in codes}
    values = values.filter(pl.col("valid_trade").fill_null(False))
    values = values.filter(((pl.col("volume_delta").fill_null(0) > 0) | (pl.col("amount_delta").fill_null(0) > 0)))
    values = values.filter(pl.col("_clock").is_not_null() & (pl.col("price") > 0))
    values = values.filter((pl.col("_clock") >= start_time if start_inclusive else pl.col("_clock") > start_time) & (pl.col("_clock") <= end_time))
    out: dict[str, dict[str, Any]] = {}
    for code in codes:
        candidate = candidates[code]
        part = values.filter(pl.col("ts_code") == code).sort("_clock")
        limit = (candidate.get("baseline") or {}).get("limit_up_price")
        if part.is_empty():
            out[code] = {"feedStatus": "unavailable", "source": "intraday_ticks", "reason": "minute_ticks_missing", "limitUpPrice": limit}
            continue
        ticks = [{"time": r["_clock"].isoformat(), "price": float(r["price"]),
                  "volumeDelta": float(r["volume_delta"]), "amountDelta": float(r["amount_delta"]),
                  "sourceTimestamp": r.get("source_timestamp"), "capturedAt": r.get("timestamp")}
                 for r in part.iter_rows(named=True)]
        raw: dict[str, Any] = {"feedStatus": "available", "source": "intraday_ticks", "limitUpPrice": limit, "trades": ticks}
        frozen = candidate.get("_p4IndustryEvidence")
        if frozen is not None:
            if code in invalid_p4:
                raw["industry"] = {"feedStatus": "unavailable", "reason": invalid_p4[code]}
            else:
                evidence = p4.aggregate(values, contract=frozen, candidate_code=code)
                conditions = ((candidate.get("playbook") or {}).get("conditions") or {}).get("p4")
                industry_conditions = conditions.get("industry") if isinstance(conditions, Mapping) else None
                required_coverage = industry_conditions.get("minimumMemberCoverage") if isinstance(industry_conditions, Mapping) else None
                try:
                    required_coverage = float(required_coverage)
                except (TypeError, ValueError):
                    required_coverage = None
                if (evidence.get("feedStatus") == "available"
                        and (required_coverage is None or evidence.get("coverage", 0.0) < required_coverage)):
                    evidence = {**evidence, "feedStatus": "unavailable", "reason": "member_coverage_below_frozen_plan",
                                "minimumMemberCoverage": required_coverage}
                raw["industry"] = evidence
        if include_close:
            raw.update({"postOpenHigh": float(part["price"].max()), "postOpenLow": float(part["price"].min()), "close": float(part["price"].tail(1)[0])})
        out[code] = raw
    return out


def _due_eod_candidates(trade_date: date, *, db_path: Optional[Path], stage: str) -> dict[str, Mapping[str, Any]]:
    candidates: dict[str, Mapping[str, Any]] = {}
    for summary in packages.list_packages(state="active", db_path=db_path):
        if summary[f"{stage}_trade_date"] != trade_date.strftime("%Y%m%d"):
            continue
        package = packages.load_package(summary["batch_id"], db_path=db_path)
        if package:
            benchmark = p4.frozen_benchmark_code(package)
            for candidate in package["candidates"]:
                value = dict(candidate)
                value["_benchmarkCode"] = benchmark
                candidates[candidate["tsCode"]] = value
    return candidates


def collect_d1_eod_readings(trade_date: date, *, db_path: Optional[Path] = None,
                            parquet_dir: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Only the D1 10:00-after path may decide continuation state."""
    candidates = _due_eod_candidates(trade_date, db_path=db_path, stage="d1")
    out = _tick_readings(trade_date, candidates=candidates, codes=sorted(candidates), start="10:00:00", end="15:00:00",
                         include_close=True, parquet_dir=parquet_dir, start_inclusive=False)
    for code, raw in out.items():
        benchmark = candidates[code].get("_benchmarkCode")
        raw["benchmarkReferencePrice"] = (_benchmark_price(
            trade_date, code=str(benchmark), end=time(10, 0), parquet_dir=parquet_dir
        ) if benchmark else None)
    return out


def collect_d2_eod_readings(trade_date: date, *, db_path: Optional[Path] = None,
                            parquet_dir: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """D2 uses the entire regular-session path, never a daily-bar proxy."""
    candidates = _due_eod_candidates(trade_date, db_path=db_path, stage="d2")
    out = _tick_readings(trade_date, candidates=candidates, codes=sorted(candidates), start="09:30", end="15:00",
                         include_close=True, parquet_dir=parquet_dir)
    benchmark_codes = {str(c.get("_benchmarkCode")) for c in candidates.values() if c.get("_benchmarkCode")}
    if len(benchmark_codes) != 1:
        return out
    benchmark = _benchmark_price(trade_date, code=next(iter(benchmark_codes)), end=time(15, 0), parquet_dir=parquet_dir)
    for raw in out.values():
        raw["benchmarkClosePrice"] = benchmark
    return out


def _benchmark_price(trade_date: date, *, code: str, end: time, parquet_dir: Optional[Path]) -> Optional[float]:
    try:
        frame = get_market_slice(trade_date, table="intraday_ticks", parquet_dir=parquet_dir)
    except Exception:
        return None
    clock = _time_column(frame) if not frame.is_empty() else None
    if clock is None or not {"ts_code", "price"}.issubset(frame.columns):
        return None
    part = frame.filter(pl.col("ts_code") == code)
    part = part.with_columns(pl.Series("_clock", [parse_local_clock(v) for v in part[clock].to_list()]))
    if not {"valid_trade", "volume_delta", "amount_delta"}.issubset(part.columns):
        return None
    part = part.filter(pl.col("valid_trade").fill_null(False) & ((pl.col("volume_delta").fill_null(0) > 0) | (pl.col("amount_delta").fill_null(0) > 0)))
    part = part.filter(pl.col("_clock").is_not_null() & (pl.col("_clock") >= time(9, 30)) & (pl.col("_clock") <= end) & (pl.col("price") > 0)).sort("_clock")
    if part.is_empty():
        return None
    return float(part["price"].tail(1)[0])


__all__ = ["collect_auction_readings", "collect_open_readings", "collect_d1_eod_readings", "collect_d2_eod_readings", "parse_local_clock"]
