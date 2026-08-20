//
//  AppModelTests.swift
//  NecklineTests — AppModel 派生逻辑 / 展示层枚举换算 /
//  V2-⑮ 新增:幂等键规则、篮子成员补录预填、per-position 静音开关、推送按 kind 路由。
//  ⚠ V2.1-① 起「问询台『永不买』不变量」+「问询台多轮上下文截断」两节测试已随
//  问询台整链退役删除(`InquiryVerdict`/`ChatMessage`/`AppModel.inquiryContext`
//  均已物理删除)。
//

import XCTest
@testable import Neckline

@MainActor
final class AppModelTests: XCTestCase {

    func testSelectionDestinationSynchronizesBasketCompatibilityState() {
        let model = AppModel()
        XCTAssertEqual(model.selectionDestination, .market)
        model.selectSelectionDestination(.basket(42))
        XCTAssertEqual(model.selectionDestination, .basket(42))
        XCTAssertEqual(model.openedBasketId, 42)
        model.selectSelectionDestination(.intel)
        XCTAssertEqual(model.selectionDestination, .intel)
        XCTAssertNil(model.openedBasketId)
    }

    func testInfoCardSelectionTargetsContainingBasketAndMember() {
        let model = AppModel()
        let member = BasketMember(tsCode: "600000.SH", name: "测试成员")
        model.report.tradeDate = "20260813"

        model.openInfoCardForSelection(basketID: 7, member: member)

        XCTAssertEqual(model.selectionDestination, .basket(7))
        XCTAssertEqual(model.openedBasketId, 7)
        XCTAssertEqual(model.selectionMemberCode, "600000.SH")
        XCTAssertEqual(model.infoCardRequest?.code, "600000.SH")
    }

    // MARK: - ⛔ V2.4.0 P0:退潮警示两条用例已退役(施工纪律 4:写明被谁取代)
    //
    // 原 `testRetreatWarningPresentWhenBrakeActive` / `testRetreatWarningNilWhenBrakeInactive`
    // 断言的是 `AppModel.board` + `AppModel.retreatWarning` 这条派生链(刹车激活 →
    // 壳层红条拿到依据一句)。**被 P0.1 表那两行取代**:「代理关注池 →『大盘退潮』= 删」
    // +「全 App 顶部退潮红条 = 删」—— 那两个属性已从 `AppModel` 上物理删除,断言对象不存在。
    //
    // 取而代之的机器判据在服务端守门里(`tests/test_v240_p0_retirement_guard.py`):
    // `TestClientBoardSurfaceGone`(剥注释后扫符号零引用)+ `TestNoDedicatedBoardPolling`。
    // ⚠ **DTO 解码用例照旧全留**(`DTODecodeTests` 的 `testDecodeBoard*`):P0.6-8 要求
    // 旧响应仍能宽松解码,那是**兼容能力**、不是"还在用"。

    // MARK: - V2.4.0 P0.5+:持仓提醒新通道(原「盘中动态」页上属于本持仓的那部分)


    // MARK: - 仓位额度三态映射(唯一事实源 = 后端字面量,客户端只穷举匹配)


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


    // MARK: - 持仓生命周期展示层派生(服务端权威 `timeExitState` 驱动,客户端不重算)


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

    // MARK: - V2.3.1 §〇c 硬伤 2:角色码中文换算(⛔ 界面不许印 leader / core / elastic)

