// KX 看板的列与分组。手机上名称列固定在左侧，其余列按组切换，不做横向滑动。

import CoreGraphics

/// 手机看板的四组列，对应顶部分段控件。
enum KXColumnGroup: CaseIterable, Hashable {
    case overview
    case today
    case volume
    case trend

    var title: String {
        switch self {
        case .overview: return "概览"
        case .today: return "今日"
        case .volume: return "量能"
        case .trend: return "趋势"
        }
    }

    /// 开、高、低、昨收不单独占列，在概览的区间条里表达。
    var columns: [KXColumn] {
        switch self {
        case .overview: return [.price, .changePercent, .range, .volumeRatio, .averages]
        case .today: return [.openChange, .amplitude, .turnoverRate, .turnover]
        case .volume: return [.turnover, .volume, .avgTurnover20, .volumeRatio]
        case .trend: return [.change5d, .change20d, .circMv, .totalMv]
        }
    }
}

/// 手机看板上的一个数据列。
enum KXColumn: Hashable {
    case price
    case changePercent
    case range
    case volumeRatio
    case averages
    case openChange
    case amplitude
    case turnoverRate
    case turnover
    case volume
    case avgTurnover20
    case change5d
    case change20d
    case circMv
    case totalMv

    /// 列头小字。量能倍数在概览里叫「量能」，在量能组里叫「倍数」。
    func heading(in group: KXColumnGroup) -> String {
        switch self {
        case .price: return "现价"
        case .changePercent: return "涨跌幅"
        case .range: return "区间"
        case .volumeRatio: return group == .overview ? "量能" : "倍数"
        case .averages: return "均线"
        case .openChange: return "开盘涨跌"
        case .amplitude: return "振幅"
        case .turnoverRate: return "换手率"
        case .turnover: return "成交额"
        case .volume: return "成交量"
        case .avgTurnover20: return "20日均额"
        case .change5d: return "5日"
        case .change20d: return "20日"
        case .circMv: return "流通市值"
        case .totalMv: return "总市值"
        }
    }

    /// 列的最小宽度，点。取样图 CSS 的列宽：数字比这宽时这一列跟着变宽，把左边的列往左推（样图的弹性布局也是这样），
    /// 所以平常的数字与样图的位置一致，极端的数字（现价 1234.56、涨跌幅 +20.00%、成交量 1234万手……）不会被截断。
    var minWidth: CGFloat {
        switch self {
        case .price: return 52
        case .changePercent: return 56
        case .range: return 54
        case .volumeRatio: return 36
        case .averages: return 24
        case .openChange: return 58
        case .amplitude, .turnoverRate: return 55
        case .turnover: return 64
        case .volume: return 64
        case .avgTurnover20: return 50
        case .change5d, .change20d: return 56
        case .circMv, .totalMv: return 54
        }
    }

    /// 与左边一列（第一列是名称）的固定间距，点。数字列之间任何时候都至少留 6，包括最宽的数字；
    /// 区间条与涨跌幅色块之间、均线三点之前沿用样图的 8 和 5（它们不是数字）。
    var leadingGap: CGFloat {
        switch self {
        case .range: return 8
        case .averages: return 5
        default: return 6
        }
    }
}

enum KXMetrics {
    /// 名称列占数据列剩下的宽度；放不下时名称最多缩到这个比例再截断。
    static let nameMinimumScale: CGFloat = 0.85
    /// 名称与第一列数字之间至少留的空白，也是数字列之间的最小空白。
    static let columnGap: CGFloat = 6
    static let rowHeight: CGFloat = 52
    static let inset: CGFloat = 16
    static let hairline: CGFloat = 0.5
    static let rangeTrackWidth: CGFloat = 50
    static let ratioBarWidth: CGFloat = 32
}
