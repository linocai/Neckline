//
//  AppConfigDefaultTests.swift
//  NecklineTests — App 默认后端 = prod(**nk.linotsai.top**)门禁。
//
//  **这条断言是客户端与现役契约的机器闸门**：默认值指错会导致整份报告解码失败。
//  本测试同时验证「保留可配置覆盖」:环境 picker 与手填 baseURLOverride 仍能切回
//  dev / 任意基址。
//
//  用注入的隔离 UserDefaults suite 保证 hermetic —— 不吃模拟器里前几次会话残留的
//  `NK_ENVIRONMENT`(共享 `.standard` 会串味,曾致这批断言误红)。
//

import XCTest
@testable import Neckline

@MainActor
final class AppConfigDefaultTests: XCTestCase {

    private final class MemoryTokenStore: APIAccessTokenStore {
        var value: String?
        var acceptsWrites = true

        func load() -> String? { value }

        func save(_ token: String) -> Bool {
            guard acceptsWrites else { return false }
            value = token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : token
            return true
        }
    }

    /// 每个测试拿一个全新、空的 UserDefaults suite(无任何持久化残留)。
    private func freshDefaults() -> UserDefaults {
        let name = "NecklineTests-\(UUID().uuidString)"
        let d = UserDefaults(suiteName: name)!
        d.removePersistentDomain(forName: name)   // 双保险:确保空
        return d
    }

    /// 无持久化选择时,默认后端 = prod(https://nk.linotsai.top)。
    func testDefaultBackendIsProdLinotsai() {
        let config = AppConfig(defaults: freshDefaults(), tokenStore: MemoryTokenStore())
        XCTAssertEqual(config.environment, .prod, "首次启动默认环境应为 prod")
        XCTAssertEqual(config.resolvedBaseURL.absoluteString, "https://nk.linotsai.top",
                       "默认后端地址应为 https://nk.linotsai.top")
    }

    /// 反向闸门:默认后端**绝不能**再指向老机 `ln.linotsai.top`。
    /// 单独立一条而不是只靠上面的相等断言 —— 相等断言在有人"顺手改回去"时会红,
    /// 但读失败信息的人未必知道为什么不能是 ln;这条把理由写在断言消息里。
    func testDefaultBackendNeverPointsAtRetiredOldHost() {
        let config = AppConfig(defaults: freshDefaults(), tokenStore: MemoryTokenStore())
        XCTAssertFalse(config.resolvedBaseURL.absoluteString.contains("ln.linotsai.top"),
                       "默认后端不得指向老机 ln —— 老机跑的是 V1.5.2 契约,新 App 打过去整份报告解不出(A 路)")
    }

    /// 保留可配置覆盖①:切到 dev 环境 → resolvedBaseURL 跟随 dev(本地 uvicorn 8002)。
    func testEnvironmentPickerStillSwitchesToDev() {
        let config = AppConfig(defaults: freshDefaults(), tokenStore: MemoryTokenStore())
        config.environment = .dev
        XCTAssertEqual(config.resolvedBaseURL.absoluteString, "http://127.0.0.1:8002")
    }

    /// HTTPS 手填地址优先；不安全远端地址必须显式报错，绝不能暗中连生产。
    func testHTTPSBaseURLOverrideWins() {
        let config = AppConfig(defaults: freshDefaults(), tokenStore: MemoryTokenStore())
        config.baseURLOverride = "https://research.example.test:9000"
        XCTAssertEqual(config.resolvedBaseURL.absoluteString, "https://research.example.test:9000")
        XCTAssertEqual(config.effectiveServiceLabel, "临时 · research.example.test:9000")
        XCTAssertNil(config.connectionConfigurationError)
    }

    func testInvalidRemoteHTTPOverrideNeverFallsBackToProduction() {
        let config = AppConfig(defaults: freshDefaults(), tokenStore: MemoryTokenStore())
        config.baseURLOverride = "http://192.168.1.50:9000"
        XCTAssertEqual(config.resolvedBaseURL.host, "configuration.invalid")
        XCTAssertNotNil(config.connectionConfigurationError)
        config.baseURLOverride = ""
        XCTAssertEqual(config.resolvedBaseURL.absoluteString, "https://nk.linotsai.top")
    }

    /// 持久化往返:选择被写进注入的 suite,新实例读回同一选择(证明 didSet 走注入的
    /// defaults 而非 `.standard`)。
    func testChoicePersistsIntoInjectedDefaults() {
        let d = freshDefaults()
        let store = MemoryTokenStore()
        let first = AppConfig(defaults: d, tokenStore: store)
        first.environment = .dev
        let second = AppConfig(defaults: d, tokenStore: store)
        XCTAssertEqual(second.environment, .dev, "环境选择应从注入的 suite 读回")
    }

    func testLegacyTokenMigratesOnlyAfterKeychainWriteSucceeds() {
        let d = freshDefaults()
        d.set("legacy-token", forKey: AppConfig.tokenKey)
        let store = MemoryTokenStore()
        let config = AppConfig(defaults: d, tokenStore: store)
        XCTAssertEqual(config.apiToken, "legacy-token")
        XCTAssertEqual(store.value, "legacy-token")
        XCTAssertNil(d.string(forKey: AppConfig.tokenKey))
    }

    func testFailedTokenMigrationRetainsLegacyValueForRetry() {
        let d = freshDefaults()
        d.set("legacy-token", forKey: AppConfig.tokenKey)
        let store = MemoryTokenStore()
        store.acceptsWrites = false
        let config = AppConfig(defaults: d, tokenStore: store)
        XCTAssertEqual(config.apiToken, "legacy-token")
        XCTAssertEqual(d.string(forKey: AppConfig.tokenKey), "legacy-token")
    }
}