    /// 🔴 **本用例存在的理由 = 上面那条用例是绿的、却没拦住线上印英文**。
    /// 它的 fixture 直接喂了中文「龙头」/「跟随」,而生产实际发的是
    /// `leader` / `core` / `elastic`(源 `neckline/selection/aggregate.py`)——
    /// **喂什么就测出什么**,喂中文等于把被测的那一步换算整个绕过去了。
    /// ⛔ 以后凡是"服务端发码、客户端换中文"的东西,fixture 一律喂**码**。
    func testRoleLabelTranslatesServerCodesToChinese() {
        // §⑪ 换算表(用户 2026-08-10 拍板,⛔ 不得重开)
        XCTAssertEqual(nkRoleLabel("leader"), "龙头")
        XCTAssertEqual(nkRoleLabel("core"), "跟随")
        XCTAssertEqual(nkRoleLabel("elastic"), "弹性", "用户原话「elastic 就叫弹性」")
        XCTAssertEqual(nkRoleLabel("unknown"), "",
                       "`unknown` = 算不出、不是一种角色 → 空串 → 由 NKChip 整枚不画,⛔ 不许画「未知」")
        XCTAssertEqual(nkRoleLabel("brand_new_code"), "brand_new_code",
                       "未识别值原样透传(沿 nkBoardLabel 先例),⛔ 不瞎翻译")
    }

    /// 三处 `roleDisplay` 共用同一份换算 —— 喂**英文码**验收(硬伤 2 的真实输入形状)。
    func testRoleDisplayOnRealServerCodes() {
        let leader = BasketMember(tsCode: "002812.SZ", name: "恩捷股份",
                                  roleLlm: "leader", roleMech: "leader", roleConflict: false)
        XCTAssertEqual(leader.roleDisplay, "龙头")

        let elastic = BasketMember(tsCode: "300207.SZ", name: "欣旺达",
                                   roleLlm: "elastic", roleMech: "elastic", roleConflict: false)
        XCTAssertEqual(elastic.roleDisplay, "弹性")

        // `unknown` → 空串 → 收起行整枚徽标不画(⛔ 不是「未知」也不是「角色未判定」:
        // 后者留给"服务端根本没发这两个键"的老卡)。
        let unknownRole = BasketMember(tsCode: "002074.SZ", name: "国轩高科",
                                       roleLlm: nil, roleMech: "unknown", roleConflict: false)
        XCTAssertEqual(unknownRole.roleDisplay, "")

        // 冲突:两说并存,两边都换算成中文,⛔ 不挑一个当正确答案。
        let conflict = BasketMember(tsCode: "300450.SZ", name: "先导智能",
                                    roleLlm: "leader", roleMech: "core", roleConflict: true)
        XCTAssertTrue(conflict.roleDisplay.contains("龙头"))
        XCTAssertTrue(conflict.roleDisplay.contains("跟随"))
        XCTAssertFalse(conflict.roleDisplay.contains("leader"), "⛔ 界面上不许出现英文码")
        XCTAssertFalse(conflict.roleDisplay.contains("core"), "⛔ 界面上不许出现英文码")

        // 「两说并存必须并排摆出两个值」的场合:换算不出时补 `—`,而不是整枚吞掉。
        XCTAssertEqual(nkRoleLabelOrDash("unknown"), "—")
        XCTAssertEqual(nkRoleLabelOrDash(nil), "—")
        XCTAssertEqual(nkRoleLabelOrDash("core"), "跟随")
    }

    /// 进场理由预填:`unknown` 时**不留孤零零的「· 」尾巴**,也⛔ 不补「未知角色」。
    func testEntryReasonTextOmitsRoleWhenUnknown() {
        let unknownRole = BasketMember(tsCode: "002074.SZ", name: "国轩高科", roleMech: "unknown")
        XCTAssertEqual(AppModel.entryReasonText(basketName: "固态电池", member: unknownRole),
                       "来自篮子「固态电池」")

        let leader = BasketMember(tsCode: "002812.SZ", name: "恩捷股份", roleMech: "leader")
        XCTAssertEqual(AppModel.entryReasonText(basketName: "固态电池", member: leader),
                       "来自篮子「固态电池」· 龙头")
    }

    // MARK: - V2.2-③-C/③-C2 位置关 / 核心关三态展示层换算(裁定 #11/#12)


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

