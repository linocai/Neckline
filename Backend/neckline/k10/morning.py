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
    summary: str
    requires_review: bool

    @property
    def automatic_debate_started(self) -> bool:
        """New information never bypasses the user's explicit deep-observation action."""
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "cutoffAt": self.cutoff_at,
            "candidateId": self.candidate_id,
            "observationId": self.observation_id,
            "reasonStatus": self.reason_status,
            "sourceStatus": self.source_status,
            "observationStatus": self.observation_status,
            "materialContraryEvidence": [dict(item) for item in self.material_contrary_evidence],
            "sourceRefs": [dict(item) for item in self.source_refs],
            "summary": self.summary,
            "requiresReview": self.requires_review,
            "automaticDebateStarted": False,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _refs(value: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    if not value:
        raise MorningUpdateError("晨间变化必须带 sourceRefs，资料缺失也要有范围/失败引用")
    refs: list[Mapping[str, Any]] = []
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
        refs.append(dict(ref))
    return tuple(refs)


def build_morning_update(
    *, cutoff_at: str, candidate_id: str, observation_id: str | None,
    reason_status: str, source_status: str, observation_status: str,
    material_contrary_evidence: Sequence[Mapping[str, Any]], source_refs: Sequence[Mapping[str, Any]],
    summary: str,
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
    return MorningUpdate(
        cutoff_at=cutoff_at.strip(), candidate_id=candidate_id, observation_id=observation_id,
        reason_status=reason_status, source_status=source_status, observation_status=observation_status,
        material_contrary_evidence=contrary, source_refs=_refs(source_refs),
        summary=summary.strip(), requires_review=requires_review,
    )


def record_morning_update(
    *, repository: MorningRepository, db_path: Path, update: MorningUpdate, opportunity_id: str,
    created_at: str | None = None, occurred_at: str | None = None, update_id: str | None = None,
) -> str:
    """Append a lifecycle event; withdrawal never alters the fixed D1/D2 window."""
    identifier = update_id or str(uuid4())
    if not isinstance(opportunity_id, str) or not opportunity_id:
        raise MorningUpdateError("晨间更新缺少正式机会")
    kind = "withdrawal" if update.reason_status == "invalidated" else ("risk" if update.requires_review else "evidence_update")
    repository.append_opportunity_update(
        lifecycle_event_id=identifier, opportunity_id=opportunity_id, kind=kind, reason=update.summary,
        source_refs=update.source_refs, content=update.to_dict(), occurred_at=occurred_at or _utc_now(),
        created_at=created_at or _utc_now(), db_path=db_path,
    )
    return identifier


__all__ = [
    "MorningRepository", "MorningUpdate", "MorningUpdateError", "OBSERVATION_STATUSES", "REASON_STATUSES",
    "SOURCE_STATUSES", "build_morning_update", "record_morning_update",
]
