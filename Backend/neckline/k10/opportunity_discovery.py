"""Validate the model's pre-publication K10-v1.4 opportunity classification.

Evidence revisions and repeated scans do not create a fresh observation window.  The
classification is frozen with the discovery draft, before any outcome is available.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


NEW_KINDS = frozenset({"initial", "material_stage", "independent"})
UPDATE_KINDS = frozenset({"continuation", "needs_review", "invalidated"})
PUBLISHABLE_ROLES = frozenset({"primary", "alternative", "tied"})
NONPUBLISHABLE_ROLES = frozenset({"pending", "excluded"})
COMPARISON_ROLES = PUBLISHABLE_ROLES | NONPUBLISHABLE_ROLES
VERIFICATION_STATUSES = frozenset({"verified", "partially_supported", "unverified", "contradicted"})


class ComparisonValidationError(ValueError):
    """A stable, non-sensitive reason why a comparison cannot be published."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_catalyst_stage(stage_key: str) -> str:
    """Return the stable identity used for a catalyst stage.

    A stage label is model output, so whitespace or letter case cannot be allowed to
    create another D1/D2 window for the same stage.
    """
    if not isinstance(stage_key, str) or not (normalized := stage_key.strip().casefold()):
        raise ValueError("机会阶段不能为空")
    return normalized


def validate_evidence_disclosure(disclosure: Mapping[str, Any]) -> None:
    """Validate the explicit publication truth boundary for verified and rumor cases."""
    if not isinstance(disclosure, Mapping):
        raise ComparisonValidationError("证据披露必须是对象", code="evidence_disclosure_invalid")
    required_fields = {"verificationStatus", "isRumor", "originStatus", "originEvidenceRef", "unverifiedReasons", "conditionalAnalysis"}
    if set(disclosure) != required_fields:
        raise ComparisonValidationError("证据披露字段不完整", code="evidence_disclosure_invalid")
    status, rumor, origin = (disclosure.get("verificationStatus"), disclosure.get("isRumor"),
                             disclosure.get("originStatus"))
    if status not in VERIFICATION_STATUSES or not isinstance(rumor, bool) or origin not in {"identified", "unknown"}:
        raise ComparisonValidationError("证据披露状态无效", code="evidence_disclosure_invalid")
    if rumor and status == "verified":
        raise ComparisonValidationError("传闻不能标记为已核实", code="evidence_disclosure_invalid")
    origin_ref = disclosure.get("originEvidenceRef")
    if origin == "identified":
        if (not isinstance(origin_ref, Mapping) or not isinstance(origin_ref.get("documentId"), str)
                or not origin_ref["documentId"] or isinstance(origin_ref.get("revision"), bool)
                or not isinstance(origin_ref.get("revision"), int) or origin_ref["revision"] < 1):
            raise ComparisonValidationError("已知源头必须有真实来源版本", code="evidence_disclosure_invalid")
    elif origin_ref is not None:
        raise ComparisonValidationError("未知源头不能伪造来源版本", code="evidence_disclosure_invalid")
    reasons = disclosure.get("unverifiedReasons")
    if status == "unverified":
        if (not isinstance(reasons, list) or not reasons
                or any(not isinstance(reason, str) or not reason.strip() for reason in reasons)):
            raise ComparisonValidationError("未核实披露必须说明未证实环节", code="evidence_disclosure_invalid")
    elif reasons not in (None, []):
        if not isinstance(reasons, list) or any(not isinstance(reason, str) or not reason.strip() for reason in reasons):
            raise ComparisonValidationError("未核实说明无效", code="evidence_disclosure_invalid")
    conditional = disclosure.get("conditionalAnalysis")
    if rumor and (not isinstance(conditional, str) or not conditional.strip()):
        raise ComparisonValidationError("传闻必须有条件化分析", code="evidence_disclosure_invalid")
    if not rumor and conditional is not None and (not isinstance(conditional, str) or not conditional.strip()):
        raise ComparisonValidationError("条件化分析无效", code="evidence_disclosure_invalid")


