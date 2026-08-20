//
//  AppModelTests.swift
//  NecklineTests — **导航与派生逻辑**的门禁(V2.5.0 S12 重写)。
//
//  ⚠ 上一版这里有 129 条用例,守的全是 K8 的东西(篮子 / 六关 / 双时钟 / 持仓角色)。
//  那条链已整体退役,用例随之删除 —— ⛔ 这不是放宽:被测的东西早就不存在了。
//

import XCTest
@testable import Neckline

@MainActor
final class AppModelTests: XCTestCase {

    // MARK: - 三板块 IA(裁定 11)

    /// 🔴 **`rawValue` 是 `NECKLINE_INITIAL_TAB` QA 钩子的契约**:
    /// `NecklineApp.init()` 按它把 App 启动到指定板块。改名 = 那个钩子静默落到默认 tab。
    func testAppTabRawValuesAreTheQAHookContract() {
        XCTAssertEqual(AppTab.allCases.map(\.rawValue),
                       ["selection", "scoreboard", "review", "settings"],
                       "顺序即 iOS TabBar 顺序;rawValue 是 QA 钩子的参数")
        // ⛔ 持仓板块整块下线(裁定 11)—— 不许以任何名字加回来。
        XCTAssertNil(AppTab(rawValue: "positions"))
        XCTAssertNil(AppTab(rawValue: "baskets"), "「篮子」是已退役的 K8 概念")
        XCTAssertEqual(AppTab.selection.title, "选股")
        XCTAssertEqual(AppTab.scoreboard.title, "成绩")
        XCTAssertEqual(AppTab.review.title, "复盘")
    }

    /// **设置在产品语义上不算板块** —— 它排最后、是入口。
    func testSettingsSitsLastBecauseItIsAnEntryNotABoard() {
        XCTAssertEqual(AppTab.allCases.last, .settings)
    }

    // MARK: - 选股两视图:9:26–15:00 默认落核对表(裁定 11 / §5.11)

    func testDefaultSelectionModeFollowsTheClock() throws {
        func at(_ hour: Int, _ minute: Int) -> Date {
            var c = DateComponents()
            c.year = 2026; c.month = 8; c.day = 20; c.hour = hour; c.minute = minute
            return Calendar.current.date(from: c)!
        }
        XCTAssertEqual(AppModel.defaultSelectionMode(now: at(9, 25)), .listing)
        XCTAssertEqual(AppModel.defaultSelectionMode(now: at(9, 26)), .checklist,
                       "9:26 起默认落核对表")
        XCTAssertEqual(AppModel.defaultSelectionMode(now: at(12, 0)), .checklist)
        XCTAssertEqual(AppModel.defaultSelectionMode(now: at(14, 59)), .checklist)
        XCTAssertEqual(AppModel.defaultSelectionMode(now: at(15, 0)), .listing,
                       "15:00 起回到清单")
        XCTAssertEqual(AppModel.defaultSelectionMode(now: at(21, 0)), .listing)
    }

    func testSelectionViewModeRawValuesAreTheQAHookContract() {
        XCTAssertEqual(SelectionViewMode.allCases.map(\.rawValue), ["listing", "checklist"])
    }

    func testReviewPageRawValuesAreTheQAHookContract() {
        XCTAssertEqual(ReviewPage.allCases.map(\.rawValue),
                       ["reconcile", "bindery", "conclusions", "mine"])
        // 🔴 每页各答一个不同的问题 —— 那一行字就是把四页的域分开的东西,⛔ 不许空。
        for p in ReviewPage.allCases {
            XCTAssertFalse(p.question.isEmpty, "\(p.rawValue) 缺「这一页答什么」")
        }
        XCTAssertTrue(ReviewPage.mine.question.contains("隔离"),
                      "「我的成绩」必须写明它与系统那两条线**完全隔离**")
    }

    // MARK: - 派生

