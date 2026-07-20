"""证伪哨兵(plan §2.4 第4条)。**只用价量结构,不看资金面**(§2.4 铁律:「盘中
主力资金流免费源不可靠,证伪只用价量结构」)。逐字实现候选四件套的证伪条件——
读 `Candidate.invalidation_spec`(阶段2 报告生成时写死,见
`neckline.report.candidates.invalidation_spec`),不重新发明任何阈值:

    · 低开不回:开盘涨幅 ≤ `low_open_pct`(默认 -2%)**且**截至当前仍未翻红
      (现价 < 昨收)。EOD 口径原文是"全天未翻红",盘中检查的是"截至目前"——
      这是哨兵与报告的本质差异:报告是事后总结,哨兵是提前预警,不必等到
      15:00 才告诉用户"今天别进了"。
    · 跌破VWAP:现价 < 当日VWAP(`require...vwap_break` 为真时生效)。
    · 量能异常:折算量比 < `vol_ratio_low`(地量无接力)或 > `vol_ratio_high`
      (异常放量疑似出货)。

命中任一条 → 「剔除勿进」。与买点哨兵共用同一条「开盘头几分钟不判断」纪律
(结构性判断在集合竞价延续期不可靠)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from neckline.report.candidates import Candidate
from neckline.sentinel.entry import MIN_STRUCTURAL_ELAPSED_MINUTES
from neckline.sentinel.intraday import elapsed_trading_minutes, intraday_vol_ratio, vwap_of
from neckline.sentinel.quotes import Quote


@dataclass
class InvalidationSignal:
    ts_code: str
    name: str
    price: float
    reasons: List[str] = field(default_factory=list)

    @property
    def reason_text(self) -> str:
        return ";".join(self.reasons)


def check_invalidation(
    candidate: Candidate,
    quote: Optional[Quote],
    prev5_avg_vol: float,
    now: datetime,
) -> Optional[InvalidationSignal]:
    """候选是否命中前晚写死的证伪条件。`quote is None` → None(拉不到行情时不
    妄下"剔除"判断,宁可漏判也不能拿缺失数据当证据)。"""
    if quote is None:
        return None

    elapsed_min = elapsed_trading_minutes(now)
    if elapsed_min < MIN_STRUCTURAL_ELAPSED_MINUTES:
        return None

    spec = candidate.invalidation_spec or {}
    reasons: List[str] = []

    if quote.pre_close and quote.pre_close > 0:
        gap_pct = (quote.open - quote.pre_close) / quote.pre_close
        low_open_pct = spec.get("low_open_pct")
        still_red = quote.price < quote.pre_close
        if low_open_pct is not None and gap_pct <= low_open_pct and still_red:
            reasons.append(f"低开{gap_pct:.1%}且截至目前未翻红")

    vwap, is_above_vwap = vwap_of(quote)
    if spec.get("vwap_break") and is_above_vwap is False:
        reasons.append(f"现价{quote.price:.2f}跌破当日VWAP{vwap:.2f}")

    vol_ratio, vol_note = intraday_vol_ratio(quote.volume, prev5_avg_vol, elapsed_min)
    if vol_ratio is not None:
        vol_low = spec.get("vol_ratio_low")
        vol_high = spec.get("vol_ratio_high")
        if vol_low is not None and vol_ratio < vol_low:
            reasons.append(f"量能折算仅{vol_ratio:.1f}倍(地量无接力)")
        elif vol_high is not None and vol_ratio > vol_high:
            reasons.append(f"量能折算高达{vol_ratio:.1f}倍(异常放量疑似出货)")

    if not reasons:
        return None

    return InvalidationSignal(ts_code=candidate.ts_code, name=candidate.name, price=quote.price, reasons=reasons)


__all__ = ["InvalidationSignal", "check_invalidation"]
