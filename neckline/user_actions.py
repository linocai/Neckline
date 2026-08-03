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

**两列刻意不同时区(契约线审计 🟡 Y2,2026-08-03 定案;别"统一")**::

    occurred_at  北京时间(`calendar.CN_TZ`)—— 它是**市场 / 用户事件时刻**,与交割单
                 成交时刻、盘中哨兵那条时间轴同一口径(CLAUDE.md v1.4-⑥)。
    created_at   UTC —— 它是**审计戳**,与 `strategy_versions.activated_at`、各 store
                 的 `created_at` 同一口径(全仓惯例,唯一写入者各自 `_now()`)。

修的是什么:DDL 注释白纸黑字写「ISO8601 北京时间」,`record()` 却默认落 UTC `+00:00`。
当前全部写入方都走缺省、暂时同质,**但 `occurred_at` 的过滤与排序是字符串比较**
(`list_actions` 的 `since`/`until` + `ORDER BY occurred_at`)—— 一旦 ⑮ 客户端按北京
时间上报 `view` 事件,同一时刻的 `…T01:00:00+00:00` 与 `…T09:00:00+08:00` 在字符串序上
完全不等价,时间轴就错乱了,而且**不会报错**。⚠ 该表 V2 新建、上线前 0 行,故直接改
写侧、不做数据迁移;真有历史行时得先归一再改。

**归一在写侧收口**:显式传入的 `occurred_at` 一律经 `normalize_occurred_at()` 转成
北京时间再落库,`list_actions` 的 `since`/`until` 走同一个函数 —— 「同一列里只有一种
时区」这件事不能靠调用方自觉,那正是 Y2 的病根。naive(无时区)输入按**北京时间**读
(市场时刻口径,与 `review/reconcile.trade_instant` 的约定一致);格式非法 **fail loud**
(「没给」`None` 与「给错了」必须分得开,§3.8)。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from neckline.calendar import CN_TZ
from neckline.db import connection, init_schema


def _now() -> str:
    """审计戳(`created_at`)—— **UTC**,同全仓 `_now()` 惯例。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cn_now() -> str:
    """事件时刻(`occurred_at`)缺省值 —— **北京时间**,唯一源 `calendar.CN_TZ`
    (⛔ 不在本模块另写 `timezone(timedelta(hours=8))`,CLAUDE.md v1.4-⑥ 明令)。"""
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def normalize_occurred_at(value: Union[str, datetime]) -> str:
    """把调用方给的事件时刻归一为**北京时间** ISO8601 串(秒精度)。

    · 带时区 → 换算到 `CN_TZ`(同一时刻,换个写法,信息零损失);
    · naive(无时区)→ 按**北京时间**读(市场时刻口径,不是 UTC);
    · 解析不了 → `ValueError` **fail loud**,⛔ 不静默原样落库(原样落 = 这一列里混进
      第二种时区,字符串排序与窗口过滤当场失真,且一声不吭)。
    """
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("normalize_occurred_at:空串不是时刻(要么别传,要么给个真时刻)")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(
                f"normalize_occurred_at:{value!r} 不是 ISO8601 时刻,拒绝落库 —— "
                f"`user_actions.occurred_at` 只接受可解析、可归一到北京时间的值"
            ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CN_TZ)
    return dt.astimezone(CN_TZ).isoformat(timespec="seconds")


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
    """落一行用户行为记录,返回新行 id。

    `occurred_at`:缺省取**当前北京时间**(与 DDL 注释一致,🟡 Y2);显式传入的值一律
    经 `normalize_occurred_at()` 归一到北京时间后落库,解析不了就 `ValueError`。
    `created_at` 仍是 **UTC** 审计戳(两列不同轴是刻意的,见模块头)。

    本函数是 `user_actions` 表**唯一**的写入入口(append-only 由此担保:本模块不存在
    第二个会碰这张表的函数)。"""
    init_schema(db_path)
    now = _now()
    happened = _cn_now() if occurred_at is None else normalize_occurred_at(occurred_at)
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO user_actions "
            "(occurred_at, kind, ts_code, basket_id, position_id, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                happened,
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
    的确定性顺序)。不做任何写入——本函数与 `record` 是本模块公开的全部三个公开名
    (`normalize_occurred_at` 是给上层做入参校验用的纯函数,不碰库)。

    `since`/`until` 走**同一个归一函数**(🟡 Y2):窗口过滤是**字符串比较**,调用方递进来
    一个 UTC 串就会静默筛错时段。给非时刻串 → `ValueError`(不猜)。"""
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
        params.append(normalize_occurred_at(since))
    if until is not None:
        clauses.append("occurred_at <= ?")
        params.append(normalize_occurred_at(until))
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


# ⚠ `__all__` 仍**只有两个会碰库的函数**(append-only 靠"没有那个函数"担保,守门单测
# `test_user_actions_module_exposes_only_insert_and_read` 断言的就是这一点);
# `normalize_occurred_at` 是纯函数、不碰库,故意**不进** `__all__` —— 它是给上层做入参
# 校验的工具,不是这张表的第三个写入面。
__all__ = ["record", "list_actions"]
