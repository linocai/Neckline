//
//  AppModelTests.swift
//  NecklineTests — AppModel 派生逻辑 / 问询台「永不买」不变量 / 交易日历。
//

import XCTest
@testable import Neckline

@MainActor
final class AppModelTests: XCTestCase {

    // MARK: - §2.5 硬约束:问询台裁决永不「买」

    /// 镜像后端 `test_verdict_always_binary_never_buy`(tests/test_api_inquiry.py):即便
    /// 上游文本疯狂喊「现在就买」,客户端裁决枚举也只可能是 reject/pass/unknown 三态之一,
    /// 且**任何一态**都不启用买入操作。
    func testInquiryVerdictNeverEnablesBuyAction() {
        let raws = [
            InquiryVerdict.rejectRaw, InquiryVerdict.passRaw,
            "现在就买!马上买入!强烈建议买买买!",   // 对抗性字符串(后端同款测试用例)
            "", "买",
        ]
        for raw in raws {
            let v = InquiryVerdict(raw)
            XCTAssertFalse(v.enablesBuyAction, "verdict for raw=\(raw) 不得启用买入操作")
        }
    }

    func testInquiryVerdictKnownCasesMapExactly() {
        XCTAssertEqual(InquiryVerdict("不符合"), .reject)
        XCTAssertEqual(InquiryVerdict("初审通过进海选池"), .pass)
        XCTAssertEqual(InquiryVerdict("不符合").label, "不符合")
        XCTAssertEqual(InquiryVerdict("初审通过进海选池").label, "初审通过进海选池")
    }

    func testInquiryVerdictUnrecognizedStringNeverSilentlyBecomesKnownState() {
        // 契约漂移防护:未知字符串必须落 .unknown,不能被静默当成 reject 或 pass 展示,
        // 否则后端一改措辞、前端就可能把"未识别"误当"已通过"渲染。
        let v = InquiryVerdict("某种新裁决")
        guard case .unknown(let raw) = v else {
            return XCTFail("未识别字符串应归 .unknown,实际 \(v)")
        }
        XCTAssertEqual(raw, "某种新裁决")
        XCTAssertFalse(v.enablesBuyAction)
    }

    // MARK: - 问询台多轮上下文截断(§2.5「客户端回传」,继承 LinoN /chat 姿势)

    func testInquiryContextTruncatesFromUserBoundary() {
        var thread: [ChatMessage] = []
        for i in 0..<20 {
            thread.append(ChatMessage(role: i % 2 == 0 ? .user : .assistant, text: "msg\(i)"))
        }
        let truncated = AppModel.inquiryContext(from: thread, maxCount: 16)
        XCTAssertLessThanOrEqual(truncated.count, 16)
        XCTAssertEqual(truncated.first?.role, .user, "截断后必须从 user 边界开始,不能 assistant 打头")
    }

    func testInquiryContextShortThreadUnchanged() {
        let thread = [ChatMessage(role: .user, text: "hi"), ChatMessage(role: .assistant, text: "hello")]
        XCTAssertEqual(AppModel.inquiryContext(from: thread).count, 2)
    }

    // MARK: - 退潮警示(§2.4「今日计划作废、禁开新仓」只警示不硬拦)

    func testRetreatWarningPresentWhenBrakeActive() {
        let model = AppModel()
        model.board = BoardSnapshot(tradeDate: "20260717", asof: "",
                                    retreatBrake: RetreatBrake(active: true, reason: "炸板率飙升"),
                                    events: [])
        XCTAssertNotNil(model.retreatWarning)
        XCTAssertTrue(model.retreatWarning!.contains("炸板率飙升"))
    }

    func testRetreatWarningNilWhenBrakeInactive() {
        let model = AppModel()
        model.board = BoardSnapshot.empty
        XCTAssertNil(model.retreatWarning)
    }

    // MARK: - 仓位额度三态映射(唯一事实源 = 后端字面量,客户端只穷举匹配)

    func testPositionQuotaMapping() {
        XCTAssertEqual(PositionQuota("满额"), .full)
        XCTAssertEqual(PositionQuota("半额"), .half)
        XCTAssertEqual(PositionQuota("休息"), .rest)
        if case .unknown(let raw) = PositionQuota("???") {
            XCTAssertEqual(raw, "???")
        } else {
            XCTFail("未识别额度应归 .unknown")
        }
    }

