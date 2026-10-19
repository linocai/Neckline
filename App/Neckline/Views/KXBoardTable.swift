// macOS 的 KX 看板：一张表显示全部列。今日推荐、昨日推荐两个区原样置顶；点列头排序只作用于「全部」那一区。

#if os(macOS)
import SwiftUI

/// 表里的列。顺序就是显示顺序。
enum KXTableColumn: CaseIterable, Hashable, Identifiable {
    case name
    case industry
    case price
    case changePercent
    case changeAmount
    case openChange
    case open
    case high
    case low
    case previousClose
    case amplitude
    case turnoverRate
    case turnover
    case volume
    case range
    case pullback
    case volumeRatio
    case avgTurnover20
    case change5d
    case change20d
    case circMv
    case totalMv
    case ma5
    case ma10
    case ma20

    var id: Self { self }

    var title: String {
        switch self {
        case .name: return "名称"
        case .industry: return "行业"
        case .price: return "现价"
        case .changePercent: return "涨跌幅"
        case .changeAmount: return "涨跌额"
        case .openChange: return "开盘涨跌"
        case .open: return "开盘"
        case .high: return "最高"
        case .low: return "最低"
        case .previousClose: return "昨收"
        case .amplitude: return "振幅"
        case .turnoverRate: return "换手率"
        case .turnover: return "成交额"
        case .volume: return "成交量"
        case .range: return "区间"
        case .pullback: return "距高"
        case .volumeRatio: return "量能倍数"
        case .avgTurnover20: return "20日均额"
        case .change5d: return "5日"
        case .change20d: return "20日"
        case .circMv: return "流通市值"
        case .totalMv: return "总市值"
        case .ma5: return "5日线"
        case .ma10: return "10日线"
        case .ma20: return "20日线"
        }
    }

    var width: ClosedRange<CGFloat> {
        switch self {
        case .name: return 110...180
        case .industry: return 80...140
        case .changePercent: return 70...90
        case .range: return 90...120
        case .volumeRatio: return 80...110
        case .turnover, .volume, .avgTurnover20, .circMv, .totalMv: return 76...110
        default: return 64...90
        }
    }

    var alignment: Alignment { self == .name || self == .industry ? .leading : .trailing }

    /// 排序用的值。缺数据的行不论升降都排最后。
    func sortKey(_ row: KXRow) -> KXSortKey {
        switch self {
        case .name: return .text(row.name)
        case .industry: return .text(row.industry ?? "")
        case .price: return .number(row.price)
        case .changePercent: return .number(row.changePercent)
        case .changeAmount: return .number(row.changeAmount)
        case .openChange: return .number(row.openChangePercent)
        case .open: return .number(row.open)
        case .high: return .number(row.high)
        case .low: return .number(row.low)
        case .previousClose: return .number(row.previousClose)
        case .amplitude: return .number(row.amplitudePercent)
        case .turnoverRate: return .number(row.turnoverRatePercent)
        case .turnover: return .number(row.turnoverYi)
        case .volume: return .number(row.volumeLots)
        case .range: return .number(row.rangePosition)
        case .pullback: return .number(row.pullbackFromHighPercent)
        case .volumeRatio: return .number(row.volumeRatio)
        case .avgTurnover20: return .number(row.avgTurnover20Yi)
        case .change5d: return .number(row.change5dPercent)
        case .change20d: return .number(row.change20dPercent)
        case .circMv: return .number(row.circMvYi)
        case .totalMv: return .number(row.totalMvYi)
        case .ma5: return .number(row.averages.ma5?.value)
        case .ma10: return .number(row.averages.ma10?.value)
        case .ma20: return .number(row.averages.ma20?.value)
        }
    }
}

enum KXSortKey: Equatable {
    case number(Double?)
    case text(String)
}

struct KXRowComparator: SortComparator, Hashable {
    var column: KXTableColumn
    var order: SortOrder = .forward

