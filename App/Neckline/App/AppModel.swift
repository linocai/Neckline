import Foundation
import Observation

enum AppTab: String, CaseIterable, Identifiable {
    case opportunities, focus, performance, settings
    var id: String { rawValue }
    var title: String { switch self { case .opportunities: return "机会"; case .focus: return "关注"; case .performance: return "选股表现"; case .settings: return "设置" } }
    var icon: String { switch self { case .opportunities: return "rectangle.stack"; case .focus: return "star"; case .performance: return "chart.bar"; case .settings: return "gearshape" } }
}
enum K10LoadState: Equatable { case idle, loading, ready, offline(String), unavailable(String), failed(String) }

struct K10CacheContext: Hashable {
    let baseURL: URL
    let scope: String
}

@MainActor @Observable final class AppModel {
    var tab: AppTab = .opportunities
    var state: K10LoadState = .idle
    var publications: [K10Publication] = []
    var companyWindows: [K10CompanyWindow] = []
    var selectionDetails: [K10SelectionDetail] = []
    var scanSummaries: [K10Scan] = []
    var morningReport: K10MorningReport?
    var morningReportLoadError: String?
    var analysisChains: [String: K10AnalysisChain] = [:]
    var opportunityDetails: [String: K10OpportunityDetail] = [:]
    var analysisRequestInFlightWindowIDs: Set<String> = []
    var results: K10Results?
    var configuration: K10Configuration?
    var usage: K10UsageSummary?
    var providers: [K10Provider] = []
    var tavilyKeySet = false
    var selectedOpportunity: K10OpportunityDetail?
    var selectedWindow: K10CompanyWindow?
    var toast: String?
    var lastAvailableAt: String?
    var offline = false
    var notificationRegistrar: (() async -> Void)?
    private var serviceFactory: () -> (any K10Servicing)? = { nil }
    private var cacheContextFactory: () -> K10CacheContext? = { nil }
    private var cacheLoader: (K10CacheContext) -> K10CacheSnapshot? = { context in
        K10Cache.load(baseURL: context.baseURL, scope: context.scope)
    }
    private var cacheSaver: (K10CacheSnapshot, K10CacheContext) -> Void = { snapshot, context in
        K10Cache.save(snapshot, baseURL: context.baseURL, scope: context.scope)
    }
    private var cacheClearer: () -> Void = { K10Cache.clearAllK10() }
    private var adminServiceFactory: (URL, String) -> any K10AdminServicing = { baseURL, token in K10AdminClient(baseURL: baseURL, token: token) }
    private var connectionGeneration = 0
    private var refreshGeneration = 0
    private var analysisChainReloadGenerations: [String: Int] = [:]
    private var analysisRequestKeys: [String: (signature: String, key: String)] = [:]
    private weak var config: AppConfig?

