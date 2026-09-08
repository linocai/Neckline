from copy import deepcopy
import pytest
from neckline.k10 import pipeline
from neckline.k10.investigation import InvestigationError, decode_stage_result
from tests.test_v310_pipeline_e2e import _run

@pytest.mark.parametrize('mutation,field,expected', [
    ({'originStatus':'unknown','originEvidenceRef':{'documentId':'input','revision':1}}, 'evidenceDisclosure.originEvidenceRef','null_when_origin_unknown'),
    ({'verificationStatus':'unverified','unverifiedReasons':[]},'evidenceDisclosure.unverifiedReasons','non_empty_string_array_when_unverified'),
    ({'isRumor':True,'verificationStatus':'verified'},'evidenceDisclosure.verificationStatus','not_verified_when_isRumor_true'),
])
def test_comparison_disclosure_has_specific_bounded_repair(tmp_path,monkeypatch,mutation,field,expected):
    original=pipeline.decode_stage_result
    attempts=[0]
    model_original=pipeline.DeepSeekDiscoveryModel.advance_research
    def check_feedback(self,**kwargs):
        if kwargs['action']=='compare_companies' and attempts[0]:
            errors=self._thread_usage.repair_feedback['validationErrors']
            assert {'field':field,'expected':expected}.items() <= errors[0].items()
        return model_original(self,**kwargs)
    def decode(raw,*,action,evidence_packet=None):
        if action=='compare_companies':
            attempts[0]+=1
            if attempts[0]==1:
                raw=deepcopy(raw)
                raw['companyAssessments'][0]['evidenceDisclosure'].update(mutation)
        return original(raw,action=action,evidence_packet=evidence_packet)
    monkeypatch.setattr(pipeline,'decode_stage_result',decode)
    monkeypatch.setattr(pipeline.DeepSeekDiscoveryModel,'advance_research',check_feedback)
    db,task_id,task,calls,gateway=_run(tmp_path,monkeypatch)
    assert task.status=='completed'
    assert calls.count('research:compare_companies')==2
    assert calls.count('understand')==1 and len(gateway.search_paths)==2
