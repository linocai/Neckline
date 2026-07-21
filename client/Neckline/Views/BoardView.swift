//
//  BoardView.swift
//  Neckline — 盘中看板(§五 阶段4C.2):哨兵判决集中显示(退潮状态 / 已证伪候选 /
//  已触发买点 / 持仓预警)。数据来自 `GET /board`。打开即拉,无推送依赖(§2.4:
//  买点/证伪/持仓事件只进看板,不进 APNs;退潮才推 APNs,但看板同样显示红条)。
//

import SwiftUI

struct BoardView: View {
    @Bindable var model: AppModel
    /// 打开即拉 + 轻量轮询(纯只读 GET,不产生任何新判断,§2.4 铁律「盘中不产生任何
    /// 新决策」——本视图只展示哨兵已落库的判决,不在客户端做任何二次裁定)。
    @State private var pollTask: Task<Void, Never>? = nil

    var body: some View {
        #if os(iOS)
        NavigationStack {
            scrollBody
                .navigationTitle("盘中看板")
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        Button { Task { await model.loadBoard() } } label: { Image(systemName: "arrow.clockwise") }
                    }
                }
                .refreshable { await model.loadBoard() }
        }
        .task { await model.loadBoard(); startPolling() }
        .onDisappear { pollTask?.cancel() }
        #else
        scrollBody
            .toolbar {
                ToolbarItem { Button { Task { await model.loadBoard() } } label: { Image(systemName: "arrow.clockwise") } }
            }
            .task { await model.loadBoard(); startPolling() }
            .onDisappear { pollTask?.cancel() }
        #endif
    }

    private func startPolling() {
        pollTask?.cancel()
        pollTask = Task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 60_000_000_000)   // 60s,同哨兵后端轮询节奏
                if Task.isCancelled { break }
                await model.loadBoard()
            }
        }
    }

    private var scrollBody: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: NKSpace.gap) {
                header
                if model.board.retreatBrake.active {
                    RetreatBrakeBanner(reason: model.board.retreatBrake.reason)
                } else {
                    NKCard {
                        HStack(spacing: 8) {
                            Image(systemName: "checkmark.seal.fill").foregroundStyle(NK.up)
                            Text("运行正常 · 无退潮刹车").font(.system(size: 13, weight: .medium))
                                .foregroundStyle(NK.textPrimary)
                            Spacer()
                        }
                    }
                }
                eventsSection
            }
            .padding(NKSpace.pagePad)
            #if os(macOS)
            .frame(maxWidth: 760)
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
            // iOS 由 navigationTitle 渲染大标题,此处再画会双标题(实机反馈修复);macOS 无大标题才自画
            #if os(macOS)
            Text("盘中看板").font(NKFont.largeTitle).foregroundStyle(NK.textPrimary)
            #endif
            if !model.board.asof.isEmpty {
                Text("最近更新 \(model.board.asof)").font(.system(size: 12)).foregroundStyle(NK.textSecondary)
            }
        }
    }

    private var eventsSection: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            NKSectionHeader(title: "哨兵事件 \(model.board.events.count)")
            if model.board.events.isEmpty {
                NKCard {
                    NKEmptyState(title: "暂无哨兵事件", subtitle: "买点触发 / 证伪剔除 / 持仓预警会在这里出现。",
                                systemImage: "waveform.path.ecg")
                }
            } else {
                ForEach(model.board.events) { e in
                    BoardEventRow(event: e)
                }
            }
        }
    }
}

private struct BoardEventRow: View {
    let event: BoardEvent

    private var tone: NKAxisTone {
        switch event.kind {
        case .entry: return .good
        case .invalidation: return .bad
        case .holding: return .warn
        case nil: return .neutral
        }
    }

    var body: some View {
        NKCard {
            HStack(alignment: .top, spacing: 10) {
                NKChip(text: event.sentinel, tone: tone, filled: true)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(event.name).font(.system(size: 13.5, weight: .semibold)).foregroundStyle(NK.textPrimary)
                        Text(event.code).font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                    }
                    Text(event.verdict).font(.system(size: 12.5)).foregroundStyle(NK.textSecondary)
                }
                Spacer()
                Text(event.ts).font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
            }
        }
    }
}
