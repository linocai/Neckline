//
//  Models.swift
//  Neckline — 客户端展示层数据模型
//
//  对齐后端 `neckline/api/schemas.py`(4A 契约,§五 阶段4C「逐字段对齐,别猜」)。
//  出参 camelCase 直接 Codable 解码(后端 pydantic 模型字段本身就是 camelCase,
//  默认 keyDecodingStrategy 不做任何转换);sentiment/sectors 两个内层快照是后端
//  `Dict[str,Any]` 透传(领域 dataclass `asdict()` 结果,字段 snake_case),用显式
//  CodingKeys 映射,不依赖 `.convertFromSnakeCase` 的隐式规则(数字开头分段行为
//  不透明,显式映射更稳)。
//
//  领域四条铁律(§2.1/§2.5,客户端只展示、不重算、不越权):
//   · 止损线 -5% 由服务端算好随 Position.stopLine 下发,客户端不再自派生。
//   · 问询台裁决只有两个字面值,枚举穷举、UI 不存在第三态或"买"按钮。
//   · 持仓开/清仓是审计台账动作(用户已在券商真实操作后来补录),客户端从不代下单、
//     不因退潮刹车而硬拦这个记录动作(阻拦=帮用户瞒报真实操作),只做醒目警示。
//   · 板块分类 / 涨跌停等领域计算全在服务端,客户端不重复实现。
//

import Foundation

// MARK: - 4A.2 报告:情绪仪表盘快照(SentimentDashboard,snake_case 透传)

struct SentimentSnapshot: Codable, Equatable {
    var tradeDate: String
    var limitUpCount: Int
    var limitDownCount: Int
    var zabanCount: Int
    var zabanRate: Double
    var maxConsecLimitUp: Int
    /// nil = 昨日无涨停股或数据缺失,非"溢价为0"(后端 docstring 原话,展示须区分)。
    var prevLimitUpPremiumAvg: Double?
    var prevLimitUpSample: Int
    var positionQuota: String       // "满额" | "半额" | "休息"
    var quotaReason: String

    enum CodingKeys: String, CodingKey {
        case tradeDate = "trade_date"
        case limitUpCount = "limit_up_count"
        case limitDownCount = "limit_down_count"
        case zabanCount = "zaban_count"
        case zabanRate = "zaban_rate"
        case maxConsecLimitUp = "max_consec_limit_up"
        case prevLimitUpPremiumAvg = "prev_limit_up_premium_avg"
        case prevLimitUpSample = "prev_limit_up_sample"
        case positionQuota = "position_quota"
        case quotaReason = "quota_reason"
    }
}

/// 仓位额度三态(唯一事实源 = 后端 `report/sentiment.py` 字面量,客户端只做穷举匹配作展示,
/// 不重新推导阈值)。未识别的字符串归 `.unknown`(前向兼容,不崩)。
enum PositionQuota: Equatable {
    case full, half, rest, unknown(String)

    init(_ raw: String) {
        switch raw {
        case "满额": self = .full
        case "半额": self = .half
        case "休息": self = .rest
        default: self = .unknown(raw)
        }
    }

    var label: String {
        switch self {
        case .full: return "满额"
        case .half: return "半额"
        case .rest: return "休息"
        case .unknown(let s): return s
        }
    }

    var tone: NKAxisTone {
        switch self {
        case .full: return .good
        case .half: return .warn
        case .rest: return .bad
        case .unknown: return .neutral
        }
    }
}

// MARK: - 4A.2 报告:强势板块快照(SectorScore,snake_case 透传)

struct SectorSnapshot: Codable, Equatable, Identifiable {
    var indexCode: String
    var name: String
    var boardAge: Int
    var ret20d: Double
    var bonus: Double
    var rank: Int

    var id: String { indexCode }

    enum CodingKeys: String, CodingKey {
        case indexCode = "index_code"
        case name
        case boardAge = "board_age"
        case ret20d = "ret_20d"
        case bonus
        case rank
    }
}

// MARK: - 4A.2 报告:候选四件套 + LLM 审判

struct LLMJudgment: Codable, Equatable {
    var verdict: String       // "通过" | "否决" | "未激活"
    var narrative: String
    var degraded: Bool
}

/// 板块英文码 → 中文展示名(唯一展示层换算源,`Candidate`/`WatchlistCheckItem` 共用
/// 同一份映射,不各自重复一份;未识别值原样透传,不静默瞎翻译)。
func nkBoardLabel(_ raw: String) -> String {
    switch raw {
    case "MAIN": return "主板"
    case "GEM": return "创业板"
    case "STAR": return "科创板"
    case "BSE": return "北交所"
    default: return raw
    }
}

/// 买点条件(结构化,§五 v1.1-E.2「一键补录预填候选买点价」的取值来源)。字段对齐
/// 服务端 `report/candidates.py::entry_spec`——只做「读哪个字段」的展示层选择
/// (pullback→ma10,breakout→platformHigh),不新推导任何数字,与 `boardLabel` 同一
/// 类展示层换算先例。`Candidate`/`WatchlistCheckItem` 同码生成,形状一致,共用本类型。
struct EntrySpec: Codable, Equatable {
    var buypoint: String?
    var ma10: Double?
    var platformHigh: Double?

    enum CodingKeys: String, CodingKey {
        case buypoint
        case ma10
        case platformHigh = "platform_high"
    }

    /// 买点参考价(展示层选择,详见类型注释)。两个字段都缺失(哨兵尚未算出 / 数据缺)
    /// → nil,UI 须留手填空位,不虚构数字。
    var referencePrice: Double? {
        switch buypoint {
        case "breakout": return platformHigh ?? ma10
        default: return ma10 ?? platformHigh
        }
    }
}

/// 五常驻板块诊断漏斗(v1.3-③-C3/⑥,§2.3)。**报告级构件,非本票专属**——每只候选携带
/// 同一份完整列表(服务端设计:0 保底板块自身无候选可挂,状态挂在所有候选的
/// `intelRank.permanentBoardStatus` 上),客户端取任一候选(通常首只)读出展示即可。
/// 0 只/不足 2 只时 `note` 必须说清「为什么」——守「『没有』和『没看』必须能分开」原则,
/// **静默空白是禁止的**,UI 不得因这份列表为空就什么都不画。
struct PermanentBoardStatus: Codable, Equatable, Identifiable {
    var board: String
    var surviveCount: Int
    var industryGatePass: Int
    var industryGateBlocked: Int
    var hardCutBlocked: Int
    var quotaFilled: Int
    var note: String

    var id: String { board }
}

/// 候选情报排序理由(v1.3-③-C3/⑥,§2.3 语义变更;v1.4-③ 起补三级排序键,需求 8)。
/// 候选=「过完安检、值得关注的票」非「会涨的票」——客户端据此写对文案,不写成正面
/// 买入暗示,展示情报维度而非回测信号。**排序 = 注意力优先级,不是收益预测;
/// 排第一 ≠ 最会涨,终选权在用户。**
struct IntelRank: Codable, Equatable {
    var sectorFlow: Double? = nil          // 所属常驻/暴起板块最大净流入(万元,C2;并列展示,不参与排序,无数据=nil)
    var themePersistDays: Int = 0          // 题材持续天数(反用:1天新鲜>2-3天警惕;≥4已在③剔;与 industryPersistDays 同源同值,旧字段名保留)
    var highElasticity: Bool = false       // 高弹板块(GEM/STAR;生成域刻意含高弹,标注给人判)
    var source: String = ""                // quota(常驻保底)| competition(情报竞争)| forced(问询强制);旧报告空串
    var industry: String = ""              // 该票行业(过行业闸后的代表行业),说清「凭什么在这个板块栏」
    var permanentBoardStatus: [PermanentBoardStatus] = []
    // —— v1.4-③ 排序键三级原样透出(`intel_candidates._sort_key`,需求 8)——————————————
    var industryRank: Int? = nil           // 排序键①:行业强度当日排名(1=最强)。nil=未参与排名
                                            // (无 industry/成员<5),**展示不得当 0**(0 会误读成"最强")
    var industryPersistDays: Int = 0       // 排序键②:行业强度持续天数(升序,第1天最新鲜)
    var yellowCardCount: Int = 0           // 排序键③:K4 avoid_flag 命中数(升序,无牌靠前;
                                            // 「无牌靠前」只是风险优先排序,无牌 ≠ 会涨)

    /// 显式 CodingKeys + 手写 `init(from:)`(容忍旧报告快照 / 手工 fixture 缺 v1.4-③ 三新键
    /// ——同 `Candidate`/`Position` 的处理姿势,新增非 Optional 字段不能指望 Swift 合成
    /// Decodable 用默认值兜底缺键)。
    enum CodingKeys: String, CodingKey {
        case sectorFlow, themePersistDays, highElasticity, source, industry, permanentBoardStatus
        case industryRank, industryPersistDays, yellowCardCount
    }

    init(sectorFlow: Double? = nil, themePersistDays: Int = 0, highElasticity: Bool = false,
         source: String = "", industry: String = "", permanentBoardStatus: [PermanentBoardStatus] = [],
         industryRank: Int? = nil, industryPersistDays: Int = 0, yellowCardCount: Int = 0) {
        self.sectorFlow = sectorFlow
        self.themePersistDays = themePersistDays
        self.highElasticity = highElasticity
        self.source = source
        self.industry = industry
        self.permanentBoardStatus = permanentBoardStatus
        self.industryRank = industryRank
        self.industryPersistDays = industryPersistDays
        self.yellowCardCount = yellowCardCount
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sectorFlow = try c.decodeIfPresent(Double.self, forKey: .sectorFlow)
        themePersistDays = try c.decodeIfPresent(Int.self, forKey: .themePersistDays) ?? 0
        highElasticity = try c.decodeIfPresent(Bool.self, forKey: .highElasticity) ?? false
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? ""
        industry = try c.decodeIfPresent(String.self, forKey: .industry) ?? ""
        permanentBoardStatus = try c.decodeIfPresent([PermanentBoardStatus].self, forKey: .permanentBoardStatus) ?? []
        industryRank = try c.decodeIfPresent(Int.self, forKey: .industryRank)
        industryPersistDays = try c.decodeIfPresent(Int.self, forKey: .industryPersistDays) ?? 0
        yellowCardCount = try c.decodeIfPresent(Int.self, forKey: .yellowCardCount) ?? 0
    }
}

