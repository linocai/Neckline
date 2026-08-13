//
//  PositionModels.swift
//  Neckline — 客户端展示层数据模型 · 持仓(审计台账,永不代下单)+ 盘中看板 LEGACY + 决策日志 + 挂单追踪
//  + 一键补录预填 + v1.2 枚举展示层换算
//
//  ⚠ **V2.4.0 P3.7 纯机械拆分**:本文件与同目录另外五份 `*Models.swift` 是原
//  `Networking/Models.swift`(5633 行)**逐字**切出来的,⛔ 一个声明没改、没加、没删
//  (切点全在顶层 `// MARK:` 之前的空行上;拆分脚本对拼回来的全文做过逐字节比对)。
//  🔴 **守门单测不再按 `Models.swift` 这个文件名读客户端 DTO** —— 一律走
//  `tests/client_sources.py::networking_swift_text()`(把本目录全部 `.swift` 拼起来)。
//  ⛔ 新增 DTO 文件必须放在本目录下,否则那些「某字段已退役」的**缺席断言**会静默变成
//  真(读不到的文件里当然搜不到),看起来还全绿 —— 这是拆分引入的唯一新风险面,
//  `tests/client_sources.py` 里的哨兵断言就是为它立的。
//
//  ⚠ 加 / 移动 `.swift` 与新增一样,**必须 `xcodegen generate`**(pbxproj 是显式文件引用)。
//

import Foundation

// MARK: - 4A.3 盘中看板 —— ⚠ **LEGACY(V2.4.0 P0:现役界面零引用)**
//
// 下面四个类型服务于 `GET /api/v1/board`。**V2.4.0 起客户端不再请求该端点、
// 不再据它画任何东西**(P0.1 表:盘中动态页 / 退潮红条 / 运行正常绿灯 / 60s 轮询,
// 四行全删)。⛔ **但类型本身不删**(P0.6-8):历史 fixture 与旧服务响应仍要能解,
// 解不出来会让整份响应炸掉 —— 宽松解码是兜底,不是"还在用"。
// 🔴 **守门单测按符号扫「视图层零引用」** —— 它们只允许出现在网络层与测试里。

struct RetreatBrake: Codable, Equatable {
    var active: Bool
    var reason: String
}

/// 哨兵事件中文标签,后端 `_SENTINEL_LABEL` 唯一源(客户端不重译)。v1.1-G.3 补
/// `precall`/`d5exit` 两枚举(盘前校准 / D5 时间退出,标签字面见 `api/app.py::_SENTINEL_LABEL`)。
/// ⚠ V2.4.0 P0:`entry`(买点哨兵,V2-⑬-1 退役)与 `invalidation`(通用盘中证伪,
/// 本版退役)**不再有新事件**,枚举值保留只为解旧 fixture / 历史行 —— ⛔ 别删
/// (删了老数据解不出),也⛔ 别据它画新界面。
/// 🔴 这里的 `invalidation` 是**通用盘中证伪**(义 ①);D0 卡上的「判断失效位置」
/// (`BasketCard.invalidationSpec`)与竞价层的 `hitInvalidation` 是另外两个东西,一行未动。
enum SentinelKind: String, Codable {
    case entry = "买点"
    case invalidation = "证伪"
    case holding = "持仓"
    case precall = "盘前校准"
    case d5exit = "D5退出"
}

struct BoardEvent: Codable, Equatable, Identifiable {
    var sentinel: String     // 买点 | 证伪 | 持仓(见 SentinelKind;未识别值原样展示,不崩)
    var code: String
    var name: String
    var eventKey: String
    var verdict: String      // 判决文案(哨兵已落库的 reason 文本,自然语言,不是模板卡)
    var ts: String

    // id 必须含 code:eventKey 是判定类型名(gap_up_invalidate 等),跨股票共用,
    // 单用它做 ForEach 身份会 id 撞车 → 全列表渲染成第一只票的内容(实机踩过)。
    var id: String { "\(code)|\(eventKey)|\(ts)" }
    var kind: SentinelKind? { SentinelKind(rawValue: sentinel) }
}

struct BoardSnapshot: Codable, Equatable {
    var tradeDate: String
    var asof: String
    var retreatBrake: RetreatBrake
    var events: [BoardEvent]

    static let empty = BoardSnapshot(tradeDate: "", asof: "",
                                     retreatBrake: RetreatBrake(active: false, reason: ""), events: [])
}

// MARK: - 4A.4 持仓(审计台账,永不代下单)

/// 今日**该持仓自己的**一条哨兵提醒(V2.4.0 P0.5+;服务端 `PositionAlertOut`)。
///
/// 逐字段透传服务端已落库的事实,**客户端不重判、不加工**:
///   · `eventKey` —— 事件键(`stop_approach` / `take_profit` / `sector_dive` /
///     `exit_reference` / `position_low_open` / `consecutive_stops` / `decoupled` /
///     `basket<id>`);展示名走 `label`(⛔ 服务端枚举码不许直接进 `Text`,
///     体例同 `nkBoardLabel`,**未识别值原样透传**)。
///   · `verdict` —— 落库当时那句话原文。
///   · `ts` —— 落库时刻(UTC ISO 串,同 `BoardEvent.ts`)。
///   · `level` —— 展示层强调档(`critical` / `warn` / `info`),由服务端按 `eventKey`
///     机械派生,⛔ 客户端别另定一套。
struct PositionAlert: Codable, Equatable, Identifiable {
    var eventKey: String
    var verdict: String
    var ts: String
    var level: String

    var id: String { "\(eventKey)|\(ts)" }

    enum CodingKeys: String, CodingKey { case eventKey, verdict, ts, level }

    init(eventKey: String, verdict: String = "", ts: String = "", level: String = "info") {
        self.eventKey = eventKey; self.verdict = verdict; self.ts = ts; self.level = level
    }

