"""排名与百分位的**唯一实现**(K9 §四 / PROJECT_PLAN §5.4.6)。

通道与排序层都要排名,而通道之间⛔ 不许互相 import(架构 §二 边界②)、通道也⛔ 不许
import `ranking`(守门 G4)—— 所以这段共用计算住在这里,谁都能拿,谁都拿不到别人的产物。

**口径定死两条,⛔ 不许各处各写一套**:

1. **百分位 `pct_rank` ∈ [0, 1],1 = 最强**(值最大者)。空集 / 全 null → 空表。
2. **并列取平均名次**(§5.4.6 逐字)—— 同一个读数拿到不同名次是纯粹的实现噪声,
   会让「同包同参跑两遍逐字节相等」变成一句空话。

⚠ **`None` 不参与排名,也不被当成 0**:算不出来的读数返回 `None`,调用方必须
显式处理(⛔ 不许 `fill_null(0)` —— 那是把「不知道」讲成「最差」)。
"""

from __future__ import annotations

from typing import Dict, Mapping, Optional, Sequence, Tuple


def pct_rank(values: Mapping[str, Optional[float]]) -> Dict[str, float]:
    """`key → 值` → `key → 百分位`(1 = 最强,并列取平均名次)。

    `None` 的键**不出现在结果里**。只有一个有效值时它拿 1.0
    (它既是最强也是最弱;⛔ 不给 0 —— 唯一的样本不该被判为最差)。
    """
    usable: Sequence[Tuple[str, float]] = sorted(
        ((k, float(v)) for k, v in values.items() if v is not None),
        key=lambda kv: (kv[1], kv[0]),          # 升序;并列时按 key 定序保证确定性
    )
    n = len(usable)
    if n == 0:
        return {}
    if n == 1:
        return {usable[0][0]: 1.0}

    out: Dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and usable[j + 1][1] == usable[i][1]:
            j += 1
        # 升序下标 i..j 并列 → 平均名次(0 起)→ 归一到 [0,1],1 = 最大值。
        avg_idx = (i + j) / 2
        score = avg_idx / (n - 1)
        for k in range(i, j + 1):
            out[usable[k][0]] = score
        i = j + 1
    return out


def in_top_fraction(rank_pct: Optional[float], fraction: float) -> bool:
    """「排名前 X%」的**唯一判据**。`fraction` 是**比例**(0~1),如 0.1 = 前 10%。

    百分位 1 = 最强,所以「前 10%」就是 `rank_pct >= 1 − 0.1 = 0.9`。
    `None`(没参与排名)→ **False**,⛔ 不是 True:算不出排名的票不该被当成领先。
    """
    if rank_pct is None:
        return False
    if not 0 < fraction < 1:
        raise ValueError(f"「前百分之几」必须落在 (0,1) 开区间,收到 {fraction}")
    return rank_pct >= 1.0 - fraction


__all__ = ["pct_rank", "in_top_fraction"]
