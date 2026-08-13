//
//  BasketCardView.swift
//  Neckline — **篮子卡(蓝图 4.6 十一项)**(V2-⑮)。
//
//  数据来自报告快照里冻结的 `card_json`(**D0 冻结件**,不是实时的);验证状态角标
//  另走 `GET /baskets/{id}/verification`(**实时**)。
//
//  ⚠ **V2.3 视觉升级**:
//    · **macOS 上这是右详情栏,不再是 700×780 的 `.sheet`**(规范 §01 决定 06)——
//      故 macOS 分支**不套 `NavigationStack`、没有「关闭」按钮**(详情栏关不掉,
//      它总要显示点什么;想回概览就点列表栏的「今日概览」)。iOS 保留 sheet。
//    · 成员卡整块换成 `NKMemberCard`(**可展开、一等对象**),⛔ 老的
//      `BasketMemberCard` / `GateVerdictRow` 已删,别再加回来。
//    · 六关在详情栏用**宫格** `GateGrid`(不是灯条);等宽原始件(`tierBreakdown` /
//      `verificationSpec` / `invalidationSpec`)下沉 `NKAuditSection`。
//
//  **本页硬纪律(⑦ / ⑭-C 对拍表 §四.2)**:
//   1. **角色两说并存**:`roleConflict=true` 时 `roleLlm` / `roleMech` **两个都显示**,
//      ⛔ 不许挑一个当"正确答案"。
//   2. **三个参考件各带 `*Clamp` + `*UnavailableReason`**:夹逼拒收时值是 `nil` 且原因
//      非空 —— ⛔ 不许把 `nil` 显示成 `0` 或空白了事。
//   3. **`exitReference` 不是止盈线**(§2.8-C 语义红线),文案里不许这么写:
//      离场参考是计划参考、不是止盈信号,是否离场由用户判断(V2.4.0 P3.2)。
//   4. **`disclaimer` 原样透传不改写**;每处参考件带「参考、非指令」且**四不**
//      (不进排序 / 不进哨兵 / 不改去留 / 不加分)。
//   5. `narrative` **原文整段呈现**(§2.7),⛔ 不拆解塞回枚举卡片。
//

import SwiftUI

struct BasketCardPage: View {
    @Bindable var model: AppModel
    let basket: Basket

