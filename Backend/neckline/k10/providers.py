"""K10 的显式 DeepSeek V4 Pro provider 解析。

K10 不使用旧策略链的任务路由或默认 provider。运行配置只声明官方 model ID；服务端
从显式数据库路径的已配置连接中找到唯一可用的 DeepSeek 记录，密钥从不离开本模块。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

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
    """Accept only DeepSeek's HTTPS base or its OpenAI-compatible completions endpoint."""
    try:
        parsed = urlsplit(base_url.strip())
    except (TypeError, ValueError):
        return None
    if parsed.scheme.lower() != "https" or parsed.hostname != DEEPSEEK_API_HOST:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    path = parsed.path.rstrip("/")
    if path in {"", "/v1"}:
        path = path + "/chat/completions"
    if path not in {"/chat/completions", "/v1/chat/completions"}:
        return None
    return urlunsplit(("https", DEEPSEEK_API_HOST, path, "", ""))


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
    # ``costLimit: null`` is an explicit approved no-cap policy. It does not prevent
    # token/failure accounting and does not cause a fallback to an unknown model.
    cost = policy.get("costLimit")
    if "costLimit" not in policy or (cost is not None and (
        isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0
    )):
        return None
    return float(timeout), attempts


def resolve_deepseek_v4_pro(
    *, configuration: Mapping[str, Any] | None, task: str, db_path: Path,
    provider_records: Sequence[ProviderRecord] | None = None,
) -> ProviderResolution:
    """Return one explicit V4 Pro client or an auditable ``not_configured`` resolution.

    No actual request is made here. A duplicate matching connection is deliberately an error:
    silently choosing by database order could send production evidence to an unintended account.
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
        return ProviderResolution("not_configured", None, None, "模型超时、重试或费用策略未明确配置")
    timeout, attempts = policy
    records = list(provider_records) if provider_records is not None else list_providers(db_path=db_path)
    matches = [
        record for record in records
        if record.enabled and bool((record.api_key or "").strip()) and record.model == DEEPSEEK_V4_PRO
        and _endpoint(record.base_url) is not None
    ]
    if not matches:
        return ProviderResolution("not_configured", None, None, "未配置可用的 DeepSeek V4 Pro 连接")
    if len(matches) != 1:
        return ProviderResolution("not_configured", None, None, "DeepSeek V4 Pro 连接不唯一，请在设置中保留一个启用连接")
    record = matches[0]
    endpoint = _endpoint(record.base_url)
    assert endpoint is not None
    provider = MeteredProvider(
        ledger_db=db_path, ledger_task=task,
        api_key=record.api_key, model=DEEPSEEK_V4_PRO, name=record.name, api_url=endpoint,
        has_web_search=False, search_engine=None, read_timeout=timeout, use_streaming=False,
    )
    # The low-level client already retries, but the count is only consumed from the explicit
    # K10 policy; it is never inherited from a legacy router default.
    provider.max_attempts = attempts
    return ProviderResolution("configured", provider, record.name, None)


__all__ = ["DEEPSEEK_API_HOST", "DEEPSEEK_V4_PRO", "ProviderResolution", "resolve_deepseek_v4_pro"]