/// 候选入选来源展示层换算(沿 `nkBoardLabel` 先例,未识别值原样透传)。
func nkIntelSourceLabel(_ raw: String) -> String {
    switch raw {
    case "quota": return "常驻保底"
    case "competition": return "情报竞争"
    case "forced": return "问询强制纳入"
    default: return raw
    }
}

/// K4 红黄牌分区展示层换算(v1.4-④,`InfoCardK4Flag.section`)——
/// hard_cut=红牌(会拦出候选池)、avoid_flag=黄牌(打标保留,只提醒)。
/// 沿 `nkBoardLabel` 先例,未识别值原样透传,不静默瞎翻译。
func nkK4SectionLabel(_ raw: String) -> String {
    switch raw {
    case "hard_cut": return "红牌"
    case "avoid_flag": return "黄牌"
    default: return raw
    }
}

func nkK4SectionTone(_ raw: String) -> NKAxisTone {
    switch raw {
    case "hard_cut": return .bad
    case "avoid_flag": return .warn
    default: return .neutral
    }
}

// MARK: - v1.4-④ 信息卡摘要(挂 `Candidate.infoCard`,不含 60 日序列,§五 v1.4-④-B)
//
// 服务端重构后恒是完整对象(pydantic 默认值兜底 + 全量序列化,§五-④-C「数据不可得
// 如实缺省」由服务端保证);这里用普通 `Codable`(不写容错 init),同 `K4Advisory`/
// `RetraceState` 等 v1.2+ 新增类型的先例——旧报告快照缺整个 `infoCard` 键时,由
// `Candidate.init(from:)` 的 `decodeIfPresent` 兜成 `nil`,不深入本类型内部兜底。

struct InfoCardSnapshot: Codable, Equatable {
    var volRatio5: Double? = nil
    var turnoverRate: Double? = nil
    var industryRank: Int? = nil            // ② 行业强度当日排名(1=最强);nil=未参与排名,不当 0
    /// ② 行业强度持续天数。**`nil` ≠ 0**(v1.4-⑩-E):`nil` = 行业强度表当日无数据
    /// (「没看」);`0` = 评了、不是强度日(「看了,没有」)。UI 据此显示「不可用」而非「0 天」。
    var industryPersistDays: Int? = nil
    var aboveMa250: Bool? = nil             // ma250 未就绪(<250 交易日历史)→ nil,不当"年线下"
    var distFromMa250Pct: Double? = nil     // 小数(非百分数),如 0.05 = 高于年线 5%
    var distFromHigh20dPct: Double? = nil
    var consecLimitUpDays: Int = 0
}

struct InfoCardNewsItem: Codable, Equatable, Identifiable {
    var category: String    // REDUCTION | INVESTIGATION | BLOWUP | REGULATORY(同 NewsAlert.category)
    var summary: String
    var source: String

    var id: String { "\(category)|\(summary)" }
    var categoryLabel: String { nkNewsCategoryLabel(category) }
}

/// 消息面摘要。「没扫到」(不在扫描域)与「扫了没有」必须能区分(同 `NewsAlertScanStatus`
/// 一贯原则)——`scanned=false` 时 `unavailableReason` 必有值,`items` 恒空数组不代表
/// "确认无消息"。
struct InfoCardNews: Codable, Equatable {
    var scanned: Bool
    var items: [InfoCardNewsItem] = []
    var unavailableReason: String? = nil
}

/// 龙虎榜摘要。`lookbackDaysCovered`(近 5 个交易日里本地已落盘、真能判定的天数,≤5)
/// 诚实反映"查了几天"——**不为凑齐而回补历史**,`lookbackDaysCovered<5` 不代表"其余
/// 天数确认未上榜",只代表"没查到那几天"。
struct InfoCardTopList: Codable, Equatable {
    var onListToday: Bool = false
    var reason: String? = nil
    var netAmount: Double? = nil
    var netRate: Double? = nil
    var lookbackDaysCovered: Int = 0
    var lookbackHitDays: Int = 0
}

struct InfoCardSummary: Codable, Equatable {
    var snapshot: InfoCardSnapshot = InfoCardSnapshot()
    var mildBand: Bool = false
    var news: InfoCardNews = InfoCardNews(scanned: false)
    var topList: InfoCardTopList = InfoCardTopList()
}

/// 执行提示单条(v1.4-⑤-A,需求 8 末段)。**语义红线**:回答"如果你决定动手,怎么
/// 执行更不吃亏",不是"该不该买"——`text` 原样透传服务端文字(DB `k4_advisory.
/// exec_hint` 原文或缺读时的模块兜底),客户端不改写、不加"建议"字样。展示标题统一
/// 「执行提示」。
struct ExecHint: Codable, Equatable, Identifiable {
    var code: String        // advisory 码(C1_strong_market_order 等四选一)
    var text: String        // 展示文字(服务端原文)
    var source: String      // db | fallback(诚实展示文字来源)

    var id: String { code }
}

struct Candidate: Codable, Equatable, Identifiable {
    var rank: Int
    var code: String
    var name: String
    var score: Double
    var board: String                 // 主板/创业板/科创板/北交所(股票板块分类,非本页"看板")
    // 四件套(§2.2/§2.3):买点 / 止损(-5%) / 目标 / 证伪条件 —— 全部自由文本,不在客户端重排模板卡
    var buyPoint: String
    var stop: String
    var target: String
    var invalidation: String
    var formTags: [String]
    var hotSectors: [String]
    var sectorNames: [String]
    var llmJudgment: LLMJudgment?      // 仅前 10 只有(nil = 未过 LLM 审判,非降级)
    /// 买点结构化条件(§五 v1.1-E.2 一键补录预填用)。服务端字段恒是一个对象(可能
    /// 内部字段皆缺),故用可选类型兜住任何缺失/旧报告没有这个键的情形,不崩。
    var entrySpec: EntrySpec? = nil
    /// v1.3-③-C3/⑥:K4 avoid_flag 命中码(打标保留;hard_cut 已在服务端拦截出池、不会
    /// 出现在候选里)+ 情报排序理由。均是**非 Optional 但要容忍缺键**的字段(真实后端
    /// 恒会发,只有本文件里较早写的手工 JSON fixture 可能没有这两键)——Swift 合成
    /// Decodable 对非 Optional 属性不会自动容忍缺键(即便声明了默认值,那只影响
    /// memberwise init,不影响解码),故本类型改手写 `init(from:)` 显式 `decodeIfPresent`
    /// 兜底,换来「旧 fixture / 旧报告快照缺这两键也不崩、直接给默认空值」。
    var k4Flags: [String] = []
    var intelRank: IntelRank = IntelRank()
    /// v1.4-④-B:信息卡摘要(不含 60 日序列,供列表页直接展示)。`nil` = 老报告快照
    /// (建于本字段前)或该次生成异常降级,**不冒充"确认无内容"**——客户端按"该信息
    /// 暂不可用"处理。完整信息卡(60 日 K 线/RS 线/行业分歧线)另走
    /// `GET /report/{date}/info-card/{code}`。
    var infoCard: InfoCardSummary? = nil
    /// v1.4-⑤-A:执行提示(读 DB `k4_advisory.exec_hint`)。0~4 条,老报告快照读回默认空。
    var execHints: [ExecHint] = []

    /// 显式 `CodingKeys`(提供自定义 `init(from:)` 时不依赖合成时机是否可靠——同
    /// `ReportResponse`/`Position` 的处理姿势)。字段名与 JSON 字面一致,逐一列出。
    enum CodingKeys: String, CodingKey {
        case rank, code, name, score, board, buyPoint, stop, target, invalidation
        case formTags, hotSectors, sectorNames, llmJudgment, entrySpec, k4Flags, intelRank
        case infoCard, execHints
    }

    var id: String { code }

    /// `board` 服务端字面实测是英文枚举码("MAIN"/"GEM"/"STAR"/"BSE",唯一源
    /// `neckline/data/board.py` 的 `Board` 枚举,§3.2.7/CLAUDE.md「板块分类唯一源」),
    /// 不是中文名。这里只做**展示层换算四个已知常量**,不改判定、不猜测新分类
    /// (未识别值原样透传,不静默瞎翻译——万一后端枚举新增值,界面照样不崩、只是显英文)。
    var boardLabel: String { nkBoardLabel(board) }

    init(rank: Int, code: String, name: String, score: Double, board: String,
         buyPoint: String, stop: String, target: String, invalidation: String,
         formTags: [String], hotSectors: [String], sectorNames: [String],
         llmJudgment: LLMJudgment?, entrySpec: EntrySpec? = nil,
         k4Flags: [String] = [], intelRank: IntelRank = IntelRank(),
         infoCard: InfoCardSummary? = nil, execHints: [ExecHint] = []) {
        self.rank = rank; self.code = code; self.name = name; self.score = score; self.board = board
        self.buyPoint = buyPoint; self.stop = stop; self.target = target; self.invalidation = invalidation
        self.formTags = formTags; self.hotSectors = hotSectors; self.sectorNames = sectorNames
        self.llmJudgment = llmJudgment; self.entrySpec = entrySpec
        self.k4Flags = k4Flags; self.intelRank = intelRank
        self.infoCard = infoCard; self.execHints = execHints
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        rank = try c.decode(Int.self, forKey: .rank)
        code = try c.decode(String.self, forKey: .code)
        name = try c.decode(String.self, forKey: .name)
        score = try c.decode(Double.self, forKey: .score)
        board = try c.decode(String.self, forKey: .board)
        buyPoint = try c.decode(String.self, forKey: .buyPoint)
        stop = try c.decode(String.self, forKey: .stop)
        target = try c.decode(String.self, forKey: .target)
        invalidation = try c.decode(String.self, forKey: .invalidation)
        formTags = try c.decode([String].self, forKey: .formTags)
        hotSectors = try c.decode([String].self, forKey: .hotSectors)
        sectorNames = try c.decode([String].self, forKey: .sectorNames)
        llmJudgment = try c.decodeIfPresent(LLMJudgment.self, forKey: .llmJudgment)
        entrySpec = try c.decodeIfPresent(EntrySpec.self, forKey: .entrySpec)
        k4Flags = try c.decodeIfPresent([String].self, forKey: .k4Flags) ?? []
        intelRank = try c.decodeIfPresent(IntelRank.self, forKey: .intelRank) ?? IntelRank()
        infoCard = try c.decodeIfPresent(InfoCardSummary.self, forKey: .infoCard)
        execHints = try c.decodeIfPresent([ExecHint].self, forKey: .execHints) ?? []
    }
}

