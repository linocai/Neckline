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
"""

from __future__ import annotations

import ast
import inspect
import json
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path

import pytest

from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, write_daily_fixture

from neckline.data.board import Board, classify_by_code
from neckline.db import connection
from neckline.scan.seeds import (
    ANOMALY_CLUSTER,
    HOT_INDUSTRY,
    LIMIT_CLUSTER,
    SURGING_CONCEPT,
    DriverSeed,
    SeedSet,
)
from neckline.sentinel import mainline, retreat, universe
from neckline.sentinel.engine import reset_retreat_process_state, run_tick
from neckline.sentinel.positions import open_position
from neckline.sentinel.quotes import Quote
from neckline.sentinel.universe import load_watch_universe

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

    def test_below_min_does_not_trigger_even_on_a_crash(self, isolated_env, monkeypatch):
        """整拍级:样本 4 只(< 5)时,哪怕全线 −5%,主线跳水一路**不判**。"""
        report_day, today = _prepare_day(isolated_env)
        _patch_seeds(monkeypatch, _seed_set(hot=[_ALL]))         # 一颗种子 → 切片最多 K=4 只
        r = run_tick(datetime.combine(today, time(10, 30)), db_path=isolated_env.db_path,
                     parquet_dir=isolated_env.parquet_dir, quotes_fn=_quotes)
        s = _recorded_sample(isolated_env, today)
        assert s["size"] == 4 and s["quoted"] == 4
        assert s["per_seed_avg"] == pytest.approx(-0.05)          # 读数算得出来
        assert r.retreat_warning is None or "主线跳水" not in r.retreat_warning
        assert s["min_sample"] == mainline.MIN_MAINLINE_SAMPLE     # 留痕带门槛,可审计


# ══════════════════════════════════════════════════════════════════════════
# ⑧-G-D 池位配额:有界必需项全进 + 两份测量样本各有保底
# ══════════════════════════════════════════════════════════════════════════

_LIMIT_UP_STRESS = 200          # 「涨停 > 180 只」压力用例(⑧-F 登记的残留耦合 ②b 现场)


def _stress_pool(env, monkeypatch, *, basket_codes, n_seeds=30):
    """极端涨停日 + 大切片:两份测量样本同时想要超过自己保底的池位。"""
    days = business_days(date(2026, 7, 1), 5)
    insert_trade_cal(env, days)
    report_day, today = days[-2], days[-1]
    limit_ups = [f"9{i:05d}.SZ" for i in range(_LIMIT_UP_STRESS)]
    write_daily_fixture(env, "limit_derived", report_day, [
        {"ts_code": c, "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
         "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
         "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": 1}
        for c in limit_ups
    ])
    seeds = [[f"6{s:02d}{i:03d}.SH" for i in range(20)] for s in range(n_seeds)]
    _patch_seeds(monkeypatch, _seed_set(hot=seeds))
    insert_stock_basic(env, [{"ts_code": c, "name": c, "market": "主板"}
                             for c in ["600001.SH", "600002.SH", "600003.SH"] + basket_codes])
    for code in ("600001.SH", "600002.SH", "600003.SH"):
        open_position(code, 10.0, 100, report_day, db_path=env.db_path)
    _seed_basket(env, report_day, basket_codes)
    mainline.reset_seed_cache()
    return report_day, today


def _seed_basket(env, report_day, codes, *, tier=1, key="k1"):
    if not codes:
        return
    with connection(env.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (report_day.strftime("%Y%m%d"), key, f"篮{key}", "驱动", "theme", tier, "K4-pack-v1",
             1, "v1.3.3", "auto", "ok", "2026-08-02T00:00:00+08:00"),
        )
        bid = int(cur.lastrowid)
        for c in codes:
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, c, "core", None, 0, "理由", 1, "2026-08-02T00:00:00+08:00"),
            )


class TestPoolQuota:
    def test_extreme_limit_up_day_keeps_every_floor(self, isolated_env, monkeypatch):
        """造 200 只涨停的极端日:持仓 / 篮子成员 / 指数 / 两份测量样本的保底量
        **一个不少**,且总量不越 `breadth_cap`。"""
        basket = ["600101.SH", "600102.SH", "600103.SH"]
        report_day, today = _stress_pool(isolated_env, monkeypatch, basket_codes=basket)
        wu = load_watch_universe(today, db_path=isolated_env.db_path,
                                 parquet_dir=isolated_env.parquet_dir)
        pool = set(wu.codes)
        mandatory = {"600001.SH", "600002.SH", "600003.SH"} | set(basket) | set(wu.index_codes)
        assert len(wu.codes) == len(pool) <= universe.DEFAULT_BREADTH_CAP
        # ① 有界必需项无条件全进
        assert {"600001.SH", "600002.SH", "600003.SH"} <= pool          # 持仓 ≤3
        assert set(basket) <= pool                                      # T1/T2 成员 ≤21
        assert wu.index_codes and set(wu.index_codes) <= pool           # 板块指数 ≤5
        # ② 两份测量样本各拿到自己的保底(两边都想要更多 → 各自压在保底上)
        assert wu.mainline_sample.size == universe.MAINLINE_SLICE_QUOTA_FLOOR
        assert len(wu.breadth_extra_codes) == universe.PREV_LIMIT_UP_QUOTA_FLOOR
        # ⚠ `mainline_codes` 是"**新占**池位"的那部分:本例持仓落在种子成分里,被
        # crc32 选中的那些不必再占位(池子欠填几个,**不是**样本少了几只)——两个量
        # 的差恰是重叠数,这正是"样本只看机械输入"的推论。
        overlap = set(wu.mainline_sample.codes) & set(mandatory)
        assert overlap, "本例特意让持仓落在种子成分里,应当至少有一只被 crc32 选中"
        assert len(wu.mainline_codes) == wu.mainline_sample.size - len(overlap)
        # ③ 样本 ⊆ 池(有报价才算得出收益率,这条不变量必须由构造保证)
        assert set(wu.mainline_sample.codes) <= pool
        # ④ 压缩走**逐颗种子轮转**:30 颗种子一颗都没掉队(掉队 = 改了 per-seed 权重)
        assert wu.mainline_sample.seed_count == 30
        assert wu.mainline_sample.restricted_from == 30 * mainline.MAINLINE_SAMPLE_PER_SEED

    def test_basket_member_count_does_not_move_either_measurement_sample(
            self, isolated_env, monkeypatch):
        """**⑧-F 登记的残留耦合 ②b 的回归判据**:篮子成员既有界又保底,就不再与任何
        测量样本抢位 —— LLM 在任何极端日都挪不动测量样本(⑧-G-D 第 3 条)。"""
        def run(basket_codes):
            with connection(isolated_env.db_path) as conn:
                conn.execute("DELETE FROM basket_members")
                conn.execute("DELETE FROM baskets")
                conn.execute("DELETE FROM positions")
            mainline.reset_seed_cache()
            report_day, today = _stress_pool(
                isolated_env, monkeypatch, basket_codes=basket_codes)
            wu = load_watch_universe(today, db_path=isolated_env.db_path,
                                     parquet_dir=isolated_env.parquet_dir)
            return wu

        few = run(["600101.SH"])
        # ⚠ 挑**最难的一种**:21 只顶格,且**故意与两份样本重叠** —— 既有切片里的码
        # (600000xx),也有昨日涨停名单里的码(9000xx),还换了个板块(300xxx)。
        # 「已经在池里的码不占额度」这种看似聪明的省池位写法会在这里露馅。
        many = run([f"600{i:03d}.SH" for i in range(7)]        # 落在种子成分里
                   + [f"9{i:05d}.SZ" for i in range(7)]        # 落在昨日涨停名单里
                   + [f"3001{i:02d}.SZ" for i in range(7)])    # 无关的第三个板块
        assert len(many.basket_codes) == 21 and len(few.basket_codes) == 1
        assert many.index_codes != few.index_codes             # 连指数数量都变了
        # …两份测量样本却**逐位相同**
        assert many.mainline_sample.codes == few.mainline_sample.codes
        assert many.mainline_sample.payload() == few.mainline_sample.payload()
        assert many.breadth_extra_codes == few.breadth_extra_codes
        # 池总量仍不越上限(重叠只让池子欠填,不会撑爆)
        for wu in (few, many):
            assert len(set(wu.codes)) == len(wu.codes) <= universe.DEFAULT_BREADTH_CAP

    def test_breadth_cap_is_unchanged(self):
        """⑧-G-D:⛔ 不许自行抬 `breadth_cap`(会改盘中轮询量与限流风险面)。"""
        assert universe.DEFAULT_BREADTH_CAP == 200
        assert (universe.MAINLINE_SLICE_QUOTA_FLOOR + universe.PREV_LIMIT_UP_QUOTA_FLOOR
                + universe.MANDATORY_POOL_RESERVE) == universe.DEFAULT_BREADTH_CAP

    def test_quota_gives_leftover_to_whoever_needs_it(self):
        """"谁不够用谁的实际量,余量归对方"(⑧-G-D 第 2 条),纯函数级三种局面。"""
        budget = universe.DEFAULT_BREADTH_CAP - universe.MANDATORY_POOL_RESERVE
        # 两边都想要更多 → 各自压在保底
        assert universe._mainline_quota(budget, 120, 200) == universe.MAINLINE_SLICE_QUOTA_FLOOR
        # 涨停很少 → 主线吃到余量(不止保底),但仍给对方留够它的实际需要
        assert universe._mainline_quota(budget, 160, 20) == budget - 20
        assert universe._mainline_quota(budget, 150, 20) == 150      # 只拿实际需要
        # 切片很小 → 只拿实际需要,余量归涨停
        assert universe._mainline_quota(budget, 40, 200) == 40

    def test_slice_enters_the_pool_on_its_own_without_being_a_limit_up(
            self, isolated_env, monkeypatch):
        """切片是**专属来源**:不借"昨日涨停"那条路也能进池(⑧-G-B)。"""
        days = business_days(date(2026, 7, 1), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        _patch_seeds(monkeypatch, _seed_set(hot=[_ALL]))
        wu = load_watch_universe(today, db_path=isolated_env.db_path,
                                 parquet_dir=isolated_env.parquet_dir)
        assert wu.breadth_extra_codes == []                    # 当日无涨停名单
        assert set(_ALL) <= set(wu.codes) and set(wu.mainline_codes) == set(_ALL)


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
# 整拍级:换掉 LLM 的成员选择 → 样本逐位不变(⑧-F 核心判据,⑧-G 继续锁死)
# ══════════════════════════════════════════════════════════════════════════

_ALL = ["600201.SH", "600202.SH", "600203.SH", "600204.SH"]
_ALL8 = _ALL + ["600205.SH", "600206.SH", "600207.SH", "600208.SH"]


def _prepare_day(env, *, codes=None, with_limit_up=False):
    codes = codes or _ALL
    days = business_days(date(2026, 7, 1), 30)
    report_day, today = days[-2], days[-1]
    insert_trade_cal(env, days)
    insert_stock_basic(env, [{"ts_code": c, "name": c, "market": "主板"} for c in codes])
    for d in days:
        if d >= today:
            continue
        write_daily_fixture(env, "daily", d, [
            {"ts_code": c, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "pre_close": 10.0, "vol": 1000.0, "amount": 10000.0} for c in codes
        ])
    if with_limit_up:
        write_daily_fixture(env, "limit_derived", report_day, [
            {"ts_code": c, "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
             "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
             "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": 1}
            for c in codes
        ])
    reset_retreat_process_state()
    return report_day, today


def _quotes(codes):
    return {c: Quote(code=c.split(".")[0], name=c, price=9.5, pre_close=10.0, open=9.8,
                     high=9.9, low=9.5, volume=1000.0, amount=950000.0, ts="", source="sina")
            for c in codes}


def _recorded_sample(env, today) -> dict:
    with connection(env.db_path) as conn:
        row = conn.execute(
            "SELECT hot_sector_sample_json FROM retreat_metrics WHERE trade_date=?",
            (today.strftime("%Y%m%d"),),
        ).fetchone()
    return json.loads(row[0])


class TestSampleIsUnchangedByLlmSelection:
    def _run(self, env, monkeypatch, basket_codes):
        mainline.reset_seed_cache()
        report_day, today = _prepare_day(env, codes=_ALL8)
        _patch_seeds(monkeypatch, _seed_set(hot=[_ALL8[:4], _ALL8[4:]]))
        _seed_basket(env, report_day, basket_codes)
        run_tick(datetime.combine(today, time(10, 30)), db_path=env.db_path,
                 parquet_dir=env.parquet_dir, quotes_fn=_quotes)
        return _recorded_sample(env, today)

    def test_swapping_basket_members_leaves_the_sample_bit_identical(
            self, isolated_env, monkeypatch):
        """⑧-F 的核心判据(⑧-G 后依然成立,而且更强:切片压根不读篮子表)。
        两次运行只差「LLM 挑了哪些成员」,样本构成必须逐位相同。"""
        a = self._run(isolated_env, monkeypatch, ["600201.SH"])
        with connection(isolated_env.db_path) as conn:
            conn.execute("DELETE FROM basket_members")
            conn.execute("DELETE FROM baskets")
            conn.execute("DELETE FROM retreat_metrics")
            conn.execute("DELETE FROM sentinel_events")
        b = self._run(isolated_env, monkeypatch, ["600203.SH", "600204.SH", "600205.SH"])
        assert a["codes"] == b["codes"] == sorted(_ALL8)
        assert a["seed_slices"] == b["seed_slices"]
        assert a["per_seed_avg"] == b["per_seed_avg"]
        assert a["size"] == b["size"] == 8

    def test_a_basket_member_picked_by_crc32_still_enters_the_sample(
            self, isolated_env, monkeypatch):
        """⚠ 排除的是「LLM 这条路」,不是「LLM 碰过的票」(⑧-G-G 第 1 条)——
        一只票被 crc32 选中就进样本,**与它是不是篮子成员无关**;⑧-F 时代
        「只靠篮子进池的码不进样本」那条口径已被 ⑧-G 的配额切片取代。"""
        s = self._run(isolated_env, monkeypatch, ["600202.SH"])
        assert "600202.SH" in s["codes"]
        assert set(s["sources"].values()) == {mainline.SOURCE_MAINLINE_SLICE}


class TestTrailAndInsufficientSample:
    def test_trail_row_carries_the_sample_composition_every_tick(
            self, isolated_env, monkeypatch):
        report_day, today = _prepare_day(isolated_env, codes=_ALL8)
        _patch_seeds(monkeypatch, _seed_set(hot=[_ALL8[:4]], concept=[_ALL8[4:]]))
        mainline.reset_seed_cache()
        run_tick(datetime.combine(today, time(10, 30)), db_path=isolated_env.db_path,
                 parquet_dir=isolated_env.parquet_dir, quotes_fn=_quotes)
        s = _recorded_sample(isolated_env, today)
        assert s["size"] == 8 and s["quoted"] == 8
        assert s["seed_counts"] == {HOT_INDUSTRY: 1, SURGING_CONCEPT: 1}
        assert [x["seed_kind"] for x in s["seed_slices"]] == [HOT_INDUSTRY, SURGING_CONCEPT]
        assert s["per_seed_k"] == mainline.MAINLINE_SAMPLE_PER_SEED
        assert s["pack_version"] == "K4-pack-v1"
        assert s["unavailable_reason"] is None
        assert s["per_seed_avg"] == pytest.approx(-0.05)
        assert s["pooled_avg"] == pytest.approx(-0.05)

    def test_insufficient_sample_does_not_trigger_and_is_disclosed(
            self, isolated_env, monkeypatch):
        """样本不足(无现役包 → 无种子)→ 主线跳水一路**不判**(即使全池都在暴跌),
        并把原因如实落进留痕。⛔ 不回退到篮子成员样本、不用小样本硬判。"""
        report_day, today = _prepare_day(isolated_env, codes=_ALL8, with_limit_up=True)
        monkeypatch.setattr("neckline.scan.seeds.generate_seeds", lambda *a, **k: None)
        mainline.reset_seed_cache()
        r = run_tick(datetime.combine(today, time(10, 30)), db_path=isolated_env.db_path,
                     parquet_dir=isolated_env.parquet_dir, quotes_fn=_quotes)
        assert r.retreat_alert is None
        assert r.retreat_warning is None or "主线跳水" not in r.retreat_warning
        s = _recorded_sample(isolated_env, today)
        assert s["size"] == 0
        assert s["unavailable_reason"] == mainline.REASON_NO_ACTIVE_PACK
        with connection(isolated_env.db_path) as conn:
            avg_chg = conn.execute(
                "SELECT hot_sector_avg_chg FROM retreat_metrics WHERE trade_date=?",
                (today.strftime("%Y%m%d"),),
            ).fetchone()[0]
        assert avg_chg is None      # 诚实"无数据",不是 0.0
