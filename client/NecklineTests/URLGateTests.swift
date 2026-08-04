//
//  URLGateTests.swift
//  NecklineTests — `APIClient.makeURL` 门禁(§五 阶段4C 坑吸收②)。
//
//  LinoN v1.3.0 曾因 `baseURL.appendingPathComponent(path)` 把带 "?query" 的整个 path
//  当单个路径组件、"?" 编码成 "%3F",致带 query 的端点(`report?date=`)真后端恒 404
//  且被静默吞(单测不走真 URL 构造层所以全绿,是"审后才被抓到"的致命坑)。本测试
//  直接断言 `?` 不被编码,新增任何带 query 的端点都必须过这道门禁。
//

import XCTest
@testable import Neckline

final class URLGateTests: XCTestCase {

    func testMakeURLPreservesQueryString() {
        let base = URL(string: "http://127.0.0.1:8002")!

        let report = APIClient.makeURL(base: base, path: "/api/v1/report?date=20260717")
        XCTAssertEqual(report?.absoluteString, "http://127.0.0.1:8002/api/v1/report?date=20260717")
        XCTAssertFalse(report?.absoluteString.contains("%3F") ?? true, "? 不能被编码成 %3F")

        // 纯路径端点(无 query)不回归
        let plain = APIClient.makeURL(base: base, path: "/api/v1/positions")
        XCTAssertEqual(plain?.absoluteString, "http://127.0.0.1:8002/api/v1/positions")

        // prod https 基址同样正确(V2-⑰ 割接后 prod = nk.linotsai.top,§3.6 / §五 V2-⑰)
        let prod = APIClient.makeURL(base: URL(string: "https://nk.linotsai.top")!,
                                     path: "/api/v1/report?date=20260101")
        XCTAssertEqual(prod?.absoluteString, "https://nk.linotsai.top/api/v1/report?date=20260101")
        XCTAssertFalse(prod?.absoluteString.contains("%3F") ?? true)

        // 路径参数端点(/positions/{id}/close)不含 query,同样必须原样保留
        let close = APIClient.makeURL(base: base, path: "/api/v1/positions/42/close")
        XCTAssertEqual(close?.absoluteString, "http://127.0.0.1:8002/api/v1/positions/42/close")
    }

    /// 反面对照:`appendingPathComponent` 在带 query 的 path 上会编码 "?"(留证据,
    /// 防止未来有人"优化"把 `makeURL` 改回这个写法)。
    func testAppendingPathComponentWouldHaveEncodedQuestionMark() {
        let base = URL(string: "http://127.0.0.1:8002")!
        let bad = base.appendingPathComponent("/api/v1/report?date=20260717")
        XCTAssertTrue(bad.absoluteString.contains("%3F"),
                      "此断言本身就是坑的证据:appendingPathComponent 会编码 ?,故 APIClient 一律禁用它")
    }
}
