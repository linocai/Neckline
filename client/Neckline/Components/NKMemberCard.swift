//
//  NKMemberCard.swift
//  Neckline — V2.3 视觉升级:成员卡(规范 §05 / §01 决定 05)
//
//  🔴 **个股是一等对象**:这张卡答的唯一问题是「**这只票为什么能进篮子**」——
//  进篮理由 + 位置关 / 核心关判定与读数 + 三个参考件 + K7 标注。
//
//  🔴 **只有标题行是开关**(规范 §05 明写):点开后正文 / 读数 / 按钮都**不**收起卡片。
//  ⛔ 别图省事把整张卡包成一个 `Button` —— 那样正文选不了字、底部两个按钮也点不动。
//
//  🔴 **判定为 nil 时整块不显示**,⛔ 不写「未判定」这种看起来像结论的占位
//  (老卡缺这六键是常态,纯新增字段)。
//
//  🔴 **角色两说并存**:`roleConflict` 时**两个都摆出来**,⛔ 不挑一个当正确答案 ——
//  分歧本身就是信息(机械按 RS 排名判、LLM 按证据判,判据不同)。
//

import SwiftUI

struct NKMemberCard: View {
    @Bindable var model: AppModel
    let member: BasketMember
    let basketName: String
    let tradeDate: String

