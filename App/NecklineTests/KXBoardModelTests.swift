import XCTest
@testable import Neckline

/// KX 看板的状态与刷新调度：前台、活跃、交易时段三个条件都满足才自动续取。
/// 间隔用接口给的秒数，但不快于 60 秒。
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

    func testRefreshPolicyNeedsVisibleActiveAndInSession() throws {
        let inSession = try board()
        let closed = try board(live: false)
        let faster = try board(seconds: 45)
        let slower = try board(seconds: 90)
        let zero = try board(seconds: 0)
        let noInterval = try board(seconds: NSNull())

        XCTAssertEqual(KXRefreshPolicy.interval(visible: true, active: true, board: inSession), 60)
        XCTAssertEqual(KXRefreshPolicy.interval(visible: true, active: true, board: faster), 60, "不快于 60 秒")
        XCTAssertEqual(KXRefreshPolicy.interval(visible: true, active: true, board: slower), 90)
        XCTAssertNil(KXRefreshPolicy.interval(visible: false, active: true, board: inSession))
        XCTAssertNil(KXRefreshPolicy.interval(visible: true, active: false, board: inSession))
        XCTAssertNil(KXRefreshPolicy.interval(visible: false, active: false, board: inSession))
        XCTAssertNil(KXRefreshPolicy.interval(visible: true, active: true, board: closed))
        XCTAssertNil(KXRefreshPolicy.interval(visible: true, active: true, board: nil))
        XCTAssertNil(KXRefreshPolicy.interval(visible: true, active: true, board: zero))
        XCTAssertNil(KXRefreshPolicy.interval(visible: true, active: true, board: noInterval))
        XCTAssertEqual(KXRefreshPolicy.interval(for: inSession), 60)
    }

    func testVisibleInSessionFetchesImmediatelyThenOnTheInterval() async throws {
        let client = FakeKXClient(.success(try board()))
        let sleeper = TickSleeper()
        let model = makeModel(client: client, sleeper: sleeper)
        XCTAssertEqual(client.calls, 0)

        model.setVisible(true)
        let first = await waitUntil { client.calls == 1 && sleeper.waiting == 1 }
        XCTAssertTrue(first)
        XCTAssertEqual(sleeper.seconds, [60])
        guard case .ready(let loaded) = model.state else { return XCTFail("应有数据") }
        XCTAssertEqual(loaded.rooms.ordered.map { $0.groups.count }, [1, 1, 3])

        sleeper.tick()
        let second = await waitUntil { client.calls == 2 && sleeper.waiting == 1 }
        XCTAssertTrue(second)
        XCTAssertEqual(sleeper.seconds, [60, 60])
        XCTAssertTrue(model.isAutoRefreshing)
    }

    func testIntervalFloorsAtSixty() async throws {
        let client = FakeKXClient(.success(try board(seconds: 45)))
        let sleeper = TickSleeper()
        let model = makeModel(client: client, sleeper: sleeper)
        model.setVisible(true)
        let waiting = await waitUntil { sleeper.waiting == 1 }
        XCTAssertTrue(waiting)
        XCTAssertEqual(sleeper.seconds, [60])
    }

    func testSlowerIntervalComesFromTheInterface() async throws {
        let client = FakeKXClient(.success(try board(seconds: 90)))
        let sleeper = TickSleeper()
        let model = makeModel(client: client, sleeper: sleeper)
        model.setVisible(true)
        let waiting = await waitUntil { sleeper.waiting == 1 }
        XCTAssertTrue(waiting)
        XCTAssertEqual(sleeper.seconds, [90])
    }

    func testOutOfSessionFetchesOnlyOnce() async throws {
        let client = FakeKXClient(.success(try board(live: false)))
        let sleeper = TickSleeper()
        let model = makeModel(client: client, sleeper: sleeper)
        model.setVisible(true)
        let done = await waitUntil { client.calls == 1 && !model.isAutoRefreshing }
        XCTAssertTrue(done)
        XCTAssertTrue(sleeper.seconds.isEmpty)
        XCTAssertEqual(KXCopy.headerMeta(board: try XCTUnwrap(model.board), refreshFailed: false), "18:30 · 已收盘")
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
        XCTAssertTrue(stopped)
        sleeper.tick()
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(client.calls, 1)

        model.setActive(true)
        let resumed = await waitUntil { client.calls == 2 && sleeper.waiting == 1 }
        XCTAssertTrue(resumed)
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
        XCTAssertTrue(stopped)
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
        XCTAssertEqual(client.calls, 0)
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
        XCTAssertFalse(model.isAutoRefreshing)
        XCTAssertTrue(sleeper.seconds.isEmpty)
    }

    func testManualRefreshResumesTheLoopAfterAFailedFirstFetch() async throws {
        let client = FakeKXClient(.failure(KXBoardError.unreachable))
        let sleeper = TickSleeper()
        let model = makeModel(client: client, sleeper: sleeper)
        model.setVisible(true)
        let failed = await waitUntil { client.calls == 1 && !model.isAutoRefreshing }
        XCTAssertTrue(failed)
        XCTAssertEqual(model.state, .error(.unreachable))

        client.result = .success(try board())
        await model.refresh()
        XCTAssertEqual(client.calls, 2)
        XCTAssertNotNil(model.board)
        let resumed = await waitUntil { sleeper.waiting == 1 }
        XCTAssertTrue(resumed)
        XCTAssertEqual(client.calls, 2)
    }

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
            .unavailable("KX 看板还没读到过：KX 暂时连不上。"), .unreachable, .unreadable,
        ] {
            let client = FakeKXClient(.failure(failure))
            let model = makeModel(client: client)
            await model.refresh()
            XCTAssertEqual(model.state, .error(failure))
        }
        let model = makeModel(client: FakeKXClient(.failure(KXBoardError.unavailable("原因"))))
        await model.refresh()
        let message = KXCopy.message(for: .unavailable("原因"))
        XCTAssertEqual(message.title, "KX 看板暂时读不了")
        XCTAssertEqual(message.detail, "原因")
    }

    func testTransientFailureKeepsTheBoardAndRecovers() async throws {
        let client = FakeKXClient(.success(try board()))
        let model = makeModel(client: client)
        await model.refresh()
        let shown = model.board

        client.result = .failure(KXBoardError.unreachable)
        await model.refresh()
        XCTAssertEqual(model.board, shown)
        XCTAssertTrue(model.refreshFailed)
        XCTAssertEqual(KXCopy.headerMeta(board: try XCTUnwrap(model.board), refreshFailed: true), "18:30 · 刷新失败")

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
        XCTAssertEqual(model.state, .error(.unauthorized))
        XCTAssertNil(model.board)
        XCTAssertFalse(model.refreshFailed)

        client.result = .success(try board())
        await model.refresh()
        client.result = .failure(KXBoardError.unavailable("KX 看板还没读到过。"))
        await model.refresh()
        XCTAssertEqual(model.state, .error(.unavailable("KX 看板还没读到过。")))
        XCTAssertNil(model.board)
    }

    func testRefreshReplacesTheBoardInPlace() async throws {
        let client = FakeKXClient(.success(try board()))
        let model = makeModel(client: client)
        await model.refresh()
        let updated = try board { object in
            var rooms = object["rooms"] as! [String: Any]
            var leader = rooms["leader"] as! [[String: Any]]
            var leaders = leader[0]["leaders"] as! [[String: Any]]
            var quote = leaders[0]["quote"] as! [String: Any]
            quote["price"] = 9.99
            leaders[0]["quote"] = quote
            leader[0]["leaders"] = leaders
            rooms["leader"] = leader
            object["rooms"] = rooms
        }
        client.result = .success(updated)
        await model.refresh()
        XCTAssertEqual(model.board?.rooms.leader.first?.leaders.first?.quote.price, 9.99)
        XCTAssertNotEqual(model.state, .loading)
    }

    func testConnectionChangeResetsAndIgnoresStaleResponses() async throws {
        let first = FakeKXClient(.success(try board()))
        let second = FakeKXClient(.success(try board(live: false)))
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
        XCTAssertEqual(model.state, .loading)
        let refetched = await waitUntil { second.calls == 1 && !model.isAutoRefreshing }
        XCTAssertTrue(refetched)

        first.release(.success(try board()))
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(model.board?.session?.inSession, false)
    }

    func testUnchangedConnectionIsIgnored() async throws {
        let client = FakeKXClient(.success(try board()))
        let model = makeModel(client: client)
        await model.refresh()
        await model.handleConnectionChange()
        XCTAssertNotEqual(model.state, .loading)
        XCTAssertEqual(client.calls, 1)
    }

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

    private func board(live: Bool = true, seconds: Any = 60, _ edit: ((inout [String: Any]) -> Void)? = nil) throws -> KXBoard {
        var object = try JSONSerialization.jsonObject(with: fixtureData("kx_board_v3")) as! [String: Any]
        var session = object["session"] as! [String: Any]
        session["inSession"] = live
        session["refreshSeconds"] = live ? seconds : NSNull()
        object["session"] = session
        if let edit { edit(&object) }
        return try JSONDecoder().decode(KXBoard.self, from: JSONSerialization.data(withJSONObject: object))
    }

    private func fixtureData(_ name: String) throws -> Data {
        let bundle = Bundle(for: KXBoardModelTests.self)
        let url = bundle.url(forResource: name, withExtension: "json")
            ?? bundle.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
        return try Data(contentsOf: try XCTUnwrap(url, "缺少夹具 \(name).json"))
    }

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
