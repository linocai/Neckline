//
//  K9Models.swift
//  Neckline — 选股板块 DTO。
//
//  对齐后端 `GET /api/v1/selection/latest` · `/selection/{tradeDate}` ·
//  `/selection/{tradeDate}/stock/{tsCode}` · `POST .../playbook`
//  (`neckline/api/app.py::_selection_payload` / `_selection_stocks`,逐字段对齐,⛔ 别猜)。
//
//  🔴 **三态是三个值,⛔ 不许合并**(裁定 5 / 架构 §3.5):
//    · `hasList` 今天有这些 —— 跑通了、有清单;
//    · `empty`   今天没有   —— **跑通了、结果是空的,可以被信任**;
//    · `notRun`  今天没跑成 —— 系统没工作(数据未到齐 / 参数未配置 / 链路异常),
//      `gaps` 逐条说明缺什么,`listingSize` 是 **null**。
//  ⛔ 把 `notRun` 渲染成「今天没有」= 把「没看」讲成「没有」;
//  ⛔ 把 `listingSize == nil` 显示成 0 同理。
//
//  🔴 **双日期契约不许退化**(LRN-20260816-001):`reportDate` 管标题 / 推送 / 可见身份,
//  `tradeDate` 管 EOD 读数 / 清单 / 预案 / 审计键。周日报告两者不同,⛔ 别只取一个。
//
//  🔴 **命名铁律(裁定 1)**:`upsideRoomMechPct`(**上方机械空间**,机械算出、排序用、
//  K9 第三层)与 `PlaybookLevels.firstResistance`(**第一压力位**,LLM 逐票判断、预案用、
//  K9 第四层)是**两个不同的量** —— 名字分开、**永不互相顶替**,⛔ 也不另造第三个量。
//

import Foundation

// MARK: - 报告三态(闭合枚举 + 全映射,⛔ 无 fallback 分支)

/// 报告三态。服务端 `neckline/report/state.py::ReportState` 是唯一源。
///
/// ⚠ **未识别值不静默归一成任何一态** —— 服务端加了第四态而客户端还没跟时,
/// 把它显示成「今天没有」是最坏的一种谎。解不出 → `nil` → 调用方按「读不出这份报告」处理。
enum K9ReportState: String, Codable, Equatable, CaseIterable {
    case hasList = "has_list"
    case empty
    case notRun = "not_run"

    /// 首行前缀的**兜底**文案。⚠ 正文首行一律优先用服务端给的 `headline`
    /// (它带着「N 只(严格 a / 放宽 b)」与逐条缺口),这里只在 headline 为空时用。
    var fallbackHeadline: String {
        switch self {
        case .hasList: return "今天有这些"
        case .empty:   return "今天没有"
        case .notRun:  return "今天没跑成"
        }
    }

    /// 这一态该用什么颜色说话。`empty` 是**中性**的 —— 它是一个可以被信任的结论,
    /// ⛔ 不许标成警告色(那会把「今天确实没有」画成「出事了」)。
    var tone: NKAxisTone {
        switch self {
        case .hasList: return .good
        case .empty:   return .neutral
        case .notRun:  return .warn
        }
    }
}

// MARK: - 形态标注(K9 §三 四个召回通道)

/// 形态码 → 中文展示名。**唯一**展示层换算源(沿 `nkBoardLabel` 先例:服务端只发码、
/// 中文在客户端换算、未识别值原样透传,⛔ 不瞎翻译)。
func nkPatternLabel(_ raw: String) -> String {
    switch raw {
    case "p1": return "放量启动"
    case "p2": return "超跌反弹"
    case "p3": return "热门强博弈"
    case "p4": return "资金领先价格"
    default: return raw
    }
}

func nkRiskLabel(_ raw: String) -> String {
    switch raw {
    case "high_position_drawdown": return "高位回撤风险"
    case "high_stall": return "高位滞涨风险"
    case "giant_breakdown": return "巨量破位风险"
    case "limit_down_contest": return "跌停博弈风险"
    default: return raw
    }
}

func nkEvidenceLabel(_ raw: String) -> String {
    switch raw {
    case "limitUp": return "涨停证据"
    case "limitDown": return "跌停博弈"
    case "topList": return "龙虎榜"
    case "controlPause": return "控盘停顿"
    case "reversalSecondWave": return "反转二波"
    case "lowRecovery": return "低点回收"
    case "downsideDeceleration": return "跌速放缓"
    case "effectiveTurnover": return "有效换手"
    default: return raw
    }
}

