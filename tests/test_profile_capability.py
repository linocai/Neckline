"""⑫-B 能力画像引擎单测(`neckline/profile/capability.py`)。

覆盖:①`_trade_stats` 胜率/盈亏比口径对;②`_mfe_mae` 整段持有期近似算对(真实
`daily` 分区);③`compute_capability` 端到端——`vs_peer_delta` 与直接调用
`eval.metrics.score_tradable` 的结果逐位一致(同一套判分,不是本模块另写一份);
④样本不足(< `MIN_SAMPLE_N`)时 `vs_peer_delta` 如实 `None`;⑤只看已平仓仓位;
⑥「错失机会」(`missed_role`)按未选成员角色分组算对;⑦`store.py` 落库/读回
逐位一致、`verdict` 不落库。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from neckline.db import connection, init_schema
from neckline.eval import metrics as eval_metrics
from neckline.eval.exit_sim import PriceMaps, notional_from_charter, score_kw_from_charter
from neckline.profile import capability as cap
from neckline.profile import common as pc
from neckline.profile import store as profile_store
from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, seed_active_rule_v1, write_daily_fixture

pytestmark = pytest.mark.usefixtures("isolated_env")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ══════════════════════════════════════════════════════════════════════════
# 纯函数:胜率/盈亏比
# ══════════════════════════════════════════════════════════════════════════

class TestTradeStats:
    def test_empty_group_is_none(self):
        assert cap._trade_stats([]) == (None, None)

    def test_win_rate_and_profit_factor(self):
        rows = [
            pc.BuyContext(
                position_id=1, ts_code="A.SZ", buy_date="20260710", buy_price=10.0, qty=100,
                status="closed", sell_price=11.0, sell_date="20260715",
                buy_fees=5.0, sell_fees=5.0, close_reason=None, basket_id=None,
                tier=None, role=None, industry=None, entry_zone=None,
            ),
            pc.BuyContext(
                position_id=2, ts_code="B.SZ", buy_date="20260710", buy_price=10.0, qty=100,
                status="closed", sell_price=9.0, sell_date="20260715",
                buy_fees=5.0, sell_fees=5.0, close_reason=None, basket_id=None,
                tier=None, role=None, industry=None, entry_zone=None,
            ),
        ]
        win_rate, pf = cap._trade_stats(rows)
        assert win_rate == pytest.approx(0.5)
        gross_profit = 100 * 1.0 - 10.0   # (11-10)*100 - fees(10)
        gross_loss = abs(100 * -1.0 - 10.0)   # (9-10)*100 - fees(10) = -110 -> abs=110
        assert pf == pytest.approx(gross_profit / gross_loss)

    def test_all_wins_is_infinite_profit_factor(self):
        rows = [
            pc.BuyContext(
                position_id=1, ts_code="A.SZ", buy_date="20260710", buy_price=10.0, qty=100,
                status="closed", sell_price=11.0, sell_date="20260715",
                buy_fees=0.0, sell_fees=0.0, close_reason=None, basket_id=None,
                tier=None, role=None, industry=None, entry_zone=None,
            ),
        ]
        win_rate, pf = cap._trade_stats(rows)
        assert win_rate == 1.0
        assert pf == float("inf")


# ══════════════════════════════════════════════════════════════════════════
# MFE/MAE(真实 daily 分区,EOD 近似延伸到整段持有期)
# ══════════════════════════════════════════════════════════════════════════

class TestMfeMae:
    def test_mfe_mae_over_holding_window(self, isolated_env):
        env = isolated_env
        days = business_days(date(2026, 7, 13), 5)
        # buy_price=10.0;第2天冲高到 11(MFE=+10%),第3天探低到 9.2(MAE=-8%)
        highs = [10.2, 10.5, 11.0, 10.8, 10.9]
        lows = [9.8, 9.6, 10.5, 9.2, 10.6]
        closes = [10.0, 10.2, 10.8, 9.5, 10.7]
        for i, d in enumerate(days):
            write_daily_fixture(env, "daily", d, [{
                "ts_code": "A.SZ", "open": closes[i], "high": highs[i], "low": lows[i],
                "close": closes[i], "pre_close": closes[i - 1] if i else closes[0],
                "vol": 1000.0, "amount": 10000.0,
            }])
        mfe, mae = cap._mfe_mae(
            "A.SZ", days[0].strftime("%Y%m%d"), days[-1].strftime("%Y%m%d"), 10.0,
            parquet_dir=env.parquet_dir,
        )
        assert mfe == pytest.approx((11.0 / 10.0) - 1.0)
        assert mae == pytest.approx((9.2 / 10.0) - 1.0)

    def test_no_data_returns_none_not_zero(self, isolated_env):
        mfe, mae = cap._mfe_mae("A.SZ", "20260713", "20260715", 10.0,
                                parquet_dir=isolated_env.parquet_dir)
        assert mfe is None and mae is None

    def test_non_positive_buy_price_returns_none(self, isolated_env):
        assert cap._mfe_mae("A.SZ", "20260713", "20260715", 0.0,
                            parquet_dir=isolated_env.parquet_dir) == (None, None)


# ══════════════════════════════════════════════════════════════════════════
# 端到端:compute_capability(注入 PriceMaps,不碰真实 parquet 的判分部分)
# ══════════════════════════════════════════════════════════════════════════

def _seed_basket(db_path: Path, *, d0: str, members_roles: Dict[str, str], tier: int = 1,
                 key: Optional[str] = None) -> int:
    """裸 SQL 铺一个 D0 篮子(体例照 `tests/test_eval_metrics_placebo.py::_seed`)。
    `key` 缺省时从成员码派生,保证同一 `d0` 下多次调用(同一交易日铺多个篮子)
    不会撞 `UNIQUE(trade_date, basket_key)`。"""
    init_schema(db_path)
    now = _now()
    basket_key = key or f"k-{d0}-{'-'.join(sorted(members_roles))}"
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d0, basket_key, "冒烟篮", "驱动", "theme", tier, "K4-pack-v1", 1, "v1.3.3",
             "auto", "ok", now),
        )
        basket_id = int(cur.lastrowid)
        for code, role in members_roles.items():
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (basket_id, code, role, role, 0, "测试理由", 1, now),
            )
    return basket_id


def _insert_position(db_path: Path, *, ts_code: str, buy_date: str, buy_price: float,
                     sell_price: Optional[float], sell_date: Optional[str],
                     status: str = "closed") -> int:
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO positions (ts_code, buy_price, qty, buy_date, status, sell_price, "
            "sell_date, buy_fees, sell_fees, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts_code, buy_price, 1000, buy_date, status, sell_price, sell_date, 5.0, 5.0, now, now),
        )
        return int(cur.lastrowid)


def _insert_snapshot(db_path: Path, position_id: int, ts_code: str, buy_date: str, *,
                     basket_id: int, tier: int, role: str) -> None:
    now = _now()
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO entry_snapshots (position_id, ts_code, trade_date, basket_id, "
            "card_version, tier, role, snapshot_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (position_id, ts_code, buy_date, basket_id, None, tier, role, "{}", now),
        )


def _rising_price_maps(codes_factors: Dict[str, float], cal: List[date]) -> PriceMaps:
    """构造单调不降的合成行情(不同 code 用不同日涨幅 `factor`),确保不会触发
    止损/回落(全程只会走时间退出),这样各 code 的最终收益完全由涨幅高低决定
    ——不需要手工反推 `_sim_one` 的分支逻辑,只需要保证方向可预期。"""
    pm: Dict[str, dict] = {}
    cal_idx = {d: i for i, d in enumerate(cal)}
    for code, factor in codes_factors.items():
        n = len(cal)
        opens = [round(10.0 * (factor ** i), 6) for i in range(n)]
        closes = [round(o * 1.001, 6) for o in opens]
        lows = [round(o * 0.995, 6) for o in opens]
        pm[code] = {"idx": dict(cal_idx), "o": opens, "l": lows, "c": closes}
    return PriceMaps(pm, set(), cal, cal_idx, qfq_anchor=cal[-1])


@pytest.fixture
def capability_universe(isolated_env):
    """三只票的一个篮子(A=leader 涨幅最大 / B=core 涨幅中等 / C=elastic 走平),
    用户买入了 A(leader)。返回 (env, cal, d0, basket_id, position_id)。"""
    env = isolated_env
    cal = business_days(date(2026, 7, 13), 20)
    insert_trade_cal(env, cal)
    seed_active_rule_v1(env)   # base_hold=5 / retrace=0.05 / stop_pct=0.05(TEST_RULE_V1_CONFIG)
    insert_stock_basic(env, [
        {"ts_code": "A.SZ", "industry": "半导体"},
        {"ts_code": "B.SZ", "industry": "半导体"},
        {"ts_code": "C.SZ", "industry": "半导体"},
    ])
    d0 = cal[0].strftime("%Y%m%d")
    buy_date = cal[1].strftime("%Y%m%d")   # D+1
    basket_id = _seed_basket(env.db_path, d0=d0,
                             members_roles={"A.SZ": "leader", "B.SZ": "core", "C.SZ": "elastic"})
    pid = _insert_position(env.db_path, ts_code="A.SZ", buy_date=buy_date,
                           buy_price=10.3, sell_price=11.5, sell_date=cal[7].strftime("%Y%m%d"))
    _insert_snapshot(env.db_path, pid, "A.SZ", buy_date, basket_id=basket_id, tier=1, role="leader")
    # 也给 A.SZ 铺一份 daily 分区,供 MFE/MAE 用(值不需要精确对齐 exit_sim 的合成价,
    # 两者是两件独立核对的事:MFE/MAE 读真实 daily,vs_peer_delta 读注入的 PriceMaps)。
    for i, d in enumerate(business_days(date(2026, 7, 13), 6)):
        write_daily_fixture(env, "daily", d, [{
            "ts_code": "A.SZ", "open": 10.3 + i * 0.1, "high": 10.3 + i * 0.15,
            "low": 10.2 + i * 0.05, "close": 10.3 + i * 0.1, "pre_close": 10.2 + i * 0.1,
            "vol": 1000.0, "amount": 10000.0,
        }])
    price_maps = _rising_price_maps({"A.SZ": 1.03, "B.SZ": 1.005, "C.SZ": 1.0}, cal)
    return {
        "env": env, "cal": cal, "d0": d0, "buy_date": buy_date,
        "basket_id": basket_id, "position_id": pid, "price_maps": price_maps,
    }


class TestComputeCapabilityVsPeer:
    def test_vs_peer_delta_matches_direct_score_tradable(self, capability_universe):
        """核心不变量:`compute_capability` 的 `vs_peer_delta` 必须与直接调用
        `eval.metrics.score_tradable`(同一份 PriceMaps/score_kw)得到的结果一致
        ——证明本模块没有另写第二份判分/对照实现。"""
        u = capability_universe
        env = u["env"]

        rows = cap.compute_capability(
            u["d0"], u["buy_date"], db_path=env.db_path, price_maps=u["price_maps"],
        )
        role_row = next(r for r in rows if r.dimension == pc.DIM_ROLE and r.value == "leader")
        assert role_row.sample_n == 1
        assert role_row.vs_peer_delta is None   # 样本量(配对数=1)< MIN_SAMPLE_N,如实 None

        # 直接跑一遍"地面真值"(同一套 PriceMaps/score_kw),验证底层数字确实拿的
        # 是同一份判分(即便本条因样本不足被 gate 成 None,数据本身仍应可复核)。
        panel = eval_metrics.load_basket_panel(u["d0"], u["buy_date"], db_path=env.db_path)
        touched = [r for r in panel if r.basket_id == u["basket_id"]]
        kw = score_kw_from_charter(db_path=env.db_path)
        notional = notional_from_charter(db_path=env.db_path)
        tr = eval_metrics.score_tradable(touched, price_maps=u["price_maps"], score_kw=kw,
                                         notional=notional, db_path=env.db_path)
        by_code = {row["ts_code"]: row for row in tr.per_member if row["basket_id"] == u["basket_id"]}
        assert by_code["A.SZ"]["filled"] and by_code["A.SZ"]["fill_code"] == "ok"
        # A(leader,涨幅最大)应明显跑赢 B/C(涨幅更小)——方向性核对
        assert by_code["A.SZ"]["ret"] > by_code["B.SZ"]["ret"] > by_code["C.SZ"]["ret"]

    def test_sample_at_min_threshold_reports_a_number(self, isolated_env):
        """样本量(配对数)恰好达到 `MIN_SAMPLE_N` → `vs_peer_delta` 不再是 None
        (与低于门槛时的行为分得开)。"""
        env = isolated_env
        cal = business_days(date(2026, 7, 13), 60)
        insert_trade_cal(env, cal)
        seed_active_rule_v1(env)
        codes = [f"A{i}.SZ" for i in range(pc.MIN_SAMPLE_N)] + ["P.SZ"]
        insert_stock_basic(env, [{"ts_code": c, "industry": "半导体"} for c in codes])
        d0 = cal[0].strftime("%Y%m%d")
        buy_date = cal[1].strftime("%Y%m%d")
        factors = {c: 1.03 for c in codes}
        factors["P.SZ"] = 1.0   # 陪衬同伴,始终存在于每个篮子
        maps_codes = set(codes) | {"P.SZ"}

        for i, code in enumerate(codes[:pc.MIN_SAMPLE_N]):
            basket_id = _seed_basket(
                env.db_path, d0=d0, members_roles={code: "leader", "P.SZ": "core"},
            )
            pid = _insert_position(env.db_path, ts_code=code, buy_date=buy_date,
                                   buy_price=10.3, sell_price=11.0, sell_date=cal[7].strftime("%Y%m%d"))
            _insert_snapshot(env.db_path, pid, code, buy_date, basket_id=basket_id, tier=1, role="leader")

        price_maps = _rising_price_maps({c: factors[c] for c in maps_codes}, cal)
        rows = cap.compute_capability(d0, buy_date, db_path=env.db_path, price_maps=price_maps)
        role_row = next(r for r in rows if r.dimension == pc.DIM_ROLE and r.value == "leader")
        assert role_row.sample_n == pc.MIN_SAMPLE_N
        assert role_row.vs_peer_delta is not None
        assert role_row.confidence != pc.CONFIDENCE_LOW

    def test_only_closed_positions_are_considered(self, capability_universe):
        u = capability_universe
        env = u["env"]
        # 追加一笔未平仓的独立买入(不应进入统计)
        _insert_position(env.db_path, ts_code="C.SZ", buy_date=u["buy_date"],
                         buy_price=10.0, sell_price=None, sell_date=None, status="open")
        rows = cap.compute_capability(u["d0"], u["buy_date"], db_path=env.db_path,
                                      price_maps=u["price_maps"])
        total_n = sum(r.sample_n for r in rows if r.dimension == pc.DIM_ROLE)
        assert total_n == 1   # 只有 A.SZ(closed)那一笔,未平仓的 C.SZ 不计入

    def test_empty_window_is_empty(self, isolated_env):
        assert cap.compute_capability("20260701", "20260731", db_path=isolated_env.db_path) == []


class TestMissedRoleOpportunities:
    def test_unpicked_peers_grouped_by_role(self, capability_universe):
        u = capability_universe
        env = u["env"]
        rows = cap.compute_capability(u["d0"], u["buy_date"], db_path=env.db_path,
                                      price_maps=u["price_maps"])
        missed = {r.value: r for r in rows if r.dimension == pc.DIM_MISSED_ROLE}
        # 用户买了 leader(A),没买 core(B)/elastic(C) —— 这两者应出现在错失机会里
        assert "core" in missed and "elastic" in missed
        assert "leader" not in missed   # leader 已被选中,不算"错失"
        assert missed["core"].vs_peer_delta is None   # 该类行不提供这个字段(见模块 docstring)
        assert missed["core"].sample_n == 1

    def test_no_touched_baskets_yields_no_missed_rows(self, isolated_env):
        env = isolated_env
        cal = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(env, cal)
        seed_active_rule_v1(env)
        _insert_position(env.db_path, ts_code="Z.SZ", buy_date=cal[1].strftime("%Y%m%d"),
                         buy_price=10.0, sell_price=10.5, sell_date=cal[3].strftime("%Y%m%d"))
        rows = cap.compute_capability(cal[0].strftime("%Y%m%d"), cal[-1].strftime("%Y%m%d"),
                                      db_path=env.db_path)
        assert not any(r.dimension == pc.DIM_MISSED_ROLE for r in rows)


class TestCapabilityStoreRoundTrip:
    def test_save_and_load_round_trips_without_verdict(self, capability_universe):
        u = capability_universe
        env = u["env"]
        rows = cap.compute_capability(u["d0"], u["buy_date"], db_path=env.db_path,
                                      price_maps=u["price_maps"])
        assert rows   # 前提:确有行可存
        n = profile_store.save_capability("20260801", rows, db_path=env.db_path)
        assert n == len(rows)
        loaded = profile_store.load_capability("20260801", db_path=env.db_path)
        assert len(loaded) == len(rows)
        assert all("verdict" not in r for r in loaded)   # DDL 无该列,确认不落库

    def test_recompute_overwrites_same_period(self, capability_universe):
        u = capability_universe
        env = u["env"]
        rows = cap.compute_capability(u["d0"], u["buy_date"], db_path=env.db_path,
                                      price_maps=u["price_maps"])
        profile_store.save_capability("20260801", rows, db_path=env.db_path)
        profile_store.save_capability("20260801", rows, db_path=env.db_path)   # 重跑一次
        loaded = profile_store.load_capability("20260801", db_path=env.db_path)
        assert len(loaded) == len(rows)   # 覆盖而非重复
