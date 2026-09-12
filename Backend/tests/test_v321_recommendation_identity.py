"""The paid company comparison owns v2 recommendation; identity cannot veto it.

The rejection reasons reproduce the September 10 report audit. All sources and
transports here are deterministic, and the real CLI creates its own task binding.
"""
import copy
import sqlite3

import pytest

from neckline.k10 import store
from neckline.k10.research_store import list_research_assessments
from neckline.k10.v2_store import read_report
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for
from tests.test_b60_pool_filtering import edit_responses


def _assert_projection_contains(actual, persisted):
    """The API may add DTO defaults, but cannot change persisted report facts."""
    if isinstance(persisted, dict):
        for key, value in persisted.items():
            assert key in actual
            _assert_projection_contains(actual[key], value)
    elif isinstance(persisted, list):
        assert len(actual) == len(persisted)
        for left, right in zip(actual, persisted):
            _assert_projection_contains(left, right)
    else:
        assert actual == persisted


def build_identity_case(tmp_path, monkeypatch, *, code, role, rejected_kind, reason):
    """Also used by native QA: the producer, rather than a fixture writer, publishes."""
    def edit(value):
        if 'items' in value:
            for row in value['items']:
                row['companyCodes'] = [code]
        if value.get('action') == 'close_research' and value['conclusion'].get('companyMappings'):
            row = copy.deepcopy(value['conclusion']['companyMappings'][0])
            value['conclusion']['companyMappings'] = [{**row, 'companyCode': code}]
        if value.get('action') == 'compare_companies':
            row = copy.deepcopy(value['companyAssessments'][0])
            value['companyAssessments'] = [{**row, 'companyCode': code, 'role': role, 'rank': 1}]
        if 'kind' in value:
            value.update(kind=rejected_kind, relatedOpportunityId=None, reason=reason)
    edit_responses(monkeypatch, edit)
    return e2e._run(
        tmp_path, monkeypatch, v2=True, pending_ranking='legacy_wrong', require_title_hint=False,
    )


@pytest.mark.parametrize('code,role,rejected_kind,reason', [
    ('300961.SZ', 'primary', 'needs_review',
     '无已有正式推荐机会；证据来源单一且未核实，官方公告和权益变动报告缺失，待官方披露确认。'),
    ('300842.SZ', 'alternative', 'background',
     '该标的仅为关联对照底稿中的alternative，未形成正式推荐，不能追认为备选。'),
])
def test_first_recommendation_survives_retired_veto_through_real_worker(
    tmp_path, monkeypatch, code, role, rejected_kind, reason,
):
    db, task_id, task, calls, _ = build_identity_case(tmp_path, monkeypatch, code=code, role=role,
        rejected_kind=rejected_kind, reason=reason)
    assert task.status == 'completed'
    assert {row['companyCode']: row['role'] for row in list_research_assessments(db_path=db, task_id=task_id)} == {code: role}
    report = read_report(db_path=db)
    assert [card['companyCode'] for card in report['eveningCards']] == [code]
    assert calls.count('classify') == 0
    card = report['eveningCards'][0]
    assert card['catalysts'][0]['verificationStatus'] == 'unverified'
    assert card['uncertainty']
    with client_for(db) as client:
        response = client.get('/api/v1/k10/v2/reports/latest?window=evening')
        assert response.status_code == 200
        _assert_projection_contains(response.json()['report']['eveningCards'], report['eveningCards'])
    opportunities = store.list_opportunities(db_path=db)
    assert len(opportunities) == 1
    assert (opportunities[0]['d1TradeDate'], opportunities[0]['d2TradeDate']) == ('2026-09-09', '2026-09-10')
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM k10_external_attempts WHERE task_id=? AND stage='classify'", (task_id,)).fetchone()[0] == 0


def test_first_pending_and_excluded_do_not_pay_for_opportunity_classification(tmp_path, monkeypatch):
    db, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True, pending_ranking='pending')
    assert task.status == 'completed'
    assert read_report(db_path=db)['eveningCards'] == []
    assert {row['role'] for row in list_research_assessments(db_path=db, task_id=task_id)} == {'pending', 'excluded'}
    assert calls.count('classify') == 0


def _identity_inputs(*, role='primary', state='needs_review', event_key='project', stage='initial'):
    from neckline.k10.discovery import CandidateComparison, EventDraft, EvidenceRef, CompanyMappingDraft, Verification
    ref = EvidenceRef('source', 1)
    return dict(
        event=EventDraft(event_key, stage, 'rumor', '供应商首次报告项目送样', 'company', {}, (ref,)),
        verification=Verification(state, '来源尚未确认，保留未知', (ref,)),
        mapping=CompanyMappingDraft('300961.SZ', 'project', (ref,), {}, '来源未核实'),
        comparison=CandidateComparison('项目关联支持当前比较', {
            'role': role, 'twoDayReason': '该新消息影响接下来两日关注',
            'evidenceDisclosure': {'verificationStatus': 'unverified', 'isRumor': True,
                'originStatus': 'unknown', 'originEvidenceRef': None, 'unverifiedReasons': ['未获官方确认'],
                'conditionalAnalysis': '仅按消息成立时讨论其影响'},
        }, (ref,), 1 if role in {'primary', 'alternative', 'tied'} else None),
    )


