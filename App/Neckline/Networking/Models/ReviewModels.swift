//
//  ReviewModels.swift
//  Neckline — 客户端展示层数据模型 · 4D 周复盘工作台 + V2.1 累计页五段 / 校准移交件 + 修改建议四分类
//  + V2.2-② 行情状态层 + V2.2-④ 双时钟
//
//  ⚠ **V2.4.0 P3.7 纯机械拆分**:本文件与同目录另外五份 `*Models.swift` 是原
//  `Networking/Models.swift`(5633 行)**逐字**切出来的,⛔ 一个声明没改、没加、没删
//  (切点全在顶层 `// MARK:` 之前的空行上;拆分脚本对拼回来的全文做过逐字节比对)。
//  🔴 **守门单测不再按 `Models.swift` 这个文件名读客户端 DTO** —— 一律走
//  `tests/client_sources.py::networking_swift_text()`(把本目录全部 `.swift` 拼起来)。
//  ⛔ 新增 DTO 文件必须放在本目录下,否则那些「某字段已退役」的**缺席断言**会静默变成
//  真(读不到的文件里当然搜不到),看起来还全绿 —— 这是拆分引入的唯一新风险面,
//  `tests/client_sources.py` 里的哨兵断言就是为它立的。
//
//  ⚠ 加 / 移动 `.swift` 与新增一样,**必须 `xcodegen generate`**(pbxproj 是显式文件引用)。
//

import Foundation

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

// ══════════════════════════════════════════════════════════════════════════
// MARK: - V2.1-⑤/⑦ 复盘板块:累计页五段 + 校准移交件(**B 类:含冻结产物内容**)
// ══════════════════════════════════════════════════════════════════════════
//
// 🔴 **为什么整族手写 `init(from:)` + `decodeIfPresent`**:这两条端点回的不是"服务端
// 每次重拼的视图",而是**已经落盘冻住的产物原文**(周度校准报告 JSON / `reviews.result_json`
// / 画像表行)透传出来的 —— 服务端升级**不会**给老产物补新键(CLAUDE.md 落库快照两类论
// 的 B 类)。合成 `Decodable` 对非 Optional 属性「有默认值也不容忍缺键」,一旦某期老产物
// 缺一个键,**整页复盘直接解不出**。

/// 复盘板块「累计」页里的**一段**(V2.1-⑤,五段形状统一)。
///
/// 🔴 **三态读法,⛔ 不许拿一个总开关罩住五段**:
///   · **有**   → `available == true` + 有内容;
///   · **没有** → `available == true` + 空内容(该段自己的空态文案说清为什么空);
///   · **没取到** → `available == false` + `unavailableReason`(⛔ 不许拿空数组冒充)。
///
/// ⚠ **画像段与对账段的空态服务端刻意判得不一样,客户端也必须分开渲染,⛔ 别"统一"**:
/// 画像缺席 = **系统自己那一步没跑**(周度批算未运行)→ 那是**「没看」**→ `available=false`;
/// 对账缺席 = 输入(券商交割单)**只能由用户给**、系统查过表确实没有 → 那是**「没有」**
/// → `available=true` + `detail.found == false`。两者给用户的动作完全不同(等系统 vs 去上传)。
struct ReviewSegment: Codable, Equatable {
    var available: Bool = false
    var unavailableReason: String? = nil
    var label: String = ""
    /// 该段的时点标识:画像期 / ISO 周 / 校准窗口(`20260720→20260724`)。
    var asOf: String = ""
    /// **原样透传领域形状**(同 `WeeklyReviewOut.result` / `EvalWeeklyOut.result` 惯例)。
    var items: [NKJSON] = []
    var detail: NKJSON = .object([:])

    enum CodingKeys: String, CodingKey {
        case available, unavailableReason, label, asOf, items, detail
    }

    init(available: Bool = false, unavailableReason: String? = nil, label: String = "",
         asOf: String = "", items: [NKJSON] = [], detail: NKJSON = .object([:])) {
        self.available = available; self.unavailableReason = unavailableReason
        self.label = label; self.asOf = asOf; self.items = items; self.detail = detail
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? false
        unavailableReason = try c.decodeIfPresent(String.self, forKey: .unavailableReason)
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        asOf = try c.decodeIfPresent(String.self, forKey: .asOf) ?? ""
        items = try c.decodeIfPresent([NKJSON].self, forKey: .items) ?? []
        detail = try c.decodeIfPresent(NKJSON.self, forKey: .detail) ?? .object([:])
    }

    /// 对账段专用:`false` = **这周没有**(不是"没查")。⚠ 只在 `available == true` 时
    /// 才读得出意思;`available == false` 时该段根本没查成,别拿它当"没有"。
    var found: Bool? { detail["found"]?.boolValue }
    /// 该段可直接渲染的一句话(服务端给的空态说明,如「本周尚未上传交割单 —— …」)。
    var note: String? { detail["note"]?.stringValue }

