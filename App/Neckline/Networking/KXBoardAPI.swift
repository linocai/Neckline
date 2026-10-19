// KX 看板请求。和晨报同一套 Bearer 令牌与传输层，错误单独定义，不混进晨报的错误。

import Foundation

enum KXBoardError: Error, Equatable {
    case missingToken
    case unauthorized
    case notConfigured
    case dbNotReady
    /// KX 名单从来没同步成功过。附服务端写明的原因。
    case poolUnverified(String?)
    /// 交易日历没有覆盖今天。附服务端写明的原因。
    case calendarGap(String?)
    case unreachable
    case unreadable

    /// 连不上、读不了是暂时的：已经有数据时保留旧数据。其余是服务端明确说「现在出不了看板」。
    var isTransient: Bool {
        switch self {
        case .unreachable, .unreadable: return true
        default: return false
        }
    }
}

protocol KXBoardClient: Sendable {
    func board() async throws -> KXBoard
}

struct KXBoardAPI: KXBoardClient, Sendable {
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

    func board() async throws -> KXBoard {
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { throw KXBoardError.missingToken }
        var request = URLRequest(url: try endpoint())
        request.httpMethod = "GET"
        // 服务端冷启动时要同步名单、取行情和日线，比晨报慢。
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
        do {
            return try JSONDecoder().decode(KXBoard.self, from: data)
        } catch {
            throw KXBoardError.unreadable
        }
    }

    private func endpoint() throws -> URL {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw KXBoardError.unreachable
        }
        let prefix = components.path == "/" ? "" : components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let root = prefix.isEmpty ? "" : "/" + prefix
        components.path = root + "/api/v4/kx/board"
        components.queryItems = nil
        guard let url = components.url else { throw KXBoardError.unreachable }
        return url
    }

    private static func failure(status: Int, data: Data) -> KXBoardError {
        if status == 401 { return .unauthorized }
        let body = try? JSONDecoder().decode(KXFailureBody.self, from: data)
        switch (status, body?.reason) {
        case (503, "v4_not_configured"): return .notConfigured
        case (503, "v4_db_not_ready"): return .dbNotReady
        case (503, "pool_unverified"): return .poolUnverified(body?.message)
        case (503, "calendar_gap"): return .calendarGap(body?.message)
        default: return .unreadable
        }
    }
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
