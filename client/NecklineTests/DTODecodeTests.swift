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

    /// v1.1-G.3:看板事件补 precall/d5exit 两新类(样例对照
    /// test_board_labels_precall_and_d5exit_events)。
    func testDecodeBoardPrecallAndD5ExitEvents() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260721", "asof": "2026-07-21T09:26:00",
          "retreatBrake": {"active": false, "reason": ""},
          "events": [
            {"sentinel": "盘前校准", "code": "600004.SH", "name": "示例丁",
             "eventKey": "precall-600004.SH-gap_up_invalidate",
             "verdict": "集合竞价开盘12.00高于买点参考位11.00 9.1%,今日买点已变形失效。",
             "ts": "2026-07-21T09:25:30"},
            {"sentinel": "D5退出", "code": "600005.SH", "name": "示例戊",
             "eventKey": "d5exit-600005.SH-trigger",
             "verdict": "示例戊 今日 D5 时间退出日,按计划离场。", "ts": "2026-07-21T09:25:30"}
          ]
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let board = try await client.fetchBoard()
        XCTAssertEqual(board.events.count, 2)
        XCTAssertEqual(board.events[0].kind, .precall)
        XCTAssertEqual(board.events[1].kind, .d5exit)
        XCTAssertTrue(board.events[1].verdict.contains("D5 时间退出日"))
    }

    // MARK: - 4A.4 持仓(样例对照 test_open_list_close_roundtrip)

    func testDecodePositions() async throws {
        let json = jsonData("""
        {"holdings": [
          {"id": 1, "code": "600519.SH", "name": "贵州茅台", "buyPrice": 1500.0, "qty": 100,
           "entryReason": "回调低吸", "buyDate": "20260716", "price": 1520.0, "status": "holding",
           "stopLine": 1425.0, "stopOrderChecked": false,
           "dCount": 2, "maxHoldDays": 5, "distToStopPct": 0.0625, "retraceState": null,
           "todayAction": "持有中(D2/D5)"}
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
        // v1.1-B.1 生命周期派生字段(服务端下发,不重算)
        XCTAssertEqual(p.dCount, 2)
        XCTAssertEqual(p.maxHoldDays, 5)
        XCTAssertEqual(p.distToStopPctServer, 0.0625)
        XCTAssertNil(p.retraceState)
        XCTAssertEqual(p.todayAction, "持有中(D2/D5)")
        XCTAssertFalse(p.isExitDay)
        XCTAssertEqual(p.todayActionTone, .neutral)
    }

    /// price=0(拉不到实时价)不可与"跌停 0 元"混淆——`hasLivePrice` 必须为 false。
    func testPositionZeroPriceMeansNoLivePrice() async throws {
        let json = jsonData("""
        {"holdings": [
          {"id": 2, "code": "600001.SH", "name": "甲", "buyPrice": 10.0, "qty": 100,
           "entryReason": "", "buyDate": "20260716", "price": 0.0, "status": "holding",
           "stopLine": 9.5, "stopOrderChecked": false,
           "dCount": 3, "maxHoldDays": 5, "distToStopPct": null, "retraceState": null,
           "todayAction": "持有中(D3/D5)"}
        ]}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let p = try await client.fetchPositions()[0]
        XCTAssertFalse(p.hasLivePrice)
        XCTAssertFalse(p.hasBrokenStop)      // 无实时价不误判破线
        XCTAssertNil(p.distToStopPct)
        XCTAssertNil(p.distToStopPctServer)
        XCTAssertEqual(p.pnlPct, 0)
    }

    /// v1.1-B.1/E.1:D5 时间退出日 + 回落止盈已触发的完整生命周期字段解码 + 展示层派生。
    func testDecodePositionD5ExitDayAndRetraceTriggered() async throws {
        let json = jsonData("""
        {"holdings": [
          {"id": 3, "code": "600002.SH", "name": "乙", "buyPrice": 20.0, "qty": 100,
           "entryReason": "", "buyDate": "20260710", "price": 21.0, "status": "holding",
           "stopLine": 19.0, "stopOrderChecked": false,
           "dCount": 5, "maxHoldDays": 5, "distToStopPct": 0.0952,
           "retraceState": {"peak": 23.0, "retracePct": 0.087, "triggered": true},
           "todayAction": "D5 时间退出日,按计划离场(时间退出是规则 v1 采纳纪律)"}
        ]}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let p = try await client.fetchPositions()[0]
        XCTAssertEqual(p.dCount, 5)
        XCTAssertTrue(p.isExitDay)
        XCTAssertEqual(p.retraceState?.triggered, true)
        XCTAssertEqual(p.retraceState?.peak, 23.0)
        XCTAssertEqual(p.todayActionTone, .bad, "D5 时间退出日必须是最高优先醒目(bad 色调)")
        XCTAssertTrue(p.todayAction.contains("D5"))
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

    /// v1.2-A2:`closeReason` 编码进请求体(camelCase,与既有 snake_case `sell_price`/
    /// `sell_time` 并存——契约本身如此,见 `CLAUDE.md`「PositionCloseIn 里 closeReason
    /// 是 camelCase」坑)。用 `httpBodyOrStream()` helper 两路读请求体。
    func testClosePositionEncodesCloseReasonAlongsideSnakeCaseFields() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["sell_price"] as? Double, 9.5)
            XCTAssertEqual(obj?["sell_time"] as? String, "20260722")
            XCTAssertEqual(obj?["closeReason"] as? String, "STOP_LOSS")
            return (200, jsonData("""
            {"ok": true}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let ok = try await client.closePosition(id: 1, sellPrice: 9.5, sellTime: "20260722", closeReason: "STOP_LOSS")
        XCTAssertTrue(ok)
    }

    /// 不选离场原因 → 请求体里**没有** `closeReason` 键(服务端按价格兜底判止损,不由
    /// 客户端二次猜)。⚠ 实测锁死:Swift 编译器自动合成的 `Encodable` 对 `Optional`
    /// 属性用 `encodeIfPresent`,nil 时直接省略该键(不是显式写 `"closeReason": null`)
    /// ——对后端 pydantic `Optional[...] = None` 字段语义等价(缺键与显式 null 均落
    /// `None`),但断言必须对齐**实际**编码结果,不能想当然认为是显式 null。
    func testClosePositionOmittedCloseReasonOmitsKeyFromRequestBody() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertNil(obj?["closeReason"], "不传 closeReason 时请求体不应含该键(也不能悄悄发空字符串占位)")
            return (200, jsonData("""
            {"ok": true}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        _ = try await client.closePosition(id: 1, sellPrice: 9.5)
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
            {"llmProvider": "glm", "llmKeySet": true,
             "push": {"report": true, "retreatBrake": false, "precall": true, "d5exit": false, "circuit": true},
             "reviewColMap": {"手续费": "费用合计"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let s = try await client.fetchSettings()
        XCTAssertEqual(s.llmProvider, "glm")
        XCTAssertTrue(s.llmKeySet)
        XCTAssertTrue(s.push.report)
        XCTAssertFalse(s.push.retreatBrake)
        // v1.1-G.1:推送开关扩到四字段(盘前校准 / D5 时间退出)。
        XCTAssertTrue(s.push.precall)
        XCTAssertFalse(s.push.d5exit)
        // v1.2-A2:第五字段(熔断提醒)。
        XCTAssertTrue(s.push.circuit)
        XCTAssertEqual(s.reviewColMap, ["手续费": "费用合计"])
    }

    /// v1.2-A2:PUT settings/push 请求体五字段一并发送(对照 test_put_push_toggles)。
    func testPutSettingsPushSendsFiveFields() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["report"] as? Bool, false)
            XCTAssertEqual(obj?["retreatBrake"] as? Bool, true)
            XCTAssertEqual(obj?["precall"] as? Bool, false)
            XCTAssertEqual(obj?["d5exit"] as? Bool, true)
            XCTAssertEqual(obj?["circuit"] as? Bool, false)
            return (200, jsonData("""
            {"ok": true}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let ok = try await client.putSettingsPush(report: false, retreatBrake: true, precall: false,
                                                   d5exit: true, circuit: false)
        XCTAssertTrue(ok)
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

    // MARK: - v1.2-E.5 一键补录预填推荐,区间双档(样例对照契约清单
    // `EntrySuggestionOut{ok,code,price,qtyLow,qtyHigh,capFloor,capCeil,stopLine}`)

    func testDecodeEntrySuggestionRange() async throws {
        MockURLProtocol.handler = { req in
            // code/price 走 query,须走 makeURL(同 §五 阶段4C 坑吸收②)。
            let url = req.url?.absoluteString ?? ""
            XCTAssertTrue(url.contains("?code=600001.SH&price=50.00"), "实际请求 URL: \(url)")
            return (200, jsonData("""
            {"ok": true, "code": "600001.SH", "price": 50.0,
             "qtyLow": 400, "qtyHigh": 800, "capFloor": 20000.0, "capCeil": 40000.0, "stopLine": 47.5}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let s = try await client.entrySuggestion(code: "600001.SH", price: 50.0)
        XCTAssertEqual(s.qtyLow, 400)
        XCTAssertEqual(s.qtyHigh, 800)
        XCTAssertEqual(s.capFloor, 20000.0)
        XCTAssertEqual(s.capCeil, 40000.0)
        XCTAssertEqual(s.stopLine, 47.5)
    }

    // MARK: - v1.1-C/F 自选池(watchlist)(样例对照 tests/test_api_watchlist.py)

    private static let sampleWatchlistCheckJSON = """
    {"code": "600001.SH", "name": "示例甲", "pinned": false, "source": "manual", "hasData": true,
     "close": 12.34, "board": "MAIN", "score": 77.7, "patternTags": ["均线多头"], "hotSectors": [],
     "sectorNames": [], "greenLight": true, "disqualifiers": [], "buyPointTriggered": true,
     "buyPoint": "回调低吸...", "stop": "止损...", "target": "目标...", "invalidation": "证伪...",
     "statusChanged": true, "llmJudgment": {"verdict": "通过", "narrative": "分析...", "degraded": false}}
    """

    func testDecodeWatchlistIncludesCheckSnapshot() async throws {
        let json = jsonData("""
        {"items": [
          {"code": "600001.SH", "name": "示例甲", "addedAt": "2026-07-20T10:00:00+00:00",
           "source": "manual", "note": "", "pinned": false, "updatedAt": "2026-07-20T10:00:00+00:00",
           "check": \(Self.sampleWatchlistCheckJSON)},
          {"code": "000001.SZ", "name": "平安银行", "addedAt": "2026-07-21T10:00:00+00:00",
           "source": "manual", "note": "", "pinned": true, "updatedAt": "2026-07-21T10:00:00+00:00",
           "check": null}
        ], "maxSize": 30}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let wl = try await client.fetchWatchlist()
        XCTAssertEqual(wl.maxSize, 30)
        XCTAssertEqual(wl.items.count, 2)
        XCTAssertEqual(wl.items[0].check?.score, 77.7)
        XCTAssertTrue(wl.items[0].check?.greenLight ?? false)
        XCTAssertTrue(wl.items[0].check?.buyPointTriggered ?? false)
        XCTAssertEqual(wl.items[0].check?.llmJudgment?.verdict, "通过")
        XCTAssertNil(wl.items[1].check, "从未体检过 → nil,不是报错")
        XCTAssertTrue(wl.items[1].pinned)
    }

    func testAddWatchlistRequestBodyAndFullResponse() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["code"] as? String, "600001.SH")
            XCTAssertEqual(obj?["name"] as? String, "示例甲")
            return (200, jsonData("""
            {"ok": true, "item": {"code": "600001.SH", "name": "示例甲",
             "addedAt": "2026-07-21T10:00:00+00:00", "source": "manual", "note": "",
             "pinned": false, "updatedAt": "2026-07-21T10:00:00+00:00", "check": null}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let item = try await client.addWatchlist(code: "600001.SH", name: "示例甲")
        XCTAssertEqual(item.code, "600001.SH")
        XCTAssertFalse(item.pinned)
    }

    /// 满 30 上限 → 422,`reason` 字面 "watchlist_full"(对照 test_add_over_cap_returns_422)。
    func testAddWatchlistFullMapsToValidationWithWatchlistFullReason() async throws {
        MockURLProtocol.handler = { _ in
            (422, jsonData("""
            {"detail": {"ok": false, "reason": "watchlist_full", "message": "自选池已达上限 30 只,请先移除再添加。"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.addWatchlist(code: "999999.SH")
            XCTFail("应抛 validation(watchlist_full)")
        } catch APIError.validation(let reason) {
            XCTAssertEqual(reason, "watchlist_full")
        }
    }

    /// 删除/取消置顶不存在的代码 → 404 not_found(与 positions 的 404 not_holding 分开映射,
    /// 对照 test_delete_nonexistent_404/test_pin_nonexistent_404)。
    func testRemoveAndPinNonexistentWatchlistCodeMapsToNotFound() async throws {
        MockURLProtocol.handler = { _ in
            (404, jsonData("""
            {"detail": {"ok": false, "reason": "not_found"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.removeWatchlist(code: "999999.SH")
            XCTFail("应抛 notFound")
        } catch APIError.notFound {
            // 期望路径
        }
        do {
            _ = try await client.pinWatchlist(code: "999999.SH", pinned: true)
            XCTFail("应抛 notFound")
        } catch APIError.notFound {
            // 期望路径
        }
    }

    func testPinWatchlistRequestBody() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["pinned"] as? Bool, true)
            return (200, jsonData("""
            {"ok": true}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let ok = try await client.pinWatchlist(code: "600001.SH", pinned: true)
        XCTAssertTrue(ok)
    }

    /// 对照 test_reconcile_ths_endpoint_diff:同花顺侧多一只、Neckline 侧多一只、一只两边都有。
    /// 断言 multipart 字段名是单数 `file`(**与下方 4D `uploadReview` 的 `files` 不同**,
    /// 混淆是真实会犯的错,§五 v1.1-F.5 坑吸收)。
    func testReconcileThsUsesSingularFileFieldAndDecodesDiff() async throws {
        MockURLProtocol.handler = { req in
            let contentType = req.value(forHTTPHeaderField: "Content-Type") ?? ""
            XCTAssertTrue(contentType.contains("multipart/form-data"))
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let bodyText = String(data: body, encoding: .utf8) ?? ""
            XCTAssertTrue(bodyText.contains("name=\"file\""), "字段名须为单数 file,非 files")
            XCTAssertFalse(bodyText.contains("name=\"files\""))
            XCTAssertTrue(bodyText.contains("自选股.txt"))
            return (200, jsonData("""
            {"ok": true, "onlyInThs": ["600000.SH"], "onlyInNeckline": ["600519.SH"], "both": ["000001.SZ"]}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let diff = try await client.reconcileThs(filename: "自选股.txt", data: "600000\n000001\n".data(using: .utf8)!)
        XCTAssertEqual(diff.onlyInThs, ["600000.SH"])
        XCTAssertEqual(diff.onlyInNeckline, ["600519.SH"])
        XCTAssertEqual(diff.both, ["000001.SZ"])
    }

    func testDecodeExportThs() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"text": "600001.SH\\n000001.SZ\\n", "count": 2}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.exportThs()
        XCTAssertEqual(r.count, 2)
        XCTAssertTrue(r.text.contains("600001.SH"))
    }

    // MARK: - 4D 周复盘工作台(样例对照 tests/test_api_review.py::test_upload_and_get_roundtrip)

    private static let sampleReviewResultJSON = """
    {
      "week": "2026-W29", "weekStart": "20260713", "weekEnd": "20260719",
      "roundTrips": [
        {"tsCode": "600519.SH", "name": "贵州茅台", "buyDate": "20260714", "buyPrice": 1500.0,
         "qty": 100, "buyAmount": 150000.0, "fees": 30.0, "sellDate": "20260716",
         "sellPrice": 1424.7, "closed": true, "netPnl": -7560.0, "pnlPct": -0.0502}
      ],
      "closedRoundTrips": [
        {"tsCode": "600519.SH", "name": "贵州茅台", "buyDate": "20260714", "buyPrice": 1500.0,
         "qty": 100, "buyAmount": 150000.0, "fees": 30.0, "sellDate": "20260716",
         "sellPrice": 1424.7, "closed": true, "netPnl": -7560.0, "pnlPct": -0.0502}
      ],
      "planChecks": [
        {"tsCode": "600519.SH", "name": "贵州茅台", "tradeDate": "20260714", "price": 1500.0,
         "qty": 100, "amount": 150000.0, "planStatus": "计划外(未经系统候选/海选池放行的自主买入)",
         "ledgerStatus": "台账缺失(未在系统持仓台账登记,止损提醒未覆盖此仓位)"}
      ],
      "disciplineViolations": ["600519.SH(贵州茅台)于 2026-07-14 买入金额 ¥150,000,超过单笔仓位上限 ¥20,000(§2.1 第3条)。"],
      "stopDiscipline": [
        {"roundTrip": {"tsCode": "600519.SH", "name": "贵州茅台", "buyDate": "20260714", "buyPrice": 1500.0,
                       "qty": 100, "buyAmount": 150000.0, "fees": 30.0, "sellDate": "20260716",
                       "sellPrice": 1424.7, "closed": true, "netPnl": -7560.0, "pnlPct": -0.0502},
         "classification": "kept_stop", "note": "卖出价相对买入价 -5.0%,落在止损容差带内,止损纪律执行到位。"}
      ],
      "stats": {"closedCount": 1, "openCount": 0, "winRate": 0.0, "profitFactor": null, "profitLossRatio": null,
                "totalFees": 30.0, "grossPnl": -7530.0, "realizedPnl": -7560.0, "realizedLoss": -7560.0},
      "forcedReview": false, "forcedReviewReason": ""
    }
    """

    func testDecodeReviewUpload() async throws {
        let json = jsonData("""
        {"ok": true, "weeks": [{"week": "2026-W29", "result": \(Self.sampleReviewResultJSON), "material": "本周平仓1回合…"}],
         "parseWarnings": [], "dataWarnings": [], "sheetFormats": {"t.xlsx · 对账单": "format1"}}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let fileData = "dummy xlsx bytes".data(using: .utf8)!
        let resp = try await client.uploadReview(files: [(filename: "交割单.xlsx", data: fileData)])
        XCTAssertTrue(resp.ok)
        XCTAssertEqual(resp.weeks.count, 1)
        let week = resp.weeks[0]
        XCTAssertEqual(week.week, "2026-W29")
        XCTAssertEqual(week.result.roundTrips.count, 1)
        XCTAssertEqual(week.result.roundTrips[0].tsCode, "600519.SH")
        XCTAssertEqual(week.result.roundTrips[0].netPnl, -7560.0)
        XCTAssertEqual(week.result.planChecks[0].isOffPlan, true)
        XCTAssertEqual(week.result.planChecks[0].isLedgerMissing, true)
        XCTAssertEqual(week.result.stopDiscipline[0].kind, .keptStop)
        XCTAssertNil(week.result.stats?.profitFactor)   // JSON null → nil,不是 0
        XCTAssertFalse(week.result.forcedReview)
        XCTAssertFalse(week.material.isEmpty)

        // 请求本身应是 multipart/form-data,且带上了文件名(不是裸 JSON POST)。
        let req = MockURLProtocol.lastRequest
        let contentType = req?.value(forHTTPHeaderField: "Content-Type") ?? ""
        XCTAssertTrue(contentType.contains("multipart/form-data"))
        let body = try XCTUnwrap(req?.httpBodyOrStream())
        let bodyText = String(data: body, encoding: .utf8) ?? ""
        XCTAssertTrue(bodyText.contains("交割单.xlsx"))
        XCTAssertTrue(bodyText.contains("name=\"files\""))
    }

    func testDecodeReviewGetFound() async throws {
        let json = jsonData("""
        {"ok": true, "found": true, "week": "2026-W29", "generatedAt": "2026-07-20T12:00:00+00:00",
         "result": \(Self.sampleReviewResultJSON), "material": "本周平仓1回合…"}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let resp = try await client.fetchReview(week: "2026-W29")
        XCTAssertTrue(resp.found)
        XCTAssertEqual(resp.result?.week, "2026-W29")
        let reqURL = MockURLProtocol.lastRequest?.url?.absoluteString ?? ""
        XCTAssertTrue(reqURL.contains("?week=2026-W29"))
        XCTAssertFalse(reqURL.contains("%3F"))
    }

    /// `result` 为 JSON null(非空字典)时应解成 `nil`,不是一个字段全空的"假"结果。
    func testDecodeReviewGetNotFound() async throws {
        let json = jsonData("""
        {"ok": true, "found": false, "week": "2099-W01", "generatedAt": "", "result": null, "material": ""}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let resp = try await client.fetchReview(week: "2099-W01")
        XCTAssertFalse(resp.found)
        XCTAssertNil(resp.result)
    }

    // MARK: - v1.2-B 预注册决策日志(样例对照 tests/test_api_decisions.py,§五 v1.2-E.1/E.6)

    private static let sampleDecisionJSON = """
    {"id": 1, "code": "600001.SH", "name": "示例甲", "createdAt": "2026-07-25T10:00:00+00:00",
     "whyBuy": "题材热+量能启动,板块龙头效应明显", "whyEntryPrice": "回调至10日均线企稳,缩量企稳信号",
     "targetPrice": 12.0, "exitLow": 9.0, "exitHigh": 9.5,
     "thesisTags": ["THEME", "CAPITAL_FLOW"], "invalidation": "跌破10日均线且缩量转放量下杀",
     "contingencyScenarios": [
       {"scenario": "次日高开超预期", "trigger": "开盘涨幅>3%", "action": "HOLD", "matched": false},
       {"scenario": "次日低开破位", "trigger": "开盘跌幅>2%", "action": "ABANDON", "matched": false}
     ],
     "playbookTag": "SWING_CHASE", "plannedPrice": 10.0, "plannedQty": 1000,
     "status": "pending", "positionId": null, "revisionOf": null}
    """

    /// `createDecision` 请求体逐字段核对 + `createdAt` 绝不由客户端构造(即便
    /// `DecisionCreateRequest` 类型上就没有这个字段,物理杜绝,同后端「三处防线」①的
    /// 客户端镜像)。
    func testCreateDecisionRequestBodyShapeAndCreatedAtNeverSent() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertNil(obj?["createdAt"], "请求体不应含 createdAt 字段——DecisionCreateRequest 类型上就没有")
            XCTAssertEqual(obj?["code"] as? String, "600001.SH")
            XCTAssertEqual(obj?["whyBuy"] as? String, "题材热+量能启动,板块龙头效应明显")
            XCTAssertEqual(obj?["thesisTags"] as? [String], ["THEME", "CAPITAL_FLOW"])
            XCTAssertEqual(obj?["playbookTag"] as? String, "SWING_CHASE")
            let scenarios = obj?["contingencyScenarios"] as? [[String: Any]]
            XCTAssertEqual(scenarios?.count, 2)
            XCTAssertEqual(scenarios?[0]["action"] as? String, "HOLD")
            XCTAssertEqual(scenarios?[0]["matched"] as? Bool, false)
            return (200, jsonData(Self.sampleDecisionJSON))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let log = try await client.createDecision(
            code: "600001.SH", name: "示例甲", whyBuy: "题材热+量能启动,板块龙头效应明显",
            whyEntryPrice: "回调至10日均线企稳,缩量企稳信号", targetPrice: 12.0, exitLow: 9.0, exitHigh: 9.5,
            thesisTags: ["THEME", "CAPITAL_FLOW"], invalidation: "跌破10日均线且缩量转放量下杀",
            contingencyScenarios: [
                ContingencyScenario(scenario: "次日高开超预期", trigger: "开盘涨幅>3%", action: "HOLD", matched: false),
                ContingencyScenario(scenario: "次日低开破位", trigger: "开盘跌幅>2%", action: "ABANDON", matched: false),
            ],
            playbookTag: "SWING_CHASE", plannedPrice: 10.0, plannedQty: 1000
        )
        XCTAssertEqual(log.id, 1)
        XCTAssertEqual(log.status, "pending")
        XCTAssertNil(log.positionId)
        XCTAssertEqual(log.contingencyScenarios.count, 2)
        XCTAssertEqual(log.thesisTagLabels, ["题材主线", "资金流向"])
        XCTAssertEqual(log.playbookTagLabel, "短线追击")
        XCTAssertFalse(log.isBreathingTrial)
    }

    /// `GET /decisions` 列表 + `status`/`code` 过滤 query 拼装(不带 query 时不追加 "?")。
    func testListDecisionsBuildsFilterQuery() async throws {
        MockURLProtocol.handler = { req in
            let url = req.url?.absoluteString ?? ""
            XCTAssertTrue(url.contains("status=filled"), "实际请求 URL: \(url)")
            XCTAssertTrue(url.contains("code=600001.SH"))
            return (200, jsonData("""
            {"items": [\(Self.sampleDecisionJSON)]}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let items = try await client.listDecisions(status: "filled", code: "600001.SH")
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(items[0].code, "600001.SH")
    }

    func testListDecisionsWithoutFiltersOmitsQueryString() async throws {
        MockURLProtocol.handler = { req in
            let url = req.url?.absoluteString ?? ""
            XCTAssertFalse(url.contains("?"), "无过滤条件时不应追加空 '?',实际请求 URL: \(url)")
            return (200, jsonData("""
            {"items": []}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let items = try await client.listDecisions()
        XCTAssertTrue(items.isEmpty)
    }

    /// `link`/`cancel` 404 not_found 映射(与 positions 的 404 not_holding 分开,复用
    /// `APIError.mapReason` 既有 `.notFound` case,不需要新代码——只需核对映射到位)。
    func testLinkAndCancelDecisionNonexistentMapsToNotFound() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["positionId"] as? Int, 7)
            return (404, jsonData("""
            {"detail": {"ok": false, "reason": "not_found"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.linkDecision(id: 999, positionId: 7)
            XCTFail("应抛 notFound")
        } catch APIError.notFound {}

        MockURLProtocol.handler = { _ in
            (404, jsonData("""
            {"detail": {"ok": false, "reason": "not_found"}}
            """))
        }
        do {
            _ = try await client.cancelDecision(id: 999)
            XCTFail("应抛 notFound")
        } catch APIError.notFound {}
    }

    /// `revise` 请求体不含 code/name(修订不能换股票);响应是新 id + `revisionOf` 指链根。
    func testReviseDecisionRequestOmitsCodeNameAndDecodesNewRow() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertNil(obj?["code"], "revise 请求体不应含 code——修订不能换股票")
            XCTAssertNil(obj?["name"])
            XCTAssertEqual(obj?["whyBuy"] as? String, "修订后的理由:资金持续净流入超预期")
            return (200, jsonData("""
            {"id": 2, "code": "600001.SH", "name": "示例甲", "createdAt": "2026-07-25T11:00:00+00:00",
             "whyBuy": "修订后的理由:资金持续净流入超预期", "whyEntryPrice": "回调至10日均线企稳,缩量企稳信号",
             "targetPrice": 13.0, "exitLow": 9.0, "exitHigh": 9.5,
             "thesisTags": ["THEME"], "invalidation": "跌破10日均线", "contingencyScenarios": [],
             "playbookTag": "SWING_CHASE", "plannedPrice": 10.0, "plannedQty": 1000,
             "status": "pending", "positionId": null, "revisionOf": 1}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let revised = try await client.reviseDecision(
            id: 1, whyBuy: "修订后的理由:资金持续净流入超预期", whyEntryPrice: "回调至10日均线企稳,缩量企稳信号",
            targetPrice: 13.0, exitLow: 9.0, exitHigh: 9.5, thesisTags: ["THEME"], invalidation: "跌破10日均线",
            contingencyScenarios: [], playbookTag: "SWING_CHASE", plannedPrice: 10.0, plannedQty: 1000
        )
        XCTAssertEqual(revised.id, 2)
        XCTAssertEqual(revised.revisionOf, 1)
        XCTAssertEqual(revised.status, "pending")
    }

    /// `scenario-outcome` 只翻 `matched`(请求体形状核对;是否真的只改这一列由后端单测锁死,
    /// 客户端只需核对自己发对了请求)。
    func testSetScenarioOutcomeRequestBodyShape() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            let outcomes = obj?["outcomes"] as? [[String: Any]]
            XCTAssertEqual(outcomes?.count, 1)
            XCTAssertEqual(outcomes?[0]["index"] as? Int, 0)
            XCTAssertEqual(outcomes?[0]["matched"] as? Bool, true)
            return (200, jsonData("""
            {"ok": true}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let ok = try await client.setScenarioOutcome(id: 1, outcomes: [(index: 0, matched: true)])
        XCTAssertTrue(ok)
    }

    /// `index` 越界 → 422,走既有 `.validation` 通用映射(不需要新 case)。
    func testSetScenarioOutcomeIndexOutOfRangeMapsToValidation() async throws {
        MockURLProtocol.handler = { _ in
            (422, jsonData("""
            {"detail": {"ok": false, "reason": "scenario_index_out_of_range", "message": "index 99 超出范围"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.setScenarioOutcome(id: 1, outcomes: [(index: 99, matched: true)])
            XCTFail("应抛 validation")
        } catch APIError.validation(let reason) {
            XCTAssertEqual(reason, "scenario_index_out_of_range")
        }
    }

    // MARK: - v1.2-A2 熔断纪律状态(样例对照 tests/test_api_circuit.py,§五 v1.2-E.3)

    func testDecodeCircuitStateUnlocked() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"locked": false, "episode": null}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let state = try await client.getCircuit()
        XCTAssertFalse(state.locked)
        XCTAssertNil(state.episode)
    }

    /// 锁定态含 episode 全部诚实边界字段(basisTradesCount/note 等);
    /// `triggerReasonLabel` 展示层换算 consecutive_stops→连续止损。
    func testDecodeCircuitStateLockedWithEpisode() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"locked": true, "episode": {
              "triggerReason": "consecutive_stops", "triggeredAt": "2026-07-22T15:05:00+00:00",
              "triggerRefDate": "20260722", "basisTradesCount": 3, "basisWindow": "2026-07-20~2026-07-22",
              "note": "基于台账 3 笔已补录成交判定连续止损触发。"
            }}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let state = try await client.getCircuit()
        XCTAssertTrue(state.locked)
        XCTAssertEqual(state.episode?.triggerReasonLabel, "连续止损")
        XCTAssertEqual(state.episode?.basisTradesCount, 3)
        XCTAssertTrue(state.episode?.note.contains("已补录成交") ?? false)
    }

    func testUnlockCircuitDecodesOk() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"ok": true}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let ok = try await client.unlockCircuit()
        XCTAssertTrue(ok)
    }

    // MARK: - v1.2-G 呼吸试验仓台账(样例对照 tests/test_api_breathing.py,§五 v1.2-E.4)

    func testBreathingTradesRoundTripContractShape() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["buyPrice"] as? Double, 10.0)
            XCTAssertEqual(obj?["fees"] as? Double, 20.0)
            XCTAssertEqual(obj?["tDate"] as? String, "20260702")
            return (200, jsonData("""
            {"id": 1, "positionId": 5, "buyPrice": 10.0, "sellPrice": 10.3, "qty": 500, "fees": 20.0,
             "tDate": "20260702", "tPnl": 130.0, "note": "日内回踩低吸"}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let trade = try await client.addBreathingTrade(positionId: 5, buyPrice: 10.0, sellPrice: 10.3, qty: 500,
                                                        fees: 20.0, tDate: "20260702", note: "日内回踩低吸")
        XCTAssertEqual(trade.id, 1)
        XCTAssertEqual(trade.positionId, 5)
        XCTAssertEqual(trade.tPnl, 130.0)
        XCTAssertEqual(trade.note, "日内回踩低吸")
    }

    /// `baseCostAdj`/`edgeToPrice` 均可为 null(无 T 记录 / 无实时价)→ 客户端不崩、
    /// 不拿 0 冒充「无优势」。
    func testDecodeBreathingLedgerWithNullDerivedFields() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"items": [], "baseCostAdj": null, "edgeToPrice": null}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let ledger = try await client.breathingTrades(positionId: 5)
        XCTAssertTrue(ledger.items.isEmpty)
        XCTAssertNil(ledger.baseCostAdj)
        XCTAssertNil(ledger.edgeToPrice)
    }

    func testDecodeBreathingLedgerWithDerivedFields() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"items": [
              {"id": 1, "positionId": 5, "buyPrice": 10.0, "sellPrice": 10.3, "qty": 500, "fees": 20.0,
               "tDate": "20260702", "tPnl": 130.0, "note": ""}
            ], "baseCostAdj": 9.74, "edgeToPrice": 0.0267}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let ledger = try await client.breathingTrades(positionId: 5)
        XCTAssertEqual(ledger.items.count, 1)
        XCTAssertEqual(ledger.baseCostAdj, 9.74)
        XCTAssertEqual(ledger.edgeToPrice, 0.0267)
    }

    /// 误录可删;不存在 → 404 not_found(幂等安全,复用既有 `.notFound` 映射)。
    func testDeleteBreathingTradeNonexistentMapsToNotFound() async throws {
        MockURLProtocol.handler = { _ in
            (404, jsonData("""
            {"detail": {"ok": false, "reason": "not_found"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.deleteBreathingTrade(id: 999)
            XCTFail("应抛 notFound")
        } catch APIError.notFound {}
    }

    /// 底仓不存在 → `GET`/`POST` 均 404 not_found。
    func testBreathingTradesPositionNotFoundMapsToNotFound() async throws {
        MockURLProtocol.handler = { _ in
            (404, jsonData("""
            {"detail": {"ok": false, "reason": "not_found"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.breathingTrades(positionId: 999)
            XCTFail("应抛 notFound")
        } catch APIError.notFound {}
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
