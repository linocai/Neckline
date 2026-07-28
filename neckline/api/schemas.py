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
    """候选情报排序理由(v1.3-③-C3,§2.3 语义变更;v1.4-③ 起补三级排序键,需求 8)。
    候选=「过完安检、值得关注的票」非「会涨的票」——客户端据此写对文案(不标「推荐买点」),
    展示情报维度。**排序 = 注意力优先级,不是收益预测;排第一 ≠ 最会涨,终选权在用户。**"""
    # sectorFlow:所属常驻/暴起板块最大净流入(万元,C2;无数据=None)。⚠ **v1.4-③ 起
    # 退为并列展示,不参与排序**(需求 8:排序键只用审计过方向的量,资金流未经方向审计;
    # 见下方 industryRank/industryPersistDays/yellowCardCount 三个字段才是实际排序依据)。
    sectorFlow: Optional[float] = None
    themePersistDays: int = 0               # 题材持续天数(反用:1天新鲜>2-3警惕;≥4已在③剔)。
                                             # 与下方 industryPersistDays 同源同值,旧字段名保留
                                             # (老客户端兼容,§3.8「新增字段可选,既有字段语义不变」)。
    highElasticity: bool = False            # 高弹板块(GEM/STAR;生成域刻意含高弹,标注给人判)
    # —— v1.3-⑥ 后端补齐(数据已在 v1.3-③-C3 落 `intel_rank` 字典/报告快照里就绪,
    # 此前 pydantic 未声明这三键 → 默认丢弃,本次补字段透出;逻辑零改动)—————————————
    source: str = ""              # quota(常驻保底)| competition(情报竞争)| forced(问询强制)。
                                   # 旧报告(建于本字段前)读回空串——客户端未识别值原样透传不崩。
    industry: str = ""            # 该票行业(stock_basic.industry,过行业闸后的代表行业),
                                   # 让用户看清「凭什么在这个板块栏」;查不到/旧报告 → 空串。
    permanentBoardStatus: List[PermanentBoardStatusOut] = Field(default_factory=list)
    # —— v1.4-③ 新增(需求 8):排序键三级原样透出(`intel_candidates._sort_key`)————————
    industryRank: Optional[int] = None      # 排序键①:行业强度当日排名(1=最强)。**None=未
                                             # 参与排名(无 industry/成员<5),客户端展示时不得
                                             # 当 0**(0 会误读成"最强";旧报告读回同样是 None,
                                             # 与"确实未参与排名"语义上不作区分,均如实缺省)。
    industryPersistDays: int = 0            # 排序键②:行业强度持续天数(升序,第1天最新鲜;
                                             # 与 themePersistDays 同值同源,新字段名对齐排序键
                                             # 命名——两个字段并存是刻意的向后兼容,不是笔误)。
    yellowCardCount: int = 0                # 排序键③:K4 avoid_flag 命中数(升序,无牌靠前;
                                             # 不数 hard_cut、不数不在 DB 的合成码,如
                                             # A3b_belowyear_bigvol)。旧报告读回默认 0。


# —— v1.4-④ 信息卡(plan §五 v1.4-④,需求 8 第 3 点)——————————————————————————
# 摘要位(挂 `CandidateOut.infoCard`,不含 60 日序列)先声明在这里(`CandidateOut`
# 引用它);完整信息卡(`GET /report/{date}/info-card/{code}` 专用,含 60 日序列)
# 的其余模型见文件末「v1.4-④ 信息卡(完整)」节,复用本节已声明的
# `InfoCardSnapshotOut`/`InfoCardNewsOut`/`InfoCardTopListOut`,不重复定义。

