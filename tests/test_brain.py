"""策略大脑版本表单测(plan 1.9)。用 tmp_path DB 隔离,验证写/读/激活唯一性/覆盖。"""

from __future__ import annotations

from pathlib import Path

import pytest

from neckline.strategy import brain


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "brain.db"


def test_save_and_get_roundtrip(db):
    rule = {"config": {"strength": "none", "stop_pct": 0.05}, "market_filter": True}
    v = brain.save_version("v1", rule, "首版", metrics={"pf_out": 0.98}, db_path=db)
    assert v.version == "v1" and v.is_active
    got = brain.get_version("v1", db_path=db)
    assert got.rule == rule
    assert got.metrics["pf_out"] == 0.98
    assert got.changelog == "首版"


def test_activation_is_unique(db):
    brain.save_version("v1", {"a": 1}, "v1", db_path=db, activate=True)
    brain.save_version("v2", {"a": 2}, "v2", db_path=db, activate=True)
    active = brain.get_active(db_path=db)
    assert active.version == "v2"
    # v1 应被置为非现役
    assert brain.get_version("v1", db_path=db).is_active is False
    assert [v.version for v in brain.list_versions(db_path=db)] == ["v1", "v2"]


def test_save_inactive_does_not_flip_active(db):
    brain.save_version("v1", {"a": 1}, "v1", db_path=db, activate=True)
    brain.save_version("v2-exp", {"a": 2}, "实验版", db_path=db, activate=False)
    assert brain.get_active(db_path=db).version == "v1"  # 现役仍是 v1


def test_overwrite_same_version(db):
    brain.save_version("v1", {"a": 1}, "初", db_path=db)
    brain.save_version("v1", {"a": 2}, "改", db_path=db)
    got = brain.get_version("v1", db_path=db)
    assert got.rule == {"a": 2} and got.changelog == "改"
    assert len(brain.list_versions(db_path=db)) == 1  # 覆盖非新增
