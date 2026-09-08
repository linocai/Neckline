"""K10-v1.4 frozen configuration validation; no strategy behaviour has a code default."""
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
_EXECUTION_STAGES = ("understand", "verify", "companyComparison", "prioritize")
_V2_MODEL_STAGES = ("lightweight", "fullText", "verify", "map", "companyComparison", "classify", "prioritize", "morning", "analysisPro", "analysisCon")
_V2_SPEND_STAGES = (*_V2_MODEL_STAGES, "search", "retry")
_BUDGET_FIELDS = (
    "maxModelCalls", "maxInputTokens", "maxOutputTokens", "maxTotalTokens",
    "maxFullTextCalls", "maxRetries", "maxSearchRequests", "maxSearchCredits",
)


def _present(value: Any) -> bool:
    return bool(value.strip()) if isinstance(value, str) else value is not None and value != {} and value != []


def validate_run_config(payload: Mapping[str, Any] | None, *, scope: str) -> ConfigurationStatus:
    if scope not in _SCOPES:
        raise ValueError(f"未知 K10-v1.4 配置范围：{scope}")
    if not isinstance(payload, Mapping):
        return ConfigurationStatus("not_configured", _SCOPES[scope], ("缺少配置包",))
    missing = [key for key in _SCOPES[scope] if not _present(payload.get(key))]
    errors: list[str] = []
    if payload.get("configVersion") != "k10-v1.4": errors.append("configVersion 必须是 k10-v1.4")
    if payload.get("universe") != "chinext": errors.append("universe 必须明确为 chinext")
    if payload.get("excludeBaijiu") is not True: errors.append("excludeBaijiu 必须明确为 true")
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
                key = adapter.get("key")
                replay = adapter.get("lateArrivalReplaySeconds")
                if not isinstance(key, str) or not key.strip():
                    errors.append("sourceAdapters.key 必须是非空来源键")
                else:
                    keys.append(key)
                if isinstance(replay, bool) or not isinstance(replay, int) or replay < 1:
                    errors.append("sourceAdapters.lateArrivalReplaySeconds 必须是正整数")
            if len(keys) != len(set(keys)):
                errors.append("sourceAdapters.key 不能重复")
    routes = payload.get("modelRoutes")
    if routes is not None and not isinstance(routes, Mapping): errors.append("modelRoutes 必须是对象")
    if isinstance(routes, Mapping):
        if set(routes) - set(_K10_MODEL_TASKS) or any(value != _DEEPSEEK_V4_PRO for value in routes.values()):
            errors.append("K10 modelRoutes 只能用 discovery、analysis、morning，且必须为 deepseek-v4-pro")
        required = _ROUTE_REQUIRED_BY_SCOPE.get(scope)
        if required and required not in routes: errors.append(f"modelRoutes 缺少 {required}")
    policies = payload.get("taskPolicies")
    if policies is not None and not isinstance(policies, Mapping): errors.append("taskPolicies 必须是对象")
    if isinstance(policies, Mapping):
        for task in ("discovery", "analysis", "morning", "evaluation"):
            policy = policies.get(task)
            if policy is None: continue
            if not isinstance(policy, Mapping): errors.append(f"taskPolicies.{task} 必须是对象"); continue
            for name in ("maxAttempts", "costLimit"):
                if name not in policy: errors.append(f"taskPolicies.{task} 缺少 {name}")
            value = policy.get("maxAttempts")
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1): errors.append(f"taskPolicies.{task}.maxAttempts 必须是正整数")
            cost = policy.get("costLimit")
            if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int,float)) or cost < 0): errors.append(f"taskPolicies.{task}.costLimit 必须为 null 或非负数")
        model_task = _ROUTE_REQUIRED_BY_SCOPE.get(scope)
        if model_task:
            model_policy = policies.get(model_task)
            if not isinstance(model_policy, Mapping):
                errors.append(f"taskPolicies.{model_task} 必须明确配置")
            else:
                timeout = model_policy.get("timeoutSeconds")
                attempts = model_policy.get("modelMaxAttempts")
                if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
                    errors.append(f"taskPolicies.{model_task}.timeoutSeconds 必须是正数")
                if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
                    errors.append(f"taskPolicies.{model_task}.modelMaxAttempts 必须是正整数")
        if scope == "discovery":
            policy=policies.get("discovery")
            if not isinstance(policy, Mapping): errors.append("taskPolicies.discovery 必须明确配置")
            else:
                for name in ("maxSourceRequests", "maxVerificationRequests"):
                    if not isinstance(policy.get(name), int) or isinstance(policy.get(name), bool) or policy[name] < 1: errors.append(f"taskPolicies.discovery.{name} 必须是正整数")
    evaluation = payload.get("evaluationPolicy")
    if evaluation is not None and not isinstance(evaluation, Mapping): errors.append("evaluationPolicy 必须是对象")
    if scope == "evaluation" and isinstance(evaluation, Mapping):
        required={"version":"k10-evaluation-v1.4","selectionFreeze":"d1_open_0930","window":"d1_d2","primaryMetric":"close_limit_up_any_d1_d2"}
        if set(evaluation) != set(required) or any(evaluation.get(k)!=v for k,v in required.items()): errors.append("evaluationPolicy 必须精确声明 V1.4 固定窗口与收盘封板口径")
    collection = payload.get("marketCollection")
    if collection is not None and not isinstance(collection, Mapping):
        errors.append("marketCollection 必须是对象")
    if scope == "evaluation" and isinstance(collection, Mapping):
        required_collection = {"retryIntervalSeconds", "retryUntilMinutesAfterClose"}
        if set(collection) != required_collection:
            errors.append("marketCollection 必须精确声明补数间隔和常规收盘后时限")
        else:
            interval = collection["retryIntervalSeconds"]
            until = collection["retryUntilMinutesAfterClose"]
            if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
                errors.append("marketCollection.retryIntervalSeconds 必须是正整数")
            if isinstance(until, bool) or not isinstance(until, int) or until < 1:
                errors.append("marketCollection.retryUntilMinutesAfterClose 必须是正整数")
            if (isinstance(interval, int) and not isinstance(interval, bool) and interval > 0 and
                isinstance(until, int) and not isinstance(until, bool) and until > 0 and
                interval >= until * 60):
                errors.append("marketCollection.retryIntervalSeconds 必须小于常规补数时限")
    return ConfigurationStatus("not_configured", tuple(missing), tuple(errors)) if missing or errors else ConfigurationStatus("configured", (), ())


