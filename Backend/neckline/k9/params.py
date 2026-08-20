"""K9 参数包的契约与校验(PROJECT_PLAN §5.4.3,裁定 5)。

> **裁定 5 的硬纪律**:Neckline 读不到参数配置时,明确报「参数未配置」并停止出清单,
> ⛔ **不使用任何默认值**。映射到报告三态:参数未配置 = 「**今天没跑成**」,
> ⛔ 不是「今天没有」。

**参数标定归 whynotme,Neckline 只消费标定完、用户确认过的参数包。**
Neckline 侧的动作只有三件:校验 → 记 `packageVersion` 进每次运行 → 校验不过就报
「今天没跑成 · 参数未配置」。⛔ 不自动拉取参数包、⛔ 不写 whynotme 的任何目录。

**路径**:`Backend/config/k9-params.<version>.json`,CLI / systemd 显式传
`--k9-params <path>`。🔴 **⛔ 无默认路径、⛔ 无内嵌默认值、⛔ 无「暂用某值」。**

## 「无默认值」是怎么被**结构性**保证的

`K9Params` 与它的每个嵌套 dataclass 都是 `@dataclass(frozen=True)`,而且
**每个字段都没有 default**。少一个值就**构造不出对象** —— 这不是靠 `if` 判断,
是靠类型。守门单测遍历 `dataclasses.fields(...)` 逐字段断言
`f.default is MISSING and f.default_factory is MISSING`。
测试夹具因此**必须显式提供每一个值**(§10 测试纪律)。

## 三个「取值待标定」的参数位:全部候选取值都实现,⛔ 没有默认分支

§8.3 #18–#20。用户 2026-08-20 把它们从「待拍板」降为「取值待标定的参数位」,
⛔ 施工侧**不许挑一个当默认**:

| 键 | 候选取值(**全部实现**) |
|---|---|
| `industry.heatAbsentPolicy` | `renormalize` / `zero` / `drop` |
| `ranking.relaySource` | `recalled` / `shortlisted` |
| `ranking.relayScoring` | `binary` / `count` |

三个都是**闭合枚举**,解析走 `Enum(value)` 的**全映射** —— ⛔ 代码里没有
`.get(x, DEFAULT)`、没有 `if policy == X: … else: …` 的兜底分支。取值不在枚举里 =
`invalid`,不是「退回某个默认」。示例配置里三个键一律写 `"__TO_BE_CALIBRATED__"`。

## ⚠ 与 Plan 不符 / Plan 未写清之处(已登记 §14,⛔ 施工侧不自行发明)

1. **`p2` 的「一字跌停判定」没有键名**。§8.1 第 4 项把「一字跌停 / 有效换手的判定」
   列为待标定,§5.4.5 只给了三个键(`normDropMin` / `maDays` / `minTurnover`)——
   `minTurnover` 是「有效换手」那一半,「一字跌停」那一半**没有键名也没有形状**
   (是按振幅?按 `high==low==limit_down_price`?按开盘即跌停 + 成交量?)。
   ⛔ 本片不发明键名(那等于替标定方决定判据形状),`REQUIRED_SCHEMA` 里没有它。
2. **`p3` 的 `notErupted*` 是 Plan 里的通配符**。§5.4.5 逐字写的就是 `p3.notErupted*`,
   §8.1 第 8 项也只说「『尚未爆发』的判定」待标定。同理 ⛔ 不发明。
3. **`patternSubWeights` 的分项键名是本片起的**。§8.3 #17 只说「形态 1 三项 / 形态 2
   一项 / 形态 3 两项 / 形态 4 两项」,§5.4.5 用中文列出了这 8 个量。这里把它们
   逐个转成标识符(见 `_SUB_WEIGHT_KEYS`),**是命名不是主张** —— 每个量都逐字
   对得上 §5.4.5 的强度性条件。
4. **「权重和」的目标值 Plan 没写**。§5.4.3 校验 2 只写「权重和」三个字。本片按
   §5.4.6「按 `patternSubWeights[pattern]` 加权求和 **∈ [0,1]**」反推为**和为 1**
   (各分项本身已归一到 [0,1]),三项主权重同样按和为 1 校验。
5. **未声明的多余键只告警不阻断**。缺键是 `missing`、取值不合法是 `invalid`,
   多余键两者都不是。S6 起每个通道**实际读到**的键都必须进 `REQUIRED_SCHEMA`,
   届时「标定了但没人读」这类漂移才真正被堵死。
"""

