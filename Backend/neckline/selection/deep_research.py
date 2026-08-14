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
    industry = brief.get("industry")
    query = label if not industry else f"{label} {industry}"
    return DeepResearchRequest(direction_id=direction_id, query=query)


__all__ = ["DeepResearchRequest", "DeepResearchResult", "request_for"]
