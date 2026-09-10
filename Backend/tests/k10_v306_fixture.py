"""Approved V3 execution fixture shared by runtime and worker regressions."""
from __future__ import annotations

import json
from pathlib import Path

from neckline.k10 import store


def policy_content() -> dict:
    return json.loads((Path(__file__).resolve().parents[1] / "neckline" / "config" / "k10-title-triage-policy-v1.json").read_text())


def execution_payload(*, policy_id: str = "v306-title-policy") -> tuple[dict, dict]:
    policy = policy_content()
    policy_hash = store._hash(policy)
    option = {"maxTokens": 128, "thinking": {"type": "disabled"}}
    stages = ("titleBatch", "titleReconcile", "understand", "verify", "companyComparison", "prioritize",
              "morning", "analysisPro", "analysisCon", "investigation")
    return policy, {
        "executionVersion": "k10-execution-v4",
        "discovery": {
            "model": "deepseek-v4-pro",
            "titleTriagePolicy": {"policyId": policy_id, "revision": 1, "contentSha256": policy_hash,
                                  "approvalState": "approved", "content": policy},
            "titleBatchSize": 2, "titleTriageConcurrency": 1, "deepReadConcurrency": 1,
            "networkMaxAttempts": 2, "jsonRepairMaxAttempts": 1, "retryBackoffSeconds": [1],
            "taskSliceSeconds": 60, "completionDeadlineSeconds": 7200, "continuationDelaySeconds": 1,
            "investigationPromptContractRevision": "k10-investigation-v1",
            "modelOptions": {stage: dict(option) for stage in stages},
        },
    }


def append_approved_execution_profile(*, db_path: Path, created_at: str, config_id: str = "v306-fixture") -> tuple[str, int]:
    policy_id = f"{config_id}-policy"
    policy, payload = execution_payload(policy_id=policy_id)
    store.append_title_triage_policy(policy_id=policy_id, content=policy, approval_state="approved",
                                     created_at=created_at, approved_at=created_at, db_path=db_path)
    revision = store.append_execution_config(config_id=config_id, payload=payload, created_at=created_at, db_path=db_path)
    return config_id, revision
