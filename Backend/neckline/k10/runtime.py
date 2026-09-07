"""K10 worker handlers assembled from explicit provider and frozen-evidence seams."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

from . import store
from .analysis import AnalysisArtifact, AnalysisInputError, record_analysis_artifact, run_con, run_pro
from .market_context import MarketContextError, attach_frozen_market_context
from .providers import resolve_deepseek_v4_pro
from .worker import TaskContext, TaskResult


def _artifact(raw: Mapping[str, Any]) -> AnalysisArtifact:
    return AnalysisArtifact(
        analysis_id=str(raw["analysisId"]), observation_id=str(raw["observationId"]), revision=int(raw["revision"]),
        role=str(raw["role"]), status=str(raw["status"]), input_cutoff_at=str(raw["inputCutoffAt"]),
        source_refs=tuple(raw.get("sourceRefs") or ()), input_lineage=dict(raw.get("inputLineage") or {}),
        full_text=str(raw.get("fullText") or ""), provider=raw.get("provider"), model=raw.get("model"),
        prompt_version=str(raw.get("promptVersion") or "k10-debate-v1"), usage=dict(raw.get("usage") or {}),
        error=raw.get("error"),
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
    snapshot = store.load_observation_context(observation_id=observation_id, cutoff_at=context.input_cutoff_at, db_path=context.db_path)
    if snapshot is None:
        return TaskResult("failed", "input", context.checkpoint, "观察对象或冻结资料不存在")
    publication = {
        "companyWindow": context.task.payload.get("companyWindow"),
        "opportunities": context.task.payload.get("opportunities"),
        "publicationSamples": context.task.payload.get("publicationSamples"),
    }
    if any(value is not None for value in publication.values()):
        if not isinstance(publication["companyWindow"], Mapping) or not isinstance(publication["opportunities"], list) or not isinstance(publication["publicationSamples"], list):
            return TaskResult("failed", "input", context.checkpoint, "冻结公司窗口发布上下文无效")
        snapshot = {**snapshot, "publicationContext": publication}
    prior = store.load_analysis_revision(
        observation_id=observation_id, input_cutoff_at=context.input_cutoff_at, analysis_kind="pro", db_path=context.db_path,
    )
    if prior and prior.get("status") == "completed" and isinstance(prior.get("content"), Mapping):
        pro = _artifact(prior["content"])
        frozen_market = pro.input_lineage.get("marketContext")
    else:
        frozen_market = context.task.payload.get("marketContext")
        revision = (int(prior["revision"]) + 1) if prior else 1
    try:
        snapshot = attach_frozen_market_context(
            observation_context=snapshot, market_context=frozen_market, cutoff_at=context.input_cutoff_at,
        )
    except MarketContextError:
        return TaskResult("failed", "input", context.checkpoint, "冻结行情上下文无效")
    if not (prior and prior.get("status") == "completed" and isinstance(prior.get("content"), Mapping)):
        try:
            pro = _with_pricing(run_pro(context=snapshot, cutoff_at=context.input_cutoff_at, provider=resolution.provider, revision=revision), configuration)
        except (AnalysisInputError, ValueError):
            return TaskResult("failed", "input", context.checkpoint, "冻结分析输入无效")
        context.require_lease()
        record_analysis_artifact(repository=store, db_path=context.db_path, artifact=pro)
    if pro.status != "completed":
        return TaskResult("failed", "pro_failed", _checkpoint(pro=pro), pro.error or "正方分析失败")
    completed_con = store.load_analysis_revision(
        observation_id=observation_id, input_cutoff_at=context.input_cutoff_at, analysis_kind="con", db_path=context.db_path,
    )
    if completed_con and completed_con.get("status") == "completed" and isinstance(completed_con.get("content"), Mapping):
        con = _artifact(completed_con["content"])
    else:
        con_revision = (int(completed_con["revision"]) + 1) if completed_con else pro.revision
        try:
            con = _with_pricing(run_con(context=snapshot, cutoff_at=context.input_cutoff_at, provider=resolution.provider, pro=pro, revision=con_revision), configuration)
        except (AnalysisInputError, ValueError):
            return TaskResult("failed", "con_input", _checkpoint(pro=pro), "反方冻结输入无效")
        context.require_lease()
        record_analysis_artifact(repository=store, db_path=context.db_path, artifact=con)
        if con.status != "completed":
            return TaskResult("failed", "con_failed", _checkpoint(pro=pro, con=con), con.error or "反方分析失败")
    context.require_lease()
    return TaskResult("completed", "analysis_ready", _checkpoint(pro=pro, con=con))


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
