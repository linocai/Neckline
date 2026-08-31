"""K9-v3 scorecard aggregation: package provenance and channel slices only.

The retired fp-3 coverage queue has no compatible meaning in K9-v3.  These
tests protect the replacement contract rather than keeping a dormant writer
alive just to exercise an obsolete table.
"""
from __future__ import annotations

from datetime import date

from neckline.db import init_schema
from neckline.scorecard import packages


def _seed(db, *, batch_id: str, params: str, channels: list[str], result: str, risk: str | None = None, revision: int = 1):
    candidate = packages.Candidate("000001.SZ", "示例", None, None, channels,
                                   {channel: 1 for channel in channels}, {}, {}, {})
    packages.create_batch(
        batch_id=batch_id, selection_date=date(2026, 8, 20), signal_trade_date=date(2026, 8, 20),
        d1_trade_date=date(2026, 8, 21), d2_trade_date=date(2026, 8, 24), revision=revision,
        params_package_version=params, params_sha256="sha-" + params, pack_id="fp4",
        frozen_contract={}, candidates=[candidate], db_path=db,
    )
    packages.append_d2(batch_id=batch_id, ts_code="000001.SZ", selection_result=result,
                       playbook_result="target_reached", risk_tag=risk, raw={}, db_path=db)


def test_settled_aggregate_never_creates_a_composite_score(tmp_path):
    db = tmp_path / "scorecard.db"; init_schema(db)
    _seed(db, batch_id="a", params="params-a", channels=["p2", "p3"], result="success_realized", risk="risk")
    _seed(db, batch_id="b", params="params-b", channels=["p4"], result="confirmed_failed", revision=2)
    groups = packages.aggregate_settled(db_path=db)["groups"]
    assert len(groups) == 2
    first = next(group for group in groups if group["paramsPackageVersion"] == "params-a")
    assert first["channels"] == {"p2": 1, "p3": 1, "p4": 0}
    assert first["selectionResults"] == {"success_realized": 1}
    assert first["riskTags"] == {"risk": 1}
    assert "score" not in first and "composite" not in first


def test_aggregate_is_empty_without_a_v3_package(tmp_path):
    db = tmp_path / "scorecard.db"; init_schema(db)
    assert packages.aggregate_settled(db_path=db) == {"groups": []}