    init(
        serviceFactory: @escaping () -> (any K10Servicing)? = { nil },
        cacheContextFactory: @escaping () -> K10CacheContext? = { nil },
        cacheLoader: @escaping (K10CacheContext) -> K10CacheSnapshot? = { context in K10Cache.load(baseURL: context.baseURL, scope: context.scope) },
        cacheSaver: @escaping (K10CacheSnapshot, K10CacheContext) -> Void = { snapshot, context in K10Cache.save(snapshot, baseURL: context.baseURL, scope: context.scope) },
        cacheClearer: @escaping () -> Void = { K10Cache.clearAllK10() },
        adminServiceFactory: @escaping (URL, String) -> any K10AdminServicing = { baseURL, token in K10AdminClient(baseURL: baseURL, token: token) }
    ) {
        self.serviceFactory = serviceFactory
        self.cacheContextFactory = cacheContextFactory
        self.cacheLoader = cacheLoader
        self.cacheSaver = cacheSaver
        self.cacheClearer = cacheClearer
        self.adminServiceFactory = adminServiceFactory
    }
    func bind(config: AppConfig) {
        advanceConnectionGeneration()
        self.config = config
        serviceFactory = { [weak config] in guard let config, config.hasToken else { return nil }; return K10APIClient(baseURL: config.resolvedBaseURL, token: config.apiToken) }
        cacheContextFactory = { [weak config] in
            guard let config else { return nil }
            return K10CacheContext(baseURL: config.resolvedBaseURL, scope: K10CacheScope.value())
        }
        K10Cache.clearLegacy()
    }
    func resetForConnectionChange() {
        advanceConnectionGeneration()
        publications = []; companyWindows = []; selectionDetails = []; scanSummaries = []; morningReport = nil; morningReportLoadError = nil; analysisChains = [:]; analysisChainReloadGenerations = [:]; opportunityDetails = [:]; analysisRequestInFlightWindowIDs = []; analysisRequestKeys = [:]; results = nil; configuration = nil; usage = nil; providers = []; tavilyKeySet = false
        selectedOpportunity = nil; selectedWindow = nil; lastAvailableAt = nil; offline = false; state = .idle; cacheClearer()
    }
    func refresh() async {
        let generation = connectionGeneration
        refreshGeneration &+= 1
        let refresh = refreshGeneration
        guard let service = serviceFactory() else {
            guard isCurrentRefresh(generation, refresh) else { return }
            state = .unavailable("未配置后端连接或 API Token")
            return
        }
        let cacheContext = cacheContextFactory()
        let wasOffline = offline
        state = .loading; offline = false
        do {
            try Task.checkCancellation()
            let health = try await service.health()
            try Task.checkCancellation()
            guard health.status == "ok", isV3(health.version) else { throw K10APIError.incompatibleVersion("服务端不是当前 Neckline / K10-v1.4 兼容版本，未查询选股数据") }
            guard isCurrentRefresh(generation, refresh) else { return }
            async let evening = availableScan(service, window: "evening")
            async let morning = availableScan(service, window: "morning")
            async let publicationsTask = service.publications()
            async let windowsTask = service.companyWindows()
            async let selectionsTask = service.selections()
            async let morningReportTask = service.latestMorningReport()
            async let resultTask = service.results()
            async let configurationTask = service.configuration()
            async let usageTask = service.usageSummary()
            let scans = try await [evening, morning].compactMap { $0 }
            let newPublications = try await publicationsTask
            let newWindows = try await windowsTask
            let newSelections = try await selectionsTask
            let newMorningReport: K10MorningReport?
            let newMorningReportLoadError: String?
            do {
                newMorningReport = try await morningReportTask
                newMorningReportLoadError = nil
            }
            catch is CancellationError { throw CancellationError() }
            catch let error as K10APIError {
                if case .notFound = error {
                    newMorningReport = nil
                    newMorningReportLoadError = nil
                } else {
                    newMorningReport = morningReport
                    newMorningReportLoadError = error.localizedDescription
                }
            }
            catch {
                newMorningReport = morningReport
                newMorningReportLoadError = error.localizedDescription
            }
            let newResults: K10Results?
            do { newResults = try await resultTask }
            catch is CancellationError { throw CancellationError() }
            catch { newResults = nil }
            let newConfiguration: K10Configuration?
            do { newConfiguration = try await configurationTask }
            catch is CancellationError { throw CancellationError() }
            catch { newConfiguration = nil }
            let newUsage: K10UsageSummary?
            do { newUsage = try await usageTask }
            catch is CancellationError { throw CancellationError() }
            catch { newUsage = nil }
            try Task.checkCancellation()
            guard isCurrentRefresh(generation, refresh) else { return }
            let loadedChainIDs = Set(analysisChains.keys)
            publications = newPublications.sorted { $0.availableAt > $1.availableAt }
            // The server persists publication rank.  Preserve its order verbatim; a local
            // timestamp or risk sort would silently change the published comparison.
            companyWindows = newWindows
            selectionDetails = newSelections
            morningReport = newMorningReport
            morningReportLoadError = newMorningReportLoadError
            opportunityDetails = [:]
            scanSummaries = scans
            results = newResults
            configuration = newConfiguration
            usage = newUsage
            lastAvailableAt = publications.map(\.availableAt).max()
            analysisChains = analysisChains.filter { entry in newWindows.contains { $0.companyWindowId == entry.key } }
            for windowID in loadedChainIDs where newWindows.contains(where: { $0.companyWindowId == windowID }) {
                await reloadAnalysisChain(windowID: windowID, service: service, generation: generation, refresh: refresh)
                guard isCurrentRefresh(generation, refresh) else { return }
            }
            // A healthy first run simply has no batch yet.  It remains ready so the empty
            // opportunity state and Settings connection badge describe the same service state.
            state = .ready
            if let cacheContext, let lastAvailableAt, !publications.isEmpty {
                cacheSaver(K10CacheSnapshot(availableAt: lastAvailableAt, savedAt: Date(), publications: publications, companyWindows: companyWindows, selections: selectionDetails, results: results), cacheContext)
            }
        } catch is CancellationError {
            guard isCurrentRefresh(generation, refresh) else { return }
            offline = wasOffline
            state = hasLoadedContent ? (wasOffline ? .offline("离线快照仍可只读") : .ready) : .idle
        } catch let error as K10APIError {
            guard isCurrentRefresh(generation, refresh) else { return }
            if error.permitsOfflineCache, let cacheContext, let cached = cacheLoader(cacheContext) {
                publications = cached.publications; companyWindows = cached.companyWindows; selectionDetails = cached.selections; results = cached.results; lastAvailableAt = cached.availableAt; offline = true; state = .offline("离线快照截至 \(cached.availableAt)，不能提交选择动作")
            } else { state = .failed(error.localizedDescription) }
        } catch {
            guard isCurrentRefresh(generation, refresh) else { return }
            state = .failed(error.localizedDescription)
        }
    }
    func loadOpportunity(_ opportunity: K10Opportunity) async -> K10OpportunityDetail? {
        let generation = connectionGeneration
        guard !offline, let service = serviceFactory() else {
            guard isCurrent(generation) else { return nil }
            toast = offline ? "离线快照未缓存完整机会资料。" : "服务连接不可用"
            return nil
        }
        do {
            let detail = try await service.opportunity(id: opportunity.opportunityId)
            return isCurrent(generation) ? detail : nil
        } catch is CancellationError {
            return nil
        } catch {
            guard isCurrent(generation) else { return nil }
            toast = error.localizedDescription
            return nil
        }
    }
    func open(_ opportunity: K10Opportunity) async {
        let generation = connectionGeneration
        if let detail = await loadOpportunity(opportunity), isCurrent(generation) { selectedOpportunity = detail }
    }
    func openOpportunity(id: String) async {
        let generation = connectionGeneration
        guard !offline, let service = serviceFactory() else {
            toast = offline ? "离线快照未缓存完整机会资料。" : "服务连接不可用"
            return
        }
        do {
            let detail = try await service.opportunity(id: id)
            guard isCurrent(generation) else { return }
            selectedOpportunity = detail
        } catch is CancellationError {
            return
        } catch {
            guard isCurrent(generation) else { return }
            toast = error.localizedDescription
        }
    }

