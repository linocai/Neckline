"""竞价两张表的读写单一通道(V2.3.3-②;`auction_reports` / `auction_verdicts`)。

🔴 **两阶段写**(§3.13-B):
    1. **9:26 机械段先落库** —— `INSERT OR IGNORE`,`llm_stage='pending'`、
       `verdict='pending_explanation'`。**必须在 LLM 之前**,这是「LLM 暂时不可用时,
       机械层继续输出数据报告和明确失效警报」(K8 §二十)的**结构性保证**,不是"顺序
       上先写一下"。
    2. **LLM 回来 / 9:29 硬截止之后只 UPDATE 一小撮列** —— 见下面两个列白名单。
       🔴 **机械列永不 UPDATE**;白名单本身有守门单测
       (`tests/test_v233_auction_guards.py`:扫 `neckline/**` 与 `scripts/**` 里所有
       UPDATE 语句的列集合,并对**非字面量 SQL** 与 `DELETE FROM` 一律报红)。

**为什么两张表刻意不进 `_APPEND_ONLY_TABLES`**:它们是**有生命周期的对象**,与
`trade_clock` 同族,不是 `basket_cards` / `selection_clock` 那种冻结件。⚠ 那条例外
的**代偿闸门**就是上面这条列白名单守门 —— **缺了它,这一层就是个后门**。

⛔ **本模块不写任何正式结论表**:`baskets` / `tier_history` / `basket_cards` /
`selection_clock` 一个都不碰(守门单测 SQL 双向扫)。

⚠ **幂等**:`finalize_*` 一律带 `WHERE llm_stage='pending'` —— 与「`llm.explain()` 的
签名里根本没有 store 句柄(工作线程只写内存 `box`,**够不着**库)」构成**双保险**,
让 9:29 之后才回来的那条结论**写不进去**(§五 〇b-5:9:35 才落进去的结论会假装是
9:29 之前给出的)。⚠ 施工图 ④-B 提到的 `deadline_passed` 标志位**没有落地、也不该落地**
——现有这两条不依赖任何人记得检查一个布尔(复审 🔵-1)。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.auction import LLM_PENDING, VERDICT_PENDING_EXPLANATION
from neckline.db import connection, init_schema

logger = logging.getLogger(__name__)

#: 🔴 第二阶段允许 UPDATE 的列(市场级)。**这条清单就是「机械列永不改」的物理落点**,
#: 加一列必须连"为什么它属于 LLM 段"一起想清楚。
LLM_UPDATABLE_REPORT_COLUMNS: Tuple[str, ...] = (
    "market_overview", "anchors_note", "risks_json", "manual_note_attached",
    "llm_stage", "llm_elapsed_ms", "notes_json", "updated_at",
)
#: 🔴 第二阶段允许 UPDATE 的列(篮子级)。
LLM_UPDATABLE_VERDICT_COLUMNS: Tuple[str, ...] = (
    "verdict", "verdict_raw", "clamped_by", "reasons_json", "llm_fields_json",
    "manual_note_attached", "llm_stage", "updated_at",
)

#: ⚠ **V2.4.0 P2.4 新增的四列全部是机械冻结列**(`quote_quality_json` +
#: `critical_data_quality` / `context_data_quality` / `quality_detail_json`)——
#: 它们只在**第一次机械落库**时写,⛔ 一个都不许进上面两条 LLM 白名单。
_REPORT_COLUMNS: Tuple[str, ...] = (
    "trade_date", "d0_date", "source", "captured_at", "requested_codes", "fetched_codes",
    "missing_codes_json", "conflict_codes_json", "data_quality", "index_gaps_json",
    "market_anchors_json", "market_overview", "anchors_note", "risks_json",
    "manual_note_attached",
    "llm_stage", "llm_elapsed_ms", "baskets_covered", "notes_json",
    "quote_quality_json",
    # 🔴 裁定 ①(2026-08-12):独立观察池的账 + 观察范围自述。**机械冻结列**。
    "observation_json",
    "created_at", "updated_at",
)
_VERDICT_COLUMNS: Tuple[str, ...] = (
    "basket_id", "trade_date", "d0_date", "basket_key", "name", "covered_tier",
    "engine_code", "engine_version", "skeleton_version", "regime_at_d0", "data_quality",
    "members_json", "sector_sync_json", "rel_strength_json", "history_json",
    "hit_invalidation_json", "plan_consistency_json", "verdict", "verdict_raw",
    "clamped_by", "reasons_json", "llm_fields_json", "manual_note_attached",
    "llm_stage",
    "critical_data_quality", "context_data_quality", "quality_detail_json",
    "created_at", "updated_at",
)


def _d(d: Any) -> str:
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    return str(d)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


# ══════════════════════════════════════════════════════════════════════════
# 第一阶段:机械段落库(INSERT OR IGNORE,幂等)
# ══════════════════════════════════════════════════════════════════════════

def save_mechanical(mech: Any, *, db_path: Optional[Path] = None) -> bool:
    """把机械层产物落成 `auction_reports` 一行 + `auction_verdicts` 每篮一行。

    返回 `True` = 本次是**新落的行**;`False` = 当日已有行(同一天重跑 → 零新行、
    机械列逐位不变,`INSERT OR IGNORE` 的幂等)。
    """
    init_schema(db_path)
    td, d0 = _d(mech.trade_date), _d(mech.d0_date)
    now = _now()
    m = mech.market
    with connection(db_path) as conn:
        cur = conn.execute(
            f"INSERT OR IGNORE INTO auction_reports ({', '.join(_REPORT_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_REPORT_COLUMNS))})",
            (
                td, d0, m.source, m.captured_at, int(m.requested_codes), int(m.fetched_codes),
                _j(list(m.missing_codes)), _j(list(m.conflict_codes)), m.data_quality,
                _j(m.index_gaps), _j(m.anchors),
                None,                       # market_overview:LLM 段,NULL = 未生成
                None,                       # anchors_note:同上
                _j(list(m.risks)), 0,
                LLM_PENDING, None, len(mech.baskets), _j(list(mech.notes)),
                # 🔴 P2.4:逐票七项校验 + **两源原始读数**(K8:两个来源的原始读数全部留存)。
                # ⚠ 老行是 NULL = 「这一版还没有逐票核验这个概念」,⛔ 不是「都合格」。
                _j(dict(getattr(m, "quote_quality", None) or {})),
                # 🔴 裁定 ①:观察范围是**当天那一份**,不落库次日就说不准了。
                # ⚠ 老行 NULL = 「这一版还没有独立观察池」,⛔ 不是「范围正常」。
                _j(dict(getattr(m, "observation", None) or {})),
                now, now,
            ),
        )
        inserted = cur.rowcount > 0
        for b in mech.baskets:
            conn.execute(
                f"INSERT OR IGNORE INTO auction_verdicts ({', '.join(_VERDICT_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_VERDICT_COLUMNS))})",
                (
                    int(b.basket_id), td, d0, b.basket_key, b.name, int(b.covered_tier),
                    b.engine_code, b.engine_version, b.skeleton_version or "", b.regime_at_d0,
                    b.data_quality,
                    _j([r.to_dict() for r in b.members]), _j(b.sector_sync),
                    _j(b.rel_strength), _j(b.history), _j(list(b.hit_invalidation_codes)),
                    _j(b.plan_consistency),
                    # 🔴 机械段落库时结论恒为「待解释」——「还没解释」与「解释过了是中性」
                    # 必须分得开(K8 §二十 原文:LLM 不可用时其余结论标记为"待解释")。
                    VERDICT_PENDING_EXPLANATION, None, None, _j([]), _j({}), 0,
                    LLM_PENDING,
                    # 🔴 P2.4 分域质量(机械冻结列)。⚠ `data_quality` 那一列自
                    # V2.4.0 起**就是** `critical_quality` —— 这里显式再落一份的
                    # 唯一理由是:老行那一列是**整体**质量,靠这一列为 NULL 才分得开。
                    getattr(b, "critical_quality", None),
                    getattr(b, "context_quality", None),
                    _j(dict(getattr(b, "quality_detail", None) or {})),
                    now, now,
                ),
            )
    return inserted


# ══════════════════════════════════════════════════════════════════════════
# 第二阶段:LLM 段回填(受限 UPDATE + 幂等 WHERE llm_stage='pending')
# ══════════════════════════════════════════════════════════════════════════

def finalize_report(
    trade_date: Any,
    *,
    llm_stage: str,
    market_overview: Optional[str] = None,
    anchors_note: Optional[str] = None,
    risks: Optional[Sequence[Mapping[str, Any]]] = None,
    manual_note_attached: bool = False,
    llm_elapsed_ms: Optional[int] = None,
    notes: Optional[Sequence[str]] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """市场级第二阶段写。**只 UPDATE `LLM_UPDATABLE_REPORT_COLUMNS`。**

    `WHERE llm_stage='pending'` 是幂等闸:9:29 硬截止后 `pipeline` 已把本行结案成
    `pending_explanation`,那条迟到的流式调用即便回来了也**改不动任何一个字**
    (§五 〇b-5)。返回是否真的改到了行。

    ⚠ `risks=None` → **保留机械段那份**(⛔ 不拿空数组把「命中失效位」这条覆盖掉 ——
    那正是 ②-G 独立警报通道要防的事)。
    """
    init_schema(db_path)
    # ⚠ **SQL 必须是静态字面量**:动态拼 `SET` 子句会让「机械列永不 UPDATE」那条守门
    # **失明**(它按 AST 取字符串字面量再解析列集合)。所以这里把白名单七列一次写全,
    # 「本次不改」用 `COALESCE(?, 原值)` 表达 —— ⛔ 别改回按需拼接。
    with connection(db_path) as conn:
        cur = conn.execute(
            "UPDATE auction_reports SET "
            "market_overview=COALESCE(?, market_overview), "
            "anchors_note=COALESCE(?, anchors_note), "
            "risks_json=COALESCE(?, risks_json), "
            "notes_json=COALESCE(?, notes_json), "
            "manual_note_attached=?, llm_stage=?, llm_elapsed_ms=?, updated_at=? "
            f"WHERE trade_date=? AND llm_stage='{LLM_PENDING}'",
            (market_overview, anchors_note,
             None if risks is None else _j(list(risks)),
             None if notes is None else _j(list(notes)),
             int(bool(manual_note_attached)), llm_stage, llm_elapsed_ms, _now(),
             _d(trade_date)),
        )
        return cur.rowcount > 0


def finalize_verdict(
    basket_id: int,
    *,
    verdict: str,
    llm_stage: str,
    verdict_raw: Optional[str] = None,
    clamped_by: Optional[str] = None,
    reasons: Optional[Sequence[str]] = None,
    llm_fields: Optional[Mapping[str, Any]] = None,
    manual_note_attached: bool = False,
    db_path: Optional[Path] = None,
) -> bool:
    """篮子级第二阶段写。**只 UPDATE `LLM_UPDATABLE_VERDICT_COLUMNS`。**

    🔴 `verdict_raw` 永远存**夹逼前**模型原话的三值,`verdict` 存夹逼后 —— 两者不同
    的那些行**就是**「模型说了什么 vs 系统最终讲了什么」的账,⛔ 不许只存一个。
    """
    init_schema(db_path)
    with connection(db_path) as conn:
        cur = conn.execute(
            "UPDATE auction_verdicts SET verdict=?, verdict_raw=?, clamped_by=?, "
            "reasons_json=?, llm_fields_json=?, manual_note_attached=?, llm_stage=?, "
            "updated_at=? "
            f"WHERE basket_id=? AND llm_stage='{LLM_PENDING}'",
            (verdict, verdict_raw, clamped_by, _j(list(reasons or [])),
             _j(dict(llm_fields or {})), int(bool(manual_note_attached)), llm_stage,
             _now(), int(basket_id)),
        )
        return cur.rowcount > 0


# ══════════════════════════════════════════════════════════════════════════
# 读
# ══════════════════════════════════════════════════════════════════════════

_JSON_REPORT_COLS = ("missing_codes_json", "conflict_codes_json", "index_gaps_json",
                     "market_anchors_json", "risks_json", "notes_json",
                     "quote_quality_json", "observation_json")
_JSON_VERDICT_COLS = ("members_json", "sector_sync_json", "rel_strength_json", "history_json",
                      "hit_invalidation_json", "plan_consistency_json", "reasons_json",
                      "llm_fields_json", "quality_detail_json")


def _row_to_dict(cols: Sequence[str], row: Sequence[Any], json_cols: Sequence[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(zip(cols, row))
    for c in json_cols:
        if c in out:
            try:
                out[c] = json.loads(out[c] or "null")
            except (TypeError, ValueError):
                # 🔴 「读不出」是**独立第三态**,与「还没生成」必须分开(V2 B1 定案):
                # 留一个显式标记,读侧据此给 500 `auction_corrupt`,⛔ 不静默当空。
                out[c] = None
                out.setdefault("_corrupt_columns", []).append(c)
    return out


def load_report(trade_date: Any, *, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """当日市场级报告。**无行 = 竞价层没跑过**(⛔ 与"跑过了但没有篮子"分开:
    后者是有行 + `baskets_covered=0`)。"""
    init_schema(db_path)
    cols = ("id",) + _REPORT_COLUMNS
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {', '.join(cols)} FROM auction_reports WHERE trade_date=?",
            (_d(trade_date),),
        ).fetchone()
    return None if row is None else _row_to_dict(cols, row, _JSON_REPORT_COLS)


def load_verdicts(trade_date: Any, *, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    init_schema(db_path)
    cols = ("id",) + _VERDICT_COLUMNS
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM auction_verdicts WHERE trade_date=? "
            f"ORDER BY covered_tier, basket_key",
            (_d(trade_date),),
        ).fetchall()
    return [_row_to_dict(cols, r, _JSON_VERDICT_COLS) for r in rows]


def load_verdict_for_basket(
    basket_id: int, *, db_path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """按篮子点查(⑥ 事后复盘的取数入口:逐篮点查,量级几篮,⛔ 零扫描)。"""
    init_schema(db_path)
    cols = ("id",) + _VERDICT_COLUMNS
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {', '.join(cols)} FROM auction_verdicts WHERE basket_id=?",
            (int(basket_id),),
        ).fetchone()
    return None if row is None else _row_to_dict(cols, row, _JSON_VERDICT_COLS)


__all__ = [
    "LLM_UPDATABLE_REPORT_COLUMNS", "LLM_UPDATABLE_VERDICT_COLUMNS",
    "save_mechanical", "finalize_report", "finalize_verdict",
    "load_report", "load_verdicts", "load_verdict_for_basket",
]
