import XCTest
import SwiftUI
#if os(iOS)
import UIKit
#else
import AppKit
#endif
@testable import Neckline

/// KX 看板：接口契约、高亮与分区、分组列、数字与文案。夹具是后端测试从隔离库产出的真实响应。
final class KXBoardContractTests: XCTestCase {
    // MARK: 夹具解码

    func testSampleBoardFixtureDecodes() throws {
        let board = try decodeBoard("kx_board_sample")
        XCTAssertEqual(board.generatedAt, "2026-10-20T10:31:07+08:00")
        XCTAssertEqual(board.pool.version, "kx-20261017")
        XCTAssertEqual(board.pool.count, 12)
        XCTAssertEqual(board.pool.checkedAt, "2026-10-20T10:31:07+08:00")
        XCTAssertFalse(board.pool.stale)
        XCTAssertNil(board.pool.reason)
        XCTAssertEqual(board.session.tradeDate, "2026-10-20")
        XCTAssertTrue(board.session.inSession)
        XCTAssertEqual(board.session.refreshSeconds, 60)
        XCTAssertEqual(board.session.quoteTime, "2026-10-20T10:29:58+08:00")
        XCTAssertEqual(board.stats.asOf, "2026-10-19")
        XCTAssertEqual(board.picks.referenceDate, "2026-10-20")
        XCTAssertEqual(board.picks.lastPickAt, "2026-10-20T10:20:00+08:00")
        XCTAssertTrue(board.gaps.isEmpty)
        XCTAssertEqual(board.rows.count, 12)
        XCTAssertEqual(board.rows.map(\.code), [
            "600010", "000001", "000003", "600008", "600007", "600006",
            "600005", "600011", "000004", "600009", "000002", "000000",
        ])

        let first = board.rows[0]
        XCTAssertEqual(first.name, "股票10")
        XCTAssertEqual(first.industry, "行业2")
        XCTAssertEqual(first.highlight, .today)
        XCTAssertEqual(first.lastPickedAt, "2026-10-20T10:20:00+08:00")
        XCTAssertEqual(first.quoteStatus, .ok)
        XCTAssertNil(first.note)
        XCTAssertEqual(first.price, 13.5)
        XCTAssertEqual(first.changePercent, 0.0)
        XCTAssertEqual(first.open, 13.63)
        XCTAssertEqual(first.high, 13.9)
        XCTAssertEqual(first.low, 13.37)
        XCTAssertEqual(first.previousClose, 13.5)
        XCTAssertEqual(first.turnoverYi, 1.1)
        XCTAssertEqual(first.volumeLots, 81481.0)
        XCTAssertEqual(first.rangePosition, 0.2453)
        XCTAssertEqual(first.pullbackFromHighPercent, -2.88)
        XCTAssertEqual(first.volumeRatio, 1.0)
        XCTAssertEqual(first.totalMvYi, 11.0)
        XCTAssertEqual(first.circMvYi, 6.5)
        XCTAssertEqual(first.averages.ma5, KXAverage(value: 13.4, position: .above))
        XCTAssertEqual(first.averages.ma20, KXAverage(value: 13.025, position: .above))
        XCTAssertEqual(board.rows[1].averages.ma5?.position, .below)
        XCTAssertEqual(board.rows[4].highlight, .faded)
        XCTAssertEqual(board.rows[4].lastPickedAt, "2026-10-19T14:20:00+08:00")
    }

    func testDegradedBoardFixtureKeepsNullsAsNil() throws {
        let board = try decodeBoard("kx_board_degraded")
        XCTAssertTrue(board.pool.stale)
        XCTAssertEqual(board.pool.reason, "KX 暂时连不上。")
        XCTAssertNil(board.stats.asOf)
        XCTAssertEqual(board.gaps, ["TuShare 行情频率超限，这次没有重试。"])
        XCTAssertEqual(board.rows.count, 6)

        let daily = board.rows[0]
        XCTAssertEqual(daily.quoteStatus, .ok)
        XCTAssertNotNil(daily.price)
        XCTAssertNil(daily.change5dPercent)
        XCTAssertNil(daily.change20dPercent)
        XCTAssertNil(daily.avgTurnover20Yi)
        XCTAssertNil(daily.volumeRatio)
        XCTAssertNil(daily.totalMvYi)
        XCTAssertNil(daily.circMvYi)
        XCTAssertEqual(daily.averages, KXAverages.none)

        let missing = try XCTUnwrap(board.rows.first { $0.quoteStatus == .missing })
        XCTAssertEqual(missing.code, "000001")
        XCTAssertEqual(missing.note, "没有取得这只的行情")
        XCTAssertNil(missing.price)
        XCTAssertNil(missing.changePercent)
        XCTAssertNil(missing.rangePosition)
        XCTAssertNil(missing.volumeLots)
        XCTAssertNil(missing.quoteTime)
        XCTAssertTrue(missing.hasNoQuote)

        let stale = try XCTUnwrap(board.rows.first { $0.quoteStatus == .stale })
        XCTAssertEqual(stale.code, "000002")
        XCTAssertEqual(stale.price, 10.59)
        XCTAssertEqual(stale.note, "报价日期是 2026-10-16，不是 2026-10-20")
        XCTAssertNil(stale.change5dPercent)
        XCTAssertTrue(stale.isMuted)
        XCTAssertFalse(daily.isMuted)
    }

    // MARK: 题材、热度、能否买入（schema 2 名单）