    // MARK: - 候选板块码展示换算(实测服务端 board 字段是英文枚举码,非中文名)

    func testCandidateBoardLabelTranslatesKnownCodes() {
        func candidate(board: String) -> Candidate {
            Candidate(rank: 1, code: "600001.SH", name: "甲", score: 90, board: board,
                     buyPoint: "", stop: "", target: "", invalidation: "",
                     formTags: [], hotSectors: [], sectorNames: [], llmJudgment: nil)
        }
        XCTAssertEqual(candidate(board: "MAIN").boardLabel, "主板")
        XCTAssertEqual(candidate(board: "GEM").boardLabel, "创业板")
        XCTAssertEqual(candidate(board: "STAR").boardLabel, "科创板")
        XCTAssertEqual(candidate(board: "BSE").boardLabel, "北交所")
        XCTAssertEqual(candidate(board: "UNKNOWN_FUTURE").boardLabel, "UNKNOWN_FUTURE",
                       "未识别值原样透传,不静默瞎翻译")
    }

    // MARK: - Position 派生计算(止损线由服务端下发,客户端不重算,只做展示派生)

    func testPositionPnlAndStopBreach() {
        let p = Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260716", price: 9.5, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false)
        XCTAssertTrue(p.hasLivePrice)
        XCTAssertTrue(p.hasBrokenStop, "price == stopLine 应判定为已破线(<=)")
        XCTAssertEqual(p.pnlPct, -5.0, accuracy: 0.001)
    }

    func testPositionNoLivePriceNeverMisreadsAsBrokenStop() {
        let p = Position(id: 2, code: "600002.SH", name: "乙", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260716", price: 0, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false)
        XCTAssertFalse(p.hasLivePrice)
        XCTAssertFalse(p.hasBrokenStop)
        XCTAssertNil(p.distToStopPct)
    }

    // MARK: - §五 v1.1-E.1 持仓生命周期展示层派生(dCount/maxHoldDays/distToStopPct/
    // retraceState 服务端下发,todayActionTone 只按优先级选颜色/是否醒目横幅,不重推文案)

    func testPositionIsExitDayWhenDCountReachesMaxHoldDays() {
        var p = Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260710", price: 10.0, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false, dCount: 5, maxHoldDays: 5)
        XCTAssertTrue(p.isExitDay)
        XCTAssertEqual(p.todayActionTone, .bad, "D5 时间退出必须最高优先醒目")

        p.dCount = 6   // 改 config 到更短的 hold 天数后,超过也仍算 exit day(不是恰好相等才算)
        XCTAssertTrue(p.isExitDay)

        p.dCount = 3
        XCTAssertFalse(p.isExitDay)
    }

    func testPositionTodayActionTonePriorityRetraceOverStopDistance() {
        // 回落止盈已触发的优先级高于"距止损线"(与服务端 `_today_action` 判定顺序一致)。
        let p = Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260716", price: 11.0, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false, dCount: 2, maxHoldDays: 5,
                         distToStopPctServer: 0.05,
                         retraceState: RetraceState(peak: 12.0, retracePct: 0.083, triggered: true))
        XCTAssertFalse(p.isExitDay)
        XCTAssertEqual(p.todayActionTone, .bad)
    }

