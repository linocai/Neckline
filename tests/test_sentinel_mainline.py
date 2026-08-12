"""V2-⑧-F/⑧-G 退潮「主线板块跳水」样本机械化 + 配额切片(PROJECT_PLAN §五 ⑧-F/⑧-G,
2026-08-03 planner 两次裁定,🔴 碰纪律触发器)。

**⑧-F 的核心判据(继续锁死)**:「换掉 LLM 的成员选择 → 样本**逐位不变**」。两条路
各证一次:①函数级 —— `derive_mainline_sample` 的签名里**根本没有**篮子成员这个入口;
②整拍级 —— 同一天只换 D0 篮子成员,`retreat_metrics` 里落的样本构成逐位相同。

**⑧-G 新增四条**:①采样键是 `crc32(ts_code)` 且与板块无关(⛔ 不是 ts_code 升序);
②估计量 = 每条主线一票(per-seed),pooled 只作审计对照;③关注池「有界必需项全进 +
两份测量样本各有保底」,极端涨停日一个保底都不少、且**篮子成员数量挪不动任一测量
样本**;④`MIN_MAINLINE_SAMPLE` 生效且是**同源 import**(单测 + AST 守门)。

另锁死三条不变量:`sector_dive` 两个阈值一字未动;`breadth_cap` 一字未动;样本不足
→ 不触发 + 如实披露。

🔴 **V2.4.0 P0 换血(施工纪律 4:旧断言必须写明被谁取代,⛔ 不删测试换绿)**

`sentinel/mainline.py` **文件保留**(行为基准 + 回滚绳,§3.14-A),故**纯函数级用例
一条不动**;被删的只有「经 `run_tick` / `load_watch_universe` 走整拍」的那一批 ——
它们断言的是**已被撤销判断权**的行为,P0.1 表「代理关注池 →『大盘退潮』= 删」。
逐条取代关系写在各处 `⛔ V2.4.0 P0` 注释里。

⚠ **被取代 ≠ 判据作废**:「样本即判据 → 样本组成不得沾 LLM」这条纪律由**仍然活着**的
`test_guard_signature_has_no_basket_entrance`(签名里没有篮子入口)继续锁死,
比原先那条"整拍级逐位相同"更强:**切片压根读不到篮子表**。
"""

from __future__ import annotations

import ast
import inspect
from collections import Counter
from datetime import date, time
from pathlib import Path

import pytest

from neckline.data.board import Board, classify_by_code
from neckline.scan.seeds import (
    ANOMALY_CLUSTER,
    HOT_INDUSTRY,
    LIMIT_CLUSTER,
    SURGING_CONCEPT,
    DriverSeed,
    SeedSet,
)
from neckline.sentinel import mainline, retreat, universe

pytestmark = pytest.mark.usefixtures("isolated_env")

D0 = date(2026, 7, 23)
_MAINLINE_SRC = Path(__file__).resolve().parent.parent / "neckline" / "sentinel" / "mainline.py"