    func testSchema2FixtureDecodesThemeHeatAndBuyable() throws {
        let board = try decodeBoard("kx_board_schema2")
        XCTAssertEqual(board.pool.version, "kx-20261020-001")
        XCTAssertEqual(board.pool.schemaVersion, 2)
        XCTAssertEqual(board.pool.count, 12)
        XCTAssertEqual(board.rows.count, 12)
        XCTAssertEqual(board.rows.map(\.code), [
            "600010", "600007", "600006", "600005", "600011", "000004",
            "000003", "600009", "000002", "000001", "600008", "000000",
        ])
        // schema 2 名单没有行业。
        XCTAssertTrue(board.rows.allSatisfy { $0.industry == nil })

        let first = board.rows[0]
        XCTAssertEqual(first.theme, "题材2")
        XCTAssertEqual(first.firstSeen, "2026-10-19")
        XCTAssertEqual(first.heatDay, 2)
        XCTAssertEqual(first.buyable, true)
        XCTAssertEqual(first.highlight, .today)

        let faded = try row(board, code: "600007")
        XCTAssertEqual(faded.highlight, .faded)
        XCTAssertEqual(faded.buyable, false)
        XCTAssertEqual(faded.heatDay, 2)

        XCTAssertEqual(try row(board, code: "600005").heatDay, 9)
        XCTAssertEqual(try row(board, code: "600005").firstSeen, "2026-10-01")
        XCTAssertEqual(try row(board, code: "000000").heatDay, 1)
        XCTAssertNil(try row(board, code: "000004").heatDay, "热度空就是空，不当成 0 或 1")
        XCTAssertEqual(try row(board, code: "000001").buyable, false)
        XCTAssertEqual(board.rows.filter { $0.buyable == false }.map(\.code), ["600007", "000001"])
        // 夹具里每一行都有题材，所以每一行都有第二行。
        XCTAssertTrue(board.rows.allSatisfy(\.hasSecondLine))
    }

    func testSchema1FixturesHaveNoThemeHeatOrBuyableAndStayOneLine() throws {
        for name in ["kx_board_sample", "kx_board_degraded"] {
            let board = try decodeBoard(name)
            XCTAssertEqual(board.pool.schemaVersion, 1, name)
            for row in board.rows {
                XCTAssertNil(row.theme, "\(name) \(row.code)")
                XCTAssertNil(row.firstSeen, "\(name) \(row.code)")
                XCTAssertNil(row.heatDay, "\(name) \(row.code)")
                XCTAssertNil(row.buyable, "\(name) \(row.code)")
                XCTAssertNotNil(row.industry, "\(name) \(row.code)")
                XCTAssertNil(row.heat)
                XCTAssertFalse(row.isOneWordBoard)
                XCTAssertFalse(row.hasSecondLine, "\(name) \(row.code)：schema 1 名单不加第二行")
            }
        }
        // 旧后端的响应没有 schemaVersion 与新字段：照常解码。
        let legacy = try edited("kx_board_sample") { object in
            var pool = object["pool"] as! [String: Any]
            pool.removeValue(forKey: "schemaVersion")
            object["pool"] = pool
        }
        XCTAssertNil(legacy.pool.schemaVersion)
    }

    func testSecondLineAppearsWhenThereIsAThemeAHeatOrAnOneWordBoard() throws {
        // 三样各自单独出现。
        XCTAssertTrue(try themedRow(theme: "人形机器人", heatDay: nil, buyable: nil).hasSecondLine)
        XCTAssertTrue(try themedRow(theme: nil, heatDay: 3, buyable: nil).hasSecondLine)
        XCTAssertTrue(try themedRow(theme: nil, heatDay: nil, buyable: false).hasSecondLine)
        // 三样都有。
        XCTAssertTrue(try themedRow(theme: "人形机器人", heatDay: 1, buyable: false).hasSecondLine)
        // 全无：能买（true）或没有这项数据（null）、没有热度、没有题材，都不加第二行。
        XCTAssertFalse(try themedRow(theme: nil, heatDay: nil, buyable: nil).hasSecondLine)
        XCTAssertFalse(try themedRow(theme: nil, heatDay: nil, buyable: true).hasSecondLine, "只有能买不算第二行")
        // 空串与空白当没有题材。
        XCTAssertNil(try themedRow(theme: "", heatDay: nil, buyable: nil).themeText)
        XCTAssertFalse(try themedRow(theme: "  \n", heatDay: nil, buyable: nil).hasSecondLine)
        XCTAssertEqual(try themedRow(theme: " 固态电池 ", heatDay: nil, buyable: nil).themeText, "固态电池")
        // 热度不是正整数当没有热度。
        XCTAssertFalse(try themedRow(theme: nil, heatDay: 0, buyable: nil).hasSecondLine)
        XCTAssertFalse(try themedRow(theme: nil, heatDay: -1, buyable: nil).hasSecondLine)
    }

    func testHeatBadgeTiers() {
        XCTAssertEqual(KXHeat(day: 1), .firstDay)
        XCTAssertEqual(KXHeat(day: 2), .warm(day: 2))
        XCTAssertEqual(KXHeat(day: 3), .warm(day: 3))
        XCTAssertEqual(KXHeat(day: 4), .warm(day: 4))
        XCTAssertEqual(KXHeat(day: 5), .hot(day: 5))
        XCTAssertEqual(KXHeat(day: 12), .hot(day: 12))
        XCTAssertNil(KXHeat(day: nil))
        XCTAssertNil(KXHeat(day: 0))
        XCTAssertNil(KXHeat(day: -3))

        XCTAssertEqual(KXHeat(day: 1)?.text, "首日")
        XCTAssertEqual(KXHeat(day: 2)?.text, "第2天")
        XCTAssertEqual(KXHeat(day: 4)?.text, "第4天")
        XCTAssertEqual(KXHeat(day: 5)?.text, "第5天")
        XCTAssertEqual(KXHeat(day: 12)?.text, "第12天")
    }

    func testOneWordBoardOnlyShowsWhenBuyableIsFalse() throws {
        XCTAssertTrue(try themedRow(theme: "题材", heatDay: 2, buyable: false).isOneWordBoard)
        XCTAssertFalse(try themedRow(theme: "题材", heatDay: 2, buyable: true).isOneWordBoard)
        XCTAssertFalse(try themedRow(theme: "题材", heatDay: 2, buyable: nil).isOneWordBoard, "没有数据不当成一字板")
    }

    // MARK: 手机第二行的尺寸