// MARK: - v1.4-④ 信息卡(完整,`GET /report/{date}/info-card/{code}` 专用,§五 v1.4-④)
//
// 摘要位共用的 `InfoCardSnapshot`/`InfoCardNews`/`InfoCardTopList` 已在上面声明(挂
// `Candidate.infoCard`);这里只补 60 日序列 + 红黄牌明细专属类型。**第〇原则(考卷
// 同构)**:数据不可得如实缺省,禁止硬凑——每一路数据源独立 `*Available`/
// `*UnavailableReason`,任何一路缺失都不得连带其余各路"看起来也不可用"。
// **execHints 待核对假设(⑤留)**:本类型刻意不含 `execHints`——信息卡页复用候选对象
// 自带的 `Candidate.execHints`(同一份数据,不重复开字段/不重复请求)。

struct InfoCardKlineBar: Codable, Equatable, Identifiable {
    var tradeDate: String
    var open: Double
    var high: Double
    var low: Double
    var close: Double
    var vol: Double
    var ma20: Double? = nil      // 早期行(历史不足窗口)→ nil,不是"均线为 0"
    var ma250: Double? = nil

    var id: String { tradeDate }
    var isUp: Bool { close >= open }
}

/// RS 线 / 行业分歧线 / 大盘指数化线共用的一个点(起点归一 100)。
struct InfoCardIndexPoint: Codable, Equatable, Identifiable {
    var tradeDate: String
    var value: Double

    var id: String { tradeDate }
}

/// 红黄牌明细。`section`:hard_cut(红牌)| avoid_flag(黄牌)——展示层换算见
/// `nkK4SectionLabel`/`nkK4SectionTone`,同 `board`/`NewsCategory` 惯例服务端不存中文。
struct InfoCardK4Flag: Codable, Equatable, Identifiable {
    var code: String
    var label: String
    var level: String              // strong | normal
    var section: String            // hard_cut | avoid_flag
    var evidenceStrength: String   // price_volume | constituent
    var evidence: String

    var id: String { code }
    var sectionLabel: String { nkK4SectionLabel(section) }
    var sectionTone: NKAxisTone { nkK4SectionTone(section) }
}

/// 市场语境(报告级构件,考卷 §三.8 同构位——大盘 60 日指数化形态 + 当日涨跌停家数 +
/// 大盘 MA20 上下)。
struct InfoCardMarket: Codable, Equatable {
    var indexCode: String = "000001.SH"
    var indexLine: [InfoCardIndexPoint] = []
    var limitUpCount: Int = 0
    var limitDownCount: Int = 0
    var aboveMa20: Bool? = nil
}

struct InfoCard: Codable, Equatable {
    var code: String
    var name: String
    var tradeDate: String
    var klineAvailable: Bool
    var kline: [InfoCardKlineBar] = []
    var klineUnavailableReason: String? = nil
    var rsAvailable: Bool = false
    var rsLine: [InfoCardIndexPoint] = []
    var rsBenchmark: String = "000001.SH"
    var rsUnavailableReason: String? = nil
    var industryDivergenceAvailable: Bool = false
    var industryDivergenceLine: [InfoCardIndexPoint] = []
    var industry: String = ""
    var industryDivergenceNote: String = "行业线=行业成员中位数合成,非申万官方指数"
    var industryDivergenceUnavailableReason: String? = nil
    var snapshot: InfoCardSnapshot = InfoCardSnapshot()
    var k4Flags: [InfoCardK4Flag] = []
    var mildBand: Bool = false
    var news: InfoCardNews = InfoCardNews(scanned: false)
    var topList: InfoCardTopList = InfoCardTopList()
    var market: InfoCardMarket = InfoCardMarket()
}

// MARK: - 4A.2 报告:整份报告

// MARK: - v1.3-③-C1 复盘情报件(ReportOut.intel;服务端 `Dict[str,Any]` 透传,§2.3)
//
// 后端 `report/intel.py::IntelReport.to_public_dict()` 是这份数据的唯一形状源(同
// `sentiment`/`sectors` 的透传惯例);客户端仍按已知稳定形状声明强类型 Codable
// (同 `ReviewWeeklyResult` 先例)。**证据强度标注**:题材/成分类字段
// (`evidenceStrength`)依赖概念板块成分(K2「成分洞」)标「参考」;涨跌幅/涨停梯队/
// 跌停榜/大盘量能/市值偏好/涨跌停制度偏好均为 EOD 硬数据(强证据)。

struct IntelMover: Codable, Equatable, Identifiable {
    var code: String
    var name: String
    var pctChg: Double
    var close: Double
    var id: String { code }
}

struct IntelLimitLadderRung: Codable, Equatable, Identifiable {
    var consecDays: Int
    var count: Int
    var id: Int { consecDays }
}

struct IntelMarketVolume: Codable, Equatable {
    var shAmountYi: Double
    var szAmountYi: Double
    var totalAmountYi: Double
    var ma5AmountYi: Double
    var sampleDays: Int   // <5 时样本不足,§硬要求诚实标注(UI 据此加注"样本仅 N 日")
}

struct IntelThemeLeader: Codable, Equatable, Identifiable {
    var code: String
    var name: String
    var pctChg: Double
    var isLimitUp: Bool
    var id: String { code }
}

struct IntelThemeItem: Codable, Equatable, Identifiable {
    var code: String
    var name: String
    var boardAge: Int
    var ret20d: Double
    var persistenceLabel: String     // 服务端已是中文文案(未站上MA20/新起1日/持续2-3日/已延续≥4日)
    var evidenceStrength: String     // 恒 constituent(成分依赖,弱证据),同 K4Advisory 词表
    var leaders: [IntelThemeLeader]
    var id: String { code }
}

struct IntelBucketCount: Codable, Equatable, Identifiable {
    var label: String
    var count: Int
    var pctOfTotal: Double
    var id: String { label }
}

struct IntelSection: Codable, Equatable {
    var tradeDate: String
    var evidenceNote: String
    var gainers: [IntelMover]
    var losers: [IntelMover]
    var limitUpLadder: [IntelLimitLadderRung]
    var limitDown: [IntelMover]
    var limitDownTotalCount: Int      // 跌停榜展示有截断上限,这里是真实总数(截断不撒谎)
    var marketVolume: IntelMarketVolume?
    var topThemes: [IntelThemeItem]
    var themePersistenceDistribution: [String: Int]
    var mvPreference: [IntelBucketCount]
    var limitRegimePreference: [IntelBucketCount]
    var excludedBoardsNote: String
    var warnings: [String]

    /// 全空 = 这份报告快照压根没有情报节(旧报告 / 尚未生成),UI 据此展示"暂无"而非
    /// 空白卡片(§硬要求「没有 vs 没看」分开)。
    var hasContent: Bool {
        !(gainers.isEmpty && losers.isEmpty && limitUpLadder.isEmpty && limitDown.isEmpty
            && marketVolume == nil && topThemes.isEmpty)
    }
}

// MARK: - v1.3-③-C2 板块资金流(ReportOut.sectorMoneyflow)
//
// **定位写死(硬要求,不可当选股信号)**:拥挤情报件,STRATEGY_LAB K2 判决板块层有效
// 但无次日领先性——展示文案不得暗示"买入依据"。

struct SectorMoneyflowItem: Codable, Equatable, Identifiable {
    var code: String
    var name: String
    var netInflowWan: Double     // 万元,东财 moneyflow_dc 口径
    var memberCount: Int
    var rank: Int
    var evidenceStrength: String   // 恒 constituent(板块归属依赖成分快照,弱证据)
    var id: String { code }
}

struct SectorMoneyflowSection: Codable, Equatable {
    var tradeDate: String
    var available: Bool
    var unavailableReason: String   // available=false 时必读(2023-09 前无数据 / 当日缺失等)
    var topInflow: [SectorMoneyflowItem]
    var topOutflow: [SectorMoneyflowItem]
    var excludedBoardsNote: String
    var evidenceNote: String
}

// MARK: - v1.3-③-C4 消息面(ReportOut.newsAlerts + newsAlertsScan,§硬要求「没扫到 vs
// 扫了没有必须能区分」)

/// 消息面命中告警。`category` 服务端码(REDUCTION/INVESTIGATION/BLOWUP/REGULATORY),
/// 展示层中文换算见 `nkNewsCategoryLabel`(沿 `boardLabel` 先例,未识别原样透传)。
struct NewsAlert: Codable, Equatable, Identifiable {
    var code: String
    var name: String
    var category: String
    var summary: String
    var source: String   // tushare_holdertrade | llm_<provider>

    var id: String { "\(code)|\(category)|\(summary)" }
    var categoryLabel: String { nkNewsCategoryLabel(category) }
}

func nkNewsCategoryLabel(_ raw: String) -> String {
    switch raw {
    case "REDUCTION": return "减持"
    case "INVESTIGATION": return "立案"
    case "BLOWUP": return "暴雷"
    case "REGULATORY": return "监管"
    default: return raw
    }
}

/// 消息面扫描状态——**必须先读这个再展示 `newsAlerts`**,不能只看后者是否为空就下结论
/// (空数组本身无法表达"这次到底扫没扫、扫没扫完")。`codesSkipped`(墙钟预算耗尽、根本
/// 没发起调用就跳过)/ `codesFailed`(调用了但失败)/ `codesNoSearch`(调用成功但联网
/// 搜索命中 0 条,结论未经搜索证实)/ `codesRotationDeferred`(v1.4-⑥-B 自选隔日轮扫、
/// 本日轮空)**四者语义各不相同,必须分开展示,不许合并成一个"没扫到"数字**。
struct NewsAlertScanStatus: Codable, Equatable, Identifiable {
    var source: String       // tushare_holdertrade | llm
    var scanned: Bool
    var reason: String = ""
    var codesTotal: Int = 0
    var codesFailed: Int = 0
    var codesSkipped: Int = 0
    var codesNoSearch: Int = 0            // v1.3.4:调用成功但联网搜索命中 0 条的标的数
    // v1.4-⑥-B:自选隔日轮扫披露。`rotationGroup` = 本次扫的自选组("A"/"B",持仓每日
    // 必扫、不参与轮扫);`codesRotationDeferred` = 本日**轮空**(压根没进本次名单)的
    // 自选数。老报告快照没有这两个键 → 缺省 ""/0,前向兼容不崩。
    var rotationGroup: String = ""
    var codesRotationDeferred: Int = 0