/// 成色标注(K9 §五-7)。`strict` = 严格档、`relaxed` = 放宽档。
/// 🔴 **这一栏必须看得见**:15 只全出自严格档,与 10 只里 8 只靠放宽凑上,是两种
/// 完全不同的日子(K9 §五-7 逐字)。⛔ 不许在界面上省掉它。
func nkTierLabel(_ raw: String) -> String {
    switch raw {
    case "strict": return "严格"
    case "relaxed": return "放宽"
    default: return raw
    }
}

/// 席位来源(K9 §五-2 / §五-3)。`floor` = 保底席、`free` = 自由竞争。
/// `nil` = 未入席(后备票)——⛔ 别把它显示成某种席位。
func nkSeatKindLabel(_ raw: String?) -> String {
    switch raw {
    case "floor": return "保底席"
    case "free": return "自由席"
    case .some(let v) where !v.isEmpty: return v
    default: return ""
    }
}

/// 消息面三态(S9)。🔴 **`unverified` 单独占一格,⛔ 不许折成「无异常」**:
/// 它是「**没查成**」(没有 provider / 调用失败 / 模型没按格式收尾),
/// 与「查过了、干净」是两件事。`nil` = 解释层根本没跑过这一只。
func nkNewsStateLabel(_ raw: String?) -> String {
    switch raw {
    case "clean": return "消息面已核实"
    case "excluded": return "消息面命中剔除项"
    case "unverified": return "消息面未核实"
    case .some(let v) where !v.isEmpty: return v
    default: return "解释层未跑"
    }
}

func nkNewsStateTone(_ raw: String?) -> NKAxisTone {
    switch raw {
    case "clean": return .good
    case "excluded": return .bad
    // 「没查成」不是「没事」,也不是「有事」—— 它是一个要人知道的缺口。
    case "unverified": return .warn
    default: return .neutral
    }
}

// MARK: - 预案(K9 §六;结构化、可机械求值)

/// 三个价位(K9 §6.1,D0 冻结,**全是 LLM 判断**)。
/// ⛔ 与 `upsideRoomMechPct` 永不互相顶替(裁定 1)。
struct PlaybookLevels: Codable, Equatable {
    var firstResistance: Double = 0     // 第一压力位 = 预期离场价 = 判断对错的标准
    var secondResistance: Double = 0    // 第二压力位 = 超预期时的第二目标
    var invalidation: Double = 0        // 失效位 = 跌破即证明原判断错误

    enum CodingKeys: String, CodingKey { case firstResistance, secondResistance, invalidation }

    init(firstResistance: Double = 0, secondResistance: Double = 0, invalidation: Double = 0) {
        self.firstResistance = firstResistance
        self.secondResistance = secondResistance
        self.invalidation = invalidation
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        firstResistance = try c.decodeIfPresent(Double.self, forKey: .firstResistance) ?? 0
        secondResistance = try c.decodeIfPresent(Double.self, forKey: .secondResistance) ?? 0
        invalidation = try c.decodeIfPresent(Double.self, forKey: .invalidation) ?? 0
    }

    /// K9 §6.1 的赔率 = 当前价到第一压力位的距离 ÷ 当前价到失效位的距离。
    /// 分母 ≤ 0(收盘已在失效位下方)→ `nil`,⛔ 不拿一个负数冒充赔率。
    func odds(close: Double) -> Double? {
        let down = close - invalidation
        guard down > 0.0001 else { return nil }
        return (firstResistance - close) / down
    }
}

/// 一条判定条件(服务端 `playbook/model.py::Condition`,语法**闭合、无算术**)。
struct PlaybookCondition: Codable, Equatable, Identifiable {
    var op: String = ""
    var lhs: String = ""
    /// `rhs` 是**有限数值或另一个 MetricRef**(服务端语法允许这两种,⛔ 没有第三种)。
    var rhsNumber: Double? = nil
    var rhsMetric: String? = nil

    var id: String { "\(lhs)\(op)\(rhsMetric ?? String(describing: rhsNumber))" }

    enum CodingKeys: String, CodingKey { case op, lhs, rhs }

