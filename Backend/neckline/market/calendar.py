"""交易日：周末休市，工作日再看静态休市表。表外年份不猜。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from neckline.calendar.static_holidays import STATIC_CLOSED, STATIC_YEARS


def is_trading_day(day: date) -> bool:
    if day.weekday() >= 5 or day.year not in STATIC_YEARS:
        return False
    return day.isoformat() not in STATIC_CLOSED


def previous_trading_day(day: date) -> date | None:
    cursor = day - timedelta(days=1)
    for _ in range(366):
        if cursor.year < STATIC_YEARS[0]:
            return None
        if is_trading_day(cursor):
            return cursor
        cursor -= timedelta(days=1)
    return None


def intraday_session_date(now: datetime) -> date | None:
    """交易日 09:30 及以后用当天，盘前和休市日用上一个交易日。"""
    day = now.date()
    if is_trading_day(day) and now.time() >= time(9, 30):
        return day
    return previous_trading_day(day)
