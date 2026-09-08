from dataclasses import replace
from datetime import timedelta
import pytest
from neckline.k10 import pipeline, store, research_runtime
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.worker import run_once
from neckline.k10.investigation import InvestigationError, decode_stage_result
from neckline.k10.research_contracts import canonical_company_code, ResearchContractError
from tests.test_v310_pipeline_e2e import _run, _http_transport, RUN_AT
from tests.test_v310_research_runtime import _runtime, REF
from neckline.k10.research_contracts import FullTextRequest, ResearchStageResult


def test_unknown_time_search_hit_can_request_fulltext_but_cannot_support_a_claim():
    runtime=_runtime()
    runtime.state['questions']=[{'questionId':'q-1'}]
    unknown={'documentId':'real-search-hit','revision':1}
    packet={'allowedEvidenceRefs':[REF],'fulltextRequestRefs':[unknown]}
    request=FullTextRequest('request','q-1',unknown,'摘录没有时间','全文可核对时间','requested',None)
    runtime._validate_result('assess_evidence',ResearchStageResult('assess_evidence',fulltext_requests=(request,)),packet)
    with pytest.raises(InvestigationError) as caught:
        runtime._validate_result('assess_evidence',ResearchStageResult('assess_evidence',evidence_updates=({
            'claimId':'claim-1','sourceRef':unknown,'relation':'supports','location':'excerpt','applicability':{}},)),packet)
    assert caught.value.code=='investigation_reference_invalid'
    with pytest.raises(InvestigationError):
        runtime._validate_result('assess_evidence',ResearchStageResult('assess_evidence',fulltext_requests=(request,)),{'allowedEvidenceRefs':[REF]})


@pytest.mark.parametrize("code,expected",[("002361","002361.SZ"),("300001","300001.SZ"),("600001","600001.SH"),("688001","688001.SH"),("300001.SH","300001.SH")])
def test_equity_code_spelling_never_changes_an_explicit_exchange(code, expected):
    assert canonical_company_code(code) == expected


@pytest.mark.parametrize("code",["NVDA","30001","3012345","800001",None])
def test_unknown_or_ambiguous_code_is_not_guessed(code):
    with pytest.raises(ResearchContractError): canonical_company_code(code)


def test_invalid_relation_cannot_become_a_completed_typed_model_result():
    raw={"action":"close_research","conclusion":{"researchStatus":"ready_for_comparison","companyMappings":[
        {"companyCode":"300001","affectedStage":"送样","inference":{},"uncertainty":"待核","relationEvidence":[]}]}}
    with pytest.raises(InvestigationError) as caught:
        decode_stage_result(raw,action="close_research")
    assert caught.value.__cause__.field_name == "companyMappings[].relationEvidence"


def test_saved_six_digit_closure_recovers_without_research_or_body_replay(tmp_path, monkeypatch):
    original_model=pipeline.decode_stage_result
    original_mappings=research_runtime._Investigation._mappings
    def old_result(value, **kwargs):
        result=original_model(value,**kwargs)
        if kwargs["action"]=="close_research" and result.conclusion.get("companyMappings"):
            result=replace(result,conclusion={**result.conclusion,"companyMappings":[
                {**item,"companyCode":item["companyCode"].split('.')[0]} for item in result.conclusion["companyMappings"]]})
        return result
    def old_runtime(self, conclusion):
        if any('.' not in item['companyCode'] for item in conclusion.get('companyMappings', [])):
            raise InvestigationError('legacy spelling rejection',code='investigation_mapping_invalid')
        return original_mappings(self,conclusion)
    monkeypatch.setattr(pipeline,'decode_stage_result',old_result)
    monkeypatch.setattr(research_runtime._Investigation,'_mappings',old_runtime)
    db,task_id,first,_,gateway=_run(tmp_path,monkeypatch)
    assert first.status=='failed'
    monkeypatch.setattr(pipeline,'decode_stage_result',original_model)
    monkeypatch.setattr(research_runtime._Investigation,'_mappings',original_mappings)
    scan=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['scanId']
    recover_scan(db_path=db,scan_id=scan,execution_config_id='b39-execution',execution_config_revision=1,
        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan,db_path=db),now=RUN_AT)
    calls=_http_transport(monkeypatch,initial_query_round=1)
    done=run_once(db_path=db,worker_id='spelling-repaired',lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:RUN_AT)
    assert done.status=='completed'
    assert 'understand' not in calls and 'research:close_research' not in calls
    assert calls.count('research:compare_companies')==1
    assert len(gateway.search_paths)==2
    candidates=store.list_candidates(scan_id=scan,state='offered',db_path=db)
    assert len(candidates)==1 and candidates[0]['companyCode']=='300001.SZ'
