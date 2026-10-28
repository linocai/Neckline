// 晨报接口模型。时间保持字符串，展示时再按北京时间格式化。
// 新晨报用 title / conclusion / questions / risk / views；旧晨报只留 headline / reason 作回退。

import Foundation

enum MorningStatus: Codable, Equatable {
    case running
    case available
    case failed
    case unknown(String)

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        switch raw {
        case "running": self = .running
        case "available": self = .available
        case "failed": self = .failed
        default: self = .unknown(raw)
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .running: try container.encode("running")
        case .available: try container.encode("available")
        case .failed: try container.encode("failed")
        case .unknown(let raw): try container.encode(raw)
        }
    }
}

struct MorningReport: Codable, Equatable {
    let reportId: String
    let tradeDate: String
    let status: MorningStatus
    let windowStart: String
    let windowEnd: String
    let availableAt: String?
    let failedAt: String?
    let failureReason: String?
    let gapSummary: String?
    let configVersion: String
    let content: MorningContent?
}

struct MorningContent: Codable, Equatable {
    let reportId: String
    let tradeDate: String
    let kind: String
    let window: MorningWindow
    let generatedAt: String
    let configVersion: String
    let gapSummary: String?
    let entries: [MorningEntry]
    let candidates: Int?
    let evaluated: Int?
}

struct MorningWindow: Codable, Equatable {
    let start: String
    let end: String
}

struct MorningQuestions: Codable, Equatable {
    let value: String?
    let priced: String?
    let money: String?
    let price: String?
}

/// 观点来源。媒体、券商、公司以外的原样显示。
enum MorningViewBy: Codable, Equatable {
    case media
    case broker
    case company
    case unknown(String)

    var label: String {
        switch self {
        case .media: "媒体"
        case .broker: "券商"
        case .company: "公司"
        case .unknown(let raw): raw
        }
    }

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        switch raw {
        case "媒体": self = .media
        case "券商": self = .broker
        case "公司": self = .company
        default: self = .unknown(raw)
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .media: try container.encode("媒体")
        case .broker: try container.encode("券商")
        case .company: try container.encode("公司")
        case .unknown(let raw): try container.encode(raw)
        }
    }
}

struct MorningView: Codable, Equatable {
    let by: MorningViewBy
    let name: String
    let text: String
    let sourceTitle: String?
    let sourceUrl: String?

    var linkURL: URL? {
        let raw = sourceUrl?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard let url = URL(string: raw), let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https" else { return nil }
        return url
    }
}

struct MorningEntry: Codable, Equatable {
    let companyCode: String
    let companyName: String
    let type: String
    let title: String?
    let conclusion: String?
    let questions: MorningQuestions?
    let risk: String?
    let views: [MorningView]?
    let firstDisclosed: String?
    let sources: [MorningSource]
    /// 旧晨报标题。新晨报没有这个字段。
    let headline: String?
    /// 旧晨报结论。新晨报没有这个字段。
    let reason: String?

    var displayTitle: String {
        let primary = title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !primary.isEmpty { return primary }
        return headline?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    var displayConclusion: String {
        let primary = conclusion?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !primary.isEmpty { return primary }
        return reason?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    /// 首次公开原文。没有就用第一条来源的发布时间。
    var disclosedRaw: String? {
        let first = firstDisclosed?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !first.isEmpty { return first }
        let published = sources.first?.publishedAt.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return published.isEmpty ? nil : published
    }
}

struct MorningSource: Codable, Equatable {
    let documentId: String
    let sourceLabel: String
    let publishedAt: String
    let title: String?
    let url: String?
}

struct MorningDocument: Codable, Equatable {
    let documentId: String
    let sourceLabel: String
    let title: String?
    let text: String
    let url: String?
    let publishedAt: String?
    let fetchedAt: String?
}

struct MorningHistory: Codable, Equatable {
    let reports: [MorningHistoryItem]
}

struct MorningHistoryItem: Codable, Equatable {
    let tradeDate: String
    let status: MorningStatus
    let availableAt: String?
    let entryCount: Int
    let failureReason: String?
}

struct MorningHealth: Codable, Equatable {
    let db: String
    let latestReport: MorningHealthLatest?
    let readPage: Bool
    let modelKey: Bool
    let version: String?
    let releaseSet: String?

    init(db: String, latestReport: MorningHealthLatest?, readPage: Bool, modelKey: Bool,
         version: String? = nil, releaseSet: String? = nil) {
        self.db = db
        self.latestReport = latestReport
        self.readPage = readPage
        self.modelKey = modelKey
        self.version = version
        self.releaseSet = releaseSet
    }
}

struct MorningHealthLatest: Codable, Equatable {
    let tradeDate: String
    let status: MorningStatus
}

struct MorningDeviceAck: Codable, Equatable {
    let ok: Bool
}

struct MorningFailureBody: Decodable, Equatable {
    let detailText: String?
    let reason: String?

    private enum Outer: String, CodingKey { case detail }
    private enum Inner: String, CodingKey { case reason }

    init(from decoder: Decoder) throws {
        let outer = try decoder.container(keyedBy: Outer.self)
        if let text = try? outer.decode(String.self, forKey: .detail) {
            detailText = text
            reason = nil
            return
        }
        let inner = try outer.nestedContainer(keyedBy: Inner.self, forKey: .detail)
        detailText = nil
        reason = try inner.decodeIfPresent(String.self, forKey: .reason)
    }
}
