//
//  WatchlistView.swift
//  Neckline — 自选板块(§五 v1.1-F,独立第五 tab):≤30 用户主理池,列表展示体检
//  (评分 / 形态标签 / 纪律红绿灯 / 触发买点四件套)、增删/pin(增删仅用户操作,本
//  文件无任何自动增删入口)、macOS 独有的同花顺 txt 对账工作台(iOS 不做,§F.4)。
//
//  数据来自 `GET /watchlist`(每项已带最近一份报告的体检快照,不现场重算)。
//

import SwiftUI
#if os(macOS)
import AppKit
import UniformTypeIdentifiers
#endif

struct WatchlistView: View {
    @Bindable var model: AppModel
    @State private var codeDraft = ""
    #if os(macOS)
    @State private var isTargeted = false
    @State private var exportedText: String? = nil
    #endif

    var body: some View {
        #if os(iOS)
        NavigationStack {
            scrollBody
                .navigationTitle("自选")
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        Button { Task { await model.loadWatchlist() } } label: { Image(systemName: "arrow.clockwise") }
                    }
                }
                .refreshable { await model.loadWatchlist() }
        }
        .task { await model.loadWatchlist() }
        #else
        scrollBody
            .toolbar {
                ToolbarItem { Button { Task { await model.loadWatchlist() } } label: { Image(systemName: "arrow.clockwise") } }
            }
            .task { await model.loadWatchlist() }
        #endif
    }

    private var scrollBody: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: NKSpace.gap) {
                header
                addBar
                if model.watchlist.items.isEmpty {
                    NKCard {
                        NKEmptyState(title: "自选池为空",
                                    subtitle: "在候选卡 / 问询台裁决卡点「+自选」,或在上方直接填代码添加。",
                                    systemImage: "star")
                    }
                } else {
                    ForEach(model.watchlist.items) { item in
                        WatchlistRow(model: model, item: item)
                    }
                }
                #if os(macOS)
                Divider().overlay(NK.hairline).padding(.vertical, 4)
                thsWorkbenchSection
                #endif
            }
            .padding(NKSpace.pagePad)
            #if os(macOS)
            .frame(maxWidth: 860)
            #endif
        }
        #if os(macOS)
        .frame(maxWidth: .infinity)
        #endif
        .background(platformBg)
    }

    private var platformBg: Color {
        #if os(iOS)
        NK.pageBgIOS
        #else
        NK.pageBg
        #endif
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            // iOS 由 navigationTitle 渲染大标题,此处再画会双标题(同 TodayPlanView/BoardView 惯例)。
            #if os(macOS)
            Text("自选").font(NKFont.largeTitle).foregroundStyle(NK.textPrimary)
            #endif
            Text("\(model.watchlist.items.count)/\(model.watchlist.maxSize) 只 · 体检随每日 16:35 报告更新")
                .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
        }
    }

    private var addBar: some View {
        HStack(spacing: 8) {
            TextField("代码,如 600519.SH", text: $codeDraft)
                #if os(iOS)
                .textInputAutocapitalization(.characters)
                .autocorrectionDisabled()
                #endif
                .padding(10)
                .background(RoundedRectangle(cornerRadius: NKRadius.field).fill(NK.fieldBg))
                .onSubmit { submitAdd() }
            Button("+ 自选") { submitAdd() }
                .buttonStyle(.borderedProminent)
                .disabled(codeDraft.trimmingCharacters(in: .whitespaces).isEmpty)
        }
    }

    private func submitAdd() {
        let code = codeDraft.trimmingCharacters(in: .whitespaces)
        guard !code.isEmpty else { return }
        Task {
            if await model.quickAddWatchlist(code: code) { codeDraft = "" }
        }
    }

    // MARK: - v1.1-F.4:macOS 同花顺 txt 对账工作台(iOS 不做)

    #if os(macOS)
    @ViewBuilder
    private var thsWorkbenchSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "同花顺 txt 对账", trailing: "桌面场景 · iOS 不做")
            dropZone
            if model.thsReconcileLoading {
                NKCard {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("对账中…").font(.system(size: 13)).foregroundStyle(NK.textSecondary)
                    }
                }
            } else if let diff = model.thsReconcileResult {
                reconcileDiffCard(diff)
            }
            exportRow
        }
    }

    private var dropZone: some View {
        RoundedRectangle(cornerRadius: NKRadius.card)
            .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6, 5]))
            .foregroundStyle(isTargeted ? NK.accent : NK.hairline)
            .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(isTargeted ? NK.accent.opacity(0.06) : NK.cardBg))
            .frame(height: 96)
            .overlay {
                VStack(spacing: 6) {
                    Image(systemName: "tray.and.arrow.down.fill").font(.system(size: 22))
                        .foregroundStyle(isTargeted ? NK.accent : NK.textTertiary)
                    Text("把同花顺导出的自选 .txt 拖到这里").font(.system(size: 12.5, weight: .medium))
                        .foregroundStyle(NK.textSecondary)
                }
            }
            .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
                handleDrop(providers)
            }
    }

    private func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first(where: { $0.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) }) else {
            return false
        }
        Task {
            guard let file = await Self.loadFile(from: provider) else {
                await MainActor.run { model.showToast("未能读取拖入的文件", isError: true) }
                return
            }
            await model.reconcileThsFile(filename: file.filename, data: file.data)
        }
        return true
    }

    /// 同 `ReviewWorkbenchView.loadFile` 姿势(§五 阶段4D),把 `NSItemProvider` 异步
    /// 回调式 API 包成 `async`,读出(文件名,原始字节)。
    private static func loadFile(from provider: NSItemProvider) async -> (filename: String, data: Data)? {
        await withCheckedContinuation { continuation in
            _ = provider.loadObject(ofClass: URL.self) { url, _ in
                guard let url, let data = try? Data(contentsOf: url) else {
                    continuation.resume(returning: nil)
                    return
                }
                continuation.resume(returning: (url.lastPathComponent, data))
            }
        }
    }

    private func reconcileDiffCard(_ diff: ThsReconcileResult) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                diffGroup("仅同花顺有(\(diff.onlyInThs.count))", diff.onlyInThs, tone: .warn)
                diffGroup("仅 Neckline 有(\(diff.onlyInNeckline.count))", diff.onlyInNeckline, tone: .warn)
                diffGroup("两者都有(\(diff.both.count))", diff.both, tone: .good)
                if !diff.onlyInThs.isEmpty || !diff.onlyInNeckline.isEmpty {
                    Button("一键对齐(加 \(diff.onlyInThs.count) · 删 \(diff.onlyInNeckline.count))") {
                        Task { await model.applyThsAlignment() }
                    }
                    .buttonStyle(.borderedProminent)
                } else {
                    Label("已一致,无需对齐", systemImage: "checkmark.seal.fill")
                        .font(.system(size: 12.5)).foregroundStyle(NK.up)
                }
            }
        }
    }

    @ViewBuilder
    private func diffGroup(_ title: String, _ codes: [String], tone: NKAxisTone) -> some View {
        if !codes.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.system(size: 11.5, weight: .semibold)).foregroundStyle(NK.textSecondary)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(codes, id: \.self) { NKChip(text: $0, tone: tone) }
                    }
                }
            }
        }
    }

    private var exportRow: some View {
        HStack {
            Button("导出为同花顺 txt") {
                Task {
                    if let text = await model.exportThsText() { exportedText = text }
                }
            }
            .buttonStyle(.bordered)
            if let text = exportedText {
                Button("存为文件…") { saveToFile(text) }
                    .buttonStyle(.bordered)
                Text("已生成 \(text.split(separator: "\n").count) 行").font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
            }
        }
    }

    private func saveToFile(_ text: String) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "neckline_watchlist.txt"
        panel.allowedContentTypes = [.plainText]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try text.write(to: url, atomically: true, encoding: .utf8)
            model.showToast("已导出到 \(url.lastPathComponent)")
        } catch {
            model.showToast("存文件失败:\(error.localizedDescription)", isError: true)
        }
    }
    #endif
}

