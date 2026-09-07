"""Validate the model's pre-publication K10-v1.4 opportunity classification.

Evidence revisions and repeated scans do not create a fresh observation window.  The
classification is frozen with the discovery draft, before any outcome is available.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


NEW_KINDS = frozenset({"initial", "material_stage", "independent"})
UPDATE_KINDS = frozenset({"continuation", "needs_review", "invalidated"})


def normalize_catalyst_stage(stage_key: str) -> str:
    """Return the stable identity used for a catalyst stage.

    A stage label is model output, so whitespace or letter case cannot be allowed to
    create another D1/D2 window for the same stage.
    """
    if not isinstance(stage_key, str) or not (normalized := stage_key.strip().casefold()):
        raise ValueError("机会阶段不能为空")
    return normalized


def validate_comparison(comparison: Mapping[str, Any]) -> None:
    if comparison.get("role") not in {"primary", "alternative", "tied"}:
        raise ValueError("公司比较必须明确主推、备选或并列")
    for key in ("priorityReason", "gap", "rankChangeConditions", "twoDayReason"):
        if not isinstance(comparison.get(key), str) or not comparison[key].strip():
            raise ValueError(f"公司比较缺少 {key}")


def validate_event_comparison(
    *, summary: Any, comparisons: Mapping[str, Any], company_codes: Sequence[str],
) -> None:
    """Validate one coherent ordering for every company mapped to an event.

    A per-company answer cannot establish that A outranks B when B is evaluated in
    a separate call.  The comparison contract therefore carries all peers in one
    model result and makes the editorial order inspectable before publication.
    """
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("事件比较缺少共同事实说明")
    expected = tuple(company_codes)
    if len(expected) != len(set(expected)):
        raise ValueError("同一事件的公司映射不得重复")
    if not isinstance(comparisons, Mapping) or set(comparisons) != set(expected):
        raise ValueError("事件比较必须恰好覆盖每家公司一次")

    roles: dict[str, str] = {}
    ranks: dict[str, int] = {}
    for company_code in expected:
        item = comparisons[company_code]
        if not isinstance(item, Mapping):
            raise ValueError("事件比较公司项无效")
        if not isinstance(item.get("summary"), str) or not item["summary"].strip():
            raise ValueError("事件比较缺少公司的具体理由")
        differences = item.get("differences")
        if not isinstance(differences, Mapping):
            raise ValueError("事件比较缺少公司差异")
        validate_comparison(differences)
        rank = item.get("rank")
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            raise ValueError("事件比较排序必须为正整数")
        roles[company_code] = str(differences["role"])
        ranks[company_code] = rank

    primary = [code for code in expected if roles[code] == "primary"]
    alternatives = [code for code in expected if roles[code] == "alternative"]
    tied = [code for code in expected if roles[code] == "tied"]
    if len(primary) > 1 or (alternatives and len(primary) != 1):
        raise ValueError("同一事件只能有一个主推，备选必须有主推")
    if primary and ranks[primary[0]] != 1:
        raise ValueError("同一事件主推必须为第 1 名")
    if tied and len({ranks[code] for code in tied}) != 1:
        raise ValueError("差异不足的并列公司必须共享同一名次")
    for rank in set(ranks.values()):
        role_set = {roles[code] for code in expected if ranks[code] == rank}
        if "tied" in role_set and len(role_set) != 1:
            raise ValueError("并列名次不得与主推或备选混用")
        if "tied" not in role_set and len(role_set) != 1:
            raise ValueError("同一事件排序角色不一致")
        if "tied" not in role_set and sum(ranks[code] == rank for code in expected) != 1:
            raise ValueError("非并列名次只能对应一家公司")
    if set(ranks.values()) != set(range(1, max(ranks.values()) + 1)):
        raise ValueError("事件比较名次必须连续")


def validate_classification(
    raw: Mapping[str, Any], *, canonical_key: str, stage_key: str,
    company_code: str, previous: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or raw.get("kind") not in NEW_KINDS | UPDATE_KINDS | {"background"}:
        raise ValueError("机会分类必须在推荐前明确")
    result = {key: raw.get(key) for key in (
        "kind", "relatedOpportunityId", "reason", "newFacts", "changedJudgment", "twoDayReason"
    )}
    kind = result["kind"]
    if not isinstance(result.get("reason"), str) or not result["reason"].strip():
        raise ValueError("机会分类缺少判断依据")
    related_id = result.get("relatedOpportunityId")
    related = next((old for old in previous if old.get("opportunityId") == related_id), None)
    if related_id is not None and (related is None or related.get("companyCode") != company_code):
        raise ValueError("机会分类引用了未知或不同公司的旧机会")
    # A first-seen item can be genuinely unresolved: retain its event and evidence as pending
    # work without inventing a predecessor or publishing a formal opportunity.  All other
    # update/stage meanings describe an existing opportunity and therefore require one.
    if kind in {"continuation", "invalidated", "material_stage"} and related is None:
        raise ValueError("延续、反证与实质新阶段必须关联原机会")
    if kind in NEW_KINDS:
        for key in ("newFacts", "twoDayReason"):
            if not isinstance(result.get(key), str) or not result[key].strip():
                raise ValueError(f"新机会缺少发布时的 {key}")
    if kind == "material_stage" and (
        not isinstance(result.get("changedJudgment"), str) or not result["changedJudgment"].strip()
    ):
        raise ValueError("新阶段未说明实质改变的关键判断")
    # The source event's identity is separate from its append-only evidence revision.
    # A substantive stage has a stable key; repeating it can never restart a window.
    normalized_stage = normalize_catalyst_stage(stage_key)
    semantic_key = (f"{canonical_key}\x1f{company_code}\x1f{normalized_stage}"
                    if kind == "material_stage" else f"{canonical_key}\x1f{company_code}")
    exact = next((old for old in previous if old.get("opportunityKey") == semantic_key), None)
    same_event = [old for old in previous if old.get("companyCode") == company_code
                  and old.get("canonicalKey") == canonical_key]
    same_stage = [old for old in same_event
                  if isinstance(old.get("catalystStage"), str)
                  and old["catalystStage"].strip()
                  and normalize_catalyst_stage(old["catalystStage"]) == normalized_stage]
    if kind == "initial" and same_event and exact is None:
        raise ValueError("已有事件不能再次分类为首次机会")
    if kind == "material_stage" and same_stage:
        # The old initial opportunity deliberately has a shorter key, so comparing
        # only semantic_key would let its own stage reopen as a new material stage.
        # The model already supplied the intended predecessor; do not choose one
        # arbitrarily when the historical identity is ambiguous.
        if related not in same_stage:
            raise ValueError("同一催化阶段不得关联为新的实质阶段")
        result.update(kind="continuation", relatedOpportunityId=related["opportunityId"],
                      reason="相同催化阶段已推荐；本次证据追加到原机会。" + result["reason"])
    if kind in NEW_KINDS and exact is not None:
        result.update(kind="continuation", relatedOpportunityId=exact["opportunityId"],
                      reason="相同催化/阶段已推荐；本次证据追加到原机会。" + result["reason"])
    result["opportunityKey"] = semantic_key
    return result
