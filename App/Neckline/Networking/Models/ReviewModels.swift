//
//  ReviewModels.swift
//  Neckline — **复盘板块**的 DTO(V2.5.0 S12 重做,架构 §六 / PROJECT_PLAN §5.9)。
//
//  复盘板块只做四件事(⛔ 不多做):
//    **交割单上传 → 解析结果 → 装订材料 → 结论存档**,外加走这条线的**我的成绩**。
//  🔴 **这一层无 LLM 调用**(架构 §六 明令):系统只负责解析、装订、存档;
//  好坏结论由用户带着材料到聊天框里得出,再用「结论存档」存回来。
//
//  对齐后端 `POST /api/v1/review/upload` · `GET /review?week=` · `GET /review/bindery?week=`
//  · `POST|GET /review/conclusions` · `GET /review/overview`。
//
//  🔴 **V2.5.0 S12 删掉六族**(端点或产出它们的判据已随 K8 整链下线,⛔ 别接回来):
//    · `ReviewPlanCheck` / `StopDisciplineKind` / `ReviewStopDisciplineEntry` /
//      `ReviewCharterSegment` / `ReviewCharterSwitch` —— K8 章程判据整块退役(§5.9);
//    · `IterationSuggestion` 四分类 —— K8 §十七 的产物;
//    · `ReviewHandoff` —— `/review/handoff` 已删;
//    · `MarketRegime*` —— `/market-regime` 已删;
//    · `SelectionClock` / `TradeClock*` —— 双时钟复盘整块退役;
//    · `ReviewObservation` —— 观察项登记册仍在服务端,但内容是 K8 语义的策略问题
//      (涨停簇 lift / 门槛制),K9 之下形状未定义 → **本版界面不呈现**
//      (PROJECT_PLAN §13.1-B8 已登记,等用户裁定)。
//
//  🔴 **为什么整族手写 `init(from:)` + `decodeIfPresent`**:这些端点回的不是"服务端每次
//  重拼的视图",而是**已经落盘冻住的产物原文**(`reviews.result_json` /
//  `review_conclusions` 行)透传出来的 —— 服务端升级**不会**给老产物补新键。
//  合成 `Decodable` 对非 Optional 属性「有默认值也不容忍缺键」,一旦某周老产物缺一个键,
//  **整页复盘直接解不出**。
//

import Foundation

// MARK: - 交割单解析结果(FIFO 回合 + 单周统计)

/// 一笔 FIFO 回合(服务端 `review/reconcile.py::round_trip_dict` 是唯一形状源)。
struct ReviewRoundTrip: Codable, Equatable, Identifiable {
    var tsCode: String = ""
    var name: String = ""
    var buyDate: String = ""
    var buyPrice: Double = 0
    var qty: Int = 0
    var buyAmount: Double = 0
    var fees: Double = 0
    var sellDate: String? = nil
    var sellPrice: Double? = nil
    var closed: Bool = false
    var netPnl: Double? = nil
    var pnlPct: Double? = nil

    var id: String { "\(tsCode)-\(buyDate)-\(sellDate ?? "open")-\(qty)-\(buyPrice)" }

    enum CodingKeys: String, CodingKey {
        case tsCode, name, buyDate, buyPrice, qty, buyAmount, fees
        case sellDate, sellPrice, closed, netPnl, pnlPct
    }

    init(tsCode: String = "", name: String = "", buyDate: String = "", buyPrice: Double = 0,
         qty: Int = 0, buyAmount: Double = 0, fees: Double = 0, sellDate: String? = nil,
         sellPrice: Double? = nil, closed: Bool = false, netPnl: Double? = nil,
         pnlPct: Double? = nil) {
        self.tsCode = tsCode; self.name = name; self.buyDate = buyDate
        self.buyPrice = buyPrice; self.qty = qty; self.buyAmount = buyAmount; self.fees = fees
        self.sellDate = sellDate; self.sellPrice = sellPrice; self.closed = closed
        self.netPnl = netPnl; self.pnlPct = pnlPct
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        buyDate = try c.decodeIfPresent(String.self, forKey: .buyDate) ?? ""
        buyPrice = try c.decodeIfPresent(Double.self, forKey: .buyPrice) ?? 0
        qty = try c.decodeIfPresent(Int.self, forKey: .qty) ?? 0
        buyAmount = try c.decodeIfPresent(Double.self, forKey: .buyAmount) ?? 0
        fees = try c.decodeIfPresent(Double.self, forKey: .fees) ?? 0
        sellDate = try c.decodeIfPresent(String.self, forKey: .sellDate)
        sellPrice = try c.decodeIfPresent(Double.self, forKey: .sellPrice)
        closed = try c.decodeIfPresent(Bool.self, forKey: .closed) ?? false
        netPnl = try c.decodeIfPresent(Double.self, forKey: .netPnl)
        pnlPct = try c.decodeIfPresent(Double.self, forKey: .pnlPct)
    }
}

