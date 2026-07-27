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


class PermanentBoardStatusOut(BaseModel):
    """五常驻板块诊断漏斗(v1.3-③-C3 `_permanent_board_status`,v1.3-⑥ 后端补齐透出)。
    **报告级构件,非本票专属**——`build_intel_candidates` 只能经候选列表进报告快照,
    0 保底板块自身无候选可挂,故这份完整列表挂在**每一只候选**的 `intelRank.
    permanentBoardStatus` 上,客户端从任一候选读到即可(通常取第一只候选或去重后展示)。
    0 只/不足 2 只时 `note` 必须说清「为什么」(行业不属主导 / 命中 K4 安检 / 被在前
    常驻板块认领),0 只明标「宁缺毋滥、非静默空白」——守项目「『没有』和『没看』必须
    能分开」原则,**静默空白是禁止的**。"""
    board: str                    # 常驻板块中文名
    surviveCount: int = 0         # 过②卫生线(流动性/次新/趋势/ST)的成员数
    industryGatePass: int = 0     # 其中行业属本板块主导行业(过行业闸)的数量
    industryGateBlocked: int = 0  # 过②但行业不属主导、被行业闸挡下的数量
    hardCutBlocked: int = 0       # 过行业闸但命中 K4 hard_cut、被安检拦截的数量
    quotaFilled: int = 0          # 实际认领的保底名额数(≤ QUOTA_PER_PERMANENT_BOARD=2)
    note: str = ""                # 人读文案(满额简述;不足额/0 只时说清「为什么」)


class IntelRankOut(BaseModel):
    """候选情报排序理由(v1.3-③-C3,§2.3 语义变更)。候选=「过完安检、值得关注的票」
    非「会涨的票」——客户端据此写对文案(不标「推荐买点」),展示情报维度。"""
    sectorFlow: Optional[float] = None      # 所属常驻/暴起板块最大净流入(万元,C2;无数据=None)
    themePersistDays: int = 0               # 题材持续天数(反用:1天新鲜>2-3警惕;≥4已在③剔)
    highElasticity: bool = False            # 高弹板块(GEM/STAR;生成域刻意含高弹,标注给人判)
    # —— v1.3-⑥ 后端补齐(数据已在 v1.3-③-C3 落 `intel_rank` 字典/报告快照里就绪,
    # 此前 pydantic 未声明这三键 → 默认丢弃,本次补字段透出;逻辑零改动)—————————————
    source: str = ""              # quota(常驻保底)| competition(情报竞争)| forced(问询强制)。
                                   # 旧报告(建于本字段前)读回空串——客户端未识别值原样透传不崩。
    industry: str = ""            # 该票行业(stock_basic.industry,过行业闸后的代表行业),
                                   # 让用户看清「凭什么在这个板块栏」;查不到/旧报告 → 空串。
    permanentBoardStatus: List[PermanentBoardStatusOut] = Field(default_factory=list)


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
    # v1.3-③-C3 候选语义变更:候选生成脱离 K1 entry mask,改情报筛选管线。
    # k4Flags:K4 avoid_flag 命中码(打标保留;hard_cut 已在服务端拦截出池、不出现在榜)。
    # intelRank:情报排序理由(资金流强度/题材天数/高弹标注)。旧报告(建于本字段前)
    # 读回为默认空(前向兼容,同 watchlist/intel 惯例)。
    k4Flags: List[str] = Field(default_factory=list)
    intelRank: IntelRankOut = Field(default_factory=IntelRankOut)
    llmJudgment: Optional[LLMJudgmentOut] = None              # 仅前 10 只有


class WatchlistCheckLLMOut(BaseModel):
    verdict: str
    narrative: str
    degraded: bool


