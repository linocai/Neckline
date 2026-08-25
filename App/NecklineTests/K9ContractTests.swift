//
//  K9ContractTests.swift
//  NecklineTests — **V2.5.0 契约的客户端半边**(S12;取代已删的 `DTODecodeTests`
//  与 `AppModelTests` 里那 129 条 K8 用例)。
//
//  这批测试守的不是"能不能解码",而是**那几条不许退化的读法**:
//    · **三态是三句不同的话**,`empty` ≠ `notRun`,`listingSize == nil` ≠ 0;
//    · **核对表恰好两段**,`ChecklistVerdict` ⛔ 没有「成立」这个取值(裁定 10);
//    · **「还没定案」≠「观察」**(后者是 10:00 真看过之后的结论);
//    · **上方机械空间 ≠ 第一压力位**(裁定 1),⛔ 缺席不补 0;
//    · **NULL 不是 0**(覆盖率两个口径);
//    · **消息面三态**,`unverified` ⛔ 不许折成「无异常」;
//    · **行业分 / 选票分⛔ 无合计**。
//
//  ⚠ **fixture 一律逐字照服务端真形状写**(`neckline/api/app.py` 与各 `store.py` 的
//  `to_dict`),⛔ 不照客户端 struct 反推 —— 反推出来的 fixture 只能证明客户端自洽。
//

import XCTest
@testable import Neckline

final class K9ContractTests: XCTestCase {

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    // MARK: - 三态

    func testThreeReportStatesAreThreeDifferentThings() throws {
        let hasList = try decode(SelectionSnapshot.self, """
        {"state":"has_list","reportDate":"20260820","tradeDate":"20260820",
         "headline":"今天有这些 · 5 只(严格 5 / 放宽 0)","gaps":[],
         "listingSize":5,"strictCount":5,"relaxedCount":0,"stocks":[]}
        """)
        XCTAssertEqual(hasList.state, .hasList)
        XCTAssertEqual(hasList.listingSize, 5)

        let empty = try decode(SelectionSnapshot.self, """
        {"state":"empty","headline":"今天没有","gaps":[],"listingSize":0}
        """)
        XCTAssertEqual(empty.state, .empty)
        XCTAssertEqual(empty.listingSize, 0, "「今天没有」是跑通了、结果是 0 —— 这个 0 是真的")

        let notRun = try decode(SelectionSnapshot.self, """
        {"state":"not_run","headline":"今天没跑成 · 参数未配置(缺键 ranking.relayScoring)",
         "gaps":["参数未配置:缺键 ranking.relayScoring"],"listingSize":null}
        """)
        XCTAssertEqual(notRun.state, .notRun)
        // 🔴 这一条是整批里最要紧的:`null` ⛔ 不许被解成 0。
        XCTAssertNil(notRun.listingSize, "「今天没跑成」没有清单大小可言 —— ⛔ 不许解成 0")
        XCTAssertEqual(notRun.gaps.count, 1, "缺口必须逐条留着,⛔ 不许合并成一句")
        XCTAssertNotEqual(notRun.state, empty.state, "⛔ 「没跑成」与「没有」不是同一态")
        XCTAssertTrue(notRun.parameterPackWasMissing)
        XCTAssertFalse(empty.parameterPackWasMissing,
                       "没有报告或空清单都不能被界面说成参数未配置")
    }

    func testUnknownStateIsNilNotSilentlyMappedToAnyOfTheThree() throws {
        let weird = try decode(SelectionSnapshot.self, """
        {"state":"brand_new_state","headline":"?","gaps":[]}
        """)
        // ⛔ 未识别值不许静默归一 —— 把第四态显示成「今天没有」是最坏的一种谎。
        XCTAssertNil(weird.state)
        XCTAssertEqual(weird.headlineText, "?", "服务端给的首行优先,⛔ 客户端不改写")
    }

