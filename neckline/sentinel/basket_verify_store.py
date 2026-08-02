"""`basket_verification` 读写(plan §五 V2-⑧,表 DDL 在 ① 建好)。**append-only**:
本模块**只有 INSERT**,没有 UPDATE / DELETE 的路径 —— 「曾经 partial 后来 verified」
本身就是审计对象,抹掉就没法回看(靠"没有那个路径"担保,不靠自觉;守门单测在
`tests/test_v2_schema_guard.py` 与 `tests/test_sentinel_basket_verify.py`)。

**落行规则(⑧-C2 第 4 / 6 条,防一天几百行流水,也防"改口")**

    · 盘中:**状态未变不落行**,变了才追加一行。
    · EOD:**无论变没变必落一行**(`source='eod'`)—— 它是当日的定论记录。
    · `falsified` 是**当日终态**:一旦落过,当日后续盘中拍**不再改写、也不再落新行**;
      EOD 那一行照落,**内容仍是 falsified**(承 v1.3「D5 判向定格一次」,堵死
      「跌破了 → 又回来了 → 系统改口说没证伪」)。⚠ 反向不成立:`verified` **不是**
      终态,尾盘走坏照样能翻 `partial`/`falsified`。

**「当前状态」三路读法(⑧/⑭/⑮ 共用,在此定死)**:有 `eod` 行取 `eod`;没有则取最近
一条 `intraday` 并标「盘中暂态、未收盘定论」;两条都没有 → `unclear` 且
`not_evaluated=True` —— **「还没判」与「判了是 unclear」必须分得开**。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from neckline.calendar import CN_TZ
from neckline.db import connection, init_schema
from neckline.selection import verification_rules as vr

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注(运行期不 import,防模块级环)
    from neckline.sentinel.basket_verify import BasketVerdict

logger = logging.getLogger(__name__)

SOURCE_INTRADAY = "intraday"
SOURCE_EOD = "eod"

# `append_if_changed` / `append_row` 的返回码(三态,别用 bool —— 「没写因为没变」与
# 「没写因为当日已证伪定格」是两件事,⑨ 统计与冒烟都要分得开)。
WROTE = "wrote"
SKIPPED_UNCHANGED = "skipped_unchanged"
SKIPPED_LATCHED = "skipped_latched"
WROTE_LATCHED = "wrote_latched"          # EOD 那一行照落,内容是已定格的 falsified

_COLUMNS = "basket_id, trade_date, observed_at, state, source, evidence_json, created_at"


def _d(trade_date: date) -> str:
    return trade_date.strftime("%Y%m%d")


def observed_at_now(now: Optional[datetime] = None) -> str:
    """`basket_verification.observed_at` 的取值(DDL 注明 **ISO8601 北京时间**)。
    时区唯一源 `neckline.calendar.CN_TZ`(⛔ 别在这里另写 `timedelta(hours=8)`)。"""
    dt = now or datetime.now(CN_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CN_TZ)      # naive 输入按**北京时间**读(市场时刻口径)
    return dt.astimezone(CN_TZ).isoformat(timespec="seconds")


@dataclass(frozen=True)
class VerificationRow:
    id: int
    basket_id: int
    trade_date: str
    observed_at: str
    state: str
    source: str
    evidence: Dict[str, Any]


@dataclass(frozen=True)
class CurrentState:
    """「当前状态」三路读法的结果。`provisional=True` = 盘中暂态、未收盘定论;
    `not_evaluated=True` = **今天还没判过**(不是"判了是 unclear")。"""

    state: str
    source: Optional[str] = None
    observed_at: Optional[str] = None
    provisional: bool = False
    not_evaluated: bool = False
    evidence: Optional[Dict[str, Any]] = None

    @property
    def label(self) -> str:
        if self.not_evaluated:
            return "今日尚未判定"
        if self.provisional:
            return "盘中暂态、未收盘定论"
        return "收盘定论"


def _rows(conn, basket_id: int, day: str) -> List[VerificationRow]:
    cur = conn.execute(
        f"SELECT id, {_COLUMNS} FROM basket_verification "
        "WHERE basket_id=? AND trade_date=? ORDER BY id",
        (int(basket_id), day),
    ).fetchall()
    out: List[VerificationRow] = []
    for r in cur:
        try:
            evidence = json.loads(r[6]) if r[6] else {}
        except (json.JSONDecodeError, TypeError):
            evidence = {}
        out.append(VerificationRow(
            id=int(r[0]), basket_id=int(r[1]), trade_date=str(r[2]), observed_at=str(r[3]),
            state=str(r[4]), source=str(r[5]), evidence=evidence,
        ))
    return out


def _insert(conn, basket_id: int, day: str, stamp: str, state: str, source: str,
            evidence: Dict[str, Any]) -> None:
    conn.execute(
        f"INSERT INTO basket_verification ({_COLUMNS}) VALUES (?,?,?,?,?,?,?)",
        (int(basket_id), day, stamp, state, source,
         json.dumps(evidence, ensure_ascii=False, sort_keys=True),
         datetime.now(CN_TZ).isoformat(timespec="seconds")),
    )


def _payload(verdict: "BasketVerdict") -> Dict[str, Any]:
    ev = dict(verdict.evidence or {})
    if verdict.reason:
        ev["reason"] = verdict.reason
    return ev


def append_if_changed(
    basket_id: int,
    trade_date: date,
    verdict: "BasketVerdict",
    *,
    source: str = SOURCE_INTRADAY,
    observed_at: Optional[str] = None,   # noqa: A002 - 与列名同名是刻意的
    db_path: Optional[Path] = None,
) -> str:
    """盘中路径:**状态变了才追加**;当日已 `falsified` → 不再落新行(终态定格)。"""
    day = _d(trade_date)
    stamp = observed_at or observed_at_now()
    init_schema(db_path)
    with connection(db_path) as conn:
        existing = _rows(conn, basket_id, day)
        if any(r.state == vr.STATE_FALSIFIED for r in existing):
            return SKIPPED_LATCHED
        if existing and existing[-1].state == verdict.state:
            return SKIPPED_UNCHANGED
        _insert(conn, basket_id, day, stamp, verdict.state, source, _payload(verdict))
    return WROTE


def append_row(
    basket_id: int,
    trade_date: date,
    verdict: "BasketVerdict",
    *,
    source: str = SOURCE_EOD,
    observed_at: Optional[str] = None,   # noqa: A002
    db_path: Optional[Path] = None,
) -> str:
    """EOD 路径:**无论状态变没变都落一行**。当日已 `falsified` → 这一行的 `state`
    仍是 `falsified`(不撤回),本次算出的结果原样留在 `evidence.latched_over` 里
    **供审计**(藏起来不是诚实,但也不许让它改写定格结论)。"""
    day = _d(trade_date)
    stamp = observed_at or observed_at_now()
    init_schema(db_path)
    with connection(db_path) as conn:
        existing = _rows(conn, basket_id, day)
        latched = any(r.state == vr.STATE_FALSIFIED for r in existing)
        payload = _payload(verdict)
        state = verdict.state
        if latched and verdict.state != vr.STATE_FALSIFIED:
            payload = {
                "latched": True,
                "note": "当日早前已判 falsified,终态不撤回;本次重算结果如实留档但不改写结论",
                "latched_over": payload,
            }
            state = vr.STATE_FALSIFIED
        _insert(conn, basket_id, day, stamp, state, source, payload)
    return WROTE_LATCHED if (latched and verdict.state != vr.STATE_FALSIFIED) else WROTE


def list_rows(basket_id: int, trade_date: date, *, db_path: Optional[Path] = None) -> List[VerificationRow]:
    """某篮某日的全部状态流水(升序)。审计 / 单测 / ⑨ 复盘用。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        return _rows(conn, basket_id, _d(trade_date))


