"""架构第一层 · 事实层(V2.5.0,PROJECT_PLAN §5.3)。只回答「今天市场发生了什么」,
**只装一天的事实,不装任何窗口量** —— 窗口长度全是策略参数,装进事实层就把可调项
埋进了不该调的地方(架构 §二 判据)。

S1 现状:本包目前只有 `limitmap.py`(自 `facts/limitmap.py` 原样搬入,涨停共振簇),
其余(`pack` / `store` / `industry` / `completeness` / `direction_llm`)归 S3。
"""

from __future__ import annotations

__all__: list = []
