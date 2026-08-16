"""Contracts for the expensive, queued-only research phase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class DeepResearchRequest:
    direction_id: str
    query: str
    enable_search: bool = True


@dataclass(frozen=True)
class DeepResearchResult:
    direction_id: str
    evidence: Mapping[str, Any]
    status: str = "ok"
    unavailable_reason: Optional[str] = None


def request_for(*, direction_id: str, label: str, brief: Mapping[str, Any]) -> DeepResearchRequest:
    """A tiny, auditable query. Callers must only build it for a DeepQueue entry."""
    label_text = str(label or "").strip()
    industry = str(brief.get("industry") or "").strip()
    parts = [label_text]
    if industry and industry != label_text:
        parts.append(industry)
    # Tavily rejects a one-character query (production observation: ``铜``).
    # A fixed market/research suffix is deterministic and improves scope for
    # every direction without inventing a length threshold or another call.
    parts.append("A股 最新产业动态")
    query = " ".join(part for part in parts if part)
    return DeepResearchRequest(direction_id=direction_id, query=query)


__all__ = ["DeepResearchRequest", "DeepResearchResult", "request_for"]
