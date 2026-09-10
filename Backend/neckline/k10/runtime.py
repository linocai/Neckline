"""K10 worker handlers assembled from explicit provider and frozen-evidence seams."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

from . import store
from .analysis import AnalysisArtifact, AnalysisInputError, augment_analysis_context, record_analysis_artifact, run_con, run_pro
from .market_context import MarketContextError, attach_frozen_market_context
from .metering import MeteredProvider, bind_provider_execution_spending, execution_model_options, provider_spend_context
from .providers import resolve_deepseek_v4_pro
from .provider_failures import provider_deadline_result, provider_failure_result
from .worker import TaskContext, TaskResult


def _artifact(raw: Mapping[str, Any]) -> AnalysisArtifact:
    return AnalysisArtifact(
        analysis_id=str(raw["analysisId"]), observation_id=str(raw["observationId"]), revision=int(raw["revision"]),
        role=str(raw["role"]), status=str(raw["status"]), input_cutoff_at=str(raw["inputCutoffAt"]),
        source_refs=tuple(raw.get("sourceRefs") or ()), input_lineage=dict(raw.get("inputLineage") or {}),
        summary=raw.get("summary"), full_text=str(raw.get("fullText") or ""), provider=raw.get("provider"), model=raw.get("model"),
        prompt_version=str(raw.get("promptVersion") or "k10-debate-v1"), usage=dict(raw.get("usage") or {}),
        error=raw.get("error"), provider_error_code=raw.get("providerErrorCode"),
        retry_after_seconds=raw.get("retryAfterSeconds"),
    )


def _with_pricing(artifact: AnalysisArtifact, configuration: Mapping[str, Any]) -> AnalysisArtifact:
    policy = (configuration.get("taskPolicies") or {}).get("analysis") if isinstance(configuration, Mapping) else None
    version = policy.get("pricingVersion") if isinstance(policy, Mapping) and isinstance(policy.get("pricingVersion"), str) else None
    usage = dict(artifact.usage)
    cost = dict(usage.get("cost") or {})
    cost["pricingVersion"] = version
    cost.setdefault("amount", None); cost.setdefault("currency", None); cost.setdefault("status", "unavailable")
    usage["cost"] = cost
    return replace(artifact, usage=usage)


def _checkpoint(*, pro: AnalysisArtifact, con: AnalysisArtifact | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"pro": pro.to_dict(), "analysisRevision": pro.revision}
    if con is not None:
        value["con"] = con.to_dict()
    return value


def _failed_role(*, context: TaskContext, role: AnalysisArtifact, checkpoint: Mapping[str, Any]) -> TaskResult:
    if role.provider_error_code is not None:
        return provider_failure_result(context=context, stage=role.role + "_failed",
                                       code=role.provider_error_code, retry_after_seconds=role.retry_after_seconds,
                                       checkpoint=checkpoint)
    return TaskResult("failed", role.role + "_failed", checkpoint, role.error or "分析失败")


def _chain_artifact(*, company_window_id: str, revision: int, role: str, db_path) -> AnalysisArtifact | None:
    """Read only the exact global revision; cutoff is not an analysis identity."""
    chain = store.list_analysis_chain(company_window_id=company_window_id, db_path=db_path)
    for item in chain.get("items", ()) if isinstance(chain, Mapping) else ():
        if not isinstance(item, Mapping) or item.get("revision") != revision:
            continue
        for raw in item.get("analyses", ()):
            if isinstance(raw, Mapping) and raw.get("role") == role and raw.get("status") == "completed":
                content = raw.get("content")
                return _artifact(content) if isinstance(content, Mapping) else None
    return None


def _ref_identity(value: Any) -> tuple[tuple[str, int], ...] | None:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        return None
    result: list[tuple[str, int]] = []
    for raw in value:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("documentId"), str) or not isinstance(raw.get("revision"), int):
            return None
        result.append((raw["documentId"], raw["revision"]))
    return tuple(result)


def _requested_snapshot(context: TaskContext, observation_id: str) -> tuple[Mapping[str, Any], int, str | None, str | None] | None:
    """Resolve initial versus append-only request input without any cutoff-based fallback."""
    payload = context.task.payload
    request_id = payload.get("analysisRequestId")
    if request_id is None:
        global_revision = payload.get("globalRevision", 1)
        if isinstance(global_revision, bool) or global_revision != 1:
            return None
        snapshot = store.load_observation_context(
            observation_id=observation_id, cutoff_at=context.input_cutoff_at, db_path=context.db_path,
        )
        return None if snapshot is None else (snapshot, 1, None, None)
    if not isinstance(request_id, str) or not request_id:
        return None
    loaded = store.load_analysis_request_context(request_id=request_id, db_path=context.db_path)
    if not isinstance(loaded, Mapping):
        return None
    request = loaded.get("analysisRequest")
    if (not isinstance(request, Mapping) or loaded.get("observationId") != observation_id
            or loaded.get("cutoffAt") != context.input_cutoff_at
            or request.get("globalRevision") != payload.get("globalRevision")
            or request.get("targetRevision") != payload.get("targetRevision")
            or request.get("parentRevision") != payload.get("parentRevision")
            or request.get("kind") != payload.get("analysisRequestKind")
            or request.get("question") != payload.get("question")
            or _ref_identity(request.get("sourceRefs")) != _ref_identity(payload.get("frozenEvidenceRefs"))):
        return None
    window_id = loaded.get("companyWindowId")
    if not isinstance(window_id, str) or not window_id:
        return None
    try:
        snapshot = augment_analysis_context(
            context=loaded, request=request, request_documents=loaded.get("requestDocuments", ()),
            prior_analyses=loaded.get("priorAnalyses", ()),
        )
    except AnalysisInputError:
        return None
    return snapshot, int(request["globalRevision"]), request_id, window_id


def analysis_handler(
    context: TaskContext,
    *,
    provider_resolver: Callable[..., Any] | None = None,
) -> TaskResult:
    """Execute a selected-stock analysis task without defaults or legacy routing.

    A completed pro artifact is reused from the durable analysis store/checkpoint. Con failures
    keep their full text in the task checkpoint and therefore never trigger another pro call.
    """
    context.require_lease()
    observation_id = context.task.payload.get("observationId")
    if not isinstance(observation_id, str) or not observation_id:
        return TaskResult("not_configured", "configuration", error="分析任务缺少 observationId")
    frozen = store.load_task_analysis_config(task_id=context.task.task_id, db_path=context.db_path)
    configuration = frozen.get("payload") if isinstance(frozen, Mapping) else None
    resolver = provider_resolver or resolve_deepseek_v4_pro
    resolution = resolver(configuration=configuration, task="analysis", db_path=context.db_path)
    if resolution.provider is None or not isinstance(configuration, Mapping):
        return TaskResult("not_configured", "configuration", context.checkpoint, resolution.error or "模型未配置")
    # A legacy provider cannot quietly become a live fallback.  The concrete
    # production provider is fail-closed until an explicit V3 attempt context
    # is bound around each role's only HTTP attempt.
    bind_provider_execution_spending(provider=resolution.provider, task_id=context.task.task_id,
                                     execution_profile=context.execution_profile)
    pro_model_options = execution_model_options(execution_profile=context.execution_profile,
                                                stage="analysisPro", option_stage="companyComparison")
    con_model_options = execution_model_options(execution_profile=context.execution_profile,
                                                stage="analysisCon", option_stage="companyComparison")
    if isinstance(resolution.provider, MeteredProvider) and (pro_model_options is None or con_model_options is None):
        return TaskResult("not_configured", "configuration", context.checkpoint,
                          "正反分析模型单次输出上限未在冻结执行配置中明确")
    requested = _requested_snapshot(context, observation_id)
    if requested is None:
        return TaskResult("failed", "input", context.checkpoint, "观察对象、追加请求或冻结资料不存在")
    snapshot, revision, request_id, company_window_id = requested
    publication = {
        "companyWindow": context.task.payload.get("companyWindow"),
        "opportunities": context.task.payload.get("opportunities"),
        "publicationSamples": context.task.payload.get("publicationSamples"),
    }
    if any(value is not None for value in publication.values()):
        if not isinstance(publication["companyWindow"], Mapping) or not isinstance(publication["opportunities"], list) or not isinstance(publication["publicationSamples"], list):
            return TaskResult("failed", "input", context.checkpoint, "冻结公司窗口发布上下文无效")
        snapshot = {**snapshot, "publicationContext": publication}
    if company_window_id is not None:
        pro = _chain_artifact(company_window_id=company_window_id, revision=revision, role="pro", db_path=context.db_path)
        prior = None
    else:
        prior = store.load_analysis_revision(
            observation_id=observation_id, input_cutoff_at=context.input_cutoff_at, analysis_kind="pro", db_path=context.db_path,
        )
        pro = _artifact(prior["content"]) if prior and prior.get("status") == "completed" and isinstance(prior.get("content"), Mapping) else None
    if pro is not None:
        frozen_market = pro.input_lineage.get("marketContext")
    else:
        frozen_market = context.task.payload.get("marketContext")
    try:
        snapshot = attach_frozen_market_context(
            observation_context=snapshot, market_context=frozen_market, cutoff_at=context.input_cutoff_at,
        )
    except MarketContextError:
        return TaskResult("failed", "input", context.checkpoint, "冻结行情上下文无效")
    if pro is None:
        expired = provider_deadline_result(context=context)
        if expired is not None:
            return expired
        try:
            with provider_spend_context(provider=resolution.provider, task_id=context.task.task_id,
                                        stage="analysisPro", item_key=f"{observation_id}:r{revision}",
                                        attempt=context.task.attempt_count, clock=context.clock):
                pro = _with_pricing(run_pro(context=snapshot, cutoff_at=context.input_cutoff_at,
                                            provider=resolution.provider, revision=revision,
                                            model_options=pro_model_options), configuration)
        except (AnalysisInputError, ValueError):
            return TaskResult("failed", "input", context.checkpoint, "冻结分析输入无效")
        context.require_lease()
        record_analysis_artifact(repository=store, db_path=context.db_path, artifact=pro)
    if pro.status != "completed":
        return _failed_role(context=context, role=pro, checkpoint=_checkpoint(pro=pro))
    if company_window_id is not None:
        con = _chain_artifact(company_window_id=company_window_id, revision=revision, role="con", db_path=context.db_path)
        completed_con = None
    else:
        completed_con = store.load_analysis_revision(
            observation_id=observation_id, input_cutoff_at=context.input_cutoff_at, analysis_kind="con", db_path=context.db_path,
        )
        con = _artifact(completed_con["content"]) if completed_con and completed_con.get("status") == "completed" and isinstance(completed_con.get("content"), Mapping) else None
    if con is not None:
        pass
    else:
        expired = provider_deadline_result(context=context, checkpoint=_checkpoint(pro=pro))
        if expired is not None:
            return expired
        try:
            with provider_spend_context(provider=resolution.provider, task_id=context.task.task_id,
                                        stage="analysisCon", item_key=f"{observation_id}:r{revision}",
                                        attempt=context.task.attempt_count, clock=context.clock):
                con = _with_pricing(run_con(context=snapshot, cutoff_at=context.input_cutoff_at,
                                            provider=resolution.provider, pro=pro, revision=revision,
                                            model_options=con_model_options), configuration)
        except (AnalysisInputError, ValueError):
            return TaskResult("failed", "con_input", _checkpoint(pro=pro), "反方冻结输入无效")
        context.require_lease()
        record_analysis_artifact(repository=store, db_path=context.db_path, artifact=con)
        if con.status != "completed":
            return _failed_role(context=context, role=con, checkpoint=_checkpoint(pro=pro, con=con))
    context.require_lease()
    checkpoint = _checkpoint(pro=pro, con=con)
    if request_id is not None:
        checkpoint.update({"analysisRequestId": request_id, "globalRevision": revision})
    return TaskResult("completed", "analysis_ready", checkpoint)


def production_analysis_handler(
    *, provider_resolver: Callable[..., Any] | None = None,
) -> Callable[[TaskContext], TaskResult]:
    """Return the selected-opportunity handler, optionally using an in-process resolver.

    The injected resolver is intended for isolated production smoke runs where
    credentials remain in process memory.  It does not bypass any frozen
    input, persistence, lease, or retry behavior.
    """
    if provider_resolver is None:
        return analysis_handler
    return lambda context: analysis_handler(context, provider_resolver=provider_resolver)


__all__ = ["analysis_handler", "production_analysis_handler"]
