import Foundation

enum K10APIError: LocalizedError, Equatable {
    case noToken, unauthorized, notFound(String), conflict(String), notConfigured(String, [String])
    case networkUnavailable(String), server(Int, String), decoding(String), incompatibleVersion(String)
    var errorDescription: String? { switch self {
    case .noToken: return "未配置 API Token"
    case .unauthorized: return "鉴权失败，请检查 API Token"
    case .notFound(let message), .conflict(let message), .decoding(let message): return message
    case .notConfigured(let message, let missing): return missing.isEmpty ? message : "\(message)：\(missing.joined(separator: "、"))"
    case .networkUnavailable(let message): return "网络不可用：\(message)"
    case .server(_, let message): return message
    case .incompatibleVersion(let message): return message
    } }
    var permitsOfflineCache: Bool { if case .networkUnavailable = self { return true }; return false }

    static func decodeServerFailure(_ data: Data, status: Int) -> K10APIError {
        let decoder = JSONDecoder()
        let direct = try? decoder.decode(K10Failure.self, from: data)
        let envelope = try? decoder.decode(K10FailureEnvelope.self, from: data)
        let failure = direct ?? envelope?.detail
        let fallback = (try? decoder.decode(K10StringFailureEnvelope.self, from: data))?.detail
        let loose = try? decoder.decode(K10LooseFailureEnvelope.self, from: data)
        let message = failure?.message ?? loose?.detail.message ?? loose?.detail.reason ?? fallback ?? "服务返回 \(status)"
        if status == 401 { return .unauthorized }
        if status == 404 { return .notFound(message) }
        if status == 409 { return .conflict(message) }
        if failure?.reason == "not_configured" { return .notConfigured(message, failure?.missing ?? []) }
        return .server(status, message)
    }
}

struct K10Failure: Codable, Equatable { let reason: String; let message: String; let missing: [String]? }
private struct K10FailureEnvelope: Decodable { let detail: K10Failure }
private struct K10StringFailureEnvelope: Decodable { let detail: String }
private struct K10LooseFailureEnvelope: Decodable { let detail: K10LooseFailure }
private struct K10LooseFailure: Decodable { let reason: String?; let message: String? }
struct K10Health: Codable, Equatable { let status: String; let version: String? }
struct K10Page: Codable, Equatable { let nextCursor: String? }
struct K10SourceReference: Codable, Identifiable, Equatable {
    let documentId: String?; let factId: String?; let companyCode: String?; let tradeDate: String?; let revision: Int?; let sourceKey: String?; let title: String?; let url: String?
    let excerpt: String?; let publishedAt: String?; let publishedPrecision: String; let fetchedAt: String?
    var collectedAt: String? = nil
    var id: String { "\(factId ?? documentId ?? url ?? sourceKey ?? title ?? "source")#\(revision.map(String.init) ?? "unversioned")#\(tradeDate ?? publishedAt ?? fetchedAt ?? "undated")" }
}
struct K10Evidence: Codable, Identifiable, Equatable { let sourceRef: K10SourceReference; let claim: String; let relation: String?; let uncertainty: String?; var id: String { "\(sourceRef.id)#\(claim)" } }
struct K10HistoricalCase: Codable, Identifiable, Equatable { let caseId: String; let outcome: String; let summary: String; let observedAt: String?; let eventTime: String?; let companyCode: String?; let stage: String?; let sourceRefs: [K10SourceReference]; let marketFacts: [K10SourceReference]; let outcomeFacts: [String: K10Value]?; var id: String { caseId } }
struct K10HistoricalCoverage: Codable, Equatable { let state: String; let requestedOutcomes: [String]; let presentOutcomes: [String]; let missingOutcomes: [String]; let reason: String?; let sourceRefs: [K10SourceReference] }
struct K10OpportunityClassification: Codable, Equatable { let kind: String; let reason: String; let newFacts: String; let changedJudgment: String?; let twoDayReason: String; let relatedOpportunityId: String? }
struct K10Comparison: Codable, Equatable { let summary: String?; let rationale: String?; let rank: Int?; let priorityReason: String?; let gap: String?; let rankChangeConditions: String?; let twoDayReason: String?; var classification: K10OpportunityClassification? = nil; let historicalCases: [K10HistoricalCase]?; let historicalCoverage: K10HistoricalCoverage?; var eventRank: Int? = nil; var rankNamespace: String? = nil }
struct K10CommonFact: Codable, Identifiable, Equatable { let key: String; let text: String; var id: String { key } }

