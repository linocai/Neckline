// K9-v3 D1 9:29 checklist, keyed by immutable package ID.

import Foundation

struct Checklist: Codable, Equatable {
    let batchId: String
    let selectionDate: String
    let signalTradeDate: String
    let tradeDate: String
    let capturedAt: String
    let strategyVersion: String
    let d0Date: String
    let dataQuality: String
    let footnote: String
    let noQuoteCodes: [String]
    let noPlaybookCodes: [String]
    let notes: [String]
    let segments: [ChecklistSegment]
}

struct ChecklistSegment: Codable, Equatable, Identifiable {
    let verdict: K9ChecklistVerdict
    let label: String
    let rows: [ChecklistRow]
    var id: String { verdict.rawValue }
}

struct ChecklistRow: Codable, Equatable, Identifiable {
    let tsCode: String
    let name: String?
    let channels: [String]
    let channelRanks: [String: Int]
    let verdict: K9ChecklistVerdict
    let readings: NKJSON
    let playbookRevision: Int
    let playbookVersion: Int
    let pattern: String
    let segment: String
    let quoteState: String
    var id: String { tsCode }
    var displayName: String { name?.isEmpty == false ? name! : tsCode }
    var playbookLabel: String { "预案第 \(playbookRevision) 版" }
}
