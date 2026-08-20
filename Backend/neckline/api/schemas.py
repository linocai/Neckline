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


# v2.0.0(⑩-A):蓝图 §5.2 六枚卖出快捷标签的服务端码,唯一源在
# `neckline.sentinel.positions.CLOSE_REASON_CODES`(pydantic Literal 需要字面量,
# 不能引用变量,故此处手写同一份字符串——两处必须保持同步,新增码时两边一起改)。
CloseReasonLiteral = Literal[
    "STOP_LOSS", "TAKE_PROFIT", "TIME_EXIT", "INVALIDATION", "MANUAL",
    "SECTOR_WEAKENING", "TARGET_ZONE_REACHED", "ACTIVE_SWITCH", "AD_HOC",
]


class EvalWeeklyOut(BaseModel):
    """周度评价校准报告(⑨-C;含安慰剂对照臂)。`result` 原样透传
    `eval/calibration.py::CalibrationReport` 的字典形状(同 `WeeklyReviewOut.result`
    透传惯例)。

    ⚠ **评价是长期统计,不是单日打分**:`available=false` 时 `unavailableReason`
    必有值(样本窗未就绪 / 前向窗口还没走完),⛔ 不许拿半截样本给结论。"""

    weekStart: str = ""
    weekEnd: str = ""
    available: bool = False
    unavailableReason: Optional[str] = None
    result: Dict[str, Any] = Field(default_factory=dict)
    markdown: str = ""


# —— 4A.5 设置 ——————————————————————————————————————————————————————————

class PushKindOut(BaseModel):
    """一个通知 kind 的开关行(V2-⑪,plan §五 V2-⑪-B / D5)。`level` 是三级之一
    (`immediate`/`important`/`digest`),客户端据此分组展示;`label` 是服务端给的
    人读名(避免双端各抄一份中文映射,同 `boardLabel` 的反面教训)。"""
    kind: str
    level: str
    label: str
    enabled: bool
    # —— V2.4.0 P0:退役位(唯一源 `notify_kinds.RETIRED_KINDS`)————————————————
    # `True` = 该 kind 已退役,服务端**永不再发这类推送**,新客户端**隐藏这一行开关**。
    # 🔴 **行仍然下发、`PUT /settings/push` 仍要求给全 `ALL_KINDS`** —— 旧客户端照旧
    # 读写这个开关不受影响(P0.5「`push_retreat` 设置字段继续接受读写」)。
    # ⛔ **客户端不许硬编码一份退役 kind 黑名单** —— 那是第二份事实源(§3.14-B);
    #    也⛔ 不许把退役行从 `kinds` 里删掉:删了旧客户端下一次 PUT 就缺键 422。
    retired: bool = False


class PushSettingsOut(BaseModel):
    """V2-⑪ 起 = **按 kind 的开关清单**(不再是 V1 的六个具名布尔字段)。

    ⚠ `kinds` 顺序 = `notify_kinds.ALL_KINDS` 顺序(确定性,客户端可直接照序渲染)。"""
    kinds: List[PushKindOut] = Field(default_factory=list)


class SettingsProviderOut(BaseModel):
    """`GET /settings` 内嵌的精简 Provider 视图(plan §五 V2-② 契约变更原文字段集
    ——比专门的 `GET /settings/providers`〔`ProviderOut`〕少 `baseUrl`/
    `searchEngine`/`notes`,只给设置屏首屏摘要够用的五个字段)。"""
    name: str
    model: str
    hasWebSearch: bool
    keySet: bool                                  # 只回布尔,绝不回 key 明文
    enabled: bool


class TavilySettingsOut(BaseModel):
    """独立检索凭据安全视图：只说明是否已配置，永不回 key 或掩码。"""
    keySet: bool = False


class SettingsOut(BaseModel):
    """V2-②起:`llmProvider`/`llmKeySet` 两字段由 `providers`/`routes` 取代
    (plan §五 V2-②「契约变更」)。"""
    providers: List[SettingsProviderOut] = Field(default_factory=list)
    routes: Dict[str, str] = Field(default_factory=dict)   # {任务名: provider 名}
    tavily: TavilySettingsOut = Field(default_factory=TavilySettingsOut)
    push: PushSettingsOut
    reviewColMap: Dict[str, str] = Field(default_factory=dict)   # 4D 周复盘交割单列映射


