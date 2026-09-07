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
    "morning": ("configVersion", "modelRoutes", "taskPolicies"),
    "evaluation": ("configVersion", "evaluationPolicy", "marketCollection", "taskPolicies"),
}
_K10_MODEL_TASKS = ("discovery", "analysis", "morning")
_DEEPSEEK_V4_PRO = "deepseek-v4-pro"
_ROUTE_REQUIRED_BY_SCOPE = {"discovery": "discovery", "analysis": "analysis", "morning": "morning"}


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
    if adapters is not None and not isinstance(adapters, list): errors.append("sourceAdapters 必须是列表")
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


__all__=["ConfigurationStatus","validate_run_config"]
