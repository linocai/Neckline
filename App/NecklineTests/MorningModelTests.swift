import XCTest
@testable import Neckline

@MainActor
final class MorningModelTests: XCTestCase {
    private var suites: [String] = []

    override func tearDown() {
        for name in suites {
            UserDefaults(suiteName: name)?.removePersistentDomain(forName: name)
        }
        suites = []
        super.tearDown()
    }

    func testLoadSuccess() async throws {
        let available = try fixture("report-available", as: MorningReport.self)
        let fake = FakeMorningClient()
        fake.latestResult = .success(available)
        fake.historyResult = .success(try fixture("reports-history", as: MorningHistory.self))
        let model = makeModel(client: fake)

        await model.reload(.latest)
        await model.reloadHistory()

        guard case .ready(let report) = model.state(for: .latest) else {
            return XCTFail("应显示已出的名单")
        }
        XCTAssertEqual(MorningCopy.header(tradeDate: report.tradeDate), "晨报 10/19 周一")
        XCTAssertEqual(MorningCopy.windowLine(report: report), "窗口 10/16 15:00 → 10/19 08:00 · 08:03 生成")
        XCTAssertEqual(report.gapSummary, "采集不完整：金十资讯没取全")
        XCTAssertEqual(report.content?.entries.map(\.companyName), ["震裕科技", "海特生物", "奥克股份"])
        let entry = try XCTUnwrap(report.content?.entries.first)
        XCTAssertEqual(MorningCopy.entryTag(type: entry.type, importance: entry.importance), "业绩向好 · 高")
        XCTAssertNil(model.message(for: .ready(report)))
        XCTAssertEqual(fake.historyLimits, [60])
        XCTAssertEqual(model.history.map(\.tradeDate), ["2026-10-19", "2026-10-16", "2026-10-15", "2026-10-14"])
    }

    func testEmptyReport() async throws {
        let empty = try fixture("report-empty", as: MorningReport.self)
        let fake = FakeMorningClient()
        fake.latestResult = .success(empty)
        let model = makeModel(client: fake)

        await model.reload(.latest)

        XCTAssertEqual(model.state(for: .latest), .empty(empty))
        let message = model.message(for: .empty(empty))
        XCTAssertEqual(message?.title, MorningCopy.emptyEntries)
        XCTAssertNil(message?.detail)
        XCTAssertEqual(message?.problem, false)
    }

    func testFailedReportShowsBackendReason() async throws {
        let failed = try fixture("report-failed", as: MorningReport.self)
        let fake = FakeMorningClient()
        fake.latestResult = .success(failed)
        let model = makeModel(client: fake)

        await model.reload(.latest)

        XCTAssertEqual(model.state(for: .latest), .failed(failed))
        let message = model.message(for: .failed(failed))
        XCTAssertEqual(message?.title, MorningCopy.failedTitle)
        XCTAssertEqual(message?.detail, "09:15 前没能出报")
        XCTAssertEqual(message?.problem, true)
        XCTAssertEqual(message?.retry, false)
        XCTAssertEqual(message?.icon, "exclamationmark.triangle")
        XCTAssertEqual(MorningCopy.windowLine(report: failed), "窗口 10/15 15:00 → 10/16 08:00")
    }

    func testRunningReport() async throws {
        let running = try runningReport()
        let fake = FakeMorningClient()
        fake.latestResult = .success(running)
        let model = makeModel(client: fake)

        await model.reload(.latest)

        XCTAssertEqual(model.state(for: .latest), .running(running))
        let message = model.message(for: .running(running))
        XCTAssertEqual(message?.title, MorningCopy.running)
        XCTAssertEqual(message?.detail, MorningCopy.runningDetail)
        XCTAssertEqual(message?.problem, false)
    }

    func testNoReport() async {
        await assertScreenError(.noReport, title: MorningCopy.noReport, detail: MorningCopy.noReportDetail, retry: false)
    }

    func testMissingToken() async {
        await assertScreenError(.missingToken, title: MorningCopy.missingToken, retry: true, settings: true)
    }

