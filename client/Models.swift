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

    var id: String { code }

    /// `board` 服务端字面实测是英文枚举码("MAIN"/"GEM"/"STAR"/"BSE",唯一源
    /// `neckline/data/board.py` 的 `Board` 枚举,§3.2.7/CLAUDE.md「板块分类唯一源」),
    /// 不是中文名。这里只做**展示层换算四个已知常量**,不改判定、不猜测新分类
    /// (未识别值原样透传,不静默瞎翻译——万一后端枚举新增值,界面照样不崩、只是显英文)。
    var boardLabel: String { nkBoardLabel(board) }
}

// MARK: - 4A.2 报告:整份报告

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
    var maxHoldDays: Int = 5         // 现役 max_hold_days(读 config,不硬编 5)
    var distToStopPctServer: Double? = nil   // 服务端算好的距止损线百分比(小数,非 ×100);无实时价 → nil
    var retraceState: RetraceState? = nil
    var todayAction: String = ""     // 今日动作提示文案(D5离场/距止损/回落止盈已触发等,服务端定文案)

    /// 显式 CodingKeys(仅因 `distToStopPctServer` 与服务端字面 `distToStopPct` 改了名——
    /// 避免和下面既有的、语义不同的客户端计算属性 `distToStopPct` 撞名——其余字段名与
    /// JSON 字面一致,逐一列出而非用 `.convertFromSnakeCase`,同文件头部注释的显式映射惯例)。
    enum CodingKeys: String, CodingKey {
        case id, code, name, buyPrice, qty, entryReason, buyDate, price, status, stopLine, stopOrderChecked
        case dCount, maxHoldDays, retraceState, todayAction
        case distToStopPctServer = "distToStopPct"
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

    // —— §五 v1.1-E.1 展示层派生(纯视觉强度选择,文案本身来自服务端 `todayAction`,
    // 这里只按已有派生字段的优先级——D5/时间退出 > 回落止盈已触发 > 距止损——决定颜色/
    // 是否用醒目横幅,不重新推导任何领域判定,同 `hasBrokenStop` 的展示层派生先例)。

    /// 是否到了/过了持有上限交易日(D5 时间退出日,`maxHoldDays` 非硬编 5)。
    var isExitDay: Bool { dCount >= maxHoldDays }

    var todayActionTone: NKAxisTone {
        if isExitDay { return .bad }
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

    var thesisTagLabels: [String] { thesisTags.map(nkThesisTagLabel) }
    var playbookTagLabel: String { nkPlaybookTagLabel(playbookTag) }
    /// 三仓 = 2 短线追击 + 1 呼吸底仓试验(§2.1 第 3 条)——呼吸台账入口露出规则
    /// (§五 v1.2-E.4)据此判断,不新存第二份「是否呼吸仓」标记。
    var isBreathingTrial: Bool { playbookTag == PlaybookTag.breathingTrial.rawValue }
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

/// 裁决二值(硬约束,§2.5「永不现在就买」)——枚举穷举只两值,任何第三个字符串
/// 归 `.unknown`(绝不静默当成某个已知态展示,便于第一时间发现契约漂移)。
enum InquiryVerdict: Equatable {
    static let rejectRaw = "不符合"
    static let passRaw = "初审通过进海选池"

    case reject
    case pass
    case unknown(String)

    init(_ raw: String) {
        switch raw {
        case Self.rejectRaw: self = .reject
        case Self.passRaw: self = .pass
        default: self = .unknown(raw)
        }
    }

    var label: String {
        switch self {
        case .reject: return Self.rejectRaw
        case .pass: return Self.passRaw
        case .unknown(let s): return s
        }
    }

    var tone: NKAxisTone {
        switch self {
        case .reject: return .bad
        case .pass: return .good
        case .unknown: return .neutral
        }
    }

    /// 硬约束不变量(§2.5「永不现在就买」):问询台裁决**任何一种取值**都不启用
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
/// (熔断提醒),对齐后端 `PushSettingsOut`/`SettingsPushIn` 五字段契约。
struct PushSettings: Codable, Equatable {
    var report: Bool
    var retreatBrake: Bool
    var precall: Bool
    var d5exit: Bool
    var circuit: Bool     // v1.2-A2:熔断提醒推送开关,默认开
}

struct SettingsSnapshot: Codable, Equatable {
    var llmProvider: String?     // "glm" | "kimi" | nil(未设)
    var llmKeySet: Bool          // 只回布尔,绝不回明文(§3.4 高危区)
    var push: PushSettings
    var reviewColMap: [String: String]      // 4D 周复盘交割单列映射(见 §五 阶段4D.1)

    static let empty = SettingsSnapshot(
        llmProvider: nil, llmKeySet: false,
        push: PushSettings(report: true, retreatBrake: true, precall: true, d5exit: true, circuit: true),
        reviewColMap: [:]
    )
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

struct ReviewWeeklyResult: Codable, Equatable {
    var week: String
    var weekStart: String
    var weekEnd: String
    var roundTrips: [ReviewRoundTrip]
    var closedRoundTrips: [ReviewRoundTrip]
    var planChecks: [ReviewPlanCheck]
    var disciplineViolations: [String]
    var stopDiscipline: [ReviewStopDisciplineEntry]
    var stats: ReviewWeeklyStats?
    var forcedReview: Bool
    var forcedReviewReason: String
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

enum NKAxisTone {
    case good, warn, bad, neutral
}
