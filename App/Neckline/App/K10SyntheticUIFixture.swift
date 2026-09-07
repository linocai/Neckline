#if DEBUG
import Foundation

/// Explicit UI-verification seam. It has no URLSession, credentials, Keychain, or APNs path.
actor K10SyntheticUIService: K10Servicing {
    private struct WindowAction: Sendable {
        var state = "unhandled"
        var frozenState: String?
        var lastActionAt: String?
        var postFreeze = false
        var actionIDs: [String] = []
        var hasAnalysis = false
    }

    private static let source = K10SourceReference(
        documentId: "synthetic-document",
        factId: nil,
        companyCode: nil,
        tradeDate: nil,
        revision: 1,
        sourceKey: "synthetic-news",
        title: "合成资料：验证环节出现新的阶段进展",
        url: "https://example.invalid/k10/synthetic-document",
        excerpt: "合成资料仅用于 UI 验证，不代表真实公司、消息或市场覆盖。",
        publishedAt: "2026-09-06T19:42:00+08:00",
        publishedPrecision: "exact",
        fetchedAt: "2026-09-06T20:00:00+08:00"
    )
    private static let tavilyExcerpt = K10SourceReference(
        documentId: "synthetic-tavily-excerpt",
        factId: nil,
        companyCode: nil,
        tradeDate: nil,
        revision: 1,
        sourceKey: "tavily",
        title: "合成核验摘录：尚未取得完整正文",
        url: "https://example.invalid/k10/synthetic-tavily",
        excerpt: "合成搜索摘录：仅验证到存在相关公开表述，完整正文未保存。",
        publishedAt: nil,
        publishedPrecision: "unknown",
        fetchedAt: "2026-09-06T20:06:00+08:00"
    )
    private static var marketSnapshot: K10SourceReference {
        var source = K10SourceReference(
            documentId: nil,
            factId: "synthetic-market-300001-20260907",
            companyCode: "300001.SZ",
            tradeDate: "2026-09-07",
            revision: 1,
            sourceKey: "market_snapshot",
            title: "合成行情快照",
            url: nil,
            excerpt: nil,
            publishedAt: nil,
            publishedPrecision: "unknown",
            fetchedAt: nil
        )
        source.collectedAt = nil
        return source
    }
    private static let comparison = K10Comparison(
        summary: "合成科技与当前催化的直接关联更明确；其余关系仍需等待新增公开资料。",
        rationale: "合成数据只用于验证比较信息层级。",
        rank: 1,
        priorityReason: "现有资料把合成科技列为直接相关方。",
        gap: "其他公司仅有间接关联，尚未形成同等证据链。",
        rankChangeConditions: "若出现新的直接订单或公司否认，当前排序需要重看。",
        twoDayReason: "只观察固定 D1/D2 的市场事实，不生成交易计划。"
    )
    private static let first = K10Opportunity(
        schemaVersion: "k10-api-v2", opportunityId: "synthetic-opportunity-1", opportunityKey: "synthetic-independent-stage",
        companyCode: "300001.SZ", companyName: "合成科技", eventId: "synthetic-event-a", eventRevision: 1,
        catalystStage: "合成阶段进展", relatedOpportunityId: nil, companyWindowId: "synthetic-evening-window",
        firstBatchId: "synthetic-evening", availableAt: "2026-09-06T21:10:00+08:00", d0TradeDate: "2026-09-04",
        d1TradeDate: "2026-09-07", d2TradeDate: "2026-09-08", sampleClass: "primary", overlapsWindowId: nil,
        lifecycle: "published", sourceMarker: "evening", latePublication: false, createdAt: "2026-09-06T21:10:00+08:00"
    )
    private static let second = K10Opportunity(
        schemaVersion: "k10-api-v2", opportunityId: "synthetic-opportunity-2", opportunityKey: "synthetic-related-catalyst",
        companyCode: "300001.SZ", companyName: "合成科技", eventId: "synthetic-event-b", eventRevision: 1,
        catalystStage: "合成关联更新", relatedOpportunityId: nil, companyWindowId: "synthetic-evening-window",
        firstBatchId: "synthetic-evening", availableAt: "2026-09-06T21:10:00+08:00", d0TradeDate: "2026-09-04",
        d1TradeDate: "2026-09-07", d2TradeDate: "2026-09-08", sampleClass: "primary", overlapsWindowId: nil,
        lifecycle: "evidence_update", sourceMarker: "evening", latePublication: false, createdAt: "2026-09-06T21:10:00+08:00"
    )
    private static let sample = K10PublicationSample(
        sampleId: "synthetic-sample", batchId: "synthetic-evening", companyWindowId: "synthetic-evening-window",
        opportunityId: "synthetic-opportunity-1", companyCandidateId: "synthetic-evening-candidate", companyCode: "300001.SZ",
        companyName: "合成科技", eventId: "synthetic-event-a", eventRevision: 1, category: "primary", sourceMarker: "evening",
        comparison: comparison,
        evidence: [
            K10Evidence(sourceRef: source, claim: "合成资料显示验证环节出现新的阶段进展，相关方信息可追溯。", relation: "资料将公司列为直接业务关联方。", uncertainty: "仅合成 UI 验证数据。"),
            K10Evidence(sourceRef: tavilyExcerpt, claim: "独立核验只保存了合成搜索摘录，完整正文待补。", relation: "用于显示待核来源。", uncertainty: "发布日期与完整正文均待核。")
        ],
        rank: 1, createdAt: "2026-09-06T21:10:00+08:00"
    )
    private static let late = K10Opportunity(
        schemaVersion: "k10-api-v2", opportunityId: "synthetic-opportunity-late", opportunityKey: "synthetic-late-stage",
        companyCode: "300002.SZ", companyName: "晨间科技", eventId: "synthetic-event-late", eventRevision: 1,
        catalystStage: "合成晨间补充", relatedOpportunityId: nil, companyWindowId: "synthetic-morning-late-window",
        firstBatchId: "synthetic-morning", availableAt: "2026-09-07T09:40:00+08:00", d0TradeDate: "2026-09-07",
        d1TradeDate: "2026-09-08", d2TradeDate: "2026-09-09", sampleClass: "primary", overlapsWindowId: nil,
        lifecycle: "published", sourceMarker: "morning", latePublication: true, createdAt: "2026-09-07T09:40:00+08:00"
    )
    private static let lateSample = K10PublicationSample(
        sampleId: "synthetic-late-sample", batchId: "synthetic-morning", companyWindowId: "synthetic-morning-late-window",
        opportunityId: "synthetic-opportunity-late", companyCandidateId: "synthetic-late-candidate", companyCode: "300002.SZ",
        companyName: "晨间科技", eventId: "synthetic-event-late", eventRevision: 1, category: "alternative", sourceMarker: "morning",
        comparison: comparison,
        evidence: [K10Evidence(sourceRef: source, claim: "合成晨间迟到样本，仅用于验证迟到窗口与独立操作。", relation: "独立合成样本。", uncertainty: nil)],
        rank: 2, createdAt: "2026-09-07T09:40:00+08:00"
    )

    private var actions: [String: WindowAction] = [:]

    func health() async throws -> K10Health { K10Health(status: "ok", version: "3.0.1") }

    func latestScan(window: String) async throws -> K10Scan {
        let cutoff = window == "morning" ? "2026-09-07T09:00:00+08:00" : "2026-09-06T21:00:00+08:00"
        let coverage = K10SourceCoverage(
            sourceKey: "synthetic", scope: "仅 UI 验证，不代表市场覆盖", authorization: "synthetic", isMarketWide: false,
            pagination: "single_page", limitations: ["合成数据"], pagesFetched: 1, pagesExpected: 1, complete: true,
            errors: [], state: "completed", windowStartAt: nil, windowCutoffAt: cutoff, successWatermark: cutoff, gaps: []
        )
        return K10Scan(schemaVersion: "k10-api-v2", scanId: "synthetic-\(window)-scan", window: window, cutoffAt: cutoff,
                       status: "completed", coverageStatus: "complete", coverageGaps: [], sourceCoverage: [coverage], createdAt: cutoff, completedAt: cutoff)
    }

    func publications() async throws -> [K10Publication] {
        [
            K10Publication(schemaVersion: "k10-api-v2", batchId: "synthetic-evening", scanId: "synthetic-evening-scan", publicationKind: "evening", availableAt: "2026-09-06T21:10:00+08:00", createdAt: "2026-09-06T21:10:00+08:00", sampleCount: 1),
            K10Publication(schemaVersion: "k10-api-v2", batchId: "synthetic-morning", scanId: "synthetic-morning-scan", publicationKind: "morning", availableAt: "2026-09-07T09:40:00+08:00", createdAt: "2026-09-07T09:40:00+08:00", sampleCount: 1)
        ]
    }

    func companyWindows() async throws -> [K10CompanyWindow] {
        let eveningAction = actions[Self.first.companyWindowId] ?? WindowAction()
        let lateAction = actions[Self.late.companyWindowId] ?? WindowAction()
        let evening = makeEveningWindow(action: eveningAction)
        let morningLate = makeLateWindow(action: lateAction)
        return [evening, morningLate]
    }

    func opportunity(id: String) async throws -> K10OpportunityDetail {
        if id == Self.late.opportunityId {
            return detail(for: Self.late, eventHeadline: "合成晨间迟到催化", facts: [
                K10CommonFact(key: "合成说明", text: "这是独立晨间迟到发布，用于验证从下一交易日开始的固定窗口。")
            ], samples: [Self.lateSample], lifecycleEvents: [])
        }
        let selected = id == Self.second.opportunityId ? Self.second : Self.first
        return detail(for: selected, eventHeadline: "合成验证进展与关联更新", facts: [
            K10CommonFact(key: "新增事实", text: "合成资料只用于验证事实、来源和比较信息的分层展示。"),
            K10CommonFact(key: "待核边界", text: "合成搜索摘录没有完整正文，不能作为已核实结论。")
        ], samples: [Self.sample], lifecycleEvents: [
            K10LifecycleEvent(lifecycleEventId: "synthetic-risk", kind: "risk", reason: "合成重大反证：需要重新阅读资料，不代表真实风险。", sourceRefs: [Self.tavilyExcerpt], content: [:], occurredAt: "2026-09-07T08:40:00+08:00", createdAt: "2026-09-07T08:40:00+08:00")
        ])
    }

    func act(companyWindowID: String, request: K10SelectionRequest) async throws -> K10SelectionAction {
        guard [Self.first.companyWindowId, Self.late.companyWindowId].contains(companyWindowID) else {
            throw K10APIError.notFound("合成公司窗口不存在")
        }
        var action = actions[companyWindowID] ?? WindowAction()
        let isEvening = companyWindowID == Self.first.companyWindowId
        let isWithdrawal = request.action == "withdraw" && action.hasAnalysis
        let timestamp: String = isEvening
            ? (isWithdrawal ? "2026-09-07T09:45:00+08:00" : "2026-09-07T09:15:00+08:00")
            : "2026-09-08T09:40:00+08:00"
        let postFreeze = isWithdrawal || !isEvening
        switch request.action {
        case "keep":
            action.state = "kept"
            action.hasAnalysis = isEvening || action.hasAnalysis
            if !postFreeze { action.frozenState = "selected" }
        case "skip":
            action.state = "skipped"
            if !postFreeze { action.frozenState = "skipped" }
        case "restore", "withdraw":
            action.state = "unhandled"
            if !postFreeze { action.frozenState = nil }
        default:
            throw K10APIError.conflict("不支持的合成选择动作")
        }
        action.lastActionAt = timestamp
        action.postFreeze = postFreeze
        action.actionIDs.append("synthetic-action-\(companyWindowID)-\(request.action)-\(action.actionIDs.count + 1)")
        actions[companyWindowID] = action
        let candidate = isEvening ? "synthetic-evening-candidate" : "synthetic-late-candidate"
        return K10SelectionAction(
            schemaVersion: "k10-api-v2", actionId: action.actionIDs.last ?? UUID().uuidString, companyWindowId: companyWindowID,
            representativeCandidateId: candidate, state: action.state, lastActionAt: timestamp, postFreeze: postFreeze,
            observationId: action.hasAnalysis ? "synthetic-evening-observation" : nil,
            analysisJobId: action.hasAnalysis ? "synthetic-evening-analysis" : nil,
            replayed: false
        )
    }

    func selections() async throws -> [K10SelectionDetail] {
        let action = actions[Self.first.companyWindowId] ?? WindowAction()
        guard action.hasAnalysis else { return [] }
        let lineage = K10AnalysisLineage(
            candidateId: "synthetic-evening-candidate", event: K10AnalysisEventLineage(eventId: Self.first.eventId, revision: Self.first.eventRevision),
            mappingIds: ["synthetic-mapping"], documentVersions: [Self.source, Self.tavilyExcerpt], inputCutoffAt: "2026-09-06T21:00:00+08:00"
        )
        let pro = K10Analysis(
            analysisId: "synthetic-pro", observationId: "synthetic-evening-observation", revision: 1, role: "pro", status: "completed",
            inputCutoffAt: "2026-09-06T21:00:00+08:00", sourceRefs: [Self.source, Self.tavilyExcerpt], inputLineage: lineage,
            fullText: Self.proMarkdown, provider: "synthetic", model: "synthetic", promptVersion: "k10-v1.4", usage: nil, error: nil
        )
        let con = K10Analysis(
            analysisId: "synthetic-con", observationId: "synthetic-evening-observation", revision: 1, role: "con", status: "completed",
            inputCutoffAt: "2026-09-06T21:00:00+08:00", sourceRefs: [Self.source, Self.tavilyExcerpt], inputLineage: lineage,
            fullText: Self.conMarkdown, provider: "synthetic", model: "synthetic", promptVersion: "k10-v1.4", usage: nil, error: nil
        )
        let job = K10Job(
            schemaVersion: "k10-api-v2", jobId: "synthetic-evening-analysis", kind: "analysis", status: "completed", stage: "done",
            attemptCount: 1, inputVersion: "k10-v1.4", inputCutoffAt: "2026-09-06T21:00:00+08:00",
            createdAt: "2026-09-06T21:11:00+08:00", updatedAt: "2026-09-06T21:12:00+08:00", error: nil
        )
        return [K10SelectionDetail(
            schemaVersion: "k10-api-v2", companyWindowId: Self.first.companyWindowId, representativeCandidateId: "synthetic-evening-candidate",
            state: action.state, lastActionAt: action.lastActionAt, postFreeze: action.postFreeze,
            observationId: "synthetic-evening-observation", opportunities: [Self.first, Self.second], analyses: [pro, con],
            analysisJobId: job.jobId, latestJob: job
        )]
    }

    func document(id: String, revision: Int?, offset: Int, limit: Int) async throws -> K10DocumentPage {
        if id == "synthetic-tavily-excerpt" {
            return K10DocumentPage(
                schemaVersion: "k10-api-v2", documentId: id, revision: revision ?? 1, sourceKey: "tavily", externalId: id,
                canonicalUrl: "https://example.invalid/k10/synthetic-tavily", title: Self.tavilyExcerpt.title, publishedAt: nil,
                publishedPrecision: "unknown", fetchedAt: "2026-09-06T20:06:00+08:00", excerpt: Self.tavilyExcerpt.excerpt,
                body: nil, page: K10Page(nextCursor: nil)
            )
        }
        let firstPage = offset == 0
        return K10DocumentPage(
            schemaVersion: "k10-api-v2", documentId: id, revision: revision ?? 1, sourceKey: "synthetic-news", externalId: "synthetic-document",
            canonicalUrl: "https://example.invalid/k10/synthetic-document", title: Self.source.title, publishedAt: Self.source.publishedAt,
            publishedPrecision: "exact", fetchedAt: "2026-09-06T20:00:00+08:00", excerpt: Self.source.excerpt,
            body: firstPage ? Self.documentFirstPage : Self.documentSecondPage, page: K10Page(nextCursor: firstPage ? "6000" : nil)
        )
    }

    func job(id: String) async throws -> K10Job {
        K10Job(schemaVersion: "k10-api-v2", jobId: id, kind: "analysis", status: "completed", stage: "done", attemptCount: 1,
               inputVersion: "k10-v1.4", inputCutoffAt: "2026-09-06T21:00:00+08:00", createdAt: "2026-09-06T21:11:00+08:00", updatedAt: "2026-09-06T21:12:00+08:00", error: nil)
    }
    func retryJob(id: String, expectedAttemptCount: Int) async throws -> K10Job { try await job(id: id) }

    func results() async throws -> K10Results {
        let d1 = K10MarketDay(
            tradeDate: "2026-09-07", availability: "available", closeLimitUp: true, touchedLimitUp: true, firstTouchedAt: "10:21",
            open: 10.0, high: 12.0, low: 9.9, close: 12.0, preClose: 10.0, limitUpPrice: 12.0,
            sourceRefs: [Self.marketSnapshot], obtainedAt: "2026-09-07T15:05:00+08:00"
        )
        let d2 = K10MarketDay(
            tradeDate: "2026-09-08", availability: "available", closeLimitUp: false, touchedLimitUp: false, firstTouchedAt: nil,
            open: 12.1, high: 12.6, low: 11.7, close: 12.2, preClose: 12.0, limitUpPrice: 14.4,
            sourceRefs: [Self.marketSnapshot], obtainedAt: "2026-09-08T15:05:00+08:00"
        )
        let metrics = K10EvaluationMetrics(sampleCount: 1, eligibleCount: 1, hitCount: 1, hitRate: 1, touchRate: 1, incompleteCount: 0, pendingCount: 0, observedCompleteCount: 1, knownHitCount: 1, touchCount: 1, suspendedCount: 0, dataGapCount: 0, anomalyCount: 0, selectionPendingCount: 0)
        let evaluation = K10Evaluation(
            companyWindowId: Self.first.companyWindowId, opportunityIds: [Self.first.opportunityId, Self.second.opportunityId], companyCode: Self.first.companyCode,
            sampleClass: "primary", selection: K10SelectionSnapshot(state: "selected", frozenAt: "2026-09-07T09:20:00+08:00", actionIds: ["synthetic-action-evening"]),
            state: "completed", revision: 1, updatedAt: "2026-09-08T15:05:00+08:00", d1: d1, d2: d2, primaryEligible: true,
            closeLimitHitAny: true, firstTouchDay: "D1", firstTouchStatus: "confirmed", knownTouchDays: ["D1"], d1OpenGap: 0,
            d1PriceChanges: ["open": 0, "high": 0.20, "low": -0.01, "close": 0.20],
            d2PriceChanges: ["open": 0.21, "high": 0.26, "low": 0.17, "close": 0.22],
            windowPriceChanges: ["open": 0, "high": 0.26, "low": -0.01, "close": 0.22],
            comparability: "raw_comparable", gaps: [], factRefs: [Self.marketSnapshot]
        )
        let gapDay = K10MarketDay(tradeDate: "2026-09-09", availability: "data_gap", closeLimitUp: nil, touchedLimitUp: nil, firstTouchedAt: nil, open: nil, high: nil, low: nil, close: nil, preClose: nil, limitUpPrice: nil, sourceRefs: [Self.marketSnapshot], obtainedAt: nil)
        let overlap = K10Evaluation(
            companyWindowId: "synthetic-overlap-window", opportunityIds: ["synthetic-overlap-opportunity"], companyCode: "300002.SZ", sampleClass: "overlap",
            selection: K10SelectionSnapshot(state: "unhandled", frozenAt: "2026-09-08T09:30:00+08:00", actionIds: []), state: "incomplete", revision: 1,
            updatedAt: "2026-09-09T15:05:00+08:00", d1: d2, d2: gapDay, primaryEligible: false, closeLimitHitAny: nil,
            firstTouchDay: nil, firstTouchStatus: "unknown", knownTouchDays: ["D1"], d1OpenGap: 0,
            d1PriceChanges: ["open": 0.21, "high": 0.26, "low": 0.17, "close": 0.22],
            d2PriceChanges: nil, windowPriceChanges: nil, comparability: "unknown", gaps: ["D2 行情缺数"], factRefs: [Self.marketSnapshot]
        )
        let empty = K10EvaluationMetrics(sampleCount: 0, eligibleCount: 0, hitCount: 0, hitRate: nil, touchRate: nil, incompleteCount: 0, pendingCount: 0, observedCompleteCount: 0, knownHitCount: 0, touchCount: 0, suspendedCount: 0, dataGapCount: 0, anomalyCount: 0, selectionPendingCount: 0)
        let incomplete = K10EvaluationMetrics(sampleCount: 1, eligibleCount: 0, hitCount: 0, hitRate: nil, touchRate: nil, incompleteCount: 1, pendingCount: 0, observedCompleteCount: 0, knownHitCount: 0, touchCount: 0, suspendedCount: 0, dataGapCount: 1, anomalyCount: 0, selectionPendingCount: 0)
        let primary = ["all": metrics, "selected": metrics, "skipped": empty, "unhandled": empty]
        return K10Results(
            schemaVersion: "k10-api-v2", state: "available", reason: nil, asOf: "2026-09-08T15:05:00+08:00", primary: primary, overlap: incomplete,
            records: [evaluation, overlap], cohorts: [K10ResultsCohort(batchId: "synthetic-evening", d1TradeDate: "2026-09-07", d2TradeDate: "2026-09-08", evaluationVersion: "k10-evaluation-v1.4", companySampleCount: 1, catalystEventCount: 2, primary: primary, overlap: incomplete)],
            eventGroups: [K10ResultsEventGroup(eventId: Self.first.eventId, headline: "合成催化共同事件", companyWindowIds: [Self.first.companyWindowId], opportunityIds: [Self.first.opportunityId, Self.second.opportunityId], companySampleCount: 1, catalystCount: 2, primary: primary)]
        )
    }

    func configuration() async throws -> K10Configuration {
        K10Configuration(schemaVersion: "k10-api-v2", configId: "synthetic", configRevision: 1, scopes: [
            K10ConfigurationScope(scope: "candidate", state: "configured", missing: [], errors: []),
            K10ConfigurationScope(scope: "analysis", state: "configured", missing: [], errors: []),
            K10ConfigurationScope(scope: "evaluation", state: "configured", missing: [], errors: [])
        ])
    }
    func usageSummary() async throws -> K10UsageSummary {
        K10UsageSummary(days: [], totals: K10UsageTotals(calls: 0, failed: 0, usageUnavailable: 0, promptTokens: nil, completionTokens: nil, totalTokens: nil, tavilyCredits: nil, durationMs: nil))
    }

    private func makeEveningWindow(action: WindowAction) -> K10CompanyWindow {
        K10CompanyWindow(
            schemaVersion: "k10-api-v2", companyWindowId: Self.first.companyWindowId, companyCode: Self.first.companyCode, companyName: Self.first.companyName,
            firstBatchId: Self.first.firstBatchId, d0TradeDate: Self.first.d0TradeDate, d1TradeDate: Self.first.d1TradeDate, d2TradeDate: Self.first.d2TradeDate,
            d1SelectionAt: "2026-09-07T09:30:00+08:00", d2CloseAt: "2026-09-08T15:00:00+08:00", sampleClass: "primary", overlapsWindowId: nil,
            selection: selectionSnapshot(for: action, frozenAt: "2026-09-07T09:30:00+08:00"), currentSelectionState: action.state,
            lastActionAt: action.lastActionAt, postFreeze: action.postFreeze, opportunities: [Self.first, Self.second], samples: [Self.sample], createdAt: Self.first.createdAt
        )
    }

    private func makeLateWindow(action: WindowAction) -> K10CompanyWindow {
        K10CompanyWindow(
            schemaVersion: "k10-api-v2", companyWindowId: Self.late.companyWindowId, companyCode: Self.late.companyCode, companyName: Self.late.companyName,
            firstBatchId: Self.late.firstBatchId, d0TradeDate: Self.late.d0TradeDate, d1TradeDate: Self.late.d1TradeDate, d2TradeDate: Self.late.d2TradeDate,
            d1SelectionAt: "2026-09-08T09:30:00+08:00", d2CloseAt: "2026-09-09T15:00:00+08:00", sampleClass: "primary", overlapsWindowId: nil,
            selection: selectionSnapshot(for: action, frozenAt: "2026-09-08T09:30:00+08:00"), currentSelectionState: action.state,
            lastActionAt: action.lastActionAt, postFreeze: action.postFreeze, opportunities: [Self.late], samples: [Self.lateSample], createdAt: Self.late.createdAt
        )
    }

    private func selectionSnapshot(for action: WindowAction, frozenAt: String) -> K10SelectionSnapshot? {
        guard let state = action.frozenState else { return nil }
        return K10SelectionSnapshot(state: state, frozenAt: frozenAt, actionIds: action.actionIDs)
    }

    private func detail(for opportunity: K10Opportunity, eventHeadline: String, facts: [K10CommonFact], samples: [K10PublicationSample], lifecycleEvents: [K10LifecycleEvent]) -> K10OpportunityDetail {
        K10OpportunityDetail(
            schemaVersion: opportunity.schemaVersion, opportunityId: opportunity.opportunityId, opportunityKey: opportunity.opportunityKey, companyCode: opportunity.companyCode,
            companyName: opportunity.companyName, eventId: opportunity.eventId, eventRevision: opportunity.eventRevision, catalystStage: opportunity.catalystStage,
            relatedOpportunityId: opportunity.relatedOpportunityId, companyWindowId: opportunity.companyWindowId, firstBatchId: opportunity.firstBatchId,
            availableAt: opportunity.availableAt, d0TradeDate: opportunity.d0TradeDate, d1TradeDate: opportunity.d1TradeDate, d2TradeDate: opportunity.d2TradeDate,
            sampleClass: opportunity.sampleClass, overlapsWindowId: opportunity.overlapsWindowId, lifecycle: opportunity.lifecycle, sourceMarker: opportunity.sourceMarker,
            latePublication: opportunity.latePublication, createdAt: opportunity.createdAt, eventHeadline: eventHeadline, commonFacts: facts, samples: samples, lifecycleEvents: lifecycleEvents
        )
    }

    private static let proMarkdown = """
    ## 合成正方观点

    合成资料描述了验证环节的新进展。它只用于 Neckline V3 的长文阅读验收，不能被理解为真实公司披露、投资判断或交易建议。

    - 资料修订 1 把合成科技列为直接业务关联方，因此页面应把新增事实、公司关系与比较理由分别呈现。
    - 同卡的第二条催化只是关联更新，仍共用同一公司窗口、用户选择与 D1/D2 市场事实。
    - 当前比较只说明资料关系强弱：若没有新增可追溯来源，不能把“主推”解释成确定性结论。

    ### 资料引用与观察边界
    原始资料 `synthetic-document@1` 保存了可分页正文；独立核验 `synthetic-tavily-excerpt@1` 只保存摘要，发布时间和完整正文仍待核。固定窗口只记录 D1/D2 的市场事实；资料变更、反证或用户取消关注都应保留修订与历史，不生成价格计划。
    """
    private static let conMarkdown = """
    ## 合成反方质疑

    当前合成资料不能证明订单、收入、客户关系或业务结果已经发生。页面展示这段内容，是为了确认反方分析可以完整阅读、保留标题与列表，并与正方共享固定来源引用。

    - 搜索摘录没有完整正文，不能被当作独立核实结论，也不能替代原始资料 `synthetic-document@1`。
    - 直接关联来自合成资料的表达；若后续来源否认关键关系，风险更新必须优先显示，但不删除原机会或两日样本。
    - D1/D2 的涨停、触板和价格变化只是一组市场事实，不能反过来证明催化成立，更不能引出买卖、持仓或收益建议。

    ### 结论与待核项
    在取得可追溯的完整正文、明确发布时间和新的关系资料前，合成搜索结果仍应标为待核。此文本仅服务 UI 验证，不构成真实研究或交易建议。
    """
    private static let documentFirstPage = """
    # 合成原始全文（第一页）

    这份内容仅用于 Neckline V3 页面验收。它模拟带标题、段落与长文阅读的原始资料，不代表真实公司公告。

    - 新增事实：验证环节出现阶段进展。
    - 公司关联：资料将合成科技列为直接关联方。
    """
    private static let documentSecondPage = """
    ## 合成原始全文（第二页）

    分页后的内容继续保留同一个资料修订。此处用于验证“加载后续正文”后，前后页会合并而不是替换。

    排序改变条件仍需来自后续可追溯资料，不能由页面自行推断。
    """
}
#endif