    func testPositionTodayActionToneWarnWhenNearStopLine() {
        let p = Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260716", price: 9.6, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false, dCount: 2, maxHoldDays: 5,
                         distToStopPctServer: 0.015)
        XCTAssertEqual(p.todayActionTone, .warn)
    }

    func testPositionTodayActionToneBadWhenAlreadyBrokenStop() {
        let p = Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260716", price: 9.2, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false, dCount: 2, maxHoldDays: 5,
                         distToStopPctServer: -0.03)
        XCTAssertEqual(p.todayActionTone, .bad)
    }

    func testPositionTodayActionToneNeutralWhenHoldingNormally() {
        let p = Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260716", price: 10.5, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false, dCount: 2, maxHoldDays: 5,
                         distToStopPctServer: 0.095)
        XCTAssertEqual(p.todayActionTone, .neutral)
    }

    // MARK: - §五 v1.1-E.2 买点参考价(展示层选择,`Candidate.entrySpec`,不新推导数字)

    func testEntrySpecReferencePricePullbackUsesMA10() {
        let spec = EntrySpec(buypoint: "pullback", ma10: 12.34, platformHigh: nil)
        XCTAssertEqual(spec.referencePrice, 12.34)
    }

    func testEntrySpecReferencePriceBreakoutUsesPlatformHigh() {
        let spec = EntrySpec(buypoint: "breakout", ma10: nil, platformHigh: 15.5)
        XCTAssertEqual(spec.referencePrice, 15.5)
    }

    func testEntrySpecReferencePriceMissingBothIsNil() {
        let spec = EntrySpec(buypoint: "pullback", ma10: nil, platformHigh: nil)
        XCTAssertNil(spec.referencePrice, "买点参考价缺失时不得虚构数字,UI 须留空手填")
    }

    // MARK: - §五 v1.1-E.2 一键补录预填(端到端,经 `MockURLProtocol` 免联网,同
    // DTODecodeTests 的桩姿势——验证 `AppModel.openEntrySheet(fromCandidate:)` 真的
    // 拉了 entry-suggestion 并把 qty/止损提示写回表单,不是只测 EntrySpec 纯函数)。

    func testOpenEntrySheetFromCandidatePrefillsPriceQtyAndStopLine() async throws {
        MockURLProtocol.handler = { req in
            let url = req.url?.absoluteString ?? ""
            XCTAssertTrue(url.contains("/positions/entry-suggestion"))
            XCTAssertTrue(url.contains("code=600519.SH"))
            XCTAssertTrue(url.contains("price=1217.11"))
            return (200, """
            {"ok": true, "code": "600519.SH", "price": 1217.11, "qty": 0, "stopLine": 1156.25}
            """.data(using: .utf8)!)
        }
        defer { MockURLProtocol.handler = nil }

        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let session = URLSession(configuration: config)
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t", session: session)
        let model = AppModel(clientProvider: { client })

        let candidate = Candidate(
            rank: 1, code: "600519.SH", name: "贵州茅台", score: 98.7, board: "MAIN",
            buyPoint: "回调低吸:现价 1253.00,站稳 10 日线(MA10≈1217.11)不破位…",
            stop: "参考止损价约 1190.35 元(-5%)", target: "不设固定止盈线…", invalidation: "次日低开…",
            formTags: [], hotSectors: [], sectorNames: [], llmJudgment: nil,
            entrySpec: EntrySpec(buypoint: "pullback", ma10: 1217.11, platformHigh: 1258.99)
        )
        await model.openEntrySheet(fromCandidate: candidate)

        XCTAssertEqual(model.entryForm.code, "600519.SH")
        XCTAssertEqual(model.entryForm.name, "贵州茅台")
        XCTAssertEqual(model.entryForm.price, "1217.11")
        XCTAssertTrue(model.entryForm.reason.contains("回调低吸"))
        XCTAssertEqual(model.entrySuggestedStopLine, 1156.25)
        XCTAssertEqual(model.modal, .open)
    }

    /// 买点参考价缺失(entrySpec 未算出)→ 价格留空手填,不虚构数字、不崩、仍能打开 sheet。
    func testOpenEntrySheetFromCandidateWithoutEntrySpecLeavesPriceBlank() async throws {
        let model = AppModel(clientProvider: { nil })   // 无后端连接也不该崩
        let candidate = Candidate(rank: 1, code: "600001.SH", name: "甲", score: 80, board: "MAIN",
                                  buyPoint: "b", stop: "s", target: "t", invalidation: "i",
                                  formTags: [], hotSectors: [], sectorNames: [], llmJudgment: nil)
        await model.openEntrySheet(fromCandidate: candidate)
        XCTAssertEqual(model.entryForm.code, "600001.SH")
        XCTAssertEqual(model.entryForm.price, "", "缺 entrySpec 参考价时不得虚构数字")
        XCTAssertNil(model.entrySuggestedStopLine)
        XCTAssertEqual(model.modal, .open)
    }

    // MARK: - §五 v1.1-F 自选体检板块码展示换算(与 Candidate 共用 `nkBoardLabel`)

    func testWatchlistCheckItemBoardLabelSharesCandidateMapping() {
        let item = WatchlistCheckItem(
            code: "300750.SZ", name: "宁德时代", pinned: false, source: "manual", hasData: true,
            close: 200.0, board: "GEM", score: 70.0, patternTags: [], hotSectors: [], sectorNames: [],
            greenLight: false, disqualifiers: [], buyPointTriggered: false, buyPoint: "", stop: "",
            target: "", invalidation: "", statusChanged: false, llmJudgment: nil
        )
        XCTAssertEqual(item.boardLabel, "创业板")
    }

    // MARK: - 开仓表单校验

    func testPositionEntryFormValidation() {
        var form = PositionEntryForm()
        XCTAssertFalse(form.isValid)
        form.code = "600519.SH"
        form.price = "1500.0"
        form.qty = "100"
        form.reason = "回调低吸"
        XCTAssertTrue(form.isValid)
        form.price = "0"
        XCTAssertFalse(form.isValid, "买入价必须 > 0")
    }

    // MARK: - 交易日历(日期解析,§五 阶段4C 复用清单)

    func testCalendarParsesCompactAndISODates() {
        let cal = StaticTradingCalendar.shared
        XCTAssertNotNil(cal.parseDate("20260717"))
        XCTAssertNotNil(cal.parseDate("2026-07-17"))
        XCTAssertEqual(cal.displayString("20260717"), "2026-07-17")
    }

    func testCalendarKnownHolidayIsNotTradingDay() {
        let cal = StaticTradingCalendar.shared
        let newYear = cal.parseDate("20260101")!
        XCTAssertFalse(cal.isTradingDay(newYear))
    }

    func testCalendarKnownTradingDayIsTradingDay() {
        // 2026-07-17 是阶段0/2 施工期真实交易日(报告/情绪仪表盘真数据锚点日之一)。
        let cal = StaticTradingCalendar.shared
        let d = cal.parseDate("20260717")!
        XCTAssertTrue(cal.isTradingDay(d))
    }
}

