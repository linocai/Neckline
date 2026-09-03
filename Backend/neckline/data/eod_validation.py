"""Semantic validation for same-day EOD payloads before they become evidence.

An API call can succeed while a provider is still publishing the fields needed
by K9.  These checks run on the in-memory response, before the active parquet
partition is replaced, so an incomplete response is retryable rather than a
false successful update.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

import polars as pl


DAILY_BASIC_REQUIRED_FIELDS = (
    "ts_code",
    "trade_date",
    "free_share",
    "turnover_rate",
    "turnover_rate_f",
)


def _invalid_numeric_count(frame: pl.DataFrame, column: str, *, positive: bool) -> int:
    numeric = pl.col(column).cast(pl.Float64, strict=False)
    valid = numeric.is_finite() & ((numeric > 0) if positive else (numeric >= 0))
    return int(frame.select((~valid.fill_null(False)).sum()).item())


def daily_basic_gaps(
    trade_date: date,
    frame: pl.DataFrame,
    *,
    expected_daily_codes: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Return strategy-critical gaps in one TuShare ``daily_basic`` response."""
    if frame.is_empty():
        return ("daily_basic 返回 0 行",)

    missing = [name for name in DAILY_BASIC_REQUIRED_FIELDS if name not in frame.columns]
    if missing:
        return (f"daily_basic 缺少字段:{','.join(missing)}",)

    gaps: list[str] = []
    target_values = set(frame["trade_date"].drop_nulls().to_list())
    if target_values != {trade_date}:
        gaps.append(
            "daily_basic 交易日错配:"
            + ",".join(sorted(str(value) for value in target_values))
        )

    codes = frame["ts_code"].drop_nulls().cast(pl.String)
    actual_codes = set(codes.to_list())
    null_codes = frame.height - len(codes)
    duplicates = len(codes) - len(actual_codes)
    if null_codes:
        gaps.append(f"daily_basic ts_code 有 {null_codes}/{frame.height} 行为空")
    if duplicates:
        gaps.append(f"daily_basic ts_code 有 {duplicates} 行重复")

    if expected_daily_codes is not None:
        expected = {str(code) for code in expected_daily_codes}
        absent = expected - actual_codes
        if absent:
            gaps.append(
                f"daily_basic 缺少当日 daily 代码 {len(absent)}/{len(expected)}"
            )

    for name, positive in (
        ("free_share", True),
        ("turnover_rate", False),
        ("turnover_rate_f", False),
    ):
        invalid = _invalid_numeric_count(frame, name, positive=positive)
        if invalid:
            gaps.append(f"daily_basic {name} 有 {invalid}/{frame.height} 行缺失或无效")

    return tuple(gaps)


__all__ = ["DAILY_BASIC_REQUIRED_FIELDS", "daily_basic_gaps"]
