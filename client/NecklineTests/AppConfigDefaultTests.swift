//
//  AppConfigDefaultTests.swift
//  NecklineTests — App 默认后端 = prod(ln.linotsai.top)门禁(§五 阶段 4E.3 接班切换)。
//
//  2026-07-20 4E 接班切换后,nginx `ln.linotsai.top` upstream 已从 LinoN 8001 切到
//  Neckline 8002,LinoN 退役。故 App 首次启动(无持久化环境选择)的默认后端应为 prod
//  的 https://ln.linotsai.top,而非旧的本地 dev。本测试锁死这条默认,并验证「保留可
//  配置覆盖」:环境 picker 与手填 baseURLOverride 仍能切回 dev / 任意基址。
//
//  用注入的隔离 UserDefaults suite 保证 hermetic —— 不吃模拟器里前几次会话残留的
//  `NK_ENVIRONMENT`(共享 `.standard` 会串味,曾致这批断言误红)。
//

import XCTest
@testable import Neckline

@MainActor
final class AppConfigDefaultTests: XCTestCase {

    /// 每个测试拿一个全新、空的 UserDefaults suite(无任何持久化残留)。
    private func freshDefaults() -> UserDefaults {
        let name = "NecklineTests-\(UUID().uuidString)"
        let d = UserDefaults(suiteName: name)!
        d.removePersistentDomain(forName: name)   // 双保险:确保空
        return d
    }

    /// 无持久化选择时,默认后端 = prod(https://ln.linotsai.top)。
    func testDefaultBackendIsProdLinotsai() {
        let config = AppConfig(defaults: freshDefaults())
        XCTAssertEqual(config.environment, .prod, "首次启动默认环境应为 prod(接班切换后)")
        XCTAssertEqual(config.resolvedBaseURL.absoluteString, "https://ln.linotsai.top",
                       "默认后端地址应为 https://ln.linotsai.top")
    }

    /// 保留可配置覆盖①:切到 dev 环境 → resolvedBaseURL 跟随 dev(本地 uvicorn 8002)。
    func testEnvironmentPickerStillSwitchesToDev() {
        let config = AppConfig(defaults: freshDefaults())
        config.environment = .dev
        XCTAssertEqual(config.resolvedBaseURL.absoluteString, "http://127.0.0.1:8002")
    }

    /// 保留可配置覆盖②:手填 baseURLOverride 优先于 environment.baseURL。
    func testBaseURLOverrideWins() {
        let config = AppConfig(defaults: freshDefaults())
        config.baseURLOverride = "http://192.168.1.50:9000"
        XCTAssertEqual(config.resolvedBaseURL.absoluteString, "http://192.168.1.50:9000",
                       "手填 baseURLOverride 应压过默认 prod")
        // 清空 override 后回落到默认 prod
        config.baseURLOverride = ""
        XCTAssertEqual(config.resolvedBaseURL.absoluteString, "https://ln.linotsai.top")
    }

    /// 持久化往返:选择被写进注入的 suite,新实例读回同一选择(证明 didSet 走注入的
    /// defaults 而非 `.standard`)。
    func testChoicePersistsIntoInjectedDefaults() {
        let d = freshDefaults()
        let first = AppConfig(defaults: d)
        first.environment = .dev
        let second = AppConfig(defaults: d)
        XCTAssertEqual(second.environment, .dev, "环境选择应从注入的 suite 读回")
    }
}