def _seed_set(*, hot=(), concept=(), limit=(), anomaly=()):
    def mk(kind, i, codes):
        return DriverSeed(seed_key=f"{kind}{i}", seed_kind=kind, label=f"{kind}{i}",
                          member_codes=tuple(codes))
    return SeedSet(
        trade_date=D0.strftime("%Y%m%d"), pack_version="K4-pack-v1",
        hot_industry=tuple(mk(HOT_INDUSTRY, i, c) for i, c in enumerate(hot)),
        surging_concept=tuple(mk(SURGING_CONCEPT, i, c) for i, c in enumerate(concept)),
        limit_cluster=tuple(mk(LIMIT_CLUSTER, i, c) for i, c in enumerate(limit)),
        anomaly_cluster=tuple(mk(ANOMALY_CLUSTER, i, c) for i, c in enumerate(anomaly)),
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    mainline.reset_seed_cache()
    yield
    mainline.reset_seed_cache()


def _patch_seeds(monkeypatch, seed_set):
    calls = []

    def fake(trade_date, **kw):
        calls.append(trade_date)
        return seed_set

    monkeypatch.setattr("neckline.scan.seeds.generate_seeds", fake)
    return calls


# ══════════════════════════════════════════════════════════════════════════
# ⑧-G-B 采样键:crc32,与板块无关,逐位可复现
# ══════════════════════════════════════════════════════════════════════════

class TestCrc32SamplingKey:
    def test_slice_is_top_k_by_crc32(self, monkeypatch):
        members = tuple(f"6000{i:02d}.SH" for i in range(20))
        _patch_seeds(monkeypatch, _seed_set(hot=[members]))
        s = mainline.derive_mainline_sample(D0)
        expect = tuple(sorted(sorted(members, key=mainline.crc_rank)[:4]))
        assert s.codes == expect
        assert len(s.codes) == mainline.MAINLINE_SAMPLE_PER_SEED

    def test_two_runs_are_bit_identical_regardless_of_input_order(self, monkeypatch):
        """跨进程可复现的前提是**全序且不吃行序**(CLAUDE.md:要复现的分组一律
        `crc32`,禁内置 `hash()` —— 后者带进程盐,`PYTHONHASHSEED` 一变分组就漂)。"""
        members = [f"3000{i:02d}.SZ" for i in range(30)]
        _patch_seeds(monkeypatch, _seed_set(hot=[members]))
        a = mainline.derive_mainline_sample(D0).codes
        mainline.reset_seed_cache()
        _patch_seeds(monkeypatch, _seed_set(hot=[list(reversed(members))]))
        b = mainline.derive_mainline_sample(D0).codes
        assert a == b

    def test_sampling_is_not_concentrated_on_one_board(self):
        """⑧-G-B 的**要害**:`ts_code` 升序在一颗行业种子内部等于**按板块排序**
        (深主板 000 / 创业板 300 排在沪主板 600 / 科创 688 前面),而板块与涨跌停
        幅度(10% vs 20%)、波动率直接相关 → 样本的波动率画像被系统性扭曲。
        `crc32` 与板块无关,四个板块应大体均分。"""
        boards = [("000", ".SZ"), ("300", ".SZ"), ("600", ".SH"), ("688", ".SH")]
        seeds = [
            [f"{p}{s * 10 + i:03d}{ex}" for p, ex in boards for i in range(10)]
            for s in range(100)
        ]
        crc_hits = Counter()
        code_hits = Counter()
        for members in seeds:
            for c in mainline.seed_slice_codes(tuple(members)):
                crc_hits[classify_by_code(c)] += 1
            for c in sorted(members)[:mainline.MAINLINE_SAMPLE_PER_SEED]:
                code_hits[classify_by_code(c)] += 1
        total = sum(crc_hits.values())
        assert total == 100 * mainline.MAINLINE_SAMPLE_PER_SEED
        # crc32:四板块都拿得到相当份额(理论各 25%,给足抽样带宽)
        for board in (Board.MAIN, Board.GEM, Board.STAR):
            assert crc_hits[board] >= total * 0.12, f"{board} 只占 {crc_hits[board]}/{total}"
        # 反证:ts_code 升序会把 100% 的名额给同一个板块(深主板 000xxx)
        assert code_hits[Board.MAIN] == total and len(code_hits) == 1

    def test_k_is_the_engine_constant_four(self):
        """⑧-G-B:**K 是精度旋钮不是策略参数**,⛔ 不许通过调 K 去凑触发频率
        (那等于偷偷改阈值)。守住这个数,改它必须先改这条断言、先读 plan。"""
        assert mainline.MAINLINE_SAMPLE_PER_SEED == 4


# ══════════════════════════════════════════════════════════════════════════
# ⑧-G-C 估计量:每条主线一票
# ══════════════════════════════════════════════════════════════════════════

class TestPerSeedEstimator:
    def _sample(self, monkeypatch, big, small):
        _patch_seeds(monkeypatch, _seed_set(hot=[big], concept=[small]))
        return mainline.derive_mainline_sample(D0)

    def test_one_vote_per_mainline_regardless_of_member_count(self, monkeypatch):
        """一颗 200 成员的种子与一颗 8 成员的种子各代表**一条主线**,不该因成员多
        就占更大权重 —— 而 pooled 口径会。两个口径都断言到(⑧-G-C 原文)。"""
        big = [f"6001{i:02d}.SH" for i in range(200)]
        small = [f"3001{i:02d}.SZ" for i in range(8)]
        s = self._sample(monkeypatch, big, small)
        assert s.seed_count == 2
        # 大种子切片全线 −6%,小种子切片全线 0%(K=4 → 两边各 4 只,人为让 pooled
        # 与 per-seed 分道扬镳:再给大种子多一只有报价的票)
        big_codes = [c for c in s.slices[0].codes]
        small_codes = [c for c in s.slices[1].codes]
        returns = {c: -0.06 for c in big_codes}
        returns.update({c: 0.0 for c in small_codes[:2]})     # 小种子只有 2 只有报价
        est = mainline.estimate(s, returns)
        assert est.seeds_with_data == 2 and est.quoted == len(big_codes) + 2
        assert est.per_seed_avg == pytest.approx((-0.06 + 0.0) / 2)      # 每条主线一票
        assert est.pooled_avg == pytest.approx(-0.06 * 4 / 6)            # 混池:大种子占 4/6
        assert est.per_seed_avg != pytest.approx(est.pooled_avg)

    def test_seed_without_any_quote_does_not_vote(self, monkeypatch):
        """没有一只有报价的种子**不投票**(不是投 0%)—— 缺数据不判、不猜。"""
        s = self._sample(monkeypatch, [f"6002{i:02d}.SH" for i in range(10)], ["300999.SZ"])
        returns = {c: -0.05 for c in s.slices[0].codes}
        est = mainline.estimate(s, returns)
        assert est.seeds_with_data == 1
        assert est.per_seed_avg == pytest.approx(-0.05)

    def test_empty_sample_estimates_to_none_not_zero(self):
        est = mainline.estimate(mainline.MainlineSample(), {})
        assert est.per_seed_avg is None and est.pooled_avg is None and est.quoted == 0

    def test_both_estimators_are_recorded_for_audit(self, monkeypatch):
        """两个口径逐拍落留痕 —— 讲不同话时要能一眼看出来,而不是事后重算。"""
        s = self._sample(monkeypatch, [f"6003{i:02d}.SH" for i in range(10)],
                         [f"3003{i:02d}.SZ" for i in range(10)])
        returns = {c: -0.02 for c in s.slices[0].codes}
        returns.update({c: -0.08 for c in s.slices[1].codes[:1]})
        payload = mainline.estimate(s, returns).payload()
        assert set(payload) >= {"per_seed_avg", "pooled_avg", "quoted", "seeds_with_data"}
        assert payload["per_seed_avg"] == pytest.approx(-0.05)
        assert payload["pooled_avg"] == pytest.approx((-0.02 * 4 + -0.08) / 5)


# ══════════════════════════════════════════════════════════════════════════
# ⑧-G-E 最小样本量:同源引用 + 真的生效
# ══════════════════════════════════════════════════════════════════════════

class TestMinMainlineSample:
    def test_value_is_the_same_object_as_industry_strength_min_members(self):
        from neckline.report.industry_strength import _MIN_MEMBERS

        assert mainline.MIN_MAINLINE_SAMPLE == _MIN_MEMBERS == 5

    def test_ast_guard_is_an_import_reference_not_a_literal(self):
        """⑧-G-E 原文:**⛔ 不抄字面量**(照 ⑤-c `MIN_LIFT_SAMPLE_SIZE` 同源引用
        体例)。AST 守门:赋值右侧必须是个名字(import 进来的别名),不是数字;
        且 `_MIN_MEMBERS` 确实从 `report.industry_strength` import 过来。"""
        tree = ast.parse(_MAINLINE_SRC.read_text(encoding="utf-8"))
        rhs = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "MIN_MAINLINE_SAMPLE" for t in node.targets)
        ]
        assert len(rhs) == 1, "MIN_MAINLINE_SAMPLE 应当只有一处模块级赋值"
        assert isinstance(rhs[0], ast.Name), "⛔ 不许写成数字字面量,必须是同源 import 的引用"
        imported = [
            (node.module, a.name, a.asname)
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for a in node.names
        ]
        assert ("neckline.report.industry_strength", "_MIN_MEMBERS", rhs[0].id) in imported

    def test_below_min_does_not_trigger_even_on_a_crash(self):
        """样本 4 只(< 5)时,哪怕全线 −5%,主线跳水一路**不判**。

        ⚠ **原用例走的是整拍级**(`run_tick` → 读 `retreat_metrics` 留痕)。V2.4.0 P0
        撤销了退潮判级、`retreat_metrics` 停写,整拍级已无从断言 —— **改成纯函数级**
        直接问 `retreat.evaluate_retreat`(该模块保留作行为基准)。
        **被验的不变量一字未改**:样本不足下限 → 主线跳水不进 `triggered`。
        """
        empty = retreat.MarketBreadthSnapshot(
            trade_date=D0, sample_size=0, limit_up_count=0, limit_down_count=0,
            zaban_count=0, zaban_rate=0.0)
        below = retreat.evaluate_retreat(
            empty, now_time=time(10, 30), same_time_zaban_baseline=None,
            hot_sector_avg_chg=-0.05, hot_sector_sample=mainline.MIN_MAINLINE_SAMPLE - 1,
            prev_tick_triggered=[], allow_red=True,
        )
        assert retreat.COND_SECTOR_DIVE not in below.triggered
        # 反向:样本恰好到下限 → 同一个读数就该判(证明上面不是"永远不判")
        at_min = retreat.evaluate_retreat(
            empty, now_time=time(10, 30), same_time_zaban_baseline=None,
            hot_sector_avg_chg=-0.05, hot_sector_sample=mainline.MIN_MAINLINE_SAMPLE,
            prev_tick_triggered=[], allow_red=True,
        )
        assert retreat.COND_SECTOR_DIVE in at_min.triggered