class InfoCardSnapshotOut(BaseModel):
    """信息卡快照数值(plan §五 v1.4-④-A-4)。任一路缺数据 → 该字段 `null`,**不得**
    用 `0` 冒充"有数据但为零"(§3.8)。"""
    volRatio5: Optional[float] = None
    turnoverRate: Optional[float] = None
    industryRank: Optional[int] = None       # ② 行业强度当日排名(1=最强);None=未参与排名
    industryPersistDays: int = 0
    aboveMa250: Optional[bool] = None        # ma250 未就绪(<250交易日历史)→ null,不当"年线下"
    distFromMa250Pct: Optional[float] = None  # 小数(非百分数),如 0.05 = 高于年线5%
    distFromHigh20dPct: Optional[float] = None
    consecLimitUpDays: int = 0


class InfoCardNewsItemOut(BaseModel):
    category: str    # REDUCTION | INVESTIGATION | BLOWUP | REGULATORY(同 NewsAlertOut.category 枚举)
    summary: str
    source: str


class InfoCardNewsOut(BaseModel):
    """消息面摘要(plan §五 v1.4-④-A-7)。**"没扫到"(不在扫描域)与"扫了没有"必须
    能区分**(同 `NewsAlertScanStatusOut` 一贯原则)——`scanned=False` 时
    `unavailableReason` 必有值,`items` 恒空数组(不代表"确认无消息")。"""
    scanned: bool
    items: List[InfoCardNewsItemOut] = Field(default_factory=list)
    unavailableReason: Optional[str] = None


class InfoCardTopListOut(BaseModel):
    """龙虎榜摘要(plan §五 v1.4-④-A-8)。`lookbackDaysCovered`(近 5 个交易日里本地
    已落盘、真能判定的天数,≤5)诚实反映"查了几天",**不为凑齐而回补历史**——
    `lookbackDaysCovered<5` 不代表"其余天数确认未上榜",只代表"没查到那几天"。"""
    onListToday: bool = False
    reason: Optional[str] = None
    netAmount: Optional[float] = None
    netRate: Optional[float] = None
    lookbackDaysCovered: int = 0
    lookbackHitDays: int = 0


class InfoCardSummaryOut(BaseModel):
    """信息卡摘要(不含 60 日序列 / 红黄牌明细,挂 `CandidateOut.infoCard`,plan §五
    v1.4-④-B)。红黄牌明细见完整信息卡端点——`CandidateOut` 顶层已有 `k4Flags`
    (码列表),摘要位不重复。"""
    snapshot: InfoCardSnapshotOut = Field(default_factory=InfoCardSnapshotOut)
    mildBand: bool = False
    news: InfoCardNewsOut = Field(default_factory=lambda: InfoCardNewsOut(scanned=False))
    topList: InfoCardTopListOut = Field(default_factory=InfoCardTopListOut)