    /// V2.4.2：仅由服务端明确下发的运行态产生提示；老载荷缺键与 complete 都不占界面。
    func testBasketDailySelectionStatusNoticeIsExplicitAndUserFacing() {
        XCTAssertNil(BasketDaily().selectionStatusNotice)
        XCTAssertNil(BasketDaily(selectionState: "complete").selectionStatusNotice)
        XCTAssertNil(BasketDaily(selectionState: "future_state").selectionStatusNotice)

        let processing = BasketDaily(selectionState: "processing")
        XCTAssertEqual(processing.selectionStatusNotice?.title, "今日选股正在整理")
        XCTAssertTrue(processing.selectionStatusNotice?.detail.contains("最近一次已完成") == true)

        let partial = BasketDaily(selectionState: "partial", selectionStateText: "已完成部分方向")
        XCTAssertEqual(partial.selectionStatusNotice?.title, "今日选股部分完成")
        XCTAssertEqual(partial.selectionStatusNotice?.detail, "已完成部分方向")
        XCTAssertTrue(partial.emptyTierDetail.contains("仍有方向未完成"))

        let unavailable = BasketDaily(selectionState: "unavailable")
        XCTAssertEqual(unavailable.selectionStatusNotice?.title, "今日选股暂未完成")
        XCTAssertTrue(unavailable.selectionStatusNotice?.detail.contains("没有机会") == true)
        XCTAssertTrue(unavailable.emptyTierDetail.contains("不是市场结论"))
        XCTAssertTrue(BasketDaily(selectionState: "complete").emptyTierDetail.contains("算过了"))
    }

    // MARK: - V2-⑮ 从篮子成员一键补录(预填 code/name + 区间下沿,⛔ 不虚构数字)


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


    // MARK: - 开仓表单校验(⑩-A **三字段即可提交**;⛔ 别把理由 / 费用加回必填)


    // MARK: - ⑩-C 用户可选补充(**空提交合法**,⛔ 不做硬阻断)


    // MARK: - ⑪-D-D per-position 触达提醒开关(**只翻静音位,计划正文一项不动**)


    // MARK: - 蓝图 6.2 同题材合并敞口(同一来源篮子的多笔仓不是完全分散的两笔)


    // MARK: - 枚举码→中文展示层换算(沿 `nkBoardLabel` 先例,未识别透传)


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


    // ⚠ **`testCircuitEpisodeTriggerReasonLabelMapping` 已随两个 DTO 于 v2.3.0 删除**
    //（两步淘汰第二步：服务端 `PositionsOut.circuit` 删键 + 客户端删 `CircuitState`/
    // `CircuitEpisode`，同一版落地）。⛔ 不是漏删测试。


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

    // MARK: - V2.1-⑦ 信息架构:三板块 + 设置沉底


    /// 🔴 **`rawValue` 是 `NECKLINE_INITIAL_TAB` QA 钩子与截图脚本的契约**:
    /// 改 case 名会把那些脚本静默变成"落到默认 tab",而**截图看起来还挺正常**。
    func testAppTabRawValuesAreTheQAHookContract() {
        XCTAssertEqual(AppTab.allCases.map(\.rawValue),
                       ["baskets", "positions", "review", "settings"])
        XCTAssertEqual(AppTab(rawValue: "review"), .review)
        XCTAssertNil(AppTab(rawValue: "inquiry"), "问询台已整链退役(V2.1-①)")
    }

    // MARK: - V2.2-③ ③b 原因码 + 六关灯条(展示层换算,纯逻辑)

