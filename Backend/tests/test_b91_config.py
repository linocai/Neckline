"""Reject an unusable B90 scheduling pack before a report can be queued."""
import json
from pathlib import Path
import sqlite3

import pytest

from neckline.k10 import cli, store
from neckline.k10.config import validate_execution_config
from tests import v340_acceptance_fixture as base


def _profile():
    payload = json.loads((Path(__file__).parents[1] / "neckline/config/k10-execution-v4.json").read_text())
    payload["discovery"]["investigationPromptContractRevision"] = "k10-research-3.6.0-b90"
    for field in ("reportInputContract", "collectionSourceKeys", "collectedInputBootstrapAt"):
        payload["discovery"].pop(field, None)
    return payload


@pytest.mark.parametrize("revision, capacity, ready", [
    ("k10-research-3.6.0-b90", 1, False),
    ("k10-research-3.6.0-b90", 2, True),
    ("k10-research-3.6.0-b90", 6, True),
    ("k10-investigation-v2", 1, True),
])
def test_parallel_pack_validation_matches_admission_without_changing_old_contracts(revision, capacity, ready):
    payload = _profile()
    payload["discovery"].update(investigationPromptContractRevision=revision, deepReadConcurrency=capacity)
    assert validate_execution_config(payload).ready is ready
    assert payload["discovery"]["deepReadConcurrency"] == capacity


def test_invalid_parallel_pack_is_visible_and_rejected_at_real_producers(tmp_path):
    database = tmp_path / "invalid-pack.sqlite"
    config_id, config_revision, execution_id, _ = base.seed_database(database)
    payload = _profile()
    payload["discovery"]["deepReadConcurrency"] = 1
    profile_file = tmp_path / "invalid-pack.json"
    profile_file.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="deepReadConcurrency"):
        cli.main(["configure-execution", "--db", str(database), "--config-id", execution_id,
                  "--file", str(profile_file)])
    # An old/directly imported immutable row may already exist.  Both public
    # read readiness and enqueue must reject that exact explicit binding.
    invalid_revision = store.append_execution_config(
        config_id=execution_id, payload=payload, created_at=base.NOW.isoformat(), db_path=database)
    with base.actual_api(database, config_id=config_id, config_revision=config_revision,
                         execution_id=execution_id, execution_revision=invalid_revision) as client:
        response = client.get("/api/v1/k10/configuration")
        assert response.status_code == 200
        scopes = {scope["scope"]: scope for scope in response.json()["scopes"]}
        for scope in ("candidate", "discovery"):
            assert scopes[scope]["state"] == "not_configured"
            assert any("deepReadConcurrency" in error for error in scopes[scope]["errors"])
    for kind in ("evening", "morning"):
        with pytest.raises(RuntimeError, match="执行配置修订不存在或未就绪"):
            cli.main(["enqueue", "--db", str(database), "--kind", kind, "--trading-day", base.DAY.isoformat(),
                      "--config-id", config_id, "--config-revision", str(config_revision),
                      "--execution-config-id", execution_id, "--execution-config-revision", str(invalid_revision)])
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM k10_tasks").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM k10_scans").fetchone() == (0,)