    /// ⚠ 初值来自 QA 钩子(缺环境变量时恒 `false`),**只改初值**,用户照常可收放。
    @State private var expanded: Bool = NKQA.expandDisclosures

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if expanded {
                VStack(alignment: .leading, spacing: NKSpace.blockGap) {
                    if !member.reason.isEmpty {
                        Text(member.reason).font(NKFont.body).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    roleConflictBlock
                    chipsRow
                    gateCard(title: "位置关 · 落地起跳",
                             label: member.positionVerdictLabel, tone: member.positionVerdictTone,
                             reason: member.positionReason, metrics: member.positionMetrics)
                    gateCard(title: "核心关 · 行业龙头",
                             label: member.coreVerdictLabel, tone: member.coreVerdictTone,
                             reason: member.coreReason, metrics: member.coreMetrics)
                    referenceBlock
                    tagsBlock
                    Divider().overlay(NK.hairline)
                    actionRow
                }
                .padding(.top, NKSpace.blockGap)
            }
        }
        .padding(NKSpace.cardPad)
        .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.hairline, lineWidth: 0.5))
    }

    // MARK: - 收起行(44px):名称 · 代码 · 角色徽标 · 两枚判定徽标 · RS 名次

    /// ⚠ **两枚判定徽标在 iPhone 宽度下必须换行**(V2.3 截图核对逮到):402pt 里塞
    /// 「名称 + 代码 + 角色 + 位置X + 核心X + RS#N + chevron」会把名称挤成两行、
    /// 把徽标压成**竖排单字**(「位 置 合 适」)—— 那已经不是紧凑,是读不出来。
    /// macOS 详情栏 ≥700pt,一行放得下,故只在 iOS 分两行。
    private var header: some View {
        Button {
            withAnimation(.easeInOut(duration: 0.16)) { expanded.toggle() }
        } label: {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 8) {
                    Text(member.name).font(NKFont.title3).foregroundStyle(NK.textPrimary)
                        .lineLimit(1).fixedSize(horizontal: true, vertical: false)
                    Text(member.tsCode).font(NKFont.caption.monospacedDigit())
                        .foregroundStyle(NK.textTertiary).lineLimit(1)
                    roleBadge
                    if member.isPrimary { NKChip(text: "主归属", tone: .good) }
                    #if os(macOS)
                    Spacer(minLength: 6)
                    verdictBadges
                    #else
                    Spacer(minLength: 4)
                    #endif
                    if let rs = member.rsRank {
                        Text("RS #\(rs)").font(NKFont.caption.monospacedDigit())
                            .foregroundStyle(NK.textSecondary).lineLimit(1)
                    }
                    Image(systemName: expanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(NK.textTertiary)
                }
                #if os(iOS)
                if member.positionVerdictLabel != nil || member.coreVerdictLabel != nil {
                    HStack(spacing: 4) { verdictBadges; Spacer(minLength: 0) }
                }
                #endif
            }
            .frame(minHeight: 44 - NKSpace.cardPad, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    /// 角色。**两说并存时收起行只给一枚琥珀「角色两说」** —— 两个角色名并排摆在
    /// 展开区(收起行放不下,而截断成一个就等于替用户挑了答案)。
    @ViewBuilder
    private var roleBadge: some View {
        if member.roleConflict {
            NKChip(text: "角色两说", tone: .warn)
        } else if !member.roleDisplay.isEmpty {
            NKChip(text: member.roleDisplay)
        }
    }

    /// 两枚判定徽标(位置 / 核心)。**各自 nil 时各自不显示**,⛔ 不占位。
    private var verdictBadges: some View {
        HStack(spacing: 4) {
            if let l = member.positionVerdictLabel {
                NKChip(text: "位置 \(l)", tone: member.positionVerdictTone, filled: true)
            }
            if let l = member.coreVerdictLabel {
                NKChip(text: "核心 \(l)", tone: member.coreVerdictTone, filled: true)
            }
        }
    }

    // MARK: - 展开区

    /// 琥珀底块并列摆出机械判与 LLM 判。⛔ 不挑一个当正确答案。
    @ViewBuilder
    private var roleConflictBlock: some View {
        if member.roleConflict {
            VStack(alignment: .leading, spacing: 5) {
                Text("角色两说并存").nkLabel().foregroundStyle(NK.amber)
                HStack(spacing: 16) {
                    labeledValue("机械判", member.roleMech ?? "—")
                    labeledValue("LLM 判", member.roleLlm ?? "—")
                    Spacer(minLength: 0)
                }
                Text("两个都显示,不挑一个当正确答案 —— 判据不同(机械按 RS 排名,LLM 按证据),分歧本身就是信息。")
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
            .background(RoundedRectangle(cornerRadius: NKRadius.inner).fill(NK.amber.opacity(0.10)))
        }
    }

    private func labeledValue(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).nkLabel().foregroundStyle(NK.textTertiary)
            Text(value).font(NKFont.callout).fontWeight(.semibold).foregroundStyle(NK.textPrimary)
        }
    }

    @ViewBuilder
    private var chipsRow: some View {
        let hasAny = (member.industry?.isEmpty == false) || member.industryLift != nil
            || (member.k4Tag?.isEmpty == false) || (member.primaryReason?.isEmpty == false)
        if hasAny {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    if let i = member.industry, !i.isEmpty { NKChip(text: i) }
                    if let lift = member.industryLift {
                        NKChip(text: String(format: "行业 lift %.2f", lift))
                    }
                    if let k4 = member.k4Tag, !k4.isEmpty { NKChip(text: k4, tone: .warn) }
                    if let pr = member.primaryReason, !pr.isEmpty { NKChip(text: pr) }
                }
                .padding(.vertical, 1)
            }
        }
    }

    /// 一张关卡片 = 关名 + 三态实心徽标 + LLM 理由 + 分隔线 + 原始读数。
    /// **判定 nil → 整块不显示**(⛔ 不写「未判定」)。
    @ViewBuilder
    private func gateCard(title: String, label: String?, tone: NKAxisTone,
                          reason: String?, metrics: NKJSON?) -> some View {
        if let l = label {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Text(title).nkLabel().foregroundStyle(NK.textTertiary)
                    NKChip(text: l, tone: tone, filled: true)
                    Spacer(minLength: 0)
                }
                if let r = reason, !r.isEmpty {
                    Text(r).font(NKFont.body).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let m = metrics, let obj = m.objectValue, !obj.isEmpty {
                    Divider().overlay(NK.hairline)
                    NKMetricsGrid(value: m)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
            .background(RoundedRectangle(cornerRadius: NKRadius.inner).fill(NK.fieldBg))
        }
    }

    /// 三个参考件。**夹逼拒收时值是 nil 且原因非空 —— ⛔ 不许显示成 0 或空白**。
    private var referenceBlock: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("三个参考件").nkLabel().foregroundStyle(NK.textTertiary)
            refLine(title: "建仓观察区间", value: member.entryZone?.rangeText,
                    clamp: member.entryZoneClamp, reason: member.entryZoneUnavailableReason,
                    extra: member.entryZone?.why)
            refLine(title: "最高追价", value: member.maxChase.map { "¥\(NKFmt.price($0))" },
                    clamp: member.maxChaseClamp, reason: member.maxChaseUnavailableReason,
                    extra: nil)
            // ⛔ **不许写成「止盈线」**(§2.8-C 语义红线):回落止盈才是纪律。
            refLine(title: "离场参考区间(不是止盈线)", value: member.exitReference?.rangeText,
                    clamp: member.exitReferenceClamp,
                    reason: member.exitReferenceUnavailableReason, extra: nil)
            NKDisclosure(summary: "参考、非指令") {
                Text("参考、非指令 · 不进排序、不进哨兵、不改去留、不加分")
                Text("离场参考不是止盈线 —— 回落止盈才是纪律。")
            }
        }
    }

    @ViewBuilder
    private func refLine(title: String, value: String?, clamp: String,
                         reason: String?, extra: String?) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 6) {
                Text(title).nkLabel().foregroundStyle(NK.textTertiary)
                if let v = value {
                    Text(v).font(NKFont.callout.monospacedDigit()).fontWeight(.semibold)
                        .foregroundStyle(NK.textPrimary)
                } else {
                    // 「这一项这次没有」——如实说原因,**不是 0、不是空白**。
                    Text(reason ?? (clamp.isEmpty ? "本次不可用" : "本次不可用(\(clamp))"))
                        .font(NKFont.caption).foregroundStyle(NK.amber)
                }
                Spacer(minLength: 0)
            }
            if let e = extra, !e.isEmpty {
                Text(e).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// ⑦-K7 标注件。`text` **已含「参考、非指令」后缀,不改写、不截断**;
    /// `tagsAbsent`(判不了的码)与「判过没命中」是两回事,⛔ 不合并成"没有标注"。
    @ViewBuilder
    private var tagsBlock: some View {
        if !member.tags.isEmpty || !member.tagsAbsent.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text("成员标注件 · K7").nkLabel().foregroundStyle(NK.textTertiary)
                ForEach(member.tags) { t in
                    HStack(alignment: .top, spacing: 5) {
                        NKChip(text: t.label, tone: t.axisTone)
                        Text(t.text).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if !member.tagsAbsent.isEmpty {
                    Text("判不了的标注:\(member.tagsAbsent.joined(separator: "、"))(数据缺失,**不等于**没命中)")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var actionRow: some View {
        HStack(spacing: 14) {
            Button {
                model.openInfoCard(tradeDate: tradeDate, code: member.tsCode, name: member.name)
            } label: {
                Label("信息卡", systemImage: "chart.xyaxis.line").font(NKFont.callout)
            }
            .buttonStyle(.plain).foregroundStyle(NK.textSecondary)
            Spacer()
            // 动作按钮,不是状态。⛔ 文案不得写成买入建议 —— 这是**补录**用户已在
            // 券商完成的真实操作(审计台账,系统永不下单)。
            // 🔴 **任何情况下都不灰化**(V2.2-⑤-B / 〇b-7:熔断三件机制整体退役;
            // 退潮刹车激活时同样不灰 —— 硬拦等于帮用户瞒报)。
            Button {
                Task { await model.beginPositionEntryFlow(fromMember: member,
                                                          basketName: basketName) }
            } label: {
                Label("买入补录", systemImage: "square.and.pencil")
                    .font(NKFont.callout).fontWeight(.semibold)
            }
            .buttonStyle(.plain).foregroundStyle(NK.accent)
        }
    }
}

/// 原始读数网格(**等宽键名 + 等宽数值**)。键是服务端语义标识符,
/// ⛔ 客户端不改名、不重算、不猜含义;顺序按字典序**确定性**排列。
struct NKMetricsGrid: View {
    let value: NKJSON

    #if os(macOS)
    private let columns = Array(repeating: GridItem(.flexible(), alignment: .leading), count: 4)
    #else
    private let columns = Array(repeating: GridItem(.flexible(), alignment: .leading), count: 2)
    #endif

    var body: some View {
        LazyVGrid(columns: columns, alignment: .leading, spacing: NKSpace.denseGap) {
            ForEach(value.sortedKeys, id: \.self) { k in
                VStack(alignment: .leading, spacing: 0) {
                    Text(k).font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
                        .lineLimit(1).minimumScaleFactor(0.8)
                    Text(value.objectValue?[k]?.displayText ?? "—")
                        .font(NKFont.monoValue).foregroundStyle(NK.textPrimary)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}