    func testEmptyStateIsNeutralNotAWarning() {
        // 「今天没有」是一个**可以被信任**的结论 —— ⛔ 不许标成警告色。
        XCTAssertEqual(K9ReportState.empty.tone, .neutral)
        XCTAssertEqual(K9ReportState.notRun.tone, .warn)
        XCTAssertEqual(K9ReportState.hasList.tone, .good)
    }

    // MARK: - 核对表:⛔ 结构上没有「成立」(裁定 10 / 守门 G20)

    func testChecklistVerdictHasExactlyTwoCasesAndNoConfirmed() {
        XCTAssertEqual(ChecklistVerdict.allCases.count, 2,
                       "🔴 核对表必须恰好两段(裁定 10)—— 加第三个 case = 违反裁定")
        XCTAssertEqual(Set(ChecklistVerdict.allCases.map(\.rawValue)),
                       ["rejected", "pending_open"])
        XCTAssertNil(ChecklistVerdict(rawValue: "confirmed"),
                     "⛔ 「成立」不是这个枚举的取值:9:29 那一拍结构上判不出它")
        // 段名里也不许出现「成立」二字。
        for v in ChecklistVerdict.allCases {
            XCTAssertFalse(v.label.contains("成立"), "段名 \(v.label) 里不许出现「成立」")
        }
    }

    func testChecklistPayloadDecodesTwoSegmentsAndKeepsTheFootnote() throws {
        let list = try decode(Checklist.self, """
        {"tradeDate":"20260821","d0Date":"20260820","capturedAt":"2026-08-21T09:27:11",
         "dataQuality":"fresh",
         "segments":[
           {"verdict":"rejected","label":"已触发放弃","rows":[
             {"tsCode":"600001.SH","name":"甲","pattern":"p1","verdict":"rejected",
              "segment":"已触发放弃","playbookVersion":1,
              "readings":{"auction_price":9.1,"prev_low":9.5,"open_price":null},
              "rejectionBranch":{"name":"放弃"},"quoteState":"fresh"}]},
           {"verdict":"pending_open","label":"待开盘后观察","rows":[]}],
         "noQuoteCodes":["000002.SZ"],"noPlaybookCodes":["000003.SZ"],
         "footnote":"成立由 10:00 结算,9:30–10:00 由我自己判定。","notes":[]}
        """)
        XCTAssertEqual(list.segments.count, 2)
        XCTAssertEqual(list.rejectedCount, 1)
        XCTAssertEqual(list.pendingCount, 0)
        XCTAssertFalse(list.footnote.isEmpty, "⛔ 脚注不许省略 —— 它是「没有成立段」的解释")
        XCTAssertEqual(list.noPlaybookCodes, ["000003.SZ"],
                       "没有冻结预案的票要逐只说出来:明早核对不了它们")
        // ⚠ 读不到的量(`open_price: null`)**不进读数表** —— ⛔ 不补 0。
        let row = try XCTUnwrap(list.segment(.rejected)?.rows.first)
        XCTAssertEqual(row.playbookRevisionLabel, "预案第 1 版")
        XCTAssertFalse(row.playbookRevisionLabel.hasPrefix("v"),
                       "次日核对表同样不能把预案修订号写成裸 vN")
        XCTAssertEqual(row.readingRows.count, 2, "9:29 读不到开盘价,那一项不该出现在表里")
        XCTAssertFalse(row.readingRows.contains { $0.label == nkMetricRefLabel("open_price") })
    }

    // MARK: - 三分支终值:「还没定案」⛔ 不是「观察」

