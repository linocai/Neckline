"""K10 固定资料窗口。

执行自然日由任务调用方明确传入；是否开市由调用方查交易所日历。所有边界统一使用北京时间，
避免任务实际启动时间把 21:00 / 08:30 的资料截止向后漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _cutoff(day: date, at: time) -> datetime:
    if not isinstance(day, date) or isinstance(day, datetime):
        raise TypeError("K10 窗口必须显式传入 date 类型的交易日")
    return datetime.combine(day, at, tzinfo=SHANGHAI)


def evening_cutoff(trading_day: date) -> datetime:
    """执行自然日晚间的固定截止，**不包含**恰在 21:00 公开的资料。"""
    return _cutoff(trading_day, time(21, 0))


def scan_calendar_day(*, kind: str, run_day: date) -> date:
    """早报查当天是否开市，晚报查翌日；与实际启动时间无关。"""
    if not isinstance(run_day, date) or isinstance(run_day, datetime):
        raise TypeError("报告执行日期必须是 date")
    if kind not in {"evening", "morning"}:
        raise ValueError("kind 必须是 evening 或 morning")
    return run_day + timedelta(days=1) if kind == "evening" else run_day


def morning_cutoff(observation_day: date) -> datetime:
    """晨报输入08:30冻结；09:20是交付诊断而非取数边界。"""
    return _cutoff(observation_day, time(8, 30))


def morning_delivery_deadline(observation_day: date) -> datetime:
    """Return the immutable B78 morning readability deadline for this trading day."""
    return _cutoff(observation_day, time(9, 20))


@dataclass(frozen=True)
class ScanWindow:
    """资料公开时间窗口，而非任务运行时间。

    ``start_inclusive`` / ``end_inclusive`` 必须由调用方按扫描类型给定。晚间
    从上次来源成功水位开区间开始；晨间固定接住 21:00，并包含 08:30。
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


def morning_window(*, observation_day: date) -> ScanWindow:
    """增量窗口为前一自然日21:00至交易日08:30，包含两个端点。"""
    cutoff = morning_cutoff(observation_day)
    return ScanWindow(
        kind="morning",
        start_at=evening_cutoff(observation_day - timedelta(days=1)),
        cutoff_at=cutoff,
        start_inclusive=True,
        cutoff_inclusive=True,
    )


__all__ = [
    "SHANGHAI", "ScanWindow", "evening_cutoff", "evening_window", "morning_cutoff",
    "morning_window", "scan_calendar_day",
]
