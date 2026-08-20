"""预案条件的**唯一求值实现**(PROJECT_PLAN §5.6.3 / §5.7.2)。

9:26 竞价核对表(`auction/checklist.py`)与 10:00 结算拍(`auction/settle.py`)
**共用本模块**,⛔ 不许各写一份 —— 两份求值器意味着同一条冻结条件在同一个早上
有两种解释,而它们的分歧永远不会报错,只会让成绩单悄悄失真。

🔴 **三值逻辑,⛔ 不是布尔**:
    `TRUE`    条件确实成立;
    `FALSE`   条件确实不成立;
    `UNKNOWN` **这个量今天早上还读不到**(9:26 读不到开盘价与前 30 分钟极值;
              双源都没抓到价;昨收缺失算不出涨跌幅 ……)。
把 `UNKNOWN` 折成 `FALSE` 就是本仓栽过三次的那族病:「没判」被讲成「判过了、不成立」。
合取(K9 §6.3 的骨架全是「且」)按 Kleene:
    有 `FALSE` → 分支 `FALSE`(**即使还有读不到的项**:一条已经证伪就够了);
    否则有 `UNKNOWN` → 分支 `UNKNOWN`;
    全 `TRUE` → 分支 `TRUE`。

🔴 **零 LLM**(架构 §四):本模块不 import `neckline.llm` / `neckline.search`,
求值是毫秒级的纯算术 —— 这正是 9:29 硬截止在 K9 时代可以从「daemon 线程兜住
LLM 墙钟」简化成「一句朴素的墙钟保护」的原因(§5.7.1)。

⚠ **`成立` 与 `放弃` 同时为真时,`放弃` 赢**(见 `settle_verdict`):
失效位被跌破是**决定性**的,而两条分支同时为真只可能来自一份自相矛盾的预案
—— 那时把票判成「成立」会让系统给出一个它自己都不相信的进场信号。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Mapping, Optional, Tuple

from neckline.playbook.model import (
    EPS,
    Branch,
    BranchName,
    Condition,
    DEFAULT_BRANCH,
    MetricRef,
    Op,
    Playbook,
)


class Truth(Enum):
    """三值真值。⛔ 不许 `bool()` 它(`UNKNOWN` 会静默变成 `True`)。"""

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


#: 读数表:`MetricRef → 值`。值为 `None` 或键缺席 = **读不到**(→ `UNKNOWN`)。
Readings = Mapping[MetricRef, Optional[float]]


@dataclass(frozen=True)
class ConditionTrace:
    """一条条件的求值留痕(落库 + 报告 + 排障都读它)。"""

    condition: str          # `Condition.describe()`
    lhs: str
    op: str
    rhs: str
    lhs_value: Optional[float]
    rhs_value: Optional[float]
    truth: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "condition": self.condition, "lhs": self.lhs, "op": self.op, "rhs": self.rhs,
            "lhsValue": self.lhs_value, "rhsValue": self.rhs_value, "truth": self.truth,
        }


@dataclass(frozen=True)
class BranchOutcome:
    """一条分支的求值结果 + 逐条留痕。"""

    name: str
    truth: Truth
    traces: Tuple[ConditionTrace, ...]

    #: 读不到的量(去重、保持出现顺序)——「今天为什么判不出来」的直接答案。
    missing: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "branch": self.name, "truth": self.truth.value,
            "conditions": [t.to_dict() for t in self.traces],
            "missing": list(self.missing),
        }


def _value_of(ref: MetricRef, readings: Readings) -> Optional[float]:
    v = readings.get(ref)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _compare(op: Op, a: float, b: float) -> bool:
    """带 `EPS` 的比较(本仓房规)。⛔ `EPS` 是浮点容差,不是判据阈值。"""
    if op is Op.LE:
        return a <= b + EPS
    if op is Op.GE:
        return a >= b - EPS
    if op is Op.LT:
        return a < b - EPS
    if op is Op.GT:
        return a > b + EPS
    # `Op` 是闭合枚举,走不到这里;⛔ 但也不静默返 False —— 那会把「求值器漏了一个
    # 算子」讲成「条件不成立」。
    raise AssertionError(f"Op 闭合枚举漏了成员:{op!r}")


def evaluate_condition(cond: Condition, readings: Readings) -> Tuple[Truth, ConditionTrace]:
    """求一条条件。**全函数** —— `MetricRef` 是闭合枚举,读不到就是 `UNKNOWN`。"""
    lhs_v = _value_of(cond.lhs, readings)
    if isinstance(cond.rhs, MetricRef):
        rhs_v = _value_of(cond.rhs, readings)
        rhs_label = cond.rhs.value
    else:
        rhs_v = float(cond.rhs)
        rhs_label = f"{float(cond.rhs):g}"
    if lhs_v is None or rhs_v is None:
        truth = Truth.UNKNOWN
    else:
        truth = Truth.TRUE if _compare(cond.op, lhs_v, rhs_v) else Truth.FALSE
    return truth, ConditionTrace(
        condition=cond.describe(), lhs=cond.lhs.value, op=cond.op.value, rhs=rhs_label,
        lhs_value=lhs_v, rhs_value=rhs_v, truth=truth.value,
    )


def evaluate_branch(branch: Branch, readings: Readings) -> BranchOutcome:
    """求一条分支(合取,Kleene 三值)。**逐条都求、都留痕** —— ⛔ 不短路:
    「另外两条当时什么样」是次日排障唯一能问的问题。"""
    traces: list = []
    missing: list = []
    saw_false = False
    saw_unknown = False
    for cond in branch.all:
        truth, trace = evaluate_condition(cond, readings)
        traces.append(trace)
        if truth is Truth.FALSE:
            saw_false = True
        elif truth is Truth.UNKNOWN:
            saw_unknown = True
            for ref in (cond.lhs, cond.rhs):
                if isinstance(ref, MetricRef) and _value_of(ref, readings) is None:
                    if ref.value not in missing:
                        missing.append(ref.value)
    if saw_false:
        result = Truth.FALSE
    elif saw_unknown:
        result = Truth.UNKNOWN
    else:
        result = Truth.TRUE
    return BranchOutcome(name=branch.name.value, truth=result,
                         traces=tuple(traces), missing=tuple(missing))


# ══════════════════════════════════════════════════════════════════════════
# 三分支结算(**只有 10:00 结算拍会调它**,裁定 10)
# ══════════════════════════════════════════════════════════════════════════

class Verdict(str, Enum):
    """K9 §6.2 的三分支**终值**。

    🔴 **唯一权威是 D1 10:00 的结算拍**(裁定 10)。9:29 那张竞价核对表
    ⛔ 不产生 `CONFIRMED`,也 ⛔ 不产生 `OBSERVED` —— 它只提前告知
    「哪几只已经死了」(`REJECTED`,`decided_stage='auction'`)。
    """

    CONFIRMED = "confirmed"    # 成立
    REJECTED = "rejected"      # 放弃
    OBSERVED = "observed"      # 观察(⛔ 不进任何正确率的分子分母,K9 §八)


#: 终值 → 人话(全映射,⛔ 无 fallback)。
VERDICT_LABEL: Mapping[Verdict, str] = {
    Verdict.CONFIRMED: BranchName.CONFIRMED.value,
    Verdict.REJECTED: BranchName.REJECTED.value,
    Verdict.OBSERVED: DEFAULT_BRANCH,
}
assert set(VERDICT_LABEL) == set(Verdict)


@dataclass(frozen=True)
class SettleOutcome:
    """一只票在 10:00 那一拍的结算结果。"""

    ts_code: str
    verdict: Verdict
    confirmed: BranchOutcome
    rejected: BranchOutcome

    def to_dict(self) -> Dict[str, object]:
        return {
            "tsCode": self.ts_code, "verdict": self.verdict.value,
            "label": VERDICT_LABEL[self.verdict],
            "branches": [self.rejected.to_dict(), self.confirmed.to_dict()],
        }


def settle_verdict(playbook: Playbook, readings: Readings) -> SettleOutcome:
    """代入读数求三分支终值(K9 §6.2)。

    次序:**先看放弃**。两条同时为真时放弃赢 —— 见模块头。
    两条都不为真(含读不到)→ `OBSERVED`「观察」:K9 §6.2 原文
    「这段时间的数据看不出来」,与「条件确实都没触发」在成绩单上是同一格
    (⛔ 都不进正确率的分子分母)。
    """
    rejected = evaluate_branch(playbook.rejection_branch, readings)
    confirmed = evaluate_branch(playbook.confirmation_branch, readings)
    if rejected.truth is Truth.TRUE:
        v = Verdict.REJECTED
    elif confirmed.truth is Truth.TRUE:
        v = Verdict.CONFIRMED
    else:
        v = Verdict.OBSERVED
    return SettleOutcome(ts_code=playbook.ts_code, verdict=v,
                         confirmed=confirmed, rejected=rejected)


__all__ = [
    "Truth", "Readings", "ConditionTrace", "BranchOutcome",
    "evaluate_condition", "evaluate_branch",
    "Verdict", "VERDICT_LABEL", "SettleOutcome", "settle_verdict",
]
