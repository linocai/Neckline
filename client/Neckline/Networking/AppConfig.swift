//
//  AppConfig.swift
//  Neckline — 后端连接配置(baseURL + apiToken 可配)
//
//  dev 默认 http://127.0.0.1:8002(本地 uvicorn,与生产 hz ECS 8002 同端口,§3.6);
//  prod 复用 LinoN 域名 ln.linotsai.top(接班切换后 nginx upstream 指向 8002,§3.6
//  「接班切换」)——域名 `ln` 语义留痕是设计已接受的表面不符,勿"纠正"成 neckline 子域。
//
//  ⚠️ API_TOKEN 绝不硬编码进提交源码(plan 铁律):
//   解析优先级 ——
//    1. UserDefaults("NK_API_TOKEN")  ← App 内设置屏填入,或预置 plist
//    2. 构建期环境变量 NK_API_TOKEN(scheme 注入,本地开发用)
//    3. gitignored 本地配置 LocalSecrets.plist(若打进 bundle)
//   都缺则 token 为空 —— 业务端点会收 401,设置屏提示用户填。
//

import Foundation

enum NKEnvironment: String, CaseIterable, Identifiable {
    case dev      // 本地 uvicorn :8002
    case prod     // ln.linotsai.top(接班切换后指向 Neckline 8002)
    var id: String { rawValue }

    var baseURL: URL {
        switch self {
        case .dev:  return URL(string: "http://127.0.0.1:8002")!
        case .prod: return URL(string: "https://ln.linotsai.top")!
        }
    }

    var label: String {
        switch self {
        case .dev:  return "Dev · 127.0.0.1:8002"
        case .prod: return "Prod · ln.linotsai.top"
        }
    }
}

/// 运行期可配置的后端连接。持久化到 UserDefaults;token 不入源码。
@MainActor
final class AppConfig: ObservableObject {
    static let envKey = "NK_ENVIRONMENT"
    static let tokenKey = "NK_API_TOKEN"
    static let baseOverrideKey = "NK_BASE_URL_OVERRIDE"

    @Published var environment: NKEnvironment {
        didSet { UserDefaults.standard.set(environment.rawValue, forKey: Self.envKey) }
    }
    /// 手填覆盖 baseURL(可选;空则用 environment.baseURL)
    @Published var baseURLOverride: String {
        didSet { UserDefaults.standard.set(baseURLOverride, forKey: Self.baseOverrideKey) }
    }
    @Published var apiToken: String {
        didSet { UserDefaults.standard.set(apiToken, forKey: Self.tokenKey) }
    }

    init() {
        let defaults = UserDefaults.standard
        self.environment = NKEnvironment(rawValue: defaults.string(forKey: Self.envKey) ?? "") ?? .dev
        self.baseURLOverride = defaults.string(forKey: Self.baseOverrideKey) ?? ""

        // token 解析:UserDefaults → 环境变量 → 本地 plist
        if let t = defaults.string(forKey: Self.tokenKey), !t.isEmpty {
            self.apiToken = t
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
        if !trimmed.isEmpty, let u = URL(string: trimmed) { return u }
        return environment.baseURL
    }

    var hasToken: Bool { !apiToken.trimmingCharacters(in: .whitespaces).isEmpty }

    /// gitignored 本地配置:Bundle 内 LocalSecrets.plist 的 NK_API_TOKEN 键。
    private static func tokenFromLocalPlist() -> String? {
        guard let url = Bundle.main.url(forResource: "LocalSecrets", withExtension: "plist"),
              let dict = NSDictionary(contentsOf: url),
              let token = dict["NK_API_TOKEN"] as? String,
              !token.isEmpty else { return nil }
        return token
    }
}