    func openNotification(_ route: K10PushRoute) async {
        let generation = connectionGeneration
        // Clear any old reading context before a notification refresh.  If its target was
        // withdrawn or the refresh fails, the app must not show a previous company's details.
        selectedOpportunity = nil
        selectedWindow = nil
        tab = route.tab
        await refresh()
        guard isCurrent(generation), case .ready = state, !offline else { return }
        if let windowID = route.companyWindowID {
            guard let window = companyWindows.first(where: { $0.companyWindowId == windowID }) else {
                toast = "通知关联的公司窗口已不可用"
                return
            }
            selectedWindow = window
            return
        }
        if let opportunityID = route.opportunityID {
            guard let opportunity = companyWindows.flatMap(\.opportunities).first(where: { $0.opportunityId == opportunityID }) else {
                toast = "通知关联的机会已不可用"
                return
            }
            guard let detail = await loadOpportunity(opportunity), isCurrent(generation) else { return }
            selectedOpportunity = detail
        }
    }

    func openDocument(_ source: K10SourceReference) async -> K10DocumentPage? { guard !offline, let id = source.documentId, let service = serviceFactory() else { toast = offline ? "离线快照未缓存原文，请恢复连接后查看。" : "该来源没有可读取的原文版本。"; return nil }; do { return try await service.document(id: id, revision: source.revision, offset: 0, limit: 6000) } catch is CancellationError { return nil } catch { toast = error.localizedDescription; return nil } }
    func loadMoreDocument(_ current: K10DocumentPage) async -> K10DocumentPage? { guard !offline, let cursor = current.page.nextCursor, let offset = Int(cursor), let service = serviceFactory() else { return nil }; do { let next = try await service.document(id: current.documentId, revision: current.revision, offset: offset, limit: 6000); return K10DocumentPage(schemaVersion: current.schemaVersion, documentId: current.documentId, revision: current.revision, sourceKey: current.sourceKey, externalId: current.externalId, canonicalUrl: current.canonicalUrl, title: current.title, publishedAt: current.publishedAt, publishedPrecision: current.publishedPrecision, fetchedAt: current.fetchedAt, excerpt: current.excerpt, body: (current.body ?? "") + (next.body ?? ""), page: next.page) } catch is CancellationError { return nil } catch { toast = error.localizedDescription; return nil } }
    func act(_ action: String, window: K10CompanyWindow) async { guard case .ready = state else { toast = "连接切换后请先刷新，不能提交旧上下文动作"; return }; guard !offline, let service = serviceFactory() else { toast = "离线快照不能提交选择"; return }; do { _ = try await service.act(companyWindowID: window.companyWindowId, request: K10SelectionRequest(action: action, idempotencyKey: UUID().uuidString, reason: nil)); await refresh() } catch is CancellationError { return } catch { toast = error.localizedDescription } }
    func retryAnalysis(for detail: K10SelectionDetail) async { guard let job = detail.latestJob else { return }; await retryAnalysis(job: job, companyWindowID: detail.companyWindowId) }
    func retryAnalysis(job: K10Job, companyWindowID: String) async {
        let generation = connectionGeneration
        guard case .ready = state, !offline, ["failed", "not_configured"].contains(job.status), let service = serviceFactory() else { return }
        do {
            _ = try await service.retryJob(id: job.jobId, expectedAttemptCount: job.attemptCount)
            guard isCurrent(generation) else { return }
            await refresh()
            guard isCurrent(generation) else { return }
            await loadAnalysisChain(companyWindowID: companyWindowID)
        } catch is CancellationError {
            return
        } catch {
            guard isCurrent(generation) else { return }
            toast = error.localizedDescription
        }
    }
    func loadAnalysisChain(for window: K10CompanyWindow) async {
        await loadAnalysisChain(companyWindowID: window.companyWindowId)
    }
    func loadAnalysisChain(companyWindowID: String) async {
        let generation = connectionGeneration
        guard !offline, let service = serviceFactory() else { return }
        await reloadAnalysisChain(windowID: companyWindowID, service: service, generation: generation)
    }
    func loadSupplementarySources(for window: K10CompanyWindow) async {
        let generation = connectionGeneration
        guard !offline, let service = serviceFactory() else { return }
        for opportunity in window.opportunities where opportunityDetails[opportunity.opportunityId] == nil {
            do {
                let detail = try await service.opportunity(id: opportunity.opportunityId)
                guard isCurrent(generation) else { return }
                opportunityDetails[opportunity.opportunityId] = detail
            } catch is CancellationError {
                return
            } catch {
                guard isCurrent(generation) else { return }
                toast = error.localizedDescription; return
            }
        }
    }
    func requestAnalysis(kind: String, question: String, sourceRefs: [K10AnalysisDocumentReference], for window: K10CompanyWindow) async {
        let generation = connectionGeneration
        guard case .ready = state, !offline, let service = serviceFactory() else { toast = offline ? "离线快照不能提交补充分析" : "服务连接不可用"; return }
        guard !analysisRequestInFlightWindowIDs.contains(window.companyWindowId) else { return }
        let trimmed = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard kind != "user_question" || !trimmed.isEmpty else { toast = "请输入要追问的问题"; return }
        guard kind != "evidence_update" || !sourceRefs.isEmpty else { toast = "补充资料需要至少一条已保存的资料版本"; return }
        let signature = "\(kind)|\(trimmed)|\(sourceRefs.map { "\($0.documentId)#\($0.revision)" }.sorted().joined(separator: ","))"
        let idempotencyKey: String
        if let saved = analysisRequestKeys[window.companyWindowId], saved.signature == signature { idempotencyKey = saved.key }
        else { idempotencyKey = UUID().uuidString; analysisRequestKeys[window.companyWindowId] = (signature, idempotencyKey) }
        analysisRequestInFlightWindowIDs.insert(window.companyWindowId)
        defer { if isCurrent(generation) { analysisRequestInFlightWindowIDs.remove(window.companyWindowId) } }
        do {
            _ = try await service.requestAnalysis(companyWindowID: window.companyWindowId, request: K10AnalysisRequest(kind: kind, question: trimmed.isEmpty ? nil : trimmed, sourceRefs: sourceRefs, idempotencyKey: idempotencyKey))
            guard isCurrent(generation) else { return }
            analysisRequestKeys[window.companyWindowId] = nil
            await refresh()
            guard isCurrent(generation) else { return }
            await loadAnalysisChain(for: window)
            guard isCurrent(generation) else { return }
            toast = "补充分析已提交，正在按冻结资料生成新版本"
        } catch is CancellationError {
            return
        } catch {
            guard isCurrent(generation) else { return }
            toast = error.localizedDescription
        }
    }
    func selection(for window: K10CompanyWindow) -> K10SelectionDetail? { selectionDetails.first { $0.companyWindowId == window.companyWindowId } }
    func detail(for window: K10CompanyWindow) -> K10SelectionDetail? { selection(for: window).flatMap { $0.state == "kept" ? $0 : nil } }
    func refreshAdminSettings() async {
        let generation = connectionGeneration
        guard let config, config.hasToken else { return }
        let client = adminServiceFactory(config.resolvedBaseURL, config.apiToken)
        do {
            async let currentProviders = client.providers()
            async let tavilyStatus = client.tavilyStatus()
            let (newProviders, newTavilyStatus) = try await (currentProviders, tavilyStatus)
            guard isCurrent(generation) else { return }
            providers = newProviders
            tavilyKeySet = newTavilyStatus.keySet
        } catch {
            guard isCurrent(generation) else { return }
            toast = error.localizedDescription
        }
    }
    func saveDeepSeekConnection(name: String, apiKey: String, enabled: Bool) async {
        let generation = connectionGeneration
        guard let config, config.hasToken else { toast = "请先配置 API Token"; return }
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { toast = "连接名称不能为空"; return }
        let client = adminServiceFactory(config.resolvedBaseURL, config.apiToken)
        let existingProvider = providers.contains(where: { $0.name == name })
        let key = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let update = K10ProviderUpdate(baseUrl: "https://api.deepseek.com/v1/chat/completions", model: "deepseek-v4-pro", apiKey: key.isEmpty ? nil : key, hasWebSearch: false, searchEngine: nil, notes: "K10-v1.4", enabled: enabled)
        do {
            if existingProvider { _ = try await client.updateProvider(name: name, update) }
            else { _ = try await client.createProvider(K10ProviderCreate(name: name, baseUrl: "https://api.deepseek.com/v1/chat/completions", model: "deepseek-v4-pro", apiKey: key.isEmpty ? nil : key, hasWebSearch: false, searchEngine: nil, notes: "K10-v1.4", enabled: enabled)) }
            guard isCurrent(generation) else { return }
            await refreshAdminSettings()
            guard isCurrent(generation) else { return }
            toast = "连接已保存；密钥不会回显"
        } catch {
            guard isCurrent(generation) else { return }
            toast = error.localizedDescription
        }
    }
    func setTavilyKey(_ key: String) async {
        let generation = connectionGeneration
        guard let config, config.hasToken else { toast = "请先配置 API Token"; return }
        let key = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty else { toast = "请输入要写入的 Tavily Key"; return }
        let client = adminServiceFactory(config.resolvedBaseURL, config.apiToken)
        do {
            let status = try await client.setTavilyKey(key)
            guard isCurrent(generation) else { return }
            tavilyKeySet = status.keySet
            toast = "Tavily Key 已写入服务器，不会回显"
        } catch {
            guard isCurrent(generation) else { return }
            toast = error.localizedDescription
        }
    }
    func clearTavilyKey() async {
        let generation = connectionGeneration
        guard let config, config.hasToken else { toast = "请先配置 API Token"; return }
        let client = adminServiceFactory(config.resolvedBaseURL, config.apiToken)
        do {
            try await client.clearTavilyKey()
            guard isCurrent(generation) else { return }
            tavilyKeySet = false
            toast = "Tavily Key 已从服务器清除"
        } catch {
            guard isCurrent(generation) else { return }
            toast = error.localizedDescription
        }
    }
    func enableNotifications() async { guard let notificationRegistrar else { toast = "此设备不支持远程推送注册"; return }; await notificationRegistrar() }
    private func availableScan(_ service: any K10Servicing, window: String) async throws -> K10Scan? { do { return try await service.latestScan(window: window) } catch let error as K10APIError { if case .notFound = error { return nil }; throw error } }
    private func reloadAnalysisChain(windowID: String, service: any K10Servicing, generation: Int, refresh: Int? = nil) async {
        analysisChainReloadGenerations[windowID, default: 0] &+= 1
        let reload = analysisChainReloadGenerations[windowID]!
        do {
            let chain = try await service.analysisChain(companyWindowID: windowID)
            guard isCurrentChainReload(windowID, reload), isCurrent(generation), refresh.map({ isCurrentRefresh(generation, $0) }) ?? true else { return }
            analysisChains[windowID] = chain
        } catch is CancellationError {
            return
        } catch let error as K10APIError {
            guard isCurrentChainReload(windowID, reload), isCurrent(generation), refresh.map({ isCurrentRefresh(generation, $0) }) ?? true else { return }
            if case .notFound = error { analysisChains[windowID] = nil } else { toast = error.localizedDescription }
        } catch {
            guard isCurrentChainReload(windowID, reload), isCurrent(generation), refresh.map({ isCurrentRefresh(generation, $0) }) ?? true else { return }
            toast = error.localizedDescription
        }
    }
    private func advanceConnectionGeneration() { connectionGeneration &+= 1 }
    private func isCurrent(_ generation: Int) -> Bool { generation == connectionGeneration }
    private func isCurrentRefresh(_ connection: Int, _ refresh: Int) -> Bool { isCurrent(connection) && refresh == refreshGeneration }
    private func isCurrentChainReload(_ windowID: String, _ reload: Int) -> Bool { analysisChainReloadGenerations[windowID] == reload }
    private var hasLoadedContent: Bool { !publications.isEmpty || !companyWindows.isEmpty || results != nil || morningReport != nil }
    private func isV3(_ version: String?) -> Bool { guard let version else { return false }; return version.lowercased().replacingOccurrences(of: "v", with: "").split(separator: ".").first == "3" }
}