    /// 🔴 **V2.2 新增七码一个都不许落回原样透传**(那等于界面上直接印英文码),
    /// 且 `no_active_engine` / `engine_unresolved` 是**系统缺席**、着色最刺眼 ——
    /// ⛔ 不许和「今天没好票」用同一种颜色。
    func testDroppedReasonLabelsCoverAllV22Codes() {
        let v22 = ["evidence_degraded_out", "mech_gate_rejected", "position_unfit",
                   "core_unfit", "members_all_removed", "no_active_engine", "engine_unresolved"]
        for code in v22 {
            XCTAssertNotEqual(nkDroppedReasonLabel(code), code, "\(code) 没有中文换算,界面会印英文码")
        }
        XCTAssertEqual(nkDroppedReasonTone("capacity_overflow"), .good)
        XCTAssertEqual(nkDroppedReasonTone("no_active_engine"), .bad)
        XCTAssertEqual(nkDroppedReasonTone("engine_unresolved"), .bad)
        XCTAssertEqual(nkDroppedReasonTone("position_unfit"), .warn)
        // 历史码仍认(老快照回放,⛔ 别当非法值)。
        XCTAssertNotEqual(nkDroppedReasonLabel("below_quality_line"), "below_quality_line")
        // 未识别码原样透传,⛔ 不瞎翻译。
        XCTAssertEqual(nkDroppedReasonLabel("some_future_code"), "some_future_code")
    }

    /// ③b 新增两键解得出;老快照缺键 → nil(⛔ 不是「没卡在任何关」)。
    func testDroppedBasketGateKeysDecodeAndOldSnapshotHasNone() throws {
        let withGate = try JSONDecoder().decode(DroppedBasket.self, from: Data("""
        {"name": "固态电池", "mechScore": 0.61, "reason": "mech_gate_rejected",
         "gate": "sector", "gateDetail": "sector.strength=0.21<0.35"}
        """.utf8))
        XCTAssertEqual(withGate.gateLabel, "板块关")
        XCTAssertEqual(withGate.gateKind, .mechanical)
        XCTAssertEqual(withGate.gateDetail, "sector.strength=0.21<0.35")

        let old = try JSONDecoder().decode(DroppedBasket.self, from: Data("""
        {"name": "老快照", "mechScore": 0.4, "reason": "below_quality_line"}
        """.utf8))
        XCTAssertNil(old.gateLabel)
        XCTAssertNil(old.gateKind)
    }

    /// 🔴 **机械关 / 证据关二分是产品语义**(裁定 #6/#11/#12):市场 · 板块 = 硬否决;
    /// 驱动 · 核心 · 位置 · 证据 = 只降级。⛔ 混成一类就是把「否决」讲成「扣分」。
    func testGateKindSplitMatchesServerContract() {
        XCTAssertEqual(nkGateKind("market"), .mechanical)
        XCTAssertEqual(nkGateKind("sector"), .mechanical)
        for g in ["driver", "core", "position", "evidence"] {
            XCTAssertEqual(nkGateKind(g), .evidence, "\(g) 是证据关(只降级)")
        }
        XCTAssertEqual(nkGateKind("future_gate"), .unknown, "⛔ 未识别关口不许猜成任何一类")
        XCTAssertEqual(nkGateOrder, ["market", "driver", "sector", "core", "position", "evidence"])
    }

    /// 六关灯条从**冻结卡**的 `tierBreakdown.gates` 读出;恒六格、顺序固定。
    func testBasketGatesProjectionFromFrozenCard() {
        let node = NKJSON.object(["gates": .object([
            "available": .bool(true),
            "engine_code": .string("C1"), "engine_version": .string("C1-v1"),
            "verdicts": .object(["market": .string("pass"), "sector": .string("reject"),
                                 "driver": .string("pass"), "evidence": .string("degrade")]),
            "evidence_degrades": .number(1),
            "degraded_gates": .array([.string("evidence")]),
            "blocks_t1": .bool(true),
            "position_unfit": .bool(false),
            "core_unfit": .bool(true),
        ])])
        let g = BasketGates(tierBreakdown: node)
        XCTAssertTrue(g.available)
        XCTAssertEqual(g.engineCode, "C1")
        XCTAssertEqual(g.lights.count, 6, "恒六格 —— 缺记录的那格也要在,⛔ 不隐藏")
        XCTAssertEqual(g.lights.map(\.gate), nkGateOrder)
        // 「板块关否决」按管线顺序排在「证据关降级」之前 → 卡在板块关。
        XCTAssertEqual(g.blockedGate, "sector")
        // 没记录的那关如实标「未记录」,⛔ 不是「过了」。
        XCTAssertEqual(g.lights.first(where: { $0.gate == "core" })?.verdictLabel, "未记录")
        XCTAssertEqual(g.lights.first(where: { $0.gate == "core" })?.tone, .neutral)
        XCTAssertTrue(g.blocksT1)
        XCTAssertTrue(g.coreUnfit)
        XCTAssertFalse(g.positionUnfit)
    }

