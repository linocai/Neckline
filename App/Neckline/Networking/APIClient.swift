//
//  APIClient.swift
//  Neckline — 后端 REST 客户端(FastAPI,§五 阶段4A → **V2.0.0 契约换血,V2-⑮**)
//
//  端点契约见 `neckline/api/schemas.py` + `neckline/api/app.py`(逐字段对齐,不猜);
//  逐字段对照表见 `archive/对照表/V2_契约三方对拍_20260803.md`。
//
//    GET  /api/v1/health                        → 免鉴权,{status,version}
//    GET  /api/v1/report/latest · /report?date= → ReportOut(**V2:candidates 已删,
//                                                  换 basketDaily 三段**)
//    GET  /api/v1/report/{date}/info-card/{code}→ InfoCardOut(+ ⑬-N 篮子块 + K7 标注件)
//    GET  /api/v1/baskets?date=&tier=           → BasketsListOut
//    GET  /api/v1/baskets/{id}                  → BasketOut          · 404 basket_not_found
//    GET  /api/v1/baskets/{id}/card?version=    → BasketCardOut      · 404 basket_not_found /
//                                                                       card_not_ready
//    GET  /api/v1/baskets/{id}/verification?date=→ BasketVerificationOut · 404 basket_not_found
//    GET  /api/v1/baskets/{id}/review?date=     → BasketReviewOut    · 404 basket_not_found /
//                                                                       not_found
//    GET  /api/v1/board                         → BoardOut
//    GET  /api/v1/positions                     → PositionsOut{holdings}
//    GET  /api/v1/positions/entry-suggestion    → EntrySuggestionOut
//    POST /api/v1/positions                     → PositionOpenOut(**幂等键**)· 400 字段
//    POST /api/v1/positions/{id}/close          → {ok}                · 404 not_holding
//    GET  /api/v1/positions/{id}/plans          → PositionPlansOut
//    POST /api/v1/positions/{id}/plans          → PositionPlanOut(201)· 400 no_base_plan
//    GET  /api/v1/positions/{id}/entry-snapshot → EntrySnapshotOut    · 404 not_found
//    ⚠ `GET /circuit` · `POST /circuit/unlock` **已随熔断整体退役删除**(V2.2-⑤-B,
//      裁定 #8);客户端两条活调用同批删。⛔ 不许接回来。
//    POST /api/v1/decisions                     → DecisionNoteOut(**用户可选补充**入口)
//    GET  /api/v1/decisions                     → {items:[DecisionOut]}(只读归因)
//    GET  /api/v1/decisions/{id}/track          → DecisionTrackOut    · 404 not_found
//    GET|POST|PUT|DELETE /api/v1/alerts[/{id}]  → AlertsListOut / CustomAlertOut
//    POST /api/v1/alerts/parse                  → AlertParseOut(**恒 200**)
//    GET  /api/v1/settings                      → SettingsOut(**providers[] + routes + kinds[]**)
//    GET|POST|PUT|DELETE /api/v1/settings/providers[/{name}]
//    GET|PUT  /api/v1/settings/llm-routes       → LLMRoutesOut
//    PUT  /api/v1/settings/push                 → {ok}(**kinds 全量覆盖**)· 422 invalid_push_kinds
//    PUT  /api/v1/settings/review-col-map       → {ok}
//    POST /api/v1/devices                       → {ok}
//    GET  /api/v1/profile/preference · /capability → ProfileOut
//    GET  /api/v1/packs · /packs/{version}      → PacksListOut / PackOut · 404 not_found
//    GET  /api/v1/eval/weekly?week=             → EvalWeeklyOut(**恒 200**)
//    POST /api/v1/review/upload · GET /review   → 复盘板块 · 对账页(macOS 上传)
//    GET  /api/v1/review/overview?week=&asOf=   → ReviewOverviewOut(**恒 200**,五段各自 available)
//    GET  /api/v1/review/handoff?from=&to=      → ReviewHandoffOut(**恒 200**,校准移交件 markdown)
//    GET  /api/v1/market-regime?date=           → MarketRegimeOut(**恒 200**,V2.2-②)
//    GET  /api/v1/clocks/selection?from=&to=    → SelectionClocksOut(**恒 200**,V2.2-④-A)
//    GET  /api/v1/clocks/trade/{position_id}    → TradeClockOut     · 404 not_found
//    POST /api/v1/clocks/trade/{id}/note        → TradeClockNoteOut · 404 not_found · 422 超长/空
//                                                 (**本版唯一新增写端点**)
//  鉴权:Authorization: Bearer <API_TOKEN>(health 外全部)。
//
//  ⚠ **V2-⑮ 删掉的五处「打向已删端点」的活调用**(⑭-C 对拍表 §六 B1/B2):
//    `PUT /settings/llm`(请求体含**明文 apiKey**,发到一个不存在的端点、界面上还是
//    一副成功的样子 = 假成功面 + 明文密钥打进空洞)+ `POST /decisions/{id}/`
//    `link|cancel|revise|scenario-outcome` 四处(服务端 ⑩-C 已删写端点)。
//    机器判据见 `tests/test_contract_crosscheck.py`(客户端调用面 ⊆ 服务端路由面)。
//

import Foundation

// MARK: - 错误类型(结构化 reason,UI 据此弹提示)

enum APIError: Error, LocalizedError, Equatable {
    case unauthorized
    case notHolding          // 404 该持仓已清或不存在(POST /positions/{id}/close)
    // 404 通用「未找到」(reason="not_found":决策追踪 / provider / alert /
    // 篮子复盘 / 建仓快照 / 策略包)。与 `notHolding` 分开是因为两者文案不同。
    case notFound
    case notTradingDay       // 400 buyDate 不是交易日
    case futureBuyDate       // 400 buyDate 晚于今天
    case reportNotFound      // 404 该日期未生成过报告
    case codeNotInReport     // 404 该票不在当日报告里
    // —— V2-⑭-B 三个全新 reason(⑮ 必接;⛔ 不吃 fallback)——————————————————
    // 404 的 fallback 是 `.notHolding`「持仓已清」——`cardNotReady` 若不建 case,
    // 用户点开一个卡还没生成的篮子会看到「持仓已清」(v1.4 `watchlist` 有案底)。
    case basketNotFound      // 404 找不到这个篮子
    case cardNotReady        // 404 篮子在、**卡还没生成**(⛔ 不是「篮子不存在」)
    // **500** 有卡行但**读不出**(json 坏 / 顶层必需键缺失)。⛔ **不是 404、不是
    // `cardNotReady`**:卡是冻结件、服务端 `INSERT OR IGNORE` 永不覆盖 → **坏了就是
    // 永久坏的**,当成「还没生成」处理就会永远重试、界面永远显示「卡还没生成」而那张卡
    // 这辈子不会来 = 静默永久失败。⛔ **不进任何静默重试路径**(重试永远不会好)。
    case cardCorrupt
    case noBasePlan          // 400 这笔仓没有可继承的计划基线
    // —— V2.3.3-⑤ D1 集合竞价确认层的两个全新 reason(⛔ 不吃 fallback)——————————
    // 404 当日 `auction_reports` **无行** = 竞价层没跑过 / 还没到 9:26。
    // ⛔ 不建 case 的话 404 fallback 会显示「持仓已清」(v1.4 `watchlist` 有案底)。
    case auctionNotReady
    // **500** 有行但**读不出**。⛔ **与上一条必须分开**:混成一类 = 客户端永远重试、
    // 永远显示"还没生成",而那份报告是冻结件、坏了不会自己好 = 静默永久失败。
    case auctionCorrupt
    // —— 409 / 422 的四个 reason(② / ⑪ 端点接线带来的)——————————————————————
    case alreadyExists       // 409 POST /settings/providers 同名 provider
    case duplicateAlert      // 409 POST /alerts 同标的 + 规则逐字节相同
    case invalidRule         // 422 提醒规则不合白名单
    case invalidTask         // 422 PUT /settings/llm-routes 未知任务名
    case invalidProvider     // 422 默认/任务路由指向不可用 Provider
    case invalidTavilyKey    // 422 Tavily key 为空白
    case invalidPushKinds    // 422 PUT /settings/push 缺键 / 未登记 kind
    case validation(String)  // 422 其它字段校验
    case server(Int, String)
    case transport(String)
    case noToken