    /// **对账段里那份已落库的周报**(`detail.result`)解成强类型;`found == false` → `nil`。
    ///
    /// 🔴 **为什么加它**(V2.3.1 批 4):macOS 对账工作台原来**只认「本次上传的返回值」**
    /// (`AppModel.reviewWeeks` 唯一写入点是 `uploadReviewFiles`)—— 重启 App 就说
    /// 「还没有对账数据 · 把每周的券商交割单拖到上面」,而**同一份数据 iPhone 那边
    /// (累计页对账段)照样看得到**。那是把**「没看」讲成了「没有」**,正是本项目一贯
    /// 要分开的那两件事。⛔ **零新增网络调用**:这份 JSON 本来就在 `/review/overview`
    /// 的响应里,这里只是把它解出来。
    /// ⚠ 解不出(老产物缺键 / 形状变了)→ `nil`,调用方照旧走空态,⛔ 不抛、不半渲染。
    var weeklyEntry: WeeklyReviewEntry? {
        guard available, found == true, let result = detail["result"] else { return nil }
        guard let data = try? JSONEncoder().encode(result),
              let decoded = try? JSONDecoder().decode(ReviewWeeklyResult.self, from: data)
        else { return nil }
        let week = detail["week"]?.stringValue ?? asOf
        return WeeklyReviewEntry(week: week.isEmpty ? decoded.week : week,
                                 result: decoded,
                                 material: detail["material"]?.stringValue ?? "")
    }
}

/// 复盘板块「累计」页的聚合读(V2.1-⑤,`GET /review/overview`)。
///
/// **零现算**:五段全部读**已冻结 / 已落盘**的产物(校准报告由离线周度作业算好落盘)。
/// 🔴 **本端点一律不 404**(空态走各段 `available=false`)→ 客户端**不需要**为它加任何
/// `mapReason` case(V2.1 零新增 reason 字符串)。
struct ReviewOverview: Codable, Equatable {
    var weekStart: String = ""
    var weekEnd: String = ""
    /// ISO 周键(`YYYY-Www`),对账段按它取。
    var weekKey: String = ""
    var calibration: ReviewSegment = ReviewSegment()
    var preference: ReviewSegment = ReviewSegment()
    var capability: ReviewSegment = ReviewSegment()
    var reconcile: ReviewSegment = ReviewSegment()
    var observations: ReviewSegment = ReviewSegment()
    // —— V2.2-④ 新增三段(**同样各自 `available` + `unavailableReason`**;
    //    ⛔ 不许被上面五段的任何一段罩住 —— 它们是八件互不相干的事)——
    var selectionClock: ReviewSegment = ReviewSegment()
    var tradeClock: ReviewSegment = ReviewSegment()
    var iterationSuggestions: ReviewSegment = ReviewSegment()

    enum CodingKeys: String, CodingKey {
        case weekStart, weekEnd, weekKey, calibration, preference, capability
        case reconcile, observations
        case selectionClock, tradeClock, iterationSuggestions
    }

    init(weekStart: String = "", weekEnd: String = "", weekKey: String = "",
         calibration: ReviewSegment = ReviewSegment(), preference: ReviewSegment = ReviewSegment(),
         capability: ReviewSegment = ReviewSegment(), reconcile: ReviewSegment = ReviewSegment(),
         observations: ReviewSegment = ReviewSegment(),
         selectionClock: ReviewSegment = ReviewSegment(),
         tradeClock: ReviewSegment = ReviewSegment(),
         iterationSuggestions: ReviewSegment = ReviewSegment()) {
        self.weekStart = weekStart; self.weekEnd = weekEnd; self.weekKey = weekKey
        self.calibration = calibration; self.preference = preference
        self.capability = capability; self.reconcile = reconcile; self.observations = observations
        self.selectionClock = selectionClock; self.tradeClock = tradeClock
        self.iterationSuggestions = iterationSuggestions
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        weekStart = try c.decodeIfPresent(String.self, forKey: .weekStart) ?? ""
        weekEnd = try c.decodeIfPresent(String.self, forKey: .weekEnd) ?? ""
        weekKey = try c.decodeIfPresent(String.self, forKey: .weekKey) ?? ""
        calibration = try c.decodeIfPresent(ReviewSegment.self, forKey: .calibration) ?? ReviewSegment()
        preference = try c.decodeIfPresent(ReviewSegment.self, forKey: .preference) ?? ReviewSegment()
        capability = try c.decodeIfPresent(ReviewSegment.self, forKey: .capability) ?? ReviewSegment()
        reconcile = try c.decodeIfPresent(ReviewSegment.self, forKey: .reconcile) ?? ReviewSegment()
        observations = try c.decodeIfPresent(ReviewSegment.self, forKey: .observations) ?? ReviewSegment()
        selectionClock = try c.decodeIfPresent(ReviewSegment.self, forKey: .selectionClock) ?? ReviewSegment()
        tradeClock = try c.decodeIfPresent(ReviewSegment.self, forKey: .tradeClock) ?? ReviewSegment()
        iterationSuggestions = try c.decodeIfPresent(ReviewSegment.self,
                                                     forKey: .iterationSuggestions) ?? ReviewSegment()
    }
}

