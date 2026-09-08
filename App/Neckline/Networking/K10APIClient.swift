import Foundation

protocol K10Servicing: Sendable {
    func health() async throws -> K10Health
    func latestScan(window: String) async throws -> K10Scan
    func publications() async throws -> [K10Publication]
    func companyWindows() async throws -> [K10CompanyWindow]
    func latestMorningReport() async throws -> K10MorningReport?
    func morningReports() async throws -> [K10MorningReport]
    func opportunity(id: String) async throws -> K10OpportunityDetail
    func act(companyWindowID: String, request: K10SelectionRequest) async throws -> K10SelectionAction
    func selections() async throws -> [K10SelectionDetail]
    func analysisChain(companyWindowID: String) async throws -> K10AnalysisChain
    func requestAnalysis(companyWindowID: String, request: K10AnalysisRequest) async throws -> K10AnalysisRequestResult
    func document(id: String, revision: Int?, offset: Int, limit: Int) async throws -> K10DocumentPage
    func job(id: String) async throws -> K10Job
    func retryJob(id: String, expectedAttemptCount: Int) async throws -> K10Job
    func results() async throws -> K10Results
    func configuration() async throws -> K10Configuration
    func operationsReadiness() async throws -> K10OperationsReadiness
    func usageSummary() async throws -> K10UsageSummary
}

extension K10Servicing {
    func latestMorningReport() async throws -> K10MorningReport? { nil }
    func morningReports() async throws -> [K10MorningReport] { [] }
    func analysisChain(companyWindowID: String) async throws -> K10AnalysisChain {
        throw K10APIError.notFound("尚无分析版本链")
    }
    func requestAnalysis(companyWindowID: String, request: K10AnalysisRequest) async throws -> K10AnalysisRequestResult {
        throw K10APIError.notFound("服务端尚未提供补充分析")
    }
    func operationsReadiness() async throws -> K10OperationsReadiness {
        throw K10APIError.notFound("服务端尚未提供运行状态")
    }
}

