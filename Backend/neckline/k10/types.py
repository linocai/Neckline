"""K10 存储层使用的不可变领域返回值。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class DocumentVersion:
    document_id: str
    revision: int
    content_hash: str


@dataclass(frozen=True)
class EventRevision:
    event_id: str
    revision: int


@dataclass(frozen=True)
class Observation:
    observation_id: str
    candidate_id: str
    created: bool


@dataclass(frozen=True)
class CompanyWindowObservation:
    """One company-window action and its shared analysis chain."""
    company_window_id: str
    action_id: str
    candidate_id: str
    observation_id: Optional[str]
    task_id: Optional[str]
    created: bool
    replayed: bool


@dataclass(frozen=True)
class Task:
    task_id: str
    kind: str
    status: str
    attempt_count: int
    lease_owner: Optional[str]
    lease_until: Optional[str]
    payload: Mapping[str, Any]

@dataclass(frozen=True)
class OpportunityPublicationInput:
    """One complete candidate snapshot included in an atomically visible batch."""
    candidate_id: str
    company_code: str
    event_id: str
    event_revision: int
    opportunity_key: str
    catalyst_stage: str
    category: str
    comparison: Mapping[str, Any]
    evidence_refs: tuple[Mapping[str, Any], ...]
    source_marker: str
    related_opportunity_id: Optional[str] = None


@dataclass(frozen=True)
class PublicationBatch:
    batch_id: str
    scan_id: str
    publication_kind: str
    available_at: str
    sample_count: int


@dataclass(frozen=True)
class CompanyWindow:
    company_window_id: str
    company_code: str
    first_batch_id: str
    d0_trade_date: str
    d1_trade_date: str
    d2_trade_date: str
    d1_selection_at: str
    d2_close_at: str
    sample_class: str
    overlaps_window_id: Optional[str]


@dataclass(frozen=True)
class Opportunity:
    opportunity_id: str
    opportunity_key: str
    company_code: str
    catalyst_event_id: str
    catalyst_event_revision: int
    catalyst_stage: str
    first_batch_id: str
    company_window_id: str
