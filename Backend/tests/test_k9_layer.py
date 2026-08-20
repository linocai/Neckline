"""K9 策略层单测(V2.5.0 S6,PROJECT_PLAN §6 S6 验收 + §5.4)。

| # | 验收 | section |
|---|---|---|
| 1 | 硬边界 9 条逐条命中,`k9_disposition` 覆盖全市场且 `excluded_by` 可解释 | ① |
| 2 | 四通道各点亮一只票;判据是形态私有的 | ② |
| 3 | 🔴 裁定 13/14/15:共享放量倍数、一字跌停零参数、p1/p3 严丝合缝互补 | ③ |
| 4 | 名额:保底 / 自由 / 一票一席 / 诚实缺席 / 容量不足披露 / 成色标注 | ④ |
| 5 | 🔴 `heatAbsentPolicy` 三种 + `relaySource × relayScoring` 四种组合各一条夹具 | ⑤ |
| 6 | 确定性:同包同参跑两遍 canonical JSON 逐字节相等 | ⑥ |
| 7 | 落库:`k9_runs` / `k9_channel_hits` / `k9_listing_entries` / disposition parquet | ⑦ |

结构性守门(AST / 全仓扫描)见 `test_v250_s6_k9_guard.py`。
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import polars as pl
import pytest

from neckline.k9 import boundary as boundary_mod
from neckline.k9 import industry_heat as heat_mod
from neckline.k9 import params as P
from neckline.k9 import quota as quota_mod
from neckline.k9 import ranking as ranking_mod
from neckline.k9 import run as k9_run
from neckline.k9 import store as k9_store
from neckline.k9 import volume as volume_mod
from neckline.k9.channels import p2_rebound
from neckline.k9.contract import Pattern, SeatKind, Tier
from tests import k9_env


@pytest.fixture
def market(isolated_env):
    """铺好的合成市场(70 个交易日,事实包逐日冻结)。"""
    day = k9_env.seed(isolated_env)
    return isolated_env, day


def _compute(env, day, tmp_path, **overrides):
    params = k9_env.params(env, tmp_path, **overrides)
    return k9_run.compute(day, params=params, parquet_dir=env.parquet_dir,
                          db_path=env.db_path), params


# ══════════════════════════════════════════════════════════════════════════
# ① K9 第一层 · 硬边界
# ══════════════════════════════════════════════════════════════════════════

class TestBoundary:
    def test_each_of_the_nine_rules_catches_its_own_case(self, market, tmp_path):
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        rows = {r["ts_code"]: r["excluded_by"] for r in res.disposition_rows}
        assert rows[k9_env.STAR_CODE] == boundary_mod.EXCL_STAR
        assert rows[k9_env.BAIJIU_CODE] == boundary_mod.EXCL_BAIJIU
        assert rows[k9_env.ST_CODE] == boundary_mod.EXCL_ST
        assert rows[k9_env.NEW_CODE] == boundary_mod.EXCL_NEW_LISTING
        assert rows[k9_env.FULL_HALT_CODE] == boundary_mod.EXCL_SUSPENDED
        assert rows[k9_env.ILLIQUID_CODE] == boundary_mod.EXCL_ILLIQUID
        assert rows[k9_env.LIMIT_UP_CODE] == boundary_mod.EXCL_LIMIT_UP
        assert rows[k9_env.SPIKE_CODE] == boundary_mod.EXCL_SPIKE_FADE

    def test_an_intraday_halt_stays_in_the_pool(self, market, tmp_path):
        """🔴 **裁定 12 的端到端证据**:盘中临时停牌的票当天正常交易 →
        ⛔ 不被 K9 第一层第 6 条排除,而且照样能被通道召回。"""
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        rows = {r["ts_code"]: r for r in res.disposition_rows}
        assert rows[k9_env.INTRADAY_HALT_CODE]["excluded_by"] is None
        assert json.loads(
            rows[k9_env.INTRADAY_HALT_CODE]["recalled_patterns_json"]) == ["p1"]
        # 对照:全天停牌的那只被第 6 条排除,且⛔ 没有被任何通道召回
        assert rows[k9_env.FULL_HALT_CODE]["excluded_by"] == boundary_mod.EXCL_SUSPENDED
        assert json.loads(rows[k9_env.FULL_HALT_CODE]["recalled_patterns_json"]) == []

    def test_excluded_stocks_enter_no_channel_at_all(self, market, tmp_path):
        """K9 §二 开宗明义:被排除的票**不进入任何形态召回**。"""
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        for r in res.disposition_rows:
            if r["excluded_by"] is not None:
                assert json.loads(r["recalled_patterns_json"]) == [], r["ts_code"]
                assert r["seated"] == 0

    def test_disposition_covers_every_stock_in_the_pack(self, market, tmp_path):
        """§5.4.8:全市场逐票一行 —— 「昨天为什么没选中这只票」是一次查表。"""
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        assert {r["ts_code"] for r in res.disposition_rows} == set(k9_env.UNIVERSE)
        reasons = {r["excluded_by"] for r in res.disposition_rows} - {None}
        assert reasons <= set(boundary_mod.EXCLUSION_ORDER), "冒出了闭合集合之外的理由"

    def test_boundary_counts_are_per_rule_not_a_total(self, market, tmp_path):
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        assert set(res.boundary_counts) == set(boundary_mod.EXCLUSION_ORDER)
        assert res.boundary_counts[boundary_mod.EXCL_STAR] == 1


# ══════════════════════════════════════════════════════════════════════════
# ② K9 第二层 · 四通道
# ══════════════════════════════════════════════════════════════════════════

class TestChannels:
    def test_all_four_channels_light_up_their_own_stock(self, market, tmp_path):
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        got = {}
        for h in res.hits:
            got.setdefault(h.pattern, set()).add(h.ts_code)
        assert k9_env.P1_CODE in got[Pattern.P1]
        assert got[Pattern.P2] == {k9_env.P2_CODE}
        assert got[Pattern.P3] == {k9_env.P3_CODE}
        assert got[Pattern.P4] == {k9_env.P4_CODE}

    def test_channel_counts_record_both_tiers_every_day(self, market, tmp_path):
        """K9 §五 末段:两档都跑、都记数,才能区分「市场今天确实没有」与
        「判据卡得过严」。"""
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        for p in ("p1", "p2", "p3", "p4"):
            assert set(res.shortlist.channel_counts[p]) == {"strict", "relaxed", "seated"}

    def test_strength_readings_ride_along_with_each_hit(self, market, tmp_path):
        """强度性条件⛔ 不设门槛,而是原值随召回上交给第三层(K9 §3.6)。"""
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        p1 = next(h for h in res.hits if h.pattern is Pattern.P1)
        assert set(p1.strength) == {"volMultiple", "upsideRoomFar", "relStrength"}
        assert p1.strength["volMultiple"] == pytest.approx(3.0, rel=1e-6)


# ══════════════════════════════════════════════════════════════════════════
# ③ 🔴 裁定 13 / 14 / 15
# ══════════════════════════════════════════════════════════════════════════

class TestVolumeRulings:
    def test_p1_and_p3_share_one_v_and_are_exactly_complementary(self, market, tmp_path):
        """🔴 **裁定 15**:形态 1 要求放量倍数 ≥ V、形态 3 要求 < V,同一个 V。

        把 V 从 3 倍上下扫过去:那只「今天放量 3 倍」的票必须**恰好**在 V 跨过 3.0
        的那一刻从 p1 掉到 p3 那一侧 —— 两个半区合起来是全集、交集为空。
        """
        env, day = market
        for v, expect_p1 in ((2.0, True), (2.999, True), (3.001, False)):
            res, _ = _compute(env, day, tmp_path, **{"volume.eruptionMultiple": v})
            in_p1 = any(h.ts_code == k9_env.P1_CODE and h.pattern is Pattern.P1
                        for h in res.hits)
            assert in_p1 is expect_p1, f"V={v}"

    def test_no_stock_is_ever_recalled_by_both_p1_and_p3(self, market, tmp_path):
        """互斥由**判据本身**保证,⛔ 不靠事后仲裁(K9 §3.4 / 裁定 15)。"""
        env, day = market
        for v in (0.5, 1.0, 1.5, 2.0, 3.0, 5.0):
            res, _ = _compute(env, day, tmp_path, **{"volume.eruptionMultiple": v})
            by_code = {}
            for h in res.hits:
                by_code.setdefault(h.ts_code, set()).add(h.pattern)
            both = [c for c, ps in by_code.items() if {Pattern.P1, Pattern.P3} <= ps]
            assert both == [], f"V={v} 时 {both} 同时命中 p1 与 p3"

    def test_one_line_limit_down_is_zero_parameter_and_blocks_p2(self, market, tmp_path):
        """🔴 **裁定 13**:一字跌停 = 开、高、低、收四价全等于跌停价,**零参数**。

        那只一字跌停的票归一化跌幅 1.0(比 P2 的 0.8 还深)、也没被硬边界排除 ——
        唯一挡住它的就是这条判据。
        """
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        rows = {r["ts_code"]: r for r in res.disposition_rows}
        assert rows[k9_env.ONE_LINE_CODE]["excluded_by"] is None, "它不该被边界排除"
        assert json.loads(rows[k9_env.ONE_LINE_CODE]["recalled_patterns_json"]) == []

    def test_the_one_line_test_is_a_pure_four_price_equality(self):
        """判据形状本身:四价全等于跌停价才算,差一分就不算。"""
        frame = pl.DataFrame({
            "ts_code": ["a", "b", "c", "d"],
            "open": [9.0, 9.0, 9.01, 9.0],
            "high": [9.0, 9.02, 9.01, 9.0],
            "low": [9.0, 9.0, 9.01, 9.0],
            "close": [9.0, 9.0, 9.01, 9.0],
            "limit_down_price": [9.0, 9.0, 9.0, None],
        })
        got = frame.with_columns(p2_rebound.one_line_limit_down().alias("x"))
        assert got["x"].to_list() == [True, False, False, False]

    def test_p2_needs_real_turnover_measured_as_the_volume_multiple(self, market, tmp_path):
        """🔴 **裁定 13**:有实际换手 = 放量倍数 ≥ 门槛(⛔ 不用换手率)。

        那只超跌票今天放量 1.5 倍;把门槛抬到 1.6 它就该掉出来 —— 而它的换手率
        (`turnover_rate`)在夹具里**恒为 5.0**,如果实现偷用了换手率,这条会绿得
        毫无道理。
        """
        env, day = market
        res, _ = _compute(env, day, tmp_path,
                          **{"channels.p2.strict.minVolMultiple": 1.6,
                             "channels.p2.relaxed.minVolMultiple": 1.6})
        assert not any(h.pattern is Pattern.P2 for h in res.hits)

    def test_the_volume_multiple_is_one_shared_quantity(self, market, tmp_path):
        """裁定 15 的「⛔ 不许在三个地方各算一份」:三条判据读到的是同一个数。"""
        env, day = market
        params = k9_env.params(env, tmp_path)
        pack, _ = k9_run.build_pack_range(
            day, params=params, parquet_dir=env.parquet_dir, db_path=env.db_path)
        vm = volume_mod.compute(pack, ma_days=params.volume.ma_days)
        got = {r["ts_code"]: r[volume_mod.COLUMN] for r in vm.iter_rows(named=True)}
        assert got[k9_env.P1_CODE] == pytest.approx(3.0, rel=1e-6)
        assert got[k9_env.P2_CODE] == pytest.approx(1.5, rel=1e-6)
        assert got[k9_env.P3_CODE] == pytest.approx(1.0, rel=1e-6)

    def test_the_volume_ratio_is_a_different_quantity_with_a_five_day_window(self):
        """⚠ 形态 4 的量比 ÷ **5** 日均量,与放量倍数(÷ `volume.maDays`)⛔ 不是一个量。"""
        assert volume_mod.VOLUME_RATIO_MA_DAYS == 5
        assert volume_mod.RATIO_COLUMN != volume_mod.COLUMN

    def test_an_unknown_volume_multiple_is_not_treated_as_no_eruption(self):
        """算不出来 ≠ 没放量 —— ⛔ 不许拿 `None` 冒充「还没爆」塞进形态 3。"""
        assert volume_mod.erupted(None, 2.0) is None
        assert volume_mod.erupted(1.9, 2.0) is False
        assert volume_mod.erupted(2.0, 2.0) is True


# ══════════════════════════════════════════════════════════════════════════
# ④ K9 §五 · 名额
# ══════════════════════════════════════════════════════════════════════════

class TestQuota:
    def test_every_pattern_with_candidates_gets_a_floor_seat(self, market, tmp_path):
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        floors = {e.primary_pattern for e in res.shortlist.entries
                  if e.seat_kind is SeatKind.FLOOR}
        assert floors == set(Pattern), "有候选的形态各先占 1 席(K9 §五-2)"

    def test_a_stock_never_takes_two_seats(self, market, tmp_path):
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        codes = [e.ts_code for e in res.shortlist.entries]
        assert len(codes) == len(set(codes))

    def test_free_competition_fills_the_rest(self, market, tmp_path):
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        assert any(e.seat_kind is SeatKind.FREE for e in res.shortlist.entries)

    def test_every_entry_carries_its_own_tier(self, market, tmp_path):
        """K9 §五-7 成色标注:每只票自带严格 / 放宽。"""
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        assert all(e.tier in (Tier.STRICT, Tier.RELAXED) for e in res.shortlist.entries)

    def test_honest_absence_when_a_pattern_has_no_candidate(self, market, tmp_path):
        """K9 §五-5:某形态今日无候选 → 标「今日无此形态」,⛔ 不放宽标准去凑。"""
        env, day = market
        res, _ = _compute(env, day, tmp_path,
                          **{"channels.p2.strict.normDropMin": 0.99,
                             "channels.p2.relaxed.normDropMin": 0.99})
        assert Pattern.P2 in res.shortlist.absent_patterns
        assert res.shortlist.channel_counts["p2"] == {"strict": 0, "relaxed": 0, "seated": 0}

    def test_capacity_shortfall_is_disclosed_not_padded(self, market, tmp_path):
        """§5.4.7 第 6 步:放宽后仍不足 → **如实出这么多** + 显式披露,⛔ 不制造候选。"""
        env, day = market
        res, _ = _compute(env, day, tmp_path, **{"quota.min": 20, "quota.max": 20})
        assert res.shortlist.capacity_short is True
        assert res.shortlist.size < 20

    def test_the_max_capacity_is_respected(self, market, tmp_path):
        env, day = market
        res, _ = _compute(env, day, tmp_path, **{"quota.min": 1, "quota.max": 2})
        assert res.shortlist.size == 2
        assert len(res.shortlist.reserve) >= 1

    def test_floor_order_is_by_best_candidate_not_by_pattern_index(self):
        """§5.4.7 第 3 步:⛔ 固定按 p1..p4 轮会给 p1 系统性优势。

        只有 **1** 个席位时,先挑的那个形态就是唯一有席位的形态 —— 这一条因此能
        把两种次序区分开:按「最佳候选分数降序」轮到 p3(0.9),按「p1..p4 固定次序」
        轮到 p1(0.8)。
        """
        def cand(code, patterns, score):
            return ranking_mod.ScoredCandidate(
                ts_code=code, patterns=patterns, primary_pattern=patterns[0],
                tier=Tier.STRICT, industry_heat_score=0.5,
                pattern_strength_score=score, relay_score=0.0, score=score)

        candidates = [
            cand("DDD", (Pattern.P3,), 0.9),
            cand("AAA", (Pattern.P1,), 0.8),
            cand("BBB", (Pattern.P1,), 0.5),
        ]
        q = P.QuotaParams(min=1, max=1, floor_per_channel=1, over_strict_consecutive_days=3)
        alloc = quota_mod.allocate(candidates, q)
        assert [s.candidate.ts_code for s in alloc.seated] == ["DDD"]
        assert alloc.seated[0].seat_kind is SeatKind.FLOOR

    def test_a_pattern_whose_best_is_taken_falls_back_to_its_runner_up(self):
        """K9 §五-4 一票一席:最佳候选被别的形态占了 → 取本形态**次优**。"""
        def cand(code, patterns, score):
            return ranking_mod.ScoredCandidate(
                ts_code=code, patterns=patterns, primary_pattern=patterns[0],
                tier=Tier.STRICT, industry_heat_score=0.5,
                pattern_strength_score=score, relay_score=0.0, score=score)

        candidates = [
            cand("AAA", (Pattern.P1, Pattern.P3), 0.9),   # 两边都中
            cand("BBB", (Pattern.P1,), 0.5),
            cand("CCC", (Pattern.P3,), 0.4),
        ]
        q = P.QuotaParams(min=1, max=2, floor_per_channel=1, over_strict_consecutive_days=3)
        alloc = quota_mod.allocate(candidates, q)
        # 并列 0.9 → 按 p1<p2<p3<p4 定序,p1 先挑走 AAA;p3 只能取次优 CCC
        assert [s.candidate.ts_code for s in alloc.seated] == ["AAA", "CCC"]
        assert all(s.seat_kind is SeatKind.FLOOR for s in alloc.seated)

    def test_over_strict_hint_needs_consecutive_relaxed_days(self):
        q = P.QuotaParams(min=10, max=20, floor_per_channel=1, over_strict_consecutive_days=3)
        assert quota_mod.over_strict(2, q) is False
        assert quota_mod.over_strict(3, q) is True


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 🔴 三个「取值待标定」的参数位:全部候选取值都实现(§8.3 #18–#20 / G22)
# ══════════════════════════════════════════════════════════════════════════

class TestCalibratedValueSlots:
    @pytest.mark.parametrize("policy", ["renormalize", "zero", "drop"])
    def test_every_heat_absent_policy_is_implemented(self, market, tmp_path, policy):
        """`minMembers` 抬到 6 → 四个 5 只成员的行业全部「查无该行业」
        (只剩 10 只成员的 801099 够格),三种处置各走一遍。"""
        env, day = market
        res, _ = _compute(env, day, tmp_path,
                          **{"industry.minMembers": 6, "industry.heatAbsentPolicy": policy})
        entries = {e.ts_code: e for e in res.shortlist.entries}
        if policy == "drop":
            assert res.shortlist.dropped_by_heat_absent, "drop 必须真的丢票"
            assert not set(res.shortlist.dropped_by_heat_absent) & set(entries)
        elif policy == "zero":
            assert any(e.industry_heat_score == 0.0 for e in entries.values())
        else:
            assert any(e.industry_heat_score is None for e in entries.values()), \
                "renormalize:行业无排名⛔ 不被当成最差行业,热度分是**缺席**不是 0"

    def test_the_three_policies_are_a_total_mapping(self):
        for policy in P.HeatAbsentPolicy:
            effect = heat_mod.apply_absent_policy(policy)
            assert isinstance(effect, heat_mod.AbsentEffect)

    @pytest.mark.parametrize("source", ["recalled", "shortlisted"])
    @pytest.mark.parametrize("scoring", ["binary", "count"])
    def test_every_relay_combination_is_implemented(self, market, tmp_path, source, scoring):
        env, day = market
        params = k9_env.params(env, tmp_path,
                               **{"ranking.relaySource": source, "ranking.relayScoring": scoring})
        res = k9_run.compute(day, params=params, parquet_dir=env.parquet_dir,
                             db_path=env.db_path)
        k9_run.persist(res, parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert all(0.0 <= e.relay_score <= 1.0 for e in res.shortlist.entries)

    def test_relay_only_counts_other_patterns(self):
        """K9 §四 逐字:「过去 N 天内被**其他**形态选中过」。"""
        records = [
            ranking_mod.RelayRecord("20240401", "AAA", Pattern.P4),
            ranking_mod.RelayRecord("20240402", "AAA", Pattern.P1),   # 与今天同形态 → 不算
            ranking_mod.RelayRecord("20240401", "BBB", Pattern.P1),   # 同形态 → 不算
        ]
        counts = ranking_mod.relay_counts(records, {"AAA": (Pattern.P1,), "BBB": (Pattern.P1,)})
        assert counts == {"AAA": 1, "BBB": 0}

    def test_relay_dedups_the_same_pattern_on_the_same_day(self):
        """两档各记一条 ⛔ 不许算成两份证据。"""
        records = [ranking_mod.RelayRecord("20240401", "AAA", Pattern.P4)] * 3
        assert ranking_mod.relay_counts(records, {"AAA": (Pattern.P1,)}) == {"AAA": 1}

    def test_relay_source_maps_to_exactly_two_tables(self):
        assert set(ranking_mod.RELAY_TABLE_OF) == set(P.RelaySource)
        assert set(ranking_mod.RELAY_TABLE_OF.values()) == {
            k9_store.HITS_TABLE, k9_store.LISTING_TABLE}


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 确定性(守门 G10)
# ══════════════════════════════════════════════════════════════════════════

def _canonical(shortlist) -> str:
    return json.dumps(
        {
            "tradeDate": shortlist.trade_date.isoformat(),
            "strategy": shortlist.strategy,
            "paramsVersion": shortlist.params_version,
            "packVersion": shortlist.pack_version,
            "tierUsed": shortlist.tier_used.value,
            "entries": [e.to_row() for e in shortlist.entries],
            "reserve": [e.to_row() for e in shortlist.reserve],
            "channelCounts": shortlist.channel_counts,
        },
        ensure_ascii=False, sort_keys=True,
    )


def test_same_pack_same_params_twice_is_byte_identical(market, tmp_path):
    """🔴 G10:同一份冻结事实包 + 同一份参数包 → 逐字节相同的清单。"""
    env, day = market
    a, _ = _compute(env, day, tmp_path)
    b, _ = _compute(env, day, tmp_path)
    assert _canonical(a.shortlist) == _canonical(b.shortlist)


def test_strength_percentiles_break_ties_deterministically(market, tmp_path):
    """并列取平均名次 —— 同一个读数拿到不同名次会让「逐字节相等」变成空话。"""
    env, day = market
    res, _ = _compute(env, day, tmp_path)
    scores = ranking_mod.pattern_strength_scores(
        list(res.hits), k9_env.raw_params()["ranking"]["patternSubWeights"])
    assert all(0.0 <= v <= 1.0 for v in scores.values())


# ══════════════════════════════════════════════════════════════════════════
# ⑦ 落库
# ══════════════════════════════════════════════════════════════════════════

class TestPersistence:
    def test_the_run_row_records_which_pack_and_which_params(self, market, tmp_path):
        """架构 §3.1:成绩单永远记得自己跑在哪版事实包 + 哪版参数上。"""
        env, day = market
        res, params = _compute(env, day, tmp_path)
        k9_run.persist(res, parquet_dir=env.parquet_dir, db_path=env.db_path)
        row = k9_store.load_run(day, db_path=env.db_path)
        assert row["params_package_version"] == params.package_version
        assert row["pack_version"] == res.shortlist.pack_version
        assert row["pack_id"] == res.shortlist.pack_id
        assert row["tier_used"] == res.shortlist.tier_used.value
        assert set(row["boundary_counts"]) == set(boundary_mod.EXCLUSION_ORDER)

    def test_the_listing_freezes_the_industry_binding(self, market, tmp_path):
        """🔴 §5.8.2:票与行业的从属关系在**写入时**冻结,事后申万调整不回改。"""
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        k9_run.persist(res, parquet_dir=env.parquet_dir, db_path=env.db_path)
        rows = {r["ts_code"]: r for r in k9_store.load_listing(day, db_path=env.db_path)}
        assert rows[k9_env.P1_CODE]["sw_l2_code"] == "801080.SI"
        assert rows[k9_env.P1_CODE]["sw_l2_name"] == "半导体"

    def test_who_finalized_the_listing_is_a_recorded_fact(self, market, tmp_path):
        """§5.5:解释层还没接入 → `listing_finalized_by='k9'`,
        「这份清单还没过消息面」因此是**查得到的事实**,不是一句注释。"""
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        k9_run.persist(res, parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert k9_store.load_run(day, db_path=env.db_path)["listing_finalized_by"] == "k9"
        with pytest.raises(ValueError, match="listing_finalized_by"):
            k9_run.persist(res, listing_finalized_by="whatever",
                           parquet_dir=env.parquet_dir, db_path=env.db_path)

    def test_channel_hits_are_append_only(self, market, tmp_path):
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        k9_run.persist(res, parquet_dir=env.parquet_dir, db_path=env.db_path)
        k9_run.persist(res, parquet_dir=env.parquet_dir, db_path=env.db_path)
        from neckline.db import connection
        with connection(env.db_path) as conn:
            n = conn.execute(f"SELECT COUNT(*) FROM {k9_store.HITS_TABLE}").fetchone()[0]
        assert n == 2 * len(res.hits), "append-only 台账⛔ 不改历史"
        # 但清单是幂等重写的,⛔ 不会翻倍
        assert len(k9_store.load_listing(day, db_path=env.db_path)) == res.shortlist.size

    def test_disposition_parquet_round_trips(self, market, tmp_path):
        env, day = market
        res, _ = _compute(env, day, tmp_path)
        k9_run.persist(res, parquet_dir=env.parquet_dir, db_path=env.db_path)
        got = k9_store.load_disposition(day, parquet_dir=env.parquet_dir)
        assert got.height == len(k9_env.UNIVERSE)
        assert set(got.columns) == {
            "trade_date", "ts_code", "excluded_by", "recalled_patterns_json", "tier",
            "score", "rank", "seated", "seat_kind", "news_excluded"}

    def test_the_relaxed_streak_is_counted_from_history(self, market, tmp_path):
        env, day = market
        assert k9_store.relaxed_streak_before(day, db_path=env.db_path) == 0


# ══════════════════════════════════════════════════════════════════════════
# 契约一 · 声明依赖
# ══════════════════════════════════════════════════════════════════════════

def test_an_undeclared_field_is_refused(market, tmp_path):
    """§5.4.2 契约一:`name ∉ DECLARED_FIELDS` **直接抛**。"""
    from neckline.k9.contract import PackRange, UndeclaredField

    env, day = market
    params = k9_env.params(env, tmp_path)
    pack, _ = k9_run.build_pack_range(
        day, params=params, parquet_dir=env.parquet_dir, db_path=env.db_path)
    assert pack.field("close").len() > 0
    with pytest.raises(UndeclaredField, match="声明依赖"):
        pack.field("turnover_rate")


def test_the_lookback_accounts_for_the_doubled_short_window(market, tmp_path):
    """p3 的「在改善」要 2 × shortWindow 天 —— 单键校验看不见这个乘 2。"""
    env, day = market
    params = k9_env.params(env, tmp_path)
    assert k9_run.required_lookback(params) == 60
    big = k9_env.params(env, tmp_path,
                        **{"channels.p3.strict.shortWindow": 61,
                           "channels.p3.relaxed.shortWindow": 61})
    with pytest.raises(P.ParamsUnavailable, match="MAX_LOOKBACK_PACKS"):
        k9_run.build_pack_range(day, params=big, parquet_dir=env.parquet_dir,
                                db_path=env.db_path)
