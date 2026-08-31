"""K9-v3 approved parameter contract.  It deliberately contains no strategy values."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "k9-params-v3"
STRATEGY_VERSION = "K9-v3"
FACT_PACK_VERSION = "fp-4"
LABEL_CONTRACT_VERSION = "d2-v2"


class ParamsUnavailable(Exception):
    def __init__(self, missing: Sequence[str] = (), invalid: Sequence[str] = ()):
        self.missing, self.invalid = tuple(missing), tuple(invalid)
        super().__init__(self.describe())
    def describe(self) -> str:
        x = [*(f"缺键 {v}" for v in self.missing), *(f"无效 {v}" for v in self.invalid)]
        return "参数未配置" + (f"（{'；'.join(x)}）" if x else "")
    def gaps(self) -> list[str]: return [*(f"缺键 {v}" for v in self.missing), *(f"无效 {v}" for v in self.invalid)]


@dataclass(frozen=True)
class V3Params:
    package_version: str
    source_sha256: str
    raw: Mapping[str, Any]
    @property
    def channels(self) -> Mapping[str, Any]: return self.raw["channels"]
    @property
    def quotas(self) -> Mapping[str, int]: return self.raw["quotas"]


# Exact shapes make a parameter addition a deliberate schema change, never a silent default.
SHAPES: dict[str, set[str]] = {
 "": {"schemaVersion","packageVersion","strategyVersion","factPackVersion","labelContractVersion","status","parameterizedBy","parameterizedAt","approvedBy","approvedAt","approvalNote","boundary","channels","quotas","settlement","evidence"},
 "boundary": {"newListingTradingDays","activity","d0Liquidity","excludedL2Codes"},
 "boundary.activity": {"windowDays","minimumValidDays","amountWeight","participationWeight","excludeBottomPct"},
 "boundary.d0Liquidity": {"minimumAmountCny","freeFloatMarketValueRatio"},
 "channels": {"p2","p3","p4"},
 "channels.p2": {"enabled","recall","ranking"},
 "channels.p2.recall": {"windowDays","volumeBaselineDays","minCumulativeDropPct","minDrawdownPct","minIndustryUnderperformancePct","minVolumeMultiple","supportCloseLocationPct","supportDailyReturnPct"},
 "channels.p2.ranking": {"oversoldDepthWeight","industryUnderperformanceWeight","lowRecoveryWeight","declineDecelerationWeight","turnoverWeight"},
 "channels.p3": {"enabled","identity","opportunity","ranking"},
 "channels.p3.identity": {"windowDays","eventWindowDays","volumeBaselineDays","minHotDays","topPct","largeMoveLimitWidthPct","largeMoveAmplitudePct","largeMoveVolumeMultiple","hotness"},
 "channels.p3.identity.hotness": {"amountWeight","turnoverWeight"},
 "channels.p3.opportunity": {"requireDirectionResolved","maxOverextendedPct","minRelativeLeadership","minCapitalRetention","minStructureIntegrity"},
 "channels.p3.ranking": {"directionWeight","notOverextendedWeight","relativeLeadershipWeight","capitalRetentionWeight","structureIntegrityWeight","recentLimitDownRiskDeduction"},
 "channels.p4": {"enabled","benchmark","industry","stock","ranking","playbookBounds"},
 "channels.p4.benchmark": {"indexCode"},
 "channels.p4.industry": {"windowDays","minMembers","minBreadthPct","minOversoldRelativePct","minRepairPct","maxIndustries","perIndustryStockCap"},
 "channels.p4.stock": {"minRelativeStrength","minCoreScore","minLiquidityScore"},
 "channels.p4.ranking": {"relativeStrengthWeight","coreWeight","liquidityWeight"},
 "channels.p4.playbookBounds": {"minimumMemberCoverageMin","minimumMemberCoverageMax","medianReturnMin","medianReturnMax","breadthMin","breadthMax","relativeBenchmarkReturnMin","relativeBenchmarkReturnMax","relativeIndustryReturnMin","relativeIndustryReturnMax"},
 "settlement": {"d1","d2"},
 "settlement.d1": {"enhancedReturnPct","enhancedCloseLocationPct","weakenedReturnPct"},
 "settlement.d2": {"opportunityReturnPct","continuationReturnPct","riskReturnPct"},
 "quotas": {"p2","p3","p4"},
}
_P2_W = SHAPES["channels.p2.ranking"]
_P3_W = SHAPES["channels.p3.ranking"] - {"recentLimitDownRiskDeduction"}
_P4_W = SHAPES["channels.p4.ranking"]


def _at(raw: Mapping[str, Any], path: str) -> object:
    out: object = raw
    for key in path.split(".") if path else ():
        if not isinstance(out, Mapping): return None
        out = out.get(key)
    return out


def _num(v: object, path: str, invalid: list[str], lo: float | None = None, hi: float | None = None, integer: bool = False) -> None:
    if isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(float(v)):
        invalid.append(f"{path} 必须为有限数值"); return
    if integer and (not isinstance(v, int) or v < 0): invalid.append(f"{path} 必须为非负整数")
    if lo is not None and float(v) < lo: invalid.append(f"{path} 必须 >= {lo}")
    if hi is not None and float(v) > hi: invalid.append(f"{path} 必须 <= {hi}")


def _weights(raw: Mapping[str, Any], path: str, keys: set[str], invalid: list[str]) -> None:
    obj = _at(raw,path)
    if not isinstance(obj,Mapping): return
    vals=[]
    for key in keys:
        value=obj.get(key); _num(value,f"{path}.{key}",invalid,0,1)
        if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(float(value)): vals.append(float(value))
    if len(vals)==len(keys) and not math.isclose(sum(vals),1.0,abs_tol=1e-9): invalid.append(f"{path} 权重和必须为 1.0")


def validate(raw: object) -> tuple[list[str], list[str]]:
    if not isinstance(raw,Mapping): return [],["顶层不是对象"]
    missing: list[str]=[]; invalid: list[str]=[]
    for path, shape in SHAPES.items():
        obj=_at(raw,path)
        if not isinstance(obj,Mapping): missing.append(path or "顶层"); continue
        prefix=f"{path}." if path else ""
        missing.extend(prefix+k for k in sorted(shape-set(obj)))
        invalid.extend(prefix+k+" 是未知键" for k in sorted(set(obj)-shape))
    for key,expected in (("schemaVersion",SCHEMA_VERSION),("strategyVersion",STRATEGY_VERSION),("factPackVersion",FACT_PACK_VERSION),("labelContractVersion",LABEL_CONTRACT_VERSION),("status","approved")):
        if raw.get(key)!=expected: invalid.append(f"{key} 必须为 {expected}")
    for key in ("packageVersion","parameterizedBy","parameterizedAt","approvedBy","approvedAt","approvalNote"):
        if not isinstance(raw.get(key),str) or not raw.get(key).strip(): invalid.append(f"{key} 必须为非空字符串")
    if not isinstance(raw.get("evidence"),Mapping): invalid.append("evidence 必须为对象")
    ints=("boundary.newListingTradingDays","boundary.activity.windowDays","boundary.activity.minimumValidDays","channels.p2.recall.windowDays","channels.p2.recall.volumeBaselineDays","channels.p3.identity.windowDays","channels.p3.identity.eventWindowDays","channels.p3.identity.volumeBaselineDays","channels.p3.identity.minHotDays","channels.p4.industry.windowDays","channels.p4.industry.minMembers","channels.p4.industry.maxIndustries")
    for path in ints: _num(_at(raw,path),path,invalid,1,None,True)
    _num(_at(raw,"boundary.d0Liquidity.minimumAmountCny"),"boundary.d0Liquidity.minimumAmountCny",invalid,0)
    ratios=("boundary.activity.amountWeight","boundary.activity.participationWeight","boundary.activity.excludeBottomPct","boundary.d0Liquidity.freeFloatMarketValueRatio","channels.p2.recall.minCumulativeDropPct","channels.p2.recall.minDrawdownPct","channels.p2.recall.minIndustryUnderperformancePct","channels.p2.recall.supportCloseLocationPct","channels.p2.recall.supportDailyReturnPct","channels.p3.identity.topPct","channels.p3.identity.largeMoveLimitWidthPct","channels.p3.identity.largeMoveAmplitudePct","channels.p3.opportunity.maxOverextendedPct","channels.p3.opportunity.minRelativeLeadership","channels.p3.opportunity.minCapitalRetention","channels.p3.opportunity.minStructureIntegrity","channels.p4.industry.minBreadthPct","channels.p4.industry.minOversoldRelativePct","channels.p4.industry.minRepairPct","channels.p4.stock.minRelativeStrength","channels.p4.stock.minCoreScore","channels.p4.stock.minLiquidityScore","settlement.d1.enhancedReturnPct","settlement.d1.enhancedCloseLocationPct","settlement.d2.opportunityReturnPct","settlement.d2.continuationReturnPct")
    for path in ratios: _num(_at(raw,path),path,invalid,0,1)
    for path in ("channels.p2.recall.minVolumeMultiple","channels.p3.identity.largeMoveVolumeMultiple"): _num(_at(raw,path),path,invalid,0)
    for path in ("settlement.d1.weakenedReturnPct","settlement.d2.riskReturnPct"): _num(_at(raw,path),path,invalid,-1,0)
    activity=_at(raw,"boundary.activity")
    if isinstance(activity,Mapping):
        a,b=activity.get("amountWeight"),activity.get("participationWeight")
        if all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(float(x)) for x in (a,b)) and not math.isclose(float(a)+float(b),1.0,abs_tol=1e-9): invalid.append("boundary.activity 权重和必须为 1.0")
    hotness=_at(raw,"channels.p3.identity.hotness")
    if isinstance(hotness,Mapping):
        a,b=hotness.get("amountWeight"),hotness.get("turnoverWeight")
        _num(a,"channels.p3.identity.hotness.amountWeight",invalid,0,1); _num(b,"channels.p3.identity.hotness.turnoverWeight",invalid,0,1)
        if all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(float(x)) for x in (a,b)) and not math.isclose(float(a)+float(b),1.0,abs_tol=1e-9): invalid.append("channels.p3.identity.hotness 权重和必须为 1.0")
    _weights(raw,"channels.p2.ranking",_P2_W,invalid); _weights(raw,"channels.p3.ranking",_P3_W,invalid); _weights(raw,"channels.p4.ranking",_P4_W,invalid)
    _num(_at(raw,"channels.p3.ranking.recentLimitDownRiskDeduction"),"channels.p3.ranking.recentLimitDownRiskDeduction",invalid,0,1)
    for channel in ("p2","p3","p4"):
        on=_at(raw,f"channels.{channel}.enabled"); quota=_at(raw,f"quotas.{channel}")
        if not isinstance(on,bool): invalid.append(f"channels.{channel}.enabled 必须为布尔值")
        _num(quota,f"quotas.{channel}",invalid,0,None,True)
        if on is True and quota==0: invalid.append(f"启用通道 {channel} 必须有正额度")
        if on is False and quota not in (None,0): invalid.append(f"禁用通道 {channel} 的额度必须为 0")
    for channel, maximum in (("p2", 5), ("p3", 8)):
        quota = _at(raw, f"quotas.{channel}")
        if isinstance(quota, int) and not isinstance(quota, bool) and quota > maximum:
            invalid.append(f"quotas.{channel} 不能超过 {maximum}")
    p4_industry = _at(raw, "channels.p4.industry")
    p4_quota = _at(raw, "quotas.p4")
    if isinstance(p4_industry, Mapping):
        per_cap = p4_industry.get("perIndustryStockCap")
        _num(per_cap, "channels.p4.industry.perIndustryStockCap", invalid, 1, None, True)
        maximum_industries = p4_industry.get("maxIndustries")
        if (isinstance(per_cap, int) and not isinstance(per_cap, bool)
                and isinstance(maximum_industries, int) and not isinstance(maximum_industries, bool)
                and isinstance(p4_quota, int) and not isinstance(p4_quota, bool)
                and p4_quota > per_cap * maximum_industries):
            invalid.append("quotas.p4 不能超过 maxIndustries × perIndustryStockCap")
    p4_bounds = _at(raw, "channels.p4.playbookBounds")
    if isinstance(p4_bounds, Mapping):
        ranges = (
            ("minimumMemberCoverage", 0, 1), ("medianReturn", -1, 1),
            ("breadth", 0, 1), ("relativeBenchmarkReturn", -1, 1),
            ("relativeIndustryReturn", -1, 1),
        )
        for name, lo, hi in ranges:
            minimum, maximum = p4_bounds.get(f"{name}Min"), p4_bounds.get(f"{name}Max")
            _num(minimum, f"channels.p4.playbookBounds.{name}Min", invalid, lo, hi)
            _num(maximum, f"channels.p4.playbookBounds.{name}Max", invalid, lo, hi)
            if (isinstance(minimum, (int, float)) and not isinstance(minimum, bool)
                    and isinstance(maximum, (int, float)) and not isinstance(maximum, bool)
                    and math.isfinite(float(minimum)) and math.isfinite(float(maximum))
                    and float(minimum) > float(maximum)):
                invalid.append(f"channels.p4.playbookBounds.{name}Min 不能大于 {name}Max")
    excluded=_at(raw,"boundary.excludedL2Codes")
    if not isinstance(excluded,list) or any(not isinstance(x,str) or not x for x in excluded) or len(set(excluded or []))!=len(excluded or []): invalid.append("boundary.excludedL2Codes 必须为不重复的非空字符串数组")
    if not isinstance(_at(raw,"channels.p3.opportunity.requireDirectionResolved"),bool): invalid.append("channels.p3.opportunity.requireDirectionResolved 必须为布尔值")
    code=_at(raw,"channels.p4.benchmark.indexCode")
    if not isinstance(code,str) or not code.strip(): invalid.append("channels.p4.benchmark.indexCode 必须为非空字符串")
    elif code.strip().upper().endswith(".SI"): invalid.append("channels.p4.benchmark.indexCode 不得使用申万 .SI 行业代码")
    return missing,invalid


def load(path: Path) -> V3Params:
    target=Path(path)
    if not target.exists(): raise ParamsUnavailable(missing=[f"参数包文件不存在:{target}"])
    try:
        blob=target.read_bytes(); raw=json.loads(blob.decode("utf-8"))
    except Exception as exc: raise ParamsUnavailable(invalid=[f"不是合法 UTF-8 JSON:{exc}"]) from exc
    missing,invalid=validate(raw)
    if missing or invalid: raise ParamsUnavailable(missing,invalid)
    return V3Params(str(raw["packageVersion"]),hashlib.sha256(blob).hexdigest(),raw)


__all__=["V3Params","ParamsUnavailable","load","validate","SCHEMA_VERSION","STRATEGY_VERSION","FACT_PACK_VERSION","LABEL_CONTRACT_VERSION"]
