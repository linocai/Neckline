"""Factual explain-layer value types.

These values are deliberately independent of K9-v3 preplans: they are only
the frozen OHLCV context that an explanation may describe.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class Bar:
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    vol: float

    def to_dict(self) -> Dict[str, Any]:
        return {"tradeDate": self.trade_date, "open": self.open, "high": self.high,
                "low": self.low, "close": self.close, "vol": self.vol}


__all__ = ["Bar"]
