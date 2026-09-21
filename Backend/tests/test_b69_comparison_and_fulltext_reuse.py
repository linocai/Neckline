import copy
from datetime import timedelta
import sqlite3

from neckline.k10 import store
from neckline.k10.research_store import list_research_assessments
from neckline.k10.verification import TavilyEvidenceGateway
from neckline.k10.v2_store import read_report
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for
from tests.test_b60_pool_filtering import edit_responses
from tests.test_v310_tavily import _SearchExtract


def test_real_worker_publishes_two_distinct_tied_groups_without_changing_model_order(tmp_path, monkeypatch):
    from tests.test_v350_research_round import _DirectModel, _complete_round, _packet, _snapshot
    from neckline.k10.research_runtime import build_research_round_packet, run_research_round

    codes = ['300080.SZ', '300376.SZ', '300409.SZ', '301487.SZ', '301658.SZ']
    raw = _complete_round()
    mapping = raw['conclusion']['companyMappings'][0]
    assessment = raw['companyAssessments'][0]
    raw['conclusion']['companyMappings'] = [{**mapping, 'companyCode': code} for code in codes]
    raw['companyAssessments'] = [{**assessment, 'companyCode': code, 'role': 'tied',
                                  'rank': 1 if index < 2 else 2}
                                 for index, code in enumerate(codes)]
    result = run_research_round(_DirectModel(raw), snapshot=_snapshot(),
                                evidence_packet=build_research_round_packet(_packet()))
    assert [(row['companyCode'], row['rank']) for row in result.company_assessments] == [
        (code, 1 if index < 2 else 2) for index, code in enumerate(codes)
    ]


def test_real_worker_reuses_completed_fulltext_when_only_explanation_changes(tmp_path, monkeypatch):
    from tests.test_v350_research_round import test_b78_fulltext_material_uses_safe_local_read_before_second_direct_round

    test_b78_fulltext_material_uses_safe_local_read_before_second_direct_round(tmp_path)