    func testUnauthorized() async {
        await assertScreenError(.unauthorized, title: MorningCopy.unauthorized, retry: true, settings: true)
    }

    func testServiceNotConfigured() async {
        await assertScreenError(.notConfigured, title: MorningCopy.notConfigured)
    }

    func testDatabaseNotReady() async {
        await assertScreenError(.dbNotReady, title: MorningCopy.dbNotReady)
    }

    func testUnreachable() async {
        await assertScreenError(.unreachable, title: MorningCopy.unreachable)
    }

    func testUnreadable() async {
        await assertScreenError(.unreadable, title: MorningCopy.unreadable)
    }

    func testSwitchingIssueKeepsTheOtherSlot() async throws {
        let available = try fixture("report-available", as: MorningReport.self)
        let failed = try fixture("report-failed", as: MorningReport.self)
        let fake = FakeMorningClient()
        fake.latestResult = .success(available)
        fake.reports["2026-10-16"] = .success(failed)
        let model = makeModel(client: fake)

        await model.reload(.latest)
        await model.open(.date("2026-10-16"))

        XCTAssertEqual(model.focus, .date("2026-10-16"))
        XCTAssertEqual(fake.reportCalls, ["2026-10-16"])
        XCTAssertEqual(model.state(for: .date("2026-10-16")), .failed(failed))
        XCTAssertEqual(model.message(for: .failed(failed))?.detail, "09:15 前没能出报")
        XCTAssertEqual(model.state(for: .latest), .ready(available))
        XCTAssertEqual(MorningCopy.header(tradeDate: "2026-10-16"), "晨报 10/16 周五")
    }

    func testTokenChangeClearsAndReloads() async throws {
        let available = try fixture("report-available", as: MorningReport.self)
        let failed = try fixture("report-failed", as: MorningReport.self)
        let history = try fixture("reports-history", as: MorningHistory.self)
        let fake = FakeMorningClient()
        fake.latestResult = .success(available)
        fake.reports["2026-10-16"] = .success(failed)
        fake.historyResult = .success(history)
        let (model, config) = makeModelAndConfig(client: fake)

        await model.reload(.latest)
        await model.open(.date("2026-10-16"))
        await model.reloadHistory()
        let callsBefore = fake.latestCalls
        await model.handleConnectionChange()
        XCTAssertEqual(fake.latestCalls, callsBefore, "同一令牌不应重载")

        fake.latestResult = .failure(MorningClientError.unreachable)
        fake.historyResult = .failure(MorningClientError.unreachable)
        config.apiToken = "changed-token"
        await model.handleConnectionChange()

        XCTAssertEqual(model.focus, .latest)
        XCTAssertEqual(model.state(for: .latest), .error(.unreachable))
        XCTAssertEqual(model.message(for: .error(.unreachable))?.title, MorningCopy.unreachable)
        XCTAssertEqual(model.state(for: .date("2026-10-16")), .loading)
        XCTAssertTrue(model.history.isEmpty)
        XCTAssertEqual(model.historyError, .unreachable)
        XCTAssertEqual(fake.latestCalls, callsBefore + 1)
    }

    func testNotificationTapReturnsToLatest() async throws {
        let available = try fixture("report-available", as: MorningReport.self)
        let empty = try fixture("report-empty", as: MorningReport.self)
        let failed = try fixture("report-failed", as: MorningReport.self)
        let fake = FakeMorningClient()
        fake.latestResult = .success(available)
        fake.reports["2026-10-16"] = .success(failed)
        let model = makeModel(client: fake)

        await model.reload(.latest)
        await model.open(.date("2026-10-16"))
        model.tab = .history
        fake.latestResult = .success(empty)
        let calls = fake.latestCalls
        let beforeReset = model.reportStackReset
        await model.handleNotificationTap()

        XCTAssertNotEqual(model.reportStackReset, beforeReset)
        XCTAssertEqual(model.tab, .report)
        XCTAssertEqual(model.focus, .latest)
        XCTAssertEqual(fake.latestCalls, calls + 1)
        XCTAssertEqual(model.state(for: .latest), .empty(empty))
        XCTAssertEqual(model.message(for: .empty(empty))?.title, MorningCopy.emptyEntries)

        let afterFirst = model.reportStackReset
        let callsAfterFirst = fake.latestCalls
        await model.handleNotificationTap()
        XCTAssertNotEqual(model.reportStackReset, afterFirst)
        XCTAssertEqual(model.tab, .report)
        XCTAssertEqual(model.focus, .latest)
        XCTAssertEqual(fake.latestCalls, callsAfterFirst + 1)
    }

