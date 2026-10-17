// V4 晨报接口模型。时间保持字符串，展示时再按北京时间格式化。

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

enum MorningImportance: Codable, Equatable {
    case high
    case medium
    case low
    case unknown(String)

    var label: String {
        switch self {
        case .high: return "高"
        case .medium: return "中"
        case .low: return "低"
        case .unknown(let raw): return raw
        }
    }

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        switch raw {
        case "高": self = .high
        case "中": self = .medium
        case "低": self = .low
        default: self = .unknown(raw)
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .high: try container.encode("高")
        case .medium: try container.encode("中")
        case .low: try container.encode("低")
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
    let review: MorningReview
}

struct MorningWindow: Codable, Equatable {
    let start: String
    let end: String
}

struct MorningReview: Codable, Equatable {
    let types: [MorningEntry]
    let relation: [MorningEntry]
    let fresh: [MorningEntry]
    let old: [MorningEntry]
}

struct MorningEntry: Codable, Equatable {
    let rank: Int
    let companyCode: String
    let companyName: String
    let type: String
    let importance: MorningImportance
    let relation: String
    let fresh: String
    let headline: String
    let reason: String
    let firstDisclosed: String?
    let sources: [MorningSource]
    let others: [MorningOtherEvent]
}

struct MorningSource: Codable, Equatable {
    let documentId: String
    let sourceLabel: String
    let publishedAt: String
    let title: String?
    let url: String?
}

struct MorningOtherEvent: Codable, Equatable {
    let headline: String
    let type: String
    let importance: MorningImportance
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
