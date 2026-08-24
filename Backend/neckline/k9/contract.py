"""策略契约三条的代码化(架构 §3.2,PROJECT_PLAN §5.4.2)。

| 契约 | 代码化 |
|---|---|
| **声明依赖** | `DECLARED_FIELDS` —— 策略只能通过 `PackRange.field(name)` 取列,名字不在声明集里**直接抛**;`load_pack_range` 按声明集做列投影(⚠ §12 坑 1:列投影是必填的,不是可选优化)。 |
| **产出署名清单** | `Shortlist(strategy='K9', params_version, pack_version, entries=[Entry(...)])`,每只票标明**由哪个通道召回**。 |
| **取数唯一来源是事实包** | `PackRange` 是策略层**唯一**的数据入口;`k9/**` 不 import `tushare_client` / `market_data`(守门 G3)。 |

🔴 **四个通道的签名固定为 `run(pack_range, params) -> list[ChannelHit]`**(架构 §二 边界②)。
通道拿不到别人的产物 —— 参数里没有别的通道的结果,`PackRange` 里也没有。
`k9/run.py` 是**唯一**同时看见四个产物的地方。

⚠ **单位约定**(标定侧必读,⛔ 别猜):
    · 名字里带 `Pct` 且落在 `params._UNIT_INTERVAL_PATHS` 的键是**比例**(0~1),
      如 `activityMinPercentile=0.2` 表示第 20 百分位;
    · 其余带 `Pct` 的键是**百分点**,如 `minAmplitudePct=25.0` 表示 25%、
      `spikeFadeGapPct=3.0` 表示 3 个点。
  两组各自有校验(见 `params.py` 的 `_UNIT_INTERVAL_PATHS` / `_PERCENT_POINT_PATHS`)。
  事实包里的 `ret_1d` / `amp_1d` 是**比例**,与百分点比较前一律 `* 100`(本模块的
  `to_percent_points()` 是唯一转换处,⛔ 不许各处各乘一次)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, Mapping, Optional, Sequence, Tuple

import polars as pl

from neckline.facts.pack import PACK_COLUMNS


class Pattern(str, Enum):
    """K9 §三 的四个形态。**闭合枚举** —— ⛔ 不许现编字符串。"""

    P1 = "p1"       # 放量启动
    P2 = "p2"       # 超跌反弹
    P3 = "p3"       # 热门强博弈
    P4 = "p4"       # 资金领先价格


#: 形态的**定序**(K9 §五-4 保底席位并列时的定序键;⛔ 不是优先级)。
PATTERN_ORDER: Tuple[Pattern, ...] = (Pattern.P1, Pattern.P2, Pattern.P3, Pattern.P4)

#: 形态的人话名(报告 / 归因用)。全映射,⛔ 无 fallback。
PATTERN_LABEL: Mapping[Pattern, str] = {
    Pattern.P1: "放量启动",
    Pattern.P2: "超跌反弹",
    Pattern.P3: "热门强博弈",
    Pattern.P4: "资金领先价格",
}
assert set(PATTERN_LABEL) == set(Pattern)


class Tier(str, Enum):
    """K9 §五-6 的分档。`strict` 通不过才看 `relaxed`;成色随票标注(§五-7)。"""

    STRICT = "strict"
    RELAXED = "relaxed"


class SeatKind(str, Enum):
    """席位来源(K9 §五-2 / §五-3)。"""

    FLOOR = "floor"      # 保底席位:当日有候选的形态各先占 1 席
    FREE = "free"        # 自由竞争:剩余席位按总分统一分配


# ══════════════════════════════════════════════════════════════════════════
# 契约一 · 声明依赖
# ══════════════════════════════════════════════════════════════════════════

#: 🔴 **K9 读事实包的全部字段**(架构 §3.2 契约一:「明确列出它读取事实包的哪些字段」)。
#: 事实包变更时据此得知哪些策略受影响;`load_pack_range` 也按它做列投影。
#: ⛔ 加一列必须先加进这里 —— `PackRange.field()` 会当场拒绝未声明的列名,
#: 这不是提醒,是一道抛异常的闸。
DECLARED_FIELDS: frozenset = frozenset({
    "trade_date",
    # 身份 / 边界(K9 §二 9 条)
    "ts_code", "name", "board", "list_date", "is_st", "suspend_flag",
    # 申万二级(裁定 3;行业热度分与成绩线的冻结绑定都要)
    "sw_l2_code", "sw_l2_name",
    # 价量
    "open", "high", "low", "close", "pre_close", "vol", "amount",
    # 当日衍生
    "ret_1d", "amp_1d", "limit_up_price", "limit_down_price", "is_limit_up", "is_limit_down",
    "consec_limit_up_days",
    # 有效活跃度 / 放量 / P3 可选证据
    "turnover_rate", "circ_mv", "top_list_state", "top_list_hit",
    # 资金流(形态 4)
    "net_amount",
    # 行业相对(裁定 2)
    "sw_l2_median_ret", "rel_strength_1d",
})

_UNDECLARED = sorted(set(DECLARED_FIELDS) - set(PACK_COLUMNS))
assert not _UNDECLARED, f"DECLARED_FIELDS 里有事实包没有的列:{_UNDECLARED}"


def to_percent_points(ratio_expr: pl.Expr) -> pl.Expr:
    """比例 → 百分点的**唯一**转换处(见模块 docstring 的单位约定)。"""
    return ratio_expr * 100.0


# ══════════════════════════════════════════════════════════════════════════
# 策略层唯一的数据入口
# ══════════════════════════════════════════════════════════════════════════

class UndeclaredField(KeyError):
    """策略碰了一个没在 `DECLARED_FIELDS` 里声明的列(契约一)。"""


@dataclass(frozen=True)
class PackRange:
    """`[start, as_of]` 区间的事实包(长表:一行 = 一天 × 一只票)。

    **策略层看得见的一切都从这里来。** 构造由 `k9/run.py` 完成(它是唯一 import
    `facts.store` 的地方),通道只拿到这个只读对象。

    · `frame` 已按 `DECLARED_FIELDS` 做过列投影(⚠ §12 坑 1 的内存红线);
    · `as_of` = 「我现在站在哪一天」(契约三:读取范围截止到当日);
    · `today` = `as_of` 当天的横截面(一行一只票),**通道最常用的东西**。
    """

    as_of: date
    frame: pl.DataFrame
    #: 当日冻结事实包的身份(署名清单要记账,架构 §3.1)。
    pack_id: str
    pack_version: str

    def field(self, name: str) -> pl.Series:
        """按列取数(契约一)。列名不在 `DECLARED_FIELDS` 里**直接抛**。"""
        self.assert_declared(name)
        return self.frame[name]

    @staticmethod
    def assert_declared(*names: str) -> None:
        bad = [n for n in names if n not in DECLARED_FIELDS]
        if bad:
            raise UndeclaredField(
                f"{bad} 不在 K9 的声明依赖里(contract.DECLARED_FIELDS)—— "
                f"策略要读一个新字段,必须先把它写进声明集(契约一:声明依赖)")

    def select(self, *names: str) -> pl.DataFrame:
        """取若干列(全部先过声明闸)。"""
        self.assert_declared(*names)
        return self.frame.select(list(names))

    @property
    def today(self) -> pl.DataFrame:
        """`as_of` 当天的横截面。"""
        return self.frame.filter(pl.col("trade_date") == self.as_of)

    def history(self, *, days: int, include_today: bool) -> pl.DataFrame:
        """最近 `days` 个**有数据的**交易日(按 `trade_date` 取最后几天)。

        `include_today=False` = 分母不含当日(放量倍数、均量、均线一律走这条)。
        缺哪天就少哪天 —— 「那天没冻结」是可被读出来的事实,由调用方对着行数自己判。
        """
        if days < 1:
            raise ValueError(f"days 必须 >= 1,收到 {days}")
        pool = self.frame if include_today else self.frame.filter(
            pl.col("trade_date") < self.as_of)
        if pool.is_empty():
            return pool
        wanted = sorted(pool["trade_date"].unique().to_list())[-days:]
        return pool.filter(pl.col("trade_date").is_in(wanted))

    @property
    def sessions(self) -> int:
        """区间里实际有几个交易日(用来判「历史够不够长」)。"""
        return 0 if self.frame.is_empty() else int(self.frame["trade_date"].n_unique())


# ══════════════════════════════════════════════════════════════════════════
# 契约二 · 产出署名清单
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ChannelHit:
    """一个通道在一档上召回的一只票。

    `strength` 装的是该形态的**强度性读数原值**(K9 §3.6:强度性⛔ 不设门槛,
    全部转成 K9 第三层的打分项)。键必须逐字对上
    `params.ranking.patternSubWeights[pattern]` 的键 —— `ranking.py` 会当场核对,
    对不上直接抛(⛔ 不静默按 0 分算,那会让一个打错的键悄悄变成「这项最差」)。
    """

    ts_code: str
    pattern: Pattern
    tier: Tier
    strength: Mapping[str, Optional[float]] = field(default_factory=dict)
    # 可选证据只作解释/形态强度输入；None 表示数据源不可用，不能当作 False。
    evidence: Mapping[str, Optional[bool]] = field(default_factory=dict)
    risks: Tuple[str, ...] = ()
    # P3 批准包定义的附加分；不属于 patternSubWeights，且受 bonusCap 限制。
    bonus_score: float = 0.0


@dataclass(frozen=True)
class Entry:
    """清单上的一只票(契约二:每只票标明由哪个通道召回)。"""

    ts_code: str
    name: Optional[str]
    sw_l2_code: Optional[str]
    sw_l2_name: Optional[str]
    patterns: Tuple[Pattern, ...]        # 命中的形态**全部列出**(K9 §五-4)
    primary_pattern: Pattern             # 形态内强度分最高的那个
    tier: Tier                           # 成色标注(K9 §五-7)
    rank: int                            # 1 起
    seat_kind: Optional[SeatKind]        # None = 未入席(reserve)
    score: float
    industry_heat_score: Optional[float]
    pattern_strength_score: float
    relay_score: float
    evidence: Mapping[str, Optional[bool]] = field(default_factory=dict)
    risks: Tuple[str, ...] = ()

    def to_row(self) -> Dict[str, object]:
        """落库 / canonical JSON 用的**确定性**字典(键序固定)。"""
        return {
            "tsCode": self.ts_code,
            "name": self.name,
            "swL2Code": self.sw_l2_code,
            "swL2Name": self.sw_l2_name,
            "patterns": [p.value for p in self.patterns],
            "primaryPattern": self.primary_pattern.value,
            "tier": self.tier.value,
            "rank": self.rank,
            "seatKind": None if self.seat_kind is None else self.seat_kind.value,
            "score": self.score,
            "industryHeatScore": self.industry_heat_score,
            "patternStrengthScore": self.pattern_strength_score,
            "relayScore": self.relay_score,
            "evidence": dict(sorted(self.evidence.items())),
            "risks": list(self.risks),
        }


#: 策略署名。将来引入 K10 时它与 `k9_runs.strategy` 一起构成「各出各的署名清单」
#: (架构 §3.2 多策略并行;本版不实现并行,裁定 8)。
STRATEGY = "K9"
STRATEGY_VERSION = "K9-v2"


@dataclass(frozen=True)
class Shortlist:
    """一次策略层运行的产物(契约二)。

    ⚠ **这还不是定稿的清单**:§5.5 规定清单在**解释层之后**定稿(消息面剔除 +
    后备补位)。策略层交出的是 `seated`(入席)+ `reserve`(后备,按名次排好),
    由编排器带着它去解释层。⛔ 别在这里把 reserve 丢掉。
    """

    strategy: str
    strategy_version: str
    label_contract_version: str
    scoring_contract: Mapping[str, object]
    params_version: str
    pack_version: str
    pack_id: str
    trade_date: date
    entries: Tuple[Entry, ...]            # 入席的,按 rank 升序
    reserve: Tuple[Entry, ...]            # 后备票,按 rank 升序
    tier_used: Tier                       # 本日用的是哪一档(§5.4.7 第 2 步)
    strict_candidates: int
    relaxed_candidates: int
    channel_counts: Mapping[str, Mapping[str, int]]   # 每形态 strict/relaxed/seated
    capacity_short: bool                  # K9 §五-6:放宽后仍不足 `quota.min`
    absent_patterns: Tuple[Pattern, ...]  # K9 §五-5:今日无此形态
    dropped_by_heat_absent: Tuple[str, ...]   # heatAbsentPolicy='drop' 丢掉的票

    @property
    def size(self) -> int:
        return len(self.entries)


__all__ = [
    "Pattern", "PATTERN_ORDER", "PATTERN_LABEL",
    "Tier", "SeatKind",
    "DECLARED_FIELDS", "to_percent_points",
    "UndeclaredField", "PackRange",
    "ChannelHit", "Entry", "Shortlist", "STRATEGY", "STRATEGY_VERSION",
]