class WatchlistCheckOut(BaseModel):
    """自选体检单只快照(plan §五 v1.1-C.3)。字段形状与 `CandidateOut` 四件套对齐
    (buyPoint/stop/target/invalidation 命名一致),供客户端复用候选卡的四件套布局
    (§五 v1.1-F.2「复用 CandidateRow 四件套布局」)。"""
    code: str
    name: str
    pinned: bool
    source: str
    hasData: bool = True
    close: float = 0.0
    board: str = "MAIN"
    score: Optional[float] = None
    patternTags: List[str] = Field(default_factory=list)
    hotSectors: List[str] = Field(default_factory=list)
    sectorNames: List[str] = Field(default_factory=list)
    greenLight: bool = False               # 纪律红绿灯:True=🟢可动,False=🔴禁买
    disqualifiers: List[str] = Field(default_factory=list)
    buyPointTriggered: bool = False
    buyPoint: str = ""
    stop: str = ""
    target: str = ""
    invalidation: str = ""
    invalidationSpec: Dict[str, Any] = Field(default_factory=dict)
    entrySpec: Dict[str, Any] = Field(default_factory=dict)
    statusChanged: bool = False            # 较上一份报告状态是否变化(红绿灯翻转/买点触发翻转/形态标签变化)
    llmJudgment: Optional[WatchlistCheckLLMOut] = None   # 仅 statusChanged∪pinned 才有


class NewsAlertOut(BaseModel):
    """消息面命中告警(v1.3-③-C4)。契约照「v1.3 客户端契约清单」四字段
    `{code, category, summary, source}`;`name` 为额外附加的展示便利字段(超集,
    向后兼容,不破坏契约)。category 枚举码:REDUCTION(减持)/INVESTIGATION(立案)/
    BLOWUP(暴雷)/REGULATORY(监管),客户端展示层换算中文,沿 `boardLabel` 先例。"""
    code: str
    name: str = ""
    category: str
    summary: str
    source: str            # tushare_holdertrade | llm_glm | llm_kimi 等


class NewsAlertScanStatusOut(BaseModel):
    """消息面扫描状态(v1.3-③-C4 新增,2026-07-26 coordinator 拍板正式收进「v1.3
    客户端契约清单」,不再是可选字段)——「没扫到」〔未激活/调用失败〕与「扫了没有」
    〔确认无此类消息〕必须能区分,`newsAlerts` 空数组本身无法表达这个区别,故加本
    字段配合展示。"""
    source: str             # tushare_holdertrade | llm
    scanned: bool
    reason: str = ""
    codesTotal: int = 0
    codesFailed: int = 0
    # v1.3-⑥ 后端补齐:领域层(`report/news_alerts.py::NewsAlertScanStatus.to_public_dict()`)
    # 早已产出 `codesSkipped`(LLM 侧墙钟预算耗尽、根本没发起调用就跳过的标的数,与
    # `codesFailed`「调用了但失败」语义分开,两者都要展示、不能合并成一个数字),但本模型
    # 与 `app.py::_shape_report` 此前均未透出该键——pydantic 默认丢弃未声明字段,契约清单
    # 承诺的字段实际从未抵达客户端,本次补齐(见 app.py 对应改动)。
    codesSkipped: int = 0


class ReportOut(BaseModel):
    tradeDate: str
    generatedAt: str
    strategyVersion: str
    sentiment: Dict[str, Any]                # SentimentDashboard 快照(含 position_quota 三态)
    sectors: List[Dict[str, Any]]            # 强势板块 + 板块年龄
    candidates: List[CandidateOut]
    # v1.1-C.3 自选体检(独立一节,不进候选榜)——旧报告(建这节之前生成的)读回来是
    # 空列表,不是 None(见 `neckline.report.store._parse_watchlist_json` 与
    # `reports.watchlist_json` 列默认值 `'[]'`),客户端前向兼容不必对 null 特判。
    watchlistCheck: List[WatchlistCheckOut] = Field(default_factory=list)
    # v1.1-B.4 漏录兜底:当日买点哨兵触发过但台账无补录时的一句提示(否则空串)。
    # **实时计算**(GET /report 每次读时按当前台账重算,用户补录后自动消失),不落库、不改评分。
    missedEntryHint: str = ""
    # v1.3-③-C1 复盘情报件(涨跌幅榜/涨停梯队/跌停榜/大盘量能/最强题材/题材持续
    # 天数/市值偏好/涨跌停制度偏好)——透传报告落库快照(`report.intel.IntelReport.
    # to_public_dict()`,camelCase 已成形),同 sentiment/sectors 惯例不重抄一份字段定义。
    intel: Dict[str, Any] = Field(default_factory=dict)
    # v1.3-③-C2 板块资金流(拥挤情报,非选股信号)——同样透传
    # `report.sector_moneyflow.SectorMoneyflowReport.to_public_dict()`。**单个对象**
    # (非数组)——携带 available/unavailableReason 等元信息,供"2023-09 前无数据"
    # 这类诚实留空原因展示,不是裸榜单。
    sectorMoneyflow: Dict[str, Any] = Field(default_factory=dict)
    # v1.3-③-C4 消息面(减持/立案/暴雷/监管,持仓+自选票扫描)——命中告警条目(契约
    # 清单字面字段 code/category/summary/source,+ 附加 name)。旧报告(建于本字段前)
    # 读回来是空列表(见 news_alerts_store.load_news_alerts 查无返回 []),同 watchlist
    # 惯例前向兼容不必特判。
    newsAlerts: List[NewsAlertOut] = Field(default_factory=list)
    # 扫描状态(非字面契约清单,本块新增透明度字段,见 NewsAlertScanStatusOut 注释)。
    newsAlertsScan: List[NewsAlertScanStatusOut] = Field(default_factory=list)
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

