import XCTest
import Foundation
import SwiftUI
#if os(macOS)
import AppKit
#else
import UIKit
#endif
@testable import Neckline

final class K10V3Tests: XCTestCase {
    func testSyntheticWindowActionsAreIndependentAndWithdrawalKeepsAnalysisHistory() async throws {
        let service = K10SyntheticUIService()
        let before = try await service.companyWindows()
        XCTAssertEqual(before.count, 2)
        let evening = try XCTUnwrap(before.first(where: { $0.companyWindowId == "synthetic-evening-window" }))
        let late = try XCTUnwrap(before.first(where: { $0.companyWindowId == "synthetic-morning-late-window" }))
        XCTAssertEqual(evening.opportunities.count, 2, "同批同公司催化应合为一个公司窗口")
        XCTAssertTrue(evening.opportunities.allSatisfy { $0.sourceMarker == "evening" && $0.latePublication == false })
        XCTAssertEqual(late.opportunities.first?.sourceMarker, "morning")
        XCTAssertEqual(late.opportunities.first?.latePublication, true)
        XCTAssertEqual(late.d1TradeDate, "2026-09-08")
        XCTAssertEqual(late.d2TradeDate, "2026-09-09")

        _ = try await service.act(companyWindowID: evening.companyWindowId, request: K10SelectionRequest(action: "keep", idempotencyKey: "keep-evening", reason: nil))
        _ = try await service.act(companyWindowID: late.companyWindowId, request: K10SelectionRequest(action: "skip", idempotencyKey: "skip-late", reason: nil))
        let afterIndependentActions = try await service.companyWindows()
        let keptEvening = try XCTUnwrap(afterIndependentActions.first(where: { $0.id == evening.id }))
        let skippedLate = try XCTUnwrap(afterIndependentActions.first(where: { $0.id == late.id }))
        XCTAssertEqual(keptEvening.currentSelectionState, "kept")
        XCTAssertEqual(keptEvening.selection?.state, "selected")
        XCTAssertEqual(keptEvening.lastActionAt, "2026-09-07T09:15:00+08:00")
        XCTAssertFalse(keptEvening.postFreeze ?? true)
        XCTAssertEqual(skippedLate.currentSelectionState, "skipped")
        XCTAssertNil(skippedLate.selection, "晨间迟到窗口的开盘后操作不能回写冻结组")
        XCTAssertTrue(skippedLate.postFreeze ?? false)

        let selectionDetailsAfterKeep = try await service.selections()
        let keptDetail = try XCTUnwrap(selectionDetailsAfterKeep.first)
        XCTAssertEqual(keptDetail.companyWindowId, evening.companyWindowId)
        XCTAssertEqual(keptDetail.analyses.count, 2)
        XCTAssertTrue(keptDetail.analyses.allSatisfy { $0.fullText?.contains("合成") == true })

        let withdrawal = try await service.act(companyWindowID: evening.companyWindowId, request: K10SelectionRequest(action: "withdraw", idempotencyKey: "withdraw-evening", reason: nil))
        XCTAssertEqual(withdrawal.state, "unhandled")
        XCTAssertEqual(withdrawal.observationId, "synthetic-evening-observation")
        XCTAssertEqual(withdrawal.analysisJobId, "synthetic-evening-analysis")
        let windowsAfterWithdrawal = try await service.companyWindows()
        let afterWithdrawal = try XCTUnwrap(windowsAfterWithdrawal.first(where: { $0.id == evening.id }))
        XCTAssertEqual(afterWithdrawal.currentSelectionState, "unhandled")
        XCTAssertEqual(afterWithdrawal.selection?.state, "selected", "开盘后取消关注不能改写已冻结留下组")
        XCTAssertEqual(afterWithdrawal.lastActionAt, "2026-09-07T09:45:00+08:00")
        XCTAssertTrue(afterWithdrawal.postFreeze ?? false)
        let selectionDetailsAfterWithdrawal = try await service.selections()
        let retained = try XCTUnwrap(selectionDetailsAfterWithdrawal.first)
        XCTAssertEqual(retained.observationId, "synthetic-evening-observation")
        XCTAssertEqual(retained.analyses.map(\.role).sorted(), ["con", "pro"])
    }

    func testSyntheticWindowsAndMarketFactsUseDistinctDatesAndChinextTwentyPercentLimit() async throws {
        let service = K10SyntheticUIService()
        let publications = try await service.publications()
        XCTAssertEqual(publications.first(where: { $0.batchId == "synthetic-morning" })?.availableAt, "2026-09-07T09:40:00+08:00")

        let results = try await service.results()
        let primary = try XCTUnwrap(results.records.first(where: { $0.companyWindowId == "synthetic-evening-window" }))
        let d1 = try XCTUnwrap(primary.d1)
        let d2 = try XCTUnwrap(primary.d2)
        XCTAssertEqual(d1.tradeDate, "2026-09-07")
        XCTAssertEqual(d2.tradeDate, "2026-09-08")
        XCTAssertNotEqual(d1.tradeDate, d2.tradeDate)
        XCTAssertEqual(d1.preClose, 10.0)
        XCTAssertEqual(d1.close, 12.0)
        XCTAssertEqual(d1.limitUpPrice, 12.0, "创业板合成样本应使用 20% 的 10→12 涨停价")
        XCTAssertTrue(d1.closeLimitUp ?? false)
        XCTAssertEqual(d2.preClose, 12.0)
        XCTAssertEqual(d2.limitUpPrice, 14.4)
        XCTAssertFalse(d2.closeLimitUp ?? true)
        XCTAssertFalse(d2.touchedLimitUp ?? true)
        XCTAssertEqual(primary.knownTouchDays, ["D1"])
        XCTAssertEqual(try XCTUnwrap(primary.d1PriceChanges?["high"] ?? nil), 0.20, accuracy: 0.0001)
        XCTAssertEqual(try XCTUnwrap(primary.d1PriceChanges?["low"] ?? nil), -0.01, accuracy: 0.0001)
        XCTAssertEqual(try XCTUnwrap(primary.d1PriceChanges?["close"] ?? nil), 0.20, accuracy: 0.0001)
        XCTAssertEqual(try XCTUnwrap(primary.d2PriceChanges?["high"] ?? nil), 0.26, accuracy: 0.0001)
        XCTAssertEqual(try XCTUnwrap(primary.d2PriceChanges?["low"] ?? nil), 0.17, accuracy: 0.0001)
        XCTAssertEqual(try XCTUnwrap(primary.d2PriceChanges?["close"] ?? nil), 0.22, accuracy: 0.0001)
        XCTAssertEqual(try XCTUnwrap(primary.windowPriceChanges?["open"] ?? nil), 0, accuracy: 0.0001)
        XCTAssertEqual(try XCTUnwrap(primary.windowPriceChanges?["high"] ?? nil), 0.26, accuracy: 0.0001)
        XCTAssertEqual(try XCTUnwrap(primary.windowPriceChanges?["low"] ?? nil), -0.01, accuracy: 0.0001)
        XCTAssertEqual(try XCTUnwrap(primary.windowPriceChanges?["close"] ?? nil), 0.22, accuracy: 0.0001)
        let fact = try XCTUnwrap(primary.factRefs.first)
        XCTAssertEqual(fact.sourceKey, "market_snapshot")
        XCTAssertNil(fact.collectedAt)
        XCTAssertNil(fact.fetchedAt, "来源原始采集时间未知时不能把整理时间伪造成采集时间")
    }

    func testSyntheticDetailsExposeNaturalEvidenceLongMarkdownAndExcerptOnlyDocument() async throws {
        let service = K10SyntheticUIService()
        let detail = try await service.opportunity(id: "synthetic-opportunity-1")
        XCTAssertTrue(detail.eventHeadline?.contains("合成") ?? false)
        XCTAssertEqual(detail.samples.count, 1)
        XCTAssertGreaterThanOrEqual(detail.samples[0].evidence.count, 2)
        XCTAssertTrue(detail.samples[0].evidence.contains { $0.claim.contains("验证环节") })
        XCTAssertTrue(detail.samples[0].evidence.contains { $0.sourceRef.sourceKey == "tavily" && $0.sourceRef.excerpt != nil })

        _ = try await service.act(companyWindowID: "synthetic-evening-window", request: K10SelectionRequest(action: "keep", idempotencyKey: "markdown", reason: nil))
        let selectionDetailsForMarkdown = try await service.selections()
        let analyses = try XCTUnwrap(selectionDetailsForMarkdown.first?.analyses)
        XCTAssertTrue(analyses.allSatisfy { ($0.fullText?.count ?? 0) > 180 })
        XCTAssertTrue(analyses.contains { $0.fullText?.contains("##") == true })

        let tavily = try await service.document(id: "synthetic-tavily-excerpt", revision: 1, offset: 0, limit: 6000)
        XCTAssertNil(tavily.body)
        XCTAssertNotNil(tavily.excerpt)
        XCTAssertNil(tavily.page.nextCursor)
    }

    @MainActor func testAppModelRefreshUsesCompanyWindowsNotPlans() async {
        let service = K10SyntheticUIService()
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        XCTAssertFalse(model.companyWindows.isEmpty)
        XCTAssertEqual(model.companyWindows.first(where: { $0.companyCode == "300001.SZ" })?.opportunities.count, 2)
        XCTAssertEqual(model.publications.first?.sampleCount, 1)
        if case .ready = model.state {} else { XCTFail("synthetic model should load") }
    }

    func testSyntheticB39ResearchFixtureKeepsFailurePendingAndRumorDisclosureDistinct() async throws {
        let service = K10SyntheticUIService(presentsB39State: true)
        let scan = try await service.latestScan(window: "evening")
        let summary = try XCTUnwrap(scan.researchSummary)
        XCTAssertTrue(summary.executionFailed)
        XCTAssertFalse(summary.comparisonComplete)
        XCTAssertEqual(summary.companyCounts.pending, 1)
        let assessments = try await service.researchAssessments(scanID: scan.scanId)
        XCTAssertEqual(Set(assessments.items.map(\.role)), ["primary", "pending", "excluded"])
        let disclosure = try XCTUnwrap(assessments.items.first(where: { $0.role == "primary" })?.evidenceDisclosure)
        XCTAssertEqual(disclosure.verificationStatus, "unverified")
        XCTAssertTrue(disclosure.isRumor)
        XCTAssertEqual(disclosure.originStatus, "unknown")
        XCTAssertFalse(try XCTUnwrap(disclosure.conditionalAnalysis).isEmpty)
    }

    @MainActor func testAppModelLoadsSyntheticB39SummaryAndAllAssessments() async throws {
        let service = K10SyntheticUIService(presentsB39State: true)
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        let scan = try XCTUnwrap(model.scanSummaries.first(where: { $0.window == "evening" }))
        XCTAssertTrue(scan.researchSummary?.executionFailed ?? false)
        XCTAssertEqual(Set(model.researchAssessments[scan.scanId, default: []].map(\.role)), ["primary", "pending", "excluded"])
    }

    @MainActor func testMorningTransportFailurePreservesSameConnectionReportThenRecovers() async throws {
        let service = ControlledK10Service(batchID: "morning-retry")
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        let previous = try XCTUnwrap(model.morningReport)

        await service.setMorningFailure(.server(503, "反向代理暂不可用"))
        await model.refresh()
        XCTAssertEqual(model.morningReport, previous)
        XCTAssertEqual(model.morningReportLoadError, "反向代理暂不可用")
        if case .ready = model.state {} else { XCTFail("a morning-only 503 must keep the page readable") }

        await service.setMorningFailure(nil)
        await model.refresh()
        XCTAssertEqual(model.morningReport?.reportId, previous.reportId)
        XCTAssertNil(model.morningReportLoadError)

        await service.setMorningFailure(.notFound("尚无晨报"))
        await model.refresh()
        XCTAssertNil(model.morningReport)
        XCTAssertNil(model.morningReportLoadError)

        await service.setMorningFailure(nil)
        await model.refresh()
        XCTAssertNotNil(model.morningReport)
        model.resetForConnectionChange()
        await service.setMorningFailure(.server(503, "新连接晨报不可用"))
        await model.refresh()
        XCTAssertNil(model.morningReport, "新连接不能泄露旧连接的晨报")
        XCTAssertEqual(model.morningReportLoadError, "新连接晨报不可用")
    }

    @MainActor func testSupplementaryRequestRefreshesAnAlreadyOpenAnalysisChain() async throws {
        let service = ControlledK10Service(batchID: "analysis-refresh")
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        let window = try XCTUnwrap(model.companyWindows.first)
        await model.loadAnalysisChain(for: window)
        XCTAssertEqual(model.analysisChains[window.companyWindowId]?.items.first?.revision, 1)

        await model.requestAnalysis(kind: "user_question", question: "新资料会改变判断吗？", sourceRefs: [], for: window)
        XCTAssertEqual(model.analysisChains[window.companyWindowId]?.items.first?.revision, 2)
        XCTAssertFalse(model.analysisRequestInFlightWindowIDs.contains(window.companyWindowId))
    }

