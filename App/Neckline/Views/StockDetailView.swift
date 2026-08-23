//
//  StockDetailView.swift
//  Neckline — 🔴 **个股详情**(选股板块;取代已删的 `BasketCardView` 与 `InfoCardView`)。
//
//  §5.11:个股详情 = **解释层资料 + 日K 评价 + 完整预案 + 预案修改入口**。
//
//  **诚实披露纪律**:
//   1. **三段各自可能缺席,各自如实标**:
//      · 资料 `nil` = 那天解释层没跑过 / 这一只没跑成 —— ⛔ 别显示成「这只票没什么可说的」;
//      · 预案 `nil` = 那天没给这一只冻预案 → **明早核对不了它**;
//      · 消息面 `unverified` = **没查成** —— ⛔ 不许显示成「无异常」。
//   2. **上方机械空间(机械、排序用)≠ 第一压力位(LLM、预案用)**(裁定 1)——
//      本页刻意把两者**分成两块**呈现,名字各写各的,⛔ 永不互相顶替。
//   3. **预案是 append-only 的**:改一次 = 新版本,老版本一个字不动 —— 版本历史看得见。
//   4. **骨架不可改**:用户能改的是方括号里的**数**,不是「哪个量跟谁比」(K9 §6.4)。
//      要填哪几个数由**服务端下发**(`playbookSlots`),⛔ 客户端不硬编一份键表。
//

import SwiftUI

struct StockDetailView: View {
    @Bindable var model: AppModel

