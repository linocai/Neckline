// KX V4 个股详情视图：macOS 放右栏、iOS 推入原生详情页。
// 严格区分「程序事实」与「DeepSeek 判断」；龙虎榜买卖前五支持展开/折叠（macOS 默认展开、iOS 默认折叠）。

import SwiftUI

struct KXStockDetailView: View {
    let item: KXBoardItem
    var onClose: (() -> Void)? = nil
    @State private var lhbExpanded: Bool

    init(item: KXBoardItem, onClose: (() -> Void)? = nil) {
        self.item = item
        self.onClose = onClose
        #if os(macOS)
        _lhbExpanded = State(initialValue: true)
        #else
        _lhbExpanded = State(initialValue: false)
        #endif
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                headerSection
                kpiSection
                trajectorySection
                judgmentSection
                lhbSection
                Text(KXCopy.footerNote)
                    .font(.system(size: 11))
                    .foregroundStyle(NKKX.textTertiary)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.top, 4)
            }
            .padding(detailPadding)
        }
        .background(NKKX.surface)
        #if os(iOS)
        .navigationTitle(item.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let onClose {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成", action: onClose)
                }
            }
        }
        #endif
    }

    private var detailPadding: CGFloat {
        #if os(macOS)
        20
        #else
        16
        #endif
    }

    // MARK: 1. 头部信息

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(item.name)
                            .font(.system(size: 21, weight: .bold))
                            .foregroundStyle(NKKX.textPrimary)
                        Text(item.code)
                            .font(.system(size: 13, design: .monospaced))
                            .foregroundStyle(NKKX.textTertiary)
                    }
                    Text("\(item.industry) · 流通 \(KXCopy.fmtCircMV(item.facts.circMvYi))")
                        .font(.system(size: 12))
                        .foregroundStyle(NKKX.textSecondary)
                }
                Spacer(minLength: 8)
                VStack(alignment: .trailing, spacing: 2) {
                    Text(KXCopy.fmtPrice(item.facts.close))
                        .font(.system(size: 22, weight: .bold, design: .monospaced))
                        .foregroundStyle(NKKX.textPrimary)
                    HStack(spacing: 4) {
                        Text(KXCopy.fmtPct(item.facts.pct))
                            .font(.system(size: 12.5, weight: .semibold, design: .monospaced))
                            .foregroundStyle(KXCopy.toneColor(item.facts.pct))
                        if !item.facts.boardStatus.isEmpty {
                            Text("· \(item.facts.boardStatus)")
                                .font(.system(size: 12))
                                .foregroundStyle(NKKX.textSecondary)
                        }
                    }
                }
            }

            KXFlowLayout(spacing: 6) {
                KXZonePill(zone: item.zone)
                KXRhythmPill(rhythm: item.judgment.rhythm)
                ForEach(Array(item.judgment.factions.enumerated()), id: \.offset) { _, tag in
                    KXFactionPillView(
                        text: "\(tag.name)\(tag.primary ? " · 主导" : " · 次要")",
                        primary: tag.primary
                    )
                }
                KXFactionPillView(text: item.cycleTag, primary: false)
            }

            if item.zone == .elim {
                HStack(spacing: 6) {
                    Image(systemName: item.nextRemove ? "clock.badge.checkmark" : "clock")
                        .font(.system(size: 11.5))
                    Text(item.nextRemove
                         ? "淘汰区已停留 \(item.elimDays) 个交易日，下期整理可移出释放坑位"
                         : "淘汰区已停留 \(item.elimDays) 个交易日，按规则展示满 1 个交易日后再判")
                        .font(.system(size: 12))
                }
                .foregroundStyle(item.nextRemove ? NKKX.amberInk : NKKX.textSecondary)
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(item.nextRemove ? NKKX.amberBackground : NKKX.surfaceSubtle)
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
        .padding(.bottom, 12)
        .overlay(alignment: .bottom) {
            Rectangle().fill(NKKX.border).frame(height: 1)
        }
    }

    // MARK: 2. 二波关键事实（程序算）

    private var kpiSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("二波关键事实（程序算）")
                    .font(.system(size: 11.5, weight: .semibold))
                    .foregroundStyle(NKKX.textTertiary)
                Spacer()
                Text("近5日：\(item.facts.zt5dN.map(String.init) ?? "—")涨停 · \(item.facts.dt5dN.map(String.init) ?? "—")跌停 · \(item.facts.lhb5dN.map(String.init) ?? "—")次上榜")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(NKKX.textSecondary)
            }

            LazyVGrid(columns: kpiColumns, spacing: 8) {
                kpiTile(
                    title: "今日换手率",
                    value: KXCopy.fmtTurnover(item.facts.tr, decimals: 2),
                    valueColor: NKKX.textPrimary,
                    sub: item.facts.suspended ? "今日停牌" : KXCopy.turnoverHint(item.facts.tr)
                )
                kpiTile(
                    title: "量能 / 本波峰值",
                    value: KXCopy.fmtRatioPct(item.facts.volRatioPeak),
                    valueColor: NKKX.textPrimary,
                    sub: KXCopy.volRatioHint(item.facts.volRatioPeak)
                )
                kpiTile(
                    title: "相对 5 日线",
                    value: KXCopy.fmtPct1(item.facts.ma5Diff),
                    valueColor: KXCopy.toneColor(item.facts.ma5Diff),
                    sub: "MA5 \(KXCopy.fmtPrice(item.facts.ma5))"
                )
                kpiTile(
                    title: "相对 10 日线",
                    value: KXCopy.fmtPct1(item.facts.ma10Diff),
                    valueColor: KXCopy.toneColor(item.facts.ma10Diff),
                    sub: "MA10 \(KXCopy.fmtPrice(item.facts.ma10))"
                )
                kpiTile(
                    title: "自高点回撤",
                    value: KXCopy.fmtPct1(item.facts.ddPeak),
                    valueColor: item.facts.ddPeak == nil ? NKKX.flat : NKKX.down,
                    sub: "15 日最高点计"
                )
            }
        }
    }

    private var kpiColumns: [GridItem] {
        #if os(macOS)
        Array(repeating: GridItem(.flexible(), spacing: 8), count: 5)
        #else
        Array(repeating: GridItem(.flexible(), spacing: 8), count: 2)
        #endif
    }

    private func kpiTile(title: String, value: String, valueColor: Color, sub: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(.system(size: 10.5))
                .foregroundStyle(NKKX.textTertiary)
            Text(value)
                .font(.system(size: 15, weight: .semibold, design: .monospaced))
                .foregroundStyle(valueColor)
            Text(sub)
                .font(.system(size: 10.5, design: .monospaced))
                .foregroundStyle(NKKX.textSecondary)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(NKKX.surfaceSubtle)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    // MARK: 3. 近 5 日盘面轨迹

    private var trajectorySection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("近 5 日盘面轨迹（涨跌幅 / 换手率）")
                .font(.system(size: 11.5, weight: .semibold))
                .foregroundStyle(NKKX.textTertiary)

            if item.facts.traj.isEmpty {
                Text("暂无轨迹数据")
                    .font(.system(size: 12))
                    .foregroundStyle(NKKX.textTertiary)
            } else {
                HStack(spacing: 6) {
                    ForEach(item.facts.traj) { day in
                        VStack(spacing: 2) {
                            Text(KXCopy.monthDay(day.date))
                                .font(.system(size: 10, design: .monospaced))
                                .foregroundStyle(NKKX.textTertiary)
                            Text(KXCopy.fmtPct1(day.pct))
                                .font(.system(size: 12.5, weight: .semibold, design: .monospaced))
                                .foregroundStyle(KXCopy.toneColor(day.pct))
                            Text("换 \(KXCopy.fmtTurnover(day.tr, decimals: 1))")
                                .font(.system(size: 10, design: .monospaced))
                                .foregroundStyle(NKKX.textSecondary)
                                .lineLimit(1)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 7)
                        .padding(.horizontal, 4)
                        .background(trajFill(day))
                        .overlay {
                            RoundedRectangle(cornerRadius: 7)
                                .stroke(trajBorder(day), lineWidth: 1)
                        }
                        .clipShape(RoundedRectangle(cornerRadius: 7))
                    }
                }
            }
        }
    }

    private func trajFill(_ day: KXTrajDay) -> Color {
        if day.zt { return NKKX.upFill }
        if day.dt { return NKKX.downFill }
        return NKKX.surfaceSubtle
    }

    private func trajBorder(_ day: KXTrajDay) -> Color {
        if day.zt { return NKKX.up.opacity(0.28) }
        if day.dt { return NKKX.down.opacity(0.28) }
        return NKKX.border
    }

    // MARK: 4. DeepSeek 盘后判断与筹码点评（与程序事实区隔）

    private var judgmentSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Text("DeepSeek 盘后判断与筹码点评")
                    .font(.system(size: 11.5, weight: .semibold))
                    .foregroundStyle(NKKX.accent)
                Text("主观判断")
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(NKKX.accent)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(NKKX.accentFill)
                    .clipShape(Capsule())
            }

            VStack(alignment: .leading, spacing: 8) {
                judgmentLine(label: "分区依据", text: item.judgment.why)
                judgmentLine(label: "席位点评", text: item.judgment.lhbComment)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(NKKX.deepseekFill)
            .clipShape(RoundedRectangle(cornerRadius: 9))

            HStack(alignment: .top, spacing: 4) {
                Text("观察提示：")
                    .font(.system(size: 12.5, weight: .semibold))
                    .foregroundStyle(NKKX.amberInk)
                + Text(item.judgment.watchNote)
                    .font(.system(size: 12.5))
                    .foregroundStyle(NKKX.amberInk)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(NKKX.amberBackground)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }

    private func judgmentLine(label: String, text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(label)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(NKKX.accent)
                .fixedSize()
            Text(text)
                .font(.system(size: 13))
                .foregroundStyle(NKKX.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: 5. 可展开的龙虎榜买卖前五明细

    private var lhbSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("龙虎榜买卖前五（TuShare 原始席位 + 游资名录标注）")
                .font(.system(size: 11.5, weight: .semibold))
                .foregroundStyle(NKKX.textTertiary)

            if let lhb = item.facts.lhb {
                VStack(alignment: .leading, spacing: 10) {
                    Button {
                        lhbExpanded.toggle()
                    } label: {
                        HStack(alignment: .center, spacing: 8) {
                            VStack(alignment: .leading, spacing: 3) {
                                HStack(spacing: 6) {
                                    Text(item.facts.lhbIsToday ? "今日龙虎榜" : "今日未上榜 · 最近上榜 \(lhb.date)")
                                        .font(.system(size: 12, weight: .semibold))
                                        .foregroundStyle(item.facts.lhbIsToday ? NKKX.textPrimary : NKKX.textSecondary)
                                    Text("净买 \(KXCopy.fmtWanSigned(lhb.netW))")
                                        .font(.system(size: 12, weight: .semibold, design: .monospaced))
                                        .foregroundStyle(KXCopy.toneColor(lhb.netW))
                                }
                                if let reason = lhb.reasons.first, !reason.isEmpty {
                                    Text(reason)
                                        .font(.system(size: 11))
                                        .foregroundStyle(NKKX.textSecondary)
                                        .lineLimit(2)
                                        .multilineTextAlignment(.leading)
                                }
                            }
                            Spacer(minLength: 6)
                            HStack(spacing: 4) {
                                Text(lhbExpanded ? "收起明细" : "展开前五")
                                    .font(.system(size: 11.5, weight: .medium))
                                Image(systemName: lhbExpanded ? "chevron.up" : "chevron.down")
                                    .font(.system(size: 11, weight: .semibold))
                            }
                            .foregroundStyle(NKKX.accent)
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 9)
                        .background(NKKX.surfaceSubtle)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)

                    if lhbExpanded {
                        #if os(macOS)
                        HStack(alignment: .top, spacing: 10) {
                            lhbBox(title: "买方前五", total: lhb.buyTotalW, seats: lhb.buys, isBuy: true)
                            lhbBox(title: "卖方前五", total: lhb.sellTotalW, seats: lhb.sells, isBuy: false)
                        }
                        #else
                        VStack(spacing: 10) {
                            lhbBox(title: "买方前五", total: lhb.buyTotalW, seats: lhb.buys, isBuy: true)
                            lhbBox(title: "卖方前五", total: lhb.sellTotalW, seats: lhb.sells, isBuy: false)
                        }
                        #endif
                    }
                }
            } else {
                Text(KXCopy.noLHBRecords)
                    .font(.system(size: 12))
                    .foregroundStyle(NKKX.textTertiary)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(NKKX.surfaceSubtle)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
    }

    private func lhbBox(title: String, total: Double?, seats: [KXLHBSeat], isBuy: Bool) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(title)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(isBuy ? NKKX.up : NKKX.down)
                Spacer()
                Text("合计 \(KXCopy.fmtWanUnsigned(total))")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(NKKX.textSecondary)
            }
            .padding(.bottom, 8)
            .overlay(alignment: .bottom) {
                Rectangle().fill(NKKX.border).frame(height: 1)
            }

            if seats.isEmpty {
                Text("无记录")
                    .font(.system(size: 11.5))
                    .foregroundStyle(NKKX.textTertiary)
                    .padding(.top, 8)
            } else {
                ForEach(Array(seats.enumerated()), id: \.offset) { idx, seat in
                    seatRow(seat, isBuy: isBuy)
                        .padding(.vertical, 7)
                        .overlay(alignment: .bottom) {
                            if idx < seats.count - 1 {
                                Rectangle().fill(NKKX.border.opacity(0.6)).frame(height: 1)
                            }
                        }
                }
            }
        }
        .padding(11)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .background(NKKX.surface)
        .overlay {
            RoundedRectangle(cornerRadius: 9).stroke(NKKX.border, lineWidth: 1)
        }
        .clipShape(RoundedRectangle(cornerRadius: 9))
    }

    private func seatRow(_ seat: KXLHBSeat, isBuy: Bool) -> some View {
        let primaryAmt = isBuy ? seat.buyW : seat.sellW
        let counterAmt = isBuy ? seat.sellW : seat.buyW
        let subText: String = {
            if let counter = counterAmt, counter > 0 {
                return (isBuy ? "卖 " : "买 ") + KXCopy.fmtWanUnsigned(counter)
            }
            return isBuy ? "纯买" : "纯卖"
        }()

        return HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 4) {
                Text(seat.org)
                    .font(.system(size: 12))
                    .foregroundStyle(NKKX.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                if !seat.badges.isEmpty {
                    KXFlowLayout(spacing: 4) {
                        ForEach(Array(seat.badges.enumerated()), id: \.offset) { _, badge in
                            let colors = KXCopy.seatBadgeColors(badge.kind)
                            Text(badge.text)
                                .font(.system(size: 10, weight: .semibold))
                                .foregroundStyle(colors.fg)
                                .padding(.horizontal, 5)
                                .padding(.vertical, 1.5)
                                .background(colors.bg)
                                .clipShape(RoundedRectangle(cornerRadius: 4))
                        }
                    }
                }
            }
            Spacer(minLength: 4)
            VStack(alignment: .trailing, spacing: 2) {
                Text(KXCopy.fmtWanUnsigned(primaryAmt))
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(isBuy ? NKKX.up : NKKX.down)
                Text(subText)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(NKKX.textTertiary)
            }
        }
    }
}