/// **我的成绩**(架构 §5.3 / §五):来源是交割单,走周末复盘线,
/// 🔴 与清单成绩、覆盖率**完全隔离** —— ⛔ 它绝不进另外两条线的分子分母,反之亦然。
struct ReviewWeeklyStats: Codable, Equatable {
    var closedCount: Int = 0
    var openCount: Int = 0
    var winRate: Double = 0
    /// `nil` = 本周无亏损回合(数学上的无穷,服务端已转 null)。⛔ 不显示成 0。
    var profitFactor: Double? = nil
    var profitLossRatio: Double? = nil
    var totalFees: Double = 0
    var grossPnl: Double = 0
    var realizedPnl: Double = 0
    /// 只累加亏损,恒 ≤ 0。
    var realizedLoss: Double = 0

    enum CodingKeys: String, CodingKey {
        case closedCount, openCount, winRate, profitFactor, profitLossRatio
        case totalFees, grossPnl, realizedPnl, realizedLoss
    }

    init(closedCount: Int = 0, openCount: Int = 0, winRate: Double = 0,
         profitFactor: Double? = nil, profitLossRatio: Double? = nil, totalFees: Double = 0,
         grossPnl: Double = 0, realizedPnl: Double = 0, realizedLoss: Double = 0) {
        self.closedCount = closedCount; self.openCount = openCount; self.winRate = winRate
        self.profitFactor = profitFactor; self.profitLossRatio = profitLossRatio
        self.totalFees = totalFees; self.grossPnl = grossPnl
        self.realizedPnl = realizedPnl; self.realizedLoss = realizedLoss
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        closedCount = try c.decodeIfPresent(Int.self, forKey: .closedCount) ?? 0
        openCount = try c.decodeIfPresent(Int.self, forKey: .openCount) ?? 0
        winRate = try c.decodeIfPresent(Double.self, forKey: .winRate) ?? 0
        profitFactor = try c.decodeIfPresent(Double.self, forKey: .profitFactor)
        profitLossRatio = try c.decodeIfPresent(Double.self, forKey: .profitLossRatio)
        totalFees = try c.decodeIfPresent(Double.self, forKey: .totalFees) ?? 0
        grossPnl = try c.decodeIfPresent(Double.self, forKey: .grossPnl) ?? 0
        realizedPnl = try c.decodeIfPresent(Double.self, forKey: .realizedPnl) ?? 0
        realizedLoss = try c.decodeIfPresent(Double.self, forKey: .realizedLoss) ?? 0
    }
}

/// 一周的解析结果(服务端 `weekly_review_dict()` 是唯一形状源)。
///
/// ⚠ **V2.5.0 起服务端不再产出 K8 章程那八个键**(`planChecks` / `disciplineViolations` /
/// `stopDiscipline` / `charterSegments` / …)。`reviews` 表里 V2.4.x 及更早的**历史行**
/// 仍带着它们(写入当时冻住的快照)—— 这里**不声明**那些键,读老行时它们被自然忽略,
/// ⛔ 不回填、不改写历史行(裁定 6)。
struct ReviewWeeklyResult: Codable, Equatable {
    var week: String = ""
    var weekStart: String = ""
    var weekEnd: String = ""
    var roundTrips: [ReviewRoundTrip] = []
    var closedRoundTrips: [ReviewRoundTrip] = []
    /// `nil` = 本周没有可统计的回合。
    var stats: ReviewWeeklyStats? = nil
    var forcedReview: Bool = false
    var forcedReviewReason: String = ""

    enum CodingKeys: String, CodingKey {
        case week, weekStart, weekEnd, roundTrips, closedRoundTrips
        case stats, forcedReview, forcedReviewReason
    }

