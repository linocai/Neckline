import XCTest
@testable import Neckline

/// Q 期货看板（schema_version: 3）跨端解码契约、API 校验与文案格式化测试。
final class QihuoBoardContractTests: XCTestCase {
    func testDecodesAllFourV3BackendFixtures() throws {
        // 1. 真实脱敏样本投影（qihuo_board_v3.json）
        let board = try decode("qihuo_board_v3")
        XCTAssertEqual(board.schemaVersion, 3)
        XCTAssertEqual(board.version, "qihuo-20261120-r54")
        XCTAssertEqual(board.asOfDate, "2026-11-20")
        XCTAssertEqual(board.retentionDays, 15)
        XCTAssertTrue(board.schedule.silent)
        XCTAssertFalse(board.stale)
        XCTAssertEqual(board.pool.activeCount, 5)
        XCTAssertEqual(board.pool.pausedCount, 11)
        XCTAssertEqual(board.pool.totalStoredCount, 16)
        XCTAssertEqual(board.products.map(\.code), ["SH", "L", "MA", "pp", "eg"])

        // 烧碱 SH：仓单 0.0 必须保留，不可当作 nil
        let sh = try XCTUnwrap(board.products.first { $0.code == "SH" })
        XCTAssertEqual(sh.slug, "sh")
        XCTAssertEqual(sh.snapshot.wsr?.value, 0.0)
        XCTAssertFalse(sh.spotPending)
        XCTAssertNotNil(sh.snapshot.spot?.value)
        XCTAssertFalse(sh.basic.sections.isEmpty)
        XCTAssertFalse(sh.holdRows.isEmpty)

        // 乙二醇 eg：spot/basis 诚实留空且 spotPending == true
        let eg = try XCTUnwrap(board.products.first { $0.code == "eg" })
        XCTAssertTrue(eg.spotPending)
        XCTAssertNil(eg.snapshot.spot)
        XCTAssertNil(eg.snapshot.basis)

        // 2. 来源异常提示样本（qihuo_board_v3_warn.json）
        let warn = try decode("qihuo_board_v3_warn")
        let warnSH = try XCTUnwrap(warn.products.first { $0.code == "SH" })
        XCTAssertEqual(warnSH.status.news.state, "failed")
        XCTAssertTrue(warnSH.status.news.message?.contains("HTTP 502") == true)
        XCTAssertTrue(warnSH.status.news.text?.contains("失败") == true)

        // 3. 缓存兜底样本（qihuo_board_v3_stale.json）
        let stale = try decode("qihuo_board_v3_stale")
        XCTAssertTrue(stale.stale)
        XCTAssertEqual(stale.staleReason, "Qihuo 暂时连不上，当前展示上一份缓存快照。")

        // 4. 未知协议版本 503 响应（qihuo_board_unavailable.json）
        let failure = try JSONDecoder().decode(KXFailureBody.self, from: fixture("qihuo_board_unavailable"))
        XCTAssertEqual(failure.reason, "qihuo_unsupported_schema")
        XCTAssertTrue(failure.message?.contains("协议版本暂不支持") == true)
        XCTAssertThrowsError(try JSONDecoder().decode(QihuoBoard.self, from: fixture("qihuo_board_unavailable")))
    }

    func testAPISendsRefreshQueryAndRejectsUnsupportedSchema() async throws {
        let transport = QihuoScriptTransport()
        transport.next = (200, try fixture("qihuo_board_v3"))
        let api = QihuoBoardAPI(baseURL: URL(string: "https://fixture.invalid")!, token: "fixture-token-not-real", transport: transport)

        let normal = try await api.board(forceRefresh: false)
        XCTAssertEqual(normal.schemaVersion, 3)
        XCTAssertEqual(transport.requests.last?.url?.absoluteString, "https://fixture.invalid/api/v4/qihuo/board")

        _ = try await api.board(forceRefresh: true)
        XCTAssertEqual(transport.requests.last?.url?.absoluteString, "https://fixture.invalid/api/v4/qihuo/board?refresh=1")

        // 503 qihuo_unsupported_schema
        transport.next = (503, try fixture("qihuo_board_unavailable"))
        await expectThrows(.unsupportedSchema("Qihuo 数据协议版本暂不支持（收到 schema 4，当前仅支持 schema 3）。")) {
            _ = try await api.board()
        }

        // 客户端防线：即便 HTTP 200 且 schema 4、缺失、null、字符串或布尔版本，均优先抛出 .unsupportedSchema
        let alienSchema4 = #"{"schemaVersion": 4, "brandNew": true}"#
        transport.next = (200, Data(alienSchema4.utf8))
        await expectThrows(.unsupportedSchema("Q 期货协议版本暂不支持（收到 schema_version=4，需要 3），未强行解析。")) {
            _ = try await api.board()
        }

        for invalidProbe in [
            #"{"products": []}"#,
            #"{"schemaVersion": null}"#,
            #"{"schemaVersion": "3"}"#,
            #"{"schemaVersion": true}"#,
            #"{"schemaVersion": 3.0}"#,
            #"{"schemaVersion": 3e0}"#,
            #"[]"#,
            #"null"#,
            #"123"#,
            #""ok""#,
            #"true"#,
        ] {
            transport.next = (200, Data(invalidProbe.utf8))
            await expectThrows(.unsupportedSchema("Q 期货协议版本缺失或无效（需要 schema_version=3），未强行解析。")) {
                _ = try await api.board()
            }
        }
    }

