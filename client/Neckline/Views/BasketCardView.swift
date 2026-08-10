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
                driverCard(card)                    // ①② 共同驱动 / 证据链
                whyNowCard(card)                    // ③ 为什么是现在
                membersSection(card)                // ④ 成员、角色与对拍分歧
                scriptsCard(card)                   // ⑥ 次日强 / 平 / 弱三剧本
                verificationCard(card)              // ⑦⑧ 验证 / 失效条件
                risksCard(card)                     // ⑨ 主要风险
                narrativeCard(card)                 // ⑩ LLM 叙述(原文整段)
                closingCard(card)                   // ⑪ disclaimer + Tier 红线句
                auditSection(card)                  // 审计视图(原始件下沉)
            }
        } else {
            NKCard {
                NKEmptyState(title: basket.cardUnavailableText ?? "本篮的卡还没生成",
                             subtitle: "⛔ 这不是「篮子不存在」——篮子在,只是卡还没生成。",
                             systemImage: "doc.badge.clock")
            }
            // 卡未就绪时,机械侧结果照常有效 —— 说清缺的是哪半份。
            NKCard {
                VStack(alignment: .leading, spacing: 6) {
                    Text("缺的是 LLM 那半份").font(NKFont.headline).foregroundStyle(NK.textPrimary)
                    Text("驱动、成员、六关、打分这些机械侧的结果照常有效,缺的是:证据链、"
                         + "三剧本、验证与失效条件、逐只成员的位置关 / 核心关判定。")
                        .font(NKFont.body).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            gatesCard
            membersFallback
        }
    }

    // MARK: - 标题块

    @ViewBuilder
    private func titleBlock(_ card: BasketCard) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                if let t = card.tier {
                    NKChip(text: "T\(t)", tone: t == 1 ? .good : (t == 2 ? .warn : .neutral),
                           filled: true)
                }
                if let r = card.rankInTier, let t = card.tier { NKChip(text: "T\(t) 第 \(r) 位") }
                if let v = card.version { NKChip(text: "卡 v\(v)") }
                // V2.2-③-E 引擎徽标(裁定 #9:单篮子单引擎,成员继承 —— 徽标只出
                // 现在篮子头上,⛔ 不在成员卡上重复画)。老卡缺这三键是常态。
                // ⚠ 文案刻意写「选股引擎」而不是裸「引擎」:审计视图里已有
                // `engineApiVersion`(契约版本号)占用了「引擎 N」这个措辞,两个是
                // 完全不同的概念,同页出现容易撞名。
                if let ev = basket.engineVersionDisplay {
                    NKChip(text: "选股引擎 \(ev)", tone: .neutral)
                }
                Spacer()
                VerificationBadge(model: model, basketId: basket.basketId)
            }
            #if os(macOS)
            Text(basket.name.isEmpty ? basket.basketKey : basket.name)
                .font(NKFont.title1).foregroundStyle(NK.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            #endif
            if !card.driver.isEmpty {
                Text(card.driver).font(NKFont.body).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - 打分卡(V2.1-④ 百分制,**纯展示层**)

    @ViewBuilder
    private func scoreCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                BasketScoreCard(percent: basket.scoreDisplayPercent,
                                contributions: basket.scoreDisplayContributions,
                                compact: false)
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

    @ViewBuilder
    private func driverCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                if !card.driver.isEmpty {
                    labeled("① 共同驱动", card.driver)
                }
                if !card.driverKind.isEmpty {
                    Text("驱动类型:\(card.driverKind)").font(NKFont.caption)
                        .foregroundStyle(NK.textTertiary)
                }
                Divider().overlay(NK.hairline)
                HStack(spacing: 6) {
                    Text("② 证据链").nkLabel().foregroundStyle(NK.textTertiary)
                    // ⛔ `evidenceStatus != ok` 必须显式标注"取证不完整",不许静默当完整证据展示。
                    if let note = card.evidenceIncompleteNote {
                        Text(note).font(NKFont.caption).fontWeight(.semibold)
                            .foregroundStyle(NK.amber)
                    }
                    Spacer()
                }
                if card.evidence.isEmpty {
                    Text("本卡未附证据条目").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                } else {
                    ForEach(card.evidence) { e in
                        VStack(alignment: .leading, spacing: 1) {
                            Text("· \(e.claim)").font(NKFont.body).foregroundStyle(NK.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                            Text([e.source, e.date].filter { !$0.isEmpty }.joined(separator: " · "))
                                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func whyNowCard(_ card: BasketCard) -> some View {
        if !card.whyNow.isEmpty {
            NKCard { labeled("③ 为什么是现在", card.whyNow) }
        }
    }

    // MARK: - ④ 成员、角色与对拍分歧(**一等对象**)

    @ViewBuilder
    private func membersSection(_ card: BasketCard) -> some View {
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
    }

    /// 卡未就绪时的成员段:**只有代码**。⛔ 「现在没有」不等于「这几只票没通过」。
    @ViewBuilder
    private var membersFallback: some View {
        if !basket.memberCodes.isEmpty {
            NKCard {
                VStack(alignment: .leading, spacing: 4) {
                    Text("成员 \(basket.memberCodes.count) · 卡未就绪时只有代码")
                        .font(NKFont.headline).foregroundStyle(NK.textPrimary)
                    ForEach(basket.memberCodes, id: \.self) { c in
                        Text(c).font(NKFont.callout.monospacedDigit())
                            .foregroundStyle(NK.textSecondary)
                    }
                    Text("名称、角色、位置关 / 核心关判定、三个参考件都随卡一起来 —— "
                         + "现在没有,不等于这几只票没通过。")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: - ⑥ 次日强 / 平 / 弱三剧本(**参考件**)

    @ViewBuilder
    private func scriptsCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                Text("⑥ 次日三剧本").font(NKFont.headline).foregroundStyle(NK.textPrimary)
                if let s = card.scripts, !s.isEmpty {
                    scriptRow("强", s.strong)
                    scriptRow("平", s.flat)
                    scriptRow("弱", s.weak)
                    NKReferenceNote()
                } else {
                    Text(card.scriptsUnavailableReason ?? "本次未生成竞价剧本")
                        .font(NKFont.caption).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    @ViewBuilder
    private func scriptRow(_ title: String, _ text: String?) -> some View {
        if let t = text, !t.trimmingCharacters(in: .whitespaces).isEmpty {
            HStack(alignment: .top, spacing: 8) {
                Text(title).nkLabel().foregroundStyle(NK.textTertiary)
                    .frame(width: 18, alignment: .leading)
                Text(t).font(NKFont.body).foregroundStyle(NK.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
        }
    }

    // MARK: - ⑦⑧ 验证 / 失效条件(结构化机器半份 + 人话半份)

    @ViewBuilder
    private func verificationCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                Text("⑦⑧ 验证与失效条件").font(NKFont.headline).foregroundStyle(NK.textPrimary)
                if let t = card.verificationText, !t.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("⑦ 验证条件").nkLabel().foregroundStyle(NK.textTertiary)
                        Text(t).font(NKFont.body).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if let t = card.invalidationText, !t.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("⑧ 失效条件").nkLabel().foregroundStyle(NK.textTertiary)
                        Text(t).font(NKFont.body).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                // CLAUDE.md 坑条:篮子 `falsified` ≠ 持仓该走。
                Text("失效说的是「这个驱动假设不成立了」,**不是**「手里的仓该卖了」——"
                     + "该不该走由持仓纪律(止损 / 回落止盈 / 时间退出)管。")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - ⑨ 主要风险

    @ViewBuilder
    private func risksCard(_ card: BasketCard) -> some View {
        if !card.risks.isEmpty {
            NKCard {
                VStack(alignment: .leading, spacing: 4) {
                    Text("⑨ 主要风险").font(NKFont.headline).foregroundStyle(NK.textPrimary)
                    ForEach(card.risks, id: \.self) { r in
                        Text("· \(r)").font(NKFont.body).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    // MARK: - ⑩ LLM 叙述(**原文整段**,§2.7)

    @ViewBuilder
    private func narrativeCard(_ card: BasketCard) -> some View {
        if !card.narrative.isEmpty || card.degraded {
            NKCard {
                VStack(alignment: .leading, spacing: 6) {
                    Text("⑩ 叙述").font(NKFont.headline).foregroundStyle(NK.textPrimary)
                    if card.degraded {
                        // `degraded=true` = **人话半份缺席、结构化半份照出**(不是"这张卡不可信")。
                        Text("本卡降级:人话半份缺席,上面的结构化内容照常有效")
                            .font(NKFont.caption).foregroundStyle(NK.amber)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if !card.narrative.isEmpty {
                        Text(card.narrative).font(NKFont.body).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    NKDisclosure(summary: "参考、非指令") {
                        Text("参考、非指令 · 不进排序、不进哨兵、不改去留、不加分")
                        if !card.llmStage.isEmpty { Text("生成阶段:\(card.llmStage)") }
                        ForEach(card.notes, id: \.self) { n in
                            Text("· \(n)").fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
    }

    // MARK: - ⑪ disclaimer(**原样透传不改写**)+ Tier 红线句

    @ViewBuilder
    private func closingCard(_ card: BasketCard) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if !card.disclaimer.isEmpty {
                Text(card.disclaimer).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text("Tier / 档内次序 = 注意力优先级,不是收益预测 · T1 ≠ 最会涨 · 终选权在你")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
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
