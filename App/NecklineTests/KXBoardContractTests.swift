import XCTest
@testable import Neckline

/// 看板 v2 的解码和展示规则。六份后端夹具都要过，未知枚举不能让整份失败。
final class KXBoardContractTests: XCTestCase {
    func testDecodesSixBackendFixtures() throws {
        let closed = try decode("kx_board_v3")
        XCTAssertEqual(closed.generatedAt, "2026-11-04T18:30:10+08:00")
        XCTAssertEqual(closed.source?.checkedAt, "2026-11-04T18:30:10.318216+08:00")
        XCTAssertEqual(closed.source?.stale, false)
        XCTAssertEqual(closed.structure?.version, "kx-20261104-003")
        XCTAssertEqual(closed.structure?.asOf, "2026-11-03")
        XCTAssertEqual(closed.structure?.deepseekStatus, .ok)
        XCTAssertEqual(closed.structure?.generatedAt, "2026-11-04T15:39:13.328208+08:00")
        XCTAssertEqual(closed.status, "partial")
        XCTAssertEqual(closed.session?.inSession, false)
        XCTAssertNil(closed.session?.refreshSeconds)
        XCTAssertEqual(closed.sentiment?.limitUpCount, 96)
        XCTAssertEqual(closed.sentiment?.brokenCount, 30)
        XCTAssertEqual(closed.sentiment?.brokenRate ?? 0, 0.2381, accuracy: 0.0000001)
        XCTAssertEqual(closed.sentiment?.limitDownCount, 10)
        XCTAssertEqual(closed.sentiment?.highestBoard?.boards, 3)
        XCTAssertEqual(closed.sentiment?.highestBoard?.names.count, 6)
        XCTAssertEqual(closed.rooms.ordered.map(\.room), [.leader, .waiting, .eliminated])
        XCTAssertEqual(closed.rooms.ordered.map { $0.groups.count }, [1, 1, 3])
        XCTAssertEqual(closed.rooms.leader.first?.leaders.map(\.code), ["600261", "002086"])
        XCTAssertEqual(closed.rooms.leader.first?.followers.map(\.code), ["002696", "002172", "002088", "002029"])
        XCTAssertEqual(try stock(closed, "002088").limitUp.boardTypeText, "换手板")
        XCTAssertEqual(try stock(closed, "000678").divergent, true)

        let partial = try decode("kx_board_v3_partial")
        XCTAssertEqual(partial.structure?.deepseekStatus, .unknown("partial"))
        XCTAssertEqual(partial.structure?.deepseekNotice, "DeepSeek 部分未完成：分组和小弟未更新，沿用上一版小弟")
        XCTAssertEqual(partial.structure?.refresh?.notice, "今日未更新")
        XCTAssertEqual(partial.factWarnings.first?.groupId, "g-600261-20261103")

        let sealed = try decode("kx_board_v3_sealed")
        let leader = try stock(sealed, "600261")
        XCTAssertEqual(leader.orderBook?.sealed, true)
        XCTAssertEqual(leader.orderBook?.sealAmount, 199_000_000)
        XCTAssertEqual(leader.orderBook?.sealChange5m?.amount, -12_000_000)
        XCTAssertEqual(sealed.session?.inSession, true)
        XCTAssertEqual(sealed.session?.refreshSeconds, 60)

        let auction = try decode("kx_board_v3_auction")
        XCTAssertEqual(try stock(auction, "600261").quote.sessionPhase, .callAuction)
        XCTAssertEqual(auction.source?.checkedAt, "2026-11-04T09:18:00+08:00")
        XCTAssertEqual(auction.sentiment?.basis, "交易时段")

        let stale = try decode("kx_board_v3_stale")
        XCTAssertEqual(stale.source?.stale, true)
        XCTAssertEqual(stale.source?.reason, "KX 暂时连不上。")
        XCTAssertTrue(stale.rooms.waiting.isEmpty)
        XCTAssertFalse(stale.rooms.leader.isEmpty)

        let failure = try JSONDecoder().decode(KXFailureBody.self, from: fixture("kx_board_unavailable"))
        XCTAssertEqual(failure.reason, "kx_unavailable")
        XCTAssertEqual(failure.message, "KX 看板还没读到过：KX 暂时连不上。")
        XCTAssertThrowsError(try JSONDecoder().decode(KXBoard.self, from: fixture("kx_board_unavailable")))
    }

