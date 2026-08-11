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

⚠ **`INSERT OR IGNORE` 只冻「同一行」,冻不住「聚合长子行」**(契约线审计 🔴 R1,
2026-08-03 修复):`baskets` 是聚合根、`basket_members` 是它的子行,行级 UNIQUE 挡得住
「同一个成员写第二遍」,挡不住「本次新算出来的成员码插进已冻结的篮」。故
`_save_baskets_on_conn` 里**篮子行已存在 = 该篮成员整段不写**,只比对成员集入
`frozen_conflicts`。凡是「父行冻结 + 子行另表」的结构(日后若再加同型表)一律照此办理
—— 冻结的是那一批**集合**,不是集合里的某一行。

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
    "engine_api_version, charter_version, via, evidence_status, created_at, "
    "engine_code, engine_version, skeleton_version"
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


def _warn_conflicts(tag: str, conflicts: Sequence[str]) -> None:
    """冻结冲突的统一披露口(**三个入口共用同一句话**,🔵 B1)。文案里的「未采纳」
    是有前提的:🔴 R1 修好之后,冻结篮的成员确实一个都没被改写,这句才成立 ——
    改写侧行为时先回头看这句话还对不对,别让日志替代码撒谎。"""
    if not conflicts:
        return
    logger.warning(
        "[%s] 今日已有冻结行,本次重算结果**未采纳**(差异 %d 处):%s",
        tag, len(conflicts), "; ".join(conflicts),
    )


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
            f"INSERT OR IGNORE INTO baskets ({_BASKET_COLUMNS}) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                b.trade_date, b.basket_key, b.name, b.driver, b.driver_kind,
                tier, b.pack_version,
                b.engine_api_version, b.charter_version, via, b.evidence_status, now,
                # V2.2-③-E(裁定 #9):篮子级引擎归属三件套(gates.py 对拍后回填;
                # 预置/测试替身没有这些属性 → NULL,与老行同语义「无归属概念」)。
                getattr(b, "engine_code", None), getattr(b, "engine_version", None),
                getattr(b, "skeleton_version", None),
            ),
        )
        basket_is_new = bool(cur.rowcount)
        if basket_is_new:
            stats["baskets_inserted"] += 1
        else:
            stats["baskets_existing"] += 1
            logger.warning(
                "[aggregate] baskets 已存在同日同键行(%s/%s),幂等跳过、不覆盖既有行;"
                "**该篮成员写入整段跳过**(冻结篮不接受追加成员,只比对留痕)。",
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
        computed_members = {m.ts_code for m in b.members}
        if basket_is_new:
            for m in b.members:
                mc = conn.execute(
                    f"INSERT OR IGNORE INTO basket_members ({_MEMBER_COLUMNS}) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (basket_id, m.ts_code, m.role_llm, m.role_mech, int(m.role_conflict),
                     m.reason, int(m.is_primary), now),
                )
                stats["members_inserted"] += mc.rowcount or 0
            continue
        # ⚠ **冻结篮:一个成员都不写**(契约线审计 🔴 R1,2026-08-03)。原实现只让
        # `baskets` 行幂等跳过,成员照走 `INSERT OR IGNORE` —— 既有成员被
        # `UNIQUE(basket_id, ts_code)` 挡下,**本次新算出来的成员码却直接插进了冻结篮**,
        # 而同一批日志正在说「本次重算结果未采纳」。`INSERT OR IGNORE` 只保证「同一行
        # 不写第二遍」,它**不保证「这个聚合不长出新子行」** —— 冻结的是**成员集合**,
        # 不是单行。泄漏进来的码会以「D0 判断的一员」身份进入 ⑧ 关注池 / ⑨ 复盘归因 /
        # ⑫ 画像对照 / ⑩ 开仓来源关联,而它从未被 D0 定档时采纳,卡上的成员节(冻结
        # card_json)与表里的成员集也就从此对不上。
        frozen_members = {
            r[0] for r in conn.execute(
                "SELECT ts_code FROM basket_members WHERE basket_id=?", (basket_id,)
            ).fetchall()
        }
        if frozen_members != computed_members:
            conflicts.append(
                f"basket_members[{b.trade_date}/{b.basket_key}] 冻结成员集 "
                f"{sorted(frozen_members)} ≠ 本次算出 {sorted(computed_members)}"
                f"(本次结果未采纳,冻结成员集原样保留)"
            )
    return stats, ids, conflicts


