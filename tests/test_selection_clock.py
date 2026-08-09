"""V2.2-④-A 选股时钟(`neckline/review/selection_clock.py`)。

覆盖 plan ④-A 点名的五条:结案幂等(同篮二次调用零新行、内容逐位不变)· 九项各自三态 ·
**零 import 持仓**(AST + 文本双向)· 未触发 / 已触发两路 · 覆盖域 = D0 全部 T1/T2
(**与买没买无关**)。
"""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

import pytest

from neckline.db import connection
from neckline.eval import metrics
from neckline.review import selection_clock as sc

from .conftest import business_days, insert_trade_cal, source_code_only

pytestmark = pytest.mark.usefixtures("isolated_env")

_ROOT = Path(__file__).resolve().parent.parent
_MODULE = _ROOT / "neckline" / "review" / "selection_clock.py"

_D0, _D1 = "20260805", "20260806"


# ══════════════════════════════════════════════════════════════════════════
# 造数
# ══════════════════════════════════════════════════════════════════════════

class _Ref:
    """duck-typed `BasketRef`(本模块刻意不 import 那个类型)。"""

    def __init__(self, basket_id, key, tier=1, codes=("A.SZ",), *,
                 engine_code="C", engine_version="C1", skeleton_version="K8-V0.5"):
        self.basket_id, self.basket_key, self.name = basket_id, key, f"篮{basket_id}"
        self.tier, self.trade_date, self.member_codes = tier, _D0, tuple(codes)
        self.engine_code, self.engine_version = engine_code, engine_version
        self.skeleton_version = skeleton_version


def _card(codes=("A.SZ",), *, zone=(9.0, 10.0), ruleset="verify_ruleset_v2"):
    return {
        "fingerprint": {"pack_version": "K8-V0.5", "verification_ruleset_version": ruleset},
        "members": [{"ts_code": c,
                     "entry_zone": ({"low": zone[0], "high": zone[1], "why": "x"} if zone else None)}
                    for c in codes],
    }


def _review_mech(*, state="verified", up=2, observed=2, led=True, index_ret=0.004,
                 outcome=0.02, not_evaluated=False):
    return {
        "verification_timing": {"available": True, "state": state, "state_label": state,
                                "eod_state": state, "has_eod_verdict": True,
                                "not_evaluated": not_evaluated, "latched_falsified": False,
                                "first_verified_at": None, "first_falsified_at": None},
        "member_alignment": {"available": True, "observed": observed, "member_count": observed,
                             "up": up, "down": observed - up, "flat": 0,
                             "alignment": 1.0, "dominant_direction": "up"},
        "close_rs": {"available": True, "index_code": "000001.SH", "index_ret": index_ret,
                     "excess_median": 0.01, "outperformers": 1},
        "leader_pull": {"available": True, "leaders": ["A.SZ"], "leader_ret_median": 0.03,
                        "others_ret_median": 0.01, "spread": 0.02, "led": led,
                        "no_peer_group": False},
        "mfe_mae": {"available": True, "mfe_median": 0.03, "mae_median": -0.01,
                    "mfe_source": "eod_approx", "capture_status": "missing"},
        "open_direction": {"available": True, "gap_median": 0.01, "gap_dir": "up",
                           "intraday_median": 0.01, "intraday_dir": "up", "aligned": True},
        "tier_vs_outcome": {"available": True, "basket_ret_median": outcome},
    }


def _bars(low=9.2, high=10.5, codes=("A.SZ",)):
    return {c: {"low": low, "high": high, "close": 10.0, "pct_chg": 2.0} for c in codes}


