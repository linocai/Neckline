"""K9-v3 D1/D2 纯判定；所有数值来自已经批准的参数包。"""
from __future__ import annotations
from typing import Literal, Mapping, Optional

D1Close = Literal["enhanced", "held", "weakened", "unavailable"]
D2Result = Literal["success_realized", "opportunity_not_continued", "confirmed_failed", "correct_reject", "false_reject", "observed_realized", "observed_not_realized", "unavailable"]


def _value(section: Mapping[str, object], *keys: str) -> float:
    for key in keys:
        if key in section:
            return float(section[key])
    raise KeyError(f"结算参数缺少 {' / '.join(keys)}")

def d1_close_state(*, d1_return: Optional[float], post_open_close_location: Optional[float],
                   close_below_invalidation: Optional[bool], settlement: Mapping[str, object]) -> D1Close:
    if d1_return is None or post_open_close_location is None or close_below_invalidation is None: return "unavailable"
    d1 = settlement["d1"]
    if not isinstance(d1, Mapping):
        raise ValueError("d1 结算合同无效")
    if d1_return >= _value(d1, "enhancedReturnPct", "enhancedReturn") and post_open_close_location >= _value(d1, "enhancedCloseLocationPct", "enhancedCloseLocation") and not close_below_invalidation: return "enhanced"
    if d1_return <= _value(d1, "weakenedReturnPct", "weakenedReturn") or close_below_invalidation: return "weakened"
    return "held"

def d2_result(*, open_verdict: Optional[str], d2_max_return: Optional[float], d2_close_return: Optional[float],
              settlement: Mapping[str, object]) -> D2Result:
    if open_verdict is None or d2_max_return is None or d2_close_return is None: return "unavailable"
    d2 = settlement["d2"]
    if not isinstance(d2, Mapping):
        raise ValueError("d2 结算合同无效")
    opportunity = d2_max_return >= _value(d2, "opportunityReturnPct", "opportunityReturn")
    continued = d2_close_return >= _value(d2, "continuationReturnPct", "continuationReturn")
    if open_verdict == "confirmed": return "success_realized" if opportunity and continued else ("opportunity_not_continued" if opportunity else "confirmed_failed")
    if open_verdict == "rejected": return "false_reject" if opportunity else "correct_reject"
    if open_verdict == "observed": return "observed_realized" if opportunity else "observed_not_realized"
    return "unavailable"

def risk_tag(*, max_drawdown: Optional[float], settlement: Mapping[str, object]) -> Optional[str]:
    """Risk is an independent tag and never replaces the D2 result."""
    if max_drawdown is None:
        return None
    d2 = settlement["d2"]
    if not isinstance(d2, Mapping):
        raise ValueError("d2 结算合同无效")
    return "risk" if max_drawdown <= _value(d2, "riskReturnPct", "riskReturn") else None


__all__ = ["d1_close_state", "d2_result", "risk_tag"]
