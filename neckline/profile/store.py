"""`profile_preference` / `profile_capability` 读写(plan §五 V2-⑫-B,DDL 见 ①)。

**「每期一版」——同一 `(as_of_date, dimension, value)` 重算 = 覆盖**(`INSERT` +
`ON CONFLICT DO UPDATE`,同 `review/store.py::save_weekly_review` 的幂等惯例),
不是 append-only(与 `user_actions`/`basket_verification` 那一类"审计事件流"不同:
画像是"当前时点的一份快照",旧快照的价值在 `as_of_date` 本身留痕,不需要同一
`as_of_date` 内的多版本历史)。

**`CapabilityRow.verdict` 不落库**——两张表的 DDL(① 定死)都没有说明性文本列,
"哪些偏好是优势/哪些是重复性错误"是由已持久化的数字(`win_rate`/`vs_peer_delta`
/`confidence`)派生的结论,消费方随时可以拿这四个数重新推导同一句话(同
`eval.metrics.Verdict`:结论是读时计算,不是需要单独持久化的事实)。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from neckline.db import connection, init_schema
from neckline.profile.capability import CapabilityRow
from neckline.profile.preference import PreferenceRow


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_preference(
    as_of_date: str, rows: Sequence[PreferenceRow], db_path: Optional[Path] = None,
) -> int:
    """落 `profile_preference`(每条 `(dimension, value)` 覆盖同一 `as_of_date`
    下的旧行)。返回写入行数。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        for r in rows:
            conn.execute(
                "INSERT INTO profile_preference "
                "(as_of_date, dimension, value, share, sample_n, window_start, window_end, "
                "confidence, computed_at) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(as_of_date, dimension, value) DO UPDATE SET "
                "share=excluded.share, sample_n=excluded.sample_n, "
                "window_start=excluded.window_start, window_end=excluded.window_end, "
                "confidence=excluded.confidence, computed_at=excluded.computed_at",
                (as_of_date, r.dimension, r.value, r.share, r.sample_n,
                 r.window_start, r.window_end, r.confidence, now),
            )
    return len(rows)


def load_preference(as_of_date: str, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """某期已落库的偏好画像(升序 `dimension, value`)。查无该期 → 空列表
    (正常场景:那一期还没算过,不是异常)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT dimension, value, share, sample_n, window_start, window_end, "
            "confidence, computed_at FROM profile_preference "
            "WHERE as_of_date=? ORDER BY dimension, value",
            (as_of_date,),
        ).fetchall()
    return [
        {
            "dimension": r[0], "value": r[1], "share": r[2], "sampleN": r[3],
            "windowStart": r[4], "windowEnd": r[5], "confidence": r[6], "computedAt": r[7],
        }
        for r in rows
    ]


def save_capability(
    as_of_date: str, rows: Sequence[CapabilityRow], db_path: Optional[Path] = None,
) -> int:
    """落 `profile_capability`。**`verdict` 不落库**(见模块头)。返回写入行数。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        for r in rows:
            conn.execute(
                "INSERT INTO profile_capability "
                "(as_of_date, dimension, value, sample_n, win_rate, profit_factor, avg_mfe, "
                "avg_mae, vs_peer_delta, window_start, window_end, confidence, computed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(as_of_date, dimension, value) DO UPDATE SET "
                "sample_n=excluded.sample_n, win_rate=excluded.win_rate, "
                "profit_factor=excluded.profit_factor, avg_mfe=excluded.avg_mfe, "
                "avg_mae=excluded.avg_mae, vs_peer_delta=excluded.vs_peer_delta, "
                "window_start=excluded.window_start, window_end=excluded.window_end, "
                "confidence=excluded.confidence, computed_at=excluded.computed_at",
                (as_of_date, r.dimension, r.value, r.sample_n, r.win_rate, r.profit_factor,
                 r.avg_mfe, r.avg_mae, r.vs_peer_delta, r.window_start, r.window_end,
                 r.confidence, now),
            )
    return len(rows)


def load_capability(as_of_date: str, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """某期已落库的能力画像(升序 `dimension, value`)。⚠ 不含 `verdict`
    (未持久化字段,需要文案的调用方重新跑 `compute_capability` 或自行按
    `win_rate`/`vs_peer_delta`/`confidence` 推导)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT dimension, value, sample_n, win_rate, profit_factor, avg_mfe, avg_mae, "
            "vs_peer_delta, window_start, window_end, confidence, computed_at "
            "FROM profile_capability WHERE as_of_date=? ORDER BY dimension, value",
            (as_of_date,),
        ).fetchall()
    return [
        {
            "dimension": r[0], "value": r[1], "sampleN": r[2], "winRate": r[3],
            "profitFactor": r[4], "avgMfe": r[5], "avgMae": r[6], "vsPeerDelta": r[7],
            "windowStart": r[8], "windowEnd": r[9], "confidence": r[10], "computedAt": r[11],
        }
        for r in rows
    ]


__all__ = ["save_preference", "load_preference", "save_capability", "load_capability"]
