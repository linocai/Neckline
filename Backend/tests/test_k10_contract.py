"""Committed V1.4 samples prevent camelCase API drift before Swift connects."""

from __future__ import annotations

import json
from pathlib import Path

from neckline.api.k10_schemas import CompanyWindowEvaluationOut, CompanyWindowOut, SelectionActionIn


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "k10_contract"


def test_company_window_fixture_matches_v14_contract() -> None:
    value = json.loads((FIXTURE_ROOT / "company_window.json").read_text(encoding="utf-8"))
    parsed = CompanyWindowOut.model_validate(value)
    assert parsed.companyWindowId == "window-1"
    assert parsed.samples[0].companyCandidateId == "cand-1"
    assert "company_window_id" not in value


def test_selection_command_is_explicit_and_has_no_trade_plan_fields() -> None:
    value = json.loads((FIXTURE_ROOT / "selection_action.json").read_text(encoding="utf-8"))
    parsed = SelectionActionIn.model_validate(value)
    assert parsed.action == "keep"
    assert "pricePlan" not in value and "holdingExitPlan" not in value


def test_evaluation_fixture_keeps_overlap_and_data_gap_visible() -> None:
    value = json.loads((FIXTURE_ROOT / "evaluation.json").read_text(encoding="utf-8"))
    parsed = CompanyWindowEvaluationOut.model_validate(value)
    assert parsed.sampleClass == "overlap"
    assert parsed.primaryEligible is False
    assert parsed.d2 is not None and parsed.d2.availability == "data_gap"
