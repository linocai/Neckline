//
//  SelectionView.swift
//  Neckline — 🔴 **选股板块**(三板块之一,裁定 11;取代已删的 `BasketDailyView`)。
//
//  **两个视图**(⛔ 不是两个板块):
//    · **今日清单** —— 三态首行 → 方向背景 → 10-20 只,每只带
//      形态标注 / **上方机械空间** / 三个价位 / 三分支预案摘要;
//    · **次日核对表** —— **已触发放弃 / 待开盘后观察 两段**(⛔ 无「成立」段,裁定 10)。
//  🔴 **9:26–15:00 默认落在核对表,其余时间落在清单**,用户随时可切。
//
//  **诚实披露纪律(本页每一段都受它约束,⛔ 不许退化)**:
//   1. **三态是三句不同的话**:「今天有这些」/「今天没有」/「今天没跑成」。
//      `empty` 是**跑通了、结果是空的**,可以被信任 —— ⛔ 不许画成警告;
//      `notRun` 是**系统没工作**,必须把 `gaps` 逐条列出来,⛔ 不许渲染成「今天没有」。
//   2. **`listingSize == nil` ⛔ 不许显示成 0**。
//   3. **方向背景不参与任何机械决策**(架构 §八)—— 这句话必须写在那一段上,
//      ⛔ 别让它看起来像一条选股依据。
//   4. **上方机械空间 ≠ 第一压力位**(裁定 1):两个量名字分开、⛔ 永不互相顶替。
//      只被 p2 / p4 召回的票**没有**这一项,如实写「本形态不看这一项」,⛔ 不补 0。
//   5. **缺席各自如实标**:没有冻结预案 → **明早核对不了这一只**;
//      消息面 `unverified` → **没查成**,⛔ 不许显示成「无异常」。
//

import SwiftUI

struct SelectionView: View {
    @Bindable var model: AppModel
    #if os(macOS)
    @State private var selectedCode: String? = nil
    #endif

