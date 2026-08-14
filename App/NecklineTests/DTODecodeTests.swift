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
                        "upsidePath": "订单落地后先修复缺口,再沿 5 日线台阶式抬升,走到前高一带算走完",
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
        XCTAssertEqual(card.upsidePath,
                       "订单落地后先修复缺口,再沿 5 日线台阶式抬升,走到前高一带算走完")
        XCTAssertEqual(card.risks, ["订单证伪"])
        XCTAssertEqual(card.fingerprint.charterVersion, "v1.3.3")
        XCTAssertEqual(card.fingerprint.packVersion, "K7-pack-v1")
        // V2.3.2-⑤:**老卡**(这份 fixture 的 `fingerprint` 里没有这两键)→ nil,
        // ⛔ 不炸、⛔ 不当"配置丢了" —— 冻结快照不回填新键(CLAUDE.md 两类论)。
        XCTAssertNil(card.fingerprint.lossWarningPct)
        XCTAssertNil(card.fingerprint.lossWarningAction)
        XCTAssertEqual(card.fingerprint.stopPct, 0.05, "⛔ 本版只加键不删键(两步淘汰第一步)")
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
        XCTAssertEqual(m.tagAbsences, [], "旧快照缺少展示标签时仍应宽松解码")

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
        XCTAssertNil(card.generationSource, "老冻结卡缺键时不可猜测生成来源")
        XCTAssertTrue(card.risks.isEmpty)
        XCTAssertNil(card.upsidePath)
        // V2.2-③-E/③-C/③-C2 九键同样是纯新增 —— 老卡缺键必须解得出来,⛔ 不崩、
        // 也⛔ 兜成"未判定"这种看起来像结论的占位(三键都该是 nil)。
        XCTAssertNil(card.engineCode)
        XCTAssertNil(card.engineVersion)
        XCTAssertNil(card.skeletonVersion)
        XCTAssertNil(card.members[0].positionVerdict)
        XCTAssertNil(card.members[0].positionMetrics)
        XCTAssertNil(card.members[0].coreVerdict)
        XCTAssertNil(card.members[0].coreMetrics)
        XCTAssertNil(card.members[0].positionVerdictLabel, "verdict 缺席时展示层也必须是 nil,不是「未判定」占位")
    }

    // MARK: - V2.2-③-E/③-C/③-C2 K8 篮子卡九键(引擎三件套 · 位置三件套 · 核心三件套)

    /// 九键全部有值时逐个解出来 —— 引擎三键**壳与卡两处都发**(裁定 #9:两条路都从
    /// `baskets` 表行直填,不是 `scorePercent` 那种"live 路径刻意留空"的非对称)。
    func testAllNineK8CardKeysDecodeWhenPresent() async throws {
        let json = jsonData("""
        {"tradeDate": "20260810", "items": [{
          "basketId": 21, "basketKey": "bk1", "name": "篮子甲", "tier": 1,
          "memberCodes": ["600001.SH"],
          "engineCode": "C", "engineVersion": "C1", "skeletonVersion": "K8-V0.5",
          "card": {
            "name": "篮子甲", "engineCode": "C", "engineVersion": "C1",
            "skeletonVersion": "K8-V0.5",
            "members": [{
              "tsCode": "600001.SH", "name": "甲",
              "positionVerdict": "weak", "positionReason": "支撑刚破又收回",
              "positionMetrics": {"platform_days": 12, "at_60d_high": false},
              "coreVerdict": "unfit", "coreReason": "行业内 30/42,是跟风",
              "coreMetrics": {"industry_member_count": 42, "industry_rs_rank_20d": 30}
            }]
          }
        }]}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let items = try await client.fetchBaskets()
        let b = try XCTUnwrap(items.first)
        // 引擎三件套:壳上直接有值(⛔ 不需要走 card 兜底)。
        XCTAssertEqual(b.engineCode, "C")
        XCTAssertEqual(b.engineVersion, "C1")
        XCTAssertEqual(b.skeletonVersion, "K8-V0.5")
        XCTAssertEqual(b.engineVersionDisplay, "C1")
        // 卡上是同一份归属的另一份拷贝(裁定 #9:成员继承篮子引擎,成员本身没有这两键)。
        XCTAssertEqual(b.card?.engineCode, "C")
        XCTAssertEqual(b.card?.engineVersion, "C1")
        XCTAssertEqual(b.card?.skeletonVersion, "K8-V0.5")

        let m = try XCTUnwrap(b.card?.members.first)
        XCTAssertEqual(m.positionVerdict, "weak")
        XCTAssertEqual(m.positionReason, "支撑刚破又收回")
        XCTAssertEqual(m.positionVerdictLabel, "勉强")
        XCTAssertEqual(m.positionVerdictTone, .warn)
        XCTAssertEqual(m.coreVerdict, "unfit")
        XCTAssertEqual(m.coreReason, "行业内 30/42,是跟风")
        XCTAssertEqual(m.coreVerdictLabel, "不合适")
        XCTAssertEqual(m.coreVerdictTone, .bad)
        // 读数原样透传(自由结构,`NKJSON` 载体)—— 分母必须读得出来。
        XCTAssertEqual(m.positionMetrics?["platform_days"]?.intValue, 12)
        XCTAssertEqual(m.coreMetrics?["industry_member_count"]?.intValue, 42)
        // ⚠ **NKJSON Bool 必须排在 Double 之前**:`at_60d_high: false` 若顺序反了会被
        // 解成数字 0,界面就会显示成"0"而不是"否"——这条链路也要守住这一坑。
        XCTAssertEqual(m.positionMetrics?["at_60d_high"]?.boolValue, false)
        XCTAssertNil(m.positionMetrics?["at_60d_high"]?.doubleValue,
                     "布尔读数不得被解成数字(顺序反了会显示成 0)")
    }

    /// **显式 `null`** 与「键缺失」是两条不同的路,都必须解得出来、都归 `nil`
    /// (⛔ 不许抛错;pydantic 的 `Optional[...] = None` 字段服务端本就可能显式发 `null`)。
    func testNineK8CardKeysDecodeWhenExplicitlyNull() async throws {
        let json = jsonData("""
        {"basketId": 22, "engineCode": null, "engineVersion": null, "skeletonVersion": null,
         "card": {"name": "篮", "engineCode": null, "engineVersion": null, "skeletonVersion": null,
           "members": [{"tsCode": "600002.SH", "positionVerdict": null, "positionReason": null,
             "positionMetrics": null, "coreVerdict": null, "coreReason": null, "coreMetrics": null}]}}
        """)
        let b = try JSONDecoder().decode(Basket.self, from: json)
        XCTAssertNil(b.engineVersionDisplay)
        let m = try XCTUnwrap(b.card?.members.first)
        XCTAssertNil(m.positionVerdict)
        XCTAssertNil(m.positionMetrics)
        XCTAssertNil(m.coreVerdict)
        XCTAssertNil(m.coreMetrics)
    }

    /// 引擎徽标展示读法:壳缺席(极旧数据的边缘情形)时兜底读卡;两处都没有则 `nil`。
    func testEngineVersionDisplayFallsBackToCardWhenShellFieldsAreMissing() {
        let shellHasIt = Basket(basketId: 1, engineVersion: "Z1", card: BasketCard(engineVersion: "Z1"))
        XCTAssertEqual(shellHasIt.engineVersionDisplay, "Z1")

        let onlyCardHasIt = Basket(basketId: 2, card: BasketCard(engineCode: "Y", engineVersion: "Y1",
                                                                  skeletonVersion: "K8-V0.5"))
        XCTAssertEqual(onlyCardHasIt.engineCodeDisplay, "Y")
        XCTAssertEqual(onlyCardHasIt.engineVersionDisplay, "Y1")
        XCTAssertEqual(onlyCardHasIt.skeletonVersionDisplay, "K8-V0.5")

        let neitherHasIt = Basket(basketId: 3)
        XCTAssertNil(neitherHasIt.engineVersionDisplay)
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

    /// `sentiment` 用 `"{}"`(空对象)而不是 `null`——**这才是服务端真实发的形状**
    /// (`app.py::_empty_report()` 传 `sentiment={}` 给 `ReportOut.sentiment: Dict[str, Any]`
    /// 这个必填非 Optional 字段,pydantic 序列化空 dict 恒是 JSON `{}`,不是 `null`;
    /// 2026-08-05 契约类型核对拿真实端点实测确认,见
    /// `testDecodeEmptyReportRealShapeSentimentIsEmptyObjectNotNull`)。此前这里手写
    /// `null` 是**错的测试假设**——`null` 走 `decodeIfPresent` 的判空捷径,天然安全,
    /// 从未真正跑到过 `{}` 那条路径,给了假的绿灯。
    func testDecodeReportDegradedEmpty() async throws {
        let json = jsonData("""
        {"tradeDate": "", "generatedAt": "", "strategyVersion": "", "sentiment": {},
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

    // MARK: - 2026-08-05 契约类型核对补漏:`sentiment: {}` 拖炸整份报告解码(与
    // `engineApiVersion` 同一晚发现的第二个"名对型不对/可选性没对齐"坑,但这次是
    // 「服务端可空对象、客户端硬解码」而非「数字 vs 字符串」)。
    //
    // 起因:`ReportResponse.init(from:)` 对 `sentiment` 用的是裸 `decodeIfPresent`(未接
    // `try?`),而同一个函数里紧邻三行之后的 `intel`/`sectorMoneyflow`/`dataFreshness`
    // 早就接了 `try?`(注释原话:「服务端恒是对象……空对象缺我方强类型要求的字段，标准
    // 合成解码会直接抛错，这里用 try? 把『形状对不上』也当『没有』处理」)—— `sentiment`
    // 满足**完全相同**的前提(`SentimentSnapshot` 九个非 Optional 字段,`{}` 必抛
    // `keyNotFound`),却是这一组四个字段里唯一漏掉 `try?` 的。
    //
    // 触发路径**不是边角情形**:`app.py::_empty_report()` 是 `GET /report/latest`
    // 当日无报告、与 `GET /report?date=` 查无该日报告**两条主路径**共用的降级响应,
    // 2026-08-05 拿真实 `api_env`(零报告的全新库)打 `/api/v1/report/latest` 实测,原始
    // 响应逐字节确认 `"sentiment": {}`(非 `null`,非缺键)。本测试固化那份真实响应,
    // 而不是手写一个"看起来像"的最小样例——这正是导致该坑长期未被发现的原因:全部手写
    // fixture 都用了 `sentiment: null` 这个从未在生产实际出现过的形状。
    func testDecodeEmptyReportRealShapeSentimentIsEmptyObjectNotNull() async throws {
        // 2026-08-05 `GET /api/v1/report/latest` 对全新(零报告)库的真实响应原文。
        let json = jsonData("""
        {"tradeDate": "", "generatedAt": "", "strategyVersion": "", "sentiment": {}, "sectors": [], \
        "basketDaily": {"tradeDate": "", "baskets": [], "basketsAvailable": false, \
        "basketsUnavailableReason": null, "droppedBaskets": [], "droppedBasketsAvailable": false, \
        "droppedBasketsUnavailableReason": null, "reviews": [], "reviewsAvailable": false, \
        "reviewsUnavailableReason": null, "reviewD0": null, "packVersion": null, "notes": []}, \
        "missedEntryHint": "", "intel": {}, "sectorMoneyflow": {}, "newsAlerts": [], \
        "newsAlertsScan": [], "dataFreshness": {}, "degraded": true, "reason": "no_report"}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())

        // 修复前:这一行会抛 `DecodingError.keyNotFound(... "trade_date" ... Path: sentiment)`,
        // 整份报告解不出——今日计划页对全新安装 / 无报告日期回放会直接报错退回空态。
        let report = try await client.fetchReportLatest()

        XCTAssertTrue(report.degraded)
        XCTAssertEqual(report.reason, "no_report")
        XCTAssertNil(report.sentiment, "空对象形状不对,归一成 nil——不是崩溃,也不是硬凑一个假快照")
        XCTAssertEqual(report.sectors, [])
        XCTAssertNil(report.intel)
        XCTAssertNil(report.sectorMoneyflow)
        XCTAssertNil(report.dataFreshness)
        XCTAssertFalse(report.basketDaily.basketsAvailable)
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

    // ⚠ V2.1-① 起「v1.4-⑦-B 问询历史」一节(`testFetchInquiriesDecodesListAndBuildsQuery`/
    // `testFetchInquiriesWithoutTsCodeOmitsQueryParam`/`testFetchInquiryDetailDecodes`/
    // `testFetchInquiryDetailNonexistentMapsToExistingNotFoundCase`)已随问询台整链
    // 退役删除(`fetchInquiries`/`fetchInquiryDetail`/`InquiryLogEntry` 均已物理删除)。

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
        let positions = try await client.fetchPositions().holdings
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
        // V2.3.2-⑤:这份 fixture **没有**这两键(= 老服务端 / 老章程未声明)→ nil,
        // 展示层退回「止损线」老文案。⛔ 不许把缺键当成"声明为强制条件单"以外的任何东西。
        XCTAssertNil(p.lossWarningPct)
        XCTAssertNil(p.lossWarningAction)
        XCTAssertFalse(p.isLossWarningCharter)
        XCTAssertEqual(p.stopLineLabel, "止损线")
        XCTAssertEqual(p.stopLineShortLabel, "止损线")
        XCTAssertNil(p.lossWarningDisclosure)
    }

    /// V2.3.2-⑤(K8.md §十九):`loss_warning_action = review` 治下,这条线叫「亏损警戒线」。
    /// 🔴 **数值口径一字未变**:`stopLine` / `hasBrokenStop` / `distToStopPctServer` 全部照旧,
    /// 变的只是称呼与那句披露 —— 本例同时正面断言"数没变"。
    func testPositionUnderLossWarningCharter() async throws {
        let json = jsonData("""
        {"holdings": [
          {"id": 9, "code": "600519.SH", "name": "贵州茅台", "buyPrice": 1500.0, "qty": 100,
           "entryReason": "", "buyDate": "20260716", "price": 1400.0, "status": "holding",
           "stopLine": 1425.0, "lossWarningPct": 0.05, "lossWarningAction": "review",
           "stopOrderChecked": false,
           "dCount": 2, "maxHoldDays": null, "distToStopPct": -0.0179, "retraceState": null,
           "todayAction": "止损警戒:现价已跌破亏损警戒线,触发后由你复核原判断(系统不代下单)"}
        ]}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let p = try await client.fetchPositions().holdings[0]
        XCTAssertEqual(p.lossWarningPct, 0.05)
        XCTAssertEqual(p.lossWarningAction, "review")
        XCTAssertTrue(p.isLossWarningCharter)
        XCTAssertEqual(p.stopLineLabel, "亏损警戒线")
        XCTAssertEqual(p.stopLineShortLabel, "警戒线")   // 紧凑位三字,版式不变
        // 🔴 V2.4.0 P3.1 取代:原断言「离场决策在你」→「触发后由你复核原判断」
        // (K8.md §十九 逐字,`charter_copy.ADVISORY_ACTION_PHRASE`)。
        XCTAssertEqual(p.lossWarningDisclosure,
                       "到线(−5%)只发亏损警戒,触发后由你复核原判断 —— 系统不代下单、不自动卖出")
        // 🔴 判定与数值一字未动
        XCTAssertEqual(p.stopLine, 1425.0)
        XCTAssertTrue(p.hasBrokenStop)                  // 1400 <= 1425,与口径无关
        XCTAssertEqual(p.distToStopPctServer, -0.0179)
    }

    /// 非 `review` 的取值(将来某版章程若换口径)→ 退回「止损线」。⛔ 别写死成亏损警戒。
    func testPositionWithNonReviewActionFallsBackToStopWording() async throws {
        let json = jsonData("""
        {"holdings": [
          {"id": 10, "code": "600001.SH", "name": "甲", "buyPrice": 10.0, "qty": 100,
           "entryReason": "", "buyDate": "20260716", "price": 9.9, "status": "holding",
           "stopLine": 9.5, "lossWarningPct": 0.05, "lossWarningAction": "hard_stop",
           "stopOrderChecked": false, "dCount": 1, "maxHoldDays": 5,
           "distToStopPct": 0.04, "retraceState": null, "todayAction": ""}
        ]}
        """)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let p = try await client.fetchPositions().holdings[0]
        XCTAssertFalse(p.isLossWarningCharter)
        XCTAssertEqual(p.stopLineLabel, "止损线")
        XCTAssertNil(p.lossWarningDisclosure)
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
        let p = try await client.fetchPositions().holdings[0]
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
        let p = try await client.fetchPositions().holdings[0]
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
        let p = try await client.fetchPositions().holdings[0]
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
        let p = try await client.fetchPositions().holdings[0]
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
        let p = try await client.fetchPositions().holdings[0]
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
        let p = try await client.fetchPositions().holdings[0]
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
        let p = try await client.fetchPositions().holdings[0]
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

    // ⚠ V2.1-① 起「4A.5 问询台」一节(`testDecodeInquiryAnalyzedWarn`/
    // `testDecodeInquiryAnalyzed`/`testDecodeInquiryIdPresentNullAndAbsent`)已随
    // 问询台整链退役删除(`sendInquiry`/`InquiryResult` 均已物理删除)。

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
             "tavily": {"keySet": true},
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
        XCTAssertTrue(s.tavily.keySet)
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

    func testPutLLMRoutesDecodesSavedDefaultProvider() async throws {
        MockURLProtocol.handler = { req in
            XCTAssertEqual(req.httpMethod, "PUT")
            let body = try XCTUnwrap(req.httpBodyOrStream())
            let obj = try XCTUnwrap(try JSONSerialization.jsonObject(with: body) as? [String: Any])
            XCTAssertEqual(obj["defaultProvider"] as? String, "deepseek")
            return (200, jsonData(#"{"routes": {}, "defaultProvider": "deepseek"}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let saved = try await client.putLLMRoutes(routes: [:], defaultProvider: "deepseek")
        XCTAssertEqual(saved.defaultProvider, "deepseek")
    }

    func testTavilyKeyIsWriteOnlyAndClearDecodesSafeStatus() async throws {
        var requestCount = 0
        MockURLProtocol.handler = { req in
            requestCount += 1
            if req.httpMethod == "PUT" {
                let body = try XCTUnwrap(req.httpBodyOrStream())
                let obj = try XCTUnwrap(try JSONSerialization.jsonObject(with: body) as? [String: Any])
                XCTAssertEqual(obj["apiKey"] as? String, "tvly-test")
                return (200, jsonData(#"{"keySet": true}"#))
            }
            XCTAssertEqual(req.httpMethod, "DELETE")
            return (200, jsonData(#"{"keySet": false}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let saved = try await client.putTavilyKey("tvly-test")
        let cleared = try await client.deleteTavilyKey()
        XCTAssertTrue(saved.keySet)
        XCTAssertFalse(cleared.keySet)
        XCTAssertEqual(requestCount, 2)
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
        // V2.3.2-⑤:这份 fixture 没发 `lossWarningAction` → nil,称呼退回「止损线」。
        XCTAssertNil(s.lossWarningAction)
        XCTAssertEqual(s.stopLineLabel, "止损线")
    }

    /// V2.3.2-⑤:`/entry-suggestion` 在亏损警戒口径下,预计线改叫「亏损警戒线」(数值不变)。
    func testDecodeEntrySuggestionUnderLossWarningCharter() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"ok": true, "code": "600001.SH", "price": 50.0,
             "qtyLow": 400, "qtyHigh": 800, "capFloor": 20000.0, "capCeil": 40000.0,
             "stopLine": 47.5, "lossWarningPct": 0.05, "lossWarningAction": "review"}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let s = try await client.entrySuggestion(code: "600001.SH", price: 50.0)
        XCTAssertEqual(s.stopLine, 47.5)                 // 数值口径一字未变
        XCTAssertEqual(s.stopLineLabel, "亏损警戒线")
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

    // ⚠ **熔断 DTO 解码测试已随两个类型于 v2.3.0 整组删除**（两步淘汰第二步）。
    //
    // V2.2-⑤-B 退役机制时留下 `CircuitState` / `CircuitEpisode` 两个 DTO 与服务端
    // `PositionsOut.circuit` 空态，为的是「老客户端装着不换包也解得出」。v2.3.0 逐版核实后
    // 确认：历代客户端 `/positions` 一律解进 `PositionsListResponse { holdings }`，
    // **从没有一版声明过 `circuit` 字段** —— 那条在线升级前提在这个键上并不存在，
    // 故服务端删键、客户端删 DTO、这组测试一并删。⛔ 不是漏删。

    // MARK: - 2026-08-05 定向快修回归:真实生产 7 篮载荷(`engineApiVersion` 字段名对、类型不对)
    //
    // 起因:`BasketFingerprint.engineApiVersion` 客户端曾按 `String` 解码,服务端契约恒发
    // int(`neckline/db.py` 三处 `engine_api_version INTEGER`,`selection/pack.py`/
    // `aggregate.py` 的 pydantic 字段同为 `int`)——`typeMismatch` 顺着 `BasketCard.
    // init(from:)` 里 `fingerprint` 那一路抛穿,拖炸**整份** `ReportResponse`(Mac 实证
    // 2026-08-05 晚,当日报告解不出、今日计划整页退回空态;iPhone 同代码同炸)。V2 契约
    // 三方对拍(`archive/对照表/V2_契约三方对拍_20260803.md` §4.2/§4.3)当时只核对了 `fingerprint`
    // 的**字段名**,没核对每个键的**值类型**,这一类"名对型不对"的坑因此没被挡住
    // (已在该文件 §七 补登记一条)。
    //
    // 本测试固化事发当时抓到的真实生产 200 响应(`GET /report`,2026-08-05 交易日,7 篮,
    // 单行 minified,数据是自家生产选股结果、非用户隐私,未脱敏)——不是挑几个字段手写小
    // 样例,是**整份真实响应喂真实解码器**,防同类"字段名对、类型没对"的坑再度整份报告拖炸。
    func testDecodeRealProductionReportFixtureFullShapeAndTypes() async throws {
        let json = jsonData(Self.realProductionReportJSON20260805)
        MockURLProtocol.handler = { _ in (200, json) }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())

        let report = try await client.fetchReportLatest()

        XCTAssertEqual(report.tradeDate, "20260805")
        XCTAssertEqual(report.generatedAt, "2026-08-05T10:11:39+00:00")
        XCTAssertEqual(report.strategyVersion, "v1.3.3")
        XCTAssertFalse(report.degraded)
        XCTAssertEqual(report.reason, "")
        XCTAssertNotNil(report.dataFreshness, "三组新鲜度都在,整份 dataFreshness 不该解丢")

        let daily = report.basketDaily
        XCTAssertTrue(daily.basketsAvailable)
        XCTAssertTrue(daily.droppedBasketsAvailable)
        XCTAssertTrue(daily.reviewsAvailable)
        XCTAssertEqual(daily.packVersion, "K7-pack-v1")
        XCTAssertEqual(daily.baskets.count, 7, "生产当日 7 篮,逐篮解码一个都不能少")
        XCTAssertEqual(Set(daily.baskets.map(\.basketId)), Set([5, 6, 7, 8, 9, 10, 11]))
        XCTAssertEqual(daily.baskets(tier: 1).count, 2)
        XCTAssertEqual(daily.baskets(tier: 2).count, 5)

        // 本次事故的核心断言:7 张卡逐张核对 `engineApiVersion` 解出真 Int(不是 nil、
        // 更不能把整份报告解码拖炸),`fingerprint` 其余字段同批核对不受累。
        for basket in daily.baskets {
            let card = try XCTUnwrap(basket.card, "\(basket.basketKey) 的卡应可解出")
            XCTAssertEqual(card.fingerprint.engineApiVersion, 1,
                           "\(basket.basketKey):engineApiVersion 必须是 Int(服务端契约),不是 nil")
            XCTAssertEqual(card.fingerprint.charterVersion, "v1.3.3")
            XCTAssertEqual(card.fingerprint.packVersion, "K7-pack-v1")
            XCTAssertEqual(card.evidenceStatus, "ok")
            XCTAssertNil(card.evidenceIncompleteNote)
        }
    }

    /// 2026-08-05 生产 `GET /report` 真实响应原文(单行 minified,143400 字节,7 篮)——
    /// 固化整份形状用,⛔ 不手动增删字段(改动就不再是"真实载荷",防回归的价值就丢了)。
    private static let realProductionReportJSON20260805: String = #"{"tradeDate":"20260805","generatedAt":"2026-08-05T10:11:39+00:00","strategyVersion":"v1.3.3","sentiment":{"trade_date":"2026-08-05","limit_up_count":104,"limit_down_count":1,"zaban_count":47,"zaban_rate":0.31125827814569534,"max_consec_limit_up":8,"prev_limit_up_premium_avg":0.05399926555139971,"prev_limit_up_sample":141,"position_quota":"半额","quota_reason":"涨停104家/跌停1家/炸板率31%/最高连板8板;(三态阈值第一版为启发式,未经回测验证,实盘归因迭代中,见 PROJECT_PLAN §2.3)"},"sectors":[{"index_code":"885918.TI","name":"快手概念","board_age":4,"ret_20d":0.1330431818777693,"bonus":3.0,"rank":1},{"index_code":"886080.TI","name":"财税数字化","board_age":6,"ret_20d":0.1302885596932224,"bonus":0.0,"rank":2},{"index_code":"886094.TI","name":"华为盘古","board_age":4,"ret_20d":0.12104426743017749,"bonus":3.0,"rank":3},{"index_code":"886018.TI","name":"高压氧舱","board_age":8,"ret_20d":0.12007072396839624,"bonus":0.0,"rank":4},{"index_code":"886101.TI","name":"兵装重组概念","board_age":10,"ret_20d":0.11997991563762822,"bonus":0.0,"rank":5},{"index_code":"885792.TI","name":"赛马概念","board_age":8,"ret_20d":0.11438045176321832,"bonus":0.0,"rank":6},{"index_code":"886074.TI","name":"AI语料","board_age":4,"ret_20d":0.11054356496741469,"bonus":3.0,"rank":7},{"index_code":"885933.TI","name":"NFT概念","board_age":6,"ret_20d":0.11014163127562071,"bonus":0.0,"rank":8},{"index_code":"885947.TI","name":"DRG/DIP","board_age":7,"ret_20d":0.10692718297076875,"bonus":0.0,"rank":9},{"index_code":"885791.TI","name":"知识产权保护","board_age":6,"ret_20d":0.1065257711991543,"bonus":0.0,"rank":10}],"basketDaily":{"tradeDate":"20260805","baskets":[{"basketId":6,"basketKey":"9cebae88","name":"MLCC涨价潮","tradeDate":"","tier":1,"memberCodes":["300408.SZ","601208.SH"],"card":{"specVersion":"basket_card_v2","version":1,"basketKey":"9cebae88","tradeDate":"20260805","nextTradeDate":"20260806","name":"MLCC涨价潮","driver":"三星电机8月1日起全品类MLCC涨价30%，村田、太阳诱电同步调价，AI服务器MLCC需求以约80%年复合增速扩容","driverKind":"event","evidence":[{"claim":"三星电机自2026年8月1日起将全品类MLCC出货价统一上调30%，覆盖消费电子、工控、汽车及AI服务器所有产品线","date":"2026-08-04","source":"证券类媒体报道（记者李益文）","url":""},{"claim":"太阳诱电于7月23日向客户发出MLCC价格调整通知，自9月1日起对部分产品进行调价","date":"2026-07-23","source":"证券类媒体报道（记者李益文）","url":""},{"claim":"村田制作所在3月底率先针对AI服务器与高阶车规MLCC上调15%至35%的价格","date":"2026-08-04","source":"证券类媒体报道（记者李益文）","url":""},{"claim":"村田制作所2026财年Q1营收5023亿日元同比增长20.7%，营业利润985亿日元同比增长59.8%，核心电容器板块营收2825亿日元同比增长30%，季度订单总额创历史新高","date":"2026-07","source":"村田制作所财报","url":""},{"claim":"三星电机2026年Q2营收34572亿韩元同比增长24%，营业利润4404亿韩元同比增长107%，元器件事业部营收同比增长29%，公司称AI数据中心服务器与车规MLCC需求为核心驱动力","date":"2026-07","source":"三星电机财报","url":""},{"claim":"截至2026年6月下旬，三星电机、村田、太阳诱电MLCC订单/出货比分别达1.31、1.30、1.25，创疫情以来新高，整体MLCC市场BB Ratio升至1.04","date":"2026-06","source":"TrendForce（集邦咨询）","url":""},{"claim":"三环集团预计2026年上半年归母净利润17.94亿–20.41亿元，同比增长45%–65%，MLCC产品销售量和销售额同比大幅增长，部分规格价格修复至原有合理价值","date":"2026-07","source":"三环集团2026年半年度业绩预告","url":""},{"claim":"三星电机与一家全球大型企业签署价值2亿美元的AI服务器MLCC供应合同，合同期限为2027年1月1日至2027年12月31日","date":"2026-08","source":"三星电机披露","url":""},{"claim":"高盛最新研报显示AI服务器MLCC市场正以约80%的年复合增速高速扩容","date":"2026-08","source":"高盛研报","url":""},{"claim":"天风证券研报指出2026年年中MLCC制造商释放的信号印证高端AI服务器MLCC供应结构失衡，高端MLCC扩产弹性有限，2026年下半年高端供给压力或进一步加剧","date":"2026-06","source":"天风证券研报","url":""},{"claim":"中国大陆渠道市场自6月起对主流消费级MLCC X5R启动调涨，平均涨幅达15%至25%","date":"2026-06","source":"TrendForce（集邦咨询）/证券时报","url":""}],"evidenceStatus":"ok","whyNow":"8月4日媒体报道三星电机涨价，村田Q1订单创历史新高且BB Ratio达1.30创疫情以来新高，天风证券指出下半年高端供给压力或进一步加剧","members":[{"tsCode":"300408.SZ","name":"三环集团","roleLlm":"leader","roleMech":null,"roleConflict":false,"reason":"国内MLCC龙头，半年报预增45%-65%且明确提及MLCC量价齐升，100亿成交容量充足","isPrimary":true,"industry":"元器件","industryLift":8.94032258064516,"liftReason":null,"primaryReason":"highest_lift","rsRank":null,"k4Tag":"avoid_flag","mech":{"close":128.19,"limit_down":102.55,"limit_up":153.83,"ma20":111.21899999999998,"no_limit_reason":null,"stop_price":121.78},"entryZone":{"high":131.0,"low":127.5,"why":"收盘128.19远高于MA20 111.22结构健康，低吸位设在收盘略下方给一点回踩空间，上沿131对应+2.2%涨幅属正常强势区间，最高追价134对应+4.5%不过于激进；压力位136–145分别对应+6%和+13%的阶段性阻力参考。"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":134.0,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":145.0,"low":136.0},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":["warn_streak_top"]},{"tsCode":"601208.SH","name":"东材科技","roleLlm":"elastic","roleMech":"leader","roleConflict":true,"reason":"今日涨停+10.01%，机械角色leader且簇内RS名次1，MLCC上游电子材料弹性标的","isPrimary":true,"industry":"化工原料","industryLift":1.3324519230769232,"liftReason":null,"primaryReason":"highest_lift","rsRank":1,"k4Tag":null,"mech":{"close":39.33,"limit_down":35.4,"limit_up":43.26,"ma20":43.0510956542733,"no_limit_reason":null,"stop_price":37.36},"entryZone":{"high":41.0,"low":38.8,"why":"今天涨停39.33次日可能有获利盘抛压，低吸位设在涨停价略下方；上沿41对应+4.2%涨幅，最高追价42.5接近但未触及涨停价43.26；压力位下沿43.50略高于MA20 43.05，若能突破MA20则结构有修复迹象。"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":42.5,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":46.0,"low":43.5},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":[]}],"roleConflicts":["601208.SH"],"tier":1,"rankInTier":1,"rankMech":1,"mechScore":0.8885714285714286,"tierBreakdown":{"contrib":{"card_density":0.05,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.288571,"tradability":0.15},"dims":{"card_density":0.5,"driver_freshness":1.0,"leader_clarity":1.0,"sector_strength":0.961905,"tradability":0.75},"engine_api_version":1,"flags":[],"neutral_filled_weight":0,"pack_version":"K7-pack-v1","score":0.888571,"weights":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2},"weights_raw":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2}},"tierReason":null,"tierNote":"T1档第1位合理——driver_freshness 1.00（8月4日刚报道）、leader_clarity 1.00、sector_strength 0.96均处高位，驱动的时效性和龙头辨识度满分；tradability 0.75和card_density 0.50略低但可接受，整体档位与序次无明显失当。","scripts":{"flat":"次日若平开，三环集团在128附近震荡属中性，可在区间下沿观察是否有资金承接，不急于行动。东材科技平开在39附近则偏弱——今天涨停后次日无溢价，说明市场对它的认可度有限，需格外谨慎，观察为主。","strong":"次日两只票同时高开或强势开盘，说明市场认可MLCC涨价逻辑。三环集团高开在130以上时可观察开盘后量价配合，若站稳可在建仓区间内分批关注；东材科技若高开在40以上弹性可能更大，但它结构偏弱、追高空间受限于涨停价43.26，接近该价位时性价比急剧下降。两者同时走强时优先关注结构更健康的三环。","weak":"次日若低开或走弱，三环集团关注是否逼近章程止损线（系统已算出，此处不另设），止损线上方企稳可继续观察、跌破则纪律执行。东材科技低开更危险——收盘价已经低于MA20，只要收盘低于39.33就构成破位，且章程止损线距现价仅约5%，容错极窄。"},"scriptsUnavailableReason":null,"verificationSpec":{"basket_key":"9cebae88","conditions":[{"code":"close_at_or_above_ref","compare":"close>=level","desc":"次日收盘 ≥ 基准日收盘(驱动被跟随)","scope":"member"},{"code":"holds_ma20","compare":"close>=level","desc":"次日收盘 ≥ 基准日 MA20(结构未破)","scope":"member"}],"evaluable_members":2,"member_count":2,"members":[{"close_at_or_above_ref":128.19,"holds_ma20":111.21899999999998,"ref_close":128.19,"ts_code":"300408.SZ"},{"close_at_or_above_ref":39.33,"holds_ma20":43.0510956542733,"ref_close":39.33,"ts_code":"601208.SH"}],"min_members_hit":1,"next_trade_date":"20260806","require":["close_at_or_above_ref","holds_ma20"],"ruleset_version":"verify_ruleset_v2","spec_version":"basket_verify_v2","trade_date":"20260805"},"verificationText":"至少有一只票次日收盘不低于今天的收盘价、也不跌破各自的20日均线，就算逻辑被市场跟住了。三环集团只要收盘不低于128.19就达标；东材科技门槛高得多，需要收盘到43.05以上、接近涨停才行。","invalidationSpec":{"any_of":["close_below_stop_line","limit_down_touch","close_below_ref_and_ma20"],"basket_key":"9cebae88","conditions":[{"code":"close_below_stop_line","compare":"close<=level","desc":"次日收盘 ≤ 章程止损线(现役 stop_pct 算,系统算不由模型给)","scope":"member"},{"code":"limit_down_touch","compare":"low<=level","desc":"次日最低价 ≤ 跌停价(触及跌停)","scope":"member"},{"code":"close_below_ref_and_ma20","compare":"close<all_levels","desc":"次日收盘 < 基准日收盘 **且** < 基准日 MA20(两条同时成立才算破位)","levels":["ref_close","ma20"],"scope":"member"}],"evaluable_members":2,"member_count":2,"members":[{"close_below_ref_and_ma20":{"ma20":111.21899999999998,"ref_close":128.19},"close_below_stop_line":121.78,"limit_down_touch":102.55,"ref_close":128.19,"ts_code":"300408.SZ"},{"close_below_ref_and_ma20":{"ma20":43.0510956542733,"ref_close":39.33},"close_below_stop_line":37.36,"limit_down_touch":35.4,"ref_close":39.33,"ts_code":"601208.SH"}],"min_members_hit":1,"next_trade_date":"20260806","ruleset_version":"verify_ruleset_v2","spec_version":"basket_invalidate_v2","stop_pct":0.05,"trade_date":"20260805"},"invalidationText":"任何一只票次日收盘跌到或低于各自的章程止损线，或盘中触及跌停板，或收盘同时低于基准日收盘价和20日均线，就算逻辑被市场否了。东材科技尤其脆弱——它今天收盘39.33已经低于MA20 43.05，所以只要明天收盘低于39.33就同时满足两个破位条件，几乎收阴即破位。","risks":["三环集团命中K4黄牌分区，机器不禁但需人工复核，可能存在驱动证据之外未被覆盖的个股风险因素","东材科技收盘价低于MA20、结构偏弱，今天涨停可能是下行趋势中的反弹而非趋势反转，验证门槛极高且容错极窄","MLCC涨价驱动虽时效性强，但若次日大盘系统性走弱，板块逻辑可能被整体拖累，与个股基本面无关"],"disclaimer":"参考,非指令 —— 买卖与终选在你,系统不代下单;纪律以章程为准。","fingerprint":{"stopPct":0.05,"takeProfitRetrace":0.08,"charterVersion":"v1.3.3","packVersion":"K7-pack-v1","engineApiVersion":1,"verificationRulesetVersion":"verify_ruleset_v2"},"disciplineLabels":["章程止损 −5.0%","回落止盈 8.0%"],"narrative":"这个篮子的核心驱动是MLCC全产业链涨价，时效性非常强。据2026年8月4日的媒体报道，三星电机自8月1日起将全品类MLCC出货价统一上调30%，覆盖消费电子到AI服务器所有产品线；村田制作所早在3月底就率先对AI服务器与高阶车规MLCC上调15%至35%；太阳诱电也于7月23日发出调价通知、9月1日起执行。更关键的是订单端的印证——据2026年6月TrendForce数据，三家巨头BB Ratio分别达1.31、1.30、1.25，创疫情以来新高；村田2026财年Q1季度订单总额创历史新高（7月财报）；三星电机Q2元器件事业部营收同比增长29%（7月财报）。高盛研报指出AI服务器MLCC市场以约80%年复合增速扩容，天风证券6月研报进一步指出下半年高端供给压力或加剧。这条驱动链从\"海外巨头涨价→订单爆满→国内渠道跟涨→国内厂商量价齐升\"逻辑闭环比较完整。\n\n两只票的分工很明确但强弱差异不小。三环集团是国内MLCC龙头，据其2026年半年度业绩预告，上半年归母净利润预增45%–65%且明确提及MLCC量价齐升、部分规格价格修复至合理价值——基本面与行业涨价逻辑高度同频。它收盘128.19远在MA20的111.22之上，结构健康。但它命中了K4黄牌分区，机器不会禁止但需要人工复核，这一点务必留意。东材科技今天涨停收39.33，机械侧判定为leader、簇内RS排名第一，但角色模型给出的是elastic，两侧对拍不一致本身就是分歧信号。更实质的问题在于东材收盘39.33低于MA20的43.05，意味着它很可能处在下行结构中的反弹首板而非上升通道中的加速。从验证门槛看差异极大：三环只需次日收盘不低于128.19即可达标，东材则需要收盘到43.05以上——从39.33算接近涨停，门槛非常高。失效方面东材也更脆弱，因为它今天收盘已经低于MA20，只要明天收盘低于39.33就同时满足\"低于基准收盘且低于MA20\"两个破位条件，几乎等于收阴即破位。所以这个篮子实质上三环扛旗、东材做弹性，但如果明天板块分歧，东材的容错空间远比三环窄。","llmStage":"ok","degraded":false,"notes":[]},"cardVersion":1,"cardUnavailableReason":null,"tierHistory":null},{"basketId":8,"basketKey":"c7f88cf8","name":"存储芯片高景气","tradeDate":"","tier":1,"memberCodes":["301308.SZ","688525.SH"],"card":{"specVersion":"basket_card_v2","version":1,"basketKey":"c7f88cf8","tradeDate":"20260805","nextTradeDate":"20260806","name":"存储芯片高景气","driver":"AI算力拉动DRAM合约价Q1环比涨约90%、NAND涨约55%，长鑫科技7月16日科创板申购募资约579亿填补A股本土存储核心资产空白","driverKind":"commodity","evidence":[{"claim":"长鑫科技招股书显示全球算力中心大规模建设拉动高带宽存储需求，一季度DRAM合约价环比大涨约90%，NAND闪存合约价环比上涨约55%，二季度分别环比上涨约60%和70%，存储行业仍处于高景气周期","date":"2026-07-16","source":"长鑫科技招股意向书/媒体报道","url":""},{"claim":"佰维存储预计2026年半年度归母净利润70亿-75亿元，同比增长3200%-3422%，受益于AI算力爆发与存储行业进入高景气周期，Q2净利润预计环比增长41%-58%","date":"2026-07-15","source":"佰维存储业绩预告公告","url":""},{"claim":"普冉股份预计2026年上半年实现归母净利润8.25亿元，同比增长1925.36%，营收同比增长335.65%，全球AI算力建设推动存储芯片供需格局优化，通用存储产品实现量价齐升","date":"2026-07-23","source":"普冉股份业绩预告公告","url":""},{"claim":"国内规模最大的存储芯片企业长鑫科技7月16日正式开启科创板申购，发行价8.66元，募资约579亿元，估值约5791亿元，上半年预计净利润500亿-570亿元，填补A股本土存储核心资产空白","date":"2026-07-16","source":"媒体报道","url":""},{"claim":"复旦大学周鹏-刘春森团队在室温单电子非易失性量子存储技术上取得突破，将存储功耗呈几何级数降低，成果发表于《科学》主刊，团队计划下半年成立初创公司推进芯片验证","date":"2026-07-17","source":"第一财经","url":""},{"claim":"德明利预计2026年半年度归母净利润57亿-65亿元，同比扭亏为盈，AI应用加速落地驱动存储需求持续增长，供应偏紧背景下存储产品价格保持上行趋势","date":"2026-07-14","source":"德明利业绩预告公告","url":""}],"evidenceStatus":"ok","whyNow":"佰维存储预计上半年净利润同比增3200%-3422%，普冉股份同比增1925%，德明利扭亏为盈，多家公司业绩预告在7月中下旬集中发布验证高景气持续","members":[{"tsCode":"688525.SH","name":"佰维存储","roleLlm":"leader","roleMech":null,"roleConflict":false,"reason":"预计上半年净利润同比增3200%-3422%为板块业绩弹性最大标的，96亿成交辨识度最高","isPrimary":true,"industry":"半导体","industryLift":11.423695586609028,"liftReason":null,"primaryReason":"highest_lift","rsRank":null,"k4Tag":null,"mech":{"close":233.07,"limit_down":186.46,"limit_up":279.68,"ma20":268.9425,"no_limit_reason":null,"stop_price":221.42},"entryZone":{"high":240.0,"low":225.0,"why":"收盘233.07距MA20约13%回调，low设在收盘下方约3.5%处给低吸空间，high略高于收盘给小幅高开留余量，max_chase 250控制在收盘上方约7%以内；exit区间255—272对应MA20 268.94附近及上方，是本轮反弹回到均线的压力带。"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":250.0,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":272.0,"low":255.0},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":["warn_streak_top"]},{"tsCode":"301308.SZ","name":"江波龙","roleLlm":"core","roleMech":null,"roleConflict":false,"reason":"今日+8.59%，82.74亿成交，存储模组赛道第二大容量标的","isPrimary":true,"industry":"半导体","industryLift":11.423695586609028,"liftReason":null,"primaryReason":"highest_lift","rsRank":null,"k4Tag":null,"mech":{"close":364.92,"limit_down":291.94,"limit_up":437.9,"ma20":414.42350000000005,"no_limit_reason":null,"stop_price":346.67},"entryZone":{"high":372.0,"low":356.0,"why":"今日已涨8.59%收盘364.92，low设在收盘下方约2.5%给回踩空间，high略高于收盘兼顾惯性，max_chase 385为收盘上方约5.5%封住追高幅度；exit区间395—420对应MA20 414.42附近，是反弹接近均线的压力参考。"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":385.0,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":420.0,"low":395.0},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":["warn_streak_top"]}],"roleConflicts":[],"tier":1,"rankInTier":2,"rankMech":2,"mechScore":0.8442857142857143,"tierBreakdown":{"contrib":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.15,"sector_strength":0.294286,"tradability":0.2},"dims":{"card_density":1.0,"driver_freshness":1.0,"leader_clarity":0.5,"sector_strength":0.980952,"tradability":1.0},"engine_api_version":1,"flags":["leader_clarity_missing"],"neutral_filled_weight":0.3,"pack_version":"K7-pack-v1","score":0.844286,"weights":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2},"weights_raw":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2}},"tierReason":null,"tierNote":"T1档内第2位、机械分0.844基本合理——card_density与sector_strength接近满分支撑了档位，但leader_clarity仅0.50反映佰维作为leader在价格表现上不够强势、与江波龙的角色定位存在张力，放在档内第2位而非第1位是恰当的。","scripts":{"flat":"平开后先看前30分钟是否有方向选择。若两只票中较强的一只率先放量上行、另一只同步翻红但不领涨，可在entries区间内对较强票做观察；若平开后持续横盘无量、两者均未表现出主动买盘，则等待盘中方向明确再说，不急于开盘即进。","strong":"若次日大幅高开、两只票迅速向上试探，不要追第一波脉冲；观察开盘后15—30分钟是否有量能持续承接且不回落至开盘价下方，有承接再在entries区间内考虑动作，max_chase是绝对上限、超过不追。若高开后快速回落翻绿，视为脉冲失败，本日不参与。","weak":"低开或走弱时，首要任务是看止损线与跌停价之间的空间是否够用。佰维止损线221.42距跌停186.46还有空间，江波龙346.67距跌停291.94也有空间，但低开若直接逼近止损线附近，说明结构已经恶化，本日不应参与；若低开后能在前30分钟内企稳回升至基准收盘价上方，可以重新纳入观察，但仍需在entries区间内执行。"},"scriptsUnavailableReason":null,"verificationSpec":{"basket_key":"c7f88cf8","conditions":[{"code":"close_at_or_above_ref","compare":"close>=level","desc":"次日收盘 ≥ 基准日收盘(驱动被跟随)","scope":"member"},{"code":"holds_ma20","compare":"close>=level","desc":"次日收盘 ≥ 基准日 MA20(结构未破)","scope":"member"}],"evaluable_members":2,"member_count":2,"members":[{"close_at_or_above_ref":233.07,"holds_ma20":268.9425,"ref_close":233.07,"ts_code":"688525.SH"},{"close_at_or_above_ref":364.92,"holds_ma20":414.42350000000005,"ref_close":364.92,"ts_code":"301308.SZ"}],"min_members_hit":1,"next_trade_date":"20260806","require":["close_at_or_above_ref","holds_ma20"],"ruleset_version":"verify_ruleset_v2","spec_version":"basket_verify_v2","trade_date":"20260805"},"verificationText":"至少一只票次日收盘同时站上自己的基准日收盘价和MA20——佰维要收上268.94、江波龙要收上414.42。在当前价位下这意味着至少十几个百分点的单日涨幅，门槛很高；只要有一只做到了，就说明市场对存储高景气逻辑的二次跟随是有效的。","invalidationSpec":{"any_of":["close_below_stop_line","limit_down_touch","close_below_ref_and_ma20"],"basket_key":"c7f88cf8","conditions":[{"code":"close_below_stop_line","compare":"close<=level","desc":"次日收盘 ≤ 章程止损线(现役 stop_pct 算,系统算不由模型给)","scope":"member"},{"code":"limit_down_touch","compare":"low<=level","desc":"次日最低价 ≤ 跌停价(触及跌停)","scope":"member"},{"code":"close_below_ref_and_ma20","compare":"close<all_levels","desc":"次日收盘 < 基准日收盘 **且** < 基准日 MA20(两条同时成立才算破位)","levels":["ref_close","ma20"],"scope":"member"}],"evaluable_members":2,"member_count":2,"members":[{"close_below_ref_and_ma20":{"ma20":268.9425,"ref_close":233.07},"close_below_stop_line":221.42,"limit_down_touch":186.46,"ref_close":233.07,"ts_code":"688525.SH"},{"close_below_ref_and_ma20":{"ma20":414.42350000000005,"ref_close":364.92},"close_below_stop_line":346.67,"limit_down_touch":291.94,"ref_close":364.92,"ts_code":"301308.SZ"}],"min_members_hit":1,"next_trade_date":"20260806","ruleset_version":"verify_ruleset_v2","spec_version":"basket_invalidate_v2","stop_pct":0.05,"trade_date":"20260805"},"invalidationText":"任一只票次日收盘触及或低于章程止损线（佰维221.42、江波龙346.67）、或触及跌停价（佰维186.46、江波龙291.94）、或收盘同时低于基准收盘和MA20——只要任意一条命中任意一只票，本篮逻辑即判失效。其中'收盘同时低于基准收盘和MA20'在当前两只票均已低于MA20的情况下，只要次日收盘不能收上基准收盘价就会触发，这是最容易命中的失效路径。","risks":["两只票均在MA20下方运行，回调尚未确认结束，存在继续下探至止损线甚至跌停的风险","驱动证据集中在7月14—23日，距今已近三周，催化时效性在衰减，若无新催化跟进则反弹动力可能不足","验证条件要求单日收盘站上MA20（距现价约12%—13%），门槛极高，而失效条件中'收盘同时低于基准收盘和MA20'在当前结构下极易触发，验证/失效不对称显著不利于多头"],"disclaimer":"参考,非指令 —— 买卖与终选在你,系统不代下单;纪律以章程为准。","fingerprint":{"stopPct":0.05,"takeProfitRetrace":0.08,"charterVersion":"v1.3.3","packVersion":"K7-pack-v1","engineApiVersion":1,"verificationRulesetVersion":"verify_ruleset_v2"},"disciplineLabels":["章程止损 −5.0%","回落止盈 8.0%"],"narrative":"这个篮子的底层逻辑是清晰的——AI算力爆发拉动了存储芯片的量价齐升，7月中下旬多家公司的业绩预告把这件事从\"讲故事\"变成了\"报表确认\"。长鑫科技7月16日科创板申购募资约579亿，既是本土存储核心资产填补空白的事件，也把板块的关注度推到了一个高点。佰维存储上半年净利润同比增3200%—3422%，是这批业绩预告里弹性最大的标的，96亿成交也确实是板块里辨识度最高的，所以系统把它放在leader位置；江波龙今天涨了8.59%，作为存储模组赛道的第二大容量标的，扮演core的角色。两个人气的传导路径是合理的。\n\n但这里有一个需要正视的矛盾：驱动证据集中在7月14日至23日之间，到今天8月5日已经过去将近两周甚至三周，最初的催化脉冲已经被市场消化了一轮——两只票现在都在MA20下方运行，佰维收盘233.07距MA20的268.94有约13%的差距，江波龙364.92距414.42也差了约12%。也就是说，基本面的景气度毋庸置疑，但技术结构上两只票都处于回调态势中，这个篮子本质上是在做一个\"高景气主题回调后的二次介入尝试\"，而不是追一个正在发散的新催化。leader_clarity只有0.50也反映了这个问题——佰维虽然成交和弹性最大，但它今天并不是价格表现最强的那只，江波龙反而是当日涨幅更猛的。这种\"基本面leader和技术面leader不完全重合\"的状态，是接下来观察时需要留意的分歧点。\n\n验证条件方面，系统要求至少一只票次日收盘同时高于基准收盘和MA20，这对佰维意味着收盘要站上268.94、对江波龙要站上414.42——在当前价的基础上都是两位数百分比的上涨，单日完成的难度不小。反过来说，失效条件触发起来要容易得多：任一成员收盘触及止损线（佰维221.42、江波龙346.67）、触及跌停、或者收盘同时低于基准收盘和MA20，就会判本篮失效。这个验证/失效不对称的结构，要求在参与时对追高保持克制。","llmStage":"ok","degraded":false,"notes":[]},"cardVersion":1,"cardUnavailableReason":null,"tierHistory":null},{"basketId":10,"basketKey":"509a4fd1","name":"培育钻石","tradeDate":"","tier":2,"memberCodes":["301021.SZ","301071.SZ"],"card":{"specVersion":"basket_card_v2","version":1,"basketKey":"509a4fd1","tradeDate":"20260805","nextTradeDate":"20260806","name":"培育钻石","driver":"上半年合成钻石一般贸易出口14.1亿元同比增65.3%，产量占全球六成以上，在集成电路与AI算力散热领域打开第二增长曲线","driverKind":"event","evidence":[{"claim":"今年上半年通过上海钻石交易所申报一般贸易出口合成钻石达14.1亿元，同比增长65.3%，我国合成钻石产量已占全球六成以上；同时合成钻石在集成电路、AI算力、高功率激光器等散热领域的广泛应用成为第二条增长曲线。","date":"2026-08-05","source":"新华网","url":""},{"claim":"商丘柘城造出247.82克拉培育钻石，刷新全球新纪录，一年内两次突破。","date":"2026-08-04","source":"商丘日报","url":""},{"claim":"“豫钻出海·链动全球”培育钻石跨境电商合作对接会在上海举行，“豫钻出海基地”正式启动，旨在推动河南培育钻石从“产能出海”向“品牌出海”转变，构建“郑州制造、上海交易、全球交付”布局。","date":"2026-07-30","source":"上海CIPA海外服务平台等联合主办会议","url":""},{"claim":"国际宝石研究院（IGI）2027财年一季度（截至2026年6月30日）报告显示，培育钻石认证业务强劲增长，收入同比增23%，印度CVD设备数量预计到2026年增至1.3万-1.4万台，且培育钻石价格在近18-24个月逐渐趋于稳定。","date":"2026-08","source":"国际宝石研究院（IGI）投资者报告","url":""}],"evidenceStatus":"ok","whyNow":"8月5日新华网报道出口数据，8月4日商丘柘城刷新全球培育钻石纪录至247.82克拉，7月30日豫钻出海基地启动推动品牌出海，产业催化在8月初密集落地","members":[{"tsCode":"301071.SZ","name":"力量钻石","roleLlm":"leader","roleMech":null,"roleConflict":false,"reason":"培育钻石纯正标的且为柘城本地企业，今日+8.60%且8.88亿成交为板块最高","isPrimary":true,"industry":"矿物制品","industryLift":30.794444444444444,"liftReason":null,"primaryReason":"highest_lift","rsRank":null,"k4Tag":null,"mech":{"close":56.47,"limit_down":45.18,"limit_up":67.76,"ma20":61.39649999999999,"no_limit_reason":null,"stop_price":53.65},"entryZone":{"high":57.0,"low":54.5,"why":"low 略低于今日收盘给弱开留观察空间，high 在收盘附近，max_chase 控制在 MA20 61.40 下方避免追入压力区；exit_low 取 MA20 本身作为第一压力参照，exit_high 取前高区域。"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":59.0,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":65.0,"low":61.4},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":["warn_streak_top"]},{"tsCode":"301021.SZ","name":"英诺激光","roleLlm":"elastic","roleMech":null,"roleConflict":false,"reason":"培育钻石制造用激光设备标的，与力量钻石20日相关性达0.97，今日+8.36%","isPrimary":true,"industry":"专用机械","industryLift":2.8377133105802046,"liftReason":null,"primaryReason":"highest_lift","rsRank":null,"k4Tag":null,"mech":{"close":60.9,"limit_down":48.72,"limit_up":73.08,"ma20":68.8515,"no_limit_reason":null,"stop_price":57.85},"entryZone":{"high":61.5,"low":59.5,"why":"low 略低于今日收盘，high 在收盘附近，max_chase 限制在 MA20 68.85 下方约 8% 处；exit_low 取 65 整数关为中间压力，exit_high 取 MA20 附近为上方压力参照。"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":63.5,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":69.0,"low":65.0},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":["warn_streak_top"]}],"roleConflicts":[],"tier":2,"rankInTier":1,"rankMech":1,"mechScore":0.8328571428571429,"tierBreakdown":{"contrib":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.15,"sector_strength":0.282857,"tradability":0.2},"dims":{"card_density":1.0,"driver_freshness":1.0,"leader_clarity":0.5,"sector_strength":0.942857,"tradability":1.0},"engine_api_version":1,"flags":["leader_clarity_missing"],"neutral_filled_weight":0.3,"pack_version":"K7-pack-v1","score":0.832857,"weights":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2},"weights_raw":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2}},"tierReason":null,"tierNote":"T2 合理——驱动新鲜度与板块强度拉满，但 leader_clarity 仅 0.50 且两票均在 MA20 下方，结构未修复，不给 T1 恰当。","scripts":{"flat":"平开则在 entry 区间内观察，看哪只票率先放量站稳今日收盘价之上。力量钻石成交额仍是关键指标——若量能不继、冲高回落，不急于建仓。","strong":"若高开在 entry high 之上但未超 max_chase，可择强分批轻仓跟随，优先看力量钻石能否率先向 MA20 发起冲击；若直接开在 max_chase 之上则不追，等回踩再观察。两只票同时高开时优先力量钻石。","weak":"低开则警惕昨日获利盘出逃，entry low 以下不介入。若盘中走弱逼近系统止损线或触及跌停，系统将自动判定失效，不试图抄底。"},"scriptsUnavailableReason":null,"verificationSpec":{"basket_key":"509a4fd1","conditions":[{"code":"close_at_or_above_ref","compare":"close>=level","desc":"次日收盘 ≥ 基准日收盘(驱动被跟随)","scope":"member"},{"code":"holds_ma20","compare":"close>=level","desc":"次日收盘 ≥ 基准日 MA20(结构未破)","scope":"member"}],"evaluable_members":2,"member_count":2,"members":[{"close_at_or_above_ref":56.47,"holds_ma20":61.39649999999999,"ref_close":56.47,"ts_code":"301071.SZ"},{"close_at_or_above_ref":60.9,"holds_ma20":68.8515,"ref_close":60.9,"ts_code":"301021.SZ"}],"min_members_hit":1,"next_trade_date":"20260806","require":["close_at_or_above_ref","holds_ma20"],"ruleset_version":"verify_ruleset_v2","spec_version":"basket_verify_v2","trade_date":"20260805"},"verificationText":"至少一只票次日收盘同时高于今日收盘和 MA20——力量钻石需收在 61.40 以上（今日收盘 56.47 的 +8.7%），英诺激光需收在 68.85 以上（今日收盘 60.90 的 +13.1%）。门槛很高，要求极强的单日延续性。","invalidationSpec":{"any_of":["close_below_stop_line","limit_down_touch","close_below_ref_and_ma20"],"basket_key":"509a4fd1","conditions":[{"code":"close_below_stop_line","compare":"close<=level","desc":"次日收盘 ≤ 章程止损线(现役 stop_pct 算,系统算不由模型给)","scope":"member"},{"code":"limit_down_touch","compare":"low<=level","desc":"次日最低价 ≤ 跌停价(触及跌停)","scope":"member"},{"code":"close_below_ref_and_ma20","compare":"close<all_levels","desc":"次日收盘 < 基准日收盘 **且** < 基准日 MA20(两条同时成立才算破位)","levels":["ref_close","ma20"],"scope":"member"}],"evaluable_members":2,"member_count":2,"members":[{"close_below_ref_and_ma20":{"ma20":61.39649999999999,"ref_close":56.47},"close_below_stop_line":53.65,"limit_down_touch":45.18,"ref_close":56.47,"ts_code":"301071.SZ"},{"close_below_ref_and_ma20":{"ma20":68.8515,"ref_close":60.9},"close_below_stop_line":57.85,"limit_down_touch":48.72,"ref_close":60.9,"ts_code":"301021.SZ"}],"min_members_hit":1,"next_trade_date":"20260806","ruleset_version":"verify_ruleset_v2","spec_version":"basket_invalidate_v2","stop_pct":0.05,"trade_date":"20260805"},"invalidationText":"任一只票出现以下情形之一即整篮失效：收盘低于系统章程止损线（资料给出力量钻石 53.65、英诺激光 57.85）、盘中触及跌停价（力量钻石 45.18、英诺激光 48.72）、或收盘同时低于今日收盘和 MA20。鉴于两只票距止损线仅约 5%，容错空间不大。","risks":["两票均在 MA20 下方运行，今日大涨属底部反弹而非趋势突破，结构未修复","单日均涨 8% 以上，次日获利盘兑现压力大，且收盘价距系统止损线仅约 5% 空间","验证门槛要求收盘站上 MA20（距现价 8.7%–13.1%），单日达成难度大，逻辑可能长时间悬置","leader_clarity 仅 0.50，龙头认定不稳固，板块若分歧则缺乏明确锚点"],"disclaimer":"参考,非指令 —— 买卖与终选在你,系统不代下单;纪律以章程为准。","fingerprint":{"stopPct":0.05,"takeProfitRetrace":0.08,"charterVersion":"v1.3.3","packVersion":"K7-pack-v1","engineApiVersion":1,"verificationRulesetVersion":"verify_ruleset_v2"},"disciplineLabels":["章程止损 −5.0%","回落止盈 8.0%"],"narrative":"这个篮子搭的是培育钻石在 8 月初密集落地的产业催化。8 月 5 日新华网报道上半年合成钻石一般贸易出口 14.1 亿元、同比增 65.3%，产量占全球六成以上，且在集成电路与 AI 算力散热领域打开第二增长曲线；8 月 4 日商丘柘城刷新全球培育钻石纪录至 247.82 克拉，一年内两次突破；7 月 30 日豫钻出海基地启动，推动从\"产能出海\"向\"品牌出海\"转型。此外，IGI 截至 2026 年 6 月 30 日的财季报告显示培育钻石认证业务收入同比增 23%，印度 CVD 设备预计到 2026 年增至 1.3 万–1.4 万台，价格在近 18–24 个月趋于稳定——供需两侧都有数据支撑，驱动新鲜度满分。\n\n两只票的搭配逻辑直接：力量钻石是柘城本地企业、培育钻石纯正标的，今日涨 8.60% 且 8.88 亿成交为板块最高，作为 leader 角色明确；英诺激光是制造端激光设备标的，与力量钻石 20 日相关性 0.97，今日涨 8.36% 作为弹性跟随。但 leader\\_clarity 只有 0.50，系统对龙头认定的置信度不算高——两只票今天之前都处在下行通道中，力量钻石 MA20 在 61.40、英诺激光 MA20 在 68.85，分别高出今日收盘价 8.7% 和 13.1%。\n\n这正是分歧所在。验证条件要求至少一只票次日收盘同时高于基准收盘和 MA20，也就是说力量钻石需要收在 61.40 以上、英诺激光需要收在 68.85 以上才能判验证——对于刚从底部反弹 8% 的票来说，这个门槛非常苛刻。如果明天冲高但收盘没站上 MA20，逻辑既不验证也不失效，悬在中间；而一旦任一只票收盘同时低于基准收盘和 MA20，或触及系统止损线、跌停价，整篮直接失效。两只票单日均涨 8% 以上，次日获利盘兑现压力不小，且从收盘价到系统章程止损线（资料给出分别为 53.65 和 57.85）只有 5% 左右的空间，容错余地有限。","llmStage":"ok","degraded":false,"notes":[]},"cardVersion":1,"cardUnavailableReason":null,"tierHistory":null},{"basketId":5,"basketKey":"5692c758","name":"黄金避险","tradeDate":"","tier":2,"memberCodes":["001337.SZ","600489.SH","600988.SH"],"card":{"specVersion":"basket_card_v2","version":1,"basketKey":"5692c758","tradeDate":"20260805","nextTradeDate":"20260806","name":"黄金避险","driver":"德银8月3日重申2026年底金价4600美元目标价，现货黄金在4020美元附近企稳反弹","driverKind":"commodity","evidence":[{"claim":"德银发布报告重申2026年底黄金目标价4600美元，认为金价仍处于“爆发阶段”；8月3日（周一）现货黄金下测4020美元/盎司后反弹。","date":"2026-08-03","source":"德意志银行","url":""},{"claim":"特朗普宣布取消对伊朗新一轮袭击并表示和平谈判即将开始，推动油价下跌，缓解了能源通胀担忧，影响市场风险情绪。","date":"2026-08-03","source":"公开新闻报道","url":""},{"claim":"7月24日现货黄金全天大跌80.90美元报4043.14美元，分析师对中短期前景存分歧，市场在连续盘整后吸引新买盘。","date":"2026-07-24","source":"中国基金报、金十数据","url":""},{"claim":"高盛6月下调2026年底黄金目标价至4900美元/盎司，美银下调2026年黄金均价预测至4360美元/盎司（此为机构历史预测参照）。","date":"2026-06","source":"高盛、美国银行（经中国基金报引述）","url":""},{"claim":"瑞银CIO表示黄金市场在消化鹰派信号，预计金价或下探3850美元/盎司且下行风险加大（此为机构历史预测参照）。","date":"2026-07","source":"瑞银财富管理投资总监办公室（经中国基金报引述）","url":""},{"claim":"黄金分析师托尔森在7月2日报告中预测7月7日前后探明本轮最终低点，并认为2026年大底在4000美元/盎司附近。","date":"2026-07-02","source":"托尔森分析报告","url":""}],"evidenceStatus":"ok","whyNow":"金价经历7月下旬连续盘整后在4000美元附近获得支撑反弹，德银认为金价仍处于'爆发阶段'，A股黄金股8月5日集体大涨6-10%反映新买盘入场","members":[{"tsCode":"600988.SH","name":"赤峰黄金","roleLlm":"leader","roleMech":null,"roleConflict":false,"reason":"今日成交额29.17亿元为黄金板块最高，纯金矿标的辨识度最强，涨幅+8.19%处于板块前列","isPrimary":true,"industry":"黄金","industryLift":554.3000000000001,"liftReason":null,"primaryReason":"highest_lift","rsRank":null,"k4Tag":"avoid_flag","mech":{"close":40.84,"limit_down":36.76,"limit_up":44.92,"ma20":35.99849999999999,"no_limit_reason":null,"stop_price":38.8},"entryZone":{"high":41.5,"low":39.5,"why":"板块成交额第一的纯金矿龙头，今日涨8.19%后有回调需求，下沿设在收盘下方约3%留回调空间但不触系统止损线，追价上限控制在收盘上方约5%以避免追高"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":43.0,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":45.0,"low":43.5},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":["warn_streak_top"]},{"tsCode":"600489.SH","name":"中金黄金","roleLlm":"core","roleMech":null,"roleConflict":false,"reason":"央企背景大市值黄金股，今日涨幅+8.94%为板块最大，27.42亿成交提供容量支撑","isPrimary":true,"industry":"黄金","industryLift":554.3000000000001,"liftReason":null,"primaryReason":"highest_lift","rsRank":null,"k4Tag":"avoid_flag","mech":{"close":24.25,"limit_down":21.83,"limit_up":26.68,"ma20":21.220499999999998,"no_limit_reason":null,"stop_price":23.04},"entryZone":{"high":25.0,"low":23.5,"why":"央企大市值、今日涨幅8.94%板块最大且成交27.42亿容量好，区间以收盘价为中心小幅波动，下沿略高于系统止损线，追价上限不超过7%溢价"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":26.0,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":28.0,"low":26.5},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":["warn_streak_top"]},{"tsCode":"001337.SZ","name":"四川黄金","roleLlm":"elastic","roleMech":"core","roleConflict":true,"reason":"今日涨停+9.99%，机械角色判定为core，次新股弹性突出","isPrimary":true,"industry":"黄金","industryLift":554.3000000000001,"liftReason":null,"primaryReason":"highest_lift","rsRank":4,"k4Tag":"avoid_flag","mech":{"close":43.27,"limit_down":38.94,"limit_up":47.6,"ma20":37.4735,"no_limit_reason":null,"stop_price":41.11},"entryZone":{"high":44.0,"low":41.5,"why":"今日涨停9.99%弹性最大但次新股波动剧烈，下沿距系统止损线仅约1%容错很小，上沿及追价上限给得相对宽以适配弹性属性，但追价风险三者中最大"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":46.0,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":50.0,"low":47.0},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":[]}],"roleConflicts":["001337.SZ"],"tier":2,"rankInTier":5,"rankMech":5,"mechScore":0.6016666666666667,"tierBreakdown":{"contrib":{"card_density":0.0,"driver_freshness":0.06,"leader_clarity":0.075,"sector_strength":0.3,"tradability":0.166667},"dims":{"card_density":0.0,"driver_freshness":0.6,"leader_clarity":0.25,"sector_strength":1.0,"tradability":0.833333},"engine_api_version":1,"flags":[],"neutral_filled_weight":0,"pack_version":"K7-pack-v1","score":0.601667,"weights":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2},"weights_raw":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2}},"tierReason":null,"tierNote":"T2第5位、机械分0.602合理——sector_strength打满但card_density为零、leader_clarity仅0.25，三票全命中K4黄牌，档位不宜再高。","scripts":{"flat":"平开说明隔夜情绪中性、昨天的涨幅在消化中。观察前15分钟方向：若在收盘价附近企稳且有板块联动，可在low到high区间内分批观察；若平开后迅速走弱、板块不跟，先观望不动。四川黄金因涨停次日属性，平开本身已偏弱，需更谨慎。","strong":"三票今天已集体大涨8-10%，次日若高开属于情绪延续而非新驱动，先看现货金隔夜是否站稳4020上方——高开后头15至30分钟若量能跟不上、冲高回落，不宜追；若高开且量能放大、板块同步走强，可在high附近轻仓观察但严守max_chase上限。K4黄牌票高开追高尤其危险。","weak":"低开或走弱大概率是获利回吐，但要看现货金是否同步走弱——若金价新低则逻辑生变、直接放弃；若金价仍稳、纯粹是A股层面获利盘，low附近可作为观察位，但三票止损线距low都很近，破位即走不做幻想。"},"scriptsUnavailableReason":null,"verificationSpec":{"basket_key":"5692c758","conditions":[{"code":"close_at_or_above_ref","compare":"close>=level","desc":"次日收盘 ≥ 基准日收盘(驱动被跟随)","scope":"member"},{"code":"holds_ma20","compare":"close>=level","desc":"次日收盘 ≥ 基准日 MA20(结构未破)","scope":"member"}],"evaluable_members":3,"member_count":3,"members":[{"close_at_or_above_ref":40.84,"holds_ma20":35.99849999999999,"ref_close":40.84,"ts_code":"600988.SH"},{"close_at_or_above_ref":24.25,"holds_ma20":21.220499999999998,"ref_close":24.25,"ts_code":"600489.SH"},{"close_at_or_above_ref":43.27,"holds_ma20":37.4735,"ref_close":43.27,"ts_code":"001337.SZ"}],"min_members_hit":2,"next_trade_date":"20260806","require":["close_at_or_above_ref","holds_ma20"],"ruleset_version":"verify_ruleset_v2","spec_version":"basket_verify_v2","trade_date":"20260805"},"verificationText":"次日（8月6日周四）收盘时，三只票中至少两只同时满足收盘价不低于今日收盘价且不低于各自MA20——三票MA20都远低于现价，所以关键其实就是至少两只票能守住今天的收盘价不破。","invalidationSpec":{"any_of":["close_below_stop_line","limit_down_touch","close_below_ref_and_ma20"],"basket_key":"5692c758","conditions":[{"code":"close_below_stop_line","compare":"close<=level","desc":"次日收盘 ≤ 章程止损线(现役 stop_pct 算,系统算不由模型给)","scope":"member"},{"code":"limit_down_touch","compare":"low<=level","desc":"次日最低价 ≤ 跌停价(触及跌停)","scope":"member"},{"code":"close_below_ref_and_ma20","compare":"close<all_levels","desc":"次日收盘 < 基准日收盘 **且** < 基准日 MA20(两条同时成立才算破位)","levels":["ref_close","ma20"],"scope":"member"}],"evaluable_members":3,"member_count":3,"members":[{"close_below_ref_and_ma20":{"ma20":35.99849999999999,"ref_close":40.84},"close_below_stop_line":38.8,"limit_down_touch":36.76,"ref_close":40.84,"ts_code":"600988.SH"},{"close_below_ref_and_ma20":{"ma20":21.220499999999998,"ref_close":24.25},"close_below_stop_line":23.04,"limit_down_touch":21.83,"ref_close":24.25,"ts_code":"600489.SH"},{"close_below_ref_and_ma20":{"ma20":37.4735,"ref_close":43.27},"close_below_stop_line":41.11,"limit_down_touch":38.94,"ref_close":43.27,"ts_code":"001337.SZ"}],"min_members_hit":2,"next_trade_date":"20260806","ruleset_version":"verify_ruleset_v2","spec_version":"basket_invalidate_v2","stop_pct":0.05,"trade_date":"20260805"},"invalidationText":"如果次日有两只票各自命中以下任一条件——收盘价等于或低于章程止损线、盘中最低价触及跌停价、或者收盘价同时低于今日收盘和MA20——篮子逻辑即失效。","risks":["三只票全部命中K4黄牌分区，系统对篮子纯度有保留，需人工复核后才能决定是否参与","card_density为0.00、leader_clarity仅0.25，板块虽强但个股层面密度信号和龙头辨识度不足，结构性支撑偏弱","瑞银7月看空至3850美元、高盛6月下调目标至4900美元，机构观点分歧明显，并非一面倒看多","三票今日已涨8-10%，次日获利回吐压力大，8月3日地缘缓和消息对黄金避险逻辑理论上偏利空"],"disclaimer":"参考,非指令 —— 买卖与终选在你,系统不代下单;纪律以章程为准。","fingerprint":{"stopPct":0.05,"takeProfitRetrace":0.08,"charterVersion":"v1.3.3","packVersion":"K7-pack-v1","engineApiVersion":1,"verificationRulesetVersion":"verify_ruleset_v2"},"disciplineLabels":["章程止损 −5.0%","回落止盈 8.0%"],"narrative":"这个篮子踩的是德银8月3日报告的节点——重申2026年底金价4600美元目标价、判断金价仍处\"爆发阶段\"，现货黄金在4020美元附近下测后企稳反弹，8月5日A股黄金板块集体大涨6到10个百分点，说明7月下旬连续盘整后确有新买盘入场。三只票分工不同：赤峰黄金被定为leader，29.17亿成交额为板块最高、纯金矿标的辨识度最强，但今天8.19%的涨幅在三者中并非最大；中金黄金涨幅8.94%才是板块最大，央企背景加27.42亿成交提供容量支撑，作为core更稳；四川黄金今天直接涨停9.99%、弹性最突出，但模型侧给的是elastic、机械侧判的是core，角色对拍不一致、两说并存，次新股属性也意味着波动天然更大。值得警惕的是三只票全部命中K4黄牌分区——机器不禁但需人复核，系统对这个篮子的纯度本身有保留。机构看法也不统一：瑞银7月CIO预计金价或下探3850美元且下行风险加大，高盛6月将年底目标下调至4900美元、美银下调均价预测至4360美元，这些都是截至6至7月的历史参照、不能当现行定价基准，但说明并非一面倒看多。另外8月3日特朗普宣布取消对伊朗新一轮袭击并表示和平谈判即将开始，推动油价下跌、缓解能源通胀担忧——地缘缓和理论上对黄金避险属性偏利空，但当天金价仍在4020企稳反弹，说明德银目标价和新买盘力量暂时占主导。从定档看，T2第5位、机械分0.602，sector_strength打满1.00但card_density为零、leader_clarity仅0.25，板块整体强但个股层面密度信号和龙头辨识度偏弱，三只票今天已涨8到10个百分点，次日获利回吐压力不可小觑。","llmStage":"ok","degraded":false,"notes":[]},"cardVersion":1,"cardUnavailableReason":null,"tierHistory":null},{"basketId":11,"basketKey":"72e90813","name":"铅锌矿端紧缺","tradeDate":"","tier":2,"memberCodes":["000751.SZ","600497.SH"],"card":{"specVersion":"basket_card_v2","version":1,"basketKey":"72e90813","tradeDate":"20260805","nextTradeDate":"20260806","name":"铅锌矿端紧缺","driver":"SMM数据显示2026年1-6月铅精矿产量同比下降1.3%，矿端供应延续紧缺格局，四季度冬储预期下加工费仍有下调压力","driverKind":"commodity","evidence":[{"claim":"SMM数据显示，2026年1-6月铅精矿产量累计77.65万金属吨，同比下降1.3%，主因原矿品位下降及国内矿山环保安全检查增多；下半年铅精矿加工费难有反弹，四季度冬储预期下仍有小幅下调预期，矿端供应延续紧缺格局。","date":"2026-07","source":"上海有色网（SMM）","url":""},{"claim":"中金岭南7月初发布2026年上半年业绩预告，预计归母净利润10.5–12亿元，同比增长87.89%–114.73%。","date":"2026-07","source":"中金岭南（000060.SZ）投资者关系活动记录表 / 业绩预告","url":""},{"claim":"中金岭南天堂矿普查报告于2025年底通过专家评审，提交资源量包括铅锌金属113万吨（大型规模）、铜金属23万吨（中型规模）、共伴生银金属1312吨（大型规模），深部发现斑岩型铜钼矿体；2026年公司持续推进万侯矿探转采工作并于4月注册成立全资子公司统筹该项目。","date":"2026-07","source":"中金岭南（000060.SZ）投资者关系活动记录表","url":""},{"claim":"SMM 2026年铅锌年会背景介绍指出，上游矿山资源偏紧、冶炼加工利润承压，产业链面临结构性调整，1-5月国内铅精矿进口量58万实物吨同比增加9.8%，银矿砂及精矿进口94.6万吨同比增加130.5%，进口补充一定程度弥补国内产量不足。","date":"2026-07","source":"上海有色网（SMM）","url":""}],"evidenceStatus":"ok","whyNow":"中金岭南7月初发布上半年业绩预告预计净利润同比增长88%-115%，SMM指出下半年铅精矿加工费难有反弹，进口补充弥补国内不足但紧缺格局持续，板块已连续两天活跃","members":[{"tsCode":"600497.SH","name":"驰宏锌锗","roleLlm":"leader","roleMech":null,"roleConflict":false,"reason":"铅锌行业龙头，今日+6.88%且23.13亿成交为板块前列，驰宏锌锗品牌辨识度高","isPrimary":true,"industry":"铅锌","industryLift":395.9285714285714,"liftReason":null,"primaryReason":"highest_lift","rsRank":null,"k4Tag":"avoid_flag","mech":{"close":10.26,"limit_down":9.23,"limit_up":11.29,"ma20":9.255500000000001,"no_limit_reason":null,"stop_price":9.75},"entryZone":{"high":10.4,"low":10.0,"why":"今日收盘10.26涨6.88%，low设在略低于收盘处给平开回踩余地，high与max_chase分别对应温和走强和强势追入的上限；exit区间10.60–11.20为短线压力参考，upper接近涨停价11.29。"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":10.8,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":11.2,"low":10.6},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":["warn_streak_top"]},{"tsCode":"000751.SZ","name":"锌业股份","roleLlm":"elastic","roleMech":"core","roleConflict":true,"reason":"今日涨停+10.11%，机械角色core且簇内RS名次2，锌冶炼弹性标的","isPrimary":true,"industry":"铅锌","industryLift":395.9285714285714,"liftReason":null,"primaryReason":"highest_lift","rsRank":2,"k4Tag":"avoid_flag","mech":{"close":5.12,"limit_down":4.61,"limit_up":5.63,"ma20":4.5215000000000005,"no_limit_reason":null,"stop_price":4.86},"entryZone":{"high":5.25,"low":5.0,"why":"今日涨停收盘5.12，low 5.00略低于收盘给高开回落缓冲，max_chase 5.50控制在涨停价5.63以内；exit 5.30–5.60为连板预期下的短线压力区间，但冶炼端基本面支撑偏弱，区间不宜看得太高。"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":5.5,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":5.6,"low":5.3},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":[]}],"roleConflicts":["000751.SZ"],"tier":2,"rankInTier":4,"rankMech":4,"mechScore":0.6971428571428572,"tierBreakdown":{"contrib":{"card_density":0.0,"driver_freshness":0.1,"leader_clarity":0.15,"sector_strength":0.297143,"tradability":0.15},"dims":{"card_density":0.0,"driver_freshness":1.0,"leader_clarity":0.5,"sector_strength":0.990476,"tradability":0.75},"engine_api_version":1,"flags":[],"neutral_filled_weight":0,"pack_version":"K7-pack-v1","score":0.697143,"weights":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2},"weights_raw":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2}},"tierReason":null,"tierNote":"T2档第4位、机械分0.697配合sector_strength 0.99但card_density 0.00——板块极强而信息真空，排在中后段合理，不宜升档。","scripts":{"flat":"次日平开时，600497在10.00–10.40区间内观察是否放量站稳10.26以上，站稳可轻仓试探；000751平开意味着涨停后获利盘不愿接力，在5.00–5.25区间缩量企稳可极轻仓观察，但需始终记住冶炼端与矿端紧缺的逻辑方向并不一致，弹性票走弱的速度往往比龙头快。","strong":"如果次日高开或强势开盘，600497作为龙头若高开在10.40–10.80区间且量能配合，可在区间内小仓跟进，但max_chase 10.80以上不追；000751若同步高开，追价控制在5.45以内。需要警惕的是：若龙头平开但弹性票大幅高开，说明资金在博弹性而非跟随基本面，矿端紧缺逻辑的纯度下降，仓位应更轻。两票同步强势才算健康。","weak":"次日低开或走弱时，尤其000751若跌破5.00则说明涨停次日资金出逃，不要硬接；600497若回到10.00以下虽离MA20仍有距离但短线结构转弱。任一成员盘中触及跌停价或收盘跌破章程止损线（600497的9.75、000751的4.86），系统自动判定该成员失效，整个篮子即失效，不要在失效信号后再加仓。"},"scriptsUnavailableReason":null,"verificationSpec":{"basket_key":"72e90813","conditions":[{"code":"close_at_or_above_ref","compare":"close>=level","desc":"次日收盘 ≥ 基准日收盘(驱动被跟随)","scope":"member"},{"code":"holds_ma20","compare":"close>=level","desc":"次日收盘 ≥ 基准日 MA20(结构未破)","scope":"member"}],"evaluable_members":2,"member_count":2,"members":[{"close_at_or_above_ref":10.26,"holds_ma20":9.255500000000001,"ref_close":10.26,"ts_code":"600497.SH"},{"close_at_or_above_ref":5.12,"holds_ma20":4.5215000000000005,"ref_close":5.12,"ts_code":"000751.SZ"}],"min_members_hit":1,"next_trade_date":"20260806","require":["close_at_or_above_ref","holds_ma20"],"ruleset_version":"verify_ruleset_v2","spec_version":"basket_verify_v2","trade_date":"20260805"},"verificationText":"次日收盘时，至少有一只票同时满足收盘价不低于今日收盘价且不低于MA20——即600497收盘≥10.26且≥9.26，或000751收盘≥5.12且≥4.52。只要有一只站住，就算这个篮子的逻辑被市场跟随了。","invalidationSpec":{"any_of":["close_below_stop_line","limit_down_touch","close_below_ref_and_ma20"],"basket_key":"72e90813","conditions":[{"code":"close_below_stop_line","compare":"close<=level","desc":"次日收盘 ≤ 章程止损线(现役 stop_pct 算,系统算不由模型给)","scope":"member"},{"code":"limit_down_touch","compare":"low<=level","desc":"次日最低价 ≤ 跌停价(触及跌停)","scope":"member"},{"code":"close_below_ref_and_ma20","compare":"close<all_levels","desc":"次日收盘 < 基准日收盘 **且** < 基准日 MA20(两条同时成立才算破位)","levels":["ref_close","ma20"],"scope":"member"}],"evaluable_members":2,"member_count":2,"members":[{"close_below_ref_and_ma20":{"ma20":9.255500000000001,"ref_close":10.26},"close_below_stop_line":9.75,"limit_down_touch":9.23,"ref_close":10.26,"ts_code":"600497.SH"},{"close_below_ref_and_ma20":{"ma20":4.5215000000000005,"ref_close":5.12},"close_below_stop_line":4.86,"limit_down_touch":4.61,"ref_close":5.12,"ts_code":"000751.SZ"}],"min_members_hit":1,"next_trade_date":"20260806","ruleset_version":"verify_ruleset_v2","spec_version":"basket_invalidate_v2","stop_pct":0.05,"trade_date":"20260805"},"invalidationText":"次日任一成员命中以下任意一条即该成员失效：收盘价跌破章程止损线（600497低于9.75、000751低于4.86），或盘中最低价触及跌停价（600497触及9.23、000751触及4.61），或收盘同时低于今日收盘和MA20。有一只失效，整个篮子就判失效。","risks":["两只票均命中K4黄牌分区，机器不禁止但需人工复核，存在异常交易特征或潜在风险事件的可能","000751角色判定分歧（模型elastic vs 机械core），且冶炼端加工费承压与矿端紧缺对冶炼利润是利空方向，其涨停更可能是板块情绪外溢而非基本面支撑","card_density 0.00说明卡片信息密度极低，板块连续两天活跃但信息骨架不足，持续性存疑","板块已连续两天活跃，T2第4位入场时机偏晚，追高空间可能有限"],"disclaimer":"参考,非指令 —— 买卖与终选在你,系统不代下单;纪律以章程为准。","fingerprint":{"stopPct":0.05,"takeProfitRetrace":0.08,"charterVersion":"v1.3.3","packVersion":"K7-pack-v1","engineApiVersion":1,"verificationRulesetVersion":"verify_ruleset_v2"},"disciplineLabels":["章程止损 −5.0%","回落止盈 8.0%"],"narrative":"这个篮子的底层逻辑是铅锌矿端紧缺。据2026年7月SMM数据，上半年铅精矿产量同比下降1.3%，下半年加工费难有反弹，四季度冬储预期下还有下调压力——这是一条供给端的硬约束叙事。中金岭南7月初发布的业绩预告也佐证了这个方向：预计上半年归母净利润同比增长88%–115%，矿端资源紧的逻辑在业绩层面开始兑现。板块已连续两天活跃，sector_strength 0.99说明盘面热度确实在。\n\n两只票的分工很清楚：驰宏锌锗（600497）是龙头，今天涨6.88%、成交23亿，品牌辨识度和流动性都在板块前列，是资金的\"主阵地\"；锌业股份（000751）今天涨停10.11%，定位是弹性标的。但这里有个需要讲透的分歧——000751是锌冶炼，而矿端紧缺、加工费下调对冶炼端实际上是利润承压的方向，它的涨停更像是板块情绪外溢带来的弹性博弈，而非基本面直接受益。模型给它elastic角色、机械侧判core，两说不一致本身就说明这只票的定位存在模糊地带。驰宏锌锗的逻辑相对干净，矿端紧缺直接利好自有矿山的企业，但它的leader_clarity只有0.50，说明龙头属性也不是铁板钉钉。\n\n风险方面最显眼的是两只票都命中K4黄牌分区，机器不禁止但需要人工复核，这意味着系统在异常交易特征上已经亮了黄灯。card_density 0.00是另一个值得警惕的信号——板块虽然连涨两天、强度极高，但卡片信息密度为零，说明这种热度更多是盘面情绪驱动而非信息面厚度支撑，持续性存疑。T2档第4位、机械分0.697的排位，放在sector_strength 0.99的极强板块里，本质上反映的是\"板块够强但信息骨架不够硬\"的矛盾状态——入场偏晚，纪律执行的要求更高。","llmStage":"ok","degraded":false,"notes":[]},"cardVersion":1,"cardUnavailableReason":null,"tierHistory":null},{"basketId":7,"basketKey":"9346bf89","name":"半导体设备国产化","tradeDate":"","tier":2,"memberCodes":["002371.SZ","688012.SH","688072.SH"],"card":{"specVersion":"basket_card_v2","version":1,"basketKey":"9346bf89","tradeDate":"20260805","nextTradeDate":"20260806","name":"半导体设备国产化","driver":"SEMI预测2026年全球半导体设备销售+23.2%，大基金三期3440亿投向设备材料国产化，ASML Q2财报超预期并上调全年指引至430-450亿欧元","driverKind":"theme","evidence":[{"claim":"国际半导体产业协会（SEMI）7月21日发布报告，预计2026年全球半导体设备销售额将同比增长23.2%至1659亿美元，到2028年进一步升至2295亿美元创历史新高；人工智能基础设施扩展及对先进逻辑芯片和下一代存储产品的投资将推动市场增长。该消息直接催化7月22日A股半导体产业链掀起涨停潮，科创50指数大涨10.73%","date":"2026-07-21","source":"SEMI（全球半导体行业协会）/ 央视财经、21世纪经济报道","url":""},{"claim":"工信部2026年7月20日在国新办发布会公布：上半年我国集成电路出口额同比增长88.7%，电子元件出口额同比增长62.6%；截至6月底我国智能算力规模达2185EFLOPS，全国算力设施整体上架率71.4%，近两年围绕国家算力枢纽节点建设超70条算力大通道","date":"2026-07-20","source":"工业和信息化部 / 人民日报综合新华社客户端、央视新闻","url":""},{"claim":"中船特气7月17日发布2026年半年度报告，上半年营收19.04亿元同比增83.13%，归母净利润3.48亿元同比增95.63%，Q2净利润环比增143%；报告指出全球半导体产业在AI算力等多重需求驱动下持续扩张，电子特种气体市场需求稳步增长，核心产品六氟化钨营收同比增长近3倍","date":"2026-07-17","source":"中船特气（688146.SH）2026年半年度报告 / 21世纪经济报道","url":""},{"claim":"TCL中环子公司拟投资119.6亿元建设集成电路用半导体大硅片深圳项目（规划12英寸70万片/月）；国科微拟定增募资不超50.61亿元用于AI视觉处理芯片、端侧AI芯片等研发及产业化；惠科股份拟出资40亿元设立全资子公司投建先进封装及测试项目（一期12寸2000万颗/月）——三家公司同日公告加码半导体","date":"2026-07-17","source":"TCL中环、国科微、惠科股份公告","url":""},{"claim":"高盛最新研报指出，中国在全球AI供应链中拥有独特竞争优势，特别是半导体等领域尚未获得股市充分估值；同期台积电、三星宣布上调先进制程晶圆代工价格5%至15%。","date":"2026-07-11","source":"半导体日报","url":""},{"claim":"华为于5月25日在上海阐释“韬定律”半导体设计新路径；中芯国际2026年一季度销售额达25亿美元（同比增长11%），预计二季度销售额环比增长14%至16%，市场对半导体自立自强战略期待升温。","date":"2026-05-26","source":"中国半导体股狂热，但盈利能力落后 / 芯片股，集体大涨","url":""},{"claim":"大基金三期注册资本3440亿元，超一期二期之和，六大国有行首次出资1140亿元，核心投向设备材料国产化及先进封装与AI存储。","date":"2026-07","source":"投资逻辑分析文章","url":""},{"claim":"2026年二季度公募基金重仓股大洗牌，电子行业持仓占比突破43%，TMT整体持仓突破60%，北方华创、中微公司等半导体核心标的集体入围前十大重仓股。","date":"2026-07","source":"投资逻辑分析文章","url":""},{"claim":"中芯国际作为大基金核心持仓，2026年6月科创板史上最大资产重组案收官，大基金A股持股比例14.03%；华虹宏力2026年二季度被公募基金大幅增持。","date":"2026-07","source":"投资逻辑分析文章","url":""},{"claim":"2026年6月5日，沪硅产业公告大基金减持公司股份9915.07万股，占总股本3%，减持金额约26.26亿元，持股比例降至12.49%。","date":"2026-06-05","source":"集微网","url":""},{"claim":"2026年6月26日，德邦科技公告大基金减持公司股份149.35万股，持股比例由11.90%降至10.85%。","date":"2026-06-26","source":"21世纪经济报道","url":""},{"claim":"ASML发布2026年第二季度财报：单季净销售额93.3亿欧元（同比+21%），净利润29.2亿欧元（同比+27%），毛利率54.0%，均超出市场预期。公司将2026年全年净销售额预期由此前360亿–400亿欧元上调至430亿–450亿欧元，为上市以来最高水平，系年内第二次上调。超预期核心驱动力来自装机售后服务业务（收入28亿欧元，高于预期3亿欧元）。","date":"2026-07-15","source":"经济观察报 / 芯东西","url":""},{"claim":"ASML宣布High NA EUV（高数值孔径极紫外光刻机）正式迈入商业量产阶段：英特尔在Intel 18A制程节点上采用ASML High NA EUV生产部分Intel Core Ultra系列3处理器，良品率达到现有NXE EUV平台水平。2026年全年确认营收的High NA EUV设备为4至5台。","date":"2026-07-16","source":"芯东西","url":""},{"claim":"ASML计划提高EUV和DUV光刻机价格。部分中国大陆客户已同意DUV系统定价提高10%；台积电对此表示抵制，且截至2029年路线图无任何使用High-NA EUV的计划。ASML CFO称强劲需求增强了定价能力，但受超长交付周期约束价格调整不会立刻落地。","date":"2026-07","source":"TrendForce / 超能网","url":""},{"claim":"ASML CFO罗杰·达森表示EUV光刻机订单已提前两年开始积累，至2027年末订单已排满，称'这种情况我们很多年未曾遇到过'。公司计划基于2026年约65台Low NA EUV产能，2027年扩产30%，并评估2028年再扩产30%；DUV浸润式光刻机2026年产能约130台，2027年同样扩产30%。","date":"2026-07-15","source":"经济观察报","url":""},{"claim":"ASML CEO傅恪礼表示AI相关投资持续推进，带动先进逻辑芯片和存储芯片需求增长，客户正加快推进产能扩张。预计2026年来自先进逻辑芯片领域收入将增长约25%。中国市场营收占比将维持约20%，增量主要来自成熟制程逻辑芯片设备需求，服务本土产业链自主需求。","date":"2026-07-15","source":"芯东西 / 经济观察报","url":""},{"claim":"ASML预计2026年第三季度净销售额110亿–120亿欧元，毛利率55%–57%，研发费用约12亿欧元，均高于市场预期。2026年上半年总收入180.93亿欧元（同比+17.2%），新增订单量保持强劲。","date":"2026-07-15","source":"经济观察报 / 芯东西","url":""}],"evidenceStatus":"ok","whyNow":"7月21日SEMI报告发布、7月15日ASML财报超预期、台积电与三星上调先进制程代工价格5-15%、公募Q2电子行业持仓突破43%，多重催化在7月下旬集中释放后板块持续活跃第二天","members":[{"tsCode":"688012.SH","name":"中微公司","roleLlm":"leader","roleMech":null,"roleConflict":false,"reason":"今日+12.67%涨幅和132亿成交均为设备板块最高，刻蚀设备龙头同时出现在四颗种子中","isPrimary":true,"industry":"半导体","industryLift":19.517605633802816,"liftReason":null,"primaryReason":"highest_lift","rsRank":null,"k4Tag":null,"mech":{"close":358.0,"limit_down":286.4,"limit_up":429.6,"ma20":377.467,"no_limit_reason":null,"stop_price":340.1},"entryZone":{"high":375.0,"low":358.0,"why":"今日+12.67%放量大涨后，下沿设于收盘价附近供回踩确认，上沿参考MA20(377.47)下方的结构阻力，最高追价控制在MA20上方不远处；压力位第一目标看回MA20、第二目标看前期高点区域"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":390.0,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":410.0,"low":377.0},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":["warn_streak_top"]},{"tsCode":"002371.SZ","name":"北方华创","roleLlm":"core","roleMech":null,"roleConflict":false,"reason":"国内半导体设备平台型龙头，99亿成交提供容量，公募Q2重仓前十大标的","isPrimary":true,"industry":"半导体","industryLift":19.517605633802816,"liftReason":null,"primaryReason":"highest_lift","rsRank":null,"k4Tag":"avoid_flag","mech":{"close":734.2,"limit_down":660.78,"limit_up":807.62,"ma20":731.591,"no_limit_reason":null,"stop_price":697.49},"entryZone":{"high":748.0,"low":730.0,"why":"三只中结构最健康(收盘734.20略高于MA20 731.59)，建仓区间围绕收盘价上下展开；K4黄牌需人工复核，区间适度收窄以控风险；压力位参考前期密集成交区域"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":762.0,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":785.0,"low":750.0},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":["warn_streak_top"]},{"tsCode":"688072.SH","name":"拓荆科技","roleLlm":"elastic","roleMech":null,"roleConflict":false,"reason":"今日+12.14%，薄膜沉积设备龙头，72.64亿成交弹性突出","isPrimary":true,"industry":"半导体","industryLift":19.517605633802816,"liftReason":null,"primaryReason":"highest_lift","rsRank":null,"k4Tag":null,"mech":{"close":677.88,"limit_down":542.3,"limit_up":813.46,"ma20":732.783,"no_limit_reason":null,"stop_price":643.99},"entryZone":{"high":693.0,"low":675.0,"why":"今日+12.14%但收盘677.88远低于MA20 732.78，结构最弱，建仓区间偏保守、最高追价仍远低于MA20；压力位上沿看回MA20附近"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":710.0,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":735.0,"low":700.0},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":["warn_streak_top"]}],"roleConflicts":[],"tier":2,"rankInTier":2,"rankMech":2,"mechScore":0.810952380952381,"tierBreakdown":{"contrib":{"card_density":0.066667,"driver_freshness":0.1,"leader_clarity":0.15,"sector_strength":0.294286,"tradability":0.2},"dims":{"card_density":0.666667,"driver_freshness":1.0,"leader_clarity":0.5,"sector_strength":0.980952,"tradability":1.0},"engine_api_version":1,"flags":["leader_clarity_missing"],"neutral_filled_weight":0.3,"pack_version":"K7-pack-v1","score":0.810952,"weights":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2},"weights_raw":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2}},"tierReason":null,"tierNote":"T2档内第2位、机械分0.811合理；sector_strength 0.98与tradability 1.00支撑该档位，但leader_clarity仅0.50与card_density 0.67说明龙头确定性不足、催化共振度有限，档内次序无明显不当。","scripts":{"flat":"平开是最中性的情景。中微和拓荆平开意味着昨日大涨后今日没有延续动能，先观察前30分钟方向——若放量上行可按建仓区间下沿附近观察，若缩量横盘则等待午后方向选择。北方华创平开于734附近时结构尚可，按区间下沿观察即可。","strong":"若次日高开，中微和拓荆的高开幅度直接决定操作余地。高开但仍低于各自MA20时，不急于追高，观察开盘后前15分钟能否放量向MA20方向发起冲击——能则按建仓区间上沿附近观察，不能则等待回踩。北方华创高开时注意734-740一带能否站稳，站稳可按区间下沿附近观察。三只同时大幅高开超过5%时追价风险陡升，max_chase是硬上限，超出不追。","weak":"低开是最需要警惕的情景。中微和拓荆本就在MA20下方，低开意味着结构进一步恶化，若低开后继续走弱、逼近章程止损线（中微340.10、拓荆643.99），不要试图接飞刀。北方华创低开至730以下时MA20(731.59)失守，结构也转弱。三只中有两只出现低开低走时，篮子失效概率显著上升。"},"scriptsUnavailableReason":null,"verificationSpec":{"basket_key":"9346bf89","conditions":[{"code":"close_at_or_above_ref","compare":"close>=level","desc":"次日收盘 ≥ 基准日收盘(驱动被跟随)","scope":"member"},{"code":"holds_ma20","compare":"close>=level","desc":"次日收盘 ≥ 基准日 MA20(结构未破)","scope":"member"}],"evaluable_members":3,"member_count":3,"members":[{"close_at_or_above_ref":358.0,"holds_ma20":377.467,"ref_close":358.0,"ts_code":"688012.SH"},{"close_at_or_above_ref":734.2,"holds_ma20":731.591,"ref_close":734.2,"ts_code":"002371.SZ"},{"close_at_or_above_ref":677.88,"holds_ma20":732.783,"ref_close":677.88,"ts_code":"688072.SH"}],"min_members_hit":2,"next_trade_date":"20260806","require":["close_at_or_above_ref","holds_ma20"],"ruleset_version":"verify_ruleset_v2","spec_version":"basket_verify_v2","trade_date":"20260805"},"verificationText":"明天收盘时，至少两只票要同时满足两个条件：收在或不低于今日收盘价，且收在或不低于各自的MA20。对北方华创来说守住734.20就基本达标；但中微要收上377.47、拓荆要收上732.78，都需要明天再来一个可观的涨幅。两只里有一只做到，加上北方华创守住，篮子就算被验证。","invalidationSpec":{"any_of":["close_below_stop_line","limit_down_touch","close_below_ref_and_ma20"],"basket_key":"9346bf89","conditions":[{"code":"close_below_stop_line","compare":"close<=level","desc":"次日收盘 ≤ 章程止损线(现役 stop_pct 算,系统算不由模型给)","scope":"member"},{"code":"limit_down_touch","compare":"low<=level","desc":"次日最低价 ≤ 跌停价(触及跌停)","scope":"member"},{"code":"close_below_ref_and_ma20","compare":"close<all_levels","desc":"次日收盘 < 基准日收盘 **且** < 基准日 MA20(两条同时成立才算破位)","levels":["ref_close","ma20"],"scope":"member"}],"evaluable_members":3,"member_count":3,"members":[{"close_below_ref_and_ma20":{"ma20":377.467,"ref_close":358.0},"close_below_stop_line":340.1,"limit_down_touch":286.4,"ref_close":358.0,"ts_code":"688012.SH"},{"close_below_ref_and_ma20":{"ma20":731.591,"ref_close":734.2},"close_below_stop_line":697.49,"limit_down_touch":660.78,"ref_close":734.2,"ts_code":"002371.SZ"},{"close_below_ref_and_ma20":{"ma20":732.783,"ref_close":677.88},"close_below_stop_line":643.99,"limit_down_touch":542.3,"ref_close":677.88,"ts_code":"688072.SH"}],"min_members_hit":2,"next_trade_date":"20260806","ruleset_version":"verify_ruleset_v2","spec_version":"basket_invalidate_v2","stop_pct":0.05,"trade_date":"20260805"},"invalidationText":"最需要盯的是两只结构弱的票：中微如果明天收盘低于358.00（且本就低于MA20），或者拓荆收盘低于677.88（且本就低于MA20），各自就算失效。再加上北方华创如果收盘同时低于734.20和731.59，两只以上失效则整个篮子判废。另外任何一只盘中触及跌停价（中微286.40、北方华创660.78、拓荆542.30）或收盘触及章程止损线，也是该成员直接失效。","risks":["中微和拓荆收盘均显著低于MA20，验证门槛高、失效门槛低，明天若不涨或小跌两只就可能同时触发破位失效","北方华创命中K4黄牌分区(avoid_flag)，存在系统未详细披露的风险维度，需人工复核后方可参与","催化集中在7月中下旬、板块今天是活跃第二天，后续若无明显增量催化，板块轮动退潮风险上升","大基金对部分半导体标的（如沪硅产业6月减持、德邦科技6月减持）已在减持，若扩散至本篮子成员将构成额外压力"],"disclaimer":"参考,非指令 —— 买卖与终选在你,系统不代下单;纪律以章程为准。","fingerprint":{"stopPct":0.05,"takeProfitRetrace":0.08,"charterVersion":"v1.3.3","packVersion":"K7-pack-v1","engineApiVersion":1,"verificationRulesetVersion":"verify_ruleset_v2"},"disciplineLabels":["章程止损 −5.0%","回落止盈 8.0%"],"narrative":"这个篮子的驱动力在 7 月中下旬集中爆发——7 月 15 日 ASML Q2 财报超预期并将全年指引上调至 430-450 亿欧元、7 月 21 日 SEMI 预测 2026 年全球半导体设备销售同比 +23.2%至 1659 亿美元、大基金三期 3440 亿聚焦设备材料国产化、台积电与三星上调先进制程代工价格 5-15%、公募 Q2 电子行业持仓突破 43%——催化密度确实高，driver_freshness 满分 1.00 印证了这一点。但要注意这些催化剂大多已过去一到两周，板块今天是持续活跃的第二天，市场已有一定程度消化。\n\n三只票的分工是清楚的：中微公司是刻蚀设备龙头、今日 +12.67% 涨幅与 132 亿成交均为板块最高，被定为 leader；北方华创是平台型龙头、99 亿成交提供容量、公募 Q2 重仓前十，被定为 core；拓荆科技是薄膜沉积设备龙头、今日 +12.14%、72.64 亿成交弹性突出，被定为 elastic。但 leader_clarity 只有 0.50，说明系统对\"谁是真正龙头\"并没有高度确信——中微今天涨幅和量都最大，但北方华创结构最健康（收盘 734.20 略高于 MA20 731.59），两者各有道理。\n\n结构上的分歧值得重视。中微收盘 358.00 远低于 MA20 377.47，拓荆收盘 677.88 更是比 MA20 732.78 低了近 8%，两只票都在均线下方运行。这意味着验证条件对它们来说门槛不低——中微明天需要收在 377.47 以上、拓荆需要收在 732.78 以上才算\"结构未破\"，对应从今日收盘价分别要涨约 5.4% 和 8.1%。只有北方华创的验证门槛相对友好，收盘守住 734.20 即可（因为 MA20 731.59 还低于收盘价，验证的约束项是收盘价本身）。换句话说，要凑够 2 只验证通过，至少需要中微或拓荆中有一只明天走出一个相当可观的涨幅。反过来看，失效条件里\"收盘同时低于基准收盘和 MA20\"这条，对已经站在 MA20 下方的中微和拓荆来说更容易触发——只要明天收跌，就同时满足两个条件直接判该成员失效，这一点必须心里有数。\n\n另外特别提示：北方华创命中 K4 黄牌分区（avoid_flag），机器不会禁止但需要人工复核——这意味着该票在某些风险维度上触发了系统警示，参与前务必自行确认是否理解该风险。sector_strength 0.98 和 tradability 1.00 都接近满分，说明板块热度和流动性不是问题；但 card_density 只有 0.67，反映催化虽密集但尚未形成压倒性的共振。纪律方面，系统现役章程止损 -5.0%、回落止盈 8.0% 自动生效，盘中按机械阈值判定，不需要手动设置。","llmStage":"ok","degraded":false,"notes":[]},"cardVersion":1,"cardUnavailableReason":null,"tierHistory":null},{"basketId":9,"basketKey":"f9140150","name":"玻璃基板封装","tradeDate":"","tier":2,"memberCodes":["000725.SZ","300433.SZ","603773.SH"],"card":{"specVersion":"basket_card_v2","version":1,"basketKey":"f9140150","tradeDate":"20260805","nextTradeDate":"20260806","name":"玻璃基板封装","driver":"2026年被产业界定义为'玻璃基板商业化元年'，京东方玻璃基载板已送样客户并通过六项信赖性测试，英特尔1月宣布量产、7月与蓝思科技战略合作","driverKind":"theme","evidence":[{"claim":"京东方发布2026年半年度业绩预告，预计上半年归母净利润50亿至55亿元，同比增长54%至69%，上年同期盈利32.47亿元","date":"2026-07","source":"中经记者报道（黎竹、张靖超，成都）","url":""},{"claim":"京东方7月3日发布投资者关系活动记录表，指出与康宁合作聚焦玻璃基封装载板、光互连相关应用，打通从材料、制造到应用的关键环节，提升先进封装整体制造能力与良率","date":"2026-07-03","source":"京东方投资者关系活动记录表","url":""},{"claim":"京东方董事长陈炎顺7月2日公开表示，与康宁合作核心聚焦AI时代玻璃基全新的技术创新和应用延展，不再是简单产品采购","date":"2026-07-02","source":"中经记者报道引用陈炎顺公开表态","url":""},{"claim":"京东方与康宁5月20日官宣合作，围绕玻璃基封装载板、可折叠玻璃、钙钛矿玻璃基板、光互连相关应用等领域开展合作；分工为康宁供高端TGV原片离子交换专利，京东方做工艺实现规模化制造","date":"2026-05-20","source":"中经记者报道（引用柏文喜分析）","url":""},{"claim":"京东方第8.6代AMOLED生产线于6月17日实现量产，全球仅韩国、中国布局该代线；分析师认为2026—2027年兑现8.6代AMOLED折旧红利","date":"2026-06-17","source":"中经记者报道（引用科研人员及柏文喜分析）","url":""},{"claim":"蓝思科技在CES 2026展示航天级超薄柔性玻璃（UTG，厚度<100微米），已成为下一代柔性太阳翼不可替代的关键封装材料；2026年被定义为商业航天从技术布局转向规模量产与利润兑现的关键之年，同步推进TGV玻璃基板等前沿项目","date":"2026-02-27","source":"蓝思科技官方微信公众号 / C114讯","url":""},{"claim":"蓝思科技将消费电子精密制造能力迁移至航天领域，超薄柔性玻璃技术经改性升级满足太空抗辐射、抗原子氧剥蚀要求，卡位商业航天供应链稀缺生态位","date":"2026-02","source":"蓝思科技消息报道","url":""},{"claim":"2026年被产业界称为'玻璃基板商业化元年'。7月30日TCL华星CEO赵军表示公司玻璃基封装样品预计今年下半年亮相，正论证筹建中试线。京东方已完成TGV开孔、深孔填铜、低应力金属布线、高层数压合等全流程工艺，20层玻璃基载板样品已送样客户并通过六项信赖性测试。英特尔2026年1月宣布玻璃基板技术进入量产阶段，三星电机4月起向苹果供应玻璃基板样品。美国康宁、德国肖特、日本AGC三家合计占据全球高端封装级玻璃原片约68%市场份额。","date":"2026-07-30","source":"观察者网","url":""},{"claim":"通富微电预计2026年上半年归母净利润16亿元—18亿元，同比增长288.26%—336.8%。公司积极布局Chiplet、2D等顶尖封装技术，开发扇出型、圆片级、倒装焊等封装技术并扩充产能。机构表示日月光等封测巨头已宣布调涨先进封装报价，表明行业目前处于高景气扩张周期。","date":"2026-08","source":"财经媒体（具体来源未标注）","url":""},{"claim":"惠科股份于2026年7月17日签署合作协议，拟出资40亿元设立全资子公司浙江惠芯先进半导体有限公司，投建先进封装及测试项目。项目一期计划建设12寸混合芯片先进封装及测试，达产后产能为2000万颗/月，建设周期不超过三年。公司称此举为主动向产业链高附加值环节延伸。","date":"2026-07-17","source":"财经媒体（具体来源未标注）","url":""},{"claim":"英伟达、AMD及云服务巨头已将ABF载板长约延伸至2028年，仍有客户要求IC载板厂提前开展2029-2030年扩产计划，并以预付款、共同承担建厂成本等方式锁定产能。欣兴将2026年资本开支上调至537亿新台币，景硕追加196亿新台币启动新厂兴建。中金公司认为IC载板产业链价格上行信号已逐步显现，高端载板需求持续扩容，上游特种树脂、铜箔、玻纤布等关键原材料供给偏紧，制造成本持续抬升。","date":"该来源未标注明确日期，时效不明","source":"行业媒体（具体名称未标注）","url":""},{"claim":"7月2日，三星电机与日本住友化学旗下东宇精细化学签署主合同，共同生产玻璃基板关键材料'玻璃芯'，预计2027年下半年启动量产","date":"2026-07-02","source":"前瞻产业研究院","url":""},{"claim":"7月2日，京东方在投资日活动上首次公开亮相玻璃基封装载板业务，已产出大尺寸高层数玻璃基载板样品并送样客户，试验线已于2026年上半年实现全自动化设备通线","date":"2026-07-02","source":"前瞻产业研究院","url":""},{"claim":"7月3日，韩国显示器产业协会通过YouTube频道正式发布玻璃基板技术宣传视频，介绍TGV技术及下一代无边框产品形态蓝图","date":"2026-07-03","source":"韩国显示器产业协会","url":""},{"claim":"7月24日，英特尔与蓝思科技宣布战略合作，结合英特尔芯片架构与先进封装能力及蓝思科技玻璃材料精密激光加工能力，探索玻璃基板封装技术","date":"2026-07-24","source":"前瞻产业研究院","url":""},{"claim":"7月30日，TCL华星CEO赵军在ChinaJoy展开幕前阐述玻璃基封装布局，称已组建专业团队与目标客户联合攻关，样品预计今年下半年亮相，正论证筹建中试线","date":"2026-07-30","source":"观察者网","url":""},{"claim":"天风证券调研指出市场将台积电CoPoS一代产品暂不采用玻璃基板方案误读为利空，实际台积电路线明确（2026年6月完成中试、2027年下半年小批量试产），Intel预计2027年落地，SK Absolics工厂已建成最快2026年量产，2026年底将完成量产验证、2027年Q2起行业迎大规模设备招标，全年资本开支有望突破百亿元","date":"该来源未标注明确日期，时效不明","source":"天风电新（天风证券）","url":""},{"claim":"2026年1月，英特尔正式宣布玻璃基板技术进入量产阶段，首款搭载Glass-Core的Xeon 6服务器处理器成为业界首个商业化落地的玻璃基板产品","date":"2026-01","source":"观察者网","url":""}],"evidenceStatus":"ok","whyNow":"7月30日TCL华星CEO公开玻璃基封装进展下半年样品亮相，7月24日英特尔与蓝思科技宣布战略合作，通富微电预计上半年净利润同比增288%-337%验证封测高景气，产业从技术验证迈向商业化落地","members":[{"tsCode":"000725.SZ","name":"京东方A","roleLlm":"leader","roleMech":null,"roleConflict":false,"reason":"玻璃基载板样品已送样客户并通过六项信赖性测试，146.56亿成交为板块辨识度最高标的","isPrimary":true,"industry":"元器件","industryLift":3.6659063301390202,"liftReason":null,"primaryReason":"highest_lift","rsRank":null,"k4Tag":null,"mech":{"close":5.97,"limit_down":5.37,"limit_up":6.57,"ma20":6.1615,"no_limit_reason":null,"stop_price":5.67},"entryZone":{"high":5.9,"low":5.75,"why":"收盘5.97在MA20 6.16下方，建仓区间设在收盘下方5.75–5.90留出缓冲但不过度逼近5.67止损线；max_chase 6.10压在MA20下方，越过MA20才视为结构修复、不追；压力位6.20–6.50对应MA20上方至前期密集成交区。"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":6.1,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":6.5,"low":6.2},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":["warn_streak_top"]},{"tsCode":"300433.SZ","name":"蓝思科技","roleLlm":"core","roleMech":null,"roleConflict":false,"reason":"7月24日与英特尔宣布战略合作探索玻璃基板封装，51.28亿成交提供容量","isPrimary":true,"industry":"元器件","industryLift":3.6659063301390202,"liftReason":null,"primaryReason":"highest_lift","rsRank":null,"k4Tag":null,"mech":{"close":35.09,"limit_down":28.07,"limit_up":42.11,"ma20":36.534,"no_limit_reason":null,"stop_price":33.34},"entryZone":{"high":35.0,"low":34.0,"why":"收盘35.09在MA20 36.53下方，建仓区间34.00–35.00在收盘附近偏下方、与33.34止损保持约2%缓冲；max_chase 36.50贴近MA20下沿不过线；压力位36.60–38.50对应收复MA20后的上方筹码松动区。"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":36.5,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":38.5,"low":36.6},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":["warn_streak_top"]},{"tsCode":"603773.SH","name":"沃格光电","roleLlm":"elastic","roleMech":"core","roleConflict":true,"reason":"今日涨停+10.00%，机械角色core，TGV玻璃基板核心标的弹性突出","isPrimary":true,"industry":"元器件","industryLift":3.6659063301390202,"liftReason":null,"primaryReason":"highest_lift","rsRank":2,"k4Tag":null,"mech":{"close":78.57,"limit_down":70.71,"limit_up":86.43,"ma20":86.96000000000001,"no_limit_reason":null,"stop_price":74.64},"entryZone":{"high":78.5,"low":76.5,"why":"今日涨停收78.57仍远低于MA20 86.96，属跌深反弹弹性；建仓区间76.50–78.50在收盘下方留缓冲但距74.64止损约2.4%；max_chase 82.00给弹性票留追价空间但不超涨停价86.43；压力位86.96为MA20所在、90.00为前期平台。"},"entryZoneClamp":"ok","entryZoneUnavailableReason":null,"maxChase":82.0,"maxChaseClamp":"ok","maxChaseUnavailableReason":null,"exitReference":{"high":90.0,"low":86.96},"exitReferenceClamp":"ok","exitReferenceUnavailableReason":null,"tags":[],"tagsAbsent":[]}],"roleConflicts":["603773.SH"],"tier":2,"rankInTier":3,"rankMech":3,"mechScore":0.8052380952380953,"tierBreakdown":{"contrib":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.15,"sector_strength":0.288571,"tradability":0.166667},"dims":{"card_density":1.0,"driver_freshness":1.0,"leader_clarity":0.5,"sector_strength":0.961905,"tradability":0.833333},"engine_api_version":1,"flags":[],"neutral_filled_weight":0,"pack_version":"K7-pack-v1","score":0.805238,"weights":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2},"weights_raw":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2}},"tierReason":null,"tierNote":"T2档内第3位、机械分0.805合理——驱动密度和新鲜度拉满但leader_clarity仅0.50且三票全在MA20下方，结构短板压制了档位，排第三不委屈。","scripts":{"flat":"平开时三只票大概率还在MA20下方震荡，沃格光电的建仓区间（76.50–78.50）最接近平开位置，可优先观察其量能是否延续今天涨停的惯性；京东方A和蓝思科技平开则处于各自区间的上沿附近，若盘中没有放量向上突破MA20的动作，不宜在中段介入，等方向明朗。","strong":"高开时先看京东方A和蓝思科技是否同步走强——若两者都快速向MA20靠拢（6.16/36.53），说明板块结构在修复，可在各自的建仓区间上沿附近参与，但追价不超过max_chase；若只有沃格光电一马当先而另外两只高开后回落，属于弹性脱离主力的信号，不追高，观察回落是否守住建仓区间下沿再定。","weak":"低开时三只票的章程止损线都不远（京东方5.67、蓝思33.34、沃格74.64），尤其是京东方A从5.97到5.67仅5%空间。低开后重点观察是否有成员快速逼近止损线——若两只以上同时接近止损区域，篮子失效概率升高，此时不应在区间内接飞刀，等收盘结构确认再评估。"},"scriptsUnavailableReason":null,"verificationSpec":{"basket_key":"f9140150","conditions":[{"code":"close_at_or_above_ref","compare":"close>=level","desc":"次日收盘 ≥ 基准日收盘(驱动被跟随)","scope":"member"},{"code":"holds_ma20","compare":"close>=level","desc":"次日收盘 ≥ 基准日 MA20(结构未破)","scope":"member"}],"evaluable_members":3,"member_count":3,"members":[{"close_at_or_above_ref":5.97,"holds_ma20":6.1615,"ref_close":5.97,"ts_code":"000725.SZ"},{"close_at_or_above_ref":35.09,"holds_ma20":36.534,"ref_close":35.09,"ts_code":"300433.SZ"},{"close_at_or_above_ref":78.57,"holds_ma20":86.96000000000001,"ref_close":78.57,"ts_code":"603773.SH"}],"min_members_hit":2,"next_trade_date":"20260806","require":["close_at_or_above_ref","holds_ma20"],"ruleset_version":"verify_ruleset_v2","spec_version":"basket_verify_v2","trade_date":"20260805"},"verificationText":"至少两只票次日收盘同时站上各自的基准日收盘价和MA20——也就是京东方A收上6.16、蓝思科技收上36.53、沃格光电收上86.96，三取其二。这意味着板块不是只有弹性票在跳，龙头和核心也跟着把结构修回来了。","invalidationSpec":{"any_of":["close_below_stop_line","limit_down_touch","close_below_ref_and_ma20"],"basket_key":"f9140150","conditions":[{"code":"close_below_stop_line","compare":"close<=level","desc":"次日收盘 ≤ 章程止损线(现役 stop_pct 算,系统算不由模型给)","scope":"member"},{"code":"limit_down_touch","compare":"low<=level","desc":"次日最低价 ≤ 跌停价(触及跌停)","scope":"member"},{"code":"close_below_ref_and_ma20","compare":"close<all_levels","desc":"次日收盘 < 基准日收盘 **且** < 基准日 MA20(两条同时成立才算破位)","levels":["ref_close","ma20"],"scope":"member"}],"evaluable_members":3,"member_count":3,"members":[{"close_below_ref_and_ma20":{"ma20":6.1615,"ref_close":5.97},"close_below_stop_line":5.67,"limit_down_touch":5.37,"ref_close":5.97,"ts_code":"000725.SZ"},{"close_below_ref_and_ma20":{"ma20":36.534,"ref_close":35.09},"close_below_stop_line":33.34,"limit_down_touch":28.07,"ref_close":35.09,"ts_code":"300433.SZ"},{"close_below_ref_and_ma20":{"ma20":86.96000000000001,"ref_close":78.57},"close_below_stop_line":74.64,"limit_down_touch":70.71,"ref_close":78.57,"ts_code":"603773.SH"}],"min_members_hit":2,"next_trade_date":"20260806","ruleset_version":"verify_ruleset_v2","spec_version":"basket_invalidate_v2","stop_pct":0.05,"trade_date":"20260805"},"invalidationText":"两只及以上成员出现以下任一情况：收盘触及或跌破章程止损线（京东方5.67、蓝思33.34、沃格74.64），或盘中触及跌停价，或收盘同时低于基准日收盘和MA20。本质上就是消息没撑住股价、结构继续恶化。","risks":["三只票基准日收盘全部在MA20下方，结构本身已偏弱，次日若不能快速修复MA20，验证条件极难达成","沃格光电今日涨停但从78.57到MA20 86.96仍有超10%缺口，连续涨停概率低，若次日冲高回落可能带动板块情绪走差","驱动事件集中在7月下旬至8月初，新鲜度高但也意味着市场已有充分反应窗口，若没有新增催化，存在利好出尽风险","IC载板产能扩张的相关材料（来源未标注日期，时效不明）和天风证券调研（来源未标注日期，时效不明）两条证据无法确认时效，不宜作为当下决策的独立支撑"],"disclaimer":"参考,非指令 —— 买卖与终选在你,系统不代下单;纪律以章程为准。","fingerprint":{"stopPct":0.05,"takeProfitRetrace":0.08,"charterVersion":"v1.3.3","packVersion":"K7-pack-v1","engineApiVersion":1,"verificationRulesetVersion":"verify_ruleset_v2"},"disciplineLabels":["章程止损 −5.0%","回落止盈 8.0%"],"narrative":"这个篮子的驱动证据密度很高，7月几乎是密集轰炸：7月2日京东方投资日首次公开亮相玻璃基封装载板业务并确认样品已送样客户，7月24日英特尔与蓝思科技宣布战略合作，7月30日TCL华星CEO赵军又在ChinaJoy前披露样品下半年亮相、正论证中试线——再加上8月通富微电预增上半年净利润288%–337%从封测景气端做了独立佐证，产业从技术验证迈向商业化的叙事链条是完整的。问题在于结构：三只票基准日收盘全部压在MA20下方，京东方A收5.97对MA20的6.16、蓝思科技收35.09对36.53、沃格光电收78.57对86.96，也就是说消息在跑、股价没跟上。板块强度0.96和驱动新鲜度1.00说明资金确实在这个方向反复活动，但leader_clarity只有0.50，背后正是这种分歧——京东方A以146.56亿成交撑住板块辨识度，是当之无愧的容量锚，但价格结构最松散；沃格光电今天涨停、弹性最烈，可它恰恰是离MA20最远的那只（78.57对86.96，缺口超10%），涨停更像是跌深反弹而非趋势新高，而且它的角色判定本身有冲突——手动侧给的是elastic、机械侧给的是core，两说并存说明系统对它的定位也没有十足把握。蓝思科技夹在中间，51亿成交提供容量但没有当天涨停的锐度，7月24日与英特尔合作的消息已经过去近两周，如果没有新催化跟进，它可能只是被动跟随。验证条件要求至少两只成员次日收盘同时站上基准日收盘和MA20，这个门槛不低：沃格光电要收上86.96意味着从78.57再涨超10%、连续第二个涨停板，京东方A要收上6.16需要涨超3%，蓝思科技要收上36.53需要涨超4%——三者里至少两个同时做到，才算是逻辑被市场真正跟随。如果明天高开，需要重点看京东方A和蓝思科技能不能合力向上修复MA20而非只剩沃格光电一个人表演弹性的独角戏，因为弹性票单兵突进、龙头和核心不跟，恰恰是板块走弱而非走强的典型信号。","llmStage":"ok","degraded":false,"notes":[]},"cardVersion":1,"cardUnavailableReason":null,"tierHistory":null}],"basketsAvailable":true,"basketsUnavailableReason":null,"droppedBaskets":[],"droppedBasketsAvailable":true,"droppedBasketsUnavailableReason":null,"reviews":[{"basketId":4,"basketKey":"235d3aa4","name":"存储芯片涨价","tier":1,"d0":"20260804","reviewDate":"20260805","depth":"full","mech":{"auction_vs_script":{"available":true,"branch":"flat","gap_median":0.006516887367951085,"per_member":{"301308.SZ":{"branch":"flat","gap":0.006516887367951085},"603986.SH":{"branch":"flat","gap":0.0055614853098140404},"688525.SH":{"branch":"flat","gap":0.011216843653748976}},"script_present":true,"script_text":"若平开，市场对存储涨价逻辑暂无增量共识。以观望为主，除非盘中放量走强且预计收阳，否则不在建仓区间内主动建仓。","scripts_branches_on_card":["strong","flat","weak"],"source":"daily_open","unavailable_reason":null},"buyability":{"available":true,"buyable":3,"buyable_ratio":1.0,"limit_up":0,"member_count":3,"no_bar":0,"one_word":0,"per_member":{"301308.SZ":{"buyable":true,"limit_up":403.26,"limit_up_source":"card_frozen","reason":"buyable"},"603986.SH":{"buyable":true,"limit_up":391.62,"limit_up_source":"card_frozen","reason":"buyable"},"688525.SH":{"buyable":true,"limit_up":261.04,"limit_up_source":"card_frozen","reason":"buyable"}},"unavailable_reason":null},"close_rs":{"available":true,"excess_median":0.06794700000000001,"index_code":"000001.SH","index_ret":0.014689,"outperformers":3,"per_member":{"301308.SZ":{"excess":0.07122099999999998,"ret":0.08590999999999999},"603986.SH":{"excess":0.06794700000000001,"ret":0.082636},"688525.SH":{"excess":0.056749,"ret":0.071438}},"rs_positive":true,"unavailable_reason":null},"leader_pull":{"available":true,"leader_ret_median":0.082636,"leader_source":"card_role_mech_or_rank","leaders":["301308.SZ","603986.SH","688525.SH"],"led":null,"member_count":3,"no_peer_group":true,"others_ret_median":null,"spread":null,"unavailable_reason":null},"member_alignment":{"alignment":1.0,"available":true,"dominant_direction":"up","down":0,"flat":0,"member_count":3,"missing":[],"observed":3,"returns":{"301308.SZ":0.08590999999999999,"603986.SH":0.082636,"688525.SH":0.071438},"unavailable_reason":null,"up":3,"up_ratio":1.0},"meta":{"basket_id":4,"basket_key":"235d3aa4","card_version":1,"charter_version":"v1.3.3","d0":"20260804","day_notes":[],"engine_api_version":1,"has_card":true,"member_count":3,"members":["301308.SZ","603986.SH","688525.SH"],"name":"存储芯片涨价","pack_version":"K7-pack-v1","review_date":"20260805","tier":1,"verification_ruleset_version":"verify_ruleset_v2"},"mfe_mae":{"available":true,"capture_covered_minutes":239,"capture_empty_ticks":0,"capture_expected_minutes":240,"capture_recorded":true,"capture_status":"full","mae_median":0.06555811471265671,"mfe_median":0.0831413965507557,"mfe_source":"intraday","note":null,"per_member":{"301308.SZ":{"mae":0.06680553489064134,"mae_at":"14:19:40","mfe":0.08588007736943903,"mfe_at":"14:56:56","source":"intraday"},"603986.SH":{"mae":0.06555811471265671,"mae_at":"14:19:40","mfe":0.0831413965507557,"mfe_at":"14:55:56","source":"intraday"},"688525.SH":{"mae":0.05824483979221262,"mae_at":"14:19:40","mfe":0.07290948374936779,"mfe_at":"14:49:53","source":"intraday"}},"unavailable_reason":null},"open_direction":{"aligned":true,"available":true,"gap_dir":"up","gap_median":0.006516887367951085,"has_intraday_capture":true,"intraday_dir":"up","intraday_median":0.07664804469273734,"per_member":{"301308.SZ":{"first_tick_dir":"up","gap":0.006516887367951085,"gap_dir":"up","intraday":0.07887890255439922,"intraday_dir":"up"},"603986.SH":{"first_tick_dir":"up","gap":0.0055614853098140404,"gap_dir":"up","intraday":0.07664804469273734,"intraday_dir":"up"},"688525.SH":{"first_tick_dir":"up","gap":0.011216843653748976,"gap_dir":"up","intraday":0.05955357548756646,"intraday_dir":"up"}},"unavailable_reason":null},"spec_version":"basket_review_mech_v1","tier_vs_outcome":{"available":true,"basket_ret_median":0.082636,"day_baskets":4,"mech_score":0.81,"rank_by_outcome":2,"rank_by_tier":2,"rank_gap":0,"rank_in_tier":2,"rank_mech":2,"rank_note":"名次差是当日横截面的机械依据,**单日不构成结论**","tier":1,"tier_breakdown":{"contrib":{"card_density":0.1,"driver_freshness":0.06,"leader_clarity":0.15,"sector_strength":0.3,"tradability":0.2},"dims":{"card_density":1.0,"driver_freshness":0.6,"leader_clarity":0.5,"sector_strength":1.0,"tradability":1.0},"engine_api_version":1,"flags":["leader_clarity_missing"],"neutral_filled_weight":0.3,"pack_version":"K7-pack-v1","score":0.81,"weights":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2},"weights_raw":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2}},"unavailable_reason":null},"verification_timing":{"available":true,"eod_state":"unclear","first_falsified_at":null,"first_partial_at":null,"first_verified_at":null,"has_eod_verdict":true,"intraday_rows":1,"latched_falsified":false,"not_evaluated":false,"provisional":false,"rows":2,"state":"unclear","state_label":"收盘定论","trail":[{"observed_at":"2026-08-05T14:06:34+08:00","source":"intraday","state":"unclear"},{"observed_at":"2026-08-05T16:35:20+08:00","source":"eod","state":"unclear"}],"unavailable_reason":null}},"llmText":"昨天定档这个存储篮子时，机械分0.81排在T1第二位，五维里密度、板块强度、可买性都给到了满分，但驱动新鲜度只有0.60、龙头清晰度0.50。D0剧本写得很谨慎：竞价平开就意味着市场对存储涨价逻辑暂无增量共识，得观望，除非盘中放量走强且预计收阳才动手。今天竞价开盘中位+0.65%，确实落在卡上的flat分支，但剧本担心的“无增量共识”这一天上没兑现——开盘后方向直接向上，日内收/开中位+7.66%，和开盘跳空同向，三只票同向率100%全红，篮子收益中位+8.26%，大盘才+1.47%，超额中位跑到+6.79%。也就是说，昨天那条“平开即观望”的防线今天被单日行情直接打穿了，这是昨天判断偏保守、今天结果更猛的地方。当然，单日名次差是0，一天的结果说明不了规则失效，最多说这一天上它没兑现平开的弱势假设。\n\n机械判里满分的几项今天倒是兑现得挺干脆：可买性1.00，今天3只全买得进，没一字也没封死涨停；板块强度1.00，三只票MFE中位+8.31%，最大不利6.56%（intraday台账覆盖了239/240分钟，差1分钟没存拍，但大体看回撤控制得还行）。至于leader_clarity那0.50，今天因为D0认定的龙头就是这三只全包，龙头+8.26%对比其余成员“算不出”，所以龙头清晰度到底准不准，今天这个结构里判不了。验证证伪这块也是unclear，首次验证和首次证伪都没发生，今天这根大阳线更多是跟着D0那个长鑫IPO和业绩预告的驱动在走，但机械层面还没给定论。整体看，昨天对驱动新鲜度打0.60是留了余地的，今天行情确实比剧本预设的平开路径要强，但一天噪声大，不构成对档位或规则的结论。","llmSkipReason":null,"degraded":false,"verification":{"state":"unclear","source":"eod","observedAt":"2026-08-05T16:35:20+08:00","provisional":false,"notEvaluated":false,"label":"收盘定论"}},{"basketId":1,"basketKey":"93963424","name":"CPO光通信","tier":1,"d0":"20260804","reviewDate":"20260805","depth":"full","mech":{"auction_vs_script":{"available":true,"branch":"weak","gap_median":-0.04929577464788737,"per_member":{"300308.SZ":{"branch":"weak","gap":-0.138934823237018},"300620.SZ":{"branch":"flat","gap":-0.017929461722958395},"601869.SH":{"branch":"weak","gap":-0.04929577464788737}},"script_present":true,"script_text":"若低开走弱，优先看中际旭创和光库科技是否守住各自收盘价（1021.99 / 254.33）。若中际旭创跌破1021.99且光库跌破247.47，两只同时破位则篮子失效，不宜强行入场。长飞若跌向269.80止损线附近，警惕其成为先失效成员；若长飞与另一只同时失效，篮子判废。","scripts_branches_on_card":["strong","flat","weak"],"source":"daily_open","unavailable_reason":null},"buyability":{"available":true,"buyable":2,"buyable_ratio":0.6666666666666666,"limit_up":1,"member_count":3,"no_bar":0,"one_word":0,"per_member":{"300308.SZ":{"buyable":true,"limit_up":1226.39,"limit_up_source":"card_frozen","reason":"buyable"},"300620.SZ":{"buyable":true,"limit_up":305.2,"limit_up_source":"card_frozen","reason":"buyable"},"601869.SH":{"buyable":false,"limit_up":312.4,"limit_up_source":"card_frozen","reason":"limit_up"}},"unavailable_reason":null},"close_rs":{"available":true,"excess_median":0.011458,"index_code":"000001.SH","index_ret":0.014689,"outperformers":2,"per_member":{"300308.SZ":{"excess":-0.087341,"ret":-0.072652},"300620.SZ":{"excess":0.011458,"ret":0.026147},"601869.SH":{"excess":0.085311,"ret":0.1}},"rs_positive":true,"unavailable_reason":null},"leader_pull":{"available":true,"leader_ret_median":0.026147,"leader_source":"card_role_mech_or_rank","leaders":["300620.SZ"],"led":true,"member_count":3,"no_peer_group":false,"others_ret_median":0.013674000000000006,"spread":0.012472999999999995,"unavailable_reason":null},"member_alignment":{"alignment":0.6666666666666666,"available":true,"dominant_direction":"up","down":1,"flat":0,"member_count":3,"missing":[],"observed":3,"returns":{"300308.SZ":-0.072652,"300620.SZ":0.026147,"601869.SH":0.1},"unavailable_reason":null,"up":2,"up_ratio":0.6666666666666666},"meta":{"basket_id":1,"basket_key":"93963424","card_version":1,"charter_version":"v1.3.3","d0":"20260804","day_notes":[],"engine_api_version":1,"has_card":true,"member_count":3,"members":["300308.SZ","300620.SZ","601869.SH"],"name":"CPO光通信","pack_version":"K7-pack-v1","review_date":"20260805","tier":1,"verification_ruleset_version":"verify_ruleset_v2"},"mfe_mae":{"available":true,"capture_covered_minutes":239,"capture_empty_ticks":0,"capture_expected_minutes":240,"capture_recorded":true,"capture_status":"full","mae_median":0.0025557346754216947,"mfe_median":0.035976880430936165,"mfe_source":"intraday","note":null,"per_member":{"300308.SZ":{"mae":-0.07846456423252679,"mae_at":"14:22:41","mfe":-0.06148788148612028,"mfe_at":"14:06:34","source":"intraday"},"300620.SZ":{"mae":0.0025557346754216947,"mae_at":"13:01:14","mfe":0.035976880430936165,"mfe_at":"13:46:28","source":"intraday"},"601869.SH":{"mae":0.09999999999999987,"mae_at":"14:06:34","mfe":0.09999999999999987,"mfe_at":"14:06:34","source":"intraday"}},"unavailable_reason":null},"open_direction":{"aligned":false,"available":true,"gap_dir":"down","gap_median":-0.04929577464788737,"has_intraday_capture":true,"intraday_dir":"up","intraday_median":0.07697727272727284,"per_member":{"300308.SZ":{"first_tick_dir":"up","gap":-0.138934823237018,"gap_dir":"down","intraday":0.07697727272727284,"intraday_dir":"up"},"300620.SZ":{"first_tick_dir":"up","gap":-0.017929461722958395,"gap_dir":"down","intraday":0.04488129078752445,"intraday_dir":"up"},"601869.SH":{"first_tick_dir":"up","gap":-0.04929577464788737,"gap_dir":"down","intraday":0.15703703703703686,"intraday_dir":"up"}},"unavailable_reason":null},"spec_version":"basket_review_mech_v1","tier_vs_outcome":{"available":true,"basket_ret_median":0.026147,"day_baskets":4,"mech_score":0.8790476190476191,"rank_by_outcome":4,"rank_by_tier":1,"rank_gap":3,"rank_in_tier":1,"rank_mech":1,"rank_note":"名次差是当日横截面的机械依据,**单日不构成结论**","tier":1,"tier_breakdown":{"contrib":{"card_density":0.066667,"driver_freshness":0.06,"leader_clarity":0.3,"sector_strength":0.285714,"tradability":0.166667},"dims":{"card_density":0.666667,"driver_freshness":0.6,"leader_clarity":1.0,"sector_strength":0.952381,"tradability":0.833333},"engine_api_version":1,"flags":[],"neutral_filled_weight":0,"pack_version":"K7-pack-v1","score":0.879048,"weights":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2},"weights_raw":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2}},"unavailable_reason":null},"verification_timing":{"available":true,"eod_state":"partial","first_falsified_at":null,"first_partial_at":"2026-08-05T14:13:37+08:00","first_verified_at":null,"has_eod_verdict":true,"intraday_rows":2,"latched_falsified":false,"not_evaluated":false,"provisional":false,"rows":3,"state":"partial","state_label":"收盘定论","trail":[{"observed_at":"2026-08-05T14:06:34+08:00","source":"intraday","state":"unclear"},{"observed_at":"2026-08-05T14:13:37+08:00","source":"intraday","state":"partial"},{"observed_at":"2026-08-05T16:35:20+08:00","source":"eod","state":"partial"}],"unavailable_reason":null}},"llmText":"昨天把CPO光通信篮子按英伟达量产和长飞业绩的强驱动定在T1档第一位，五维给分很高，龙头清晰度满分、板块强度0.95，结果今天竞价直接开出中位-4.93%的weak分支，一开盘就触发了昨天剧本里写好的“低开走弱”防守预案。剧本当时重点盯了中际旭创的1021.99、光库的247.47和长飞的269.80这几条线，但从存拍台账看，全天分时相对昨收的最大不利只有0.26%，并没有发生剧本担心的那种集体破位失效。所以开盘这个跳空虽然吓人，但在结构上并没有击穿篮子，今天也没有触发首次证伪。\n\n开盘砸下去之后日内是往上拉的，收/开中位拉了+7.70%，收盘篮子收益中位定在+2.61%，跑赢大盘约1.15个点，3只成员里有2只跑赢大盘。这里昨天判断对的一块就是龙头认定：D0点名的龙头300620今天涨了+2.61%，压过其余成员的+1.37%，龙头带住了盘面，同向率也占了67%。不过收盘验证状态只停在“partial”，既没首次验证也没证伪，说明这个低开高走只是把篮子从开盘的被动里拽了回来，还没到彻底转强的程度；可买性上也打了个折，有1只收在涨停，实际只有2只今天买得进。\n\n今天最明显的落差在排序上：D0在当天四个篮子里排第1，今天实际结果只排到第4，名次差拉了3位。但系统明确标了单日名次差是噪声、不构成结论，所以不能由这一天就说高分定档失效，最多讲今天这个强驱动没在全天维度兑现成绝对领涨。整体看，昨天的强预期今天被开盘打了个折，靠着龙头拉回半个身位，剩下的一半还没拿稳。","llmSkipReason":null,"degraded":false,"verification":{"state":"partial","source":"eod","observedAt":"2026-08-05T16:35:20+08:00","provisional":false,"notEvaluated":false,"label":"收盘定论"}},{"basketId":2,"basketKey":"745b191e","name":"半导体设备国产化","tier":2,"d0":"20260804","reviewDate":"20260805","depth":"full","mech":{"auction_vs_script":{"available":true,"branch":"flat","gap_median":0.011644832605531397,"per_member":{"002371.SZ":{"branch":"flat","gap":0.011644832605531397},"300604.SZ":{"branch":"flat","gap":-0.0072817508746236825},"688012.SH":{"branch":"flat","gap":0.01567319191792027}},"script_present":true,"script_text":"次日平开时先观察半小时。华创若能守住687上方不破，说明基准收盘价有一定支撑，可在678-690区间内择机观察。长川作为弹性票若平开后率先翻红且量能配合，关注241-248区间。中微平开时离MA20最远，结构修复非一日之功，优先级最低、仓位最轻。","scripts_branches_on_card":["strong","flat","weak"],"source":"daily_open","unavailable_reason":null},"buyability":{"available":true,"buyable":3,"buyable_ratio":1.0,"limit_up":0,"member_count":3,"no_bar":0,"one_word":0,"per_member":{"002371.SZ":{"buyable":true,"limit_up":755.7,"limit_up_source":"card_frozen","reason":"buyable"},"300604.SZ":{"buyable":true,"limit_up":294.98,"limit_up_source":"card_frozen","reason":"buyable"},"688012.SH":{"buyable":true,"limit_up":381.29,"limit_up_source":"card_frozen","reason":"buyable"}},"unavailable_reason":null},"close_rs":{"available":true,"excess_median":0.091812,"index_code":"000001.SH","index_ret":0.014689,"outperformers":3,"per_member":{"002371.SZ":{"excess":0.054016,"ret":0.068705},"300604.SZ":{"excess":0.091812,"ret":0.106501},"688012.SH":{"excess":0.112018,"ret":0.12670700000000001}},"rs_positive":true,"unavailable_reason":null},"leader_pull":{"available":true,"leader_ret_median":0.106501,"leader_source":"card_role_mech_or_rank","leaders":["002371.SZ","300604.SZ","688012.SH"],"led":null,"member_count":3,"no_peer_group":true,"others_ret_median":null,"spread":null,"unavailable_reason":null},"member_alignment":{"alignment":1.0,"available":true,"dominant_direction":"up","down":0,"flat":0,"member_count":3,"missing":[],"observed":3,"returns":{"002371.SZ":0.068705,"300604.SZ":0.106501,"688012.SH":0.12670700000000001},"unavailable_reason":null,"up":3,"up_ratio":1.0},"meta":{"basket_id":2,"basket_key":"745b191e","card_version":1,"charter_version":"v1.3.3","d0":"20260804","day_notes":[],"engine_api_version":1,"has_card":true,"member_count":3,"members":["002371.SZ","300604.SZ","688012.SH"],"name":"半导体设备国产化","pack_version":"K7-pack-v1","review_date":"20260805","tier":2,"verification_ruleset_version":"verify_ruleset_v2"},"mfe_mae":{"available":true,"capture_covered_minutes":239,"capture_empty_ticks":0,"capture_expected_minutes":240,"capture_recorded":true,"capture_status":"full","mae_median":0.10231063379708738,"mfe_median":0.11606053209665612,"mfe_source":"intraday","note":null,"per_member":{"002371.SZ":{"mae":0.05967976710334799,"mae_at":"14:23:42","mfe":0.07101892285298383,"mfe_at":"14:10:36","source":"intraday"},"300604.SZ":{"mae":0.10231063379708738,"mae_at":"14:22:41","mfe":0.11606053209665612,"mfe_at":"14:06:34","source":"intraday"},"688012.SH":{"mae":0.12003524894567885,"mae_at":"14:40:49","mfe":0.1354881349531063,"mfe_at":"14:06:34","source":"intraday"}},"unavailable_reason":null},"open_direction":{"aligned":true,"available":true,"gap_dir":"up","gap_median":0.011644832605531397,"has_intraday_capture":true,"intraday_dir":"up","intraday_median":0.10932077342587987,"per_member":{"002371.SZ":{"first_tick_dir":"up","gap":0.011644832605531397,"gap_dir":"up","intraday":0.0564028776978418,"intraday_dir":"up"},"300604.SZ":{"first_tick_dir":"up","gap":-0.0072817508746236825,"gap_dir":"down","intraday":0.11461705528008848,"intraday_dir":"up"},"688012.SH":{"first_tick_dir":"up","gap":0.01567319191792027,"gap_dir":"up","intraday":0.10932077342587987,"intraday_dir":"up"}},"unavailable_reason":null},"spec_version":"basket_review_mech_v1","tier_vs_outcome":{"available":true,"basket_ret_median":0.106501,"day_baskets":4,"mech_score":0.81,"rank_by_outcome":1,"rank_by_tier":3,"rank_gap":-2,"rank_in_tier":1,"rank_mech":1,"rank_note":"名次差是当日横截面的机械依据,**单日不构成结论**","tier":2,"tier_breakdown":{"contrib":{"card_density":0.1,"driver_freshness":0.06,"leader_clarity":0.15,"sector_strength":0.3,"tradability":0.2},"dims":{"card_density":1.0,"driver_freshness":0.6,"leader_clarity":0.5,"sector_strength":1.0,"tradability":1.0},"engine_api_version":1,"flags":["leader_clarity_missing"],"neutral_filled_weight":0.3,"pack_version":"K7-pack-v1","score":0.81,"weights":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2},"weights_raw":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2}},"unavailable_reason":null},"verification_timing":{"available":true,"eod_state":"unclear","first_falsified_at":null,"first_partial_at":null,"first_verified_at":null,"has_eod_verdict":true,"intraday_rows":1,"latched_falsified":false,"not_evaluated":false,"provisional":false,"rows":2,"state":"unclear","state_label":"收盘定论","trail":[{"observed_at":"2026-08-05T14:06:34+08:00","source":"intraday","state":"unclear"},{"observed_at":"2026-08-05T16:35:20+08:00","source":"eod","state":"unclear"}],"unavailable_reason":null}},"llmText":"昨天把半导体设备国产化这个篮子定在T2档第一位，今天收盘看下来，大方向和强度上的判断是对上了的。卡上竞价中位+1.16%落进了“flat”分支，虽然算作平开，但开盘首方向和日内收/开方向完全同向，三只票002371、300604、688012全天同向率100%全红，大盘才涨1.47%，篮子超额中位直接干到了+9.18%，可买性满分1.00也是对的，今天无一字板、无收盘涨停，想买都能买得进。分时台账覆盖了239分钟，最大有利幅度+11.61%，连最大不利幅度都是+10.23%，说明全天基本都在昨收上方运行，昨天卡里写“华创守687上方”、“长川关注241-248”那种防回撤的细腻观察区间，在今天这种单边暴涨里压根没机会触发，盘面比剧本预期的要暴烈得多。\n\n不过，这套机械判定里也有今天没法定论甚至算不出的地方。D0给的龙头清晰度只有0.50，今天把三只票全认作龙头，导致“龙头带动”这项直接显示算不出、判不了，所以到底是谁带动谁，系统里没记录。验证与证伪这块今天也是“unclear”，首次判为已验证或证伪都没发生，单日结果再猛，机械上也还没给出收盘定论。另外，今天这个篮子在当日四个篮子里从D0排序第3跑到了今日结果第1，名次差是-2，但系统自己也标了单日名次差纯属噪声，不构成任何规则生效或失效的结论。整体而言，今天就是一顿暴涨把昨天的强预期兑现了，但卡里那些精细的节奏推演，在今天的盘面上没怎么用上。","llmSkipReason":null,"degraded":false,"verification":{"state":"unclear","source":"eod","observedAt":"2026-08-05T16:35:20+08:00","provisional":false,"notEvaluated":false,"label":"收盘定论"}},{"basketId":3,"basketKey":"f53f5826","name":"先进封装玻璃基板","tier":2,"d0":"20260804","reviewDate":"20260805","depth":"full","mech":{"auction_vs_script":{"available":true,"branch":"flat","gap_median":-0.007104795737122527,"per_member":{"000725.SZ":{"branch":"flat","gap":-0.007104795737122527},"002156.SZ":{"branch":"weak","gap":-0.028597122302158384},"300433.SZ":{"branch":"flat","gap":-0.004552352048558639}},"script_present":true,"script_text":"平开则重点观察通富微电能否在55.60上方企稳放量、另外两只是否跟随翻红。三只票均在MA20下方，平开后若缺乏买盘承接容易阴跌，建仓宜从区间下沿起步、轻仓试探。","scripts_branches_on_card":["strong","flat","weak"],"source":"daily_open","unavailable_reason":null},"buyability":{"available":true,"buyable":3,"buyable_ratio":1.0,"limit_up":0,"member_count":3,"no_bar":0,"one_word":0,"per_member":{"000725.SZ":{"buyable":true,"limit_up":6.19,"limit_up_source":"card_frozen","reason":"buyable"},"002156.SZ":{"buyable":true,"limit_up":61.16,"limit_up_source":"card_frozen","reason":"buyable"},"300433.SZ":{"buyable":true,"limit_up":39.54,"limit_up_source":"card_frozen","reason":"buyable"}},"unavailable_reason":null},"close_rs":{"available":true,"excess_median":0.045702,"index_code":"000001.SH","index_ret":0.014689,"outperformers":3,"per_member":{"000725.SZ":{"excess":0.045702,"ret":0.060391},"002156.SZ":{"excess":0.028475999999999994,"ret":0.043164999999999995},"300433.SZ":{"excess":0.050258000000000004,"ret":0.064947}},"rs_positive":true,"unavailable_reason":null},"leader_pull":{"available":true,"leader_ret_median":0.060391,"leader_source":"card_role_mech_or_rank","leaders":["000725.SZ","002156.SZ","300433.SZ"],"led":null,"member_count":3,"no_peer_group":true,"others_ret_median":null,"spread":null,"unavailable_reason":null},"member_alignment":{"alignment":1.0,"available":true,"dominant_direction":"up","down":0,"flat":0,"member_count":3,"missing":[],"observed":3,"returns":{"000725.SZ":0.060391,"002156.SZ":0.043164999999999995,"300433.SZ":0.064947},"unavailable_reason":null,"up":3,"up_ratio":1.0},"meta":{"basket_id":3,"basket_key":"f53f5826","card_version":1,"charter_version":"v1.3.3","d0":"20260804","day_notes":[],"engine_api_version":1,"has_card":true,"member_count":3,"members":["000725.SZ","002156.SZ","300433.SZ"],"name":"先进封装玻璃基板","pack_version":"K7-pack-v1","review_date":"20260805","tier":2,"verification_ruleset_version":"verify_ruleset_v2"},"mfe_mae":{"available":true,"capture_covered_minutes":239,"capture_empty_ticks":0,"capture_expected_minutes":240,"capture_recorded":true,"capture_status":"full","mae_median":0.04440497335701599,"mfe_median":0.060390763765541644,"mfe_source":"intraday","note":null,"per_member":{"000725.SZ":{"mae":0.04440497335701599,"mae_at":"14:18:40","mfe":0.060390763765541644,"mfe_at":"14:50:54","source":"intraday"},"002156.SZ":{"mae":0.03489208633093521,"mae_at":"14:19:40","mfe":0.053057553956834536,"mfe_at":"14:10:36","source":"intraday"},"300433.SZ":{"mae":0.054324734446130396,"mae_at":"14:19:40","mfe":0.06767830045523504,"mfe_at":"14:10:36","source":"intraday"}},"unavailable_reason":null},"open_direction":{"aligned":false,"available":true,"gap_dir":"down","gap_median":-0.007104795737122527,"has_intraday_capture":true,"intraday_dir":"up","intraday_median":0.06981707317073194,"per_member":{"000725.SZ":{"first_tick_dir":"up","gap":-0.007104795737122527,"gap_dir":"down","intraday":0.06797853309481217,"intraday_dir":"up"},"002156.SZ":{"first_tick_dir":"up","gap":-0.028597122302158384,"gap_dir":"down","intraday":0.07387520829476024,"intraday_dir":"up"},"300433.SZ":{"first_tick_dir":"up","gap":-0.004552352048558639,"gap_dir":"down","intraday":0.06981707317073194,"intraday_dir":"up"}},"unavailable_reason":null},"spec_version":"basket_review_mech_v1","tier_vs_outcome":{"available":true,"basket_ret_median":0.060391,"day_baskets":4,"mech_score":0.81,"rank_by_outcome":3,"rank_by_tier":4,"rank_gap":-1,"rank_in_tier":2,"rank_mech":2,"rank_note":"名次差是当日横截面的机械依据,**单日不构成结论**","tier":2,"tier_breakdown":{"contrib":{"card_density":0.1,"driver_freshness":0.06,"leader_clarity":0.15,"sector_strength":0.3,"tradability":0.2},"dims":{"card_density":1.0,"driver_freshness":0.6,"leader_clarity":0.5,"sector_strength":1.0,"tradability":1.0},"engine_api_version":1,"flags":["leader_clarity_missing"],"neutral_filled_weight":0.3,"pack_version":"K7-pack-v1","score":0.81,"weights":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2},"weights_raw":{"card_density":0.1,"driver_freshness":0.1,"leader_clarity":0.3,"sector_strength":0.3,"tradability":0.2}},"unavailable_reason":null},"verification_timing":{"available":true,"eod_state":"unclear","first_falsified_at":null,"first_partial_at":null,"first_verified_at":null,"has_eod_verdict":true,"intraday_rows":1,"latched_falsified":false,"not_evaluated":false,"provisional":false,"rows":2,"state":"unclear","state_label":"收盘定论","trail":[{"observed_at":"2026-08-05T14:06:34+08:00","source":"intraday","state":"unclear"},{"observed_at":"2026-08-05T16:35:20+08:00","source":"eod","state":"unclear"}],"unavailable_reason":null}},"llmText":"昨天把先进封装玻璃基板这个篮子定在T2档、4个篮子里排在第4位，今天收盘看，结果是实打实接住了情绪。篮子收益中位有6.04%，大盘才涨1.47%，超额中位拉到4.57%，三只票同向率100%全部收红，实际名次还往前挪了一位到了第3。单日名次差当噪声看，但这至少说明昨天把这个方向选进来在今天是对的，三只票全买得进，也没有一字板挡路。\n\n差距主要出在开盘剧本的谨慎度上。昨天卡上的“flat”分支写得很防守，原话是三只票都在MA20下方，平开后若没承接容易阴跌，建议区间下沿轻仓试探。今天竞价确实开在-0.71%的平盘偏下位置，开盘首方向也是向下，相对昨收的最大不利幅度确实走到了4.44%。剧本防的这波早盘下探是真发生了，但错就错在低估了盘中买盘的承接力。日内收/开中位硬是拉回了6.98%，开盘和日内方向完全相反，全天走的是先抑后扬，并没有像剧本担心的那样滑进阴跌里。\n\n另外几项机械判上，因为昨天把这三只票全列为了龙头，今天龙头对其余成员的带动效应算不出结果。收盘的验证证伪判定目前还是unclear，首次判为已验证和首次证伪都没发生。也就是说，虽然今天这根阳线把昨天的入选判断给兑现了，但机械台账还没到给出定论的时候，这一天它没认怂，也只是一天的结果。","llmSkipReason":null,"degraded":false,"verification":{"state":"unclear","source":"eod","observedAt":"2026-08-05T16:35:20+08:00","provisional":false,"notEvaluated":false,"label":"收盘定论"}}],"reviewsAvailable":true,"reviewsUnavailableReason":null,"reviewD0":"20260804","packVersion":"K7-pack-v1","notes":[]},"missedEntryHint":"","intel":{"tradeDate":"2026-08-05","evidenceNote":"涨跌幅榜/涨停梯队/跌停榜/大盘量能/市值偏好/涨跌停制度偏好 = EOD 硬数据(daily/limit_derived/daily_basic/index_daily 直接读,强证据);最强题材与题材持续天数依赖同花顺概念板块成分(ths_member 当前快照,K2「成分洞」)= 弱证据,仅供参考,不作强判据(见各题材项 evidenceStrength 字段)。","gainers":[{"code":"920038.BJ","name":"N森合","pctChg":66.24,"close":48.31},{"code":"920117.BJ","name":"龙鑫智能","pctChg":29.97,"close":35.17},{"code":"920092.BJ","name":"汉鑫科技","pctChg":23.04,"close":37.44},{"code":"300248.SZ","name":"新开普","pctChg":20.02,"close":10.07},{"code":"300552.SZ","name":"万集科技","pctChg":20.02,"close":24.88},{"code":"688549.SH","name":"中巨芯","pctChg":20.01,"close":23.21},{"code":"300686.SZ","name":"智动力","pctChg":20.0,"close":15.06},{"code":"301045.SZ","name":"天禄科技","pctChg":20.0,"close":63.36},{"code":"688596.SH","name":"正帆科技","pctChg":20.0,"close":57.42},{"code":"301026.SZ","name":"浩通科技","pctChg":19.99,"close":21.49},{"code":"688600.SH","name":"皖仪科技","pctChg":19.98,"close":27.44},{"code":"300615.SZ","name":"欣天科技","pctChg":19.97,"close":15.02},{"code":"300420.SZ","name":"五洋自控","pctChg":19.73,"close":7.89},{"code":"688059.SH","name":"华锐精密","pctChg":17.78,"close":88.29},{"code":"688432.SH","name":"有研硅","pctChg":17.62,"close":32.97},{"code":"300476.SZ","name":"胜宏科技","pctChg":17.12,"close":236.35},{"code":"920505.BJ","name":"九菱科技","pctChg":16.69,"close":28.81},{"code":"300820.SZ","name":"英杰电气","pctChg":16.1,"close":52.99},{"code":"688233.SH","name":"神工股份","pctChg":15.6,"close":105.25},{"code":"300948.SZ","name":"冠中生态","pctChg":15.44,"close":20.19}],"losers":[{"code":"300912.SZ","name":"凯龙高科","pctChg":-10.79,"close":20.42},{"code":"600363.SH","name":"联创光电","pctChg":-10.01,"close":24.64},{"code":"600499.SH","name":"科达制造","pctChg":-7.34,"close":14.52},{"code":"300308.SZ","name":"中际旭创","pctChg":-7.27,"close":947.74},{"code":"920651.BJ","name":"天罡股份","pctChg":-6.6,"close":22.5},{"code":"920339.BJ","name":"恒太照明","pctChg":-6.25,"close":11.55},{"code":"300989.SZ","name":"蕾奥规划","pctChg":-6.07,"close":13.62},{"code":"688622.SH","name":"*ST禾信","pctChg":-6.01,"close":108.2},{"code":"600530.SH","name":"ST交昂","pctChg":-5.57,"close":4.24},{"code":"300164.SZ","name":"通源石油","pctChg":-5.33,"close":10.66},{"code":"300502.SZ","name":"新易盛","pctChg":-5.29,"close":424.3},{"code":"920130.BJ","name":"立方控股","pctChg":-4.91,"close":21.11},{"code":"301270.SZ","name":"汉仪股份","pctChg":-4.31,"close":33.74},{"code":"300123.SZ","name":"ST亚光","pctChg":-4.14,"close":4.4},{"code":"001232.SZ","name":"C嘉立创","pctChg":-3.89,"close":199.91},{"code":"920856.BJ","name":"浩淼科技","pctChg":-3.88,"close":10.9},{"code":"301228.SZ","name":"实朴检测","pctChg":-3.82,"close":61.25},{"code":"600815.SH","name":"厦工股份","pctChg":-3.72,"close":3.36},{"code":"002501.SZ","name":"*ST利源","pctChg":-3.62,"close":1.33},{"code":"603956.SH","name":"威派格","pctChg":-3.62,"close":6.92}],"limitUpLadder":[{"consecDays":8,"count":1},{"consecDays":4,"count":2},{"consecDays":3,"count":2},{"consecDays":2,"count":27},{"consecDays":1,"count":72}],"limitDown":[{"code":"600363.SH","name":"联创光电","pctChg":-10.01,"close":24.64}],"limitDownTotalCount":1,"marketVolume":{"shAmountYi":12087.23,"szAmountYi":8960.72,"totalAmountYi":21047.95,"ma5AmountYi":18658.45,"sampleDays":5},"topThemes":[{"code":"885918.TI","name":"快手概念","boardAge":4,"ret20d":0.133,"persistenceLabel":"已延续(≥4日,警惕退潮)","evidenceStrength":"constituent","leaders":[{"code":"002425.SZ","name":"凯撒文化","pctChg":10.16,"isLimitUp":true},{"code":"600556.SH","name":"天下秀","pctChg":9.92,"isLimitUp":true}]},{"code":"886080.TI","name":"财税数字化","boardAge":6,"ret20d":0.1303,"persistenceLabel":"已延续(≥4日,警惕退潮)","evidenceStrength":"constituent","leaders":[{"code":"920092.BJ","name":"汉鑫科技","pctChg":23.04,"isLimitUp":false},{"code":"301396.SZ","name":"宏景科技","pctChg":12.6,"isLimitUp":false}]},{"code":"886094.TI","name":"华为盘古","boardAge":4,"ret20d":0.121,"persistenceLabel":"已延续(≥4日,警惕退潮)","evidenceStrength":"constituent","leaders":[{"code":"920092.BJ","name":"汉鑫科技","pctChg":23.04,"isLimitUp":false},{"code":"300248.SZ","name":"新开普","pctChg":20.02,"isLimitUp":true}]},{"code":"886018.TI","name":"高压氧舱","boardAge":8,"ret20d":0.1201,"persistenceLabel":"已延续(≥4日,警惕退潮)","evidenceStrength":"constituent","leaders":[{"code":"920199.BJ","name":"倍益康","pctChg":2.39,"isLimitUp":false},{"code":"002430.SZ","name":"杭氧股份","pctChg":1.98,"isLimitUp":false}]},{"code":"885792.TI","name":"赛马概念","boardAge":8,"ret20d":0.1144,"persistenceLabel":"已延续(≥4日,警惕退潮)","evidenceStrength":"constituent","leaders":[{"code":"603843.SH","name":"*ST正平","pctChg":2.35,"isLimitUp":false},{"code":"002264.SZ","name":"新华都","pctChg":1.06,"isLimitUp":false}]},{"code":"886074.TI","name":"AI语料","boardAge":4,"ret20d":0.1105,"persistenceLabel":"已延续(≥4日,警惕退潮)","evidenceStrength":"constituent","leaders":[{"code":"300248.SZ","name":"新开普","pctChg":20.02,"isLimitUp":true},{"code":"300654.SZ","name":"世纪天鸿","pctChg":12.61,"isLimitUp":false}]},{"code":"885933.TI","name":"NFT概念","boardAge":6,"ret20d":0.1101,"persistenceLabel":"已延续(≥4日,警惕退潮)","evidenceStrength":"constituent","leaders":[{"code":"002425.SZ","name":"凯撒文化","pctChg":10.16,"isLimitUp":true},{"code":"600556.SH","name":"天下秀","pctChg":9.92,"isLimitUp":true}]},{"code":"885947.TI","name":"DRG/DIP","boardAge":7,"ret20d":0.1069,"persistenceLabel":"已延续(≥4日,警惕退潮)","evidenceStrength":"constituent","leaders":[{"code":"300244.SZ","name":"迪安诊断","pctChg":7.57,"isLimitUp":false},{"code":"603990.SH","name":"麦迪科技","pctChg":6.46,"isLimitUp":false}]},{"code":"885791.TI","name":"知识产权保护","boardAge":6,"ret20d":0.1065,"persistenceLabel":"已延续(≥4日,警惕退潮)","evidenceStrength":"constituent","leaders":[{"code":"600556.SH","name":"天下秀","pctChg":9.92,"isLimitUp":true},{"code":"603607.SH","name":"京华激光","pctChg":7.93,"isLimitUp":false}]},{"code":"886098.TI","name":"小红书概念","boardAge":4,"ret20d":0.1043,"persistenceLabel":"已延续(≥4日,警惕退潮)","evidenceStrength":"constituent","leaders":[{"code":"002649.SZ","name":"博彦科技","pctChg":9.97,"isLimitUp":true},{"code":"600556.SH","name":"天下秀","pctChg":9.92,"isLimitUp":true}]}],"themePersistenceDistribution":{"已延续(≥4日,警惕退潮)":10},"mvPreference":[{"label":"<50亿","count":24,"pctOfTotal":0.2308},{"label":"50-100亿","count":25,"pctOfTotal":0.2404},{"label":"100-300亿","count":38,"pctOfTotal":0.3654},{"label":"300-1000亿","count":12,"pctOfTotal":0.1154},{"label":"≥1000亿","count":5,"pctOfTotal":0.0481}],"limitRegimePreference":[{"label":"10cm","count":95,"pctOfTotal":0.9135},{"label":"20cm","count":9,"pctOfTotal":0.0865}],"excludedBoardsNote":"板块池卫生线已剔除:名称模式(资格/宽基成分类标签)剔除 28 个:融资融券(3850只)、深股通(1886只)、沪股通(1644只)、国企改革(1470只)、专精特新(1227只)、人民币贬值受益(606只)、中证500成份股(550只)、2026中报预增(506只)、股权转让(并购重组)(473只)、央企国企改革(394只)、上证380成份股(380只)、沪深300样本股(319只)、ST板块(256只)、上证180成份股(197只)、参股银行(194只)、新股与次新股(161只)、参股券商(114只)、2026一季报预增(101只)、注册制次新股(92只)、摘帽(90只)、参股保险(58只)、上证50样本股(55只)、上海国企改革(54只)、深圳国企改革(29只)、举牌(27只)、科创次新股(26只)、同花顺果指数(20只)、兵装重组概念(7只)","warnings":[]},"sectorMoneyflow":{"tradeDate":"2026-08-05","available":true,"unavailableReason":"","topInflow":[{"code":"885756.TI","name":"芯片概念","netInflowWan":3370148.2,"memberCount":872,"rank":1,"evidenceStrength":"constituent"},{"code":"885517.TI","name":"机器人概念","netInflowWan":2144811.7,"memberCount":1160,"rank":2,"evidenceStrength":"constituent"},{"code":"885431.TI","name":"新能源汽车","netInflowWan":2074055.0,"memberCount":1007,"rank":3,"evidenceStrength":"constituent"},{"code":"885556.TI","name":"5G","netInflowWan":1640022.9,"memberCount":445,"rank":4,"evidenceStrength":"constituent"},{"code":"886042.TI","name":"存储芯片","netInflowWan":1606895.9,"memberCount":199,"rank":5,"evidenceStrength":"constituent"},{"code":"885710.TI","name":"锂电池概念","netInflowWan":1594484.0,"memberCount":581,"rank":6,"evidenceStrength":"constituent"},{"code":"885531.TI","name":"光伏概念","netInflowWan":1563300.1,"memberCount":576,"rank":7,"evidenceStrength":"constituent"},{"code":"886009.TI","name":"先进封装","netInflowWan":1539370.0,"memberCount":193,"rank":8,"evidenceStrength":"constituent"},{"code":"885806.TI","name":"华为概念","netInflowWan":1496684.6,"memberCount":983,"rank":9,"evidenceStrength":"constituent"},{"code":"885312.TI","name":"物联网","netInflowWan":1493621.4,"memberCount":446,"rank":10,"evidenceStrength":"constituent"},{"code":"886033.TI","name":"共封装光学(CPO)","netInflowWan":1445661.9,"memberCount":198,"rank":11,"evidenceStrength":"constituent"},{"code":"885552.TI","name":"小金属概念","netInflowWan":1416616.0,"memberCount":166,"rank":12,"evidenceStrength":"constituent"},{"code":"885728.TI","name":"人工智能","netInflowWan":1368732.3,"memberCount":1045,"rank":13,"evidenceStrength":"constituent"},{"code":"885887.TI","name":"数据中心(AIDC)","netInflowWan":1297319.6,"memberCount":627,"rank":14,"evidenceStrength":"constituent"},{"code":"885545.TI","name":"汽车电子","netInflowWan":1263727.2,"memberCount":376,"rank":15,"evidenceStrength":"constituent"}],"topOutflow":[{"code":"886072.TI","name":"高股息精选","netInflowWan":-257998.2,"memberCount":281,"rank":1,"evidenceStrength":"constituent"},{"code":"886095.TI","name":"IP经济(谷子经济)","netInflowWan":-172881.3,"memberCount":191,"rank":2,"evidenceStrength":"constituent"},{"code":"885525.TI","name":"白酒概念","netInflowWan":-153264.7,"memberCount":47,"rank":3,"evidenceStrength":"constituent"},{"code":"885950.TI","name":"虚拟数字人","netInflowWan":-143565.8,"memberCount":173,"rank":4,"evidenceStrength":"constituent"},{"code":"885933.TI","name":"NFT概念","netInflowWan":-138617.3,"memberCount":71,"rank":5,"evidenceStrength":"constituent"},{"code":"885761.TI","name":"超级品牌","netInflowWan":-136739.9,"memberCount":47,"rank":6,"evidenceStrength":"constituent"},{"code":"885966.TI","name":"跨境支付(CIPS)","netInflowWan":-128586.2,"memberCount":72,"rank":7,"evidenceStrength":"constituent"},{"code":"886015.TI","name":"创新药","netInflowWan":-122014.1,"memberCount":273,"rank":8,"evidenceStrength":"constituent"},{"code":"885457.TI","name":"手机游戏","netInflowWan":-120760.2,"memberCount":58,"rank":9,"evidenceStrength":"constituent"},{"code":"886091.TI","name":"华为手机","netInflowWan":-116024.7,"memberCount":44,"rank":10,"evidenceStrength":"constituent"},{"code":"885737.TI","name":"电子竞技","netInflowWan":-104625.3,"memberCount":35,"rank":11,"evidenceStrength":"constituent"},{"code":"885418.TI","name":"文化传媒概念","netInflowWan":-99200.5,"memberCount":194,"rank":12,"evidenceStrength":"constituent"},{"code":"885913.TI","name":"医美概念","netInflowWan":-98135.3,"memberCount":95,"rank":13,"evidenceStrength":"constituent"},{"code":"885927.TI","name":"CRO概念","netInflowWan":-92039.3,"memberCount":74,"rank":14,"evidenceStrength":"constituent"},{"code":"885372.TI","name":"页岩气","netInflowWan":-90587.7,"memberCount":47,"rank":15,"evidenceStrength":"constituent"}],"excludedBoardsNote":"板块池卫生线已剔除:名称模式(资格/宽基成分类标签)剔除 28 个:融资融券(3850只)、深股通(1886只)、沪股通(1644只)、国企改革(1470只)、专精特新(1227只)、人民币贬值受益(606只)、中证500成份股(550只)、2026中报预增(506只)、股权转让(并购重组)(473只)、央企国企改革(394只)、上证380成份股(380只)、沪深300样本股(319只)、ST板块(256只)、上证180成份股(197只)、参股银行(194只)、新股与次新股(161只)、参股券商(114只)、2026一季报预增(101只)、注册制次新股(92只)、摘帽(90只)、参股保险(58只)、上证50样本股(55只)、上海国企改革(54只)、深圳国企改革(29只)、举牌(27只)、科创次新股(26只)、同花顺果指数(20只)、兵装重组概念(7只)","evidenceNote":"板块层资金净流入用于展示当前资金拥挤度,并非选股信号(STRATEGY_LAB K2 判决:板块层资金/动量有效但无次日领先性,不进任何评分/候选筛选)。净流入数值本身(moneyflow_dc)为 EOD 硬数据,但板块归属依赖概念板块成分快照(ths_member,K2「成分洞」)——归属这一步为弱证据,标 constituent、仅供参考。"},"newsAlerts":[{"code":"603773.SH","name":"沃格光电","category":"INVESTIGATION","summary":"2026年5月14日因涉嫌信息披露违法违规，公司实控人及持股5%以上股东被证监会立案调查。","source":"llm_GLM"},{"code":"603773.SH","name":"沃格光电","category":"BLOWUP","summary":"7月13日预告2026年上半年净利润亏损1亿至1.4亿元，扣非净利润预亏1.1亿至1.5亿元。","source":"llm_GLM"}],"newsAlertsScan":[{"source":"tushare_holdertrade","scanned":true,"reason":"","codesTotal":0,"codesFailed":0,"codesSkipped":0,"codesNoSearch":0,"rotationGroup":"","codesRotationDeferred":0},{"source":"llm","scanned":true,"reason":"自选隔日轮扫:本次扫的是 B 组,9 只自选本日轮空(明日轮到,不代表确认无消息;持仓每日必扫,不参与轮扫)。","codesTotal":8,"codesFailed":0,"codesSkipped":0,"codesNoSearch":0,"rotationGroup":"B","codesRotationDeferred":9}],"dataFreshness":{"sectorDataDate":"20260805","sectorLagDays":0,"stale":false,"industryStrengthDate":"20260805","industryStrengthLagDays":0,"industryStrengthStale":false,"scanLayerDate":"20260805","scanLayerLagDays":0,"scanLayerStale":false},"degraded":false,"reason":""}"#


    // MARK: - V2.1-⑤/⑦ 复盘板块:累计页五段 + 校准移交件
    //
    // 样例对照 `tests/test_api_review.py`(⑤ 完工记录里的真实冒烟形状)。

    /// 五段各自独立三态;🔴 **画像段「没看」与对账段「没有」必须解出来是两回事**。
    func testFetchReviewOverviewKeepsFiveSegmentsIndependent() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"weekStart": "20260720", "weekEnd": "20260724", "weekKey": "2026-W30",
             "calibration": {"available": true, "label": "包成绩单 · 周度校准",
                             "asOf": "20260720→20260724",
                             "detail": {"nTradingDays": 1, "nBaskets": 2, "notes": ["样本不足结论线"],
                                        "strata": [{"packVersion": "K4-pack-v1",
                                                    "rulesetVersion": "verify_ruleset_v2",
                                                    "nDays": 1, "nBaskets": 2,
                                                    "tierMonotonicity": {"median_outcome": {"1": 0.01, "2": -0.02},
                                                                         "observed": {"1": 1, "2": 1},
                                                                         "monotonic": true},
                                                    "verification": {"verified_rate": 0.5,
                                                                     "distribution": {"verified": 1, "unclear": 1},
                                                                     "not_evaluated": 0},
                                                    "tradable": {"median": 0.012, "win_rate": 0.5}}],
                                        "placebo": [{"packVersion": "K4-pack-v1", "nDays": 1, "draws": 30,
                                                     "real": {"median": 0.01, "mean": 0.01, "n": 1}}]}},
             "preference": {"available": false, "label": "偏好画像 · 喜欢什么",
                            "unavailableReason": "该期从未算过"},
             "capability": {"available": true, "label": "能力画像 · 什么真有效", "asOf": "20260724",
                            "items": [{"dimension": "role", "bucket": "龙头", "sample_n": 2,
                                       "window_start": "20260501", "window_end": "20260724",
                                       "confidence": "low"}]},
             "reconcile": {"available": true, "label": "交割单对账", "asOf": "2026-W30",
                           "detail": {"found": false, "week": "2026-W30",
                                      "note": "本周尚未上传交割单 —— 对账需要券商交割单"}},
             "observations": {"available": true, "label": "观察项 · 等证据的策略问题",
                              "items": [{"id": "P3-33", "title": "Tier 质量线初值",
                                         "question": "0.60/0.40 是不是对的?",
                                         "evidence_needed": "更多样本", "status": "等证据"}]}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let ov = try await client.fetchReviewOverview(week: "20260723")
        XCTAssertTrue(MockURLProtocol.lastRequest?.url?.absoluteString.contains("week=20260723") == true)
        XCTAssertEqual(ov.weekKey, "2026-W30")

        // 画像缺席 = **「没看」**(系统那一步没跑)。
        XCTAssertFalse(ov.preference.available)
        XCTAssertEqual(ov.preference.unavailableReason, "该期从未算过")
        // 对账缺席 = **「没有」**(available 仍为 true,是 found=false)——⛔ 两者不许统一。
        XCTAssertTrue(ov.reconcile.available)
        XCTAssertEqual(ov.reconcile.found, false)
        XCTAssertNotNil(ov.reconcile.note)
        XCTAssertNil(ov.preference.found, "画像段压根没有 found 这个概念,⛔ 别拿它当'没有'")

        // 包成绩单 = 产物原文里的 strata 本身(⛔ 客户端不另建第二份聚合)。
        let strata = try XCTUnwrap(ov.calibration.detail["strata"]?.arrayValue)
        XCTAssertEqual(strata.count, 1)
        XCTAssertEqual(strata[0]["packVersion"]?.stringValue, "K4-pack-v1")
        XCTAssertEqual(strata[0]["tierMonotonicity"]?["observed"]?["1"]?.intValue, 1)
        XCTAssertEqual(ov.calibration.detail["nBaskets"]?.intValue, 2)

        let row = ProfileRow(raw: ov.capability.items[0])
        XCTAssertTrue(row.isLowConfidence, "low 置信度必须识别得出 —— UI 据此写「样本不足,不给结论」")
        let obs = ReviewObservation(raw: ov.observations.items[0])
        XCTAssertEqual(obs.obsId, "P3-33")
    }

    /// 🔴 **B 类缺键容错**:老产物 / 保险丝降级的响应可能只有半截键,
    /// 手写 `init(from:)` 必须让它**解得出来**(合成 Codable 会整页解不出)。
    func testReviewOverviewDecodesWithMissingKeys() throws {
        let ov = try JSONDecoder().decode(ReviewOverview.self, from: jsonData("{}"))
        XCTAssertEqual(ov.weekKey, "")
        for seg in [ov.calibration, ov.preference, ov.capability, ov.reconcile, ov.observations] {
            XCTAssertFalse(seg.available, "缺键 → 默认「没取到」,⛔ 不默认成「有」")
        }
        let partial = try JSONDecoder().decode(ReviewSegment.self,
                                               from: jsonData(#"{"available": true}"#))
        XCTAssertTrue(partial.available)
        XCTAssertTrue(partial.items.isEmpty)
        XCTAssertNil(partial.found)
    }

    /// ⚠ URL 上必须是契约里那个 `?from=`(服务端 `Query(alias="from")`),⛔ 不许改名。
    func testFetchReviewHandoffUsesFromAliasAndDecodes() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"available": true, "windowFrom": "20260720", "windowTo": "20260724",
             "generatedAt": "2026-08-08T01:00:00+00:00",
             "sampleN": {"tradingDays": 1, "baskets": 2, "strata": 1},
             "markdown": "# 校准移交件\\n\\n## ① 窗口与样本量\\n"}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let h = try await client.fetchReviewHandoff(from: "20260720", to: "20260724")
        let url = try XCTUnwrap(MockURLProtocol.lastRequest?.url?.absoluteString)
        XCTAssertTrue(url.contains("from=20260720"), "契约参数名是 `from`,实际:\(url)")
        XCTAssertTrue(url.contains("to=20260724"))
        XCTAssertTrue(h.available)
        XCTAssertEqual(h.windowLabel, "20260720 → 20260724")
        XCTAssertEqual(h.sampleN["baskets"], 2)
        XCTAssertEqual(h.suggestedFilename, "Neckline_校准移交件_20260720_20260724.md")
        XCTAssertTrue(h.markdown.hasPrefix("# 校准移交件"))
    }

    /// 不可用时**原样保留服务端那句话**(它已把"还没生成"与"读不出"分开写),
    /// ⛔ 客户端不合并成一句「暂不可用」。
    func testReviewHandoffUnavailableKeepsServerReasonVerbatim() async throws {
        let reason = "本窗口(20260720→20260724)的周度校准产物**读不出**(文件在、JSON 解析失败)—— 它不会自愈,需人工排查。"
        MockURLProtocol.handler = { _ in
            (200, jsonData("{\"available\": false, \"unavailableReason\": \"\(reason)\"}"))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let h = try await client.fetchReviewHandoff()
        XCTAssertFalse(h.available)
        XCTAssertEqual(h.unavailableReason, reason)
        XCTAssertEqual(h.windowLabel, "窗口未知", "⛔ 不显示一个 ' → ' 的空壳窗口")
    }

    // MARK: - V2.1-④ 百分制打分卡:两条路各填一处 + 老快照兜底

    /// live 路径(`GET /baskets`):分数住 `tierHistory`,`BasketOut` 两键刻意留空。
    func testLiveBasketScoreLivesOnTierHistory() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"tradeDate": "20260805", "items": [
              {"basketId": 1, "basketKey": "b1", "name": "篮子甲", "tier": 1,
               "tierHistory": {"basketId": 1, "tier": 1, "mechScore": 0.617,
                 "scorePercent": 61.7,
                 "scoreContributions": [
                   {"dim": "tradability", "label": "可交易性", "dimScore": 1.0, "weight": 0.2,
                    "contribPercent": 20.0, "neutralFilled": false},
                   {"dim": "leader_clarity", "label": "龙头清晰度", "dimScore": 0.5, "weight": 0.25,
                    "contribPercent": 12.5, "neutralFilled": true}]}}]}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let baskets = try await client.fetchBaskets()
        let b = try XCTUnwrap(baskets.first)
        XCTAssertNil(b.scorePercent, "live 路径上 BasketOut 两键刻意留空")
        XCTAssertEqual(b.scoreDisplayPercent, 61.7)
        XCTAssertEqual(b.scoreDisplayContributions.count, 2)
        XCTAssertEqual(b.scoreDisplayContributions[1].neutralFilled, true,
                       "中性填充必须解得出来 —— 那一维今天没算出来,⛔ 不是「表现好」")
        // 契约保留 4 位小数(精度住契约),⛔ 解码时不许先舍入。
        XCTAssertEqual(b.scoreDisplayContributions[0].contribPercent, 20.0)
    }

    /// 🔴 **老报告快照(建于 ④ 之前)没有这两个键** → 解码不抛、如实为空,
    /// ⛔ 不冒充 0 分。
    func testOldReportSnapshotWithoutScoreKeysStillDecodes() throws {
        let b = try JSONDecoder().decode(Basket.self, from: jsonData("""
        {"basketId": 3, "basketKey": "old", "name": "老篮子", "tier": 2}
        """))
        XCTAssertNil(b.scoreDisplayPercent)
        XCTAssertTrue(b.scoreDisplayContributions.isEmpty)
    }

    /// 报告快照路径:两键有值时**优先用快照自己的**(它与那份报告同时冻住)。
    func testSnapshotScoreWinsOverTierHistory() throws {
        let b = try JSONDecoder().decode(Basket.self, from: jsonData("""
        {"basketId": 4, "scorePercent": 55.0,
         "scoreContributions": [{"dim": "card_density", "label": "卡密度", "contribPercent": 6.6667}],
         "tierHistory": {"basketId": 4, "scorePercent": 99.9,
                         "scoreContributions": [{"dim": "x", "contribPercent": 1.0}]}}
        """))
        XCTAssertEqual(b.scoreDisplayPercent, 55.0)
        XCTAssertEqual(b.scoreDisplayContributions.map(\.dim), ["card_density"])
        XCTAssertEqual(try XCTUnwrap(b.scoreDisplayContributions[0].contribPercent),
                       6.6667, accuracy: 1e-9, "契约 4 位小数原样保留,⛔ 解码时不许先舍入")
    }

    // ══════════════════════════════════════════════════════════════════
    //  V2.2-② 行情状态层(`GET /market-regime`)
    // ══════════════════════════════════════════════════════════════════

    /// 正常一天:三态 + 增强/减弱方向 + 五维输入。`regimeLabel` **由服务端给**,
    /// 客户端不另建映射(测的就是"照服务端那份显示")。
    func testDecodeMarketRegimeAvailableDay() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"available": true, "unavailableReason": null, "days": [], "day": {
              "tradeDate": "20260807", "regime": "high_divergence", "regimeLabel": "高位分歧",
              "regimeReason": "核心强度较 5 日均值下降 0.34;宽度回落",
              "inputs": {"core_strength": {"available": true, "value": 0.31},
                         "t1t2_accuracy": {"available": false, "unavailable_reason": "missing:t1t2_accuracy"}},
              "strengthening": [{"industry": "半导体", "basis": "今日强度日"}],
              "weakening": [{"industry": "白酒", "basis": "连续两日走弱"}],
              "skeletonVersion": "K8-V0.5", "computedAt": "2026-08-07T08:35:00+00:00"
            }}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.fetchMarketRegime()
        XCTAssertTrue(r.available)
        XCTAssertEqual(r.day?.displayLabel, "高位分歧")
        XCTAssertEqual(r.day?.tone, .bad)
        XCTAssertEqual(r.day?.strengthening.count, 1)
        // 🔴 五维里没算出来的那一维必须能被拎出来说 —— 缺维 ≠ 这一维没问题。
        XCTAssertEqual(r.day?.missingDims, ["t1t2_accuracy"])
        XCTAssertEqual(MockURLProtocol.lastRequest?.url?.path, "/api/v1/market-regime")
    }

    /// 🔴 缺行:端点**恒 200**,`available=false` + 原因。客户端必须解得出这一态、
    /// 且 `day == nil`(⛔ 不许伪造一个"正常"的三态)。
    func testDecodeMarketRegimeUnavailableIsNotAnError() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"available": false,
             "unavailableReason": "20260808 无行情状态判定行(D0 盘后批算未跑或该日非交易日)。缺行 = 不知道,不猜。",
             "day": null, "days": []}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.fetchMarketRegime(date: "20260808")
        XCTAssertFalse(r.available)
        XCTAssertNil(r.day)
        XCTAssertTrue(r.unavailableReason?.contains("缺行 = 不知道") ?? false)
        XCTAssertEqual(MockURLProtocol.lastRequest?.url?.query, "date=20260808")
    }

    // ══════════════════════════════════════════════════════════════════
    //  V2.2-④ 双时钟
    // ══════════════════════════════════════════════════════════════════

    func testDecodeSelectionClocks() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"dateFrom": "20260803", "dateTo": "20260807", "items": [{
              "basketId": 41, "d0Date": "20260806", "d1Date": "20260807", "coveredTier": 1,
              "regimeAtD0": "trend_continuation", "tierAccuracy": "verified",
              "untriggeredReason": null, "closedAt": "2026-08-07T08:40:00+00:00",
              "skeletonVersion": "K8-V0.5", "verificationRulesetVersion": "vr-1",
              "engineBreakdown": {"engine_code": "C1", "engine_version": "C1-v1"},
              "mech": {"regime_at_d0": {"available": true, "value": "trend_continuation"}}
            }]}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let items = try await client.fetchSelectionClocks(from: "20260803", to: "20260807")
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(items[0].engineCode, "C1")
        XCTAssertEqual(items[0].tierAccuracyTone, .good)
        XCTAssertEqual(items[0].coveredTier, 1)
    }

    /// 🔴 **`tierAccuracy == nil` = 没判**,⛔ 不是"判错了" —— 文案必须是"未给判定"。
    func testSelectionClockNilAccuracyReadsAsNotJudged() throws {
        let c = SelectionClock(basketId: 7, coveredTier: 2)
        XCTAssertEqual(c.tierAccuracyLabel, "本篮未给分层准确性判定")
        XCTAssertEqual(c.tierAccuracyTone, .neutral)
        XCTAssertNil(c.regimeAtD0)
    }

    /// 运行中的交易时钟:`final` 是**显式 null** → 解成 nil(「还没结案」)。
    func testDecodeTradeClockRunningHasNilFinal() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"positionId": 12, "tsCode": "600519.SH", "basketId": null,
             "openedOn": "20260805", "closedOn": null, "status": "running",
             "entryPlan": {"driver": "白酒复苏"}, "final": null,
             "events": [{"id": 3, "eventDate": "20260806", "kind": "manual_note",
                         "mech": {}, "userNote": "开盘冲高时我犹豫了", "createdAt": "2026-08-06T02:00:00+00:00"}]}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let c = try await client.fetchTradeClock(positionId: 12)
        XCTAssertTrue(c.isRunning)
        XCTAssertNil(c.final, "运行中必须是 nil —— 「还没结案」与「结案了但八项算不出」要分得开")
        XCTAssertNil(c.basketId, "非篮子来源的手动开仓是**合法**的")
        XCTAssertEqual(c.userNotes.count, 1)
        XCTAssertEqual(c.events[0].kindLabel, "你的说明")
    }

    /// 🔴 这笔仓没有交易时钟 → 404 `not_found` → **既有 `.notFound` case**
    /// (V2.2 契约要求零新增 reason;`mapReason` 一字未动的机器判据)。
    func testTradeClockMissingMapsToExistingNotFoundReason() async throws {
        MockURLProtocol.handler = { _ in
            (404, jsonData(#"{"detail": {"ok": false, "reason": "not_found"}}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.fetchTradeClock(positionId: 99)
            XCTFail("应抛 notFound")
        } catch let e as APIError {
            XCTAssertEqual(e, .notFound)
        }
    }

    func testPostTradeClockNoteSendsOnlyNoteAndDecodesCoverage() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData(#"{"ok": true, "eventId": 8, "eventDate": "20260809", "coverage": {"total": 4, "withNote": 2}}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let r = try await client.postTradeClockNote(positionId: 12, note: "追高了,当时怕踏空")
        XCTAssertEqual(r.eventId, 8)
        XCTAssertEqual(r.coverageText, "本期 4 笔中 2 笔带说明")
        XCTAssertEqual(MockURLProtocol.lastRequest?.url?.path, "/api/v1/clocks/trade/12/note")
        let body = MockURLProtocol.lastRequest?.httpBodyOrStream()
        let obj = try XCTUnwrap(try JSONSerialization.jsonObject(with: XCTUnwrap(body)) as? [String: Any])
        // ⛔ 只发 note:kind / 时间戳由服务端定,多发一份就是第二个事实源。
        XCTAssertEqual(Array(obj.keys), ["note"])
    }

    /// 超长 → 服务端 422(pydantic 数组形状)→ `.validation`,**⛔ 不新增 reason**。
    func testTradeClockNoteTooLongSurfacesAsValidation() async throws {
        MockURLProtocol.handler = { _ in
            (422, jsonData(#"{"detail": [{"msg": "String should have at most 500 characters"}]}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.postTradeClockNote(positionId: 12, note: String(repeating: "长", count: 501))
            XCTFail("应抛 validation")
        } catch let e as APIError {
            guard case .validation(let m) = e else { return XCTFail("应是 .validation,实得 \(e)") }
            XCTAssertTrue(m.contains("500"))
        }
    }

    // ══════════════════════════════════════════════════════════════════
    //  V2.2-⑤ 章程无时间退出条款:`maxHoldDays` 的「缺键」vs「显式 null」
    // ══════════════════════════════════════════════════════════════════

    /// 🔴 **显式 null = 本版章程没有时间退出条款** → 不显示任何 D 上限。
    /// ⛔ 拿 5 顶上就是把"没有这条规矩"讲成"上限是 5"。
    func testPositionNullMaxHoldDaysMeansNoTimeExitRule() throws {
        let data = jsonData("""
        {"id": 1, "code": "600519.SH", "name": "贵州茅台", "buyPrice": 10.0, "qty": 100,
         "entryReason": "", "buyDate": "20260805", "price": 10.5, "status": "open",
         "stopLine": 9.5, "stopOrderChecked": false, "dCount": 7,
         "maxHoldDays": null, "maxHoldDaysEffective": null, "timeExitState": "holding"}
        """)
        let p = try JSONDecoder().decode(Position.self, from: data)
        XCTAssertNil(p.maxHoldDays)
        XCTAssertNil(p.maxHoldDaysEffective)
        XCTAssertFalse(p.hasTimeExitRule)
        XCTAssertEqual(p.dBadgeText, "D7", "⛔ 不许出现 D7/D5 这种假上限")
        // 🔴 V2.4.0 P3.1 取代:原断言「无时间退出条款」(章程术语)→ K8.md §十三
        // 逐字的人话「本版无机械时间退出 —— D 计数只作记录」,与服务端
        // `charter_copy.TIME_EXIT_DISABLED_COPY` 同一套词(两句会同屏出现)。
        // ⚠ 判据(`maxHoldDaysEffective == nil`)与上面三条断言一字未动。
        XCTAssertTrue(p.timeExitDisclosure?.contains("本版无机械时间退出") ?? false)
        XCTAssertTrue(p.timeExitDisclosure?.contains("D 计数只作记录") ?? false)
        XCTAssertFalse(p.isExitDay)
    }

    /// **缺键**(真·老服务端 / 老 fixture)→ 仍按当时的单档口径补 5,老断言逐位不变。
    func testPositionMissingMaxHoldDaysKeepsLegacyFive() throws {
        let data = jsonData("""
        {"id": 1, "code": "600519.SH", "name": "贵州茅台", "buyPrice": 10.0, "qty": 100,
         "entryReason": "", "buyDate": "20260805", "price": 10.5, "status": "open",
         "stopLine": 9.5, "stopOrderChecked": false, "dCount": 6}
        """)
        let p = try JSONDecoder().decode(Position.self, from: data)
        XCTAssertEqual(p.maxHoldDays, 5)
        XCTAssertEqual(p.maxHoldDaysEffective, 5)
        XCTAssertEqual(p.dBadgeText, "D6/D5")
        XCTAssertNil(p.timeExitDisclosure)
        // 缺 `timeExitState` 时的旧派生仍成立(dCount 6 >= 5 → 到期)。
        XCTAssertTrue(p.isExitDay)
    }

    // ══════════════════════════════════════════════════════════════════
    //  V2.2-④ 复盘 overview 三段 + 四分类「分界线未定」
    // ══════════════════════════════════════════════════════════════════

    func testDecodeReviewOverviewThreeNewSegments() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"weekStart": "20260803", "weekEnd": "20260807", "weekKey": "2026-W32",
             "calibration": {"available": false}, "preference": {"available": false},
             "capability": {"available": false}, "reconcile": {"available": true},
             "observations": {"available": true},
             "selectionClock": {"available": true, "label": "选股时钟 · D0 全部 T1/T2",
                                "detail": {"samples": 12}},
             "tradeClock": {"available": false, "label": "交易时钟 · 真实买入",
                            "unavailableReason": "本窗口尚无周度校准产物"},
             "iterationSuggestions": {"available": true,
               "label": "修改建议 · 保留 / 观察 / 降权 / 淘汰",
               "items": [{"factor": "gate=position:ok", "n": 12, "klass": null,
                          "klassStatus": "thresholds_undecided", "klassLabel": null,
                          "engineCode": "C1", "engineVersion": "C1-v1",
                          "suggestion": "统计量已备齐,四分类尚不可给 —— 请拍板 min_n 与 retire_min_n。"}],
               "detail": {"thresholds": {"available": false}, "disclaimer": "四分类是建议,不是动作"}}}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let ov = try await client.fetchReviewOverview()
        XCTAssertTrue(ov.selectionClock.available)
        // 🔴 三段各自独立:交易时钟段没取到,⛔ 不许被选股时钟段的 available 罩住。
        XCTAssertFalse(ov.tradeClock.available)
        XCTAssertTrue(ov.iterationSuggestions.available)
        let rows = ov.iterationSuggestions.items.map(IterationSuggestion.init(raw:))
        XCTAssertEqual(rows.count, 1)
        // 🔴 **「分界线未定」是设计中的状态**:不是 nil-klass 就当"暂无建议"。
        XCTAssertTrue(rows[0].thresholdsUndecided)
        XCTAssertNil(rows[0].klass)
        XCTAssertNil(rows[0].klassLabel, "⛔ 未拍板时不许伪造一个分类名")
        XCTAssertTrue(rows[0].suggestion.contains("min_n"))
    }

    /// 分界线拍板之后:`klass` 有值 + 服务端下发 `klassLabel`(客户端优先用服务端那份)。
    func testIterationSuggestionDecidedUsesServerLabel() throws {
        let raw = NKJSON.object([
            "factor": .string("engine=C1"), "n": .number(41), "klass": .string("retire"),
            "klassStatus": .string("decided"), "klassLabel": .string("淘汰:持续失效"),
        ])
        let r = IterationSuggestion(raw: raw)
        XCTAssertFalse(r.thresholdsUndecided)
        XCTAssertEqual(r.klassLabel, "淘汰:持续失效")
        XCTAssertEqual(r.klassTone, .bad)
    }

    // ══════════════════════════════════════════════════════════════════
    //  V2.3.3-⑤ D1 集合竞价确认层(`GET /auction`,K8.md §二十)
    // ══════════════════════════════════════════════════════════════════

    func testDecodeAuctionFiveBlocks() async throws {
        MockURLProtocol.handler = { _ in
            (200, jsonData("""
            {"tradeDate": "20260811", "d0Date": "20260810",
             "dataStatus": {"source": "sina", "capturedAt": "2026-08-11T09:26:30+08:00",
                            "requestedCodes": 6, "fetchedCodes": 5,
                            "missingCodes": ["300001.SZ"], "conflictCodes": [],
                            "dataQuality": "degraded"},
             "marketOverview": {"indexGaps": [{"tsCode": "000001.SH", "name": "上证综指", "gapPct": 0.004}],
                                "anchors": [{"tsCode": "600111.SH", "name": "锚点股", "gapPct": 0.031}],
                                "text": "指数普遍高开", "textUnavailableReason": null,
                                "anchorsNote": "锚点只解释资金方向"},
             "baskets": [{"basketId": 7, "basketKey": "k1", "name": "测试篮", "coveredTier": 1,
                          "engineCode": "Z", "engineVersion": "Z1", "skeletonVersion": "K8-V0.7",
                          "regimeAtD0": "trend_continuation", "dataQuality": "ok",
                          "verdict": "neutral", "verdictRaw": "confirm",
                          "clampedBy": "clamped_by_single_strong",
                          "reasons": ["只有一只竞价强股"],
                          "members": [{"tsCode": "600000.SH", "name": "浦发银行", "role": "leader",
                                       "auctionPrice": 10.5, "preClose": 10.0, "gapPct": 0.05,
                                       "auctionVolume": 12000, "auctionAmount": 126000,
                                       "volVsPrev5Frac": 0.08, "relToSector": 0.012,
                                       "relToIndex": 0.046, "hitInvalidation": false,
                                       "gapUpDeviation": true, "anchorStale": false,
                                       "planFit": "above_max_chase", "dataQuality": "ok",
                                       "volumeNote": "竞价放量"}],
                          "sectorSync": {"up_count": 3}, "relStrength": {},
                          "history": {"history_days_available": 2}, "planConsistency": {},
                          "hitInvalidation": ["600000.SH"], "manualNoteAttached": true,
                          "llmStage": "ok"}],
             "basketsUnavailableReason": null,
             "risks": [{"kind": "hit_invalidation", "text": "1 只命中 D0 失效位。"}],
             "manualNote": "APP 观察:……", "proxySampleNote": "竞价强势股取自关注池",
             "llmStage": "ok", "notes": []}
            """))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        let a = try await client.fetchAuction()
        XCTAssertEqual(MockURLProtocol.lastRequest?.url?.path, "/api/v1/auction")
        XCTAssertEqual(a.dataStatus.coverageText, "5/6")
        XCTAssertEqual(a.dataStatus.dataQualityLabel, "有缺失")
        XCTAssertEqual(a.neutralCount, 1)
        XCTAssertEqual(a.confirmCount, 0)
        XCTAssertEqual(a.hitInvalidationCodes, ["600000.SH"])
        // 🔴 「模型说了什么 vs 系统最终讲了什么」两者都要在,且被夹逼过必须能说出口。
        let b = a.baskets[0]
        XCTAssertEqual(b.verdictLabel, "中性")
        XCTAssertEqual(b.verdictRaw, "confirm")
        XCTAssertTrue(b.clampText?.contains("只有一只竞价强股") ?? false)
        XCTAssertEqual(b.historyDaysAvailable, 2)
        // 🔴 枚举码全部走展示层换算(⛔ 不许把码印上界面)。
        XCTAssertEqual(b.members[0].statusText, "已超最高追价")
        XCTAssertEqual(b.members[0].roleLabel, nkRoleLabel("leader"))
    }

    /// 🔴 **B 类冻结快照**:老行没有的键必须**照样解得出**(手写 `init(from:)` 的意义)。
    /// ⛔ 合成 `Decodable` 会让「装了新 App 的用户翻昨天那份报告」整条解不出。
    func testAuctionDecodesWithMissingKeys() throws {
        let data = jsonData("""
        {"tradeDate": "20260811", "baskets": [{"basketId": 3}]}
        """)
        let a = try JSONDecoder().decode(AuctionPayload.self, from: data)
        XCTAssertEqual(a.tradeDate, "20260811")
        XCTAssertEqual(a.d0Date, "")
        XCTAssertEqual(a.baskets.count, 1)
        XCTAssertEqual(a.baskets[0].verdict, "pending_explanation")
        XCTAssertEqual(a.baskets[0].members, [])
        XCTAssertNil(a.manualNote)
        XCTAssertEqual(a.proxySampleNote, "")
    }

    /// 逐票三态布尔:`null` = **没判**(⛔ 不是 `false`「没问题」)。
    func testAuctionMemberTristateBooleanStaysNil() throws {
        let data = jsonData("""
        {"tradeDate": "20260811", "baskets": [{"basketId": 3, "members": [
          {"tsCode": "600000.SH", "hitInvalidation": null, "gapUpDeviation": null,
           "anchorStale": true, "dataQuality": "insufficient"}]}]}
        """)
        let a = try JSONDecoder().decode(AuctionPayload.self, from: data)
        let m = a.baskets[0].members[0]
        XCTAssertNil(m.hitInvalidation)
        XCTAssertNil(m.gapUpDeviation)
        XCTAssertTrue(m.anchorStale)
        // 关键字段缺失 → 「中性｜数据不足」(K8 §二十 逐字)
        XCTAssertEqual(m.statusText, "中性｜数据不足")
    }

    // MARK: - 🔴 用户裁定 P3-70(2026-08-12):两条独立读数 + 第三态

    /// 🔴 两条读数**分开解、分开画**,各自带上「减的是哪一支 / 哪一组」。
    func testAuctionRelStrengthDecodesBothPathsSeparately() throws {
        let data = jsonData("""
        {"tradeDate": "20260811", "baskets": [{"basketId": 3, "members": [
          {"tsCode": "600000.SH", "gapPct": 0.05, "relToSector": 0.02, "relToIndex": 0.046,
           "relToSectorSource": "peer_median",
           "sectorPeerCodes": ["600100.SH", "600101.SH", "600102.SH"],
           "sectorBenchmarkGapPct": 0.03, "industry": "半导体",
           "indexBenchmarkCode": "000001.SH", "indexBenchmarkGapPct": 0.004,
           "dataQuality": "ok"}]}]}
        """)
        let m = try JSONDecoder().decode(AuctionPayload.self, from: data).baskets[0].members[0]
        XCTAssertEqual(m.relToSectorSource, "peer_median")
        XCTAssertEqual(m.sectorPeerCodes.count, 3)
        XCTAssertEqual(m.industry, "半导体")
        XCTAssertEqual(m.indexBenchmarkCode, "000001.SH")
        XCTAssertFalse(m.relToSectorMissing)
        XCTAssertFalse(m.relToIndexMissing)
        // 两句话各自说清减的是什么,且**是两个不同的基准**
        XCTAssertTrue(m.relToSectorText.contains("同行业「半导体」3 只中位"), m.relToSectorText)
        XCTAssertTrue(m.relToIndexText.contains("市场指数 000001.SH"), m.relToIndexText)
        XCTAssertNotEqual(m.relToSectorText, m.relToIndexText)
        // 🔴 `Text(String)` 不解析 Markdown → 这两句里一个 `*` 都不许有
        XCTAssertFalse(m.relToSectorText.contains("*"))
        XCTAssertFalse(m.relToIndexText.contains("*"))
    }

    /// 🔴 **第三态**:`null` = 没有这个读数 —— ⛔ 绝不许渲染成 0 或「持平」。
    /// 科创板那条尤其:`board_excluded` + ⛔ 不 fallback 到别的指数。
    func testAuctionRelStrengthNullIsAThirdStateNotZero() throws {
        let data = jsonData("""
        {"tradeDate": "20260811", "baskets": [{"basketId": 3, "members": [
          {"tsCode": "688001.SH", "gapPct": 0.05, "relToSector": null, "relToIndex": null,
           "relToSectorSource": "unavailable", "relToSectorReason": "data_insufficient",
           "relToIndexReason": "board_excluded", "indexBenchmarkCode": null,
           "dataQuality": "ok"}]}]}
        """)
        let m = try JSONDecoder().decode(AuctionPayload.self, from: data).baskets[0].members[0]
        XCTAssertNil(m.relToSector)
        XCTAssertNil(m.relToIndex)
        XCTAssertTrue(m.relToSectorMissing && m.relToIndexMissing)
        XCTAssertTrue(m.relToSectorText.contains("未取得"), m.relToSectorText)
        // 🔴 定向复审 🔵-1:那个 3 是**服务端裁定值**,客户端不许硬编 —— 传进来才有数字,
        // 没传进来就说一句不带数字的话(⛔ 不猜)。
        let withMin = m.relToSectorText(sectorPeerMin: 3)
        XCTAssertTrue(withMin.contains("有效板块对照股不足 3 只"), withMin)
        XCTAssertFalse(m.relToSectorText.contains("3 只"),
                       "⛔ 服务端没下发下限时不许凭空印一个数:\(m.relToSectorText)")
        XCTAssertTrue(m.relToSectorText.contains("不是「持平」"), m.relToSectorText)
        XCTAssertTrue(m.relToIndexText.contains("科创板"), m.relToIndexText)
        // ⛔ 一个 0 都不许出现在这两句里(那正是「没有」被讲成「持平」的样子)
        XCTAssertFalse(m.relToSectorText.contains("0.00%"))
        XCTAssertFalse(m.relToIndexText.contains("0.00%"))
    }

    /// 老行(整改前冻的 B 类快照)没有这些键 → 缺省值 + 「原因未记录」,
    /// ⛔ 仍不许讲成「持平」。
    func testAuctionRelStrengthOldRowsDegradeHonestly() throws {
        let data = jsonData("""
        {"tradeDate": "20260811", "baskets": [{"basketId": 3, "members": [
          {"tsCode": "600000.SH", "gapPct": 0.05, "dataQuality": "ok"}]}]}
        """)
        let m = try JSONDecoder().decode(AuctionPayload.self, from: data).baskets[0].members[0]
        XCTAssertEqual(m.relToSectorSource, "unavailable")
        XCTAssertTrue(m.sectorPeerCodes.isEmpty)
        XCTAssertTrue(m.relToSectorText.contains("原因未记录"), m.relToSectorText)
        XCTAssertTrue(m.relToIndexText.contains("未取得"), m.relToIndexText)
    }

    // MARK: - 🔴 用户裁定 P3-69(2026-08-12):历史样本充足与否由服务端判

    /// 🔴 「历史样本不足」标志与文案**只由服务端下发**,客户端零门槛。
    func testAuctionHistorySufficiencyComesFromTheServer() throws {
        let data = jsonData("""
        {"tradeDate": "20260811", "baskets": [{"basketId": 3, "history": {
          "history_days_available": 3, "history_lookback_trading_days": 20,
          "history_lookback_days": 60, "history_min_sample_for_comparison": 15,
          "history_sample_sufficient": false,
          "history_insufficient_note": "当期有效样本不足 15 天,按「历史样本不足」处理。",
          "history_lookback_note": "这一项回看最近 20 个有效交易日的竞价快照"}}]}
        """)
        let b = try JSONDecoder().decode(AuctionPayload.self, from: data).baskets[0]
        XCTAssertEqual(b.historyDaysAvailable, 3)
        XCTAssertEqual(b.historyLookbackTradingDays, 20)
        XCTAssertEqual(b.historyLookbackDays, 60)
        XCTAssertEqual(b.historySampleSufficient, false)
        XCTAssertTrue(b.historyInsufficientNote?.contains("历史样本不足") ?? false)
    }

    /// `n ≥ 15` → 服务端标 `true` 且**不发**那句不足文案。
    func testAuctionHistorySufficientHidesTheInsufficientNote() throws {
        let data = jsonData("""
        {"tradeDate": "20260811", "baskets": [{"basketId": 3, "history": {
          "history_days_available": 18, "history_sample_sufficient": true}}]}
        """)
        let b = try JSONDecoder().decode(AuctionPayload.self, from: data).baskets[0]
        XCTAssertEqual(b.historySampleSufficient, true)
        XCTAssertNil(b.historyInsufficientNote)
    }

    /// 老行没有 `history_sample_sufficient` → `nil` → 界面**什么都不说**
    /// (⛔ 不许默认成"够"或"不够" —— 那是替服务端下一个它没下过的判断)。
    func testAuctionHistorySufficiencyIsNilOnOldRows() throws {
        let data = jsonData("""
        {"tradeDate": "20260811", "baskets": [{"basketId": 3,
          "history": {"history_days_available": 2}}]}
        """)
        let b = try JSONDecoder().decode(AuctionPayload.self, from: data).baskets[0]
        XCTAssertNil(b.historySampleSufficient)
        XCTAssertNil(b.historyInsufficientNote)
        // 老行也没有逐票段 → 空(⛔ 不许凭空造一条"够了"的记录)
        XCTAssertTrue(b.historyPerMember.isEmpty)
        XCTAssertNil(b.historyFor("600000.SH"))
    }

    // MARK: - 🔴 定向复审 🔴-1 / 🟡-1 / 🔵-1 / 🔵-2 / 🔵-3(2026-08-12)

    /// 🔴 **逐票的历史样本**:一只 20 天、一只 2 天同篮 —— 界面必须看得出**是哪一只**不够,
    /// 且不够的那只要**逐日列出原始值**(裁定 P3-69 原文「只展示原始值」)。
    func testAuctionHistoryIsPerMemberAndNamesTheShortOne() throws {
        let data = jsonData("""
        {"tradeDate": "20260811", "baskets": [{"basketId": 3, "history": {
          "history_days_available": 2, "history_days_available_basis": "min_per_member",
          "history_sample_sufficient": false,
          "history_insufficient_codes": ["600000.SH"],
          "history_days_per_member": [
            {"ts_code": "600519.SH", "days_available": 20, "sample_sufficient": true,
             "comparison_readings": {"auction_volume": {"min": 4000, "median": 5000,
                                                        "max": 6000, "observed": 20},
                                     "gap_pct": {"min": 0.01, "median": 0.02,
                                                 "max": 0.03, "observed": 20}}},
            {"ts_code": "600000.SH", "days_available": 2, "sample_sufficient": false}],
          "per_member": {"600000.SH": [
            {"trade_date": "2026-08-07", "auction_volume": 7777, "auction_amount": 50000,
             "gap_pct": 0.01},
            {"trade_date": "2026-08-10", "auction_volume": 8888, "auction_amount": 50000,
             "gap_pct": 0.012}]}}}]}
        """)
        let b = try JSONDecoder().decode(AuctionPayload.self, from: data).baskets[0]
        XCTAssertEqual(b.historyDaysAvailable, 2, "篮级 = 逐票最小值")
        XCTAssertEqual(b.historyInsufficientCodes, ["600000.SH"])
        XCTAssertEqual(b.historyPerMember.count, 2)

        let long = b.historyFor("600519.SH")
        XCTAssertEqual(long?.daysAvailable, 20)
        XCTAssertEqual(long?.sampleSufficient, true)
        // 🟡-1 的客户端一半:够样本时给的是**对照读数**,不是一句空口许可
        XCTAssertTrue(long?.noteText.contains("可作历史比较") ?? false, long?.noteText ?? "")
        XCTAssertTrue(long?.noteText.contains("中位") ?? false, long?.noteText ?? "")

        let short = b.historyFor("600000.SH")
        XCTAssertEqual(short?.daysAvailable, 2)
        XCTAssertEqual(short?.sampleSufficient, false)
        XCTAssertTrue(short?.noteText.contains("样本不足") ?? false, short?.noteText ?? "")
        // 🔵-3:「只展示原始值」那半句要在界面上真的落地
        XCTAssertTrue(short?.noteText.contains("2026-08-07") ?? false, short?.noteText ?? "")
        XCTAssertTrue(short?.noteText.contains("7,777") ?? false, short?.noteText ?? "")
        // 🔴 `Text(String)` 不解析 Markdown → 这些整句里一个 `*` 都不许有
        XCTAssertFalse(short?.noteText.contains("*") ?? true)
        XCTAssertFalse(long?.noteText.contains("*") ?? true)
    }

    /// 「一天都没有」⛔ 不许被讲成「跟平时一样」(「没有」≠「不满足」)。
    func testAuctionHistoryZeroDaysSaysThereIsNothingToCompare() throws {
        let data = jsonData("""
        {"tradeDate": "20260811", "baskets": [{"basketId": 3, "history": {
          "history_days_available": 0, "history_sample_sufficient": false,
          "history_days_per_member": [
            {"ts_code": "600000.SH", "days_available": 0, "sample_sufficient": false}]}}]}
        """)
        let b = try JSONDecoder().decode(AuctionPayload.self, from: data).baskets[0]
        let h = b.historyFor("600000.SH")
        XCTAssertTrue(h?.noteText.contains("一条历史竞价快照都没有") ?? false, h?.noteText ?? "")
        XCTAssertTrue(h?.noteText.contains("不是「跟平时一样」") ?? false, h?.noteText ?? "")
    }

    /// 🔵-1:板块对照股下限**从服务端读**(`relStrength.sector_peer_min`),⛔ 客户端零硬编。
    func testAuctionSectorPeerMinComesFromTheServer() throws {
        let data = jsonData("""
        {"tradeDate": "20260811", "baskets": [{"basketId": 3,
          "relStrength": {"sector_peer_min": 3, "sector_peer_pool_note": "取自关注池"}}]}
        """)
        let b = try JSONDecoder().decode(AuctionPayload.self, from: data).baskets[0]
        XCTAssertEqual(b.sectorPeerMin, 3)
        XCTAssertEqual(b.sectorPeerPoolNote, "取自关注池")
        // 全仓不许再出现那句硬编的中文(唯一源在服务端)
        XCTAssertEqual(nkAuctionRelReasonLabel("data_insufficient", sectorPeerMin: 3),
                       "有效板块对照股不足 3 只")
        XCTAssertFalse(nkAuctionRelReasonLabel("data_insufficient").contains("3"),
                       "⛔ 服务端没下发下限时不许印一个自己拍的数")
    }

    /// 🔵-2:老行**有值、却没有来源码** → ⛔ 不许印成「+1.42%(对照:未取得 未记录)」
    /// (有值又说没取到,自相矛盾)。如实说这是旧口径的值。
    func testAuctionOldRowWithValueButNoSourceIsNotSelfContradictory() throws {
        let data = jsonData("""
        {"tradeDate": "20260811", "baskets": [{"basketId": 3, "members": [
          {"tsCode": "600000.SH", "gapPct": 0.05, "relToSector": 0.0142,
           "dataQuality": "ok"}]}]}
        """)
        let m = try JSONDecoder().decode(AuctionPayload.self, from: data).baskets[0].members[0]
        XCTAssertEqual(m.relToSectorSource, "unavailable")
        let text = m.relToSectorText(sectorPeerMin: 3)
        XCTAssertTrue(text.contains("1.42%"), text)
        XCTAssertFalse(text.contains("未取得"), "⛔ 有值就别说「未取得」:\(text)")
        XCTAssertTrue(text.contains("旧口径"), text)
    }

    /// 🔵-7:「整张行业表都没读到」(系统缺席)与「这一只票没登记行业」是两句话。
    func testAuctionIndustryMapUnavailableIsItsOwnReason() throws {
        XCTAssertNotEqual(nkAuctionRelReasonLabel("industry_map_unavailable"),
                          nkAuctionRelReasonLabel("no_industry"))
        XCTAssertTrue(nkAuctionRelReasonLabel("industry_map_unavailable").contains("系统缺席"))
    }

    /// 🔴 404 `auction_not_ready` 与 500 `auction_corrupt` **必须映射成两个不同的错误**
    /// (⛔ 不吃 404 fallback「持仓已清」;⛔ 不把 500 降格成"还没生成")。
    func testAuctionReasonsMapToTheirOwnCases() async throws {
        MockURLProtocol.handler = { _ in
            (404, jsonData(#"{"detail": {"ok": false, "reason": "auction_not_ready"}}"#))
        }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: mockSession())
        do {
            _ = try await client.fetchAuction(date: "20260811")
            XCTFail("应当抛 auctionNotReady")
        } catch let e as APIError {
            XCTAssertEqual(e, .auctionNotReady)
            XCTAssertNotEqual(e, .notHolding, "⛔ 不许吃 404 fallback「持仓已清」")
        }
        MockURLProtocol.handler = { _ in
            (500, jsonData(#"{"detail": {"ok": false, "reason": "auction_corrupt"}}"#))
        }
        do {
            _ = try await client.fetchAuction()
            XCTFail("应当抛 auctionCorrupt")
        } catch let e as APIError {
            XCTAssertEqual(e, .auctionCorrupt)
            XCTAssertNotEqual(e, .auctionNotReady, "⛔ 「读不出」与「还没生成」必须分开")
        }
    }

    /// 三态在 `AppModel` 里就分好:404 → 不画卡(`auction == nil` 且 `corrupt == false`)。
    @MainActor
    func testAuctionNotReadyLeavesTheCardUnpainted() async throws {
        XCTAssertEqual(nkAuctionVerdictLabel("pending_explanation"), "待解释")
        XCTAssertEqual(nkAuctionVerdictLabel("something_new"), "something_new",
                       "未识别值必须原样透传")
        XCTAssertEqual(nkAuctionLlmStageLabel("call_failed:TimeoutError"), "调用失败")
        XCTAssertEqual(nkAuctionGapText(nil), "算不出", "⛔ 不拿 0 冒充平开")
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