    /// 显式 CodingKeys + 容错 `init(from:)`(本类型历经 v1.3-③-C4→v1.3.4→v1.4-⑥-B 三次
    /// 加字段,旧报告快照 / 手工 fixture 缺新键是常态——同 `IntelRank` 的处理姿势,不必
    /// 逐个改旧测试 fixture)。
    enum CodingKeys: String, CodingKey {
        case source, scanned, reason, codesTotal, codesFailed, codesSkipped, codesNoSearch
        case rotationGroup, codesRotationDeferred
    }

    init(source: String, scanned: Bool, reason: String = "", codesTotal: Int = 0, codesFailed: Int = 0,
         codesSkipped: Int = 0, codesNoSearch: Int = 0, rotationGroup: String = "",
         codesRotationDeferred: Int = 0) {
        self.source = source
        self.scanned = scanned
        self.reason = reason
        self.codesTotal = codesTotal
        self.codesFailed = codesFailed
        self.codesSkipped = codesSkipped
        self.codesNoSearch = codesNoSearch
        self.rotationGroup = rotationGroup
        self.codesRotationDeferred = codesRotationDeferred
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? ""
        scanned = try c.decodeIfPresent(Bool.self, forKey: .scanned) ?? false
        reason = try c.decodeIfPresent(String.self, forKey: .reason) ?? ""
        codesTotal = try c.decodeIfPresent(Int.self, forKey: .codesTotal) ?? 0
        codesFailed = try c.decodeIfPresent(Int.self, forKey: .codesFailed) ?? 0
        codesSkipped = try c.decodeIfPresent(Int.self, forKey: .codesSkipped) ?? 0
        codesNoSearch = try c.decodeIfPresent(Int.self, forKey: .codesNoSearch) ?? 0
        rotationGroup = try c.decodeIfPresent(String.self, forKey: .rotationGroup) ?? ""
        codesRotationDeferred = try c.decodeIfPresent(Int.self, forKey: .codesRotationDeferred) ?? 0
    }

    var id: String { source }
    var sourceLabel: String {
        switch source {
        case "tushare_holdertrade": return "减持(股东增减持,结构化数据)"
        case "llm": return "立案 / 暴雷 / 监管(LLM 联网核实)"
        default: return source
        }
    }
}

/// 数据新鲜度(v1.4-①-C 板块三键 + v1.4-⑩-F 行业强度三键;§七 P0-3 / P0-23)。
/// `sectorLagDays=-1` = 板块数据完全缺失(哨兵值,服务端
/// `report/sectors.py::SECTOR_LAG_UNKNOWN`,刻意不用 0——0 是"新鲜")。
/// `stale=true` 时「当日暴起板块」与「题材持续天数」本日不可信,须显式标注。
///
/// **⚠ `stale` 只表板块数据,一个字没改**;行业强度未就绪是**另一件独立故障**,走下面
/// 三个键。两者**不许合并成一个 bool** —— 合并就分不清哪个坏了(服务端同样并列存放)。
///
/// 三个行业强度键是 `Optional`:老报告快照(建于本字段前)没有这三键 → `nil` 兜底不崩。
/// 之所以能这么偷懒:`dataFreshness` 属于「`_shape_report` 每次响应**重新构造**」那一类
/// (**不是** `reviews.result_json` 那种写入时冻住的历史快照,后者新增字段必须手写
/// `init(from:)` 做 `decodeIfPresent` 兜底,见项目 CLAUDE.md 该条)。
struct DataFreshness: Codable, Equatable {
    var sectorDataDate: String?
    var sectorLagDays: Int
    var stale: Bool
    /// 行业强度预计算表(`industry_strength_daily`)库内最新日;`nil` = 完全无数据 / 老快照缺键。
    var industryStrengthDate: String? = nil
    /// 落后几个交易日;`-1` = 完全无数据(哨兵值,同 `sectorLagDays` 惯例)。
    var industryStrengthLagDays: Int? = nil
    /// `lag > 0` 即 true(**无容忍度** —— 行业强度用当日 EOD 算,16:05 当天就该有)。
    var industryStrengthStale: Bool? = nil

    /// 顶部横幅是否该出现:板块过期**或**行业强度未就绪,任一成立即展示(两条各自成行)。
    var needsBanner: Bool { stale || industryStrengthStale == true }
}

struct ReportSnapshot: Codable, Equatable {
    var tradeDate: String
    var generatedAt: String
    var strategyVersion: String
    var sentiment: SentimentSnapshot?
    var sectors: [SectorSnapshot]
    var candidates: [Candidate]
    var degraded: Bool
    var reason: String
    /// §五 v1.1-B.4 漏录兜底:当日买点哨兵触发过但台账无补录时的一句提示,否则空串
    /// (服务端实时算,用户补录后自动消失;E.3 据此在今日计划顶部展示提示条)。
    var missedEntryHint: String = ""
    // —— v1.3-③-C1/C2/C4「情报」板块(§五 v1.3-⑥-F)——————————————————————————————
    /// nil = 该报告快照没有情报节(旧报告 / 降级态,后端 `intel` 落空字典 `{}`,解码
    /// 阶段 `try?` 兜成 nil,见 `APIClient.ReportResponse`)。
    var intel: IntelSection? = nil
    var sectorMoneyflow: SectorMoneyflowSection? = nil
    var newsAlerts: [NewsAlert] = []
    var newsAlertsScan: [NewsAlertScanStatus] = []
    /// v1.4-①-C:板块数据新鲜度(§七 P0-3)。`nil` = 老报告(建于本字段前)或空对象
    /// (解码阶段同 `intel`/`sectorMoneyflow` 用 `try?` 归一),客户端按"该版本还没有
    /// 新鲜度概念"处理,不当"新鲜"展示。
    var dataFreshness: DataFreshness? = nil

    /// 空态占位(无报告 / 拉取失败),UI 据 `degraded`+`reason` 诚实展示,不假装有数据。
    static func empty(reason: String) -> ReportSnapshot {
        ReportSnapshot(tradeDate: "", generatedAt: "", strategyVersion: "",
                       sentiment: nil, sectors: [], candidates: [], degraded: true, reason: reason)
    }
}

// MARK: - 4A.3 盘中看板

struct RetreatBrake: Codable, Equatable {
    var active: Bool
    var reason: String
}

/// 哨兵事件中文标签,后端 `_SENTINEL_LABEL` 唯一源(客户端不重译)。v1.1-G.3 补
/// `precall`/`d5exit` 两枚举(盘前校准 / D5 时间退出,标签字面见 `api/app.py::_SENTINEL_LABEL`)。
enum SentinelKind: String, Codable {
    case entry = "买点"
    case invalidation = "证伪"
    case holding = "持仓"
    case precall = "盘前校准"
    case d5exit = "D5退出"
}

struct BoardEvent: Codable, Equatable, Identifiable {
    var sentinel: String     // 买点 | 证伪 | 持仓(见 SentinelKind;未识别值原样展示,不崩)
    var code: String
    var name: String
    var eventKey: String
    var verdict: String      // 判决文案(哨兵已落库的 reason 文本,自然语言,不是模板卡)
    var ts: String

    // id 必须含 code:eventKey 是判定类型名(gap_up_invalidate 等),跨股票共用,
    // 单用它做 ForEach 身份会 id 撞车 → 全列表渲染成第一只票的内容(实机踩过)。
    var id: String { "\(code)|\(eventKey)|\(ts)" }
    var kind: SentinelKind? { SentinelKind(rawValue: sentinel) }
}

struct BoardSnapshot: Codable, Equatable {
    var tradeDate: String
    var asof: String
    var retreatBrake: RetreatBrake
    var events: [BoardEvent]

    static let empty = BoardSnapshot(tradeDate: "", asof: "",
                                     retreatBrake: RetreatBrake(active: false, reason: ""), events: [])
}

// MARK: - 4A.4 持仓(审计台账,永不代下单)

/// 回落止盈状态(§五 v1.1-B.1,服务端 `_retrace_state` 算好下发:峰值 / 回落幅度 /
/// 是否触发——判定复用 `sentinel/holding.py::check_take_profit`,客户端只展示,不重算阈值)。
struct RetraceState: Codable, Equatable {
    var peak: Double
    var retracePct: Double
    var triggered: Bool
}

/// K4 持仓牌单条命中(v1.3-② / §五 v1.3-⑥-C)。服务端 16:35 EOD 面板上对持仓票重算
/// K4 advisory 命中,客户端只展示不重算。
///  · `level`:strong(强警示,置顶醒目)| normal(普通警示,进列表)。
///  · `evidenceStrength`:price_volume(价量硬数据,强证据)| constituent(概念板块成分,
///    弱证据,标「参考」——题材持续天数依赖 `ths_member` 快照,不单独触发强警示)。
///  · 只有「level=strong ∧ evidenceStrength=price_volume」才置顶醒目展示(疑似派发/换手
///    异常等);其余(含 strong 但成分类证据、或 normal)一律降级为列表/chip 展示。
struct K4Advisory: Codable, Equatable, Identifiable {
    var code: String
    var label: String
    var level: String              // strong | normal
    var evidence: String
    var evidenceStrength: String   // price_volume | constituent

    var id: String { code }
    var isStrong: Bool { level == "strong" }
    var isPriceVolumeEvidence: Bool { evidenceStrength == "price_volume" }
    /// 置顶醒目的判据(§五 v1.3-⑥-C 硬约束,不是「strong 就置顶」——弱证据即便标了
    /// strong 也只降级展示,守 §2.4 铁律「证伪只用价量结构」)。
    var isTopBillboard: Bool { isStrong && isPriceVolumeEvidence }
}

/// v1.3-① 两档时间退出态(服务端权威判定,§2.1 第 2 条;客户端只展示,不重算净浮盈)。
/// 未识别字符串兜底 `.holding`(不误报离场——宁可少提醒,不可错误地把未知态判成「该走了」)。
enum PositionTimeExitState: Equatable {
    static let timeExitNextDayRaw = "time_exit_next_day"
    static let profitExemptRaw = "profit_exempt"
    static let hardCapExitRaw = "hard_cap_exit"
    static let holdingRaw = "holding"
    static let suspendedHoldRaw = "suspended_hold"   // v1.4-①-B(§七 P0-2)

    case timeExitNextDay   // 非浮盈,次日按计划离场
    case profitExempt      // 浮盈豁免时间退出,交回落止盈+止损管到硬上限——**持有态,非离场提示**
    case hardCapExit       // 已达浮盈硬上限(D15),次日无条件离场
    case holding           // 常规持有(K1 单档下恒为此值或 timeExitNextDay)
    // v1.4-①-B:当日无 EOD 行(停牌/数据缺口)且尚未定格 → 判向挂起,不推 D5/硬上限
    // 提醒;`dCount` 照常按交易日累计并展示。复牌当日 16:35 用复牌当日 EOD 正常定格。
    case suspendedHold