    /// ⚠ B 类以外也一律手写(V2-⑮ 起的通例):合成 `Decodable` 对非 Optional 属性
    /// **有默认值也不容忍缺键**。
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        eventKey = try c.decodeIfPresent(String.self, forKey: .eventKey) ?? ""
        verdict = try c.decodeIfPresent(String.self, forKey: .verdict) ?? ""
        ts = try c.decodeIfPresent(String.self, forKey: .ts) ?? ""
        level = try c.decodeIfPresent(String.self, forKey: .level) ?? "info"
    }

    /// 事件键的展示层换算。**未识别值原样透传**(服务端日后加新 event_key 时,
    /// 界面照样把它显示出来,⛔ 不静默吞掉一条真实提醒)。
    var label: String { nkPositionAlertLabel(eventKey) }

    /// 落库时刻的展示串:UTC ISO → **本地 `HH:mm`**。
    /// ⚠ **解析不出就原样返回那串 ISO**(⛔ 不显示空、⛔ 不编一个时间)——
    /// 一个看不懂但真实的时刻,好过一个看起来漂亮却可能错的时刻。
    var timeLabel: String { nkAlertTimeLabel(ts) }
}

/// UTC ISO8601(带时区)→ 本地 `HH:mm`;**解析失败原样返回入参**。
/// ⚠ 服务端 `sentinel_events.pushed_at` 是 `datetime.now(timezone.utc).isoformat(timespec="seconds")`
/// = `2026-08-12T07:23:12+00:00`,25 个字符直接上屏又长又不像时刻。
func nkAlertTimeLabel(_ raw: String) -> String {
    if raw.isEmpty { return "" }
    let iso = ISO8601DateFormatter()
    iso.formatOptions = [.withInternetDateTime]
    guard let d = iso.date(from: raw) else { return raw }
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")   // 同 `NKFmt` 的口径:⛔ 不跟系统区域漂
    f.dateFormat = "HH:mm"
    return f.string(from: d)
}

/// `PositionAlert.eventKey` → 人读名。⛔ 服务端 `dict` 的 key / 枚举码不许直接进 `Text`
/// (CLAUDE.md 那条「角色码印英文」的同款病);未识别值**原样返回**。
///
/// 🔴 **V2.4.0 复审 🔴-1(裁定 A)补四条**:服务端取数口径由「只取 `holding`」
/// 扩成白名单 `{holding, attention, circuit, precall}`,于是这四个 event_key 会真的
/// 出现在持仓卡上 —— 少一条映射 = 界面上印一串英文码。
/// ⚠ `basket<篮子 id>` 是**动态键**(`attention/basket_peers_weak`),精确 `switch`
///   逮不到 → 单独走前缀分支,⛔ 别把它写死成 `basket12` 这种。
func nkPositionAlertLabel(_ raw: String) -> String {
    switch raw {
    case "stop_approach": return "逼近/触发亏损警戒线"
    case "take_profit": return "回落止盈"
    case "sector_dive": return "所属板块跳水"
    case "exit_reference": return "触达离场参考区间"
    case "position_low_open": return "竞价低开逼近/跌破亏损警戒线"
    case "consecutive_stops": return "连续止损提醒"
    case "decoupled": return "从跟随板块转为独立弱势"
    default:
        if raw.hasPrefix("basket") { return "同篮成员集体转弱" }
        return raw
    }
}

/// 回落止盈状态(§五 v1.1-B.1,服务端 `_retrace_state` 算好下发:峰值 / 回落幅度 /
/// 是否触发——判定复用 `sentinel/holding.py::check_take_profit`,客户端只展示,不重算阈值)。
struct RetraceState: Codable, Equatable {
    var peak: Double
    var retracePct: Double
    var triggered: Bool
}

/// K4 持仓牌单条命中(v1.3-② / §五 v1.3-⑥-C)。服务端 16:35 EOD 面板上对持仓票重算
/// K4 advisory 命中,客户端只展示不重算。
///  · `level`:strong(强警示,置顶醒目)| normal(普通警示,进列表)。
///  · `evidenceStrength`:price_volume(价量硬数据,强证据)| constituent(概念板块成分,
///    弱证据,标「参考」——题材持续天数依赖 `ths_member` 快照,不单独触发强警示)。
///  · 只有「level=strong ∧ evidenceStrength=price_volume」才置顶醒目展示(疑似派发/换手
///    异常等);其余(含 strong 但成分类证据、或 normal)一律降级为列表/chip 展示。
struct K4Advisory: Codable, Equatable, Identifiable {
    var code: String
    var label: String
    var level: String              // strong | normal
    var evidence: String
    var evidenceStrength: String   // price_volume | constituent

    var id: String { code }
    var isStrong: Bool { level == "strong" }
    var isPriceVolumeEvidence: Bool { evidenceStrength == "price_volume" }
    /// 置顶醒目的判据(§五 v1.3-⑥-C 硬约束,不是「strong 就置顶」——弱证据即便标了
    /// strong 也只降级展示,守 §2.4 铁律「证伪只用价量结构」)。
    var isTopBillboard: Bool { isStrong && isPriceVolumeEvidence }
}

/// v1.3-① 两档时间退出态(服务端权威判定,§2.1 第 2 条;客户端只展示,不重算净浮盈)。
/// 未识别字符串兜底 `.holding`(不误报离场——宁可少提醒,不可错误地把未知态判成「该走了」)。
enum PositionTimeExitState: Equatable {
    static let timeExitNextDayRaw = "time_exit_next_day"
    static let profitExemptRaw = "profit_exempt"
    static let hardCapExitRaw = "hard_cap_exit"
    static let holdingRaw = "holding"
    static let suspendedHoldRaw = "suspended_hold"   // v1.4-①-B(§七 P0-2)

    case timeExitNextDay   // 非浮盈,次日按计划离场
    case profitExempt      // 浮盈豁免时间退出,交回落止盈+止损管到硬上限——**持有态,非离场提示**
    case hardCapExit       // 已达浮盈硬上限(D15),次日无条件离场
    case holding           // 常规持有(K1 单档下恒为此值或 timeExitNextDay)
    // v1.4-①-B:当日无 EOD 行(停牌/数据缺口)且尚未定格 → 判向挂起,不推 D5/硬上限
    // 提醒;`dCount` 照常按交易日累计并展示。复牌当日 16:35 用复牌当日 EOD 正常定格。
    case suspendedHold

    init(_ raw: String) {
        switch raw {
        case Self.timeExitNextDayRaw: self = .timeExitNextDay
        case Self.profitExemptRaw: self = .profitExempt
        case Self.hardCapExitRaw: self = .hardCapExit
        case Self.suspendedHoldRaw: self = .suspendedHold
        default: self = .holding
        }
    }
}

