//
//  AppModel.swift
//  Neckline — 应用状态(@Observable)
//
//  **信息架构 = V2.1 三板块(2026-08-07 用户裁定 #2,⛔ 施工时不得重开)**:
//    **选股 / 持仓 / 复盘** 三个板块 + **设置沉底为入口**(排最后、齿轮图标,
//    **产品语义上不算板块**)。iOS 底部 TabBar 四项即此顺序;macOS 侧栏「交易」组
//    = 选股 / 持仓,「复盘」组 = 复盘,设置沉到侧栏最底部。
//
//  ⚠ **前身 = D8 四板块**(今日篮子 / 持仓 / 问询台 / 设置):问询台整链退役(V2.1-①)、
//  「今日篮子」改名「选股」、原 macOS 独有的「周复盘工作台」升为**复盘板块**(V2.1-⑦)。
//
//  🔴 **V2.4.0 P0:「盘中动态」整节退役** —— V1 它是独立 tab、V2 并入持仓板块作为一节,
//  V2.4.0 **整个删掉**(审计规格 P0:撤销盘中通用证伪与代理池退潮刹车的交易判断权)。
//  ⚠ **删的是聚合页面,不是有效提醒**:持仓亏损警戒 / 离场参考 / 板块跳水改由
//  `Position.alerts` 随 `/positions` 一起下发(P0.5+),⛔ 不新增任何请求或轮询。
//

import Foundation
import Observation

enum AppTab: String, CaseIterable, Identifiable {
    // V2.1-⑦:三板块 + 设置沉底,**枚举顺序即 iOS TabBar 顺序**。
    // 🔴 **`rawValue` 一个都不许改**(= case 名):`NECKLINE_INITIAL_TAB` QA 钩子与既有
    // 截图脚本按 rawValue 传参,改名会把那些脚本静默变成"落到默认 tab"。改的只有
    // `title` / `systemImage` / 顺序。
    // ⚠ V2.1-① 起 `inquiry` case 已删(问询台整链退役,用户裁定 #1)。
    case baskets, positions, review, settings
    var id: String { rawValue }
    var title: String {
        switch self {
        // 「选股」= 原「今日篮子」改名(用户裁定 #2)。⚠ **板块名是导航语义,
        // 报告段名(「③ 今日篮子」等)是报告结构,两回事** —— 段名与服务端 markdown
        // 报告同构、是审计锚,⛔ 不许跟着改。
        case .baskets: return "选股"
        case .positions: return "持仓"
        case .review: return "复盘"
        case .settings: return "设置"
        }
    }
    var systemImage: String {
        switch self {
        case .baskets: return "square.grid.2x2"
        case .positions: return "chart.line.uptrend.xyaxis"
        // 它已不只是"拖交割单"(每日复盘 + 累计成绩单 + 对账 + 移交件四件),
        // 故弃用 `tray.and.arrow.down`(那是"上传"的图标)。
        case .review: return "chart.bar.doc.horizontal"
        case .settings: return "gearshape"
        }
    }
}

/// 复盘板块的五页(V2.1-⑦ 三页 + **V2.2-④ 双时钟两页**)。
/// **每页答一个不同的问题,⛔ 不合并**:
/// 每日 =「昨天那批篮子后来怎么样了」· **选股钟 =「这批票选得对不对」(覆盖 D0 全部
/// T1/T2,与买没买无关)** · **交易钟 =「这笔买卖做得怎么样」(只在实际买入后存在)** ·
/// 累计 =「这套选股长期成绩如何」· 对账 =「我实际的成交与计划/章程对不对得上」。
///
/// 🔴 **两个时钟刻意分成两页而不是合成一页**(K8 §十四):它们的**样本域根本不同** ——
/// 一页并排就会让人以为"选股时钟里的篮子 = 我买过的票",而那正是 K8 反复强调的、
/// 最容易把覆盖域讲小的误读。
///
/// ⚠ `rawValue` 一个都不许改(`NECKLINE_INITIAL_REVIEW_PAGE` QA 钩子按它传参)。
enum ReviewPage: String, CaseIterable, Identifiable {
    case daily, selectionClock, tradeClock, cumulative, reconcile
    var id: String { rawValue }
    var title: String {
        switch self {
        case .daily: return "每日"
        case .selectionClock: return "选股钟"
        case .tradeClock: return "交易钟"
        case .cumulative: return "累计"
        case .reconcile: return "对账"
        }
    }

