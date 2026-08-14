"""Validated configuration and deterministic covered deep-research queues."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .direction_brief import DirectionBrief


class DirectionPipelineConfigError(ValueError):
    """Raised instead of silently inventing a production budget or coverage policy."""


@dataclass(frozen=True)
class DirectionPipelineConfig:
    version: str
    deep_initial_limit: int
    triage_batch_size: int
    triage_concurrency: int
    deep_reason_batch_size: int
    fill_batch_size: int
    sufficient_candidate_count: int
    normal_before_reserve: bool
    coverage_industry_min: int
    coverage_seed_kind_min: int
    coverage_potential_czy_min: int
    selection_token_budget: int
    selection_wall_seconds: float
    max_total_deep: int
    max_fill_rounds: int
    cross_seed_merge_policy: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DirectionPipelineConfig":
        required = tuple(cls.__dataclass_fields__)
        missing = [key for key in required if key not in raw]
        if missing:
            raise DirectionPipelineConfigError("direction_pipeline missing required fields: " + ", ".join(missing))
        try:
            value = cls(**{key: raw[key] for key in required})
        except (TypeError, ValueError) as exc:
            raise DirectionPipelineConfigError("invalid direction_pipeline configuration") from exc
        if not isinstance(value.version, str) or not value.version.strip():
            raise DirectionPipelineConfigError("direction_pipeline version is required")
        if value.deep_initial_limit != 20:
            raise DirectionPipelineConfigError("deep_initial_limit must be the confirmed value 20")
        if value.cross_seed_merge_policy != "identity_only":
            raise DirectionPipelineConfigError("only identity_only cross_seed_merge_policy is confirmed")
        int_fields = (
            "triage_batch_size", "triage_concurrency", "deep_reason_batch_size", "fill_batch_size",
            "sufficient_candidate_count", "coverage_industry_min", "coverage_seed_kind_min",
            "coverage_potential_czy_min", "selection_token_budget", "max_total_deep", "max_fill_rounds",
        )
        if any(not isinstance(getattr(value, key), int) or isinstance(getattr(value, key), bool)
               or getattr(value, key) < 0 for key in int_fields):
            raise DirectionPipelineConfigError("direction_pipeline integer limits must be non-negative integers")
        if any(getattr(value, key) == 0 for key in ("triage_batch_size", "triage_concurrency", "deep_reason_batch_size", "fill_batch_size", "max_total_deep")):
            raise DirectionPipelineConfigError("direction_pipeline batch and total limits must be positive")
        if not isinstance(value.normal_before_reserve, bool):
            raise DirectionPipelineConfigError("normal_before_reserve must be boolean")
        if not isinstance(value.selection_wall_seconds, (int, float)) or isinstance(value.selection_wall_seconds, bool) or value.selection_wall_seconds <= 0:
            raise DirectionPipelineConfigError("selection_wall_seconds must be positive")
        return value


@dataclass(frozen=True)
class QueueEntry:
    direction_id: str
    disposition: str
    queue_round: int
    coverage_reason: str


@dataclass(frozen=True)
class DeepQueue:
    entries: Tuple[QueueEntry, ...]
    remaining_ids: Tuple[str, ...]

    @property
    def direction_ids(self) -> Tuple[str, ...]:
        return tuple(entry.direction_id for entry in self.entries)


def _category_values(brief: DirectionBrief) -> Tuple[Tuple[str, str], ...]:
    values: List[Tuple[str, str]] = [("seed_kind", brief.seed_kind)]
    if brief.industry:
        values.append(("industry", brief.industry))
    values.extend(("potential_czy", code) for code in brief.potential_czy)
    return tuple(values)


def build_deep_queue(
    briefs: Sequence[DirectionBrief], dispositions: Mapping[str, str], config: DirectionPipelineConfig,
    *, already_queued: Iterable[str] = (), queue_round: int = 0, limit: Optional[int] = None,
) -> DeepQueue:
    """Pick a deterministic, covered queue without treating missing data as a rejection.

    The first call is capped by the confirmed 20 initial slots.  Later fill calls
    supply an explicit limit (normally configured fill_batch_size).
    """
    used = set(already_queued)
    cap = config.deep_initial_limit if limit is None else limit
    cap = min(cap, config.max_total_deep - len(used))
    if cap <= 0:
        return DeepQueue((), tuple(item.direction_id for item in briefs if item.direction_id not in used))
    items = [item for item in briefs if item.direction_id not in used and dispositions.get(item.direction_id) in {"deep", "normal", "reserve"}]
    priority = {"deep": 0, "normal": 1 if config.normal_before_reserve else 2, "reserve": 2 if config.normal_before_reserve else 1}
    items.sort(key=lambda item: (priority[dispositions.get(item.direction_id, "reserve")], item.ordinal, item.direction_id))
    selected: List[QueueEntry] = []
    selected_ids = set()
    seen_categories: Dict[str, set] = {"industry": set(), "seed_kind": set(), "potential_czy": set()}
    minima = {
        "industry": config.coverage_industry_min,
        "seed_kind": config.coverage_seed_kind_min,
        "potential_czy": config.coverage_potential_czy_min,
    }
    # Coverage pass deliberately selects candidates that add a category until its configured target is met.
    for category in ("industry", "seed_kind", "potential_czy"):
        while len(selected) < cap and len(seen_categories[category]) < minima[category]:
            candidate = next((item for item in items if item.direction_id not in selected_ids and any(kind == category and value not in seen_categories[category] for kind, value in _category_values(item))), None)
            if candidate is None:
                break
            added = [value for kind, value in _category_values(candidate) if kind == category and value not in seen_categories[category]]
            selected.append(QueueEntry(candidate.direction_id, dispositions[candidate.direction_id], queue_round, f"coverage:{category}:{added[0]}"))
            selected_ids.add(candidate.direction_id)
            for kind, value in _category_values(candidate):
                seen_categories[kind].add(value)
    for item in items:
        if len(selected) >= cap:
            break
        if item.direction_id in selected_ids:
            continue
        selected.append(QueueEntry(item.direction_id, dispositions[item.direction_id], queue_round, "fill:mechanical_order"))
        selected_ids.add(item.direction_id)
    remaining = tuple(item.direction_id for item in items if item.direction_id not in selected_ids)
    return DeepQueue(tuple(selected), remaining)


__all__ = ["DirectionPipelineConfig", "DirectionPipelineConfigError", "QueueEntry", "DeepQueue", "build_deep_queue"]
