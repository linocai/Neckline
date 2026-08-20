"""预案的落库与读回(V2.5.0 S10)。表 `k9_playbooks`,**append-only 版本化**。

🔴 **⛔ 没有 UPDATE**(K9 §6.4 / §5.6.4:「用户修改产生新版本、原版本不变」):
本模块只有 `INSERT`。用户改一次 → `version + 1` 的**新行**;
9:26 与 10:00 两拍读的一律是 `max(version)`。守门单测扫本文件的 SQL:
`UPDATE k9_playbooks` / `DELETE FROM k9_playbooks` 零命中。

⚠ **改版本会改变次日核对的判据**,所以每一版都带 `source` 与 `filled_by`
—— 「这条件是模型给的还是我改的」在事后必须查得出。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from neckline.db import connection, init_schema
from neckline.playbook.model import (
    Playbook,
    PlaybookInvalid,
    SOURCE_LLM,
    SOURCE_USER,
    SOURCES,
    parse_playbook,
)

logger = logging.getLogger(__name__)

TABLE = "k9_playbooks"


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def next_version(
    trade_date: date, ts_code: str, *, db_path: Optional[Path] = None
) -> int:
    """下一个版本号(现有最大 + 1;没有则 1)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        r = conn.execute(
            f"SELECT MAX(version) FROM {TABLE} WHERE trade_date=? AND ts_code=?",
            (_d(trade_date), ts_code),
        ).fetchone()
    return int(r[0]) + 1 if r and r[0] is not None else 1


def save(playbook: Playbook, *, db_path: Optional[Path] = None) -> int:
    """落一版预案。**`INSERT`,⛔ 不是 `INSERT OR REPLACE`** ——
    同 `(trade_date, ts_code, version)` 再写一次直接抛(冻结件不可覆盖)。

    返回写进去的 `version`。"""
    if playbook.source not in SOURCES:
        raise PlaybookInvalid(f"source 只能是 {list(SOURCES)},收到 {playbook.source!r}")
    init_schema(db_path)
    with connection(db_path) as conn:
        conn.execute(
            f"INSERT INTO {TABLE} "
            "(trade_date, ts_code, version, source, pattern, first_resistance, "
            " second_resistance, invalidation, branches_json, filled_by, filled_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (playbook.trade_date, playbook.ts_code, playbook.version, playbook.source,
             playbook.pattern, playbook.levels.first_resistance,
             playbook.levels.second_resistance, playbook.levels.invalidation,
             json.dumps([b.to_dict() for b in playbook.branches], ensure_ascii=False),
             playbook.filled_by, playbook.filled_at, _now()),
        )
    return playbook.version


def _row_to_playbook(r: Sequence[Any]) -> Playbook:
    return parse_playbook({
        "tradeDate": r[0], "tsCode": r[1], "version": int(r[2]), "source": r[3],
        "pattern": r[4],
        "levels": {"firstResistance": r[5], "secondResistance": r[6], "invalidation": r[7]},
        "branches": json.loads(r[8]),
        "filledBy": r[9], "filledAt": r[10],
    })


_SELECT = ("trade_date, ts_code, version, source, pattern, first_resistance, "
           "second_resistance, invalidation, branches_json, filled_by, filled_at")


def load_latest(
    trade_date: date, *, codes: Optional[Sequence[str]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Playbook]:
    """某个 D0 每只票的**最新版**预案。

    ⚠ 解析不过的行**跳过并 WARNING**,⛔ 不让它掀翻整张核对表:一份坏预案
    只该让**那一只**票落进「没有可用预案」那一栏,而不是让今天早上整拍不出。
    """
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_SELECT} FROM {TABLE} p WHERE trade_date=? AND version = "
            f"(SELECT MAX(version) FROM {TABLE} q WHERE q.trade_date=p.trade_date "
            f" AND q.ts_code=p.ts_code) ORDER BY ts_code",
            (_d(trade_date),),
        ).fetchall()
    want = set(codes) if codes is not None else None
    out: Dict[str, Playbook] = {}
    for r in rows:
        if want is not None and r[1] not in want:
            continue
        try:
            out[r[1]] = _row_to_playbook(r)
        except PlaybookInvalid:
            logger.warning("[playbook] %s %s 的冻结预案解析不过,本次跳过这一只",
                           r[0], r[1], exc_info=True)
    return out


def load_versions(
    trade_date: date, ts_code: str, *, db_path: Optional[Path] = None
) -> List[Playbook]:
    """一只票的**全部版本**(升序)。用户改过几次、每次改了什么,在这里看得见。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_SELECT} FROM {TABLE} WHERE trade_date=? AND ts_code=? ORDER BY version",
            (_d(trade_date), ts_code),
        ).fetchall()
    out: List[Playbook] = []
    for r in rows:
        try:
            out.append(_row_to_playbook(r))
        except PlaybookInvalid:
            logger.warning("[playbook] %s %s v%s 解析不过,跳过", r[0], r[1], r[2], exc_info=True)
    return out


def count_for_day(trade_date: date, *, db_path: Optional[Path] = None) -> int:
    """当日有几只票冻了预案(去重后)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        r = conn.execute(
            f"SELECT COUNT(DISTINCT ts_code) FROM {TABLE} WHERE trade_date=?",
            (_d(trade_date),),
        ).fetchone()
    return int(r[0]) if r else 0


__all__ = [
    "TABLE", "SOURCE_LLM", "SOURCE_USER",
    "next_version", "save", "load_latest", "load_versions", "count_for_day",
]
