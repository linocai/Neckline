import XCTest
@testable import Neckline

final class MorningContractTests: XCTestCase {
    func testAvailableReportFixture() throws {
        let report = try decode("report-available", as: MorningReport.self)
        let content = try XCTUnwrap(report.content)
        XCTAssertEqual(report.status, .available)
        XCTAssertEqual(report.tradeDate, "2026-10-19")
        XCTAssertEqual(report.gapSummary, "采集不完整：金十资讯没取全")
        XCTAssertEqual(content.gapSummary, report.gapSummary)
        XCTAssertEqual(content.window.start, "2026-10-16T15:00+08:00")
        XCTAssertEqual(content.window.end, "2026-10-19T08:00+08:00")
        XCTAssertEqual(content.generatedAt, "2026-10-19T08:03+08:00")
        XCTAssertEqual(report.windowStart, "2026-10-16T07:00:00+00:00")
        XCTAssertEqual(report.windowEnd, "2026-10-19T00:00:00+00:00")

        let entries = content.entries
        XCTAssertEqual(entries.map(\.companyName), ["震裕科技", "海特生物", "奥克股份"])
        XCTAssertEqual(entries.map(\.companyCode), ["300953", "300683", "300082"])
        XCTAssertEqual(entries[0].type, "业绩向好")
        XCTAssertEqual(entries[0].importance, .high)
        XCTAssertNil(entries[0].firstDisclosed)
        XCTAssertEqual(entries[0].sources[0].url, "https://example.invalid/flash/z1")
        XCTAssertEqual(entries[0].others.map(\.headline), ["签订设备采购合同"])
        XCTAssertEqual(entries[0].others[0].importance, .medium)
        XCTAssertEqual(entries[1].firstDisclosed, "2026-10-16")
        XCTAssertNil(entries[1].sources[0].url)
        XCTAssertNil(entries[1].sources[0].title)
        XCTAssertEqual(entries[2].importance, .medium)
        XCTAssertEqual(content.review.types.map(\.companyName), ["汇川技术"])
        XCTAssertEqual(content.review.types[0].importance, .low)
        XCTAssertEqual(content.review.relation.map(\.companyName), ["亿纬锂能"])
        XCTAssertEqual(content.review.relation[0].relation, "受益方")
        XCTAssertTrue(content.review.fresh.isEmpty)
        XCTAssertTrue(content.review.old.isEmpty)

        let document = try decode("document", as: MorningDocument.self)
        XCTAssertEqual(document.documentId, entries[0].sources[0].documentId)
        XCTAssertEqual(document.url, "https://example.invalid/flash/z1")
        XCTAssertEqual(document.sourceLabel, "金十快讯")
        XCTAssertEqual(document.text, "震裕科技预计前三季度净利增长 58%。")
        XCTAssertEqual(document.publishedAt, "2026-10-16T17:05+08:00")
    }