    init(week: String = "", weekStart: String = "", weekEnd: String = "",
         roundTrips: [ReviewRoundTrip] = [], closedRoundTrips: [ReviewRoundTrip] = [],
         stats: ReviewWeeklyStats? = nil, forcedReview: Bool = false,
         forcedReviewReason: String = "") {
        self.week = week; self.weekStart = weekStart; self.weekEnd = weekEnd
        self.roundTrips = roundTrips; self.closedRoundTrips = closedRoundTrips
        self.stats = stats; self.forcedReview = forcedReview
        self.forcedReviewReason = forcedReviewReason
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        week = try c.decodeIfPresent(String.self, forKey: .week) ?? ""
        weekStart = try c.decodeIfPresent(String.self, forKey: .weekStart) ?? ""
        weekEnd = try c.decodeIfPresent(String.self, forKey: .weekEnd) ?? ""
        roundTrips = try c.decodeIfPresent([ReviewRoundTrip].self, forKey: .roundTrips) ?? []
        closedRoundTrips = try c.decodeIfPresent([ReviewRoundTrip].self,
                                                 forKey: .closedRoundTrips) ?? []
        stats = try c.decodeIfPresent(ReviewWeeklyStats.self, forKey: .stats)
        forcedReview = try c.decodeIfPresent(Bool.self, forKey: .forcedReview) ?? false
        forcedReviewReason = try c.decodeIfPresent(String.self, forKey: .forcedReviewReason) ?? ""
    }
}

struct WeeklyReviewEntry: Codable, Equatable, Identifiable {
    var week: String = ""
    var result: ReviewWeeklyResult = ReviewWeeklyResult()
    var material: String = ""

    var id: String { week }

    enum CodingKeys: String, CodingKey { case week, result, material }

    init(week: String = "", result: ReviewWeeklyResult = ReviewWeeklyResult(),
         material: String = "") {
        self.week = week; self.result = result; self.material = material
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        week = try c.decodeIfPresent(String.self, forKey: .week) ?? ""
        result = try c.decodeIfPresent(ReviewWeeklyResult.self, forKey: .result)
            ?? ReviewWeeklyResult()
        material = try c.decodeIfPresent(String.self, forKey: .material) ?? ""
    }
}

struct ReviewUploadResponse: Codable, Equatable {
    var ok: Bool = false
    var weeks: [WeeklyReviewEntry] = []
    /// 解析层面的问题(未知格式 / 反查失败 / 非法工作簿)。
    var parseWarnings: [String] = []
    /// FIFO 数据完整性问题(如卖出找不到匹配买入)。⛔ 与上一栏分开,两者要人做的事不同。
    var dataWarnings: [String] = []
    var sheetFormats: [String: String] = [:]

    enum CodingKeys: String, CodingKey {
        case ok, weeks, parseWarnings, dataWarnings, sheetFormats
    }

    init(ok: Bool = false, weeks: [WeeklyReviewEntry] = [], parseWarnings: [String] = [],
         dataWarnings: [String] = [], sheetFormats: [String: String] = [:]) {
        self.ok = ok; self.weeks = weeks; self.parseWarnings = parseWarnings
        self.dataWarnings = dataWarnings; self.sheetFormats = sheetFormats
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? false
        weeks = try c.decodeIfPresent([WeeklyReviewEntry].self, forKey: .weeks) ?? []
        parseWarnings = try c.decodeIfPresent([String].self, forKey: .parseWarnings) ?? []
        dataWarnings = try c.decodeIfPresent([String].self, forKey: .dataWarnings) ?? []
        sheetFormats = try c.decodeIfPresent([String: String].self, forKey: .sheetFormats) ?? [:]
    }
}

struct ReviewGetResponse: Codable, Equatable {
    var ok: Bool = false
    /// `false` = **这周没上传过交割单**(⛔ 不是「系统没跑」)。
    var found: Bool = false
    var week: String = ""
    var generatedAt: String = ""
    var result: ReviewWeeklyResult? = nil
    var material: String = ""

    enum CodingKeys: String, CodingKey { case ok, found, week, generatedAt, result, material }

    init(ok: Bool = false, found: Bool = false, week: String = "", generatedAt: String = "",
         result: ReviewWeeklyResult? = nil, material: String = "") {
        self.ok = ok; self.found = found; self.week = week
        self.generatedAt = generatedAt; self.result = result; self.material = material
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? false
        found = try c.decodeIfPresent(Bool.self, forKey: .found) ?? false
        week = try c.decodeIfPresent(String.self, forKey: .week) ?? ""
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt) ?? ""
        result = try c.decodeIfPresent(ReviewWeeklyResult.self, forKey: .result)
        material = try c.decodeIfPresent(String.self, forKey: .material) ?? ""
    }
}

// MARK: - 行情材料装订(架构 §六 第 2 件事)

