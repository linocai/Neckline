//
//  ReviewView.swift
//  Neckline — 复盘板块。
//
//  **四页答四个不同的问题,⛔ 不合并**(`ReviewPage`):
//    · **交割单** —— 这周我实际成交了哪些(上传 → 解析结果);
//    · **装订材料** —— 那几笔当时长什么样(K 线 + 买卖点 + 大盘 + 行业 + 当时的报告与预案);
//    · **结论存档** —— 这周复盘得出了什么(append-only,下周可检索);
//    · **我的成绩** —— 我自己做得怎么样(⚠ 与系统的清单成绩 / 覆盖率**完全隔离**)。
//
//  🔴 **这一层无 LLM 调用**(架构 §六 明令)。系统只做三件事:解析 / 装订 / 存档;
//  **好坏结论由用户带着材料到聊天框里得出,再用「结论存档」存回来** —— ⛔ 系统不做对话、
//  ⛔ 不给判断、⛔ 不做任何自动反馈回写选股。
//
//  **诚实披露纪律**:
//   1. 「**没有**」与「**没看**」永远分开渲染:交割单缺席 = 输入只能由用户给、系统查过
//      确实没有(`found == false`)—— 那是**「没有」**,该说的是「去传一份」;
//      拉取失败 = **「没看」**,该说的是「本次没取到」。⛔ 别"统一"成一句「暂无数据」。
//   2. **装订材料的 `gaps` 必须原样呈现**:哪一段没取到、为什么,**是材料的一部分**;
//      ⛔ 客户端不许把它折叠掉(那等于让缺失静默)。
//   3. **装订是「点一下才算」**:它要读 parquet 行情 —— ⛔ 别塞进每次进板块都会拉的
//      聚合读里(§12 坑 1:重活进常驻服务 = 卡死不报错)。
//
//  当前复盘只呈现已经定义的对账和结论内容；未定义的数据不渲染为空壳。
//  一个永远 `available=false` 的段,比没有这个段更让人以为「系统那一步坏了」。
//

import SwiftUI
#if os(macOS)
import AppKit
import UniformTypeIdentifiers
#endif

struct ReviewView: View {
    @Bindable var model: AppModel

