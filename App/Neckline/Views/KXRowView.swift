// 手机看板的一行。名称列固定在左，数据列按当前分组排在右边，尺寸、颜色与样图一致。

import SwiftUI

extension KXRow {
    var isToday: Bool { highlight == .today }
    var isFaded: Bool { highlight == .faded }
    /// 报价不是当日的行（停牌等）：数字是旧的，不上涨跌色。
    var isMuted: Bool { quoteStatus == .stale }
    var hasNoQuote: Bool { quoteStatus == .missing }

    /// 热度档位。没有这项数据（或不是正整数）时为空。
    var heat: KXHeat? { KXHeat(day: heatDay) }
    /// 一字板：只有明确写着不能买入才算；没有这项数据不当成一字板。
    var isOneWordBoard: Bool { buyable == false }
    /// 题材文字。空串、全是空白都当没有。
    var themeText: String? {
        guard let text = theme?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
        return text
    }
    /// 手机看板的第二行：热度、一字板、题材有任何一样就加；三样都没有（schema 1 名单）行高保持 52。
    var hasSecondLine: Bool { heat != nil || isOneWordBoard || themeText != nil }
}

/// 热度徽标的三档：入选首日；第 2 到 4 天；第 5 天起（热了很久）。
enum KXHeat: Equatable {
    case firstDay
    case warm(day: Int)
    case hot(day: Int)

    init?(day: Int?) {
        guard let day, day >= 1 else { return nil }
        switch day {
        case 1: self = .firstDay
        case 2...4: self = .warm(day: day)
        default: self = .hot(day: day)
        }
    }

    var text: String {
        switch self {
        case .firstDay: return "首日"
        case .warm(let day), .hot(let day): return "第\(day)天"
        }
    }
}

/// 区间条的几何，0 到 1。竖线是现价，色段从开盘到现价。缺数据就少画，不猜。
struct KXRangeGeometry: Equatable {
    let marker: Double?
    let segment: Segment?

    struct Segment: Equatable {
        let start: Double
        let end: Double
        let rising: Bool
    }

    init(row: KXRow) {
        guard let position = row.rangePosition, position.isFinite else {
            marker = nil
            segment = nil
            return
        }
        let current = min(max(position, 0), 1)
        marker = current
        if let open = row.open, let high = row.high, let low = row.low, high > low {
            let openPosition = min(max((open - low) / (high - low), 0), 1)
            segment = Segment(start: min(current, openPosition), end: max(current, openPosition), rising: current >= openPosition)
        } else {
            segment = nil
        }
    }
}

struct KXRowView: View {
    let row: KXRow
    let group: KXColumnGroup

    var body: some View {
        content
            .padding(.horizontal, KXMetrics.inset)
            .background { background }
            .overlay(alignment: .leading) { bar }
            .overlay(alignment: .bottom) {
                Rectangle().fill(NKMorning.hairline).frame(height: KXMetrics.hairline)
            }
            .accessibilityElement(children: .combine)
    }

    /// 第一行（名称加数据列）。没有第二行时就是整行，高 52；有第二行时整行按样图 A1 的 .r2 排：
    /// 上内边距 7、第一行 34、间距 4、第二行 17、下内边距 6，一共 68。
    @ViewBuilder private var content: some View {
        if row.hasSecondLine {
            VStack(alignment: .leading, spacing: KXMetrics.secondLineGap) {
                firstLine
                    .frame(minHeight: KXMetrics.firstLineHeight)
                KXSecondLine(row: row)
            }
            .padding(.top, KXMetrics.twoLineTop)
            .padding(.bottom, KXMetrics.twoLineBottom)
        } else {
            firstLine
                .frame(height: KXMetrics.rowHeight)
        }
    }

    private var firstLine: some View {
        HStack(spacing: 0) {
            KXNameCell(row: row)
            ForEach(group.columns, id: \.self) { column in
                KXCell(row: row, column: column)
                    .frame(minWidth: column.minWidth, alignment: .trailing)
                    .fixedSize(horizontal: true, vertical: false)
                    .padding(.leading, column.leadingGap)
            }
        }
    }

    private var background: some View {
        ZStack {
            NKMorning.card
            switch row.highlight {
            case .today: NKKX.todayFill
            case .faded: NKKX.ydayFill
            case .normal: Color.clear
            }
        }
    }

    @ViewBuilder private var bar: some View {
        if row.highlight != .normal {
            UnevenRoundedRectangle(topLeadingRadius: 0, bottomLeadingRadius: 0, bottomTrailingRadius: 2, topTrailingRadius: 2, style: .continuous)
                .fill(NKKX.bar)
                .opacity(row.isFaded ? NKKX.ydayBarOpacity : 1)
                .frame(width: 3)
                .padding(.vertical, 8)
        }
    }
}

/// 手机看板的第二行：热度徽标、一字板标签、题材。题材单行，放不下用省略号收尾，徽标不压缩、不换行。
struct KXSecondLine: View {
    let row: KXRow

