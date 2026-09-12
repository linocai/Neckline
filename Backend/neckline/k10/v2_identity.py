"""Route v2 recommendation to an identity without judging it a second time."""
from typing import Any, Mapping, Sequence

from .opportunity_discovery import (
    ComparisonValidationError, PUBLISHABLE_ROLES, normalize_catalyst_stage,
    validate_classification, validate_evidence_disclosure,
)

IDENTITY_CONTRACT = 'k10-v2-identity-3.2.1'


def recommendation_is_complete(*, comparison, verification) -> bool:
    if comparison.differences.get('role') not in PUBLISHABLE_ROLES or verification.state == 'contradicted':
        return False
    disclosure = comparison.differences.get('evidenceDisclosure')
    if disclosure is None:
        return False
    validate_evidence_disclosure(disclosure)
    return disclosure['verificationStatus'] != 'contradicted'


def validate_identity_role(value: Mapping[str, Any], *, comparison, verification) -> None:
    if recommendation_is_complete(comparison=comparison, verification=verification):
        if value.get('kind') not in {'initial', 'independent', 'material_stage', 'continuation'}:
            raise ComparisonValidationError(
                '有效 K10-v2 比较已决定推荐；此步骤只能判断新旧催化身份，不得再次否决推荐',
                code='v2_identity_role_conflict',
            )


def classify_identity(*, event, verification, mapping, comparison,
                      previous: Sequence[Mapping[str, Any]], classifier) -> dict[str, Any]:
    """Use exact identities only; ambiguous historical links still need a model.

    Descriptions below are the already validated event/comparison text. Nothing
    here invents a fact, an investment reason or a historical predecessor.
    """
    complete = recommendation_is_complete(comparison=comparison, verification=verification)
    common = dict(reason=comparison.summary, newFacts=None, changedJudgment=None,
                  twoDayReason=None, relatedOpportunityId=None)
    if complete and not previous:
        raw = dict(common, kind='initial', newFacts=event.headline,
                   twoDayReason=comparison.differences['twoDayReason'])
    elif complete and len(same_stage := [old for old in previous
            if old.get('canonicalKey') == event.canonical_key
            and isinstance(old.get('catalystStage'), str) and old['catalystStage'].strip()
            and normalize_catalyst_stage(old['catalystStage']) == normalize_catalyst_stage(event.stage_key)]) == 1:
        raw = dict(common, kind='continuation', relatedOpportunityId=same_stage[0]['opportunityId'],
                   newFacts=event.headline, twoDayReason=comparison.differences['twoDayReason'])
    elif not previous and comparison.differences.get('role') in {'pending', 'excluded'}:
        raw = dict(common, kind='background')
    elif not previous and verification.state == 'contradicted':
        raw = dict(common, kind='needs_review', reason=verification.summary)
    else:
        if not callable(classifier):
            raise ValueError('发现模型缺少机会延续/新催化分类')
        raw = classifier(event=event, verification=verification, mapping=mapping,
                         comparison=comparison, previous=previous)
    value = validate_classification(raw, canonical_key=event.canonical_key, stage_key=event.stage_key,
                                    company_code=mapping.company_code, previous=previous)
    validate_identity_role(value, comparison=comparison, verification=verification)
    return value
