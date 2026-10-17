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
        await model.handleNotificationTap()

        XCTAssertEqual(model.tab, .report)
        XCTAssertEqual(model.focus, .latest)
        XCTAssertEqual(fake.latestCalls, calls + 1)
        XCTAssertEqual(model.state(for: .latest), .empty(empty))
        XCTAssertEqual(model.message(for: .empty(empty))?.title, MorningCopy.emptyEntries)
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
    private(set) var latestCalls = 0
    private(set) var reportCalls: [String] = []
    private(set) var historyLimits: [Int] = []

    func health() async throws -> MorningHealth { try healthResult.get() }

    func latest() async throws -> MorningReport {
        latestCalls += 1
        return try latestResult.get()
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

    func registerDevice(token: String) async throws -> MorningDeviceAck { MorningDeviceAck(ok: true) }
}

private struct MemoryTokenStore: APIAccessTokenStore {
    func load() -> String? { nil }
    func save(_ token: String) -> Bool { true }
}
