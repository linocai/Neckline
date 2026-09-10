"""K10 09:00 固定截止的晨间变化记录。

晨报更新资料和风险状态，绝不覆盖既有计划、替换用户名单或为未选候选自动启动正反分析。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

REASON_STATUSES = frozenset({"current", "needs_review", "invalidated", "unavailable"})
SOURCE_STATUSES = frozenset({"complete", "partial", "unavailable"})
OBSERVATION_STATUSES = frozenset({"current", "needs_review", "unavailable", "expired"})


class MorningUpdateError(ValueError):
    pass


MORNING_SECTIONS = (
    "major_contrary", "thesis_changed", "continuing_or_expiring", "new", "needs_review",
)


class MorningReportError(ValueError):
    """A report item cannot honestly be placed in one of the five fixed sections."""


@dataclass(frozen=True)
class MorningReportItem:
    """The normalized, immutable item passed from review workers to the scan orchestrator.

    ``content`` deliberately keeps the complete update payload.  The store owns persistence;
    this module owns the product classification rules so a partial review cannot look like a
    verified continuation.
    """

    item_id: str
    opportunity_id: str
    company_window_id: str
    display_rank: int
    selection_state: str
    lifecycle: str
    section: str
    priority: str
    summary: str
    coverage: Mapping[str, Any]
    source_refs: tuple[Mapping[str, Any], ...]
    independent_verification_refs: tuple[Mapping[str, Any], ...]
    lifecycle_event_id: str | None
    content: Mapping[str, Any]

    def to_store_item(self) -> dict[str, Any]:
        return {
            "itemId": self.item_id,
            "opportunityId": self.opportunity_id,
            "companyWindowId": self.company_window_id,
            "status": "completed",
            "content": {
                "displayRank": self.display_rank,
                "selectionState": self.selection_state,
                "lifecycle": self.lifecycle,
                "section": self.section,
                "priority": self.priority,
                "summary": self.summary,
                "coverage": dict(self.coverage),
                "sourceRefs": [dict(ref) for ref in self.source_refs],
                "independentVerificationRefs": [dict(ref) for ref in self.independent_verification_refs],
                "lifecycleEventId": self.lifecycle_event_id,
                **dict(self.content),
            },
        }


def _versioned_refs(value: Sequence[Mapping[str, Any]], *, field: str, required: bool) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or (required and not value):
        raise MorningReportError(f"{field} 必须是非空资料版本列表")
    result: list[Mapping[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("documentId"), str) or not raw["documentId"]:
            raise MorningReportError(f"{field} 必须含 documentId")
        revision = raw.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise MorningReportError(f"{field} 必须含精确 revision")
        key = (raw["documentId"], revision)
        if key in seen:
            raise MorningReportError(f"{field} 不可重复")
        seen.add(key)
        result.append(dict(raw))
    return tuple(result)


def morning_section(*, lifecycle: str, reason_status: str, source_status: str,
                    material: bool, is_new: bool, task_status: str = "completed") -> str:
    """Classify exactly once; incomplete coverage is never described as no change."""
    if task_status != "completed" or source_status != "complete":
        return "needs_review"
    if lifecycle == "withdrawn" or reason_status == "invalidated":
        return "major_contrary"
    if reason_status in {"needs_review", "unavailable"}:
        return "needs_review"
    if material:
        return "thesis_changed"
    if is_new:
        return "new"
    return "continuing_or_expiring"


def build_morning_report_item(
    *, item_id: str, opportunity_id: str, company_window_id: str, display_rank: int,
    selection_state: str, lifecycle: str, source_status: str, reason_status: str,
    material: bool, is_new: bool, summary: str, coverage: Mapping[str, Any],
    source_refs: Sequence[Mapping[str, Any]], independent_verification_refs: Sequence[Mapping[str, Any]],
    lifecycle_event_id: str | None = None, task_status: str = "completed",
    content: Mapping[str, Any] | None = None,
) -> MorningReportItem:
    if not all(isinstance(value, str) and value for value in (item_id, opportunity_id, company_window_id, selection_state, lifecycle)):
        raise MorningReportError("晨报项目缺少正式机会、窗口或状态")
    if isinstance(display_rank, bool) or not isinstance(display_rank, int) or display_rank < 1:
        raise MorningReportError("晨报项目缺少冻结 displayRank")
    if not isinstance(summary, str) or not summary.strip() or not isinstance(coverage, Mapping):
        raise MorningReportError("晨报项目缺少 summary 或 coverage")
    if task_status not in {"completed", "failed", "not_configured"}:
        raise MorningReportError("晨报项目任务状态无效")
    refs = _versioned_refs(source_refs, field="sourceRefs", required=True)
    independent = _versioned_refs(independent_verification_refs, field="independentVerificationRefs", required=False)
    section = morning_section(lifecycle=lifecycle, reason_status=reason_status, source_status=source_status,
                              material=material, is_new=is_new, task_status=task_status)
    # An invalidated conclusion needs independent frozen support.  A material item still marked
    # ``needs_review`` is an honest risk alert, not an already verified withdrawal, so it must
    # remain visible even before independent confirmation arrives.
    if reason_status == "invalidated" and lifecycle != "withdrawn" and not independent:
        raise MorningReportError("已核撤回必须带独立核验资料版本")
    priority = "high" if section == "major_contrary" else ("review" if section == "needs_review" else "normal")
    return MorningReportItem(
        item_id=item_id, opportunity_id=opportunity_id, company_window_id=company_window_id,
        display_rank=display_rank, selection_state=selection_state, lifecycle=lifecycle,
        section=section, priority=priority, summary=summary.strip(), coverage=dict(coverage),
        source_refs=refs, independent_verification_refs=independent,
        lifecycle_event_id=lifecycle_event_id, content=dict(content or {}),
    )


def group_morning_report_items(items: Sequence[MorningReportItem]) -> dict[str, list[dict[str, Any]]]:
    """Return every mandated section, deterministically ordered for immutable persistence."""
    groups: dict[str, list[MorningReportItem]] = {section: [] for section in MORNING_SECTIONS}
    for item in items:
        if not isinstance(item, MorningReportItem):
            raise MorningReportError("晨报只能收录标准项目")
        groups[item.section].append(item)
    return {
        section: [item.to_store_item() for item in sorted(groups[section], key=lambda item: (item.display_rank, item.item_id))]
        for section in MORNING_SECTIONS
    }


class MorningRepository(Protocol):
    def append_opportunity_update(
        self, *, lifecycle_event_id: str, opportunity_id: str, kind: str, reason: str | None,
        source_refs: Sequence[Mapping[str, Any]], content: Mapping[str, Any], occurred_at: str,
        created_at: str, db_path: Path,
    ) -> None: ...


@dataclass(frozen=True)
class MorningUpdate:
    cutoff_at: str
    candidate_id: str
    observation_id: str | None
    reason_status: str
    source_status: str
    observation_status: str
    material_contrary_evidence: tuple[Mapping[str, Any], ...]
    source_refs: tuple[Mapping[str, Any], ...]
    independent_verification_refs: tuple[Mapping[str, Any], ...]
    summary: str
    requires_review: bool
    evidence_disclosure: Mapping[str, Any] | None = None

    @property
    def automatic_debate_started(self) -> bool:
        """New information never bypasses the user's explicit deep-observation action."""
        return False

    def to_dict(self) -> dict[str, Any]:
        result = {
            "cutoffAt": self.cutoff_at,
            "candidateId": self.candidate_id,
            "observationId": self.observation_id,
            "reasonStatus": self.reason_status,
            "sourceStatus": self.source_status,
            "observationStatus": self.observation_status,
            "materialContraryEvidence": [dict(item) for item in self.material_contrary_evidence],
            "sourceRefs": [dict(item) for item in self.source_refs],
            "independentVerificationRefs": [dict(item) for item in self.independent_verification_refs],
            "summary": self.summary,
            "requiresReview": self.requires_review,
            "automaticDebateStarted": False,
        }
        if self.evidence_disclosure is not None:
            result["evidenceDisclosure"] = dict(self.evidence_disclosure)
        return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _refs(value: Sequence[Mapping[str, Any]], *, required: bool = True) -> tuple[Mapping[str, Any], ...]:
    if required and not value:
        raise MorningUpdateError("晨间变化必须带 sourceRefs，资料缺失也要有范围/失败引用")
    refs: list[Mapping[str, Any]] = []
    seen: set[tuple[str, int] | tuple[str, str]] = set()
    for ref in value:
        if not isinstance(ref, Mapping):
            raise MorningUpdateError("sourceRefs 每项必须是对象")
        document = ref.get("documentId")
        revision = ref.get("revision")
        url = ref.get("url") or ref.get("canonicalUrl")
        has_document_version = isinstance(document, str) and bool(document) and isinstance(revision, int) and revision >= 1
        has_url_time = isinstance(url, str) and bool(url) and (
            isinstance(ref.get("publishedAt"), str) or isinstance(ref.get("fetchedAt"), str)
        )
        if not (has_document_version or has_url_time):
            raise MorningUpdateError("sourceRefs 必须含 documentId+revision，或 URL 加发布时间/取得时间")
        key: tuple[str, int] | tuple[str, str]
        if has_document_version:
            key = (document, revision)
        else:
            key = ("url", str(url))
        if key in seen:
            continue
        seen.add(key)
        refs.append(dict(ref))
    return tuple(refs)


