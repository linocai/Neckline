"""行情状态预计算表 `market_regime_daily` 的读写单一通道(plan §五 V2.2-②,
§七 P0-23:EOD 预计算落表、在线只读;§3.11-B 点名的两张全市场级预计算表之一)。

体例照 `report/industry_strength_store.py`:`TABLE` 常量 / `_d` / `_now` /
upsert / 按日读 / 区间读。判定与五维采集的**唯一实现在 `scan/regime.py`**
(本模块从它 import,方向 store → regime 单向;`regime.py` 零写库,写只发生在
这里)。

**读侧纪律**:在线路径(报告 / 端点 / 未来 ③ 市场关)只读本表,**缺行 = 「不知道」
= 合法结果**(返回 `None` / 空列表,不崩、⛔ 不现算自愈)—— 消费方按保险丝语义
处理(降级方向 = 不拦 + `available=false` 显式披露,P0-39/②-缺行裁定)。

**写侧纪律**:`refresh_market_regime` 逐日「算 → INSERT OR REPLACE」,每天一个
事务;单日计算炸了只 WARNING + 计入 `failed`,不断批(该日**不落行** —— 缺行由
读侧如实披露,⛔ 不落一个猜出来的行冒充"算过了")。同日重跑幂等覆盖
(`trade_date` 主键)。"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from neckline.db import connection, init_schema
from neckline.scan.regime import RegimeDayResult, compute_market_regime_for_day

logger = logging.getLogger(__name__)

TABLE = "market_regime_daily"

_COLUMNS = (
    "trade_date, regime, regime_reason, inputs_json, strengthening_json, "
    "weakening_json, skeleton_version, computed_at"
)
_UPSERT_SQL = f"INSERT OR REPLACE INTO {TABLE} ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?)"


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _result_row(res: RegimeDayResult, now: str) -> tuple:
    return (
        _d(res.trade_date),
        res.regime,
        res.regime_reason,
        json.dumps(res.inputs, ensure_ascii=False, sort_keys=True),
        json.dumps(res.strengthening, ensure_ascii=False),
        json.dumps(res.weakening, ensure_ascii=False),
        res.skeleton_version,
        now,
    )


def refresh_market_regime(
    days: Iterable[date],
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """批算 + upsert 落表(16:35 晚间链 SEG_SCAN 日更与补算 CLI 共用同一实现)。
    升序逐日、每天一个事务;单日炸了 WARNING + `failed` 计数,不断批(见模块
    docstring「写侧纪律」)。返回 `{"days":…, "rows":…, "failed":…}`。"""
    init_schema(db_path)
    stats = {"days": 0, "rows": 0, "failed": 0}
    for d in sorted(set(days)):
        stats["days"] += 1
        try:
            res = compute_market_regime_for_day(d, db_path=db_path, parquet_dir=parquet_dir)
        except Exception:  # noqa: BLE001  保险丝:单日失败不断批,该日缺行由读侧披露
            logger.warning("[regime_store] %s 行情状态批算异常(该日不落行)", _d(d), exc_info=True)
            stats["failed"] += 1
            continue
        with connection(db_path) as conn:
            conn.execute(_UPSERT_SQL, _result_row(res, _now()))
        stats["rows"] += 1
    return stats


def _row_to_dict(row: tuple) -> Dict[str, Any]:
    return {
        "trade_date": row[0],
        "regime": row[1],
        "regime_reason": row[2],
        "inputs": json.loads(row[3]),
        "strengthening": json.loads(row[4]),
        "weakening": json.loads(row[5]),
        "skeleton_version": row[6],
        "computed_at": row[7],
    }


def load_market_regime(
    trade_date: date, *, db_path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """按日读(在线唯一入口)。`None` = 当日缺行 = 「不知道」,合法结果,⛔ 不现算
    自愈(写在 16:35、读在其后,职责不混)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM {TABLE} WHERE trade_date=?", (_d(trade_date),)
        ).fetchone()
    return _row_to_dict(row) if row is not None else None


def load_market_regime_range(
    start: date, end: date, *, db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """区间读(升序;区间内缺行的日子**不出现**,如实反映,不补默认行)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM {TABLE} WHERE trade_date>=? AND trade_date<=? "
            f"ORDER BY trade_date ASC",
            (_d(start), _d(end)),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def load_latest_market_regime(*, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """表内最近一日的判定行(端点 `date` 缺省时的取数;`None` = 表空 = 引擎从没
    跑过,如实披露)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM {TABLE} ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
    return _row_to_dict(row) if row is not None else None


__all__ = [
    "TABLE",
    "refresh_market_regime",
    "load_market_regime",
    "load_market_regime_range",
    "load_latest_market_regime",
]
