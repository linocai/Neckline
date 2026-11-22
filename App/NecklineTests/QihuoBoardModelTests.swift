import XCTest
@testable import Neckline

/// Q 期货看板会话状态测试：默认首卡展开（Option A）、全展/全收、卡内分页与基本面二次折叠、盘中静默与跨 20:15 窗口补拉。
@MainActor
final class QihuoBoardModelTests: XCTestCase {
    func testInitialLoadExpandsFirstCardOnlyAndSupportsExpandCollapseBasic() async throws {
        let board = try loadFixture("qihuo_board_v3")
        let client = FakeQihuoClient(outcomes: [.success(board), .success(board)])
        let model = QihuoBoardModel(config: makeConfig(), clientFactory: { _ in client })

        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false])
        XCTAssertEqual(model.board?.version, board.version)
        // Option A：首次加载默认展开第 1 张（sh），其余收起
        XCTAssertEqual(model.expandedSlugs, ["sh"])
        XCTAssertTrue(model.isExpanded("sh"))
        XCTAssertFalse(model.isExpanded("l"))
        XCTAssertFalse(model.isBasicExpanded("sh"))

        // 切换卡片展开、卡内 tab 与基本面二次折叠
        model.toggleCard("l")
        XCTAssertTrue(model.isExpanded("l"))
        model.setTab(.holding, for: "sh")
        XCTAssertEqual(model.tab(for: "sh"), .holding)
        model.toggleBasic("sh")
        XCTAssertTrue(model.isBasicExpanded("sh"))

        // 全部展开 / 全部收起
        model.expandAll()
        XCTAssertEqual(model.expandedSlugs, Set(["sh", "l", "ma", "pp", "eg"]))
        model.collapseAll()
        XCTAssertTrue(model.expandedSlugs.isEmpty)

        // 手动刷新保留用户交互状态（不会重新强行展开首卡）
        await model.refresh()
        XCTAssertEqual(client.recordedForceFlags, [false, true])
        XCTAssertTrue(model.expandedSlugs.isEmpty)
        XCTAssertTrue(model.isBasicExpanded("sh"))
    }

    func testTransientErrorKeepsSnapshotWhileUnsupportedSchemaReplacesState() async throws {
        let board = try loadFixture("qihuo_board_v3")
        let client = FakeQihuoClient(outcomes: [
            .success(board),
            .failure(QihuoBoardError.unreachable),
            .failure(QihuoBoardError.unsupportedSchema("需要 schema 3")),
        ])
        let model = QihuoBoardModel(config: makeConfig(), clientFactory: { _ in client })

        await model.loadIfNeeded()
        XCTAssertEqual(model.board?.version, board.version)

        await model.refresh()
        XCTAssertEqual(model.board?.version, board.version)
        XCTAssertTrue(model.refreshFailed)

        await model.refresh()
        XCTAssertNil(model.board)
        XCTAssertEqual(model.state, .error(.unsupportedSchema("需要 schema 3")))
        XCTAssertFalse(model.refreshFailed)
    }

    func testIntradaySilenceAndCross2015QuietRefreshWithoutMorningSpam() async throws {
        let board = try loadFixture("qihuo_board_v3")
        let client = FakeQihuoClient(outcomes: [
            .success(board),
            .failure(QihuoBoardError.unreachable),
            .success(board),
        ])
        var current = cstDate(2026, 11, 23, 19, 0)
        let model = QihuoBoardModel(config: makeConfig(), clientFactory: { _ in client }, now: { current })

        // 周一 19:00 首次加载
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false])

        // 周一 20:10（尚未跨 20:15）：彻底静默
        current = cstDate(2026, 11, 23, 20, 10)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false])

        // 周二 09:00（已跨周一 20:15 同步窗口）：安静补拉一次，若断网失败，盘中不再反复打扰
        current = cstDate(2026, 11, 24, 9, 0)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false, false])
        XCTAssertTrue(model.refreshFailed)

        // 周二 10:30 盘中切回：彻底静默
        current = cstDate(2026, 11, 24, 10, 30)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false, false])

        // 周二 20:20 跨过当日 20:15 窗口：安静拉一次
        current = cstDate(2026, 11, 24, 20, 20)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false, false, false])
        XCTAssertFalse(model.refreshFailed)

        // 同一晚间窗口（20:30）再次切回前台：绝不重复自动拉取
        current = cstDate(2026, 11, 24, 20, 30)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false, false, false])
    }

    func testColdStartTransientErrorRecoversOnNextWindowWithoutSpammingCurrentWindow() async throws {
        let board = try loadFixture("qihuo_board_v3")
        let client = FakeQihuoClient(outcomes: [
            .failure(QihuoBoardError.unreachable),
            .success(board),
        ])
        var current = cstDate(2026, 11, 23, 19, 0)
        let model = QihuoBoardModel(config: makeConfig(), clientFactory: { _ in client }, now: { current })

        // 冷启动临时断网进入 .error(.unreachable)
        await model.loadIfNeeded()
        XCTAssertEqual(model.state, .error(.unreachable))
        XCTAssertEqual(client.recordedForceFlags, [false])

        // 同窗口（19:30）不再反复重发
        current = cstDate(2026, 11, 23, 19, 30)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false])

        // 跨入晚间新窗口（20:20）：自动给一次恢复机会并成功
        current = cstDate(2026, 11, 23, 20, 20)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false, false])
        XCTAssertEqual(model.board?.version, board.version)
    }

    func testUnsupportedSchemaRecoversOnNextWindowWhenServerRestoresV3() async throws {
        let board = try loadFixture("qihuo_board_v3")
        let client = FakeQihuoClient(outcomes: [
            .failure(QihuoBoardError.unsupportedSchema("收到 schema 4")),
            .success(board),
        ])
        var current = cstDate(2026, 11, 23, 19, 0)
        let model = QihuoBoardModel(config: makeConfig(), clientFactory: { _ in client }, now: { current })

        await model.loadIfNeeded()
        XCTAssertEqual(model.state, .error(.unsupportedSchema("收到 schema 4")))

        // 同一窗口不重复打扰
        current = cstDate(2026, 11, 23, 19, 30)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false])

        // 跨入晚间新窗口自动复核一次，服务端恢复 v3 后自动恢复展示
        current = cstDate(2026, 11, 23, 20, 20)
        await model.loadIfNeeded()
        XCTAssertEqual(client.recordedForceFlags, [false, false])
        XCTAssertEqual(model.board?.version, board.version)
    }

    func testConnectionChangeResetsStateAndReloadsWhenVisible() async throws {
        let board = try loadFixture("qihuo_board_v3")
        let config = makeConfig()
        let client = FakeQihuoClient(outcomes: [.success(board), .success(board)])
        let model = QihuoBoardModel(config: config, clientFactory: { _ in client })
        model.setVisible(true)
        await model.loadIfNeeded()
        model.collapseAll()
        XCTAssertTrue(model.expandedSlugs.isEmpty)

        config.apiToken = "another-token-value"
        await model.handleConnectionChange()
        XCTAssertEqual(client.recordedForceFlags, [false, false])
        // 连接切换重新初始化后再次默认展开首卡
        XCTAssertEqual(model.expandedSlugs, ["sh"])
    }

    // MARK: Helpers

    private func cstDate(_ year: Int, _ month: Int, _ day: Int, _ hour: Int, _ minute: Int) -> Date {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "Asia/Shanghai")!
        return cal.date(from: DateComponents(year: year, month: month, day: day, hour: hour, minute: minute))!
    }

    private func makeConfig() -> AppConfig {
        let defaults = UserDefaults(suiteName: "top.linotsai.neckline.test.qihuo.\(UUID().uuidString)")!
        let config = AppConfig(defaults: defaults, tokenStore: QihuoMemoryTokenStore(), loadPersistentCredentials: false)
        config.apiToken = "fixture-token-not-real"
        config.baseURLOverride = "https://fixture.invalid"
        return config
    }

    private func loadFixture(_ name: String) throws -> QihuoBoard {
        let bundle = Bundle(for: QihuoBoardModelTests.self)
        let url = bundle.url(forResource: name, withExtension: "json")
            ?? bundle.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
        let data = try Data(contentsOf: try XCTUnwrap(url, "缺少夹具 \(name).json"))
        return try JSONDecoder().decode(QihuoBoard.self, from: data)
    }
}

private final class FakeQihuoClient: QihuoBoardClient, @unchecked Sendable {
    private let lock = NSLock()
    private var outcomes: [Result<QihuoBoard, Error>]
    private var flags: [Bool] = []

    init(outcomes: [Result<QihuoBoard, Error>]) {
        self.outcomes = outcomes
    }

    var recordedForceFlags: [Bool] {
        lock.lock()
        defer { lock.unlock() }
        return flags
    }

    func board(forceRefresh: Bool) async throws -> QihuoBoard {
        let next: Result<QihuoBoard, Error> = lock.withLock {
            flags.append(forceRefresh)
            if outcomes.isEmpty {
                return .failure(QihuoBoardError.unreachable)
            }
            return outcomes.removeFirst()
        }
        return try next.get()
    }
}

private final class QihuoMemoryTokenStore: APIAccessTokenStore {
    func load() -> String? { nil }
    func save(_ token: String) -> Bool { true }
}