    var body: some View {
        #if os(iOS)
        NavigationStack {
            ScrollView {
                content.padding(NKSpace.pagePad)
            }
            .background(NK.pageBgIOS)
            .navigationTitle(basket.name.isEmpty ? basket.basketKey : basket.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { model.dismissBasket() }
                }
            }
        }
        #else
        // macOS:详情栏内容(外层 `NKSplitLayout` 已经给了 ScrollView 与页边距)。
        content
        #endif
    }

    // 🔴 **V2.4.0 P3.4:默认/解释/审计三层**(K8.md §十「报告信息层级」,施工图
    // P3.4 逐字;字段分组的权威映射见 `neckline/selection/basket_card.py`
    // `BasketCard.to_card_json()` 前的注释块,⛔ 两处分组口径必须一致,改一处改两处)。
    //
    // 默认层只答「买什么、在哪买、什么时候不成立」五件事;六关 / 机械分 / 原始 LLM
    // 叙述**不得继续占用默认层**(它们把 ⑥ 挤到第二屏正是现役 v4/v5 卡的病灶)。
    // ⚠ **这是纯展示层收敛**:`card_json` 一个字段都没删,`gatesCard`/`scoreCard`/
    // `narrativeCard` 三个既有函数原样保留、只是挪进 `auditSection` 调用。
    @ViewBuilder
    private var content: some View {
        if let card = basket.card {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                titleBlock(card)                    // ① Tier / 篮子名 / 引擎版本
                driverOneLineCard(card)              // ② 一句话共同驱动
                preferredMemberCard(card)            // ③ 首选成员 + 角色 + 入场区间 + 失效位置
                whyNowCard(card)                     // ④ 为什么是现在
                primaryRiskCard(card)                // ⑤ 一条主要风险或待确认项
                referenceDisclosure(card)            // 「i 参考、非指令」+ Tier 红线句(常显)
                explainDisclosure(card)              // 一级展开「解释」
                auditSection(card)                   // 二级展开「审计」(原始件下沉)
            }
        } else {
            // 🔴 **卡未就绪也有完整的标题块**(原型 646–652 行):篮子是在的,
            // 缺的只是 LLM 那半份 —— 连标题都不给,读者第一眼看到的就是"什么都没有"。
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                notReadyTitleBlock
                cardNotReadyCard
                if basket.scoreDisplayPercent != nil { scoreCardFallback }
                gatesCard
                membersFallback
            }
        }
    }

    // MARK: - 标题块

    @ViewBuilder
    private func titleBlock(_ card: BasketCard) -> some View {
        VStack(alignment: .leading, spacing: 7) {   // 原型 253 行 margin-bottom:7
            HStack(spacing: 7) {                    // 原型 253 行 gap:7
                // 原型 254 行:Tier 与档内次序是**一枚**实心徽标「T1 第 1 位」,
                // ⛔ 不是「T1」+「T1 第 1 位」两枚(同一件事写两遍)。
                if let t = card.tier {
                    NKChip(text: card.rankInTier.map { "T\(t) 第 \($0) 位" } ?? "T\(t)",
                           tone: t == 1 ? .good : (t == 2 ? .warn : .neutral), filled: true)
                }
                // V2.2-③-E 引擎徽标(裁定 #9:单篮子单引擎,成员继承 —— 徽标只出
                // 现在篮子头上,⛔ 不在成员卡上重复画)。老卡缺这三键是常态。
                // ⚠ 文案刻意写「选股引擎」而不是裸「引擎」:审计视图里已有
                // `engineApiVersion`(契约版本号)占用了「引擎 N」这个措辞,两个是
                // 完全不同的概念,同页出现容易撞名。
                if let ev = basket.engineVersionDisplay {
                    NKChip(text: "选股引擎 \(ev)", tone: .neutral)
                }
                if let v = card.version { NKChip(text: "卡 v\(v)") }
                // ⛔ V2.4.0 P0:原先这里还挂一枚退潮红实底徽标,已随退潮刹车退役删除。
                Spacer()
                VerificationBadge(model: model, basketId: basket.basketId)
            }
            #if os(macOS)
            Text(basket.name.isEmpty ? basket.basketKey : basket.name)
                .font(NKFont.title1).tracking(-0.4).foregroundStyle(NK.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            #endif
            // ⚠ **驱动一句在这里不重复**:它是 ① 那张卡的正文(原型 341–343 行),
            // 标题下再印一遍就是同一句话连着出现两次。
        }
    }

    /// 卡未就绪时的标题块(原型 646–652):Tier 徽标 + 引擎 + 「今天还没判过」+
    /// 大标题 + 驱动一句(这时 ① 那张卡不存在,驱动只能挂在这里)。
    private var notReadyTitleBlock: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 7) {
                if let t = basket.tier {
                    NKChip(text: "T\(t)", tone: t == 1 ? .good : (t == 2 ? .warn : .neutral),
                           filled: true)
                }
                if let ev = basket.engineVersionDisplay {
                    NKChip(text: "选股引擎 \(ev)", tone: .neutral)
                }
                // ⛔ V2.4.0 P0:退潮红实底徽标已删(同上)。
                Spacer()
                VerificationBadge(model: model, basketId: basket.basketId)
            }
            Text(basket.name.isEmpty ? basket.basketKey : basket.name)
                .font(NKFont.title1).tracking(-0.4).foregroundStyle(NK.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // ⛔ **V2.4.0 P0:`brakeNoticeCard` 与 `brakeButton` 已整体删除。**
    //
    // P0.1 表那两行:「代理关注池 →『大盘退潮』= 删」+「作废当日计划 / 停止开新仓的
    // 交易动作语义 = 删」。
    // ⚠ 那张卡里真正有价值的两句话**已不再需要**:①「作废的是今日的开仓计划、不是这张卡」
    // —— 现在压根不会有那回事;②「补录开仓永不灰化」—— 该纪律仍然成立,
    // 落点在 `NKMemberCard` 与持仓页的补录入口(它们本来就无条件可点),⛔ 不需要一张
    // 只在某个已退役状态下才出现的卡来重申。
    // 🔴 ⛔ 不许换名接回来(「风险提示卡」/「观察卡」)——审计规格 P0.7 第二种假完成。

    /// 「本篮的卡还没生成」(原型 654–662):琥珀描边卡 + 图标 + 两段文案。
    /// ⛔ **不是空态图标居中那一套** —— 这不是"没有内容",是"缺了一半"。
    private var cardNotReadyCard: some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: "doc.badge.clock").font(.system(size: 20, weight: .light))
                .foregroundStyle(NK.amber).padding(.top, 1)
            VStack(alignment: .leading, spacing: 6) {
                Text(basket.cardUnavailableText ?? "本篮的卡还没生成")
                    .font(NKFont.headline).foregroundStyle(NK.textPrimary)
                Text(cardNotReadyBody)
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 20).padding(.vertical, 22)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.card)
            .stroke(NK.amber.opacity(0.30), lineWidth: 0.5))
    }

    /// ⚠ 原因码原样带出来(`card_not_ready` / `card_corrupt` 是**服务端语义标识符**,
    /// 排障时要能对上),⛔ 不改写成人话就把码丢了。
    private var cardNotReadyBody: String {
        let code = basket.cardUnavailableReason.map { "(\($0))" } ?? ""
        return "⛔ 这不是「篮子不存在」—— 篮子在,只是 11 项卡还没生成\(code)。\n"
            + "驱动、成员、六关、打分这些机械侧的结果照常有效,缺的是 LLM 那半份:"
            + "证据链、预期上涨路径、验证与失效条件、逐只成员的位置关 / 核心关判定。"
    }

    /// 卡未就绪时仍然有机械分(原型 664–676 行照样画打分卡)。
    private var scoreCardFallback: some View {
        NKCard {
            BasketScoreCard(percent: basket.scoreDisplayPercent,
                            contributions: basket.scoreDisplayContributions,
                            compact: false, mechScore: basket.tierHistory?.mechScore)
        }
    }

    // MARK: - 🔴 P3.4 默认层 ②③④⑤(K8.md §十 末段逐字)

    /// ② 一句话共同驱动(不含证据链 / 为什么是现在 —— 那两段分别挪进③④与「解释」层)。
    @ViewBuilder
    private func driverOneLineCard(_ card: BasketCard) -> some View {
        if !card.driver.isEmpty || !card.driverKind.isEmpty {
            NKCard {
                Text(driverText(card)).font(NKFont.body).lineSpacing(4)
                    .foregroundStyle(NK.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// ④ 为什么是现在。
    @ViewBuilder
    private func whyNowCard(_ card: BasketCard) -> some View {
        if !card.whyNow.isEmpty {
            NKCard {
                VStack(alignment: .leading, spacing: 5) {
                    Text("为什么是现在").nkLabel().foregroundStyle(NK.textTertiary)
                    Text(card.whyNow).font(NKFont.body).lineSpacing(4)
                        .foregroundStyle(NK.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// ⑤ 一条主要风险或待确认项(⛔ 不是全部——`risks.first`;其余移进「解释」层的
    /// 验证与失效条件旁边,一字不丢)。
    @ViewBuilder
    private func primaryRiskCard(_ card: BasketCard) -> some View {
        if let first = card.risks.first {
            NKCard {
                VStack(alignment: .leading, spacing: 5) {
                    Text("待确认").nkLabel().foregroundStyle(NK.amber)
                    Text(first).font(NKFont.body).lineSpacing(4)
                        .foregroundStyle(NK.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// 「i 参考、非指令」+ Tier 红线句 —— **常显的小披露**,不折进「解释」/「审计」两层
    /// (它答的不是"这张卡的证据",是"这张卡的效力边界",每次都该被看见一次)。
    /// `disclaimer` / `llmStage` / `notes` 也在这里 —— 都是**关于这张卡本身**的话,
    /// 不是关于某一票的解释材料。
    @ViewBuilder
    private func referenceDisclosure(_ card: BasketCard) -> some View {
        NKDisclosure(summary: "参考、非指令") {
            Text("参考、非指令 · 不进排序、不进哨兵、不改去留、不加分")
            if !card.disclaimer.isEmpty {
                Text(card.disclaimer).fixedSize(horizontal: false, vertical: true)
            }
            Text("Tier / 档内次序 = 注意力优先级,不是收益预测 · T1 ≠ 最会涨 · 终选权在你")
                .fixedSize(horizontal: false, vertical: true)
            if !card.llmStage.isEmpty { Text("生成阶段:\(card.llmStage)") }
            ForEach(card.notes, id: \.self) { n in
                Text("· \(n)").fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - 🔴 P3.4 默认层 ③:首选成员块

    /// 首选成员 = `is_primary` 那一个,缺席时取第一个(卡上成员从不为空数组之外的
    /// 别的东西——`card.members.isEmpty` 时整块不画,回退态见 `membersFallback`)。
    private func preferredMember(_ card: BasketCard) -> BasketMember? {
        card.members.first(where: \.isPrimary) ?? card.members.first
    }

    /// D0 收盘价(= 卡上语境里的「现价」)。机械面板原样透传,⛔ 不是实时行情
    /// (篮子卡是 D0 冻结件,这里显示的是生成那天的收盘参考价)。
    private func memberClose(_ m: BasketMember) -> Double? { m.mech.objectValue?["close"]?.doubleValue }

    /// 「失效位」= `mech.stop_price`(= `close×(1−stop_pct)`,系统按现役章程算,
    /// 与 `invalidation_spec.members[].close_below_stop_line` 同一个数、同一个唯一源
    /// ——`CLAUDE.md`「invalidation 三处同名不同物」②:D0 冻结的判断失效位置)。
    /// ⛔ 不是 `exitReference`(那是目标离场区间,完全另一件事)。
    private func memberStopPrice(_ m: BasketMember) -> Double? { m.mech.objectValue?["stop_price"]?.doubleValue }

    /// 现价相对入场区间的位置(纯算术派生,⛔ 不发明新阈值——只回答"在/上/下")。
    private func memberPositionNote(_ m: BasketMember) -> String? {
        guard let close = memberClose(m), close > 0,
              let low = m.entryZone?.low, let high = m.entryZone?.high,
              low > 0, high > 0 else { return nil }
        if close > high {
            return "现价已高于入场区间上沿 \(String(format: "%.1f", (close - high) / high * 100))%"
        }
        if close < low {
            return "现价已低于入场区间下沿 \(String(format: "%.1f", (low - close) / low * 100))%"
        }
        return "现价在入场区间内"
    }

    @ViewBuilder
    private func preferredMemberCard(_ card: BasketCard) -> some View {
        if let m = preferredMember(card) {
            NKCard {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 8) {
                        Text("首选成员").nkLabel().foregroundStyle(NK.textTertiary)
                        Spacer(minLength: 0)
                        if m.roleConflict {
                            NKChip(text: "角色两说", tone: .warn)
                        } else if !m.roleDisplay.isEmpty {
                            NKChip(text: m.roleDisplay)
                        }
                        // 🔴 裁定 ⑤:三态(已确认 / 归属待确认 / 老卡未记录)。
                        if m.isPrimary {
                            NKChip(text: NKPrimaryStatus.chipText(m.primaryStatus),
                                   tone: NKPrimaryStatus.isPending(m.primaryStatus) ? .warn
                                       : (NKPrimaryStatus.isRecorded(m.primaryStatus) ? .good : .info))
                        }
                    }
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        // ⛔ 视图里不写裸 `.system(size:)`(CLAUDE.md V2.3 字阶纪律)——
                        // 设计交接包要的 `20/600` 恰好就是 `NKFont.metric` 那一档。
                        Text(m.name).font(NKFont.metric).tracking(-0.3)
                            .foregroundStyle(NK.textPrimary).lineLimit(1)
                        Text(m.tsCode).font(NKFont.callout.monospacedDigit())
                            .foregroundStyle(NK.textTertiary).lineLimit(1)
                        Spacer(minLength: 6)
                        if let rs = m.rsRank {
                            Text("RS #\(rs)").font(NKFont.caption.monospacedDigit())
                                .foregroundStyle(NK.textSecondary)
                        }
                        #if os(macOS)
                        // macOS 详情栏够宽,多带一项行业 lift(iOS 402pt 放不下三项数字)。
                        if let lift = m.industryLift {
                            Text(String(format: "行业 lift %.2f", lift))
                                .font(NKFont.caption.monospacedDigit())
                                .foregroundStyle(NK.textTertiary)
                        }
                        #endif
                    }
                    Divider().overlay(NK.hairline.opacity(0.8))
                    HStack(alignment: .top, spacing: 12) {
                        // ⚠ **入场区间那一格用 `compactRangeText`(去 `¥`、短破折号)**:
                        // 三格等分下 `¥30.60 ~ ¥31.95` 在 393pt 上会被截成 `¥30.60 ~ ¥31…`
                        // (V2.4.0 P3 实拍逮到)。⛔ **不靠 `layoutPriority` 加宽这一格** ——
                        // 三格都是 `maxWidth:.infinity`,给谁高优先级谁就吃掉全部空间。
                        memberStatCell("入场区间", m.entryZone?.compactRangeText, NK.textPrimary)
                        memberStatCell("失效位",
                                       memberStopPrice(m).map { NKFmt.price($0) }, NK.down)
                        memberStatCell("现价", memberClose(m).map { NKFmt.price($0) }, NK.textPrimary)
                        #if os(macOS)
                        if let note = memberPositionNote(m) {
                            memberStatCell("位置", note, NK.amber)
                        }
                        #endif
                    }
                    if let stop = memberStopPrice(m), let zone = m.entryZone,
                       let low = zone.low, let high = zone.high, stop > 0, low > 0, high > 0 {
                        NKMemberScaleBar(invalidation: stop, entryLow: low, entryHigh: high,
                                         price: memberClose(m) ?? 0)
                    }
                    HStack(spacing: 8) {
                        if card.members.count > 1 {
                            Text("另 \(card.members.count - 1) 只成员")
                                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                            // 其余成员各一颗状态点(位置关 / 核心关取最差)——**与列表行
                            // 那一颗同一把尺**(`nkMemberDotColor` 单一源)。⚠ 它只说
                            // 「那几只里有没有黄 / 红」,⛔ 不代表任何结论,点开「解释」才是全貌。
                            HStack(spacing: 3) {
                                ForEach(card.members.filter { $0.tsCode != m.tsCode }) { other in
                                    Circle().fill(nkMemberDotColor(other))
                                        .frame(width: 5, height: 5)
                                }
                            }
                        }
                        Spacer(minLength: 0)
                        Text("解释 ›").font(NKFont.caption).fontWeight(.semibold)
                            .foregroundStyle(NK.accent)
                    }
                }
            }
        }
    }

    /// 首选成员块的一格读数。`value == nil` 时如实说「本次不可用」(⛔ 不显示成 0/空白)。
    private func memberStatCell(_ title: String, _ value: String?, _ tone: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(NKFont.caption).foregroundStyle(NK.textTertiary)
            Text(value ?? "本次不可用")
                .font(NKFont.headline.monospacedDigit())
                .foregroundStyle(value != nil ? tone : NK.amber)
                .lineLimit(1).minimumScaleFactor(0.85)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - 一级展开「解释」(K8.md §十:全部成员及角色 / 预期上涨路径 / 最强支持证据 /
    // 主要反证 / 验证和失效条件)

    @ViewBuilder
    private func explainDisclosure(_ card: BasketCard) -> some View {
        NKDisclosure(summary: "全部成员及角色 · 预期上涨路径 · 支持证据 · 反证 · 验证与失效条件") {
            membersSection(card)
            upsidePathCard(card)
            strongestEvidenceBlock(card)
            counterEvidenceBlock(card)
            verificationCard(card)
        }
    }

    /// 「最强支持证据」——取 `evidence` 首条(检索环节已按相关性排列,⛔ 客户端不二次排序)。
    @ViewBuilder
    private func strongestEvidenceBlock(_ card: BasketCard) -> some View {
        if let top = card.evidence.first {
            VStack(alignment: .leading, spacing: 3) {
                Text("最强支持证据").nkLabel().foregroundStyle(NK.textTertiary)
                Text(top.claim).font(NKFont.callout).lineSpacing(3)
                    .foregroundStyle(NK.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Text([top.source, top.date].filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }
        }
    }

    /// 「主要反证」——取自六关判定留痕的 `gate_counter_evidence`(P1.5+ 已产出,
    /// `tier.py::_gate_breakdown` 按关归并、成员级两关按成员码前缀区分是谁说的)。
    /// ⛔ 这不是新字段,是既有 `tierBreakdown.gates.gate_counter_evidence` 的解释层取材。
    @ViewBuilder
    private func counterEvidenceBlock(_ card: BasketCard) -> some View {
        let items = (card.tierBreakdown.objectValue?["gates"]?.objectValue?["gate_counter_evidence"]?
            .objectValue ?? [:])
            .sorted { $0.key < $1.key }
            .flatMap { gate, arr in (arr.arrayValue ?? []).compactMap(\.stringValue).map { (gate, $0) } }
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 3) {
                Text("主要反证").nkLabel().foregroundStyle(NK.textTertiary)
                ForEach(Array(items.enumerated()), id: \.offset) { _, pair in
                    Text("· [\(nkGateLabel(pair.0))] \(pair.1)")
                        .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: - 打分卡(V2.1-④ 百分制,**纯展示层**;二级展开「审计」的机械评分与贡献项)

    @ViewBuilder
    private func scoreCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                BasketScoreCard(percent: basket.scoreDisplayPercent,
                                contributions: basket.scoreDisplayContributions,
                                compact: false, mechScore: card.mechScore)
                // §2.8-C 红线:Tier = 注意力优先级,不是收益预测。
                if let reason = card.tierReason, !reason.isEmpty {
                    Divider().overlay(NK.hairline)
                    Text("⑤ 分层理由").nkLabel().foregroundStyle(NK.textTertiary)
                    Text(reason).font(NKFont.body).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let note = card.tierNote, !note.isEmpty {
                    Text(note).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                    NKReferenceNote()
                }
                if let th = basket.tierHistory, th.llmRankDelta != 0 {
                    HStack(spacing: 6) {
                        NKChip(text: "LLM 微调 \(th.llmRankDelta > 0 ? "+" : "")\(th.llmRankDelta) 位",
                               tone: .warn)
                        Spacer()
                    }
                    if let r = th.llmReason, !r.isEmpty {
                        Text("微调理由:\(r)").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                        NKReferenceNote()
                    }
                }
            }
        }
    }

    // MARK: - 🔴 六关判定(详情栏 = 宫格)

    private var gatesCard: some View {
        NKCard { GateGrid(gates: basket.gates) }
    }

    // MARK: - 完整证据链(二级展开「审计」原料;P3.4 起不再占默认层)
    //
    // 🔴 原「①②③ 驱动/证据链/为什么是现在」一张卡三段已拆:①③ 挪进默认层的
    // `driverOneLineCard`/`whyNowCard`(为什么是现在仍是**决策**信息,不是审计材料),
    // 本函数只留②「完整证据链」,原样一字不改地下沉进 `auditSection`
    // ——「最强支持证据」(解释层)只取首条,**完整清单仍在这里**。

    @ViewBuilder
    private func evidenceChainCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 7) {
                HStack(spacing: 8) {
                    Text("完整证据链").nkLabel().foregroundStyle(NK.textTertiary)
                    // ⛔ `evidenceStatus != ok` 必须显式标注"取证不完整",不许静默当完整证据展示。
                    if let note = card.evidenceIncompleteNote {
                        NKChip(text: note, tone: .warn)
                    }
                    Spacer(minLength: 0)
                }
                if card.evidence.isEmpty {
                    Text("本卡未附证据条目").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                } else {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(card.evidence) { e in
                            // 原型 349 行:每条证据左侧一根 3px 竖条,⛔ 不是「·」。
                            HStack(alignment: .top, spacing: 9) {
                                // 原型 `rgba(60,60,67,.14)`;`textTertiary` 是同一灰的 .40。
                                RoundedRectangle(cornerRadius: 2)
                                    .fill(NK.textTertiary.opacity(0.35))
                                    .frame(width: 3)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(e.claim).font(NKFont.callout).lineSpacing(3)
                                        .foregroundStyle(NK.textPrimary)
                                        .fixedSize(horizontal: false, vertical: true)
                                    Text([e.source, e.date].filter { !$0.isEmpty }
                                        .joined(separator: " · "))
                                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                                }
                                Spacer(minLength: 0)
                            }
                            .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
    }

    /// 原型 342 行把驱动类型接在驱动正文后面(「…同步放量。驱动类型:产业事件 · 可证伪。」),
    /// ⛔ 不另起一行小灰字 —— 它是这段话的一部分。
    private func driverText(_ card: BasketCard) -> String {
        guard !card.driverKind.isEmpty else { return card.driver }
        guard !card.driver.isEmpty else { return "驱动类型:\(card.driverKind)。" }
        return card.driver + "驱动类型:\(card.driverKind)。"
    }

    // MARK: - ④ 成员、角色与对拍分歧(**一等对象**)

    /// 🔴 **macOS 上成员卡是"卡里的卡"**(原型 363–626 行):外面一张白卡
    /// `padding:14px 14px 12px`,里面每个成员是一块 `radius 10` 的**无边框**区域,
    /// 选中(= 展开)才有 `#FAFAFC` 底 + 内描边。⛔ 别让每个成员各自当一张独立白卡 ——
    /// 那样四个成员 = 四张卡,与「④ 是一段」的信息结构不符。
    /// iOS 保持既有的独立卡(手机上没有"外层容器"这一层可用宽度,批 7 另行核对)。
    @ViewBuilder
    private func membersSection(_ card: BasketCard) -> some View {
        #if os(macOS)
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text("④ 成员 \(card.members.count) · 为什么这只票能进篮子")
                    .nkLabel().foregroundStyle(NK.textTertiary)
                if !card.roleConflicts.isEmpty {
                    NKChip(text: "角色分歧 \(card.roleConflicts.count) 只", tone: .warn)
                }
                Spacer(minLength: 0)
                Text("点开看逐只判定").font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }
            .padding(.horizontal, 4).padding(.bottom, 10)   // 原型 365 行 `padding:0 4px 10px`
            if card.members.isEmpty {
                NKEmptyState(title: "本卡未列成员", systemImage: "person.2")
            } else {
                ForEach(card.members) { m in
                    NKMemberCard(model: model, member: m, basketName: card.name,
                                 tradeDate: card.tradeDate.isEmpty ? model.report.tradeDate
                                                                   : card.tradeDate)
                }
            }
        }
        .padding(.horizontal, 14).padding(.top, 14).padding(.bottom, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.hairline, lineWidth: 0.5))
        #else
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            HStack(spacing: 6) {
                Text("④ 成员 \(card.members.count) · 为什么这只票能进篮子")
                    .font(NKFont.title3).foregroundStyle(NK.textPrimary)
                Spacer()
                if !card.roleConflicts.isEmpty {
                    NKChip(text: "角色分歧 \(card.roleConflicts.count) 只", tone: .warn)
                }
            }
            Text("点开看逐只判定").font(NKFont.caption).foregroundStyle(NK.textTertiary)
            if card.members.isEmpty {
                NKCard { NKEmptyState(title: "本卡未列成员", systemImage: "person.2") }
            } else {
                ForEach(card.members) { m in
                    NKMemberCard(model: model, member: m, basketName: card.name,
                                 tradeDate: card.tradeDate.isEmpty ? model.report.tradeDate
                                                                   : card.tradeDate)
                }
            }
        }
        #endif
    }

    /// 卡未就绪时的成员段:**只有代码**(原型 699–705:代码是一排等宽小方块,
    /// ⛔ 不是竖着一行一个)。「现在没有」不等于「这几只票没通过」。
    @ViewBuilder
    private var membersFallback: some View {
        if !basket.memberCodes.isEmpty {
            NKCard {
                VStack(alignment: .leading, spacing: 10) {
                    Text("成员 \(basket.memberCodes.count) · 卡未就绪时只有代码")
                        .nkLabel().foregroundStyle(NK.textTertiary)
                    NKWrapRow(spacing: 8, lineSpacing: 8) {
                        ForEach(basket.memberCodes, id: \.self) { c in
                            Text(c).font(NKFont.callout.monospaced())
                                .foregroundStyle(NK.textSecondary)
                                .padding(.horizontal, 10).padding(.vertical, 5)
                                .background(RoundedRectangle(cornerRadius: 6)
                                    .fill(NK.chipNeutral))
                        }
                    }
                    Text("名称、角色、位置关 / 核心关判定、三个参考件都随卡一起来 —— "
                         + "现在没有,不等于这几只票没通过。")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: - ⑥ 预期上涨路径(**参考件**)

    /// V2.3.3-①(K8.md §十 第 8 项 / §十一 第 1 项):由「次日强 / 平 / 弱三剧本」换成
    /// **一段话**的预期上涨路径 —— 驱动怎么推动价格、沿什么结构与节奏往上走、走到哪算走完。
    /// ⛔ 三列并排的 `scriptCell` 已删:开盘那一刻怎么办由**次日 9:26 的竞价确认层**负责。
    /// ⛔ 取不到时**不留空胶囊 / 空格子**,用一句琥珀色的话说出口(CLAUDE.md 空徽标坑)。
    @ViewBuilder
    private func upsidePathCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 11) {
                Text("⑥ 预期上涨路径").nkLabel().foregroundStyle(NK.textTertiary)
                let path = (card.upsidePath ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                if !path.isEmpty {
                    Text(path)
                        .font(NKFont.callout).lineSpacing(3)
                        .foregroundStyle(NK.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    NKReferenceNote()
                } else {
                    Text(card.upsidePathUnavailableReason ?? "本次未生成预期上涨路径")
                        .font(NKFont.caption).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: - ⑦⑧ 验证 / 失效条件 + ⑨ 主要风险(原型 604–618 行:**同一张卡两段**)

    @ViewBuilder
    private func verificationCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 14) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("⑦⑧ 验证与失效条件").nkLabel().foregroundStyle(NK.textTertiary)
                    // 原型 607/608 行:验证 = 绿勾方块,失效 = 红叉方块 —— 两件事的**方向
                    // 相反**,同色同形会读成一串并列条件。
                    if let t = card.verificationText, !t.isEmpty {
                        conditionRow(system: "checkmark", tone: .good, text: t)
                    }
                    if let t = card.invalidationText, !t.isEmpty {
                        conditionRow(system: "xmark", tone: .bad, text: t)
                    }
                    // CLAUDE.md 坑条:篮子 `falsified` ≠ 持仓该走。
                    // 🔴 **⛔ 别用 `+` 把这句拼起来**(V2.3.3 批 ⑦ 实拍逮到):`"a" + "b"`
                    // 的结果是 `String`,`Text(String)` **不解析 Markdown** → `**不是**`
                    // 的四个星号会**原样印在屏幕上**。要拼就拼成**一整条字面量**。
                    //
                    // 🔴 **V2.4.0 复审 🟡-4:⛔ 不许在这里点名三条机械纪律**。旧句写的是
                    // 「止损 / 回落止盈 / 时间退出」,而 `v2.3-k8` 的
                    // `take_profit_retrace = nil` / `maxHoldDays = nil` —— **后两条根本
                    // 不存在**,−5% 那条也已改叫「亏损警戒线」。一句话宣传了三条纪律、
                    // 其中两条是空的 = 最终 DoD 第 15 条不满足。
                    // ✅ 改法:**指向这张卡自己冻结的那份**(下面 ⑩「纪律标签」,服务端
                    // `discipline_labels` 随章程派生),⛔ 客户端不再自己列举纪律名。
                    Text("失效说的是「这个驱动假设不成立了」,**不是**「手里的仓该卖了」——该不该走由持仓纪律管,这张卡当天冻结了哪几条,展开「审计」里的「纪律标签」看。")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                // 🔴 P3.4:`risks.first` 已挪进默认层 `primaryRiskCard`(⑤),
                // 这里只列**其余**风险 —— 一条不少,只是不再全堆在默认层。
                let moreRisks = Array(card.risks.dropFirst())
                if !moreRisks.isEmpty {
                    Divider().overlay(NK.hairline)
                    VStack(alignment: .leading, spacing: 6) {
                        Text("⑨ 其余风险").nkLabel().foregroundStyle(NK.textTertiary)
                        ForEach(moreRisks, id: \.self) { r in
                            Text("· \(r)").font(NKFont.body).lineSpacing(4)
                                .foregroundStyle(NK.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
    }

    private func conditionRow(system: String, tone: NKAxisTone, text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            RoundedRectangle(cornerRadius: NKRadius.badge)
                .fill(tone.color.opacity(0.12))
                .frame(width: 15, height: 15)
                .overlay(Image(systemName: system)
                    .font(.system(size: 8, weight: .bold)).foregroundStyle(tone.color))
                .padding(.top, 1)
            Text(text).font(NKFont.callout).lineSpacing(3).foregroundStyle(NK.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }

    // MARK: - ⑩ LLM 原始叙述(**原文整段**,§2.7;二级展开「审计」原料)
    //
    // 🔴 P3.4:⑪ disclaimer 已挪进默认层的 `referenceDisclosure`(常显小披露,
    // 不必点开审计层才看得到「参考、非指令」)——本函数只留 ⑩ 叙述本体 + 降级说明。

    /// `narrative` 为空且未降级 → 如实写「本卡未附叙述」,⛔ 不整张卡消失。
    @ViewBuilder
    private func narrativeCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                Text("⑩ LLM 原始叙述").nkLabel().foregroundStyle(NK.textTertiary)
                if !card.degraded, card.narrative.isEmpty {
                    Text("本卡未附叙述").font(NKFont.caption)
                        .foregroundStyle(NK.textTertiary)
                }
                if card.degraded {
                    // `degraded=true` = **人话半份缺席、结构化半份照出**(不是"这张卡不可信")。
                    Text("本卡降级:人话半份缺席,上面的结构化内容照常有效")
                        .font(NKFont.caption).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !card.narrative.isEmpty {
                    // 原型 680 行 `13.5px; line-height:1.75` —— 整段叙述是这张卡里
                    // 唯一要**读**的东西,行距刻意比别处松。
                    Text(card.narrative).font(NKFont.body).lineSpacing(6)
                        .foregroundStyle(NK.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: - 审计视图(**原始件下沉**:口径指纹 / 验证条件集 / 机械读数原始件)

    @ViewBuilder
    private func auditSection(_ card: BasketCard) -> some View {
        NKAuditSection(contains: "六关宫格、机械评分、LLM 原始叙述、口径指纹、验证条件集、机械读数原始件") {
            // 🔴 P3.4:六关宫格 / 机械评分与贡献项 / LLM 原始叙述**不得继续占用默认层**
            // (现役 v4/v5 卡把 ⑥ 挤到第二屏的病灶正是这三样)——三个既有函数原样复用,
            // 只是从默认流挪进这个折叠区,内容一字未改。
            gatesCard
            evidenceChainCard(card)
            scoreCard(card)
            narrativeCard(card)
            NKAuditGroup(title: "口径指纹") {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        if let v = card.fingerprint.charterVersion { NKChip(text: "章程 \(v)") }
                        if let v = card.fingerprint.packVersion { NKChip(text: "选股包 \(v)") }
                        if let v = card.fingerprint.engineApiVersion { NKChip(text: "引擎 \(v)") }
                        if let v = card.fingerprint.verificationRulesetVersion {
                            NKChip(text: "验证条件集 \(v)")
                        }
                        if let v = card.specVersion { NKChip(text: "卡形状 \(v)") }
                        // V2.2-③-E:K8 骨架线版本(股票池 / 篮子 / 梯度那条线,与上面
                        // 头部的「选股引擎」徽标是两码事 —— 骨架线只有一条,引擎有三条)。
                        if let v = basket.skeletonVersionDisplay { NKChip(text: "骨架 \(v)") }
                    }
                }
            }
            if !card.disciplineLabels.isEmpty {
                NKAuditGroup(title: "纪律标签") {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(card.disciplineLabels, id: \.self) { l in
                                NKChip(text: l, tone: .warn)
                            }
                        }
                    }
                }
            }
            if let obj = card.tierBreakdown.objectValue, !obj.isEmpty {
                NKAuditGroup(title: "五维分项(维度名与现役包权重键逐字对应)") {
                    NKJSONTable(value: card.tierBreakdown)
                }
            }
            if let obj = card.verificationSpec.objectValue, !obj.isEmpty {
                NKAuditGroup(title: "⑦ 验证条件集(机器半份)") {
                    NKJSONTable(value: card.verificationSpec)
                }
            }
            if let obj = card.invalidationSpec.objectValue, !obj.isEmpty {
                NKAuditGroup(title: "⑧ 失效条件集(机器半份)") {
                    NKJSONTable(value: card.invalidationSpec)
                }
            }
        }
    }

    private func labeled(_ title: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).nkLabel().foregroundStyle(NK.textTertiary)
            Text(text).font(NKFont.body).foregroundStyle(NK.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - 首选成员块的刻度条(P3.4「默认层 ③」专用)
//
// 🔴 **按价格线性映射,⛔ 不代表任何概率或建议**(同 `NKStopScale`/`NKStopMiniBar`
// 既有纪律)——红段 = 失效位以下、蓝段 = 入场区间、黑刻度 = 现价。与持仓页
// `NKStopScale` 是**同一套读法**、但形状不同(这里画的是一个**区间**而非单点成本),
// 故另起一个轻量组件,不强行复用止损尺(那是给"止损点 + 成本点"设计的两点几何)。
struct NKMemberScaleBar: View {
    let invalidation: Double
    let entryLow: Double
    let entryHigh: Double
    /// 现价。`0`(或更小)= 本次拉不到 → 不画黑刻度(同项目一贯"没有 ≠ 0"纪律)。
    let price: Double

    private var domain: (lo: Double, hi: Double) {
        var vs = [invalidation, entryLow, entryHigh]
        if price > 0 { vs.append(price) }
        let lo = vs.min() ?? 0
        let hi = vs.max() ?? 1
        let span = max(hi - lo, 0.0001)
        let pad = span * 0.12
        return (lo - pad, hi + pad)
    }

    private func x(_ v: Double, _ w: CGFloat) -> CGFloat {
        let d = domain
        let t = (v - d.lo) / max(d.hi - d.lo, 0.0001)
        return w * CGFloat(min(max(t, 0), 1))
    }

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            ZStack(alignment: .topLeading) {
                Capsule().fill(NK.hairline.opacity(0.7)).frame(width: w, height: 5).offset(y: 1)
                // 红段:定义域左端 → 失效位。
                Capsule().fill(NK.down.opacity(0.32))
                    .frame(width: max(x(invalidation, w), 0), height: 5).offset(y: 1)
                // 蓝段:入场区间。
                let ex = x(entryLow, w), eh = x(entryHigh, w)
                Capsule().fill(NK.accent.opacity(0.45))
                    .frame(width: max(eh - ex, 0), height: 5)
                    .offset(x: ex, y: 1)
                if price > 0 {
                    RoundedRectangle(cornerRadius: 1.5).fill(NK.textPrimary)
                        .frame(width: 3, height: 11)
                        .offset(x: x(price, w) - 1.5, y: -2)
                }
            }
            .frame(width: w, height: 9, alignment: .topLeading)
        }
        .frame(height: 9)
        .padding(.horizontal, 2)
    }
}