def save_baskets(
    result: "AggregateResult",
    *,
    tier_by_basket_key: Mapping[str, int],
    db_path: Optional[Path] = None,
    via: str = "auto",
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
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
    **写入行为与搬迁前逐字节等价**。

    返回 stats;**含 `frozen_conflicts`**(契约线审计 🔵 B1,2026-08-03):本函数是
    导出的公开入口,以前把冲突直接丢弃 —— 走这条路的调用方连「成员集与冻结的不一样」
    都看不见,只剩一句泛化的「幂等跳过」。现在与 `save_tier_decision` 同口径:打
    WARNING + 原样带出。⚠ 正常路径仍应走 `save_tier_decision`(三表同事务)。
    """
    _validate_tiers(result, tier_by_basket_key)

    stats: Dict[str, Any] = {
        "baskets_inserted": 0, "baskets_existing": 0, "members_inserted": 0,
        "frozen_conflicts": [],
    }
    if not result.baskets:
        return stats

    now = _now()
    if conn is not None:
        wstats, _ids, conflicts = _save_baskets_on_conn(
            conn, result, tier_by_basket_key, via=via, now=now
        )
    else:
        init_schema(db_path)
        with connection(db_path) as own:
            wstats, _ids, conflicts = _save_baskets_on_conn(
                own, result, tier_by_basket_key, via=via, now=now
            )
    stats.update(wstats)
    stats["frozen_conflicts"] = conflicts
    _warn_conflicts("aggregate", conflicts)
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
) -> Dict[str, Any]:
    """落 `tier_history`(每日一行幂等,`UNIQUE(trade_date, basket_id)`)。

    `entries` 每条是一个 mapping,必须带 `basket_key` 以及
    `_TIER_HISTORY_REQUIRED` 全部键;`basket_id` 由 `basket_id_by_key` 解析 ——
    **查不到就 fail loud**(篮子还没落库就写它的定档留痕 = 次序错了,不许静默跳过)。

    ⚠ 正常路径应该走 `save_tier_decision()`(三表同事务);本函数单独暴露只为
    单测与补写场景。返回 stats **含 `frozen_conflicts`**(🔵 B1,同 `save_baskets`:
    公开入口不许把冲突吞掉)。
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
    stats: Dict[str, Any] = {
        "tier_history_inserted": 0, "tier_history_existing": 0, "frozen_conflicts": [],
    }
    if not rows:
        return stats
    if conn is not None:
        wstats, conflicts = _save_tier_history_on_conn(conn, rows)
    else:
        init_schema(db_path)
        with connection(db_path) as own:
            wstats, conflicts = _save_tier_history_on_conn(own, rows)
    stats.update(wstats)
    stats["frozen_conflicts"] = conflicts
    _warn_conflicts("tier", conflicts)
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
    _warn_conflicts("tier", stats["frozen_conflicts"])
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


# 判「这张卡还读得出来吗」的**内容键**(B1 裁定「顶层必需键缺失」这一半的落地)。
#
# **判据 = 三者至少有其一**(不是"三者都要有"),这是刻意的:
#   ① **各消费方要的不是同一批键** —— ⑧ 篮子验证只读两份 spec(`basket_verify` 的
#      设计如此,卡面其余项与它的判定无关)、⑩ 开仓继承只读 `members` 里的区间、
#      客户端要全套。要求"都得有"会把这些**合法的局部卡**判成损坏。
#   ② **误判代价不对称** —— 判错成损坏 = 端点 500、用户看不到一张其实好好的卡,而且
#      这个错还不可自愈;判漏 = 卡上少几个字段照常渲染。故判据取最保守的一侧:
#      **顶层一个内容键都没有 = 这压根不是一张卡**(典型是被别的东西覆写、或半截写入),
#      只要还有一个内容键在,就交给各消费方按自己的口径处理。
# ⛔ 刻意**不收**身份键(`basket_key`/`trade_date`/`spec_version`)与展示键:身份信息
#    行上本来就有(`basket_cards.basket_id`/`version` + join `baskets`),而外部预置的卡
#    (⑯-F `preseed_baskets.py` 灌的那种)可能只带内容不带身份键 —— 那不是数据事故。
# ⛔ 更别往这里加"新版本才出现"的键 —— 卡是冻结件,老卡永远不会补新键,那等于把合法
#    老卡判成坏卡(同 CLAUDE.md「落库快照两类论」第二类的处置纪律)。
CARD_CONTENT_KEYS: Tuple[str, ...] = (
    "members", "verification_spec", "invalidation_spec",
)


def _decode_card_json(
    raw: Any, *, basket_id: Any = None, version: Any = None
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """`card_json` → `(卡, 损坏原因)`。**两者恰有一个非 None**:读得出且键齐 →
    `(卡, None)`;否则 `(None, 人读原因)` 并打 **ERROR**(冻结件损坏是数据事故)。
    ⛔ 不做任何补全 / 跳过坏字段(读侧糊过去 = 藏真数据)。"""
    try:
        card = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        reason = "card_json 解不出(不是合法 JSON)"
    else:
        if not isinstance(card, dict):
            reason = f"card_json 顶层不是对象(实为 {type(card).__name__})"
        elif not any(k in card for k in CARD_CONTENT_KEYS):
            reason = ("card_json 顶层一个内容键都没有(期望至少有 "
                      + " / ".join(CARD_CONTENT_KEYS) + " 之一)")
        else:
            return card, None
    logger.error(
        "[basket_card] basket_id=%s version=%s 的冻结卡损坏(%s)—— 卡是冻结件、"
        "INSERT OR IGNORE 永不覆盖,**这张卡不会自己好**,需要人排查。"
        "⛔ 不当作『卡还没生成』处理(那会让客户端永远等一张永远不来的卡)。",
        basket_id, version, reason,
    )
    return None, reason


def load_basket_card(
    basket_id: int, *, version: Optional[int] = None, db_path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """读一张卡(`version=None` → **最新版本**)。无卡 → `None`,由调用方表达
    「有篮子、无卡」这个合法中间态(契约侧 `GET /baskets/{id}/card` 返 404 +
    reason `card_not_ready`,⑭-B 落地)。**⛔ 不许因为没卡就把篮子从报告里抹掉。**

    **「有行但读不出」是第三态,与「没有行」分得开**(2026-08-04 planner 裁定 B1,
    小审 🔵 B-3):`card_json` 解不出、或顶层必需键缺失 → 返回的 dict 里
    `card=None` **且** `card_corrupt=True` + `card_corrupt_reason=<人读原因>`,
    调用方据此走 `card_corrupt`(端点 = **500**,⛔ 不是 404 家族)。
    理由(裁定原文压缩版):卡是冻结件、`INSERT OR IGNORE` 永不覆盖 → **坏了就是
    永久坏的**;当成 `card_not_ready` 处理的客户端会永远重试、界面永远显示「卡还没
    生成」而那张卡这辈子不会来 = 静默永久失败。
    ⛔ **不许在读侧"重建"或跳过坏字段糊过去**(藏真数据不是诚实);检测到即打
    **ERROR**(不是 WARNING)—— 冻结件损坏是真数据事故,必须有人看见。"""
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
    out["card"], out["card_corrupt_reason"] = _decode_card_json(
        out.get("card_json"), basket_id=out.get("basket_id"), version=out.get("version"))
    out["card_corrupt"] = out["card_corrupt_reason"] is not None
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
    # V2.2-③-E:篮子级引擎归属(成员继承;老行/K8 前的篮子 = None,如实)。
    engine_code: Optional[str] = None
    engine_version: Optional[str] = None
    skeleton_version: Optional[str] = None


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
    sql = ("SELECT id, trade_date, basket_key, name, tier, engine_code, engine_version, "
           "skeleton_version FROM baskets WHERE trade_date=?")
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
                engine_code=r[5], engine_version=r[6], skeleton_version=r[7],
            ))
    return out


def load_basket(basket_id: int, *, db_path: Optional[Path] = None) -> Optional[BasketRef]:
    """按 id 读一个篮子(**不含卡**,同 `BasketRef` 的既定边界)。

    `None` = **这个篮子不存在**(`basket_not_found`)—— 与「篮子在、卡没生成」
    (`card_not_ready`,`load_basket_card` 返 `None`)是**两件不同的事**,
    ⛔ 调用方不许把两者合并成一个 404 reason:前者说系统丢了篮子,后者说卡还没做完。
    """
    init_schema(db_path)
    with connection(db_path) as conn:
        r = conn.execute(
            "SELECT id, trade_date, basket_key, name, tier, engine_code, engine_version, "
            "skeleton_version FROM baskets WHERE id=?",
            (int(basket_id),),
        ).fetchone()
        if r is None:
            return None
        members = conn.execute(
            "SELECT ts_code FROM basket_members WHERE basket_id=? ORDER BY ts_code",
            (int(r[0]),),
        ).fetchall()
    return BasketRef(
        basket_id=int(r[0]), trade_date=str(r[1]), basket_key=str(r[2]),
        name=str(r[3]), tier=int(r[4]), member_codes=tuple(str(m[0]) for m in members),
        engine_code=r[5], engine_version=r[6], skeleton_version=r[7],
    )


def load_tier_history(basket_id: int, *, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """读某篮的 Tier 定档留痕(`tier_history` 一行,`UNIQUE(trade_date, basket_id)`
    使一篮一行)。`None` = 没有留痕 —— 只可能出现在事务 1 之外手工造数的库里。

    键保持 **snake_case**(领域形状),转 camel 是 API 层的事。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        r = conn.execute(
            "SELECT trade_date, tier, mech_score, mech_breakdown_json, rank_in_tier, "
            "rank_mech, llm_rank_delta, llm_reason, pack_version, created_at "
            "FROM tier_history WHERE basket_id=? ORDER BY trade_date DESC LIMIT 1",
            (int(basket_id),),
        ).fetchone()
    if r is None:
        return None
    try:
        breakdown = json.loads(r[3]) if r[3] else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("[basket_store] basket_id=%s 的 mech_breakdown_json 解不出,按空 dict 读回",
                       basket_id)
        breakdown = {}
    return {
        "trade_date": str(r[0]), "tier": int(r[1]), "mech_score": r[2],
        "mech_breakdown": breakdown, "rank_in_tier": r[4], "rank_mech": r[5],
        "llm_rank_delta": int(r[6] or 0), "llm_reason": r[7],
        "pack_version": r[8], "created_at": str(r[9]),
    }


# ══════════════════════════════════════════════════════════════════════════
# V2.3.2-②-B:OUT 一等状态(D0 **股票级** OUT 清单)
#
# K8 §六:候选状态只有 T1 / T2 / **OUT** 三个。③b 这一节自此装**两类行**,⛔ 不合并:
#   · **OUT 行**(关口未过 / 引擎缺席)—— 本表,**股票级**;
#   · **未定档行**(`capacity_overflow`)—— **不是 OUT**(K8 §八 的 OUT 适用状态里没有
#     "位置满"),维持篮子级,仍走 `basket_dropped_handoff` / `droppedBaskets`。
# 🔴 这条分类**直接决定 OUT 研究影子对照的样本域**:溢出篮**不进**影子对照。
#
# ⚠ **命名口径**(planner 定案,⛔ 不重开):`③b` 是报告的**节号**,`OUT` 是候选的
# **状态**,两者不是同义词。用户可见文案统一说「OUT」;既有内部标识与既有契约键
# `droppedBaskets` / `dropped*` **一律不改名**(客户端删/改键有两步淘汰纪律,为一次
# 纯改名走两个版本不值;且原因码一经落库不改)。
# ══════════════════════════════════════════════════════════════════════════

OUT_CANDIDATES_TABLE = "out_candidates"

_OUT_COLUMNS = (
    "d0_date, basket_key, ts_code, name, role, engine_code, engine_version, "
    "skeleton_version, out_gate, out_reason, out_detail, created_at"
)

# ⛔ **不是 OUT** 的出局原因码:位置满(K8 §八 的 OUT 四条适用状态里没有它)。
# ⚠ 这里刻意只列**一个**码而不是"列出哪些算 OUT" —— 新增的出局码默认**是** OUT,
# 漏登记的后果是"多进一条 OUT"(吵),反过来会让一票 OUT 静默消失(漏审)。
NON_OUT_REASONS = frozenset({"capacity_overflow"})


def is_out_reason(reason: str) -> bool:
    """该出局原因码是不是 K8 意义上的 OUT(⛔ 「位置满」不是)。"""
    return str(reason or "") not in NON_OUT_REASONS


def save_out_candidates(
    trade_date: Any,
    dropped: Sequence[Any],
    baskets_by_key: Mapping[str, Any],
    *,
    engine_by_key: Optional[Mapping[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> int:
    """把当日**股票级** OUT 清单落 `out_candidates`。返回新增行数。

    `dropped`:⑥ 的 `TierResult.dropped`(篮子级,带原因码 / 关 / 差多少);
    `baskets_by_key`:**对拍前**那批候选(`basket_key → BasketCandidate`)——
      🔴 必须是对拍前的,被关口除名的候选只活在那份里;传对拍后的会让 OUT 票全空。
    `engine_by_key`:`basket_key → gates.BasketGateSummary`(取引擎三件套;缺则留空)。

    **append-only + 幂等**:靠 `UNIQUE(d0_date, basket_key, ts_code)` 去重
    (`INSERT OR IGNORE`)—— 同日重跑不产生重复行,也**不覆盖**既有行
    (⛔ 零 UPDATE / 零 DELETE / 零 INSERT OR REPLACE:「上一次怎么判的」本身是审计对象)。
    """
    day = trade_date if isinstance(trade_date, str) else trade_date.strftime("%Y%m%d")
    now = _now()
    summaries = engine_by_key or {}
    rows: List[Tuple[Any, ...]] = []
    for d in dropped:
        reason = str(getattr(d, "reason", "") or "")
        if not is_out_reason(reason):
            continue                       # 位置满 → 未定档行,不是 OUT
        key = str(getattr(d, "basket_key", "") or "")
        basket = baskets_by_key.get(key)
        if basket is None:
            logger.warning(
                "[basket_store] OUT 清单:篮子 %r 在对拍前候选里查无此篮 —— "
                "该篮的成员本次记不下来(调用方可能传了对拍后的 result)", key)
            continue
        s = summaries.get(key)
        for m in getattr(basket, "members", ()) or ():
            rows.append((
                day, key, str(getattr(m, "ts_code", "") or ""),
                str(getattr(m, "name", "") or ""),
                getattr(m, "role_llm", None),
                getattr(s, "engine_code", None) if s is not None else None,
                getattr(s, "engine_version", None) if s is not None else None,
                getattr(s, "skeleton_version", None) if s is not None else None,
                getattr(d, "gate", None), reason, getattr(d, "gate_detail", None),
                now,
            ))
    if not rows:
        return 0
    init_schema(db_path)
    with connection(db_path) as conn:
        cur = conn.executemany(
            f"INSERT OR IGNORE INTO {OUT_CANDIDATES_TABLE} ({_OUT_COLUMNS}) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows,
        )
        return int(cur.rowcount or 0)


def load_out_candidates(
    trade_date: Any, *, db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """读某个 D0 的股票级 OUT 清单(确定性排序:`basket_key` → `ts_code`)。

    ⚠ **零行有两种相反成因**(今天真没有 OUT / 这一段压根没跑)—— 本函数只答"表里
    有什么",「跑没跑过」由调用方按 ③b 既有的三件套(`available` + 原因)自己披露,
    ⛔ 别把空列表当成"今天没有 OUT"(§七 P0-39 同一条纪律)。"""
    day = trade_date if isinstance(trade_date, str) else trade_date.strftime("%Y%m%d")
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT id, {_OUT_COLUMNS} FROM {OUT_CANDIDATES_TABLE} "
            "WHERE d0_date=? ORDER BY basket_key, ts_code", (day,),
        ).fetchall()
    keys = ["id"] + [c.strip() for c in _OUT_COLUMNS.split(",")]
    return [dict(zip(keys, r)) for r in rows]


__all__ = [
    "save_baskets",
    "save_tier_history",
    "save_tier_decision",
    "next_card_version",
    "load_basket",
    "load_tier_history",
    "save_basket_card",
    "save_basket_cards",
    "load_basket_card",
    "BasketRef",
    "load_baskets_for_date",
    "OUT_CANDIDATES_TABLE",
    "NON_OUT_REASONS",
    "is_out_reason",
    "save_out_candidates",
    "load_out_candidates",
]
