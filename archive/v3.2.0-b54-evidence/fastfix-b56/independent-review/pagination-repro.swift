import Foundation
private actor RefreshGate {
    private var opened = false
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private var enteredWaiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        guard !opened else { return }
        let entered = enteredWaiters
        enteredWaiters.removeAll()
        entered.forEach { $0.resume() }
        await withCheckedContinuation { waiters.append($0) }
    }

    func waitUntilEntered() async {
        guard waiters.isEmpty else { return }
        await withCheckedContinuation { enteredWaiters.append($0) }
    }

    func open() {
        opened = true
        let paused = waiters
        waiters.removeAll()
        paused.forEach { $0.resume() }
    }
}

private actor ControlledK10Service: K10Servicing {
    private let batchID: String
    private let empty: Bool
    private let healthGate: RefreshGate?
    private let publicationGate: RefreshGate?
    private let opportunityGate: RefreshGate?
    private let analysisChainGate: RefreshGate?
    private let healthFailure: K10APIError?
    private let firstPublicationFailure: K10APIError?
    private let analysisChainCancellation: Bool
    private let firstAnalysisChainRevision: Int?
    private let laterAnalysisChainRevision: Int?
    private let fixture = K10SyntheticUIService()
    private let morningReport = K10MorningReport(
        schemaVersion: "k10-api-v3", reportId: "controlled-morning", scanId: "controlled-scan", revision: 1,
        cutoffAt: "2026-09-07T08:30:00+08:00", createdAt: "2026-09-07T08:35:00+08:00", status: "completed",
        coverage: [:], coverageStatus: "complete", coverageGaps: [], items: []
    )
    private var analysisRevision = 1
    private var retryCalls = 0
    private var publicationCalls = 0
    private var analysisChainCalls = 0
    private var morningFailure: K10APIError?
    private var dailyFailure: K10APIError?
    private var dailyEmpty = false
    private var nextDailyPage: K10DailyReportResponse?
    private var dailyMorningOverride: K10DailyReportResponse?
    private var dailyActionCalls = 0

    init(batchID: String, empty: Bool = false, healthGate: RefreshGate? = nil, publicationGate: RefreshGate? = nil, opportunityGate: RefreshGate? = nil, analysisChainGate: RefreshGate? = nil, healthFailure: K10APIError? = nil, firstPublicationFailure: K10APIError? = nil, analysisChainCancellation: Bool = false, firstAnalysisChainRevision: Int? = nil, laterAnalysisChainRevision: Int? = nil) {
        self.batchID = batchID
        self.empty = empty
        self.healthGate = healthGate
        self.publicationGate = publicationGate
        self.opportunityGate = opportunityGate
        self.analysisChainGate = analysisChainGate
        self.healthFailure = healthFailure
        self.firstPublicationFailure = firstPublicationFailure
        self.analysisChainCancellation = analysisChainCancellation
        self.firstAnalysisChainRevision = firstAnalysisChainRevision
        self.laterAnalysisChainRevision = laterAnalysisChainRevision
    }

    func health() async throws -> K10Health {
        if let healthGate { await healthGate.wait() }
        if let healthFailure { throw healthFailure }
        return K10Health(status: "ok", version: "3.0.2 Build 33")
    }

    func latestScan(window: String) async throws -> K10Scan {
        if empty { throw K10APIError.notFound("尚无扫描") }
        return try await fixture.latestScan(window: window)
    }

    func publications() async throws -> [K10Publication] {
        publicationCalls += 1
        let isFirstPublication = publicationCalls == 1
        if isFirstPublication, let publicationGate { await publicationGate.wait() }
        if isFirstPublication, let firstPublicationFailure { throw firstPublicationFailure }
        guard !empty else { return [] }
        return [K10Publication(schemaVersion: "k10-api-v2", batchId: batchID, scanId: "scan-\(batchID)", publicationKind: "evening", availableAt: "2026-09-07T21:00:00+08:00", createdAt: "2026-09-07T21:00:00+08:00", sampleCount: 1)]
    }

    func companyWindows() async throws -> [K10CompanyWindow] {
        if empty { return [] }
        return try await fixture.companyWindows()
    }
    func latestDailyReport(window: String) async throws -> K10DailyReportResponse {
        if let dailyFailure { throw dailyFailure }
        if empty || dailyEmpty { return .init(schemaVersion: 8, state: "empty", reason: nil, report: nil) }
        if window == "morning", let dailyMorningOverride { return dailyMorningOverride }
        return try await fixture.latestDailyReport(window: window)
    }
    func dailyReport(id: String, cursor: String) async throws -> K10DailyReportResponse {
        guard let nextDailyPage else { throw K10APIError.notFound("没有下一页") }
        return nextDailyPage
    }
    func setDailyFailure(_ error: K10APIError?) { dailyFailure = error }
    func setDailyEmpty() { dailyEmpty = true }
    func setDailyPages(first: K10DailyReportResponse, next: K10DailyReportResponse) { dailyMorningOverride = first; nextDailyPage = next }
    func latestMorningReport() async throws -> K10MorningReport? {
        if let morningFailure { throw morningFailure }
        return morningReport
    }
    func opportunity(id: String) async throws -> K10OpportunityDetail {
        if let opportunityGate { await opportunityGate.wait() }
        return try await fixture.opportunity(id: id)
    }
    func act(companyWindowID: String, request: K10SelectionRequest) async throws -> K10SelectionAction {
        dailyActionCalls += 1
        return try await fixture.act(companyWindowID: companyWindowID, request: request)
    }
    func dailyActionCallCount() -> Int { dailyActionCalls }
    func selections() async throws -> [K10SelectionDetail] {
        if empty { return [] }
        return try await fixture.selections()
    }
    func analysisChain(companyWindowID: String) async throws -> K10AnalysisChain {
        analysisChainCalls += 1
        let isFirstChain = analysisChainCalls == 1
        let revision = isFirstChain ? (firstAnalysisChainRevision ?? analysisRevision) : (laterAnalysisChainRevision ?? analysisRevision)
        if isFirstChain, let analysisChainGate { await analysisChainGate.wait() }
        if analysisChainCancellation { throw CancellationError() }
        return K10AnalysisChain(
            schemaVersion: "k10-api-v2", companyWindowId: companyWindowID,
            items: [K10AnalysisChainItem(revision: revision, inputCutoffAt: "2026-09-07T09:00:00+08:00", requestId: revision == 1 ? nil : "request-\(revision)", kind: revision == 1 ? "initial" : "user_question", question: revision == 1 ? nil : "后续追问", parentRevision: revision == 1 ? nil : revision - 1, sourceRefs: [], analyses: [], job: nil)]
        )
    }
    func requestAnalysis(companyWindowID: String, request: K10AnalysisRequest) async throws -> K10AnalysisRequestResult {
        analysisRevision += 1
        return K10AnalysisRequestResult(schemaVersion: "k10-api-v2", requestId: "request-\(analysisRevision)", companyWindowId: companyWindowID, observationId: "observation-1", analysisJobId: "analysis-job-\(analysisRevision)", revision: analysisRevision, parentRevision: analysisRevision - 1, inputCutoffAt: "2026-09-07T09:00:00+08:00", replayed: false)
    }
    func document(id: String, revision: Int?, offset: Int, limit: Int) async throws -> K10DocumentPage { try await fixture.document(id: id, revision: revision, offset: offset, limit: limit) }
    func job(id: String) async throws -> K10Job { try await fixture.job(id: id) }
    func retryJob(id: String, expectedAttemptCount: Int) async throws -> K10Job { retryCalls += 1; return try await fixture.retryJob(id: id, expectedAttemptCount: expectedAttemptCount) }
    func results() async throws -> K10Results { try await fixture.results() }
    func configuration() async throws -> K10Configuration { try await fixture.configuration() }
    func usageSummary() async throws -> K10UsageSummary { try await fixture.usageSummary() }
    func retryCallCount() -> Int { retryCalls }
    func setMorningFailure(_ value: K10APIError?) { morningFailure = value }
}


