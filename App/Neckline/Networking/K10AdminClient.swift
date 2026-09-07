import Foundation

protocol K10AdminServicing: Sendable {
    func providers() async throws -> [K10Provider]
    func createProvider(_ provider: K10ProviderCreate) async throws -> K10Provider
    func updateProvider(name: String, _ provider: K10ProviderUpdate) async throws -> K10Provider
    func tavilyStatus() async throws -> K10TavilyStatus
    func setTavilyKey(_ key: String) async throws -> K10TavilyStatus
    func clearTavilyKey() async throws
    func registerDevice(token: String) async throws
}

/// Stable generic settings endpoints. Secrets are write-only and never cached or returned.
actor K10AdminClient: K10AdminServicing {
    private let baseURL: URL
    private let token: String
    private let session: URLSession

    init(baseURL: URL, token: String, session: URLSession = .shared) {
        self.baseURL = baseURL; self.token = token; self.session = session
    }

    func providers() async throws -> [K10Provider] { let page: K10ProviderList = try await request("/api/v1/settings/providers", method: "GET", body: Optional<Data>.none); return page.items }
    func createProvider(_ provider: K10ProviderCreate) async throws -> K10Provider { try await request("/api/v1/settings/providers", method: "POST", body: provider) }
    func updateProvider(name: String, _ provider: K10ProviderUpdate) async throws -> K10Provider { try await request("/api/v1/settings/providers/\(name.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? name)", method: "PUT", body: provider) }
    func tavilyStatus() async throws -> K10TavilyStatus {
        struct Snapshot: Codable { let tavily: K10TavilyStatus }
        let snapshot: Snapshot = try await request("/api/v1/settings", method: "GET", body: Optional<Data>.none)
        return snapshot.tavily
    }
    func setTavilyKey(_ key: String) async throws -> K10TavilyStatus { try await request("/api/v1/settings/tavily", method: "PUT", body: K10TavilyUpdate(apiKey: key)) }
    func clearTavilyKey() async throws { let _: K10TavilyStatus = try await request("/api/v1/settings/tavily", method: "DELETE", body: Optional<Data>.none) }
    func registerDevice(token: String) async throws { let _: K10OK = try await request("/api/v1/devices", method: "POST", body: K10DeviceRegistration(token: token, platform: "ios")) }

    private func request<T: Decodable, Body: Encodable>(_ path: String, method: String, body: Body?) async throws -> T {
        guard !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw K10APIError.noToken }
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else { throw K10APIError.server(0, "服务地址无效") }
        components.path = path
        guard let url = components.url else { throw K10APIError.server(0, "请求地址无效") }
        var request = URLRequest(url: url); request.httpMethod = method; request.timeoutInterval = 15
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body { request.httpBody = try JSONEncoder().encode(body); request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw K10APIError.server(0, "服务未返回 HTTP 响应") }
            guard 200..<300 ~= http.statusCode else {
                throw K10APIError.decodeServerFailure(data, status: http.statusCode)
            }
            do { return try JSONDecoder().decode(T.self, from: data) } catch { throw K10APIError.decoding("服务响应无法按设置契约读取") }
        } catch let error as K10APIError { throw error }
        catch { throw K10APIError.networkUnavailable(error.localizedDescription) }
    }
}
