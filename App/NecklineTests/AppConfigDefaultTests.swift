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

    /// 每个测试拿一个全新、空的 UserDefaults suite(无任何持久化残留)。
    private func freshDefaults() -> UserDefaults {
        let name = "NecklineTests-\(UUID().uuidString)"
        let d = UserDefaults(suiteName: name)!
        d.removePersistentDomain(forName: name)   // 双保险:确保空
        return d
    }

    /// 无持久化选择时,默认后端 = prod(https://nk.linotsai.top)。
    func testDefaultBackendIsProdLinotsai() {
        let config = AppConfig(defaults: freshDefaults())
        XCTAssertEqual(config.environment, .prod, "首次启动默认环境应为 prod")
        XCTAssertEqual(config.resolvedBaseURL.absoluteString, "https://nk.linotsai.top",
                       "默认后端地址应为 https://nk.linotsai.top")
    }

    /// 反向闸门:默认后端**绝不能**再指向老机 `ln.linotsai.top`。
    /// 单独立一条而不是只靠上面的相等断言 —— 相等断言在有人"顺手改回去"时会红,
    /// 但读失败信息的人未必知道为什么不能是 ln;这条把理由写在断言消息里。
    func testDefaultBackendNeverPointsAtRetiredOldHost() {
        let config = AppConfig(defaults: freshDefaults())
        XCTAssertFalse(config.resolvedBaseURL.absoluteString.contains("ln.linotsai.top"),
                       "默认后端不得指向老机 ln —— 老机跑的是 V1.5.2 契约,新 App 打过去整份报告解不出(A 路)")
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
        XCTAssertEqual(config.resolvedBaseURL.absoluteString, "https://nk.linotsai.top")
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