    func testDetailEntryFindsPendingWithoutFallback() async throws {
        let available = try fixture("report-available", as: MorningReport.self)
        let empty = try fixture("report-empty", as: MorningReport.self)
        let main = try XCTUnwrap(available.content?.entries.first)
        let pending = try XCTUnwrap(available.content?.review.relation.first)
        let model = makeModel(client: FakeMorningClient())

        XCTAssertNil(model.detailEntry(in: available))
        XCTAssertNil(model.detailEntry(in: empty))

        for bucket in ReviewBucket.allCases {
            let ready = report(from: available, main: available.content?.entries ?? [], pending: pending, bucket: bucket)
            let blank = report(from: empty, main: [], pending: pending, bucket: bucket)
            model.select(pending)
            XCTAssertEqual(model.detailEntry(in: ready)?.listID, pending.listID, "有条目的期应找到 \(bucket)")
            XCTAssertEqual(model.detailEntry(in: blank)?.listID, pending.listID, "0 条的期应找到 \(bucket)")
        }

        let unselected = makeModel(client: FakeMorningClient())
        let blank = report(from: empty, main: [], pending: pending, bucket: .types)
        XCTAssertNil(unselected.detailEntry(in: blank))

        model.select(main)
        XCTAssertEqual(model.detailEntry(in: available)?.listID, main.listID)
    }

    func testConnectionEditsDoNotLeaveLatestLoading() async throws {
        let fresh = try fixture("report-available", as: MorningReport.self)
        let stale = try fixture("report-empty", as: MorningReport.self)
        let fake = FakeMorningClient()
        fake.suspendLatest = true
        let (model, config) = makeModelAndConfig(client: fake)
        defer { fake.resumeAllLatest(.success(fresh)) }

        let initial = Task { await model.reload(.latest) }
        guard await waitUntil("第一次最新一期应挂起", { fake.latestCalls == 1 }) else { return }

        config.apiToken = "token-b"
        model.connectionInputsChanged()
        guard await waitUntil("改到 B 后应开始重载", timeoutMs: 3000, { fake.latestCalls == 2 }) else { return }

        config.apiToken = "token-c"
        model.connectionInputsChanged()
        config.apiToken = "token-b"
        model.connectionInputsChanged()
        XCTAssertEqual(fake.latestCalls, 2, "300 毫秒内改走又改回不应再发请求，也不该取消进行中的那次")

        fake.resumeLatest(at: 0, .success(stale))
        await initial.value
        XCTAssertEqual(model.state(for: .latest), .loading)
        XCTAssertNotEqual(model.state(for: .latest), .empty(stale))

        fake.resumeLatest(at: 1, .success(fresh))
        guard await waitUntil("放行后应加载完成", { model.state(for: .latest) == .ready(fresh) }) else { return }
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(model.state(for: .latest), .ready(fresh))
        XCTAssertNotEqual(model.state(for: .latest), .loading)
        XCTAssertEqual(fake.latestCalls, 2)
    }

