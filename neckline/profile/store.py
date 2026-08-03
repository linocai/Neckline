"""`profile_preference` / `profile_capability` 读写(plan §五 V2-⑫-B,DDL 见 ①)。

**「每期一版」——同一 `as_of_date` + 同一 `dimension` 重算 = 该维度整段替换**
(先 `DELETE` 该维度旧行、再 `INSERT`,**同一事务**),不是 append-only(与
`user_actions`/`basket_verification` 那一类"审计事件流"不同:画像是"当前时点的一份
快照",旧快照的价值在 `as_of_date` 本身留痕,不需要同一 `as_of_date` 内的多版本历史)。

⚠ **为什么不能只靠 UPSERT(契约线审计 🟡 Y3,2026-08-03 修)**:`ON CONFLICT DO UPDATE`
只覆盖**同键**行 —— 同一期重算时,凡是新一轮**不再产出**的 `(dimension, value)` 旧行
**原样残留**,和新行混在同一期里,`load_*` 又不区分 `computed_at`。「每期一版」于是悄悄
变成「每期 = 多次运行的并集」:上午跑完画像、用户又补录两笔、傍晚重跑,某个占比已经
归零的题材值仍以旧 `share` 挂在当期里,**该维度 share 合计 > 1**,而这是一份要拿去跟
用户讲「你偏好什么」的画像。

**按 dimension 而不是按整期删**(⑫ 的 `share` 是**维度内**归一:`share = n / 该维度
总数`),所以「一致性单位」就是维度:全量重算时每个维度各自整段换新,效果与整期替换
相同;将来若有人只重算某一个维度,也不会顺手抹掉别的维度的当期结果。
**批次为空 = 这一期真的什么都没算出来** → 整期清空(如实,不留上一次的残影)。
`ON CONFLICT DO UPDATE` 保留不动:清完之后它只可能被**同一批里的重复键**触发,那时
"后来者覆盖"是无害的幂等语义,不是 Y3 治的那个跨批次残留。

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


def _clear_period(
    conn, table: str, as_of_date: str, dimensions: Sequence[str],
) -> None:
    """🟡 Y3:同期同维度的旧行先清干净(**与后续 INSERT 同一事务**,调用方持有 conn)。

    `dimensions` 为空(= 本次一行都没算出来)→ **整期清空**:那是「这期算出来是空的」
    这个事实本身,留着上一次的残影等于让读侧看见一份没人算过的画像。
    """
    if dimensions:
        marks = ",".join("?" * len(dimensions))
        conn.execute(
            f"DELETE FROM {table} WHERE as_of_date=? AND dimension IN ({marks})",
            (as_of_date, *dimensions),
        )
    else:
        conn.execute(f"DELETE FROM {table} WHERE as_of_date=?", (as_of_date,))


def save_preference(
    as_of_date: str, rows: Sequence[PreferenceRow], db_path: Optional[Path] = None,
) -> int:
    """落 `profile_preference`(本批涉及的每个 `dimension` 在该 `as_of_date` 下**整段
    替换**,见模块头 🟡 Y3)。返回写入行数。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        _clear_period(conn, "profile_preference", as_of_date,
                      sorted({r.dimension for r in rows}))
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
    """落 `profile_capability`(同 `save_preference` 的整段替换语义)。**`verdict` 不
    落库**(见模块头)。返回写入行数。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        _clear_period(conn, "profile_capability", as_of_date,
                      sorted({r.dimension for r in rows}))
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


_TABLE_BY_KIND = {"preference": "profile_preference", "capability": "profile_capability"}


def latest_as_of(kind: str, *, db_path: Optional[Path] = None) -> Optional[str]:
    """某张画像表里**最近一期**的 `as_of_date`('YYYYMMDD');一期都没有 → `None`。

    `None` 是「**从未算过**」,与「算过、这一期一行都没有」(返回日期 + 空行列表)是
    两件事 —— 调用方(⑭-B 的 `GET /profile/*`)据此给出不同的文案,⛔ 不许合并
    (§3.8「没有」与「没看」必须能分开)。`kind` ∈ {`preference`, `capability`}。"""
    table = _TABLE_BY_KIND.get(kind)
    if table is None:
        raise ValueError(f"latest_as_of: 未知 kind={kind!r},只接受 {sorted(_TABLE_BY_KIND)}")
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(f"SELECT MAX(as_of_date) FROM {table}").fetchone()
    return row[0] if row and row[0] else None


__all__ = ["save_preference", "load_preference", "save_capability", "load_capability",
           "latest_as_of"]
