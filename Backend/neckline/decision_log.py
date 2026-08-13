"""预注册决策日志(plan §五 v1.2-B,§2.1 第 3 条人机协作配套)**只读**存取层。

**v2.0.0 起停写留档(PROJECT_PLAN §五 V2-⑩-C 决策日志强制表单退役)**:`decision_log`
表的 DDL 保留(`neckline/db.py`,注释标「v2.0.0 起停写」),历史行供归因只读;本
模块**不再提供任何写函数**——`create_decision`/`link_decision`/`cancel_decision`/
`expire_decision`/`revise_decision`/`set_scenario_outcomes` 连同它们的不可编辑
口径注释一并删除,**不是注释掉,是物理删除**(全仓 grep 守门断言零写入,见
`tests/test_decision_log.py`)。「预注册买入前的强制表单」被「开仓自动快照
(`entry_snapshots`)+ 用户字段全部可选(落 `user_actions`)」取代,详见
`neckline.positions_entry` 与 `api/app.py::create_decision`(v2.0.0 起复用同一
URL 但已换血成完全不同的"用户可选补充"入口,不再碰本表)。

本模块留存的只有:①`get_decision`/`list_decisions` 两个只读函数(`GET /decisions`
/`GET /decisions/{id}/track` 的唯一数据来源);②`DecisionRow` 数据类与枚举码常量
(供只读装配复用,历史行里这些值仍然合法);③`created_at_cn_date` 等纯函数(供
`list_decisions` 按北京日期过滤历史行,逻辑与"是否还能写"无关)。

历史行的字段语义(为什么买/为什么这个入场价/……/⑨ 最高追价上限)与不可编辑口径
的完整说明见 `archive/`(v1.2-B~v1.4-⑤-B 原始设计)与本文件 git 历史(v2.0.0 之前
版本);本文件不再复述,避免对着一堆不存在的函数说明「它们不可编辑」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline.db import connection, init_schema
from neckline.review.parse import normalize_ts_code

STATUS_PENDING = "pending"
STATUS_FILLED = "filled"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"

# ⑤论点标签(服务端码,客户端展示层换算,沿 `CandidateOut.board`/`boardLabel` 先例)。
# v2.0.0 起写入口退役,常量保留供只读装配历史行(合法值集合不变)。
THESIS_TAG_CODES = ("THEME", "SENTIMENT_CYCLE", "CAPITAL_FLOW", "TECH_PATTERN", "NEWS")
# ⑧打法标签(单选;三仓 = 2 短线追击 + 1 呼吸底仓试验)。
PLAYBOOK_TAG_CODES = ("SWING_CHASE", "BREATHING_TRIAL")
# ⑦情景树 action(有限枚举码)。
SCENARIO_ACTION_CODES = ("BUY", "HOLD", "REDUCE", "ABANDON")

_SELECT_COLS = (
    "id, ts_code, name, created_at, why_buy, why_entry_price, target_price, "
    "exit_low, exit_high, thesis_tags, invalidation, contingency_scenarios, "
    "playbook_tag, planned_price, planned_qty, status, position_id, revision_of, updated_at, "
    "max_chase_pct"
)


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
    # v1.4-⑤-B(需求 2 补充):⑨最高追价上限,相对昨收百分比(如 3.0=+3%);允许负值
    # (只在低开时买);None=显式选择"不设上限"或(老行)建于本字段前——两种情况在
    # 存储层无法区分,见 `db.py` CREATE TABLE decision_log 注释。
    max_chase_pct: Optional[float] = None


def _loads(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def _to_iso_date(yyyymmdd: str) -> str:
    """'YYYYMMDD' → 'YYYY-MM-DD',供与 `created_at` 换算出的**北京日期**做字符串范围
    比较(`list_decisions` 的 `from`/`to` 过滤,ISO 日期字符串天然可字典序比较)。
    非法格式原样返回(比较大概率不命中任何行,不因脏输入 500)。"""
    s = (yyyymmdd or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def created_at_cn_date(created_at: str) -> str:
    """`created_at`(**UTC** ISO8601,历史写入口〔v2.0.0 前的 `create_decision`/
    `revise_decision`,已随写入口一并退役〕留下的既有格式)→ **北京日期**
    `'YYYY-MM-DD'`。

    **v1.4 review 契约线 🟡-2(时区缝)**:`from`/`to` 过滤从前直接拿 `substr(created_at,1,10)`
    比,那是 **UTC 日期** —— 北京时间 **T+1 00:00–07:59** 创建的决策,UTC 日期还停在 T,
    于是历史回放 T 日报告时 `exec_hint` 的 C3 会读到「T 日当时并不存在」的决策(盘前 7 点
    预注册是完全现实的用法),把该模块自己立的**无前视偏差铁律**戳穿一个 8 小时的洞。
    交易日的边界口径全系统只有一个:**北京时间**(`neckline.calendar.CN_TZ`,与 ⑥-A 逐笔
    章程判定同一个源,不另立)。

    naive 串(手工 SQL 补的老行)按 **UTC** 读 —— 与 `brain._parse_instant` 对 naive
    `activated_at` 的约定同源同理由:本列历史写入口写的就是 UTC。解析不了 → 退回
    前 10 字符(旧行为,不因脏数据 500)。"""
    from neckline.calendar import CN_TZ

    s = (created_at or "").strip()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return s[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CN_TZ).strftime("%Y-%m-%d")


def _row_to_decision(row) -> DecisionRow:
    return DecisionRow(
        id=row[0], ts_code=row[1], name=row[2], created_at=row[3],
        why_buy=row[4], why_entry_price=row[5], target_price=row[6],
        exit_low=row[7], exit_high=row[8],
        thesis_tags=_loads(row[9], []), invalidation=row[10],
        contingency_scenarios=_loads(row[11], []),
        playbook_tag=row[12], planned_price=row[13], planned_qty=row[14],
        status=row[15], position_id=row[16], revision_of=row[17], updated_at=row[18],
        max_chase_pct=row[19],
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
    position_id: Optional[int] = None,  # v1.3-②-D:按关联持仓过滤(挑出该持仓待对照决策)
    db_path: Optional[Path] = None,
) -> List[DecisionRow]:
    """`GET /decisions` 的查询(plan B.2 + v1.3-②-D)。默认返全部,可按 status / code / 日期
    区间 / `position_id`(v1.3-②-D 情景树每日对照,挑出该持仓关联决策)过滤;按 `created_at, id`
    升序(与其它列表端点惯例一致)。**只读过滤,无新写路径**。

    **日期区间按北京日期比(v1.4 review 契约线 🟡-2)**:`created_at` 落库是 UTC,而
    `from`/`to` 是**交易日**语义 —— 用 UTC 日期比会让北京 T+1 凌晨创建的决策算作 T 日
    (`exec_hint` C3 的无前视截断因此漏 8 小时,见 `created_at_cn_date`)。故日期这两条
    过滤挪到 Python 侧、经 `created_at_cn_date` 换算后再比;status/code/position_id 三条
    仍走 SQL(等值,与时区无关)。`decision_log` 是人工录入的小表(百量级),取回再过滤的
    代价可忽略,换来的是**判据口径只有一个:北京日**。"""
    init_schema(db_path)
    clauses: List[str] = []
    params: List[Any] = []
    if status:
        clauses.append("status=?")
        params.append(status)
    if ts_code:
        # 查询侧同样归一(v1.3.3):写入通道已归一成 `300759.SZ`,若查询方传裸 `300759`
        # 这条等值过滤会一条都不命中(静默空结果)。两侧过同一个函数才对得上。
        clauses.append("ts_code=?")
        params.append(normalize_ts_code(ts_code))
    if position_id is not None:
        clauses.append("position_id=?")
        params.append(position_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM decision_log {where} ORDER BY created_at, id", params
        ).fetchall()
    lo = _to_iso_date(date_from) if date_from else None
    hi = _to_iso_date(date_to) if date_to else None
    out = [_row_to_decision(r) for r in rows]
    if lo or hi:
        out = [
            d for d in out
            if (lo is None or created_at_cn_date(d.created_at) >= lo)
            and (hi is None or created_at_cn_date(d.created_at) <= hi)
        ]
    return out


__all__ = [
    "STATUS_PENDING", "STATUS_FILLED", "STATUS_CANCELLED", "STATUS_EXPIRED",
    "THESIS_TAG_CODES", "PLAYBOOK_TAG_CODES", "SCENARIO_ACTION_CODES",
    "DecisionRow", "created_at_cn_date",
    "get_decision", "list_decisions",
]
