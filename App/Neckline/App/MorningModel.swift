// 晨报会话状态。只在内存留下本次打开过的期和历史列表，不写磁盘缓存。

import Foundation
import Observation

/// 顶层分区。iOS 是底部三个标签；macOS 侧栏只有 K10 和 KX。
enum MorningTab: Hashable {
    case k10
    case kx
    case settings
}

/// iOS 的 K10 里在「最新」和「历史」之间切换。
enum K10Section: Hashable {
    case latest
    case history
}

enum MorningSlot: Hashable {
    case latest
    case date(String)
}

enum MorningLoadState: Equatable {
    case loading
    case ready(MorningReport)
    case empty(MorningReport)
    case failed(MorningReport)
    case running(MorningReport)
    case error(MorningClientError)
}

struct MorningMessage: Equatable {
    var title: String
    var detail: String?
    var problem: Bool
    var retry: Bool
    var settings: Bool = false
    var icon: String = "exclamationmark.circle"
}

struct MorningCheckLine: Identifiable, Equatable {
    let id: Int
    let text: String
    let problem: Bool
}

enum MorningNoticePermission: Equatable {
    case notAsked
    case allowed
    case denied

    var title: String {
        switch self {
        case .notAsked: "未询问"
        case .allowed: "已允许"
        case .denied: "已拒绝"
        }
    }
}

enum MorningNoticeRegistration: Equatable {
    case registered
    case failed

    var title: String {
        switch self {
        case .registered: "已登记"
        case .failed: "没能登记"
        }
    }
}

@MainActor
@Observable
final class MorningModel {
    private let config: AppConfig
    private let clientFactory: @MainActor (AppConfig) -> any MorningClient
    private let defaults: UserDefaults
    private var client: any MorningClient
    private var appliedSignature: String
    private var tickets: [MorningSlot: Int] = [:]
    private var historyTicket = 0
    private var epoch = 0
    private var connectionDebounce: Task<Void, Never>?
    private var connectionChange: Task<Void, Never>?
    private var pendingDeviceToken: String?
    private var registeredDeviceToken: String?
    private var registrationFlight: (epoch: Int, token: String)?
    private var loadCancelRetries: [MorningSlot: Int] = [:]

    var tab: MorningTab = .k10
    var k10Section: K10Section = .latest
    var focus: MorningSlot = .latest
    private(set) var history: [MorningHistoryItem] = []
    private(set) var historyError: MorningClientError?
    private(set) var historyLoaded = false
    private(set) var connectionLines: [MorningCheckLine] = []
    private(set) var isCheckingConnection = false
    private(set) var notificationPermission: MorningNoticePermission = .notAsked
    private(set) var notificationRegistration: MorningNoticeRegistration?
    private(set) var selectedEntryKey: String?
    /// 每次点推送加一。晨报栈用它回到名单根页，历史栈不看这个数。
    private(set) var reportStackReset = 0
    private var states: [MorningSlot: MorningLoadState] = [:]

    init(config: AppConfig,
         clientFactory: @escaping @MainActor (AppConfig) -> any MorningClient = { MorningAPI(config: $0) },
         defaults: UserDefaults = .standard) {
        self.config = config
        self.clientFactory = clientFactory
        self.defaults = defaults
        self.client = clientFactory(config)
        self.appliedSignature = Self.signature(of: config)
        discardLegacyCache()
    }

    func state(for slot: MorningSlot) -> MorningLoadState {
        states[slot] ?? .loading
    }

    func report(for slot: MorningSlot) -> MorningReport? {
        switch state(for: slot) {
        case .ready(let report), .empty(let report), .failed(let report), .running(let report):
            return report
        case .loading, .error:
            return nil
        }
    }

