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
    # v1.4-⑩-E:**`null` ≠ 0**。`null` = 行业强度表当日无数据(「没看」);`0` = 评了、
    # 不是强度日(「看了,没有」)。客户端展示「不可用」而非「0 天」。
    industryPersistDays: Optional[int] = None
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


# —— v1.5-①-F 参考件三件套(需求 9,§2.0 第〇原则)——————————————————————————————
# LLM 参考,**不触发任何机器动作**——买入/离场参考区间、明早证伪剧本均须标"参考、
# 非指令"。老四件套(buyPoint/stop/target/invalidation,见 `CandidateOut` 下方)本版
# 保留不动(③ 才做过渡文案替换),本节是**新增**字段,不影响老客户端解码。

class ReferencePlanBuyOut(BaseModel):
    """买入参考区间(①-C 唯一底线:数字必须落在明日涨跌停区间内,出界不显示——
    出现在这里说明已经过夹逼校验)。`stopPrice` 系统算(`close×(1−stop_pct)`),
    不是 LLM 产出,与买入区间同一行展示,§2.1「−5.0 是全系统单一止损常量」。

    `stopPct`(v1.5.1 增量键,两线 review 共同项):产出该参考件时的**现役止损比例**
    (小数,如 0.05),客户端据此**动态生成**「章程 −5%」这句标签,不许硬编数字;
    `null`(老快照/章程未配置)时退化成不带数字的「章程止损」。老客户端忽略本键。"""
    low: float
    high: float
    stopPrice: Optional[float] = None
    stopPct: Optional[float] = None
    why: str = ""


class ReferencePlanExitOut(BaseModel):
    """离场参考区间(本轮上涨压力位,**不受涨跌停夹逼**——压力位可能几天后才到;
    **明示参考、非止盈线**,回落止盈纪律独立生效、不受此区间影响)。

    `takeProfitRetrace`(v1.5.1 增量键,同 `ReferencePlanBuyOut.stopPct` 一对):产出该
    参考件时的**现役回落止盈比例**(小数,如 0.08),客户端据此动态生成「纪律仍以回落
    止盈 8% 兜底」这句旁注;`null` 时退化成不带数字的说法。"""
    low: float
    high: float
    takeProfitRetrace: Optional[float] = None
    why: str = ""


class ReferencePlanOut(BaseModel):
    """参考件三件套(plan §五 v1.5-①-F)。`status` 三态不许合并
    (ok=通过+至少一件有效 | vetoed=否决,三件套全空 | unavailable=LLM未激活/调用
    失败/JSON解析失败,"没看"不是"没有")。`buy`/`exit` 为 `None` 时对应的
    `*UnavailableReason` 必有值(与 `buy`/`exit` 互斥非空);`disclaimer` 原样透传、
    不改写。"""
    status: str                                      # ok | vetoed | unavailable
    buy: Optional[ReferencePlanBuyOut] = None
    buyUnavailableReason: Optional[str] = None
    exit: Optional[ReferencePlanExitOut] = None
    exitUnavailableReason: Optional[str] = None
    script: Optional[str] = None                      # 明早证伪剧本(自由文本,含分支)
    vetoReason: Optional[str] = None
    unavailableReason: Optional[str] = None            # status=unavailable 时的原文
    disclaimer: str = ""
    degraded: bool = False