/// 持仓票价格陈旧度(v1.4-①-B,§七 P0-2)。当日**无 EOD 行**时才会有值(正常票不背这个
/// 字段的负担,`Position.priceStale` 为 `nil`)——`reason` 三态:`suspended`(停牌名单
/// 命中)/ `data_gap`(全市场当日有数据但唯独这只没有)/ `unknown`(停牌名单本身拿不到,
/// 如实说不知道,绝不猜成 suspended)。**绝不静默把老价当今日价**——这个类型就是那句
/// 「静默」的解药。
struct PriceStale: Codable, Equatable {
    var staleDays: Int
    var lastCloseDate: String    // 'YYYYMMDD';回看窗口内都找不到 → ""(如实留空,不臆造)
    var reason: String           // suspended | data_gap | unknown

    var reasonLabel: String {
        switch reason {
        case "suspended": return "停牌"
        case "data_gap": return "数据缺口"
        case "unknown": return "原因未知"
        default: return reason
        }
    }
}

struct Position: Codable, Equatable, Identifiable {
    var id: Int
    var code: String
    var name: String
    var buyPrice: Double
    var qty: Int
    var entryReason: String
    var buyDate: String      // 'YYYYMMDD',服务端字面口径(见 sentinel/positions.py)
    var price: Double        // 哨兵最近一拍 / EOD 兜底;拉不到 → 0.0(不可与"跌停 0 元"混淆,UI 需判断)
    var status: String
    var stopLine: Double     // 服务端派生 = buy×0.95(§2.1 单一常量),客户端不重算
    // —— V2.3.2-⑤(K8.md §十九):这条线的**对外语义**——————————————————————————
    /// 🔴 **数值口径一字未变**(仍是 `buy×(1−stop_pct)`),变的是它触发什么:
    /// `lossWarningAction == "review"` = 到线只发**亏损警戒**、离场决策在你,系统不代下单。
    /// **nil = 现役章程没有声明过这个语义**(老章程行里就没有这两个字段)→ 展示层退回
    /// 「止损线」老文案,⛔ 不许当成"声明为强制条件单"、也⛔ 不许拿它当"配置丢了"。
    var lossWarningPct: Double? = nil
    var lossWarningAction: String? = nil
    /// 🔴 **V2.4.0 P3.1 新增**:现役章程的 `take_profit_retrace`(与 `lossWarningPct`
    /// 同一次服务端 `_active_config()` 读出)。`nil` = **该章程没有配置回落止盈**
    /// (`v2.3-k8` 起的常态,K8.md §十三)——展示层据此把「回落止盈」那一格显示成
    /// 「本版无机械回落止盈」,⛔ 不再靠 `retraceState.triggered == false` 反推
    /// "没有这项纪律"(那个位只回答"触发了没有",无法区分"没配置"与"配置了但还没
    /// 触发",两者必须讲不同的话)。⚠ **不参与任何判定**,只是文案指纹。
    var takeProfitRetrace: Double? = nil
    var stopOrderChecked: Bool
    // —— §五 v1.1-B.1/E.1 持仓生命周期派生字段(服务端算好,客户端不重算日历/阈值)——
    var dCount: Int = 1              // D 计数(买入日=D1,唯一源 sentinel/positions.py::d_count)
    /// 现役 `max_hold_days`(读 config,不硬编 5)。
    /// 🔴 **V2.2-⑤ 起可为 nil = 本版章程无时间退出条款**(`v2.2-k8`,K8 §十三:时间退出
    /// 让位主观换股权)。**取值域放宽,不是删键**。⛔ 拿 5 顶上冒充"有时间退出"是本项目
    /// 反复禁止的那类谎 —— nil 时展示层走 `timeExitDisclosure`,不显示任何 D 上限。
    var maxHoldDays: Int? = 5
    var distToStopPctServer: Double? = nil   // 服务端算好的距止损线百分比(小数,非 ×100);无实时价 → nil
    var retraceState: RetraceState? = nil
    var todayAction: String = ""     // 今日动作提示文案(D5离场/距止损/回落止盈已触发等,服务端定文案)
    // —— v1.3-① 两档时间退出(服务端按 D5 净浮盈判好下发,客户端不重算)——————————————
    /// 该单有效硬上限:非浮盈=maxHoldDays;浮盈豁免=硬上限(如 15)。
    /// 🔴 **同样自 V2.2-⑤ 起可为 nil**(章程无时间退出条款 → 根本没有"有效硬上限"这回事)。
    var maxHoldDaysEffective: Int? = 5
    var timeExitState: String = "holding"
    // —— v1.3-① 费用回显(实付,供周复盘对账用真数;nil=未录)——————————————————————
    var buyFees: Double? = nil
    var sellFees: Double? = nil
    // —— v1.4-①-B 停牌 / 无行情持仓票的显式标注(§七 P0-2)————————————————————————
    /// 当日无 EOD 行时给出「陈旧几个交易日 / 最后成交日 / 为什么」三件;当日有行 → nil
    /// (正常票不背这个字段的负担)。
    var priceStale: PriceStale? = nil
    /// K4 每日体检是否因无 EOD 行被整份跳过。**三值**:true=没体检 / false=体检过了
    /// (空 `k4Advisory` 才等于「体检过没问题」)/ nil=老快照未记录(如实说不知道,不冒充 false)。
    var k4DataUnavailable: Bool? = nil
    // —— v1.4-⑥-C 定格日 ≠ D5 显式标注(§七 P1-6)——————————————————————————————————
    /// 定格发生当时的 `dCount`;nil=尚未定格(或老快照缺记录),**不拿今天冒充定格日**。
    var timeExitLockedDay: Int? = nil
    /// = `timeExitLockedDay − maxHoldDays`,下限 0;客户端 **>0 才展示**
    /// 「定格于 D{n},晚于 D{maxHoldDays} {k} 天」。⛔ 只提示,不改判定逻辑。
    var timeExitLockedLateDays: Int = 0
    // —— v1.3-② K4 持仓牌(服务端 16:35 EOD 重算命中;老快照/刚开仓未体检 → 空数组,
    // 前向兼容不特判)——————————————————————————————————————————————————————————
    var k4Advisory: [K4Advisory] = []
    // 该持仓是否有关联决策日志(via position_id)含非空情景树待每日对照(v1.3-②-D 提醒)。
    // ⚠ 🔵-5 小审 2026-08-03 措辞订正:原注释称"勾选仍走既有 `POST /decisions/{id}/
    // scenario-outcome`"——V2-⑩-C 起 `decision_log` 停写留档,该端点与客户端对应方法
    // `setScenarioOutcome` 均已物理删除(见 `APIClient.swift:555`)。本字段现在纯只读
    // 展示「挑出来」,不再有任何写回动作,别被这句话误导去把调用接回来。
    var scenarioReviewPending: Bool = false
    /// **V2.4.0 P0.5+:今日该持仓自己的哨兵提醒**(源 `sentinel_events` 的
    /// `sentinel='holding'` 行,服务端随 `/positions` 一起下发)。
    ///
    /// 🔴 **它是「先迁移再删页面」的落点,不是「盘中动态页换了个地方」**:亏损警戒 /
    /// 离场参考 / 板块跳水此前**只经 `GET /board`** 下发,而 V2.4.0 客户端零调用该端点 ——
    /// 直接删页面就会静默弄丢这些仍然有效的提醒(P0.3 末段明令)。
    /// ⛔ **只画该持仓自己的事件**:⛔ 无市场级行、⛔ 无「运行正常」绿灯、⛔ 无轮询
    /// (随 `refreshPositions()` / `refresh()` 一起拉,**不新增任何请求**)、
    /// ⛔ 客户端不做任何二次裁定(服务端落库时那句话原样展示)。
    /// ⚠ 老服务端不发这个键 → 空数组(与"今天没有提醒"同形,如实为空,不编)。
    var alerts: [PositionAlert] = []