// MARK: - V2.2-④-D 修改建议四分类(K8 §十七;**只给建议,⛔ 零写回**)
//
// 🔴 **`klass == nil` + `klassStatus == "thresholds_undecided"` 是设计中的状态,不是缺陷**:
// K8 §十七 只给了定性描述(保留 / 观察 / 降权 / 淘汰),**没有给「多少样本算够」
// 「差多少算失效」这两个数**;它们必须由用户拍板、经四道闸进骨架包才生效。
// ⛔ **界面上绝不许把它显示成「暂无建议」或空白** —— 那会把「还没决定」讲成「没问题」;
// ⛔ 也不许渲染成「观察」——「还没决定」与「样本不足」是两件事(服务端 docstring 原话)。

/// 四分类码 → 中文(唯一源 = 服务端 `eval/iteration.py::KLASS_LABELS`;
/// 服务端已在 `klassLabel` 里下发,本函数只在那个键缺席时兜底)。
func nkIterationKlassLabel(_ raw: String) -> String {
    switch raw {
    case "keep": return "保留 · 持续有效"
    case "observe": return "观察 · 样本不足"
    case "downweight": return "降权 · 辅助有效"
    case "retire": return "淘汰 · 持续失效"
    default: return raw
    }
}

func nkIterationKlassTone(_ raw: String) -> NKAxisTone {
    switch raw {
    case "keep": return .good
    case "observe": return .neutral
    case "downweight": return .warn
    case "retire": return .bad
    default: return .neutral
    }
}

/// 四分类建议一行(`ReviewSegment.items` 的一条,**自由结构原样透传** → 这里只做投影)。
struct IterationSuggestion: Identifiable, Equatable {
    let raw: NKJSON
    /// 因素标识(`dimension=value`,如 `gate=position:ok`)。
    var factor: String { raw["factor"]?.stringValue ?? "—" }
    var n: Int { raw["n"]?.intValue ?? 0 }
    var klass: String? { raw["klass"]?.stringValue }
    var klassStatus: String? { raw["klassStatus"]?.stringValue }
    /// 服务端给的中文名优先(`klassLabel`),缺席才用客户端换算。
    var klassLabel: String? {
        raw["klassLabel"]?.stringValue ?? klass.map(nkIterationKlassLabel)
    }
    var klassTone: NKAxisTone { klass.map(nkIterationKlassTone) ?? .neutral }
    /// 服务端写好的整句建议(**分界线未定时,这句话里就写着缺哪两个数**)。⛔ 原样展示。
    var suggestion: String { raw["suggestion"]?.stringValue ?? "" }
    var engineCode: String? { raw["engineCode"]?.stringValue }
    var engineVersion: String? { raw["engineVersion"]?.stringValue }
    var accuracy: Double? { raw["accuracy"]?.doubleValue }
    var delta: Double? { raw["delta"]?.doubleValue }
    var placeboEdge: String? { raw["placeboEdge"]?.stringValue }

    /// 🔴 **分界线还没拍板**。⛔ 界面必须显式说出这件事。
    var thresholdsUndecided: Bool { klass == nil && klassStatus == "thresholds_undecided" }

    var id: String { "\(engineCode ?? "")|\(engineVersion ?? "")|\(factor)" }
}

/// 校准移交件(V2.1-⑤,`GET /review/handoff`)——一份能**直接交给策略台**的 markdown。
///
/// **`available == false` 的两种成因文案服务端已分开写在 `unavailableReason` 里**
/// (① 一期产物都还没有 = **会自愈**;② 指定窗口的产物读不出 = **不会自愈**),
/// ⛔ 客户端原样展示那句话,别自己再合并成一句「暂不可用」。
struct ReviewHandoff: Codable, Equatable {
    var available: Bool = false
    var unavailableReason: String? = nil
    var windowFrom: String = ""
    var windowTo: String = ""
    var generatedAt: String = ""
    /// 那份文档 §① 的数字版(`tradingDays` / `baskets` / `strata` / `preferenceRows` / …)。
    var sampleN: [String: Int] = [:]
    var markdown: String = ""

    enum CodingKeys: String, CodingKey {
        case available, unavailableReason, windowFrom, windowTo, generatedAt, sampleN, markdown
    }

