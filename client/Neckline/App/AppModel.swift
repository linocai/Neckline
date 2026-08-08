//
//  AppModel.swift
//  Neckline — 应用状态(@Observable)
//
//  **信息架构 = D8 已拍板(2026-08-02 用户逐条裁定,⛔ 施工时不得重开)**:
//    iPhone 四板块 = **今日篮子 / 持仓 / 问询台 / 设置**;macOS 同四板块 + **周复盘工作台**。
//    **不新增 tab** —— 加 tab 会稀释「打开就看今天做什么」。
//
//  ⚠ V1 的「盘中看板」不再是独立 tab:它的内容(退潮刹车 + 哨兵事件)**并入持仓板块**
//  作为一节 —— V2 的注意力分配是 80/15/5(持仓 80%),盘中动态本来就是为解释持仓服务的。
//  ⛔ 不是删掉数据,是换了挂载点(`BoardSection`)。
//

import Foundation
import Observation

enum AppTab: String, CaseIterable, Identifiable {
    // D8:iPhone 四 tab,顺序即 TabBar 顺序;`review` 只在 macOS 侧栏。
    // ⚠ V2.1-① 起 `inquiry` case 已删(问询台整链退役,用户裁定 #1)——
    // IA 重排(三板块 + 设置沉底、`review` 升为 iOS 第四 tab)归 V2.1-⑦,本块
    // 只做"问询台从产品面消失"这一件事,不改剩余 case 的顺序/文案。
    case baskets, positions, settings, review
    var id: String { rawValue }
    var title: String {
        switch self {
        case .baskets: return "今日篮子"
        case .positions: return "持仓"
        case .settings: return "设置"
        case .review: return "周复盘工作台"
        }
    }
    var systemImage: String {
        switch self {
        case .baskets: return "square.grid.2x2"
        case .positions: return "chart.line.uptrend.xyaxis"
        case .settings: return "gearshape"
        case .review: return "tray.and.arrow.down"
        }
    }
}

/// 开仓录入草稿(⑩-A **极简录入**:票 + 价 + 量 + 日期;其余由服务端自动关联)。
/// 止损线由服务端派生返回,表单不手填、不预先本地算。
struct PositionEntryForm {
    var code = ""
    var name = ""
    var price = ""
    var qty = ""
    /// ⚠ **V2-⑩-A:进场理由不再是必填**(「买卖录入控制在数秒内、不再要求长表单」)。
    /// 留一个可选输入位:用户想写就写,不写照样提交。
    var reason = ""
    /// 实付买入费用。⚠ V2 起**不再 UI 强制必填**(服务端本就宽松);留空 → 不传,
    /// D5 净浮盈估算走默认佣金率兜底并**诚实标注为估算**。
    var buyFees = ""
    /// 真实买入日,默认今天、**不可选未来**(`DatePicker` 用 `in: ...Date()` 限死上界)。
    /// 非交易日由服务端 400 拒绝,不在客户端预判(交易日历唯一事实源在服务端)。
    var buyDate: Date = Date()

    var buyPrice: Double? { Double(price.trimmingCharacters(in: .whitespaces)) }
    var qtyInt: Int? { Int(qty.trimmingCharacters(in: .whitespaces)) }
    var buyFeesValue: Double? { Double(buyFees.trimmingCharacters(in: .whitespaces)) }
    /// ⑩-A 三字段即可提交(票 + 价 + 量)。⛔ 别把理由 / 费用加回必填 —— 表单退役是
    /// 本版立项主题之一(减摩擦),归因标签稀疏这个代价已由用户拍板接受(裁定 #6)。
    var isValid: Bool {
        !code.trimmingCharacters(in: .whitespaces).isEmpty
            && (buyPrice ?? 0) > 0
            && (qtyInt ?? 0) > 0
    }
}

/// ⑩-C「用户可选补充」草稿(七枚标签 + 一句可选说明)。
/// ⚠ **这不是决策日志**:`decision_log` v2.0.0 起停写留档,本表单落 `user_actions`。
/// **全部可空**(空提交合法),⛔ 不做任何硬阻断。
struct NoteForm {
    var code = ""
    var positionId: Int? = nil
    var labels: Set<NoteLabel> = []
    var voiceNote = ""

