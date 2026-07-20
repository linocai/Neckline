//
//  AppModel.swift
//  Neckline — 应用状态(@Observable)
//
//  四板块(iPhone TabBar / macOS 侧栏 + 周复盘工作台,§五 阶段4C):
//    今日计划(report + positions) / 盘中看板(board) / 问询台(inquiry) / 设置(settings)。
//

import Foundation
import Observation

enum AppTab: String, CaseIterable, Identifiable {
    case today, board, inquiry, settings, review
    var id: String { rawValue }
    var title: String {
        switch self {
        case .today: return "今日计划"
        case .board: return "盘中看板"
        case .inquiry: return "问询台"
        case .settings: return "设置"
        case .review: return "周复盘工作台"
        }
    }
    var systemImage: String {
        switch self {
        case .today: return "list.bullet.clipboard"
        case .board: return "waveform.path.ecg"
        case .inquiry: return "bubble.left.and.bubble.right"
        case .settings: return "gearshape"
        case .review: return "tray.and.arrow.down"
        }
    }
}

/// 开仓录入草稿(止损线由服务端派生返回,表单不手填、不预先本地算)。
struct PositionEntryForm {
    var code = ""
    var name = ""
    var price = ""
    var qty = ""
    var reason = ""

    var buyPrice: Double? { Double(price.trimmingCharacters(in: .whitespaces)) }
    var qtyInt: Int? { Int(qty.trimmingCharacters(in: .whitespaces)) }
    var isValid: Bool {
        !code.trimmingCharacters(in: .whitespaces).isEmpty
            && (buyPrice ?? 0) > 0
            && (qtyInt ?? 0) > 0
            && !reason.trimmingCharacters(in: .whitespaces).isEmpty
    }
}

enum PositionModal: Equatable { case open, close(code: String) }

struct Toast: Identifiable, Equatable {
    let id = UUID()
    let message: String
    var isError: Bool = false
}

@MainActor
@Observable
final class AppModel {
    // —— 导航 ——
    var view: AppTab = .today

    // —— 4A.2 今日计划:报告 ——
    var report: ReportSnapshot = .empty(reason: "not_loaded")
    var reportLoading = false

    // —— 4A.4 今日计划:持仓 ——
    var positions: [Position] = []
    var positionsLoading = false
    var loadError: String? = nil

    // —— 4A.3 盘中看板 ——
    var board: BoardSnapshot = .empty
    var boardLoading = false

    // —— 4A.5 问询台(§2.5:裁决二值,任何时候都不存在「买」路径)——
    var inquiryCode: String = ""
    var inquiryThread: [ChatMessage] = []
    var inquiryVerdict: InquiryVerdict? = nil
    var inquiryEvidence: [String] = []
    var inquiryDegraded = false
    var inquiryComposer = ""
    var inquiryLoading = false

    // —— 4A.5 设置 ——
    var settings: SettingsSnapshot = .empty
    var settingsLoading = false
    var llmProviderDraft: LLMProviderKind = .glm
    var llmKeyDraft: String = ""          // 安全态:从不用存量 key 预填,只在本次填写时持有
    var pushReportDraft: Bool = true
    var pushRetreatDraft: Bool = true

    // —— 模态 / 录入 / toast ——
    var modal: PositionModal? = nil
    var entryForm = PositionEntryForm()
    var closeSellPrice = ""
    var toast: Toast? = nil

