//
//  AppModelTests.swift
//  NecklineTests — AppModel 派生逻辑 / 问询台「永不买」不变量 / 展示层枚举换算 /
//  V2-⑮ 新增:幂等键规则、篮子成员补录预填、per-position 静音开关、推送按 kind 路由。
//

import XCTest
@testable import Neckline

@MainActor
final class AppModelTests: XCTestCase {

    // MARK: - §2.5 硬约束:问询台标注永不「买」

    /// 镜像后端 `test_verdict_is_descriptive_never_a_judgement`(tests/test_api_inquiry.py):
    /// 即便上游文本疯狂喊「现在就买」,客户端标注枚举也只可能是
    /// analyzed/analyzedWarn/unknown 三态之一,且**任何一态**都不启用买入操作。
    func testInquiryVerdictNeverEnablesBuyAction() {
        let raws = [
            InquiryVerdict.analyzedRaw, InquiryVerdict.analyzedWarnRaw,
            "现在就买!马上买入!强烈建议买买买!",   // 对抗性字符串(后端同款测试用例)
            "", "买",
        ]
        for raw in raws {
            let v = InquiryVerdict(raw)
            XCTAssertFalse(v.enablesBuyAction, "verdict for raw=\(raw) 不得启用买入操作")
        }
    }

    func testInquiryVerdictKnownCasesMapExactly() {
        XCTAssertEqual(InquiryVerdict("已分析"), .analyzed)
        XCTAssertEqual(InquiryVerdict("已分析·有风险提示"), .analyzedWarn)
        XCTAssertEqual(InquiryVerdict("已分析").label, "已分析")
        XCTAssertEqual(InquiryVerdict("已分析·有风险提示").label, "已分析·有风险提示")
    }

    func testInquiryVerdictWarnGetsWarnToneAnalyzedStaysNeutral() {
        XCTAssertEqual(InquiryVerdict("已分析·有风险提示").tone, .warn)
        XCTAssertEqual(InquiryVerdict("已分析").tone, .neutral)
    }

    func testInquiryVerdictUnrecognizedStringNeverSilentlyBecomesKnownState() {
        let v = InquiryVerdict("某种新裁决")
        guard case .unknown(let raw) = v else {
            return XCTFail("未识别字符串应归 .unknown,实际 \(v)")
        }
        XCTAssertEqual(raw, "某种新裁决")
        XCTAssertFalse(v.enablesBuyAction)
    }

    // MARK: - 问询台多轮上下文截断

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

    // MARK: - 板块码展示换算(服务端 board 是英文枚举码,中文只在客户端换算;E7)

    func testBoardLabelTranslatesKnownCodes() {
        XCTAssertEqual(nkBoardLabel("MAIN"), "主板")
        XCTAssertEqual(nkBoardLabel("GEM"), "创业板")
        XCTAssertEqual(nkBoardLabel("STAR"), "科创板")
        XCTAssertEqual(nkBoardLabel("BSE"), "北交所")
        XCTAssertEqual(nkBoardLabel("UNKNOWN_FUTURE"), "UNKNOWN_FUTURE",
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

    // MARK: - 持仓生命周期展示层派生(服务端权威 `timeExitState` 驱动,客户端不重算)

    func testPositionIsExitDayWhenDCountReachesMaxHoldDays() {
        var p = Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260710", price: 10.0, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false, dCount: 5, maxHoldDays: 5,
                         maxHoldDaysEffective: 5, timeExitState: PositionTimeExitState.timeExitNextDayRaw)
        XCTAssertTrue(p.isExitDay)
        XCTAssertEqual(p.todayActionTone, .bad, "D5 时间退出必须最高优先醒目")

        p.dCount = 6
        XCTAssertTrue(p.isExitDay)

        p.dCount = 3
        p.timeExitState = PositionTimeExitState.holdingRaw
        XCTAssertFalse(p.isExitDay)
    }

    /// `profitExempt`(浮盈豁免续持到 D15)**不是**离场提示 —— 即便 `dCount` 已经 ≥ 旧单档
    /// `maxHoldDays`(5)。这正是把 `isExitDay` 判据从 `dCount>=maxHoldDays` 迁到
    /// `timeExitState` 的根本原因:旧口径会在这里误报。
    func testPositionProfitExemptIsNotExitDayEvenPastOldSingleTierThreshold() {
        let p = Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260701", price: 12.0, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false, dCount: 8, maxHoldDays: 5,
                         maxHoldDaysEffective: 15, timeExitState: PositionTimeExitState.profitExemptRaw)
        XCTAssertFalse(p.isExitDay, "浮盈豁免续持不是离场日,即便 dCount(8) 已过旧单档阈值(5)")
        XCTAssertEqual(p.todayActionTone, .good, "浮盈豁免是持有态,不应染成警示红")
        XCTAssertEqual(p.timeExitKind, .profitExempt)
    }