    @MainActor
    func testRowHeightsFollowTheMockup() throws {
        let scale: CGFloat = 2
        let schema1 = try decodeBoard("kx_board_sample")
        for row in schema1.rows {
            let image = try render(KXRowView(row: row, group: .overview), width: 375, scale: scale)
            XCTAssertEqual(CGFloat(image.height) / scale, KXMetrics.rowHeight, "\(row.code)：没有第二行时行高保持 52")
        }
        let schema2 = try decodeBoard("kx_board_schema2")
        for group in KXColumnGroup.allCases {
            for row in schema2.rows {
                let image = try render(KXRowView(row: row, group: group), width: 375, scale: scale)
                let height = CGFloat(image.height) / scale
                // 样图 A1：上 7、第一行 34、间距 4、第二行 17、下 6，共 68；题材再长也不换行，所以一样高。
                XCTAssertEqual(height, 68, accuracy: 0.5, "\(group.title) \(row.code)")
            }
        }
        XCTAssertEqual(KXMetrics.twoLineRowHeight, 68)
        XCTAssertEqual(KXMetrics.twoLineTop, 7)
        XCTAssertEqual(KXMetrics.firstLineHeight, 34)
        XCTAssertEqual(KXMetrics.secondLineGap, 4)
        XCTAssertEqual(KXMetrics.secondLineHeight, 17)
        XCTAssertEqual(KXMetrics.twoLineBottom, 6)
        XCTAssertEqual(KXMetrics.badgeTextHeight + 2 * KXMetrics.badgePaddingV, KXMetrics.secondLineHeight, "徽标高 17")
        XCTAssertEqual(KXMetrics.badgeGap, 6)
        XCTAssertEqual(KXMetrics.badgePaddingV, 1)
        XCTAssertEqual(KXMetrics.badgePaddingH, 6)
        XCTAssertEqual(KXMetrics.badgeRadius, 5)
    }

    /// 80 字的长题材：375 点宽下第二行始终单行，用省略号收尾，不换行，也不挤压第一行。
    /// 做法：把同一行的长题材换成短题材，各排四个分组；整行高度相同，上面第一行的像素逐点相同。
    @MainActor
    func testEightyCharacterThemeStaysOnOneLineWithoutSqueezingTheFirstLine() throws {
        let long = try decodeBoard("kx_board_extreme")
        let short = try edited("kx_board_extreme") { object in
            var rows = object["rows"] as! [[String: Any]]
            for index in rows.indices where rows[index]["theme"] is String { rows[index]["theme"] = "题材" }
            object["rows"] = rows
        }
        XCTAssertEqual(long.rows.compactMap(\.theme).map(\.count), [80, 80, 80])
        XCTAssertEqual(long.rows.map(\.hasSecondLine), [true, true, true, true])
        XCTAssertEqual(long.rows.map(\.heat), [.hot(day: 12), .warm(day: 4), .firstDay, nil])
        XCTAssertEqual(long.rows.map(\.isOneWordBoard), [true, false, true, false])

        let scale: CGFloat = 2
        // 第一行占 7 到 41，第二行从 45 起；截到 41 就只含第一行。
        let firstLineCrop = (KXMetrics.twoLineTop + KXMetrics.firstLineHeight) * scale
        for group in KXColumnGroup.allCases {
            for (longRow, shortRow) in zip(long.rows, short.rows) {
                let name = "\(group.title) \(longRow.code)"
                let longImage = try render(KXRowView(row: longRow, group: group), width: 375, scale: scale)
                let shortImage = try render(KXRowView(row: shortRow, group: group), width: 375, scale: scale)
                XCTAssertEqual(longImage.height, shortImage.height, "\(name)：长题材把行撑高了")
                XCTAssertEqual(longImage.width, shortImage.width, "\(name)：长题材把行撑宽了")
                XCTAssertEqual(CGFloat(longImage.width) / scale, 375, "\(name)")
                XCTAssertEqual(CGFloat(longImage.height) / scale, KXMetrics.twoLineRowHeight, accuracy: 0.5, "\(name)：第二行换行了")

                let longTop = try pixels(of: XCTUnwrap(longImage.cropping(to: CGRect(x: 0, y: 0, width: longImage.width, height: Int(firstLineCrop)))))
                let shortTop = try pixels(of: XCTUnwrap(shortImage.cropping(to: CGRect(x: 0, y: 0, width: shortImage.width, height: Int(firstLineCrop)))))
                XCTAssertTrue(longTop == shortTop, "\(name)：第二行的题材挤动了第一行")
                if longRow.theme != nil {
                    XCTAssertTrue(try pixels(of: longImage) != pixels(of: shortImage), "\(name)：长题材没有画出来")
                }
            }
        }

        // 第二行自己：徽标全部露出（热度加一字板加至少 40 点的题材），题材占满剩下的宽度。
        let widest = long.rows[0]
        let badges = idealSize(KXHeatBadge(heat: try XCTUnwrap(widest.heat))).width + idealSize(KXOneWordBoardBadge()).width + 2 * KXMetrics.badgeGap
        XCTAssertLessThan(badges, (375 - KXMetrics.inset * 2) - 40, "375 点宽放不下徽标加题材")
        let secondLine = try render(KXSecondLine(row: widest), width: 375 - KXMetrics.inset * 2, scale: scale)
        XCTAssertEqual(CGFloat(secondLine.height) / scale, KXMetrics.secondLineHeight, accuracy: 0.5, "第二行换行了")
    }

