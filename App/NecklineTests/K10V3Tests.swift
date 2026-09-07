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

    func testSourceReferenceIdentityKeepsDocumentRevisionsDistinct() {
        let first = K10SourceReference(documentId: "doc", factId: nil, companyCode: nil, tradeDate: nil, revision: 1, sourceKey: "test", title: nil, url: nil, excerpt: nil, publishedAt: "2026-09-06", publishedPrecision: "date", fetchedAt: nil)
        let second = K10SourceReference(documentId: "doc", factId: nil, companyCode: nil, tradeDate: nil, revision: 2, sourceKey: "test", title: nil, url: nil, excerpt: nil, publishedAt: "2026-09-06", publishedPrecision: "date", fetchedAt: nil)
        XCTAssertNotEqual(first.id, second.id)
        let marketFirst = K10SourceReference(documentId: nil, factId: "fact", companyCode: "300001.SZ", tradeDate: "2026-09-07", revision: 1, sourceKey: "market", title: nil, url: nil, excerpt: nil, publishedAt: nil, publishedPrecision: "unknown", fetchedAt: nil)
        let marketSecond = K10SourceReference(documentId: nil, factId: "fact", companyCode: "300001.SZ", tradeDate: "2026-09-08", revision: 2, sourceKey: "market", title: nil, url: nil, excerpt: nil, publishedAt: nil, publishedPrecision: "unknown", fetchedAt: nil)
        XCTAssertNotEqual(marketFirst.id, marketSecond.id)
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
        let windows = try await client.companyWindows()
        XCTAssertEqual(windows.count, 2)
        let primaryWindow = try XCTUnwrap(windows.first(where: { $0.companyCode == "300001.SZ" }))
        XCTAssertEqual(primaryWindow.sampleClass, "primary")
        XCTAssertEqual(primaryWindow.opportunities.count, 1)
        XCTAssertEqual(primaryWindow.opportunities[0].sourceMarker, "evening")
        XCTAssertEqual(primaryWindow.opportunities[0].latePublication, false)
        let detail = try await client.opportunity(id: primaryWindow.opportunities[0].opportunityId)
        XCTAssertEqual(detail.eventHeadline, "合成催化")
        XCTAssertEqual(detail.commonFacts.count, 2)
        XCTAssertEqual(detail.samples.count, 2)
        let alternate = try XCTUnwrap(detail.samples.first(where: { $0.comparison.rank == 2 }))
        XCTAssertEqual(alternate.comparison.priorityReason, "受益较弱")
        XCTAssertEqual(alternate.comparison.gap, "订单兑现较慢")
        XCTAssertEqual(alternate.comparison.rankChangeConditions, "订单超预期")
        XCTAssertEqual(alternate.comparison.twoDayReason, "催化尚可")
        let result = try await client.results()
        XCTAssertEqual(result.records.count, 2)
        let incomplete = try XCTUnwrap(result.records.first(where: { $0.companyCode == "300001.SZ" }))
        XCTAssertEqual(incomplete.state, "incomplete")
        XCTAssertEqual(incomplete.d2?.availability, "data_gap")
        let marketFact = try XCTUnwrap(incomplete.d1?.sourceRefs.first)
        XCTAssertEqual(marketFact.factId, "market-d1")
        XCTAssertEqual(marketFact.companyCode, "300001.SZ")
        XCTAssertEqual(marketFact.tradeDate, "2026-09-07")
        XCTAssertEqual(marketFact.revision, 1)
        XCTAssertEqual(incomplete.firstTouchStatus, "unknown")
        XCTAssertEqual(incomplete.d1PriceChanges?["close"] ?? nil, 0.01)
        XCTAssertEqual(incomplete.comparability, "unknown")
        XCTAssertFalse(incomplete.primaryEligible)
        XCTAssertEqual(result.primary["all"]?.sampleCount, 2)
        XCTAssertGreaterThanOrEqual(result.primary["all"]?.dataGapCount ?? -1, 0)
        XCTAssertEqual(result.cohorts?.first?.companySampleCount, 2)
        XCTAssertEqual(result.eventGroups?.first?.companySampleCount, 2)
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
    private let healthFailure: K10APIError?
    private let fixture = K10SyntheticUIService()

    init(batchID: String, empty: Bool = false, healthGate: RefreshGate? = nil, publicationGate: RefreshGate? = nil, opportunityGate: RefreshGate? = nil, healthFailure: K10APIError? = nil) {
        self.batchID = batchID
        self.empty = empty
        self.healthGate = healthGate
        self.publicationGate = publicationGate
        self.opportunityGate = opportunityGate
        self.healthFailure = healthFailure
    }

    func health() async throws -> K10Health {
        if let healthGate { await healthGate.wait() }
        if let healthFailure { throw healthFailure }
        return K10Health(status: "ok", version: "v3.0.1")
    }

    func latestScan(window: String) async throws -> K10Scan {
        if empty { throw K10APIError.notFound("尚无扫描") }
        return try await fixture.latestScan(window: window)
    }

    func publications() async throws -> [K10Publication] {
        if let publicationGate { await publicationGate.wait() }
        guard !empty else { return [] }
        return [K10Publication(schemaVersion: "k10-api-v2", batchId: batchID, scanId: "scan-\(batchID)", publicationKind: "evening", availableAt: "2026-09-07T21:00:00+08:00", createdAt: "2026-09-07T21:00:00+08:00", sampleCount: 1)]
    }

    func companyWindows() async throws -> [K10CompanyWindow] {
        if empty { return [] }
        return try await fixture.companyWindows()
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
    func document(id: String, revision: Int?, offset: Int, limit: Int) async throws -> K10DocumentPage { try await fixture.document(id: id, revision: revision, offset: offset, limit: limit) }
    func job(id: String) async throws -> K10Job { try await fixture.job(id: id) }
    func retryJob(id: String, expectedAttemptCount: Int) async throws -> K10Job { try await fixture.retryJob(id: id, expectedAttemptCount: expectedAttemptCount) }
    func results() async throws -> K10Results { try await fixture.results() }
    func configuration() async throws -> K10Configuration { try await fixture.configuration() }
    func usageSummary() async throws -> K10UsageSummary { try await fixture.usageSummary() }
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