    func testUnknownEnumsAndMissingFieldsDoNotFailTheBoard() throws {
        let board = try edited("kx_board_v3") { object in
            var structure = object["structure"] as! [String: Any]
            structure["deepseekStatus"] = "later"
            object["structure"] = structure
            var rooms = object["rooms"] as! [String: Any]
            var leader = rooms["leader"] as! [[String: Any]]
            var leaders = leader[0]["leaders"] as! [[String: Any]]
            leaders[0]["role"] = "中军"
            var quote = leaders[0]["quote"] as! [String: Any]
            quote["quoteStatus"] = "sideways"
            quote["sessionPhase"] = "lunch"
            quote.removeValue(forKey: "price")
            leaders[0]["quote"] = quote
            leader[0]["leaders"] = leaders
            var flags = leader[0]["flags"] as? [[String: Any]] ?? []
            flags.append(["kind": "观察", "code": NSNull(), "text": "观察"])
            leader[0]["flags"] = flags
            rooms["leader"] = leader
            rooms.removeValue(forKey: "waiting")
            object["rooms"] = rooms
            object["factWarnings"] = ["格式不符的原文"]
        }
        XCTAssertEqual(board.structure?.deepseekStatus, .unknown("later"))
        let leader = try XCTUnwrap(board.rooms.leader.first?.leaders.first)
        XCTAssertEqual(leader.role, .unknown("中军"))
        XCTAssertEqual(leader.quote.quoteStatus, .unknown("sideways"))
        XCTAssertEqual(leader.quote.sessionPhase, .unknown("lunch"))
        XCTAssertNil(leader.quote.price)
        XCTAssertEqual(board.rooms.leader.first?.flags.last?.kind, .unknown("观察"))
        XCTAssertTrue(board.rooms.waiting.isEmpty)
        XCTAssertEqual(board.factWarnings.map(\.text), ["格式不符的原文"])
        XCTAssertNil(board.factWarnings.first?.groupId)

        let odd = try edited("kx_board_v3") { object in
            var structure = object["structure"] as! [String: Any]
            structure["deepseekStatus"] = true
            object["structure"] = structure
            object["status"] = 3
        }
        XCTAssertNil(odd.structure?.deepseekStatus)
        XCTAssertNil(odd.status)
        XCTAssertEqual(odd.rooms.leader.count, 1)
    }

