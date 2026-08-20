"""报告三态(架构 §3.5,PROJECT_PLAN §5.10 / §6 S5)。

> **三种状态,每天必发其一,首行即可分辨。**

| 状态 | 触发 | 首行 |
|---|---|---|
| `has_list` 今天有这些 | 事实包已冻结 + 参数有效 + 清单 ≥1 只 | `今天有这些 · N 只(严格 a / 放宽 b)` |
| `empty` 今天没有 | 事实包已冻结 + 参数有效 + 清单 0 只 | `今天没有` |
| `not_run` 今天没跑成 | 事实包未冻结(数据未到齐)/ 参数未配置或无效 / 链路异常 | `今天没跑成 · <缺口逐条>` |

🔴 **`empty` 与 `not_run` 不可互换**(裁定 5):
「今天没有」= **跑通了、结果为空、可以被信任**;「今天没跑成」= **系统没工作**。
把参数未配置渲染成「今天没有」,等于让一句谎话每天准时到达手机 —— 空清单从此
再也不可信。单测逐条锁死这一条。

**结构性保证:全映射渲染,⛔ 无 fallback 分支。**
首行由 `_HEADLINE` 这张**三键全覆盖**的映射产出;模块加载时就断言
`set(_HEADLINE) == set(ReportState)`。⛔ 没有 `.get(state, 默认文案)`、没有
`if … elif … else 兜底`。加一个状态而忘了写它的首行 = **import 就炸**,
不是某天在手机上看见一句「未知状态」。

⚠ **参数未配置的日子照样发报告**(§5.10):清单段标「今天没跑成 · 参数未配置」,
而**方向背景、市场事实、覆盖率成绩线照常呈现**。日节奏不断,尺子照跑。
`not_run` 管的是**清单段**,不是整份报告。
"""

from __future__ import annotations

from enum import Enum
from typing import Callable, Mapping, Optional, Sequence


class ReportState(str, Enum):
    """报告三态。⛔ 只此三值 —— 加第四个必须同时补 `_HEADLINE`(见模块 docstring)。"""

    HAS_LIST = "has_list"
    EMPTY = "empty"
    NOT_RUN = "not_run"


def resolve_state(
    *,
    pack_frozen: bool,
    params_ok: bool,
    listing_count: Optional[int],
) -> ReportState:
    """三态判定。三个入参都是**必填关键字** —— ⛔ 不给默认值去猜。

    · 事实包未冻结(数据未到齐)或参数未配置 / 无效 → `NOT_RUN`;
    · 两者都成立、清单 0 只 → `EMPTY`;
    · 两者都成立、清单 ≥1 只 → `HAS_LIST`。

    ⚠ `listing_count is None` 在 `pack_frozen and params_ok` 的前提下意味着
    「链路异常,清单根本没算出来」→ 同样是 `NOT_RUN`,⛔ 不是 `EMPTY`。
    """
    if not pack_frozen or not params_ok or listing_count is None:
        return ReportState.NOT_RUN
    return ReportState.EMPTY if listing_count == 0 else ReportState.HAS_LIST


def _headline_has_list(
    listing_count: Optional[int], strict_count: Optional[int],
    relaxed_count: Optional[int], gaps: Sequence[str],
) -> str:
    tail = ""
    if strict_count is not None and relaxed_count is not None:
        tail = f"(严格 {strict_count} / 放宽 {relaxed_count})"
    return f"今天有这些 · {listing_count} 只{tail}"


def _headline_empty(
    listing_count: Optional[int], strict_count: Optional[int],
    relaxed_count: Optional[int], gaps: Sequence[str],
) -> str:
    return "今天没有"


def _headline_not_run(
    listing_count: Optional[int], strict_count: Optional[int],
    relaxed_count: Optional[int], gaps: Sequence[str],
) -> str:
    """⚠ **缺口必须逐条列出来**(架构 §3.5)——「今天没跑成」不说清缺什么,
    等于每天推一句「坏了」。"""
    detail = "、".join(g for g in gaps if g) or "缺口未知(请查服务端日志)"
    return f"今天没跑成 · {detail}"


#: 🔴 **全映射**:三个状态一个不落。⛔ 不许改成 `.get(state, …)`。
_HEADLINE: Mapping[ReportState, Callable[..., str]] = {
    ReportState.HAS_LIST: _headline_has_list,
    ReportState.EMPTY: _headline_empty,
    ReportState.NOT_RUN: _headline_not_run,
}

# 模块加载时就把「漏写一个状态的首行」变成 ImportError,⛔ 不留到运行时。
assert set(_HEADLINE) == set(ReportState), (
    f"ReportState 有 {set(ReportState) - set(_HEADLINE)} 没有首行渲染 —— "
    f"三态渲染是全映射,⛔ 不许留 fallback 分支")


def headline(
    state: ReportState,
    *,
    listing_count: Optional[int] = None,
    strict_count: Optional[int] = None,
    relaxed_count: Optional[int] = None,
    gaps: Sequence[str] = (),
) -> str:
    """报告首行。**首行即可分辨三态**(架构 §3.5)。"""
    return _HEADLINE[state](listing_count, strict_count, relaxed_count, gaps)


__all__ = ["ReportState", "resolve_state", "headline"]