    init(_ raw: String) {
        switch raw {
        case Self.timeExitNextDayRaw: self = .timeExitNextDay
        case Self.profitExemptRaw: self = .profitExempt
        case Self.hardCapExitRaw: self = .hardCapExit
        case Self.suspendedHoldRaw: self = .suspendedHold
        default: self = .holding
        }
    }
}

/// 持仓票价格陈旧度(v1.4-①-B,§七 P0-2)。当日**无 EOD 行**时才会有值(正常票不背这个
/// 字段的负担,`Position.priceStale` 为 `nil`)——`reason` 三态:`suspended`(停牌名单
/// 命中)/ `data_gap`(全市场当日有数据但唯独这只没有)/ `unknown`(停牌名单本身拿不到,
/// 如实说不知道,绝不猜成 suspended)。**绝不静默把老价当今日价**——这个类型就是那句
/// 「静默」的解药。
struct PriceStale: Codable, Equatable {
    var staleDays: Int
    var lastCloseDate: String    // 'YYYYMMDD';回看窗口内都找不到 → ""(如实留空,不臆造)
    var reason: String           // suspended | data_gap | unknown

    var reasonLabel: String {
        switch reason {
        case "suspended": return "停牌"
        case "data_gap": return "数据缺口"
        case "unknown": return "原因未知"
        default: return reason
        }
    }
}

struct Position: Codable, Equatable, Identifiable {
    var id: Int
    var code: String
    var name: String
    var buyPrice: Double
    var qty: Int
    var entryReason: String
    var buyDate: String      // 'YYYYMMDD',服务端字面口径(见 sentinel/positions.py)
    var price: Double        // 哨兵最近一拍 / EOD 兜底;拉不到 → 0.0(不可与"跌停 0 元"混淆,UI 需判断)
    var status: String
    var stopLine: Double     // 服务端派生 = buy×0.95(§2.1 单一常量),客户端不重算
    var stopOrderChecked: Bool
    // —— §五 v1.1-B.1/E.1 持仓生命周期派生字段(服务端算好,客户端不重算日历/阈值)——
    var dCount: Int = 1              // D 计数(买入日=D1,唯一源 sentinel/positions.py::d_count)
    var maxHoldDays: Int = 5         // 现役 max_hold_days(读 config,不硬编 5);K1 单档口径,v1.3 起
                                      // 展示改用 `maxHoldDaysEffective`(见下),本字段保留供旧逻辑/归因参考
    var distToStopPctServer: Double? = nil   // 服务端算好的距止损线百分比(小数,非 ×100);无实时价 → nil
    var retraceState: RetraceState? = nil
    var todayAction: String = ""     // 今日动作提示文案(D5离场/距止损/回落止盈已触发等,服务端定文案)
    // —— v1.3-① 两档时间退出(服务端按 D5 净浮盈判好下发,客户端不重算)——————————————
    var maxHoldDaysEffective: Int = 5   // 该单有效硬上限:非浮盈=maxHoldDays;浮盈豁免=硬上限(如 15)
    var timeExitState: String = "holding"
    // —— v1.3-① 费用回显(实付,供周复盘对账用真数;nil=未录)——————————————————————
    var buyFees: Double? = nil
    var sellFees: Double? = nil
    // —— v1.4-①-B 停牌 / 无行情持仓票的显式标注(§七 P0-2)————————————————————————
    /// 当日无 EOD 行时给出「陈旧几个交易日 / 最后成交日 / 为什么」三件;当日有行 → nil
    /// (正常票不背这个字段的负担)。
    var priceStale: PriceStale? = nil
    /// K4 每日体检是否因无 EOD 行被整份跳过。**三值**:true=没体检 / false=体检过了
    /// (空 `k4Advisory` 才等于「体检过没问题」)/ nil=老快照未记录(如实说不知道,不冒充 false)。
    var k4DataUnavailable: Bool? = nil
    // —— v1.4-⑥-C 定格日 ≠ D5 显式标注(§七 P1-6)——————————————————————————————————
    /// 定格发生当时的 `dCount`;nil=尚未定格(或老快照缺记录),**不拿今天冒充定格日**。
    var timeExitLockedDay: Int? = nil
    /// = `timeExitLockedDay − maxHoldDays`,下限 0;客户端 **>0 才展示**
    /// 「定格于 D{n},晚于 D{maxHoldDays} {k} 天」。⛔ 只提示,不改判定逻辑。
    var timeExitLockedLateDays: Int = 0
    // —— v1.3-② K4 持仓牌(服务端 16:35 EOD 重算命中;老快照/刚开仓未体检 → 空数组,
    // 前向兼容不特判)——————————————————————————————————————————————————————————
    var k4Advisory: [K4Advisory] = []
    // 该持仓是否有关联决策日志(via position_id)含非空情景树待每日对照(v1.3-②-D 提醒;
    // 勾选仍走既有 `POST /decisions/{id}/scenario-outcome`,本字段只做「挑出来」)。
    var scenarioReviewPending: Bool = false

    /// 显式 CodingKeys(`distToStopPctServer` 与服务端字面 `distToStopPct` 改了名——避免
    /// 和下面既有的、语义不同的客户端计算属性 `distToStopPct` 撞名;其余字段名与 JSON
    /// 字面一致)。**本类型自 v1.3-⑥ 起改手写 `init(from:)`**(见下)——`maxHoldDaysEffective`/
    /// `timeExitState`/`k4Advisory`/`scenarioReviewPending` 等虽非 Optional 但要容忍旧
    /// fixture/旧快照缺键(Swift 合成 Decodable 对非 Optional 属性不会自动容忍缺键,
    /// 默认值只影响 memberwise init、不影响解码,同 `Candidate` 这一版的处理姿势)。
    enum CodingKeys: String, CodingKey {
        case id, code, name, buyPrice, qty, entryReason, buyDate, price, status, stopLine, stopOrderChecked
        case dCount, maxHoldDays, retraceState, todayAction
        case distToStopPctServer = "distToStopPct"
        case maxHoldDaysEffective, timeExitState, buyFees, sellFees, k4Advisory, scenarioReviewPending
        case priceStale, k4DataUnavailable, timeExitLockedDay, timeExitLockedLateDays
    }

    init(id: Int, code: String, name: String, buyPrice: Double, qty: Int, entryReason: String,
         buyDate: String, price: Double, status: String, stopLine: Double, stopOrderChecked: Bool,
         dCount: Int = 1, maxHoldDays: Int = 5, distToStopPctServer: Double? = nil,
         retraceState: RetraceState? = nil, todayAction: String = "",
         maxHoldDaysEffective: Int = 5, timeExitState: String = "holding",
         buyFees: Double? = nil, sellFees: Double? = nil,
         priceStale: PriceStale? = nil, k4DataUnavailable: Bool? = nil,
         timeExitLockedDay: Int? = nil, timeExitLockedLateDays: Int = 0,
         k4Advisory: [K4Advisory] = [], scenarioReviewPending: Bool = false) {
        self.id = id; self.code = code; self.name = name; self.buyPrice = buyPrice; self.qty = qty
        self.entryReason = entryReason; self.buyDate = buyDate; self.price = price; self.status = status
        self.stopLine = stopLine; self.stopOrderChecked = stopOrderChecked
        self.dCount = dCount; self.maxHoldDays = maxHoldDays; self.distToStopPctServer = distToStopPctServer
        self.retraceState = retraceState; self.todayAction = todayAction
        self.maxHoldDaysEffective = maxHoldDaysEffective; self.timeExitState = timeExitState
        self.buyFees = buyFees; self.sellFees = sellFees
        self.priceStale = priceStale; self.k4DataUnavailable = k4DataUnavailable
        self.timeExitLockedDay = timeExitLockedDay; self.timeExitLockedLateDays = timeExitLockedLateDays
        self.k4Advisory = k4Advisory; self.scenarioReviewPending = scenarioReviewPending
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        code = try c.decode(String.self, forKey: .code)
        name = try c.decode(String.self, forKey: .name)
        buyPrice = try c.decode(Double.self, forKey: .buyPrice)
        qty = try c.decode(Int.self, forKey: .qty)
        entryReason = try c.decode(String.self, forKey: .entryReason)
        buyDate = try c.decode(String.self, forKey: .buyDate)
        price = try c.decode(Double.self, forKey: .price)
        status = try c.decode(String.self, forKey: .status)
        stopLine = try c.decode(Double.self, forKey: .stopLine)
        stopOrderChecked = try c.decode(Bool.self, forKey: .stopOrderChecked)
        dCount = try c.decodeIfPresent(Int.self, forKey: .dCount) ?? 1
        maxHoldDays = try c.decodeIfPresent(Int.self, forKey: .maxHoldDays) ?? 5
        distToStopPctServer = try c.decodeIfPresent(Double.self, forKey: .distToStopPctServer)
        retraceState = try c.decodeIfPresent(RetraceState.self, forKey: .retraceState)
        todayAction = try c.decodeIfPresent(String.self, forKey: .todayAction) ?? ""
        maxHoldDaysEffective = try c.decodeIfPresent(Int.self, forKey: .maxHoldDaysEffective) ?? maxHoldDays
        // 缺键(真正的旧服务端/旧 fixture,v1.3-① 前)→ 按旧单档口径派生(dCount>=maxHoldDays
        // 才算到期),与「服务端本该发什么」逐位一致——不是拍脑袋的"holding"兜底,而是精确
        // 复现 v1.1 单档时间退出行为,故老 fixture 的 isExitDay 断言不必因这次改动而重写。
        timeExitState = try c.decodeIfPresent(String.self, forKey: .timeExitState)
            ?? (dCount >= maxHoldDays ? PositionTimeExitState.timeExitNextDayRaw : PositionTimeExitState.holdingRaw)
        buyFees = try c.decodeIfPresent(Double.self, forKey: .buyFees)
        sellFees = try c.decodeIfPresent(Double.self, forKey: .sellFees)
        priceStale = try c.decodeIfPresent(PriceStale.self, forKey: .priceStale)
        k4DataUnavailable = try c.decodeIfPresent(Bool.self, forKey: .k4DataUnavailable)
        timeExitLockedDay = try c.decodeIfPresent(Int.self, forKey: .timeExitLockedDay)
        timeExitLockedLateDays = try c.decodeIfPresent(Int.self, forKey: .timeExitLockedLateDays) ?? 0
        k4Advisory = try c.decodeIfPresent([K4Advisory].self, forKey: .k4Advisory) ?? []
        scenarioReviewPending = try c.decodeIfPresent(Bool.self, forKey: .scenarioReviewPending) ?? false
    }