from __future__ import annotations

import json
import logging
from dataclasses import MISSING, dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.db import connection, init_schema
from neckline.facts.pack import MAX_LOOKBACK_PACKS, PACK_VERSION

logger = logging.getLogger(__name__)

#: 示例配置里每个数值位的占位符。⛔ 示例文件里不许出现任何真数字(§6 S5)。
TO_BE_CALIBRATED = "__TO_BE_CALIBRATED__"

#: 权重和的浮点容差(数值容差,不是策略参数)。
_WEIGHT_SUM_TOL = 1e-6


# ══════════════════════════════════════════════════════════════════════════
# 三个「取值待标定」的闭合枚举 —— 全部候选取值都实现,⛔ 无默认
# ══════════════════════════════════════════════════════════════════════════

class HeatAbsentPolicy(str, Enum):
    """行业热度分「查无该行业」(成员数不足 / 被排除)的票怎么处理(§8.3 #18)。"""

    #: 按剩余两项权重重新归一 —— 行业无排名**不被当成「最差行业」**。
    RENORMALIZE = "renormalize"
    #: 行业热度分记 0 —— 等同「最差行业」。
    ZERO = "zero"
    #: 该票直接不参与本日清单。
    DROP = "drop"


class RelaySource(str, Enum):
    """跨日接力分里「被选中」的口径(§8.3 #19)。

    ⚠ 一条**观察**(供标定判断,⛔ 不是预设):K9 §四 原文「过去 N 天内被其他形态
    **选中**过」字面上更接近 `recalled`,但 K9 没有明确 → 仍作参数位,⛔ 不预设。"""

    #: 「被选中」= 被通道召回。
    RECALLED = "recalled"
    #: 「被选中」= 进入过清单。
    SHORTLISTED = "shortlisted"


class RelayScoring(str, Enum):
    """跨日接力分的打分形状(§8.3 #20)。"""

    #: 有 / 无,二值。
    BINARY = "binary"
    #: 计次(被几个不同形态在几天里选过)。
    COUNT = "count"


#: 三个参数位 → 它们的枚举类。**全映射**,⛔ 没有「哪个是默认」这一项。
ENUM_PARAM_SLOTS: Mapping[str, type] = {
    "industry.heatAbsentPolicy": HeatAbsentPolicy,
    "ranking.relaySource": RelaySource,
    "ranking.relayScoring": RelayScoring,
}


# ══════════════════════════════════════════════════════════════════════════
# 失败类型
# ══════════════════════════════════════════════════════════════════════════

class ParamsUnavailable(Exception):
    """参数未配置 / 配置无效。→ 三态里的 `not_run`(**今天没跑成**),⛔ 不是 `empty`。

    `missing` = 缺了哪些键(点名到路径);`invalid` = 哪些值不合法(带原因)。
    报告要**逐条**把它们印出来 —— 「今天没跑成」必须说清缺口(架构 §3.5)。"""

    def __init__(self, missing: Sequence[str] = (), invalid: Sequence[str] = ()):
        self.missing: Tuple[str, ...] = tuple(missing)
        self.invalid: Tuple[str, ...] = tuple(invalid)
        super().__init__(self.describe())

    def describe(self) -> str:
        parts: List[str] = []
        if self.missing:
            parts.append("缺键:" + "、".join(self.missing))
        if self.invalid:
            parts.append("取值无效:" + "、".join(self.invalid))
        return "参数未配置" + ("(" + ";".join(parts) + ")" if parts else "")

    def gaps(self) -> List[str]:
        """给报告首行逐条列出来的缺口。"""
        return [f"缺键 {k}" for k in self.missing] + [f"无效 {k}" for k in self.invalid]


# ══════════════════════════════════════════════════════════════════════════
# DTO —— 🔴 每个字段都没有默认值(见模块 docstring)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class BoundaryParams:
    """K9 第一层 9 条排除项里**带数字**的那几条(§5.4.4)。

    另外 5 条(科创板 / ST / 北交所 / 停牌 / 当日涨停)是纯判定,不带参数;
    白酒走 `IndustryParams.excluded_l2_codes`。"""

    new_listing_days: int          # 第 5 条:trade_date − list_date < 它
    liquidity_window_days: int     # 第 7 条:成交额均值的窗口
    liquidity_bottom_pct: float    # 第 7 条:处于全市场后百分之几(0~1)
    spike_fade_ret_pct: float      # 第 9 条:当日涨幅 >
    spike_fade_gap_pct: float      # 第 9 条:最高涨幅 − 收盘涨幅 ≥