class K4AdvisoryOut(BaseModel):
    """K4 持仓牌单条命中(plan §五 v1.3-②)。服务端在 16:35 EOD 面板上对持仓票重算 K4
    advisory 命中(读 DB `K4.k4_advisory`,polars 镜像),客户端只展示不重算。
    · level:strong(强警示,置顶醒目)| normal(普通警示,进看板/报告卡)。
    · evidenceStrength:price_volume(价量硬数据,强证据)| constituent(概念板块成分,弱证据,
      标「参考」——题材持续天数依赖 ths_member 快照〔K2 成分洞〕,**不单独触发强警示 APNs**)。
    · 第六类 APNs 派发警报只由「level=strong ∧ evidenceStrength=price_volume」命中触发。"""
    code: str                    # advisory 码,如 A1_turnover_gt_10 / A3_belowyear_limitup
    label: str                   # 人读文案
    level: str                   # strong | normal
    evidence: str                # advisory 证据口径原文(诚实透出研究依据)
    evidenceStrength: str        # price_volume | constituent


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
    stopLine: float              # = buy×(1−stop_pct) 派生(读现役 config,§2.1 单一常量)
    stopOrderChecked: bool = False   # 用户自证「已挂 -5% 条件单」(真对账在 4D 周复盘)
    # —— v1.1-B.1 持仓生命周期派生字段(服务端算好,客户端不重算日历)——————————
    dCount: int = 1              # D 计数(买入日=D1,交易日历口径,单一源 positions.d_count)
    maxHoldDays: int = 5         # 现役 max_hold_days(读 config,不硬编);= 非浮盈时间退出档
    distToStopPct: Optional[float] = None   # (price−stopLine)/price;无实时价 → null
    retraceState: Optional[Dict[str, Any]] = None   # 回落止盈状态{peak,retracePct,triggered};无价/无阈 → null
    todayAction: str = ""        # 今日动作提示(D5离场 / 距止损 / 回落止盈已触发 等)
    # —— v1.3-① 两档时间退出(服务端按 D5 净浮盈判好下发,客户端不重算)——————————————
    maxHoldDaysEffective: int = 5   # 该单有效硬上限:非浮盈=maxHoldDays;浮盈豁免=max_hold_days_profit(如 15)
    timeExitState: str = "holding"  # time_exit_next_day | profit_exempt | hard_cap_exit | holding
    # —— v1.3-① 费用回显(实付,供周复盘对账用真数;NULL=未录)——————————————————
    buyFees: Optional[float] = None
    sellFees: Optional[float] = None
    # —— v1.3-② K4 持仓牌(服务端 16:35 EOD 重算命中;老快照/刚开仓未体检 → 空数组)——————
    k4Advisory: List[K4AdvisoryOut] = Field(default_factory=list)
    # 该持仓是否有关联决策日志(via position_id)含非空情景树待每日对照(②-D 提醒;勾选仍走
    # 既有 POST /decisions/{id}/scenario-outcome,本字段只做「挑出来」,无新写路径)。
    scenarioReviewPending: bool = False


