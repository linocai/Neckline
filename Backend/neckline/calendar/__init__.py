"""已查证的静态休市表。

包名 `calendar` 与标准库同名。调用方使用绝对导入
（`from neckline.calendar.static_holidays import ...`），不要裸写 `import calendar`。
"""

from neckline.calendar.static_holidays import CLOSED_2025, CLOSED_2026, STATIC_CLOSED, STATIC_YEARS

__all__ = ["CLOSED_2025", "CLOSED_2026", "STATIC_CLOSED", "STATIC_YEARS"]