    @MainActor func testRetryUsesTheFailedAnalysisChainJobWithoutCreatingAnotherRequest() async throws {
        let service = ControlledK10Service(batchID: "analysis-retry")
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        let window = try XCTUnwrap(model.companyWindows.first)
        let failed = K10Job(schemaVersion: "k10-api-v2", jobId: "supplementary-failed", kind: "analysis", status: "failed", stage: "debate", attemptCount: 2, inputVersion: "K10-v1.4", inputCutoffAt: "2026-09-07T09:00:00+08:00", createdAt: "2026-09-07T09:01:00+08:00", updatedAt: "2026-09-07T09:02:00+08:00", error: K10JobFailure(reason: "provider_failed", message: "合成失败"))
        await model.retryAnalysis(job: failed, companyWindowID: window.companyWindowId)
        let retryCount = await service.retryCallCount()
        XCTAssertEqual(retryCount, 1)
    }

    @MainActor func testAnalysisChainLoadCannotCrossConnectionGeneration() async throws {
        let gate = RefreshGate()
        let old = ControlledK10Service(batchID: "analysis-old", analysisChainGate: gate)
        let current = ControlledK10Service(batchID: "analysis-current")
        let services = ServiceBox(old)
        let model = AppModel(serviceFactory: { services.service }, cacheClearer: {})
        await model.refresh()
        let oldWindow = try XCTUnwrap(model.companyWindows.first)
        let loading = Task { await model.loadAnalysisChain(for: oldWindow) }
        await gate.waitUntilEntered()
        model.resetForConnectionChange()
        services.service = current
        await model.refresh()
        await gate.open()
        await loading.value
        XCTAssertTrue(model.analysisChains.isEmpty)
    }

    @MainActor func testCancelledAnalysisChainLoadDoesNotReportNetworkFailure() async throws {
        let service = ControlledK10Service(batchID: "analysis-cancelled", analysisChainCancellation: true)
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        let window = try XCTUnwrap(model.companyWindows.first)

        await model.loadAnalysisChain(for: window)

        XCTAssertEqual(model.state, .ready)
        XCTAssertFalse(model.offline)
        XCTAssertNil(model.toast)
        XCTAssertNil(model.analysisChains[window.companyWindowId])
    }

    @MainActor func testNewerAnalysisChainReloadWinsWithinOneConnection() async throws {
        let gate = RefreshGate()
        let service = ControlledK10Service(batchID: "analysis-overlap", analysisChainGate: gate, firstAnalysisChainRevision: 1, laterAnalysisChainRevision: 2)
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        let window = try XCTUnwrap(model.companyWindows.first)

        let firstLoad = Task { await model.loadAnalysisChain(for: window) }
        await gate.waitUntilEntered()
        await model.loadAnalysisChain(for: window)
        await gate.open()
        await firstLoad.value

        XCTAssertEqual(model.analysisChains[window.companyWindowId]?.items.first?.revision, 2)
        XCTAssertNil(model.toast)
    }

    func testSourceReferenceIdentityKeepsDocumentRevisionsDistinct() {
        let first = K10SourceReference(documentId: "doc", factId: nil, companyCode: nil, tradeDate: nil, revision: 1, sourceKey: "test", title: nil, url: nil, excerpt: nil, publishedAt: "2026-09-06", publishedPrecision: "date", fetchedAt: nil)
        let second = K10SourceReference(documentId: "doc", factId: nil, companyCode: nil, tradeDate: nil, revision: 2, sourceKey: "test", title: nil, url: nil, excerpt: nil, publishedAt: "2026-09-06", publishedPrecision: "date", fetchedAt: nil)
        XCTAssertNotEqual(first.id, second.id)
        let marketFirst = K10SourceReference(documentId: nil, factId: "fact", companyCode: "300001.SZ", tradeDate: "2026-09-07", revision: 1, sourceKey: "market", title: nil, url: nil, excerpt: nil, publishedAt: nil, publishedPrecision: "unknown", fetchedAt: nil)
        let marketSecond = K10SourceReference(documentId: nil, factId: "fact", companyCode: "300001.SZ", tradeDate: "2026-09-08", revision: 2, sourceKey: "market", title: nil, url: nil, excerpt: nil, publishedAt: nil, publishedPrecision: "unknown", fetchedAt: nil)
        XCTAssertNotEqual(marketFirst.id, marketSecond.id)
    }

    func testV302RealProducerContractDecodesMorningLifecycleAnalysisHistoryAndMarket() async throws {
        let environment = ProcessInfo.processInfo.environment
        guard let raw = environment["NK_V304_API_URL"] ?? environment["NK_V303_API_URL"] ?? environment["NK_V302_API_URL"], let baseURL = URL(string: raw) else {
            throw XCTSkip("set NK_V303_API_URL (or NK_V302_API_URL) to run the producer-to-Swift contract test")
        }
        let client = K10APIClient(baseURL: baseURL, token: "temporary-test-token")
        let latestReport = try await client.latestMorningReport()
        let report = try XCTUnwrap(latestReport)
        XCTAssertFalse(report.items.isEmpty)
        let allowedSections: Set<String> = ["major_contrary", "thesis_changed", "continuing_or_expiring", "new", "needs_review"]
        XCTAssertTrue(report.items.allSatisfy { allowedSections.contains($0.section) })
        XCTAssertTrue(report.items.contains { !$0.sourceRefs.isEmpty })

        var details: [K10OpportunityDetail] = []
        for opportunityID in Set(report.items.compactMap(\.opportunityId)) {
            details.append(try await client.opportunity(id: opportunityID))
        }
        let lifecycleDetail = try XCTUnwrap(details.first(where: { $0.lifecycleEvents.contains { containsNestedValue($0.content) } }))
        XCTAssertEqual(try JSONDecoder().decode(K10OpportunityDetail.self, from: JSONEncoder().encode(lifecycleDetail)), lifecycleDetail)

        let windows = try await client.companyWindows()
        for opportunityID in Set(windows.flatMap(\.opportunities).map(\.opportunityId)) where !details.contains(where: { $0.opportunityId == opportunityID }) {
            details.append(try await client.opportunity(id: opportunityID))
        }
        let materialStage = try XCTUnwrap(details.flatMap(\.samples).first(where: { $0.comparison.classification?.kind == "material_stage" })?.comparison.classification)
        XCTAssertFalse(materialStage.newFacts.isEmpty)
        XCTAssertFalse(try XCTUnwrap(materialStage.changedJudgment).isEmpty)
        XCTAssertFalse(materialStage.twoDayReason.isEmpty)
        XCTAssertFalse(try XCTUnwrap(materialStage.relatedOpportunityId).isEmpty)
        let selections = try await client.selections()
        let analyzedSelection = try XCTUnwrap(selections.first(where: { detail in
            detail.state == "kept" && detail.analyses.contains { $0.role == "pro" && $0.fullText != nil }
        }))
        let windowID = try XCTUnwrap(windows.first(where: { $0.companyWindowId == analyzedSelection.companyWindowId })?.companyWindowId)
        let chain = try await client.analysisChain(companyWindowID: windowID)
        XCTAssertGreaterThanOrEqual(chain.items.count, 2)
        for item in chain.items {
            XCTAssertNotNil(item.analyses.first(where: { $0.role == "pro" })?.fullText, "分析第 \(item.revision) 版必须完整保留正方")
            XCTAssertNotNil(item.analyses.first(where: { $0.role == "con" })?.fullText, "分析第 \(item.revision) 版必须完整保留反方")
            XCTAssertEqual(item.job?.status, "completed", "分析第 \(item.revision) 版任务应已完成")
            XCTAssertEqual(item.job?.attemptCount, 1, "分析第 \(item.revision) 版不应遗留重试次数")
        }
        let historical = try XCTUnwrap(details.flatMap(\.samples).first(where: { $0.comparison.historicalCoverage != nil })?.comparison.historicalCoverage)
        XCTAssertFalse(historical.requestedOutcomes.isEmpty)

        let results = try await client.results()
        XCTAssertGreaterThan(results.overlap.observedCompleteCount, 0)
        XCTAssertEqual(results.overlap.eligibleCount, 0)
        let primaryMetrics = try XCTUnwrap(results.primary["all"])
        XCTAssertGreaterThan(primaryMetrics.pendingCount, 0)
        XCTAssertGreaterThan(primaryMetrics.suspendedCount, 0)
        XCTAssertGreaterThan(primaryMetrics.dataGapCount, 0)
        XCTAssertGreaterThan(primaryMetrics.anomalyCount, 0)
        XCTAssertLessThanOrEqual(primaryMetrics.dataGapCount, primaryMetrics.incompleteCount)
        let cohort = try XCTUnwrap(results.cohorts?.first)
        let sourceBatches = try XCTUnwrap(cohort.batchIds)
        XCTAssertFalse(sourceBatches.isEmpty)
        XCTAssertTrue(sourceBatches.contains(cohort.batchId))
        XCTAssertNotNil(cohort.primary["all"])
        XCTAssertNotNil(results.eventGroups?.first?.primary["all"])
        let day = try XCTUnwrap(results.records.flatMap { [$0.d1, $0.d2] }.compactMap { $0 }.first(where: { !($0.fieldChecks ?? []).isEmpty }))
        XCTAssertFalse(day.fieldChecks?.isEmpty ?? true)
    }

    func testBuild35RealProducerPreservesCoverageConfigurationOverlapAndWithdrawnEvidence() async throws {
        guard let raw = ProcessInfo.processInfo.environment["NK_V304_API_URL"], let baseURL = URL(string: raw) else {
            throw XCTSkip("set NK_V304_API_URL to run Build 35 real producer acceptance")
        }
        let client = K10APIClient(baseURL: baseURL, token: "temporary-test-token")
        let scan = try await client.latestScan(window: "evening")
        XCTAssertEqual(scan.coverageStatus, "partial")
        let source = try XCTUnwrap(scan.sourceCoverage.first)
        XCTAssertEqual(source.timeCoverage, "partial")
        XCTAssertEqual(source.unknownPublicationTimeCount, 1)
        let uncertain = try XCTUnwrap(source.uncertainTimeDocumentRefs?.first)
        XCTAssertEqual(uncertain.publishedPrecision, "unknown")
        XCTAssertNotNil(uncertain.documentId)
        let replay = try XCTUnwrap(scan.sourceReplay)
        XCTAssertEqual(replay.replaySeconds, 86400)
        XCTAssertNotNil(replay.nominalStartAt)
        XCTAssertNotNil(replay.effectiveStartAt)
        XCTAssertNotNil(replay.replayStartAt)
        XCTAssertNotNil(replay.cutoffAt)

        let results = try await client.results()
        XCTAssertEqual(results.configurationState, "not_configured")
        XCTAssertEqual(results.primary["all"]?.notConfiguredCount, 1)
        let unconfigured = try XCTUnwrap(results.records.first(where: { $0.companyCode == "300006.SZ" }))
        XCTAssertEqual(unconfigured.evaluationConfigurationState, "not_configured")
        XCTAssertFalse(unconfigured.primaryEligible)
        XCTAssertNil(unconfigured.closeLimitHitAny, "完整行情不能绕过冻结评价配置门禁")
        XCTAssertTrue(unconfigured.evaluationConfigurationMissing?.contains("evaluationPolicy") ?? false)
        let missingLimit = try XCTUnwrap(results.records.first(where: { $0.companyCode == "300003.SZ" }))
        XCTAssertTrue(missingLimit.gaps.contains("limit_data_unavailable"))
        XCTAssertEqual(results.primary["all"]?.dataGapCount, 2)
        let overlapEvent = try XCTUnwrap(results.eventGroups?.first(where: { ($0.overlap?.hitCount ?? 0) > 0 }))
        XCTAssertEqual(overlapEvent.overlap?.hitCount, 1)
        XCTAssertEqual(overlapEvent.overlap?.eligibleCount, 0)
        XCTAssertNil(overlapEvent.overlap?.hitRate)

        let fetchedReport = try await client.latestMorningReport()
        let report = try XCTUnwrap(fetchedReport)
        let fallback = try XCTUnwrap(report.items.first(where: { $0.section == "continuing_or_expiring" }))
        XCTAssertEqual(fallback.coverageStatus, "complete")
        let withdrawal = try XCTUnwrap(report.items.first(where: { $0.section == "major_contrary" }))
        XCTAssertTrue(withdrawal.independentVerificationRefs.contains { $0.documentId == "doc-v304-independent" })
        let detail = try await client.opportunity(id: XCTUnwrap(withdrawal.opportunityId))
        let lifecycle = try XCTUnwrap(detail.lifecycleEvents.first(where: { $0.kind == "withdrawal" }))
        XCTAssertTrue(lifecycle.sourceRefs.contains { $0.documentId == "doc-v304-independent" && $0.title != nil })
        XCTAssertEqual(lifecycle.independentVerificationRefs?.map(\.documentId), ["doc-v304-independent"])
        let peers = detail.samples.filter { $0.category == "tied" }
        XCTAssertEqual(peers.count, 2)
        XCTAssertEqual(Set(peers.compactMap { $0.comparison.eventRank }), [1])
        XCTAssertEqual(Set(peers.compactMap(\.rank)), [1, 2])
        XCTAssertTrue(peers.allSatisfy { $0.comparison.rankNamespace == "event" })
        XCTAssertEqual(try JSONDecoder().decode(K10OpportunityDetail.self, from: JSONEncoder().encode(detail)), detail)
    }

