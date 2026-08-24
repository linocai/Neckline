"""预案的**结构化可机械求值**形状(PROJECT_PLAN §5.6.3,架构 §3.4 硬约束)。

```
MetricRef  ∈ 闭合枚举 { auction_price, auction_gap_pct, open_price, gap_pct,
                        first30_low, first30_high, prev_close, prev_low, prev_high }
Condition  = { op: "<=|>=|<|>", lhs: MetricRef, rhs: number | MetricRef }
Branch     = { name: "成立"|"放弃", all: [Condition, ...] }
Playbook   = { ts_code, pattern, levels{first_resistance, second_resistance, invalidation},
               branches: [成立, 放弃], default: "观察", filled_by, filled_at }
```

🔴 **闭合枚举 = 求值器是全函数**:未知 `MetricRef` → **D0 当场判 playbook 无效**
(`parse_playbook` 抛 `PlaybookInvalid`),⛔ 绝不让一个次日早上求不出值的条件被冻结进去。

🔴 **⛔ 没有算术**:`rhs` 只能是一个数或另一个 `MetricRef`。
B2 已裁定形态 2 的放弃条件只保存明确价格；不保存、不反算、不展示百分比。

🔴 **⛔ 没有自由文本**:`Condition` / `Branch` / `Levels` 三个 dataclass 里
一个 `str` 字段都不承载评价(`Branch.name` 是闭合枚举、`Playbook.pattern` 是形态码)。
守门单测逐字段断言(G6 一族)—— 预案层**知道形态,但不做好坏评价**(架构 §四 第 4 条)。

⚠ **单位约定**(与 `k9/contract.py` 同一套,⛔ 别猜):
    · `*_price` / `prev_*` / `first30_*` 一律是**价**(元);
    · `*_gap_pct` 一律是**百分点**(高开 3% → `3.0`),⛔ 不是 0.03。
  两处 gap 的算式唯一源 = `gap_percent_points()`。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union


class PlaybookInvalid(ValueError):
    """预案结构不合法 —— D0 当场拒绝冻结(⛔ 不许降级成「先存着,明早再说」)。"""


class MetricRef(str, Enum):
    """次日早上**可观测的量**的闭合枚举。⛔ 加一个成员就要同时回答:
    9:26 那一拍与 10:00 那一拍**各自从哪里读到它**(见 `auction/checklist.py` /
    `auction/settle.py` 的读数构造)。读不到 = 求值 `UNKNOWN` = 那只票落「观察」。"""

    #: 集合竞价成交价(9:25 撮合结果)。
    AUCTION_PRICE = "auction_price"
    #: 竞价涨跌幅 = `竞价价 / 昨收 − 1`,**百分点**。
    AUCTION_GAP_PCT = "auction_gap_pct"
    #: 开盘价(9:30)。⚠ 9:26 那一拍**读不到**(还没开盘)。
    OPEN_PRICE = "open_price"
    #: D1 10:00 结算拍真正抓到的最后有效成交价；只供 D1 确认账冻结参考价。
    LAST_VALID_TRADE_AT_10_00 = "last_valid_trade_at_10_00"
    #: 高开幅度 = `开盘价 / 昨收 − 1`,**百分点**。⚠ 9:26 那一拍读不到。
    GAP_PCT = "gap_pct"
    #: 🔴 **本场至今的最低价**(含 9:25 竞价成交)。10:00 那一拍读到的就是
    #: 「前 30 分钟最低价」(Plan §5.7.2 逐字:10:00 时的 high/low 即前 30 分钟极值)。
    #: ⚠ 它是**单调下行**的量 —— 9:26 已经跌破的价位,10:00 一定仍然跌破;
    #: 这正是「9:29 判的『放弃』先到先定、10:00 ⛔ 不改判」在语义上站得住的原因。
    FIRST30_LOW = "first30_low"
    #: 本场至今的最高价(含 9:25 竞价成交)。⚠ 9:26 那一拍不提供(见 checklist)。
    FIRST30_HIGH = "first30_high"
    #: D0 收盘价(**冻结事实包**,⛔ 不取实时源的 `pre_close`)。
    PREV_CLOSE = "prev_close"
    #: D0 最低价(冻结事实包)。
    PREV_LOW = "prev_low"
    #: D0 最高价(冻结事实包)。
    PREV_HIGH = "prev_high"


class Op(str, Enum):
    """比较算子的闭合枚举。⛔ 没有 `==`(浮点相等在两条不同来源的价格上必翻车)。"""

    LE = "<="
    GE = ">="
    LT = "<"
    GT = ">"


class BranchName(str, Enum):
    """K9 §6.3 的两条**显式**分支。第三种(观察)是 `DEFAULT_BRANCH`,
    它是「两条都没触发」的**兜底名**,⛔ 不是一条要去求值的分支。"""

    CONFIRMED = "成立"
    REJECTED = "放弃"


#: 「其余:观察」(K9 §6.3 四个骨架逐字)。
DEFAULT_BRANCH = "观察"

#: 浮点容差(本仓房规:纪律阈值比较一律加 `_EPS`)。⛔ 不是判据阈值。
EPS = 1e-9

Rhs = Union[float, MetricRef]


@dataclass(frozen=True)
class Condition:
    """一条可机械求值的条件。"""

    op: Op
    lhs: MetricRef
    rhs: Rhs

    def to_dict(self) -> Dict[str, Any]:
        return {
            "op": self.op.value,
            "lhs": self.lhs.value,
            "rhs": self.rhs.value if isinstance(self.rhs, MetricRef) else float(self.rhs),
        }

    def describe(self) -> str:
        """给报告 / 审计看的一行。⚠ 这是**渲染**,⛔ 不是条件本身的载体
        —— 求值一律走 `evaluate.py` 读上面三个字段,永不解析这句话。"""
        rhs = self.rhs.value if isinstance(self.rhs, MetricRef) else f"{float(self.rhs):g}"
        return f"{self.lhs.value} {self.op.value} {rhs}"


@dataclass(frozen=True)
class Branch:
    """一条分支 = 若干条件的**合取**(K9 §6.3 的骨架里全是「且」)。"""

    name: BranchName
    all: Tuple[Condition, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name.value, "all": [c.to_dict() for c in self.all]}


@dataclass(frozen=True)
class Levels:
    """K9 §6.1 的三个价位(D0 冻结)。**全是 LLM 判断**(裁定 1)。"""

    first_resistance: float     # 第一压力位 = 预期离场价 = 判断对错的标准
    second_resistance: float    # 第二压力位 = 超预期时的第二目标
    invalidation: float         # 失效位 = 跌破即证明原判断错误

    @property
    def odds(self) -> Optional[float]:
        """赔率 = `(第一压力位 − 收盘) / (收盘 − 失效位)`。

        ⚠ 需要收盘价才算得出,故这里只提供**分母侧**的自检:
        `first_resistance > invalidation` 不成立 → `None`(算式无意义)。
        真正的赔率由 `odds_of(close)` 给。"""
        return None if self.first_resistance <= self.invalidation else 1.0

    def odds_of(self, close: float) -> Optional[float]:
        """K9 §6.1:当前价到第一压力位的距离 ÷ 当前价到失效位的距离。
        分母 ≤ 0(收盘已在失效位下方)→ `None`,⛔ 不拿一个负数冒充赔率。"""
        down = float(close) - self.invalidation
        if down <= EPS:
            return None
        return (self.first_resistance - float(close)) / down

    def to_dict(self) -> Dict[str, float]:
        return {
            "firstResistance": self.first_resistance,
            "secondResistance": self.second_resistance,
            "invalidation": self.invalidation,
        }


#: 预案的来源。`llm` = 预案层填的;`user` = 用户盘后过目后改的(K9 §6.4「最终确认」)。
SOURCE_LLM = "llm"
SOURCE_USER = "user"
SOURCES: Tuple[str, ...] = (SOURCE_LLM, SOURCE_USER)


@dataclass(frozen=True)
class Playbook:
    """一只票的 D0 冻结预案。**append-only 版本化**:用户改动写新版本,
    ⛔ 不覆盖原冻结版本(K9 §6.4 / §5.6.4)。"""

    trade_date: str            # D0(YYYYMMDD)
    ts_code: str
    pattern: str               # 形态码 p1..p4(骨架按它套用)
    levels: Levels
    branches: Tuple[Branch, ...]
    version: int = 1
    source: str = SOURCE_LLM
    filled_by: str = ""        # provider/model 或用户标识
    filled_at: str = ""
    default: str = DEFAULT_BRANCH

    def branch(self, name: BranchName) -> Branch:
        for b in self.branches:
            if b.name is name:
                return b
        raise PlaybookInvalid(f"{self.ts_code} 的预案缺少「{name.value}」分支")

    @property
    def rejection_branch(self) -> Branch:
        """🔴 **9:26 竞价核对表唯一会碰的那条分支**(裁定 10)。"""
        return self.branch(BranchName.REJECTED)

    @property
    def confirmation_branch(self) -> Branch:
        """⚠ **只有 10:00 结算拍会读它**(K9 §6.3 四个成立分支全含「前 30 分钟」
        合取项,9:29 时它尚未发生)。⛔ `auction/checklist.py` 里零命中,守门单测锁死。"""
        return self.branch(BranchName.CONFIRMED)

    def metrics_used(self) -> Tuple[MetricRef, ...]:
        out: list = []
        for b in self.branches:
            for c in b.all:
                for m in (c.lhs, c.rhs):
                    if isinstance(m, MetricRef) and m not in out:
                        out.append(m)
        return tuple(out)

    def to_dict(self) -> Dict[str, Any]:
        """canonical 形状(键序固定,供落库与逐字节对拍)。"""
        return {
            "tradeDate": self.trade_date,
            "tsCode": self.ts_code,
            "pattern": self.pattern,
            "levels": self.levels.to_dict(),
            "branches": [b.to_dict() for b in self.branches],
            "default": self.default,
            "version": self.version,
            "source": self.source,
            "filledBy": self.filled_by,
            "filledAt": self.filled_at,
        }


# ══════════════════════════════════════════════════════════════════════════
# 解析(**唯一入口**:落库前、读回后、用户改完都走它)
# ══════════════════════════════════════════════════════════════════════════

def _parse_metric(raw: Any, *, where: str) -> MetricRef:
    try:
        return MetricRef(str(raw))
    except ValueError as exc:
        raise PlaybookInvalid(
            f"{where}:`{raw}` 不在 MetricRef 闭合枚举里 —— 次日早上求不出这个量,"
            f"⛔ 不许冻结。可用:{[m.value for m in MetricRef]}") from exc


def _parse_number(raw: Any, *, where: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise PlaybookInvalid(f"{where}:期望一个数值,收到 {raw!r}(⛔ 不许自然语言条件)")
    v = float(raw)
    if not math.isfinite(v):
        raise PlaybookInvalid(f"{where}:数值必须有限,收到 {raw!r}")
    return v


def parse_condition(raw: Mapping[str, Any], *, where: str) -> Condition:
    if not isinstance(raw, Mapping):
        raise PlaybookInvalid(f"{where}:条件必须是对象 {{op, lhs, rhs}},收到 {type(raw).__name__}")
    extra = sorted(set(raw) - {"op", "lhs", "rhs"})
    if extra:
        raise PlaybookInvalid(f"{where}:条件里出现了语法外的键 {extra}(⛔ 闭合语法只有 op/lhs/rhs)")
    try:
        op = Op(str(raw.get("op")))
    except ValueError as exc:
        raise PlaybookInvalid(
            f"{where}:算子 `{raw.get('op')}` 不在闭合枚举里,可用 {[o.value for o in Op]}") from exc
    lhs = _parse_metric(raw.get("lhs"), where=f"{where}.lhs")
    rhs_raw = raw.get("rhs")
    rhs: Rhs
    if isinstance(rhs_raw, str):
        rhs = _parse_metric(rhs_raw, where=f"{where}.rhs")
    else:
        rhs = _parse_number(rhs_raw, where=f"{where}.rhs")
    return Condition(op=op, lhs=lhs, rhs=rhs)


def parse_branch(raw: Mapping[str, Any], *, where: str) -> Branch:
    if not isinstance(raw, Mapping):
        raise PlaybookInvalid(f"{where}:分支必须是对象 {{name, all}}")
    try:
        name = BranchName(str(raw.get("name")))
    except ValueError as exc:
        raise PlaybookInvalid(
            f"{where}:分支名 `{raw.get('name')}` 不在闭合枚举里 —— 只有"
            f"「成立」「放弃」两条显式分支,「观察」是兜底名不是分支") from exc
    conds = raw.get("all")
    if not isinstance(conds, Sequence) or isinstance(conds, (str, bytes)) or not conds:
        raise PlaybookInvalid(f"{where}:`all` 必须是**非空**的条件数组(空数组 = 恒成立)")
    return Branch(name=name, all=tuple(
        parse_condition(c, where=f"{where}.all[{i}]") for i, c in enumerate(conds)))


def parse_levels(raw: Mapping[str, Any], *, where: str) -> Levels:
    if not isinstance(raw, Mapping):
        raise PlaybookInvalid(f"{where}:levels 必须是对象")
    first = _parse_number(raw.get("firstResistance"), where=f"{where}.firstResistance")
    second = _parse_number(raw.get("secondResistance"), where=f"{where}.secondResistance")
    inval = _parse_number(raw.get("invalidation"), where=f"{where}.invalidation")
    for label, v in (("firstResistance", first), ("secondResistance", second),
                     ("invalidation", inval)):
        if v <= 0:
            raise PlaybookInvalid(f"{where}.{label}:价位必须 > 0,收到 {v}")
    # 三个价位的**次序**是 K9 §6.1 的定义(第二压力位是「第二目标」,失效位在下方)。
    # ⛔ 次序错了不静默接受:那会让赔率变成负数、让「跌破失效位」变成开盘即触发。
    if not (inval < first < second):
        raise PlaybookInvalid(
            f"{where}:三个价位必须满足 失效位 < 第一压力位 < 第二压力位,"
            f"收到 {inval} / {first} / {second}")
    return Levels(first_resistance=first, second_resistance=second, invalidation=inval)


def parse_playbook(raw: Mapping[str, Any]) -> Playbook:
    """dict → `Playbook`。**任何一处不合法立即抛** —— 这是「不许一个求不出值的条件
    被冻结进去」的那道闸(§5.6.3)。"""
    if not isinstance(raw, Mapping):
        raise PlaybookInvalid("预案必须是对象")
    ts_code = str(raw.get("tsCode") or "")
    if not ts_code:
        raise PlaybookInvalid("预案缺 tsCode")
    where = f"playbook[{ts_code}]"
    pattern = str(raw.get("pattern") or "")
    if not pattern:
        raise PlaybookInvalid(f"{where}:缺 pattern(骨架按形态套用,没有形态就没有骨架)")
    branches_raw = raw.get("branches")
    if not isinstance(branches_raw, Sequence) or isinstance(branches_raw, (str, bytes)):
        raise PlaybookInvalid(f"{where}:branches 必须是数组")
    branches = tuple(parse_branch(b, where=f"{where}.branches[{i}]")
                     for i, b in enumerate(branches_raw))
    names = [b.name for b in branches]
    if sorted(n.value for n in names) != sorted(n.value for n in BranchName):
        raise PlaybookInvalid(
            f"{where}:必须**恰好**有「成立」「放弃」两条分支,收到 {[n.value for n in names]}")
    default = str(raw.get("default") or DEFAULT_BRANCH)
    if default != DEFAULT_BRANCH:
        raise PlaybookInvalid(f"{where}:default 只能是「{DEFAULT_BRANCH}」,收到 `{default}`")
    return Playbook(
        trade_date=str(raw.get("tradeDate") or ""),
        ts_code=ts_code,
        pattern=pattern,
        levels=parse_levels(raw.get("levels") or {}, where=f"{where}.levels"),
        branches=branches,
        version=int(raw.get("version") or 1),
        source=str(raw.get("source") or SOURCE_LLM),
        filled_by=str(raw.get("filledBy") or ""),
        filled_at=str(raw.get("filledAt") or ""),
        default=default,
    )


# ══════════════════════════════════════════════════════════════════════════
# 预案层的输入 DTO(§5.2 边界④:字段集冻结)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Bar:
    """一根日 K(**原始未复权**,与事实包同口径)。"""

    trade_date: str
    open: float
    high: float
    low: float
    close: float
    vol: float

    def to_dict(self) -> Dict[str, Any]:
        return {"tradeDate": self.trade_date, "open": self.open, "high": self.high,
                "low": self.low, "close": self.close, "vol": self.vol}


@dataclass(frozen=True)
class PlaybookInput:
    """交给预案层的一只票。

    🔴 **含 `patterns`**(骨架需要,架构 §四 第 4 条「预案层知道形态」),
    🔴 **⛔ 不含** `rank` / `score` / `seat_kind` / `tier` / `upside_room_mech*`
    (§5.2 边界④)。最后一项尤其要紧:把排序用的**机械空间**喂给预案 LLM,
    等于邀请它把那个数原样吐回来当「第一压力位」—— 裁定 1 要拆开的循环依赖当场复活。
    字段集由 `PLAYBOOK_INPUT_FIELDS` 冻结,守门单测逐字断言(G6)。
    """

    ts_code: str
    name: Optional[str]
    patterns: Tuple[str, ...]
    primary_pattern: str
    sw_l2_name: Optional[str]
    close: float
    prev_close: Optional[float]
    high: Optional[float]
    low: Optional[float]
    bars: Tuple[Bar, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tsCode": self.ts_code, "name": self.name,
            "patterns": list(self.patterns), "primaryPattern": self.primary_pattern,
            "swL2Name": self.sw_l2_name,
            "close": self.close, "prevClose": self.prev_close,
            "high": self.high, "low": self.low,
            "bars": [b.to_dict() for b in self.bars],
        }


#: 🔴 **字段集冻结**(§5.2 边界④ / G6):加字段必须先改这个列表 = 一次自觉行为。
PLAYBOOK_INPUT_FIELDS: Tuple[str, ...] = (
    "ts_code", "name", "patterns", "primary_pattern", "sw_l2_name",
    "close", "prev_close", "high", "low", "bars",
)

#: ⛔ 这些词根一个都不许出现在 `PlaybookInput` 的字段名里(排序位次 / 席位 / 机械空间)。
PLAYBOOK_INPUT_FORBIDDEN: Tuple[str, ...] = (
    "rank", "score", "seat", "tier", "upside_room_mech",
)


def gap_percent_points(price: Optional[float], prev_close: Optional[float]) -> Optional[float]:
    """`(price / prev_close − 1) × 100`,**百分点**。**算式唯一源。**

    昨收缺失或 ≤ 0 → `None`(⛔ 不拿 0 冒充「平开」—— 那会让「算不出」看起来像
    「没涨没跌」,而两者在核对表上要走完全不同的两段)。"""
    try:
        p = float(price) if price is not None else None
        pc = float(prev_close) if prev_close is not None else None
    except (TypeError, ValueError):
        return None
    if not p or p <= 0 or pc is None or pc <= 0:
        return None
    return (p / pc - 1.0) * 100.0


__all__ = [
    "PlaybookInvalid", "MetricRef", "Op", "BranchName", "DEFAULT_BRANCH", "EPS",
    "Condition", "Branch", "Levels", "Playbook", "Bar", "PlaybookInput",
    "PLAYBOOK_INPUT_FIELDS", "PLAYBOOK_INPUT_FORBIDDEN",
    "SOURCE_LLM", "SOURCE_USER", "SOURCES",
    "parse_condition", "parse_branch", "parse_levels", "parse_playbook",
    "gap_percent_points",
]
