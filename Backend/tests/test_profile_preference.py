"""⑫-B 偏好画像引擎单测(`neckline/profile/preference.py`)。

覆盖:四维分桶(题材/角色/入场方式/Tier)占比与样本量算对;单笔不成偏好但仍
出行、`confidence` 如实标 low;维度不串(同一份数据算出的四个维度互不覆盖对方
的桶);零数据 → 空列表(不是异常);`entry_label` 维度读 `user_actions.
kind='label'`(plan ⑫-B 原文数据源之一)、一笔买入多标签按标签实例计数、没贴
标签的买入不拉低该维度自己的样本量;`store.py` 落库/读回逐位一致(每期一版,
`INSERT OR REPLACE` 幂等)。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from neckline import user_actions
from neckline.db import connection, init_schema
from neckline.profile import common as pc
from neckline.profile import preference as pref
from neckline.profile import store as profile_store
from tests.conftest import insert_stock_basic

pytestmark = pytest.mark.usefixtures("isolated_env")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _insert_position(db_path: Path, *, ts_code: str, buy_date: str, buy_price: float = 10.0) -> int:
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO positions (ts_code, buy_price, qty, buy_date, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (ts_code, buy_price, 100, buy_date, "open", now, now),
        )
        return int(cur.lastrowid)


def _insert_snapshot(db_path: Path, position_id: int, ts_code: str, buy_date: str, *,
                     basket_id: Optional[int] = None, tier: Optional[int] = None,
                     role: Optional[str] = None) -> None:
    now = _now()
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO entry_snapshots (position_id, ts_code, trade_date, basket_id, "
            "card_version, tier, role, snapshot_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (position_id, ts_code, buy_date, basket_id, None, tier, role, "{}", now),
        )


def _seed_buy(db_path: Path, *, ts_code: str, buy_date: str, industry: Optional[str] = None,
             tier: Optional[int] = None, role: Optional[str] = None,
             basket_id: Optional[int] = None) -> int:
    if industry is not None:
        insert_stock_basic_row(db_path, ts_code, industry)
    pid = _insert_position(db_path, ts_code=ts_code, buy_date=buy_date)
    _insert_snapshot(db_path, pid, ts_code, buy_date, basket_id=basket_id, tier=tier, role=role)
    return pid


def insert_stock_basic_row(db_path: Path, ts_code: str, industry: str) -> None:
    import sqlite3

    init_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_basic (ts_code, symbol, name, industry, market, list_status) "
            "VALUES (?,?,?,?,?,?)",
            (ts_code, ts_code.split(".")[0], ts_code, industry, "主板", "L"),
        )
        conn.commit()
    finally:
        conn.close()


class TestComputePreference:
    def test_empty_window_is_empty(self, isolated_env):
        assert pref.compute_preference("20260701", "20260731", db_path=isolated_env.db_path) == []

    def test_share_and_sample_n_add_up(self, isolated_env):
        env = isolated_env
        _seed_buy(env.db_path, ts_code="600001.SH", buy_date="20260710", role="leader")
        _seed_buy(env.db_path, ts_code="600002.SH", buy_date="20260711", role="leader")
        _seed_buy(env.db_path, ts_code="600003.SH", buy_date="20260712", role="core")
        rows = pref.compute_preference("20260701", "20260731", db_path=env.db_path)
        role_rows = {r.value: r for r in rows if r.dimension == pc.DIM_ROLE}
        assert role_rows["leader"].sample_n == 2
        assert role_rows["leader"].share == pytest.approx(2 / 3, abs=1e-3)
        assert role_rows["core"].sample_n == 1
        assert role_rows["core"].share == pytest.approx(1 / 3, abs=1e-3)
        # 占比之和为 1(同一维度内;share 四舍五入到 4 位小数,容差同上）
        assert sum(r.share for r in role_rows.values()) == pytest.approx(1.0, abs=1e-3)

    def test_single_buy_still_gets_a_row_but_confidence_is_low(self, isolated_env):
        """「单笔不成偏好」不等于「不出行」——偏好是统计事实,只是标 low 置信度。"""
        env = isolated_env
        _seed_buy(env.db_path, ts_code="600001.SH", buy_date="20260710", role="leader")
        rows = pref.compute_preference("20260701", "20260731", db_path=env.db_path)
        role_row = next(r for r in rows if r.dimension == pc.DIM_ROLE)
        assert role_row.sample_n == 1
        assert role_row.share == pytest.approx(1.0)
        assert role_row.confidence == pc.CONFIDENCE_LOW

    def test_high_confidence_when_sample_is_large(self, isolated_env):
        env = isolated_env
        for i in range(pc.MEDIUM_SAMPLE_N):
            _seed_buy(env.db_path, ts_code=f"6000{i:02d}.SH", buy_date=f"202607{10 + i:02d}", role="leader")
        rows = pref.compute_preference("20260701", "20260731", db_path=env.db_path)
        role_row = next(r for r in rows if r.dimension == pc.DIM_ROLE)
        assert role_row.sample_n == pc.MEDIUM_SAMPLE_N
        assert role_row.confidence == pc.CONFIDENCE_HIGH

    def test_dimensions_do_not_bleed_into_each_other(self, isolated_env):
        """维度不串:题材桶的 value 集合与角色桶的 value 集合互不相关(验收条款
        「两张画像表分别产出且维度不串」在偏好画像内部的对应体现)。"""
        env = isolated_env
        _seed_buy(env.db_path, ts_code="600001.SH", buy_date="20260710",
                 industry="半导体", role="leader", tier=1)
        _seed_buy(env.db_path, ts_code="600002.SH", buy_date="20260711",
                 industry="新能源", role="core", tier=2)
        rows = pref.compute_preference("20260701", "20260731", db_path=env.db_path)
        dims = {r.dimension for r in rows}
        assert dims == {pc.DIM_THEME, pc.DIM_ROLE, pc.DIM_ENTRY_STYLE, pc.DIM_TIER}
        theme_values = {r.value for r in rows if r.dimension == pc.DIM_THEME}
        role_values = {r.value for r in rows if r.dimension == pc.DIM_ROLE}
        assert theme_values == {"半导体", "新能源"}
        assert role_values == {"leader", "core"}
        assert theme_values.isdisjoint(role_values)

    def test_independent_buys_bucket_as_independent(self, isolated_env):
        env = isolated_env
        _seed_buy(env.db_path, ts_code="600001.SH", buy_date="20260710")   # 无 basket/tier/role
        rows = pref.compute_preference("20260701", "20260731", db_path=env.db_path)
        role_row = next(r for r in rows if r.dimension == pc.DIM_ROLE)
        tier_row = next(r for r in rows if r.dimension == pc.DIM_TIER)
        assert role_row.value == pc.ROLE_INDEPENDENT
        assert tier_row.value == pc.TIER_INDEPENDENT

    def test_window_bounds_match_buy_dates(self, isolated_env):
        env = isolated_env
        _seed_buy(env.db_path, ts_code="600001.SH", buy_date="20260705")
        _seed_buy(env.db_path, ts_code="600002.SH", buy_date="20260720")
        rows = pref.compute_preference("20260701", "20260731", db_path=env.db_path)
        assert all(r.window_start == "20260705" and r.window_end == "20260720" for r in rows)


class TestEntryLabelDimension:
    """`entry_label` 维度(plan ⑫-B「数据源含 user_actions」的落点)。"""

    def test_no_labels_yields_no_rows(self, isolated_env):
        env = isolated_env
        _seed_buy(env.db_path, ts_code="600001.SH", buy_date="20260710")
        rows = pref.compute_preference("20260701", "20260731", db_path=env.db_path)
        assert not any(r.dimension == pc.DIM_ENTRY_LABEL for r in rows)

    def test_single_label_counts_once(self, isolated_env):
        env = isolated_env
        pid = _seed_buy(env.db_path, ts_code="600001.SH", buy_date="20260710")
        user_actions.record("label", position_id=pid, payload={"labels": ["THEME_SHIFT"]},
                            db_path=env.db_path)
        rows = pref.compute_preference("20260701", "20260731", db_path=env.db_path)
        label_rows = {r.value: r for r in rows if r.dimension == pc.DIM_ENTRY_LABEL}
        assert label_rows["THEME_SHIFT"].sample_n == 1
        assert label_rows["THEME_SHIFT"].share == pytest.approx(1.0)

    def test_multi_label_buy_counts_each_label_as_its_own_instance(self, isolated_env):
        """一笔买入同时贴两个标签 → 两个 label 值各计一次(不是摊成 0.5)。"""
        env = isolated_env
        pid = _seed_buy(env.db_path, ts_code="600001.SH", buy_date="20260710")
        user_actions.record("label", position_id=pid,
                            payload={"labels": ["THEME_SHIFT", "NEWS_CATALYST"]}, db_path=env.db_path)
        rows = pref.compute_preference("20260701", "20260731", db_path=env.db_path)
        label_rows = {r.value: r for r in rows if r.dimension == pc.DIM_ENTRY_LABEL}
        assert label_rows["THEME_SHIFT"].sample_n == 1
        assert label_rows["NEWS_CATALYST"].sample_n == 1
        assert label_rows["THEME_SHIFT"].share == pytest.approx(0.5)   # 2 个实例里各占一半

    def test_unlabeled_buys_do_not_dilute_label_dimension_sample(self, isolated_env):
        """3 笔买入只有 1 笔贴了标签 → `entry_label` 维度的样本量是 1(该维度自己
        的分母),不是被稀释成"3 笔里 1 笔贴标签"这种混合口径。"""
        env = isolated_env
        pid1 = _seed_buy(env.db_path, ts_code="600001.SH", buy_date="20260710")
        _seed_buy(env.db_path, ts_code="600002.SH", buy_date="20260711")
        _seed_buy(env.db_path, ts_code="600003.SH", buy_date="20260712")
        user_actions.record("label", position_id=pid1, payload={"labels": ["PURE_TAPE_READING"]},
                            db_path=env.db_path)
        rows = pref.compute_preference("20260701", "20260731", db_path=env.db_path)
        label_row = next(r for r in rows if r.dimension == pc.DIM_ENTRY_LABEL)
        assert label_row.sample_n == 1
        assert label_row.share == pytest.approx(1.0)
        # 但 role/tier/theme 等其它维度仍然是全部 3 笔的样本量,不受标签稀疏影响
        role_row = next(r for r in rows if r.dimension == pc.DIM_ROLE)
        assert role_row.sample_n == 3

    def test_label_on_position_outside_window_is_excluded(self, isolated_env):
        env = isolated_env
        pid_outside = _seed_buy(env.db_path, ts_code="600001.SH", buy_date="20260801")   # 窗口外
        user_actions.record("label", position_id=pid_outside, payload={"labels": ["THEME_SHIFT"]},
                            db_path=env.db_path)
        rows = pref.compute_preference("20260701", "20260731", db_path=env.db_path)
        assert not any(r.dimension == pc.DIM_ENTRY_LABEL for r in rows)


class TestProfileStoreRoundTrip:
    def test_save_and_load_preference_round_trips(self, isolated_env):
        env = isolated_env
        _seed_buy(env.db_path, ts_code="600001.SH", buy_date="20260710", role="leader")
        rows = pref.compute_preference("20260701", "20260731", db_path=env.db_path)
        n = profile_store.save_preference("20260731", rows, db_path=env.db_path)
        assert n == len(rows)
        loaded = profile_store.load_preference("20260731", db_path=env.db_path)
        assert len(loaded) == len(rows)
        role_loaded = next(r for r in loaded if r["dimension"] == pc.DIM_ROLE)
        assert role_loaded["value"] == "leader"
        assert role_loaded["confidence"] == pc.CONFIDENCE_LOW

    def test_recompute_same_period_overwrites_not_duplicates(self, isolated_env):
        """「每期一版」:同一 `as_of_date` 重新落库覆盖旧行,不是 append 出重复行。"""
        env = isolated_env
        _seed_buy(env.db_path, ts_code="600001.SH", buy_date="20260710", role="leader")
        rows1 = pref.compute_preference("20260701", "20260731", db_path=env.db_path)
        profile_store.save_preference("20260731", rows1, db_path=env.db_path)

        _seed_buy(env.db_path, ts_code="600002.SH", buy_date="20260711", role="leader")
        rows2 = pref.compute_preference("20260701", "20260731", db_path=env.db_path)
        profile_store.save_preference("20260731", rows2, db_path=env.db_path)

        loaded = profile_store.load_preference("20260731", db_path=env.db_path)
        role_loaded = [r for r in loaded if r["dimension"] == pc.DIM_ROLE and r["value"] == "leader"]
        assert len(role_loaded) == 1   # 覆盖而非重复
        assert role_loaded[0]["sampleN"] == 2   # 数字已更新为第二次算出的值

    def test_recompute_drops_values_that_no_longer_exist(self, isolated_env):
        """🟡 Y3(契约线审计 2026-08-03):同期重算时,**新一轮不再产出的旧 `value` 行
        必须消失** —— 只靠 UPSERT 的话它们原样残留,当期就成了两次运行的并集:
        某个占比已经归零的取值仍以旧 `share` 挂着,**该维度 share 合计 > 1**。"""
        env = isolated_env
        rows1 = [
            pref.PreferenceRow(dimension=pc.DIM_ROLE, value="leader", share=0.5, sample_n=1,
                               window_start="20260701", window_end="20260731",
                               confidence=pc.CONFIDENCE_LOW),
            pref.PreferenceRow(dimension=pc.DIM_ROLE, value="elastic", share=0.5, sample_n=1,
                               window_start="20260701", window_end="20260731",
                               confidence=pc.CONFIDENCE_LOW),
        ]
        profile_store.save_preference("20260731", rows1, db_path=env.db_path)

        # 第二轮只剩 leader(elastic 那笔被撤/改标签,占比归零 → 压根不再产出这一行)
        rows2 = [
            pref.PreferenceRow(dimension=pc.DIM_ROLE, value="leader", share=1.0, sample_n=2,
                               window_start="20260701", window_end="20260731",
                               confidence=pc.CONFIDENCE_LOW),
        ]
        profile_store.save_preference("20260731", rows2, db_path=env.db_path)

        loaded = [r for r in profile_store.load_preference("20260731", db_path=env.db_path)
                  if r["dimension"] == pc.DIM_ROLE]
        assert {r["value"] for r in loaded} == {"leader"}, "不再产出的取值必须消失"
        assert sum(r["share"] for r in loaded) == pytest.approx(1.0), "share 合计不许 > 1"

    def test_recompute_of_one_dimension_leaves_other_dimensions_alone(self, isolated_env):
        """按 **dimension** 整段替换、不是按整期:只重算一个维度时不该顺手抹掉别的维度
        的当期结果(share 是维度内归一,一致性单位就是维度)。"""
        env = isolated_env
        base = dict(share=1.0, sample_n=1, window_start="20260701", window_end="20260731",
                    confidence=pc.CONFIDENCE_LOW)
        profile_store.save_preference("20260731", [
            pref.PreferenceRow(dimension=pc.DIM_ROLE, value="leader", **base),
            pref.PreferenceRow(dimension=pc.DIM_TIER, value="T1", **base),
        ], db_path=env.db_path)
        profile_store.save_preference("20260731", [
            pref.PreferenceRow(dimension=pc.DIM_ROLE, value="elastic", **base),
        ], db_path=env.db_path)

        loaded = profile_store.load_preference("20260731", db_path=env.db_path)
        assert {(r["dimension"], r["value"]) for r in loaded} == {
            (pc.DIM_ROLE, "elastic"), (pc.DIM_TIER, "T1")}

    def test_empty_recompute_clears_the_period(self, isolated_env):
        """本期一行都没算出来 = 这期就是空的,不留上一次的残影。"""
        env = isolated_env
        profile_store.save_preference("20260731", [
            pref.PreferenceRow(dimension=pc.DIM_ROLE, value="leader", share=1.0, sample_n=1,
                               window_start="20260701", window_end="20260731",
                               confidence=pc.CONFIDENCE_LOW),
        ], db_path=env.db_path)
        assert profile_store.save_preference("20260731", [], db_path=env.db_path) == 0
        assert profile_store.load_preference("20260731", db_path=env.db_path) == []

    def test_other_periods_are_untouched(self, isolated_env):
        """阴性方向:整段替换只作用于**本期**,旧期画像是历史留痕,不许被牵连。"""
        env = isolated_env
        base = dict(dimension=pc.DIM_ROLE, value="leader", share=1.0, sample_n=1,
                    window_start="20260601", window_end="20260630",
                    confidence=pc.CONFIDENCE_LOW)
        profile_store.save_preference("20260630", [pref.PreferenceRow(**base)], db_path=env.db_path)
        profile_store.save_preference("20260731", [], db_path=env.db_path)
        assert len(profile_store.load_preference("20260630", db_path=env.db_path)) == 1

    def test_load_unknown_period_is_empty_not_error(self, isolated_env):
        assert profile_store.load_preference("20260731", db_path=isolated_env.db_path) == []