    private func containsNestedValue(_ content: [String: K10Value]) -> Bool {
        func nested(_ value: K10Value) -> Bool {
            switch value {
            case .array: return true
            case .object(let object): return !object.isEmpty || object.values.contains(where: nested)
            case .string, .number, .bool, .null: return false
            }
        }
        return content.values.contains(where: nested)
    }

    func testMarketSnapshotCollectedAtDecodesWithoutInventingFetchedAt() throws {
        let json = #"{"documentId":null,"factId":"market-fact","companyCode":"300001.SZ","tradeDate":"2026-09-07","revision":1,"sourceKey":"market_snapshot","title":"合成行情快照","url":null,"excerpt":null,"publishedAt":null,"publishedPrecision":"unknown","fetchedAt":null,"collectedAt":"2026-09-07T15:05:00+08:00"}"#
        let source = try JSONDecoder().decode(K10SourceReference.self, from: Data(json.utf8))
        XCTAssertEqual(source.collectedAt, "2026-09-07T15:05:00+08:00")
        XCTAssertNil(source.fetchedAt)
    }

    func testPushRouteUsesV14WindowAndOpportunityIdentifiers() {
        let focus = K10PushRoute(userInfo: ["companyWindowId": "window-1"])
        XCTAssertEqual(focus?.tab, .focus)
        XCTAssertEqual(focus?.companyWindowID, "window-1")
        let opportunity = K10PushRoute(userInfo: ["opportunityId": "opportunity-1", "batchId": "batch-1"])
        XCTAssertEqual(opportunity?.tab, .opportunities)
        XCTAssertEqual(opportunity?.opportunityID, "opportunity-1")
        XCTAssertNil(K10PushRoute(userInfo: ["companyCandidateId": "retired-k9-id"]))
    }

    func testFastAPIErrorEnvelopePreservesActionableDetails() async {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [FailureEnvelopeProtocol.self]
        let session = URLSession(configuration: configuration)
        let conflict = K10APIClient(baseURL: URL(string: "https://conflict.example")!, token: "synthetic", session: session)
        do {
            _ = try await conflict.results()
            XCTFail("409 must be surfaced")
        } catch let error as K10APIError {
            XCTAssertEqual(error, .conflict("公司窗口已到期，不能修改选择"))
            XCTAssertEqual(error.localizedDescription, "公司窗口已到期，不能修改选择")
        } catch { XCTFail("unexpected error: \(error)") }

        let unavailable = K10APIClient(baseURL: URL(string: "https://configuration.example")!, token: "synthetic", session: session)
        do {
            _ = try await unavailable.configuration()
            XCTFail("503 must be surfaced")
        } catch let error as K10APIError {
            XCTAssertEqual(error, .notConfigured("两日评价参数未配置", ["evaluationPolicy", "marketCollection"]))
            XCTAssertEqual(error.localizedDescription, "两日评价参数未配置：evaluationPolicy、marketCollection")
        } catch { XCTFail("unexpected error: \(error)") }
    }

    func testCancelledTransportIsNotMappedToNetworkUnavailable() async {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [CancelledRequestProtocol.self]
        let client = K10APIClient(baseURL: URL(string: "https://cancelled.example")!, token: "synthetic", session: URLSession(configuration: configuration))

        do {
            _ = try await client.results()
            XCTFail("cancelled transport must propagate cancellation")
        } catch is CancellationError {
            // A task being dismissed is not a connection outage.
        } catch {
            XCTFail("unexpected cancellation mapping: \(error)")
        }
    }

    func testReasonTextKeepsRecordedNaturalLanguageAndMakesUnknownCodeAuditable() {
        XCTAssertEqual(k10ReasonText("公开资料缺少可追溯平淡与失败分类"), "公开资料缺少可追溯平淡与失败分类")
        XCTAssertEqual(k10ReasonText("unmapped_worker_reason"), "已记录原因：unmapped_worker_reason（请结合来源核对）")
        XCTAssertEqual(k10ReasonText(nil), "未记录具体原因，查看来源核对")
    }

    @MainActor func testHealthyEmptyFirstRunIsReady() async {
        let model = AppModel(serviceFactory: { ControlledK10Service(batchID: "empty", empty: true) })
        await model.refresh()
        XCTAssertEqual(model.state, .ready)
        XCTAssertTrue(model.scanSummaries.isEmpty)
        XCTAssertTrue(model.publications.isEmpty)
        XCTAssertTrue(model.companyWindows.isEmpty)
    }

    @MainActor func testConnectionGenerationIgnoresStaleSuccessAndItsCacheWrite() async {
        let gate = RefreshGate()
        let old = ControlledK10Service(batchID: "batch-old", publicationGate: gate)
        let current = ControlledK10Service(batchID: "batch-current")
        let services = ServiceBox(old)
        let caches = CacheRecorder()
        let contexts = CacheContextBox(K10CacheContext(baseURL: URL(string: "https://a.example")!, scope: "test-a"))
        let model = AppModel(
            serviceFactory: { services.service },
            cacheContextFactory: { contexts.context },
            cacheLoader: { caches.load($0) },
            cacheSaver: { caches.save($0, for: $1) },
            cacheClearer: { caches.clear() }
        )

        let oldRefresh = Task { await model.refresh() }
        await gate.waitUntilEntered()
        model.resetForConnectionChange()
        services.service = current
        contexts.context = K10CacheContext(baseURL: URL(string: "https://b.example")!, scope: "test-b")
        await model.refresh()
        await gate.open()
        await oldRefresh.value

        XCTAssertEqual(model.state, .ready)
        XCTAssertEqual(model.publications.map(\.batchId), ["batch-current"])
        XCTAssertEqual(caches.savedContexts, [K10CacheContext(baseURL: URL(string: "https://b.example")!, scope: "test-b")])
        XCTAssertEqual(caches.snapshots[K10CacheContext(baseURL: URL(string: "https://b.example")!, scope: "test-b")]?.publications.map(\.batchId), ["batch-current"])
    }

    @MainActor func testConnectionGenerationIgnoresStaleFailure() async {
        let gate = RefreshGate()
        let old = ControlledK10Service(batchID: "batch-old", healthGate: gate, healthFailure: .networkUnavailable("旧连接超时"))
        let current = ControlledK10Service(batchID: "batch-current")
        let services = ServiceBox(old)
        let model = AppModel(serviceFactory: { services.service }, cacheClearer: {})

        let oldRefresh = Task { await model.refresh() }
        await gate.waitUntilEntered()
        model.resetForConnectionChange()
        services.service = current
        await model.refresh()
        await gate.open()
        await oldRefresh.value

        XCTAssertEqual(model.state, .ready)
        XCTAssertEqual(model.publications.map(\.batchId), ["batch-current"])
        XCTAssertFalse(model.offline)
    }

    @MainActor func testSameConnectionNewRefreshWinsOverEarlierFailure() async {
        let gate = RefreshGate()
        let service = ControlledK10Service(batchID: "same-connection", publicationGate: gate, firstPublicationFailure: .networkUnavailable("旧刷新超时"))
        let model = AppModel(serviceFactory: { service }, cacheClearer: {})

        let firstRefresh = Task { await model.refresh() }
        await gate.waitUntilEntered()
        await model.refresh()
        await gate.open()
        await firstRefresh.value

        XCTAssertEqual(model.state, .ready)
        XCTAssertFalse(model.offline)
        XCTAssertEqual(model.publications.map(\.batchId), ["same-connection"])
    }

    @MainActor func testAdminSettingsCannotCrossConnectionGenerations() async {
        let suite = "top.linotsai.neckline.tests.admin-generation"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        defer { defaults.removePersistentDomain(forName: suite) }
        let config = AppConfig(defaults: defaults, tokenStore: RecordingTokenStore())
        config.apiToken = "synthetic-token"
        config.baseURLOverride = "https://a.example"
        let gate = RefreshGate()
        let old = ControlledAdminService(providerName: "old-provider", tavilyKeySet: true, providersGate: gate)
        let current = ControlledAdminService(providerName: "current-provider", tavilyKeySet: false)
        let admins = AdminServiceBox(old)
        let model = AppModel(serviceFactory: { nil }, cacheClearer: {}, adminServiceFactory: { _, _ in admins.service })
        model.bind(config: config)

        let oldRefresh = Task { await model.refreshAdminSettings() }
        await gate.waitUntilEntered()
        model.resetForConnectionChange()
        config.baseURLOverride = "https://b.example"
        admins.service = current
        model.bind(config: config)
        await model.refreshAdminSettings()
        await gate.open()
        await oldRefresh.value

        XCTAssertEqual(model.providers.map(\.name), ["current-provider"])
        XCTAssertFalse(model.tavilyKeySet)
        XCTAssertEqual(model.toast, nil)
    }

    @MainActor func testStaleAdminSaveDoesNotRefreshTheNewConnection() async {
        let suite = "top.linotsai.neckline.tests.admin-save-generation"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        defer { defaults.removePersistentDomain(forName: suite) }
        let config = AppConfig(defaults: defaults, tokenStore: RecordingTokenStore())
        config.apiToken = "synthetic-token"
        config.baseURLOverride = "https://a.example"
        let gate = RefreshGate()
        let old = ControlledAdminService(providerName: "deepseek", tavilyKeySet: true, updateGate: gate)
        let current = ControlledAdminService(providerName: "current-provider", tavilyKeySet: false)
        let admins = AdminServiceBox(old)
        let model = AppModel(serviceFactory: { nil }, cacheClearer: {}, adminServiceFactory: { _, _ in admins.service })
        model.bind(config: config)
        model.providers = [old.provider]

        let saving = Task { await model.saveModelConnection(name: "deepseek", baseURL: "https://api.deepseek.com/v1/chat/completions", modelName: "deepseek-v4-pro", apiKey: "new-key", enabled: true, creating: false) }
        await gate.waitUntilEntered()
        model.resetForConnectionChange()
        config.baseURLOverride = "https://b.example"
        admins.service = current
        model.bind(config: config)
        await gate.open()
        await saving.value

        XCTAssertTrue(model.providers.isEmpty)
        XCTAssertFalse(model.tavilyKeySet)
        XCTAssertEqual(model.toast, nil)
        let currentReads = await current.providerReadCount()
        XCTAssertEqual(currentReads, 0)
    }

    @MainActor func testNotificationRoutingOpensExactV14ObjectAndNeverFallsBack() async {
        let service = ControlledK10Service(batchID: "batch-current")
        let model = AppModel(serviceFactory: { service })

        await model.openNotification(try! XCTUnwrap(K10PushRoute(userInfo: ["companyWindowId": "synthetic-morning-late-window"])))
        XCTAssertEqual(model.tab, .focus)
        XCTAssertEqual(model.selectedWindow?.companyWindowId, "synthetic-morning-late-window")
        XCTAssertNil(model.selectedOpportunity)

        await model.openNotification(try! XCTUnwrap(K10PushRoute(userInfo: ["opportunityId": "synthetic-opportunity-1"])))
        XCTAssertEqual(model.tab, .opportunities)
        XCTAssertEqual(model.selectedOpportunity?.opportunityId, "synthetic-opportunity-1")
        XCTAssertNil(model.selectedWindow)

        await model.openNotification(try! XCTUnwrap(K10PushRoute(userInfo: ["companyWindowId": "withdrawn-window"])))
        XCTAssertEqual(model.tab, .focus)
        XCTAssertNil(model.selectedWindow)
        XCTAssertNil(model.selectedOpportunity)
        XCTAssertEqual(model.toast, "通知关联的公司窗口已不可用")
    }

    @MainActor func testNotificationRefreshFailureDoesNotOpenOldReadingContext() async {
        let model = AppModel(serviceFactory: { ControlledK10Service(batchID: "failed", healthFailure: .networkUnavailable("暂时不可达")) })
        let previousWindows = try! await K10SyntheticUIService().companyWindows()
        model.selectedWindow = previousWindows.first
        await model.openNotification(try! XCTUnwrap(K10PushRoute(userInfo: ["companyWindowId": "synthetic-evening-window"])))
        XCTAssertEqual(model.tab, .focus)
        XCTAssertNil(model.selectedWindow)
        XCTAssertNil(model.selectedOpportunity)
        XCTAssertEqual(model.state, .failed("网络不可用：暂时不可达"))
    }

    @MainActor func testNotificationDoesNotOpenOldDetailAfterConnectionChanges() async {
        let gate = RefreshGate()
        let old = ControlledK10Service(batchID: "batch-old", opportunityGate: gate)
        let current = ControlledK10Service(batchID: "batch-current")
        let services = ServiceBox(old)
        let model = AppModel(serviceFactory: { services.service }, cacheClearer: {})
        let route = try! XCTUnwrap(K10PushRoute(userInfo: ["opportunityId": "synthetic-opportunity-1"]))

        let opening = Task { await model.openNotification(route) }
        await gate.waitUntilEntered()
        model.resetForConnectionChange()
        services.service = current
        await model.refresh()
        await gate.open()
        await opening.value

        XCTAssertEqual(model.state, .ready)
        XCTAssertEqual(model.publications.map(\.batchId), ["batch-current"])
        XCTAssertNil(model.selectedOpportunity)
        XCTAssertNil(model.selectedWindow)
    }