class ExecHintOut(BaseModel):
    """执行提示单条(plan §五 v1.4-⑤-A,需求 8 末段)。**语义红线**:回答"如果你决定
    动手,怎么执行更不吃亏",不是"该不该买"——`text` 原样透传 DB `k4_advisory.exec_hint`
    文字(或缺读时的模块兜底),客户端不改写、不加"建议"字样。"""
    code: str              # advisory 码(C1_strong_market_order 等四选一)
    text: str              # 展示文字(DB 原文 或 缺读兜底)
    source: str            # db | fallback ——文字来源,供诚实展示


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
    # v1.4-④-B:信息卡摘要(不含 60 日序列,供列表页直接展示,§二.四快照数值/温和带/
    # 消息面/龙虎榜)。`None` = 老报告(建于本字段前)或该次生成异常降级,**不冒充
    # "确认无内容"**——客户端按"该信息暂不可用"处理,不是"已查证为空"。完整信息卡
    # (60日K线/RS线/行业分歧线)另走 `GET /report/{date}/info-card/{code}`。
    infoCard: Optional[InfoCardSummaryOut] = None
    # v1.4-⑤-A:执行提示(读 DB `k4_advisory.exec_hint`,展示标题统一「执行提示」,不叫
    # 「买入建议」)。0~4 条,老报告快照(建于本字段前)读回默认空列表(前向兼容)。
    execHints: List[ExecHintOut] = Field(default_factory=list)
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
    # v1.3.4:调用成功但联网搜索命中 0 条的标的数——它的「未发现三类消息」是模型凭
    # 训练数据说的、非搜索证实。与 codesFailed/codesSkipped 同属「扫了 vs 没扫」的
    # 分辨维度,三者不可合并。⚠ 同上条教训:新增键必须同时补 `app.py::_shape_report`,
    # 否则 pydantic 丢弃未声明字段,后端算了也到不了客户端。
    codesNoSearch: int = 0


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
    # v1.4-①-C 板块数据新鲜度(§七 P0-3):`{sectorDataDate:'YYYYMMDD', sectorLagDays:int,
    # stale:bool}`,透传落库快照(**随报告冻住**——读三天前的报告该看到当时的新鲜度)。
    # `sectorLagDays=-1` = 板块数据完全缺失(哨兵值,见 `report/sectors.py::
    # SECTOR_LAG_UNKNOWN`;刻意不用 0,0 是「新鲜」)。**空 dict = 老报告**(建于本字段
    # 之前),客户端按「该版本还没有新鲜度概念」处理,不得当成「新鲜」。
    # ⚠ `stale=True` 时「当日暴起板块」与「题材持续天数」**本日不可信**,客户端须显式
    # 标注,不静默把它们当正常结果展示。
    dataFreshness: Dict[str, Any] = Field(default_factory=dict)
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
    # v1.4-①-B 起多一个第五态 `suspended_hold`(当日无 EOD 行 且 尚未定格 → 判向挂起,
    # 不推 D5 / 不推硬上限;`dCount` 照常累计展示)。客户端展示层须为它加一档文案。
    timeExitState: str = "holding"  # time_exit_next_day | profit_exempt | hard_cap_exit | holding | suspended_hold
    # —— v1.4-⑥-C 定格日 ≠ D5 显式标注(§七 P1-6)——————————————————————————————————
    # `timeExitLockedDay`:**定格发生当时的 `dCount`**(= 从 buy_date 到 `time_exit_locked_date`
    # 的交易日数)。EOD 管线连续断跑 / ①-B 停牌票复牌后定格时,它可能是 D7/D8 而不是 D5 ——
    # 系统一直如实落库,但界面此前不提示。**null = 尚未定格**(或老快照缺 `locked_date`)。
    # `timeExitLockedLateDays`:= `timeExitLockedDay − maxHoldDays`,**下限 0**(不晚就是 0);
    # 客户端 **>0 才展示**「定格于 D{n},晚于 D{maxHoldDays} {k} 天」。
    # ⛔ **只提示,不改判定逻辑**:定格语义是审计 🔴-1 的结论(D5 判一次定格、消费点只读定格
    # 值),这两个字段是**纯派生展示位**,不参与 `timeExitState` / `maxHoldDaysEffective` 的
    # 任何计算(有单测锁死「加了标注后判向输出逐位不变」)。
    timeExitLockedDay: Optional[int] = None
    timeExitLockedLateDays: int = 0
    # —— v1.3-① 费用回显(实付,供周复盘对账用真数;NULL=未录)——————————————————
    buyFees: Optional[float] = None
    sellFees: Optional[float] = None
    # —— v1.4-①-B 停牌 / 无行情持仓票的显式标注(§七 P0-2)————————————————————————
    # `priceStale`:当日**无 EOD 行**时给出「陈旧几个交易日 / 最后成交日 / 为什么」三件
    # (`{staleDays:int, lastCloseDate:'YYYYMMDD', reason:'suspended'|'data_gap'|'unknown'}`,
    # 领域源 `data/price_stale.py`)。**当日有行 → null**(正常票不背这个字段的负担)。
    # 客户端持仓卡文案:「停牌/无数据 {staleDays} 个交易日,价格为 {lastCloseDate} 最后成交价」。
    # ⚠ **绝不静默把老价当今日价** —— 这个字段就是那句「静默」的解药。
    priceStale: Optional[Dict[str, Any]] = None
    # `k4DataUnavailable`:当日 K4 体检是否因无 EOD 行被**整份跳过**。**三值**:
    # true=没体检 / false=体检过了(空 `k4Advisory` 才等于「体检过没问题」)/ **null=老快照
    # 未记录**(建于本字段之前的行,如实说不知道,不冒充 false)。
    k4DataUnavailable: Optional[bool] = None
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
    # v1.4-①-A(§七 P0-1):真实买入日 'YYYYMMDD'。**缺省 = 今天**,与 v1.4 之前
    # `buy_date=date.today()` 写死的行为**逐位一致**——老客户端不传时行为不变。
    # 校验两条(违反 → 400 + reason,见 `app.py::open_position`):
    #   · 不是 `trade_cal` 里的交易日 → `not_trading_day`
    #   · 晚于今天(未来日)→ `future_buy_date`
    # ⚠ 为什么必须能指定:D 计数 / 时间退出 D5-D15 判向 / 回落止盈峰值追踪起点 /
    # 周复盘持有天数 / 按打法归因的持有周期,**全部以买入日为起点**——补录历史成交
    # 时若被盖成补录当天,以上全错(2026-07-27 真踩,3 笔历史持仓被盖成当天)。
    # 领域层 `sentinel/positions.py::open_position` 与 CLI `scripts/positions.py add`
    # 本来就收 `buy_date`,缺口只在本 HTTP 契约 + 客户端。
    buyDate: Optional[str] = None


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
    # v1.4-⑤-B(需求 2 补充,决策日志第⑨项「最高追价上限」):相对昨收百分比,如
    # `3.0` = 昨收+3%(**不是小数 0.03**,与 `dist_from_ma250_pct` 等字段的小数惯例不同,
    # 照 plan 原文口径)。允许负值 = 只在低开时买;`null` = 显式选择"不设上限"。
    # **⚠ 必须显式传(即便是 null)**——省略该 JSON 键 → `app.py::create_decision` 用
    # `model_fields_set` 探测键是否存在,缺失时 400 `reason="max_chase_required"`;
    # 显式 `null` 合法(pydantic 默认值与"未传"在 `model_fields_set` 层面可区分)。
    # **⚠ 与 `plannedPrice` 不是一回事,不许合并**:`plannedPrice` 是"我打算挂多少价",
    # `maxChasePct` 是"开盘冲多高我就放弃、盘中不追补"——两者并存,各自独立取值,见
    # `neckline.decision_log` 模块 docstring「与 planned_price 语义分离」。
    maxChasePct: Optional[float] = None
    # 注意:有意**不含** `createdAt` 字段——服务端生成,客户端任何同名字段值都会被
    # pydantic 直接忽略(`DecisionCreateIn` 无此字段,压根不会解析进请求体)。


