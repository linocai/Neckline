//
//  APIClient.swift
//  Neckline — 后端 REST 客户端(track 4A FastAPI,§五 阶段4A)
//
//  端点契约见 `neckline/api/schemas.py` + `neckline/api/app.py`(逐字段对齐,不猜):
//    GET  /api/v1/health                    → 免鉴权,{status,version}
//    GET  /api/v1/report/latest             → ReportOut
//    GET  /api/v1/report?date=YYYYMMDD      → ReportOut(历史回放;带 query,走 makeURL)
//    GET  /api/v1/board                     → BoardOut
//    GET  /api/v1/positions                 → PositionsOut{holdings}
//    POST /api/v1/positions                 → {ok,position_id,stop_line}       · 422 字段
//    POST /api/v1/positions/{id}/close      → {ok}                            · 404 not_holding
//    POST /api/v1/inquiry                   → InquiryOut(裁决二值,§2.5)
//    GET  /api/v1/settings                  → SettingsOut(key 只回布尔)
//    PUT  /api/v1/settings/llm              → {ok}                            · 422 供应商非法
//    PUT  /api/v1/settings/push             → {ok}
//    POST /api/v1/devices                   → {ok}
//  鉴权:Authorization: Bearer <API_TOKEN>(health 外全部)。
//

import Foundation

// MARK: - 错误类型(结构化 reason,UI 据此弹提示)

enum APIError: Error, LocalizedError, Equatable {
    case unauthorized
    case notHolding          // 404 该持仓已清或不存在
    case validation(String)  // 422 字段校验(含 provider 白名单)
    case server(Int, String)
    case transport(String)
    case noToken

    var errorDescription: String? {
        switch self {
        case .unauthorized:     return "鉴权失败(检查 API Token)"
        case .notHolding:       return "该持仓已清或不存在"
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
}
private struct OpenPositionResponse: Decodable {
    let ok: Bool
    let position_id: Int
    let stop_line: Double
}

struct ClosePositionRequest: Encodable {
    let sell_price: Double
    let sell_time: String?   // 'YYYYMMDD';缺省服务端用今日
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
}

private struct SettingsResponse: Decodable {
    let llmProvider: String?
    let llmKeySet: Bool
    let push: PushSettings
    let reviewColMap: [String: String]
}
struct SettingsLLMRequest: Encodable { let provider: String; let apiKey: String }
struct SettingsPushRequest: Encodable { let report: Bool; let retreatBrake: Bool }
struct SettingsReviewColMapRequest: Encodable { let colMap: [String: String] }

struct DeviceRegisterRequest: Encodable { let token: String; let platform: String }

private struct OkResponse: Decodable { let ok: Bool }

/// 无请求体 POST 占位({})。
private struct EmptyBody: Encodable {}

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

    // —— health(免鉴权,联通性自检)——
    func health() async throws -> Bool {
        guard let url = Self.makeURL(base: baseURL, path: "/api/v1/health") else {
            throw APIError.transport("无效 URL")
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else { return false }
        let obj = try? JSONDecoder().decode(HealthResponse.self, from: data)
        return obj?.status == "ok"
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
                              degraded: r.degraded, reason: r.reason)
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
    func openPosition(code: String, name: String?, buyPrice: Double, qty: Int,
                      entryReason: String) async throws -> (positionId: Int, stopLine: Double) {
        let body = OpenPositionRequest(code: code, name: name, buy_price: buyPrice,
                                       qty: qty, entry_reason: entryReason)
        let data = try await post("/api/v1/positions", body: body)
        let r = try JSONDecoder().decode(OpenPositionResponse.self, from: data)
        return (r.position_id, r.stop_line)
    }

    /// 清仓录入。`sellTime` 缺省 → 服务端用今日('YYYYMMDD')。
    @discardableResult
    func closePosition(id: Int, sellPrice: Double, sellTime: String? = nil) async throws -> Bool {
        let body = ClosePositionRequest(sell_price: sellPrice, sell_time: sellTime)
        let data = try await post("/api/v1/positions/\(id)/close", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
    }

    // —— 4A.5 问询台(§2.5:裁决二值,永不「现在就买」)——
    /// `messages` 为客户端持有的全部上下文(无状态端点,每次全量回传,继承 LinoN `/chat` 姿势)。
    func sendInquiry(code: String, messages: [ChatMessage]) async throws -> InquiryResult {
        let wire = messages.map { ChatMessageWire(role: $0.role.rawValue, content: $0.text) }
        let body = InquiryRequest(code: code, messages: wire)
        // LLM 段可能真联网搜索 + 降级重试(§3.4 _MAX_ATTEMPTS=3),给足长超时,同 LinoN chat 60s 模式。
        let data = try await post("/api/v1/inquiry", body: body, timeout: 60)
        let r = try JSONDecoder().decode(InquiryResponse.self, from: data)
        return InquiryResult(code: r.code, reply: r.reply, verdict: InquiryVerdict(r.verdict),
                             evidence: r.evidence, degraded: r.degraded)
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

    @discardableResult
    func putSettingsPush(report: Bool, retreatBrake: Bool) async throws -> Bool {
        let body = SettingsPushRequest(report: report, retreatBrake: retreatBrake)
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

    // —— 设备注册(iOS APNs token)——
    @discardableResult
    func registerDevice(token deviceToken: String, platform: String = "ios") async throws -> Bool {
        let body = DeviceRegisterRequest(token: deviceToken, platform: platform)
        let data = try await post("/api/v1/devices", body: body)
        return try JSONDecoder().decode(OkResponse.self, from: data).ok
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
        default: return fallback
        }
    }

    private func reasonString(_ data: Data) -> String? {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        if let detail = obj["detail"] as? [String: Any], let r = detail["reason"] as? String { return r }
        if let detailStr = obj["detail"] as? String { return detailStr }
        // 422 的 detail 是数组(pydantic v2 ValidationError 形状)
        if let arr = obj["detail"] as? [[String: Any]], let first = arr.first,
           let msg = first["msg"] as? String { return msg }
        return nil
    }
}