# —— V2-② LLM Provider 注册表(自填制,plan §3.10-B)——————————————————————

class ProviderOut(BaseModel):
    """LLM Provider 安全视图:**绝不含 `api_key`**,只回 `keySet` 布尔。"""
    name: str
    baseUrl: str
    model: str
    hasWebSearch: bool
    searchEngine: Optional[str] = None
    notes: Optional[str] = None
    enabled: bool
    keySet: bool


class ProvidersListOut(BaseModel):
    items: List[ProviderOut] = Field(default_factory=list)


class ProviderCreateIn(BaseModel):
    """POST 请求体(新建)。`name` 已存在 → 409(须显式走 PUT 更新,防误覆盖)。"""
    name: str = Field(min_length=1)
    baseUrl: str = Field(min_length=1)
    model: str = Field(min_length=1)
    apiKey: Optional[str] = None
    hasWebSearch: bool = False
    searchEngine: Optional[str] = None
    notes: Optional[str] = None
    enabled: bool = True


class ProviderUpdateIn(BaseModel):
    """PUT 请求体(局部更新):未出现的字段不改(`model_fields_set` 判据,同
    `_extract_max_chase_pct_or_400` 先例);出现且为空串的 `apiKey`/`searchEngine`/
    `notes` 视为显式清空(同既有 `settings_store._clean()` 纪律)。"""
    baseUrl: Optional[str] = None
    model: Optional[str] = None
    apiKey: Optional[str] = None
    hasWebSearch: Optional[bool] = None
    searchEngine: Optional[str] = None
    notes: Optional[str] = None
    enabled: Optional[bool] = None


class LLMRoutesOut(BaseModel):
    routes: Dict[str, str] = Field(default_factory=dict)
    defaultProvider: Optional[str] = None


class LLMRoutesIn(BaseModel):
    """PUT 请求体:全量覆盖式写(同 `SettingsPushIn` 六字段必填风格,调用方须传
    完整状态)。`routes` 的键须落在 `neckline.llm.router.ALL_TASKS`,否则 422。"""
    routes: Dict[str, str] = Field(default_factory=dict)
    defaultProvider: Optional[str] = None


class TavilySettingsIn(BaseModel):
    """只写凭据。清除使用 DELETE，避免空字符串同时承担“不改/清除”两种语义。"""
    apiKey: str = Field(min_length=1)


class SettingsPushIn(BaseModel):
    """PUT 请求体(V2-⑪):**全量覆盖式**写按 kind 的推送开关。

    `kinds` 必须给全 `notify_kinds.ALL_KINDS` 的每一个键(缺键 / 未登记 kind → 422),
    承 V1「六字段均必填,防漏传静默重置某开关」的同一条纪律 —— 静默忽略会让用户
    以为自己关掉了某类通知而服务端根本没收到。"""
    kinds: Dict[str, bool] = Field(default_factory=dict)


class DeviceRegisterIn(BaseModel):
    token: str
    platform: str = "ios"


class SettingsReviewColMapIn(BaseModel):
    colMap: Dict[str, str] = Field(default_factory=dict)


# 蓝图 §2.2「主要补充」七枚标签的服务端码(唯一源在此;中文短语 → 码的对应关系见
# 各码行内注释,客户端展示层换算沿 `boardLabel` 先例)。
NoteLabelLiteral = Literal[
    "THEME_SHIFT",        # 题材切换
    "LEADER_REACTIVATE",  # 龙头重新激活
    "VOLUME_BREAKOUT",    # 放量突破
    "WEAK_TO_STRONG",     # 弱转强
    "CORE_POSITION",      # 容量中军
    "NEWS_CATALYST",      # 消息催化
    "PURE_TAPE_READING",  # 纯盘口判断
]