    init(op: String = "", lhs: String = "", rhsNumber: Double? = nil, rhsMetric: String? = nil) {
        self.op = op; self.lhs = lhs; self.rhsNumber = rhsNumber; self.rhsMetric = rhsMetric
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        op = try c.decodeIfPresent(String.self, forKey: .op) ?? ""
        lhs = try c.decodeIfPresent(String.self, forKey: .lhs) ?? ""
        if let n = try? c.decodeIfPresent(Double.self, forKey: .rhs) {
            rhsNumber = n
        } else {
            rhsMetric = try? c.decodeIfPresent(String.self, forKey: .rhs)
        }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(op, forKey: .op)
        try c.encode(lhs, forKey: .lhs)
        if let m = rhsMetric { try c.encode(m, forKey: .rhs) } else { try c.encode(rhsNumber ?? 0, forKey: .rhs) }
    }

    /// 人读一行。⚠ 这是**渲染**,⛔ 不是条件本身的载体 —— 求值永远在服务端,
    /// 客户端从不解析这句话、也从不自己求值(架构 §四:判断已经在 D0 完成并冻结)。
    var text: String {
        let right = rhsMetric.map(nkMetricRefLabel) ?? NKFmt.price(rhsNumber ?? 0)
        return "\(nkMetricRefLabel(lhs)) \(op) \(right)"
    }
}

/// `MetricRef` 闭合枚举的展示层换算(服务端 `playbook/model.py::MetricRef` 九个成员)。
/// 未识别值原样透传 —— 服务端加了第十个而客户端没跟时,印出码总好过印一句瞎翻译。
func nkMetricRefLabel(_ raw: String) -> String {
    switch raw {
    case "auction_price": return "竞价价"
    case "auction_gap_pct": return "竞价高开幅度"
    case "open_price": return "开盘价"
    case "gap_pct": return "高开幅度"
    case "first30_low": return "前 30 分钟最低价"
    case "first30_high": return "前 30 分钟最高价"
    case "prev_close": return "昨收"
    case "prev_low": return "昨日最低"
    case "prev_high": return "昨日最高"
    default: return raw
    }
}

/// 一条分支 = 若干条件的**合取**(K9 §6.3 的骨架里全是「且」)。
struct PlaybookBranch: Codable, Equatable, Identifiable {
    var name: String = ""
    var all: [PlaybookCondition] = []

    var id: String { name }

    enum CodingKeys: String, CodingKey { case name, all }

    init(name: String = "", all: [PlaybookCondition] = []) { self.name = name; self.all = all }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        all = try c.decodeIfPresent([PlaybookCondition].self, forKey: .all) ?? []
    }
}

/// 一只票的 D0 冻结预案。**B 类冻结快照**:随 D0 冻住、服务端升级永不给它补新键 →
/// 手写 `init(from:)` + 全字段 `decodeIfPresent`(⛔ 合成 Codable 会让老预案解不出)。
///
/// 🔴 **append-only 版本化**:用户改一次就多一版,`version` 单调递增,老版本一个字不动
/// (K9 §6.4「最终确认由我盘后逐只过目,可修改」)。
struct Playbook: Codable, Equatable {
    var tradeDate: String = ""
    var tsCode: String = ""
    var pattern: String = ""
    var levels: PlaybookLevels = PlaybookLevels()
    var branches: [PlaybookBranch] = []
    /// 恒「观察」——三分支里那个**不需要条件**的默认分支(K9 §6.2)。
    var defaultBranch: String = "观察"
    var version: Int = 1
    /// `llm` = 预案层填的;`user` = 用户盘后过目后改的。
    var source: String = "llm"
    var filledBy: String = ""
    var filledAt: String = ""

    enum CodingKeys: String, CodingKey {
        case tradeDate, tsCode, pattern, levels, branches, version, source, filledBy, filledAt
        case defaultBranch = "default"
    }

    init(tradeDate: String = "", tsCode: String = "", pattern: String = "",
         levels: PlaybookLevels = PlaybookLevels(), branches: [PlaybookBranch] = [],
         defaultBranch: String = "观察", version: Int = 1, source: String = "llm",
         filledBy: String = "", filledAt: String = "") {
        self.tradeDate = tradeDate; self.tsCode = tsCode; self.pattern = pattern
        self.levels = levels; self.branches = branches; self.defaultBranch = defaultBranch
        self.version = version; self.source = source
        self.filledBy = filledBy; self.filledAt = filledAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        pattern = try c.decodeIfPresent(String.self, forKey: .pattern) ?? ""
        levels = try c.decodeIfPresent(PlaybookLevels.self, forKey: .levels) ?? PlaybookLevels()
        branches = try c.decodeIfPresent([PlaybookBranch].self, forKey: .branches) ?? []
        defaultBranch = try c.decodeIfPresent(String.self, forKey: .defaultBranch) ?? "观察"
        version = try c.decodeIfPresent(Int.self, forKey: .version) ?? 1
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? "llm"
        filledBy = try c.decodeIfPresent(String.self, forKey: .filledBy) ?? ""
        filledAt = try c.decodeIfPresent(String.self, forKey: .filledAt) ?? ""
    }

