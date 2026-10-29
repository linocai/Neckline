// KX 看板的文案与数字格式。时间一律按北京时间显示，不读设备时区；缺数据写「—」。

import Foundation

/// 涨跌方向。按显示的位数取整后判断：显示 0.00 的就是平。
enum KXTrend: Equatable {
    case up
    case down
    case flat

    init(_ value: Double?, digits: Int = 2) {
        guard let value else {
            self = .flat
            return
        }
        let scale = pow(10, Double(digits))
        let rounded = (value * scale).rounded() / scale
        self = rounded > 0 ? .up : (rounded < 0 ? .down : .flat)
    }
}

enum KXCopy {
    static let dash = "—"

    // MARK: 数字

    /// 固定位数。保留符号；显示为零的不带负号。
    static func fixed(_ value: Double?, _ digits: Int) -> String {
        guard let value, value.isFinite else { return dash }
        return format(round(value, digits), digits)
    }

    /// 带符号：正数前加 +，零不带符号。
    static func signed(_ value: Double?, _ digits: Int) -> String {
        guard let value, value.isFinite else { return dash }
        let rounded = round(value, digits)
        return (rounded > 0 ? "+" : "") + format(rounded, digits)
    }

    static func percent(_ value: Double?, _ digits: Int = 2) -> String {
        guard let value, value.isFinite else { return dash }
        return fixed(value, digits) + "%"
    }

    static func signedPercent(_ value: Double?, _ digits: Int = 2) -> String {
        guard let value, value.isFinite else { return dash }
        return signed(value, digits) + "%"
    }

    /// 成交额、日均成交额，亿元，一位小数。
    static func yi(_ value: Double?) -> String {
        guard let value, value.isFinite else { return dash }
        return fixed(value, 1) + "亿"
    }

    /// 成交量，手。一万手以上写「万手」，不到的写「手」。
    static func volume(lots: Double?) -> String {
        guard let lots, lots.isFinite else { return dash }
        if lots >= 10_000 {
            let wan = lots / 10_000
            return fixed(wan, wan >= 100 ? 0 : 1) + "万手"
        }
        return fixed(lots, 0) + "手"
    }

    /// 市值，亿元。一万亿以上写「万亿」。
    static func marketValue(yi value: Double?) -> String {
        guard let value, value.isFinite else { return dash }
        if value >= 10_000 { return fixed(value / 10_000, 2) + "万亿" }
        return fixed(value, value >= 100 ? 0 : 1) + "亿"
    }

    static func ratio(_ value: Double?) -> String {
        guard let value, value.isFinite else { return dash }
        return fixed(value, 1) + "×"
    }

    /// 区间条下面的小字，写的是距今日最高跌了多少（小于等于 0）。
    static func pullback(_ value: Double?) -> String {
        guard let value, value.isFinite else { return dash }
        return "距高 " + percent(value, 1)
    }

    private static func round(_ value: Double, _ digits: Int) -> Double {
        let scale = pow(10, Double(digits))
        let rounded = (value * scale).rounded() / scale
        return rounded == 0 ? 0 : rounded
    }

    private static func format(_ value: Double, _ digits: Int) -> String {
        String(format: "%.\(digits)f", value)
    }

    // MARK: 时间

    /// 「HH:mm」。解析不了返回 nil。
    static func clock(_ iso: String?) -> String? {
        guard let date = KXClock.parse(iso) else { return nil }
        return KXClock.string(date, "HH:mm")
    }

    static func clockWithSeconds(_ iso: String?) -> String? {
        guard let date = KXClock.parse(iso) else { return nil }
        return KXClock.string(date, "HH:mm:ss")
    }

    /// 「10/19 10:28」。
    static func monthDayClock(_ iso: String?) -> String? {
        guard let date = KXClock.parse(iso) else { return nil }
        return KXClock.string(date, "M/d HH:mm")
    }

    // MARK: 页眉与页脚

    /// 右上角小字：「10:32 更新 · 盘中」。刷新没成功时，后半写「刷新失败」，前半仍是最近一次成功的时间。
    static func meta(board: KXBoard, refreshFailed: Bool) -> String {
        let status = refreshFailed ? "刷新失败" : (board.session.inSession ? "盘中" : "已收盘")
        guard let time = clock(board.generatedAt) else { return status }
        return "\(time) 更新 · \(status)"
    }

    /// 底部小字。名单版本与核对时间、报价时间、最后收到推荐的时间；名单没能核对和整体缺口如实写在这里。
    static func footerLines(board: KXBoard) -> [String] {
        var first: [String] = []
        let pool = board.pool
        if pool.stale {
            let checked = monthDayClock(pool.checkedAt) ?? pool.checkedAt
            first.append("\(pool.version) · 名单未能核对，最后核对 \(checked)")
        } else {
            first.append("名单 \(pool.version) · \(sameDayOrDated(pool.checkedAt, reference: board.generatedAt)) 核对")
        }
        if let quote = board.session.quoteTime {
            first.append("报价 \(quoteClock(quote, reference: board.generatedAt))")
        }
        var lines = [first.joined(separator: "　")]
        if let last = monthDayClock(board.picks.lastPickAt) {
            lines.append("最后收到推荐 \(last)")
        } else {
            lines.append("最后收到推荐 \(dash)")
        }
        let gaps = board.gaps.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        if !gaps.isEmpty {
            lines.append(gaps.joined(separator: " "))
        }
        return lines
    }

