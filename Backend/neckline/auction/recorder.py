"""Production intraday evidence recorder for K9-v3 packages.

The established realtime provider supplies live snapshots, not a historical
minute API.  This recorder therefore freezes actual provider snapshots while
the single FastAPI process is alive.  It never invents a bar from EOD data:
missing source responses are written to the capture audit and later become
``unavailable`` package conclusions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Mapping, Optional

import polars as pl

from neckline.calendar import CN_TZ, is_trading_day
from neckline.data.market_data import day_file_path, write_table_day
from neckline.data.realtime import get_quotes
from neckline.db import connection
from neckline.scorecard import packages
from neckline.auction import p4

CAPTURE_START = time(9, 26)
CAPTURE_END = time(15, 1)


@dataclass(frozen=True)
class CaptureResult:
    ran: bool
    captured: int = 0
    unavailable: int = 0
    skipped_reason: str = ""


def is_capture_window(now: datetime, *, db_path: Optional[Path] = None) -> bool:
    local = now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)
    try:
        open_day = is_trading_day(local.date(), **({"db_path": db_path} if db_path is not None else {}))
    except RuntimeError:
        return False
    return open_day and CAPTURE_START <= local.time().replace(tzinfo=None) < CAPTURE_END


def _due_codes(trade_date, *, db_path: Optional[Path]) -> set[str]:
    codes: set[str] = set()
    for summary in packages.list_packages(state="active", db_path=db_path):
        if trade_date.strftime("%Y%m%d") not in {summary["d1_trade_date"], summary["d2_trade_date"]}:
            continue
        package = packages.load_package(summary["batch_id"], db_path=db_path)
        if package is None:
            continue
        candidates = package["candidates"]
        codes.update(str(item["tsCode"]) for item in candidates)
        benchmark = p4.frozen_benchmark_code(package)
        if benchmark is not None:
            codes.add(benchmark)
        p4_candidates = []
        for item in candidates:
            frozen = p4.frozen_industry(package, item)
            if frozen is not None:
                p4_candidates.append({**item, "_p4IndustryEvidence": frozen})
        member_codes, _invalid = p4.target_codes(p4_candidates)
        # This can only add frozen constituent equities and the approved market
        # index.  An SW L2 .SI code is never a stock-quote target.
        codes.update(member_codes)
    return codes


def _audit(*, trade_date, captured_at: str, codes: set[str], quotes, status_by_code: Mapping[str, tuple[str, str | None]], db_path: Optional[Path]) -> None:
    with connection(db_path) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO k9_intraday_capture_audit "
            "(trade_date,captured_at,ts_code,source,status,reason) VALUES (?,?,?,?,?,?)",
            [
                (trade_date.strftime("%Y%m%d"), captured_at, code,
                 None if quotes.get(code) is None else quotes[code].source,
                 status_by_code.get(code, ("unavailable", "realtime_quote_missing"))[0],
                 status_by_code.get(code, ("unavailable", "realtime_quote_missing"))[1])
                for code in sorted(codes)
            ],
        )


def record_snapshot(now: datetime, *, db_path: Optional[Path] = None,
                    parquet_dir: Optional[Path] = None) -> CaptureResult:
    """Capture all due D1/D2 candidates plus their frozen benchmark once.

    The caller owns scheduling.  Repeating the same second is idempotent by
    `(ts_code,timestamp)` and does not create a second market observation.
    """
    if not is_capture_window(now, db_path=db_path):
        return CaptureResult(False, skipped_reason="not_capture_window")
    local_now = now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)
    codes = _due_codes(local_now.date(), db_path=db_path)
    if not codes:
        return CaptureResult(False, skipped_reason="no_due_package")
    quotes = get_quotes(sorted(codes))
    captured_at = local_now.isoformat(timespec="seconds")
    status_by_code: dict[str, tuple[str, str | None]] = {}
    try:
        # One day is one immutable path.  Reading through ``get_market_slice``
        # would scan every intraday partition in the year before filtering;
        # one historical schema mismatch could then prevent a brand-new day
        # from creating its first baseline.  Absence is normal; only an
        # existing target-day file that cannot be read is evidence-risk.
        path = day_file_path("intraday_ticks", local_now.date(), parquet_dir)
        old = pl.read_parquet(path) if path.exists() else pl.DataFrame()
    except Exception:
        status_by_code = {code: ("write_failed", "existing_partition_unreadable") for code in codes}
        _audit(trade_date=local_now.date(), captured_at=captured_at, codes=codes, quotes=quotes,
               status_by_code=status_by_code, db_path=db_path)
        return CaptureResult(True, unavailable=len(codes), skipped_reason="existing_partition_unreadable")
    previous: dict[str, tuple[float, float]] = {}
    if not old.is_empty() and {"ts_code", "cum_volume", "cum_amount"}.issubset(old.columns):
        for row in old.sort("timestamp").iter_rows(named=True):
            previous[str(row["ts_code"])] = (float(row.get("cum_volume") or 0), float(row.get("cum_amount") or 0))
    rows = []
    for code in sorted(codes):
        quote = quotes.get(code)
        # ``price`` is a display field and providers may fill it with pre-close
        # on zero turnover.  Only the explicit traded_price is evidence.
        traded_price = None if quote is None else quote.traded_price
        if quote is None or traded_price is None or quote.volume is None or quote.amount is None:
            status_by_code[code] = ("unavailable", "no_valid_trade")
            continue
        volume, amount = float(quote.volume), float(quote.amount)
        prior = previous.get(code)
        prior_volume, prior_amount = prior if prior is not None else (0.0, 0.0)
        delta_volume, delta_amount = volume - prior_volume, amount - prior_amount
        if prior is not None and (delta_volume < 0 or delta_amount < 0):
            status_by_code[code] = ("unavailable", "cumulative_counter_regressed")
            continue
        # A first quote only establishes a counter baseline.  A display price
        # (including provider pre-close fallback) is never settlement evidence.
        valid_trade = prior is not None and (delta_volume > 0 or delta_amount > 0)
        if prior is None:
            status_by_code[code] = ("unavailable", "first_snapshot_baseline")
        elif not valid_trade:
            status_by_code[code] = ("unavailable", "no_counter_increment")
        else:
            status_by_code[code] = ("captured", None)
        rows.append({"ts_code": code, "trade_date": local_now.date(), "timestamp": captured_at,
                     "event_time": local_now.strftime("%H:%M:%S"), "price": float(traded_price),
                     "volume": volume, "amount": amount, "cum_volume": volume, "cum_amount": amount,
                     "volume_delta": delta_volume if prior is not None else None,
                     "amount_delta": delta_amount if prior is not None else None, "valid_trade": valid_trade,
                     "source": quote.source, "source_timestamp": quote.ts,
                     "open": quote.open, "pre_close": quote.pre_close})
    try:
        if rows:
            new = pl.DataFrame(rows)
            frame = pl.concat([old, new], how="diagonal_relaxed") if not old.is_empty() else new
            frame = frame.unique(subset=["ts_code", "timestamp"], keep="last", maintain_order=True)
            write_table_day("intraday_ticks", local_now.date(), frame, parquet_dir=parquet_dir)
    except Exception:
        status_by_code = {code: ("write_failed", "parquet_atomic_write_failed") for code in codes}
        _audit(trade_date=local_now.date(), captured_at=captured_at, codes=codes, quotes=quotes,
               status_by_code=status_by_code, db_path=db_path)
        return CaptureResult(True, unavailable=len(codes), skipped_reason="parquet_atomic_write_failed")
    # Audit follows successful evidence persistence, never precedes it.
    _audit(trade_date=local_now.date(), captured_at=captured_at, codes=codes, quotes=quotes,
           status_by_code=status_by_code, db_path=db_path)
    return CaptureResult(True, captured=sum(1 for row in rows if row["valid_trade"]), unavailable=len(codes) - sum(1 for row in rows if row["valid_trade"]))


__all__ = ["CAPTURE_START", "CAPTURE_END", "CaptureResult", "is_capture_window", "record_snapshot"]
