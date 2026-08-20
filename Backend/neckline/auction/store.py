"""次日核对的两阶段读写(V2.5.0 S8)。`k9_checklists` + `k9_d1_verdicts` 两张表。

🔴 **「先到先定」是 SQL 的事,不是谁记得跳过**:
    · 9:26 那一拍写行时,只有判「放弃」才顺手落终值(`decided_stage='auction'`);
    · 10:00 那一拍一律 `UPDATE ... WHERE decided_stage IS NULL` ——
      **已在竞价定案的票结构上改不动**(裁定 10 / §5.7.2)。
    · 两拍都用 `INSERT OR IGNORE` 建行 → 重复跑不改判、不重复落。

🔴 **`Verdict.CONFIRMED` 只可能来自 `settle_verdicts()`**:
`record_auction_stage()` 收的是**二值** `ChecklistVerdict`,映射到终值的
`_AUCTION_FINAL` 是一张**两键全映射**表 —— 那条写路径上根本构造不出「成立」。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.auction.checklist import Checklist, ChecklistVerdict
from neckline.db import connection, init_schema, readonly_tables
from neckline.playbook.evaluate import SettleOutcome, Verdict

logger = logging.getLogger(__name__)

CHECKLIST_TABLE = "k9_checklists"
VERDICTS_TABLE = "k9_d1_verdicts"

STAGE_AUCTION = "auction"
STAGE_OPEN30 = "open30"
STAGES: Tuple[str, ...] = (STAGE_AUCTION, STAGE_OPEN30)

#: 🔴 二值 → 终值的**全映射**。`PENDING_OPEN` 映到 `None` = 「今天还没定案」,
#: ⛔ 不是「观察」——「观察」是 10:00 真看过之后的结论。
#: 表里没有「成立」这一项,因为**源枚举里就没有**(裁定 10)。
_AUCTION_FINAL: Mapping[ChecklistVerdict, Optional[Verdict]] = {
    ChecklistVerdict.REJECTED: Verdict.REJECTED,
    ChecklistVerdict.PENDING_OPEN: None,
}
assert set(_AUCTION_FINAL) == set(ChecklistVerdict)


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=False)


# ══════════════════════════════════════════════════════════════════════════
# 9:26 那一拍
# ══════════════════════════════════════════════════════════════════════════

def save_checklist(
    checklist: Checklist, *, strategy: str = "K9", db_path: Optional[Path] = None
) -> bool:
    """冻结整张核对表。**`INSERT OR IGNORE`** —— 已经有一张就不覆盖
    (当日只跑一次由 `dedup.py` 保证;这里是结构性的第二道)。

    返回 `True` = 本次真的写进去了。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        cur = conn.execute(
            f"INSERT OR IGNORE INTO {CHECKLIST_TABLE} "
            "(trade_date, strategy, d0_date, captured_at, data_quality, "
            " rejected_count, pending_count, checklist_json, notes_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (_d(checklist.trade_date), strategy, _d(checklist.d0_date),
             checklist.captured_at.isoformat(timespec="seconds"), checklist.data_quality,
             len(checklist.rejected), len(checklist.pending_open),
             _j(checklist.to_dict()), _j(list(checklist.notes)), _now()),
        )
        return cur.rowcount > 0


def record_auction_stage(
    checklist: Checklist, *, strategy: str = "K9", db_path: Optional[Path] = None
) -> Dict[str, int]:
    """把 9:26 那一拍的逐票结果落进 `k9_d1_verdicts`。

    ⚠ 判「放弃」的那几只**顺手落终值**(`decided_stage='auction'`);
    判「待开盘后观察」的只落读数,`verdict` / `decided_stage` **留 NULL**
    —— 「今天还没定案」与「10:00 看过之后判观察」是两件事。"""
    init_schema(db_path)
    now = _now()
    rows: List[tuple] = []
    for r in checklist.rows:
        final = _AUCTION_FINAL[r.verdict]
        rows.append((
            _d(checklist.trade_date), r.ts_code, strategy, _d(checklist.d0_date),
            r.pattern, r.playbook_version,
            r.verdict.value, _j(dict(r.readings)), _j(r.branch.to_dict()), now,
            None if final is None else final.value,
            None if final is None else STAGE_AUCTION,
            now,
        ))
    with connection(db_path) as conn:
        conn.executemany(
            f"INSERT OR IGNORE INTO {VERDICTS_TABLE} "
            "(trade_date, ts_code, strategy, d0_date, pattern, playbook_version, "
            " auction_verdict, auction_readings_json, auction_branch_json, auction_at, "
            " verdict, decided_stage, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    return {
        "rows": len(rows),
        "rejected": len(checklist.rejected),
        "pending": len(checklist.pending_open),
    }


# ══════════════════════════════════════════════════════════════════════════
# 10:00 那一拍(**三分支终值的唯一权威**,裁定 10)
# ══════════════════════════════════════════════════════════════════════════

