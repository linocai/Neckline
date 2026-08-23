//
//  IntegrationSmokeTests.swift
//  NecklineTests — 真实网络联调冒烟(V2.5.0 S12 换血)。
//
//  这批测试用**真实 URLSession**(不经 Mock)对本机 dev 后端发真请求,只有本地跑起
//  dev uvicorn 才会执行;检测不到服务 → `XCTSkip`,不影响默认 `xcodebuild test` 的
//  常规门禁(其余测试全部离线可跑)。
//
//  跑法(先起 dev 后端,固定占位 token 沿用 `scripts/smoke_api.sh` 同款惯例 ——
//  纯本地联调用,非真实密钥):
//    DB_PATH=/tmp/neckline_it.db API_TOKEN=smoke_token_at_least_16_chars_long \
//      NECKLINE_ENABLE_MORNING_TASKS=0 .venv/bin/python -m uvicorn neckline.api.app:app \
//      --host 127.0.0.1 --port 8002
//
//  ⚠️ **只打 127.0.0.1:8002,任何情况下都不得指向 prod**(nk.linotsai.top)——
//     这批测试会真实改设置、真实写结论,⛔ 绝不能碰生产台账。
//
//  🔴 **本版这批测试全部是「读」+「设置屏那一条可复原的写」**:
//  K9 的写入口只有两个(改预案 / 存结论),它们都是 **append-only** 的 ——
//  真跑一次就会在 dev 库里多留一版,⛔ 不放进常跑的冒烟里(它们由后端的
//  `smoke_api.sh` 39–46 步覆盖,那边跑在**临时库**上)。
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
        // 顺带冒烟一下 version 不为空(⛔ 不锁具体值,避免与「本地跑的是哪个版本」耦合)。
        XCTAssertNotNil(health.version)
    }

    /// 选股:`GET /selection/latest` 真请求。
    ///
    /// 🔴 **空库也是 200**(`state='not_run'` 的空态)—— ⛔ 这条测试**刻意不断言
    /// 「今天有清单」**:参数未标定之前每天都是「今天没跑成 · 参数未配置」,
    /// 那是**设计行为**(裁定 5 / §9.5)。能断言的是**三态真的解出来了**。
    func testSelectionLatestRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let snap = try await makeClient().fetchSelectionLatest()
        XCTAssertNotNil(snap.state, "三态必须解得出来 —— 解不出说明契约漂了")
        XCTAssertFalse(snap.headlineText.isEmpty, "首行即可分辨,⛔ 不许是空串")
        if snap.state == .notRun {
            XCTAssertNil(snap.listingSize, "「今天没跑成」的 listingSize 是 null,⛔ 不是 0")
        }
        // 逐只摘要:有清单就该有票,且**上方机械空间缺席是合法的**(p2/p4 不看这一项)。
        if snap.state == .hasList {
            XCTAssertFalse(snap.stocks.isEmpty)
            for s in snap.stocks {
                XCTAssertFalse(s.tsCode.isEmpty)
                XCTAssertFalse(s.primaryPattern.isEmpty)
            }
        }
    }

    /// 次日核对表:**404 是常态**(一天里只有 9:26 之后、且 D0 真出过清单才有)。
    /// ⛔ 这条测试不许把 404 判成失败 —— 那正是「合法空态」。
    func testChecklistRealRequestTreats404AsALegitimateEmptyState() async throws {
        try await skipUnlessDevServerReachable()
        let today = StaticTradingCalendar.shared.compactString(Date())
        do {
            let list = try await makeClient().fetchChecklist(tradeDate: today)
            // 🔴 跑过了 → **恰好两段**,⛔ 没有「成立」。
            XCTAssertEqual(list.segments.count, 2)
            XCTAssertFalse(list.footnote.isEmpty)
            for seg in list.segments {
                XCTAssertNotEqual(seg.displayLabel, "已触发成立")
                XCTAssertFalse(seg.displayLabel.contains("成立"))
            }
        } catch let e as APIError where e.isNotFound {
            // 那天没跑过那一拍 —— 合法。
        }
    }

    /// 成绩:覆盖率恒 200(空库 = 空数组);终值端点恒 200(那天没有 = 空数组)。
    func testScoreboardRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let client = makeClient()
        let coverage = try await client.fetchCoverage(window: 5)
        XCTAssertGreaterThan(coverage.window, 0)
        for d in coverage.days {
            // 🔴 NULL 不是 0:覆盖率两个口径都可能是 null,⛔ 客户端不许当 0。
            if d.coverageAll == nil { XCTAssertNil(d.coveredCount) }
        }
        let today = StaticTradingCalendar.shared.compactString(Date())
        let verdicts = try await client.fetchVerdicts(tradeDate: today)
        XCTAssertEqual(verdicts.tradeDate, today)
        for v in verdicts.verdicts where v.isUndecided {
            XCTAssertNil(v.decidedStage, "「还没定案」两列必须都是 null")
        }
    }

    /// 复盘聚合读:**恒 200**,空态走各段自己的 `available`。
    func testReviewOverviewRealRequest() async throws {
        try await skipUnlessDevServerReachable()
        let ov = try await makeClient().fetchReviewOverview()
        XCTAssertFalse(ov.weekKey.isEmpty, "ISO 周键由服务端给 —— ⛔ 客户端不自己算")
        // 「没有」与「没看」分开:`available=true` + `found=false` = 这周没传过交割单。
        if ov.reconcile.available { XCTAssertNotNil(ov.reconcile.found) }
        else { XCTAssertNotNil(ov.reconcile.unavailableReason, "没取到必须给得出原因") }
    }

    /// 设置真请求闭环:GET → POST provider(key 只发一次)→ GET(**确认只回 keySet
    /// 布尔、绝不回明文**)→ PUT push(按 kind 全量覆盖)→ GET → DELETE 清理。
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
}
