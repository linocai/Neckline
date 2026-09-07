import Foundation
import Observation

enum AppTab: String, CaseIterable, Identifiable {
    case opportunities, focus, performance, settings
    var id: String { rawValue }
    var title: String { switch self { case .opportunities: return "机会"; case .focus: return "关注"; case .performance: return "选股表现"; case .settings: return "设置" } }
    var icon: String { switch self { case .opportunities: return "rectangle.stack"; case .focus: return "star"; case .performance: return "chart.bar"; case .settings: return "gearshape" } }
}
enum K10LoadState: Equatable { case idle, loading, ready, offline(String), unavailable(String), failed(String) }

@MainActor @Observable final class AppModel {
    var tab: AppTab = .opportunities
    var state: K10LoadState = .idle
    var publications: [K10Publication] = []
    var companyWindows: [K10CompanyWindow] = []
    var selectionDetails: [K10SelectionDetail] = []
    var scanSummaries: [K10Scan] = []
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
    private weak var config: AppConfig?

    init(serviceFactory: @escaping () -> (any K10Servicing)? = { nil }) { self.serviceFactory = serviceFactory }
    func bind(config: AppConfig) {
        self.config = config
        serviceFactory = { [weak config] in guard let config, config.hasToken else { return nil }; return K10APIClient(baseURL: config.resolvedBaseURL, token: config.apiToken) }
        K10Cache.clearLegacy()
    }
    func resetForConnectionChange() {
        publications = []; companyWindows = []; selectionDetails = []; scanSummaries = []; results = nil; configuration = nil; usage = nil
        selectedOpportunity = nil; selectedWindow = nil; lastAvailableAt = nil; offline = false; state = .idle; K10Cache.clearAllK10()
    }
    func refresh() async {
        guard let service = serviceFactory() else { state = .unavailable("未配置后端连接或 API Token"); return }
        state = .loading; offline = false
        do {
            let health = try await service.health()
            guard health.status == "ok", isV3(health.version) else { throw K10APIError.incompatibleVersion("服务端不是 Neckline 3.0.0 / K10-v1.4 兼容版本，未查询选股数据") }
            async let evening = availableScan(service, window: "evening")
            async let morning = availableScan(service, window: "morning")
            async let publicationsTask = service.publications()
            async let windowsTask = service.companyWindows()
            async let selectionsTask = service.selections()
            async let resultTask = service.results()
            async let configurationTask = service.configuration()
            async let usageTask = service.usageSummary()
            let scans = try await [evening, morning].compactMap { $0 }
            let newPublications = try await publicationsTask
            let newWindows = try await windowsTask
            let newSelections = try await selectionsTask
            publications = newPublications.sorted { $0.availableAt > $1.availableAt }
            companyWindows = newWindows.sorted { $0.createdAt > $1.createdAt }
            selectionDetails = newSelections
            scanSummaries = scans
            results = try? await resultTask
            configuration = try? await configurationTask
            usage = try? await usageTask
            lastAvailableAt = publications.map(\.availableAt).max()
            let unavailable = scans.isEmpty && publications.isEmpty && companyWindows.isEmpty
            state = unavailable ? .unavailable("尚无可查看的 K10-v1.4 发布批次；来源与设置状态仍可读取。") : .ready
            if let config, let lastAvailableAt, !publications.isEmpty { K10Cache.save(K10CacheSnapshot(availableAt: lastAvailableAt, savedAt: Date(), publications: publications, companyWindows: companyWindows, selections: selectionDetails, results: results), baseURL: config.resolvedBaseURL, scope: K10CacheScope.value()) }
        } catch let error as K10APIError {
            if error.permitsOfflineCache, let config, let cached = K10Cache.load(baseURL: config.resolvedBaseURL, scope: K10CacheScope.value()) {
                publications = cached.publications; companyWindows = cached.companyWindows; selectionDetails = cached.selections; results = cached.results; lastAvailableAt = cached.availableAt; offline = true; state = .offline("离线快照截至 \(cached.availableAt)，不能提交选择动作")
            } else { state = .failed(error.localizedDescription) }
        } catch { state = .failed(error.localizedDescription) }
    }
    func loadOpportunity(_ opportunity: K10Opportunity) async -> K10OpportunityDetail? {
        guard !offline, let service = serviceFactory() else {
            toast = offline ? "离线快照未缓存完整机会资料。" : "服务连接不可用"
            return nil
        }
        do { return try await service.opportunity(id: opportunity.opportunityId) }
        catch { toast = error.localizedDescription; return nil }
    }
    func open(_ opportunity: K10Opportunity) async {
        if let detail = await loadOpportunity(opportunity) { selectedOpportunity = detail }
    }

