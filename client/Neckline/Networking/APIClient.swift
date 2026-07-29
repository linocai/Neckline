//
//  APIClient.swift
//  Neckline — 后端 REST 客户端(track 4A FastAPI,§五 阶段4A)
//
//  端点契约见 `neckline/api/schemas.py` + `neckline/api/app.py`(逐字段对齐,不猜):
//    GET  /api/v1/health                    → 免鉴权,{status,version}
//    GET  /api/v1/report/latest             → ReportOut(含 v1.1 watchlistCheck[]/missedEntryHint,
//                                              v1.4-①-C dataFreshness)
//    GET  /api/v1/report?date=YYYYMMDD      → ReportOut(历史回放;带 query,走 makeURL)
//    GET  /api/v1/report/{date}/info-card/{code} → InfoCardOut(v1.4-④,60日K线/RS线/行业分歧线
//                                              + 快照/红黄牌/温和带/消息面/龙虎榜/市场语境)
//                                              · 404 report_not_found/code_not_in_report
//    GET  /api/v1/board                     → BoardOut(v1.1 事件含 precall/d5exit 两新类)
//    GET  /api/v1/positions                 → PositionsOut{holdings}(v1.1-B.1 生命周期字段,
//                                              v1.4-①-B priceStale/suspended_hold,v1.4-⑥-C 定格标注)
//    GET  /api/v1/positions/entry-suggestion→ EntrySuggestionOut(v1.2-E.5 改区间双档)
//    POST /api/v1/positions                 → {ok,position_id,stop_line}       · 400/422 字段
//    POST /api/v1/positions/{id}/close      → {ok}(closeReason 可选,v1.2-A2)   · 404 not_holding
//    GET  /api/v1/circuit                   → CircuitStateOut(v1.2-A2 熔断纪律状态)
//    POST /api/v1/circuit/unlock            → {ok}
//    POST /api/v1/decisions                 → DecisionOut(v1.2-B 预注册决策日志八项 +
//                                              v1.4-⑤-B ⑨maxChasePct)        · 400 max_chase_required
//    GET  /api/v1/decisions                 → {items:[DecisionOut]}(status/code/from/to 过滤)
//    GET  /api/v1/decisions/{id}/track      → DecisionTrackOut(v1.4-⑦-A 挂单未成交追踪) · 404 not_found
//    POST /api/v1/decisions/{id}/link       → {ok}                            · 404 not_found
//    POST /api/v1/decisions/{id}/cancel     → {ok}                            · 404 not_found
//    POST /api/v1/decisions/{id}/revise     → DecisionOut{新id}(新增修订行,旧行原地不变)
//    POST /api/v1/decisions/{id}/scenario-outcome → {ok}(只翻 matched)         · 404/422
//    GET  /api/v1/breathing/{id}/trades     → {items,baseCostAdj,edgeToPrice}(v1.2-G)· 404
//    POST /api/v1/breathing/{id}/trades     → BreathingTradeOut                · 404
//    DELETE /api/v1/breathing/trades/{id}   → {ok}                            · 404 not_found
//    POST /api/v1/inquiry                   → InquiryOut(描述性标注非裁决,§2.5,v1.4-⑦-B 带 inquiryId)
//    GET  /api/v1/inquiries                 → {items:[InquiryLogOut]}(v1.4-⑦-B 问询历史,分页+tsCode过滤)
//    GET  /api/v1/inquiries/{id}            → InquiryLogOut(详情)              · 404 not_found
//    GET  /api/v1/settings                  → SettingsOut(key 只回布尔;push 六字段 v1.3-②)
//    PUT  /api/v1/settings/llm              → {ok}                            · 422 供应商非法
//    PUT  /api/v1/settings/push             → {ok}(六字段:report/retreatBrake/precall/d5exit/circuit/holdingAlert)
//    GET  /api/v1/settings/intel-boards     → IntelWatchBoardsOut{boards}(v1.3-⑥,五常驻板块可配)
//    PUT  /api/v1/settings/intel-boards     → IntelWatchBoardsOut{boards}    · 422 board_not_found(禁模糊匹配)
//    POST /api/v1/devices                   → {ok}
//    GET  /api/v1/watchlist                 → WatchlistOut{items,maxSize}(v1.1-C/F)
//    POST /api/v1/watchlist                 → {ok,item}                       · 422 watchlist_full
//    DELETE /api/v1/watchlist/{code}        → {ok}                            · 404 not_found
//    PUT  /api/v1/watchlist/{code}/pin      → {ok}                            · 404 not_found
//    POST /api/v1/watchlist/reconcile-ths   → ThsReconcileOut(multipart,字段名 file)
//    GET  /api/v1/watchlist/export-ths      → ThsExportOut{text,count}
//  鉴权:Authorization: Bearer <API_TOKEN>(health 外全部)。
//

import Foundation

// MARK: - 错误类型(结构化 reason,UI 据此弹提示)