def _old(*, key='project', stage='initial', state='active', identity='old-1'):
    return {'opportunityId': identity, 'companyCode': '300961.SZ', 'canonicalKey': key,
            'catalystStage': stage, 'opportunityKey': key + '\x1f300961.SZ', 'state': state,
            'companyWindowId': 'frozen-window', 'd1TradeDate': '2026-09-09', 'd2TradeDate': '2026-09-10'}


def _unexpected_classification(**_):
    pytest.fail('This identity is derivable without another model call')


@pytest.mark.parametrize('state', ['active', 'expired'])
def test_exact_previous_stage_preserves_identity_even_after_window_expiry(state):
    from neckline.k10.v2_identity import classify_identity
    old = _old(state=state)
    frozen = copy.deepcopy(old)
    result = classify_identity(**_identity_inputs(stage=' INITIAL '), previous=(old,), classifier=_unexpected_classification)
    assert result['kind'] == 'continuation'
    assert result['relatedOpportunityId'] == old['opportunityId']
    assert result['opportunityKey'] == old['opportunityKey']
    assert old == frozen


@pytest.mark.parametrize('kind,new_key,new_stage', [
    ('material_stage', 'project', 'production'), ('independent', 'other-project', 'announcement'),
])
def test_real_new_catalyst_requires_identity_comparison_and_keeps_predecessor(kind, new_key, new_stage):
    from neckline.k10.v2_identity import classify_identity
    old = _old()
    observed = []
    def classify(**kwargs):
        observed.append(kwargs)
        return {'kind': kind, 'relatedOpportunityId': old['opportunityId'] if kind == 'material_stage' else None,
                'reason': '本次是不同催化阶段', 'newFacts': '新增量产消息', 'changedJudgment': '由送样转向量产',
                'twoDayReason': '新增事实改变两日关注理由'}
    result = classify_identity(**_identity_inputs(event_key=new_key, stage=new_stage), previous=(old,), classifier=classify)
    assert len(observed) == 1
    assert result['kind'] == kind and result['opportunityKey'] != old['opportunityKey']
    assert old['companyWindowId'] == 'frozen-window'


def test_ambiguous_same_stage_does_not_choose_a_window_by_list_order():
    from neckline.k10.v2_identity import classify_identity
    first, second = _old(), _old(identity='old-2')
    second['opportunityKey'] += '\x1finitial'
    called = []
    def classify(**kwargs):
        called.append(kwargs)
        return {'kind': 'continuation', 'relatedOpportunityId': 'old-2', 'reason': '证据针对第二个原机会'}
    result = classify_identity(**_identity_inputs(), previous=(first, second), classifier=classify)
    assert len(called) == 1
    assert result['opportunityKey'] == second['opportunityKey']


@pytest.mark.parametrize('veto', ['background', 'needs_review', 'invalidated'])
def test_historical_identity_may_not_rejudge_a_valid_recommendation(veto):
    from neckline.k10.opportunity_discovery import ComparisonValidationError
    from neckline.k10.v2_identity import classify_identity
    old = _old(key='different-event')
    with pytest.raises(ComparisonValidationError, match='不得再次否决'):
        classify_identity(**_identity_inputs(), previous=(old,), classifier=lambda **_: {
            'kind': veto, 'relatedOpportunityId': old['opportunityId'], 'reason': '没有官方确认'})


def test_nonrecommended_real_denial_still_reaches_old_opportunity_lifecycle():
    from neckline.k10.v2_identity import classify_identity
    old = _old()
    calls = []
    def classify(**kwargs):
        calls.append(kwargs['verification'].state)
        return {'kind': 'invalidated', 'relatedOpportunityId': old['opportunityId'], 'reason': '官方否认已核实'}
    result = classify_identity(**_identity_inputs(role='excluded', state='contradicted'), previous=(old,), classifier=classify)
    assert calls == ['contradicted']
    assert result['kind'] == 'invalidated' and result['opportunityKey'] == old['opportunityKey']


def test_first_contradicted_reason_cannot_become_a_recommendation():
    from neckline.k10.v2_identity import classify_identity
    result = classify_identity(**_identity_inputs(state='contradicted'), previous=(), classifier=_unexpected_classification)
    assert result['kind'] == 'needs_review' and result['relatedOpportunityId'] is None
