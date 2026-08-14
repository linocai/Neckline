"""The intentionally narrow V2.4.2 direction merge boundary."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence, Tuple

from .direction_brief import DirectionBrief


@dataclass(frozen=True)
class MergeResult:
    directions: Tuple[DirectionBrief, ...]
    merged_count: int = 0
    policy: str = "identity_only"


def merge_directions(briefs: Sequence[DirectionBrief], *, policy: str = "identity_only") -> MergeResult:
    """Do not infer cross-seed equivalence before the user confirms a policy.

    Inventory already removes exact source duplicates.  This function makes that
    limitation explicit in the trace instead of silently performing fuzzy merges.
    """
    if policy != "identity_only":
        raise ValueError("cross-seed merge policy is not confirmed; only identity_only is allowed")
    return MergeResult(
        directions=tuple(replace(item, merge_status="merge_policy_unconfigured") for item in briefs),
        policy=policy,
    )


__all__ = ["MergeResult", "merge_directions"]