    func testUndecidedIsNotObserved() throws {
        let snap = try decode(K9VerdictsSnapshot.self, """
        {"tradeDate":"20260821","verdicts":[
          {"tsCode":"600001.SH","d0Date":"20260820","pattern":"p1","playbookVersion":1,
           "auctionVerdict":"pending_open","verdict":null,"decidedStage":null,
           "auctionReadings":null,"open30Readings":null,"branches":[],"settledAt":null},
          {"tsCode":"600002.SH","d0Date":"20260820","pattern":"p3","playbookVersion":1,
           "auctionVerdict":"pending_open","verdict":"observed","decidedStage":"open30",
           "auctionReadings":null,"open30Readings":null,"branches":[],
           "settledAt":"2026-08-21T10:00:31"}]}
        """)
        XCTAssertEqual(snap.undecidedCount, 1)
        XCTAssertEqual(snap.count(.observed), 1)
        XCTAssertEqual(snap.decided.count, 1, "「还没定案」不进已定案明细")
        XCTAssertTrue(snap.verdicts[0].isUndecided)
        XCTAssertFalse(snap.verdicts[1].isUndecided,
                       "🔴 「观察」是 10:00 真看过之后的结论,⛔ 不是「还没定案」")
        XCTAssertEqual(nkDecidedStageLabel(nil), "尚未定案")
        XCTAssertEqual(nkDecidedStageLabel("open30"), "10:00 结算")
        XCTAssertEqual(nkDecidedStageLabel("auction"), "9:29 竞价定案")
    }

    func testObservedIsNeutralBecauseItEntersNoRatio() {
        // K9 §八:观察分支**不进任何正确率的分子分母** —— ⛔ 别把它画成好或坏。
        XCTAssertEqual(K9Verdict.observed.tone, .neutral)
        XCTAssertEqual(K9Verdict.confirmed.tone, .good)
        XCTAssertEqual(K9Verdict.rejected.tone, .bad)
    }

    // MARK: - 裁定 1:上方机械空间 ≠ 第一压力位

    func testUpsideRoomAbsentIsNilNotZero() throws {
        let p2Only = try decode(K9Stock.self, """
        {"tsCode":"600004.SH","name":"丁","swL2Code":"801080.SI","swL2Name":"电子",
         "patterns":["p2"],"primaryPattern":"p2","tier":"strict","seatKind":"floor","rank":3,
         "upsideRoomMechPct":null,"playbook":null,"newsState":null,
         "newsCategory":null,"klineComment":null,"explainOk":null}
        """)
        // 🔴 只被 p2 召回 → **本形态不看这一项**,⛔ 不是「上方没有空间」。
        XCTAssertNil(p2Only.upsideRoomMechPct)

        let p1 = try decode(K9Stock.self, """
        {"tsCode":"600005.SH","patterns":["p1"],"primaryPattern":"p1","tier":"strict",
         "rank":1,"upsideRoomMechPct":0.1234}
        """)
        XCTAssertEqual(p1.upsideRoomMechPct ?? 0, 0.1234, accuracy: 1e-9)
    }

    func testPlaybookLevelsAndUpsideRoomAreDifferentFields() throws {
        let stock = try decode(K9Stock.self, """
        {"tsCode":"600006.SH","patterns":["p1"],"primaryPattern":"p1","tier":"strict","rank":1,
         "upsideRoomMechPct":0.20,
         "playbook":{"tradeDate":"20260820","tsCode":"600006.SH","pattern":"p1",
          "levels":{"firstResistance":12.5,"secondResistance":13.8,"invalidation":9.9},
          "branches":[{"name":"成立","all":[{"op":"<=","lhs":"gap_pct","rhs":3.0},
                                            {"op":">=","lhs":"first30_low","rhs":10.2}]},
                      {"name":"放弃","all":[{"op":"<","lhs":"first30_low","rhs":9.9}]}],
          "default":"观察","version":1,"source":"llm","filledBy":"k9","filledAt":"2026-08-20T17:02:00"}}
        """)
        let pb = try XCTUnwrap(stock.playbook)
        // 两个量必须**各是各的**:一个是比例(机械),一个是价位(LLM)。
        XCTAssertEqual(stock.upsideRoomMechPct ?? 0, 0.20, accuracy: 1e-9)
        XCTAssertEqual(pb.levels.firstResistance, 12.5, accuracy: 1e-9)
        XCTAssertNotEqual(stock.upsideRoomMechPct, pb.levels.firstResistance)
        // 赔率 = (第一压力位 − 现价) ÷ (现价 − 失效位)。
        let odds = try XCTUnwrap(pb.levels.odds(close: 11.0))
        XCTAssertEqual(odds, (12.5 - 11.0) / (11.0 - 9.9), accuracy: 1e-9)
        XCTAssertNil(pb.levels.odds(close: 9.0), "收盘已在失效位下方 → ⛔ 不拿负数冒充赔率")
    }