    func testFailedDeviceRegistrationRetriesWithoutOverlap() async throws {
        let available = try fixture("report-available", as: MorningReport.self)
        let health = MorningHealth(db: "ready", latestReport: nil, readPage: true, modelKey: true)
        let fake = FakeMorningClient()
        fake.healthResult = .success(health)
        fake.latestResult = .success(available)
        fake.registerResult = .failure(MorningClientError.unreachable)
        let model = makeModel(client: fake)

        await model.registerDevice(hexToken: "device-a")
        XCTAssertEqual(model.notificationRegistration, .failed)
        XCTAssertEqual(model.notificationRegistration?.title, "没能登记")
        XCTAssertEqual(fake.registerCalls, 1)

        fake.registerResult = .success(MorningDeviceAck(ok: true))
        await model.checkConnection()
        XCTAssertEqual(model.notificationRegistration, .registered)
        XCTAssertEqual(model.notificationRegistration?.title, "已登记")
        XCTAssertEqual(fake.registerCalls, 2)

        await model.checkConnection()
        XCTAssertEqual(fake.registerCalls, 2, "已登记后不再发请求")

        let foregroundClient = FakeMorningClient()
        foregroundClient.registerResult = .failure(MorningClientError.unreachable)
        let foreground = makeModel(client: foregroundClient)
        await foreground.registerDevice(hexToken: "device-foreground")
        XCTAssertEqual(foreground.notificationRegistration, .failed)
        foregroundClient.registerResult = .success(MorningDeviceAck(ok: true))
        await foreground.retryFailedDeviceRegistration()
        XCTAssertEqual(foreground.notificationRegistration, .registered)
        XCTAssertEqual(foregroundClient.registerCalls, 2)
        await foreground.retryFailedDeviceRegistration()
        XCTAssertEqual(foregroundClient.registerCalls, 2)

        let hanging = FakeMorningClient()
        hanging.healthResult = .success(health)
        hanging.latestResult = .success(available)
        hanging.suspendRegister = true
        let concurrent = makeModel(client: hanging)
        defer { hanging.resumeAllRegisters(.success(MorningDeviceAck(ok: true))) }

        let first = Task { await concurrent.registerDevice(hexToken: "device-b") }
        guard await waitUntil("第一次登记应挂起", { hanging.registerCalls == 1 }) else { return }

        var checkDone = false
        let check = Task {
            await concurrent.checkConnection()
            checkDone = true
        }
        guard await waitUntil("检查连接不该跟着挂起的登记一起等", { checkDone }) else { return }

        var secondDone = false
        let second = Task {
            await concurrent.registerDevice(hexToken: "device-b")
            secondDone = true
        }
        guard await waitUntil("同一令牌的登记不应并发第二发", { secondDone }) else { return }
        XCTAssertEqual(hanging.registerCalls, 1)

        hanging.resumeRegister(at: 0, .success(MorningDeviceAck(ok: true)))
        await first.value
        await second.value
        await check.value
        XCTAssertEqual(concurrent.notificationRegistration, .registered)
        XCTAssertEqual(hanging.registerCalls, 1)
    }

    func testStaleDeviceRegistrationDoesNotOverwrite() async throws {
        let available = try fixture("report-available", as: MorningReport.self)
        let fake = FakeMorningClient()
        fake.latestResult = .success(available)
        fake.historyResult = .success(MorningHistory(reports: []))
        fake.suspendRegister = true
        let (model, config) = makeModelAndConfig(client: fake)
        defer { fake.resumeAllRegisters(.failure(MorningClientError.unreachable)) }

        let first = Task { await model.registerDevice(hexToken: "device-a") }
        guard await waitUntil("A 的登记应挂起", { fake.registerCalls == 1 }) else { return }

        config.apiToken = "token-b"
        let switched = Task { await model.handleConnectionChange() }
        guard await waitUntil("切到 B 后应另发登记", timeoutMs: 3000, { fake.registerCalls == 2 }) else { return }

        fake.resumeRegister(at: 1, .success(MorningDeviceAck(ok: true)))
        guard await waitUntil("B 应登记成功", { model.notificationRegistration == .registered }) else { return }
        await switched.value

        fake.resumeRegister(at: 0, .failure(MorningClientError.unreachable))
        await first.value
        XCTAssertEqual(model.notificationRegistration, .registered)
        XCTAssertEqual(model.notificationRegistration?.title, "已登记")
        XCTAssertEqual(fake.registerCalls, 2)
    }