    init(available: Bool = false, unavailableReason: String? = nil, windowFrom: String = "",
         windowTo: String = "", generatedAt: String = "", sampleN: [String: Int] = [:],
         markdown: String = "") {
        self.available = available; self.unavailableReason = unavailableReason
        self.windowFrom = windowFrom; self.windowTo = windowTo; self.generatedAt = generatedAt
        self.sampleN = sampleN; self.markdown = markdown
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? false
        unavailableReason = try c.decodeIfPresent(String.self, forKey: .unavailableReason)
        windowFrom = try c.decodeIfPresent(String.self, forKey: .windowFrom) ?? ""
        windowTo = try c.decodeIfPresent(String.self, forKey: .windowTo) ?? ""
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt) ?? ""
        sampleN = try c.decodeIfPresent([String: Int].self, forKey: .sampleN) ?? [:]
        markdown = try c.decodeIfPresent(String.self, forKey: .markdown) ?? ""
    }

    /// 窗口显示名;两端都空时给一句诚实的占位(⛔ 不显示 "→")。
    var windowLabel: String {
        guard !windowFrom.isEmpty || !windowTo.isEmpty else { return "窗口未知" }
        return "\(windowFrom) → \(windowTo)"
    }

    /// 导出文件名(`ShareLink` / macOS 存盘共用,**双端同一个名字**)。
    var suggestedFilename: String {
        let w = windowFrom.isEmpty && windowTo.isEmpty ? "unknown" : "\(windowFrom)_\(windowTo)"
        return "Neckline_校准移交件_\(w).md"
    }
}

/// 观察项一条(移交件与累计页共用的读取视图;服务端 `HANDOFF_OBSERVATIONS` 静态登记册)。
/// **只读透传,⛔ 客户端不改写 status、不给"建议"** —— 它是**等证据的策略问题清单**,
/// 攒够样本后由用户带去策略台,不是待办事项。
struct ReviewObservation: Identifiable, Equatable {
    let raw: NKJSON
    var id: String { obsId }

    var obsId: String { raw["id"]?.stringValue ?? "" }
    var title: String { raw["title"]?.stringValue ?? "" }
    var question: String { raw["question"]?.stringValue ?? "" }
    var evidenceNeeded: String { raw["evidence_needed"]?.stringValue ?? "" }
    var status: String { raw["status"]?.stringValue ?? "" }
}

// MARK: - V2.2-② 行情状态层(`GET /market-regime`,D0 盘后三态)
//
// 🔴 **三条硬边界**(服务端 docstring 原文):**只读、零现算、一律不 404** ——
// 缺行 / 表空 / 参数非法一律 200 + `available=false` + 自由文本原因。
// ⛔ **`available == false` 既不是错误、也不是「没风险」**:它是「我们今天没算出
// 行情状态」。展示处必须把 `unavailableReason` 那句话原样说出口(§3.8 诚实披露)。
//
// ⚠ **纯展示、⛔ 零动作**:行情状态**不改变任何持仓判定、不触发任何提醒**
// (§五 〇b-7:不许留「建议今天别开仓」这类自动状态位)。

/// `market_regime_daily` 一行。`inputs` / `strengthening` / `weakening` 是**原样透传**
/// 的自由结构(五维各自带 `available`/`unavailable_reason` 双位);`regimeLabel` 由
/// **服务端**给人读名(唯一源 `scan/regime.py::REGIME_LABELS`)——⛔ 客户端不另建
/// 一份中文映射,那会在服务端改名时静默显示旧名。
struct MarketRegimeDay: Codable, Equatable {
    var tradeDate: String = ""
    /// `trend_continuation` | `high_divergence` | `rotation_confirmed`。
    var regime: String = ""
    var regimeLabel: String = ""
    var regimeReason: String = ""
    var inputs: NKJSON = .object([:])
    var strengthening: [NKJSON] = []
    var weakening: [NKJSON] = []
    var skeletonVersion: String = ""
    var computedAt: String = ""

    enum CodingKeys: String, CodingKey {
        case tradeDate, regime, regimeLabel, regimeReason, inputs
        case strengthening, weakening, skeletonVersion, computedAt
    }

    init(tradeDate: String = "", regime: String = "", regimeLabel: String = "",
         regimeReason: String = "", inputs: NKJSON = .object([:]),
         strengthening: [NKJSON] = [], weakening: [NKJSON] = [],
         skeletonVersion: String = "", computedAt: String = "") {
        self.tradeDate = tradeDate; self.regime = regime; self.regimeLabel = regimeLabel
        self.regimeReason = regimeReason; self.inputs = inputs
        self.strengthening = strengthening; self.weakening = weakening
        self.skeletonVersion = skeletonVersion; self.computedAt = computedAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        regime = try c.decodeIfPresent(String.self, forKey: .regime) ?? ""
        regimeLabel = try c.decodeIfPresent(String.self, forKey: .regimeLabel) ?? ""
        regimeReason = try c.decodeIfPresent(String.self, forKey: .regimeReason) ?? ""
        inputs = try c.decodeIfPresent(NKJSON.self, forKey: .inputs) ?? .object([:])
        strengthening = try c.decodeIfPresent([NKJSON].self, forKey: .strengthening) ?? []
        weakening = try c.decodeIfPresent([NKJSON].self, forKey: .weakening) ?? []
        skeletonVersion = try c.decodeIfPresent(String.self, forKey: .skeletonVersion) ?? ""
        computedAt = try c.decodeIfPresent(String.self, forKey: .computedAt) ?? ""
    }