    func testTodayStatusAndPoolFacts() throws {
        let up = try limit(#"{"inLimitUp":true,"inBroken":false,"inLimitDown":false}"#)
        XCTAssertEqual(KXCopy.todayStatus(up).map(\.text), ["涨停"])
        XCTAssertEqual(KXCopy.todayStatus(up).map(\.tone), [.up])

        let broken = try limit(#"{"inLimitUp":false,"inBroken":true,"inLimitDown":false}"#)
        XCTAssertEqual(KXCopy.todayStatus(broken).map(\.tone), [.broken])

        let down = try limit(#"{"inLimitUp":false,"inBroken":false,"inLimitDown":true}"#)
        XCTAssertEqual(KXCopy.todayStatus(down).map(\.text), ["跌停"])
        XCTAssertEqual(KXCopy.todayStatus(down).map(\.tone), [.down])
        XCTAssertNil(KXCopy.poolFacts(down))

        let both = try limit(#"{"inLimitUp":false,"inBroken":true,"inLimitDown":true}"#)
        XCTAssertEqual(KXCopy.todayStatus(both).map(\.text), ["炸板", "跌停"])
        XCTAssertEqual(KXCopy.todayStatus(both).map(\.tone), [.broken, .down])

        let quiet = try limit(#"{"inLimitUp":false,"inBroken":false,"inLimitDown":false}"#)
        XCTAssertEqual(KXCopy.todayStatus(quiet).map(\.text), ["未涨停"])
        XCTAssertEqual(KXCopy.todayStatus(quiet).map(\.tone), [.off])

        let missing = try limit(#"{}"#)
        XCTAssertEqual(KXCopy.todayStatus(missing).map(\.text), ["—"])
        XCTAssertEqual(KXCopy.todayStatus(missing).map(\.tone), [.missing])

        let mixed = try limit(#"{"inLimitUp":false,"inBroken":null}"#)
        XCTAssertEqual(KXCopy.todayStatus(mixed).map(\.text), ["—"])

        let closed = try decode("kx_board_v3")
        let wolf = try stock(closed, "002029")
        XCTAssertEqual(KXCopy.poolFacts(wolf.limitUp)?.map(\.text), ["— 板", "—", "首封 09:25", "开板 1"])
        let follower = try stock(closed, "002088")
        XCTAssertEqual(KXCopy.poolFacts(follower.limitUp)?.map(\.text), ["2 板", "换手板", "首封 09:30", "开板 2"])
        XCTAssertFalse(KXCopy.poolFacts(follower.limitUp)!.contains { $0.text.contains("一字") })

        let sealed = try stock(try decode("kx_board_v3_sealed"), "600261")
        XCTAssertEqual(KXCopy.todayStatus(sealed.limitUp).map(\.text), ["涨停"])
        XCTAssertEqual(
            KXCopy.poolFacts(sealed.limitUp)?.map(\.text),
            ["5 板", "开盘封死，至今没开板", "首封 09:30", "开板 0"]
        )
    }

    func testOrderLineSealBidWarningsAndAuction() throws {
        let closed = try decode("kx_board_v3")
        let sun = try stock(closed, "600261")
        let bid = try XCTUnwrap(KXCopy.orderLine(sun))
        XCTAssertEqual(bid.kind, .bid)
        XCTAssertEqual(bid.amount, "—")
        XCTAssertNil(bid.delta)
        XCTAssertEqual(bid.warnings, "买一价无效。")
        XCTAssertNil(KXCopy.extraWarnings(sun))

        let ocean = try stock(closed, "002086")
        XCTAssertEqual(KXCopy.orderLine(ocean)?.kind, .bid)
        XCTAssertEqual(KXCopy.orderLine(ocean)?.amount, "112.7万")
        XCTAssertNil(KXCopy.orderLine(ocean)?.warnings)

        XCTAssertNil(KXCopy.orderLine(try stock(closed, "002724")))

        let sealed = try stock(try decode("kx_board_v3_sealed"), "600261")
        let seal = try XCTUnwrap(KXCopy.orderLine(sealed))
        XCTAssertEqual(seal.kind, .seal)
        XCTAssertEqual(seal.amount, "1.99亿")
        XCTAssertEqual(seal.delta, KXDelta(text: "−1200万", direction: .down))
        XCTAssertNil(seal.warnings)

        let auction = try stock(try decode("kx_board_v3_auction"), "600261")
        XCTAssertTrue(KXCopy.isAuction(auction))
        XCTAssertEqual(KXCopy.orderLine(auction)?.kind, .bid)
        XCTAssertEqual(KXCopy.orderLine(auction)?.warnings, "集合竞价阶段无法判断封单")
        XCTAssertFalse(KXCopy.orderLine(auction)!.amount.contains("封单"))

        let forced = try decodeStock("""
        {"code":"600261","name":"阳光照明","quote":{"sessionPhase":"call_auction"},
         "orderBook":{"sealed":true,"sealAmount":199000000,"sealChange5m":{"amount":12000000},"warnings":["甲","乙"]}}
        """)
        XCTAssertEqual(KXCopy.orderLine(forced)?.kind, .bid)
        XCTAssertEqual(KXCopy.orderLine(forced)?.warnings, "甲；乙")
        XCTAssertEqual(KXCopy.sealDelta(12_000_000), KXDelta(text: "+1200万", direction: .up))
        XCTAssertEqual(KXCopy.sealDelta(0)?.text, "0")
        XCTAssertNil(KXCopy.sealDelta(nil))

        let extra = try decodeStock("""
        {"code":"1","name":"甲","quote":{"warnings":["报价缺字段"]},"limitUp":{"warnings":["池子缺字段"]},"orderBook":{"sealed":false,"warnings":["报价缺字段"]}}
        """)
        XCTAssertEqual(KXCopy.extraWarnings(extra), "池子缺字段")
    }

    func testAmountAndBrokenRate() {
        XCTAssertEqual(KXCopy.amount(199_000_000), "1.99亿")
        XCTAssertEqual(KXCopy.amount(100_000_000), "1亿")
        XCTAssertEqual(KXCopy.amount(12_000_000), "1200万")
        XCTAssertEqual(KXCopy.amount(1_126_842), "112.7万")
        XCTAssertEqual(KXCopy.amount(5_000), "0.5万")
        XCTAssertEqual(KXCopy.amount(nil), "—")
        XCTAssertEqual(KXCopy.brokenRate(0.2381), "23.8%")
        XCTAssertEqual(KXCopy.brokenRate(0), "0.0%")
        XCTAssertEqual(KXCopy.brokenRate(nil), "—")
        XCTAssertEqual(KXCopy.price(4.46), "4.46")
        XCTAssertEqual(KXCopy.percent(-9.899), "-9.90%")
        XCTAssertEqual(KXCopy.percent(9.964), "+9.96%")
        XCTAssertEqual(KXCopy.percent(0), "0.00%")
        XCTAssertEqual(KXCopy.trend(0), .flat)
        XCTAssertEqual(KXCopy.percent(nil), "—")
    }

    func testFactWarningsLandOnTheGroupOrThePage() throws {
        let partial = try decode("kx_board_v3_partial")
        let leader = try XCTUnwrap(partial.rooms.leader.first)
        XCTAssertEqual(KXCopy.mismatches(group: leader, board: partial), ["依据里写“连续4板”，麦蕊涨停池当天为 3 板"])
        XCTAssertTrue(KXCopy.orphanFactTexts(partial).isEmpty)
        XCTAssertFalse(KXCopy.grayLine(board: partial)?.contains("连续4板") ?? true)

        let edited = try edited("kx_board_v3_partial") { object in
            object["factWarnings"] = [
                ["groupId": "g-600261-20261103", "text": "对得上"],
                ["groupId": "", "text": "空组"],
                ["groupId": "missing-group", "text": "找不到组"],
            ]
        }
        XCTAssertEqual(KXCopy.mismatches(group: try XCTUnwrap(edited.rooms.leader.first), board: edited), ["对得上"])
        XCTAssertEqual(KXCopy.orphanFactTexts(edited), ["空组", "找不到组"])
        XCTAssertEqual(
            KXCopy.grayLine(board: edited),
            "今日未更新 · 部分行情不完整 · 这句与麦蕊数据不符：空组；找不到组"
        )
    }

    func testAmberAndGrayNotices() throws {
        let closed = try decode("kx_board_v3")
        XCTAssertTrue(KXCopy.amberLines(board: closed).isEmpty)
        XCTAssertEqual(KXCopy.grayLine(board: closed), "部分行情不完整")

        let partial = try decode("kx_board_v3_partial")
        XCTAssertEqual(KXCopy.amberLines(board: partial), ["DeepSeek 部分未完成：分组和小弟未更新，沿用上一版小弟"])
        XCTAssertEqual(KXCopy.grayLine(board: partial), "今日未更新 · 部分行情不完整")

        let stale = try decode("kx_board_v3_stale")
        XCTAssertEqual(KXCopy.amberLines(board: stale), ["未能核对，最后读到 18:30"])
        XCTAssertEqual(KXCopy.grayLine(board: stale), "部分行情不完整")

        let pending = try edited("kx_board_v3") { object in
            var structure = object["structure"] as! [String: Any]
            structure["deepseekStatus"] = "failed"
            structure["deepseekNotice"] = NSNull()
            var refresh = structure["refresh"] as! [String: Any]
            refresh["notice"] = "今日未更新"
            structure["refresh"] = refresh
            object["structure"] = structure
            object["gaps"] = ["1 组数据不合格，没有显示"]
            object["status"] = "ok"
        }
        XCTAssertEqual(KXCopy.amberLines(board: pending), ["DeepSeek 未完成"])
        XCTAssertEqual(KXCopy.grayLine(board: pending), "今日未更新 · 1 组数据不合格，没有显示")

        let noted = try edited("kx_board_v3") { object in
            var structure = object["structure"] as! [String: Any]
            structure["deepseekNotice"] = "补充一句"
            object["structure"] = structure
            object["status"] = "ok"
        }
        XCTAssertTrue(KXCopy.amberLines(board: noted).isEmpty)
        XCTAssertEqual(KXCopy.grayLine(board: noted), "补充一句")

        // 状态缺失（或类型不对）时，非空说明不能被藏掉：按未完成标出；没有说明就什么都不标。
        for raw in [NSNull(), 3 as Any] as [Any] {
            let unstated = try edited("kx_board_v3") { object in
                var structure = object["structure"] as! [String: Any]
                structure["deepseekStatus"] = raw
                structure["deepseekNotice"] = "DeepSeek 还在整理"
                object["structure"] = structure
                object["status"] = "ok"
            }
            XCTAssertEqual(KXCopy.amberLines(board: unstated), ["DeepSeek 还在整理"])
            XCTAssertNil(KXCopy.grayLine(board: unstated))
        }
        let silent = try edited("kx_board_v3") { object in
            var structure = object["structure"] as! [String: Any]
            structure.removeValue(forKey: "deepseekStatus")
            structure["deepseekNotice"] = NSNull()
            object["structure"] = structure
            object["status"] = "ok"
        }
        XCTAssertTrue(KXCopy.amberLines(board: silent).isEmpty)
    }

    func testHeaderAndFooterUseBeijingTime() throws {
        let closed = try decode("kx_board_v3")
        XCTAssertEqual(KXCopy.headerMeta(board: closed, refreshFailed: false), "18:30 · 已收盘")
        XCTAssertEqual(KXCopy.headerMeta(board: closed, refreshFailed: true), "18:30 · 刷新失败")
        XCTAssertEqual(KXCopy.headerSub(board: closed), "按 11/03 收盘整理")
        XCTAssertEqual(
            KXCopy.footer(closed).line,
            "11/04 · 读取 18:30:10 · 报价 15:00:02 · 涨停池 18:30:10 · 结构写入 15:39:13"
        )
        XCTAssertEqual(KXCopy.footer(closed).version, "kx-20261104-003")

        let sealed = try decode("kx_board_v3_sealed")
        XCTAssertEqual(KXCopy.headerMeta(board: sealed, refreshFailed: false), "10:30 · 盘中")
        let auction = try decode("kx_board_v3_auction")
        XCTAssertEqual(KXCopy.headerMeta(board: auction, refreshFailed: false), "09:18 · 盘中")
        XCTAssertTrue(KXCopy.sentiment(auction.sentiment).intraday)
        XCTAssertFalse(KXCopy.sentiment(closed.sentiment).intraday)
        XCTAssertEqual(KXCopy.sentiment(closed.sentiment).limitUp, "96")
        XCTAssertEqual(KXCopy.sentiment(closed.sentiment).brokenRate, "23.8%")
        XCTAssertEqual(KXCopy.sentiment(closed.sentiment).nameTitle, "6 只的名字")
        XCTAssertEqual(KXCopy.sentiment(closed.sentiment).yesterday, "昨日连板 15 只，今日继续 6 只")

        XCTAssertEqual(KXCopy.clock("2026-11-04T02:30:10Z"), "10:30")
        XCTAssertEqual(KXCopy.clock("2026-11-04T18:30:10.318216+08:00"), "18:30")
        XCTAssertEqual(KXCopy.monthDay("2026-11-03"), "11/03")
        XCTAssertNil(KXCopy.clock(nil))
        XCTAssertNil(KXCopy.clock("昨天"))

        let shifted = try edited("kx_board_v3") { object in
            var rooms = object["rooms"] as! [String: Any]
            for name in ["leader", "waiting", "eliminated"] {
                var groups = rooms[name] as! [[String: Any]]
                for index in groups.indices {
                    for key in ["leaders", "followers"] {
                        var people = groups[index][key] as! [[String: Any]]
                        for person in people.indices {
                            var quote = people[person]["quote"] as! [String: Any]
                            quote["sourceTime"] = "2026-11-02T15:00:00+08:00"
                            people[person]["quote"] = quote
                        }
                        groups[index][key] = people
                    }
                }
                rooms[name] = groups
            }
            object["rooms"] = rooms
        }
        XCTAssertTrue(KXCopy.footer(shifted).line.contains("报价 11/02 15:00:00"))
    }

    func testChromeDoesNotAddTradingAdvice() throws {
        let board = try decode("kx_board_v3")
        let chrome = [
            KXCopy.headerMeta(board: board, refreshFailed: false),
            KXCopy.headerSub(board: board),
            KXCopy.grayLine(board: board) ?? "",
            KXCopy.footer(board).line,
            KXCopy.emptyRoom,
            "封单", "买一挂单", "未涨停", "竞价中", "涨停", "炸板", "跌停",
        ]
        for word in ["买入", "卖出", "持仓", "收益"] {
            XCTAssertFalse(chrome.contains { $0.contains(word) }, word)
        }
        let leader = try XCTUnwrap(board.rooms.leader.first)
        if case .judgment(let verdict, _, let title, _)? = KXCopy.deepseekBody(leader) {
            XCTAssertEqual(verdict, "晋升")
            XCTAssertEqual(title, "高度、带动、承接、说明")
        } else {
            XCTFail("龙头组应有结论")
        }
        let eliminated = try XCTUnwrap(board.rooms.eliminated.first)
        XCTAssertEqual(KXCopy.eliminatedLine(eliminated), "断板日 10/26 · 已观察 6 个交易日（满 10 个交易日踢出）")
        XCTAssertEqual(KXCopy.flagLabels(try XCTUnwrap(board.rooms.eliminated.dropFirst().first)).contains("背离 000678"), true)
        if case .note = KXCopy.deepseekBody(eliminated) {
        } else {
            XCTFail("没有结论时只留说明")
        }
    }

    func testClientRequestsBoardAndMapsFailures() async throws {
        let transport = KXScriptTransport()
        transport.next = (200, try fixture("kx_board_v3"))
        let api = KXBoardAPI(baseURL: URL(string: "https://fixture.invalid")!, token: " fixture-token-not-real ", transport: transport)
        let board = try await api.board()
        XCTAssertEqual(board.rooms.leader.count, 1)
        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.url?.path, "/api/v4/kx/board")
        XCTAssertNil(request.url?.query)
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-token-not-real")

        let prefixed = KXBoardAPI(baseURL: URL(string: "https://fixture.invalid/nk/")!, token: "fixture-token-not-real", transport: transport)
        _ = try await prefixed.board()
        XCTAssertEqual(transport.requests.last?.url?.path, "/nk/api/v4/kx/board")

        await expectFailure(.unauthorized, status: 401, body: #"{"detail":"未授权"}"#)
        await expectFailure(.notConfigured, status: 503, body: #"{"detail":{"reason":"v4_not_configured"}}"#)
        await expectFailure(.dbNotReady, status: 503, body: #"{"detail":{"reason":"v4_db_not_ready"}}"#)
        await expectFailure(
            .unavailable("KX 看板还没读到过：KX 暂时连不上。"),
            status: 503,
            body: String(data: try fixture("kx_board_unavailable"), encoding: .utf8)!
        )
        await expectFailure(.unavailable(nil), status: 503, body: #"{"detail":{"reason":"kx_unavailable"}}"#)
        await expectFailure(.unreadable, status: 503, body: #"{"detail":{"reason":"pool_unverified","message":"旧原因"}}"#)
        await expectFailure(.unreadable, status: 503, body: #"{"detail":{"reason":"calendar_gap"}}"#)
        await expectFailure(.unreadable, status: 500, body: "")
        await expectFailure(.unreadable, status: 200, body: "{}")
        await expectFailure(.unreadable, status: 200, body: "not json")

        let offline = KXScriptTransport(error: URLError(.cannotConnectToHost))
        let disconnected = KXBoardAPI(baseURL: URL(string: "https://fixture.invalid")!, token: "fixture-token-not-real", transport: offline)
        await expectThrows(KXBoardError.unreachable) { _ = try await disconnected.board() }

        let trap = KXScriptTransport(error: URLError(.badServerResponse))
        let blank = KXBoardAPI(baseURL: URL(string: "https://fixture.invalid")!, token: "   ", transport: trap)
        await expectThrows(KXBoardError.missingToken) { _ = try await blank.board() }
        XCTAssertTrue(trap.requests.isEmpty)

        let cancelled = KXScriptTransport(error: URLError(.cancelled))
        let cancelling = KXBoardAPI(baseURL: URL(string: "https://fixture.invalid")!, token: "fixture-token-not-real", transport: cancelled)
        do {
            _ = try await cancelling.board()
            XCTFail("取消应当抛出 CancellationError")
        } catch is CancellationError {
        } catch {
            XCTFail("取消应当抛出 CancellationError，实际 \(error)")
        }

        XCTAssertFalse(KXBoardError.unavailable("原因").isTransient)
        XCTAssertFalse(KXBoardError.unauthorized.isTransient)
        XCTAssertTrue(KXBoardError.unreachable.isTransient)
        XCTAssertTrue(KXBoardError.unreadable.isTransient)

        let message = KXCopy.message(for: .unavailable("KX 看板还没读到过：KX 暂时连不上。"))
        XCTAssertEqual(message.title, "KX 看板暂时读不了")
        XCTAssertEqual(message.detail, "KX 看板还没读到过：KX 暂时连不上。")
        XCTAssertEqual(KXCopy.message(for: .unreachable).title, "连不上服务")
        XCTAssertEqual(KXCopy.message(for: .unreadable).title, "KX 看板暂时读不了")
        XCTAssertNil(KXCopy.message(for: .unreadable).detail)
        XCTAssertEqual(KXCopy.message(for: .notConfigured).title, "服务还没配好")
        XCTAssertEqual(KXCopy.message(for: .dbNotReady).title, "数据库还没就绪")
        XCTAssertNil(KXCopy.message(for: .unavailable("  ")).detail)
    }

    private func decode(_ name: String) throws -> KXBoard {
        try JSONDecoder().decode(KXBoard.self, from: fixture(name))
    }

    private func edited(_ name: String, _ edit: (inout [String: Any]) -> Void) throws -> KXBoard {
        var object = try JSONSerialization.jsonObject(with: fixture(name)) as! [String: Any]
        edit(&object)
        return try JSONDecoder().decode(KXBoard.self, from: JSONSerialization.data(withJSONObject: object))
    }

    private func limit(_ json: String) throws -> KXLimitUp {
        try JSONDecoder().decode(KXLimitUp.self, from: Data(json.utf8))
    }

    private func decodeStock(_ json: String) throws -> KXStock {
        try JSONDecoder().decode(KXStock.self, from: Data(json.utf8))
    }

    private func stock(_ board: KXBoard, _ code: String, file: StaticString = #filePath, line: UInt = #line) throws -> KXStock {
        let all = board.rooms.ordered.flatMap { $0.groups.flatMap { $0.leaders + $0.followers } }
        return try XCTUnwrap(all.first { $0.code == code }, "没有 \(code)", file: file, line: line)
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