enum APIError: Error, LocalizedError, Equatable {
    case unauthorized
    case notHolding          // 404 该持仓已清或不存在(POST /positions/{id}/close)
    // v1.1-F:404 通用「未找到」(watchlist delete/pin 代码不存在等,reason="not_found")。
    // 与 `notHolding` 分开是因为两者文案不同,合并会让"删自选未命中"误显"持仓已清"。
    case notFound
    // v1.4-①-A:补录买入日的两个 400 reason。**逐个建 case,不吃 fallback**——守项目
    // CLAUDE.md「404/reason 映射」坑(watchlist `not_found` 曾被 fallback 误显成
    // 「持仓已清」)。两者文案不同:一个是「那天不开市」,一个是「你填到未来去了」。
    case notTradingDay       // 400 buyDate 不是交易日(reason="not_trading_day")
    case futureBuyDate       // 400 buyDate 晚于今天(reason="future_buy_date")
    // v1.4-④-B:`GET /report/{date}/info-card/{code}` 两个 404 reason(**逐个建 case,
    // 不吃 fallback**,同 notTradingDay/futureBuyDate 守法)。
    case reportNotFound      // 404 该日期未生成过报告(日期非法 / 当天未生成)
    case codeNotInReport     // 404 该票不在当日候选榜里
    // v1.4-⑤-B:`POST/revise /decisions` 的 `maxChasePct` 必须显式传(填数字或显式
    // null),省略该键 → 400 reason="max_chase_required"。
    case maxChaseRequired
    case validation(String)  // 422 字段校验(含 provider 白名单)
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
        case .codeNotInReport:  return "这只票不在当日候选榜里"
        case .maxChaseRequired: return "请设置「最高追价上限」(填数字,或勾选不设上限)"
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
    let candidates: [Candidate]
    let degraded: Bool
    let reason: String
    // v1.1-B.4:漏录兜底提示。`Optional` 兼容老响应/测试 fixture 没有这个键的情形
    // (真实后端恒返回该字段,但用 Optional 更稳,缺失时按空串处理,不崩)。
    let missedEntryHint: String?
    // v1.3-③-C1/C2/C4「情报」板块(§五 v1.3-⑥-F)。
    let intel: IntelSection?
    let sectorMoneyflow: SectorMoneyflowSection?
    let newsAlerts: [NewsAlert]?
    let newsAlertsScan: [NewsAlertScanStatus]?
    // v1.4-①-C:板块数据新鲜度(§七 P0-3)。同 intel/sectorMoneyflow 惯例——服务端恒是
    // 对象(旧报告是空字典 `{}`),`try?` 把「形状对不上」也当「没有」处理,归一成 nil。
    let dataFreshness: DataFreshness?

    /// 显式 `CodingKeys`(提供自定义 `init(from:)` 时,编译器不总能推出合成
    /// `CodingKeys`,显式声明避免依赖不透明的合成时机)。字段名与 JSON 字面一致,
    /// 逐一列出。
    enum CodingKeys: String, CodingKey {
        case tradeDate, generatedAt, strategyVersion, sentiment, sectors, candidates
        case degraded, reason, missedEntryHint, intel, sectorMoneyflow, newsAlerts, newsAlertsScan
        case dataFreshness
    }