    var isUserEdited: Bool { source == "user" }
    /// `version` 是这一只股票的 append-only 预案修订序号，不是 K9 策略版本。
    /// 产品界面必须把命名空间说清楚，避免 `v1` 被误读成 K9-v1。
    var revisionLabel: String { "预案第 \(version) 版" }
    func branch(named name: String) -> PlaybookBranch? { branches.first { $0.name == name } }
    var confirmBranch: PlaybookBranch? { branch(named: "成立") }
    var rejectBranch: PlaybookBranch? { branch(named: "放弃") }
}

// MARK: - 清单上的一只票(`/selection/*` 响应的 `stocks[]`)

/// 今日清单里的一只票 —— §5.11 要求每只带:**形态标注 / 上方机械空间 / 三个价位 /
/// 三分支预案摘要**。
///
/// ⚠ **三样缺席各自如实标,⛔ 不许合并成一句「暂无」**:
///   · `upsideRoomMechPct == nil` —— 这只票只被 p2 / p4 召回,**本形态不看这一项**
///     (K9 §3.3 / §3.5 的强度性里没有它),⛔ **不是**「上方没有空间」;
///   · `playbook == nil` —— 那天没给这一只冻预案 → **明早核对不了它**;
///   · `newsState == nil` —— 解释层没跑过这一只(与 `"unverified"`「查过没查成」不是一回事)。
struct K9Stock: Codable, Equatable, Identifiable {
    var tsCode: String = ""
    var name: String? = nil
    var swL2Code: String? = nil
    var swL2Name: String? = nil
    var patterns: [String] = []
    var primaryPattern: String = ""
    var tier: String = ""
    var seatKind: String? = nil
    var rank: Int = 0
    /// D0 冻结事实中的收盘价；不是实时行情。
    var referenceClose: Double? = nil
    /// 解释层既有画像中的一句话，缺席时不编造。
    var oneLineProfile: String? = nil
    /// **上方机械空间**(裁定 1:机械算出的「收盘价距过去 N 日最高价」的比例)。
    /// ⛔ 它**不是**第一压力位,⛔ 永不互相顶替。
    var upsideRoomMechPct: Double? = nil
    var playbook: Playbook? = nil
    var newsState: String? = nil
    var newsCategory: String? = nil
    var klineComment: String? = nil
    var explainOk: Bool? = nil
    var evidence: NKJSON = .object([:])
    var risks: [String] = []

    var id: String { tsCode }
    var displayName: String { (name?.isEmpty == false) ? name! : tsCode }

    enum CodingKeys: String, CodingKey {
        case tsCode, name, swL2Code, swL2Name, patterns, primaryPattern, tier, seatKind, rank
        case referenceClose, oneLineProfile
        case upsideRoomMechPct, playbook, newsState, newsCategory, klineComment, explainOk
        case evidence, risks
    }

