"""API 出入参 schema(plan 4A 契约)。沿 LinoN `api/schemas.py` 姿势(pydantic)。

约定:
    · **出参** camelCase(SwiftUI Codable 直接解码,§4C);**入参**沿 plan 契约的
      snake_case(`buy_price`/`entry_reason`/`sell_price`)。
    · sentiment / sectors 直接透传报告落库时的结构化快照(`Dict`/`List[Dict]`)——
      避免在 API 层重抄一份领域字段定义(同码不重写铁律),客户端拿到的就是报告
      存档的完整快照,不丢字段、不随 `SentimentDashboard` 演进而漂移。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class OkOut(BaseModel):
    ok: bool = True


# —— 4A.2 报告 ————————————————————————————————————————————————————————

class LLMJudgmentOut(BaseModel):
    verdict: str                 # 通过 | 否决 | 未激活
    narrative: str
    degraded: bool


class CandidateOut(BaseModel):
    rank: int
    code: str
    name: str
    score: float
    board: str
    # 四件套(§2.2/§2.3):买点 / 止损(-5%) / 目标 / 证伪条件
    buyPoint: str
    stop: str
    target: str
    invalidation: str
    invalidationSpec: Dict[str, Any] = Field(default_factory=dict)
    entrySpec: Dict[str, Any] = Field(default_factory=dict)
    formTags: List[str] = Field(default_factory=list)         # 价量结构形态标签
    hotSectors: List[str] = Field(default_factory=list)       # 命中今日热门板块(含年龄)
    sectorNames: List[str] = Field(default_factory=list)
    llmJudgment: Optional[LLMJudgmentOut] = None              # 仅前 10 只有


class ReportOut(BaseModel):
    tradeDate: str
    generatedAt: str
    strategyVersion: str
    sentiment: Dict[str, Any]                # SentimentDashboard 快照(含 position_quota 三态)
    sectors: List[Dict[str, Any]]            # 强势板块 + 板块年龄
    candidates: List[CandidateOut]
    degraded: bool = False
    reason: str = ""


# —— 4A.3 盘中看板 ————————————————————————————————————————————————————

class RetreatBrakeOut(BaseModel):
    active: bool
    reason: str = ""


class BoardEventOut(BaseModel):
    sentinel: str                # 买点(entry) / 证伪(invalidation) / 持仓(holding)
    code: str
    name: str
    eventKey: str
    verdict: str                 # 判决文案(哨兵已落库的 reason 文本)
    ts: str


class BoardOut(BaseModel):
    tradeDate: str
    asof: str = ""
    retreatBrake: RetreatBrakeOut
    events: List[BoardEventOut] = Field(default_factory=list)


# —— 4A.4 持仓 ————————————————————————————————————————————————————————

class PositionOut(BaseModel):
    id: int
    code: str
    name: str
    buyPrice: float
    qty: int
    entryReason: str = ""
    buyDate: str
    price: float                 # 哨兵最近一拍 / EOD 兜底;拉不到 → 0.0
    status: str
    stopLine: float              # = buy×0.95 派生(§2.1 -5% 单一常量)
    stopOrderChecked: bool = False   # 用户自证「已挂 -5% 条件单」(真对账在 4D 周复盘)


class PositionsOut(BaseModel):
    holdings: List[PositionOut] = Field(default_factory=list)


class PositionOpenIn(BaseModel):
    code: str
    name: Optional[str] = None
    buy_price: float
    qty: int
    entry_reason: str = ""


class PositionOpenOut(BaseModel):
    ok: bool = True
    position_id: int
    stop_line: float


class PositionCloseIn(BaseModel):
    sell_price: float
    sell_time: Optional[str] = None      # 'YYYYMMDD' 可选,缺省=今日


# —— 4A.5 问询台 + 设置 ————————————————————————————————————————————————

class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class InquiryIn(BaseModel):
    code: str
    messages: List[ChatMessageIn] = Field(default_factory=list)


# 裁决二值(硬约束,§2.5:永不「现在就买」)——枚举只两值,是「永不买」的双保险之一。
VERDICT_REJECT = "不符合"
VERDICT_PASS = "初审通过进海选池"


class InquiryOut(BaseModel):
    ok: bool = True
    code: str
    reply: str                                   # 自由对话体(§2.7)
    verdict: Literal["不符合", "初审通过进海选池"]
    evidence: List[str] = Field(default_factory=list)
    degraded: bool = False                       # LLM 段是否走了降级占位


class PushSettingsOut(BaseModel):
    report: bool
    retreatBrake: bool


class SettingsOut(BaseModel):
    llmProvider: Optional[str] = None
    llmKeySet: bool = False                       # 只回布尔,绝不回 key 明文
    push: PushSettingsOut
    reviewColMap: Dict[str, str] = Field(default_factory=dict)   # 4D 周复盘交割单列映射


class SettingsLLMIn(BaseModel):
    provider: Literal["glm", "kimi"]
    apiKey: str


class SettingsPushIn(BaseModel):
    report: bool
    retreatBrake: bool


class DeviceRegisterIn(BaseModel):
    token: str
    platform: str = "ios"


class SettingsReviewColMapIn(BaseModel):
    colMap: Dict[str, str] = Field(default_factory=dict)


# —— 4D 周复盘工作台 ————————————————————————————————————————————————————
#
# `result` 直接透传 `neckline.review.reconcile.weekly_review_dict()` 的完整快照
# (roundTrips/planChecks/disciplineViolations/stopDiscipline/stats/forcedReview
# 等,camelCase,该函数本身就是 API 响应与 `reviews.result_json` 落库共用的唯一
# 形状源)——同 `ReportOut.sentiment/sectors` 的透传惯例(schemas.py 顶部约定),
# 不在 API 层重复声明一套嵌套 pydantic 模型去镜像领域字段(同码不重写)。

class WeeklyReviewOut(BaseModel):
    week: str
    result: Dict[str, Any]
    material: str = ""


class ReviewUploadOut(BaseModel):
    ok: bool = True
    weeks: List[WeeklyReviewOut] = Field(default_factory=list)
    parseWarnings: List[str] = Field(default_factory=list)   # 解析层面的问题(未知格式/反查失败/非法工作簿等)
    dataWarnings: List[str] = Field(default_factory=list)    # FIFO 数据完整性问题(如卖出找不到匹配买入)
    sheetFormats: Dict[str, str] = Field(default_factory=dict)


class ReviewGetOut(BaseModel):
    ok: bool = True
    found: bool = False
    week: str = ""
    generatedAt: str = ""
    # None(JSON null,非 `{}`)当 `found=False`——客户端据此把 `result` 解码成强类型
    # struct 的 Optional,不必为"空字典 vs 合法结果"写一套容错回退逻辑。
    result: Optional[Dict[str, Any]] = None
    material: str = ""


__all__ = [
    "OkOut", "LLMJudgmentOut", "CandidateOut", "ReportOut",
    "RetreatBrakeOut", "BoardEventOut", "BoardOut",
    "PositionOut", "PositionsOut", "PositionOpenIn", "PositionOpenOut", "PositionCloseIn",
    "ChatMessageIn", "InquiryIn", "InquiryOut", "VERDICT_REJECT", "VERDICT_PASS",
    "PushSettingsOut", "SettingsOut", "SettingsLLMIn", "SettingsPushIn", "DeviceRegisterIn",
    "SettingsReviewColMapIn", "WeeklyReviewOut", "ReviewUploadOut", "ReviewGetOut",
]
