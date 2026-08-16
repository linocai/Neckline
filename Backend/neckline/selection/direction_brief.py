"""Mechanical DirectionBrief construction; no narrative generation belongs here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from .direction_inventory import DirectionSeed


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _potential_czy(evidence: Mapping[str, Any]) -> Tuple[str, ...]:
    raw = evidence.get("potential_czy", evidence.get("potential_engines", evidence.get("potential_engine")))
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return ()
    return tuple(sorted({str(x).upper() for x in raw if str(x).upper() in {"C", "Z", "Y"}}))


@dataclass(frozen=True)
class DirectionBrief:
    direction_id: str
    ordinal: int
    seed_key: str
    seed_kind: str
    label: str
    member_codes: Tuple[str, ...]
    industry: Optional[str]
    potential_czy: Tuple[str, ...]
    evidence: Mapping[str, Any]
    merge_status: str = "merge_policy_unconfigured"

    def public_dict(self) -> Mapping[str, Any]:
        return {
            "directionId": self.direction_id, "seedKey": self.seed_key,
            "seedKind": self.seed_kind, "label": self.label,
            "memberCodes": list(self.member_codes), "industry": self.industry,
            "potentialCZY": list(self.potential_czy), "evidence": dict(self.evidence),
            "mergeStatus": self.merge_status,
        }


def build_brief(item: DirectionSeed) -> DirectionBrief:
    evidence = dict(item.evidence)
    industry = _first_text(
        evidence.get("industry"), evidence.get("anchor_industry"),
        item.label if item.seed_kind == "hot_industry" else None,
    )
    return DirectionBrief(
        direction_id=item.direction_id, ordinal=item.ordinal, seed_key=item.seed_key,
        seed_kind=item.seed_kind, label=item.label, member_codes=item.member_codes,
        industry=industry, potential_czy=_potential_czy(evidence), evidence=evidence,
    )


def build_briefs(items: Sequence[DirectionSeed]) -> Tuple[DirectionBrief, ...]:
    return tuple(build_brief(item) for item in items)


__all__ = ["DirectionBrief", "build_brief", "build_briefs"]