    /// 🔴 **「这一页答什么」**(V2.3:用户原话「五个页签分不清」的解法)。
    /// ⚠ 这不是装饰文案 —— **选股钟 / 交易钟的样本域根本不同**,这两句话就是把它们
    /// 分开的那条线:一个问「选得对不对」(与你买没买无关)、一个问「做得怎么样」
    /// (只在你真的买了之后存在)。⛔ 别改写成对仗好听但讲不清域的句子。
    var question: String {
        switch self {
        case .daily: return "昨天那批篮子后来怎么样了"
        case .selectionClock: return "这批票选得对不对 · 样本 = D0 全部 T1/T2,与你买没买无关"
        case .tradeClock: return "这笔买卖做得怎么样 · 只在你真的买了之后存在"
        case .cumulative: return "这套选股长期成绩如何"
        case .reconcile: return "我实际的成交与计划 / 章程对不对得上"
        }
    }

    var systemImage: String {
        switch self {
        case .daily: return "calendar"
        case .selectionClock: return "scope"
        case .tradeClock: return "clock.arrow.circlepath"
        case .cumulative: return "chart.bar.doc.horizontal"
        case .reconcile: return "doc.text.magnifyingglass"
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
    /// ⑩-C 用户可选补充(标签 + 语音说明)。
    case note
    /// V2.2-④-B 交易时钟的一条主观说明(K8 §十五)。**本版唯一新增写入口。**
    case tradeNote(positionId: Int)
    // ⚠ `case circuitReview` **已删**(V2.2-⑤-B 熔断整体退役,用户裁定 #8)——
    // 「强制复盘解锁」这件机制在产品面消失,⛔ 不许接回来。
}

struct Toast: Identifiable, Equatable {
    let id = UUID()
    let message: String
    var isError: Bool = false
}

@MainActor
@Observable
final class AppModel {
    // —— 导航(V2.1 三板块 + 设置沉底)——
    var view: AppTab = .baskets
    /// 复盘板块当前页(三页各自独立数据源,切页不重拉已有数据)。
    var reviewPage: ReviewPage = .daily

    // —— 今日篮子:报告(含 `basketDaily` 三段)——
    var report: ReportSnapshot = .empty(reason: "not_loaded")
    var reportLoading = false

    /// **本机上一次成功刷新的时刻**(V2.3 工具栏「刷新按钮上直接显示上次更新时刻」)。
    /// ⚠ 这是**客户端**的钟,回答「我上次去问是什么时候」——⛔ **不是** `report.generatedAt`
    /// (那是服务端出报告的时刻,两者可以差好几个小时:16:35 出的报告,你 21:00 才打开)。
    /// 两个时刻**刻意不合并**:合并了就分不清「数据旧」和「我很久没刷」。
    /// `nil` = 本次启动还没成功刷新过,展示层如实留空,⛔ 不拿"现在"冒充。
    var lastRefreshedAt: Date? = nil

    /// 每篮的验证状态(⑧ 三路读法),按需懒加载 —— 报告快照里的卡是 D0 冻结件,
    /// 验证状态是**实时**的,两者不是一回事。
    var basketVerifications: [Int: BasketVerification] = [:]
    private var verificationLoading: Set<Int> = []

    /// 展开中的篮子(卡详情页)。
    var openedBasketId: Int? = nil

    // —— 持仓 ——
    var positions: [Position] = []
    /// 🔴 **组合环境提醒**(2026-08-12 用户裁定 ②):板块级 / 市场级的环境证据,
    /// 落在**持仓页顶部**那一段。⚠ 空数组 = 今天没有这两类事件,
    /// ⛔ **不许据此画一句「环境正常」** —— 那正是 P0 撤销掉的状态机。
    var portfolioAlerts: [PortfolioAlert] = []
    var positionsLoading = false
    /// V2.3 macOS 三区布局:详情栏当前显示的那一笔(`nil` = 还没选)。
    /// ⚠ 纯导航态,**不参与任何判定**;iOS 上走推入式详情、不读它。
    var selectedPositionId: Int? = nil
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

    // ⛔ **V2.4.0 P0**:`board` / `boardLoading` 两个状态位已删(P0.1 表
    // 「独立『盘中动态』页面与事件列表 = 删」)。持仓相关提醒改由
    // `Position.alerts` 随 `/positions` 一起下发(P0.5+),⛔ 不留任何 `/board` 状态。

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

    // —— 复盘板块 · 对账页(交割单上传与周报,macOS 独有)——
    var reviewWeeks: [WeeklyReviewEntry] = []
    var reviewSelectedWeek: String? = nil
    var reviewParseWarnings: [String] = []
    var reviewDataWarnings: [String] = []
    var reviewUploading = false
    var reviewHasUploaded = false

    // —— 复盘板块 · 累计页 + 移交件(V2.1-⑦,数据来自 ⑤ 的两条聚合端点)——
    //
    // ⚠ **两张画像账与校准报告不再各自单拉**:`GET /review/overview` 一次给回五段
    // (校准 / 偏好画像 / 能力画像 / 对账 / 观察项),**每段自带 `available`**。
    // 原 macOS 工作台里的「画像」「评价校准报告」两节由本页取代 —— ⛔ 不在两处各画
    // 一遍(同一份数据画两遍就会在两处看到可能不同步的两个版本,同 ② 持仓体检那条)。
    var reviewOverview: ReviewOverview? = nil
    var reviewOverviewLoading = false

    // —— 复盘板块 · 每日页「回看某一天」(V2.3 结 §七 P3-47;K8 §十五 硬需求)——
    //
    // 🔴 **复用 `GET /report?date=`,⛔ 不新建「复盘历史」端点去现连 `basket_review_daily`**
    // (P3-47 原文点名):现连会绕开冻结快照 —— 回看到的复盘就会与**当时那份报告**
    // 讲不同的话,而「回看当时看到的东西」正是这个功能存在的理由。
    //
    // ⚠ **刻意另存一份快照,⛔ 不覆盖 `report`**:`report` 是全 App 的"今天",选股板块
    // 也在读它;把它换成三天前那份,用户切回选股会看到三天前的篮子却毫不知情。
    /// `nil` = 看今天(直接读 `report`)。
    var reviewDailyDate: String? = nil
    var reviewDailyReport: ReportSnapshot? = nil
    var reviewDailyLoading = false
    /// 该日查无报告等诚实空态(服务端 `no_report` 等),⛔ 不弹成通用错误。
    var reviewDailyError: String? = nil

    /// 每日页当前该读哪一份篮子日报:选了日期就读那一份,没选就读今天。
    var reviewDailyBasket: BasketDaily {
        (reviewDailyReport ?? report).basketDaily
    }

    /// 每日页当前这一份报告的交易日(展示用)。
    var reviewDailyTradeDate: String {
        (reviewDailyReport ?? report).tradeDate
    }

    /// 切到某一天(`nil` = 回到今天)。**只动复盘每日页那一份快照。**
    func loadReviewDaily(date: String?) async {
        reviewDailyError = nil
        guard let date else {
            reviewDailyDate = nil
            reviewDailyReport = nil
            return
        }
        reviewDailyDate = date
        guard let client = clientProvider() else {
            reviewDailyError = "未配置后端连接"
            return
        }
        reviewDailyLoading = true
        defer { reviewDailyLoading = false }
        do {
            let snap = try await client.fetchReport(date: date)
            reviewDailyReport = snap
            // 🔴 **降级快照要如实说**:服务端给了 200 但 `degraded=true`(那天没报告)
            // 与"拉取失败"是两回事,⛔ 不合并成一句。
            if snap.degraded {
                reviewDailyError = "该交易日没有报告快照(\(snap.reason))—— ⛔ 不等于那天没有篮子"
            }
        } catch {
            reviewDailyReport = nil
            reviewDailyError = "没取到该日报告:\(error.localizedDescription)"
        }
    }
    /// 累计页看的是哪一周(`YYYYMMDD`,该周任意一天;`nil` = 本周)。
    /// ⚠ **必须能翻周**:周度校准产物是**周六离线作业**落的,周一到周五看"本周"永远是
    /// 「尚未生成」—— 没有翻周入口 = 用户永远看不到上周那份已经算好的成绩单。
    var reviewWeekAnchor: String? = nil
    /// 校准移交件(`GET /review/handoff`)。**按需拉**(用户点导出才拉),
    /// ⛔ 不进主刷新 —— 它要读产物 + 装配 markdown,不该每次开 App 都跑一次。
    var reviewHandoff: ReviewHandoff? = nil
    var reviewHandoffLoading = false

    // 🔴 **熔断状态位已删**(V2.2-⑤-B / 〇b-7):`var circuit` 连同它驱动的横幅、
    // 开仓灰化、解锁弹层一起没了。⛔ **不许以任何名字加回一个「今天别开仓」的状态位**
    // —— 用户裁定 #8 原话:「我不需要你替我做决定;这个程序永远是提醒」。
    // ⚠ `CircuitState` 类型本身仍留在 `Models.swift`(服务端 `PositionsOut.circuit`
    // 恒发空态、零删键),删 DTO 排 v2.3。

    // —— V2.2-② 行情状态(**纯展示、⛔ 无动作**)——
    //
    // 🔴 三态既不是买入背书、也不是禁令:它只回答「今天市场结构是什么样」。
    // `available == false` = **我们今天没算出来**,⛔ 不许当成"没风险"。
    var marketRegime: MarketRegime = .empty

    // —— V2.3.3-⑤ D1 集合竞价确认层(**纯展示、⛔ 零动作**;K8.md §二十)——
    //
    // 🔴 **三态在这里就分好**,⛔ 别让视图层去猜:
    //   · `auction == nil` **且** `auctionCorrupt == false` = 今天还没跑到 9:26 /
    //     竞价层没跑过(404 合法空态)→ **不画那张卡**(⛔ 不画空卡,那是噪声);
    //   · `auctionCorrupt == true` = 有行但读不出(500)→ 画一行「需要排查」,
    //     ⛔ **不许显示成"还没生成"**(那份报告是冻结件、坏了不会自己好 = 让用户白等);
    //   · `auction != nil` = 跑过了。⚠ 它的 `baskets` 为空**也是跑过了**
    //     (D0 当天没有 T1/T2 篮子),原因在 `basketsUnavailableReason` 里。
    // 🔴 **竞价结论只说明竞价反映出的信息,不等于买入指令**(K8 §二十 逐字)。
    var auction: AuctionPayload? = nil
    var auctionCorrupt = false
    /// 竞价小报告五块的弹层开关(收起态那张卡点开)。
    var showAuctionSheet = false

    /// V2.4.0 P3.3-E:数据新鲜度⑤段完整内容的弹层开关(工具栏徽标点开)。
    /// ⚠ **纯导航态**,不参与任何判定 —— 与 `showAuctionSheet` 同一种落法。
    var showFreshnessSheet = false

    // —— V2.2-④ 双时钟 ——
    //
    // 选股时钟:**覆盖 D0 全部 T1/T2,与买没买无关**(K8 §十四)——⛔ 文案别写成
    // 「你关注的篮子」。交易时钟:**只在实际买入后存在**,按仓懒加载。
    var selectionClocks: [SelectionClock] = []
    var selectionClocksLoading = false
    var tradeClocks: [Int: TradeClock] = [:]
    /// 🔴 **「这笔仓没有时钟」与「还没拉到」是两件事**:服务端 404 `not_found` 落进
    /// 这个集合(合法空态),⛔ 不弹错误、⛔ 不显示成通用的「未找到该记录」。
    var tradeClockAbsent: Set<Int> = []
    private var tradeClockLoading: Set<Int> = []

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

    // ⛔ **V2.4.0 P0**:派生状态 `retreatWarning`(退潮刹车激活时的依据一句)已删 ——
    // 退潮判级退役后服务端**永不再产生 active 状态**,留一个恒 nil 的派生位就是
    // 「前端隐藏、后台仍在判」的错觉来源。

    /// **跨板块的「打开持仓板块的哪一屏」请求**(V2.3.1 批 6)。
    ///
    /// 取值 = `PositionsPane` 的 `case` 名(`"alerts"` / `"position"`;
    /// ⛔ **V2.4.0 P0 起没有 `"board"` 了** —— 那一屏整个删掉了)——
    /// ⚠ 用字符串而不是那个枚举:`PositionsPane` 住在 `PositionsView.swift`(视图层),
    /// `AppModel` 反向 import 视图类型会把依赖方向倒过来。
    /// 消费方 `PositionsView` 收到后**立刻置回 `nil`**(它是一次性请求,不是状态)。
    /// ⛔ 别拿它当"当前选中的是哪一屏"用 —— 那个仍然住在 `PositionsView` 自己的 `@State` 里。
    var positionsPaneRequest: String? = nil

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

    // MARK: - 刷新(V2.4.0 P3.6:板块级刷新)
    //
    // 🔴 **原单体 `refresh()` 已拆成四个板块级函数**(施工图 P3.6 表逐字;`board` 按
    // P0 校正落在 `refreshPositions()` 的注释里,不再是独立请求 —— P0 之后
    // `/board` 客户端零调用,持仓事件随 `/positions` 一起下发)。
    //   `refreshSelection()` → report / regime / auction(选股板块)
    //   `refreshPositions()` → positions(**含 P0.5+ 持仓事件**)/ 持仓相关派生
    //   `refreshReview()`    → 当前选中的 review page(⑤ 段 + 选股时钟结案表)
    //   `refreshSettings()`  → settings / provider / push kinds(薄壳,复用既有 `loadSettings()`)
    // **要求**:首次进入 Tab 时加载(`ensureLoaded`)· 工具栏刷新只刷当前 Tab
    // (`refresh(for:)`)· 深链打开某篮 / 某持仓时自动补拉依赖 · 保持既有
    // empty/loading/error/degraded 三态。
    //
    // ⚠ **行情状态取代了原来那一路熔断态**(V2.2-⑤-B 熔断整体退役):两者形似而
    // 语义相反 —— 熔断是**状态锁**(会改变行为),行情状态是**纯展示**(⛔ 零动作)。
    // ⚠ `marketRegime` 是**跨板块共用组件**(`MarketRegimeStrip` 在选股与持仓两页都
    // compact 展示)——`refreshSelection()`/`refreshPositions()` 各自独立请求一次,
    // 与「选股页移除持仓入口后不再拉完整持仓和盘中看板」那条纪律不冲突:那条纪律管的是
    // **重数据**(持仓全量 / 已退役的 `/board`),行情状态是一次轻量请求,双向可见。

    /// 已首次加载过的板块(`ensureLoaded` 用来判断"要不要在首次进入 Tab 时拉一次")。
    private var loadedBoards: Set<AppTab> = []

    /// 首次进入某板块时加载(已加载过则跳过)。深链 / QA 钩子设置的初始 Tab 走这条路
    /// (`RootView.task`),保证不管从哪个 Tab 启动都只拉那个 Tab 需要的数据。
    func ensureLoaded(_ tab: AppTab) async {
        guard !loadedBoards.contains(tab) else { return }
        loadedBoards.insert(tab)
        await refresh(for: tab)
    }

    /// 工具栏 / 下拉刷新走这条(**不受 `loadedBoards` 门控**,用户主动要求就真的去拉)。
    func refresh(for tab: AppTab) async {
        switch tab {
        case .baskets: await refreshSelection()
        case .positions: await refreshPositions()
        case .review: await refreshReview()
        case .settings: await refreshSettings()
        }
    }

    /// 当前选中 Tab 是否在加载中(工具栏 / 刷新胶囊按它决定要不要转圈,⛔ 不再统一读
    /// `reportLoading` —— 那只是选股板块自己的加载位,持仓板块转圈时它不该也转)。
    var isLoadingCurrentTab: Bool {
        switch view {
        case .baskets: return reportLoading
        case .positions: return positionsLoading
        case .review: return reviewOverviewLoading || selectionClocksLoading
        case .settings: return settingsLoading
        }
    }

    /// 选股板块:report / 行情状态 / 竞价小报告(三者并发)。
    func refreshSelection() async {
        guard let client = clientProvider() else {
            loadError = "未配置后端连接"
            return
        }
        reportLoading = true
        loadError = nil
        async let reportTask: Result<ReportSnapshot, Error> = fetchResult { try await client.fetchReportLatest() }
        async let regimeTask: Result<MarketRegime, Error> = fetchResult { try await client.fetchMarketRegime() }
        // V2.3.3-⑤ 竞价小报告:**404 是常态**(一天里只有 9:26 之后才有),
        // 故与其它两路并列拉、失败一律不弹错 —— 三态的分派在下面 switch 里。
        async let auctionTask: Result<AuctionPayload, Error> = fetchResult { try await client.fetchAuction() }
        let (reportResult, regimeResult, auctionResult) = await (reportTask, regimeTask, auctionTask)

        switch reportResult {
        case .success(let r): self.report = r
        case .failure(let e): handleLoadFailure(e, context: "报告")
        }
        switch regimeResult {
        case .success(let r): self.marketRegime = r
        // 🔴 端点**恒 200** → 走到这里只可能是网络 / 鉴权没通。**如实标为没取到**,
        // ⛔ 不拿失败静默换成一个看起来正常的三态(那是把"没看"讲成"没有")。
        case .failure(let e):
            self.marketRegime = MarketRegime(
                available: false,
                unavailableReason: "本次没连上服务端,行情状态未取得(\(e.localizedDescription))")
        }
        // 竞价三态分派(⛔ 别把三者压成一个 `try?`):
        switch auctionResult {
        case .success(let a):
            self.auction = a
            self.auctionCorrupt = false
        case .failure(let e):
            self.auction = nil
            // 只有 500 `auction_corrupt` 才是"坏了要排查";404 / 网络不通都属于
            // 「今天还没有这份报告」——⛔ 不许拿一句错误提示占住选股屏顶部。
            self.auctionCorrupt = (e as? APIError) == .auctionCorrupt
        }
        reportLoading = false
        if loadError == nil { lastRefreshedAt = Date() }
        applyQAHooksAfterRefresh()
    }

    /// 持仓板块:positions(**含 P0.5+ 随行下发的持仓事件**)+ 临时提醒 + 持仓计划。
    /// ⛔ **不拉 `/board`**(P0 之后客户端零调用;施工图 P3.6 原文把 `board` 列在这里是
    /// 与 P0 冲突的一处笔误,以 P0 为准,见施工图 P3.6 段落注)。
    func refreshPositions() async {
        guard let client = clientProvider() else {
            loadError = "未配置后端连接"
            return
        }
        positionsLoading = true
        loadError = nil
        async let positionsTask: Result<PositionsSnapshot, Error> = fetchResult { try await client.fetchPositions() }
        async let alertsTask: Result<[CustomAlert], Error> = fetchResult { try await client.fetchAlerts() }
        let (positionsResult, alertsResult) = await (positionsTask, alertsTask)

        switch positionsResult {
        case .success(let p):
            self.positions = p.holdings
            self.portfolioAlerts = p.portfolioAlerts
        case .failure(let e): handleLoadFailure(e, context: "持仓")
        }
        switch alertsResult {
        case .success(let a): self.alerts = a
        case .failure: break   // 静默降级:提醒列表不是持仓页的主角
        }
        positionsLoading = false
        if loadError == nil { lastRefreshedAt = Date() }
        // 持仓计划要靠它才能算合并敞口 + 展示计划继承卡,随持仓一起拉(每仓一次)。
        await loadAllPositionPlans()
        applyQAHooksAfterRefresh()
    }

    /// 复盘板块:五段汇总 + 选股时钟结案表(与 `ReviewView` 此前的 `loadIfNeeded()`/
    /// `reloadBoard()` 是同一份逻辑,收口到这里、避免两处各写一份)。
    /// ⚠ 「每日」页在没有单独选中某一天时读的是全局 `report`(`reviewDailyBasket`
    /// 的既有 fallback)——正常路径下 `report` 已由 `refreshSelection()`(App 默认
    /// 启动的 `.baskets` Tab)填过;万一直接从复盘板块冷启动(如 QA 钩子),这里补一次。
    func refreshReview() async {
        if reviewDailyDate == nil, report.tradeDate.isEmpty {
            await refreshSelection()
        }
        await loadReviewOverview()
        if selectionClocks.isEmpty && !selectionClocksLoading {
            await loadSelectionClocks()
        }
    }

    /// 设置板块:薄壳,复用既有 `loadSettings()`(该函数已经是「进设置页才拉」的
    /// 独立数据源,本函数只是把它接进 P3.6 的统一板块级刷新入口)。
    func refreshSettings() async {
        await loadSettings()
    }

    /// ⚠ **仅供旧调用点过渡 / 单测复用**:等价于依次刷新选股 + 持仓两板块
    /// (逐位对应 P3.6 之前 `refresh()` 的报告 + 持仓两路)。新代码一律用
    /// `ensureLoaded(_:)` / `refresh(for:)` 按 Tab 精确刷新,⛔ 不要在新代码里
    /// 加这个函数的新调用点。
    func refresh() async {
        await refreshSelection()
        await refreshPositions()
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
        // 持仓板块选仓钩子(V2.3.1 批 3):`NECKLINE_INITIAL_POSITION_ID=<id>`。
        // ⚠ 同上,**必须在数据到位之后** —— `positions` 是 `refresh()` 拉回来的。
        if let raw = env["NECKLINE_INITIAL_POSITION_ID"], let pid = Int(raw),
           positions.contains(where: { $0.id == pid }) {
            selectedPositionId = pid
        }
        // 竞价小报告弹层钩子(V2.3.3-⑤):`NECKLINE_INITIAL_AUCTION_SHEET=1`。
        // ⚠ **必须在数据到位之后** —— 报告是 `refresh()` 拉回来的,`NecklineApp.init()`
        // 里的同步钩子够不着(v1.4-⑧ `NECKLINE_INITIAL_INFOCARD_CODE` 立的先例)。
        // ⛔ 没拉到就不开(开一个空弹层等于把"没有"演成"有")。
        if env["NECKLINE_INITIAL_AUCTION_SHEET"] == "1", auction != nil {
            showAuctionSheet = true
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
        // 复盘板块选页钩子(V2.1-⑦):`NECKLINE_INITIAL_REVIEW_PAGE=daily|cumulative|reconcile`。
        // ⚠ **必须在这里、不能塞进 `NecklineApp.init()`** —— 累计页 / 移交件的内容要等
        // `refresh()` 之后的网络往返才有,`init()` 里够不着(同 `NECKLINE_INITIAL_INFOCARD_CODE`
        // 先例)。⛔ 只影响截图路径:缺此环境变量时行为与之前逐字节相同。
        if let raw = env["NECKLINE_INITIAL_REVIEW_PAGE"], let page = ReviewPage(rawValue: raw) {
            reviewPage = page
            // 五段由 `ReviewView.task` 拉;**移交件是按需拉的**(见 `loadReviewHandoff`
            // docstring),截图钩子得替用户点那一下,否则那一段永远是"点按钮才有"。
            if page == .cumulative { Task { await loadReviewHandoff() } }
        }
        // 同族钩子:`NECKLINE_INITIAL_REVIEW_WEEK=YYYYMMDD` 直接把累计页翻到某一周。
        // ⚠ 本环境 computer-use 点不动模拟器(CLAUDE.md 坑条),翻周箭头点不了 ——
        // 没有这个钩子就**拍不到"有产物的那一周"**(周度产物只在周六作业后才有)。
        if let w = env["NECKLINE_INITIAL_REVIEW_WEEK"], w.count == 8 {
            reviewWeekAnchor = w
            Task { await loadReviewOverview() }
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

    // ⛔ **V2.4.0 P0**:`loadBoard()` 已删(P0.1 表「60 秒客户端 `/board` 专用轮询 = 删」)。
    // `APIClient.fetchBoard()` **保留**(历史 fixture 与旧服务响应仍要能解),但
    // **现役视图与 AppModel 零调用** —— 守门单测按调用点扫。

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
        // ⚠ `roleDisplay` 经 V2.3.1 §〇c 硬伤 2 收口后会**返回空串**(`role_mech=unknown`
        // = 算不出,不是一种角色)—— 空串直接拼进去会留下一个孤零零的「· 」尾巴,
        // 所以这里退回只写来源。⛔ 别为了凑格式补「未知角色」,那是把算不出讲成判断。
        let role = member.roleConflict ? "角色两说并存" : member.roleDisplay
        guard !role.isEmpty else { return "来自篮子「\(name)」" }
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

    // MARK: - V2.2-④ 双时钟(选股时钟只读 / 交易时钟只读 + 一条主观说明)
    //
    // ⚠ **`confirmCircuitReview()` 已删**(V2.2-⑤-B):它是「强制复盘解锁」的客户端
    // 半边,机制整体退役后没有任何写入通道 —— 留着就是假成功面。⛔ 不许接回来。

    /// 已结案的选股时钟。🔴 **样本 = D0 全部 T1/T2,与买没买无关**(K8 §十四)。
    /// 端点恒 200,空列表 = 这段时间没有结案样本(合法),⛔ 不是"系统没跑"。
    func loadSelectionClocks(from: String? = nil, to: String? = nil) async {
        guard let client = clientProvider() else { return }
        selectionClocksLoading = true
        defer { selectionClocksLoading = false }
        do { selectionClocks = try await client.fetchSelectionClocks(from: from, to: to) }
        catch let e as APIError {
            if case .noToken = e {} else { showToast(e.errorDescription ?? "选股时钟读取失败", isError: true) }
        } catch { showToast("选股时钟读取失败:\(error.localizedDescription)", isError: true) }
    }

    /// 某笔仓的交易时钟(按仓懒加载,一仓一次)。
    /// 🔴 **404 `not_found` = 这笔仓没有交易时钟,是合法空态** —— 落进
    /// `tradeClockAbsent`、**不弹错误**。⛔ 别让通用文案「未找到该记录(可能已被删除)」
    /// 出现在这里:那会让用户以为自己的持仓丢了(v1.4 `watchlist` 有案底)。
    func loadTradeClock(positionId: Int) async {
        guard tradeClocks[positionId] == nil, !tradeClockAbsent.contains(positionId),
              !tradeClockLoading.contains(positionId), let client = clientProvider() else { return }
        tradeClockLoading.insert(positionId)
        defer { tradeClockLoading.remove(positionId) }
        do { tradeClocks[positionId] = try await client.fetchTradeClock(positionId: positionId) }
        catch APIError.notFound { tradeClockAbsent.insert(positionId) }
        catch { /* 网络抖动:保持"读取中",下次进页面再试,⛔ 不冒充"没有时钟" */ }
    }

    func beginTradeClockNote(positionId: Int) { modal = .tradeNote(positionId: positionId) }

    /// 追加一条用户主观说明(K8 §十五)。**纯追加**;成功后重拉该仓时钟以带出新事件。
    /// ⚠ 超长 → 服务端 422(`.validation`),⛔ 客户端不静默截断。
    func submitTradeClockNote(positionId: Int, note: String) async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        do {
            let r = try await client.postTradeClockNote(positionId: positionId, note: note)
            tradeClocks[positionId] = nil
            tradeClockAbsent.remove(positionId)
            await loadTradeClock(positionId: positionId)
            dismissModal()
            showToast(r.coverageText.map { "已记下 · \($0)" } ?? "已记下")
        } catch let e as APIError {
            showToast(e.errorDescription ?? "记录失败", isError: true)
        } catch {
            showToast("记录失败:\(error.localizedDescription)", isError: true)
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

    // MARK: - 复盘板块 · 对账页(对账逻辑全在后端 `neckline/review/`,本模型只装配/展示)

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

    // MARK: - 复盘板块 · 累计页 + 移交件(V2.1-⑦;聚合逻辑全在服务端 ⑤,本模型只装配/展示)

    /// 累计页五段(`GET /review/overview`)。**端点恒 200**,空态走各段自己的
    /// `available` —— 故这里拉失败**只可能是网络/鉴权**,那才是 `reviewOverview = nil`
    /// 的含义(界面上说「本次没取到累计复盘」,⛔ 不冒充"五段都没有")。
    func loadReviewOverview(week: String? = nil) async {
        guard let client = clientProvider() else { return }
        reviewOverviewLoading = true
        do {
            reviewOverview = try await client.fetchReviewOverview(week: week ?? reviewWeekAnchor)
            hydrateReviewWeeks()
        }
        catch let e as APIError {
            if case .noToken = e {} else { showToast(e.errorDescription ?? "累计复盘拉取失败", isError: true) }
        } catch { showToast("累计复盘拉取失败", isError: true) }
        reviewOverviewLoading = false
    }

    /// 🔴 **把服务端已有的那一周对账并进工作台**(V2.3.1 批 4)。
    ///
    /// 原来 `reviewWeeks` 的**唯一**写入点是 `uploadReviewFiles` —— 于是重启 App 后
    /// macOS 对账工作台一律说「还没有对账数据 · 把交割单拖到上面」,**而同一份数据
    /// iPhone 那边(累计页对账段)照样看得到**:那是把**「没看」讲成了「没有」**,
    /// 正是本项目一贯要分开的那两件事。
    /// ⛔ **零新增网络调用**:`/review/overview` 的对账段本来就带着整份 `result`。
    /// ⚠ **按周去重、本次上传优先**:刚上传的那一份比服务端上一次落盘的更新。
    private func hydrateReviewWeeks() {
        guard let entry = reviewOverview?.reconcile.weeklyEntry else { return }
        if !reviewWeeks.contains(where: { $0.week == entry.week }) {
            reviewWeeks = (reviewWeeks + [entry]).sorted { $0.week > $1.week }
        }
        if reviewSelectedWeek == nil { reviewSelectedWeek = entry.week }
    }

    /// 累计页翻周:`delta = -1` 上一周 / `+1` 下一周 / `nil` 回到本周。
    /// **纯选参数,不做任何判定** —— 周边界由服务端按交易日历算(客户端给该周任意一天即可)。
    func shiftReviewWeek(_ delta: Int?) async {
        guard let d = delta else {
            reviewWeekAnchor = nil
            await loadReviewOverview()
            return
        }
        let base = reviewWeekAnchor.flatMap { calendar.parseDate($0) }
            ?? reviewOverview.flatMap { calendar.parseDate($0.weekStart) }
            ?? Date()
        let moved = base.addingTimeInterval(TimeInterval(7 * 86400 * d))
        reviewWeekAnchor = calendar.compactString(moved)
        await loadReviewOverview()
    }

    /// 校准移交件(`GET /review/handoff`)。**同样恒 200**:产物没生成 / 读不出都由
    /// `available=false` + `unavailableReason` 如实说,⛔ 客户端不把两者合成一句。
    func loadReviewHandoff(from: String? = nil, to: String? = nil) async {
        guard let client = clientProvider() else {
            showToast("未配置后端连接", isError: true); return
        }
        reviewHandoffLoading = true
        do { reviewHandoff = try await client.fetchReviewHandoff(from: from, to: to) }
        catch let e as APIError {
            showToast(e.errorDescription ?? "移交件拉取失败", isError: true)
        } catch { showToast("移交件拉取失败:\(error.localizedDescription)", isError: true) }
        reviewHandoffLoading = false
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
