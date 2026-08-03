#!/usr/bin/env python3
"""V2-⑫-B 端到端冒烟(合成数据,隔离临时库 + 隔离临时 parquet,不碰真实
`data/`)。合成一批 `user_actions` + 持仓 + 篮子数据,跑一遍偏好画像 + 能力画像,
打印每张表至少一条带样本量与置信度的条目,供人工核对。

**这不是活体验证的替代品**(同 `smoke_basket_review.py` 的自我定位),只是"两个
引擎在有数据时确实按预期工作"的一次冒烟检查。

用法::

    python scripts/smoke_profile.py
    python scripts/smoke_profile.py --keep     # 保留临时目录(调试用)
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import Settings  # noqa: E402
from neckline.data.market_data import write_table_day  # noqa: E402
from neckline.db import connection, init_schema  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("smoke_profile")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _business_days(start: date, n: int) -> List[date]:
    out: List[date] = []
    cur = start
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def _seed_trade_cal(db_path: Path, days: List[date]) -> None:
    import sqlite3

    start, end = min(days) - timedelta(days=5), max(days) + timedelta(days=5)
    open_set = set(days)
    conn = sqlite3.connect(str(db_path))
    try:
        rows, cur = [], start
        while cur <= end:
            rows.append(("SSE", cur.strftime("%Y%m%d"), 1 if cur in open_set else 0, ""))
            cur += timedelta(days=1)
        conn.executemany(
            "INSERT OR REPLACE INTO trade_cal (exchange, cal_date, is_open, pretrade_date) VALUES (?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _seed_stock_basic(db_path: Path, rows: List[Dict[str, str]]) -> None:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        for r in rows:
            conn.execute(
                "INSERT OR REPLACE INTO stock_basic (ts_code, symbol, name, industry, market, list_status) "
                "VALUES (?,?,?,?,?,?)",
                (r["ts_code"], r["ts_code"].split(".")[0], r.get("name", r["ts_code"]),
                 r["industry"], "主板", "L"),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_basket(db_path: Path, *, d0: str, members_roles: Dict[str, str], tier: int = 1) -> int:
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d0, f"smoke-{d0}", "冒烟·AI 算力", "冒烟桩:资金与消息共振", "theme", tier,
             "K4-pack-v1", 1, "v1.3.3", "auto", "ok", now),
        )
        basket_id = int(cur.lastrowid)
        for code, role in members_roles.items():
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (basket_id, code, role, role, 0, "冒烟桩理由", 1, now),
            )
    return basket_id


def _seed_position(db_path: Path, *, ts_code: str, buy_date: str, buy_price: float,
                   status: str, sell_price=None, sell_date=None) -> int:
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO positions (ts_code, buy_price, qty, buy_date, status, sell_price, "
            "sell_date, buy_fees, sell_fees, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts_code, buy_price, 1000, buy_date, status, sell_price, sell_date, 15.0, 15.0, now, now),
        )
        return int(cur.lastrowid)


def _seed_entry_snapshot(db_path: Path, position_id: int, ts_code: str, buy_date: str, *,
                         basket_id: int, tier: int, role: str) -> None:
    now = _now()
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO entry_snapshots (position_id, ts_code, trade_date, basket_id, "
            "card_version, tier, role, snapshot_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (position_id, ts_code, buy_date, basket_id, None, tier, role, "{}", now),
        )


def _seed_plan(db_path: Path, position_id: int, entry_zone: Dict[str, float]) -> None:
    now = _now()
    plan = {"available": True, "entry_zone": entry_zone}
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO position_plans (position_id, version, source_basket_id, "
            "source_card_version, plan_json, note, created_at) VALUES (?,1,?,?,?,?,?)",
            (position_id, None, None, json.dumps(plan, ensure_ascii=False), None, now),
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="store_true", help="保留临时目录(调试用)")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="neckline_smoke_profile_"))
    data_dir = tmp / "data"
    fake_settings = Settings(
        tushare_token=None, llm_provider=None, llm_api_key=None,
        project_root=tmp, data_dir=data_dir, parquet_dir=data_dir / "parquet",
        db_path=data_dir / "neckline.db",
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    fake_settings.parquet_dir.mkdir(parents=True, exist_ok=True)

    import neckline.calendar.trading_calendar as tc_mod
    import neckline.data.market_data as md_mod
    import neckline.data.tushare_client as ts_mod

    tc_mod.settings = fake_settings  # type: ignore[assignment]
    md_mod.settings = fake_settings  # type: ignore[assignment]
    ts_mod.settings = fake_settings  # type: ignore[assignment]

    init_schema(db_path=fake_settings.db_path)
    logger.info("隔离临时库 %s / parquet %s(不碰真实 data/)", fake_settings.db_path, fake_settings.parquet_dir)

    try:
        from neckline import user_actions
        from neckline.profile import capability as cap
        from neckline.profile import common as pc
        from neckline.profile import preference as pref
        from neckline.profile import store as profile_store
        from neckline.strategy import brain

        cal = _business_days(date(2026, 7, 13), 20)
        _seed_trade_cal(fake_settings.db_path, cal)
        brain.save_version(
            "v1", {"config": {
                "strength": "none", "buypoint": "pullback", "forbid_high_elasticity": True,
                "stop_pct": 0.05, "take_profit_retrace": 0.05, "max_hold_days": 5,
                "cooldown_days": 0, "single_cap": 20000.0, "max_positions": 5,
                "max_exposure_frac": 0.60, "week_halving": False,
            }}, "冒烟夹具:镜像 rule v1", metrics={}, activate=True, db_path=fake_settings.db_path,
        )
        _seed_stock_basic(fake_settings.db_path, [
            {"ts_code": "600001.SH", "industry": "半导体", "name": "冒烟票甲"},
            {"ts_code": "600002.SH", "industry": "半导体", "name": "冒烟票乙"},
            {"ts_code": "600003.SH", "industry": "半导体", "name": "冒烟票丙"},
            {"ts_code": "600004.SH", "industry": "新能源", "name": "冒烟票丁"},
        ])

        d0 = cal[0].strftime("%Y%m%d")
        buy_date = cal[1].strftime("%Y%m%d")
        sell_date = cal[7].strftime("%Y%m%d")
        basket_id = _seed_basket(fake_settings.db_path, d0=d0, members_roles={
            "600001.SH": "leader", "600002.SH": "core", "600003.SH": "elastic",
        })

        # 用户买入①:来自篮子的 leader,价格落在建仓区间内,平仓且盈利,贴了标签。
        pid1 = _seed_position(fake_settings.db_path, ts_code="600001.SH", buy_date=buy_date,
                              buy_price=10.3, status="closed", sell_price=11.5, sell_date=sell_date)
        _seed_entry_snapshot(fake_settings.db_path, pid1, "600001.SH", buy_date,
                             basket_id=basket_id, tier=1, role="leader")
        _seed_plan(fake_settings.db_path, pid1, {"low": 10.0, "high": 10.6})
        user_actions.record("buy", ts_code="600001.SH", position_id=pid1,
                            payload={"buy_price": 10.3, "qty": 1000}, db_path=fake_settings.db_path)
        user_actions.record("label", position_id=pid1, payload={"labels": ["LEADER_REACTIVATE"]},
                            db_path=fake_settings.db_path)

        # 用户买入②:独立买入(无来源篮子),追高买入,平仓且亏损。
        pid2 = _seed_position(fake_settings.db_path, ts_code="600004.SH", buy_date=buy_date,
                              buy_price=20.0, status="closed", sell_price=18.5, sell_date=sell_date)
        user_actions.record("buy", ts_code="600004.SH", position_id=pid2,
                            payload={"buy_price": 20.0, "qty": 1000}, db_path=fake_settings.db_path)

        # daily 分区(供 MFE/MAE + 能力画像的 exit_sim 判分用),覆盖两笔买入的持有期
        # + 篮子里另外两个"没被选中"的成员(600002/600003,供「错失机会」有数据可算)。
        import polars as pl

        for i, d in enumerate(cal[:9]):
            rows = [
                {"ts_code": "600001.SH", "trade_date": d, "open": 10.3 + i * 0.15, "high": 10.3 + i * 0.2,
                 "low": 10.2 + i * 0.1, "close": 10.3 + i * 0.15, "pre_close": 10.2 + i * 0.15,
                 "vol": 1000.0, "amount": 10000.0},
                {"ts_code": "600002.SH", "trade_date": d, "open": 10.0 + i * 0.05, "high": 10.05 + i * 0.05,
                 "low": 9.95 + i * 0.05, "close": 10.0 + i * 0.05, "pre_close": 10.0 + i * 0.04,
                 "vol": 1000.0, "amount": 10000.0},
                {"ts_code": "600003.SH", "trade_date": d, "open": 10.0, "high": 10.05,
                 "low": 9.95, "close": 10.0, "pre_close": 10.0,
                 "vol": 1000.0, "amount": 10000.0},
                {"ts_code": "600004.SH", "trade_date": d, "open": 20.0 - i * 0.2, "high": 20.1 - i * 0.15,
                 "low": 19.5 - i * 0.25, "close": 20.0 - i * 0.2, "pre_close": 20.0 - i * 0.15,
                 "vol": 1000.0, "amount": 10000.0},
            ]
            write_table_day("daily", d, pl.DataFrame(rows), parquet_dir=fake_settings.parquet_dir)

        window_start, window_end = "20260701", "20260831"

        logger.info("=== 偏好画像 ===")
        pref_rows = pref.compute_preference(window_start, window_end, db_path=fake_settings.db_path)
        for r in pref_rows:
            logger.info("  %-14s %-20s 占比 %5.1f%%  N=%-3d  窗口 %s~%s  置信度 %s",
                       r.dimension, r.value, r.share * 100, r.sample_n,
                       r.window_start, r.window_end, r.confidence)
        assert pref_rows, "偏好画像应至少产出一条(合成数据里有 2 笔买入)"
        assert any(r.sample_n and r.confidence for r in pref_rows), "至少一条带样本量与置信度"

        logger.info("=== 能力画像 ===")
        cap_rows = cap.compute_capability(window_start, window_end, db_path=fake_settings.db_path,
                                          parquet_dir=fake_settings.parquet_dir)
        for r in cap_rows:
            win = "—" if r.win_rate is None else f"{r.win_rate:.0%}"
            delta = "—" if r.vs_peer_delta is None else f"{r.vs_peer_delta:+.1%}"
            mfe = "—" if r.avg_mfe is None else f"{r.avg_mfe:+.1%}"
            mae = "—" if r.avg_mae is None else f"{r.avg_mae:+.1%}"
            logger.info("  %-14s %-20s N=%-3d 胜率 %-6s MFE %-8s MAE %-8s vs同篮未选 %-8s 置信度 %-6s · %s",
                       r.dimension, r.value, r.sample_n, win, mfe, mae, delta, r.confidence, r.verdict)
        assert cap_rows, "能力画像应至少产出一条(合成数据里有 2 笔已平仓)"
        assert any(r.win_rate is not None for r in cap_rows), "至少一条带胜率(win_rate)"

        logger.info("=== 落库 + 读回(as_of=20260831)===")
        n1 = profile_store.save_preference("20260831", pref_rows, db_path=fake_settings.db_path)
        n2 = profile_store.save_capability("20260831", cap_rows, db_path=fake_settings.db_path)
        loaded_pref = profile_store.load_preference("20260831", db_path=fake_settings.db_path)
        loaded_cap = profile_store.load_capability("20260831", db_path=fake_settings.db_path)
        logger.info("  profile_preference 写 %d / 读回 %d 行;profile_capability 写 %d / 读回 %d 行",
                   n1, len(loaded_pref), n2, len(loaded_cap))
        assert len(loaded_pref) == n1 and len(loaded_cap) == n2

        logger.info("冒烟通过:两张画像均产出 >=1 条带样本量与置信度的条目,落库/读回一致。")
        return 0
    finally:
        if args.keep:
            logger.info("临时目录保留:%s", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
