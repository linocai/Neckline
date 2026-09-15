"""Do not reject an entire paid plan for a redundant malformed company route."""
import pytest
from neckline.k10.investigation import decode_stage_result
from tests.test_b72_query_path_resilience import fixture


@pytest.mark.parametrize('covered', [True,False])
def test_known_claim_only_company_route_requires_valid_alternative(covered):
    p,raw=fixture()
    p['_localState']['questions']=p['_localState']['questions'][:1]
    good=raw['queryPaths'][0]
    bad={**good,'pathId':'missing-company','query':good['query']+' incomplete','purposeKind':'company_event_link','targetRefs':[{'kind':'claim','claimId':'c1'}]}
    raw['queryPaths']=[good,bad] if covered else [bad]
    result=decode_stage_result(raw,action='plan_queries',evidence_packet=p)
    assert [x.path_id for x in result.query_paths]==(['valid'] if covered else ['missing-company'])
    if covered:assert result.conclusion['runtimeOutputSanitization']['discardedUnusableQueryPaths']==1


def test_real_cli_recovers_paid_plan_without_rebilling_redundant_company_route(tmp_path,monkeypatch):
    from tests import test_b72_query_path_resilience as old
    original=old.edit_mixed_plan
    def edit(value):
        original(value)
        if value.get('action')=='plan_queries':
            valid=value['queryPaths'][1]
            value['queryPaths'].append({**valid,'pathId':'redundant-company','purposeKind':'company_event_link',
                'targetRefs':[{'kind':'claim','claimId':'c1'}]})
    monkeypatch.setattr(old,'edit_mixed_plan',edit)
    old.test_real_cli_worker_keeps_scoped_searches_and_reuses_paid_failed_plan(tmp_path,monkeypatch,True)
