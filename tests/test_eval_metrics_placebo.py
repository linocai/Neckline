"""V2-⑨-C / ⑨-C2 评价引擎 + 安慰剂对照臂(plan §五 V2-⑨ 验收逐条)。

覆盖:分层归因(`pack_version` × `verification_ruleset_version`,preseed 自成一层);
Tier 单调性 / 共振率 / 验证率在造数上算对;买不进的 0 **不进收益均值**;
前向窗口没走完的不混进已完成样本;已选 vs 未选在无用户数据时**空值如实**;
**⑨-C2 追加三条** —— ① 两臂在造数上算对 + **同一交易日跑两次逐位相同**
(`crc32` 派生种子的可复现性);② 随机臂走同一套 `exit_sim`(调用面断言);
③ 小样本时报告出的是**样本数而非结论**(文案断言)。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tests.conftest import business_days, insert_trade_cal

from neckline.db import connection
from neckline.eval import calibration, metrics, placebo
from neckline.eval.exit_sim import PriceMaps
from neckline.selection import verification_rules as vr

pytestmark = pytest.mark.usefixtures("isolated_env")

_REPO = Path(__file__).resolve().parent.parent

_KW = dict(base_hold=5, retrace=0.08, stop=0.05, v1=True, hard_cap=15)


# ══════════════════════════════════════════════════════════════════════════
# 造数
# ══════════════════════════════════════════════════════════════════════════

def _mech(*, outcome=0.03, up=2, observed=2, alignment=1.0, spread=0.02, led=True,
          index_ret=0.005, mfe_source="eod_approx", buyable=None):
    return {
        "spec_version": "basket_review_mech_v1",
        "meta": {"pack_version": "K4-pack-v1",
                 "verification_ruleset_version": vr.VERIFICATION_RULESET_VERSION},
        "member_alignment": {"observed": observed, "up": up, "down": observed - up,
                             "alignment": alignment},
        "leader_pull": {"spread": spread, "led": led, "no_peer_group": False},
        "close_rs": {"index_ret": index_ret},
        "mfe_mae": {"mfe_source": mfe_source},
        "buyability": {"per_member": buyable or {}},
        "tier_vs_outcome": {"basket_ret_median": outcome},
    }


_DEFAULT_RULESET = object()      # 「没传」与「显式传 None」必须分得开(测分层键缺失)


def _rec(basket_id, d0="20260723", *, tier=1, key=None, members=("A.SZ",), outcome=0.03,
         pack="K4-pack-v1", ruleset=_DEFAULT_RULESET, state=vr.STATE_VERIFIED, selected=False,
         driver_kind="theme", **mech_kw) -> metrics.BasketRecord:
    r = metrics.BasketRecord(
        basket_id=basket_id, d0=d0, basket_key=key or f"k{basket_id}", name=f"篮{basket_id}",
        tier=tier, driver_kind=driver_kind, pack_version=pack,
        members=tuple(members), roles={c: "leader" for c in members},
        ruleset_version=(vr.VERIFICATION_RULESET_VERSION if ruleset is _DEFAULT_RULESET else ruleset),
        verification_state=state, selected=selected, evidence_status="ok",
    )
    r.review_mech = _mech(outcome=outcome, **mech_kw)
    r.review_date = "20260724"
    r.review_degraded = 0
    return r


def _flat_maps(codes, cal, *, closes=None):
    """一段平价行情:每天开=收=10,无止损/回落触发,`base_hold` 到期时间退出。"""
    pm = {}
    for c in codes:
        n = len(cal)
        pm[c] = {"idx": {d: i for i, d in enumerate(cal)},
                 "o": [10.0] * n, "l": [9.99] * n, "c": (closes or [10.0] * n)}
    return PriceMaps(pm, set(), cal, {d: i for i, d in enumerate(cal)}, qfq_anchor=cal[-1])


# ══════════════════════════════════════════════════════════════════════════
# 分层
# ══════════════════════════════════════════════════════════════════════════

class TestStratification:
    def test_pack_and_ruleset_are_separate_strata(self):
        # 第三条刻意用**已退役的** `verify_ruleset_v1`(判定线 🟡-1 之后现役是 v2):
        # 这里要的是「另一个条件集版本自成一层」,不是某个具体串,故取真实历史值而不是
        # 跟着现役版本走(跟着走就永远比不出两层)。
        recs = [_rec(1, pack="K4-pack-v1"), _rec(2, pack="K7-pack-v1"),
                _rec(3, pack="K4-pack-v1", ruleset="verify_ruleset_v1")]
        reports = metrics.evaluate(recs, score_kw=_KW, notional=20000.0, with_tradable=False)
        keys = [(r.pack_version, r.ruleset_version) for r in reports]
        assert keys == sorted(keys)                      # 层序确定
        assert len(keys) == 3

    def test_preseed_is_its_own_stratum(self):
        """preseed 行 `pack_version='preseed'` 单独分层 —— 人工配的成绩不许算到包头上。"""
        recs = [_rec(1, pack="K4-pack-v1"), _rec(2, pack="preseed")]
        reports = metrics.evaluate(recs, score_kw=_KW, notional=20000.0, with_tradable=False)
        assert {r.pack_version for r in reports} == {"K4-pack-v1", "preseed"}

    def test_missing_stratum_key_is_explicit_not_merged(self):
        recs = [_rec(1, ruleset=None)]
        assert recs[0].stratum[1] == metrics.UNSET
        reports = metrics.evaluate(recs, score_kw=_KW, notional=20000.0, with_tradable=False)
        assert reports[0].ruleset_version == metrics.UNSET


# ══════════════════════════════════════════════════════════════════════════
# 指标算得对
# ══════════════════════════════════════════════════════════════════════════

class TestMetrics:
    def test_tier_monotonicity(self):
        recs = [_rec(1, tier=1, outcome=0.05), _rec(2, tier=2, outcome=0.02),
                _rec(3, tier=3, outcome=-0.01)]
        t = metrics.tier_monotonicity(recs)
        assert t["median_outcome"] == {1: 0.05, 2: 0.02, 3: -0.01}
        assert t["monotonic"] is True
        broken = metrics.tier_monotonicity(
            [_rec(1, tier=1, outcome=0.0), _rec(2, tier=2, outcome=0.09)])
        assert broken["monotonic"] is False
        assert "不是收益预测" in t["note"]

    def test_historical_t3_samples_are_never_dropped_or_merged(self):
        """🔴 **V2.1-② 的"历史样本不许消失"机器判据**(plan ② 测试与守门第 3 条)。

        T3 于 V2.1 退役,但 `K7-pack-v1` 那个分层里的 tier=3 篮子**是真实历史样本**。
        评价引擎若把它们丢掉(或更糟:并进 T2)就是**伪造归因** —— 成绩单会凭空变好
        或变差,而且看不出来。判据:三档全部出现在 `counts`/`observed`/`median_outcome`
        里,且单调性**按三档算**。"""
        recs = [_rec(1, tier=1, outcome=0.05), _rec(2, tier=2, outcome=0.02),
                _rec(3, tier=3, outcome=-0.01), _rec(4, tier=3, outcome=-0.03)]
        t = metrics.tier_monotonicity(recs)
        assert t["counts"] == {1: 1, 2: 1, 3: 2}          # 两个 T3 样本一个都没少
        assert t["observed"] == {1: 1, 2: 1, 3: 2}
        assert set(t["median_outcome"]) == {1, 2, 3}
        assert t["median_outcome"][3] == pytest.approx(-0.02)   # ⛔ 没被并进 T2
        assert t["monotonic"] is True                    # 按 T1 ≥ T2 ≥ T3 三档算

        # 反例:只有 T3 的中位数抬到 T2 之上,三档比较才判得出"不单调"
        # ——若历史 T3 被丢掉,这个 False 就永远出不来(那正是"归因被搅乱"的样子)。
        broken = metrics.tier_monotonicity(
            [_rec(1, tier=1, outcome=0.05), _rec(2, tier=2, outcome=0.02),
             _rec(3, tier=3, outcome=0.09)])
        assert broken["monotonic"] is False

    def test_two_tier_data_is_scored_on_two_tiers_without_a_ghost_third(self):
        """反向:V2.1 之后的新数据只有 T1/T2 → **不许**凭空多出一个恒为 0 的 T3
        幽灵档(写死 `(1,2,3)` 就会)。单调性自然退化为 `T1 ≥ T2`。"""
        recs = [_rec(1, tier=1, outcome=0.05), _rec(2, tier=2, outcome=0.02)]
        t = metrics.tier_monotonicity(recs)
        assert t["counts"] == {1: 1, 2: 1}
        assert t["observed"] == {1: 1, 2: 1}
        assert 3 not in t["median_outcome"] and 3 not in t["mean_outcome"]
        assert t["monotonic"] is True

    def test_tier_chain_text_follows_the_data_not_a_hardcoded_arity(self):
        """`strata()` 的 `tier_verdict` 文案按**本分层实际出现的档位**拼:两档时代
        说 `T1 ≥ T2`,历史三档分层说 `T1 ≥ T2 ≥ T3`。⛔ 写死一边就在另一边说假话。"""
        two = metrics.tier_monotonicity(
            [_rec(1, tier=1, outcome=0.05), _rec(2, tier=2, outcome=0.02)])
        three = metrics.tier_monotonicity(
            [_rec(1, tier=1, outcome=0.05), _rec(2, tier=2, outcome=0.02),
             _rec(3, tier=3, outcome=-0.01)])
        assert metrics._tier_chain_text(two) == "T1 ≥ T2"
        assert metrics._tier_chain_text(three) == "T1 ≥ T2 ≥ T3"
        assert metrics._tier_chain_text({}) == "各档"      # 无样本时不说假话

    def test_resonance_reuses_min_members_hit(self):
        """门槛复用 ⑦-b 的 `min_members_hit`,⛔ 不在评价层发明第二个"几只算共振"。"""
        recs = [_rec(1, up=2, observed=3),       # ceil(3/2)=2 → 共振
                _rec(2, up=1, observed=3),       # 1 < 2 → 不共振
                _rec(3, up=0, observed=0)]       # 判不了
        r = metrics.resonance_rate(recs)
        assert r["judged"] == 2 and r["resonant"] == 1 and r["rate"] == pytest.approx(0.5)
        assert r["unjudged"] == 1
        assert vr.min_members_hit(3) == 2

    def test_verification_rate_excludes_not_evaluated_from_denominator(self):
        recs = [_rec(1, state=vr.STATE_VERIFIED), _rec(2, state=vr.STATE_FALSIFIED),
                _rec(3, state=None)]
        v = metrics.verification_rate(recs)
        assert v["judged"] == 2 and v["not_evaluated"] == 1
        assert v["verified_rate"] == pytest.approx(0.5)   # 而不是 1/3
        assert "不进分母" in v["note"]

    def test_leader_vs_members(self):
        recs = [_rec(1, spread=0.03, led=True), _rec(2, spread=-0.01, led=False)]
        ld = metrics.leader_vs_members(recs)
        assert ld["judged"] == 2 and ld["led"] == 1
        assert ld["spread_median"] == pytest.approx(0.01)

    def test_selected_vs_not_is_honest_when_no_user_data(self):
        out = metrics.selected_vs_not([_rec(1), _rec(2)])
        assert out["available"] is False and out["reason"] == "no_user_data"
        assert out["selected"] == 0 and out["selected_outcome"] is None

    def test_selected_vs_not_when_data_exists(self):
        out = metrics.selected_vs_not([_rec(1, selected=True, outcome=0.06), _rec(2, outcome=0.01)])
        assert out["available"] is True
        assert out["selected_outcome"] == pytest.approx(0.06)
        assert out["not_selected_outcome"] == pytest.approx(0.01)

    def test_contribution_buckets(self):
        recs = [_rec(1, driver_kind="policy", outcome=0.04, index_ret=0.01),
                _rec(2, driver_kind="theme", outcome=-0.02, index_ret=-0.01)]
        c = metrics.contribution(recs)
        assert set(c["by_driver_kind"]) == {"policy", "theme"}
        assert set(c["by_market_regime"]) == {"index_up", "index_down"}
        assert c["by_driver_kind"]["policy"]["median"] == pytest.approx(0.04)


# ══════════════════════════════════════════════════════════════════════════
# 可交易收益(判分唯一源)
# ══════════════════════════════════════════════════════════════════════════

class TestTradable:
    def test_unbuyable_member_is_not_a_zero_return(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        cal = business_days(date(2026, 7, 23), 12)
        rec = _rec(1, members=("A.SZ", "B.SZ"),
                   buyable={"A.SZ": {"buyable": True}, "B.SZ": {"buyable": False}})
        maps = _flat_maps(["A.SZ", "B.SZ"], cal)
        tr = metrics.score_tradable([rec], price_maps=maps, score_kw=_KW, notional=20000.0)
        assert tr.not_filled == 1 and tr.filled == 1
        assert tr.fill_reasons.get("not_buyable") == 1
        # 篮子收益只由成交的那只贡献,买不进的没有被当成 0 拉低均值
        assert tr.per_basket[1] is not None

    def test_unfinished_window_is_counted_apart_not_averaged(self, isolated_env):
        """触发了退出但没有 T+1 可撮合(`reason="end"`)→ `unfinished`,不进均值。"""
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        cal = business_days(date(2026, 7, 23), 4)
        rec = _rec(1, members=("A.SZ",))
        # 末日暴跌触发 stop,但那之后没有交易日可撮合 → `_sim_one` 记 end
        maps = _flat_maps(["A.SZ"], cal, closes=[10.0, 10.0, 10.0, 8.0])
        tr = metrics.score_tradable([rec], price_maps=maps, score_kw=_KW, notional=20000.0)
        assert tr.unfinished == 1 and tr.unresolved == 0
        assert tr.per_basket[1] is None               # ⛔ 没走完就不给这个篮子一个数
        assert tr.summary()["member_unfinished"] == 1

    def test_unresolved_exit_is_counted_apart_too(self, isolated_env):
        """整段日历都没解出退出(窗口太短 / 价缺失)→ `unresolved`,同样不进均值。"""
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        cal = business_days(date(2026, 7, 23), 3)     # 平价 + 太短,`base_hold` 都没到
        rec = _rec(1, members=("A.SZ",))
        tr = metrics.score_tradable([rec], price_maps=_flat_maps(["A.SZ"], cal),
                                    score_kw=_KW, notional=20000.0)
        assert tr.unresolved == 1 and tr.unfinished == 0
        assert tr.per_basket[1] is None
        assert tr.fill_reasons.get("unresolved") == 1

    def test_max_chase_ceiling_is_respected(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        cal = business_days(date(2026, 7, 23), 12)
        rec = _rec(1, members=("A.SZ",))
        rec.card = {"members": [{"ts_code": "A.SZ", "max_chase": 9.0}]}   # 低于开盘 10.0
        maps = _flat_maps(["A.SZ"], cal)
        blocked = metrics.score_tradable([rec], price_maps=maps, score_kw=_KW, notional=20000.0)
        assert blocked.fill_reasons.get("above_ceiling") == 1
        free = metrics.score_tradable([rec], price_maps=maps, score_kw=_KW, notional=20000.0,
                                      respect_max_chase=False)
        assert "above_ceiling" not in free.fill_reasons

    def test_forward_window_ready(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 6, 1), 90))
        assert metrics.forward_window_ready(
            date(2026, 7, 1), score_kw=_KW, as_of=date(2026, 7, 31)) is True
        assert metrics.forward_window_ready(
            date(2026, 7, 30), score_kw=_KW, as_of=date(2026, 7, 31)) is False


# ══════════════════════════════════════════════════════════════════════════
# 面板读侧
# ══════════════════════════════════════════════════════════════════════════

def _seed(env, *, basket_id_key="ka", tier=1, pack="K4-pack-v1", codes=("A.SZ",),
          d0="20260723", card_json=None, review_mech=None, state=None, selected=False) -> int:
    with connection(env.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d0, basket_id_key, "篮", "驱动", "theme", tier, pack, 1, "v1.3.3", "auto", "ok", "t"),
        )
        bid = int(cur.lastrowid)
        for c in codes:
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, c, "core", "leader", 0, "r", 1, "t"))
        conn.execute(
            "INSERT INTO tier_history (trade_date, basket_id, tier, mech_score,"
            " mech_breakdown_json, rank_in_tier, rank_mech, llm_rank_delta, llm_reason,"
            " pack_version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (d0, bid, tier, 0.7, "{}", 1, 1, 0, None, pack, "t"))
        if card_json is not None:
            conn.execute(
                "INSERT INTO basket_cards (basket_id, version, card_json, stop_pct,"
                " take_profit_retrace, charter_version, pack_version, engine_api_version,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (bid, 1, card_json, 0.05, 0.08, "v1.3.3", pack, 1, "t"))
        if review_mech is not None:
            conn.execute(
                "INSERT INTO basket_review_daily (basket_id, review_date, depth, mech_json,"
                " llm_text, llm_skip_reason, degraded, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, "20260724", "full", review_mech, None, "no_provider", 1, "t"))
        if state:
            conn.execute(
                "INSERT INTO basket_verification (basket_id, trade_date, observed_at, state,"
                " source, evidence_json, created_at) VALUES (?,?,?,?,?,?,?)",
                (bid, "20260724", "2026-07-24T15:05:00+08:00", state, "eod", "{}", "t"))
        if selected:
            conn.execute(
                "INSERT INTO user_actions (occurred_at, kind, ts_code, basket_id, position_id,"
                " payload_json, created_at) VALUES (?,?,?,?,?,?,?)",
                ("2026-07-23T20:00:00+08:00", "select", None, bid, None, "{}", "t"))
    return bid


class TestLoadPanel:
    def test_pulls_everything_and_prefers_eod_state(self, isolated_env):
        import json

        env = isolated_env
        card = json.dumps({"members": [{"ts_code": "A.SZ", "max_chase": 11.0}],
                           "fingerprint": {"verification_ruleset_version": "verify_ruleset_v1"}})
        mech = json.dumps(_mech(outcome=0.04))
        bid = _seed(env, card_json=card, review_mech=mech, state=vr.STATE_VERIFIED, selected=True)
        recs = metrics.load_basket_panel("20260701", "20260731", db_path=env.db_path)
        assert len(recs) == 1
        r = recs[0]
        assert r.basket_id == bid and r.members == ("A.SZ",)
        assert r.ruleset_version == "verify_ruleset_v1"
        assert r.verification_state == vr.STATE_VERIFIED and r.verification_source == "eod"
        assert r.outcome == pytest.approx(0.04)
        assert r.max_chase_of("A.SZ") == pytest.approx(11.0)
        assert r.selected is True
        assert r.review_degraded == 1

    def test_missing_card_and_review_are_not_fatal(self, isolated_env):
        env = isolated_env
        _seed(env)
        recs = metrics.load_basket_panel("20260701", "20260731", db_path=env.db_path)
        assert recs[0].card is None and recs[0].review_mech is None
        assert recs[0].outcome is None and recs[0].ruleset_version is None


# ══════════════════════════════════════════════════════════════════════════
# ⑨-C2 安慰剂对照臂
# ══════════════════════════════════════════════════════════════════════════

class TestPlaceboSeeds:
    def test_seed_is_crc32_derived_and_stable(self):
        import zlib

        s = placebo.derive_seed("20260723", "K4-pack-v1", placebo.ARM_RANDOM)
        assert s == zlib.crc32(b"20260723|K4-pack-v1|random_basket")
        assert s == placebo.derive_seed(date(2026, 7, 23), "K4-pack-v1", placebo.ARM_RANDOM)
        assert s != placebo.derive_seed("20260724", "K4-pack-v1", placebo.ARM_RANDOM)
        assert s != placebo.derive_seed("20260723", "K7-pack-v1", placebo.ARM_RANDOM)

    def test_module_never_uses_builtin_hash(self):
        """⛔ 禁内置 `hash()`(带进程盐,历史报告不可复现)—— 源码级守门。"""
        import ast

        src = (_REPO / "neckline" / "eval" / "placebo.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "hash", f"第 {node.lineno} 行用了内置 hash()"


class TestRandomArm:
    def _domain(self, n=20):
        return placebo.LegalDomain("20260723", tuple(f"D{i:02d}.SZ" for i in range(n)))

    def _maps(self, domain, cal):
        return _flat_maps(list(domain.codes) + ["A.SZ", "B.SZ"], cal)

    def test_same_day_twice_is_bit_identical(self, isolated_env):
        """⑨-C2 验收第 ① 条:同一交易日跑两次逐位相同(种子可复现)。"""
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        cal = business_days(date(2026, 7, 23), 12)
        dom = self._domain()
        maps = self._maps(dom, cal)
        a1 = placebo.random_arm(date(2026, 7, 23), [2, 1], dom, "K4-pack-v1",
                                price_maps=maps, score_kw=_KW, notional=20000.0, draws=20)
        a2 = placebo.random_arm(date(2026, 7, 23), [2, 1], dom, "K4-pack-v1",
                                price_maps=maps, score_kw=_KW, notional=20000.0, draws=20)
        assert a1.values == a2.values and a1.seed == a2.seed
        assert a1.available and a1.draws == 20 and len(a1.values) == 20
        assert set(a1.quantiles) == {"p10", "p25", "p50", "p75", "p90"}

    def test_shape_matches_real_baskets(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        cal = business_days(date(2026, 7, 23), 12)
        dom = self._domain()
        arm = placebo.random_arm(date(2026, 7, 23), [3, 2], dom, "K4-pack-v1",
                                 price_maps=self._maps(dom, cal), score_kw=_KW,
                                 notional=20000.0, draws=5)
        assert arm.real_shape == [3, 2] and arm.domain_size == 20

    def test_domain_too_small_is_unavailable_not_resampled(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        cal = business_days(date(2026, 7, 23), 12)
        dom = self._domain(n=2)
        arm = placebo.random_arm(date(2026, 7, 23), [3, 2], dom, "K4-pack-v1",
                                 price_maps=self._maps(dom, cal), score_kw=_KW,
                                 notional=20000.0, draws=5)
        assert arm.available is False and "凑不出同规模对照" in arm.unavailable_reason
        assert arm.values == []          # ⛔ 不放宽成有放回抽样

    def test_unavailable_domain_is_reported(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        bad = placebo.LegalDomain("20260723", (), ok=False, note="当日无簇")
        arm = placebo.random_arm(date(2026, 7, 23), [1], bad, "K4-pack-v1",
                                 price_maps=_flat_maps(["A.SZ"], business_days(date(2026, 7, 23), 12)),
                                 score_kw=_KW,
                                 notional=20000.0, draws=5)
        assert arm.available is False and arm.unavailable_reason == "当日无簇"

    def test_scoring_goes_through_exit_sim_only(self, isolated_env, monkeypatch):
        """⑨-C2 验收第 ② 条:随机臂的每一笔都经 `exit_sim.fill_and_score`。"""
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        cal = business_days(date(2026, 7, 23), 12)
        dom = self._domain()
        calls = {"n": 0}
        real = placebo.fill_and_score

        def spy(*a, **kw):
            calls["n"] += 1
            return real(*a, **kw)

        monkeypatch.setattr(placebo, "fill_and_score", spy)
        placebo.random_arm(date(2026, 7, 23), [2], dom, "K4-pack-v1",
                           price_maps=self._maps(dom, cal), score_kw=_KW,
                           notional=20000.0, draws=7)
        assert calls["n"] == 7 * 2       # 7 次抽样 × 每次 2 只成员,一笔不漏


class TestBuyAndHoldArm:
    def test_missing_index_is_unavailable_not_zero(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        arm = placebo.buy_and_hold_arm(date(2026, 7, 23), "K4-pack-v1", score_kw=_KW,
                                       parquet_dir=env.parquet_dir)
        assert arm.available is False and arm.unavailable_reason
        assert arm.values == []

    def test_index_window_return(self, isolated_env):
        from tests.conftest import write_daily_fixture

        env = isolated_env
        days = business_days(date(2026, 7, 1), 40)
        insert_trade_cal(env, days)
        cal = [d for d in days if d > date(2026, 7, 23)][:5]
        for i, d in enumerate(cal):
            write_daily_fixture(env, "index_daily", d, [{
                "ts_code": "000001.SH", "trade_date": d, "open": 100.0 + i, "high": 101.0 + i,
                "low": 99.0 + i, "close": 100.5 + i, "pre_close": 100.0 + i,
                "change": 0.5, "pct_chg": 0.5, "vol": 1.0, "amount": 1.0}])
        arm = placebo.buy_and_hold_arm(date(2026, 7, 23), "K4-pack-v1", score_kw=_KW,
                                       parquet_dir=env.parquet_dir)
        assert arm.available is True
        # 首日开盘 100.0 → 第 5 日收盘 104.5
        assert arm.values[0] == pytest.approx(104.5 / 100.0 - 1.0)
        assert "不套用任何纪律" in (arm.note or "")


class TestPlaceboReport:
    def test_small_sample_reports_count_not_conclusion(self, isolated_env):
        """⑨-C2 验收第 ③ 条:小样本时报告出的是**样本数**而非结论。"""
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        dom = placebo.LegalDomain("20260723", tuple(f"D{i:02d}.SZ" for i in range(20)))
        reps = placebo.run_placebo(
            [_rec(1, members=("A.SZ",))], score_kw=_KW, notional=20000.0, draws=5,
            domain_resolver=lambda d, dbp, pq: dom, db_path=env.db_path,
            parquet_dir=env.parquet_dir,
        )
        assert len(reps) == 1
        v = reps[0].vs_random
        assert v["conclusive"] is False
        assert v["text"].startswith("N=1 个交易日,尚不足以判断")
        assert str(metrics.MIN_CONCLUSION_DAYS) in v["text"]

    def test_verdict_gives_conclusion_once_sample_is_enough(self):
        few = metrics.verdict(3, 9, "真的更好")
        assert few.conclusive is False and "尚不足以判断" in few.text and "真的更好" not in few.text
        many = metrics.verdict(metrics.MIN_CONCLUSION_DAYS, 40, "真的更好")
        assert many.conclusive is True and many.text == "真的更好"

    def test_arms_are_layered_by_pack_version(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        dom = placebo.LegalDomain("20260723", tuple(f"D{i:02d}.SZ" for i in range(20)))
        reps = placebo.run_placebo(
            [_rec(1, pack="K4-pack-v1"), _rec(2, pack="K7-pack-v1")],
            score_kw=_KW, notional=20000.0, draws=3,
            domain_resolver=lambda d, dbp, pq: dom, db_path=env.db_path,
            parquet_dir=env.parquet_dir,
        )
        assert [r.pack_version for r in reps] == ["K4-pack-v1", "K7-pack-v1"]

    def test_report_dict_states_the_no_ceiling_alignment(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        dom = placebo.LegalDomain("20260723", tuple(f"D{i:02d}.SZ" for i in range(20)))
        reps = placebo.run_placebo([_rec(1)], score_kw=_KW, notional=20000.0, draws=3,
                                   domain_resolver=lambda d, dbp, pq: dom,
                                   db_path=env.db_path, parquet_dir=env.parquet_dir)
        assert "不设最高追价上限" in reps[0].to_dict()["note"]


# ══════════════════════════════════════════════════════════════════════════
# 周度校准报告
# ══════════════════════════════════════════════════════════════════════════

class TestCalibrationReport:
    def test_empty_range_is_a_note_not_an_exception(self, isolated_env):
        env = isolated_env
        rep = calibration.build_report("20260701", "20260731", db_path=env.db_path,
                                       parquet_dir=env.parquet_dir, with_placebo=False)
        assert rep.n_baskets == 0 and rep.notes
        md = calibration.render_markdown(rep)
        assert "周度校准报告" in md and "本期无可校准对象" in " ".join(rep.notes)

    def test_render_carries_disclaimer_and_no_recommendation_language(self, isolated_env):
        import json

        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        card = json.dumps({"members": [{"ts_code": "A.SZ", "max_chase": 11.0}],
                           "fingerprint": {"verification_ruleset_version": "verify_ruleset_v1"}})
        _seed(env, card_json=card, review_mech=json.dumps(_mech()), state=vr.STATE_VERIFIED)
        rep = calibration.build_report("20260701", "20260731", db_path=env.db_path,
                                       parquet_dir=env.parquet_dir, with_placebo=False,
                                       with_tradable=False)
        md = calibration.render_markdown(rep)
        assert "不进任何在线判据" in md
        assert "注意力优先级" in md
        for banned in ("推荐买入", "建议买入", "看好", "值得买", "止盈线", "目标价"):
            assert banned not in md, f"校准报告出现禁用表述:{banned}"
        assert "尚不足以判断" in md          # 单日样本 → 只报样本数

    def test_tier_line_follows_the_data_no_ghost_t3_no_lost_history(self, isolated_env):
        """V2.1-②:校准报告的「Tier 单调性」一行按**本分层实际出现的档位**渲染。

        写死 `(1,2,3)` 会两头出错 —— 两档时代凭空多一行「T3 —(n=0)」(读起来像
        "今天 T3 没样本",真相是 T3 已取消 = 把系统缺席讲成了实质性结论);
        写死 `(1,2)` 又会让历史分层里真实存在的 T3 样本从成绩单上消失(伪造归因)。
        这份报告是复盘板块要端给用户的东西,两种错法都不许有。"""
        import json

        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        # ① 新数据(只有 T1/T2)→ 不许出现 T3
        _seed(env, basket_id_key="ka", tier=1, review_mech=json.dumps(_mech()))
        _seed(env, basket_id_key="kb", tier=2, review_mech=json.dumps(_mech()))
        md = calibration.render_markdown(calibration.build_report(
            "20260701", "20260731", db_path=env.db_path, parquet_dir=env.parquet_dir,
            with_placebo=False, with_tradable=False))
        tier_line = next(ln for ln in md.splitlines() if "Tier 单调性" in ln)
        assert "T1" in tier_line and "T2" in tier_line
        assert "T3" not in tier_line, tier_line

        # ② 同一份代码读历史(含 tier=3)→ T3 必须照常出现在成绩单上
        _seed(env, basket_id_key="kc", tier=3, review_mech=json.dumps(_mech()))
        md2 = calibration.render_markdown(calibration.build_report(
            "20260701", "20260731", db_path=env.db_path, parquet_dir=env.parquet_dir,
            with_placebo=False, with_tradable=False))
        tier_line2 = next(ln for ln in md2.splitlines() if "Tier 单调性" in ln)
        assert "T3" in tier_line2 and "(n=1)" in tier_line2, tier_line2

    def test_honesty_section_counts_gaps(self, isolated_env):
        import json

        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        _seed(env, basket_id_key="ka", review_mech=json.dumps(_mech()))
        _seed(env, basket_id_key="kb")           # 无卡、无复盘、无验证
        rep = calibration.build_report("20260701", "20260731", db_path=env.db_path,
                                       parquet_dir=env.parquet_dir, with_placebo=False,
                                       with_tradable=False)
        h = rep.honesty
        assert h["baskets"] == 2 and h["withoutReview"] == 1
        assert h["withoutCard"] == 2 and h["notEvaluated"] == 2
        assert h["mfeSource"]["eod_approx"] == 1
        assert "运维缺口" in h["note"]

    def test_write_report_emits_md_and_json(self, isolated_env, tmp_path):
        env = isolated_env
        rep = calibration.build_report("20260701", "20260731", db_path=env.db_path,
                                       parquet_dir=env.parquet_dir, with_placebo=False)
        paths = calibration.write_report(rep, tmp_path / "out")
        assert paths["markdown"].exists() and paths["json"].exists()
        import json as _json
        doc = _json.loads(paths["json"].read_text(encoding="utf-8"))
        assert doc["specVersion"] == calibration.REPORT_SPEC_VERSION
        assert "不进任何在线判据" in doc["disclaimer"]

    def test_week_bounds(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, business_days(date(2026, 7, 1), 40))
        lo, hi = calibration.week_bounds(date(2026, 7, 23))     # 周四
        assert lo == date(2026, 7, 20) and hi == date(2026, 7, 24)
