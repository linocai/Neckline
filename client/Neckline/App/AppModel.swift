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
    // v1.1-F.1:自选独立第五板块(iPhone 5 tab = 今日计划/盘中看板/自选/问询台/设置,
    // 顺序即 TabBar 顺序;review 只在 macOS 侧栏,非 iOS TabBar 成员)。
    case today, board, watchlist, inquiry, settings, review
    var id: String { rawValue }
    var title: String {
        switch self {
        case .today: return "今日计划"
        case .board: return "盘中看板"
        case .watchlist: return "自选"
        case .inquiry: return "问询台"
        case .settings: return "设置"
        case .review: return "周复盘工作台"
        }
    }
    var systemImage: String {
        switch self {
        case .today: return "list.bullet.clipboard"
        case .board: return "waveform.path.ecg"
        case .watchlist: return "star.fill"
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

// —— v1.2-B 决策日志八项录入草稿(§五 v1.2-E.1)——————————————————————————————
//
// 嵌「已按计划买入」补录流程之前(建计划 → 录八项 → 成交后一键关联,见
// `AppModel.beginPositionEntryFlow`)。**决策日志强制度 = 软约束**(§三条本版硬
// 约束②)——表单只是「默认先走」,不做硬阻断,`skipDecisionLog()` 提供跳过出口。

/// ⑦ 应对方案·情景树单行的可编辑草稿(带稳定 `id` 供 SwiftUI `ForEach` 增删)。
/// 提交前转换成服务端契约的 `ContingencyScenario`(Models.swift),`matched` 恒
/// 从 `false` 起(结果标记是次日复盘的事,见 `AppModel.toggleScenarioOutcome`)。
struct ContingencyScenarioDraft: Identifiable, Equatable {
    let id = UUID()
    var scenario = ""
    var trigger = ""
    var action: ScenarioAction = .hold
}

struct DecisionLogForm {
    var code = ""
    var name = ""
    var whyBuy = ""              // ① 为什么买
    var whyEntryPrice = ""       // ② 为什么这个入场价
    var targetPrice = ""         // ③ 目标价(可选)
    var exitLow = ""             // ④ 离场价格区间
    var exitHigh = ""
    var thesisTags: Set<ThesisTag> = []   // ⑤ 论点标签(多选)
    var invalidation = ""        // ⑥ 证伪条件
    var scenarios: [ContingencyScenarioDraft] =
        [ContingencyScenarioDraft(), ContingencyScenarioDraft()]   // ⑦ 引导 2-3 行,服务端不强制条数
    var playbookTag: PlaybookTag = .swingChase   // ⑧ 打法标签(单选)
    var plannedPrice = ""
    var plannedQty = ""

    init() {}

    /// 修订模式预填(从已存在的 `DecisionLog` 构造草稿)。
    init(from log: DecisionLog) {
        code = log.code
        name = log.name
        whyBuy = log.whyBuy
        whyEntryPrice = log.whyEntryPrice
        targetPrice = log.targetPrice.map { String(format: "%.2f", $0) } ?? ""
        exitLow = log.exitLow.map { String(format: "%.2f", $0) } ?? ""
        exitHigh = log.exitHigh.map { String(format: "%.2f", $0) } ?? ""
        thesisTags = Set(log.thesisTags.compactMap { ThesisTag(rawValue: $0) })
        invalidation = log.invalidation
        let restored = log.contingencyScenarios.map {
            ContingencyScenarioDraft(scenario: $0.scenario, trigger: $0.trigger,
                                     action: ScenarioAction(rawValue: $0.action) ?? .hold)
        }
        scenarios = restored.isEmpty ? [ContingencyScenarioDraft(), ContingencyScenarioDraft()] : restored
        playbookTag = PlaybookTag(rawValue: log.playbookTag) ?? .swingChase
        plannedPrice = log.plannedPrice.map { String(format: "%.2f", $0) } ?? ""
        plannedQty = log.plannedQty.map { String($0) } ?? ""
    }

    var isValid: Bool {
        !code.trimmingCharacters(in: .whitespaces).isEmpty
            && !whyBuy.trimmingCharacters(in: .whitespaces).isEmpty
            && !whyEntryPrice.trimmingCharacters(in: .whitespaces).isEmpty
            && !invalidation.trimmingCharacters(in: .whitespaces).isEmpty
    }

    /// 只提交「情景描述 + 触发条件」都非空的行(服务端不强制条数,留白的引导行
    /// 不当垃圾数据提交)。
    var filledScenarios: [ContingencyScenarioDraft] {
        scenarios.filter {
            !$0.scenario.trimmingCharacters(in: .whitespaces).isEmpty
                && !$0.trigger.trimmingCharacters(in: .whitespaces).isEmpty
        }
    }
}

// —— v1.2-G 呼吸 T 台账录入草稿(§五 v1.2-E.4)———————————————————————————————

struct BreathingTradeForm {
    var buyPrice = ""
    var sellPrice = ""
    var qty = ""
    var fees = ""     // 必填(§G.2「不替用户估费率」),留空视为无效,不代入 0
    var note = ""

    var buyPriceValue: Double? { Double(buyPrice.trimmingCharacters(in: .whitespaces)) }
    var sellPriceValue: Double? { Double(sellPrice.trimmingCharacters(in: .whitespaces)) }
    var qtyValue: Int? { Int(qty.trimmingCharacters(in: .whitespaces)) }
    var feesValue: Double? { Double(fees.trimmingCharacters(in: .whitespaces)) }

    var isValid: Bool {
        (buyPriceValue ?? 0) > 0 && (sellPriceValue ?? 0) > 0 && (qtyValue ?? 0) > 0
            && (feesValue ?? -1) >= 0
    }
}

enum PositionModal: Equatable {
    case decisionLog                  // v1.2-E.1:八项录入(建计划 → 录八项 → 成交后关联)
    case open
    case close(code: String)
    case circuitReview                // v1.2-E.3:熔断复盘材料 + 解锁
    case breathing(positionId: Int)   // v1.2-E.4:呼吸 T 台账
}

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
    // v1.1-G.1:推送开关扩到四类(盘前校准 / D5 时间退出)。
    var pushPrecallDraft: Bool = true
    var pushD5exitDraft: Bool = true
    // v1.2-A2:第五类(熔断提醒),默认开。
    var pushCircuitDraft: Bool = true

    // —— v1.1-F 自选板块(watchlist)——
    var watchlist: WatchlistSnapshot = .empty
    var watchlistLoading = false
    var thsReconcileResult: ThsReconcileResult? = nil
    var thsReconcileLoading = false

    // —— 4D 周复盘工作台(拖入交割单对账;macOS 独有,§五 阶段4D)——
    var reviewWeeks: [WeeklyReviewEntry] = []
    var reviewSelectedWeek: String? = nil
    var reviewParseWarnings: [String] = []
    var reviewDataWarnings: [String] = []
    var reviewUploading = false
    var reviewHasUploaded = false   // 区分"从未上传过"与"上传过但没解析出交易"两种空态

    // —— v1.2-B 预注册决策日志(§五 v1.2-E.1;审计件、非下单件)——
    /// 仅 `status=filled`(已成交关联)的决策日志——持仓卡回显 / 呼吸台账入口露出
    /// 规则只需要「这个持仓关联了哪条决策」,pending/cancelled 的不必常驻内存。
    var decisions: [DecisionLog] = []
    var decisionForm = DecisionLogForm()
    /// 建计划 → 录八项 → 成交后一键关联,期间的暂存 id;`.open` 阶段提交成功后
    /// 用它调 `link`,用户中途放弃则用它调 `cancel`(见 `dismissModal()`)。
    var pendingDecisionId: Int? = nil
    /// 非 nil = 表单处于「修订」模式(提交调 `reviseDecision`,不触发开仓流程)。
    var revisingDecisionId: Int? = nil

    // —— v1.2-A2 熔断纪律(§五 v1.2-E.3;纯提醒层,客户端只展示 + 自律灰化,§3.8)——
    var circuit: CircuitState = .empty

    // —— v1.2-G 呼吸试验仓台账(§五 v1.2-E.4)——
    var breathingLedger: BreathingLedger = .empty
    var breathingLoading = false
    var breathingTradeForm = BreathingTradeForm()

    // —— 模态 / 录入 / toast ——
    var modal: PositionModal? = nil
    var entryForm = PositionEntryForm()
    var closeSellPrice = ""
    var closeReasonDraft: CloseReasonCode? = nil   // v1.2-A2:不选 → 服务端 NULL + 价格兜底
    /// v1.2-E.5 改区间双档(替换 v1.1 的单 qty/单 stopLine 预填提示):来自
    /// `GET /positions/entry-suggestion`,只读展示,不参与提交——真正的止损线以
    /// 提交后服务端按实际买入价返回的为准,真正的数量由用户自己拍板。
    var entrySuggestionRange: EntrySuggestionRange? = nil
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

    /// 该持仓关联的决策日志(若有;取该 positionId 下 id 最大的一行——正常情况下
    /// 一个持仓只会被 `link` 一次,取 max 是防御性写法)。§五 v1.2-E「入口露出规则」
    /// 据此判断 `playbookTag == BREATHING_TRIAL` 是否在持仓卡露出呼吸台账入口。
    func linkedDecision(forPositionId id: Int) -> DecisionLog? {
        decisions.filter { $0.positionId == id }.max(by: { $0.id < $1.id })
    }

    // MARK: - 4A.2/4A.4:今日计划刷新

    /// 今日计划刷新:报告 + 持仓 + 看板 + 熔断态 + 已成交决策日志(五者并发)。看板 /
    /// 熔断态也在此拉一份是刻意的——「退潮红色刹车禁开新仓」「熔断中停开新仓」的
    /// 警示要在用户点「开仓」之前就可见,不能等用户先手动切页才看到,否则形同虚设。
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
        async let circuitTask: Result<CircuitState, Error> = fetchResult { try await client.getCircuit() }
        async let decisionsTask: Result<[DecisionLog], Error> = fetchResult { try await client.listDecisions(status: "filled") }
        let (reportResult, positionsResult, boardResult, circuitResult, decisionsResult) =
            await (reportTask, positionsTask, boardTask, circuitTask, decisionsTask)

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
        switch circuitResult {
        case .success(let c): self.circuit = c
        case .failure: break   // 熔断态读取失败保留上次已知态,不拿失败静默重置成"未锁定"
        }
        switch decisionsResult {
        case .success(let d): self.decisions = d
        case .failure: break   // 同看板降级:决策日志回显失败不弹错,只是持仓卡少一节
        }
        reportLoading = false
        positionsLoading = false
    }

    /// 单独刷新已成交决策日志(revise / scenario-outcome 后局部更新,不必整页 refresh)。
    func loadDecisions() async {
        guard let client = clientProvider() else { return }
        do { decisions = try await client.listDecisions(status: "filled") } catch { /* 静默降级,同 board */ }
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
    //
    // v1.2-E.1:「已按计划买入」补录流程之前插入决策日志录入——`beginPositionEntryFlow`
    // 系列函数现在先开 `.decisionLog` 表单(建计划 → 录八项),而非直接开 `.open`
    // (旧名 `openEntrySheet` 已按新语义改名)。**决策日志强制度 = 软约束**(§三条
    // 本版硬约束②):`skipDecisionLog()` 提供「跳过,直接补录开仓」出口,不做硬阻断。

    /// 手动补录(无候选来源;今日计划持仓区「补录开仓」按钮)。
    func beginPositionEntryFlow() {
        entryForm = PositionEntryForm()
        decisionForm = DecisionLogForm()
        entrySuggestionRange = nil
        pendingDecisionId = nil
        revisingDecisionId = nil
        modal = .decisionLog
    }

    /// v1.1-E.2 先例的候选卡「买入补录」入口——预填 code/name(来自候选)、参考价
    /// (候选买点参考价,来自 `Candidate.entrySpec`,取不到则留空手填)、区间双档推荐
    /// (`GET /positions/entry-suggestion`,v1.2-E.5 改区间,只读展示,不预填具体数量
    /// ——不替用户拍单笔金额)。只算一次(打开时),提交仍走既有 `POST /positions`。
    func beginPositionEntryFlow(fromCandidate candidate: Candidate) async {
        entryForm = PositionEntryForm()
        decisionForm = DecisionLogForm()
        entrySuggestionRange = nil
        pendingDecisionId = nil
        revisingDecisionId = nil
        entryForm.code = candidate.code
        entryForm.name = candidate.name
        entryForm.reason = "已按计划买入 · \(candidate.buyPoint)"
        decisionForm.code = candidate.code
        decisionForm.name = candidate.name
        if let refPrice = candidate.entrySpec?.referencePrice, refPrice > 0 {
            let priceStr = String(format: "%.2f", refPrice)
            entryForm.price = priceStr
            decisionForm.plannedPrice = priceStr
            if let client = clientProvider() {
                do {
                    entrySuggestionRange = try await client.entrySuggestion(code: candidate.code, price: refPrice)
                } catch {
                    // 拉不到区间推荐 → 留给用户手填,不崩、不显示编造的数字(同 v1.1-E.2 先例)。
                }
            }
        }
        modal = .decisionLog
    }

    func openCloseSheet(code: String) {
        guard let pos = positions.first(where: { $0.code == code }) else { return }
        closeSellPrice = pos.hasLivePrice ? String(format: "%.2f", pos.price) : ""
        closeReasonDraft = nil
        modal = .close(code: code)
    }

    /// 关闭任意模态。**`.open` 阶段若还留有未关联的 `pendingDecisionId`**(用户在
    /// 决策日志之后中途放弃了这次开仓)**→ 自动 `cancel` 该预注册计划**(§五 v1.2-E.1
    /// 「放弃 → POST /decisions/{id}/cancel」),不留孤儿 pending 行;成功开仓 + 关联
    /// 后 `pendingDecisionId` 已在 `submitOpenPosition()` 里清空,此处不会重复触发。
    func dismissModal() {
        if modal == .open, let did = pendingDecisionId, let client = clientProvider() {
            Task { _ = try? await client.cancelDecision(id: did) }
        }
        pendingDecisionId = nil
        revisingDecisionId = nil
        modal = nil
    }

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
            if let did = pendingDecisionId {
                pendingDecisionId = nil
                // 关联失败不阻断开仓成功(审计件、非下单件,§3.8);决策日志仍留在
                // pending 态供事后排查,不静默吞错误(showToast 提示但不回滚开仓)。
                do { _ = try await client.linkDecision(id: did, positionId: r.positionId) }
                catch { showToast("开仓已录入,但决策日志关联失败(可稍后在持仓卡重试)", isError: true) }
            }
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
            _ = try await client.closePosition(id: pos.id, sellPrice: sell, closeReason: closeReasonDraft?.rawValue)
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

    // MARK: - v1.2-B 决策日志(§五 v1.2-E.1;审计件、非下单件——本节任何函数都不
    // 触发任何下单 / 开仓 / 撤单动作,只落决策日志表)。

    /// 修订入口(持仓卡决策日志回显区「修订」按钮)。⚠ 后端 `revise_decision` 新增
    /// 的修订行 `positionId`/`status` 会重置(不继承原行的成交关联)——修订语义上
    /// 更贴近「预注册阶段的计划微调」,不是「事后改写已执行的审计记录」,故本次
    /// 只暴露 API 方法,未在 UI 挂常规入口(报告里已如实说明,见交付说明)。此函数
    /// 保留供后续块直接复用(表单预填 + 修订态提交路径已完整可用)。
    func beginReviseDecision(_ log: DecisionLog) {
        decisionForm = DecisionLogForm(from: log)
        revisingDecisionId = log.id
        entrySuggestionRange = nil
        modal = .decisionLog
    }

    /// 跳过决策日志,直接进入「补录开仓」(§三条本版硬约束②:决策日志是软约束,
    /// 不做硬阻断——硬阻断会逼出假日志)。已填的代码/名称顺带带过去,不必重打。
    func skipDecisionLog() {
        let code = decisionForm.code.trimmingCharacters(in: .whitespaces)
        if !code.isEmpty {
            entryForm.code = code
            entryForm.name = decisionForm.name.trimmingCharacters(in: .whitespaces)
        }
        pendingDecisionId = nil
        revisingDecisionId = nil
        modal = .open
    }

    /// 提交决策日志表单。两种模式:①创建(`revisingDecisionId == nil`)→ 预注册
    /// 成功后转入 `.open`(暂存 `pendingDecisionId`,成交后由 `submitOpenPosition()`
    /// 一键关联);②修订(`revisingDecisionId != nil`)→ 新增修订行、原行原地不变,
    /// 提交后直接关闭表单(不涉及开仓流程)。
    func submitDecisionLog() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        guard decisionForm.isValid else {
            showToast("请完整填写代码 / 为什么买 / 为什么这个入场价 / 证伪条件", isError: true); return
        }
        let tags = decisionForm.thesisTags.map(\.rawValue)
        let scenarios = decisionForm.filledScenarios.map {
            ContingencyScenario(scenario: $0.scenario.trimmingCharacters(in: .whitespaces),
                                trigger: $0.trigger.trimmingCharacters(in: .whitespaces),
                                action: $0.action.rawValue, matched: false)
        }
        let targetPrice = Double(decisionForm.targetPrice.trimmingCharacters(in: .whitespaces))
        let exitLow = Double(decisionForm.exitLow.trimmingCharacters(in: .whitespaces))
        let exitHigh = Double(decisionForm.exitHigh.trimmingCharacters(in: .whitespaces))
        let plannedPrice = Double(decisionForm.plannedPrice.trimmingCharacters(in: .whitespaces))
        let plannedQty = Int(decisionForm.plannedQty.trimmingCharacters(in: .whitespaces))
        do {
            if let did = revisingDecisionId {
                _ = try await client.reviseDecision(
                    id: did, whyBuy: decisionForm.whyBuy, whyEntryPrice: decisionForm.whyEntryPrice,
                    targetPrice: targetPrice, exitLow: exitLow, exitHigh: exitHigh, thesisTags: tags,
                    invalidation: decisionForm.invalidation, contingencyScenarios: scenarios,
                    playbookTag: decisionForm.playbookTag.rawValue, plannedPrice: plannedPrice, plannedQty: plannedQty
                )
                revisingDecisionId = nil
                modal = nil
                await loadDecisions()
                showToast("决策日志已修订(新增修订行,原记录保留不变)")
            } else {
                let code = decisionForm.code.trimmingCharacters(in: .whitespaces)
                let name = decisionForm.name.trimmingCharacters(in: .whitespaces)
                let log = try await client.createDecision(
                    code: code, name: name.isEmpty ? nil : name,
                    whyBuy: decisionForm.whyBuy, whyEntryPrice: decisionForm.whyEntryPrice,
                    targetPrice: targetPrice, exitLow: exitLow, exitHigh: exitHigh, thesisTags: tags,
                    invalidation: decisionForm.invalidation, contingencyScenarios: scenarios,
                    playbookTag: decisionForm.playbookTag.rawValue, plannedPrice: plannedPrice, plannedQty: plannedQty
                )
                pendingDecisionId = log.id
                entryForm.code = code
                entryForm.name = name
                if entryForm.price.trimmingCharacters(in: .whitespaces).isEmpty, let pp = plannedPrice {
                    entryForm.price = String(format: "%.2f", pp)
                }
                modal = .open
                showToast("预注册计划已提交 · 请在成交后补录实际开仓")
            }
        } catch let e as APIError {
            showToast(e.errorDescription ?? "提交失败", isError: true)
        } catch {
            showToast("提交失败:\(error.localizedDescription)", isError: true)
        }
    }

    /// ⑦ 情景树结果标记(次日复盘勾选情景兑现)。只翻 `matched`,情景文本 UI 上只读
    /// 不可改(§五 v1.2-E.1「情景文本 UI 上只读、不可改」硬边界)。
    func toggleScenarioOutcome(decisionId: Int, index: Int, matched: Bool) async {
        guard let client = clientProvider() else { return }
        do {
            _ = try await client.setScenarioOutcome(id: decisionId, outcomes: [(index: index, matched: matched)])
            await loadDecisions()
        } catch let e as APIError {
            showToast(e.errorDescription ?? "更新失败", isError: true)
        } catch {
            showToast("更新失败:\(error.localizedDescription)", isError: true)
        }
    }

    // MARK: - v1.2-A2 熔断纪律(§五 v1.2-E.3;纯提醒层——本节绝不代下单 / 撤单,
    // 服务端也绝不拦 `POST /positions`,客户端只做自律灰化 + 状态展示,§3.8)。

    /// 「熔断复盘」按钮:确认已阅读触发材料 → 解锁。系统无法验证用户「真的复盘
    /// 了」,但强制把材料摆到面前(`CircuitReviewSheet` 展示 `episode` 全部字段)
    /// + 记录本次确认(`unlocked_via="review_ack"`,服务端落库)。
    func confirmCircuitReview() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        do {
            _ = try await client.unlockCircuit()
            circuit = try await client.getCircuit()
            dismissModal()
            showToast("已解锁 · 可继续开新仓")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "解锁失败", isError: true)
        } catch {
            showToast("解锁失败:\(error.localizedDescription)", isError: true)
        }
    }

    // MARK: - v1.2-G 呼吸试验仓台账(§五 v1.2-E.4;写入只经这三个函数对应的端点,
    // 同 positions/watchlist 姿势,不存在任何自动写路径)。

    func openBreathingSheet(positionId: Int) {
        breathingLedger = .empty
        breathingTradeForm = BreathingTradeForm()
        modal = .breathing(positionId: positionId)
    }

    func loadBreathingLedger(positionId: Int) async {
        guard let client = clientProvider() else { return }
        breathingLoading = true
        do {
            breathingLedger = try await client.breathingTrades(positionId: positionId)
        } catch let e as APIError {
            showToast(e.errorDescription ?? "呼吸台账拉取失败", isError: true)
        } catch {
            showToast("呼吸台账拉取失败", isError: true)
        }
        breathingLoading = false
    }

    /// 录入一次 T。`fees` 必填、如实录入(不替用户估费率,G.2)。
    func submitBreathingTrade(positionId: Int) async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        guard breathingTradeForm.isValid,
              let buy = breathingTradeForm.buyPriceValue, let sell = breathingTradeForm.sellPriceValue,
              let qty = breathingTradeForm.qtyValue, let fees = breathingTradeForm.feesValue else {
            showToast("请完整填写买价/卖价/数量/费用", isError: true); return
        }
        do {
            _ = try await client.addBreathingTrade(
                positionId: positionId, buyPrice: buy, sellPrice: sell, qty: qty, fees: fees, tDate: nil,
                note: breathingTradeForm.note.trimmingCharacters(in: .whitespaces).isEmpty
                    ? nil : breathingTradeForm.note
            )
            breathingTradeForm = BreathingTradeForm()
            await loadBreathingLedger(positionId: positionId)
            showToast("已记一笔 T")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "记录失败", isError: true)
        } catch {
            showToast("记录失败:\(error.localizedDescription)", isError: true)
        }
    }

    /// 误录可删(硬删除)。
    func deleteBreathingTrade(id: Int, positionId: Int) async {
        guard let client = clientProvider() else { return }
        do {
            _ = try await client.deleteBreathingTrade(id: id)
            await loadBreathingLedger(positionId: positionId)
            showToast("已删除")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "删除失败", isError: true)
        } catch {
            showToast("删除失败:\(error.localizedDescription)", isError: true)
        }
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
            self.pushPrecallDraft = s.push.precall
            self.pushD5exitDraft = s.push.d5exit
            self.pushCircuitDraft = s.push.circuit
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

    /// 推送开关五类一并写入(报告 / 退潮刹车 / 盘前校准 / D5 时间退出 / v1.2-A2 熔断提醒)。
    func savePushSettings() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        do {
            _ = try await client.putSettingsPush(report: pushReportDraft, retreatBrake: pushRetreatDraft,
                                                 precall: pushPrecallDraft, d5exit: pushD5exitDraft,
                                                 circuit: pushCircuitDraft)
            await loadSettings()
            showToast("推送设置已保存")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "保存失败", isError: true)
        } catch {
            showToast("保存失败:\(error.localizedDescription)", isError: true)
        }
    }

    // MARK: - v1.1-F:自选板块(增删只由用户显式操作触发,本模型不含任何自动增删路径)

    func loadWatchlist() async {
        guard let client = clientProvider() else { return }
        watchlistLoading = true
        do {
            watchlist = try await client.fetchWatchlist()
        } catch let e as APIError {
            if case .noToken = e {} else { showToast(e.errorDescription ?? "自选拉取失败", isError: true) }
        } catch {
            showToast("自选拉取失败", isError: true)
        }
        watchlistLoading = false
    }

    /// 通用「+自选」入口(候选卡 / 问询台裁决卡 / 自选板块自身「+自选」共用,plan F.3)。
    /// 满 30 上限 → 422 → 明确提示(不是静默失败);返回是否加入成功。
    @discardableResult
    func quickAddWatchlist(code: String, name: String? = nil) async -> Bool {
        let trimmedCode = code.trimmingCharacters(in: .whitespaces)
        guard !trimmedCode.isEmpty else { return false }
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return false
        }
        do {
            _ = try await client.addWatchlist(code: trimmedCode, name: name)
            await loadWatchlist()
            showToast("已加入自选")
            return true
        } catch APIError.validation(let reason) where reason.contains("watchlist_full") {
            showToast("自选池已满(≤\(watchlist.maxSize) 只),请先移除再添加", isError: true)
            return false
        } catch let e as APIError {
            showToast(e.errorDescription ?? "加入自选失败", isError: true)
            return false
        } catch {
            showToast("加入自选失败:\(error.localizedDescription)", isError: true)
            return false
        }
    }

    func removeFromWatchlist(code: String) async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        do {
            _ = try await client.removeWatchlist(code: code)
            await loadWatchlist()
            showToast("已从自选移除")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "移除失败", isError: true)
        } catch {
            showToast("移除失败:\(error.localizedDescription)", isError: true)
        }
    }

    func toggleWatchlistPin(code: String, pinned: Bool) async {
        guard let client = clientProvider() else { return }
        do {
            _ = try await client.pinWatchlist(code: code, pinned: pinned)
            await loadWatchlist()
        } catch let e as APIError {
            showToast(e.errorDescription ?? "更新失败", isError: true)
        } catch {
            showToast("更新失败:\(error.localizedDescription)", isError: true)
        }
    }

    // —— v1.1-F.4:macOS 同花顺 txt 对账工作台(iOS 不做,§C.4/F.4)——————————————

    func reconcileThsFile(filename: String, data: Data) async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        thsReconcileLoading = true
        do {
            thsReconcileResult = try await client.reconcileThs(filename: filename, data: data)
        } catch let e as APIError {
            showToast(e.errorDescription ?? "对账失败", isError: true)
        } catch {
            showToast("对账失败:\(error.localizedDescription)", isError: true)
        }
        thsReconcileLoading = false
    }

    /// 一键对齐(plan C.4「对齐动作由客户端按差异调 C.1 CRUD」,后端对账端点本身不写入)。
    /// 逐项独立 try,单项失败(如撞上 30 只上限)不拖累其它项,结束后汇总提示 + 刷新。
    func applyThsAlignment() async {
        guard let client = clientProvider(), let diff = thsReconcileResult else { return }
        var failed = 0
        for code in diff.onlyInThs {
            do { _ = try await client.addWatchlist(code: code) } catch { failed += 1 }
        }
        for code in diff.onlyInNeckline {
            do { _ = try await client.removeWatchlist(code: code) } catch { failed += 1 }
        }
        await loadWatchlist()
        thsReconcileResult = nil
        showToast(failed == 0 ? "已按同花顺自选对齐" : "对齐完成,\(failed) 项失败(可能已达上限)", isError: failed > 0)
    }

    func exportThsText() async -> String? {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return nil
        }
        do {
            return try await client.exportThs().text
        } catch let e as APIError {
            showToast(e.errorDescription ?? "导出失败", isError: true)
            return nil
        } catch {
            showToast("导出失败:\(error.localizedDescription)", isError: true)
            return nil
        }
    }

    // MARK: - 4D:周复盘工作台(对账逻辑全在后端 `neckline/review/`,本模型只装配/展示)

    /// 当前展示哪一周(默认最近解析出的一周;用户可用周切换器改选)。
    var selectedReviewEntry: WeeklyReviewEntry? {
        if let sel = reviewSelectedWeek, let hit = reviewWeeks.first(where: { $0.week == sel }) {
            return hit
        }
        return reviewWeeks.first
    }

    /// 拖入的文件(可能多份)一次性上传解析对账。`files`:(文件名,内容)对,由
    /// View 层从 `NSItemProvider` 读出(§五 阶段4D「客户端只负责拖文件上传与展示,
    /// 不重算任何判定」)。
    func uploadReviewFiles(_ files: [(filename: String, data: Data)]) async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        guard !files.isEmpty else { return }
        reviewUploading = true
        do {
            let resp = try await client.uploadReview(files: files)
            reviewHasUploaded = true
            reviewWeeks = resp.weeks.sorted { $0.week > $1.week }
            reviewSelectedWeek = reviewWeeks.first?.week
            reviewParseWarnings = resp.parseWarnings
            reviewDataWarnings = resp.dataWarnings
            if reviewWeeks.isEmpty {
                showToast("未解析出任何成交记录,请查看下方警告排查文件格式", isError: true)
            } else {
                showToast("对账完成 · 共 \(reviewWeeks.count) 周")
            }
        } catch let e as APIError {
            showToast(e.errorDescription ?? "上传失败", isError: true)
        } catch {
            showToast("上传失败:\(error.localizedDescription)", isError: true)
        }
        reviewUploading = false
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
