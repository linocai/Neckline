import XCTest
import Foundation
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

        let saving = Task { await model.saveDeepSeekConnection(name: "deepseek", apiKey: "new-key", enabled: true) }
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

    func testExecutionProgressAndNotificationReadinessDecodeOnlySafeFields() throws {
        let scanData = Data("""
        {
          "schemaVersion":"k10-api-v2","scanId":"scan-safe","window":"evening",
          "cutoffAt":"2026-09-08T21:00:00+08:00","status":"partial","coverageStatus":"partial",
          "coverageGaps":["source_coverage_incomplete"],"sourceCoverage":[],"publicationStatus":"not_published",
          "publicationBatchId":null,"availableAt":null,"configId":"k10-v1.4-production","configRevision":2,
          "createdAt":"2026-09-08T21:00:00+08:00","completedAt":null,
          "executionProgress":{"state":"partial","stage":"understanding","coverageStatus":"partial",
            "documentCounts":{"received":2472,"deduplicated":3,"templateSkipped":18,"understood":6,"fullText":2,"failedPending":1},
            "eventCounts":{"verified":0,"compared":0,"publishable":0},"nextRetryAt":"2026-09-08T22:00:00+08:00",
            "safeFailures":[{"stage":"understanding","code":"model_output_invalid","ref":"doc-safe@1"}],
            "strategyBinding":{"configId":"k10-v1.4-production","revision":2},
            "executionBinding":{"configId":"k10-execution","revision":1}}
        }
        """.utf8)
        let scan = try JSONDecoder().decode(K10Scan.self, from: scanData)
        XCTAssertEqual(scan.executionProgress?.documentCounts.received, 2472)
        XCTAssertEqual(scan.executionProgress?.safeFailures.first?.code, "model_output_invalid")
        XCTAssertEqual(scan.executionProgress?.safeFailures.first?.ref, "doc-safe@1")

        let readinessData = Data("""
        {"schemaVersion":"k10-api-v2","notificationReadiness":{"state":"blocked","reasonCode":"credentials_missing","nextRetryAt":null,"checkedAt":"2026-09-08T21:01:00+08:00"}}
        """.utf8)
        let readiness = try JSONDecoder().decode(K10OperationsReadiness.self, from: readinessData)
        XCTAssertEqual(readiness.notificationReadiness.state, "blocked")
        XCTAssertEqual(readiness.notificationReadiness.reasonCode, "credentials_missing")
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
    func latestMorningReport() async throws -> K10MorningReport? {
        if let morningFailure { throw morningFailure }
        return morningReport
    }
    func opportunity(id: String) async throws -> K10OpportunityDetail {
        if let opportunityGate { await opportunityGate.wait() }
        return try await fixture.opportunity(id: id)
    }
    func act(companyWindowID: String, request: K10SelectionRequest) async throws -> K10SelectionAction { try await fixture.act(companyWindowID: companyWindowID, request: request) }
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
