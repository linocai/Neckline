import XCTest
@testable import Neckline

final class K9ContractTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(T.self, from: Data(json.utf8))
    }

    func testPackageDetailDecodesDualDatesAllChannelsAndVersionLineage() throws {
        let detail = try decode(ScoreboardPackageDetail.self, """
        {"batchId":"k9-v3-20260830-r1","selectionDate":"20260830","signalTradeDate":"20260828","d1TradeDate":"20260831","d2TradeDate":"20260901","revision":1,"state":"d1","coverageState":"partial","strategyVersion":"K9-v3","paramsPackageVersion":"k9-params-v3-r1","packVersion":"fp-4","labelContractVersion":"d2-v2","candidateCount":1,"createdAt":"2026-08-30T20:00:00","frozenContract":{},"candidates":[{"tsCode":"600001.SH","name":"甲","swL2Code":"801010.SI","swL2Name":"农林牧渔","channels":["p2","p3","p4"],"channelRanks":{"p2":1,"p3":2,"p4":3},"playbook":{"revision":2},"baseline":{},"thresholds":{},"d1":{"checklistVerdict":"pending_open","openVerdict":"observed","referencePrice":10.2,"raw":{},"closeState":"held","closeRaw":{}},"d2":{"selectionResult":"observed_realized","playbookResult":"target_reached","riskTag":"risk","raw":{"d2MaxReturn":0.03,"tradable":true}}}]}
        """)
        XCTAssertEqual(detail.selectionDate, "20260830")
        XCTAssertEqual(detail.signalTradeDate, "20260828")
        XCTAssertEqual(detail.candidates[0].channels, ["p2", "p3", "p4"])
        XCTAssertEqual(detail.candidates[0].channelRanks["p4"], 3)
        XCTAssertEqual(detail.candidates[0].playbookLabel, "预案第 2 版")
        XCTAssertEqual(detail.candidates[0].d1?.openVerdict, .observed)
        XCTAssertEqual(detail.candidates[0].d2?.selectionResult, .observedRealized)
    }

    func testChecklistHasExactlyThreeK9V3Segments() throws {
        let checklist = try decode(Checklist.self, """
        {"batchId":"b","selectionDate":"20260830","signalTradeDate":"20260828","tradeDate":"20260831","capturedAt":"x","strategyVersion":"K9-v3","d0Date":"20260830","dataQuality":"unavailable","footnote":"x","noQuoteCodes":[],"noPlaybookCodes":[],"notes":[],"segments":[
        {"verdict":"rejected","label":"已触发放弃","rows":[]},{"verdict":"unbuyable","label":"确认不可买","rows":[]},{"verdict":"pending_open","label":"待开盘后观察","rows":[]}]}
        """)
        XCTAssertEqual(checklist.segments.map(\.verdict), [.rejected, .unbuyable, .pendingOpen])
        XCTAssertEqual(K9OpenVerdict.allCases.count, 5) // 四种交易结论 + 分时源缺失的不可评价
    }

    func testD2EightCategoriesAreClosedAndRiskIsIndependent() {
        XCTAssertEqual(K9D2SelectionResult.allCases.count, 8)
        XCTAssertEqual(K9D2SelectionResult.unavailable.label, "不可评价")
        XCTAssertEqual(K9D1CloseState.allCases.count, 4)
    }

    func testDirectionAndPlaybookContractsBecomeReadablePresentation() throws {
        let direction = try decode(NKJSON.self, """
        {"state":"available","summary":"资金回流低位方向。","themes":[{"name":"农业","reason":"板块量价同步修复"}]}
        """)
        let presented = K9Presentation.direction(direction)
        XCTAssertEqual(presented.summary, "资金回流低位方向。")
        XCTAssertEqual(presented.themes, [K9DirectionTheme(name: "农业", reason: "板块量价同步修复")])

        let conditions = try decode(NKJSON.self, """
        {"holdAbove":9.25,"industry":{"minimumMemberCoverage":0.8,"relativeBenchmarkReturnAtOrAbove":0.015}}
        """)
        let fields = K9Presentation.readableFields(conditions)
        XCTAssertTrue(fields.contains(K9ReadableField(path: "holdAbove", label: "守住价", value: "9.25")))
        XCTAssertTrue(fields.contains(K9ReadableField(path: "industry.minimumMemberCoverage", label: "行业 · 最低成员覆盖", value: "80.0%")))
        XCTAssertTrue(fields.contains(K9ReadableField(path: "industry.relativeBenchmarkReturnAtOrAbove", label: "行业 · 相对基准涨幅至少", value: "1.5%")))
    }

    func testSelectionMissingParametersStaysNotRun() throws {
        let snapshot = try decode(SelectionSnapshot.self, """
        {"state":"not_run","headline":"今天没跑成 · 参数未配置","gaps":["参数未配置"],"listingSize":null,"stocks":[]}
        """)
        XCTAssertEqual(snapshot.state, .notRun)
        XCTAssertTrue(snapshot.parameterPackWasMissing)
        XCTAssertNil(snapshot.listingSize)
    }

    func testScoreboardDynamicCopyRendersDatesCountsAndPlanRevision() throws {
        let package = ScoreboardPackage(
            batchId: "b", selectionDate: "20260830", signalTradeDate: "20260828",
            d1TradeDate: "20260831", d2TradeDate: "20260901", revision: 2, state: .d1,
            coverageState: .partial, strategyVersion: "K9-v3", paramsPackageVersion: "k9-params-v3-r1",
            packVersion: "fp-4", labelContractVersion: "d2-v2", candidateCount: 7, createdAt: "x"
        )
        let detail = try decode(ScoreboardPackageDetail.self, """
        {"batchId":"b","selectionDate":"20260830","signalTradeDate":"20260828","d1TradeDate":"20260831","d2TradeDate":"20260901","revision":2,"state":"d1","coverageState":"partial","strategyVersion":"K9-v3","paramsPackageVersion":"k9-params-v3-r1","packVersion":"fp-4","labelContractVersion":"d2-v2","candidateCount":7,"createdAt":"x","frozenContract":{},"candidates":[]}
        """)

        XCTAssertEqual(ScoreboardText.cardSubtitle(package), "信号交易日 2026年8月28日 · 7 只")
        XCTAssertEqual(ScoreboardText.planRevision(package.revision), "预案第 2 版")
        XCTAssertEqual(ScoreboardText.selectionDate(detail.selectionDate), "选股日 2026年8月30日")
        XCTAssertEqual(ScoreboardText.stageDates(detail), "信号交易日 2026年8月28日 · D1 2026年8月31日 · D2 2026年9月1日")
    }

    func testNoRetiredP1OrRollingScorecardNamesRemainInProductionModels() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        let texts = ["Neckline/Networking/Models/K9Models.swift", "Neckline/Networking/Models/ScoreboardModels.swift"]
            .compactMap { try? String(contentsOf: root.appendingPathComponent($0)) }.joined()
        for forbidden in ["K9-v2", "activeQueueLimit", "strictCount", "relaxedCount", "SeatKind"] {
            XCTAssertFalse(texts.contains(forbidden), "retired symbol \(forbidden) must not return")
        }
    }
}