// MARK: - PushManager 推送路由(纯函数,iOS 专属——PushManager 整文件 #if os(iOS)）

#if os(iOS)
@MainActor
final class PushRoutingTests: XCTestCase {
    func testReportCategoryRoutesToToday() {
        XCTAssertEqual(PushManager.targetTab(forCategory: NKNotificationCategory.report), .today)
    }
    func testRetreatCategoryRoutesToBoard() {
        XCTAssertEqual(PushManager.targetTab(forCategory: NKNotificationCategory.retreat), .board)
    }
    // v1.1-G.2:推送白名单四类路由(PRECALL→盘中看板、D5EXIT→今日计划)。
    func testPrecallCategoryRoutesToBoard() {
        XCTAssertEqual(PushManager.targetTab(forCategory: NKNotificationCategory.precall), .board)
    }
    func testD5ExitCategoryRoutesToToday() {
        XCTAssertEqual(PushManager.targetTab(forCategory: NKNotificationCategory.d5exit), .today)
    }
    func testUnknownCategoryRoutesNowhere() {
        XCTAssertNil(PushManager.targetTab(forCategory: "SOME_OTHER_CATEGORY"))
    }
    /// category 字面必须与后端 `neckline/push/apns.py` 的 `CATEGORY_PRECALL="PRECALL"`/
    /// `CATEGORY_D5EXIT="D5EXIT"` 完全一致(客户端/服务端各自独立声明字符串,契约漂移
    /// 只能靠这类断言在编译期之外兜底——同 `test_categories_are_four_distinct` 后端镜像)。
    func testCategoryLiteralsMatchBackend() {
        XCTAssertEqual(NKNotificationCategory.report, "REPORT")
        XCTAssertEqual(NKNotificationCategory.retreat, "RETREAT")
        XCTAssertEqual(NKNotificationCategory.precall, "PRECALL")
        XCTAssertEqual(NKNotificationCategory.d5exit, "D5EXIT")
    }
}
#endif
