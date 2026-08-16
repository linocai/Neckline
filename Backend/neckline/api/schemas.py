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
#
# ⚠ **V2-⑭-B 契约总装:候选榜整族 DTO 已退役** —— `CandidateOut` / `IntelRankOut` /
# `LLMJudgmentOut` / `InfoCardSummaryOut` 四个,连同 `ReportOut.candidates` 键一起删。
# 实现层早在 ⑬-1/2/3/4/6 就删干净了,新报告的 `candidates_json` 恒 `[]` —— 契约面还留着
# 等于**它还在承诺这件事**(同下方 `PermanentBoardStatusOut` 的处置逻辑,一模一样的病)。
# 取而代之的是本节的**篮子族** DTO(`BasketDailyOut` / `BasketOut` / `BasketCardOut` …)。
# **删键安全性**:D2 = A 路已拍板(新机挂 `nk` 新子域、老 App 打老机、两者不交叉),契约
# 一次性换血、不留过渡键;况且 ⑬-6 已删掉 `Candidate` 的四个 `try c.decode` **必需**键,
# 老客户端本就解不出 `candidates` 数组,整键删掉反而比留半截干净。
# ⚠ 「先客户端可选解码、下版服务端才删键」这条两步淘汰纪律**本身仍然有效**(CLAUDE.md
# 铁律,V2 之后照守),V2 只是靠换机窗口结构性地绕开了它,不是废除它。
#
# ⚠ **`PermanentBoardStatusOut` 已随 V2-⑬-1 退役**(契约线审计 🟡 Y4,2026-08-03 补删):
# 五常驻板块保底(`QUOTA_PER_PERMANENT_BOARD` / `_permanent_board_status`)住在已删除的
# `report/intel_candidates.py` 里,⑬-1 把实现层删干净了,**契约层这个 DTO 与客户端那张
# 卡片整套还留着** —— 新报告 `candidates` 恒空,那张卡因此会**稳定**显示「暂无候选可显示
# 常驻板块状态(今晚 16:35 报告后可见)」:一句永远兑现不了的承诺,另一分支还指向已删的
# `/settings/intel-boards`。守门只断言了 settings_store 符号与端点,罩不到 DTO 与客户端。
# ⛔ 别因为「留着不占地方」就把僵尸 DTO 留在契约里:契约面留着 = 它还在承诺这件事。
# 删除安全性:客户端 `IntelRank.permanentBoardStatus` 是 `decodeIfPresent ?? []`(非
# `try c.decode`),服务端停发不会让老 App 解不出报告 —— 这是 CLAUDE.md「删键前先查客户端
# 是不是硬解码」那条的一次实查结论,不是想当然。


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


# —— V2-⑭-B 篮子族契约(⑤⑥⑦⑧⑨ 的产出在 API 面的形状)————————————————————
#
# **两类 DTO,判断规则写在这里,新增字段前先分清是哪一类**(CLAUDE.md「落库快照按
# 是否随每次响应重新拼装分两类」):
#
#   A. **每次响应重新拼装**(`BasketOut` / `TierOut` / `BasketVerificationOut`):
#      服务端用 pydantic 默认值重构,新字段旧数据也会补全 → 客户端可以用合成
#      `Codable` + `Optional`/默认值兜底。
#   B. **写入当时冻住的历史快照**(`BasketCardOut` ← `basket_cards.card_json`;
#      `BasketReviewOut.mech` ← `basket_review_daily.mech_json`):服务端升级**永远
#      不会**给老快照补新键 → 客户端**必须**手写 `init(from:)` 全字段 `decodeIfPresent`
#      兜底。⛔ 用合成 `Codable` 的后果:装了新 App 的用户翻几周前的老卡 → 整张卡解不出。
#
# snake→camel 的**唯一转换点**是 `report/basket_daily.py::card_to_public_dict`
# (报告快照与 `GET /baskets/{id}/card` 两条路共用),API 层不再各写一份。


class BasketMemberOut(BaseModel):
    """篮子卡上的一名成员(**B 类:冻结快照**)。全字段可选 —— 老卡缺键是常态。

    `roleLlm` / `roleMech` 是**两说并存**的对拍结果:`roleConflict=true` 时客户端
    **必须两个都显示**,⛔ 不许挑一个当"正确答案"(⑦ 的对拍分歧展示纪律)。
    `entryZone` / `maxChase` / `exitReference` 三项都是**参考件**,各自带
    `*Clamp` + `*UnavailableReason` —— 夹逼闸拒收时值是 `null` 且原因非空,
    ⛔ 客户端不许把 `null` 显示成 0 或空白了事。
    **`exitReference` 不是止盈线**(§2.8-C 语义红线),文案里不许这么写。"""

    tsCode: str = ""
    name: str = ""
    roleLlm: Optional[str] = None
    roleMech: Optional[str] = None
    roleConflict: bool = False
    reason: str = ""
    isPrimary: bool = False
    industry: Optional[str] = None
    industryLift: Optional[float] = None
    liftReason: Optional[str] = None
    primaryReason: Optional[str] = None
    # 🔴 **2026-08-12 用户裁定 ⑤**:主归属的**确认状态**与「归属待确认」原因码。
    # `confirmed` = 策略结论 · `pending_confirmation` = **技术兜底的产物,⛔ 不是策略结论**。
    # ⚠ **`None` / 空串 = 这张卡没记**(老 `basket_card_v4` 及更早)——
    # ⛔ 客户端不许当成 `confirmed`,那是把「没记」讲成「策略确认过了」。
    primaryStatus: Optional[str] = None
    primaryPendingReason: Optional[str] = None
    rsRank: Optional[int] = None
    k4Tag: Optional[str] = None
    # —— V2.2-③-C 位置关(**2026-08-09 用户裁定 #11**:位置关由机械关改判为证据关,
    # 判定交 LLM、只降级不除名)————————————————————————————————————————————
    # `positionVerdict` ∈ `ok`(位置合适)/ `weak`(勉强、降一档)/ `unfit`(不合适,
    # 该篮退出正式候选但**仍在报告 ③b 列名**)。`positionReason` = 模型那句人话
    # (或「本次没给判定,保守按 weak」的兜底原因码)。`positionMetrics` = **当次喂给
    # 模型的那份落地起跳读数原样**(信息卡展开用;缺项就是真的没取到,⛔ 客户端不许
    # 把缺项显示成 0)。
    # ⚠ **零删键纪律(〇b-3)在这里不适用,因为没有键被删**:上一版曾计划发的
    # `landingState`(四态枚举)**从未上产**(V2.2 批 2 没部署,生产客户端从未见过
    # 这个键),故直接换成本组三键,⛔ 不走「先改客户端 → 下一版服务端才删」两步。
    # 后人别据此以为可以随手换键 —— 换得成的前提是**那个键一天都没上过产**。
    positionVerdict: Optional[str] = None
    positionReason: Optional[str] = None
    positionMetrics: Optional[Dict[str, Any]] = None
    # —— V2.2-③-C2 核心关(**2026-08-09 用户裁定 #12**:核心关也退出机械闸,机械侧
    # 只出行业域读数、判定交 LLM,只降级不除名)——————————————————————————————
    # `coreVerdict` ∈ `ok`(是那一群的龙头)/ `weak`(勉强、降一档)/ `unfit`(不是
    # 核心地位,该篮退出正式候选但**仍在报告 ③b 列名**)。`coreMetrics` = **当次喂给
    # 模型的那份行业域读数原样**,里面**一定带分母**(`industryMemberCount` 等价的
    # snake 键 `industry_member_count`)—— 没有分母,「第 3 名」是 3/8 还是 3/80 读不
    # 出来。⚠ 读数键是**自由结构原样透传**(同 `positionMetrics`),⛔ 客户端不许把
    # 缺项显示成 0:缺项就是真的没取到。
    # ⚠ 与位置三键一样:上一版的 `leader_rs_rank ≤ 3` 从未以任何键发到过客户端,
    # 这里是**纯新增、零删键**。
    coreVerdict: Optional[str] = None
    coreReason: Optional[str] = None
    coreMetrics: Optional[Dict[str, Any]] = None
    # 机械面板原样透传(⑦ `MemberMech.to_dict()`;自由结构,在 API 层再镜像一套嵌套
    # 模型只会多一处会漂的定义 —— 同 `WeeklyReviewOut.result` 的既定透传惯例)。
    mech: Dict[str, Any] = Field(default_factory=dict)
    entryZone: Optional[Dict[str, Any]] = None
    entryZoneClamp: str = ""
    entryZoneUnavailableReason: Optional[str] = None
    maxChase: Optional[float] = None
    maxChaseClamp: str = ""
    maxChaseUnavailableReason: Optional[str] = None
    exitReference: Optional[Dict[str, Any]] = None
    exitReferenceClamp: str = ""
    exitReferenceUnavailableReason: Optional[str] = None
    # ⑦-K7 标注件(**四不硬约束**:不进排序 / 不进哨兵 / 不改去留 / 不加分)。
    # 形状与 `InfoCardMemberTagOut` 同源(`selection/member_tags.py` 唯一实现),
    # 交叉断言锁死「同一票同一天两处标签集合逐位相同」。
    tags: List[Dict[str, Any]] = Field(default_factory=list)
    # 判不了的标注码 —— 与「判过没命中」是两回事,⛔ 不许合并成"没有标注"。
    tagsAbsent: List[str] = Field(default_factory=list)
    tagAbsences: List[Dict[str, str]] = Field(default_factory=list)