    func testResultsSeparatePrimaryAndOverlap() async throws {
        let results = try await K10SyntheticUIService().results()
        XCTAssertEqual(results.primary["selected"]?.sampleCount, 1)
        XCTAssertEqual(results.primary["selected"]?.hitCount, 1)
        XCTAssertEqual(results.overlap.sampleCount, 1)
        XCTAssertEqual(results.records.last?.state, "incomplete")
        XCTAssertEqual(results.cohorts?.first?.primary["all"]?.sampleCount, 1)
        XCTAssertEqual(results.eventGroups?.first?.companySampleCount, 1)
        XCTAssertTrue(results.records[0].primaryEligible)
    }

    @MainActor func testDocumentPaginationPinsRevisionAndConcatenatesBody() async {
        let service = K10SyntheticUIService()
        let model = AppModel(serviceFactory: { service })
        let source = K10SourceReference(documentId: "synthetic-document", factId: nil, companyCode: nil, tradeDate: nil, revision: 1, sourceKey: "synthetic-news", title: nil, url: nil, excerpt: nil, publishedAt: nil, publishedPrecision: "unknown", fetchedAt: nil)
        guard let first = await model.openDocument(source) else { return XCTFail("first document page should load") }
        XCTAssertEqual(first.revision, 1)
        XCTAssertEqual(first.page.nextCursor, "6000")
        XCTAssertTrue(first.body?.contains("第一页") ?? false)
        guard let merged = await model.loadMoreDocument(first) else { return XCTFail("second document page should load") }
        XCTAssertEqual(merged.revision, 1)
        XCTAssertTrue(merged.body?.contains("第一页") ?? false)
        XCTAssertTrue(merged.body?.contains("第二页") ?? false)
        XCTAssertNil(merged.page.nextCursor)
    }

    func testPublicationsPaginationUsesCursorWithoutDroppingSecondPage() async throws {
        PagingProtocol.reset()
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [PagingProtocol.self]
        let client = K10APIClient(baseURL: URL(string: "https://paging.example")!, token: "synthetic", session: URLSession(configuration: configuration))
        let values = try await client.publications()
        XCTAssertEqual(values.map(\.batchId), ["batch-1", "batch-2"])
        XCTAssertEqual(PagingProtocol.paths().count, 2)
        XCTAssertEqual(URLComponents(url: PagingProtocol.paths()[1], resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "cursor" })?.value, "next / two")
    }

    func testSelectionTimingAndUnknownLimitStatusDecodeWithoutFalseNegative() throws {
        let json = #"{"schemaVersion":"k10-api-v2","companyWindowId":"window","representativeCandidateId":null,"state":"kept","lastActionAt":"2026-09-07T09:31:00+08:00","postFreeze":true,"observationId":null,"opportunities":[],"analyses":[],"analysisJobId":null,"latestJob":null}"#
        let detail = try JSONDecoder().decode(K10SelectionDetail.self, from: Data(json.utf8))
        XCTAssertEqual(detail.lastActionAt, "2026-09-07T09:31:00+08:00")
        XCTAssertEqual(detail.postFreeze, true)
        XCTAssertEqual(k10LimitStatusText(closeLimitUp: nil, touchedLimitUp: nil, firstTouchedAt: nil), "涨停状态待核")
        XCTAssertEqual(k10StatusText("due"), "已到期·待核")
        XCTAssertEqual(k10StatusText("anomaly"), "行情异常")
    }

    @MainActor func testDebugQACredentialSwitchUsesOnlyProcessToken() {
        let disableKey = "NK_DISABLE_PERSISTENT_CREDENTIALS"
        let tokenKey = "NK_API_TOKEN"
        let oldDisable = ProcessInfo.processInfo.environment[disableKey]
        let oldToken = ProcessInfo.processInfo.environment[tokenKey]
        defer {
            if let oldDisable { setenv(disableKey, oldDisable, 1) } else { unsetenv(disableKey) }
            if let oldToken { setenv(tokenKey, oldToken, 1) } else { unsetenv(tokenKey) }
        }
        setenv(disableKey, "1", 1)
        setenv(tokenKey, "qa-process-token", 1)
        let suite = "top.linotsai.neckline.tests.qa-credentials"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        let store = RecordingTokenStore()
        let config = AppConfig(defaults: defaults, tokenStore: store)
        XCTAssertEqual(config.apiToken, "qa-process-token")
        XCTAssertEqual(store.loadCount, 0)
        config.apiToken = "updated-in-memory-only"
        XCTAssertEqual(store.saveCount, 0)
    }

    func testTemporaryBackendAPISmokeDecodesCompanyWindowAndIncompleteResult() async throws {
        guard let raw = ProcessInfo.processInfo.environment["NK_TEMPORARY_API_SMOKE_URL"], let baseURL = URL(string: raw) else {
            throw XCTSkip("temporary K10 API smoke server was not requested")
        }
        let client = K10APIClient(baseURL: baseURL, token: "temporary-test-token")
        let progressScan = try await client.latestScan(window: "evening")
        XCTAssertEqual(progressScan.executionProgress?.state, "partial")
        XCTAssertEqual(progressScan.executionProgress?.documentCounts.failedPending, 1)
        XCTAssertNil(progressScan.executionProgress?.eventCounts.publishable, "统一排序前不能把可发布数伪装成零")
        XCTAssertEqual(progressScan.executionProgress?.safeFailures.first?.code, "model_output_invalid")
        let legacyScan = try await client.latestScan(window: "morning")
        XCTAssertNil(legacyScan.executionProgress, "旧扫描没有 B36 检查点时必须保持未记录")
        let readiness = try await client.operationsReadiness()
        XCTAssertTrue(["ready", "blocked", "notConfigured"].contains(readiness.notificationReadiness.state))
        XCTAssertFalse(readiness.notificationReadiness.reasonCode?.contains("/") ?? false)
        let windows = try await client.companyWindows()
        XCTAssertGreaterThanOrEqual(windows.count, 2)
        let primaryWindow = try XCTUnwrap(windows.first(where: { $0.companyCode == "300001.SZ" }))
        XCTAssertEqual(primaryWindow.sampleClass, "primary")
        let opportunity = try XCTUnwrap(primaryWindow.opportunities.first)
        XCTAssertEqual(opportunity.sourceMarker, "evening")
        XCTAssertEqual(opportunity.latePublication, false)
        let detail = try await client.opportunity(id: opportunity.opportunityId)
        XCTAssertFalse(detail.eventHeadline?.isEmpty ?? true)
        XCTAssertFalse(detail.commonFacts.isEmpty)
        let rankedSample = try XCTUnwrap(detail.samples.first(where: { $0.comparison.rank != nil }))
        XCTAssertNotNil(rankedSample.comparison.priorityReason)
        XCTAssertNotNil(rankedSample.comparison.gap)
        XCTAssertNotNil(rankedSample.comparison.rankChangeConditions)
        XCTAssertNotNil(rankedSample.comparison.twoDayReason)
        let result = try await client.results()
        XCTAssertGreaterThanOrEqual(result.records.count, 2)
        let incomplete = try XCTUnwrap(result.records.first(where: { $0.state == "incomplete" }))
        XCTAssertEqual(incomplete.state, "incomplete")
        XCTAssertTrue(incomplete.firstTouchStatus?.hasPrefix("unknown") ?? false)
        XCTAssertEqual(incomplete.comparability, "unknown")
        XCTAssertFalse(incomplete.primaryEligible)
        XCTAssertGreaterThanOrEqual(result.primary["all"]?.sampleCount ?? 0, 1)
        XCTAssertGreaterThanOrEqual(result.primary["all"]?.dataGapCount ?? -1, 0)
        XCTAssertGreaterThanOrEqual(result.cohorts?.first?.companySampleCount ?? 0, 1)
        XCTAssertGreaterThanOrEqual(result.eventGroups?.first?.companySampleCount ?? 0, 1)
    }

    func testV306IsolatedFastAPIProgressDecodesTitleFirstProjection() async throws {
        guard let raw = ProcessInfo.processInfo.environment["NK_V306_API_URL"],
              let baseURL = URL(string: raw) else {
            throw XCTSkip("set NK_V306_API_URL to run the isolated B38 FastAPI-to-Swift DTO check")
        }
        let client = K10APIClient(baseURL: baseURL, token: "temporary-test-token")
        let scan = try await client.latestScan(window: "evening")
        let progress = try XCTUnwrap(scan.executionProgress)
        XCTAssertEqual(progress.state, "partial")
        XCTAssertEqual(progress.titleCounts?.received, 2)
        XCTAssertEqual(progress.titleCounts?.exactDeduplicated, 0)
        XCTAssertEqual(progress.titleCounts?.triaged, 2)
        XCTAssertEqual(progress.titleCounts?.merged, 1)
        XCTAssertEqual(progress.articleCounts?.limit, 80)
        XCTAssertEqual(progress.articleCounts?.selected, 1)
        XCTAssertEqual(progress.articleCounts?.missingBody, 1)
        XCTAssertEqual(progress.articleCounts?.tavilyExcerpt, 2)
        XCTAssertEqual(progress.articleCounts?.tavilyFullArticle, 1)
        XCTAssertEqual(progress.attemptCounts?.succeeded, 1)
        XCTAssertEqual(progress.factCacheHits, 2)
    }

    func testV310IsolatedFastAPIResearchSummaryAndAssessmentsDecodeDisclosure() async throws {
        guard let raw = ProcessInfo.processInfo.environment["NK_V310_API_URL"],
              let baseURL = URL(string: raw) else {
            throw XCTSkip("set NK_V310_API_URL to run the isolated B39 FastAPI-to-Swift DTO check")
        }
        let client = K10APIClient(baseURL: baseURL, token: "temporary-test-token")
        let scan = try await client.latestScan(window: "evening")
        let embedded = try XCTUnwrap(scan.researchSummary)
        XCTAssertTrue(embedded.executionFailed)
        XCTAssertTrue(embedded.comparisonComplete, "研究完成与执行失败是独立状态，界面仍须优先显示比较未完成")
        XCTAssertEqual(embedded.safeFailureCounts["comparison_interrupted"], 1)

        let summary = try await client.researchSummary(scanID: scan.scanId)
        XCTAssertEqual(summary, embedded)
        let assessments = try await client.researchAssessments(scanID: scan.scanId)
        XCTAssertEqual(Set(assessments.items.map(\.role)), ["primary", "pending", "excluded"])
        let rumor = try XCTUnwrap(assessments.items.first(where: { $0.role == "primary" })?.evidenceDisclosure)
        XCTAssertEqual(rumor.verificationStatus, "unverified")
        XCTAssertTrue(rumor.isRumor)
        XCTAssertEqual(rumor.originStatus, "unknown")
        XCTAssertEqual(assessments.items.first(where: { $0.role == "primary" })?.safeErrorCode, "comparison_interrupted")
    }

    func testExecutionProgressAndNotificationReadinessDecodeOnlySafeFields() throws {
        let scanData = Data("""
        {
          "schemaVersion":"k10-api-v2","scanId":"scan-safe","window":"evening",
          "cutoffAt":"2026-09-08T21:00:00+08:00","status":"partial","coverageStatus":"partial",
          "coverageGaps":["source_coverage_incomplete"],"sourceCoverage":[],"publicationStatus":"not_published",
          "publicationBatchId":null,"availableAt":null,"configId":"k10-v1.4-production","configRevision":2,
          "createdAt":"2026-09-08T21:00:00+08:00","completedAt":null,
          "executionProgress":{"state":"partial","stage":"title_triage","coverageStatus":"partial",
            "documentCounts":{"received":30,"deduplicated":1,"templateSkipped":0,"understood":29,"fullText":0,"failedPending":1},
            "eventCounts":{"verified":0,"compared":0,"publishable":0},"nextRetryAt":"2026-09-08T22:00:00+08:00",
            "safeFailures":[{"stage":"understanding","code":"model_output_invalid","ref":"doc-safe@1"}],
            "strategyBinding":{"configId":"k10-v1.4-production","revision":2},
            "executionBinding":{"configId":"k10-execution","revision":1},
            "runControl":{"state":"paused","reasonCode":"user_paused","changedAt":"2026-09-08T21:01:00+08:00"},
            "titleCounts":{"received":30,"exactDeduplicated":1,"triaged":29,"merged":2,"notSelected":19,"protected":2,"partial":0},
            "articleCounts":{"limit":80,"selected":8,"admitted":8,"completed":7,"missingBody":1,"tavilyExcerpt":2,"tavilyFullArticle":0},
            "attemptCounts":{"started":0,"succeeded":8,"failed":1,"unknown":1},"factCacheHits":6}
        }
        """.utf8)
        let scan = try JSONDecoder().decode(K10Scan.self, from: scanData)
        XCTAssertEqual(scan.executionProgress?.documentCounts.received, 30)
        XCTAssertEqual(scan.executionProgress?.safeFailures.first?.code, "model_output_invalid")
        XCTAssertEqual(scan.executionProgress?.safeFailures.first?.ref, "doc-safe@1")
        XCTAssertEqual(scan.executionProgress?.runControl?.state, "paused")
        XCTAssertEqual(scan.executionProgress?.titleCounts?.merged, 2, "保持服务响应的 merged=2，不在客户端重新统计")
        XCTAssertEqual(scan.executionProgress?.articleCounts?.limit, 80)
        XCTAssertEqual(scan.executionProgress?.articleCounts?.missingBody, 1)
        XCTAssertEqual(scan.executionProgress?.attemptCounts?.unknown, 1)
        XCTAssertEqual(scan.executionProgress?.factCacheHits, 6)

        let readinessData = Data("""
        {"schemaVersion":"k10-api-v2","notificationReadiness":{"state":"blocked","reasonCode":"credentials_missing","nextRetryAt":null,"checkedAt":"2026-09-08T21:01:00+08:00"},"runControl":{"state":"paused","reasonCode":"user_paused","changedAt":"2026-09-08T21:01:00+08:00"}}
        """.utf8)
        let readiness = try JSONDecoder().decode(K10OperationsReadiness.self, from: readinessData)
        XCTAssertEqual(readiness.notificationReadiness.state, "blocked")
        XCTAssertEqual(readiness.notificationReadiness.reasonCode, "credentials_missing")
        XCTAssertEqual(readiness.runControl.reasonCode, "user_paused")
    }

    func testB39DisclosureDecodesUnverifiedRumorWithoutPromotingLegacyComparison() throws {
        let rumor = Data("""
        {"summary":"待核传闻","rationale":null,"rank":1,"priorityReason":"映射待核","gap":"缺独立来源","rankChangeConditions":"正式披露","twoDayReason":"仅观察","classification":null,"historicalCases":[],"historicalCoverage":null,"eventRank":1,"rankNamespace":"event","evidenceDisclosure":{"verificationStatus":"unverified","isRumor":true,"originStatus":"unknown","originEvidenceRef":null,"unverifiedReasons":["尚无独立来源核验"],"conditionalAnalysis":"仅在正式披露确认时重新评估。"}}
        """.utf8)
        let decoded = try JSONDecoder().decode(K10Comparison.self, from: rumor)
        XCTAssertEqual(decoded.evidenceDisclosure?.verificationStatus, "unverified")
        XCTAssertEqual(decoded.evidenceDisclosure?.isRumor, true)
        XCTAssertEqual(decoded.evidenceDisclosure?.unverifiedReasons, ["尚无独立来源核验"])

        let legacy = Data("""
        {"summary":"历史比较","rationale":null,"rank":1,"priorityReason":"旧理由","gap":null,"rankChangeConditions":null,"twoDayReason":null,"classification":null,"historicalCases":[],"historicalCoverage":null,"eventRank":null,"rankNamespace":null}
        """.utf8)
        XCTAssertNil(try JSONDecoder().decode(K10Comparison.self, from: legacy).evidenceDisclosure)
    }
}

