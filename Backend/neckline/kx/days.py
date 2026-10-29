"""KX 看板用到的交易日规则。交易日只认 `neckline.morning.schedule` 的静态休市表，表外年份抛 CalendarGap。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from neckline.morning.schedule import CST, CalendarGap, is_trading_day, previous_trading_day

CLOSE = time(15, 0)         # 推荐的归属分界：交易日 15:00 前归当天，之后归下一个交易日
QUOTE_DAY_FROM = time(9, 15)  # 行情对应的交易日从 09:15 起算当天（集合竞价的报价算当天）
SESSION_FROM = time(9, 15)    # 盘中刷新窗口：含午休，这样 13:00 开盘后不用重新判断
SESSION_UNTIL = time(15, 5)   # 收盘后留几分钟，取得 15:00 的最终价
_CALL_UNTIL = time(9, 25)     # 09:25 起是开盘价，不再是集合竞价
_OPEN_UNTIL = time(9, 30)     # 09:30 起进入连续竞价


def session_phase(moment: datetime) -> str | None:
    """报价时刻（上海时间）落在竞价的哪一段。

    09:15:00 ≤ t < 09:25:00 为 call_auction，09:25:00 ≤ t < 09:30:00 为 pre_open，其余为空。
    """
    clock = moment.astimezone(CST).time()
    if QUOTE_DAY_FROM <= clock < _CALL_UNTIL:
        return "call_auction"
    if _CALL_UNTIL <= clock < _OPEN_UNTIL:
        return "pre_open"
    return None


def next_trading_day(day: date) -> date:
    probe = day + timedelta(days=1)
    for _ in range(40):
        if is_trading_day(probe):
            return probe
        probe += timedelta(days=1)
    raise CalendarGap(f"{day} 之后 40 天内找不到交易日")


def trading_days_ending(end: date, count: int) -> list[date]:
    """以交易日 end 结尾的连续 count 个交易日，按日期升序。"""
    if not is_trading_day(end):
        raise CalendarGap(f"{end} 不是交易日")
    days = [end]
    while len(days) < count:
        days.append(previous_trading_day(days[-1]))
    days.reverse()
    return days


def heat_days(first_seen: date, end: date) -> int | None:
    """热了第几天：从 first_seen 起到交易日 end 止的交易日数，含两端。

    first_seen 不是交易日就从其后第一个交易日起算；first_seen 晚于 end，或日历没有覆盖到，返回 None。
    """
    try:
        start = first_seen if is_trading_day(first_seen) else next_trading_day(first_seen)
        if start > end:
            return None
        count, day = 0, start
        while day <= end:
            count += is_trading_day(day)
            day += timedelta(days=1)
        return count
    except CalendarGap:
        return None


def pick_trade_date(picked_at: datetime) -> date:
    """推荐归属的交易日：交易日 15:00 之前归当天；15:00 起、周末与节假日归下一个交易日。"""
    local = picked_at.astimezone(CST)
    day = local.date()
    if is_trading_day(day) and local.time() < CLOSE:
        return day
    return next_trading_day(day)


@dataclass(frozen=True)
class BoardClock:
    today: date
    trading_day: bool
    quote_day: date           # 行情应当对应的交易日
    stats_base_day: date      # 日线统计的基准日：行情日的前一个交易日（最近一个完整交易日）
    reference_day: date       # 高亮的参考日
    previous_reference: date  # 参考日的前一个交易日（变淡的那一天）
    in_session: bool


def board_clock(now: datetime) -> BoardClock:
    local = now.astimezone(CST)
    today = local.date()
    trading = is_trading_day(today)
    quote_day = today if trading and local.time() >= QUOTE_DAY_FROM else previous_trading_day(today)
    reference = today if trading else next_trading_day(today)
    return BoardClock(
        today=today, trading_day=trading, quote_day=quote_day, stats_base_day=previous_trading_day(quote_day),
        reference_day=reference,
        previous_reference=previous_trading_day(reference),
        in_session=trading and SESSION_FROM <= local.time() < SESSION_UNTIL)


def highlight(trade_date: date, clock: BoardClock) -> str:
    """推荐状态：归属日不早于参考日是 today；恰是参考日前一个交易日是 faded；其余是 normal。"""
    if trade_date >= clock.reference_day:
        return "today"
    if trade_date == clock.previous_reference:
        return "faded"
    return "normal"
