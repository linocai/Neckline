"""K10 所选对象的一轮正反分析。

本模块不选择候选、不调用旧策略路由，也不补搜资料。调用方必须传入已经留下的
Observation 冻结上下文及显式 K10 provider；这样两个角色始终读取同一资料截止，
并且原始文本只作为不可信证据传递给模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

from neckline.llm.base import ChatMessage, LLMProvider, LLMResult

from .config import validate_run_config
from .opportunity_discovery import ComparisonValidationError, validate_evidence_disclosure
from .prompts import PROMPT_VERSION, con_messages, pro_messages


ANALYSIS_STATUSES = frozenset({"queued", "completed", "failed", "not_configured"})


class AnalysisInputError(ValueError):
    """The caller attempted to analyse something that is not a selected observation."""


class AnalysisRepository(Protocol):
    """The narrow append-only seam used by worker code and tests.

    Core owns the SQLite implementation. This protocol intentionally carries no model or
    strategy decision: it supplies an observation's frozen evidence and appends revisions.
    """

    def load_observation_context(
        self, *, observation_id: str, cutoff_at: str, db_path: Path
    ) -> Mapping[str, Any]: ...

    def append_analysis_revision(
        self, *, analysis_id: str, observation_id: str, revision: int, analysis_kind: str,
        input_cutoff_at: str, input_lineage: Mapping[str, Any], content: Mapping[str, Any],
        status: str, created_at: str, db_path: Path,
    ) -> None: ...


@dataclass(frozen=True)
class AnalysisArtifact:
    analysis_id: str
    observation_id: str
    revision: int
    role: str
    status: str
    input_cutoff_at: str
    source_refs: tuple[Mapping[str, Any], ...]
    input_lineage: Mapping[str, Any]
    full_text: str
    provider: str | None
    model: str | None
    prompt_version: str
    usage: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysisId": self.analysis_id,
            "observationId": self.observation_id,
            "revision": self.revision,
            "role": self.role,
            "status": self.status,
            "inputCutoffAt": self.input_cutoff_at,
            "sourceRefs": [dict(item) for item in self.source_refs],
            "inputLineage": dict(self.input_lineage),
            "fullText": self.full_text,
            "provider": self.provider,
            "model": self.model,
            "promptVersion": self.prompt_version,
            "usage": dict(self.usage),
            "error": self.error,
        }


@dataclass(frozen=True)
class DebateResult:
    pro: AnalysisArtifact
    con: AnalysisArtifact

    @property
    def status(self) -> str:
        if self.pro.status == "not_configured":
            return "not_configured"
        if self.pro.status == "completed" and self.con.status == "completed":
            return "completed"
        return "failed"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisInputError(f"{field} 不能为空")
    return value.strip()


def _source_refs(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise AnalysisInputError("分析必须带可追溯 sourceRefs")
    result: list[Mapping[str, Any]] = []
    for ref in value:
        if not isinstance(ref, Mapping):
            raise AnalysisInputError("sourceRefs 每项必须是对象")
        document_id = ref.get("documentId")
        revision = ref.get("revision")
        url = ref.get("url") or ref.get("canonicalUrl")
        published = ref.get("publishedAt")
        fetched = ref.get("fetchedAt")
        has_document_version = isinstance(document_id, str) and document_id and isinstance(revision, int) and revision >= 1
        has_url_timing = isinstance(url, str) and url and (
            isinstance(published, str) or isinstance(fetched, str) or isinstance(ref.get("collectedAt"), str)
        )
        if not (has_document_version or has_url_timing):
            raise AnalysisInputError("sourceRefs 必须含 documentId+revision，或 URL 加发布时间/取得时间")
        result.append(dict(ref))
    return tuple(result)


def _exact_refs(value: Any, *, field: str, required: bool) -> tuple[dict[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or (required and not value):
        raise AnalysisInputError(f"{field} 必须是资料版本列表")
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("documentId"), str) or not raw["documentId"]:
            raise AnalysisInputError(f"{field} 必须包含 documentId")
        revision = raw.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise AnalysisInputError(f"{field} 必须包含精确 revision")
        key = (raw["documentId"], revision)
        if key in seen:
            raise AnalysisInputError(f"{field} 不可重复")
        seen.add(key)
        refs.append(dict(raw))
    return tuple(refs)


def augment_analysis_context(
    *, context: Mapping[str, Any], request: Mapping[str, Any], request_documents: Sequence[Mapping[str, Any]],
    prior_analyses: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Attach one immutable follow-up request to an observation snapshot.

    Store retrieves the exact document versions and complete prior revision.  This pure adapter
    only joins those frozen inputs, so an added request cannot silently read a later document or
    an unrelated analysis revision.
    """
    request_id = _require_text(request.get("requestId") or request.get("analysisRequestId"), "requestId")
    kind = request.get("kind") or request.get("analysisRequestKind")
    if kind not in {"user_question", "evidence_update"}:
        raise AnalysisInputError("追加分析 kind 无效")
    global_revision = request.get("globalRevision")
    parent_revision = request.get("parentRevision")
    if isinstance(global_revision, bool) or not isinstance(global_revision, int) or global_revision < 2:
        raise AnalysisInputError("追加分析缺少 globalRevision")
    if (parent_revision is not None and (isinstance(parent_revision, bool) or not isinstance(parent_revision, int)
                                        or parent_revision < 1 or parent_revision >= global_revision)):
        raise AnalysisInputError("追加分析 parentRevision 无效")
    question = request.get("question")
    if kind == "user_question" and (not isinstance(question, str) or not question.strip()):
        raise AnalysisInputError("用户追问缺少 question")
    if question is not None and (not isinstance(question, str) or not question.strip()):
        raise AnalysisInputError("question 无效")
    added_refs = _exact_refs(
        request.get("sourceRefs") if "sourceRefs" in request else request.get("frozenEvidenceRefs"),
        field="addedEvidenceRefs", required=kind == "evidence_update",
    )
    documents = context.get("documents")
    if isinstance(documents, (str, bytes)) or not isinstance(documents, Sequence):
        raise AnalysisInputError("原分析资料快照无效")
    by_ref = {
        (item.get("documentId"), item.get("revision")): dict(item)
        for item in request_documents if isinstance(item, Mapping)
        and isinstance(item.get("documentId"), str) and isinstance(item.get("revision"), int)
    }
    if any((ref["documentId"], ref["revision"]) not in by_ref for ref in added_refs):
        raise AnalysisInputError("追加分析冻结资料不存在或版本不匹配")
    original_refs = context.get("frozenEvidenceRefs")
    if isinstance(original_refs, (str, bytes)) or not isinstance(original_refs, Sequence):
        raise AnalysisInputError("原分析缺少冻结资料引用")
    merged_refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for raw in [*original_refs, *added_refs]:
        if not isinstance(raw, Mapping):
            raise AnalysisInputError("冻结资料引用无效")
        ref = dict(raw)
        key = (ref.get("documentId"), ref.get("revision"))
        if not isinstance(key[0], str) or not isinstance(key[1], int):
            raise AnalysisInputError("冻结资料引用必须有精确版本")
        if key not in seen:
            seen.add(key); merged_refs.append(ref)
    merged_documents = [dict(item) for item in documents if isinstance(item, Mapping)]
    existing_documents = {(item.get("documentId"), item.get("revision")) for item in merged_documents}
    for ref in added_refs:
        key = (ref["documentId"], ref["revision"])
        if key not in existing_documents:
            merged_documents.append(by_ref[key])
    prior: list[dict[str, Any]] = []
    for raw in prior_analyses:
        if not isinstance(raw, Mapping):
            continue
        content = raw.get("content")
        # Store chain rows retain metadata beside the persisted artifact.  Prompts receive the
        # actual full artifact body, never just a summary/projection.
        item = {**dict(content), "analysisId": raw.get("analysisId"), "role": raw.get("role"),
                "status": raw.get("status"), "revision": raw.get("revision", request.get("parentRevision"))} \
            if isinstance(content, Mapping) else dict(raw)
        prior.append(item)
    if parent_revision is not None:
        roles = {item.get("role") for item in prior}
        if roles != {"pro", "con"} or any(item.get("revision") != parent_revision or not isinstance(item.get("fullText"), str) for item in prior):
            raise AnalysisInputError("追加分析缺少上一版完整正反全文")
    chain = {"requestId": request_id, "kind": kind, "parentRevision": parent_revision,
             "question": question.strip() if isinstance(question, str) else None,
             "addedEvidenceRefs": [dict(ref) for ref in added_refs]}
    return {**dict(context), "frozenEvidenceRefs": merged_refs, "documents": merged_documents,
            "analysisRequest": {**dict(request), "requestId": request_id, "kind": kind,
                                "globalRevision": global_revision, "parentRevision": parent_revision,
                                "question": chain["question"], "sourceRefs": [dict(ref) for ref in added_refs]},
            "priorAnalyses": prior, "analysisChain": chain}