    func message(for state: MorningLoadState) -> MorningMessage? {
        switch state {
        case .loading:
            return MorningMessage(title: MorningCopy.reading, detail: nil, problem: false, retry: false, icon: "clock")
        case .ready:
            return nil
        case .empty:
            return MorningMessage(title: MorningCopy.emptyEntries, detail: nil, problem: false, retry: false, icon: "tray")
        case .failed(let report):
            let reason = report.failureReason?.trimmingCharacters(in: .whitespacesAndNewlines)
            let detail = (reason?.isEmpty == false) ? reason : nil
            return MorningMessage(title: MorningCopy.failedTitle, detail: detail, problem: true, retry: false, icon: "exclamationmark.triangle")
        case .running:
            return MorningMessage(title: MorningCopy.running, detail: MorningCopy.runningDetail, problem: false, retry: true, icon: "clock")
        case .error(let error):
            return .forClientError(error)
        }
    }

    func selectedEntry(in report: MorningReport) -> MorningEntry? {
        let main = report.content?.entries ?? []
        if let selectedEntryKey, let match = Self.entries(in: report).first(where: { $0.listID == selectedEntryKey }) {
            return match
        }
        return main.first
    }

    /// 右栏详情只认当前选中，在主名单和四组待定里找；没选中时不退回主名单第一条。
    func detailEntry(in report: MorningReport) -> MorningEntry? {
        guard let selectedEntryKey else { return nil }
        return Self.entries(in: report).first { $0.listID == selectedEntryKey }
    }

    func select(_ entry: MorningEntry) {
        selectedEntryKey = entry.listID
    }

    func reload(_ slot: MorningSlot) async {
        let capturedEpoch = epoch
        let ticket = (tickets[slot] ?? 0) + 1
        tickets[slot] = ticket
        if states[slot] == nil {
            states[slot] = .loading
        }
        do {
            let report: MorningReport
            switch slot {
            case .latest:
                report = try await client.latest()
            case .date(let tradeDate):
                report = try await client.report(tradeDate: tradeDate)
            }
            guard epoch == capturedEpoch, tickets[slot] == ticket else { return }
            loadCancelRetries[slot] = 0
            states[slot] = Self.classify(report)
            if slot == focus { adoptSelection(for: report) }
        } catch is CancellationError {
            // 被后来的请求取代时直接丢掉。仍是当前这一期、又停在读取中时，换一个不被取消的任务再取一次。
            guard epoch == capturedEpoch, tickets[slot] == ticket else { return }
            guard case .loading = states[slot] else { return }
            let retries = loadCancelRetries[slot] ?? 0
            guard retries < 1 else { return }
            loadCancelRetries[slot] = retries + 1
            Task { @MainActor [weak self] in
                await self?.reload(slot)
            }
        } catch {
            guard epoch == capturedEpoch, tickets[slot] == ticket else { return }
            states[slot] = .error(Self.clientError(error))
        }
    }

    func open(_ slot: MorningSlot) async {
        focus = slot
        await reload(slot)
    }

    func reloadHistory() async {
        let capturedEpoch = epoch
        historyTicket += 1
        let ticket = historyTicket
        do {
            let page = try await client.history(limit: 60)
            guard epoch == capturedEpoch, historyTicket == ticket else { return }
            history = page.reports
            historyError = nil
            historyLoaded = true
        } catch is CancellationError {
            return
        } catch {
            guard epoch == capturedEpoch, historyTicket == ticket else { return }
            historyLoaded = true
            if history.isEmpty { historyError = Self.clientError(error) }
        }
    }

    func handleConnectionChange() async {
        let next = Self.signature(of: config)
        guard next != appliedSignature else { return }
        appliedSignature = next
        epoch += 1
        states.removeAll()
        history.removeAll()
        historyError = nil
        historyLoaded = false
        connectionLines.removeAll()
        selectedEntryKey = nil
        focus = .latest
        notificationRegistration = nil
        registeredDeviceToken = nil
        loadCancelRetries.removeAll()
        client = clientFactory(config)
        await reload(.latest)
        await reloadHistory()
        await sendDeviceTokenIfPossible()
    }