def build_morning_update(
    *, cutoff_at: str, candidate_id: str, observation_id: str | None,
    reason_status: str, source_status: str, observation_status: str,
    material_contrary_evidence: Sequence[Mapping[str, Any]], source_refs: Sequence[Mapping[str, Any]],
    summary: str, evidence_disclosure: Mapping[str, Any] | None = None,
    independent_verification_refs: Sequence[Mapping[str, Any]] = (),
) -> MorningUpdate:
    """Create one immutable morning record for either an observed or unselected candidate."""
    if not isinstance(cutoff_at, str) or not cutoff_at.strip():
        raise MorningUpdateError("cutoffAt 不能为空")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise MorningUpdateError("candidateId 不能为空")
    if observation_id is not None and (not isinstance(observation_id, str) or not observation_id):
        raise MorningUpdateError("observationId 必须是非空字符串或 null")
    if reason_status not in REASON_STATUSES:
        raise MorningUpdateError("未知 reasonStatus")
    if source_status not in SOURCE_STATUSES:
        raise MorningUpdateError("未知 sourceStatus")
    if observation_status not in OBSERVATION_STATUSES:
        raise MorningUpdateError("未知 observationStatus")
    if not isinstance(summary, str) or not summary.strip():
        raise MorningUpdateError("summary 不能为空")
    contrary = tuple(dict(item) for item in material_contrary_evidence)
    if any(not isinstance(item, Mapping) for item in material_contrary_evidence):
        raise MorningUpdateError("materialContraryEvidence 每项必须是对象")
    # A material contrary item cannot be silently shown as current. This is a state rule,
    # not a strategy threshold: facts have priority in the morning report.
    if contrary and reason_status == "current":
        raise MorningUpdateError("存在重大反证时 reasonStatus 不能是 current")
    if reason_status == "invalidated" and not contrary:
        raise MorningUpdateError("理由失效必须列出已核重大反证")
    requires_review = bool(contrary) or reason_status in {"needs_review", "invalidated"} or source_status != "complete" or observation_status != "current"
    # The lifecycle ledger must retain both the ordinary morning material and the independent
    # material supporting a later withdrawal.  The first is still useful context, but only the
    # explicitly named second set may be carried forward as independent verification.
    independent = _refs(independent_verification_refs, required=False)
    all_refs = _refs([*source_refs, *independent], required=True)
    if evidence_disclosure is not None:
        # Keep this immutable user-facing projection valid without coupling the
        # morning product model to research persistence internals.
        from .opportunity_discovery import ComparisonValidationError, validate_evidence_disclosure
        try:
            validate_evidence_disclosure(evidence_disclosure)
        except ComparisonValidationError as exc:
            raise MorningUpdateError("晨间更新的冻结证据披露无效") from exc
    return MorningUpdate(
        cutoff_at=cutoff_at.strip(), candidate_id=candidate_id, observation_id=observation_id,
        reason_status=reason_status, source_status=source_status, observation_status=observation_status,
        material_contrary_evidence=contrary, source_refs=all_refs,
        independent_verification_refs=independent,
        summary=summary.strip(), requires_review=requires_review,
        evidence_disclosure=None if evidence_disclosure is None else dict(evidence_disclosure),
    )


