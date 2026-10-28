// 晨报文案与北京时间格式化。日期用公历和 Asia/Shanghai，不读设备时区。

import Foundation

enum MorningCopy {
    static let reading = "正在读取"
    static let noReport = "还没有晨报"
    static let noReportDetail = "每个交易日 08:00 截止后出报"
    static let emptyEntries = "今天没有值得推的"
    static let questionsHeading = "四个问题"
    static let viewsHeading = "各方怎么看"
    static let sourcesHeading = "原文"
    static let questionValue = "① 利好值多少"
    static let questionPriced = "② 市场已反映多少"
    static let questionMoney = "③ 资金为什么买、为什么不买"
    static let questionPrice = "④ 价格划不划算"
    static let failedTitle = "晨报出问题了"
    static let running = "正在出报"
    static let runningDetail = "完成后下拉刷新"
    static let unreachable = "连不上服务"
    static let missingToken = "还没有填写令牌"
    static let unauthorized = "令牌不正确"
    static let notConfigured = "服务还没配好"
    static let dbNotReady = "数据库还没就绪"
    static let unreadable = "这份晨报暂时读不了"
    static let noDocument = "找不到这篇原文"

    static func text(for error: MorningClientError) -> String {
        switch error {
        case .missingToken: return missingToken
        case .unauthorized: return unauthorized
        case .noReport: return noReport
        case .noDocument: return noDocument
        case .notConfigured: return notConfigured
        case .dbNotReady: return dbNotReady
        case .unreachable: return unreachable
        case .unreadable: return unreadable
        }
    }

    static func detail(for error: MorningClientError) -> String? {
        switch error {
        case .noReport: return noReportDetail
        default: return nil
        }
    }

    static func header(tradeDate: String) -> String {
        guard let parts = ShanghaiClock.day(tradeDate) else { return "晨报 \(tradeDate)" }
        return "晨报 \(parts.month)/\(parts.day) \(parts.weekday)"
    }

    static func historyTitle(tradeDate: String) -> String {
        guard let parts = ShanghaiClock.day(tradeDate) else { return tradeDate }
        return "\(parts.month)/\(parts.day) \(parts.weekday)"
    }

    static func windowLine(report: MorningReport) -> String {
        if let content = report.content {
            return windowLine(start: content.window.start, end: content.window.end, generatedAt: content.generatedAt)
        }
        return windowLine(start: report.windowStart, end: report.windowEnd, generatedAt: nil)
    }

    /// 名单页眉的时间范围。有条目时带上「N 家公司」。没有正文的期仍用窗口行。
    static func listMeta(report: MorningReport) -> String {
        guard let content = report.content else { return windowLine(report: report) }
        let left = ShanghaiClock.monthDayClock(content.window.start) ?? content.window.start
        let right = ShanghaiClock.monthDayClock(content.window.end) ?? content.window.end
        let range = "\(left) \u{2013} \(right)"
        let count = content.entries.count
        guard count > 0 else { return range }
        return "\(range) · \(companyCount(count))"
    }

    static func companyCount(_ count: Int) -> String {
        "\(count) 家公司"
    }

    /// 0 条时的第二行。旧晨报没有 evaluated 就不显示。
    static func emptyEvaluatedLine(_ evaluated: Int?) -> String? {
        guard let evaluated else { return nil }
        return "评估了 \(evaluated) 家公司的隔夜消息，没有值得推的"
    }

