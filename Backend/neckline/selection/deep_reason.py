"""Strict result contract for the sole full-reasoning call in V2.4.2."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple


DEEP_DECISIONS = frozenset({"candidate", "not_candidate", "uncertain"})
DRIVER_KINDS = frozenset({"theme", "policy", "event", "commodity", "overseas", "rotation", "limit_cluster"})
MEMBER_ROLES = frozenset({"leader", "core", "elastic"})
CHECK_VERDICTS = frozenset({"ok", "weak", "unfit", "unknown"})
PRIMARY_CLAIMS = frozenset({"yes", "no", "unsure"})


@dataclass(frozen=True)
class DeepReasonResult:
    direction_id: str
    decision: str
    decision_reason: str
    name: str = ""
    driver: str = ""
    driver_kind: str = ""
    why_now: str = ""
    seed_keys: Tuple[str, ...] = ()
    narrative: str = ""
    members: Tuple[Mapping[str, Any], ...] = ()
    engine_claim: Optional[str] = None
    gate_evidence: Mapping[str, Any] = field(default_factory=dict)
    price_plan_candidates: Mapping[str, Any] = field(default_factory=dict)
    card_material: Mapping[str, Any] = field(default_factory=dict)
    risks: Tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_candidate(self) -> bool:
        return self.decision == "candidate"

    def to_legacy_proposal(self) -> Mapping[str, Any]:
        """Mechanical adapter for existing whitelist/clamp/gate code.

        It only projects the one deep-reason response; it must not call an LLM or
        synthesize missing assertions.  Existing gates remain the authority.
        """
        if not self.is_candidate:
            raise ValueError("only candidate decisions can become proposals")
        proposal = dict(self.raw or {})
        proposal.update({
            "directionId": self.direction_id,
            "name": self.name, "driver": self.driver, "driver_kind": self.driver_kind,
            "why_now": self.why_now, "seed_keys": list(self.seed_keys),
            "members": [dict(member) for member in self.members],
        })
        return proposal


def _require_check(raw: Any, *, field: str) -> None:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{field} must be an object")
    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in CHECK_VERDICTS:
        raise ValueError(f"{field}.verdict is invalid")
    if not isinstance(raw.get("support"), list) or not isinstance(raw.get("counter_evidence"), list) or not isinstance(raw.get("missing"), list):
        raise ValueError(f"{field} evidence fields must be arrays")
    if not isinstance(raw.get("reason"), str) or not raw["reason"].strip():
        raise ValueError(f"{field}.reason must be text")


def validate_card_material(raw: Any, *, member_codes: Sequence[str]) -> Mapping[str, Any]:
    """Validate the human-card payload emitted by the sole deep-reason call.

    V2.4.2 retired the second per-card LLM call, so this object is no longer an
    optional decoration: it is the only semantic source for the three price
    references and the human-readable verification text.  Missing or empty
    material is therefore a provider contract failure, never ``llmStage=ok``.
    Mechanical limit/close clamps remain authoritative after this structural
    validation.
    """
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError("card_material must be a non-empty object")
    for key in ("upside_path", "verification", "invalidation"):
        if not isinstance(raw.get(key), str) or not str(raw[key]).strip():
            raise ValueError(f"card_material.{key} must be non-empty text")
    risks = raw.get("risks")
    if not isinstance(risks, list) or not risks or any(
        not isinstance(item, str) or not item.strip() for item in risks
    ):
        raise ValueError("card_material.risks must contain non-empty text")
    tier_note = raw.get("tier_note")
    if tier_note is not None and (not isinstance(tier_note, str) or not tier_note.strip()):
        raise ValueError("card_material.tier_note must be text or null")

    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("card_material.entries must be a non-empty array")
    expected = {str(code).strip() for code in member_codes if str(code).strip()}
    seen = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("card_material.entries items must be objects")
        code = str(entry.get("ts_code") or "").strip()
        if not code or code not in expected or code in seen:
            raise ValueError("card_material.entries must exactly cover candidate members")
        seen.add(code)
        values = []
        for key in ("low", "high", "max_chase", "exit_low", "exit_high"):
            value = entry.get(key)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(float(value)) or float(value) <= 0):
                raise ValueError(f"card_material.entries[{code}].{key} must be a positive number")
            values.append(float(value))
        low, high, chase, exit_low, exit_high = values
        if not low <= high <= chase or not exit_low <= exit_high:
            raise ValueError(f"card_material.entries[{code}] price ordering is invalid")
        if not isinstance(entry.get("why"), str) or not entry["why"].strip():
            raise ValueError(f"card_material.entries[{code}].why must be non-empty text")
    if seen != expected:
        raise ValueError("card_material.entries must exactly cover candidate members")
    return dict(raw)


def parse_deep_reason(
    raw: Any, *, direction_id: str, seed_key: str,
    allowed_member_codes: Sequence[str],
) -> DeepReasonResult:
    """Validate one deep decision and bind mechanical identity server-side.

    The model decides whether a direction merits a basket and supplies the
    qualitative fields.  It never owns the seed identity or member whitelist.
    Contract violations are system failures, not investment rejections.
    """
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, Mapping):
        raise ValueError("deep reason response must be an object")
    response_id = str(raw.get("directionId", raw.get("direction_id", ""))).strip()
    if response_id != direction_id:
        raise ValueError("directionId does not match the requested direction")
    decision = str(raw.get("decision") or "").strip().lower()
    decision_reason = str(raw.get("decisionReason", raw.get("decision_reason", ""))).strip()
    if decision not in DEEP_DECISIONS or not decision_reason:
        raise ValueError("deep reason decision contract is missing or invalid")
    if decision != "candidate":
        return DeepReasonResult(
            direction_id=direction_id, decision=decision,
            decision_reason=decision_reason, seed_keys=(seed_key,), raw=dict(raw),
        )

    narrative = raw.get("narrative")
    members = raw.get("members")
    required_text = ("name", "driver", "driver_kind", "why_now")
    if (not isinstance(narrative, str) or not narrative.strip() or not isinstance(members, list)
            or any(not isinstance(raw.get(key), str) or not raw[key].strip() for key in required_text)):
        raise ValueError("deep reason missing required card material")
    for field_name in ("common_trait", "persistence", "strengthen_and_invalidate"):
        if not isinstance(raw.get(field_name), str) or not raw[field_name].strip():
            raise ValueError(f"{field_name} must be non-empty text")
    if not isinstance(raw.get("evidence_conflicts"), str):
        raise ValueError("evidence_conflicts must be text")
    driver_kind = str(raw["driver_kind"]).strip().lower()
    if driver_kind not in DRIVER_KINDS:
        raise ValueError("driver_kind is invalid")
    if not (1 <= len(members) <= 3) or not all(isinstance(item, Mapping) for item in members):
        raise ValueError("candidate members must contain 1 to 3 objects")
    allowed = {str(code).strip() for code in allowed_member_codes if str(code).strip()}
    seen = set()
    for member in members:
        code = str(member.get("ts_code") or "").strip()
        role = str(member.get("role") or "").strip().lower()
        if not code or code not in allowed:
            raise ValueError(f"member {code or '<missing>'} is outside the mechanical whitelist")
        if code in seen:
            raise ValueError(f"member {code} is duplicated")
        seen.add(code)
        if role not in MEMBER_ROLES or not isinstance(member.get("reason"), str) or not member["reason"].strip():
            raise ValueError(f"member {code} role/reason is invalid")
        claim = str(member.get("primary_claim") or "").strip().lower()
        if (claim not in PRIMARY_CLAIMS
                or not isinstance(member.get("primary_claim_reason"), str)
                or not member["primary_claim_reason"].strip()):
            raise ValueError(f"member {code} primary claim is invalid")
        _require_check(member.get("position_check"), field=f"member {code} position_check")
        _require_check(member.get("core_check"), field=f"member {code} core_check")
    engine_code = str(raw.get("engine_code") or "").strip().upper()
    if engine_code not in {"C", "Z", "Y"}:
        raise ValueError("engine_code is invalid")
    _require_check(raw.get("market_check"), field="market_check")
    _require_check(raw.get("sector_check"), field="sector_check")
    card_material = validate_card_material(
        raw.get("card_material", raw.get("cardMaterial")),
        member_codes=[str(member.get("ts_code") or "") for member in members],
    )
    return DeepReasonResult(
        direction_id=direction_id, decision=decision, decision_reason=decision_reason,
        name=raw["name"].strip(), driver=raw["driver"].strip(),
        driver_kind=driver_kind, why_now=raw["why_now"].strip(),
        seed_keys=(seed_key,),
        narrative=narrative.strip(),
        members=tuple(item for item in members if isinstance(item, Mapping)),
        engine_claim=raw.get("engineClaim") if isinstance(raw.get("engineClaim"), str) else None,
        gate_evidence=raw.get("gateEvidence") if isinstance(raw.get("gateEvidence"), Mapping) else {},
        price_plan_candidates=raw.get("pricePlanCandidates") if isinstance(raw.get("pricePlanCandidates"), Mapping) else {},
        card_material=card_material,
        risks=tuple(str(item) for item in raw.get("risks", ()) if isinstance(item, (str, int, float))),
        raw=dict(raw),
    )


__all__ = ["DEEP_DECISIONS", "DeepReasonResult", "parse_deep_reason", "validate_card_material"]