    init(tsCode: String = "", name: String? = nil, swL2Code: String? = nil,
         swL2Name: String? = nil, patterns: [String] = [], primaryPattern: String = "",
         tier: String = "", seatKind: String? = nil, rank: Int = 0,
         referenceClose: Double? = nil, oneLineProfile: String? = nil,
         upsideRoomMechPct: Double? = nil, playbook: Playbook? = nil,
         newsState: String? = nil, newsCategory: String? = nil,
         klineComment: String? = nil, explainOk: Bool? = nil,
         evidence: NKJSON = .object([:]), risks: [String] = []) {
        self.tsCode = tsCode; self.name = name; self.swL2Code = swL2Code
        self.swL2Name = swL2Name; self.patterns = patterns; self.primaryPattern = primaryPattern
        self.tier = tier; self.seatKind = seatKind; self.rank = rank
        self.referenceClose = referenceClose; self.oneLineProfile = oneLineProfile
        self.upsideRoomMechPct = upsideRoomMechPct; self.playbook = playbook
        self.newsState = newsState; self.newsCategory = newsCategory
        self.klineComment = klineComment; self.explainOk = explainOk
        self.evidence = evidence; self.risks = risks
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name)
        swL2Code = try c.decodeIfPresent(String.self, forKey: .swL2Code)
        swL2Name = try c.decodeIfPresent(String.self, forKey: .swL2Name)
        patterns = try c.decodeIfPresent([String].self, forKey: .patterns) ?? []
        primaryPattern = try c.decodeIfPresent(String.self, forKey: .primaryPattern) ?? ""
        tier = try c.decodeIfPresent(String.self, forKey: .tier) ?? ""
        seatKind = try c.decodeIfPresent(String.self, forKey: .seatKind)
        rank = try c.decodeIfPresent(Int.self, forKey: .rank) ?? 0
        referenceClose = try c.decodeIfPresent(Double.self, forKey: .referenceClose)
        oneLineProfile = try c.decodeIfPresent(String.self, forKey: .oneLineProfile)
        upsideRoomMechPct = try c.decodeIfPresent(Double.self, forKey: .upsideRoomMechPct)
        playbook = try c.decodeIfPresent(Playbook.self, forKey: .playbook)
        newsState = try c.decodeIfPresent(String.self, forKey: .newsState)
        newsCategory = try c.decodeIfPresent(String.self, forKey: .newsCategory)
        klineComment = try c.decodeIfPresent(String.self, forKey: .klineComment)
        explainOk = try c.decodeIfPresent(Bool.self, forKey: .explainOk)
        evidence = try c.decodeIfPresent(NKJSON.self, forKey: .evidence) ?? .object([:])
        risks = try c.decodeIfPresent([String].self, forKey: .risks) ?? []
    }
}

// MARK: - 一份选股报告(`GET /selection/latest` · `/selection/{tradeDate}`)

/// 报告快照。**B 类冻结件**(`k9_reports` 一行随发布冻住)→ 手写 `init(from:)`。
struct SelectionSnapshot: Codable, Equatable {
    /// `nil` = 服务端给了一个连三态都解不出的响应 → 调用方按「读不出这份报告」处理,
    /// ⛔ 不许当成任何一态。
    var state: K9ReportState? = nil
    var reportDate: String = ""
    var tradeDate: String = ""
    var headline: String = ""
    var gaps: [String] = []
    var strategy: String = ""
    var strategyVersion: String = ""
    var paramsPackageVersion: String? = nil
    var packId: String? = nil
    var packVersion: String? = nil
    /// 🔴 `nil` ≠ 0:`notRun` 的日子这里就是 `nil`(「今天没跑成」没有清单大小可言)。
    var listingSize: Int? = nil
    var strictCount: Int? = nil
    var relaxedCount: Int? = nil
    var generatedAt: String = ""
    var markdown: String = ""
    /// 结构化完整版(默认折叠,展开可整段复制到聊天框 —— §5.10 两层视图)。
    var structured: NKJSON = .object([:])
    var directionSnapshot: NKJSON? = nil
    var marketSnapshot: NKJSON? = nil
    var coverageSnapshot: NKJSON? = nil
    var copyText: String = ""
    var stocks: [K9Stock] = []

    static let notLoaded = SelectionSnapshot(
        state: nil, headline: "还没连上服务端", gaps: ["本次启动尚未成功拉过报告"])

    enum CodingKeys: String, CodingKey {
        case state, reportDate, tradeDate, headline, gaps, strategy, strategyVersion, paramsPackageVersion
        case packId, packVersion, listingSize, strictCount, relaxedCount, generatedAt
        case markdown, structured, copyText, stocks
        case directionSnapshot = "direction"
        case marketSnapshot = "market"
        case coverageSnapshot = "coverage"
    }

