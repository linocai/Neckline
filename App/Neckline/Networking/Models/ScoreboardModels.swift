//
//  ScoreboardModels.swift
//  Neckline — **成绩板块**的 DTO(V2.5.0 S12 新增,裁定 11:成绩线升为板块)。
//
//  对齐后端 `GET /api/v1/scoreboard/coverage?window=` 与
//  `GET /api/v1/scoreboard/verdicts/{tradeDate}`(逐字段对齐,⛔ 别猜)。
//
//  🔴 **三条成绩线分开存放、互不进入对方的分子分母**(架构 §五)。本文件只装前两条
//  (清单成绩 / 覆盖率);**我的成绩**走复盘线,住 `ReviewModels.swift`,⛔ 不合并。
//
//  🔴 **行业分与选票分必须分两栏,⛔ 不给任何合计字段**(K9 §八 / 架构 §5.1):
//  行业分低是**方向层**的问题,行业分高而选票分低是**选票参数**的问题 ——
//  两者吃的药完全不同。服务端 `scorecard` 存储层刻意没有 `total` / `combined` 一类字段
//  (守门单测锁死),客户端同理:本文件里⛔ 不许出现把两者相加的任何东西。
//
//  🔴 **NULL 不是 0**:`coverageAll == nil` = 昨天还没有清单(上线首日 / 参数未配置的
//  日子);`coverageInPool == nil` = 没有 D−1 的全市场 disposition(边界参数缺失)。
//  ⛔ 客户端必须渲染成「尚不可得」,⛔ 绝不显示成 0%。
//

import Foundation

// MARK: - 覆盖率(架构 §5.2;**这条线不读任何待标定参数**)

/// 一天的覆盖率读数。**口径 = 涨停**,涨停是硬事实 —— 参数标定完成之前它就是那把尺子。
struct CoverageDay: Codable, Equatable, Identifiable {
    var tradeDate: String = ""
    var packVersion: String? = nil
    var limitUpCount: Int = 0
    var limitDownCount: Int = 0
    var zabanCount: Int = 0
    var zabanRate: Double? = nil
    var maxConsecDays: Int? = nil
    var clusterCount: Int = 0
    /// 拿来比的是**哪一天的清单**(D−1)。空 = 那天前一个交易日没有清单。
    var listingTradeDate: String? = nil
    var listingSize: Int? = nil
    var coveredCount: Int? = nil
    /// 🔴 **头条数字**。`nil` = 昨天还没有清单(⛔ 不是 0%)。
    var coverageAll: Double? = nil
    var inPoolDenominator: Int? = nil
    var coveredInPool: Int? = nil
    /// ⚠ 它**依赖边界参数** → 参数缺失时服务端写 NULL,⛔ 客户端不许当 0。
    var coverageInPool: Double? = nil
    /// 当日涨停票的结构性分布(板块 / ST / 申万二级)—— 与参数无关的硬事实。
    var census: NKJSON = .object([:])

    var id: String { tradeDate }

    enum CodingKeys: String, CodingKey {
        case tradeDate, packVersion, limitUpCount, limitDownCount, zabanCount, zabanRate
        case maxConsecDays, clusterCount, listingTradeDate, listingSize, coveredCount
        case coverageAll, inPoolDenominator, coveredInPool, coverageInPool, census
    }

