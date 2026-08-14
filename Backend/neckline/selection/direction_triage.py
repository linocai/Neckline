"""Low-cost, no-search direction triage parsing and server-side safety clamps."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from .direction_brief import DirectionBrief

TRIAGE_DISPOSITIONS = frozenset({"deep", "normal", "reserve", "unfit"})


@dataclass(frozen=True)
class TriageDecision:
    direction_id: str
    disposition: str
    reason: str
    status: str = "ok"


@dataclass(frozen=True)
class TriageBatch:
    decisions: Tuple[TriageDecision, ...]
    malformed: bool = False


def build_triage_payload(briefs: Sequence[DirectionBrief]) -> Mapping[str, Any]:
    """The provider route must use this payload with ``enable_search=False``."""
    return {"directions": [brief.public_dict() for brief in briefs], "enable_search": False}


def _decode(raw: Any) -> Tuple[Mapping[str, Any], ...]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, Mapping):
        raw = raw.get("directions", raw.get("items", raw.get("results")))
    if not isinstance(raw, list) or not all(isinstance(item, Mapping) for item in raw):
        raise ValueError("triage response must be an array of direction decisions")
    return tuple(raw)


def parse_triage_response(raw: Any, briefs: Sequence[DirectionBrief]) -> TriageBatch:
    """Parse one LLM batch; omissions/malformed content becomes retryable reserve.

    A brief with absent market data is forcibly reserve even if the model calls it
    deep.  Missing is not a reason to silently remove a direction.
    """
    by_id = {item.direction_id: item for item in briefs}
    try:
        records = _decode(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return TriageBatch(tuple(TriageDecision(item.direction_id, "reserve", "triage_malformed_retryable", "retryable") for item in briefs), True)
    parsed: Dict[str, TriageDecision] = {}
    for record in records:
        direction_id = record.get("directionId", record.get("direction_id"))
        if direction_id not in by_id or direction_id in parsed:
            continue
        disposition = str(record.get("disposition", "")).strip().lower()
        reason = str(record.get("reason", "")).strip()[:280]
        if disposition not in TRIAGE_DISPOSITIONS:
            parsed[direction_id] = TriageDecision(direction_id, "reserve", "triage_invalid_disposition_retryable", "retryable")
            continue
        evidence = by_id[direction_id].evidence
        if evidence.get("data_missing") or evidence.get("missing"):
            parsed[direction_id] = TriageDecision(direction_id, "reserve", "data_missing_clamped_reserve")
        else:
            parsed[direction_id] = TriageDecision(direction_id, disposition, reason or "triage_reason_missing")
    decisions = tuple(parsed.get(item.direction_id, TriageDecision(item.direction_id, "reserve", "triage_omitted_retryable", "retryable")) for item in briefs)
    return TriageBatch(decisions, False)


def disposition_map(batch: TriageBatch) -> Mapping[str, str]:
    return {item.direction_id: item.disposition for item in batch.decisions}


__all__ = ["TRIAGE_DISPOSITIONS", "TriageDecision", "TriageBatch", "build_triage_payload", "parse_triage_response", "disposition_map"]