    /// 🔴 老卡没有 `gates` 节 → `available == false`,**⛔ 绝不许被读成「六关都过了」**。
    func testBasketGatesAbsentIsNotAllPass() {
        XCTAssertFalse(BasketGates(tierBreakdown: nil).available)
        XCTAssertFalse(BasketGates(tierBreakdown: .object([:])).available)
        // 服务端显式写 available=false(该篮没有关口汇总)同样不是"都过了"。
        XCTAssertFalse(BasketGates(tierBreakdown: .object(["gates": .object(["available": .bool(false)])])).available)
        XCTAssertTrue(BasketGates(tierBreakdown: nil).lights.allSatisfy { $0.verdict == nil })
    }

    /// 篮子级读法:卡优先、留痕兜底(报告快照路只有卡,live 路两处都有)。
    func testBasketGatesReadsCardFirstThenTierHistory() {
        let gatesNode = NKJSON.object(["gates": .object([
            "available": .bool(true), "verdicts": .object(["market": .string("pass")]),
        ])])
        let liveOnly = Basket(basketId: 1, tierHistory: Tier(basketId: 1, mechBreakdown: gatesNode))
        XCTAssertTrue(liveOnly.gates.available, "live 路要能从 tierHistory 兜到")
        let snapshot = Basket(basketId: 1, card: BasketCard(tierBreakdown: gatesNode))
        XCTAssertTrue(snapshot.gates.available, "报告快照路要能从卡上读到")
    }


    // MARK: - V2.2-② 行情状态:`available == false` 不是「没风险」

    func testMarketRegimeEmptyStateIsExplicitlyUnavailable() {
        XCTAssertFalse(MarketRegime.empty.available)
        XCTAssertNil(MarketRegime.empty.day)
    }

    /// 复盘板块各页的 rawValue = `NECKLINE_INITIAL_REVIEW_PAGE` 的合法值。
    /// 🔴 **V2.5.0 S1 从五页回到三页**:`selectionClock` / `tradeClock` 随双时钟复盘
    /// 整块退役(`review/{selection_clock,trade_clock}.py` 与 `/api/clocks/*` 已删除)。
    /// ⛔ **反向断言两条不许去掉**:旧 rawValue 必须解不出来 —— 老截图脚本传它时应当
    /// 落到默认页并被人看见,而不是静默命中一个"看起来还在"的空页。
    func testReviewPageRawValuesAreTheQAHookContract() {
        XCTAssertEqual(ReviewPage.allCases.map(\.rawValue),
                       ["daily", "cumulative", "reconcile"])
        XCTAssertEqual(ReviewPage(rawValue: "cumulative"), .cumulative)
        XCTAssertNil(ReviewPage(rawValue: "selectionClock"), "双时钟复盘已退役")
        XCTAssertNil(ReviewPage(rawValue: "tradeClock"), "双时钟复盘已退役")
        XCTAssertNil(ReviewPage(rawValue: "handoff"), "移交件是累计页里的出口,不是独立页")
    }

    // MARK: - V2.1-② 移交 ⑦:③ 节档位 = **现役两档 ∪ 快照实际档位**

