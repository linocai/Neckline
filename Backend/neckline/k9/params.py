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

## 🔴 裁定 13 / 14 / 15:放量倍数的两个门槛(S5 挂起的两条登记已关闭)

S5 曾登记「`p2` 的一字跌停判定没有键名」「`p3` 的 `notErupted*` 是通配符」两条,
⛔ 当时未自行发明。用户 2026-08-20 的裁定 13/14/15 把形状定死:

| 判据 | 形状 | 参数位 |
|---|---|---|
| 形态 2「一字跌停」 | 开、高、低、收**四价全等于当日跌停价** | **零参数**(⛔ 不为它造键) |
| 形态 2「有实际换手」 | **放量倍数 ≥ 门槛** | `channels.p2.<档>.minVolMultiple`(§8.2 #21) |
| 形态 3「尚未放量爆发」 | **放量倍数 < V** | `volume.eruptionMultiple`(§8.2 #22) |
| 形态 1「放量启动」 | **放量倍数 ≥ V** | **同一个** `volume.eruptionMultiple` |

**放量倍数 = 当日成交量 ÷ 前 `volume.maDays` 个交易日均量**(K9 §3.0.1),
三处**共用一处计算**(`k9/volume.py`)。

🔴 **V 为什么不分档**(⚠ 与 K9 §五-6「定义性条件中带数字的项设两档」的张力,已登记 §14):
V 是形态 1 与形态 3 的**分界点**,不是松紧旋钮 —— 调低它只是把票从 p3 挪到 p1,
p1∪p3 的召回总量**一只都不会多**,放宽档在它身上没有意义。反过来,若给它两档
(`V_strict` / `V_relaxed`),放宽档一开就会出现「放量倍数落在两值之间」的票同时
命中 p1(放宽档)与 p3(严格档)—— 裁定 15 要的「严丝合缝互补」当场破掉。
故 V 是**单值**,住 `volume` 而不是 `channels.pN.<档>`。
p2 的 `minVolMultiple` 是真正的松紧旋钮(调高 = 候选变少),照旧**分两档**。
## ⚠ 其余与 Plan 不符 / Plan 未写清之处(已登记 §14,⛔ 施工侧不自行发明)

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
from typing import get_args as get_type_args
from typing import get_type_hints

from neckline.db import connection, init_schema
from neckline.facts.pack import MAX_LOOKBACK_PACKS, PACK_VERSION

logger = logging.getLogger(__name__)

#: 示例配置里每个数值位的占位符。⛔ 示例文件里不许出现任何真数字(§6 S5)。
TO_BE_CALIBRATED = "__TO_BE_CALIBRATED__"

#: 权重和的浮点容差(数值容差,不是策略参数)。
_WEIGHT_SUM_TOL = 1e-6

#: B17：K9 正文已经给定的结构值。它们不是标定旋钮，参数包只能逐字转录。
#: 两档定义性条件里的正文值落在 strict 档；relaxed 档仍由联合通过率标定。
K9_FIXED_VALUES: Mapping[str, Any] = {
    "boundary.newListingDays": 30,
    "boundary.liquidityWindowDays": 20,
    "boundary.liquidityBottomPct": 0.2,
    "boundary.spikeFadeRetPct": 5.0,
    "boundary.spikeFadeGapPct": 3.0,
    "volume.maDays": 20,
    "channels.p1.strict.ampWindowDays": 20,
    "channels.p1.strict.ampMaxPct": 25.0,
    "channels.p1.strict.minRetPct": 0.0,
    "channels.p2.strict.maDays": 20,
    "channels.p4.strict.cumDays": 5,
    "quota.min": 10,
    "quota.max": 20,
    "quota.floorPerChannel": 1,
}


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
class VolumeParams:
    """放量倍数(K9 §3.0.1)的两个数。🔴 **不分档**,理由见模块 docstring。

    ⚠ 与形态 4 的**量比**(÷ **5** 日均量的盘后口径,§4.7)是两个不同的量,⛔ 别混。"""

    ma_days: int                   # 分母窗口(K9 §3.0.1 原文 20 日),⛔ 不含当日
    eruption_multiple: float       # 裁定 15 的 V:p1 ≥ V、p3 < V,**同一个值**


@dataclass(frozen=True)
class P1Tier:
    """形态 1 放量启动的**定义性条件**(§5.4.5 + 裁定 15)。

    ⚠ 放量倍数的门槛**不在这里** —— 它是 `volume.eruptionMultiple`(与 p3 共用的 V)。
    强度性条件⛔ 不设门槛。"""

    amp_window_days: int
    amp_max_pct: float
    min_ret_pct: float


@dataclass(frozen=True)
class P2Tier:
    norm_drop_min: float           # 归一化跌幅 = 跌幅 ÷ 该板跌停幅度
    ma_days: int                   # 前一日收盘 ≥ N 日均线
    min_vol_multiple: float        # 裁定 13「有实际换手」:放量倍数 ≥ 它


@dataclass(frozen=True)
class P3Tier:
    """⚠ 「尚未放量爆发」的门槛**不在这里** —— 它是 `volume.eruptionMultiple`
    的**上界侧**(裁定 14/15,与 p1 共用同一个 V)。"""

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
    volume: VolumeParams
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

#: 每档的键 → **声明类型**。🔴 类型不是装饰:窗口类键被声明成 `float` 时,
#: `_check_ranges` 里那两个 `isinstance(v, int)` 前置判断会**整条跳过** ——
#: 2026-08-21 复审实测 `channels.p3.strict.longWindow = 500.0` 与
#: `channels.p2.strict.maDays = 0.4` 双双**校验通过**,而前者会绕过
#: `MAX_LOOKBACK_PACKS`、后者会让 `PackRange.history(days=0.4)` 抛裸 `ValueError`
#: (于是裁定 5 的「参数未配置 = 今天没跑成」在这些输入上走的是异常路径)。
#: ⚠ 这里的类型必须与四个 `PNTier` dataclass 的字段注解逐个一致
#: (`_assert_tier_types_match_the_dataclasses()` 在 import 期核对)。
_CHANNEL_TIER_KEYS: Mapping[str, Mapping[str, type]] = {
    # ⚠ p1 没有 `volMaDays` / 放量门槛:两者都是**共享**的 `volume.*`(裁定 15)。
    "p1": {"ampWindowDays": int, "ampMaxPct": float, "minRetPct": float},
    "p2": {"normDropMin": float, "maDays": int, "minVolMultiple": float},
    "p3": {"longWindow": int, "shortWindow": int, "flatBand": float},
    "p4": {"dailyInflowRankPct": float, "cumDays": int,
           "cumInflowRankPct": float, "lagRankGap": float},
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
    #: 裁定 13/14/15 的共享量。🔴 `eruptionMultiple` **一个值**,p1 与 p3 都读它 ——
    #: 「互斥由判据本身保证」这句话在参数结构上的落点就是这里。
    "volume": {
        "maDays": int,
        "eruptionMultiple": float,
    },
    "channels": {
        ch: {tier: dict(keys) for tier in _TIER_NAMES}
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

def _tier_paths(*keys: str) -> Tuple[str, ...]:
    """`channels.<ch>.<档>.<key>` 的全部路径(两档都要),按 `_CHANNEL_TIER_KEYS` 取。"""
    return tuple(
        f"channels.{ch}.{tier}.{key}"
        for ch, ks in _CHANNEL_TIER_KEYS.items()
        for tier in _TIER_NAMES
        for key in ks
        if key in keys
    )


#: 必须 `<= MAX_LOOKBACK_PACKS` 的窗口位(§5.4.3 校验 2)。路径是点分键路。
_WINDOW_PATHS: Tuple[str, ...] = (
    "boundary.liquidityWindowDays",
    "volume.maDays",
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
    "industry.minMembers", "volume.maDays",
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

# ── 阈值位的**结构性**区间 ─────────────────────────────────────────────────
# 🔴 下面每一条都是「这个量自己的取值范围**决定**的」,⛔ 不是施工侧挑的标定值:
# 越界的值不会让系统报错,它会让判据**退化**(对全体成立或对全体不成立),
# 于是报告照常出、只说「今日无此形态」—— 裁定 5 建立的「跑通了、结果为空、
# 可以被信任」在这里被稀释成一句看不出真假的话。逐条理由见各自注释。
# ⛔ 施工侧**没有**给 `ampMaxPct` / `minRetPct` / `spikeFadeRetPct` 的**量级**
# 加任何上下界 —— 那需要挑一个数,归标定侧(§8 待标定总表)。

#: 必须 > 0 的阈值位(非整数)。
_POSITIVE_FLOAT_PATHS: Tuple[str, ...] = (
    # 放量倍数 = 当日量 ÷ N 日均量 ≥ 0。V ≤ 0 → p1 的「≥ V」对全体成立、
    # p3 的「< V」对全体不成立 → **形态 3 当天归零**、形态 1 的放量门槛整条失效。
    "volume.eruptionMultiple",
    # 第 9 条:最高涨幅 − 收盘涨幅 ≥ 0 恒成立(high ≥ close)。门槛 ≤ 0 →
    # 第 9 条退化成只看「当日涨幅 >」那半条。
    "boundary.spikeFadeGapPct",
) + _tier_paths(
    # 裁定 13「当日有实际换手」的判据,同 `eruptionMultiple`:≤ 0 对全体成立。
    "minVolMultiple",
    # 振幅上限:振幅 ≥ 0,门槛 ≤ 0 → p1 永远召不到票。
    "ampMaxPct",
    # 长窗相对强度「≈0」的**区间宽度**:≤ 0 是空区间 → p3 永远召不到票。
    "flatBand",
)

#: 必须落在 (0, 1] 的比例位 —— 上界由这个量自己的定义给出。
_HALF_OPEN_UNIT_PATHS: Tuple[str, ...] = _tier_paths(
    # 归一化跌幅 = −`ret_1d` ÷ 该板跌停幅度 ∈ [−1, 1](跌不过跌停)。
    # ≤ 0 → 对一只**上涨**的票也成立(「超跌反弹」失去意义);> 1 → 永远召不到票。
    "normDropMin",
)

#: 必须落在 [0, 1] 闭区间的**百分位差值**位。
_UNIT_GAP_PATHS: Tuple[str, ...] = _tier_paths(
    # 资金流入百分位 − 涨跌幅百分位,两者都 ∈ (0,1] → 差值 ∈ (−1, 1)。
    # `lagRankGap` 是它的下限门槛:0 有意义(「资金排名不低于涨幅排名」),
    # > 1 则**永远召不到票**。p4 模块头与 §14 S6 登记 ① 都明写它是 0~1 的差值。
    "lagRankGap",
)


def _schema_type_at(path: str) -> Any:
    """`REQUIRED_SCHEMA` 在这条点分键路上声明的类型(走不到 → `None`)。"""
    cur: Any = REQUIRED_SCHEMA
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _assert_int_paths_are_declared_int() -> None:
    """🔴 **整数闸的前提自检**(2026-08-21 复审 H1)。

    `_check_ranges` 里 `_POSITIVE_INT_PATHS` / `_WINDOW_PATHS` 两个循环都以
    `isinstance(v, int)` 开路 —— 值不是 int 就**整条跳过**。于是只要 schema 把某个
    窗口键声明成 `float`,这两道闸(正数、`MAX_LOOKBACK_PACKS`)连同 `_walk` 的
    整数性检查会**一起**失效,而且校验是绿的。上一版正是这样:四个通道的档内键被
    一句 `{k: float for k in keys}` 统一声明成 `float`。

    这条 import 期断言把「跳过」变成不可能:凡进这两张表的路径,schema 必须声明
    `int`。⛔ 想加一个不是 int 的窗口位?那说明这条路径不该进这两张表。
    """
    bad = [
        p for p in dict.fromkeys(_POSITIVE_INT_PATHS + _WINDOW_PATHS)
        if _schema_type_at(p) is not int
    ]
    assert not bad, (
        f"这些路径进了整数闸,`REQUIRED_SCHEMA` 却没把它们声明成 int:{bad} —— "
        f"`_check_ranges` 的 `isinstance(v, int)` 会把它们整条跳过")


def _assert_tier_types_match_the_dataclasses() -> None:
    """档内键的**声明类型**必须与四个 `PNTier` 的字段注解逐个一致。

    两处各说各话 = 「schema 说 float、dataclass 说 int」那种谁也不报错的漂移。
    """
    classes = {"p1": P1Tier, "p2": P2Tier, "p3": P3Tier, "p4": P4Tier}
    bad: List[str] = []
    for ch, keys in _CHANNEL_TIER_KEYS.items():
        hints = get_type_hints(classes[ch])
        for key, declared in keys.items():
            got = hints.get(_to_snake(key))
            if got is not declared:
                bad.append(f"channels.{ch}.{key}: schema={declared} vs dataclass={got}")
    assert not bad, "档内键的类型两处不一致:\n" + "\n".join(bad)


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
    for path in _POSITIVE_FLOAT_PATHS:
        v = _dig(raw, path)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v <= 0:
            invalid.append(
                f"{path}={v} 必须 > 0 —— 非正值不会报错,它会让判据**退化**"
                f"(对全体成立或对全体不成立),报告只会说「今日无此形态」")
    for path in _HALF_OPEN_UNIT_PATHS:
        v = _dig(raw, path)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and not (0 < v <= 1):
            invalid.append(
                f"{path}={v} 必须落在 (0, 1] —— 归一化跌幅的取值上限是 1(跌不过跌停)")
    for path in _UNIT_GAP_PATHS:
        v = _dig(raw, path)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and not (0 <= v <= 1):
            invalid.append(
                f"{path}={v} 必须落在 [0, 1] —— 它是两个百分位的差值(p4 模块头 / §14 S6 登记 ①)")

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


def _check_k9_fixed_values(raw: Mapping[str, Any], invalid: List[str]) -> None:
    """K9 原文给定值只能转录，不能被参数包安静改写。"""
    for path, expected in K9_FIXED_VALUES.items():
        got = _dig(raw, path)
        if isinstance(expected, float) and isinstance(got, (int, float)) \
                and not isinstance(got, bool):
            matches = abs(float(got) - expected) <= _WEIGHT_SUM_TOL
        else:
            matches = got == expected
        if not matches:
            invalid.append(
                f"{path}={got!r} 必须等于 K9 原文值 {expected!r}；"
                "这是固定结构值，不是标定参数")


def _check_excluded_codes(
    raw: Mapping[str, Any], invalid: List[str], db_path: Optional[Path]
) -> None:
    """§5.4.3 校验 2 的后半:`excludedL2Codes` 里每个码必须在 `sw_industry_classify`
    里存在;`801125.SI` 额外核一次名字叫「白酒Ⅱ」—— **名称不符只告警不阻断**
    (§12 坑 6:名称会变、代码不变)。

    🔴 **白酒必须在里面**(2026-08-21 复审 M4):K9 §二 第 2 条是**给定**排除项,
    ⛔ 不是待标定项(§14 S5 已把 `excludedL2Codes` 登记为示例配置里唯一的真值)。
    复审实测 `excludedL2Codes = []` 校验通过 —— 于是一份参数包可以**安静地**把
    白酒 19 只(§4.8)重新放回池子,而报告里看不出发生过这件事。
    ⚠ 这一条不依赖分类表:它核的是「参数包里有没有这个码」,不是「这个码存不存在」。

    分类表本身是空的(还没日更过)→ **跳过存在性校验**,⛔ 不把「没拉过分类表」误报成
    「参数写错了」:那是**数据缺口**,归 `facts/completeness.py` 判「今天没跑成」。"""
    from neckline.data.sw_industry import BAIJIU_L2_CODE, BAIJIU_L2_NAME

    codes = _dig(raw, "industry.excludedL2Codes")
    if not isinstance(codes, list):
        return
    bad_type = [c for c in codes if not isinstance(c, str) or not c.strip()]
    if bad_type:
        invalid.append(f"industry.excludedL2Codes 含非字符串项:{bad_type}")
        return
    if BAIJIU_L2_CODE not in codes:
        invalid.append(
            f"industry.excludedL2Codes 里没有 {BAIJIU_L2_CODE}(白酒Ⅱ)—— "
            f"K9 §二 第 2 条是**给定**排除项,⛔ 不是标定侧可以关掉的开关")
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
        volume=VolumeParams(
            ma_days=raw["volume"]["maDays"],
            eruption_multiple=raw["volume"]["eruptionMultiple"],
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
        _check_k9_fixed_values(raw, invalid)
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


#: 🔴 **递归够不到的根**。`ChannelTiers.strict` / `relaxed` 声明成 `Any`
#: (两档的形状随通道不同),注解里没有类型信息 —— 四个 `PNTier` 只能从这里进来。
#:
#: ⚠ 这张表**不是**判据本身。上一版的判据就是一张写死 14 个类的清单,而清单
#: 「维护得好就守得住」不是结构性保证:新增一个带默认值的嵌套 dataclass 忘了加进
#: 清单,守门全绿(2026-08-21 复审实测)。现在清单缩到只剩注解够不到的那四个,
#: 其余靠活着的递归走;并且
#: `test_v250_s5_params_guard.py::test_the_no_default_walk_reaches_every_param_dataclass`
#: 断言「本模块里每个参数 dataclass 都被走到了」—— 漏加一个会当场红。
PARAM_DATACLASS_ROOTS: Tuple[type, ...] = (K9Params, P1Tier, P2Tier, P3Tier, P4Tier)


def _nested_dataclasses(annotation: Any) -> List[type]:
    """一个类型注解里**装着**的参数 dataclass(拆开 `Optional` / `List` / `Dict` …)。"""
    if isinstance(annotation, type) and is_dataclass(annotation):
        return [annotation]
    out: List[type] = []
    for arg in get_type_args(annotation):
        out.extend(_nested_dataclasses(arg))
    return out


def _field_hints(cls: type) -> Dict[str, Any]:
    """字段名 → **解析过的**类型注解。

    🔴 本模块顶上有 `from __future__ import annotations`,`dataclasses.fields()` 交出来的
    `f.type` 是**字符串** —— `is_dataclass("BoundaryParams")` 恒为 `False`。
    上一版的递归写的正是那一句,于是**一次都没走过**。
    """
    try:
        return get_type_hints(cls)
    except Exception:                                  # pragma: no cover - 解析不了就退回
        return {}


def param_dataclass_closure(*roots: type) -> List[type]:
    """从这些根出发、经字段注解能走到的全部参数 dataclass(含根本身)。

    守门单测拿它断言「递归是活的」—— 一条死了的递归不会报错,只会让一切照绿。
    """
    seen: List[type] = []

    def walk(c: type) -> None:
        if not (isinstance(c, type) and is_dataclass(c)) or c in seen:
            return
        seen.append(c)
        hints = _field_hints(c)
        for f in fields(c):
            for nested in _nested_dataclasses(hints.get(f.name, f.type)):
                walk(nested)

    for root in roots:
        walk(root)
    return seen


def assert_no_field_defaults(cls: type) -> List[str]:
    """遍历一个参数 dataclass(含嵌套)的每个字段,返回**带默认值**的字段路径列表。

    🔴 §5.4.3 校验 4 的机器判据:空列表 = 「少一个值就构造不出对象」这条结构性保证
    仍然成立。守门单测直接断言它是空的。
    """
    roots: List[type] = [cls]
    if cls is K9Params:
        roots.extend(r for r in PARAM_DATACLASS_ROOTS if r is not K9Params)

    offenders: List[str] = []
    paths: Dict[type, str] = {}
    reached = param_dataclass_closure(*roots)
    for c in reached:
        prefix = paths.get(c, c.__name__)
        hints = _field_hints(c)
        for f in fields(c):
            path = f"{prefix}.{f.name}"
            if f.default is not MISSING or f.default_factory is not MISSING:  # type: ignore[misc]
                offenders.append(path)
            for nested in _nested_dataclasses(hints.get(f.name, f.type)):
                paths.setdefault(nested, path)
    return offenders


# ══════════════════════════════════════════════════════════════════════════
# import 期自检 —— ⛔ 不是可选的:这两条塌了,上面的区间闸会**安静地**失效
# ══════════════════════════════════════════════════════════════════════════
_assert_int_paths_are_declared_int()
_assert_tier_types_match_the_dataclasses()


__all__ = [
    "TO_BE_CALIBRATED",
    "HeatAbsentPolicy",
    "RelaySource",
    "RelayScoring",
    "ENUM_PARAM_SLOTS",
    "K9_FIXED_VALUES",
    "ParamsUnavailable",
    "BoundaryParams",
    "IndustryParams",
    "VolumeParams",
    "P1Tier", "P2Tier", "P3Tier", "P4Tier",
    "ChannelTiers", "ChannelParams",
    "RankingWeights", "RankingParams",
    "QuotaParams", "ExplainParams",
    "K9Params",
    "REQUIRED_SCHEMA",
    "validate",
    "load",
    "assert_no_field_defaults",
    "param_dataclass_closure",
    "PARAM_DATACLASS_ROOTS",
]