    var body: some View {
        #if os(iOS)
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    weekBar
                    pagePicker
                    currentPage
                }
                .padding(NKSpace.pagePad)
            }
            .background(NK.pageBgIOS)
            .navigationTitle(AppTab.review.title)
            .toolbar { ToolbarItem(placement: .primaryAction) { NKRefreshPill(model: model) } }
            .sheet(isPresented: Binding(get: { model.showConclusionEditor },
                                        set: { model.showConclusionEditor = $0 })) {
                ConclusionEditorSheet(model: model)
            }
        }
        .task { await model.ensureLoaded(.review) }
        #else
        NKSplitLayout {
            listColumn
        } detail: {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                weekBar
                currentPage
            }
        }
        .task { await model.ensureLoaded(.review) }
        .sheet(isPresented: Binding(get: { model.showConclusionEditor },
                                    set: { model.showConclusionEditor = $0 })) {
            ConclusionEditorSheet(model: model)
        }
        #endif
    }

    // MARK: - 周切换

    /// 🔴 **必须能翻周**:交割单是周末才传的,周一到周五看"本周"永远是空 ——
    /// 没有翻周入口 = 用户永远看不到上周那份已经解析好的东西。
    private var weekBar: some View {
        NKCard(padding: 12) {
            HStack(spacing: 10) {
                Button { Task { await model.shiftReviewWeek(-1) } } label: {
                    Image(systemName: "chevron.left")
                }
                .buttonStyle(.plain).foregroundStyle(NK.accent)
                VStack(alignment: .leading, spacing: 1) {
                    Text(weekText).font(NKFont.headline).foregroundStyle(NK.textPrimary)
                    if let ov = model.reviewOverview, !ov.weekStart.isEmpty {
                        Text("\(ov.weekStart) – \(ov.weekEnd)")
                            .font(NKFont.caption.monospacedDigit())
                            .foregroundStyle(NK.textTertiary)
                    }
                }
                Spacer(minLength: 6)
                Button { Task { await model.shiftReviewWeek(nil) } } label: { Text("本周") }
                    .buttonStyle(.plain).font(NKFont.callout).foregroundStyle(NK.accent)
                Button { Task { await model.shiftReviewWeek(1) } } label: {
                    Image(systemName: "chevron.right")
                }
                .buttonStyle(.plain).foregroundStyle(NK.accent)
            }
        }
    }

    private var weekText: String {
        // 🔴 ISO 周键的**唯一源是服务端** —— ⛔ 客户端不自己算(跨年那一周必然对不上)。
        model.currentWeekKey.map { "第 \($0) 周" } ?? "本周"
    }

    private var pagePicker: some View {
        Picker("", selection: Binding(get: { model.reviewPage },
                                      set: { model.reviewPage = $0 })) {
            ForEach(ReviewPage.allCases) { p in Text(p.title).tag(p) }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
    }

    @ViewBuilder
    private var currentPage: some View {
        switch model.reviewPage {
        case .reconcile: reconcilePage
        case .bindery: binderyPage
        case .conclusions: conclusionsPage
        case .mine: minePage
        }
    }

    // MARK: - ① 交割单(上传 → 解析结果)

    @ViewBuilder
    private var reconcilePage: some View {
        #if os(macOS)
        uploadCard
        #else
        // ⚠ **必须是单个字面量**:`NKNoteBlock.text` 是 `LocalizedStringKey`,
        // 而 `"a" + "b"` 求值成 `String` —— 编译不过(且这一支只在 iOS 编译,
        // macOS 构建不会覆盖该分支，必须由 iOS 构建与测试覆盖。
        NKNoteBlock(text: "上传交割单是**桌面场景**(要拖文件)—— 在 macOS 端的复盘 · 交割单页做。这里能看到已经解析好的结果。")
        #endif
        warningsCard
        if let entry = model.selectedReviewEntry {
            roundTripsCard(entry)
        } else if let seg = model.reviewOverview?.reconcile {
            // 🔴 「没有」与「没看」分开:`available=true` + `found=false` = **这周没传过**。
            if seg.available {
                NKEmptyState(title: "这周还没上传交割单",
                             subtitle: seg.note ?? "装订与我的成绩都要它 —— 系统补不出没上传的那一份。",
                             systemImage: "tray")
            } else {
                NKEmptyState(title: "本次没取到对账段",
                             subtitle: seg.unavailableReason ?? "网络或鉴权没通 —— ⚠ 这不是「这周没有」。",
                             systemImage: "wifi.exclamationmark")
            }
        } else {
            NKEmptyState(title: "还没拉过复盘", subtitle: "下拉或点刷新去问一次。",
                         systemImage: "arrow.clockwise")
        }
    }

    #if os(macOS)
    private var uploadCard: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                NKSectionHeader(title: "上传交割单", trailing: "支持两家券商的原始导出")
                Text("把本周的券商交割单拖到下面,或点「选择文件」。"
                     + "解析与 FIFO 回合闭合全在服务端做,⛔ 客户端不重算任何一笔。")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                Button { pickFiles() } label: {
                    HStack(spacing: 6) {
                        if model.reviewUploading {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "tray.and.arrow.down")
                        }
                        Text(model.reviewUploading ? "解析中…" : "选择文件")
                    }
                    .font(NKFont.body)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(RoundedRectangle(cornerRadius: NKRadius.control)
                        .fill(NK.accent.opacity(0.10)))
                    .foregroundStyle(NK.accent)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(model.reviewUploading)
            }
        }
        .onDrop(of: [.fileURL], isTargeted: nil) { providers in
            loadDropped(providers); return true
        }
    }

    private func pickFiles() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        if panel.runModal() == .OK {
            let files = panel.urls.compactMap { url -> (String, Data)? in
                guard let d = try? Data(contentsOf: url) else { return nil }
                return (url.lastPathComponent, d)
            }
            Task { await model.uploadReviewFiles(files) }
        }
    }

    /// 拖入的文件。⚠ `loadItem` 的回调可能在**任意线程**上来 —— 收齐之后统一回主 actor
    /// 交给 `AppModel`(它是 `@MainActor`),⛔ 别在回调里直接改状态。
    private func loadDropped(_ providers: [NSItemProvider]) {
        let group = DispatchGroup()
        let lock = NSLock()
        var files: [(filename: String, data: Data)] = []
        for provider in providers {
            group.enter()
            provider.loadDataRepresentation(forTypeIdentifier: UTType.fileURL.identifier) { data, _ in
                defer { group.leave() }
                guard let data,
                      let url = URL(dataRepresentation: data, relativeTo: nil),
                      let payload = try? Data(contentsOf: url) else { return }
                lock.lock()
                files.append((filename: url.lastPathComponent, data: payload))
                lock.unlock()
            }
        }
        group.notify(queue: .main) {
            let picked = files
            guard !picked.isEmpty else { return }
            Task { @MainActor in await model.uploadReviewFiles(picked) }
        }
    }
    #endif

    /// 🔴 **两栏警告刻意分开**:解析层面的问题(文件格式)与 FIFO 数据完整性问题
    /// (卖出找不到匹配买入)要人做的事完全不同,⛔ 不合并成一句。
    @ViewBuilder
    private var warningsCard: some View {
        if !model.reviewParseWarnings.isEmpty || !model.reviewDataWarnings.isEmpty {
            NKCard {
                VStack(alignment: .leading, spacing: 8) {
                    if !model.reviewParseWarnings.isEmpty {
                        warningBlock("解析警告 · 文件格式那一层", model.reviewParseWarnings, .warn)
                    }
                    if !model.reviewDataWarnings.isEmpty {
                        warningBlock("数据完整性警告 · FIFO 那一层", model.reviewDataWarnings, .bad)
                    }
                }
            }
        }
    }

    private func warningBlock(_ title: String, _ items: [String], _ tone: NKAxisTone) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(NKFont.callout).fontWeight(.semibold).foregroundStyle(tone.color)
            ForEach(Array(items.enumerated()), id: \.offset) { _, w in
                Text("· \(w)").font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func roundTripsCard(_ entry: WeeklyReviewEntry) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                NKSectionHeader(title: "解析结果 · FIFO 回合",
                                trailing: "\(entry.result.roundTrips.count) 笔")
                ForEach(entry.result.roundTrips) { rt in
                    HStack(alignment: .top, spacing: 8) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(rt.name.isEmpty ? rt.tsCode : rt.name)
                                .font(NKFont.body).foregroundStyle(NK.textPrimary)
                            Text(rt.tsCode).font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
                        }
                        .frame(width: 96, alignment: .leading)
                        VStack(alignment: .leading, spacing: 1) {
                            Text("买 \(rt.buyDate) @ \(NKFmt.price(rt.buyPrice)) × \(rt.qty)")
                                .font(NKFont.caption.monospacedDigit())
                                .foregroundStyle(NK.textSecondary)
                            if let sd = rt.sellDate, let sp = rt.sellPrice {
                                Text("卖 \(sd) @ \(NKFmt.price(sp))")
                                    .font(NKFont.caption.monospacedDigit())
                                    .foregroundStyle(NK.textSecondary)
                            } else {
                                Text("未平仓").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                            }
                        }
                        Spacer(minLength: 6)
                        if let pnl = rt.netPnl {
                            VStack(alignment: .trailing, spacing: 1) {
                                Text(NKFmt.signedAmount(pnl)).font(NKFont.monoValue)
                                    .foregroundStyle(pnl >= 0 ? NK.up : NK.down)
                                if let p = rt.pnlPct {
                                    Text(NKFmt.signedPct(p)).font(NKFont.caption.monospacedDigit())
                                        .foregroundStyle(NK.textTertiary)
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // MARK: - ② 装订材料(**点一下才算**)

    @ViewBuilder
    private var binderyPage: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                NKSectionHeader(title: "行情材料装订", trailing: "按需生成")
                Text("每笔回合前后的 K 线、买卖点标注、同期大盘与行业，以及当时的报告和预案快照。"
                     + "生成时会读取行情资料，请按需打开。")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                Button { Task { await model.loadBindery() } } label: {
                    HStack(spacing: 6) {
                        if model.binderyLoading { ProgressView().controlSize(.small) }
                        else { Image(systemName: "chart.xyaxis.line") }
                        Text(model.binderyLoading ? "装订中…" : "装订这一周")
                    }
                    .font(NKFont.body)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(RoundedRectangle(cornerRadius: NKRadius.control)
                        .fill(NK.accent.opacity(0.10)))
                    .foregroundStyle(NK.accent)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(model.binderyLoading)
            }
        }
        if let err = model.binderyError {
            NKEmptyState(title: "这次没装订成", subtitle: err,
                         systemImage: "exclamationmark.triangle")
        } else if let b = model.bindery {
            if !b.found {
                NKEmptyState(title: "这周没有可装订的成交",
                             subtitle: b.unavailableReason ?? "装订的输入只能由用户给 —— 先上传交割单。",
                             systemImage: "tray")
            } else if let why = b.unavailableReason {
                NKEmptyState(title: "这周的材料本次没装订成功", subtitle: why,
                             systemImage: "exclamationmark.triangle")
            } else {
                binderyResult(b)
            }
        }
    }

    private func binderyResult(_ b: ReviewBindery) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                NKSectionHeader(title: "本周材料",
                                trailing: "\(NKFmt.reportDate(b.windowStart)) – \(NKFmt.reportDate(b.windowEnd))")
                HStack(spacing: 8) {
                    NKChip(text: "\(b.roundTrips.count) 笔回合", tone: .info)
                    if !b.benchmarkName.isEmpty { NKChip(text: "大盘 \(b.benchmarkName)", tone: .neutral) }
                }
                // 🔴 **缺口原样呈现,⛔ 不许折叠掉** —— 哪一段没取到、为什么,是材料的一部分。
                if !b.gaps.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("材料缺口 · \(b.gaps.count) 条").font(NKFont.callout)
                            .fontWeight(.semibold).foregroundStyle(NK.amber)
                        ForEach(Array(b.gaps.enumerated()), id: \.offset) { _, g in
                            Text("· \(g)").font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                if !b.note.isEmpty {
                    // 服务端写在材料里的那句话(「这是回看材料,不是判断」)。⛔ 不改写、不省略。
                    NKNoteBlock(text: LocalizedStringKey(b.note))
                }
                if !b.markdown.isEmpty {
                    NKDisclosure(summary: "完整材料（可复制到聊天框） · \(b.markdown.count) 字") {
                        Text(nkMarkdown(b.markdown)).font(NKFont.callout)
                            .foregroundStyle(NK.textSecondary)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    // MARK: - ③ 结论存档(append-only)

    @ViewBuilder
    private var conclusionsPage: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    NKSectionHeader(title: "结论存档", trailing: "保留每一版 · 下周可检索")
                    Spacer()
                    Button("写一版") { model.beginConclusion() }
                        .buttonStyle(.plain).font(NKFont.callout).foregroundStyle(NK.accent)
                }
                Text("把材料带到聊天框或笔记里形成自己的结论，再在这里保存。"
                     + "每次保存都会新建一版，过去的版本保持不变。")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let latest = model.conclusions.latest {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 8) {
                            NKChip(text: "v\(latest.version)", tone: .info)
                            Text(latest.title).font(NKFont.headline)
                                .foregroundStyle(NK.textPrimary)
                            Spacer(minLength: 6)
                            Text(NKFmt.timestamp(latest.createdAt)).font(NKFont.caption.monospacedDigit())
                                .foregroundStyle(NK.textTertiary)
                        }
                        Text(latest.body).font(NKFont.body).foregroundStyle(NK.textSecondary)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                        if !latest.tags.isEmpty {
                            NKWrapRow(spacing: 5, lineSpacing: 5) {
                                ForEach(latest.tags, id: \.self) { NKChip(text: $0, tone: .neutral) }
                            }
                        }
                    }
                } else {
                    // ⚠ `latest == nil` = **那周还没写过结论** —— ⛔ 别渲染成「这周没问题」。
                    Text("这周还没有保存结论。")
                        .font(NKFont.body).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        if model.conclusions.versions.count > 1 {
            NKCard {
                VStack(alignment: .leading, spacing: 8) {
                    NKSectionHeader(title: "版本历史",
                                    trailing: "\(model.conclusions.versions.count) 版")
                    ForEach(model.conclusions.versions) { c in
                        HStack(spacing: 8) {
                            NKChip(text: "v\(c.version)",
                                   tone: c.version == model.conclusions.latest?.version ? .info : .neutral)
                            Text(c.title).font(NKFont.body).foregroundStyle(NK.textPrimary)
                            Spacer(minLength: 6)
                            Text(NKFmt.timestamp(c.createdAt)).font(NKFont.caption.monospacedDigit())
                                .foregroundStyle(NK.textTertiary)
                        }
                    }
                }
            }
        }
    }

    // MARK: - ④ 我的成绩(⚠ 与前两条成绩线**完全隔离**)

    @ViewBuilder
    private var minePage: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 12) {
                NKSectionHeader(title: "我的成绩", trailing: "来源 = 交割单")
                Text("这里记录的是你的交易结果，与系统的清单成绩分开计算。"
                     + "是否实际买入与清单表现是两件事，彼此不会混在同一项统计里。")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                if let stats = model.selectedReviewEntry?.result.stats {
                    NKStatGrid {
                        NKStatCell(title: "已平仓", value: String(stats.closedCount),
                                   footnote: "未平 \(stats.openCount) 笔")
                        NKStatCell(title: "胜率", value: NKFmt.ratioPct(stats.winRate))
                        NKStatCell(title: "盈利因子",
                                   value: stats.profitFactor.map { String(format: "%.2f", $0) }
                                       ?? "本周无亏损回合",
                                   footnote: stats.profitFactor == nil
                                       ? "⚠ 数学上是无穷 —— ⛔ 不是 0" : nil)
                        NKStatCell(title: "盈亏比",
                                   value: stats.profitLossRatio.map { String(format: "%.2f", $0) } ?? "—")
                        NKStatCell(title: "净盈亏", value: NKFmt.signedAmount(stats.realizedPnl),
                                   tone: stats.realizedPnl >= 0 ? .good : .bad,
                                   footnote: "毛 \(NKFmt.signedAmount(stats.grossPnl)) · 费用 \(NKFmt.amount(stats.totalFees))")
                        NKStatCell(title: "累计亏损", value: NKFmt.signedAmount(stats.realizedLoss),
                                   tone: .bad, footnote: "只累加亏损,恒 ≤ 0")
                    }
                } else {
                    NKEmptyState(title: "这周还没有可统计的成交",
                                 subtitle: "我的成绩来自交割单 —— 先在「交割单」页传一份。",
                                 systemImage: "person.crop.circle.badge.questionmark")
                }
            }
        }
    }

    // MARK: - macOS 列表栏

    #if os(macOS)
    private var listColumn: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            VStack(alignment: .leading, spacing: 2) {
                Text(AppTab.review.title).font(NKFont.title2).tracking(-0.3)
                    .foregroundStyle(NK.textPrimary)
                Text("上传、整理与保存")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }
            .padding(.horizontal, NKSpace.listHeaderExtraH).padding(.bottom, 12)

            ForEach(ReviewPage.allCases) { p in
                NKListRow(selected: model.reviewPage == p) { model.reviewPage = p } content: {
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 8) {
                            Image(systemName: p.systemImage).font(.system(size: 11))
                                .foregroundStyle(model.reviewPage == p ? NK.accent : NK.textTertiary)
                                .frame(width: 16)
                            Text(p.title).font(NKFont.body)
                                .fontWeight(model.reviewPage == p ? .semibold : .regular)
                                .foregroundStyle(NK.textPrimary)
                        }
                        // 🔴 **「这一页答什么」** —— 四页的域各不相同,一行字把它们分开。
                        Text(p.question).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.leading, 24)
                    }
                }
            }
        }
    }
    #endif
}

