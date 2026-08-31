//
//  AppConfig.swift
//  Neckline — 后端连接配置(baseURL + apiToken 可配)
//
//  **App 默认后端 = prod(https://nk.linotsai.top)**。
//  默认域名是客户端与现役契约的边界；改错会导致整份报告无法解码、页面全空。
//  dev(http://127.0.0.1:8002,本地 uvicorn)保留作可切换选项(设置屏「环境」picker /
//  手填 baseURLOverride);两环境同端口 8002。
//  ⚠ **`NK_BASE_URL_OVERRIDE` 压过本默认值**:老 App 若手填过 `ln` 基址,换包后仍会打老机
//  (override 优先级见 `resolvedBaseURL`)。换包后连不上时先去设置屏清空手填基址。
//
//  ⚠️ API_TOKEN 绝不硬编码进提交源码：
//   解析优先级 ——
//    1. Keychain（唯一持久化位置）
//    2. 构建期环境变量 NK_API_TOKEN(scheme 注入,本地开发用)
//    3. gitignored 本地配置 LocalSecrets.plist(若打进 bundle)
//   都缺则 token 为空 —— 业务端点会收 401,设置屏提示用户填。
//

import Foundation
import Security

enum NKEnvironment: String, CaseIterable, Identifiable {
    case dev      // 本地 uvicorn :8002
    case prod     // nk.linotsai.top(NB 现行入口 114.66.2.205,NPM 反代 → 8002)
    var id: String { rawValue }

    var baseURL: URL {
        switch self {
        case .dev:  return URL(string: "http://127.0.0.1:8002")!
        case .prod: return URL(string: "https://nk.linotsai.top")!
        }
    }

    var label: String {
        switch self {
        case .dev:  return "Dev · 127.0.0.1:8002"
        case .prod: return "Prod · nk.linotsai.top"
        }
    }

    /// 分段控件里的**短名**(macOS 原型 1623–1624 行两段就写 `Dev` / `Prod`)——
    /// 地址在它下面那一行「生效 baseURL」里完整给出,段内再重复一遍会把控件撑爆。
    var shortLabel: String {
        switch self {
        case .dev:  return "Dev"
        case .prod: return "Prod"
        }
    }
}

enum NKVersionCompatibility {
    static func message(serverVersion: String?, appVersion: String? = nil) -> String? {
        guard let serverVersion else { return nil }
        let server = normalized(serverVersion)
        let app = normalized(appVersion ?? (
            Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        ))
        guard !server.isEmpty, !app.isEmpty, server != app else { return nil }
        guard let order = compare(server, app) else {
            return "服务端为 v\(server)，当前 App 为 \(app)；版本不一致，请核对发布套件。"
        }
        // `2.7` 与 `2.7.0` 是同一个语义版本，不应仅因书写位数不同而阻断使用。
        if order == 0 { return nil }
        if order < 0 {
            return "服务端仍是 v\(server)，当前 App 为 \(app)；请先部署服务端。"
        }
        return "服务端已是 v\(server)，当前 App 为 \(app)；请更新 App。"
    }

    private static func normalized(_ value: String) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.lowercased().hasPrefix("v") ? String(trimmed.dropFirst()) : trimmed
    }

    private static func compare(_ lhs: String, _ rhs: String) -> Int? {
        let left = lhs.split(separator: ".").map(String.init)
        let right = rhs.split(separator: ".").map(String.init)
        guard left.allSatisfy({ Int($0) != nil }), right.allSatisfy({ Int($0) != nil }) else { return nil }
        for index in 0..<max(left.count, right.count) {
            let l = index < left.count ? Int(left[index])! : 0
            let r = index < right.count ? Int(right[index])! : 0
            if l != r { return l < r ? -1 : 1 }
        }
        return 0
    }
}

protocol APIAccessTokenStore {
    func load() -> String?
    @discardableResult func save(_ token: String) -> Bool
}

struct KeychainAPIAccessTokenStore: APIAccessTokenStore {
    func load() -> String? { TokenKeychain.load() }
    func save(_ token: String) -> Bool { TokenKeychain.save(token) }
}

/// 运行期可配置的后端连接。Token 只进 Keychain，绝不持久化到 UserDefaults。
@MainActor
final class AppConfig: ObservableObject {
    static let envKey = "NK_ENVIRONMENT"
    static let tokenKey = "NK_API_TOKEN"
    static let baseOverrideKey = "NK_BASE_URL_OVERRIDE"

    /// 持久化后端(生产 = `.standard`;单测注入隔离 suite 保证 hermetic,不吃模拟器
    /// 里前几次会话残留的 `NK_ENVIRONMENT`)。
    private let defaults: UserDefaults
    private let tokenStore: any APIAccessTokenStore

