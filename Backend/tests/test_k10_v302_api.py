"""Regression evidence for the K10-v1.4 conformance repairs in 3.0.2."""
from __future__ import annotations

from neckline.api.k10 import _metrics
from neckline.api.k10_schemas import CompanyWindowEvaluationOut, MarketDayOut


def _complete_record(sample_class: str, *, window_id: str = "window") -> CompanyWindowEvaluationOut:
    return CompanyWindowEvaluationOut(
        companyWindowId=window_id, companyCode="300001.SZ", sampleClass=sample_class,
        state="completed", revision=1, updatedAt="2026-01-06T15:00:00+08:00",
        d1=MarketDayOut(tradeDate="2026-01-05", availability="available", closeLimitUp=True, touchedLimitUp=True),
        d2=MarketDayOut(tradeDate="2026-01-06", availability="available", closeLimitUp=True, touchedLimitUp=True),
        primaryEligible=sample_class == "primary", closeLimitHitAny=True,
    )


def test_overlap_retains_one_hit_without_acquiring_primary_rate_eligibility():
    record = _complete_record("overlap")
    metrics = _metrics([record], windows={"window": {"d2CloseAt": "2026-01-06T15:00:00+08:00"}},
                       sample_class="overlap", touch_denominator="observed")
    assert metrics.sampleCount == metrics.observedCompleteCount == metrics.hitCount == 1
    assert metrics.eligibleCount == 0
    assert metrics.hitRate is None
    assert metrics.touchRate == 1.0
    assert metrics.knownHitCount == 1


def test_overlap_does_not_count_an_unfinished_window_as_a_completed_hit():
    record = _complete_record("overlap")
    metrics = _metrics([record], windows={"window": {"d2CloseAt": "2099-01-06T15:00:00+08:00"}},
                       sample_class="overlap", touch_denominator="observed")
    assert metrics.hitCount == metrics.observedCompleteCount == metrics.eligibleCount == 0
    assert metrics.knownHitCount == 1
    assert metrics.hitRate is None and metrics.touchRate is None


def test_primary_rate_cannot_acquire_an_overlap_even_with_invalid_eligibility_flag():
    primary = _complete_record("primary", window_id="primary")
    overlap = _complete_record("overlap", window_id="overlap").model_copy(update={"primaryEligible": True})
    metrics = _metrics([primary, overlap])
    assert metrics.eligibleCount == metrics.hitCount == 1
    assert metrics.hitRate == 1.0


def test_morning_api_flattens_all_sections_and_hydrates_exact_sources(tmp_path):
    from tests.test_k10_api import _seed, _client, _freeze_k10_clocks, NOW
    from neckline.k10 import store
    path = tmp_path / 'morning-api.sqlite'
    _seed(path)
    window = store.list_company_windows(db_path=path)[0]
    opportunity = next(item for item in store.list_opportunities(db_path=path) if item['companyWindowId'] == window['companyWindowId'])
    store.create_scan(scan_id='morning', window_kind='morning', cutoff_at=NOW, config_id='cfg', config_revision=1,
                      status='completed', coverage={'status':'complete'}, created_at=NOW, completed_at=NOW, db_path=path)
    sections = ('major_contrary','thesis_changed','continuing_or_expiring','new','needs_review')
    groups = {section:[] for section in sections}
    for section, opportunity_row in zip(('major_contrary','needs_review'), store.list_opportunities(db_path=path)):
        groups[section] = [{'itemId':section,'companyWindowId':opportunity_row['companyWindowId'], 'opportunityId':opportunity_row['opportunityId'], 'status':'completed',
                'content':{'summary':section,'displayRank':1,'selectionState':'unhandled','lifecycle':'active',
                'coverage':{'status':'complete','gaps':[]},'sourceRefs':[{'documentId':'doc-1','revision':1}],
                'independentVerificationRefs':[]}}]
    store.append_morning_report(report_id='report', scan_id='morning', cutoff_at=NOW, generated_at=NOW, status='completed',
                                  coverage={'status':'complete'}, groups=groups, created_at=NOW, db_path=path)
    with _client(path) as client:
        response = client.get('/api/v1/k10/morning-reports/latest')
        assert response.status_code == 200, response.text
        value = response.json()
        assert [item['section'] for item in value['items']] == ['major_contrary','needs_review']
        assert value['items'][0]['sourceRefs'][0]['title'] == '合成公告'
        assert value['items'][0]['deadlineAt'] == window['d2CloseAt']
        assert client.get('/api/v1/k10/morning-reports').json()['items'][0] == value