    /// 手填地址和令牌按键连续变化时稍等再清空，避免每打一个字就打一次接口。
    /// 后续按键只取消还在等待的防抖；已经开始的切换单独跑完，不被这次取消带走。
    func connectionInputsChanged() {
        connectionDebounce?.cancel()
        connectionDebounce = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 300_000_000)
            guard !Task.isCancelled, let self else { return }
            self.connectionChange = Task { @MainActor [weak self] in
                guard let self else { return }
                await self.handleConnectionChange()
            }
        }
    }

    func handleNotificationTap() async {
        tab = .k10
        k10Section = .latest
        focus = .latest
        reportStackReset += 1
        await reload(.latest)
    }

    func checkConnection() async {
        guard !isCheckingConnection else { return }
        isCheckingConnection = true
        defer { isCheckingConnection = false }
        await collectConnectionLines()
        await retryFailedDeviceRegistration()
    }

    /// 上一次登记失败后重试。已登记、或没有待登记令牌时不发请求。
    func retryFailedDeviceRegistration() async {
        guard notificationRegistration == .failed else { return }
        await sendDeviceTokenIfPossible()
    }

    private func collectConnectionLines() async {
        let capturedEpoch = epoch
        if let problem = config.connectionConfigurationError {
            connectionLines = [MorningCheckLine(id: 0, text: problem, problem: true)]
            return
        }
        let health: MorningHealth
        do {
            health = try await client.health()
        } catch is CancellationError {
            return
        } catch {
            guard epoch == capturedEpoch else { return }
            connectionLines = [MorningCheckLine(id: 0, text: MorningCopy.text(for: Self.clientError(error)), problem: true)]
            return
        }
        guard epoch == capturedEpoch else { return }
        var lines: [MorningCheckLine] = []
        switch health.db {
        case "ready":
            lines.append(MorningCheckLine(id: lines.count, text: "服务正常，库已就绪", problem: false))
        case "not_configured":
            lines.append(MorningCheckLine(id: lines.count, text: MorningCopy.notConfigured, problem: true))
        default:
            lines.append(MorningCheckLine(id: lines.count, text: MorningCopy.dbNotReady, problem: true))
        }
        if config.hasToken {
            do {
                let report = try await client.latest()
                guard epoch == capturedEpoch else { return }
                let phrase = Self.statusPhrase(report.status)
                lines.append(MorningCheckLine(id: lines.count, text: "最新一期 \(Self.monthDay(report.tradeDate))，\(phrase.text)", problem: phrase.problem))
            } catch is CancellationError {
                return
            } catch {
                guard epoch == capturedEpoch else { return }
                let mapped = Self.clientError(error)
                let line: MorningCheckLine
                switch mapped {
                case .noReport:
                    line = MorningCheckLine(id: lines.count, text: MorningCopy.noReport, problem: false)
                case .unauthorized:
                    line = MorningCheckLine(id: lines.count, text: MorningCopy.unauthorized, problem: true)
                default:
                    line = MorningCheckLine(id: lines.count, text: MorningCopy.text(for: mapped), problem: true)
                }
                if !lines.contains(where: { $0.text == line.text }) { lines.append(line) }
            }
        } else if let latest = health.latestReport {
            let phrase = Self.statusPhrase(latest.status)
            lines.append(MorningCheckLine(id: lines.count, text: "最新一期 \(Self.monthDay(latest.tradeDate))，\(phrase.text)", problem: phrase.problem))
        } else {
            lines.append(MorningCheckLine(id: lines.count, text: MorningCopy.noReport, problem: false))
        }
        lines.append(MorningCheckLine(id: lines.count, text: health.readPage ? "只读网页可用" : "只读网页未配置", problem: !health.readPage))
        lines.append(MorningCheckLine(id: lines.count, text: health.modelKey ? "模型已配置" : "模型未配置", problem: !health.modelKey))
        guard epoch == capturedEpoch else { return }
        connectionLines = lines
    }

    func fetchDocument(id: String) async throws -> MorningDocument {
        try await client.document(id: id)
    }

    func registerDevice(hexToken: String) async {
        pendingDeviceToken = hexToken
        await sendDeviceTokenIfPossible()
    }

    func noteNotificationPermission(_ permission: MorningNoticePermission) {
        notificationPermission = permission
    }

    func noteNotificationRegistration(_ registration: MorningNoticeRegistration) {
        notificationRegistration = registration
    }

    private func sendDeviceTokenIfPossible() async {
        guard let token = pendingDeviceToken, config.hasToken else { return }
        if notificationRegistration == .registered, registeredDeviceToken == token { return }
        if let flight = registrationFlight, flight.epoch == epoch, flight.token == token { return }
        let capturedEpoch = epoch
        let capturedToken = token
        registrationFlight = (capturedEpoch, capturedToken)
        defer {
            if registrationFlight?.epoch == capturedEpoch, registrationFlight?.token == capturedToken {
                registrationFlight = nil
            }
        }
        do {
            let ack = try await client.registerDevice(token: capturedToken)
            guard epoch == capturedEpoch, pendingDeviceToken == capturedToken else { return }
            if ack.ok {
                notificationRegistration = .registered
                registeredDeviceToken = capturedToken
            } else {
                notificationRegistration = .failed
                registeredDeviceToken = nil
            }
        } catch is CancellationError {
            return
        } catch {
            guard epoch == capturedEpoch, pendingDeviceToken == capturedToken else { return }
            notificationRegistration = .failed
            registeredDeviceToken = nil
        }
    }

    private func adoptSelection(for report: MorningReport) {
        let entries = Self.entries(in: report)
        if let selectedEntryKey, entries.contains(where: { $0.listID == selectedEntryKey }) { return }
        selectedEntryKey = report.content?.entries.first?.listID
    }

    private func discardLegacyCache() {
        let keys = defaults.dictionaryRepresentation().keys.filter { $0.hasPrefix("neckline.k10.") }
        for key in keys { defaults.removeObject(forKey: key) }
    }

    private static func classify(_ report: MorningReport) -> MorningLoadState {
        switch report.status {
        case .available:
            guard let content = report.content else { return .error(.unreadable) }
            return content.entries.isEmpty ? .empty(report) : .ready(report)
        case .failed:
            return .failed(report)
        case .running:
            return .running(report)
        case .unknown:
            return .error(.unreadable)
        }
    }

    private static func entries(in report: MorningReport) -> [MorningEntry] {
        guard let content = report.content else { return [] }
        return content.entries + content.review.types + content.review.relation + content.review.fresh + content.review.old
    }

    private static func signature(of config: AppConfig) -> String {
        config.resolvedBaseURL.absoluteString + "\n" + config.apiToken
    }

    private static func clientError(_ error: Error) -> MorningClientError {
        error as? MorningClientError ?? .unreadable
    }

    private static func statusPhrase(_ status: MorningStatus) -> (text: String, problem: Bool) {
        switch status {
        case .available: ("已出", false)
        case .failed: ("出问题", true)
        case .running: ("正在生成", false)
        case .unknown: ("状态不明", true)
        }
    }

    private static func monthDay(_ tradeDate: String) -> String {
        let pieces = tradeDate.split(separator: "-")
        guard pieces.count == 3, let month = Int(pieces[1]), let day = Int(pieces[2]),
              (1...12).contains(month), (1...31).contains(day) else { return tradeDate }
        return "\(month)/\(day)"
    }
}

extension MorningMessage {
    static func forClientError(_ error: MorningClientError) -> MorningMessage {
        MorningMessage(
            title: MorningCopy.text(for: error),
            detail: MorningCopy.detail(for: error),
            problem: false,
            retry: error != .noReport,
            settings: error == .missingToken || error == .unauthorized,
            icon: MorningCopy.symbol(for: error)
        )
    }
}

extension MorningEntry {
    var listID: String { "\(rank)|\(companyCode)|\(headline)|\(type)" }
}

extension MorningHistoryItem: Identifiable {
    var id: String { tradeDate }
}
