//
//  IntegrationSmokeTests.swift
//  NecklineTests — 真实网络联调冒烟(§五 阶段4C 验收:「与本地 dev 后端的联调闭环
//  证据(开清仓/看板真请求)」)。⚠ V2.1-① 起「问询台真请求」一项(原三项之一)
//  已随问询台整链退役删除。
//
//  这批测试用**真实 URLSession**(不经 MockURLProtocol)对本机 dev 后端发真请求,
//  只有本地跑起 dev uvicorn 才会执行;检测不到服务 → `XCTSkip`,不影响默认
//  `xcodebuild test` 的常规门禁(那 31 个测试全部离线可跑,见 URLGateTests /
//  DTODecodeTests / AppModelTests)。
//
//  跑法(先起 dev 后端,固定占位 token 沿用 `scripts/smoke_api.sh` 同款惯例——
//  纯本地联调用,非真实密钥):
//    DB_PATH=/tmp/neckline_it.db API_TOKEN=smoke_token_at_least_16_chars_long \
//      NECKLINE_ENABLE_SENTINEL=0 .venv/bin/python -m uvicorn neckline.api.app:app \
//      --host 127.0.0.1 --port 8002
//    然后:xcodebuild test -project Neckline.xcodeproj -scheme Neckline \
//      -destination 'platform=iOS Simulator,name=LinoJ-iPhone16Pro' \
//      -only-testing:NecklineTests/IntegrationSmokeTests
//
//  ⚠️ 只打 127.0.0.1:8002,任何情况下都不得指向 prod(nk.linotsai.top)—— 这批测试
//     会真实开/清仓、真实改设置,绝不能碰生产台账。
//

import XCTest
@testable import Neckline

final class IntegrationSmokeTests: XCTestCase {
    /// 与 `scripts/smoke_api.sh` 同款固定占位 token(本地联调惯例,非真实密钥)。
    private static let devToken = "smoke_token_at_least_16_chars_long"
    private static let devBase = URL(string: "http://127.0.0.1:8002")!

    private func makeClient() -> APIClient {
        APIClient(baseURL: Self.devBase, token: Self.devToken)
    }

    /// 探活;打不通就 skip(不让"没起 dev 后端"污染常规门禁的红绿判断)。
    private func skipUnlessDevServerReachable() async throws {
        let client = makeClient()
        let reachable = (try? await client.health())?.ok ?? false
        try XCTSkipUnless(reachable, "dev 后端未在 127.0.0.1:8002 运行,跳过真实联调(见文件头跑法说明)")
    }

    func testHealthReachable() async throws {
        try await skipUnlessDevServerReachable()
        let health = try await makeClient().health()
        XCTAssertTrue(health.ok)
        // v1.5-⑤-E:health 端点顺带把 version 带回,联调时冒烟一下不为空(不锁具体值,
        // 避免测试与「本地跑的到底是哪个版本」耦合)。
        XCTAssertNotNil(health.version)
    }

