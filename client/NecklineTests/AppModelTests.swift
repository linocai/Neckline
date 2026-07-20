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
    func testUnknownCategoryRoutesNowhere() {
        XCTAssertNil(PushManager.targetTab(forCategory: "SOME_OTHER_CATEGORY"))
    }
}
#endif