def _seed_regime(env, day=_D0, regime="trend_continuation"):
    with connection(env.db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO market_regime_daily (trade_date, regime, regime_reason,"
            " inputs_json, strengthening_json, weakening_json, skeleton_version, computed_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (day, regime, "r", "{}", "[]", "[]", "K8-V0.5", "t"),
        )


def _seed_landing(env, day=_D1, code="A.SZ"):
    with connection(env.db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO landing_metrics_daily (trade_date, ts_code, metrics_json,"
            " metrics_missing, computed_at) VALUES (?,?,?,?,?)",
            (day, code, json.dumps({"rs5": 0.03, "cum_return_3d": 0.05}), "{}", "t"),
        )


def _closure(env, **kw):
    ref = kw.pop("ref", None) or _Ref(1, "ka")
    return sc.build_closure(ref, kw.pop("card", _card()), kw.pop("review_mech", _review_mech()),
                            d1=_D1, bars=kw.pop("bars", _bars()), db_path=env.db_path)


# ══════════════════════════════════════════════════════════════════════════
# 🔴 结构性保证:写入路径零 import 持仓
# ══════════════════════════════════════════════════════════════════════════

class TestNoPositionsPath:
    """🔴 plan ④-A:「买没买不影响样本」**靠"没有那条路"担保,不靠自觉**。"""

    def test_write_path_never_imports_positions(self):
        tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        offenders = [m for m in imported
                     if "positions" in m or "position_entry" in m or "positions_entry" in m]
        assert not offenders, (
            f"选股时钟 import 了持仓相关模块:{offenders} —— 「买没买不影响样本」"
            f"(K8 §十四 第 3 条)是靠没有这条路担保的,⛔ 别加回来")

    def test_module_text_never_touches_position_tables(self):
        """文本半:SQL 里不许出现持仓侧表名(AST 抓不到存在常量里的 SQL)。

        ⚠ 扫的是**剥掉注释与 docstring 的代码**(`source_code_only`)—— 模块头正
        写着「不 import 持仓」这些字,裸 grep 会被自己的护栏注释绊住(CLAUDE.md ⑰ 教训);
        真代码里的字符串常量(SQL)原样保留,该抓的照样抓得到。"""
        text = source_code_only(_MODULE).upper()
        for table in ("FROM POSITIONS", "INTO POSITIONS", "FROM ENTRY_SNAPSHOTS",
                      "FROM POSITION_PLANS", "FROM TRADE_CLOCK"):
            assert table not in text, f"选股时钟碰了持仓侧表:{table}"

    def test_coverage_is_all_t1_t2_regardless_of_holdings(self, isolated_env):
        """覆盖域写死 = D0 全部 T1/T2,**⛔ 不随调用方漂**(注入 T3 也会被剔掉)。"""
        assert sc.COVERED_TIERS == (1, 2)
        refs = [_Ref(1, "ka", tier=1), _Ref(2, "kb", tier=2), _Ref(3, "kc", tier=3)]
        res = sc.close_day(_D1, d0=_D0, refs=refs, cards={}, review_mechs={},
                           bars={}, db_path=isolated_env.db_path)
        assert res.baskets == 2
        assert {c.covered_tier for c in res.closures} == {1, 2}


# ══════════════════════════════════════════════════════════════════════════
# 结案 = 只增不改
# ══════════════════════════════════════════════════════════════════════════

class TestClosureIsFrozen:
    def test_second_close_adds_no_row_and_changes_nothing(self, isolated_env):
        env = isolated_env
        c = _closure(env)
        assert sc.save_closures([c], db_path=env.db_path) == {"inserted": 1, "existing": 0}
        first = sc.load_closure(1, db_path=env.db_path)

        # 第二次拿一份**内容不同**的结案件来撞:结了就是结了,一个字都不许被改写。
        c2 = _closure(env, review_mech=_review_mech(state="falsified", led=False))
        assert sc.save_closures([c2], db_path=env.db_path) == {"inserted": 0, "existing": 1}
        again = sc.load_closure(1, db_path=env.db_path)
        assert again == first
        assert again["tier_accuracy"] == "verified"          # 不是第二次那份的 falsified

        with connection(env.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM selection_clock").fetchone()[0] == 1

    def test_close_day_is_idempotent(self, isolated_env):
        env = isolated_env
        kw = dict(d0=_D0, refs=[_Ref(1, "ka")], cards={1: _card()},
                  review_mechs={1: _review_mech()}, bars=_bars(), db_path=env.db_path)
        a = sc.close_day(_D1, **kw)
        b = sc.close_day(_D1, **kw)
        assert (a.inserted, a.existing) == (1, 0)
        assert (b.inserted, b.existing) == (0, 1)

    def test_module_has_no_update_or_delete_path(self):
        text = source_code_only(_MODULE).upper()
        assert "INSERT OR IGNORE INTO SELECTION_CLOCK" in text
        for verb in ("INSERT OR REPLACE INTO SELECTION_CLOCK", "REPLACE INTO SELECTION_CLOCK"):
            assert verb not in text


# ══════════════════════════════════════════════════════════════════════════
# 九项各自三态
# ══════════════════════════════════════════════════════════════════════════

class TestNineItems:
    def test_keys_are_the_k8_nine_in_order(self, isolated_env):
        mech = _closure(isolated_env).mech
        present = [k for k in mech if k in sc.MECH_ITEM_KEYS]
        assert present == list(sc.MECH_ITEM_KEYS), "九项顺序 = K8 §十四 原文顺序,⛔ 不许重排"
        assert len(sc.MECH_ITEM_KEYS) == 9

    def test_every_item_carries_available_and_reason(self, isolated_env):
        mech = _closure(isolated_env).mech
        for key in sc.MECH_ITEM_KEYS:
            item = mech[key]
            assert "available" in item and "unavailable_reason" in item, key
            if not item["available"]:
                assert item["unavailable_reason"], f"{key} 标了不可得却没说为什么"

    def test_regime_missing_row_is_unknown_not_default_state(self, isolated_env):
        """🔴 缺行 = **不知道**,⛔ 不许回填默认态(那会把系统缺席讲成"市场是延续")。"""
        item = _closure(isolated_env).mech["regime_at_d0"]
        assert item["available"] is False and item["regime"] is None
        assert "缺行" in item["unavailable_reason"]

    def test_regime_present_is_read_through(self, isolated_env):
        env = isolated_env
        _seed_regime(env, regime="high_divergence")
        c = _closure(env)
        assert c.mech["regime_at_d0"]["regime"] == "high_divergence"
        assert c.regime_at_d0 == "high_divergence"

    def test_reused_items_come_from_the_daily_review_not_recomputed(self, isolated_env):
        """②③④⑦ 四项复用 ⑨ 日复盘的机械判(plan ④-A「⛔ 不重写」)。"""
        mech = _closure(isolated_env).mech
        assert mech["driver_persistence"]["source"] == "basket_review.verification_timing"
        assert mech["sector_sync"]["source"].startswith("basket_review.")
        assert mech["core_strength"]["source"] == "basket_review.leader_pull"
        assert mech["intraday_support_and_close"]["source"].startswith("basket_review.")

    def test_missing_review_degrades_those_four_not_the_whole_closure(self, isolated_env):
        c = _closure(isolated_env, review_mech=None)
        for key in ("driver_persistence", "sector_sync", "core_strength",
                    "intraday_support_and_close"):
            assert c.mech[key]["available"] is False
        assert c.mech["entry_zone_triggered"]["available"] is True     # 其余项照出

    def test_liftoff_reads_d1_metrics_and_d0_verdict_but_gives_no_state(self, isolated_env):
        """🔴 裁定 #11 后机械层**没有「起跳态」** —— 本项只出读数,⛔ 不许出现态字段。"""
        env = isolated_env
        _seed_landing(env)
        item = _closure(env).mech["liftoff_signal"]
        assert item["available"] is True
        assert item["d1_metrics"]["A.SZ"]["metrics"]["rs5"] == 0.03
        assert "landing_state" not in item and "state" not in item
        assert "买入期望" in item["note"]

    def test_liftoff_missing_metrics_is_unavailable_not_zero(self, isolated_env):
        item = _closure(isolated_env).mech["liftoff_signal"]
        assert item["available"] is False and item["members_with_metrics"] == 0
        assert item["d1_metrics"]["A.SZ"]["available"] is False


# ══════════════════════════════════════════════════════════════════════════
# ⑤⑧ 触发 / 未触发两路
# ══════════════════════════════════════════════════════════════════════════

class TestEntryZoneAndUntriggered:
    def test_triggered_when_the_day_range_touches_the_frozen_zone(self, isolated_env):
        c = _closure(isolated_env, bars=_bars(low=9.5, high=10.2))
        item = c.mech["entry_zone_triggered"]
        assert item["any_triggered"] is True and item["triggered"] == 1
        assert item["per_member"]["A.SZ"]["reason"] == "in_zone"
        assert c.untriggered_reason is None            # 触发了 → NULL,⛔ 不拿空串冒充

    def test_not_reached_when_the_day_stays_above_the_zone(self, isolated_env):
        c = _closure(isolated_env, bars=_bars(low=10.5, high=11.0))
        assert c.mech["entry_zone_triggered"]["any_triggered"] is False
        assert c.untriggered_reason == sc.UNTRIGGERED_ZONE_NOT_REACHED

    def test_no_zone_on_card_is_its_own_reason(self, isolated_env):
        c = _closure(isolated_env, card=_card(zone=None))
        assert c.mech["entry_zone_triggered"]["members_with_zone"] == 0
        assert c.untriggered_reason == sc.UNTRIGGERED_NO_ENTRY_ZONE

    def test_no_d1_bar_is_its_own_reason(self, isolated_env):
        c = _closure(isolated_env, bars={})
        assert c.untriggered_reason == sc.UNTRIGGERED_NO_D1_BAR

    def test_the_four_reasons_are_distinguishable(self):
        codes = {sc.UNTRIGGERED_NO_ENTRY_ZONE, sc.UNTRIGGERED_NO_D1_BAR,
                 sc.UNTRIGGERED_ZONE_NOT_REACHED, sc.UNTRIGGERED_UNKNOWN}
        assert len(codes) == 4


# ══════════════════════════════════════════════════════════════════════════
# ⑨ 分层准确性 = 四态原样(⛔ 不压成 0/1)
# ══════════════════════════════════════════════════════════════════════════

class TestTierAccuracy:
    @pytest.mark.parametrize("state", ["verified", "partial", "unclear", "falsified"])
    def test_four_states_pass_through_verbatim(self, isolated_env, state):
        c = _closure(isolated_env, review_mech=_review_mech(state=state))
        assert c.tier_accuracy == state

    def test_not_evaluated_is_a_separate_code_not_a_bad_score(self, isolated_env):
        c = _closure(isolated_env, review_mech=_review_mech(not_evaluated=True))
        assert c.tier_accuracy == sc.TIER_ACCURACY_NOT_EVALUATED
        assert c.mech["tier_accuracy"]["available"] is False

    def test_no_review_at_all_is_unknown(self, isolated_env):
        assert _closure(isolated_env, review_mech=None).tier_accuracy == sc.TIER_ACCURACY_UNKNOWN

    def test_it_is_never_a_boolean_or_a_ratio(self, isolated_env):
        """🔴 「多少算对」是一条**定量的线**,K8 §十七 没给 —— 本列保留四态原样。"""
        c = _closure(isolated_env)
        assert isinstance(c.tier_accuracy, str)
        assert "⛔ 本列不压成 0/1" in c.mech["tier_accuracy"]["note"]


# ══════════════════════════════════════════════════════════════════════════
# 分层键 / 引擎归因 / 读侧
# ══════════════════════════════════════════════════════════════════════════

class TestStratumAndRead:
    def test_engine_breakdown_is_two_keys_after_decision_9(self, isolated_env):
        c = _closure(isolated_env)
        assert c.engine_breakdown == {"engine_code": "C", "engine_version": "C1"}

    def test_legacy_basket_falls_back_to_its_pack_version(self, isolated_env):
        """老篮子(K8 之前)没有骨架版本 —— 骨架位退回它当时的 `pack_version`,
        让历史样本**留在自己那一层**(⛔ 不许并进 K8 新层)。"""
        ref = _Ref(9, "old", engine_code=None, engine_version=None, skeleton_version=None)
        c = sc.build_closure(ref, _card(), _review_mech(), d1=_D1, bars=_bars(),
                             db_path=isolated_env.db_path)
        assert c.skeleton_version == "K8-V0.5"        # 来自卡 fingerprint.pack_version
        assert c.engine_breakdown == {"engine_code": None, "engine_version": None}

    def test_unset_placeholder_matches_the_metrics_one(self):
        """两处占位串必须同值 —— 否则同一批样本在两份成绩单上会分到两个层。"""
        assert sc.UNSET_VERSION == metrics.UNSET

    def test_list_closures_is_deterministic_and_window_bounded(self, isolated_env):
        env = isolated_env
        sc.save_closures([
            sc.build_closure(_Ref(2, "kb"), _card(), _review_mech(), d1=_D1,
                             bars=_bars(), db_path=env.db_path),
            sc.build_closure(_Ref(1, "ka"), _card(), _review_mech(), d1=_D1,
                             bars=_bars(), db_path=env.db_path),
        ], db_path=env.db_path)
        rows = sc.list_closures("20260801", "20260810", db_path=env.db_path)
        assert [r["basket_id"] for r in rows] == [1, 2]
        assert sc.list_closures("20260101", "20260102", db_path=env.db_path) == []

    def test_narrative_is_a_pointer_not_a_second_llm_call(self, isolated_env):
        """⛔ 零新增 LLM 调用:结案叙述并进 ⑨ 复盘那一次,这里只留指针。"""
        meta = _closure(isolated_env).mech["meta"]
        assert meta["narrative_ref"]["table"] == "basket_review_daily"
        assert "不单独调用 LLM" in meta["narrative_ref"]["note"]

    def test_module_never_calls_an_llm(self):
        tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
        calls = {n.func.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert "chat" not in calls


# ══════════════════════════════════════════════════════════════════════════
# 编排:从库里自己凑齐素材(独立回放路径)
# ══════════════════════════════════════════════════════════════════════════

def test_close_day_loads_everything_from_the_db_when_nothing_is_injected(isolated_env):
    env = isolated_env
    insert_trade_cal(env, business_days(date(2026, 8, 3), 10))
    with connection(env.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status,"
            " created_at, engine_code, engine_version, skeleton_version)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_D0, "ka", "篮", "驱动", "theme", 1, "K8-V0.5", 2, "v1.3.3", "auto", "ok", "t",
             "Z", "Z1", "K8-V0.5"),
        )
        bid = int(cur.lastrowid)
        conn.execute("INSERT INTO basket_members (basket_id, ts_code, role_llm, reason,"
                     " created_at) VALUES (?,?,?,?,?)", (bid, "A.SZ", "leader", "r", "t"))
        conn.execute("INSERT INTO basket_review_daily (basket_id, review_date, depth,"
                     " mech_json, created_at) VALUES (?,?,?,?,?)",
                     (bid, _D1, "full", json.dumps(_review_mech()), "t"))

    res = sc.close_day(_D1, d0=_D0, db_path=env.db_path)
    assert res.inserted == 1
    row = sc.load_closure(bid, db_path=env.db_path)
    assert row["engine_breakdown"] == {"engine_code": "Z", "engine_version": "Z1"}
    assert row["tier_accuracy"] == "verified"


def test_close_day_never_raises_on_a_broken_input(isolated_env):
    """**永不抛异常**:素材装配炸了只记 note(同 `review_day` 的既定姿势)。"""
    res = sc.close_day("not-a-date", d0=_D0, db_path=isolated_env.db_path)
    assert res.closures == [] and res.notes
