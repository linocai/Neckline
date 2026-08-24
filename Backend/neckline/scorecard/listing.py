"""K9-v2 清单成绩：D1—D2 全样本、D2 强制结算，绝不读取真实成交。"""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from neckline.calendar import trading_days_between
from neckline.db import connection, init_schema, readonly_tables
from neckline.facts import store as fact_store

TABLE = "k9_predictions"
STRATEGY_VERSION = "K9-v2"
COHORT_FINAL = "final"
COHORT_STRICT = "strict_recall"
COHORT_BASELINE = "matched_baseline"
PATTERNS = ("p1", "p2", "p3", "p4")
ACTIVE_QUEUE_LIMIT = 60


def _d(value: date) -> str:
    return value.strftime("%Y%m%d")


def _day(raw: str) -> date:
    return datetime.strptime(raw, "%Y%m%d").date()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _return(end: Optional[float], start: Optional[float]) -> Optional[float]:
    return None if start is None or end is None or start <= 0 else end / start - 1.0


def _rows(frame) -> Dict[str, dict]:
    return {} if frame.is_empty() else {
        str(row["ts_code"]): row for row in frame.iter_rows(named=True)
    }


def _forward_two(d0: date, as_of: date) -> List[date]:
    return trading_days_between(d0 + timedelta(days=1), as_of)[:2]


def _one_line_limit_up(row: Mapping[str, Any]) -> bool:
    if not bool(row.get("is_limit_up")):
        return False
    prices = [_finite(row.get(key)) for key in ("open", "high", "low", "close")]
    return None not in prices and len({round(float(v), 2) for v in prices}) == 1


def _industry_returns(d0_rows: Mapping[str, dict], d2_rows: Mapping[str, dict]) -> Dict[str, float]:
    samples: Dict[str, List[float]] = {}
    for code, start in d0_rows.items():
        l2 = start.get("sw_l2_code")
        end = d2_rows.get(code, {})
        value = _return(_finite(end.get("close")), _finite(start.get("close")))
        if l2 and value is not None:
            samples.setdefault(str(l2), []).append(value)
    return {key: statistics.median(values) for key, values in samples.items() if values}


def _market_return(d0_rows: Mapping[str, dict], d2_rows: Mapping[str, dict]) -> Optional[float]:
    values = [
        value for code, start in d0_rows.items()
        if (value := _return(
            _finite(d2_rows.get(code, {}).get("close")), _finite(start.get("close"))
        )) is not None
    ]
    return statistics.median(values) if values else None


def open_day(
    d0: date, *, strategy: str = "K9", strategy_version: str = STRATEGY_VERSION,
    parquet_dir: Optional[Path] = None, db_path: Optional[Path] = None,
) -> int:
    """正式清单定稿后建立 D0 预测；同日重跑只替换本批次，不复制记录。"""
    forward = trading_days_between(d0 + timedelta(days=1), d0 + timedelta(days=20))[:2]
    if len(forward) < 2:
        return 0
    d1, d2 = forward
    with readonly_tables(
        "k9_listing_entries.strategy_version", "k9_runs.label_contract_version",
        db_path=db_path,
    ) as conn:
        if conn is None:
            return 0
        rows = conn.execute(
            "SELECT l.ts_code,l.name,l.sw_l2_code,l.sw_l2_name,l.primary_pattern,"
            "r.label_contract_version FROM k9_listing_entries l JOIN k9_runs r "
            "ON r.run_id=l.run_id WHERE l.trade_date=? AND l.strategy=? "
            "AND l.strategy_version=? ORDER BY l.rank,l.ts_code",
            (_d(d0), strategy, strategy_version),
        ).fetchall()
    if not rows:
        return 0
    try:
        d0_rows = _rows(fact_store.load_pack(
            d0, parquet_dir=parquet_dir, db_path=db_path).rows)
    except (fact_store.PackNotFrozen, FileNotFoundError):
        d0_rows = {}
    computed = _now()
    records = [(
        _d(d0), _d(d1), _d(d2), str(code), strategy, strategy_version, label,
        COHORT_FINAL, pattern, name, l2, l2_name,
        _finite(d0_rows.get(str(code), {}).get("close")),
        None, None, None, None, None, "pending", None, None, None, None, None,
        0, None, computed, None, None,
    ) for code, name, l2, l2_name, pattern, label in rows]
    if len(records) > 20:
        raise ValueError(f"K9-v2 D0 正式清单 {len(records)} 条，超过单批 20 条上限")
    init_schema(db_path)
    with connection(db_path) as conn:
        conn.execute(
            f"DELETE FROM {TABLE} WHERE d0_date=? AND strategy=? AND strategy_version=? "
            "AND cohort=?",
            (_d(d0), strategy, strategy_version, COHORT_FINAL),
        )
        conn.executemany(
            f"INSERT INTO {TABLE} (d0_date,d1_date,d2_date,ts_code,strategy,strategy_version,"
            "label_contract_version,cohort,primary_pattern,name,sw_l2_code,sw_l2_name,"
            "d0_close,d2_close,max_high_d1_d2,min_low_d1_d2,touch_up,close_win,path_state,"
            "stock_d2_return,industry_d2_return,industry_excess,max_drawdown,d1_verdict,"
            "evaluable,unavailable_reason,computed_at,d1_reference_price,d1_touch_up) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            records,
        )
    return len(records)


