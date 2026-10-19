import XCTest
@testable import Neckline

/// KX 看板的状态与刷新调度：前台、活跃、交易时段三个条件都满足才自动续取，间隔用接口给的秒数。
@MainActor
final class KXBoardModelTests: XCTestCase {
    private var suites: [String] = []

    override func tearDown() {
        for name in suites {
            UserDefaults(suiteName: name)?.removePersistentDomain(forName: name)
        }
        suites = []
        super.tearDown()
    }

    // MARK: 调度策略

    func testRefreshPolicyNeedsVisibleActiveAndInSession() throws {
        let inSession = try board()
        let closed = try board { object in
            var session = object["session"] as! [String: Any]
            session["inSession"] = false
            session["refreshSeconds"] = NSNull()
            object["session"] = session
        }
        let custom = try board { object in
            var session = object["session"] as! [String: Any]
            session["refreshSeconds"] = 45
            object["session"] = session
        }
        let zero = try board { object in
            var session = object["session"] as! [String: Any]
            session["refreshSeconds"] = 0
            object["session"] = session
        }
        let noInterval = try board { object in
            var session = object["session"] as! [String: Any]
            session["refreshSeconds"] = NSNull()
            object["session"] = session
        }

        XCTAssertEqual(KXRefreshPolicy.interval(visible: true, active: true, board: inSession), 60)
        XCTAssertEqual(KXRefreshPolicy.interval(visible: true, active: true, board: custom), 45, "间隔以接口为准")
        XCTAssertNil(KXRefreshPolicy.interval(visible: false, active: true, board: inSession), "切到别的标签就停")
        XCTAssertNil(KXRefreshPolicy.interval(visible: true, active: false, board: inSession), "后台或锁屏就停")
        XCTAssertNil(KXRefreshPolicy.interval(visible: false, active: false, board: inSession))
        XCTAssertNil(KXRefreshPolicy.interval(visible: true, active: true, board: closed), "非交易时段只取一次")
        XCTAssertNil(KXRefreshPolicy.interval(visible: true, active: true, board: nil), "没有拿到看板不续取")
        XCTAssertNil(KXRefreshPolicy.interval(visible: true, active: true, board: zero))
        XCTAssertNil(KXRefreshPolicy.interval(visible: true, active: true, board: noInterval))
        XCTAssertEqual(KXRefreshPolicy.interval(for: inSession), 60)
    }

    // MARK: 自动续取

    func testVisibleInSessionFetchesImmediatelyThenEverySixtySeconds() async throws {
        let client = FakeKXClient(.success(try board()))
        let sleeper = TickSleeper()
        let model = makeModel(client: client, sleeper: sleeper)
        XCTAssertEqual(client.calls, 0, "没打开页面前不取")

        model.setVisible(true)
        let first = await waitUntil { client.calls == 1 && sleeper.waiting == 1 }
        XCTAssertTrue(first, "页面出现时取一次，然后等 60 秒")
        XCTAssertEqual(sleeper.seconds, [60])
        guard case .ready(let loaded) = model.state else { return XCTFail("应有数据") }
        XCTAssertEqual(loaded.rows.count, 12)

        sleeper.tick()
        let second = await waitUntil { client.calls == 2 && sleeper.waiting == 1 }
        XCTAssertTrue(second, "过了 60 秒再取一次")
        XCTAssertEqual(sleeper.seconds, [60, 60])
        XCTAssertTrue(model.isAutoRefreshing)
    }

    func testIntervalComesFromTheInterface() async throws {
        let client = FakeKXClient(.success(try board { object in
            var session = object["session"] as! [String: Any]
            session["refreshSeconds"] = 45
            object["session"] = session
        }))
        let sleeper = TickSleeper()
        let model = makeModel(client: client, sleeper: sleeper)
        model.setVisible(true)
        let waiting = await waitUntil { sleeper.waiting == 1 }
        XCTAssertTrue(waiting)
        XCTAssertEqual(sleeper.seconds, [45])
    }