def _core_context(context: Mapping[str, Any], *, cutoff_at: str) -> Mapping[str, Any]:
    """Adapt core's read-only snapshot to the worker's explicit prompt contract.

    This is intentionally an in-memory translation: it neither writes a snapshot nor searches
    for later evidence. ``load_observation_context`` already restricts document versions to the
    supplied cutoff.
    """
    candidate = context.get("candidate")
    event = context.get("event")
    mappings = context.get("mappings")
    documents = context.get("documents")
    if not isinstance(candidate, Mapping) or not isinstance(event, Mapping):
        return context
    disclosure = candidate.get("evidenceDisclosure")
    comparison = candidate.get("comparison")
    if disclosure is None and isinstance(comparison, Mapping):
        disclosure = comparison.get("evidenceDisclosure")
        if disclosure is None and isinstance(comparison.get("differences"), Mapping):
            disclosure = comparison["differences"].get("evidenceDisclosure")
    if disclosure is not None:
        if not isinstance(disclosure, Mapping):
            raise AnalysisInputError("冻结证据披露无效")
        try:
            validate_evidence_disclosure(disclosure)
        except ComparisonValidationError as exc:
            raise AnalysisInputError("冻结证据披露无效") from exc
        disclosure = dict(disclosure)
    if context.get("cutoffAt") != cutoff_at:
        raise AnalysisInputError("Observation 资料截止与任务截止不一致")
    if not isinstance(mappings, Sequence) or isinstance(mappings, (str, bytes)):
        raise AnalysisInputError("缺少公司映射快照")
    if not isinstance(documents, Sequence) or isinstance(documents, (str, bytes)):
        raise AnalysisInputError("缺少资料版本快照")
    versions = [
        {
            "documentId": item.get("documentId"), "revision": item.get("revision"),
            "contentSha256": item.get("contentSha256"), "publishedAt": item.get("publishedAt"),
            "publishedPrecision": item.get("publishedPrecision"), "fetchedAt": item.get("fetchedAt"),
            "fetchVersion": item.get("fetchVersion"),
        }
        for item in documents if isinstance(item, Mapping)
    ]
    document_by_ref = {(item["documentId"], item["revision"]): item for item in versions
                       if isinstance(item.get("documentId"), str) and isinstance(item.get("revision"), int)}
    document_by_id = {item["documentId"]: item for item in versions if isinstance(item.get("documentId"), str)}
    refs: list[dict[str, Any]] = []
    frozen_refs = context.get("frozenEvidenceRefs", event.get("sourceRefs", []))
    if not isinstance(frozen_refs, Sequence) or isinstance(frozen_refs, (str, bytes)):
        raise AnalysisInputError("缺少冻结证据引用")
    for raw_ref in frozen_refs:
        if not isinstance(raw_ref, Mapping):
            continue
        ref = dict(raw_ref)
        document = document_by_ref.get((ref.get("documentId"), ref.get("revision")))
        # Pre-v1.4 test/adapter contexts carried only an event document ID.  Current store
        # contexts always expose frozenEvidenceRefs with an exact revision, so this fallback
        # cannot select a later production document revision.
        if document is None and "frozenEvidenceRefs" not in context:
            document = document_by_id.get(ref.get("documentId"))
        if document is not None:
            for key in ("revision", "publishedAt", "publishedPrecision", "fetchedAt"):
                if ref.get(key) is None and document.get(key) is not None:
                    ref[key] = document[key]
        refs.append(ref)
    evidence: list[Mapping[str, Any]] = [
        {"kind": "event", "event": dict(event)},
        {"kind": "candidate", "candidate": dict(candidate)},
        *({"kind": "mapping", "mapping": dict(item)} for item in mappings if isinstance(item, Mapping)),
        *({"kind": "document", "document": dict(item)} for item in documents if isinstance(item, Mapping)),
    ]
    if isinstance(disclosure, Mapping):
        evidence.append({"kind": "evidence_disclosure", "evidenceDisclosure": dict(disclosure)})
    market = context.get("marketContext")
    if isinstance(market, Mapping):
        market_copy = dict(market)
        market_refs = market_copy.get("sourceRefs")
        if isinstance(market_refs, Sequence) and not isinstance(market_refs, (str, bytes)):
            refs.extend(dict(item) for item in market_refs if isinstance(item, Mapping))
        evidence.append({"kind": "market", "marketContext": market_copy})
    publication = context.get("publicationContext")
    if isinstance(publication, Mapping):
        publication_copy = dict(publication)
        evidence.append({"kind": "company_window", "publicationContext": publication_copy})
    request_chain = context.get("analysisChain")
    prior_analyses = context.get("priorAnalyses")
    if request_chain is not None:
        if not isinstance(request_chain, Mapping) or not isinstance(prior_analyses, Sequence) or isinstance(prior_analyses, (str, bytes)):
            raise AnalysisInputError("追加分析谱系无效")
        evidence.extend({"kind": "previous_analysis", "analysis": dict(item)} for item in prior_analyses if isinstance(item, Mapping))
    lineage = {
        "candidateId": candidate.get("candidateId"),
        "event": {"eventId": event.get("eventId"), "revision": event.get("revision")},
        "mappingIds": [item.get("mappingId") for item in mappings if isinstance(item, Mapping)],
        "documentVersions": versions,
        "frozenEvidenceRefs": [dict(item) for item in frozen_refs if isinstance(item, Mapping)],
        "inputCutoffAt": cutoff_at,
        **({"evidenceDisclosure": dict(disclosure)} if isinstance(disclosure, Mapping) else {}),
        **({"marketContext": market_copy} if isinstance(market, Mapping) else {}),
        **({"publicationContext": publication_copy} if isinstance(publication, Mapping) else {}),
        **({"chain": dict(request_chain)} if isinstance(request_chain, Mapping) else {}),
    }
    return {
        "isObserved": True,
        "observationId": context.get("observationId"),
        "companyCandidateId": candidate.get("candidateId"),
        "inputCutoffAt": cutoff_at,
        "sourceRefs": refs,
        "inputLineage": lineage,
        "evidence": evidence,
        "userConstraints": {},
    }