class BasketCardOut(BaseModel):
    """一张 D0 冻结的篮子卡(**B 类:冻结快照**,蓝图 4.6 十一项)。

    `specVersion` 随形状变化而 bump;`fingerprint` 带口径指纹(章程 / 包 / 引擎 /
    验证条件集四个版本号)—— ⑨ 的按包归因靠它分层,⛔ 别当成装饰字段丢掉。
    `narrative` 是 LLM 叙述,**原文整段呈现**、不得拆解塞回枚举卡片(§2.7);
    `degraded=true` = 人话半份缺席、结构化半份照出(不是"这张卡不可信")。
    `disclaimer` 是固定文案单一源,**客户端原样透传不改写**。"""

    specVersion: Optional[str] = None
    version: Optional[int] = None
    basketKey: str = ""
    tradeDate: str = ""
    nextTradeDate: Optional[str] = None
    name: str = ""
    driver: str = ""
    driverKind: str = ""
    # V2.2-③-E(裁定 #9 单篮子单引擎):篮子级引擎归属,成员继承 —— 新增可选键,
    # 老卡(v2 及以前)缺键 = 「当时没有引擎归属概念」,⛔ 不是 "engine 为空"。
    engineCode: Optional[str] = None
    engineVersion: Optional[str] = None
    skeletonVersion: Optional[str] = None
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    # ⑤ 两段式流水的单侧故障披露:ok | search_unavailable | partial。
    # ⛔ 不是 `ok` 时客户端必须显式标注"取证不完整",不许静默当完整证据展示。
    evidenceStatus: str = ""
    whyNow: str = ""
    members: List[BasketMemberOut] = Field(default_factory=list)
    roleConflicts: List[str] = Field(default_factory=list)
    tier: Optional[int] = None
    rankInTier: Optional[int] = None
    rankMech: Optional[int] = None
    mechScore: Optional[float] = None
    # 五维分项 + 权重。**键是维度名**(与现役包的权重键逐字对应),故原样透传、
    # ⛔ 不做 camel 化 —— 那会把语义标识符改名。
    tierBreakdown: Dict[str, Any] = Field(default_factory=dict)
    tierReason: Optional[str] = None
    tierNote: Optional[str] = None
    # V2.3.3-①(K8.md §十 第 8 项):卡 #6 由三剧本换成「预期上涨路径」**一段话**。
    # 🔴 `scripts` / `scriptsUnavailableReason` 两键**直接停发**(客户端本就是
    # `decodeIfPresent`,停发安全);老 v3 卡里那两键仍在 `card_json` 里,只是不进契约。
    upsidePath: Optional[str] = None
    upsidePathUnavailableReason: Optional[str] = None
    # 喂 ⑧ 哨兵的结构化 spec(机器半份),同样原样透传。
    verificationSpec: Dict[str, Any] = Field(default_factory=dict)
    verificationText: Optional[str] = None
    invalidationSpec: Dict[str, Any] = Field(default_factory=dict)
    invalidationText: Optional[str] = None
    risks: List[str] = Field(default_factory=list)
    disclaimer: str = ""
    fingerprint: Dict[str, Any] = Field(default_factory=dict)
    disciplineLabels: List[str] = Field(default_factory=list)
    narrative: str = ""
    llmStage: str = ""
    # V2.4.2:新增可选来源；历史冻结卡缺键 = 当时未记录，不作猜测。
    generationSource: Optional[str] = None
    degraded: bool = False
    notes: List[str] = Field(default_factory=list)


class ScoreContribOut(BaseModel):
    """百分制打分卡里的**一维贡献**(V2.1-④,**纯展示层**)。

    `contribPercent = 归一化权重 × 该维得分 × 100`,五维合计 ≈ `scorePercent`
    (各项独立舍入,末位可能差零点几)。唯一换算实现 =
    `neckline/report/score_display.py`,⛔ 双端都不许另写一份换算或另建中文标签表。

    🔴 **`neutralFilled=true` 是一句必须说出口的话**:那一维今天**没算出来**、按中性分
    0.5 计入 —— 它撑起来的那部分分数**不是"这一维表现好"**。⛔ 客户端不许把它渲染成
    与其它维度无差别的一根条(§3.8「没有」与「没看」必须分得开)。

    `dimScore`/`weight` 为 `null` = 该维在冻结留痕里就缺这个数,⛔ 不是 0。"""

    dim: str
    label: str = ""
    dimScore: Optional[float] = None
    weight: Optional[float] = None
    contribPercent: Optional[float] = None
    neutralFilled: bool = False


class TierOut(BaseModel):
    """一篮的 Tier 定档留痕(`tier_history` 一行,**A 类**)。

    **Tier = 注意力优先级,不是收益预测**(§2.8-C 红线):`rankInTier` 排第一 ≠ 最会涨。
    `rankMech` 是 LLM 微调**之前**的机械序,`llmRankDelta` 是微调位移 —— 两个都留着,
    才谈得上「定档可完整复现」(⑥ 的验收条款)。

    `tier` 取值域:**新数据 ∈ {1, 2}**(V2.1-② T3 全链退役,写侧收窄);
    历史留痕行仍可能是 `3`,⛔ 客户端别把 3 当非法值 —— 那是 V2 时代的真实数据。

    **V2.1-④ 新增两个只读键**(`scorePercent` / `scoreContributions`):
    `mechScore` 的百分制**等价换算 + 五维拆解**,由 `report/score_display.py` 从
    **同一份已冻结的 `mechBreakdown`** 算出 —— ⛔ 它不是第二个分数、不进任何判定路径
    (排序 / 哨兵 / 去留一律不读它,守门在 `tests/test_score_display.py` 三条)。
    `scorePercent=null` = 这一篮取不到分(没有 breakdown),**⛔ 不是 0 分**;
    此时 `scoreContributions` 为空数组(A 类每次响应重拼,不是冻结快照)。"""

    basketId: int
    tradeDate: str = ""
    tier: Optional[int] = None
    mechScore: Optional[float] = None
    mechBreakdown: Dict[str, Any] = Field(default_factory=dict)
    rankInTier: Optional[int] = None
    rankMech: Optional[int] = None
    llmRankDelta: int = 0
    llmReason: Optional[str] = None
    packVersion: Optional[str] = None
    scorePercent: Optional[float] = None
    scoreContributions: List[ScoreContribOut] = Field(default_factory=list)


class BasketOut(BaseModel):
    """一个篮子的壳(**A 类**)。`card=null` + `cardUnavailableReason='card_not_ready'`
    = 篮子在、卡没生成(事务 1 与事务 2 分开,**合法中间态**)。
    ⛔ 客户端不许把它显示成「篮子不存在」——那是另一回事(`basket_not_found`)。

    `tier` 取值域:**新数据 ∈ {1, 2}**(V2.1-② T3 退役);历史日期查回来仍可能是 `3`。

    🔴 **`scorePercent`/`scoreContributions` 两键只在"报告快照"这条路上有值**
    (V2.1-④,**B 类:随 `reports.basket_daily_json` 冻住**)。⛔ **live 路径
    (`GET /baskets`、`GET /baskets/{id}`)刻意留空** —— 那条路上同一个数住
    `tierHistory.scorePercent`(**分数是定档留痕的属性**,那才是它的家);两处都填
    等于在同一份响应里放两个必须永远一致的副本。**客户端读法(⑦ 照此)**:
    `basket.scorePercent ?? basket.tierHistory?.scorePercent`。
    `scorePercent=null` + `scoreContributions=[]` = **本篮无打分可显示**(老报告快照
    没有这两个键 / 这一篮没有定档留痕,两种成因经本 DTO 收口后不可区分,给用户的
    动作也相同)—— 🔴 **⛔ 绝不是 0 分**,客户端如实写「本报告版本无打分」。"""

    basketId: int
    basketKey: str = ""
    name: str = ""
    tradeDate: str = ""
    tier: Optional[int] = None
    memberCodes: List[str] = Field(default_factory=list)
    # V2.2-③-E(裁定 #9):篮子级引擎归属,**成员继承篮子引擎**(K8 §四「每票唯一
    # 主引擎及准确版本」由这三键 + memberCodes 共同表达)。新增可选键;历史行 = null
    # =「当时没有引擎归属概念」,⛔ 不回填猜测。
    engineCode: Optional[str] = None
    engineVersion: Optional[str] = None
    skeletonVersion: Optional[str] = None
    card: Optional[BasketCardOut] = None
    cardVersion: Optional[int] = None
    cardUnavailableReason: Optional[str] = None
    tierHistory: Optional[TierOut] = None
    scorePercent: Optional[float] = None
    scoreContributions: List[ScoreContribOut] = Field(default_factory=list)


class BasketsListOut(BaseModel):
    tradeDate: str = ""
    items: List[BasketOut] = Field(default_factory=list)


class DroppedBasketOut(BaseModel):
    """③b 一行(⑥-b-C;**V2.2-③ 升级为「名 / 分 / 卡在哪一关、差多少 / 原因码」**)。
    **`reason` 各码语义不同,⛔ 客户端不许合并成一句「未入选」**:
    `capacity_overflow` = 关口过了、位置满 →「今天机会多到装不下」;
    `below_quality_line` = 历史码(V2.2 门槛制前的「连质量线都没过」,老快照仍会出现);
    V2.2 新码见 `report/basket_daily.py::DROPPED_REASON_LABEL`(证据关降级出局 /
    机械关硬否决 / 成员全出篮 / 引擎缺席两种)。**码字符串一经落库不改** —— ⑨ 按
    原因码归因,改码 = 历史归因断线。
    `gate`/`gateDetail`(新增可选键):卡在哪一关、差多少(老快照缺键,零删键)。
    **没有 `basketId`** —— 它没进 `baskets` 表,给一个 id 会让人以为点得进去。"""

    name: str = ""
    mechScore: Optional[float] = None
    reason: str = ""
    gate: Optional[str] = None
    gateDetail: Optional[str] = None


class OutCandidateOut(BaseModel):
    """③b 的**另一类行**:V2.3.2-②-B 的**股票级 OUT**(K8 §十-11 四项 = 股票 /
    主引擎+版本 / 出局关口 / 理由)。

    ⚠ **与 `DroppedBasketOut` 刻意不合并**:那一类是**篮子级**的「档位已满 · 未定档」
    (`capacity_overflow`)—— K8 §八 的 OUT 适用状态里**没有**"位置满",它不是 OUT;
    这一类才是 K8 §六 意义上的 OUT。⛔ 客户端两段分开渲染,别合成一张表。
    `outReason` 与 `droppedBaskets.reason` **共用同一套原因码**(`DROPPED_REASON_LABEL`),
    ⛔ 不另起第二套词表。**没有 `basketId`** —— 它没进 `baskets` 表。

    ⚠ **`basketKey` 必须发**(2026-08-11 复审整改):同一只票可能在同一天的**多个**
    OUT 篮里出局(篮子间成员可重叠,`out_candidates` 主键就含 `basket_key`)——
    不发它,客户端 `Identifiable.id` 会撞主键、Markdown 会出两行一模一样的记录。
    ⛔ 它**不是** `basketId`(点不进去),只是消歧标识。"""

    tsCode: str = ""
    name: str = ""
    role: Optional[str] = None
    engineCode: Optional[str] = None
    engineVersion: Optional[str] = None
    outGate: Optional[str] = None
    outReason: str = ""
    outDetail: Optional[str] = None
    basketKey: Optional[str] = None


