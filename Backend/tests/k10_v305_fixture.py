"""Explicit V3.0.5 test-only configuration builders.

They deliberately use small deterministic numbers and are never a production
configuration source.  Production remains closed until a user-approved pack is
written through the operational path.
"""
from __future__ import annotations

from pathlib import Path

from neckline.k10 import store


SPEND_FIELDS = ("maxModelCalls", "maxInputTokens", "maxOutputTokens", "maxTotalTokens",
                "maxFullTextCalls", "maxRetries", "maxSearchRequests", "maxSearchCredits")
SPEND_STAGES = ("lightweight", "fullText", "verify", "map", "companyComparison", "classify", "prioritize",
                "morning", "analysisPro", "analysisCon", "search", "retry")


def limits(value: int) -> dict[str, int]:
    return {field: value for field in SPEND_FIELDS}


def rules() -> list[dict]:
    return [{"ruleId": "fixture-correction", "revision": 1, "action": "protect", "auditReason": "fixture",
             "match": {"allPatterns": [{"patternId": "fixture-correction", "regex": "更正"}]}}]


def execution_payload(*, template_id: str, template_revision: int, template_hash: str, max_model_calls: int = 4) -> dict:
    round_limits = limits(100)
    round_limits["maxModelCalls"] = max_model_calls
    return {
        "executionVersion": "k10-execution-v2",
        "discovery": {
            "model": "deepseek-v4-pro",
            "screeningTemplate": {"templateId": template_id, "revision": template_revision,
                                  "contentSha256": template_hash, "approvalState": "approved", "rules": rules()},
            "packageLimits": {"maxDocuments": 4, "maxKeyPassageCharacters": 800, "fullTextEnabled": True},
            "documentBatchSize": 4, "understandConcurrency": 1, "keyPassageMaxCharacters": 800,
            "networkMaxAttempts": 1, "jsonRepairMaxAttempts": 0, "retryBackoffSeconds": [1],
            "taskSliceSeconds": 30, "completionDeadlineSeconds": 7200, "continuationDelaySeconds": 1,
            "modelOptions": {
                "understand": {"maxTokens": 20, "thinking": {"type": "disabled"}},
                "verify": {"maxTokens": 20, "thinking": {"type": "enabled"}, "reasoningEffort": "high"},
                "companyComparison": {"maxTokens": 20, "thinking": {"type": "enabled"}, "reasoningEffort": "high"},
                "prioritize": {"maxTokens": 20, "thinking": {"type": "enabled"}, "reasoningEffort": "high"},
            },
            "budgets": {"round": round_limits, "stages": {stage: limits(100) for stage in SPEND_STAGES},
                        "reservation": {stage: limits(100) for stage in SPEND_STAGES}},
            "priorityOrder": ["publishedMajorContrary", "changedKnownFact", "newEvent"],
        },
    }


def append_approved_execution_profile(*, db_path: Path, created_at: str, config_id: str = "v305-fixture",
                                      max_model_calls: int = 4) -> tuple[str, int]:
    template_id = f"{config_id}-template"
    revision = store.append_screening_template(
        template_id=template_id, payload={"templateVersion": "k10-screening-template-v1", "rules": rules()},
        approval_state="approved", approved_at=created_at, created_at=created_at, db_path=db_path,
    )
    template = store.read_screening_template(template_id=template_id, revision=revision, db_path=db_path)
    assert template is not None
    execution_revision = store.append_execution_config(
        config_id=config_id,
        payload=execution_payload(template_id=template_id, template_revision=revision,
                                  template_hash=str(template["contentSha256"]), max_model_calls=max_model_calls),
        created_at=created_at, db_path=db_path,
    )
    return config_id, execution_revision
