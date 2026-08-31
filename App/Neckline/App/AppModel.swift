//
//  AppModel.swift
//  Neckline — 应用状态(@Observable)。
//
//  🔴 **信息架构 = 三板块 选股 / 成绩 / 复盘 + 设置沉底**(**裁定 11**,⛔ 施工时不得重开)。
//  设置**在产品语义上不算板块** —— 它只是个入口,排最后、齿轮图标。
//
//  `NECKLINE_INITIAL_TAB` 由本工程消费；当前 rawValue 是 QA 启动契约，不可随意改动。
//

import Foundation
import Observation

/// 三板块 + 设置沉底。**枚举顺序即 iOS TabBar 顺序。**
///
/// 🔴 三个板块各答一个不同的问题,⛔ 不合并:
///   · **选股** —— 今天该细看哪几只 / 明早哪几只已经可以划掉;
///   · **成绩** —— 这套系统做得怎么样(清单成绩 + 覆盖率);
///   · **复盘** —— 我实际做得怎么样(交割单 → 装订 → 结论 → 我的成绩)。
enum AppTab: String, CaseIterable, Identifiable {
    case selection, scoreboard, review, settings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .selection: return "选股"
        case .scoreboard: return "成绩"
        case .review: return "复盘"
        case .settings: return "设置"
        }
    }

    var systemImage: String {
        switch self {
        case .selection: return "list.bullet.rectangle"
        case .scoreboard: return "chart.bar.xaxis"
        case .review: return "doc.text.magnifyingglass"
        case .settings: return "gearshape"
        }
    }
}

/// 选股板块的两个视图(**裁定 11**:次日核对表留在选股板块内作第二视图)。
///
/// 🔴 **9:26–15:00 默认落在核对表,其余时间落在清单**,用户随时可切
/// —— 早盘那段时间人要看的是「昨晚那批票今早哪几只已经死了」,而不是昨晚的清单。
/// ⚠ `rawValue` 是 `NECKLINE_INITIAL_SELECTION_VIEW` QA 钩子的参数。
enum SelectionViewMode: String, CaseIterable, Identifiable {
    case listing, checklist

    var id: String { rawValue }

    var title: String {
        switch self {
        case .listing: return "今日清单"
        case .checklist: return "次日核对表"
        }
    }

    var systemImage: String {
        switch self {
        case .listing: return "list.number"
        case .checklist: return "checklist"
        }
    }
}

/// 复盘板块的四页(架构 §六 三件事 + 我的成绩)。**每页答一个不同的问题,⛔ 不合并。**
/// ⚠ `rawValue` 是 `NECKLINE_INITIAL_REVIEW_PAGE` QA 钩子的参数。
enum ReviewPage: String, CaseIterable, Identifiable {
    case reconcile, bindery, conclusions, mine

    var id: String { rawValue }

    var title: String {
        switch self {
        case .reconcile: return "交割单"
        case .bindery: return "装订材料"
        case .conclusions: return "结论存档"
        case .mine: return "我的成绩"
        }
    }

    /// 🔴 **「这一页答什么」**。⛔ 别改写成对仗好听但讲不清的句子。
    var question: String {
        switch self {
        case .reconcile: return "这周我实际成交了哪些(上传交割单 → 解析结果)"
        case .bindery: return "那几笔当时长什么样(K 线 + 买卖点 + 大盘 + 行业 + 当时的报告与预案)"
        case .conclusions: return "这周复盘得出了什么(结论存档,下周可检索)"
        case .mine: return "我自己的成绩如何(⚠ 与系统的清单成绩**完全隔离**)"
        }
    }

    var systemImage: String {
        switch self {
        case .reconcile: return "tray.and.arrow.down"
        case .bindery: return "chart.xyaxis.line"
        case .conclusions: return "text.book.closed"
        case .mine: return "person.crop.circle.badge.checkmark"
        }
    }
}

/// Provider 编辑草稿(设置屏增删改)。⚠ **`apiKey` 只写不回显**:草稿里的 key 只在
/// 本次填写期间存在,提交后立即清空;服务端永远只回 `keySet` 布尔。
struct ProviderForm {
    /// `nil` = 新建;非 nil = 编辑该 provider(`name` 是主键,编辑时不可改)。
    var editingName: String? = nil
    var name = ""
    var baseUrl = ""
    var model = ""
    var apiKey = ""
    var hasWebSearch = false
    var searchEngine = ""
    var notes = ""
    var enabled = true

