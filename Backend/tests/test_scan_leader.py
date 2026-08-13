"""簇内龙头结构预计算表 `leader_structure_daily` 单测(plan §五 V2-④,RS 口径
与 tie-break 已按策略线 K7 交接稿需求 1a 对齐,见 `neckline/scan/leader.py`
模块头)。

覆盖:①`rs_rank`=RS20(20 日收益率)排名,tie-break 定死为 RS 降序→成交额
降序→ts_code 升序;②`limit_height` 直接搬运 `consecutive_days`,`amount_share`
归一到簇内总额,但**两者均不参与 `role_mech` 判断**(K7 需求 1a:连板高度只
用于未来的双尾警示,成交额只作 tie-break);③`role_mech` 纯粹从 `rs_rank`
派生,`rs_rank` 缺失(RS20 观测不足 `PRICE_WINDOW_DAYS`)→ `unknown`;
④确定性(同一天跑两次逐位相同);⑤三路等价(批/逐日/落表读回)。
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from neckline.db import connection
from neckline.scan import leader
from neckline.scan.corr import PRICE_WINDOW_DAYS
from tests.conftest import (
    business_days,
    insert_stock_basic,
    insert_trade_cal,
    write_daily_fixture,
)

D0 = date(2024, 5, 6)


def _clusters(rows):
    """`rows = [(cluster_key, ts_code, consecutive_days), ...]`。"""
    return pl.DataFrame(rows, schema=["cluster_key", "ts_code", "consecutive_days"], orient="row")


def _full_window(code_to_total_return: dict, n: int = PRICE_WINDOW_DAYS) -> list:
    """给定 `{ts_code: 目标 RS20 总收益}`,构造**恰好 `n` 天**的 `ret_1d` 序列
    (第 0 天带全部涨幅,其余 `n-1` 天为 0——`∏(1+r)-1` 与"一次到位"代数等价,
    只是为了凑够 `MIN_OBS_FOR_RS` 天观测,不影响相关性/RS 的计算口径)。"""
    rows = []
    for code, total in code_to_total_return.items():
        rows.append((code, 0, total))
        rows.extend((code, i, 0.0) for i in range(1, n))
    return rows


def _price_window(rows) -> pl.DataFrame:
    """`rows = [(ts_code, day_idx, ret_1d), ...]`,`day_idx` 只用来造出不同的
    `trade_date`(具体日期值不重要,函数只按 `ts_code` 分组求积)。"""
    return pl.DataFrame(
        [(code, date(2024, 1, 1) + timedelta(days=i), r) for code, i, r in rows],
        schema=["ts_code", "trade_date", "ret_1d"],
        orient="row",
    )


def _amounts(rows) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=["ts_code", "amount"], orient="row")


# ══════════════════════════════════════════════════════════════════════════
# ①②③ rs_rank(RS20 + tie-break)/ limit_height / amount_share / role_mech
# ══════════════════════════════════════════════════════════════════════════

def test_role_mech_is_pure_rs_rank_not_mixed_with_streak():
    """K7 需求 1a 核心修正:600001 连板最多但 RS20 表现最弱 → role_mech 仍是
    `elastic`(不是 leader);RS20 最强的 600003 才是 `leader`——role_mech 与
    连板高度(`limit_height`)完全独立。"""
    clusters = _clusters([
        ("K1", "600001.SH", 3),   # 连板最多,但价格表现最弱
        ("K1", "600002.SH", 2),
        ("K1", "600003.SH", 1),   # 连板最少,但 RS20 最强
    ])
    price = _price_window(_full_window({"600001.SH": -0.05, "600002.SH": 0.0, "600003.SH": 0.10}))
    amounts = _amounts([("600001.SH", 500_000.0), ("600002.SH", 300_000.0), ("600003.SH", 200_000.0)])

    out = leader.compute_leader_structure_for_day(D0, price, clusters, amounts)
    by_code = {r["ts_code"]: r for r in out.iter_rows(named=True)}

    # rs_rank 与 role_mech 现在同源(都只看 RS20):600003 最强 → rank1/leader
    assert by_code["600003.SH"]["rs_rank"] == 1
    assert by_code["600003.SH"]["role_mech"] == "leader"
    assert by_code["600002.SH"]["rs_rank"] == 2
    assert by_code["600002.SH"]["role_mech"] == "core"
    assert by_code["600001.SH"]["rs_rank"] == 3
    assert by_code["600001.SH"]["role_mech"] == "elastic"   # 连板最多但仍是 elastic

    # limit_height 仍照原样搬运 consecutive_days(继续产出,只是不进 role_mech)
    assert by_code["600001.SH"]["limit_height"] == 3
    assert by_code["600002.SH"]["limit_height"] == 2
    assert by_code["600003.SH"]["limit_height"] == 1

    # amount_share 归一,同样不影响 role_mech
    assert by_code["600001.SH"]["amount_share"] == pytest.approx(0.5)
    assert by_code["600002.SH"]["amount_share"] == pytest.approx(0.3)
    assert by_code["600003.SH"]["amount_share"] == pytest.approx(0.2)


def test_rs_rank_tiebreak_by_amount_then_ts_code():
    """K7 需求 1a 定死 tie-break:RS20 并列时按成交额降序,再按 ts_code 升序。"""
    clusters = _clusters([("K1", "600001.SH", 1), ("K1", "600002.SH", 1), ("K1", "600003.SH", 1)])
    # 三票 RS20 完全相同 → 全部进入 tie-break
    price = _price_window(_full_window({"600001.SH": 0.05, "600002.SH": 0.05, "600003.SH": 0.05}))
    amounts = _amounts([("600001.SH", 100.0), ("600002.SH", 300.0), ("600003.SH", 300.0)])

    out = leader.compute_leader_structure_for_day(D0, price, clusters, amounts)
    by_code = {r["ts_code"]: r for r in out.iter_rows(named=True)}

    # 600002/600003 成交额并列最高 → 按 ts_code 升序,600002 排 600003 前面
    assert by_code["600002.SH"]["rs_rank"] == 1
    assert by_code["600003.SH"]["rs_rank"] == 2
    assert by_code["600001.SH"]["rs_rank"] == 3   # 成交额最低,RS 并列时排最后


def test_role_mech_unknown_when_rs20_window_incomplete():
    """窗口内观测 < `PRICE_WINDOW_DAYS`(RS20 不足 20 天)→ `rs_rank=None` →
    `role_mech="unknown"`,不用"能算多少天算多少天"的近似值顶替。"""
    clusters = _clusters([("K1", "600001.SH", 2), ("K1", "600004.SH", 2)])
    rows = _full_window({"600001.SH": 0.05})           # 600001 凑满 20 天
    rows += [("600004.SH", 0, 0.05)]                    # 600004 只有 1 天(如刚上市)
    price = _price_window(rows)
    amounts = _amounts([("600001.SH", 100.0), ("600004.SH", 100.0)])

    out = leader.compute_leader_structure_for_day(D0, price, clusters, amounts)
    by_code = {r["ts_code"]: r for r in out.iter_rows(named=True)}
    assert by_code["600004.SH"]["rs_rank"] is None
    assert by_code["600004.SH"]["role_mech"] == "unknown"
    assert by_code["600001.SH"]["role_mech"] == "leader"   # 唯一有效排名的成员


def test_unknown_members_do_not_shrink_core_cutoff_denominator():
    """`unknown` 成员不计入 core/elastic 分界的分母(`n_ranked` 只数有效排名的
    成员)。3 只中 1 只 unknown、2 只有效排名 → 分界按 `n_ranked=2` 算
    (`1+2//2=2`),第二名归 `core`。"""
    clusters = _clusters([("K1", "600001.SH", 1), ("K1", "600002.SH", 1), ("K1", "600004.SH", 1)])
    rows = _full_window({"600001.SH": 0.10, "600002.SH": 0.02})
    rows += [("600004.SH", 0, 0.05)]   # 只 1 天观测 → unknown
    price = _price_window(rows)
    amounts = _amounts([("600001.SH", 1.0), ("600002.SH", 1.0), ("600004.SH", 1.0)])

    out = leader.compute_leader_structure_for_day(D0, price, clusters, amounts)
    by_code = {r["ts_code"]: r for r in out.iter_rows(named=True)}
    assert by_code["600001.SH"]["role_mech"] == "leader"
    assert by_code["600002.SH"]["role_mech"] == "core"    # n_ranked=2 → cutoff=2,rank2 仍是 core
    assert by_code["600004.SH"]["role_mech"] == "unknown"


def test_two_ranked_member_cluster_second_place_is_core_not_elastic():
    """`n_ranked=2` 时第二名归 `core`(比例 `1+2//2=2`,不是"投机跟风";见模块
    docstring 判据说明)。"""
    clusters = _clusters([("K1", "600001.SH", 5), ("K1", "600002.SH", 3)])
    price = _price_window(_full_window({"600001.SH": 0.08, "600002.SH": 0.02}))
    amounts = _amounts([("600001.SH", 1.0), ("600002.SH", 1.0)])
    out = leader.compute_leader_structure_for_day(D0, price, clusters, amounts)
    by_code = {r["ts_code"]: r for r in out.iter_rows(named=True)}
    assert by_code["600001.SH"]["role_mech"] == "leader"
    assert by_code["600002.SH"]["role_mech"] == "core"


def test_empty_clusters_yields_empty_frame():
    out = leader.compute_leader_structure_for_day(D0, pl.DataFrame(), pl.DataFrame(schema={
        "cluster_key": pl.String, "ts_code": pl.String, "consecutive_days": pl.Int64,
    }), pl.DataFrame(schema={"ts_code": pl.String, "amount": pl.Float64}))
    assert out.is_empty()


# ══════════════════════════════════════════════════════════════════════════
# ④⑤ 确定性 + 三路等价(经 refresh_leader_structure 落表读回)
# ══════════════════════════════════════════════════════════════════════════

def _seed_full_day(env, d: date) -> None:
    """铺 `PRICE_WINDOW_DAYS+1` 个**连续交易日**(最后一天 = `d`,`d` 本身须是
    工作日)的三只票行情 + 当日涨停簇,供 `refresh_leader_structure` 端到端跑通。
    600001 持续上涨、600003 持续下跌、600002 走平——三条互不相同的路径,保证
    凑满 RS20 所需的**整窗、无断口**观测(`business_days` 只会向前生成,故先
    多生成一批再按 `<=d` 截尾取最后 N 个,不能像别处那样直接覆盖最后一个元素
    ——那样会在窗口中间留下人为的大断口,RS20 会因观测不足而全员判 unknown)。"""
    assert d.weekday() < 5, "D0 必须是工作日,否则造不出以它收尾的连续交易日序列"
    long_run = business_days(d - timedelta(days=120), 120)
    days = [x for x in long_run if x <= d][-(PRICE_WINDOW_DAYS + 1):]
    assert days[-1] == d
    insert_trade_cal(env, days)
    insert_stock_basic(env, [{"ts_code": "600001.SH"}, {"ts_code": "600002.SH"}, {"ts_code": "600003.SH"}])
    with connection(env.db_path) as conn:
        for code, cdays in [("600001.SH", 3), ("600002.SH", 2), ("600003.SH", 1)]:
            conn.execute(
                "INSERT OR REPLACE INTO limit_cluster_daily "
                "(trade_date, cluster_key, ts_code, cluster_kind, cluster_size, consecutive_days, "
                " anchor_industry, anchor_concept, computed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (d.strftime("%Y%m%d"), "K1", code, "same_day", 3, cdays, "测试行业", None, "2024-01-01T00:00:00+00:00"),
            )
    for i, day in enumerate(days):
        write_daily_fixture(env, "daily", day, [
            {"ts_code": "600001.SH", "open": 10, "high": 10, "low": 10, "close": 10 + i * 0.05,
             "pre_close": 10 + max(i - 1, 0) * 0.05, "vol": 1000.0, "amount": 500_000.0},
            {"ts_code": "600002.SH", "open": 10, "high": 10, "low": 10, "close": 10,
             "pre_close": 10, "vol": 1000.0, "amount": 300_000.0},
            {"ts_code": "600003.SH", "open": 10, "high": 10, "low": 10, "close": 10 - i * 0.05,
             "pre_close": 10 - max(i - 1, 0) * 0.05, "vol": 1000.0, "amount": 200_000.0},
        ])


def test_refresh_is_deterministic_and_readback_matches(isolated_env):
    """⚠ **比较时排除 `computed_at`**(§七 P1-36 同一定案,2026-08-03 V2-⑬ 期间在
    `leader` 侧复现):该列是「这行何时算的」审计戳、不是业务列;两次独立 refresh 只要
    跨越墙钟秒边界,它就会**合法地**不同 —— 拿它比等价性是测试缺陷,不是业务分叉
    (`test_scan_cluster.py`/`test_scan_corr.py` 已按此修过,本文件当时漏改)。"""
    env = isolated_env
    _seed_full_day(env, D0)

    stats = leader.refresh_leader_structure([D0], db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert stats["rows"] == 3
    first = leader.load_leader_structure(D0, db_path=env.db_path).sort("ts_code")
    # 端到端跑通后,RS20 最强的 600001(持续上涨)应是 leader
    assert first.filter(pl.col("ts_code") == "600001.SH")["role_mech"].item() == "leader"

    leader.refresh_leader_structure([D0], db_path=env.db_path, parquet_dir=env.parquet_dir)
    second = leader.load_leader_structure(D0, db_path=env.db_path).sort("ts_code")
    assert first.drop("computed_at").equals(second.drop("computed_at")), \
        "两次 refresh 的业务列应逐位相同(已排除审计戳 computed_at)"
    with connection(env.db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM leader_structure_daily WHERE trade_date=?", (D0.strftime("%Y%m%d"),)
        ).fetchone()[0]
    assert n == 3


def test_no_clusters_is_empty_not_error(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    stats = leader.refresh_leader_structure([D0], db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert stats == {"days": 1, "rows": 0}
    assert leader.load_leader_structure(D0, db_path=env.db_path).is_empty()