struct K10SourceCoverage: Codable, Identifiable, Equatable {
    let sourceKey: String; let scope: String?; let authorization: String?; let isMarketWide: Bool?; let pagination: String?
    let limitations: [String]?; let pagesFetched: Int?; let pagesExpected: Int?; let complete: Bool?; let errors: [String]?; let state: String?
    let windowStartAt: String?; let windowCutoffAt: String?; let successWatermark: String?; let gaps: [String]?
    var timeCoverage: String? = nil
    var unknownPublicationTimeCount: Int? = nil
    var uncertainTimeDocumentRefs: [K10SourceReference]? = nil
    var id: String { sourceKey }
    var displayGaps: [String] { Array(Set((gaps ?? []) + (errors ?? []))).sorted() }
}
struct K10SourceReplay: Codable, Equatable {
    let sourceKey: String?; let nominalStartAt: String?; let effectiveStartAt: String?
    let replayStartAt: String?; let cutoffAt: String?; let replaySeconds: Int?; let requestState: String?
}
struct K10Scan: Codable, Identifiable, Equatable {
    let schemaVersion: String; let scanId: String; let window: String; let cutoffAt: String; let status: String; let coverageStatus: String
    let coverageGaps: [String]; let sourceCoverage: [K10SourceCoverage]; let createdAt: String; let completedAt: String?
    var sourceReplay: K10SourceReplay? = nil
    var id: String { scanId }
}

struct K10Publication: Codable, Identifiable, Equatable {
    let schemaVersion: String; let batchId: String; let scanId: String; let publicationKind: String; let availableAt: String; let createdAt: String; let sampleCount: Int
    var id: String { batchId }
}
struct K10PublicationList: Codable { let items: [K10Publication]; let page: K10Page }
struct K10LifecycleEvent: Codable, Identifiable, Equatable {
    let lifecycleEventId: String; let kind: String; let reason: String?; let sourceRefs: [K10SourceReference]; let content: [String: K10Value]; let occurredAt: String; let createdAt: String
    var independentVerificationRefs: [K10SourceReference]? = nil
    var id: String { lifecycleEventId }
}
struct K10Opportunity: Codable, Identifiable, Equatable {
    let schemaVersion: String; let opportunityId: String; let opportunityKey: String; let companyCode: String; let companyName: String?
    let eventId: String; let eventRevision: Int; let catalystStage: String; let relatedOpportunityId: String?; let companyWindowId: String; let firstBatchId: String
    let availableAt: String; let d0TradeDate: String; let d1TradeDate: String; let d2TradeDate: String; let sampleClass: String; let overlapsWindowId: String?
    let lifecycle: String; let sourceMarker: String?; let latePublication: Bool?; var displayRank: Int? = nil; let createdAt: String
    var id: String { opportunityId }
}
struct K10PublicationSample: Codable, Identifiable, Equatable {
    let sampleId: String; let batchId: String; let companyWindowId: String; let opportunityId: String; let companyCandidateId: String; let companyCode: String; let companyName: String?
    let eventId: String; let eventRevision: Int; let category: String; let sourceMarker: String; let comparison: K10Comparison; let evidence: [K10Evidence]; let rank: Int?; let createdAt: String
    var id: String { sampleId }
}
struct K10OpportunityDetail: Codable, Identifiable, Equatable {
    let schemaVersion: String; let opportunityId: String; let opportunityKey: String; let companyCode: String; let companyName: String?
    let eventId: String; let eventRevision: Int; let catalystStage: String; let relatedOpportunityId: String?; let companyWindowId: String; let firstBatchId: String
    let availableAt: String; let d0TradeDate: String; let d1TradeDate: String; let d2TradeDate: String; let sampleClass: String; let overlapsWindowId: String?
    let lifecycle: String; let sourceMarker: String?; let latePublication: Bool?; let createdAt: String; let eventHeadline: String?; let commonFacts: [K10CommonFact]; let samples: [K10PublicationSample]; let lifecycleEvents: [K10LifecycleEvent]
    var id: String { opportunityId }
}
struct K10OpportunityList: Codable { let items: [K10Opportunity]; let page: K10Page }
struct K10SelectionSnapshot: Codable, Equatable { let state: String; let frozenAt: String; let actionIds: [String] }
struct K10CompanyWindow: Codable, Identifiable, Equatable {
    let schemaVersion: String; let companyWindowId: String; let companyCode: String; let companyName: String?; let firstBatchId: String
    let d0TradeDate: String; let d1TradeDate: String; let d2TradeDate: String; let d1SelectionAt: String; let d2CloseAt: String
    let sampleClass: String; let overlapsWindowId: String?; let selection: K10SelectionSnapshot?; let currentSelectionState: String?; let lastActionAt: String?; let postFreeze: Bool?
    let opportunities: [K10Opportunity]; let samples: [K10PublicationSample]; var displayRank: Int? = nil; var availableAt: String? = nil; let createdAt: String
    var id: String { companyWindowId }
}
struct K10CompanyWindowList: Codable { let items: [K10CompanyWindow]; let page: K10Page }
struct K10SelectionRequest: Codable { let action: String; let idempotencyKey: String; let reason: String? }
struct K10SelectionAction: Codable, Equatable { let schemaVersion: String; let actionId: String; let companyWindowId: String; let representativeCandidateId: String?; let state: String; let lastActionAt: String?; let postFreeze: Bool?; let observationId: String?; let analysisJobId: String?; let replayed: Bool }