private final class RecordingTokenStore: APIAccessTokenStore {
    private(set) var loadCount = 0
    private(set) var saveCount = 0
    func load() -> String? { loadCount += 1; return "persistent-token" }
    func save(_ token: String) -> Bool { saveCount += 1; return true }
}

private final class PagingProtocol: URLProtocol {
    private static let lock = NSLock(); private static var urls: [URL] = []
    static func reset() { lock.lock(); urls = []; lock.unlock() }
    static func paths() -> [URL] { lock.lock(); defer { lock.unlock() }; return urls }
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "paging.example" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard let url = request.url, let client else { return }
        Self.lock.lock(); Self.urls.append(url); Self.lock.unlock()
        let cursor = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "cursor" })?.value
        let body: String
        if cursor == nil { body = #"{"items":[{"schemaVersion":"k10-api-v2","batchId":"batch-1","scanId":"scan-1","publicationKind":"evening","availableAt":"2026-09-06T21:00:00+08:00","createdAt":"2026-09-06T21:00:00+08:00","sampleCount":1}],"page":{"nextCursor":"next / two"}}"# }
        else { body = #"{"items":[{"schemaVersion":"k10-api-v2","batchId":"batch-2","scanId":"scan-2","publicationKind":"morning","availableAt":"2026-09-07T08:00:00+08:00","createdAt":"2026-09-07T08:00:00+08:00","sampleCount":2}],"page":{"nextCursor":null}}"# }
        client.urlProtocol(self, didReceive: HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
        client.urlProtocol(self, didLoad: Data(body.utf8)); client.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

private final class FailureEnvelopeProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool {
        ["conflict.example", "configuration.example"].contains(request.url?.host)
    }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard let url = request.url, let client else { return }
        let status: Int
        let body: String
        if url.host == "conflict.example" {
            status = 409
            body = #"{"detail":{"reason":"window_expired","message":"公司窗口已到期，不能修改选择","missing":[]}}"#
        } else {
            status = 503
            body = #"{"detail":{"reason":"not_configured","message":"两日评价参数未配置","missing":["evaluationPolicy","marketCollection"]}}"#
        }
        client.urlProtocol(self, didReceive: HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
        client.urlProtocol(self, didLoad: Data(body.utf8))
        client.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

private final class CancelledRequestProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "cancelled.example" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() { client?.urlProtocol(self, didFailWithError: URLError(.cancelled)) }
    override func stopLoading() {}
}

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
    private var dailySelections: [String: String] = [:]
    private var dailyPageFailure: K10APIError?
    private var dailyActionResponse: K10SelectionAction?

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
        if window == "morning", let dailyMorningOverride { return try applyingDailySelections(dailyMorningOverride) }
        return try await fixture.latestDailyReport(window: window)
    }
    func dailyReport(id: String, cursor: String) async throws -> K10DailyReportResponse {
        if let dailyPageFailure { throw dailyPageFailure }
        guard let nextDailyPage else { throw K10APIError.notFound("没有下一页") }
        return try applyingDailySelections(nextDailyPage)
    }
    private func applyingDailySelections(_ input: K10DailyReportResponse) throws -> K10DailyReportResponse {
        var response = input
        response.report?.addedCards = try (input.report?.addedCards ?? []).map { card in
            guard let state = dailySelections[card.companyWindowId] else { return card }
            var row = try JSONSerialization.jsonObject(with: JSONEncoder().encode(card)) as! [String: Any]
            row["currentSelectionState"] = state
            return try JSONDecoder().decode(K10DailyCard.self, from: JSONSerialization.data(withJSONObject: row))
        }
        return response
    }
    func setDailyActionResponse(_ response: K10SelectionAction) { dailyActionResponse = response }
    func setDailyPageFailure(_ error: K10APIError?) { dailyPageFailure = error }
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
        dailySelections[companyWindowID] = ["keep": "kept", "skip": "skipped", "restore": "unhandled"][request.action]
        if let dailyActionResponse { return dailyActionResponse }
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

private actor ControlledAdminService: K10AdminServicing {
    let provider: K10Provider
    private let tavilyValue: Bool
    private let providersGate: RefreshGate?
    private let updateGate: RefreshGate?
    private var providerReads = 0

    init(providerName: String, tavilyKeySet: Bool, providersGate: RefreshGate? = nil, updateGate: RefreshGate? = nil) {
        self.provider = K10Provider(name: providerName, baseUrl: "https://api.deepseek.com/v1/chat/completions", model: "deepseek-v4-pro", hasWebSearch: false, searchEngine: nil, notes: "test", enabled: true, keySet: true)
        self.tavilyValue = tavilyKeySet
        self.providersGate = providersGate
        self.updateGate = updateGate
    }

    func providers() async throws -> [K10Provider] {
        if let providersGate { await providersGate.wait() }
        providerReads += 1
        return [provider]
    }
    func createProvider(_ provider: K10ProviderCreate) async throws -> K10Provider {
        if let updateGate { await updateGate.wait() }
        return self.provider
    }
    func updateProvider(name: String, _ provider: K10ProviderUpdate) async throws -> K10Provider {
        if let updateGate { await updateGate.wait() }
        return self.provider
    }
    func tavilyStatus() async throws -> K10TavilyStatus { K10TavilyStatus(keySet: tavilyValue) }
    func setTavilyKey(_ key: String) async throws -> K10TavilyStatus {
        if let updateGate { await updateGate.wait() }
        return K10TavilyStatus(keySet: true)
    }
    func clearTavilyKey() async throws { if let updateGate { await updateGate.wait() } }
    func deleteProvider(name: String) async throws {}
    func registerDevice(token: String) async throws {}
    func providerReadCount() -> Int { providerReads }
}

@MainActor private final class ServiceBox {
    var service: any K10Servicing
    init(_ service: any K10Servicing) { self.service = service }
}

@MainActor private final class CacheContextBox {
    var context: K10CacheContext
    init(_ context: K10CacheContext) { self.context = context }
}

@MainActor private final class AdminServiceBox {
    var service: any K10AdminServicing
    init(_ service: any K10AdminServicing) { self.service = service }
}

@MainActor private final class CacheRecorder {
    private(set) var snapshots: [K10CacheContext: K10CacheSnapshot] = [:]
    private(set) var savedContexts: [K10CacheContext] = []
    func load(_ context: K10CacheContext) -> K10CacheSnapshot? { snapshots[context] }
    func save(_ snapshot: K10CacheSnapshot, for context: K10CacheContext) {
        snapshots[context] = snapshot
        savedContexts.append(context)
    }
    func clear() { snapshots.removeAll(); savedContexts.removeAll() }
}

extension K10V3Tests {
    @MainActor
    func testV320ClosedHistoricalWindowCannotRestoreButActiveWindowCan() async throws {
        let service = ControlledK10Service(batchID: "closed-history")
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        var window = try XCTUnwrap(model.companyWindows.first)
        window.canSelect = false
        await model.act("keep", window: window)
        await model.act("restore", window: window)
        let blockedCalls = await service.dailyActionCallCount()
        XCTAssertEqual(blockedCalls, 0, "历史窗口不能绕过日报已关闭目标的限制")
        XCTAssertEqual(model.toast, "该机会已撤回或到期，原选择和两日成绩继续保留")
        window.canSelect = true
        await model.act("restore", window: window)
        let allowedCalls = await service.dailyActionCallCount()
        XCTAssertEqual(allowedCalls, 1, "同公司仍有效的独立窗口继续允许找回")
    }

    @MainActor
    func testV320ClosedDailyCardCannotQueueNewSelection() async throws {
        let service = ControlledK10Service(batchID: "closed-card")
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        var card = try XCTUnwrap(model.eveningCards.first)
        card.canSelect = false
        await model.act("keep", card: card)
        await model.act("restore", card: card)
        let calls = await service.dailyActionCallCount()
        XCTAssertEqual(calls, 0, "已终止的目标窗口不能从客户端重新启动分析")
        XCTAssertEqual(model.toast, "该机会已撤回或到期，原选择和两日成绩继续保留")
    }

    @MainActor
    func testV320LifecycleNoticeSurvivesWithoutAnyDailyCardAndDeduplicates() async throws {
        let service = K10SyntheticUIService()
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        let update = K10DailyLifecycleUpdate(updateId: "old-withdrawal", opportunityId: "old-opportunity",
            companyWindowId: "old-window", companyCode: "300001.SZ", companyName: "旧关注公司", kind: "withdrawal",
            reason: "公司明确否认，原催化理由失效。", createdAt: "2026-09-09T09:00:00+08:00", sourceRefs: [])
        model.dailyEvening?.report?.eveningCards = []
        model.dailyEvening?.report?.lifecycleUpdates = [update]
        model.dailyMorning?.report?.updatedCards = []
        model.dailyMorning?.report?.addedCards = []
        model.dailyMorning?.report?.lifecycleUpdates = [update]
        XCTAssertTrue(model.eveningCards.isEmpty)
        XCTAssertEqual(model.dailyLifecycleUpdates, [update], "旧关注变化不能依赖今天有推荐卡，也不能因晚晨两份投影而重复")
    }

    @MainActor
    func testB60HomeMovesExpiryToHistoryButKeepsWithdrawalWithoutNewCards() async throws {
        let service = K10SyntheticUIService()
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        func update(_ kind: String) -> K10DailyLifecycleUpdate {
            K10DailyLifecycleUpdate(updateId: kind, opportunityId: "old-opportunity",
                companyWindowId: "old-window", companyCode: "300001.SZ", companyName: "历史公司",
                kind: kind, reason: "原始变化依据", createdAt: "2026-09-09T09:00:00+08:00", sourceRefs: [])
        }
        model.dailyEvening?.report?.eveningCards = []
        model.dailyMorning?.report?.addedCards = []
        model.dailyMorning?.report?.updatedCards = []
        model.dailyEvening?.report?.lifecycleUpdates = [update("expiry"), update("withdrawal"), update("risk")]
        model.dailyMorning?.report?.lifecycleUpdates = [update("expiry")]
        XCTAssertEqual(model.historicalLifecycleUpdates.map(\.kind), ["expiry"])
        XCTAssertEqual(model.currentLifecycleUpdates.map(\.kind), ["withdrawal", "risk"])
        XCTAssertEqual(model.dailyLifecycleUpdates.count, 3, "历史仍保留且跨报告去重")
        XCTAssertTrue(model.currentEveningCards.isEmpty)
        let window = try XCTUnwrap(model.companyWindows.first)
        var archived = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(window)) as? [String: Any])
        archived["companyWindowId"] = "old-window"
        archived["opportunities"] = (archived["opportunities"] as! [[String: Any]]).map { item in
            var expired = item; expired["lifecycle"] = "expired"; return expired
        }
        model.companyWindows = [try JSONDecoder().decode(K10CompanyWindow.self, from: JSONSerialization.data(withJSONObject: archived))]
        XCTAssertTrue(model.currentLifecycleUpdates.isEmpty, "已到期窗口的旧风险也不能持续占据首页")
        XCTAssertEqual(model.historicalLifecycleUpdates.count, 3)
    }

    @MainActor
    func testB62ActualWithdrawnWindowNoticesArchiveAtD2() throws {
        guard let root = ProcessInfo.processInfo.environment["NK_V320_DTO_DIR"] else {
            throw XCTSkip("Set NK_V320_DTO_DIR to actual withdrawal-history API responses")
        }
        for (label, instant) in [("before", "2026-09-10T14:59:59+08:00"),
                                 ("at", "2026-09-10T15:00:00+08:00"),
                                 ("after", "2026-09-13T18:00:00+08:00")] {
            func read<T: Decodable>(_ name: String, as type: T.Type) throws -> T {
                try JSONDecoder().decode(type, from: Data(contentsOf: URL(fileURLWithPath: root)
                    .appendingPathComponent("b62_\(label)_\(name).json")))
            }
            let now = try XCTUnwrap(ISO8601DateFormatter().date(from: instant))
            let model = AppModel(clock: { now })
            model.dailyEvening = try read("evening", as: K10DailyReportResponse.self)
            model.dailyMorning = try read("morning", as: K10DailyReportResponse.self)
            model.companyWindows = try read("windows", as: K10CompanyWindowList.self).items
            let window = try XCTUnwrap(model.companyWindows.first {
                $0.companyCode == "300004.SZ" && $0.firstBatchId == "v2-batch-evening"
            })
            XCTAssertEqual(window.d2CloseAt, "2026-09-10T15:00:00+08:00")
            XCTAssertFalse(window.opportunities.isEmpty)
            XCTAssertTrue(window.opportunities.allSatisfy { $0.lifecycle == "withdrawal" })
            XCTAssertFalse(window.allowsSelection)
            let original = model.dailyLifecycleUpdates.filter { $0.companyWindowId == window.id }
            XCTAssertTrue(original.contains { $0.kind == "withdrawal" })
            let current = model.currentLifecycleUpdates.filter { $0.companyWindowId == window.id }
            let history = model.historicalLifecycleUpdates.filter { $0.companyWindowId == window.id }
            XCTAssertEqual(current, label == "before" ? original : [], label)
            XCTAssertEqual(history, label == "before" ? [] : original, label)
            XCTAssertEqual(model.dailyLifecycleUpdates.filter { $0.companyWindowId == window.id }, original)
            if label == "before" {
                XCTAssertTrue(model.currentLifecycleUpdates.contains { $0.companyCode == "300005.SZ" && $0.kind == "risk" },
                              "窗口内的当前风险继续可见")
            }
        }
    }

    @MainActor
    func testB62WithdrawnHistoryAcceptsFractionalUTCDeadline() async throws {
        let now = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-09-10T07:00:00Z"))
        let model = AppModel(serviceFactory: { K10SyntheticUIService() }, clock: { now })
        await model.refresh()
        let window = try XCTUnwrap(model.companyWindows.first)
        var payload = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(window)) as? [String: Any])
        payload["d2CloseAt"] = "2026-09-10T07:00:00.000000Z"
        payload["opportunities"] = (payload["opportunities"] as! [[String: Any]]).map { item in
            var withdrawn = item; withdrawn["lifecycle"] = "withdrawal"; return withdrawn
        }
        let ended = try JSONDecoder().decode(K10CompanyWindow.self, from: JSONSerialization.data(withJSONObject: payload))
        payload["companyWindowId"] = "independent-future-window"
        payload["d2CloseAt"] = "2026-09-11T15:00:00+08:00"
        model.companyWindows = [ended, try JSONDecoder().decode(K10CompanyWindow.self, from: JSONSerialization.data(withJSONObject: payload))]
        let notices = ["withdrawal", "risk"].map { kind in
            K10DailyLifecycleUpdate(updateId: kind, opportunityId: "withdrawn-opportunity", companyWindowId: window.id,
                companyCode: window.companyCode, companyName: "历史公司", kind: kind, reason: "保留原始依据",
                createdAt: "2026-09-09T09:00:00+08:00", sourceRefs: [])
        }
        let current = K10DailyLifecycleUpdate(updateId: "current-risk", opportunityId: "current-opportunity",
            companyWindowId: "independent-future-window", companyCode: window.companyCode, companyName: "同公司独立窗口",
            kind: "risk", reason: "仍在观察窗口内", createdAt: "2026-09-10T07:00:00Z", sourceRefs: [])
        model.dailyEvening?.report?.lifecycleUpdates = notices + [current]
        model.dailyMorning?.report?.lifecycleUpdates = []
        XCTAssertEqual(model.currentLifecycleUpdates, [current], "同公司未结束窗口的当前风险不能随旧窗口隐藏")
        XCTAssertEqual(model.historicalLifecycleUpdates, notices)
    }

    @MainActor
    func testB60HomeArchivesClosedCardsWithoutChangingPublishedReportOrMixedWindow() async throws {
        let service = K10SyntheticUIService()
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        var card = try XCTUnwrap(model.eveningCards.first)
        var mixed = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(card)) as? [String: Any])
        var catalysts = try XCTUnwrap(mixed["catalysts"] as? [[String: Any]])
        var oldCatalyst = try XCTUnwrap(catalysts.first)
        oldCatalyst["companyWindowId"] = "expired-other-window"
        oldCatalyst["opportunityId"] = "expired-other-opportunity"
        oldCatalyst["lifecycleState"] = "expired"
        catalysts.append(oldCatalyst); mixed["catalysts"] = catalysts
        card = try JSONDecoder().decode(K10DailyCard.self, from: JSONSerialization.data(withJSONObject: mixed))
        card.canSelect = true
        model.dailyEvening?.report?.eveningCards = [card]
        model.dailyMorning?.report?.updatedCards = [card]
        XCTAssertEqual(model.currentEveningCards, [card], "当前目标可选时，旧催化结束不能隐藏整张混合卡")
        card.canSelect = false
        model.dailyEvening?.report?.eveningCards = [card]
        model.dailyMorning?.report?.updatedCards = [card]
        model.dailyMorning?.report?.addedCards = [card]
        XCTAssertTrue(model.currentEveningCards.isEmpty)
        XCTAssertTrue(model.currentMorningCards.isEmpty)
        XCTAssertTrue(model.currentMorningUpdates.isEmpty)
        XCTAssertEqual(model.endedDailyCards, [card], "同一已结束卡在历史中只出现一次")
        XCTAssertEqual(model.eveningCards, [card], "首页投影不改已发布的推荐、选择或窗口")
        XCTAssertEqual(model.dailyMorning?.report?.addedCards, [card])
    }

    @MainActor
    func testV320DailySelectionKeepsPublishedCardAndTargetsItsWindow() async throws {
        let service = K10SyntheticUIService()
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        let card = try XCTUnwrap(model.eveningCards.first)
        XCTAssertEqual(card.section, "updated")
        XCTAssertEqual(model.eveningCards.count, 1, "晨间更新并入原公司卡")
        XCTAssertTrue(card.isUnverified)
        await model.act("keep", card: card)
        let after = try XCTUnwrap(model.eveningCards.first)
        XCTAssertEqual(after.cardId, card.cardId)
        XCTAssertEqual(after.currentSelectionState, "kept")
        XCTAssertEqual(after.companyWindowId, card.companyWindowId)
        XCTAssertEqual(after.d1TradeDate, card.d1TradeDate)
        XCTAssertEqual(after.d2TradeDate, card.d2TradeDate)
        let newCard = try XCTUnwrap(model.dailyMorning?.report?.addedCards.first)
        XCTAssertEqual(newCard.currentSelectionState, "unhandled", "留下旧机会不能替另一机会作选择")
        let selected = try XCTUnwrap(model.companyWindows.first { $0.id == card.companyWindowId })
        XCTAssertEqual(selected.selection?.state, "selected", "持久化 selected 映射到卡片 kept")
    }

    @MainActor
    func testV320DailyReadFailurePreservesExplicitlyStaleReportAndEmptyClearsIt() async throws {
        let service = ControlledK10Service(batchID: "daily-read")
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        let previous = try XCTUnwrap(model.dailyMorning)
        await service.setDailyFailure(.decoding("晨报结构无效"))
        await model.refresh()
        XCTAssertEqual(model.dailyMorning, previous)
        XCTAssertEqual(model.dailyReportErrors["morning"], "晨报结构无效")
        await service.setDailyFailure(nil)
        await service.setDailyEmpty()
        await model.refresh()
        XCTAssertNil(model.dailyMorning?.report)
        XCTAssertTrue(model.eveningCards.isEmpty, "历史窗口不能作为今日卡片的回退来源")
        XCTAssertFalse(model.companyWindows.isEmpty)
        XCTAssertTrue(model.dailyReportErrors.isEmpty)
    }

    @MainActor
    func testV320MorningPaginationDeduplicatesAndRejectsRepeatedCursor() async throws {
        let service = ControlledK10Service(batchID: "daily-pages")
        var first = try await service.latestDailyReport(window: "morning")
        first.report?.nextCursor = "cursor-one"
        var next = first
        next.report?.nextCursor = nil
        await service.setDailyPages(first: first, next: next)
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        let count = model.dailyMorning?.report?.addedCards.count
        await model.loadMoreDailyCards()
        XCTAssertEqual(model.dailyMorning?.report?.addedCards.count, count)
        XCTAssertNil(model.dailyMorning?.report?.nextCursor)
        await service.setDailyPages(first: first, next: first)
        await model.refresh()
        await model.loadMoreDailyCards()
        XCTAssertNotNil(model.dailyReportErrors["morning"])
        XCTAssertEqual(model.dailyMorning?.report?.nextCursor, "cursor-one")
    }

    func testV320ActualFastAPIReportResponsesDecode() async throws {
        guard let root = ProcessInfo.processInfo.environment["NK_V320_DTO_DIR"] else {
            throw XCTSkip("Set NK_V320_DTO_DIR to isolated FastAPI response artifacts")
        }
        let decoder = JSONDecoder()
        let evening = try decoder.decode(K10DailyReportResponse.self, from: Data(contentsOf: URL(fileURLWithPath: root).appendingPathComponent("evening.json")))
        let morning = try decoder.decode(K10DailyReportResponse.self, from: Data(contentsOf: URL(fileURLWithPath: root).appendingPathComponent("morning.json")))
        XCTAssertEqual(evening.schemaVersion, 8)
        XCTAssertEqual(evening.report?.strategyVersion, "K10-v2")
        XCTAssertFalse(try XCTUnwrap(evening.report).eveningCards.isEmpty)
        XCTAssertFalse(try XCTUnwrap(morning.report).updatedCards.isEmpty)
        XCTAssertFalse(try XCTUnwrap(morning.report).addedCards.isEmpty)
        for card in (evening.report?.eveningCards ?? []) + (morning.report?.updatedCards ?? []) + (morning.report?.addedCards ?? []) {
            XCTAssertTrue(["kept", "skipped", "unhandled"].contains(card.currentSelectionState))
            XCTAssertFalse(card.companyWindowId.isEmpty)
            XCTAssertFalse(card.catalysts.isEmpty)
        }
        func read<T: Decodable>(_ name: String, as type: T.Type) throws -> T {
            try decoder.decode(type, from: Data(contentsOf: URL(fileURLWithPath: root).appendingPathComponent(name + ".json")))
        }
        let configuration = try read("configuration", as: K10Configuration.self)
        XCTAssertEqual(configuration.profileReviewStatus, "local_draft_awaiting_user")
        XCTAssertNotNil(configuration.universeSnapshotId)
        let windows = try read("windows", as: K10CompanyWindowList.self).items
        XCTAssertEqual(Set(windows.compactMap(\.strategyVersion)), ["K10-v1.4", "K10-v2"])
        XCTAssertTrue(windows.filter { $0.strategyVersion == "K10-v2" }.allSatisfy { !($0.companyName?.isEmpty ?? true) })
        let mixed = try XCTUnwrap(morning.report?.updatedCards.first)
        XCTAssertEqual(mixed.sampleClass, "overlap")
        XCTAssertEqual(mixed.currentSelectionState, "unhandled")
        XCTAssertEqual(Set(mixed.catalysts.map(\.companyWindowId)).count, 2)
        let original = try XCTUnwrap(evening.report?.eveningCards.first { $0.companyCode == mixed.companyCode })
        XCTAssertNotEqual(original.companyWindowId, mixed.companyWindowId)
        XCTAssertEqual(windows.first { $0.id == original.companyWindowId }?.selection?.state, "selected")
        let detail = try read("detail", as: K10OpportunityDetail.self)
        XCTAssertEqual(detail.strategyVersion, "K10-v2")
        let results = try read("results", as: K10Results.self)
        let historical = try read("historical_results", as: K10Results.self)
        XCTAssertEqual(results.strategyVersion, "K10-v2")
        XCTAssertEqual(historical.strategyVersion, "K10-v1.4")
        XCTAssertTrue(Set(results.records.map(\.companyWindowId)).isDisjoint(with: historical.records.map(\.companyWindowId)))
    }

    @MainActor
    func testV320RepairActualLifecycleResponsesReachCurrentCards() async throws {
        guard let root = ProcessInfo.processInfo.environment["NK_V320_DTO_DIR"] else {
            throw XCTSkip("Set NK_V320_DTO_DIR to actual isolated repair responses")
        }
        let decoder = JSONDecoder()
        func read<T: Decodable>(_ name: String, as type: T.Type) throws -> T {
            try decoder.decode(type, from: Data(contentsOf: URL(fileURLWithPath: root).appendingPathComponent(name + ".json")))
        }
        let evening = try read("repair_evening", as: K10DailyReportResponse.self)
        let morning = try read("repair_morning", as: K10DailyReportResponse.self)
        let windows = try read("repair_windows", as: K10CompanyWindowList.self).items
        let model = AppModel()
        model.dailyEvening = evening
        model.dailyMorning = morning
        model.companyWindows = windows
        let updates = model.dailyLifecycleUpdates
        XCTAssertTrue(Set(updates.map(\.kind)).isSuperset(of: ["risk", "withdrawal", "expiry"]))
        XCTAssertEqual(Set(updates.map(\.id)).count, updates.count)
        XCTAssertTrue(updates.allSatisfy { !$0.reason.isEmpty && !$0.companyWindowId.isEmpty && !$0.opportunityId.isEmpty })
        let cards = model.eveningCards + (morning.report?.addedCards ?? [])
        XCTAssertTrue(updates.contains { update in !cards.contains { $0.companyWindowId == update.companyWindowId } }, "无今日卡的旧关注变化仍可见")
        let closed = try XCTUnwrap(cards.first { $0.canSelect == false })
        XCTAssertFalse(closed.allowsSelection)
        let closedReasons = closed.catalysts.filter { $0.companyWindowId == closed.companyWindowId }
        XCTAssertFalse(closedReasons.isEmpty)
        XCTAssertTrue(closedReasons.allSatisfy { ["withdrawn", "expired"].contains($0.lifecycleState ?? "") })
        let mixed = try XCTUnwrap(cards.first { $0.canSelect == true && Set($0.catalysts.map(\.companyWindowId)).count > 1 })
        XCTAssertTrue(mixed.allowsSelection)
        XCTAssertTrue(mixed.catalysts.contains { $0.companyWindowId != mixed.companyWindowId && $0.lifecycleState == "withdrawn" })
        XCTAssertTrue(mixed.catalysts.contains { $0.companyWindowId == mixed.companyWindowId && ["active", "risk"].contains($0.lifecycleState ?? "") })
        let originalWindow = try XCTUnwrap(windows.first { $0.companyWindowId == closed.companyWindowId })
        XCTAssertEqual(originalWindow.canSelect, false)
        XCTAssertFalse(originalWindow.allowsSelection)
        let mixedTarget = try XCTUnwrap(windows.first { $0.companyWindowId == mixed.companyWindowId })
        XCTAssertEqual(mixedTarget.canSelect, true)
        XCTAssertTrue(mixedTarget.allowsSelection)
        XCTAssertEqual(closed.d1TradeDate, originalWindow.d1TradeDate)
        XCTAssertEqual(closed.d2TradeDate, originalWindow.d2TradeDate)
        XCTAssertEqual(mixed.currentSelectionState, "unhandled", "旧催化的历史选择不能传给新的目标窗口")
    }

    @MainActor
    func testV320ActualFailedMorningRetainsCardsAndListsIncompleteReviews() async throws {
        guard let root = ProcessInfo.processInfo.environment["NK_V320_DTO_DIR"] else {
            throw XCTSkip("Set NK_V320_DTO_DIR to actual isolated boundary responses")
        }
        let data = try Data(contentsOf: URL(fileURLWithPath: root).appendingPathComponent("boundary_morning_failure.json"))
        let response = try JSONDecoder().decode(K10DailyReportResponse.self, from: data)
        let report = try XCTUnwrap(response.report)
        XCTAssertEqual(report.status, "partial")
        XCTAssertNotNil(response.reason)
        XCTAssertFalse(try XCTUnwrap(report.coverageGaps).isEmpty)
        let incomplete = try XCTUnwrap(report.incompleteReviews)
        XCTAssertFalse(incomplete.isEmpty)
        XCTAssertTrue(incomplete.allSatisfy { !$0.reason.isEmpty && !$0.companyCode.isEmpty && !$0.companyWindowId.isEmpty && $0.status != "completed" })
        let service = ControlledK10Service(batchID: "morning-partial")
        await service.setDailyPages(first: response, next: response)
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        XCTAssertEqual(model.dailyMorning?.report?.incompleteReviews, incomplete)
        XCTAssertEqual(model.dailyMorning?.report?.updatedCards, report.updatedCards)
        XCTAssertEqual(model.dailyMorning?.report?.addedCards, report.addedCards)
        XCTAssertFalse(report.updatedCards.isEmpty && report.addedCards.isEmpty, "子复核失败不能抹掉已经完成的推荐")
    }

    @MainActor
    func testV320ActualClosedHistoryDoesNotDisableIndependentNewWindow() async throws {
        guard let root = ProcessInfo.processInfo.environment["NK_V320_DTO_DIR"] else {
            throw XCTSkip("Set NK_V320_DTO_DIR to actual isolated boundary responses")
        }
        let data = try Data(contentsOf: URL(fileURLWithPath: root).appendingPathComponent("boundary_windows.json"))
        let windows = try JSONDecoder().decode(K10CompanyWindowList.self, from: data).items
        let closed = try XCTUnwrap(windows.first { $0.currentSelectionState == "skipped" && $0.canSelect == false })
        let active = try XCTUnwrap(windows.first { $0.companyCode == closed.companyCode && $0.canSelect == true })
        XCTAssertNotEqual(closed.id, active.id)
        let service = ControlledK10Service(batchID: "actual-closed-history")
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        await model.act("restore", window: closed)
        let closedCalls = await service.dailyActionCallCount()
        XCTAssertEqual(closedCalls, 0)
        await model.act("restore", window: active)
        let activeCalls = await service.dailyActionCallCount()
        XCTAssertEqual(activeCalls, 1, "限制必须只作用于关闭窗口，而非整家公司")
    }
    @MainActor
    func testB55ActualConsistencyResponses() async throws {
        let root = try XCTUnwrap(ProcessInfo.processInfo.environment["NK_V320_DTO_DIR"])
        func read<T: Decodable>(_ name: String, as type: T.Type) throws -> T {
            try JSONDecoder().decode(type, from: Data(contentsOf: URL(fileURLWithPath: root).appendingPathComponent(name + ".json")))
        }
        let morning = try read("b55_morning", as: K10DailyReportResponse.self)
        let change = try XCTUnwrap(morning.report?.lifecycleUpdates?.first { $0.kind == "evidence_update" })
        XCTAssertEqual(change.label, "事实与论点变化")
        XCTAssertTrue(try XCTUnwrap(morning.report).updatedCards.isEmpty)
        let evening = try read("b55_evening", as: K10DailyReportResponse.self)
        let card = try XCTUnwrap(evening.report?.eveningCards.first)
        XCTAssertTrue(try XCTUnwrap(card.priceReaction).contains("+12.00%"))
        XCTAssertEqual(card.priceContext?.tradeDate, "2026-09-08")
        XCTAssertFalse(try XCTUnwrap(card.priceContext).sourceRefs.isEmpty)
        let chain = try read("b55_analysis", as: K10AnalysisChain.self)
        let con = try XCTUnwrap(chain.items.first?.analyses.first { $0.role == "con" })
        XCTAssertFalse(try XCTUnwrap(con.summary).commonFacts.isEmpty)
        XCTAssertFalse(try XCTUnwrap(con.summary).disagreements.isEmpty)
        XCTAssertFalse(try XCTUnwrap(con.summary).unknowns.isEmpty)
        XCTAssertTrue(try XCTUnwrap(con.fullText).contains("共同未知"))
        let results = try read("b55_results", as: K10Results.self)
        XCTAssertEqual(results.primary["all"]?.consecutiveLimitUpCount, 1)
        XCTAssertEqual(results.primary["all"]?.hitCount, 1)
        XCTAssertEqual(results.records.first?.consecutiveLimitUp, true)
    }

    @MainActor
    func testB56ActualProviderRecoveryAndStageWindowResponses() async throws {
        let root = try XCTUnwrap(ProcessInfo.processInfo.environment["NK_V320_DTO_DIR"])
        func read<T: Decodable>(_ name: String, as type: T.Type) throws -> T {
            try JSONDecoder().decode(type, from: Data(contentsOf: URL(fileURLWithPath: root).appendingPathComponent(name + ".json")))
        }
        for role in ["pro", "con"] {
            let unpaid = try read("b56_analysis_" + role + "_402", as: K10AnalysisChain.self)
            XCTAssertEqual(unpaid.items.first?.job?.status, "failed")
            XCTAssertTrue(try XCTUnwrap(unpaid.items.first?.analyses.first { $0.role == role }?.error).contains("余额不足"))
            let throttled = try read("b56_analysis_" + role + "_429", as: K10AnalysisChain.self)
            XCTAssertEqual(throttled.items.first?.job?.status, "queued")
            XCTAssertTrue(try XCTUnwrap(throttled.items.first?.job?.error?.message).contains("延后重试"))
            let recovered = try read("b56_analysis_" + role + "_recovered", as: K10AnalysisChain.self)
            XCTAssertEqual(recovered.items.first?.job?.status, "completed")
            XCTAssertEqual(recovered.items.first?.analyses.count, 2)
            XCTAssertTrue(try XCTUnwrap(recovered.items.first).analyses.allSatisfy { $0.status == "completed" })
        }
        for code in [402, 429] {
            let morning = try read("b56_morning_" + String(code), as: K10DailyReportResponse.self)
            let child = try XCTUnwrap(morning.report?.incompleteReviews?.first)
            XCTAssertEqual(child.status, code == 402 ? "failed" : "queued")
            XCTAssertTrue(child.reason.contains(code == 402 ? "余额不足" : "延后重试"))
            XCTAssertEqual(morning.report?.status, "partial")
        }
        let restored = try read("b56_morning_recovered", as: K10DailyReportResponse.self)
        XCTAssertEqual(restored.report?.status, "completed")
        XCTAssertTrue(try XCTUnwrap(restored.report?.incompleteReviews).isEmpty)
        let evening = try read("b56_stage_evening", as: K10DailyReportResponse.self)
        let card = try XCTUnwrap(evening.report?.eveningCards.first)
        XCTAssertEqual(card.d1TradeDate, "2026-09-10")
        XCTAssertEqual(card.d2TradeDate, "2026-09-11")
        XCTAssertEqual(card.canSelect, true)
        XCTAssertEqual(card.catalysts.first?.companyWindowId, card.companyWindowId)
    }

    @MainActor
    private func b57MorningPages() async throws -> (ControlledK10Service, K10DailyReportResponse, K10DailyReportResponse, K10DailyCard) {
        let service = ControlledK10Service(batchID: "b57-pagination")
        var first = try await service.latestDailyReport(window: "morning")
        let tail = try XCTUnwrap(first.report?.addedCards.first)
        let template = try JSONEncoder().encode(tail)
        first.report?.addedCards = try (1...30).map { index in
            var row = try JSONSerialization.jsonObject(with: template) as! [String: Any]
            row["cardId"] = "b57-card-\(index)"
            row["companyCode"] = String(format: "300%03d.SZ", index)
            row["companyWindowId"] = "b57-window-\(index)"
            row["rank"] = index
            return try JSONDecoder().decode(K10DailyCard.self, from: JSONSerialization.data(withJSONObject: row))
        }
        let firstCursor = first.report?.addedCards.last?.cardId
        first.report?.nextCursor = firstCursor
        var next = first
        next.report?.addedCards = [tail]
        next.report?.nextCursor = nil
        await service.setDailyPages(first: first, next: next)
        return (service, first, next, tail)
    }

    @MainActor
    func testB57SecondPageSelectionRefreshPreservesLoadedCardsAndUpdatesState() async throws {
        let (service, _, _, tail) = try await b57MorningPages()
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        await model.loadMoreDailyCards()
        let ids = try XCTUnwrap(model.dailyMorning?.report).addedCards.map(\.cardId)
        XCTAssertEqual(ids.count, 31)
        for (action, state) in [("skip", "skipped"), ("keep", "kept"), ("restore", "unhandled")] {
            await model.act(action, card: tail)
            let report = try XCTUnwrap(model.dailyMorning?.report)
            XCTAssertEqual(report.addedCards.map(\.cardId), ids, "当前卡不能在刷新期间被第一页替换掉")
            XCTAssertEqual(report.addedCards.last?.currentSelectionState, state)
            XCTAssertNil(report.nextCursor)
            XCTAssertNil(model.dailyReportErrors["morning"])
        }
    }

    @MainActor
    func testB57RefreshPageFailurePreservesRangeAndNewReportResetsIt() async throws {
        let (service, first, next, _) = try await b57MorningPages()
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        await model.loadMoreDailyCards()
        let previous = model.dailyMorning
        await service.setDailyPageFailure(.networkUnavailable("后续页暂时不可读"))
        await model.refresh()
        XCTAssertEqual(model.dailyMorning, previous)
        XCTAssertNotNil(model.dailyReportErrors["morning"])
        await service.setDailyPageFailure(nil)
        var encoded = try JSONSerialization.jsonObject(with: JSONEncoder().encode(first)) as! [String: Any]
        var report = encoded["report"] as! [String: Any]
        report["reportId"] = "b57-new-morning"
        encoded["report"] = report
        let new = try JSONDecoder().decode(K10DailyReportResponse.self, from: JSONSerialization.data(withJSONObject: encoded))
        await service.setDailyPages(first: new, next: next)
        await model.refresh()
        XCTAssertEqual(model.dailyMorning?.report?.reportId, "b57-new-morning")
        XCTAssertEqual(model.dailyMorning?.report?.addedCards.count, 30)
        XCTAssertNil(model.dailyReportErrors["morning"])
    }

    func testB57ActualEarlyFailureAndParentRecoveryResponsesDecode() throws {
        let root = try XCTUnwrap(ProcessInfo.processInfo.environment["NK_V320_DTO_DIR"])
        for kind in ["morning", "evening"] {
            let data = try Data(contentsOf: URL(fileURLWithPath: root).appendingPathComponent("b57_early_\(kind)_provider.json"))
            let response = try JSONDecoder().decode(K10DailyReportResponse.self, from: data)
            XCTAssertEqual(response.report?.status, "not_configured")
            XCTAssertTrue(try XCTUnwrap(response.reason?.message).contains("参数未配置"))
            XCTAssertTrue(try XCTUnwrap(response.report?.cutoffAt).hasPrefix("2026-09-09"))
            XCTAssertNil(response.report?.availableAt)
        }
        for status in [402, 429] {
            let data = try Data(contentsOf: URL(fileURLWithPath: root).appendingPathComponent("b57_parent_\(status).json"))
            let response = try JSONDecoder().decode(K10DailyReportResponse.self, from: data)
            XCTAssertEqual(response.report?.status, "partial")
            XCTAssertEqual(response.report?.incompleteReviews?.first?.status, "failed")
            XCTAssertTrue(try XCTUnwrap(response.report?.incompleteReviews?.first?.reason).contains(status == 402 ? "余额不足" : "重试上限"))
        }
    }

    @MainActor
    func testB57ActualPaginatedAPIResponsesSurviveActionRefresh() async throws {
        let root = try XCTUnwrap(ProcessInfo.processInfo.environment["NK_V320_DTO_DIR"])
        func read<T: Decodable>(_ name: String, as type: T.Type) throws -> T {
            try JSONDecoder().decode(type, from: Data(contentsOf: URL(fileURLWithPath: root).appendingPathComponent("b57_pages_" + name + ".json")))
        }
        let first = try read("first", as: K10DailyReportResponse.self)
        let next = try read("next", as: K10DailyReportResponse.self)
        let selected = try read("selected", as: K10DailyReportResponse.self)
        let action = try read("action", as: K10SelectionAction.self)
        let tail = try XCTUnwrap(next.report?.addedCards.first)
        let service = ControlledK10Service(batchID: "b57-real-pages")
        await service.setDailyPages(first: first, next: next)
        let model = AppModel(serviceFactory: { service })
        await model.refresh()
        await model.loadMoreDailyCards()
        XCTAssertEqual(model.dailyMorning?.report?.addedCards.count, 31)
        await service.setDailyPages(first: first, next: selected)
        await service.setDailyActionResponse(action)
        await model.act("skip", card: tail)
        let refreshed = try XCTUnwrap(model.dailyMorning?.report)
        XCTAssertEqual(refreshed.addedCards.count, 31)
        XCTAssertEqual(refreshed.addedCards.last?.cardId, tail.cardId)
        XCTAssertEqual(refreshed.addedCards.last?.currentSelectionState, "skipped")
        XCTAssertEqual(refreshed.addedCards.last?.companyWindowId, tail.companyWindowId)
        XCTAssertEqual(refreshed.addedCards.last?.d1TradeDate, tail.d1TradeDate)
        XCTAssertNil(model.dailyReportErrors["morning"])
    }

}

