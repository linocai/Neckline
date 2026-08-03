"""V2-⑧-F 退潮「主线板块跳水」样本机械化(PROJECT_PLAN §五 ⑧-F,2026-08-03 planner
裁定,🔴 碰纪律触发器)。

**核心判据(⑧-F 验收原文)**:「换掉 LLM 的成员选择 → 样本**逐位不变**」。本文件用
两条路各证一次:①函数级 —— `derive_mainline_sample` 的签名里**根本没有**篮子成员
这个入口(结构性不可能被塑形);②整拍级 —— 同一天同一份 `limit_derived`,只换 D0
篮子成员,`retreat_metrics` 里落的样本构成逐位相同。

另外锁死三条:`sector_dive` 阈值一字未动;样本不足 → 不触发 + 如实披露;留痕行含
样本构成(codes + 来源标签 + 样本量)。
"""

from __future__ import annotations

import inspect
import json
from datetime import date, datetime, time

import pytest

from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, write_daily_fixture

from neckline.db import connection
from neckline.scan.seeds import (
    ANOMALY_CLUSTER,
    HOT_INDUSTRY,
    LIMIT_CLUSTER,
    SURGING_CONCEPT,
    DriverSeed,
    SeedSet,
)
from neckline.sentinel import mainline
from neckline.sentinel import retreat
from neckline.sentinel.engine import reset_retreat_process_state, run_tick
from neckline.sentinel.positions import open_position
from neckline.sentinel.quotes import Quote

pytestmark = pytest.mark.usefixtures("isolated_env")

D0 = date(2026, 7, 23)


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
# 派生本身(纯函数级)
# ══════════════════════════════════════════════════════════════════════════

