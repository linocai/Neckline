"""K10 固定资料窗口。

交易日由任务调用方明确传入；本模块不猜测周末或节假日。所有边界统一使用北京时间，
避免任务实际启动时间把 21:00 / 09:00 的资料截止向后漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _cutoff(day: date, at: time) -> datetime:
    if not isinstance(day, date) or isinstance(day, datetime):
        raise TypeError("K10 窗口必须显式传入 date 类型的交易日")
    return datetime.combine(day, at, tzinfo=SHANGHAI)


def evening_cutoff(trading_day: date) -> datetime:
    """D0 晚间扫描的固定截止，**不包含**恰在 21:00 公开的资料。"""
    return _cutoff(trading_day, time(21, 0))


def morning_cutoff(observation_day: date) -> datetime:
    """D1 晨间扫描的固定且包含的截止；任务晚启动也不能改变它。"""
    return _cutoff(observation_day, time(9, 0))


@dataclass(frozen=True)
class ScanWindow:
    """资料公开时间窗口，而非任务运行时间。

    ``start_inclusive`` / ``end_inclusive`` 必须由调用方按扫描类型给定。晚间
    从上次来源成功水位开区间开始；晨间固定接住 21:00，并包含 09:00。
    """

    kind: str
    start_at: datetime | None
    cutoff_at: datetime
    start_inclusive: bool
    cutoff_inclusive: bool

    def __post_init__(self) -> None:
        if self.kind not in {"evening", "morning"}:
            raise ValueError("K10 window kind 必须是 evening 或 morning")
        if self.cutoff_at.tzinfo is None:
            raise ValueError("K10 cutoff 必须带时区")
        if self.start_at is not None:
            if self.start_at.tzinfo is None:
                raise ValueError("K10 start 必须带时区")
            if self.start_at > self.cutoff_at:
                raise ValueError("K10 window start 不能晚于 cutoff")

    def contains(self, published_at: datetime) -> bool:
        """按资料公开时间判断；日期精度不明的资料不能在此被擅自归窗。"""
        if published_at.tzinfo is None:
            raise ValueError("published_at 必须带时区")
        if self.start_at is not None:
            if published_at < self.start_at:
                return False
            if published_at == self.start_at and not self.start_inclusive:
                return False
        if published_at > self.cutoff_at:
            return False
        if published_at == self.cutoff_at and not self.cutoff_inclusive:
            return False
        return True


def evening_window(*, trading_day: date, source_success_watermark: datetime | None) -> ScanWindow:
    """D0 窗口以每来源自身成功水位为起点，21:00 排除到 D1 晨间。"""
    cutoff = evening_cutoff(trading_day)
    if source_success_watermark is not None and source_success_watermark.tzinfo is None:
        raise ValueError("source_success_watermark 必须带时区")
    return ScanWindow(
        kind="evening",
        start_at=source_success_watermark,
        cutoff_at=cutoff,
        start_inclusive=False,
        cutoff_inclusive=False,
    )


def morning_window(*, previous_trading_day: date, observation_day: date) -> ScanWindow:
    """D1 增量窗口严格为 ``[D0 21:00, D1 09:00]``。

    两个交易日必须由调用者依据真实日历提供；这里仅拒绝显然倒置的日期。
    """
    if previous_trading_day >= observation_day:
        raise ValueError("previous_trading_day 必须早于 observation_day")
    return ScanWindow(
        kind="morning",
        start_at=evening_cutoff(previous_trading_day),
        cutoff_at=morning_cutoff(observation_day),
        start_inclusive=True,
        cutoff_inclusive=True,
    )


__all__ = [
    "SHANGHAI", "ScanWindow", "evening_cutoff", "evening_window", "morning_cutoff",
    "morning_window",
]
