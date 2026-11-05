"""KX 看板的盘中窗口。交易日只认 `neckline.morning.schedule` 的静态休市表，表外年份抛 CalendarGap。"""
from __future__ import annotations

from datetime import datetime, time

from neckline.morning.schedule import CST, is_trading_day

SESSION_FROM = time(9, 15)   # 含集合竞价；午休也算在窗口里，13:00 开盘后不用重新判断
SESSION_UNTIL = time(15, 5)  # 收盘后留几分钟，右端不含


def in_session(now: datetime) -> bool:
    """交易日 09:15 ≤ t < 15:05 为盘中。周末、节假日不是。日历没覆盖到时抛 CalendarGap。"""
    local = now.astimezone(CST)
    return is_trading_day(local.date()) and SESSION_FROM <= local.time() < SESSION_UNTIL
