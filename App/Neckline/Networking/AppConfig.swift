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
//    1. UserDefaults("NK_API_TOKEN")  ← App 内设置屏填入,或预置 plist
//    2. 构建期环境变量 NK_API_TOKEN(scheme 注入,本地开发用)
//    3. gitignored 本地配置 LocalSecrets.plist(若打进 bundle)
//   都缺则 token 为空 —— 业务端点会收 401,设置屏提示用户填。
//

import Foundation

enum NKEnvironment: String, CaseIterable, Identifiable {
    case dev      // 本地 uvicorn :8002
    case prod     // nk.linotsai.top(V2 新机 114.66.0.38,NPM 反代 → 8002)
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

/// 运行期可配置的后端连接。持久化到 UserDefaults;token 不入源码。
@MainActor
final class AppConfig: ObservableObject {
    static let envKey = "NK_ENVIRONMENT"
    static let tokenKey = "NK_API_TOKEN"
    static let baseOverrideKey = "NK_BASE_URL_OVERRIDE"

    /// 持久化后端(生产 = `.standard`;单测注入隔离 suite 保证 hermetic,不吃模拟器
    /// 里前几次会话残留的 `NK_ENVIRONMENT`)。
    private let defaults: UserDefaults

    @Published var environment: NKEnvironment {
        didSet { defaults.set(environment.rawValue, forKey: Self.envKey) }
    }
    /// 手填覆盖 baseURL(可选;空则用 environment.baseURL)
    @Published var baseURLOverride: String {
        didSet { defaults.set(baseURLOverride, forKey: Self.baseOverrideKey) }
    }
    @Published var apiToken: String {
        didSet { defaults.set(apiToken, forKey: Self.tokenKey) }
    }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        // 默认后端 = prod(https://nk.linotsai.top,V2-⑰ 割接后的新机)。无持久化选择时用 prod;
        // dev(本地 uvicorn 8002)仍可在设置屏「环境」picker 或手填 baseURLOverride 切换,配置
        // 能力不变。⚠ 老 App 存过的 `NK_ENVIRONMENT="prod"` 在这里会被读成 **新** prod = nk,
        // 这正是换包要的效果(同一个 rawValue,指向随版本换血)。
        self.environment = NKEnvironment(rawValue: defaults.string(forKey: Self.envKey) ?? "") ?? .prod
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
