from __future__ import annotations

import json
from pathlib import Path

from neckline.k10.config import validate_run_config


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
