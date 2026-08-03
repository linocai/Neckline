"""API 层业务台账存取(plan 4A.4/4A.5):`devices`(APNs 注册)+ `inquiry_pool`
(问询台海选票)+ `inquiry_log`(v1.4-⑦-B,问询记录档案)。极简 CRUD,幂等——沿本项目
既有 store 姿势(`report/store.py`/`sentinel/dedup.py`),stdlib sqlite3 直连,不引 ORM。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline.db import connection, init_schema


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
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute("SELECT token FROM devices ORDER BY created_at").fetchall()
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
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT ts_code, name, reason, created_at FROM inquiry_pool "
            "WHERE trade_date=? ORDER BY created_at, ts_code",
            (_d(trade_date),),
        ).fetchall()
    return [{"ts_code": r[0], "name": r[1], "reason": r[2], "created_at": r[3]} for r in rows]


# —— inquiry_log(问询记录档案,plan §五 v1.4-⑦-B / §七 P3-13)——————————————————
# 纯追加式档案,**不是队列**(见 `neckline.db` CREATE TABLE inquiry_log 表头注释
# 「与 inquiry_pool 是两件事」)——每行落库即完整终态,不需要「审计时间戳 + 独立
# 消费标记」两字段拆分那一套(那是给队列表用的模式)。本节函数与上面 `inquiry_pool`
# 一节**互不调用、互不读写对方的表**(单测断言见 `tests/test_api_inquiry.py`)。

_INQUIRY_LOG_COLS = (
    "id, created_at, ts_code, name, question, materials_json, answer, "
    "evidence_json, search_hits_json, verdict, position_id, decision_id"
)


def create_inquiry_log(
    ts_code: str,
    question: str,
    answer: str,
    verdict: str,
    *,
    name: Optional[str] = None,
    materials: Optional[Dict[str, Any]] = None,
    evidence: Optional[List[str]] = None,
    search_hits: Optional[List[Dict[str, Any]]] = None,
    position_id: Optional[int] = None,
    decision_id: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> int:
    """一次问询落一行(`api.inquiry.run_inquiry` 结尾的旁路写入调用,失败不影响
    当次回答——调用方自己包 try/except,本函数不做任何"静默吞异常"的处理,写不进
    去就如实抛出)。`ts_code` 经归一(同写入通道惯例,查询侧 `list_inquiry_logs` 的
    `ts_code` 过滤才对得上,同 `decision_log`/`positions` 既有先例)。`position_id`/
    `decision_id` 当前无任何调用方传值(见 `neckline.db` 表头注释),预留可空形参。
    返回新行 id。"""
    init_schema(db_path)
    from neckline.review.parse import normalize_ts_code
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO inquiry_log (created_at, ts_code, name, question, materials_json, "
            "answer, evidence_json, search_hits_json, verdict, position_id, decision_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                now, normalize_ts_code(ts_code), name, question,
                json.dumps(materials or {}, ensure_ascii=False),
                answer,
                json.dumps(list(evidence or []), ensure_ascii=False),
                json.dumps(list(search_hits or []), ensure_ascii=False),
                verdict, position_id, decision_id,
            ),
        )
        return int(cur.lastrowid)


def _row_to_inquiry_log(r) -> dict:
    return {
        "id": r[0], "createdAt": r[1], "code": r[2], "name": r[3] or "",
        "question": r[4] or "", "materials": json.loads(r[5] or "{}"),
        "answer": r[6], "evidence": json.loads(r[7] or "[]"),
        "searchHits": json.loads(r[8] or "[]"), "verdict": r[9],
        "positionId": r[10], "decisionId": r[11],
    }


def list_inquiry_logs(
    limit: int = 20, offset: int = 0, ts_code: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[dict]:
    """历史列表(`GET /inquiries?limit&offset&tsCode=`),按 `created_at` **倒序**
    (最近的问询在前,聊天历史惯例;`id DESC` 作同秒并列的确定性次序兜底)。`ts_code`
    传入先归一再等值匹配(同 `decision_log.list_decisions` 惯例——裸 6 位查询不归一
    会静默 0 命中)。"""
    init_schema(db_path)
    clauses: List[str] = []
    params: List[Any] = []
    if ts_code:
        from neckline.review.parse import normalize_ts_code
        clauses.append("ts_code=?")
        params.append(normalize_ts_code(ts_code))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_INQUIRY_LOG_COLS} FROM inquiry_log {where} "
            f"ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
    return [_row_to_inquiry_log(r) for r in rows]


def get_inquiry_log(inquiry_id: int, db_path: Optional[Path] = None) -> Optional[dict]:
    """单条详情(`GET /inquiries/{id}`)。不存在 → `None`(API 层据此 404)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_INQUIRY_LOG_COLS} FROM inquiry_log WHERE id=?", (inquiry_id,),
        ).fetchone()
    return _row_to_inquiry_log(row) if row else None


__all__ = [
    "upsert_device", "list_device_tokens", "load_inquiry_pool",
    "create_inquiry_log", "list_inquiry_logs", "get_inquiry_log",
]
