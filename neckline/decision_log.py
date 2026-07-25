"""预注册决策日志(plan §五 v1.2-B,§2.1 第 3 条人机协作配套)存取 + CRUD。

下单前录八项(为什么买 / 为什么这个入场价 / 目标价 / 离场价格区间 / 论点标签 /
证伪条件 / 应对方案·情景树 / 打法标签),时间戳先于成交防结果污染。**审计件、
非下单件**——本模块任何函数都不触发下单 / 撤单 / 拉行情,只做记账(§3.8 铁律,
同 `neckline.sentinel.positions`/`neckline.watchlist` 姿势)。

**不可编辑口径(核心不变量,逐条对应表结构注释)**:
    · ①-⑥(`why_buy`/`why_entry_price`/`target_price`/`exit_low`/`exit_high`/
      `thesis_tags`/`invalidation`)+ ⑦ 情景树的 `scenario`/`trigger`/`action` +
      ⑧(`playbook_tag`)—— 本模块**无任何 UPDATE 语句触碰这些列**。改动只能走
      `revise_decision` 新增一行(`revision_of` 落链根 id),旧行原地不变。
    · 唯一例外 = ⑦ 情景树的 `matched`(事后结果标记,非预注册内容)——只能经
      `set_scenario_outcomes` 翻,该函数的 UPDATE 只碰 `contingency_scenarios` +
      `updated_at` 两列,绝不改 `scenario`/`trigger`/`action`。
    · `status`/`position_id` 是审计结果关联字段(非八项之一),`link_decision`/
      `cancel_decision` 可以改它们。

**`created_at` 服务端生成**:本模块所有创建函数(`create_decision`/
`revise_decision`)签名里根本没有 `created_at` 形参——杜绝调用方(含 API 入参)
伪造预注册时间戳,防结果污染(同研究铁律「预注册先行」原理)。

**revision_of 落链根,不落直接父行**:`revise_decision(id, ...)` 新增行的
`revision_of` = 「`id` 若自身已是修订行,取其 `revision_of`;否则 `id` 本身即
链根」。整条修订链因此扁平——`WHERE revision_of=<根id>` 一步查出全部修订版本,
`WHERE revision_of IS NULL` 一步查出全部首版,归因(v1.2.1-D)不必递归遍历链条。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline.db import connection, init_schema

STATUS_PENDING = "pending"
STATUS_FILLED = "filled"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"

# ⑤论点标签(服务端码,客户端展示层换算,沿 `CandidateOut.board`/`boardLabel` 先例)。
THESIS_TAG_CODES = ("THEME", "SENTIMENT_CYCLE", "CAPITAL_FLOW", "TECH_PATTERN", "NEWS")
# ⑧打法标签(单选;三仓 = 2 短线追击 + 1 呼吸底仓试验)。
PLAYBOOK_TAG_CODES = ("SWING_CHASE", "BREATHING_TRIAL")
# ⑦情景树 action(有限枚举码)。
SCENARIO_ACTION_CODES = ("BUY", "HOLD", "REDUCE", "ABANDON")

_SELECT_COLS = (
    "id, ts_code, name, created_at, why_buy, why_entry_price, target_price, "
    "exit_low, exit_high, thesis_tags, invalidation, contingency_scenarios, "
    "playbook_tag, planned_price, planned_qty, status, position_id, revision_of, updated_at"
)


class ScenarioIndexError(ValueError):
    """`set_scenario_outcomes` 的 `index` 越界(超出该决策情景树数组范围)。
    API 层据此转 422,不是 404(id 本身存在,只是 index 非法)。"""


@dataclass
class DecisionRow:
    id: int
    ts_code: str
    name: Optional[str]
    created_at: str
    why_buy: str
    why_entry_price: str
    target_price: Optional[float]
    exit_low: Optional[float]
    exit_high: Optional[float]
    thesis_tags: List[str] = field(default_factory=list)
    invalidation: str = ""
    contingency_scenarios: List[Dict[str, Any]] = field(default_factory=list)
    playbook_tag: str = ""
    planned_price: Optional[float] = None
    planned_qty: Optional[int] = None
    status: str = STATUS_PENDING
    position_id: Optional[int] = None
    revision_of: Optional[int] = None
    updated_at: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loads(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def _normalize_scenarios(scenarios: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """情景树落库前的结构归一(防御性——API 层的 `ContingencyScenarioIn` 已保证
    形状,这里是给直调本模块的调用方〔含单测〕兜底,缺 key 不炸)。`matched` 未传
    → 默认 False(表头注释「事后结果标记」的默认态)。"""
    out: List[Dict[str, Any]] = []
    for s in scenarios:
        out.append({
            "scenario": s.get("scenario", ""),
            "trigger": s.get("trigger", ""),
            "action": s.get("action", ""),
            "matched": bool(s.get("matched", False)),
        })
    return out


def _to_iso_date(yyyymmdd: str) -> str:
    """'YYYYMMDD' → 'YYYY-MM-DD',供与 `created_at`(ISO8601)前 10 字符做字符串
    范围比较(`list_decisions` 的 `from`/`to` 过滤,ISO 日期字符串天然可字典序比较)。
    非法格式原样返回(比较大概率不命中任何行,不因脏输入 500)。"""
    s = (yyyymmdd or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _row_to_decision(row) -> DecisionRow:
    return DecisionRow(
        id=row[0], ts_code=row[1], name=row[2], created_at=row[3],
        why_buy=row[4], why_entry_price=row[5], target_price=row[6],
        exit_low=row[7], exit_high=row[8],
        thesis_tags=_loads(row[9], []), invalidation=row[10],
        contingency_scenarios=_loads(row[11], []),
        playbook_tag=row[12], planned_price=row[13], planned_qty=row[14],
        status=row[15], position_id=row[16], revision_of=row[17], updated_at=row[18],
    )


# —— 读 ——————————————————————————————————————————————————————————————————

def get_decision(decision_id: int, db_path: Optional[Path] = None) -> Optional[DecisionRow]:
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM decision_log WHERE id=?", (decision_id,)
        ).fetchone()
    return _row_to_decision(row) if row else None


def list_decisions(
    status: Optional[str] = None,
    ts_code: Optional[str] = None,
    date_from: Optional[str] = None,   # 'YYYYMMDD',按 created_at 日期过滤
    date_to: Optional[str] = None,     # 'YYYYMMDD'
    db_path: Optional[Path] = None,
) -> List[DecisionRow]:
    """`GET /decisions` 的查询(plan B.2)。默认返全部,可按 status / code / 日期
    区间过滤;按 `created_at, id` 升序(与其它列表端点惯例一致)。"""
    init_schema(db_path)
    clauses: List[str] = []
    params: List[Any] = []
    if status:
        clauses.append("status=?")
        params.append(status)
    if ts_code:
        clauses.append("ts_code=?")
        params.append(ts_code)
    if date_from:
        clauses.append("substr(created_at,1,10) >= ?")
        params.append(_to_iso_date(date_from))
    if date_to:
        clauses.append("substr(created_at,1,10) <= ?")
        params.append(_to_iso_date(date_to))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM decision_log {where} ORDER BY created_at, id", params
        ).fetchall()
    return [_row_to_decision(r) for r in rows]


# —— 写(唯一写入通道,同 `watchlist.py`/`sentinel/positions.py` 姿势)——————————

def create_decision(
    ts_code: str,
    why_buy: str,
    why_entry_price: str,
    invalidation: str,
    thesis_tags: List[str],
    playbook_tag: str,
    contingency_scenarios: Optional[List[Dict[str, Any]]] = None,
    name: Optional[str] = None,
    target_price: Optional[float] = None,
    exit_low: Optional[float] = None,
    exit_high: Optional[float] = None,
    planned_price: Optional[float] = None,
    planned_qty: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> DecisionRow:
    """预注册一条决策日志(八项,plan B.1/B.2)。`created_at` 服务端生成——本函数
    签名本就无 `created_at` 形参,任何调用方都无法覆盖。新行 `status="pending"`、
    `position_id=None`、`revision_of=None`(首版)。"""
    init_schema(db_path)
    now = _now()
    scenarios = _normalize_scenarios(contingency_scenarios or [])
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO decision_log ("
            "ts_code, name, created_at, why_buy, why_entry_price, target_price, "
            "exit_low, exit_high, thesis_tags, invalidation, contingency_scenarios, "
            "playbook_tag, planned_price, planned_qty, status, position_id, revision_of, updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ts_code, name, now, why_buy, why_entry_price, target_price,
                exit_low, exit_high, json.dumps(list(thesis_tags), ensure_ascii=False), invalidation,
                json.dumps(scenarios, ensure_ascii=False), playbook_tag, planned_price, planned_qty,
                STATUS_PENDING, None, None, now,
            ),
        )
        new_id = int(cur.lastrowid)
    row = get_decision(new_id, db_path=db_path)
    assert row is not None  # 刚写入,必然读得到
    return row


def link_decision(decision_id: int, position_id: int, db_path: Optional[Path] = None) -> bool:
    """成交后一键关联(plan B.2)。`status` 置 `filled` + `position_id` 回填。返回
    是否命中该 id(不存在 → False,API 层据此 404)。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "UPDATE decision_log SET status=?, position_id=?, updated_at=? WHERE id=?",
            (STATUS_FILLED, position_id, now, decision_id),
        )
        return cur.rowcount > 0