struct K10AnalysisEventLineage: Codable, Equatable { let eventId: String; let revision: Int }
struct K10AnalysisLineage: Codable, Equatable { let candidateId: String?; let event: K10AnalysisEventLineage?; let mappingIds: [String]; let documentVersions: [K10SourceReference]; let inputCutoffAt: String? }
struct K10ModelCost: Codable, Equatable { let amount: Double?; let currency: String?; let pricingVersion: String?; let status: String? }
struct K10ModelUsage: Codable, Equatable { let inputTokens: Int?; let outputTokens: Int?; let totalTokens: Int?; let cacheTokens: Int?; let usageUnavailable: Bool?; let cacheUsageUnavailable: Bool?; let cost: K10ModelCost? }
struct K10Analysis: Codable, Identifiable, Equatable {
    let analysisId: String; let observationId: String; let revision: Int; let role: String; let status: String; let inputCutoffAt: String
    let sourceRefs: [K10SourceReference]; let inputLineage: K10AnalysisLineage; let fullText: String?; let provider: String?; let model: String?; let promptVersion: String?; let usage: K10ModelUsage?; let error: String?
    var id: String { analysisId }
}
struct K10JobFailure: Codable, Equatable { let reason: String; let message: String }
struct K10Job: Codable, Identifiable, Equatable { let schemaVersion: String; let jobId: String; let kind: String; let status: String; let stage: String; let attemptCount: Int; let inputVersion: String; let inputCutoffAt: String; let createdAt: String; let updatedAt: String; let error: K10JobFailure?; var id: String { jobId } }
struct K10SelectionDetail: Codable, Identifiable, Equatable {
    let schemaVersion: String; let companyWindowId: String; let representativeCandidateId: String?; let state: String; let lastActionAt: String?; let postFreeze: Bool?; let observationId: String?; let opportunities: [K10Opportunity]; let analyses: [K10Analysis]; let analysisJobId: String?; let latestJob: K10Job?
    var id: String { companyWindowId }
}
struct K10SelectionList: Codable { let items: [K10SelectionDetail]; let page: K10Page }