    func testHasListingRequiresBothStateAndRows() {
        let model = AppModel()
        XCTAssertFalse(model.hasListing, "初始态没拉过 → 没有清单")

        model.selection = SelectionSnapshot(state: .empty, headline: "今天没有", listingSize: 0)
        XCTAssertFalse(model.hasListing)

        model.selection = SelectionSnapshot(state: .notRun, headline: "今天没跑成",
                                            gaps: ["参数未配置"], listingSize: nil)
        XCTAssertFalse(model.hasListing)
        // 🔴 两者都返 false,但**它们不是同一件事** —— 说什么话看 `state`。
        XCTAssertNotEqual(model.selection.state, .empty)

        model.selection = SelectionSnapshot(state: .hasList, headline: "今天有这些 · 1 只",
                                            listingSize: 1,
                                            stocks: [K9Stock(tsCode: "600000.SH", rank: 1)])
        XCTAssertTrue(model.hasListing)
    }

    func testNotLoadedSnapshotDoesNotPretendToBeAnyOfTheThreeStates() {
        // ⛔ 「还没连上服务端」不许被读成三态里的任何一态。
        XCTAssertNil(SelectionSnapshot.notLoaded.state)
        XCTAssertEqual(SelectionSnapshot.notLoaded.tone, .warn)
    }

    /// 🔴 **`checklist == nil` 有两种成因,⛔ 不许合并**:
    /// 「那天没跑过那一拍」(404,合法空态)与「本次没取到」(网络没通)。
    func testChecklistMissingIsDistinctFromNetworkFailure() {
        let model = AppModel()
        XCTAssertNil(model.checklist)
        XCTAssertNil(model.checklistMissing, "初始态两者都是 nil = 本次还没去拉")

        model.checklistMissing = "20260821 没有竞价核对表"
        XCTAssertNil(model.checklist)
        XCTAssertNotNil(model.checklistMissing, "404 → 有 why = 那天没跑过那一拍")
    }

    // MARK: - 结论草稿(append-only 的客户端半边)

    func testConclusionFormSplitsTagsOnBothCommaKindsAndSpaces() {
        var form = ConclusionForm()
        form.tagsText = "追高, 止损　空仓,,情绪"
        XCTAssertEqual(form.tags, ["追高", "止损　空仓", "情绪"].filter { !$0.isEmpty }
                        .flatMap { $0.split(separator: " ").map(String.init) })
    }

    func testConclusionFormRequiresWeekTitleAndBody() {
        var form = ConclusionForm()
        XCTAssertFalse(form.isValid)
        form.week = "2026-W34"
        XCTAssertFalse(form.isValid)
        form.title = "这周"
        XCTAssertFalse(form.isValid)
        form.body = "结论正文"
        XCTAssertTrue(form.isValid)
    }

    // MARK: - 通用 JSON 载体

    func testNKJSONKeepsBoolDistinctFromNumber() throws {
        let json = #"{"b":true,"n":1,"s":"x","arr":[1,2],"obj":{"k":"v"},"nul":null}"#
        let v = try JSONDecoder().decode(NKJSON.self, from: Data(json.utf8))
        // ⚠ Bool 必须排在 Double 之前:JSON `true` 在 Foundation 里也能解成 1.0。
        XCTAssertEqual(v["b"]?.boolValue, true)
        XCTAssertNil(v["b"]?.doubleValue)
        XCTAssertEqual(v["n"]?.intValue, 1)
        XCTAssertEqual(v["s"]?.stringValue, "x")
        XCTAssertEqual(v["arr"]?.arrayValue?.count, 2)
        XCTAssertEqual(v["obj"]?["k"]?.stringValue, "v")
        XCTAssertTrue(v["nul"]?.isNull ?? false)
        // 键序**确定性** —— 界面上逐项列出时顺序不能每次刷新都跳。
        XCTAssertEqual(v.sortedKeys, ["arr", "b", "n", "nul", "obj", "s"])
    }

    // MARK: - 交易日历

    func testCalendarParsesCompactAndISODates() {
        let cal = StaticTradingCalendar.shared
        XCTAssertNotNil(cal.parseDate("20260820"))
        XCTAssertNotNil(cal.parseDate("2026-08-20"))
        XCTAssertNil(cal.parseDate("not-a-date"))
        let d = try? XCTUnwrap(cal.parseDate("20260820"))
        XCTAssertEqual(d.map(cal.compactString), "20260820")
    }
}
