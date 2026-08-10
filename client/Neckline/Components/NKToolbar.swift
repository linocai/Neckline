//
//  NKToolbar.swift
//  Neckline — V2.3 视觉升级:macOS 统一工具栏(规范 §05 / §06)
//
//  🔴 **左侧 240px 导航栏整个去掉**(规范 §01 决定 01):四个板块进工具栏,
//  窗口宽度全部还给内容。
//
//  🔴 **齿轮不做成第四个胶囊** —— 设置**是入口不是板块**(V2.1 用户裁定 #2,
//  「设置在产品语义上不算板块」)。它沉在右端,与三个胶囊之间隔开。
//
//  🔴 **退潮刹车条压在工具栏下方通栏,⛔ 不进卡片流**:它管的是「今天整份计划」,
//  不是某一篮。⚠ 刹车激活时**篮子仍然全部列出、点得开** —— 作废的是计划,不是数据;
//  **补录开仓按钮同样不灰化**(硬拦 = 帮用户瞒报,V2.2-⑤-B 已拆过一次熔断)。
//

import SwiftUI

#if os(macOS)

struct NKToolbar: View {
    @Bindable var model: AppModel
    /// 三个板块(设置不在内 —— 它是右端那个齿轮)。
    private let tabs: [AppTab] = [.baskets, .positions, .review]