    /// 自定义解码(而非纯合成):`intel`/`sectorMoneyflow`/`dataFreshness` 服务端**恒是
    /// 对象**(旧报告/降级态是空对象 `{}`,不是缺键或 null)——空对象缺我方强类型要求的
    /// 字段(如 `tradeDate`/`sectorLagDays`),标准合成解码会直接抛错,这里用 `try?` 把
    /// "形状对不上"也当"没有"处理,归一成 `nil`(§硬要求「没有 vs 没看」由 nil 表达
    /// "这份报告没有该节数据",UI 据此展示诚实空态而非崩溃)。`newsAlerts`/
    /// `newsAlertsScan` 是数组,老响应/老 fixture 缺键时按空数组兜底。
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradeDate = try c.decode(String.self, forKey: .tradeDate)
        generatedAt = try c.decode(String.self, forKey: .generatedAt)
        strategyVersion = try c.decode(String.self, forKey: .strategyVersion)
        sentiment = try c.decodeIfPresent(SentimentSnapshot.self, forKey: .sentiment)
        sectors = try c.decode([SectorSnapshot].self, forKey: .sectors)
        candidates = try c.decode([Candidate].self, forKey: .candidates)
        degraded = try c.decode(Bool.self, forKey: .degraded)
        reason = try c.decode(String.self, forKey: .reason)
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

private struct PositionsListResponse: Decodable { let holdings: [Position] }

struct OpenPositionRequest: Encodable {
    let code: String
    let name: String?
    let buy_price: Double
    let qty: Int
    let entry_reason: String
    // v1.3-①/⑥:补录开仓实付买入费用(camelCase,与既有 snake_case 字段并存——契约
    // 如此,同 `ClosePositionRequest.closeReason` 惯例)。UI 层强制必填(见
    // `PositionEntryForm.isValid`),这里仍设 Optional + 默认 nil——服务端本就宽松
    // (`PositionOpenIn.buyFees: Optional[float] = None`),且这样不必为完全不关心
    // 费用的既有测试调用点(如 IntegrationSmokeTests 的基础开仓闭环)逐一补参数。
    let buyFees: Double?
    // v1.4-①-A(§七 P0-1):真实买入日 'YYYYMMDD'。Optional + 默认 nil —— Swift 合成的
    // Encodable 对 Optional 走 `encodeIfPresent`,**nil 时该键根本不出现在 JSON 里**,
    // 故不传时请求体与 v1.4 之前逐字节相同(服务端此时取今天,行为不变)。
    // 日期选择器 UI 归第 ⑧ 块;本块只把传输层的口子开好 + 400 reason 映射到位。
    let buyDate: String?
}
private struct OpenPositionResponse: Decodable {
    let ok: Bool
    let position_id: Int
    let stop_line: Double
}

struct ClosePositionRequest: Encodable {
    let sell_price: Double
    let sell_time: String?   // 'YYYYMMDD';缺省服务端用今日
    // v1.2-A2:离场原因(可选)。⚠ 契约字段名 `closeReason` 是 camelCase,与本结构体
    // 既有 `sell_price`/`sell_time` 的 snake_case 并存——后端契约如此(见 CLAUDE.md
    // 「v1.1-E/F/G 踩过的坑」同款留痕),不自作主张统一大小写。
    let closeReason: String?
    // v1.3-①/⑥:清仓实付卖出费用真数(可选,成交后回填)——周复盘对账用真数、不用估数。
    let sellFees: Double?
}

private struct ChatMessageWire: Encodable { let role: String; let content: String }
private struct InquiryRequest: Encodable { let code: String; let messages: [ChatMessageWire] }
private struct InquiryResponse: Decodable {
    let ok: Bool
    let code: String
    let reply: String
    let verdict: String
    let evidence: [String]
    let degraded: Bool
    /// v1.4-⑦-B:本次问询落进 `inquiry_log` 档案表的行 id。**Optional 是契约语义,不是
    /// 容错兜底**——服务端落库是旁路(失败时如实发 null,本次回答仍有效,见 schemas.py
    /// `InquiryOut.inquiryId` 注释);老服务端(v1.3 及更早)压根没有这个键 → nil。
    /// ⚠ v1.4 review 契约线 🟡-3:服务端 → JSON 三段都在,唯独这里从前没声明,字段被
    /// Codable 静默丢弃 =「链路末段漏字段」的复发形态(这次漏在客户端侧)。
    let inquiryId: Int?
}

private struct SettingsResponse: Decodable {
    let llmProvider: String?
    let llmKeySet: Bool
    let push: PushSettings
    let reviewColMap: [String: String]
}
struct SettingsLLMRequest: Encodable { let provider: String; let apiKey: String }
/// v1.1-G.1 推送开关四字段(报告 / 退潮刹车 / 盘前校准 / D5 时间退出)+ v1.2-A2 第五字段
/// (熔断提醒)+ v1.3-②/⑥ 第六字段(K4 持仓派发警报)。六字段均必填(后端 `SettingsPushIn`
/// 无默认值,缺字段 → 422)。
struct SettingsPushRequest: Encodable {
    let report: Bool; let retreatBrake: Bool; let precall: Bool; let d5exit: Bool
    let circuit: Bool; let holdingAlert: Bool
}
struct SettingsReviewColMapRequest: Encodable { let colMap: [String: String] }

// —— v1.3-③-C3/⑥ 五常驻板块可配(`GET/PUT /settings/intel-boards`)——————————————————
struct IntelWatchBoardsRequest: Encodable { let boards: [String] }
private struct IntelWatchBoardsResponse: Decodable { let boards: [String] }

struct DeviceRegisterRequest: Encodable { let token: String; let platform: String }

private struct OkResponse: Decodable { let ok: Bool }

// —— v1.1-C/F 自选池(watchlist)+ 同花顺 txt 对账/导出 ————————————————————————

private struct WatchlistResponse: Decodable { let items: [WatchlistItem]; let maxSize: Int }
private struct WatchlistAddResponse: Decodable { let ok: Bool; let item: WatchlistItem }
struct WatchlistAddRequest: Encodable { let code: String; let name: String?; let note: String? }
struct WatchlistPinRequest: Encodable { let pinned: Bool }
private struct ThsReconcileResponse: Decodable {
    let ok: Bool; let onlyInThs: [String]; let onlyInNeckline: [String]; let both: [String]
}
private struct ThsExportResponse: Decodable { let text: String; let count: Int }

// —— v1.2-E.5 一键补录预填推荐(区间双档,替换 v1.1 的单 `qty`)——————————————————

private struct EntrySuggestionResponse: Decodable {
    let ok: Bool; let code: String; let price: Double
    let qtyLow: Int; let qtyHigh: Int; let capFloor: Double; let capCeil: Double; let stopLine: Double
}

/// 无请求体 POST 占位({})。
private struct EmptyBody: Encodable {}

// —— v1.2-B 预注册决策日志(§五 v1.2-E.1;`code`/`name` 走 create,revise 请求体
// 不含这两个字段——修订不能换股票,新行继承原行 ts_code/name,见 CLAUDE.md
// 「decision_log 唯一写入通道」坑)——————————————————————————————————————————

// v1.4-⑤-B(需求 2 补充):`maxChasePct` 必须**永远出现在请求体里**(填数字或显式
// `null`),绝不能被省略——服务端 `_extract_max_chase_pct_or_400` 用
// `model_fields_set` 判「这个键有没有出现过」,缺键 → 400 `reason=max_chase_required`。
// Swift 编译器合成的 `Encodable` 对 `Optional` 属性一律走 `encodeIfPresent`(nil 时
// 整个键消失,同本文件其余 Optional 字段的既有行为)——那对这一个字段是错的,故两个
// 请求体改手写 `encode(to:)`,只对 `maxChasePct` 用 `encode(_:forKey:)`(`Optional`
// 类型自身的 `Encodable` conformance 在 nil 时走 `encodeNil`,故键永远存在,值为
// 数字或 JSON null),其余字段仍是 `encodeIfPresent`(省略键)/`encode`(必填),逐位
// 保留原有请求体形状。

struct DecisionCreateRequest: Encodable {
    let code: String
    let name: String?
    let whyBuy: String
    let whyEntryPrice: String
    let targetPrice: Double?
    let exitLow: Double?
    let exitHigh: Double?
    let thesisTags: [String]
    let invalidation: String
    let contingencyScenarios: [ContingencyScenario]
    let playbookTag: String
    let plannedPrice: Double?
    let plannedQty: Int?
    let maxChasePct: Double?

    enum CodingKeys: String, CodingKey {
        case code, name, whyBuy, whyEntryPrice, targetPrice, exitLow, exitHigh
        case thesisTags, invalidation, contingencyScenarios, playbookTag
        case plannedPrice, plannedQty, maxChasePct
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(code, forKey: .code)
        try c.encodeIfPresent(name, forKey: .name)
        try c.encode(whyBuy, forKey: .whyBuy)
        try c.encode(whyEntryPrice, forKey: .whyEntryPrice)
        try c.encodeIfPresent(targetPrice, forKey: .targetPrice)
        try c.encodeIfPresent(exitLow, forKey: .exitLow)
        try c.encodeIfPresent(exitHigh, forKey: .exitHigh)
        try c.encode(thesisTags, forKey: .thesisTags)
        try c.encode(invalidation, forKey: .invalidation)
        try c.encode(contingencyScenarios, forKey: .contingencyScenarios)
        try c.encode(playbookTag, forKey: .playbookTag)
        try c.encodeIfPresent(plannedPrice, forKey: .plannedPrice)
        try c.encodeIfPresent(plannedQty, forKey: .plannedQty)
        try c.encode(maxChasePct, forKey: .maxChasePct)   // 永远出现该键,nil → JSON null
    }
}

struct DecisionReviseRequest: Encodable {
    let whyBuy: String
    let whyEntryPrice: String
    let targetPrice: Double?
    let exitLow: Double?
    let exitHigh: Double?
    let thesisTags: [String]
    let invalidation: String
    let contingencyScenarios: [ContingencyScenario]
    let playbookTag: String
    let plannedPrice: Double?
    let plannedQty: Int?
    let maxChasePct: Double?