    /// 显式 CodingKeys(`distToStopPctServer` 与服务端字面 `distToStopPct` 改了名——避免
    /// 和下面既有的、语义不同的客户端计算属性 `distToStopPct` 撞名;其余字段名与 JSON
    /// 字面一致)。**本类型自 v1.3-⑥ 起改手写 `init(from:)`**(见下)——`maxHoldDaysEffective`/
    /// `timeExitState`/`k4Advisory`/`scenarioReviewPending` 等虽非 Optional 但要容忍旧
    /// fixture/旧快照缺键(Swift 合成 Decodable 对非 Optional 属性不会自动容忍缺键,
    /// 默认值只影响 memberwise init、不影响解码,同 `Candidate` 这一版的处理姿势)。
    enum CodingKeys: String, CodingKey {
        case id, code, name, buyPrice, qty, entryReason, buyDate, price, status, stopLine, stopOrderChecked
        case lossWarningPct, lossWarningAction, takeProfitRetrace
        case dCount, maxHoldDays, retraceState, todayAction
        case distToStopPctServer = "distToStopPct"
        case maxHoldDaysEffective, timeExitState, buyFees, sellFees, k4Advisory, scenarioReviewPending
        case priceStale, k4DataUnavailable, timeExitLockedDay, timeExitLockedLateDays
        case alerts
    }

