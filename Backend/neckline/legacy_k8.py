"""K8 历史数据的**只读追溯入口**(V2.5.0 S13,PROJECT_PLAN 裁定 6 / §5.12 / §6.13)。

裁定 6 逐字:K8 时代的那批表**保留、只读、不迁移、不回填**,并保留**一个**只读入口
供追溯。本模块就是那一个入口,`GET /api/v1/legacy/k8/baskets` 是它唯一的消费方。

🔴 **本模块结构上只会 SELECT。** 守门单测扫本文件的 SQL 字面量:
`INSERT` / `UPDATE` / `DELETE` / `REPLACE` / `CREATE` / `DROP` / `ALTER` **零命中**。
⛔ 不是靠注释提醒 —— 一条写路径都不存在,想写就得先加一条 SQL,而那当场被守门拦下。

⚠ **它读的是 K8 的语义,不是 K9 的**。`tier` / `driver` / `role_llm` / `stop_pct` 这些
字段属于已退役的「驱动种子 → 方向 → 篮子 → 六关 → Tier → 卡片」那条链;它们对 K9
**没有任何意义**,⛔ 不许被翻译成 K9 的 `pattern` / `seat_kind` / `first_resistance`,
也 ⛔ 不许进任何成绩线。它们只回答一个问题:**「那天那份篮子是什么样」**。

⚠ **表可能根本不存在**(全新库从没建过这些 K8 表 —— S1 起 `init_schema` 不再为新库
建它们)。本模块把「表不在」与「表在但那天没有行」**分开说**:前者是
`available=False`(这个库里就没有 K8 历史),后者是 `found=False`(有历史,但不是那天)。
⛔ 合成一句会让人以为数据丢了。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline.config import settings

logger = logging.getLogger(__name__)

#: 裁定 6 点名「保留、只读、不迁移、不回填」的 K8 表。⚠ 这份清单是**登记册**,
#: 不是本模块会去读的全集 —— 本模块只读前三张(篮子 / 成员 / 卡),其余留给
#: 将来真有人要追溯时按同一条纪律加读函数。
LEGACY_TABLES = (
    "baskets", "basket_members", "basket_cards", "basket_verification",
    "basket_review_daily", "tier_history", "gate_evaluations", "out_candidates",
    "basket_dropped_handoff", "basket_stage_handoff", "reports",
    "industry_strength_daily", "industry_stage_daily", "limit_cluster_daily",
    "corr_matrix_daily", "leader_structure_daily",
    "positions", "position_plans", "entry_snapshots", "decision_log",
    "custom_alerts", "selection_packs", "strategy_versions",
)

_ROOT_TABLES = ("baskets", "basket_members", "basket_cards")


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _readonly(db_path: Optional[Path]) -> sqlite3.Connection:
    """**只读连接**(`mode=ro`)。⛔ 不走 `neckline.db.connection` —— 那一条是受控
    **写**入口(它还顺手 `init_schema`,而本模块绝不该给任何库建表)。
    库文件不存在时 `mode=ro` 直接抛,不会像可写连接那样凭空造一个空库出来。"""
    p = Path(db_path or settings.db_path)
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def _tables_present(conn: sqlite3.Connection, names) -> Dict[str, bool]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    have = {r[0] for r in rows}
    return {n: (n in have) for n in names}


def _json_or_raw(raw: Optional[str]) -> Any:
    """K8 的 `card_json` 是当年冻住的 blob。解不出就**原样回字符串**并留 WARNING,
    ⛔ 不吞成 `{}` —— 追溯要看的正是那份原文,把它换成空字典等于把历史抹了。"""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("[legacy_k8] card_json 解析失败,原样返回字符串")
        return raw


def load_baskets(
    trade_date: Optional[date] = None, *, db_path: Optional[Path] = None
) -> Dict[str, Any]:
    """K8 篮子的只读追溯。

    `trade_date=None` → 只回**总览**(这个库里有哪几天、共多少篮),供调用方先定位;
    给了日期 → 那天的篮子 + 成员 + **最新版**卡(`MAX(version)`)。

    返回体**四态**,⛔ 一个都不许合并 —— 它们对读者意味着完全不同的事:
      · `available=False` —— 这个库里**连 `baskets` 表都没有**(外部库 / 指错了路径);
      · `available=True`,`overview.basketCount == 0` —— 表在、**一行都没有**:
        这个库**从没跑过 K8**(⚠ 全新 Neckline 库就是这样:`init_schema` 仍按裁定 6
        建那张表,只是应用层没有任何写路径了);
      · `available=True, found=False` —— 有 K8 历史,但**那天**没有篮子;
      · `available=True, found=True` —— 有。

    ⚠ 第二态与第三态的 `reason` **必须说不同的话**:「这库没跑过 K8」与
    「跑过、只是不是这一天」会让人去做完全不同的下一步。
    """
    try:
        conn = _readonly(db_path)
    except sqlite3.OperationalError as exc:
        return {"available": False, "found": False,
                "reason": f"K8 历史库打不开(只读):{exc}"}
    try:
        present = _tables_present(conn, _ROOT_TABLES)
        if not present["baskets"]:
            return {"available": False, "found": False,
                    "reason": "本库没有 `baskets` 表 —— 它从没跑过 K8(裁定 6:"
                              "旧表保留只读,但新库不为它建表)。"}

        span = conn.execute(
            "SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM baskets").fetchone()
        overview = {
            "firstDate": span[0], "lastDate": span[1], "basketCount": int(span[2] or 0),
            "tablesPresent": present,
        }
        if trade_date is None:
            return {"available": True, "found": overview["basketCount"] > 0,
                    "overview": overview, "date": None, "baskets": []}

        day = _d(trade_date)
        rows = conn.execute(
            "SELECT id, trade_date, basket_key, name, driver, driver_kind, tier, "
            "pack_version, engine_api_version, charter_version, via, evidence_status, "
            "selection_run_id, created_at FROM baskets WHERE trade_date=? "
            "ORDER BY tier, id",
            (day,),
        ).fetchall()
        if not rows:
            reason = (
                "本库的 `baskets` 表里一行都没有 —— 它**从没跑过 K8**"
                "(表按裁定 6 保留只读,应用层已无写路径)。"
                if overview["basketCount"] == 0
                else f"{day} 没有 K8 篮子(库里有 K8 历史 "
                     f"{overview['firstDate']}—{overview['lastDate']},但不是这一天)。"
            )
            return {"available": True, "found": False, "date": day,
                    "overview": overview, "baskets": [], "reason": reason}

        ids = [int(r[0]) for r in rows]
        holes = ",".join("?" for _ in ids)
        members: Dict[int, List[Dict[str, Any]]] = {i: [] for i in ids}
        if present["basket_members"]:
            for m in conn.execute(
                f"SELECT basket_id, ts_code, role_llm, role_mech, role_conflict, reason, "
                f"is_primary FROM basket_members WHERE basket_id IN ({holes}) "
                f"ORDER BY basket_id, ts_code", ids,
            ).fetchall():
                members[int(m[0])].append({
                    "tsCode": m[1], "roleLlm": m[2], "roleMech": m[3],
                    "roleConflict": bool(m[4]), "reason": m[5],
                    "isPrimary": bool(m[6]),
                })
        cards: Dict[int, Dict[str, Any]] = {}
        if present["basket_cards"]:
            for c in conn.execute(
                f"SELECT basket_id, version, card_json, created_at FROM basket_cards c "
                f"WHERE basket_id IN ({holes}) AND version = "
                f"(SELECT MAX(version) FROM basket_cards q WHERE q.basket_id=c.basket_id)",
                ids,
            ).fetchall():
                cards[int(c[0])] = {"version": int(c[1]), "card": _json_or_raw(c[2]),
                                    "createdAt": c[3]}

        baskets = [
            {
                "id": int(r[0]), "tradeDate": r[1], "basketKey": r[2], "name": r[3],
                "driver": r[4], "driverKind": r[5], "tier": r[6],
                "packVersion": r[7], "engineApiVersion": r[8], "charterVersion": r[9],
                "via": r[10], "evidenceStatus": r[11], "selectionRunId": r[12],
                "createdAt": r[13],
                "members": members.get(int(r[0]), []),
                "latestCard": cards.get(int(r[0])),
            }
            for r in rows
        ]
        return {"available": True, "found": True, "date": day,
                "overview": overview, "baskets": baskets}
    finally:
        conn.close()


__all__ = ["LEGACY_TABLES", "load_baskets"]