def test_followup_api_requires_complete_parent_and_preserves_request_and_window(tmp_path, monkeypatch):
    from tests.test_k10_api import _seed, _client, _freeze_k10_clocks, NOW
    from neckline.k10 import store
    from neckline.api import k10 as api
    path = tmp_path / 'followup-api.sqlite'
    _seed(path)
    _freeze_k10_clocks(monkeypatch, '2026-09-07T00:30:00+00:00')
    window = store.list_company_windows(db_path=path)[0]
    window_id = window['companyWindowId']
    monkeypatch.setattr(api, '_now', lambda: '2026-09-07T00:30:00+00:00')
    from tests.k10_v306_fixture import append_approved_execution_profile
    append_approved_execution_profile(db_path=path,created_at=NOW,config_id="api-v306-execution")
    with _client(path, execution_config_binding=("api-v306-execution",1,None)) as client:
        prefix = '/api/v1/k10/company-windows/' + window_id
        command = {'kind':'user_question','question':'新资料会改变排序吗？','sourceRefs':[], 'idempotencyKey':'question-1'}
        assert client.post(prefix + '/analysis-requests', json=command).status_code == 409
        selected = client.post(prefix + '/selection', json={'action':'keep','idempotencyKey':'keep-1'}).json()
        assert client.post(prefix + '/analysis-requests', json=command).status_code == 409
        for role in ('pro','con'):
            store.append_analysis_revision(analysis_id=role, observation_id=selected['observationId'], revision=1,
                analysis_kind=role, input_cutoff_at=NOW, input_lineage={}, content={'fullText':role+'全文'},
                status='completed', created_at=NOW, db_path=path)
        response = client.post(prefix + '/analysis-requests', json=command)
        assert response.status_code == 200, response.text
        first = response.json()
        assert first['revision'] == 2 and first['parentRevision'] == 1 and not first['replayed']
        monkeypatch.setattr(api, '_now', lambda: '2026-09-07T01:00:00+00:00')
        replay = client.post(prefix + '/analysis-requests', json=command).json()
        assert replay == {**first,'replayed':True}
        assert client.post(prefix + '/analysis-requests', json={**command,'question':'不同的问题'}).status_code == 409
        assert client.post(prefix + '/analysis-requests', json={**command,'idempotencyKey':'question-2'}).status_code == 409
        chain = client.get(prefix + '/analysis-chain')
        assert chain.status_code == 200, chain.text
        assert [item['revision'] for item in chain.json()['items']] == [1,2]
        assert [item['fullText'] for item in chain.json()['items'][0]['analyses']] == ['pro全文','con全文']
        assert chain.json()['items'][1]['job']['status'] == 'queued'
    latest = next(item for item in store.list_company_windows(db_path=path) if item['companyWindowId'] == window_id)
    assert latest['d1TradeDate'] == window['d1TradeDate'] and latest['d2TradeDate'] == window['d2TradeDate']


def test_evening_and_preopen_morning_share_one_result_cohort(tmp_path):
    from datetime import datetime
    from tests.test_k10_api import _seed, _client, NOW
    from neckline.k10 import store
    from neckline.k10.types import OpportunityPublicationInput
    path = tmp_path / 'same-d1.sqlite'
    _seed(path)
    refs = [{'documentId':'doc-1','revision':1}]
    comparison = store.get_candidate(candidate_id='cand-1',db_path=path)['comparison']
    comparison = {**comparison,'classification':{**comparison['classification'],'opportunityKey':'morning-new'}}
    store.create_scan(scan_id='scan-morning',window_kind='morning',cutoff_at='2026-09-07T00:45:00+00:00',config_id='cfg',config_revision=1,status='completed',coverage={'status':'complete'},created_at=NOW,completed_at=NOW,db_path=path)
    store.create_candidate(candidate_id='cand-morning',scan_id='scan-morning',event_id='event-1',event_revision=1,company_code='300003.SZ',comparison=comparison,evidence=refs,created_at=NOW,db_path=path)
    store.publish_opportunities(batch_id='batch-morning',scan_id='scan-morning',publication_kind='morning',
        inputs=[OpportunityPublicationInput(candidate_id='cand-morning',company_code='300003.SZ',event_id='event-1',event_revision=1,
            opportunity_key='morning-new',catalyst_stage='initial',category='primary',comparison=comparison,evidence_refs=tuple(refs),source_marker='morning')],
        db_path=path,clock=lambda:datetime.fromisoformat('2026-09-07T01:00:00+00:00'))
    with _client(path) as client:
        response=client.get('/api/v1/k10/results')
        assert response.status_code == 200, response.text
        cohorts=response.json()['cohorts']
        assert len(cohorts)==1
        assert cohorts[0]['batchIds']==['batch-1','batch-morning']
        assert cohorts[0]['companySampleCount']==3
        windows=client.get('/api/v1/k10/company-windows').json()['items']
        assert {op['sourceMarker'] for w in windows for op in w['opportunities']}=={'evening','morning'}


def test_partial_analysis_and_legacy_morning_rows_do_not_break_public_readers(tmp_path, monkeypatch):
    from tests.test_k10_api import _seed, _client, _freeze_k10_clocks, NOW
    from neckline.k10 import store
    path=tmp_path/'partial.sqlite';_seed(path)
    _freeze_k10_clocks(monkeypatch, '2026-09-07T00:30:00+00:00')
    window=store.list_company_windows(db_path=path)[0]
    prefix='/api/v1/k10/company-windows/'+window['companyWindowId']
    from tests.k10_v306_fixture import append_approved_execution_profile
    append_approved_execution_profile(db_path=path,created_at=NOW,config_id="api-v306-execution")
    with _client(path, execution_config_binding=("api-v306-execution",1,None)) as client:
        selected=client.post(prefix+'/selection',json={'action':'keep','idempotencyKey':'keep'}).json()
        for role,revision in [('pro',1),('morning',99)]:
            store.append_analysis_revision(analysis_id=role,observation_id=selected['observationId'],revision=revision,analysis_kind=role,
                input_cutoff_at=NOW,input_lineage={},content={'fullText':'部分资料已完成'},status='partial',created_at=NOW,db_path=path)
        selection=client.get('/api/v1/k10/selections/'+window['companyWindowId'])
        assert selection.status_code==200,selection.text
        assert [x['role'] for x in selection.json()['analyses']]==['pro']
        assert selection.json()['analyses'][0]['status']=='partial'
        chain=client.get(prefix+'/analysis-chain')
        assert chain.status_code==200,chain.text
        assert [x['revision'] for x in chain.json()['items']]==[1]
