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
#if os(macOS)
import AppKit
#else
import UIKit
#endif

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
        coverageSection
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
        if !model.selection.copyText.isEmpty {
            NKDisclosure(summary: "完整资料与预案") {
                VStack(alignment: .leading, spacing: 10) {
                    Text("展开阅读完整资料，或复制到聊天框继续讨论。")
                        .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    Text(model.selection.copyText)
                        .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    Button("复制完整资料") { NKClipboard.copy(model.selection.copyText) }
                        .buttonStyle(.bordered)
                }
            }
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
                        Text("报告日 \(NKFmt.reportDate(model.selection.reportDate))")
                    }
                    if !model.selection.tradeDate.isEmpty {
                        Text("行情截至 \(NKFmt.reportDate(model.selection.tradeDate))")
                    }
                }
                .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                if model.selectionOffline {
                    Text("离线浏览 · 显示本机最近保存的报告")
                        .font(NKFont.caption).foregroundStyle(NK.amber)
                }
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
                    Text("方向解读暂未生成。")
                        .font(NKFont.body).foregroundStyle(NK.textSecondary)
                }
                NKReferenceNote(text: "这是市场背景，供你理解当天环境，不是交易建议。")
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

    @ViewBuilder
    private var coverageSection: some View {
        if let coverage = model.selection.coverage {
            NKCard {
                VStack(alignment: .leading, spacing: 8) {
                    NKSectionHeader(title: "昨日清单覆盖情况")
                    if let value = coverage["coverage_all"]?.doubleValue {
                        Text("昨日清单覆盖了今天走强股票中的 \(NKFmt.ratioPct(value))")
                            .font(NKFont.body).foregroundStyle(NK.textPrimary)
                    } else {
                        Text("暂无法核对前一日清单。")
                            .font(NKFont.body).foregroundStyle(NK.textSecondary)
                    }
                }
            }
        }
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
                    coverageSection
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

private enum NKClipboard {
    static func copy(_ text: String) {
        #if os(macOS)
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        #else
        UIPasteboard.general.string = text
        #endif
    }
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
            if let profile = stock.oneLineProfile, !profile.isEmpty {
                Text(profile).font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .lineLimit(compact ? 2 : 3).fixedSize(horizontal: false, vertical: true)
            } else {
                Text("资料暂未生成").font(NKFont.callout).foregroundStyle(NK.textTertiary)
            }
            HStack(spacing: 6) {
                Text("收盘价（截至行情日）").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                Text(stock.referenceClose.map(NKFmt.price) ?? "资料暂未保存")
                    .font(NKFont.monoValue).foregroundStyle(NK.textPrimary)
            }
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
            Text(stock.displayName).font(NKFont.headline).foregroundStyle(NK.textPrimary)
            Text(stock.tsCode).font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
            Spacer(minLength: 6)
            if let n = stock.swL2Name, !n.isEmpty {
                Text(n).font(NKFont.caption).foregroundStyle(NK.textSecondary)
            }
        }
    }

    /// 默认层只留一个能理解的形态标签；内部排序/档位移到详情资料。
    private var patternChips: some View {
        NKWrapRow(spacing: 5, lineSpacing: 5) {
            NKChip(text: nkPatternLabel(stock.primaryPattern), tone: .info, filled: true)
        }
    }

}