    var hasContent: Bool {
        !labels.isEmpty || !voiceNote.trimmingCharacters(in: .whitespaces).isEmpty
    }
}

/// NL 提醒录入草稿(⑪-C)。
struct AlertComposeForm {
    var tsCode = ""
    var text = ""
    /// 解析结果(确认卡 + draft);`nil` = 还没解析过。
    var parsed: AlertParseResult? = nil
    var parsing = false
    var submitting = false
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

enum PositionModal: Equatable {
    case open
    case close(code: String)
    case circuitReview
    /// ⑩-C 用户可选补充(标签 + 语音说明)。
    case note
}

struct Toast: Identifiable, Equatable {
    let id = UUID()
    let message: String
    var isError: Bool = false
}

@MainActor
@Observable
final class AppModel {
    // —— 导航(D8 四板块)——
    var view: AppTab = .baskets

    // —— 今日篮子:报告(含 `basketDaily` 三段)——
    var report: ReportSnapshot = .empty(reason: "not_loaded")
    var reportLoading = false

    /// 每篮的验证状态(⑧ 三路读法),按需懒加载 —— 报告快照里的卡是 D0 冻结件,
    /// 验证状态是**实时**的,两者不是一回事。
    var basketVerifications: [Int: BasketVerification] = [:]
    private var verificationLoading: Set<Int> = []

    /// 展开中的篮子(卡详情页)。
    var openedBasketId: Int? = nil

    // —— 持仓 ——
    var positions: [Position] = []
    var positionsLoading = false
    var loadError: String? = nil
    /// 每笔仓的计划版本(⑩-B),按需懒加载。
    var positionPlans: [Int: [PositionPlan]] = [:]
    private var plansLoading: Set<Int> = []

    // —— 信息卡(篮子成员详情页地基,D1 保留改造)——
    /// ⚠ 不再携带整个 `Candidate`(该类型已退役):只带展示头需要的三样。
    struct InfoCardRequest: Identifiable, Equatable {
        let tradeDate: String
        let code: String
        let name: String
        var id: String { "\(tradeDate)|\(code)" }
    }
    var infoCardRequest: InfoCardRequest? = nil
    var infoCard: InfoCard? = nil
    var infoCardLoading = false
    var infoCardError: String? = nil

    // —— 盘中动态(并入持仓板块的一节,不再是独立 tab)——
    var board: BoardSnapshot = .empty
    var boardLoading = false

    // ⚠ V2.1-① 起「问询台」+「问询历史」两节(11 个属性 + 4 个方法)已随问询台
    // 整链退役删除——见 `tests/test_v21_retirement_guard.py::test_inquiry_desk_is_gone`。

    // —— 设置 ——
    var settings: SettingsSnapshot = .empty
    var settingsLoading = false
    var providers: [Provider] = []
    var llmRoutes: LLMRoutes = LLMRoutes()
    var providerForm = ProviderForm()
    var showProviderForm = false
    /// 推送开关草稿:**服务端发什么就渲染什么**(⛔ 不硬编 kind 清单)。
    var pushKindsDraft: [PushKind] = []
    var serverVersion: String? = nil

    // —— NL 临时提醒(⑪-C)——
    var alerts: [CustomAlert] = []
    var alertsLoading = false
    var alertForm = AlertComposeForm()
    var showAlertComposer = false

    // —— 周复盘工作台(macOS)——
    var reviewWeeks: [WeeklyReviewEntry] = []
    var reviewSelectedWeek: String? = nil
    var reviewParseWarnings: [String] = []
    var reviewDataWarnings: [String] = []
    var reviewUploading = false
    var reviewHasUploaded = false
    var preferenceProfile: Profile? = nil
    var capabilityProfile: Profile? = nil
    var evalWeekly: EvalWeekly? = nil
    var workbenchLoading = false

