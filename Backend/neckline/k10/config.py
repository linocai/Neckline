"""K10-v1.4 strategy and V3.0.6 execution configuration validation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ConfigurationStatus:
    state: str
    missing: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.state == "configured"


_SCOPES = {
    "candidate": ("configVersion", "hardExclusions", "sourceAdapters"),
    "discovery": ("configVersion", "hardExclusions", "sourceAdapters", "modelRoutes", "taskPolicies"),
    "analysis": ("configVersion", "modelRoutes", "taskPolicies"),
    "morning": ("configVersion", "sourceAdapters", "modelRoutes", "taskPolicies"),
    "evaluation": ("configVersion", "evaluationPolicy", "marketCollection", "taskPolicies"),
}
_K10_MODEL_TASKS = ("discovery", "analysis", "morning")
_DEEPSEEK_V4_PRO = "deepseek-v4-pro"
_ROUTE_REQUIRED_BY_SCOPE = {"discovery": "discovery", "analysis": "analysis", "morning": "morning"}
_V3_MODEL_STAGES = (
    "titleBatch", "titleReconcile", "understand", "verify", "companyComparison", "prioritize",
    "morning", "analysisPro", "analysisCon",
)


def _present(value: Any) -> bool:
    return bool(value.strip()) if isinstance(value, str) else value is not None and value != {} and value != []


def _positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 1


def _nonnegative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def validate_run_config(payload: Mapping[str, Any] | None, *, scope: str) -> ConfigurationStatus:
    if scope not in _SCOPES:
        raise ValueError(f"未知 K10-v1.4 配置范围：{scope}")
    if not isinstance(payload, Mapping):
        return ConfigurationStatus("not_configured", _SCOPES[scope], ("缺少配置包",))
    missing = [key for key in _SCOPES[scope] if not _present(payload.get(key))]
    errors: list[str] = []
    if payload.get("configVersion") != "k10-v1.4":
        errors.append("configVersion 必须是 k10-v1.4")
    if payload.get("universe") != "chinext":
        errors.append("universe 必须明确为 chinext")
    if payload.get("excludeBaijiu") is not True:
        errors.append("excludeBaijiu 必须明确为 true")
    hard = payload.get("hardExclusions")
    expected_hard = {"approved": True, "board": "chinext", "priceLimit": "none", "st": "exclude", "swL2Exclusions": ["801125.SI"]}
    if not isinstance(hard, Mapping) or set(hard) != set(expected_hard) or any(hard.get(k) != v for k, v in expected_hard.items()):
        errors.append("hardExclusions 必须精确为已批准的创业板、无价限、ST 与 801125.SI 白酒排除规则")
    adapters = payload.get("sourceAdapters")
    if scope in {"candidate", "discovery", "morning"}:
        if not isinstance(adapters, list):
            errors.append("sourceAdapters 必须是列表")
        else:
            keys: list[str] = []
            for adapter in adapters:
                if not isinstance(adapter, Mapping) or set(adapter) != {"key", "lateArrivalReplaySeconds"}:
                    errors.append("sourceAdapters 每项必须精确包含 key 与 lateArrivalReplaySeconds")
                    continue
                key, replay = adapter.get("key"), adapter.get("lateArrivalReplaySeconds")
                if not isinstance(key, str) or not key.strip():
                    errors.append("sourceAdapters.key 必须是非空来源键")
                else:
                    keys.append(key)
                if not _positive_int(replay):
                    errors.append("sourceAdapters.lateArrivalReplaySeconds 必须是正整数")
            if len(keys) != len(set(keys)):
                errors.append("sourceAdapters.key 不能重复")
    routes = payload.get("modelRoutes")
    if routes is not None and not isinstance(routes, Mapping):
        errors.append("modelRoutes 必须是对象")
    if isinstance(routes, Mapping):
        if set(routes) - set(_K10_MODEL_TASKS) or any(value != _DEEPSEEK_V4_PRO for value in routes.values()):
            errors.append("K10 modelRoutes 只能用 discovery、analysis、morning，且必须为 deepseek-v4-pro")
        required = _ROUTE_REQUIRED_BY_SCOPE.get(scope)
        if required and required not in routes:
            errors.append(f"modelRoutes 缺少 {required}")
    policies = payload.get("taskPolicies")
    if policies is not None and not isinstance(policies, Mapping):
        errors.append("taskPolicies 必须是对象")
    if isinstance(policies, Mapping):
        for task in ("discovery", "analysis", "morning", "evaluation"):
            policy = policies.get(task)
            if policy is None:
                continue
            if not isinstance(policy, Mapping):
                errors.append(f"taskPolicies.{task} 必须是对象")
                continue
            if not _positive_int(policy.get("maxAttempts")):
                errors.append(f"taskPolicies.{task}.maxAttempts 必须是正整数")
        model_task = _ROUTE_REQUIRED_BY_SCOPE.get(scope)
        if model_task:
            model_policy = policies.get(model_task)
            if not isinstance(model_policy, Mapping):
                errors.append(f"taskPolicies.{model_task} 必须明确配置")
            else:
                timeout = model_policy.get("timeoutSeconds")
                if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
                    errors.append(f"taskPolicies.{model_task}.timeoutSeconds 必须是正数")
                if not _positive_int(model_policy.get("modelMaxAttempts")):
                    errors.append(f"taskPolicies.{model_task}.modelMaxAttempts 必须是正整数")
        if scope == "discovery":
            policy = policies.get("discovery")
            if not isinstance(policy, Mapping) or not _positive_int(policy.get("maxSourceRequests")):
                errors.append("taskPolicies.discovery.maxSourceRequests 必须是正整数")
    evaluation = payload.get("evaluationPolicy")
    if evaluation is not None and not isinstance(evaluation, Mapping):
        errors.append("evaluationPolicy 必须是对象")
    if scope == "evaluation" and isinstance(evaluation, Mapping):
        required = {"version": "k10-evaluation-v1.4", "selectionFreeze": "d1_open_0930", "window": "d1_d2", "primaryMetric": "close_limit_up_any_d1_d2"}
        if set(evaluation) != set(required) or any(evaluation.get(k) != v for k, v in required.items()):
            errors.append("evaluationPolicy 必须精确声明 V1.4 固定窗口与收盘封板口径")
    collection = payload.get("marketCollection")
    if collection is not None and not isinstance(collection, Mapping):
        errors.append("marketCollection 必须是对象")
    if scope == "evaluation" and isinstance(collection, Mapping):
        required_collection = {"retryIntervalSeconds", "retryUntilMinutesAfterClose"}
        if set(collection) != required_collection:
            errors.append("marketCollection 必须精确声明补数间隔和常规收盘后时限")
        elif not _positive_int(collection["retryIntervalSeconds"]) or not _positive_int(collection["retryUntilMinutesAfterClose"]):
            errors.append("marketCollection 的补数时间必须为正整数")
        elif collection["retryIntervalSeconds"] >= collection["retryUntilMinutesAfterClose"] * 60:
            errors.append("marketCollection.retryIntervalSeconds 必须小于常规补数时限")
    return ConfigurationStatus("not_configured", tuple(missing), tuple(errors)) if missing or errors else ConfigurationStatus("configured", (), ())


def _validate_model_options(value: Any, errors: list[str]) -> None:
    if not isinstance(value, Mapping) or set(value) != set(_V3_MODEL_STAGES):
        errors.append("discovery.modelOptions 必须逐项声明标题、正文、核验、比较、晨报和正反分析操作")
        return
    for stage in _V3_MODEL_STAGES:
        option = value[stage]
        if not isinstance(option, Mapping) or not {"maxTokens", "thinking"} <= set(option):
            errors.append(f"discovery.modelOptions.{stage} 必须包含 maxTokens 与 thinking")
            continue
        if not _positive_int(option.get("maxTokens")):
            errors.append(f"discovery.modelOptions.{stage}.maxTokens 必须是正整数")
        thinking = option.get("thinking")
        mode = thinking.get("type") if isinstance(thinking, Mapping) else None
        if mode == "disabled":
            if set(thinking) != {"type"} or set(option) != {"maxTokens", "thinking"}:
                errors.append(f"discovery.modelOptions.{stage}.thinking disabled 时不能附带推理强度")
        elif mode == "enabled":
            if (set(thinking) != {"type"} or set(option) != {"maxTokens", "thinking", "reasoningEffort"}
                    or option.get("reasoningEffort") not in {"low", "high", "max"}):
                errors.append(f"discovery.modelOptions.{stage}.thinking enabled 时必须明确 reasoningEffort")
        else:
            errors.append(f"discovery.modelOptions.{stage}.thinking.type 必须为 disabled 或 enabled")


def _valid_title_policy_content(value: Any) -> bool:
    expected = {
        "policyVersion", "allExactDeduplicatedTitlesRequired", "titleInputFields", "retain", "merge",
        "forbiddenHardExclusions", "selection",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        return False
    if value.get("policyVersion") != "k10-title-triage-v1" or value.get("allExactDeduplicatedTitlesRequired") is not True:
        return False
    if value.get("titleInputFields") != ["documentId", "revision", "sourceKey", "publishedAt", "title"]:
        return False
    retain = value.get("retain")
    if not isinstance(retain, list) or len(retain) < 4 or any(not isinstance(item, str) or not item.strip() for item in retain):
        return False
    merge = value.get("merge")
    if not isinstance(merge, Mapping) or set(merge) != {"sameMatterSyndication", "neverMerge"}:
        return False
    if not isinstance(merge.get("sameMatterSyndication"), str) or not merge["sameMatterSyndication"].strip():
        return False
    if not isinstance(merge.get("neverMerge"), list) or len(merge["neverMerge"]) < 3:
        return False
    forbidden = value.get("forbiddenHardExclusions")
    if not isinstance(forbidden, list) or len(forbidden) < 6:
        return False
    selection = value.get("selection")
    if not isinstance(selection, Mapping) or set(selection) != {
        "globalAfterAllBatches", "actualArticleCount", "doNotFillQuota", "noBodyBeforeFrozenSelection", "requiredDisposition",
    }:
        return False
    return (all(selection.get(name) is True for name in ("globalAfterAllBatches", "actualArticleCount", "doNotFillQuota", "noBodyBeforeFrozenSelection"))
            and selection.get("requiredDisposition") == ["candidate", "uncertain", "protected", "not_selected", "merged", "no_value"])


def validate_execution_config(payload: Mapping[str, Any] | None) -> ConfigurationStatus:
    """Validate the V3 title-first operational profile.

    V1/V2 profiles remain readable historical records, but never describe an
    executable paid K10 task after the V3.0.6 cutover.
    """
    required = ("executionVersion", "discovery")
    if not isinstance(payload, Mapping):
        return ConfigurationStatus("not_configured", required, ("缺少执行配置包",))
    missing = tuple(key for key in required if not _present(payload.get(key)))
    errors: list[str] = []
    if payload.get("executionVersion") != "k10-execution-v3":
        errors.append("执行配置必须是 k10-execution-v3；V1/V2 只可读取历史，不能绑定运行")
    if set(payload) != set(required):
        errors.append("执行配置只能包含 executionVersion 与 discovery")
    discovery = payload.get("discovery")
    expected = {
    "model", "titleTriagePolicy", "articleLimits", "titleBatchSize", "titleTriageConcurrency",
        "deepReadConcurrency", "networkMaxAttempts", "jsonRepairMaxAttempts", "retryBackoffSeconds",
        "taskSliceSeconds", "completionDeadlineSeconds", "continuationDelaySeconds", "modelOptions",
    }
    if not isinstance(discovery, Mapping) or set(discovery) != expected:
        errors.append("discovery 必须精确声明标题 policy、80/40 正文限额和单请求执行边界")
        return ConfigurationStatus("not_configured", missing, tuple(errors))
    if discovery.get("model") != _DEEPSEEK_V4_PRO:
        errors.append("discovery.model 必须精确为 deepseek-v4-pro")
    policy = discovery.get("titleTriagePolicy")
    policy_expected = {"policyId", "revision", "contentSha256", "approvalState", "content"}
    if not isinstance(policy, Mapping) or set(policy) != policy_expected:
        errors.append("titleTriagePolicy 必须精确声明 ID、revision、哈希、批准状态与语义内容")
    else:
        if not isinstance(policy.get("policyId"), str) or not policy["policyId"].strip():
            errors.append("titleTriagePolicy.policyId 必须是非空字符串")
        if not _positive_int(policy.get("revision")):
            errors.append("titleTriagePolicy.revision 必须是正整数")
        content_hash = policy.get("contentSha256")
        if not isinstance(content_hash, str) or len(content_hash) != 64 or any(char not in "0123456789abcdef" for char in content_hash):
            errors.append("titleTriagePolicy.contentSha256 必须是 SHA-256 小写十六进制")
        if policy.get("approvalState") != "approved":
            errors.append("titleTriagePolicy 必须显式为 approved")
        if not _valid_title_policy_content(policy.get("content")):
            errors.append("titleTriagePolicy.content 必须完整声明全量标题、保留、合并、禁止硬排与全局冻结语义")
    limits = discovery.get("articleLimits")
    if not isinstance(limits, Mapping) or set(limits) != {"evening", "morning"}:
        errors.append("articleLimits 必须精确声明 evening 与 morning")
    elif limits.get("evening") != 80 or limits.get("morning") != 40:
        errors.append("articleLimits 必须精确为 evening=80、morning=40")
    for name in ("titleBatchSize", "titleTriageConcurrency", "deepReadConcurrency", "networkMaxAttempts", "taskSliceSeconds", "completionDeadlineSeconds", "continuationDelaySeconds"):
        if not _positive_int(discovery.get(name)):
            errors.append(f"discovery.{name} 必须是正整数")
    if not _nonnegative_int(discovery.get("jsonRepairMaxAttempts")):
        errors.append("discovery.jsonRepairMaxAttempts 必须是非负整数")
    backoff = discovery.get("retryBackoffSeconds")
    if (not isinstance(backoff, list) or not backoff or any(not _positive_int(value) for value in backoff)
            or any(later <= earlier for earlier, later in zip(backoff, backoff[1:]))):
        errors.append("discovery.retryBackoffSeconds 必须是严格递增的正整数列表")
    _validate_model_options(discovery.get("modelOptions"), errors)
    return ConfigurationStatus("not_configured", missing, tuple(errors)) if missing or errors else ConfigurationStatus("configured", (), ())


__all__ = ["ConfigurationStatus", "validate_execution_config", "validate_run_config"]