actor K10APIClient: K10Servicing {
    private let baseURL: URL; private let token: String; private let session: URLSession
    init(baseURL: URL, token: String, session: URLSession = .shared) { self.baseURL = baseURL; self.token = token; self.session = session }
    func health() async throws -> K10Health { try await get("/api/v1/health", authenticated: false) }
    func latestScan(window: String) async throws -> K10Scan { try await get("/api/v1/k10/scans/latest", query: [URLQueryItem(name: "window", value: window)]) }
    func publications() async throws -> [K10Publication] { try await allPages(path: "/api/v1/k10/publications", extra: [], as: K10PublicationList.self).items }
    func companyWindows() async throws -> [K10CompanyWindow] { try await allPages(path: "/api/v1/k10/company-windows", extra: [], as: K10CompanyWindowList.self).items }
    func latestMorningReport() async throws -> K10MorningReport? {
        do { return try await get("/api/v1/k10/morning-reports/latest") }
        catch let error as K10APIError { if case .notFound = error { return nil }; throw error }
    }
    func morningReports() async throws -> [K10MorningReport] { try await allPages(path: "/api/v1/k10/morning-reports", extra: [], as: K10MorningReportList.self).items }
    func opportunity(id: String) async throws -> K10OpportunityDetail { try await get("/api/v1/k10/opportunities/\(id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? id)") }
    func act(companyWindowID: String, request: K10SelectionRequest) async throws -> K10SelectionAction { try await post("/api/v1/k10/company-windows/\(companyWindowID.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? companyWindowID)/selection", body: request) }
    func selections() async throws -> [K10SelectionDetail] { try await allPages(path: "/api/v1/k10/selections", extra: [], as: K10SelectionList.self).items }
    func analysisChain(companyWindowID: String) async throws -> K10AnalysisChain { try await get("/api/v1/k10/company-windows/\(companyWindowID.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? companyWindowID)/analysis-chain") }
    func requestAnalysis(companyWindowID: String, request: K10AnalysisRequest) async throws -> K10AnalysisRequestResult { try await post("/api/v1/k10/company-windows/\(companyWindowID.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? companyWindowID)/analysis-requests", body: request) }
    func document(id: String, revision: Int?, offset: Int, limit: Int) async throws -> K10DocumentPage { var query = [URLQueryItem(name: "offset", value: String(offset)), URLQueryItem(name: "limit", value: String(limit))]; if let revision { query.append(URLQueryItem(name: "revision", value: String(revision))) }; return try await get("/api/v1/k10/documents/\(id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? id)", query: query) }
    func job(id: String) async throws -> K10Job { try await get("/api/v1/k10/jobs/\(id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? id)") }
    func retryJob(id: String, expectedAttemptCount: Int) async throws -> K10Job { try await post("/api/v1/k10/jobs/\(id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? id)/retry", body: ["expectedAttemptCount": expectedAttemptCount]) }
    func results() async throws -> K10Results { try await get("/api/v1/k10/results") }
    func configuration() async throws -> K10Configuration { try await get("/api/v1/k10/configuration") }
    func operationsReadiness() async throws -> K10OperationsReadiness { try await get("/api/v1/k10/operations/readiness") }
    func usageSummary() async throws -> K10UsageSummary { try await get("/api/v1/usage/summary") }

    private func allPages<Page: Decodable>(path: String, extra: [URLQueryItem], as _: Page.Type) async throws -> Page where Page: K10Paginated {
        var items = Page.emptyItems; var cursor: String?
        repeat { var query = extra + [URLQueryItem(name: "limit", value: "100")]; if let cursor { query.append(URLQueryItem(name: "cursor", value: cursor)) }; let page: Page = try await get(path, query: query); items += page.anyItems; cursor = page.nextCursor } while cursor != nil
        return Page.from(items: items)
    }
    private func get<T: Decodable>(_ path: String, query: [URLQueryItem] = [], authenticated: Bool = true) async throws -> T { try await request(path, method: "GET", query: query, body: Optional<Data>.none, authenticated: authenticated) }
    private func post<T: Decodable, Body: Encodable>(_ path: String, body: Body) async throws -> T { try await request(path, method: "POST", query: [], body: body, authenticated: true) }
    private func request<T: Decodable, Body: Encodable>(_ path: String, method: String, query: [URLQueryItem], body: Body?, authenticated: Bool) async throws -> T {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else { throw K10APIError.server(0, "服务地址无效") }
        components.path = path; components.queryItems = query.isEmpty ? nil : query
        guard let url = components.url else { throw K10APIError.server(0, "请求地址无效") }
        var request = URLRequest(url: url); request.httpMethod = method; request.timeoutInterval = 20; request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if authenticated { guard !token.isEmpty else { throw K10APIError.noToken }; request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let body { request.httpBody = try JSONEncoder().encode(body) }
        do { let (data, response) = try await session.data(for: request); guard let http = response as? HTTPURLResponse else { throw K10APIError.server(0, "服务未返回 HTTP 响应") }; guard 200..<300 ~= http.statusCode else { throw decodeError(data, status: http.statusCode) }; do { return try JSONDecoder().decode(T.self, from: data) } catch { throw K10APIError.decoding("服务响应无法按 K10-v1.4 契约读取") } } catch is CancellationError { throw CancellationError() } catch let error as URLError where error.code == .cancelled { throw CancellationError() } catch let error as K10APIError { throw error } catch { throw K10APIError.networkUnavailable(error.localizedDescription) }
    }
    private func decodeError(_ data: Data, status: Int) -> K10APIError { K10APIError.decodeServerFailure(data, status: status) }
}

private protocol K10Paginated { associatedtype Item; static var emptyItems: [Item] { get }; var anyItems: [Item] { get }; var nextCursor: String? { get }; static func from(items: [Item]) -> Self }
extension K10PublicationList: K10Paginated { static var emptyItems: [K10Publication] { [] }; var anyItems: [K10Publication] { items }; var nextCursor: String? { page.nextCursor }; static func from(items: [K10Publication]) -> K10PublicationList { .init(items: items, page: .init(nextCursor: nil)) } }
extension K10CompanyWindowList: K10Paginated { static var emptyItems: [K10CompanyWindow] { [] }; var anyItems: [K10CompanyWindow] { items }; var nextCursor: String? { page.nextCursor }; static func from(items: [K10CompanyWindow]) -> K10CompanyWindowList { .init(items: items, page: .init(nextCursor: nil)) } }
extension K10SelectionList: K10Paginated { static var emptyItems: [K10SelectionDetail] { [] }; var anyItems: [K10SelectionDetail] { items }; var nextCursor: String? { page.nextCursor }; static func from(items: [K10SelectionDetail]) -> K10SelectionList { .init(items: items, page: .init(nextCursor: nil)) } }
extension K10MorningReportList: K10Paginated { static var emptyItems: [K10MorningReport] { [] }; var anyItems: [K10MorningReport] { items }; var nextCursor: String? { page.nextCursor }; static func from(items: [K10MorningReport]) -> K10MorningReportList { .init(items: items, page: .init(nextCursor: nil)) } }