    init(tradeDate: String = "", packVersion: String? = nil, limitUpCount: Int = 0,
         limitDownCount: Int = 0, zabanCount: Int = 0, zabanRate: Double? = nil,
         maxConsecDays: Int? = nil, clusterCount: Int = 0, listingTradeDate: String? = nil,
         listingSize: Int? = nil, coveredCount: Int? = nil, coverageAll: Double? = nil,
         inPoolDenominator: Int? = nil, coveredInPool: Int? = nil,
         coverageInPool: Double? = nil, census: NKJSON = .object([:])) {
        self.tradeDate = tradeDate; self.packVersion = packVersion
        self.limitUpCount = limitUpCount; self.limitDownCount = limitDownCount
        self.zabanCount = zabanCount; self.zabanRate = zabanRate
        self.maxConsecDays = maxConsecDays; self.clusterCount = clusterCount
        self.listingTradeDate = listingTradeDate; self.listingSize = listingSize
        self.coveredCount = coveredCount; self.coverageAll = coverageAll
        self.inPoolDenominator = inPoolDenominator; self.coveredInPool = coveredInPool
        self.coverageInPool = coverageInPool; self.census = census
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        packVersion = try c.decodeIfPresent(String.self, forKey: .packVersion)
        limitUpCount = try c.decodeIfPresent(Int.self, forKey: .limitUpCount) ?? 0
        limitDownCount = try c.decodeIfPresent(Int.self, forKey: .limitDownCount) ?? 0
        zabanCount = try c.decodeIfPresent(Int.self, forKey: .zabanCount) ?? 0
        zabanRate = try c.decodeIfPresent(Double.self, forKey: .zabanRate)
        maxConsecDays = try c.decodeIfPresent(Int.self, forKey: .maxConsecDays)
        clusterCount = try c.decodeIfPresent(Int.self, forKey: .clusterCount) ?? 0
        listingTradeDate = try c.decodeIfPresent(String.self, forKey: .listingTradeDate)
        listingSize = try c.decodeIfPresent(Int.self, forKey: .listingSize)
        coveredCount = try c.decodeIfPresent(Int.self, forKey: .coveredCount)
        coverageAll = try c.decodeIfPresent(Double.self, forKey: .coverageAll)
        inPoolDenominator = try c.decodeIfPresent(Int.self, forKey: .inPoolDenominator)
        coveredInPool = try c.decodeIfPresent(Int.self, forKey: .coveredInPool)
        coverageInPool = try c.decodeIfPresent(Double.self, forKey: .coverageInPool)
        census = try c.decodeIfPresent(NKJSON.self, forKey: .census) ?? .object([:])
    }
}

/// 漏检归因的一条(六值闭合枚举,服务端 `scorecard/coverage.py::_attribute` 是唯一源)。
func nkMissReasonLabel(_ raw: String) -> String {
    switch raw {
    case "no_listing": return "那天还没有清单"
    case "no_disposition": return "那天没有全市场处置记录"
    case "excluded_by_boundary": return "被硬边界排除"
    case "not_recalled": return "四通道都没召回"
    case "recalled_not_seated": return "召回了但没进席"
    case "news_excluded": return "被消息面剔除"
    default: return raw   // ⛔ 不瞎翻译:印出码好过印一句猜的中文
    }
}

/// 一只**没被覆盖**的涨停票。⚠ 这不是在指责系统 —— 它是「昨天为什么没选中这只涨停票」
/// 的**查表结果**(§5.4.8),读法见 `reason`。
struct CoverageMiss: Codable, Equatable, Identifiable {
    var tradeDate: String = ""
    var tsCode: String = ""
    var name: String? = nil
    var board: String? = nil
    var l2Code: String? = nil
    var l2Name: String? = nil
    var consecLimitUpDays: Int? = nil
    var reason: String = ""
    var detail: String? = nil

    var id: String { "\(tradeDate)-\(tsCode)" }
    var displayName: String { (name?.isEmpty == false) ? name! : tsCode }
    var reasonLabel: String { nkMissReasonLabel(reason) }

    enum CodingKeys: String, CodingKey {
        case tradeDate, tsCode, name, board, l2Code, l2Name, consecLimitUpDays, reason, detail
    }

    init(tradeDate: String = "", tsCode: String = "", name: String? = nil, board: String? = nil,
         l2Code: String? = nil, l2Name: String? = nil, consecLimitUpDays: Int? = nil,
         reason: String = "", detail: String? = nil) {
        self.tradeDate = tradeDate; self.tsCode = tsCode; self.name = name; self.board = board
        self.l2Code = l2Code; self.l2Name = l2Name; self.consecLimitUpDays = consecLimitUpDays
        self.reason = reason; self.detail = detail
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name)
        board = try c.decodeIfPresent(String.self, forKey: .board)
        l2Code = try c.decodeIfPresent(String.self, forKey: .l2Code)
        l2Name = try c.decodeIfPresent(String.self, forKey: .l2Name)
        consecLimitUpDays = try c.decodeIfPresent(Int.self, forKey: .consecLimitUpDays)
        reason = try c.decodeIfPresent(String.self, forKey: .reason) ?? ""
        detail = try c.decodeIfPresent(String.self, forKey: .detail)
    }
}

struct CoverageSnapshot: Codable, Equatable {
    var window: Int = 0
    /// 按 `tradeDate` 降序(服务端顺序,⛔ 客户端不重排)。
    var days: [CoverageDay] = []
    /// **最新那一天**的漏检归因逐只清单。
    var latestMisses: [CoverageMiss] = []
    var missReasonCounts: [String: Int] = [:]

