"""Frozen-member P4 intraday aggregation.

P4 is intentionally unable to query a Shenwan ``.SI`` code through the stock
quote providers.  Its D0 package freezes the exact fp-4 constituent set; this
module is the sole reader of that frozen contract during D1.
"""
from __future__ import annotations

import hashlib
import math
import statistics
from typing import Any, Iterable, Mapping, Optional

import polars as pl


AGGREGATION_SCHEMA_VERSION = "k9-v3-p4-member-aggregate-v1"


def frozen_benchmark_code(package: Mapping[str, Any]) -> Optional[str]:
    """Return the package-wide frozen market benchmark used by D1/D2.

    The benchmark is a settlement input for every K9-v3 channel, not only P4.
    P4 member evidence carries a duplicate copy for its own aggregation, but a
    P2/P3-only package must still record this approved market index.
    """
    contract = package.get("frozen_contract") or {}
    params = contract.get("parameters") if isinstance(contract, Mapping) else None
    channels = params.get("channels") if isinstance(params, Mapping) else None
    p4_params = channels.get("p4") if isinstance(channels, Mapping) else None
    benchmark = p4_params.get("benchmark") if isinstance(p4_params, Mapping) else None
    code = benchmark.get("indexCode") if isinstance(benchmark, Mapping) else None
    if not isinstance(code, str) or not code.strip() or code.strip().upper().endswith(".SI"):
        return None
    return code.strip()


def frozen_industry(package: Mapping[str, Any], candidate: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    if "p4" not in (candidate.get("channels") or ()):
        return None
    code = candidate.get("swL2Code")
    contract = package.get("frozen_contract") or {}
    root = contract.get("p4IndustryEvidence") if isinstance(contract, Mapping) else None
    industries = root.get("industries") if isinstance(root, Mapping) else None
    item = industries.get(code) if isinstance(industries, Mapping) and isinstance(code, str) else None
    if not isinstance(item, Mapping):
        return None
    return dict(item)


def validate_frozen_industry(item: Mapping[str, Any]) -> Optional[str]:
    if item.get("aggregationSchemaVersion") != AGGREGATION_SCHEMA_VERSION:
        return "frozen_industry_schema_invalid"
    members = item.get("memberCodes")
    if not isinstance(members, list) or not members or any(not isinstance(v, str) or not v for v in members):
        return "frozen_members_missing"
    if any(v.upper().endswith(".SI") for v in members):
        return "frozen_member_code_not_stock"
    deduped = sorted(set(members))
    if deduped != members or item.get("memberCount") != len(members) or item.get("fp4MemberCount") != len(members):
        return "frozen_member_count_invalid"
    digest = hashlib.sha256("\n".join(members).encode("utf-8")).hexdigest()
    if item.get("memberHashSha256") != digest:
        return "frozen_member_hash_invalid"
    benchmark = item.get("benchmark")
    if not isinstance(benchmark, Mapping) or not isinstance(benchmark.get("indexCode"), str) or not benchmark["indexCode"].strip():
        return "frozen_benchmark_missing"
    if str(benchmark["indexCode"]).upper().endswith(".SI"):
        return "frozen_benchmark_not_stock_index"
    return None


def target_codes(items: Iterable[Mapping[str, Any]]) -> tuple[set[str], dict[str, str]]:
    """Return constituent/benchmark targets only from valid frozen contracts."""
    codes: set[str] = set()
    invalid: dict[str, str] = {}
    for candidate in items:
        contract = candidate.get("_p4IndustryEvidence")
        if not isinstance(contract, Mapping):
            continue
        error = validate_frozen_industry(contract)
        key = str(candidate.get("tsCode") or "")
        if error:
            invalid[key] = error
            continue
        codes.update(contract["memberCodes"])
        codes.add(str(contract["benchmark"]["indexCode"]).strip())
    return codes, invalid


def _last_by_code(frame: pl.DataFrame, codes: list[str]) -> dict[str, dict[str, Any]]:
    if frame.is_empty() or not codes:
        return {}
    if not {"valid_trade", "volume_delta", "amount_delta"}.issubset(frame.columns):
        return {}
    values = frame.filter(pl.col("ts_code").is_in(codes) & pl.col("valid_trade").fill_null(False)
                          & ((pl.col("volume_delta").fill_null(0) > 0) | (pl.col("amount_delta").fill_null(0) > 0))).sort(["ts_code", "_clock"])
    out: dict[str, dict[str, Any]] = {}
    for row in values.iter_rows(named=True):
        code = str(row["ts_code"])
        price, pre_close = row.get("price"), row.get("pre_close")
        try:
            price_f, pre_close_f = float(price), float(pre_close)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(price_f) and math.isfinite(pre_close_f) and price_f > 0 and pre_close_f > 0):
            continue
        out[code] = {"price": price_f, "preClose": pre_close_f, "time": str(row.get("_clock") or "")}
    return out