    enum CodingKeys: String, CodingKey {
        case whyBuy, whyEntryPrice, targetPrice, exitLow, exitHigh
        case thesisTags, invalidation, contingencyScenarios, playbookTag
        case plannedPrice, plannedQty, maxChasePct
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(whyBuy, forKey: .whyBuy)
        try c.encode(whyEntryPrice, forKey: .whyEntryPrice)
        try c.encodeIfPresent(targetPrice, forKey: .targetPrice)
        try c.encodeIfPresent(exitLow, forKey: .exitLow)
        try c.encodeIfPresent(exitHigh, forKey: .exitHigh)
        try c.encode(thesisTags, forKey: .thesisTags)
        try c.encode(invalidation, forKey: .invalidation)
        try c.encode(contingencyScenarios, forKey: .contingencyScenarios)
        try c.encode(playbookTag, forKey: .playbookTag)
        try c.encodeIfPresent(plannedPrice, forKey: .plannedPrice)
        try c.encodeIfPresent(plannedQty, forKey: .plannedQty)
        try c.encode(maxChasePct, forKey: .maxChasePct)   // 永远出现该键,nil → JSON null
    }
}

struct DecisionLinkRequest: Encodable { let positionId: Int }
struct ScenarioOutcomeItemRequest: Encodable { let index: Int; let matched: Bool }
struct ScenarioOutcomeRequest: Encodable { let outcomes: [ScenarioOutcomeItemRequest] }

private struct DecisionsListResponse: Decodable { let items: [DecisionLog] }

// —— v1.2-G 呼吸试验仓台账(§五 v1.2-E.4)—————————————————————————————————————

struct BreathingTradeRequest: Encodable {
    let buyPrice: Double
    let sellPrice: Double
    let qty: Int
    let fees: Double        // 客户端如实录入,服务端原样落库、不按费率估算
    let tDate: String?
    let note: String?
}

private struct BreathingTradesResponse: Decodable {
    let items: [BreathingTrade]
    let baseCostAdj: Double?
    let edgeToPrice: Double?
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