    static let empty = CoverageSnapshot()

    enum CodingKeys: String, CodingKey { case window, days, latestMisses, missReasonCounts }

    init(window: Int = 0, days: [CoverageDay] = [], latestMisses: [CoverageMiss] = [],
         missReasonCounts: [String: Int] = [:]) {
        self.window = window; self.days = days
        self.latestMisses = latestMisses; self.missReasonCounts = missReasonCounts
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        window = try c.decodeIfPresent(Int.self, forKey: .window) ?? 0
        days = try c.decodeIfPresent([CoverageDay].self, forKey: .days) ?? []
        latestMisses = try c.decodeIfPresent([CoverageMiss].self, forKey: .latestMisses) ?? []
        missReasonCounts = try c.decodeIfPresent([String: Int].self, forKey: .missReasonCounts) ?? [:]
    }

    var latest: CoverageDay? { days.first }
}

// MARK: - 10:00 结算拍的三分支终值(裁定 10)

/// K9 §6.2 的三分支**终值**。🔴 **唯一权威是 D1 10:00 的结算拍。**
/// ⚠ 「观察」= 10:00 真看过之后的结论,它**不进任何正确率的分子分母**(K9 §八),
/// ⛔ 与「还没定案」(`verdict == nil`)不是一回事。
enum K9Verdict: String, Codable, Equatable, CaseIterable {
    case confirmed
    case rejected
    case observed

    var label: String {
        switch self {
        case .confirmed: return "成立"
        case .rejected: return "放弃"
        case .observed: return "观察"
        }
    }

    var tone: NKAxisTone {
        switch self {
        case .confirmed: return .good
        case .rejected: return .bad
        // 「看不出来」既不是对也不是错 —— 它就是没有结论。
        case .observed: return .neutral
        }
    }
}

/// 定案发生在哪一拍。`auction` = 9:29 竞价那一拍**先到先定**(只可能是「放弃」);
/// `open30` = 10:00 结算拍(三分支终值的唯一权威)。
func nkDecidedStageLabel(_ raw: String?) -> String {
    switch raw {
    case "auction": return "9:29 竞价定案"
    case "open30": return "10:00 结算"
    case .some(let v) where !v.isEmpty: return v
    default: return "尚未定案"
    }
}

struct K9VerdictRow: Codable, Equatable, Identifiable {
    var tsCode: String = ""
    /// 这一只是**哪一天的清单**上的(D0)。
    var d0Date: String = ""
    var pattern: String = ""
    var playbookVersion: Int = 0
    /// 9:29 那一拍的二值裁定(`rejected` / `pending_open`)。⛔ 它不是终值。
    var auctionVerdict: String? = nil
    /// 🔴 三分支**终值**。`nil` = **今天还没定案**(⛔ 不是「观察」)。
    var verdict: K9Verdict? = nil
    /// `nil` = 还没定案。⛔ 别把 `nil` 显示成某一拍。
    var decidedStage: String? = nil
    var auctionReadings: NKJSON = .null
    var open30Readings: NKJSON = .null
    /// 逐条分支留痕(哪一条成立 / 放弃、读数是多少)。
    var branches: [NKJSON] = []
    var settledAt: String? = nil

    var id: String { tsCode }
    /// 🔴 `verdict == nil && decidedStage == nil` = 「今天还没定案」。
    /// **这与「观察」是两句不同的话**:「观察」是 10:00 真看过之后的结论,
    /// 它带着 `decidedStage == "open30"`。⛔ 界面上不许合并。
    var isUndecided: Bool { verdict == nil }

    enum CodingKeys: String, CodingKey {
        case tsCode, d0Date, pattern, playbookVersion, auctionVerdict, verdict
        case decidedStage, auctionReadings, open30Readings, branches, settledAt
    }