    init(id: Int, code: String, name: String, buyPrice: Double, qty: Int, entryReason: String,
         buyDate: String, price: Double, status: String, stopLine: Double, stopOrderChecked: Bool,
         lossWarningPct: Double? = nil, lossWarningAction: String? = nil,
         takeProfitRetrace: Double? = nil,
         dCount: Int = 1, maxHoldDays: Int? = 5, distToStopPctServer: Double? = nil,
         retraceState: RetraceState? = nil, todayAction: String = "",
         maxHoldDaysEffective: Int? = 5, timeExitState: String = "holding",
         buyFees: Double? = nil, sellFees: Double? = nil,
         priceStale: PriceStale? = nil, k4DataUnavailable: Bool? = nil,
         timeExitLockedDay: Int? = nil, timeExitLockedLateDays: Int = 0,
         k4Advisory: [K4Advisory] = [], scenarioReviewPending: Bool = false,
         alerts: [PositionAlert] = []) {
        self.id = id; self.code = code; self.name = name; self.buyPrice = buyPrice; self.qty = qty
        self.entryReason = entryReason; self.buyDate = buyDate; self.price = price; self.status = status
        self.stopLine = stopLine; self.stopOrderChecked = stopOrderChecked
        self.lossWarningPct = lossWarningPct; self.lossWarningAction = lossWarningAction
        self.takeProfitRetrace = takeProfitRetrace
        self.dCount = dCount; self.maxHoldDays = maxHoldDays; self.distToStopPctServer = distToStopPctServer
        self.retraceState = retraceState; self.todayAction = todayAction
        self.maxHoldDaysEffective = maxHoldDaysEffective; self.timeExitState = timeExitState
        self.buyFees = buyFees; self.sellFees = sellFees
        self.priceStale = priceStale; self.k4DataUnavailable = k4DataUnavailable
        self.timeExitLockedDay = timeExitLockedDay; self.timeExitLockedLateDays = timeExitLockedLateDays
        self.k4Advisory = k4Advisory; self.scenarioReviewPending = scenarioReviewPending
        self.alerts = alerts
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        code = try c.decode(String.self, forKey: .code)
        name = try c.decode(String.self, forKey: .name)
        buyPrice = try c.decode(Double.self, forKey: .buyPrice)
        qty = try c.decode(Int.self, forKey: .qty)
        entryReason = try c.decode(String.self, forKey: .entryReason)
        buyDate = try c.decode(String.self, forKey: .buyDate)
        price = try c.decode(Double.self, forKey: .price)
        status = try c.decode(String.self, forKey: .status)
        stopLine = try c.decode(Double.self, forKey: .stopLine)
        stopOrderChecked = try c.decode(Bool.self, forKey: .stopOrderChecked)
        // V2.3.2-⑤:老服务端不发这两键 / 老章程行发 null,两种情况都 → nil = 未声明。
        // 这里**不必**像 `maxHoldDays` 那样区分「缺键 vs 显式 null」——两者语义相同。
        lossWarningPct = try c.decodeIfPresent(Double.self, forKey: .lossWarningPct)
        lossWarningAction = try c.decodeIfPresent(String.self, forKey: .lossWarningAction)
        // V2.4.0 P3.1:老服务端不发这个键 / 章程未配置回落止盈,两种情况都 → nil
        // (与 `lossWarningPct` 同一条纪律,缺键与显式 null 语义相同,不必区分)。
        takeProfitRetrace = try c.decodeIfPresent(Double.self, forKey: .takeProfitRetrace)
        dCount = try c.decodeIfPresent(Int.self, forKey: .dCount) ?? 1
        // 🔴 **「缺键」与「显式 null」在这里语义相反,必须分开**(V2.2-⑤):
        //   · **缺键** = 真·老服务端 / 老 fixture(v1.1 之前根本没有这个字段)→ 按当时
        //     的单档口径补 5,老断言逐位不变;
        //   · **显式 null** = **本版章程无时间退出条款**(`v2.2-k8`)→ 如实 nil,
        //     ⛔ 不许拿 5 顶上冒充"有时间退出"。
        // `decodeIfPresent` 两种情况都返回 nil、区分不了 → 用 `contains(_:)` 判键在不在
        // (它对显式 null 返回 true)。⛔ 别"简化"回 `?? 5`,那会让新章程静默显示 D5。
        maxHoldDays = c.contains(.maxHoldDays)
            ? try c.decodeIfPresent(Int.self, forKey: .maxHoldDays)
            : 5
        distToStopPctServer = try c.decodeIfPresent(Double.self, forKey: .distToStopPctServer)
        retraceState = try c.decodeIfPresent(RetraceState.self, forKey: .retraceState)
        todayAction = try c.decodeIfPresent(String.self, forKey: .todayAction) ?? ""
        maxHoldDaysEffective = c.contains(.maxHoldDaysEffective)
            ? try c.decodeIfPresent(Int.self, forKey: .maxHoldDaysEffective)
            : maxHoldDays
        // 缺键(真正的旧服务端/旧 fixture,v1.3-① 前)→ 按旧单档口径派生(dCount>=maxHoldDays
        // 才算到期),与「服务端本该发什么」逐位一致——不是拍脑袋的"holding"兜底,而是精确
        // 复现 v1.1 单档时间退出行为,故老 fixture 的 isExitDay 断言不必因这次改动而重写。
        // ⚠ **无上限(nil)时这条派生整个不成立** → 只能是 `holding`(没有"到期"这回事)。
        timeExitState = try c.decodeIfPresent(String.self, forKey: .timeExitState)
            ?? ((maxHoldDays.map { dCount >= $0 } ?? false)
                ? PositionTimeExitState.timeExitNextDayRaw : PositionTimeExitState.holdingRaw)
        buyFees = try c.decodeIfPresent(Double.self, forKey: .buyFees)
        sellFees = try c.decodeIfPresent(Double.self, forKey: .sellFees)
        priceStale = try c.decodeIfPresent(PriceStale.self, forKey: .priceStale)
        k4DataUnavailable = try c.decodeIfPresent(Bool.self, forKey: .k4DataUnavailable)
        timeExitLockedDay = try c.decodeIfPresent(Int.self, forKey: .timeExitLockedDay)
        timeExitLockedLateDays = try c.decodeIfPresent(Int.self, forKey: .timeExitLockedLateDays) ?? 0
        k4Advisory = try c.decodeIfPresent([K4Advisory].self, forKey: .k4Advisory) ?? []
        scenarioReviewPending = try c.decodeIfPresent(Bool.self, forKey: .scenarioReviewPending) ?? false
        alerts = try c.decodeIfPresent([PositionAlert].self, forKey: .alerts) ?? []
    }

    var hasLivePrice: Bool { price > 0 }
    var pnlPct: Double {
        guard hasLivePrice, buyPrice > 0 else { return 0 }
        return (price - buyPrice) / buyPrice * 100
    }
    var pnlAmount: Double {
        guard hasLivePrice else { return 0 }
        return (price - buyPrice) * Double(qty)
    }
    /// 距止损线百分比(正 = 尚有缓冲,负 = 已破线);无实时价 → nil,UI 不误显 0%。
    /// 客户端派生(与服务端 `distToStopPctServer` 算法一致,同一口径,仅百分比展示单位不同),
    /// 保留是因为早于 B.1 已有该计算且被既有单测覆盖;新代码可直接读 `distToStopPctServer`。
    var distToStopPct: Double? {
        guard hasLivePrice, price > 0 else { return nil }
        return (price - stopLine) / price * 100
    }
    /// 已破 -5% 止损线(展示红色警示;真实止损执行在券商条件单,系统只审计)。
    var hasBrokenStop: Bool {
        guard hasLivePrice else { return false }
        return price <= stopLine
    }

    // —— V2.3.2-⑤ 退出字段语义换血(K8.md §十三 / §十九)———————————————————————
    //
    // 🔴 **纯展示层换算,零判定**:`stopLine` 的数值、`hasBrokenStop` 的判据、刻度尺的
    // 几何**一个都没动**。这里只回答「这条线该叫什么、到线之后谁来决定」。
    // ⛔ 别把它写成固定文案 —— 章程回滚到强制条件单口径时,界面必须跟着回去。

    /// 现役章程是否声明了「亏损警戒 + 由用户完成离场决策」(K8.md §十九 的 `review`)。
    /// **未声明(nil)→ false**:退回老文案,⛔ 不替一版没说过这话的章程发言。
    var isLossWarningCharter: Bool { lossWarningAction == "review" }

    /// 这条线的称呼:亏损警戒口径 →「亏损警戒线」;否则 →「止损线」。
    var stopLineLabel: String { isLossWarningCharter ? "亏损警戒线" : "止损线" }

    /// 紧凑位专用的**三字**称呼(「警戒线」/「止损线」)。⚠ 存在的唯一理由是 iPhone
    /// 402pt 宽度:列表卡那一行是「状态徽标 + 线价位 + 现价 + 距线百分比」的横向密集行,
    /// 把「止损线」换成「亏损警戒线」多两个字就可能把它挤成两行(CLAUDE.md 有案底,
    /// **编译与单测都发现不了**)。全称版留给详情卡与那句披露。
    var stopLineShortLabel: String { isLossWarningCharter ? "警戒线" : "止损线" }

