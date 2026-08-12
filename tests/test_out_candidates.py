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

    def test_basket_key_is_shipped_so_the_same_stock_in_two_baskets_is_distinguishable(self):
        """🔴 **复审 🟡-7**:同一只票可能在同一天的**多个** OUT 篮里出局
        (`out_candidates` 主键就含 `basket_key`)。不下发这个键 → 客户端
        `Identifiable.id`(`tsCode|outReason|outGate`)**撞主键**、Markdown 出两行
        一模一样的记录。契约**只增**这一个键,老客户端不受影响。"""
        from neckline.api.schemas import OutCandidateOut

        assert "basketKey" in OutCandidateOut.model_fields
        pub = bd_mod.OutCandidateView(ts_code="600001.SH", out_reason="market_unfit",
                                      basket_key="kA").to_public_dict()
        assert pub["basketKey"] == "kA"
        # 客户端那一侧:`id` 必须把 basketKey 算进去(⛔ 别把它拿掉)
        from tests.client_sources import models_text
        models = models_text()   # V2.4.0 P3.7:DTO 已拆六份,统一入口读
        block = models.split("struct OutCandidate:")[1].split("\nstruct ")[0]
        assert "case basketKey" in block
        assert "basketKey = try c.decodeIfPresent(String.self, forKey: .basketKey)" in block
        id_line = next(ln for ln in block.splitlines() if "var id: String" in ln)
        assert "basketKey" in id_line, id_line

    def test_out_candidates_are_exactly_what_dropped_baskets_no_longer_carries(self):
        """🔴 **plan §五 ②-B 验收 ①ff**:两段的成员集合由**同一个判据**互补切分 ——
        `is_out_reason()` 真 → ③b-2(股票级 OUT);假 → ③b(档位已满 · 未定档)。
        ⛔ 不许有第三种归属,也⛔ 不许有码两边都进(那就是双列)。"""
        codes = [ti.DROP_CAPACITY_OVERFLOW, ti.DROP_POSITION_UNFIT, ti.DROP_CORE_UNFIT,
                 ti.DROP_MARKET_UNFIT, ti.DROP_SECTOR_UNFIT, ti.DROP_EVIDENCE_DEGRADED_OUT,
                 ti.DROP_BELOW_QUALITY_LINE,
                 "mech_gate_rejected", "no_active_engine", "engine_unresolved"]
        out_side = {c for c in codes if store.is_out_reason(c)}
        dropped_side = {c for c in codes if not store.is_out_reason(c)}
        assert out_side & dropped_side == set()
        assert out_side | dropped_side == set(codes)
        assert dropped_side == {ti.DROP_CAPACITY_OVERFLOW}


def test_out_candidate_row_stays_402pt_safe():
    """🔴 **③b-2 那一行在 iPhone 402pt 上的版式契约,钉成机器判据。**

    **为什么要这条**:V2.3.2 ⑥ 出图时,③b-2 整节在 iPhone 视口里落在**第二屏**
    (它前面有 行情状态卡 + ① 情绪卡 + ④ 昨日复盘卡 + ③ 的 T1/T2 两个空态),
    砍了三轮演示数据仍顶不上首屏 → **本版没有 ③b-2 的 iPhone 实拍**(macOS 那张是全的)。
    ⚠ 用户 2026-08-11 裁定:**⛔ 不许用 iPad 模拟器**(本项目只有 macOS 与 iOS 两个平台),
    装不下就如实认缺口 + 靠单测兜底 —— **这条就是那个兜底**。

    **它保护的是什么**(CLAUDE.md 402pt 那条坑的具体形态):首行是「票名(代码)」+
    右对齐「出局结论」的横向密集行。**名称必须截断、徽标必须永不换行** ——
    少了 `lineLimit(1)` 名称会撑成两行;少了 `fixedSize()` 中文徽标会被压成竖排单字
    (「位 置 关 判 定 不 合 适」)。两种回归**编译不报错、跑不出来、只有实拍看得见**。
    """
    src = (Path(__file__).resolve().parent.parent
           / "client" / "Neckline" / "Views" / "BasketDailyView.swift"
           ).read_text(encoding="utf-8")
    block = src.split("private struct OutCandidateRow: View {", 1)[1].split("\n}\n", 1)[0]

    # ① 首行两件:名称截断 + 结论不换行不压缩
    assert ".lineLimit(1).truncationMode(.tail)" in block, "票名少了截断 → 会撑成两行"
    assert ".lineLimit(1).fixedSize()" in block, "出局结论少了 fixedSize → 中文会被压成竖排单字"
    # ② 结构必须是「首行只放两件、其余收进次行」(⛔ 别把 role/引擎/关口搬回首行)
    head = block.split("if let sub = subline", 1)[0]
    for banned in ("item.role", "item.engineLabel", "item.gateLabel"):
        assert banned not in head, f"{banned} 被搬回首行了 —— 402pt 上会把这一行挤爆"
    assert "subline" in block and "nkGateEnforcementNote" in block