    init(tsCode: String = "", d0Date: String = "", pattern: String = "",
         playbookVersion: Int = 0, auctionVerdict: String? = nil, verdict: K9Verdict? = nil,
         decidedStage: String? = nil, auctionReadings: NKJSON = .null,
         open30Readings: NKJSON = .null, branches: [NKJSON] = [], settledAt: String? = nil) {
        self.tsCode = tsCode; self.d0Date = d0Date; self.pattern = pattern
        self.playbookVersion = playbookVersion; self.auctionVerdict = auctionVerdict
        self.verdict = verdict; self.decidedStage = decidedStage
        self.auctionReadings = auctionReadings; self.open30Readings = open30Readings
        self.branches = branches; self.settledAt = settledAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        d0Date = try c.decodeIfPresent(String.self, forKey: .d0Date) ?? ""
        pattern = try c.decodeIfPresent(String.self, forKey: .pattern) ?? ""
        playbookVersion = try c.decodeIfPresent(Int.self, forKey: .playbookVersion) ?? 0
        auctionVerdict = try c.decodeIfPresent(String.self, forKey: .auctionVerdict)
        verdict = try? c.decodeIfPresent(K9Verdict.self, forKey: .verdict)
        decidedStage = try c.decodeIfPresent(String.self, forKey: .decidedStage)
        auctionReadings = try c.decodeIfPresent(NKJSON.self, forKey: .auctionReadings) ?? .null
        open30Readings = try c.decodeIfPresent(NKJSON.self, forKey: .open30Readings) ?? .null
        branches = try c.decodeIfPresent([NKJSON].self, forKey: .branches) ?? []
        settledAt = try c.decodeIfPresent(String.self, forKey: .settledAt)
    }
}

struct K9VerdictsSnapshot: Codable, Equatable {
    var tradeDate: String = ""
    var verdicts: [K9VerdictRow] = []

    static let empty = K9VerdictsSnapshot()

    enum CodingKeys: String, CodingKey { case tradeDate, verdicts }

    init(tradeDate: String = "", verdicts: [K9VerdictRow] = []) {
        self.tradeDate = tradeDate; self.verdicts = verdicts
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        verdicts = try c.decodeIfPresent([K9VerdictRow].self, forKey: .verdicts) ?? []
    }

    func count(_ v: K9Verdict) -> Int { verdicts.filter { $0.verdict == v }.count }
    var undecidedCount: Int { verdicts.filter(\.isUndecided).count }

    /// 已定案的三分支明细(成立 / 放弃 / 观察),**「还没定案」不在其中**。
    /// ⚠ 这是**明细**,⛔ 不是成立率 —— 五指标的结算归 S17(见 `NKListingScorecard`)。
    var decided: [K9VerdictRow] { verdicts.filter { !$0.isUndecided } }
}

// MARK: - 清单成绩五指标(K9 §八)—— **本版只有壳,结算归 S17**

/// 五指标的展示骨架。
///
/// 🔴 **行业分与选票分永远是两栏,⛔ 本类型不提供、也不许有任何合计口径** ——
/// 服务端 `scorecard` 存储层刻意没有 `total` / `combined` 字段(架构 §5.1 / K9 §八),
/// 客户端也⛔ 不许自己相加。这不是排版洁癖:行业分低是**方向层**的问题,
/// 行业分高而选票分低是**选票参数**的问题,两者吃的药完全不同。
///
/// ⚠ **本版(V2.5.0)只有壳,没有数**,这是**计划中的顺序**不是漏做:
/// 五指标的结算是 **S17**,它排在批 B(等参数标定完成、用户确认之后),
/// 依赖 D+1~D+4 的行情回填(`k9_followups`)与 10:00 结算终值。
/// 服务端因此还**没有** `GET /scoreboard/listing` 这条路由 —— 客户端⛔ 不许去调
/// 一个不存在的端点(契约对拍会当场红),界面如实说「这条线还没开始结算」。
enum NKListingScorecard {
    /// 五个指标各自在问什么(K9 §八 表格逐字)。⛔ 别改写成对仗好听但讲不清的句子。
    static let metrics: [(name: String, question: String)] = [
        ("成立率", "预案条件的松紧 —— 长期 1/20 成立说明卡太死,15/20 说明形同虚设"),
        ("兑现率", "选票与压力位判断准不准"),
        ("错杀率", "预案是否过严,把好票劝退了"),
        ("行业分", "方向对不对"),
        ("选票分", "票挑得好不好"),
    ]

    /// 🔴 两栏永远分开呈现的那两项。⛔ **不给合计**。
    static let splitPair: (industry: String, pick: String) = ("行业分", "选票分")

    /// 为什么现在还没有数(如实说,⛔ 不写「暂无数据」——那会把「还没做」讲成「查过了没有」)。
    static let notSettledNote =
        "清单成绩五指标要 D+1~D+4 的行情回填与 10:00 结算终值才算得出,"
        + "结算归 S17(排在参数标定完成之后)。现在能看的是下面两块:"
        + "**10:00 结算拍的三分支终值**(成立率的明细)与**覆盖率**。"
}