    /// 🔴 **V2.4.0 P3.1:止损刻度尺上那根红刻度的名字**(`NKStopScale.stopLabel`)。
    /// 亏损警戒口径 →「警戒线」;**老口径逐字不变的「止损」**(⛔ 不是「止损线」——
    /// 那把尺上的标签历来只写两个字,P3.1 只改 K8 这一侧,老章程的图一个像素不动)。
    /// ⚠ 这是同一屏上**最后一处**还在写死「止损」的地方:徽标 / 四格 / 那句解释
    /// V2.3.2-⑤ 都已改口,唯独尺上漏了 —— 一屏两个名字正是那次实拍逮到的病。
    var stopScaleMarkLabel: String { isLossWarningCharter ? stopLineShortLabel : "止损" }

    /// 亏损警戒口径下必须说出口的那句(否则 nil,不啰嗦)。
    /// ⚠ 这是**语义披露**,不是操作建议 —— 系统永不代下单、永不自动卖出。
    /// 🔴 **V2.4.0 P3.1**:「触发后由你复核原判断」是 K8.md §十九 逐字(取代旧措辞
    /// 「离场决策在你」——旧措辞没点名"复核"这个动作,容易被读成"系统已经判完、
    /// 只是不动手";与服务端 `charter_copy.ADVISORY_ACTION_PHRASE` 用词一致)。
    var lossWarningDisclosure: String? {
        guard isLossWarningCharter else { return nil }
        let pct = lossWarningPct.map { String(format: "−%.0f%%", $0 * 100) } ?? "章程比例"
        return "到线(\(pct))只发亏损警戒,触发后由你复核原判断 —— 系统不代下单、不自动卖出"
    }

    /// 现役章程是否配置了回落止盈这项机械纪律。`false` = `v2.3-k8` 起的常态
    /// (K8.md §十三:「K8 不设置机械回落止盈」),⛔ 不是"没取到"。
    var hasMechanicalRetrace: Bool { takeProfitRetrace != nil }

    /// 🔴 **V2.4.0 P3.1:这条纪律该说哪句话**(三态,⛔ 不许合并成两态)——
    ///   · 已触发 → 由调用方画那句红字(本属性返回 nil,不重复说);
    ///   · **章程配了回落止盈**(历史章程常态)→ 「回落止盈 8.0%」**说出那个比例**
    ///     (⛔ 不许因为今天的章程没有它就把历史也改口 —— K8.md §十三 末句);
    ///   · 章程没配 → 「本版无机械回落止盈」(`retraceDisabledDisclosure`)。
    /// ⚠ 比例来自服务端下发的 `takeProfitRetrace`,**客户端不重算、不写死**。
    var retraceRuleLine: String? {
        if retraceState?.triggered == true { return nil }
        if let pct = takeProfitRetrace, pct > 0 {
            return "回落止盈 " + String(format: "%.1f%%", pct * 100)
        }
        return retraceDisabledDisclosure
    }

    /// 「本版无机械回落止盈」那句(未配置时才有,已配置 → nil,不啰嗦)。
    /// 🔴 **这是对「回落止盈 8%」这类历史文案的正面回答**——`retraceState.triggered`
    /// 只答"触发了没有",答不了"这项纪律存不存在";v2.3-k8 下这句必须**主动说出口**,
    /// ⛔ 不能靠"没触发过"这件事的沉默来暗示。
    var retraceDisabledDisclosure: String? {
        hasMechanicalRetrace ? nil : "本版无机械回落止盈"
    }

    // —— §五 v1.1-E.1/v1.3-⑥-A 展示层派生(纯视觉强度选择,文案本身来自服务端
    // `todayAction`,这里只按服务端权威 `timeExitState` 两态选颜色/是否醒目横幅,
    // 不重新推导任何领域判定,同 `hasBrokenStop` 的展示层派生先例)。

    /// 服务端两档时间退出态的展示层枚举(见 `PositionTimeExitState`)。
    var timeExitKind: PositionTimeExitState { PositionTimeExitState(timeExitState) }

    // —— V2.2-⑤ 章程按 K8 持仓原则修订:**时间退出让位主观换股权** ——————————
    //
    // 🔴 `maxHoldDaysEffective == nil` = **本版章程没有时间退出条款**(不是"读不到")。
    // ⛔ 不许显示成 `D3/D5` 这类假上限,也不许显示成 `D3/D0`。

    /// 本单是否受时间退出条款约束。
    var hasTimeExitRule: Bool { maxHoldDaysEffective != nil }

    /// D 徽标文案。无时间退出条款时**只报 D 计数**(它仍是有用的持有天数记录)。
    var dBadgeText: String {
        guard let cap = maxHoldDaysEffective else { return "D\(dCount)" }
        return "D\(dCount)/D\(cap)"
    }

    /// 无时间退出条款时那句必须说出口的话;有条款时 nil(不啰嗦)。
    /// 🔴 **V2.4.0 P3.1 起改用 K8.md §十三 的逐字措辞**「本版无机械时间退出 —— D 计数
    /// 只作记录」——与服务端 `charter_copy.TIME_EXIT_DISABLED_COPY` 以及它拼进
    /// `todayAction` 的那句**同一套词**。⚠ **改口不是改判定**:`maxHoldDaysEffective`
    /// 仍是唯一判据、D 徽标仍照旧,变的只是这句话怎么说。⛔ 别再回到「无时间退出条款」
    /// 那种章程内部术语 —— 用户读的是界面,不是章程表的列名。
    var timeExitDisclosure: String? {
        guard maxHoldDaysEffective == nil else { return nil }
        return "本版无机械时间退出 —— D 计数只作记录,不构成离场提示(K8:换股由你主观决定)"
    }

    /// 是否该醒目展示为「离场/到期」(两档:非浮盈到期 `timeExitNextDay` 或浮盈硬上限到期
    /// `hardCapExit`)。**`profitExempt` 不算**——它是持有态(交回落止盈+止损管到硬上限),
    /// §五 v1.3-⑥-A 明文「不要当离场提示展示」,故不能再用旧口径 `dCount >= maxHoldDays`
    /// 判定(那样会把「浮盈豁免续持到 D15」的正常单错误标红成「该走了」)。
    var isExitDay: Bool { timeExitKind == .timeExitNextDay || timeExitKind == .hardCapExit }