@dataclass(frozen=True)
class IndustryParams:
    min_members: int                       # §8.2 #16(代价表见 §4.5)
    excluded_l2_codes: Tuple[str, ...]     # K9 §二 第 2 条:白酒Ⅱ `801125.SI`
    heat_absent_policy: HeatAbsentPolicy    # §8.3 #18,三种取值全部实现


@dataclass(frozen=True)
class P1Tier:
    """形态 1 放量启动的**定义性条件**(§5.4.5)。强度性条件⛔ 不设门槛。"""

    amp_window_days: int
    amp_max_pct: float
    min_ret_pct: float
    vol_ma_days: int


@dataclass(frozen=True)
class P2Tier:
    norm_drop_min: float           # 归一化跌幅 = 跌幅 ÷ 该板跌停幅度
    ma_days: int                   # 前一日收盘 ≥ N 日均线
    min_turnover: float            # 「有效换手」那一半


@dataclass(frozen=True)
class P3Tier:
    long_window: int
    short_window: int
    flat_band: float               # 长窗相对强度「≈0」的区间宽度


@dataclass(frozen=True)
class P4Tier:
    daily_inflow_rank_pct: float
    cum_days: int
    cum_inflow_rank_pct: float
    lag_rank_gap: float            # 资金流入排名 − 涨跌幅排名 ≥


@dataclass(frozen=True)
class ChannelTiers:
    """一个通道的两档(K9 §五-6:严格档不足 `quota.min` 时自动切换放宽档)。"""

    strict: Any
    relaxed: Any


@dataclass(frozen=True)
class ChannelParams:
    p1: ChannelTiers
    p2: ChannelTiers
    p3: ChannelTiers
    p4: ChannelTiers


@dataclass(frozen=True)
class RankingWeights:
    """三项主权重(§8.1 #11)。和为 1(见模块 docstring 第 4 条登记)。"""

    industry_heat: float
    pattern_strength: float
    relay: float


@dataclass(frozen=True)
class RankingParams:
    weights: RankingWeights
    pattern_sub_weights: Mapping[str, Mapping[str, float]]   # §8.3 #17,4 组
    relay_lookback_days: int
    relay_source: RelaySource        # §8.3 #19,两种取值全部实现
    relay_scoring: RelayScoring      # §8.3 #20,两种取值全部实现
    upside_room_mech_days: int       # §8.2 #15


@dataclass(frozen=True)
class QuotaParams:
    min: int
    max: int
    floor_per_channel: int
    over_strict_consecutive_days: int


@dataclass(frozen=True)
class ExplainParams:
    max_backfill_rounds: int


@dataclass(frozen=True)
class K9Params:
    """一份**已校验**的参数包。🔴 每个字段都没有默认值(见模块 docstring)。"""

    package_version: str
    fact_pack_version: str
    calibrated_by: str
    calibrated_at: str
    approved_by: str
    approved_at: str
    boundary: BoundaryParams
    industry: IndustryParams
    channels: ChannelParams
    ranking: RankingParams
    quota: QuotaParams
    explain: ExplainParams
    source_path: str


# ══════════════════════════════════════════════════════════════════════════
# 显式必填 schema(§5.4.3 校验 1:一张**显式嵌套**的表,缺键 = 错误,⛔ 永不取默认)
# ══════════════════════════════════════════════════════════════════════════

#: 每个形态内**强度项**的合成权重键(§8.3 #17)。名字逐个对着 §5.4.5 的强度性条件。
_SUB_WEIGHT_KEYS: Mapping[str, Tuple[str, ...]] = {
    #: 放量倍数 / 上方机械空间(正向)/ 当日相对强度
    "p1": ("volMultiple", "upsideRoomFar", "relStrength"),
    #: 跑输行业的幅度
    "p2": ("relStrengthShortfall",),
    #: 短窗改善幅度 / 上方机械空间(反向)
    "p3": ("shortWindowImprovement", "upsideRoomNear"),
    #: 净流入排名 / 量比排名(⚠ 自算 `vol/vol_ma5`,§4.7:`volume_ratio` 只有 2 位小数)
    "p4": ("inflowRank", "volumeRatioRank"),
}