    /// 人读名兜底:服务端没给 `regimeLabel` 时原样显示英文码(⛔ 不瞎翻译)。
    var displayLabel: String { regimeLabel.isEmpty ? regime : regimeLabel }

    /// 三态着色。**「切换确认」不是坏事、「趋势延续」也不是买入背书** —— 这里的颜色
    /// 只表达「市场结构有多不稳」,⛔ 不是行情看多看空的信号(§2.8-C)。
    var tone: NKAxisTone {
        switch regime {
        case "trend_continuation": return .good
        case "high_divergence": return .bad
        case "rotation_confirmed": return .warn
        default: return .neutral
        }
    }

    /// 五维里**没算出来**的那几维(`inputs.<dim>.available == false`)。
    /// 🔴 界面要把它说出口:缺维不是「这一维没问题」。
    /// ⚠ 返回的是**服务端英文码**;要印到界面上一律过 `nkRegimeDimLabel`。
    var missingDims: [String] {
        (inputs.objectValue ?? [:])
            .filter { $0.value["available"]?.boolValue == false }
            .keys.sorted()
    }

    /// 中文展示名(界面用这个)。⚠ 未识别的码**原样透传**,⛔ 不瞎翻译。
    var missingDimLabels: [String] { missingDims.map(nkRegimeDimLabel) }
}

/// 行情状态五维码 → 中文展示名(**唯一**展示层换算源,沿 `nkBoardLabel` /
/// `nkRoleLabel` 先例:服务端只发英文码、中文在客户端换算、未识别值原样透传)。
///
/// 🔴 **不换算就是把 `moneyflow_migration` 这种机器标识符直接印在首屏上**
/// (V2.3.1 批 2 实拍逮到,与 §〇c 硬伤 2「角色码印英文」同一个病)。
/// 唯一源 = 服务端 `neckline/scan/regime.py` 顶部的 `DIM_*` 常量。
func nkRegimeDimLabel(_ raw: String) -> String {
    switch raw {
    case "core_strength": return "核心资产强度"
    case "breadth": return "赚钱效应宽度"
    case "relative_strength": return "新老方向相对强度"
    case "moneyflow_migration": return "资金流迁移"
    case "t1t2_accuracy": return "T1/T2 命中率"
    case "position_quota": return "仓位额度"
    default: return raw
    }
}

/// **行情状态三态码 → 中文**(展示层换算,同 `nkBoardLabel` / `nkRegimeDimLabel` 先例;
/// 唯一源 = 服务端 `neckline/scan/regime.py::REGIME_LABELS`)。**未识别值原样透传**。
///
/// 🔴 **为什么另要一个**:`/market-regime` 会**下发** `regimeLabel`(行情状态条走那条),
/// 但**选股时钟的 `regimeAtD0` 只有裸码** —— V2.3.0 直接 `Text("D0 行情状态:\(r)")`,
/// 界面上印出 `trend_continuation`(硬伤 2 的**第九处**,V2.3.1 批 4 实拍逮到)。
func nkRegimeLabel(_ raw: String) -> String {
    switch raw {
    case "trend_continuation": return "趋势延续"
    case "high_divergence": return "高位分歧"
    case "rotation_confirmed": return "切换确认"
    default: return raw
    }
}

/// **安慰剂对照判定码 → 中文**(唯一源 `neckline/eval/iteration.py` 的 `EDGE_*`)。
/// ⚠ `inconclusive` 与 `unavailable` **刻意分开**:一个是「算过了、样本没到结论线」、
/// 一个是「这一层压根没有对照臂产物」—— ⛔ 别合并成一句「无」。未识别值原样透传。
func nkPlaceboEdgeLabel(_ raw: String) -> String {
    switch raw {
    case "better": return "优于随机"
    case "worse": return "劣于随机"
    case "inconclusive": return "样本未到结论线"
    case "unavailable": return "本层无对照臂"
    default: return raw
    }
}

/// **交易时钟六项的项码 → 中文**(K8 §十六;唯一源 `neckline/eval/iteration.py::TRADE_ITEMS`)。
///
/// ⚠ **原型那六格(买点偏离中位 / 持有天数中位 / 止损执行率 …)在契约里不存在** ——
/// 那是理想化 mock。这里给的是**真的那六项**,⛔ 不为了对上原型的字面去造六个新指标。
func nkTradeClockItemLabel(_ raw: String) -> String {
    switch raw {
    case "thesis_accuracy": return "原始判断正确率"
    case "plan_consistency": return "入场与预案一致性"
    case "exit_quality_on_thesis": return "判断成立时的离场质量"
    case "exit_quality_on_decay": return "上涨效率下降时的离场"
    case "stop_quality_on_failure": return "判断失效时的止损质量"
    case "user_pick_vs_all": return "实际选择 vs 全部候选"
    default: return raw
    }
}