// MARK: 共用胶囊与流式换行布局

struct KXZonePill: View {
    let zone: KXZone

    var body: some View {
        let style: (fg: Color, bg: Color) = {
            switch zone {
            case .leader: (NKKX.up, NKKX.upFill)
            case .hot: (NKKX.accent, NKKX.accentFill)
            case .elim: (NKKX.deadInk, NKKX.deadFill)
            }
        }()
        Text(KXCopy.zoneTitle(zone))
            .font(.system(size: 10.5, weight: .semibold))
            .foregroundStyle(style.fg)
            .padding(.horizontal, 7)
            .padding(.vertical, 2.5)
            .background(style.bg)
            .clipShape(RoundedRectangle(cornerRadius: 5))
    }
}

struct KXRhythmPill: View {
    let rhythm: KXRhythm

    var body: some View {
        let colors = KXCopy.rhythmColors(rhythm.kind)
        Text(rhythm.text)
            .font(.system(size: 10.5, weight: .semibold))
            .foregroundStyle(colors.fg)
            .lineLimit(1)
            .fixedSize()
            .padding(.horizontal, 7)
            .padding(.vertical, 2.5)
            .background(colors.bg)
            .clipShape(RoundedRectangle(cornerRadius: 5))
    }
}

struct KXChangeBadgePill: View {
    let badge: KXChangeBadge

    var body: some View {
        if let spec = KXCopy.changeBadgeSpec(badge) {
            Text(spec.text)
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(spec.fg)
                .lineLimit(1)
                .fixedSize()
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(spec.bg)
                .clipShape(RoundedRectangle(cornerRadius: 4))
        }
    }
}

struct KXFactionPillView: View {
    let text: String
    let primary: Bool

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: primary ? .medium : .regular))
            .foregroundStyle(primary ? NKKX.accent : NKKX.textSecondary)
            .lineLimit(1)
            .fixedSize()
            .padding(.horizontal, 7)
            .padding(.vertical, 2.5)
            .background(primary ? NKKX.accentFill : NKKX.surfaceSubtle)
            .clipShape(RoundedRectangle(cornerRadius: 5))
    }
}

/// 轻量原生换行布局，用于标签与席位徽章。
struct KXFlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var totalWidth: CGFloat = 0

        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0, x + size.width > maxWidth {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
            totalWidth = max(totalWidth, min(maxWidth, x - spacing))
        }
        return CGSize(width: totalWidth, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var rowHeight: CGFloat = 0

        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > bounds.minX, x + size.width > bounds.maxX {
                x = bounds.minX
                y += rowHeight + spacing
                rowHeight = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}
