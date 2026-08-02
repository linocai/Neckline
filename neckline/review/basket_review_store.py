"""`basket_review_daily` 读写(plan §五 V2-⑨,表 DDL 在 ① 建好)。**每日一行幂等**:
本模块**只有 INSERT OR IGNORE**,没有 UPDATE / DELETE 的路径。

**为什么连"补写 LLM 解释"的 UPDATE 都不给**(定死,别以后"顺手"加一个):

    · `UNIQUE(basket_id, review_date)` 表达的是「一篮一天一份复盘」。允许回头改
      `llm_text`,等于允许「第一次跑降级了、第二次跑补上」—— 那么库里那一行到底是
      哪次的结论、`degraded=1` 还算不算数,就再也说不清了。而 ⑨-C 的归因**恰恰要
      按 `degraded` 分层**统计「有解释 vs 没解释的复盘覆盖率」。
    · 正确的做法在**写之前**:`basket_review.review_day()` 刻意「先算完九项 → 再跑
      LLM → 最后一次性落库」,让 LLM 段的结论(成功 / 缺席 / 被降级丢掉)在**同一
      行**里一次写清。真要重做,删的是那一天那一行,由人显式来做,不由代码悄悄覆盖。

同日重跑发现库里那份与本次算出的不一致 → **不覆盖**,把差异逐条 WARNING + 原样
返回给调用方(`conflicts`),由报告层如实披露(同 `basket_store` 的既有体例)。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from neckline.db import connection, init_schema

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注(运行期不 import,防模块级环)
    from neckline.review.basket_review import BasketReview

logger = logging.getLogger(__name__)

_COLUMNS = (
    "basket_id, review_date, depth, mech_json, llm_text, llm_skip_reason, degraded, created_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(d: Any) -> str:
    return d if isinstance(d, str) else d.strftime("%Y%m%d")


@dataclass(frozen=True)
class ReviewRow:
    id: int
    basket_id: int
    review_date: str
    depth: str
    mech: Dict[str, Any]
    llm_text: Optional[str]
    llm_skip_reason: Optional[str]
    degraded: int
    created_at: str

    @property
    def pack_version(self) -> Optional[str]:
        return (self.mech.get("meta") or {}).get("pack_version")

    @property
    def ruleset_version(self) -> Optional[str]:
        return (self.mech.get("meta") or {}).get("verification_ruleset_version")


def _row(r: Sequence[Any]) -> ReviewRow:
    try:
        mech = json.loads(r[4]) if r[4] else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("[basket_review_store] id=%s 的 mech_json 解不出,按空 dict 读回", r[0])
        mech = {}
    return ReviewRow(id=int(r[0]), basket_id=int(r[1]), review_date=str(r[2]), depth=str(r[3]),
                     mech=mech, llm_text=r[5], llm_skip_reason=r[6], degraded=int(r[7] or 0),
                     created_at=str(r[8]))


def _save_on_conn(conn: sqlite3.Connection, review: "BasketReview", now: str) -> Dict[str, Any]:
    text = json.dumps(review.mech, ensure_ascii=False, sort_keys=True)
    day = _d(review.review_date)
    cur = conn.execute(
        f"INSERT OR IGNORE INTO basket_review_daily ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?)",
        (int(review.basket_id), day, review.depth, text, review.llm_text,
         review.llm_skip_reason, int(bool(review.degraded)), now),
    )
    if cur.rowcount:
        return {"inserted": 1, "existing": 0, "conflicts": []}
    logger.warning("[basket_review_store] basket_review_daily 已存在 (basket_id=%s, %s) 的行,"
                   "幂等跳过、不覆盖。", review.basket_id, day)
    frozen = conn.execute(
        "SELECT mech_json, depth FROM basket_review_daily WHERE basket_id=? AND review_date=?",
        (int(review.basket_id), day),
    ).fetchone()
    conflicts: List[str] = []
    if frozen is not None and frozen[0] != text:
        conflicts.append(
            f"basket_review_daily[basket_id={review.basket_id}/{day}] 库里那份机械判与本次算出的"
            f"不一致,本次结果未采纳(库里 {len(frozen[0])} 字节 vs 本次 {len(text)} 字节)"
        )
    if frozen is not None and frozen[1] != review.depth:
        conflicts.append(
            f"basket_review_daily[basket_id={review.basket_id}/{day}] 库里 depth={frozen[1]} "
            f"≠ 本次 {review.depth}"
        )
    for c in conflicts:
        logger.warning("[basket_review_store] %s", c)
    return {"inserted": 0, "existing": 1, "conflicts": conflicts}


def save_review(review: "BasketReview", *, db_path: Optional[Path] = None,
                conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """落一份复盘(**每日一行幂等**)。`conn` 给了就复用调用方连接、不自开事务。"""
    now = _now()
    if conn is not None:
        return _save_on_conn(conn, review, now)
    init_schema(db_path)
    with connection(db_path) as own:
        return _save_on_conn(own, review, now)


def save_reviews(reviews: Sequence["BasketReview"], *,
                 db_path: Optional[Path] = None) -> Dict[str, Any]:
    """一批复盘同连接落地。与 `save_basket_cards` 同理:**不追求整批原子性** ——
    复盘与复盘之间没有引用关系,「今天有几篮复盘了、几篮没有」本就是合法状态。"""
    stats: Dict[str, Any] = {"inserted": 0, "existing": 0, "conflicts": []}
    if not reviews:
        return stats
    now = _now()
    init_schema(db_path)
    with connection(db_path) as conn:
        for review in reviews:
            one = _save_on_conn(conn, review, now)
            stats["inserted"] += one["inserted"]
            stats["existing"] += one["existing"]
            stats["conflicts"].extend(one["conflicts"])
    return stats


def load_review(basket_id: int, review_date: Any, *,
                db_path: Optional[Path] = None) -> Optional[ReviewRow]:
    init_schema(db_path)
    with connection(db_path) as conn:
        r = conn.execute(
            f"SELECT id, {_COLUMNS.replace(', created_at', '')}, created_at "
            "FROM basket_review_daily WHERE basket_id=? AND review_date=?",
            (int(basket_id), _d(review_date)),
        ).fetchone()
    return None if r is None else _row(r)


def list_reviews(
    *,
    date_from: Any = None,
    date_to: Any = None,
    basket_ids: Optional[Sequence[int]] = None,
    db_path: Optional[Path] = None,
) -> List[ReviewRow]:
    """区间内的复盘行(按 `review_date`、`basket_id` 升序,**确定性排序**)。

    ⚠ `date_to` 存在的意义是**防前视**:历史回放要问「截至那天我们知道什么」,就得
    能把之后写入的行截掉(同 `exec_hint._latest_decision` / `list_decisions(date_to=)`
    的既有体例)。⑨-C 做周度归因时是**回看**,可以不传;报告期内查表**必须传**。
    """
    sql = (f"SELECT id, {_COLUMNS.replace(', created_at', '')}, created_at "
           "FROM basket_review_daily WHERE 1=1")
    args: List[Any] = []
    if date_from is not None:
        sql += " AND review_date >= ?"
        args.append(_d(date_from))
    if date_to is not None:
        sql += " AND review_date <= ?"
        args.append(_d(date_to))
    if basket_ids:
        sql += " AND basket_id IN (" + ",".join("?" * len(basket_ids)) + ")"
        args.extend(int(b) for b in basket_ids)
    sql += " ORDER BY review_date, basket_id"
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return [_row(r) for r in rows]


def review_dates(*, date_from: Any = None, date_to: Any = None,
                 db_path: Optional[Path] = None) -> List[str]:
    """有复盘记录的交易日(升序)。⑨-C 用它数「样本有几个交易日」——
    **样本量不足时报告只给这个数、不给结论**(⑨-C2 诚实边界)。"""
    sql = "SELECT DISTINCT review_date FROM basket_review_daily WHERE 1=1"
    args: List[Any] = []
    if date_from is not None:
        sql += " AND review_date >= ?"
        args.append(_d(date_from))
    if date_to is not None:
        sql += " AND review_date <= ?"
        args.append(_d(date_to))
    sql += " ORDER BY review_date"
    init_schema(db_path)
    with connection(db_path) as conn:
        return [str(r[0]) for r in conn.execute(sql, tuple(args)).fetchall()]


__all__ = [
    "ReviewRow", "save_review", "save_reviews",
    "load_review", "list_reviews", "review_dates",
]