    /// 新报告(只有 T1/T2)→ 恰好两档:⛔ 不许凭空多一个恒空 T3 分组
    /// (那会说「今日 T3 为空(算过了…)」,而真相是 T3 已取消 = 把系统缺席讲成市场结论)。
    func testDisplayTiersOnTwoTierSnapshotHasNoGhostT3() {
        let daily = BasketDaily(baskets: [Basket(basketId: 1, tier: 1), Basket(basketId: 2, tier: 2)],
                                basketsAvailable: true)
        XCTAssertEqual(daily.displayTiers, [1, 2])
    }

    /// 空篮子的新报告仍显示两档(「今日 T1 为空」这句诚实披露不许消失)。
    func testDisplayTiersOnEmptySnapshotStillShowsBothLiveTiers() {
        XCTAssertEqual(BasketDaily(baskets: [], basketsAvailable: true).displayTiers, [1, 2])
    }

    /// 🔴 回放 V2 老报告(含 tier=3)→ **T3 分组必须照出**:写死 `[1,2]` 会让历史 T3
    /// 在客户端静默消失,等于把服务端的读侧宽容在展示层拆掉(② 移交 ⑦ 的硬约束)。
    func testDisplayTiersKeepsHistoricalT3FromFrozenSnapshot() {
        let daily = BasketDaily(baskets: [Basket(basketId: 1, tier: 1), Basket(basketId: 9, tier: 3)],
                                basketsAvailable: true)
        XCTAssertEqual(daily.displayTiers, [1, 2, 3])
        XCTAssertEqual(daily.baskets(tier: 3).map(\.basketId), [9])
    }

    /// `tier == nil`(极旧快照 / 数据缺口)**不进任何档**,⛔ 不拿假档位塞进去。
    func testDisplayTiersIgnoresNilTierBaskets() {
        let daily = BasketDaily(baskets: [Basket(basketId: 7, tier: nil)], basketsAvailable: true)
        XCTAssertEqual(daily.displayTiers, [1, 2])
    }

    // MARK: - V2.1-④ 百分制打分卡的读法(两条路各填一处)

    /// 报告快照路径:分数住 `BasketOut.scorePercent`(B 类,随报告冻住)。
    func testScoreReadsFromSnapshotWhenPresent() {
        let b = Basket(basketId: 1, scorePercent: 62.5,
                       scoreContributions: [ScoreContribution(dim: "tradability", label: "可交易性",
                                                              contribPercent: 20.0)])
        XCTAssertEqual(b.scoreDisplayPercent, 62.5)
        XCTAssertEqual(b.scoreDisplayContributions.map(\.dim), ["tradability"])
    }

    /// live 路径(`GET /baskets`):`BasketOut` 两键刻意留空,分数住 `tierHistory`。
    func testScoreFallsBackToTierHistoryOnLivePath() {
        let th = Tier(basketId: 1, scorePercent: 55.0,
                      scoreContributions: [ScoreContribution(dim: "card_density", label: "卡密度",
                                                             contribPercent: 6.7)])
        let b = Basket(basketId: 1, tierHistory: th)
        XCTAssertEqual(b.scoreDisplayPercent, 55.0)
        XCTAssertEqual(b.scoreDisplayContributions.map(\.dim), ["card_density"],
                       "分数从哪条路来,拆解就从哪条路取 —— ⛔ 不许拼两份数据的混合体")
    }

    /// 🔴 两条路都没有 → `nil` = **本报告版本无打分**,⛔ **绝不是 0 分**
    /// (0 是一个极差的实质性判断,拿它冒充"没这个数"是本项目反复禁止的那类谎)。
    func testScoreAbsenceIsNilNotZero() {
        let b = Basket(basketId: 1)
        XCTAssertNil(b.scoreDisplayPercent)
        XCTAssertTrue(b.scoreDisplayContributions.isEmpty)
    }

