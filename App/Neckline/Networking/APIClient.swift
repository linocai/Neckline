//
//  APIClient.swift
//  Neckline — 后端 REST 客户端(FastAPI)。**V2.5.0 S12 契约换血**。
//
//  端点契约见 `neckline/api/schemas.py` + `neckline/api/app.py`(逐字段对齐,⛔ 别猜)。
//  🔴 **机器判据 = `Backend/tests/test_contract_crosscheck.py`**:
//  「**客户端调用面 ⊆ 服务端路由面**」用 `==` 断言(不是 `<=`)——
//  往这里加一条打不到的调用会当场红。⛔ 别把已删端点接回来。
//
//    GET  /api/v1/health                             → 免鉴权,{status, version}
//
//    —— 选股(S7 / S9 / S10)——
//    GET  /api/v1/selection/latest                   → 三态 + 双日期 + 清单 + 逐只摘要
//    GET  /api/v1/selection/{tradeDate}              → 同上,按**交易日**查历史
//    GET  /api/v1/selection/{tradeDate}/stock/{code} → 解释层资料 + 日K评价 + 全部预案版本
//    POST /api/v1/selection/{tradeDate}/stock/{code}/playbook → 用户改预案(append-only)
//
//    —— 次日核对(S8)——
//    GET  /api/v1/checklist/{tradeDate}              → **两段**;404 = 那天没跑过那一拍
//
//    —— 成绩(S4 / S8)——
//    GET  /api/v1/scoreboard/coverage?window=        → 覆盖率 + 漏检归因
//    GET  /api/v1/scoreboard/verdicts/{tradeDate}    → 10:00 结算拍的三分支终值
//
//    —— 复盘(S11)——
//    POST /api/v1/review/upload                      → 交割单上传(multipart)
//    GET  /api/v1/review?week=                       → 该周解析结果 + 我的成绩
//    GET  /api/v1/review/bindery?week=               → 行情材料装订
//    POST /api/v1/review/conclusions                 → 存一版结论(append-only)
//    GET  /api/v1/review/conclusions?week=|q=        → 读回 / 检索
//    GET  /api/v1/review/overview?week=              → 聚合读(**恒 200**,各段自带 available)
//
//    —— 设置 ——
//    GET  /api/v1/settings · GET|POST /settings/providers · PUT|DELETE /settings/providers/{name}
//    GET|PUT /settings/llm-routes · PUT|DELETE /settings/tavily · PUT /settings/push
//    PUT  /settings/review-col-map · POST /devices
//
//  鉴权:`Authorization: Bearer <API_TOKEN>`(health 外全部)。
//

import Foundation

// MARK: - 错误类型(结构化 reason,UI 据此弹提示)

enum APIError: Error, LocalizedError, Equatable {
    case unauthorized
    /// 404 通用「未找到」。🔴 **V2.5.0 起它是 404 的唯一 fallback**,
    /// 并且**带上服务端那句 detail 原文**(「20260430 没有报告」/「600001.SH 不在清单里」)。
    /// ⛔ 别再用一个具体业务错误当 404 的 fallback:上一版那个 fallback 是
    /// 「该持仓已清或不存在」,持仓整块下线之后,任何一条 K9 的 404 都会显示成那句
    /// 驴唇不对马嘴的话(v1.4 `watchlist` 与 V2 `card_not_ready` 已经踩过两次)。
    case notFound(String)
    // —— 设置屏的六个 reason(**服务端本版 reason 面就这六条**,
    //    唯一源见 `tests/test_contract_crosscheck.py::SERVER_REASONS`)——
    case alreadyExists       // 409 POST /settings/providers 同名 provider
    case invalidTask         // 422 PUT /settings/llm-routes 未知任务名
    case invalidProvider     // 422 默认 / 任务路由指向不可用 Provider
    case invalidTavilyKey    // 422 Tavily key 为空白
    case invalidPushKinds    // 422 PUT /settings/push 缺键 / 未登记 kind
    case validation(String)  // 422 其它字段校验(⚠ 预案改值的键集校验走这条)
    case server(Int, String)
    /// URLSession 明确报告的传输层不可达；这是唯一允许回看本地报告快照的失败类别。
    case networkUnavailable(String)
    case transport(String)
    case noToken

