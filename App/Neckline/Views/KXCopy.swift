// KX 看板的文案与数字格式。时间一律按北京时间显示，不读设备时区；缺数据写「—」。

import Foundation

enum KXTrend: Equatable {
    case up
    case down
    case flat

    init(_ value: Double?) {
        guard let value, value.isFinite else {
            self = .flat
            return
        }
        if value > 0 { self = .up }
        else if value < 0 { self = .down }
        else { self = .flat }
    }
}

struct KXStatusPiece: Equatable {
    enum Tone: Equatable {
        case up
        case broken
        case down
        case off
        case missing
    }

    var text: String
    var tone: Tone
}

struct KXFactBit: Equatable {
    var text: String
    var missing: Bool
}

struct KXDelta: Equatable {
    enum Direction: Equatable {
        case up
        case down
        case flat
    }

    var text: String
    var direction: Direction
}

struct KXOrderLine: Equatable {
    enum Kind: Equatable {
        case seal
        case bid
    }

    var kind: Kind
    var amount: String
    var delta: KXDelta?
    var warnings: String?
}

struct KXLabeledLine: Equatable {
    var label: String
    var value: String
}

enum KXDeepseekBody: Equatable {
    case judgment(verdict: String, reason: String, foldTitle: String, lines: [KXLabeledLine])
    case note(String)
}

struct KXSentimentText: Equatable {
    var limitUp: String
    var broken: String
    var brokenRate: String
    var limitDown: String
    var boards: String
    var showsBoardUnit: Bool
    var highestLabel: String
    var names: [String]
    var nameTitle: String?
    var yesterday: String
    var intraday: Bool
}

struct KXFooter: Equatable {
    var line: String
    var version: String?
}

enum KXCopy {
    static let dash = "—"
    static let emptyRoom = "今天没有"
    static let unreadableTitle = "KX 看板暂时读不了"
    static let mismatchTitle = "这句与麦蕊数据不符"
    static let partialQuote = "部分行情不完整"
    static let deepseekPending = "DeepSeek 未完成"

    // MARK: 数字

    static func price(_ value: Double?) -> String {
        guard let value, value.isFinite else { return dash }
        return format(value, digits: 2)
    }

    static func percent(_ value: Double?) -> String {
        guard let value, value.isFinite else { return dash }
        let body = format(value, digits: 2)
        return (value > 0 ? "+" : "") + body + "%"
    }

    static func trend(_ value: Double?) -> KXTrend {
        KXTrend(value)
    }

    /// ≥1 亿写「x.xx亿」，否则写「x.x万」。末尾的 0 去掉，和样图一样。
    static func amount(_ yuan: Double?) -> String {
        guard let yuan, yuan.isFinite else { return dash }
        let magnitude = abs(yuan)
        if magnitude >= 100_000_000 {
            return trim(format(magnitude / 100_000_000, digits: 2)) + "亿"
        }
        return trim(format(magnitude / 10_000, digits: 1)) + "万"
    }

    /// 炸板率是 0～1，写成百分数一位小数。
    static func brokenRate(_ value: Double?) -> String {
        guard let value, value.isFinite else { return dash }
        return format(value * 100, digits: 1) + "%"
    }

    static func whole(_ value: Double?) -> String {
        guard let value, value.isFinite else { return dash }
        if value.rounded(.toNearestOrAwayFromZero) == value, abs(value) < 1e15 {
            return String(Int(value))
        }
        return trim(format(value, digits: 1))
    }

    static func sealDelta(_ yuan: Double?) -> KXDelta? {
        guard let yuan, yuan.isFinite else { return nil }
        if yuan == 0 { return KXDelta(text: "0", direction: .flat) }
        let sign = yuan > 0 ? "+" : "\u{2212}"
        return KXDelta(text: sign + amount(abs(yuan)), direction: yuan > 0 ? .up : .down)
    }

    // MARK: 今日状态与盘口

