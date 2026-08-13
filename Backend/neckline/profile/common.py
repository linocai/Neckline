"""偏好 / 能力两个画像引擎共用的面板装配 + 工程常量(plan §五 V2-⑫-B)。

`load_buy_contexts` 是**唯一**的"一次买入长什么样"读取入口——只读
`positions` / `entry_snapshots` / `position_plans`(version=1)三张表 + `stock_basic`
(查行业,单一权威,不走 `entry_snapshots.snapshot_json` 里可能缺失的嵌套字段)。
偏好引擎与能力引擎都从这里取数,不各写一份查询(同 `eval.metrics.load_basket_panel`
「面板装配与指标计算分层」的既定体例)。

**样本量 / 置信度门槛是本模块的工程常量,不进策略包也不进章程**(plan 原文
「最小样本量常量,住 profile/ 模块」)——登记体例照 `selection/verification_rules.py`
/ `sentinel/attention.py::ATTENTION_DEFAULTS`:零回测背书,只是防止在 N=1 的样本上
说"这是你的偏好"。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from neckline.db import connection, init_schema

logger = logging.getLogger(__name__)

_EPS = 1e-9

# ══════════════════════════════════════════════════════════════════════════
# ⚠ 样本量 / 置信度阈值(工程默认,零回测背书——登记体例照 attention.py)
# ══════════════════════════════════════════════════════════════════════════
#
# 下面两个数字只是为了让「样本不足」这道闸能跑起来而拟的占位值,**没有任何统计
# 显著性检验支持它们**(同 `eval.metrics.MIN_CONCLUSION_DAYS` 的诚实边界精神)。
# 两套引擎的样本量量级天差地别——复盘按"交易日数"计,画像按"这个人一共买过几次"
# 计——**不共用同一个门槛数字**,各自独立登记。
#     · **不进选股策略包**(`selection_packs`)——不是选股判据;
#     · **不进纪律章程**(`strategy_versions`)——不是纪律;
#     · **不影响任何判定**——调大调小只改"这条画像给不给结论",不改任何 Tier /
#       排序 / 哨兵判定(蓝图 4.4 禁令,守门单测见 `tests/test_profile_guardrails.py`)。
MIN_SAMPLE_N = 5                 # 单笔不成偏好/结论(plan §五 V2-⑫-B 原文用词)
MEDIUM_SAMPLE_N = MIN_SAMPLE_N * 4   # low/medium/high 三档置信度的中档门槛

PROFILE_DEFAULTS: Dict[str, int] = {
    "min_sample_n": MIN_SAMPLE_N,
    "medium_sample_n": MEDIUM_SAMPLE_N,
}

CONFIDENCE_LOW = "low"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_HIGH = "high"


def confidence_for(n: int) -> str:
    """样本量 → 置信度三档(单调:`n` 越大置信度越高,边界处 `n==阈值` 算达标一侧
    ——同项目纪律阈值比较 `_EPS` 容差体例,这里用整数比较不需要浮点容差)。"""
    if n < MIN_SAMPLE_N:
        return CONFIDENCE_LOW
    if n < MEDIUM_SAMPLE_N:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_HIGH


# ══════════════════════════════════════════════════════════════════════════
# 维度名唯一源(偏好/能力两张账共用同一套维度定义——同一个 dimension+value 在
# 两张表里都能查到,方便"这个偏好是不是优势"的配对读法,不是巧合)
# ══════════════════════════════════════════════════════════════════════════

DIM_THEME = "theme"                  # 常买题材(行业,`stock_basic.industry`)
DIM_ROLE = "role"                    # 常买角色(leader/core/elastic/independent)
DIM_ENTRY_STYLE = "entry_style"      # 常用入场方式(几何口径:买入价相对建仓区间——区间内/追高/低吸/无参考)
DIM_ENTRY_LABEL = "entry_label"      # 常用入场方式(用户自述口径:⑩-C `user_actions.kind='label'` 七枚标签)
DIM_TIER = "tier"                    # 常选 Tier(T1/T2/T3/independent)

#: 能力画像专属维度(⑫-B「哪些机会经常被错误忽略」)——描述**未被用户选中**的
#: 篮子成员自身表现,与「用户自己选了什么」的 `DIM_ROLE` 行分开命名,不混在一起
#: (两者回答完全不同的问题,合并成一个 dimension 会让消费方分不清谁是谁)。
DIM_MISSED_ROLE = "missed_role"

ROLE_INDEPENDENT = "independent"      # 无来源篮子(独立买入,entry_snapshots.role 为 NULL)
TIER_INDEPENDENT = "independent"      # 同上,tier 为 NULL

ENTRY_STYLE_WITHIN_ZONE = "within_zone"       # 买入价落在卡上建仓观察区间内
ENTRY_STYLE_CHASED_ABOVE = "chased_above"     # 买入价高于区间上沿(追高)
ENTRY_STYLE_BELOW_ZONE = "below_zone"         # 买入价低于区间下沿(更低吸)
ENTRY_STYLE_NO_REFERENCE = "no_reference"     # 无区间可比(独立买入 / 计划未就绪)


def classify_entry_style(buy_price: float, entry_zone: Optional[Mapping[str, Any]]) -> str:
    """入场方式四分类。`entry_zone` 取自 `position_plans.plan_json.entry_zone`
    (⑦ 夹逼闸给出的建仓观察区间,不在本函数另造阈值——同
    `positions_entry.evaluate_entry_deviation` 的既定口径,只是那边产出的是一句
    展示文案,这里产出的是可聚合的分类值)。"""
    if not isinstance(entry_zone, Mapping):
        return ENTRY_STYLE_NO_REFERENCE
    low, high = entry_zone.get("low"), entry_zone.get("high")
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return ENTRY_STYLE_NO_REFERENCE
    if buy_price > high + _EPS:
        return ENTRY_STYLE_CHASED_ABOVE
    if buy_price < low - _EPS:
        return ENTRY_STYLE_BELOW_ZONE
    return ENTRY_STYLE_WITHIN_ZONE


# ══════════════════════════════════════════════════════════════════════════
# 面板装配:一次买入的画像输入面板
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class BuyContext:
    """一次买入的画像输入(偏好 + 能力共用),只读 `positions` / `entry_snapshots`
    / `position_plans`(version=1)三张表拼来。"""

    position_id: int
    ts_code: str
    buy_date: str                     # 'YYYYMMDD'
    buy_price: float
    qty: int
    status: str                       # open | closed
    sell_price: Optional[float]
    sell_date: Optional[str]          # 'YYYYMMDD' | None
    buy_fees: Optional[float]
    sell_fees: Optional[float]
    close_reason: Optional[str]
    basket_id: Optional[int]          # None = 独立买入(⑩ 查无来源篮子)
    tier: Optional[int]
    role: Optional[str]
    industry: Optional[str]
    entry_zone: Optional[Dict[str, Any]]

    @property
    def entry_style(self) -> str:
        return classify_entry_style(self.buy_price, self.entry_zone)

    @property
    def role_value(self) -> str:
        return self.role or ROLE_INDEPENDENT

    @property
    def tier_label(self) -> str:
        return f"T{self.tier}" if self.tier else TIER_INDEPENDENT

    @property
    def theme_value(self) -> str:
        return self.industry or "(未知行业)"

    @property
    def net_pnl(self) -> Optional[float]:
        """已平仓才有净盈亏(计双边费用,费用缺录按 0 兜底——`positions.buy_fees`/
        `sell_fees` 本就是"补录"字段,允许为 NULL,同 `sentinel.positions.Position`
        docstring 既定语义)。未平仓 → `None`,不是 0(仍在变化的数不能贴结论标签)。"""
        if self.status != "closed" or self.sell_price is None:
            return None
        fees = (self.buy_fees or 0.0) + (self.sell_fees or 0.0)
        return round((self.sell_price - self.buy_price) * self.qty - fees, 2)


def _load_entry_zone(plan_json_text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not plan_json_text:
        return None
    try:
        plan = json.loads(plan_json_text)
    except (TypeError, ValueError):
        return None
    zone = plan.get("entry_zone") if isinstance(plan, dict) else None
    return zone if isinstance(zone, dict) else None


def load_buy_contexts(
    window_start: str, window_end: str, *, db_path: Optional[Path] = None,
) -> List[BuyContext]:
    """按 `buy_date` 落在 `[window_start, window_end]`(闭区间,'YYYYMMDD' 字符串
    字典序比较,与 `positions.buy_date` 存储格式同口径)的全部持仓(含未平仓)。

    排序 `(buy_date, position_id)` —— 确定性,重跑结果逐位可比。空区间 / 无数据
    → 空列表(正常场景,不是异常)。"""
    init_schema(db_path)
    out: List[BuyContext] = []
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, ts_code, buy_price, qty, buy_date, status, sell_price, sell_date, "
            "buy_fees, sell_fees, close_reason FROM positions "
            "WHERE buy_date >= ? AND buy_date <= ? ORDER BY buy_date, id",
            (window_start, window_end),
        ).fetchall()
        if not rows:
            return out
        ids = [int(r[0]) for r in rows]
        marks = ",".join("?" * len(ids))

        snap: Dict[int, Tuple[Optional[int], Optional[int], Optional[str]]] = {}
        for pid, basket_id, tier, role in conn.execute(
            f"SELECT position_id, basket_id, tier, role FROM entry_snapshots "
            f"WHERE position_id IN ({marks})", ids,
        ).fetchall():
            snap[int(pid)] = (
                int(basket_id) if basket_id is not None else None,
                int(tier) if tier is not None else None,
                role,
            )

        entry_zones: Dict[int, Optional[Dict[str, Any]]] = {}
        for pid, plan_json in conn.execute(
            f"SELECT position_id, plan_json FROM position_plans "
            f"WHERE position_id IN ({marks}) AND version=1", ids,
        ).fetchall():
            entry_zones[int(pid)] = _load_entry_zone(plan_json)

        codes = sorted({str(r[1]) for r in rows})
        industry_by_code: Dict[str, Optional[str]] = {}
        if codes:
            cmarks = ",".join("?" * len(codes))
            for code, industry in conn.execute(
                f"SELECT ts_code, industry FROM stock_basic WHERE ts_code IN ({cmarks})", codes,
            ).fetchall():
                if industry:
                    industry_by_code[str(code)] = str(industry)

    for r in rows:
        pid = int(r[0])
        basket_id, tier, role = snap.get(pid, (None, None, None))
        out.append(BuyContext(
            position_id=pid, ts_code=str(r[1]), buy_price=float(r[2]), qty=int(r[3]),
            buy_date=str(r[4]), status=str(r[5]),
            sell_price=(float(r[6]) if r[6] is not None else None), sell_date=r[7],
            buy_fees=(float(r[8]) if r[8] is not None else None),
            sell_fees=(float(r[9]) if r[9] is not None else None),
            close_reason=r[10],
            basket_id=basket_id, tier=tier, role=role,
            industry=industry_by_code.get(str(r[1])),
            entry_zone=entry_zones.get(pid),
        ))
    return out


__all__ = [
    "MIN_SAMPLE_N", "MEDIUM_SAMPLE_N", "PROFILE_DEFAULTS",
    "CONFIDENCE_LOW", "CONFIDENCE_MEDIUM", "CONFIDENCE_HIGH", "confidence_for",
    "DIM_THEME", "DIM_ROLE", "DIM_ENTRY_STYLE", "DIM_ENTRY_LABEL", "DIM_TIER", "DIM_MISSED_ROLE",
    "ROLE_INDEPENDENT", "TIER_INDEPENDENT",
    "ENTRY_STYLE_WITHIN_ZONE", "ENTRY_STYLE_CHASED_ABOVE", "ENTRY_STYLE_BELOW_ZONE",
    "ENTRY_STYLE_NO_REFERENCE", "classify_entry_style",
    "BuyContext", "load_buy_contexts",
]
