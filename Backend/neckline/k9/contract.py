"""K9-v3 只读合同；不包含 tier、席位、P1 或跨通道总分。"""
from __future__ import annotations
from enum import Enum
STRATEGY = "K9"
STRATEGY_VERSION = "K9-v3"
class Pattern(str, Enum):
    P2 = "p2"
    P3 = "p3"
    P4 = "p4"
PATTERN_LABEL = {Pattern.P2: "超跌反弹", Pattern.P3: "热门强博弈", Pattern.P4: "行业超跌修复"}
class Shortlist: pass