    static func todayStatus(_ limit: KXLimitUp) -> [KXStatusPiece] {
        let up = limit.inLimitUp
        let broken = limit.inBroken
        let down = limit.inLimitDown
        if up == nil, broken == nil, down == nil {
            return [KXStatusPiece(text: dash, tone: .missing)]
        }
        var parts: [KXStatusPiece] = []
        if up == true { parts.append(KXStatusPiece(text: "涨停", tone: .up)) }
        if broken == true { parts.append(KXStatusPiece(text: "炸板", tone: .broken)) }
        if down == true { parts.append(KXStatusPiece(text: "跌停", tone: .down)) }
        if !parts.isEmpty { return parts }
        if up == false, broken == false, down == false {
            return [KXStatusPiece(text: "未涨停", tone: .off)]
        }
        return [KXStatusPiece(text: dash, tone: .missing)]
    }

    static func inPool(_ limit: KXLimitUp) -> Bool {
        limit.inLimitUp == true || limit.inBroken == true
    }

    /// 只在涨停池或炸板池里才有几板、板型、首封、开板。板型用接口原文。
    static func poolFacts(_ limit: KXLimitUp) -> [KXFactBit]? {
        guard inPool(limit) else { return nil }
        let boards = limit.consecutiveBoards.map { KXFactBit(text: whole($0) + " 板", missing: false) }
            ?? KXFactBit(text: "— 板", missing: true)
        let typeText = clean(limit.boardTypeText)
        let type = KXFactBit(text: typeText ?? dash, missing: typeText == nil)
        let seal = shortClock(limit.firstSealTime)
        let first = KXFactBit(text: "首封 " + (seal ?? dash), missing: seal == nil)
        let opened = limit.breakCount.map { KXFactBit(text: "开板 " + whole($0), missing: false) }
            ?? KXFactBit(text: "开板 \(dash)", missing: true)
        return [boards, type, first, opened]
    }

    static func isAuction(_ stock: KXStock) -> Bool {
        stock.quote.sessionPhase == .callAuction
    }

    /// 竞价中不写「封单」。sealed 不是 true 时只写买一挂单。没有盘口就不写这一行。
    static func orderLine(_ stock: KXStock) -> KXOrderLine? {
        guard let book = stock.orderBook else { return nil }
        let warnings = joined(book.warnings)
        if book.sealed == true, !isAuction(stock) {
            return KXOrderLine(kind: .seal, amount: amount(book.sealAmount), delta: sealDelta(book.sealChange5m?.amount), warnings: warnings)
        }
        return KXOrderLine(kind: .bid, amount: amount(book.bid1Amount), delta: nil, warnings: warnings)
    }

    static func extraWarnings(_ stock: KXStock) -> String? {
        let seen = Set((stock.orderBook?.warnings ?? []).compactMap(clean))
        let more = (stock.quote.warnings + stock.limitUp.warnings).compactMap(clean).filter { !seen.contains($0) }
        return more.isEmpty ? nil : more.joined(separator: "；")
    }

    static func flagLabel(_ flag: KXFlag) -> String {
        if let code = clean(flag.code) {
            return flag.kind.raw + " " + code
        }
        if let colon = flag.text.firstIndex(of: ":") {
            let left = flag.text[..<colon].trimmingCharacters(in: .whitespacesAndNewlines)
            let right = flag.text[flag.text.index(after: colon)...].trimmingCharacters(in: .whitespacesAndNewlines)
            if !left.isEmpty, !right.isEmpty { return left + " " + right }
        }
        return flag.text
    }

    static func flagLabels(_ group: KXGroup) -> [String] {
        group.flags.map(flagLabel).filter { !$0.isEmpty }
    }

    static func roomSince(_ group: KXGroup) -> String {
        "进房 \(monthDay(group.roomSince))"
    }

    static func eliminatedLine(_ group: KXGroup) -> String {
        "断板日 \(monthDay(group.brokeOn)) · \(clean(group.observedText) ?? dash)"
    }

    static func deepseekBody(_ group: KXGroup) -> KXDeepseekBody? {
        let note = clean(group.note)
        if let judgment = group.judgment {
            var lines = [
                KXLabeledLine(label: "高度", value: clean(judgment.height) ?? dash),
                KXLabeledLine(label: "带动", value: clean(judgment.drive) ?? dash),
                KXLabeledLine(label: "承接", value: clean(judgment.resilience) ?? dash),
            ]
            var title = "高度、带动、承接"
            if let note {
                lines.append(KXLabeledLine(label: "说明", value: note))
                title += "、说明"
            }
            return .judgment(
                verdict: clean(judgment.verdict) ?? dash,
                reason: clean(judgment.reason) ?? dash,
                foldTitle: title,
                lines: lines
            )
        }
        if let note { return .note(note) }
        return nil
    }

