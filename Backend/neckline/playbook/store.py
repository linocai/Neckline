"""预案的落库与读回(V2.5.0 S10)。表 `k9_playbooks`,**append-only 版本化**。

🔴 **⛔ 没有 UPDATE**(K9 §6.4 / §5.6.4:「用户修改产生新版本、原版本不变」):
本模块只有 `INSERT`。用户改一次 → `version + 1` 的**新行**。守门单测扫本文件的
SQL:`UPDATE k9_playbooks` / `DELETE FROM k9_playbooks` 零命中。

🔴 **「用哪一版」与「谁定案」是两件事**(R2-03,落实 K9 §六「**D0 冻结**」+
架构 §四「代入 D0**已冻结**的预案条件」):

  · **9:26 那一拍**读 `load_latest` —— 到那一刻为止的最新版就是 D0 冻结那一版,
    因为 `POST …/playbook` 有**冻结闸**:D1 一开始就拒绝再写新版本
    (`api/app.py::post_stock_playbook`,`PlaybookFrozen`);
  · **10:00 结算拍**读 `load_at_versions` —— 用 9:26 那一拍**记在
    `k9_d1_verdicts.playbook_version` 里的那一版**,⛔ 不再取一次 `MAX(version)`。
    这是第二道锁:就算有别的写入口(CLI / 回放 / 将来某个脚本)在两拍之间塞进
    一个新版本,**权威那一拍代入的仍然是账上记着的那一版** ——
    ⛔ 绝不允许出现「终值按 v2 求的、账上记着 v1」这种查不出来的污染。

⚠ **改版本会改变次日核对的判据**,所以每一版都带 `source` 与 `filled_by`
—— 「这条件是模型给的还是我改的」在事后必须查得出。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.db import connection, init_schema, readonly_tables
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

    ⚠ **只读**(`readonly_tables`,R3-🔴-2):表还没建的老库读出来就是空
    —— ⛔ 读一次不许把库迁移掉(README /§9.2 /§9.4)。
    """
    with readonly_tables(TABLE, db_path=db_path) as conn:
        if conn is None:
            return {}
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


def load_at_versions(
    trade_date: date, versions: Mapping[str, int], *, db_path: Optional[Path] = None
) -> Dict[str, Playbook]:
    """🔴 **按点名的版本号**取预案(R2-03)——「用哪一版」的唯一取法。

    `versions` = `{ts_code: version}`,通常来自 `k9_d1_verdicts.playbook_version`
    (9:26 那一拍**冻在账上**的那一版)。10:00 结算拍用它,⛔ 不再取 `MAX(version)`:
    裁定 10 锁住了「三分支的唯一权威是 10:00 这一拍」,但**没**锁住这一拍
    「代入哪一版条件」—— 权威那一拍代入一份**在看过竞价之后才写下**的条件,
    等于事后改写 D1 辅助成绩的分母，而且在账上**查不出来**
    (`playbook_version` 那一列还记着旧版号)。

    ⚠ 点名的版本**不存在** → 那只票缺席(⛔ 不静默回退到最新版:回退正是本条要防的
    那件事)。解析不过的行同 `load_latest`:跳过 + WARNING。
    """
    wanted = {c: int(v) for c, v in versions.items() if c}
    if not wanted:
        return {}
    pairs = sorted(wanted.items())
    placeholders = ",".join("(?,?)" for _ in pairs)
    flat: List[Any] = []
    for code, ver in pairs:
        flat.extend((code, ver))
    with readonly_tables(TABLE, db_path=db_path) as conn:   # 只读,R3-🔴-2
        if conn is None:
            return {}
        rows = conn.execute(
            f"SELECT {_SELECT} FROM {TABLE} WHERE trade_date=? "
            f"AND (ts_code, version) IN (VALUES {placeholders}) ORDER BY ts_code",
            (_d(trade_date), *flat),
        ).fetchall()
    out: Dict[str, Playbook] = {}
    for r in rows:
        try:
            out[r[1]] = _row_to_playbook(r)
        except PlaybookInvalid:
            logger.warning("[playbook] %s %s v%s 的冻结预案解析不过,本次跳过这一只",
                           r[0], r[1], r[2], exc_info=True)
    missing = sorted(set(wanted) - set(out))
    if missing:
        logger.warning(
            "[playbook] %s 有 %d 只点名的版本取不回来(%s)—— 这几只本次缺席,"
            "⛔ 不回退到最新版", trade_date, len(missing), missing[:5])
    return out


def load_versions(
    trade_date: date, ts_code: str, *, db_path: Optional[Path] = None
) -> List[Playbook]:
    """一只票的**全部版本**(升序)。用户改过几次、每次改了什么,在这里看得见。

    ⚠ **只读**(R3-🔴-2):表还没建 → 空列表。"""
    with readonly_tables(TABLE, db_path=db_path) as conn:
        if conn is None:
            return []
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


def load_latest_range(
    start: date, end: date, codes: Sequence[str], *, db_path: Optional[Path] = None
) -> Dict[Tuple[str, str], Playbook]:
    """`[start, end]` × `codes` 的**最新版**预案:`(trade_date, ts_code) → Playbook`。

    🔴 **一次查询**取全,⛔ 别在调用方按日循环调 `load_latest` —— `init_schema()` 在
    每次调用里都会重跑整份 schema 脚本,40 天的复盘窗口就是 40 次全表建表检查
    (S11 复盘装订原本会踩这个)。

    解析不过的行同 `load_latest`:**跳过并 WARNING**,⛔ 不让一份坏预案掀翻整份材料。
    """
    wanted = [c for c in dict.fromkeys(codes) if c]
    if not wanted or start > end:
        return {}
    placeholders = ",".join("?" for _ in wanted)
    with readonly_tables(TABLE, db_path=db_path) as conn:   # 只读,R3-🔴-2
        if conn is None:
            return {}
        rows = conn.execute(
            f"SELECT {_SELECT} FROM {TABLE} p WHERE p.trade_date>=? AND p.trade_date<=? "
            f"AND p.ts_code IN ({placeholders}) AND p.version = "
            f"(SELECT MAX(version) FROM {TABLE} q WHERE q.trade_date=p.trade_date "
            f" AND q.ts_code=p.ts_code) ORDER BY p.trade_date, p.ts_code",
            (_d(start), _d(end), *wanted),
        ).fetchall()
    out: Dict[Tuple[str, str], Playbook] = {}
    for r in rows:
        try:
            out[(r[0], r[1])] = _row_to_playbook(r)
        except PlaybookInvalid:
            logger.warning("[playbook] %s %s 的冻结预案解析不过,本次跳过这一只",
                           r[0], r[1], exc_info=True)
    return out


def count_for_day(trade_date: date, *, db_path: Optional[Path] = None) -> int:
    """当日有几只票冻了预案(去重后)。⚠ **只读**(R3-🔴-2):表还没建 → 0。"""
    with readonly_tables(TABLE, db_path=db_path) as conn:
        if conn is None:
            return 0
        r = conn.execute(
            f"SELECT COUNT(DISTINCT ts_code) FROM {TABLE} WHERE trade_date=?",
            (_d(trade_date),),
        ).fetchone()
    return int(r[0]) if r else 0


__all__ = [
    "TABLE", "SOURCE_LLM", "SOURCE_USER",
    "next_version", "save", "load_latest", "load_at_versions", "load_versions",
    "load_latest_range", "count_for_day",
]