class BasketVerificationOut(BaseModel):
    """⑧ 的「当前状态」三路读法(**A 类**)。三个位分别回答不同问题,⛔ 不许合并:
    `state` = 四态之一(verified/partial/unclear/falsified);
    `provisional=true` = 盘中暂态、未收盘定论;
    `notEvaluated=true` = **今天还没判过**(不是「判了是 unclear」)。
    `rows` 是当日全部审计行(append-only,不回写)。"""

    basketId: int
    tradeDate: str = ""
    state: str = ""
    label: str = ""
    source: Optional[str] = None
    observedAt: Optional[str] = None
    provisional: bool = False
    notEvaluated: bool = False
    evidence: Optional[Dict[str, Any]] = None
    rows: List[Dict[str, Any]] = Field(default_factory=list)


class BasketReviewOut(BaseModel):
    """⑨ 的一篮盘后复盘(`mech` 是 **B 类冻结快照**,`llmText` 是参考件)。

    `depth`:`full`(T1/T2 详复盘)| `brief`(**历史值**:V2 时代 T3 篮子的简评;
    V2.1-② T3 退役后新数据恒 `full`,但历史行照常读回,⛔ 客户端别把 `brief` 当非法值)。
    `tier`:新数据 ∈ {1,2},历史数据仍可能是 3。
    `llmText=null` + `llmSkipReason` 非空 = **未生成**(预算耗尽/降级),
    ⛔ 不拿空串冒充「生成了但没内容」。"""

    basketId: int
    basketKey: str = ""
    name: str = ""
    tier: Optional[int] = None
    d0: str = ""
    reviewDate: str = ""
    depth: str = ""
    mech: Dict[str, Any] = Field(default_factory=dict)
    llmText: Optional[str] = None
    llmSkipReason: Optional[str] = None
    degraded: bool = False
    verification: Optional[Dict[str, Any]] = None


class BasketDailyOut(BaseModel):
    """报告里的篮子日报三段(③ / ③b / ④)。**每段各自带 `*Available` +
    `*UnavailableReason`**:空数组 + `available=true` = **今天真没有**(合法输出);
    `available=false` = **本次没取到**。⛔ 两者在界面上必须讲不同的话。

    `droppedBaskets` **默认空数组**(⑭-A 契约原文)—— 但空数组只有在
    `droppedBasketsAvailable=true` 时才等于「今日无未定档篮子」。"""

    tradeDate: str = ""
    baskets: List[BasketOut] = Field(default_factory=list)
    basketsAvailable: bool = False
    basketsUnavailableReason: Optional[str] = None
    # ⛔ `droppedBaskets*` 三键**原样保留、一个不删**(契约只增不删)。
    # ⚠ **V2.3.2-②-A 起内容已窄化**为「非 OUT 的未定档行」——判据 =
    # `selection/basket_store.py::is_out_reason()`(唯一源;当前 `NON_OUT_REASONS`
    # 只含 `capacity_overflow`)。OUT 票改由下面的 `outCandidates` 逐股给出,
    # ⛔ 两段不许合并、也不许同一批票双列。**键没删、语义窄了**。
    droppedBaskets: List[DroppedBasketOut] = Field(default_factory=list)
    droppedBasketsAvailable: bool = False
    droppedBasketsUnavailableReason: Optional[str] = None
    # V2.3.2-②-B:③b 的第二类行 —— **股票级 OUT**(K8 §六 候选三态之一)。
    outCandidates: List[OutCandidateOut] = Field(default_factory=list)
    outCandidatesAvailable: bool = False
    outCandidatesUnavailableReason: Optional[str] = None
    reviews: List[BasketReviewOut] = Field(default_factory=list)
    reviewsAvailable: bool = False
    reviewsUnavailableReason: Optional[str] = None
    reviewD0: Optional[str] = None
    packVersion: Optional[str] = None
    # ── 🔴 V2.4.0 P2.5:「正式空结果」与「系统缺席」严格分开(K8 §十)───────────
    # · `basketsAvailable=true` + 空数组 = **今天没有形成正式篮子**(合法输出);
    # · `basketsAvailable=false` + 下面四位 = **选股解释未完成** ——
    #   ⛔ 界面绝不许把它说成「今天没有机会」。
    # 🔴 判读逻辑唯一实现 = `selection/basket_stage_handoff.py::stage_verdict`
    # (P0-39 定案),本组字段只是把它**下发到契约**,⛔ 不在客户端再推一遍。
    selectionStage: Optional[str] = None
    selectionUnavailableReason: Optional[str] = None
    #: 🔴 **未解释 seed 不是第四种候选状态**(K8 §十 末句):⛔ 不冒充 T2 / OUT、
    #: ⛔ 不进选股时钟样本。`null` = 当时没记这一位(⛔ 不拿 `0` 冒充「一个都没有」)。
    unexplainedSeedCount: Optional[int] = None
    unexplainedSeedSummary: Optional[str] = None
    # V2.4.2 读时运行态覆盖。缺席不是 complete，而是「该快照没有此概念/本次未取到」。
    selectionState: Optional[str] = None
    selectionStateText: Optional[str] = None
    notes: List[str] = Field(default_factory=list)


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
    # 报告发布日；周日出报告时为周日。tradeDate 仍是行情/篮子事实所绑定的交易日。
    reportDate: str = ""
    tradeDate: str
    generatedAt: str
    strategyVersion: str
    sentiment: Dict[str, Any]                # SentimentDashboard 快照(含 position_quota 三态)
    sectors: List[Dict[str, Any]]            # 强势板块 + 板块年龄
    # V2-⑭-B:篮子日报三段(③ 今日篮子 / ③b 未定档 / ④ 昨日复盘),取代已退役的
    # `candidates`。透传 `reports.basket_daily_json` 落库快照(**随报告冻住**,读三天前
    # 的报告该看到当时的篮子,不是今天的)。老报告(建于本字段前)读回一份三段全标
    # `available=false` 的诚实占位,⛔ 不冒充「那天没有篮子」。
    basketDaily: BasketDailyOut = Field(default_factory=BasketDailyOut)
    # v1.1-B.4 漏录兜底:当日买点哨兵触发过但台账无补录时的一句提示(否则空串)。
    # **实时计算**(GET /report 每次读时按当前台账重算,用户补录后自动消失),不落库、不改评分。
    # ⚠ **V2-⑬ 起买点哨兵已退役**:新数据下恒空;**历史日期回放仍会非空**(`pipeline.py`
    # 读 `sentinel_events` 的历史 `entry` 行)—— 与 `candidates` 的历史回放语义同类。
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
    # V2-⑭-A 起再加**扫描层三键**(`scanLayerDate`/`scanLayerLagDays`/`scanLayerStale`)——
    # **三件独立故障并列,⛔ 不合并成一个 bool**:概念板块日更 / 行业强度日更 / 扫描层
    # 批算,合并就分不清哪个坏了。扫描层没跑 → 今日无种子 → 今日无篮子,而「今天没有
    # 篮子」与「今天没看」必须能分开。**该三键整体缺席 = 本次连新鲜度都没查到**,
    # 不是"新鲜"。
    dataFreshness: Dict[str, Any] = Field(default_factory=dict)
    degraded: bool = False
    reason: str = ""


# —— 4A.3 盘中看板 —— ⚠ **LEGACY AUDIT(V2.4.0 P0 起)** ————————————————————
#
# 下面三个 DTO 服务于 `GET /api/v1/board`,该端点保留一个兼容周期供**已装 2.3.x
# 客户端**使用;**v2.4.0 客户端零调用**,且它们**不驱动任何 v2.4.0 界面**。
# 🔴 `retreatBrake.active` 在新生产链下**永不再被置 true**(退潮判级已退役);
# `sentinel='invalidation'` 也不再有新行 —— 但**历史行照实返回**,
# ⛔ 不通过删历史行让界面「看起来修好了」(P0.5)。
# ⛔ **不删这三个类**:删了老 App 会 404 / 解不出;清理留给下一个破坏性 API 大版本。

class RetreatBrakeOut(BaseModel):
    """⚠ LEGACY AUDIT:新链路恒 `active=False`;非 false 只可能来自历史行。"""
    active: bool
    reason: str = ""


class BoardEventOut(BaseModel):
    """⚠ LEGACY AUDIT:`sentinel` 取值里的 `invalidation`(通用盘中证伪)与
    `retreat`(退潮)自 V2.4.0 P0 起**不再有新行**,只剩历史;`holding` 类事件仍在
    产生,但新客户端改从 `PositionOut.alerts` 读(P0.5+),不再走本端点。"""
    sentinel: str                # 买点(entry) / 证伪(invalidation,⛔ 已退役) / 持仓(holding)
    code: str
    name: str
    eventKey: str
    verdict: str                 # 判决文案(哨兵已落库的 reason 文本)
    ts: str


class BoardOut(BaseModel):
    """⚠ LEGACY AUDIT —— 见本节抬头。"""
    tradeDate: str
    asof: str = ""
    retreatBrake: RetreatBrakeOut
    events: List[BoardEventOut] = Field(default_factory=list)


# —— 4A.4 持仓 ————————————————————————————————————————————————————————