class DecisionReviseIn(BaseModel):
    """`POST /decisions/{id}/revise` 请求体(同九项,不含 code/name——修订不能换
    股票,新行的 ts_code/name 继承自被修订的原行,见 `neckline.decision_log.
    revise_decision`)。`maxChasePct` 必填语义(显式传/可 null,缺键 400)与
    `DecisionCreateIn` 相同——修订等于重新预注册一整套九项内容,同一份纪律。"""
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
    maxChasePct: Optional[float] = None


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
    # v1.4-⑤-B:最高追价上限(相对昨收百分比,如 3.0=+3%)。老行(建于本字段前)读回
    # `None`——与"用户显式选择不设上限"在存储层无法区分(两者都是 SQL NULL),这是
    # 迁移引入新必填字段时不可避免的历史模糊,不影响新行起的强制语义。
    maxChasePct: Optional[float] = None
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


# —— v1.4-④ 信息卡(完整,`GET /report/{date}/info-card/{code}` 专用)——————————————
# 摘要位共用的 `InfoCardSnapshotOut`/`InfoCardNewsOut`/`InfoCardTopListOut` 声明在
# `CandidateOut` 之前(见该处注释),这里只补 60 日序列 + 红黄牌明细专属的模型。

class InfoCardKlineBarOut(BaseModel):
    """一根 K 线(前复权,plan §五 v1.4-④-A-1)。`ma20`/`ma250` 早期行(历史不足窗口)
    → `null`,不是"均线为0"。"""
    tradeDate: str
    open: float
    high: float
    low: float
    close: float
    vol: float
    ma20: Optional[float] = None
    ma250: Optional[float] = None