    func testCheckConnectionLines() async throws {
        let available = try fixture("report-available", as: MorningReport.self)
        let failed = try fixture("report-failed", as: MorningReport.self)
        let fake = FakeMorningClient()
        fake.healthResult = .success(MorningHealth(
            db: "ready",
            latestReport: MorningHealthLatest(tradeDate: "2026-10-14", status: .available),
            readPage: true,
            modelKey: true
        ))
        fake.latestResult = .success(available)
        let (model, config) = makeModelAndConfig(client: fake)

        await model.checkConnection()
        XCTAssertEqual(model.connectionLines.map(\.text), [
            "服务正常，库已就绪",
            "最新一期 10/19，已出",
            "只读网页可用",
            "模型已配置",
        ])
        XCTAssertEqual(model.connectionLines.map(\.problem), [false, false, false, false])

        fake.latestResult = .success(failed)
        fake.healthResult = .success(MorningHealth(db: "ready", latestReport: nil, readPage: false, modelKey: false))
        await model.checkConnection()
        XCTAssertEqual(model.connectionLines.map(\.text), [
            "服务正常，库已就绪",
            "最新一期 10/16，出问题",
            "只读网页未配置",
            "模型未配置",
        ])
        XCTAssertEqual(model.connectionLines.map(\.problem), [false, true, true, true])

        fake.latestResult = .failure(MorningClientError.noReport)
        await model.checkConnection()
        XCTAssertEqual(model.connectionLines[1].text, MorningCopy.noReport)
        XCTAssertEqual(model.connectionLines[1].problem, false)
        XCTAssertEqual(model.connectionLines.last?.text, "模型未配置")

        fake.latestResult = .failure(MorningClientError.unauthorized)
        await model.checkConnection()
        XCTAssertEqual(model.connectionLines[1].text, MorningCopy.unauthorized)
        XCTAssertEqual(model.connectionLines[1].problem, true)

        let calls = fake.latestCalls
        config.apiToken = ""
        fake.healthResult = .success(MorningHealth(
            db: "ready",
            latestReport: MorningHealthLatest(tradeDate: "2026-10-14", status: .available),
            readPage: false,
            modelKey: true
        ))
        await model.checkConnection()
        XCTAssertEqual(fake.latestCalls, calls, "没填令牌时不请求最新一期")
        XCTAssertEqual(model.connectionLines.map(\.text), [
            "服务正常，库已就绪",
            "最新一期 10/14，已出",
            "只读网页未配置",
            "模型已配置",
        ])

        fake.healthResult = .failure(MorningClientError.notConfigured)
        await model.checkConnection()
        XCTAssertEqual(model.connectionLines.map(\.text), [MorningCopy.notConfigured])
        XCTAssertEqual(model.connectionLines.map(\.problem), [true])

        fake.healthResult = .failure(MorningClientError.dbNotReady)
        await model.checkConnection()
        XCTAssertEqual(model.connectionLines.map(\.text), [MorningCopy.dbNotReady])
        XCTAssertEqual(model.connectionLines.map(\.problem), [true])
    }

    func testStartupDropsLegacyCacheKeys() {
        let defaults = isolatedDefaults()
        defaults.set(Data([1]), forKey: "neckline.k10.v15.cache.leftover")
        defaults.set("keep", forKey: "neckline.keep")
        let config = makeConfig(defaults: defaults, token: "fixture-token-not-real")
        _ = MorningModel(config: config, clientFactory: { _ in FakeMorningClient() }, defaults: defaults)
        XCTAssertNil(defaults.object(forKey: "neckline.k10.v15.cache.leftover"))
        XCTAssertEqual(defaults.string(forKey: "neckline.keep"), "keep")
    }

    private enum ReviewBucket: CaseIterable {
        case types, relation, fresh, old
    }

