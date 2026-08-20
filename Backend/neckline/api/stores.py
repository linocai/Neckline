"""API 层业务台账存取(plan 4A.4/4A.5):`devices`(APNs 注册)+ `inquiry_pool`
(问询台海选票,已退役历史队列表,只留只读)。极简 CRUD,幂等——沿本项目
既有 store 姿势(`report/store.py`/`dedup.py`),stdlib sqlite3 直连,不引 ORM。

**V2.1-① 问询台整链退役**:本文件原有的 `inquiry_log`(问询记录档案,v1.4-⑦-B)一节
——`create_inquiry_log`/`list_inquiry_logs`/`get_inquiry_log`/`_row_to_inquiry_log`/
`_INQUIRY_LOG_COLS`——已随问询台主体一并物理删除。`inquiry_log` 表**停写留档不 DROP**
(§七 P4-31,七张停写表之一);表内历史行只能靠 `sqlite3` 直查,本文件不再提供任何
读写函数(与 `inquiry_pool` 的「只留只读」处置**刻意不同**——那张表仍有周复盘
`review/reconcile.py` 在读,这张没有任何下游消费方,见 `neckline/db.py` 表头注释)。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from neckline.db import connection, init_schema, readonly_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


# —— devices(APNs 设备注册,plan 4A.5 / 4B.5)——————————————————————————————

def upsert_device(token: str, platform: str = "ios", db_path: Optional[Path] = None) -> None:
    """注册/更新一个 APNs device token(复用 LinoN `upsert_device_token` 语义)。同一
    token 再注册只刷新 `updated_at`,不重复建行。空 token → 静默忽略(客户端首次授权前
    可能拿不到 token,不应因此报错)。"""
    token = (token or "").strip()
    if not token:
        return
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO devices (token, platform, created_at, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(token) DO UPDATE SET platform=excluded.platform, updated_at=excluded.updated_at",
            (token, (platform or "ios").strip() or "ios", now, now),
        )


def list_device_tokens(db_path: Optional[Path] = None) -> List[str]:
    """全部已注册 device token(16:00 报告 / 退潮刹车推送时遍历)。"""
    try:
        with readonly_connection(db_path) as conn:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='devices'"
            ).fetchone():
                return []
            rows = conn.execute("SELECT token FROM devices ORDER BY created_at").fetchall()
    except FileNotFoundError:
        return []
    return [r[0] for r in rows]


# —— inquiry_pool(问询台海选池)—— ⚠ **V2-⑬-11/⑬-10 起只剩历史只读** ——————————
# 强制并入通道整条删除(裁定 #9 的连带):`add_to_inquiry_pool`(写)、
# `load_pending_inquiry_codes`(消费查询)、`mark_inquiry_pool_consumed`(消费标记)
# 三个函数**已物理删除**,表停写留档不 DROP。**只留 `load_inquiry_pool` 一个只读**
# —— 周复盘 `review/reconcile.py::check_plan_and_ledger` 用它判「计划内(问询台
# 海选池)」,那是对**历史行**的归因判定,删了会改写历史周的 plan_status。

def load_inquiry_pool(trade_date: date, db_path: Optional[Path] = None) -> List[dict]:
    """某交易日**入池当日**(`trade_date` 列)的票。返回 `{ts_code, name, reason,
    created_at}` 列表,按入池时间升序。

    ⚠ **V2-⑬-10 起这是本表唯一的访问函数**,且只有历史意义:V2 不再有任何写入方
    (`add_to_inquiry_pool` 已删),表里只剩 v1.1~v1.5.2 的历史行。唯一消费方 =
    `review/reconcile.py::check_plan_and_ledger` 的「计划内(问询台海选池)」判定
    —— 那是对历史成交的归因,**保留它是为了不改写历史周的 plan_status**。"""
    try:
        with readonly_connection(db_path) as conn:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='inquiry_pool'"
            ).fetchone():
                return []
            rows = conn.execute(
                "SELECT ts_code, name, reason, created_at FROM inquiry_pool "
                "WHERE trade_date=? ORDER BY created_at, ts_code",
                (_d(trade_date),),
            ).fetchall()
    except FileNotFoundError:
        return []
    return [{"ts_code": r[0], "name": r[1], "reason": r[2], "created_at": r[3]} for r in rows]


__all__ = [
    "upsert_device", "list_device_tokens", "load_inquiry_pool",
]