    var isEditing: Bool { editingName != nil }
    var isValid: Bool {
        !name.trimmingCharacters(in: .whitespaces).isEmpty
            && !baseUrl.trimmingCharacters(in: .whitespaces).isEmpty
            && !model.trimmingCharacters(in: .whitespaces).isEmpty
    }

    static func editing(_ p: Provider) -> ProviderForm {
        ProviderForm(editingName: p.name, name: p.name, baseUrl: p.baseUrl, model: p.model,
                     apiKey: "", hasWebSearch: p.hasWebSearch,
                     searchEngine: p.searchEngine ?? "", notes: p.notes ?? "", enabled: p.enabled)
    }
}

/// 结论存档的录入草稿(**append-only**:提交一次 = 新版本)。
struct ConclusionForm {
    var week = ""
    var title = ""
    var body = ""
    var tagsText = ""
    var submitting = false

    var tags: [String] {
        tagsText.split(whereSeparator: { $0 == "," || $0 == "," || $0 == " " })
            .map { String($0).trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    var isValid: Bool {
        !week.trimmingCharacters(in: .whitespaces).isEmpty
            && !title.trimmingCharacters(in: .whitespaces).isEmpty
            && !body.trimmingCharacters(in: .whitespaces).isEmpty
    }
}

struct Toast: Identifiable, Equatable {
    let id = UUID()
    let message: String
    var isError: Bool = false
}

/// 10:00 结算刷新只是一条客户端读策略：不生成报告、不触发推送，也不改服务端状态。
/// 时间统一按上海时区，避免设备时区变化后错过 A 股结算窗口。
@MainActor
@Observable
final class AppModel {
    // —— 导航(三板块 + 设置沉底)——
    var view: AppTab = .selection
    /// 选股板块当前视图。**默认值由钟点决定**(见 `defaultSelectionMode`)。
    var selectionMode: SelectionViewMode = AppModel.defaultSelectionMode()
    /// 复盘板块当前页。
    var reviewPage: ReviewPage = .reconcile

    // ══════════════════════════════════════════════════════════════════════
    // 选股
    // ══════════════════════════════════════════════════════════════════════

    /// 当日报告(三态 + 双日期 + 清单 + 逐只摘要)。
    var selection: SelectionSnapshot = .notLoaded
    var selectionLoading = false
    /// `nil` = 本次还没成功拉过 / 拉失败(界面如实说「没取到」,⛔ 不冒充三态里的任何一态)。
    var selectionLoaded = false
    /// 在线响应以外的最近一份成功报告。只在网络不可达时使用，鉴权错误绝不回退。
    var selectionOffline = false
    var selectionCachedAt: Date? = nil

    /// 次日核对表。**三态各自独立,⛔ 别压成一个 `try?`**:
    ///   · `checklist != nil`            = 那一拍跑过了;
    ///   · `checklist == nil && checklistMissing != nil` = **那天没跑过那一拍**(404,合法空态);
    ///   · 两者都是 nil                  = 本次没去拉 / 网络没通。
    var checklist: Checklist? = nil
    var checklistMissing: String? = nil
    var checklistLoading = false

    /// K9-v3 个股与预案只从不可变成绩包读取。
    var selectedScorePackage: ScoreboardPackageDetail? = nil
    var selectedScorePackageID: String? = nil
    var selectedScorePackageLoading = false
    var selectedScorePackageError: String? = nil

    /// **本机上一次成功刷新的时刻**。⚠ 这是**客户端**的钟,回答「我上次去问是什么时候」
    /// —— ⛔ **不是** `selection.generatedAt`(那是服务端出报告的时刻,两者可以差好几个
    /// 小时:19:00 启动生成的报告,你 21:00 才打开)。两个时刻**刻意不合并**。
    var lastRefreshedAt: Date? = nil

    // ══════════════════════════════════════════════════════════════════════
    // 成绩
    // ══════════════════════════════════════════════════════════════════════

    var activeScorePackages: [ScoreboardPackage] = []
    var settledScorePackages: [ScoreboardPackage] = []
    var scoreboardLoading = false

    // ══════════════════════════════════════════════════════════════════════
    // 复盘
    // ══════════════════════════════════════════════════════════════════════

    var reviewWeeks: [WeeklyReviewEntry] = []
    var reviewSelectedWeek: String? = nil
    var reviewParseWarnings: [String] = []
    var reviewDataWarnings: [String] = []
    var reviewUploading = false
    var reviewOverview: ReviewOverview? = nil
    var reviewOverviewLoading = false
    /// 累计页看的是哪一周(`YYYYMMDD`,该周任意一天;`nil` = 本周)。
    /// ⚠ **必须能翻周**:交割单是周末才传的,周一到周五看"本周"永远是空。
    var reviewWeekAnchor: String? = nil

    var bindery: ReviewBindery? = nil
    var binderyLoading = false
    var binderyError: String? = nil

    var conclusions: ReviewConclusionsResponse = .empty
    var conclusionsLoading = false
    var conclusionForm = ConclusionForm()
    var showConclusionEditor = false

    // —— 设置 ——
    var settings: SettingsSnapshot = .empty
    var settingsLoading = false
    var providers: [Provider] = []
    var llmRoutes: LLMRoutes = LLMRoutes()
    var usageSummary: UsageSummary = .empty
    /// Tavily key 草稿只存在于本次输入期间;成功或失败后都立即清空。
    var tavilyKeyDraft = ""
    var providerForm = ProviderForm()
    var showProviderForm = false
    /// 推送开关草稿:**服务端发什么就渲染什么**(⛔ 不硬编 kind 清单)。
    var pushKindsDraft: [PushKind] = []
    var serverVersion: String? = nil

    /// 板块级拉取失败的一句话(展示在内容区,不弹层)。
    var loadError: String? = nil
    /// 一次性提示条(操作反馈)。
    var toast: Toast? = nil

    // —— 依赖(运行期注入)——
    let calendar = StaticTradingCalendar.shared
    private var clientProvider: () -> APIClient?
    private var snapshotStoreProvider: () -> ReportSnapshotStore? = { nil }
    #if os(iOS)
    weak var pushManager: PushManager? = nil
    #endif

    init(clientProvider: @escaping () -> APIClient? = { nil }) {
        self.clientProvider = clientProvider
    }

    /// 用 config 绑定后端连接(随 config 实时取值)。幂等,可重复调。
    func bind(config: AppConfig) {
        self.clientProvider = { [weak config] in
            guard let c = config, c.hasToken else { return nil }
            return APIClient(baseURL: c.resolvedBaseURL, token: c.apiToken)
        }
        self.snapshotStoreProvider = { [weak config] in
            guard let c = config, c.hasToken else { return nil }
            return ReportSnapshotStore(baseURL: c.resolvedBaseURL, token: c.apiToken)
        }
    }

    func clearReportSnapshotCache() {
        ReportSnapshotStore.clearAll()
        selectionOffline = false
        selectionCachedAt = nil
    }

    // MARK: - 派生(纯逻辑,单测覆盖)

    /// 🔴 **9:26–15:00 默认落在核对表,其余时间落在清单**(裁定 11 / §5.11)。
    /// **纯钟点判定,⛔ 不判交易日**:非交易日核对表本来就没有那一行,
    /// 视图自己会说「那天没跑过那一拍」——在这里再判一次交易日等于把同一件事讲两遍,
    /// 而两遍总有一天会对不上。
    static func defaultSelectionMode(now: Date = Date(),
                                     calendar: Calendar = .current) -> SelectionViewMode {
        let c = calendar.dateComponents([.hour, .minute], from: now)
        let minutes = (c.hour ?? 0) * 60 + (c.minute ?? 0)
        return (minutes >= 9 * 60 + 26 && minutes < 15 * 60) ? .checklist : .listing
    }

    /// 三态是否**有清单**。⚠ `empty`(今天没有)与 `notRun`(今天没跑成)都返 false,
    /// 但它们**不是同一件事** —— 判断「要不要画列表」用这个,判断「说什么话」看 `state`。
    var hasListing: Bool { selection.state == .hasList && !selection.stocks.isEmpty }

    // MARK: - 刷新(板块级)

    private var loadedBoards: Set<AppTab> = []

    /// 首次进入某板块时加载(已加载过则跳过)。
    func ensureLoaded(_ tab: AppTab) async {
        guard !loadedBoards.contains(tab) else { return }
        loadedBoards.insert(tab)
        await refresh(for: tab)
    }

    /// 工具栏 / 下拉刷新走这条(**不受 `loadedBoards` 门控**,用户主动要求就真的去拉)。
    func refresh(for tab: AppTab) async {
        switch tab {
        case .selection: await refreshSelection()
        case .scoreboard: await refreshScoreboard()
        case .review: await refreshReview()
        case .settings: await refreshSettings()
        }
    }

    var isLoadingCurrentTab: Bool {
        switch view {
        case .selection: return selectionLoading || checklistLoading
        case .scoreboard: return scoreboardLoading || selectedScorePackageLoading
        case .review: return reviewOverviewLoading || reviewUploading
        case .settings: return settingsLoading
        }
    }

    /// 选股板块:报告 + 次日核对表(两路并发)。
    func refreshSelection() async {
        // 两条读取路径都必须在本轮刷新中收口；否则 `not_run` 这类没有成绩包的
        // 正常报告会把核对表空态永远留在 ProgressView。
        selectionLoading = true
        checklistLoading = true
        guard let client = clientProvider() else {
            loadError = "未配置后端连接"
            selectionLoading = false
            checklistLoading = false
            return
        }
        loadError = nil
        let snapshot = await fetchResult { try await client.fetchSelectionLatest() }
        switch snapshot {
        case .success(let s):
            selection = s
            selectionLoaded = true
            selectionOffline = false
            selectionCachedAt = nil
            // `not_run` 是服务端当前的明确结论，不以旧快照遮盖；也不把它覆盖到
            // 最近一份可离线阅读的完整报告上。
            if s.state != .notRun { try? snapshotStoreProvider()?.save(s) }
        case .failure(let e):
            let cached = snapshotStoreProvider()?.latest()
            if Self.shouldDisplayOfflineSelectionSnapshot(for: e, hasCachedSnapshot: cached != nil),
               let cached {
                selection = cached.snapshot
                selectionLoaded = true
                selectionOffline = true
                selectionCachedAt = cached.savedAt
                loadError = nil
            } else {
                selectionOffline = false
                handleLoadFailure(e, context: "报告")
            }
        }
        selectionLoading = false
        // 🔴 **核对表核的是 D1** —— 它按**今天**取,而报告的 `tradeDate` 是 D0。
        // 两者刻意用不同的日期:昨晚的清单 + 今早的核对,本来就是两天的事。
        if let batchId = selection.structured.objectValue?["batchIds"]?.arrayValue?.first?.stringValue {
            await loadChecklist(batchId: batchId)
        } else {
            checklist = nil
            checklistMissing = "当前报告没有可核对的 K9-v3 成绩包"
            checklistLoading = false
        }
        if loadError == nil { lastRefreshedAt = Date() }
        applyQAHooksAfterRefresh()
    }

    /// 这是刻意窄的状态机入口，便于测试也避免未来某个笼统的 `catch` 扩大离线回退。
    static func mayUseOfflineSelectionSnapshot(for error: Error) -> Bool {
        (error as? APIError)?.permitsOfflineSelectionSnapshot == true
    }

    static func shouldDisplayOfflineSelectionSnapshot(
        for error: Error, hasCachedSnapshot: Bool,
    ) -> Bool {
        hasCachedSnapshot && mayUseOfflineSelectionSnapshot(for: error)
    }

    /// 次日核对表。**404 是常态**(一天里只有 9:26 之后才有,且要 D0 真出过清单)——
    /// ⛔ 不弹错、⛔ 不画一张空表,如实说「那天没跑过那一拍」。
    func loadChecklist(batchId: String) async {
        guard let client = clientProvider() else {
            checklistLoading = false
            return
        }
        checklistLoading = true
        defer { checklistLoading = false }
        do {
            checklist = try await client.fetchChecklist(batchId: batchId)
            checklistMissing = nil
        } catch let e as APIError where e.isNotFound {
            checklist = nil
            checklistMissing = e.errorDescription ?? "该成绩包尚无次日核对表"
        } catch {
            checklist = nil
            checklistMissing = nil   // ⛔ 网络没通 ≠ 那天没跑过,两者不许合并
        }
    }

    /// 成绩板块只读 K9-v3 的独立包历史；不把不同包聚合成总分。
    func refreshScoreboard() async {
        guard let client = clientProvider() else {
            loadError = "未配置后端连接"
            return
        }
        scoreboardLoading = true
        loadError = nil
        async let activePackagesTask: Result<ScoreboardPackagesResponse, Error> =
            fetchResult { try await client.fetchScoreboardPackages(state: "active") }
        async let settledPackagesTask: Result<ScoreboardPackagesResponse, Error> =
            fetchResult { try await client.fetchScoreboardPackages(state: "settled") }
        let (activePackages, settledPackages) = await (activePackagesTask, settledPackagesTask)
        if case .success(let response) = activePackages { activeScorePackages = response.packages }
        else if case .failure(let error) = activePackages { handleLoadFailure(error, context: "进行中成绩包") }
        if case .success(let response) = settledPackages { settledScorePackages = response.packages }
        else if case .failure(let error) = settledPackages { handleLoadFailure(error, context: "已结算历史") }
        scoreboardLoading = false
        if loadError == nil { lastRefreshedAt = Date() }
    }

    /// 复盘板块:聚合读(对账段 + 结论段)。⚠ 装订材料**点一下才算**,不在这里拉。
    func refreshReview() async {
        await loadReviewOverview()
        await loadConclusions()
    }

    /// 设置板块:薄壳,复用既有 `loadSettings()`。
    func refreshSettings() async {
        await loadSettings()
    }

    /// 纯 QA / 截图辅助。⚠ **必须放在数据到位之后** —— `NecklineApp.init()` 里的同步钩子
    /// 够不着这些内容(数据是异步拉的)。缺环境变量则不触发,不影响正常用户路径。
    private func applyQAHooksAfterRefresh() {
        let env = ProcessInfo.processInfo.environment
        if let raw = env["NECKLINE_INITIAL_SELECTION_VIEW"],
           let mode = SelectionViewMode(rawValue: raw) {
            selectionMode = mode
        }
        if let raw = env["NECKLINE_INITIAL_REVIEW_PAGE"], let page = ReviewPage(rawValue: raw) {
            reviewPage = page
        }
        if let w = env["NECKLINE_INITIAL_REVIEW_WEEK"], w.count == 8 {
            reviewWeekAnchor = w
            Task { await loadReviewOverview() }
        }
    }

    // MARK: - 不可变成绩包详情

    func openScorePackage(batchId: String) {
        selectedScorePackageID = batchId
        selectedScorePackage = nil
        selectedScorePackageError = nil
        Task { await loadScorePackage(batchId: batchId) }
    }

    func dismissScorePackage() {
        selectedScorePackageID = nil
        selectedScorePackage = nil
        selectedScorePackageError = nil
    }

    func loadScorePackage(batchId: String) async {
        guard let client = clientProvider() else { return }
        selectedScorePackageLoading = true
        defer { selectedScorePackageLoading = false }
        do {
            selectedScorePackage = try await client.fetchScoreboardPackage(batchId: batchId)
        } catch let error as APIError {
            selectedScorePackageError = error.errorDescription ?? "成绩包详情加载失败"
        } catch {
            selectedScorePackageError = error.localizedDescription
        }
    }

    /// K9-v3 only appends a user revision. The server rejects a post-9:26
    /// change and validates it against the frozen mechanical skeleton.
    func appendPlaybookRevision(batchId: String, tsCode: String, playbook: NKJSON) async -> String? {
        guard let client = clientProvider() else { return "未配置后端连接" }
        do {
            try await client.appendK9V3PlaybookRevision(batchId: batchId, tsCode: tsCode, playbook: playbook)
            await loadScorePackage(batchId: batchId)
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    // MARK: - 设置

    /// 服务端版本(`GET /health`,免鉴权)。**静默降级** —— 拉不到就保持 `nil`,
    /// 设置屏展示"服务端版本未知",⛔ 不冒充"版本相同"。
    func loadServerVersion() async {
        guard let client = clientProvider() else { return }
        do {
            let (_, version) = try await client.health()
            serverVersion = version
        } catch { /* 保持 nil */ }
    }

    func loadSettings() async {
        guard let client = clientProvider() else { return }
        settingsLoading = true
        async let settingsTask: Result<SettingsSnapshot, Error> = fetchResult { try await client.fetchSettings() }
        async let providersTask: Result<[Provider], Error> = fetchResult { try await client.fetchProviders() }
        async let routesTask: Result<LLMRoutes, Error> = fetchResult { try await client.fetchLLMRoutes() }
        async let usageTask: Result<UsageSummary, Error> = fetchResult { try await client.fetchUsageSummary() }
        let (s, p, r, u) = await (settingsTask, providersTask, routesTask, usageTask)
        switch s {
        case .success(let v):
            settings = v
            // ⚠ **服务端发什么就渲染什么**(⛔ 不硬编 kind 清单)。
            pushKindsDraft = v.push.kinds
        case .failure(let e):
            if let api = e as? APIError, case .noToken = api {} else { showToast("设置拉取失败", isError: true) }
        }
        if case .success(let v) = p { providers = v }
        if case .success(let v) = r { llmRoutes = v }
        if case .success(let v) = u { usageSummary = v }
        settingsLoading = false
    }

    func beginCreateProvider() {
        providerForm = ProviderForm()
        showProviderForm = true
    }

    func beginEditProvider(_ p: Provider) {
        providerForm = ProviderForm.editing(p)
        showProviderForm = true
    }

    func submitProviderForm() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        guard providerForm.isValid else {
            showToast("请填写名称 / Base URL / 模型名", isError: true); return
        }
        let f = providerForm
        let key = f.apiKey.trimmingCharacters(in: .whitespaces)
        let notes = f.notes.trimmingCharacters(in: .whitespaces)
        do {
            if let editing = f.editingName {
                // 局部更新:**没填就不传该键**(不传 = 不改;传了才是显式改写)。
                // ⚠ `searchEngine` / `notes` 与 `apiKey` 同一种「留空即不改」的读法 ——
                // 同一张表单里两种"留空"含义不同容易让用户误清。
                let body = ProviderUpdateRequest(
                    baseUrl: f.baseUrl, model: f.model, apiKey: key.isEmpty ? nil : key,
                    hasWebSearch: false, searchEngine: nil,
                    notes: notes.isEmpty ? nil : notes, enabled: f.enabled)
                _ = try await client.updateProvider(name: editing, body)
            } else {
                let body = ProviderCreateRequest(
                    name: f.name, baseUrl: f.baseUrl, model: f.model,
                    apiKey: key.isEmpty ? nil : key, hasWebSearch: false,
                    searchEngine: nil,
                    notes: notes.isEmpty ? nil : notes, enabled: f.enabled)
                _ = try await client.createProvider(body)
            }
            providerForm = ProviderForm()      // 安全态:key 草稿立即清空
            showProviderForm = false
            await loadSettings()
            showToast("Provider 已保存 · 运行时生效")
        } catch let e as APIError {
            providerForm.apiKey = ""   // 失败重试保留其余字段,key 草稿不残留明文
            showToast(e.errorDescription ?? "保存失败", isError: true)
        } catch {
            providerForm.apiKey = ""
            showToast("保存失败:\(error.localizedDescription)", isError: true)
        }
    }

    func saveTavilyKey() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        let key = tavilyKeyDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty else {
            showToast("请填写 Tavily API key", isError: true); return
        }
        do {
            _ = try await client.putTavilyKey(key)
            tavilyKeyDraft = ""
            await loadSettings()
            showToast("Tavily key 已保存")
        } catch let e as APIError {
            tavilyKeyDraft = ""
            showToast(e.errorDescription ?? "保存失败", isError: true)
        } catch {
            tavilyKeyDraft = ""
            showToast("保存失败:\(error.localizedDescription)", isError: true)
        }
    }

    func clearTavilyKey() async {
        guard let client = clientProvider() else { return }
        do {
            _ = try await client.deleteTavilyKey()
            tavilyKeyDraft = ""
            await loadSettings()
            showToast("Tavily key 已清除")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "清除失败", isError: true)
        } catch {
            showToast("清除失败:\(error.localizedDescription)", isError: true)
        }
    }

    func deleteProvider(name: String) async {
        guard let client = clientProvider() else { return }
        do {
            _ = try await client.deleteProvider(name: name)
            await loadSettings()
            showToast("已删除 Provider「\(name)」")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "删除失败", isError: true)
        } catch {
            showToast("删除失败:\(error.localizedDescription)", isError: true)
        }
    }

