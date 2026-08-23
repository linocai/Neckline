"""解释层的落库(V2.5.0 S9)。`k9_explain_notes` + `k9_explain_audit` 两张表。

🔴 **每次剔除与补位都写进运行审计**(§5.5 逐字):`k9_explain_audit` 是
**append-only** 的一条链 ——「谁被剔、为什么、谁补上、第几轮」事后一查就有,
⛔ 不靠日志(日志会滚掉,而这条链要活到复盘那天)。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from neckline.db import connection, init_schema, readonly_tables
from neckline.explain.aggregate import ExplainNote
from neckline.explain.news_exclusion import NewsState, NewsVerdict

logger = logging.getLogger(__name__)

NOTES_TABLE = "k9_explain_notes"
AUDIT_TABLE = "k9_explain_audit"

#: 审计动作(闭合两值)。补位不再有轮数上限；reserve 用尽由最终清单容量如实表达。
ACTION_EXCLUDED = "excluded"
ACTION_BACKFILLED = "backfilled"
ACTIONS = (ACTION_EXCLUDED, ACTION_BACKFILLED)


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_notes(
    trade_date: date,
    notes: Sequence[ExplainNote],
    *,
    news_by_code: Optional[Mapping[str, NewsVerdict]] = None,
    db_path: Optional[Path] = None,
) -> int:
    """落资料聚合 + 消息面结论。同 `(trade_date, ts_code)` **幂等重写**
    (同一天重跑解释层是合法的:它没有窗口纪律,不像早晨那两拍)。"""
    init_schema(db_path)
    news = dict(news_by_code or {})
    now = _now()
    rows = []
    for n in notes:
        v = news.get(n.ts_code)
        state = (v.state if v is not None else NewsState.UNVERIFIED).value
        rows.append((
            _d(trade_date), n.ts_code,
            json.dumps(dict(n.profile), ensure_ascii=False),
            n.kline_comment,
            state,
            None if (v is None or v.category is None) else v.category.value,
            json.dumps(v.to_dict() if v is not None else
                       {"state": state, "reason": "解释层未查"}, ensure_ascii=False),
            int(n.llm_ok), n.filled_by, now,
        ))
    with connection(db_path) as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO {NOTES_TABLE} "
            "(trade_date, ts_code, profile_json, kline_comment, news_state, "
            " news_category, news_json, llm_ok, filled_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


def append_audit(
    trade_date: date, entries: Sequence[Mapping[str, Any]], *,
    db_path: Optional[Path] = None,
) -> int:
    """追加审计链(append-only)。`entries` 逐条 `{round_no, action, ts_code, reason}`。

    ⚠ `seq` 由本函数按**当前已有条数**续号 —— 一天之内重跑解释层会接着往下记,
    ⛔ 不重排、不覆盖(那条链的意义就是「按发生次序读得回来」)。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        r = conn.execute(
            f"SELECT COALESCE(MAX(seq), 0) FROM {AUDIT_TABLE} WHERE trade_date=?",
            (_d(trade_date),),
        ).fetchone()
        seq = int(r[0]) if r else 0
        rows = []
        for e in entries:
            action = str(e["action"])
            if action not in ACTIONS:
                raise ValueError(f"审计动作只能是 {list(ACTIONS)},收到 {action!r}")
            seq += 1
            rows.append((_d(trade_date), seq, int(e.get("round_no", 0)), action,
                         str(e.get("ts_code", "")), str(e.get("reason", "")), now))
        conn.executemany(
            f"INSERT INTO {AUDIT_TABLE} "
            "(trade_date, seq, round_no, action, ts_code, reason, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


def load_notes(
    trade_date: date, *, codes: Optional[Sequence[str]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Dict[str, Any]]:
    """某日的资料聚合(按 `ts_code`)。空 = 那天解释层没跑过。

    ⚠ **只读**(`readonly_tables`,R3-🔴-2):表还没建的老库读出来就是空
    —— ⛔ 读一次不许把库迁移掉(README /§9.2 /§9.4)。"""
    with readonly_tables(NOTES_TABLE, db_path=db_path) as conn:
        if conn is None:
            return {}
        rows = conn.execute(
            f"SELECT ts_code, profile_json, kline_comment, news_state, news_category, "
            f"news_json, llm_ok, filled_by, created_at FROM {NOTES_TABLE} "
            "WHERE trade_date=? ORDER BY ts_code",
            (_d(trade_date),),
        ).fetchall()
    want = set(codes) if codes is not None else None
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if want is not None and r[0] not in want:
            continue
        out[r[0]] = {
            "ts_code": r[0], "profile": json.loads(r[1]), "kline_comment": r[2],
            "news_state": r[3], "news_category": r[4],
            "news": json.loads(r[5]) if r[5] else {},
            "llm_ok": bool(r[6]), "filled_by": r[7], "created_at": r[8],
        }
    return out


def load_audit(
    trade_date: date, *, db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """某日的剔除 / 补位审计链(按 `seq` 升序)。

    ⚠ **只读**(R3-🔴-2):表还没建 → 空列表。"""
    with readonly_tables(AUDIT_TABLE, db_path=db_path) as conn:
        if conn is None:
            return []
        rows = conn.execute(
            f"SELECT seq, round_no, action, ts_code, reason, created_at FROM {AUDIT_TABLE} "
            "WHERE trade_date=? ORDER BY seq",
            (_d(trade_date),),
        ).fetchall()
    return [
        {"seq": int(r[0]), "round_no": int(r[1]), "action": r[2], "ts_code": r[3],
         "reason": r[4], "created_at": r[5]}
        for r in rows
    ]


__all__ = [
    "NOTES_TABLE", "AUDIT_TABLE",
    "ACTION_EXCLUDED", "ACTION_BACKFILLED", "ACTIONS",
    "save_notes", "append_audit", "load_notes", "load_audit",
]