    var hasLivePrice: Bool { price > 0 }
    var pnlPct: Double {
        guard hasLivePrice, buyPrice > 0 else { return 0 }
        return (price - buyPrice) / buyPrice * 100
    }
    var pnlAmount: Double {
        guard hasLivePrice else { return 0 }
        return (price - buyPrice) * Double(qty)
    }
    /// 距止损线百分比(正 = 尚有缓冲,负 = 已破线);无实时价 → nil,UI 不误显 0%。
    /// 客户端派生(与服务端 `distToStopPctServer` 算法一致,同一口径,仅百分比展示单位不同),
    /// 保留是因为早于 B.1 已有该计算且被既有单测覆盖;新代码可直接读 `distToStopPctServer`。
    var distToStopPct: Double? {
        guard hasLivePrice, price > 0 else { return nil }
        return (price - stopLine) / price * 100
    }
    /// 已破 -5% 止损线(展示红色警示;真实止损执行在券商条件单,系统只审计)。
    var hasBrokenStop: Bool {
        guard hasLivePrice else { return false }
        return price <= stopLine
    }

    // —— §五 v1.1-E.1/v1.3-⑥-A 展示层派生(纯视觉强度选择,文案本身来自服务端
    // `todayAction`,这里只按服务端权威 `timeExitState` 两态选颜色/是否醒目横幅,
    // 不重新推导任何领域判定,同 `hasBrokenStop` 的展示层派生先例)。

    /// 服务端两档时间退出态的展示层枚举(见 `PositionTimeExitState`)。
    var timeExitKind: PositionTimeExitState { PositionTimeExitState(timeExitState) }

    /// 是否该醒目展示为「离场/到期」(两档:非浮盈到期 `timeExitNextDay` 或浮盈硬上限到期
    /// `hardCapExit`)。**`profitExempt` 不算**——它是持有态(交回落止盈+止损管到硬上限),
    /// §五 v1.3-⑥-A 明文「不要当离场提示展示」,故不能再用旧口径 `dCount >= maxHoldDays`
    /// 判定(那样会把「浮盈豁免续持到 D15」的正常单错误标红成「该走了」)。
    var isExitDay: Bool { timeExitKind == .timeExitNextDay || timeExitKind == .hardCapExit }

    var todayActionTone: NKAxisTone {
        if isExitDay { return .bad }
        if timeExitKind == .profitExempt { return .good }   // 浮盈豁免:持有态,非警示,给个正向色调
        // v1.4-①-B:判向挂起(停牌/无当日行情)——警示级但非"该走了",价格本身是陈旧的,
        // 不该被下面的距止损/回落止盈信号(基于陈旧价算出)误染成更高优先级的警示。
        if timeExitKind == .suspendedHold { return .warn }
        if retraceState?.triggered == true { return .bad }
        if let d = distToStopPctServer {
            if d <= 0 { return .bad }
            if d <= 0.02 { return .warn }
        }
        return .neutral
    }
}

// MARK: - v1.2 枚举展示层换算(服务端码 + 客户端展示层换算,沿 `nkBoardLabel` 先例;
// 未识别码原样透传,不静默瞎翻译)。自由函数用于「解码任意历史码做展示」的场景
// (如 `DecisionLog.thesisTags`);下面各 `CaseIterable` 枚举用于「录入表单的有限
// 可选项 picker」场景——两者共用同一份 label 映射,不重复定义第二份中文对照表。

func nkThesisTagLabel(_ raw: String) -> String {
    switch raw {
    case "THEME": return "题材主线"
    case "SENTIMENT_CYCLE": return "情绪周期位"
    case "CAPITAL_FLOW": return "资金流向"
    case "TECH_PATTERN": return "技术形态"
    case "NEWS": return "消息"
    default: return raw
    }
}

/// ⑤ 论点标签(v1.2-B,多选)。
enum ThesisTag: String, CaseIterable, Identifiable, Hashable, Codable {
    case theme = "THEME"
    case sentimentCycle = "SENTIMENT_CYCLE"
    case capitalFlow = "CAPITAL_FLOW"
    case techPattern = "TECH_PATTERN"
    case news = "NEWS"

    var id: String { rawValue }
    var label: String { nkThesisTagLabel(rawValue) }
}

func nkPlaybookTagLabel(_ raw: String) -> String {
    switch raw {
    case "SWING_CHASE": return "短线追击"
    case "BREATHING_TRIAL": return "呼吸底仓试验"
    default: return raw
    }
}

/// ⑧ 打法标签(v1.2-B,单选;对应三仓 = 2 短线追击 + 1 呼吸底仓试验,§2.1 第 3 条)。
enum PlaybookTag: String, CaseIterable, Identifiable, Hashable, Codable {
    case swingChase = "SWING_CHASE"
    case breathingTrial = "BREATHING_TRIAL"

    var id: String { rawValue }
    var label: String { nkPlaybookTagLabel(rawValue) }
}

func nkScenarioActionLabel(_ raw: String) -> String {
    switch raw {
    case "BUY": return "买入"
    case "HOLD": return "持有"
    case "REDUCE": return "减仓"
    case "ABANDON": return "放弃"
    default: return raw
    }
}

/// ⑦ 应对方案·情景树的动作枚举(v1.2-B)。
enum ScenarioAction: String, CaseIterable, Identifiable, Hashable, Codable {
    case buy = "BUY", hold = "HOLD", reduce = "REDUCE", abandon = "ABANDON"

    var id: String { rawValue }
    var label: String { nkScenarioActionLabel(rawValue) }
}

func nkCloseReasonLabel(_ raw: String) -> String {
    switch raw {
    case "STOP_LOSS": return "止损"
    case "TAKE_PROFIT": return "回落止盈"
    case "TIME_EXIT": return "时间退出"
    case "INVALIDATION": return "证伪离场"
    case "MANUAL": return "主动离场"
    default: return raw
    }
}

/// 离场原因(v1.2-A2,`PositionCloseIn.closeReason`,五码;不选则服务端按价格兜底
/// 判止损,见 CLAUDE.md「熔断兜底判据」坑)。
enum CloseReasonCode: String, CaseIterable, Identifiable, Hashable, Codable {
    case stopLoss = "STOP_LOSS"
    case takeProfit = "TAKE_PROFIT"
    case timeExit = "TIME_EXIT"
    case invalidation = "INVALIDATION"
    case manual = "MANUAL"

    var id: String { rawValue }
    var label: String { nkCloseReasonLabel(rawValue) }
}

// MARK: - v1.2-B 预注册决策日志(§五 v1.2-E.1;审计件、非下单件——本文件任何类型
// 都只是展示/编解码模型,不含任何触发下单的逻辑)。

/// ⑦ 应对方案·情景树单项。`Codable` 双向复用:解码 `DecisionOut.contingencyScenarios`
/// 时用,构造 `POST /decisions`·`revise` 请求体时也用(服务端 `ContingencyScenarioIn`/
/// `ContingencyScenarioOut` 形状一致,不必两份类型)。
struct ContingencyScenario: Codable, Equatable {
    var scenario: String
    var trigger: String
    var action: String        // BUY/HOLD/REDUCE/ABANDON,服务端码
    var matched: Bool = false

    var actionLabel: String { nkScenarioActionLabel(action) }
}

/// 对齐 `DecisionOut`(逐字段,见「v1.2 客户端契约清单」)。字段名与服务端 JSON
/// 完全一致,直接 `Codable` 解码,不需要私有 wire DTO 中转(同 `WatchlistItem`/
/// `BoardEvent`/`Position` 的直接解码先例)。
struct DecisionLog: Codable, Equatable, Identifiable {
    var id: Int
    var code: String
    var name: String
    var createdAt: String
    var whyBuy: String
    var whyEntryPrice: String
    var targetPrice: Double?
    var exitLow: Double?
    var exitHigh: Double?
    var thesisTags: [String]
    var invalidation: String
    var contingencyScenarios: [ContingencyScenario]
    var playbookTag: String
    var plannedPrice: Double?
    var plannedQty: Int?
    var status: String                // pending | filled | cancelled | expired
    var positionId: Int?
    var revisionOf: Int?
    /// ⑨ 最高追价上限(v1.4-⑤-B,需求 2 补充)。相对昨收百分比,如 `3.0`=+3%(**不是
    /// 小数 0.03**);允许负值(只在低开时买);`nil` = 显式选择"不设上限",**或**老行
    /// (建于本字段前)——两者在存储层无法区分,是迁移引入新必填字段时不可避免的历史
    /// 模糊,不影响新行起的强制语义。与 `plannedPrice`("我打算挂多少价")是两回事,
    /// 不要合并展示:本字段回答的是"开盘冲多高我就放弃、盘中不追补"。
    var maxChasePct: Double? = nil

    var thesisTagLabels: [String] { thesisTags.map(nkThesisTagLabel) }
    var playbookTagLabel: String { nkPlaybookTagLabel(playbookTag) }
    /// 三仓 = 2 短线追击 + 1 呼吸底仓试验(§2.1 第 3 条)——呼吸台账入口露出规则
    /// (§五 v1.2-E.4)据此判断,不新存第二份「是否呼吸仓」标记。
    var isBreathingTrial: Bool { playbookTag == PlaybookTag.breathingTrial.rawValue }
}

// MARK: - v1.4-⑦-A 挂单未成交追踪(§五 v1.4-⑦-A,§七 P3-12)。领域数据自 v1.3-④ 起已在攒
// (`report/pending_track.py`),本节把 `GET /decisions/{id}/track` 已有数据接上展示。

struct DecisionTrackRow: Codable, Equatable, Identifiable {
    var tradeDate: String
    var dOffset: Int
    var close: Double
    var retFromPlan: Double? = nil    // nil = 该决策未设 plannedPrice,不臆造

    var id: String { tradeDate }
}

/// `rows` 按 `tradeDate` 升序,**可能为空**——该决策尚未攒到任何追踪快照(刚创建、还没
/// 到下一交易日)不等于"没有这条决策"(那是 404),这是合法的 200 空态,UI 须展示
/// "暂未攒到数据"而非当作错误处理。
struct DecisionTrack: Codable, Equatable {
    var status: String
    var planPrice: Double? = nil
    var rows: [DecisionTrackRow] = []
}