    var todayActionTone: NKAxisTone {
        if isExitDay { return .bad }
        if timeExitKind == .profitExempt { return .good }   // 浮盈豁免:持有态,非警示,给个正向色调
        // v1.4-①-B:判向挂起(停牌/无当日行情)——警示级但非"该走了",价格本身是陈旧的,
        // 不该被下面的距止损/回落止盈信号(基于陈旧价算出)误染成更高优先级的警示。
        if timeExitKind == .suspendedHold { return .warn }
        if retraceState?.triggered == true { return .bad }
        if let d = distToStopPctServer {
            if d <= 0 { return .bad }
            if d <= 0.02 { return .warn }
        }
        return .neutral
    }
}

// MARK: - v1.2 枚举展示层换算(服务端码 + 客户端展示层换算,沿 `nkBoardLabel` 先例;
// 未识别码原样透传,不静默瞎翻译)。自由函数用于「解码任意历史码做展示」的场景
// (如 `DecisionLog.thesisTags`);下面各 `CaseIterable` 枚举用于「录入表单的有限
// 可选项 picker」场景——两者共用同一份 label 映射,不重复定义第二份中文对照表。

func nkThesisTagLabel(_ raw: String) -> String {
    switch raw {
    case "THEME": return "题材主线"
    case "SENTIMENT_CYCLE": return "情绪周期位"
    case "CAPITAL_FLOW": return "资金流向"
    case "TECH_PATTERN": return "技术形态"
    case "NEWS": return "消息"
    default: return raw
    }
}

/// ⑤ 论点标签(v1.2-B,多选)。
enum ThesisTag: String, CaseIterable, Identifiable, Hashable, Codable {
    case theme = "THEME"
    case sentimentCycle = "SENTIMENT_CYCLE"
    case capitalFlow = "CAPITAL_FLOW"
    case techPattern = "TECH_PATTERN"
    case news = "NEWS"

    var id: String { rawValue }
    var label: String { nkThesisTagLabel(rawValue) }
}

func nkPlaybookTagLabel(_ raw: String) -> String {
    switch raw {
    case "SWING_CHASE": return "短线追击"
    case "BREATHING_TRIAL": return "呼吸底仓试验"
    default: return raw
    }
}

/// ⑧ 打法标签(v1.2-B,单选;对应三仓 = 2 短线追击 + 1 呼吸底仓试验,§2.1 第 3 条)。
enum PlaybookTag: String, CaseIterable, Identifiable, Hashable, Codable {
    case swingChase = "SWING_CHASE"
    case breathingTrial = "BREATHING_TRIAL"

    var id: String { rawValue }
    var label: String { nkPlaybookTagLabel(rawValue) }
}

func nkScenarioActionLabel(_ raw: String) -> String {
    switch raw {
    case "BUY": return "买入"
    case "HOLD": return "持有"
    case "REDUCE": return "减仓"
    case "ABANDON": return "放弃"
    default: return raw
    }
}

/// ⑦ 应对方案·情景树的动作枚举(v1.2-B)。
enum ScenarioAction: String, CaseIterable, Identifiable, Hashable, Codable {
    case buy = "BUY", hold = "HOLD", reduce = "REDUCE", abandon = "ABANDON"

    var id: String { rawValue }
    var label: String { nkScenarioActionLabel(rawValue) }
}

func nkCloseReasonLabel(_ raw: String) -> String {
    switch raw {
    case "STOP_LOSS": return "止损"
    case "TAKE_PROFIT": return "回落止盈"
    case "TIME_EXIT": return "时间退出"
    case "INVALIDATION": return "证伪离场"
    case "MANUAL": return "主动离场"
    // —— v2.0.0(⑩-A)蓝图 §5.2 卖出快捷标签新增四码 ——————————————————————
    case "SECTOR_WEAKENING": return "板块转弱"
    // ⛔ **不许写成「止盈」**:离场参考区间不是止盈线(§2.8-C 语义红线)——
    // 离场参考是计划参考,是否离场由用户判断(V2.4.0 P3.2)。码名与文案都要守住这条。
    case "TARGET_ZONE_REACHED": return "达到参考区间"
    case "ACTIVE_SWITCH": return "主动切换"
    case "AD_HOC": return "临时决定"
    default: return raw
    }
}

/// 离场原因(`PositionCloseIn.closeReason`)。v2.0.0(⑩-A)起**九码**:既有五码原样
/// 不动、只加不改;熔断判据「是否止损离场」只看 `STOP_LOSS`,新增四码不改任何纪律判定。
/// 不选则服务端按价格兜底判止损(见 CLAUDE.md「熔断兜底判据」坑)。
enum CloseReasonCode: String, CaseIterable, Identifiable, Hashable, Codable {
    case stopLoss = "STOP_LOSS"
    case takeProfit = "TAKE_PROFIT"
    case timeExit = "TIME_EXIT"
    case invalidation = "INVALIDATION"
    case manual = "MANUAL"
    case sectorWeakening = "SECTOR_WEAKENING"
    case targetZoneReached = "TARGET_ZONE_REACHED"
    case activeSwitch = "ACTIVE_SWITCH"
    case adHoc = "AD_HOC"

    var id: String { rawValue }
    var label: String { nkCloseReasonLabel(rawValue) }
}

/// 蓝图 §2.2「用户可选补充」七枚标签(`POST /decisions` 的 `labels`,落 `user_actions`)。
/// **服务端只存英文码**(`schemas.NoteLabelLiteral` 唯一源),中文在此换算 —— 同
/// `board`/`NewsCategory`/`CloseReasonCode` 三处的既定体例。⛔ 不另造一套中文键。
func nkNoteLabelText(_ raw: String) -> String {
    switch raw {
    case "THEME_SHIFT": return "题材切换"
    case "LEADER_REACTIVATE": return "龙头重新激活"
    case "VOLUME_BREAKOUT": return "放量突破"
    case "WEAK_TO_STRONG": return "弱转强"
    case "CORE_POSITION": return "容量中军"
    case "NEWS_CATALYST": return "消息催化"
    case "PURE_TAPE_READING": return "纯盘口判断"
    default: return raw
    }
}

enum NoteLabel: String, CaseIterable, Identifiable, Hashable, Codable {
    case themeShift = "THEME_SHIFT"
    case leaderReactivate = "LEADER_REACTIVATE"
    case volumeBreakout = "VOLUME_BREAKOUT"
    case weakToStrong = "WEAK_TO_STRONG"
    case corePosition = "CORE_POSITION"
    case newsCatalyst = "NEWS_CATALYST"
    case pureTapeReading = "PURE_TAPE_READING"