    func openDocument(_ source: K10SourceReference) async -> K10DocumentPage? { guard !offline, let id = source.documentId, let service = serviceFactory() else { toast = offline ? "离线快照未缓存原文，请恢复连接后查看。" : "该来源没有可读取的原文版本。"; return nil }; do { return try await service.document(id: id, revision: source.revision, offset: 0, limit: 6000) } catch { toast = error.localizedDescription; return nil } }
    func loadMoreDocument(_ current: K10DocumentPage) async -> K10DocumentPage? { guard !offline, let cursor = current.page.nextCursor, let offset = Int(cursor), let service = serviceFactory() else { return nil }; do { let next = try await service.document(id: current.documentId, revision: current.revision, offset: offset, limit: 6000); return K10DocumentPage(schemaVersion: current.schemaVersion, documentId: current.documentId, revision: current.revision, sourceKey: current.sourceKey, externalId: current.externalId, canonicalUrl: current.canonicalUrl, title: current.title, publishedAt: current.publishedAt, publishedPrecision: current.publishedPrecision, fetchedAt: current.fetchedAt, excerpt: current.excerpt, body: (current.body ?? "") + (next.body ?? ""), page: next.page) } catch { toast = error.localizedDescription; return nil } }
    func act(_ action: String, window: K10CompanyWindow) async { guard case .ready = state else { toast = "连接切换后请先刷新，不能提交旧上下文动作"; return }; guard !offline, let service = serviceFactory() else { toast = "离线快照不能提交选择"; return }; do { _ = try await service.act(companyWindowID: window.companyWindowId, request: K10SelectionRequest(action: action, idempotencyKey: UUID().uuidString, reason: nil)); await refresh() } catch { toast = error.localizedDescription } }
    func retryAnalysis(for detail: K10SelectionDetail) async { guard case .ready = state, !offline, let job = detail.latestJob, ["failed", "not_configured"].contains(job.status), let service = serviceFactory() else { return }; do { _ = try await service.retryJob(id: job.jobId, expectedAttemptCount: job.attemptCount); await refresh() } catch { toast = error.localizedDescription } }
    func selection(for window: K10CompanyWindow) -> K10SelectionDetail? { selectionDetails.first { $0.companyWindowId == window.companyWindowId } }
    func detail(for window: K10CompanyWindow) -> K10SelectionDetail? { selection(for: window).flatMap { $0.state == "kept" ? $0 : nil } }
    func refreshAdminSettings() async { guard let config, config.hasToken else { return }; let client = K10AdminClient(baseURL: config.resolvedBaseURL, token: config.apiToken); do { providers = try await client.providers(); tavilyKeySet = try await client.tavilyStatus().keySet } catch { toast = error.localizedDescription } }
    func saveDeepSeekConnection(name: String, apiKey: String, enabled: Bool) async { guard let config, config.hasToken else { toast = "请先配置 API Token"; return }; let name = name.trimmingCharacters(in: .whitespacesAndNewlines); guard !name.isEmpty else { toast = "连接名称不能为空"; return }; let client = K10AdminClient(baseURL: config.resolvedBaseURL, token: config.apiToken); let key = apiKey.trimmingCharacters(in: .whitespacesAndNewlines); do { if providers.contains(where: { $0.name == name }) { _ = try await client.updateProvider(name: name, K10ProviderUpdate(baseUrl: "https://api.deepseek.com/v1/chat/completions", model: "deepseek-v4-pro", apiKey: key.isEmpty ? nil : key, hasWebSearch: false, searchEngine: nil, notes: "K10-v1.4", enabled: enabled)) } else { _ = try await client.createProvider(K10ProviderCreate(name: name, baseUrl: "https://api.deepseek.com/v1/chat/completions", model: "deepseek-v4-pro", apiKey: key.isEmpty ? nil : key, hasWebSearch: false, searchEngine: nil, notes: "K10-v1.4", enabled: enabled)) }; await refreshAdminSettings(); toast = "连接已保存；密钥不会回显" } catch { toast = error.localizedDescription } }
    func setTavilyKey(_ key: String) async { guard let config, config.hasToken else { toast = "请先配置 API Token"; return }; let key = key.trimmingCharacters(in: .whitespacesAndNewlines); guard !key.isEmpty else { toast = "请输入要写入的 Tavily Key"; return }; do { tavilyKeySet = try await K10AdminClient(baseURL: config.resolvedBaseURL, token: config.apiToken).setTavilyKey(key).keySet; toast = "Tavily Key 已写入服务器，不会回显" } catch { toast = error.localizedDescription } }
    func clearTavilyKey() async { guard let config, config.hasToken else { toast = "请先配置 API Token"; return }; do { try await K10AdminClient(baseURL: config.resolvedBaseURL, token: config.apiToken).clearTavilyKey(); tavilyKeySet = false; toast = "Tavily Key 已从服务器清除" } catch { toast = error.localizedDescription } }
    func enableNotifications() async { guard let notificationRegistrar else { toast = "此设备不支持远程推送注册"; return }; await notificationRegistrar() }
    private func availableScan(_ service: any K10Servicing, window: String) async throws -> K10Scan? { do { return try await service.latestScan(window: window) } catch let error as K10APIError { if case .notFound = error { return nil }; throw error } }
    private func isV3(_ version: String?) -> Bool { guard let version else { return false }; return version.lowercased().replacingOccurrences(of: "v", with: "").split(separator: ".").first == "3" }
}