    // —— 依赖(运行期注入)——
    let calendar = StaticTradingCalendar.shared
    private var clientProvider: () -> APIClient?
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
    }

    // MARK: - 派生(纯逻辑,单测覆盖)

    /// 退潮刹车激活时的今日计划状态提示(§2.4「今日计划作废、禁开新仓」)。
    /// **只警示、不硬拦**——开仓录入是补记用户已在券商完成的真实操作(审计台账,
    /// §3.8「系统永不自动下单」),硬拦会变成帮用户瞒报真实操作,故只做醒目提醒。
    var retreatWarning: String? {
        guard board.retreatBrake.active else { return nil }
        let base = "退潮红色刹车已触发 · 今日计划作废、不建议开新仓"
        guard !board.retreatBrake.reason.isEmpty else { return base }
        return base + "(依据:\(board.retreatBrake.reason))"
    }

    var hasReportData: Bool { !report.degraded && !report.tradeDate.isEmpty }
    var quota: PositionQuota? { report.sentiment.map { PositionQuota($0.positionQuota) } }

    func position(byID id: Int) -> Position? { positions.first(where: { $0.id == id }) }

    // MARK: - 4A.2/4A.4:今日计划刷新

    /// 今日计划刷新:报告 + 持仓 + 看板(三者并发)。看板也在此拉一份是刻意的——
    /// 「退潮红色刹车禁开新仓」的警示要在用户点「开仓」之前就可见(`retreatWarning`
    /// 派生自 `board`),不能等用户先手动切到盘中看板才看到,否则警示形同虚设。
    func refresh() async {
        guard let client = clientProvider() else {
            loadError = "未配置后端连接"
            return
        }
        reportLoading = true
        positionsLoading = true
        loadError = nil
        async let reportTask: Result<ReportSnapshot, Error> = fetchResult { try await client.fetchReportLatest() }
        async let positionsTask: Result<[Position], Error> = fetchResult { try await client.fetchPositions() }
        async let boardTask: Result<BoardSnapshot, Error> = fetchResult { try await client.fetchBoard() }
        let (reportResult, positionsResult, boardResult) = await (reportTask, positionsTask, boardTask)

        switch reportResult {
        case .success(let r): self.report = r
        case .failure(let e): handleLoadFailure(e, context: "报告")
        }
        switch positionsResult {
        case .success(let p): self.positions = p
        case .failure(let e): handleLoadFailure(e, context: "持仓")
        }
        switch boardResult {
        case .success(let b): self.board = b
        case .failure: break   // 看板降级不弹错(今日计划的主内容是报告+持仓,看板只为警示条服务)
        }
        reportLoading = false
        positionsLoading = false
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

    // MARK: - 4A.3:盘中看板

    func loadBoard() async {
        guard let client = clientProvider() else { return }
        boardLoading = true
        do {
            self.board = try await client.fetchBoard()
        } catch let e as APIError {
            if case .noToken = e {} else { showToast(e.errorDescription ?? "看板拉取失败", isError: true) }
        } catch {
            showToast("看板拉取失败", isError: true)
        }
        boardLoading = false
    }

    // MARK: - 4A.4:开仓 / 清仓(审计台账,永不代下单)

    func openEntrySheet() {
        entryForm = PositionEntryForm()
        modal = .open
    }

    func openCloseSheet(code: String) {
        guard let pos = positions.first(where: { $0.code == code }) else { return }
        closeSellPrice = pos.hasLivePrice ? String(format: "%.2f", pos.price) : ""
        modal = .close(code: code)
    }

    func dismissModal() { modal = nil }

    func submitOpenPosition() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        guard entryForm.isValid, let price = entryForm.buyPrice, let qty = entryForm.qtyInt else {
            showToast("请完整填写代码/价格/数量/理由", isError: true); return
        }
        let code = entryForm.code.trimmingCharacters(in: .whitespaces)
        let name = entryForm.name.trimmingCharacters(in: .whitespaces)
        let reason = entryForm.reason.trimmingCharacters(in: .whitespaces)
        do {
            let r = try await client.openPosition(code: code, name: name.isEmpty ? nil : name,
                                                  buyPrice: price, qty: qty, entryReason: reason)
            dismissModal()
            await refresh()
            showToast("已录入开仓 · 止损线 \(String(format: "%.2f", r.stopLine))(-5%)")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "录入失败", isError: true)
        } catch {
            showToast("录入失败:\(error.localizedDescription)", isError: true)
        }
    }

    func submitClosePosition() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        guard case .close(let code) = modal, let pos = position(byCode: code) else {
            showToast("找不到该持仓", isError: true); return
        }
        guard let sell = Double(closeSellPrice.trimmingCharacters(in: .whitespaces)), sell > 0 else {
            showToast("请填写有效卖出价", isError: true); return
        }
        do {
            _ = try await client.closePosition(id: pos.id, sellPrice: sell)
            dismissModal()
            await refresh()
            showToast("已录入清仓")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "清仓失败", isError: true)
        } catch {
            showToast("清仓失败:\(error.localizedDescription)", isError: true)
        }
    }

    private func position(byCode code: String) -> Position? {
        positions.first(where: { $0.code == code })
    }

    // MARK: - 4A.5:问询台(§2.5 二值裁决;§2.7 自由对话体)

    /// 切换问询股票代码 → 清空上下文(每票独立对话,同后端"无状态、按 code 走"语义)。
    func startInquiry(code: String) {
        inquiryCode = code.trimmingCharacters(in: .whitespaces)
        inquiryThread = []
        inquiryVerdict = nil
        inquiryEvidence = []
        inquiryDegraded = false
    }

    func sendInquiryComposer() async {
        let text = inquiryComposer.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !inquiryCode.isEmpty else { return }
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        inquiryThread.append(ChatMessage(role: .user, text: text))
        inquiryComposer = ""
        inquiryLoading = true
        do {
            let payload = Self.inquiryContext(from: inquiryThread)
            let r = try await client.sendInquiry(code: inquiryCode, messages: payload)
            inquiryThread.append(ChatMessage(role: .assistant, text: r.reply))
            inquiryVerdict = r.verdict
            inquiryEvidence = r.evidence
            inquiryDegraded = r.degraded
        } catch let e as APIError {
            inquiryThread.append(ChatMessage(role: .assistant, text: "问询失败:\(e.errorDescription ?? "未知错误")"))
        } catch {
            inquiryThread.append(ChatMessage(role: .assistant, text: "问询失败:\(error.localizedDescription)"))
        }
        inquiryLoading = false
    }

    /// 多轮上下文回传(纯函数,单测覆盖):保留最近 16 条、从 user 边界截起,
    /// 继承 LinoN `/chat` 客户端持有上下文姿势(§2.5「客户端回传」)。
    static func inquiryContext(from thread: [ChatMessage], maxCount: Int = 16) -> [ChatMessage] {
        guard thread.count > maxCount else { return thread }
        var truncated = Array(thread.suffix(maxCount))
        while let first = truncated.first, first.role != .user {
            truncated.removeFirst()
        }
        return truncated
    }

    // MARK: - 4A.5:设置

    func loadSettings() async {
        guard let client = clientProvider() else { return }
        settingsLoading = true
        do {
            let s = try await client.fetchSettings()
            self.settings = s
            self.llmProviderDraft = LLMProviderKind(rawValue: s.llmProvider ?? "") ?? .glm
            self.pushReportDraft = s.push.report
            self.pushRetreatDraft = s.push.retreatBrake
        } catch let e as APIError {
            if case .noToken = e {} else { showToast("设置拉取失败", isError: true) }
        } catch {
            showToast("设置拉取失败", isError: true)
        }
        settingsLoading = false
    }

    /// 保存 LLM 供应商 + key。**key 只在用户本次填写非空时才发送**,发送后立即清空
    /// 草稿(安全态,不回显存量 key,§五 阶段4C「key 输入框安全态」)。
    func saveLLMSettings() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        let key = llmKeyDraft.trimmingCharacters(in: .whitespaces)
        guard !key.isEmpty else {
            showToast("请填写 API key", isError: true); return
        }
        do {
            _ = try await client.putSettingsLLM(provider: llmProviderDraft, apiKey: key)
            llmKeyDraft = ""
            await loadSettings()
            showToast("LLM 设置已保存 · 运行时生效")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "保存失败", isError: true)
        } catch {
            showToast("保存失败:\(error.localizedDescription)", isError: true)
        }
    }

    func savePushSettings() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        do {
            _ = try await client.putSettingsPush(report: pushReportDraft, retreatBrake: pushRetreatDraft)
            await loadSettings()
            showToast("推送设置已保存")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "保存失败", isError: true)
        } catch {
            showToast("保存失败:\(error.localizedDescription)", isError: true)
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