    // —— 熔断纪律(纯提醒层,客户端只展示 + 自律灰化,§3.8)——
    var circuit: CircuitState = .empty

    // —— 模态 / 录入 / toast ——
    var modal: PositionModal? = nil
    var entryForm = PositionEntryForm()
    /// **本次开仓提交动作**的幂等键(v2.0.0,契约线 🟡 Y7)。
    /// ⚠ 规则定死:**每笔新提交动作铸一枚新键**(打开录入表单时铸),提交失败重试
    /// **复用同一枚**;提交成功**不主动作废这枚键**,旧值原样留在内存里,直到**下一次
    /// `beginPositionEntryFlow()`** 才换新(🔵-5 小审 2026-08-03 措辞订正:原注释写
    /// "提交成功后作废"与实现不符——成功路径只 `dismissModal()`,没有重置本属性;
    /// 风险为零,因为下一笔提交必经 `beginPositionEntryFlow()` 铸新键,
    /// `AppModelTests` 已反向断言同一录入流程内重试不换键)。⛔ 严禁跨提交复用、
    /// ⛔ 别绑「票 + 日期」之类业务量 —— 服务端是标准幂等语义,同键不同 payload 会
    /// **静默重放原仓**,把用户改过的价格数量整个吃掉。
    private(set) var entryIdempotencyKey: String = UUID().uuidString
    var noteForm = NoteForm()
    var closeSellPrice = ""
    var closeReasonDraft: CloseReasonCode? = nil
    var closeSellFees = ""
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

    /// 退潮刹车激活时的状态提示(§2.4「今日计划作废、禁开新仓」)。
    /// **只警示、不硬拦** —— 开仓录入是补记用户已在券商完成的真实操作(审计台账),
    /// 硬拦会变成帮用户瞒报真实操作。
    var retreatWarning: String? {
        guard board.retreatBrake.active else { return nil }
        let base = "退潮红色刹车已触发 · 今日计划作废、不建议开新仓"
        guard !board.retreatBrake.reason.isEmpty else { return base }
        return base + "(依据:\(board.retreatBrake.reason))"
    }

    var hasReportData: Bool { !report.degraded && !report.tradeDate.isEmpty }
    var quota: PositionQuota? { report.sentiment.map { PositionQuota($0.positionQuota) } }

    func position(byID id: Int) -> Position? { positions.first(where: { $0.id == id }) }

    /// 篮子日报(报告快照里的三段)。
    var basketDaily: BasketDaily { report.basketDaily }

    /// **同题材合并敞口**(蓝图 6.2:同一来源篮子的多笔仓**不得视为完全分散的两笔仓位**)。
    /// 归并键 = 来源篮子(取自各仓 `version=1` 计划的 `sourceBasketId`);拿不到计划的仓
    /// **不参与归并**(⛔ 不拿"同行业"之类近似量顶替 —— 那会把两笔无关的仓说成一笔)。
    /// 只在**同一篮 ≥2 个不同标的**时才成立(单票加仓不算"看起来分散实则集中")。
    var mergedExposures: [MergedExposure] {
        var byBasket: [Int: (name: String, positions: [Position])] = [:]
        for p in positions {
            guard let plans = positionPlans[p.id], let base = plans.first(where: { $0.version == 1 }),
                  let bid = base.sourceBasketId else { continue }
            let name = base.sourceBasketName ?? base.sourceBasketKey ?? "篮子 #\(bid)"
            byBasket[bid, default: (name, [])].positions.append(p)
        }
        return byBasket.compactMap { bid, v -> MergedExposure? in
            let codes = Set(v.positions.map(\.code))
            guard codes.count >= 2 else { return nil }
            let cost = v.positions.reduce(0.0) { $0 + $1.buyPrice * Double($1.qty) }
            let market = v.positions.reduce(0.0) {
                $0 + ($1.hasLivePrice ? $1.price : $1.buyPrice) * Double($1.qty)
            }
            return MergedExposure(basketId: bid, basketName: v.name,
                                  codes: v.positions.map(\.code).sorted(),
                                  costAmount: cost, marketAmount: market)
        }
        .sorted { $0.basketId < $1.basketId }
    }