    func testSafeExternalURLRejectsScriptsAndTokenizedReadEndpoints() {
        XCTAssertNotNil(QihuoProductCard.safeExternalURL("https://futures.eastmoney.com/a/123.html"))
        XCTAssertNotNil(QihuoProductCard.safeExternalURL("http://example.com/news"))
        XCTAssertNil(QihuoProductCard.safeExternalURL("javascript:alert(1)"))
        XCTAssertNil(QihuoProductCard.safeExternalURL("file:///etc/passwd"))
        XCTAssertNil(QihuoProductCard.safeExternalURL("https://nk.linotsai.top/read/secretkey1234567890123456/qihuo"))
        XCTAssertNil(QihuoProductCard.safeExternalURL("https://linotsai.top/qihuo/read/secret"))
        XCTAssertNil(QihuoProductCard.safeExternalURL("https://user:pass@example.com/news"))
        XCTAssertNil(QihuoProductCard.safeExternalURL("not a url"))
    }

    func testCopyFormattingDistinguishesZeroFromNil() {
        XCTAssertEqual(QihuoCopy.fmtNumber(0.0), "0")
        XCTAssertEqual(QihuoCopy.fmtNumber(nil), "—")
        XCTAssertEqual(QihuoCopy.fmtNumber(2148.5), "2,148.5")
        XCTAssertEqual(QihuoCopy.fmtInt(0), "0")
        XCTAssertEqual(QihuoCopy.fmtInt(nil), "—")
        XCTAssertEqual(QihuoCopy.fmtSignedInt(0), "0")
        XCTAssertEqual(QihuoCopy.fmtSignedInt(1250), "+1,250")
        XCTAssertEqual(QihuoCopy.fmtSignedInt(-300), "-300")
        XCTAssertEqual(QihuoCopy.fmtSignedInt(nil), "—")
        XCTAssertTrue(QihuoCopy.fmtSignedInt(Int.min).hasPrefix("-"))
        XCTAssertTrue(QihuoCopy.fmtShortW(Int.min).hasPrefix("-"))
        XCTAssertEqual(QihuoCopy.fmtPct(0.0), "0.00%")
        XCTAssertEqual(QihuoCopy.fmtPct(1.23), "+1.23%")
        XCTAssertEqual(QihuoCopy.fmtPct(-0.85), "-0.85%")
        XCTAssertEqual(QihuoCopy.fmtPct(nil), "—")
        XCTAssertEqual(QihuoCopy.fmtShortW(15230), "+1.52万")
        XCTAssertEqual(QihuoCopy.fmtShortW(-850), "-850")
        XCTAssertEqual(QihuoCopy.fmtShortW(nil), "—")
        XCTAssertEqual(QihuoCopy.shortDay("2026-11-20"), "11-20")
        XCTAssertEqual(QihuoCopy.shortDay(nil), "—")
    }

    // MARK: Helpers

    private func decode(_ name: String) throws -> QihuoBoard {
        try JSONDecoder().decode(QihuoBoard.self, from: fixture(name))
    }

    private func fixture(_ name: String) throws -> Data {
        let bundle = Bundle(for: QihuoBoardContractTests.self)
        let url = bundle.url(forResource: name, withExtension: "json")
            ?? bundle.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
        return try Data(contentsOf: try XCTUnwrap(url, "缺少夹具 \(name).json"))
    }

    private func expectThrows(_ expected: QihuoBoardError, file: StaticString = #filePath, line: UInt = #line, _ call: () async throws -> Void) async {
        do {
            try await call()
            XCTFail("应当抛出 \(expected)", file: file, line: line)
        } catch let error as QihuoBoardError {
            XCTAssertEqual(error, expected, file: file, line: line)
        } catch {
            XCTFail("应当抛出 \(expected)，实际 \(error)", file: file, line: line)
        }
    }
}

private final class QihuoScriptTransport: MorningTransport, @unchecked Sendable {
    var next: (Int, Data)?
    var error: Error?
    private let lock = NSLock()
    private var stored: [URLRequest] = []

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
