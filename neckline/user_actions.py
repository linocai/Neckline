"""用户行为记录(plan §五 V2-①,§2.8-B 第 1 条「事实 / 用户行为 / 模型判断三类分存,
互不覆盖」的「用户行为」一类)。`user_actions` 是用户行为的**唯一落点**,读写单一通道
= 本模块。

**append-only 靠"没有那个函数"担保,不靠自觉**:本模块只提供 `record`(INSERT)与
`list_actions`(只读查询)两个公开函数,**没有 update / delete 函数**——调用方物理上
无法通过本模块改写或抹除既有行(同 `neckline/user_actions.py` 在 PROJECT_PLAN §五
V2-① 的原始设计意图)。真要修正一条历史行为记录(极少见场景),唯一姿势是再 `record`
一条新的予以说明,不是回头改旧的——事实表如实记录"当时发生了什么",不接受事后改写。

`kind` 不做枚举强校验(字符串自由):具体取值词表由各消费方(篮子日报 / 持仓台账 /
NL 提醒等)在各自模块定义,本模块只管落库通用骨架。已知会出现的取值(非穷举)——
`view` / `select` / `buy` / `sell` / `alert` / `label` / `voice_note`。

`occurred_at` 与 `created_at` 的区别:`occurred_at` 是事件**发生**的时刻(调用方可显式
传入以还原历史事件时间,如批量导入场景),`created_at` 是本行**落库**的时刻(服务端
生成,审计"系统何时知道这件事"——两者通常相同,但补录/回填场景会不同)。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline.db import connection, init_schema


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(
    kind: str,
    *,
    ts_code: Optional[str] = None,
    basket_id: Optional[int] = None,
    position_id: Optional[int] = None,
    payload: Optional[Dict[str, Any]] = None,
    occurred_at: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    """落一行用户行为记录,返回新行 id。`occurred_at` 缺省取当前 UTC ISO8601 时间。

    本函数是 `user_actions` 表**唯一**的写入入口(append-only 由此担保:本模块不存在
    第二个会碰这张表的函数)。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO user_actions "
            "(occurred_at, kind, ts_code, basket_id, position_id, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                occurred_at or now,
                kind,
                ts_code,
                basket_id,
                position_id,
                json.dumps(payload or {}, ensure_ascii=False),
                now,
            ),
        )
        return int(cur.lastrowid)


def list_actions(
    *,
    kind: Optional[str] = None,
    ts_code: Optional[str] = None,
    basket_id: Optional[int] = None,
    position_id: Optional[int] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """只读查询,按可选条件过滤,`occurred_at` 升序(再按 `id` 兜底,保证同一时刻多行时
    的确定性顺序)。不做任何写入——本函数与 `record` 是本模块公开的全部两个函数。"""
    init_schema(db_path)
    clauses: List[str] = []
    params: List[Any] = []
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)
    if ts_code is not None:
        clauses.append("ts_code = ?")
        params.append(ts_code)
    if basket_id is not None:
        clauses.append("basket_id = ?")
        params.append(basket_id)
    if position_id is not None:
        clauses.append("position_id = ?")
        params.append(position_id)
    if since is not None:
        clauses.append("occurred_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("occurred_at <= ?")
        params.append(until)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        "SELECT id, occurred_at, kind, ts_code, basket_id, position_id, payload_json, created_at "
        f"FROM user_actions {where} ORDER BY occurred_at, id"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": r[0],
            "occurred_at": r[1],
            "kind": r[2],
            "ts_code": r[3],
            "basket_id": r[4],
            "position_id": r[5],
            "payload": json.loads(r[6] or "{}"),
            "created_at": r[7],
        }
        for r in rows
    ]


__all__ = ["record", "list_actions"]