    func testPlaybookConditionRhsIsEitherNumberOrMetricRefNeverBoth() throws {
        let branch = try decode(PlaybookBranch.self, """
        {"name":"成立","all":[{"op":">=","lhs":"first30_low","rhs":"prev_low"},
                              {"op":"<=","lhs":"gap_pct","rhs":3.0}]}
        """)
        XCTAssertEqual(branch.all.count, 2)
        XCTAssertEqual(branch.all[0].rhsMetric, "prev_low")
        XCTAssertNil(branch.all[0].rhsNumber)
        XCTAssertEqual(branch.all[1].rhsNumber ?? 0, 3.0, accuracy: 1e-9)
        XCTAssertNil(branch.all[1].rhsMetric)
        // 展示层换算:两种 rhs 都要读得通(⛔ 条件语法没有算术,这里也不做算术)。
        XCTAssertTrue(branch.all[0].text.contains(nkMetricRefLabel("prev_low")))
    }

    func testPlaybookVersionsAreAppendOnlyAndUserEditIsVisible() throws {
        let detail = try decode(K9StockDetail.self, """
        {"tradeDate":"20260820","tsCode":"600007.SH",
         "entry":{"name":"戊","patterns":["p3"],"primaryPattern":"p3","tier":"relaxed",
                  "seatKind":"free","rank":7,"swL2Code":"801080.SI","swL2Name":"电子"},
         "explain":null,
         "playbook":{"tradeDate":"20260820","tsCode":"600007.SH","pattern":"p3",
           "levels":{"firstResistance":20.0,"secondResistance":22.0,"invalidation":17.0},
           "branches":[],"default":"观察","version":2,"source":"user",
           "filledBy":"user","filledAt":"2026-08-20T21:10:00"},
         "playbookVersions":[
           {"tradeDate":"20260820","tsCode":"600007.SH","pattern":"p3",
            "levels":{"firstResistance":19.0,"secondResistance":21.0,"invalidation":17.0},
            "branches":[],"default":"观察","version":1,"source":"llm",
            "filledBy":"k9","filledAt":"2026-08-20T17:02:00"},
           {"tradeDate":"20260820","tsCode":"600007.SH","pattern":"p3",
            "levels":{"firstResistance":20.0,"secondResistance":22.0,"invalidation":17.0},
            "branches":[],"default":"观察","version":2,"source":"user",
            "filledBy":"user","filledAt":"2026-08-20T21:10:00"}],
         "playbookSlots":[
           {"key":"firstResistance","kind":"price","label":"第一压力位","hint":"预期离场价(元)"},
           {"key":"first30FloorPrice","kind":"price","label":"[A]","hint":"前 30 分钟不破的价位"}]}
        """)
        XCTAssertEqual(detail.playbookVersions.count, 2)
        // 🔴 append-only:v1 的数**一个字没改**。
        XCTAssertEqual(detail.playbookVersions[0].levels.firstResistance, 19.0, accuracy: 1e-9)
        XCTAssertEqual(detail.playbookVersions[0].source, "llm")
        XCTAssertEqual(detail.playbookVersions[0].revisionLabel, "预案第 1 版")
        XCTAssertEqual(detail.playbook?.revisionLabel, "预案第 2 版")
        XCTAssertFalse(detail.playbookVersions[0].revisionLabel.hasPrefix("v"),
                       "裸 vN 会被误读成 K9 策略版本")
        XCTAssertTrue(detail.playbook?.isUserEdited ?? false)
        // 🔴 要填哪几个数由**服务端下发**(⛔ 客户端不硬编一份键表)。
        XCTAssertEqual(detail.playbookSlots.map(\.key), ["firstResistance", "first30FloorPrice"])
        XCTAssertEqual(detail.playbookSlots[0].unit, "元")
    }

