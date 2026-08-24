"""K9-v2 正式参数包的完整、无默认值契约。

Neckline 只消费 whynotme 产出的批准 JSON 原件，不转换、不补值、不读取研究仓。
缺包、缺键、未知键、错版本或交叉约束失败统一抛 ``ParamsUnavailable``；上层必须
呈现“今天没跑成 · 参数未配置”，不得产生清单。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import MISSING, dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from typing import get_args as get_type_args
from typing import get_type_hints

from neckline.db import connection
from neckline.facts.pack import MAX_LOOKBACK_PACKS, PACK_VERSION

logger = logging.getLogger(__name__)

TO_BE_CALIBRATED = "__TO_BE_CALIBRATED__"
_TOL = 1e-6
LABEL_CONTRACT_VERSION = "d2-v1"


class HeatAbsentPolicy(str, Enum):
    RENORMALIZE = "renormalize"
    ZERO = "zero"
    DROP = "drop"


class RelaySource(str, Enum):
    RECALLED = "recalled"
    SHORTLISTED = "shortlisted"


class RelayScoring(str, Enum):
    BINARY = "binary"
    COUNT = "count"


class D1Reference(str, Enum):
    LAST_VALID_TRADE_AT_10_00 = "last_valid_trade_at_10_00"


class MatchedBaseline(str, Enum):
    INDUSTRY_MEDIAN = "industryMedian"


class ParamsUnavailable(Exception):
    def __init__(self, missing: Sequence[str] = (), invalid: Sequence[str] = ()):
        self.missing = tuple(missing)
        self.invalid = tuple(invalid)
        super().__init__(self.describe())

    def describe(self) -> str:
        parts = []
        if self.missing:
            parts.append("缺键:" + "、".join(self.missing))
        if self.invalid:
            parts.append("取值无效:" + "、".join(self.invalid))
        return "参数未配置" + ("(" + ";".join(parts) + ")" if parts else "")

    def gaps(self) -> List[str]:
        return ([f"缺键 {key}" for key in self.missing]
                + [f"无效 {item}" for item in self.invalid])


@dataclass(frozen=True)
class BoundaryParams:
    new_listing_days: int
    activity_amount_window_days: int
    activity_participation_window_days: int
    activity_minimum_valid_days: int
    activity_amount_weight: float
    activity_participation_weight: float
    strict_activity_min_percentile: float
    relaxed_activity_min_percentile: float
    spike_fade_ret_pct: float
    spike_fade_gap_pct: float


@dataclass(frozen=True)
class IndustryParams:
    min_members: int
    excluded_l2_codes: Tuple[str, ...]
    heat_absent_policy: HeatAbsentPolicy


@dataclass(frozen=True)
class VolumeParams:
    ma_days: int


@dataclass(frozen=True)
class HotIdentityParams:
    lookback_days: int
    daily_heat_top_pct: float
    min_hot_days: int
    recent_window_days: int
    min_recent_hot_days: int


@dataclass(frozen=True)
class P1Tier:
    min_vol_multiple: float
    min_ret_pct: float
    min_industry_excess_pct: float
    min_close_location: float
    breakout_window_days: int
    max_distance_below_prior_high_pct: float
    hot_identity_exclusion: HotIdentityParams


@dataclass(frozen=True)
class P2Tier:
    window_days: int
    min_cumulative_drop_pct: float
    min_drawdown_from_window_high_pct: float
    min_industry_underperformance_pct: float
    min_vol_multiple: float
    min_close_location: float
    min_daily_ret_pct: float


@dataclass(frozen=True)
class DailyHeatParams:
    amount_weight: float
    turnover_weight: float


@dataclass(frozen=True)
class P3Tier:
    hot_lookback_days: int
    daily_heat_top_pct: float
    min_hot_days: int
    current_heat_top_pct: float
    conflict_lookback_days: int
    min_absolute_move_as_limit_ratio: float
    min_amplitude_as_limit_ratio: float
    min_huge_vol_multiple: float


@dataclass(frozen=True)
class P3Bonuses:
    bonus_cap: float
    recent_limit_up: float
    recent_limit_down_heat: float
    dragon_tiger_list: float
    controlled_anomaly: float
    reversal_or_second_wave: float
    recent_limit_down_risk_level_increment: int


@dataclass(frozen=True)
class ChannelTiers:
    strict: Any
    relaxed: Any


@dataclass(frozen=True)
class P3Params:
    daily_heat: DailyHeatParams
    strict: P3Tier
    relaxed: P3Tier
    bonuses: P3Bonuses


@dataclass(frozen=True)
class P4Tier:
    daily_inflow_rank_pct: float
    cum_days: int
    cum_inflow_rank_pct: float
    lag_rank_gap: float


@dataclass(frozen=True)
class ChannelParams:
    p1: ChannelTiers
    p2: ChannelTiers
    p3: P3Params
    p4: ChannelTiers


@dataclass(frozen=True)
class RankingWeights:
    industry_heat: float
    pattern_strength: float
    relay: float


@dataclass(frozen=True)
class RankingParams:
    weights: RankingWeights
    pattern_sub_weights: Mapping[str, Mapping[str, float]]
    relay_lookback_days: int
    relay_source: RelaySource
    relay_scoring: RelayScoring


@dataclass(frozen=True)
class QuotaParams:
    min: int
    max: int
    floor_per_channel: int
    over_strict_consecutive_days: int


@dataclass(frozen=True)
class ScoringParams:
    touch_threshold_u: float
    risk_line_l: float
    d1_reference: D1Reference
    matched_baseline: MatchedBaseline


@dataclass(frozen=True)
class K9Params:
    schema_version: str
    package_version: str
    strategy_version: str
    fact_pack_version: str
    label_contract_version: str
    status: str
    validation_mode: str
    parameterized_by: str
    parameterized_at: str
    approved_by: str
    approved_at: str
    approval_note: str
    boundary: BoundaryParams
    industry: IndustryParams
    volume: VolumeParams
    channels: ChannelParams
    ranking: RankingParams
    quota: QuotaParams
    scoring: ScoringParams
    max_backfill_rounds: int
    evidence: Mapping[str, Any]
    source_path: str
    source_sha256: str


_LIST_STR = ("list", str)

_HOT_IDENTITY_SCHEMA = {
    "lookbackDays": int, "dailyHeatTopPct": float, "minHotDays": int,
    "recentWindowDays": int, "minRecentHotDays": int,
}
_P1_TIER_SCHEMA = {
    "minVolMultiple": float, "minRetPct": float, "minIndustryExcessPct": float,
    "minCloseLocation": float, "breakoutWindowDays": int,
    "maxDistanceBelowPriorHighPct": float, "confirmationMode": str,
    "hotIdentityExclusion": _HOT_IDENTITY_SCHEMA,
}
_P2_TIER_SCHEMA = {
    "windowDays": int, "minCumulativeDropPct": float,
    "minDrawdownFromWindowHighPct": float, "absoluteOversoldMode": str,
    "minIndustryUnderperformancePct": float, "minVolMultiple": float,
    "excludeOneLineLimitDown": bool, "supportMode": str,
    "minCloseLocation": float, "minDailyRetPct": float,
}
_P3_TIER_SCHEMA = {
    "hotLookbackDays": int, "dailyHeatTopPct": float, "minHotDays": int,
    "currentHeatTopPct": float, "conflictLookbackDays": int,
    "minAbsoluteMoveAsLimitRatio": float, "minAmplitudeAsLimitRatio": float,
    "minHugeVolMultiple": float, "conflictMode": str,
}
_P4_TIER_SCHEMA = {
    "dailyInflowRankPct": float, "cumDays": int,
    "cumInflowRankPct": float, "lagRankGap": float,
}

_SUB_WEIGHT_KEYS = {
    "p1": ("volMultiple", "amountRank", "closeLocation", "breakout",
           "industryRelativeStrength", "upsideRoom"),
    "p2": ("absoluteOversoldDepth", "industryUnderperformance", "lowRecovery",
           "declineDeceleration", "effectiveTurnover"),
    "p3": ("hotPersistence", "maxAmplitude", "maxAbsoluteMove", "hugeTurnover",
           "conflictRecency", "directionAndIndustryRelativeStrength"),
    "p4": ("dailyInflowRank", "cumulativeInflowRank", "lagRankGap", "volumeRatioRank"),
}

REQUIRED_SCHEMA: Mapping[str, Any] = {
    "schemaVersion": str, "packageVersion": str, "strategyVersion": str,
    "factPackVersion": str, "status": str, "validationMode": str,
    "parameterizedBy": str, "parameterizedAt": str, "approvedBy": str,
    "approvedAt": str, "approvalNote": str,
    "boundary": {
        "newListingDays": int, "spikeFadeRetPct": float, "spikeFadeGapPct": float,
        "activity": {
            "typicalAmountWindowDays": int, "participationWindowDays": int,
            "minimumValidDays": int, "typicalAmountStatistic": str,
            "participationStatistic": str,
            "weights": {"typicalAmountPercentile": float,
                        "participationDensityPercentile": float},
            "strict": {"excludeBottomPct": float},
            "relaxed": {"excludeBottomPct": float},
            "missingComponentPolicy": str,
        },
    },
    "industry": {
        "classification": str, "minMembers": int, "excludedL2Codes": _LIST_STR,
        "heatAbsentPolicy": HeatAbsentPolicy, "historicalMembershipPolicy": str,
    },
    "volume": {"maDays": int, "excludeCurrentDayFromMean": bool},
    "channels": {
        "p1": {"name": str, "strict": _P1_TIER_SCHEMA, "relaxed": _P1_TIER_SCHEMA},
        "p2": {"name": str, "strict": _P2_TIER_SCHEMA, "relaxed": _P2_TIER_SCHEMA},
        "p3": {
            "name": str,
            "dailyHeat": {
                "weights": {"amountPercentile": float, "turnoverRatePercentile": float},
                "missingComponentPolicy": str,
            },
            "strict": _P3_TIER_SCHEMA, "relaxed": _P3_TIER_SCHEMA,
            "bonuses": {
                "bonusCap": float, "recentLimitUp": float,
                "recentLimitDownHeat": float, "dragonTigerList": float,
                "controlledAnomaly": float, "reversalOrSecondWave": float,
                "recentLimitDownRiskLevelIncrement": int,
                "missingOptionalEvidencePolicy": str,
            },
        },
        "p4": {"name": str, "strict": _P4_TIER_SCHEMA, "relaxed": _P4_TIER_SCHEMA},
    },
    "ranking": {
        "weights": {"industryHeat": float, "patternStrength": float, "relay": float},
        "patternSubWeights": {
            channel: {key: float for key in keys} for channel, keys in _SUB_WEIGHT_KEYS.items()
        },
        "relayLookbackDays": int, "relaySource": RelaySource,
        "relayScoring": RelayScoring, "sameDayMultiChannelBonus": float,
        "percentileScope": str,
    },
    "quota": {"min": int, "max": int, "floorPerChannel": int,
              "overStrictConsecutiveDays": int},
    "evaluation": {
        "horizonTradingDays": int, "selectorReference": str,
        "touchThresholdPct": float, "riskThresholdPct": float,
        "d1ConfirmationReference": D1Reference, "touchRequiresTradablePath": bool,
        "sameDayDualTouchWithoutIntradaySequence": str, "forceSettleAtD2": bool,
        "runnerExtensionScore": bool,
    },
    "explain": {"maxBackfillRounds": int},
    "evidence": {
        "snapshot": str, "snapshotSha256": str,
        "factRange": {"from": str, "to": str, "tradingDays": int, "rows": int},
        "probeRange": {"trainFrom": str, "trainTo": str,
                       "forwardProbeFrom": str, "forwardProbeTo": str},
        "knownLimitations": _LIST_STR,
    },
}

K9_FIXED_VALUES: Mapping[str, Any] = {
    "schemaVersion": "k9-params-v2", "strategyVersion": "K9-v2",
    "factPackVersion": "fp-3", "status": "approved", "validationMode": "live_forward",
    "boundary.newListingDays": 30, "boundary.spikeFadeRetPct": 5.0,
    "boundary.spikeFadeGapPct": 3.0,
    "boundary.activity.typicalAmountStatistic": "median",
    "boundary.activity.participationStatistic": "turnover_rate_median",
    "boundary.activity.missingComponentPolicy": "parameters_not_configured",
    "industry.classification": "SW2021_L2",
    "industry.historicalMembershipPolicy": "freeze_by_trade_date",
    "volume.maDays": 20, "volume.excludeCurrentDayFromMean": True,
    "channels.p1.name": "first_volume_launch",
    "channels.p1.strict.confirmationMode": "close_location_or_breakout",
    "channels.p1.relaxed.confirmationMode": "close_location_or_breakout",
    "channels.p2.name": "idiosyncratic_oversold_rebound",
    "channels.p2.strict.absoluteOversoldMode": "cumulative_drop_or_drawdown",
    "channels.p2.relaxed.absoluteOversoldMode": "cumulative_drop_or_drawdown",
    "channels.p2.strict.excludeOneLineLimitDown": True,
    "channels.p2.relaxed.excludeOneLineLimitDown": True,
    "channels.p2.strict.supportMode": "close_location_or_daily_return",
    "channels.p2.relaxed.supportMode": "close_location_or_daily_return",
    "channels.p3.name": "hot_high_conflict",
    "channels.p3.dailyHeat.missingComponentPolicy": "parameters_not_configured",
    "channels.p3.strict.conflictMode": "move_or_amplitude_or_volume",
    "channels.p3.relaxed.conflictMode": "move_or_amplitude_or_volume",
    "channels.p3.bonuses.missingOptionalEvidencePolicy": "no_bonus_no_penalty",
    "channels.p4.name": "money_leads_price",
    "ranking.sameDayMultiChannelBonus": 0.0,
    "ranking.percentileScope": "within_channel_per_trade_date",
    "quota.min": 10, "quota.max": 20, "quota.floorPerChannel": 1,
    "evaluation.horizonTradingDays": 2, "evaluation.selectorReference": "d0_close",
    "evaluation.d1ConfirmationReference": "last_valid_trade_at_10_00",
    "evaluation.touchRequiresTradablePath": True,
    "evaluation.sameDayDualTouchWithoutIntradaySequence": "path_unknown",
    "evaluation.forceSettleAtD2": True, "evaluation.runnerExtensionScore": False,
    "explain.maxBackfillRounds": 2,
}

ENUM_PARAM_SLOTS: Mapping[str, type] = {
    "industry.heatAbsentPolicy": HeatAbsentPolicy,
    "ranking.relaySource": RelaySource,
    "ranking.relayScoring": RelayScoring,
    "evaluation.d1ConfirmationReference": D1Reference,
}


def _dig(raw: Mapping[str, Any], path: str) -> Any:
    value: Any = raw
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _walk(schema: Mapping[str, Any], raw: Any, prefix: str,
          missing: List[str], invalid: List[str], extras: List[str]) -> None:
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
        elif isinstance(spec, tuple) and len(spec) == 2 and spec[0] == "list":
            if not isinstance(value, list):
                invalid.append(f"{path} 不是数组")
            elif any(not isinstance(item, spec[1]) or (spec[1] is str and not item.strip())
                     for item in value):
                invalid.append(f"{path} 含错误类型或空值")
        elif isinstance(spec, type) and issubclass(spec, Enum):
            allowed = [item.value for item in spec]
            if value not in allowed:
                invalid.append(f"{path}={value!r} 不在 {allowed} 中")
        elif spec is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                invalid.append(f"{path}={value!r} 不是数值")
        elif spec is int:
            if isinstance(value, bool) or not isinstance(value, int):
                invalid.append(f"{path}={value!r} 不是整数")
        elif spec is bool:
            if not isinstance(value, bool):
                invalid.append(f"{path}={value!r} 不是布尔值")
        elif spec is str:
            if not isinstance(value, str) or not value.strip():
                invalid.append(f"{path}={value!r} 不是非空字符串")
    for key in raw:
        if key not in schema:
            extras.append(f"{prefix}.{key}" if prefix else key)


def _check_fixed(raw: Mapping[str, Any], invalid: List[str]) -> None:
    for path, expected in K9_FIXED_VALUES.items():
        got = _dig(raw, path)
        same = (abs(float(got) - expected) <= _TOL
                if isinstance(expected, float)
                and isinstance(got, (int, float)) and not isinstance(got, bool)
                else got == expected)
        if not same:
            invalid.append(f"{path}={got!r} 必须等于正式合同值 {expected!r}")


def _number(raw: Mapping[str, Any], path: str) -> Optional[float]:
    value = _dig(raw, path)
    return (float(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
            else None)


def _check_positive(raw: Mapping[str, Any], invalid: List[str]) -> None:
    positive = [
        "boundary.activity.typicalAmountWindowDays",
        "boundary.activity.participationWindowDays",
        "boundary.activity.minimumValidDays", "industry.minMembers", "volume.maDays",
        "ranking.relayLookbackDays", "quota.min", "quota.max", "quota.floorPerChannel",
        "quota.overStrictConsecutiveDays", "evaluation.horizonTradingDays",
        "explain.maxBackfillRounds",
    ]
    for channel, keys in (
        ("p1", ("breakoutWindowDays",)), ("p2", ("windowDays",)),
        ("p3", ("hotLookbackDays", "minHotDays", "conflictLookbackDays")),
        ("p4", ("cumDays",)),
    ):
        for tier in ("strict", "relaxed"):
            positive.extend(f"channels.{channel}.{tier}.{key}" for key in keys)
    for tier in ("strict", "relaxed"):
        positive.extend(
            f"channels.p1.{tier}.hotIdentityExclusion.{key}"
            for key in ("lookbackDays", "minHotDays", "recentWindowDays", "minRecentHotDays")
        )
    for path in positive:
        value = _number(raw, path)
        if value is not None and value <= 0:
            invalid.append(f"{path}={value} 必须 > 0")
        if value is not None and path.endswith("Days") and value > MAX_LOOKBACK_PACKS:
            invalid.append(f"{path}={value} 超过工程回看上限 {MAX_LOOKBACK_PACKS}")

    unit_paths = [
        "boundary.activity.weights.typicalAmountPercentile",
        "boundary.activity.weights.participationDensityPercentile",
        "boundary.activity.strict.excludeBottomPct",
        "boundary.activity.relaxed.excludeBottomPct",
    ]
    for tier in ("strict", "relaxed"):
        unit_paths.extend([
            f"channels.p1.{tier}.minCloseLocation",
            f"channels.p1.{tier}.hotIdentityExclusion.dailyHeatTopPct",
            f"channels.p2.{tier}.minCloseLocation",
            f"channels.p3.{tier}.dailyHeatTopPct",
            f"channels.p3.{tier}.currentHeatTopPct",
            f"channels.p4.{tier}.dailyInflowRankPct",
            f"channels.p4.{tier}.cumInflowRankPct",
            f"channels.p4.{tier}.lagRankGap",
        ])
    for path in unit_paths:
        value = _number(raw, path)
        if value is not None and not 0 <= value <= 1:
            invalid.append(f"{path}={value} 必须在 [0,1]")

    minimum = _number(raw, "boundary.activity.minimumValidDays")
    amount_window = _number(raw, "boundary.activity.typicalAmountWindowDays")
    part_window = _number(raw, "boundary.activity.participationWindowDays")
    if None not in (minimum, amount_window, part_window) and minimum > min(amount_window, part_window):
        invalid.append("boundary.activity.minimumValidDays 不得超过任一统计窗口")
    touch = _number(raw, "evaluation.touchThresholdPct")
    risk = _number(raw, "evaluation.riskThresholdPct")
    if touch is not None and touch <= 0:
        invalid.append("evaluation.touchThresholdPct 必须 > 0")
    if risk is not None and risk >= 0:
        invalid.append("evaluation.riskThresholdPct 必须 < 0")


def _check_weights(raw: Mapping[str, Any], invalid: List[str]) -> None:
    groups = {
        "boundary.activity.weights": _dig(raw, "boundary.activity.weights"),
        "channels.p3.dailyHeat.weights": _dig(raw, "channels.p3.dailyHeat.weights"),
        "ranking.weights": _dig(raw, "ranking.weights"),
        **{f"ranking.patternSubWeights.{channel}":
           _dig(raw, f"ranking.patternSubWeights.{channel}") for channel in _SUB_WEIGHT_KEYS},
    }
    for path, group in groups.items():
        if not isinstance(group, Mapping):
            continue
        values = list(group.values())
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            continue
        if any(float(v) < 0 for v in values):
            invalid.append(f"{path} 含负权重")
        elif abs(sum(float(v) for v in values) - 1.0) > _TOL:
            invalid.append(f"{path} 权重和必须为 1")


def _strict_not_wider(raw: Mapping[str, Any], invalid: List[str]) -> None:
    higher = {
        "p1": ("minVolMultiple", "minRetPct", "minIndustryExcessPct", "minCloseLocation"),
        "p2": ("minCumulativeDropPct", "minDrawdownFromWindowHighPct",
               "minIndustryUnderperformancePct", "minVolMultiple", "minCloseLocation",
               "minDailyRetPct"),
        "p3": ("minHotDays", "minAbsoluteMoveAsLimitRatio", "minAmplitudeAsLimitRatio",
               "minHugeVolMultiple"),
        "p4": ("lagRankGap",),
    }
    lower = {
        "p1": ("maxDistanceBelowPriorHighPct",),
        "p3": ("dailyHeatTopPct", "currentHeatTopPct"),
        "p4": ("dailyInflowRankPct", "cumInflowRankPct"),
    }
    for channel, keys in higher.items():
        for key in keys:
            strict = _number(raw, f"channels.{channel}.strict.{key}")
            relaxed = _number(raw, f"channels.{channel}.relaxed.{key}")
            if strict is not None and relaxed is not None and strict < relaxed:
                invalid.append(f"channels.{channel}.{key}: strict 不得比 relaxed 更宽")
    for channel, keys in lower.items():
        for key in keys:
            strict = _number(raw, f"channels.{channel}.strict.{key}")
            relaxed = _number(raw, f"channels.{channel}.relaxed.{key}")
            if strict is not None and relaxed is not None and strict > relaxed:
                invalid.append(f"channels.{channel}.{key}: strict 不得比 relaxed 更宽")
    strict_cut = _number(raw, "boundary.activity.strict.excludeBottomPct")
    relaxed_cut = _number(raw, "boundary.activity.relaxed.excludeBottomPct")
    if strict_cut is not None and relaxed_cut is not None and strict_cut < relaxed_cut:
        invalid.append("boundary.activity.strict.excludeBottomPct 不得小于 relaxed")


def _check_bonus(raw: Mapping[str, Any], invalid: List[str]) -> None:
    cap = _number(raw, "channels.p3.bonuses.bonusCap")
    for key in ("recentLimitUp", "recentLimitDownHeat", "dragonTigerList",
                "controlledAnomaly", "reversalOrSecondWave"):
        value = _number(raw, f"channels.p3.bonuses.{key}")
        if value is not None and (value < 0 or (cap is not None and value > cap)):
            invalid.append(f"channels.p3.bonuses.{key} 必须在 [0, bonusCap]")
    if cap is not None and cap < 0:
        invalid.append("channels.p3.bonuses.bonusCap 不得为负")


def _check_excluded_codes(raw: Mapping[str, Any], invalid: List[str], db_path: Optional[Path]) -> None:
    from neckline.data.sw_industry import BAIJIU_L2_CODE, BAIJIU_L2_NAME

    codes = _dig(raw, "industry.excludedL2Codes")
    if not isinstance(codes, list):
        return
    if BAIJIU_L2_CODE not in codes:
        invalid.append(f"industry.excludedL2Codes 必须包含 {BAIJIU_L2_CODE}")
    with connection(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sw_industry_classify'"
        ).fetchone()
        if not exists or conn.execute("SELECT COUNT(*) FROM sw_industry_classify").fetchone()[0] == 0:
            return
        known = dict(conn.execute(
            "SELECT index_code,name FROM sw_industry_classify WHERE level='L2'"
        ).fetchall())
    unknown = sorted(set(codes) - set(known))
    if unknown:
        invalid.append(f"industry.excludedL2Codes 不在 SW2021 L2 中:{unknown}")
    if known.get(BAIJIU_L2_CODE) not in (None, BAIJIU_L2_NAME):
        logger.warning("[k9-params] %s 当前名称为 %s；仍按稳定代码排除",
                       BAIJIU_L2_CODE, known[BAIJIU_L2_CODE])


def validate(raw: Mapping[str, Any], *, db_path: Optional[Path] = None) -> Tuple[List[str], List[str], List[str]]:
    missing: List[str] = []
    invalid: List[str] = []
    extras: List[str] = []
    _walk(REQUIRED_SCHEMA, raw, "", missing, invalid, extras)
    if not missing:
        _check_fixed(raw, invalid)
        if _dig(raw, "factPackVersion") != PACK_VERSION:
            invalid.append(f"factPackVersion 必须等于当前事实包 {PACK_VERSION}")
        _check_positive(raw, invalid)
        _check_weights(raw, invalid)
        _strict_not_wider(raw, invalid)
        _check_bonus(raw, invalid)
        _check_excluded_codes(raw, invalid, db_path)
    return missing, invalid, extras


def _hot_identity(raw: Mapping[str, Any]) -> HotIdentityParams:
    return HotIdentityParams(
        raw["lookbackDays"], raw["dailyHeatTopPct"], raw["minHotDays"],
        raw["recentWindowDays"], raw["minRecentHotDays"])


def _p1(raw: Mapping[str, Any]) -> P1Tier:
    return P1Tier(
        raw["minVolMultiple"], raw["minRetPct"], raw["minIndustryExcessPct"],
        raw["minCloseLocation"], raw["breakoutWindowDays"],
        raw["maxDistanceBelowPriorHighPct"], _hot_identity(raw["hotIdentityExclusion"]))


def _p2(raw: Mapping[str, Any]) -> P2Tier:
    return P2Tier(
        raw["windowDays"], raw["minCumulativeDropPct"],
        raw["minDrawdownFromWindowHighPct"], raw["minIndustryUnderperformancePct"],
        raw["minVolMultiple"], raw["minCloseLocation"], raw["minDailyRetPct"])


def _p3(raw: Mapping[str, Any]) -> P3Tier:
    return P3Tier(
        raw["hotLookbackDays"], raw["dailyHeatTopPct"], raw["minHotDays"],
        raw["currentHeatTopPct"], raw["conflictLookbackDays"],
        raw["minAbsoluteMoveAsLimitRatio"], raw["minAmplitudeAsLimitRatio"],
        raw["minHugeVolMultiple"])


def _p4(raw: Mapping[str, Any]) -> P4Tier:
    return P4Tier(raw["dailyInflowRankPct"], raw["cumDays"],
                  raw["cumInflowRankPct"], raw["lagRankGap"])


def _build(raw: Mapping[str, Any], source_path: str, source_sha256: str) -> K9Params:
    activity = raw["boundary"]["activity"]
    p3_raw = raw["channels"]["p3"]
    bonuses = p3_raw["bonuses"]
    ranking = raw["ranking"]
    evaluation = raw["evaluation"]
    return K9Params(
        schema_version=raw["schemaVersion"], package_version=raw["packageVersion"],
        strategy_version=raw["strategyVersion"], fact_pack_version=raw["factPackVersion"],
        label_contract_version=LABEL_CONTRACT_VERSION, status=raw["status"],
        validation_mode=raw["validationMode"], parameterized_by=raw["parameterizedBy"],
        parameterized_at=raw["parameterizedAt"], approved_by=raw["approvedBy"],
        approved_at=raw["approvedAt"], approval_note=raw["approvalNote"],
        boundary=BoundaryParams(
            raw["boundary"]["newListingDays"], activity["typicalAmountWindowDays"],
            activity["participationWindowDays"], activity["minimumValidDays"],
            activity["weights"]["typicalAmountPercentile"],
            activity["weights"]["participationDensityPercentile"],
            activity["strict"]["excludeBottomPct"],
            activity["relaxed"]["excludeBottomPct"],
            raw["boundary"]["spikeFadeRetPct"], raw["boundary"]["spikeFadeGapPct"]),
        industry=IndustryParams(
            raw["industry"]["minMembers"], tuple(raw["industry"]["excludedL2Codes"]),
            HeatAbsentPolicy(raw["industry"]["heatAbsentPolicy"])),
        volume=VolumeParams(raw["volume"]["maDays"]),
        channels=ChannelParams(
            ChannelTiers(_p1(raw["channels"]["p1"]["strict"]),
                         _p1(raw["channels"]["p1"]["relaxed"])),
            ChannelTiers(_p2(raw["channels"]["p2"]["strict"]),
                         _p2(raw["channels"]["p2"]["relaxed"])),
            P3Params(
                DailyHeatParams(
                    p3_raw["dailyHeat"]["weights"]["amountPercentile"],
                    p3_raw["dailyHeat"]["weights"]["turnoverRatePercentile"]),
                _p3(p3_raw["strict"]), _p3(p3_raw["relaxed"]),
                P3Bonuses(
                    bonuses["bonusCap"], bonuses["recentLimitUp"],
                    bonuses["recentLimitDownHeat"], bonuses["dragonTigerList"],
                    bonuses["controlledAnomaly"], bonuses["reversalOrSecondWave"],
                    bonuses["recentLimitDownRiskLevelIncrement"])),
            ChannelTiers(_p4(raw["channels"]["p4"]["strict"]),
                         _p4(raw["channels"]["p4"]["relaxed"])),
        ),
        ranking=RankingParams(
            RankingWeights(ranking["weights"]["industryHeat"],
                           ranking["weights"]["patternStrength"],
                           ranking["weights"]["relay"]),
            {key: dict(ranking["patternSubWeights"][key]) for key in _SUB_WEIGHT_KEYS},
            ranking["relayLookbackDays"], RelaySource(ranking["relaySource"]),
            RelayScoring(ranking["relayScoring"])),
        quota=QuotaParams(
            raw["quota"]["min"], raw["quota"]["max"], raw["quota"]["floorPerChannel"],
            raw["quota"]["overStrictConsecutiveDays"]),
        scoring=ScoringParams(
            evaluation["touchThresholdPct"] / 100.0,
            abs(evaluation["riskThresholdPct"]) / 100.0,
            D1Reference(evaluation["d1ConfirmationReference"]),
            MatchedBaseline.INDUSTRY_MEDIAN),
        max_backfill_rounds=raw["explain"]["maxBackfillRounds"],
        evidence=dict(raw["evidence"]), source_path=source_path,
        source_sha256=source_sha256)


def load(path: Path, *, db_path: Optional[Path] = None) -> K9Params:
    p = Path(path)
    if not p.exists():
        raise ParamsUnavailable(missing=[f"参数包文件不存在:{p}"])
    try:
        content = p.read_bytes()
        raw = json.loads(content.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ParamsUnavailable(invalid=[f"参数包不是合法 UTF-8 JSON({p}):{exc}"]) from exc
    if not isinstance(raw, dict):
        raise ParamsUnavailable(invalid=[f"参数包顶层不是对象({p})"])
    missing, invalid, extras = validate(raw, db_path=db_path)
    if extras:
        invalid.append(f"参数包含未声明键:{extras}")
    if missing or invalid:
        raise ParamsUnavailable(missing=missing, invalid=invalid)
    return _build(raw, str(p), hashlib.sha256(content).hexdigest())


PARAM_DATACLASS_ROOTS: Tuple[type, ...] = (K9Params, P1Tier, P2Tier, P3Tier, P4Tier)


def _nested_dataclasses(annotation: Any) -> List[type]:
    if isinstance(annotation, type) and is_dataclass(annotation):
        return [annotation]
    result: List[type] = []
    for arg in get_type_args(annotation):
        result.extend(_nested_dataclasses(arg))
    return result


def param_dataclass_closure(*roots: type) -> List[type]:
    seen: List[type] = []

    def visit(cls: type) -> None:
        if not is_dataclass(cls) or cls in seen:
            return
        seen.append(cls)
        hints = get_type_hints(cls)
        for field in fields(cls):
            for nested in _nested_dataclasses(hints.get(field.name, field.type)):
                visit(nested)

    for root in roots:
        visit(root)
    return seen


def assert_no_field_defaults(cls: type) -> List[str]:
    roots = [cls, *(root for root in PARAM_DATACLASS_ROOTS if cls is K9Params and root is not cls)]
    offenders = []
    for current in param_dataclass_closure(*roots):
        for field in fields(current):
            if field.default is not MISSING or field.default_factory is not MISSING:  # type: ignore[misc]
                offenders.append(f"{current.__name__}.{field.name}")
    return offenders


__all__ = [
    "TO_BE_CALIBRATED", "LABEL_CONTRACT_VERSION", "HeatAbsentPolicy", "RelaySource",
    "RelayScoring", "D1Reference", "MatchedBaseline", "ENUM_PARAM_SLOTS",
    "K9_FIXED_VALUES", "ParamsUnavailable", "BoundaryParams", "IndustryParams",
    "VolumeParams", "HotIdentityParams", "P1Tier", "P2Tier", "DailyHeatParams",
    "P3Tier", "P3Bonuses", "P3Params", "P4Tier", "ChannelTiers", "ChannelParams",
    "RankingWeights", "RankingParams", "QuotaParams", "ScoringParams", "K9Params",
    "REQUIRED_SCHEMA", "validate", "load", "assert_no_field_defaults",
    "param_dataclass_closure", "PARAM_DATACLASS_ROOTS",
]
