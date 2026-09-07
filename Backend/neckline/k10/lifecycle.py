"""Fixed K10-v1.4 opportunity windows.

This module has no wall-clock default: publication storage passes the timestamp captured in
its write transaction.  Calendar coverage must be official; a missing calendar is never
approximated into a D1/D2 sample.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

from neckline.calendar.trading_calendar import CN_TZ, MARKET_CLOSE_TIME, next_trading_day, official_is_trading_day, prev_trading_day


class LifecycleError(ValueError):
    pass


@dataclass(frozen=True)
class FixedWindow:
    d0_trade_date: str
    d1_trade_date: str
    d2_trade_date: str
    d1_selection_at: str
    d2_close_at: str
    delayed: bool


def _iso(value: datetime) -> str:
    return value.astimezone(CN_TZ).isoformat(timespec="seconds")


def expected_d1_for_scan_cutoff(*, cutoff_at: datetime, db_path: Path) -> str:
    """Resolve the D1 the frozen scan cutoff was intended to feed.

    This is deliberately independent of when a worker eventually manages to publish.  It lets a
    delayed evening scan be identified as late without changing its source marker.
    """
    if cutoff_at.tzinfo is None:
        raise LifecycleError("扫描截止时间必须带时区")
    cutoff = cutoff_at.astimezone(CN_TZ)
    official_today = official_is_trading_day(cutoff.date(), db_path=db_path)
    if official_today is None:
        raise LifecycleError("交易日历缺少扫描截止日期覆盖")
    try:
        d1 = cutoff.date() if official_today and cutoff.timetz().replace(tzinfo=None) < time(9, 30) else next_trading_day(cutoff.date(), db_path=db_path)
    except RuntimeError as exc:
        raise LifecycleError("交易日历缺少扫描预期 D1 覆盖") from exc
    return d1.isoformat()


def fixed_window_for_publication(*, available_at: datetime, publication_kind: str, db_path: Path,
                                 expected_d1_trade_date: str | None = None) -> FixedWindow:
    """Assign D1/D2 once from actual visibility, not scan-start or completion metadata."""
    if available_at.tzinfo is None:
        raise LifecycleError("实际可查看时间必须带时区")
    visible = available_at.astimezone(CN_TZ)
    day = visible.date()
    official_today = official_is_trading_day(day, db_path=db_path)
    if official_today is None:
        raise LifecycleError("交易日历缺少实际可查看日期覆盖")
    if publication_kind not in {"evening", "morning"}:
        raise LifecycleError("发布批次类型无效")
    # D1 is determined by actual visibility, not the source scan label. An evening scan that
    # becomes visible on the next morning before 09:30 can still enter that day's observation.
    on_time = official_today is True and visible.timetz().replace(tzinfo=None) < time(9, 30)
    try:
        d1 = day if on_time else next_trading_day(day, db_path=db_path)
        d0 = prev_trading_day(d1, db_path=db_path)
        d2 = next_trading_day(d1, db_path=db_path)
    except RuntimeError as exc:
        raise LifecycleError("交易日历缺少 D0/D1/D2 覆盖") from exc
    d1_at = datetime.combine(d1, time(9, 30), tzinfo=CN_TZ)
    d2_at = datetime.combine(d2, MARKET_CLOSE_TIME, tzinfo=CN_TZ)
    return FixedWindow(d0.isoformat(), d1.isoformat(), d2.isoformat(), _iso(d1_at), _iso(d2_at),
                       expected_d1_trade_date is not None and d1.isoformat() != expected_d1_trade_date)


def windows_overlap(*, d1_a: str, d2_a: str, d1_b: str, d2_b: str) -> bool:
    """Inclusive trading-day overlap; evaluation ownership is fixed at publication time."""
    return max(d1_a, d1_b) <= min(d2_a, d2_b)


__all__ = ["FixedWindow", "LifecycleError", "expected_d1_for_scan_cutoff", "fixed_window_for_publication", "windows_overlap"]