class PositionAlertOut(BaseModel):
    """今日**该持仓自己的**一条哨兵提醒(V2.4.0 P0.5+)。

    源 = 当日 `sentinel_events` 里 `sentinel` 属于**白名单四类**
    (`holding` / `attention` / `circuit` / `precall`,V2.4.0 复审 🔴-1 裁定 A)
    且 `ts_code` 等于本持仓的行,逐字段透传,**服务端不重判、客户端不二次裁定**。
    ⚠ **两类环境事件不走这里**(2026-08-12 用户裁定 ②):`sector_bid_fade` /
    `market_shock` 落在 `PositionsOut.portfolioAlerts`,裁定原文「⛔ 也不重复塞进单票详情」。

    · `eventKey` —— 既有事件键,现役四种:`stop_approach`(逼近/触发亏损警戒线)/
      `take_profit`(回落止盈,由现役章程的 `take_profit_retrace` 驱动;`v2.3-k8` 下
      该配置为空 → 恒不触发)/ `sector_dive`(所属板块跳水)/ `exit_reference`
      (触达离场参考区间)。⚠ **未识别的键原样透传**,展示层自己兜底,
      ⛔ 服务端不在这里建第二份中文映射之外的白名单。
    · `verdict` —— 事件落库时那句话(`payload.body`)原文,⛔ 不改写、不加工。
    · `ts` —— 落库时刻(`sentinel_events.pushed_at`,UTC ISO 串,同 `BoardEventOut.ts`)。
    · `level` —— 展示层强调档,由 `eventKey` 机械派生(⛔ 不是新判定):
      `critical`(stop_approach)/ `warn`(sector_dive)/ `info`(其余)。
      口径与哨兵推送时用的 `channels` 级别同源,⛔ 别在客户端另定一套。

    🔴 **⛔ 这里只画事实,不画状态**:没有「运行正常」、没有事件数量以外的汇总、
    没有市场级行 —— 那些正是 P0 撤销掉的东西。
    """
    eventKey: str
    verdict: str = ""
    ts: str = ""
    level: str = "info"


class PortfolioAlertOut(BaseModel):
    """今日**组合环境提醒**一条(V2.4.0 复审挂账 P1-81 的落点,**2026-08-12 用户裁定 ②**)。

    🔴 **裁定原文(逐字)**:「两类提醒统一落在**持仓页顶部的「组合环境提醒」**。
    `sector_bid_fade` **按板块展示,并列出受影响持仓**;`market_shock` 作为**全组合提醒**。
    二者均使用**黄色**提醒,**只提供环境证据,不给交易指令,不影响选股等级和交易资格,
    也不重复塞进单票详情**。」

    源 = 当日 `sentinel_events` 里 `sentinel='attention'` 且 `payload.kind` 属于
    `_PORTFOLIO_ONLY_KINDS` 的行,逐字段透传 —— **服务端不重判、客户端不二次裁定**:

    · `kind` —— `sector_bid_fade` | `market_shock`(`notify_kinds` 的码,⛔ 不发中文)。
    · `scopeKind` —— `sector`(按板块)| `portfolio`(全组合)。🔴 客户端**先按它分支**,
      ⛔ 别拿 `affectedCodes` 空不空去猜是哪一类:全组合那一类**本来就不列码**。
    · `scopeCode` —— 板块基准指数码(`sector` 那一类)/ 空串(`portfolio`)。
      中文换算在客户端展示层(照 `nkBoardLabel` 先例,未识别值原样透传)。
    · `verdict` —— 事件落库时那句话(`payload.body`)原文,⛔ 不改写、不加工。
    · `ts` —— 落库时刻(`sentinel_events.pushed_at`,UTC ISO 串)。
    · `level` —— 恒 `warn`(裁定 ③ 逐字:「二者均使用黄色提醒」),⛔ 不是新判定。
    · `affectedCodes` —— **受影响持仓**(冻结在事件 `payload.metrics.holders` 里的那一批,
      = 检测那一刻真正落在这个板块下的持仓码)。⛔ 服务端**不按现在的持仓重算** ——
      那会把「当时受影响的」讲成「现在还在的」。
    · `affectedRecorded` —— 🔴 **三态的第三态**:`False` = 这条事件的 payload 里
      **压根没记**受影响持仓(老行 / 老版本)。⛔ 别把它与「记了、但是空的」折平,
      也 ⛔ 别把「没记」画成「没有受影响的持仓」。

    🔴 **⛔ 只画环境证据,不画指令**:没有「该不该动」、没有状态机、没有红绿灯 ——
    那些正是 P0 撤销掉的东西。**本模型不参与任何选股等级 / 交易资格判定**
    (结构性:它只在 `/positions` 响应里出现,`selection/**` 与 `auction/**` 零引用,
    守门单测正反双向锁)。
    """
    kind: str
    scopeKind: str = "portfolio"
    scopeCode: str = ""
    verdict: str = ""
    ts: str = ""
    level: str = "warn"
    affectedCodes: List[str] = Field(default_factory=list)
    affectedRecorded: bool = False


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
    # —— V2.3.2-⑤(K8.md §十九):`stopLine` 这条线**现在是「亏损警戒线」**——————————
    # 🔴 **数值口径一字未变**:仍是 `buy×(1−stop_pct)`,`stop_pct` 仍是 0.05、仍是唯一源。
    # 变的是它**触发什么**:`lossWarningAction == "review"` = 到线只发**亏损警戒**、由用户
    # 完成离场决策,**系统不触发任何自动卖出**(K8.md §十三 逐字)。
    # ⚠ **两位都可能是 `null`** = 现役章程**没有声明过**这个语义(`v2.2-k8` 及以前的行里
    # 就没有这两个字段)—— ⛔ 不是"声明为强制条件单",也⛔ 不许拿 `stop_pct` 顶上去。
    # 客户端据此决定这条线叫「亏损警戒线」还是「止损线」(`decodeIfPresent`,缺键 → 老文案)。
    lossWarningPct: Optional[float] = None
    lossWarningAction: Optional[str] = None
    # 🔴 **V2.4.0 P3.1 新增**:现役章程的 `take_profit_retrace`(与 `lossWarningPct` 同一次
    # `_active_config()` 读出,零新增读库)。`null` = **该章程没有配置回落止盈**(`v2.3-k8`
    # 起的常态,K8.md §十三)——客户端据此把「回落止盈」那一格显示成「本版无机械回落止盈」,
    # 不再靠 `retraceState.triggered == false` 反推"没有这项纪律"(那个位只回答"触发了没有",
    # 无法区分"没配置"与"配置了但还没触发",两者在展示层必须讲不同的话)。
    # ⚠ **不参与任何判定** —— 判定仍在 `retraceState.triggered`(复用
    # `sentinel/holding.py::check_take_profit`),本字段只是这条线该不该显示的文案指纹。
    takeProfitRetrace: Optional[float] = None
    # ⛔ **已停用的自证位**(2026-08-12 用户裁定 ④:客户端那个「已在券商挂 -5% 条件单」
    # 复选框**现役版本删除**)。
    # 🔴 **它从来就没有对账消费方**:`app.py` 恒发字面量 `False`(既不读库也不读请求),
    #    没有 DB 列、没有写入端点;`review/reconcile.py::classify_stop_discipline` 的输入
    #    只有回合 `pnl_pct` + 现役章程 `stop_pct` + `advisory`,**一次都没读过这个位**。
    #    ⚠ 本行原注释写的「真对账在 4D 周复盘」是**一句从未兑现的承诺**,已就地订正
    #    (§七 P3-80 结案段记了这次订正的出处)。
    # 🔴 **键保留是红线要求**(⛔ 停采不删):已装老客户端(2.3.x)对它是 `try c.decode`
    #    硬解码,服务端删键会让老包整份持仓解不出 —— 淘汰顺序永远是「先发一版客户端改
    #    `decodeIfPresent` → 下一版服务端才可删键」,本版正是那"先发的一版"。
    stopOrderChecked: bool = False
    # —— v1.1-B.1 持仓生命周期派生字段(服务端算好,客户端不重算日历)——————————
    dCount: int = 1              # D 计数(买入日=D1,交易日历口径,单一源 positions.d_count)
    # 现役 max_hold_days(读 config,不硬编);= 非浮盈时间退出档。
    # ⚠ **V2.2-⑤ 起可为 `null`** = **章程无时间退出条款**(`v2.2-k8`,K8 §十三)。这是
    # **取值域放宽,不是删键**(〇b-3 零删键铁律仍满足);已装 2.0.0/2.1.0 客户端用
    # `decodeIfPresent(Int.self) ?? 5` 解,`null` 与缺键同路、不炸(⑥ 再补展示层文案)。
    # ⛔ 不许服务端拿 5 顶上冒充"有时间退出" —— 那正是 §3.11-E 否决哨兵位的同一种病。
    maxHoldDays: Optional[int] = 5
    distToStopPct: Optional[float] = None   # (price−stopLine)/price;无实时价 → null
    retraceState: Optional[Dict[str, Any]] = None   # 回落止盈状态{peak,retracePct,triggered};无价/无阈 → null
    todayAction: str = ""        # 今日动作提示(D5离场 / 距止损 / 回落止盈已触发 等)
    # —— v1.3-① 两档时间退出(服务端按 D5 净浮盈判好下发,客户端不重算)——————————————
    # 该单有效硬上限:非浮盈=maxHoldDays;浮盈豁免=max_hold_days_profit(如 15)。
    # ⚠ V2.2-⑤ 起同样可为 `null`(章程无时间退出条款 → 没有"有效硬上限"这回事)。
    maxHoldDaysEffective: Optional[int] = 5
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
    # —— V2.4.0 P0.5+:今日该持仓自己的哨兵提醒(**纯增量键**,旧客户端不解也不崩)——
    # 🔴 **它是 P0.3「先迁移再删页面」的落点,不是「盘中动态页换了个地方」**:
    # 亏损警戒 / 离场参考 / 板块跳水这三类提醒此前**只经 `GET /board` 下发**,而 P0
    # 要求新客户端零调用 `/board` —— 直接删页面 = 静默弄丢仍然有效的持仓提醒。
    # 取数 = 当日 `sentinel_events` 中 `sentinel='holding'` 且 `ts_code` 匹配本持仓的行
    # (复用 `sentinel/dedup.py::load_events_for_date`,⛔ 不新建取数实现)。
    # ⛔ **只取 `holding`**:`invalidation` / `retreat` 已停写,`precall` 归竞价报告。
    # ⛔ 无市场级行、⛔ 无「运行正常」绿灯、⛔ 无轮询(随 `/positions` 一起拉)、
    # ⛔ 客户端不做任何二次裁定 —— 只按时间列出服务端已经落库的事实。
    alerts: List["PositionAlertOut"] = Field(default_factory=list)