// MARK: - 自选行(体检展示 + pin/删,§F.2「复用 CandidateRow 四件套布局」经 FourPieceDisclosure)

private struct WatchlistRow: View {
    @Bindable var model: AppModel
    let item: WatchlistItem

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(item.name).font(NKFont.stockName).foregroundStyle(NK.textPrimary)
                            Text(item.code).font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                        }
                        if let c = item.check {
                            Text(c.boardLabel).font(.system(size: 11)).foregroundStyle(NK.textSecondary)
                        }
                    }
                    Spacer()
                    if let score = item.check?.score {
                        Text(String(format: "%.1f 分", score))
                            .font(.system(size: 13, weight: .semibold).monospacedDigit())
                    }
                }
                if let c = item.check {
                    HStack(spacing: 6) {
                        NKChip(text: c.greenLight ? "纪律绿灯 · 可动" : "纪律红灯 · 禁买",
                              tone: c.greenLight ? .good : .bad, filled: true)
                        if c.buyPointTriggered { NKChip(text: "买点已触发", tone: .good) }
                        if c.statusChanged { NKChip(text: "较昨日有变化", tone: .warn) }
                    }
                    if !c.patternTags.isEmpty || !c.hotSectors.isEmpty {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 6) {
                                ForEach(c.patternTags, id: \.self) { NKChip(text: $0) }
                                ForEach(c.hotSectors, id: \.self) { NKChip(text: $0, tone: .good) }
                            }
                        }
                    }
                    if !c.disqualifiers.isEmpty {
                        VStack(alignment: .leading, spacing: 2) {
                            ForEach(c.disqualifiers, id: \.self) { d in
                                Text("· \(d)").font(.system(size: 11)).foregroundStyle(NK.down)
                            }
                        }
                    }
                    if c.buyPointTriggered {
                        FourPieceDisclosure(buyPoint: c.buyPoint, stop: c.stop, target: c.target,
                                            invalidation: c.invalidation, llmJudgment: c.llmJudgment)
                    }
                } else {
                    Text("尚未体检 · 今晚 16:35 报告后可见评分").font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
                }
                Divider().overlay(NK.hairline)
                HStack(spacing: 16) {
                    Button {
                        Task { await model.toggleWatchlistPin(code: item.code, pinned: !item.pinned) }
                    } label: {
                        Label(item.pinned ? "已置顶(每日必审)" : "置顶(每日必审)",
                             systemImage: item.pinned ? "pin.fill" : "pin")
                            .font(.system(size: 12))
                    }
                    .buttonStyle(.plain).foregroundStyle(item.pinned ? NK.accent : NK.textSecondary)
                    Spacer()
                    Button {
                        Task { await model.removeFromWatchlist(code: item.code) }
                    } label: {
                        Label("移除", systemImage: "trash").font(.system(size: 12, weight: .semibold))
                    }
                    .buttonStyle(.plain).foregroundStyle(NK.down)
                }
            }
        }
    }
}