    #if os(macOS)
    @MainActor
    func testMacTableShowsThemeHeatAndBuyableInsteadOfIndustry() throws {
        let columns = KXTableColumn.allCases
        XCTAssertEqual(Array(columns.prefix(4)), [.name, .theme, .heat, .buyable])
        XCTAssertEqual(columns.prefix(4).map(\.title), ["名称", "题材", "热度", "可买"])
        XCTAssertFalse(columns.map(\.title).contains("行业"))
        XCTAssertEqual(columns.count, 27)
        XCTAssertEqual(KXTableColumn.theme.alignment, .leading)
        XCTAssertEqual(KXTableColumn.heat.alignment, .trailing)
        XCTAssertEqual(KXTableColumn.buyable.alignment, .trailing)
        XCTAssertGreaterThan(KXTableColumn.theme.width.upperBound, KXTableColumn.price.width.upperBound * 2, "题材是宽列")

        let board = try decodeBoard("kx_board_schema2")
        let locked = try row(board, code: "600007")
        let normal = try row(board, code: "600010")
        XCTAssertEqual(KXTableColumn.theme.sortKey(normal), .text("题材2"))
        XCTAssertEqual(KXTableColumn.heat.sortKey(try row(board, code: "600005")), .number(9))
        XCTAssertEqual(KXTableColumn.heat.sortKey(try row(board, code: "000004")), .number(nil))
        XCTAssertEqual(KXTableColumn.buyable.sortKey(locked), .number(0))
        XCTAssertEqual(KXTableColumn.buyable.sortKey(normal), .number(1))

        // schema 1 名单：题材列写「—」，热度列写「—」，可买列留空。
        let old = try decodeBoard("kx_board_sample").rows[0]
        XCTAssertNil(old.themeText)
        XCTAssertEqual(KXTableColumn.theme.sortKey(old), .text(""))
        XCTAssertEqual(KXTableColumn.heat.sortKey(old), .number(nil))
        XCTAssertEqual(KXTableColumn.buyable.sortKey(old), .number(nil))

        // 长题材最多两行：窄列里不超过两行的高度，也比一行高（确实折行了）。
        let extreme = try decodeBoard("kx_board_extreme").rows[0]
        let oneLine = idealSize(KXTableCell(row: normal, column: .theme).frame(width: 220)).height
        let longTheme = idealSize(KXTableCell(row: extreme, column: .theme).frame(width: 220)).height
        XCTAssertGreaterThan(longTheme, oneLine + 8, "长题材没有折成第二行")
        XCTAssertLessThan(longTheme, oneLine + 26, "长题材超过了两行")
    }
    #endif

    // MARK: 高亮状态映射与分区

    func testHighlightMappingAndSections() throws {
        let board = try decodeBoard("kx_board_sample")
        XCTAssertEqual(KXHighlight.today.section, .today)
        XCTAssertEqual(KXHighlight.faded.section, .faded)
        XCTAssertEqual(KXHighlight.normal.section, .all)
        XCTAssertEqual(KXSection.allCases.map(\.title), ["今日推荐", "昨日推荐", "全部"])

        let sections = board.sections
        XCTAssertEqual(sections.map(\.section), [.today, .faded, .all])
        XCTAssertEqual(sections.map(\.rows.count), [4, 1, 7])
        // 区内保持服务端给的行序，不重排。
        XCTAssertEqual(sections[0].rows.map(\.code), ["600010", "000001", "000003", "600008"])
        XCTAssertEqual(sections[1].rows.map(\.code), ["600007"])
        XCTAssertEqual(sections[2].rows.map(\.code), ["600006", "600005", "600011", "000004", "600009", "000002", "000000"])
        XCTAssertEqual(sections.flatMap(\.rows).map(\.code), board.rows.map(\.code))
        XCTAssertEqual(board.rows(in: .faded).map(\.code), ["600007"])
        XCTAssertTrue(board.rows.filter(\.isToday).allSatisfy { $0.highlight == .today })
        XCTAssertEqual(board.rows.filter(\.isFaded).map(\.code), ["600007"])
    }

    func testEmptySectionIsNotShown() throws {
        // 降级夹具里没有昨日推荐。
        let degraded = try decodeBoard("kx_board_degraded")
        XCTAssertEqual(degraded.sections.map(\.section), [.today, .all])
        XCTAssertEqual(degraded.sections.map(\.rows.count), [1, 5])

        let noPicks = try edited("kx_board_sample") { object in
            var rows = object["rows"] as! [[String: Any]]
            for index in rows.indices { rows[index]["highlight"] = "normal" }
            object["rows"] = rows
        }
        XCTAssertEqual(noPicks.sections.map(\.section), [.all])
        let empty = try edited("kx_board_sample") { $0["rows"] = [[String: Any]]() }
        XCTAssertTrue(empty.sections.isEmpty)
    }

    func testUnknownEnumValuesDegradeSafely() throws {
        let board = try edited("kx_board_sample") { object in
            var rows = object["rows"] as! [[String: Any]]
            rows[0]["highlight"] = "boosted"
            rows[1]["quoteStatus"] = "halted"
            var averages = rows[2]["averages"] as! [String: Any]
            averages["ma5"] = ["value": 10.0, "position": "touching"]
            rows[2]["averages"] = averages
            object["rows"] = rows
        }
        XCTAssertEqual(board.rows[0].highlight, .normal)
        XCTAssertEqual(board.rows[0].highlight.section, .all)
        XCTAssertEqual(board.rows[1].quoteStatus, .missing)
        XCTAssertEqual(board.rows[2].averages.ma5?.position, .flat)
        XCTAssertEqual(KXAverageDots.color(board.rows[2].averages.ma5), NKKX.flat)
    }

    // MARK: 接口

    func testClientRequestsBoardWithBearerOnly() async throws {
        let data = try fixture("kx_board_sample")
        let transport = KXScriptTransport()
        transport.next = (200, data)
        let api = KXBoardAPI(baseURL: URL(string: "https://fixture.invalid")!, token: " fixture-token-not-real ", transport: transport)
        let board = try await api.board()
        XCTAssertEqual(board.rows.count, 12)

        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(transport.requests.count, 1)
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.url?.path, "/api/v4/kx/board")
        XCTAssertNil(request.url?.query)
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-token-not-real")
        XCTAssertFalse(request.url?.absoluteString.contains("fixture-token-not-real") ?? true)

