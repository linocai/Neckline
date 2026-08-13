"""⑫-B 偏好画像引擎(plan §五 V2-⑫-B,蓝图 6.3)。回答「喜欢什么」——纯统计口味,
不判断好坏、不给"优势/错误"标签(那是能力画像的事,两张账分开)。

数据源 = `neckline.profile.common.load_buy_contexts`(只读 `positions` /
`entry_snapshots` / `position_plans`)+ `neckline.user_actions`(`kind='label'`,
plan ⑫-B 原文「数据源 = ⑨ 的评价引擎产出 + user_actions + positions + 对账结果」
点名的用户行为一类)。**不看卖出结果**——偏好问的是"当初怎么选的",不是"后来
赚没赚"。

**蓝图 6.3 列了五项偏好维度,本引擎实现四项半**:常买题材 / 常买角色 / 常用
入场方式(两个口径并存,见下)/ 常选 Tier。第五项「竞价与盘中确认习惯」结构性
缺席——⑩ 完工记录已如实登记 `entry_snapshots.snapshot_json` 本轮范围收窄未
采集 `auction_performance`(唯一数据源缺失,不是本模块疏漏),待 ⑩ 或后续块
补上该数据源后再补这一维度,本模块不伪造。

**「常用入场方式」并存两个口径,不是重复**:`entry_style`(`common.
classify_entry_style` 的几何口径——买入价相对建仓观察区间的位置)是**系统能
100% 覆盖**的客观分类;`entry_label`(`user_actions.kind='label'`,⑩-C 用户
可选补充的七枚标签)是**用户自己怎么描述这次入场**的主观口径,大概率稀疏
(标签是可选项,不是每笔买入都会补)。两者互补,不合并成一个维度——合并会
把"系统推断"和"用户自述"这两种性质不同的证据混在一起。

**单笔不成偏好,但仍然出行**:`common.MIN_SAMPLE_N` 门槛下的维度值**不省略**
(偏好统计本身不是一个需要证据支撑的"结论",只是一个占比事实),而是如实标
`confidence='low'`——是否弱化展示交给消费方(⑭ 的画像端点/客户端),本引擎
只管把"样本够不够"说清楚,不替消费方决定"给不给看"。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from neckline.profile.common import (
    BuyContext,
    DIM_ENTRY_LABEL,
    DIM_ENTRY_STYLE,
    DIM_ROLE,
    DIM_TIER,
    DIM_THEME,
    confidence_for,
    load_buy_contexts,
)


@dataclass(frozen=True)
class PreferenceRow:
    """对应 `profile_preference` 一行(`UNIQUE(as_of_date, dimension, value)`,
    落库时机由调用方决定 `as_of_date`——本引擎只算数,不管"哪天算的")。"""

    dimension: str
    value: str
    share: float          # 占该维度全部样本的比例(0~1)
    sample_n: int
    window_start: str
    window_end: str
    confidence: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "dimension": self.dimension, "value": self.value, "share": self.share,
            "sampleN": self.sample_n, "windowStart": self.window_start,
            "windowEnd": self.window_end, "confidence": self.confidence,
        }


def _bucket(
    contexts: Sequence[BuyContext], dimension: str, key: Callable[[BuyContext], str],
) -> List[PreferenceRow]:
    total = len(contexts)
    if total == 0:
        return []
    counts = Counter(key(c) for c in contexts)
    ws = min(c.buy_date for c in contexts)
    we = max(c.buy_date for c in contexts)
    rows: List[PreferenceRow] = []
    for value, n in sorted(counts.items()):
        rows.append(PreferenceRow(
            dimension=dimension, value=str(value), share=round(n / total, 4),
            sample_n=n, window_start=ws, window_end=we, confidence=confidence_for(n),
        ))
    return rows


def _entry_label_rows(contexts: Sequence[BuyContext], *, db_path: Optional[Path]) -> List[PreferenceRow]:
    """入场标签偏好(`user_actions.kind='label'`,⑩-C 用户可选补充)。**样本单位
    是"标签实例"而不是"买入笔数"**——一笔买入可以同时贴多个标签(`payload
    ["labels"]` 是列表,蓝图 §2.2 七枚标签不是单选),贴了两个标签的买入会给
    两个不同 label 值各计一次,不强行摊成 0.5 次。

    没有贴过任何标签的买入不参与(标签是可选项,`payload` 为空的买入天然不
    贡献样本)——**大概率稀疏,如实反映,不因为大部分买入没贴标签就拉低这一
    维度自己的样本量**(它的分母是"贴过标签的实例数",不是"全部买入数")。"""
    from neckline.user_actions import list_actions

    contexts_by_pid: Dict[int, BuyContext] = {c.position_id: c for c in contexts}
    if not contexts_by_pid:
        return []
    counts: Counter = Counter()
    touched_dates: List[str] = []
    for row in list_actions(kind="label", db_path=db_path):
        ctx = contexts_by_pid.get(row.get("position_id"))
        if ctx is None:
            continue   # 标签挂在窗口外的持仓上,不属于本期
        labels = (row.get("payload") or {}).get("labels") or []
        if not labels:
            continue
        for label in labels:
            counts[str(label)] += 1
        touched_dates.append(ctx.buy_date)
    total = sum(counts.values())
    if total == 0:
        return []
    ws, we = min(touched_dates), max(touched_dates)
    return [
        PreferenceRow(
            dimension=DIM_ENTRY_LABEL, value=value, share=round(n / total, 4),
            sample_n=n, window_start=ws, window_end=we, confidence=confidence_for(n),
        )
        for value, n in sorted(counts.items())
    ]


def compute_preference(
    window_start: str, window_end: str, *, db_path: Optional[Path] = None,
) -> List[PreferenceRow]:
    """`[window_start, window_end]`(按买入日,'YYYYMMDD')区间内的偏好画像。
    区间内零买入 → 空列表(如实反映"这期没有数据",不是异常)。"""
    contexts = load_buy_contexts(window_start, window_end, db_path=db_path)
    rows: List[PreferenceRow] = []
    rows += _bucket(contexts, DIM_THEME, lambda c: c.theme_value)
    rows += _bucket(contexts, DIM_ROLE, lambda c: c.role_value)
    rows += _bucket(contexts, DIM_ENTRY_STYLE, lambda c: c.entry_style)
    rows += _bucket(contexts, DIM_TIER, lambda c: c.tier_label)
    rows += _entry_label_rows(contexts, db_path=db_path)
    return rows


__all__ = ["PreferenceRow", "compute_preference"]
