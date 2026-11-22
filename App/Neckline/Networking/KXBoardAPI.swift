// KX V4 看板请求。和晨报同一套 Bearer 令牌与传输层，只认 schema_version == 5，未知版本不硬解。

import Foundation

enum KXBoardError: Error, Equatable {
    case missingToken
    case unauthorized
    case notConfigured
    case dbNotReady
    /// 503，reason 为 kx_unsupported_schema，或客户端收到非 5 的 schemaVersion。
    case unsupportedSchema(String?)
    /// 503，reason 为 kx_unavailable 等服务端明确原因。
    case unavailable(String?)
    case unreachable
    case unreadable

    /// 连不上、读不了是暂时的：已经有数据时保留旧数据并提示刷新失败。
    var isTransient: Bool {
        switch self {
        case .unreachable, .unreadable: return true
        default: return false
        }
    }
}

protocol KXBoardClient: Sendable {
    func board(forceRefresh: Bool) async throws -> KXBoard
}

extension KXBoardClient {
    func board() async throws -> KXBoard {
        try await board(forceRefresh: false)
    }
}

struct KXBoardAPI: KXBoardClient, Sendable {
    static let expectedSchemaVersion = 5

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

    func board(forceRefresh: Bool = false) async throws -> KXBoard {
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { throw KXBoardError.missingToken }
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
        } catch let error as KXBoardError {
            throw error
        } catch {
            throw KXBoardError.unreachable
        }
        guard (200..<300).contains(http.statusCode) else { throw Self.failure(status: http.statusCode, data: data) }
        // 两段解码：先只读 schemaVersion，再解整板，避免未来 schema 6 字段变化时误报 .unreadable
        if let probe = try? JSONDecoder().decode(KXSchemaProbe.self, from: data),
           let version = probe.schemaVersion,
           version != Self.expectedSchemaVersion {
            throw KXBoardError.unsupportedSchema(
                "KX 看板协议版本暂不支持（收到 schema_version=\(version)，需要 \(Self.expectedSchemaVersion)），未强行解析。"
            )
        }
        let decoded: KXBoard
        do {
            decoded = try JSONDecoder().decode(KXBoard.self, from: data)
        } catch {
            throw KXBoardError.unreadable
        }
        guard decoded.schemaVersion == Self.expectedSchemaVersion else {
            throw KXBoardError.unsupportedSchema(
                "KX 看板协议版本暂不支持（收到 schema_version=\(decoded.schemaVersion)，需要 \(Self.expectedSchemaVersion)），未强行解析。"
            )
        }
        return decoded
    }

    private func endpoint(forceRefresh: Bool) throws -> URL {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw KXBoardError.unreachable
        }
        let prefix = components.path == "/" ? "" : components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let root = prefix.isEmpty ? "" : "/" + prefix
        components.path = root + "/api/v4/kx/board"
        components.queryItems = forceRefresh ? [URLQueryItem(name: "refresh", value: "1")] : nil
        guard let url = components.url else { throw KXBoardError.unreachable }
        return url
    }

    private static func failure(status: Int, data: Data) -> KXBoardError {
        if status == 401 { return .unauthorized }
        let body = try? JSONDecoder().decode(KXFailureBody.self, from: data)
        switch (status, body?.reason) {
        case (503, "v4_not_configured"): return .notConfigured
        case (503, "v4_db_not_ready"): return .dbNotReady
        case (503, "kx_unsupported_schema"): return .unsupportedSchema(body?.message)
        case (503, _): return .unavailable(body?.message)
        default: return .unreadable
        }
    }
}

/// 只探测协议版本的最小结构，用于在字段结构变化时先给出明确的协议不支持提示。
private struct KXSchemaProbe: Decodable {
    let schemaVersion: Int?
}

/// 503 的正文：`{"detail": {"reason": ..., "message": ...}}`。detail 是字符串时两项都没有。
struct KXFailureBody: Decodable, Equatable {
    let reason: String?
    let message: String?

    private enum Outer: String, CodingKey { case detail }
    private enum Inner: String, CodingKey { case reason, message }

    init(from decoder: Decoder) throws {
        let outer = try decoder.container(keyedBy: Outer.self)
        guard let inner = try? outer.nestedContainer(keyedBy: Inner.self, forKey: .detail) else {
            reason = nil
            message = nil
            return
        }
        reason = try inner.decodeIfPresent(String.self, forKey: .reason)
        message = try inner.decodeIfPresent(String.self, forKey: .message)
    }
}
