"""预案的 LLM 填值(V2.5.0 S10,K9 §6.4 + 架构 §八「LLM 的三个岗位」之三)。

    条件骨架 = 机械(`skeleton.py`)
    **具体数值 = LLM 逐票**(本模块)
    最终确认 = 用户盘后逐只过目、可修改(`POST /selection/{date}/stock/{code}/playbook`)

🔴 **返回体 schema 只允许数值与价位键**(§5.2 边界④ 第 2 条):
出现任何**自由文本评价键** → 校验拒绝。判据是结构性的 ——
`_validate()` 只认 `required_keys(pattern)` 那几个键,且每个值**必须是数值**;
多出来的键一律拒绝(⛔ 不是「忽略」:忽略等于默许模型往里塞评价,
下一次它就会塞得更多,而没有任何东西会报错)。

🔴 **空成功一律判失败**(§12 坑 13 / LRN-20260816-002):
上一版的血泪是「后一段 LLM 退役后,它原本负责的字段没人产出,而校验只检查了
『是个 mapping』」。这里查的是 **post-clamp 完整性**:骨架要的每一个槽位都得有
一个有限数值,少一个就整只票判失败(⛔ 不冻结半份预案)。

🔴 **⛔ 不给模型看排序位次与上方机械空间**(§5.2 边界④ / 裁定 1):
`PlaybookInput` 里根本没有 `rank` / `score` / `seat_kind` / `tier` /
`upside_room_mech*` —— 把排序用的机械空间喂进来,等于邀请模型把那个数原样吐回来
当「第一压力位」,裁定 1 要拆开的循环依赖当场复活。
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.llm.base import ChatMessage, LLMProvider
from neckline.llm.json_block import split_narrative_and_json
from neckline.llm.prompt_context import TIMELINESS_RULES, date_anchor_line
from neckline.playbook import skeleton as skeleton_mod
from neckline.playbook import store as pb_store
from neckline.playbook.model import (
    Bar,
    Levels,
    Playbook,
    PlaybookInput,
    PlaybookInvalid,
    SOURCE_LLM,
    parse_playbook,
)

logger = logging.getLogger(__name__)

PLAYBOOK_FILL_SYSTEM_PROMPT = """你是「颈线」系统的预案填值员。系统每天盘后给出一份候选清单,每只票要冻结一份
「明天怎么走算成立、怎么走算放弃」的预案 —— 你的工作是**填数**。

你要给出的是**价位与阈值**,不是评价:

① 第一压力位 —— 预期离场价。是否涨到它,是判断这次选股对错的标准。
② 第二压力位 —— 判断正确且走势超预期时的第二目标(必须高于第一压力位)。
③ 失效位 —— 跌破它即证明原判断错误(必须低于第一压力位)。
④ 该形态骨架里方括号的那几个数(材料里逐条列了要什么、什么量纲)。

压力位来自技术分析:前期高点、平台上沿、缺口、关键均线、整数关口 ——
由你看图判断哪一个才是「下一个」。

铁律(不可违反):
1. 你**只填数**。不写「建议买入」「值得关注」这类评价,也不给理由字段 ——
   系统只收数值,多给的键会被整只票判为无效。
2. 三个价位必须满足:失效位 < 第一压力位 < 第二压力位。
3. 只依据材料里给出的日K 与当日读数;材料里没有的不要猜。
4. 你**不知道**这只票排第几、得分多少 —— 材料里没有这些,请不要猜测。

""" + TIMELINESS_RULES + """