# ⚠ **`CircuitEpisodeOut` / `CircuitStateOut` 已于 v2.3.0 物理删除**（两步淘汰第二步）：
# 熔断三件机制在 V2.2-⑤-B 随用户裁定 #8 整体退役，`PositionsOut.circuit` 当时按零删键
# 铁律（〇b-3）恒发空态过渡一版；本版连键带类型一起删。
# 🔴 **删得掉的判据是逐版核实过的**：历代客户端 `/positions` 一律解进
# `PositionsListResponse { holdings }`，**没有任何一版声明过 `circuit` 字段**（2.0.0 那台
# iPhone 读的是**独立端点** `GET /circuit`，该端点自 V2.2 起 404，与本键无关）。
# ⛔ 别把这条当成「零删键铁律可以不守」的先例 —— 它守住了，只是核实之后发现
# 这个键从来没有消费方。

class PositionsOut(BaseModel):
    holdings: List[PositionOut] = Field(default_factory=list)
    #: 🔴 **组合环境提醒**(2026-08-12 用户裁定 ②,§七 P1-81 的落点)。
    #: 它是**持仓页顶部**那一段,与逐持仓的 `PositionOut.alerts` **刻意分开**:
    #: 板块级 / 市场级的事件匹配不到任何一笔持仓码,塞进单票详情就会把一条环境判断
    #: 复述 N 遍(裁定原文「⛔ 也不重复塞进单票详情」)。
    #: ⚠ 空数组 = 今天没有这两类事件,⛔ 客户端不许因此合成一句「环境正常」。
    portfolioAlerts: List[PortfolioAlertOut] = Field(default_factory=list)


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
    # V2.3.2-⑤:同 `PositionOut` —— 这条线在 `lossWarningAction=="review"` 下是
    # 「亏损警戒线」(数值口径不变,系统永不代下单);`null` = 现役章程未声明该语义。
    lossWarningPct: Optional[float] = None
    lossWarningAction: Optional[str] = None


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
    # v2.0.0(契约线审计 🟡 Y7):**幂等键**。客户端为"这一次开仓意图"生成一个稳定串
    # (UUID 即可,**重试时必须复用同一个**),服务端同键二次提交 = 重放上次结果、
    # **不开第二笔仓**,响应 `replayed=true`。缺省 `None` = 不设防(CLI / 老客户端 /
    # 历史补录逐字节不变)。⚠ 它防的是「服务端已落库、响应没回到客户端」这一类重试
    # ——开仓是**不可逆记账**,重复一笔的代价是后面每一个纪律判定都建立在错的持仓上。
    idempotencyKey: Optional[str] = None


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
    # V2.2-③-E:交易资格四件套(K8 §十一)缺件**警示**(⛔ 不是拦截 —— 系统只审计
    # 不代下单,§3.8)。null = 四件齐或无来源计划可验;非空 = 人话缺件清单
    # (单一文案源 `basket_card.trade_plan_missing_label`)。
    planIncompleteNotice: Optional[str] = None
    # v2.0.0(契约线审计 🟡 Y7):`true` = 本次请求**没有开新仓**,`positionId` 指的是同一个
    # `idempotencyKey` 之前已经开好的那笔。如实透出,别让"看起来成功了"掩盖"其实什么都没
    # 发生";客户端据此不必重复提示"已开仓"。老客户端忽略未知键,不受影响。
    replayed: bool = False


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


# —— V2-⑭-B 计划继承(`position_plans`)+ 建仓快照(`entry_snapshots`)————————

class PositionPlanOut(BaseModel):
    """一条持仓计划版本(⑩-B)。`version=1` 恒从 D0 篮子卡继承;用户可创建
    `version=2,3…`,**新版本不修改原始篮子卡**(单测锁死)。

    `plan` **原样透传领域 `plan_json`(snake_case)** —— 同 `CustomAlertOut.rule` /
    `WeeklyReviewOut.result` 的既定透传惯例:它是哨兵旁路 E 的判据源(`exit_reference_
    armed` / `..._reason` / `..._note` / `..._muted` 四键**恒存在**,缺键即不武装,
    fail-closed),在 API 层再镜像一套嵌套模型只会多一处会漂的定义。
    ⚠ `plan.available=false` + `plan.reason` ∈ {`no_source_basket`, `card_not_ready`}
    是**合法**结果(独立买入 / 卡未就绪),行照落、⛔ 不省略整条记录。
    ⚠ `plan.reason="card_corrupt"`(2026-08-04 起,B1 同类裁定)**不是**合法中间态,
    是数据事故——basket_store 的冻结卡有行但读不出(`json` 解不出 / 顶层内容键缺失),
    与 `card_not_ready`(压根没有行)分得开,⛔ 客户端不许把两者合并展示。判据唯一
    检测点在 `neckline.selection.basket_store`(打 ERROR),本模型只透传 reason 字符串,
    不是独立的 404/500 端点(这是 `plan.reason` 内嵌字段,不是 `GET /baskets/{id}/card`
    那种整请求即整卡的场景,故不单独升 500——同 `_shape_basket` 内嵌卡的既定姿势)。"""

    id: int
    positionId: int
    version: int
    sourceBasketId: Optional[int] = None
    sourceCardVersion: Optional[int] = None
    plan: Dict[str, Any] = Field(default_factory=dict)
    note: Optional[str] = None
    createdAt: str = ""


class PositionPlansOut(BaseModel):
    items: List[PositionPlanOut] = Field(default_factory=list)


class PositionPlanCreateIn(BaseModel):
    """`POST /positions/{id}/plans`(⑩-B「用户可创建新版本」的 HTTP 入口)。

    `plan` 是完整的新版本正文(snake_case,同 `PositionPlanOut.plan` 口径)。
    ⚠ **武装态由服务端重算,客户端说了不算**(⑪-D-B 闸②):即使请求体里带了
    `exit_reference_armed`,`create_position_plan_version` 也会拿这笔仓的真实成交价
    重过一遍闸 —— 否则"写个新版本"就成了绕开红线闸的后门。"""

    plan: Dict[str, Any] = Field(default_factory=dict)
    note: Optional[str] = None


class EntrySnapshotOut(BaseModel):
    """建仓瞬间的冻结快照(⑩-A,`entry_snapshots` 一行,**B 类冻结快照**)。

    `snapshot` 里的 `not_captured` 数组**如实列出本次没采到的项**(资金流 / 竞价表现 /
    换手率 / 量比四项在 ⑩ 的范围内未采集)—— ⛔ 别把"没采"读成"没有"。"""

    positionId: int
    tsCode: str = ""
    tradeDate: str = ""
    basketId: Optional[int] = None
    cardVersion: Optional[int] = None
    tier: Optional[int] = None
    role: Optional[str] = None
    snapshot: Dict[str, Any] = Field(default_factory=dict)
    createdAt: str = ""


# —— V2-⑭-B 画像 / 策略包 / 评价(⑫ 与 ③ 的产出接上 API 面)——————————————

class ProfileOut(BaseModel):
    """偏好画像 / 能力画像(⑫-B,每期一版)。**两张账刻意分开**:偏好答「喜欢什么」、
    能力答「什么真有效」——⛔ 不合并成一张"用户画像"。

    每行必带 **样本量 / 时间范围 / 置信度**(`sampleN` / `windowStart` / `windowEnd` /
    `confidence`);`confidence='low'` 时客户端**必须**显式写「样本不足,不给结论」,
    ⛔ 不许把低置信度的数字当结论展示(⑫ 验收条款)。
    `asOf` 为空 = **该期从未算过**(不是"算出来是空的")。

    🔴 **初期不得反向影响客观 Tier**(蓝图 4.4 禁令):本 DTO 只服务展示,
    `neckline/selection/` 与 `neckline/scan/` 全目录零 `profile` 引用(守门单测锁死)。"""

    asOf: str = ""
    available: bool = False
    unavailableReason: Optional[str] = None
    items: List[Dict[str, Any]] = Field(default_factory=list)


class PackOut(BaseModel):
    """一个选股策略包(⑫ `selection_packs` 一行)。

    ⚠ **策略包与纪律章程是两条版本线、两张表、两套激活流程,永不混用**(§五 红线 6):
    本 DTO **不含任何纪律参数**(`stop_pct` 等住 `strategy_versions`)。
    `config` 原样透传(原语白名单在领域层已卡死);**包不装可执行代码**(§12.1 定案)。"""

    packVersion: str
    isActive: bool = False
    createdAt: str = ""
    activatedAt: Optional[str] = None
    manifest: Dict[str, Any] = Field(default_factory=dict)
    config: Dict[str, Any] = Field(default_factory=dict)


class PacksListOut(BaseModel):
    items: List[PackOut] = Field(default_factory=list)


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


class InfoCardMemberTagOut(BaseModel):
    """⑬-N-K7 成员标注件一条(⑦-K7 唯一实现 `selection/member_tags.py` 产出,
    服务端原样透传)。`text` **已含「参考、非指令」后缀**,客户端不许改写、不许截断。
    **四不硬约束**:不进排序 / 不进哨兵 / 不改去留 / 不加分。"""
    code: str            # pullback_leader | warn_streak_top | warn_chase_zone
    label: str
    tone: str            # neutral | warn(客户端据此上色)
    text: str
    source: str          # 证据出处(研究报告锚点)


class InfoCardBasketPeerOut(BaseModel):
    """同篮其他成员一行(数值取自卡里冻结的成员节,零重算)。"""
    tsCode: str
    name: str
    roleLlm: Optional[str] = None
    roleMech: Optional[str] = None
    roleConflict: bool = False
    rsRank: Optional[int] = None
    close: Optional[float] = None
    industry: Optional[str] = None