def open_due(
    as_of: date, *, strategy: str = "K9", strategy_version: str = STRATEGY_VERSION,
    parquet_dir: Optional[Path] = None, db_path: Optional[Path] = None,
) -> int:
    """恢复最近三个正式清单批次中漏建的 D0 预测。"""
    with readonly_tables("k9_listing_entries.strategy_version", db_path=db_path) as conn:
        dates = [] if conn is None else [r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM k9_listing_entries WHERE strategy=? "
            "AND strategy_version=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 3",
            (strategy, strategy_version, _d(as_of)),
        ).fetchall()]
        existing = set() if conn is None else {r[0] for r in conn.execute(
            f"SELECT DISTINCT d0_date FROM {TABLE} WHERE strategy=? AND strategy_version=? "
            "AND cohort=?",
            (strategy, strategy_version, COHORT_FINAL),
        ).fetchall()} if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone() else set()
    return sum(open_day(
        _day(raw), strategy=strategy, strategy_version=strategy_version,
        parquet_dir=parquet_dir, db_path=db_path,
    ) for raw in dates if raw not in existing)


def active_queue_count(
    as_of: date, *, strategy: str = "K9", strategy_version: str = STRATEGY_VERSION,
    db_path: Optional[Path] = None,
) -> int:
    """D0/D1/D2 尚未结算的正式预测数；结构上最多 3 × 20 = 60。"""
    with readonly_tables(f"{TABLE}.strategy_version", db_path=db_path) as conn:
        if conn is None:
            return 0
        count = int(conn.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE strategy=? AND strategy_version=? "
            "AND cohort=? AND path_state='pending' AND d0_date<=? AND d2_date>=?",
            (strategy, strategy_version, COHORT_FINAL, _d(as_of), _d(as_of)),
        ).fetchone()[0])
    if count > ACTIVE_QUEUE_LIMIT:
        raise ValueError(f"K9-v2 活跃预测队列 {count} 条，超过 {ACTIVE_QUEUE_LIMIT} 条上限")
    return count