    init(state: K9ReportState? = nil, reportDate: String = "", tradeDate: String = "",
         headline: String = "", gaps: [String] = [], strategy: String = "",
         strategyVersion: String = "",
         paramsPackageVersion: String? = nil, packId: String? = nil, packVersion: String? = nil,
         listingSize: Int? = nil, strictCount: Int? = nil, relaxedCount: Int? = nil,
         generatedAt: String = "", markdown: String = "",
         structured: NKJSON = .object([:]), directionSnapshot: NKJSON? = nil,
         marketSnapshot: NKJSON? = nil, coverageSnapshot: NKJSON? = nil,
         copyText: String = "", stocks: [K9Stock] = []) {
        self.state = state; self.reportDate = reportDate; self.tradeDate = tradeDate
        self.headline = headline; self.gaps = gaps; self.strategy = strategy
        self.strategyVersion = strategyVersion
        self.paramsPackageVersion = paramsPackageVersion; self.packId = packId
        self.packVersion = packVersion; self.listingSize = listingSize
        self.strictCount = strictCount; self.relaxedCount = relaxedCount
        self.generatedAt = generatedAt; self.markdown = markdown
        self.structured = structured; self.stocks = stocks
        self.directionSnapshot = directionSnapshot; self.marketSnapshot = marketSnapshot
        self.coverageSnapshot = coverageSnapshot; self.copyText = copyText
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // ⚠ 未识别的 state 值 → `nil`(⛔ 不静默归成三态里的任何一个,见文件头)。
        state = try? c.decodeIfPresent(K9ReportState.self, forKey: .state)
        reportDate = try c.decodeIfPresent(String.self, forKey: .reportDate) ?? ""
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        headline = try c.decodeIfPresent(String.self, forKey: .headline) ?? ""
        gaps = try c.decodeIfPresent([String].self, forKey: .gaps) ?? []
        strategy = try c.decodeIfPresent(String.self, forKey: .strategy) ?? ""
        strategyVersion = try c.decodeIfPresent(String.self, forKey: .strategyVersion) ?? ""
        paramsPackageVersion = try c.decodeIfPresent(String.self, forKey: .paramsPackageVersion)
        packId = try c.decodeIfPresent(String.self, forKey: .packId)
        packVersion = try c.decodeIfPresent(String.self, forKey: .packVersion)
        listingSize = try c.decodeIfPresent(Int.self, forKey: .listingSize)
        strictCount = try c.decodeIfPresent(Int.self, forKey: .strictCount)
        relaxedCount = try c.decodeIfPresent(Int.self, forKey: .relaxedCount)
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt) ?? ""
        markdown = try c.decodeIfPresent(String.self, forKey: .markdown) ?? ""
        structured = try c.decodeIfPresent(NKJSON.self, forKey: .structured) ?? .object([:])
        directionSnapshot = try c.decodeIfPresent(NKJSON.self, forKey: .directionSnapshot)
        marketSnapshot = try c.decodeIfPresent(NKJSON.self, forKey: .marketSnapshot)
        coverageSnapshot = try c.decodeIfPresent(NKJSON.self, forKey: .coverageSnapshot)
        copyText = try c.decodeIfPresent(String.self, forKey: .copyText) ?? ""
        stocks = try c.decodeIfPresent([K9Stock].self, forKey: .stocks) ?? []
    }

    /// 首行:服务端给的 `headline` 优先(它带着「N 只(严格 a / 放宽 b)」与逐条缺口)。
    var headlineText: String {
        if !headline.isEmpty { return headline }
        return state?.fallbackHeadline ?? "读不出这份报告"
    }

    var tone: NKAxisTone { state?.tone ?? .warn }

    /// 只有报告明确把“参数未配置”列为失败原因时，界面才可以这么说。
    /// 没有任何报告、网络失败或其它链路错误都不能冒充成参数缺失。
    var parameterPackWasMissing: Bool {
        headline.contains("参数未配置") || gaps.contains { $0.contains("参数未配置") }
    }

    /// 方向背景(事实层的 LLM 旁路,架构 §八)。⚠ **不参与筛选、不参与排序、
    /// 不影响任何机械决策** —— 界面上必须把这句话说出口,⛔ 别让它看起来像一条选股依据。
    /// `nil` = 当日方向旁路没有生成可用内容；不代表市场“没有方向”。
    var direction: NKJSON? {
        if let directionSnapshot, !directionSnapshot.isNull { return directionSnapshot }
        guard let d = structured["direction"], !d.isNull else { return nil }
        return d
    }

    /// 市场事实(涨停分布 / 连板高度 / 炸板率 / 全市场中位涨幅…)。
    var market: NKJSON? {
        if let marketSnapshot, !marketSnapshot.isNull { return marketSnapshot }
        guard let m = structured["market"], !m.isNull, (m.objectValue?.isEmpty == false) else { return nil }
        return m
    }

    /// 覆盖率成绩线的当日读数(⚠ 完整的覆盖率在**成绩**板块,这里只是报告里的一段)。
    var coverage: NKJSON? {
        if let coverageSnapshot, !coverageSnapshot.isNull { return coverageSnapshot }
        guard let c = structured["coverage"], !c.isNull else { return nil }
        return c
    }
}

// MARK: - 个股详情(`GET /selection/{tradeDate}/stock/{tsCode}`)

