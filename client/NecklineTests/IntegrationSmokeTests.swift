//
//  IntegrationSmokeTests.swift
//  NecklineTests — 真实网络联调冒烟(§五 阶段4C 验收:「与本地 dev 后端的联调闭环
//  证据(开清仓/看板/问询台真请求)」)。
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
//  ⚠️ 只打 127.0.0.1:8002,任何情况下都不得指向 prod(ln.linotsai.top)—— 这批测试
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
        let reachable = (try? await client.health()) ?? false
        try XCTSkipUnless(reachable, "dev 后端未在 127.0.0.1:8002 运行,跳过真实联调(见文件头跑法说明)")
    }

    func testHealthReachable() async throws {
        try await skipUnlessDevServerReachable()
        let ok = try await makeClient().health()
        XCTAssertTrue(ok)
    }

    /// 今日计划:GET /report/latest 真请求(§4C.1)。
    func testReportLatestRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let report = try await makeClient().fetchReportLatest()
        XCTAssertFalse(report.degraded, "dev 后端应已由 scripts/report.py 生成过真实报告")
        XCTAssertFalse(report.candidates.isEmpty)
        XCTAssertNotNil(report.sentiment)
        // 四件套字段非空(真实报告文案,不是占位符)
        let c0 = report.candidates[0]
        XCTAssertFalse(c0.buyPoint.isEmpty)
        XCTAssertFalse(c0.stop.isEmpty)
        XCTAssertTrue(c0.stop.contains("-5%"), "止损口径必须是 -5% 单一常量")
    }

    /// 盘中看板:GET /board 真请求(§4C.2)。
    func testBoardRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let board = try await makeClient().fetchBoard()
        // 不对具体内容做强断言(测试环境状态可能变化),只验证真实解码成功 + 类型正确。
        XCTAssertFalse(board.tradeDate.isEmpty)
        for e in board.events {
            XCTAssertFalse(e.code.isEmpty)
            XCTAssertFalse(e.verdict.isEmpty)
        }
    }

    /// 持仓开/清仓真请求闭环(§4C.1「审计台账」;用独立测试代码,不碰手动种的演示持仓)。
    func testPositionOpenCloseRoundTripRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let client = makeClient()
        let before = try await client.fetchPositions()

        let opened = try await client.openPosition(code: "000001.SZ", name: "平安银行(集成测试)",
                                                    buyPrice: 10.0, qty: 100,
                                                    entryReason: "IntegrationSmokeTests 真请求闭环")
        XCTAssertEqual(opened.stopLine, 9.5, accuracy: 0.001, "-5% 单一止损常量派生")

        let afterOpen = try await client.fetchPositions()
        XCTAssertEqual(afterOpen.count, before.count + 1)
        XCTAssertTrue(afterOpen.contains { $0.id == opened.positionId })

        try await client.closePosition(id: opened.positionId, sellPrice: 10.3)
        let afterClose = try await client.fetchPositions()
        XCTAssertFalse(afterClose.contains { $0.id == opened.positionId }, "清仓后不应再出现在持仓列表")

        // 二次关闭同一笔 → 404 not_holding(对齐后端契约,真实网络往返验证,非 mock)。
        do {
            _ = try await client.closePosition(id: opened.positionId, sellPrice: 10.3)
            XCTFail("重复清仓应抛 notHolding")
        } catch APIError.notHolding {
            // 期望路径
        }
    }

    /// 问询台真请求(§4C.3):裁决必须落在二值之一,「不符合」/「初审通过进海选池」,
    /// 且这是**真实**跑通了「确定性检查 + LLM 降级链」全链路(dev 环境未必配了 LLM key,
    /// 走降级占位也是本条铁律「缺 key 全链路不崩」的真实证据,而不是靠 mock 假装)。
    func testInquiryRealRequestVerdictIsBinary() async throws {
        try await skipUnlessDevServerReachable()
        let client = makeClient()
        let result = try await client.sendInquiry(
            code: "600519.SH",
            messages: [ChatMessage(role: .user, text: "这票现在还能追吗?")]
        )
        XCTAssertTrue(result.verdict == .pass || result.verdict == .reject,
                      "真实响应裁决必须是二值之一,实际 verdict=\(result.verdict)")
        XCTAssertFalse(result.reply.isEmpty)
        XCTAssertFalse(result.verdict.enablesBuyAction)
    }

    /// 设置真请求闭环(§4C.4,🔴):GET → PUT llm → GET(确认不回明文)→ PUT push → GET。
    func testSettingsRoundTripRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let client = makeClient()

        _ = try await client.putSettingsLLM(provider: .glm, apiKey: "sk-integration-test-not-real")
        let afterLLM = try await client.fetchSettings()
        XCTAssertEqual(afterLLM.llmProvider, "glm")
        XCTAssertTrue(afterLLM.llmKeySet)

        _ = try await client.putSettingsPush(report: true, retreatBrake: false)
        let afterPush = try await client.fetchSettings()
        XCTAssertTrue(afterPush.push.report)
        XCTAssertFalse(afterPush.push.retreatBrake)

        _ = try await client.registerDevice(token: "integration-test-device-token")
    }
}