def _context(context: Mapping[str, Any], *, cutoff_at: str) -> tuple[str, str, tuple[Mapping[str, Any], ...], Mapping[str, Any], Sequence[Mapping[str, Any]], Mapping[str, Any]]:
    context = _core_context(context, cutoff_at=cutoff_at)
    if context.get("isObserved") is not True:
        raise AnalysisInputError("只能对用户已留下的 Observation 运行正反分析")
    observation_id = _require_text(context.get("observationId"), "observationId")
    candidate_id = _require_text(context.get("companyCandidateId"), "companyCandidateId")
    context_cutoff = _require_text(context.get("inputCutoffAt"), "inputCutoffAt")
    if context_cutoff != cutoff_at:
        raise AnalysisInputError("Observation 资料截止与任务截止不一致")
    refs = _source_refs(context.get("sourceRefs"))
    lineage = context.get("inputLineage")
    evidence = context.get("evidence")
    constraints = context.get("userConstraints", {})
    if not isinstance(lineage, Mapping):
        raise AnalysisInputError("缺少冻结输入谱系")
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
        raise AnalysisInputError("evidence 必须是资料列表")
    if not isinstance(constraints, Mapping):
        raise AnalysisInputError("userConstraints 必须是对象")
    return observation_id, candidate_id, refs, dict(lineage), list(evidence), dict(constraints)