/// 一周的装订材料。`binding` 原样透传服务端 `review/bindery.py::WeekBinding.to_dict()`
/// (同 `result` 透传惯例,⛔ 不在客户端镜像一套会漂的嵌套模型);
/// `markdown` 是同一份材料的排版结果,供用户**整段复制到聊天框**。
///
/// 🔴 **`gaps` 必须原样呈现**:哪一段材料没取到、为什么,**是材料的一部分**;
/// ⛔ 客户端不许把它折叠掉(那等于让缺失静默)。
/// ⚠ `found == false` = **这周没上传过交割单**(⛔ 不是 404、⛔ 不是「系统没跑」)。
struct ReviewBindery: Codable, Equatable {
    var ok: Bool = false
    var found: Bool = false
    var week: String = ""
    var binding: NKJSON = .null
    var markdown: String = ""
    var unavailableReason: String? = nil

    static let empty = ReviewBindery()

    enum CodingKeys: String, CodingKey {
        case ok, found, week, binding, markdown, unavailableReason
    }

    init(ok: Bool = false, found: Bool = false, week: String = "", binding: NKJSON = .null,
         markdown: String = "", unavailableReason: String? = nil) {
        self.ok = ok; self.found = found; self.week = week
        self.binding = binding; self.markdown = markdown
        self.unavailableReason = unavailableReason
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? false
        found = try c.decodeIfPresent(Bool.self, forKey: .found) ?? false
        week = try c.decodeIfPresent(String.self, forKey: .week) ?? ""
        binding = try c.decodeIfPresent(NKJSON.self, forKey: .binding) ?? .null
        markdown = try c.decodeIfPresent(String.self, forKey: .markdown) ?? ""
        unavailableReason = try c.decodeIfPresent(String.self, forKey: .unavailableReason)
    }

    /// 整份材料级的缺口(⛔ 不许折叠)。
    var gaps: [String] {
        (binding["gaps"]?.arrayValue ?? []).compactMap(\.stringValue)
    }

    var roundTrips: [NKJSON] { binding["roundTrips"]?.arrayValue ?? [] }
    var windowStart: String { binding["windowStart"]?.stringValue ?? "" }
    var windowEnd: String { binding["windowEnd"]?.stringValue ?? "" }
    var benchmarkName: String { binding["benchmarkName"]?.stringValue ?? "" }
    /// 服务端写在材料里的那句话(「这是回看材料,不是判断」+ 申万归属的语义差)。
    /// ⛔ 客户端不许改写、不许省略。
    var note: String { binding["note"]?.stringValue ?? "" }
}

// MARK: - 结论存档(架构 §六 第 3 件事;append-only)

/// 一版复盘结论。**存一次 = 新版本,⛔ 老版本一个字不动。**
struct ReviewConclusion: Codable, Equatable, Identifiable {
    var week: String = ""
    var version: Int = 0
    var title: String = ""
    var body: String = ""
    var tags: [String] = []
    var author: String = "user"
    var createdAt: String = ""

    var id: String { "\(week)-v\(version)" }

    enum CodingKeys: String, CodingKey {
        case week, version, title, body, tags, author, createdAt
    }

    init(week: String = "", version: Int = 0, title: String = "", body: String = "",
         tags: [String] = [], author: String = "user", createdAt: String = "") {
        self.week = week; self.version = version; self.title = title; self.body = body
        self.tags = tags; self.author = author; self.createdAt = createdAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        week = try c.decodeIfPresent(String.self, forKey: .week) ?? ""
        version = try c.decodeIfPresent(Int.self, forKey: .version) ?? 0
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        body = try c.decodeIfPresent(String.self, forKey: .body) ?? ""
        tags = try c.decodeIfPresent([String].self, forKey: .tags) ?? []
        author = try c.decodeIfPresent(String.self, forKey: .author) ?? "user"
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
    }
}

/// 结论存档的读回。⚠ `latest == nil` = **那周还没写过结论**
/// (⛔ 别渲染成「这周没问题」)。
struct ReviewConclusionsResponse: Codable, Equatable {
    var ok: Bool = false
    var week: String = ""
    var latest: ReviewConclusion? = nil
    /// 该周全部版本(升序)。
    var versions: [ReviewConclusion] = []
    /// 检索命中(每周只出最新版,按周降序)。
    var matches: [ReviewConclusion] = []

    static let empty = ReviewConclusionsResponse()

    enum CodingKeys: String, CodingKey { case ok, week, latest, versions, matches }