# —— v1.2-A2 熔断纪律状态(§2.1 第 7 条 / plan §五 v1.2-A2)——————————————————————
#
# 诚实边界(§2.1 第 7 条):熔断只能基于**用户已补录进台账**的成交判定——判定所依据
# 的数据与时效随状态一起下发(`basisTradesCount`「基于台账 N 笔已补录成交」+
# `basisWindow` 时窗 + `note`)。锁定态 = 派生(`circuit_breaker.unlocked_at IS NULL`)。

class CircuitEpisodeOut(BaseModel):
    triggerReason: str            # consecutive_stops | daily_loss(客户端展示层换算)
    triggeredAt: str
    triggerRefDate: str           # 'YYYYMMDD' 触发所在交易日
    basisTradesCount: int         # 参与判定的台账已补录成交笔数(诚实边界)
    basisWindow: str              # 判据时窗(展示口径,如 '2026-07-22' 或 '2026-07-20~2026-07-22')
    note: str                     # 诚实边界文案(含「基于台账 N 笔已补录成交」)


class CircuitStateOut(BaseModel):
    locked: bool
    episode: Optional[CircuitEpisodeOut] = None   # 锁定时带当前触发 episode;未锁定 → null


class PositionsOut(BaseModel):
    holdings: List[PositionOut] = Field(default_factory=list)
    # v1.2-A2:今日计划面内嵌熔断状态(处置最相关)。默认未锁定,端点按 `circuit.get_state` 填。
    circuit: CircuitStateOut = Field(default_factory=lambda: CircuitStateOut(locked=False))


class EntrySuggestionOut(BaseModel):
    """一键补录预填**区间**(plan v1.2-E.5 / 契约清单,只读计算,不写台账)。

    v1.1-B.3 原返单个 `qty`(= 按 `single_cap` 取满的手数),v1.2 章程把 `single_cap`
    的语义从「推荐值」改成「**违纪判定上限**」——单笔金额由用户视股价与当时想控的
    仓位当场自定(§2.1 第 3 条)。故这里改返两档区间,**系统不替用户拍板单笔金额**:
    上限档对应违纪线(非推荐值,客户端文案须标注),下限档是保守下沿。
    """
    ok: bool = True
    code: str
    price: float
    qtyLow: int                  # 下限档手数:floor(capFloor/price/100)*100
    qtyHigh: int                 # 上限档手数:floor(capCeil/price/100)*100(= 违纪上限对应手数,非推荐)
    capFloor: float              # 下限档金额 = single_cap × 展示层因子(见 app.py)
    capCeil: float               # 上限档金额 = single_cap(违纪判定上限,读现役 config)
    stopLine: float              # 现价×(1−stop_pct)派生(读现役 config)


class PositionOpenIn(BaseModel):
    code: str
    name: Optional[str] = None
    buy_price: float
    qty: int
    entry_reason: str = ""
    # v1.3-①:补录开仓实付买入费用(客户端契约 camelCase,与既有 snake_case 入参并存,同
    # closeReason 惯例)。契约上客户端补录必填;服务端宽松可选(缺省 NULL → D5 净浮盈估算
    # 走默认佣金率兜底,不崩,见 fees.py)——不硬性拒绝历史/CLI 无费用录入。
    buyFees: Optional[float] = None


class PositionOpenOut(BaseModel):
    ok: bool = True
    position_id: int
    stop_line: float


class PositionCloseIn(BaseModel):
    sell_price: float
    sell_time: Optional[str] = None      # 'YYYYMMDD' 可选,缺省=今日
    # v1.2-A2 离场原因(可选,客户端清仓时选;不传 → NULL,服务端熔断评估走价格兜底判止损)。
    # 服务端码 Literal 白名单(非法码 422);客户端展示层码换算,沿 `boardLabel` 先例。
    # 契约字段名 `closeReason`(v1.2 客户端契约清单)——与本模型既有 snake_case 入参
    # (sell_price/sell_time)并存,同 decisions 入参走 camelCase 的既定不一致(留痕报告)。
    closeReason: Optional[
        Literal["STOP_LOSS", "TAKE_PROFIT", "TIME_EXIT", "INVALIDATION", "MANUAL"]
    ] = None
    # v1.3-①:清仓实付卖出费用真数(可选,成交后回填)——周复盘对账用真数、不用估数。
    sellFees: Optional[float] = None