class CandidateOut(BaseModel):
    rank: int
    code: str
    name: str
    score: float
    board: str
    # 老四件套(K1 时代文案,§2.2/§2.3)**v1.5.0 起已退役**:候选卡输出层改
    # `referencePlan`(见下)。四个键仍在、类型仍是非空 `str`(向后兼容硬约束,
    # 已装 v1.4.1 客户端对这四键硬解码,`decode(String.self,…)`),但值**恒为**
    # `api/app.py::LEGACY_FOURPIECE_NOTICE` 过渡文案,不再是真实买点/止损/目标/
    # 证伪条件——不读落库快照里的 `entry_plan`/`stop_loss`/`target`/
    # `invalidation_text`(那几个字段本身已随候选生成路径退役、恒为默认空串)。
    # 真正删键条件见 PROJECT_PLAN §七 P3-27。
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
    # v1.5-①-F:参考件三件套(需求 9)。`None` = 老报告快照(建于本字段前)或本次生成
    # 整体异常,**不冒充"确认无参考"**——与 `status="unavailable"`(已装配好、只是
    # "没看")刻意区分,客户端按此判断展示哪种缺省文案。
    referencePlan: Optional[ReferencePlanOut] = None
    # v1.5-②-A:20 只全覆盖(旧「仅前10只有」分档退役)——`llmJudgment` 现在 20 只
    # 都可能有,`None` 有两种成因,靠 `judgeSkipped` 分辨是哪种:① 老报告快照
    # (建于本字段前);② 本次生成时 v1.5-②-B 墙钟预算耗尽、这一票根本没发起调用
    # (`judgeSkipped=true`,与「发起了但失败/未激活」的 `llmJudgment.degraded`
    # 语义不同,不许合并成一个"没审",承 `newsAlertsScan.codesSkipped`/
    # `codesFailed` 同一纪律)。
    llmJudgment: Optional[LLMJudgmentOut] = None
    judgeSkipped: bool = False


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
    # v1.4-⑥-B 自选隔日轮扫:`rotationGroup` = 本次扫的是哪一组自选("A"/"B";持仓每日必扫、
    # 不参与轮扫),`codesRotationDeferred` = 本日**轮空**(压根没进本次名单)的自选数。
    # ⚠ **与 codesSkipped(进了名单但预算耗尽没发起)/ codesFailed / codesNoSearch 四者语义
    # 各不相同,客户端不许合并成一个「没扫到」数字**。⚠ 同上两条教训:新增键必须同时补
    # `app.py::_shape_report`,否则 pydantic 丢弃未声明字段,后端算了也到不了客户端。
    # 老报告快照没有这两个键 → 缺省 ""/0,前向兼容不崩。
    rotationGroup: str = ""
    codesRotationDeferred: int = 0


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
    # —— v2.0.0(⑩-A/B,纯展示,新增字段不影响老客户端解码)——————————————————
    # 系统自动关联的结果:来源篮子(当日现役卡里查,查不到 → 以下三项皆 null,如实
    # 标"独立买入",不臆造)。`role` 取 `role_mech` 优先、缺席才退 `role_llm`(同 Tier
    # "机械优先"精神,见 `positions_entry.SourceBasketMember.role`)。
    sourceBasketKey: Optional[str] = None
    sourceBasketName: Optional[str] = None
    tier: Optional[int] = None
    role: Optional[str] = None
    # 计划继承是否有实质内容(`False` = 无来源篮子 或 篮子有但卡未就绪,`position_
    # plans` 仍已落 version=1 空计划行,只是这里没内容可展示)。
    planAvailable: bool = False
    # 「原盈亏结构已变」偏离提示(⑩-B:实际成交价与建仓观察区间明显偏离时的纯展示
    # 提示,不质问不阻断);无从比较(无 entry_zone)→ null,不是"未偏离"。
    planDeviationNotice: Optional[str] = None


# v2.0.0(⑩-A):蓝图 §5.2 六枚卖出快捷标签的服务端码,唯一源在
# `neckline.sentinel.positions.CLOSE_REASON_CODES`(pydantic Literal 需要字面量,
# 不能引用变量,故此处手写同一份字符串——两处必须保持同步,新增码时两边一起改)。
CloseReasonLiteral = Literal[
    "STOP_LOSS", "TAKE_PROFIT", "TIME_EXIT", "INVALIDATION", "MANUAL",
    "SECTOR_WEAKENING", "TARGET_ZONE_REACHED", "ACTIVE_SWITCH", "AD_HOC",
]


