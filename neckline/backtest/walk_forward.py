"""Walk-forward 时间切分器骨架(plan 0.8)。供阶段 1 调参用:样本内窗口跑参数选优,
样本外窗口验证——"回测 + walk-forward 样本外跑赢现役版本才可上线"(§2.6 策略进化
带笼子)。阶段 0 只搭切分器,真正的"参数选优 + 样本外对照现役版本"逻辑留阶段 1。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from neckline.calendar import trading_days_between


@dataclass
class WalkForwardWindow:
    train_start: date
    train_end: date
    test_start: date
    test_end: date


def generate_walk_forward_windows(
    start: date,
    end: date,
    train_days: int,
    test_days: int,
    step_days: Optional[int] = None,
) -> List[WalkForwardWindow]:
    """滚动切分 [start, end] 为若干 (样本内窗, 样本外窗) 对。

    `step_days` 默认等于 `test_days`(样本外窗口首尾相接、不重叠;传更小的值可让
    窗口重叠滚动)。窗口按【交易日】计数,不是自然日。区间不足一组完整窗口时返回
    空列表(不报错,调用方按空列表判断"样本不够")。
    """
    if train_days <= 0 or test_days <= 0:
        raise ValueError("train_days / test_days 必须 > 0")
    all_days = trading_days_between(start, end)
    step = step_days if step_days and step_days > 0 else test_days

    windows: List[WalkForwardWindow] = []
    i = 0
    while True:
        train_slice = all_days[i : i + train_days]
        test_slice = all_days[i + train_days : i + train_days + test_days]
        if len(train_slice) < train_days or len(test_slice) < test_days:
            break
        windows.append(
            WalkForwardWindow(
                train_start=train_slice[0],
                train_end=train_slice[-1],
                test_start=test_slice[0],
                test_end=test_slice[-1],
            )
        )
        i += step
    return windows


__all__ = ["WalkForwardWindow", "generate_walk_forward_windows"]