# —— v1.1-C 自选池(watchlist)————————————————————————————————————————————

class WatchlistItemOut(BaseModel):
    code: str
    name: str
    addedAt: str
    source: str
    note: str = ""
    pinned: bool
    updatedAt: str
    # 最近一份报告的自选体检快照(GET /watchlist「列表 + 各只体检最近快照」,
    # plan C.1);从未跑过报告 / 该票是刚加入还未被下一份报告体检过 → None。
    check: Optional[WatchlistCheckOut] = None


class WatchlistOut(BaseModel):
    items: List[WatchlistItemOut] = Field(default_factory=list)
    maxSize: int = 30


class WatchlistAddIn(BaseModel):
    code: str
    name: Optional[str] = None
    note: Optional[str] = None


class WatchlistAddOut(BaseModel):
    ok: bool = True
    item: WatchlistItemOut


class WatchlistPinIn(BaseModel):
    pinned: bool


class ThsReconcileOut(BaseModel):
    """同花顺自选 txt 对账差异(plan C.4「差异对账端点(两边差集)」)。三个列表均
    为 Neckline `ts_code` 格式(已归一)。"""
    ok: bool = True
    onlyInThs: List[str] = Field(default_factory=list)
    onlyInNeckline: List[str] = Field(default_factory=list)
    both: List[str] = Field(default_factory=list)


class ThsExportOut(BaseModel):
    text: str
    count: int


# —— 4A.5 问询台 + 设置 ————————————————————————————————————————————————

class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class InquiryIn(BaseModel):
    code: str
    messages: List[ChatMessageIn] = Field(default_factory=list)


# —— v1.3.3:二值裁决退役 → **描述性标注**(用户 2026-07-27 拍板「问询台改自由分析师」)——
# 旧的 `VERDICT_REJECT="不符合"` / `VERDICT_PASS="初审通过进海选池"` 已删除:问询台不再
# 做通过/不通过的判决,也不再自动写海选池。下面两个值**不是判决**——它们既不授权也不
# 禁止任何操作,只告诉客户端"这次回答里带没带风险提示",供徽标展示。
VERDICT_ANALYZED = "已分析"
VERDICT_ANALYZED_WARN = "已分析·有风险提示"


class InquiryOut(BaseModel):
    ok: bool = True
    code: str
    reply: str                                   # 自由对话体(§2.7)
    # **契约刻意不破**:字段名/字段集合一个不动,只把类型由
    # `Literal["不符合","初审通过进海选池"]` 放宽成 `str`(v1.3.3)。已装的 macOS 客户端
    # 对未识别取值走 `InquiryVerdict.unknown(raw)`(原样显示 + 中性色调)、且
    # `enablesBuyAction` 恒 false 穷举写死,故不会解码失败、不会误显示成某个已知态。
    verdict: str
    evidence: List[str] = Field(default_factory=list)
    degraded: bool = False                       # LLM 段是否走了降级占位


class PushSettingsOut(BaseModel):
    report: bool
    retreatBrake: bool
    precall: bool      # v1.1-G.1:盘前校准 9:26 汇总推送开关
    d5exit: bool       # v1.1-G.1:D5 时间退出推送开关
    circuit: bool      # v1.2-A2:熔断提醒推送开关(第五类,默认开)
    holdingAlert: bool # v1.3-②:K4 持仓派发警报推送开关(第六类,默认开)


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
    precall: bool      # v1.1-G.1:盘前校准 9:26 汇总推送开关
    d5exit: bool       # v1.1-G.1:D5 时间退出推送开关
    circuit: bool      # v1.2-A2:熔断提醒推送开关(第五类,默认开)
    holdingAlert: bool # v1.3-②:K4 持仓派发警报推送开关(第六类,默认开)——六字段均必填(缺 → 422)


class DeviceRegisterIn(BaseModel):
    token: str
    platform: str = "ios"