@main struct AuditPagination {
  @MainActor static func main() async throws {
    let service = ControlledK10Service(batchID: "pagination-audit")
    var first = try await service.latestDailyReport(window: "morning")
    let tail = first.report!.addedCards.first!
    let encoded = try JSONEncoder().encode(tail)
    var page: [K10DailyCard] = []
    for n in 1...30 {
      var row = try JSONSerialization.jsonObject(with: encoded) as! [String: Any]
      row["cardId"] = "page-first-\(n)"
      row["companyCode"] = "300\(String(format: "%03d", n)).SZ"
      row["companyWindowId"] = "page-window-\(n)"
      row["rank"] = n
      page.append(try JSONDecoder().decode(K10DailyCard.self, from: JSONSerialization.data(withJSONObject: row)))
    }
    first.report?.addedCards = page
    first.report?.nextCursor = page.last!.cardId
    var next = first
    next.report?.addedCards = [tail]
    next.report?.nextCursor = nil
    await service.setDailyPages(first: first, next: next)
    let model = AppModel(serviceFactory: { service }, cacheContextFactory: { nil }, cacheLoader: { _ in nil }, cacheSaver: { _, _ in }, cacheClearer: {})
    await model.refresh()
    await model.loadMoreDailyCards()
    let before = model.dailyMorning!.report!.addedCards.count
    precondition(before == 31)
    await model.act("skip", card: tail)
    let after = model.dailyMorning!.report!.addedCards.count
    let present = model.dailyMorning!.report!.addedCards.contains { $0.cardId == tail.cardId }
    print("loaded_before_action=\(before) loaded_after_action=\(after) acted_card_still_loaded=\(present) actions=\(await service.dailyActionCallCount())")
    precondition(after == 30 && !present)
  }
}
