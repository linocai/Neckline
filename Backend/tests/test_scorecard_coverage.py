"""覆盖率成绩线单测(V2.5.0 S4,PROJECT_PLAN §6 S4 验收 + §5.8.1)。

S4 的四条验收逐条对应本文件的四个 section:

| # | 验收 | section |
|---|---|---|
| 1 | 对本地历史某日跑出涨停家数与归因分布 | ① |
| 2 | 🔴 `coverage_all` **不读任何参数包** | ② |
| 3 | 🔴 参数缺失时 `coverage_in_pool` 为 **NULL 而不是 0** | ③ |
| 4 | ⛔ 不回填历史覆盖率 | ④ |

另加落表 / 读回 / API 三段。结构性守门(`scorecard/**` 零 import `neckline.k9`)
在 `test_v250_s4_scorecard_guard.py`。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from neckline.facts import pack as fact_pack
from neckline.facts import store as fact_store
from neckline.scorecard import coverage as cov
from neckline.scorecard import store as cov_store
from tests.conftest import (
    insert_namechange,
    insert_stock_basic,
    insert_sw_members,
    insert_trade_cal,
    write_daily_fixture,
)

D0 = date(2024, 3, 4)
D1 = date(2024, 3, 5)

#: 6 只票:半导体 3 只(其中 2 只当天涨停 → 成簇)、白酒 1 只涨停、
#: 科创板 1 只涨停(K9 第一层排除它,但 `coverage_all` 的分母**照收**)、ST 1 只涨停。
UNIVERSE = {
    "600001.SH": ("801080.SI", "半导体", "主板"),
    "600002.SH": ("801080.SI", "半导体", "主板"),
    "600003.SH": ("801080.SI", "半导体", "主板"),
    "600004.SH": ("801125.SI", "白酒Ⅱ", "主板"),
    "688001.SH": ("801080.SI", "半导体", "科创板"),
    "600005.SH": ("801156.SI", "医疗美容", "主板"),
}
LIMIT_UPS = ("600001.SH", "600002.SH", "600004.SH", "688001.SH", "600005.SH")


def _seed(env, day: date = D0) -> fact_store.FactPack:
    insert_trade_cal(env, [D0, D1])
    insert_stock_basic(env, [
        {"ts_code": c, "name": ("*ST示例" if c == "600005.SH" else f"示例{c[:6]}"),
         "market": mkt, "list_date": date(2020, 1, 2)}
        for c, (_l2, _n, mkt) in UNIVERSE.items()
    ])
    insert_namechange(env, [
        {"ts_code": c, "name": ("*ST示例" if c == "600005.SH" else f"示例{c[:6]}"),
         "start_date": date(2020, 1, 2)} for c in UNIVERSE
    ])
    insert_sw_members(env, [
        {"ts_code": c, "l2_code": l2, "l2_name": n} for c, (l2, n, _m) in UNIVERSE.items()
    ])

    daily_rows = [
        {"ts_code": c, "open": 10.0, "high": 11.0, "low": 10.0,
         "close": 11.0 if c in LIMIT_UPS else 10.1, "pre_close": 10.0,
         "change": 1.0, "pct_chg": 10.0, "vol": 1e5, "amount": 1e6}
        for c in UNIVERSE
    ]
    write_daily_fixture(env, "daily", day, daily_rows)
    write_daily_fixture(env, "daily_basic", day, [
        {"ts_code": c, "turnover_rate": 5.0, "turnover_rate_f": 6.0, "volume_ratio": 1.0,
         "circ_mv": 1e6, "total_mv": 2e6, "free_share": 1e5} for c in UNIVERSE])
    write_daily_fixture(env, "adj_factor", day, [
        {"ts_code": c, "adj_factor": 1.0} for c in UNIVERSE])
    write_daily_fixture(env, "moneyflow_dc", day, [
        {"ts_code": c, "net_amount": 1.0, "net_amount_rate": 0.1,
         "buy_elg_amount": 1.0, "buy_lg_amount": 1.0} for c in UNIVERSE])
    write_daily_fixture(env, "limit_derived", day, [
        {"ts_code": c, "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
         "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
         "is_limit_down": False, "is_zaban": False,
         "consec_limit_up_days": 2 if c == "600001.SH" else 1}
        for c in LIMIT_UPS
    ] + [
        {"ts_code": "600003.SH", "board": "MAIN", "status": "zaban", "limit_pct": 0.10,
         "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": False,
         "is_limit_down": False, "is_zaban": True, "consec_limit_up_days": 0},
    ])
    from neckline.data.market_data import write_table_day
    write_table_day("suspend_d", day, pl.DataFrame(schema={
        "ts_code": pl.String, "trade_date": pl.Date, "suspend_type": pl.String}),
        parquet_dir=env.parquet_dir)

    built = fact_pack.build(day, parquet_dir=env.parquet_dir, db_path=env.db_path)
    assert isinstance(built, fact_pack.CompletePack), getattr(built, "missing", None)
    return fact_store.freeze_pack(
        built, parquet_dir=env.parquet_dir, db_path=env.db_path)


# ══════════════════════════════════════════════════════════════════════════
# ① 涨停普查与归因分布
# ══════════════════════════════════════════════════════════════════════════

class TestCensus:
    def test_limit_up_census_runs_from_day_one(self, isolated_env):
        """「参数没到齐也能跑的那半」:涨停普查 + 涨停簇画像 + 结构性分布。"""
        pack = _seed(isolated_env)
        day = cov.compute_day(pack)
        assert day.limit_up_count == len(LIMIT_UPS)
        assert day.zaban_count == 1
        assert day.zaban_rate == pytest.approx(1 / 6)
        assert day.max_consec_days == 2
        # 半导体两只涨停 → 一个同日簇(白酒 / 医疗美容 / 科创板各自孤身,不成簇)
        assert day.cluster_count == 1
        assert day.census["clusters"][0]["l2Code"] == "801080.SI"

    def test_census_is_purely_structural_facts_from_the_pack(self, isolated_env):
        """⚠ 普查里装的全是事实包的列(板块 / ST / 申万二级),
        ⛔ 不含任何「被第 N 条边界排除」的判定 —— 那要边界参数。"""
        pack = _seed(isolated_env)
        census = cov.compute_day(pack).census
        assert census["byBoard"] == {"MAIN": 4, "STAR": 1}
        assert census["stCount"] == 1
        assert {b["l2Code"] for b in census["byL2"]} == {"801080.SI", "801125.SI", "801156.SI"}
        assert "excluded" not in str(census)

    def test_star_and_bse_limit_ups_stay_in_the_headline_denominator(self, isolated_env):
        """K9 第一层排除科创板 → 它的涨停结构上永远覆盖不到,而这**正是要看见的**
        (架构 §5.2:覆盖率衡量的是事实层与策略层的**联合**漏检)。
        想看「池子里那部分」看 `coverage_in_pool`。"""
        pack = _seed(isolated_env)
        day = cov.compute_day(pack)
        assert "688001.SH" in {m.ts_code for m in day.misses}
        assert day.limit_up_count == 5

    def test_miss_reasons_come_from_a_closed_enum(self, isolated_env):
        pack = _seed(isolated_env)
        day = cov.compute_day(pack)
        assert {m.reason for m in day.misses} <= set(cov.MISS_REASONS)


# ══════════════════════════════════════════════════════════════════════════
# ② coverage_all ⛔ 不读任何参数包
# ══════════════════════════════════════════════════════════════════════════

class TestHeadlineNeverReadsParams:
    def test_compute_day_signature_cannot_even_accept_params(self):
        """🔴 结构性保证:`compute_day` 的签名里**收不下**参数包。
        策略侧的信息只能经 `dispositions` 这条**数据**通道进来,不通过 import。"""
        import inspect
        names = set(inspect.signature(cov.compute_day).parameters)
        assert names == {"pack", "listing", "dispositions"}
        assert not any("param" in n.lower() for n in names)

    def test_coverage_all_only_needs_the_pack_and_yesterdays_codes(self, isolated_env):
        pack = _seed(isolated_env)
        listing = cov.ListingSnapshot(
            trade_date=D0, codes=frozenset({"600001.SH", "600004.SH", "600009.SH"}))
        day = cov.compute_day(pack, listing=listing)
        assert day.covered_count == 2                    # 600001 / 600004
        assert day.coverage_all == pytest.approx(2 / 5)
        assert day.listing_size == 3
        # 昨天在清单里的票不进漏检归因
        assert {m.ts_code for m in day.misses} == {"600002.SH", "688001.SH", "600005.SH"}

    def test_the_observe_branch_still_counts_toward_coverage(self, isolated_env):
        """K9 §八:**观察分支仍进覆盖率** —— 覆盖率只看「昨天在不在清单里」,
        与三分支判定无关。`ListingSnapshot` 因此只有 `codes`,⛔ 没有 verdict 字段。"""
        import dataclasses
        fields = {f.name for f in dataclasses.fields(cov.ListingSnapshot)}
        assert fields == {"trade_date", "codes"}


# ══════════════════════════════════════════════════════════════════════════
# ③ NULL 不是 0
# ══════════════════════════════════════════════════════════════════════════

class TestNullIsNotZero:
    def test_no_listing_yesterday_means_coverage_all_is_null(self, isolated_env):
        """上线首日 / 参数未配置的日子:昨天**没有清单**。
        ⛔ 写 0 会把「还没开始」读成「一只都没覆盖到」。"""
        pack = _seed(isolated_env)
        day = cov.compute_day(pack)
        assert day.coverage_all is None
        assert day.covered_count is None
        assert day.listing_trade_date is None
        assert {m.reason for m in day.misses} == {cov.REASON_NO_LISTING}

    def test_missing_disposition_means_coverage_in_pool_is_null(self, isolated_env):
        """🔴 §5.8.1 逐字:`coverage_in_pool` 依赖边界参数 → 参数缺失时写 **NULL**。"""
        pack = _seed(isolated_env)
        day = cov.compute_day(
            pack, listing=cov.ListingSnapshot(D0, frozenset({"600001.SH"})))
        assert day.coverage_all == pytest.approx(1 / 5)
        assert day.coverage_in_pool is None
        assert day.in_pool_denominator is None
        assert {m.reason for m in day.misses} == {cov.REASON_NO_DISPOSITION}

    def test_zero_limit_ups_gives_null_not_zero(self, isolated_env):
        """一天一只涨停都没有 → 分母 0 → 比率 `None`(⛔ 不是 0%)。"""
        env = isolated_env
        pack = _seed(env)
        empty = cov.compute_day(
            _EmptyPack(pack), listing=cov.ListingSnapshot(D0, frozenset({"600001.SH"})))
        assert empty.limit_up_count == 0
        assert empty.coverage_all is None
        assert empty.zaban_rate is None
        assert empty.max_consec_days is None

    def test_in_pool_ratio_is_computed_once_dispositions_arrive(self, isolated_env):
        """S6 把 disposition 接上之后,同一天重算就能把两个数都填上。"""
        pack = _seed(isolated_env)
        disp = [
            cov.DispositionRow("600001.SH", None, True, 3, True, False),
            cov.DispositionRow("600002.SH", None, True, 25, False, False),
            cov.DispositionRow("600004.SH", "白酒", False, None, False, False),
            cov.DispositionRow("688001.SH", "科创板", False, None, False, False),
            cov.DispositionRow("600005.SH", None, False, None, False, True),
        ]
        day = cov.compute_day(
            pack, listing=cov.ListingSnapshot(D0, frozenset({"600001.SH"})), dispositions=disp)
        assert day.in_pool_denominator == 3          # 600001 / 600002 / 600005
        assert day.covered_in_pool == 1
        assert day.coverage_in_pool == pytest.approx(1 / 3)
        by_code = {m.ts_code: m for m in day.misses}
        assert by_code["600002.SH"].reason == cov.REASON_RECALLED_NOT_SEATED
        assert by_code["600002.SH"].detail == "rank=25"
        assert by_code["600004.SH"].reason == cov.REASON_EXCLUDED_BY_BOUNDARY
        assert by_code["600004.SH"].detail == "白酒"
        assert by_code["600005.SH"].reason == cov.REASON_NEWS_EXCLUDED

    def test_not_recalled_is_distinct_from_excluded(self, isolated_env):
        pack = _seed(isolated_env)
        disp = [cov.DispositionRow(c, None, False, None, False, False) for c in LIMIT_UPS]
        day = cov.compute_day(pack, listing=cov.ListingSnapshot(D0, frozenset()), dispositions=disp)
        assert {m.reason for m in day.misses} == {cov.REASON_NOT_RECALLED}
        assert day.in_pool_denominator == 5


class _EmptyPack:
    """把一份真包的行换成「零涨停」的同形表(只为造分母 0 的场景)。"""

    def __init__(self, pack: fact_store.FactPack):
        self._pack = pack

    def __getattr__(self, name):
        return getattr(self._pack, name)

    @property
    def rows(self) -> pl.DataFrame:
        return self._pack.rows.with_columns(
            pl.lit(False).alias("is_limit_up"),
            pl.lit(False).alias("is_limit_down"),
            pl.lit(False).alias("is_limit_open"),
            pl.lit(0, dtype=pl.Int64).alias("consec_limit_up_days"),
        )


# ══════════════════════════════════════════════════════════════════════════
# ④ 落表 / 读回 / ⛔ 不回填历史
# ══════════════════════════════════════════════════════════════════════════

class TestStore:
    def test_round_trip_keeps_null_as_null(self, isolated_env):
        env = isolated_env
        _seed(env)
        day = cov.refresh_day(trade_date=D0, parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert day is not None
        rows = cov_store.load_coverage_days(db_path=env.db_path)
        assert len(rows) == 1
        assert rows[0]["coverage_all"] is None, "NULL 落表读回必须仍是 NULL,⛔ 不折成 0"
        assert rows[0]["coverage_in_pool"] is None
        assert rows[0]["limit_up_count"] == 5
        assert rows[0]["census_json"]["byBoard"] == {"MAIN": 4, "STAR": 1}

    def test_a_day_without_a_frozen_pack_writes_nothing(self, isolated_env):
        """⛔ **不回填历史覆盖率**(§5.8.1 末):没有冻结包的日子不编一行 0。"""
        env = isolated_env
        _seed(env)
        assert cov.refresh_day(
            trade_date=D1, parquet_dir=env.parquet_dir, db_path=env.db_path) is None
        assert cov_store.load_coverage_days(db_path=env.db_path) == []

    def test_recompute_replaces_the_old_attribution_wholesale(self, isolated_env):
        """昨天的清单定稿后重算:原先标 `no_listing` 的归因必须**整批**换掉,
        ⛔ 不能让新旧两代归因混在同一天里。"""
        env = isolated_env
        _seed(env)
        cov.refresh_day(trade_date=D0, parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert cov_store.miss_reason_counts(db_path=env.db_path) == {cov.REASON_NO_LISTING: 5}

        cov.refresh_day(
            trade_date=D0,
            listing=cov.ListingSnapshot(D0, frozenset({"600001.SH", "600002.SH"})),
            dispositions=[cov.DispositionRow(c, None, True, 9, False, False) for c in LIMIT_UPS],
            parquet_dir=env.parquet_dir, db_path=env.db_path,
        )
        counts = cov_store.miss_reason_counts(db_path=env.db_path)
        assert counts == {cov.REASON_RECALLED_NOT_SEATED: 3}
        rows = cov_store.load_coverage_days(db_path=env.db_path)
        assert rows[0]["coverage_all"] == pytest.approx(2 / 5)
        assert rows[0]["listing_trade_date"] == "20240304"

    def test_misses_carry_the_sw_l2_binding(self, isolated_env):
        """归因必须带**申万二级** —— 「漏掉的是哪一类票」靠它回答(裁定 3)。"""
        env = isolated_env
        _seed(env)
        cov.refresh_day(trade_date=D0, parquet_dir=env.parquet_dir, db_path=env.db_path)
        misses = {m["ts_code"]: m for m in cov_store.load_misses(D0, db_path=env.db_path)}
        assert misses["600004.SH"]["sw_l2_code"] == "801125.SI"
        assert misses["600004.SH"]["sw_l2_name"] == "白酒Ⅱ"
        assert misses["688001.SH"]["board"] == "STAR"
        assert misses["600001.SH"]["consec_limit_up_days"] == 2


# ══════════════════════════════════════════════════════════════════════════
# ⑤ API
# ══════════════════════════════════════════════════════════════════════════

class TestApi:
    def test_endpoint_requires_a_token(self, client):
        assert client.get("/api/v1/scoreboard/coverage").status_code == 401

    def test_empty_history_is_an_honest_empty_list(self, client, AUTH):
        body = client.get("/api/v1/scoreboard/coverage", headers=AUTH).json()
        assert body["days"] == [] and body["latestMisses"] == []
        assert body["missReasonCounts"] == {}

    def test_endpoint_preserves_null_over_the_wire(self, api_env, client, AUTH):
        _seed(api_env)
        cov.refresh_day(trade_date=D0, parquet_dir=api_env.parquet_dir, db_path=api_env.db_path)
        body = client.get("/api/v1/scoreboard/coverage?window=5", headers=AUTH).json()
        assert len(body["days"]) == 1
        d = body["days"][0]
        assert d["tradeDate"] == "20240304"
        assert d["coverageAll"] is None, "⛔ null 不许在传输层被折成 0"
        assert d["coverageInPool"] is None
        assert d["limitUpCount"] == 5
        assert d["census"]["byBoard"] == {"MAIN": 4, "STAR": 1}
        assert len(body["latestMisses"]) == 5
        assert body["missReasonCounts"] == {cov.REASON_NO_LISTING: 5}

    @pytest.mark.parametrize("w", ["0", "1", "99999"])
    def test_window_is_clamped_into_range(self, api_env, client, AUTH, w):
        _seed(api_env)
        cov.refresh_day(trade_date=D0, parquet_dir=api_env.parquet_dir, db_path=api_env.db_path)
        body = client.get(f"/api/v1/scoreboard/coverage?window={w}", headers=AUTH).json()
        assert 1 <= body["window"] <= 250
        assert len(body["days"]) == 1
