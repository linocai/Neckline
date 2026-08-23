"""清单成绩五指标（S17）。

主收益口径固定为 D0 收盘到 D+4 收盘；D+1..D+4 最高收益只保存为辅助读数。
行业归属取清单写入时冻结的申万二级，行业收益取 D0 同行业成分的同期收益中位数。
本模块不读取交割单，也不 import `neckline.k9`。
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from neckline.calendar import trading_days_between
from neckline.db import connection, init_schema, readonly_tables
from neckline.facts import store as fact_store

TABLE = "k9_followups"


def _d(value: date) -> str:
    return value.strftime("%Y%m%d")


def _day(raw: str) -> date:
    return datetime.strptime(raw, "%Y%m%d").date()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ratio(num: int, den: int) -> Optional[float]:
    return None if den == 0 else num / den


def _return(end: Optional[float], start: Optional[float]) -> Optional[float]:
    if start is None or end is None or start <= 0:
        return None
    return float(end) / float(start) - 1.0


def _finite_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _rows_by_code(frame) -> Dict[str, dict]:
    if frame.is_empty() or "ts_code" not in frame.columns:
        return {}
    return {str(r["ts_code"]): r for r in frame.iter_rows(named=True)}


def _future_four(d0: date, as_of: date) -> List[date]:
    return trading_days_between(d0 + timedelta(days=1), as_of)[:4]


def refresh_day(d0: date, *, as_of: date, strategy: str = "K9",
                parquet_dir: Optional[Path] = None,
                db_path: Optional[Path] = None) -> bool:
    """D+4 到齐后幂等计算一个清单日；不足四个交易日返回 False。"""
    sessions = _future_four(d0, as_of)
    if len(sessions) < 4:
        return False
    d4 = sessions[-1]
    with readonly_tables("k9_listing_entries", db_path=db_path) as conn:
        listing = [] if conn is None else conn.execute(
            "SELECT ts_code, name, sw_l2_code, sw_l2_name FROM k9_listing_entries "
            "WHERE trade_date=? AND strategy=? ORDER BY rank, ts_code",
            (_d(d0), strategy),
        ).fetchall()
        if not listing:
            return False
        verdict_rows = conn.execute(
            "SELECT ts_code, verdict FROM k9_d1_verdicts "
            "WHERE d0_date=? AND strategy=?",
            (_d(d0), strategy),
        ).fetchall()
        playbook_rows = conn.execute(
            "SELECT p.ts_code, p.first_resistance FROM k9_playbooks p "
            "JOIN (SELECT ts_code, MAX(version) AS version FROM k9_playbooks "
            "WHERE trade_date=? GROUP BY ts_code) q "
            "ON p.ts_code=q.ts_code AND p.version=q.version "
            "WHERE p.trade_date=?",
            (_d(d0), _d(d0)),
        ).fetchall()

    try:
        packs = [fact_store.load_pack(day, parquet_dir=parquet_dir, db_path=db_path)
                 for day in [d0, *sessions]]
        frames = [pack.rows for pack in packs]
    except (fact_store.PackNotFrozen, FileNotFoundError):
        return False

    by_day = [_rows_by_code(frame) for frame in frames]
    d0_rows, d4_rows = by_day[0], by_day[-1]
    verdict_of = {str(code): verdict for code, verdict in verdict_rows}
    resistance_of: Dict[str, Optional[float]] = {}
    for code, value in playbook_rows:
        resistance_of[str(code)] = _finite_float(value)

    # 用 D0 冻结行业归属给全市场同行分组，同行同期收益取中位数。
    industry_samples: Dict[str, List[float]] = {}
    for code, start_row in d0_rows.items():
        l2 = start_row.get("sw_l2_code")
        end_row = d4_rows.get(code)
        ret = _return(None if end_row is None else _finite_float(end_row.get("close")),
                      _finite_float(start_row.get("close")))
        if l2 and ret is not None:
            industry_samples.setdefault(str(l2), []).append(ret)
    industry_return = {l2: statistics.median(values)
                       for l2, values in industry_samples.items() if values}

    records = []
    computed = _now()
    for code, name, l2_code, l2_name in listing:
        code = str(code)
        start = d0_rows.get(code, {})
        end = d4_rows.get(code, {})
        d0_close = _finite_float(start.get("close"))
        d4_close = _finite_float(end.get("close"))
        highs = [_finite_float(day_rows.get(code, {}).get("high")) for day_rows in by_day[1:]]
        highs = [v for v in highs if v is not None]
        max_high = max(highs) if highs else None
        stock_ret = _return(d4_close, d0_close)
        stock_max_ret = _return(max_high, d0_close)
        industry_ret = industry_return.get(str(l2_code)) if l2_code else None
        pick_excess = (None if stock_ret is None or industry_ret is None
                       else stock_ret - industry_ret)
        first_resistance = resistance_of.get(code)
        hit = (None if first_resistance is None or max_high is None
               else int(max_high >= first_resistance))
        records.append((
            _d(d0), _d(d4), code, strategy, name, l2_code, l2_name, verdict_of.get(code),
            int(code in resistance_of), first_resistance, hit, d0_close, d4_close, max_high,
            stock_ret, stock_max_ret, industry_ret, pick_excess, computed,
        ))

    init_schema(db_path)
    with connection(db_path) as conn:
        conn.execute(f"DELETE FROM {TABLE} WHERE d0_date=? AND strategy=?", (_d(d0), strategy))
        conn.executemany(
            f"INSERT INTO {TABLE} (d0_date,d4_date,ts_code,strategy,name,sw_l2_code,"
            "sw_l2_name,verdict,has_playbook,first_resistance,hit_first_resistance,d0_close,"
            "d4_close,max_high_d1_d4,stock_close_return,stock_max_return,"
            "industry_close_return,pick_close_excess,computed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            records,
        )
    return True


def refresh_due(as_of: date, *, strategy: str = "K9", parquet_dir: Optional[Path] = None,
                db_path: Optional[Path] = None) -> int:
    """补齐截至 as_of 已走完 D+4 的所有正式清单日。"""
    with readonly_tables("k9_listing_entries", db_path=db_path) as conn:
        rows = [] if conn is None else conn.execute(
            "SELECT DISTINCT trade_date FROM k9_listing_entries WHERE strategy=? "
            "AND trade_date<=? ORDER BY trade_date", (strategy, _d(as_of))).fetchall()
    return sum(1 for (raw,) in rows
               if refresh_day(_day(raw), as_of=as_of, strategy=strategy,
                              parquet_dir=parquet_dir, db_path=db_path))


def load_scorecard(*, window: int = 20, strategy: str = "K9",
                   db_path: Optional[Path] = None) -> Dict[str, Any]:
    """最近 window 个已结算清单日的五指标。行业分与选票分独立返回，无合计。"""
    with readonly_tables(TABLE, db_path=db_path) as conn:
        if conn is None:
            rows: Sequence[tuple] = ()
        else:
            dates = [r[0] for r in conn.execute(
                f"SELECT DISTINCT d0_date FROM {TABLE} WHERE strategy=? "
                "ORDER BY d0_date DESC LIMIT ?", (strategy, int(window))).fetchall()]
            if not dates:
                rows = ()
            else:
                marks = ",".join("?" for _ in dates)
                rows = conn.execute(
                    f"SELECT d0_date,d4_date,ts_code,verdict,has_playbook,hit_first_resistance,"
                    f"stock_close_return,stock_max_return,industry_close_return,pick_close_excess "
                    f"FROM {TABLE} WHERE strategy=? AND d0_date IN ({marks}) "
                    "ORDER BY d0_date DESC, ts_code", (strategy, *dates)).fetchall()

    establishment_den = len(rows)  # B22：正式清单全量；verdict 表只贡献分子。
    establishment_num = sum(1 for r in rows if r[3] == "confirmed")
    confirmed = [r for r in rows if r[3] == "confirmed" and r[4] and r[5] is not None]
    rejected = [r for r in rows if r[3] == "rejected" and r[4] and r[5] is not None]
    realization_num = sum(1 for r in confirmed if r[5])
    false_kill_num = sum(1 for r in rejected if r[5])
    industry_values = [float(r[8]) for r in rows if r[8] is not None]
    pick_values = [float(r[9]) for r in rows if r[9] is not None]
    d0_dates = sorted({r[0] for r in rows}, reverse=True)
    return {
        "window": int(window), "settledDays": len(d0_dates), "listingCount": len(rows),
        "establishmentRate": _ratio(establishment_num, establishment_den),
        "establishmentNumerator": establishment_num,
        "establishmentDenominator": establishment_den,
        "realizationRate": _ratio(realization_num, len(confirmed)),
        "realizationNumerator": realization_num, "realizationDenominator": len(confirmed),
        "falseKillRate": _ratio(false_kill_num, len(rejected)),
        "falseKillNumerator": false_kill_num, "falseKillDenominator": len(rejected),
        "industryScore": (statistics.mean(industry_values) if industry_values else None),
        "pickScore": (statistics.mean(pick_values) if pick_values else None),
        "latestD0Date": d0_dates[0] if d0_dates else None,
        "rows": [
            {"d0Date": r[0], "d4Date": r[1], "tsCode": r[2], "verdict": r[3],
             "hasPlaybook": bool(r[4]), "hitFirstResistance": None if r[5] is None else bool(r[5]),
             "stockCloseReturn": r[6], "stockMaxReturn": r[7],
             "industryCloseReturn": r[8], "pickCloseExcess": r[9]}
            for r in rows
        ],
    }


__all__ = ["TABLE", "refresh_day", "refresh_due", "load_scorecard"]