def validate_execution_config(payload: Mapping[str, Any] | None) -> ConfigurationStatus:
    """Validate an operational profile without changing a frozen K10 strategy pack.

    There are intentionally no code defaults here.  Every field which can alter
    throughput, retry consumption or model reasoning must be bound to the task.
    """
    if isinstance(payload, Mapping) and payload.get("executionVersion") == "k10-execution-v2":
        return _validate_execution_config_v2(payload)
    required = ("executionVersion", "discovery")
    if not isinstance(payload, Mapping):
        return ConfigurationStatus("not_configured", required, ("缺少执行配置包",))
    missing = tuple(key for key in required if not _present(payload.get(key)))
    errors: list[str] = []
    if payload.get("executionVersion") != "k10-execution-v1":
        errors.append("executionVersion 必须是 k10-execution-v1")
    if set(payload) != set(required):
        errors.append("执行配置只能包含 executionVersion 与 discovery")
    discovery = payload.get("discovery")
    expected = {"documentBatchSize", "understandConcurrency", "keyPassageMaxCharacters",
                "networkMaxAttempts", "jsonRepairMaxAttempts", "retryBackoffSeconds",
                "taskSliceSeconds", "completionDeadlineSeconds", "continuationDelaySeconds", "modelOptions"}
    if not isinstance(discovery, Mapping) or set(discovery) != expected:
        errors.append("discovery 执行配置字段不完整或包含未知字段")
    else:
        for name in ("documentBatchSize", "understandConcurrency", "keyPassageMaxCharacters",
                     "networkMaxAttempts", "taskSliceSeconds", "completionDeadlineSeconds", "continuationDelaySeconds"):
            value = discovery[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                errors.append(f"discovery.{name} 必须是正整数")
        repairs = discovery["jsonRepairMaxAttempts"]
        if isinstance(repairs, bool) or not isinstance(repairs, int) or repairs < 0:
            errors.append("discovery.jsonRepairMaxAttempts 必须是非负整数")
        backoff = discovery["retryBackoffSeconds"]
        if (not isinstance(backoff, list) or not backoff or
            any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in backoff) or
            any(later <= earlier for earlier, later in zip(backoff, backoff[1:]))):
            errors.append("discovery.retryBackoffSeconds 必须是严格递增的正整数列表")
        options = discovery["modelOptions"]
        if not isinstance(options, Mapping) or set(options) != set(_EXECUTION_STAGES):
            errors.append("discovery.modelOptions 必须逐项声明 understand、verify、companyComparison、prioritize")
        else:
            for stage in _EXECUTION_STAGES:
                option = options[stage]
                if not isinstance(option, Mapping) or not {"maxTokens", "thinking"} <= set(option):
                    errors.append(f"discovery.modelOptions.{stage} 必须包含 maxTokens 与 thinking")
                    continue
                max_tokens = option["maxTokens"]
                if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
                    errors.append(f"discovery.modelOptions.{stage}.maxTokens 必须是正整数")
                thinking = option["thinking"]
                thinking_type = thinking.get("type") if isinstance(thinking, Mapping) else None
                if not isinstance(thinking_type, str) or thinking_type not in {"disabled", "enabled"}:
                    errors.append(f"discovery.modelOptions.{stage}.thinking.type 必须为 disabled 或 enabled")
                elif thinking_type == "disabled":
                    if set(thinking) != {"type"} or set(option) != {"maxTokens", "thinking"}:
                        errors.append(f"discovery.modelOptions.{stage}.thinking disabled 时不能附带推理强度")
                elif (set(thinking) != {"type"} or set(option) != {"maxTokens", "thinking", "reasoningEffort"} or
                      not isinstance(option.get("reasoningEffort"), str) or option.get("reasoningEffort") not in {"low", "high", "max"}):
                    errors.append(f"discovery.modelOptions.{stage}.thinking enabled 时必须明确 reasoningEffort")
    return ConfigurationStatus("not_configured", missing, tuple(errors)) if missing or errors else ConfigurationStatus("configured", (), ())