    var errorDescription: String? {
        switch self {
        case .unauthorized:     return "鉴权失败(检查 API Token)"
        case .notHolding:       return "该持仓已清或不存在"
        case .notFound:         return "未找到该记录(可能已被删除)"
        case .notTradingDay:    return "买入日不是交易日,请选择实际成交的交易日"
        case .futureBuyDate:    return "买入日不能晚于今天"
        case .reportNotFound:   return "该交易日尚无报告(日期不合法或当天报告尚未生成)"
        case .codeNotInReport:  return "这只票不在当日报告里"
        case .basketNotFound:   return "找不到这个篮子"
        case .cardNotReady:     return "本篮的卡还没生成"
        case .cardCorrupt:      return "这张卡的数据损坏了,需要排查"
        case .noBasePlan:       return "这笔仓没有可继承的计划基线"
        case .auctionNotReady:  return "今天的竞价报告还没生成"
        case .auctionCorrupt:   return "竞价报告数据损坏,需要排查"
        case .alreadyExists:    return "同名 Provider 已存在(请改用「编辑」)"
        case .duplicateAlert:   return "已有一条一模一样的提醒(未重复创建)"
        case .invalidRule:      return "提醒规则不在支持的条件范围内"
        case .invalidTask:      return "路由表里有未知的任务名"
        case .invalidProvider:  return "只能选择已启用且 key 已配置的 Provider"
        case .invalidTavilyKey: return "Tavily API key 不能为空"
        case .invalidPushKinds: return "推送开关清单不完整或含未登记的通知类型"
        case .validation(let m): return "字段校验失败:\(m)"
        case .server(let c, let m): return "服务端错误 \(c):\(m)"
        case .transport(let m): return "网络错误:\(m)"
        case .noToken:          return "未配置 API Token · 去设置填入"
        }
    }
}

// MARK: - 请求/响应载荷(私有 DTO,严格对齐后端字面字段名;公开侧用 Models.swift 展示模型)

private struct HealthResponse: Decodable { let status: String; let version: String? }

private struct ReportResponse: Decodable {
    let tradeDate: String
    let generatedAt: String
    let strategyVersion: String
    let sentiment: SentimentSnapshot?
    let sectors: [SectorSnapshot]
    /// V2-⑭-B:取代已删的 `candidates`。**服务端恒是对象**(老报告读回一份三段全标
    /// `available=false` 的诚实占位),故 `decodeIfPresent` + 默认值兜底。
    let basketDaily: BasketDaily?
    let degraded: Bool
    let reason: String
    let missedEntryHint: String?
    let intel: IntelSection?
    let sectorMoneyflow: SectorMoneyflowSection?
    let newsAlerts: [NewsAlert]?
    let newsAlertsScan: [NewsAlertScanStatus]?
    let dataFreshness: DataFreshness?

    enum CodingKeys: String, CodingKey {
        case tradeDate, generatedAt, strategyVersion, sentiment, sectors, basketDaily
        case degraded, reason, missedEntryHint, intel, sectorMoneyflow, newsAlerts, newsAlertsScan
        case dataFreshness
    }

    /// 自定义解码:`sentiment`/`intel`/`sectorMoneyflow`/`dataFreshness` 服务端**恒是对象**
    /// (旧报告 / 降级态是空对象 `{}`,不是缺键或 null)——空对象缺我方强类型要求的字段,
    /// 标准合成解码会直接抛错,这里用 `try?` 把"形状对不上"也当"没有"处理,归一成 `nil`
    /// (「没有 vs 没看」由 nil 表达"这份报告没有该节数据",UI 据此展示诚实空态而非崩溃)。
    ///
    /// **⑮ 小审 🔵 B-1(2026-08-04 A9-④ 拉平)**:`sectors` 数组 + 五个标量原本还是硬解码
    /// (`try c.decode`)。现役契约恒发这些键、当时不构成活险,但它们与已修的三处**同源
    /// 同病**:服务端哪天动了 `sectors` 的形状 = **整份报告解不出**,今日计划整页空白,
    /// 而真正坏的只是一个板块字段。现全部改成"取不到就退到诚实空态"——单个字段降级,
    /// ⛔ 不再让一个字段掀翻整份报告。
    /// ⚠ **`degraded` 缺键时取 `true` 而不是 `false`**:这个位的含义是「这份报告完不完整」,
    /// 缺了它就是**不知道**,而 `false` 是在替服务端保证"一切正常"——那正是拿"没看"当
    /// "没有"。宁可多显示一次降级提示,不可静默把降级报告当完整报告展示。
    ///
    /// **2026-08-05 契约类型核对补漏**:`sentiment` 此前是本组四个"服务端 `Dict[str,Any]`
    /// 透传 + 客户端强类型 struct"字段(`sentiment`/`intel`/`sectorMoneyflow`/
    /// `dataFreshness`)里唯一一个漏加 `try?` 的——`app.py::_empty_report()`(`GET
    /// /report/latest` 当日无报告 / `GET /report?date=` 查无该日报告,**两条主路径**)
    /// 实测真发 `"sentiment": {}`,`SentimentSnapshot` 九个非可选字段缺键必抛
    /// `keyNotFound`,且此前唯独这一行没接 `try?` → 整份 `ReportResponse` 解码被拖炸,
    /// 与当晚 `engineApiVersion` 同一种炸法(§4.2/§4.3 型别核对未覆盖到的口子,已补登
    /// `archive/对照表/V2_契约三方对拍_20260803.md` §七;新对照表见
    /// `archive/对照表/V2_契约类型核对_20260805.md`)。真实响应回归 fixture 见
    /// `DTODecodeTests.swift::testDecodeEmptyReportRealShapeSentimentIsEmptyObjectNotNull`。
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradeDate = (try? c.decode(String.self, forKey: .tradeDate)) ?? ""
        generatedAt = (try? c.decode(String.self, forKey: .generatedAt)) ?? ""
        strategyVersion = (try? c.decode(String.self, forKey: .strategyVersion)) ?? ""
        sentiment = try? c.decodeIfPresent(SentimentSnapshot.self, forKey: .sentiment)
        sectors = (try? c.decode([SectorSnapshot].self, forKey: .sectors)) ?? []
        basketDaily = try c.decodeIfPresent(BasketDaily.self, forKey: .basketDaily)
        let degradedRaw = try? c.decode(Bool.self, forKey: .degraded)
        degraded = degradedRaw ?? true
        reason = (try? c.decode(String.self, forKey: .reason))
            ?? (degradedRaw == nil ? "服务端响应缺 degraded 字段,无法确认这份报告是否完整" : "")
        missedEntryHint = try c.decodeIfPresent(String.self, forKey: .missedEntryHint)
        intel = try? c.decodeIfPresent(IntelSection.self, forKey: .intel)
        sectorMoneyflow = try? c.decodeIfPresent(SectorMoneyflowSection.self, forKey: .sectorMoneyflow)
        newsAlerts = try c.decodeIfPresent([NewsAlert].self, forKey: .newsAlerts)
        newsAlertsScan = try c.decodeIfPresent([NewsAlertScanStatus].self, forKey: .newsAlertsScan)
        dataFreshness = try? c.decodeIfPresent(DataFreshness.self, forKey: .dataFreshness)
    }
}

private struct BoardResponse: Decodable {
    let tradeDate: String
    let asof: String
    let retreatBrake: RetreatBrake
    let events: [BoardEvent]
}