class PositionCloseIn(BaseModel):
    sell_price: float
    sell_time: Optional[str] = None      # 'YYYYMMDD' 可选,缺省=今日
    # v1.2-A2 离场原因(可选,客户端清仓时选;不传 → NULL,服务端熔断评估走价格兜底判止损)。
    # 服务端码 Literal 白名单(非法码 422);客户端展示层码换算,沿 `boardLabel` 先例。
    # 契约字段名 `closeReason`(v1.2 客户端契约清单)——与本模型既有 snake_case 入参
    # (sell_price/sell_time)并存,同 decisions 入参走 camelCase 的既定不一致(留痕报告)。
    closeReason: Optional[CloseReasonLiteral] = None
    # v1.3-①:清仓实付卖出费用真数(可选,成交后回填)——周复盘对账用真数、不用估数。
    sellFees: Optional[float] = None


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
    # v1.4-⑦-B(P3-13):问一次落一行(`inquiry_log` 表),历史见
    # `GET /inquiries`/`GET /inquiries/{id}`。**落库是旁路**——失败不影响本次回答,
    # 此时为 `None`(不代表本次问询本身失败;`reply`/`verdict`/`evidence` 仍是有效
    # 结果,`degraded` 字段专指 LLM 段是否降级,与这个字段是两件独立的事)。老客户端
    # (v1.3 及更早)对未声明的多余字段直接忽略,不影响既有解码——契约新增字段,不是破坏。
    inquiryId: Optional[int] = None


# —— v1.4-⑦-B 问询记录档案(plan §五 v1.4-⑦-B / §七 P3-13)——————————————————————
# **与 `inquiry_pool`(已退役历史队列表)是两件事**:本节是问答本身的档案记录,
# 供 `GET /inquiries`(历史列表)/`GET /inquiries/{id}`(详情)使用。

class InquiryLogOut(BaseModel):
    """问询记录档案单条。`materials`/`searchHits` 是落库时的快照(不重算,读回来
    就是当时喂给/搜回来的东西);`evidence`/`answer`/`verdict` 与当时 `InquiryOut`
    返回给用户的内容一致(同一份数据,两处落地)。"""
    id: int
    createdAt: str
    code: str
    name: str = ""
    question: str = ""
    materials: Dict[str, Any] = Field(default_factory=dict)
    answer: str
    evidence: List[str] = Field(default_factory=list)
    searchHits: List[Dict[str, Any]] = Field(default_factory=list)
    verdict: str
    positionId: Optional[int] = None
    decisionId: Optional[int] = None


class InquiryLogsListOut(BaseModel):
    items: List[InquiryLogOut] = Field(default_factory=list)


class PushKindOut(BaseModel):
    """一个通知 kind 的开关行(V2-⑪,plan §五 V2-⑪-B / D5)。`level` 是三级之一
    (`immediate`/`important`/`digest`),客户端据此分组展示;`label` 是服务端给的
    人读名(避免双端各抄一份中文映射,同 `boardLabel` 的反面教训)。"""
    kind: str
    level: str
    label: str
    enabled: bool


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


class SettingsOut(BaseModel):
    """V2-②起:`llmProvider`/`llmKeySet` 两字段由 `providers`/`routes` 取代
    (plan §五 V2-②「契约变更」)。"""
    providers: List[SettingsProviderOut] = Field(default_factory=list)
    routes: Dict[str, str] = Field(default_factory=dict)   # {任务名: provider 名}
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
# **v2.0.0(⑩-C)决策日志强制表单退役**:`decision_log` 表 v2.0.0 起停写留档
# (历史行只读归因,`neckline.decision_log` 不再提供任何写函数,见该模块 docstring)。
# `DecisionOut`/`DecisionsListOut`/`DecisionTrackOut` 三个**出参**形状不变(`GET
# /decisions`/`GET /decisions/{id}/track` 保留为只读归因入口,继续装配历史行);
# 旧的 `DecisionCreateIn`(九项强制表单)/`DecisionReviseIn`/`DecisionLinkIn`/
# `ScenarioOutcomeIn` 连同它们对应的写端点一并退役——`decision_log` 停写之后,
# "创建/修订/关联/翻转情景结果"这些动作物理上无处可落,不是"暂时不做"。
#
# `DecisionCreateIn` **复用同名字保留 `POST /decisions` 路径**,但语义换成蓝图
# §2.2/§5.2「用户可选补充」入口——七枚标签 + 一句可选语音说明,写入 `user_actions`
# (`kind='label'`/`'voice_note'`),不再写 `decision_log`。**全部字段可选**(⑩-C
# 「不传五必填 → 200 而非 400」的落点)。标签码是本次新拟(蓝图给的是中文短语,
# 未给码,如实登记于 PROJECT_PLAN ⑩ 完工记录待用户/⑭ 核对)。

class ContingencyScenarioOut(BaseModel):
    """历史决策日志行的情景树只读展示项(`GET /decisions` 装配用,写入口已退役)。"""
    scenario: str
    trigger: str
    action: str
    matched: bool = False


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


