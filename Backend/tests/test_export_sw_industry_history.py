from __future__ import annotations

from datetime import date
import importlib.util
from pathlib import Path
import sys

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    path = _ROOT / "scripts" / "export_sw_industry_history.py"
    spec = importlib.util.spec_from_file_location("_export_sw_history_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


EX = _load_script()


def _row(code: str, l2: str, in_date: str, out_date: str, state: str) -> dict:
    return {
        "ts_code": code, "name": code,
        "l1_code": "801000.SI", "l1_name": "L1",
        "l2_code": l2, "l2_name": l2,
        "l3_code": f"{l2}-L3", "l3_name": "L3",
        "in_date": in_date, "out_date": out_date, "is_new": state,
    }


def test_interval_history_switches_on_out_date_and_matches_current_snapshot():
    days = [date(2026, 8, 28), date(2026, 8, 31)]
    current = [
        _row("600001.SH", "new", "20260831", "", "Y"),
        _row("600002.SH", "steady", "20200101", "", "Y"),
    ]
    former = [_row("600001.SH", "old", "20200101", "20260831", "N")]
    document = EX.build_document(days, current, former, source_time="2026-08-31T12:00:00+00:00")
    first, final = document["snapshots"]
    assert {row["ts_code"]: row["l2_code"] for row in first["members"]} == {
        "600001.SH": "old", "600002.SH": "steady",
    }
    assert {row["ts_code"]: row["l2_code"] for row in final["members"]} == {
        "600001.SH": "new", "600002.SH": "steady",
    }
    assert final["complete"] is True and final["expectedMemberCount"] == 2


def test_overlapping_assignments_are_refused():
    days = [date(2026, 8, 31)]
    current = [_row("600001.SH", "new", "20260801", "", "Y")]
    former = [_row("600001.SH", "old", "20200101", "20260901", "N")]
    with pytest.raises(ValueError, match="重叠申万归属"):
        EX.build_document(days, current, former, source_time="2026-08-31T12:00:00+00:00")


def test_last_day_must_equal_independent_current_snapshot():
    days = [date(2026, 8, 31)]
    current = [
        _row("600001.SH", "new", "20260901", "", "Y"),
        _row("600002.SH", "steady", "20200101", "", "Y"),
    ]
    with pytest.raises(ValueError, match="最后一日与独立当前快照不一致"):
        EX.build_document(days, current, [], source_time="2026-08-31T12:00:00+00:00")