extension K10V3Tests {
    @MainActor func testB59ActualSettingsAPIEditingAndFailureKeepsState() async throws {
        guard let raw = ProcessInfo.processInfo.environment["NK_B59_API_URL"], let url = URL(string: raw) else {
            throw XCTSkip("Isolated BYOK API was not requested")
        }
        let suite = "b59-byok-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let config = AppConfig(defaults: defaults, tokenStore: RecordingTokenStore(), loadPersistentCredentials: false)
        config.apiToken = "b59-local-test-token-only"; config.baseURLOverride = raw
        let model = AppModel(serviceFactory: { nil }, cacheClearer: {})
        model.bind(config: config)
        let name = "常用 模型-\(UUID().uuidString)"
        let secondName = "备用-\(UUID().uuidString)"
        let saved = await model.saveModelConnection(name: name, baseURL: "https://gateway.example/v1/", modelName: "vendor/custom-model", apiKey: "synthetic-key", enabled: true, creating: true)
        XCTAssertTrue(saved)
        XCTAssertEqual(model.providers.first?.baseUrl, "https://gateway.example/v1/chat/completions")
        XCTAssertEqual(model.providers.first?.model, "vendor/custom-model")
        XCTAssertEqual(model.providers.first?.keySet, true)
        let updated = await model.saveModelConnection(name: name, baseURL: "https://gateway.example/v1", modelName: "changed-model", apiKey: "", enabled: true, creating: false)
        XCTAssertTrue(updated)
        XCTAssertEqual(model.providers.first?.keySet, true)
        let duplicate = await model.saveModelConnection(name: name, baseURL: "https://gateway.example/v1", modelName: "overwrite", apiKey: "wrong-key", enabled: true, creating: true)
        XCTAssertFalse(duplicate)
        XCTAssertTrue(model.providerSettingsError?.contains("已存在") == true)
        XCTAssertEqual(model.providers.first?.model, "changed-model")
        XCTAssertFalse(model.providerSettingsSaving)
        let second = await model.saveModelConnection(name: secondName, baseURL: "https://second.example/v1", modelName: "other-model", apiKey: "other-key", enabled: true, creating: true)
        XCTAssertTrue(second)
        XCTAssertEqual(model.providers.filter(\.enabled).map(\.name), [secondName])
        let blocked = await model.saveModelConnection(name: secondName, baseURL: "https://third.example/v1", modelName: "other-model", apiKey: "", enabled: true, creating: false)
        XCTAssertFalse(blocked)
        XCTAssertTrue(model.providerSettingsError?.contains("API Key") == true)
        let cleared = await model.saveModelConnection(name: secondName, baseURL: "https://second.example/v1", modelName: "other-model", apiKey: "", enabled: false, creating: false, clearKey: true)
        XCTAssertTrue(cleared)
        XCTAssertEqual(model.providers.first { $0.name == secondName }?.keySet, false)
        let client = K10AdminClient(baseURL: url, token: "b59-local-test-token-only")
        let rows = try await client.providers()
        XCTAssertEqual(rows.first { $0.name == name }?.model, "changed-model")
        XCTAssertEqual(rows.first { $0.name == name }?.keySet, true)
        let removed = await model.deleteModelConnection(name: name)
        XCTAssertTrue(removed)
        let removedSecond = await model.deleteModelConnection(name: secondName)
        XCTAssertTrue(removedSecond)
        let after = try await client.providers()
        XCTAssertFalse(after.contains { $0.name == name || $0.name == secondName })
    }