// MARK: - v1.2-A2 熔断纪律状态(§五 v1.2-E.3;§2.1 第 7 条纯提醒层——客户端只展示
// 锁定态 + 灰化「开新仓」入口,绝不假装能拦下单,判定/阈值全在服务端)。

struct CircuitEpisode: Codable, Equatable {
    var triggerReason: String     // consecutive_stops | daily_loss
    var triggeredAt: String
    var triggerRefDate: String
    var basisTradesCount: Int     // 诚实边界:判定所依据的台账已补录成交笔数
    var basisWindow: String
    var note: String              // 服务端文案,含「基于台账 N 笔已补录成交」,客户端直接展示不改写

    var triggerReasonLabel: String {
        switch triggerReason {
        case "consecutive_stops": return "连续止损"
        case "daily_loss": return "单日净亏"
        default: return triggerReason
        }
    }
}

struct CircuitState: Codable, Equatable {
    var locked: Bool
    var episode: CircuitEpisode?

    static let empty = CircuitState(locked: false, episode: nil)
}

// MARK: - v1.2-G 呼吸试验仓台账(§五 v1.2-E.4)。`tPnl`/`baseCostAdj`/`edgeToPrice`
// 均服务端派生下发,客户端不重算(§2.1/§2.5 领域四条铁律的延伸)。

struct BreathingTrade: Codable, Equatable, Identifiable {
    var id: Int
    var positionId: Int
    var buyPrice: Double
    var sellPrice: Double
    var qty: Int
    var fees: Double
    var tDate: String
    var tPnl: Double
    var note: String = ""
}

struct BreathingLedger: Codable, Equatable {
    var items: [BreathingTrade]
    /// 底仓摊薄成本(先手成本)。无 T 记录 / 算不出 → nil,展示「—」不崩。
    var baseCostAdj: Double?
    /// 「先手」距离,**相对成本口径**(2026-07-25 用户拍板,浮盈率直觉):
    /// `(price−baseCostAdj)/baseCostAdj`——正值代表先手成本比现价低(浮盈),
    /// 负值代表先手成本比现价高(浮亏)。文案按「先手成本比现价低/高 X%」写,
    /// **不要**按「距现价」写(容易和 `Position.distToStopPct` 的现价分母口径混淆)。
    var edgeToPrice: Double?

    static let empty = BreathingLedger(items: [], baseCostAdj: nil, edgeToPrice: nil)
}

// MARK: - v1.1-B.3/v1.2-E.5 一键补录预填(区间双档,替换 v1.1 的单 `qty`)
//
// `EntrySuggestionOut` 改区间:`qtyHigh`/`capCeil` = 现役 `single_cap` 违纪判定
// 上限对应手数/金额(**非推荐值**);`qtyLow`/`capFloor` = 半仓保守下沿。客户端只
// 展示两档供参考,不替用户拍单笔金额(§2.1 第 3 条三仓制「单笔金额不定死」)。

struct EntrySuggestionRange: Codable, Equatable {
    var code: String
    var price: Double
    var qtyLow: Int
    var qtyHigh: Int
    var capFloor: Double
    var capCeil: Double
    var stopLine: Double
}

// MARK: - 4A.5 问询台

enum ChatRole: String, Codable {
    case user, assistant
}

struct ChatMessage: Identifiable, Equatable {
    let id = UUID()
    var role: ChatRole
    var text: String
}

/// 问询台描述性标注(§2.5,v1.3.3 起「审判员→自由分析师」)——**不是裁决**,不授权
/// 也不禁止任何操作,只标"这次回答里带没带风险提示"。后端 `verdict` 是宽松 `str`
/// (非枚举),客户端只认当前两个已知值,任何第三个字符串归 `.unknown`(绝不静默
/// 当成某个已知态展示,便于第一时间发现契约漂移)。
///
/// P3-14(⑦-C,2026-07-29):此前认的是 v1.3.3 已退役的二值裁决「不符合」/
/// 「初审通过进海选池」(`rejectRaw`/`passRaw`)——那两个值后端早已不产出,只剩
/// 单测在引用,是死码;换成当前真实会出现的两个值,顺带修掉「有风险提示」被
/// `.unknown` 兜底成中性色调、看不出风险的展示 bug(见 `tone`)。
enum InquiryVerdict: Equatable {
    static let analyzedRaw = "已分析"
    static let analyzedWarnRaw = "已分析·有风险提示"

    case analyzed
    case analyzedWarn
    case unknown(String)

    init(_ raw: String) {
        switch raw {
        case Self.analyzedRaw: self = .analyzed
        case Self.analyzedWarnRaw: self = .analyzedWarn
        default: self = .unknown(raw)
        }
    }

    var label: String {
        switch self {
        case .analyzed: return Self.analyzedRaw
        case .analyzedWarn: return Self.analyzedWarnRaw
        case .unknown(let s): return s
        }
    }

    /// P3-14(⑦-C):「已分析·有风险提示」此前落 `.unknown` → 中性色调,风险提示
    /// 形同隐身;现识别为已知态并显式给**警示色**(复用既有 `NKAxisTone.warn` →
    /// `NK.amber`,不新造色值)。「已分析」(无风险提示)维持中性——**verdict 不是
    /// 判决**,不给它套好评色调,以免被误读成"系统认可这只票"。
    var tone: NKAxisTone {
        switch self {
        case .analyzed: return .neutral
        case .analyzedWarn: return .warn
        case .unknown: return .neutral
        }
    }

    /// 硬约束不变量(§2.5「永不现在就买」):问询台标注**任何一种取值**都不启用
    /// 「买」类操作——UI 层只展示 `label` 徽标,从不为任何 verdict 渲染下单/买入按钮。
    /// 恒 false,穷举写死,不看 verdict 分支(见 NecklineTests 的对抗性字符串单测)。
    var enablesBuyAction: Bool { false }
}

struct InquiryResult: Equatable {
    var code: String
    var reply: String
    var verdict: InquiryVerdict
    var evidence: [String]
    var degraded: Bool
}

// MARK: - v1.4-⑦-B 问询记录档案(§五 v1.4-⑦-B,§七 P3-13)。**与 `inquiry_pool`
// (已退役历史队列表)是两件事**——本节是问答本身的档案记录,供历史列表 + 详情使用。
// `materials`/`searchHits`(服务端落库的原始快照,任意嵌套 JSON)不在客户端强类型化
// (UI 不需要逐字段渲染这两坨结构化材料,解码时按未声明键处理、天然跳过,不报错)。

struct InquiryLogEntry: Codable, Equatable, Identifiable {
    var id: Int
    var createdAt: String
    var code: String
    var name: String = ""
    var question: String = ""
    var answer: String
    var evidence: [String] = []
    var verdict: String
    var positionId: Int? = nil
    var decisionId: Int? = nil

    var verdictBadge: InquiryVerdict { InquiryVerdict(verdict) }
}

// MARK: - 4A.5 设置

enum LLMProviderKind: String, CaseIterable, Identifiable, Codable {
    case glm, kimi
    var id: String { rawValue }
    var label: String {
        switch self {
        case .glm: return "GLM"
        case .kimi: return "Kimi"
        }
    }
}

/// v1.1-G.1 推送开关四类(报告 / 退潮刹车 / 盘前校准 / D5 时间退出)+ v1.2-A2 第五类
/// (熔断提醒)+ v1.3-②/⑥ 第六类(K4 持仓派发警报),对齐后端 `PushSettingsOut`/
/// `SettingsPushIn` 六字段契约。
struct PushSettings: Codable, Equatable {
    var report: Bool
    var retreatBrake: Bool
    var precall: Bool
    var d5exit: Bool
    var circuit: Bool         // v1.2-A2:熔断提醒推送开关,默认开
    var holdingAlert: Bool    // v1.3-②:K4 持仓派发警报推送开关(第六类,默认开),独立于 d5exit
}

struct SettingsSnapshot: Codable, Equatable {
    var llmProvider: String?     // "glm" | "kimi" | nil(未设)
    var llmKeySet: Bool          // 只回布尔,绝不回明文(§3.4 高危区)
    var push: PushSettings
    var reviewColMap: [String: String]      // 4D 周复盘交割单列映射(见 §五 阶段4D.1)

    static let empty = SettingsSnapshot(
        llmProvider: nil, llmKeySet: false,
        push: PushSettings(report: true, retreatBrake: true, precall: true, d5exit: true,
                           circuit: true, holdingAlert: true),
        reviewColMap: [:]
    )
}

/// v1.3-③-C3/⑥ 候选情报管线「五板块常驻」名单(`GET/PUT /settings/intel-boards`)。
/// 板块中文名列表,按配置顺序(保底认领 load-bearing,§2.3);写入须与 `ths_index.name`
/// 精确匹配,匹配失败服务端 422(见 `APIClient.putIntelWatchBoards`)。
struct IntelWatchBoards: Codable, Equatable {
    var boards: [String]
    static let empty = IntelWatchBoards(boards: [])
}

// MARK: - §五 v1.1-F 自选板块(watchlist)
//
// 后端 `neckline/api/schemas.py::WatchlistCheckOut` 字段命名与 `CandidateOut` 四件套
// 一致(buyPoint/stop/target/invalidation),plan 原文点名「F.2 客户端可直接复用
// CandidateRow 四件套布局」——四件套展开区已抽成 `FourPieceDisclosure`(见
// Components/SharedUI.swift)供 `CandidateRow` 与本节的 `WatchlistRow` 共用,不重写。

struct WatchlistCheckItem: Codable, Equatable {
    var code: String
    var name: String
    var pinned: Bool
    var source: String
    var hasData: Bool
    var close: Double
    var board: String
    var score: Double?
    var patternTags: [String]
    var hotSectors: [String]
    var sectorNames: [String]
    var greenLight: Bool             // 纪律红绿灯:true=🟢可动,false=🔴禁买
    var disqualifiers: [String]
    var buyPointTriggered: Bool
    var buyPoint: String
    var stop: String
    var target: String
    var invalidation: String
    var statusChanged: Bool          // 较上一份报告状态是否变化(体检 LLM 只审 changed∪pinned 的判据)
    var llmJudgment: LLMJudgment?    // 仅 statusChanged∪pinned 才有(形状与 `CandidateOut.llmJudgment` 相同,复用同一类型)