    var errorDescription: String? {
        switch self {
        case .unauthorized:     return "鉴权失败(检查 API Token)"
        case .notFound(let m):  return m.isEmpty ? "服务端没有这条记录" : m
        case .alreadyExists:    return "同名 Provider 已存在(请改用「编辑」)"
        case .invalidTask:      return "路由表里有未知的任务名"
        case .invalidProvider:  return "只能选择已启用且 key 已配置的 Provider"
        case .invalidTavilyKey: return "Tavily API key 不能为空"
        case .invalidPushKinds: return "推送开关清单不完整或含未登记的通知类型"
        case .validation(let m): return "字段校验失败:\(m)"
        case .server(let c, let m): return "服务端错误 \(c):\(m)"
        case .networkUnavailable(let m): return "网络暂不可用:\(m)"
        case .transport(let m): return "网络错误:\(m)"
        case .noToken:          return "未配置 API Token · 去设置填入"
        }
    }

    /// 这次失败是不是「服务端说那天没有 / 那只不在清单里」这类**合法空态**。
    /// 调用方据此走空态而不是弹错(⛔ 别把「没有」讲成「故障」)。
    var isNotFound: Bool { if case .notFound = self { return true }; return false }

    /// 离线快照只为「服务无法到达」而准备。HTTP 状态、鉴权、配置、解码失败都必须
    /// 原样曝光，不能用旧报告掩盖新的服务端事实。
    var permitsOfflineSelectionSnapshot: Bool {
        if case .networkUnavailable = self { return true }
        return false
    }
}

// MARK: - 请求体(只写不回显的字段一律单向)

/// `PUT /settings/push`:**全量覆盖式**写。`kinds` 必须给全服务端 `ALL_KINDS` 的每一个键
/// (缺键 / 未登记 kind → 422 `invalid_push_kinds`)—— 静默忽略会让用户以为自己关掉了
/// 某类通知而服务端根本没收到。
struct SettingsPushRequest: Encodable { let kinds: [String: Bool] }

struct SettingsReviewColMapRequest: Encodable { let colMap: [String: String] }

/// 新建 Provider。`apiKey` **只发一次、不回显、不落日志**;`name` 已存在 → 409。
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

/// 局部更新:**未出现的字段不改**(服务端 `model_fields_set` 判据)。故一律 `Optional` +
/// 合成 `Encodable`(nil → 该键不出现),**刻意**不手写 `encode(to:)`。
struct ProviderUpdateRequest: Encodable {
    let baseUrl: String?
    let model: String?
    let apiKey: String?
    let hasWebSearch: Bool?
    let searchEngine: String?
    let notes: String?
    let enabled: Bool?
}

/// 路由表:**全量覆盖式**写(调用方须传完整状态)。
struct LLMRoutesRequest: Encodable {
    let routes: [String: String]
    let defaultProvider: String?
}

struct TavilySettingsRequest: Encodable { let apiKey: String }

struct DeviceRegisterRequest: Encodable { let token: String; let platform: String }

/// 存一版复盘结论(append-only:每存一次 = 新版本,⛔ 老版本一个字不动)。
struct ReviewConclusionRequest: Encodable {
    let week: String
    let title: String
    let body: String
    let tags: [String]
    let author: String
}

