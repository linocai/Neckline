"""篮子四表的**唯一写入口**(plan §五【planner 裁定 · 跨块】「篮子四表的运行期
落库次序」,2026-08-02)。

**为什么一族表一个 store**:`baskets` / `basket_members` / `tier_history` /
`basket_cards` 四张表有**事务边界**要管,写入口散在各块里就管不住了。这也是项目
既有体例(`report/store.py` / `reference_plan_store.py` / `industry_strength_store.py`
/ `review/store.py` 都是「一族表一个 store」)。

**运行期次序(裁定定死,不许改序)**::

    ⑤ aggregate_baskets()   → 内存:BasketCandidate[](无 tier)
    ⑥ score_and_tier()      → 内存:tier / mech_score / breakdown / rank
    ⑥ 【事务 1】单事务       → baskets(拿 id)→ basket_members(用 id)→ tier_history(用 id)
    ⑦ build_card()          → 调 LLM(慢、可失败)
    ⑦ 【事务 2】             → basket_cards(version=1)

**刻意两个事务**:⑦ 的卡生成要调 LLM,跨 LLM 调用持 SQLite 事务是错的(慢、易
超时、锁库)。故本模块提供 `save_tier_decision()`(事务 1)与 `save_basket_card()`
/ `save_basket_cards()`(事务 2,V2-⑦ 落地)两个独立入口,**不提供把四张表并成
一个事务的路径**。「有篮子、无卡」是**合法中间态**(事务 1 成功、⑦ 的 LLM 不可用 /
预算耗尽 / 生成失败):不回删篮子、不抛异常,同一 D0 内可重跑补 `version=1`。

**幂等与冻结(裁定定死)**:四张表一律 `INSERT OR IGNORE`,同日重跑 = no-op、
**绝不覆盖既有行**(它们是 D0 冻结件)。⚠ 这与 v1.5 契约线 🟡-1「同日重跑要在写侧
清掉旧行」**不冲突,别套错模板** —— 那条治的是「快照上的标」与「响应时现连的表」
讲相反的话;这里四张表同批冻结、只有一个说法。**重跑发现不一致必须留痕**:本模块
把差异逐条 WARNING 落日志并**原样返回给调用方**(`frozen_conflicts`),由报告层
如实披露,**不静默**。

**搬迁说明(V2-⑥)**:`save_baskets()` 从 `neckline/selection/aggregate.py` **原地
搬来,行为逐字节不变**(新增的 `conn=` 参数默认 `None`,不传时与搬迁前逐字等价);
`aggregate.py` 保留同名再导出,⑤ 的既有单测一字不动 —— 照 ⑤ 自己刚做过的
`llm/json_block.py` 搬迁体例。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.db import connection, init_schema

if TYPE_CHECKING:  # pragma: no cover - 仅供类型标注,运行期不 import(防循环)
    from neckline.selection.aggregate import AggregateResult

logger = logging.getLogger(__name__)


_BASKET_COLUMNS = (
    "trade_date, basket_key, name, driver, driver_kind, tier, pack_version, "
    "engine_api_version, charter_version, via, evidence_status, created_at"
)
_MEMBER_COLUMNS = (
    "basket_id, ts_code, role_llm, role_mech, role_conflict, reason, is_primary, created_at"
)
_TIER_HISTORY_COLUMNS = (
    "trade_date, basket_id, tier, mech_score, mech_breakdown_json, rank_in_tier, "
    "rank_mech, llm_rank_delta, llm_reason, pack_version, created_at"
)
_BASKET_CARD_COLUMNS = (
    "basket_id, version, card_json, stop_pct, take_profit_retrace, charter_version, "
    "pack_version, engine_api_version, created_at"
)

# `save_tier_history` 每条 entry 的必填键(**缺一就 fail loud**,不补默认值 ——
# `tier_history` 的 `rank_mech`/`rank_in_tier` 两者都 NOT NULL 正是为了"可复现、
# 可归因",臆造任何一个都等于伪造留痕)。`basket_id` 由 `save_tier_decision` 在
# 事务内填,故不在这里要求。
_TIER_HISTORY_REQUIRED = (
    "tier", "mech_score", "mech_breakdown", "rank_in_tier", "rank_mech", "pack_version",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ══════════════════════════════════════════════════════════════════════════
# `baskets` / `basket_members`(V2-⑥ 写入;搬自 aggregate.py,行为不变)
# ══════════════════════════════════════════════════════════════════════════

def _validate_tiers(result: "AggregateResult", tier_by_basket_key: Mapping[str, int]) -> None:
    missing = [b.basket_key for b in result.baskets if b.basket_key not in tier_by_basket_key]
    if missing:
        raise ValueError(
            f"save_baskets:以下篮子缺 tier,拒绝落库(tier 由 ⑥ 定档,本层不臆造):{missing}"
        )
    bad_tier = {
        b.basket_key: tier_by_basket_key[b.basket_key]
        for b in result.baskets
        if tier_by_basket_key[b.basket_key] not in (1, 2, 3)
    }
    if bad_tier:
        raise ValueError(f"save_baskets:tier 只能是 1/2/3,实得 {bad_tier}")


def _save_baskets_on_conn(
    conn: sqlite3.Connection,
    result: "AggregateResult",
    tier_by_basket_key: Mapping[str, int],
    *,
    via: str,
    now: str,
) -> Tuple[Dict[str, int], Dict[str, int], List[str]]:
    """事务内的实际写入。返回 `(stats, basket_id_by_key, frozen_conflicts)`。

    `frozen_conflicts` = 「库里已冻结的行与本次算出的不一致」的人读描述(**只记录、
    不覆盖**);调用方负责如实披露。
    """
    stats = {"baskets_inserted": 0, "baskets_existing": 0, "members_inserted": 0}
    ids: Dict[str, int] = {}
    conflicts: List[str] = []
    for b in result.baskets:
        tier = int(tier_by_basket_key[b.basket_key])
        cur = conn.execute(
            f"INSERT OR IGNORE INTO baskets ({_BASKET_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                b.trade_date, b.basket_key, b.name, b.driver, b.driver_kind,
                tier, b.pack_version,
                b.engine_api_version, b.charter_version, via, b.evidence_status, now,
            ),
        )
        if cur.rowcount:
            stats["baskets_inserted"] += 1
        else:
            stats["baskets_existing"] += 1
            logger.warning(
                "[aggregate] baskets 已存在同日同键行(%s/%s),幂等跳过、不覆盖既有行。",
                b.trade_date, b.basket_key,
            )
        row = conn.execute(
            "SELECT id, tier FROM baskets WHERE trade_date=? AND basket_key=?",
            (b.trade_date, b.basket_key),
        ).fetchone()
        basket_id = int(row[0])
        ids[b.basket_key] = basket_id
        if int(row[1]) != tier:
            conflicts.append(
                f"baskets[{b.trade_date}/{b.basket_key}].tier 冻结值 {int(row[1])} ≠ 本次算出 {tier}"
            )
        frozen_members = {
            r[0] for r in conn.execute(
                "SELECT ts_code FROM basket_members WHERE basket_id=?", (basket_id,)
            ).fetchall()
        }
        for m in b.members:
            mc = conn.execute(
                f"INSERT OR IGNORE INTO basket_members ({_MEMBER_COLUMNS}) VALUES (?,?,?,?,?,?,?,?)",
                (basket_id, m.ts_code, m.role_llm, m.role_mech, int(m.role_conflict),
                 m.reason, int(m.is_primary), now),
            )
            stats["members_inserted"] += mc.rowcount or 0
        if frozen_members and frozen_members != {m.ts_code for m in b.members}:
            conflicts.append(
                f"basket_members[{b.trade_date}/{b.basket_key}] 冻结成员集 "
                f"{sorted(frozen_members)} ≠ 本次算出 {sorted(m.ts_code for m in b.members)}"
            )
    return stats, ids, conflicts


def save_baskets(
    result: "AggregateResult",
    *,
    tier_by_basket_key: Mapping[str, int],
    db_path: Optional[Path] = None,
    via: str = "auto",
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, int]:
    """把聚合结果落 `baskets` / `basket_members`。

    ⚠ **`tier_by_basket_key` 是必填、且必须覆盖每一个篮子** —— `baskets.tier` 是
    `NOT NULL`,而 tier 由 **⑥ Tier 分层引擎**定档。本函数**绝不臆造 tier**:少一个
    就 `ValueError` fail loud。这也是为什么 `aggregate_baskets()` **不自动调用本
    函数** —— ⑤ 只提供能力,写入时机归 ⑥(裁定后 `baskets`/`basket_members` 的
    写入块由 ⑦ 改判为 ⑥,见模块头「运行期次序」)。

    **幂等语义**:`UNIQUE(trade_date, basket_key)` / `UNIQUE(basket_id, ts_code)`
    走 `INSERT OR IGNORE` —— 同日同 `basket_key` 重跑 = 幂等 no-op,**不覆盖已写入
    的行**(冻结/追加三律:模型判断是新版本、不回写)。驱动一旦变了,`driver_slug`
    随之变、`basket_key` 也就变了,这正是 `crc32(trade_date|driver_slug)` 的设计
    意图,不需要"更新"这个动作。

    `conn`:给了就**复用调用方的 connection、不自己开事务也不 commit**(供
    `save_tier_decision` 的事务 1 并入三表同批落地);不给就照旧自开自提交 ——
    **不传 `conn` 时行为与搬迁前逐字节等价**。
    """
    _validate_tiers(result, tier_by_basket_key)

    stats = {"baskets_inserted": 0, "baskets_existing": 0, "members_inserted": 0}
    if not result.baskets:
        return stats

    now = _now()
    if conn is not None:
        stats, _ids, _conflicts = _save_baskets_on_conn(
            conn, result, tier_by_basket_key, via=via, now=now
        )
        return stats

    init_schema(db_path)
    with connection(db_path) as own:
        stats, _ids, _conflicts = _save_baskets_on_conn(
            own, result, tier_by_basket_key, via=via, now=now
        )
    return stats


# ══════════════════════════════════════════════════════════════════════════
# `tier_history`(V2-⑥ 写入,与上面两张**同一事务**)
# ══════════════════════════════════════════════════════════════════════════

def _tier_history_row(entry: Mapping[str, Any], *, trade_date: str, basket_id: int, now: str):
    missing = [k for k in _TIER_HISTORY_REQUIRED if k not in entry]
    if missing:
        raise ValueError(
            f"save_tier_history:entry 缺必填键 {missing}(rank_mech/rank_in_tier 两者都要,"
            f"少一个就不是可复现可归因的留痕,本层不补默认值)"
        )
    breakdown = entry["mech_breakdown"]
    breakdown_json = (
        breakdown if isinstance(breakdown, str)
        else json.dumps(breakdown, ensure_ascii=False, sort_keys=True)
    )
    return (
        trade_date, basket_id, int(entry["tier"]), float(entry["mech_score"]), breakdown_json,
        int(entry["rank_in_tier"]), int(entry["rank_mech"]),
        int(entry.get("llm_rank_delta", 0) or 0), entry.get("llm_reason"),
        str(entry["pack_version"]), now,
    )


def _save_tier_history_on_conn(
    conn: sqlite3.Connection,
    rows: Sequence[Tuple[Any, ...]],
) -> Tuple[Dict[str, int], List[str]]:
    stats = {"tier_history_inserted": 0, "tier_history_existing": 0}
    conflicts: List[str] = []
    for row in rows:
        cur = conn.execute(
            f"INSERT OR IGNORE INTO tier_history ({_TIER_HISTORY_COLUMNS}) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            row,
        )
        if cur.rowcount:
            stats["tier_history_inserted"] += 1
            continue
        stats["tier_history_existing"] += 1
        logger.warning(
            "[tier] tier_history 已存在同日同篮行(%s/basket_id=%s),幂等跳过、不覆盖既有行。",
            row[0], row[1],
        )
        frozen = conn.execute(
            "SELECT tier, rank_in_tier, rank_mech FROM tier_history "
            "WHERE trade_date=? AND basket_id=?",
            (row[0], row[1]),
        ).fetchone()
        if frozen is not None and tuple(int(x) for x in frozen) != (row[2], row[5], row[6]):
            conflicts.append(
                f"tier_history[{row[0]}/basket_id={row[1]}] 冻结 (tier,rank_in_tier,rank_mech)="
                f"{tuple(int(x) for x in frozen)} ≠ 本次算出 {(row[2], row[5], row[6])}"
            )
    return stats, conflicts


def save_tier_history(
    entries: Sequence[Mapping[str, Any]],
    *,
    trade_date: str,
    basket_id_by_key: Mapping[str, int],
    db_path: Optional[Path] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, int]:
    """落 `tier_history`(每日一行幂等,`UNIQUE(trade_date, basket_id)`)。

    `entries` 每条是一个 mapping,必须带 `basket_key` 以及
    `_TIER_HISTORY_REQUIRED` 全部键;`basket_id` 由 `basket_id_by_key` 解析 ——
    **查不到就 fail loud**(篮子还没落库就写它的定档留痕 = 次序错了,不许静默跳过)。

    ⚠ 正常路径应该走 `save_tier_decision()`(三表同事务);本函数单独暴露只为
    单测与补写场景。
    """
    now = _now()
    rows = []
    for e in entries:
        key = e.get("basket_key")
        if key not in basket_id_by_key:
            raise ValueError(
                f"save_tier_history:basket_key={key!r} 在库里找不到对应 baskets.id —— "
                f"落库次序要求先写 baskets 再写 tier_history(见 basket_store 模块头)"
            )
        rows.append(_tier_history_row(e, trade_date=trade_date,
                                      basket_id=basket_id_by_key[key], now=now))
    if not rows:
        return {"tier_history_inserted": 0, "tier_history_existing": 0}
    if conn is not None:
        stats, _conflicts = _save_tier_history_on_conn(conn, rows)
        return stats
    init_schema(db_path)
    with connection(db_path) as own:
        stats, _conflicts = _save_tier_history_on_conn(own, rows)
    return stats


# ══════════════════════════════════════════════════════════════════════════
# 【事务 1】三表同批落地(V2-⑥ 的唯一正常落库路径)
# ══════════════════════════════════════════════════════════════════════════

def save_tier_decision(
    result: "AggregateResult",
    *,
    tier_by_basket_key: Mapping[str, int],
    tier_history_by_basket_key: Mapping[str, Mapping[str, Any]],
    db_path: Optional[Path] = None,
    via: str = "auto",
) -> Dict[str, Any]:
    """**事务 1**:`baskets`(拿 id)→ `basket_members`(用 id)→ `tier_history`
    (用 id),**一起成功或一起回滚,不留半截**。

    回滚靠 sqlite3 的既定语义:`connection()` 只在**正常退出**时 `commit()`,
    任何异常都会跳过 commit 直接 `close()` —— 未提交事务随之丢弃。故只要三段写在
    同一个 `with connection(...)` 里,中途抛异常就是整体回滚。

    返回 stats(含 `frozen_conflicts` 人读列表:同日重跑且结果与已冻结行不一致时
    **不覆盖**,如实带出来给报告层披露)。
    """
    stats: Dict[str, Any] = {
        "baskets_inserted": 0, "baskets_existing": 0, "members_inserted": 0,
        "tier_history_inserted": 0, "tier_history_existing": 0, "frozen_conflicts": [],
    }
    if not result.baskets:
        return stats

    _validate_tiers(result, tier_by_basket_key)
    missing_hist = [
        b.basket_key for b in result.baskets if b.basket_key not in tier_history_by_basket_key
    ]
    if missing_hist:
        raise ValueError(
            f"save_tier_decision:以下篮子缺 tier_history 留痕,拒绝落库"
            f"(rank_mech/rank_in_tier 是可复现可归因的凭据,不许只落篮子):{missing_hist}"
        )

    now = _now()
    init_schema(db_path)
    with connection(db_path) as conn:
        bstats, ids, bconflicts = _save_baskets_on_conn(
            conn, result, tier_by_basket_key, via=via, now=now
        )
        rows = [
            _tier_history_row(
                tier_history_by_basket_key[b.basket_key],
                trade_date=b.trade_date, basket_id=ids[b.basket_key], now=now,
            )
            for b in result.baskets
        ]
        hstats, hconflicts = _save_tier_history_on_conn(conn, rows)
    stats.update(bstats)
    stats.update(hstats)
    stats["frozen_conflicts"] = bconflicts + hconflicts
    if stats["frozen_conflicts"]:
        logger.warning(
            "[tier] 今日已有冻结篮子,本次重算结果**未采纳**(差异 %d 处):%s",
            len(stats["frozen_conflicts"]), "; ".join(stats["frozen_conflicts"]),
        )
    return stats


# ══════════════════════════════════════════════════════════════════════════
# 【事务 2】`basket_cards`(V2-⑦ 写入,**单独一个事务**,不与事务 1 合并)
# ══════════════════════════════════════════════════════════════════════════
#
# **为什么单独一个事务**(裁定原文):⑦ 的卡生成要**调 LLM**(剧本 / 人话条款),
# 跨 LLM 调用持 SQLite 事务是错的(慢、易超时、锁库)。故本节的入口在 LLM 调用
# **之后**才被调用,进来时 `baskets.id` 已由事务 1 落好。
#
# **追加版本制**:`version=1` 是 D0 原判(冻结);D+1 的新信息写 `version=2,3…`,
# **D0 行一字不改**。`UNIQUE(basket_id, version)` + `INSERT OR IGNORE` 使
# 「同一 D0 内重跑补 `version=1`」天然是幂等的:没写成过就补上,已经有了就 no-op。

def next_card_version(basket_id: int, *, db_path: Optional[Path] = None) -> int:
    """该篮子**下一个可用**的卡版本号(= 现有最大 version + 1,无卡则 1)。

    ⚠ **只给 D+1 追加新版本的调用方用**,不要拿它当 `save_basket_card` 的默认值 ——
    默认自增会让「同一 D0 内重跑补 version=1」变成"每重跑一次多一版",那不是幂等
    补写、而是把冻结件写成流水账。D0 路径请显式传 `version=1`。
    """
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(version) FROM basket_cards WHERE basket_id=?", (int(basket_id),)
        ).fetchone()
    return int(row[0]) + 1 if row is not None and row[0] is not None else 1


def _card_json_text(card_json: Any) -> str:
    """卡正文序列化。`sort_keys=True` 是**刻意**的:冻结件要能逐字节比对(同日重跑
    时用它判"库里那份与本次算出的是不是同一张卡"),键序不稳就没法比。已经是字符串
    的原样透传(调用方自己序列化过的情形)。"""
    if isinstance(card_json, str):
        return card_json
    return json.dumps(card_json, ensure_ascii=False, sort_keys=True)


def save_basket_card(
    basket_id: int,
    card_json: Any,
    *,
    version: int = 1,
    stop_pct: Optional[float] = None,
    take_profit_retrace: Optional[float] = None,
    charter_version: Optional[str] = None,
    pack_version: Optional[str] = None,
    engine_api_version: Optional[int] = None,
    db_path: Optional[Path] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """**事务 2**:落一张篮子卡(`basket_cards`,⑦ 唯一写的表)。

    **冻结语义**:`INSERT OR IGNORE` —— 同 `(basket_id, version)` 二次写 = **拒**
    (no-op,绝不覆盖既有行)。若本次算出的卡与库里已冻结的那份不同 → WARNING 落
    日志 + `frozen_conflicts` 原样带出给报告层如实披露,**不静默、也不覆盖**
    (「藏起来不是诚实」,裁定 B 条)。

    `conn`:给了就复用调用方的 connection(不自开事务、不 commit),供批量落卡时把
    多张卡并进一个事务;不给就自开自提交。签名其余部分与 fail-loud 语义与本模块
    另两个入口一致。

    返回 `{"cards_inserted", "cards_existing", "version", "frozen_conflicts"}`。
    """
    version_i = int(version)
    if version_i < 1:
        raise ValueError(f"save_basket_card:version 必须 ≥1(1 = D0 原判),实得 {version!r}")
    text = _card_json_text(card_json)
    now = _now()
    row = (
        int(basket_id), version_i, text,
        None if stop_pct is None else float(stop_pct),
        None if take_profit_retrace is None else float(take_profit_retrace),
        charter_version, pack_version,
        None if engine_api_version is None else int(engine_api_version),
        now,
    )
    if conn is not None:
        return _save_basket_card_on_conn(conn, row)
    init_schema(db_path)
    with connection(db_path) as own:
        return _save_basket_card_on_conn(own, row)


def _save_basket_card_on_conn(conn: sqlite3.Connection, row: Tuple[Any, ...]) -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "cards_inserted": 0, "cards_existing": 0, "version": row[1], "frozen_conflicts": [],
    }
    cur = conn.execute(
        f"INSERT OR IGNORE INTO basket_cards ({_BASKET_CARD_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?)",
        row,
    )
    if cur.rowcount:
        stats["cards_inserted"] = 1
        return stats

    stats["cards_existing"] = 1
    logger.warning(
        "[basket_card] basket_cards 已存在 (basket_id=%s, version=%s) 的冻结行,"
        "幂等跳过、不覆盖既有行。", row[0], row[1],
    )
    frozen = conn.execute(
        "SELECT card_json FROM basket_cards WHERE basket_id=? AND version=?", (row[0], row[1])
    ).fetchone()
    if frozen is not None and frozen[0] != row[2]:
        conflict = (
            f"basket_cards[basket_id={row[0]}/version={row[1]}] 已冻结的卡正文与本次算出的不一致,"
            f"本次结果未采纳(冻结 {len(frozen[0])} 字节 vs 本次 {len(row[2])} 字节)"
        )
        stats["frozen_conflicts"].append(conflict)
        logger.warning("[basket_card] %s", conflict)
    return stats


def save_basket_cards(
    cards_by_basket_id: Mapping[int, Any],
    *,
    version: int = 1,
    meta_by_basket_id: Optional[Mapping[int, Mapping[str, Any]]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """一批卡同事务落地(**仍是事务 2**,只是一次开一个连接把这批写完 —— 与事务 1
    仍然分开)。`meta_by_basket_id` 逐篮给口径指纹五项;缺的按 `None` 落。

    ⚠ 与事务 1 的「一起成功或一起回滚」不同,本函数**不追求整批原子性**也不需要:
    卡与卡之间没有引用关系,「有几张卡、缺几张卡」本就是合法中间态(裁定 C 条)。
    这里合并连接纯粹是为了少开几次库。
    """
    stats: Dict[str, Any] = {"cards_inserted": 0, "cards_existing": 0, "frozen_conflicts": []}
    if not cards_by_basket_id:
        return stats
    init_schema(db_path)
    with connection(db_path) as conn:
        for basket_id, card in cards_by_basket_id.items():
            meta = dict((meta_by_basket_id or {}).get(basket_id) or {})
            one = save_basket_card(
                int(basket_id), card, version=version,
                stop_pct=meta.get("stop_pct"),
                take_profit_retrace=meta.get("take_profit_retrace"),
                charter_version=meta.get("charter_version"),
                pack_version=meta.get("pack_version"),
                engine_api_version=meta.get("engine_api_version"),
                conn=conn,
            )
            stats["cards_inserted"] += one["cards_inserted"]
            stats["cards_existing"] += one["cards_existing"]
            stats["frozen_conflicts"].extend(one["frozen_conflicts"])
    return stats


def load_basket_card(
    basket_id: int, *, version: Optional[int] = None, db_path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """读一张卡(`version=None` → **最新版本**)。无卡 → `None`,由调用方表达
    「有篮子、无卡」这个合法中间态(契约侧 `GET /baskets/{id}/card` 返 404 +
    reason `card_not_ready`,⑭-B 落地)。**⛔ 不许因为没卡就把篮子从报告里抹掉。**"""
    init_schema(db_path)
    sql = (
        f"SELECT id, {_BASKET_CARD_COLUMNS} FROM basket_cards WHERE basket_id=?"
        + (" AND version=?" if version is not None else "")
        + " ORDER BY version DESC LIMIT 1"
    )
    args: Tuple[Any, ...] = (int(basket_id),) if version is None else (int(basket_id), int(version))
    with connection(db_path) as conn:
        row = conn.execute(sql, args).fetchone()
    if row is None:
        return None
    keys = ["id"] + [c.strip() for c in _BASKET_CARD_COLUMNS.split(",")]
    out = dict(zip(keys, row))
    try:
        out["card"] = json.loads(out["card_json"])
    except (json.JSONDecodeError, TypeError):
        logger.warning("[basket_card] basket_id=%s version=%s 的 card_json 解不出,原样返回字符串",
                       out.get("basket_id"), out.get("version"))
        out["card"] = None
    return out


# ══════════════════════════════════════════════════════════════════════════
# 读侧:某个 D0 的篮子 + 成员(V2-⑧ D+1 验证 / 关注池要用)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class BasketRef:
    """一个已冻结篮子的**只读引用**(⑧ 关注池与验证状态机的输入)。

    ⚠ 只装「谁、哪一档、有哪些成员」—— **不装卡**(卡走 `load_basket_card`,
    「有篮子、无卡」是合法中间态,⑧ 见到 `None` 要落 `unclear` + `no_card`,
    ⛔ 不许拿默认条件顶上)。
    """

    basket_id: int
    trade_date: str          # D0(YYYYMMDD)
    basket_key: str
    name: str
    tier: int
    member_codes: Tuple[str, ...]


def load_baskets_for_date(
    trade_date: Any,
    *,
    tiers: Optional[Sequence[int]] = None,
    db_path: Optional[Path] = None,
) -> List[BasketRef]:
    """读某个 D0 已落库的篮子(按 `tier` 升序、`basket_key` 升序,**确定性排序**)。

    `tiers=(1, 2)` → 只取 T1/T2(⑧ 关注池的口径:容量有限,盘中只盯前两档);
    `None` → 全部(⑧ 的 EOD 那一拍要判全部篮子,EOD 面板是全市场、无拉价成本)。
    无篮子 → 空列表(合法状态,如当日引擎没跑或今日无篮子达到定档标准)。
    """
    day = trade_date if isinstance(trade_date, str) else trade_date.strftime("%Y%m%d")
    sql = "SELECT id, trade_date, basket_key, name, tier FROM baskets WHERE trade_date=?"
    args: List[Any] = [day]
    if tiers:
        sql += " AND tier IN (" + ",".join("?" * len(tiers)) + ")"
        args.extend(int(t) for t in tiers)
    sql += " ORDER BY tier, basket_key"
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
        out: List[BasketRef] = []
        for r in rows:
            members = conn.execute(
                "SELECT ts_code FROM basket_members WHERE basket_id=? ORDER BY ts_code",
                (int(r[0]),),
            ).fetchall()
            out.append(BasketRef(
                basket_id=int(r[0]), trade_date=str(r[1]), basket_key=str(r[2]),
                name=str(r[3]), tier=int(r[4]),
                member_codes=tuple(str(m[0]) for m in members),
            ))
    return out


__all__ = [
    "save_baskets",
    "save_tier_history",
    "save_tier_decision",
    "next_card_version",
    "save_basket_card",
    "save_basket_cards",
    "load_basket_card",
    "BasketRef",
    "load_baskets_for_date",
]