def refresh_day(
    d0: date, *, as_of: date, strategy: str = "K9",
    strategy_version: str = STRATEGY_VERSION,
    parquet_dir: Optional[Path] = None, db_path: Optional[Path] = None,
) -> bool:
    """D2 到齐即幂等结算；行情不可评价也落终态，绝不顺延。"""
    sessions = _forward_two(d0, as_of)
    if len(sessions) < 2:
        return False
    d1, d2 = sessions
    with readonly_tables(
        "k9_listing_entries.strategy_version", "k9_runs.scoring_contract_json",
        "k9_channel_hits.strategy_version", "k9_d1_verdicts.verdict",
        db_path=db_path,
    ) as conn:
        if conn is None:
            return False
        listing = conn.execute(
            "SELECT ts_code,name,sw_l2_code,sw_l2_name,primary_pattern "
            "FROM k9_listing_entries WHERE trade_date=? AND strategy=? "
            "AND strategy_version=? ORDER BY rank,ts_code",
            (_d(d0), strategy, strategy_version),
        ).fetchall()
        run = conn.execute(
            "SELECT label_contract_version,scoring_contract_json FROM k9_runs "
            "WHERE trade_date=? AND strategy=? AND strategy_version=?",
            (_d(d0), strategy, strategy_version),
        ).fetchone()
        if not listing or run is None:
            return False
        strict = conn.execute(
            "SELECT h.ts_code,MIN(h.pattern),MAX(l.name),MAX(l.sw_l2_code),MAX(l.sw_l2_name) "
            "FROM k9_channel_hits h LEFT JOIN k9_listing_entries l "
            "ON l.trade_date=h.trade_date AND l.ts_code=h.ts_code "
            "AND l.strategy_version=h.strategy_version "
            "WHERE h.trade_date=? AND h.strategy_version=? AND h.tier='strict' "
            "GROUP BY h.ts_code ORDER BY h.ts_code",
            (_d(d0), strategy_version),
        ).fetchall()
        verdict_rows = conn.execute(
            "SELECT ts_code,verdict,open30_readings_json FROM k9_d1_verdicts "
            "WHERE d0_date=? AND strategy=?",
            (_d(d0), strategy),
        ).fetchall() if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='k9_d1_verdicts'"
        ).fetchone() else []

    verdicts = {str(row[0]): row[1] for row in verdict_rows}
    d1_references = {}
    for code, _, payload in verdict_rows:
        try:
            readings = json.loads(payload) if payload else {}
        except (TypeError, json.JSONDecodeError):
            readings = {}
        d1_references[str(code)] = _finite(readings.get("last_valid_trade_at_10_00"))

    contract = json.loads(run[1])
    threshold_u = float(contract["touchThresholdU"])
    risk_l = float(contract["riskLineL"])
    d1_reference_kind = str(contract["d1Reference"])
    baseline_kind = str(contract["matchedBaseline"])
    if d1_reference_kind != "last_valid_trade_at_10_00":
        raise ValueError(f"K9-v2 不支持的 D1 参考价合同: {d1_reference_kind!r}")
    try:
        packs = [fact_store.load_pack(day, parquet_dir=parquet_dir, db_path=db_path)
                 for day in (d0, d1, d2)]
        d0_rows, d1_rows, d2_rows = [_rows(pack.rows) for pack in packs]
    except (fact_store.PackNotFrozen, FileNotFoundError):
        # D2 已到但事实包缺失：本批次应退出队列；留下不可评价而非无限等待。
        d0_rows, d1_rows, d2_rows = {}, {}, {}

    industry = _industry_returns(d0_rows, d2_rows)
    market = _market_return(d0_rows, d2_rows)
    final_meta = {
        str(code): (name, l2, l2_name, pattern)
        for code, name, l2, l2_name, pattern in listing
    }
    strict_meta = {
        str(code): (name, l2, l2_name, pattern)
        for code, pattern, name, l2, l2_name in strict
    }
    computed = _now()

    def actual_record(code: str, meta: tuple, cohort: str) -> tuple:
        name, l2, l2_name, pattern = meta
        start, day1, day2 = d0_rows.get(code, {}), d1_rows.get(code, {}), d2_rows.get(code, {})
        d0_close, d2_close = _finite(start.get("close")), _finite(day2.get("close"))
        highs = [_finite(day1.get("high")), _finite(day2.get("high"))]
        lows = [_finite(day1.get("low")), _finite(day2.get("low"))]
        highs, lows = [v for v in highs if v is not None], [v for v in lows if v is not None]
        max_high, min_low = (max(highs) if highs else None), (min(lows) if lows else None)
        stock_ret = _return(d2_close, d0_close)
        industry_ret = industry.get(str(l2)) if l2 else None
        excess = None if stock_ret is None or industry_ret is None else stock_ret - industry_ret
        drawdown = _return(min_low, d0_close)
        evaluable = d0_close is not None and d2_close is not None and max_high is not None and min_low is not None
        touch = None
        path = "unavailable"
        if evaluable:
            up_days: List[int] = []
            risk_days: List[int] = []
            for index, day_row in enumerate((day1, day2), 1):
                up_move = _return(_finite(day_row.get("high")), d0_close)
                down_move = _return(_finite(day_row.get("low")), d0_close)
                if not _one_line_limit_up(day_row) and up_move is not None \
                        and up_move >= threshold_u:
                    up_days.append(index)
                if down_move is not None and down_move <= -risk_l:
                    risk_days.append(index)
            touch = int(bool(up_days))
            if up_days and risk_days:
                path = ("unknown" if up_days[0] == risk_days[0]
                        else "up_first" if up_days[0] < risk_days[0] else "risk_first")
            elif up_days:
                path = "up_only"
            elif risk_days:
                path = "risk_only"
            else:
                path = "neither"
        hit_risk = evaluable and drawdown is not None and drawdown <= -risk_l
        if hit_risk and path == "neither":
            path = "risk_only"
        d1_reference = d1_references.get(code)
        d1_touch = None
        if d1_reference is not None and d1_reference > 0 and not _one_line_limit_up(day1):
            d1_touch = int(any(
                not _one_line_limit_up(day_row)
                and (move := _return(_finite(day_row.get("high")), d1_reference)) is not None
                and move >= threshold_u
                for day_row in (day1, day2)
            ))
        reason = None if evaluable else "D0/D1/D2 行情不完整"
        return (
            _d(d0), _d(d1), _d(d2), code, strategy, strategy_version, run[0], cohort,
            pattern, name, l2, l2_name, d0_close, d2_close, max_high, min_low, touch,
            None if stock_ret is None else int(stock_ret > 0), path, stock_ret, industry_ret,
            excess, drawdown, verdicts.get(code), int(evaluable), reason, computed,
            d1_reference, d1_touch,
        )

    records = [actual_record(code, meta, COHORT_FINAL) for code, meta in final_meta.items()]
    records += [actual_record(code, meta, COHORT_STRICT) for code, meta in strict_meta.items()]
    # 匹配基准按最终清单逐票冻结，保证总表与 P1—P4 的分母可追溯。
    for code, (name, l2, l2_name, pattern) in final_meta.items():
        baseline_ret = industry.get(str(l2)) if baseline_kind == "industryMedian" and l2 else market
        records.append((
            _d(d0), _d(d1), _d(d2), code, strategy, strategy_version, run[0],
            COHORT_BASELINE, pattern, name, l2, l2_name, None, None, None, None, None,
            None if baseline_ret is None else int(baseline_ret > 0), "baseline",
            baseline_ret, None, None, None, verdicts.get(code),
            int(baseline_ret is not None),
            None if baseline_ret is not None else "匹配基准不可得", computed, None, None,
        ))

    init_schema(db_path)
    with connection(db_path) as conn:
        conn.execute(
            f"DELETE FROM {TABLE} WHERE d0_date=? AND strategy=? AND strategy_version=?",
            (_d(d0), strategy, strategy_version),
        )
        conn.executemany(
            f"INSERT INTO {TABLE} (d0_date,d1_date,d2_date,ts_code,strategy,strategy_version,"
            "label_contract_version,cohort,primary_pattern,name,sw_l2_code,sw_l2_name,"
            "d0_close,d2_close,max_high_d1_d2,min_low_d1_d2,touch_up,close_win,path_state,"
            "stock_d2_return,industry_d2_return,industry_excess,max_drawdown,d1_verdict,"
            "evaluable,unavailable_reason,computed_at,d1_reference_price,d1_touch_up) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            records,
        )
    return True