    /// 与参照时刻同一天只写时分，不同天加上月日。
    private static func sameDayOrDated(_ iso: String, reference: String) -> String {
        guard let date = KXClock.parse(iso) else { return iso }
        if let ref = KXClock.parse(reference), KXClock.sameDay(date, ref) {
            return KXClock.string(date, "HH:mm")
        }
        return KXClock.string(date, "M/d HH:mm")
    }

    private static func quoteClock(_ iso: String, reference: String) -> String {
        guard let date = KXClock.parse(iso) else { return iso }
        if let ref = KXClock.parse(reference), !KXClock.sameDay(date, ref) {
            return KXClock.string(date, "M/d HH:mm")
        }
        return KXClock.string(date, "HH:mm:ss")
    }

    // MARK: 竞价阶段

    /// 手机第二行与 macOS 现价格里的小字。不认识的阶段和空都不出标签。
    static func sessionPhaseLabel(_ phase: KXSessionPhase?) -> String? {
        switch phase {
        case .callAuction: return "竞价中"
        case .preOpen: return "开盘价"
        case .unknown, .none: return nil
        }
    }

    // MARK: 状态文案

    static let emptyPoolTitle = "名单里还没有股票"
    static let poolUnverifiedTitle = "KX 名单还没有同步"
    static let calendarGapTitle = "交易日历还没有覆盖今天"
    static let unreadableTitle = "KX 看板暂时读不了"

    /// 沿用晨报的状态风格：一个图标、一句话、必要时一行原因和按钮。
    static func message(for error: KXBoardError) -> MorningMessage {
        switch error {
        case .missingToken:
            return MorningMessage(title: MorningCopy.missingToken, detail: nil, problem: false, retry: true, settings: true,
                                  icon: MorningCopy.symbol(for: .missingToken))
        case .unauthorized:
            return MorningMessage(title: MorningCopy.unauthorized, detail: nil, problem: false, retry: true, settings: true,
                                  icon: MorningCopy.symbol(for: .unauthorized))
        case .notConfigured:
            return MorningMessage(title: MorningCopy.notConfigured, detail: nil, problem: false, retry: true,
                                  icon: MorningCopy.symbol(for: .notConfigured))
        case .dbNotReady:
            return MorningMessage(title: MorningCopy.dbNotReady, detail: nil, problem: false, retry: true,
                                  icon: MorningCopy.symbol(for: .dbNotReady))
        case .poolUnverified(let reason):
            return MorningMessage(title: poolUnverifiedTitle, detail: clean(reason), problem: false, retry: true,
                                  icon: "list.bullet.rectangle")
        case .calendarGap(let reason):
            return MorningMessage(title: calendarGapTitle, detail: clean(reason), problem: false, retry: true,
                                  icon: "calendar.badge.exclamationmark")
        case .unreachable:
            return MorningMessage(title: MorningCopy.unreachable, detail: nil, problem: false, retry: true,
                                  icon: MorningCopy.symbol(for: .unreachable))
        case .unreadable:
            return MorningMessage(title: unreadableTitle, detail: nil, problem: false, retry: true,
                                  icon: MorningCopy.symbol(for: .unreadable))
        }
    }

    static let emptyPoolMessage = MorningMessage(title: emptyPoolTitle, detail: nil, problem: false, retry: true, icon: "tray")

    private static func clean(_ text: String?) -> String? {
        let trimmed = text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return trimmed.isEmpty ? nil : trimmed
    }
}

private enum KXClock {
    private static let shanghai = TimeZone(identifier: "Asia/Shanghai")!
    private static let plain: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()
    private static let fractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
    private static var formatters: [String: DateFormatter] = [:]

    static func parse(_ iso: String?) -> Date? {
        guard let iso, !iso.isEmpty else { return nil }
        return plain.date(from: iso) ?? fractional.date(from: iso)
    }

    static func string(_ date: Date, _ pattern: String) -> String {
        formatter(pattern).string(from: date)
    }

    static func sameDay(_ lhs: Date, _ rhs: Date) -> Bool {
        string(lhs, "yyyy-MM-dd") == string(rhs, "yyyy-MM-dd")
    }

    private static func formatter(_ pattern: String) -> DateFormatter {
        if let cached = formatters[pattern] { return cached }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = shanghai
        formatter.dateFormat = pattern
        formatters[pattern] = formatter
        return formatter
    }
}