    var body: some View {
        HStack(spacing: 10) {
            // 左:红绿灯留白 → Logo + 字标 → 分隔线 → 三个板块胶囊
            Color.clear.frame(width: 68, height: 1)      // 红绿灯占位
            HStack(spacing: 7) {
                NKLogo(size: 20)
                Text("Neckline").font(NKFont.callout).fontWeight(.semibold)
                    .foregroundStyle(NK.textPrimary)
            }
            Divider().frame(height: 18).overlay(NK.hairline)
            HStack(spacing: 3) { ForEach(tabs) { tabPill($0) } }

            Spacer(minLength: 12)

            // 右:行情状态 ← 交易日·章程·选股包 ← 降级告警 ← 刷新 ← 齿轮
            regimePill
            metaLine
            degradeBadge
            refreshButton
            gearButton
        }
        .padding(.horizontal, 12)
        .frame(height: 50)
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) { Divider().overlay(NK.hairline) }
    }

    // MARK: - 板块胶囊(选中 = 白底 + 1px 投影 + .5px 描边)

    private func tabPill(_ tab: AppTab) -> some View {
        let active = model.view == tab
        return Button { model.view = tab } label: {
            HStack(spacing: 5) {
                Image(systemName: tab.systemImage).font(.system(size: 11, weight: .medium))
                Text(tab.title).font(NKFont.callout)
                    .fontWeight(active ? .semibold : .regular)
                if tab == .baskets, basketCount > 0 {
                    Text("\(basketCount)").font(NKFont.caption.monospacedDigit())
                        .fontWeight(.semibold)
                        .foregroundStyle(NK.accent)
                }
                // 退潮刹车时持仓那枚带一个感叹号 —— 与刹车条同时出现,不是替代关系。
                if tab == .positions, model.board.retreatBrake.active {
                    Text("!").font(.system(size: 9, weight: .bold)).foregroundStyle(.white)
                        .padding(.horizontal, 4).padding(.vertical, 0.5)
                        .background(Capsule().fill(NK.down))
                }
            }
            .foregroundStyle(active ? NK.textPrimary : NK.textSecondary)
            .padding(.horizontal, 11).padding(.vertical, 5)
            .background(
                RoundedRectangle(cornerRadius: NKRadius.control)
                    .fill(active ? NK.cardBg : Color.clear)
                    .shadow(color: active ? Color.black.opacity(0.10) : .clear, radius: 1, y: 0.5)
            )
            .overlay(
                RoundedRectangle(cornerRadius: NKRadius.control)
                    .stroke(active ? NK.hairline : Color.clear, lineWidth: 0.5)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var basketCount: Int { model.report.basketDaily.baskets.count }

    // MARK: - 行情状态(色点 + 标签);退潮刹车时翻成 NK.down 实底白字

    @ViewBuilder
    private var regimePill: some View {
        if model.board.retreatBrake.active {
            Text("退潮刹车").font(NKFont.caption).fontWeight(.bold)
                .foregroundStyle(.white)
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background(Capsule().fill(NK.down))
        } else if let d = model.marketRegime.day, model.marketRegime.available {
            HStack(spacing: 4) {
                Circle().fill(d.tone.color).frame(width: 6, height: 6)
                Text(d.displayLabel).font(NKFont.caption).foregroundStyle(NK.textSecondary)
            }
        } else {
            // 🔴 「没取到」不等于「今天没什么特别的」——⛔ 不许什么都不显示。
            HStack(spacing: 4) {
                Circle().fill(NK.textTertiary).frame(width: 6, height: 6)
                Text("行情状态未取得").font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }
        }
    }

    /// 交易日 · 章程 · 选股包。**缺哪个就不写哪个**,⛔ 不用占位符冒充。
    private var metaLine: some View {
        Text(metaParts.joined(separator: " · "))
            .font(NKFont.caption.monospacedDigit())
            .foregroundStyle(NK.textTertiary)
    }

    private var metaParts: [String] {
        var parts: [String] = []
        let d = model.report.tradeDate
        if d.count == 8 {
            parts.append("\(d.dropFirst(4).prefix(2))-\(d.suffix(2))")
        } else if !d.isEmpty {
            parts.append(d)
        }
        if !model.report.strategyVersion.isEmpty { parts.append(model.report.strategyVersion) }
        // 选股包版本来自报告快照(⛔ 不硬编 `K8-V0.5`,也别改读 `marketRegime` 的
        // `skeletonVersion` —— 那是**行情状态层**的骨架版本,与选股包不是同一个量)。
        if let pack = model.basketDaily.packVersion, !pack.isEmpty { parts.append(pack) }
        return parts
    }

    /// 降级告警:**有才出现**,可点(点进选股板块的 ⑤ 数据新鲜度段)。
    @ViewBuilder
    private var degradeBadge: some View {
        let n = degradeCount
        if n > 0 {
            Button { model.view = .baskets } label: {
                HStack(spacing: 4) {
                    Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 9))
                    Text("降级 \(n) 项").font(NKFont.caption).fontWeight(.semibold)
                }
                .foregroundStyle(NK.amber)
                .padding(.horizontal, 7).padding(.vertical, 3)
                .background(Capsule().fill(NK.amber.opacity(0.14)))
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }

    /// ⚠ 只数**已知为真**的三件(板块 / 行业强度 / 扫描层);`nil` 是「没查到」——
    /// 它由 ⑤ 那一段如实说出口,⛔ 不在这里当成"降级"也不当成"正常"。
    private var degradeCount: Int {
        guard let f = model.report.dataFreshness else { return 0 }
        var n = 0
        if f.stale { n += 1 }
        if f.industryStrengthStale == true { n += 1 }
        if f.scanLayerStale == true { n += 1 }
        return n
    }

    // MARK: - 刷新(按钮上直接显示上次更新时刻)+ 齿轮

    private var refreshButton: some View {
        Button { Task { await model.refresh() } } label: {
            HStack(spacing: 5) {
                if model.reportLoading {
                    ProgressView().controlSize(.mini).tint(.white)
                } else {
                    Image(systemName: "arrow.clockwise").font(.system(size: 10, weight: .semibold))
                }
                // ⚠ 还没成功刷新过就不写时刻(⛔ 不拿"现在"冒充)。
                Text(model.lastRefreshedAt.map(NKToolbar.hhmm) ?? "刷新")
                    .font(NKFont.caption.monospacedDigit()).fontWeight(.semibold)
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 9).padding(.vertical, 4)
            .background(Capsule().fill(NK.accent))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(model.reportLoading)
        .help("刷新报告 / 持仓 / 盘中动态;按钮上是本机上次成功刷新的时刻")
    }

    /// 🔴 **齿轮是入口不是板块** —— ⛔ 别把它做成第四个胶囊。
    private var gearButton: some View {
        Button { model.view = .settings } label: {
            Image(systemName: "gearshape")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(model.view == .settings ? NK.accent : NK.textSecondary)
                .frame(width: 24, height: 24)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("设置")
    }

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return f
    }()

    static func hhmm(_ d: Date) -> String { timeFormatter.string(from: d) }
}

#endif