def refresh_due(
    as_of: date, *, strategy: str = "K9", strategy_version: str = STRATEGY_VERSION,
    parquet_dir: Optional[Path] = None, db_path: Optional[Path] = None,
) -> int:
    with readonly_tables("k9_listing_entries.strategy_version", db_path=db_path) as conn:
        rows = [] if conn is None else conn.execute(
            "SELECT DISTINCT trade_date FROM k9_listing_entries WHERE strategy=? "
            "AND strategy_version=? AND trade_date<=? ORDER BY trade_date",
            (strategy, strategy_version, _d(as_of)),
        ).fetchall()
    return sum(
        refresh_day(_day(raw), as_of=as_of, strategy=strategy,
                    strategy_version=strategy_version,
                    parquet_dir=parquet_dir, db_path=db_path)
        for (raw,) in rows
    )


def _ratio(num: int, den: int) -> Optional[float]:
    return None if den == 0 else num / den


def _metrics(rows: Sequence[tuple]) -> Dict[str, Any]:
    final = [r for r in rows if r[3] == COHORT_FINAL and r[10]]
    strict = [r for r in rows if r[3] == COHORT_STRICT and r[10]]
    baseline = [r for r in rows if r[3] == COHORT_BASELINE and r[10]]
    touches = [r for r in final if r[5] is not None]
    wins = [r for r in final if r[6] is not None]
    excess = [float(r[8]) for r in final if r[8] is not None]
    drawdowns = [float(r[9]) for r in final if r[9] is not None]
    final_returns = [float(r[7]) for r in final if r[7] is not None]
    strict_returns = [float(r[7]) for r in strict if r[7] is not None]
    baseline_returns = [float(r[7]) for r in baseline if r[7] is not None]
    final_mean = statistics.mean(final_returns) if final_returns else None
    strict_mean = statistics.mean(strict_returns) if strict_returns else None
    baseline_mean = statistics.mean(baseline_returns) if baseline_returns else None
    return {
        "sampleCount": len(final),
        "touchRate": _ratio(sum(int(r[5]) for r in touches), len(touches)),
        "touchNumerator": sum(int(r[5]) for r in touches), "touchDenominator": len(touches),
        "d2CloseWinRate": _ratio(sum(int(r[6]) for r in wins), len(wins)),
        "d2CloseWinNumerator": sum(int(r[6]) for r in wins),
        "d2CloseWinDenominator": len(wins),
        "averageIndustryExcess": statistics.mean(excess) if excess else None,
        "averageMaxDrawdown": statistics.mean(drawdowns) if drawdowns else None,
        "finalListingLift": {
            "finalMeanD2Return": final_mean,
            "strictRecallMeanD2Return": strict_mean,
            "matchedBaselineMeanD2Return": baseline_mean,
            "vsStrictRecall": None if final_mean is None or strict_mean is None else final_mean - strict_mean,
            "vsMatchedBaseline": None if final_mean is None or baseline_mean is None else final_mean - baseline_mean,
        },
    }