class InfoCardBasketOut(BaseModel):
    """⑬-N 三块:①所属篮子与共同驱动 ②本票角色(含对拍分歧)③与同篮其他成员的对比。
    `available=False` 时 `unavailableReason` **必有值**且两态分得开:「不在任何篮子里」
    vs「在篮子里但卡没生成」—— ⛔ 客户端不许把两者显示成同一句话。"""
    available: bool = False
    unavailableReason: Optional[str] = None
    basketId: Optional[int] = None
    basketKey: str = ""
    name: str = ""
    tier: Optional[int] = None
    driver: str = ""
    driverKind: str = ""
    whyNow: str = ""
    roleLlm: Optional[str] = None
    roleMech: Optional[str] = None
    roleConflict: bool = False
    roleReason: str = ""
    isPrimary: bool = False
    # 🔴 裁定 ⑤:同 `BasketMemberOut` 的两位(⛔ `None`/空串 = 老卡没记,不是 confirmed)。
    primaryStatus: Optional[str] = None
    primaryPendingReason: Optional[str] = None
    industry: Optional[str] = None
    industryLift: Optional[float] = None
    peers: List[InfoCardBasketPeerOut] = Field(default_factory=list)


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
    # —— V2-⑬-N:篮子成员详情页地基 + ⑬-N-K7 标注件展示区 ————————————————————
    basket: InfoCardBasketOut = Field(default_factory=InfoCardBasketOut)
    tags: List[InfoCardMemberTagOut] = Field(default_factory=list)
    # 判不了的标注码(数据缺失)—— 与「判过没命中」是两回事,⛔ 不许合并成"没有标注"。
    tagsAbsent: List[str] = Field(default_factory=list)
    tagAbsences: List[Dict[str, str]] = Field(default_factory=list)


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