    func testEmptyFailedHistoryAndStatusFixtures() throws {
        let empty = try decode("report-empty", as: MorningReport.self)
        let emptyContent = try XCTUnwrap(empty.content)
        XCTAssertEqual(empty.status, .available)
        XCTAssertTrue(emptyContent.entries.isEmpty)
        XCTAssertTrue(emptyContent.review.types.isEmpty)
        XCTAssertTrue(emptyContent.review.relation.isEmpty)
        XCTAssertTrue(emptyContent.review.fresh.isEmpty)
        XCTAssertTrue(emptyContent.review.old.isEmpty)
        XCTAssertNil(empty.gapSummary)

        let failed = try decode("report-failed", as: MorningReport.self)
        XCTAssertEqual(failed.status, .failed)
        XCTAssertNil(failed.content)
        XCTAssertEqual(failed.failureReason, "09:15 前没能出报")
        XCTAssertEqual(failed.windowStart, "2026-10-15T07:00:00+00:00")
        XCTAssertEqual(failed.windowEnd, "2026-10-16T00:00:00+00:00")
        XCTAssertEqual(failed.failedAt, "2026-10-16T01:20:00+00:00")

        let history = try decode("reports-history", as: MorningHistory.self)
        XCTAssertEqual(history.reports.map(\.tradeDate), ["2026-10-19", "2026-10-16", "2026-10-15", "2026-10-14"])
        XCTAssertEqual(history.reports.map(\.status), [.available, .failed, .running, .available])
        XCTAssertEqual(history.reports[0].entryCount, 3)
        XCTAssertEqual(history.reports[1].failureReason, "09:15 前没能出报")
        XCTAssertNil(history.reports[2].failureReason)
        XCTAssertNil(history.reports[2].availableAt)

        let health = try decode("health", as: MorningHealth.self)
        XCTAssertEqual(health.db, "ready")
        XCTAssertEqual(health.latestReport?.tradeDate, "2026-10-19")
        XCTAssertEqual(health.latestReport?.status, .available)
        XCTAssertFalse(health.readPage)
        XCTAssertFalse(health.modelKey)
        let noLatest = try JSONDecoder().decode(MorningHealth.self, from: Data(#"{"db":"ready","latestReport":null,"readPage":false,"modelKey":false}"#.utf8))
        XCTAssertNil(noLatest.latestReport)

        let ack = try decode("devices-ok", as: MorningDeviceAck.self)
        XCTAssertTrue(ack.ok)
    }

    func testErrorBodiesAndUnknownValues() throws {
        let unauthorized = try decode("unauthorized", as: MorningFailureBody.self)
        XCTAssertEqual(unauthorized.detailText, "未授权:缺失或错误的 Bearer token")
        XCTAssertNil(unauthorized.reason)
        let objectDetail = try JSONDecoder().decode(MorningFailureBody.self, from: Data(#"{"detail":{"reason":"bad_token"}}"#.utf8))
        XCTAssertNil(objectDetail.detailText)
        XCTAssertEqual(objectDetail.reason, "bad_token")
        XCTAssertEqual(try decode("report-not-found", as: MorningFailureBody.self).reason, "no_report")
        XCTAssertEqual(try decode("document-not-found", as: MorningFailureBody.self).reason, "no_document")
        XCTAssertEqual(try decode("not-ready", as: MorningFailureBody.self).reason, "v4_db_not_ready")
        XCTAssertEqual(try decode("not-configured", as: MorningFailureBody.self).reason, "v4_not_configured")

        var object = try JSONSerialization.jsonObject(with: fixture("report-available")) as! [String: Any]
        object["status"] = "paused"
        var content = object["content"] as! [String: Any]
        var entries = content["entries"] as! [[String: Any]]
        entries[0]["importance"] = "极高"
        content["entries"] = entries
        object["content"] = content
        let report = try JSONDecoder().decode(MorningReport.self, from: JSONSerialization.data(withJSONObject: object))
        XCTAssertEqual(report.status, .unknown("paused"))
        XCTAssertEqual(report.content?.entries[0].importance, .unknown("极高"))
        XCTAssertEqual(report.content?.entries[0].companyName, "震裕科技")
    }

    func testClientMapsHTTPFailures() async throws {
        await expectFailure(.unauthorized, status: 401, data: try fixture("unauthorized")) { api in _ = try await api.latest() }
        await expectFailure(.unauthorized, status: 401, data: Data(#"{"detail":{"reason":"bad_token"}}"#.utf8)) { api in _ = try await api.latest() }
        await expectFailure(.noReport, status: 404, data: try fixture("report-not-found")) { api in _ = try await api.latest() }
        await expectFailure(.noDocument, status: 404, data: try fixture("document-not-found")) { api in _ = try await api.document(id: "doc_missing") }
        await expectFailure(.dbNotReady, status: 503, data: try fixture("not-ready")) { api in _ = try await api.latest() }
        await expectFailure(.notConfigured, status: 503, data: try fixture("not-configured")) { api in _ = try await api.latest() }
        await expectFailure(.unreadable, status: 404, data: Data(#"{"detail":{"reason":"other"}}"#.utf8)) { api in _ = try await api.latest() }
        await expectFailure(.unreadable, status: 200, data: Data("{}".utf8)) { api in _ = try await api.latest() }

        let offline = ScriptTransport(error: URLError(.cannotConnectToHost))
        let disconnected = MorningAPI(baseURL: URL(string: "https://fixture.invalid")!, token: "fixture-token-not-real", transport: offline)
        await XCTAssertThrowsErrorAsync(try await disconnected.health(), MorningClientError.unreachable)

        let trap = ScriptTransport(error: URLError(.badServerResponse))
        let blank = MorningAPI(baseURL: URL(string: "https://fixture.invalid")!, token: "   ", transport: trap)
        await XCTAssertThrowsErrorAsync(try await blank.latest(), MorningClientError.missingToken)
        XCTAssertTrue(trap.requests.isEmpty)
    }

    func testClientRequestsOnlyV4() async throws {
        let health = try fixture("health")
        let available = try fixture("report-available")
        let history = try fixture("reports-history")
        let document = try fixture("document")
        let devices = try fixture("devices-ok")
        let transport = ScriptTransport()
        transport.route = { request in
            switch request.url?.path {
            case "/api/v4/health": return (200, health)
            case "/api/v4/reports/latest", "/api/v4/reports/2026-10-19": return (200, available)
            case "/api/v4/reports": return (200, history)
            case "/api/v4/documents/doc_1": return (200, document)
            case "/api/v4/devices": return (200, devices)
            default: return (500, Data())
            }
        }
        let api = MorningAPI(baseURL: URL(string: "https://fixture.invalid")!, token: "fixture-token-not-real", transport: transport)
        _ = try await api.health()
        _ = try await api.latest()
        _ = try await api.report(tradeDate: "2026-10-19")
        _ = try await api.history(limit: 60)
        _ = try await api.document(id: "doc_1")
        let device = "0123456789abcdef0123456789abcdef"
        let ack = try await api.registerDevice(token: device)
        XCTAssertTrue(ack.ok)

        XCTAssertEqual(transport.requests.map(\.url?.path), [
            "/api/v4/health", "/api/v4/reports/latest", "/api/v4/reports/2026-10-19",
            "/api/v4/reports", "/api/v4/documents/doc_1", "/api/v4/devices",
        ])
        let healthRequest = try XCTUnwrap(transport.requests.first)
        XCTAssertNil(healthRequest.value(forHTTPHeaderField: "Authorization"))
        for request in transport.requests.dropFirst() {
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-token-not-real")
            XCTAssertFalse(request.url?.absoluteString.contains("fixture-token-not-real") ?? true)
        }
        let historyRequest = transport.requests[3]
        XCTAssertEqual(URLComponents(url: try XCTUnwrap(historyRequest.url), resolvingAgainstBaseURL: false)?.query, "limit=60")
        let posted = try XCTUnwrap(transport.requests.last)
        XCTAssertEqual(posted.httpMethod, "POST")
        let body = try JSONSerialization.jsonObject(with: try XCTUnwrap(posted.httpBody)) as! [String: String]
        XCTAssertEqual(body, ["token": device, "platform": "ios"])
        XCTAssertFalse(transport.requests.contains { $0.url?.absoluteString.contains("/api/v1") == true })
    }

    func testCopyHeaderWindowHistoryAndTags() throws {
        let available = try decode("report-available", as: MorningReport.self)
        let failed = try decode("report-failed", as: MorningReport.self)
        XCTAssertEqual(MorningCopy.header(tradeDate: available.tradeDate), "晨报 10/19 周一")
        XCTAssertEqual(MorningCopy.historyTitle(tradeDate: "2026-10-19"), "10/19 周一")
        XCTAssertEqual(MorningCopy.historyTitle(tradeDate: "2026-10-18"), "10/18 周日")
        XCTAssertEqual(MorningCopy.windowLine(report: available), "窗口 10/16 15:00 → 10/19 08:00 · 08:03 生成")
        XCTAssertEqual(
            MorningCopy.windowLine(start: available.windowStart, end: available.windowEnd, generatedAt: available.availableAt),
            "窗口 10/16 15:00 → 10/19 08:00 · 08:03 生成"
        )
        XCTAssertEqual(MorningCopy.windowLine(report: failed), "窗口 10/15 15:00 → 10/16 08:00")
        XCTAssertEqual(
            MorningCopy.windowLine(start: "2026-10-16T23:30:00-07:00", end: "2026-10-17T01:00:00-07:00", generatedAt: "2026-10-17T00:03:00Z"),
            "窗口 10/17 14:30 → 10/17 16:00 · 08:03 生成"
        )
        XCTAssertEqual(MorningCopy.historySubtitle(status: .available, entryCount: 3), "已出 · 3 条")
        XCTAssertEqual(MorningCopy.historySubtitle(status: .failed, entryCount: 0), "出问题")
        XCTAssertEqual(MorningCopy.historySubtitle(status: .running, entryCount: 0), "正在生成")
        let entry = try XCTUnwrap(available.content?.entries.first)
        XCTAssertEqual(MorningCopy.entryTag(type: entry.type, importance: entry.importance), "业绩向好 · 高")
        XCTAssertEqual(MorningCopy.entryTag(type: "业绩向好", importance: .unknown("极高")), "业绩向好 · 极高")
        XCTAssertEqual(MorningCopy.failureLine(reason: failed.failureReason), "晨报出问题了\n09:15 前没能出报")
        XCTAssertEqual(MorningCopy.text(for: .missingToken), "还没有填写令牌")
        XCTAssertEqual(MorningCopy.text(for: .unauthorized), "令牌不正确")
        XCTAssertEqual(MorningCopy.text(for: .noReport), "还没有晨报")
        XCTAssertEqual(MorningCopy.detail(for: .noReport), "每个交易日 08:00 截止后出报")
        XCTAssertEqual(MorningCopy.text(for: .noDocument), "找不到这篇原文")
        XCTAssertEqual(MorningCopy.text(for: .notConfigured), "服务还没配好")
        XCTAssertEqual(MorningCopy.text(for: .dbNotReady), "数据库还没就绪")
        XCTAssertEqual(MorningCopy.text(for: .unreachable), "连不上服务")
        XCTAssertEqual(MorningCopy.text(for: .unreadable), "这份晨报暂时读不了")
        XCTAssertEqual(MorningCopy.reading, "正在读取")
        XCTAssertEqual(MorningCopy.emptyEntries, "今天没有符合条件的利好")
        XCTAssertEqual(MorningCopy.running, "正在出报")
        XCTAssertEqual(MorningCopy.runningDetail, "完成后下拉刷新")
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Shanghai")!
        let now = try XCTUnwrap(calendar.date(from: DateComponents(year: 2026, month: 10, day: 17, hour: 12)))
        XCTAssertEqual(MorningCopy.firstDisclosedLine("2026-10-13 16:45", now: now), "首次公开 10/13 16:45")
        XCTAssertEqual(MorningCopy.firstDisclosedLine("2026-10-13", now: now), "首次公开 10/13")
        XCTAssertEqual(MorningCopy.firstDisclosedLine("2025-03-02 09:05", now: now), "首次公开 2025/3/2 9:05")
        XCTAssertEqual(MorningCopy.documentHeading(title: nil, text: "【海特生物：授权】后续", sourceLabel: "金十快讯"), "海特生物：授权")
        XCTAssertEqual(MorningCopy.documentHeading(title: "已有标题", text: "【不用】", sourceLabel: "金十快讯"), "已有标题")
        XCTAssertEqual(MorningCopy.documentHeading(title: nil, text: "没有括号", sourceLabel: "金十快讯"), "金十快讯")
        XCTAssertEqual(MorningCopy.sourceName(" 金十快讯 "), "金十快讯")
    }

    private func decode<T: Decodable>(_ name: String, as type: T.Type) throws -> T {
        try JSONDecoder().decode(type, from: fixture(name))
    }

    private func fixture(_ name: String) throws -> Data {
        let bundle = Bundle(for: MorningContractTests.self)
        let url = bundle.url(forResource: name, withExtension: "json")
            ?? bundle.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
        return try Data(contentsOf: try XCTUnwrap(url, "缺少夹具 \(name).json"))
    }

    private func expectFailure(_ expected: MorningClientError, status: Int, data: Data, file: StaticString = #filePath, line: UInt = #line, _ call: (MorningAPI) async throws -> Void) async {
        let transport = ScriptTransport()
        transport.next = (status, data)
        let api = MorningAPI(baseURL: URL(string: "https://fixture.invalid")!, token: "fixture-token-not-real", transport: transport)
        await XCTAssertThrowsErrorAsync(try await call(api), expected, file: file, line: line)
    }
}

private final class ScriptTransport: MorningTransport, @unchecked Sendable {
    var next: (Int, Data)?
    var error: Error?
    var route: ((URLRequest) -> (Int, Data))?
    private let lock = NSLock()
    private var stored: [URLRequest] = []

    init(error: Error? = nil) { self.error = error }

    var requests: [URLRequest] {
        lock.lock()
        defer { lock.unlock() }
        return stored
    }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        lock.lock()
        stored.append(request)
        let error = error
        let next = next
        lock.unlock()
        if let error { throw error }
        let (status, data) = next ?? route?(request) ?? (500, Data())
        let url = request.url ?? URL(string: "https://fixture.invalid")!
        guard let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: nil) else {
            throw URLError(.badServerResponse)
        }
        return (data, response)
    }
}

private func XCTAssertThrowsErrorAsync<T>(_ expression: @autoclosure () async throws -> T, _ expected: MorningClientError, file: StaticString = #filePath, line: UInt = #line) async {
    do {
        _ = try await expression()
        XCTFail("应当抛出 \(expected)", file: file, line: line)
    } catch let error as MorningClientError {
        XCTAssertEqual(error, expected, file: file, line: line)
    } catch {
        XCTFail("应当抛出 \(expected)，实际 \(error)", file: file, line: line)
    }
}