    // —— health(免鉴权,联通性自检 + v1.5-⑤-E 服务端版本诚实展示)——
    /// 端点本就返 `{status, version}`,此前 `version` 被丢弃(§五 v1.5-⑤-E「此前被
    /// 丢弃」)。**返回值改元组**——`ok` 语义不变(200 且 `status=="ok"`),`version`
    /// 独立于 `ok` 展示(即便 `ok=false` 也把拿到的 version 原样带回,由调用方判断
    /// 用不用;拿不到/解码失败 → `nil`,不冒充"服务端无版本")。
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
                              sectors: r.sectors, candidates: r.candidates,
                              degraded: r.degraded, reason: r.reason,
                              missedEntryHint: r.missedEntryHint ?? "",
                              intel: r.intel, sectorMoneyflow: r.sectorMoneyflow,
                              newsAlerts: r.newsAlerts ?? [], newsAlertsScan: r.newsAlertsScan ?? [],
                              dataFreshness: r.dataFreshness)
    }

    /// v1.4-④-B:单只完整信息卡(60 日 K 线/RS 线/行业分歧线 + 快照 + 红黄牌 + 温和带 +
    /// 消息面 + 龙虎榜 + 市场语境,§五 v1.4-④)。**候选专属**(本版只接候选,不接持仓/
    /// 自选——若发现某处需要脱离候选对象单独调用本端点,先停下来核对,不要自行猜测)。
    /// `code` 支持裸 6 位或带交易所后缀(服务端 `normalize_ts_code` 归一比对)。
    /// 直接 `Codable` 解码 `InfoCard`(字段名与 JSON 字面一致,不需要私有 wire DTO 中转,
    /// 同 `Position`/`WatchlistItem`/`BoardEvent` 先例)。404 两个 reason:
    /// `report_not_found`(日期非法/当天未生成过报告)、`code_not_in_report`(该日报告
    /// 存在但这只票不在候选榜里)——均映射到独立 `APIError` case,不吃 fallback。
    /// **`timeout: 60`(v1.4.1 热修,§七 P1-26)** —— 本端点是全仓最重的读:一次请求要装
    /// 60 日 K 线面板 + 大盘指数线 + 情绪仪表盘(5 次全市场横截面)+ 龙虎榜回看 + 红黄牌,
    /// 生产 2 vCPU 箱上冷调实测以秒计。默认 12s **必然超时**(用户报障「信息卡总是加载
    /// 失败」的直接原因),且失败会诱发用户反复重试、把常驻服务顶到内存节流线,越试越慢。
    /// 服务端已按年裁剪分区把耗时压下来,但**客户端也不该拿一个比服务端真实耗时还短的
    /// 超时去赌** —— 照问询台(同样重、同样 60s)的既有惯例给足预算。
    func fetchInfoCard(date: String, code: String) async throws -> InfoCard {
        let data = try await get("/api/v1/report/\(date)/info-card/\(code)", timeout: 60)
        return try JSONDecoder().decode(InfoCard.self, from: data)
    }

    // —— 4A.3 盘中看板 ——
    func fetchBoard() async throws -> BoardSnapshot {
        let data = try await get("/api/v1/board")
        let r = try JSONDecoder().decode(BoardResponse.self, from: data)
        return BoardSnapshot(tradeDate: r.tradeDate, asof: r.asof,
                             retreatBrake: r.retreatBrake, events: r.events)
    }

    // —— 4A.4 持仓(审计台账;系统永不自动下单,§3.8)——
    func fetchPositions() async throws -> [Position] {
        let data = try await get("/api/v1/positions")
        return try JSONDecoder().decode(PositionsListResponse.self, from: data).holdings
    }

    /// 开仓录入(补录用户已在券商完成的真实操作)。返回 (positionId, 派生止损线)。
    /// `buyFees`(v1.3-①/⑥):实付买入费用。UI 层(`PositionEntryForm.isValid`)强制
    /// 必填,这里仍是 Optional + 默认 nil(服务端宽松,且不强迫不关心费用的既有调用点
    /// 逐一改)。
    /// `buyDate`(v1.4-①-A):真实买入日 'YYYYMMDD',**不传 → 服务端取今天**(与 v1.4
    /// 之前逐位一致)。服务端校验非交易日 / 未来日 → 400 + reason,分别映射到
    /// `.notTradingDay` / `.futureBuyDate`(见 `mapReason`)。
    func openPosition(code: String, name: String?, buyPrice: Double, qty: Int,
                      entryReason: String, buyFees: Double? = nil,
                      buyDate: String? = nil) async throws -> (positionId: Int, stopLine: Double) {
        let body = OpenPositionRequest(code: code, name: name, buy_price: buyPrice,
                                       qty: qty, entry_reason: entryReason, buyFees: buyFees,
                                       buyDate: buyDate)
        let data = try await post("/api/v1/positions", body: body)
        let r = try JSONDecoder().decode(OpenPositionResponse.self, from: data)
        return (r.position_id, r.stop_line)
    }

    /// 清仓录入。`sellTime` 缺省 → 服务端用今日('YYYYMMDD')。`closeReason`(v1.2-A2)
    /// 可选——不传 → 服务端落 NULL,熔断评估走价格兜底判止损(不由客户端二次猜)。
    /// `sellFees`(v1.3-①/⑥):清仓实付卖出费用真数,可选、成交后回填,周复盘对账用真数。
    @discardableResult
    func closePosition(id: Int, sellPrice: Double, sellTime: String? = nil,
                       closeReason: String? = nil, sellFees: Double? = nil) async throws -> Bool {
        let body = ClosePositionRequest(sell_price: sellPrice, sell_time: sellTime,
                                        closeReason: closeReason, sellFees: sellFees)
        let data = try await post("/api/v1/positions/\(id)/close", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    /// 一键补录预填推荐(v1.2-E.5 改区间双档,只读计算,不写台账):`qtyHigh`/`capCeil`
    /// = 现役 `single_cap` 违纪判定上限对应手数/金额(**非推荐值**),`qtyLow`/`capFloor`
    /// = 半仓保守下沿;`stopLine = price×(1−stop_pct)`(读现役 config)。客户端只展示
    /// 两档,不替用户拍单笔金额。`code`/`price` 走 query(同 `fetchReport(date:)` 惯例,
    /// 需走 `makeURL` 免 "?" 编码坑)。
    func entrySuggestion(code: String, price: Double) async throws -> EntrySuggestionRange {
        let priceStr = String(format: "%.2f", price)
        let data = try await get("/api/v1/positions/entry-suggestion?code=\(code)&price=\(priceStr)")
        let r = try JSONDecoder().decode(EntrySuggestionResponse.self, from: data)
        return EntrySuggestionRange(code: r.code, price: r.price, qtyLow: r.qtyLow, qtyHigh: r.qtyHigh,
                                    capFloor: r.capFloor, capCeil: r.capCeil, stopLine: r.stopLine)
    }

    // —— v1.2-A2 熔断纪律状态(§五 v1.2-E.3;纯提醒层,客户端只读锁定态 + 记录用户
    // 解锁 ack,绝不代下单/撤单、绝不拦 `POST /positions`,§3.8)——————————————————————

    /// 权威熔断锁定态。`PositionsOut.circuit` 内嵌同一形状供今日计划面直接读取
    /// (契约清单「或」两种取法均可,这里选独立端点,避免牵动 `fetchPositions()`
    /// 既有返回类型 / 既有单测)。
    func getCircuit() async throws -> CircuitState {
        let data = try await get("/api/v1/circuit")
        return try JSONDecoder().decode(CircuitState.self, from: data)
    }

    /// 客户端「熔断复盘」按钮解锁(先展示强制复盘材料,用户确认后调用)。无锁定态时
    /// 幂等成功。
    @discardableResult
    func unlockCircuit() async throws -> Bool {
        let data = try await post("/api/v1/circuit/unlock", body: EmptyBody())
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    // —— v1.2-B 预注册决策日志(§五 v1.2-E.1;审计件、非下单件——本节任何方法都不
    // 触发任何持仓写入)——————————————————————————————————————————————————————————

    /// 预注册(status=pending)。`createdAt` 服务端生成,请求体本就无此字段,物理
    /// 杜绝客户端覆盖(同 CLAUDE.md「B 块 created_at 三处防线」①)。`maxChasePct`
    /// (⑨,v1.4-⑤-B)**必须显式传**(填数字或显式 `nil`=不设上限)——省略等价于
    /// Swift 里没得选,本参数无默认值,强制每个调用点显式决定,与服务端「省略该键→
    /// 400」的强制语义对齐。
    func createDecision(code: String, name: String?, whyBuy: String, whyEntryPrice: String,
                        targetPrice: Double?, exitLow: Double?, exitHigh: Double?,
                        thesisTags: [String], invalidation: String,
                        contingencyScenarios: [ContingencyScenario], playbookTag: String,
                        plannedPrice: Double?, plannedQty: Int?,
                        maxChasePct: Double?) async throws -> DecisionLog {
        let body = DecisionCreateRequest(code: code, name: name, whyBuy: whyBuy, whyEntryPrice: whyEntryPrice,
                                         targetPrice: targetPrice, exitLow: exitLow, exitHigh: exitHigh,
                                         thesisTags: thesisTags, invalidation: invalidation,
                                         contingencyScenarios: contingencyScenarios, playbookTag: playbookTag,
                                         plannedPrice: plannedPrice, plannedQty: plannedQty,
                                         maxChasePct: maxChasePct)
        let data = try await post("/api/v1/decisions", body: body)
        return try JSONDecoder().decode(DecisionLog.self, from: data)
    }

    /// 客户端历史 + macOS 归因表(默认返全部,可按 `status`/`code`/`from`/`to` 过滤;
    /// `from`/`to` 对齐服务端 `created_at` 日期区间,'YYYYMMDD')。
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

    /// v1.4-⑦-A:挂单未成交追踪(§七 P3-12)。`id` 不存在 → 404 not_found(既有
    /// `case "not_found": return .notFound` 已覆盖,未新增 case);决策存在但还没攒到
    /// 任何追踪快照 → 合法 200 空态 `rows=[]`,不是错误(两种「空」分开)。直接
    /// `Codable` 解码 `DecisionTrack`(字段名与 JSON 字面一致)。
    func decisionTrack(id: Int) async throws -> DecisionTrack {
        let data = try await get("/api/v1/decisions/\(id)/track")
        return try JSONDecoder().decode(DecisionTrack.self, from: data)
    }

    /// 成交后一键关联:`status` 置 filled + `position_id` 回填。id 不存在 → 404 not_found。
    @discardableResult
    func linkDecision(id: Int, positionId: Int) async throws -> Bool {
        let body = DecisionLinkRequest(positionId: positionId)
        let data = try await post("/api/v1/decisions/\(id)/link", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    /// 用户放弃该预注册计划:`status` 置 cancelled。id 不存在 → 404 not_found。
    @discardableResult
    func cancelDecision(id: Int) async throws -> Bool {
        let data = try await post("/api/v1/decisions/\(id)/cancel", body: EmptyBody())
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    /// 新增一行修订(旧行原地不变,`revisionOf` 落链根 id,`status` 重置 pending、
    /// `positionId` 重置为 nil——修订与「已成交关联」是两件事,不自动重新关联)。
    /// `id` 不存在 → 404 not_found。**`maxChasePct` 必须显式传**(同 `createDecision`
    /// 纪律——修订等于重新预注册一整套九项内容)。
    func reviseDecision(id: Int, whyBuy: String, whyEntryPrice: String, targetPrice: Double?,
                        exitLow: Double?, exitHigh: Double?, thesisTags: [String], invalidation: String,
                        contingencyScenarios: [ContingencyScenario], playbookTag: String,
                        plannedPrice: Double?, plannedQty: Int?,
                        maxChasePct: Double?) async throws -> DecisionLog {
        let body = DecisionReviseRequest(whyBuy: whyBuy, whyEntryPrice: whyEntryPrice, targetPrice: targetPrice,
                                         exitLow: exitLow, exitHigh: exitHigh, thesisTags: thesisTags,
                                         invalidation: invalidation, contingencyScenarios: contingencyScenarios,
                                         playbookTag: playbookTag, plannedPrice: plannedPrice, plannedQty: plannedQty,
                                         maxChasePct: maxChasePct)
        let data = try await post("/api/v1/decisions/\(id)/revise", body: body)
        return try JSONDecoder().decode(DecisionLog.self, from: data)
    }

    /// ⑦ 情景树结果标记专用(只翻 `matched`,绝不改 `scenario`/`trigger`/`action`)。
    /// `id` 不存在 → 404;`index` 越界 → 422(FastAPI/pydantic 走既有 `.validation` 映射)。
    @discardableResult
    func setScenarioOutcome(id: Int, outcomes: [(index: Int, matched: Bool)]) async throws -> Bool {
        let body = ScenarioOutcomeRequest(outcomes: outcomes.map { ScenarioOutcomeItemRequest(index: $0.index, matched: $0.matched) })
        let data = try await post("/api/v1/decisions/\(id)/scenario-outcome", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    // —— v1.2-G 呼吸试验仓台账(§五 v1.2-E.4;写入只经这三个端点,同 positions/
    // watchlist 姿势)—————————————————————————————————————————————————————————

    /// T 子账列表 + 底仓摊薄成本 / 先手距离派生。底仓不存在 → 404 not_found。
    func breathingTrades(positionId: Int) async throws -> BreathingLedger {
        let data = try await get("/api/v1/breathing/\(positionId)/trades")
        let r = try JSONDecoder().decode(BreathingTradesResponse.self, from: data)
        return BreathingLedger(items: r.items, baseCostAdj: r.baseCostAdj, edgeToPrice: r.edgeToPrice)
    }

    /// 录入一次 T。`fees` 必填、如实录入(不替用户估费率,G.2)。底仓不存在 → 404 not_found。
    func addBreathingTrade(positionId: Int, buyPrice: Double, sellPrice: Double, qty: Int, fees: Double,
                           tDate: String? = nil, note: String? = nil) async throws -> BreathingTrade {
        let body = BreathingTradeRequest(buyPrice: buyPrice, sellPrice: sellPrice, qty: qty, fees: fees,
                                         tDate: tDate, note: note)
        let data = try await post("/api/v1/breathing/\(positionId)/trades", body: body)
        return try JSONDecoder().decode(BreathingTrade.self, from: data)
    }

    /// 误录可删(硬删除)。不存在 → 404 not_found(幂等安全,重复删除同样 404)。
    @discardableResult
    func deleteBreathingTrade(id: Int) async throws -> Bool {
        let data = try await delete("/api/v1/breathing/trades/\(id)")
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    // —— 4A.5 问询台(§2.5:描述性标注非裁决,永不「现在就买」)——
    /// `messages` 为客户端持有的全部上下文(无状态端点,每次全量回传,继承 LinoN `/chat` 姿势)。
    func sendInquiry(code: String, messages: [ChatMessage]) async throws -> InquiryResult {
        let wire = messages.map { ChatMessageWire(role: $0.role.rawValue, content: $0.text) }
        let body = InquiryRequest(code: code, messages: wire)
        // LLM 段可能真联网搜索 + 降级重试(§3.4 _MAX_ATTEMPTS=3),给足长超时,同 LinoN chat 60s 模式。
        let data = try await post("/api/v1/inquiry", body: body, timeout: 60)
        let r = try JSONDecoder().decode(InquiryResponse.self, from: data)
        return InquiryResult(code: r.code, reply: r.reply, verdict: InquiryVerdict(r.verdict),
                             evidence: r.evidence, degraded: r.degraded, inquiryId: r.inquiryId)
    }

    // —— v1.4-⑦-B 问询历史(§七 P3-13;**与已退役的 `inquiry_pool` 无耦合**,本节读的
    // 是 `inquiry_log` 档案表)——————————————————————————————————————————————————

    private struct InquiryLogsListResponse: Decodable { let items: [InquiryLogEntry] }

    /// 倒序分页,`tsCode` 归一后等值匹配(空/nil = 不按代码过滤)。
    func fetchInquiries(limit: Int = 20, offset: Int = 0, tsCode: String? = nil) async throws -> [InquiryLogEntry] {
        var query = ["limit=\(limit)", "offset=\(offset)"]
        if let c = tsCode, !c.trimmingCharacters(in: .whitespaces).isEmpty { query.append("tsCode=\(c)") }
        let data = try await get("/api/v1/inquiries?" + query.joined(separator: "&"))
        return try JSONDecoder().decode(InquiryLogsListResponse.self, from: data).items
    }

    /// 详情。不存在 → 404 not_found(既有 case 覆盖,未新增)。
    func fetchInquiryDetail(id: Int) async throws -> InquiryLogEntry {
        let data = try await get("/api/v1/inquiries/\(id)")
        return try JSONDecoder().decode(InquiryLogEntry.self, from: data)
    }

    // —— 4A.5 设置(🔴 LLM key 服务端存取)——
    func fetchSettings() async throws -> SettingsSnapshot {
        let data = try await get("/api/v1/settings")
        let r = try JSONDecoder().decode(SettingsResponse.self, from: data)
        return SettingsSnapshot(llmProvider: r.llmProvider, llmKeySet: r.llmKeySet, push: r.push,
                                reviewColMap: r.reviewColMap)
    }

    /// 写 LLM 供应商 + key。**key 只发一次、不回显、不落日志**(§3.4 高危区)。
    @discardableResult
    func putSettingsLLM(provider: LLMProviderKind, apiKey: String) async throws -> Bool {
        let body = SettingsLLMRequest(provider: provider.rawValue, apiKey: apiKey)
        let data = try await put("/api/v1/settings/llm", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    /// 推送开关六字段一并写入(报告 / 退潮刹车 / 盘前校准 / D5 时间退出 / v1.2-A2 熔断提醒 /
    /// v1.3-② K4 持仓派发警报)。`holdingAlert` 给默认值 `true`(六字段里最新加的一个,
    /// 服务端本就要求六字段必填——默认值只是省得既有「不关心持仓警报开关」的调用点
    /// 逐一改,实际请求体仍会带上这第六字段,不会让后端因缺字段 422)。
    @discardableResult
    func putSettingsPush(report: Bool, retreatBrake: Bool, precall: Bool, d5exit: Bool,
                         circuit: Bool, holdingAlert: Bool = true) async throws -> Bool {
        let body = SettingsPushRequest(report: report, retreatBrake: retreatBrake, precall: precall,
                                       d5exit: d5exit, circuit: circuit, holdingAlert: holdingAlert)
        let data = try await put("/api/v1/settings/push", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    /// 周复盘交割单列映射(plan 4D.1「留 review_col_map 可覆盖以支持两家券商原始格式」)。
    @discardableResult
    func putSettingsReviewColMap(_ colMap: [String: String]) async throws -> Bool {
        let body = SettingsReviewColMapRequest(colMap: colMap)
        let data = try await put("/api/v1/settings/review-col-map", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    // —— v1.3-③-C3/⑥ 五常驻板块可配 ——————————————————————————————————————————

    /// 读当前常驻板块名单(从未配置 → 默认五板块;曾显式清空 → 空数组)。
    func fetchIntelWatchBoards() async throws -> IntelWatchBoards {
        let data = try await get("/api/v1/settings/intel-boards")
        return IntelWatchBoards(boards: try JSONDecoder().decode(IntelWatchBoardsResponse.self, from: data).boards)
    }

    /// 写常驻板块名单。**禁模糊匹配**——每个名字须能在 `ths_index.name` 精确匹配到,匹配
    /// 不到 → `APIError.validation("board_not_found:名字1、名字2")`(`reasonString` 对
    /// `unresolved` 数组的展开,见传输层注释),调用方据此给出具体哪个名字没对上的提示,
    /// 不是笼统的「字段校验失败」。返回写入后的最终名单(与 GET 同形状)。
    @discardableResult
    func putIntelWatchBoards(_ boards: [String]) async throws -> IntelWatchBoards {
        let body = IntelWatchBoardsRequest(boards: boards)
        let data = try await put("/api/v1/settings/intel-boards", body: body)
        return IntelWatchBoards(boards: try JSONDecoder().decode(IntelWatchBoardsResponse.self, from: data).boards)
    }

    // —— 设备注册(iOS APNs token)——
    @discardableResult
    func registerDevice(token deviceToken: String, platform: String = "ios") async throws -> Bool {
        let body = DeviceRegisterRequest(token: deviceToken, platform: platform)
        let data = try await post("/api/v1/devices", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    // —— v1.1-C/F 自选池(watchlist)+ 同花顺 txt 对账/导出 ——————————————————————
    // 增删只经这几个端点(§C.1 拍板「系统代码路径绝不写入」),客户端也只在用户显式
    // 操作(+自选/删/pin/一键对齐)时调用,不存在任何自动触发路径。

    func fetchWatchlist() async throws -> WatchlistSnapshot {
        let data = try await get("/api/v1/watchlist")
        let r = try JSONDecoder().decode(WatchlistResponse.self, from: data)
        return WatchlistSnapshot(items: r.items, maxSize: r.maxSize)
    }

    /// 加一只自选。满 30 上限 → 422(`APIError.validation("watchlist_full")`,调用方据此
    /// 给出「自选池已满」提示,不是裸的「字段校验失败」)。
    @discardableResult
    func addWatchlist(code: String, name: String? = nil, note: String? = nil) async throws -> WatchlistItem {
        let body = WatchlistAddRequest(code: code, name: name, note: note)
        let data = try await post("/api/v1/watchlist", body: body)
        return try JSONDecoder().decode(WatchlistAddResponse.self, from: data).item
    }

    @discardableResult
    func removeWatchlist(code: String) async throws -> Bool {
        let data = try await delete("/api/v1/watchlist/\(code)")
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    @discardableResult
    func pinWatchlist(code: String, pinned: Bool) async throws -> Bool {
        let body = WatchlistPinRequest(pinned: pinned)
        let data = try await put("/api/v1/watchlist/\(code)/pin", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    /// 同花顺 txt 对账(单文件,字段名 `file`——**注意与下方 4D `uploadReview` 的字段名
    /// `files`〔复数〕不同**,严格对齐后端 `reconcile_ths(file: UploadFile = File(...))`)。
    /// 只算差集、不写入(对齐动作由客户端按差异结果调上面的 CRUD)。
    func reconcileThs(filename: String, data fileData: Data) async throws -> ThsReconcileResult {
        try ensureToken()
        guard let url = Self.makeURL(base: baseURL, path: "/api/v1/watchlist/reconcile-ths") else {
            throw APIError.transport("无效 URL")
        }
        let boundary = "NecklineBoundary-\(UUID().uuidString)"
        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append(
            "Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".data(using: .utf8)!
        )
        body.append("Content-Type: text/plain\r\n\r\n".data(using: .utf8)!)
        body.append(fileData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)

        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.httpBody = body
        req.timeoutInterval = 20
        let data = try await send(req)
        let r = try JSONDecoder().decode(ThsReconcileResponse.self, from: data)
        return ThsReconcileResult(onlyInThs: r.onlyInThs, onlyInNeckline: r.onlyInNeckline, both: r.both)
    }

    /// 导出当前自选为同花顺可导入 txt(§C.4/F.4)。
    func exportThs() async throws -> (text: String, count: Int) {
        let data = try await get("/api/v1/watchlist/export-ths")
        let r = try JSONDecoder().decode(ThsExportResponse.self, from: data)
        return (r.text, r.count)
    }

    // —— 4D 周复盘工作台(拖入交割单对账;macOS 独有,§五 阶段4D)——————————————————

    /// 上传一份或多份 xlsx 交割单 → 解析 → 对账(可能同时落多个 ISO 周)。解析/数据
    /// 完整性问题走 `parseWarnings`/`dataWarnings` 降级展示,不当异常抛(同后端契约)。
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
        req.timeoutInterval = 60   // 解析+对账可能稍慢(涉及面板计算),同问询台 60s 惯例
        let data = try await send(req)
        return try JSONDecoder().decode(ReviewUploadResponse.self, from: data)
    }

    /// 历史回放(带 query,务必走 makeURL,同 `fetchReport(date:)` 惯例)。
    func fetchReview(week: String) async throws -> ReviewGetResponse {
        let data = try await get("/api/v1/review?week=\(week)")
        return try JSONDecoder().decode(ReviewGetResponse.self, from: data)
    }

    // MARK: - 传输层

    /// 由 base + path(可含 "?query")构造 URL。**禁止 `appendingPathComponent`**——它把整个
    /// path(含 "?date=...")当单个路径组件、"?" 编码成 "%3F",带 query 的端点(`report?date=`)
    /// 真后端恒 404 且被静默吞(LinoN v1.3.0 致命坑,§五 阶段4C 坑吸收清单②)。用
    /// `URL(string:relativeTo:)` 让 "?" 正确解析为 query 分隔符。可单测(见 NecklineTests)。
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

    /// v1.1-F:`DELETE /watchlist/{code}` 用(无请求体)。
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
        // v1.4-①-A:400 走 reason 映射(`POST /positions` 的 buyDate 校验)。fallback 保持
        // 既有 `.server(400, …)` 语义 —— 未知 400 reason 不冒充成买入日错误。
        case 400:
            throw mapReason(data, fallback: .server(400, reasonString(data) ?? "请求不合法"))
        case 404:
            throw mapReason(data, fallback: .notHolding)
        case 422:
            throw APIError.validation(reasonString(data) ?? "请检查输入")
        default:
            throw APIError.server(http.statusCode, reasonString(data) ?? "未知错误")
        }
    }

    /// FastAPI 的 HTTPException(detail={ok:false, reason:...})落在 "detail" 里。
    private func mapReason(_ data: Data, fallback: APIError) -> APIError {
        guard let reason = reasonString(data) else { return fallback }
        switch reason {
        case "not_holding": return .notHolding
        // v1.4-⑦-A:GET /decisions/{id}/track 的 not_found(decision_id 不存在)复用
        // 本既有 case(字符串与 decisions link/cancel/revise 端点相同,未新增);
        // v1.4-⑦-B:GET /inquiries/{id} 的 not_found 同样复用本 case。
        case "not_found": return .notFound   // v1.1-F:watchlist delete/pin 代码不存在
        // v1.4-①-A:POST /positions 的 buyDate 校验(400),两个 reason 各一 case。
        case "not_trading_day": return .notTradingDay
        case "future_buy_date": return .futureBuyDate
        // v1.4-④-B:GET /report/{date}/info-card/{code} 的两个 404 reason。
        case "report_not_found": return .reportNotFound
        case "code_not_in_report": return .codeNotInReport
        // v1.4-⑤-B:POST/revise /decisions 的 maxChasePct 未显式传(400)。
        case "max_chase_required": return .maxChaseRequired
        default: return fallback
        }
    }

    private func reasonString(_ data: Data) -> String? {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        if let detail = obj["detail"] as? [String: Any], let r = detail["reason"] as? String {
            // v1.3-⑥:`PUT /settings/intel-boards` 422 额外带 `unresolved`(具体哪些板块名
            // 没精确匹配到)——**纯附加行为**,只在这个键存在时才拼接,不影响其它端点既有
            // `reason` 语义(如 `watchlist_full` 无此键,原样返回不变,既有
            // `.contains("watchlist_full")` 调用点不受影响)。拼接成
            // "board_not_found:名字1、名字2",调用方按前缀识别 + 取冒号后的展示文案。
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
