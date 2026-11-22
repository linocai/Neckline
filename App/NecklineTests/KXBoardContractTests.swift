import XCTest
@testable import Neckline

/// KX V4（schema_version 5）跨端解码契约、API 校验与文案格式化测试。
final class KXBoardContractTests: XCTestCase {
    func testDecodesAllFiveV4BackendFixtures() throws {
        // 1. 真实脱敏样本投影（kx_board_v4.json）
        let board = try decode("kx_board_v4")
        XCTAssertEqual(board.schemaVersion, 5)
        XCTAssertEqual(board.status, "ok")
        XCTAssertEqual(board.version, "kx-20261120-003")
        XCTAssertEqual(board.asOf, "2026-11-20")
        XCTAssertEqual(board.deepseekStatus, "ok")
        XCTAssertFalse(board.stale)
        XCTAssertNil(board.alertBannerText)
        XCTAssertEqual(board.capacity.used, 19)
        XCTAssertEqual(board.capacity.max, 25)
        XCTAssertEqual(board.capacity.leaderCount, 8)
        XCTAssertEqual(board.capacity.hotCount, 11)
        XCTAssertEqual(board.capacity.elimCount, 0)
        XCTAssertEqual(board.sentiment.days.count, 5)
        let latest = try XCTUnwrap(board.latestSentimentDay)
        XCTAssertEqual(latest.date, "2026-11-20")
        XCTAssertEqual(latest.zt, 25)
        XCTAssertEqual(latest.dt, 57)
        XCTAssertEqual(latest.zbRate ?? 0, 21.9, accuracy: 0.01)
        XCTAssertEqual(latest.maxLbc, 4)
        XCTAssertEqual(latest.highest.map(\.name), ["真视通", "盛剑科技"])
        XCTAssertEqual(board.sentiment.judgment?.stage, "退潮加速 · 局部反核")
        XCTAssertFalse(board.factions.isEmpty)
        XCTAssertEqual(board.items.count, 19)
        XCTAssertEqual(board.items(in: .leader).count, 8)
        XCTAssertEqual(board.items(in: .hot).count, 11)
        XCTAssertEqual(board.items(in: .elim).count, 0)

        let first = try XCTUnwrap(board.items.first)
        XCTAssertEqual(first.code, "002771")
        XCTAssertEqual(first.name, "真视通")
        XCTAssertEqual(first.facts.traj.count, 5)
        XCTAssertNotNil(first.facts.lhb)
        XCTAssertTrue(first.judgment.factions.contains(where: \.primary))

        // 2. DeepSeek 失败降级（kx_board_v4_partial.json）
        let partial = try decode("kx_board_v4_partial")
        XCTAssertEqual(partial.status, "partial")
        XCTAssertEqual(partial.deepseekStatus, "failed")
        XCTAssertNotNil(partial.alertBannerText)
        XCTAssertTrue(partial.alertBannerText?.contains("DeepSeek") == true)

        // 3. 缓存兜底（kx_board_v4_stale.json）
        let stale = try decode("kx_board_v4_stale")
        XCTAssertTrue(stale.stale)
        XCTAssertEqual(stale.staleReason, "KX 暂时连不上。")
        XCTAssertTrue(stale.alertBannerText?.contains("KX 暂时连不上") == true)

        // 4. 含淘汰区、全部 5 种节奏、回流与可移出徽章（kx_board_v4_elim.json）
        let elimBoard = try decode("kx_board_v4_elim")
        let elimItems = elimBoard.items(in: .elim)
        XCTAssertEqual(elimItems.count, 2)
        XCTAssertEqual(elimItems[0].elimDays, 1)
        XCTAssertFalse(elimItems[0].nextRemove)
        XCTAssertEqual(elimItems[0].changeBadge, .elimNew)
        XCTAssertEqual(elimItems[0].judgment.rhythm.kind, .dead)
        XCTAssertNil(elimItems[0].facts.lhb)
        XCTAssertEqual(elimItems[1].elimDays, 2)
        XCTAssertTrue(elimItems[1].nextRemove)
        XCTAssertEqual(elimItems[1].changeBadge, .elimOut)
        XCTAssertEqual(elimBoard.items[0].changeBadge, .up)
        XCTAssertEqual(elimBoard.items[1].changeBadge, .return)
        XCTAssertEqual(elimBoard.capacity.nextFree, 2)

        // 5. 未知协议版本 503 响应（kx_board_unavailable.json）
        let failure = try JSONDecoder().decode(KXFailureBody.self, from: fixture("kx_board_unavailable"))
        XCTAssertEqual(failure.reason, "kx_unsupported_schema")
        XCTAssertTrue(failure.message?.contains("协议版本暂不支持") == true)
        XCTAssertThrowsError(try JSONDecoder().decode(KXBoard.self, from: fixture("kx_board_unavailable")))
    }