struct K10MorningReportItem: Codable, Identifiable, Equatable { let itemId: String; let reportId: String; let scanId: String; let companyWindowId: String?; let opportunityId: String?; let companyCode: String?; let companyName: String?; let displayRank: Int?; let selectionState: String?; let lifecycle: String?; let section: String; let priority: Int; let summary: String; let coverage: [String: K10Value]; let coverageStatus: String; let coverageGaps: [String]; let sourceRefs: [K10SourceReference]; let independentVerificationRefs: [K10SourceReference]; let lifecycleEventId: String?; let deadlineAt: String?; let createdAt: String; var id: String { itemId } }
struct K10MorningReport: Codable, Identifiable, Equatable { let schemaVersion: String; let reportId: String; let scanId: String; let revision: Int; let cutoffAt: String; let createdAt: String; let status: String; let coverage: [String: K10Value]; let coverageStatus: String; let coverageGaps: [String]; let items: [K10MorningReportItem]; var id: String { reportId } }
struct K10MorningReportList: Codable { let items: [K10MorningReport]; let page: K10Page }

struct K10AnalysisDocumentReference: Codable, Equatable { let documentId: String; let revision: Int }
struct K10AnalysisRequest: Encodable { let kind: String; let question: String?; let sourceRefs: [K10AnalysisDocumentReference]; let idempotencyKey: String }
struct K10AnalysisRequestResult: Codable, Equatable { let schemaVersion: String; let requestId: String; let companyWindowId: String; let observationId: String; let analysisJobId: String; let revision: Int; let parentRevision: Int; let inputCutoffAt: String; let replayed: Bool }
struct K10AnalysisChainItem: Codable, Identifiable, Equatable { let revision: Int; let inputCutoffAt: String; let requestId: String?; let kind: String; let question: String?; let parentRevision: Int?; let sourceRefs: [K10SourceReference]; let analyses: [K10Analysis]; let job: K10Job?; var id: String { "analysis-revision-\(revision)" } }
struct K10AnalysisChain: Codable, Equatable { let schemaVersion: String; let companyWindowId: String; let items: [K10AnalysisChainItem] }

struct K10MarketDay: Codable, Identifiable, Equatable {
    let tradeDate: String; let availability: String; let closeLimitUp: Bool?; let touchedLimitUp: Bool?; let firstTouchedAt: String?; let open: Double?; let high: Double?; let low: Double?; let close: Double?; let preClose: Double?; let limitUpPrice: Double?; let sourceRefs: [K10SourceReference]; let obtainedAt: String?; var fieldChecks: [K10MarketFieldCheck]? = nil; var anomalyReason: String? = nil
    var id: String { tradeDate }
}
struct K10MarketFieldSource: Codable, Equatable { let source: String; let value: K10Value?; let observedAt: String? }
struct K10MarketFieldCheck: Codable, Identifiable, Equatable { let field: String; let state: String; let reason: String; let sourceValues: [K10MarketFieldSource]; var id: String { field } }
struct K10Evaluation: Codable, Identifiable, Equatable {
    let companyWindowId: String; let opportunityIds: [String]; let companyCode: String; let sampleClass: String; let selection: K10SelectionSnapshot?; let state: String; let revision: Int; let updatedAt: String
    let d1: K10MarketDay?; let d2: K10MarketDay?; let primaryEligible: Bool; let closeLimitHitAny: Bool?; let firstTouchDay: String?; let firstTouchStatus: String?; let knownTouchDays: [String]?
    let d1OpenGap: Double?; let d1PriceChanges: [String: Double?]?; let d2PriceChanges: [String: Double?]?; let windowPriceChanges: [String: Double?]?; let comparability: String?; let gaps: [String]; let factRefs: [K10SourceReference]
    var evaluationConfigurationState: String? = nil
    var evaluationConfigurationMissing: [String]? = nil
    var evaluationConfigurationErrors: [String]? = nil
    var id: String { companyWindowId }
}
struct K10EvaluationMetrics: Codable, Equatable { let sampleCount: Int; let eligibleCount: Int; let hitCount: Int; let hitRate: Double?; let touchRate: Double?; let incompleteCount: Int; let pendingCount: Int; let observedCompleteCount: Int; let knownHitCount: Int; let touchCount: Int; let suspendedCount: Int; let dataGapCount: Int; let anomalyCount: Int; let selectionPendingCount: Int; var notConfiguredCount: Int? = nil }
struct K10ResultsCohort: Codable, Identifiable, Equatable { let batchId: String; var batchIds: [String]? = nil; let d1TradeDate: String; let d2TradeDate: String; let evaluationVersion: String?; let companySampleCount: Int; let catalystEventCount: Int; let primary: [String: K10EvaluationMetrics]; let overlap: K10EvaluationMetrics; var id: String { "\(d1TradeDate)#\(d2TradeDate)#\(evaluationVersion ?? "未记录")" } }
struct K10ResultsEventGroup: Codable, Identifiable, Equatable { let eventId: String; let headline: String?; let companyWindowIds: [String]; let opportunityIds: [String]; let companySampleCount: Int; let catalystCount: Int; let primary: [String: K10EvaluationMetrics]; var overlap: K10EvaluationMetrics? = nil; var id: String { eventId } }
struct K10Results: Codable, Equatable { let schemaVersion: String; let state: String; let reason: K10Failure?; let asOf: String?; let primary: [String: K10EvaluationMetrics]; let overlap: K10EvaluationMetrics; let records: [K10Evaluation]; let cohorts: [K10ResultsCohort]?; let eventGroups: [K10ResultsEventGroup]?; var configurationState: String? = nil; var configurationMissing: [String]? = nil; var configurationErrors: [String]? = nil }

