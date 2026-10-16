"""交易日与晨报窗口。

交易日只认已查证的静态休市表（`neckline.calendar.static_holidays`，现覆盖 2025–2026），
超出覆盖年份就报错，不按工作日猜。窗口与截止时刻都来自配置。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from neckline.calendar.static_holidays import STATIC_CLOSED, STATIC_YEARS

CST = timezone(timedelta(hours=8))


class CalendarGap(RuntimeError):
    pass


def is_trading_day(day: date) -> bool:
    if day.year not in STATIC_YEARS:
        raise CalendarGap(f"交易日历未覆盖 {day.year} 年，先补上官方休市安排")
    return day.weekday() < 5 and day.isoformat() not in STATIC_CLOSED


def previous_trading_day(day: date) -> date:
    probe = day - timedelta(days=1)
    for _ in range(40):
        if is_trading_day(probe):
            return probe
        probe -= timedelta(days=1)
    raise CalendarGap(f"{day} 之前 40 天内找不到交易日")


def _clock(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute), tzinfo=CST)


def morning_window(day: date, schedule: dict) -> tuple[datetime, datetime]:
    """上个交易日收盘 → 当天截止（北京时间）。"""
    start = datetime.combine(previous_trading_day(day), _clock(schedule["previousClose"]))
    end = datetime.combine(day, _clock(schedule["windowEnd"]))
    return start, end


def deadline(day: date, schedule: dict) -> datetime:
    return datetime.combine(day, _clock(schedule["deadline"]))
