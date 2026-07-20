//
//  DTODecodeTests.swift
//  NecklineTests — APIClient 解码对齐后端真实 JSON 样例(§五 阶段4C「逐字段对齐,别猜」)。
//
//  用 `MockURLProtocol` 注入固定响应(沿 Neckline 后端 `MockTransport`/`httpx.MockTransport`
//  同款思路:可注入 transport,免联网单测),直接调真实 `APIClient` 公开方法,同时验证
//  「URL 构造 + JSON 解码 + DTO→展示模型映射」整条链路,而不是孤立测一个私有 DTO。
//  JSON 样例字段逐个对照 `neckline/api/schemas.py` + `tests/test_api_*.py` 真实断言写死,
//  不臆造字段名。
//

import XCTest
@testable import Neckline

// MARK: - URLProtocol 网络桩(标准 Swift 测试技巧,等价于 Python 侧的 MockTransport)

final class MockURLProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (Int, Data))?
    static var lastRequest: URLRequest?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.lastRequest = request
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badURL))
            return
        }
        do {
            let (status, data) = try handler(request)
            let resp = HTTPURLResponse(url: request.url!, statusCode: status,
                                       httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
            client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private func mockSession() -> URLSession {
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [MockURLProtocol.self]
    return URLSession(configuration: config)
}

private func jsonData(_ s: String) -> Data { s.data(using: .utf8)! }

final class DTODecodeTests: XCTestCase {

    override func tearDown() {
        MockURLProtocol.handler = nil
        MockURLProtocol.lastRequest = nil
        super.tearDown()
    }

    // MARK: - 4A.2 报告(字段样例逐字对照 tests/test_api_report_board.py::test_report_latest)

    func testDecodeReportLatest() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260717",
          "generatedAt": "2026-07-17T08:05:00+00:00",
          "strategyVersion": "v1",
          "sentiment": {
            "trade_date": "2026-07-17",
            "limit_up_count": 34,
            "limit_down_count": 212,
            "zaban_count": 12,
            "zaban_rate": 0.28,
            "max_consec_limit_up": 3,
            "prev_limit_up_premium_avg": -0.015,
            "prev_limit_up_sample": 20,
            "position_quota": "休息",
            "quota_reason": "涨停34家/跌停212家/炸板率28%/最高连板3板"
          },
          "sectors": [
            {"index_code": "883300.TI", "name": "AI", "board_age": 3, "ret_20d": 0.12, "bonus": 3.0, "rank": 1}
          ],
          "candidates": [
            {
              "rank": 1, "code": "600001.SH", "name": "示例甲", "score": 88.0, "board": "主板",
              "buyPoint": "回调低吸:站稳10日线", "stop": "参考止损价约 9.50 元(-5%)",
              "target": "不设固定止盈线;持有满5日无条件离场",
              "invalidation": "次日低开≤-2%且全天未翻红…",
              "invalidationSpec": {"low_open_pct": -0.02, "vwap_break": true},
              "entrySpec": {"buypoint": "pullback", "ma10": 9.9},
              "formTags": ["浅回调贴前高", "放量"],
              "hotSectors": ["AI(板块年龄3天,20日+12.0%)"],
              "sectorNames": ["AI"],
              "llmJudgment": {"verdict": "通过", "narrative": "催化站得住。", "degraded": false}
            },
            {
              "rank": 2, "code": "600002.SH", "name": "示例乙", "score": 80.0, "board": "主板",
              "buyPoint": "b", "stop": "s", "target": "t", "invalidation": "i",
              "formTags": [], "hotSectors": [], "sectorNames": [],
              "llmJudgment": null
            }
          ],
          "degraded": false,
          "reason": ""
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())

        let report = try await client.fetchReportLatest()
        XCTAssertEqual(report.tradeDate, "20260717")
        XCTAssertEqual(report.strategyVersion, "v1")
        XCTAssertFalse(report.degraded)
        XCTAssertEqual(report.sentiment?.positionQuota, "休息")
        XCTAssertEqual(report.sentiment?.limitUpCount, 34)
        XCTAssertEqual(report.sentiment?.limitDownCount, 212)
        XCTAssertEqual(report.sentiment?.prevLimitUpPremiumAvg, -0.015)
        XCTAssertEqual(report.sectors.first?.boardAge, 3)
        XCTAssertEqual(report.sectors.first?.ret20d, 0.12)
        XCTAssertEqual(report.candidates.count, 2)
        let c0 = report.candidates[0]
        XCTAssertEqual(c0.code, "600001.SH")
        XCTAssertTrue(c0.buyPoint.contains("回调低吸"))
        XCTAssertTrue(c0.stop.contains("-5%"))
        XCTAssertEqual(c0.formTags, ["浅回调贴前高", "放量"])
        XCTAssertEqual(c0.llmJudgment?.verdict, "通过")
        XCTAssertNil(report.candidates[1].llmJudgment)   // 未审判候选无 llmJudgment(nil,非降级占位)
    }

    func testDecodeReportDegradedEmpty() async throws {
        let json = jsonData("""
        {"tradeDate": "", "generatedAt": "", "strategyVersion": "", "sentiment": null,
         "sectors": [], "candidates": [], "degraded": true, "reason": "no_report"}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        XCTAssertTrue(report.degraded)
        XCTAssertEqual(report.reason, "no_report")
        XCTAssertTrue(report.candidates.isEmpty)
        XCTAssertNil(report.sentiment)
    }

    /// `report?date=` 带 query,顺带验证请求真走了 makeURL(URL 里 "?" 未被编码)。
    func testFetchReportByDateUsesQueryURL() async throws {
        let json = jsonData("""
        {"tradeDate": "20260716", "generatedAt": "g", "strategyVersion": "v1", "sentiment": null,
         "sectors": [], "candidates": [], "degraded": false, "reason": ""}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReport(date: "20260716")
        XCTAssertEqual(report.tradeDate, "20260716")
        let reqURL = MockURLProtocol.lastRequest?.url?.absoluteString ?? ""
        XCTAssertTrue(reqURL.contains("?date=20260716"), "实际请求 URL: \(reqURL)")
        XCTAssertFalse(reqURL.contains("%3F"))
    }

    // MARK: - 4A.3 盘中看板(样例对照 test_board_aggregates_events)

    func testDecodeBoard() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260717", "asof": "2026-07-17T14:32:00",
          "retreatBrake": {"active": true, "reason": "炸板率飙升,今日计划作废"},
          "events": [
            {"sentinel": "买点", "code": "600001.SH", "name": "示例甲",
             "eventKey": "entry-600001.SH-trigger", "verdict": "买点确认:站稳VWAP", "ts": "2026-07-17T10:05:00"},
            {"sentinel": "持仓", "code": "600003.SH", "name": "示例丙",
             "eventKey": "holding-600003.SH-stop_approach", "verdict": "逼近止损线", "ts": "2026-07-17T10:06:00"}
          ]
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let board = try await client.fetchBoard()
        XCTAssertTrue(board.retreatBrake.active)
        XCTAssertTrue(board.retreatBrake.reason.contains("炸板率飙升"))
        XCTAssertEqual(board.events.count, 2)
        XCTAssertEqual(board.events[0].kind, .entry)
        XCTAssertEqual(board.events[1].kind, .holding)
    }

    // MARK: - 4A.4 持仓(样例对照 test_open_list_close_roundtrip)

    func testDecodePositions() async throws {
        let json = jsonData("""
        {"holdings": [
          {"id": 1, "code": "600519.SH", "name": "贵州茅台", "buyPrice": 1500.0, "qty": 100,
           "entryReason": "回调低吸", "buyDate": "20260716", "price": 1520.0, "status": "holding",
           "stopLine": 1425.0, "stopOrderChecked": false}
        ]}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let positions = try await client.fetchPositions()
        XCTAssertEqual(positions.count, 1)
        let p = positions[0]
        XCTAssertEqual(p.code, "600519.SH")
        XCTAssertEqual(p.stopLine, 1425.0)
        XCTAssertEqual(p.buyPrice, 1500.0)
        XCTAssertTrue(p.hasLivePrice)
        XCTAssertFalse(p.hasBrokenStop)   // 1520 > 1425
        XCTAssertEqual(p.pnlAmount, (1520.0 - 1500.0) * 100, accuracy: 0.001)
    }

    /// price=0(拉不到实时价)不可与"跌停 0 元"混淆——`hasLivePrice` 必须为 false。
    func testPositionZeroPriceMeansNoLivePrice() async throws {
        let json = jsonData("""
        {"holdings": [
          {"id": 2, "code": "600001.SH", "name": "甲", "buyPrice": 10.0, "qty": 100,
           "entryReason": "", "buyDate": "20260716", "price": 0.0, "status": "holding",
           "stopLine": 9.5, "stopOrderChecked": false}
        ]}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let p = try await client.fetchPositions()[0]
        XCTAssertFalse(p.hasLivePrice)
        XCTAssertFalse(p.hasBrokenStop)      // 无实时价不误判破线
        XCTAssertNil(p.distToStopPct)
        XCTAssertEqual(p.pnlPct, 0)
    }

    // MARK: - 4A.4 开仓请求体(snake_case 入参,对照 PositionOpenIn)

    func testOpenPositionRequestBodyUsesSnakeCase() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["buy_price"] as? Double, 1500.0)
            XCTAssertEqual(obj?["entry_reason"] as? String, "回调低吸")
            XCTAssertEqual(obj?["code"] as? String, "600519.SH")
            let resp = jsonData("""
            {"ok": true, "position_id": 7, "stop_line": 1425.0}
            """)
            return (200, resp)
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.openPosition(code: "600519.SH", name: "贵州茅台", buyPrice: 1500.0,
                                              qty: 100, entryReason: "回调低吸")
        XCTAssertEqual(r.positionId, 7)
        XCTAssertEqual(r.stopLine, 1425.0)
    }

    /// 404 not_holding 映射(对照 test_close_nonexistent_404 的 detail 形状)。
    func testCloseNonexistentMapsToNotHolding() async throws {
        MockURLProtocol.handler = { _ in
            (404, jsonData("""
            {"detail": {"ok": false, "reason": "not_holding"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.closePosition(id: 999, sellPrice: 1.0)
            XCTFail("应抛 notHolding")
        } catch APIError.notHolding {
            // 期望路径
        }
    }

    // MARK: - 4A.5 问询台(样例对照 test_inquiry_endpoint;裁决二值)

    func testDecodeInquiryPass() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"ok": true, "code": "600001.SH", "reply": "结合搜索,题材催化尚在,未见明显利空。",
             "verdict": "初审通过进海选池", "evidence": ["主板,非ST", "板块年龄3天"], "degraded": false}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.sendInquiry(code: "600001.SH", messages: [ChatMessage(role: .user, text: "看看这票")])
        XCTAssertEqual(r.verdict, .pass)
        XCTAssertEqual(r.verdict.label, "初审通过进海选池")
        XCTAssertFalse(r.evidence.isEmpty)
        XCTAssertFalse(r.degraded)
    }

    func testDecodeInquiryReject() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"ok": true, "code": "600002.SH", "reply": "ST 状态不符合纪律。",
             "verdict": "不符合", "evidence": ["ST 剔除"], "degraded": false}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.sendInquiry(code: "600002.SH", messages: [])
        XCTAssertEqual(r.verdict, .reject)
    }

    // MARK: - 4A.5 设置(样例对照 test_settings_default / test_put_llm_key_not_leaked)

    func testDecodeSettings() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"llmProvider": "glm", "llmKeySet": true, "push": {"report": true, "retreatBrake": false}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let s = try await client.fetchSettings()
        XCTAssertEqual(s.llmProvider, "glm")
        XCTAssertTrue(s.llmKeySet)
        XCTAssertTrue(s.push.report)
        XCTAssertFalse(s.push.retreatBrake)
    }

    func testPutSettingsLLMBodyNeverLogsButDoesSendKeyOnce() async throws {
        // 客户端职责只是"发一次、不缓存、不回显";这里断言请求体确实带上了本次填写的
        // key(否则后端收不到就没法激活),但响应解码路径(SettingsOut)不含任何 key 字段
        // ——类型层面就不存在能回显明文的字段,契约漂移会在编译期直接报错。
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["apiKey"] as? String, "sk-secret-abc")
            XCTAssertEqual(obj?["provider"] as? String, "glm")
            return (200, jsonData("""
            {"ok": true}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let ok = try await client.putSettingsLLM(provider: .glm, apiKey: "sk-secret-abc")
        XCTAssertTrue(ok)
    }
}

private extension URLRequest {
    /// URLSession 经自定义 `URLProtocol` 转发请求时,常把 `httpBody` 内部转成
    /// `httpBodyStream`(`startLoading()` 里拿到的 `request.httpBody` 因而是 nil,
    /// 这是 URLProtocol 测试桩的已知坑,不是 APIClient 的 bug)——两路都试,谁有读谁。
    func httpBodyOrStream() -> Data? {
        if let body = httpBody { return body }
        guard let stream = httpBodyStream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        let bufferSize = 4096
        var buffer = [UInt8](repeating: 0, count: bufferSize)
        while stream.hasBytesAvailable {
            let read = stream.read(&buffer, maxLength: bufferSize)
            if read > 0 { data.append(buffer, count: read) } else { break }
        }
        return data
    }
}