class SettingsReviewColMapIn(BaseModel):
    colMap: Dict[str, str] = Field(default_factory=dict)


# —— v1.3-⑥ 后端补齐:五常驻板块可配(`app_settings.intel_watch_boards`,列 + 存取函数
# `settings_store.get_intel_watch_boards`/`set_intel_watch_boards` 早在 v1.3-③-C3 已就绪,
# 本块只补 HTTP 读写端点)——————————————————————————————————————————————————

class IntelWatchBoardsOut(BaseModel):
    boards: List[str] = Field(default_factory=list)   # 板块中文名,按配置顺序(保底认领 load-bearing)


class IntelWatchBoardsIn(BaseModel):
    """PUT 请求体。**禁模糊匹配**——每个名字须能在 `ths_index.name` 精确匹配到,匹配不到
    422(`reason="board_not_found"` + `unresolved` 列出具体哪些名字没匹配到,不含糊拒收)。
    允许空列表(显式清空常驻,`set_intel_watch_boards([])` 语义,与「未配置」回退默认区分)。"""
    boards: List[str] = Field(default_factory=list)


# —— v1.2-B 预注册决策日志(§2.1 第 3 条 / plan §五 v1.2-B)——————————————————
#
# 枚举一律**服务端码 + 客户端展示层换算**(沿 `CandidateOut.board`/`boardLabel`
# 先例)——`thesisTags`/`playbookTag`/情景树 `action` 入参用 `Literal` 白名单
# 校验(非法码 422,FastAPI/pydantic 自动处理,不必手写 if/else);出参 `DecisionOut`
# 对应字段回宽松 `str`(与 `CandidateOut.board: str` 同惯例,不为已落库的历史合法值
# 重新收紧类型)。

class ContingencyScenarioIn(BaseModel):
    """⑦应对方案·情景树单项(预注册内容)。`matched` 默认 False——真正翻转走
    专用端点 `POST /decisions/{id}/scenario-outcome`,创建/修订时传的 `matched`
    只是初始态(通常就是 False,不强制)。"""
    scenario: str
    trigger: str
    action: Literal["BUY", "HOLD", "REDUCE", "ABANDON"]
    matched: bool = False


class ContingencyScenarioOut(BaseModel):
    scenario: str
    trigger: str
    action: str
    matched: bool = False


class DecisionCreateIn(BaseModel):
    code: str
    name: Optional[str] = None
    whyBuy: str
    whyEntryPrice: str
    targetPrice: Optional[float] = None
    exitLow: Optional[float] = None
    exitHigh: Optional[float] = None
    thesisTags: List[Literal["THEME", "SENTIMENT_CYCLE", "CAPITAL_FLOW", "TECH_PATTERN", "NEWS"]] = Field(default_factory=list)
    invalidation: str
    contingencyScenarios: List[ContingencyScenarioIn] = Field(default_factory=list)
    playbookTag: Literal["SWING_CHASE", "BREATHING_TRIAL"]
    plannedPrice: Optional[float] = None
    plannedQty: Optional[int] = None
    # 注意:有意**不含** `createdAt` 字段——服务端生成,客户端任何同名字段值都会被
    # pydantic 直接忽略(`DecisionCreateIn` 无此字段,压根不会解析进请求体)。


class DecisionReviseIn(BaseModel):
    """`POST /decisions/{id}/revise` 请求体(同八项,不含 code/name——修订不能换
    股票,新行的 ts_code/name 继承自被修订的原行,见 `neckline.decision_log.
    revise_decision`)。"""
    whyBuy: str
    whyEntryPrice: str
    targetPrice: Optional[float] = None
    exitLow: Optional[float] = None
    exitHigh: Optional[float] = None
    thesisTags: List[Literal["THEME", "SENTIMENT_CYCLE", "CAPITAL_FLOW", "TECH_PATTERN", "NEWS"]] = Field(default_factory=list)
    invalidation: str
    contingencyScenarios: List[ContingencyScenarioIn] = Field(default_factory=list)
    playbookTag: Literal["SWING_CHASE", "BREATHING_TRIAL"]
    plannedPrice: Optional[float] = None
    plannedQty: Optional[int] = None