# —— 4D 周复盘工作台 ————————————————————————————————————————————————————
#
# `result` 直接透传 `neckline.review.reconcile.weekly_review_dict()` 的完整快照
# (`roundTrips` / `closedRoundTrips` / `stats` / `forcedReview`,camelCase,该函数
# 本身就是 API 响应与 `reviews.result_json` 落库共用的唯一形状源)——同透传惯例
# (schemas.py 顶部约定),不在 API 层重复声明一套嵌套 pydantic 模型镜像领域字段。
# ⚠ **V2.5.0 S1 起 `planChecks` / `disciplineViolations` / `stopDiscipline` /
# `charterSegments` 等八个键已不再产出**(K8 章程判据整块退役)。`reviews` 表里
# V2.4.x 及更早的**历史行**仍带着它们(写入当时冻住的快照),客户端按
# `decodeIfPresent` 读即可;⛔ 不回填、不改写历史行(裁定 6)。

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


class ReviewSegmentOut(BaseModel):
    """复盘板块「累计」页里的**一段**。V2.5.0 S11 起共**四段**
    (校准 / 对账 / 结论存档 / 观察项),形状统一。

    🔴 **每段各自带 `available` + `unavailableReason`,⛔ 不许拿一个总开关罩住四段** ——
    校准产物没生成、这周没传交割单、这周还没写结论是**三件互不相干的事**,合成一句
    读者就分不清哪个没有。三态读法(plan §五⑤ 验收原文「有 / 没有 / 没取到」):

      · **有**   → `available=true` + 有内容;
      · **没有** → `available=true` + 空内容(该段自己的空态文案说清为什么空);
      · **没取到** → `available=false` + `unavailableReason`(⛔ 不许拿空数组冒充)。

    ⚠ **校准段与对账 / 结论两段的空态刻意判得不一样,⛔ 别"统一"**:校准产物缺席 =
    **系统自己那一步没跑**(周度离线作业未运行)→ 那是「没看」→ `available=false`;
    对账与结论缺席 = 输入(券商交割单 / 用户写的结论)**只能由用户给**、系统查过表
    确实没有 → 那是「没有」→ `available=true` + `detail.found=false`。
    两者给用户的动作完全不同(等系统 vs 自己去做),⛔ 别合并。

    `items` / `detail` **原样透传**领域层形状(同 `WeeklyReviewOut.result` /
    `EvalWeeklyOut.result` 的既定惯例)—— 在 API 层再镜像一套嵌套模型只会多一处会漂的
    定义。`label` 由服务端给人读名(同 `PushKindOut.label` 先例,免双端各抄一份中文)。"""

    available: bool = False
    unavailableReason: Optional[str] = None
    label: str = ""
    asOf: str = ""                          # 该段的时点标识:画像期 / ISO 周 / 校准窗口
    items: List[Dict[str, Any]] = Field(default_factory=list)
    detail: Dict[str, Any] = Field(default_factory=dict)


