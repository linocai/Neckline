// KX 看板接口模型（GET /api/v4/kx/board）。时间保持字符串，展示时再按北京时间格式化。
// 数值缺了就是 nil，界面写「—」，不当成 0。

import Foundation

/// 推荐高亮。服务端按交易日历算好：今日推荐、昨日推荐（变淡）、普通。不认识的值按普通处理。
enum KXHighlight: Decodable, Equatable {
    case today
    case faded
    case normal

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        switch raw {
        case "today": self = .today
        case "faded": self = .faded
        default: self = .normal
        }
    }
}

/// 这一行的行情有没有取到。不认识的值按没取到处理，不拿旧数字冒充。
enum KXQuoteStatus: Decodable, Equatable {
    case ok
    case stale
    case missing

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        switch raw {
        case "ok": self = .ok
        case "stale": self = .stale
        default: self = .missing
        }
    }
}

/// 现价在均线的哪一侧。不认识的值按持平处理。
enum KXAveragePosition: Decodable, Equatable {
    case above
    case below
    case flat

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        switch raw {
        case "above": self = .above
        case "below": self = .below
        default: self = .flat
        }
    }
}

struct KXAverage: Decodable, Equatable {
    let value: Double
    let position: KXAveragePosition
}

struct KXAverages: Decodable, Equatable {
    let ma5: KXAverage?
    let ma10: KXAverage?
    let ma20: KXAverage?

    static let none = KXAverages(ma5: nil, ma10: nil, ma20: nil)
}

struct KXPool: Decodable, Equatable {
    let version: String
    let count: Int
    let checkedAt: String
    let stale: Bool
    let reason: String?
}

/// 报价时刻所处的竞价阶段。不认识的字符串按 unknown，不让整份看板解码失败；界面上 unknown 与空都不出标签。
enum KXSessionPhase: Decodable, Equatable {
    case callAuction
    case preOpen
    case unknown

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        switch raw {
        case "call_auction": self = .callAuction
        case "pre_open": self = .preOpen
        default: self = .unknown
        }
    }
}

struct KXSession: Decodable, Equatable {
    let tradeDate: String
    let inSession: Bool
    let refreshSeconds: Double?
    let quoteTime: String?
}

struct KXStats: Decodable, Equatable {
    let asOf: String?
}

struct KXPicks: Decodable, Equatable {
    let referenceDate: String
    let lastPickAt: String?
}

struct KXRow: Decodable, Equatable, Identifiable {
    let code: String
    var id: String { code }
    let name: String
    /// schema 1 名单才有；schema 2、3 名单为空。
    let industry: String?
    /// 题材（KX 按市场说法写的，不超过 80 字）。schema 1 名单为空。
    let theme: String?
    /// 首次入选的交易日，"YYYY-MM-DD"。
    let firstSeen: String?
    /// 入选第几个交易日：1 是首日。空表示没有这项数据。
    let heatDay: Int?
    /// 只有报价取到且是当天时才可能有。空、或不认识的值，都不出阶段标签。
    let sessionPhase: KXSessionPhase?
    let highlight: KXHighlight
    let lastPickedAt: String?
    let quoteStatus: KXQuoteStatus
    let note: String?

    let price: Double?
    let changePercent: Double?
    let changeAmount: Double?
    let open: Double?
    let high: Double?
    let low: Double?
    let previousClose: Double?
    let amplitudePercent: Double?
    let turnoverRatePercent: Double?
    /// 成交额，亿元。
    let turnoverYi: Double?
    /// 成交量，手。
    let volumeLots: Double?
    let openChangePercent: Double?
    /// 现价在今日区间的位置，0 到 1。最高等于最低时服务端留空。
    let rangePosition: Double?
    /// 距今日最高的回撤，小于等于 0。
    let pullbackFromHighPercent: Double?
    let quoteTime: String?

    let change5dPercent: Double?
    let change20dPercent: Double?
    let avgTurnover20Yi: Double?
    let volumeRatio: Double?
    let totalMvYi: Double?
    let circMvYi: Double?
    let averages: KXAverages
}

struct KXBoard: Decodable, Equatable {
    let generatedAt: String
    let pool: KXPool
    let session: KXSession
    let stats: KXStats
    let picks: KXPicks
    let gaps: [String]
    /// 服务端已排好：今日推荐、昨日推荐，再是按涨跌幅的其余。按这个顺序显示。
    let rows: [KXRow]
}

/// 看板的三个区，从上到下。
enum KXSection: Hashable, CaseIterable {
    case today
    case faded
    case all

    var title: String {
        switch self {
        case .today: return "今日推荐"
        case .faded: return "昨日推荐"
        case .all: return "全部"
        }
    }
}

extension KXHighlight {
    var section: KXSection {
        switch self {
        case .today: return .today
        case .faded: return .faded
        case .normal: return .all
        }
    }
}

struct KXSectionRows: Equatable {
    let section: KXSection
    let rows: [KXRow]
}

extension KXBoard {
    func rows(in section: KXSection) -> [KXRow] {
        rows.filter { $0.highlight.section == section }
    }

    /// 非空的区，保持服务端给的行序。空区不出现。
    var sections: [KXSectionRows] {
        KXSection.allCases.compactMap { section in
            let chosen = rows(in: section)
            return chosen.isEmpty ? nil : KXSectionRows(section: section, rows: chosen)
        }
    }
}