    func testOutOfSessionFetchesOnlyOnce() async throws {
        let client = FakeKXClient(.success(try closedBoard()))
        let sleeper = TickSleeper()
        let model = makeModel(client: client, sleeper: sleeper)
        model.setVisible(true)
        let done = await waitUntil { client.calls == 1 && !model.isAutoRefreshing }
        XCTAssertTrue(done, "非交易时段取一次就结束")
        XCTAssertTrue(sleeper.seconds.isEmpty)
        XCTAssertEqual(KXCopy.meta(board: try XCTUnwrap(model.board), refreshFailed: false), "10:31 更新 · 已收盘")
        XCTAssertEqual(client.calls, 1)
    }

    func testBackgroundStopsAndForegroundFetchesAgain() async throws {
        let client = FakeKXClient(.success(try board()))
        let sleeper = TickSleeper()
        let model = makeModel(client: client, sleeper: sleeper)
        model.setVisible(true)
        let started = await waitUntil { client.calls == 1 && sleeper.waiting == 1 }
        XCTAssertTrue(started)

        model.setActive(false)
        let stopped = await waitUntil { sleeper.waiting == 0 && !model.isAutoRefreshing }
        XCTAssertTrue(stopped, "进入后台、锁屏就停")
        sleeper.tick()
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(client.calls, 1, "停下以后不再取")

        model.setActive(true)
        let resumed = await waitUntil { client.calls == 2 && sleeper.waiting == 1 }
        XCTAssertTrue(resumed, "回到前台立刻取一次，再接着续取")
    }

    func testLeavingTheTabStopsAndComingBackFetchesAgain() async throws {
        let client = FakeKXClient(.success(try board()))
        let sleeper = TickSleeper()
        let model = makeModel(client: client, sleeper: sleeper)
        model.setVisible(true)
        let started = await waitUntil { client.calls == 1 && sleeper.waiting == 1 }
        XCTAssertTrue(started)

        model.setVisible(false)
        let stopped = await waitUntil { sleeper.waiting == 0 && !model.isAutoRefreshing }
        XCTAssertTrue(stopped, "切到别的标签就停")
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(client.calls, 1)

        model.setVisible(true)
        let back = await waitUntil { client.calls == 2 && sleeper.waiting == 1 }
        XCTAssertTrue(back)
    }

    func testRepeatedVisibilityReportsDoNotFetchTwice() async throws {
        let client = FakeKXClient(.success(try board()))
        let sleeper = TickSleeper()
        let model = makeModel(client: client, sleeper: sleeper)
        model.setVisible(true)
        model.setVisible(true)
        model.setActive(true)
        let started = await waitUntil { client.calls == 1 && sleeper.waiting == 1 }
        XCTAssertTrue(started)
        model.setVisible(true)
        model.setActive(true)
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(client.calls, 1)
        XCTAssertEqual(sleeper.seconds.count, 1)
    }

    func testInactiveAppDoesNotFetchUntilActive() async throws {
        let client = FakeKXClient(.success(try board()))
        let sleeper = TickSleeper()
        let model = makeModel(client: client, sleeper: sleeper)
        model.setActive(false)
        model.setVisible(true)
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(client.calls, 0, "App 不活跃时页面出现也不取")
        model.setActive(true)
        let fetched = await waitUntil { client.calls == 1 }
        XCTAssertTrue(fetched)
    }

    func testPullToRefreshFetchesOnceAndKeepsSchedulingOff() async throws {
        let client = FakeKXClient(.success(try board()))
        let sleeper = TickSleeper()
        let model = makeModel(client: client, sleeper: sleeper)
        await model.refresh()
        XCTAssertEqual(client.calls, 1)
        XCTAssertNotNil(model.board)
        XCTAssertFalse(model.isAutoRefreshing, "页面不在前台时，下拉刷新不启动自动续取")
        XCTAssertTrue(sleeper.seconds.isEmpty)
    }