def cancel_decision(decision_id: int, db_path: Optional[Path] = None) -> bool:
    """用户放弃该预注册计划(plan B.2)。`status` 置 `cancelled`。返回是否命中该 id
    (不存在 → False,API 层据此 404)。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "UPDATE decision_log SET status=?, updated_at=? WHERE id=?",
            (STATUS_CANCELLED, now, decision_id),
        )
        return cur.rowcount > 0


def revise_decision(
    decision_id: int,
    *,
    why_buy: str,
    why_entry_price: str,
    invalidation: str,
    thesis_tags: List[str],
    playbook_tag: str,
    contingency_scenarios: Optional[List[Dict[str, Any]]] = None,
    target_price: Optional[float] = None,
    exit_low: Optional[float] = None,
    exit_high: Optional[float] = None,
    planned_price: Optional[float] = None,
    planned_qty: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> Optional[DecisionRow]:
    """新增一行修订(plan B.2「改动只新增修订行,不改旧行」)。`decision_id` 对应的
    旧行**原地不变**(本函数无任何 UPDATE 作用于它);新行 `ts_code`/`name` 继承自
    `decision_id` 行(修订不能换股票),`revision_of` = **链根** id(见模块头注释),
    `status` 重置为 `pending`。`decision_id` 不存在 → None(API 层据此 404)。"""
    base = get_decision(decision_id, db_path=db_path)
    if base is None:
        return None
    root_id = base.revision_of if base.revision_of is not None else base.id
    now = _now()
    scenarios = _normalize_scenarios(contingency_scenarios or [])
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO decision_log ("
            "ts_code, name, created_at, why_buy, why_entry_price, target_price, "
            "exit_low, exit_high, thesis_tags, invalidation, contingency_scenarios, "
            "playbook_tag, planned_price, planned_qty, status, position_id, revision_of, updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                base.ts_code, base.name, now, why_buy, why_entry_price, target_price,
                exit_low, exit_high, json.dumps(list(thesis_tags), ensure_ascii=False), invalidation,
                json.dumps(scenarios, ensure_ascii=False), playbook_tag, planned_price, planned_qty,
                STATUS_PENDING, None, root_id, now,
            ),
        )
        new_id = int(cur.lastrowid)
    row = get_decision(new_id, db_path=db_path)
    assert row is not None
    return row


def set_scenario_outcomes(
    decision_id: int,
    outcomes: List[Dict[str, Any]],
    db_path: Optional[Path] = None,
) -> bool:
    """情景树⑦结果标记专用(plan B.2「scenario-outcome 只翻 matched、绝不碰情景
    文本」)。`outcomes` 每项 `{index, matched}`,`index` 对齐 `contingency_scenarios`
    数组下标。**只 UPDATE `contingency_scenarios` + `updated_at` 两列**,`scenario`/
    `trigger`/`action` 逐字不变(本函数从旧数组原样拷贝这三个 key,只替换命中项的
    `matched`)。

    返回是否命中该 id(不存在 → False,API 层据此 404)。任一 `index` 越界 →
    `ScenarioIndexError`(API 层据此 422)——**先校验全部 index 合法,再一次性写回**
    (不部分生效:一批 outcomes 里若有一个越界,整批都不落库)。
    """
    row = get_decision(decision_id, db_path=db_path)
    if row is None:
        return False
    scenarios = [dict(s) for s in row.contingency_scenarios]  # 拷贝,不动原文本
    for item in outcomes:
        idx = item.get("index")
        if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 or idx >= len(scenarios):
            raise ScenarioIndexError(
                f"情景树下标越界:{idx!r}(该决策情景树长度 {len(scenarios)},合法范围 "
                f"0..{len(scenarios) - 1})"
            )
    for item in outcomes:
        scenarios[item["index"]]["matched"] = bool(item["matched"])
    now = _now()
    init_schema(db_path)
    with connection(db_path) as conn:
        conn.execute(
            "UPDATE decision_log SET contingency_scenarios=?, updated_at=? WHERE id=?",
            (json.dumps(scenarios, ensure_ascii=False), now, decision_id),
        )
    return True


__all__ = [
    "STATUS_PENDING", "STATUS_FILLED", "STATUS_CANCELLED", "STATUS_EXPIRED",
    "THESIS_TAG_CODES", "PLAYBOOK_TAG_CODES", "SCENARIO_ACTION_CODES",
    "ScenarioIndexError", "DecisionRow",
    "get_decision", "list_decisions",
    "create_decision", "link_decision", "cancel_decision", "revise_decision",
    "set_scenario_outcomes",
]
