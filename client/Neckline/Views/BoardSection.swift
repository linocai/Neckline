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
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            HStack {
                NKSectionHeader(title: "盘中动态 \(model.board.events.count)")
                Spacer()
                if !model.board.asof.isEmpty {
                    Text("更新于 \(model.board.asof)").font(NKFont.caption)
                        .foregroundStyle(NK.textTertiary)
                }
            }
            // ⚠ **V2.3:这里只留一行状态,通栏刹车条已上移到壳**(`RootView`)——
            // 刹车管的是「今天整份计划」,不是这一节;同一件事在一屏里画两遍,
            // 醒目的那条反而会被当成重复内容跳过。
            statusRow
            Text("哨兵已落库的判决 · 只读,不产生任何新判断")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
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
            NKDisclosure(summary: "盘中关注池是代理样本,不是全市场") {
                Text("关注池 = 持仓 + T1/T2 篮子成员 + 板块基准指数 + 昨日涨停(免费源限流取舍)。"
                     + "⛔ 它不是全市场扫描 —— 没出现在这里,不等于全市场没发生。")
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .task { await model.loadBoard(); startPolling() }
        .onDisappear { pollTask?.cancel() }
    }

    @ViewBuilder
    private var statusRow: some View {
        NKCard(padding: 12) {
            HStack(spacing: 8) {
                if model.board.retreatBrake.active {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(NK.down)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("退潮红色刹车已触发").font(NKFont.body).fontWeight(.semibold)
                            .foregroundStyle(NK.down)
                        if !model.board.retreatBrake.reason.isEmpty {
                            Text(model.board.retreatBrake.reason).font(NKFont.caption)
                                .foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                } else {
                    Image(systemName: "checkmark.seal.fill").foregroundStyle(NK.up)
                    Text("运行正常 · 无退潮刹车").font(NKFont.body)
                        .foregroundStyle(NK.textPrimary)
                }
                Spacer(minLength: 0)
            }
        }
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
                        Text(event.name).font(NKFont.body).fontWeight(.semibold).foregroundStyle(NK.textPrimary)
                        Text(event.code).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    }
                    Text(event.verdict).font(NKFont.callout).foregroundStyle(NK.textSecondary)
                }
                Spacer()
                Text(event.ts).font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }
        }
    }
}