class ReviewOverviewOut(BaseModel):
    """复盘板块「累计」页的聚合读(`GET /review/overview`)。

    **零现算**:四段全部读**已冻结 / 已落盘**的产物 —— 校准报告由离线周度作业算好落盘
    (§七 P0-23:本端点与常驻服务同进程,重活进常驻服务 = 卡死不报错),对账读
    `reviews` 表,结论读 `review_conclusions` 表。⛔ 读不到就说读不到,**永不在线补算**。
    ⚠ **装订材料刻意不在这里**:它要读 parquet 行情,属于「点一下才算」的动作,
    单独走 `GET /review/bindery`(⛔ 别把它塞进这个每次进板块都会拉的聚合读)。

    **包成绩单 = `calibration.detail.strata` 本身**(产物原文已按
    `pack_version × verification_ruleset_version` 分层)——⛔ 不另建第二份聚合,
    那就是「同一个数两个算法」的老病。

    🔴 **本端点一律不 404**(空态走各段的 `available=false`)→ V2.1 **零新增 reason
    字符串**,`SERVER_REASONS` 与客户端 `mapReason` 一字不动。"""

    weekStart: str = ""
    weekEnd: str = ""
    weekKey: str = ""                       # ISO 周键(`YYYY-Www`),对账段按它取
    calibration: ReviewSegmentOut = Field(default_factory=ReviewSegmentOut)
    reconcile: ReviewSegmentOut = Field(default_factory=ReviewSegmentOut)
    # V2.5.0 S11:结论存档段(架构 §六 第 3 件事)。同对账段的三态读法 ——
    # `available=true` + `detail.found=false` = 「这周还没写结论」(⛔ 不是「没取到」)。
    conclusions: ReviewSegmentOut = Field(default_factory=ReviewSegmentOut)
    observations: ReviewSegmentOut = Field(default_factory=ReviewSegmentOut)
    # 🔴 V2.5.0 S1:`preference` / `capability`(`profile/` 整包退役)与
    # `selectionClock` / `tradeClock` / `iterationSuggestions`(双时钟复盘退役)
    # 五段**已删除** —— 它们的数据源已随 K8 一起下线,留着只会让客户端每次都拿到
    # 一段 `available=false` 的空壳,把"这个功能没了"伪装成"这次没取到"。
    # 复盘板块的目标形态见 PROJECT_PLAN §5.9 / §5.11,S11 收口。


class ReviewBinderyOut(BaseModel):
    """行情材料装订(V2.5.0 S11,架构 §六 第 2 件事)。

    🔴 **零 LLM、零写库**:这一层只取数与排版(架构 §六 逐字「这一层无 LLM 调用」)。
    `binding` 原样透传 `review/bindery.py::WeekBinding.to_dict()`(同 `result` 透传
    惯例,⛔ 不在 API 层镜像一套会漂的嵌套模型);`markdown` 是同一份材料的排版结果,
    供用户**整段复制到聊天框**。

    ⚠ **`found=False` 是「这周没上传过交割单」**(⛔ 不是 404、⛔ 不是「系统没跑」):
    装订的输入只能由用户给,系统查过 `reviews` 表确实没有那一行。
    ⚠ **`binding.gaps` 与逐笔的 `gaps` 必须原样呈现**:哪一段材料没取到、为什么,
    是材料的一部分;⛔ 客户端不许把它折叠掉(那等于让缺失静默)。"""

    ok: bool = True
    found: bool = False
    week: str = ""
    binding: Optional[Dict[str, Any]] = None
    markdown: str = ""
    unavailableReason: Optional[str] = None


class ReviewConclusionIn(BaseModel):
    """存一版复盘结论(append-only:每存一次 = 新版本,⛔ 老版本一个字不动)。"""

    week: str
    title: str
    body: str
    tags: List[str] = Field(default_factory=list)
    author: str = "user"


class ReviewConclusionsOut(BaseModel):
    """结论存档的读回。`latest` = 该周最新版;`versions` = 该周全部版本(升序);
    `matches` = 检索命中(每周只出最新版,按周降序)。

    ⚠ `latest=None` = **那周还没写过结论**(⛔ 别渲染成「这周没问题」)。"""

    ok: bool = True
    week: str = ""
    latest: Optional[Dict[str, Any]] = None
    versions: List[Dict[str, Any]] = Field(default_factory=list)
    matches: List[Dict[str, Any]] = Field(default_factory=list)


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
    "OkOut",
    "EvalWeeklyOut",
    "PushKindOut",
    "PushSettingsOut",
    "SettingsProviderOut",
    "TavilySettingsOut",
    "SettingsOut",
    "ProviderOut",
    "ProvidersListOut",
    "ProviderCreateIn",
    "ProviderUpdateIn",
    "LLMRoutesOut",
    "LLMRoutesIn",
    "TavilySettingsIn",
    "SettingsPushIn",
    "DeviceRegisterIn",
    "SettingsReviewColMapIn",
    "WeeklyReviewOut",
    "ReviewUploadOut",
    "ReviewSegmentOut",
    "ReviewOverviewOut",
    "ReviewBinderyOut",
    "ReviewConclusionIn",
    "ReviewConclusionsOut",
    "ReviewGetOut",
]
