"""⑥ 溢出篮**跨进程**留痕(plan §五 V2-⑯-D 补记,2026-08-04 定向小修)。

**背景**:⑥-b-C 裁定「容量溢出篮不落库」——不臆造 tier、不落 `baskets`、不落
`tier_history`,留痕在内存态 `TierResult.dropped`,由报告层(`reports.
basket_daily_json`)如实披露。裁定当时晚间链是**单进程**,"内存态"天然活得过
"⑥ 算完 → 报告读到"这段距离。⑯-D 把 ⑤⑥⑦(`neckline-basket.service`)与
⑨+报告(`neckline-report.service`)拆进**两个 systemd oneshot、两个独立进程**后,
内存态活不过进程边界——报告 ③b 因此**恒为** `available=false`,且既有披露文案
"本次未运行 Tier 分层引擎"在这种场景下是**误导**(今晚其实跑了,只是在另一个
进程里)。三条出路见 `deploy/neckline-report.service` 头部登记,本文件是**出路③**
的落地:让 `dropped` 落一张轻量表,供报告段跨进程读回。

**为什么不塞进 `basket_store.py`**:那个模块头写明是「篮子四表的唯一写入口」,
四张表共享同一套"冻结 / 追加"语义与事务边界(`baskets`/`basket_cards` 冻结、
`basket_verification`/`tier_history` 各自的幂等纪律)。本表**语义完全不同**——
它是可变的、按 `trade_date` **整行覆写**的搬运工,不是篮子的永久档案,也没有
`basket_id`(溢出篮从未拿到身份)。混进"四表"的框架里会把两种性质的表讲成一种,
故单独一个文件、单独一张表(`basket_dropped_handoff`,DDL 见 `neckline/db.py`
—— 追加在 `industry_stage_daily` 之后,不回头改 ⑥/⑦ 的 DDL 段)。

**这不是给溢出篮一个身份**:⑥-b-C 的裁定原样有效,本表不提供"按篮子查历史"
的入口,也不出现在报告契约的 `basketId` 字段里——它只回答一件事:"最近一次
⑤⑥ 跑出来的 dropped 清单长什么样",不是"这个篮子历史上曾被判定过溢出"。

**三态**(与 `EveningChainResult.dropped_baskets` 的 `None`/`[]`/`[...]` 同一套
纪律,见 `report/evening.py` 模块头):
- 该 `trade_date` **无行** = ⑤⑥ 本次(迄今)没跑过 —— `load_dropped_handoff` 返 `None`。
- 有行、`dropped_json='[]'` = 跑了,今天零溢出。
- 有行、非空数组 = 跑了,有溢出。

**唯一调用方**是 `neckline/report/evening.py::run_evening_chain`:写侧在
`_run_basket_segment`(⑥ 定档成功之后立刻落,与 `save_tier_decision` 提交
"baskets/tier_history 已是既成事实"同一时刻——不依赖 ⑦ 卡生成是否成功,同
"有篮子无卡是合法中间态"的既定姿势);读侧只在 SEG_REPORT 且**本次调用压根
没打算跑 SEG_BASKET** 时才查(单进程整链跑法 `SEG_BASKET in wanted` 恒真,
查表分支不触发,行为逐字节不变)。**不追加历史**:同一晚重跑 ⑤⑥ 会覆写这一行,
只保留"最近一次"的结论。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from neckline.db import connection, init_schema, readonly_connection
from neckline.selection.tier import DroppedBasket

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _day(trade_date: date | str) -> str:
    return trade_date if isinstance(trade_date, str) else trade_date.strftime("%Y%m%d")


def save_dropped_handoff(
    trade_date: date,
    dropped: Sequence[DroppedBasket],
    *,
    selection_run_id: Optional[str] = None,
    db_path: Optional[Path] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """落一行(`INSERT OR REPLACE`,`trade_date` 主键)。**`dropped` 允许空序列**
    (= 跑了零溢出,与"没跑"必须能分开——靠"有没有这一行"区分,不是数组是否为空)。

    V2.2-③ 起 `DroppedBasket` 多了 `name`/`gate`/`gate_detail`(③b 升级为
    「名 / 分 / 卡在哪一关、差多少 / 原因码」),照存;老行没有这些键 → 读回取
    默认(`name` 空时报告层兜底拿 `basket_key` 顶上,既有行为不变)。
    """
    payload = [
        {"basket_key": d.basket_key, "reason": d.reason, "mech_score": d.mech_score,
         "name": getattr(d, "name", "") or "",
         "gate": getattr(d, "gate", None),
         "gate_detail": getattr(d, "gate_detail", None)}
        for d in dropped
    ]
    def _save(target: sqlite3.Connection) -> None:
        target.execute(
            "INSERT OR REPLACE INTO basket_dropped_handoff "
            "(trade_date, selection_run_id, dropped_json, created_at) VALUES (?,?,?,?)",
            (_day(trade_date), str(selection_run_id or ""),
             json.dumps(payload, ensure_ascii=False, sort_keys=True), _now()),
        )
    if conn is not None:
        _save(conn)
        return
    init_schema(db_path)
    with connection(db_path) as own:
        _save(own)


def load_dropped_handoff(
    trade_date: date, *, db_path: Optional[Path] = None,
) -> Optional[List[DroppedBasket]]:
    """读回上面存的那一行。**无行 → `None`**(⑤⑥ 本次〔迄今〕没跑过,⛔ 不许猜成
    零溢出);解析异常 → 同样按 `None` 处理并 WARNING(读侧永远不比"没有这张表"更糟,
    绝不让一行解不出的脏数据把报告段拖垮)。"""
    try:
        with readonly_connection(db_path) as conn:
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(basket_dropped_handoff)")}
            if not columns:
                return None
            sql = "SELECT dropped_json FROM basket_dropped_handoff WHERE trade_date=?"
            args = [_day(trade_date)]
            if "selection_run_id" in columns:
                from neckline.selection.run_store import latest_published_run_id
                run_id = latest_published_run_id(_day(trade_date), db_path=db_path)
                sql += " AND COALESCE(selection_run_id,'')=?"
                args.append(run_id or "")
            row = conn.execute(sql, tuple(args)).fetchone()
    except FileNotFoundError:
        return None
    if row is None:
        return None
    try:
        items = json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        logger.warning("[basket_dropped_handoff] trade_date=%s 的 dropped_json 解不出,"
                       "按未取得处理", _day(trade_date))
        return None
    if not isinstance(items, list):
        logger.warning("[basket_dropped_handoff] trade_date=%s 的 dropped_json 不是数组,"
                       "按未取得处理", _day(trade_date))
        return None
    out: List[DroppedBasket] = []
    for it in items:
        if not isinstance(it, dict):
            logger.warning("[basket_dropped_handoff] trade_date=%s 一条记录不是对象,已跳过:%r",
                           _day(trade_date), it)
            continue
        try:
            out.append(DroppedBasket(
                basket_key=str(it.get("basket_key") or ""),
                reason=str(it.get("reason") or ""),
                mech_score=float(it["mech_score"]),
                name=str(it.get("name") or ""),
                gate=(str(it["gate"]) if it.get("gate") else None),
                gate_detail=(str(it["gate_detail"]) if it.get("gate_detail") else None),
            ))
        except (TypeError, ValueError, KeyError):
            logger.warning("[basket_dropped_handoff] trade_date=%s 一条记录解析失败,已跳过:%r",
                           _day(trade_date), it)
    return out


__all__ = ["save_dropped_handoff", "load_dropped_handoff"]