    func compare(_ lhs: KXRow, _ rhs: KXRow) -> ComparisonResult {
        let result: ComparisonResult
        switch (column.sortKey(lhs), column.sortKey(rhs)) {
        case (.number(let left), .number(let right)):
            switch (left, right) {
            case (nil, nil): return .orderedSame
            case (nil, _): return .orderedDescending
            case (_, nil): return .orderedAscending
            case (let a?, let b?):
                result = a < b ? .orderedAscending : (a > b ? .orderedDescending : .orderedSame)
            }
        case (.text(let left), .text(let right)):
            result = left.localizedStandardCompare(right)
        default:
            return .orderedSame
        }
        return order == .forward ? result : Self.reversed(result)
    }

    private static func reversed(_ result: ComparisonResult) -> ComparisonResult {
        switch result {
        case .orderedAscending: return .orderedDescending
        case .orderedDescending: return .orderedAscending
        case .orderedSame: return .orderedSame
        }
    }
}

struct KXBoardTable: View {
    @Bindable var kx: KXBoardModel
    var openSettings: () -> Void = {}
    @State private var sortOrder: [KXRowComparator] = []

    var body: some View {
        content
            .navigationTitle("KX")
            .navigationSubtitle(subtitle)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background { NKMorning.page }
    }

    private var subtitle: String {
        guard let board = kx.board else { return "" }
        return KXCopy.meta(board: board, refreshFailed: kx.refreshFailed)
    }

    @ViewBuilder private var content: some View {
        switch kx.state {
        case .loading:
            MorningLoadingView()
        case .error(let error):
            MorningStatusView(message: KXCopy.message(for: error), retry: { Task { await kx.refresh() } }, openSettings: openSettings)
        case .ready(let board):
            if board.rows.isEmpty {
                MorningStatusView(message: KXCopy.emptyPoolMessage, retry: { Task { await kx.refresh() } })
            } else {
                table(board)
                    .safeAreaInset(edge: .bottom, spacing: 0) {
                        KXFootView(lines: KXCopy.footerLines(board: board))
                    }
            }
        }
    }

    private func table(_ board: KXBoard) -> some View {
        let today = board.rows(in: .today)
        let faded = board.rows(in: .faded)
        let others = board.rows(in: .all).sorted(using: sortOrder)
        return Table(of: KXRow.self, sortOrder: $sortOrder) {
            TableColumnForEach(KXTableColumn.allCases) { column in
                TableColumn(column.title, sortUsing: KXRowComparator(column: column)) { row in
                    KXTableCell(row: row, column: column)
                }
                .width(min: column.width.lowerBound, ideal: column.width.lowerBound + 6, max: column.width.upperBound)
                .alignment(column.alignment == .leading ? .leading : .trailing)
            }
        } rows: {
            if !today.isEmpty {
                Section("今日推荐 · \(today.count) 只") {
                    ForEach(today, id: \.code) { TableRow($0) }
                }
            }
            if !faded.isEmpty {
                Section("昨日推荐 · \(faded.count) 只") {
                    ForEach(faded, id: \.code) { TableRow($0) }
                }
            }
            if !others.isEmpty {
                Section("全部 · \(others.count) 只") {
                    ForEach(others, id: \.code) { TableRow($0) }
                }
            }
        }
        .tableStyle(.inset(alternatesRowBackgrounds: false))
    }
}

/// 表里的一个格子。涨红跌绿平灰、今日推荐加粗，和手机看板同一套语言；缺数据写「—」。
struct KXTableCell: View {
    let row: KXRow
    let column: KXTableColumn

