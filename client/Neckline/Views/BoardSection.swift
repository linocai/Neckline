//
//  BoardSection.swift
//  Neckline — **盘中动态**(V2-⑮:由 V1 的独立「盘中看板」tab 降为**持仓板块的一节**)。
//
//  **为什么不再是一个 tab**:D8 拍板 iPhone 四板块 = 今日篮子 / 持仓 / 问询台 / 设置,
//  **不新增 tab**;而 V2 的注意力分配是 **80/15/5(持仓 80%)** —— 盘中动态本来就是
//  为解释持仓服务的,挂在持仓板块比单开一个 tab 更贴合。⛔ **数据一条没删**,只是换了
//  挂载点(退潮刹车红条 + 哨兵事件列表原样保留)。
//
//  数据来自 `GET /board`。**只读、不产生任何新判断**(§2.4 铁律):本视图只展示哨兵
//  已落库的判决,不在客户端做任何二次裁定。
//

import SwiftUI

struct BoardSection: View {
    @Bindable var model: AppModel
    /// 轻量轮询(纯只读 GET)。
    @State private var pollTask: Task<Void, Never>? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.gap) {
            HStack {
                NKSectionHeader(title: "盘中动态 \(model.board.events.count)")
                Spacer()
                if !model.board.asof.isEmpty {
                    Text("更新于 \(model.board.asof)").font(.system(size: 10.5))
                        .foregroundStyle(NK.textTertiary)
                }
            }
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
            if model.board.events.isEmpty {
                NKCard {
                    NKEmptyState(title: "暂无哨兵事件",
                                 subtitle: "证伪剔除 / 持仓预警 / 盘前校准会在这里出现。",
                                 systemImage: "waveform.path.ecg")
                }
            } else {
                ForEach(model.board.events) { e in
                    BoardEventRow(event: e)
                }
            }
        }
        .task { await model.loadBoard(); startPolling() }
        .onDisappear { pollTask?.cancel() }
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
}

private struct BoardEventRow: View {
    let event: BoardEvent

    private var tone: NKAxisTone {
        switch event.kind {
        case .entry: return .good
        case .invalidation: return .bad
        case .holding: return .warn
        case .precall: return .warn
        case .d5exit: return .bad        // 今日必须离场,同证伪一样醒目
        case nil: return .neutral        // 未识别类型原样展示,不崩
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