_CHANNEL_TIER_KEYS: Mapping[str, Tuple[str, ...]] = {
    "p1": ("ampWindowDays", "ampMaxPct", "minRetPct", "volMaDays"),
    "p2": ("normDropMin", "maDays", "minTurnover"),
    "p3": ("longWindow", "shortWindow", "flatBand"),
    "p4": ("dailyInflowRankPct", "cumDays", "cumInflowRankPct", "lagRankGap"),
}

_TIER_NAMES: Tuple[str, ...] = ("strict", "relaxed")

REQUIRED_SCHEMA: Mapping[str, Any] = {
    "packageVersion": str,
    "factPackVersion": str,
    "calibratedBy": str,
    "calibratedAt": str,
    "approvedBy": str,
    "approvedAt": str,
    "boundary": {
        "newListingDays": int,
        "liquidityWindowDays": int,
        "liquidityBottomPct": float,
        "spikeFadeRetPct": float,
        "spikeFadeGapPct": float,
    },
    "industry": {
        "minMembers": int,
        "excludedL2Codes": list,
        "heatAbsentPolicy": HeatAbsentPolicy,
    },
    "channels": {
        ch: {tier: {k: float for k in keys} for tier in _TIER_NAMES}
        for ch, keys in _CHANNEL_TIER_KEYS.items()
    },
    "ranking": {
        "weights": {"industryHeat": float, "patternStrength": float, "relay": float},
        "patternSubWeights": {
            ch: {k: float for k in keys} for ch, keys in _SUB_WEIGHT_KEYS.items()
        },
        "relayLookbackDays": int,
        "relaySource": RelaySource,
        "relayScoring": RelayScoring,
        "upsideRoomMechDays": int,
    },
    "quota": {
        "min": int,
        "max": int,
        "floorPerChannel": int,
        "overStrictConsecutiveDays": int,
    },
    "explain": {"maxBackfillRounds": int},
}

#: 必须 `<= MAX_LOOKBACK_PACKS` 的窗口位(§5.4.3 校验 2)。路径是点分键路。
_WINDOW_PATHS: Tuple[str, ...] = (
    "boundary.liquidityWindowDays",
    "ranking.relayLookbackDays",
    "ranking.upsideRoomMechDays",
) + tuple(
    f"channels.{ch}.{tier}.{key}"
    for ch, keys in _CHANNEL_TIER_KEYS.items()
    for tier in _TIER_NAMES
    for key in keys
    if key.endswith("Days") or key.endswith("Window") or key == "maDays"
)

#: 必须落在 (0, 1) 开区间的分位 / 比例位。
_UNIT_INTERVAL_PATHS: Tuple[str, ...] = (
    "boundary.liquidityBottomPct",
    "channels.p4.strict.dailyInflowRankPct",
    "channels.p4.relaxed.dailyInflowRankPct",
    "channels.p4.strict.cumInflowRankPct",
    "channels.p4.relaxed.cumInflowRankPct",
)

#: 必须 > 0 的整数位。
_POSITIVE_INT_PATHS: Tuple[str, ...] = (
    "boundary.newListingDays", "boundary.liquidityWindowDays",
    "industry.minMembers",
    "ranking.relayLookbackDays", "ranking.upsideRoomMechDays",
    "quota.min", "quota.max", "quota.floorPerChannel", "quota.overStrictConsecutiveDays",
    "explain.maxBackfillRounds",
) + tuple(
    f"channels.{ch}.{tier}.{key}"
    for ch, keys in _CHANNEL_TIER_KEYS.items()
    for tier in _TIER_NAMES
    for key in keys
    if key.endswith("Days") or key.endswith("Window")
)


# ══════════════════════════════════════════════════════════════════════════
# 校验
# ══════════════════════════════════════════════════════════════════════════

