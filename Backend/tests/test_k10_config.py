from __future__ import annotations

import json
from pathlib import Path

from neckline import config as runtime_config
from neckline.k10.config import validate_execution_config, validate_run_config


def _base() -> dict:
    return json.loads((Path(__file__).parents[1] / "neckline/config/k10-v1.4.json").read_text())


def test_missing_config_is_explicit_not_configured_without_defaults():
    status=validate_run_config(None,scope="candidate")
    assert status.state == "not_configured" and "configVersion" in status.missing


def test_v14_pack_is_ready_for_all_live_scopes_with_no_cost_cap():
    payload=_base()
    for scope in ("candidate","discovery","analysis","morning","evaluation"):
        assert validate_run_config(payload,scope=scope).ready
    assert set(payload["modelRoutes"].values()) == {"deepseek-v4-pro"}
    assert all(item["costLimit"] is None for item in payload["taskPolicies"].values())


def test_execution_pack_is_explicit_and_ready_without_strategy_defaults():
    payload = json.loads((Path(__file__).parents[1] / "neckline/config/k10-execution-v1.json").read_text())
    assert validate_execution_config(payload).ready
    discovery = payload["discovery"]
    assert discovery["documentBatchSize"] == 24
    assert discovery["understandConcurrency"] == 12
    assert discovery["modelOptions"]["understand"]["maxTokens"] == 8192
    payload["discovery"]["understandConcurrency"] = 0
    assert not validate_execution_config(payload).ready


def test_v14_rejects_removed_price_route_and_any_extra_hard_rule():
    payload=_base(); payload["modelRoutes"]={**payload["modelRoutes"],"price":"deepseek-v4-pro"}
    assert not validate_run_config(payload,scope="discovery").ready
    payload=_base(); payload["hardExclusions"]={**payload["hardExclusions"],"marketCap":"small"}
    assert not validate_run_config(payload,scope="candidate").ready


def test_evaluation_requires_explicit_fixed_window_contract():
    payload=_base(); payload["evaluationPolicy"]={"version":"other"}
    assert not validate_run_config(payload,scope="evaluation").ready


def test_evaluation_requires_explicit_market_collection_schedule_without_default():
    payload=_base(); del payload["marketCollection"]
    missing=validate_run_config(payload,scope="evaluation")
    assert not missing.ready and "marketCollection" in missing.missing
    payload=_base(); payload["marketCollection"]={"retryIntervalSeconds":7200,"retryUntilMinutesAfterClose":120}
    assert not validate_run_config(payload,scope="evaluation").ready
    payload=_base(); payload["marketCollection"]={"retryIntervalSeconds":True,"retryUntilMinutesAfterClose":120}
    assert not validate_run_config(payload,scope="evaluation").ready


def test_model_tasks_require_explicit_timeout_and_model_retry_policy():
    payload=_base(); del payload["taskPolicies"]["discovery"]["modelMaxAttempts"]
    assert not validate_run_config(payload,scope="discovery").ready
    payload=_base(); del payload["taskPolicies"]["analysis"]["timeoutSeconds"]
    assert not validate_run_config(payload,scope="analysis").ready


def test_legacy_source_shape_remains_readable_for_non_ingestion_scopes_only():
    payload = _base()
    payload["sourceAdapters"] = ["legacy-b33-source"]
    assert validate_run_config(payload, scope="analysis").ready
    assert validate_run_config(payload, scope="evaluation").ready
    assert not validate_run_config(payload, scope="candidate").ready
    assert not validate_run_config(payload, scope="discovery").ready
    assert not validate_run_config(payload, scope="morning").ready


def test_environment_config_binding_is_explicit_and_rejects_illegal_revision(monkeypatch):
    monkeypatch.setenv("K10_CONFIG_ID", "k10-production")
    monkeypatch.setenv("K10_CONFIG_REVISION", "2")
    configured = runtime_config._load_settings()
    assert configured.k10_config_id == "k10-production"
    assert configured.k10_config_revision == 2
    assert configured.k10_config_binding_error is None

    monkeypatch.setenv("K10_CONFIG_REVISION", "02")
    invalid = runtime_config._load_settings()
    assert invalid.k10_config_id == "k10-production"
    assert invalid.k10_config_revision is None
    assert invalid.k10_config_binding_error == "K10_CONFIG_REVISION 必须是正整数"

    monkeypatch.setenv("K10_EXECUTION_CONFIG_ID", "execution-production")
    monkeypatch.setenv("K10_EXECUTION_CONFIG_REVISION", "1")
    execution = runtime_config._load_settings()
    assert execution.k10_execution_config_id == "execution-production"
    assert execution.k10_execution_config_revision == 1
    assert execution.k10_execution_config_binding_error is None
