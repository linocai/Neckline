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

    // MARK: - health(v1.5-⑤-E:此前 `version` 被丢弃,现改为设置屏展示,对照
    // `neckline/api/app.py::health()` 字面响应 `{"status": "ok", "version": VERSION}`)

    func testDecodeHealthReturnsOkAndVersion() async throws {
        MockURLProtocol.handler = { _ in (200, jsonData(#"{"status": "ok", "version": "v1.5.0"}"#)) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let health = try await client.health()
        XCTAssertTrue(health.ok)
        XCTAssertEqual(health.version, "v1.5.0")
    }

    /// 非 200 → `(false, nil)`,不因为拿不到 version 就崩或误报 ok。
    func testDecodeHealthNon200MapsToNotOkWithNilVersion() async throws {
        MockURLProtocol.handler = { _ in (503, jsonData(#"{"detail": "unavailable"}"#)) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let health = try await client.health()
        XCTAssertFalse(health.ok)
        XCTAssertNil(health.version)
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
        // v1.3-⑥:本样例 JSON 没有 k4Flags/intelRank 键(早于该字段的形状)——`Candidate`
        // 自定义 `init(from:)` 须容忍缺键,不能整份候选解码失败,前向兼容不特判。
        XCTAssertEqual(c0.k4Flags, [])
        XCTAssertEqual(c0.intelRank, IntelRank())
    }

    /// v1.3-③-C3/⑥:候选新语义字段——`k4Flags`(avoid_flag 打标)+ `intelRank`(情报排序
    /// 理由:来源/资金流强度/题材天数/高弹/行业/五常驻板块诊断漏斗)。样例对照
    /// `test_report_latest_intel_rank_carries_source_industry_permanent_board_status`。
    func testDecodeCandidateK4FlagsAndIntelRankWithPermanentBoardStatus() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260722", "generatedAt": "g", "strategyVersion": "v1.3", "sentiment": null,
          "sectors": [],
          "candidates": [{
            "rank": 1, "code": "600001.SH", "name": "示例甲", "score": 88.0, "board": "MAIN",
            "buyPoint": "b", "stop": "s", "target": "t", "invalidation": "i",
            "formTags": [], "hotSectors": [], "sectorNames": [], "llmJudgment": null,
            "k4Flags": ["B2_double_gold_cross"],
            "intelRank": {
              "sectorFlow": 1234.5, "themePersistDays": 1, "highElasticity": true,
              "source": "quota", "industry": "小金属",
              "permanentBoardStatus": [
                {"board": "稀土永磁", "surviveCount": 9, "industryGatePass": 1, "industryGateBlocked": 8,
                 "hardCutBlocked": 1, "quotaFilled": 0,
                 "note": "稀土永磁:保底 0 只 —— 9 只过卫生线成员中 8 只行业不属本板块主导行业、1 只过闸但命中 K4 安检拦截,宁缺毋滥、非静默空白"}
              ]
            }
          }],
          "degraded": false, "reason": ""
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        let rank = report.candidates[0].intelRank
        XCTAssertEqual(report.candidates[0].k4Flags, ["B2_double_gold_cross"])
        XCTAssertEqual(rank.source, "quota")
        XCTAssertEqual(nkIntelSourceLabel(rank.source), "常驻保底")
        XCTAssertEqual(rank.industry, "小金属")
        XCTAssertEqual(rank.sectorFlow, 1234.5)
        XCTAssertTrue(rank.highElasticity)
        XCTAssertEqual(rank.permanentBoardStatus.count, 1)
        let status0 = rank.permanentBoardStatus[0]
        XCTAssertEqual(status0.board, "稀土永磁")
        XCTAssertEqual(status0.quotaFilled, 0)
        XCTAssertEqual(status0.industryGateBlocked, 8)
        XCTAssertTrue(status0.note.contains("宁缺毋滥"), "0 只时必须带「为什么」,不能静默空白")
    }

    /// v1.4-③ 排序键三级原样透出(需求 8):`industryRank`(①,nil=未参与排名,**不得
    /// 当 0**)/ `industryPersistDays`(②)/ `yellowCardCount`(③)。
    func testDecodeIntelRankSortKeyThreeFieldsAndNilRankIsNotZero() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260728", "generatedAt": "g", "strategyVersion": "v1.3.3", "sentiment": null,
          "sectors": [],
          "candidates": [
            {
              "rank": 1, "code": "600001.SH", "name": "甲", "score": 90.0, "board": "MAIN",
              "buyPoint": "b", "stop": "s", "target": "t", "invalidation": "i",
              "formTags": [], "hotSectors": [], "sectorNames": [], "llmJudgment": null,
              "intelRank": {"industryRank": 2, "industryPersistDays": 1, "yellowCardCount": 3}
            },
            {
              "rank": 2, "code": "600002.SH", "name": "乙", "score": 85.0, "board": "MAIN",
              "buyPoint": "b", "stop": "s", "target": "t", "invalidation": "i",
              "formTags": [], "hotSectors": [], "sectorNames": [], "llmJudgment": null,
              "intelRank": {"industryRank": null}
            }
          ],
          "degraded": false, "reason": ""
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        XCTAssertEqual(report.candidates[0].intelRank.industryRank, 2)
        XCTAssertEqual(report.candidates[0].intelRank.industryPersistDays, 1)
        XCTAssertEqual(report.candidates[0].intelRank.yellowCardCount, 3)
        // 未参与排名(成员<5/无行业)→ nil,展示层不得当 0(0 会误读成"最强")。
        XCTAssertNil(report.candidates[1].intelRank.industryRank)
        XCTAssertEqual(report.candidates[1].intelRank.industryPersistDays, 0, "缺键兜底 0")
        XCTAssertEqual(report.candidates[1].intelRank.yellowCardCount, 0)
    }

    /// v1.4-④-B/⑤-A:候选携带信息卡摘要(`infoCard`)+ 执行提示(`execHints`)。
    func testDecodeCandidateInfoCardSummaryAndExecHints() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260728", "generatedAt": "g", "strategyVersion": "v1.3.3", "sentiment": null,
          "sectors": [],
          "candidates": [{
            "rank": 1, "code": "600001.SH", "name": "甲", "score": 90.0, "board": "MAIN",
            "buyPoint": "b", "stop": "s", "target": "t", "invalidation": "i",
            "formTags": [], "hotSectors": [], "sectorNames": [], "llmJudgment": null,
            "infoCard": {
              "snapshot": {"volRatio5": 1.8, "turnoverRate": 6.2, "industryRank": 3, "industryPersistDays": 1,
                          "aboveMa250": true, "distFromMa250Pct": 0.12, "distFromHigh20dPct": -0.03,
                          "consecLimitUpDays": 0},
              "mildBand": true,
              "news": {"scanned": false, "items": [], "unavailableReason": "候选不在消息面扫描域(仅持仓+自选)"},
              "topList": {"onListToday": false, "lookbackDaysCovered": 3, "lookbackHitDays": 0}
            },
            "execHints": [
              {"code": "C2_mild_red_low_variance", "text": "低方差首选带(H5),但≈0期望、非正alpha,不构成买入理由", "source": "db"}
            ]
          }],
          "degraded": false, "reason": ""
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        let c = report.candidates[0]
        let card = try XCTUnwrap(c.infoCard)
        XCTAssertEqual(card.snapshot.volRatio5, 1.8)
        XCTAssertEqual(card.snapshot.industryRank, 3)
        XCTAssertEqual(card.snapshot.aboveMa250, true)
        XCTAssertTrue(card.mildBand)
        XCTAssertFalse(card.news.scanned)
        XCTAssertEqual(card.news.unavailableReason, "候选不在消息面扫描域(仅持仓+自选)")
        XCTAssertEqual(card.topList.lookbackDaysCovered, 3)
        XCTAssertEqual(c.execHints.count, 1)
        XCTAssertEqual(c.execHints[0].code, "C2_mild_red_low_variance")
        XCTAssertEqual(c.execHints[0].source, "db")
    }

    /// 老报告快照(建于本字段前)缺 `infoCard`/`execHints`/`referencePlan`/`judgeSkipped`
    /// 键 → `nil`/`[]`/`nil`/`false`,不崩(`infoCard=nil`/`referencePlan=nil` 表示"该
    /// 信息暂不可用",不冒充"确认无内容"——v1.5-①-F 契约「`referencePlan=nil` 不冒充
    /// 确认无参考」同一惯例)。
    func testDecodeCandidateOmittingV14InfoCardExecHintsDefaultsGracefully() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260717", "generatedAt": "g", "strategyVersion": "v1", "sentiment": null,
          "sectors": [],
          "candidates": [{
            "rank": 1, "code": "600001.SH", "name": "甲", "score": 90.0, "board": "MAIN",
            "buyPoint": "b", "stop": "s", "target": "t", "invalidation": "i",
            "formTags": [], "hotSectors": [], "sectorNames": [], "llmJudgment": null
          }],
          "degraded": false, "reason": ""
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        XCTAssertNil(report.candidates[0].infoCard)
        XCTAssertEqual(report.candidates[0].execHints, [])
        XCTAssertNil(report.candidates[0].referencePlan)
        XCTAssertFalse(report.candidates[0].judgeSkipped)
    }

    /// v1.5-①-F/②-A(需求 9):参考件三件套 `referencePlan` 三态(ok/vetoed/unavailable)
    /// + `judgeSkipped`(预算耗尽未发起,与 `llmJudgment` 为 nil 但语义不同,不许合并成
    /// 一个"没审")。样例字段对照 `neckline/api/schemas.py::ReferencePlanOut`。**同时是
    /// §五 v1.5-⑤-G「老客户端兼容回归」的机器证据一部分**:老四件套四键在本样例里同
    /// 老服务端一样保持非空 String,用当前(未改严格性的)`Candidate.init(from:)` 解码
    /// 不抛错,证明向后兼容硬约束成立。
    func testDecodeCandidateReferencePlanThreeStatesAndJudgeSkipped() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260730", "generatedAt": "g", "strategyVersion": "v1.3.3", "sentiment": null,
          "sectors": [],
          "candidates": [
            {
              "rank": 1, "code": "600001.SH", "name": "甲", "score": 90.0, "board": "MAIN",
              "buyPoint": "本版已由「参考三件套」取代四件套,请更新 App 查看(参考、非指令)。",
              "stop": "本版已由「参考三件套」取代四件套,请更新 App 查看(参考、非指令)。",
              "target": "本版已由「参考三件套」取代四件套,请更新 App 查看(参考、非指令)。",
              "invalidation": "本版已由「参考三件套」取代四件套,请更新 App 查看(参考、非指令)。",
              "formTags": [], "hotSectors": [], "sectorNames": [],
              "llmJudgment": {"verdict": "通过", "narrative": "催化站得住。", "degraded": false},
              "referencePlan": {
                "status": "ok",
                "buy": {"low": 12.30, "high": 12.98, "stopPrice": 11.68, "why": "站稳10日线,量能温和"},
                "exit": {"low": 15.10, "high": 15.80, "why": "本轮上涨压力位"},
                "script": "竞价掉进危险区就放弃,温和低开量能正常就观望。",
                "disclaimer": "参考,非指令 —— 买卖与终选在你,系统不代下单;纪律以章程为准。",
                "degraded": false
              }
            },
            {
              "rank": 2, "code": "600002.SH", "name": "乙", "score": 85.0, "board": "MAIN",
              "buyPoint": "b", "stop": "s", "target": "t", "invalidation": "i",
              "formTags": [], "hotSectors": [], "sectorNames": [],
              "llmJudgment": {"verdict": "否决", "narrative": "催化证据不足。", "degraded": false},
              "referencePlan": {"status": "vetoed", "vetoReason": "无法验证催化真实性",
                                "disclaimer": "参考,非指令 —— 买卖与终选在你,系统不代下单;纪律以章程为准。",
                                "degraded": false}
            },
            {
              "rank": 3, "code": "600003.SH", "name": "丙", "score": 80.0, "board": "MAIN",
              "buyPoint": "b", "stop": "s", "target": "t", "invalidation": "i",
              "formTags": [], "hotSectors": [], "sectorNames": [],
              "llmJudgment": {"verdict": "未激活", "narrative": "LLM 未配置。", "degraded": true},
              "referencePlan": {"status": "unavailable", "unavailableReason": "LLM 未激活",
                                "disclaimer": "参考,非指令 —— 买卖与终选在你,系统不代下单;纪律以章程为准。",
                                "degraded": true}
            },
            {
              "rank": 4, "code": "600004.SH", "name": "丁", "score": 75.0, "board": "MAIN",
              "buyPoint": "b", "stop": "s", "target": "t", "invalidation": "i",
              "formTags": [], "hotSectors": [], "sectorNames": [], "llmJudgment": null,
              "judgeSkipped": true
            }
          ],
          "degraded": false, "reason": ""
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        XCTAssertEqual(report.candidates.count, 4)

        let ok = report.candidates[0]
        XCTAssertFalse(ok.judgeSkipped)
        let okPlan = try XCTUnwrap(ok.referencePlan)
        XCTAssertTrue(okPlan.isOk)
        XCTAssertEqual(okPlan.buy?.low, 12.30)
        XCTAssertEqual(okPlan.buy?.stopPrice, 11.68)
        XCTAssertEqual(okPlan.exit?.high, 15.80)
        XCTAssertTrue(okPlan.script?.contains("放弃") ?? false)
        XCTAssertFalse(okPlan.disclaimer.isEmpty)
        // 老四件套四键仍是非空 String(向后兼容硬约束,§五 v1.5-⑤-C「本版不改它们的
        // 解码严格性」),新 UI 不展示,但必须继续能被严格解码逻辑读到、不抛错。
        XCTAssertFalse(ok.buyPoint.isEmpty)
        XCTAssertFalse(ok.stop.isEmpty)
        XCTAssertFalse(ok.target.isEmpty)
        XCTAssertFalse(ok.invalidation.isEmpty)

        let vetoed = report.candidates[1]
        let vetoedPlan = try XCTUnwrap(vetoed.referencePlan)
        XCTAssertTrue(vetoedPlan.isVetoed)
        XCTAssertNil(vetoedPlan.buy)
        XCTAssertNil(vetoedPlan.exit)
        XCTAssertEqual(vetoedPlan.vetoReason, "无法验证催化真实性")

        let unavailable = report.candidates[2]
        let unavailablePlan = try XCTUnwrap(unavailable.referencePlan)
        XCTAssertTrue(unavailablePlan.isUnavailable)
        XCTAssertEqual(unavailablePlan.unavailableReason, "LLM 未激活")

        // judgeSkipped=true(预算耗尽未发起)与 llmJudgment=nil 并存,referencePlan 键
        // 干脆没给(该票压根没被送去审)——两者语义不同,不许合并成一个"没审"。
        let skipped = report.candidates[3]
        XCTAssertTrue(skipped.judgeSkipped)
        XCTAssertNil(skipped.llmJudgment)
        XCTAssertNil(skipped.referencePlan)

        // v1.5.1 增量两键在本样例里都没给(等价老快照)→ nil,不是 0(0 会被
        // `ratioPct` 渲染成 "0%",等于对用户宣称"章程止损 0%",极危险)。
        XCTAssertNil(okPlan.buy?.stopPct)
        XCTAssertNil(okPlan.exit?.takeProfitRetrace)
    }

    /// v1.5.1(两线 review 共同项):章程口径指纹 `buy.stopPct` / `exit.takeProfitRetrace`
    /// 解码 + 标签动态生成。样例对照 `tests/test_api_report_board.py::
    /// test_report_candidate_reference_plan_carries_charter_fingerprints`。
    func testDecodeReferencePlanCharterFingerprintsAndDynamicLabels() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260730", "generatedAt": "g", "strategyVersion": "v1.5.1", "sentiment": null,
          "sectors": [],
          "candidates": [
            {
              "rank": 1, "code": "600001.SH", "name": "甲", "score": 90.0, "board": "MAIN",
              "buyPoint": "x", "stop": "x", "target": "x", "invalidation": "x",
              "formTags": [], "hotSectors": [], "sectorNames": [],
              "referencePlan": {
                "status": "ok",
                "buy": {"low": 12.30, "high": 12.98, "stopPrice": 11.32, "stopPct": 0.08, "why": ""},
                "exit": {"low": 15.10, "high": 15.80, "takeProfitRetrace": 0.12, "why": ""},
                "script": "s", "disclaimer": "参考,非指令", "degraded": false
              }
            }
          ],
          "degraded": false, "reason": ""
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        let plan = try XCTUnwrap(report.candidates[0].referencePlan)
        XCTAssertEqual(plan.buy?.stopPct, 0.08)
        XCTAssertEqual(plan.exit?.takeProfitRetrace, 0.12)
        // 标签跟着指纹走,不再硬编 −5% / 8%(章程一改,数字与标签同步)。
        XCTAssertEqual(ReferencePlanSection.stopLabel(try XCTUnwrap(plan.buy)), "章程 −8%")
        XCTAssertEqual(ReferencePlanSection.retraceLabel(try XCTUnwrap(plan.exit)),
                       "纪律仍以回落止盈 12% 兜底")
    }

    /// 指纹缺失(老快照 / 章程未配置)→ 退化成**不带数字**的说法,绝不硬编 5%/8%。
    func testReferencePlanCharterLabelsDegradeWithoutNumber() {
        XCTAssertEqual(ReferencePlanSection.stopLabel(ReferencePlanBuy(low: 1, high: 2)), "章程止损")
        XCTAssertEqual(ReferencePlanSection.retraceLabel(ReferencePlanExit(low: 1, high: 2)),
                       "纪律仍以章程的回落止盈兜底")
    }

    /// 比例→百分数格式化:整百分点不留 ".00",非整百分点不四舍五入成整数骗人。
    func testRatioPctFormatting() {
        XCTAssertEqual(NKFmt.ratioPct(0.05), "5%")
        XCTAssertEqual(NKFmt.ratioPct(0.08), "8%")
        XCTAssertEqual(NKFmt.ratioPct(0.055), "5.5%")
        XCTAssertEqual(NKFmt.ratioPct(0.1), "10%")
    }

    /// v1.5.1(契约线 review 🔵-2):`plan != nil` 但 status 未知时**不许整节静默消失**
    /// ——展示态判定必须落到 `.unknown(原始status)`,由 UI 给一条诚实兜底文案。
    func testReferencePlanUnknownStatusIsNotSilentlyDropped() {
        XCTAssertEqual(ReferencePlanSection.displayState(nil), .absent)
        XCTAssertEqual(ReferencePlanSection.displayState(ReferencePlan(status: "ok")), .ok)
        XCTAssertEqual(ReferencePlanSection.displayState(ReferencePlan(status: "vetoed")), .vetoed)
        XCTAssertEqual(ReferencePlanSection.displayState(ReferencePlan(status: "unavailable")), .unavailable)
        XCTAssertEqual(ReferencePlanSection.displayState(ReferencePlan(status: "future_state")),
                       .unknown("future_state"))
        XCTAssertEqual(ReferencePlanSection.displayState(ReferencePlan(status: "")), .unknown(""))
    }

    // MARK: - v1.3-③-C1/C2/C4「情报」板块(样例对照 test_report_latest_carries_intel_and_sector_moneyflow /
    // test_report_latest_carries_news_alerts_and_scan_status)

    func testDecodeReportIntelSectionAndSectorMoneyflow() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260722", "generatedAt": "g", "strategyVersion": "v1.3", "sentiment": null,
          "sectors": [], "candidates": [], "degraded": false, "reason": "",
          "intel": {
            "tradeDate": "2026-07-22", "evidenceNote": "题材/成分类字段依赖概念板块成分,标参考",
            "gainers": [{"code": "600001.SH", "name": "示例甲", "pctChg": 9.98, "close": 12.34}],
            "losers": [], "limitUpLadder": [{"consecDays": 3, "count": 2}],
            "limitDown": [], "limitDownTotalCount": 5,
            "marketVolume": {"shAmountYi": 5000.0, "szAmountYi": 4000.0, "totalAmountYi": 9000.0,
                             "ma5AmountYi": 8500.0, "sampleDays": 5},
            "topThemes": [{"code": "883300.TI", "name": "AI", "boardAge": 3, "ret20d": 0.12,
                          "persistenceLabel": "持续2-3日", "evidenceStrength": "constituent",
                          "leaders": [{"code": "600001.SH", "name": "示例甲", "pctChg": 9.98, "isLimitUp": true}]}],
            "themePersistenceDistribution": {"新起1日": 3, "持续2-3日": 2},
            "mvPreference": [{"label": "50-100亿", "count": 4, "pctOfTotal": 0.4}],
            "limitRegimePreference": [{"label": "10cm", "count": 8, "pctOfTotal": 0.8}],
            "excludedBoardsNote": "已剔除融资融券等28个资格/宽基标签板块", "warnings": []
          },
          "sectorMoneyflow": {
            "tradeDate": "2026-07-22", "available": true, "unavailableReason": "",
            "topInflow": [{"code": "AAA.TI", "name": "汽车芯片", "netInflowWan": 60.2, "memberCount": 12,
                          "rank": 1, "evidenceStrength": "constituent"}],
            "topOutflow": [], "excludedBoardsNote": "", "evidenceNote": "拥挤情报,非选股信号"
          }
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        let intel = try XCTUnwrap(report.intel)
        XCTAssertTrue(intel.hasContent)
        XCTAssertEqual(intel.gainers.first?.code, "600001.SH")
        XCTAssertEqual(intel.limitUpLadder.first?.consecDays, 3)
        XCTAssertEqual(intel.marketVolume?.totalAmountYi, 9000.0)
        XCTAssertEqual(intel.marketVolume?.sampleDays, 5)
        XCTAssertEqual(intel.topThemes.first?.leaders.first?.name, "示例甲")
        XCTAssertEqual(intel.topThemes.first?.evidenceStrength, "constituent")
        XCTAssertEqual(intel.themePersistenceDistribution["新起1日"], 3)
        XCTAssertEqual(intel.mvPreference.first?.label, "50-100亿")

        let mf = try XCTUnwrap(report.sectorMoneyflow)
        XCTAssertTrue(mf.available)
        XCTAssertEqual(mf.topInflow.first?.name, "汽车芯片")
        XCTAssertEqual(mf.topInflow.first?.netInflowWan, 60.2)
    }

    /// `intel`/`sectorMoneyflow` 服务端恒是对象(旧报告/降级态是空对象 `{}`,不是缺键或
    /// null)——空对象缺我方强类型要求的字段,解码阶段 `try?` 归一成 `nil`,不崩、不假装
    /// 有数据(§硬要求「没有 vs 没看」)。
    func testDecodeReportIntelAndSectorMoneyflowAreNilWhenEmptyObjects() async throws {
        let json = jsonData("""
        {"tradeDate": "20260717", "generatedAt": "g", "strategyVersion": "v1", "sentiment": null,
         "sectors": [], "candidates": [], "degraded": false, "reason": "", "intel": {}, "sectorMoneyflow": {}}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        XCTAssertNil(report.intel)
        XCTAssertNil(report.sectorMoneyflow)
    }

    /// 消息面(§硬要求「没扫到 vs 扫了没有必须能区分」)——必须先读 `newsAlertsScan`
    /// 再展示 `newsAlerts`,`codesSkipped`(预算耗尽跳过)与 `codesFailed`(调用失败)
    /// 语义分开,两者都要透出。
    func testDecodeReportNewsAlertsAndScanStatus() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260722", "generatedAt": "g", "strategyVersion": "v1.3", "sentiment": null,
          "sectors": [], "candidates": [], "degraded": false, "reason": "",
          "newsAlerts": [
            {"code": "600001.SH", "name": "示例甲", "category": "REDUCTION", "summary": "张三减持 5万股",
             "source": "tushare_holdertrade"}
          ],
          "newsAlertsScan": [
            {"source": "tushare_holdertrade", "scanned": true, "reason": "", "codesTotal": 0,
             "codesFailed": 0, "codesSkipped": 0},
            {"source": "llm", "scanned": true, "reason": "墙钟预算耗尽,部分标的未及扫描",
             "codesTotal": 5, "codesFailed": 1, "codesSkipped": 2}
          ]
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        XCTAssertEqual(report.newsAlerts.count, 1)
        XCTAssertEqual(report.newsAlerts[0].categoryLabel, "减持")
        let scan = Dictionary(uniqueKeysWithValues: report.newsAlertsScan.map { ($0.source, $0) })
        XCTAssertEqual(scan["tushare_holdertrade"]?.scanned, true)
        XCTAssertEqual(scan["llm"]?.codesFailed, 1)
        XCTAssertEqual(scan["llm"]?.codesSkipped, 2, "预算耗尽跳过与调用失败必须分开计数")
    }

    /// v1.4-⑥-B:自选隔日轮扫披露(`rotationGroup`/`codesRotationDeferred`)+ v1.3.4
    /// 命中诚实标注(`codesNoSearch`)——**四个计数各不相同,分开展示**,样例对照
    /// `test_board_labels_precall_and_d5exit_events` 邻近的 `test_api_report_board.py`
    /// rotation 用例。
    func testDecodeNewsAlertScanStatusRotationAndNoSearch() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260728", "generatedAt": "g", "strategyVersion": "v1.3.3", "sentiment": null,
          "sectors": [], "candidates": [], "degraded": false, "reason": "",
          "newsAlertsScan": [
            {"source": "llm", "scanned": true, "reason": "", "codesTotal": 4, "codesFailed": 0,
             "codesSkipped": 0, "codesNoSearch": 1, "rotationGroup": "A", "codesRotationDeferred": 8}
          ]
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        let scan = try XCTUnwrap(report.newsAlertsScan.first)
        XCTAssertEqual(scan.codesNoSearch, 1)
        XCTAssertEqual(scan.rotationGroup, "A")
        XCTAssertEqual(scan.codesRotationDeferred, 8)
    }

    /// v1.4-①-C(§七 P0-3):板块数据新鲜度。`stale=true` 时顶部告警须能读出
    /// `sectorDataDate`/`sectorLagDays`。
    func testDecodeReportDataFreshnessStale() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260728", "generatedAt": "g", "strategyVersion": "v1.3.3", "sentiment": null,
          "sectors": [], "candidates": [], "degraded": false, "reason": "",
          "dataFreshness": {"sectorDataDate": "20260722", "sectorLagDays": 4, "stale": true}
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        let freshness = try XCTUnwrap(report.dataFreshness)
        XCTAssertEqual(freshness.sectorDataDate, "20260722")
        XCTAssertEqual(freshness.sectorLagDays, 4)
        XCTAssertTrue(freshness.stale)
    }

    /// v1.4-⑩-F(§七 P0-23):`dataFreshness` 新增行业强度三键。**两件独立故障并列**——
    /// 本例板块新鲜(`stale=false`)而行业强度未就绪(`industryStrengthStale=true`),
    /// 横幅照样要出(`needsBanner`),证明两者没被合并成一个 bool。
    func testDecodeReportDataFreshnessIndustryStrengthKeys() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260729", "generatedAt": "g", "strategyVersion": "v1.3.3", "sentiment": null,
          "sectors": [], "candidates": [], "degraded": false, "reason": "",
          "dataFreshness": {"sectorDataDate": "20260729", "sectorLagDays": 0, "stale": false,
                            "industryStrengthDate": "20260728", "industryStrengthLagDays": 1,
                            "industryStrengthStale": true}
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        let f = try XCTUnwrap(report.dataFreshness)
        XCTAssertFalse(f.stale, "板块新鲜度语义一个字没改,仍只表板块")
        XCTAssertEqual(f.industryStrengthDate, "20260728")
        XCTAssertEqual(f.industryStrengthLagDays, 1)
        XCTAssertEqual(f.industryStrengthStale, true)
        XCTAssertTrue(f.needsBanner, "板块新鲜但行业强度未就绪 → 横幅仍须出现")
    }

    /// 老报告快照只有板块三键(建于 v1.4-⑩ 之前)→ 行业强度三键 `nil` 兜底不崩,
    /// 且 `needsBanner` 不因"缺键"误报(缺键 = 该版本还没有这个概念,不是"未就绪")。
    func testDecodeReportDataFreshnessIndustryKeysAbsentAreNilNotFalse() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260717", "generatedAt": "g", "strategyVersion": "v1.3.3", "sentiment": null,
          "sectors": [], "candidates": [], "degraded": false, "reason": "",
          "dataFreshness": {"sectorDataDate": "20260717", "sectorLagDays": 0, "stale": false}
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        let f = try XCTUnwrap(report.dataFreshness)
        XCTAssertNil(f.industryStrengthDate)
        XCTAssertNil(f.industryStrengthLagDays)
        XCTAssertNil(f.industryStrengthStale)
        XCTAssertFalse(f.needsBanner)
    }

    /// v1.4-⑩-E:信息卡快照 `industryPersistDays` 的 **`null` ≠ 0**。`null` = 行业强度
    /// 表当日无数据(「没看」),UI 显示「数据未就绪」;`0` = 评了、不是强度日(「看了,
    /// 没有」)。两者都必须解得出来、且能区分。
    func testDecodeInfoCardSnapshotIndustryPersistDaysNullVsZero() async throws {
        func snapshot(_ persist: String) -> Data {
            jsonData("""
            {
              "tradeDate": "20260728", "generatedAt": "g", "strategyVersion": "v1", "sentiment": null,
              "sectors": [], "degraded": false, "reason": "",
              "candidates": [{
                "rank": 1, "code": "600001.SH", "name": "甲", "score": 90.0, "board": "MAIN",
                "buyPoint": "b", "stop": "s", "target": "t", "invalidation": "i",
                "formTags": [], "hotSectors": [], "sectorNames": [], "llmJudgment": null,
                "infoCard": {
                  "snapshot": {"volRatio5": 1.1, "turnoverRate": 5.0, "industryRank": null,
                               "industryPersistDays": \(persist), "aboveMa250": null,
                               "distFromMa250Pct": null, "distFromHigh20dPct": null, "consecLimitUpDays": 0},
                  "mildBand": false,
                  "news": {"scanned": false, "items": [], "unavailableReason": "r"},
                  "topList": {"onListToday": false, "lookbackDaysCovered": 0, "lookbackHitDays": 0}
                }
              }]
            }
            """)
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())

        MockURLProtocol.handler = { _ in (200, snapshot("null")) }
        let missing = try await client.fetchReportLatest()
        XCTAssertNil(try XCTUnwrap(missing.candidates[0].infoCard).snapshot.industryPersistDays)

        MockURLProtocol.handler = { _ in (200, snapshot("0")) }
        let zero = try await client.fetchReportLatest()
        XCTAssertEqual(try XCTUnwrap(zero.candidates[0].infoCard).snapshot.industryPersistDays, 0)
    }

    /// 老报告(建于本字段前)/ 空对象 `{}` → `nil`(同 `intel`/`sectorMoneyflow` 惯例),
    /// 不当"新鲜"展示。
    func testDecodeReportDataFreshnessAbsentOrEmptyIsNil() async throws {
        let json = jsonData("""
        {"tradeDate": "20260717", "generatedAt": "g", "strategyVersion": "v1", "sentiment": null,
         "sectors": [], "candidates": [], "degraded": false, "reason": "", "dataFreshness": {}}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        XCTAssertNil(report.dataFreshness)
    }

    /// 旧报告(建于 newsAlerts/newsAlertsScan 字段前)缺这两键 → 空数组,不崩、不误判
    /// 「确认无消息」(§硬要求,newsAlertsScan 为空时客户端不得渲染"以上为命中,已扫描过")。
    func testDecodeReportNewsAlertsDefaultToEmptyWhenAbsent() async throws {
        let json = jsonData("""
        {"tradeDate": "20260717", "generatedAt": "g", "strategyVersion": "v1", "sentiment": null,
         "sectors": [], "candidates": [], "degraded": false, "reason": ""}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let report = try await client.fetchReportLatest()
        XCTAssertEqual(report.newsAlerts, [])
        XCTAssertEqual(report.newsAlertsScan, [])
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

    // MARK: - v1.4-④-B 信息卡(样例对照 tests/test_api_info_card.py::
    // test_info_card_happy_path_shapes_full_payload)

    func testFetchInfoCardDecodesFullPayload() async throws {
        let json = jsonData("""
        {
          "code": "600001.SH", "name": "示例甲", "tradeDate": "20260728",
          "klineAvailable": true,
          "kline": [
            {"tradeDate": "20260727", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.25, "vol": 100000.0,
             "ma20": 10.1, "ma250": null},
            {"tradeDate": "20260728", "open": 10.3, "high": 10.6, "low": 10.1, "close": 10.55, "vol": 120000.0,
             "ma20": 10.2, "ma250": 9.5}
          ],
          "rsAvailable": true, "rsLine": [{"tradeDate": "20260728", "value": 102.5}],
          "rsBenchmark": "000001.SH",
          "industryDivergenceAvailable": false, "industryDivergenceLine": [],
          "industry": "小众行业",
          "industryDivergenceNote": "行业线=行业成员中位数合成,非申万官方指数",
          "industryDivergenceUnavailableReason": "行业样本不足(成员数不足,分歧线缺省)",
          "snapshot": {"volRatio5": 1.1, "turnoverRate": 5.0, "industryRank": null, "industryPersistDays": 0,
                      "aboveMa250": null, "distFromMa250Pct": null, "distFromHigh20dPct": -0.02,
                      "consecLimitUpDays": 0},
          "k4Flags": [
            {"code": "A1_turnover_gt_10", "label": "换手率>10%(过热放量,接盘区)", "level": "strong",
             "section": "hard_cut", "evidenceStrength": "price_volume", "evidence": "换手>10%次日跌停3.37%"}
          ],
          "mildBand": true,
          "news": {"scanned": false, "items": [], "unavailableReason": "候选不在消息面扫描域(仅持仓+自选)"},
          "topList": {"onListToday": false, "lookbackDaysCovered": 3, "lookbackHitDays": 0},
          "market": {"indexCode": "000001.SH", "indexLine": [{"tradeDate": "20260728", "value": 101.2}],
                    "limitUpCount": 42, "limitDownCount": 3, "aboveMa20": true}
        }
        """)
        MockURLProtocol.handler = { req in
            XCTAssertTrue((req.url?.absoluteString ?? "").contains("/report/20260728/info-card/600001.SH"))
            return (200, json)
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let card = try await client.fetchInfoCard(date: "20260728", code: "600001.SH")
        XCTAssertEqual(card.code, "600001.SH")
        XCTAssertTrue(card.klineAvailable)
        XCTAssertEqual(card.kline.count, 2)
        XCTAssertEqual(card.kline[0].close, 10.25)
        XCTAssertEqual(card.kline[1].ma250, 9.5)
        XCTAssertTrue(card.rsAvailable)
        XCTAssertEqual(card.rsLine.first?.value, 102.5)
        XCTAssertFalse(card.industryDivergenceAvailable)
        XCTAssertTrue((card.industryDivergenceUnavailableReason ?? "").contains("样本不足"))
        XCTAssertEqual(card.snapshot.volRatio5, 1.1)
        XCTAssertNil(card.snapshot.industryRank, "未参与排名不得当 0")
        XCTAssertEqual(card.k4Flags.first?.sectionLabel, "红牌")
        XCTAssertTrue(card.mildBand)
        XCTAssertFalse(card.news.scanned)
        XCTAssertEqual(card.topList.lookbackDaysCovered, 3)
        XCTAssertEqual(card.market.limitUpCount, 42)
        XCTAssertEqual(card.market.aboveMa20, true)
    }

    /// 404 `report_not_found`(日期非法/当天未生成过报告)→ `.reportNotFound`(逐个建
    /// case,不吃 fallback)。
    func testFetchInfoCardReportNotFoundMapsToDedicatedError() async throws {
        MockURLProtocol.handler = { _ in
            (404, jsonData("""
            {"detail": {"ok": false, "reason": "report_not_found"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.fetchInfoCard(date: "20200101", code: "600001.SH")
            XCTFail("应抛 reportNotFound")
        } catch APIError.reportNotFound {}
    }

    /// 404 `code_not_in_report`(该日报告存在但这只票不在候选榜里)→ `.codeNotInReport`。
    func testFetchInfoCardCodeNotInReportMapsToDedicatedError() async throws {
        MockURLProtocol.handler = { _ in
            (404, jsonData("""
            {"detail": {"ok": false, "reason": "code_not_in_report"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.fetchInfoCard(date: "20260728", code: "999999.SH")
            XCTFail("应抛 codeNotInReport")
        } catch APIError.codeNotInReport {}
    }

    // MARK: - v1.4-⑦-A 挂单未成交追踪(样例对照 tests/test_api_decision_track.py)

    func testDecisionTrackRoundTripWithRows() async throws {
        let json = jsonData("""
        {"status": "expired", "planPrice": 10.0,
         "rows": [
           {"tradeDate": "20260722", "dOffset": 1, "close": 10.2, "retFromPlan": 0.02},
           {"tradeDate": "20260723", "dOffset": 2, "close": 9.9, "retFromPlan": -0.01}
         ]}
        """)
        MockURLProtocol.handler = { req in
            XCTAssertTrue((req.url?.absoluteString ?? "").contains("/decisions/42/track"))
            return (200, json)
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let track = try await client.decisionTrack(id: 42)
        XCTAssertEqual(track.status, "expired")
        XCTAssertEqual(track.planPrice, 10.0)
        XCTAssertEqual(track.rows.count, 2)
        XCTAssertEqual(track.rows[0].dOffset, 1)
        XCTAssertEqual(track.rows[1].retFromPlan, -0.01)
    }

    /// 决策存在但还没攒到任何追踪快照 → 合法 200 空态 `rows=[]`,不是错误(两种「空」
    /// 分开——本测试不该抛任何 error)。
    func testDecisionTrackEmptyRowsIsLegalNotError() async throws {
        let json = jsonData("""
        {"status": "pending", "planPrice": 10.0, "rows": []}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let track = try await client.decisionTrack(id: 7)
        XCTAssertEqual(track.rows, [])
        XCTAssertEqual(track.status, "pending")
    }

    /// `decisionId` 不存在 → 404 `not_found`(**复用既有 `.notFound` case,未新增**,
    /// 字符串与 decisions link/cancel/revise 端点相同)。
    func testDecisionTrackNonexistentMapsToExistingNotFoundCase() async throws {
        MockURLProtocol.handler = { _ in
            (404, jsonData("""
            {"detail": {"ok": false, "reason": "not_found"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.decisionTrack(id: 999999)
            XCTFail("应抛 notFound")
        } catch APIError.notFound {}
    }

    // MARK: - v1.4-⑦-B 问询历史(样例对照 tests/test_api_inquiry_log.py)

    func testFetchInquiriesDecodesListAndBuildsQuery() async throws {
        let json = jsonData("""
        {"items": [
          {"id": 3, "createdAt": "2026-07-28T10:00:00+00:00", "code": "600001.SH", "name": "示例甲",
           "question": "300759 康龙化成怎么样", "answer": "综合评分…", "evidence": ["硬线核对通过"],
           "verdict": "已分析", "positionId": null, "decisionId": null}
        ]}
        """)
        MockURLProtocol.handler = { req in
            let url = req.url?.absoluteString ?? ""
            XCTAssertTrue(url.contains("limit=10"))
            XCTAssertTrue(url.contains("offset=0"))
            XCTAssertTrue(url.contains("tsCode=600001.SH"))
            return (200, json)
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let items = try await client.fetchInquiries(limit: 10, offset: 0, tsCode: "600001.SH")
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(items[0].id, 3)
        XCTAssertEqual(items[0].question, "300759 康龙化成怎么样")
        XCTAssertEqual(items[0].verdictBadge, .analyzed)
    }

    /// 不传 `tsCode` → 请求 URL 不含该 query 段(默认全量列表)。
    func testFetchInquiriesWithoutTsCodeOmitsQueryParam() async throws {
        MockURLProtocol.handler = { req in
            let url = req.url?.absoluteString ?? ""
            XCTAssertFalse(url.contains("tsCode="))
            return (200, jsonData("""
            {"items": []}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let items = try await client.fetchInquiries()
        XCTAssertTrue(items.isEmpty)
    }

    func testFetchInquiryDetailDecodes() async throws {
        let json = jsonData("""
        {"id": 3, "createdAt": "2026-07-28T10:00:00+00:00", "code": "600001.SH", "name": "示例甲",
         "question": "怎么样", "answer": "综合评分…", "evidence": [],
         "verdict": "已分析·有风险提示", "positionId": 5, "decisionId": null}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let item = try await client.fetchInquiryDetail(id: 3)
        XCTAssertEqual(item.answer, "综合评分…")
        XCTAssertEqual(item.verdictBadge, .analyzedWarn)
        XCTAssertEqual(item.positionId, 5)
        XCTAssertNil(item.decisionId)
    }

    /// 不存在 → 404 `not_found`(复用既有 case,未新增)。
    func testFetchInquiryDetailNonexistentMapsToExistingNotFoundCase() async throws {
        MockURLProtocol.handler = { _ in
            (404, jsonData("""
            {"detail": {"ok": false, "reason": "not_found"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.fetchInquiryDetail(id: 999999)
            XCTFail("应抛 notFound")
        } catch APIError.notFound {}
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

    // MARK: - v1.3-①/②/⑥ 两档时间退出 + 费用回显 + K4 持仓牌 + 情景树待对照
    // (样例对照 tests/test_api_positions.py 与 §五 v1.3-①/② 完工纪要契约增量)

    /// 浮盈豁免态(profit_exempt)——**不是**离场提示,`maxHoldDaysEffective` 是 15
    /// (硬上限档),`isExitDay` 必须 false、`todayActionTone` 不得是 `.bad`(§五 v1.3-⑥-A
    /// 硬要求)。
    func testDecodePositionProfitExemptTwoTierFields() async throws {
        let json = jsonData("""
        {"holdings": [
          {"id": 4, "code": "600003.SH", "name": "丙", "buyPrice": 10.0, "qty": 1000,
           "entryReason": "", "buyDate": "20260701", "price": 12.0, "status": "holding",
           "stopLine": 9.5, "stopOrderChecked": false,
           "dCount": 8, "maxHoldDays": 5, "distToStopPct": 0.2083, "retraceState": null,
           "todayAction": "浮盈豁免时间退出,交回落止盈+止损管到硬上限(D8/D15)",
           "maxHoldDaysEffective": 15, "timeExitState": "profit_exempt",
           "buyFees": 12.5, "sellFees": null,
           "k4Advisory": [
             {"code": "B2_double_gold_cross", "label": "双金叉态", "level": "normal",
              "evidence": "macd_cross", "evidenceStrength": "price_volume"}
           ],
           "scenarioReviewPending": true}
        ]}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let p = try await client.fetchPositions()[0]
        XCTAssertEqual(p.maxHoldDaysEffective, 15)
        XCTAssertEqual(p.timeExitKind, .profitExempt)
        XCTAssertFalse(p.isExitDay, "浮盈豁免不是离场日")
        XCTAssertEqual(p.todayActionTone, .good)
        XCTAssertEqual(p.buyFees, 12.5)
        XCTAssertNil(p.sellFees, "未平仓单卖出费恒 nil")
        XCTAssertEqual(p.k4Advisory.count, 1)
        XCTAssertFalse(p.k4Advisory[0].isTopBillboard, "normal 级别不置顶")
        XCTAssertTrue(p.scenarioReviewPending)
    }

    /// K4 强警示置顶判据(level=strong ∧ evidenceStrength=price_volume)解码正确。
    func testDecodePositionK4StrongPriceVolumeAdvisoryIsTopBillboard() async throws {
        let json = jsonData("""
        {"holdings": [
          {"id": 5, "code": "600004.SH", "name": "丁", "buyPrice": 15.0, "qty": 500,
           "entryReason": "", "buyDate": "20260715", "price": 14.0, "status": "holding",
           "stopLine": 14.25, "stopOrderChecked": false,
           "dCount": 3, "maxHoldDays": 5, "distToStopPct": null, "retraceState": null, "todayAction": "",
           "k4Advisory": [
             {"code": "A3_belowyear_limitup", "label": "年线下涨停,疑似派发", "level": "strong",
              "evidence": "close>=limit_price and close<ma250", "evidenceStrength": "price_volume"},
             {"code": "A2_theme_persist_ge_4", "label": "题材持续≥4天", "level": "strong",
              "evidence": "board_age>=4", "evidenceStrength": "constituent"}
           ]}
        ]}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let p = try await client.fetchPositions()[0]
        XCTAssertEqual(p.k4Advisory.count, 2)
        XCTAssertTrue(p.k4Advisory[0].isTopBillboard, "价量结构强证据应置顶")
        XCTAssertFalse(p.k4Advisory[1].isTopBillboard, "成分类弱证据即便 strong 也不置顶,只标「参考」")
    }

    /// 旧持仓快照(建于 v1.3-①/② 之前)缺这些键 → 前向兼容默认值,不崩:
    /// `maxHoldDaysEffective` 兜底到 `maxHoldDays`(不是硬编 5)、`k4Advisory` 空数组、
    /// `scenarioReviewPending` false。
    func testDecodePositionOmittingV13FieldsDefaultsGracefully() async throws {
        let json = jsonData("""
        {"holdings": [
          {"id": 6, "code": "600005.SH", "name": "戊", "buyPrice": 8.0, "qty": 100,
           "entryReason": "", "buyDate": "20260716", "price": 8.2, "status": "holding",
           "stopLine": 7.6, "stopOrderChecked": false,
           "dCount": 2, "maxHoldDays": 5, "distToStopPct": 0.0732, "retraceState": null,
           "todayAction": "持有中(D2/D5)"}
        ]}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let p = try await client.fetchPositions()[0]
        XCTAssertEqual(p.maxHoldDaysEffective, 5, "缺键兜底到 maxHoldDays,不是硬编 5")
        XCTAssertEqual(p.k4Advisory, [])
        XCTAssertFalse(p.scenarioReviewPending)
        XCTAssertNil(p.buyFees)
        XCTAssertNil(p.sellFees)
        // v1.4-①-B/⑥-C:同样缺键的新字段一并兜底,不崩。
        XCTAssertNil(p.priceStale)
        XCTAssertNil(p.k4DataUnavailable, "老快照未记录 = 不知道,不冒充 false")
        XCTAssertNil(p.timeExitLockedDay)
        XCTAssertEqual(p.timeExitLockedLateDays, 0)
        XCTAssertEqual(p.timeExitKind, .holding)
    }

    /// v1.4-①-B(§七 P0-2):停牌/无数据持仓票——`priceStale` 三字段齐备 +
    /// `timeExitState=suspended_hold` 正确映射到 `.suspendedHold`(**不是**误落
    /// `.holding` 兜底那条分支,虽然行为结果凑巧一致但语义不同)+ 展示层派生
    /// (`isExitDay=false`、`todayActionTone=.warn`)。样例对照
    /// `test_price_stale_reports_days_last_close_and_reason` /
    /// `test_suspended_hold_state_and_action_text`。
    func testDecodePositionPriceStaleAndSuspendedHold() async throws {
        let json = jsonData("""
        {"holdings": [
          {"id": 7, "code": "002036.SZ", "name": "联创电子", "buyPrice": 7.184, "qty": 3000,
           "entryReason": "", "buyDate": "20260716", "price": 7.05, "status": "holding",
           "stopLine": 6.82, "stopOrderChecked": false,
           "dCount": 6, "maxHoldDays": 5, "distToStopPct": 0.0326, "retraceState": null,
           "todayAction": "停牌/无当日行情,时间退出判向挂起(D6 照常累计,复牌当日收盘再定格)",
           "maxHoldDaysEffective": 5, "timeExitState": "suspended_hold",
           "priceStale": {"staleDays": 3, "lastCloseDate": "20260722", "reason": "suspended"},
           "k4DataUnavailable": true}
        ]}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let p = try await client.fetchPositions()[0]
        XCTAssertEqual(p.priceStale?.staleDays, 3)
        XCTAssertEqual(p.priceStale?.lastCloseDate, "20260722")
        XCTAssertEqual(p.priceStale?.reason, "suspended")
        XCTAssertEqual(p.priceStale?.reasonLabel, "停牌")
        XCTAssertEqual(p.timeExitKind, .suspendedHold)
        XCTAssertFalse(p.isExitDay, "判向挂起不是离场日")
        XCTAssertEqual(p.todayActionTone, .warn)
        XCTAssertEqual(p.k4DataUnavailable, true)
    }

    /// v1.4-⑥-C(§七 P1-6):定格日 ≠ D5 显式标注——只在 `timeExitLockedLateDays>0`
    /// 才有意义展示(展示层判据在 View,这里只测解码正确)。样例对照
    /// `test_locked_day_and_late_days_when_pipeline_lagged`。
    func testDecodePositionTimeExitLockedDayLateDays() async throws {
        let json = jsonData("""
        {"holdings": [
          {"id": 8, "code": "600006.SH", "name": "己", "buyPrice": 10.0, "qty": 100,
           "entryReason": "", "buyDate": "20260701", "price": 10.5, "status": "holding",
           "stopLine": 9.5, "stopOrderChecked": false,
           "dCount": 7, "maxHoldDays": 5, "distToStopPct": 0.0952, "retraceState": null,
           "todayAction": "持有中(D7/D5)",
           "timeExitLockedDay": 7, "timeExitLockedLateDays": 2}
        ]}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let p = try await client.fetchPositions()[0]
        XCTAssertEqual(p.timeExitLockedDay, 7)
        XCTAssertEqual(p.timeExitLockedLateDays, 2)
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

    /// v1.3-①/⑥-B:`buyFees` 编码进请求体(camelCase,与既有 snake_case 字段并存——
    /// 契约本身如此,同 `closeReason` 惯例)。UI 层强制必填,`AppModel.submitOpenPosition`
    /// 校验通过后才会带上真实值调用本方法。
    func testOpenPositionRequestBodyIncludesBuyFeesWhenProvided() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["buyFees"] as? Double, 12.5)
            return (200, jsonData("""
            {"ok": true, "position_id": 8, "stop_line": 1425.0}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        _ = try await client.openPosition(code: "600519.SH", name: "贵州茅台", buyPrice: 1500.0,
                                          qty: 100, entryReason: "回调低吸", buyFees: 12.5)
    }

    /// 不传 `buyFees` → 请求体不含该键(方法参数默认 nil,`Encodable` 对 Optional 属性用
    /// `encodeIfPresent`;服务端本就宽松,不阻断既有「不关心费用」的调用点)。
    func testOpenPositionRequestBodyOmitsBuyFeesWhenNotProvided() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertNil(obj?["buyFees"])
            return (200, jsonData("""
            {"ok": true, "position_id": 9, "stop_line": 1425.0}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        _ = try await client.openPosition(code: "600519.SH", name: "贵州茅台", buyPrice: 1500.0,
                                          qty: 100, entryReason: "回调低吸")
    }

    // MARK: v1.4-①-A 补录真实买入日(§七 P0-1)

    /// 不传 `buyDate` → 请求体**不含该键**(`encodeIfPresent`),服务端取今天 —— 老客户端
    /// 行为逐字节不变。这是 ①-A 的向后兼容红线。
    func testOpenPositionOmitsBuyDateWhenNotProvided() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertNil(obj?["buyDate"])
            return (200, jsonData("""
            {"ok": true, "position_id": 10, "stop_line": 1425.0}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        _ = try await client.openPosition(code: "600519.SH", name: "贵州茅台", buyPrice: 1500.0,
                                          qty: 100, entryReason: "回调低吸")
    }

    /// 传了就编码进请求体('YYYYMMDD')。
    func testOpenPositionEncodesBuyDateWhenProvided() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["buyDate"] as? String, "20260722")
            return (200, jsonData("""
            {"ok": true, "position_id": 11, "stop_line": 1425.0}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        _ = try await client.openPosition(code: "600519.SH", name: "贵州茅台", buyPrice: 1500.0,
                                          qty: 100, entryReason: "回调低吸", buyDate: "20260722")
    }

    /// 400 + `reason=not_trading_day` → `.notTradingDay`(**逐个建 case,不吃 fallback**;
    /// 守 CLAUDE.md「404/reason 映射」坑:watchlist `not_found` 曾被 fallback 误显成「持仓已清」)。
    func testOpenPositionNonTradingDayMapsToDedicatedError() async throws {
        MockURLProtocol.handler = { _ in
            (400, jsonData("""
            {"detail": {"ok": false, "reason": "not_trading_day"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.openPosition(code: "600519.SH", name: nil, buyPrice: 1.0, qty: 100,
                                              entryReason: "", buyDate: "20260726")
            XCTFail("应抛 notTradingDay")
        } catch APIError.notTradingDay {
            XCTAssertEqual(APIError.notTradingDay.errorDescription, "买入日不是交易日,请选择实际成交的交易日")
        }
    }

    /// 400 + `reason=future_buy_date` → `.futureBuyDate`,文案与上一条**不同**
    /// (「那天不开市」vs「你填到未来去了」,合并会让用户改错地方)。
    func testOpenPositionFutureBuyDateMapsToDedicatedError() async throws {
        MockURLProtocol.handler = { _ in
            (400, jsonData("""
            {"detail": {"ok": false, "reason": "future_buy_date"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.openPosition(code: "600519.SH", name: nil, buyPrice: 1.0, qty: 100,
                                              entryReason: "", buyDate: "20991231")
            XCTFail("应抛 futureBuyDate")
        } catch APIError.futureBuyDate {
            XCTAssertEqual(APIError.futureBuyDate.errorDescription, "买入日不能晚于今天")
        }
    }

    /// 未知 400 reason → **不冒充**买入日错误,退回既有 `.server(400, …)` 语义。
    func testUnknown400FallsBackToServerError() async throws {
        MockURLProtocol.handler = { _ in
            (400, jsonData("""
            {"detail": {"ok": false, "reason": "some_future_reason"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.openPosition(code: "600519.SH", name: nil, buyPrice: 1.0, qty: 100,
                                              entryReason: "")
            XCTFail("应抛 server(400,…)")
        } catch APIError.server(let code, let msg) {
            XCTAssertEqual(code, 400)
            XCTAssertEqual(msg, "some_future_reason")
        }
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

    /// v1.3-①/⑥-B:`sellFees` 可选,成交后回填——编码进请求体(与 `closeReason` 并存
    /// 不冲突),供周复盘对账用真数、不用估数。
    func testClosePositionRequestBodyIncludesSellFeesWhenProvided() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["sellFees"] as? Double, 8.2)
            XCTAssertEqual(obj?["closeReason"] as? String, "TAKE_PROFIT")
            return (200, jsonData("""
            {"ok": true}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        _ = try await client.closePosition(id: 1, sellPrice: 9.5, closeReason: "TAKE_PROFIT", sellFees: 8.2)
    }

    /// 不传 `sellFees` → 请求体不含该键(可选回填,不阻断基础清仓闭环)。
    func testClosePositionRequestBodyOmitsSellFeesWhenNotProvided() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertNil(obj?["sellFees"])
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

    // MARK: - 4A.5 问询台(样例对照 test_inquiry_endpoint;§2.5 描述性标注,非裁决)

    func testDecodeInquiryAnalyzedWarn() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"ok": true, "code": "600001.SH", "reply": "结合搜索,题材催化尚在,未见明显利空。",
             "verdict": "已分析·有风险提示", "evidence": ["主板,非ST", "板块年龄3天"], "degraded": false}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.sendInquiry(code: "600001.SH", messages: [ChatMessage(role: .user, text: "看看这票")])
        XCTAssertEqual(r.verdict, .analyzedWarn)
        XCTAssertEqual(r.verdict.label, "已分析·有风险提示")
        XCTAssertEqual(r.verdict.tone, .warn)   // P3-14:警示色,不再是中性色
        XCTAssertFalse(r.evidence.isEmpty)
        XCTAssertFalse(r.degraded)
    }

    func testDecodeInquiryAnalyzed() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"ok": true, "code": "600002.SH", "reply": "未命中任何硬线,形态上暂未走出买点。",
             "verdict": "已分析", "evidence": ["未命中系统硬线"], "degraded": false}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.sendInquiry(code: "600002.SH", messages: [])
        XCTAssertEqual(r.verdict, .analyzed)
        XCTAssertEqual(r.verdict.tone, .neutral)
    }

    /// v1.4 review 契约线 🟡-3:`POST /inquiry` 的 `inquiryId`(v1.4-⑦-B 契约清单登记在案)
    /// 从前在 Swift 解码段被丢掉 —— 服务端 → JSON 三段都在,第四段漏了,问询历史关联位没料。
    /// 三态各锁一条:有值 / 显式 null(服务端落库失败的旁路态)/ 老服务端压根没这个键。
    func testDecodeInquiryIdPresentNullAndAbsent() async throws {
        func send(_ json: String) async throws -> InquiryResult {
            MockURLProtocol.handler = { _ in (200, jsonData(json)) }
            let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t",
                                   session: mockSession())
            return try await client.sendInquiry(code: "600002.SH", messages: [])
        }
        let base = """
        {"ok": true, "code": "600002.SH", "reply": "r", "verdict": "已分析",
         "evidence": [], "degraded": false
        """
        let withId = try await send(base + ", \"inquiryId\": 42}")
        let nullId = try await send(base + ", \"inquiryId\": null}")   // 落库失败 = 旁路
        let noKey = try await send(base + "}")                         // 老服务端无此键
        XCTAssertEqual(withId.inquiryId, 42)
        XCTAssertNil(nullId.inquiryId)
        XCTAssertNil(noKey.inquiryId)
        // 落库失败不影响回答本身:reply/verdict 照常(与 degraded 是两件独立的事)
        XCTAssertEqual(nullId.verdict, .analyzed)
        XCTAssertFalse(nullId.degraded)
    }

    // MARK: - 4A.5 设置(样例对照 test_settings_default / test_put_llm_key_not_leaked)

    func testDecodeSettings() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"llmProvider": "glm", "llmKeySet": true,
             "push": {"report": true, "retreatBrake": false, "precall": true, "d5exit": false,
                      "circuit": true, "holdingAlert": false},
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
        // v1.3-②/⑥:第六字段(K4 持仓派发警报)。
        XCTAssertFalse(s.push.holdingAlert)
        XCTAssertEqual(s.reviewColMap, ["手续费": "费用合计"])
    }

    /// v1.3-②/⑥:PUT settings/push 请求体六字段一并发送(对照 test_put_push_toggles)。
    func testPutSettingsPushSendsSixFieldsIncludingHoldingAlert() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["report"] as? Bool, false)
            XCTAssertEqual(obj?["retreatBrake"] as? Bool, true)
            XCTAssertEqual(obj?["precall"] as? Bool, false)
            XCTAssertEqual(obj?["d5exit"] as? Bool, true)
            XCTAssertEqual(obj?["circuit"] as? Bool, false)
            XCTAssertEqual(obj?["holdingAlert"] as? Bool, true)
            return (200, jsonData("""
            {"ok": true}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let ok = try await client.putSettingsPush(report: false, retreatBrake: true, precall: false,
                                                   d5exit: true, circuit: false, holdingAlert: true)
        XCTAssertTrue(ok)
    }

    /// `holdingAlert` 省略时的默认参数值仍会被编码进请求体(六字段服务端均必填,§五
    /// v1.3-② 拍板),不是"省略即不发"——同 §五 v1.3-⑥ 后端补齐②对"UI 强制必填"的
    /// 处理精神:方法签名给默认值只是省得既有调用点逐一改,实际请求体不受影响。
    func testPutSettingsPushOmittedHoldingAlertStillEncodesDefaultTrue() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["holdingAlert"] as? Bool, true)
            return (200, jsonData("""
            {"ok": true}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        _ = try await client.putSettingsPush(report: true, retreatBrake: true, precall: true,
                                             d5exit: true, circuit: true)
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

    // MARK: - v1.3-③-C3/⑥ 五常驻板块可配(样例对照 tests/test_api_settings.py 新增用例)

    func testFetchIntelWatchBoardsDecodesArray() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"boards": ["芯片概念", "创新药", "储能", "机器人概念", "稀土永磁"]}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.fetchIntelWatchBoards()
        XCTAssertEqual(r.boards, ["芯片概念", "创新药", "储能", "机器人概念", "稀土永磁"])
    }

    func testPutIntelWatchBoardsRequestBodyAndResponse() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["boards"] as? [String], ["储能", "芯片概念"])
            return (200, jsonData("""
            {"boards": ["储能", "芯片概念"]}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.putIntelWatchBoards(["储能", "芯片概念"])
        XCTAssertEqual(r.boards, ["储能", "芯片概念"])
    }

    /// 精确匹配失败 → 422,`reason` 前缀 "board_not_found",冒号后是具体没匹配到的名字
    /// (`reasonString` 对 detail.unresolved 数组的展开,见传输层注释;对照
    /// test_put_intel_boards_rejects_fuzzy_unmatched_name_422)。
    func testPutIntelWatchBoardsRejectedNamesSurfaceInValidationReason() async throws {
        MockURLProtocol.handler = { _ in
            (422, jsonData("""
            {"detail": {"ok": false, "reason": "board_not_found", "unresolved": ["芯片"],
                        "message": "以下板块名未能在 ths_index.name 精确匹配到(禁模糊匹配,请核对全名):['芯片']"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.putIntelWatchBoards(["芯片"])
            XCTFail("应抛 validation(board_not_found)")
        } catch APIError.validation(let reason) {
            XCTAssertEqual(reason, "board_not_found:芯片")
        }
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
        // v1.5-④-A1/⑤-D:`sampleWatchlistCheckJSON` 样例故意不含 `dispatchAlerts` 键
        // (模拟老报告快照建于本字段前)——`WatchlistCheckItem` 手写 `init(from:)` 须
        // 兜成空数组,不崩。
        XCTAssertEqual(wl.items[0].check?.dispatchAlerts, [])
        XCTAssertNil(wl.items[1].check, "从未体检过 → nil,不是报错")
        XCTAssertTrue(wl.items[1].pinned)
    }

    /// v1.5-④-A1/⑤-D:自选票 K4 派发警示(`dispatchAlerts`)有数据时的解码。样例字段
    /// 对照 `neckline/api/schemas.py::DispatchAlertOut`(契约故意省略 `level`,两码均恒
    /// 强价量证据)。
    func testDecodeWatchlistCheckDispatchAlerts() async throws {
        let json = jsonData("""
        {"items": [
          {"code": "600001.SH", "name": "示例甲", "addedAt": "2026-07-20T10:00:00+00:00",
           "source": "manual", "note": "", "pinned": false, "updatedAt": "2026-07-20T10:00:00+00:00",
           "check": {"code": "600001.SH", "name": "示例甲", "pinned": false, "source": "manual",
             "hasData": true, "close": 12.34, "board": "MAIN", "score": 77.7, "patternTags": [],
             "hotSectors": [], "sectorNames": [], "greenLight": true, "disqualifiers": [],
             "buyPointTriggered": false, "buyPoint": "", "stop": "", "target": "", "invalidation": "",
             "statusChanged": false, "llmJudgment": null,
             "dispatchAlerts": [
               {"code": "A3_belowyear_limitup", "label": "年线下涨停(疑似派发)",
                "evidence": "12-25 涨停但收于年线下方", "evidenceStrength": "price_volume"},
               {"code": "A3b_belowyear_bigvol", "label": "年线下放量大阳(疑似派发)",
                "evidence": "换手 12.3%,量比 3.2", "evidenceStrength": "price_volume"}
             ]}}
        ], "maxSize": 30}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let wl = try await client.fetchWatchlist()
        let alerts = try XCTUnwrap(wl.items.first?.check?.dispatchAlerts)
        XCTAssertEqual(alerts.count, 2)
        XCTAssertEqual(alerts[0].code, "A3_belowyear_limitup")
        XCTAssertEqual(alerts[0].evidenceStrength, "price_volume")
        XCTAssertTrue(alerts[1].evidence.contains("换手"))
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
      "strategyVersion": "v1.3.3",
      "charterSegments": [
        {"version": "v1.3.3", "start": null, "tradeCount": 2},
        {"version": "v1.3.4", "start": "2026-07-16 14:36", "tradeCount": 1}
      ],
      "charterSwitches": [
        {"at": "2026-07-16 14:36", "fromVersion": "v1.3.3", "toVersion": "v1.3.4", "note": "本周 2026-07-16 14:36 发生章程切换"}
      ],
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
        // v1.4-⑥-A(§七 P1-4):章程切换分段——strategyVersion 只是周初标签,该周若发生
        // 切换须能读出分段计数 + 切换事件详情。
        XCTAssertEqual(week.result.strategyVersion, "v1.3.3")
        XCTAssertEqual(week.result.charterSegments.count, 2)
        XCTAssertEqual(week.result.charterSegments[0].start, nil, "第一段 start=nil 表示自周初起")
        XCTAssertEqual(week.result.charterSegments[0].tradeCount, 2)
        XCTAssertEqual(week.result.charterSegments[1].version, "v1.3.4")
        XCTAssertEqual(week.result.charterSwitches.count, 1)
        XCTAssertEqual(week.result.charterSwitches[0].fromVersion, "v1.3.3")
        XCTAssertEqual(week.result.charterSwitches[0].toVersion, "v1.3.4")
        XCTAssertEqual(week.result.charterSwitches[0].at, "2026-07-16 14:36")

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

    /// v1.4-⑥-A 向后兼容:`reviews.result_json` 是**写入当时冻住的快照**(不像
    /// intelRank/infoCard 那样服务端每次响应都重构),真实历史周报(建于本字段前)落库
    /// 时压根没有 `strategyVersion`/`charterSegments`/`charterSwitches` 三键——必须
    /// 缺键不崩,归空(§3.8「没有 vs 没看」)。
    func testDecodeReviewResultMissingCharterFieldsDefaultsGracefully() async throws {
        let json = jsonData("""
        {"ok": true, "found": true, "week": "2026-W20", "generatedAt": "2026-05-20T12:00:00+00:00",
         "result": {
           "week": "2026-W20", "weekStart": "20260518", "weekEnd": "20260524",
           "roundTrips": [], "closedRoundTrips": [], "planChecks": [], "disciplineViolations": [],
           "stopDiscipline": [], "stats": null, "forcedReview": false, "forcedReviewReason": ""
         },
         "material": ""}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let resp = try await client.fetchReview(week: "2026-W20")
        XCTAssertTrue(resp.found)
        XCTAssertEqual(resp.result?.strategyVersion, "")
        XCTAssertEqual(resp.result?.charterSegments, [])
        XCTAssertEqual(resp.result?.charterSwitches, [])
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
            // v1.4-⑤-B ⑨:最高追价上限必须在请求体里。
            XCTAssertEqual(obj?["maxChasePct"] as? Double, 3.0)
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
            playbookTag: "SWING_CHASE", plannedPrice: 10.0, plannedQty: 1000,
            maxChasePct: 3.0
        )
        XCTAssertEqual(log.id, 1)
        XCTAssertEqual(log.status, "pending")
        XCTAssertNil(log.positionId)
        XCTAssertEqual(log.contingencyScenarios.count, 2)
        XCTAssertEqual(log.thesisTagLabels, ["题材主线", "资金流向"])
        XCTAssertEqual(log.playbookTagLabel, "短线追击")
        XCTAssertFalse(log.isBreathingTrial)
    }

    /// v1.4-⑤-B:`maxChasePct` **永远出现在请求体里**——nil 时编码成 JSON `null`,
    /// 不是被省略(与 `buyDate`/`closeReason` 等既有"nil→省略键"字段刻意不同,守
    /// 服务端 `model_fields_set` 的"必须显式传"纪律)。
    func testCreateDecisionRequestAlwaysEncodesMaxChasePctKeyEvenWhenNil() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertNotNil(obj, "请求体应能正常解析")
            XCTAssertTrue(obj?.keys.contains("maxChasePct") ?? false, "maxChasePct 键必须永远出现,即便值是 null")
            XCTAssertTrue(obj?["maxChasePct"] is NSNull, "显式不设上限应编码成 JSON null,不是被省略")
            return (200, jsonData(Self.sampleDecisionJSON))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        _ = try await client.createDecision(
            code: "600001.SH", name: nil, whyBuy: "b", whyEntryPrice: "e", targetPrice: nil,
            exitLow: nil, exitHigh: nil, thesisTags: [], invalidation: "i", contingencyScenarios: [],
            playbookTag: "SWING_CHASE", plannedPrice: nil, plannedQty: nil,
            maxChasePct: nil
        )
    }

    /// 响应体里的 `maxChasePct` 正确解码回 `DecisionLog`(数值 / 负值均可)。
    func testDecodeDecisionLogMaxChasePct() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"id": 5, "code": "600001.SH", "name": "示例甲", "createdAt": "2026-07-28T10:00:00+00:00",
             "whyBuy": "b", "whyEntryPrice": "e", "targetPrice": null, "exitLow": null, "exitHigh": null,
             "thesisTags": [], "invalidation": "i", "contingencyScenarios": [], "playbookTag": "SWING_CHASE",
             "plannedPrice": null, "plannedQty": null, "maxChasePct": -1.5,
             "status": "pending", "positionId": null, "revisionOf": null}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let log = try await client.createDecision(
            code: "600001.SH", name: nil, whyBuy: "b", whyEntryPrice: "e", targetPrice: nil,
            exitLow: nil, exitHigh: nil, thesisTags: [], invalidation: "i", contingencyScenarios: [],
            playbookTag: "SWING_CHASE", plannedPrice: nil, plannedQty: nil, maxChasePct: -1.5
        )
        XCTAssertEqual(log.maxChasePct, -1.5, "允许负值(只在低开时买)")
    }

    /// 400 `reason=max_chase_required` → `.maxChaseRequired`(逐个建 case,不吃 fallback)。
    func testCreateDecisionMissingMaxChaseMapsToDedicatedError() async throws {
        MockURLProtocol.handler = { _ in
            (400, jsonData("""
            {"detail": {"ok": false, "reason": "max_chase_required"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.createDecision(
                code: "600001.SH", name: nil, whyBuy: "b", whyEntryPrice: "e", targetPrice: nil,
                exitLow: nil, exitHigh: nil, thesisTags: [], invalidation: "i", contingencyScenarios: [],
                playbookTag: "SWING_CHASE", plannedPrice: nil, plannedQty: nil, maxChasePct: nil
            )
            XCTFail("应抛 maxChaseRequired")
        } catch APIError.maxChaseRequired {
            XCTAssertEqual(APIError.maxChaseRequired.errorDescription, "请设置「最高追价上限」(填数字,或勾选不设上限)")
        }
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
            XCTAssertEqual(obj?["maxChasePct"] as? Double, -0.5, "revise 同 create 纪律:maxChasePct 必须显式传")
            return (200, jsonData("""
            {"id": 2, "code": "600001.SH", "name": "示例甲", "createdAt": "2026-07-25T11:00:00+00:00",
             "whyBuy": "修订后的理由:资金持续净流入超预期", "whyEntryPrice": "回调至10日均线企稳,缩量企稳信号",
             "targetPrice": 13.0, "exitLow": 9.0, "exitHigh": 9.5,
             "thesisTags": ["THEME"], "invalidation": "跌破10日均线", "contingencyScenarios": [],
             "playbookTag": "SWING_CHASE", "plannedPrice": 10.0, "plannedQty": 1000, "maxChasePct": -0.5,
             "status": "pending", "positionId": null, "revisionOf": 1}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let revised = try await client.reviseDecision(
            id: 1, whyBuy: "修订后的理由:资金持续净流入超预期", whyEntryPrice: "回调至10日均线企稳,缩量企稳信号",
            targetPrice: 13.0, exitLow: 9.0, exitHigh: 9.5, thesisTags: ["THEME"], invalidation: "跌破10日均线",
            contingencyScenarios: [], playbookTag: "SWING_CHASE", plannedPrice: 10.0, plannedQty: 1000,
            maxChasePct: -0.5
        )
        XCTAssertEqual(revised.id, 2)
        XCTAssertEqual(revised.maxChasePct, -0.5)
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

// v1.4-⑧ 起 `AppModelTests.swift` 的端到端请求体断言也要用这个 helper(同一坑),
// 故从 `private`(仅本文件可见)放宽到默认 `internal`(NecklineTests 模块内可见,
// 两个文件同编译单元共享一份实现,不重复定义第二份)。
extension URLRequest {
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