// MARK: - 结论录入弹层

/// 🔴 **append-only**:提交一次 = 新版本,老版本一个字不动。
/// ⚠ 从上一版起草是**便利**,不是覆盖 —— 存下去仍是新的一版。
struct ConclusionEditorSheet: View {
    @Bindable var model: AppModel

    var body: some View {
        NKSheetShell(title: "写一版复盘结论",
                     primaryTitle: "存新版",
                     primaryDisabled: !model.conclusionForm.isValid || model.conclusionForm.submitting,
                     onCancel: { model.showConclusionEditor = false },
                     onPrimary: { Task { await model.submitConclusion() } }) {
            VStack(alignment: .leading, spacing: 10) {
                Text("结论由你基于材料自行形成；这里负责保存与回看。"
                     + "每次保存都会新建一版，过去的版本保持不变。")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                field("周（例如 2026-W34）", text: Binding(
                    get: { model.conclusionForm.week },
                    set: { model.conclusionForm.week = $0 }))
                field("标题", text: Binding(
                    get: { model.conclusionForm.title },
                    set: { model.conclusionForm.title = $0 }))
                VStack(alignment: .leading, spacing: 4) {
                    Text("正文").font(NKFont.callout).fontWeight(.semibold)
                        .foregroundStyle(NK.textPrimary)
                    TextEditor(text: Binding(
                        get: { model.conclusionForm.body },
                        set: { model.conclusionForm.body = $0 }))
                        .font(NKFont.body)
                        .frame(minHeight: 160)
                        .overlay(RoundedRectangle(cornerRadius: NKRadius.control)
                            .stroke(NK.hairline, lineWidth: 0.5))
                }
                field("标签(空格或逗号分隔)", text: Binding(
                    get: { model.conclusionForm.tagsText },
                    set: { model.conclusionForm.tagsText = $0 }))
            }
        }
        #if os(macOS)
        .frame(minWidth: 520, minHeight: 520)
        #endif
    }

    private func field(_ label: String, text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(NKFont.callout).fontWeight(.semibold)
                .foregroundStyle(NK.textPrimary)
            TextField("", text: text).textFieldStyle(.roundedBorder).font(NKFont.body)
        }
    }
}
