"""事实层与事实包单测(V2.5.0 S3,PROJECT_PLAN §6 S3 验收逐条)。

本文件逐条对应 S3 的六条验收 + §5.3 的设计承诺:

| # | 验收 | 本文件的 section |
|---|---|---|
| 1 | 类型级:`freeze_pack(IncompletePack)` 不通过 | ① |
| 2 | 冻结不可覆盖:同 `(trade_date, pack_version)` 二次冻结抛错 | ② |
| 3 | 中位数三路等价:现算 ≡ 落表 ≡ 读回 | ③ |
| 4 | 停牌**多向**夹具(全天停牌混进 daily → 排除且计数 1;盘中临时停牌 / R 涨停 → **计入**) | ④ |
| 5 | 缺一个上游分区 → `IncompletePack` 且 `missing` 列出具体表名 | ⑤ |
| 6 | 保留策略:parquet 滚动裁剪,**清单行永不裁剪** | ⑥ |

另加两组结构性守门(§5.3.2 / 架构 §二 边界①)见 `test_v250_s3_facts_guard.py`。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from neckline.db import connection
from neckline.facts import completeness, industry
from neckline.facts import pack as fact_pack
from neckline.facts import store as fact_store
from tests.conftest import (
    insert_namechange,
    insert_stock_basic,
    insert_sw_members,
    insert_trade_cal,
    write_daily_fixture,
)

D0 = date(2024, 3, 4)
D1 = date(2024, 3, 5)

#: 三个二级行业:半导体 3 只 / 白酒Ⅱ 2 只 / 医疗美容 2 只。
#: ⚠ 白酒Ⅱ 与医疗美容都只有 2 只 —— 这正是 §4.5 里最小的那几个行业的量级。
#: 事实层**照样为它们产出中位数**(⛔ 无最小成员数门槛,那是策略参数)。
UNIVERSE = {
    "600001.SH": ("801080.SI", "半导体"),
    "600002.SH": ("801080.SI", "半导体"),
    "600003.SH": ("801080.SI", "半导体"),
    "600004.SH": ("801125.SI", "白酒Ⅱ"),
    "600005.SH": ("801125.SI", "白酒Ⅱ"),
    "300001.SZ": ("801156.SI", "医疗美容"),
    "300002.SZ": ("801156.SI", "医疗美容"),
}

_TABLES = ("daily", "daily_basic", "adj_factor", "moneyflow_dc", "limit_derived", "suspend_d")


def _seed_meta(env) -> None:
    insert_trade_cal(env, [D0, D1])
    insert_stock_basic(env, [
        {"ts_code": c, "name": f"示例{c[:6]}",
         "market": "创业板" if c.startswith("3") else "主板",
         "list_date": date(2020, 1, 2)}
        for c in UNIVERSE
    ])
    insert_namechange(env, [
        {"ts_code": c, "name": f"示例{c[:6]}", "start_date": date(2020, 1, 2)} for c in UNIVERSE
    ])
    insert_sw_members(env, [
        {"ts_code": c, "l2_code": l2, "l2_name": name} for c, (l2, name) in UNIVERSE.items()
    ])


def _seed_day(
    env,
    day: date = D0,
    *,
    closes: dict | None = None,
    suspend_rows: list[dict] | None = None,
    limit_rows: list[dict] | None = None,
    skip_tables: tuple = (),
    extra_daily: list[dict] | None = None,
) -> None:
    """铺一整天的上游分区。`closes` 给 `ts_code -> close`(`pre_close` 恒 10.0)。"""
    closes = closes or {c: 10.0 for c in UNIVERSE}
    daily_rows = [
        {"ts_code": c, "open": 10.0, "high": max(10.0, px), "low": min(10.0, px),
         "close": px, "pre_close": 10.0, "change": px - 10.0,
         "pct_chg": (px / 10.0 - 1) * 100, "vol": 100000.0, "amount": 1_000_000.0}
        for c, px in closes.items()
    ]
    daily_rows += extra_daily or []
    if "daily" not in skip_tables:
        write_daily_fixture(env, "daily", day, daily_rows)
    if "daily_basic" not in skip_tables:
        write_daily_fixture(env, "daily_basic", day, [
            {"ts_code": r["ts_code"], "turnover_rate": 5.0, "turnover_rate_f": 6.0,
             "volume_ratio": 1.2, "circ_mv": 1e6, "total_mv": 2e6, "free_share": 1e5}
            for r in daily_rows
        ])
    if "adj_factor" not in skip_tables:
        write_daily_fixture(env, "adj_factor", day, [
            {"ts_code": r["ts_code"], "adj_factor": 1.0} for r in daily_rows
        ])
    if "moneyflow_dc" not in skip_tables:
        write_daily_fixture(env, "moneyflow_dc", day, [
            {"ts_code": r["ts_code"], "net_amount": 1000.0, "net_amount_rate": 0.5,
             "buy_elg_amount": 500.0, "buy_lg_amount": 400.0} for r in daily_rows
        ])
    if "limit_derived" not in skip_tables:
        write_daily_fixture(env, "limit_derived", day, limit_rows or [
            {"ts_code": "600001.SH", "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
             "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
             "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": 1},
        ])
    if "suspend_d" not in skip_tables:
        # 当日零停牌是**正常且有信息量**的结果(「今天没人停牌」≠「今天没查」),照样落盘;
        # 空表也要给显式 dtype(v1.3.5 血训:空分区是脏基准的唯一来源)。
        from neckline.data.market_data import write_table_day
        rows = suspend_rows or []
        df = (
            pl.DataFrame([{**r, "trade_date": day} for r in rows])
            if rows
            else pl.DataFrame(schema={
                "ts_code": pl.String, "trade_date": pl.Date, "suspend_type": pl.String})
        )
        write_table_day("suspend_d", day, df, parquet_dir=env.parquet_dir)


def _build(env, day: date = D0):
    return fact_pack.build(day, parquet_dir=env.parquet_dir, db_path=env.db_path)


def _freeze(env, built, origin: str = fact_store.ORIGIN_LIVE):
    return fact_store.freeze_pack(
        built, origin=origin, parquet_dir=env.parquet_dir, db_path=env.db_path)


# ══════════════════════════════════════════════════════════════════════════
# ① 类型级:数据未到齐 → 不冻结,是**类型错误**不是布尔标志
# ══════════════════════════════════════════════════════════════════════════

class TestFreezeOnlyAcceptsCompletePack:
    def test_incomplete_pack_has_neither_rows_nor_freeze(self):
        """🔴 §5.3.2 纪律 1 的结构判据:`IncompletePack` **没有 rows、没有 freeze**。
        「不冻结」于是不可能被谁忘了检查。⛔ 不许给它加这两样东西。"""
        inc = fact_pack.IncompletePack(trade_date=D0, pack_version="fp-1", missing=("daily:没有",))
        assert not hasattr(inc, "rows")
        assert not any("freeze" in n for n in dir(inc)), "IncompletePack 上冒出了 freeze 相关成员"

    def test_freeze_pack_rejects_incomplete_pack_at_runtime(self, isolated_env):
        inc = fact_pack.IncompletePack(trade_date=D0, pack_version="fp-1", missing=("daily:没有",))
        with pytest.raises(TypeError, match="只接受 CompletePack"):
            _freeze(isolated_env, inc)

    def test_nothing_is_written_when_the_pack_is_incomplete(self, isolated_env):
        env = isolated_env
        inc = fact_pack.IncompletePack(trade_date=D0, pack_version="fp-1", missing=("daily:没有",))
        with pytest.raises(TypeError):
            _freeze(env, inc)
        assert fact_store.list_packs(db_path=env.db_path) == []


# ══════════════════════════════════════════════════════════════════════════
# ② 冻结不可覆盖
# ══════════════════════════════════════════════════════════════════════════

class TestFreezeIsNotOverwritable:
    def test_second_freeze_of_the_same_date_and_version_raises(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        _seed_day(env)
        first = _freeze(env, _build(env))
        with pytest.raises(fact_store.PackAlreadyFrozen, match="不可覆盖"):
            _freeze(env, _build(env))
        # 清单里仍然只有那**一行**,指纹没被动过。
        rows = fact_store.list_packs(db_path=env.db_path)
        assert len(rows) == 1
        assert fact_store.load_pack(
            D0, parquet_dir=env.parquet_dir, db_path=env.db_path
        ).content_fingerprint == first.content_fingerprint

    def test_a_new_pack_version_is_the_only_way_to_re_freeze(self, isolated_env):
        """口径变了就发新 `pack_version` —— ⛔ 没有静默重写这条路(§5.3.2 纪律 3)。"""
        env = isolated_env
        _seed_meta(env)
        _seed_day(env)
        _freeze(env, _build(env))
        import dataclasses
        v2 = dataclasses.replace(_build(env), pack_version="fp-99")
        _freeze(env, v2)
        assert {r[1] for r in fact_store.list_packs(db_path=env.db_path)} == {fact_pack.PACK_VERSION, "fp-99"}

    def test_the_manifest_row_only_ever_says_frozen(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        _seed_day(env)
        _freeze(env, _build(env))
        with connection(env.db_path) as conn:
            states = {r[0] for r in conn.execute("SELECT state FROM fact_packs").fetchall()}
        assert states == {"frozen"}, "「今天没跑成」是**没有行**,⛔ 不是一行标着 incomplete"


# ══════════════════════════════════════════════════════════════════════════
# ③ 中位数三路等价:现算 ≡ 落表 ≡ 读回
# ══════════════════════════════════════════════════════════════════════════

class TestIndustryMedianThreeWays:
    def test_recompute_equals_stored_equals_read_back(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        # 半导体三只:+10% / +2% / −3% → 中位数 +2%;白酒两只:+1% / +5% → 中位数 +3%
        _seed_day(env, closes={
            "600001.SH": 11.0, "600002.SH": 10.2, "600003.SH": 9.7,
            "600004.SH": 10.1, "600005.SH": 10.5,
            "300001.SZ": 10.0, "300002.SZ": 10.0,
        })
        built = _build(env)

        # 路① 现算(build 装配时算出来的那一份)
        computed = {r.l2_code: r for r in built.industry_rows}
        assert computed["801080.SI"].median_ret == pytest.approx(0.02)
        assert computed["801080.SI"].member_count == 3
        assert computed["801125.SI"].median_ret == pytest.approx(0.03)

        _freeze(env, built)
        # 路② 落表读回
        stored = {r.l2_code: r for r in industry.load_day(D0, db_path=env.db_path)}
        assert set(stored) == set(computed)
        for code, row in computed.items():
            assert stored[code].median_ret == pytest.approx(row.median_ret)
            assert stored[code].member_count == row.member_count

        # 路③ 事实包大表里贴的 `sw_l2_median_ret` 与 `rel_strength_1d`
        got = fact_store.load_pack(D0, parquet_dir=env.parquet_dir, db_path=env.db_path).rows
        by_code = {r["ts_code"]: r for r in got.iter_rows(named=True)}
        assert by_code["600001.SH"]["sw_l2_median_ret"] == pytest.approx(0.02)
        assert by_code["600001.SH"]["rel_strength_1d"] == pytest.approx(0.10 - 0.02)
        assert by_code["600004.SH"]["rel_strength_1d"] == pytest.approx(0.01 - 0.03)

    def test_every_industry_gets_a_median_no_matter_how_few_members(self, isolated_env):
        """🔴 遗留 1 的机器判据:事实层**没有最小成员数门槛**。

        「成员数不足则不产出强度」直接决定哪些票拿不到相对强度、进不了形态召回,
        是**策略主张**,值是 §8.2 第 16 项待标定参数,住策略层。
        `report/industry_strength.py::_MIN_MEMBERS = 5` 已随该模块整体退役。"""
        env = isolated_env
        _seed_meta(env)
        _seed_day(env)
        built = _build(env)
        counts = {r.l2_code: r.member_count for r in built.industry_rows}
        assert counts == {"801080.SI": 3, "801125.SI": 2, "801156.SI": 2}
        assert min(counts.values()) == 2, "两只成员的行业也必须拿到中位数"

    def test_no_threshold_constant_survives_anywhere_in_the_fact_layer(self):
        """门槛不许以任何名字回到事实层。"""
        for mod in (industry, fact_pack, fact_store, completeness):
            for name in dir(mod):
                assert "MIN_MEMBER" not in name.upper(), f"{mod.__name__}.{name}"


# ══════════════════════════════════════════════════════════════════════════
# ④ 停牌:**多向**夹具(§5.3.4,🔴 裁定 12 返工后)
# ══════════════════════════════════════════════════════════════════════════

class TestSuspensionIsAnAssertionNotAnAssumption:
    def test_a_full_day_halt_that_sneaks_into_daily_is_excluded_and_counted(self, isolated_env):
        """人造一条**全天停牌**(`suspend_timing` 为空)的票混进 `daily`
        → 被排除出中位数、计数 = 1。

        §4.6 实测:全天停牌的票**天然不在 daily 分区里**(150 天 2001 行 0 命中)。
        真出现了就是数据事故,⛔ 不静默、不掩盖(WARNING +
        `fact_packs.suspend_anomaly_count`)。"""
        env = isolated_env
        _seed_meta(env)
        _seed_day(
            env,
            closes={"600001.SH": 11.0, "600002.SH": 10.2, "600003.SH": 9.7,
                    "600004.SH": 10.0, "600005.SH": 10.0,
                    "300001.SZ": 10.0, "300002.SZ": 10.0},
            suspend_rows=[{"ts_code": "600001.SH", "suspend_type": "S"}],
        )
        built = _build(env)
        assert built.suspend_anomaly_count == 1
        semi = {r.l2_code: r for r in built.industry_rows}["801080.SI"]
        # 600001 被剔除 → 只剩 +2% / −3%,中位数 = −0.5%
        assert semi.member_count == 2
        assert semi.suspended_excluded == 1
        assert semi.median_ret == pytest.approx(-0.005)

        frozen = _freeze(env, built)
        assert frozen.suspend_anomaly_count == 1
        # 那一行**仍在事实包里**(K9 第一层第 6 条要靠 `suspend_flag` 把它排除掉)
        rows = {r["ts_code"]: r for r in frozen.rows.iter_rows(named=True)}
        assert rows["600001.SH"]["suspend_flag"] == "S"
        assert frozen.market["suspendAnomaly"] == {
            "total": 1, "codes": ["600001.SH"],
            "intradayCounted": 0, "intradayTimings": {},
        }

    def test_an_intraday_halt_is_counted_into_the_median_and_is_not_an_anomaly(self, isolated_env):
        """🔴 **裁定 12**(2026-08-20 用户对 S3 的返工):`suspend_timing` 非空 =
        **盘中临时停牌**,那只票当天**照常交易、照常有完整涨跌幅**
        → **照常计入行业中位数**,且⛔ **不算异常**。

        150 日实测:盘中停牌 36 行里 **35 行**都在 daily、分布在 25/150 天 ——
        把它当异常等于让告警从此没人看,把它剔出中位数等于每 6 天就悄悄抹掉一只
        正常交易的票。"""
        env = isolated_env
        _seed_meta(env)
        _seed_day(
            env,
            closes={"600001.SH": 11.0, "600002.SH": 10.2, "600003.SH": 9.7,
                    "600004.SH": 10.0, "600005.SH": 10.0,
                    "300001.SZ": 10.0, "300002.SZ": 10.0},
            suspend_rows=[
                {"ts_code": "600001.SH", "suspend_type": "S", "suspend_timing": "9:30-9:40"},
            ],
        )
        built = _build(env)
        assert built.suspend_anomaly_count == 0, "盘中临时停牌是常态,⛔ 不是异常"
        semi = {r.l2_code: r for r in built.industry_rows}["801080.SI"]
        assert semi.member_count == 3, "盘中停过十分钟的票当天正常交易,必须**计入**中位数"
        assert semi.suspended_excluded == 0
        assert semi.median_ret == pytest.approx(0.02)   # +10% / +2% / −3% 的中位数

        frozen = _freeze(env, built)
        rows = {r["ts_code"]: r for r in frozen.rows.iter_rows(named=True)}
        assert rows["600001.SH"]["suspend_flag"] == "I", "⛔ 不许把盘中停牌折回 S"
        # 判别证据仍然留在包里 —— 「那天有几只票盘中停过」事后仍查得到。
        assert frozen.market["suspendAnomaly"] == {
            "total": 0, "codes": [],
            "intradayCounted": 1, "intradayTimings": {"600001.SH": "9:30-9:40"},
        }

    def test_the_two_kinds_of_halt_never_collapse_into_one_flag(self, isolated_env):
        """同一天两类都出现:全天停牌进异常并被剔除,盘中停牌照常计入。"""
        env = isolated_env
        _seed_meta(env)
        _seed_day(
            env,
            closes={"600001.SH": 11.0, "600002.SH": 10.2, "600003.SH": 9.7,
                    "600004.SH": 10.0, "600005.SH": 10.0,
                    "300001.SZ": 10.0, "300002.SZ": 10.0},
            suspend_rows=[
                {"ts_code": "600001.SH", "suspend_type": "S", "suspend_timing": "9:30-9:40"},
                {"ts_code": "600002.SH", "suspend_type": "S", "suspend_timing": None},
            ],
        )
        built = _build(env)
        assert built.suspend_anomaly_count == 1, "只有全天停牌那一只算异常"
        detail = built.market["suspendAnomaly"]
        assert detail["total"] == 1 and detail["codes"] == ["600002.SH"]
        assert detail["intradayCounted"] == 1
        assert detail["intradayTimings"] == {"600001.SH": "9:30-9:40"}
        semi = {r.l2_code: r for r in built.industry_rows}["801080.SI"]
        assert semi.member_count == 2 and semi.suspended_excluded == 1
        rows = {r["ts_code"]: r for r in built.rows.iter_rows(named=True)}
        assert rows["600001.SH"]["suspend_flag"] == "I"
        assert rows["600002.SH"]["suspend_flag"] == "S"

    def test_the_flag_resolver_is_the_single_implementation(self):
        """判别只有一处实现,四值闭合(⛔ 下游不许自己再看一次 `suspend_timing`)。"""
        f = fact_pack._suspend_flag_of
        assert f("S", None) == fact_pack.SUSPEND_HALTED
        assert f("S", "9:30-9:40") == fact_pack.SUSPEND_INTRADAY
        assert f("R", None) == fact_pack.SUSPEND_RESUMED
        assert f("R", "9:30-9:40") == fact_pack.SUSPEND_RESUMED
        assert f("", None) == fact_pack.SUSPEND_NONE

    def test_an_R_row_at_limit_up_is_counted_in(self, isolated_env):
        """🔴 `suspend_type='R'` = **复牌**,当天正常交易(§4.6 实测 20230103 的
        000045.SZ 涨停 +10.01%、成交 14639 手)。⛔ 认 R 会误杀真实交易日。"""
        env = isolated_env
        _seed_meta(env)
        _seed_day(
            env,
            closes={"600001.SH": 11.0, "600002.SH": 10.2, "600003.SH": 9.7,
                    "600004.SH": 10.0, "600005.SH": 10.0,
                    "300001.SZ": 10.0, "300002.SZ": 10.0},
            suspend_rows=[{"ts_code": "600001.SH", "suspend_type": "R"}],
        )
        built = _build(env)
        assert built.suspend_anomaly_count == 0, "R 不是停牌,⛔ 不许计进异常"
        semi = {r.l2_code: r for r in built.industry_rows}["801080.SI"]
        assert semi.member_count == 3, "复牌票必须**计入**中位数"
        assert semi.suspended_excluded == 0
        assert semi.median_ret == pytest.approx(0.02)
        rows = {r["ts_code"]: r for r in built.rows.iter_rows(named=True)}
        assert rows["600001.SH"]["suspend_flag"] == "R"
        assert rows["600001.SH"]["ret_1d"] == pytest.approx(0.10)

    def test_stocks_outside_the_suspend_list_are_flagged_none(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        _seed_day(env)
        rows = {r["ts_code"]: r for r in _build(env).rows.iter_rows(named=True)}
        assert {r["suspend_flag"] for r in rows.values()} == {"none"}


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 完整性:缺一个上游分区 → IncompletePack,`missing` 点名到表
# ══════════════════════════════════════════════════════════════════════════

class TestCompleteness:
    @pytest.mark.parametrize("table", _TABLES)
    def test_missing_one_partition_names_that_table(self, isolated_env, table):
        env = isolated_env
        _seed_meta(env)
        _seed_day(env, skip_tables=(table,))
        built = _build(env)
        assert isinstance(built, fact_pack.IncompletePack)
        assert any(m.startswith(f"{table}:") for m in built.missing), built.missing

    def test_dense_table_with_zero_rows_is_a_gap(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        _seed_day(env)
        from neckline.data.market_data import write_table_day
        write_table_day("daily", D0, pl.DataFrame(schema={
            "ts_code": pl.String, "trade_date": pl.Date, "close": pl.Float64,
            "pre_close": pl.Float64}), parquet_dir=env.parquet_dir)
        built = _build(env)
        assert isinstance(built, fact_pack.IncompletePack)
        assert any(m.startswith("daily:") and "0 行" in m for m in built.missing)

    def test_sparse_table_with_zero_rows_is_not_a_gap(self, isolated_env):
        """`limit_derived` 是稀疏表、`suspend_d` 实测只有几行 —— 给它们设行数下限
        等于把一个平静的日子判成故障(见 `completeness` 模块 docstring)。"""
        env = isolated_env
        _seed_meta(env)
        _seed_day(env, limit_rows=[], suspend_rows=[])
        assert isinstance(_build(env), fact_pack.CompletePack)

    def test_a_day_with_no_limit_ups_at_all_still_builds(self, isolated_env):
        """「当日一只涨停都没有」是**合法的市场事实**,不是故障。

        ⚠ 判据必须是「分区里有没有 `ts_code` 列」而不是 `is_empty()`:0 行的分区
        可能只带一列 `trade_date`,拿它去 join 会抛「找不到 ts_code」——
        那正是把一个平静的日子读成故障。"""
        env = isolated_env
        _seed_meta(env)
        _seed_day(env)
        from neckline.data.market_data import write_table_day
        write_table_day("limit_derived", D0, pl.DataFrame(schema={"trade_date": pl.Date}),
                        parquet_dir=env.parquet_dir)
        built = _build(env)
        assert isinstance(built, fact_pack.CompletePack)
        rows = built.rows
        assert rows.height == len(UNIVERSE)
        assert rows["is_limit_up"].sum() == 0
        assert rows["consec_limit_up_days"].sum() == 0
        assert built.market["limitMap"]["limitUpCount"] == 0
        assert built.market["limitMap"]["zabanRate"] is None

    def test_empty_sw_membership_is_a_gap(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [D0, D1])
        insert_stock_basic(env, [{"ts_code": c} for c in UNIVERSE])
        _seed_day(env)
        built = _build(env)
        assert isinstance(built, fact_pack.IncompletePack)
        assert any(m.startswith("sw_industry_member:") for m in built.missing)

    def test_non_trading_day_is_a_gap(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        _seed_day(env, day=date(2024, 3, 9))     # 未登记为交易日
        built = _build(env, date(2024, 3, 9))
        assert isinstance(built, fact_pack.IncompletePack)
        assert any(m.startswith("trade_cal:") for m in built.missing)

    def test_sources_record_every_upstream_partition_with_its_row_count(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        _seed_day(env)
        comp = completeness.check(D0, parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert comp.ok
        names = {s.name: s for s in comp.sources}
        assert set(_TABLES) <= set(names)
        assert names["daily"].rows == len(UNIVERSE)
        assert names["sw_industry_member"].rows == len(UNIVERSE)


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 保留策略 + 只读语义
# ══════════════════════════════════════════════════════════════════════════

class TestRetentionAndReadOnly:
    def test_trim_removes_parquet_but_never_the_manifest_row(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        for d in (D0, D1):
            _seed_day(env, day=d)
            _freeze(env, _build(env, d))

        removed = fact_store.trim_parquet(keep=1, parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert removed == [D0]
        assert len(fact_store.list_packs(db_path=env.db_path)) == 2, "⛔ 清单行永不裁剪"
        old = fact_store.load_pack(D0, parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert old.content_fingerprint          # 指纹仍查得到
        with pytest.raises(FileNotFoundError):
            _ = old.rows                        # ⛔ 不返回空表冒充「那天没数据」

    def test_dry_run_touches_nothing(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        for d in (D0, D1):
            _seed_day(env, day=d)
            _freeze(env, _build(env, d))
        assert fact_store.trim_parquet(
            keep=1, parquet_dir=env.parquet_dir, db_path=env.db_path, dry_run=True) == [D0]
        assert fact_store.load_pack(
            D0, parquet_dir=env.parquet_dir, db_path=env.db_path).rows.height == len(UNIVERSE)

    def test_freezing_leaves_no_staging_leftovers(self, isolated_env):
        """写序用的临时目录必须收干净 —— 它在 `parquet_dir` 里面(`os.replace` 要求
        同一文件系统),留着会在生产上慢慢漏磁盘。"""
        env = isolated_env
        _seed_meta(env)
        for d in (D0, D1):
            _seed_day(env, day=d)
            _freeze(env, _build(env, d))
        table_root = env.parquet_dir / fact_store.PARQUET_TABLE
        assert not (table_root / ".staging").exists()
        assert sorted(p.name for p in table_root.iterdir()) == ["year=2024"]

    def test_rows_is_a_fresh_copy_every_time(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        _seed_day(env)
        _freeze(env, _build(env))
        fp = fact_store.load_pack(D0, parquet_dir=env.parquet_dir, db_path=env.db_path)
        a = fp.rows
        a.drop_in_place("close")
        assert "close" in fp.rows.columns, "调用方改自己的副本不该弄脏别人"

    def test_field_rejects_undeclared_columns(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        _seed_day(env)
        _freeze(env, _build(env))
        fp = fact_store.load_pack(D0, parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert fp.field("close").len() == len(UNIVERSE)
        with pytest.raises(KeyError):
            fp.field("upside_room_mech_pct")

    def test_latest_pack_survives_a_day_that_did_not_run(self, isolated_env):
        """裁定 5 的「**保留上一份冻结结果**」在事实层的落地:`fact_packs` 是
        `INSERT` only,今天没跑成动不了昨天那一行。"""
        env = isolated_env
        _seed_meta(env)
        _seed_day(env, day=D0)
        _freeze(env, _build(env, D0))
        _seed_day(env, day=D1, skip_tables=("moneyflow_dc",))
        assert isinstance(_build(env, D1), fact_pack.IncompletePack)
        latest = fact_store.latest_pack(
            on_or_before=D1, parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert latest is not None and latest.trade_date == D0


# ══════════════════════════════════════════════════════════════════════════
# ⑦ 区间读取的两条硬断言(§5.4.2 策略契约第三条)
# ══════════════════════════════════════════════════════════════════════════

_SOME_COLS = ["ts_code", "close"]


class TestLoadPackRange:
    def test_reading_past_as_of_is_refused(self, isolated_env):
        env = isolated_env
        with pytest.raises(ValueError, match="截止到当日"):
            fact_store.load_pack_range(
                D0, D1, as_of=D0, columns=_SOME_COLS, db_path=env.db_path)

    def test_span_longer_than_max_lookback_is_refused(self, isolated_env):
        env = isolated_env
        far = date(2026, 1, 5)
        insert_trade_cal(env, [d for d in _weekdays(date(2024, 3, 4), far)])
        with pytest.raises(ValueError, match="MAX_LOOKBACK_PACKS"):
            fact_store.load_pack_range(
                D0, far, as_of=far, columns=_SOME_COLS, db_path=env.db_path)

    def test_column_projection_is_mandatory(self):
        """🛑 §12 坑 1:120 个交易日读**全 41 列**实测 frame 185 MB / RSS 峰值 865 MB
        (10 列投影只要 53.6 MB / 270 MB),而生产是 2 vCPU / 1.6 G、历史上 700M cap
        被 OOM-kill 过。⛔ 不许把那条红线做成缺省路径 —— `columns` 是必填的。"""
        import inspect
        sig = inspect.signature(fact_store.load_pack_range)
        assert sig.parameters["columns"].default is inspect.Parameter.empty, (
            "`columns` 被给了默认值 —— 「读全部列」于是成了缺省路径,§12 坑 1 会重演")
        assert sig.parameters["as_of"].default is inspect.Parameter.empty

    def test_range_projects_columns_and_keeps_trade_date(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        for d in (D0, D1):
            _seed_day(env, day=d)
            _freeze(env, _build(env, d))
        got = fact_store.load_pack_range(
            D0, D1, as_of=D1, columns=["ts_code", "close"],
            parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert got.columns == ["trade_date", "ts_code", "close"]
        assert got.height == 2 * len(UNIVERSE)

    def test_range_rejects_columns_outside_the_pack(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        _seed_day(env)
        _freeze(env, _build(env))
        with pytest.raises(KeyError):
            fact_store.load_pack_range(
                D0, D0, as_of=D0, columns=["ts_code", "industry_rank"],
                parquet_dir=env.parquet_dir, db_path=env.db_path)


def _weekdays(start: date, end: date):
    from datetime import timedelta
    cur, out = start, []
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


# ══════════════════════════════════════════════════════════════════════════
# ⑧ 市场级读数(→ fact_packs.market_json)
# ══════════════════════════════════════════════════════════════════════════

class TestMarketReadings:
    def test_market_json_carries_the_limit_map_and_median(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        _seed_day(env, closes={
            "600001.SH": 11.0, "600002.SH": 10.2, "600003.SH": 9.7,
            "600004.SH": 10.1, "600005.SH": 10.5, "300001.SZ": 10.0, "300002.SZ": 10.0,
        })
        frozen = _freeze(env, _build(env))
        market = frozen.market
        assert market["limitMap"]["limitUpCount"] == 1
        assert market["limitMap"]["zabanRate"] == 0.0
        assert market["swCoverage"] == {"total": len(UNIVERSE), "missing": 0}
        assert market["industryCount"] == 3
        assert market["marketMedianRet"] == pytest.approx(0.01)

    def test_stocks_without_sw_membership_are_counted_not_hidden(self, isolated_env):
        env = isolated_env
        _seed_meta(env)
        insert_stock_basic(env, [{"ts_code": "600009.SH", "name": "无归属", "market": "主板",
                                  "list_date": date(2020, 1, 2)}])
        _seed_day(env, extra_daily=[
            {"ts_code": "600009.SH", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "pre_close": 10.0, "change": 0.0, "pct_chg": 0.0, "vol": 1.0, "amount": 1.0},
        ])
        frozen = _freeze(env, _build(env))
        assert frozen.market["swCoverage"]["missing"] == 1
        rows = {r["ts_code"]: r for r in frozen.rows.iter_rows(named=True)}
        assert rows["600009.SH"]["sw_l2_code"] is None
        assert rows["600009.SH"]["rel_strength_1d"] is None, "查无行业 → 相对强度算不出,⛔ 不补 0"
