import XCTest
@testable import Neckline

/// KX V4 会话状态测试：盘中彻底静默、手动刷新与容错、派系直滤与搜索、iOS 推送路由。
@MainActor
final class KXBoardModelTests: XCTestCase {
    func testIntradaySilenceLoadsOnceAndOnlyManualRefreshForcesFetch() async throws {
        let board = try loadFixture("kx_board_v4")
        let client = FakeKXClient(outcomes: [.success(board), .success(board)])
        let model = KXBoardModel(config: makeConfig(), clientFactory: { _ in client })

        // 首次进入页面加载一次（非强制刷新）
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false])
        XCTAssertEqual(model.board?.version, board.version)
        XCTAssertEqual(model.selectedItem?.code, board.items.first?.code)

        // 已有快照后再次调用 loadIfNeeded 彻底静默，不再请求
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false])

        // 手动下拉 / 点按钮刷新发 forceRefresh = true
        await model.refresh()
        XCTAssertEqual(client.recordedForceFlags, [false, true])
        XCTAssertFalse(model.refreshFailed)
    }

    func testTransientErrorKeepsExistingSnapshotWhileUnsupportedSchemaReplacesState() async throws {
        let board = try loadFixture("kx_board_v4")
        let client = FakeKXClient(outcomes: [
            .success(board),
            .failure(KXBoardError.unreachable),
            .failure(KXBoardError.unsupportedSchema("需要 schema 5")),
        ])
        let model = KXBoardModel(config: makeConfig(), clientFactory: { _ in client })

        await model.loadIfNeeded()
        XCTAssertEqual(model.board?.version, board.version)

        // 暂时连不上：保留已有盘后快照，仅标 refreshFailed
        await model.refresh()
        XCTAssertEqual(model.board?.version, board.version)
        XCTAssertTrue(model.refreshFailed)

        // 协议不兼容：明确错误态，不硬解
        await model.refresh()
        XCTAssertNil(model.board)
        XCTAssertEqual(model.state, .error(.unsupportedSchema("需要 schema 5")))
        XCTAssertFalse(model.refreshFailed)
    }

    func testFactionDirectFilterAndSearchReconcileSelection() async throws {
        let board = try loadFixture("kx_board_v4")
        let client = FakeKXClient(outcomes: [.success(board)])
        let model = KXBoardModel(config: makeConfig(), clientFactory: { _ in client })
        await model.loadIfNeeded()

        let topFaction = try XCTUnwrap(board.factions.first)
        model.selectedFaction = topFaction.name
        XCTAssertEqual(model.filteredItems.count, topFaction.count)
        XCTAssertTrue(model.filteredItems.allSatisfy { item in
            item.judgment.factions.contains(where: { $0.name == topFaction.name })
        })
        XCTAssertNotNil(model.selectedItem)
        XCTAssertTrue(model.filteredItems.contains(where: { $0.code == model.selectedItem?.code }))

        // 搜索股票代码直接过滤
        model.resetFilters()
        model.searchText = "002086"
        XCTAssertEqual(model.filteredItems.map(\.code), ["002086"])
        XCTAssertEqual(model.selectedItem?.code, "002086")

        // 无匹配时清空选中，重置筛选后恢复
        model.searchText = "不存在的关键词xyz"
        XCTAssertTrue(model.filteredItems.isEmpty)
        XCTAssertNil(model.selectedItem)

        model.resetFilters()
        XCTAssertEqual(model.filteredItems.count, board.items.count)
        XCTAssertNotNil(model.selectedItem)
    }

    func testPushTapDismissesOpenDetailAndRefreshes() async throws {
        let board = try loadFixture("kx_board_v4")
        let client = FakeKXClient(outcomes: [.success(board), .success(board)])
        let model = KXBoardModel(config: makeConfig(), clientFactory: { _ in client })
        await model.loadIfNeeded()
        model.openDetail("002771")
        XCTAssertEqual(model.presentedDetailCode, "002771")

        await model.handlePushTap()
        XCTAssertNil(model.presentedDetailCode)
        XCTAssertEqual(client.recordedForceFlags, [false, true])
    }

    func testConnectionChangeReloadsWhenVisible() async throws {
        let board = try loadFixture("kx_board_v4")
        let config = makeConfig()
        let client = FakeKXClient(outcomes: [.success(board), .success(board)])
        let model = KXBoardModel(config: config, clientFactory: { _ in client })
        model.setVisible(true)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false])

        config.apiToken = "another-token-value"
        await model.handleConnectionChange()
        XCTAssertEqual(client.recordedForceFlags, [false, false])
        XCTAssertEqual(model.board?.version, board.version)
    }

    func testCrossDayMorningReopenFetchesOnceQuietlyAfter2000Cutoff() async throws {
        let board = try loadFixture("kx_board_v4")
        let client = FakeKXClient(outcomes: [.success(board), .success(board)])
        var current = cstDate(2026, 11, 23, 19, 0)
        let model = KXBoardModel(config: makeConfig(), clientFactory: { _ in client }, now: { current })

        // 周一 19:00 首次进入：加载一次
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false])

        // 周一 19:30（未跨 20:00）：静默不请求
        current = cstDate(2026, 11, 23, 19, 30)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false])

        // 周二 09:00（已跨周一 20:00 盘后窗口）：安静按需拉一次（非强刷）
        current = cstDate(2026, 11, 24, 9, 0)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false, false])

        // 周二 10:30（同一窗口盘中）：彻底静默
        current = cstDate(2026, 11, 24, 10, 30)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false, false])
    }

    func testStaleSnapshotLoadedAfter2000CatchesUpOnceNextMorningThenStaysSilent() async throws {
        // P2-6: 周一 20:02 读到上周五（2026-11-20）旧板，周一 20:08 上游更新；
        // 周二 09:00 回到驻留的 KX 页必须安静补拉一次，随后周二白天彻底静默。
        let oldBoard = try loadFixture("kx_board_v4") // asOf = 2026-11-20
        let client = FakeKXClient(outcomes: [.success(oldBoard), .success(oldBoard)])
        var current = cstDate(2026, 11, 23, 20, 2)
        let model = KXBoardModel(config: makeConfig(), clientFactory: { _ in client }, now: { current })

        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false])

        // 次日清晨 09:00：昨夜读到的是更早交易日旧板，安静补拉一次
        current = cstDate(2026, 11, 24, 9, 0)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false, false])

        // 即使补拉结果仍是周五板（例如周一休市），周二盘中 10:30 也不得反复请求
        current = cstDate(2026, 11, 24, 10, 30)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false, false])
    }

    func testMorningQuietCatchupFailureDoesNotSpamIntradayOnForegroundReentry() async throws {
        // Round-4 P2-6: 昨 19:00 读过旧板，今晨 09:00 补拉遇临时断网失败，盘中再次切回前台绝不反复重试
        let oldBoard = try loadFixture("kx_board_v4")
        let client = FakeKXClient(outcomes: [.success(oldBoard), .failure(KXBoardError.unreachable), .success(oldBoard)])
        var current = cstDate(2026, 11, 23, 19, 0)
        let model = KXBoardModel(config: makeConfig(), clientFactory: { _ in client }, now: { current })

        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false])

        // 次晨 09:00 补拉一次失败：保留旧板并标记 refreshFailed
        current = cstDate(2026, 11, 24, 9, 0)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false, false])
        XCTAssertTrue(model.refreshFailed)

        // 盘中 10:30 再次切回前台：同一白天不再自动重发
        current = cstDate(2026, 11, 24, 10, 30)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false, false])
    }

    private func cstDate(_ year: Int, _ month: Int, _ day: Int, _ hour: Int, _ minute: Int) -> Date {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "Asia/Shanghai")!
        return cal.date(from: DateComponents(year: year, month: month, day: day, hour: hour, minute: minute))!
    }

    #if os(iOS)
    func testPushTargetRoutingDistinguishesKXAndMorning() {
        XCTAssertEqual(NecklinePushTarget.parse(userInfo: ["target": "kx"]), .kx)
        XCTAssertEqual(NecklinePushTarget.parse(userInfo: ["target": "KX"]), .kx)
        XCTAssertEqual(NecklinePushTarget.parse(userInfo: ["target": "morning"]), .morning)
        XCTAssertEqual(NecklinePushTarget.parse(userInfo: nil), .morning)
    }
    #endif

    // MARK: Helpers

    private func makeConfig() -> AppConfig {
        let defaults = UserDefaults(suiteName: "top.linotsai.neckline.test.kx.\(UUID().uuidString)")!
        let config = AppConfig(defaults: defaults, tokenStore: MemoryTokenStore(), loadPersistentCredentials: false)
        config.apiToken = "fixture-token-not-real"
        config.baseURLOverride = "https://fixture.invalid"
        return config
    }

    private func loadFixture(_ name: String) throws -> KXBoard {
        let bundle = Bundle(for: KXBoardModelTests.self)
        let url = bundle.url(forResource: name, withExtension: "json")
            ?? bundle.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
        let data = try Data(contentsOf: try XCTUnwrap(url, "缺少夹具 \(name).json"))
        return try JSONDecoder().decode(KXBoard.self, from: data)
    }
}

private final class FakeKXClient: KXBoardClient, @unchecked Sendable {
    private let lock = NSLock()
    private var outcomes: [Result<KXBoard, Error>]
    private var flags: [Bool] = []

    init(outcomes: [Result<KXBoard, Error>]) {
        self.outcomes = outcomes
    }

    var recordedForceFlags: [Bool] {
        lock.lock()
        defer { lock.unlock() }
        return flags
    }

    func board(forceRefresh: Bool) async throws -> KXBoard {
        let next: Result<KXBoard, Error> = lock.withLock {
            flags.append(forceRefresh)
            if outcomes.isEmpty {
                return .failure(KXBoardError.unreachable)
            }
            return outcomes.removeFirst()
        }
        return try next.get()
    }
}

private final class MemoryTokenStore: APIAccessTokenStore {
    func load() -> String? { nil }
    func save(_ token: String) -> Bool { true }
}
