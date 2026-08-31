//
//  URLGateTests.swift
//  NecklineTests — `APIClient.makeURL` 门禁。
//
//  LinoN v1.3.0 曾因 `baseURL.appendingPathComponent(path)` 把带 "?query" 的整个 path
//  当单个路径组件、"?" 编码成 "%3F",致带 query 的端点真后端**恒 404 且被静默吞**
//  (单测不走真 URL 构造层所以全绿,是"审后才被抓到"的致命坑)。本测试直接断言 `?`
//  不被编码 —— **新增任何带 query 的端点都必须过这道门禁**。
//
//  ⚠ V2.5.0 S12:样本换成本版真实端点(`/report` / `/positions` 那一族已删)。
//

import XCTest
@testable import Neckline

final class URLGateTests: XCTestCase {

    func testVersionMismatchMessagePointsAtTheOlderSide() {
        XCTAssertEqual(
            NKVersionCompatibility.message(serverVersion: "2.6.0", appVersion: "2.7.0"),
            "服务端仍是 v2.6.0，当前 App 为 2.7.0；请先部署服务端。"
        )
        XCTAssertEqual(
            NKVersionCompatibility.message(serverVersion: "v2.8.0", appVersion: "2.7.0"),
            "服务端已是 v2.8.0，当前 App 为 2.7.0；请更新 App。"
        )
        XCTAssertNil(NKVersionCompatibility.message(serverVersion: "v2.7.0", appVersion: "2.7.0"))
        XCTAssertNil(NKVersionCompatibility.message(serverVersion: "2.7", appVersion: "2.7.0"))
    }

    func testMakeURLPreservesQueryString() {
        let base = URL(string: "http://127.0.0.1:8002")!

        let packages = APIClient.makeURL(base: base, path: "/api/v1/scoreboard/packages?state=active")
        XCTAssertEqual(packages?.absoluteString,
                       "http://127.0.0.1:8002/api/v1/scoreboard/packages?state=active")
        XCTAssertFalse(packages?.absoluteString.contains("%3F") ?? true, "? 不能被编码成 %3F")

        let review = APIClient.makeURL(base: base, path: "/api/v1/review?week=2026-W34")
        XCTAssertEqual(review?.absoluteString, "http://127.0.0.1:8002/api/v1/review?week=2026-W34")

        // 纯路径端点(无 query)不回归
        let plain = APIClient.makeURL(base: base, path: "/api/v1/selection/latest")
        XCTAssertEqual(plain?.absoluteString, "http://127.0.0.1:8002/api/v1/selection/latest")

        // prod https 基址同样正确
        let prod = APIClient.makeURL(base: URL(string: "https://nk.linotsai.top")!,
                                     path: "/api/v1/review/bindery?week=2026-W34")
        XCTAssertEqual(prod?.absoluteString,
                       "https://nk.linotsai.top/api/v1/review/bindery?week=2026-W34")
        XCTAssertFalse(prod?.absoluteString.contains("%3F") ?? true)

        // 多段路径参数端点(个股详情 / 预案)不含 query,同样必须原样保留
        let stock = APIClient.makeURL(base: base,
                                      path: "/api/v1/scoreboard/packages/k9-v3-20260820-demo")
        XCTAssertEqual(stock?.absoluteString,
                       "http://127.0.0.1:8002/api/v1/scoreboard/packages/k9-v3-20260820-demo")
        let playbook = APIClient.makeURL(
            base: base, path: "/api/v1/checklists/k9-v3-20260820-demo")
        XCTAssertEqual(playbook?.absoluteString,
                       "http://127.0.0.1:8002/api/v1/checklists/k9-v3-20260820-demo")
    }

    /// 反面对照:`appendingPathComponent` 在带 query 的 path 上会编码 "?"(留证据,
    /// 防止未来有人"优化"把 `makeURL` 改回这个写法)。
    func testAppendingPathComponentWouldHaveEncodedQuestionMark() {
        let base = URL(string: "http://127.0.0.1:8002")!
        let bad = base.appendingPathComponent("/api/v1/scoreboard/packages?state=active")
        XCTAssertTrue(bad.absoluteString.contains("%3F"),
                      "此断言本身就是坑的证据:appendingPathComponent 会编码 ?,故 APIClient 一律禁用它")
    }
}
