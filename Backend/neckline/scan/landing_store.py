"""落地起跳原始读数表 `landing_metrics_daily` 的读写单一通道(plan §五 V2.2-③-C,
🔴 2026-08-09 用户裁定 #11 后:P0-23 / §七 P4-50「EOD 预计算落表、在线只读」纪律
依旧,只是表里不再有任何判定结论,只有事实型读数)。

体例照 `scan/regime_store.py`:特征装配的**唯一实现在 `scan/landing.py`**(本模块
从它 import,方向 store → landing 单向;`landing.py` 零写库,写只发生在这里)。

**读侧纪律**:在线路径(批 2 的六关⑤位置关 / ④ 选股时钟 D1 验证)只读本表,
**缺行 = 「不知道」= 合法结果**(返回 `None` / 空 DataFrame,不崩、⛔ 不现算自愈)
—— 消费方(`gates.py` 位置关)按「缺行 ⛔ 不给 T1、也不拦,喂 LLM 时如实说
"这票今天没有读数"」处理(plan §五 ③-C)。

**写侧纪律**:`refresh_landing_metrics` 按**连续日块**批算(全市场逐票 × ~145 交易日
回看,逐日各自取数会把同一段 parquet 反复读 N 遍——P0-23 的算法成本纪律要求
按块摊销 I/O;⚠ 与 `regime_store` 逐日独立保险丝的体例刻意不同,登记于此),
每天一个事务 upsert;单块计算炸了只 WARNING + 该块整段计入 `failed`,不断批
(失败日**不落行**,缺行由读侧如实披露,⛔ 不落猜出来的行冒充"算过了")。
同日重跑幂等覆盖(`(trade_date, ts_code)` 主键)。

⚠ **命名与语义已对齐(裁定 #11 返工收尾时统一改名,2026-08-09)**:表 `landing_state_daily`
→ `landing_metrics_daily`,函数 `refresh_landing_states`/`compute_landing_states` →
`refresh_landing_metrics`/`compute_landing_metrics`。⛔ **别把 `state` 那套名字改回来**
——这张表自裁定 #11 起**只存读数、不存任何状态/结论**,名字里出现 `state` 就是在
撒谎。`tests/test_scan_layer_guardrails.py` 的 P0-23 写入口黑名单已同步到新名
(⚠ 那份黑名单**按函数名字面匹配**:写入口再改名必须同步改它,否则守门会变成
「测试还绿、但已经不守任何东西」——本次返工就真出现过这一幕)。"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import polars as pl

from neckline.db import connection, init_schema
from neckline.scan.landing import TABLE, compute_landing_metrics

logger = logging.getLogger(__name__)

_COLUMNS = "trade_date, ts_code, metrics_json, metrics_missing, computed_at"
_UPSERT_SQL = f"INSERT OR REPLACE INTO {TABLE} ({_COLUMNS}) VALUES (?,?,?,?,?)"

# 一次批算的最大连续天数(内存上界的一部分:块越大,取数区间越长)。60 天 ≈ 一个
# 季度回填的单块;增量日更(1 天)与三路等价测试(≤3 天)都远小于它。
_CHUNK_DAYS = 60


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def refresh_landing_metrics(
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
            rows = compute_landing_metrics(chunk, db_path=db_path, parquet_dir=parquet_dir)
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
                json.dumps(r["metrics"], ensure_ascii=False, sort_keys=True),
                json.dumps(r["metrics_missing"], ensure_ascii=False, sort_keys=True),
                now,
            ))
        for day_str in sorted(by_day):
            with connection(db_path) as conn:
                conn.executemany(_UPSERT_SQL, by_day[day_str])
            stats["rows"] += len(by_day[day_str])
    return stats


def load_landing_metrics(
    trade_date: date, *, db_path: Optional[Path] = None
) -> pl.DataFrame:
    """某日全市场读数行(按 ts_code 升序的 DataFrame;在线按日读的唯一入口)。
    空 DataFrame = 当日缺行 = 「没算过/没数据」,合法结果,⛔ 不现算自愈。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM {TABLE} WHERE trade_date=? ORDER BY ts_code ASC",
            (_d(trade_date),),
        ).fetchall()
    schema = {
        "trade_date": pl.String, "ts_code": pl.String,
        "metrics_json": pl.String, "metrics_missing": pl.String,
        "computed_at": pl.String,
    }
    return pl.DataFrame(rows, schema=schema, orient="row")


def load_landing_metric(
    trade_date: date, ts_code: str, *, db_path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """单票单日读数行(dict,`metrics`/`metrics_missing` 已反序列化)。`None` = 缺行
    = 「不知道」(停牌当日无 daily 行 / 引擎没跑),合法结果,消费方按「⛔ 不给
    T1、也不拦」处理(plan §五 ③-C 缺行语义)。"""
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
        "metrics": json.loads(row[2]),
        "metrics_missing": json.loads(row[3]),
        "computed_at": row[4],
    }


def landing_metrics_coverage(
    trade_date: date, *, db_path: Optional[Path] = None
) -> Dict[str, Any]:
    """某日读数覆盖率 + 缺项分布(CLI 回放打印 / 自检用)。返回
    `{"total": 全市场判定行数, "missing_counts": {读数键: 该项缺失的行数}}`
    (`total=0` = 当日缺行)。⚠ 这不是"四态分布"——裁定 #11 后机械层没有"态"这个
    东西,这是诚实的数据完整性快照,供人核对"读数算全了没有"。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT metrics_missing FROM {TABLE} WHERE trade_date=?",
            (_d(trade_date),),
        ).fetchall()
    missing_counts: Dict[str, int] = {}
    for (blob,) in rows:
        for key in json.loads(blob):
            missing_counts[key] = missing_counts.get(key, 0) + 1
    return {"total": len(rows), "missing_counts": missing_counts}


__all__ = [
    "TABLE",
    "refresh_landing_metrics",
    "load_landing_metrics",
    "load_landing_metric",
    "landing_metrics_coverage",
]
