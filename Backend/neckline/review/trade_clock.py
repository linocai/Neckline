"""④-B **交易时钟**(plan §五 V2.2-④-B;需求原件 K8 §十四 / §十五)。

K8 原文两条硬规则::

    · **实际买入是启动交易时钟的唯一条件**
    · 已有持仓持续运行原交易时钟,**全部离场后结案**

**「唯一条件」怎么做成结构性事实,而不是一句自觉**
    本模块**不自己判断"该不该开时钟"**,它做的是**对账**:`sync_from_positions()`
    以 `positions` 表为唯一真相,「有一笔仓 ⇔ 有一个交易时钟」。
    ⛔ 刻意**不**在 `positions_entry.record_buy` 里挂钩子,理由两条:
      ① 买入有多条入口(API / CLI / 历史补录 / 幂等重放),挂钩子必然漏;对账天然全覆盖。
      ② 钩子失败要么吞掉(时钟静默缺席)、要么阻断开仓(用记账去挡真实交易)——
         两个都比"晚一点对上账"糟。对账是**幂等**的,漏跑一晚下一晚自动补齐。

**八项验证内容**(K8 §十四,`final_json` 顶层键,顺序即原文顺序)::

    ① entry_price_position       买入位置
    ② entry_conditions           入场条件
    ③ expected_path              预期路径
    ④ driver_structure_support   驱动、结构与支撑
    ⑤ target_zone_handling       目标区间处理
    ⑥ upside_efficiency          上涨效率变化
    ⑦ stop_after_invalidation    失效后的止损
    ⑧ thesis_consistency         原始理由的一致性

**⑥「上涨效率变化」的机械定义**(plan ④-B 原文的落地)
    = 持有期内**每日涨幅的 3 日滑动均值** ÷ **入场后前 3 日的同一个量**。
    ⚠ 「3 日」是这个**量的定义**(同 `scan/landing.py::LIFT_WINDOW_DAYS` 的性质),
    **不是一条及格线** —— 本模块只出这个比值,⛔ **不给它任何阈值、不下"效率下降了"
    的结论**(K8 §十三 明写「上涨效率下降 → **保留主观换股权**,不设机械规则」)。
    它 `source='engineering_v1'`,**只进复盘与展示**:⛔ 不触发任何持仓动作、
    ⛔ 不进推送、⛔ 不进任何在线判据。

**用户主观补充**(K8 §十五「用户只补充系统无法识别的主观原因,**每次一条简短说明**」)
    = `append_user_note()`,唯一写端点 `POST /api/v1/clocks/trade/{position_id}/note`。
    **只追加**(落 `trade_clock_events`,append-only 三律);⛔ **不做 LLM 代猜**
    (§七 **P3-28** 原文纪律一字不变 —— 代猜标签必须标来源、分列存放、不得进判定,
    本版连代猜本身都不做)。同时出**覆盖率指标** `note_coverage()`(「本期 N 笔中有
    M 笔带说明」),让稀疏程度可见 —— 那正是 P3-28 候选解法 ① 的落点。

**与交割单的关系:没有关系**(§七 **P3-38**)
    本表外键是 `position_id`(确定性),⛔ 与 `RoundTrip`(交割单里的一笔回合)
    **不做任何近似匹配**。那条纪律一字不变。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

TABLE = "trade_clock"
EVENTS_TABLE = "trade_clock_events"

#: `final_json` 的**形状**版本(形状变了就 bump)。
FINAL_SPEC_VERSION = "trade_clock_final_v1"

STATUS_RUNNING = "running"
STATUS_CLOSED = "closed"

# —— 事件种类(plan ④-B 定死的六个,⛔ 不自由发挥)——————————————————————
KIND_D1_OPEN = "d1_open"
KIND_DAILY_CHECK = "daily_check"
KIND_TARGET_ZONE = "target_zone"
KIND_INVALIDATION = "invalidation"
KIND_MANUAL_NOTE = "manual_note"
KIND_CLOSE = "close"
EVENT_KINDS: Tuple[str, ...] = (
    KIND_D1_OPEN, KIND_DAILY_CHECK, KIND_TARGET_ZONE, KIND_INVALIDATION,
    KIND_MANUAL_NOTE, KIND_CLOSE,
)

#: 八项键(顺序 = K8 §十四 原文顺序,⛔ 不许重排、不许增减)。
FINAL_ITEM_KEYS: Tuple[str, ...] = (
    "entry_price_position",
    "entry_conditions",
    "expected_path",
    "driver_structure_support",
    "target_zone_handling",
    "upside_efficiency",
    "stop_after_invalidation",
    "thesis_consistency",
)

#: 「上涨效率」滑动窗口(**量的定义**,不是及格线 —— 见模块头)。
EFFICIENCY_WINDOW_DAYS = 3

#: 用户主观说明的长度上限(**工程护栏**,不是策略参数:K8 §十五 原文要求
#: 「每次一条**简短**说明」,这里给"简短"一个不至于把一整篇小作文塞进事件流的上界)。
USER_NOTE_MAX_CHARS = 500


def _d(x: Any) -> str:
    return x if isinstance(x, str) else x.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f else None


def _mean(xs: Sequence[float]) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None]
    return sum(vals) / len(vals) if vals else None


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 上涨效率变化(**只出比值,⛔ 不下结论**)
# ══════════════════════════════════════════════════════════════════════════

def upside_efficiency(daily_returns: Sequence[Optional[float]], *,
                      window: int = EFFICIENCY_WINDOW_DAYS) -> Dict[str, Any]:
    """持有期每日涨幅序列 → 上涨效率读数。

    · `baseline` = 入场后**前 `window` 日**每日涨幅均值
    · `recent`   = **最后 `window` 日**每日涨幅均值
    · `ratio`    = `recent / baseline`(`baseline` 为 0 或缺 → `None`,⛔ 不拿 0 顶)

    🔴 **本函数不判断"效率下降了没有"** —— 那需要一条阈值,而 K8 §十三 明确说这里
    「保留主观换股权,**不设机械规则**」。要下这个判断的是用户,不是代码。
    """
    rets = [r for r in daily_returns if _num(r) is not None]
    n = len(rets)
    if n < window:
        return {"available": False, "source": "engineering_v1",
                "unavailable_reason": f"持有期不足 {window} 个交易日,滑动均值无定义",
                "window": window, "observations": n,
                "baseline": None, "recent": None, "ratio": None}
    baseline = _mean(rets[:window])
    recent = _mean(rets[-window:])
    ratio = None
    if baseline is not None and recent is not None and abs(baseline) > 0:
        ratio = recent / baseline
    return {
        "available": True, "source": "engineering_v1", "unavailable_reason": None,
        "window": window, "observations": n,
        "baseline": baseline, "recent": recent, "ratio": ratio,
        "note": ("比值只是读数:⛔ 不设阈值、不触发任何持仓动作、不进推送"
                 "(K8 §十三「保留主观换股权,不设机械规则」)"),
    }


# ══════════════════════════════════════════════════════════════════════════
# 读写(`trade_clock` 有生命周期可 UPDATE;`trade_clock_events` append-only)
# ══════════════════════════════════════════════════════════════════════════

_CLOCK_COLUMNS = ("id, position_id, ts_code, basket_id, opened_on, closed_on, status, "
                  "entry_plan_json, final_json, created_at, updated_at")


def _loads(blob: Any) -> Optional[Dict[str, Any]]:
    if not blob:
        return None
    try:
        v = json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        return None
    return v if isinstance(v, dict) else None


def _clock_row(row: Sequence[Any]) -> Dict[str, Any]:
    return {
        "id": int(row[0]), "position_id": int(row[1]), "ts_code": str(row[2]),
        "basket_id": (int(row[3]) if row[3] is not None else None),
        "opened_on": str(row[4]), "closed_on": row[5], "status": str(row[6]),
        "entry_plan": _loads(row[7]) or {}, "final": _loads(row[8]),
        "created_at": str(row[9]), "updated_at": str(row[10]),
    }


def load_trade_clock(position_id: int, *,
                     db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """按 `position_id` 读一个交易时钟。`None` = **还没建**(合法:那笔仓还没对过账,
    或压根没有那笔仓)—— ⛔ 调用方别把它读成"这笔仓不存在"。"""
    from neckline.db import connection, init_schema

    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_CLOCK_COLUMNS} FROM trade_clock WHERE position_id=?",
            (int(position_id),),
        ).fetchone()
    return _clock_row(row) if row is not None else None


def list_trade_clocks(*, status: Optional[str] = None,
                      date_from: Any = None, date_to: Any = None,
                      db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """按 `opened_on` 区间读交易时钟(排序 `(opened_on, position_id)`,确定性)。"""
    from neckline.db import connection, init_schema

    sql = f"SELECT {_CLOCK_COLUMNS} FROM trade_clock"
    where: List[str] = []
    args: List[Any] = []
    if status:
        where.append("status=?")
        args.append(status)
    if date_from is not None:
        where.append("opened_on >= ?")
        args.append(_d(date_from))
    if date_to is not None:
        where.append("opened_on <= ?")
        args.append(_d(date_to))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY opened_on, position_id"
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return [_clock_row(r) for r in rows]


def list_events(trade_clock_id: int, *,
                db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """某时钟的事件流水(**升序 id = 写入序**;append-only,读回即历史)。"""
    from neckline.db import connection, init_schema

    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, trade_clock_id, event_date, kind, mech_json, user_note, created_at "
            "FROM trade_clock_events WHERE trade_clock_id=? ORDER BY id",
            (int(trade_clock_id),),
        ).fetchall()
    return [{"id": int(r[0]), "trade_clock_id": int(r[1]), "event_date": str(r[2]),
             "kind": str(r[3]), "mech": _loads(r[4]) or {}, "user_note": r[5],
             "created_at": str(r[6])} for r in rows]


def _append_event(conn: Any, trade_clock_id: int, event_date: str, kind: str, *,
                  mech: Optional[Mapping[str, Any]] = None,
                  user_note: Optional[str] = None) -> int:
    # `kind` 是**归因要按它分桶**的码 —— 拼错一个字符不会报错、只会静默多出一个
    # 谁也不认识的桶(fail loud 比事后从成绩单里找错别字便宜)。
    if kind not in EVENT_KINDS:
        raise ValueError(f"未登记的事件种类 {kind!r}(合法值:{EVENT_KINDS})")
    cur = conn.execute(
        "INSERT INTO trade_clock_events "
        "(trade_clock_id, event_date, kind, mech_json, user_note, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (int(trade_clock_id), event_date, kind,
         json.dumps(dict(mech or {}), ensure_ascii=False, sort_keys=True),
         user_note, _now()),
    )
    return int(cur.lastrowid)


def _has_event(conn: Any, trade_clock_id: int, event_date: str, kind: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM trade_clock_events WHERE trade_clock_id=? AND event_date=? AND kind=? "
        "LIMIT 1",
        (int(trade_clock_id), event_date, kind),
    ).fetchone()
    return row is not None


class UserNoteError(ValueError):
    """用户主观说明不合法(空 / 超长)。**fail loud** —— ⛔ 不静默截断:
    截断会把用户写的话改掉一半还装作收下了。"""


def append_user_note(position_id: int, note: str, *,
                     event_date: Any = None,
                     db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """追加一条用户主观说明(K8 §十五)。返回新事件行;`None` = **该仓没有交易时钟**
    (调用方据此 404 —— 复用既有 `not_found` reason,⛔ 不新增 reason 字符串)。

    **只追加**:写的是 `trade_clock_events` 的新行,⛔ 不改任何既有行(append-only
    三律;守门单测扫本模块无 UPDATE/DELETE 该表的 SQL)。
    """
    from neckline.db import connection, init_schema

    text = (note or "").strip()
    if not text:
        raise UserNoteError("说明不能为空")
    if len(text) > USER_NOTE_MAX_CHARS:
        raise UserNoteError(f"说明超过 {USER_NOTE_MAX_CHARS} 字上限(K8 §十五:每次一条简短说明)")

    day = _d(event_date) if event_date is not None else _d(date.today())
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute("SELECT id FROM trade_clock WHERE position_id=?",
                           (int(position_id),)).fetchone()
        if row is None:
            return None
        clock_id = int(row[0])
        event_id = _append_event(conn, clock_id, day, KIND_MANUAL_NOTE, user_note=text)
    return {"id": event_id, "trade_clock_id": clock_id, "event_date": day,
            "kind": KIND_MANUAL_NOTE, "user_note": text}


def note_coverage(*, date_from: Any = None, date_to: Any = None,
                  db_path: Optional[Path] = None) -> Dict[str, Any]:
    """§七 **P3-28** 的落点之二:归因标签**覆盖率**(「本期 N 笔中有 M 笔带说明」)。

    P3-28 原文要的正是「先加标签覆盖率指标,让稀疏程度可见」——⛔ 覆盖率低不触发
    任何自动补救(候选解法 ② LLM 代猜**仍然不做**),它只是把事实摆出来。
    """
    clocks = list_trade_clocks(date_from=date_from, date_to=date_to, db_path=db_path)
    if not clocks:
        return {"available": False, "unavailable_reason": "本期没有任何交易时钟(没有真实买入)",
                "trades": 0, "with_note": 0, "coverage": None, "notes": 0}
    from neckline.db import connection, init_schema

    ids = [c["id"] for c in clocks]
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT trade_clock_id, COUNT(*) FROM trade_clock_events "
            "WHERE kind=? AND trade_clock_id IN (%s) GROUP BY trade_clock_id"
            % ",".join("?" * len(ids)),
            (KIND_MANUAL_NOTE, *ids),
        ).fetchall()
    per = {int(r[0]): int(r[1]) for r in rows}
    with_note = len(per)
    return {
        "available": True, "unavailable_reason": None,
        "trades": len(clocks), "with_note": with_note,
        "coverage": with_note / len(clocks),
        "notes": sum(per.values()),
        "note": ("K8 §十五 的用户主观说明是**唯一**的人工输入;覆盖率低只作披露,"
                 "⛔ 不做 LLM 代猜(§七 P3-28 纪律不变)"),
    }


# ══════════════════════════════════════════════════════════════════════════
# 结案八项(全部读既有冻结件与行情,⛔ 不写任何退出 / 成交逻辑)
# ══════════════════════════════════════════════════════════════════════════

def _plan_piece(plan: Mapping[str, Any], key: str) -> Any:
    v = plan.get(key)
    return v if v not in ({}, [], "") else None


def build_final(
    clock: Mapping[str, Any],
    *,
    position: Mapping[str, Any],
    daily_returns: Sequence[Optional[float]] = (),
    events: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    """结案时的八项验证。**只装配已有事实**:入场计划四件套来自开仓冻结的
    `entry_plan_json`,价格来自 `positions` 台账,路径读数来自事件流水。

    🔴 **判分与退出模拟一行都不在这里** —— 可交易收益永远只有
    `neckline/eval/exit_sim.py` 一份(⑨-D 唯一源,既有 grep 守门单测)。本函数
    产出的是「这笔交易与它开仓时那份计划对不对得上」,不是「它本该赚多少」。
    """
    plan = dict(clock.get("entry_plan") or {})
    buy = _num(position.get("buy_price"))
    sell = _num(position.get("sell_price"))
    zone = plan.get("entry_zone") if isinstance(plan.get("entry_zone"), Mapping) else None
    zone_low, zone_high = (_num((zone or {}).get("low")), _num((zone or {}).get("high")))
    exit_ref = plan.get("exit_reference") if isinstance(plan.get("exit_reference"), Mapping) else None
    ex_low, ex_high = (_num((exit_ref or {}).get("low")), _num((exit_ref or {}).get("high")))
    max_chase = _num(plan.get("max_chase"))

    in_zone = None
    if buy is not None and zone_low is not None and zone_high is not None:
        in_zone = zone_low <= buy <= zone_high
    above_chase = (None if (buy is None or max_chase is None) else buy > max_chase)

    hit_target = None
    if sell is not None and ex_low is not None:
        hit_target = sell >= ex_low

    kinds = [str(e.get("kind")) for e in events]
    final: Dict[str, Any] = {
        "spec_version": FINAL_SPEC_VERSION,
        # ① 买入位置
        "entry_price_position": {
            "available": buy is not None,
            "unavailable_reason": None if buy is not None else "台账无买入价",
            "buy_price": buy, "entry_zone": ({"low": zone_low, "high": zone_high}
                                             if zone_low is not None else None),
            "in_entry_zone": in_zone, "max_chase": max_chase,
            "above_max_chase": above_chase,
        },
        # ② 入场条件(= 开仓时四件套齐不齐;缺件在 ⑩ 已出过警示,这里留痕)
        "entry_conditions": {
            "available": bool(plan),
            "unavailable_reason": None if plan else "开仓时没有继承到任何计划(非篮子来源)",
            "plan_available": plan.get("available"),
            "missing_pieces": plan.get("missing_pieces") or plan.get("missing") or [],
            "source_basket_id": clock.get("basket_id"),
        },
        # ③ 预期路径(卡上的上涨判断;⛔ 不在结案时重写它)
        # ⚠ **三路 OR 是刻意的**:`entry_plan_json` 是**开仓当时**冻住的历史快照,
        # 库里同时存在三种形状 —— ① `upside_script`(⑩ 继承时已展平的四件套码,
        # 🔴 该码字符串一字不改)· ② `upside_path`(V2.3.3 起的卡键)· ③ `scripts`
        # (V2.3.3 之前那张卡的三剧本形状)。少一路就会让某一代老仓假装缺件。
        "expected_path": {
            "available": bool(_plan_piece(plan, "upside_script")
                              or plan.get("upside_path") or plan.get("scripts")),
            "unavailable_reason": None if (plan.get("upside_script")
                                           or plan.get("upside_path")
                                           or plan.get("scripts"))
            else "计划里没有上涨判断(预期上涨路径)",
            "upside_script": (plan.get("upside_script") or plan.get("upside_path")
                              or plan.get("scripts")),
        },
        # ④ 驱动、结构与支撑(D0 冻结的驱动 + 失效位置)
        "driver_structure_support": {
            "available": bool(plan.get("driver") or plan.get("invalidation")),
            "unavailable_reason": None if (plan.get("driver") or plan.get("invalidation"))
            else "计划里既无驱动也无失效位置",
            "driver": plan.get("driver"),
            "invalidation": plan.get("invalidation"),
        },
        # ⑤ 目标区间处理
        "target_zone_handling": {
            "available": ex_low is not None,
            "unavailable_reason": None if ex_low is not None else "计划里没有目标离场区间",
            "exit_reference": ({"low": ex_low, "high": ex_high} if ex_low is not None else None),
            "sell_price": sell,
            "reached_target": hit_target,
            "target_zone_events": sum(1 for k in kinds if k == KIND_TARGET_ZONE),
        },
        # ⑥ 上涨效率变化(**只出比值**,见模块头)
        "upside_efficiency": upside_efficiency(daily_returns),
        # ⑦ 失效后的止损(⚠ 章程口径,只作**警戒记录**——V2.2-⑤ 起「违纪判定」已降级)
        "stop_after_invalidation": {
            "available": bool(kinds) or sell is not None,
            "unavailable_reason": None if (kinds or sell is not None) else "无事件、无卖出记录",
            "invalidation_events": sum(1 for k in kinds if k == KIND_INVALIDATION),
            "close_reason": position.get("close_reason"),
            "sell_price": sell,
            "note": "本项只作警戒记录,⛔ 不在这里判违纪(判定归周复盘对账)",
        },
        # ⑧ 原始理由的一致性(把 D0 那句理由与实际处理并排放好,**不下结论**)
        "thesis_consistency": {
            "available": bool(plan.get("driver") or plan.get("reason")),
            "unavailable_reason": None if (plan.get("driver") or plan.get("reason"))
            else "开仓时没有留下原始理由",
            "original_reason": plan.get("reason"),
            "driver": plan.get("driver"),
            "user_notes": [e.get("user_note") for e in events
                           if e.get("kind") == KIND_MANUAL_NOTE and e.get("user_note")],
            "note": "原始理由与实际处理并排呈现,是否一致由人判断(⛔ 机器不给结论)",
        },
    }
    return final


# ══════════════════════════════════════════════════════════════════════════
# 对账编排:`positions` 是唯一真相
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class SyncResult:
    as_of: str
    opened: int = 0          # 新建的时钟数(= 新发现的真实买入)
    closed: int = 0          # 本次结案的时钟数
    events: int = 0          # 本次追加的事件数
    running: int = 0         # 对账后仍在运行的时钟数
    notes: List[str] = field(default_factory=list)


def _entry_plan_snapshot(position_id: int, *, db_path: Optional[Path]) -> Dict[str, Any]:
    """开仓时冻的四件套快照 = `position_plans` 的 **version=1**(⑩ 从篮子卡继承那版)。

    ⚠ 取 v1 而不是"最新版":K8 §十五「原始快照只增不改」—— 交易时钟要对账的是
    **开仓当时那份计划**,用户后来改过的版本是另一件事(它们仍在 `position_plans`
    里,谁要看走那张表)。
    """
    from neckline.db import connection, init_schema

    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT plan_json, source_basket_id, source_card_version FROM position_plans "
            "WHERE position_id=? AND version=1", (int(position_id),),
        ).fetchone()
    if row is None:
        return {"available": False,
                "unavailable_reason": "开仓时没有 version=1 的计划(非篮子来源 / 老数据)"}
    plan = _loads(row[0]) or {}
    plan.setdefault("source_basket_id", row[1])
    plan.setdefault("source_card_version", row[2])
    return plan


def _basket_id_of(position_id: int, *, db_path: Optional[Path]) -> Optional[int]:
    """来源篮子 = `entry_snapshots.basket_id`(⑩ 开仓时冻的);`None` = 手动开仓。"""
    from neckline.db import connection, init_schema

    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute("SELECT basket_id FROM entry_snapshots WHERE position_id=?",
                           (int(position_id),)).fetchone()
    return int(row[0]) if (row is not None and row[0] is not None) else None


def _daily_returns(ts_code: str, start: str, end: Optional[str], *,
                   parquet_dir: Optional[Path]) -> List[float]:
    """持有期每日涨幅(小数)。取 `daily.pct_chg` —— 与 ⑨ 复盘的 `member_return`
    同口径(TuShare 按当日 `pre_close` 算,已处理除权除息,⛔ 别改成收盘价相减)。

    读不到 → 空列表(⑥ 会如实标"持有期不足/算不出",⛔ 不补 0)。
    """
    try:
        from neckline.data.market_data import get_stock_history

        lo = datetime.strptime(start, "%Y%m%d").date()
        hi = datetime.strptime(end, "%Y%m%d").date() if end else date.today()
        df = get_stock_history(ts_code, lo, hi, parquet_dir=parquet_dir)
        if df.is_empty() or "pct_chg" not in df.columns:
            return []
        return [float(v) / 100.0 for v in df.sort("trade_date")["pct_chg"].to_list()
                if v is not None and not (isinstance(v, float) and v != v)]
    except Exception:  # noqa: BLE001 —— 可选情报保险丝:算不出就少一项读数
        logger.warning("[trade_clock] %s 持有期涨幅读取失败,⑥ 上涨效率将标不可得",
                       ts_code, exc_info=True)
        return []


def sync_from_positions(
    as_of: Any = None, *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> SyncResult:
    """以 `positions` 为唯一真相对一次账。**幂等**:同一天跑两次 = 第二次零新增。

    三件事,顺序固定:
      ① **建**:每一笔仓(open 或 closed 都算)若还没有时钟 → 建一个,并落 `d1_open`
         事件(开仓日 = `positions.buy_date`,**不是**对账当天)。
      ② **跟**:仍在运行的时钟,当天补一条 `daily_check`(同日不重复,靠事件去重)。
      ③ **结**:仓已 `status='closed'` 而时钟还 `running` → 装配八项、落 `close` 事件、
         时钟转 `closed`。

    🔴 **「实际买入是唯一启动条件」在这里是字面真的**:时钟只可能因为 `positions`
    里有那一行而存在;本函数不看篮子、不看候选、不看选股时钟。
    """
    from neckline.db import connection, init_schema

    day = _d(as_of) if as_of is not None else _d(date.today())
    res = SyncResult(as_of=day)
    init_schema(db_path)

    try:
        with connection(db_path) as conn:
            positions = conn.execute(
                "SELECT id, ts_code, buy_date, status, sell_date, sell_price, buy_price, "
                "close_reason FROM positions ORDER BY id"
            ).fetchall()
            existing = {int(r[0]) for r in
                        conn.execute("SELECT position_id FROM trade_clock")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trade_clock] 对账读取失败", exc_info=True)
        res.notes.append(f"对账读取失败:{type(exc).__name__}: {exc}")
        return res

    for row in positions:
        pid = int(row[0])
        ts_code, buy_date, status = str(row[1]), str(row[2]), str(row[3])
        sell_date, sell_price, buy_price, close_reason = row[4], row[5], row[6], row[7]
        try:
            if pid not in existing:
                plan = _entry_plan_snapshot(pid, db_path=db_path)
                basket_id = _basket_id_of(pid, db_path=db_path)
                with connection(db_path) as conn:
                    now = _now()
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO trade_clock "
                        "(position_id, ts_code, basket_id, opened_on, closed_on, status, "
                        " entry_plan_json, final_json, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (pid, ts_code, basket_id, buy_date, None, STATUS_RUNNING,
                         json.dumps(plan, ensure_ascii=False, sort_keys=True), None, now, now),
                    )
                    if cur.rowcount:
                        res.opened += 1
                        clock_id = int(cur.lastrowid)
                        _append_event(conn, clock_id, buy_date, KIND_D1_OPEN, mech={
                            "buy_price": _num(buy_price),
                            "entry_plan_available": plan.get("available"),
                            "source_basket_id": basket_id,
                            "note": "启动条件 = 实际买入(K8 §十四),由 positions 对账建立",
                        })
                        res.events += 1

            clock = load_trade_clock(pid, db_path=db_path)
            if clock is None:
                continue

            if clock["status"] == STATUS_RUNNING and status == "open":
                # ⚠ **买入当天不落 `daily_check`**:K8 §十四 的时间节点写死「D1 买入日:
                # 记录入场区间、入场条件和收盘状态;**D2 及以后**:验证预期路径……」——
                # 买入日那一拍的名字叫 `d1_open`,再补一条 `daily_check` 就是同一天两条
                # 讲同一件事,归因时按 kind 计数会重复。
                if day != clock["opened_on"]:
                    with connection(db_path) as conn:
                        if not _has_event(conn, clock["id"], day, KIND_DAILY_CHECK):
                            _append_event(conn, clock["id"], day, KIND_DAILY_CHECK, mech={
                                "as_of": day, "note": "持有中的每日跟踪拍(K8 §十四 D2 及以后)",
                            })
                            res.events += 1
                res.running += 1
                continue

            if clock["status"] == STATUS_RUNNING and status == "closed":
                closed_on = str(sell_date or day)
                rets = _daily_returns(ts_code, buy_date, closed_on, parquet_dir=parquet_dir)
                events = list_events(clock["id"], db_path=db_path)
                final = build_final(
                    clock,
                    position={"buy_price": buy_price, "sell_price": sell_price,
                              "close_reason": close_reason, "ts_code": ts_code},
                    daily_returns=rets, events=events,
                )
                with connection(db_path) as conn:
                    conn.execute(
                        "UPDATE trade_clock SET status=?, closed_on=?, final_json=?, "
                        "updated_at=? WHERE id=? AND status=?",
                        (STATUS_CLOSED, closed_on,
                         json.dumps(final, ensure_ascii=False, sort_keys=True),
                         _now(), clock["id"], STATUS_RUNNING),
                    )
                    _append_event(conn, clock["id"], closed_on, KIND_CLOSE, mech={
                        "sell_price": _num(sell_price), "close_reason": close_reason,
                        "note": "全部离场 → 交易时钟结案(K8 §十四)",
                    })
                res.closed += 1
                res.events += 1
        except Exception as exc:  # noqa: BLE001 —— 一笔炸不连坐其余
            logger.warning("[trade_clock] position_id=%s 对账失败", pid, exc_info=True)
            res.notes.append(f"position_id={pid} 对账失败:{type(exc).__name__}")
    return res


__all__ = [
    "TABLE", "EVENTS_TABLE", "FINAL_SPEC_VERSION", "FINAL_ITEM_KEYS", "EVENT_KINDS",
    "STATUS_RUNNING", "STATUS_CLOSED",
    "KIND_D1_OPEN", "KIND_DAILY_CHECK", "KIND_TARGET_ZONE", "KIND_INVALIDATION",
    "KIND_MANUAL_NOTE", "KIND_CLOSE",
    "EFFICIENCY_WINDOW_DAYS", "USER_NOTE_MAX_CHARS", "UserNoteError",
    "upside_efficiency", "build_final",
    "load_trade_clock", "list_trade_clocks", "list_events",
    "append_user_note", "note_coverage",
    "SyncResult", "sync_from_positions",
]
