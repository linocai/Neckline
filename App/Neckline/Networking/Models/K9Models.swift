// K9-v3 report and frozen-selection DTOs.

import Foundation

enum K9ReportState: String, Codable, Equatable, CaseIterable {
    case hasList = "has_list", empty, notRun = "not_run"
    var fallbackHeadline: String {
        switch self {
        case .hasList: return "今天有这些"
        case .empty: return "今天没有"
        case .notRun: return "今天没跑成"
        }
    }
    var tone: NKAxisTone {
        switch self {
        case .hasList: return .good
        case .empty: return .neutral
        case .notRun: return .warn
        }
    }
}

struct K9Stock: Codable, Equatable, Identifiable {
    let tsCode: String
    let name: String?
    let swL2Code: String?
    let swL2Name: String?
    let patterns: [String]
    let primaryPattern: String
    let channelRanks: [String: Int]
    let playbook: NKJSON?
    let baseline: NKJSON?
    let thresholds: NKJSON?
    let batchId: String?
    var id: String { tsCode }
    var displayName: String { name?.isEmpty == false ? name! : tsCode }
    var playbookRevision: Int { playbook?.objectValue?["revision"]?.intValue ?? 1 }
    var playbookLabel: String { "预案第 \(playbookRevision) 版" }

    init(tsCode: String, name: String? = nil, swL2Code: String? = nil, swL2Name: String? = nil,
         patterns: [String] = [], primaryPattern: String = "", channelRanks: [String: Int] = [:],
         playbook: NKJSON? = nil, baseline: NKJSON? = nil, thresholds: NKJSON? = nil, batchId: String? = nil) {
        self.tsCode = tsCode; self.name = name; self.swL2Code = swL2Code; self.swL2Name = swL2Name
        self.patterns = patterns; self.primaryPattern = primaryPattern; self.channelRanks = channelRanks
        self.playbook = playbook; self.baseline = baseline; self.thresholds = thresholds; self.batchId = batchId
    }
}

struct SelectionSnapshot: Codable, Equatable {
    let state: K9ReportState?
    let reportDate: String
    let tradeDate: String
    let headline: String
    let gaps: [String]
    let strategy: String
    let strategyVersion: String
    let paramsPackageVersion: String?
    let packId: String?
    let packVersion: String?
    let listingSize: Int?
    let generatedAt: String
    let markdown: String
    let structured: NKJSON
    let directionSnapshot: NKJSON?
    let marketSnapshot: NKJSON?
    let copyText: String
    let stocks: [K9Stock]

    static let notLoaded = SelectionSnapshot(state: nil, reportDate: "", tradeDate: "",
                                               headline: "还没连上服务端", gaps: ["本次启动尚未成功拉过报告"],
                                               strategy: "", strategyVersion: "", paramsPackageVersion: nil,
                                               packId: nil, packVersion: nil, listingSize: nil, generatedAt: "",
                                               markdown: "", structured: .object([:]), directionSnapshot: nil,
                                               marketSnapshot: nil, copyText: "", stocks: [])

    enum CodingKeys: String, CodingKey {
        case state, reportDate, tradeDate, headline, gaps, strategy, strategyVersion, paramsPackageVersion
        case packId, packVersion, listingSize, generatedAt, markdown, structured, copyText, stocks
        case directionSnapshot = "direction", marketSnapshot = "market"
    }

    init(state: K9ReportState?, reportDate: String = "", tradeDate: String = "", headline: String = "", gaps: [String] = [],
         strategy: String = "", strategyVersion: String = "", paramsPackageVersion: String? = nil, packId: String? = nil,
         packVersion: String? = nil, listingSize: Int? = nil, generatedAt: String = "", markdown: String = "", structured: NKJSON = .object([:]),
         directionSnapshot: NKJSON? = nil, marketSnapshot: NKJSON? = nil, copyText: String = "", stocks: [K9Stock] = []) {
        self.state = state; self.reportDate = reportDate; self.tradeDate = tradeDate; self.headline = headline
        self.gaps = gaps; self.strategy = strategy; self.strategyVersion = strategyVersion
        self.paramsPackageVersion = paramsPackageVersion; self.packId = packId; self.packVersion = packVersion
        self.listingSize = listingSize; self.generatedAt = generatedAt; self.markdown = markdown
        self.structured = structured; self.directionSnapshot = directionSnapshot; self.marketSnapshot = marketSnapshot
        self.copyText = copyText; self.stocks = stocks
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
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
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt) ?? ""
        markdown = try c.decodeIfPresent(String.self, forKey: .markdown) ?? ""
        structured = try c.decodeIfPresent(NKJSON.self, forKey: .structured) ?? .object([:])
        directionSnapshot = try c.decodeIfPresent(NKJSON.self, forKey: .directionSnapshot)
        marketSnapshot = try c.decodeIfPresent(NKJSON.self, forKey: .marketSnapshot)
        copyText = try c.decodeIfPresent(String.self, forKey: .copyText) ?? ""
        stocks = try c.decodeIfPresent([K9Stock].self, forKey: .stocks) ?? []
    }

    var headlineText: String { headline.isEmpty ? (state?.fallbackHeadline ?? "读不出这份报告") : headline }
    var tone: NKAxisTone { state?.tone ?? .warn }
    var parameterPackWasMissing: Bool { headline.contains("参数未配置") || gaps.contains { $0.contains("参数未配置") } }
    var batchIds: [String] { structured.objectValue?["batchIds"]?.arrayValue?.compactMap(\.stringValue) ?? [] }
    var direction: NKJSON? { directionSnapshot ?? structured.objectValue?["direction"] }
    var market: NKJSON? { marketSnapshot ?? structured.objectValue?["market"] }
}