def ensure_rows(
    trade_date: date, *, d0_date: date, rows: Sequence[Mapping[str, Any]],
    strategy: str = "K9", db_path: Optional[Path] = None,
) -> int:
    """建行(`INSERT OR IGNORE`)。**9:26 那一拍没跑成的日子**也要能结算 ——
    那时表里一行都没有,由本函数补出骨架行(竞价那半留 NULL,如实标「那一拍没跑」)。"""
    init_schema(db_path)
    now = _now()
    payload = [
        (_d(trade_date), r["ts_code"], strategy, _d(d0_date), r["pattern"],
         int(r["playbook_version"]), now)
        for r in rows
    ]
    with connection(db_path) as conn:
        cur = conn.executemany(
            f"INSERT OR IGNORE INTO {VERDICTS_TABLE} "
            "(trade_date, ts_code, strategy, d0_date, pattern, playbook_version, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            payload,
        )
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def settle_verdicts(
    trade_date: date, outcomes: Sequence[SettleOutcome], *,
    readings_by_code: Mapping[str, Mapping[str, Optional[float]]],
    strategy: str = "K9", db_path: Optional[Path] = None,
) -> Dict[str, int]:
    """写三分支终值。🔴 **幂等 `WHERE decided_stage IS NULL`** ——
    已在竞价定案的票**改不动**(裁定 10),重复跑也不改判。

    返回 `{settled, unchanged, confirmed, rejected, observed}`:
    `unchanged` = 那几只早上 9:29 就已经定案了。

    🔴 **三分支计数只数「真的被 UPDATE 到」的那些**(R2-10)。从前
    `settled` 取真实 rowcount、而 `confirmed/rejected/observed` 由调用方直接从
    `outcomes` 里数 —— 正常路径两者一致,但 `undecided_codes` 与本函数之间若发生
    并发写,日志与 `SettleRunResult.counts` 会报出一个**没有落库的分布**。
    账上的数与库里的行对不上,是本仓最不该出现的那一类。
    """
    init_schema(db_path)
    now = _now()
    settled = 0
    landed: Dict[str, int] = {v.value: 0 for v in Verdict}
    with connection(db_path) as conn:
        for out in outcomes:
            cur = conn.execute(
                f"UPDATE {VERDICTS_TABLE} SET verdict=?, decided_stage=?, "
                " open30_readings_json=?, open30_branches_json=?, settled_at=? "
                "WHERE trade_date=? AND ts_code=? AND strategy=? AND decided_stage IS NULL",
                (out.verdict.value, STAGE_OPEN30,
                 _j(dict(readings_by_code.get(out.ts_code, {}))),
                 _j([out.rejected.to_dict(), out.confirmed.to_dict()]),
                 now, _d(trade_date), out.ts_code, strategy),
            )
            hit = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            settled += hit
            if hit:
                landed[out.verdict.value] += 1
    return {"settled": settled, "unchanged": len(outcomes) - settled, **landed}


# ══════════════════════════════════════════════════════════════════════════
# 读
# ══════════════════════════════════════════════════════════════════════════

def load_checklist(
    trade_date: date, *, strategy: str = "K9", db_path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """某日的核对表。`None` = **那天没跑过那一拍**(⛔ 不是「跑了、表是空的」)。

    ⚠ **只读**(`readonly_tables`,R3-🔴-2):表还没建的老库读出来就是 `None`
    —— ⛔ 读一次不许把库迁移掉(README /§9.2 /§9.4)。"""
    with readonly_tables(CHECKLIST_TABLE, db_path=db_path) as conn:
        if conn is None:
            return None
        r = conn.execute(
            f"SELECT checklist_json, created_at FROM {CHECKLIST_TABLE} "
            "WHERE trade_date=? AND strategy=?",
            (_d(trade_date), strategy),
        ).fetchone()
    if r is None:
        return None
    out = json.loads(r[0])
    out["createdAt"] = r[1]
    return out


def load_verdicts(
    trade_date: date, *, strategy: str = "K9", db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """某日逐票的三分支终值(按 `ts_code` 升序)。

    ⚠ **只读**(R3-🔴-2):表还没建 → 空列表(那天本来就没有终值)。"""
    with readonly_tables(VERDICTS_TABLE, db_path=db_path) as conn:
        if conn is None:
            return []
        rows = conn.execute(
            f"SELECT ts_code, d0_date, pattern, playbook_version, auction_verdict, "
            f"auction_readings_json, auction_branch_json, auction_at, verdict, "
            f"decided_stage, open30_readings_json, open30_branches_json, settled_at "
            f"FROM {VERDICTS_TABLE} WHERE trade_date=? AND strategy=? ORDER BY ts_code",
            (_d(trade_date), strategy),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "ts_code": r[0], "d0_date": r[1], "pattern": r[2],
            "playbook_version": int(r[3]),
            "auction_verdict": r[4],
            "auction_readings": json.loads(r[5]) if r[5] else None,
            "auction_branch": json.loads(r[6]) if r[6] else None,
            "auction_at": r[7],
            "verdict": r[8], "decided_stage": r[9],
            "open30_readings": json.loads(r[10]) if r[10] else None,
            "open30_branches": json.loads(r[11]) if r[11] else None,
            "settled_at": r[12],
        })
    return out


def undecided_codes(
    trade_date: date, *, strategy: str = "K9", db_path: Optional[Path] = None
) -> List[str]:
    """还没定案的票(`decided_stage IS NULL`)—— 10:00 那一拍要结算的就是它们。

    ⚠ **只读**(R3-🔴-2):表还没建 → 空列表。名字不带 `load_` 前缀,但它确实是
    读函数 —— ⛔ 别因为守门的前缀清单扫不到它就留一个后门。"""
    with readonly_tables(VERDICTS_TABLE, db_path=db_path) as conn:
        if conn is None:
            return []
        return [
            r[0] for r in conn.execute(
                f"SELECT ts_code FROM {VERDICTS_TABLE} "
                "WHERE trade_date=? AND strategy=? AND decided_stage IS NULL ORDER BY ts_code",
                (_d(trade_date), strategy),
            ).fetchall()
        ]


__all__ = [
    "CHECKLIST_TABLE", "VERDICTS_TABLE", "STAGE_AUCTION", "STAGE_OPEN30", "STAGES",
    "save_checklist", "record_auction_stage", "ensure_rows", "settle_verdicts",
    "load_checklist", "load_verdicts", "undecided_codes",
]
