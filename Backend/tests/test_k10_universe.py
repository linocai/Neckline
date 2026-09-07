from __future__ import annotations

from datetime import datetime, timezone

from neckline.k10.universe import BAIJIU_L2_CODE, CompanyMetadata, evaluate_company


NOW = datetime(2026, 9, 6, 13, tzinfo=timezone.utc)


def _metadata(**changes):
    values = {"company_code": "300001.SZ", "board": "chinext", "is_st": False,
              "sw_l2_code": "801080.SI", "as_of": NOW}
    values.update(changes)
    return CompanyMetadata(**values)


def test_only_approved_company_filters_apply():
    assert evaluate_company(_metadata()).state == "eligible"
    assert evaluate_company(_metadata(board="main")).reason == "非创业板"
    assert evaluate_company(_metadata(is_st=True)).reason == "ST 或 *ST"
    assert BAIJIU_L2_CODE == "801125.SI"
    assert "白酒" in evaluate_company(_metadata(sw_l2_code=BAIJIU_L2_CODE)).reason


def test_missing_metadata_is_not_silently_eligible_or_a_permanent_exclusion():
    assert evaluate_company(None).state == "insufficient_metadata"
    state = evaluate_company(_metadata(sw_l2_code=None))
    assert state.state == "insufficient_metadata"
    assert "sw_l2_code" in state.reason


def test_company_name_and_theme_are_not_white_liquor_filters():
    # The data shape intentionally has no name/theme.  A non-baijiu SW L2 code remains eligible.
    assert evaluate_company(_metadata(company_code="300999.SZ", sw_l2_code="801120.SI")).eligible