    private func report(from base: MorningReport, main: [MorningEntry], pending: MorningEntry, bucket: ReviewBucket) -> MorningReport {
        let content = base.content!
        let review = MorningReview(
            types: bucket == .types ? [pending] : [],
            relation: bucket == .relation ? [pending] : [],
            fresh: bucket == .fresh ? [pending] : [],
            old: bucket == .old ? [pending] : []
        )
        let rewritten = MorningContent(
            reportId: content.reportId,
            tradeDate: content.tradeDate,
            kind: content.kind,
            window: content.window,
            generatedAt: content.generatedAt,
            configVersion: content.configVersion,
            gapSummary: content.gapSummary,
            entries: main,
            review: review
        )
        return MorningReport(
            reportId: base.reportId,
            tradeDate: base.tradeDate,
            status: base.status,
            windowStart: base.windowStart,
            windowEnd: base.windowEnd,
            availableAt: base.availableAt,
            failedAt: base.failedAt,
            failureReason: base.failureReason,
            gapSummary: base.gapSummary,
            configVersion: base.configVersion,
            content: rewritten
        )
    }

    @discardableResult
    private func waitUntil(_ description: String, timeoutMs: Int = 2000, _ condition: @MainActor () -> Bool, file: StaticString = #filePath, line: UInt = #line) async -> Bool {
        let steps = max(timeoutMs / 20, 1)
        for _ in 0..<steps {
            if condition() { return true }
            try? await Task.sleep(nanoseconds: 20_000_000)
        }
        XCTFail("超时：\(description)", file: file, line: line)
        return false
    }

    private func assertScreenError(_ error: MorningClientError, title: String, detail: String? = nil, retry: Bool = true, settings: Bool = false, file: StaticString = #filePath, line: UInt = #line) async {
        let fake = FakeMorningClient()
        fake.latestResult = .failure(error)
        let model = makeModel(client: fake)
        await model.reload(.latest)
        let state = model.state(for: .latest)
        XCTAssertEqual(state, .error(error), file: file, line: line)
        let message = model.message(for: state)
        XCTAssertEqual(message?.title, title, file: file, line: line)
        XCTAssertEqual(message?.detail, detail, file: file, line: line)
        XCTAssertEqual(message?.problem, false, file: file, line: line)
        XCTAssertEqual(message?.retry, retry, file: file, line: line)
        XCTAssertEqual(message?.settings, settings, file: file, line: line)
    }

    private func makeModel(client: FakeMorningClient) -> MorningModel {
        makeModelAndConfig(client: client).model
    }

    private func makeModelAndConfig(client: FakeMorningClient) -> (model: MorningModel, config: AppConfig) {
        let defaults = isolatedDefaults()
        let config = makeConfig(defaults: defaults, token: "fixture-token-not-real")
        let model = MorningModel(config: config, clientFactory: { _ in client }, defaults: defaults)
        return (model, config)
    }

    private func makeConfig(defaults: UserDefaults, token: String) -> AppConfig {
        let config = AppConfig(defaults: defaults, tokenStore: MemoryTokenStore(), loadPersistentCredentials: false)
        config.apiToken = token
        return config
    }

    private func isolatedDefaults() -> UserDefaults {
        let name = "neckline.morning.tests.\(UUID().uuidString)"
        suites.append(name)
        let defaults = UserDefaults(suiteName: name)!
        defaults.removePersistentDomain(forName: name)
        return defaults
    }

    private func runningReport() throws -> MorningReport {
        var object = try JSONSerialization.jsonObject(with: fixtureData("report-failed")) as! [String: Any]
        object["status"] = "running"
        object["failureReason"] = NSNull()
        object["tradeDate"] = "2026-10-15"
        return try JSONDecoder().decode(MorningReport.self, from: JSONSerialization.data(withJSONObject: object))
    }

    private func fixture<T: Decodable>(_ name: String, as type: T.Type) throws -> T {
        try JSONDecoder().decode(type, from: fixtureData(name))
    }

    private func fixtureData(_ name: String) throws -> Data {
        let bundle = Bundle(for: MorningModelTests.self)
        let url = bundle.url(forResource: name, withExtension: "json")
            ?? bundle.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
        return try Data(contentsOf: try XCTUnwrap(url, "缺少夹具 \(name).json"))
    }
}