class DecisionCreateIn(BaseModel):
    """`POST /decisions`(v2.0.0 起,⑩-C 退役重定义)。全部字段可选——不再有任何
    "强制表单":`code` 缺省 `None` 也合法(该次提交完全没有可落的内容时,端点直接
    204/200 空提交,不 400)。"""
    code: Optional[str] = None
    positionId: Optional[int] = None
    labels: List[NoteLabelLiteral] = Field(default_factory=list)
    voiceNote: Optional[str] = None


class DecisionNoteOut(BaseModel):
    """`POST /decisions` 响应——如实回显本次记了哪些 `user_actions` kind(`[]` =
    没传任何标签/语音说明,合法的"空提交",不是错误)。"""
    ok: bool = True
    recorded: List[Literal["label", "voice_note"]] = Field(default_factory=list)


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


# —— v1.4-⑦-A 挂单未成交追踪出口(plan §五 v1.4-⑦-A / §七 P3-12)——————————————————
# 领域数据自 v1.3-④ 起已在攒(`report/pending_track.py::track_pending_decisions`),
# 但此前 API 从未暴露任何端点。本节把已有数据接上 `GET /decisions/{id}/track`。

class DecisionTrackRowOut(BaseModel):
    tradeDate: str
    dOffset: int
    close: float
    retFromPlan: Optional[float] = None    # None = 该决策未设 plannedPrice,不臆造


class DecisionTrackOut(BaseModel):
    """`GET /decisions/{id}/track` 响应。`status` = 该决策当前状态(pending/filled/
    cancelled/expired,`decision_log` 同源,供客户端判断"为什么追踪停在这里")。
    `rows` 按 `tradeDate` 升序,**可能为空**——该决策尚未攒到任何追踪快照(刚创建、
    还没到下一交易日)不等于"没有这条决策",这不是 404 情形,见端点 docstring。"""
    status: str
    planPrice: Optional[float] = None
    rows: List[DecisionTrackRowOut] = Field(default_factory=list)


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


# —— V2-⑪-C 自然语言临时提醒(`custom_alerts`)————————————————————————————
#
# 规则本体(`rule`)按 `neckline/custom_alerts.py::normalize_rule` 的形状**透传**
# (同 `WeeklyReviewOut.result` 的透传惯例):它是哨兵的判据源、白名单在领域层已经
# 卡死,在 API 层再镜像一套嵌套模型只会多一处会漂的定义。

class ConfirmationCardOut(BaseModel):
    """⑪-C 的**七项确认卡**。后两项是固定文案、**恒出现**(蓝图 5.6 安全要求),
    客户端不得隐藏 —— 用户是在这张卡上同意「行情有延迟」「只通知不交易」的。"""
    subject: str                    # ① 标的
    condition: str                  # ② 触发条件与方向
    activeWindow: str               # ③ 生效时间
    notifyLimit: str                # ④ 通知次数 / 冷却
    expiry: str                     # ⑤ 到期时间
    quoteDelayDisclosure: str       # ⑥ 行情延迟 / 数据中断披露(必选)
    noAutoTrade: str                # ⑦ 只通知不自动交易
    rule: Dict[str, Any] = Field(default_factory=dict)


class CustomAlertOut(BaseModel):
    id: int
    tsCode: Optional[str] = None            # null = 大盘级
    nlText: str = ""                        # 用户原话(留痕;哨兵不看)
    rule: Dict[str, Any] = Field(default_factory=dict)
    condition: str = ""                     # 由结构化规则生成的人读描述
    activeFrom: Optional[str] = None
    activeTo: Optional[str] = None
    expiresAt: Optional[str] = None
    persist: bool = False
    cooldownSeconds: int = 0
    maxFires: int = 1
    firedCount: int = 0
    status: str = "active"                  # active | expired | cancelled
    # `status` 是**库里那一列**;`expiredNow` 是「按此刻算实际上还生不生效」——
    # 读路径不写库(status 由哨兵那一拍翻),两者可能短暂不一致,分开给、不合并。
    expiredNow: bool = False
    createdAt: str = ""
    updatedAt: str = ""


class AlertsListOut(BaseModel):
    items: List[CustomAlertOut] = Field(default_factory=list)