class TestDerive:
    def test_sample_is_seed_members_intersect_mechanical_pool(self, monkeypatch):
        _patch_seeds(monkeypatch, _seed_set(hot=[["A.SH", "B.SH", "C.SH"]]))
        s = mainline.derive_mainline_sample(
            D0, position_codes=["C.SH"], prev_limit_up_codes=["A.SH", "Z.SH"])
        assert s.codes == ("A.SH", "C.SH")          # Z 不在种子里;B 没进池
        assert s.sources == {"A.SH": mainline.SOURCE_PREV_LIMIT_UP,
                             "C.SH": mainline.SOURCE_POSITION}
        assert s.unavailable_reason is None

    def test_position_label_wins_over_prev_limit_up(self, monkeypatch):
        _patch_seeds(monkeypatch, _seed_set(hot=[["A.SH"]]))
        s = mainline.derive_mainline_sample(
            D0, position_codes=["A.SH"], prev_limit_up_codes=["A.SH"])
        assert s.codes == ("A.SH",)
        assert s.sources["A.SH"] == mainline.SOURCE_POSITION

    def test_only_hot_industry_and_surging_concept_feed_the_sample(self, monkeypatch):
        """⛔ 涨停簇 / 异动簇不是「板块」,拿它们凑样本会把"主线板块跳水"的意思改掉。"""
        _patch_seeds(monkeypatch, _seed_set(
            hot=[["A.SH"]], concept=[["B.SH"]], limit=[["C.SH"]], anomaly=[["D.SH"]]))
        s = mainline.derive_mainline_sample(
            D0, prev_limit_up_codes=["A.SH", "B.SH", "C.SH", "D.SH"])
        assert s.codes == ("A.SH", "B.SH")
        assert s.seed_counts == {HOT_INDUSTRY: 1, SURGING_CONCEPT: 1}

    def test_order_is_deterministic_regardless_of_input_order(self, monkeypatch):
        _patch_seeds(monkeypatch, _seed_set(hot=[["C.SH", "A.SH", "B.SH"]]))
        s1 = mainline.derive_mainline_sample(D0, prev_limit_up_codes=["C.SH", "A.SH", "B.SH"])
        mainline.reset_seed_cache()
        s2 = mainline.derive_mainline_sample(D0, prev_limit_up_codes=["B.SH", "C.SH", "A.SH"])
        assert s1.codes == s2.codes == ("A.SH", "B.SH", "C.SH")

    def test_seed_set_is_frozen_for_the_day_after_first_derivation(self, monkeypatch):
        calls = _patch_seeds(monkeypatch, _seed_set(hot=[["A.SH"]]))
        for _ in range(5):
            mainline.derive_mainline_sample(D0, prev_limit_up_codes=["A.SH"])
        assert len(calls) == 1        # 当日冻结:只算一次(盘中 60s 一拍不重算)

    # —— 样本不足 → 不触发 + 如实披露(四个原因码各一)————————————————
    def test_no_active_pack_is_disclosed_not_silently_empty(self, monkeypatch):
        monkeypatch.setattr("neckline.scan.seeds.generate_seeds", lambda *a, **k: None)
        s = mainline.derive_mainline_sample(D0, prev_limit_up_codes=["A.SH"])
        assert s.codes == () and s.size == 0
        assert s.unavailable_reason == mainline.REASON_NO_ACTIVE_PACK
        assert "无现役选股包" in s.payload()["unavailable_text"]

    def test_seed_failure_is_disclosed_and_does_not_raise(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("parquet 炸了")
        monkeypatch.setattr("neckline.scan.seeds.generate_seeds", boom)
        s = mainline.derive_mainline_sample(D0, prev_limit_up_codes=["A.SH"])
        assert s.unavailable_reason == mainline.REASON_SEED_FAILED

    def test_no_mainline_seeds_is_its_own_reason(self, monkeypatch):
        _patch_seeds(monkeypatch, _seed_set(limit=[["C.SH"]]))
        s = mainline.derive_mainline_sample(D0, prev_limit_up_codes=["C.SH"])
        assert s.unavailable_reason == mainline.REASON_NO_MAINLINE_SEEDS

    def test_no_overlap_is_its_own_reason(self, monkeypatch):
        _patch_seeds(monkeypatch, _seed_set(hot=[["A.SH"]]))
        s = mainline.derive_mainline_sample(D0, prev_limit_up_codes=["Z.SH"])
        assert s.codes == ()
        assert s.unavailable_reason == mainline.REASON_NO_OVERLAP

    # —— 结构性守门 ————————————————————————————————————————————————
    def test_guard_signature_has_no_basket_entrance(self):
        """⛔ 签名里没有篮子成员这个口子 —— 想接得先改签名、先读模块头。"""
        params = set(inspect.signature(mainline.derive_mainline_sample).parameters)
        assert params == {"report_date", "position_codes", "prev_limit_up_codes",
                          "db_path", "parquet_dir"}
        assert not any("basket" in p or "member" in p or "target" in p for p in params)

    def test_guard_payload_declares_only_mechanical_sources(self, monkeypatch):
        _patch_seeds(monkeypatch, _seed_set(hot=[["A.SH"]]))
        p = mainline.derive_mainline_sample(D0, prev_limit_up_codes=["A.SH"]).payload()
        assert p["allowed_sources"] == [mainline.SOURCE_PREV_LIMIT_UP, mainline.SOURCE_POSITION]
        assert set(p["sources"].values()) <= set(p["allowed_sources"])

    def test_guard_sector_dive_thresholds_unchanged(self):
        """⑧-F-B:只换样本来源,**阈值一字不动**。"""
        assert retreat.SECTOR_DIVE_RET_TRIGGER == -0.03
        assert retreat.SECTOR_DIVE_RET_TRIGGER_EARLY == -0.04


# ══════════════════════════════════════════════════════════════════════════
# 整拍级:换掉 LLM 的成员选择 → 样本逐位不变
# ══════════════════════════════════════════════════════════════════════════

_ALL = ["600201.SH", "600202.SH", "600203.SH", "600204.SH"]


def _setup_day(env, monkeypatch, *, basket_codes):
    days = business_days(date(2026, 7, 1), 30)
    report_day, today = days[-2], days[-1]
    insert_trade_cal(env, days)
    insert_stock_basic(env, [{"ts_code": c, "name": c, "market": "主板"} for c in _ALL])
    for d in days:
        if d >= today:
            continue
        write_daily_fixture(env, "daily", d, [
            {"ts_code": c, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "pre_close": 10.0, "vol": 1000.0, "amount": 10000.0} for c in _ALL
        ])
    # D0 涨停名单(机械进池那条路)——**四只全在**,与篮子怎么挑无关
    write_daily_fixture(env, "limit_derived", report_day, [
        {"ts_code": c, "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
         "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
         "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": 1}
        for c in _ALL
    ])
    with connection(env.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (report_day.strftime("%Y%m%d"), "k1", "篮k1", "驱动", "theme", 1, "K4-pack-v1", 1,
             "v1.3.3", "auto", "ok", "2026-08-02T00:00:00+08:00"),
        )
        bid = int(cur.lastrowid)
        for c in basket_codes:
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, c, "core", None, 0, "理由", 1, "2026-08-02T00:00:00+08:00"),
            )
    _patch_seeds(monkeypatch, _seed_set(hot=[_ALL]))
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
        reset_retreat_process_state()
        mainline.reset_seed_cache()
        report_day, today = _setup_day(env, monkeypatch, basket_codes=basket_codes)
        run_tick(datetime.combine(today, time(10, 30)), db_path=env.db_path,
                 parquet_dir=env.parquet_dir, quotes_fn=_quotes)
        return _recorded_sample(env, today)

    def test_swapping_basket_members_leaves_the_sample_bit_identical(
            self, isolated_env, monkeypatch):
        """⑧-F 的核心判据。两次运行只差「LLM 挑了哪些成员」,样本构成必须逐位相同。"""
        a = self._run(isolated_env, monkeypatch, ["600201.SH"])
        # 清掉上一次的篮子与留痕,换一批成员重跑同一天
        with connection(isolated_env.db_path) as conn:
            conn.execute("DELETE FROM basket_members")
            conn.execute("DELETE FROM baskets")
            conn.execute("DELETE FROM retreat_metrics")
            conn.execute("DELETE FROM sentinel_events")
        b = self._run(isolated_env, monkeypatch, ["600203.SH", "600204.SH"])
        assert a["codes"] == b["codes"] == _ALL
        assert a["sources"] == b["sources"]
        assert a["size"] == b["size"] == 4

    def test_basket_only_codes_never_enter_the_sample(self, isolated_env, monkeypatch):
        """只靠篮子进池的码(不在 D0 涨停名单里)即使在种子成分里也**不进样本**。"""
        reset_retreat_process_state()
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        insert_trade_cal(isolated_env, days)
        insert_stock_basic(isolated_env,
                           [{"ts_code": c, "name": c, "market": "主板"} for c in _ALL])
        write_daily_fixture(isolated_env, "limit_derived", report_day, [
            {"ts_code": "600201.SH", "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
             "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
             "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": 1},
        ])
        with connection(isolated_env.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
                " pack_version, engine_api_version, charter_version, via, evidence_status,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (report_day.strftime("%Y%m%d"), "k1", "篮k1", "驱动", "theme", 1,
                 "K4-pack-v1", 1, "v1.3.3", "auto", "ok", "2026-08-02T00:00:00+08:00"),
            )
            bid = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, "600202.SH", "core", None, 0, "理由", 1, "2026-08-02T00:00:00+08:00"),
            )
        _patch_seeds(monkeypatch, _seed_set(hot=[_ALL]))
        mainline.reset_seed_cache()
        run_tick(datetime.combine(today, time(10, 30)), db_path=isolated_env.db_path,
                 parquet_dir=isolated_env.parquet_dir, quotes_fn=_quotes)
        sample = _recorded_sample(isolated_env, today)
        assert sample["codes"] == ["600201.SH"]        # 600202 只靠篮子进池 → 不进样本
        assert "600202.SH" not in sample["sources"]

    def test_position_codes_enter_the_sample(self, isolated_env, monkeypatch):
        reset_retreat_process_state()
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        insert_trade_cal(isolated_env, days)
        insert_stock_basic(isolated_env,
                           [{"ts_code": c, "name": c, "market": "主板"} for c in _ALL])
        open_position("600204.SH", 10.0, 100, report_day, db_path=isolated_env.db_path)
        _patch_seeds(monkeypatch, _seed_set(hot=[_ALL]))
        mainline.reset_seed_cache()
        run_tick(datetime.combine(today, time(10, 30)), db_path=isolated_env.db_path,
                 parquet_dir=isolated_env.parquet_dir, quotes_fn=_quotes)
        sample = _recorded_sample(isolated_env, today)
        assert sample["codes"] == ["600204.SH"]
        assert sample["sources"]["600204.SH"] == mainline.SOURCE_POSITION


