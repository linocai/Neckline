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
    // V2-⑬-11:自选 tab 随自选池整链删除(裁定 #9-a)→ iPhone 4 tab =
    // 今日计划/盘中看板/问询台/设置,顺序即 TabBar 顺序;review 只在 macOS 侧栏。
    // ⚠ ⑮ 会把 today 改造成「今日篮子」并重排信息架构(D8),本块只做删除。
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
    /// v1.3-①/⑥:实付买入费用(**UI 强制必填**,服务端宽松——§五 v1.3-⑥-B 拍板口径)。
    var buyFees = ""
    /// v1.4-①-A(§七 P0-1):真实买入日,默认今天、**不可选未来**(`OpenPositionSheet`
    /// 的 `DatePicker` 用 `in: ...Date()` 限死上界)。提交时格式化成 'YYYYMMDD' 传给
    /// `APIClient.openPosition(buyDate:)`;非交易日由服务端 400 拒绝,不在客户端预判
    /// (服务端才是交易日历的唯一事实源)。
    var buyDate: Date = Date()

    var buyPrice: Double? { Double(price.trimmingCharacters(in: .whitespaces)) }
    var qtyInt: Int? { Int(qty.trimmingCharacters(in: .whitespaces)) }
    var buyFeesValue: Double? { Double(buyFees.trimmingCharacters(in: .whitespaces)) }
    var isValid: Bool {
        !code.trimmingCharacters(in: .whitespaces).isEmpty
            && (buyPrice ?? 0) > 0
            && (qtyInt ?? 0) > 0
            && !reason.trimmingCharacters(in: .whitespaces).isEmpty
            && (buyFeesValue ?? -1) >= 0   // 必填且非负(允许 0——如实录入,不代表"不填")
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
    // —— ⑨ 最高追价上限(v1.4-⑤-B,需求 2 补充)———————————————————————————————————
    /// 相对昨收百分比数字(允许负值,如 `-1.5` = 只在低开 1.5% 以上才买)。
    var maxChasePct = ""
    /// 显式勾选「不设上限」——**无论开盘涨多高都照买,不设放弃线**(与
    /// `maxChasePct` 二选一强制,同论点必填纪律:两者皆无不许提交)。勾选时数字框
    /// 语义上被忽略(提交时以本开关为准,见 `AppModel.submitDecisionLog`)。
    var maxChaseNoCap = false

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
        // ⚠ 老行 `maxChasePct == nil` 有两种可能:「用户当年显式选了不设上限」或「建于
        // 本字段前的历史行」——存储层无法区分(schemas.py 原文已记)。**不自动预勾选
        // 「不设上限」**(那会替用户瞎猜当年的选择),两格都留白,强制用户修订时重新
        // 主动选择一次——修订本就是「重新预注册一整套九项内容,同一份纪律」。
        if let m = log.maxChasePct {
            maxChasePct = String(format: "%.2f", m)
            maxChaseNoCap = false
        } else {
            maxChasePct = ""
            maxChaseNoCap = false
        }
    }

    var maxChasePctValue: Double? { Double(maxChasePct.trimmingCharacters(in: .whitespaces)) }
    /// ⚠ **V2-⑬-5:强制表单退役** —— 服务端已在 ⑩-C 下线全部五项必填校验
    /// (`decision_log` 停写留档,`POST /decisions` 换血成「用户可选补充」入口,
    /// **不传五必填 → 200 而非 400**)。客户端的必填分支随之删除:`maxChaseChosen`
    /// 从「二选一强制」降级为**纯展示态**(勾了「不设上限」或填了数字都算已选,
    /// 用来决定要不要显示换算提示),⛔ 不再驱动任何提交拦截。
    var maxChaseChosen: Bool { maxChaseNoCap || maxChasePctValue != nil }

    /// ⚠ **V2-⑬-5**:原先要求 `code`/`whyBuy`/`whyEntryPrice`/`invalidation`/`maxChase`
    /// 五项齐全才准提交(硬约束②的"软阻断"落点)。表单强制度已整体退役 → 现在只保留
    /// **一条真硬前提**:没有 `code` 就无从记账。其余全部可空(空提交合法)。
    /// ⛔ 别把那四项加回来:「表单可选化 → 归因标签稀疏」这个代价已由用户拍板接受
    /// (裁定 #6,§七 P3-28 挂账),不是遗漏。
    var isValid: Bool {
        !code.trimmingCharacters(in: .whitespaces).isEmpty
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

enum PositionModal: Equatable {
    case decisionLog                  // v1.2-E.1:八项录入(建计划 → 录八项 → 成交后关联)
    case open
    case close(code: String)
    case circuitReview                // v1.2-E.3:熔断复盘材料 + 解锁
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

    // —— v1.4-④ 信息卡(§五 v1.4-④,依需求现算;候选专属,本版先只接候选)——————————
    /// 候选卡点进去要展示信息卡时的目标(携带整个 `Candidate`,不只是 code——
    /// (⑬-4 起执行提示位已删,信息卡页不再展示它)。
    struct InfoCardRequest: Identifiable, Equatable {
        let tradeDate: String
        let candidate: Candidate
        var id: String { "\(tradeDate)|\(candidate.code)" }
    }
    var infoCardRequest: InfoCardRequest? = nil
    var infoCard: InfoCard? = nil
    var infoCardLoading = false
    var infoCardError: String? = nil

    // —— 4A.3 盘中看板 ——
    var board: BoardSnapshot = .empty
    var boardLoading = false

    // —— 4A.5 问询台(§2.5:描述性标注非裁决,任何时候都不存在「买」路径)——
    var inquiryCode: String = ""
    var inquiryThread: [ChatMessage] = []
    var inquiryVerdict: InquiryVerdict? = nil
    var inquiryEvidence: [String] = []
    var inquiryDegraded = false
    var inquiryComposer = ""
    var inquiryLoading = false

    // —— v1.4-⑦-B 问询历史(§七 P3-13;与已退役的 `inquiry_pool` 无耦合)——————————————
    var inquiryHistory: [InquiryLogEntry] = []
    var inquiryHistoryLoading = false
    var showInquiryHistory = false
    /// 本次问询落进档案表的行 id(= `InquiryLogEntry.id` 的关联位,v1.4 review 契约线 🟡-3
    /// 补齐的末段)。**nil = 服务端落库失败(旁路,回答仍有效)或对端是老服务端**,不是
    /// 「没问过」;下一次提问前清空,免得把上一轮的 id 挂在这一轮头上。
    var lastInquiryId: Int? = nil

    // —— 4A.5 设置 ——
    var settings: SettingsSnapshot = .empty
    var settingsLoading = false
    /// v1.5-⑤-E:服务端版本(`GET /health` 的 `version`,免鉴权、独立于设置本身是否
    /// 拉取成功)。`nil` = 尚未拉到(网络失败 / 老服务端不带该字段),设置屏据此展示
    /// "服务端版本未知",不冒充"版本相同"。
    var serverVersion: String? = nil
    var llmProviderDraft: LLMProviderKind = .glm
    var llmKeyDraft: String = ""          // 安全态:从不用存量 key 预填,只在本次填写时持有
    var pushReportDraft: Bool = true
    var pushRetreatDraft: Bool = true
    // v1.1-G.1:推送开关扩到四类(盘前校准 / D5 时间退出)。
    var pushPrecallDraft: Bool = true
    var pushD5exitDraft: Bool = true
    // v1.2-A2:第五类(熔断提醒),默认开。
    var pushCircuitDraft: Bool = true
    // v1.3-②/⑥:第六类(K4 持仓派发警报),默认开,独立于 D5 时间退出通道。
    var pushHoldingAlertDraft: Bool = true

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

    // —— v1.4-⑦-A 挂单未成交追踪(§七 P3-12)。按 decisionId 缓存,持仓卡展开多张
    // 决策日志详情时各自独立、互不冲突;`decisionTrackLoading` 用 Set 记正在加载中的 id。
    var decisionTracks: [Int: DecisionTrack] = [:]
    var decisionTrackLoading: Set<Int> = []

    // —— v1.2-A2 熔断纪律(§五 v1.2-E.3;纯提醒层,客户端只展示 + 自律灰化,§3.8)——
    var circuit: CircuitState = .empty

    // —— 模态 / 录入 / toast ——
    var modal: PositionModal? = nil
    var entryForm = PositionEntryForm()
    var closeSellPrice = ""
    var closeReasonDraft: CloseReasonCode? = nil   // v1.2-A2:不选 → 服务端 NULL + 价格兜底
    /// v1.3-①/⑥:清仓实付卖出费用真数(可选,成交后回填;不选 → 服务端 NULL,D5 净浮盈
    /// 判向走估算公式,不影响清仓主流程)。
    var closeSellFees = ""
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
        maybeOpenInfoCardFromQAHook()
    }

    /// 纯 QA/截图辅助(同 `NecklineApp` 的 `NECKLINE_INITIAL_TAB`/`NECKLINE_INITIAL_MODAL`
    /// 先例):`NECKLINE_INITIAL_INFOCARD_CODE=<code>` 免交互地在报告加载后直接打开该
    /// 候选的信息卡页(info card 需要 `Candidate` 对象,拿不到候选对象时无法用同款
    /// 「init() 里同步设」的写法,故放在 `refresh()` 数据到位后触发)。不影响正常用户
    /// 路径(缺此环境变量则不触发);仅在候选列表里能找到匹配 code 时才生效。
    private func maybeOpenInfoCardFromQAHook() {
        guard let code = ProcessInfo.processInfo.environment["NECKLINE_INITIAL_INFOCARD_CODE"],
              !code.isEmpty, infoCardRequest == nil,
              let candidate = report.candidates.first(where: { $0.code == code }) else { return }
        openInfoCard(tradeDate: report.tradeDate, candidate: candidate)
    }

    /// 单独刷新已成交决策日志(revise / scenario-outcome 后局部更新,不必整页 refresh)。
    func loadDecisions() async {
        guard let client = clientProvider() else { return }
        do { decisions = try await client.listDecisions(status: "filled") } catch { /* 静默降级,同 board */ }
    }

    // MARK: - v1.4-④ 信息卡(依需求现算,不进 `refresh()` 常规刷新——单只完整卡含 60 日
    // 序列,体量不小,只在用户点开候选时才请求,§五 v1.4-④-B「不落库、服务端现算」)。

    /// 候选卡「查看信息卡」入口调用。携带整个 `Candidate`(而非只传 code)是刻意的
    /// ——⑬-4 起执行提示位已删,本页不再展示它;不为它
    /// 单独发一次请求、也不给 `InfoCardOut` 加字段(⑤ 留的待核对假设,已核对:本版
    /// 信息卡入口只有候选卡这一条路,假设成立)。
    func openInfoCard(tradeDate: String, candidate: Candidate) {
        infoCard = nil
        infoCardError = nil
        infoCardRequest = InfoCardRequest(tradeDate: tradeDate, candidate: candidate)
        Task { await loadInfoCard() }
    }

    func loadInfoCard() async {
        guard let req = infoCardRequest, let client = clientProvider() else { return }
        infoCardLoading = true
        do {
            infoCard = try await client.fetchInfoCard(date: req.tradeDate, code: req.candidate.code)
        } catch let e as APIError {
            infoCardError = e.errorDescription ?? "信息卡加载失败"
        } catch {
            infoCardError = error.localizedDescription
        }
        infoCardLoading = false
    }

    func dismissInfoCard() {
        infoCardRequest = nil
        infoCard = nil
        infoCardError = nil
        infoCardLoading = false
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
        // ⚠ **V2-⑬-3/⑬-6**:老四件套与参考件三件套两个键都已删 → 进场理由预填退回
        // 通用文案(⑮ 换成篮子卡后改取篮子卡的对应字段,那时再接)。
        entryForm.reason = Self.entryReasonText(for: candidate)
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

    /// 「买入补录」入口的进场理由预填(纯函数,单测覆盖)。⚠ **V2-⑬-3**:原先优先取
    /// 参考买入区间的 `why`,该键已随参考件三件套展示位删除 → 统一退回通用文案,
    /// 不显示空字符串、也不编造理由(⑮ 接篮子卡时再从卡里取)。
    static func entryReasonText(for candidate: Candidate) -> String {
        _ = candidate
        return "已按计划买入"
    }

    func openCloseSheet(code: String) {
        guard let pos = positions.first(where: { $0.code == code }) else { return }
        closeSellPrice = pos.hasLivePrice ? String(format: "%.2f", pos.price) : ""
        closeReasonDraft = nil
        closeSellFees = ""
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
        guard entryForm.isValid, let price = entryForm.buyPrice, let qty = entryForm.qtyInt,
              let fees = entryForm.buyFeesValue else {
            showToast("请完整填写代码/价格/数量/理由/实付买入费用", isError: true); return
        }
        let code = entryForm.code.trimmingCharacters(in: .whitespaces)
        let name = entryForm.name.trimmingCharacters(in: .whitespaces)
        let reason = entryForm.reason.trimmingCharacters(in: .whitespaces)
        // v1.4-①-A:真实买入日,始终显式传(与不传今天在服务端行为等价,但更明确);
        // 非交易日 / 未来日由服务端 400 拒绝(交易日历唯一事实源在服务端,不在客户端预判)。
        let buyDateStr = calendar.compactString(entryForm.buyDate)
        do {
            let r = try await client.openPosition(code: code, name: name.isEmpty ? nil : name,
                                                  buyPrice: price, qty: qty, entryReason: reason,
                                                  buyFees: fees, buyDate: buyDateStr)
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
        // v1.3-①/⑥:实付卖出费用可选(留空 → nil,服务端 NULL + D5 净浮盈判向走估算公式,
        // 不阻断清仓;成交后再补填也可以)。
        let sellFees = Double(closeSellFees.trimmingCharacters(in: .whitespaces))
        do {
            _ = try await client.closePosition(id: pos.id, sellPrice: sell,
                                               closeReason: closeReasonDraft?.rawValue, sellFees: sellFees)
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
        // ⚠ **V2-⑬-5**:五项必填校验文案随强制表单退役一并删除(服务端 ⑩-C 起空提交
        // 合法)。只剩「没有代码就无从记账」这一条真硬前提。
        guard decisionForm.isValid else {
            showToast("请先填写股票代码", isError: true); return
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
        // ⑨ 最高追价上限:勾了「不设上限」→ 显式传 nil(= JSON null);否则传数字文本框
        // 解析出的值(⑬-5 后两者皆无也合法 → 同样传 nil)。
        let maxChasePct: Double? = decisionForm.maxChaseNoCap ? nil : decisionForm.maxChasePctValue
        do {
            if let did = revisingDecisionId {
                _ = try await client.reviseDecision(
                    id: did, whyBuy: decisionForm.whyBuy, whyEntryPrice: decisionForm.whyEntryPrice,
                    targetPrice: targetPrice, exitLow: exitLow, exitHigh: exitHigh, thesisTags: tags,
                    invalidation: decisionForm.invalidation, contingencyScenarios: scenarios,
                    playbookTag: decisionForm.playbookTag.rawValue, plannedPrice: plannedPrice, plannedQty: plannedQty,
                    maxChasePct: maxChasePct
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
                    playbookTag: decisionForm.playbookTag.rawValue, plannedPrice: plannedPrice, plannedQty: plannedQty,
                    maxChasePct: maxChasePct
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

    // MARK: - v1.4-⑦-A 挂单未成交追踪(§七 P3-12;持仓卡决策日志详情区展示 N 日走势)

    /// 拉一次并缓存(按 decisionId);`rows` 空数组是合法态(尚未攒到数据),不当错误。
    func loadDecisionTrack(id: Int) async {
        guard let client = clientProvider(), !decisionTrackLoading.contains(id) else { return }
        decisionTrackLoading.insert(id)
        do {
            decisionTracks[id] = try await client.decisionTrack(id: id)
        } catch {
            // 静默降级(同 board/decisions 惯例)——追踪走势是持仓卡详情区的次级信息,
            // 拉不到不该弹错打断主流程,展开区自身会按「无数据」空态展示。
        }
        decisionTrackLoading.remove(id)
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

    // MARK: - 4A.5:问询台(§2.5 自由分析师,描述性标注非裁决;§2.7 自由对话体)

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
        lastInquiryId = nil          // 本轮还没结果,上一轮的 id 不许留在这儿
        do {
            let payload = Self.inquiryContext(from: inquiryThread)
            let r = try await client.sendInquiry(code: inquiryCode, messages: payload)
            inquiryThread.append(ChatMessage(role: .assistant, text: r.reply))
            inquiryVerdict = r.verdict
            inquiryEvidence = r.evidence
            inquiryDegraded = r.degraded
            lastInquiryId = r.inquiryId
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

    // MARK: - v1.4-⑦-B:问询历史(§七 P3-13;与已退役的 `inquiry_pool` 无耦合)

    func loadInquiryHistory() async {
        guard let client = clientProvider() else { return }
        inquiryHistoryLoading = true
        do {
            inquiryHistory = try await client.fetchInquiries()
        } catch let e as APIError {
            if case .noToken = e {} else { showToast(e.errorDescription ?? "问询历史拉取失败", isError: true) }
        } catch {
            showToast("问询历史拉取失败", isError: true)
        }
        inquiryHistoryLoading = false
    }

    // MARK: - 4A.5:设置

    /// v1.5-⑤-E:拉服务端版本(`GET /health`,免鉴权)供设置屏「App 版本 / 服务端版本」
    /// 双版本展示。**静默降级**(同 `loadBoard`/`loadDecisions` 惯例)——这不是主流程,
    /// 拉不到就保持 `nil`,不弹错、不阻断设置屏其余内容。
    func loadServerVersion() async {
        guard let client = clientProvider() else { return }
        do {
            let (_, version) = try await client.health()
            serverVersion = version
        } catch {
            // 拉不到就保持 nil,设置屏展示"服务端版本未知",不弹错打断。
        }
    }

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
            self.pushHoldingAlertDraft = s.push.holdingAlert
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

    /// 推送开关六类一并写入(报告 / 退潮刹车 / 盘前校准 / D5 时间退出 / v1.2-A2 熔断提醒 /
    /// v1.3-② K4 持仓派发警报)。
    func savePushSettings() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        do {
            _ = try await client.putSettingsPush(report: pushReportDraft, retreatBrake: pushRetreatDraft,
                                                 precall: pushPrecallDraft, d5exit: pushD5exitDraft,
                                                 circuit: pushCircuitDraft, holdingAlert: pushHoldingAlertDraft)
            await loadSettings()
            showToast("推送设置已保存")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "保存失败", isError: true)
        } catch {
            showToast("保存失败:\(error.localizedDescription)", isError: true)
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