    func testAPISendsRefreshQueryAndRejectsUnsupportedSchema() async throws {
        let transport = KXScriptTransport()
        transport.next = (200, try fixture("kx_board_v4"))
        let api = KXBoardAPI(baseURL: URL(string: "https://fixture.invalid")!, token: "fixture-token-not-real", transport: transport)

        let normal = try await api.board(forceRefresh: false)
        XCTAssertEqual(normal.schemaVersion, 5)
        XCTAssertEqual(transport.requests.last?.url?.absoluteString, "https://fixture.invalid/api/v4/kx/board")

        _ = try await api.board(forceRefresh: true)
        XCTAssertEqual(transport.requests.last?.url?.absoluteString, "https://fixture.invalid/api/v4/kx/board?refresh=1")

        // 503 kx_unsupported_schema
        transport.next = (503, try fixture("kx_board_unavailable"))
        await expectThrows(.unsupportedSchema("KX 看板协议版本暂不支持（收到 schema_version=4，需要 5），未强行解析。")) {
            _ = try await api.board()
        }

        // 客户端防线：即便 HTTP 200 且 schema 6 结构完全改变，也先报 .unsupportedSchema 而非 .unreadable
        let alienSchema6 = #"{"schemaVersion": 6, "brandNewStructure": {"foo": "bar"}}"#
        transport.next = (200, Data(alienSchema6.utf8))
        await expectThrows(.unsupportedSchema("KX 看板协议版本暂不支持（收到 schema_version=6，需要 5），未强行解析。")) {
            _ = try await api.board()
        }
    }

    func testAlertMessagesCombineStaleDeepSeekAndFactWarningsWithoutMasking() throws {
        let baseData = try fixture("kx_board_v4_partial")
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: baseData) as? [String: Any])
        json["stale"] = true
        json["staleReason"] = "KX 暂时连不上。"
        json["factWarnings"] = ["002086 龙虎榜核验提示"]
        let mergedData = try JSONSerialization.data(withJSONObject: json)
        let board = try JSONDecoder().decode(KXBoard.self, from: mergedData)
        XCTAssertTrue(board.alertMessages.contains(where: { $0.contains("KX 暂时连不上") }))
        XCTAssertTrue(board.alertMessages.contains(where: { $0.contains("DeepSeek") }))
        XCTAssertTrue(board.alertMessages.contains(where: { $0.contains("002086") }))
    }

    func testCopyFormattingTreatsNilAsDashNeverZero() {
        XCTAssertEqual(KXCopy.fmtPct(nil), "—")
        XCTAssertEqual(KXCopy.fmtPct(9.97), "+9.97%")
        XCTAssertEqual(KXCopy.fmtPct(-7.21), "-7.21%")
        XCTAssertEqual(KXCopy.fmtPct1(nil), "—")
        XCTAssertEqual(KXCopy.fmtPrice(nil), "—")
        XCTAssertEqual(KXCopy.fmtTurnover(nil), "—")
        XCTAssertEqual(KXCopy.fmtRatioPct(nil), "—")
        XCTAssertEqual(KXCopy.fmtWanSigned(nil), "—")
        XCTAssertEqual(KXCopy.fmtWanSigned(31435.6), "+3.14亿")
        XCTAssertEqual(KXCopy.fmtWanSigned(-781.8), "-782万")
        XCTAssertEqual(KXCopy.fmtWanUnsigned(14113.0), "1.41亿")
        XCTAssertEqual(KXCopy.monthDay("2026-11-20"), "11-20")
        XCTAssertEqual(KXCopy.generatedShortTime("2026-11-20T20:03:15+08:00"), "11-20 20:03")
    }

    // MARK: Helpers

    private func decode(_ name: String) throws -> KXBoard {
        try JSONDecoder().decode(KXBoard.self, from: fixture(name))
    }

    private func fixture(_ name: String) throws -> Data {
        let bundle = Bundle(for: KXBoardContractTests.self)
        let url = bundle.url(forResource: name, withExtension: "json")
            ?? bundle.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
        return try Data(contentsOf: try XCTUnwrap(url, "缺少夹具 \(name).json"))
    }

    private func expectThrows(_ expected: KXBoardError, file: StaticString = #filePath, line: UInt = #line, _ call: () async throws -> Void) async {
        do {
            try await call()
            XCTFail("应当抛出 \(expected)", file: file, line: line)
        } catch let error as KXBoardError {
            XCTAssertEqual(error, expected, file: file, line: line)
        } catch {
            XCTFail("应当抛出 \(expected)，实际 \(error)", file: file, line: line)
        }
    }
}

private final class KXScriptTransport: MorningTransport, @unchecked Sendable {
    var next: (Int, Data)?
    var error: Error?
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
        let (status, data) = next ?? (500, Data())
        let url = request.url ?? URL(string: "https://fixture.invalid")!
        guard let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: nil) else {
            throw URLError(.badServerResponse)
        }
        return (data, response)
    }
}
