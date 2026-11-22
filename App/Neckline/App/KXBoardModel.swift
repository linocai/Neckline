// KX V4 看板会话状态：盘中彻底静默，无自动轮询定时器；
// 首次进入、连接配置变化或跨过新的 20:00 盘后窗口重新进入时按需安静拉一次，支持手动刷新与本地筛选搜索。

import Foundation
import Observation

@MainActor
@Observable
final class KXBoardModel {
    enum LoadState: Equatable {
        case loading
        case ready(KXBoard)
        case error(KXBoardError)
    }

    @ObservationIgnored private let config: AppConfig
    @ObservationIgnored private let clientFactory: @MainActor (AppConfig) -> any KXBoardClient
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private var client: any KXBoardClient
    @ObservationIgnored private var appliedSignature: String
    @ObservationIgnored private var epoch = 0
    @ObservationIgnored private var ticket = 0
    @ObservationIgnored private var lastLoadedAt: Date?
    @ObservationIgnored private var lastDaytimeAttemptDay: String?
    @ObservationIgnored private var isVisible = false
    @ObservationIgnored private var inFlightLoad: Task<Void, Never>?
    @ObservationIgnored private var connectionDebounce: Task<Void, Never>?
    @ObservationIgnored private var connectionChange: Task<Void, Never>?

    private(set) var state: LoadState = .loading
    /// 已经有数据时，手动刷新遇到暂时性网络错误，保留旧快照并展示轻量提醒。
    private(set) var refreshFailed = false
    private(set) var isRefreshing = false

    /// 情绪派系过滤（默认「全部」直接过滤，不置灰）与搜索关键词。
    var selectedFaction: String = KXCopy.allFaction {
        didSet { reconcileSelection() }
    }
    var searchText: String = "" {
        didSet { reconcileSelection() }
    }
    /// macOS 右栏选中股票代码。
    var selectedCode: String?
    /// iOS 详情 Sheet 当前打开的股票代码（按当前快照动态解析，点 KX 推送自动关闭旧详情）。
    var presentedDetailCode: String?

    init(config: AppConfig,
         clientFactory: @escaping @MainActor (AppConfig) -> any KXBoardClient = { KXBoardAPI(config: $0) },
         now: @escaping () -> Date = Date.init) {
        self.config = config
        self.clientFactory = clientFactory
        self.now = now
        self.client = clientFactory(config)
        self.appliedSignature = Self.signature(of: config)
    }

    var board: KXBoard? {
        if case .ready(let board) = state { return board }
        return nil
    }

    var filteredItems: [KXBoardItem] {
        guard let board else { return [] }
        return board.items.filter { $0.matches(faction: selectedFaction, query: searchText) }
    }

    func filteredItems(in zone: KXZone) -> [KXBoardItem] {
        filteredItems.filter { $0.zone == zone }
    }

    func item(for code: String) -> KXBoardItem? {
        board?.items.first { $0.code == code }
    }

    var selectedItem: KXBoardItem? {
        let visible = filteredItems
        if let selectedCode, let match = visible.first(where: { $0.code == selectedCode }) {
            return match
        }
        return visible.first
    }

    var presentedDetailItem: KXBoardItem? {
        guard let code = presentedDetailCode else { return nil }
        return item(for: code)
    }

    var hasActiveFilter: Bool {
        selectedFaction != KXCopy.allFaction || !searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    func select(_ code: String) {
        selectedCode = code
    }

    func openDetail(_ code: String) {
        selectedCode = code
        presentedDetailCode = code
    }

    func closeDetail() {
        presentedDetailCode = nil
    }

    func resetFilters() {
        selectedFaction = KXCopy.allFaction
        searchText = ""
    }

    // MARK: 页面可见性与按需取数（盘中彻底静默，无定时器）

    func setVisible(_ visible: Bool) {
        isVisible = visible
    }

    /// 首次打开或跨过新的 20:00 盘后窗口后重新进入 KX 页时按需拉一次；合并并发首次加载。
    func loadIfNeeded() async {
        if let existing = inFlightLoad {
            await existing.value
            return
        }
        switch state {
        case .loading:
            break
        case .ready(let current):
            guard shouldQuietlyRefreshAfterPostMarket(current) else { return }
        case .error:
            return
        }
        let task = Task { @MainActor [self] in
            await fetch(forceRefresh: false)
        }
        inFlightLoad = task
        await task.value
        inFlightLoad = nil
    }

    /// 用户手动下拉刷新或点刷新按钮。
    func refresh() async {
        await Task { @MainActor [self] in
            await fetch(forceRefresh: true)
        }.value
    }

    /// iOS 点击 KX 20:00 推送通知：关闭旧个股详情，切回新版列表并强制刷新。
    func handlePushTap() async {
        presentedDetailCode = nil
        await refresh()
    }

    private func shouldQuietlyRefreshAfterPostMarket(_ board: KXBoard) -> Bool {
        let current = now()
        guard let today20 = Self.todayPostMarketCutoff(at: current),
              let cutoff = Self.latestPostMarketCutoff(at: current, today20: today20) else { return false }
        guard let lastLoadedAt else { return true }
        let todayIso = Self.shanghaiDayString(current)
        // 1. 今晚 20:00 后：首次跨过 20:00 或快照尚非今日且距上次读取满 5 分钟，安静检查一次
        if current >= today20 {
            if lastLoadedAt < today20 {
                return true
            }
            return board.asOf < todayIso && current.timeIntervalSince(lastLoadedAt) >= 300
        }
        // 2. 次日白天（00:00～19:59）：两个次晨入口共用当天尝试标记，遇临时断网也绝不盘中反复请求
        guard lastDaytimeAttemptDay != todayIso else { return false }
        let prevWindowDay = Self.shanghaiDayString(cutoff)
        let startOfToday = Self.shanghaiCalendar().startOfDay(for: current)
        if lastLoadedAt < cutoff || (board.asOf < prevWindowDay && lastLoadedAt < startOfToday) {
            lastDaytimeAttemptDay = todayIso
            return true
        }
        return false
    }

    private static func shanghaiCalendar() -> Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Shanghai") ?? TimeZone(secondsFromGMT: 8 * 3600)!
        return calendar
    }