    /// 名单右侧和详情里的时刻：MM-dd，有钟点再加 HH:mm。解析不了就原样返回。
    /// 正好 00:00 的多是巨潮只标到日期的公告（前一晚发布、记为披露日），只显示日期，免得看成半夜发布。
    static func listStamp(_ value: String) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.contains("T"), let parts = ShanghaiClock.paddedClock(trimmed) {
            if parts.hour == 0 && parts.minute == 0 {
                return String(format: "%02d-%02d", parts.month, parts.day)
            }
            return String(format: "%02d-%02d %02d:%02d", parts.month, parts.day, parts.hour, parts.minute)
        }
        if let civil = ShanghaiClock.civil(trimmed) {
            let day = String(format: "%02d-%02d", civil.month, civil.day)
            if let hour = civil.hour, let minute = civil.minute {
                return "\(day) \(String(format: "%02d:%02d", hour, minute))"
            }
            return day
        }
        return trimmed
    }

    static func riskLine(_ risk: String) -> String {
        let trimmed = risk.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.hasPrefix("风险") { return trimmed }
        return "风险：\(trimmed)"
    }

    static func viewAttribution(by: String, name: String) -> String {
        let who = by.trimmingCharacters(in: .whitespacesAndNewlines)
        let named = name.trimmingCharacters(in: .whitespacesAndNewlines)
        if who.isEmpty { return named }
        if named.isEmpty { return who }
        return "\(who) · \(named)"
    }

    /// 北京时间的「10/16 17:05」。解析不了就原样返回。
    static func beijingClock(_ value: String) -> String {
        ShanghaiClock.monthDayClock(value) ?? value
    }

    static func windowLine(start: String, end: String, generatedAt: String?) -> String {
        let left = ShanghaiClock.monthDayClock(start) ?? start
        let right = ShanghaiClock.monthDayClock(end) ?? end
        if let generatedAt, let clock = ShanghaiClock.hourMinute(generatedAt) {
            return "窗口 \(left) → \(right) · \(clock) 生成"
        }
        return "窗口 \(left) → \(right)"
    }

    static func historySubtitle(status: MorningStatus, entryCount: Int) -> String {
        switch status {
        case .available: return "已出 · \(entryCount) 条"
        case .failed: return "出问题"
        case .running: return "正在生成"
        case .unknown: return "状态不明"
        }
    }

    static func failureLine(reason: String?) -> String {
        guard let reason, !reason.isEmpty else { return failedTitle }
        return "\(failedTitle)\n\(reason)"
    }

    static func firstDisclosedLine(_ value: String) -> String {
        "首次公开 \(listStamp(value))"
    }

    static func documentHeading(title: String?, text: String, sourceLabel: String) -> String {
        let named = title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !named.isEmpty { return named }
        if let bracket = leadingBracket(text) { return bracket }
        return sourceName(sourceLabel)
    }

    static func sourceName(_ sourceLabel: String) -> String {
        let trimmed = sourceLabel.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "原文" : trimmed
    }

    static func symbol(for error: MorningClientError) -> String {
        switch error {
        case .noReport: return "calendar"
        case .missingToken, .unauthorized: return "key"
        case .unreachable: return "wifi.slash"
        case .notConfigured: return "gearshape.badge.exclamationmark"
        case .dbNotReady: return "externaldrive.badge.exclamationmark"
        case .unreadable: return "doc.badge.exclamationmark"
        case .noDocument: return "doc.text.magnifyingglass"
        }
    }

    private static func leadingBracket(_ text: String) -> String? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.first == "【", let end = trimmed.firstIndex(of: "】") else { return nil }
        let inner = trimmed[trimmed.index(after: trimmed.startIndex)..<end]
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return inner.isEmpty ? nil : inner
    }
}

private enum ShanghaiClock {
    struct DayParts {
        let month: Int
        let day: Int
        let weekday: String
    }

    private struct Stamp {
        let year: Int
        let month: Int
        let day: Int
        let hour: Int
        let minute: Int
        let second: Int
        let offsetSeconds: Int
    }