def _artifact(
    *, observation_id: str, revision: int, role: str, status: str, cutoff_at: str,
    refs: tuple[Mapping[str, Any], ...], lineage: Mapping[str, Any], full_text: str = "",
    provider: str | None = None, model: str | None = None, error: str | None = None,
    analysis_id: str | None = None, usage: Mapping[str, Any] | None = None,
) -> AnalysisArtifact:
    if status not in ANALYSIS_STATUSES:
        raise ValueError(f"未知分析状态：{status}")
    return AnalysisArtifact(
        analysis_id=analysis_id or str(uuid4()), observation_id=observation_id, revision=revision,
        role=role, status=status, input_cutoff_at=cutoff_at, source_refs=refs,
        input_lineage=dict(lineage), full_text=full_text, provider=provider, model=model,
        prompt_version=PROMPT_VERSION, usage=dict(usage or {}), error=error,
    )


def _usage(result: LLMResult) -> dict[str, Any]:
    """Keep only provider-reported accounting facts; never estimate an amount from token counts."""
    cache_values: list[int] = []
    cache_known = True
    responses = result.raw_usage.get("responses") if isinstance(result.raw_usage, Mapping) else None
    if not isinstance(responses, list) or not responses:
        cache_known = False
    else:
        for response in responses:
            if not isinstance(response, Mapping):
                cache_known = False
                continue
            details = response.get("prompt_tokens_details")
            candidates = (
                response.get("prompt_cache_hit_tokens"), response.get("cache_tokens"),
                response.get("cached_tokens"),
                details.get("cached_tokens") if isinstance(details, Mapping) else None,
            )
            value = next((item for item in candidates if isinstance(item, int) and not isinstance(item, bool) and item >= 0), None)
            if value is None:
                cache_known = False
            else:
                cache_values.append(value)
    return {
        "inputTokens": result.prompt_tokens,
        "outputTokens": result.completion_tokens,
        "totalTokens": result.total_tokens,
        "cacheTokens": sum(cache_values) if cache_known else None,
        "usageUnavailable": bool(result.usage_unavailable),
        "cacheUsageUnavailable": not cache_known,
        # DeepSeek does not return a reliable currency charge in this interface.  No amount is guessed.
        "cost": {"amount": None, "currency": None, "pricingVersion": None, "status": "unavailable"},
    }