def load_scorecard(
    *, window: int = 20, strategy: str = "K9",
    strategy_version: str = STRATEGY_VERSION, db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """最近 window 个 D2 已结算批次；只读 K9-v2，旧观察窗数据不进入分母。"""
    with readonly_tables(f"{TABLE}.strategy_version", db_path=db_path) as conn:
        dates = [] if conn is None else [r[0] for r in conn.execute(
            f"SELECT DISTINCT d0_date FROM {TABLE} WHERE strategy=? AND strategy_version=? "
            "ORDER BY d0_date DESC LIMIT ?", (strategy, strategy_version, int(window)),
        ).fetchall()]
        if not dates or conn is None:
            rows: Sequence[tuple] = ()
        else:
            marks = ",".join("?" for _ in dates)
            rows = conn.execute(
                f"SELECT d0_date,d1_date,d2_date,cohort,primary_pattern,touch_up,close_win,"
                f"stock_d2_return,industry_excess,max_drawdown,evaluable,d1_verdict,path_state,"
                f"ts_code,unavailable_reason,d1_touch_up FROM {TABLE} WHERE strategy=? "
                f"AND strategy_version=? AND d0_date IN ({marks}) ORDER BY d0_date DESC,ts_code",
                (strategy, strategy_version, *dates),
            ).fetchall()
    overall = _metrics(rows)
    by_pattern = {pattern: _metrics([r for r in rows if r[4] == pattern])
                  for pattern in PATTERNS}
    final = [r for r in rows if r[3] == COHORT_FINAL]
    settled = [r for r in final if r[11] in ("confirmed", "rejected", "observed")]
    confirmed_count = sum(r[11] == "confirmed" for r in settled)
    aux = {
        "confirmationRate": {
            "touchRate": _ratio(confirmed_count, len(settled)),
            "numerator": confirmed_count, "denominator": len(settled),
        }
    }
    for verdict in ("confirmed", "rejected", "observed"):
        group = [r for r in final if r[11] == verdict and r[15] is not None]
        aux[verdict] = {
            "touchRate": _ratio(sum(int(r[15]) for r in group), len(group)),
            "numerator": sum(int(r[15]) for r in group), "denominator": len(group),
        }
    return {
        "strategyVersion": strategy_version,
        "labelContractVersion": "d2-v1",
        "window": int(window), "settledDays": len(dates),
        "activeQueueCount": active_queue_count(
            date.today(), strategy=strategy, strategy_version=strategy_version, db_path=db_path),
        "activeQueueLimit": ACTIVE_QUEUE_LIMIT,
        "listingCount": overall["sampleCount"],
        "overall": overall, "byPattern": by_pattern, "d1Aux": aux,
        "latestD0Date": dates[0] if dates else None,
        "rows": [
            {
                "d0Date": r[0], "d1Date": r[1], "d2Date": r[2], "cohort": r[3],
                "primaryPattern": r[4], "touchUp": None if r[5] is None else bool(r[5]),
                "closeWin": None if r[6] is None else bool(r[6]), "stockD2Return": r[7],
                "industryExcess": r[8], "maxDrawdown": r[9], "evaluable": bool(r[10]),
                "d1Verdict": r[11], "pathState": r[12], "tsCode": r[13],
                "unavailableReason": r[14],
                "d1TouchUp": None if r[15] is None else bool(r[15]),
            } for r in rows if r[3] == COHORT_FINAL
        ],
    }


__all__ = [
    "TABLE", "STRATEGY_VERSION", "ACTIVE_QUEUE_LIMIT", "open_day", "open_due",
    "active_queue_count", "refresh_day", "refresh_due", "load_scorecard",
]
