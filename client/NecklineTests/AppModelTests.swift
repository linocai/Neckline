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

    /// v1.3-①/⑥:`isExitDay` 自本版起由服务端权威 `timeExitState` 驱动(不再由客户端
    /// 重新比较 `dCount >= maxHoldDays`)——这里直接构造 Position 时必须显式给
    /// `timeExitState`,才能反映"服务端在这个 dCount 会怎么判"(K1 单档口径下,
    /// `time_exit_next_day` 恰好等价于 `dCount >= maxHoldDays`,见 `sentinel/precall.py::
    /// classify_time_exit` config 未启用分支)。
    func testPositionIsExitDayWhenDCountReachesMaxHoldDays() {
        var p = Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260710", price: 10.0, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false, dCount: 5, maxHoldDays: 5,
                         maxHoldDaysEffective: 5, timeExitState: PositionTimeExitState.timeExitNextDayRaw)
        XCTAssertTrue(p.isExitDay)
        XCTAssertEqual(p.todayActionTone, .bad, "D5 时间退出必须最高优先醒目")

        p.dCount = 6   // 改 config 到更短的 hold 天数后,超过也仍算 exit day(不是恰好相等才算)
        XCTAssertTrue(p.isExitDay)

        p.dCount = 3
        p.timeExitState = PositionTimeExitState.holdingRaw   // 服务端此时应判 holding,非 time_exit_next_day
        XCTAssertFalse(p.isExitDay)
    }

    /// v1.3-①/⑥ 两档 D 徽标核心场景:`profitExempt`(浮盈豁免续持到 D15)**不是**离场提示
    /// ——即便 `dCount` 已经 ≥ 旧单档 `maxHoldDays`(5),只要服务端判 `profit_exempt`,
    /// `isExitDay` 必须为 false、`todayActionTone` 不得是 `.bad`(§五 v1.3-⑥-A 明文
    /// "profit_exempt 是持有态,不要当离场提示展示")。这正是本版把 `isExitDay` 判据从
    /// `dCount>=maxHoldDays` 迁到 `timeExitState` 的根本原因——旧口径会在这里误报。
    func testPositionProfitExemptIsNotExitDayEvenPastOldSingleTierThreshold() {
        let p = Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260701", price: 12.0, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false, dCount: 8, maxHoldDays: 5,
                         maxHoldDaysEffective: 15, timeExitState: PositionTimeExitState.profitExemptRaw)
        XCTAssertFalse(p.isExitDay, "浮盈豁免续持不是离场日,即便 dCount(8) 已过旧单档阈值(5)")
        XCTAssertEqual(p.todayActionTone, .good, "浮盈豁免是持有态,不应染成警示红")
        XCTAssertEqual(p.timeExitKind, .profitExempt)
    }

    /// 硬上限到期(D15)**是**离场提示——两档里唯二的"该走了"态之一(另一是非浮盈 D5)。
    func testPositionHardCapExitIsExitDay() {
        let p = Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260601", price: 13.0, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false, dCount: 15, maxHoldDays: 5,
                         maxHoldDaysEffective: 15, timeExitState: PositionTimeExitState.hardCapExitRaw)
        XCTAssertTrue(p.isExitDay)
        XCTAssertEqual(p.todayActionTone, .bad)
        XCTAssertEqual(p.timeExitKind, .hardCapExit)
    }

    /// 未识别 `timeExitState` 字符串兜底 `.holding`(不误报离场,同 `PositionQuota`/
    /// `InquiryVerdict` 等既有"未识别值归中性态"先例)。
    func testPositionUnknownTimeExitStateFallsBackToHoldingNotExitDay() {
        let p = Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260710", price: 10.0, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false, dCount: 5, maxHoldDays: 5,
                         timeExitState: "some_future_state")
        XCTAssertEqual(p.timeExitKind, .holding)
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

    // MARK: - §五 v1.2-E.1/E.5 决策日志优先流程 + 一键补录预填区间(端到端,经
    // `MockURLProtocol` 免联网,同 DTODecodeTests 的桩姿势——验证
    // `AppModel.beginPositionEntryFlow(fromCandidate:)` 真的拉了 entry-suggestion
    // 并把区间写回状态,且流程先落在 `.decisionLog` 而非直接 `.open`
    // 〔v1.2-E.1「嵌『已按计划买入』流程之前」硬边界〕,不是只测 EntrySpec 纯函数)。

    func testBeginPositionEntryFlowFromCandidateOpensDecisionLogFirstAndPrefillsRange() async throws {
        MockURLProtocol.handler = { req in
            let url = req.url?.absoluteString ?? ""
            XCTAssertTrue(url.contains("/positions/entry-suggestion"))
            XCTAssertTrue(url.contains("code=600519.SH"))
            XCTAssertTrue(url.contains("price=1217.11"))
            return (200, """
            {"ok": true, "code": "600519.SH", "price": 1217.11,
             "qtyLow": 200, "qtyHigh": 400, "capFloor": 20000.0, "capCeil": 40000.0, "stopLine": 1156.25}
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
        await model.beginPositionEntryFlow(fromCandidate: candidate)

        // §五 v1.2-E.1 硬边界:先落决策日志表单,不是直接开仓表单。
        XCTAssertEqual(model.modal, .decisionLog)
        XCTAssertEqual(model.entryForm.code, "600519.SH")
        XCTAssertEqual(model.entryForm.name, "贵州茅台")
        XCTAssertEqual(model.entryForm.price, "1217.11")
        XCTAssertTrue(model.entryForm.reason.contains("回调低吸"))
        XCTAssertEqual(model.decisionForm.code, "600519.SH")
        XCTAssertEqual(model.decisionForm.plannedPrice, "1217.11")
        // v1.2-E.5:区间双档,不再预填单一 qty(不替用户拍单笔金额)。
        XCTAssertEqual(model.entrySuggestionRange?.qtyLow, 200)
        XCTAssertEqual(model.entrySuggestionRange?.qtyHigh, 400)
        XCTAssertEqual(model.entrySuggestionRange?.capCeil, 40000.0)
        XCTAssertEqual(model.entrySuggestionRange?.stopLine, 1156.25)
        XCTAssertEqual(model.entryForm.qty, "", "客户端不替用户拍单笔金额,qty 必须留空手填")
    }

    /// 买点参考价缺失(entrySpec 未算出)→ 价格留空手填,不虚构数字、不崩、仍能打开表单。
    func testBeginPositionEntryFlowFromCandidateWithoutEntrySpecLeavesPriceBlank() async throws {
        let model = AppModel(clientProvider: { nil })   // 无后端连接也不该崩
        let candidate = Candidate(rank: 1, code: "600001.SH", name: "甲", score: 80, board: "MAIN",
                                  buyPoint: "b", stop: "s", target: "t", invalidation: "i",
                                  formTags: [], hotSectors: [], sectorNames: [], llmJudgment: nil)
        await model.beginPositionEntryFlow(fromCandidate: candidate)
        XCTAssertEqual(model.entryForm.code, "600001.SH")
        XCTAssertEqual(model.entryForm.price, "", "缺 entrySpec 参考价时不得虚构数字")
        XCTAssertNil(model.entrySuggestionRange)
        XCTAssertEqual(model.modal, .decisionLog)
    }

    // MARK: - §五 v1.2-E.1 决策日志软约束:跳过 / 建计划→录八项→成交后关联 / 中途放弃

    func testSkipDecisionLogGoesStraightToOpenSheetWithoutHardBlocking() {
        let model = AppModel(clientProvider: { nil })
        model.beginPositionEntryFlow()
        model.decisionForm.code = "600001.SH"
        model.decisionForm.name = "甲"
        XCTAssertEqual(model.modal, .decisionLog)

        model.skipDecisionLog()

        XCTAssertEqual(model.modal, .open, "跳过必须能直接进入开仓补录,不做硬阻断(§三条本版硬约束②)")
        XCTAssertEqual(model.entryForm.code, "600001.SH", "跳过时已填的代码应带过去,不必重打")
        XCTAssertNil(model.pendingDecisionId)
    }

    /// 请求体逐字段核对(含「客户端传 createdAt 也会被忽略」)已在
    /// `DTODecodeTests.testCreateDecisionRequestBodyShapeAndCreatedAtNeverSent` 覆盖
    /// (`httpBodyOrStream()` 是该文件内 `private extension`,不跨文件复用);这里只
    /// 验证 `AppModel` 的状态编排——创建成功后正确转入 `.open` 并暂存 `pendingDecisionId`。
    func testSubmitDecisionLogCreatesThenTransitionsToOpenWithPendingId() async throws {
        MockURLProtocol.handler = { _ in
            (200, """
            {"id": 42, "code": "600001.SH", "name": "甲", "createdAt": "2026-07-25T10:00:00+00:00",
             "whyBuy": "题材热", "whyEntryPrice": "回调企稳", "targetPrice": null, "exitLow": null,
             "exitHigh": null, "thesisTags": ["THEME"], "invalidation": "跌破均线",
             "contingencyScenarios": [], "playbookTag": "SWING_CHASE", "plannedPrice": 10.0,
             "plannedQty": 1000, "status": "pending", "positionId": null, "revisionOf": null}
            """.data(using: .utf8)!)
        }
        defer { MockURLProtocol.handler = nil }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t",
                               session: URLSession(configuration: config))
        let model = AppModel(clientProvider: { client })
        model.beginPositionEntryFlow()
        model.decisionForm.code = "600001.SH"
        model.decisionForm.name = "甲"
        model.decisionForm.whyBuy = "题材热"
        model.decisionForm.whyEntryPrice = "回调企稳"
        model.decisionForm.invalidation = "跌破均线"

        await model.submitDecisionLog()

        XCTAssertEqual(model.pendingDecisionId, 42)
        XCTAssertEqual(model.modal, .open, "创建成功后应转入开仓补录表单(建计划→录八项→成交后关联)")
        XCTAssertEqual(model.entryForm.code, "600001.SH")
    }

    /// 用户在 `.open` 阶段中途放弃(dismissModal)→ 自动 cancel 该预注册计划,不留孤儿 pending 行。
    func testDismissModalDuringOpenAutoCancelsPendingDecision() async throws {
        var cancelledId: Int? = nil
        let expectation = XCTestExpectation(description: "cancel fired")
        MockURLProtocol.handler = { req in
            if req.url?.absoluteString.contains("/decisions/42/cancel") == true {
                cancelledId = 42
                expectation.fulfill()
            }
            return (200, """
            {"ok": true}
            """.data(using: .utf8)!)
        }
        defer { MockURLProtocol.handler = nil }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t",
                               session: URLSession(configuration: config))
        let model = AppModel(clientProvider: { client })
        model.modal = .open
        model.pendingDecisionId = 42

        model.dismissModal()

        await fulfillment(of: [expectation], timeout: 2.0)
        XCTAssertEqual(cancelledId, 42)
        XCTAssertNil(model.modal)
        XCTAssertNil(model.pendingDecisionId)
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
        // v1.3-①/⑥-B:实付买入费用 UI 强制必填(§五 v1.3-⑥-B 拍板口径),缺了仍不通过。
        XCTAssertFalse(form.isValid, "缺实付买入费用不应通过")
        form.buyFees = "12.5"
        XCTAssertTrue(form.isValid)
        form.price = "0"
        XCTAssertFalse(form.isValid, "买入价必须 > 0")
        form.price = "1500.0"
        form.buyFees = "0"
        XCTAssertTrue(form.isValid, "费用允许为 0(如实录入,不代表未填)")
        form.buyFees = "-1"
        XCTAssertFalse(form.isValid, "费用不能为负")
    }

    // MARK: - §五 v1.2-B/E.6 枚举码→中文展示层换算(沿 `nkBoardLabel` 先例,未识别透传)

    func testThesisTagLabelMapping() {
        XCTAssertEqual(nkThesisTagLabel("THEME"), "题材主线")
        XCTAssertEqual(nkThesisTagLabel("SENTIMENT_CYCLE"), "情绪周期位")
        XCTAssertEqual(nkThesisTagLabel("CAPITAL_FLOW"), "资金流向")
        XCTAssertEqual(nkThesisTagLabel("TECH_PATTERN"), "技术形态")
        XCTAssertEqual(nkThesisTagLabel("NEWS"), "消息")
        XCTAssertEqual(nkThesisTagLabel("SOME_FUTURE_CODE"), "SOME_FUTURE_CODE", "未识别值原样透传,不静默瞎翻译")
        XCTAssertEqual(ThesisTag.allCases.count, 5)
    }

    func testPlaybookTagLabelMapping() {
        XCTAssertEqual(nkPlaybookTagLabel("SWING_CHASE"), "短线追击")
        XCTAssertEqual(nkPlaybookTagLabel("BREATHING_TRIAL"), "呼吸底仓试验")
        XCTAssertEqual(nkPlaybookTagLabel("???"), "???")
        XCTAssertEqual(PlaybookTag.allCases.count, 2)
    }

    func testScenarioActionLabelMapping() {
        XCTAssertEqual(nkScenarioActionLabel("BUY"), "买入")
        XCTAssertEqual(nkScenarioActionLabel("HOLD"), "持有")
        XCTAssertEqual(nkScenarioActionLabel("REDUCE"), "减仓")
        XCTAssertEqual(nkScenarioActionLabel("ABANDON"), "放弃")
        XCTAssertEqual(nkScenarioActionLabel("UNKNOWN"), "UNKNOWN")
        XCTAssertEqual(ScenarioAction.allCases.count, 4)
    }

    func testCloseReasonLabelMapping() {
        XCTAssertEqual(nkCloseReasonLabel("STOP_LOSS"), "止损")
        XCTAssertEqual(nkCloseReasonLabel("TAKE_PROFIT"), "回落止盈")
        XCTAssertEqual(nkCloseReasonLabel("TIME_EXIT"), "时间退出")
        XCTAssertEqual(nkCloseReasonLabel("INVALIDATION"), "证伪离场")
        XCTAssertEqual(nkCloseReasonLabel("MANUAL"), "主动离场")
        XCTAssertEqual(nkCloseReasonLabel("???"), "???")
        XCTAssertEqual(CloseReasonCode.allCases.count, 5)
    }

    // MARK: - §五 v1.3-⑥ 枚举码→中文展示层换算(消息面类别 / 候选情报来源,沿
    // `nkBoardLabel` 先例,未识别值原样透传不静默瞎翻译)

    func testNewsCategoryLabelMapping() {
        XCTAssertEqual(nkNewsCategoryLabel("REDUCTION"), "减持")
        XCTAssertEqual(nkNewsCategoryLabel("INVESTIGATION"), "立案")
        XCTAssertEqual(nkNewsCategoryLabel("BLOWUP"), "暴雷")
        XCTAssertEqual(nkNewsCategoryLabel("REGULATORY"), "监管")
        XCTAssertEqual(nkNewsCategoryLabel("SOME_FUTURE_CATEGORY"), "SOME_FUTURE_CATEGORY")
    }

    func testIntelSourceLabelMapping() {
        XCTAssertEqual(nkIntelSourceLabel("quota"), "常驻保底")
        XCTAssertEqual(nkIntelSourceLabel("competition"), "情报竞争")
        XCTAssertEqual(nkIntelSourceLabel("forced"), "问询强制纳入")
        XCTAssertEqual(nkIntelSourceLabel(""), "", "旧报告空串原样透传,不冒充某个已知来源")
    }

    // MARK: - §五 v1.3-⑥-C K4 持仓牌展示层派生(`K4Advisory.isTopBillboard`)——
    // 只有「level=strong ∧ evidenceStrength=price_volume」才置顶醒目,守 §2.4 铁律
    // 「证伪只用价量结构」(题材类弱证据即便标了 strong 也只能降级展示)。

    func testK4AdvisoryTopBillboardRequiresBothStrongAndPriceVolume() {
        let strongPriceVolume = K4Advisory(code: "A3_belowyear_limitup", label: "年线下涨停,疑似派发",
                                           level: "strong", evidence: "close>=limit_price", evidenceStrength: "price_volume")
        XCTAssertTrue(strongPriceVolume.isTopBillboard)

        let strongConstituent = K4Advisory(code: "A2_theme_persist_ge_4", label: "题材持续≥4天",
                                           level: "strong", evidence: "board_age>=4", evidenceStrength: "constituent")
        XCTAssertFalse(strongConstituent.isTopBillboard, "强证据字段是 strong 但 evidenceStrength=constituent(成分类弱证据)不得置顶")

        let normalPriceVolume = K4Advisory(code: "B2_double_gold_cross", label: "双金叉",
                                           level: "normal", evidence: "macd_cross", evidenceStrength: "price_volume")
        XCTAssertFalse(normalPriceVolume.isTopBillboard, "normal 级别即便是价量证据也不置顶,只进列表")
    }

    /// 熔断触发原因展示层换算的另一分支(consecutive_stops 已在 DTODecodeTests 随
    /// 网络解码测过,这里补 daily_loss + 未识别兜底,纯模型层不必再起网络桩)。
    func testCircuitEpisodeTriggerReasonLabelMapping() {
        let daily = CircuitEpisode(triggerReason: "daily_loss", triggeredAt: "", triggerRefDate: "",
                                   basisTradesCount: 1, basisWindow: "", note: "")
        XCTAssertEqual(daily.triggerReasonLabel, "单日净亏")
        let unknown = CircuitEpisode(triggerReason: "some_future_reason", triggeredAt: "", triggerRefDate: "",
                                     basisTradesCount: 0, basisWindow: "", note: "")
        XCTAssertEqual(unknown.triggerReasonLabel, "some_future_reason")
    }

    /// 三仓 = 2 短线追击 + 1 呼吸底仓试验(§2.1 第 3 条)——`isBreathingTrial` 是
    /// 呼吸台账入口露出规则(§五 v1.2-E.4)的唯一判据,不新存第二份标记。
    func testDecisionLogIsBreathingTrialDerivation() {
        func log(playbookTag: String) -> DecisionLog {
            DecisionLog(id: 1, code: "600001.SH", name: "甲", createdAt: "", whyBuy: "", whyEntryPrice: "",
                       targetPrice: nil, exitLow: nil, exitHigh: nil, thesisTags: [], invalidation: "",
                       contingencyScenarios: [], playbookTag: playbookTag, plannedPrice: nil, plannedQty: nil,
                       status: "filled", positionId: 1, revisionOf: nil)
        }
        XCTAssertTrue(log(playbookTag: "BREATHING_TRIAL").isBreathingTrial)
        XCTAssertFalse(log(playbookTag: "SWING_CHASE").isBreathingTrial)
    }

    // MARK: - §五 v1.2-B ⑦ 情景树数组 Codable 往返(纯模型层,不必经网络桩)

    func testContingencyScenarioArrayCodableRoundTrip() throws {
        let original = [
            ContingencyScenario(scenario: "次日高开超预期", trigger: "开盘涨幅>3%", action: "HOLD", matched: false),
            ContingencyScenario(scenario: "次日低开破位", trigger: "开盘跌幅>2%", action: "ABANDON", matched: true),
        ]
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode([ContingencyScenario].self, from: data)
        XCTAssertEqual(decoded, original)
        XCTAssertEqual(decoded[1].matched, true)
        XCTAssertEqual(decoded[0].actionLabel, "持有")
        XCTAssertEqual(decoded[1].actionLabel, "放弃")
    }

    // MARK: - §五 v1.2-E.1 决策日志录入草稿(`DecisionLogForm`)

    func testDecisionLogFormValidation() {
        var form = DecisionLogForm()
        XCTAssertFalse(form.isValid)
        form.code = "600001.SH"
        form.whyBuy = "题材热"
        form.whyEntryPrice = "回调企稳"
        form.invalidation = "跌破均线"
        XCTAssertTrue(form.isValid)
        form.code = "  "
        XCTAssertFalse(form.isValid, "代码不能只是空白")
    }

    /// 情景树 UI 引导 2-3 行,服务端不强制条数——只提交「情景描述+触发条件」都非空的行,
    /// 留白的引导行不当垃圾数据提交。
    func testDecisionLogFormFilledScenariosFiltersBlankRows() {
        var form = DecisionLogForm()
        form.scenarios = [
            ContingencyScenarioDraft(scenario: "次日高开超预期", trigger: "开盘涨幅>3%", action: .hold),
            ContingencyScenarioDraft(scenario: "", trigger: "", action: .hold),
            ContingencyScenarioDraft(scenario: "只填了情景没填触发条件", trigger: "", action: .abandon),
        ]
        XCTAssertEqual(form.filledScenarios.count, 1)
        XCTAssertEqual(form.filledScenarios[0].scenario, "次日高开超预期")
    }

    /// 修订模式预填(`beginReviseDecision` 用):从已有 `DecisionLog` 构造草稿,
    /// 枚举码正确映射回对应 case,情景树数组还原。
    func testDecisionLogFormInitFromDecisionLogPrefillsAllFields() {
        let log = DecisionLog(
            id: 9, code: "600001.SH", name: "甲", createdAt: "2026-07-25T10:00:00+00:00",
            whyBuy: "题材热", whyEntryPrice: "回调企稳", targetPrice: 12.0, exitLow: 9.0, exitHigh: 9.5,
            thesisTags: ["THEME", "NEWS"], invalidation: "跌破均线",
            contingencyScenarios: [ContingencyScenario(scenario: "s1", trigger: "t1", action: "BUY", matched: false)],
            playbookTag: "BREATHING_TRIAL", plannedPrice: 10.0, plannedQty: 1000,
            status: "filled", positionId: 7, revisionOf: nil
        )
        let form = DecisionLogForm(from: log)
        XCTAssertEqual(form.code, "600001.SH")
        XCTAssertEqual(form.whyBuy, "题材热")
        XCTAssertEqual(form.targetPrice, "12.00")
        XCTAssertEqual(form.exitLow, "9.00")
        XCTAssertEqual(form.exitHigh, "9.50")
        XCTAssertEqual(form.thesisTags, [.theme, .news])
        XCTAssertEqual(form.playbookTag, .breathingTrial)
        XCTAssertEqual(form.scenarios.count, 1)
        XCTAssertEqual(form.scenarios[0].scenario, "s1")
        XCTAssertEqual(form.scenarios[0].action, .buy)
        XCTAssertEqual(form.plannedQty, "1000")
        XCTAssertTrue(form.isValid)
    }

    // MARK: - §五 v1.2-E.4 呼吸台账录入草稿 + 入口露出规则(`AppModel.linkedDecision`)

    func testBreathingTradeFormValidationRequiresFeesNonNegative() {
        var form = BreathingTradeForm()
        XCTAssertFalse(form.isValid)
        form.buyPrice = "10.0"; form.sellPrice = "10.3"; form.qty = "500"
        XCTAssertFalse(form.isValid, "费用留空不能通过校验(不代入 0,§G.2「不替用户估费率」)")
        form.fees = "20.0"
        XCTAssertTrue(form.isValid)
        form.fees = "0"
        XCTAssertTrue(form.isValid, "费用允许为 0(如实录入,不是必须 > 0)")
        form.fees = "-1"
        XCTAssertFalse(form.isValid, "费用不能为负")
    }

    /// 入口露出规则(§五 v1.2-E.4):取该 positionId 下最新一行;无关联 → nil。
    func testLinkedDecisionPicksLatestRowForPositionId() {
        let model = AppModel(clientProvider: { nil })
        func log(id: Int, positionId: Int?) -> DecisionLog {
            DecisionLog(id: id, code: "600001.SH", name: "甲", createdAt: "", whyBuy: "", whyEntryPrice: "",
                       targetPrice: nil, exitLow: nil, exitHigh: nil, thesisTags: [], invalidation: "",
                       contingencyScenarios: [], playbookTag: "BREATHING_TRIAL", plannedPrice: nil, plannedQty: nil,
                       status: positionId == nil ? "pending" : "filled", positionId: positionId, revisionOf: nil)
        }
        model.decisions = [log(id: 1, positionId: 7), log(id: 3, positionId: 7), log(id: 2, positionId: 8)]
        XCTAssertEqual(model.linkedDecision(forPositionId: 7)?.id, 3, "同一持仓多行时取 id 最大的一行")
        XCTAssertEqual(model.linkedDecision(forPositionId: 8)?.id, 2)
        XCTAssertNil(model.linkedDecision(forPositionId: 999), "无关联 → nil,不是报错")
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
    // v1.2-A2:第五类(CIRCUIT→今日计划,熔断横幅在今日计划面顶部,§五 v1.2-E.3)。
    func testCircuitCategoryRoutesToToday() {
        XCTAssertEqual(PushManager.targetTab(forCategory: NKNotificationCategory.circuit), .today)
    }
    // v1.3-②/⑥:第六类(HOLDINGALERT→今日计划,K4 持仓牌强警示在持仓卡置顶,§五 v1.3-⑥-C)。
    func testHoldingAlertCategoryRoutesToToday() {
        XCTAssertEqual(PushManager.targetTab(forCategory: NKNotificationCategory.holdingAlert), .today)
    }
    func testUnknownCategoryRoutesNowhere() {
        XCTAssertNil(PushManager.targetTab(forCategory: "SOME_OTHER_CATEGORY"))
    }
    /// category 字面必须与后端 `neckline/push/apns.py` 的 `CATEGORY_PRECALL="PRECALL"`/
    /// `CATEGORY_D5EXIT="D5EXIT"`/`CATEGORY_CIRCUIT="CIRCUIT"`/
    /// `CATEGORY_HOLDING_ALERT="HOLDINGALERT"` 完全一致(客户端/服务端各自独立声明
    /// 字符串,契约漂移只能靠这类断言在编译期之外兜底——同后端 `test_notify.py`
    /// 白名单六入口结构守护镜像)。
    func testCategoryLiteralsMatchBackend() {
        XCTAssertEqual(NKNotificationCategory.report, "REPORT")
        XCTAssertEqual(NKNotificationCategory.retreat, "RETREAT")
        XCTAssertEqual(NKNotificationCategory.precall, "PRECALL")
        XCTAssertEqual(NKNotificationCategory.d5exit, "D5EXIT")
        XCTAssertEqual(NKNotificationCategory.circuit, "CIRCUIT")
        XCTAssertEqual(NKNotificationCategory.holdingAlert, "HOLDINGALERT")
    }
}
#endif