输出格式(硬约束):先写一小段自由说明(你是怎么看这几个位置的),然后另起一段,
给出一个 ```json 代码块,里面**只有**材料里点名要的那几个数值键,值一律是数字
(不带单位、不带引号)。⛔ 不要加任何其它键。
"""


@dataclass(frozen=True)
class FillResult:
    """一只票的填值结果。"""

    ts_code: str
    ok: bool
    playbook: Optional[Playbook] = None
    reason: str = ""
    narrative: str = ""
    filled_by: str = ""
    values: Mapping[str, float] = field(default_factory=dict)


def _is_number(v: Any) -> bool:
    return (not isinstance(v, bool) and isinstance(v, (int, float))
            and math.isfinite(float(v)))


def validate_fill(pattern: str, block: Any) -> Tuple[Dict[str, float], str]:
    """校验模型返回体。返回 `(values, "")` 或 `({}, 原因)`。

    三条判据(全部是**结构性**的):
        ① 必须是对象;
        ② **键集恰好等于** `required_keys(pattern)` —— 少一个 = 空成功(判失败),
           多一个 = 模型往里塞了别的东西(⛔ 不忽略,判失败);
        ③ 每个值必须是**有限数值** —— 一个字符串就够判失败(⛔ 不尝试 `float("约 12 元")`)。
    """
    if not isinstance(block, Mapping):
        return {}, "模型未给出结构化收尾"
    want = set(skeleton_mod.required_keys(pattern))
    got = set(block)
    missing = sorted(want - got)
    extra = sorted(got - want)
    if missing:
        return {}, f"缺数值键:{missing}"
    if extra:
        # 🔴 自由文本评价键正是从这里进来的 —— 拒绝,⛔ 不是忽略。
        return {}, f"出现了 schema 之外的键(预案层只收数值):{extra}"
    bad = sorted(k for k in want if not _is_number(block[k]))
    if bad:
        return {}, f"这些键的值不是数值:{bad}"
    return {k: float(block[k]) for k in want}, ""


def assemble(
    item: PlaybookInput, values: Mapping[str, float], *,
    trade_date: date, version: int, filled_by: str, source: str = SOURCE_LLM,
) -> Playbook:
    """骨架(机械)+ 数值(LLM / 用户)→ 一份完整预案。

    ⚠ 最后一定要过一遍 `parse_playbook` —— 那里才是价位次序、闭合枚举、
    「恰好两条分支」三条硬校验的**唯一**落点(⛔ 别在这里再写一份)。"""
    pattern = item.primary_pattern
    branches = skeleton_mod.skeleton_for(pattern).build(values)
    levels = Levels(
        first_resistance=values["firstResistance"],
        second_resistance=values["secondResistance"],
        invalidation=values["invalidation"],
    )
    return parse_playbook({
        "tradeDate": trade_date.strftime("%Y%m%d"),
        "tsCode": item.ts_code,
        "pattern": pattern,
        "levels": levels.to_dict(),
        "branches": [b.to_dict() for b in branches],
        "version": version,
        "source": source,
        "filledBy": filled_by,
        "filledAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def _material(item: PlaybookInput) -> str:
    lines: List[str] = [date_anchor_line()]
    lines.append(f"标的:{item.name or ''}({item.ts_code})"
                 f",申万二级行业:{item.sw_l2_name or '未知'}")
    lines.append(f"形态:{item.primary_pattern}(命中 {'/'.join(item.patterns)})")
    lines.append(f"当日:收盘 {item.close:g}、最高 "
                 f"{item.high if item.high is not None else '未取得'}、最低 "
                 f"{item.low if item.low is not None else '未取得'}、昨收 "
                 f"{item.prev_close if item.prev_close is not None else '未取得'}")
    if item.bars:
        lines.append(f"日K(最近 {len(item.bars)} 个交易日,原始未复权,"
                     f"格式 日期/开/高/低/收/量):")
        lines.extend(
            f"  {b.trade_date} {b.open:g}/{b.high:g}/{b.low:g}/{b.close:g}/{b.vol:g}"
            for b in item.bars)
    else:
        lines.append("日K:未取得(事实包缺失)")
    lines.append("")
    lines.append("请给出下列数值键(⛔ 只给这些键,值一律是数字):")
    for s in skeleton_mod.all_slots(item.primary_pattern):
        unit = "百分点" if s.kind == skeleton_mod.KIND_PERCENT else "元"
        lines.append(f"  · {s.key}({s.label},单位:{unit}):{s.hint}")
    return "\n".join(lines)


def fill_one(
    item: PlaybookInput, *, trade_date: date, provider: Optional[LLMProvider],
    version: int = 1, transport: Optional[Any] = None, report_date: Optional[date] = None,
    pack_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> FillResult:
    """填一只票。`provider=None` → 直接失败,**零网络调用**、⛔ 不编一份预案。"""
    if provider is None:
        from neckline.llm.usage import record
        record(task="playbook", trade_date=trade_date, report_date=report_date, pack_id=pack_id, outcome="skipped",
               failure_reason="未配置可用的 LLM provider", db_path=db_path)
        return FillResult(ts_code=item.ts_code, ok=False,
                          reason="未配置可用的 LLM provider")
    messages = [
        ChatMessage(role="system", content=PLAYBOOK_FILL_SYSTEM_PROMPT),
        ChatMessage(role="user", content=_material(item)),
    ]
    started = time.monotonic()
    try:
        result = provider.chat(messages, enable_search=False, transport=transport)
    except Exception as e:  # noqa: BLE001 —— 一只票炸了只缺它自己那一份
        logger.warning("[playbook] %s 填值调用异常", item.ts_code, exc_info=True)
        from neckline.llm.usage import record
        record(task="playbook", trade_date=trade_date, report_date=report_date, pack_id=pack_id, outcome="failed",
               duration_ms=int((time.monotonic()-started)*1000), failure_reason="调用异常", db_path=db_path)
        return FillResult(ts_code=item.ts_code, ok=False, reason=f"调用异常:{e}")
    from neckline.llm.usage import record
    record(task="playbook", result=result, trade_date=trade_date, report_date=report_date, pack_id=pack_id,
           duration_ms=int((time.monotonic()-started)*1000), db_path=db_path)
    if not result.ok:
        return FillResult(ts_code=item.ts_code, ok=False,
                          reason=f"调用未成功:{result.reason}")
    narrative, block = split_narrative_and_json(result.content or "")
    filled_by = f"{result.provider}/{result.model}"
    values, why = validate_fill(item.primary_pattern, block)
    if why:
        return FillResult(ts_code=item.ts_code, ok=False, reason=why,
                          narrative=narrative, filled_by=filled_by)
    try:
        pb = assemble(item, values, trade_date=trade_date, version=version,
                      filled_by=filled_by)
    except PlaybookInvalid as e:
        # 价位次序 / 闭合枚举没过 —— **当场拒绝冻结**(§5.6.3)。
        return FillResult(ts_code=item.ts_code, ok=False, reason=str(e),
                          narrative=narrative, filled_by=filled_by, values=values)
    return FillResult(ts_code=item.ts_code, ok=True, playbook=pb, narrative=narrative,
                      filled_by=filled_by, values=values)


def build_inputs(
    trade_date: date, listing: Sequence[Mapping[str, Any]], *,
    sessions: int, parquet_dir: Optional[Path] = None, db_path: Optional[Path] = None,
) -> List[PlaybookInput]:
    """从**定稿清单** + 冻结事实包造预案层输入。

    🔴 只取 `patterns` / `primary_pattern`(骨架需要,架构 §四 第 4 条),
    ⛔ **不取** `rank` / `score` / `seat_kind` / `tier`(§5.2 边界④)——
    清单行里有这些列,但本函数一个都不读。字段集由 `PLAYBOOK_INPUT_FIELDS` 冻结。
    """
    from neckline.explain.input import build_inputs as build_explain_inputs

    codes = sorted(str(r["ts_code"]) for r in listing)
    facts = {i.ts_code: i for i in build_explain_inputs(
        trade_date, codes, sessions=sessions, parquet_dir=parquet_dir, db_path=db_path)}
    out: List[PlaybookInput] = []
    for r in sorted(listing, key=lambda x: str(x["ts_code"])):
        code = str(r["ts_code"])
        f = facts.get(code)
        out.append(PlaybookInput(
            ts_code=code, name=r.get("name"),
            patterns=tuple(r.get("patterns") or ()),
            primary_pattern=str(r["primary_pattern"]),
            sw_l2_name=r.get("sw_l2_name"),
            close=float(f.close) if (f and f.close is not None) else 0.0,
            prev_close=f.prev_close if f else None,
            high=(f.bars[-1].high if (f and f.bars) else None),
            low=(f.bars[-1].low if (f and f.bars) else None),
            bars=(f.bars if f else ()),
        ))
    return out


def fill_for_listing(
    trade_date: date, listing: Sequence[Mapping[str, Any]], *,
    provider: Optional[LLMProvider], transport: Optional[Any] = None,
    parquet_dir: Optional[Path] = None, report_date: Optional[date] = None, pack_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """给定稿清单上每只票冻一份预案。**已经有 v1 的票跳过**(⛔ 不覆盖冻结件)。

    ⚠ 逐只失败**只影响那一只**:失败的票没有预案 → 次日早上它落进
    「没有冻结预案」那一栏,如实报出来。⛔ 不编一份。
    """
    from neckline.explain.input import KLINE_SESSIONS

    existing = pb_store.load_latest(trade_date, db_path=db_path)
    items = build_inputs(trade_date, listing, sessions=KLINE_SESSIONS,
                         parquet_dir=parquet_dir, db_path=db_path)
    frozen = 0
    failed: List[str] = []
    skipped = 0
    for item in items:
        if item.ts_code in existing:
            skipped += 1
            continue
        if item.close <= 0:
            failed.append(item.ts_code)
            logger.warning("[playbook] %s 当日收盘价缺失,不填预案(⛔ 不编一份)",
                           item.ts_code)
            continue
        res = fill_one(item, trade_date=trade_date, provider=provider,
                       version=pb_store.next_version(trade_date, item.ts_code,
                                                     db_path=db_path),
                       transport=transport, report_date=report_date, pack_id=pack_id, db_path=db_path)
        if not res.ok or res.playbook is None:
            failed.append(item.ts_code)
            logger.warning("[playbook] %s 填值失败:%s", item.ts_code, res.reason)
            continue
        pb_store.save(res.playbook, db_path=db_path)
        frozen += 1
    logger.info("[playbook] %s 冻结 %d 份预案(跳过已有 %d,失败 %d)",
                trade_date, frozen, skipped, len(failed))
    return {"frozen": frozen, "skipped": skipped, "failed": failed,
            "listing": len(items)}


__all__ = [
    "PLAYBOOK_FILL_SYSTEM_PROMPT", "FillResult",
    "validate_fill", "assemble", "fill_one", "build_inputs", "fill_for_listing",
]