    var id: String { rawValue }
    var label: String { nkNoteLabelText(rawValue) }
}

// MARK: - v1.2-B 预注册决策日志(§五 v1.2-E.1;审计件、非下单件——本文件任何类型
// 都只是展示/编解码模型,不含任何触发下单的逻辑)。

/// ⑦ 应对方案·情景树单项。`Codable` 双向复用:解码 `DecisionOut.contingencyScenarios`
/// 时用,构造 `POST /decisions`·`revise` 请求体时也用(服务端 `ContingencyScenarioIn`/
/// `ContingencyScenarioOut` 形状一致,不必两份类型)。
struct ContingencyScenario: Codable, Equatable {
    var scenario: String
    var trigger: String
    var action: String        // BUY/HOLD/REDUCE/ABANDON,服务端码
    var matched: Bool = false

    var actionLabel: String { nkScenarioActionLabel(action) }
}

/// 对齐 `DecisionOut`(逐字段,见「v1.2 客户端契约清单」)。字段名与服务端 JSON
/// 完全一致,直接 `Codable` 解码,不需要私有 wire DTO 中转(同 `Position`/
/// `BoardEvent`/`Position` 的直接解码先例)。
struct DecisionLog: Codable, Equatable, Identifiable {
    var id: Int
    var code: String
    var name: String
    var createdAt: String
    var whyBuy: String
    var whyEntryPrice: String
    var targetPrice: Double?
    var exitLow: Double?
    var exitHigh: Double?
    var thesisTags: [String]
    var invalidation: String
    var contingencyScenarios: [ContingencyScenario]
    var playbookTag: String
    var plannedPrice: Double?
    var plannedQty: Int?
    var status: String                // pending | filled | cancelled | expired
    var positionId: Int?
    var revisionOf: Int?
    /// ⑨ 最高追价上限(v1.4-⑤-B,需求 2 补充)。相对昨收百分比,如 `3.0`=+3%(**不是
    /// 小数 0.03**);允许负值(只在低开时买);`nil` = 显式选择"不设上限",**或**老行
    /// (建于本字段前)——两者在存储层无法区分,是迁移引入新必填字段时不可避免的历史
    /// 模糊,不影响新行起的强制语义。与 `plannedPrice`("我打算挂多少价")是两回事,
    /// 不要合并展示:本字段回答的是"开盘冲多高我就放弃、盘中不追补"。
    var maxChasePct: Double? = nil

    var thesisTagLabels: [String] { thesisTags.map(nkThesisTagLabel) }
    var playbookTagLabel: String { nkPlaybookTagLabel(playbookTag) }
    /// 三仓 = 2 短线追击 + 1 呼吸底仓试验(§2.1 第 3 条)——呼吸台账入口露出规则
    /// (§五 v1.2-E.4)据此判断,不新存第二份「是否呼吸仓」标记。
    var isBreathingTrial: Bool { playbookTag == PlaybookTag.breathingTrial.rawValue }
}

// MARK: - v1.4-⑦-A 挂单未成交追踪(§五 v1.4-⑦-A,§七 P3-12)。领域数据自 v1.3-④ 起已在攒
// (`report/pending_track.py`),本节把 `GET /decisions/{id}/track` 已有数据接上展示。

struct DecisionTrackRow: Codable, Equatable, Identifiable {
    var tradeDate: String
    var dOffset: Int
    var close: Double
    var retFromPlan: Double? = nil    // nil = 该决策未设 plannedPrice,不臆造

    var id: String { tradeDate }
}

/// `rows` 按 `tradeDate` 升序,**可能为空**——该决策尚未攒到任何追踪快照(刚创建、还没
/// 到下一交易日)不等于"没有这条决策"(那是 404),这是合法的 200 空态,UI 须展示
/// "暂未攒到数据"而非当作错误处理。
struct DecisionTrack: Codable, Equatable {
    var status: String
    var planPrice: Double? = nil
    var rows: [DecisionTrackRow] = []
}

// ⚠ **`CircuitEpisode` / `CircuitState` 两个 DTO 已于 v2.3.0 物理删除**(两步淘汰第二步)。
//
// 熔断三件机制在 V2.2-⑤-B 随用户裁定 #8 整体退役;当时按零删键铁律(〇b-3)让服务端
// `PositionsOut.circuit` 恒发空态过渡一版,客户端两个 DTO 也一并留着。本版服务端删键、
// 客户端删 DTO,**同一版落地**。
// 🔴 **删得掉的判据**:历代客户端 `/positions` 一律解进 `PositionsListResponse { holdings }`,
// **没有任何一版声明过 `circuit` 字段** —— 2.0.0 那台 iPhone 读的是**独立端点**
// `GET /circuit`(自 V2.2 起 404,与本键无关)。⛔ 别把这条当成「零删键铁律可以不守」的先例。
// ⛔ 更不许以任何名字把熔断状态位加回来(§五 〇b-7,用户裁定 #8:「我不需要你替我做决定」)。

// MARK: - v1.1-B.3/v1.2-E.5 一键补录预填(区间双档,替换 v1.1 的单 `qty`)
//
// `EntrySuggestionOut` 改区间:`qtyHigh`/`capCeil` = 现役 `single_cap` 违纪判定
// 上限对应手数/金额(**非推荐值**);`qtyLow`/`capFloor` = 半仓保守下沿。客户端只
// 展示两档供参考,不替用户拍单笔金额(§2.1 第 3 条三仓制「单笔金额不定死」)。

struct EntrySuggestionRange: Codable, Equatable {
    var code: String
    var price: Double
    var qtyLow: Int
    var qtyHigh: Int
    var capFloor: Double
    var capCeil: Double
    var stopLine: Double
    /// V2.3.2-⑤:同 `Position` —— 这条预计线的对外语义(`"review"` = 亏损警戒 + 离场
    /// 决策在你)。**nil = 现役章程未声明** → 展示层退回「预计止损线」老文案。
    var lossWarningAction: String? = nil

    /// 这条预计线的称呼(与 `Position.stopLineLabel` 同判据,⛔ 别写死成其一)。
    var stopLineLabel: String { lossWarningAction == "review" ? "亏损警戒线" : "止损线" }
}