/// **画像维度码 → 中文**(唯一源 `neckline/profile/common.py` 顶部的 `DIM_*`)。
/// ⚠ `entry_style`(几何口径:买入价相对建仓区间)与 `entry_label`(用户自述的七枚标签)
/// **刻意是两个维度**,⛔ 别合成一句「入场方式」——它们回答的是不同的问题。
/// **未识别值原样透传**。
func nkProfileDimensionLabel(_ raw: String) -> String {
    switch raw {
    case "theme": return "常买题材"
    case "role": return "常买角色"
    case "entry_style": return "入场方式 · 几何口径"
    case "entry_label": return "入场方式 · 你自述"
    case "tier": return "常选 Tier"
    case "missed_role": return "常被忽略的角色"
    default: return raw
    }
}

/// **画像分组值 → 中文**(值的词表**随维度不同**,故必须带着维度一起换算)。
/// `theme` 的值本来就是中文行业名 → 原样;其余按各自词表;**未识别值原样透传**。
func nkProfileValueLabel(dimension: String, value: String) -> String {
    switch dimension {
    case "role", "missed_role":
        return value == "independent" ? "独立买入" : nkRoleLabelOrDash(value)
    case "tier":
        return value == "independent" ? "独立买入" : "T\(value)"
    case "entry_style":
        switch value {
        case "within_zone": return "区间内"
        case "chased_above": return "追高"
        case "below_zone": return "更低吸"
        case "no_reference": return "无区间可比"
        default: return value
        }
    default: return value
    }
}

/// `GET /market-regime` 响应。`day` = 单日查询;`days` = 区间查询(本版只用 `day`)。
struct MarketRegime: Codable, Equatable {
    var available: Bool = false
    var unavailableReason: String? = nil
    var day: MarketRegimeDay? = nil
    var days: [MarketRegimeDay] = []

    static let empty = MarketRegime()

    enum CodingKeys: String, CodingKey { case available, unavailableReason, day, days }

    init(available: Bool = false, unavailableReason: String? = nil,
         day: MarketRegimeDay? = nil, days: [MarketRegimeDay] = []) {
        self.available = available; self.unavailableReason = unavailableReason
        self.day = day; self.days = days
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? false
        unavailableReason = try c.decodeIfPresent(String.self, forKey: .unavailableReason)
        day = try c.decodeIfPresent(MarketRegimeDay.self, forKey: .day)
        days = try c.decodeIfPresent([MarketRegimeDay].self, forKey: .days) ?? []
    }
}

// MARK: - V2.2-④ 双时钟(选股时钟 / 交易时钟)
//
// 🔴 **两个时钟问的是两件事,⛔ 不合并**(K8 §十四):
//   · **选股时钟** = 「这批票选得对不对」—— 覆盖 D0 **全部** T1/T2,
//     **与用户买没买无关**,D1 收盘统一验证一次后**结案**;
//   · **交易时钟** = 「这笔买卖做得怎么样」—— **启动唯一条件 = 实际买入**,全部离场后结案。
// ⛔ 客户端文案不许把选股时钟写成「你关注的篮子」之类 —— 那会把覆盖域讲小。
//
// ⚠ 两者都是 **B 类冻结快照**(`selection_clock.mech_json` / `trade_clock.final_json`
// 写入当时冻住、不随服务端升级补键)→ DTO **必须手写 `init(from:)`**(V2-⑮ 铁律)。

/// 一篮的选股时钟**结案件**。`tierAccuracy` 是 ⑦-b **四态原样**
/// (`verified`/`partial`/`unclear`/`falsified`)加两个"没判"码 —— **⛔ 不是 0/1 的
/// 对错**,折成正确率是周度侧的事,客户端不折。
struct SelectionClock: Codable, Equatable, Identifiable {
    var basketId: Int = 0
    var d0Date: String = ""
    var d1Date: String = ""
    var coveredTier: Int = 0
    /// D0 当天的行情状态;**nil = 当日无行**(如实,⛔ 不猜)。
    var regimeAtD0: String? = nil
    var tierAccuracy: String? = nil
    /// 未触发原因(触发了则 nil)。
    var untriggeredReason: String? = nil
    var closedAt: String = ""
    var skeletonVersion: String = ""
    var verificationRulesetVersion: String = ""
    /// 引擎归因快照(裁定 #9 单篮子单引擎 → 两键)。
    var engineBreakdown: NKJSON = .object([:])
    /// 九项验证内容(**顺序即 K8 原文顺序**;每项自带 available/source)。原样透传。
    var mech: NKJSON = .object([:])

    var id: Int { basketId }