def record_morning_update(
    *, repository: MorningRepository, db_path: Path, update: MorningUpdate, opportunity_id: str,
    created_at: str | None = None, occurred_at: str | None = None, update_id: str | None = None, scan_id: str | None = None, material: bool = False,
) -> str:
    """Append a lifecycle event; withdrawal never alters the fixed D1/D2 window."""
    identifier = update_id or str(uuid4())
    if not isinstance(opportunity_id, str) or not opportunity_id:
        raise MorningUpdateError("晨间更新缺少正式机会")
    kind = "withdrawal" if update.reason_status == "invalidated" else ("risk" if update.requires_review else "evidence_update")
    repository.append_opportunity_update(
        lifecycle_event_id=identifier, opportunity_id=opportunity_id, kind=kind, reason=update.summary,
        source_refs=update.source_refs, content={**update.to_dict(), "material": material, **({"scanId":scan_id} if scan_id else {})}, occurred_at=occurred_at or _utc_now(),
        created_at=created_at or _utc_now(), db_path=db_path,
    )
    return identifier


__all__ = [
    "MORNING_SECTIONS", "MorningReportError", "MorningReportItem", "MorningRepository", "MorningUpdate",
    "MorningUpdateError", "OBSERVATION_STATUSES", "REASON_STATUSES", "SOURCE_STATUSES",
    "build_morning_report_item", "build_morning_update", "group_morning_report_items", "morning_section",
    "record_morning_update",
]