private struct HealthResponse: Decodable { let status: String; let version: String? }
private struct OkResponse: Decodable { let ok: Bool }
private struct ProvidersListResponse: Decodable { let items: [Provider] }

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

    // ══════════════════════════════════════════════════════════════════════
    // 选股
    // ══════════════════════════════════════════════════════════════════════

    /// 最近一份报告。⚠ **库里一份都没有也是 200**(`state='not_run'` 的空态)——
    /// ⛔ 别把它当错误,那是一个正常的可读结论。
    func fetchSelectionLatest() async throws -> SelectionSnapshot {
        let data = try await get("/api/v1/selection/latest")
        return try JSONDecoder().decode(SelectionSnapshot.self, from: data)
    }

    /// 按**交易日**查历史报告(⚠ 不是发布日 —— 双日期契约里它才是审计键)。
    /// 404 = 那天没生成过报告(合法空态,走 `APIError.notFound`)。
    func fetchSelection(tradeDate: String) async throws -> SelectionSnapshot {
        let data = try await get("/api/v1/selection/\(tradeDate)")
        return try JSONDecoder().decode(SelectionSnapshot.self, from: data)
    }

    /// 个股详情。**⚠ 走 LLM 产物但只读库**,不重;404 = 这只票不在那天的清单里。
    func fetchStockDetail(tradeDate: String, code: String) async throws -> K9StockDetail {
        let data = try await get("/api/v1/selection/\(tradeDate)/stock/\(code)", timeout: 30)
        return try JSONDecoder().decode(K9StockDetail.self, from: data)
    }

    /// **用户修改预案**(K9 §6.4「最终确认由我盘后逐只过目,可修改」)。
    ///
    /// 🔴 **append-only**:服务端只新增一个版本,原冻结版本一个字不改。
    /// 🔴 **请求体只收数值**,键集 = 服务端下发的 `playbookSlots` 的 `key` 全集 ——
    /// 多一个键 / 少一个键 / 值不是数字 → **422**(服务端⛔ 不做「忽略多余的」)。
    /// 422 的 detail 里服务端会把该形态要的键逐个列出来,界面**原样**说出口。
    func saveStockPlaybook(tradeDate: String, code: String,
                           values: [String: Double]) async throws -> PlaybookSaveResult {
        let data = try await post("/api/v1/selection/\(tradeDate)/stock/\(code)/playbook",
                                  body: values)
        return try JSONDecoder().decode(PlaybookSaveResult.self, from: data)
    }

    // ══════════════════════════════════════════════════════════════════════
    // 次日核对(⛔ 响应体里没有「成立」这个取值,裁定 10)
    // ══════════════════════════════════════════════════════════════════════

    /// 9:29 竞价核对表。**404 = 那天没跑过那一拍**(合法空态:还没到 9:26,
    /// 或那天根本没有清单)—— ⛔ 别弹成错误,也 ⛔ 别画一张空表。
    func fetchChecklist(tradeDate: String) async throws -> Checklist {
        let data = try await get("/api/v1/checklist/\(tradeDate)")
        return try JSONDecoder().decode(Checklist.self, from: data)
    }

    // ══════════════════════════════════════════════════════════════════════
    // 成绩
    // ══════════════════════════════════════════════════════════════════════

    /// 覆盖率 + 漏检归因。**这条线不读任何待标定参数**,上线首日就出数。
    /// ⚠ 响应里的 `coverageAll` / `coverageInPool` 可能是 **null** —— ⛔ 不许当 0。
    func fetchCoverage(window: Int? = nil) async throws -> CoverageSnapshot {
        let path = "/api/v1/scoreboard/coverage" + (window.map { "?window=\($0)" } ?? "")
        let data = try await get(path, timeout: 30)
        return try JSONDecoder().decode(CoverageSnapshot.self, from: data)
    }

    /// 已经走完 D+4 的清单成绩。行业分与选票分由服务端分别给出，不存在合计分。
    func fetchListingScorecard(window: Int? = nil) async throws -> ListingScorecardSnapshot {
        let path = "/api/v1/scoreboard/listing" + (window.map { "?window=\($0)" } ?? "")
        let data = try await get(path, timeout: 30)
        return try JSONDecoder().decode(ListingScorecardSnapshot.self, from: data)
    }

    /// **10:00 结算拍的三分支终值**(裁定 10)。⚠ 一律 200(那天没有就是空数组)。
    func fetchVerdicts(tradeDate: String) async throws -> K9VerdictsSnapshot {
        let data = try await get("/api/v1/scoreboard/verdicts/\(tradeDate)")
        return try JSONDecoder().decode(K9VerdictsSnapshot.self, from: data)
    }

    func fetchUsageSummary(days: Int = 5) async throws -> UsageSummary {
        let data = try await get("/api/v1/usage/summary?days=\(max(1, min(days, 35)))")
        return try JSONDecoder().decode(UsageSummary.self, from: data)
    }

    // ══════════════════════════════════════════════════════════════════════
    // 复盘(架构 §六:解析 / 装订 / 存档,**这一层零 LLM**)
    // ══════════════════════════════════════════════════════════════════════

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
        req.timeoutInterval = 60   // 解析 + FIFO 对账稍慢
        let data = try await send(req)
        return try JSONDecoder().decode(ReviewUploadResponse.self, from: data)
    }

    /// 某周的解析结果 + 我的成绩。`week` = ISO 周键 `YYYY-Www`。
    func fetchReview(week: String) async throws -> ReviewGetResponse {
        let data = try await get("/api/v1/review?week=\(week)")
        return try JSONDecoder().decode(ReviewGetResponse.self, from: data)
    }

    /// 行情材料装订。⚠ **点一下才算**(它要读 parquet 行情)——⛔ 别放进每次进板块都拉的
    /// 聚合读里(§12 坑 1:重活进常驻服务 = 卡死不报错)。
    func fetchBindery(week: String) async throws -> ReviewBindery {
        let data = try await get("/api/v1/review/bindery?week=\(week)", timeout: 60)
        return try JSONDecoder().decode(ReviewBindery.self, from: data)
    }

    /// 存一版复盘结论(append-only)。
    func saveConclusion(week: String, title: String, body: String,
                        tags: [String]) async throws -> ReviewConclusionsResponse {
        let payload = ReviewConclusionRequest(week: week, title: title, body: body,
                                              tags: tags, author: "user")
        let data = try await post("/api/v1/review/conclusions", body: payload)
        return try JSONDecoder().decode(ReviewConclusionsResponse.self, from: data)
    }

    /// 读结论存档。传 `week` → 那一周的最新版 + 全部版本;否则按 `q` 检索。
    func fetchConclusions(week: String? = nil, query: String? = nil)
        async throws -> ReviewConclusionsResponse {
        var q: [String] = []
        if let w = week, !w.isEmpty { q.append("week=\(w)") }
        if let s = query, !s.isEmpty {
            q.append("q=\(s.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? s)")
        }
        let path = "/api/v1/review/conclusions" + (q.isEmpty ? "" : "?" + q.joined(separator: "&"))
        let data = try await get(path)
        return try JSONDecoder().decode(ReviewConclusionsResponse.self, from: data)
    }

    /// 复盘聚合读(**恒 200**,各段自带 `available`)。`week` = 该周任意一天 `YYYYMMDD`。
    func fetchReviewOverview(week: String? = nil) async throws -> ReviewOverview {
        let path = "/api/v1/review/overview" + ((week?.isEmpty == false) ? "?week=\(week!)" : "")
        let data = try await get(path, timeout: 30)
        return try JSONDecoder().decode(ReviewOverview.self, from: data)
    }

    // ══════════════════════════════════════════════════════════════════════
    // 设置(🔴 key 服务端存取,只写不回显)
    // ══════════════════════════════════════════════════════════════════════

    func fetchSettings() async throws -> SettingsSnapshot {
        let data = try await get("/api/v1/settings")
        return try JSONDecoder().decode(SettingsSnapshot.self, from: data)
    }

    func fetchProviders() async throws -> [Provider] {
        let data = try await get("/api/v1/settings/providers")
        return try JSONDecoder().decode(ProvidersListResponse.self, from: data).items
    }

    /// 新建 Provider。同名 → 409 `already_exists`(须显式走 `updateProvider`,防误覆盖)。
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

    /// 按 `kind` 的推送开关,**全量覆盖式**写。⛔ 客户端不许硬编 kind 清单 ——
    /// 传的是从 `GET /settings` 拿回来的那一份(服务端 `notify_kinds.py` 是唯一源)。
    @discardableResult
    func putSettingsPush(kinds: [String: Bool]) async throws -> Bool {
        let data = try await put("/api/v1/settings/push", body: SettingsPushRequest(kinds: kinds))
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    /// 交割单列映射(支持两家券商原始格式)。
    @discardableResult
    func putSettingsReviewColMap(_ colMap: [String: String]) async throws -> Bool {
        let body = SettingsReviewColMapRequest(colMap: colMap)
        let data = try await put("/api/v1/settings/review-col-map", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    @discardableResult
    func registerDevice(token deviceToken: String, platform: String = "ios") async throws -> Bool {
        let body = DeviceRegisterRequest(token: deviceToken, platform: platform)
        let data = try await post("/api/v1/devices", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    // MARK: - 传输层

    /// 由 base + path(可含 "?query")构造 URL。**禁止 `appendingPathComponent`** ——
    /// 它把整个 path(含 "?date=...")当单个路径组件、"?" 编码成 "%3F",带 query 的端点
    /// 真后端恒 404 且被静默吞(LinoN v1.3.0 致命坑)。
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

    private func post<B: Encodable>(_ path: String, body: B,
                                    timeout: TimeInterval = 12) async throws -> Data {
        try await write("POST", path, body: body, timeout: timeout)
    }

    private func put<B: Encodable>(_ path: String, body: B,
                                   timeout: TimeInterval = 12) async throws -> Data {
        try await write("PUT", path, body: body, timeout: timeout)
    }

    private func write<B: Encodable>(_ method: String, _ path: String, body: B,
                                     timeout: TimeInterval) async throws -> Data {
        try ensureToken()
        guard let url = Self.makeURL(base: baseURL, path: path) else {
            throw APIError.transport("无效 URL: \(path)")
        }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        req.timeoutInterval = timeout
        return try await send(req)
    }

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
        } catch let error as URLError {
            throw APIError.networkUnavailable(error.localizedDescription)
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
        case 400:
            throw mapReason(data, fallback: .server(400, reasonString(data) ?? "请求不合法"))
        // 🔴 **404 的 fallback 带上服务端原文**:K9 的四条端点返的是纯字符串 detail
        // (「20260430 没有报告」/「600001.SH 不在 20260430 的清单里」),那句话比任何
        // 客户端猜的文案都准。⛔ 别再拿某个具体业务错误当 404 的 fallback。
        case 404:
            throw mapReason(data, fallback: .notFound(reasonString(data) ?? ""))
        case 409:
            throw mapReason(data, fallback: .server(409, reasonString(data) ?? "资源冲突"))
        case 422:
            throw mapReason(data, fallback: .validation(reasonString(data) ?? "请检查输入"))
        case 500:
            throw mapReason(data, fallback: .server(500, reasonString(data) ?? "服务端内部错误"))
        default:
            throw APIError.server(http.statusCode, reasonString(data) ?? "未知错误")
        }
    }

    /// FastAPI 的 `HTTPException(detail={ok:false, reason:...})` 落在 "detail" 里。
    ///
    /// ⚠ **新增会返 4xx 的端点必须回来检查这里要不要加 case**(机器判据见
    /// `tests/test_contract_crosscheck.py::test_map_reason_covers_every_server_reason…`)。
    /// **复用已有 reason 字符串不算"没加"**,只有全新字符串才需要新 case。
    /// ⚠ 本版 reason 面只有六条,**全部出自设置屏** —— K9 的端点返纯字符串 detail,
    /// 它们走 `notFound(原文)` / `validation(原文)`,⛔ 不需要也不该有 reason 码。
    private func mapReason(_ data: Data, fallback: APIError) -> APIError {
        guard let reason = reasonString(data) else { return fallback }
        switch reason {
        case "not_found": return .notFound("未找到该记录(可能已被删除)")
        case "already_exists": return .alreadyExists
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
            // **纯附加行为**,只在这个键存在时才拼接。
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