# ══════════════════════════════════════════════════════════════════════════
# ⑧-G-D 池位配额 —— ⛔ **整节已于 V2.4.0 P0 退役**
# ══════════════════════════════════════════════════════════════════════════
#
# **取代关系(施工纪律 4)**:原 `TestPoolQuota`(5 例)与
# `TestBreadthExtraSampleTraceability`(2 例)断言的是「剩余池位如何在主线切片与
# 昨日涨停宽度样本之间按保底分配」「压缩后的需求量 vs 实际量如何落
# `retreat_metrics.breadth_extra_sample_json`」—— **这两份样本与那张表的写入
# 都已被 P0.1 表「代理关注池 →『大盘退潮』= 删」整体撤销**,配额函数
# (`_measurement_budget` / `_mainline_quota`)与留痕字段一并删除,断言对象不存在了。
#
# **没有随之作废、且仍被守住的**:
#   · `breadth_cap` 一字不动 —— 下面 `TestBreadthCapUnchanged` 接手;
#   · 「样本即判据 → 样本组成不得沾 LLM」—— 由
#     `TestDerive::test_guard_signature_has_no_basket_entrance` 继续锁死(更强:
#     `derive_mainline_sample` 的签名里根本没有篮子入口);
#   · `sector_dive` 两个阈值不动 —— `test_guard_sector_dive_thresholds_unchanged`。


