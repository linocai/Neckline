"""Resolve an explicit BYOK Chat Completions connection without making a request."""

from __future__ import annotations

from dataclasses import dataclass
import math
import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Mapping, Sequence
from neckline.llm.connection import chat_endpoint, model_name
from neckline.db import connection

from neckline.llm.openai_compat import OpenAICompatProvider
from neckline.settings_store import ProviderRecord, list_providers
from .metering import MeteredProvider


DEEPSEEK_V4_PRO = "deepseek-v4-pro"
DEEPSEEK_API_HOST = "api.deepseek.com"
_TASKS = frozenset({"discovery", "analysis", "morning"})


@dataclass(frozen=True)
class ProviderResolution:
    state: str
    provider: OpenAICompatProvider | None
    provider_name: str | None
    error: str | None


def _endpoint(base_url: str) -> str | None:
    try:
        return chat_endpoint(base_url)
    except ValueError:
        return None


def runtime_execution_profile(profile, provider):
    """Keep approved parameters intact; fingerprint the separate user-selected connection."""
    binding = getattr(provider, "runtime_binding", None)
    if not isinstance(profile, Mapping) or not isinstance(binding, Mapping):
        return profile
    return {**profile, "runtimeProvider": dict(binding)}


def _policy(configuration: Mapping[str, Any], task: str) -> tuple[float, int] | None:
    policies = configuration.get("taskPolicies")
    policy = policies.get(task) if isinstance(policies, Mapping) else None
    if not isinstance(policy, Mapping):
        return None
    timeout = policy.get("timeoutSeconds")
    attempts = policy.get("modelMaxAttempts")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        return None
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        return None
    return float(timeout), attempts


def resolve_deepseek_v4_pro(
    *, configuration: Mapping[str, Any] | None, task: str, db_path: Path,
    provider_records: Sequence[ProviderRecord] | None = None, task_id: str | None = None,
) -> ProviderResolution:
    """Resolve the active connection; workers durably pin it before any paid step.

    Existing task bindings keep their original connection even after another profile is
    activated. Key rotation is allowed only while the bound endpoint/model still match.
    """
    if task not in _TASKS:
        raise ValueError(f"未知 K10 模型任务：{task}")
    if not isinstance(configuration, Mapping):
        return ProviderResolution("not_configured", None, None, "K10 模型配置缺失")
    routes = configuration.get("modelRoutes")
    if not isinstance(routes, Mapping) or routes.get(task) != DEEPSEEK_V4_PRO:
        return ProviderResolution("not_configured", None, None, f"{task} 必须明确使用 {DEEPSEEK_V4_PRO}")
    policy = _policy(configuration, task)
    if policy is None:
        return ProviderResolution("not_configured", None, None, "模型超时或重试配置不完整")
    timeout, attempts = policy
    # Settings mutations and initial task binding serialize on the same database.
    with connection(db_path) if task_id is not None else nullcontext(None) as conn:
        if conn is not None:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT checkpoint_json FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                return ProviderResolution("not_configured", None, None, "模型任务不存在")
            checkpoint = json.loads(row[0])
            binding = checkpoint.get("providerBinding")
            if binding is None and conn.execute(
                "SELECT 1 FROM k10_external_attempts WHERE task_id=? LIMIT 1", (task_id,)
            ).fetchone() is not None:
                return ProviderResolution("not_configured", None, None, "旧任务未记录模型连接，无法安全恢复；请新建任务")
        else:
            binding = None
        records = list(provider_records) if provider_records is not None else list_providers(db_path=db_path)
        if binding is not None:
            if not isinstance(binding, dict) or set(binding) != {"name", "endpoint", "model"}:
                return ProviderResolution("not_configured", None, None, "任务模型连接记录无效")
            matches = [r for r in records if r.name == binding["name"]]
            if len(matches) != 1 or _endpoint(matches[0].base_url) != binding["endpoint"] or matches[0].model != binding["model"]:
                return ProviderResolution("not_configured", None, None, "任务原连接已变更或删除；请恢复原连接或新建任务")
        else:
            matches = [r for r in records if r.enabled]
        if len(matches) != 1:
            message = "模型连接不唯一，请在设置中选择一个当前连接" if matches else "请在设置中启用一个模型连接"
            return ProviderResolution("not_configured", None, None, message)
        record = matches[0]
        endpoint = _endpoint(record.base_url)
        try:
            selected_model = model_name(record.model)
        except ValueError:
            selected_model = None
        if endpoint is None or selected_model is None or not (record.api_key or "").strip():
            return ProviderResolution("not_configured", None, record.name, "模型连接缺少有效端点、模型名称或 API Key")
        binding = {"name": record.name, "endpoint": endpoint, "model": selected_model}
        if conn is not None and checkpoint.get("providerBinding") is None:
            checkpoint["providerBinding"] = binding
            conn.execute("UPDATE k10_tasks SET checkpoint_json=? WHERE task_id=?",
                         (json.dumps(checkpoint, ensure_ascii=False), task_id))
    provider = MeteredProvider(
        ledger_db=db_path, ledger_task=task,
        api_key=record.api_key, model=selected_model, name=record.name, api_url=endpoint,
        has_web_search=False, search_engine=None, read_timeout=timeout, use_streaming=False,
    )
    # The low-level client already retries, but the count is only consumed from the explicit
    # K10 policy; it is never inherited from a legacy router default.
    provider.max_attempts = attempts
    provider.runtime_binding = binding
    return ProviderResolution("configured", provider, record.name, None)


__all__ = ["DEEPSEEK_API_HOST", "DEEPSEEK_V4_PRO", "ProviderResolution", "resolve_deepseek_v4_pro"]