def current_state(basket_id: int, trade_date: date, *, db_path: Optional[Path] = None) -> CurrentState:
    """「当前状态」三路读法(⑧-C2 第 5 条,⑧/⑭/⑮ 共用这一个实现,⛔ 别各写一份)。"""
    rows = list_rows(basket_id, trade_date, db_path=db_path)
    eod = [r for r in rows if r.source == SOURCE_EOD]
    if eod:
        last = eod[-1]
        return CurrentState(state=last.state, source=SOURCE_EOD, observed_at=last.observed_at,
                            evidence=last.evidence)
    intraday = [r for r in rows if r.source == SOURCE_INTRADAY]
    if intraday:
        last = intraday[-1]
        return CurrentState(state=last.state, source=SOURCE_INTRADAY, observed_at=last.observed_at,
                            provisional=True, evidence=last.evidence)
    return CurrentState(state=vr.STATE_UNCLEAR, not_evaluated=True)


def states_for_date(trade_date: date, *, db_path: Optional[Path] = None) -> Dict[int, CurrentState]:
    """该日**有过判定**的篮子 → 当前状态(报告 / 端点批量读用)。没判过的篮子**不在
    返回里** —— 调用方自己决定要不要把它显示成 `not_evaluated`(别在这里编行)。"""
    day = _d(trade_date)
    init_schema(db_path)
    with connection(db_path) as conn:
        ids = [int(r[0]) for r in conn.execute(
            "SELECT DISTINCT basket_id FROM basket_verification WHERE trade_date=? ORDER BY basket_id",
            (day,),
        ).fetchall()]
    return {bid: current_state(bid, trade_date, db_path=db_path) for bid in ids}


__all__ = [
    "SOURCE_INTRADAY", "SOURCE_EOD",
    "WROTE", "SKIPPED_UNCHANGED", "SKIPPED_LATCHED", "WROTE_LATCHED",
    "VerificationRow", "CurrentState",
    "observed_at_now", "append_if_changed", "append_row",
    "list_rows", "current_state", "states_for_date",
]