class InfoCardIndexPointOut(BaseModel):
    """RS 线 / 行业分歧线 / 大盘指数化线共用的一个点(起点归一 100)。"""
    tradeDate: str
    value: float


class InfoCardK4FlagOut(BaseModel):
    """红黄牌明细(plan §五 v1.4-④-A-5,"复用③已算好的 k4_flags,不重算")。
    `section`:hard_cut(红牌)| avoid_flag(黄牌)——客户端展示层换算,同 `board`/
    `NewsCategory` 惯例,服务端不存中文。"""
    code: str
    label: str
    level: str               # strong | normal
    section: str
    evidenceStrength: str    # price_volume | constituent
    evidence: str


class InfoCardMarketOut(BaseModel):
    """市场语境(报告级构件,plan §五 v1.4-④-A-9)。"""
    indexCode: str = "000001.SH"
    indexLine: List[InfoCardIndexPointOut] = Field(default_factory=list)
    limitUpCount: int = 0
    limitDownCount: int = 0
    aboveMa20: Optional[bool] = None


class InfoCardOut(BaseModel):
    """完整信息卡(考卷同构九件套,plan §五 v1.4-④)。每一路数据源独立
    `*Available`/`*UnavailableReason`——**数据不可得如实缺省,禁止硬凑**是本端点
    的第〇原则,任何一路缺失都不得连带其余各路"看起来也不可用"。"""
    code: str
    name: str
    tradeDate: str
    klineAvailable: bool
    kline: List[InfoCardKlineBarOut] = Field(default_factory=list)
    klineUnavailableReason: Optional[str] = None
    rsAvailable: bool = False
    rsLine: List[InfoCardIndexPointOut] = Field(default_factory=list)
    rsBenchmark: str = "000001.SH"
    rsUnavailableReason: Optional[str] = None
    industryDivergenceAvailable: bool = False
    industryDivergenceLine: List[InfoCardIndexPointOut] = Field(default_factory=list)
    industry: str = ""
    industryDivergenceNote: str = "行业线=行业成员中位数合成,非申万官方指数"
    industryDivergenceUnavailableReason: Optional[str] = None
    snapshot: InfoCardSnapshotOut = Field(default_factory=InfoCardSnapshotOut)
    k4Flags: List[InfoCardK4FlagOut] = Field(default_factory=list)
    mildBand: bool = False
    news: InfoCardNewsOut = Field(default_factory=lambda: InfoCardNewsOut(scanned=False))
    topList: InfoCardTopListOut = Field(default_factory=InfoCardTopListOut)
    market: InfoCardMarketOut = Field(default_factory=InfoCardMarketOut)


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
    "OkOut", "LLMJudgmentOut", "PermanentBoardStatusOut", "IntelRankOut",
    "InfoCardSnapshotOut", "InfoCardNewsItemOut", "InfoCardNewsOut", "InfoCardTopListOut",
    "InfoCardSummaryOut", "ExecHintOut", "CandidateOut",
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
    "InfoCardKlineBarOut", "InfoCardIndexPointOut", "InfoCardK4FlagOut", "InfoCardMarketOut", "InfoCardOut",
]