    static func mismatches(group: KXGroup, board: KXBoard) -> [String] {
        guard let id = clean(group.id) else { return [] }
        return board.factWarnings.compactMap { warning in
            guard warning.groupId == id else { return nil }
            return clean(warning.text)
        }
    }

    static func orphanFactTexts(_ board: KXBoard) -> [String] {
        let ids = Set(board.rooms.ordered.flatMap { $0.groups.compactMap(\.id) })
        return board.factWarnings.compactMap { warning in
            guard let text = clean(warning.text) else { return nil }
            if let id = clean(warning.groupId), ids.contains(id) { return nil }
            return text
        }
    }

    // MARK: 页眉、提示、页脚

    static func headerMeta(board: KXBoard, refreshFailed: Bool) -> String {
        let time = clock(board.source?.checkedAt) ?? dash
        let state: String
        if refreshFailed {
            state = "刷新失败"
        } else if board.session?.inSession == true {
            state = "盘中"
        } else {
            state = "已收盘"
        }
        return "\(time) · \(state)"
    }

    static func headerSub(board: KXBoard) -> String {
        "按 \(monthDay(board.structure?.asOf)) 收盘整理"
    }

    static func amberLines(board: KXBoard) -> [String] {
        var lines: [String] = []
        if board.source?.stale == true {
            let time = clock(board.source?.lastReadAt) ?? dash
            lines.append("未能核对，最后读到 \(time)")
        }
        if let status = board.structure?.deepseekStatus, !status.isOK {
            lines.append(clean(board.structure?.deepseekNotice) ?? deepseekPending)
        }
        return lines
    }

    static func grayLine(board: KXBoard) -> String? {
        var parts: [String] = []
        if board.structure?.deepseekStatus?.isOK == true, let notice = clean(board.structure?.deepseekNotice) {
            parts.append(notice)
        }
        if let notice = clean(board.structure?.refresh?.notice) {
            parts.append(notice)
        }
        if board.status == "partial" {
            parts.append(partialQuote)
        }
        parts.append(contentsOf: board.gaps.compactMap(clean))
        let orphans = orphanFactTexts(board)
        if !orphans.isEmpty {
            parts.append(mismatchTitle + "：" + orphans.joined(separator: "；"))
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    static func sentiment(_ sentiment: KXSentiment?) -> KXSentimentText {
        let highest = sentiment?.highestBoard
        let names = highest?.names ?? []
        let codes = highest?.codes ?? []
        let count = names.isEmpty ? codes.count : names.count
        let boards = highest?.boards
        let label = boards == nil && count == 0 ? "最高 —" : "最高 · \(count) 只"
        return KXSentimentText(
            limitUp: whole(sentiment?.limitUpCount),
            broken: whole(sentiment?.brokenCount),
            brokenRate: brokenRate(sentiment?.brokenRate),
            limitDown: whole(sentiment?.limitDownCount),
            boards: boards == nil ? dash : whole(boards),
            showsBoardUnit: boards != nil,
            highestLabel: label,
            names: names,
            nameTitle: names.isEmpty ? nil : "\(count) 只的名字",
            yesterday: "昨日连板 \(whole(sentiment?.yesterdayConsecutive)) 只，今日继续 \(whole(sentiment?.yesterdayContinued)) 只",
            intraday: sentiment?.basis == "交易时段"
        )
    }

    static func footer(_ board: KXBoard) -> KXFooter {
        let read = board.source?.checkedAt
        let quote = latestQuoteTime(board)
        let pool = board.sentiment?.fetchedAt?.limitUp
        let written = board.structure?.generatedAt
        let base = KXClock.parse(read).map(KXClock.dayKey)
        func one(_ label: String, _ raw: String?) -> String {
            guard let raw, let date = KXClock.parse(raw) else { return "\(label) \(dash)" }
            let clock = KXClock.string(date, "HH:mm:ss")
            if let base, KXClock.dayKey(date) == base {
                return "\(label) \(clock)"
            }
            return "\(label) \(KXClock.string(date, "MM/dd")) \(clock)"
        }
        let prefix: String
        if let read, let date = KXClock.parse(read) {
            prefix = KXClock.string(date, "MM/dd") + " · "
        } else {
            prefix = ""
        }
        let line = prefix + [one("读取", read), one("报价", quote), one("涨停池", pool), one("结构写入", written)].joined(separator: " · ")
        return KXFooter(line: line, version: clean(board.structure?.version))
    }

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
        case .unavailable(let reason):
            return MorningMessage(title: unreadableTitle, detail: clean(reason), problem: false, retry: true,
                                  icon: MorningCopy.symbol(for: .unreadable))
        case .unreachable:
            return MorningMessage(title: MorningCopy.unreachable, detail: nil, problem: false, retry: true,
                                  icon: MorningCopy.symbol(for: .unreachable))
        case .unreadable:
            return MorningMessage(title: unreadableTitle, detail: nil, problem: false, retry: true,
                                  icon: MorningCopy.symbol(for: .unreadable))
        }
    }