private final class FakeMorningClient: MorningClient, @unchecked Sendable {
    var latestResult: Result<MorningReport, Error> = .failure(MorningClientError.unreadable)
    var reports: [String: Result<MorningReport, Error>] = [:]
    var historyResult: Result<MorningHistory, Error> = .success(MorningHistory(reports: []))
    var healthResult: Result<MorningHealth, Error> = .failure(MorningClientError.unreadable)
    var suspendLatest = false
    var suspendRegister = false
    var registerResult: Result<MorningDeviceAck, Error> = .success(MorningDeviceAck(ok: true))
    private(set) var latestCalls = 0
    private(set) var reportCalls: [String] = []
    private(set) var historyLimits: [Int] = []
    private(set) var registerCalls = 0
    private(set) var registerTokens: [String] = []
    private let latestGate = SuspendGate<MorningReport>()
    private let registerGate = SuspendGate<MorningDeviceAck>()

    func health() async throws -> MorningHealth { try healthResult.get() }

    func latest() async throws -> MorningReport {
        latestCalls += 1
        if suspendLatest { return try await latestGate.park() }
        return try latestResult.get()
    }

    func resumeLatest(at index: Int, _ result: Result<MorningReport, Error>) {
        latestGate.resume(at: index, result)
    }

    func resumeAllLatest(_ result: Result<MorningReport, Error>) {
        latestGate.resumeAll(result)
    }

    func resumeRegister(at index: Int, _ result: Result<MorningDeviceAck, Error>) {
        registerGate.resume(at: index, result)
    }

    func resumeAllRegisters(_ result: Result<MorningDeviceAck, Error>) {
        registerGate.resumeAll(result)
    }

    func report(tradeDate: String) async throws -> MorningReport {
        reportCalls.append(tradeDate)
        guard let result = reports[tradeDate] else { throw MorningClientError.noReport }
        return try result.get()
    }

    func history(limit: Int) async throws -> MorningHistory {
        historyLimits.append(limit)
        return try historyResult.get()
    }

    func document(id: String) async throws -> MorningDocument { throw MorningClientError.noDocument }

    func registerDevice(token: String) async throws -> MorningDeviceAck {
        registerCalls += 1
        registerTokens.append(token)
        if suspendRegister { return try await registerGate.park() }
        return try registerResult.get()
    }
}

private final class SuspendGate<Value>: @unchecked Sendable {
    private final class Waiter {
        var continuation: CheckedContinuation<Value, Error>?
        var pending: Result<Value, Error>?
        var finished = false
    }

    private let lock = NSLock()
    private var waiters: [Waiter] = []

    func park() async throws -> Value {
        lock.lock()
        let index = waiters.count
        waiters.append(Waiter())
        lock.unlock()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Value, Error>) in
                self.attach(index, continuation)
            }
        } onCancel: {
            self.finish(index, .failure(CancellationError()))
        }
    }

    func resume(at index: Int, _ result: Result<Value, Error>) {
        finish(index, result)
    }

    func resumeAll(_ result: Result<Value, Error>) {
        lock.lock()
        let count = waiters.count
        lock.unlock()
        for index in 0..<count {
            finish(index, result)
        }
    }

    private func attach(_ index: Int, _ continuation: CheckedContinuation<Value, Error>) {
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

    private func finish(_ index: Int, _ result: Result<Value, Error>) {
        lock.lock()
        guard waiters.indices.contains(index) else {
            lock.unlock()
            return
        }
        let waiter = waiters[index]
        if waiter.finished {
            lock.unlock()
            return
        }
        waiter.finished = true
        let continuation = waiter.continuation
        waiter.continuation = nil
        if continuation == nil {
            waiter.pending = result
            lock.unlock()
            return
        }
        lock.unlock()
        continuation?.resume(with: result)
    }
}

private struct MemoryTokenStore: APIAccessTokenStore {
    func load() -> String? { nil }
    func save(_ token: String) -> Bool { true }
}