    /// 同题材合并敞口一条。
    struct MergedExposure: Identifiable, Equatable {
        let basketId: Int
        let basketName: String
        let codes: [String]
        let costAmount: Double
        let marketAmount: Double
        var id: Int { basketId }
    }

    // MARK: - 刷新

    /// 主刷新:报告 + 持仓 + 盘中动态 + 熔断态 + 临时提醒(五者并发)。
    /// 盘中动态 / 熔断态也在此拉一份是刻意的 —— 「退潮红色刹车禁开新仓」「熔断中停开
    /// 新仓」的警示要在用户点「开仓」之前就可见。
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
        async let alertsTask: Result<[CustomAlert], Error> = fetchResult { try await client.fetchAlerts() }
        let (reportResult, positionsResult, boardResult, circuitResult, alertsResult) =
            await (reportTask, positionsTask, boardTask, circuitTask, alertsTask)

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
        case .failure: break   // 盘中动态降级不弹错(主内容是篮子 + 持仓,它只为警示条服务)
        }
        switch circuitResult {
        case .success(let c): self.circuit = c
        case .failure: break   // 熔断态读取失败保留上次已知态,不拿失败静默重置成"未锁定"
        }
        switch alertsResult {
        case .success(let a): self.alerts = a
        case .failure: break   // 同上,静默降级
        }
        reportLoading = false
        positionsLoading = false
        // 持仓计划要靠它才能算合并敞口 + 展示计划继承卡,随主刷新一起拉(每仓一次)。
        await loadAllPositionPlans()
        applyQAHooksAfterRefresh()
    }

    /// 纯 QA/截图辅助(同 `NecklineApp` 的 `NECKLINE_INITIAL_TAB`/`NECKLINE_INITIAL_MODAL`
    /// 先例)。⚠ **必须放在 `refresh()` 数据到位之后**——`NecklineApp.init()` 里的同步钩子
    /// 够不着这些内容(数据是异步拉的),这是 v1.4-⑧ `NECKLINE_INITIAL_INFOCARD_CODE`
    /// 立下的先例。不影响正常用户路径(缺环境变量则不触发)。
    private func applyQAHooksAfterRefresh() {
        let env = ProcessInfo.processInfo.environment
        if let raw = env["NECKLINE_INITIAL_BASKET_ID"], let bid = Int(raw), openedBasketId == nil,
           basketDaily.baskets.contains(where: { $0.basketId == bid }) {
            openedBasketId = bid
        }
        if let code = env["NECKLINE_INITIAL_INFOCARD_CODE"], !code.isEmpty, infoCardRequest == nil {
            // 从篮子成员里找这只票(候选管线已退役,成员才是新的入口)。
            for b in basketDaily.baskets {
                if let m = b.card?.members.first(where: { $0.tsCode == code }) {
                    openInfoCard(tradeDate: report.tradeDate, code: m.tsCode, name: m.name)
                    return
                }
            }
        }
        // NL 提醒确认卡的截图钩子:开确认卡需要先发一次解析请求(异步),同样够不着
        // `init()`。`NECKLINE_INITIAL_ALERT_TEXT=<一句话>`(可选
        // `NECKLINE_INITIAL_ALERT_CODE=<代码>`)→ 开合成器并跑一次解析。
        if let text = env["NECKLINE_INITIAL_ALERT_TEXT"], !text.isEmpty, !showAlertComposer {
            beginAlertComposer(tsCode: env["NECKLINE_INITIAL_ALERT_CODE"] ?? "")
            alertForm.text = text
            Task { await parseAlertText() }
        }
    }

    // MARK: - 篮子

    /// 每篮的验证状态(⑧)。**报告里的卡是 D0 冻结件,验证状态是实时的** —— 两者不是
    /// 一回事,故独立拉。失败静默(角标显示"未取到"而非崩)。
    func loadBasketVerification(id: Int) async {
        guard let client = clientProvider(), !verificationLoading.contains(id) else { return }
        verificationLoading.insert(id)
        do { basketVerifications[id] = try await client.fetchBasketVerification(id: id) }
        catch { /* 静默降级:角标不显示,不假装"已验证" */ }
        verificationLoading.remove(id)
    }

    func openBasket(id: Int) { openedBasketId = id }
    func dismissBasket() { openedBasketId = nil }

    func basket(byID id: Int) -> Basket? {
        basketDaily.baskets.first(where: { $0.basketId == id })
    }

    // MARK: - 信息卡(依需求现算,不进常规刷新)

    func openInfoCard(tradeDate: String, code: String, name: String) {
        infoCard = nil
        infoCardError = nil
        infoCardRequest = InfoCardRequest(tradeDate: tradeDate, code: code, name: name)
        Task { await loadInfoCard() }
    }

    func loadInfoCard() async {
        guard let req = infoCardRequest, let client = clientProvider() else { return }
        infoCardLoading = true
        do {
            infoCard = try await client.fetchInfoCard(date: req.tradeDate, code: req.code)
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

    // MARK: - 盘中动态(并入持仓板块的一节)

    func loadBoard() async {
        guard let client = clientProvider() else { return }
        boardLoading = true
        do {
            self.board = try await client.fetchBoard()
        } catch let e as APIError {
            if case .noToken = e {} else { showToast(e.errorDescription ?? "盘中动态拉取失败", isError: true) }
        } catch {
            showToast("盘中动态拉取失败", isError: true)
        }
        boardLoading = false
    }

    // MARK: - 开仓 / 清仓(审计台账,永不代下单)

    /// 手动补录(持仓区「补录开仓」按钮)。
    /// ⚠ **每次进入录入流程铸一枚新幂等键** —— 这就是「绑在这一次提交动作上」的落点。
    func beginPositionEntryFlow() {
        entryForm = PositionEntryForm()
        entrySuggestionRange = nil
        entryIdempotencyKey = UUID().uuidString
        modal = .open
    }

    /// 从篮子成员进入补录:预填 code/name + 参考价(取成员的建仓观察区间下沿,
    /// **夹逼拒收时留空手填**,⛔ 不虚构数字)+ 区间双档推荐(只读展示,不预填数量)。
    func beginPositionEntryFlow(fromMember member: BasketMember, basketName: String) async {
        beginPositionEntryFlow()
        entryForm.code = member.tsCode
        entryForm.name = member.name
        entryForm.reason = Self.entryReasonText(basketName: basketName, member: member)
        if let low = member.entryZone?.low, low > 0 {
            entryForm.price = String(format: "%.2f", low)
            if let client = clientProvider() {
                do { entrySuggestionRange = try await client.entrySuggestion(code: member.tsCode, price: low) }
                catch { /* 拉不到区间推荐 → 留给用户手填,不崩、不显示编造的数字 */ }
            }
        }
    }

    /// 「买入补录」入口的进场理由预填(纯函数,单测覆盖)。
    /// ⚠ 文案只陈述**来源事实**,⛔ 不得出现「推荐 / 建议买入 / 看好」类表述(§2.8 红线)。
    static func entryReasonText(basketName: String, member: BasketMember) -> String {
        let name = basketName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return "已按计划买入" }
        let role = member.roleConflict ? "角色两说并存" : member.roleDisplay
        return "来自篮子「\(name)」· \(role)"
    }

    func openCloseSheet(code: String) {
        guard let pos = positions.first(where: { $0.code == code }) else { return }
        closeSellPrice = pos.hasLivePrice ? String(format: "%.2f", pos.price) : ""
        closeReasonDraft = nil
        closeSellFees = ""
        modal = .close(code: code)
    }

    func dismissModal() { modal = nil }

    func submitOpenPosition() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        guard entryForm.isValid, let price = entryForm.buyPrice, let qty = entryForm.qtyInt else {
            showToast("请填写代码 / 买入价 / 数量", isError: true); return
        }
        let code = entryForm.code.trimmingCharacters(in: .whitespaces)
        let name = entryForm.name.trimmingCharacters(in: .whitespaces)
        let reason = entryForm.reason.trimmingCharacters(in: .whitespaces)
        let buyDateStr = calendar.compactString(entryForm.buyDate)
        do {
            let r = try await client.openPosition(code: code, name: name.isEmpty ? nil : name,
                                                  buyPrice: price, qty: qty, entryReason: reason,
                                                  buyFees: entryForm.buyFeesValue, buyDate: buyDateStr,
                                                  // ⚠ 同一次录入流程内重试**复用同一枚键**;
                                                  // 下次 `beginPositionEntryFlow()` 才换新的。
                                                  idempotencyKey: entryIdempotencyKey)
            dismissModal()
            await refresh()
            if r.replayed {
                // ⚠ 如实说「什么都没发生」,⛔ 别让"看起来成功了"掩盖它。
                showToast("这笔提交与之前那次是同一笔(未重复开仓)")
            } else {
                var msg = "已录入开仓 · 止损线 \(String(format: "%.2f", r.stopLine))"
                if let src = r.sourceBasketName { msg += " · 来源篮子「\(src)」" }
                showToast(msg)
            }
            if let notice = r.planDeviationNotice, !notice.isEmpty {
                showToast(notice)   // 「原盈亏结构已变」:纯提示,不质问、不阻断
            }
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

    // MARK: - ⑩-B 计划继承 + ⑪-D-D per-position 触达提醒开关

    func loadAllPositionPlans() async {
        for p in positions { await loadPositionPlans(positionId: p.id) }
    }

    func loadPositionPlans(positionId: Int) async {
        guard let client = clientProvider(), !plansLoading.contains(positionId) else { return }
        plansLoading.insert(positionId)
        do { positionPlans[positionId] = try await client.fetchPositionPlans(positionId: positionId) }
        catch { /* 静默降级:计划卡显示"暂不可用",不弹错打断主流程 */ }
        plansLoading.remove(positionId)
    }

    /// 该仓的**现役计划**(版本号最大的一版)。
    func latestPlan(positionId: Int) -> PositionPlan? {
        positionPlans[positionId]?.max(by: { $0.version < $1.version })
    }

    /// ⑪-D-D:per-position「不提醒」开关。
    ///
    /// **为什么是 per-position 而不是全局关**:全局关 `take_profit` 会连坐所有持仓,
    /// 用户真正想要的是「这只票的这个数不靠谱,别烦我」。
    /// 落法 = 在最新计划之上**追加一个新版本**(`position_plans` 是版本化只增表,
    /// ⛔ 不就地改历史行),⚠ **只翻静音位、计划正文一项不动**;武装态由服务端重算
    /// (⑪-D-B 闸②:请求体说了不算,否则"写个新版本"就成了绕开红线闸的后门)。
    func setExitReferenceMuted(positionId: Int, muted: Bool) async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        guard let latest = latestPlan(positionId: positionId) else {
            showToast("这笔仓没有可继承的计划基线", isError: true); return
        }
        do {
            let note = muted ? "用户关闭本票触达提醒" : "用户恢复本票触达提醒"
            _ = try await client.createPositionPlanVersion(
                positionId: positionId, plan: latest.planBodyTogglingMute(muted), note: note)
            await loadPositionPlansForcing(positionId: positionId)
            showToast(muted ? "已关闭本票的触达提醒" : "已恢复本票的触达提醒")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "设置失败", isError: true)
        } catch {
            showToast("设置失败:\(error.localizedDescription)", isError: true)
        }
    }

    private func loadPositionPlansForcing(positionId: Int) async {
        plansLoading.remove(positionId)
        await loadPositionPlans(positionId: positionId)
    }

    // MARK: - ⑩-C 用户可选补充(七枚标签 + 一句可选说明)

    func beginNote(code: String, positionId: Int? = nil) {
        noteForm = NoteForm(code: code, positionId: positionId)
        modal = .note
    }

    /// **空提交合法**(服务端 200,不是 400)—— ⛔ 不做任何硬阻断。
    func submitNote() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        let code = noteForm.code.trimmingCharacters(in: .whitespaces)
        let voice = noteForm.voiceNote.trimmingCharacters(in: .whitespaces)
        do {
            let r = try await client.postDecisionNote(
                code: code.isEmpty ? nil : code, positionId: noteForm.positionId,
                labels: noteForm.labels.map(\.rawValue).sorted(),
                voiceNote: voice.isEmpty ? nil : voice)
            dismissModal()
            showToast(r.recorded.isEmpty ? "没有可记录的补充内容(未记账)" : "已记录补充")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "提交失败", isError: true)
        } catch {
            showToast("提交失败:\(error.localizedDescription)", isError: true)
        }
    }

    // MARK: - 熔断纪律(纯提醒层;绝不代下单 / 撤单)

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

    // MARK: - ⑪-C 自然语言临时提醒(**只通知,永不交易**)

    func loadAlerts() async {
        guard let client = clientProvider() else { return }
        alertsLoading = true
        do { alerts = try await client.fetchAlerts() }
        catch let e as APIError {
            if case .noToken = e {} else { showToast(e.errorDescription ?? "提醒拉取失败", isError: true) }
        } catch { showToast("提醒拉取失败", isError: true) }
        alertsLoading = false
    }

    func beginAlertComposer(tsCode: String = "") {
        alertForm = AlertComposeForm(tsCode: tsCode)
        showAlertComposer = true
    }

    /// 自然语言 → 结构化规则 → **确认卡**。端点恒 200:LLM 不可用时 `degraded=true` +
    /// 手填表单,**不静默失败**。
    func parseAlertText() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        let text = alertForm.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        alertForm.parsing = true
        alertForm.parsed = nil
        let code = alertForm.tsCode.trimmingCharacters(in: .whitespaces)
        do {
            alertForm.parsed = try await client.parseAlert(text: text, tsCode: code.isEmpty ? nil : code)
        } catch let e as APIError {
            showToast(e.errorDescription ?? "解析失败", isError: true)
        } catch {
            showToast("解析失败:\(error.localizedDescription)", isError: true)
        }
        alertForm.parsing = false
    }

    /// 用户在确认卡上点「确认」→ 把 `draft` **原样回传** `POST /alerts`。
    func confirmAlertDraft() async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        guard let draft = alertForm.parsed?.draft else {
            showToast("还没有可创建的提醒规则", isError: true); return
        }
        alertForm.submitting = true
        do {
            _ = try await client.createAlert(draft)
            showAlertComposer = false
            alertForm = AlertComposeForm()
            await loadAlerts()
            showToast("提醒已创建 · 只通知,不自动交易")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "创建失败", isError: true)
        } catch {
            showToast("创建失败:\(error.localizedDescription)", isError: true)
        }
        alertForm.submitting = false
    }

    func deleteAlert(id: Int) async {
        guard let client = clientProvider() else { return }
        do {
            _ = try await client.deleteAlert(id: id)
            await loadAlerts()
            showToast("提醒已停用")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "停用失败", isError: true)
        } catch {
            showToast("停用失败:\(error.localizedDescription)", isError: true)
        }
    }

    /// 「改」= 局部更新(未传的字段不改)。本版只暴露最常用的两项:重置命中计数 / 改到期。
    func updateAlert(id: Int, resetFired: Bool = false, expiresAt: String? = nil) async {
        guard let client = clientProvider() else { return }
        let body = AlertUpdateRequest(conditions: nil, logic: nil, nlText: nil, activeFrom: nil,
                                      activeTo: nil, expiresAt: expiresAt, persist: nil,
                                      cooldownSeconds: nil, maxFires: nil, resetFired: resetFired)
        do {
            _ = try await client.updateAlert(id: id, body)
            await loadAlerts()
            showToast("提醒已更新")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "更新失败", isError: true)
        } catch {
            showToast("更新失败:\(error.localizedDescription)", isError: true)
        }
    }

    // MARK: - 设置

    /// 服务端版本(`GET /health`,免鉴权)。**静默降级** —— 拉不到就保持 `nil`,
    /// 设置屏展示"服务端版本未知",不冒充"版本相同"。
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
        let (s, p, r) = await (settingsTask, providersTask, routesTask)
        switch s {
        case .success(let v):
            settings = v
            // ⚠ **服务端发什么就渲染什么**(⛔ 不硬编 kind 清单;新增 kind 时客户端
            // 不改代码就能显示出来)。
            pushKindsDraft = v.push.kinds
        case .failure(let e):
            if let api = e as? APIError, case .noToken = api {} else { showToast("设置拉取失败", isError: true) }
        }
        if case .success(let v) = p { providers = v }
        if case .success(let v) = r { llmRoutes = v }
        settingsLoading = false
    }

    // —— Provider 注册表增删改(**key 只写不回显**)——

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
        let engine = f.searchEngine.trimmingCharacters(in: .whitespaces)
        let notes = f.notes.trimmingCharacters(in: .whitespaces)
        do {
            if let editing = f.editingName {
                // 局部更新:**没填就不传该键**(不传 = 不改;传了才是显式改写)。
                // ⚠ 🔵-6 小审 2026-08-03 对齐:`searchEngine`/`notes` 原先不判空,留空会
                // 被当成"显式清空"发给服务端,与 `apiKey`「留空 = 不改」的语义不对称、
                // 同一张表单里两种"留空"含义不同容易让用户误清；现改为与 `apiKey` 同一种
                // 留空即不改的读法(代价与 `apiKey` 相同:一旦服务端已有值,不能再靠"清空
                // 这个字段"把它改回空 —— 要清除得整个 Provider 删了重建)。
                let body = ProviderUpdateRequest(
                    baseUrl: f.baseUrl, model: f.model, apiKey: key.isEmpty ? nil : key,
                    hasWebSearch: f.hasWebSearch, searchEngine: engine.isEmpty ? nil : engine,
                    notes: notes.isEmpty ? nil : notes, enabled: f.enabled)
                _ = try await client.updateProvider(name: editing, body)
            } else {
                let body = ProviderCreateRequest(
                    name: f.name, baseUrl: f.baseUrl, model: f.model,
                    apiKey: key.isEmpty ? nil : key, hasWebSearch: f.hasWebSearch,
                    searchEngine: engine.isEmpty ? nil : engine,
                    notes: notes.isEmpty ? nil : notes, enabled: f.enabled)
                _ = try await client.createProvider(body)
            }
            providerForm = ProviderForm()      // 安全态:key 草稿立即清空
            showProviderForm = false
            await loadSettings()
            showToast("Provider 已保存 · 运行时生效")
        } catch let e as APIError {
            providerForm.apiKey = ""   // 🔵-4 小审 2026-08-03:失败重试保留其余字段,key 草稿不残留明文
            showToast(e.errorDescription ?? "保存失败", isError: true)
        } catch {
            providerForm.apiKey = ""
            showToast("保存失败:\(error.localizedDescription)", isError: true)
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
    /// (缺键 → 422),承 V1「防漏传静默重置某开关」的同一条纪律。
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

    // MARK: - 周复盘工作台(对账逻辑全在后端 `neckline/review/`,本模型只装配/展示)

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

    /// 画像两张账 + 评价校准报告(macOS 工作台)。**三者各自独立降级**,一路失败不连带
    /// 其余各路"看起来也不可用"。
    func loadWorkbenchExtras() async {
        guard let client = clientProvider() else { return }
        workbenchLoading = true
        async let prefTask: Result<Profile, Error> = fetchResult { try await client.fetchPreferenceProfile() }
        async let capTask: Result<Profile, Error> = fetchResult { try await client.fetchCapabilityProfile() }
        async let evalTask: Result<EvalWeekly, Error> = fetchResult { try await client.fetchEvalWeekly() }
        let (pref, cap, ev) = await (prefTask, capTask, evalTask)
        if case .success(let v) = pref { preferenceProfile = v }
        if case .success(let v) = cap { capabilityProfile = v }
        if case .success(let v) = ev { evalWeekly = v }
        workbenchLoading = false
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