    func testPositionHardCapExitIsExitDay() {
        let p = Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260601", price: 13.0, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false, dCount: 15, maxHoldDays: 5,
                         maxHoldDaysEffective: 15, timeExitState: PositionTimeExitState.hardCapExitRaw)
        XCTAssertTrue(p.isExitDay)
        XCTAssertEqual(p.todayActionTone, .bad)
        XCTAssertEqual(p.timeExitKind, .hardCapExit)
    }

    func testPositionUnknownTimeExitStateFallsBackToHoldingNotExitDay() {
        let p = Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10.0, qty: 100,
                         entryReason: "", buyDate: "20260710", price: 10.0, status: "holding",
                         stopLine: 9.5, stopOrderChecked: false, dCount: 5, maxHoldDays: 5,
                         timeExitState: "some_future_state")
        XCTAssertEqual(p.timeExitKind, .holding)
        XCTAssertFalse(p.isExitDay)
    }

    func testPositionTodayActionTonePriorityRetraceOverStopDistance() {
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

    // MARK: - V2-⑮ 参考件展示纪律:夹逼拒收 → nil,⛔ 不许显示成 0 或空白

    func testBasketPriceBandRangeTextNilWhenClamped() {
        XCTAssertEqual(BasketPriceBand(low: 10.0, high: 11.0).rangeText, "¥10.00 ~ ¥11.00")
        XCTAssertNil(BasketPriceBand(low: nil, high: nil).rangeText,
                     "夹逼拒收时必须是 nil(UI 据此显示原因),⛔ 不许兜成 0")
        XCTAssertNil(BasketPriceBand(low: 10.0, high: nil).rangeText, "半边缺失同样不算可用区间")
    }

    /// **角色两说并存**(E5):`roleConflict=true` 时两个都出现,⛔ 不挑一个当正确答案。
    func testBasketMemberRoleDisplayKeepsBothWhenConflicting() {
        let conflicting = BasketMember(tsCode: "600001.SH", name: "甲", roleLlm: "龙头",
                                       roleMech: "跟随", roleConflict: true)
        XCTAssertTrue(conflicting.roleDisplay.contains("龙头"))
        XCTAssertTrue(conflicting.roleDisplay.contains("跟随"))

        let agreed = BasketMember(tsCode: "600002.SH", name: "乙", roleLlm: "跟随",
                                  roleMech: "跟随", roleConflict: false)
        XCTAssertEqual(agreed.roleDisplay, "跟随")

        let unknown = BasketMember(tsCode: "600003.SH", name: "丙")
        XCTAssertEqual(unknown.roleDisplay, "角色未判定", "两个角色都空时如实说没判定,不留空白")
    }

    /// ③b 两个原因码**语义相反,⛔ 不许合并成「未入选」**(E2)。
    func testDroppedBasketReasonLabelsAreDistinct() {
        let overflow = DroppedBasket(name: "A", mechScore: 8.0, reason: "capacity_overflow")
        let belowLine = DroppedBasket(name: "B", mechScore: 2.0, reason: "below_quality_line")
        XCTAssertNotEqual(overflow.reasonLabel, belowLine.reasonLabel)
        XCTAssertTrue(overflow.reasonLabel.contains("装不下"))
        XCTAssertTrue(belowLine.reasonLabel.contains("没什么好货"))
        XCTAssertEqual(overflow.reasonTone, .good)
        XCTAssertEqual(belowLine.reasonTone, .warn)
        XCTAssertEqual(nkDroppedReasonLabel("some_future_reason"), "some_future_reason")
    }

    /// 验证四态角标:**「今天还没判过」与「判了是 unclear」讲不同的话**。
    func testVerificationBadgeSeparatesNotEvaluatedFromUnclear() {
        let notEvaluated = BasketVerification(basketId: 1, state: "unclear", label: "未明",
                                              notEvaluated: true)
        XCTAssertEqual(notEvaluated.badgeText, "今日尚未判定")
        XCTAssertEqual(notEvaluated.badgeTone, .neutral)

        let unclear = BasketVerification(basketId: 1, state: "unclear", label: "未明")
        XCTAssertEqual(unclear.badgeText, "未明")

        let provisional = BasketVerification(basketId: 1, state: "verified", label: "已验证",
                                             provisional: true)
        XCTAssertTrue(provisional.badgeText.contains("盘中暂态"))
        XCTAssertEqual(provisional.badgeTone, .good)

        let falsified = BasketVerification(basketId: 1, state: "falsified", label: "驱动假设已证伪")
        XCTAssertEqual(falsified.badgeTone, .bad)
    }

    /// 「卡还没生成」⛔ **不是**「篮子不存在」。
    func testBasketCardUnavailableTextIsNeverBasketNotFound() {
        let b = Basket(basketId: 7, basketKey: "k", name: "篮子", cardUnavailableReason: "card_not_ready")
        XCTAssertEqual(b.cardUnavailableText, "本篮的卡还没生成")
        XCTAssertFalse(b.cardUnavailableText!.contains("不存在"))
        let withCard = Basket(basketId: 8, card: BasketCard(basketKey: "k2"))
        XCTAssertNil(withCard.cardUnavailableText)
    }

    /// 取证不完整必须**显式标注**,⛔ 不许静默当完整证据展示。
    func testBasketCardEvidenceIncompleteNoteSpeaksUp() {
        XCTAssertNil(BasketCard(evidenceStatus: "ok").evidenceIncompleteNote)
        XCTAssertNil(BasketCard(evidenceStatus: "").evidenceIncompleteNote)
        XCTAssertNotNil(BasketCard(evidenceStatus: "search_unavailable").evidenceIncompleteNote)
        XCTAssertNotNil(BasketCard(evidenceStatus: "partial").evidenceIncompleteNote)
        XCTAssertNotNil(BasketCard(evidenceStatus: "some_future_status").evidenceIncompleteNote,
                        "未识别状态同样要说出来,⛔ 不许当 ok")
    }

    /// E1:空档位如实显示 —— `baskets(tier:)` 对没有的档返回空数组(由 UI 画「今日 T1 为空」)。
    func testBasketDailyTierFilter() {
        let daily = BasketDaily(baskets: [Basket(basketId: 1, tier: 1), Basket(basketId: 2, tier: 3)],
                                basketsAvailable: true)
        XCTAssertEqual(daily.baskets(tier: 1).map(\.basketId), [1])
        XCTAssertTrue(daily.baskets(tier: 2).isEmpty)
        XCTAssertEqual(daily.baskets(tier: 3).map(\.basketId), [2])
    }

    // MARK: - V2-⑮ 从篮子成员一键补录(预填 code/name + 区间下沿,⛔ 不虚构数字)

    func testBeginPositionEntryFlowFromMemberPrefillsAndFetchesRange() async throws {
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

        let member = BasketMember(tsCode: "600519.SH", name: "贵州茅台", roleMech: "龙头",
                                  entryZone: BasketPriceBand(low: 1217.11, high: 1258.99))
        await model.beginPositionEntryFlow(fromMember: member, basketName: "白酒复苏")

        XCTAssertEqual(model.modal, .open, "⑩-A 表单退役后直接进开仓录入,不再前置决策日志表单")
        XCTAssertEqual(model.entryForm.code, "600519.SH")
        XCTAssertEqual(model.entryForm.name, "贵州茅台")
        XCTAssertEqual(model.entryForm.price, "1217.11")
        XCTAssertTrue(model.entryForm.reason.contains("白酒复苏"))
        XCTAssertEqual(model.entrySuggestionRange?.qtyLow, 200)
        XCTAssertEqual(model.entrySuggestionRange?.qtyHigh, 400)
        XCTAssertEqual(model.entrySuggestionRange?.stopLine, 1156.25)
        XCTAssertEqual(model.entryForm.qty, "", "客户端不替用户拍单笔金额,qty 必须留空手填")
    }

    /// 建仓区间被夹逼拒收(`entryZone == nil`)→ 价格留空手填,**不虚构数字**、不崩。
    func testBeginPositionEntryFlowFromMemberWithoutEntryZoneLeavesPriceBlank() async throws {
        let model = AppModel(clientProvider: { nil })   // 无后端连接也不该崩
        let member = BasketMember(tsCode: "600001.SH", name: "甲",
                                  entryZoneClamp: "rejected_out_of_limit")
        await model.beginPositionEntryFlow(fromMember: member, basketName: "某题材")
        XCTAssertEqual(model.entryForm.code, "600001.SH")
        XCTAssertEqual(model.entryForm.price, "", "夹逼拒收时不得虚构数字")
        XCTAssertNil(model.entrySuggestionRange)
        XCTAssertEqual(model.modal, .open)
    }

    /// 进场理由预填:只陈述**来源事实**,⛔ 不得出现「推荐 / 建议买入 / 看好」类表述。
    func testEntryReasonTextStatesSourceOnlyNeverRecommends() {
        let member = BasketMember(tsCode: "600001.SH", name: "甲", roleMech: "跟随")
        let text = AppModel.entryReasonText(basketName: "某题材", member: member)
        XCTAssertTrue(text.contains("某题材"))
        XCTAssertFalse(text.isEmpty)
        for banned in ["推荐", "建议买入", "看好", "值得买"] {
            XCTAssertFalse(text.contains(banned), "语义红线:进场理由不得出现「\(banned)」")
        }
        // 篮子名缺失 → 退回中性文案,不显示空串。
        XCTAssertEqual(AppModel.entryReasonText(basketName: "  ", member: member), "已按计划买入")
    }

    /// **角色对拍分歧时预填文案不挑边**(E5 的延伸:连预填的一句话也不能替用户选一说)。
    func testEntryReasonTextDoesNotPickASideWhenRolesConflict() {
        let member = BasketMember(tsCode: "600001.SH", name: "甲", roleLlm: "龙头",
                                  roleMech: "跟随", roleConflict: true)
        let text = AppModel.entryReasonText(basketName: "某题材", member: member)
        XCTAssertTrue(text.contains("两说并存"))
        XCTAssertFalse(text.contains("龙头"))
        XCTAssertFalse(text.contains("跟随"))
    }

    // MARK: - V2-⑮ 幂等键规则(契约线 🟡 Y7;**每笔新提交动作一个新键,⛔ 严禁复用**)

    /// **两次独立提交动作的键必须不同**(⛔ 别绑「票 + 日期」之类业务量 —— 那必然复用;
    /// 服务端是标准幂等语义,同键不同 payload 会**静默重放原仓**、把用户改过的价格数量吃掉)。
    func testIdempotencyKeyIsFreshPerEntryFlow() {
        let model = AppModel(clientProvider: { nil })
        model.beginPositionEntryFlow()
        let first = model.entryIdempotencyKey
        model.beginPositionEntryFlow()
        let second = model.entryIdempotencyKey
        XCTAssertFalse(first.isEmpty)
        XCTAssertNotEqual(first, second, "每次进入录入流程必须铸一枚新键")
        // 同一次流程内(含从篮子成员进入)不改键 —— 那是"重试同一意图"。
        XCTAssertEqual(model.entryIdempotencyKey, second)
    }

    /// **同一次提交动作的重试复用同一枚键**:提交失败后再提交,请求体里的 key 必须一致。
    func testIdempotencyKeyReusedAcrossRetriesOfSameSubmission() async throws {
        var keys: [String] = []
        MockURLProtocol.handler = { req in
            if let body = req.httpBodyOrStream(),
               let obj = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
               let k = obj["idempotencyKey"] as? String {
                keys.append(k)
            }
            // 第一次故意 500,逼出一次重试。
            if keys.count == 1 { return (500, #"{"detail": "boom"}"#.data(using: .utf8)!) }
            return (200, #"{"ok": true, "position_id": 1, "stop_line": 9.5}"#.data(using: .utf8)!)
        }
        defer { MockURLProtocol.handler = nil }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t",
                               session: URLSession(configuration: config))
        let model = AppModel(clientProvider: { client })
        model.beginPositionEntryFlow()
        model.entryForm.code = "600519.SH"
        model.entryForm.price = "10.0"
        model.entryForm.qty = "100"

        await model.submitOpenPosition()   // 失败
        await model.submitOpenPosition()   // 重试

        XCTAssertEqual(keys.count, 2)
        XCTAssertEqual(keys[0], keys[1], "同一次提交动作的重试必须复用同一枚幂等键")
    }

    /// 服务端 `replayed=true` 时,如实说「什么都没发生」—— ⛔ 别让"看起来成功了"掩盖它。
    func testReplayedResponseIsSurfacedHonestly() async throws {
        MockURLProtocol.handler = { req in
            if req.url?.absoluteString.contains("/positions") == true, req.httpMethod == "POST" {
                return (200, """
                {"ok": true, "position_id": 7, "stop_line": 9.5, "replayed": true}
                """.data(using: .utf8)!)
            }
            return (200, #"{"holdings": []}"#.data(using: .utf8)!)
        }
        defer { MockURLProtocol.handler = nil }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t",
                               session: URLSession(configuration: config))
        let model = AppModel(clientProvider: { client })
        model.beginPositionEntryFlow()
        model.entryForm.code = "600519.SH"
        model.entryForm.price = "10.0"
        model.entryForm.qty = "100"

        await model.submitOpenPosition()

        XCTAssertEqual(model.toast?.message.contains("未重复开仓"), true)
    }

    // MARK: - 开仓表单校验(⑩-A **三字段即可提交**;⛔ 别把理由 / 费用加回必填)

    func testPositionEntryFormValidationIsThreeFieldsOnly() {
        var form = PositionEntryForm()
        XCTAssertFalse(form.isValid)
        form.code = "600519.SH"
        form.price = "1500.0"
        form.qty = "100"
        XCTAssertTrue(form.isValid, "⑩-A:票 + 价 + 量三字段即可提交(表单退役是本版立项主题)")
        form.price = "0"
        XCTAssertFalse(form.isValid, "买入价必须 > 0")
        form.price = "1500.0"
        form.qty = "0"
        XCTAssertFalse(form.isValid, "数量必须 > 0")
        form.qty = "100"
        form.reason = ""
        form.buyFees = ""
        XCTAssertTrue(form.isValid, "理由 / 费用留空照样提交(⛔ 不做硬阻断)")
    }

    /// 补录持仓日期选择器:`submitOpenPosition()` 正确格式化成 'YYYYMMDD' 并透传。
    func testSubmitOpenPositionEncodesBuyDateFromForm() async throws {
        var capturedBuyDate: String?
        MockURLProtocol.handler = { req in
            if let body = req.httpBodyOrStream(),
               let obj = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
                capturedBuyDate = obj["buyDate"] as? String
            }
            return (200, """
            {"ok": true, "position_id": 1, "stop_line": 9.5}
            """.data(using: .utf8)!)
        }
        defer { MockURLProtocol.handler = nil }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t",
                               session: URLSession(configuration: config))
        let model = AppModel(clientProvider: { client })
        model.entryForm.code = "600519.SH"
        model.entryForm.price = "10.0"
        model.entryForm.qty = "100"
        let fixedDate = StaticTradingCalendar.shared.parseDate("20260722")!
        model.entryForm.buyDate = fixedDate

        await model.submitOpenPosition()

        XCTAssertEqual(capturedBuyDate, "20260722")
    }

    // MARK: - ⑩-C 用户可选补充(**空提交合法**,⛔ 不做硬阻断)

    func testNoteFormAllowsEmptySubmission() async throws {
        var requestFired = false
        MockURLProtocol.handler = { _ in
            requestFired = true
            return (200, #"{"ok": true, "recorded": []}"#.data(using: .utf8)!)
        }
        defer { MockURLProtocol.handler = nil }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t",
                               session: URLSession(configuration: config))
        let model = AppModel(clientProvider: { client })
        model.beginNote(code: "600001.SH")
        // 一个标签都不选、说明也不写 —— ⑩-C 起这是合法的空提交。
        await model.submitNote()
        XCTAssertTrue(requestFired, "空提交必须照发请求(服务端 200,不是 400)")
        XCTAssertNil(model.modal)
    }

    func testNoteFormSendsSelectedLabelCodes() async throws {
        var captured: [String] = []
        MockURLProtocol.handler = { req in
            if let body = req.httpBodyOrStream(),
               let obj = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
                captured = (obj["labels"] as? [String]) ?? []
                XCTAssertEqual(obj["voiceNote"] as? String, "跟着龙头进的")
            }
            return (200, #"{"ok": true, "recorded": ["label", "voice_note"]}"#.data(using: .utf8)!)
        }
        defer { MockURLProtocol.handler = nil }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t",
                               session: URLSession(configuration: config))
        let model = AppModel(clientProvider: { client })
        model.beginNote(code: "600001.SH", positionId: 3)
        model.noteForm.labels = [.themeShift, .leaderReactivate]
        model.noteForm.voiceNote = "跟着龙头进的"

        await model.submitNote()

        // **服务端只认英文码**(唯一源 `NoteLabelLiteral`),⛔ 不许发中文键。
        XCTAssertEqual(captured.sorted(), ["LEADER_REACTIVATE", "THEME_SHIFT"])
    }

    // MARK: - ⑪-D-D per-position 触达提醒开关(**只翻静音位,计划正文一项不动**)

    func testSetExitReferenceMutedOnlyFlipsMuteKeyAndKeepsPlanBodyIntact() async throws {
        var capturedPlan: [String: Any] = [:]
        MockURLProtocol.handler = { req in
            let url = req.url?.absoluteString ?? ""
            if url.contains("/positions/3/plans"), req.httpMethod == "POST" {
                if let body = req.httpBodyOrStream(),
                   let obj = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
                    capturedPlan = (obj["plan"] as? [String: Any]) ?? [:]
                }
                return (201, #"{"id": 2, "positionId": 3, "version": 2, "plan": {}}"#.data(using: .utf8)!)
            }
            return (200, #"{"items": []}"#.data(using: .utf8)!)
        }
        defer { MockURLProtocol.handler = nil }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8002")!, token: "t",
                               session: URLSession(configuration: config))
        let model = AppModel(clientProvider: { client })
        model.positionPlans[3] = [PositionPlan(
            id: 1, positionId: 3, version: 1,
            plan: .object([
                "available": .bool(true),
                "driver": .string("题材 A"),
                "exit_reference": .object(["low": .number(12.0), "high": .number(13.0)]),
                "exit_reference_armed": .bool(true),
                "exit_reference_muted": .bool(false),
                "risks": .array([.string("风险一")]),
            ])
        )]

        await model.setExitReferenceMuted(positionId: 3, muted: true)

        XCTAssertEqual(capturedPlan["exit_reference_muted"] as? Bool, true)
        // ⛔ 计划正文一项不动。
        XCTAssertEqual(capturedPlan["driver"] as? String, "题材 A")
        XCTAssertEqual((capturedPlan["exit_reference"] as? [String: Any])?["low"] as? Double, 12.0)
        XCTAssertEqual((capturedPlan["risks"] as? [String])?.count, 1)
    }

    /// `PositionPlan` 的武装态便利读取:**缺键 = 不武装**(fail-closed,与服务端读侧同口径)。
    func testPositionPlanArmingAccessorsAreFailClosed() {
        let noKeys = PositionPlan(plan: .object([:]))
        XCTAssertFalse(noKeys.exitReferenceArmed, "缺键即不武装(fail-closed)")
        XCTAssertFalse(noKeys.exitReferenceMuted)
        XCTAssertFalse(noKeys.available)

        let armed = PositionPlan(plan: .object([
            "available": .bool(true),
            "exit_reference_armed": .bool(true),
            "exit_reference_muted": .bool(false),
            "exit_reference_armed_note": .string("已武装"),
        ]))
        XCTAssertTrue(armed.exitReferenceArmed)
        XCTAssertTrue(armed.available)
        XCTAssertEqual(armed.exitReferenceArmedNote, "已武装")

        // 「无来源篮子」与「卡未就绪」两态**分得开**(合法结果,不是错误)。
        let noSource = PositionPlan(plan: .object([
            "available": .bool(false), "reason": .string("no_source_basket"),
        ]))
        XCTAssertEqual(noSource.unavailableText, "独立买入 · 没有来源篮子可继承")
        let cardNotReady = PositionPlan(plan: .object([
            "available": .bool(false), "reason": .string("card_not_ready"),
        ]))
        XCTAssertNotEqual(noSource.unavailableText, cardNotReady.unavailableText)
    }

    // MARK: - 蓝图 6.2 同题材合并敞口(同一来源篮子的多笔仓不是完全分散的两笔)

    func testMergedExposureGroupsByBasketAndNeedsTwoDistinctCodes() {
        let model = AppModel(clientProvider: { nil })
        model.positions = [
            Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10, qty: 100, entryReason: "",
                     buyDate: "20260716", price: 11, status: "holding", stopLine: 9.5, stopOrderChecked: false),
            Position(id: 2, code: "600002.SH", name: "乙", buyPrice: 20, qty: 50, entryReason: "",
                     buyDate: "20260716", price: 21, status: "holding", stopLine: 19, stopOrderChecked: false),
            Position(id: 3, code: "600003.SH", name: "丙", buyPrice: 5, qty: 100, entryReason: "",
                     buyDate: "20260716", price: 5, status: "holding", stopLine: 4.75, stopOrderChecked: false),
        ]
        func plan(_ pid: Int, basketId: Int?) -> PositionPlan {
            PositionPlan(id: pid, positionId: pid, version: 1, sourceBasketId: basketId,
                         plan: .object(["source_basket_name": .string("题材 A")]))
        }
        model.positionPlans = [1: [plan(1, basketId: 9)], 2: [plan(2, basketId: 9)],
                               3: [plan(3, basketId: nil)]]

        let merged = model.mergedExposures
        XCTAssertEqual(merged.count, 1, "只有同一篮 ≥2 个不同标的才算合并敞口")
        XCTAssertEqual(merged[0].basketId, 9)
        XCTAssertEqual(merged[0].codes, ["600001.SH", "600002.SH"])
        XCTAssertEqual(merged[0].costAmount, 10 * 100 + 20 * 50, accuracy: 0.001)
        XCTAssertEqual(merged[0].marketAmount, 11 * 100 + 21 * 50, accuracy: 0.001)
    }

    /// 同一篮里**同一只票**加了两笔仓 → **不算**"看起来分散实则集中",不出提示。
    func testMergedExposureIgnoresSameCodeAddedTwice() {
        let model = AppModel(clientProvider: { nil })
        model.positions = [
            Position(id: 1, code: "600001.SH", name: "甲", buyPrice: 10, qty: 100, entryReason: "",
                     buyDate: "20260716", price: 11, status: "holding", stopLine: 9.5, stopOrderChecked: false),
            Position(id: 2, code: "600001.SH", name: "甲", buyPrice: 10.5, qty: 100, entryReason: "",
                     buyDate: "20260717", price: 11, status: "holding", stopLine: 9.9, stopOrderChecked: false),
        ]
        let p = PositionPlan(version: 1, sourceBasketId: 9)
        model.positionPlans = [1: [p], 2: [p]]
        XCTAssertTrue(model.mergedExposures.isEmpty)
    }

    // MARK: - 枚举码→中文展示层换算(沿 `nkBoardLabel` 先例,未识别透传)

    func testThesisTagLabelMapping() {
        XCTAssertEqual(nkThesisTagLabel("THEME"), "题材主线")
        XCTAssertEqual(nkThesisTagLabel("SENTIMENT_CYCLE"), "情绪周期位")
        XCTAssertEqual(nkThesisTagLabel("CAPITAL_FLOW"), "资金流向")
        XCTAssertEqual(nkThesisTagLabel("TECH_PATTERN"), "技术形态")
        XCTAssertEqual(nkThesisTagLabel("NEWS"), "消息")
        XCTAssertEqual(nkThesisTagLabel("SOME_FUTURE_CODE"), "SOME_FUTURE_CODE", "未识别值原样透传")
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

    /// ⑩-A:既有五码**原样不动、只加不改**,新增四码。
    /// ⚠ `TARGET_ZONE_REACHED` 的文案是「达到参考区间」,⛔ **不许写成「止盈」**
    /// —— 离场参考区间不是止盈线(§2.8-C 语义红线),码名与文案都要守住这条。
    func testCloseReasonLabelMappingNineCodes() {
        XCTAssertEqual(nkCloseReasonLabel("STOP_LOSS"), "止损")
        XCTAssertEqual(nkCloseReasonLabel("TAKE_PROFIT"), "回落止盈")
        XCTAssertEqual(nkCloseReasonLabel("TIME_EXIT"), "时间退出")
        XCTAssertEqual(nkCloseReasonLabel("INVALIDATION"), "证伪离场")
        XCTAssertEqual(nkCloseReasonLabel("MANUAL"), "主动离场")
        XCTAssertEqual(nkCloseReasonLabel("SECTOR_WEAKENING"), "板块转弱")
        XCTAssertEqual(nkCloseReasonLabel("TARGET_ZONE_REACHED"), "达到参考区间")
        XCTAssertFalse(nkCloseReasonLabel("TARGET_ZONE_REACHED").contains("止盈"),
                       "⛔ 离场参考区间不是止盈线")
        XCTAssertEqual(nkCloseReasonLabel("ACTIVE_SWITCH"), "主动切换")
        XCTAssertEqual(nkCloseReasonLabel("AD_HOC"), "临时决定")
        XCTAssertEqual(nkCloseReasonLabel("???"), "???")
        XCTAssertEqual(CloseReasonCode.allCases.count, 9)
    }

    /// ⑩-C 七枚标签码(服务端 `NoteLabelLiteral` 唯一源;客户端只做展示层换算)。
    func testNoteLabelMapping() {
        XCTAssertEqual(nkNoteLabelText("THEME_SHIFT"), "题材切换")
        XCTAssertEqual(nkNoteLabelText("LEADER_REACTIVATE"), "龙头重新激活")
        XCTAssertEqual(nkNoteLabelText("VOLUME_BREAKOUT"), "放量突破")
        XCTAssertEqual(nkNoteLabelText("WEAK_TO_STRONG"), "弱转强")
        XCTAssertEqual(nkNoteLabelText("CORE_POSITION"), "容量中军")
        XCTAssertEqual(nkNoteLabelText("NEWS_CATALYST"), "消息催化")
        XCTAssertEqual(nkNoteLabelText("PURE_TAPE_READING"), "纯盘口判断")
        XCTAssertEqual(nkNoteLabelText("SOME_FUTURE_LABEL"), "SOME_FUTURE_LABEL")
        XCTAssertEqual(NoteLabel.allCases.count, 7)
    }

    func testNewsCategoryLabelMapping() {
        XCTAssertEqual(nkNewsCategoryLabel("REDUCTION"), "减持")
        XCTAssertEqual(nkNewsCategoryLabel("INVESTIGATION"), "立案")
        XCTAssertEqual(nkNewsCategoryLabel("BLOWUP"), "暴雷")
        XCTAssertEqual(nkNewsCategoryLabel("REGULATORY"), "监管")
        XCTAssertEqual(nkNewsCategoryLabel("SOME_FUTURE_CATEGORY"), "SOME_FUTURE_CATEGORY")
    }

    /// 三级展示层换算:**未识别 level 原样透传**(服务端加第四级时,设置屏照样把它
    /// 分成独立一组显示,⛔ 不静默丢弃)。
    func testPushLevelLabelMapping() {
        XCTAssertEqual(nkPushLevelLabel("immediate"), "立即")
        XCTAssertEqual(nkPushLevelLabel("important"), "重要不紧急")
        XCTAssertEqual(nkPushLevelLabel("digest"), "盘后汇总")
        XCTAssertEqual(nkPushLevelLabel("some_future_level"), "some_future_level")
        XCTAssertEqual(nkPushLevelLabel(""), "未分级")
    }

    /// 验证状态四态兜底换算。
    func testVerificationStateLabelMapping() {
        XCTAssertEqual(nkVerificationStateLabel("verified"), "已验证")
        XCTAssertEqual(nkVerificationStateLabel("partial"), "部分验证")
        XCTAssertEqual(nkVerificationStateLabel("unclear"), "未明")
        XCTAssertEqual(nkVerificationStateLabel("falsified"), "驱动假设已证伪")
        XCTAssertEqual(nkVerificationStateLabel("some_future_state"), "some_future_state")
    }

    // MARK: - K4 持仓牌展示层派生(只有 strong ∧ price_volume 才置顶醒目)

    func testK4AdvisoryTopBillboardRequiresBothStrongAndPriceVolume() {
        let strongPriceVolume = K4Advisory(code: "A3_belowyear_limitup", label: "年线下涨停,疑似派发",
                                           level: "strong", evidence: "close>=limit_price", evidenceStrength: "price_volume")
        XCTAssertTrue(strongPriceVolume.isTopBillboard)

        let strongConstituent = K4Advisory(code: "A2_theme_persist_ge_4", label: "题材持续≥4天",
                                           level: "strong", evidence: "board_age>=4", evidenceStrength: "constituent")
        XCTAssertFalse(strongConstituent.isTopBillboard, "成分类弱证据即便标 strong 也不得置顶")

        let normalPriceVolume = K4Advisory(code: "B2_double_gold_cross", label: "双金叉",
                                           level: "normal", evidence: "macd_cross", evidenceStrength: "price_volume")
        XCTAssertFalse(normalPriceVolume.isTopBillboard, "normal 级别即便是价量证据也不置顶")
    }

    func testCircuitEpisodeTriggerReasonLabelMapping() {
        let daily = CircuitEpisode(triggerReason: "daily_loss", triggeredAt: "", triggerRefDate: "",
                                   basisTradesCount: 1, basisWindow: "", note: "")
        XCTAssertEqual(daily.triggerReasonLabel, "单日净亏")
        let unknown = CircuitEpisode(triggerReason: "some_future_reason", triggeredAt: "", triggerRefDate: "",
                                     basisTradesCount: 0, basisWindow: "", note: "")
        XCTAssertEqual(unknown.triggerReasonLabel, "some_future_reason")
    }

    /// `DecisionLog` 保留为**只读归因**类型(v2.0.0 起零新增行);展示层派生仍需正确。
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

    // MARK: - 通用 JSON 值(自由结构透传字段的载体)

    func testNKJSONDecodesScalarsAndKeepsBoolDistinctFromNumber() throws {
        let raw = """
        {"b": true, "n": 3, "f": 1.5, "s": "x", "nul": null,
         "arr": [1, "a"], "obj": {"k": "v"}}
        """.data(using: .utf8)!
        let v = try JSONDecoder().decode(NKJSON.self, from: raw)
        XCTAssertEqual(v["b"]?.boolValue, true)
        XCTAssertNil(v["b"]?.doubleValue, "布尔不得被解成数字(顺序反了会显示成 1)")
        XCTAssertEqual(v["n"]?.intValue, 3)
        XCTAssertEqual(v["f"]?.doubleValue, 1.5)
        XCTAssertEqual(v["s"]?.stringValue, "x")
        XCTAssertEqual(v["nul"]?.isNull, true)
        XCTAssertEqual(v["arr"]?.arrayValue?.count, 2)
        XCTAssertEqual(v["obj"]?["k"]?.stringValue, "v")
        XCTAssertEqual(v.sortedKeys, ["arr", "b", "f", "n", "nul", "obj", "s"], "键序必须确定")
        XCTAssertEqual(v["b"]?.displayText, "是")
        XCTAssertEqual(v["n"]?.displayText, "3")
        XCTAssertEqual(v["nul"]?.displayText, "—")
    }

    // MARK: - 交易日历(日期解析)

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
        let cal = StaticTradingCalendar.shared
        let d = cal.parseDate("20260717")!
        XCTAssertTrue(cal.isTradingDay(d))
    }
}