    private static let weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]

    struct CivilDate {
        let year: Int
        let month: Int
        let day: Int
        let hour: Int?
        let minute: Int?
    }

    /// 「YYYY-MM-DD」或「YYYY-MM-DD HH:MM」。
    static func civil(_ value: String) -> CivilDate? {
        let pieces = value.split(separator: " ", maxSplits: 1).map(String.init)
        guard let date = pieces.first?.split(separator: "-"), date.count == 3,
              let year = Int(date[0]), let month = Int(date[1]), let day = Int(date[2]),
              (1...12).contains(month), (1...31).contains(day) else { return nil }
        guard pieces.count == 2 else { return CivilDate(year: year, month: month, day: day, hour: nil, minute: nil) }
        let time = pieces[1].split(separator: ":")
        guard time.count >= 2, let hour = Int(time[0]), let minute = Int(time[1]),
              (0...23).contains(hour), (0...59).contains(minute) else {
            return CivilDate(year: year, month: month, day: day, hour: nil, minute: nil)
        }
        return CivilDate(year: year, month: month, day: day, hour: hour, minute: minute)
    }

    static func day(_ value: String) -> DayParts? {
        let pieces = value.split(separator: "-")
        guard pieces.count == 3, let year = Int(pieces[0]), let month = Int(pieces[1]), let day = Int(pieces[2]),
              (1...12).contains(month), (1...31).contains(day) else { return nil }
        var calendar = gregorian(timeZone: shanghai)
        var components = DateComponents()
        components.year = year
        components.month = month
        components.day = day
        components.hour = 12
        guard let date = calendar.date(from: components) else { return nil }
        let weekday = calendar.component(.weekday, from: date)
        guard weekdays.indices.contains(weekday - 1) else { return nil }
        return DayParts(month: month, day: day, weekday: weekdays[weekday - 1])
    }

    static func monthDayClock(_ value: String) -> String? {
        guard let parts = shanghaiParts(value), let month = parts.month, let day = parts.day,
              let hour = parts.hour, let minute = parts.minute else { return nil }
        return "\(month)/\(day) \(clock(hour, minute))"
    }

    static func hourMinute(_ value: String) -> String? {
        guard let parts = shanghaiParts(value), let hour = parts.hour, let minute = parts.minute else { return nil }
        return clock(hour, minute)
    }

    static func paddedClock(_ value: String) -> (month: Int, day: Int, hour: Int, minute: Int)? {
        guard let parts = shanghaiParts(value), let month = parts.month, let day = parts.day,
              let hour = parts.hour, let minute = parts.minute else { return nil }
        return (month, day, hour, minute)
    }

    private static func clock(_ hour: Int, _ minute: Int) -> String {
        String(format: "%02d:%02d", hour, minute)
    }

    private static var shanghai: TimeZone { TimeZone(identifier: "Asia/Shanghai")! }

    private static func gregorian(timeZone: TimeZone) -> Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        calendar.locale = Locale(identifier: "en_US_POSIX")
        return calendar
    }

    private static func shanghaiParts(_ value: String) -> DateComponents? {
        guard let stamp = parse(value), let zone = TimeZone(secondsFromGMT: stamp.offsetSeconds) else { return nil }
        var source = gregorian(timeZone: zone)
        var components = DateComponents()
        components.year = stamp.year
        components.month = stamp.month
        components.day = stamp.day
        components.hour = stamp.hour
        components.minute = stamp.minute
        components.second = stamp.second
        guard let date = source.date(from: components) else { return nil }
        return gregorian(timeZone: shanghai).dateComponents([.month, .day, .hour, .minute], from: date)
    }

    private static func parse(_ value: String) -> Stamp? {
        guard let separator = value.firstIndex(of: "T") else { return nil }
        let date = value[..<separator].split(separator: "-")
        guard date.count == 3, let year = Int(date[0]), let month = Int(date[1]), let day = Int(date[2]) else { return nil }
        let rest = value[value.index(after: separator)...]
        guard let sign = rest.lastIndex(where: { $0 == "Z" || $0 == "+" || $0 == "-" }) else { return nil }
        let time = rest[..<sign].split(separator: ":")
        guard time.count == 2 || time.count == 3, let hour = Int(time[0]), let minute = Int(time[1]) else { return nil }
        var second = 0
        if time.count == 3 {
            let whole = time[2].split(separator: ".").first.map(String.init) ?? ""
            guard let parsed = Int(whole) else { return nil }
            second = parsed
        }
        guard (0...23).contains(hour), (0...59).contains(minute), (0...60).contains(second) else { return nil }
        let zone = rest[sign...]
        let offset: Int
        if zone == "Z" {
            offset = 0
        } else {
            let digits = zone.dropFirst().split(separator: ":")
            guard digits.count == 2, let zoneHour = Int(digits[0]), let zoneMinute = Int(digits[1]),
                  (0...23).contains(zoneHour), (0...59).contains(zoneMinute) else { return nil }
            let magnitude = zoneHour * 3600 + zoneMinute * 60
            offset = zone.first == "-" ? -magnitude : magnitude
        }
        return Stamp(year: year, month: month, day: day, hour: hour, minute: minute, second: second, offsetSeconds: offset)
    }
}