/// 解释层给的一只票的资料(S9)。在线 API 统一使用 camelCase；数据库内部键不外泄。
struct K9ExplainNote: Codable, Equatable {
    var tsCode: String = ""
    /// 五句话画像(`company` / `industryContext` / `position` / `recent` / `klineComment`)。
    var profile: NKJSON = .object([:])
    var klineComment: String? = nil
    var newsState: String? = nil
    var newsCategory: String? = nil
    var news: NKJSON = .object([:])
    /// `false` = 那一只的资料聚合**没跑成** —— ⛔ 别把空白当成「这只票没什么可说的」。
    var llmOk: Bool = false
    var filledBy: String? = nil
    var createdAt: String = ""

    enum CodingKeys: String, CodingKey {
        case tsCode
        case profile
        case klineComment
        case newsState
        case newsCategory
        case news
        case llmOk
        case filledBy
        case createdAt
    }

    init(tsCode: String = "", profile: NKJSON = .object([:]), klineComment: String? = nil,
         newsState: String? = nil, newsCategory: String? = nil, news: NKJSON = .object([:]),
         llmOk: Bool = false, filledBy: String? = nil, createdAt: String = "") {
        self.tsCode = tsCode; self.profile = profile; self.klineComment = klineComment
        self.newsState = newsState; self.newsCategory = newsCategory; self.news = news
        self.llmOk = llmOk; self.filledBy = filledBy; self.createdAt = createdAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        profile = try c.decodeIfPresent(NKJSON.self, forKey: .profile) ?? .object([:])
        klineComment = try c.decodeIfPresent(String.self, forKey: .klineComment)
        newsState = try c.decodeIfPresent(String.self, forKey: .newsState)
        newsCategory = try c.decodeIfPresent(String.self, forKey: .newsCategory)
        news = try c.decodeIfPresent(NKJSON.self, forKey: .news) ?? .object([:])
        llmOk = try c.decodeIfPresent(Bool.self, forKey: .llmOk) ?? false
        filledBy = try c.decodeIfPresent(String.self, forKey: .filledBy)
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
    }

    /// 五句话画像按**固定顺序**取(⛔ 不按字典序 —— 那会把「它是什么公司」排到中间）。
    static let profileOrder: [(key: String, label: String)] = [
        ("company", "公司是什么"),
        ("industryContext", "在行业里的处境"),
        ("position", "位置与结构"),
        ("recent", "近期表现"),
    ]

    var profileRows: [(label: String, text: String)] {
        Self.profileOrder.compactMap { item in
            guard let s = profile[item.key]?.stringValue, !s.isEmpty else { return nil }
            return (label: item.label, text: s)
        }
    }
}

/// 清单条目的身份信息(个股详情响应里的 `entry` 段)。
struct K9StockEntry: Codable, Equatable {
    var name: String? = nil
    var patterns: [String] = []
    var primaryPattern: String = ""
    var tier: String = ""
    var seatKind: String? = nil
    var rank: Int = 0
    var swL2Code: String? = nil
    var swL2Name: String? = nil

    enum CodingKeys: String, CodingKey {
        case name, patterns, primaryPattern, tier, seatKind, rank, swL2Code, swL2Name
    }

    init(name: String? = nil, patterns: [String] = [], primaryPattern: String = "",
         tier: String = "", seatKind: String? = nil, rank: Int = 0,
         swL2Code: String? = nil, swL2Name: String? = nil) {
        self.name = name; self.patterns = patterns; self.primaryPattern = primaryPattern
        self.tier = tier; self.seatKind = seatKind; self.rank = rank
        self.swL2Code = swL2Code; self.swL2Name = swL2Name
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name)
        patterns = try c.decodeIfPresent([String].self, forKey: .patterns) ?? []
        primaryPattern = try c.decodeIfPresent(String.self, forKey: .primaryPattern) ?? ""
        tier = try c.decodeIfPresent(String.self, forKey: .tier) ?? ""
        seatKind = try c.decodeIfPresent(String.self, forKey: .seatKind)
        rank = try c.decodeIfPresent(Int.self, forKey: .rank) ?? 0
        swL2Code = try c.decodeIfPresent(String.self, forKey: .swL2Code)
        swL2Name = try c.decodeIfPresent(String.self, forKey: .swL2Name)
    }
}