class AlertConditionIn(BaseModel):
    metric: str
    op: str
    value: float
    ref: Optional[str] = None               # 仅 index_chg_pct 需要
    refBasketId: Optional[int] = None       # 仅 basket_weak_ratio 可选


class AlertCreateIn(BaseModel):
    """建一条提醒(**用户已在确认卡上确认之后**)。手填表单走的也是这个入口 ——
    LLM 解析只是把这些字段先替用户填好,落库路径只有一条。"""
    tsCode: Optional[str] = None
    nlText: str = ""
    conditions: List[AlertConditionIn] = Field(default_factory=list)
    logic: str = "all"
    activeFrom: Optional[str] = None
    activeTo: Optional[str] = None
    expiresAt: Optional[str] = None
    persist: bool = False
    cooldownSeconds: int = 0
    maxFires: int = 1


class AlertUpdateIn(BaseModel):
    """局部更新(未出现的字段不改,pydantic v2 `model_fields_set` 体例)。"""
    conditions: Optional[List[AlertConditionIn]] = None
    logic: Optional[str] = None
    nlText: Optional[str] = None
    activeFrom: Optional[str] = None
    activeTo: Optional[str] = None
    expiresAt: Optional[str] = None
    persist: Optional[bool] = None
    cooldownSeconds: Optional[int] = None
    maxFires: Optional[int] = None
    resetFired: bool = False


class AlertParseIn(BaseModel):
    text: str
    tsCode: Optional[str] = None            # 客户端当前上下文里的标的(可选提示)


class AlertParseOut(BaseModel):
    """NL 解析结果。**永远 200**(交互式接口):失败也要把可读原因和降级表单给出去,
    ⑪-C「LLM 不可用 → 降级为手填结构化表单,**不静默失败**」的契约落点。"""
    ok: bool
    action: str = "create"                  # create | query | cancel | modify
    reason: str = "ok"
    narrative: str = ""                     # 模型那句复述(只展示,不进判据)
    degraded: bool = False                  # True = LLM 不可用,已给手填表单
    manualForm: Optional[Dict[str, Any]] = None
    confirmationCard: Optional[ConfirmationCardOut] = None
    draft: Optional[AlertCreateIn] = None   # 用户点「确认」时原样回传给 POST /alerts
    targetAlertId: Optional[int] = None     # cancel / modify 指认的目标
    matches: List[CustomAlertOut] = Field(default_factory=list)  # action=query 时的命中


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
    "InfoCardSummaryOut", "ExecHintOut", "CandidateOut", "NewsAlertOut", "NewsAlertScanStatusOut", "ReportOut",
    "RetreatBrakeOut", "BoardEventOut", "BoardOut", "K4AdvisoryOut",
    "PositionOut", "PositionsOut", "PositionOpenIn", "PositionOpenOut", "PositionCloseIn",
    "EntrySuggestionOut", "CircuitEpisodeOut", "CircuitStateOut",
    "ChatMessageIn", "InquiryIn", "InquiryOut", "VERDICT_ANALYZED", "VERDICT_ANALYZED_WARN",
    "PushKindOut", "PushSettingsOut", "SettingsOut", "SettingsProviderOut", "SettingsPushIn", "DeviceRegisterIn",
    "ConfirmationCardOut", "CustomAlertOut", "AlertsListOut", "AlertConditionIn",
    "AlertCreateIn", "AlertUpdateIn", "AlertParseIn", "AlertParseOut",
    "ProviderOut", "ProvidersListOut", "ProviderCreateIn", "ProviderUpdateIn",
    "LLMRoutesOut", "LLMRoutesIn",
    "SettingsReviewColMapIn", "IntelWatchBoardsOut", "IntelWatchBoardsIn",
    "WeeklyReviewOut", "ReviewUploadOut", "ReviewGetOut",
    "ContingencyScenarioOut", "NoteLabelLiteral",
    "DecisionCreateIn", "DecisionNoteOut", "DecisionOut", "DecisionsListOut",
    "DecisionTrackOut", "DecisionTrackRowOut",
    "InfoCardKlineBarOut", "InfoCardIndexPointOut", "InfoCardK4FlagOut", "InfoCardMarketOut", "InfoCardOut",
]