class TestTrailAndInsufficientSample:
    def test_trail_row_carries_the_sample_composition_every_tick(
            self, isolated_env, monkeypatch):
        reset_retreat_process_state()
        mainline.reset_seed_cache()
        report_day, today = _setup_day(isolated_env, monkeypatch, basket_codes=["600201.SH"])
        run_tick(datetime.combine(today, time(10, 30)), db_path=isolated_env.db_path,
                 parquet_dir=isolated_env.parquet_dir, quotes_fn=_quotes)
        s = _recorded_sample(isolated_env, today)
        assert s["size"] == 4 and s["quoted"] == 4
        assert s["seed_counts"] == {HOT_INDUSTRY: 1, SURGING_CONCEPT: 0}
        assert s["pack_version"] == "K4-pack-v1"
        assert s["unavailable_reason"] is None

    def test_insufficient_sample_does_not_trigger_and_is_disclosed(
            self, isolated_env, monkeypatch):
        """样本不足(无现役包 → 无种子)→ 主线跳水一路**不判**(即使全池都在暴跌),
        并把原因如实落进留痕。⛔ 不回退到篮子成员样本、不用小样本硬判。"""
        reset_retreat_process_state()
        mainline.reset_seed_cache()
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        insert_trade_cal(isolated_env, days)
        insert_stock_basic(isolated_env,
                           [{"ts_code": c, "name": c, "market": "主板"} for c in _ALL])
        write_daily_fixture(isolated_env, "limit_derived", report_day, [
            {"ts_code": c, "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
             "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
             "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": 1}
            for c in _ALL
        ])
        monkeypatch.setattr("neckline.scan.seeds.generate_seeds", lambda *a, **k: None)
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
