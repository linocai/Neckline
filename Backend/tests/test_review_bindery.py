"""S11 行情材料装订的**行为**判据(架构 §六 第 2 件事,PROJECT_PLAN §5.9)。

结构判据(零 LLM / 三条成绩线隔离 / 只读 k9_*)在 `test_v250_s11_s13_guard.py`。

本文件锁四件事:
  ① 五样材料**真的配齐**了 —— 日 K + 买卖点 + 同期大盘 + 同期申万二级 + 当时的
     报告 / 预案 / 清单快照;
  ② **缺什么就说什么**:每一样缺席都在 `gaps` 里点名,⛔ 不许用空列表冒充「查过了没有」;
  ③ **一次取数**:全部票走**一次** parquet glob(§12 坑 1 —— 逐票 glob 会把常驻服务
     拖死),行业 / 报告 / 预案 / 清单各走**一次**区间 SQL;
  ④ 申万归属如实标成 `current_snapshot`(⛔ 不冒充成交当日的归属)。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from neckline.playbook.model import (
    Branch, BranchName, Condition, Levels, MetricRef, Op, Playbook,
)
from neckline.review import bindery
from neckline.review.reconcile import RoundTrip, WeeklyReview, iso_week_key, week_range
from tests.conftest import (
    business_days,
    insert_stock_basic,
    insert_sw_members,
    insert_trade_cal,
    write_daily_fixture,
)

_CODE = "600519.SH"
_L2 = ("801125.SI", "白酒Ⅱ")
_BENCH = bindery.BENCHMARK_CODE


def _bar(code: str, close: float, pre: float, *, high=None, low=None):
    return {
        "ts_code": code, "open": pre, "high": high if high is not None else close + 0.5,
        "low": low if low is not None else close - 0.5, "close": close,
        "pre_close": pre, "vol": 100000.0, "amount": 1_000_000.0, "pct_chg": 0.0,
    }


@pytest.fixture
def market(isolated_env):
    """40 个交易日的合成市场:一只票 + 上证综指,行业中位数逐日落表。"""
    days = business_days(date(2026, 6, 1), 40)
    insert_trade_cal(isolated_env, days)
    insert_stock_basic(isolated_env, [{"ts_code": _CODE, "name": "贵州茅台"}])
    insert_sw_members(isolated_env, [{
        "ts_code": _CODE, "l1_code": "801120.SI", "l1_name": "食品饮料",
        "l2_code": _L2[0], "l2_name": _L2[1], "l3_code": "850831.SI", "l3_name": "白酒Ⅲ",
    }])

    from neckline.facts import industry as facts_industry

    price = 100.0
    for i, d in enumerate(days):
        write_daily_fixture(isolated_env, "daily", d, [_bar(_CODE, price + i, price + i - 1)])
        write_daily_fixture(isolated_env, "index_daily", d,
                            [_bar(_BENCH, 3000.0 + i, 3000.0 + i - 1)])
        facts_industry.save_day(d, [facts_industry.IndustryDay(
            l2_code=_L2[0], l2_name=_L2[1], member_count=19,
            suspended_excluded=0, median_ret=0.001 * i)], db_path=isolated_env.db_path)
    return isolated_env, days


def _review(days, buy_i: int, sell_i: int) -> WeeklyReview:
    buy, sell = days[buy_i], days[sell_i]
    week = iso_week_key(sell)
    lo, hi = week_range(week)
    rt = RoundTrip(ts_code=_CODE, name="贵州茅台", buy_date=buy, buy_price=110.0,
                   qty=100, fees=30.0, sell_date=sell, sell_price=118.0, closed=True)
    r = WeeklyReview(week=week, week_start=lo, week_end=hi)
    r.round_trips = [rt]
    r.closed_round_trips = [rt]
    return r


def _seed_system_records(env, day, *, code: str = _CODE) -> None:
    """在窗口内的某一天铺齐系统当时留下的三样:报告 / 清单 / 预案。

    ⚠ 数值全是**夹具**,⛔ 不是标定值。"""
    import json
    import sqlite3

    from neckline.playbook import store as pb_store
    from neckline.report import store as report_store

    report_store.save_k9_report(
        trade_date=day, report_date=day, state="has_list",
        headline="今天有这些 · 1 只(严格 1 / 放宽 0)", gaps=[], markdown="# 夹具",
        structured={}, strategy="K9", strategy_version="K9-v2",
        params_package_version="k9-params-fixture",
        pack_id="pid", pack_version="fp-3", listing_size=1,
        strict_count=1, relaxed_count=0, db_path=env.db_path)

    conn = sqlite3.connect(str(env.db_path))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO k9_listing_entries (trade_date, ts_code, run_id, "
            "strategy, strategy_version, name, sw_l2_code, sw_l2_name, patterns_json, primary_pattern, "
            "tier, seat_kind, rank, score, industry_heat_score, pattern_strength_score, "
            "relay_score, evidence_json, risks_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (day.strftime("%Y%m%d"), code, "run-fixture", "K9", "K9-v2", "贵州茅台",
             _L2[0], _L2[1], json.dumps(["p1"]), "p1", "strict", "floor", 1, 0.9,
             0.5, 0.4, 0.0, "{}", "[]", "2026-01-01T00:00:00"))
        conn.commit()
    finally:
        conn.close()

    pb_store.save(Playbook(
        trade_date=day.strftime("%Y%m%d"), ts_code=code, pattern="p1",
        levels=Levels(first_resistance=130.0, second_resistance=140.0, invalidation=105.0),
        branches=(
            Branch(name=BranchName.CONFIRMED, all=(
                Condition(op=Op.LE, lhs=MetricRef.GAP_PCT, rhs=3.0),)),
            Branch(name=BranchName.REJECTED, all=(
                Condition(op=Op.LT, lhs=MetricRef.FIRST30_LOW, rhs=105.0),)),
        ),
        filled_by="fixture", filled_at="2026-01-01T00:00:00"),
        db_path=env.db_path)


def _bind(env, review, **kw):
    return bindery.bind_week(review, db_path=env.db_path,
                             parquet_dir=env.parquet_dir, **kw)


# ══════════════════════════════════════════════════════════════════════════
# ① 五样材料配齐
# ══════════════════════════════════════════════════════════════════════════

class TestMaterialsAreBound:
    def test_all_five_materials_are_bound_with_no_gaps(self, market):
        """架构 §六 第 2 件事的五样:日 K + 买卖点 + 同期大盘 + 同期申万二级
        + **当时那几天的报告与预案快照**。五样齐全时 `gaps` 必须是空的。"""
        env, days = market
        _seed_system_records(env, days[20])
        binding = _bind(env, _review(days, 20, 24), pre_sessions=5, post_sessions=5)
        (rt,) = binding.round_trips
        assert rt.bars, "该票窗口内的日 K 一根都没装订上"
        assert rt.marks, "买卖点一个都没标"
        assert rt.benchmark, "同期大盘一根都没装订上"
        assert rt.industry, "同期申万二级中位数一天都没装订上"
        assert rt.sw_l2_code == _L2[0] and rt.sw_l2_name == _L2[1]
        assert rt.snapshots, "当时那几天的报告 / 清单 / 预案快照一条都没装订上"
        assert binding.gaps == (), f"材料齐了却报了周级缺口:{binding.gaps}"
        assert rt.gaps == (), f"材料齐了却报了缺口:{rt.gaps}"

    def test_snapshot_carries_report_listing_and_playbook(self, market):
        env, days = market
        _seed_system_records(env, days[20])
        binding = _bind(env, _review(days, 20, 24), pre_sessions=5, post_sessions=5)
        (rt,) = binding.round_trips
        day = days[20].strftime("%Y%m%d")
        (snap,) = [s for s in rt.snapshots if s.trade_date == day]
        assert snap.report["state"] == "has_list"
        assert snap.listing["rank"] == 1 and snap.listing["primary_pattern"] == "p1"
        assert snap.playbook["levels"]["firstResistance"] == 130.0
        # 🔴 报告索引**刻意不带 markdown**(40 天窗口塞 40 份全文是无用负担)。
        assert "markdown" not in snap.report

    def test_days_without_any_system_record_are_not_padded(self, market):
        """⛔ 不给「那天系统什么都没留下」铺一行空壳 —— 空壳会被读成
        「那天系统看过、没说什么」。"""
        env, days = market
        _seed_system_records(env, days[20])
        binding = _bind(env, _review(days, 20, 24), pre_sessions=5, post_sessions=5)
        (rt,) = binding.round_trips
        assert [s.trade_date for s in rt.snapshots] == [days[20].strftime("%Y%m%d")]

    def test_window_spans_pre_and_post_sessions(self, market):
        env, days = market
        binding = _bind(env, _review(days, 20, 24), pre_sessions=5, post_sessions=5)
        assert binding.window_start == days[15].strftime("%Y%m%d")
        assert binding.window_end == days[29].strftime("%Y%m%d")
        (rt,) = binding.round_trips
        assert len(rt.bars) == 15                       # 5 前 + 5 段内 + 5 后
        assert len(rt.benchmark) == len(rt.bars)

    def test_buy_and_sell_points_use_the_ledger_price_not_the_close(self, market):
        """🔴 买卖点取**交割单原价**。复盘要看的恰恰是「我成交在那天的什么位置」,
        拿 K 线收盘价冒充成交价会把这个问题问没了。"""
        env, days = market
        binding = _bind(env, _review(days, 20, 24), pre_sessions=3, post_sessions=3)
        (rt,) = binding.round_trips
        assert [m.side for m in rt.marks] == ["buy", "sell"]
        assert rt.marks[0].price == 110.0 and rt.marks[0].qty == 100
        assert rt.marks[1].price == 118.0
        closes = {b.trade_date: b.close for b in rt.bars}
        assert closes[rt.marks[0].trade_date] != rt.marks[0].price

    def test_open_position_has_only_a_buy_mark(self, market):
        env, days = market
        review = _review(days, 20, 24)
        rt = review.round_trips[0]
        review.round_trips = [RoundTrip(
            ts_code=rt.ts_code, name=rt.name, buy_date=rt.buy_date,
            buy_price=rt.buy_price, qty=rt.qty, fees=rt.fees)]
        review.closed_round_trips = []
        binding = _bind(env, review, pre_sessions=3, post_sessions=3)
        (b,) = binding.round_trips
        assert [m.side for m in b.marks] == ["buy"]
        assert b.sell_date is None and b.closed is False

    def test_pct_chg_is_recomputed_from_close_over_pre_close(self, market):
        """⛔ 不取 `daily.pct_chg` 那一列:夹具把它写成 0,而真涨幅不是 0 ——
        同一份材料里两个来源会给出两个「那天涨了几个点」。"""
        env, days = market
        binding = _bind(env, _review(days, 20, 24), pre_sessions=2, post_sessions=2)
        (rt,) = binding.round_trips
        assert all(b.pct_chg is not None for b in rt.bars)
        assert any(abs(b.pct_chg) > 0.0 for b in rt.bars)


# ══════════════════════════════════════════════════════════════════════════
# ② 缺什么就说什么
# ══════════════════════════════════════════════════════════════════════════

class TestGapsAreNamed:
    def test_missing_bars_are_named_not_silently_empty(self, market):
        env, days = market
        review = _review(days, 20, 24)
        review.round_trips[0] = RoundTrip(
            ts_code="000001.SZ", name="平安银行", buy_date=days[20], buy_price=10.0,
            qty=100, fees=1.0, sell_date=days[24], sell_price=11.0, closed=True)
        review.closed_round_trips = list(review.round_trips)
        binding = _bind(env, review, pre_sessions=3, post_sessions=3)
        (rt,) = binding.round_trips
        assert rt.bars == ()
        assert any(g.startswith("bars_missing") for g in rt.gaps)

    def test_unmapped_industry_is_named(self, market):
        env, days = market
        review = _review(days, 20, 24)
        review.round_trips[0] = RoundTrip(
            ts_code="000001.SZ", name="平安银行", buy_date=days[20], buy_price=10.0,
            qty=100, fees=1.0, sell_date=days[24], sell_price=11.0, closed=True)
        review.closed_round_trips = list(review.round_trips)
        binding = _bind(env, review, pre_sessions=3, post_sessions=3)
        (rt,) = binding.round_trips
        assert rt.industry_source == bindery.INDUSTRY_SOURCE_NONE
        assert rt.sw_l2_code is None
        assert any(g.startswith("industry_unmapped") for g in rt.gaps)

    def test_no_playbook_in_window_is_named(self, market):
        """v2.5.0 上线前的日子本来就没有预案 —— 那要说出口,⛔ 不能让材料看起来完整。"""
        env, days = market
        binding = _bind(env, _review(days, 20, 24), pre_sessions=3, post_sessions=3)
        (rt,) = binding.round_trips
        assert any(g.startswith("playbooks_missing") for g in rt.gaps)

    def test_no_reports_in_window_is_named_at_week_level(self, market):
        env, days = market
        binding = _bind(env, _review(days, 20, 24), pre_sessions=3, post_sessions=3)
        assert any(g.startswith("reports_missing") for g in binding.gaps)

    def test_empty_review_says_so(self, isolated_env):
        r = WeeklyReview(week="2026-W30", week_start=date(2026, 7, 20),
                         week_end=date(2026, 7, 26))
        binding = bindery.bind_week(r, db_path=isolated_env.db_path,
                                    parquet_dir=isolated_env.parquet_dir)
        assert binding.round_trips == ()
        assert any(g.startswith("no_round_trips") for g in binding.gaps)

    def test_oversized_window_is_truncated_loudly(self, market):
        """🔴 §12 坑 1:一份跨了三年的交割单不许把整段行情读进常驻服务。
        超容量上限 → **如实截断并记 gap**,⛔ 不静默照读。"""
        env, days = market
        binding = _bind(env, _review(days, 20, 24),
                        pre_sessions=bindery.MAX_WINDOW_SESSIONS + 50, post_sessions=5)
        assert any(g.startswith("window_truncated") for g in binding.gaps)

    def test_truncation_never_moves_the_window_off_the_trade_dates(self, market):
        """🔴 **R2-07**:容量上限只削**上下文**,⛔ 不许把成交日削掉。

        从前的写法是「窗口超了就取最后 250 根」—— 只动 `start`、`end` 不动。
        窗口被向后拉长时,截断后的 `[start, end]` 会**整段落在成交日之后**,
        那一周的每一笔回合都拿到 0 根 K 线;跨度较长的交割单则会被从最早处截掉。
        """
        env, days = market
        buy, sell = days[20], days[24]
        binding = _bind(env, _review(days, 20, 24),
                        pre_sessions=bindery.MAX_WINDOW_SESSIONS * 3,
                        post_sessions=bindery.MAX_WINDOW_SESSIONS * 3)
        assert any(g.startswith("window_truncated") for g in binding.gaps)
        rt = binding.round_trips[0]
        assert rt.window_start <= buy.strftime("%Y%m%d"), "成交日被截到窗口外面去了"
        assert rt.window_end >= sell.strftime("%Y%m%d"), "窗口整段落在成交日之前 / 之后"
        # 🔴 每一笔回合仍然拿得到自己的 K 线(材料不是空的)。
        bar_days = {b.trade_date for b in rt.bars}
        assert buy.strftime("%Y%m%d") in bar_days and sell.strftime("%Y%m%d") in bar_days
        # 首行那句「买入前 N 个交易日」得说真话:记的是**实际铺了几天**。
        assert binding.pre_sessions < bindery.MAX_WINDOW_SESSIONS * 3
        assert binding.pre_sessions + binding.post_sessions <= bindery.MAX_WINDOW_SESSIONS

    def test_a_normal_window_is_not_touched_by_the_cap(self, market):
        """⚠ 反向自检:没超上限的窗口一个字都不许改。"""
        env, days = market
        binding = _bind(env, _review(days, 20, 24), pre_sessions=3, post_sessions=3)
        assert not any(g.startswith("window_truncated") for g in binding.gaps)
        assert (binding.pre_sessions, binding.post_sessions) == (3, 3)


# ══════════════════════════════════════════════════════════════════════════
# ③ 一次取数(§12 坑 1)
# ══════════════════════════════════════════════════════════════════════════

class TestReadsAreBatched:
    def test_all_codes_share_one_parquet_glob(self, market, monkeypatch):
        """🔴 逐票 glob 会开上万个 parquet footer(2026-07-29 信息卡端点 18~20s 的
        同一条链)。三只票也只许 glob **一次**日 K + 一次大盘。"""
        env, days = market
        from neckline.data import market_data as md

        calls = {"multi": 0, "single": 0}
        real_multi = md.get_multi_stock_history
        real_single = md.get_stock_history

        def spy_multi(*a, **kw):
            calls["multi"] += 1
            return real_multi(*a, **kw)

        def spy_single(*a, **kw):
            calls["single"] += 1
            return real_single(*a, **kw)

        monkeypatch.setattr(md, "get_multi_stock_history", spy_multi)
        monkeypatch.setattr(md, "get_stock_history", spy_single)

        review = _review(days, 20, 24)
        base = review.round_trips[0]
        review.round_trips = [base] + [
            RoundTrip(ts_code=c, name=c, buy_date=days[21], buy_price=10.0, qty=100,
                      fees=1.0, sell_date=days[23], sell_price=11.0, closed=True)
            for c in ("000001.SZ", "000002.SZ")
        ]
        review.closed_round_trips = list(review.round_trips)
        _bind(env, review, pre_sessions=3, post_sessions=3)

        assert calls["multi"] == 1, "三只票的日 K 必须一次 glob 取全"
        # 大盘走 `get_index_history` → 它内部转 `get_stock_history`,只此一次。
        assert calls["single"] == 1, f"除大盘外不该再有单票取数(实际 {calls['single']} 次)"

    def test_industry_series_is_one_query_not_one_per_day(self, market, monkeypatch):
        env, days = market
        from neckline.facts import industry as facts_industry

        seen = {"series": 0, "day": 0}
        real_series = facts_industry.load_series
        real_day = facts_industry.load_day
        monkeypatch.setattr(facts_industry, "load_series",
                            lambda *a, **k: (seen.__setitem__("series", seen["series"] + 1),
                                             real_series(*a, **k))[1])
        monkeypatch.setattr(facts_industry, "load_day",
                            lambda *a, **k: (seen.__setitem__("day", seen["day"] + 1),
                                             real_day(*a, **k))[1])
        _bind(env, _review(days, 20, 24), pre_sessions=10, post_sessions=10)
        assert seen["series"] == 1
        assert seen["day"] == 0, "⛔ 别按日循环调 load_day(每次都会重跑整份 schema 脚本)"


# ══════════════════════════════════════════════════════════════════════════
# ④ 语义诚实
# ══════════════════════════════════════════════════════════════════════════

class TestHonestSemantics:
    def test_industry_source_is_labelled_current_snapshot(self, market):
        """⚠ 申万归属用的是**今天的**成分快照,不是成交当日的(接口只给当前归属)。
        这与事实包回填的语义差是同一件事,必须写在明处。"""
        env, days = market
        binding = _bind(env, _review(days, 20, 24), pre_sessions=3, post_sessions=3)
        (rt,) = binding.round_trips
        assert rt.industry_source == bindery.INDUSTRY_SOURCE_CURRENT
        assert "当前" in binding.to_dict()["note"]

    def test_markdown_prints_every_gap(self, market):
        env, days = market
        binding = _bind(env, _review(days, 20, 24), pre_sessions=3, post_sessions=3)
        md = bindery.render_binding_markdown(binding)
        for g in binding.gaps:
            assert g in md
        for g in binding.round_trips[0].gaps:
            assert g in md

    def test_markdown_states_it_is_material_not_a_judgement(self, market):
        """架构 §六:这一层只解析 / 装订 / 存档,结论由用户在聊天框里得出。"""
        env, days = market
        md = bindery.render_binding_markdown(
            _bind(env, _review(days, 20, 24), pre_sessions=3, post_sessions=3))
        assert "回看材料" in md and "不是判断" in md
