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
//      回落止盈才是纪律,离场参考只是卡上的一个参考位。
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

    @ViewBuilder
    private var content: some View {
        if let card = basket.card {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                titleBlock(card)                    // 标题 + 徽标 + 验证角标
                scoreCard(card)                     // 百分制打分卡(总分 + 五维)
                gatesCard                           // 🔴 六关判定(宫格)
                driverCard(card)                    // ①②③ 驱动 / 证据链 / 为什么是现在
                membersSection(card)                // ④ 成员、角色与对拍分歧
                upsidePathCard(card)               // ⑥ 预期上涨路径
                verificationCard(card)              // ⑦⑧ 验证 / 失效条件 + ⑨ 主要风险
                narrativeCard(card)                 // ⑩ 叙述 + ⑪ disclaimer(收进披露区)
                auditSection(card)                  // 审计视图(原始件下沉)
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

    // MARK: - 打分卡(V2.1-④ 百分制,**纯展示层**)

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

    // MARK: - ①② 驱动 / 证据链

    /// ①②③ **一张卡三段**(原型 340–362 行:`gap:14` + 两条 `.5px` 分隔)。
    /// ⛔ 别拆回三张卡 —— 这三段答的是同一个问题(这条驱动是什么 / 凭什么信 / 为什么是今天)。
    @ViewBuilder
    private func driverCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 14) {
                if !card.driver.isEmpty || !card.driverKind.isEmpty {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("① 共同驱动").nkLabel().foregroundStyle(NK.textTertiary)
                        Text(driverText(card)).font(NKFont.body).lineSpacing(4)
                            .foregroundStyle(NK.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Divider().overlay(NK.hairline)
                VStack(alignment: .leading, spacing: 7) {
                    HStack(spacing: 8) {
                        Text("② 证据链").nkLabel().foregroundStyle(NK.textTertiary)
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
                if !card.whyNow.isEmpty {
                    Divider().overlay(NK.hairline)
                    VStack(alignment: .leading, spacing: 5) {
                        Text("③ 为什么是现在").nkLabel().foregroundStyle(NK.textTertiary)
                        Text(card.whyNow).font(NKFont.body).lineSpacing(4)
                            .foregroundStyle(NK.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
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
                    Text("失效说的是「这个驱动假设不成立了」,**不是**「手里的仓该卖了」——该不该走由持仓纪律(止损 / 回落止盈 / 时间退出)管。")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !card.risks.isEmpty {
                    Divider().overlay(NK.hairline)
                    VStack(alignment: .leading, spacing: 6) {
                        Text("⑨ 主要风险").nkLabel().foregroundStyle(NK.textTertiary)
                        Text(card.risks.joined(separator: ";"))
                            .font(NKFont.body).lineSpacing(4).foregroundStyle(NK.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
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

    // MARK: - ⑩ LLM 叙述(**原文整段**,§2.7)+ ⑪ disclaimer 收进披露区

    /// ⚠ **恒在**:⑪ disclaimer 与 Tier 红线句住在这张卡的披露区里 —— 卡有没有叙述,
    /// 那两句都得说得出口。`narrative` 为空时如实写「本卡未附叙述」,⛔ 不整张卡消失。
    @ViewBuilder
    private func narrativeCard(_ card: BasketCard) -> some View {
        NKCard {
                VStack(alignment: .leading, spacing: 8) {
                    Text("⑩ 叙述").nkLabel().foregroundStyle(NK.textTertiary)
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
                    // 🔴 ⑪ 与 Tier 红线句在原型里就住在这个披露区(macOS 原型 683–687),
                    // ⛔ 不是卡外面另起一段小灰字。文案一字未改。
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
        }
    }

    // MARK: - 审计视图(**原始件下沉**:口径指纹 / 验证条件集 / 机械读数原始件)

    @ViewBuilder
    private func auditSection(_ card: BasketCard) -> some View {
        NKAuditSection(contains: "口径指纹、验证条件集、机械读数原始件") {
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
