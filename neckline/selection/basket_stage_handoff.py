"""⑤ 驱动聚合的**段状态**留痕(§七 P0-39,2026-08-05 生产实打后定向小修)。

**病灶**:`report/basket_daily.py::build_basket_daily` 里 ③ 节的
`baskets_available = True` 原本挂在「`load_today_baskets()` 读表成功」上 —— 那只
证明**表读得出来**,不证明**引擎跑过**。于是 08-04 生产上 `llm_providers` 配置
不全导致 `[aggregate] 推理段缺席(no_provider)—— 当日不成篮`、`baskets` 零行时,
报告 ③ 节照样输出「今日无篮子达到定档标准…今天没有共同驱动清晰、成员结构够格
的篮子」—— 一句**实质性市场判断**,而系统其实什么都没判。

**⑤ 侧的信息本来就有**(`selection/aggregate.py` 的 `STAGE_*` 是一等状态、注释
明写"语义不合并"),缺的只是一个**报告层读得到的落点**。本模块就是那个落点。

**与 `basket_dropped_handoff.py` 的关系**:同一族(都是按 `trade_date` 整行覆写
的搬运工表,不是审计账本),但**问的问题不同** ——
- `basket_dropped_handoff` 问「⑥ 定档跑了没有、溢出了哪些」(③b 的数据源);
- 本表问「⑤ 聚合跑成什么样」(③ 的三态判据)。
⚠ **不许合并成一张表**:⑤ 缺席时 `_run_basket_segment` 在 `if not result.baskets`
就早返回了,压根走不到 ⑥ —— 那时 dropped 表**没有行**,而本表**必须有行**(它要
记下"⑤ 跑过、结论是 no_provider"这件事)。两张表的"有没有行"回答的是两个问题。

**三态**(与 ③b 同一套纪律):
- 该 `trade_date` **无行** = ⑤ 本次(迄今)没跑过 → ③ 如实标「本段未取得」;
- 有行、`engine_ran=True` = 跑了 → 零篮子是**真结论**,③ 可以说「今日无篮子达标」;
- 有行、`engine_ran=False` = 没跑成 → ③ 标「本段未取得」+ **原因码**。

**判读逻辑住在这里,⛔ 不许在 `report/` 再推一遍**(第二个事实源):`stage_verdict()`
是「哪些段状态算跑过」的唯一实现。判据锚在 **`reason_stage`**(推理段)——它是
"能不能成篮"的那一段;`search_stage`(检索段)缺席时 ⑤ 仍会出篮子(卡上
`evidence_status` 自有诚实披露),不进本判据。

**同日重跑 = 覆写**(`INSERT OR REPLACE`),只保留最近一次 ⑤ 的结论:同一个交易日、
同一份数据,最近一次的判定就是当前判定。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple

from neckline.db import connection, init_schema
from neckline.selection.aggregate import (
    STAGE_BUDGET_EXHAUSTED,
    STAGE_CALL_FAILED,
    STAGE_NO_PROVIDER,
    STAGE_NO_SEEDS,
    STAGE_OK,
    STAGE_PARSE_FAILED,
)

logger = logging.getLogger(__name__)

# `aggregate_baskets` 自己那道保险丝(整段异常)在 notes 里留的前缀 —— 它返回的
# `AggregateResult` 走的是**默认字段值**(`reason_stage=no_seeds`),光看段状态会被
# 误读成"跑了、今天真没种子"。故判读必须先看 notes(⚠ 这正是 P0-39 那类误读的
# 同款陷阱:一个"看起来正常"的默认值把故障讲成了结论)。
NOTE_AGGREGATE_FAILED_PREFIX = "aggregate_failed:"
# 无现役选股包 → `generate_seeds` 返 `None` → ⑤ 早返回(同样是默认段状态)。
# 这是**配置缺口**,不是"今天没热点",不许算作跑过。
NOTE_NO_ACTIVE_PACK = "no_active_pack_or_seed_set"

# ⑤⑥⑦ 整段在 `report/evening.py` 的保险丝里炸掉时写进 `reason_stage` 的合成码
# (不是 `aggregate.py` 的枚举值,故带自己的前缀,一眼看得出来源)。
STAGE_SEGMENT_FAILED_PREFIX = "segment_failed:"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _day(trade_date: date) -> str:
    return trade_date.strftime("%Y%m%d")


@dataclass(frozen=True)
class BasketStageVerdict:
    """一行段状态的判读结果。`engine_ran=False` 时 `reason_code` 必非空。"""

    trade_date: str
    search_stage: str
    reason_stage: str
    basket_count: int
    notes: Tuple[str, ...]
    engine_ran: bool
    reason_code: Optional[str]


def stage_verdict(
    *, search_stage: str, reason_stage: str, basket_count: int, notes: Sequence[str],
    trade_date: str = "",
) -> BasketStageVerdict:
    """「⑤ 到底跑成了没有」的**唯一判读实现**。

    判为**跑过**(`engine_ran=True`,零篮子时报告可以讲市场结论)只有两种:
    - `reason_stage == ok`:推理段真跑完了 —— 零篮子 = 提案全被机械闸拦下 /
      LLM 明说今天没有,都是**真结论**;
    - `reason_stage == no_seeds` 且**不是**因为无现役包:④ 扫描层跑过、当日零
      种子 —— 「今日无热点 → 今日无篮子」是既有的合法输出(见
      `report/evening.py` SEG_SCAN 那条 note),不是缺席。

    其余一律判为**没跑成**,原因码如实带出:`aggregate_failed:*`(⑤ 整段异常)、
    `no_active_pack`(无现役选股包)、`no_provider`/`budget_exhausted`/
    `call_failed:*`/`parse_failed`(推理段缺席)、`segment_failed:*`(⑤⑥⑦ 整段
    炸在编排层)。**未知码保守判没跑成** —— 认不出来的状态不许当成"知道没有"。
    """
    notes_t = tuple(str(n) for n in notes)
    for n in notes_t:
        if n.startswith(NOTE_AGGREGATE_FAILED_PREFIX):
            return _verdict(trade_date, search_stage, reason_stage, basket_count, notes_t, n)
    if NOTE_NO_ACTIVE_PACK in notes_t:
        return _verdict(trade_date, search_stage, reason_stage, basket_count, notes_t,
                        "no_active_pack")
    if reason_stage == STAGE_OK or reason_stage == STAGE_NO_SEEDS:
        return _verdict(trade_date, search_stage, reason_stage, basket_count, notes_t, None)
    known = (
        reason_stage in (STAGE_NO_PROVIDER, STAGE_BUDGET_EXHAUSTED, STAGE_PARSE_FAILED)
        or reason_stage.startswith(STAGE_CALL_FAILED)
        or reason_stage.startswith(STAGE_SEGMENT_FAILED_PREFIX)
    )
    return _verdict(trade_date, search_stage, reason_stage, basket_count, notes_t,
                    reason_stage if known else f"unknown_stage:{reason_stage}")


def _verdict(trade_date: str, search_stage: str, reason_stage: str, basket_count: int,
             notes: Tuple[str, ...], reason_code: Optional[str]) -> BasketStageVerdict:
    return BasketStageVerdict(
        trade_date=trade_date, search_stage=str(search_stage), reason_stage=str(reason_stage),
        basket_count=int(basket_count), notes=notes,
        engine_ran=(reason_code is None), reason_code=reason_code,
    )


def save_stage_handoff(
    trade_date: date, result: Any, *, db_path: Optional[Path] = None,
) -> None:
    """把一次 ⑤ 的段状态落成一行(`INSERT OR REPLACE`,`trade_date` 主键)。

    `result`:`AggregateResult`(duck-typed —— 只读 `search_stage`/`reason_stage`/
    `baskets`/`notes` 四项,便于编排层在整段炸掉时传一个合成对象进来)。
    """
    payload_notes = [str(n) for n in (getattr(result, "notes", None) or ())]
    init_schema(db_path)
    with connection(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO basket_stage_handoff "
            "(trade_date, search_stage, reason_stage, basket_count, notes_json, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                _day(trade_date),
                str(getattr(result, "search_stage", "") or ""),
                str(getattr(result, "reason_stage", "") or ""),
                len(getattr(result, "baskets", None) or ()),
                json.dumps(payload_notes, ensure_ascii=False),
                _now(),
            ),
        )


def load_stage_verdict(
    trade_date: date, *, db_path: Optional[Path] = None,
) -> Optional[BasketStageVerdict]:
    """读回该日最近一次 ⑤ 的段状态并判读。

    **无行 → `None`**(⑤ 本次〔迄今〕没跑过,⛔ 不许猜成"跑了、今天没有");解析
    异常同样按 `None` 处理并 WARNING —— 读侧永远不比"没有这张表"更糟。
    """
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT search_stage, reason_stage, basket_count, notes_json "
            "FROM basket_stage_handoff WHERE trade_date=?",
            (_day(trade_date),),
        ).fetchone()
    if row is None:
        return None
    try:
        notes = json.loads(row[3]) if row[3] else []
    except (json.JSONDecodeError, TypeError):
        logger.warning("[basket_stage_handoff] trade_date=%s 的 notes_json 解不出,按空处理",
                       _day(trade_date))
        notes = []
    if not isinstance(notes, list):
        notes = []
    try:
        count = int(row[2])
    except (TypeError, ValueError):
        count = 0
    return stage_verdict(
        trade_date=_day(trade_date), search_stage=str(row[0] or ""),
        reason_stage=str(row[1] or ""), basket_count=count, notes=notes,
    )


__all__ = [
    "NOTE_AGGREGATE_FAILED_PREFIX", "NOTE_NO_ACTIVE_PACK", "STAGE_SEGMENT_FAILED_PREFIX",
    "BasketStageVerdict", "load_stage_verdict", "save_stage_handoff", "stage_verdict",
]