    enum CodingKeys: String, CodingKey {
        case basketId, d0Date, d1Date, coveredTier, regimeAtD0, tierAccuracy
        case untriggeredReason, closedAt, skeletonVersion, verificationRulesetVersion
        case engineBreakdown, mech
    }

    init(basketId: Int = 0, d0Date: String = "", d1Date: String = "", coveredTier: Int = 0,
         regimeAtD0: String? = nil, tierAccuracy: String? = nil, untriggeredReason: String? = nil,
         closedAt: String = "", skeletonVersion: String = "",
         verificationRulesetVersion: String = "", engineBreakdown: NKJSON = .object([:]),
         mech: NKJSON = .object([:])) {
        self.basketId = basketId; self.d0Date = d0Date; self.d1Date = d1Date
        self.coveredTier = coveredTier; self.regimeAtD0 = regimeAtD0
        self.tierAccuracy = tierAccuracy; self.untriggeredReason = untriggeredReason
        self.closedAt = closedAt; self.skeletonVersion = skeletonVersion
        self.verificationRulesetVersion = verificationRulesetVersion
        self.engineBreakdown = engineBreakdown; self.mech = mech
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId) ?? 0
        d0Date = try c.decodeIfPresent(String.self, forKey: .d0Date) ?? ""
        d1Date = try c.decodeIfPresent(String.self, forKey: .d1Date) ?? ""
        coveredTier = try c.decodeIfPresent(Int.self, forKey: .coveredTier) ?? 0
        regimeAtD0 = try c.decodeIfPresent(String.self, forKey: .regimeAtD0)
        tierAccuracy = try c.decodeIfPresent(String.self, forKey: .tierAccuracy)
        untriggeredReason = try c.decodeIfPresent(String.self, forKey: .untriggeredReason)
        closedAt = try c.decodeIfPresent(String.self, forKey: .closedAt) ?? ""
        skeletonVersion = try c.decodeIfPresent(String.self, forKey: .skeletonVersion) ?? ""
        verificationRulesetVersion = try c.decodeIfPresent(
            String.self, forKey: .verificationRulesetVersion) ?? ""
        engineBreakdown = try c.decodeIfPresent(NKJSON.self, forKey: .engineBreakdown) ?? .object([:])
        mech = try c.decodeIfPresent(NKJSON.self, forKey: .mech) ?? .object([:])
    }

    var engineCode: String? { engineBreakdown["engine_code"]?.stringValue }
    var engineVersion: String? { engineBreakdown["engine_version"]?.stringValue }

    /// ⚠ **`tierAccuracy == nil` 是「没判」,不是「判错了」** —— 展示处如实说。
    var tierAccuracyLabel: String {
        guard let t = tierAccuracy else { return "本篮未给分层准确性判定" }
        return nkVerificationStateLabel(t)
    }
    var tierAccuracyTone: NKAxisTone {
        switch tierAccuracy {
        case "verified": return .good
        case "partial": return .warn
        case "falsified": return .bad
        default: return .neutral   // unclear / 两个"没判"码 / nil
        }
    }
}

/// 交易时钟事件流水一行(**append-only**)。`userNote` = K8 §十五 用户主观说明
/// (⛔ 系统不生成、不改写、不合并 —— §七 P3-28 纪律)。
struct TradeClockEvent: Codable, Equatable, Identifiable {
    var id: Int = 0
    var eventDate: String = ""
    /// `d1_open | daily_check | target_zone | invalidation | manual_note | close`。
    var kind: String = ""
    var mech: NKJSON = .object([:])
    var userNote: String? = nil
    var createdAt: String = ""

    enum CodingKeys: String, CodingKey { case id, eventDate, kind, mech, userNote, createdAt }

    init(id: Int = 0, eventDate: String = "", kind: String = "", mech: NKJSON = .object([:]),
         userNote: String? = nil, createdAt: String = "") {
        self.id = id; self.eventDate = eventDate; self.kind = kind
        self.mech = mech; self.userNote = userNote; self.createdAt = createdAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int.self, forKey: .id) ?? 0
        eventDate = try c.decodeIfPresent(String.self, forKey: .eventDate) ?? ""
        kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? ""
        mech = try c.decodeIfPresent(NKJSON.self, forKey: .mech) ?? .object([:])
        userNote = try c.decodeIfPresent(String.self, forKey: .userNote)
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
    }

    var kindLabel: String { nkTradeClockEventKindLabel(kind) }
}

/// 交易时钟事件类型的展示层换算(未识别码原样透传,⛔ 不瞎翻译)。
func nkTradeClockEventKindLabel(_ raw: String) -> String {
    switch raw {
    case "d1_open": return "D1 开盘"
    case "daily_check": return "每日检查"
    case "target_zone": return "进入目标区间"
    case "invalidation": return "失效信号"
    case "manual_note": return "你的说明"
    case "close": return "结案"
    default: return raw
    }
}

