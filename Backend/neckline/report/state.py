"""The three honest K9-v3 report states."""
from __future__ import annotations
from enum import Enum
from typing import Optional, Sequence

class ReportState(str, Enum):
    HAS_LIST = "has_list"
    EMPTY = "empty"
    NOT_RUN = "not_run"

def resolve_state(*, pack_frozen: bool, params_ok: bool,
                  listing_count: Optional[int]) -> ReportState:
    if not pack_frozen or not params_ok or listing_count is None:
        return ReportState.NOT_RUN
    return ReportState.EMPTY if listing_count == 0 else ReportState.HAS_LIST

def headline(state: ReportState, *, listing_count: Optional[int] = None,
             gaps: Sequence[str] = ()) -> str:
    if state is ReportState.HAS_LIST:
        return f"今天有这些 · {listing_count} 只"
    if state is ReportState.EMPTY:
        return "今天没有"
    detail = "、".join(x for x in gaps if x) or "参数未配置"
    return f"今天没跑成 · {detail}"

__all__ = ["ReportState", "resolve_state", "headline"]