/// 个股详情 = **解释层资料 + 日K 评价 + 完整预案(全部版本)**(§5.11)。
struct K9StockDetail: Codable, Equatable {
    var tradeDate: String = ""
    var tsCode: String = ""
    var entry: K9StockEntry = K9StockEntry()
    /// `nil` = 那天解释层没跑过 / 这一只没跑成 —— ⛔ 别显示成「这只票没什么可说的」。
    var explain: K9ExplainNote? = nil
    /// `nil` = 那天没给这一只冻预案 → **明早核对不了它**。
    var playbook: Playbook? = nil
    /// 全部版本(升序)。用户改过几次、每次改了什么,在这里看得见(append-only)。
    var playbookVersions: [Playbook] = []
    /// 改预案要填哪几个数(**服务端下发**,见 `PlaybookSlot`)。空 = 界面不给改。
    var playbookSlots: [PlaybookSlot] = []

    enum CodingKeys: String, CodingKey {
        case tradeDate, tsCode, entry, explain, playbook, playbookVersions, playbookSlots
    }

    init(tradeDate: String = "", tsCode: String = "", entry: K9StockEntry = K9StockEntry(),
         explain: K9ExplainNote? = nil, playbook: Playbook? = nil,
         playbookVersions: [Playbook] = [], playbookSlots: [PlaybookSlot] = []) {
        self.tradeDate = tradeDate; self.tsCode = tsCode; self.entry = entry
        self.explain = explain; self.playbook = playbook
        self.playbookVersions = playbookVersions; self.playbookSlots = playbookSlots
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        entry = try c.decodeIfPresent(K9StockEntry.self, forKey: .entry) ?? K9StockEntry()
        explain = try? c.decodeIfPresent(K9ExplainNote.self, forKey: .explain)
        playbook = try c.decodeIfPresent(Playbook.self, forKey: .playbook)
        playbookVersions = try c.decodeIfPresent([Playbook].self, forKey: .playbookVersions) ?? []
        playbookSlots = try c.decodeIfPresent([PlaybookSlot].self, forKey: .playbookSlots) ?? []
    }
}

/// `POST /selection/{date}/stock/{code}/playbook` 的返回。
struct PlaybookSaveResult: Codable, Equatable {
    var tradeDate: String = ""
    var tsCode: String = ""
    var version: Int = 0
    var playbook: Playbook = Playbook()

    enum CodingKeys: String, CodingKey { case tradeDate, tsCode, version, playbook }

    init(tradeDate: String = "", tsCode: String = "", version: Int = 0,
         playbook: Playbook = Playbook()) {
        self.tradeDate = tradeDate; self.tsCode = tsCode
        self.version = version; self.playbook = playbook
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        version = try c.decodeIfPresent(Int.self, forKey: .version) ?? 0
        playbook = try c.decodeIfPresent(Playbook.self, forKey: .playbook) ?? Playbook()
    }
}

// MARK: - 预案修改入口的数值槽位(骨架**不可改**,能改的只有方括号里的数)

/// 一个待填的数值位。**唯一源在服务端** `playbook/skeleton.py`,随
/// `GET /selection/{date}/stock/{code}` 的 `playbookSlots` 下发。
///
/// 🔴 **客户端⛔ 不许硬编一份键表** —— 那是第二份事实源(同 `PushKind` 的
/// `label` / `retired` 由服务端下发的先例)。漂了的后果是静默的:用户改完点提交
/// 拿一个英文 422,而界面上的表单一路是绿的。
/// ⚠ 槽位**只有数值**(`kind ∈ {price, percent}`),⛔ 没有「理由」「评价」这类键
/// (架构 §四 第 4 条:预案层知道形态,但不做好坏评价)。
/// ⚠ **骨架不可改**:这里给的是「方括号里那几个数」,不是「哪个量跟谁比」(K9 §6.4)。
struct PlaybookSlot: Codable, Equatable, Identifiable {
    var key: String = ""
    /// `price` = 元,`percent` = 百分点。未识别值原样透传(⛔ 不猜量纲)。
    var kind: String = ""
    var label: String = ""
    var hint: String = ""

    var id: String { key }
    var unit: String {
        switch kind {
        case "price": return "元"
        case "percent": return "百分点"
        default: return ""
        }
    }

    enum CodingKeys: String, CodingKey { case key, kind, label, hint }

    init(key: String = "", kind: String = "", label: String = "", hint: String = "") {
        self.key = key; self.kind = kind; self.label = label; self.hint = hint
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decodeIfPresent(String.self, forKey: .key) ?? ""
        kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? ""
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        hint = try c.decodeIfPresent(String.self, forKey: .hint) ?? ""
    }
}