private struct PositionsListResponse: Decodable {
    let holdings: [Position]
    /// 🔴 组合环境提醒(2026-08-12 用户裁定 ②)。**缺键 = 老服务端**(2.4.0 之前),
    /// 按空数组处理 —— ⛔ 空数组不等于「环境正常」,只是「今天没有这两类事件」。
    let portfolioAlerts: [PortfolioAlert]?
}

/// `/positions` 的完整快照:持仓 + 组合环境提醒。
/// ⚠ 两段**刻意分开**:板块级 / 市场级事件匹配不到任何一笔持仓码(裁定 ② 原文
/// 「⛔ 也不重复塞进单票详情」)。
struct PositionsSnapshot {
    var holdings: [Position]
    var portfolioAlerts: [PortfolioAlert]
}

struct OpenPositionRequest: Encodable {
    let code: String
    let name: String?
    let buy_price: Double
    let qty: Int
    let entry_reason: String
    let buyFees: Double?
    let buyDate: String?
    /// v2.0.0(契约线审计 🟡 Y7)**幂等键**。⚠ **每笔新提交动作一个新键(UUID),⛔ 严禁
    /// 复用、⛔ 别绑「票 + 日期」之类业务量**(那必然复用)——服务端是标准幂等语义:
    /// **同键 = 同一笔意图的重试**,同键不同 payload 会**静默重放原仓、把用户改过的
    /// 价格数量整个吃掉**。生成点见 `AppModel.beginPositionEntryFlow`(每次打开录入
    /// 表单铸一枚新键,提交失败重试复用同一枚)。
    let idempotencyKey: String?
}

/// `POST /positions` 响应(v2.0.0 起带 ⑩-A/B 的自动关联结果 + 幂等重放位)。
private struct OpenPositionResponse: Decodable {
    let ok: Bool
    let position_id: Int
    let stop_line: Double
    let sourceBasketKey: String?
    let sourceBasketName: String?
    let tier: Int?
    let role: String?
    let planAvailable: Bool?
    /// 「原盈亏结构已变」偏离提示(纯展示,不质问不阻断);**无从比较 → null,不是"未偏离"**。
    let planDeviationNotice: String?
    /// `true` = 本次**没有开新仓**,`position_id` 指的是同一个幂等键之前已经开好的那笔。
    /// 如实透出,别让"看起来成功了"掩盖"其实什么都没发生"。
    let replayed: Bool?
}

/// 开仓结果(展示层)。
struct OpenPositionResult: Equatable {
    var positionId: Int
    var stopLine: Double
    var sourceBasketKey: String? = nil
    var sourceBasketName: String? = nil
    var tier: Int? = nil
    var role: String? = nil
    var planAvailable: Bool = false
    var planDeviationNotice: String? = nil
    var replayed: Bool = false
}

struct ClosePositionRequest: Encodable {
    let sell_price: Double
    let sell_time: String?   // 'YYYYMMDD';缺省服务端用今日
    // ⚠ 契约字段名 `closeReason` 是 camelCase,与本结构体既有 `sell_price`/`sell_time`
    // 的 snake_case 并存——后端契约如此,不自作主张统一大小写。
    let closeReason: String?
    let sellFees: Double?
}

// —— 设置(V2-② Provider 自填制 + V2-⑪ 按 kind 的推送开关)——————————————————

/// `PUT /settings/push`:**全量覆盖式**写。`kinds` 必须给全服务端 `ALL_KINDS` 的每一个键
/// (缺键 / 未登记 kind → 422 `invalid_push_kinds`),承 V1「六字段均必填,防漏传静默
/// 重置某开关」的同一条纪律 —— 静默忽略会让用户以为自己关掉了某类通知而服务端根本没收到。
struct SettingsPushRequest: Encodable { let kinds: [String: Bool] }

struct SettingsReviewColMapRequest: Encodable { let colMap: [String: String] }

/// 新建 Provider。`apiKey` **只发一次、不回显、不落日志**(§3.4 高危区);
/// `name` 已存在 → 409 `already_exists`(须显式走 PUT 更新,防误覆盖)。
struct ProviderCreateRequest: Encodable {
    let name: String
    let baseUrl: String
    let model: String
    let apiKey: String?
    let hasWebSearch: Bool
    let searchEngine: String?
    let notes: String?
    let enabled: Bool
}

/// 局部更新:**未出现的字段不改**(服务端 `model_fields_set` 判据)。故这里一律
/// `Optional` + 合成 `Encodable`(nil → 该键不出现),**刻意**不手写 `encode(to:)`
/// ——与 `maxChasePct` 那种「键必须永远出现」的字段是相反的需求,别套错模板。
struct ProviderUpdateRequest: Encodable {
    let baseUrl: String?
    let model: String?
    let apiKey: String?
    let hasWebSearch: Bool?
    let searchEngine: String?
    let notes: String?
    let enabled: Bool?
}

/// 路由表:**全量覆盖式**写(同 `SettingsPushRequest` 风格,调用方须传完整状态)。
struct LLMRoutesRequest: Encodable {
    let routes: [String: String]
    let defaultProvider: String?
}

/// Tavily key 只写不回显；响应只含 `keySet`。
struct TavilySettingsRequest: Encodable { let apiKey: String }

// —— 设备注册 + 通用 ok 响应 ————————————————————————————————————————————————

struct DeviceRegisterRequest: Encodable { let token: String; let platform: String }

private struct OkResponse: Decodable { let ok: Bool }

// —— v1.2-E.5 一键补录预填推荐(区间双档)——————————————————————————————

private struct EntrySuggestionResponse: Decodable {
    let ok: Bool; let code: String; let price: Double
    let qtyLow: Int; let qtyHigh: Int; let capFloor: Double; let capCeil: Double; let stopLine: Double
    /// V2.3.2-⑤:这条预计线的对外语义;老服务端不发 → nil = 未声明(合成 `Decodable`
    /// 对 `Optional` 属性天然容忍缺键,这里不必手写 `init(from:)`)。
    let lossWarningAction: String?
}

// ⚠ 「无请求体 POST 占位」`EmptyBody` 已删:它的唯一使用者是 `POST /circuit/unlock`,
// 该端点随熔断整体退役消失(V2.2-⑤-B)。⛔ 别为了"以后可能用得上"留一个死类型。

// —— ⑩-C「用户可选补充」入口(`POST /decisions` 语义换血:不再是九项强制表单)————
//
// **全部字段可选**——`code` 缺省也合法(该次提交完全没有可落的内容时,端点 200 空提交,
// 不 400)。落 `user_actions`(`kind='label'`/`'voice_note'`),**不碰 `decision_log`**
// (该表 v2.0.0 起停写留档)。
struct DecisionNoteRequest: Encodable {
    let code: String?
    let positionId: Int?
    let labels: [String]
    let voiceNote: String?
}

/// `POST /decisions` 响应:**如实回显本次记了哪些 kind**(`[]` = 空提交,合法、不是错误)。
struct DecisionNoteResult: Decodable, Equatable {
    var ok: Bool = true
    var recorded: [String] = []
}

private struct DecisionsListResponse: Decodable { let items: [DecisionLog] }

// —— V2-⑭-B 篮子族 / 计划 / 画像 / 包 / 评价 / 提醒 的列表包装 ————————————————

private struct BasketsListResponse: Decodable { let tradeDate: String; let items: [Basket] }
/// V2.2-④-A `GET /clocks/selection` 的列表包装(**空列表是合法态**,见方法 doc)。
private struct SelectionClocksResponse: Decodable {
    let dateFrom: String?
    let dateTo: String?
    let items: [SelectionClock]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        dateFrom = try c.decodeIfPresent(String.self, forKey: .dateFrom)
        dateTo = try c.decodeIfPresent(String.self, forKey: .dateTo)
        items = try c.decodeIfPresent([SelectionClock].self, forKey: .items) ?? []
    }
    enum CodingKeys: String, CodingKey { case dateFrom, dateTo, items }
}
/// `POST /clocks/trade/{id}/note` 请求体。**唯一字段**(K8 §十五「每次一条简短说明」);
/// ⛔ 不加 kind / 不加时间戳 —— 那两样服务端自己定,客户端多发一份就是第二个事实源。
private struct TradeClockNoteRequest: Encodable { let note: String }
private struct PositionPlansResponse: Decodable { let items: [PositionPlan] }
private struct PacksListResponse: Decodable { let items: [Pack] }
private struct AlertsListResponse: Decodable { let items: [CustomAlert] }
private struct ProvidersListResponse: Decodable { let items: [Provider] }

