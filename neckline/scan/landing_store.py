"""落地起跳预计算表 `landing_state_daily` 的读写单一通道(plan §五 V2.2-③-C,
P0-23 / §七 P4-50:EOD 预计算落表、在线只读;§3.11-B 点名的两张全市场级预计算表
之二)。

体例照 `scan/regime_store.py`:判定与特征装配的**唯一实现在 `scan/landing.py`**
(本模块从它 import,方向 store → landing 单向;`landing.py` 零写库,写只发生在
这里)。

**读侧纪律**:在线路径(批 2 的六关位置关 / ④ 选股时钟 D1 验证)只读本表,
**缺行 = 「不知道」= 合法结果**(返回 `None` / 空 DataFrame,不崩、⛔ 不现算自愈)
—— 消费方按「none/缺行 ⛔ 不给 T1、也不拦」的既定语义处理(plan §五 ③-C 四态表)。

**写侧纪律**:`refresh_landing_states` 按**连续日块**批算(全市场逐票 × 145 交易日
回看,逐日各自取数会把同一段 parquet 反复读 N 遍——P0-23 的算法成本纪律要求
按块摊销 I/O;⚠ 与 `regime_store` 逐日独立保险丝的体例刻意不同,登记于此),
每天一个事务 upsert;单块计算炸了只 WARNING + 该块整段计入 `failed`,不断批
(失败日**不落行**,缺行由读侧如实披露,⛔ 不落猜出来的行冒充"算过了")。
同日重跑幂等覆盖(`(trade_date, ts_code)` 主键)。"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import polars as pl

from neckline.db import connection, init_schema
from neckline.scan.landing import TABLE, compute_landing_states

logger = logging.getLogger(__name__)

_COLUMNS = "trade_date, ts_code, state, state_reason, metrics_json, skeleton_version, computed_at"
_UPSERT_SQL = f"INSERT OR REPLACE INTO {TABLE} ({_COLUMNS}) VALUES (?,?,?,?,?,?,?)"

# 一次批算的最大连续天数(内存上界的一部分:块越大,取数区间越长)。60 天 ≈ 一个
# 季度回填的单块;增量日更(1 天)与三路等价测试(≤3 天)都远小于它。
_CHUNK_DAYS = 60


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def refresh_landing_states(
    days: Iterable[date],
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """批算 + upsert 落表(16:35 晚间链 SEG_SCAN 日更与补算 CLI 共用同一实现)。
    升序去重后按 `_CHUNK_DAYS` 分块;块内一次取数、每天一个事务写入。返回
    `{"days":…, "rows":…, "failed":…}`(`failed` = 计算异常的**天数**)。"""
    init_schema(db_path)
    uniq = sorted(set(days))
    stats = {"days": len(uniq), "rows": 0, "failed": 0}
    for i in range(0, len(uniq), _CHUNK_DAYS):
        chunk = uniq[i:i + _CHUNK_DAYS]
        try:
            rows = compute_landing_states(chunk, db_path=db_path, parquet_dir=parquet_dir)
        except Exception:  # noqa: BLE001  保险丝:单块失败不断批,缺行由读侧披露
            logger.warning(
                "[landing_store] %s ~ %s 落地起跳批算异常(该块不落行)",
                _d(chunk[0]), _d(chunk[-1]), exc_info=True,
            )
            stats["failed"] += len(chunk)
            continue
        by_day: Dict[str, List[tuple]] = {}
        now = _now()
        for r in rows:
            by_day.setdefault(r["trade_date"], []).append((
                r["trade_date"],
                r["ts_code"],
                r["state"],
                r["state_reason"],
                json.dumps(r["metrics"], ensure_ascii=False, sort_keys=True),
                r["skeleton_version"],
                now,
            ))
        for day_str in sorted(by_day):
            with connection(db_path) as conn:
                conn.executemany(_UPSERT_SQL, by_day[day_str])
            stats["rows"] += len(by_day[day_str])
    return stats


def load_landing_states(
    trade_date: date, *, db_path: Optional[Path] = None
) -> pl.DataFrame:
    """某日全市场判定行(按 ts_code 升序的 DataFrame;在线按日读的唯一入口)。
    空 DataFrame = 当日缺行 = 「没算过/没数据」,合法结果,⛔ 不现算自愈。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM {TABLE} WHERE trade_date=? ORDER BY ts_code ASC",
            (_d(trade_date),),
        ).fetchall()
    schema = {
        "trade_date": pl.String, "ts_code": pl.String, "state": pl.String,
        "state_reason": pl.String, "metrics_json": pl.String,
        "skeleton_version": pl.String, "computed_at": pl.String,
    }
    return pl.DataFrame(rows, schema=schema, orient="row")


def load_landing_state(
    trade_date: date, ts_code: str, *, db_path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """单票单日判定行(dict,`metrics` 已反序列化)。`None` = 缺行 = 「不知道」
    (停牌当日无 daily 行 / 引擎没跑),合法结果,消费方按「⛔ 不给 T1、也不拦」
    处理(plan §五 ③-C `none` 行语义的缺行版)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM {TABLE} WHERE trade_date=? AND ts_code=?",
            (_d(trade_date), ts_code),
        ).fetchone()
    if row is None:
        return None
    return {
        "trade_date": row[0],
        "ts_code": row[1],
        "state": row[2],
        "state_reason": row[3],
        "metrics": json.loads(row[4]),
        "skeleton_version": row[5],
        "computed_at": row[6],
    }


def landing_state_counts(
    trade_date: date, *, db_path: Optional[Path] = None
) -> Dict[str, int]:
    """某日四态 + none 的行数分布(CLI 回放打印 / 自检用;空 dict = 当日缺行)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT state, COUNT(*) FROM {TABLE} WHERE trade_date=? GROUP BY state",
            (_d(trade_date),),
        ).fetchall()
    return {state: int(n) for state, n in rows}


__all__ = [
    "TABLE",
    "refresh_landing_states",
    "load_landing_states",
    "load_landing_state",
    "landing_state_counts",
]