        let prefixed = KXBoardAPI(baseURL: URL(string: "https://fixture.invalid/nk/")!, token: "fixture-token-not-real", transport: transport)
        _ = try await prefixed.board()
        XCTAssertEqual(transport.requests.last?.url?.path, "/nk/api/v4/kx/board")
    }

    func testClientMapsFailures() async throws {
        await expectFailure(.unauthorized, status: 401, body: #"{"detail":"未授权:缺失或错误的 Bearer token"}"#)
        await expectFailure(.notConfigured, status: 503, body: #"{"detail":{"reason":"v4_not_configured"}}"#)
        await expectFailure(.dbNotReady, status: 503, body: #"{"detail":{"reason":"v4_db_not_ready"}}"#)
        await expectFailure(
            .poolUnverified("KX 名单还没有成功同步过，暂不出看板：KX 暂时连不上。"),
            status: 503,
            body: #"{"detail":{"reason":"pool_unverified","message":"KX 名单还没有成功同步过，暂不出看板：KX 暂时连不上。"}}"#
        )
        await expectFailure(
            .calendarGap("交易日历没有覆盖今天，暂不出看板：2027-01-04"),
            status: 503,
            body: #"{"detail":{"reason":"calendar_gap","message":"交易日历没有覆盖今天，暂不出看板：2027-01-04"}}"#
        )
        await expectFailure(.poolUnverified(nil), status: 503, body: #"{"detail":{"reason":"pool_unverified"}}"#)
        await expectFailure(.unreadable, status: 503, body: #"{"detail":{"reason":"something_else"}}"#)
        await expectFailure(.unreadable, status: 500, body: "")
        await expectFailure(.unreadable, status: 200, body: "{}")
        await expectFailure(.unreadable, status: 200, body: "not json")

        let offline = KXScriptTransport(error: URLError(.cannotConnectToHost))
        let disconnected = KXBoardAPI(baseURL: URL(string: "https://fixture.invalid")!, token: "fixture-token-not-real", transport: offline)
        await expectThrows(KXBoardError.unreachable) { _ = try await disconnected.board() }

        let trap = KXScriptTransport(error: URLError(.badServerResponse))
        let blank = KXBoardAPI(baseURL: URL(string: "https://fixture.invalid")!, token: "   ", transport: trap)
        await expectThrows(KXBoardError.missingToken) { _ = try await blank.board() }
        XCTAssertTrue(trap.requests.isEmpty, "没有令牌时不发请求")

        let cancelled = KXScriptTransport(error: URLError(.cancelled))
        let cancelling = KXBoardAPI(baseURL: URL(string: "https://fixture.invalid")!, token: "fixture-token-not-real", transport: cancelled)
        do {
            _ = try await cancelling.board()
            XCTFail("取消应当抛出 CancellationError")
        } catch is CancellationError {
        } catch {
            XCTFail("取消应当抛出 CancellationError，实际 \(error)")
        }
    }

    func testFailureBodyWithStringDetailHasNoReason() throws {
        let body = try JSONDecoder().decode(KXFailureBody.self, from: Data(#"{"detail":"Not authenticated"}"#.utf8))
        XCTAssertNil(body.reason)
        XCTAssertNil(body.message)
        XCTAssertFalse(KXBoardError.unauthorized.isTransient)
        XCTAssertTrue(KXBoardError.unreachable.isTransient)
        XCTAssertTrue(KXBoardError.unreadable.isTransient)
        XCTAssertFalse(KXBoardError.poolUnverified(nil).isTransient)
        XCTAssertFalse(KXBoardError.calendarGap(nil).isTransient)
    }

    // MARK: 分组列选择

    func testColumnGroupsPickTheRightColumns() {
        XCTAssertEqual(KXColumnGroup.allCases, [.overview, .today, .volume, .trend])
        XCTAssertEqual(KXColumnGroup.allCases.map(\.title), ["概览", "今日", "量能", "趋势"])
        XCTAssertEqual(KXColumnGroup.overview.columns, [.price, .changePercent, .range, .volumeRatio, .averages])
        XCTAssertEqual(KXColumnGroup.today.columns, [.openChange, .amplitude, .turnoverRate, .turnover])
        XCTAssertEqual(KXColumnGroup.volume.columns, [.turnover, .volume, .avgTurnover20, .volumeRatio])
        XCTAssertEqual(KXColumnGroup.trend.columns, [.change5d, .change20d, .circMv, .totalMv])

        XCTAssertEqual(KXColumn.volumeRatio.heading(in: .overview), "量能")
        XCTAssertEqual(KXColumn.volumeRatio.heading(in: .volume), "倍数")
        XCTAssertEqual(KXColumnGroup.overview.columns.map { $0.heading(in: .overview) }, ["现价", "涨跌幅", "区间", "量能", "均线"])
        XCTAssertEqual(KXColumnGroup.today.columns.map { $0.heading(in: .today) }, ["开盘涨跌", "振幅", "换手率", "成交额"])
        XCTAssertEqual(KXColumnGroup.volume.columns.map { $0.heading(in: .volume) }, ["成交额", "成交量", "20日均额", "倍数"])
        XCTAssertEqual(KXColumnGroup.trend.columns.map { $0.heading(in: .trend) }, ["5日", "20日", "流通市值", "总市值"])
    }

    func testColumnMetricsKeepTheMockupAndAtLeastSixPointsBetweenNumbers() {
        // 概览组的最小宽度与样图的 CSS 一致：现价 52、色块 56（左边距 6）、区间条 54（左边距 8）、量能 36（6）、均线 24（5）。
        XCTAssertEqual(KXColumnGroup.overview.columns.map(\.minWidth), [52, 56, 54, 36, 24])
        XCTAssertEqual(KXColumnGroup.overview.columns.map(\.leadingGap), [6, 6, 8, 6, 5])
        XCTAssertEqual(KXMetrics.rowHeight, 52)
        XCTAssertEqual(KXMetrics.rangeTrackWidth, 50)
        XCTAssertEqual(KXMetrics.ratioBarWidth, 32)

        // 任何两列数字之间固定留 6 以上；只有均线三点（不是数字）沿用样图的 5。
        for group in KXColumnGroup.allCases {
            for column in group.columns where column != .averages {
                XCTAssertGreaterThanOrEqual(column.leadingGap, KXMetrics.columnGap, "\(group.title)·\(column.heading(in: group)) 左边的空白不够 6")
            }
            XCTAssertEqual(Set(group.columns).count, group.columns.count, "\(group.title) 有重复列")
        }
        XCTAssertEqual(KXMetrics.columnGap, 6)
    }

    /// 最宽的真实值：现价 1234.56、涨跌幅 -10.00% 与 +20.00%、5 日 -12.3%、20 日 +123.4%、成交额 123.4亿、成交量 1234万手、
    /// 20 日均额 99.9亿、倍数 12.3×、市值（12345 亿显示为 1.23万亿）。四个分组在最小的 iPhone（375 点宽）上
    /// 每个格子都是单行、完整显示，数据列加固定间距之后名称仍有位置。
    @MainActor
    func testExtremeValuesFitTheSmallestPhoneWithoutTouchingOrTruncating() throws {
        let board = try decodeBoard("kx_board_extreme")
        XCTAssertEqual(board.rows.count, 4)
        let widest = board.rows[0]
        XCTAssertEqual(KXCopy.fixed(widest.price, 2), "1234.56")
        XCTAssertEqual(KXCopy.signedPercent(widest.changePercent), "+20.00%")
        XCTAssertEqual(KXCopy.signedPercent(board.rows[1].changePercent), "-10.00%")
        XCTAssertEqual(KXCopy.signedPercent(widest.change5dPercent, 1), "-12.3%")
        XCTAssertEqual(KXCopy.signedPercent(widest.change20dPercent, 1), "+123.4%")
        XCTAssertEqual(KXCopy.yi(widest.turnoverYi), "123.4亿")
        XCTAssertEqual(KXCopy.volume(lots: widest.volumeLots), "1234万手")
        XCTAssertEqual(KXCopy.yi(widest.avgTurnover20Yi), "99.9亿")
        XCTAssertEqual(KXCopy.ratio(widest.volumeRatio), "12.3×")
        XCTAssertEqual(KXCopy.marketValue(yi: widest.circMvYi), "1.23万亿")
        XCTAssertEqual(KXCopy.marketValue(yi: board.rows[2].circMvYi), "9999亿")

        let phone: CGFloat = 375 - KXMetrics.inset * 2
        // 四个汉字的名称缩到最小比例（0.85）时的宽度；数据列占完之后剩下的宽度不能比它小。
        let nameFloor: CGFloat = 4 * 15 * KXMetrics.nameMinimumScale
        for group in KXColumnGroup.allCases {
            for row in board.rows {
                var used: CGFloat = 0
                for column in group.columns {
                    let size = idealSize(KXCell(row: row, column: column))
                    XCTAssertLessThan(size.height, 32, "\(group.title)·\(column.heading(in: group)) 换行了：\(row.name)")
                    used += max(column.minWidth, size.width) + column.leadingGap
                }
                XCTAssertGreaterThanOrEqual(phone - used, nameFloor, "\(group.title)：375 点宽放不下 \(row.name) 的极端数字，名称只剩 \(phone - used)")
            }
        }
        // 平常的数字不比样图宽：示例夹具每组的数据列都在样图的宽度之内。
        let sample = try decodeBoard("kx_board_sample")
        for group in KXColumnGroup.allCases {
            for row in sample.rows {
                let used = group.columns.reduce(CGFloat(0)) { $0 + max($1.minWidth, idealSize(KXCell(row: row, column: $1)).width) + $1.leadingGap }
                XCTAssertGreaterThanOrEqual(phone - used, 80, "\(group.title)：平常的数字不该挤到名称")
            }
        }
    }

    @MainActor
    private func idealSize<V: View>(_ view: V) -> CGSize {
        #if os(iOS)
        return UIHostingController(rootView: view.fixedSize()).sizeThatFits(in: CGSize(width: 4000, height: 400))
        #else
        return NSHostingView(rootView: view.fixedSize()).fittingSize
        #endif
    }

    // MARK: 区间条

    func testRangeGeometry() throws {
        let board = try decodeBoard("kx_board_sample")
        let falling = KXRangeGeometry(row: board.rows[0])
        XCTAssertEqual(try XCTUnwrap(falling.marker), 0.2453, accuracy: 0.0001)
        let segment = try XCTUnwrap(falling.segment)
        // 开盘位置 (13.63-13.37)/(13.9-13.37)；现价在开盘下方，色段是绿的。
        XCTAssertEqual(segment.start, 0.2453, accuracy: 0.0001)
        XCTAssertEqual(segment.end, 0.26 / 0.53, accuracy: 0.0001)
        XCTAssertFalse(segment.rising)

        let rising = try row(board, code: "600006")
        let up = KXRangeGeometry(row: rising)
        XCTAssertTrue(try XCTUnwrap(up.segment).rising)

        // 最高等于最低：服务端 rangePosition 为空，什么都不画。
        let flat = try edited("kx_board_sample") { object in
            var rows = object["rows"] as! [[String: Any]]
            rows[0]["rangePosition"] = NSNull()
            rows[0]["high"] = 13.5
            rows[0]["low"] = 13.5
            object["rows"] = rows
        }
        let nothing = KXRangeGeometry(row: flat.rows[0])
        XCTAssertNil(nothing.marker)
        XCTAssertNil(nothing.segment)

        // 有位置但开盘、最高、最低缺一个：只画竖线。
        let partial = try edited("kx_board_sample") { object in
            var rows = object["rows"] as! [[String: Any]]
            rows[0]["open"] = NSNull()
            object["rows"] = rows
        }
        let markerOnly = KXRangeGeometry(row: partial.rows[0])
        XCTAssertNotNil(markerOnly.marker)
        XCTAssertNil(markerOnly.segment)

        // 位置越界时夹在 0 到 1 之间。
        let clamped = try edited("kx_board_sample") { object in
            var rows = object["rows"] as! [[String: Any]]
            rows[0]["rangePosition"] = 1.4
            object["rows"] = rows
        }
        XCTAssertEqual(KXRangeGeometry(row: clamped.rows[0]).marker, 1.0)
    }

    // MARK: 数字格式

    func testNumberFormatting() {
        XCTAssertEqual(KXCopy.dash, "—")
        XCTAssertEqual(KXCopy.fixed(nil, 2), "—")
        XCTAssertEqual(KXCopy.fixed(10.4, 2), "10.40")
        XCTAssertEqual(KXCopy.signed(1.36, 2), "+1.36")
        XCTAssertEqual(KXCopy.signed(-7.77, 2), "-7.77")
        XCTAssertEqual(KXCopy.signed(0, 2), "0.00")
        XCTAssertEqual(KXCopy.signed(-0.001, 2), "0.00", "显示为零的不带负号")
        XCTAssertEqual(KXCopy.signedPercent(4.48), "+4.48%")
        XCTAssertEqual(KXCopy.signedPercent(-1.18), "-1.18%")
        XCTAssertEqual(KXCopy.signedPercent(0.0), "0.00%")
        XCTAssertEqual(KXCopy.signedPercent(nil), "—")
        XCTAssertEqual(KXCopy.signedPercent(8.84, 1), "+8.8%")
        XCTAssertEqual(KXCopy.percent(2.14), "2.14%")
        XCTAssertEqual(KXCopy.percent(nil), "—")
        XCTAssertEqual(KXCopy.yi(15.64), "15.6亿")
        XCTAssertEqual(KXCopy.yi(nil), "—")
        XCTAssertEqual(KXCopy.volume(lots: 81481), "8.1万手")
        XCTAssertEqual(KXCopy.volume(lots: 2_248_000), "225万手")
        XCTAssertEqual(KXCopy.volume(lots: 9_500), "9500手")
        XCTAssertEqual(KXCopy.volume(lots: nil), "—")
        XCTAssertEqual(KXCopy.marketValue(yi: 6.5), "6.5亿")
        XCTAssertEqual(KXCopy.marketValue(yi: 128.1), "128亿")
        XCTAssertEqual(KXCopy.marketValue(yi: 3058), "3058亿")
        XCTAssertEqual(KXCopy.marketValue(yi: 12_000), "1.20万亿")
        XCTAssertEqual(KXCopy.marketValue(yi: nil), "—")
        XCTAssertEqual(KXCopy.ratio(1.5), "1.5×")
        XCTAssertEqual(KXCopy.ratio(nil), "—")
        XCTAssertEqual(KXCopy.pullback(-2.88), "距高 -2.9%")
        XCTAssertEqual(KXCopy.pullback(0), "距高 0.0%")
        XCTAssertEqual(KXCopy.pullback(-0.04), "距高 0.0%")
        XCTAssertEqual(KXCopy.pullback(nil), "—")
    }

    func testTrendFollowsDisplayedDigits() {
        XCTAssertEqual(KXTrend(1.36), .up)
        XCTAssertEqual(KXTrend(-0.01), .down)
        XCTAssertEqual(KXTrend(0.0), .flat)
        XCTAssertEqual(KXTrend(0.004), .flat, "显示 0.00% 的是平")
        XCTAssertEqual(KXTrend(-0.004), .flat)
        XCTAssertEqual(KXTrend(nil), .flat)
        XCTAssertEqual(KXTrend(0.04, digits: 1), .flat)
        XCTAssertEqual(KXTrend(0.06, digits: 1), .up)
        XCTAssertEqual(KXTrend.up.color, NKKX.up)
        XCTAssertEqual(KXTrend.down.color, NKKX.down)
        XCTAssertEqual(KXTrend.flat.color, NKKX.flat)
    }

    func testMissingValuesNeverBecomeZero() throws {
        let degraded = try decodeBoard("kx_board_degraded")
        let missing = try row(degraded, code: "000001")
        XCTAssertEqual(KXCopy.fixed(missing.price, 2), "—")
        XCTAssertEqual(KXCopy.signedPercent(missing.changePercent), "—")
        XCTAssertEqual(KXCopy.percent(missing.amplitudePercent), "—")
        XCTAssertEqual(KXCopy.yi(missing.turnoverYi), "—")
        XCTAssertEqual(KXCopy.volume(lots: missing.volumeLots), "—")
        XCTAssertEqual(KXCopy.pullback(missing.pullbackFromHighPercent), "—")
        XCTAssertEqual(KXCopy.ratio(missing.volumeRatio), "—")
        XCTAssertEqual(KXCopy.signedPercent(missing.change5dPercent, 1), "—")
        XCTAssertEqual(KXCopy.marketValue(yi: missing.circMvYi), "—")
        // 日线没取到但报价有：报价照常显示，日线那几格写「—」。
        let daily = try row(degraded, code: "000003")
        XCTAssertEqual(KXCopy.fixed(daily.price, 2), "11.05")
        XCTAssertEqual(KXCopy.ratio(daily.volumeRatio), "—")
        XCTAssertEqual(KXCopy.signedPercent(daily.change20dPercent, 1), "—")
    }

    // MARK: 页眉与页脚

    func testHeaderMetaAndFooter() throws {
        let sample = try decodeBoard("kx_board_sample")
        XCTAssertEqual(KXCopy.meta(board: sample, refreshFailed: false), "10:31 更新 · 盘中")
        XCTAssertEqual(KXCopy.meta(board: sample, refreshFailed: true), "10:31 更新 · 刷新失败")
        XCTAssertEqual(KXCopy.footerLines(board: sample), [
            "名单 kx-20261017 · 10:31 核对　报价 10:29:58",
            "最后收到推荐 10/20 10:20",
        ])

        let closed = try edited("kx_board_sample") { object in
            var session = object["session"] as! [String: Any]
            session["inSession"] = false
            session["refreshSeconds"] = NSNull()
            object["session"] = session
        }
        XCTAssertEqual(KXCopy.meta(board: closed, refreshFailed: false), "10:31 更新 · 已收盘")

        let degraded = try decodeBoard("kx_board_degraded")
        XCTAssertEqual(KXCopy.footerLines(board: degraded), [
            "kx-20261017 · 名单未能核对，最后核对 10/20 10:31　报价 10:29:58",
            "最后收到推荐 10/20 10:20",
            "TuShare 行情频率超限，这次没有重试。",
        ])

        // 核对时间不在同一天：写月日；没有推荐记录：写「—」；报价时间缺：不写报价。
        let old = try edited("kx_board_sample") { object in
            var pool = object["pool"] as! [String: Any]
            pool["checkedAt"] = "2026-10-19T09:00:00+08:00"
            object["pool"] = pool
            var session = object["session"] as! [String: Any]
            session["quoteTime"] = NSNull()
            object["session"] = session
            var picks = object["picks"] as! [String: Any]
            picks["lastPickAt"] = NSNull()
            object["picks"] = picks
        }
        XCTAssertEqual(KXCopy.footerLines(board: old), [
            "名单 kx-20261017 · 10/19 09:00 核对",
            "最后收到推荐 —",
        ])
    }

    func testClockIsBeijingTimeWhateverTheOffset() {
        XCTAssertEqual(KXCopy.clock("2026-10-20T10:31:07+08:00"), "10:31")
        XCTAssertEqual(KXCopy.clock("2026-10-20T02:31:07Z"), "10:31")
        XCTAssertEqual(KXCopy.clockWithSeconds("2026-10-20T10:29:58+08:00"), "10:29:58")
        XCTAssertEqual(KXCopy.monthDayClock("2026-10-19T10:28:00+08:00"), "10/19 10:28")
        XCTAssertNil(KXCopy.clock(nil))
        XCTAssertNil(KXCopy.clock("昨天"))
    }

    // MARK: 状态文案

    func testStateMessages() {
        let missing = KXCopy.message(for: .missingToken)
        XCTAssertEqual(missing.title, "还没有填写令牌")
        XCTAssertTrue(missing.settings)
        XCTAssertTrue(missing.retry)
        let unauthorized = KXCopy.message(for: .unauthorized)
        XCTAssertEqual(unauthorized.title, "令牌不正确")
        XCTAssertTrue(unauthorized.settings)

        let pool = KXCopy.message(for: .poolUnverified("KX 名单还没有成功同步过，暂不出看板：KX 暂时连不上。"))
        XCTAssertEqual(pool.title, "KX 名单还没有同步")
        XCTAssertEqual(pool.detail, "KX 名单还没有成功同步过，暂不出看板：KX 暂时连不上。")
        XCTAssertTrue(pool.retry)
        XCTAssertFalse(pool.settings)
        XCTAssertFalse(pool.problem, "不堆警告：状态页不用红色")
        XCTAssertNil(KXCopy.message(for: .poolUnverified("  ")).detail)

        let calendar = KXCopy.message(for: .calendarGap("交易日历没有覆盖今天，暂不出看板：2027-01-04"))
        XCTAssertEqual(calendar.title, "交易日历还没有覆盖今天")
        XCTAssertEqual(calendar.detail, "交易日历没有覆盖今天，暂不出看板：2027-01-04")

        XCTAssertEqual(KXCopy.message(for: .unreachable).title, "连不上服务")
        XCTAssertEqual(KXCopy.message(for: .unreadable).title, "KX 看板暂时读不了")
        XCTAssertEqual(KXCopy.message(for: .notConfigured).title, "服务还没配好")
        XCTAssertEqual(KXCopy.message(for: .dbNotReady).title, "数据库还没就绪")
        XCTAssertEqual(KXCopy.emptyPoolMessage.title, "名单里还没有股票")
    }

    // MARK: 辅助

    private func decodeBoard(_ name: String) throws -> KXBoard {
        try JSONDecoder().decode(KXBoard.self, from: fixture(name))
    }

    private func edited(_ name: String, _ edit: (inout [String: Any]) -> Void) throws -> KXBoard {
        var object = try JSONSerialization.jsonObject(with: fixture(name)) as! [String: Any]
        edit(&object)
        return try JSONDecoder().decode(KXBoard.self, from: JSONSerialization.data(withJSONObject: object))
    }

    /// 以 schema 2 夹具的第一行为底，换上指定的题材、热度、能否买入（nil 写成 null）。
    private func themedRow(theme: String?, heatDay: Int?, buyable: Bool?) throws -> KXRow {
        let board = try edited("kx_board_schema2") { object in
            var rows = object["rows"] as! [[String: Any]]
            rows[0]["theme"] = theme ?? NSNull()
            rows[0]["heatDay"] = heatDay ?? NSNull()
            rows[0]["buyable"] = buyable ?? NSNull()
            object["rows"] = rows
        }
        return board.rows[0]
    }

    /// 把视图按给定宽度画成位图（高度取自然高度）。
    @MainActor
    private func render<V: View>(_ view: V, width: CGFloat, scale: CGFloat) throws -> CGImage {
        let renderer = ImageRenderer(content: view.frame(width: width).environment(\.colorScheme, .light))
        renderer.scale = scale
        return try XCTUnwrap(renderer.cgImage, "没能画出位图")
    }

    private func pixels(of image: CGImage) throws -> [UInt8] {
        let width = image.width, height = image.height
        var data = [UInt8](repeating: 0, count: width * height * 4)
        let drawn = data.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
                                          space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        XCTAssertTrue(drawn, "没能读出像素")
        return data
    }

    private func row(_ board: KXBoard, code: String) throws -> KXRow {
        try XCTUnwrap(board.rows.first { $0.code == code }, "夹具里没有 \(code)")
    }

    private func fixture(_ name: String) throws -> Data {
        let bundle = Bundle(for: KXBoardContractTests.self)
        let url = bundle.url(forResource: name, withExtension: "json")
            ?? bundle.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
        return try Data(contentsOf: try XCTUnwrap(url, "缺少夹具 \(name).json"))
    }

    private func expectFailure(_ expected: KXBoardError, status: Int, body: String, file: StaticString = #filePath, line: UInt = #line) async {
        let transport = KXScriptTransport()
        transport.next = (status, Data(body.utf8))
        let api = KXBoardAPI(baseURL: URL(string: "https://fixture.invalid")!, token: "fixture-token-not-real", transport: transport)
        await expectThrows(expected, file: file, line: line) { _ = try await api.board() }
    }

    private func expectThrows(_ expected: KXBoardError, file: StaticString = #filePath, line: UInt = #line, _ call: () async throws -> Void) async {
        do {
            try await call()
            XCTFail("应当抛出 \(expected)", file: file, line: line)
        } catch let error as KXBoardError {
            XCTAssertEqual(error, expected, file: file, line: line)
        } catch {
            XCTFail("应当抛出 \(expected)，实际 \(error)", file: file, line: line)
        }
    }
}

private final class KXScriptTransport: MorningTransport, @unchecked Sendable {
    var next: (Int, Data)?
    var error: Error?
    private let lock = NSLock()
    private var stored: [URLRequest] = []

    init(error: Error? = nil) { self.error = error }

    var requests: [URLRequest] {
        lock.lock()
        defer { lock.unlock() }
        return stored
    }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        lock.lock()
        stored.append(request)
        let error = error
        let next = next
        lock.unlock()
        if let error { throw error }
        let (status, data) = next ?? (500, Data())
        let url = request.url ?? URL(string: "https://fixture.invalid")!
        guard let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: nil) else {
            throw URLError(.badServerResponse)
        }
        return (data, response)
    }
}