    @MainActor func testB59ActualSettingsDTOAndNativeScreens() async throws {
        guard let root = ProcessInfo.processInfo.environment["NK_B59_DTO_DIR"] else {
            throw XCTSkip("Isolated BYOK response artifacts were not requested")
        }
        for state in ["empty", "providers"] {
            let data = try Data(contentsOf: URL(fileURLWithPath: root).appendingPathComponent(state + ".json"))
            let page = try JSONDecoder().decode(K10ProviderList.self, from: data)
            XCTAssertEqual(page.items.count, state == "empty" ? 0 : 2)
            XCTAssertFalse(String(decoding: data, as: UTF8.self).contains("synthetic-only"))
            let model = AppModel(serviceFactory: { nil }, cacheClearer: {})
            model.providers = page.items
            if state == "providers" {
                XCTAssertEqual(page.items.first?.model, "vendor/model-a")
                XCTAssertEqual(page.items.first?.keySet, true)
            }
            try await renderB59Editor(model: model, root: root, state: state)
        }
    }
}

@MainActor private func renderB59Editor(model: AppModel, root: String, state: String) async throws {
    #if os(macOS)
    let host = NSHostingView(rootView: ModelEditor(model: model))
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 720, height: 920), styleMask: [.titled], backing: .buffered, defer: false)
    window.contentView = host; window.appearance = NSAppearance(named: .aqua)
    window.orderFront(nil)
    defer { window.orderOut(nil) }
    try await Task.sleep(for: .milliseconds(350))
    host.layoutSubtreeIfNeeded()
    let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
    host.cacheDisplay(in: host.bounds, to: bitmap)
    try XCTUnwrap(bitmap.representation(using: .png, properties: [:])).write(to: URL(fileURLWithPath: root).appendingPathComponent("macos-\(state).png"))
    #else
    let controller = UIHostingController(rootView: ModelEditor(model: model))
    let scene = try XCTUnwrap(UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first)
    let window = UIWindow(windowScene: scene)
    window.frame = scene.coordinateSpace.bounds
    window.overrideUserInterfaceStyle = .light
    window.rootViewController = controller; window.makeKeyAndVisible()
    defer { window.isHidden = true }
    try await Task.sleep(for: .milliseconds(350))
    controller.view.layoutIfNeeded()
    func capture(_ suffix: String) throws {
        let image = UIGraphicsImageRenderer(size: window.bounds.size).image { _ in
            XCTAssertTrue(window.drawHierarchy(in: window.bounds, afterScreenUpdates: true), "Native screen must actually be visible")
        }
        try XCTUnwrap(image.pngData()).write(to: URL(fileURLWithPath: root).appendingPathComponent("ios-\(state)-\(suffix).png"))
    }
    try capture("top")
    func scrollView(_ view: UIView) -> UIScrollView? {
        if let scroll = view as? UIScrollView, scroll.contentSize.height > scroll.bounds.height { return scroll }
        return view.subviews.compactMap { scrollView($0) }.first
    }
    if let scroll = scrollView(controller.view) {
        scroll.setContentOffset(CGPoint(x: 0, y: max(0, scroll.contentSize.height - scroll.bounds.height + scroll.adjustedContentInset.bottom)), animated: false)
        try await Task.sleep(for: .milliseconds(150))
        try capture("bottom")
    }
    #endif
}

extension K10V3Tests {
    @MainActor func testB59SlowSettingsReadCannotUndoSavedConnection() async throws {
        let suite = "b59-read-race-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let config = AppConfig(defaults: defaults, tokenStore: RecordingTokenStore())
        config.apiToken = "synthetic-token"
        config.baseURLOverride = "https://example.test"
        let gate = RefreshGate()
        let old = ControlledAdminService(providerName: "stale", tavilyKeySet: false, providersGate: gate)
        let saved = ControlledAdminService(providerName: "saved", tavilyKeySet: false)
        let admins = AdminServiceBox(old)
        let model = AppModel(serviceFactory: { nil }, cacheClearer: {}, adminServiceFactory: { _, _ in admins.service })
        model.bind(config: config)
        let loading = Task { await model.refreshAdminSettings() }
        await gate.waitUntilEntered()
        admins.service = saved
        let success = await model.saveModelConnection(name: "saved", baseURL: "https://api.deepseek.com/v1", modelName: "deepseek-v4-pro", apiKey: "synthetic-key", enabled: true, creating: true)
        XCTAssertTrue(success)
        await gate.open()
        await loading.value
        XCTAssertEqual(model.providers.map(\.name), ["saved"])
    }
}