def validate_comparison(comparison: Mapping[str, Any], *, require_evidence_disclosure: bool = False) -> None:
    role = comparison.get("role")
    if role not in COMPARISON_ROLES:
        raise ComparisonValidationError("公司比较必须声明发布、待核或排除角色", code="compare_company_role_invalid")
    for key in ("priorityReason", "gap", "rankChangeConditions", "twoDayReason"):
        if not isinstance(comparison.get(key), str) or not comparison[key].strip():
            raise ComparisonValidationError(f"公司比较缺少 {key}", code="compare_company_coverage_invalid")
    disclosure = comparison.get("evidenceDisclosure")
    if disclosure is None:
        disclosure = comparison.get("differences", {}).get("evidenceDisclosure") if isinstance(comparison.get("differences"), Mapping) else None
    if disclosure is None and require_evidence_disclosure:
        raise ComparisonValidationError("公司比较缺少证据披露", code="evidence_disclosure_invalid")
    if disclosure is not None:
        if not isinstance(disclosure, Mapping):
            raise ComparisonValidationError("证据披露必须是对象", code="evidence_disclosure_invalid")
        validate_evidence_disclosure(disclosure)


def publishable_assessments(comparisons: Mapping[str, Any]) -> dict[str, Any]:
    """Derive, never mutate, the formal-candidate subset from a full comparison.

    The persisted B39 assessment is a flat typed record; the established
    candidate history stores the same comparison fields below ``differences``.
    Supporting both shapes keeps the projection single-purpose while leaving
    historical JSON untouched.
    """
    if not isinstance(comparisons, Mapping):
        raise ComparisonValidationError("公司比较必须是对象", code="compare_company_coverage_invalid")

    def role_of(value: Any) -> Any:
        if not isinstance(value, Mapping):
            return None
        direct = value.get("role")
        if direct is not None:
            return direct
        differences = value.get("differences")
        return differences.get("role") if isinstance(differences, Mapping) else None

    return {
        str(company_code): value for company_code, value in comparisons.items()
        if role_of(value) in PUBLISHABLE_ROLES
    }


def validate_event_comparison(
    *, summary: Any, comparisons: Mapping[str, Any], company_codes: Sequence[str],
    require_evidence_disclosure: bool = False,
) -> None:
    """Validate one coherent ordering for every company mapped to an event.

    A per-company answer cannot establish that A outranks B when B is evaluated in
    a separate call.  The comparison contract therefore carries all peers in one
    model result and makes the editorial order inspectable before publication.
    """
    if not isinstance(summary, str) or not summary.strip():
        raise ComparisonValidationError("事件比较缺少共同事实说明", code="compare_output_root_invalid")
    expected = tuple(company_codes)
    if len(expected) != len(set(expected)):
        raise ComparisonValidationError("同一事件的公司映射不得重复", code="compare_company_coverage_invalid")
    if not isinstance(comparisons, Mapping) or set(comparisons) != set(expected):
        raise ComparisonValidationError("事件比较必须恰好覆盖每家公司一次", code="compare_company_coverage_invalid")

    roles: dict[str, str] = {}
    ranks: dict[str, int] = {}
    for company_code in expected:
        item = comparisons[company_code]
        if not isinstance(item, Mapping):
            raise ComparisonValidationError("事件比较公司项无效", code="compare_company_coverage_invalid")
        if not isinstance(item.get("summary"), str) or not item["summary"].strip():
            raise ComparisonValidationError("事件比较缺少公司的具体理由", code="compare_company_coverage_invalid")
        differences = item.get("differences")
        if not isinstance(differences, Mapping):
            raise ComparisonValidationError("事件比较缺少公司差异", code="compare_company_coverage_invalid")
        validate_comparison(differences, require_evidence_disclosure=require_evidence_disclosure)
        roles[company_code] = str(differences["role"])
        rank = item.get("rank")
        if roles[company_code] in PUBLISHABLE_ROLES:
            if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
                raise ComparisonValidationError("可发布公司排序必须为正整数", code="compare_company_ranking_invalid")
            ranks[company_code] = rank
        elif rank is not None:
            raise ComparisonValidationError("pending 或 excluded 不得有发布排序", code="compare_company_ranking_invalid")

    # K10-v2 lets the model choose recommendation count and order. Numeric
    # ranks carry that order (including shared ranks); valid recommendation
    # labels must not impose a second, contradictory one-primary quota.
    # Keep every model-authored label, rank and explanation unchanged.
    if ranks and set(ranks.values()) != set(range(1, max(ranks.values()) + 1)):
        raise ComparisonValidationError("事件比较名次必须连续", code="compare_company_ranking_invalid")


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
    if result["kind"] in UPDATE_KINDS and result.get("relatedOpportunityId") is not None:
        # An update inherits the explicitly chosen opportunity's identity, including
        # a material-stage suffix; the generic event key may name an older window.
        predecessor = next(old for old in previous if old.get("opportunityId") == result["relatedOpportunityId"])
        result["opportunityKey"] = predecessor["opportunityKey"]
    else:
        result["opportunityKey"] = semantic_key
    return result