def _walk(schema: Mapping[str, Any], raw: Any, prefix: str,
          missing: List[str], invalid: List[str], extras: List[str]) -> None:
    """按 `REQUIRED_SCHEMA` 逐层比对。缺键 → `missing`;类型不对 / 枚举取值不对 →
    `invalid`;多余键 → `extras`(只告警,见模块 docstring 第 5 条登记)。"""
    if not isinstance(raw, Mapping):
        invalid.append(f"{prefix or '<根>'} 不是对象")
        return
    for key, spec in schema.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in raw:
            missing.append(path)
            continue
        value = raw[key]
        if isinstance(spec, Mapping):
            _walk(spec, value, path, missing, invalid, extras)
            continue
        if isinstance(spec, type) and issubclass(spec, Enum):
            allowed = [m.value for m in spec]
            if value not in allowed:
                invalid.append(
                    f"{path}={value!r} 不在候选取值 {allowed} 里"
                    f"(⛔ 无默认值:三个取值待标定的参数位全部候选取值都已实现,"
                    f"标定阶段挑一个填进来)")
            continue
        if spec is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                invalid.append(f"{path}={value!r} 不是数值")
        elif spec is int:
            if isinstance(value, bool) or not isinstance(value, int):
                invalid.append(f"{path}={value!r} 不是整数")
        elif spec is list:
            if not isinstance(value, list):
                invalid.append(f"{path}={value!r} 不是数组")
        elif spec is str:
            if not isinstance(value, str) or not value.strip():
                invalid.append(f"{path}={value!r} 不是非空字符串")
    for key in raw:
        if key not in schema:
            extras.append(f"{prefix}.{key}" if prefix else key)


def _dig(raw: Mapping[str, Any], path: str) -> Any:
    cur: Any = raw
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _check_ranges(raw: Mapping[str, Any], invalid: List[str]) -> None:
    for path in _POSITIVE_INT_PATHS:
        v = _dig(raw, path)
        if isinstance(v, int) and not isinstance(v, bool) and v <= 0:
            invalid.append(f"{path}={v} 必须 > 0")
    for path in _WINDOW_PATHS:
        v = _dig(raw, path)
        if isinstance(v, int) and not isinstance(v, bool) and v > MAX_LOOKBACK_PACKS:
            invalid.append(
                f"{path}={v} 超过 MAX_LOOKBACK_PACKS={MAX_LOOKBACK_PACKS}"
                f"(工程容量上限,不是策略参数 —— 策略层读不了这么长的历史)")
    for path in _UNIT_INTERVAL_PATHS:
        v = _dig(raw, path)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and not (0 < v < 1):
            invalid.append(f"{path}={v} 必须落在 (0, 1) 开区间")

    lo, hi = _dig(raw, "quota.min"), _dig(raw, "quota.max")
    if isinstance(lo, int) and isinstance(hi, int) and lo > hi:
        invalid.append(f"quota.min={lo} > quota.max={hi}")


