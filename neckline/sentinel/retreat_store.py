"""退潮哨兵逐拍指标台账(`retreat_metrics` 表,v1.1-H2 双级制重构)。三职责:
    1. `record_retreat_metrics` —— 每个盘中 tick 落一行(全量指标 + 判级 + 触发
       路径),供事后给红色刹车算命中率成绩单。
    2. `load_prev_tick_triggered` —— 读同一交易日"上一拍"(hhmm 严格更小的最近
       一行)的触发条件族,供持续性("连续 2 拍")判定(修法2)。
    3. `load_same_time_zaban_baseline` —— 读**昨日(上一交易日)同一时刻(±窗)**
       的关注池炸板率,供飙升条件"同时段对比"(修法1)。无数据 → None(飙升
       子判据静默失效)。

**为何 DB 而非纯内存**:①同时段对比天然要跨日持久;②持续性判据即便进程午间
重启也能从表里恢复上一拍(再叠加 `engine` 的"首拍不触发红色"保守闸,双保险);
③成绩单需要每拍留痕。落库入口统一走本模块,不在别处另写 `retreat_metrics` 的 SQL。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from neckline.calendar import prev_trading_day
from neckline.db import connection, init_schema
from neckline.sentinel.retreat import RetreatMetrics


def _d(trade_date: date) -> str:
    return trade_date.strftime("%Y%m%d")


def _hhmm_to_minutes(hhmm: str) -> Optional[int]:
    """'HHMM' → 自午夜起分钟数;非法输入 → None(不炸,调用方按缺失处理)。"""
    if not hhmm or len(hhmm) != 4 or not hhmm.isdigit():
        return None
    return int(hhmm[:2]) * 60 + int(hhmm[2:])


def record_retreat_metrics(
    metrics: RetreatMetrics,
    *,
    triggered: List[str],
    tier: str,
    red_via: List[str],
    db_path: Optional[Path] = None,
) -> None:
    """落/覆盖本 tick 的一行(PK=(trade_date,hhmm),`INSERT OR REPLACE` 幂等)。

    `hot_sector_sample_json`(V2-⑧-F):主线跳水样本构成的留痕,**每拍都落**(触发与否
    都落 —— 要审计的是"样本怎么来的",不是"触发那一刻长什么样")。

    `breadth_extra_sample_json`(⑧-G-D 追加要求,review 判定线 🟡-N1 一并处理,
    2026-08-03):昨日涨停宽度代理样本的需求量 vs 实际采纳量,同样每拍都落。"""
    init_schema(db_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connection(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO retreat_metrics "
            "(trade_date, hhmm, sample_size, limit_up_count, limit_down_count, zaban_count, "
            " zaban_rate, hot_sector_avg_chg, hot_sector_sample_json, breadth_extra_sample_json, "
            " triggered_json, red_via_json, tier, recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                _d(metrics.trade_date), metrics.hhmm, metrics.sample_size,
                metrics.limit_up_count, metrics.limit_down_count, metrics.zaban_count,
                metrics.zaban_rate, metrics.hot_sector_avg_chg,
                json.dumps(metrics.hot_sector_sample_detail, ensure_ascii=False),
                json.dumps(metrics.breadth_extra_sample_detail, ensure_ascii=False),
                json.dumps(triggered, ensure_ascii=False),
                json.dumps(red_via, ensure_ascii=False),
                tier, now,
            ),
        )


def load_prev_tick_triggered(
    trade_date: date, before_hhmm: str, db_path: Optional[Path] = None
) -> List[str]:
    """同一交易日 hhmm 严格小于 `before_hhmm` 的最近一行的触发条件族。无上一拍
    (当日首拍)→ 空列表。解析失败 → 空列表(保守:当作上一拍无触发)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT triggered_json FROM retreat_metrics "
            "WHERE trade_date=? AND hhmm<? ORDER BY hhmm DESC LIMIT 1",
            (_d(trade_date), before_hhmm),
        ).fetchone()
    if row is None or not row[0]:
        return []
    try:
        val = json.loads(row[0])
        return [str(x) for x in val] if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def load_same_time_zaban_baseline(
    trade_date: date, hhmm: str, *, window_min: int, db_path: Optional[Path] = None
) -> Optional[float]:
    """昨日(上一交易日)同一时刻(±`window_min` 分钟窗)关注池炸板率,取窗内
    时刻最接近的一行。无上一交易日行 / 窗内无数据 → None(飙升条件静默失效)。

    刻意只回上一**交易日**(`prev_trading_day`),不回退到更早的历史日:跨越假期
    或更久之前的同时段炸板率处在不同市场环境,拿来做"飙升基线"反而误导——宁可
    静默失效也不用陈旧基线(部署首日→后天才有完整基线,是已知且可接受的过渡)。
    """
    target = _hhmm_to_minutes(hhmm)
    if target is None:
        return None
    try:
        prev_day = prev_trading_day(trade_date)
    except Exception:  # noqa: BLE001  日历异常不该掀翻退潮判定,静默失效即可
        return None

    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT hhmm, zaban_rate FROM retreat_metrics WHERE trade_date=?",
            (_d(prev_day),),
        ).fetchall()

    best_rate: Optional[float] = None
    best_dist: Optional[int] = None
    for hh, rate in rows:
        m = _hhmm_to_minutes(hh)
        if m is None:
            continue
        dist = abs(m - target)
        if dist > window_min:
            continue
        if best_dist is None or dist < best_dist:
            best_dist, best_rate = dist, float(rate)
    return best_rate


__all__ = [
    "record_retreat_metrics",
    "load_prev_tick_triggered",
    "load_same_time_zaban_baseline",
]