class ReviewSegmentOut(BaseModel):
    """复盘板块「累计」页里的**一段**(V2.1-⑤)。五段各一份,形状统一。

    🔴 **每段各自带 `available` + `unavailableReason`,⛔ 不许拿一个总开关罩住五段** ——
    校准产物没生成、画像没批算、这周没传交割单是**三件互不相干的事**,合成一句读者就
    分不清哪个没有。三态读法(plan §五⑤ 验收原文「有 / 没有 / 没取到」):

      · **有**   → `available=true` + 有内容;
      · **没有** → `available=true` + 空内容(该段自己的空态文案说清为什么空);
      · **没取到** → `available=false` + `unavailableReason`(⛔ 不许拿空数组冒充)。

    ⚠ **画像段与对账段的空态刻意判得不一样,⛔ 别"统一"**:画像缺席 = **系统自己那一步
    没跑**(周度批算未运行)→ 那是「没看」→ `available=false`;对账缺席 = 输入(券商
    交割单)**只能由用户给**、系统查过表确实没有 → 那是「没有」→ `available=true` +
    `detail.found=false`。两者给用户的动作完全不同(等系统 vs 去上传)。

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
    """复盘板块「累计」页的聚合读(V2.1-⑤,`GET /review/overview`)。

    **零现算**:五段全部读**已冻结 / 已落盘**的产物 —— 校准报告由离线周度作业算好落盘
    (§七 P0-23:本端点与盘中哨兵同进程,重活进常驻服务 = 卡死不报错),画像读
    `profile_*` 两表,对账读 `reviews` 表。⛔ 读不到就说读不到,**永不在线补算**。

    **包成绩单 = `calibration.detail.strata` 本身**(产物原文已按
    `pack_version × verification_ruleset_version` 分层)——⛔ 不另建第二份聚合,
    那就是「同一个数两个算法」的老病。

    🔴 **本端点一律不 404**(空态走各段的 `available=false`)→ V2.1 **零新增 reason
    字符串**,`SERVER_REASONS` 与客户端 `mapReason` 一字不动。"""

    weekStart: str = ""
    weekEnd: str = ""
    weekKey: str = ""                       # ISO 周键(`YYYY-Www`),对账段按它取
    calibration: ReviewSegmentOut = Field(default_factory=ReviewSegmentOut)
    preference: ReviewSegmentOut = Field(default_factory=ReviewSegmentOut)
    capability: ReviewSegmentOut = Field(default_factory=ReviewSegmentOut)
    reconcile: ReviewSegmentOut = Field(default_factory=ReviewSegmentOut)
    observations: ReviewSegmentOut = Field(default_factory=ReviewSegmentOut)
    # —— V2.2-④ 新增三段(**同样各自 `available` + `unavailableReason`**;
    #    ⛔ 不许被上面五段的任何一段罩住)——
    selectionClock: ReviewSegmentOut = Field(default_factory=ReviewSegmentOut)
    tradeClock: ReviewSegmentOut = Field(default_factory=ReviewSegmentOut)
    iterationSuggestions: ReviewSegmentOut = Field(default_factory=ReviewSegmentOut)


class ReviewHandoffOut(BaseModel):
    """校准移交件导出(V2.1-⑤,`GET /review/handoff`)。

    `markdown` = 一份能**直接交给策略台**的五节文档(窗口与样本量 / 校准报告原文 /
    画像两表 / 观察项清单 / 免责)。`sampleN` 是那份文档 §① 的数字版,便于客户端在
    按钮旁边先显示"这一份带多少样本"。

    **`available=false` 的两种成因文案必须分开**(⛔ 别合并):① 一期校准产物都还没有
    (周度作业没跑过)—— **会自愈**;② 指定窗口的产物**读不出**(文件在、JSON 解不出)
    —— **不会自愈**,要人排查。合成一句就会让人一直等一份永远好不了的产物。

    🔴 本端点同样**一律不 404**,零新增 reason。"""

    available: bool = False
    unavailableReason: Optional[str] = None
    windowFrom: str = ""
    windowTo: str = ""
    generatedAt: str = ""
    sampleN: Dict[str, int] = Field(default_factory=dict)
    markdown: str = ""


class ReviewGetOut(BaseModel):
    ok: bool = True
    found: bool = False
    week: str = ""
    generatedAt: str = ""
    # None(JSON null,非 `{}`)当 `found=False`——客户端据此把 `result` 解码成强类型
    # struct 的 Optional,不必为"空字典 vs 合法结果"写一套容错回退逻辑。
    result: Optional[Dict[str, Any]] = None
    material: str = ""


class MarketRegimeDayOut(BaseModel):
    """`market_regime_daily` 一行(V2.2-②,`GET /market-regime`)。`inputs` /
    `strengthening` / `weakening` **原样透传**领域层形状(同 `ReviewSegmentOut.detail`
    的既定惯例 —— API 层再镜像一套嵌套模型只会多一处会漂的定义);`inputs` 五维各自
    带 `available`/`unavailable_reason` 双位(§3.8)。`regimeLabel` 由服务端给人读名
    (唯一源 `scan/regime.py::REGIME_LABELS`,同 `PushKindOut.label` 先例)。"""

    tradeDate: str
    regime: str
    regimeLabel: str = ""
    regimeReason: str = ""
    inputs: Dict[str, Any] = Field(default_factory=dict)
    strengthening: List[Dict[str, Any]] = Field(default_factory=list)
    weakening: List[Dict[str, Any]] = Field(default_factory=list)
    skeletonVersion: str = ""
    computedAt: str = ""


# —— V2.2-④ 双时钟(`GET /clocks/selection` · `GET /clocks/trade/{position_id}` ·
#    `POST /clocks/trade/{position_id}/note`)——————————————————————————————————
#
# 用户主观说明的长度上界:**唯一源在领域层** `review/trade_clock.USER_NOTE_MAX_CHARS`
# (⛔ 契约层不抄一个数 —— 两处各写一份,改一处就静默出现"客户端说能写、服务端说太长")。
from neckline.review.trade_clock import USER_NOTE_MAX_CHARS as _USER_NOTE_MAX_CHARS

#
# ⚠ `mech` / `final` / `entryPlan` / `engineBreakdown` **原样透传**冻结件形状(同
# `ReviewSegmentOut.detail` 的既定惯例)。这几个是 **B 类冻结快照**(写入当时冻住、
# 不随服务端升级补键)→ 客户端 DTO **必须手写 `init(from:)` 做 `decodeIfPresent`**
# (CLAUDE.md V2-⑮ 铁律;守门按类型名精确锁)。

class SelectionClockOut(BaseModel):
    """一篮的选股时钟**结案件**(V2.2-④-A)。

    🔴 样本口径 = D0 **全部** T1/T2,**与用户买没买无关**(K8 §十四 第 3 条)——
    客户端文案不许写成「你关注的篮子」之类,那会把覆盖域讲小。
    `tierAccuracy` 是 ⑦-b **四态原样**(`verified`/`partial`/`unclear`/`falsified`)
    加两个"没判"码,**⛔ 不是 0/1 的对错** —— 折成正确率是周度侧的事。"""

    basketId: int
    d0Date: str
    d1Date: str
    coveredTier: int
    regimeAtD0: Optional[str] = None
    tierAccuracy: Optional[str] = None
    untriggeredReason: Optional[str] = None
    closedAt: str = ""
    skeletonVersion: str = ""
    verificationRulesetVersion: str = ""
    engineBreakdown: Dict[str, Any] = Field(default_factory=dict)
    mech: Dict[str, Any] = Field(default_factory=dict)


class SelectionClocksOut(BaseModel):
    """区间内已结案的选股时钟。**空列表 = 这段时间没有结案样本**(合法);
    ⛔ 别读成"系统没跑" —— 那要看 `basket_review_daily` 与段状态。"""

    dateFrom: str = ""
    dateTo: str = ""
    items: List[SelectionClockOut] = Field(default_factory=list)


class TradeClockEventOut(BaseModel):
    """交易时钟事件流水一行(**append-only**)。`userNote` = K8 §十五 用户主观说明。"""

    id: int
    eventDate: str
    kind: str
    mech: Dict[str, Any] = Field(default_factory=dict)
    userNote: Optional[str] = None
    createdAt: str = ""


class TradeClockOut(BaseModel):
    """一笔真实买入的交易时钟(V2.2-④-B)。`status` ∈ `running|closed`;
    `final` 只在结案后有值(**运行中恒 `null`** —— 「还没结案」与「结案了但八项算不出」
    必须分得开)。`basketId=null` = 非篮子来源的手动开仓,**合法**。"""

    positionId: int
    tsCode: str
    basketId: Optional[int] = None
    openedOn: str
    closedOn: Optional[str] = None
    status: str
    entryPlan: Dict[str, Any] = Field(default_factory=dict)
    final: Optional[Dict[str, Any]] = None
    events: List[TradeClockEventOut] = Field(default_factory=list)


class TradeClockNoteIn(BaseModel):
    """`POST /clocks/trade/{position_id}/note` 的请求体(K8 §十五「每次一条简短说明」)。

    ⛔ **不做 LLM 代猜**(§七 P3-28 纪律):这条是**用户自己写的**,系统不生成、
    不改写、不合并 —— 只追加。

    ⚠ 长度上限走 `Field` 约束(→ 非法即 422,与本项目其它端点的非法请求体同款形状),
    **⛔ 刻意不新造一个 reason 字符串**(V2.2「零新增 reason」是机器判据);上界与领域层
    `review/trade_clock.USER_NOTE_MAX_CHARS` **同源**,⛔ 别在这里抄一个数。"""

    note: str = Field(min_length=1, max_length=_USER_NOTE_MAX_CHARS)


class TradeClockNoteOut(BaseModel):
    ok: bool = True
    eventId: int = 0
    eventDate: str = ""
    coverage: Dict[str, Any] = Field(default_factory=dict)


class MarketRegimeOut(BaseModel):
    """`GET /market-regime` 响应(V2.2-②)。🔴 **只读、零现算、一律不 404**(空态走
    `available=false` + 自由文本 `unavailableReason`)→ **零新增 reason 字符串**,
    `SERVER_REASONS` 与客户端 `mapReason` 一字不动(体例照 `/review` 三条硬边界)。
    单日查询填 `day`;`from`/`to` 区间查询填 `days`(升序,缺行的日子不出现)。"""

    available: bool = False
    unavailableReason: Optional[str] = None
    day: Optional[MarketRegimeDayOut] = None
    days: List[MarketRegimeDayOut] = Field(default_factory=list)


# —— V2.3.3-⑤ D1 集合竞价确认层(`GET /auction[?date=]`,K8.md §二十)———————————
#
# 🔴 **三态与 `/market-regime` 刻意不同**(⛔ 别"统一"):行情状态是**日更只读表的区间
# 查询**(缺行 = 那天没批算,200 + `available=false` 更合适);竞价小报告是**冻结件
# 点查**,与 `basket_cards` 同族 → 照 B1 的三态办:
#   · 当日无行(竞价层没跑过 / 还没到 9:26)→ **404 `auction_not_ready`**;
#   · 有行但 json 解不出 → **500 `auction_corrupt`**(⛔ 不降格成 404:混成一类 =
#     客户端永远重试、永远显示"还没生成" = 静默永久失败);
#   · 有行(**含 `baskets_covered=0`**)→ **200** + `basketsUnavailableReason` 说出口。
# 🔴 「没跑」与「跑了没有」必须分得开(§七 P0-39 同款病),⛔ 不许混成一句
# 「今天没有竞价报告」。
#
# 🔴 **一律发枚举码,中文换算在客户端**(`verdict` / `clampedBy` / `dataQuality` /
# `planFit` / `risk.kind` 五族)—— CLAUDE.md 连踩三次的坑(`role · leader` /
# `pullback_leader` 都是把码直接印上界面造成的)。
#
# ⚠ `sectorSync` / `relStrength` / `history` / `planConsistency` 四段**原样透传**领域层
# 形状(同 `ReviewSegmentOut.detail` / `SelectionClockOut.mech` 既定惯例);它们来自
# `auction_verdicts` 的 json 列 = **B 类冻结快照** → 客户端 DTO 必须手写 `init(from:)`
# 做 `decodeIfPresent`(CLAUDE.md V2-⑮ 铁律)。

class AuctionQuoteCheckOut(BaseModel):
    """**一源**对一只代码的原始读数 + 七项校验结果(V2.4.0 P2.1/P2.2)。

    🔴 **两源都发**(K8 §二十:「两个来源的原始读数全部留存」)—— ⛔ 不许只发
    胜出的那一个:界面上要能回答「备源当时说的是什么」。"""

    role: str = ""                 # primary(新浪)| backup(腾讯)
    source: str = ""               # sina | tencent
    status: str = ""               # `auction.QUOTE_STATUSES` 之一
    errors: List[str] = Field(default_factory=list)
    tsRaw: str = ""
    tsParsed: Optional[str] = None
    price: Optional[float] = None
    preClose: Optional[float] = None
    open: Optional[float] = None
    volume: Optional[float] = None
    amount: Optional[float] = None


class AuctionQualityDetailOut(BaseModel):
    """逐票双源核验的一条账(V2.4.0 P2.2)。`freshness` ∈ `fresh|insufficient|conflict`。

    🔴 `sourceDegraded=true` = **主源不可用、本次用的是备源**(K8 §二十「记录来源
    降级」)—— ⛔ 不许静默换源。`conflict` 非空 = 两源给出**相反的结论**,
    ⛔ 不能高置信输出。

    🔴 **`crossVerified`(复审 🔴-2 新增)= 这一格真的做过两源对拍**。
    ⛔ **别拿 `conflict == null` 当"核验过了没冲突"**:只有一源、或有一源读数不合格时
    `conflict` **同样**是 `null`,那时的含义是「**没得比**」。判别式单一源在
    `auction/quality.py::_is_cross_verified`(与触发 `detect_conflict` 的那个 `if`
    是同一个函数);⚠ 老行没有这一键 → `False` = 保守的「没核验过」,⛔ 不在读侧重推。"""

    tsCode: str = ""
    freshness: str = ""
    status: str = ""
    chosenRole: Optional[str] = None
    chosenSource: Optional[str] = None
    sourceDegraded: bool = False
    conflict: Optional[str] = None
    crossVerified: bool = False
    errors: List[str] = Field(default_factory=list)
    checks: List[AuctionQuoteCheckOut] = Field(default_factory=list)


class AuctionDataStatusOut(BaseModel):
    """小报告第 1 块「数据状态」。`dataQuality` 三态是**结构性判据**(全有 / 全无 /
    其余),⛔ 不是百分比阈值。

    🔴 **V2.4.0 P2.2 起 `conflictCodes` 真的会有值**:`get_quotes_dual()` 双源批量
    核验已上线(净增 1 次 HTTP 请求 / 早晨)。V2.3.3 时代「结构性恒空、⛔ 不加第二次
    请求」的旧口径**已被 K8 §二十「有界双源核验」推翻**(§七 P4-66 改判)。

    🔴 **V2.4.0 P2.3 分域**:`criticalDataQuality` / `contextDataQuality` 为 `null`
    时**必须显示「旧版本未细分」**,⛔ 不得默认成正常(施工图 §五 P2.3 逐字)。
    ⚠ `dataQuality`(市场级)**仍是整体覆盖率读数**,刻意没有收窄成关键域 ——
    它不驱动任何夹逼闸,而「跑过了、D0 当天就没有 T1/T2 篮子」那一早本来就该判 ok。
    夹逼闸看的是**篮子级**的 `AuctionVerdictOut.criticalDataQuality`。"""

    source: str = "unknown"
    capturedAt: str = ""
    requestedCodes: int = 0
    fetchedCodes: int = 0
    missingCodes: List[str] = Field(default_factory=list)
    #: 🔴 V2.4.0 P2.1:抓到了、但七项校验没过的码。⛔ 与 `missingCodes` 分开 ——
    #: 「没抓到」与「抓到了一份昨天的」排障方向完全相反。
    invalidCodes: List[str] = Field(default_factory=list)
    conflictCodes: List[str] = Field(default_factory=list)
    dataQuality: str = "insufficient"
    criticalDataQuality: Optional[str] = None
    contextDataQuality: Optional[str] = None
    #: 逐票双源核验账。老报告没有这一列 → 空数组 + 两个分域字段为 `null`,
    #: 客户端据此说「旧版本未细分」。
    qualityDetails: List[AuctionQualityDetailOut] = Field(default_factory=list)
    #: 全域出现过的校验失败码(去重、确定性顺序)。空 = 全过**或**老报告没记。
    validationErrors: List[str] = Field(default_factory=list)


class AuctionIndexGapOut(BaseModel):
    tsCode: str
    name: str = ""
    gapPct: Optional[float] = None


class AuctionMarketOverviewOut(BaseModel):
    """小报告第 2 块「市场与主线概览」。`text` 为 `null` = **LLM 没给**(⛔ 不冒充
    "没内容"),配 `textUnavailableReason` 说出口。`anchors` = 竞价强势股 = **市场锚点**,
    K8 §二十:「只解释资金方向,**不取得交易资格**」。"""

    indexGaps: List[AuctionIndexGapOut] = Field(default_factory=list)
    anchors: List[AuctionIndexGapOut] = Field(default_factory=list)
    text: Optional[str] = None
    textUnavailableReason: Optional[str] = None
    anchorsNote: Optional[str] = None


class AuctionMemberRowOut(BaseModel):
    """逐票明细(键表 = §五 ②-F,与 `members_json` 逐字对应的 camelCase)。

    🔴 `hitInvalidation` / `gapUpDeviation` 是**三态**:`true` 命中 / `false` 看过了
    没命中 / `null` **没判**(锚失效 / 卡上无冻结价位 / 开盘价未发布 / 有篮无卡 /
    没抓到)。⛔ `null` 不是 `false`「没问题」—— 客户端必须把这两态讲成不同的话。
    ⚠ `null` 时**必配一个原因码**(`hitInvalidationUndeterminedReason` /
    `gapUpDeviationUndeterminedReason`,唯一源 `auction.UNDETERMINED_CODES`);
    老行(V2.3.3 复审整改**之前**冻的 `members_json`)可能只有 `null` 没有原因码 ——
    那时客户端照实说「没判(原因未记录)」,⛔ 仍不许说成「无异常」。"""

    tsCode: str
    name: str = ""
    role: Optional[str] = None
    auctionPrice: Optional[float] = None
    preClose: Optional[float] = None
    gapPct: Optional[float] = None
    auctionVolume: Optional[float] = None
    auctionAmount: Optional[float] = None
    volVsPrev5Frac: Optional[float] = None
    # 🔴 **两条独立路径**(用户裁定 P3-70,2026-08-12):`relToSector` 减的是**板块基准**
    # (板块指数 → 取不到 → ≥3 只同行业对照股中位数)、`relToIndex` 减的是**市场指数**
    # (沪主板 / 深主板 / 创业板 / 北证50;**科创板按 K8 §三 排除**)。⛔ 禁止同源同值。
    # 🔴 `null` = **没有这个读数**,⛔ 不是 0、⛔ 不是「持平」—— 必配一个 `*Reason` 原因码
    # (唯一源 `auction.REL_UNDETERMINED_CODES`),客户端必须画**第三态**。
    relToSector: Optional[float] = None
    relToIndex: Optional[float] = None
    relToSectorSource: str = "unavailable"
    relToSectorReason: Optional[str] = None
    sectorPeerCodes: List[str] = Field(default_factory=list)
    sectorIndexCode: Optional[str] = None
    sectorBenchmarkGapPct: Optional[float] = None
    industry: Optional[str] = None
    indexBenchmarkCode: Optional[str] = None
    indexBenchmarkGapPct: Optional[float] = None
    relToIndexReason: Optional[str] = None
    hitInvalidation: Optional[bool] = None
    gapUpDeviation: Optional[bool] = None
    hitInvalidationUndeterminedReason: Optional[str] = None
    gapUpDeviationUndeterminedReason: Optional[str] = None
    anchorStale: bool = False
    planFit: str = "unknown"
    dataQuality: str = "insufficient"
    volumeNote: Optional[str] = None
    # ── 🔴 V2.4.0 P2.1/P2.2:这条读数**从哪来、是不是今天的、两源打不打架** ──────
    # ⚠ 空串 / `null` = **老快照没记这一位**(V2.4.0 之前冻的 `members_json`),
    # ⛔ 客户端不许把它渲染成「校验通过」。
    quoteFreshness: str = ""            # fresh | insufficient | conflict
    quoteStatus: str = ""               # `auction.QUOTE_STATUSES` 之一
    quoteSource: Optional[str] = None   # sina | tencent;`null` = 两源都没读数
    quoteTimestamp: Optional[str] = None  # **源自带**的时刻(⚠ 不是 capturedAt)
    sourceDegraded: bool = False        # 主源不可用 → 本次用了备源(⛔ 不静默)
    sourceConflict: Optional[str] = None  # `auction.CONFLICT_CODES` 之一
    validationErrors: List[str] = Field(default_factory=list)


class AuctionVerdictOut(BaseModel):
    """小报告第 3 块「篮子与逐票结论」一篮一条。

    🔴 `verdictRaw`(夹逼**前**模型原话)与 `verdict`(夹逼后)**两者都发**:不同的
    那些行**就是**「模型说了什么 vs 系统最终讲了什么」的账,⛔ 不许只发一个。
    `clampedBy` 非空 = 被机械夹逼闸改过 —— 客户端必须说出口(⛔ 不许静默)。"""

    basketId: int
    basketKey: str = ""
    name: str = ""
    coveredTier: int = 0
    engineCode: Optional[str] = None
    engineVersion: Optional[str] = None
    skeletonVersion: str = ""
    regimeAtD0: Optional[str] = None
    #: 🔴 **V2.4.0 P2.3 起本字段 = 关键域质量**(含义收窄,施工图 §五 P2.3);
    #: V2.3.3 及更早的行里它是**整体**质量 —— 靠 `criticalDataQuality` 为 `null` 分辨。
    dataQuality: str = "insufficient"
    #: 🔴 `null` = **旧版本未细分**,⛔ 不得默认成正常(施工图 §五 P2.3 逐字)。
    criticalDataQuality: Optional[str] = None
    #: 上下文域质量。🔴 降级**只降置信度 + 披露缺失,⛔ 不夹逼结论**。
    contextDataQuality: Optional[str] = None
    #: 两域的逐项账(哪些码进了哪一域、各自缺了什么、冲突在哪)。原样透传领域层形状。
    qualityDetail: Dict[str, Any] = Field(default_factory=dict)
    verdict: str = "pending_explanation"
    verdictRaw: Optional[str] = None
    clampedBy: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)
    members: List[AuctionMemberRowOut] = Field(default_factory=list)
    sectorSync: Dict[str, Any] = Field(default_factory=dict)
    relStrength: Dict[str, Any] = Field(default_factory=dict)
    history: Dict[str, Any] = Field(default_factory=dict)
    planConsistency: Dict[str, Any] = Field(default_factory=dict)
    hitInvalidation: List[str] = Field(default_factory=list)
    manualNoteAttached: bool = False
    llmStage: str = ""


class AuctionRiskOut(BaseModel):
    """小报告第 4 块「异常与风险」一条。`kind` 是枚举码(唯一源
    `neckline/auction/__init__.py` 的 `RISK_*`),中文换算在客户端。"""

    kind: str = ""
    text: str = ""


class AuctionOut(BaseModel):
    """`GET /auction` 响应 = K8 §二十 的**竞价小报告五块**。

    🔴 `proxySampleNote` **恒发**(§五 ⑨-B-2):竞价强势股只在竞价层**自己的观察池**里
    排序,不是全市场竞价排行 —— 这句话必须印在小报告上,⛔ 不许只写在代码注释里。
    🔴 `observationScopeNote`(2026-08-12 用户裁定 ①)= **当天那一份**观察范围的自述
    (多少只、来自哪几层、题材层为什么缺席)。⚠ **空串 = 这份报告是 v2.4.0 之前落的**
    (那时还没有独立观察池这个概念)—— ⛔ 客户端不许把它读成「范围正常」或
    「范围就是全市场」;两句话分工是刻意的:上面那句恒定,这一句随天变。
    🔴 `manualNote` 只在**挂了**的时候发,文案本体是服务端常量
    (`auction.AUCTION_MANUAL_NOTE`,K8 §二十 逐字)—— ⛔ 客户端不许自己写这段字。
    🔴 `basketsUnavailableReason`:`baskets` 为空时**必须**说出口(〇b-6)——
    「跑过了、D0 当天就没有 T1/T2 篮子」与「竞价层根本没跑」(那是 404)是两件事。"""

    tradeDate: str
    d0Date: str = ""
    dataStatus: AuctionDataStatusOut = Field(default_factory=AuctionDataStatusOut)
    marketOverview: AuctionMarketOverviewOut = Field(default_factory=AuctionMarketOverviewOut)
    baskets: List[AuctionVerdictOut] = Field(default_factory=list)
    basketsUnavailableReason: Optional[str] = None
    risks: List[AuctionRiskOut] = Field(default_factory=list)
    manualNote: Optional[str] = None
    proxySampleNote: str = ""
    observationScopeNote: str = ""
    llmStage: str = ""
    notes: List[str] = Field(default_factory=list)


__all__ = [
    "OkOut",
    "InfoCardSnapshotOut", "InfoCardNewsItemOut", "InfoCardNewsOut", "InfoCardTopListOut",
    "NewsAlertOut", "NewsAlertScanStatusOut", "ReportOut",
    # V2-⑭-B 篮子族
    "BasketMemberOut", "BasketCardOut", "ScoreContribOut", "TierOut", "BasketOut", "BasketsListOut",
    "DroppedBasketOut", "OutCandidateOut",
    "BasketVerificationOut", "BasketReviewOut", "BasketDailyOut",
    # V2-⑭-B 计划继承 / 建仓快照 / 画像 / 策略包 / 评价
    "PositionPlanOut", "PositionPlansOut", "PositionPlanCreateIn", "EntrySnapshotOut",
    "ProfileOut", "PackOut", "PacksListOut", "EvalWeeklyOut",
    "RetreatBrakeOut", "BoardEventOut", "BoardOut", "K4AdvisoryOut", "PositionAlertOut",
    "PortfolioAlertOut",
    "PositionOut", "PositionsOut", "PositionOpenIn", "PositionOpenOut", "PositionCloseIn",
    "EntrySuggestionOut",
    "PushKindOut", "PushSettingsOut", "SettingsOut", "SettingsProviderOut", "SettingsPushIn", "DeviceRegisterIn",
    "ConfirmationCardOut", "CustomAlertOut", "AlertsListOut", "AlertConditionIn",
    "AlertCreateIn", "AlertUpdateIn", "AlertParseIn", "AlertParseOut",
    "ProviderOut", "ProvidersListOut", "ProviderCreateIn", "ProviderUpdateIn",
    "LLMRoutesOut", "LLMRoutesIn",
    "TavilySettingsOut", "TavilySettingsIn",
    "SettingsReviewColMapIn",
    "WeeklyReviewOut", "ReviewUploadOut", "ReviewGetOut",
    # V2.2-② 行情状态层
    "MarketRegimeDayOut", "MarketRegimeOut",
    # V2.3.3-⑤ D1 集合竞价确认层
    "AuctionDataStatusOut", "AuctionIndexGapOut", "AuctionMarketOverviewOut",
    "AuctionMemberRowOut", "AuctionVerdictOut", "AuctionRiskOut", "AuctionOut",
    "AuctionQuoteCheckOut", "AuctionQualityDetailOut",
    # V2.2-④ 双时钟
    "SelectionClockOut", "SelectionClocksOut", "TradeClockOut", "TradeClockEventOut",
    "TradeClockNoteIn", "TradeClockNoteOut",
    # V2.1-⑤ 复盘板块聚合读 + 校准移交件
    "ReviewSegmentOut", "ReviewOverviewOut", "ReviewHandoffOut",
    "ContingencyScenarioOut", "NoteLabelLiteral",
    "DecisionCreateIn", "DecisionNoteOut", "DecisionOut", "DecisionsListOut",
    "DecisionTrackOut", "DecisionTrackRowOut",
    "InfoCardKlineBarOut", "InfoCardIndexPointOut", "InfoCardK4FlagOut", "InfoCardMarketOut",
    "InfoCardMemberTagOut", "InfoCardBasketPeerOut", "InfoCardBasketOut", "InfoCardOut",
]
