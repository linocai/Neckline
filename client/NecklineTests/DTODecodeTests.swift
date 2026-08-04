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
          "strategyVersion": "v1.3.3",
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
          "basketDaily": {
            "tradeDate": "20260717",
            "baskets": [
              {"basketId": 11, "basketKey": "ai_infra", "name": "AI 基建", "tradeDate": "20260717",
               "tier": 1, "memberCodes": ["600001.SH", "600002.SH"],
               "card": {"specVersion": "v2", "version": 1, "basketKey": "ai_infra",
                        "name": "AI 基建", "driver": "算力订单落地", "driverKind": "industry",
                        "evidenceStatus": "ok", "whyNow": "订单在昨日公告",
                        "members": [
                          {"tsCode": "600001.SH", "name": "甲", "roleLlm": "龙头", "roleMech": "跟随",
                           "roleConflict": true, "isPrimary": true,
                           "entryZone": {"low": 9.5, "high": 10.2, "why": "回踩 MA10"},
                           "entryZoneClamp": "ok",
                           "maxChase": null, "maxChaseClamp": "rejected_out_of_limit",
                           "maxChaseUnavailableReason": "超出次日涨跌停参考价",
                           "exitReference": {"low": 12.0, "high": 13.0}, "exitReferenceClamp": "ok",
                           "tags": [{"code": "pullback_leader", "label": "龙回头位", "tone": "neutral",
                                     "text": "该票处在龙回头位(参考、非指令)", "source": "H9"}],
                           "tagsAbsent": ["warn_streak_top"]}
                        ],
                        "roleConflicts": ["600001.SH"],
                        "tier": 1, "rankInTier": 1, "rankMech": 2, "mechScore": 8.4,
                        "tierBreakdown": {"driver_freshness": 0.9, "leader_clarity": 0.8},
                        "tierReason": "驱动新鲜 + 龙头清晰",
                        "scripts": {"strong": "强开跟随", "flat": "平开观察", "weak": "弱开放弃"},
                        "verificationSpec": {"min_up_ratio": 0.5}, "verificationText": "过半成员翻红",
                        "invalidationSpec": {"max_down_ratio": 0.5}, "invalidationText": "过半成员跌破",
                        "risks": ["订单证伪"], "disclaimer": "以上均为参考,不构成任何操作建议。",
                        "fingerprint": {"stopPct": 0.05, "charterVersion": "v1.3.3",
                                        "packVersion": "K7-pack-v1", "verificationRulesetVersion": "v2"},
                        "disciplineLabels": ["止损 5%"], "narrative": "这一篮的共同驱动很清楚。",
                        "llmStage": "full", "degraded": false, "notes": []},
               "cardVersion": 1,
               "tierHistory": {"basketId": 11, "tradeDate": "20260717", "tier": 1, "mechScore": 8.4,
                               "mechBreakdown": {}, "rankInTier": 1, "rankMech": 2,
                               "llmRankDelta": 1, "llmReason": "龙头更清晰", "packVersion": "K7-pack-v1"}},
              {"basketId": 12, "basketKey": "no_card", "name": "卡未就绪篮", "tradeDate": "20260717",
               "tier": 3, "memberCodes": ["600003.SH"],
               "card": null, "cardVersion": null, "cardUnavailableReason": "card_not_ready"}
            ],
            "basketsAvailable": true,
            "droppedBaskets": [
              {"name": "溢出篮", "mechScore": 7.9, "reason": "capacity_overflow"},
              {"name": "低质篮", "mechScore": 2.1, "reason": "below_quality_line"}
            ],
            "droppedBasketsAvailable": true,
            "reviews": [
              {"basketId": 5, "basketKey": "yday", "name": "昨日篮", "tier": 1, "d0": "20260716",
               "reviewDate": "20260717", "depth": "full", "mech": {"up_ratio": 0.6},
               "llmText": "昨日这一篮走出来了。", "degraded": false}
            ],
            "reviewsAvailable": true,
            "reviewD0": "20260716",
            "packVersion": "K7-pack-v1",
            "notes": []
          },
          "degraded": false,
          "reason": ""
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())

        let report = try await client.fetchReportLatest()
        XCTAssertEqual(report.tradeDate, "20260717")
        XCTAssertEqual(report.strategyVersion, "v1.3.3")
        XCTAssertFalse(report.degraded)
        XCTAssertEqual(report.sentiment?.positionQuota, "休息")
        XCTAssertEqual(report.sentiment?.limitUpCount, 34)
        XCTAssertEqual(report.sentiment?.prevLimitUpPremiumAvg, -0.015)
        XCTAssertEqual(report.sectors.first?.boardAge, 3)

        // ③ 今日篮子
        let daily = report.basketDaily
        XCTAssertTrue(daily.basketsAvailable)
        XCTAssertEqual(daily.baskets.count, 2)
        XCTAssertEqual(daily.baskets(tier: 1).map(\.basketId), [11])
        XCTAssertTrue(daily.baskets(tier: 2).isEmpty, "空档位如实为空(UI 画「今日 T2 为空」)")

        let card = try XCTUnwrap(daily.baskets[0].card)
        XCTAssertEqual(card.driver, "算力订单落地")
        XCTAssertNil(card.evidenceIncompleteNote, "evidenceStatus=ok 时不标「取证不完整」")
        XCTAssertEqual(card.scripts?.strong, "强开跟随")
        XCTAssertEqual(card.risks, ["订单证伪"])
        XCTAssertEqual(card.fingerprint.charterVersion, "v1.3.3")
        XCTAssertEqual(card.fingerprint.packVersion, "K7-pack-v1")
        // `tierBreakdown` 的键是**五维维度名**,原样透传(⛔ 不 camel 化、不改名)。
        XCTAssertEqual(card.tierBreakdown["driver_freshness"]?.doubleValue, 0.9)
        XCTAssertEqual(card.verificationSpec["min_up_ratio"]?.doubleValue, 0.5)

        // 成员:角色两说并存 + 三个参考件各带 clamp/reason
        let m = try XCTUnwrap(card.members.first)
        XCTAssertTrue(m.roleConflict)
        XCTAssertTrue(m.roleDisplay.contains("龙头"))
        XCTAssertTrue(m.roleDisplay.contains("跟随"))
        XCTAssertEqual(m.entryZone?.low, 9.5)
        XCTAssertEqual(m.entryZone?.why, "回踩 MA10")
        // 夹逼拒收 → **值为 nil 且原因非空**,⛔ 不许兜成 0。
        XCTAssertNil(m.maxChase)
        XCTAssertEqual(m.maxChaseClamp, "rejected_out_of_limit")
        XCTAssertEqual(m.maxChaseUnavailableReason, "超出次日涨跌停参考价")
        XCTAssertEqual(m.exitReference?.rangeText, "¥12.00 ~ ¥13.00")
        XCTAssertEqual(m.tags.first?.code, "pullback_leader")
        XCTAssertTrue(m.tags.first!.text.contains("参考、非指令"), "标注件文案原样透传不改写")
        XCTAssertEqual(m.tagsAbsent, ["warn_streak_top"], "判不了的码与「判过没命中」是两回事")

        // 篮子在、卡没生成 = 合法中间态,⛔ 不是「篮子不存在」
        XCTAssertNil(daily.baskets[1].card)
        XCTAssertEqual(daily.baskets[1].cardUnavailableReason, "card_not_ready")
        XCTAssertEqual(daily.baskets[1].cardUnavailableText, "本篮的卡还没生成")

        // Tier 留痕:机械序与 LLM 微调位移**两个都留着**
        XCTAssertEqual(daily.baskets[0].tierHistory?.rankMech, 2)
        XCTAssertEqual(daily.baskets[0].tierHistory?.llmRankDelta, 1)

        // ③b 两个原因码语义相反,⛔ 不许合并
        XCTAssertTrue(daily.droppedBasketsAvailable)
        XCTAssertEqual(daily.droppedBaskets.count, 2)
        XCTAssertNotEqual(daily.droppedBaskets[0].reasonLabel, daily.droppedBaskets[1].reasonLabel)

        // ④ 昨日复盘
        XCTAssertTrue(daily.reviewsAvailable)
        XCTAssertEqual(daily.reviews.first?.depthLabel, "详复盘")
        XCTAssertEqual(daily.reviews.first?.mech["up_ratio"]?.doubleValue, 0.6)
        XCTAssertEqual(daily.reviewD0, "20260716")
        XCTAssertEqual(daily.packVersion, "K7-pack-v1")
    }

    /// **三段的两种「空」必须讲不同的话**(E3):
    /// 空数组 + `available=true` = 今天真没有;`available=false` = 本次没取到。
    func testBasketDailyDistinguishesEmptyFromUnavailable() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260717", "generatedAt": "g", "strategyVersion": "v1.3.3",
          "sentiment": null, "sectors": [],
          "basketDaily": {
            "tradeDate": "20260717",
            "baskets": [], "basketsAvailable": true,
            "droppedBaskets": [], "droppedBasketsAvailable": true,
            "reviews": [], "reviewsAvailable": false,
            "reviewsUnavailableReason": "本次未跑 ⑨ 复盘段",
            "notes": ["扫描层未就绪,今日无种子"]
          },
          "degraded": false, "reason": ""
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let daily = try await client.fetchReportLatest().basketDaily
        XCTAssertTrue(daily.basketsAvailable, "算过了、今天真没有篮子(合法输出)")
        XCTAssertTrue(daily.baskets.isEmpty)
        XCTAssertTrue(daily.droppedBasketsAvailable, "零溢出 ≠ 没跑")
        XCTAssertFalse(daily.reviewsAvailable, "本次没跑复盘")
        XCTAssertEqual(daily.reviewsUnavailableReason, "本次未跑 ⑨ 复盘段")
        XCTAssertEqual(daily.notes, ["扫描层未就绪,今日无种子"])
    }

    /// 老报告(建于 `basketDaily` 之前)整个键缺席 → 三段全 `available=false` 的诚实空态,
    /// **⛔ 不冒充「那天没有篮子」**,更不能整份报告解不出。
    func testReportWithoutBasketDailyKeyDecodesToHonestPlaceholder() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260701", "generatedAt": "g", "strategyVersion": "v1.3.3",
          "sentiment": null, "sectors": [], "degraded": false, "reason": ""
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let daily = try await client.fetchReportLatest().basketDaily
        XCTAssertFalse(daily.basketsAvailable)
        XCTAssertFalse(daily.droppedBasketsAvailable)
        XCTAssertFalse(daily.reviewsAvailable)
        XCTAssertTrue(daily.baskets.isEmpty)
    }

    /// 🔵 **B-1(2026-08-04 A9-④)`ReportResponse` 六处硬解码拉平**:`sectors` 数组与
    /// `tradeDate`/`generatedAt`/`strategyVersion`/`degraded`/`reason` 五个标量原本是
    /// `try c.decode`(缺键 = **整份报告解不出**、今日计划整页空白)。现在单个字段降级成
    /// 诚实空态,⛔ 一个字段掀不翻整份报告。
    func testReportSurvivesMissingScalarsAndSectorsWithHonestEmptyState() async throws {
        // 极端形状:整份报告只剩 basketDaily 一段(现役契约恒发那六个键,这是防未来)
        let json = jsonData(#"{"basketDaily": {"baskets": [], "basketsAvailable": true}}"#)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let snap = try await client.fetchReportLatest()
        XCTAssertEqual(snap.tradeDate, "")
        XCTAssertEqual(snap.generatedAt, "")
        XCTAssertEqual(snap.strategyVersion, "")
        XCTAssertTrue(snap.sectors.isEmpty)
        XCTAssertTrue(snap.basketDaily.basketsAvailable, "别的段照常解出来")
        // ⚠ `degraded` 缺键取 **true** 而不是 false:这个位说的是"这份报告完不完整",
        // 缺了它就是**不知道** —— false 等于替服务端保证"一切正常"(拿"没看"当"没有")。
        XCTAssertTrue(snap.degraded)
        XCTAssertTrue(snap.reason.contains("缺 degraded"), "空态要说清为什么,不给一句空白")
    }

    /// 形状变了(不是缺键)同样只降级那一个字段 —— `sectors` 从数组变成对象时,
    /// 报告其余部分照常可用。
    func testReportSectorsShapeChangeDoesNotBreakTheWholeReport() async throws {
        let json = jsonData("""
        {"tradeDate": "20260717", "generatedAt": "g", "strategyVersion": "v1.3.3",
         "sentiment": null, "sectors": {"unexpected": "shape"},
         "degraded": false, "reason": ""}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let snap = try await client.fetchReportLatest()
        XCTAssertTrue(snap.sectors.isEmpty)
        XCTAssertEqual(snap.tradeDate, "20260717", "别的字段一个没丢")
        XCTAssertFalse(snap.degraded, "服务端明说没降级就是没降级")
        XCTAssertEqual(snap.reason, "")
    }

    /// 🔴 **B 类冻结快照的核心回归**:`card_json` 是**写入当时冻住**的,老卡缺一堆新键。
    /// 全字段 `decodeIfPresent` 必须让老卡**照常解得出来** —— ⛔ 合成 `Codable` 会让
    /// 「装了新 App 的用户翻几周前的老卡」变成整张卡解不出。
    func testFrozenBasketCardWithOnlyLegacyKeysStillDecodes() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260601", "generatedAt": "g", "strategyVersion": "v1.3.3",
          "sentiment": null, "sectors": [],
          "basketDaily": {
            "baskets": [{"basketId": 3, "card": {"name": "老卡", "members": [{"tsCode": "600001.SH"}]}}],
            "basketsAvailable": true
          },
          "degraded": false, "reason": ""
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let daily = try await client.fetchReportLatest().basketDaily
        let card = try XCTUnwrap(daily.baskets.first?.card)
        XCTAssertEqual(card.name, "老卡")
        XCTAssertEqual(card.members.count, 1)
        XCTAssertEqual(card.members[0].tsCode, "600001.SH")
        XCTAssertEqual(card.disclaimer, "", "缺键 → 默认值,不是解码失败")
        XCTAssertNil(card.specVersion)
        XCTAssertTrue(card.risks.isEmpty)
        XCTAssertNil(card.scripts)
    }

    /// 取证不完整(⑤ 两段式流水单侧故障)必须**显式标注**,⛔ 不静默当完整证据展示。
    func testBasketCardEvidenceStatusSurfacesIncompleteness() async throws {
        let json = jsonData("""
        {
          "tradeDate": "20260717", "generatedAt": "g", "strategyVersion": "v1.3.3",
          "sentiment": null, "sectors": [],
          "basketDaily": {"baskets": [{"basketId": 4, "card": {
            "name": "取证不全篮", "evidenceStatus": "search_unavailable",
            "evidence": [{"claim": "订单落地", "source": "公司公告", "date": "2026-07-16"}],
            "degraded": true}}], "basketsAvailable": true},
          "degraded": false, "reason": ""
        }
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let daily = try await client.fetchReportLatest().basketDaily
        let card = try XCTUnwrap(daily.baskets.first?.card)
        XCTAssertEqual(card.evidenceStatus, "search_unavailable")
        XCTAssertNotNil(card.evidenceIncompleteNote)
        XCTAssertEqual(card.evidence.first?.source, "公司公告")
        XCTAssertTrue(card.degraded, "降级 = 人话半份缺席,结构化半份照出")
    }

    // MARK: - V2-⑭-B 篮子族端点(`/baskets*`)

    func testFetchBasketsBuildsQueryAndDecodes() async throws {
        MockURLProtocol.handler = { req in
            let url = req.url?.absoluteString ?? ""
            XCTAssertTrue(url.contains("date=20260717"))
            XCTAssertTrue(url.contains("tier=1"))
            return (200, jsonData("""
            {"tradeDate": "20260717",
             "items": [{"basketId": 11, "basketKey": "k", "name": "篮", "tier": 1,
                        "memberCodes": ["600001.SH"]}]}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let items = try await client.fetchBaskets(date: "20260717", tier: 1)
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(items[0].basketId, 11)
    }

    /// 🔴 **`card_not_ready` 必须有独立 case**:404 的 fallback 是 `.notHolding`「持仓已清」,
    /// 不加 case 用户点开一个卡还没生成的篮子会看到「持仓已清」(v1.4 `watchlist` 有案底)。
    func testBasketCardNotReadyMapsToDedicatedErrorNotNotHolding() async throws {
        MockURLProtocol.handler = { _ in
            (404, jsonData(#"{"detail": {"ok": false, "reason": "card_not_ready"}}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.fetchBasketCard(id: 11)
            XCTFail("应抛 cardNotReady")
        } catch let e as APIError {
            XCTAssertEqual(e, .cardNotReady)
            XCTAssertEqual(e.errorDescription, "本篮的卡还没生成")
            XCTAssertNotEqual(e, .notHolding)
        }
    }

    /// 🔴 **B1(2026-08-04 裁定):`card_corrupt` 走 500,且必须有独立 case**。
    /// 三条同时锁死:①**不是** `.cardNotReady`(两者要求的反应完全相反 —— 一个等就行,
    /// 一个必须人排查);②**不是**泛泛的 `.server(500, …)`(那句话用户看不懂);
    /// ③文案就是「这张卡的数据损坏了,需要排查」。卡是冻结件、坏了就是永久坏的,
    /// 当成「还没生成」处理 = 客户端永远重试、界面永远显示"还没生成" = 静默永久失败。
    func testBasketCardCorruptMapsToDedicatedErrorOn500() async throws {
        MockURLProtocol.handler = { _ in
            (500, jsonData(#"{"detail": {"ok": false, "reason": "card_corrupt"}}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.fetchBasketCard(id: 11)
            XCTFail("应抛 cardCorrupt")
        } catch let e as APIError {
            XCTAssertEqual(e, .cardCorrupt)
            XCTAssertEqual(e.errorDescription, "这张卡的数据损坏了,需要排查")
            XCTAssertNotEqual(e, .cardNotReady)
            XCTAssertNotEqual(e, .server(500, "card_corrupt"))
        }
    }

    /// 未登记的 500 **不许**冒充成某个具体业务错误 —— fallback 仍是 `.server(500, …)`。
    func testUnknownFiveHundredStillFallsBackToServerError() async throws {
        MockURLProtocol.handler = { _ in
            (500, jsonData(#"{"detail": {"ok": false, "reason": "something_else"}}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.fetchBasketCard(id: 11)
            XCTFail("应抛 server(500,…)")
        } catch let e as APIError {
            XCTAssertEqual(e, .server(500, "something_else"))
        }
    }

    /// 篮子壳上的 `cardUnavailableReason` 两态文案不许合并(同一条裁定的展示侧)。
    func testBasketShellCardUnavailableTextSplitsCorruptFromNotReady() {
        let notReady = Basket(basketId: 1, cardUnavailableReason: "card_not_ready")
        let corrupt = Basket(basketId: 2, cardUnavailableReason: "card_corrupt")
        XCTAssertEqual(notReady.cardUnavailableText, "本篮的卡还没生成")
        XCTAssertEqual(corrupt.cardUnavailableText, "本篮卡数据损坏,已记录待排查")
    }

    func testBasketNotFoundMapsToDedicatedError() async throws {
        MockURLProtocol.handler = { _ in
            (404, jsonData(#"{"detail": {"ok": false, "reason": "basket_not_found"}}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.fetchBasket(id: 99)
            XCTFail("应抛 basketNotFound")
        } catch let e as APIError {
            XCTAssertEqual(e, .basketNotFound)
            XCTAssertEqual(e.errorDescription, "找不到这个篮子")
        }
    }

    /// 「篮子在、今天还没判过」照返 200 + `notEvaluated=true`(⛔ 不是 404,也不是 unclear)。
    func testFetchBasketVerificationNotEvaluatedIs200() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"basketId": 11, "tradeDate": "20260717", "state": "unclear", "label": "未明",
             "provisional": false, "notEvaluated": true, "rows": []}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let v = try await client.fetchBasketVerification(id: 11)
        XCTAssertTrue(v.notEvaluated)
        XCTAssertEqual(v.badgeText, "今日尚未判定")
    }

    /// 篮子在、那天还没复盘 → 404 **`not_found`(复用既有 reason,⛔ 无需新 case)**。
    func testFetchBasketReviewNotYetReviewedReusesNotFound() async throws {
        MockURLProtocol.handler = { _ in
            (404, jsonData(#"{"detail": {"ok": false, "reason": "not_found"}}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.fetchBasketReview(id: 11, date: "20260717")
            XCTFail("应抛 notFound")
        } catch let e as APIError {
            XCTAssertEqual(e, .notFound)
        }
    }

    /// 🔴 `BasketReview.mech` 也是 **B 类冻结快照** —— 老行缺键必须照常解得出来。
    func testFetchBasketReviewFrozenMechTolerantDecode() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData(#"{"basketId": 5, "name": "老复盘", "mech": {}, "llmSkipReason": "预算耗尽"}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.fetchBasketReview(id: 5)
        XCTAssertEqual(r.name, "老复盘")
        XCTAssertNil(r.llmText, "未生成 ≠ 生成了但没内容")
        XCTAssertEqual(r.llmSkipReason, "预算耗尽")
        XCTAssertEqual(r.depth, "", "缺键兜默认值,不是解码失败")
    }

    // MARK: - V2-⑩-B 计划继承 / 建仓快照

    func testFetchPositionPlansDecodesArmingKeys() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"items": [{"id": 1, "positionId": 3, "version": 1, "sourceBasketId": 11,
                        "sourceCardVersion": 1, "createdAt": "2026-07-17T08:00:00",
                        "plan": {"available": true, "reason": null,
                                 "source_basket_name": "AI 基建", "driver": "算力订单",
                                 "entry_zone": {"low": 9.5, "high": 10.2},
                                 "exit_reference": {"low": 12.0, "high": 13.0},
                                 "exit_reference_armed": false,
                                 "exit_reference_armed_reason": "below_entry_price",
                                 "exit_reference_armed_note": "离场参考低于你的成本,本票不做触达提醒",
                                 "exit_reference_muted": false,
                                 "risks": ["订单证伪"]}}]}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let plans = try await client.fetchPositionPlans(positionId: 3)
        let p = try XCTUnwrap(plans.first)
        XCTAssertTrue(p.available)
        XCTAssertEqual(p.sourceBasketName, "AI 基建")
        XCTAssertEqual(p.entryZone?.rangeText, "¥9.50 ~ ¥10.20")
        XCTAssertFalse(p.exitReferenceArmed)
        XCTAssertEqual(p.exitReferenceArmedReason, "below_entry_price")
        // 未武装文案是**服务端单一源**,客户端不另拍。
        XCTAssertEqual(p.exitReferenceArmedNote, "离场参考低于你的成本,本票不做触达提醒")
        XCTAssertFalse(p.exitReferenceMuted)
        XCTAssertEqual(p.risks, ["订单证伪"])
    }

    /// `POST /positions/{id}/plans` 无既有计划 → 400 **`no_base_plan`**(全新 reason,必须有 case)。
    func testCreatePlanVersionNoBasePlanMapsToDedicatedError() async throws {
        MockURLProtocol.handler = { _ in
            (400, jsonData(#"{"detail": {"ok": false, "reason": "no_base_plan"}}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.createPositionPlanVersion(positionId: 3, plan: .object([:]))
            XCTFail("应抛 noBasePlan")
        } catch let e as APIError {
            XCTAssertEqual(e, .noBasePlan)
            XCTAssertEqual(e.errorDescription, "这笔仓没有可继承的计划基线")
        }
    }

    func testFetchEntrySnapshotDecodesNotCaptured() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"positionId": 3, "tsCode": "600001.SH", "tradeDate": "20260717", "basketId": 11,
             "cardVersion": 1, "tier": 1, "role": "跟随", "createdAt": "2026-07-17T09:31:00",
             "snapshot": {"price": 9.8, "not_captured": ["资金流", "竞价表现", "换手率", "量比"]}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let snap = try await client.fetchEntrySnapshot(positionId: 3)
        XCTAssertEqual(snap.tier, 1)
        // ⛔ 别把"没采"读成"没有"。
        XCTAssertEqual(snap.notCaptured, ["资金流", "竞价表现", "换手率", "量比"])
    }

    // MARK: - V2-⑫ 画像 / ③ 策略包 / ⑨-C 评价

    func testFetchProfilesKeepTwoLedgersSeparate() async throws {
        MockURLProtocol.handler = { req in
            let url = req.url?.absoluteString ?? ""
            if url.contains("/profile/preference") {
                return (200, jsonData("""
                {"asOf": "20260717", "available": true,
                 "items": [{"dimension": "role", "bucket": "龙头", "sample_n": 3,
                            "window_start": "20260601", "window_end": "20260717",
                            "confidence": "low", "pick_rate": 0.4}]}
                """))
            }
            return (200, jsonData(#"{"asOf": "", "available": false, "unavailableReason": "该期从未算过"}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let pref = try await client.fetchPreferenceProfile()
        XCTAssertTrue(pref.available)
        let row = ProfileRow(raw: pref.items[0])
        XCTAssertEqual(row.sampleN, 3)
        XCTAssertTrue(row.isLowConfidence, "低置信度必须能识别出来 —— UI 据此写「样本不足,不给结论」")

        let cap = try await client.fetchCapabilityProfile()
        XCTAssertFalse(cap.available)
        XCTAssertEqual(cap.unavailableReason, "该期从未算过", "`asOf` 为空 = 从未算过,不是「算出来是空的」")
    }

    func testFetchPacksAndPackDecodes() async throws {
        MockURLProtocol.handler = { req in
            if req.url?.absoluteString.contains("/packs/K7-pack-v1") == true {
                return (200, jsonData("""
                {"packVersion": "K7-pack-v1", "isActive": true, "createdAt": "c",
                 "activatedAt": "a", "manifest": {"name": "K7"}, "config": {"weights": {"x": 1}}}
                """))
            }
            return (200, jsonData(#"{"items": [{"packVersion": "K4-pack-v1", "isActive": false}]}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let packs = try await client.fetchPacks()
        XCTAssertEqual(packs.first?.packVersion, "K4-pack-v1")
        let pack = try await client.fetchPack(version: "K7-pack-v1")
        XCTAssertTrue(pack.isActive)
        XCTAssertEqual(pack.config["weights"]?["x"]?.intValue, 1)
    }

    /// 评价**恒 200**:不可用时给可读原因,⛔ 不许拿半截样本给结论。
    func testFetchEvalWeeklyUnavailableStillReturns200WithReason() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"weekStart": "20260713", "weekEnd": "20260717", "available": false,
             "unavailableReason": "前向窗口还没走完", "result": {}, "markdown": ""}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let ev = try await client.fetchEvalWeekly()
        XCTAssertFalse(ev.available)
        XCTAssertEqual(ev.unavailableReason, "前向窗口还没走完")
    }

    /// 比例→百分数格式化:整百分点不留 ".00",非整百分点不四舍五入成整数骗人。
    func testRatioPctFormatting() {
        XCTAssertEqual(NKFmt.ratioPct(0.05), "5%")
        XCTAssertEqual(NKFmt.ratioPct(0.08), "8%")
        XCTAssertEqual(NKFmt.ratioPct(0.055), "5.5%")
        XCTAssertEqual(NKFmt.ratioPct(0.1), "10%")
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
    /// `industryPersistDays` **`nil` ≠ 0**:`nil` = 行业强度表当日无数据(「没看」);
    /// `0` = 评了、不是强度日(「看了,没有」)。UI 据此显示「不可用」而非「0 天」。
    /// ⚠ 候选族退役后,该字段的载体只剩**信息卡**(`GET /report/{date}/info-card/{code}`)。
    func testDecodeInfoCardSnapshotIndustryPersistDaysNullVsZero() async throws {
        func payload(_ persist: String) -> Data {
            jsonData("""
            {"code": "600001.SH", "name": "甲", "tradeDate": "20260728", "klineAvailable": false,
             "snapshot": {"industryPersistDays": \(persist), "consecLimitUpDays": 0}}
            """)
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())

        MockURLProtocol.handler = { _ in (200, payload("null")) }
        let missing = try await client.fetchInfoCard(date: "20260728", code: "600001.SH")
        XCTAssertNil(missing.snapshot.industryPersistDays, "nil = 「没看」,⛔ 不得当 0 天")

        MockURLProtocol.handler = { _ in (200, payload("0")) }
        let zero = try await client.fetchInfoCard(date: "20260728", code: "600001.SH")
        XCTAssertEqual(zero.snapshot.industryPersistDays, 0, "0 = 「看了,不是强度日」")
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
        XCTAssertTrue(report.basketDaily.baskets.isEmpty)
        XCTAssertFalse(report.basketDaily.basketsAvailable,
                       "降级态:三段全 available=false,⛔ 不冒充「今天没有篮子」")
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
    /// 守 CLAUDE.md「404/reason 映射」坑:自选池 `not_found` 曾被 fallback 误显成「持仓已清」)。
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

    // MARK: - 4A.5 设置(V2-② Provider 自填制 + V2-⑪ 按 kind 的推送开关)

    /// V2-②/⑪ 换血后的 `/settings`:`providers[]` + `routes` + `push.kinds[]`。
    /// ⚠ 老形状(`llmProvider`/`llmKeySet` + 六个具名 bool)已整族退役。
    func testDecodeSettingsProvidersRoutesAndPushKinds() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"providers": [{"name": "deepseek", "model": "deepseek-chat", "hasWebSearch": false,
                            "keySet": true, "enabled": true},
                           {"name": "glm", "model": "glm-4", "hasWebSearch": true,
                            "keySet": false, "enabled": false}],
             "routes": {"basket_narrative": "deepseek", "nl_alert": "glm"},
             "push": {"kinds": [
               {"kind": "retreat", "level": "immediate", "label": "退潮红色刹车", "enabled": true},
               {"kind": "d5exit", "level": "important", "label": "时间退出", "enabled": false},
               {"kind": "report_ready", "level": "digest", "label": "盘后报告就绪", "enabled": true},
               {"kind": "some_future_kind", "level": "some_future_level", "label": "未来类型", "enabled": true}
             ]},
             "reviewColMap": {"手续费": "费用合计"}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let s = try await client.fetchSettings()
        XCTAssertEqual(s.providers.count, 2)
        XCTAssertEqual(s.providers[0].name, "deepseek")
        // **只回布尔,绝不回 key 明文**。
        XCTAssertTrue(s.providers[0].keySet)
        XCTAssertFalse(s.providers[1].keySet)
        XCTAssertTrue(s.providers[1].hasWebSearch)
        XCTAssertEqual(s.routes["basket_narrative"], "deepseek")
        XCTAssertEqual(s.reviewColMap, ["手续费": "费用合计"])

        // 按 kind 的开关:**服务端发什么就有什么**(⛔ 客户端不硬编清单)。
        XCTAssertEqual(s.push.kinds.count, 4)
        XCTAssertEqual(s.push.kinds.first(where: { $0.kind == "d5exit" })?.enabled, false)
        // 未识别 level **自成一组照常显示**,⛔ 不静默丢弃。
        let groups = s.push.groupedByLevel
        XCTAssertEqual(groups.map(\.level), ["immediate", "important", "digest", "some_future_level"])
        XCTAssertEqual(groups.last?.kinds.first?.label, "未来类型")
        XCTAssertEqual(s.push.enabledMap["some_future_kind"], true)
    }

    /// `label` 缺席时退回 kind 串本身:**照常显示**好过什么都不显示(E6 同一条纪律)。
    func testPushKindWithoutLabelFallsBackToKindString() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData(#"{"push": {"kinds": [{"kind": "brand_new", "level": "immediate"}]}}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let s = try await client.fetchSettings()
        XCTAssertEqual(s.push.kinds.first?.label, "brand_new")
        XCTAssertEqual(s.push.kinds.first?.enabled, true, "缺 enabled 键按默认开")
    }

    /// `PUT /settings/push`:**全量覆盖式**写 `{kind: enabled}`(缺键 → 422)。
    func testPutSettingsPushSendsFullKindMap() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            let kinds = try XCTUnwrap(obj?["kinds"] as? [String: Any])
            XCTAssertEqual(kinds["retreat"] as? Bool, true)
            XCTAssertEqual(kinds["d5exit"] as? Bool, false)
            XCTAssertEqual(kinds.count, 2)
            return (200, jsonData(#"{"ok": true}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let ok = try await client.putSettingsPush(kinds: ["retreat": true, "d5exit": false])
        XCTAssertTrue(ok)
    }

    /// 缺键 / 未登记 kind → 422 `invalid_push_kinds`,**独立 case**(⛔ 不吃泛泛的「字段校验失败」)。
    func testPutSettingsPushInvalidKindsMapsToDedicatedError() async throws {
        MockURLProtocol.handler = { _ in
            (422, jsonData(#"{"detail": {"ok": false, "reason": "invalid_push_kinds"}}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.putSettingsPush(kinds: ["retreat": true])
            XCTFail("应抛 invalidPushKinds")
        } catch let e as APIError {
            XCTAssertEqual(e, .invalidPushKinds)
        }
    }

    // MARK: - V2-② Provider 注册表(**key 只写不回显**)

    /// 🔴 **回归锁死**:`ProviderOut` 类型里**根本不存在**能回显明文 key 的字段 ——
    /// 契约漂移会在编译期直接报错。这里只断言「创建时确实把 key 发出去了一次」。
    func testCreateProviderSendsKeyOnceAndResponseNeverCarriesPlaintext() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["name"] as? String, "deepseek")
            XCTAssertEqual(obj?["apiKey"] as? String, "sk-secret-abc")
            XCTAssertEqual(obj?["baseUrl"] as? String, "https://api.deepseek.com/v1")
            return (201, jsonData("""
            {"name": "deepseek", "baseUrl": "https://api.deepseek.com/v1", "model": "deepseek-chat",
             "hasWebSearch": false, "enabled": true, "keySet": true}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let p = try await client.createProvider(ProviderCreateRequest(
            name: "deepseek", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat",
            apiKey: "sk-secret-abc", hasWebSearch: false, searchEngine: nil, notes: nil, enabled: true))
        XCTAssertTrue(p.keySet)
    }

    /// 同名 provider → 409 `already_exists`,**独立 case**(须显式走 PUT 更新,防误覆盖)。
    func testCreateProviderDuplicateMapsToAlreadyExists() async throws {
        MockURLProtocol.handler = { _ in
            (409, jsonData(#"{"detail": {"ok": false, "reason": "already_exists"}}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.createProvider(ProviderCreateRequest(
                name: "glm", baseUrl: "u", model: "m", apiKey: nil, hasWebSearch: false,
                searchEngine: nil, notes: nil, enabled: true))
            XCTFail("应抛 alreadyExists")
        } catch let e as APIError {
            XCTAssertEqual(e, .alreadyExists)
        }
    }

    /// 局部更新:**未出现的字段不改** —— 没填 key 时请求体里**不该有 `apiKey` 这个键**
    /// (合成 `Encodable` 对 Optional 走 `encodeIfPresent`,这正是这里要的行为)。
    func testUpdateProviderOmitsApiKeyWhenNotProvided() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertFalse(obj?.keys.contains("apiKey") ?? true, "不传 = 不改,⛔ 不能变成清空")
            XCTAssertEqual(obj?["model"] as? String, "glm-4-plus")
            return (200, jsonData("""
            {"name": "glm", "baseUrl": "u", "model": "glm-4-plus", "hasWebSearch": true,
             "enabled": true, "keySet": true}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let p = try await client.updateProvider(name: "glm", ProviderUpdateRequest(
            baseUrl: "u", model: "glm-4-plus", apiKey: nil, hasWebSearch: true,
            searchEngine: nil, notes: nil, enabled: true))
        XCTAssertEqual(p.model, "glm-4-plus")
    }

    func testDeleteProviderDecodesOk() async throws {
        MockURLProtocol.handler = { req in
            XCTAssertEqual(req.httpMethod, "DELETE")
            XCTAssertTrue(req.url?.absoluteString.hasSuffix("/settings/providers/glm") ?? false)
            return (200, jsonData(#"{"ok": true}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let ok = try await client.deleteProvider(name: "glm")
        XCTAssertTrue(ok)
    }

    /// 路由表未知任务名 → 422 `invalid_task`,**独立 case**。
    func testPutLLMRoutesInvalidTaskMapsToDedicatedError() async throws {
        MockURLProtocol.handler = { _ in
            (422, jsonData(#"{"detail": {"ok": false, "reason": "invalid_task"}}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.putLLMRoutes(routes: ["no_such_task": "glm"], defaultProvider: nil)
            XCTFail("应抛 invalidTask")
        } catch let e as APIError {
            XCTAssertEqual(e, .invalidTask)
        }
    }

    func testFetchLLMRoutesDecodes() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData(#"{"routes": {"nl_alert": "glm"}, "defaultProvider": "deepseek"}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.fetchLLMRoutes()
        XCTAssertEqual(r.routes["nl_alert"], "glm")
        XCTAssertEqual(r.defaultProvider, "deepseek")
    }

    // MARK: - V2-⑪-C 自然语言临时提醒(**只通知,永不交易**)

    /// 确认卡**七项必须齐**(含行情延迟披露与「只通知不自动交易」固定尾巴)。
    func testParseAlertReturnsSevenItemConfirmationCard() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["text"] as? String, "跌破 12 块提醒我")
            return (200, jsonData("""
            {"ok": true, "action": "create", "reason": "ok", "narrative": "在 12 元下方提醒一次。",
             "degraded": false,
             "confirmationCard": {"subject": "600001.SH 甲", "condition": "最新价 < 12.00",
               "activeWindow": "今日 09:30–15:00", "notifyLimit": "最多 1 次,冷却 300 秒",
               "expiry": "今日收盘自动失效",
               "quoteDelayDisclosure": "行情可能有延迟或中断,以券商成交为准",
               "noAutoTrade": "只通知,不自动交易", "rule": {"metric": "price"}},
             "draft": {"tsCode": "600001.SH", "nlText": "跌破 12 块提醒我",
                       "conditions": [{"metric": "price", "op": "lt", "value": 12.0}],
                       "logic": "all", "persist": false, "cooldownSeconds": 300, "maxFires": 1}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let parsed = try await client.parseAlert(text: "跌破 12 块提醒我", tsCode: "600001.SH")
        let card = try XCTUnwrap(parsed.confirmationCard)
        XCTAssertEqual(card.rows.count, 7, "七项一项都不许省")
        XCTAssertFalse(card.quoteDelayDisclosure.isEmpty, "行情延迟披露是**必选项**")
        XCTAssertFalse(card.noAutoTrade.isEmpty, "「只通知不自动交易」是固定尾巴")
        XCTAssertEqual(parsed.draft?.conditions.first?.metric, "price")
        XCTAssertEqual(parsed.draft?.cooldownSeconds, 300)
    }

    /// LLM 不可用 → **恒 200** + `degraded=true` + 手填表单,**不静默失败**。
    func testParseAlertDegradedStillReturns200WithManualForm() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"ok": false, "action": "create", "reason": "llm_unavailable", "degraded": true,
             "manualForm": {"fields": ["metric", "op", "value"]}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let parsed = try await client.parseAlert(text: "随便说点什么")
        XCTAssertTrue(parsed.degraded)
        XCTAssertNil(parsed.confirmationCard)
        XCTAssertNotNil(parsed.manualForm, "降级必须给出手填表单,⛔ 不静默失败")
    }

    /// `draft` **原样回传** `POST /alerts`(LLM 解析只是替用户先填好,落库路径只有一条)。
    func testCreateAlertPostsDraftVerbatim() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(obj?["tsCode"] as? String, "600001.SH")
            XCTAssertEqual(obj?["maxFires"] as? Int, 1)
            XCTAssertEqual((obj?["conditions"] as? [[String: Any]])?.first?["metric"] as? String, "price")
            return (201, jsonData("""
            {"id": 5, "tsCode": "600001.SH", "nlText": "跌破 12 块提醒我", "rule": {},
             "condition": "最新价 < 12.00", "persist": false, "cooldownSeconds": 300,
             "maxFires": 1, "firedCount": 0, "status": "active", "expiredNow": false}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let alert = try await client.createAlert(AlertDraft(
            tsCode: "600001.SH", nlText: "跌破 12 块提醒我",
            conditions: [AlertCondition(metric: "price", op: "lt", value: 12.0)],
            cooldownSeconds: 300, maxFires: 1))
        XCTAssertEqual(alert.id, 5)
        XCTAssertEqual(alert.statusLabel, "生效中")
        XCTAssertEqual(alert.subjectLabel, "600001.SH")
    }

    /// 规则重复 → 409 `duplicate_alert`;规则不合白名单 → 422 `invalid_rule`。两者各有独立 case。
    func testAlertDuplicateAndInvalidRuleMapToDedicatedErrors() async throws {
        MockURLProtocol.handler = { _ in
            (409, jsonData(#"{"detail": {"ok": false, "reason": "duplicate_alert"}}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.createAlert(AlertDraft())
            XCTFail("应抛 duplicateAlert")
        } catch let e as APIError {
            XCTAssertEqual(e, .duplicateAlert)
        }

        MockURLProtocol.handler = { _ in
            (422, jsonData(#"{"detail": {"ok": false, "reason": "invalid_rule"}}"#))
        }
        do {
            _ = try await client.createAlert(AlertDraft())
            XCTFail("应抛 invalidRule")
        } catch let e as APIError {
            XCTAssertEqual(e, .invalidRule)
        }
    }

    /// `status` 是库里那一列,`expiredNow` 是「按此刻算还生不生效」—— **分开给、不合并**。
    func testFetchAlertsUsesStatusQueryAndKeepsExpiredNowSeparate() async throws {
        MockURLProtocol.handler = { req in
            // ⚠ ⑭-B 契约修正:查询键是 `status`,**不是** `status_filter`。
            XCTAssertTrue(req.url?.absoluteString.contains("status=active") ?? false)
            return (200, jsonData("""
            {"items": [{"id": 5, "tsCode": null, "nlText": "", "rule": {}, "condition": "大盘跌 2%",
                        "status": "active", "expiredNow": true, "firedCount": 1, "maxFires": 1}]}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let alerts = try await client.fetchAlerts(status: "active")
        XCTAssertEqual(alerts[0].status, "active")
        XCTAssertTrue(alerts[0].expiredNow)
        XCTAssertTrue(alerts[0].statusLabel.contains("已过期"))
        XCTAssertEqual(alerts[0].subjectLabel, "大盘", "tsCode=null = 大盘级")
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

    // MARK: - ⑩-C 用户可选补充(`POST /decisions` 语义换血)+ 只读归因入口
    //
    // ⚠ **V2-⑮ 删掉的四个方法**:`linkDecision` / `cancelDecision` / `reviseDecision` /
    // `setScenarioOutcome` —— 服务端 ⑩-C 已删对应写端点(`decision_log` 停写留档),
    // 留着就是**假成功面**(点了没有任何写入通道)。机器判据见
    // `tests/test_contract_crosscheck.py`(客户端调用面 ⊆ 服务端路由面)。

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

    /// 「用户可选补充」请求体:七枚**英文码** + 一句可选说明。
    /// ⛔ 不发中文键(服务端 `NoteLabelLiteral` 是唯一源);⛔ 请求体里没有 `createdAt`
    /// (类型上就没有这个字段,物理杜绝客户端覆盖)。
    func testPostDecisionNoteSendsLabelCodesAndNeverCreatedAt() async throws {
        MockURLProtocol.handler = { req in
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertNil(obj?["createdAt"])
            XCTAssertEqual(obj?["code"] as? String, "600001.SH")
            XCTAssertEqual(obj?["positionId"] as? Int, 7)
            XCTAssertEqual(obj?["labels"] as? [String], ["THEME_SHIFT", "VOLUME_BREAKOUT"])
            XCTAssertEqual(obj?["voiceNote"] as? String, "跟着龙头进的")
            return (200, jsonData(#"{"ok": true, "recorded": ["label", "voice_note"]}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.postDecisionNote(code: "600001.SH", positionId: 7,
                                                  labels: ["THEME_SHIFT", "VOLUME_BREAKOUT"],
                                                  voiceNote: "跟着龙头进的")
        XCTAssertEqual(r.recorded, ["label", "voice_note"])
    }

    /// **空提交合法**:`recorded=[]` 是「这次没有可落的内容」,**不是错误**(服务端 200)。
    func testPostDecisionNoteEmptySubmissionIsLegal() async throws {
        MockURLProtocol.handler = { _ in (200, jsonData(#"{"ok": true, "recorded": []}"#)) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.postDecisionNote(code: nil)
        XCTAssertTrue(r.ok)
        XCTAssertTrue(r.recorded.isEmpty)
    }

    /// `GET /decisions` 保留为**只读归因**入口(v2.0.0 起零新增行,读的都是历史)。
    func testDecodeDecisionLogHistoricalRowIncludingMaxChasePct() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"items": [{"id": 1, "code": "600001.SH", "name": "甲", "createdAt": "c",
              "whyBuy": "b", "whyEntryPrice": "e", "targetPrice": null, "exitLow": null,
              "exitHigh": null, "thesisTags": [], "invalidation": "i", "contingencyScenarios": [],
              "playbookTag": "SWING_CHASE", "plannedPrice": null, "plannedQty": null,
              "maxChasePct": 3.5, "status": "filled", "positionId": 7, "revisionOf": null}]}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let logs = try await client.listDecisions()
        XCTAssertEqual(logs.first?.maxChasePct, 3.5)
        XCTAssertEqual(logs.first?.positionId, 7)
    }

    func testListDecisionsBuildsFilterQuery() async throws {
        MockURLProtocol.handler = { req in
            let url = req.url?.absoluteString ?? ""
            XCTAssertTrue(url.contains("status=filled"))
            XCTAssertTrue(url.contains("code=600001.SH"))
            XCTAssertTrue(url.contains("from=20260701"))
            XCTAssertTrue(url.contains("to=20260731"))
            XCTAssertFalse(url.contains("%3F"), "带 query 的 URL 不得把 ? 编码成 %3F")
            return (200, jsonData(#"{"items": []}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        _ = try await client.listDecisions(status: "filled", code: "600001.SH",
                                           from: "20260701", to: "20260731")
    }

    func testListDecisionsWithoutFiltersOmitsQueryString() async throws {
        MockURLProtocol.handler = { req in
            XCTAssertFalse(req.url?.absoluteString.contains("?") ?? true)
            return (200, jsonData(#"{"items": []}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        _ = try await client.listDecisions()
    }

    /// 🔴 **回归锁死:`sampleDecisionJSON` 仍能解码**(历史行形状一个字段没改),
    /// 但客户端**不再有任何写决策日志的方法** —— 那四个写端点服务端已删。
    func testHistoricalDecisionShapeStillDecodes() throws {
        let log = try JSONDecoder().decode(DecisionLog.self,
                                           from: jsonData(Self.sampleDecisionJSON))
        XCTAssertEqual(log.id, 1)
        XCTAssertEqual(log.thesisTagLabels, ["题材主线", "资金流向"])
        XCTAssertEqual(log.contingencyScenarios.count, 2)
        XCTAssertEqual(log.contingencyScenarios[0].actionLabel, "持有")
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
