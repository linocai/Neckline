//
//  StaticTradingCalendar.swift
//  Neckline — 客户端交易日历(日期解析 + 展示;搬自 LinoN,§五 阶段4C 复用清单)
//
//  与 LinoN 版本的区别:Neckline 的持仓审计不做「买入日=D1 / D4 强平」这类客户端
//  持仓天数规则(那是 LinoN 特有的产品设计;Neckline 止损/止盈/时间退出参数全在
//  服务端策略大脑 `strategy_versions`,客户端不重算、不派生持仓交易日计数)。
//  本文件只保留日期解析 + 交易日判断两个通用能力,供 UI 展示("非交易日"提示等)。
//
//  日期口径:Neckline 后端字面用 'YYYYMMDD'(报告 tradeDate / 持仓 buyDate 均此格式,
//  见 `neckline/sentinel/positions.py`);本类同时兼容 'YYYY-MM-DD' 便于未来扩展。
//

import Foundation

final class StaticTradingCalendar {
    static let shared = StaticTradingCalendar()

    /// 与后端 `neckline/calendar` 静态兜底表同源口径(2025–2026 沪市休市日 + 调休补班周末)。
    private let closed: Set<String> = [
        // —— 2025 ——
        "2025-01-01",
        "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
        "2025-02-01", "2025-02-02", "2025-02-03", "2025-02-04",
        "2025-04-04", "2025-04-05", "2025-04-06",
        "2025-05-01", "2025-05-02", "2025-05-03", "2025-05-04", "2025-05-05",
        "2025-05-31", "2025-06-01", "2025-06-02",
        "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-04",
        "2025-10-05", "2025-10-06", "2025-10-07", "2025-10-08",
        "2025-01-26", "2025-02-08", "2025-04-27", "2025-09-28", "2025-10-11",
        // —— 2026 ——
        "2026-01-01", "2026-01-02", "2026-01-03",
        "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
        "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
        "2026-04-04", "2026-04-05", "2026-04-06",
        "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
        "2026-06-19", "2026-06-20", "2026-06-21",
        "2026-09-25", "2026-09-26", "2026-09-27",
        "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
        "2026-10-05", "2026-10-06", "2026-10-07",
        "2026-01-04", "2026-02-14", "2026-02-28", "2026-05-09", "2026-09-20", "2026-10-10",
    ]

    private let cal: Calendar
    private let isoFmt: DateFormatter      // yyyy-MM-dd
    private let compactFmt: DateFormatter  // yyyyMMdd(后端字面口径)
    private let displayFmt: DateFormatter  // yyyy-MM-dd(展示用,同 isoFmt,单独具名便于阅读)

    private init() {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Asia/Shanghai") ?? .current
        self.cal = c
        func fmt(_ pattern: String) -> DateFormatter {
            let f = DateFormatter()
            f.calendar = c
            f.timeZone = c.timeZone
            f.dateFormat = pattern
            return f
        }
        self.isoFmt = fmt("yyyy-MM-dd")
        self.compactFmt = fmt("yyyyMMdd")
        self.displayFmt = fmt("yyyy-MM-dd")
    }

    func key(_ date: Date) -> String { isoFmt.string(from: date) }

    /// 接受后端字面 'YYYYMMDD' 或 'YYYY-MM-DD'(含带时间前缀,取前 8/10 位)。
    func parseDate(_ s: String) -> Date? {
        let trimmed = s.trimmingCharacters(in: .whitespaces)
        if trimmed.count >= 8, !trimmed.contains("-"), let d = compactFmt.date(from: String(trimmed.prefix(8))) {
            return d
        }
        if let d = isoFmt.date(from: String(trimmed.prefix(10))) { return d }
        return nil
    }

    /// 'YYYYMMDD' → 'YYYY-MM-DD' 展示串;解析失败原样返回(不崩、不假装格式化成功)。
    func displayString(_ raw: String) -> String {
        guard let d = parseDate(raw) else { return raw }
        return displayFmt.string(from: d)
    }

    func isTradingDay(_ date: Date) -> Bool {
        let weekday = cal.component(.weekday, from: date)   // 1=Sun … 7=Sat
        if weekday == 1 || weekday == 7 { return false }
        return !closed.contains(key(date))
    }

    var isTodayTradingDay: Bool { isTradingDay(Date()) }
}
