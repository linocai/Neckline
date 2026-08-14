"""预算三本账(plan §五 V2-② / §3.10-B):检索 / 推理 / 复盘三条预算**独立、不共享、
不合并**——一个吃光另一个是最难查的那类故障(承 v1.5-②「候选审判独立墙钟预算」
定案的同一条精神,只是从"一本账"扩成"三本账各自独立")。

**降级次序定死**:预算耗尽时丢 T2 复盘细节(**V2.1-② T3 全链退役后,可丢清单只剩
这一项**),**篮子卡冻结与纪律外壳永远不在可丢清单里**——它们不受 LLM 预算支配
(前者是机械冻结产物,后者是章程,两者都不经过本模块)。**T1 从来、现在、以后都
不在可丢清单里**。

本模块**只提供记账与次序原语**,不做实际的"调用/跳过某个篮子的复盘"决策——那是
消费方(⑨ 盘后复盘引擎等)的职责,读 `next_to_drop()` 决定跳过谁。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple

# 三本账默认预算(秒)。晚间管线三段化(§3.10-D)后,各段各自 new 一个
# `BudgetLedger`——**不是进程级全局单例**,全局单例会让"重跑一次报告"与"当天
# 正常运行一次"共享同一账本,产生跨批次的虚假透支。三个常量可分别被单测
# monkeypatch,互不牵连(见 `BudgetLedger.limits` 的 `default_factory`,取值发生
# 在实例化那一刻,不是模块加载那一刻)。
SEARCH_BUDGET_SECONDS: float = 20 * 60.0   # T1/T2 驱动证据检索(带联网,单只慢)
REASON_BUDGET_SECONDS: float = 30 * 60.0   # 篮子逻辑/Tier 微调/剧本等推理任务
# ⚠ **V2.1-② 不下调**(算过了,不是没算):篮子上限 17→7 让复盘调用数上界从 17 降到
# 7,但预算账是"防跑飞的上限"不是"预期用量";按次计费与墙钟预算无关,调小它换不来
# 钱,却会在慢夜把正常调用掐掉(plan §五 V2.1 附「成本与超时算术」第 4 条)。
REVIEW_BUDGET_SECONDS: float = 15 * 60.0   # 盘后复盘解释(T1/T2 全部 full 深度)

LEDGER_SEARCH = "search"
LEDGER_REASON = "reason"
LEDGER_REVIEW = "review"
_LEDGERS: Tuple[str, ...] = (LEDGER_SEARCH, LEDGER_REASON, LEDGER_REVIEW)


def _default_limits() -> Dict[str, float]:
    # 读**当前**模块级常量(不是定义时闭包捕获的值),使 monkeypatch 单个常量
    # 对新建的 `BudgetLedger` 立即生效——闭包在调用时才求值,天然满足这一点。
    return {LEDGER_SEARCH: SEARCH_BUDGET_SECONDS, LEDGER_REASON: REASON_BUDGET_SECONDS,
            LEDGER_REVIEW: REVIEW_BUDGET_SECONDS}


@dataclass
class BudgetLedger:
    """一次批处理运行的预算账本。三个子账各自独立计数,`spend()` 只碰自己那本账,
    不会「借用」或「透支」到另一本——这正是「互不透支」单测要锁的行为。"""

    limits: Dict[str, float] = field(default_factory=_default_limits)
    spent: Dict[str, float] = field(default_factory=lambda: {k: 0.0 for k in _LEDGERS})
    # Token limits have no production defaults in V2.4.2: the direction
    # pipeline must supply its approved budget explicitly.  `None` means this
    # generic legacy ledger does not enforce tokens, never that tokens are
    # free or estimated.
    token_limits: Dict[str, Optional[int]] = field(
        default_factory=lambda: {k: None for k in _LEDGERS}
    )
    token_spent: Dict[str, int] = field(default_factory=lambda: {k: 0 for k in _LEDGERS})
    usage_unavailable: Dict[str, bool] = field(default_factory=lambda: {k: False for k in _LEDGERS})

    def _check(self, ledger: str) -> None:
        if ledger not in self.limits:
            raise ValueError(f"未知预算账:{ledger!r}(仅允许 {_LEDGERS})")

    def spend(self, ledger: str, seconds: float) -> None:
        self._check(ledger)
        self.spent[ledger] = self.spent.get(ledger, 0.0) + max(0.0, float(seconds))

    def remaining(self, ledger: str) -> float:
        self._check(ledger)
        return max(0.0, self.limits[ledger] - self.spent.get(ledger, 0.0))

    def exhausted(self, ledger: str) -> bool:
        return self.remaining(ledger) <= 0.0

    def record_call(self, ledger: str, result: Any, wall_seconds: float) -> bool:
        """Record wall time and actual provider usage for one completed call.

        Returns false when the provider did not expose complete token usage.
        That is an accounting fault for any caller enforcing a token budget;
        it is intentionally not patched with an estimate.
        """
        self._check(ledger)
        self.spend(ledger, wall_seconds)
        total = getattr(result, "total_tokens", None)
        unavailable = bool(getattr(result, "usage_unavailable", True))
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            unavailable = True
        if unavailable:
            self.usage_unavailable[ledger] = True
            return False
        self.token_spent[ledger] = self.token_spent.get(ledger, 0) + total
        return True

    def token_remaining(self, ledger: str) -> Optional[int]:
        self._check(ledger)
        limit = self.token_limits.get(ledger)
        if limit is None:
            return None
        return max(0, int(limit) - self.token_spent.get(ledger, 0))

    def token_exhausted(self, ledger: str) -> bool:
        remaining = self.token_remaining(ledger)
        return remaining is not None and remaining <= 0


# —— 降级次序(定死,plan §五 V2-② / V2.1-②)————————————————————————————————
# 预算不足时被丢的只有:T2 复盘细节。**这一项之外的任何东西都不许出现在这个元组里**
# ——尤其是篮子卡冻结(D0 产物,机械冻结,不经 LLM 预算)与纪律外壳(章程,§2.1,
# LLM 说什么都不改它,§2.0 第〇原则),以及 T1 复盘(从来不在可丢清单里)。
#
# ⚠ **V2.1-②:`DROP_T3_BRIEF` 已删除**(T3 全链退役,不留影子档)——反向 hasattr
# 守门在 `tests/test_llm_router_budget.py`,⛔ 别顺手加回来。历史 `basket_review_daily`
# 里 `llm_skip_reason='llm_dropped:t3_brief'` 的旧行仍能原样读回(那是**值**层面的
# "停写留档",不需要常量还活着)。
DROP_T2_REVIEW_DETAIL = "t2_review_detail"   # Tier-2 篮子复盘的细节展开

DEGRADE_ORDER: Tuple[str, ...] = (DROP_T2_REVIEW_DETAIL,)

# 永不可丢清单(仅用于自证 + 单测断言,不参与任何运行时判断——`DEGRADE_ORDER`
# 本身从一开始就不包含它们,这里只是把"不许包含"这条断言变成机器可查的常量)。
NEVER_DROPPED: Tuple[str, ...] = ("basket_card_freeze", "discipline_shell")


def next_to_drop(already_dropped: Iterable[str]) -> Optional[str]:
    """给定已经丢弃的项集合,返回**下一个**该丢的项(`DEGRADE_ORDER` 中第一个还
    没被丢的);全部丢完 → `None`(意味着连 T2 细节都保不住了,但 `DEGRADE_ORDER`
    的边界仍然是「只到这一项为止」,调用方不应该、也无法从本函数问出"再丢点别的"
    ——没有别的可丢)。"""
    dropped = set(already_dropped)
    for item in DEGRADE_ORDER:
        if item not in dropped:
            return item
    return None


__all__ = [
    "SEARCH_BUDGET_SECONDS",
    "REASON_BUDGET_SECONDS",
    "REVIEW_BUDGET_SECONDS",
    "LEDGER_SEARCH",
    "LEDGER_REASON",
    "LEDGER_REVIEW",
    "BudgetLedger",
    "DROP_T2_REVIEW_DETAIL",
    "DEGRADE_ORDER",
    "NEVER_DROPPED",
    "next_to_drop",
]