    /// 展示层换算,与 `Candidate.boardLabel` 共用同一份映射(见 `nkBoardLabel`)。
    var boardLabel: String { nkBoardLabel(board) }
}

struct WatchlistItem: Codable, Equatable, Identifiable {
    var code: String
    var name: String
    var addedAt: String
    var source: String
    var note: String
    var pinned: Bool
    var updatedAt: String
    /// 最近一份报告的自选体检快照;从未体检过(刚加入 / 从无报告)→ nil,非报错。
    var check: WatchlistCheckItem?

    var id: String { code }
}

struct WatchlistSnapshot: Codable, Equatable {
    var items: [WatchlistItem]
    var maxSize: Int

    static let empty = WatchlistSnapshot(items: [], maxSize: 30)
}

/// 同花顺 txt 对账差异(§五 v1.1-C.4/F.4)。三个列表均为 Neckline `ts_code` 格式
/// (服务端已归一);对齐动作(加/删)由客户端按差异结果调 CRUD,本类型只是只读展示。
struct ThsReconcileResult: Codable, Equatable {
    var onlyInThs: [String]
    var onlyInNeckline: [String]
    var both: [String]

    static let empty = ThsReconcileResult(onlyInThs: [], onlyInNeckline: [], both: [])
}

// MARK: - 4D 周复盘工作台(对账三查 + 单周统计,§五 阶段4D)
//
// 后端 `neckline/api/schemas.py` 的 `WeeklyReviewOut.result`/`ReviewGetOut.result`
// 在 API 层是 `Dict[str, Any]` 透传(领域形状唯一源 = `neckline/review/reconcile.py`
// 的 `weekly_review_dict()`,同 `ReportOut.sentiment/sectors` 的透传惯例)——客户端
// 仍按已知稳定形状声明强类型 Codable(同 `SentimentSnapshot`/`SectorSnapshot` 先例),
// 便于渲染表格,不必满页 `[String: Any]` 手动取值。

struct ReviewRoundTrip: Codable, Equatable, Identifiable {
    var tsCode: String
    var name: String
    var buyDate: String
    var buyPrice: Double
    var qty: Int
    var buyAmount: Double
    var fees: Double
    var sellDate: String?
    var sellPrice: Double?
    var closed: Bool
    var netPnl: Double?
    var pnlPct: Double?

    var id: String { "\(tsCode)-\(buyDate)-\(sellDate ?? "open")-\(qty)-\(buyPrice)" }
}

struct ReviewPlanCheck: Codable, Equatable, Identifiable {
    var tsCode: String
    var name: String
    var tradeDate: String
    var price: Double
    var qty: Int
    var amount: Double
    var planStatus: String
    var ledgerStatus: String

    var id: String { "\(tsCode)-\(tradeDate)-\(price)" }
    var isOffPlan: Bool { planStatus.hasPrefix("计划外") }
    var isLedgerMissing: Bool { ledgerStatus.hasPrefix("台账缺失") }
    var isLedgerMismatch: Bool { ledgerStatus.hasPrefix("台账记录价格不符") }
}

/// 止损纪律分类(后端字面常量,`neckline.review.reconcile` 的四个模块常量,
/// 唯一源不重译阈值——只做展示层四常量换算,同 `Candidate.boardLabel` 先例)。
enum StopDisciplineKind: String, Codable {
    case breached = "breached"
    case keptStop = "kept_stop"
    case notTriggered = "not_triggered"
    case notApplicable = "not_applicable"

    var label: String {
        switch self {
        case .breached: return "破止损未离场"
        case .keptStop: return "止损执行到位"
        case .notTriggered: return "未触及止损"
        case .notApplicable: return "不适用"
        }
    }

    var tone: NKAxisTone {
        switch self {
        case .breached: return .bad
        case .keptStop: return .good
        case .notTriggered: return .neutral
        case .notApplicable: return .neutral
        }
    }
}

struct ReviewStopDisciplineEntry: Codable, Equatable, Identifiable {
    var roundTrip: ReviewRoundTrip
    var classification: String
    var note: String

    var id: String { roundTrip.id + classification }
    var kind: StopDisciplineKind? { StopDisciplineKind(rawValue: classification) }
}

struct ReviewWeeklyStats: Codable, Equatable {
    var closedCount: Int
    var openCount: Int
    var winRate: Double
    var profitFactor: Double?      // nil = 本周无亏损回合(数学上的无穷,后端已转 null)
    var profitLossRatio: Double?
    var totalFees: Double
    var grossPnl: Double
    var realizedPnl: Double
    var realizedLoss: Double       // 只累加亏损(§2.1 第4条口径),恒 <= 0
}

/// 周内一段「同一版章程治下」的区间(v1.4-⑥-A,§七 P1-4)。`start=nil` = 自周初起的
/// 那一段;时刻为北京时间 `'YYYY-MM-DD HH:MM'`。
struct ReviewCharterSegment: Codable, Equatable, Identifiable {
    var version: String
    var start: String? = nil
    var tradeCount: Int = 0

    var id: String { "\(version)|\(start ?? "week-start")|\(tradeCount)" }
}

/// 周内发生的一次章程切换(= `strategy_versions` 的一次激活落在本周窗口内)。
struct ReviewCharterSwitch: Codable, Equatable, Identifiable {
    var at: String            // 'YYYY-MM-DD HH:MM' 北京时间
    var fromVersion: String
    var toVersion: String
    var note: String = ""

    var id: String { at }
}

struct ReviewWeeklyResult: Codable, Equatable {
    var week: String
    var weekStart: String
    var weekEnd: String
    /// v1.4-⑥-A:该周**周初标签**(`brain.config_governing_for_week`,判据「激活日 <
    /// week_start」)——**不可再当"整周按这版判"展示**,该周若发生过章程切换,逐笔实际
    /// 按哪版判见 `charterSegments`/`charterSwitches`。旧结果(建于本字段前)读回空串。
    var strategyVersion: String = ""
    var charterSegments: [ReviewCharterSegment] = []
    var charterSwitches: [ReviewCharterSwitch] = []
    var roundTrips: [ReviewRoundTrip]
    var closedRoundTrips: [ReviewRoundTrip]
    var planChecks: [ReviewPlanCheck]
    var disciplineViolations: [String]
    var stopDiscipline: [ReviewStopDisciplineEntry]
    var stats: ReviewWeeklyStats?
    var forcedReview: Bool
    var forcedReviewReason: String

    /// `result` 是 `reviews.result_json` **写入当时**冻住的快照(不像 `intelRank`/
    /// `infoCard` 那样每次响应都由服务端重构、天然带全新字段默认值)——真实历史周报
    /// (建于 v1.4-⑥-A 之前)落库时压根没有 `strategyVersion`/`charterSegments`/
    /// `charterSwitches` 三键,**必须手写容错解码**,否则老周报直接读不出来。
    enum CodingKeys: String, CodingKey {
        case week, weekStart, weekEnd, strategyVersion, charterSegments, charterSwitches
        case roundTrips, closedRoundTrips, planChecks, disciplineViolations, stopDiscipline
        case stats, forcedReview, forcedReviewReason
    }

    init(week: String, weekStart: String, weekEnd: String, strategyVersion: String = "",
         charterSegments: [ReviewCharterSegment] = [], charterSwitches: [ReviewCharterSwitch] = [],
         roundTrips: [ReviewRoundTrip], closedRoundTrips: [ReviewRoundTrip],
         planChecks: [ReviewPlanCheck], disciplineViolations: [String],
         stopDiscipline: [ReviewStopDisciplineEntry], stats: ReviewWeeklyStats?,
         forcedReview: Bool, forcedReviewReason: String) {
        self.week = week; self.weekStart = weekStart; self.weekEnd = weekEnd
        self.strategyVersion = strategyVersion
        self.charterSegments = charterSegments; self.charterSwitches = charterSwitches
        self.roundTrips = roundTrips; self.closedRoundTrips = closedRoundTrips
        self.planChecks = planChecks; self.disciplineViolations = disciplineViolations
        self.stopDiscipline = stopDiscipline; self.stats = stats
        self.forcedReview = forcedReview; self.forcedReviewReason = forcedReviewReason
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        week = try c.decode(String.self, forKey: .week)
        weekStart = try c.decode(String.self, forKey: .weekStart)
        weekEnd = try c.decode(String.self, forKey: .weekEnd)
        strategyVersion = try c.decodeIfPresent(String.self, forKey: .strategyVersion) ?? ""
        charterSegments = try c.decodeIfPresent([ReviewCharterSegment].self, forKey: .charterSegments) ?? []
        charterSwitches = try c.decodeIfPresent([ReviewCharterSwitch].self, forKey: .charterSwitches) ?? []
        roundTrips = try c.decode([ReviewRoundTrip].self, forKey: .roundTrips)
        closedRoundTrips = try c.decode([ReviewRoundTrip].self, forKey: .closedRoundTrips)
        planChecks = try c.decode([ReviewPlanCheck].self, forKey: .planChecks)
        disciplineViolations = try c.decode([String].self, forKey: .disciplineViolations)
        stopDiscipline = try c.decode([ReviewStopDisciplineEntry].self, forKey: .stopDiscipline)
        stats = try c.decodeIfPresent(ReviewWeeklyStats.self, forKey: .stats)
        forcedReview = try c.decode(Bool.self, forKey: .forcedReview)
        forcedReviewReason = try c.decode(String.self, forKey: .forcedReviewReason)
    }
}

struct WeeklyReviewEntry: Codable, Equatable, Identifiable {
    var week: String
    var result: ReviewWeeklyResult
    var material: String

    var id: String { week }
}

struct ReviewUploadResponse: Codable, Equatable {
    var ok: Bool
    var weeks: [WeeklyReviewEntry]
    var parseWarnings: [String]
    var dataWarnings: [String]
    var sheetFormats: [String: String]
}

struct ReviewGetResponse: Codable, Equatable {
    var ok: Bool
    var found: Bool
    var week: String
    var generatedAt: String
    var result: ReviewWeeklyResult?
    var material: String
}

// MARK: - 展示用轴向着色(沿用 LinoN `AxisTone` 概念,四值穷举)
//
//  刻意只留纯枚举(不 import SwiftUI),保持 Models.swift 是纯 Foundation 数据层、
//  可脱离 UI 单测。真正的颜色映射在 `Components/SharedUI.swift`(那里把
//  `NKAxisTone` 映射到 `NK.up/.down/.amber/.textSecondary`)。

enum NKAxisTone: Equatable {
    case good, warn, bad, neutral
}