def _check_weight_sums(raw: Mapping[str, Any], invalid: List[str]) -> None:
    """§5.4.3 校验 2 的「权重和」。目标值 Plan 未写,按 §5.4.6「加权求和 ∈ [0,1]」
    反推为**和为 1**(见模块 docstring 第 4 条登记)。"""
    groups: List[Tuple[str, Any]] = [("ranking.weights", _dig(raw, "ranking.weights"))]
    for ch in _SUB_WEIGHT_KEYS:
        groups.append((f"ranking.patternSubWeights.{ch}",
                       _dig(raw, f"ranking.patternSubWeights.{ch}")))
    for path, group in groups:
        if not isinstance(group, Mapping):
            continue
        vals = [v for v in group.values()
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(vals) != len(group):
            continue                      # 类型问题已在 `_walk` 里报过,不重复报
        if any(v < 0 for v in vals):
            invalid.append(f"{path} 含负权重:{dict(group)}")
            continue
        total = sum(vals)
        if abs(total - 1.0) > _WEIGHT_SUM_TOL:
            invalid.append(
                f"{path} 的权重和 = {total!r},必须为 1"
                f"(§5.4.6:形态内强度分加权求和 ∈ [0,1])")


def _check_fingerprint(raw: Mapping[str, Any], invalid: List[str]) -> None:
    """§5.4.3 校验 3:`factPackVersion` 必须等于事实层常量,否则该参数包无效。"""
    got = _dig(raw, "factPackVersion")
    if isinstance(got, str) and got != PACK_VERSION:
        invalid.append(
            f"factPackVersion={got!r} ≠ 事实层当前 PACK_VERSION={PACK_VERSION!r} —— "
            f"这份参数包是在另一版事实包口径上标定的,⛔ 不能拿来跑")


def _check_excluded_codes(
    raw: Mapping[str, Any], invalid: List[str], db_path: Optional[Path]
) -> None:
    """§5.4.3 校验 2 的后半:`excludedL2Codes` 里每个码必须在 `sw_industry_classify`
    里存在;`801125.SI` 额外核一次名字叫「白酒Ⅱ」—— **名称不符只告警不阻断**
    (§12 坑 6:名称会变、代码不变)。

    分类表本身是空的(还没日更过)→ **跳过这条校验**,⛔ 不把「没拉过分类表」误报成
    「参数写错了」:那是**数据缺口**,归 `facts/completeness.py` 判「今天没跑成」。"""
    from neckline.data.sw_industry import BAIJIU_L2_CODE, BAIJIU_L2_NAME

    codes = _dig(raw, "industry.excludedL2Codes")
    if not isinstance(codes, list):
        return
    bad_type = [c for c in codes if not isinstance(c, str) or not c.strip()]
    if bad_type:
        invalid.append(f"industry.excludedL2Codes 含非字符串项:{bad_type}")
        return
    init_schema(db_path)
    with connection(db_path) as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM sw_industry_classify").fetchone()[0])
        if total == 0:
            logger.warning(
                "[k9-params] `sw_industry_classify` 为空,跳过 excludedL2Codes 存在性校验 —— "
                "那是数据缺口(归事实层的完整性判定),不是参数写错了")
            return
        known = {
            r[0]: r[1] for r in conn.execute(
                "SELECT index_code, name FROM sw_industry_classify WHERE level='L2'").fetchall()
        }
    unknown = [c for c in codes if c not in known]
    if unknown:
        invalid.append(
            f"industry.excludedL2Codes 里这些码不在 sw_industry_classify 的 L2 层:{unknown}"
            f"(⛔ 按代码识别,不按名称 —— 名称会变、代码不变)")
    if BAIJIU_L2_CODE in known and known[BAIJIU_L2_CODE] != BAIJIU_L2_NAME:
        logger.warning(
            "[k9-params] %s 的名称是「%s」而非「%s」—— 名称会变,按代码识别的判据不受影响"
            "(只告警,不阻断)", BAIJIU_L2_CODE, known[BAIJIU_L2_CODE], BAIJIU_L2_NAME)


# ══════════════════════════════════════════════════════════════════════════
# 组装
# ══════════════════════════════════════════════════════════════════════════

def _tier(cls: type, raw: Mapping[str, Any], keys: Sequence[str]) -> Any:
    """把一档的 camelCase 键装进对应 dataclass。⛔ 不给任何字段兜底值 ——
    到这一步 `_walk` 已经证明每个键都在了。"""
    snake = {_to_snake(k): raw[k] for k in keys}
    return cls(**snake)


def _to_snake(camel: str) -> str:
    out = []
    for ch in camel:
        if ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def _build(raw: Mapping[str, Any], source_path: str) -> K9Params:
    ch = raw["channels"]
    channel_cls = {"p1": P1Tier, "p2": P2Tier, "p3": P3Tier, "p4": P4Tier}
    channels = ChannelParams(**{
        name: ChannelTiers(
            strict=_tier(channel_cls[name], ch[name]["strict"], _CHANNEL_TIER_KEYS[name]),
            relaxed=_tier(channel_cls[name], ch[name]["relaxed"], _CHANNEL_TIER_KEYS[name]),
        )
        for name in _CHANNEL_TIER_KEYS
    })
    rk = raw["ranking"]
    return K9Params(
        package_version=raw["packageVersion"],
        fact_pack_version=raw["factPackVersion"],
        calibrated_by=raw["calibratedBy"],
        calibrated_at=raw["calibratedAt"],
        approved_by=raw["approvedBy"],
        approved_at=raw["approvedAt"],
        boundary=_tier(BoundaryParams, raw["boundary"],
                       tuple(REQUIRED_SCHEMA["boundary"].keys())),
        industry=IndustryParams(
            min_members=raw["industry"]["minMembers"],
            excluded_l2_codes=tuple(raw["industry"]["excludedL2Codes"]),
            # 🔴 全映射构造:取值不在枚举里在 `_walk` 就已判 invalid,
            # ⛔ 这里没有 `.get(x, DEFAULT)`、没有 else 兜底分支。
            heat_absent_policy=HeatAbsentPolicy(raw["industry"]["heatAbsentPolicy"]),
        ),
        channels=channels,
        ranking=RankingParams(
            weights=RankingWeights(
                industry_heat=rk["weights"]["industryHeat"],
                pattern_strength=rk["weights"]["patternStrength"],
                relay=rk["weights"]["relay"],
            ),
            pattern_sub_weights={
                name: dict(rk["patternSubWeights"][name]) for name in _SUB_WEIGHT_KEYS
            },
            relay_lookback_days=rk["relayLookbackDays"],
            relay_source=RelaySource(rk["relaySource"]),
            relay_scoring=RelayScoring(rk["relayScoring"]),
            upside_room_mech_days=rk["upsideRoomMechDays"],
        ),
        quota=QuotaParams(
            min=raw["quota"]["min"], max=raw["quota"]["max"],
            floor_per_channel=raw["quota"]["floorPerChannel"],
            over_strict_consecutive_days=raw["quota"]["overStrictConsecutiveDays"],
        ),
        explain=ExplainParams(max_backfill_rounds=raw["explain"]["maxBackfillRounds"]),
        source_path=source_path,
    )


def validate(
    raw: Mapping[str, Any], *, db_path: Optional[Path] = None
) -> Tuple[List[str], List[str], List[str]]:
    """纯校验(不装配),返回 `(missing, invalid, extras)`。供守门单测与 CLI 自检用。"""
    missing: List[str] = []
    invalid: List[str] = []
    extras: List[str] = []
    _walk(REQUIRED_SCHEMA, raw, "", missing, invalid, extras)
    _check_fingerprint(raw, invalid)
    if not missing:
        _check_ranges(raw, invalid)
        _check_weight_sums(raw, invalid)
        _check_excluded_codes(raw, invalid, db_path)
    return missing, invalid, extras


def load(path: Path, *, db_path: Optional[Path] = None) -> K9Params:
    """从**显式传入**的路径加载并校验参数包。

    🔴 **⛔ 无默认路径**:调用方必须显式给 `--k9-params <path>`。
    任何一步不过 → `ParamsUnavailable` → 报告「今天没跑成 · 参数未配置」+ 逐条缺口,
    **保留上一份冻结结果**。⛔ 绝不降级成「今天没有」。
    """
    p = Path(path)
    if not p.exists():
        raise ParamsUnavailable(missing=[f"参数包文件不存在:{p}"])
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ParamsUnavailable(invalid=[f"参数包不是合法 JSON({p}):{e}"]) from e
    if not isinstance(raw, dict):
        raise ParamsUnavailable(invalid=[f"参数包顶层不是对象({p})"])

    missing, invalid, extras = validate(raw, db_path=db_path)
    if extras:
        logger.warning(
            "[k9-params] %s 里有 %d 个未声明的键(本次**忽略**,不阻断):%s。"
            "⚠ S6 起每个通道实际读到的键都必须进 REQUIRED_SCHEMA —— "
            "「标定了但没人读」是要出事的那种漂移",
            p.name, len(extras), extras[:10])
    if missing or invalid:
        raise ParamsUnavailable(missing=missing, invalid=invalid)
    return _build(raw, str(p))


def assert_no_field_defaults(cls: type) -> List[str]:
    """遍历一个参数 dataclass(含嵌套)的每个字段,返回**带默认值**的字段路径列表。

    🔴 §5.4.3 校验 4 的机器判据:空列表 = 「少一个值就构造不出对象」这条结构性保证
    仍然成立。守门单测直接断言它是空的。"""
    offenders: List[str] = []

    def walk(c: type, prefix: str) -> None:
        if not is_dataclass(c):
            return
        for f in fields(c):
            path = f"{prefix}.{f.name}" if prefix else f"{c.__name__}.{f.name}"
            if f.default is not MISSING or f.default_factory is not MISSING:  # type: ignore[misc]
                offenders.append(path)
            if is_dataclass(f.type):
                walk(f.type, path)

    for cls_ in (K9Params, BoundaryParams, IndustryParams, ChannelParams, ChannelTiers,
                 P1Tier, P2Tier, P3Tier, P4Tier, RankingParams, RankingWeights,
                 QuotaParams, ExplainParams):
        walk(cls_, "")
    return offenders


__all__ = [
    "TO_BE_CALIBRATED",
    "HeatAbsentPolicy",
    "RelaySource",
    "RelayScoring",
    "ENUM_PARAM_SLOTS",
    "ParamsUnavailable",
    "BoundaryParams",
    "IndustryParams",
    "P1Tier", "P2Tier", "P3Tier", "P4Tier",
    "ChannelTiers", "ChannelParams",
    "RankingWeights", "RankingParams",
    "QuotaParams", "ExplainParams",
    "K9Params",
    "REQUIRED_SCHEMA",
    "validate",
    "load",
    "assert_no_field_defaults",
]