def _call(provider: LLMProvider, messages: list[ChatMessage], *, model_options: Mapping[str, Any] | None = None) -> tuple[str, str | None, str | None, str | None, Mapping[str, Any]]:
    try:
        kwargs: dict[str, Any] = {"enable_search": False}
        if model_options is not None:
            kwargs["model_options"] = model_options
        result: LLMResult = provider.chat(messages, **kwargs)
    except Exception as exc:  # exception text may contain an upstream URL or credential
        return "", None, None, f"模型调用异常：{type(exc).__name__}", {}
    usage = _usage(result)
    if not result.ok:
        return "", result.provider or None, result.model or None, "模型调用失败", usage
    if not isinstance(result.content, str) or not result.content.strip():
        return "", result.provider or None, result.model or None, "模型未返回正文", usage
    return result.content.strip(), result.provider or None, result.model or None, None, usage


def run_pro(
    *, context: Mapping[str, Any], cutoff_at: str, provider: LLMProvider, revision: int = 1,
    model_options: Mapping[str, Any] | None = None,
) -> AnalysisArtifact:
    """Run and return only the first role, so a worker can durably checkpoint it before con."""
    observation_id, candidate_id, refs, lineage, evidence, constraints = _context(context, cutoff_at=cutoff_at)
    text, provider_name, model, error, usage = _call(provider, pro_messages(
        observation_id=observation_id, candidate_id=candidate_id, cutoff_at=cutoff_at,
        source_refs=refs, input_lineage=lineage, evidence=evidence, user_constraints=constraints,
    ), model_options=model_options)
    return _artifact(
        observation_id=observation_id, revision=revision, role="pro",
        status="failed" if error else "completed", cutoff_at=cutoff_at, refs=refs, lineage=lineage,
        full_text=text, provider=provider_name, model=model, usage=usage, error=error,
    )


def run_con(
    *, context: Mapping[str, Any], cutoff_at: str, provider: LLMProvider,
    pro: AnalysisArtifact, revision: int | None = None, model_options: Mapping[str, Any] | None = None,
) -> AnalysisArtifact:
    """Run the second role only after a persisted successful pro artifact is supplied."""
    observation_id, candidate_id, refs, lineage, evidence, constraints = _context(context, cutoff_at=cutoff_at)
    target_revision = pro.revision if revision is None else revision
    if (
        pro.role != "pro" or pro.status != "completed" or pro.observation_id != observation_id
        or pro.input_cutoff_at != cutoff_at
    ):
        raise AnalysisInputError("反方只能读取同一 Observation、截止和修订的已完成正方全文")
    if dict(pro.input_lineage) != dict(lineage) or tuple(pro.source_refs) != tuple(refs):
        raise AnalysisInputError("正反双方必须读取同一份冻结资料和行情")
    lineage = {**lineage, "proAnalysis": {"analysisId": pro.analysis_id, "revision": pro.revision,
        "inputCutoffAt": pro.input_cutoff_at, "contentSha256": sha256(pro.full_text.encode("utf-8")).hexdigest()}}
    text, provider_name, model, error, usage = _call(provider, con_messages(
        observation_id=observation_id, candidate_id=candidate_id, cutoff_at=cutoff_at,
        source_refs=refs, input_lineage=lineage, evidence=evidence, pro_full_text=pro.full_text,
        user_constraints=constraints,
    ), model_options=model_options)
    return _artifact(
        observation_id=observation_id, revision=target_revision, role="con",
        status="failed" if error else "completed", cutoff_at=cutoff_at, refs=refs, lineage=lineage,
        full_text=text, provider=provider_name, model=model, usage=usage, error=error,
    )


