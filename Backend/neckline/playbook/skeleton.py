"""K9-v2 四通道的 D1 条件骨架(K9 §6.3)。

    形态 1 · 放量启动   成立:高开幅度 ≤ [A]%  且  前 30 分钟最低价 ≥ [B]
                        放弃:跌破 [C](昨日启动的起点)          其余:观察
    形态 2 · 超跌反弹   成立:开盘价 ≥ [A]  且  前 30 分钟不创昨日新低
                        放弃:跌破明确价位 [B]                    其余:观察
    P3 · 热门强博弈   成立:高开未越透支线 [A] 且前 30 分钟守住关键位 [B]
                       放弃:跌破强博弈结构失效位 [C]
    P4 · 资金领先价格 成立:前 30 分钟不破 [A]                    放弃:跌破 [B]

🔴 **骨架是机械的,数值是 LLM 的**(K9 §6.4 分工表):本模块**只**负责
「哪个量、哪个算子、跟谁比」;方括号里的数由 `fill.py` 逐票问模型。
⛔ 本模块里一个待标定参数都没有 —— 骨架的形状是 K9 原文给的,不是标定出来的。

⚠ **形态 2 的「不创昨日新低」是零 LLM 的**:它比的是 `first30_low >= prev_low`,
右边是**另一个 `MetricRef`** 而不是一个数(闭合语法允许 `rhs` 是 MetricRef)——
这一条模型不必填,也**不该**填。

⚠ **B2 最终裁定**:
    · 形态 1 的 `[A]%`「高开幅度」仍是**百分点**(`gap_pct <= A`);
    · 形态 2 的 `[B]` 是明确价格。系统不保存、不反算、不展示百分比。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence, Tuple

from neckline.playbook.model import (
    Branch,
    BranchName,
    Condition,
    MetricRef,
    Op,
    PlaybookInvalid,
)

#: 槽位的两种量纲(闭合)。`price` = 元,`percent` = 百分点。
KIND_PRICE = "price"
KIND_PERCENT = "percent"
KINDS: Tuple[str, ...] = (KIND_PRICE, KIND_PERCENT)


@dataclass(frozen=True)
class Slot:
    """一个待 LLM 填的数值位。⚠ **只有数值** —— ⛔ 没有「理由」「评价」这类键
    (架构 §四 第 4 条:预案层知道形态,但**不做好坏评价**)。"""

    key: str
    kind: str
    label: str          # K9 §6.3 原文里那个方括号叫什么
    hint: str           # 给模型的一句话说明(⛔ 不是判据)


@dataclass(frozen=True)
class Skeleton:
    """一个形态的骨架。`build()` 吃满一份槽位值,吐出两条分支。"""

    pattern: str
    slots: Tuple[Slot, ...]
    _confirm: Tuple[Tuple[Op, MetricRef, object], ...]
    _reject: Tuple[Tuple[Op, MetricRef, object], ...]

    def slot_keys(self) -> Tuple[str, ...]:
        return tuple(s.key for s in self.slots)

    def build(self, filled: Mapping[str, float]) -> Tuple[Branch, ...]:
        """套用骨架。**缺任何一个槽位直接抛** —— ⛔ 不许拿一份半成品预案去冻结
        (次日早上一条求不出值的条件 = 那只票白白落进「观察」)。"""
        missing = [k for k in self.slot_keys() if k not in filled]
        if missing:
            raise PlaybookInvalid(f"{self.pattern} 骨架缺槽位 {missing}")

        def _conds(spec) -> Tuple[Condition, ...]:
            out: List[Condition] = []
            for op, lhs, rhs in spec:
                out.append(Condition(
                    op=op, lhs=lhs,
                    rhs=rhs if isinstance(rhs, MetricRef) else float(filled[rhs])))
            return tuple(out)

        return (
            Branch(name=BranchName.CONFIRMED, all=_conds(self._confirm)),
            Branch(name=BranchName.REJECTED, all=_conds(self._reject)),
        )


_P1 = Skeleton(
    pattern="p1",
    slots=(
        Slot("maxGapUpPct", KIND_PERCENT, "[A]",
             "高开幅度上限(百分点):高开超过它就算追太贵,今天不算成立"),
        Slot("first30FloorPrice", KIND_PRICE, "[B]",
             "前 30 分钟最低价的下限(元):跌到它下方就不算接上了"),
        Slot("rejectPrice", KIND_PRICE, "[C]",
             "昨日启动的起点(元):跌破它即判放弃"),
    ),
    _confirm=((Op.LE, MetricRef.GAP_PCT, "maxGapUpPct"),
              (Op.GE, MetricRef.FIRST30_LOW, "first30FloorPrice")),
    _reject=((Op.LT, MetricRef.FIRST30_LOW, "rejectPrice"),),
)

_P2 = Skeleton(
    pattern="p2",
    slots=(
        Slot("minOpenPrice", KIND_PRICE, "[A]",
             "开盘价的下限(元):低于它说明止跌没成立"),
        Slot("rejectPrice", KIND_PRICE, "[B]",
             "明确放弃价位(元);跌破它即判放弃，不生成百分比"),
    ),
    # 「前 30 分钟不创昨日新低」= `first30_low >= prev_low`,**零 LLM**(见模块头)。
    _confirm=((Op.GE, MetricRef.OPEN_PRICE, "minOpenPrice"),
              (Op.GE, MetricRef.FIRST30_LOW, MetricRef.PREV_LOW)),
    _reject=((Op.LT, MetricRef.FIRST30_LOW, "rejectPrice"),),
)


_P3 = Skeleton(
    pattern="p3",
    slots=(
        Slot("maxGapUpPct", KIND_PERCENT, "[A]",
             "逐票透支线上限(百分点):高开超过它说明热门博弈已被过度定价"),
        Slot("first30FloorPrice", KIND_PRICE, "[B]",
             "前 30 分钟必须守住的强博弈关键位(元)"),
        Slot("rejectPrice", KIND_PRICE, "[C]",
             "强博弈结构失效位(元):跌破即判放弃"),
    ),
    _confirm=((Op.LE, MetricRef.GAP_PCT, "maxGapUpPct"),
              (Op.GE, MetricRef.FIRST30_LOW, "first30FloorPrice")),
    _reject=((Op.LT, MetricRef.FIRST30_LOW, "rejectPrice"),),
)


def _p4_ambush() -> Skeleton:
    """P4 资金领先价格的埋伏型骨架。

    资金信号是 D0 盘后事实，D1 早盘无法再观测；成立只要求资金进场后价格不破位。"""
    return Skeleton(
        pattern="p4",
        slots=(
            Slot("first30FloorPrice", KIND_PRICE, "[A]",
                 "前 30 分钟不破的价位(元):守住它就算没出事"),
            Slot("rejectPrice", KIND_PRICE, "[B]",
                 "放弃价位(元):跌破它即判放弃"),
        ),
        _confirm=((Op.GE, MetricRef.FIRST30_LOW, "first30FloorPrice"),),
        _reject=((Op.LT, MetricRef.FIRST30_LOW, "rejectPrice"),),
    )


#: 四个形态 → 骨架。**全映射**(⛔ 无 fallback:一个没登记的形态就该当场抛,
#: 而不是悄悄套一个别的骨架)。
SKELETONS: Mapping[str, Skeleton] = {
    "p1": _P1,
    "p2": _P2,
    "p3": _P3,
    "p4": _p4_ambush(),
}


def skeleton_for(pattern: str) -> Skeleton:
    sk = SKELETONS.get(pattern)
    if sk is None:
        raise PlaybookInvalid(
            f"形态 `{pattern}` 没有骨架;K9 §6.3 只定义了 {sorted(SKELETONS)}")
    return sk


#: 三个价位的槽位(所有形态共有,K9 §6.1)。
LEVEL_SLOTS: Tuple[Slot, ...] = (
    Slot("firstResistance", KIND_PRICE, "第一压力位",
         "预期离场价(元)—— 是否涨到它,是判断这次选股对错的标准"),
    Slot("secondResistance", KIND_PRICE, "第二压力位",
         "走势超预期时的第二目标(元),必须高于第一压力位"),
    Slot("invalidation", KIND_PRICE, "失效位",
         "跌破即证明原判断错误的价位(元),必须低于第一压力位"),
)


def required_keys(pattern: str) -> Tuple[str, ...]:
    """一份完整预案要模型给的**全部**数值键(三个价位 + 该形态的槽位)。"""
    return tuple(s.key for s in LEVEL_SLOTS) + skeleton_for(pattern).slot_keys()


def all_slots(pattern: str) -> Tuple[Slot, ...]:
    return LEVEL_SLOTS + skeleton_for(pattern).slots


__all__ = [
    "KIND_PRICE", "KIND_PERCENT", "KINDS", "Slot", "Skeleton",
    "SKELETONS", "LEVEL_SLOTS", "skeleton_for", "required_keys", "all_slots",
]
