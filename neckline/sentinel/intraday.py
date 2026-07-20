"""盘中数据纯函数(plan 阶段3 §2.4/§3.7)。继承 LinoN
`/Users/linotsai/Lino/LinoN/backend/app/data/intraday.py` 的四个函数,改接
`neckline.calendar`(而非 LinoN 自己的 `app.calendar`)、`Quote` 换成本包的
`neckline.sentinel.quotes.Quote`,逻辑不变。

本模块只吃「已拿到的数据」(Quote / prev5 均量数值),不自己拉价、不联网——
拉价在 `quotes.py`,组装当日关注池在 `universe.py`,便于单测不联网 + 端点批量
复用一拍拉价。

四个函数职责(与 LinoN 完全一致,含关键的"是否盘中"判定口径选择):
    · `is_intraday_now(now)` —— 本包唯一的"是否盘中"真值源。**刻意把午休
      (11:30-13:00)算作"盘中"**,因为午休时当日累计成交量/amount/VWAP/现价
      都是有效的上午终态,哨兵在午休时段仍可读到有意义的数据(§2.4 工程要求
      「午休可降频」暗示的正是"午休仍在盘中窗口内,只是轮询节奏可以放慢",
      不是"午休不算盘中")。
    · `elapsed_trading_minutes(now)` —— 已开盘时长(分钟),跨午休定格 120。
    · `intraday_vol_ratio(current_vol, prev5_avg_vol, elapsed_min)` —— 按已开盘
      时长折算全天量,除以前5日均量得比值;早盘头 60min/无基准/收盘边缘分支
      各标注 note,买点/证伪哨兵据此决定"数据是否足以下判断"。
    · `vwap_of(quote)` —— vwap = amount/(volume×100)(元/股)。**系数注意**:
      `Quote` 归一后 volume 单位=手、amount 单位=元(见 `quotes.py` 模块头注释),
      与 EOD `daily.amount`(千元)的量纲不同,不要混用两套系数。
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Optional, Tuple

from neckline.calendar import is_trading_day
from neckline.sentinel.quotes import Quote

_OPEN = time(9, 30)
_CLOSE = time(15, 0)
_NOON_START = time(11, 30)
_NOON_END = time(13, 0)

# 早盘头 60min:集合竞价噪声 + A 股早盘量能前置,折算系统性高估,阈提到 60min
# (继承 LinoN 已验证的经验值)。
EARLY_MINUTES_THRESHOLD = 60
# 全天交易分钟数(120 + 120)。
FULL_DAY_MINUTES = 240


def is_intraday_now(now: datetime) -> bool:
    """本包唯一"是否盘中"判定:交易日 且 09:30 ≤ now.time() < 15:00(含午休)。"""
    if not is_trading_day(now.date()):
        return False
    t = now.time()
    return _OPEN <= t < _CLOSE


def elapsed_trading_minutes(now: datetime) -> int:
    """已开盘交易分钟数(跨午休 11:30–13:00 不计,午休期间定格 120)。
    `now<09:30` → 0;`now>=15:00` → 240。纯 datetime 运算,时段有效性由调用方
    先经 `is_intraday_now` 判。"""
    t = now.time()
    if t < _OPEN:
        return 0
    if t >= _CLOSE:
        return FULL_DAY_MINUTES
    if t < _NOON_START:
        delta = datetime.combine(now.date(), t) - datetime.combine(now.date(), _OPEN)
        return int(delta.total_seconds() // 60)
    if t < _NOON_END:
        return 120
    delta = datetime.combine(now.date(), t) - datetime.combine(now.date(), _NOON_END)
    return 120 + int(delta.total_seconds() // 60)


def intraday_vol_ratio(
    current_vol: float,
    prev5_avg_vol: float,
    elapsed_min: int,
) -> Tuple[Optional[float], str]:
    """按已开盘时长折算全天量 / 前5日均量。

    返回 (ratio_or_None, note),note ∈ {"ok","early","closed","no_base"}。
    优先级:early 阈(60min)先判,no_base 次之,>=240 走 closed,否则 ok。
    ratio 保留 1 位小数。
    """
    if elapsed_min < EARLY_MINUTES_THRESHOLD:
        return None, "early"
    if prev5_avg_vol <= 0:
        return None, "no_base"
    projected_full_vol = current_vol / elapsed_min * FULL_DAY_MINUTES
    ratio = round(projected_full_vol / prev5_avg_vol, 1)
    note = "closed" if elapsed_min >= FULL_DAY_MINUTES else "ok"
    return ratio, note


def vwap_of(quote: Optional[Quote]) -> Tuple[Optional[float], Optional[bool]]:
    """vwap = amount/(volume×100)(元/股);is_above_vwap = price >= vwap。
    `quote is None` 或 `volume<=0`(停牌/开盘前/无成交)→ (None, None)。"""
    if quote is None or quote.volume <= 0:
        return None, None
    vwap = quote.amount / (quote.volume * 100.0)
    if vwap <= 0:
        return None, None
    is_above = quote.price >= vwap
    return round(vwap, 4), is_above


__all__ = [
    "is_intraday_now",
    "elapsed_trading_minutes",
    "intraday_vol_ratio",
    "vwap_of",
    "EARLY_MINUTES_THRESHOLD",
    "FULL_DAY_MINUTES",
]
