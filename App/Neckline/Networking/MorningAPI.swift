// 只请求 /api/v4。令牌只放在 Authorization 头，不进日志。

import Foundation

enum MorningClientError: Error, Equatable {
    case missingToken
    case unauthorized
    case noReport
    case noDocument
    case notConfigured
    case dbNotReady
    case unreachable
    case unreadable
}

protocol MorningTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

struct URLSessionMorningTransport: MorningTransport {
    var session: URLSession = .shared

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        return (data, http)
    }
}

struct MorningAPI: Sendable {
    var baseURL: URL
    var token: String
    var transport: any MorningTransport

    init(baseURL: URL, token: String, transport: any MorningTransport = URLSessionMorningTransport()) {
        self.baseURL = baseURL
        self.token = token
        self.transport = transport
    }

    @MainActor
    init(config: AppConfig, transport: any MorningTransport = URLSessionMorningTransport()) {
        self.init(baseURL: config.resolvedBaseURL, token: config.apiToken, transport: transport)
    }

    func health() async throws -> MorningHealth {
        try await get("/health", authenticated: false)
    }

    func latest() async throws -> MorningReport {
        try await get("/reports/latest")
    }

    func report(tradeDate: String) async throws -> MorningReport {
        try await get("/reports/\(Self.pathComponent(tradeDate))")
    }

    func history(limit: Int) async throws -> MorningHistory {
        try await get("/reports", query: [URLQueryItem(name: "limit", value: String(limit))])
    }

    func document(id: String) async throws -> MorningDocument {
        try await get("/documents/\(Self.pathComponent(id))")
    }

    func registerDevice(token: String) async throws -> MorningDeviceAck {
        let body = try JSONEncoder().encode(DeviceBody(token: token, platform: "ios"))
        return try await request("/devices", method: "POST", query: [], body: body, authenticated: true)
    }

    private struct DeviceBody: Encodable {
        let token: String
        let platform: String
    }

    private func get<T: Decodable>(_ path: String, query: [URLQueryItem] = [], authenticated: Bool = true) async throws -> T {
        try await request(path, method: "GET", query: query, body: nil, authenticated: authenticated)
    }

    private func request<T: Decodable>(_ path: String, method: String, query: [URLQueryItem], body: Data?, authenticated: Bool) async throws -> T {
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        if authenticated, trimmed.isEmpty { throw MorningClientError.missingToken }
        let url = try endpoint(path, query: query)
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 20
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if authenticated {
            request.setValue("Bearer \(trimmed)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = body
        }
        let data: Data
        let http: HTTPURLResponse
        do {
            (data, http) = try await transport.send(request)
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch let error as MorningClientError {
            throw error
        } catch {
            throw MorningClientError.unreachable
        }
        guard (200..<300).contains(http.statusCode) else { throw Self.failure(status: http.statusCode, data: data) }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw MorningClientError.unreadable
        }
    }

    private func endpoint(_ path: String, query: [URLQueryItem]) throws -> URL {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw MorningClientError.unreachable
        }
        let prefix = components.path == "/" ? "" : components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let root = prefix.isEmpty ? "" : "/" + prefix
        components.path = root + "/api/v4" + path
        components.queryItems = query.isEmpty ? nil : query
        guard let url = components.url else { throw MorningClientError.unreachable }
        return url
    }

    private static func pathComponent(_ value: String) -> String {
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove(charactersIn: "/")
        return value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value
    }

    private static func failure(status: Int, data: Data) -> MorningClientError {
        if status == 401 { return .unauthorized }
        let reason = (try? JSONDecoder().decode(MorningFailureBody.self, from: data))?.reason
        switch (status, reason) {
        case (404, "no_report"): return .noReport
        case (404, "no_document"): return .noDocument
        case (503, "v4_not_configured"): return .notConfigured
        case (503, "v4_db_not_ready"): return .dbNotReady
        default: return .unreadable
        }
    }
}
