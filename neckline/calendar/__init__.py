"""交易日历原语(plan 0.3;主真值源 trade_cal 落 SQLite,静态表兜底/交叉核对)。

注意:本包名为 `calendar`,与标准库同名。包内 / 调用方一律【绝对导入】
(`from neckline.calendar import ...` / `from neckline.calendar.trading_calendar import ...`),
不要在任何地方裸写 `import calendar` 期望拿到标准库(继承 LinoN 教训)。
"""

from neckline.calendar.trading_calendar import (
    is_trading_day,
    next_trading_day,
    prev_trading_day,
    trading_days_between,
    trading_window,
    verify_against_static,
    reset_cache,
)

__all__ = [
    "is_trading_day",
    "next_trading_day",
    "prev_trading_day",
    "trading_days_between",
    "trading_window",
    "verify_against_static",
    "reset_cache",
]
