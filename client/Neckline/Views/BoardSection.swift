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
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            #if os(macOS)
            // 原型 1200–1202 行:大标题 `26/700 tracking -.4` + 一句 `12 .55` 副标题
            //(把「只读、更新于几点」两件都说在副标题里)。
            VStack(alignment: .leading, spacing: 3) {
                Text("盘中动态").font(NKFont.title1).tracking(-0.4)
                    .foregroundStyle(NK.textPrimary)
                Text(subtitleText).font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            #else
            HStack {
                NKSectionHeader(title: "盘中动态 \(model.board.events.count)")
                Spacer()
                if !model.board.asof.isEmpty {
                    Text("更新于 \(model.board.asof)").font(NKFont.caption)
                        .foregroundStyle(NK.textTertiary)
                }
            }
            Text("哨兵已落库的判决 · 只读,不产生任何新判断")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
            #endif
            // ⚠ **V2.3:这里只留一行状态,通栏刹车条已上移到壳**(`RootView`)——
            // 刹车管的是「今天整份计划」,不是这一节;同一件事在一屏里画两遍,
            // 醒目的那条反而会被当成重复内容跳过。
            statusRow
            if model.board.events.isEmpty {
                NKCard {
                    NKEmptyState(title: "暂无哨兵事件",
                                 subtitle: "证伪剔除 / 持仓预警 / 盘前校准会在这里出现。",
                                 systemImage: "waveform.path.ecg")
                }
            } else {
                eventsCard
            }
            // 原型 1224 行是一块**常开**的灰底说明,⛔ 不是折叠区:「关注池不是全市场」
            // 是读这一屏的前提,收起来等于没写。
            NKNoteBlock(text: "盘中关注池是代理样本,不是全市场 —— 持仓 + T1/T2 篮子成员 + 板块基准指数 + 昨日涨停(免费源限流取舍)。⛔ 没出现在这里,不等于全市场没发生。")
        }
        .task { await model.loadBoard(); startPolling() }
        .onDisappear { pollTask?.cancel() }
    }

    private var subtitleText: String {
        var s = "哨兵已落库的判决 · 只读,不产生任何新判断"
        if !model.board.asof.isEmpty { s += " · 更新于 \(model.board.asof)" }
        return s
    }

    /// 事件列表 = **一张卡里若干行 + `.5px` 分隔**(原型 1207–1223),
    /// ⛔ 不是一条事件一张卡(那样三条事件读起来像三段互不相干的内容)。
    private var eventsCard: some View {
        VStack(spacing: 0) {
            ForEach(Array(model.board.events.enumerated()), id: \.element.id) { idx, e in
                BoardEventRow(event: e)
                if idx < model.board.events.count - 1 {
                    Rectangle().fill(NK.hairline.opacity(0.6)).frame(height: 0.5)
                }
            }
        }
        .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.hairline, lineWidth: 0.5))
    }

    @ViewBuilder
    private var statusRow: some View {
        // 原型 1203–1206 行:`padding:14px 18px`,16px 图标 + `13.5/600` 一句话。
        NKCard {
            HStack(spacing: 10) {
                if model.board.retreatBrake.active {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 16, weight: .semibold)).foregroundStyle(NK.down)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("退潮红色刹车已触发").font(NKFont.body).fontWeight(.semibold)
                            .foregroundStyle(NK.down)
                        if !model.board.retreatBrake.reason.isEmpty {
                            Text(model.board.retreatBrake.reason).font(NKFont.caption)
                                .foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                } else {
                    Image(systemName: "checkmark.circle")
                        .font(.system(size: 16, weight: .semibold)).foregroundStyle(NK.up)
                    Text("运行正常 · 无退潮刹车").font(NKFont.body).fontWeight(.semibold)
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

    /// 原型 1208–1211 行:`padding:13px 18px; gap:12`;实心类型徽标 + 名称 `13/600`
    /// (代码 `11 .40` **同一行内**跟在后面)+ 判词 `12 .55` + 右端时刻 `10.5 .40`。
    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            NKChip(text: event.sentinel, tone: tone, filled: true)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(event.name).font(NKFont.body).fontWeight(.semibold)
                        .foregroundStyle(NK.textPrimary)
                    Text(event.code).font(NKFont.caption.monospacedDigit())
                        .foregroundStyle(NK.textTertiary)
                }
                Text(event.verdict).font(NKFont.callout).lineSpacing(2)
                    .foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            Text(event.ts).font(NKFont.caption.monospacedDigit())
                .foregroundStyle(NK.textTertiary)
        }
        .padding(.horizontal, NKSpace.cardPadH).padding(.vertical, 13)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