    @Published var environment: NKEnvironment {
        didSet { defaults.set(environment.rawValue, forKey: Self.envKey) }
    }
    /// 手填覆盖 baseURL(可选;空则用 environment.baseURL)
    @Published var baseURLOverride: String {
        didSet { defaults.set(baseURLOverride, forKey: Self.baseOverrideKey) }
    }
    @Published var apiToken: String {
        didSet {
            if tokenStore.save(apiToken) {
                defaults.removeObject(forKey: Self.tokenKey)
            }
        }
    }

    init(defaults: UserDefaults = .standard,
         tokenStore: any APIAccessTokenStore = KeychainAPIAccessTokenStore()) {
        self.defaults = defaults
        self.tokenStore = tokenStore
        // 默认后端 = prod(https://nk.linotsai.top,V2-⑰ 割接后的新机)。无持久化选择时用 prod;
        // dev(本地 uvicorn 8002)仍可在设置屏「环境」picker 或手填 baseURLOverride 切换,配置
        // 能力不变。⚠ 老 App 存过的 `NK_ENVIRONMENT="prod"` 在这里会被读成 **新** prod = nk,
        // 这正是换包要的效果(同一个 rawValue,指向随版本换血)。
        self.environment = NKEnvironment(rawValue: defaults.string(forKey: Self.envKey) ?? "") ?? .prod
        self.baseURLOverride = defaults.string(forKey: Self.baseOverrideKey) ?? ""

        // 一次性迁移旧 UserDefaults；Keychain 成功落入后才删除旧值。
        if let t = tokenStore.load(), !t.isEmpty {
            self.apiToken = t
        } else if let legacy = defaults.string(forKey: Self.tokenKey), !legacy.isEmpty {
            self.apiToken = legacy
            if tokenStore.save(legacy) {
                defaults.removeObject(forKey: Self.tokenKey)
            }
        } else if let env = ProcessInfo.processInfo.environment["NK_API_TOKEN"], !env.isEmpty {
            self.apiToken = env
        } else if let plistToken = Self.tokenFromLocalPlist() {
            self.apiToken = plistToken
        } else {
            self.apiToken = ""
        }
    }

    var resolvedBaseURL: URL {
        let trimmed = baseURLOverride.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty, let u = URL(string: trimmed), isAllowedOverride(u) { return u }
        if !trimmed.isEmpty { return URL(string: "https://configuration.invalid")! }
        return environment.baseURL
    }

    var connectionConfigurationError: String? {
        let trimmed = baseURLOverride.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        guard let url = URL(string: trimmed) else { return "服务地址格式不正确" }
        return isAllowedOverride(url) ? nil : "远端服务必须使用 HTTPS；本地开发仅允许 Debug 的 127.0.0.1/localhost"
    }

    var effectiveServiceLabel: String {
        let trimmed = baseURLOverride.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return environment.label }
        guard connectionConfigurationError == nil else { return "临时地址无效" }
        let address = resolvedBaseURL.host.map { host in
            resolvedBaseURL.port.map { "\(host):\($0)" } ?? host
        } ?? resolvedBaseURL.absoluteString
        return "临时 · \(address)"
    }

    var hasToken: Bool { !apiToken.trimmingCharacters(in: .whitespaces).isEmpty }

    private func isAllowedOverride(_ url: URL) -> Bool {
        if url.scheme?.lowercased() == "https" { return true }
        #if DEBUG
        let host = url.host?.lowercased()
        return url.scheme?.lowercased() == "http" && (host == "127.0.0.1" || host == "localhost")
        #else
        return false
        #endif
    }

    /// gitignored 本地配置:Bundle 内 LocalSecrets.plist 的 NK_API_TOKEN 键。
    private static func tokenFromLocalPlist() -> String? {
        guard let url = Bundle.main.url(forResource: "LocalSecrets", withExtension: "plist"),
              let dict = NSDictionary(contentsOf: url),
              let token = dict["NK_API_TOKEN"] as? String,
              !token.isEmpty else { return nil }
        return token
    }
}

private enum TokenKeychain {
    private static let service = "top.linotsai.neckline"
    private static let account = "api-token"

    static func load() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data,
              let token = String(data: data, encoding: .utf8) else { return nil }
        return token
    }

    @discardableResult
    static func save(_ token: String) -> Bool {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        if token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return SecItemDelete(query as CFDictionary) == errSecSuccess || load() == nil
        }
        let attributes: [String: Any] = [kSecValueData as String: Data(token.utf8)]
        let update = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if update == errSecSuccess { return true }
        if update != errSecItemNotFound { return false }
        var add = query
        add[kSecValueData as String] = Data(token.utf8)
        return SecItemAdd(add as CFDictionary, nil) == errSecSuccess
    }
}
