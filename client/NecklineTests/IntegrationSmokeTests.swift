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

    /// 问询台真请求(§4C.3,v1.3.3 起自由分析师):标注必须落在两个已知描述性标注
    /// 之一,「已分析」/「已分析·有风险提示」(**不是裁决**,不影响任何操作),
    /// 且这是**真实**跑通了「确定性检查 + LLM 降级链」全链路(dev 环境未必配了 LLM key,
    /// 走降级占位也是本条铁律「缺 key 全链路不崩」的真实证据,而不是靠 mock 假装)。
    func testInquiryRealRequestVerdictIsDescriptive() async throws {
        try await skipUnlessDevServerReachable()
        let client = makeClient()
        let result = try await client.sendInquiry(
            code: "600519.SH",
            messages: [ChatMessage(role: .user, text: "这票现在还能追吗?")]
        )
        XCTAssertTrue(result.verdict == .analyzed || result.verdict == .analyzedWarn,
                      "真实响应应落在两个已知描述性标注之一,实际 verdict=\(result.verdict)")
        XCTAssertFalse(result.reply.isEmpty)
        XCTAssertFalse(result.verdict.enablesBuyAction)
    }

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

    /// 一键补录预填推荐真请求(§五 v1.2-E.5,区间双档)。
    ///
    /// ⚠️ **已知契约缺口**(v1.2-E 施工期发现,已在交付报告里提出、未擅自改后端):
    /// `neckline/api/schemas.py::EntrySuggestionOut` + `app.py::entry_suggestion()`
    /// 仍是 v1.1-B.3 的单 `qty` 旧形状,尚未按「v1.2 客户端契约清单」改成
    /// `qtyLow/qtyHigh/capFloor/capCeil`。客户端已按新契约实现并有 mock 单测覆盖
    /// (见 `DTODecodeTests.testDecodeEntrySuggestionRange`),但对**当前**后端发真
    /// 请求会因缺字段解码失败——用 `catch` 转 `XCTSkip` 而非放任失败,待后端补齐
    /// 后请删掉这个 catch、改回直接断言。
    func testEntrySuggestionRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let client = makeClient()
        do {
            let s = try await client.entrySuggestion(code: "600001.SH", price: 50.0)
            XCTAssertEqual(s.qtyHigh, 400)   // floor(single_cap/price/100)*100(现役 config 兜底 single_cap=20000)
            XCTAssertEqual(s.qtyLow, 200)    // floor(single_cap*0.5/price/100)*100
            XCTAssertEqual(s.stopLine, 47.5, accuracy: 0.001)   // 50×(1-0.05)
        } catch {
            throw XCTSkip("已知契约缺口:后端 EntrySuggestionOut 尚未实现区间字段,详见本方法头注释。原始错误:\(error)")
        }
    }

    // MARK: - ⑩ 极简录入真请求闭环:三字段开仓 → 自动关联 + 计划继承 + 建仓快照 → 可选补充

    /// ⚠ **V2-⑮ 换血**:决策日志「创建 → link → scenario-outcome」闭环整套退役
    /// (`decision_log` v2.0.0 起停写留档,四个写端点服务端已删)。本用例改为验
    /// ⑩ 的新闭环:**三字段开仓**(其余自动关联)+ `position_plans` v1 自动落 +
    /// `entry_snapshots` 冻结 + ⑩-C「用户可选补充」空提交合法。
    func testMinimalEntryAutoLinkAndOptionalNoteRoundTripRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let client = makeClient()

        // ⑩-A:票 + 价 + 量三字段即可(理由 / 费用都不传)。幂等键每次新铸。
        let opened = try await client.openPosition(
            code: "600006.SH", name: nil, buyPrice: 10.0, qty: 1000, entryReason: "",
            idempotencyKey: UUID().uuidString)
        XCTAssertGreaterThan(opened.positionId, 0)
        XCTAssertFalse(opened.replayed, "全新幂等键不该被判成重放")

        // ⑩-B:开仓必落 `version=1` 计划行(**有仓无 v1 是走不出去的死局**)。
        let plans = try await client.fetchPositionPlans(positionId: opened.positionId)
        let v1 = try XCTUnwrap(plans.first { $0.version == 1 }, "开仓应自动落 version=1")
        // 无来源篮子 / 卡未就绪都是**合法**结果 —— 行照落、如实标原因,⛔ 不省略整条记录。
        if !v1.available { XCTAssertNotNil(v1.unavailableText) }
        // ⑪-D 武装态四键**恒存在**(读侧 fail-closed:缺键 = 不武装)。
        XCTAssertNotNil(v1.plan["exit_reference_armed"], "武装态键必须恒存在,不搞「缺键即默认」")

        // ⑩-A:`entry_snapshots` 冻结一行(没有则 404 not_found,也是可接受的历史态)。
        do {
            let snap = try await client.fetchEntrySnapshot(positionId: opened.positionId)
            XCTAssertEqual(snap.positionId, opened.positionId)
            // ⛔ 别把"没采"读成"没有"。
            XCTAssertFalse(snap.notCaptured.contains(""), "not_captured 不该有空串")
        } catch APIError.notFound {
            XCTFail("⑩ 起开仓必落 entry_snapshots 冻结行")
        }

        // ⑩-C:用户可选补充 —— **空提交合法**(200,不是 400)。
        let empty = try await client.postDecisionNote(code: "600006.SH")
        XCTAssertTrue(empty.ok)
        let labeled = try await client.postDecisionNote(
            code: "600006.SH", positionId: opened.positionId,
            labels: ["THEME_SHIFT"], voiceNote: "IntegrationSmokeTests")
        XCTAssertTrue(labeled.recorded.contains("label"))

        // 清理:清仓,不污染 dev 库演示数据。
        _ = try? await client.closePosition(id: opened.positionId, sellPrice: 10.1,
                                            closeReason: "MANUAL")
    }

    /// **幂等键真实往返**:同键二次提交 → `replayed=true` 且**不开第二笔仓**。
    func testIdempotencyKeyReplayDoesNotOpenSecondPositionRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let client = makeClient()
        let key = UUID().uuidString
        let first = try await client.openPosition(code: "600008.SH", name: nil, buyPrice: 5.0,
                                                  qty: 100, entryReason: "", idempotencyKey: key)
        let second = try await client.openPosition(code: "600008.SH", name: nil, buyPrice: 5.0,
                                                   qty: 100, entryReason: "", idempotencyKey: key)
        XCTAssertTrue(second.replayed, "同键 = 同一笔意图的重试,服务端应重放而不是开第二笔")
        XCTAssertEqual(first.positionId, second.positionId)
        _ = try? await client.closePosition(id: first.positionId, sellPrice: 5.1, closeReason: "MANUAL")
    }

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

    // MARK: - v1.2-A2 熔断纪律状态真请求(§五 v1.2-E.3;不强造锁定态,只验真实解码)

    func testCircuitStateRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let client = makeClient()
        let state = try await client.getCircuit()
        // 不对 locked 具体值做强断言(dev 库当前态可能因其它测试残留而变化),只验证
        // 真实解码成功 + 类型正确;锁定态时 episode 字段齐全。
        if state.locked {
            XCTAssertNotNil(state.episode)
            XCTAssertFalse(state.episode?.note.isEmpty ?? true)
        }
        // 无锁定态时解锁应幂等成功(不因"本来就没锁"而报错)。
        _ = try await client.unlockCircuit()
    }

    /// 清仓带 closeReason 真请求(§五 v1.2-E.2)。
    func testClosePositionWithCloseReasonRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let client = makeClient()
        let opened = try await client.openPosition(code: "600007.SH", name: "集成测试乙", buyPrice: 20.0,
                                                    qty: 100, entryReason: "IntegrationSmokeTests closeReason")
        _ = try await client.closePosition(id: opened.positionId, sellPrice: 19.0, closeReason: "STOP_LOSS")
        // closeReason 不在 PositionOut 里回显(只有开放持仓列表),这里只验证带
        // closeReason 的清仓请求真实成功(未 422/500),契约形状由 mock 单测覆盖。
    }
}