    /// 任务路由表:**全量覆盖式**写(未知任务名 → 422 `invalid_task`)。
    func saveRoutes(_ routes: [String: String], defaultProvider: String?) async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        do {
            _ = try await client.putLLMRoutes(routes: routes, defaultProvider: defaultProvider)
            await loadSettings()
            showToast("任务路由已保存")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "保存失败", isError: true)
        } catch {
            showToast("保存失败:\(error.localizedDescription)", isError: true)
        }
    }

    /// 按 kind 的推送开关。⚠ **全量覆盖式**:把当前草稿里的**每一个** kind 都传上去
    /// (缺键 → 422),承「防漏传静默重置某开关」的同一条纪律。
    func savePushSettings() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        let payload = Dictionary(pushKindsDraft.map { ($0.kind, $0.enabled) },
                                 uniquingKeysWith: { _, b in b })
        do {
            _ = try await client.putSettingsPush(kinds: payload)
            await loadSettings()
            showToast("推送设置已保存")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "保存失败", isError: true)
        } catch {
            showToast("保存失败:\(error.localizedDescription)", isError: true)
        }
    }

    /// 单个 kind 的开关(草稿层,点「保存」才写服务端)。
    func setPushKind(_ kind: String, enabled: Bool) {
        guard let idx = pushKindsDraft.firstIndex(where: { $0.kind == kind }) else { return }
        pushKindsDraft[idx].enabled = enabled
    }

    // MARK: - 复盘(解析逻辑全在后端 `neckline/review/`,本模型只装配 / 展示)

    var selectedReviewEntry: WeeklyReviewEntry? {
        if let sel = reviewSelectedWeek, let hit = reviewWeeks.first(where: { $0.week == sel }) {
            return hit
        }
        return reviewWeeks.first
    }

    func uploadReviewFiles(_ files: [(filename: String, data: Data)]) async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        guard !files.isEmpty else { return }
        reviewUploading = true
        do {
            let resp = try await client.uploadReview(files: files)
            reviewWeeks = resp.weeks.sorted { $0.week > $1.week }
            reviewSelectedWeek = reviewWeeks.first?.week
            reviewParseWarnings = resp.parseWarnings
            reviewDataWarnings = resp.dataWarnings
            if reviewWeeks.isEmpty {
                showToast("未解析出任何成交记录,请看下方警告排查文件格式", isError: true)
            } else {
                showToast("解析完成 · 共 \(reviewWeeks.count) 周")
            }
        } catch let e as APIError {
            showToast(e.errorDescription ?? "上传失败", isError: true)
        } catch {
            showToast("上传失败:\(error.localizedDescription)", isError: true)
        }
        reviewUploading = false
    }

    /// 复盘聚合读。**端点恒 200**,空态走各段自己的 `available` —— 故这里拉失败
    /// **只可能是网络 / 鉴权**,那才是 `reviewOverview = nil` 的含义
    /// (界面上说「本次没取到」,⛔ 不冒充"各段都没有")。
    func loadReviewOverview(week: String? = nil) async {
        guard let client = clientProvider() else { return }
        reviewOverviewLoading = true
        do {
            reviewOverview = try await client.fetchReviewOverview(week: week ?? reviewWeekAnchor)
            hydrateReviewWeeks()
        } catch let e as APIError {
            if case .noToken = e {} else { showToast(e.errorDescription ?? "复盘拉取失败", isError: true) }
        } catch { showToast("复盘拉取失败", isError: true) }
        reviewOverviewLoading = false
    }

    /// 🔴 **把服务端已有的那一周对账并进工作台**。
    ///
    /// 若 `reviewWeeks` 的唯一写入点是 `uploadReviewFiles`,重启 App 后工作台一律说
    /// 「还没有对账数据 · 把交割单拖到上面」——**而同一份数据服务端明明有**:
    /// 那是把**「没看」讲成了「没有」**。
    /// ⛔ **零新增网络调用**:`/review/overview` 的对账段本来就带着整份 `result`。
    /// ⚠ **按周去重、本次上传优先**:刚上传的那一份比服务端上一次落盘的更新。
    private func hydrateReviewWeeks() {
        guard let entry = reviewOverview?.reconcile.weeklyEntry else { return }
        if !reviewWeeks.contains(where: { $0.week == entry.week }) {
            reviewWeeks = (reviewWeeks + [entry]).sorted { $0.week > $1.week }
        }
        if reviewSelectedWeek == nil { reviewSelectedWeek = entry.week }
    }

    /// 翻周:`delta = -1` 上一周 / `+1` 下一周 / `nil` 回到本周。
    /// **纯选参数,不做任何判定** —— 周边界由服务端按交易日历算。
    func shiftReviewWeek(_ delta: Int?) async {
        guard let d = delta else {
            reviewWeekAnchor = nil
            await loadReviewOverview()
            await loadConclusions()
            return
        }
        let base = reviewWeekAnchor.flatMap { calendar.parseDate($0) }
            ?? reviewOverview.flatMap { calendar.parseDate($0.weekStart) }
            ?? Date()
        let moved = base.addingTimeInterval(TimeInterval(7 * 86400 * d))
        reviewWeekAnchor = calendar.compactString(moved)
        await loadReviewOverview()
        await loadConclusions()
    }

    /// 装订材料。⚠ **点一下才算** —— 它要读 parquet 行情,属于重活;
    /// ⛔ 别塞进每次进板块都会拉的聚合读里(§12 坑 1)。
    func loadBindery() async {
        guard let client = clientProvider() else {
            binderyError = "未配置后端连接"; return
        }
        guard let week = currentWeekKey, !week.isEmpty else {
            binderyError = "还不知道这是哪一周 —— 先拉一次复盘聚合读"
            return
        }
        binderyLoading = true
        binderyError = nil
        defer { binderyLoading = false }
        do {
            bindery = try await client.fetchBindery(week: week)
        } catch let e as APIError {
            bindery = nil
            binderyError = e.errorDescription ?? "装订失败"
        } catch {
            bindery = nil
            binderyError = "装订失败:\(error.localizedDescription)"
        }
    }

    /// 结论存档:该周最新版 + 全部版本。
    func loadConclusions() async {
        guard let client = clientProvider() else { return }
        guard let week = currentWeekKey, !week.isEmpty else { return }
        conclusionsLoading = true
        defer { conclusionsLoading = false }
        do { conclusions = try await client.fetchConclusions(week: week) }
        catch let e as APIError {
            if case .noToken = e {} else { showToast(e.errorDescription ?? "结论存档拉取失败", isError: true) }
        } catch { /* 网络没通:保持上一份,界面自己会说没刷新成 */ }
    }

    func beginConclusion() {
        conclusionForm = ConclusionForm(week: currentWeekKey ?? "")
        if let latest = conclusions.latest {
            // 从上一版起草(**append-only**:提交会存成新版本,老版本一个字不动)。
            conclusionForm.title = latest.title
            conclusionForm.body = latest.body
            conclusionForm.tagsText = latest.tags.joined(separator: " ")
        }
        showConclusionEditor = true
    }

    func submitConclusion() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        guard conclusionForm.isValid else {
            showToast("周、标题、正文都要填", isError: true); return
        }
        conclusionForm.submitting = true
        defer { conclusionForm.submitting = false }
        do {
            conclusions = try await client.saveConclusion(
                week: conclusionForm.week, title: conclusionForm.title,
                body: conclusionForm.body, tags: conclusionForm.tags)
            showConclusionEditor = false
            showToast("结论已存为第 \(conclusions.latest?.version ?? 0) 版")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "保存失败", isError: true)
        } catch {
            showToast("保存失败:\(error.localizedDescription)", isError: true)
        }
    }

    /// 当前这一周的 ISO 周键(`YYYY-Www`)。**唯一源是服务端**下发的 `weekKey`
    /// —— ⛔ 客户端不自己算 ISO 周(那是第二份事实源,跨年那一周必然对不上)。
    var currentWeekKey: String? {
        let key = reviewOverview?.weekKey ?? ""
        return key.isEmpty ? reviewSelectedWeek : key
    }

    private func fetchResult<T>(_ op: () async throws -> T) async -> Result<T, Error> {
        do { return .success(try await op()) } catch { return .failure(error) }
    }

    private func handleLoadFailure(_ error: Error, context: String) {
        if let e = error as? APIError {
            if case .noToken = e { return }
            loadError = e.errorDescription
            showToast("\(context)拉取失败:\(e.errorDescription ?? "")", isError: true)
        } else {
            loadError = error.localizedDescription
        }
    }

    // MARK: - Toast

    func showToast(_ message: String, isError: Bool = false) {
        let t = Toast(message: message, isError: isError)
        toast = t
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 2_400_000_000)
            if self.toast?.id == t.id { self.toast = nil }
        }
    }
}