def run_debate(
    *, context: Mapping[str, Any], cutoff_at: str, config: Mapping[str, Any] | None,
    provider: LLMProvider | None, revision: int = 1,
) -> DebateResult:
    """Run exactly pro then con, or return persistent-ready failure records.

    There is deliberately no fallback provider: a missing/invalid K10 configuration or explicit
    provider yields ``not_configured`` before any network activity. The con prompt receives the
    complete pro output only after a successful pro call.
    """
    observation_id, candidate_id, refs, lineage, evidence, constraints = _context(context, cutoff_at=cutoff_at)
    configuration = validate_run_config(config, scope="analysis")
    if not configuration.ready or provider is None:
        missing = list(configuration.missing)
        errors = list(configuration.errors)
        if provider is None:
            errors.append("K10 analysis provider 未配置")
        error = "；".join([*(f"缺少 {item}" for item in missing), *errors]) or "K10 分析未配置"
        pro = _artifact(observation_id=observation_id, revision=revision, role="pro", status="not_configured",
                        cutoff_at=cutoff_at, refs=refs, lineage=lineage, error=error)
        con = _artifact(observation_id=observation_id, revision=revision, role="con", status="not_configured",
                        cutoff_at=cutoff_at, refs=refs, lineage=lineage, error=error)
        return DebateResult(pro, con)

    pro = run_pro(context=context, cutoff_at=cutoff_at, provider=provider, revision=revision)
    if pro.status != "completed":
        con = _artifact(observation_id=observation_id, revision=revision, role="con", status="failed",
                        cutoff_at=cutoff_at, refs=refs, lineage=lineage,
                        error="正方未完成，反方未启动：" + (pro.error or "未知失败"))
        return DebateResult(pro, con)
    con = run_con(context=context, cutoff_at=cutoff_at, provider=provider, pro=pro)
    return DebateResult(pro, con)


def record_debate(
    *, repository: AnalysisRepository, db_path: Path, result: DebateResult,
    created_at: str | None = None,
) -> DebateResult:
    """Append both artifacts. The append-only store owns collision handling and schema checks."""
    timestamp = created_at or _utc_now()
    for artifact in (result.pro, result.con):
        record_analysis_artifact(repository=repository, db_path=db_path, artifact=artifact, created_at=timestamp)
    return result


def record_analysis_artifact(
    *, repository: AnalysisRepository, db_path: Path, artifact: AnalysisArtifact, created_at: str | None = None,
) -> None:
    repository.append_analysis_revision(
        analysis_id=artifact.analysis_id, observation_id=artifact.observation_id,
        revision=artifact.revision, analysis_kind=artifact.role,
        input_cutoff_at=artifact.input_cutoff_at, input_lineage=artifact.input_lineage,
        content=artifact.to_dict(), status=artifact.status, created_at=created_at or _utc_now(), db_path=db_path,
    )


def run_and_record_debate(
    *, repository: AnalysisRepository, db_path: Path, observation_id: str, cutoff_at: str,
    config: Mapping[str, Any] | None, provider: LLMProvider | None, revision: int = 1,
    created_at: str | None = None,
) -> DebateResult:
    """Worker convenience entry point. The repository supplies the frozen selected context."""
    context = repository.load_observation_context(
        observation_id=observation_id, cutoff_at=cutoff_at, db_path=db_path
    )
    if context is None:
        raise AnalysisInputError("Observation 不存在或无法读取冻结上下文")
    return record_debate(
        repository=repository, db_path=db_path,
        result=run_debate(context=context, cutoff_at=cutoff_at, config=config, provider=provider, revision=revision),
        created_at=created_at,
    )


__all__ = [
    "ANALYSIS_STATUSES", "AnalysisArtifact", "AnalysisInputError", "AnalysisRepository", "DebateResult",
    "augment_analysis_context", "record_analysis_artifact", "record_debate", "run_and_record_debate", "run_con",
    "run_debate", "run_pro",
]
