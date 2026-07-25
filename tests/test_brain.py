"""策略大脑版本表单测(plan 1.9 + v1.2-A 激活时间线)。用 tmp_path DB 隔离,验证
写/读/激活唯一性/覆盖 + `activated_at` stamp/回填 + `config_active_at` 时间线解析。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from neckline.db import connection, init_schema
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


# ======================================================================
#  v1.2-A:激活时间戳(activated_at)+ 时间线解析(config_active_at)
# ======================================================================

def _set_activated(db: Path, version: str, iso: str, is_active: int) -> None:
    """测试专用:显式设定某版本的 activated_at / is_active(模拟一条确定的激活时间线,
    不受 now() 抖动影响)。config_active_at 不触发 init_schema/回填,故此设定稳定。"""
    with connection(db) as conn:
        conn.execute(
            "UPDATE strategy_versions SET activated_at=?, is_active=? WHERE version=?",
            (iso, is_active, version),
        )


def test_save_active_stamps_activated_at(db):
    """save_version(activate=True) 应回填 activated_at(向后兼容,既有调用点自动获得)。"""
    v = brain.save_version("K1", {"config": {"single_cap": 20000.0}}, "k1", activate=True, db_path=db)
    assert v.activated_at is not None
    assert v.activated_at == v.created_at  # 激活戳 = 该次 save 的 created(现役=激活)


def test_save_inactive_leaves_activated_at_null(db):
    """save_version(activate=False) 落新行 activated_at 应为 NULL(从未激活)。"""
    brain.save_version("K1", {"config": {}}, "k1", activate=True, db_path=db)
    v12 = brain.save_version("v1.2", {"config": {}, "lineage": "K1"}, "v12", activate=False, db_path=db)
    assert v12.activated_at is None
    assert brain.get_active(db_path=db).version == "K1"  # 现役仍 K1


def test_inactive_overwrite_preserves_prior_activated_at(db):
    """INSERT OR REPLACE 抹列陷阱防线:activate=False 覆盖既有已激活行,activated_at
    不被抹成 NULL(读回旧戳原样带回)。"""
    v1 = brain.save_version("K1", {"config": {}}, "k1", activate=True, db_path=db)
    stamp = v1.activated_at
    assert stamp is not None
    # 再以 activate=False 覆盖 K1(如二次落库)——activated_at 应保全
    brain.save_version("K1", {"config": {"x": 1}}, "k1改", activate=False, db_path=db)
    got = brain.get_version("K1", db_path=db)
    assert got.activated_at == stamp
    assert got.is_active is False  # activate=False 也把它降为非现役(既有语义)


def test_activate_version_stamps_and_unique(db):
    """activate_version:置目标 is_active=1 + stamp activated_at,其余 is_active=0 但
    保留它们的 activated_at(历史时间线不清空);现役唯一。"""
    brain.save_version("K1", {"config": {}}, "k1", activate=True, db_path=db)
    k1_stamp = brain.get_version("K1", db_path=db).activated_at
    brain.save_version("v1.2", {"config": {}, "lineage": "K1"}, "v12", activate=False, db_path=db)

    v12 = brain.activate_version("v1.2", db_path=db)
    assert v12.is_active and v12.activated_at is not None
    # 唯一现役
    actives = [v.version for v in brain.list_versions(db_path=db) if v.is_active]
    assert actives == ["v1.2"]
    # K1 被降为非现役,但 activated_at 仍在(时间线不清空)
    k1_after = brain.get_version("K1", db_path=db)
    assert k1_after.is_active is False
    assert k1_after.activated_at == k1_stamp


def test_activate_missing_version_raises(db):
    brain.save_version("K1", {"config": {}}, "k1", activate=True, db_path=db)
    with pytest.raises(ValueError):
        brain.activate_version("v9.9", db_path=db)


def test_config_active_at_timeline(db):
    """时间线解析:激活前的周取旧版本、激活后取新版本、多版本升序边界。"""
    brain.save_version("K1", {"config": {"single_cap": 20000.0}}, "k1", activate=True, db_path=db)
    brain.save_version("v1.2", {"config": {"single_cap": 40000.0}}, "v12", activate=False, db_path=db)
    # 造确定时间线:K1 激活 2026-07-20,v1.2 激活 2026-08-01(现役)
    _set_activated(db, "K1", "2026-07-20T00:00:00+00:00", 0)
    _set_activated(db, "v1.2", "2026-08-01T00:00:00+00:00", 1)

    # 激活前深过去(早于 K1 激活)→ 取最早激活版本 K1(不臆造更早历史)
    assert brain.config_active_at(date(2026, 1, 1), db_path=db).version == "K1"
    # K1 激活日当天(边界含)→ K1
    assert brain.config_active_at(date(2026, 7, 20), db_path=db).version == "K1"
    # K1 与 v1.2 之间 → K1
    assert brain.config_active_at(date(2026, 7, 26), db_path=db).version == "K1"
    # v1.2 激活日当天(边界含)→ v1.2
    assert brain.config_active_at(date(2026, 8, 1), db_path=db).version == "v1.2"
    # 之后 → v1.2
    assert brain.config_active_at(date(2026, 9, 1), db_path=db).version == "v1.2"


def test_config_active_at_legacy_fallback(db):
    """整表无任何 activated_at(纯 legacy 老库)→ 退回 get_active() = v1.2 之前旧行为。
    直接插一条 is_active=1 且 activated_at NULL 的行(config_active_at 不触发回填)。"""
    init_schema(db)  # 建表(空表,回填 no-op)
    with connection(db) as conn:
        conn.execute(
            "INSERT INTO strategy_versions "
            "(version, created_at, rule_json, changelog, metrics_json, is_active, activated_at) "
            "VALUES (?,?,?,?,?,1,NULL)",
            ("K1", "2026-07-20T00:00:00+00:00", json.dumps({"config": {"single_cap": 20000.0}}), "legacy", "{}"),
        )
    # 无 stamped → 退回 get_active(),任何 ref 都取当前现役 K1(当前现役判全部周)
    assert brain.config_active_at(date(2020, 1, 1), db_path=db).version == "K1"
    assert brain.config_active_at(date(2099, 1, 1), db_path=db).version == "K1"


def test_config_active_at_none_when_empty(db):
    """无任何版本 → config_active_at 返 None(run_weekly_review 据此诚实跳过检查)。"""
    init_schema(db)
    assert brain.config_active_at(date(2026, 7, 20), db_path=db) is None


def test_reads_tolerate_pre_migration_schema(db):
    """robustness(命门):读入口(get_active/get_version/list_versions/config_active_at/
    active_config)**不触发迁移**,故必须容忍老库无 activated_at 列——裸 SELECT 该列会炸
    `no such column`(k2/k3 guardrail 读真实未迁移库时真实踩到)。造一张**旧 schema**的
    strategy_versions(无 activated_at 列)验证读入口不崩、activated_at 读回 None。"""
    with connection(db) as conn:
        conn.execute(
            "CREATE TABLE strategy_versions ("
            "version TEXT PRIMARY KEY, created_at TEXT NOT NULL, rule_json TEXT NOT NULL, "
            "changelog TEXT NOT NULL, metrics_json TEXT NOT NULL DEFAULT '{}', "
            "is_active INTEGER NOT NULL DEFAULT 0)"   # 故意不含 activated_at(v1.2 之前老 schema)
        )
        conn.execute(
            "INSERT INTO strategy_versions VALUES ('K1','2026-07-20T00:00:00+00:00',?,'k1','{}',1)",
            (json.dumps({"config": {"single_cap": 20000.0}}),),
        )
    assert brain.get_active(db_path=db).version == "K1"
    assert brain.get_active(db_path=db).activated_at is None
    assert brain.get_version("K1", db_path=db).activated_at is None
    assert [v.version for v in brain.list_versions(db_path=db)] == ["K1"]
    assert brain.active_config(db_path=db)["single_cap"] == 20000.0
    # config_active_at:无 stamped → legacy 兜底退回 get_active(),不崩
    assert brain.config_active_at(date(2026, 8, 1), db_path=db).version == "K1"


def test_backfill_active_version_on_migration(db):
    """一次性回填(幂等):is_active=1 且 activated_at NULL 的现役版本经 init_schema
    回填 activated_at=created_at;is_active=0 的版本保持 NULL;重跑不变。"""
    init_schema(db)
    with connection(db) as conn:
        conn.execute(
            "INSERT INTO strategy_versions "
            "(version, created_at, rule_json, changelog, metrics_json, is_active, activated_at) "
            "VALUES ('K1','2026-07-20T03:07:52+00:00','{}','k1','{}',1,NULL),"
            "       ('K2','2026-07-22T13:06:11+00:00','{}','k2','{}',0,NULL)",
        )
    init_schema(db)  # 触发回填
    k1 = brain.get_version("K1", db_path=db)
    k2 = brain.get_version("K2", db_path=db)
    assert k1.activated_at == "2026-07-20T03:07:52+00:00"  # 回填 = created_at
    assert k2.activated_at is None  # 从未激活,保持 NULL
    # 幂等:再跑一次不变(不会把 K1 戳刷新成别的)
    init_schema(db)
    assert brain.get_version("K1", db_path=db).activated_at == "2026-07-20T03:07:52+00:00"