    private static func shanghaiDayString(_ date: Date) -> String {
        let comps = shanghaiCalendar().dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", comps.year ?? 0, comps.month ?? 0, comps.day ?? 0)
    }

    private static func todayPostMarketCutoff(at date: Date) -> Date? {
        let cal = shanghaiCalendar()
        var comps = cal.dateComponents([.year, .month, .day], from: date)
        comps.hour = 20
        comps.minute = 0
        comps.second = 0
        return cal.date(from: comps)
    }

    /// 返回 <= date 的最近一个北京时间 20:00 时刻（20:00 前返回昨天 20:00，20:00 及以后返回今天 20:00）。
    private static func latestPostMarketCutoff(at date: Date, today20: Date) -> Date? {
        if date >= today20 {
            return today20
        }
        return shanghaiCalendar().date(byAdding: .day, value: -1, to: today20)
    }

    private func fetch(forceRefresh: Bool) async {
        let capturedEpoch = epoch
        ticket += 1
        let mine = ticket
        isRefreshing = true
        defer {
            if epoch == capturedEpoch, ticket == mine {
                isRefreshing = false
            }
        }
        do {
            let board = try await client.board(forceRefresh: forceRefresh)
            guard epoch == capturedEpoch, ticket == mine else { return }
            state = .ready(board)
            lastLoadedAt = now()
            refreshFailed = false
            if selectedFaction != KXCopy.allFaction,
               !board.factions.contains(where: { $0.name == selectedFaction }) {
                selectedFaction = KXCopy.allFaction
            }
            if let openCode = presentedDetailCode, !board.items.contains(where: { $0.code == openCode }) {
                presentedDetailCode = nil
            }
            reconcileSelection()
        } catch is CancellationError {
            return
        } catch {
            guard epoch == capturedEpoch, ticket == mine else { return }
            let failure = error as? KXBoardError ?? .unreadable
            if case .ready = state, failure.isTransient {
                refreshFailed = true
            } else {
                state = .error(failure)
                presentedDetailCode = nil
                refreshFailed = false
            }
        }
    }

    private func reconcileSelection() {
        let visible = filteredItems
        if let selectedCode, visible.contains(where: { $0.code == selectedCode }) {
            return
        }
        selectedCode = visible.first?.code
    }

    // MARK: 连接变化

    func connectionInputsChanged() {
        connectionDebounce?.cancel()
        connectionDebounce = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 300_000_000)
            guard !Task.isCancelled, let self else { return }
            self.connectionChange = Task { @MainActor [weak self] in
                await self?.handleConnectionChange()
            }
        }
    }

    func handleConnectionChange() async {
        let next = Self.signature(of: config)
        guard next != appliedSignature else { return }
        appliedSignature = next
        epoch += 1
        inFlightLoad?.cancel()
        inFlightLoad = nil
        state = .loading
        lastLoadedAt = nil
        lastDaytimeAttemptDay = nil
        refreshFailed = false
        selectedFaction = KXCopy.allFaction
        searchText = ""
        selectedCode = nil
        presentedDetailCode = nil
        client = clientFactory(config)
        if isVisible {
            await loadIfNeeded()
        }
    }

    private static func signature(of config: AppConfig) -> String {
        config.resolvedBaseURL.absoluteString + "\n" + config.apiToken
    }
}