/// 一笔真实买入的交易时钟。`final` **只在结案后有值**(运行中恒 nil ——「还没结案」
/// 与「结案了但八项算不出」必须分得开);`basketId == nil` = 非篮子来源的手动开仓,
/// **合法**,⛔ 不是数据错误。
struct TradeClock: Codable, Equatable, Identifiable {
    var positionId: Int = 0
    var tsCode: String = ""
    var basketId: Int? = nil
    var openedOn: String = ""
    var closedOn: String? = nil
    /// `running` | `closed`。
    var status: String = ""
    /// 开仓时从 `position_plans` 冻的四件套快照(原样透传)。
    var entryPlan: NKJSON = .object([:])
    /// 结案时的八项验证(K8 §十四)。nil = **还在跑**。
    var final: NKJSON? = nil
    var events: [TradeClockEvent] = []

    var id: Int { positionId }

    enum CodingKeys: String, CodingKey {
        case positionId, tsCode, basketId, openedOn, closedOn, status, entryPlan, final, events
    }

    init(positionId: Int = 0, tsCode: String = "", basketId: Int? = nil, openedOn: String = "",
         closedOn: String? = nil, status: String = "", entryPlan: NKJSON = .object([:]),
         final: NKJSON? = nil, events: [TradeClockEvent] = []) {
        self.positionId = positionId; self.tsCode = tsCode; self.basketId = basketId
        self.openedOn = openedOn; self.closedOn = closedOn; self.status = status
        self.entryPlan = entryPlan; self.final = final; self.events = events
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        positionId = try c.decodeIfPresent(Int.self, forKey: .positionId) ?? 0
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId)
        openedOn = try c.decodeIfPresent(String.self, forKey: .openedOn) ?? ""
        closedOn = try c.decodeIfPresent(String.self, forKey: .closedOn)
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? ""
        entryPlan = try c.decodeIfPresent(NKJSON.self, forKey: .entryPlan) ?? .object([:])
        // ⚠ `final` 运行中服务端发的是 JSON `null`(不是缺键)——`decodeIfPresent`
        // 对显式 null 返回 nil,正是我们要的「还没结案」。
        final = try c.decodeIfPresent(NKJSON.self, forKey: .final)
        events = try c.decodeIfPresent([TradeClockEvent].self, forKey: .events) ?? []
    }

    var isRunning: Bool { status == "running" }
    var statusLabel: String {
        switch status {
        case "running": return "跟踪中"
        case "closed": return "已结案"
        default: return status.isEmpty ? "状态未知" : status
        }
    }
    /// 用户已补的主观说明(按时间序)。**空 = 这笔仓一条说明都没写**(K8 §十五 覆盖率
    /// 稀疏的直接体现,§七 P3-28)—— ⛔ 系统不代猜、不代填。
    var userNotes: [TradeClockEvent] { events.filter { ($0.userNote ?? "").isEmpty == false } }
}

/// `POST /clocks/trade/{id}/note` 响应。`coverage` = 「本期 N 笔中有 M 笔带说明」
/// (§七 P3-28 候选解法①:让稀疏程度当场可见)。
struct TradeClockNoteResult: Codable, Equatable {
    var ok: Bool = true
    var eventId: Int = 0
    var eventDate: String = ""
    var coverage: NKJSON = .object([:])

    enum CodingKeys: String, CodingKey { case ok, eventId, eventDate, coverage }

    init(ok: Bool = true, eventId: Int = 0, eventDate: String = "",
         coverage: NKJSON = .object([:])) {
        self.ok = ok; self.eventId = eventId; self.eventDate = eventDate; self.coverage = coverage
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? true
        eventId = try c.decodeIfPresent(Int.self, forKey: .eventId) ?? 0
        eventDate = try c.decodeIfPresent(String.self, forKey: .eventDate) ?? ""
        coverage = try c.decodeIfPresent(NKJSON.self, forKey: .coverage) ?? .object([:])
    }

    /// 一句人读的覆盖率(键缺就不说,⛔ 不拿 0 冒充)。
    var coverageText: String? {
        guard let total = coverage["total"]?.intValue,
              let withNote = coverage["withNote"]?.intValue else { return nil }
        return "本期 \(total) 笔中 \(withNote) 笔带说明"
    }
}

/// 🔴 **用户主观说明的长度上界 —— 这是服务端 `review/trade_clock.USER_NOTE_MAX_CHARS`
/// 的镜像,⛔ 不是客户端自己定的阈值。** 权威永远在服务端:超长服务端返 **422**,
/// 客户端这个数只用来画字数计数器与提前提示。
/// ⚠ 两处不同步就会静默出现「客户端说能写、服务端说太长」——
/// 故 `tests/test_contract_crosscheck.py` 有一条机器判据把两个数钉成相等,
/// **改服务端那个常量不改这里,Python 套件当场红**。
let nkTradeNoteMaxChars: Int = 500