// MARK: - PushManager 推送路由(纯函数,iOS 专属——PushManager 整文件 #if os(iOS))

#if os(iOS)
@MainActor
final class PushRoutingTests: XCTestCase {

    /// **按 `kind` 分发,⛔ 不按 category** —— category 只决定「怎么响」。
    func testReportReadyKindRoutesToBaskets() {
        XCTAssertEqual(PushManager.targetTab(forKind: "report_ready"), .baskets)
    }

    func testRetreatKindRoutesToBaskets() {
        XCTAssertEqual(PushManager.targetTab(forKind: "retreat"), .baskets,
                       "退潮 = 今日计划整体作废,指向选股面")
    }

    /// 持仓线各 kind **各自独立**指向持仓板块 —— ⛔ 不因为同属一个 category 就连坐。
    func testHoldingKindsRouteToPositionsIndividually() {
        for kind in ["circuit", "d5exit", "holding_alert", "precall",
                     "stop_approach", "take_profit", "sector_dive",
                     "basket_peers_weak", "sector_bid_fade", "holding_decoupled",
                     "market_shock", "custom_alert"] {
            XCTAssertEqual(PushManager.targetTab(forKind: kind), .positions, "kind=\(kind)")
        }
    }

    /// **未知 kind 优雅降级**:不路由(停在当前页),但通知本身照常展示 ——
    /// ⛔ 不静默丢弃(v1.5「Swift 未知 status 静默消失」的同类坑)。
    func testUnknownKindRoutesNowhereButIsNotAnError() {
        XCTAssertNil(PushManager.targetTab(forKind: "some_future_kind"))
        XCTAssertNil(PushManager.targetTab(forKind: ""))
    }

    /// ⛔ **篮子 `falsified` 不是也不会是一个 kind**(⑪-B planner 裁决 + CLAUDE.md 坑条 +
    /// ⑦-b/⑧-C2 红线三处锁死)。这里锁死客户端**不给它任何路由**,防止日后有人
    /// 「照例举接回去」。
    func testBasketFalsifiedIsNotARoutableKind() {
        XCTAssertNil(PushManager.targetTab(forKind: "basket_falsified"))
    }

    /// category 字面必须与服务端 `neckline/notify_kinds.py` 的三个 `CATEGORY_*` 完全一致
    /// (双端各自独立声明字符串,契约漂移只能靠这类断言兜底)。
    func testCategoryLiteralsMatchBackend() {
        XCTAssertEqual(NKNotificationCategory.immediate, "NKIMMEDIATE")
        XCTAssertEqual(NKNotificationCategory.important, "NKIMPORTANT")
        XCTAssertEqual(NKNotificationCategory.digest, "NKDIGEST")
        XCTAssertEqual(NKNotificationCategory.all.count, 3, "V2 起是三级,不再是 V1 的六个具名 category")
    }
}
#endif