    var body: some View {
        HStack(spacing: KXMetrics.badgeGap) {
            if let heat = row.heat {
                KXHeatBadge(heat: heat)
            }
            if row.isOneWordBoard {
                KXOneWordBoardBadge()
            }
            if let theme = row.themeText {
                Text(theme)
                    .font(NKKXFont.theme)
                    .foregroundStyle(row.isFaded ? NKMorning.textTertiary : NKMorning.textSecondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(maxWidth: .infinity, minHeight: KXMetrics.secondLineHeight, alignment: .leading)
            }
        }
        .frame(maxWidth: .infinity, minHeight: KXMetrics.secondLineHeight, alignment: .leading)
    }
}

/// 热度徽标：首日蓝色描边、透明底；第 2 到 4 天灰底；第 5 天起浅红底。手机第二行与 macOS 表格共用。
struct KXHeatBadge: View {
    let heat: KXHeat

    var body: some View {
        Text(heat.text)
            .font(NKKXFont.badge)
            .foregroundStyle(foreground)
            .lineLimit(1)
            .frame(height: KXMetrics.badgeTextHeight)
            .padding(.vertical, KXMetrics.badgePaddingV)
            .padding(.horizontal, KXMetrics.badgePaddingH)
            .background(fill, in: shape)
            .overlay {
                if heat == .firstDay {
                    shape.strokeBorder(NKMorning.accent, lineWidth: 1)
                }
            }
            .fixedSize()
    }

    private var shape: RoundedRectangle {
        RoundedRectangle(cornerRadius: KXMetrics.badgeRadius, style: .continuous)
    }

    private var foreground: Color {
        switch heat {
        case .firstDay: return NKMorning.accent
        case .warm: return NKMorning.textSecondary
        case .hot: return NKKX.up
        }
    }

    private var fill: Color {
        switch heat {
        case .firstDay: return .clear
        case .warm: return NKMorning.tagFill
        case .hot: return NKKX.heatHotFill
        }
    }
}

/// 一字板标签：只在不能买入时出现，琥珀色。
struct KXOneWordBoardBadge: View {
    var body: some View {
        Text("一字板")
            .font(NKKXFont.badge)
            .foregroundStyle(NKKX.lockText)
            .lineLimit(1)
            .frame(height: KXMetrics.badgeTextHeight)
            .padding(.vertical, KXMetrics.badgePaddingV)
            .padding(.horizontal, KXMetrics.badgePaddingH)
            .background(NKKX.lockFill, in: RoundedRectangle(cornerRadius: KXMetrics.badgeRadius, style: .continuous))
            .fixedSize()
    }
}

/// 名称列：名称加代码，占数据列剩下的宽度。数据列按各自的宽度排在右边，名称在窄屏上最多缩小一点再截断。
struct KXNameCell: View {
    let row: KXRow

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(row.name)
                .font(row.isToday ? NKKXFont.nameToday : NKKXFont.name)
                .foregroundStyle(row.isFaded ? NKMorning.textSecondary : NKMorning.textPrimary)
                .lineLimit(1)
                .minimumScaleFactor(KXMetrics.nameMinimumScale)
                .truncationMode(.tail)
            Text(row.code)
                .font(NKKXFont.code)
                .foregroundStyle(NKMorning.textTertiary)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

extension KXTrend {
    var color: Color {
        switch self {
        case .up: return NKKX.up
        case .down: return NKKX.down
        case .flat: return NKKX.flat
        }
    }
}

/// 一个数据格。缺数据的格子写「—」。
struct KXCell: View {
    let row: KXRow
    let column: KXColumn

    var body: some View {
        switch column {
        case .price:
            value(KXCopy.fixed(row.price, 2), trend: KXTrend(row.changePercent), bold: row.isToday)
        case .changePercent:
            KXPill(text: KXCopy.signedPercent(row.changePercent), trend: KXTrend(row.changePercent), muted: row.isMuted)
        case .range:
            KXRangeCell(row: row)
        case .volumeRatio:
            KXRatioCell(ratio: row.volumeRatio)
        case .averages:
            KXAverageDots(averages: row.averages)
        case .openChange:
            value(KXCopy.signedPercent(row.openChangePercent), trend: KXTrend(row.openChangePercent))
        case .amplitude:
            value(KXCopy.percent(row.amplitudePercent))
        case .turnoverRate:
            value(KXCopy.percent(row.turnoverRatePercent))
        case .turnover:
            value(KXCopy.yi(row.turnoverYi))
        case .volume:
            value(KXCopy.volume(lots: row.volumeLots))
        case .avgTurnover20:
            value(KXCopy.yi(row.avgTurnover20Yi))
        case .change5d:
            value(KXCopy.signedPercent(row.change5dPercent, 1), trend: KXTrend(row.change5dPercent, digits: 1))
        case .change20d:
            value(KXCopy.signedPercent(row.change20dPercent, 1), trend: KXTrend(row.change20dPercent, digits: 1))
        case .circMv:
            value(KXCopy.marketValue(yi: row.circMvYi))
        case .totalMv:
            value(KXCopy.marketValue(yi: row.totalMvYi))
        }
    }