    func testManualRefreshResumesTheLoopAfterAFailedFirstFetch() async throws {
        let client = FakeKXClient(.failure(KXBoardError.unreachable))
        let sleeper = TickSleeper()
        let model = makeModel(client: client, sleeper: sleeper)
        model.setVisible(true)
        let failed = await waitUntil { client.calls == 1 && !model.isAutoRefreshing }
        XCTAssertTrue(failed, "没拿到看板时循环结束")
        XCTAssertEqual(model.state, .error(.unreachable))

        client.result = .success(try board())
        await model.refresh()
        XCTAssertEqual(client.calls, 2, "下拉刷新只取一次")
        XCTAssertNotNil(model.board)
        let resumed = await waitUntil { sleeper.waiting == 1 }
        XCTAssertTrue(resumed, "刷新成功后接着自动续取")
        XCTAssertEqual(client.calls, 2, "恢复续取时不重复取")
    }

    // MARK: 状态

    func testStartsLoadingThenShowsBoard() async throws {
        let client = FakeKXClient(.success(try board()))
        let model = makeModel(client: client)
        XCTAssertEqual(model.state, .loading)
        XCTAssertNil(model.board)
        await model.refresh()
        XCTAssertEqual(model.state, .ready(try board()))
        XCTAssertFalse(model.refreshFailed)
    }

    func testServerRefusalsReplaceTheState() async throws {
        for failure: KXBoardError in [
            .missingToken, .unauthorized, .notConfigured, .dbNotReady,
            .poolUnverified("KX 名单还没有成功同步过，暂不出看板：KX 暂时连不上。"),
            .calendarGap("交易日历没有覆盖今天"), .unreachable, .unreadable,
        ] {
            let client = FakeKXClient(.failure(failure))
            let model = makeModel(client: client)
            await model.refresh()
            XCTAssertEqual(model.state, .error(failure))
        }
        let model = makeModel(client: FakeKXClient(.failure(KXBoardError.poolUnverified("原因"))))
        await model.refresh()
        let message = KXCopy.message(for: .poolUnverified("原因"))
        XCTAssertEqual(message.title, "KX 名单还没有同步")
        XCTAssertEqual(message.detail, "原因")
    }

    func testTransientFailureKeepsTheBoardAndRecovers() async throws {
        let client = FakeKXClient(.success(try board()))
        let model = makeModel(client: client)
        await model.refresh()
        let shown = model.board

        client.result = .failure(KXBoardError.unreachable)
        await model.refresh()
        XCTAssertEqual(model.board, shown, "连不上时保留已有数据")
        XCTAssertTrue(model.refreshFailed)
        XCTAssertEqual(KXCopy.meta(board: try XCTUnwrap(model.board), refreshFailed: model.refreshFailed), "10:31 更新 · 刷新失败")

        client.result = .failure(KXBoardError.unreadable)
        await model.refresh()
        XCTAssertEqual(model.board, shown)
        XCTAssertTrue(model.refreshFailed)

        client.result = .success(try board())
        await model.refresh()
        XCTAssertFalse(model.refreshFailed)
        XCTAssertEqual(model.board, shown)
    }

    func testServerRefusalAfterDataDropsTheStaleBoard() async throws {
        let client = FakeKXClient(.success(try board()))
        let model = makeModel(client: client)
        await model.refresh()
        XCTAssertNotNil(model.board)
        client.result = .failure(KXBoardError.unauthorized)
        await model.refresh()
        XCTAssertEqual(model.state, .error(.unauthorized), "令牌被拒后不留着旧行情")
        XCTAssertNil(model.board)
        XCTAssertFalse(model.refreshFailed)
    }

    func testRefreshKeepsCurrentGroupAndReplacesRowsInPlace() async throws {
        let client = FakeKXClient(.success(try board()))
        let model = makeModel(client: client)
        XCTAssertEqual(model.group, .overview)
        await model.refresh()
        model.group = .trend

        let updated = try board { object in
            var rows = object["rows"] as! [[String: Any]]
            rows[0]["price"] = 13.88
            object["rows"] = rows
        }
        client.result = .success(updated)
        await model.refresh()
        XCTAssertEqual(model.group, .trend, "刷新不重置分组")
        XCTAssertEqual(model.board?.rows.first?.price, 13.88)
        XCTAssertNotEqual(model.state, .loading, "刷新时不退回读取中，页面不闪")
    }

