// Q 期货看板接口请求。复用统一 Bearer 令牌与传输层，严格校验 schemaVersion == 3，未知版本不硬解。

import Foundation

enum QihuoBoardError: Error, Equatable {
    case missingToken
    case unauthorized
    case notConfigured
    case dbNotReady
    /// 503 reason == qihuo_unsupported_schema，或客户端收到非 3 的 schemaVersion。
    case unsupportedSchema(String?)
    /// 503 服务端明确不可用原因。
    case unavailable(String?)
    case unreachable
    case unreadable

    var isTransient: Bool {
        switch self {
        case .unreachable, .unreadable: return true
        default: return false
        }
    }
}

protocol QihuoBoardClient: Sendable {
    func board(forceRefresh: Bool) async throws -> QihuoBoard
}

extension QihuoBoardClient {
    func board() async throws -> QihuoBoard {
        try await board(forceRefresh: false)
    }
}

struct QihuoBoardAPI: QihuoBoardClient, Sendable {
    static let expectedSchemaVersion = 3

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

    func board(forceRefresh: Bool = false) async throws -> QihuoBoard {
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { throw QihuoBoardError.missingToken }
        var request = URLRequest(url: try endpoint(forceRefresh: forceRefresh))
        request.httpMethod = "GET"
        request.timeoutInterval = 30
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(trimmed)", forHTTPHeaderField: "Authorization")

        let data: Data
        let http: HTTPURLResponse
        do {
            (data, http) = try await transport.send(request)
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch let error as QihuoBoardError {
            throw error
        } catch {
            throw QihuoBoardError.unreachable
        }
        guard (200..<300).contains(http.statusCode) else { throw Self.failure(status: http.statusCode, data: data) }

        // 严格两段校验：先确认顶层是 JSON 对象且 schemaVersion 为精确整数 3；
        // 缺失、null、字符串、布尔或其他版本号均立即报 .unsupportedSchema，绝不落入可保留旧画面的 .unreadable。
        let topObj: Any
        do {
            topObj = try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
        } catch {
            throw QihuoBoardError.unreadable
        }
        guard let dict = topObj as? [String: Any],
              let version = Self.exactInt(dict["schemaVersion"]) else {
            throw QihuoBoardError.unsupportedSchema(
                "Q 期货协议版本缺失或无效（需要 schema_version=\(Self.expectedSchemaVersion)），未强行解析。"
            )
        }
        guard version == Self.expectedSchemaVersion else {
            throw QihuoBoardError.unsupportedSchema(
                "Q 期货协议版本暂不支持（收到 schema_version=\(version)，需要 \(Self.expectedSchemaVersion)），未强行解析。"
            )
        }

        let decoded: QihuoBoard
        do {
            decoded = try JSONDecoder().decode(QihuoBoard.self, from: data)
        } catch {
            throw QihuoBoardError.unreadable
        }
        return decoded
    }

    private static func exactInt(_ value: Any?) -> Int? {
        guard let num = value as? NSNumber else { return nil }
        if CFGetTypeID(num) == CFBooleanGetTypeID() { return nil }
        if CFNumberIsFloatType(num) { return nil }
        return num.intValue
    }

    private func endpoint(forceRefresh: Bool) throws -> URL {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw QihuoBoardError.unreachable
        }
        let prefix = components.path == "/" ? "" : components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let root = prefix.isEmpty ? "" : "/" + prefix
        components.path = root + "/api/v4/qihuo/board"
        components.queryItems = forceRefresh ? [URLQueryItem(name: "refresh", value: "1")] : nil
        guard let url = components.url else { throw QihuoBoardError.unreachable }
        return url
    }

    private static func failure(status: Int, data: Data) -> QihuoBoardError {
        if status == 401 { return .unauthorized }
        let body = try? JSONDecoder().decode(KXFailureBody.self, from: data)
        switch (status, body?.reason) {
        case (503, "v4_not_configured"): return .notConfigured
        case (503, "v4_db_not_ready"): return .dbNotReady
        case (503, "qihuo_unsupported_schema"): return .unsupportedSchema(body?.message)
        case (503, _): return .unavailable(body?.message)
        default: return .unreadable
        }
    }
}