    var body: some View {
        #if os(iOS)
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    modePicker
                    switch model.selectionMode {
                    case .listing: listingContent
                    case .checklist: CheckListView(model: model)
                    }
                }
                .padding(NKSpace.pagePad)
            }
            .background(NK.pageBgIOS)
            .navigationTitle(AppTab.selection.title)
            .toolbar { ToolbarItem(placement: .primaryAction) { NKRefreshPill(model: model) } }
            .sheet(isPresented: Binding(get: { model.stockDetailCode != nil },
                                        set: { if !$0 { model.dismissStockDetail() } })) {
                StockDetailView(model: model)
            }
        }
        .task { await model.ensureLoaded(.selection) }
        #else
        NKSplitLayout {
            listColumn
        } detail: {
            detailColumn
        }
        .task { await model.ensureLoaded(.selection) }
        #endif
    }

    // MARK: - 视图切换(清单 / 核对表)

    private var modePicker: some View {
        VStack(alignment: .leading, spacing: 6) {
            Picker("", selection: Binding(get: { model.selectionMode },
                                          set: { model.selectionMode = $0 })) {
                ForEach(SelectionViewMode.allCases) { m in
                    Label(m.title, systemImage: m.systemImage).tag(m)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            // 🔴 把「为什么现在默认落在这一个」说出口 —— ⛔ 别让用户猜自动切换的规则。
            Text(model.selectionMode == .checklist
                 ? "9:26–15:00 默认落在核对表 · 昨晚那批票今早哪几只已经可以划掉"
                 : "盘后默认落在清单 · 今天该细看哪几只")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
        }
    }

    // MARK: - 今日清单

    @ViewBuilder
    private var listingContent: some View {
        stateHeadline
        if model.selection.state == .notRun {
            gapsCard
        }
        directionSection
        marketSection
        if model.hasListing {
            NKGroupHeader("清单 · \(model.selection.stocks.count) 只")
            ForEach(model.selection.stocks) { stock in
                Button { model.openStockDetail(code: stock.tsCode) } label: {
                    K9StockRow(stock: stock)
                }
                .buttonStyle(.plain)
            }
        } else if model.selection.state == .empty {
            // 🔴 「今天没有」是一个**可以被信任**的结论 —— 中性说法,⛔ 不画成警告。
            NKEmptyState(title: "今天没有",
                         subtitle: "跑通了、四通道一只都没召回。这是一个可以被信任的结论,不是故障。",
                         systemImage: "checkmark.circle")
        }
    }

    /// 三态首行。🔴 **首行即可分辨**(架构 §3.5)—— 服务端给的 `headline` 优先,
    /// 它带着「N 只(严格 a / 放宽 b)」与逐条缺口。
    private var stateHeadline: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Circle().fill(model.selection.tone.color).frame(width: 8, height: 8)
                    Text(model.selection.headlineText)
                        .font(NKFont.title3).foregroundStyle(NK.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 8)
                }
                // 🔴 双日期都写出来(LRN-20260816-001):周日报告两者不同。
                HStack(spacing: 10) {
                    if !model.selection.reportDate.isEmpty {
                        Text("报告日 \(model.selection.reportDate)")
                    }
                    if !model.selection.tradeDate.isEmpty {
                        Text("行情截至 \(model.selection.tradeDate)")
                    }
                }
                .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                // 🔴 `listingSize == nil` ⛔ 不许显示成 0。
                HStack(spacing: 8) {
                    if let n = model.selection.listingSize {
                        NKChip(text: "清单 \(n) 只", tone: .info)
                        if let s = model.selection.strictCount { NKChip(text: "严格 \(s)", tone: .good) }
                        if let r = model.selection.relaxedCount, r > 0 {
                            NKChip(text: "放宽 \(r)", tone: .warn)
                        }
                    } else {
                        NKChip(text: "清单大小尚不可得", tone: .neutral)
                    }
                }
            }
        }
    }

    /// 「今天没跑成」的**逐条缺口**。⛔ 不许折叠、不许合并成一句「系统异常」。
    private var gapsCard: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 6) {
                NKSectionHeader(title: "缺了什么")
                if model.selection.gaps.isEmpty {
                    Text("服务端没有给出缺口清单 —— 这本身就该查。")
                        .font(NKFont.body).foregroundStyle(NK.textSecondary)
                } else {
                    ForEach(Array(model.selection.gaps.enumerated()), id: \.offset) { _, g in
                        Text("· \(g)").font(NKFont.body).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Text("⚠ 「今天没跑成」= 系统没工作,与「今天没有」(跑通了、结果是空)不是一回事。")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// 方向背景。🔴 **不参与筛选、不参与排序、不影响任何机械决策**(架构 §八)——
    /// 这句话必须写在这一段上。
    @ViewBuilder
    private var directionSection: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                NKSectionHeader(title: "方向背景")
                if let d = model.selection.direction {
                    if let s = d["summary"]?.stringValue, !s.isEmpty {
                        Text(nkMarkdown(s)).font(NKFont.body).foregroundStyle(NK.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    ForEach(Array((d["themes"]?.arrayValue ?? []).enumerated()), id: \.offset) { _, t in
                        HStack(alignment: .top, spacing: 6) {
                            Text(t["name"]?.stringValue ?? "").font(NKFont.callout)
                                .fontWeight(.semibold).foregroundStyle(NK.textPrimary)
                            Text(t["reason"]?.stringValue ?? "").font(NKFont.callout)
                                .foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                } else {
                    Text("今日方向解读**未接入**(事实层的 LLM 旁路尚未建)。")
                        .font(NKFont.body).foregroundStyle(NK.textSecondary)
                }
                NKReferenceNote(text: "方向背景只是报告背景 · 不参与筛选、不参与排序、不影响任何机械决策")
            }
        }
    }

    /// 市场事实(涨停分布 / 连板高度 / 炸板率 / 全市场中位涨幅)。
    /// ⚠ **参数未配置的日子照样呈现**(§5.10):它来自已冻结的事实包,与参数无关。
    @ViewBuilder
    private var marketSection: some View {
        if let m = model.selection.market {
            let lm = m["limitMap"]
            NKCard {
                VStack(alignment: .leading, spacing: 12) {
                    NKSectionHeader(title: "市场事实", trailing: "来自当日已冻结的事实包")
                    NKStatGrid {
                        NKStatCell(title: "涨停", value: intText(lm?["limitUpCount"]), tone: .good)
                        NKStatCell(title: "跌停", value: intText(lm?["limitDownCount"]), tone: .bad)
                        NKStatCell(title: "炸板", value: intText(lm?["zabanCount"]),
                                   footnote: lm?["zabanRate"]?.doubleValue.map {
                                       "炸板率 \(NKFmt.ratioPct($0))"
                                   })
                        NKStatCell(title: "连板高度", value: intText(lm?["maxConsecDays"]))
                        NKStatCell(title: "申万二级涨停簇",
                                   value: String((lm?["clusters"]?.arrayValue ?? []).count))
                        NKStatCell(title: "全市场中位涨幅",
                                   value: m["marketMedianRet"]?.doubleValue
                                       .map { NKFmt.signedPct($0) } ?? "—")
                    }
                }
            }
        }
    }

    private func intText(_ v: NKJSON?) -> String {
        guard let n = v?.intValue else { return "—" }   // ⛔ 缺席不写 0
        return String(n)
    }

    // MARK: - macOS 两栏

    #if os(macOS)
    private var listColumn: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            VStack(alignment: .leading, spacing: 8) {
                Text(AppTab.selection.title).font(NKFont.title2).tracking(-0.3)
                    .foregroundStyle(NK.textPrimary)
                modePicker
            }
            .padding(.horizontal, NKSpace.listHeaderExtraH)
            .padding(.bottom, 12)

            switch model.selectionMode {
            case .listing:
                if model.hasListing {
                    ForEach(model.selection.stocks) { stock in
                        NKListRow(selected: selectedCode == stock.tsCode) {
                            selectedCode = stock.tsCode
                            model.openStockDetail(code: stock.tsCode)
                        } content: {
                            K9StockRow(stock: stock, compact: true)
                        }
                    }
                } else {
                    Text(model.selection.headlineText)
                        .font(NKFont.body).foregroundStyle(NK.textSecondary)
                        .padding(.horizontal, NKSpace.listHeaderExtraH)
                        .fixedSize(horizontal: false, vertical: true)
                }
            case .checklist:
                CheckListRail(model: model)
            }
        }
    }

    @ViewBuilder
    private var detailColumn: some View {
        switch model.selectionMode {
        case .listing:
            if model.stockDetailCode != nil {
                StockDetailView(model: model)
            } else {
                VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    stateHeadline
                    if model.selection.state == .notRun { gapsCard }
                    directionSection
                    marketSection
                    if model.hasListing {
                        NKDetailPlaceholder(title: "左边点一只票看详情",
                                            subtitle: "解释层资料 · 日K 评价 · 完整预案 · 预案修改入口",
                                            systemImage: "sidebar.left")
                    }
                }
            }
        case .checklist:
            CheckListView(model: model)
        }
    }
    #endif
}