struct K10DocumentPage: Codable, Equatable, Identifiable { let schemaVersion: String; let documentId: String; let revision: Int; let sourceKey: String; let externalId: String; let canonicalUrl: String?; let title: String?; let publishedAt: String?; let publishedPrecision: String; let fetchedAt: String; let excerpt: String?; let body: String?; let page: K10Page; var id: String { "\(documentId)-\(revision)" } }
struct K10UsageTotals: Codable, Equatable { let calls: Int; let failed: Int; let usageUnavailable: Int; let promptTokens: Int?; let completionTokens: Int?; let totalTokens: Int?; let tavilyCredits: Int?; let durationMs: Int? }
struct K10UsageDay: Codable, Equatable { let date: String; let totals: K10UsageTotals }
struct K10UsageSummary: Codable, Equatable { let days: [K10UsageDay]; let totals: K10UsageTotals }
struct K10Configuration: Codable, Equatable { let schemaVersion: String; let configId: String?; let configRevision: Int?; let scopes: [K10ConfigurationScope] }
struct K10ConfigurationScope: Codable, Identifiable, Equatable { let scope: String; let state: String; let missing: [String]; let errors: [String]; var id: String { scope } }

enum K10Value: Codable, Equatable { case string(String), number(Double), bool(Bool), null, object([String: K10Value]), array([K10Value])
    init(from decoder: Decoder) throws { let c = try decoder.singleValueContainer(); if c.decodeNil() { self = .null } else if let b = try? c.decode(Bool.self) { self = .bool(b) } else if let n = try? c.decode(Double.self) { self = .number(n) } else if let value = try? c.decode(String.self) { self = .string(value) } else if let object = try? c.decode([String: K10Value].self) { self = .object(object) } else { self = .array(try c.decode([K10Value].self)) } }
    func encode(to encoder: Encoder) throws { var c = encoder.singleValueContainer(); switch self { case .string(let v): try c.encode(v); case .number(let v): try c.encode(v); case .bool(let v): try c.encode(v); case .null: try c.encodeNil(); case .object(let v): try c.encode(v); case .array(let v): try c.encode(v) } }
}

// Generic settings contract stays independent from K10 selection data.
struct K10Provider: Codable, Equatable, Identifiable { let name: String; let baseUrl: String; let model: String; let hasWebSearch: Bool; let searchEngine: String?; let notes: String?; let enabled: Bool; let keySet: Bool; var id: String { name } }
struct K10ProviderList: Codable { let items: [K10Provider] }
struct K10ProviderCreate: Encodable { let name: String; let baseUrl: String; let model: String; let apiKey: String?; let hasWebSearch: Bool; let searchEngine: String?; let notes: String?; let enabled: Bool }
struct K10ProviderUpdate: Encodable { let baseUrl: String?; let model: String?; let apiKey: String?; let hasWebSearch: Bool?; let searchEngine: String?; let notes: String?; let enabled: Bool? }
struct K10TavilyStatus: Codable { let keySet: Bool }
struct K10TavilyUpdate: Encodable { let apiKey: String }
struct K10DeviceRegistration: Encodable { let token: String; let platform: String }
struct K10OK: Codable { let ok: Bool }