    init(ok: Bool = false, week: String = "", latest: ReviewConclusion? = nil,
         versions: [ReviewConclusion] = [], matches: [ReviewConclusion] = []) {
        self.ok = ok; self.week = week; self.latest = latest
        self.versions = versions; self.matches = matches
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? false
        week = try c.decodeIfPresent(String.self, forKey: .week) ?? ""
        latest = try c.decodeIfPresent(ReviewConclusion.self, forKey: .latest)
        versions = try c.decodeIfPresent([ReviewConclusion].self, forKey: .versions) ?? []
        matches = try c.decodeIfPresent([ReviewConclusion].self, forKey: .matches) ?? []
    }
}

// MARK: - 复盘聚合读(`GET /review/overview`)

/// 复盘板块里的**一段**。四段形状统一,**各自带 `available` + `unavailableReason`**。
///
/// 🔴 **三态读法,⛔ 不许拿一个总开关罩住四段**:
///   · **有**   → `available == true` + 有内容;
///   · **没有** → `available == true` + 空内容(该段自己的空态文案说清为什么空);
///   · **没取到** → `available == false` + `unavailableReason`(⛔ 不许拿空数组冒充)。
///
/// ⚠ **对账 / 结论两段与校准段的空态服务端刻意判得不一样,⛔ 别"统一"**:
/// 对账与结论缺席 = 输入(券商交割单 / 用户写的结论)**只能由用户给**、系统查过表确实
/// 没有 → 那是**「没有」**→ `available=true` + `detail.found == false`;
/// 校准产物缺席 = **系统自己那一步没跑** → 那是**「没看」**→ `available=false`。
/// 两者给用户的动作完全不同(自己去做 vs 等系统)。
struct ReviewSegment: Codable, Equatable {
    var available: Bool = false
    var unavailableReason: String? = nil
    var label: String = ""
    var asOf: String = ""
    /// **原样透传领域形状**(同 `WeeklyReviewOut.result` 惯例)。
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

    /// `false` = **这周没有**(不是"没查")。⚠ 只在 `available == true` 时才读得出意思。
    var found: Bool? { detail["found"]?.boolValue }
    /// 服务端给的空态说明(如「本周尚未上传交割单 —— …」),原样展示。
    var note: String? { detail["note"]?.stringValue }

    /// 对账段里那份**已落库的周报**解成强类型;`found == false` → `nil`。
    ///
    /// 🔴 **为什么要它**:macOS 对账工作台若只认「本次上传的返回值」,重启 App 就会说
    /// 「还没有对账数据」——而同一份数据服务端明明有。那是把**「没看」讲成「没有」**。
    /// ⛔ **零新增网络调用**:这份 JSON 本来就在 `/review/overview` 的响应里。
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

/// 复盘板块的聚合读。**零现算**:各段全部读已冻结 / 已落盘的产物。
/// 🔴 **本端点一律不 404**(空态走各段 `available=false`)→ 客户端**不需要**为它加任何
/// `mapReason` case。
///
/// 服务端只保留 `reconcile` 与 `conclusions` 两段；K8 校准与观察登记已下架。
struct ReviewOverview: Codable, Equatable {
    var weekStart: String = ""
    var weekEnd: String = ""
    /// ISO 周键(`YYYY-Www`),对账段与结论段按它取。
    var weekKey: String = ""
    var reconcile: ReviewSegment = ReviewSegment()
    var conclusions: ReviewSegment = ReviewSegment()

    static let empty = ReviewOverview()

    enum CodingKeys: String, CodingKey {
        case weekStart, weekEnd, weekKey, reconcile, conclusions
    }

    init(weekStart: String = "", weekEnd: String = "", weekKey: String = "",
         reconcile: ReviewSegment = ReviewSegment(),
         conclusions: ReviewSegment = ReviewSegment()) {
        self.weekStart = weekStart; self.weekEnd = weekEnd; self.weekKey = weekKey
        self.reconcile = reconcile; self.conclusions = conclusions
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        weekStart = try c.decodeIfPresent(String.self, forKey: .weekStart) ?? ""
        weekEnd = try c.decodeIfPresent(String.self, forKey: .weekEnd) ?? ""
        weekKey = try c.decodeIfPresent(String.self, forKey: .weekKey) ?? ""
        reconcile = try c.decodeIfPresent(ReviewSegment.self, forKey: .reconcile) ?? ReviewSegment()
        conclusions = try c.decodeIfPresent(ReviewSegment.self, forKey: .conclusions)
            ?? ReviewSegment()
    }
}