// MARK: - 清单上的一行

/// 一只票。§5.11 要求每只带:**形态标注 / 上方机械空间 / 三个价位 / 三分支预案摘要**。
struct K9StockRow: View {
    let stock: K9Stock
    var compact: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            patternChips
            roomAndLevels
            if !compact { branchSummary }
            newsLine
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        // ⚠ compact 形态**不自带卡壳**:macOS 列表栏的选中态由 `NKListRow` 统一给
        // (⛔ 两处各画一套选中语言,必然漂)。
        .padding(.vertical, compact ? 0 : NKSpace.cardPad)
        .padding(.horizontal, compact ? 0 : NKSpace.cardPadH)
        .background(compact ? nil : RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
        .overlay(compact ? nil : RoundedRectangle(cornerRadius: NKRadius.card)
            .stroke(NK.hairline, lineWidth: 0.5))
        .contentShape(Rectangle())
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("\(stock.rank)").font(NKFont.caption.monospacedDigit())
                .foregroundStyle(NK.textTertiary).frame(minWidth: 16, alignment: .trailing)
            Text(stock.displayName).font(NKFont.headline).foregroundStyle(NK.textPrimary)
            Text(stock.tsCode).font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
            Spacer(minLength: 6)
            if let n = stock.swL2Name, !n.isEmpty {
                Text(n).font(NKFont.caption).foregroundStyle(NK.textSecondary)
            }
        }
    }

    /// 形态标注 + 成色 + 席位。🔴 **成色必须看得见**(K9 §五-7):15 只全出自严格档,
    /// 与 10 只里 8 只靠放宽凑上,是两种完全不同的日子。
    private var patternChips: some View {
        NKWrapRow(spacing: 5, lineSpacing: 5) {
            ForEach(stock.patterns, id: \.self) { p in
                NKChip(text: nkPatternLabel(p),
                       tone: p == stock.primaryPattern ? .info : .neutral,
                       filled: p == stock.primaryPattern)
            }
            NKChip(text: nkTierLabel(stock.tier),
                   tone: stock.tier == "strict" ? .good : .warn)
            NKChip(text: nkSeatKindLabel(stock.seatKind), tone: .neutral)
        }
    }

    /// **上方机械空间**(裁定 1:机械、排序用)+ **三个价位**(LLM、预案用)。
    /// 🔴 两者名字分开、⛔ 永不互相顶替 —— 这一行的排版就是那条铁律的落地。
    private var roomAndLevels: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text("上方机械空间").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                if let pct = stock.upsideRoomMechPct {
                    Text(NKFmt.signedRatioPct(pct))
                        .font(NKFont.monoValue).foregroundStyle(NK.textPrimary)
                } else {
                    // ⛔ 不补 0:「本形态不看它」与「上方没有空间」是两件事。
                    Text("本形态不看这一项").font(NKFont.caption)
                        .foregroundStyle(NK.textTertiary)
                }
            }
            if let pb = stock.playbook {
                HStack(spacing: 12) {
                    levelCell("失效位", pb.levels.invalidation, .bad)
                    levelCell("第一压力位", pb.levels.firstResistance, .good)
                    levelCell("第二压力位", pb.levels.secondResistance, .neutral)
                }
            } else {
                // 🔴 没有预案要**逐只说出来**:明早那两拍核对不了这一只。
                Text("⚠ 没有冻结预案 —— 明早核对不了这一只")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
            }
        }
    }

    private func levelCell(_ title: String, _ v: Double, _ tone: NKAxisTone) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).font(NKFont.caption).foregroundStyle(NK.textTertiary)
            Text(NKFmt.price(v)).font(NKFont.monoValue).foregroundStyle(tone.color)
        }
    }

    /// 三分支预案摘要。⚠ **只列条件,⛔ 不替系统求值** —— 求值在服务端,
    /// 而且只在 D1 那两拍发生(架构 §四:判断已经在 D0 完成并冻结)。
    @ViewBuilder
    private var branchSummary: some View {
        if let pb = stock.playbook {
            VStack(alignment: .leading, spacing: 3) {
                branchLine("成立", pb.confirmBranch, .good)
                branchLine("放弃", pb.rejectBranch, .bad)
                Text("其余:\(pb.defaultBranch)")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }
        }
    }

    @ViewBuilder
    private func branchLine(_ name: String, _ branch: PlaybookBranch?, _ tone: NKAxisTone) -> some View {
        if let b = branch, !b.all.isEmpty {
            HStack(alignment: .top, spacing: 6) {
                Text(name).font(NKFont.caption).fontWeight(.semibold).foregroundStyle(tone.color)
                Text(b.all.map(\.text).joined(separator: " 且 "))
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// 消息面三态。🔴 `unverified` = **没查成**,⛔ 不许显示成「无异常」。
    private var newsLine: some View {
        HStack(spacing: 6) {
            NKChip(text: nkNewsStateLabel(stock.newsState), tone: nkNewsStateTone(stock.newsState))
            if let c = stock.klineComment, !c.isEmpty, !compact {
                Text(c).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    .lineLimit(2).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