    func testPickChangesMoveRowsBetweenSectionsOnRefresh() async throws {
        let client = FakeKXClient(.success(try board()))
        let model = makeModel(client: client)
        await model.refresh()
        XCTAssertEqual(model.board?.sections.map(\.rows.count), [4, 1, 7])
        client.result = .success(try board { object in
            var rows = object["rows"] as! [[String: Any]]
            rows[4]["highlight"] = "normal"
            object["rows"] = rows
        })
        await model.refresh()
        XCTAssertEqual(model.board?.sections.map(\.section), [.today, .all])
        XCTAssertEqual(model.board?.sections.map(\.rows.count), [4, 8])
    }

    // MARK: 连接变化

    func testConnectionChangeResetsAndIgnoresStaleResponses() async throws {
        let first = FakeKXClient(.success(try board()))
        let second = FakeKXClient(.success(try closedBoard()))
        var clients: [FakeKXClient] = [first, second]
        let defaults = isolatedDefaults()
        let config = makeConfig(defaults: defaults, token: "fixture-token-not-real")
        let sleeper = TickSleeper()
        let model = KXBoardModel(config: config, clientFactory: { _ in clients.removeFirst() }, sleep: { try await sleeper.sleep($0) })

        first.hold = true
        model.setVisible(true)
        let inFlight = await waitUntil { first.calls == 1 }
        XCTAssertTrue(inFlight)

        config.apiToken = "another-fixture-token"
        await model.handleConnectionChange()
        XCTAssertEqual(model.state, .loading, "换了令牌先清空")
        let refetched = await waitUntil { second.calls == 1 && !model.isAutoRefreshing }
        XCTAssertTrue(refetched, "页面在前台时用新连接重新取")

        first.release(.success(try board()))
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(model.board?.session.inSession, false, "旧连接晚到的响应被丢掉")
    }

    func testUnchangedConnectionIsIgnored() async throws {
        let client = FakeKXClient(.success(try board()))
        let model = makeModel(client: client)
        await model.refresh()
        await model.handleConnectionChange()
        XCTAssertNotEqual(model.state, .loading)
        XCTAssertEqual(client.calls, 1)
    }

    // MARK: 辅助

    private func makeModel(client: FakeKXClient, sleeper: TickSleeper = TickSleeper()) -> KXBoardModel {
        let config = makeConfig(defaults: isolatedDefaults(), token: "fixture-token-not-real")
        return KXBoardModel(config: config, clientFactory: { _ in client }, sleep: { try await sleeper.sleep($0) })
    }

    private func makeConfig(defaults: UserDefaults, token: String) -> AppConfig {
        let config = AppConfig(defaults: defaults, tokenStore: KXMemoryTokenStore(), loadPersistentCredentials: false)
        config.apiToken = token
        return config
    }

    private func isolatedDefaults() -> UserDefaults {
        let name = "neckline.kx.tests.\(UUID().uuidString)"
        suites.append(name)
        let defaults = UserDefaults(suiteName: name)!
        defaults.removePersistentDomain(forName: name)
        return defaults
    }

    private func board(_ edit: (inout [String: Any]) -> Void = { _ in }) throws -> KXBoard {
        var object = try JSONSerialization.jsonObject(with: fixtureData("kx_board_sample")) as! [String: Any]
        edit(&object)
        return try JSONDecoder().decode(KXBoard.self, from: JSONSerialization.data(withJSONObject: object))
    }

    private func closedBoard() throws -> KXBoard {
        try board { object in
            var session = object["session"] as! [String: Any]
            session["inSession"] = false
            session["refreshSeconds"] = NSNull()
            object["session"] = session
        }
    }