    // MARK: - 消息面三态

    func testNewsStateHasThreeDistinctReadingsAndUnverifiedIsNotClean() {
        XCTAssertEqual(nkNewsStateTone("clean"), .good)
        XCTAssertEqual(nkNewsStateTone("excluded"), .bad)
        // 🔴 「没查成」既不是好也不是坏 —— 它是一个要人知道的缺口。
        XCTAssertEqual(nkNewsStateTone("unverified"), .warn)
        XCTAssertNotEqual(nkNewsStateLabel("unverified"), nkNewsStateLabel("clean"))
        XCTAssertFalse(nkNewsStateLabel("unverified").contains("无异常"),
                       "⛔ 「未核实」不许写成「无异常」")
        // `nil` = 解释层根本没跑过这一只,与「查过没查成」也不是一回事。
        XCTAssertNotEqual(nkNewsStateLabel(nil), nkNewsStateLabel("unverified"))
        XCTAssertEqual(nkNewsStateTone(nil), .neutral)
    }

    func testExplainNoteDecodesOnlineCamelCaseKeys() throws {
        // 数据库内部是 snake_case；在线端点必须经 `_explain_api` 收口为 camelCase。
        // 客户端只消费 API 契约，不读取数据库内部形状。
        let note = try decode(K9ExplainNote.self, """
        {"tsCode":"600008.SH",
         "profile":{"company":"一句话","industryContext":"处境","position":"位置","recent":"近期"},
         "klineComment":"日K 评价","newsState":"unverified","newsCategory":null,
         "news":{},"llmOk":false,"filledBy":"explain","createdAt":"2026-08-20T17:00:00"}
        """)
        XCTAssertEqual(note.tsCode, "600008.SH")
        XCTAssertEqual(note.klineComment, "日K 评价")
        XCTAssertEqual(note.newsState, "unverified")
        XCTAssertFalse(note.llmOk, "⛔ 「跑了但没跑成」不许被解成 true")
        // 五句话画像按**固定顺序**取(⛔ 不按字典序 —— 那会把「它是什么公司」排到中间)。
        XCTAssertEqual(note.profileRows.map(\.text), ["一句话", "处境", "位置", "近期"])
    }

    // MARK: - 覆盖率:NULL 不是 0

    func testCoverageNullIsNotZero() throws {
        let snap = try decode(CoverageSnapshot.self, """
        {"window":20,"days":[
          {"tradeDate":"20260820","packVersion":"fp-3","limitUpCount":43,"limitDownCount":24,
           "zabanCount":17,"zabanRate":0.283,"maxConsecDays":4,"clusterCount":12,
           "listingTradeDate":null,"listingSize":null,"coveredCount":null,
           "coverageAll":null,"inPoolDenominator":null,"coveredInPool":null,
           "coverageInPool":null,"census":{}}],
         "latestMisses":[
          {"tradeDate":"20260820","tsCode":"600009.SH","name":"己","board":"MAIN",
           "l2Code":"801080.SI","l2Name":"电子","consecLimitUpDays":1,
           "reason":"no_listing","detail":null}],
         "missReasonCounts":{"no_listing":43}}
        """)
        let day = try XCTUnwrap(snap.latest)
        // 🔴 这两条是这批里最容易退化的:`null` ⛔ 不许被解成 0。
        XCTAssertNil(day.coverageAll, "昨天还没有清单 → 尚不可得,⛔ 不是 0%")
        XCTAssertNil(day.coverageInPool, "边界参数缺失 → 服务端写 NULL,⛔ 不是 0%")
        XCTAssertEqual(day.limitUpCount, 43, "涨停是硬事实,这条线不依赖任何待标定数字")
        XCTAssertEqual(snap.latestMisses.first?.reasonLabel, nkMissReasonLabel("no_listing"))
        XCTAssertNotEqual(nkMissReasonLabel("no_listing"), "no_listing", "归因码要换算成人话")
        XCTAssertEqual(nkMissReasonLabel("brand_new_reason"), "brand_new_reason",
                       "未识别码原样透传 —— ⛔ 不瞎翻译")
    }

