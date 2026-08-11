"""V2.3.2-② OUT 一等状态的机器判据(plan §五 ② 验收清单逐条)。

覆盖:
    ②-A **两类行不许合并**:OUT(股票级)与「档位已满 · 未定档」(篮子级)——
        `capacity_overflow` **不是 OUT**(K8 §八 的 OUT 适用状态里没有"位置满"),
        它不进 `out_candidates`,也因此**不进** OUT 研究影子对照的样本域;
    ②-B **早退候选(`no_active_engine`)的成员照样进表** —— 这正是建表的理由
        (`gate_evaluations` 对早退候选零成员行、`basket_dropped_handoff` 不含成员码);
        append-only + 幂等(同日重跑不产生重复行、也不覆盖既有行);
    契约:`outCandidates` 三件套只增不删,`droppedBaskets*` 三键**一个不少**。
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import List

import pytest

from neckline.report import basket_daily as bd_mod
from neckline.selection import basket_store as store
from neckline.selection import tier as ti

D0 = date(2024, 4, 8)
D0_S = "20240408"


def _member(code: str, *, name: str = "", role: str = "core") -> SimpleNamespace:
    return SimpleNamespace(ts_code=code, name=name or code, role_llm=role)


def _basket(key: str, codes) -> SimpleNamespace:
    return SimpleNamespace(basket_key=key, name=key,
                           members=tuple(_member(c) for c in codes))


def _dropped(key: str, reason: str, *, gate=None, detail=None) -> ti.DroppedBasket:
    return ti.DroppedBasket(basket_key=key, reason=reason, mech_score=0.5, name=key,
                            gate=gate, gate_detail=detail)


def _summary(engine="C", version="C1", skeleton="K8-V0.5") -> SimpleNamespace:
    return SimpleNamespace(engine_code=engine, engine_version=version,
                           skeleton_version=skeleton)


class TestOutIsNotTheSameAsCapacityOverflow:
    def test_capacity_overflow_is_not_out(self):
        """🔴 K8 §八 的 OUT 四条适用状态里**没有**"位置满" —— 溢出篮关口全过了,
        只是装不下。⛔ 它不是 OUT,也因此**不进 OUT 研究影子对照**(混进去会污染
        错杀分析:那些票压根没被判"不够格")。"""
        assert store.is_out_reason(ti.DROP_CAPACITY_OVERFLOW) is False
        for reason in (ti.DROP_POSITION_UNFIT, ti.DROP_CORE_UNFIT,
                       ti.DROP_MARKET_UNFIT, ti.DROP_SECTOR_UNFIT,
                       ti.DROP_EVIDENCE_DEGRADED_OUT,
                       "mech_gate_rejected", "no_active_engine", "engine_unresolved"):
            assert store.is_out_reason(reason) is True, reason

    def test_three_out_stocks_and_one_overflow_basket_never_cross(self, isolated_env):
        """plan ② 验收 ①:造一天含「关口未过 3 票 + 溢出 1 篮」——
        `out_candidates` 恰 3 行、`droppedBaskets` 恰 1 行,两段互不串。"""
        env = isolated_env
        baskets = {
            "k-out": _basket("k-out", ["600001.SH", "600002.SH", "600003.SH"]),
            "k-full": _basket("k-full", ["600009.SH"]),
        }
        dropped = [
            _dropped("k-out", "mech_gate_rejected", gate="sector",
                     detail="sector.industry_rank=30>10"),
            _dropped("k-full", ti.DROP_CAPACITY_OVERFLOW),
        ]
        n = store.save_out_candidates(D0, dropped, baskets,
                                      engine_by_key={"k-out": _summary()},
                                      db_path=env.db_path)
        assert n == 3
        rows = store.load_out_candidates(D0, db_path=env.db_path)
        assert [r["ts_code"] for r in rows] == ["600001.SH", "600002.SH", "600003.SH"]
        # ⛔ 溢出篮的成员一个都不许混进来
        assert all(r["basket_key"] == "k-out" for r in rows)
        assert "600009.SH" not in {r["ts_code"] for r in rows}
        assert {r["out_reason"] for r in rows} == {"mech_gate_rejected"}
        assert {r["out_gate"] for r in rows} == {"sector"}
        assert {r["engine_code"] for r in rows} == {"C"}


class TestEarlyExitCandidatesAreExactlyWhyThisTableExists:
    def test_no_active_engine_members_still_land_in_the_table(self, isolated_env):
        """🔴 plan ② 验收 ②(**建表的理由**):`no_active_engine` / `engine_unresolved`
        两种**早退候选零成员行**(它们压根没跑到关口层),`gate_evaluations` 取不到;
        `basket_dropped_handoff` 又**不含成员代码** —— 两条现有路径都取不全 OUT 票。"""
        env = isolated_env
        baskets = {"k-noengine": _basket("k-noengine", ["600001.SH", "600002.SH"])}
        dropped = [_dropped("k-noengine", "no_active_engine",
                            detail="无运行中的引擎线(C/Z/Y 均未激活)")]
        # ⚠ 刻意**不传** engine_by_key:早退候选连引擎都没解析出来,三件套本来就是空的
        n = store.save_out_candidates(D0, dropped, baskets, db_path=env.db_path)
        assert n == 2
        rows = store.load_out_candidates(D0, db_path=env.db_path)
        assert [r["ts_code"] for r in rows] == ["600001.SH", "600002.SH"]
        assert {r["engine_code"] for r in rows} == {None}      # 如实空,⛔ 不编一个
        assert {r["out_gate"] for r in rows} == {None}         # 非关口原因

    def test_rerun_is_idempotent_and_never_overwrites(self, isolated_env):
        """append-only + `UNIQUE(d0_date, basket_key, ts_code)`:同日重跑不产生重复行,
        也**不覆盖**既有行(⛔ 零 UPDATE / 零 DELETE / 零 INSERT OR REPLACE ——
        「上一次怎么判的」本身是审计对象)。"""
        env = isolated_env
        baskets = {"k1": _basket("k1", ["600001.SH"])}
        d1 = [_dropped("k1", "mech_gate_rejected", gate="market", detail="第一次的理由")]
        assert store.save_out_candidates(D0, d1, baskets, db_path=env.db_path) == 1
        d2 = [_dropped("k1", "position_unfit", gate="position", detail="第二次的理由")]
        assert store.save_out_candidates(D0, d2, baskets, db_path=env.db_path) == 0
        rows = store.load_out_candidates(D0, db_path=env.db_path)
        assert len(rows) == 1
        assert rows[0]["out_detail"] == "第一次的理由"        # ⛔ 没被覆盖

    def test_same_stock_in_two_out_baskets_gets_two_rows(self, isolated_env):
        """篮子间成员可重叠 → 同一票在同一 D0 可能出现在多个 OUT 篮。
        本表主键含 `basket_key`,故**两行**(「这票在哪个篮里为什么出局」是两条事实)。
        ⚠ D1 读数那张表(`out_shadow_daily`)的主键刻意**不含** `basket_key` ——
        那是这只票的属性,存两份就是两个事实源。⛔ 两张表别套同一个主键直觉。"""
        env = isolated_env
        baskets = {"kA": _basket("kA", ["600001.SH"]), "kB": _basket("kB", ["600001.SH"])}
        dropped = [_dropped("kA", "mech_gate_rejected", gate="market"),
                   _dropped("kB", ti.DROP_SECTOR_UNFIT, gate="sector")]
        assert store.save_out_candidates(D0, dropped, baskets, db_path=env.db_path) == 2
        rows = store.load_out_candidates(D0, db_path=env.db_path)
        assert [r["basket_key"] for r in rows] == ["kA", "kB"]

    def test_post_gate_result_would_lose_every_out_stock(self, isolated_env, caplog):
        """反向守门:传**对拍后**的候选集(被关口除名的篮子已被摘除)→ 一票都记不下来,
        且必须留一条 WARNING。这条测试的存在本身就是提醒:调用方必须传**对拍前**那批。"""
        env = isolated_env
        dropped = [_dropped("k-out", "mech_gate_rejected", gate="sector")]
        with caplog.at_level("WARNING"):
            n = store.save_out_candidates(D0, dropped, {}, db_path=env.db_path)
        assert n == 0
        assert any("对拍前" in r.message or "查无此篮" in r.message for r in caplog.records)


class TestContractOnlyGrows:
    def test_dropped_keys_all_survive_and_out_keys_are_added(self):
        """契约**只增不删**:`droppedBaskets*` 三键一个不少(老客户端靠它们渲染 ③b),
        `outCandidates*` 三件套是新增。"""
        daily = bd_mod.BasketDaily(trade_date=D0)
        pub = daily.to_public_dict()
        for k in ("droppedBaskets", "droppedBasketsAvailable",
                  "droppedBasketsUnavailableReason"):
            assert k in pub, k
        for k in ("outCandidates", "outCandidatesAvailable",
                  "outCandidatesUnavailableReason"):
            assert k in pub, k

    def test_legacy_snapshot_says_not_taken_rather_than_none_today(self):
        """老快照读回:如实标「该版本没有 OUT 清单」,⛔ 不冒充「那天没有 OUT」。"""
        pub = bd_mod.basket_daily_from_snapshot(None)
        assert pub["outCandidates"] == [] and pub["outCandidatesAvailable"] is False
        assert pub["outCandidatesUnavailableReason"]

    def test_out_view_reuses_the_same_reason_code_vocabulary(self):
        """`outReason` 与 `droppedBaskets.reason` **共用同一套原因码词表**,
        ⛔ 不另起第二套(④ 按码归因要对得上)。"""
        v = bd_mod.OutCandidateView(ts_code="600001.SH", out_reason="market_unfit")
        assert v.reason_label == bd_mod.DROPPED_REASON_LABEL["market_unfit"]
        # 未识别码原样透传,⛔ 不静默瞎翻译
        assert bd_mod.OutCandidateView(ts_code="x", out_reason="zzz").reason_label == "zzz"

    def test_out_candidate_out_schema_has_the_four_k8_fields(self):
        """K8 §十-11 四项:股票 / 主引擎+版本 / 出局关口 / 理由。"""
        from neckline.api.schemas import OutCandidateOut

        fields = set(OutCandidateOut.model_fields)
        assert {"tsCode", "engineCode", "engineVersion", "outGate", "outReason"} <= fields
