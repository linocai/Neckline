"""策略大脑版本表单测(plan 1.9 + v1.2-A 激活时间线)。用 tmp_path DB 隔离,验证
写/读/激活唯一性/覆盖 + `activated_at` stamp/回填 + `config_active_at` 时间线解析。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from neckline.calendar import CN_TZ
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

def _set_timeline(db: Path, events, *, active: str = None) -> None:
    """造一条确定的激活时间线(不受 now() 抖动影响)。**v1.4 review 🟡-1 起时间轴事实源是
    append-only 表 `strategy_activation_log`**,故一律走共享 helper 写那张表(只 UPDATE
    `activated_at` 已不决定判向);实现与理由见 `conftest.set_activation_timeline`。"""
    from .conftest import set_activation_timeline

    set_activation_timeline(db, events, active=active)


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
    """INSERT OR REPLACE 抹列陷阱防线:activate=False 覆盖**已卸任**(曾激活过、现非现役)
    的行,activated_at 不被抹成 NULL(读回旧戳原样带回,历史时间线不丢)。"""
    v1 = brain.save_version("K1", {"config": {}}, "k1", activate=True, db_path=db)
    stamp = v1.activated_at
    assert stamp is not None
    brain.save_version("v1.3", {"config": {}, "lineage": "K1"}, "v13", activate=True, db_path=db)
    assert brain.get_active(db_path=db).version == "v1.3"       # K1 已卸任
    # 覆盖已卸任的 K1(如二次落库)——activated_at 应保全,is_active 仍 0
    brain.save_version("K1", {"config": {"x": 1}}, "k1改", activate=False, db_path=db)
    got = brain.get_version("K1", db_path=db)
    assert got.activated_at == stamp
    assert got.is_active is False


def test_save_version_refuses_to_demote_the_active_row(db):
    """审计 🔵-8:对**当前现役**行调 `activate=False` 会造成「全库无现役版本」→ 全线静默
    退回 MomentumConfig 默认(hold=3、无回落止盈、单笔 2 万),极危险 → 硬拒绝。"""
    brain.save_version("K1", {"config": {"stop_pct": 0.05}}, "k1", activate=True, db_path=db)
    with pytest.raises(ValueError, match="全库无现役版本"):
        brain.save_version("K1", {"config": {"stop_pct": 0.9}}, "改坏", activate=False, db_path=db)
    # 库未被改动:现役仍 K1,参数仍是原值(拒绝发生在写之前)
    assert brain.get_active(db_path=db).version == "K1"
    assert brain.active_config(db_path=db) == {"stop_pct": 0.05}


def test_save_version_allows_updating_active_row_when_staying_active(db):
    """阳性方向:带 `activate=True` 更新现役行的参数照常放行(护栏没有把正路堵死)。"""
    brain.save_version("K1", {"config": {"stop_pct": 0.05}}, "k1", activate=True, db_path=db)
    brain.save_version("K1", {"config": {"stop_pct": 0.06}}, "k1改", activate=True, db_path=db)
    assert brain.get_active(db_path=db).version == "K1"
    assert brain.active_config(db_path=db) == {"stop_pct": 0.06}


def test_save_version_new_inactive_row_unaffected(db):
    """阴性方向:落一行**新的**非现役版本(charter 脚本的正常姿势)不受护栏影响。"""
    brain.save_version("K1", {"config": {}}, "k1", activate=True, db_path=db)
    brain.save_version("v1.3", {"config": {}, "lineage": "K1"}, "v13", activate=False, db_path=db)
    assert brain.get_active(db_path=db).version == "K1"
    assert brain.get_version("v1.3", db_path=db).is_active is False


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
    _set_timeline(db, [("K1", "2026-07-20T00:00:00+00:00"), ("v1.2", "2026-08-01T00:00:00+00:00")])

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


# ======================================================================
#  v1.4-⑥-A:时刻粒度时间线解析(config_governing_at / activations_between)
#  —— 周复盘「按成交时刻逐笔判纪律」的基础。**时区是本节的主角**:
#     `activated_at` 落库是 UTC 戳,成交时刻是北京时间,不归一就会差 8 小时错判。
# ======================================================================

def _two_charter_db(db: Path, v13_activated_at: str) -> None:
    """K1(激活 2026-07-13T00:00Z)+ v1.3(按给定戳激活,现役)。"""
    brain.save_version("K1", {"config": {"single_cap": 20000.0}}, "k1", activate=True, db_path=db)
    brain.save_version("v1.3", {"config": {"single_cap": 40000.0}}, "v13", activate=False, db_path=db)
    _set_timeline(db, [("K1", "2026-07-13T00:00:00+00:00"), ("v1.3", v13_activated_at)])


def test_config_governing_at_utc_vs_beijing_not_confused(db):
    """**时区命门**:v1.3 激活于 UTC 08:00 = **北京 16:00**(收盘之后)。同日北京 15:00
    (收盘)的成交必须仍判 K1 —— 若把 UTC 戳当北京时间裸比(08:00 < 15:00)就会错判成
    v1.3,正是 ⑥-A 要防的 8 小时错位。"""
    _two_charter_db(db, "2026-07-22T08:00:00+00:00")
    close_cn = datetime(2026, 7, 22, 15, 0, tzinfo=CN_TZ)
    assert brain.config_governing_at(close_cn, db_path=db).version == "K1"
    # 次日收盘 → 已在激活之后 → v1.3
    assert brain.config_governing_at(datetime(2026, 7, 23, 15, 0, tzinfo=CN_TZ), db_path=db).version == "v1.3"


def test_config_governing_at_same_day_switch_before_close(db):
    """激活于 UTC 06:36(= 北京 14:36,盘中)→ 同日北京 15:00 收盘的成交判**新**章程
    (生产 2026-07-27 v1.3.3 激活的真实时刻形态)。"""
    _two_charter_db(db, "2026-07-27T06:36:10+00:00")
    assert brain.config_governing_at(datetime(2026, 7, 27, 15, 0, tzinfo=CN_TZ), db_path=db).version == "v1.3"
    # 同日更早的时刻(北京 10:00)仍在激活之前 → 旧章程
    assert brain.config_governing_at(datetime(2026, 7, 27, 10, 0, tzinfo=CN_TZ), db_path=db).version == "K1"


def test_config_governing_at_exact_activation_instant_counts_as_new(db):
    """**边界定死**:成交时刻**恰好等于**激活时刻 → 算新章程(判据 `激活时刻 <= ts`)。
    早一秒 → 旧章程。"""
    _two_charter_db(db, "2026-07-22T07:00:00+00:00")   # = 北京 15:00
    exact = datetime(2026, 7, 22, 15, 0, 0, tzinfo=CN_TZ)
    assert brain.config_governing_at(exact, db_path=db).version == "v1.3"
    assert brain.config_governing_at(exact - timedelta(seconds=1), db_path=db).version == "K1"


def test_config_governing_at_naive_input_read_as_beijing(db):
    """入参 naive datetime 按**北京时间**读(市场时刻口径;与 `activated_at` 的 naive
    按 UTC 读刻意相反,两处 docstring 各自定死)。"""
    _two_charter_db(db, "2026-07-22T08:00:00+00:00")   # = 北京 16:00
    assert brain.config_governing_at(datetime(2026, 7, 22, 15, 0), db_path=db).version == "K1"
    assert brain.config_governing_at(datetime(2026, 7, 22, 17, 0), db_path=db).version == "v1.3"


def test_config_governing_at_naive_activated_at_read_as_utc(db):
    """`activated_at` 若是**不带时区**的老串,按 **UTC** 读(唯一写入者 `_now()` 写的就是
    UTC;当成北京时间读会把激活时刻凭空前移 8 小时)。"""
    _two_charter_db(db, "2026-07-22T08:00:00")          # 无 tz 后缀 → 视作 UTC 08:00 = 北京 16:00
    assert brain.config_governing_at(datetime(2026, 7, 22, 15, 0, tzinfo=CN_TZ), db_path=db).version == "K1"
    assert brain.config_governing_at(datetime(2026, 7, 22, 17, 0, tzinfo=CN_TZ), db_path=db).version == "v1.3"


def test_config_governing_at_date_only_activated_at(db):
    """`activated_at` 是纯日期串 → 当日 00:00 UTC(= 北京 08:00)。"""
    _two_charter_db(db, "2026-07-22")
    assert brain.config_governing_at(datetime(2026, 7, 22, 7, 0, tzinfo=CN_TZ), db_path=db).version == "K1"
    assert brain.config_governing_at(datetime(2026, 7, 22, 9, 0, tzinfo=CN_TZ), db_path=db).version == "v1.3"


def test_config_governing_at_deep_past_and_legacy_and_empty(db):
    """三条兜底与 `config_active_at` 同款:早于所有激活 → 最早激活版本;整表无戳 →
    退回 get_active();空库 → None。"""
    _two_charter_db(db, "2026-08-01T00:00:00+00:00")
    assert brain.config_governing_at(datetime(2020, 1, 1, tzinfo=CN_TZ), db_path=db).version == "K1"

    legacy = db.parent / "legacy.db"
    init_schema(legacy)
    with connection(legacy) as conn:
        conn.execute(
            "INSERT INTO strategy_versions "
            "(version, created_at, rule_json, changelog, metrics_json, is_active, activated_at) "
            "VALUES ('K1','2026-07-20T00:00:00+00:00','{}','k1','{}',1,NULL)",
        )
    assert brain.config_governing_at(datetime(2026, 8, 1, tzinfo=CN_TZ), db_path=legacy).version == "K1"

    empty = db.parent / "empty.db"
    init_schema(empty)
    assert brain.config_governing_at(datetime(2026, 8, 1, tzinfo=CN_TZ), db_path=empty) is None


def test_activations_between_half_open_window(db):
    """`activations_between` 取 **[start, end)**(半开)——相邻周不会把同一次激活各算一遍。"""
    _two_charter_db(db, "2026-07-27T06:36:10+00:00")
    week31_lo = datetime(2026, 7, 27, 0, 0, tzinfo=CN_TZ)
    week31_hi = datetime(2026, 8, 3, 0, 0, tzinfo=CN_TZ)
    got = brain.activations_between(week31_lo, week31_hi, db_path=db)
    assert [v.version for _, v in got] == ["v1.3"]
    # 上一周窗口(07-20~07-26)不含它
    assert brain.activations_between(
        datetime(2026, 7, 20, tzinfo=CN_TZ), week31_lo, db_path=db,
    ) == []
    # 边界:激活时刻恰为窗口右端 → 不含(半开)
    at = got[0][0].astimezone(CN_TZ)
    assert brain.activations_between(week31_lo, at, db_path=db) == []
    assert len(brain.activations_between(at, week31_hi, db_path=db)) == 1


def test_activations_between_sorted_and_skips_unstamped(db):
    """按时刻升序;从未激活过(activated_at NULL)的版本不参与。"""
    _two_charter_db(db, "2026-07-27T06:36:10+00:00")
    brain.save_version("K9", {"config": {}}, "never", activate=False, db_path=db)   # 无激活戳
    got = brain.activations_between(
        datetime(2026, 7, 1, tzinfo=CN_TZ), datetime(2026, 8, 1, tzinfo=CN_TZ), db_path=db,
    )
    assert [v.version for _, v in got] == ["K1", "v1.3"]
    assert got[0][0] < got[1][0]


# ======================================================================
#  v1.4 review 🟡-1(2026-07-29):激活历史 append-only 表 `strategy_activation_log`
#  —— 回滚 / 重激活**不得改写历史判定**。旧模型「一版一个 activated_at 戳」表达不了
#     「被激活过两次」,回滚会把戳前移 → 回滚前那段历史整段改判 = 洗白口(判定线审计
#     🟡-1 实测复现)。本节锁死修复后的语义。
# ======================================================================

def _log_rows(db: Path):
    with connection(db) as conn:
        return conn.execute(
            "SELECT version, activated_at, via FROM strategy_activation_log ORDER BY id"
        ).fetchall()


class TestActivationLogAppendOnly:
    def test_rollback_reactivation_does_not_rewrite_timeline(self, db):
        """**命门反例(🟡-1 的复现改成回归护栏)**:K1(07-20 激活)→ v1.3(07-25 激活)后
        **回滚重激活 K1**(今天)。回滚只在时间轴末尾追加一个事件:
          · 07-22(K1 治下)→ 仍判 K1 —— **旧实现在这里改判 v1.3 = 违纪被 4 万上限洗白**;
          · 07-26(v1.3 治下)→ 仍判 v1.3 —— 回滚不吞掉中间那段治权;
          · 回滚之后 → 判 K1(回滚确实生效,不是把回滚忽略掉)。
        周标签(`config_governing_for_week`)同样不被改写。"""
        brain.save_version("K1", {"config": {"single_cap": 20000.0}}, "k1", activate=True, db_path=db)
        brain.save_version("v1.3", {"config": {"single_cap": 40000.0}}, "v13", activate=False, db_path=db)
        _set_timeline(db, [("K1", "2026-07-20T00:00:00+00:00"), ("v1.3", "2026-07-25T00:00:00+00:00")])

        t_0722 = datetime(2026, 7, 22, 15, 0, tzinfo=CN_TZ)
        t_0726 = datetime(2026, 7, 26, 15, 0, tzinfo=CN_TZ)
        before = (brain.config_governing_at(t_0722, db_path=db).version,
                  brain.config_governing_at(t_0726, db_path=db).version,
                  brain.config_governing_for_week(date(2026, 7, 20), db_path=db).version,
                  brain.config_active_at(date(2026, 7, 22), db_path=db).version)
        assert before == ("K1", "v1.3", "K1", "K1")

        brain.activate_version("K1", db_path=db)          # ← 回滚(切换器白名单内的合法路径)

        after = (brain.config_governing_at(t_0722, db_path=db).version,
                 brain.config_governing_at(t_0726, db_path=db).version,
                 brain.config_governing_for_week(date(2026, 7, 20), db_path=db).version,
                 brain.config_active_at(date(2026, 7, 22), db_path=db).version)
        assert after == before, "回滚重激活改写了历史判定 = 洗白口复活"
        # 回滚本身生效:此刻之后按 K1 判
        assert brain.config_governing_at(
            datetime.now(timezone.utc) + timedelta(hours=1), db_path=db).version == "K1"

    def test_reactivation_appends_never_updates(self, db):
        """append-only 不变式:重激活**追加**一行,既有行原样不动(行数只增不减,
        第一条事件的 (version, at) 逐字节不变)。"""
        brain.save_version("K1", {"config": {}}, "k1", activate=True, db_path=db)
        brain.save_version("v1.3", {"config": {}}, "v13", activate=False, db_path=db)
        first = _log_rows(db)
        assert [r[0] for r in first] == ["K1"] and first[0][2] == "save_version"

        brain.activate_version("v1.3", db_path=db)
        brain.activate_version("K1", db_path=db)          # 回滚
        rows = _log_rows(db)
        assert [r[0] for r in rows] == ["K1", "v1.3", "K1"]
        assert rows[0] == first[0]                        # 第一条事件一字未改
        assert [r[2] for r in rows[1:]] == ["activate_version", "activate_version"]
        # 兼容列 = 该版**最后一次**激活(不变式),而历史仍在表里
        assert brain.get_version("K1", db_path=db).activated_at == rows[2][1]

    def test_multi_activation_timeline_resolves_each_segment(self, db):
        """K1 → v1.3.3 → 回退 v1.3 三段治权,逐段解析各归各的(一个版本出现两次也不串)。"""
        for name, cap in (("K1", 20000.0), ("v1.3", 40000.0), ("v1.3.3", 40000.0)):
            brain.save_version(name, {"config": {"single_cap": cap}}, name, activate=False, db_path=db)
        _set_timeline(db, [
            ("K1", "2026-07-13T00:00:00+00:00"),
            ("v1.3.3", "2026-07-20T02:00:00+00:00"),
            ("v1.3", "2026-07-27T02:00:00+00:00"),        # 回退目标(白名单里唯一合法的那个)
        ])
        at = lambda d: brain.config_governing_at(datetime(2026, 7, d, 15, 0, tzinfo=CN_TZ), db_path=db).version
        assert (at(14), at(21), at(28)) == ("K1", "v1.3.3", "v1.3")
        assert [v for _, v in brain.activation_history(db_path=db)] == ["K1", "v1.3.3", "v1.3"]

    def test_same_instant_events_last_appended_wins(self, db):
        """同一时刻的两条事件按追加顺序(id)定序 —— `candidates[-1]` 取到后追加的那条。"""
        brain.save_version("K1", {"config": {}}, "k1", activate=False, db_path=db)
        brain.save_version("v1.3", {"config": {}}, "v13", activate=False, db_path=db)
        _set_timeline(db, [("K1", "2026-07-20T00:00:00+00:00"), ("v1.3", "2026-07-20T00:00:00+00:00")])
        assert brain.config_governing_at(
            datetime(2026, 7, 20, 15, 0, tzinfo=CN_TZ), db_path=db).version == "v1.3"


class TestActivationLogMigration:
    def test_seeded_from_legacy_activated_at_and_idempotent(self, db):
        """老库播种(幂等):`strategy_versions.activated_at` 的单戳时间线经 `init_schema`
        一次性搬进历史表;**重跑不重复播种**(否则每次 init_schema 都会往历史里灌重复事件)。"""
        init_schema(db)
        with connection(db) as conn:
            conn.execute(
                "INSERT INTO strategy_versions "
                "(version, created_at, rule_json, changelog, metrics_json, is_active, activated_at) "
                "VALUES ('K1','2026-07-13T00:00:00+00:00','{}','k1','{}',0,'2026-07-13T00:00:00+00:00'),"
                "       ('v1.3','2026-07-25T00:00:00+00:00','{}','v13','{}',1,'2026-07-25T00:00:00+00:00'),"
                "       ('K9','2026-07-26T00:00:00+00:00','{}','k9','{}',0,NULL)",
            )
            conn.execute("DELETE FROM strategy_activation_log")   # 模拟"本表还没播过种"的老库
        init_schema(db)
        rows = _log_rows(db)
        assert [(r[0], r[2]) for r in rows] == [("K1", "seed"), ("v1.3", "seed")]  # 未激活过的 K9 不播
        init_schema(db)
        assert _log_rows(db) == rows                              # 幂等:重跑不再灌

    def test_single_activation_log_equals_legacy_stamp_path(self, db):
        """**等价护栏**:单次激活场景下,「读历史表」与「读 activated_at 单戳」两条路径
        逐位同物 —— 播种前后、有表无表,判向不得有任何差别(⑥-A 既有 golden 靠这条不塌)。"""
        brain.save_version("K1", {"config": {}}, "k1", activate=True, db_path=db)
        brain.save_version("v1.3", {"config": {}}, "v13", activate=False, db_path=db)
        _set_timeline(db, [("K1", "2026-07-13T00:00:00+00:00"), ("v1.3", "2026-07-22T08:00:00+00:00")])
        probes = [datetime(2026, 7, d, h, 0, tzinfo=CN_TZ) for d in (13, 22, 23) for h in (7, 15, 17)]
        with_log = [brain.config_governing_at(p, db_path=db).version for p in probes]
        days = [date(2026, 7, d) for d in (12, 13, 22, 23)]
        with_log_days = [brain.config_active_at(d, db_path=db).version for d in days]

        with connection(db) as conn:                 # 抹掉历史表 → 回退单戳路径
            conn.execute("DROP TABLE strategy_activation_log")
        assert [brain.config_governing_at(p, db_path=db).version for p in probes] == with_log
        assert [brain.config_active_at(d, db_path=db).version for d in days] == with_log_days

    def test_reads_tolerate_missing_log_table(self, db):
        """读入口**不触发迁移**(既有纪律),故必须容忍历史表不存在的老库:裸 SELECT 会炸
        `no such table`。造一张只有 strategy_versions 的库,四个读入口全部不崩。"""
        with connection(db) as conn:
            conn.execute(
                "CREATE TABLE strategy_versions ("
                "version TEXT PRIMARY KEY, created_at TEXT NOT NULL, rule_json TEXT NOT NULL, "
                "changelog TEXT NOT NULL, metrics_json TEXT NOT NULL DEFAULT '{}', "
                "is_active INTEGER NOT NULL DEFAULT 0, activated_at TEXT)"
            )
            conn.execute(
                "INSERT INTO strategy_versions VALUES "
                "('K1','2026-07-13T00:00:00+00:00',?,'k1','{}',1,'2026-07-13T00:00:00+00:00')",
                (json.dumps({"config": {"single_cap": 20000.0}}),),
            )
        ts = datetime(2026, 7, 22, 15, 0, tzinfo=CN_TZ)
        assert brain.config_governing_at(ts, db_path=db).version == "K1"
        assert brain.config_active_at(date(2026, 7, 22), db_path=db).version == "K1"
        assert brain.config_governing_for_week(date(2026, 7, 20), db_path=db).version == "K1"
        assert [v for _, v in brain.activation_history(db_path=db)] == ["K1"]

    def test_event_pointing_at_deleted_version_is_skipped_loudly(self, db):
        """诚实降级:事件指向已不存在的版本行 → 跳过 + WARNING(不静默少判一段历史)。"""
        import logging

        brain.save_version("K1", {"config": {}}, "k1", activate=True, db_path=db)
        _set_timeline(db, [("K1", "2026-07-13T00:00:00+00:00"), ("幽灵版", "2026-07-25T00:00:00+00:00")],
                      active="K1")
        logger = logging.getLogger("neckline.strategy.brain")
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        logger.addHandler(handler)
        try:
            got = brain.config_governing_at(datetime(2026, 8, 1, tzinfo=CN_TZ), db_path=db)
        finally:
            logger.removeHandler(handler)
        assert got.version == "K1"
        assert any("幽灵版" in r.getMessage() for r in records)