    // MARK: - K9-v2 D2 五指标

    func testD2ScorecardHasFiveMetricsWithNoSyntheticTotal() {
        let names = NKListingScorecard.metrics.map(\.name)
        XCTAssertEqual(names.count, 5, "K9 §八 是五个指标")
        XCTAssertTrue(names.contains("D1—D2 上涨触达"))
        XCTAssertTrue(names.contains("D2 收盘胜率"))
        XCTAssertTrue(names.contains("D2 行业超额"))
        XCTAssertTrue(names.contains("D1—D2 最大回撤"))
        XCTAssertTrue(names.contains("最终清单提升"))
        for banned in ["合计", "总分", "综合", "total", "combined"] {
            XCTAssertFalse(names.contains { $0.localizedCaseInsensitiveContains(banned) },
                           "⛔ 五指标里不许出现合计口径:\(banned)")
        }
    }

    // MARK: - 展示层换算

    func testPatternTierSeatLabelsTranslateAndPassThroughUnknown() {
        XCTAssertEqual(nkPatternLabel("p1"), "放量启动")
        XCTAssertEqual(nkPatternLabel("p3"), "热门强博弈")
        XCTAssertEqual(nkPatternLabel("p4"), "资金领先价格")
        XCTAssertEqual(nkPatternLabel("p9"), "p9", "未识别值原样透传,⛔ 不瞎翻译")
        XCTAssertEqual(nkTierLabel("strict"), "严格")
        XCTAssertEqual(nkTierLabel("relaxed"), "放宽")
        XCTAssertEqual(nkSeatKindLabel(nil), "", "未入席 → 空串 → 整枚徽标不画")
        XCTAssertEqual(nkSeatKindLabel("floor"), "保底席")
        XCTAssertEqual(nkBoardLabel("GEM"), "创业板")
    }

    func testMetricRefLabelsCoverTheNineClosedEnumMembers() {
        let members = ["auction_price", "auction_gap_pct", "open_price", "gap_pct",
                       "first30_low", "first30_high", "prev_close", "prev_low", "prev_high"]
        XCTAssertEqual(members.count, 9, "服务端 `MetricRef` 是恰好九个成员的闭合枚举")
        for m in members {
            XCTAssertNotEqual(nkMetricRefLabel(m), m, "`\(m)` 缺展示层换算 —— 界面会直接印英文码")
        }
        XCTAssertEqual(nkMetricRefLabel("tenth_member"), "tenth_member", "第十个 → 原样透传")
    }

    /// 🔴 **可编辑数值位⛔ 不带千分位** —— 那个串会被原样 `Double(...)` 解回去。
    func testSlotValueRoundTripsThroughDouble() {
        for v in [12.5, 1802.0, 0.01, 123456.78] {
            let s = NKFmt.slotValue(v)
            XCTAssertFalse(s.contains(","), "⛔ `\(s)` 带了千分位,`Double(...)` 解不回来")
            XCTAssertEqual(Double(s) ?? .nan, v, accuracy: 1e-6)
        }
        // 对照:展示用的 `price` **就是**带千分位的(两者⛔ 不许互换)。
        XCTAssertTrue(NKFmt.price(1802.0).contains(","))
    }
}