    /// 数字：缺了写「—」用浅灰；报价是旧的不上涨跌色。
    private func value(_ text: String, trend: KXTrend? = nil, bold: Bool = false) -> some View {
        let missing = text == KXCopy.dash
        let color: Color
        if missing || (row.isMuted && trend != nil) {
            color = NKMorning.textTertiary
        } else if let trend {
            color = trend.color
        } else {
            color = NKMorning.textPrimary
        }
        return Text(text)
            .font(bold ? NKKXFont.cellBold : NKKXFont.cell)
            .foregroundStyle(color)
            .lineLimit(1)
            .fixedSize()
    }
}

/// 涨跌幅色块。比列宽长时向左长出，不挤别的列。
struct KXPill: View {
    let text: String
    let trend: KXTrend
    let muted: Bool

    var body: some View {
        if text == KXCopy.dash {
            Text(text)
                .font(NKKXFont.cell)
                .foregroundStyle(NKMorning.textTertiary)
        } else {
            Text(text)
                .font(NKKXFont.pill)
                .foregroundStyle(muted ? NKMorning.textTertiary : NKKX.pillText)
                .lineLimit(1)
                .padding(.horizontal, 5)
                .padding(.vertical, 3)
                .frame(minWidth: 56, alignment: .trailing)
                .background(muted ? NKMorning.tagFill : trend.color, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                .fixedSize()
        }
    }
}

/// 今日区间条：竖线是现价，色段是开盘到现价，下方写距今日最高。
struct KXRangeCell: View {
    let row: KXRow

    var body: some View {
        if row.hasNoQuote {
            Text(KXCopy.dash)
                .font(NKKXFont.cell)
                .foregroundStyle(NKMorning.textTertiary)
        } else {
            let geometry = KXRangeGeometry(row: row)
            let width = KXMetrics.rangeTrackWidth
            VStack(alignment: .trailing, spacing: 3) {
                // 轨道 50×6。色段和竖线画在轨道的 overlay 里，竖线比轨道高也不会把轨道撑高。
                RoundedRectangle(cornerRadius: 3, style: .continuous)
                    .fill(NKMorning.tagFill)
                    .frame(width: width, height: 6)
                    .overlay(alignment: .leading) {
                        if let segment = geometry.segment {
                            RoundedRectangle(cornerRadius: 3, style: .continuous)
                                .fill(segment.rising ? NKKX.up : NKKX.down)
                                .opacity(0.55)
                                .frame(width: max(2, (segment.end - segment.start) * width), height: 6)
                                .offset(x: segment.start * width)
                        }
                    }
                    .overlay(alignment: .leading) {
                        if let marker = geometry.marker {
                            RoundedRectangle(cornerRadius: 2, style: .continuous)
                                .fill(row.isMuted ? NKMorning.textTertiary : NKMorning.textPrimary)
                                .frame(width: 3, height: 12)
                                .offset(x: marker * width - 1.5)
                        }
                    }
                Text(KXCopy.pullback(row.pullbackFromHighPercent))
                    .font(NKKXFont.rangeNote)
                    .foregroundStyle(NKMorning.textTertiary)
                    .lineLimit(1)
                    .fixedSize()
            }
        }
    }
}

/// 量能倍数：短条加「1.5×」。条在 3 倍时满。
struct KXRatioCell: View {
    let ratio: Double?

    var body: some View {
        VStack(alignment: .trailing, spacing: 3) {
            Capsule()
                .fill(NKMorning.tagFill)
                .frame(width: KXMetrics.ratioBarWidth, height: 5)
                .overlay(alignment: .leading) {
                    if let ratio, ratio.isFinite {
                        Capsule()
                            .fill(NKMorning.accent)
                            .opacity(0.75)
                            .frame(width: KXMetrics.ratioBarWidth * min(max(ratio / 3, 0), 1), height: 5)
                    }
                }
            Text(KXCopy.ratio(ratio))
                .font(NKKXFont.ratio)
                .foregroundStyle(NKMorning.textSecondary)
                .lineLimit(1)
                .fixedSize()
        }
    }
}

/// 均线三点：5、10、20 日。现价在线上红、在线下绿，持平或没有数据灰。
struct KXAverageDots: View {
    let averages: KXAverages

    var body: some View {
        HStack(spacing: 3) {
            dot(averages.ma5)
            dot(averages.ma10)
            dot(averages.ma20)
        }
    }

    private func dot(_ average: KXAverage?) -> some View {
        Circle()
            .fill(Self.color(average))
            .frame(width: 6, height: 6)
    }

    static func color(_ average: KXAverage?) -> Color {
        switch average?.position {
        case .above: return NKKX.up
        case .below: return NKKX.down
        case .flat, .none: return NKKX.flat
        }
    }
}