    var body: some View {
        content
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: column.alignment)
            .padding(.vertical, 2)
            // 表格的格子之间有固定的空隙；底色向两边各多画出半个空隙，一行才连成一整条。
            .background { highlightFill.padding(.horizontal, -8.5).padding(.vertical, -3) }
    }

    @ViewBuilder private var content: some View {
        switch column {
        case .name:
            HStack(spacing: 8) {
                if row.highlight != .normal {
                    Capsule()
                        .fill(NKKX.bar)
                        .opacity(row.isFaded ? NKKX.ydayBarOpacity : 1)
                        .frame(width: 3, height: 22)
                }
                VStack(alignment: .leading, spacing: 0) {
                    Text(row.name)
                        .font(.system(size: 13, weight: row.isToday ? .bold : .medium))
                        .foregroundStyle(row.isFaded ? NKMorning.textSecondary : NKMorning.textPrimary)
                        .lineLimit(1)
                    Text(row.code)
                        .font(.system(size: 11).monospacedDigit())
                        .foregroundStyle(NKMorning.textTertiary)
                }
            }
        case .industry:
            Text(row.industry ?? KXCopy.dash)
                .font(.system(size: 13))
                .foregroundStyle(NKMorning.textSecondary)
                .lineLimit(1)
        case .price:
            number(KXCopy.fixed(row.price, 2), trend: KXTrend(row.changePercent), bold: row.isToday)
        case .changePercent:
            KXPill(text: KXCopy.signedPercent(row.changePercent), trend: KXTrend(row.changePercent), muted: row.isMuted)
        case .changeAmount:
            number(KXCopy.signed(row.changeAmount, 2), trend: KXTrend(row.changeAmount))
        case .openChange:
            number(KXCopy.signedPercent(row.openChangePercent), trend: KXTrend(row.openChangePercent))
        case .open:
            number(KXCopy.fixed(row.open, 2))
        case .high:
            number(KXCopy.fixed(row.high, 2))
        case .low:
            number(KXCopy.fixed(row.low, 2))
        case .previousClose:
            number(KXCopy.fixed(row.previousClose, 2))
        case .amplitude:
            number(KXCopy.percent(row.amplitudePercent))
        case .turnoverRate:
            number(KXCopy.percent(row.turnoverRatePercent))
        case .turnover:
            number(KXCopy.yi(row.turnoverYi))
        case .volume:
            number(KXCopy.volume(lots: row.volumeLots))
        case .range:
            KXRangeCell(row: row)
        case .pullback:
            number(KXCopy.percent(row.pullbackFromHighPercent, 1))
        case .volumeRatio:
            KXRatioCell(ratio: row.volumeRatio)
        case .avgTurnover20:
            number(KXCopy.yi(row.avgTurnover20Yi))
        case .change5d:
            number(KXCopy.signedPercent(row.change5dPercent, 1), trend: KXTrend(row.change5dPercent, digits: 1))
        case .change20d:
            number(KXCopy.signedPercent(row.change20dPercent, 1), trend: KXTrend(row.change20dPercent, digits: 1))
        case .circMv:
            number(KXCopy.marketValue(yi: row.circMvYi))
        case .totalMv:
            number(KXCopy.marketValue(yi: row.totalMvYi))
        case .ma5:
            average(row.averages.ma5)
        case .ma10:
            average(row.averages.ma10)
        case .ma20:
            average(row.averages.ma20)
        }
    }

    private func number(_ text: String, trend: KXTrend? = nil, bold: Bool = false) -> some View {
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
            .font(.system(size: 13, weight: bold ? .bold : .regular).monospacedDigit())
            .foregroundStyle(color)
            .lineLimit(1)
    }

    /// 均线值，颜色表示现价在线上（红）还是线下（绿）。
    private func average(_ average: KXAverage?) -> some View {
        Text(KXCopy.fixed(average?.value, 2))
            .font(.system(size: 13).monospacedDigit())
            .foregroundStyle(average == nil ? NKMorning.textTertiary : KXAverageDots.color(average))
            .lineLimit(1)
    }

    @ViewBuilder private var highlightFill: some View {
        switch row.highlight {
        case .today: NKKX.todayFill
        case .faded: NKKX.ydayFill
        case .normal: Color.clear
        }
    }
}
#endif
