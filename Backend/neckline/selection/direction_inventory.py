"""Deterministic, zero-LLM inventory for every scan DriverSeed (V2.4.2)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence, Tuple

from neckline.scan.seeds import DriverSeed


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class DirectionSeed:
    """One visible source direction.  ``ordinal`` is the mechanical scan order."""

    direction_id: str
    ordinal: int
    seed_key: str
    seed_kind: str
    label: str
    member_codes: Tuple[str, ...]
    evidence: Mapping[str, Any]

    @property
    def identity(self) -> Tuple[str, str, str, Tuple[str, ...], str]:
        """Full source identity; only exact duplicates may be removed before a merge policy exists."""
        return (self.seed_key, self.seed_kind, self.label, self.member_codes, _json(self.evidence))


@dataclass(frozen=True)
class InventoryResult:
    directions: Tuple[DirectionSeed, ...]
    duplicate_count: int


def direction_id_for(seed: DriverSeed) -> str:
    """Stable ID scoped to the input identity, not a subjective/fuzzy direction merge."""
    payload = _json({
        "seed_key": seed.seed_key,
        "seed_kind": seed.seed_kind,
        "label": seed.label,
        "member_codes": tuple(seed.member_codes),
        "evidence": seed.evidence,
    })
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def build_inventory(seeds: Iterable[DriverSeed]) -> InventoryResult:
    """Make every input visible and remove only byte-for-byte source duplicates.

    This function is deliberately pure: it cannot receive an LLM/provider and is the
    guardrail against reintroducing the legacy first-20 eligibility cutoff.
    """
    out = []
    seen = set()
    duplicate_count = 0
    for seed in seeds:
        item = DirectionSeed(
            direction_id=direction_id_for(seed), ordinal=len(out), seed_key=seed.seed_key,
            seed_kind=seed.seed_kind, label=seed.label,
            member_codes=tuple(seed.member_codes), evidence=dict(seed.evidence),
        )
        if item.identity in seen:
            duplicate_count += 1
            continue
        seen.add(item.identity)
        out.append(item)
    return InventoryResult(tuple(out), duplicate_count)


__all__ = ["DirectionSeed", "InventoryResult", "build_inventory", "direction_id_for"]
