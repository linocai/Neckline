// Q 期货看板会话状态：完全静默（零定时器、零推送），按窗口（白天/晚间 20:15 后）每窗最多安静尝试一次；
// 默认展开第 1 张卡片（烧碱 SH），其余收起，支持全部展开/全部收起、卡内分段与基本信息二次折叠。

import Foundation
import Observation

enum QihuoCardTab: String, CaseIterable, Hashable {
    case all
    case news
    case data
    case holding
}

@MainActor
@Observable
final class QihuoBoardModel {
    enum LoadState: Equatable {
        case loading
        case ready(QihuoBoard)
        case error(QihuoBoardError)
    }

    @ObservationIgnored private let config: AppConfig
    @ObservationIgnored private let clientFactory: @MainActor (AppConfig) -> any QihuoBoardClient
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private var client: any QihuoBoardClient
    @ObservationIgnored private var appliedSignature: String
    @ObservationIgnored private var epoch = 0
    @ObservationIgnored private var ticket = 0
    @ObservationIgnored private var lastAttemptWindowKey: String?
    @ObservationIgnored private var isVisible = false
    @ObservationIgnored private var inFlightLoad: Task<Void, Never>?
    @ObservationIgnored private var inFlightToken = 0
    @ObservationIgnored private var connectionDebounce: Task<Void, Never>?
    @ObservationIgnored private var connectionChange: Task<Void, Never>?
    @ObservationIgnored private var hasInitializedExpanded = false

    private(set) var state: LoadState = .loading
    private(set) var refreshFailed = false
    private(set) var isRefreshing = false

    /// 展开的品种卡片 slug 集合（首次加载默认展开第 1 张，与样图一致）。
    var expandedSlugs: Set<String> = []
    /// 每张卡片内部分段选择（默认 `.all`）。
    var cardTabs: [String: QihuoCardTab] = [:]
    /// 每张卡片底部「基本信息档案」二次折叠状态（默认收起）。
    var expandedBasicSlugs: Set<String> = []

    init(config: AppConfig,
         clientFactory: @escaping @MainActor (AppConfig) -> any QihuoBoardClient = { QihuoBoardAPI(config: $0) },
         now: @escaping () -> Date = Date.init) {
        self.config = config
        self.clientFactory = clientFactory
        self.now = now
        self.client = clientFactory(config)
        self.appliedSignature = Self.signature(of: config)
    }

    var board: QihuoBoard? {
        if case .ready(let board) = state { return board }
        return nil
    }

    func isExpanded(_ slug: String) -> Bool {
        expandedSlugs.contains(slug)
    }

    func toggleCard(_ slug: String) {
        if expandedSlugs.contains(slug) {
            expandedSlugs.remove(slug)
        } else {
            expandedSlugs.insert(slug)
        }
    }

    func expandAll() {
        guard let board else { return }
        expandedSlugs = Set(board.products.map(\.slug))
    }

    func collapseAll() {
        expandedSlugs.removeAll()
    }

    func tab(for slug: String) -> QihuoCardTab {
        cardTabs[slug] ?? .all
    }

    func setTab(_ tab: QihuoCardTab, for slug: String) {
        cardTabs[slug] = tab
    }

    func isBasicExpanded(_ slug: String) -> Bool {
        expandedBasicSlugs.contains(slug)
    }

    func toggleBasic(_ slug: String) {
        if expandedBasicSlugs.contains(slug) {
            expandedBasicSlugs.remove(slug)
        } else {
            expandedBasicSlugs.insert(slug)
        }
    }

    // MARK: 页面可见性与按需取数（盘中彻底静默）

    func setVisible(_ visible: Bool) {
        isVisible = visible
    }

    func loadIfNeeded() async {
        if let existing = inFlightLoad {
            await existing.value
            return
        }
        let windowKey = Self.windowKey(at: now())
        switch state {
        case .loading:
            break
        case .ready:
            guard lastAttemptWindowKey != windowKey else { return }
        case .error(let err):
            // 缺少令牌或鉴权失败等待用户改配置；其余错误（含临时协议不兼容）跨入新窗口后给一次自动复核机会
            switch err {
            case .missingToken, .unauthorized:
                return
            default:
                guard lastAttemptWindowKey != windowKey else { return }
            }
        }
        lastAttemptWindowKey = windowKey
        inFlightToken += 1
        let myToken = inFlightToken
        let task = Task { @MainActor [self] in
            await fetch(forceRefresh: false)
        }
        inFlightLoad = task
        await task.value
        if inFlightToken == myToken {
            inFlightLoad = nil
        }
    }

    func refresh() async {
        lastAttemptWindowKey = Self.windowKey(at: now())
        await Task { @MainActor [self] in
            await fetch(forceRefresh: true)
        }.value
    }

    /// 将北京时间划分为两个自然窗口：`YYYY-MM-DD|day`（00:00～20:14）与 `YYYY-MM-DD|evening`（20:15～23:59）。
    /// 每个窗口最多自动尝试拉取一次：既不在晚间或周末反复自动请求，也不会因冷启动断网永久锁死。
    private static func windowKey(at date: Date) -> String {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "Asia/Shanghai") ?? TimeZone(secondsFromGMT: 8 * 3600)!
        let comps = cal.dateComponents([.year, .month, .day, .hour, .minute], from: date)
        let day = String(format: "%04d-%02d-%02d", comps.year ?? 0, comps.month ?? 0, comps.day ?? 0)
        let mins = (comps.hour ?? 0) * 60 + (comps.minute ?? 0)
        let slot = mins >= (20 * 60 + 15) ? "evening" : "day"
        return "\(day)|\(slot)"
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
            refreshFailed = false
            if !hasInitializedExpanded {
                hasInitializedExpanded = true
                if let first = board.products.first {
                    expandedSlugs = [first.slug]
                }
            } else {
                let valid = Set(board.products.map(\.slug))
                expandedSlugs = expandedSlugs.intersection(valid)
                expandedBasicSlugs = expandedBasicSlugs.intersection(valid)
            }
        } catch is CancellationError {
            return
        } catch {
            guard epoch == capturedEpoch, ticket == mine else { return }
            let failure = error as? QihuoBoardError ?? .unreadable
            if case .ready = state, failure.isTransient {
                refreshFailed = true
            } else {
                state = .error(failure)
                refreshFailed = false
            }
        }
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

    /// 同步立即失效旧连接与旧快照，避免切换环境时先等其他板块重载而短暂沿用旧客户端。
    @discardableResult
    func invalidateForConnectionChange() -> Bool {
        let next = Self.signature(of: config)
        guard next != appliedSignature else { return false }
        appliedSignature = next
        epoch += 1
        inFlightToken += 1
        inFlightLoad?.cancel()
        inFlightLoad = nil
        state = .loading
        lastAttemptWindowKey = nil
        hasInitializedExpanded = false
        expandedSlugs.removeAll()
        expandedBasicSlugs.removeAll()
        cardTabs.removeAll()
        refreshFailed = false
        client = clientFactory(config)
        return true
    }

    func handleConnectionChange() async {
        guard invalidateForConnectionChange() else { return }
        if isVisible {
            await loadIfNeeded()
        }
    }

    private static func signature(of config: AppConfig) -> String {
        "\(config.environment.rawValue)|\(config.baseURLOverride)|\(config.apiToken)"
    }
}
