"""周复盘存档(plan 4D.2/4D.3)。`reviews` 表(week PK)已在 4A 建表(forward-compat),
本模块首次落地读写。`result_json` 落 `reconcile.weekly_review_dict()` 的同一份形状
(API 响应 = DB 存档 = 同一契约,不重复定义)。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline.db import connection, init_schema, readonly_tables
from neckline.review.reconcile import WeeklyReview, weekly_review_dict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_weekly_review(review: WeeklyReview, material: Optional[str] = None, db_path: Optional[Path] = None) -> None:
    """幂等覆盖(`INSERT OR REPLACE`)——同一周重新上传交割单会覆盖旧对账结果。"""
    init_schema(db_path)
    now = _now()
    payload = json.dumps(weekly_review_dict(review), ensure_ascii=False)
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO reviews (week, generated_at, result_json, material, updated_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(week) DO UPDATE SET generated_at=excluded.generated_at, "
            "result_json=excluded.result_json, material=excluded.material, "
            "updated_at=excluded.updated_at",
            (review.week, now, payload, material, now),
        )


def load_weekly_review(week: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """查一周的存档对账结果。查一个从未上传过交割单的周是正常场景。

    ⚠ **只读**(`readonly_tables`,R3-🔴-2):原先这里是「防御性 `init_schema`」
    —— 那正是 R3-🔴-2 点名的病(一次读把老库迁移掉)。表还没建 → `None`。"""
    with readonly_tables("reviews", db_path=db_path) as conn:
        if conn is None:
            return None
        row = conn.execute(
            "SELECT week, generated_at, result_json, material, updated_at FROM reviews WHERE week=?",
            (week,),
        ).fetchone()
    if row is None:
        return None
    return {
        "week": row[0],
        "generatedAt": row[1],
        "result": json.loads(row[2]),
        "material": row[3],
        "updatedAt": row[4],
    }


def list_review_weeks(db_path: Optional[Path] = None) -> List[str]:
    """全部已存档的周(降序,最近的周在前),供工作台"历史周列表"展示用。
    ⚠ **只读**(R3-🔴-2):表还没建 → 空列表。"""
    with readonly_tables("reviews", db_path=db_path) as conn:
        if conn is None:
            return []
        rows = conn.execute("SELECT week FROM reviews ORDER BY week DESC").fetchall()
    return [r[0] for r in rows]


__all__ = ["save_weekly_review", "load_weekly_review", "list_review_weeks"]