def aggregate(frame: pl.DataFrame, *, contract: Mapping[str, Any], candidate_code: str) -> dict[str, Any]:
    """Aggregate frozen members using last valid 9:30–10:00 evidence.

    Upward breadth is explicitly ``return > 0`` among evaluated frozen members.
    No current member table, industry index, daily bar, or implied coverage ever
    participates in this calculation.
    """
    invalid = validate_frozen_industry(contract)
    if invalid:
        return {"feedStatus": "unavailable", "reason": invalid}
    members = list(contract["memberCodes"])
    latest = _last_by_code(frame, [*members, str(contract["benchmark"]["indexCode"]).strip(), candidate_code])
    returns: list[float] = []
    evidence_times: list[str] = []
    for code in members:
        value = latest.get(code)
        if value is None:
            continue
        returns.append(value["price"] / value["preClose"] - 1)
        evidence_times.append(value["time"])
    evaluated = len(returns)
    member_count = len(members)
    coverage = evaluated / member_count
    benchmark = latest.get(str(contract["benchmark"]["indexCode"]).strip())
    stock = latest.get(candidate_code)
    if benchmark is None:
        return {"feedStatus": "unavailable", "reason": "benchmark_ticks_missing", "memberCount": member_count,
                "evaluatedCount": evaluated, "coverage": coverage}
    if stock is None:
        return {"feedStatus": "unavailable", "reason": "candidate_ticks_missing", "memberCount": member_count,
                "evaluatedCount": evaluated, "coverage": coverage}
    if not returns:
        return {"feedStatus": "unavailable", "reason": "member_ticks_missing", "memberCount": member_count,
                "evaluatedCount": 0, "coverage": 0.0}
    median = float(statistics.median(returns))
    benchmark_return = benchmark["price"] / benchmark["preClose"] - 1
    stock_return = stock["price"] / stock["preClose"] - 1
    return {
        "feedStatus": "available", "aggregationSchemaVersion": AGGREGATION_SCHEMA_VERSION,
        "industryCode": contract["industryCode"], "industryName": contract.get("industryName"),
        "signalTradeDate": contract["signalTradeDate"], "memberHashSha256": contract["memberHashSha256"],
        "memberCount": member_count, "evaluatedCount": evaluated, "coverage": coverage,
        "medianReturn": median, "breadth": sum(value > 0 for value in returns) / evaluated,
        "benchmarkCode": contract["benchmark"]["indexCode"], "benchmarkReturn": benchmark_return,
        "relativeReturn": median - benchmark_return, "stockReturn": stock_return,
        "stockRelativeIndustryReturn": stock_return - median,
        "evidenceTimes": sorted(set(evidence_times + [benchmark["time"], stock["time"]])),
    }


__all__ = [
    "AGGREGATION_SCHEMA_VERSION", "aggregate", "frozen_benchmark_code", "frozen_industry",
    "target_codes", "validate_frozen_industry",
]
