"""Data contracts for persisted profile rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class CapabilityRow:
    dimension: str
    value: str
    sample_n: int
    win_rate: Optional[float]
    profit_factor: Optional[float]
    avg_mfe: Optional[float]
    avg_mae: Optional[float]
    vs_peer_delta: Optional[float]
    window_start: str
    window_end: str
    confidence: str
    verdict: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "value": self.value,
            "sampleN": self.sample_n,
            "winRate": self.win_rate,
            "profitFactor": self.profit_factor,
            "avgMfe": self.avg_mfe,
            "avgMae": self.avg_mae,
            "vsPeerDelta": self.vs_peer_delta,
            "windowStart": self.window_start,
            "windowEnd": self.window_end,
            "confidence": self.confidence,
            "verdict": self.verdict,
        }


__all__ = ["CapabilityRow"]