    var body: some View {
        #if os(iOS)
        NavigationStack {
            ScrollView { content.padding(NKSpace.pagePad) }
                .background(NK.pageBgIOS)
                .navigationTitle(titleText)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("关闭") { model.dismissStockDetail() }
                    }
                }
        }
        #else
        content
        #endif
    }

    private var titleText: String {
        model.stockDetail?.entry.name ?? model.stockDetailCode ?? "个股详情"
    }

    @ViewBuilder
    private var content: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            if model.stockDetailLoading && model.stockDetail == nil {
                ProgressView().frame(maxWidth: .infinity).padding(.vertical, 40)
            } else if let err = model.stockDetailError {
                NKEmptyState(title: "没取到这一只的详情", subtitle: err,
                             systemImage: "exclamationmark.triangle")
            } else if let detail = model.stockDetail {
                identityCard(detail)
                explainCard(detail)
                playbookCard(detail)
                versionsCard(detail)
            } else {
                NKDetailPlaceholderCompat(title: "选一只票来看")
            }
        }
        .sheet(isPresented: Binding(get: { model.showPlaybookEditor },
                                    set: { model.showPlaybookEditor = $0 })) {
            PlaybookEditorSheet(model: model)
        }
    }

    // MARK: - 身份 + 形态标注 + 上方机械空间

    private func identityCard(_ d: K9StockDetail) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(d.entry.name ?? d.tsCode).font(NKFont.title3)
                        .foregroundStyle(NK.textPrimary)
                    Text(d.tsCode).font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
                    Spacer(minLength: 6)
                }
                NKWrapRow(spacing: 5, lineSpacing: 5) {
                    ForEach(d.entry.patterns, id: \.self) { p in
                        NKChip(text: nkPatternLabel(p),
                               tone: p == d.entry.primaryPattern ? .info : .neutral,
                               filled: p == d.entry.primaryPattern)
                    }
                    if let n = d.entry.swL2Name, !n.isEmpty { NKChip(text: n, tone: .neutral) }
                }
                if let stock = model.selection.stocks.first(where: { $0.tsCode == d.tsCode }) {
                    HStack(spacing: 6) {
                        Text("收盘价（截至行情日）").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        if let close = stock.referenceClose {
                            Text(NKFmt.price(close)).font(NKFont.monoValue)
                                .foregroundStyle(NK.textPrimary)
                        } else {
                            Text("资料暂未保存")
                                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        }
                    }
                    .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: - 解释层资料 + 日K 评价

    private func explainCard(_ d: K9StockDetail) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                NKSectionHeader(title: "解释层资料")
                if let e = d.explain {
                    if !e.llmOk {
                        // ⛔ 「跑了但没跑成」不许显示成空白 —— 空白读起来像「没什么可说的」。
                        Text("这只股票的资料暂未完整生成，以下内容可能不全。")
                            .font(NKFont.callout).foregroundStyle(NK.amber)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    ForEach(e.profileRows, id: \.label) { row in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.label).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                            Text(row.text).font(NKFont.body).foregroundStyle(NK.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if let k = e.klineComment, !k.isEmpty {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("日K 形态评价").font(NKFont.caption)
                                .foregroundStyle(NK.textTertiary)
                            Text(k).font(NKFont.body).foregroundStyle(NK.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    // 🔴 消息面三态。`unverified` 单独占一格,⛔ 不许折成「无异常」。
                    HStack(spacing: 6) {
                        NKChip(text: nkNewsStateLabel(e.newsState),
                               tone: nkNewsStateTone(e.newsState))
                        if let c = e.newsCategory, !c.isEmpty {
                            NKChip(text: c, tone: .bad)
                        }
                    }
                    if e.newsState == "unverified" {
                        Text("消息面暂未核实：这不代表没有消息，也不代表存在问题。")
                            .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let obj = e.news.objectValue, !obj.isEmpty {
                        NKDisclosure(summary: "消息面检索留痕 · \(obj.count) 项") {
                            NKJSONTable(value: e.news)
                        }
                    }
                    NKReferenceNote(text: "这份资料用于理解公司与走势，不构成交易建议。")
                } else {
                    Text("这只股票的资料当日未生成，不代表它没有值得了解的信息。")
                        .font(NKFont.body).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: - 完整预案 + 修改入口

    private func playbookCard(_ d: K9StockDetail) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    NKSectionHeader(title: "次日预案")
                    Spacer()
                    if !d.playbookSlots.isEmpty {
                        Button("修改预案") { model.beginPlaybookEdit() }
                            .buttonStyle(.plain)
                            .font(NKFont.callout).foregroundStyle(NK.accent)
                    }
                }
                if let pb = d.playbook {
                    HStack(spacing: 8) {
                        NKChip(text: "v\(pb.version)", tone: .info)
                        NKChip(text: pb.isUserEdited ? "我改过" : "预案层填的",
                               tone: pb.isUserEdited ? .good : .neutral)
                        if !pb.filledAt.isEmpty {
                            Text(NKFmt.timestamp(pb.filledAt)).font(NKFont.caption.monospacedDigit())
                                .foregroundStyle(NK.textTertiary)
                        }
                    }
                    NKStatGrid(columns: 3) {
                        NKStatCell(title: "失效价", value: NKFmt.price(pb.levels.invalidation), tone: .bad)
                        NKStatCell(title: "第一压力位", value: NKFmt.price(pb.levels.firstResistance), tone: .good)
                        NKStatCell(title: "第二压力位", value: NKFmt.price(pb.levels.secondResistance))
                    }
                    branchBlock("成立", pb.confirmBranch, .good)
                    branchBlock("放弃", pb.rejectBranch, .bad)
                    Text("其余:\(pb.defaultBranch)")
                        .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    Text("条件的计算由系统在指定时点完成；这里展示的是供你复核的预案。")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text("这只股票当日没有生成预案，次日无法自动核对。")
                        .font(NKFont.body).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    @ViewBuilder
    private func branchBlock(_ name: String, _ branch: PlaybookBranch?, _ tone: NKAxisTone) -> some View {
        if let b = branch, !b.all.isEmpty {
            VStack(alignment: .leading, spacing: 3) {
                Text(name).font(NKFont.callout).fontWeight(.semibold).foregroundStyle(tone.color)
                ForEach(b.all) { cond in
                    Text("· \(cond.text)").font(NKFont.callout.monospacedDigit())
                        .foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// 全部版本(升序)。**append-only 的证据**:改过几次、每次改了什么,这里看得见。
    @ViewBuilder
    private func versionsCard(_ d: K9StockDetail) -> some View {
        if d.playbookVersions.count > 1 {
            NKCard {
                VStack(alignment: .leading, spacing: 8) {
                    NKSectionHeader(title: "预案版本历史",
                                    trailing: "每次修改都会保留旧版本")
                    ForEach(d.playbookVersions, id: \.version) { pb in
                        HStack(spacing: 8) {
                            NKChip(text: "v\(pb.version)",
                                   tone: pb.version == d.playbook?.version ? .info : .neutral)
                            Text(pb.isUserEdited ? "我改的" : "预案层填的")
                                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                            Spacer(minLength: 6)
                            Text("压 \(NKFmt.price(pb.levels.firstResistance)) / 失 \(NKFmt.price(pb.levels.invalidation))")
                                .font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
                        }
                    }
                }
            }
        }
    }
}

// MARK: - 预案修改弹层

/// 🔴 **要填哪几个数由服务端下发**(`playbookSlots`)—— 客户端⛔ 不硬编键表。
/// 🔴 **append-only**:提交 = 存一个新版本,原冻结版本一个字不改。
/// ⚠ **⛔ 不替用户补 0**:没填成数字的项当场拦下来说清是哪几项,
/// 补一个 0 发出去等于把一个他没填的数冻进明天的核对条件。
struct PlaybookEditorSheet: View {
    @Bindable var model: AppModel

    private var slots: [PlaybookSlot] { model.stockDetail?.playbookSlots ?? [] }

    var body: some View {
        NKSheetShell(title: "修改预案",
                     primaryTitle: "存新版",
                     primaryDisabled: slots.isEmpty || model.playbookSubmitting,
                     onCancel: { model.showPlaybookEditor = false },
                     onPrimary: { Task { await model.submitPlaybookEdit() } }) {
            if slots.isEmpty {
                Text("这只票的形态没有可改的数值位。")
                    .font(NKFont.body).foregroundStyle(NK.textSecondary)
            } else {
                Text("你可以调整数值；保存后会保留旧版本，方便回看。")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(slots) { slot in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 6) {
                            Text(slot.label).font(NKFont.callout).fontWeight(.semibold)
                                .foregroundStyle(NK.textPrimary)
                            if !slot.unit.isEmpty {
                                Text(slot.unit).font(NKFont.caption)
                                    .foregroundStyle(NK.textTertiary)
                            }
                        }
                        TextField(slot.key, text: Binding(
                            get: { model.playbookDraft[slot.key] ?? "" },
                            set: { model.playbookDraft[slot.key] = $0 }))
                            .textFieldStyle(.roundedBorder)
                            .font(NKFont.body.monospacedDigit())
                        if !slot.hint.isEmpty {
                            Text(slot.hint).font(NKFont.caption)
                                .foregroundStyle(NK.textTertiary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                Text("每一项都需要填写数字，系统不会替你补值。")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        #if os(macOS)
        .frame(minWidth: 460, minHeight: 420)
        #endif
    }
}

/// macOS 详情栏的空态在 `RootView` 里(`NKDetailPlaceholder`);iOS 没有那个容器,
/// 这里给一个双端都能用的薄壳,⛔ 不为一句空态在两处各写一份。
struct NKDetailPlaceholderCompat: View {
    let title: String
    var body: some View {
        NKEmptyState(title: title, systemImage: "sidebar.left")
    }
}