class TestBreadthCapUnchanged:
    def test_breadth_cap_is_unchanged(self):
        """⑧-G-D:⛔ 不许自行抬 `breadth_cap`(会改盘中轮询量与限流风险面)。

        ⚠ 原断言还有第二句 `MAINLINE_SLICE_QUOTA_FLOOR + PREV_LIMIT_UP_QUOTA_FLOOR
        + MANDATORY_POOL_RESERVE == 200`(三个配额常量正好把池分完)。那三个常量已随
        两份测量样本删除,该恒等式**被 P0.1「代理关注池 →『大盘退潮』= 删」取代**;
        这里改为正面断言它们**确实不在了**,防日后有人"顺手"把配额机器接回来。
        """
        assert universe.DEFAULT_BREADTH_CAP == 200
        for gone in ("MANDATORY_POOL_RESERVE", "MAINLINE_SLICE_QUOTA_FLOOR",
                     "PREV_LIMIT_UP_QUOTA_FLOOR", "_measurement_budget", "_mainline_quota"):
            assert not hasattr(universe, gone), gone


# ══════════════════════════════════════════════════════════════════════════
# 派生本身(纯函数级;⑧-F 立的守门 + ⑧-G 口径)
# ══════════════════════════════════════════════════════════════════════════

class TestDerive:
    def test_only_hot_industry_and_surging_concept_feed_the_sample(self, monkeypatch):
        """⛔ 涨停簇 / 异动簇不是「板块」,拿它们凑样本会把"主线板块跳水"的意思改掉。"""
        _patch_seeds(monkeypatch, _seed_set(
            hot=[["A.SH"]], concept=[["B.SH"]], limit=[["C.SH"]], anomaly=[["D.SH"]]))
        s = mainline.derive_mainline_sample(D0)
        assert s.codes == ("A.SH", "B.SH")
        assert s.seed_counts == {HOT_INDUSTRY: 1, SURGING_CONCEPT: 1}

    def test_seed_set_is_frozen_for_the_day_after_first_derivation(self, monkeypatch):
        calls = _patch_seeds(monkeypatch, _seed_set(hot=[["A.SH"]]))
        for _ in range(5):
            mainline.derive_mainline_sample(D0)
        assert len(calls) == 1        # 当日冻结:只算一次(盘中 60s 一拍不重算)

    def test_failures_are_not_frozen_so_the_path_self_heals(self, monkeypatch):
        """⚠ 冻结只冻**成功**的派生:一次瞬时故障 / 尚未激活策略包,不该把这条纪律
        路径钉死到收盘。下一拍照常重试,恢复后立刻拿到样本。"""
        state = {"fail": True}
        seed_set = _seed_set(hot=[["A.SH"]])

        def flaky(trade_date, **kw):
            if state["fail"]:
                raise RuntimeError("瞬时读表失败")
            return seed_set

        monkeypatch.setattr("neckline.scan.seeds.generate_seeds", flaky)
        assert mainline.derive_mainline_sample(D0).unavailable_reason == mainline.REASON_SEED_FAILED
        state["fail"] = False
        s = mainline.derive_mainline_sample(D0)
        assert s.codes == ("A.SH",) and s.unavailable_reason is None

    # —— 样本不足 → 不触发 + 如实披露(四个原因码各一)————————————————
    def test_no_active_pack_is_disclosed_not_silently_empty(self, monkeypatch):
        monkeypatch.setattr("neckline.scan.seeds.generate_seeds", lambda *a, **k: None)
        s = mainline.derive_mainline_sample(D0)
        assert s.codes == () and s.size == 0
        assert s.unavailable_reason == mainline.REASON_NO_ACTIVE_PACK
        assert "无现役选股包" in s.payload()["unavailable_text"]

    def test_seed_failure_is_disclosed_and_does_not_raise(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("parquet 炸了")
        monkeypatch.setattr("neckline.scan.seeds.generate_seeds", boom)
        assert mainline.derive_mainline_sample(D0).unavailable_reason == mainline.REASON_SEED_FAILED

    def test_no_mainline_seeds_is_its_own_reason(self, monkeypatch):
        _patch_seeds(monkeypatch, _seed_set(limit=[["C.SH"]]))
        s = mainline.derive_mainline_sample(D0)
        assert s.unavailable_reason == mainline.REASON_NO_MAINLINE_SEEDS

    def test_no_pool_quota_is_its_own_reason(self, monkeypatch):
        """池里一个位都腾不出来 → 如实披露,⛔ 不静默当成"板块健康"。"""
        _patch_seeds(monkeypatch, _seed_set(hot=[["A.SH", "B.SH"]]))
        s = mainline.derive_mainline_sample(D0).restrict(0)
        assert s.codes == () and s.unavailable_reason == mainline.REASON_NO_POOL_QUOTA

    def test_restrict_signature_has_no_already_pooled_backdoor(self):
        """⚠ `restrict()` **只看额度**:一旦让"已在池里的码不占额度",LLM 多挑一个
        恰好也在切片里的成员就能让样本多出一只(残留耦合 ②b 换个面目回来)。
        结构性守门:签名里没有那个口子。"""
        params = set(inspect.signature(mainline.MainlineSample.restrict).parameters)
        assert params == {"self", "allowance"}

    def test_restrict_rotates_across_seeds_instead_of_dropping_whole_seeds(self, monkeypatch):
        """压缩走**逐颗种子轮转**:挤掉的是每条主线的精度,不是整条主线(整颗掉队
        会直接改掉 per-seed 估计量的权重构成)。"""
        _patch_seeds(monkeypatch, _seed_set(
            hot=[[f"6{s}{i:04d}.SH" for i in range(10)] for s in range(5)]))
        full = mainline.derive_mainline_sample(D0)
        assert full.seed_count == 5 and full.size == 20
        s = full.restrict(5)
        assert s.seed_count == 5                    # 五颗种子一颗没掉
        assert all(len(x.codes) == 1 for x in s.slices)
        assert s.size == 5 and s.restricted_from == 20

    # —— 结构性守门 ————————————————————————————————————————————————
    def test_guard_signature_has_no_basket_entrance(self):
        """⛔ 签名里没有篮子成员这个口子 —— 想接得先改签名、先读模块头。
        ⚠ ⑧-G 起连持仓 / 昨日涨停也不接了:样本 = 机械切片本身,与"我恰好盯着谁"
        解耦(⑧-G-A)。"""
        params = set(inspect.signature(mainline.derive_mainline_sample).parameters)
        assert params == {"report_date", "db_path", "parquet_dir"}
        assert not any("basket" in p or "member" in p or "target" in p for p in params)

    def test_guard_payload_declares_only_mechanical_sources(self, monkeypatch):
        _patch_seeds(monkeypatch, _seed_set(hot=[["A.SH"]]))
        p = mainline.derive_mainline_sample(D0).payload()
        assert p["allowed_sources"] == [mainline.SOURCE_MAINLINE_SLICE]
        assert set(p["sources"].values()) <= set(p["allowed_sources"])

    def test_guard_sector_dive_thresholds_unchanged(self):
        """⑧-F-B / ⑧-G-F:只换**样本怎么取**,阈值一字不动。"""
        assert retreat.SECTOR_DIVE_RET_TRIGGER == -0.03
        assert retreat.SECTOR_DIVE_RET_TRIGGER_EARLY == -0.04


# ══════════════════════════════════════════════════════════════════════════
# 整拍级(⑧-F 核心判据)—— ⛔ **整节已于 V2.4.0 P0 退役**
# ══════════════════════════════════════════════════════════════════════════
#
# **取代关系(施工纪律 4)**:原 `TestSampleIsUnchangedByLlmSelection`(2 例)与
# `TestTrailAndInsufficientSample`(2 例)都要跑一遍 `run_tick`、再从
# `retreat_metrics.hot_sector_sample_json` 把样本构成读回来对拍。P0.1 表
# 「代理关注池 →『大盘退潮』= 删」撤销了退潮判级,`retreat_metrics` **停写**,
# 那条留痕不再产生 —— 断言的数据源不存在了。
#
# **它们要守的两条不变量都还有人守,且更靠上游**:
#   ① 「换掉 LLM 的成员选择 → 样本逐位不变」→
#      `TestDerive::test_guard_signature_has_no_basket_entrance`
#      (`derive_mainline_sample` 的签名里**根本没有**篮子/成员/目标入口,
#      比"跑两遍比对结果"更强:结构上就读不到);
#   ② 「样本不足 → 不触发 + 如实披露」→ 上游的
#      `TestMinMainlineSample::test_below_min_does_not_trigger_even_on_a_crash`
#      (改成纯函数级问 `retreat.evaluate_retreat`)+ `TestDerive` 里
#      `no_active_pack` / `seed_failed` / `no_mainline_seeds` / `no_pool_quota`
#      四条原因码用例(**一条未动**)。
