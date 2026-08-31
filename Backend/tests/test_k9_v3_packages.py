from datetime import date
from pathlib import Path

import pytest

from neckline.db import init_schema
from neckline.k9 import v3_params
from neckline.report import pipeline
from neckline.scorecard import packages


def test_missing_or_v2_params_never_load_as_v3(tmp_path: Path):
    with pytest.raises(v3_params.ParamsUnavailable):
        v3_params.load(tmp_path / "missing.json")
    path = tmp_path / "old.json"
    path.write_text('{"schemaVersion":"k9-params-v2"}', encoding="utf-8")
    with pytest.raises(v3_params.ParamsUnavailable):
        v3_params.load(path)


def test_batch_lifecycle_is_append_only_and_two_packages_lock_candidates(tmp_path: Path):
    db = tmp_path / "test.db"; init_schema(db)
    candidate = packages.Candidate("000001.SZ", "示例", None, None, ["p2"], {"p2": 1}, {}, {}, {})
    common = dict(params_package_version="p", params_sha256="h", pack_id="fp", frozen_contract={"version": 1}, candidates=[candidate], db_path=db)
    packages.create_batch(batch_id="a", selection_date=date(2026, 8, 20), signal_trade_date=date(2026, 8, 20), d1_trade_date=date(2026, 8, 21), d2_trade_date=date(2026, 8, 24), revision=1, **common)
    assert packages.recent_locked_codes(before_selection_date=date(2026, 8, 21), db_path=db) == {"000001.SZ"}
    packages.append_d1(batch_id="a", ts_code="000001.SZ", checklist_verdict="unbuyable", open_verdict="unbuyable", reference_price=None, close_state="unavailable", raw={}, db_path=db)
    packages.append_d2(batch_id="a", ts_code="000001.SZ", selection_result="unavailable", playbook_result=None, risk_tag=None, raw={}, db_path=db)
    assert packages.list_packages(state="settled", db_path=db)[0]["state"] == "settled"
    with pytest.raises(packages.PackageConflict):
        packages.append_d2(batch_id="a", ts_code="000001.SZ", selection_result="confirmed_failed", playbook_result=None, risk_tag=None, raw={}, db_path=db)


def test_d1_retry_must_match_every_frozen_value(tmp_path: Path):
    db = tmp_path / "test.db"; init_schema(db)
    candidate = packages.Candidate("000001.SZ", "示例", None, None, ["p2"], {"p2": 1}, {}, {}, {})
    packages.create_batch(batch_id="a", selection_date=date(2026, 8, 20), signal_trade_date=date(2026, 8, 20),
                          d1_trade_date=date(2026, 8, 21), d2_trade_date=date(2026, 8, 24), revision=1,
                          params_package_version="p", params_sha256="h", pack_id="fp", frozen_contract={},
                          candidates=[candidate], db_path=db)
    packages.append_d1(batch_id="a", ts_code="000001.SZ", checklist_verdict="pending_open",
                       open_verdict="confirmed", reference_price=10.0, close_state="held", raw={"source": "x"}, db_path=db)
    with pytest.raises(packages.PackageConflict):
        packages.append_d1(batch_id="a", ts_code="000001.SZ", checklist_verdict="pending_open",
                           open_verdict="confirmed", reference_price=10.1, close_state="held", raw={"source": "x"}, db_path=db)


def test_no_parameter_pack_is_a_not_run_report_without_a_batch(tmp_path: Path):
    db = tmp_path / "test.db"; init_schema(db)
    result = pipeline.build_report(date(2026, 8, 20), db_path=db)
    assert result.state.value == "not_run"
    assert result.listing_size is None
    assert result.batch_ids == ()
    assert "参数未配置" in result.headline


def test_v3_read_paths_do_not_create_a_missing_database(tmp_path: Path):
    db = tmp_path / "absent.db"
    assert packages.list_packages(state="active", db_path=db) == []
    assert packages.load_package("unknown", db_path=db) is None
    assert not db.exists()
