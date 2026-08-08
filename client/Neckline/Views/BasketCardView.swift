//
//  BasketCardView.swift
//  Neckline — **篮子卡(蓝图 4.6 十一项)+ 成员卡**(V2-⑮)。
//
//  数据来自报告快照里冻结的 `card_json`(**D0 冻结件**,不是实时的);验证状态角标
//  另走 `GET /baskets/{id}/verification`(**实时**)。
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
        NavigationStack {
            ScrollView {
                content.padding(NKSpace.pagePad)
            }
            .background(platformBg)
            .navigationTitle(basket.name.isEmpty ? basket.basketKey : basket.name)
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { model.dismissBasket() }
                }
            }
        }
    }

    private var platformBg: Color {
        #if os(iOS)
        NK.pageBgIOS
        #else
        NK.pageBg
        #endif
    }

    @ViewBuilder
    private var content: some View {
        if let card = basket.card {
            VStack(alignment: .leading, spacing: NKSpace.gap) {
                headerCard(card)                    // ①② 名称 / 共同驱动 / 证据
                whyNowCard(card)                    // ③ 为什么是现在
                membersSection(card)                // ④ 成员、角色与对拍分歧
                tierCard(card)                      // ⑤ Tier 及分层理由
                scriptsCard(card)                   // ⑥ 次日强 / 平 / 弱三剧本
                verificationCard(card)              // ⑦⑧ 验证 / 失效条件
                risksCard(card)                     // ⑨ 主要风险
                narrativeCard(card)                 // ⑩ LLM 叙述(原文整段)
                disclaimerCard(card)                // ⑪ disclaimer(原样透传)
                fingerprintCard(card)               // 口径指纹 + 纪律标签
            }
        } else {
            NKCard {
                NKEmptyState(title: basket.cardUnavailableText ?? "本篮的卡还没生成",
                             subtitle: "⛔ 这不是「篮子不存在」——篮子在,只是卡还没生成。",
                             systemImage: "doc.badge.clock")
            }
        }
    }

    // MARK: - ①② 名称 / 驱动 / 证据

    @ViewBuilder
    private func headerCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    if let t = card.tier {
                        NKChip(text: "T\(t)", tone: t == 1 ? .good : (t == 2 ? .warn : .neutral),
                               filled: true)
                    }
                    if let v = card.version { NKChip(text: "卡 v\(v)") }
                    Spacer()
                    VerificationBadge(model: model, basketId: basket.basketId)
                }
                if !card.driver.isEmpty {
                    labeled("① 共同驱动", card.driver)
                }
                if !card.driverKind.isEmpty {
                    Text("驱动类型:\(card.driverKind)").font(.system(size: 11))
                        .foregroundStyle(NK.textTertiary)
                }
                Divider().overlay(NK.hairline)
                Text("② 证据链").font(.system(size: 11, weight: .bold)).foregroundStyle(NK.textTertiary)
                // ⛔ `evidenceStatus != ok` 必须显式标注"取证不完整",不许静默当完整证据展示。
                if let note = card.evidenceIncompleteNote {
                    Text(note).font(.system(size: 11.5, weight: .semibold)).foregroundStyle(NK.amber)
                }
                if card.evidence.isEmpty {
                    Text("本卡未附证据条目").font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
                } else {
                    ForEach(card.evidence) { e in
                        VStack(alignment: .leading, spacing: 1) {
                            Text("· \(e.claim)").font(.system(size: 12)).foregroundStyle(NK.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                            Text([e.source, e.date].filter { !$0.isEmpty }.joined(separator: " · "))
                                .font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
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

    // MARK: - ④ 成员、角色与对拍分歧

    @ViewBuilder
    private func membersSection(_ card: BasketCard) -> some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "④ 成员 \(card.members.count)",
                            trailing: card.roleConflicts.isEmpty ? nil
                                : "角色分歧 \(card.roleConflicts.count) 只")
            if card.members.isEmpty {
                NKCard { NKEmptyState(title: "本卡未列成员", systemImage: "person.2") }
            } else {
                ForEach(card.members) { m in
                    BasketMemberCard(model: model, member: m, basketName: card.name,
                                     tradeDate: card.tradeDate.isEmpty ? model.report.tradeDate
                                                                       : card.tradeDate)
                }
            }
        }
    }

    // MARK: - ⑤ Tier 及分层理由

    @ViewBuilder
    private func tierCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                Text("⑤ Tier 及分层理由").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(NK.textPrimary)
                // §2.8-C 红线:Tier = 注意力优先级,不是收益预测。
                Text("Tier = 注意力优先级,不是收益预测;排第一 ≠ 最会涨")
                    .font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                HStack(spacing: 6) {
                    if let t = card.tier { NKChip(text: "T\(t)") }
                    if let r = card.rankInTier { NKChip(text: "档内第 \(r) 位") }
                    if let rm = card.rankMech { NKChip(text: "机械序 #\(rm)") }
                    if let s = card.mechScore { NKChip(text: String(format: "机械分 %.2f", s)) }
                    Spacer()
                }
                if let reason = card.tierReason, !reason.isEmpty {
                    Text(reason).font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let note = card.tierNote, !note.isEmpty {
                    Text(note).font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                    NKReferenceNote()
                }
                // V2.1-④ 百分制打分卡(**纯展示层**:机械分 ×100 + 五维贡献拆解)。
                // ⚠ 它与下面的 `tierBreakdown` 原始表**不是重复**:那张表是冻结留痕的
                // 原样键值(审计用),这张卡是同一份数据的人读换算(理解用)。
                Divider().overlay(NK.hairline)
                BasketScoreCard(percent: basket.scoreDisplayPercent,
                                contributions: basket.scoreDisplayContributions,
                                compact: false)
                if let obj = card.tierBreakdown.objectValue, !obj.isEmpty {
                    Text("五维分项(维度名与现役包权重键逐字对应)")
                        .font(.system(size: 10.5, weight: .bold)).foregroundStyle(NK.textTertiary)
                    NKJSONTable(value: card.tierBreakdown)
                }
                if let th = basket.tierHistory {
                    HStack(spacing: 6) {
                        if th.llmRankDelta != 0 {
                            NKChip(text: "LLM 微调 \(th.llmRankDelta > 0 ? "+" : "")\(th.llmRankDelta) 位",
                                   tone: .warn)
                        }
                        if let pv = th.packVersion { NKChip(text: pv) }
                        Spacer()
                    }
                    if let r = th.llmReason, !r.isEmpty {
                        Text("微调理由:\(r)").font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                        NKReferenceNote()
                    }
                }
            }
        }
    }

    // MARK: - ⑥ 次日强 / 平 / 弱三剧本(**参考件**)

    @ViewBuilder
    private func scriptsCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                Text("⑥ 次日三剧本").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(NK.textPrimary)
                if let s = card.scripts, !s.isEmpty {
                    scriptRow("强势开盘", s.strong)
                    scriptRow("平开", s.flat)
                    scriptRow("低开 / 走弱", s.weak)
                    NKReferenceNote()
                } else {
                    Text(card.scriptsUnavailableReason ?? "本次未生成竞价剧本")
                        .font(.system(size: 11.5)).foregroundStyle(NK.amber)
                }
            }
        }
    }

    @ViewBuilder
    private func scriptRow(_ title: String, _ text: String?) -> some View {
        if let t = text, !t.trimmingCharacters(in: .whitespaces).isEmpty {
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.system(size: 10.5, weight: .bold)).foregroundStyle(NK.textTertiary)
                Text(t).font(.system(size: 12)).foregroundStyle(NK.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - ⑦⑧ 验证 / 失效条件(结构化机器半份 + 人话半份)

    @ViewBuilder
    private func verificationCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                Text("⑦ 验证条件").font(.system(size: 13, weight: .semibold)).foregroundStyle(NK.textPrimary)
                if let t = card.verificationText, !t.isEmpty {
                    Text(t).font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let obj = card.verificationSpec.objectValue, !obj.isEmpty {
                    NKJSONTable(value: card.verificationSpec)
                }
                Divider().overlay(NK.hairline)
                Text("⑧ 失效条件").font(.system(size: 13, weight: .semibold)).foregroundStyle(NK.textPrimary)
                if let t = card.invalidationText, !t.isEmpty {
                    Text(t).font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let obj = card.invalidationSpec.objectValue, !obj.isEmpty {
                    NKJSONTable(value: card.invalidationSpec)
                }
                // CLAUDE.md 坑条:篮子 `falsified` ≠ 持仓该走。
                Text("失效说的是「这个驱动假设不成立了」,**不是**「手里的仓该卖了」——"
                     + "该不该走由持仓纪律(止损 / 回落止盈 / 时间退出)管。")
                    .font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
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
                    Text("⑨ 主要风险").font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(NK.textPrimary)
                    ForEach(card.risks, id: \.self) { r in
                        Text("· \(r)").font(.system(size: 12)).foregroundStyle(NK.textSecondary)
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
                    Text("⑩ 分析师叙述").font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(NK.textPrimary)
                    if card.degraded {
                        // `degraded=true` = **人话半份缺席、结构化半份照出**(不是"这张卡不可信")。
                        Text("本卡降级:人话半份缺席,上面的结构化内容照常有效")
                            .font(.system(size: 11.5)).foregroundStyle(NK.amber)
                    }
                    if !card.narrative.isEmpty {
                        Text(card.narrative).font(.system(size: 12.5)).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                        NKReferenceNote()
                    }
                    if !card.llmStage.isEmpty {
                        Text("生成阶段:\(card.llmStage)").font(.system(size: 10))
                            .foregroundStyle(NK.textTertiary)
                    }
                    if !card.notes.isEmpty {
                        ForEach(card.notes, id: \.self) { n in
                            Text("· \(n)").font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                        }
                    }
                }
            }
        }
    }

    // MARK: - ⑪ disclaimer(**原样透传不改写**)

    @ViewBuilder
    private func disclaimerCard(_ card: BasketCard) -> some View {
        if !card.disclaimer.isEmpty {
            NKCard {
                Text(card.disclaimer).font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - 口径指纹 + 纪律标签

    @ViewBuilder
    private func fingerprintCard(_ card: BasketCard) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 6) {
                Text("口径指纹").font(.system(size: 11, weight: .bold)).foregroundStyle(NK.textTertiary)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        if let v = card.fingerprint.charterVersion { NKChip(text: "章程 \(v)") }
                        if let v = card.fingerprint.packVersion { NKChip(text: "选股包 \(v)") }
                        if let v = card.fingerprint.engineApiVersion { NKChip(text: "引擎 \(v)") }
                        if let v = card.fingerprint.verificationRulesetVersion {
                            NKChip(text: "验证条件集 \(v)")
                        }
                        if let v = card.specVersion { NKChip(text: "卡形状 \(v)") }
                    }
                }
                if !card.disciplineLabels.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(card.disciplineLabels, id: \.self) { l in
                                NKChip(text: l, tone: .warn)
                            }
                        }
                    }
                }
            }
        }
    }

    private func labeled(_ title: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.system(size: 11, weight: .bold)).foregroundStyle(NK.textTertiary)
            Text(text).font(.system(size: 13)).foregroundStyle(NK.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - 成员卡(角色 / 对拍分歧两说并存 / 三个参考件 / K7 标注件 / 信息卡入口)

struct BasketMemberCard: View {
    @Bindable var model: AppModel
    let member: BasketMember
    let basketName: String
    let tradeDate: String

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top, spacing: 6) {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(member.name).font(NKFont.stockName).foregroundStyle(NK.textPrimary)
                            Text(member.tsCode).font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                            if member.isPrimary { NKChip(text: "主归属", tone: .good) }
                        }
                        roleRow
                    }
                    Spacer()
                    if let rs = member.rsRank {
                        Text("RS #\(rs)").font(.system(size: 11.5).monospacedDigit())
                            .foregroundStyle(NK.textSecondary)
                    }
                }
                if !member.reason.isEmpty {
                    Text(member.reason).font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                industryRow
                referenceRow
                tagsRow
                Divider().overlay(NK.hairline)
                HStack(spacing: 14) {
                    Button {
                        model.openInfoCard(tradeDate: tradeDate, code: member.tsCode, name: member.name)
                    } label: {
                        Label("信息卡", systemImage: "chart.xyaxis.line")
                            .font(.system(size: 12, weight: .medium))
                    }
                    .buttonStyle(.plain).foregroundStyle(NK.textSecondary)
                    Spacer()
                    // 动作按钮,不是状态。⛔ 文案不得写成买入建议 —— 这是**补录**用户已在
                    // 券商完成的真实操作(审计台账,系统永不下单)。
                    Button {
                        Task { await model.beginPositionEntryFlow(fromMember: member,
                                                                  basketName: basketName) }
                    } label: {
                        Label(model.circuit.locked ? "熔断中" : "买入补录",
                              systemImage: model.circuit.locked ? "lock.fill" : "square.and.pencil")
                            .font(.system(size: 12.5, weight: .semibold))
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(model.circuit.locked ? NK.textTertiary : NK.accent)
                    .disabled(model.circuit.locked)
                }
            }
        }
    }

    /// **两说并存**:`roleConflict=true` 时两个角色都显示,⛔ 不挑一个当正确答案。
    @ViewBuilder
    private var roleRow: some View {
        if member.roleConflict {
            HStack(spacing: 4) {
                NKChip(text: "LLM:\(member.roleLlm ?? "—")", tone: .warn)
                NKChip(text: "机械:\(member.roleMech ?? "—")", tone: .warn)
                Text("两说并存").font(.system(size: 10)).foregroundStyle(NK.amber)
            }
        } else {
            Text(member.roleDisplay).font(.system(size: 11)).foregroundStyle(NK.textSecondary)
        }
    }

    @ViewBuilder
    private var industryRow: some View {
        if member.industry != nil || member.industryLift != nil {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    if let i = member.industry, !i.isEmpty { NKChip(text: i) }
                    if let lift = member.industryLift {
                        NKChip(text: String(format: "行业 lift %.2f", lift))
                    }
                    if let k4 = member.k4Tag, !k4.isEmpty { NKChip(text: k4, tone: .warn) }
                    if let pr = member.primaryReason, !pr.isEmpty { NKChip(text: pr) }
                }
            }
        }
    }

    /// 三个参考件。**夹逼拒收时值是 nil 且原因非空 —— ⛔ 不许显示成 0 或空白**。
    @ViewBuilder
    private var referenceRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            refLine(title: "建仓观察区间",
                    value: member.entryZone?.rangeText,
                    clamp: member.entryZoneClamp,
                    reason: member.entryZoneUnavailableReason,
                    extra: member.entryZone?.why)
            refLine(title: "最高追价",
                    value: member.maxChase.map { "¥\(NKFmt.price($0))" },
                    clamp: member.maxChaseClamp,
                    reason: member.maxChaseUnavailableReason,
                    extra: nil)
            // ⛔ **不许写成「止盈线」**(§2.8-C 语义红线):回落止盈才是纪律。
            refLine(title: "离场参考区间(不是止盈线)",
                    value: member.exitReference?.rangeText,
                    clamp: member.exitReferenceClamp,
                    reason: member.exitReferenceUnavailableReason,
                    extra: nil)
            NKReferenceNote()
        }
    }

    @ViewBuilder
    private func refLine(title: String, value: String?, clamp: String,
                         reason: String?, extra: String?) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 6) {
                Text(title).font(.system(size: 10.5, weight: .bold)).foregroundStyle(NK.textTertiary)
                if let v = value {
                    Text(v).font(.system(size: 12, weight: .medium).monospacedDigit())
                        .foregroundStyle(NK.textPrimary)
                } else {
                    // 「这一项这次没有」——如实说原因,**不是 0、不是空白**。
                    Text(reason ?? (clamp.isEmpty ? "本次不可用" : "本次不可用(\(clamp))"))
                        .font(.system(size: 11.5)).foregroundStyle(NK.amber)
                }
                Spacer()
            }
            if let e = extra, !e.isEmpty {
                Text(e).font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// ⑦-K7 标注件。`text` **已含「参考、非指令」后缀,不改写、不截断**;
    /// `tagsAbsent`(判不了的码)与「判过没命中」是两回事,⛔ 不合并成"没有标注"。
    @ViewBuilder
    private var tagsRow: some View {
        if !member.tags.isEmpty || !member.tagsAbsent.isEmpty {
            VStack(alignment: .leading, spacing: 3) {
                ForEach(member.tags) { t in
                    HStack(alignment: .top, spacing: 5) {
                        NKChip(text: t.label, tone: t.axisTone)
                        Text(t.text).font(.system(size: 10.5)).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if !member.tagsAbsent.isEmpty {
                    Text("判不了的标注:\(member.tagsAbsent.joined(separator: "、"))(数据缺失,**不等于**没命中)")
                        .font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                }
            }
        }
    }
}