    // MARK: 时间

    static func clock(_ iso: String?) -> String? {
        guard let date = KXClock.parse(iso) else { return nil }
        return KXClock.string(date, "HH:mm")
    }

    static func clockWithSeconds(_ iso: String?) -> String? {
        guard let date = KXClock.parse(iso) else { return nil }
        return KXClock.string(date, "HH:mm:ss")
    }

    static func monthDay(_ raw: String?) -> String {
        guard let raw = clean(raw) else { return dash }
        if !raw.contains("T") {
            let parts = raw.split(separator: "-", omittingEmptySubsequences: false)
            if parts.count >= 3, parts[1].count == 2, parts[2].count >= 2 {
                return "\(parts[1])/\(parts[2].prefix(2))"
            }
        }
        guard let date = KXClock.parse(raw) else { return dash }
        return KXClock.string(date, "MM/dd")
    }

    static func shortClock(_ raw: String?) -> String? {
        guard let raw = clean(raw) else { return nil }
        if let date = KXClock.parse(raw) { return KXClock.string(date, "HH:mm") }
        guard let range = raw.range(of: #"\d{2}:\d{2}"#, options: .regularExpression) else { return nil }
        return String(raw[range])
    }

    static func clean(_ text: String?) -> String? {
        let trimmed = text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return trimmed.isEmpty ? nil : trimmed
    }

    private static func joined(_ items: [String]) -> String? {
        let cleaned = items.compactMap(clean)
        return cleaned.isEmpty ? nil : cleaned.joined(separator: "；")
    }

    private static func latestQuoteTime(_ board: KXBoard) -> String? {
        var best: (Date, String)?
        for room in board.rooms.ordered {
            for group in room.groups {
                for stock in group.leaders + group.followers {
                    guard let raw = stock.quote.sourceTime, let date = KXClock.parse(raw) else { continue }
                    if best == nil || date > best!.0 { best = (date, raw) }
                }
            }
        }
        return best?.1
    }

    private static func format(_ value: Double, digits: Int) -> String {
        String(format: "%.\(digits)f", locale: Locale(identifier: "en_US_POSIX"), value)
    }

    private static func trim(_ text: String) -> String {
        guard text.contains(".") else { return text }
        var result = text
        while result.last == "0" { result.removeLast() }
        if result.last == "." { result.removeLast() }
        return result
    }
}

enum KXClock {
    static let shanghai = TimeZone(identifier: "Asia/Shanghai")!
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
    private static let lock = NSLock()

    static func parse(_ raw: String?) -> Date? {
        guard let raw, !raw.isEmpty else { return nil }
        lock.lock()
        defer { lock.unlock() }
        if let date = fractional.date(from: raw) ?? plain.date(from: raw) { return date }
        let trimmed = trimmingFraction(raw)
        guard trimmed != raw else { return nil }
        return fractional.date(from: trimmed) ?? plain.date(from: trimmed)
    }

    static func string(_ date: Date, _ pattern: String) -> String {
        lock.lock()
        defer { lock.unlock() }
        return formatter(pattern).string(from: date)
    }

    static func dayKey(_ date: Date) -> String {
        string(date, "yyyy-MM-dd")
    }

    private static func trimmingFraction(_ raw: String) -> String {
        guard let regex = try? NSRegularExpression(pattern: #"(\.\d{3})\d+"#) else { return raw }
        let range = NSRange(raw.startIndex..., in: raw)
        return regex.stringByReplacingMatches(in: raw, range: range, withTemplate: "$1")
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