    private func fixtureData(_ name: String) throws -> Data {
        let bundle = Bundle(for: KXBoardModelTests.self)
        let url = bundle.url(forResource: name, withExtension: "json")
            ?? bundle.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
        return try Data(contentsOf: try XCTUnwrap(url, "缺少夹具 \(name).json"))
    }

    /// 轮询到条件成立或超时。调度在独立任务里跑，测试靠它等到下一个稳定点。
    private func waitUntil(timeout: TimeInterval = 3, _ condition: () -> Bool) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() {
            if Date() > deadline { return false }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        return true
    }
}

private final class FakeKXClient: KXBoardClient, @unchecked Sendable {
    private let lock = NSLock()
    private var stored: Result<KXBoard, Error>
    private var heldContinuation: CheckedContinuation<KXBoard, Error>?
    private var callCount = 0
    private var holding = false

    init(_ result: Result<KXBoard, Error>) { stored = result }

    var result: Result<KXBoard, Error> {
        get { lock.lock(); defer { lock.unlock() }; return stored }
        set { lock.lock(); stored = newValue; lock.unlock() }
    }

    var calls: Int {
        lock.lock()
        defer { lock.unlock() }
        return callCount
    }

    /// 为 true 时下一次请求停住，直到 release。
    var hold: Bool {
        get { lock.lock(); defer { lock.unlock() }; return holding }
        set { lock.lock(); holding = newValue; lock.unlock() }
    }

    func release(_ result: Result<KXBoard, Error>) {
        lock.lock()
        let continuation = heldContinuation
        heldContinuation = nil
        holding = false
        lock.unlock()
        continuation?.resume(with: result)
    }

    func board() async throws -> KXBoard {
        lock.lock()
        callCount += 1
        let shouldHold = holding
        let current = stored
        lock.unlock()
        if shouldHold {
            return try await withCheckedThrowingContinuation { continuation in
                lock.lock()
                heldContinuation = continuation
                lock.unlock()
            }
        }
        return try current.get()
    }
}

/// 假的等待：记下每次要等多久，停住直到 tick；被取消就抛 CancellationError。
private final class TickSleeper: @unchecked Sendable {
    private final class Waiter {
        var continuation: CheckedContinuation<Void, Error>?
        var pending: Result<Void, Error>?
        var finished = false
    }

    private let lock = NSLock()
    private var waiters: [Waiter] = []
    private var requested: [TimeInterval] = []

    var seconds: [TimeInterval] {
        lock.lock()
        defer { lock.unlock() }
        return requested
    }

    /// 还停着的等待数。
    var waiting: Int {
        lock.lock()
        defer { lock.unlock() }
        return waiters.filter { !$0.finished }.count
    }

    func sleep(_ interval: TimeInterval) async throws {
        lock.lock()
        let index = waiters.count
        waiters.append(Waiter())
        requested.append(interval)
        lock.unlock()
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                self.attach(index, continuation)
            }
        } onCancel: {
            self.finish(index, .failure(CancellationError()))
        }
    }

    /// 让最早一个还停着的等待结束。
    func tick() {
        lock.lock()
        let index = waiters.firstIndex { !$0.finished }
        lock.unlock()
        if let index { finish(index, .success(())) }
    }

    private func attach(_ index: Int, _ continuation: CheckedContinuation<Void, Error>) {
        lock.lock()
        let waiter = waiters[index]
        if let pending = waiter.pending {
            lock.unlock()
            continuation.resume(with: pending)
            return
        }
        waiter.continuation = continuation
        lock.unlock()
    }

    private func finish(_ index: Int, _ result: Result<Void, Error>) {
        lock.lock()
        guard waiters.indices.contains(index), !waiters[index].finished else {
            lock.unlock()
            return
        }
        let waiter = waiters[index]
        waiter.finished = true
        let continuation = waiter.continuation
        waiter.continuation = nil
        if continuation == nil { waiter.pending = result }
        lock.unlock()
        continuation?.resume(with: result)
    }
}

private struct KXMemoryTokenStore: APIAccessTokenStore {
    func load() -> String? { nil }
    func save(_ token: String) -> Bool { true }
}
