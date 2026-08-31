import SwiftUI

struct SelectionView: View {
    @Bindable var model: AppModel

    var body: some View {
        #if os(macOS)
        NKSplitLayout { rail } detail: { main }
        #else
        NavigationStack { ScrollView {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                picker
                main
            }
            .padding(NKSpace.pagePad)
        }
            .contentMargins(.bottom, 96, for: .scrollContent)
            .background(NK.pageBgIOS).navigationTitle("选股")
            .refreshable { await model.refreshSelection() } }
        #endif
    }

    private var picker: some View {
        Picker("", selection: $model.selectionMode) {
            ForEach(SelectionViewMode.allCases) { mode in Text(mode.title).tag(mode) }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
    }

    private var rail: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            Text("选股").font(NKFont.title2).foregroundStyle(NK.textPrimary)
            picker
            if model.selectionMode == .listing {
                ForEach(model.selection.stocks) { stock in
                    Button { if let batch = stock.batchId { model.openScorePackage(batchId: batch); model.view = .scoreboard } } label: {
                        stockRow(stock)
                    }.buttonStyle(.plain)
                }
            } else {
                CheckListRail(model: model)
            }
        }
    }

    @ViewBuilder private var main: some View {
        if model.selectionMode == .checklist {
            CheckListView(model: model)
        } else {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                stateCard
                if model.selection.state == .notRun { gapsCard }
                if model.hasListing {
                    ForEach(model.selection.stocks) { stock in
                        Button { if let batch = stock.batchId { model.openScorePackage(batchId: batch); model.view = .scoreboard } } label: {
                            stockRow(stock)
                        }.buttonStyle(.plain)
                    }
                }
                if let direction = model.selection.direction, !direction.isNull {
                    K9DirectionCard(value: direction)
                }
            }
        }
    }

    private var stateCard: some View {
        NKCard { VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(versionMismatch == nil ? model.selection.headlineText : "前后端版本不一致")
                    .font(NKFont.title3).foregroundStyle(NK.textPrimary)
                Spacer(); NKChip(text: "K9-v3", tone: .info)
            }
            if let versionMismatch {
                NKInlineNote(text: LocalizedStringKey(versionMismatch), tone: .warn)
            }
            Text("报告日 \(NKFmt.reportDate(model.selection.reportDate)) · 信号交易日 \(NKFmt.reportDate(model.selection.tradeDate))")
                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
            if let count = model.selection.listingSize { Text("本次清单 \(count) 只").font(NKFont.callout).foregroundStyle(NK.textSecondary) }
            if model.selectionOffline { Text("离线浏览：仅显示同一 K9-v3 / fp-4 合同的本地快照。").font(NKFont.caption).foregroundStyle(NK.amber) }
        }}
    }

    private var versionMismatch: String? {
        NKVersionCompatibility.message(serverVersion: model.serverVersion)
    }

    private var gapsCard: some View {
        NKCard { VStack(alignment: .leading, spacing: 5) {
            NKSectionHeader(title: "今天没跑成")
            ForEach(model.selection.gaps, id: \.self) { Text("· \($0)").font(NKFont.callout).foregroundStyle(NK.textSecondary) }
            Text("参数未配置、事实未就绪和空清单是不同状态；不会显示旧版缓存。")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
        }}
    }

    private func stockRow(_ stock: K9Stock) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack { Text(stock.displayName).font(NKFont.headline).foregroundStyle(NK.textPrimary); Text(stock.tsCode).font(NKFont.monoKey).foregroundStyle(NK.textTertiary); Spacer() }
            if let industry = stock.swL2Name, !industry.isEmpty { Text(industry).font(NKFont.caption).foregroundStyle(NK.textSecondary) }
            NKWrapRow(spacing: 5, lineSpacing: 5) {
                ForEach(stock.patterns, id: \.self) { channel in NKChip(text: "\(nkChannelLabel(channel)) · 第 \(stock.channelRanks[channel] ?? 0)", tone: .info) }
                NKChip(text: stock.playbookLabel, tone: .neutral)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading).padding(NKSpace.cardPad)
        .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.hairline, lineWidth: 0.5))
    }
}

private struct K9DirectionCard: View {
    let value: NKJSON
    private var content: K9DirectionPresentation { K9Presentation.direction(value) }

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                NKSectionHeader(title: "方向背景")
                if !content.summary.isEmpty {
                    Text(content.summary).font(NKFont.callout).foregroundStyle(NK.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                ForEach(content.themes) { theme in
                    VStack(alignment: .leading, spacing: 3) {
                        Text(theme.name).font(NKFont.headline).foregroundStyle(NK.textPrimary)
                        Text(theme.reason).font(NKFont.callout).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(.vertical, 8).padding(.horizontal, 10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 8).fill(NK.textTertiary.opacity(0.07)))
                }
                if content.summary.isEmpty && content.themes.isEmpty {
                    Text(content.failureReason.isEmpty ? "方向解读暂未生成。" : "方向解读暂不可用：\(content.failureReason)")
                        .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                }
                DisclosureGroup("查看原始方向合同") { NKJSONTable(value: value).padding(.top, 6) }
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                NKReferenceNote(text: "方向背景不参与 K9-v3 的机械召回、排序或额度。")
            }
        }
    }
}