    /// 今日计划:GET /report/latest 真请求(§4C.1)。
    func testReportLatestRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let report = try await makeClient().fetchReportLatest()
        XCTAssertFalse(report.degraded, "dev 后端应已由 scripts/report.py 生成过真实报告")
        XCTAssertNotNil(report.sentiment)
        // V2-⑮:候选榜整族已退役,报告改由**篮子日报三段**承载。⚠ 这里**刻意不断言
        // 「今日篮子非空」** —— 「今天真没有篮子」是合法输出(⑥-b-B);能断言的是
        // **三段的可得性位真的解出来了**(空数组 + available=true 与 available=false
        // 是两回事,界面上必须讲不同的话)。
        let daily = report.basketDaily
        for b in daily.baskets {
            XCTAssertGreaterThan(b.basketId, 0)
            // 篮子在、卡没生成是合法中间态 —— 有卡时卡里的 basketKey 不该是空的。
            if let card = b.card { XCTAssertFalse(card.basketKey.isEmpty) }
            else { XCTAssertNotNil(b.cardUnavailableText, "无卡时必须给得出诚实文案") }
        }
        // ③b 两个原因码语义相反,⛔ 不许合并成「未入选」。
        for d in daily.droppedBaskets {
            XCTAssertTrue(["capacity_overflow", "below_quality_line"].contains(d.reason),
                          "未登记的 dropped reason:\(d.reason)")
        }
    }


    // ⚠ V2.1-① 起 `testInquiryRealRequestVerdictIsDescriptive`(§4C.3 问询台真请求)
    // 已随问询台整链退役删除(`sendInquiry`/`ChatMessage` 均已物理删除)。

    /// 设置真请求闭环(V2-②/⑪ 换血后):GET → POST provider(key 只发一次)→ GET
    /// (**确认只回 keySet 布尔、绝不回明文**)→ PUT push(按 kind 全量覆盖)→ GET →
    /// DELETE provider 清理。
    func testSettingsRoundTripRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let client = makeClient()

        let name = "integration-test-provider"
        _ = try? await client.deleteProvider(name: name)   // 清理上一轮残留
        _ = try await client.createProvider(ProviderCreateRequest(
            name: name, baseUrl: "https://example.invalid/v1", model: "test-model",
            apiKey: "sk-integration-test-not-real", hasWebSearch: false,
            searchEngine: nil, notes: "IntegrationSmokeTests", enabled: false))

        let afterCreate = try await client.fetchSettings()
        let p = try XCTUnwrap(afterCreate.providers.first { $0.name == name })
        XCTAssertTrue(p.keySet, "key 已配 —— 但响应里只有布尔,类型层面就没有明文字段")

        // 按 kind 全量覆盖式写:**把服务端发来的那一份原样回写**(⛔ 客户端不硬编清单)。
        var kinds = afterCreate.push.enabledMap
        XCTAssertFalse(kinds.isEmpty, "服务端应给出已登记的 kind 清单")
        if let first = afterCreate.push.kinds.first { kinds[first.kind] = !first.enabled }
        _ = try await client.putSettingsPush(kinds: kinds)
        let afterPush = try await client.fetchSettings()
        if let first = afterCreate.push.kinds.first {
            XCTAssertEqual(afterPush.push.kinds.first { $0.kind == first.kind }?.enabled,
                           !first.enabled, "翻一个 kind 不该连坐其它 kind")
        }
        // 复原(不污染 dev 库)。
        _ = try await client.putSettingsPush(kinds: afterCreate.push.enabledMap)
        _ = try await client.deleteProvider(name: name)

        _ = try await client.registerDevice(token: "integration-test-device-token")
    }


    // MARK: - ⑩ 极简录入真请求闭环:三字段开仓 → 自动关联 + 计划继承 + 建仓快照 → 可选补充


    /// 篮子族端点真请求:列表 → 单篮 → 卡 / 验证。**空列表是合法输出**,⛔ 不是 404。
    func testBasketsRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let client = makeClient()
        let baskets = try await client.fetchBaskets()
        guard let first = baskets.first else {
            throw XCTSkip("dev 库当日无篮子(合法输出),跳过卡 / 验证的真请求")
        }
        let one = try await client.fetchBasket(id: first.basketId)
        XCTAssertEqual(one.basketId, first.basketId)
        // 「篮子在、卡没生成」是合法中间态 → `card_not_ready`,⛔ **不是**「篮子不存在」。
        do { _ = try await client.fetchBasketCard(id: first.basketId) }
        catch APIError.cardNotReady { /* 合法中间态 */ }
        let v = try await client.fetchBasketVerification(id: first.basketId)
        XCTAssertEqual(v.basketId, first.basketId)
    }

    // ⚠ **`testCircuitStateRealRequest` 已删**(V2.2-⑤-B):`GET /circuit` /
    // `POST /circuit/unlock` 两条端点随熔断整体退役消失(用户裁定 #8),客户端两个
    // 方法同批删 —— 这条真请求测试没有可打的目标了。⛔ 不许接回来。

}
