from __future__ import annotations

from neckline.k10.providers import DEEPSEEK_V4_PRO, resolve_deepseek_v4_pro
from neckline.settings_store import ProviderRecord


def _record(*, name="deepseek", base_url="https://api.deepseek.com", model=DEEPSEEK_V4_PRO,
            key="sk-test", enabled=True):
    return ProviderRecord(1, name, base_url, model, key, False, None, None, enabled, "created", "updated")


def _config(**policy):
    return {
        "modelRoutes": {"analysis": DEEPSEEK_V4_PRO},
        "taskPolicies": {"analysis": {"maxAttempts": 3, "modelMaxAttempts": 3, "timeoutSeconds": 90, "costLimit": None, **policy}},
    }


def test_resolves_one_official_deepseek_v4_pro_connection_with_explicit_policy(tmp_path):
    result = resolve_deepseek_v4_pro(
        configuration=_config(), task="analysis", db_path=tmp_path / "isolated.db", provider_records=[_record()],
    )
    assert result.state == "configured"
    assert result.provider is not None
    assert result.provider.model == DEEPSEEK_V4_PRO
    assert result.provider.api_url == "https://api.deepseek.com/chat/completions"
    assert result.provider.read_timeout == 90
    assert result.provider.max_attempts == 3
    assert result.provider.has_web_search is False


def test_only_official_https_deepseek_endpoint_and_exact_model_are_usable(tmp_path):
    for record in (
        _record(base_url="http://api.deepseek.com"),
        _record(base_url="https://other.example.com"),
        _record(base_url="https://api.deepseek.com/other"),
        _record(model="deepseek-chat"),
        _record(key=None),
        _record(enabled=False),
    ):
        result = resolve_deepseek_v4_pro(
            configuration=_config(), task="analysis", db_path=tmp_path / "isolated.db", provider_records=[record],
        )
        assert result.state == "not_configured"
        assert result.provider is None


def test_provider_selection_never_chooses_ambiguous_database_row(tmp_path):
    result = resolve_deepseek_v4_pro(
        configuration=_config(), task="analysis", db_path=tmp_path / "isolated.db",
        provider_records=[_record(name="first"), _record(name="second")],
    )
    assert result.state == "not_configured"
    assert "不唯一" in result.error


def test_model_route_and_engineering_policy_are_explicit_and_cost_limit_null_is_allowed(tmp_path):
    missing_route = _config(); missing_route["modelRoutes"]["analysis"] = "deepseek-chat"
    assert resolve_deepseek_v4_pro(
        configuration=missing_route, task="analysis", db_path=tmp_path / "isolated.db", provider_records=[_record()],
    ).state == "not_configured"
    missing_retry = _config(); del missing_retry["taskPolicies"]["analysis"]["modelMaxAttempts"]
    assert resolve_deepseek_v4_pro(
        configuration=missing_retry, task="analysis", db_path=tmp_path / "isolated.db", provider_records=[_record()],
    ).state == "not_configured"
    assert resolve_deepseek_v4_pro(
        configuration=_config(costLimit=None), task="analysis", db_path=tmp_path / "isolated.db", provider_records=[_record()],
    ).state == "configured"