    /// 未登记的新维度:`label` 为空时**原样显示 `dim`**,⛔ 客户端不另建中文映射表。
    func testScoreContributionFallsBackToRawDimName() {
        XCTAssertEqual(ScoreContribution(dim: "brand_new_dim").displayLabel, "brand_new_dim")
        XCTAssertEqual(ScoreContribution(dim: "sector_strength", label: "板块强度").displayLabel, "板块强度")
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

    /// ⛔ **V2.4.0 P0**:原 `testRetreatKindRoutesToBaskets` 断言 `"retreat"` 路由到
    /// 选股面。**被 P0.1 表「退潮 APNs / Bark / 系统推送 = 删」取代** —— 该 kind 已进
    /// 服务端 `RETIRED_KINDS`、永不再有新推送,把一条只可能是换包前遗留的旧通知路由
    /// 到某个板块,只会让人以为那个能力还在。**改成反向断言:不路由**(通知本身照常显示)。
    func testRetiredRetreatKindNoLongerRoutes() {
        XCTAssertNil(PushManager.targetTab(forKind: "retreat"))
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

    // MARK: - V2.3.1 金额格式化(千分位 + 符号位置)
    //
    // 🔴 **这组测试是为一个真踩过的 bug 立的**:`signedAmount` 最初把负数直接喂给
    // `NumberFormatter`,`¥` 又是自己拼在前面的 → 得到 **`¥-1,116`**(负号跑进货币符号
    // 里面)。**每一笔亏损仓都会中**,而编译与单测当时都发现不了。
    // 期望值全部取自设计原型(`Neckline视觉升级/Neckline macOS.dc.html` 778/800/804/958 行、
    // `Neckline 信息卡与对账.dc.html` 185 行),⛔ 不是拍脑袋定的。

    func testAmountFormattingMatchesPrototype() {
        // 「额」:千分位、**不带小数**
        XCTAssertEqual(NKFmt.amount(48600), "48,600", "同题材敞口 ¥48,600")
        XCTAssertEqual(NKFmt.amount(120000), "120,000", "总仓分母 ¥120,000")
        // 「价 / 费」:千分位 + **两位小数**(高价股也要读得出来)
        XCTAssertEqual(NKFmt.price(42.30), "42.30")
        XCTAssertEqual(NKFmt.price(12.69), "12.69")
        XCTAssertEqual(NKFmt.price(1802), "1,802.00", "高价股必须分组")
    }

    func testSignedAmountPutsSignOutsideCurrencySymbol() {
        XCTAssertEqual(NKFmt.signedAmount(1444), "+¥1,444", "合计浮盈")
        // 🔴 就是这一条:⛔ 不许是 `¥-1,116`
        XCTAssertEqual(NKFmt.signedAmount(-1116), "-¥1,116", "符号必须在 ¥ 外面")
        XCTAssertEqual(NKFmt.signedAmount(0), "¥0", "零不带符号")
        // 0 位小数下四舍五入到 0 的小额负数:**符号仍保留** ——
        // 宁可看着怪,也不把一笔小亏印成持平。
        XCTAssertEqual(NKFmt.signedAmount(-0.4), "-¥0")
    }

    func testSignedMoneyKeepsCentsAndGroups() {
        XCTAssertEqual(NKFmt.signedMoney(142300000), "+¥142,300,000.00", "龙虎榜净额那一族保留分")
        XCTAssertEqual(NKFmt.signedMoney(-142300000), "-¥142,300,000.00")
    }

    /// ⚠ 分组符必须**与用户系统区域无关**(locale 钉死 `en_US_POSIX`):跟着系统走会让
    /// 不同机器上的截图对不上,某些区域还会出现空格分组(`77 080`)。
    func testGroupingIsLocaleIndependent() {
        let saved = NSTimeZone.default
        defer { NSTimeZone.default = saved }
        XCTAssertTrue(NKFmt.amount(1234567).contains(","), "必须用逗号分组")
        XCTAssertFalse(NKFmt.amount(1234567).contains(" "), "⛔ 不许出现空格分组")
        XCTAssertEqual(NKFmt.amount(1234567), "1,234,567")
    }
}
#endif
