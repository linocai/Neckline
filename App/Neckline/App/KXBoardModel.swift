// KX 看板的会话状态与自动刷新。和晨报状态分开：只在内存留最近一次看板，不写磁盘。

import Foundation
import Observation

/// 什么时候自动续取。只有页面在前台、App 处于活跃、且接口说这是交易时段时才续取，间隔用接口给的秒数。
enum KXRefreshPolicy {
    /// 下一次自动续取前要等多久；不该续取时返回 nil。
    static func interval(visible: Bool, active: Bool, board: KXBoard?) -> TimeInterval? {
        guard visible, active, let board else { return nil }
        return interval(for: board)
    }

    /// 只看接口：交易时段并且给了正的刷新间隔。
    static func interval(for board: KXBoard) -> TimeInterval? {
        guard board.session.inSession, let seconds = board.session.refreshSeconds, seconds > 0 else { return nil }
        return seconds
    }
}

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
    @ObservationIgnored private let sleep: @Sendable (TimeInterval) async throws -> Void
    @ObservationIgnored private var client: any KXBoardClient
    @ObservationIgnored private var appliedSignature: String
    @ObservationIgnored private var epoch = 0
    @ObservationIgnored private var ticket = 0
    @ObservationIgnored private var loop: Task<Void, Never>?
    @ObservationIgnored private var loopToken = 0
    @ObservationIgnored private var connectionDebounce: Task<Void, Never>?
    @ObservationIgnored private var connectionChange: Task<Void, Never>?

    private(set) var state: LoadState = .loading
    /// 已经有数据时，最近一次续取没成功。数据保留，页眉小字写明。
    private(set) var refreshFailed = false
    /// 当前分组。刷新、切标签都不重置。
    var group: KXColumnGroup = .overview
    private(set) var isVisible = false
    private(set) var isActive = true

    init(config: AppConfig,
         clientFactory: @escaping @MainActor (AppConfig) -> any KXBoardClient = { KXBoardAPI(config: $0) },
         sleep: @escaping @Sendable (TimeInterval) async throws -> Void = { seconds in
             try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
         }) {
        self.config = config
        self.clientFactory = clientFactory
        self.sleep = sleep
        self.client = clientFactory(config)
        self.appliedSignature = Self.signature(of: config)
    }

    var board: KXBoard? {
        if case .ready(let board) = state { return board }
        return nil
    }

    /// 自动续取循环是否在跑。
    var isAutoRefreshing: Bool { loop != nil }

    // MARK: 页面与 App 状态

    /// KX 页面是否在前台。切到别的标签就是 false。
    func setVisible(_ visible: Bool) {
        guard visible != isVisible else { return }
        isVisible = visible
        syncAutoRefresh()
    }

    /// App 是否处于活跃状态。进入后台、锁屏就是 false。
    func setActive(_ active: Bool) {
        guard active != isActive else { return }
        isActive = active
        syncAutoRefresh()
    }

    // MARK: 取数

    /// 下拉刷新和重试。取数放进独立任务，下拉手势结束时不会把请求一起取消。
    func refresh() async {
        await Task { @MainActor [self] in await fetch() }.value
        resumeLoopIfNeeded()
    }

    private func fetch() async {
        let capturedEpoch = epoch
        ticket += 1
        let mine = ticket
        do {
            let board = try await client.board()
            guard epoch == capturedEpoch, ticket == mine else { return }
            state = .ready(board)
            refreshFailed = false
        } catch is CancellationError {
            return
        } catch {
            guard epoch == capturedEpoch, ticket == mine else { return }
            let failure = error as? KXBoardError ?? .unreadable
            if case .ready = state, failure.isTransient {
                refreshFailed = true
            } else {
                state = .error(failure)
                refreshFailed = false
            }
        }
    }

    // MARK: 自动续取

    private func syncAutoRefresh() {
        if isVisible && isActive {
            if loop == nil { startLoop(fetchFirst: true) }
        } else {
            stopLoop()
        }
    }

    /// 首次取数没成功、循环已经结束时，手动刷新成功后接着自动续取。
    private func resumeLoopIfNeeded() {
        guard loop == nil, KXRefreshPolicy.interval(visible: isVisible, active: isActive, board: board) != nil else { return }
        startLoop(fetchFirst: false)
    }

    private func startLoop(fetchFirst: Bool) {
        loopToken += 1
        let token = loopToken
        loop = Task { [weak self] in
            guard let self else { return }
            if fetchFirst { await self.fetch() }
            while !Task.isCancelled, self.loopToken == token {
                // 非交易时段、或没拿到过看板：只取这一次。
                guard let seconds = KXRefreshPolicy.interval(visible: self.isVisible, active: self.isActive, board: self.board) else { break }
                do {
                    try await self.sleep(seconds)
                } catch {
                    break
                }
                guard !Task.isCancelled, self.loopToken == token else { break }
                await self.fetch()
            }
            if self.loopToken == token { self.loop = nil }
        }
    }

    private func stopLoop() {
        loopToken += 1
        loop?.cancel()
        loop = nil
    }

    // MARK: 连接变化

    /// 手填地址和令牌按键连续变化时稍等再切换，避免每打一个字就取一次。
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
        state = .loading
        refreshFailed = false
        client = clientFactory(config)
        stopLoop()
        if isVisible && isActive { startLoop(fetchFirst: true) }
    }

    private static func signature(of config: AppConfig) -> String {
        config.resolvedBaseURL.absoluteString + "\n" + config.apiToken
    }
}
