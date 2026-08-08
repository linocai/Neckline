"""V2-⑨-A / ⑨-B 盘后复盘引擎(plan §五 V2-⑨ 验收逐条)。

覆盖:机械判九项各自的正常路径与「算不出」路径;**缺存拍走 EOD 近似且如实标注**
(单测两路,⑨ 验收第二条);龙头从**冻结卡**认(不拿当日涨幅最大者当龙头);
可买性与 `fwd_buyable` 同口径 + 一字单列;当日横截面名次的**确定性 tie-break**;
`basket_review_daily` 每日一行幂等 + **没有 UPDATE 路径**;LLM 降级次序
(V2.1-② 起只剩 T2 细节一项、T1 永不在可丢清单里)+ 缺席时机械判照出;
**历史 `depth='brief'` 行读回渲染**(T3 退役后 `brief` 无写入方,但老行照常读)。
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from tests.conftest import business_days, insert_trade_cal, write_daily_fixture

from neckline.db import connection
from neckline.llm.budget import (
    DROP_T2_REVIEW_DETAIL, LEDGER_REVIEW, NEVER_DROPPED, BudgetLedger,
)
from neckline.review import basket_review as br
from neckline.review import basket_review_store as store
from neckline.selection import basket_card as bc
from neckline.selection import verification_rules as vr
from neckline.selection.basket_store import BasketRef, save_basket_card
from neckline.sentinel import basket_verify as bv
from neckline.sentinel import basket_verify_store as bvs

pytestmark = pytest.mark.usefixtures("isolated_env")

_REPO = Path(__file__).resolve().parent.parent

D0 = date(2026, 7, 23)
D1 = date(2026, 7, 24)


# ══════════════════════════════════════════════════════════════════════════
# 夹具
# ══════════════════════════════════════════════════════════════════════════

def _bar(code, *, open_=10.0, high=10.5, low=9.8, close=10.2, pre_close=10.0):
    return {"ts_code": code, "open": open_, "high": high, "low": low, "close": close,
            "pre_close": pre_close, "change": close - pre_close,
            "pct_chg": (close / pre_close - 1.0) * 100.0, "vol": 10000.0, "amount": 1.0e7}


def _day(bars=None, limits=None, index_ret=None, ticks=None, auction=None,
         capture=None) -> br.DayMarket:
    dm = br.DayMarket(review_date=D1, d0=D0)
    dm.bars = {b["ts_code"]: b for b in (bars or [])}
    dm.limits = {r["ts_code"]: r for r in (limits or [])}
    dm.index_code = "000001.SH"
    dm.index_ret = index_ret
    dm.ticks = ticks or {}
    dm.auction = auction or {}
    dm.capture_status = capture or {"capture_status": "missing", "recorded": False}
    return dm


def _mech(code, *, close=10.0, ma20=9.2, limit_up=11.0, limit_down=9.0, stop_price=9.5):
    return bc.MemberMech(ts_code=code, name=code, close=close, ma20=ma20,
                         limit_up=limit_up, limit_down=limit_down, stop_price=stop_price)


def _card(codes, *, roles=None, rs_ranks=None, max_chase=None, scripts=None, mechs=None):
    """一张够 ⑨ 读的最小卡(形状与 ⑦ 真卡一致的那几个键)。"""
    ms = mechs or [_mech(c) for c in codes]
    members = []
    for i, c in enumerate(codes):
        m = ms[i].to_dict()
        m.pop("ts_code", None)
        m.pop("name", None)
        members.append({
            "ts_code": c, "name": c, "role_mech": (roles or {}).get(c),
            "role_llm": "core", "is_primary": 1 if i == 0 else 0,
            "rs_rank": (rs_ranks or {}).get(c),
            "max_chase": (max_chase or {}).get(c),
            # ⚠ **机械数据在 `mech` 子对象里**,不在成员顶层 —— 这是 ⑦ 真卡的形状
            # (`MemberCardEntry.to_dict()`)。夹具刻意照抄真形状:施工期按顶层读过
            # 一次,单测因为夹具"顺手也放了一份在顶层"而全绿,真卡上却一律取到 None
            # (端到端冒烟才照出来)。
            "mech": m,
        })
    return {
        "spec_version": bc.CARD_SPEC_VERSION, "version": 1,
        "driver": "某共同驱动", "driver_kind": "theme", "why_now": "因为今天",
        "members": members,
        "scripts": scripts if scripts is not None else {
            "strong": "强开怎么看", "flat": "平开怎么看", "weak": "弱开怎么看"},
        "tier": 1, "rank_in_tier": 1, "rank_mech": 1, "mech_score": 0.72,
        "tier_breakdown": {"dims": {"sector": 0.8, "fresh": 0.7}},
        "verification_spec": bc.build_verification_spec("bk", D0, ms),
        "invalidation_spec": bc.build_invalidation_spec("bk", D0, ms, stop_pct=0.05),
        "fingerprint": {"stop_pct": 0.05, "pack_version": "K4-pack-v1",
                        "charter_version": "v1.3.3", "engine_api_version": 1,
                        "verification_ruleset_version": vr.VERIFICATION_RULESET_VERSION},
    }


def _ref(codes, *, basket_id=1, tier=1, key="k1", name="测试篮") -> BasketRef:
    return BasketRef(basket_id=basket_id, trade_date=D0.strftime("%Y%m%d"), basket_key=key,
                     name=name, tier=tier, member_codes=tuple(codes))


def _seed_basket(env, codes, *, tier=1, key="k1", name="测试篮", card=None,
                 pack_version="K4-pack-v1") -> int:
    with connection(env.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (D0.strftime("%Y%m%d"), key, name, "某共同驱动", "theme", tier,
             pack_version, 1, "v1.3.3", "auto", "ok", "2026-08-02T00:00:00+08:00"),
        )
        bid = int(cur.lastrowid)
        for i, code in enumerate(codes):
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, code, "core", "leader" if i == 0 else "follower", 0, "理由",
                 1 if i == 0 else 0, "2026-08-02T00:00:00+08:00"),
            )
        conn.execute(
            "INSERT INTO tier_history (trade_date, basket_id, tier, mech_score,"
            " mech_breakdown_json, rank_in_tier, rank_mech, llm_rank_delta, llm_reason,"
            " pack_version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (D0.strftime("%Y%m%d"), bid, tier, 0.72, '{"dims": {"sector": 0.8}}', 1, 1, 0,
             None, pack_version, "2026-08-02T00:00:00+08:00"),
        )
    save_basket_card(bid, card if card is not None else _card(codes), db_path=env.db_path)
    return bid


# ══════════════════════════════════════════════════════════════════════════
# ① 竞价 vs 剧本
# ══════════════════════════════════════════════════════════════════════════

class TestAuctionVsScript:
    def test_branch_thresholds(self):
        assert br.script_branch(0.03) == "strong"
        assert br.script_branch(0.0) == "flat"
        assert br.script_branch(-0.05) == "weak"
        assert br.script_branch(br.AUCTION_STRONG_GAP) == "strong"     # 边界含等号
        assert br.script_branch(br.AUCTION_WEAK_GAP) == "weak"
        assert br.script_branch(None) is None

    def test_prefers_auction_snapshot_over_daily_open(self):
        day = _day(bars=[_bar("A.SZ", open_=10.0, pre_close=10.0)],
                   auction={"A.SZ": {"ts_code": "A.SZ", "gap_pct": 0.05}})
        out = br.judge_auction_vs_script(["A.SZ"], _card(["A.SZ"]), day)
        assert out["source"] == "auction_snapshot"
        assert out["gap_median"] == pytest.approx(0.05)
        assert out["branch"] == "strong" and out["script_text"] == "强开怎么看"

    def test_falls_back_to_daily_open_and_says_so(self):
        day = _day(bars=[_bar("A.SZ", open_=10.5, pre_close=10.0)])
        out = br.judge_auction_vs_script(["A.SZ"], _card(["A.SZ"]), day)
        assert out["source"] == "daily_open"
        assert out["gap_median"] == pytest.approx(0.05)

    def test_no_data_is_unavailable_not_zero(self):
        out = br.judge_auction_vs_script(["A.SZ"], _card(["A.SZ"]), _day())
        assert out["available"] is False and out["gap_median"] is None
        assert out["unavailable_reason"]

    def test_missing_script_branch_is_reported(self):
        day = _day(bars=[_bar("A.SZ", open_=10.5, pre_close=10.0)])
        out = br.judge_auction_vs_script(["A.SZ"], _card(["A.SZ"], scripts={"flat": "只有平开"}), day)
        assert out["branch"] == "strong" and out["script_present"] is False
        assert out["scripts_branches_on_card"] == ["flat"]


# ══════════════════════════════════════════════════════════════════════════
# ② 开盘首方向 / ③ MFE·MAE
# ══════════════════════════════════════════════════════════════════════════

class TestOpenDirectionAndMfe:
    def test_gap_and_intraday_direction(self):
        day = _day(bars=[_bar("A.SZ", open_=10.5, close=10.1, pre_close=10.0)])
        out = br.judge_open_direction(["A.SZ"], day)
        assert out["gap_dir"] == "up" and out["intraday_dir"] == "down"
        assert out["aligned"] is False
        assert out["per_member"]["A.SZ"]["first_tick_dir"] is None      # 无存拍就是 None

    def test_first_tick_direction_needs_capture(self):
        day = _day(bars=[_bar("A.SZ", open_=10.0, close=10.4, pre_close=10.0)],
                   ticks={"A.SZ": [("09:31:00", 10.2), ("14:59:00", 10.4)]})
        out = br.judge_open_direction(["A.SZ"], day)
        assert out["per_member"]["A.SZ"]["first_tick_dir"] == "up"
        assert out["has_intraday_capture"] is True

    def test_mfe_from_intraday_capture_carries_timestamps(self):
        day = _day(bars=[_bar("A.SZ", high=11.0, low=9.5, pre_close=10.0)],
                   ticks={"A.SZ": [("09:31:00", 10.3), ("10:15:00", 10.8), ("14:00:00", 9.7)]},
                   capture={"capture_status": "full", "recorded": True,
                            "covered_minutes": 236, "expected_minutes": 240, "empty_ticks": 0})
        out = br.judge_mfe_mae(["A.SZ"], day)
        assert out["mfe_source"] == br.MFE_SOURCE_INTRADAY
        assert out["mfe_median"] == pytest.approx(0.08)
        assert out["mae_median"] == pytest.approx(-0.03)
        assert out["per_member"]["A.SZ"]["mfe_at"] == "10:15:00"
        assert out["capture_status"] == "full" and out["note"] is None

    def test_mfe_falls_back_to_eod_and_is_labelled(self):
        """⑨ 验收第二条:缺存拍 → EOD 近似 + **如实标注**(不装精确、不编时刻)。"""
        day = _day(bars=[_bar("A.SZ", high=11.0, low=9.5, pre_close=10.0)],
                   capture={"capture_status": "missing", "recorded": False})
        out = br.judge_mfe_mae(["A.SZ"], day)
        assert out["mfe_source"] == br.MFE_SOURCE_EOD_APPROX
        assert out["mfe_median"] == pytest.approx(0.10)
        assert out["mae_median"] == pytest.approx(-0.05)
        assert out["per_member"]["A.SZ"]["mfe_at"] is None       # ⛔ 不编时刻
        assert "时刻未知" in (out["note"] or "")
        assert out["capture_recorded"] is False                  # 「没记过」≠ full

    def test_mixed_source_is_reported_as_mixed(self):
        day = _day(bars=[_bar("A.SZ", high=11.0, low=9.5, pre_close=10.0),
                         _bar("B.SZ", high=10.6, low=9.9, pre_close=10.0)],
                   ticks={"A.SZ": [("09:31:00", 10.5)]})
        out = br.judge_mfe_mae(["A.SZ", "B.SZ"], day)
        assert out["mfe_source"] == "mixed"


# ══════════════════════════════════════════════════════════════════════════
# ④ 成员同向率 / ⑤ 龙头带动
# ══════════════════════════════════════════════════════════════════════════

class TestAlignmentAndLeader:
    def test_alignment_counts_and_missing_are_separate(self):
        day = _day(bars=[_bar("A.SZ", close=10.5, pre_close=10.0),
                         _bar("B.SZ", close=10.3, pre_close=10.0)])
        out = br.judge_member_alignment(["A.SZ", "B.SZ", "C.SZ"], day)
        assert out["member_count"] == 3 and out["observed"] == 2
        assert out["up"] == 2 and out["alignment"] == pytest.approx(1.0)
        assert out["missing"] == ["C.SZ"]         # 没数据的不当成"平"

    def test_leader_comes_from_frozen_card_not_todays_best(self):
        """龙头必须是 D0 卡上认的那只 —— 拿当日涨幅最大者当龙头是拿结果当原因。"""
        card = _card(["A.SZ", "B.SZ"], roles={"A.SZ": "leader", "B.SZ": "follower"})
        day = _day(bars=[_bar("A.SZ", close=9.5, pre_close=10.0),      # 龙头今天跌
                         _bar("B.SZ", close=11.0, pre_close=10.0)])    # 跟风今天大涨
        assert br.resolve_leaders(["A.SZ", "B.SZ"], card) == ["A.SZ"]
        out = br.judge_leader_pull(["A.SZ", "B.SZ"], card, day)
        assert out["leaders"] == ["A.SZ"] and out["led"] is False
        assert out["spread"] == pytest.approx(-0.15)

    def test_leader_falls_back_to_rs_rank_then_primary(self):
        by_rank = _card(["A.SZ", "B.SZ"], rs_ranks={"A.SZ": 2, "B.SZ": 1})
        assert br.resolve_leaders(["A.SZ", "B.SZ"], by_rank) == ["B.SZ"]
        by_primary = _card(["A.SZ", "B.SZ"])       # 只有 is_primary
        assert br.resolve_leaders(["A.SZ", "B.SZ"], by_primary) == ["A.SZ"]
        assert br.resolve_leaders(["A.SZ"], None) == []


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 可买性
# ══════════════════════════════════════════════════════════════════════════

class TestBuyability:
    def test_one_word_board_is_flagged_separately_from_limit_up(self):
        card = _card(["A.SZ", "B.SZ"])       # 卡上冻结 limit_up=11.0
        day = _day(bars=[_bar("A.SZ", open_=11.0, high=11.0, low=11.0, close=11.0, pre_close=10.0),
                         _bar("B.SZ", open_=10.2, high=11.0, low=10.1, close=11.0, pre_close=10.0)])
        out = br.judge_buyability(["A.SZ", "B.SZ"], card, day)
        assert out["per_member"]["A.SZ"]["reason"] == br.BUY_ONE_WORD
        assert out["per_member"]["B.SZ"]["reason"] == br.BUY_LIMIT_UP_CLOSE
        assert out["buyable"] == 0 and out["one_word"] == 1 and out["limit_up"] == 1

    def test_uses_frozen_limit_price_from_card(self):
        card = _card(["A.SZ"])
        day = _day(bars=[_bar("A.SZ", open_=10.4, high=10.6, low=10.3, close=10.5, pre_close=10.0)])
        out = br.judge_buyability(["A.SZ"], card, day)
        assert out["per_member"]["A.SZ"]["limit_up_source"] == "card_frozen"
        assert out["per_member"]["A.SZ"]["buyable"] is True

    def test_reads_limit_price_from_nested_mech_not_top_level(self):
        """卡上的涨停价住在 `members[].mech.limit_up`。**顶层保留为老形状兜底。**"""
        card = _card(["A.SZ"])
        assert "limit_up" not in card["members"][0]                 # 真卡形状:顶层没有
        assert card["members"][0]["mech"]["limit_up"] == 11.0
        day = _day(bars=[_bar("A.SZ", open_=11.0, high=11.0, low=11.0, close=11.0, pre_close=10.0)])
        out = br.judge_buyability(["A.SZ"], card, day)
        assert out["per_member"]["A.SZ"]["limit_up"] == 11.0
        assert out["per_member"]["A.SZ"]["limit_up_source"] == "card_frozen"
        # 老形状(字段在成员顶层)也要读得出来
        legacy = {"members": [{"ts_code": "A.SZ", "limit_up": 11.0}]}
        out2 = br.judge_buyability(["A.SZ"], legacy, day)
        assert out2["per_member"]["A.SZ"]["limit_up_source"] == "card_frozen"

    def test_falls_back_to_limit_derived_when_card_has_no_price(self):
        card = _card(["A.SZ"], mechs=[_mech("A.SZ", limit_up=None)])
        day = _day(bars=[_bar("A.SZ", open_=11.0, high=11.0, low=11.0, close=11.0, pre_close=10.0)],
                   limits=[{"ts_code": "A.SZ", "limit_up_price": 11.0, "is_limit_up": True}])
        out = br.judge_buyability(["A.SZ"], card, day)
        assert out["per_member"]["A.SZ"]["limit_up_source"] == "limit_derived"
        assert out["per_member"]["A.SZ"]["reason"] == br.BUY_ONE_WORD

    def test_no_bar_is_not_buyable_and_counted_apart(self):
        out = br.judge_buyability(["A.SZ"], _card(["A.SZ"]), _day())
        assert out["per_member"]["A.SZ"]["reason"] == br.BUY_NO_BAR
        assert out["no_bar"] == 1 and out["available"] is False


# ══════════════════════════════════════════════════════════════════════════
# ⑦ 验证时点 / ⑧ 收盘 RS
# ══════════════════════════════════════════════════════════════════════════

class TestVerificationTimingAndRs:
    def test_reads_verification_trail(self, isolated_env):
        env = isolated_env
        bid = _seed_basket(env, ["A.SZ"])
        v1 = bv.BasketVerdict(state=vr.STATE_PARTIAL)
        v2 = bv.BasketVerdict(state=vr.STATE_VERIFIED)
        bvs.append_if_changed(bid, D1, v1, observed_at="2026-07-24T10:00:00+08:00",
                              db_path=env.db_path)
        bvs.append_if_changed(bid, D1, v2, observed_at="2026-07-24T13:00:00+08:00",
                              db_path=env.db_path)
        bvs.append_row(bid, D1, v2, observed_at="2026-07-24T15:05:00+08:00", db_path=env.db_path)
        out = br.judge_verification_timing(bid, D1, db_path=env.db_path)
        assert out["state"] == vr.STATE_VERIFIED and out["has_eod_verdict"] is True
        assert out["first_partial_at"] == "2026-07-24T10:00:00+08:00"
        assert out["first_verified_at"] == "2026-07-24T13:00:00+08:00"
        assert out["first_falsified_at"] is None
        assert out["rows"] == 3 and out["provisional"] is False

    def test_never_evaluated_is_distinguishable(self, isolated_env):
        env = isolated_env
        bid = _seed_basket(env, ["A.SZ"])
        out = br.judge_verification_timing(bid, D1, db_path=env.db_path)
        assert out["available"] is False and out["not_evaluated"] is True
        assert out["state"] == vr.STATE_UNCLEAR       # 「还没判」不是「判了是 unclear」

    def test_close_rs_needs_index(self):
        day = _day(bars=[_bar("A.SZ", close=10.5, pre_close=10.0)], index_ret=0.01)
        out = br.judge_close_rs(["A.SZ"], day)
        assert out["excess_median"] == pytest.approx(0.04)
        assert out["rs_positive"] is True and out["outperformers"] == 1
        no_idx = br.judge_close_rs(["A.SZ"], _day(bars=[_bar("A.SZ")]))
        assert no_idx["available"] is False and no_idx["rs_positive"] is None


# ══════════════════════════════════════════════════════════════════════════
# ⑨ Tier vs 结果 + 当日横截面名次的确定性
# ══════════════════════════════════════════════════════════════════════════

class TestTierVsOutcome:
    def test_day_rank_table_is_deterministic_on_ties(self):
        """收益完全相同的两个篮子,名次由 `basket_key` 升序打破 —— 不受入参顺序影响。"""
        a = [("kb", 1, 1, 0.03), ("ka", 1, 2, 0.03), ("kc", 2, 1, 0.01)]
        t1 = br.day_rank_table(a)
        t2 = br.day_rank_table(list(reversed(a)))
        assert t1 == t2
        assert t1["ka"]["rank_by_outcome"] == 1 and t1["kb"]["rank_by_outcome"] == 2

    def test_unscored_basket_gets_no_fabricated_rank(self):
        t = br.day_rank_table([("ka", 1, 1, 0.02), ("kb", 1, 2, None)])
        assert t["kb"]["rank_by_outcome"] is None and t["kb"]["rank_gap"] is None
        assert t["kb"]["total"] == 2

    def test_attaches_breakdown_and_marks_single_day_as_non_conclusive(self):
        day = _day(bars=[_bar("A.SZ", close=10.5, pre_close=10.0)])
        out = br.judge_tier_vs_outcome(_ref(["A.SZ"]), _card(["A.SZ"]), day,
                                       day_rank={"total": 3, "rank_by_tier": 1,
                                                 "rank_by_outcome": 3, "rank_gap": 2})
        assert out["tier_breakdown"] == {"dims": {"sector": 0.8, "fresh": 0.7}}
        assert out["basket_ret_median"] == pytest.approx(0.05)
        assert "单日" in out["rank_note"]


# ══════════════════════════════════════════════════════════════════════════
# 九项装配 + 保险丝
# ══════════════════════════════════════════════════════════════════════════

class TestBuildMech:
    def test_all_nine_items_present_with_strata_keys(self, isolated_env):
        env = isolated_env
        day = _day(bars=[_bar("A.SZ", close=10.5, pre_close=10.0)], index_ret=0.01)
        mech = br.build_mech(_ref(["A.SZ"]), _card(["A.SZ"]), day, db_path=env.db_path)
        for key in br.MECH_ITEM_KEYS:
            assert key in mech, f"机械判缺项:{key}"
        assert mech["spec_version"] == br.MECH_SPEC_VERSION
        assert mech["meta"]["pack_version"] == "K4-pack-v1"
        assert mech["meta"]["verification_ruleset_version"] == vr.VERIFICATION_RULESET_VERSION

    def test_one_broken_item_does_not_sink_the_other_eight(self, isolated_env, monkeypatch):
        env = isolated_env
        monkeypatch.setattr(br, "judge_close_rs",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        day = _day(bars=[_bar("A.SZ", close=10.5, pre_close=10.0)], index_ret=0.01)
        mech = br.build_mech(_ref(["A.SZ"]), _card(["A.SZ"]), day, db_path=env.db_path)
        assert mech["close_rs"]["available"] is False and "RuntimeError" in mech["close_rs"]["error"]
        assert mech["member_alignment"]["available"] is True

    def test_no_card_still_produces_mech(self, isolated_env):
        env = isolated_env
        day = _day(bars=[_bar("A.SZ", close=10.5, pre_close=10.0)], index_ret=0.0)
        mech = br.build_mech(_ref(["A.SZ"]), None, day, db_path=env.db_path)
        assert mech["meta"]["has_card"] is False
        assert mech["meta"]["pack_version"] is None       # 分层键缺就是缺,不编
        assert mech["member_alignment"]["available"] is True


# ══════════════════════════════════════════════════════════════════════════
# 落库:每日一行幂等 + 没有 UPDATE 路径
# ══════════════════════════════════════════════════════════════════════════

class TestStore:
    def _review(self, mech=None, **kw):
        return br.BasketReview(
            basket_id=kw.get("basket_id", 1), basket_key="k1", name="测试篮", tier=1,
            review_date=D1, d0=D0, depth=kw.get("depth", br.DEPTH_FULL),
            mech=mech or {"meta": {"pack_version": "K4-pack-v1",
                                   "verification_ruleset_version": "verify_ruleset_v1"}},
            llm_text=kw.get("llm_text"), llm_skip_reason=kw.get("llm_skip_reason"),
            degraded=kw.get("degraded", False),
        )

    def test_insert_is_idempotent_and_conflicts_are_surfaced(self, isolated_env):
        env = isolated_env
        first = store.save_review(self._review(), db_path=env.db_path)
        assert first == {"inserted": 1, "existing": 0, "conflicts": []}
        again = store.save_review(self._review(), db_path=env.db_path)
        assert again["inserted"] == 0 and again["existing"] == 1 and not again["conflicts"]
        changed = store.save_review(self._review(mech={"meta": {}, "x": 1}), db_path=env.db_path)
        assert changed["existing"] == 1 and changed["conflicts"], "不一致必须如实带出,不静默"

    def test_round_trip_exposes_strata_keys(self, isolated_env):
        env = isolated_env
        store.save_review(self._review(llm_text="今天这篮…"), db_path=env.db_path)
        row = store.load_review(1, D1, db_path=env.db_path)
        assert row.pack_version == "K4-pack-v1"
        assert row.ruleset_version == "verify_ruleset_v1"
        assert row.llm_text == "今天这篮…" and row.degraded == 0

    def test_list_supports_date_to_cutoff(self, isolated_env):
        env = isolated_env
        store.save_review(self._review(), db_path=env.db_path)
        assert store.list_reviews(date_to="20260723", db_path=env.db_path) == []
        assert len(store.list_reviews(date_to="20260724", db_path=env.db_path)) == 1
        assert store.review_dates(db_path=env.db_path) == ["20260724"]

    def test_store_has_no_update_or_delete_path(self):
        """靠"没有那条路径"担保,不靠自觉(同 `basket_verify_store` 的守门体例)。"""
        src = (_REPO / "neckline" / "review" / "basket_review_store.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        sql = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        for text in sql:
            upper = text.upper()
            assert "UPDATE BASKET_REVIEW_DAILY" not in upper
            assert "DELETE FROM BASKET_REVIEW_DAILY" not in upper


# ══════════════════════════════════════════════════════════════════════════
# ⑨-B LLM 降级次序
# ══════════════════════════════════════════════════════════════════════════

class _StubProvider:
    def __init__(self, text="这是复盘叙述。", ok=True):
        self.text, self.ok, self.calls = text, ok, 0

    def chat(self, messages, **kw):
        self.calls += 1
        assert kw.get("enable_search") is False, "复盘链路不该联网"

        class R:
            pass
        r = R()
        r.ok, r.content, r.reason = self.ok, self.text, "stub"
        return r


class TestLlmDegradeOrder:
    def test_drop_order_is_only_t2_and_never_t1(self):
        """V2.1-②:可丢清单由「T3 简评 → T2 细节」收窄为**只有 T2 细节**。"""
        from neckline.llm.budget import DEGRADE_ORDER

        empty = BudgetLedger(limits={LEDGER_REVIEW: 0.0, "search": 1.0, "reason": 1.0})
        assert br.plan_llm_drops([], empty) == [DROP_T2_REVIEW_DETAIL]
        assert br.plan_llm_drops([], BudgetLedger()) == []      # 预算够就一个都不丢
        # 可丢清单**只有这一项** —— T1 复盘、卡冻结、纪律外壳一律不在其中
        assert DEGRADE_ORDER == (DROP_T2_REVIEW_DETAIL,)
        assert all(item not in DEGRADE_ORDER for item in NEVER_DROPPED)
        assert not any("t1" in item for item in DEGRADE_ORDER)
        assert not any("t3" in item for item in DEGRADE_ORDER)

    def test_budget_exhausted_drops_t2_detail_and_says_so(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        _seed_basket(env, ["A.SZ"], tier=1, key="ka", name="T1篮")
        _seed_basket(env, ["B.SZ"], tier=2, key="kb", name="T2篮")
        write_daily_fixture(env, "daily", D1, [_bar("A.SZ", close=10.5, pre_close=10.0),
                                               _bar("B.SZ", close=9.8, pre_close=10.0)])
        provider = _StubProvider()
        ledger = BudgetLedger(limits={LEDGER_REVIEW: 0.0, "search": 60.0, "reason": 60.0})
        res = br.review_day(D1, d0=D0, db_path=env.db_path, parquet_dir=env.parquet_dir,
                            use_llm=True, provider=provider, ledger=ledger)
        assert res.llm_dropped == [DROP_T2_REVIEW_DETAIL]
        t2 = next(r for r in res.reviews if r.tier == 2)
        assert t2.depth == br.DEPTH_FULL
        assert t2.llm_skip_reason == f"{br.LLM_DROPPED}:{DROP_T2_REVIEW_DETAIL}" and t2.degraded is True
        # T1 不在可丢清单里 —— 它照样试着调,只是预算已空,如实标 budget_exhausted
        t1 = next(r for r in res.reviews if r.tier == 1)
        assert t1.llm_skip_reason == br.LLM_BUDGET_EXHAUSTED
        assert provider.calls == 0
        # **机械判照出**:LLM 缺席不影响九项
        assert all(k in t1.mech for k in br.MECH_ITEM_KEYS)

    def test_llm_success_is_recorded_without_degrade(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        _seed_basket(env, ["A.SZ"], tier=1, key="ka")
        write_daily_fixture(env, "daily", D1, [_bar("A.SZ", close=10.5, pre_close=10.0)])
        provider = _StubProvider("今天这篮 A 领涨,验证条件全数达成。")
        res = br.review_day(D1, d0=D0, db_path=env.db_path, parquet_dir=env.parquet_dir,
                            use_llm=True, provider=provider, ledger=BudgetLedger())
        assert provider.calls == 1 and res.llm_called == 1
        r = res.reviews[0]
        assert r.llm_text and r.degraded is False and r.llm_skip_reason is None
        row = store.load_review(r.basket_id, D1, db_path=env.db_path)
        assert row.llm_text == r.llm_text and row.degraded == 0

    def test_context_starts_with_date_anchor(self, isolated_env):
        env = isolated_env
        day = _day(bars=[_bar("A.SZ", close=10.5, pre_close=10.0)], index_ret=0.0)
        mech = br.build_mech(_ref(["A.SZ"]), _card(["A.SZ"]), day, db_path=env.db_path)
        review = br.BasketReview(basket_id=1, basket_key="k1", name="测试篮", tier=1,
                                 review_date=D1, d0=D0, depth=br.DEPTH_FULL, mech=mech)
        ctx = br.build_review_context(review, _card(["A.SZ"]), day)
        assert ctx.splitlines()[0].startswith("今天是")     # `prompt_context` 唯一实现
        assert "2026" in ctx.splitlines()[0]

    def test_prompt_forbids_prediction_and_single_day_conclusions(self):
        p = br.REVIEW_SYSTEM_PROMPT
        assert "不预测明天" in p and "不许由一天的结果" in p
        assert "参考,不是指令" in p


class TestReviewDayOrchestration:
    def test_depth_by_tier(self):
        """V2.1-②:两档恒 `full`(T3 退役,`brief` 已无写入方)。

        `DEPTH_BRIEF` 常量**保留**——历史 `basket_review_daily` 行仍是 `depth='brief'`,
        读回渲染要用它(「停写留档」纪律在**值**层面的同一条),⛔ 别因为"没人写了"
        就把常量删掉。历史 D0 被重放(那天可能有 tier=3)也给 `full`:新规下给更完整
        的复盘而不是更少,方向安全。"""
        assert br.depth_for_tier(1) == br.DEPTH_FULL
        assert br.depth_for_tier(2) == br.DEPTH_FULL
        assert br.depth_for_tier(3) == br.DEPTH_FULL
        assert br.DEPTH_BRIEF == "brief"      # 常量还在(读侧要用)

    def test_no_basket_day_is_a_note_not_an_exception(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        res = br.review_day(D1, d0=D0, db_path=env.db_path, parquet_dir=env.parquet_dir)
        assert res.reviews == [] and res.notes

    def test_cross_basket_rank_is_attached(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        _seed_basket(env, ["A.SZ"], tier=1, key="ka", name="强篮")
        _seed_basket(env, ["B.SZ"], tier=2, key="kb", name="弱篮")
        write_daily_fixture(env, "daily", D1, [_bar("A.SZ", close=10.6, pre_close=10.0),
                                               _bar("B.SZ", close=9.4, pre_close=10.0)])
        res = br.review_day(D1, d0=D0, db_path=env.db_path, parquet_dir=env.parquet_dir)
        by_key = {r.basket_key: r for r in res.reviews}
        assert by_key["ka"].mech["tier_vs_outcome"]["rank_by_outcome"] == 1
        assert by_key["kb"].mech["tier_vs_outcome"]["rank_by_outcome"] == 2
        assert by_key["ka"].mech["tier_vs_outcome"]["day_baskets"] == 2
        assert res.rows_inserted == 2