def _positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 1


def _nonnegative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _validate_budget(value: Any, *, label: str, errors: list[str]) -> None:
    if not isinstance(value, Mapping) or set(value) != set(_BUDGET_FIELDS):
        errors.append(f"{label} 必须精确声明全部调用与 token 预算")
        return
    for name in _BUDGET_FIELDS:
        if not _nonnegative_int(value.get(name)):
            errors.append(f"{label}.{name} 必须是非负整数")


def _validate_execution_config_v2(payload: Mapping[str, Any]) -> ConfigurationStatus:
    """Validate the stopped-by-default V3.0.5 operational contract.

    This is deliberately separate from the frozen v1 profile.  A syntactically
    valid profile still needs a matching approved template in the store before
    it can bind a task; this function prevents a missing or empty pack from
    being interpreted as an allow-all filter.
    """
    required = ("executionVersion", "discovery")
    missing = tuple(key for key in required if not _present(payload.get(key)))
    errors: list[str] = []
    if set(payload) != set(required):
        errors.append("执行配置只能包含 executionVersion 与 discovery")
    if payload.get("executionVersion") != "k10-execution-v2":
        errors.append("executionVersion 必须是 k10-execution-v2")
    discovery = payload.get("discovery")
    expected = {"model", "screeningTemplate", "packageLimits", "budgets", "priorityOrder",
                "documentBatchSize", "understandConcurrency", "keyPassageMaxCharacters",
                "networkMaxAttempts", "jsonRepairMaxAttempts", "retryBackoffSeconds",
                "taskSliceSeconds", "completionDeadlineSeconds", "continuationDelaySeconds", "modelOptions"}
    if not isinstance(discovery, Mapping) or set(discovery) != expected:
        errors.append("discovery 必须精确声明模型、模板、资料包、预算、优先级与执行边界")
        return ConfigurationStatus("not_configured", missing, tuple(errors))
    if discovery.get("model") != _DEEPSEEK_V4_PRO:
        errors.append("discovery.model 必须精确为 deepseek-v4-pro")
    template = discovery.get("screeningTemplate")
    template_expected = {"templateId", "revision", "contentSha256", "approvalState", "rules"}
    if not isinstance(template, Mapping) or set(template) != template_expected:
        errors.append("screeningTemplate 必须精确声明 ID、revision、哈希、批准状态与规则")
    else:
        if not isinstance(template.get("templateId"), str) or not template["templateId"].strip():
            errors.append("screeningTemplate.templateId 必须是非空字符串")
        if not _positive_int(template.get("revision")):
            errors.append("screeningTemplate.revision 必须是正整数")
        content_hash = template.get("contentSha256")
        if not isinstance(content_hash, str) or len(content_hash) != 64 or any(c not in "0123456789abcdef" for c in content_hash):
            errors.append("screeningTemplate.contentSha256 必须是 SHA-256 小写十六进制")
        if template.get("approvalState") != "approved":
            errors.append("screeningTemplate 必须显式为 approved")
        rules = template.get("rules")
        if not isinstance(rules, list) or not rules:
            errors.append("screeningTemplate.rules 不得为空，空模板不能全量放行")
        else:
            rule_ids: list[str] = []
            for item in rules:
                required_rule = {"ruleId", "action", "auditReason", "match"}
                allowed_rule = required_rule | {"revision", "matterExtract"}
                if not isinstance(item, Mapping) or not required_rule <= set(item) or set(item) - allowed_rule:
                    errors.append("模板规则必须包含 ruleId、action、auditReason、match，且不能有未知字段")
                    continue
                rule_id = item.get("ruleId")
                if not isinstance(rule_id, str) or not rule_id.strip():
                    errors.append("模板 ruleId 必须非空")
                else:
                    rule_ids.append(rule_id)
                if item.get("action") not in {"exclude", "defer", "protect"}:
                    errors.append("模板 action 只能是 exclude、defer 或 protect")
                if not isinstance(item.get("auditReason"), str) or not item["auditReason"].strip():
                    errors.append("模板 auditReason 必须非空")
                if "revision" in item and not _positive_int(item.get("revision")):
                    errors.append("模板 rule revision 必须是正整数")
                match = item.get("match")
                if not isinstance(match, Mapping) or set(match) != {"allPatterns"} or not isinstance(match.get("allPatterns"), list) or not match["allPatterns"]:
                    errors.append("模板 match 必须精确包含非空 allPatterns")
                else:
                    pattern_ids: list[str] = []
                    for pattern in match["allPatterns"]:
                        if not isinstance(pattern, Mapping) or set(pattern) != {"patternId", "regex"}:
                            errors.append("模板 pattern 必须精确包含 patternId 与 regex")
                            continue
                        if not isinstance(pattern.get("patternId"), str) or not pattern["patternId"].strip():
                            errors.append("模板 patternId 必须非空")
                        else:
                            pattern_ids.append(pattern["patternId"])
                        if not isinstance(pattern.get("regex"), str) or not pattern["regex"].strip():
                            errors.append("模板 regex 必须非空")
                    if len(pattern_ids) != len(set(pattern_ids)):
                        errors.append("模板 patternId 不能重复")
                extract = item.get("matterExtract")
                if extract is not None and (not isinstance(extract, Mapping) or set(extract) != {"pattern", "groups"}
                                            or not isinstance(extract.get("pattern"), str) or not extract["pattern"].strip()
                                            or not isinstance(extract.get("groups"), Mapping)
                                            or set(extract["groups"]) != {"subject", "object", "date", "amount", "stage"}
                                            or any(not isinstance(v, str) or not v.strip() for v in extract["groups"].values())):
                    errors.append("matterExtract 必须精确声明 pattern 与 subject、object、date、amount、stage 分组")
            if len(rule_ids) != len(set(rule_ids)):
                errors.append("模板 ruleId 不能重复")
    limits = discovery.get("packageLimits")
    expected_limits = {"maxDocuments", "maxKeyPassageCharacters", "fullTextEnabled"}
    if not isinstance(limits, Mapping) or set(limits) != expected_limits:
        errors.append("packageLimits 必须精确声明资料包材料和全文许可")
    else:
        for name in ("maxDocuments", "maxKeyPassageCharacters"):
            if not _positive_int(limits.get(name)):
                errors.append(f"packageLimits.{name} 必须是正整数")
        if not isinstance(limits.get("fullTextEnabled"), bool):
            errors.append("packageLimits.fullTextEnabled 必须是布尔值")
        elif limits.get("maxKeyPassageCharacters") != discovery.get("keyPassageMaxCharacters"):
            errors.append("packageLimits.maxKeyPassageCharacters 必须与执行 keyPassageMaxCharacters 一致")
    for name in ("documentBatchSize", "understandConcurrency", "keyPassageMaxCharacters", "networkMaxAttempts",
                 "taskSliceSeconds", "completionDeadlineSeconds", "continuationDelaySeconds"):
        if not _positive_int(discovery.get(name)):
            errors.append(f"discovery.{name} 必须是正整数")
    if not _nonnegative_int(discovery.get("jsonRepairMaxAttempts")):
        errors.append("discovery.jsonRepairMaxAttempts 必须是非负整数")
    backoff = discovery.get("retryBackoffSeconds")
    if (not isinstance(backoff, list) or not backoff or any(not _positive_int(value) for value in backoff)
            or any(later <= earlier for earlier, later in zip(backoff, backoff[1:]))):
        errors.append("discovery.retryBackoffSeconds 必须是严格递增的正整数列表")
    options = discovery.get("modelOptions")
    if not isinstance(options, Mapping) or set(options) != set(_EXECUTION_STAGES):
        errors.append("discovery.modelOptions 必须逐项声明 understand、verify、companyComparison、prioritize")
    else:
        for stage in _EXECUTION_STAGES:
            option = options[stage]
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
    budgets = discovery.get("budgets")
    if not isinstance(budgets, Mapping) or set(budgets) != {"round", "stages", "reservation"}:
        errors.append("budgets 必须精确声明整轮、阶段和单次预留额度")
    else:
        _validate_budget(budgets.get("round"), label="budgets.round", errors=errors)
        stages = budgets.get("stages")
        if not isinstance(stages, Mapping) or set(stages) != set(_V2_SPEND_STAGES):
            errors.append("budgets.stages 必须逐项声明轻量、全文、核验、比较、排序、搜索和重试")
        else:
            for stage in _V2_SPEND_STAGES:
                _validate_budget(stages.get(stage), label=f"budgets.stages.{stage}", errors=errors)
        reservation = budgets.get("reservation")
        if not isinstance(reservation, Mapping) or set(reservation) != set(_V2_SPEND_STAGES):
            errors.append("budgets.reservation 必须逐项声明每次保守预留")
        else:
            for stage in _V2_SPEND_STAGES:
                _validate_budget(reservation.get(stage), label=f"budgets.reservation.{stage}", errors=errors)
    priority = discovery.get("priorityOrder")
    required_priority = ["publishedMajorContrary", "changedKnownFact", "newEvent"]
    if priority != required_priority:
        errors.append("priorityOrder 必须按重大反证、既有事实变化、新事项的顺序显式声明")
    return ConfigurationStatus("not_configured", missing, tuple(errors)) if missing or errors else ConfigurationStatus("configured", (), ())


__all__=["ConfigurationStatus","validate_execution_config","validate_run_config"]
