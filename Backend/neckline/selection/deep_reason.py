"""Strict result contract for the sole full-reasoning call in V2.4.2."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class DeepReasonResult:
    direction_id: str
    name: str
    driver: str
    driver_kind: str
    why_now: str
    seed_keys: Tuple[str, ...]
    narrative: str
    members: Tuple[Mapping[str, Any], ...]
    engine_claim: Optional[str]
    gate_evidence: Mapping[str, Any]
    price_plan_candidates: Mapping[str, Any]
    risks: Tuple[str, ...]
    raw: Mapping[str, Any]

    def to_legacy_proposal(self) -> Mapping[str, Any]:
        """Mechanical adapter for existing whitelist/clamp/gate code.

        It only projects the one deep-reason response; it must not call an LLM or
        synthesize missing assertions.  Existing gates remain the authority.
        """
        proposal = dict(self.raw)
        proposal.update({
            "name": self.name, "driver": self.driver, "driver_kind": self.driver_kind,
            "why_now": self.why_now, "seed_keys": list(self.seed_keys),
            "members": [dict(member) for member in self.members],
        })
        return proposal


def parse_deep_reason(raw: Any, *, direction_id: str) -> DeepReasonResult:
    """Parse one deep-reason response; callers apply whitelist/gates afterwards."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, Mapping):
        raise ValueError("deep reason response must be an object")
    narrative = raw.get("narrative")
    members = raw.get("members")
    required_text = ("name", "driver", "driver_kind", "why_now")
    if (not isinstance(narrative, str) or not narrative.strip() or not isinstance(members, list)
            or any(not isinstance(raw.get(key), str) or not raw[key].strip() for key in required_text)
            or not isinstance(raw.get("seed_keys"), list) or not raw["seed_keys"]):
        raise ValueError("deep reason missing required card material")
    return DeepReasonResult(
        direction_id=direction_id,
        name=raw["name"].strip(), driver=raw["driver"].strip(),
        driver_kind=raw["driver_kind"].strip(), why_now=raw["why_now"].strip(),
        seed_keys=tuple(str(item) for item in raw["seed_keys"] if str(item).strip()),
        narrative=narrative.strip(),
        members=tuple(item for item in members if isinstance(item, Mapping)),
        engine_claim=raw.get("engineClaim") if isinstance(raw.get("engineClaim"), str) else None,
        gate_evidence=raw.get("gateEvidence") if isinstance(raw.get("gateEvidence"), Mapping) else {},
        price_plan_candidates=raw.get("pricePlanCandidates") if isinstance(raw.get("pricePlanCandidates"), Mapping) else {},
        risks=tuple(str(item) for item in raw.get("risks", ()) if isinstance(item, (str, int, float))),
        raw=dict(raw),
    )


__all__ = ["DeepReasonResult", "parse_deep_reason"]