class DecisionOut(BaseModel):
    id: int
    code: str
    name: str
    createdAt: str
    whyBuy: str
    whyEntryPrice: str
    targetPrice: Optional[float] = None
    exitLow: Optional[float] = None
    exitHigh: Optional[float] = None
    thesisTags: List[str] = Field(default_factory=list)
    invalidation: str
    contingencyScenarios: List[ContingencyScenarioOut] = Field(default_factory=list)
    playbookTag: str
    plannedPrice: Optional[float] = None
    plannedQty: Optional[int] = None
    status: str                              # pending | filled | cancelled | expired
    positionId: Optional[int] = None
    revisionOf: Optional[int] = None


class DecisionsListOut(BaseModel):
    items: List[DecisionOut] = Field(default_factory=list)


class DecisionLinkIn(BaseModel):
    positionId: int


class ScenarioOutcomeItemIn(BaseModel):
    index: int
    matched: bool


class ScenarioOutcomeIn(BaseModel):
    outcomes: List[ScenarioOutcomeItemIn] = Field(default_factory=list)


# —— v1.2-G 呼吸试验仓台账(§2.1 第 3 条仓位分配 / plan §五 v1.2-G)——————————————
#
# 底仓是普通 `positions` 行(不改其语义);T 仓走独立子表 `breathing_t_trades`(一个
# 底仓 → N 次 T 一对多)。打法标签唯一源 = `decision_log.playbook_tag`(v1.2-B ⑧),
# 本节不复制第二份。`tPnl`/`baseCostAdj`/`edgeToPrice` 均为读时派生,不落库列。

class BreathingTradeIn(BaseModel):
    buyPrice: float
    sellPrice: float
    qty: int
    fees: float                     # 该次 T 的实际费用,由客户端录入,服务端原样落库、不估算
    tDate: Optional[str] = None     # 'YYYYMMDD',缺省 = 今日
    note: Optional[str] = None


class BreathingTradeOut(BaseModel):
    id: int
    positionId: int
    buyPrice: float
    sellPrice: float
    qty: int
    fees: float
    tDate: str
    tPnl: float                     # 派生 = (sellPrice−buyPrice)×qty−fees,不落列
    note: str = ""


class BreathingTradesOut(BaseModel):
    items: List[BreathingTradeOut] = Field(default_factory=list)
    baseCostAdj: Optional[float] = None   # 底仓摊薄成本(派生,§G.3);算不出 → null
    edgeToPrice: Optional[float] = None   # 先手距离(派生,需现价);无实时价 → null


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
    "OkOut", "LLMJudgmentOut", "PermanentBoardStatusOut", "IntelRankOut", "CandidateOut",
    "WatchlistCheckLLMOut", "WatchlistCheckOut", "NewsAlertOut", "NewsAlertScanStatusOut", "ReportOut",
    "RetreatBrakeOut", "BoardEventOut", "BoardOut", "K4AdvisoryOut",
    "PositionOut", "PositionsOut", "PositionOpenIn", "PositionOpenOut", "PositionCloseIn",
    "EntrySuggestionOut", "CircuitEpisodeOut", "CircuitStateOut",
    "WatchlistItemOut", "WatchlistOut", "WatchlistAddIn", "WatchlistAddOut", "WatchlistPinIn",
    "ThsReconcileOut", "ThsExportOut",
    "ChatMessageIn", "InquiryIn", "InquiryOut", "VERDICT_ANALYZED", "VERDICT_ANALYZED_WARN",
    "PushSettingsOut", "SettingsOut", "SettingsLLMIn", "SettingsPushIn", "DeviceRegisterIn",
    "SettingsReviewColMapIn", "IntelWatchBoardsOut", "IntelWatchBoardsIn",
    "WeeklyReviewOut", "ReviewUploadOut", "ReviewGetOut",
    "ContingencyScenarioIn", "ContingencyScenarioOut",
    "DecisionCreateIn", "DecisionReviseIn", "DecisionOut", "DecisionsListOut", "DecisionLinkIn",
    "ScenarioOutcomeItemIn", "ScenarioOutcomeIn",
    "BreathingTradeIn", "BreathingTradeOut", "BreathingTradesOut",
]