/// `POST /alerts` 请求体 = `AlertDraft` 原样回传(⑪-C:LLM 解析只是把字段先替用户填好,
/// **落库路径只有一条**)。
private struct AlertParseRequest: Encodable { let text: String; let tsCode: String? }

/// `PUT /alerts/{id}`:局部更新(未出现的字段不改)。
struct AlertUpdateRequest: Encodable {
    let conditions: [AlertCondition]?
    let logic: String?
    let nlText: String?
    let activeFrom: String?
    let activeTo: String?
    let expiresAt: String?
    let persist: Bool?
    let cooldownSeconds: Int?
    let maxFires: Int?
    let resetFired: Bool
}

/// `POST /positions/{id}/plans`:计划新版本。
/// ⚠ **武装态由服务端重算,请求体说了不算**(⑪-D-B 闸②)——即使这里带了
/// `exit_reference_armed`,服务端也会拿这笔仓的真实成交价重过一遍闸。
struct PositionPlanCreateRequest: Encodable {
    let plan: NKJSON
    let note: String?
}

// MARK: - APIClient

actor APIClient {
    private let baseURL: URL
    private let token: String
    private let session: URLSession

    init(baseURL: URL, token: String, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.token = token
        self.session = session
    }

    // —— health(免鉴权,联通性自检 + 服务端版本诚实展示)——
    func health() async throws -> (ok: Bool, version: String?) {
        guard let url = Self.makeURL(base: baseURL, path: "/api/v1/health") else {
            throw APIError.transport("无效 URL")
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else { return (false, nil) }
        let obj = try? JSONDecoder().decode(HealthResponse.self, from: data)
        return (obj?.status == "ok", obj?.version)
    }

    // —— 4A.2 报告 ——
    func fetchReportLatest() async throws -> ReportSnapshot {
        let data = try await get("/api/v1/report/latest")
        return try Self.decodeReport(data)
    }

    /// 历史回放(带 query,务必走 makeURL 免 "?" 编码坑,§五 阶段4C 坑②)。
    func fetchReport(date: String) async throws -> ReportSnapshot {
        let data = try await get("/api/v1/report?date=\(date)")
        return try Self.decodeReport(data)
    }

    private static func decodeReport(_ data: Data) throws -> ReportSnapshot {
        let r = try JSONDecoder().decode(ReportResponse.self, from: data)
        return ReportSnapshot(tradeDate: r.tradeDate, generatedAt: r.generatedAt,
                              strategyVersion: r.strategyVersion, sentiment: r.sentiment,
                              sectors: r.sectors, basketDaily: r.basketDaily ?? BasketDaily(),
                              degraded: r.degraded, reason: r.reason,
                              missedEntryHint: r.missedEntryHint ?? "",
                              intel: r.intel, sectorMoneyflow: r.sectorMoneyflow,
                              newsAlerts: r.newsAlerts ?? [], newsAlertsScan: r.newsAlertsScan ?? [],
                              dataFreshness: r.dataFreshness)
    }

    /// 单只完整信息卡(60 日 K 线/RS 线/行业分歧线 + 快照 + 红黄牌 + 温和带 + 消息面 +
    /// 龙虎榜 + 市场语境 + **⑬-N 篮子块与 K7 标注件**)。`code` 支持裸 6 位或带交易所后缀。
    /// **`timeout: 60`**(§七 P1-26):本端点是全仓最重的读,默认 12s 在生产 2 vCPU 箱上
    /// 必然超时,且失败会诱发用户反复重试、把常驻服务顶到内存节流线。
    func fetchInfoCard(date: String, code: String) async throws -> InfoCard {
        let data = try await get("/api/v1/report/\(date)/info-card/\(code)", timeout: 60)
        return try JSONDecoder().decode(InfoCard.self, from: data)
    }

    // —— V2-⑭-B 篮子族 ————————————————————————————————————————————————

    /// 某交易日的篮子清单(T1/T2/T3,按 tier 升序、basket_key 升序,**确定性**)。
    /// `date` 缺省 = 最近一份报告的交易日;`tier`(1/2/3)可选过滤。
    /// **空列表是合法输出**(「今日无篮子达到定档标准」),⛔ 不是 404。
    func fetchBaskets(date: String? = nil, tier: Int? = nil) async throws -> [Basket] {
        var query: [String] = []
        if let d = date, !d.isEmpty { query.append("date=\(d)") }
        if let t = tier, (1...3).contains(t) { query.append("tier=\(t)") }
        let path = query.isEmpty ? "/api/v1/baskets" : "/api/v1/baskets?" + query.joined(separator: "&")
        let data = try await get(path)
        return try JSONDecoder().decode(BasketsListResponse.self, from: data).items
    }

    /// 单个篮子(含冻结卡与 Tier 留痕)。不存在 → 404 `basket_not_found`。
    /// ⚠ **篮子在、卡没生成不是 404**:照返 200,`card == nil` +
    /// `cardUnavailableReason == "card_not_ready"`(合法中间态)。
    func fetchBasket(id: Int) async throws -> Basket {
        let data = try await get("/api/v1/baskets/\(id)")
        return try JSONDecoder().decode(Basket.self, from: data)
    }

    /// 一张冻结的篮子卡。`version` 缺省 = 最新版本。
    /// **404 两个 reason 语义相反**:`basket_not_found`(篮子本身不存在)/
    /// `card_not_ready`(**篮子在、卡还没生成**),各有独立 `APIError` case。
    func fetchBasketCard(id: Int, version: Int? = nil) async throws -> BasketCard {
        let path = version.map { "/api/v1/baskets/\(id)/card?version=\($0)" }
            ?? "/api/v1/baskets/\(id)/card"
        let data = try await get(path)
        return try JSONDecoder().decode(BasketCard.self, from: data)
    }

    /// 某篮某日的验证状态(⑧ 三路读法,只读不判)。篮子不存在 → 404 `basket_not_found`;
    /// **篮子在、今天没判过**照返 200 + `notEvaluated == true`(⛔ 不是 404)。
    func fetchBasketVerification(id: Int, date: String? = nil) async throws -> BasketVerification {
        let path = (date?.isEmpty == false) ? "/api/v1/baskets/\(id)/verification?date=\(date!)"
                                            : "/api/v1/baskets/\(id)/verification"
        let data = try await get(path)
        return try JSONDecoder().decode(BasketVerification.self, from: data)
    }

    /// 某篮某个复盘日(D+1)的盘后复盘。篮子不存在 → 404 `basket_not_found`;
    /// 篮子在、那天还没复盘 → 404 `not_found`(**复用既有 reason,无需新 case**)。
    func fetchBasketReview(id: Int, date: String? = nil) async throws -> BasketReview {
        let path = (date?.isEmpty == false) ? "/api/v1/baskets/\(id)/review?date=\(date!)"
                                            : "/api/v1/baskets/\(id)/review"
        let data = try await get(path)
        return try JSONDecoder().decode(BasketReview.self, from: data)
    }

    // —— 4A.3 盘中看板 ——
    func fetchBoard() async throws -> BoardSnapshot {
        let data = try await get("/api/v1/board")
        let r = try JSONDecoder().decode(BoardResponse.self, from: data)
        return BoardSnapshot(tradeDate: r.tradeDate, asof: r.asof,
                             retreatBrake: r.retreatBrake, events: r.events)
    }

    // —— 4A.4 持仓(审计台账;系统永不自动下单,§3.8)——
    func fetchPositions() async throws -> PositionsSnapshot {
        let data = try await get("/api/v1/positions")
        let r = try JSONDecoder().decode(PositionsListResponse.self, from: data)
        return PositionsSnapshot(holdings: r.holdings, portfolioAlerts: r.portfolioAlerts ?? [])
    }

    /// 开仓录入(补录用户已在券商完成的真实操作)。
    ///
    /// `idempotencyKey`(v2.0.0):**这一次提交动作**的键。⛔ 严禁跨提交复用 —— 同键不同
    /// payload 会被服务端当重试**静默重放原仓**。生成规则见 `OpenPositionRequest`。
    /// `buyDate`:**不传 → 服务端取今天**;非交易日 / 未来日 → 400 + reason。
    func openPosition(code: String, name: String?, buyPrice: Double, qty: Int,
                      entryReason: String, buyFees: Double? = nil,
                      buyDate: String? = nil,
                      idempotencyKey: String? = nil) async throws -> OpenPositionResult {
        let body = OpenPositionRequest(code: code, name: name, buy_price: buyPrice,
                                       qty: qty, entry_reason: entryReason, buyFees: buyFees,
                                       buyDate: buyDate, idempotencyKey: idempotencyKey)
        let data = try await post("/api/v1/positions", body: body)
        let r = try JSONDecoder().decode(OpenPositionResponse.self, from: data)
        return OpenPositionResult(positionId: r.position_id, stopLine: r.stop_line,
                                  sourceBasketKey: r.sourceBasketKey,
                                  sourceBasketName: r.sourceBasketName,
                                  tier: r.tier, role: r.role,
                                  planAvailable: r.planAvailable ?? false,
                                  planDeviationNotice: r.planDeviationNotice,
                                  replayed: r.replayed ?? false)
    }

    /// 清仓录入。`sellTime` 缺省 → 服务端用今日。`closeReason` 可选(九码之一)。
    @discardableResult
    func closePosition(id: Int, sellPrice: Double, sellTime: String? = nil,
                       closeReason: String? = nil, sellFees: Double? = nil) async throws -> Bool {
        let body = ClosePositionRequest(sell_price: sellPrice, sell_time: sellTime,
                                        closeReason: closeReason, sellFees: sellFees)
        let data = try await post("/api/v1/positions/\(id)/close", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    /// 一键补录预填推荐(区间双档,只读计算,不写台账)。
    func entrySuggestion(code: String, price: Double) async throws -> EntrySuggestionRange {
        let priceStr = String(format: "%.2f", price)
        let data = try await get("/api/v1/positions/entry-suggestion?code=\(code)&price=\(priceStr)")
        let r = try JSONDecoder().decode(EntrySuggestionResponse.self, from: data)
        return EntrySuggestionRange(code: r.code, price: r.price, qtyLow: r.qtyLow, qtyHigh: r.qtyHigh,
                                    capFloor: r.capFloor, capCeil: r.capCeil, stopLine: r.stopLine,
                                    lossWarningAction: r.lossWarningAction)
    }

    // —— V2-⑩-B 计划继承 + 建仓快照 ————————————————————————————————————

    /// 某持仓的全部计划版本(升序,`version=1` 是从 D0 卡继承的原判)。
    /// **空列表 = 这笔仓不存在或建于 ⑩ 之前**,不 404。
    func fetchPositionPlans(positionId: Int) async throws -> [PositionPlan] {
        let data = try await get("/api/v1/positions/\(positionId)/plans")
        return try JSONDecoder().decode(PositionPlansResponse.self, from: data).items
    }

    /// 创建计划新版本(⑩-B)。**新版本不修改原始篮子卡**。
    /// 无既有计划(缺 `version=1`)→ 400 `no_base_plan`。
    @discardableResult
    func createPositionPlanVersion(positionId: Int, plan: NKJSON,
                                   note: String? = nil) async throws -> PositionPlan {
        let body = PositionPlanCreateRequest(plan: plan, note: note)
        let data = try await post("/api/v1/positions/\(positionId)/plans", body: body)
        return try JSONDecoder().decode(PositionPlan.self, from: data)
    }

    /// 建仓瞬间的冻结快照。无快照行 → 404 `not_found`(复用既有 case)。
    func fetchEntrySnapshot(positionId: Int) async throws -> EntrySnapshot {
        let data = try await get("/api/v1/positions/\(positionId)/entry-snapshot")
        return try JSONDecoder().decode(EntrySnapshot.self, from: data)
    }

    // —— V2.2-④-B 双时钟(选股时钟只读 / 交易时钟只读 + **本版唯一新增写端点**)——
    //
    // 🔴 **样本口径**:选股时钟覆盖 D0 **全部** T1/T2,**与用户买没买无关**(K8 §十四
    // 第 3 条)—— ⛔ 客户端文案不许写成「你关注的篮子」,那会把覆盖域讲小。
    // ⚠ 两条 `clocks/trade` 端点 404 复用既有 `not_found`(服务端 docstring 明写
    // 「⛔ 不新增 reason」)→ `mapReason` **一字不动**;调用方把 `.notFound` 当
    // **「这笔仓还没有交易时钟」的合法空态**处理,⛔ 别弹成网络错误。

    /// 已结案的选股时钟(按 **D0** 区间)。**空列表 = 这段时间没有结案样本**(合法),
    /// ⛔ 不是「系统没跑」。端点整段包保险丝,恒 200。
    func fetchSelectionClocks(from: String? = nil, to: String? = nil) async throws -> [SelectionClock] {
        var query: [String] = []
        if let f = from, !f.isEmpty { query.append("from=\(f)") }
        if let t = to, !t.isEmpty { query.append("to=\(t)") }
        let path = "/api/v1/clocks/selection" + (query.isEmpty ? "" : "?" + query.joined(separator: "&"))
        let data = try await get(path)
        return try JSONDecoder().decode(SelectionClocksResponse.self, from: data).items
    }

    /// 一笔真实买入的交易时钟 + 全部事件流水。这笔仓没有交易时钟 → 404 `not_found`
    /// (既有 case 覆盖,⛔ 不新增 reason)。
    func fetchTradeClock(positionId: Int) async throws -> TradeClock {
        let data = try await get("/api/v1/clocks/trade/\(positionId)")
        return try JSONDecoder().decode(TradeClock.self, from: data)
    }

    /// 追加一条**用户主观说明**(K8 §十五)。**纯追加**,⛔ 不改任何既有行、⛔ 系统
    /// 不代猜(§七 P3-28)。超长 / 空 → **422**(服务端 `TradeClockNoteIn.note` 的
    /// `Field` 约束,上界唯一源 = `review/trade_clock.USER_NOTE_MAX_CHARS`)。
    @discardableResult
    func postTradeClockNote(positionId: Int, note: String) async throws -> TradeClockNoteResult {
        let data = try await post("/api/v1/clocks/trade/\(positionId)/note",
                                  body: TradeClockNoteRequest(note: note))
        return try JSONDecoder().decode(TradeClockNoteResult.self, from: data)
    }

    // ⚠ **V2.2-⑤-B 熔断整体退役(用户裁定 #8)**:`getCircuit()` / `unlockCircuit()`
    // 两个方法已随服务端 `GET /circuit` · `POST /circuit/unlock` 两条端点一起删除 ——
    // 「锁定态 / 次日只减不加 / 强制复盘解锁」三件机制在产品面消失,留下的只有一条
    // 提醒推送与一条看板事件(`sentinel='circuit'`)。⛔ **不许以任何形式接回来**
    // (§五 〇b-7:不许留锁定标志、灰化按钮、或「建议今天别开仓」的自动状态位)。
    // ⚠ **`CircuitState` / `CircuitEpisode` 两个 DTO 刻意留在 `Models.swift`**:
    // 服务端 `PositionsOut.circuit` 本版仍恒发 `locked=false` 空态(〇b-3 零删键),
    // 删 DTO 与服务端删键同排 v2.3 —— ⛔ 本版不动它们。

    // —— ⑩-C 用户可选补充(七枚标签 + 一句可选说明)————————————————————————
    //
    // ⚠ **决策日志强制表单已退役**:`decision_log` 表 v2.0.0 起停写留档,
    // `link`/`cancel`/`revise`/`scenario-outcome` 四个写端点服务端已删,客户端对应四个
    // 方法随之删除(⑭-C 对拍表 §六 B2)。`GET /decisions`(只读归因)保留。

    /// 记一次「用户可选补充」。**全部参数可选**,空提交合法(200,不是 400)。
    @discardableResult
    func postDecisionNote(code: String?, positionId: Int? = nil,
                          labels: [String] = [], voiceNote: String? = nil) async throws -> DecisionNoteResult {
        let body = DecisionNoteRequest(code: code, positionId: positionId,
                                       labels: labels, voiceNote: voiceNote)
        let data = try await post("/api/v1/decisions", body: body)
        return try JSONDecoder().decode(DecisionNoteResult.self, from: data)
    }

    /// 历史决策日志(**只读归因**;v2.0.0 起零新增行,读的都是历史)。
    func listDecisions(status: String? = nil, code: String? = nil,
                       from: String? = nil, to: String? = nil) async throws -> [DecisionLog] {
        var query: [String] = []
        if let s = status, !s.isEmpty { query.append("status=\(s)") }
        if let c = code, !c.isEmpty { query.append("code=\(c)") }
        if let f = from, !f.isEmpty { query.append("from=\(f)") }
        if let t = to, !t.isEmpty { query.append("to=\(t)") }
        let path = query.isEmpty ? "/api/v1/decisions" : "/api/v1/decisions?" + query.joined(separator: "&")
        let data = try await get(path)
        return try JSONDecoder().decode(DecisionsListResponse.self, from: data).items
    }

    /// 挂单未成交追踪。`id` 不存在 → 404 `not_found`(既有 case 覆盖);决策存在但还没攒到
    /// 任何追踪快照 → 合法 200 空态 `rows=[]`,不是错误(两种「空」分开)。
    func decisionTrack(id: Int) async throws -> DecisionTrack {
        let data = try await get("/api/v1/decisions/\(id)/track")
        return try JSONDecoder().decode(DecisionTrack.self, from: data)
    }

    // ⚠ V2.1-① 起「问询台」(`sendInquiry`)+「问询历史」(`fetchInquiries`/
    // `fetchInquiryDetail`)三个方法已随问询台整链退役删除。

    // —— V2-⑪-C 自然语言临时提醒(**只通知,永不交易**)————————————————————

    /// `status` 过滤(⚠ 契约键是 `status`,不是 `status_filter` —— ⑭-B 已改名)。
    func fetchAlerts(status: String? = nil, tsCode: String? = nil) async throws -> [CustomAlert] {
        var query: [String] = []
        if let s = status, !s.isEmpty { query.append("status=\(s)") }
        if let c = tsCode, !c.isEmpty { query.append("tsCode=\(c)") }
        let path = query.isEmpty ? "/api/v1/alerts" : "/api/v1/alerts?" + query.joined(separator: "&")
        let data = try await get(path)
        return try JSONDecoder().decode(AlertsListResponse.self, from: data).items
    }

    /// 自然语言解析 → 确认卡。**永远 200**:LLM 不可用时 `degraded=true` + 手填表单,
    /// **不静默失败**。
    func parseAlert(text: String, tsCode: String? = nil) async throws -> AlertParseResult {
        let body = AlertParseRequest(text: text, tsCode: tsCode)
        // 走 LLM,给足长超时(同问询台 60s 惯例)。
        let data = try await post("/api/v1/alerts/parse", body: body, timeout: 60)
        return try JSONDecoder().decode(AlertParseResult.self, from: data)
    }

    /// 建一条提醒(**用户已在确认卡上确认之后**)。同标的 + 规则逐字节相同 → 409
    /// `duplicate_alert`;规则不合白名单 → 422 `invalid_rule`。
    func createAlert(_ draft: AlertDraft) async throws -> CustomAlert {
        let data = try await post("/api/v1/alerts", body: draft)
        return try JSONDecoder().decode(CustomAlert.self, from: data)
    }

    /// 局部更新(未出现的字段不改)。
    func updateAlert(id: Int, _ body: AlertUpdateRequest) async throws -> CustomAlert {
        let data = try await put("/api/v1/alerts/\(id)", body: body)
        return try JSONDecoder().decode(CustomAlert.self, from: data)
    }

    /// 停用 / 删除一条提醒。不存在 → 404 `not_found`。
    @discardableResult
    func deleteAlert(id: Int) async throws -> Bool {
        let data = try await delete("/api/v1/alerts/\(id)")
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    // —— 4A.5 设置(🔴 key 服务端存取,只写不回显)——

    func fetchSettings() async throws -> SettingsSnapshot {
        let data = try await get("/api/v1/settings")
        return try JSONDecoder().decode(SettingsSnapshot.self, from: data)
    }

    /// Provider 注册表(完整安全视图,**绝不含 key 明文**)。
    func fetchProviders() async throws -> [Provider] {
        let data = try await get("/api/v1/settings/providers")
        return try JSONDecoder().decode(ProvidersListResponse.self, from: data).items
    }

    /// 新建 Provider。同名 → 409 `already_exists`(须显式走 `updateProvider`,防误覆盖)。
    /// **`apiKey` 只发一次、不回显、不落日志**(§3.4 高危区)。
    func createProvider(_ body: ProviderCreateRequest) async throws -> Provider {
        let data = try await post("/api/v1/settings/providers", body: body)
        return try JSONDecoder().decode(Provider.self, from: data)
    }

    /// 局部更新(未出现的字段不改;`apiKey` 传空串 = **显式清空**)。
    func updateProvider(name: String, _ body: ProviderUpdateRequest) async throws -> Provider {
        let data = try await put("/api/v1/settings/providers/\(name)", body: body)
        return try JSONDecoder().decode(Provider.self, from: data)
    }

    @discardableResult
    func deleteProvider(name: String) async throws -> Bool {
        let data = try await delete("/api/v1/settings/providers/\(name)")
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    func fetchLLMRoutes() async throws -> LLMRoutes {
        let data = try await get("/api/v1/settings/llm-routes")
        return try JSONDecoder().decode(LLMRoutes.self, from: data)
    }

    /// **全量覆盖式**写路由表。未知任务名 → 422 `invalid_task`。
    @discardableResult
    func putLLMRoutes(routes: [String: String], defaultProvider: String?) async throws -> LLMRoutes {
        let body = LLMRoutesRequest(routes: routes, defaultProvider: defaultProvider)
        let data = try await put("/api/v1/settings/llm-routes", body: body)
        return try JSONDecoder().decode(LLMRoutes.self, from: data)
    }

    func putTavilyKey(_ apiKey: String) async throws -> TavilySettings {
        let data = try await put("/api/v1/settings/tavily", body: TavilySettingsRequest(apiKey: apiKey))
        return try JSONDecoder().decode(TavilySettings.self, from: data)
    }

    func deleteTavilyKey() async throws -> TavilySettings {
        let data = try await delete("/api/v1/settings/tavily")
        return try JSONDecoder().decode(TavilySettings.self, from: data)
    }

    /// 按 `kind` 的推送开关,**全量覆盖式**写。⛔ 客户端不许硬编 kind 清单 —— 传的是
    /// 从 `GET /settings` 拿回来的那一份(服务端 `notify_kinds.py` 是唯一源),
    /// 缺键 / 未登记 kind → 422 `invalid_push_kinds`。
    @discardableResult
    func putSettingsPush(kinds: [String: Bool]) async throws -> Bool {
        let body = SettingsPushRequest(kinds: kinds)
        let data = try await put("/api/v1/settings/push", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    /// 周复盘交割单列映射(支持两家券商原始格式)。
    @discardableResult
    func putSettingsReviewColMap(_ colMap: [String: String]) async throws -> Bool {
        let body = SettingsReviewColMapRequest(colMap: colMap)
        let data = try await put("/api/v1/settings/review-col-map", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    // —— 设备注册(iOS APNs token)——
    @discardableResult
    func registerDevice(token deviceToken: String, platform: String = "ios") async throws -> Bool {
        let body = DeviceRegisterRequest(token: deviceToken, platform: platform)
        let data = try await post("/api/v1/devices", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    // —— V2-⑫ 画像 / ③ 策略包 / ⑨-C 评价 ————————————————————————————————

    /// 偏好画像(答「喜欢什么」)。**与能力画像刻意分开,⛔ 不合并成一张"用户画像"**。
    func fetchPreferenceProfile(asOf: String? = nil) async throws -> Profile {
        let path = (asOf?.isEmpty == false) ? "/api/v1/profile/preference?asOf=\(asOf!)"
                                            : "/api/v1/profile/preference"
        let data = try await get(path)
        return try JSONDecoder().decode(Profile.self, from: data)
    }

    /// 能力画像(答「什么真有效」)。
    func fetchCapabilityProfile(asOf: String? = nil) async throws -> Profile {
        let path = (asOf?.isEmpty == false) ? "/api/v1/profile/capability?asOf=\(asOf!)"
                                            : "/api/v1/profile/capability"
        let data = try await get(path)
        return try JSONDecoder().decode(Profile.self, from: data)
    }

    func fetchPacks() async throws -> [Pack] {
        let data = try await get("/api/v1/packs")
        return try JSONDecoder().decode(PacksListResponse.self, from: data).items
    }

    /// 单个策略包。不存在 → 404 `not_found`。
    func fetchPack(version: String) async throws -> Pack {
        let data = try await get("/api/v1/packs/\(version)")
        return try JSONDecoder().decode(Pack.self, from: data)
    }

    /// 周度评价校准报告(**恒 200**,失败给可读原因)。
    func fetchEvalWeekly(week: String? = nil) async throws -> EvalWeekly {
        let path = (week?.isEmpty == false) ? "/api/v1/eval/weekly?week=\(week!)"
                                            : "/api/v1/eval/weekly"
        let data = try await get(path, timeout: 60)
        return try JSONDecoder().decode(EvalWeekly.self, from: data)
    }

    // —— 4D 周复盘工作台(拖入交割单对账;macOS 独有)——————————————————————

    func uploadReview(files: [(filename: String, data: Data)]) async throws -> ReviewUploadResponse {
        try ensureToken()
        guard let url = Self.makeURL(base: baseURL, path: "/api/v1/review/upload") else {
            throw APIError.transport("无效 URL")
        }
        let boundary = "NecklineBoundary-\(UUID().uuidString)"
        var body = Data()
        for (filename, fileData) in files {
            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append(
                "Content-Disposition: form-data; name=\"files\"; filename=\"\(filename)\"\r\n"
                    .data(using: .utf8)!
            )
            body.append("Content-Type: application/octet-stream\r\n\r\n".data(using: .utf8)!)
            body.append(fileData)
            body.append("\r\n".data(using: .utf8)!)
        }
        body.append("--\(boundary)--\r\n".data(using: .utf8)!)

        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.httpBody = body
        req.timeoutInterval = 60   // 解析+对账可能稍慢(涉及面板计算)
        let data = try await send(req)
        return try JSONDecoder().decode(ReviewUploadResponse.self, from: data)
    }

    func fetchReview(week: String) async throws -> ReviewGetResponse {
        let data = try await get("/api/v1/review?week=\(week)")
        return try JSONDecoder().decode(ReviewGetResponse.self, from: data)
    }

    // —— V2.1-⑤/⑦ 复盘板块:累计页五段 + 校准移交件 ————————————————————————
    //
    // 🔴 **两条端点都恒 200**(空态走各自的 `available=false` + 可读原因)→ V2.1
    // **零新增 reason 字符串**,`mapReason` 一字未动 —— ⛔ 别为它们加 case,也别把
    // `available=false` 当错误抛出去(那正是"把没有讲成故障"那类谎)。

    /// 行情状态 D0 盘后三态(V2.2-②,`market_regime_daily` **只读、零现算**)。
    /// `date` 缺省 = 表内最近一日。🔴 **一律不 404**:缺行 / 表空 / 参数非法一律 200 +
    /// `available=false` + 自由文本原因 —— ⛔ **不许把 `available=false` 当错误抛**
    /// (那正是「把没有讲成故障」);⛔ 也不许把它当「没风险」(那是把没看讲成没有)。
    func fetchMarketRegime(date: String? = nil) async throws -> MarketRegime {
        let path = "/api/v1/market-regime" + ((date?.isEmpty == false) ? "?date=\(date!)" : "")
        let data = try await get(path)
        return try JSONDecoder().decode(MarketRegime.self, from: data)
    }

    /// **D1 集合竞价确认层**的竞价小报告(V2.3.3-⑤,K8.md §二十)。`date` 缺省 = 今天。
    ///
    /// 🔴 **三态,调用方必须分开处理**(⛔ 别 `try?` 一把吞掉):
    ///   · `.auctionNotReady`(404)= **今天还没跑到 9:26 / 竞价层没跑过** → 合法空态,
    ///     **不画那张卡**(⛔ 不画空卡,那是噪声);
    ///   · `.auctionCorrupt`(500)= 有行但读不出 → **需要人排查**,⛔ 不进静默重试;
    ///   · 200 = 跑过了。⚠ **`baskets` 为空也是 200**(D0 当天没有 T1/T2 篮子),
    ///     那时 `basketsUnavailableReason` 会把原因说出口 —— ⛔ 别把它读成"没跑"。
    ///
    /// 🔴 **竞价结论只说明竞价反映出的信息,不等于买入指令**(K8 §二十 逐字)。
    func fetchAuction(date: String? = nil) async throws -> AuctionPayload {
        let path = "/api/v1/auction" + ((date?.isEmpty == false) ? "?date=\(date!)" : "")
        let data = try await get(path)
        return try JSONDecoder().decode(AuctionPayload.self, from: data)
    }

    /// 复盘板块「累计」页五段。`week` = 该周任意一天 `YYYYMMDD`(缺省本周)。
    func fetchReviewOverview(week: String? = nil, asOf: String? = nil) async throws -> ReviewOverview {
        var query: [String] = []
        if let w = week, !w.isEmpty { query.append("week=\(w)") }
        if let a = asOf, !a.isEmpty { query.append("asOf=\(a)") }
        let path = "/api/v1/review/overview" + (query.isEmpty ? "" : "?" + query.joined(separator: "&"))
        let data = try await get(path, timeout: 30)
        return try JSONDecoder().decode(ReviewOverview.self, from: data)
    }

    /// 校准移交件。`from`/`to` 缺省 = **最近一期已落盘的校准窗口**(⛔ 不是"现在算一份")。
    /// ⚠ 服务端那个查询参数就叫 `from`(Python 关键字,服务端用 `Query(alias="from")`
    /// 绕开)—— 客户端这边**照契约原样发 `?from=`**,别自作主张改名。
    func fetchReviewHandoff(from: String? = nil, to: String? = nil,
                            asOf: String? = nil) async throws -> ReviewHandoff {
        var query: [String] = []
        if let f = from, !f.isEmpty { query.append("from=\(f)") }
        if let t = to, !t.isEmpty { query.append("to=\(t)") }
        if let a = asOf, !a.isEmpty { query.append("asOf=\(a)") }
        let path = "/api/v1/review/handoff" + (query.isEmpty ? "" : "?" + query.joined(separator: "&"))
        // 装配要读产物 + 拼 markdown(仍是纯读),比一般 GET 稍慢,给足超时。
        let data = try await get(path, timeout: 30)
        return try JSONDecoder().decode(ReviewHandoff.self, from: data)
    }

    // MARK: - 传输层

    /// 由 base + path(可含 "?query")构造 URL。**禁止 `appendingPathComponent`**——它把整个
    /// path(含 "?date=...")当单个路径组件、"?" 编码成 "%3F",带 query 的端点真后端恒 404
    /// 且被静默吞(LinoN v1.3.0 致命坑)。
    static func makeURL(base: URL, path: String) -> URL? {
        URL(string: path, relativeTo: base)?.absoluteURL
    }

    private func get(_ path: String, timeout: TimeInterval = 12) async throws -> Data {
        try ensureToken()
        guard let url = Self.makeURL(base: baseURL, path: path) else {
            throw APIError.transport("无效 URL: \(path)")
        }
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.timeoutInterval = timeout
        return try await send(req)
    }

    private func post<B: Encodable>(_ path: String, body: B, timeout: TimeInterval = 12) async throws -> Data {
        try ensureToken()
        guard let url = Self.makeURL(base: baseURL, path: path) else {
            throw APIError.transport("无效 URL: \(path)")
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        req.timeoutInterval = timeout
        return try await send(req)
    }

    private func put<B: Encodable>(_ path: String, body: B, timeout: TimeInterval = 12) async throws -> Data {
        try ensureToken()
        guard let url = Self.makeURL(base: baseURL, path: path) else {
            throw APIError.transport("无效 URL: \(path)")
        }
        var req = URLRequest(url: url)
        req.httpMethod = "PUT"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        req.timeoutInterval = timeout
        return try await send(req)
    }

    /// 无请求体的 DELETE(`/alerts/{id}`、`/settings/providers/{name}` 用)。
    @discardableResult
    private func delete(_ path: String, timeout: TimeInterval = 12) async throws -> Data {
        try ensureToken()
        guard let url = Self.makeURL(base: baseURL, path: path) else {
            throw APIError.transport("无效 URL: \(path)")
        }
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.timeoutInterval = timeout
        return try await send(req)
    }

    private func ensureToken() throws {
        if token.trimmingCharacters(in: .whitespaces).isEmpty { throw APIError.noToken }
    }

    private func send(_ req: URLRequest) async throws -> Data {
        let data: Data
        let resp: URLResponse
        do {
            (data, resp) = try await session.data(for: req)
        } catch {
            throw APIError.transport(error.localizedDescription)
        }
        guard let http = resp as? HTTPURLResponse else {
            throw APIError.transport("无 HTTP 响应")
        }
        switch http.statusCode {
        case 200...299:
            return data
        case 401:
            throw APIError.unauthorized
        // 400 走 reason 映射;fallback 保持 `.server(400, …)` 语义 —— 未知 400 reason
        // 不冒充成某个具体业务错误。
        case 400:
            throw mapReason(data, fallback: .server(400, reasonString(data) ?? "请求不合法"))
        case 404:
            throw mapReason(data, fallback: .notHolding)
        // V2-⑮:409 / 422 也走 `mapReason` —— ② 的 `already_exists`、⑪ 的 `duplicate_alert`/
        // `invalid_rule`、`invalid_task`、`invalid_push_kinds` 五个 reason 各有独立 case,
        // 不再靠一句泛泛的「字段校验失败」蒙混(⑭-C 对拍表 §六 C3)。
        case 409:
            throw mapReason(data, fallback: .server(409, reasonString(data) ?? "资源冲突"))
        case 422:
            throw mapReason(data, fallback: .validation(reasonString(data) ?? "请检查输入"))
        // V2 B1(2026-08-04):**500 也走 `mapReason`** —— `card_corrupt`(冻结卡损坏)
        // 是唯一一个刻意用 500 承载的业务 reason(裁定:404 会说谎、且会与「卡还没生成」
        // 撞成同一类,而两者要求的反应完全相反)。fallback 保持 `.server(500, …)`,
        // 未知 500 不冒充成某个具体业务错误。
        case 500:
            throw mapReason(data, fallback: .server(500, reasonString(data) ?? "服务端内部错误"))
        default:
            throw APIError.server(http.statusCode, reasonString(data) ?? "未知错误")
        }
    }

    /// FastAPI 的 HTTPException(detail={ok:false, reason:...})落在 "detail" 里。
    ///
    /// ⚠ **新增会返 4xx 的端点必须回来检查这里要不要加 case**(CLAUDE.md 明文;机器判据
    /// 见 `tests/test_contract_crosscheck.py::test_map_reason_covers_every_server_reason…`)。
    /// **复用已有 reason 字符串不算"没加"**,只有全新字符串才需要新 case。
    private func mapReason(_ data: Data, fallback: APIError) -> APIError {
        guard let reason = reasonString(data) else { return fallback }
        switch reason {
        case "not_holding": return .notHolding
        // 通用「引用对象不存在」:决策追踪 / 问询详情 / provider / alert / **篮子复盘** /
        // **建仓快照** / **策略包** 共用这一个字符串(复用,不新增 case)。
        case "not_found": return .notFound
        case "not_trading_day": return .notTradingDay
        case "future_buy_date": return .futureBuyDate
        case "report_not_found": return .reportNotFound
        case "code_not_in_report": return .codeNotInReport
        // —— V2-⑭-B 三个全新 reason ——————————————————————————————————————
        case "basket_not_found": return .basketNotFound
        // ⛔ **文案是「本篮的卡还没生成」,不是「篮子不存在」** —— 后者会让用户以为
        // 系统丢了篮子;不加这个 case 则 404 fallback 会显示「持仓已清」(有案底)。
        case "card_not_ready": return .cardNotReady
        // ⛔ 与上一条**必须分开**:这条是「卡在库里但读不出」(500),文案「数据损坏,
        // 需要排查」;写成「还没生成」= 让用户白等一张永远不会来的卡。
        case "card_corrupt": return .cardCorrupt
        case "no_base_plan": return .noBasePlan
        // —— V2.3.3-⑤ 竞价确认层两个全新 reason ——————————————————————————————
        // 404「今天还没跑到 9:26 / 竞价层没跑过」——**合法空态**,调用方据此**不画那张卡**
        // (⛔ 不弹错误、⛔ 不画一张空卡)。
        case "auction_not_ready": return .auctionNotReady
        // 500「有行但读不出」——⛔ 与上一条分开:那份报告是冻结件,**不会自己好**,
        // 当成「还没生成」= 让客户端永远重试一份永远不来的报告。
        case "auction_corrupt": return .auctionCorrupt
        // —— 409 / 422 ————————————————————————————————————————————————
        case "already_exists": return .alreadyExists
        case "duplicate_alert": return .duplicateAlert
        case "invalid_rule": return .invalidRule
        case "invalid_task": return .invalidTask
        case "invalid_provider": return .invalidProvider
        case "invalid_tavily_key": return .invalidTavilyKey
        case "invalid_push_kinds": return .invalidPushKinds
        default: return fallback
        }
    }

    private func reasonString(_ data: Data) -> String? {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        if let detail = obj["detail"] as? [String: Any], let r = detail["reason"] as? String {
            // 422 若额外带 `unresolved` 数组(具体哪些名字没对上)则拼进 reason ——
            // **纯附加行为**,只在这个键存在时才拼接,不影响其它端点既有 `reason` 语义。
            if let unresolved = detail["unresolved"] as? [String], !unresolved.isEmpty {
                return "\(r):\(unresolved.joined(separator: "、"))"
            }
            return r
        }
        if let detailStr = obj["detail"] as? String { return detailStr }
        // 422 的 detail 是数组(pydantic v2 ValidationError 形状)
        if let arr = obj["detail"] as? [[String: Any]], let first = arr.first,
           let msg = first["msg"] as? String { return msg }
        return nil
    }
}
