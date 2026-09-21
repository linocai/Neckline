"""B76 report-delivery identity, gap projection, and deterministic hashes.

This module deliberately contains no database or provider access.  The pipeline
uses it to turn durable discovery facts into a reader-safe delivery manifest;
the store writes that manifest in the same transaction as visible report rows.
"""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Mapping, Sequence


LEGACY_REPORT_DELIVERY_CONTRACT = "k10-report-delivery-3.4.0-b76"
LEGACY_RESEARCH_CONTRACT = "k10-research-3.4.0-b76"
REPORT_DELIVERY_CONTRACT = "k10-report-delivery-3.5.0-b78"
RESEARCH_CONTRACT = "k10-research-3.5.0-b78"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def runtime_contract() -> dict[str, str]:
    return {"reportDelivery": REPORT_DELIVERY_CONTRACT, "research": RESEARCH_CONTRACT}


def is_b76_runtime_contract(value: object) -> bool:
    """Whether a task has one of the explicitly supported frozen contracts.

    The name is retained for old callers.  It deliberately accepts B76 only as
    a read/recovery boundary and returns B78 from ``runtime_contract`` for all
    new work; callers must never manufacture a new B76 binding.
    """
    return isinstance(value, Mapping) and dict(value) in (
        runtime_contract(),
        {"reportDelivery": LEGACY_REPORT_DELIVERY_CONTRACT,
         "research": LEGACY_RESEARCH_CONTRACT},
    )


def is_current_runtime_contract(value: object) -> bool:
    """New workers execute only B78 tasks; historical bindings are read-only."""
    return isinstance(value, Mapping) and dict(value) == runtime_contract()


def delivery_gap(*, stage: str, unit_kind: str, unit_id: str, reason_code: str,
                 message: str, source_refs: Sequence[Mapping[str, Any]] = (),
                 event_ids: Sequence[str] = (), company_codes: Sequence[str] = (),
                 company_scope_known: bool) -> dict[str, Any]:
    """Return a stable, safe-to-display gap without provider/private content."""
    source_refs = [dict(item) for item in source_refs if isinstance(item, Mapping)]
    event_ids = sorted({str(item) for item in event_ids if isinstance(item, str) and item})
    company_codes = sorted({str(item) for item in company_codes if isinstance(item, str) and item})
    identity = {
        "stage": stage, "unitKind": unit_kind, "unitId": unit_id,
        "reasonCode": reason_code, "sourceRefs": source_refs,
        "eventIds": event_ids, "companyCodes": company_codes,
        "companyScopeKnown": company_scope_known,
    }
    return {
        "gapId": "gap_" + digest(identity)[:32], "stage": stage,
        "unitKind": unit_kind, "unitId": unit_id, "reasonCode": reason_code,
        "message": message, "sourceRefs": source_refs, "eventIds": event_ids,
        "companyCodes": company_codes, "companyScopeKnown": company_scope_known,
    }


def delivery_manifest(*, outcome: str, ranking_scope: str, counts: Mapping[str, int],
                      gaps: Sequence[Mapping[str, Any]], input_manifest: object,
                      eligible_set: object, ranking_input: object | None) -> dict[str, Any]:
    """Build the typed public manifest and enforce its non-deceptive invariants."""
    if outcome not in {"complete", "partial", "failed"}:
        raise ValueError("B76 delivery outcome 无效")
    if ranking_scope not in {"all_processed", "completed_subset", "none"}:
        raise ValueError("B76 delivery ranking scope 无效")
    expected = {"titleInput", "titleProcessed", "titleFailed", "titleUnprocessed",
                "eventInput", "eventProcessed", "eventFailed", "eventUnprocessed",
                "comparableCompanies", "publishedCompanies"}
    if set(counts) != expected or any(isinstance(value, bool) or not isinstance(value, int) or value < 0
                                      for value in counts.values()):
        raise ValueError("B76 delivery counts 无效")
    if counts["titleProcessed"] + counts["titleFailed"] + counts["titleUnprocessed"] != counts["titleInput"]:
        raise ValueError("B76 title counts 未对账")
    if counts["eventProcessed"] + counts["eventFailed"] + counts["eventUnprocessed"] != counts["eventInput"]:
        raise ValueError("B76 event counts 未对账")
    clean_gaps = [dict(item) for item in gaps]
    if outcome == "complete" and (clean_gaps or ranking_scope != "all_processed"):
        raise ValueError("完整报告不得包含执行缺口或子集排序")
    if outcome == "partial" and not clean_gaps:
        raise ValueError("部分报告必须说明执行缺口")
    if outcome == "failed" and ranking_scope != "none":
        raise ValueError("失败报告不得声明排序范围")
    if ranking_scope == "none" and ranking_input is not None:
        raise ValueError("无排序报告不得携带排序输入")
    if ranking_scope != "none" and ranking_input is None:
        raise ValueError("已排序报告缺少冻结排序输入")
    return {
        "contractVersion": REPORT_DELIVERY_CONTRACT, "outcome": outcome,
        "rankingScope": ranking_scope, "counts": dict(counts), "gaps": clean_gaps,
        "inputManifestSha256": digest(input_manifest),
        "eligibleSetSha256": digest(eligible_set),
        "rankingInputSha256": None if ranking_input is None else digest(ranking_input),
    }


__all__ = ["LEGACY_REPORT_DELIVERY_CONTRACT", "LEGACY_RESEARCH_CONTRACT", "REPORT_DELIVERY_CONTRACT", "RESEARCH_CONTRACT", "canonical_json", "delivery_gap",
           "delivery_manifest", "digest", "is_b76_runtime_contract", "is_current_runtime_contract", "runtime_contract"]
