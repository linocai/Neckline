// KX V4 看板文案、格式化与语义色映射。所有缺失值统一显示“—”，绝不当成 0。

import SwiftUI

enum KXCopy {
    static let title = "KX"
    static let allFaction = "全部"
    static let searchPlaceholder = "搜索名称、代码、行业或派系"
    static let footerNote = "仅展示盘后事实与 DeepSeek 候选判断，不构成买卖建议 · 沪深主板（不含 ST）"
    static let reading = "正在读取 KX 盘后看板…"
    static let refreshFailedNotice = "最近一次刷新未成功，当前仍展示已有盘后快照。"
    static let emptyFilterTitle = "当前筛选条件下没有匹配标的"
    static let clearFilter = "清除筛选"
    static let emptyBoardTitle = "当前盘后快照没有候选标的"
    static let selectLeftPrompt = "选择左侧一只标的查看盘后事实与龙虎榜详情"
    static let noLHBRecords = "近 7 个交易日无龙虎榜记录"

    // MARK: 候选区标题与说明

    static func zoneTitle(_ zone: KXZone) -> String {
        switch zone {
        case .leader: "龙头候选区"
        case .hot: "高情绪候选区"
        case .elim: "淘汰候选区"
        }
    }

    static func zoneHint(_ zone: KXZone) -> String {
        switch zone {
        case .leader: "核心总龙 / 独立卡位新龙"
        case .hot: "高换手仍在博弈（含剧烈杀跌洗筹）"
        case .elim: "低换手失血冷清（满 1 日再移出）"
        }
    }

    static func zoneDotColor(_ zone: KXZone) -> Color {
        switch zone {
        case .leader: NKKX.up
        case .hot: NKKX.amber
        case .elim: NKKX.elimDot
        }
    }

    // MARK: 独立于分区的 5 档节奏样式

    static func rhythmColors(_ kind: KXRhythmKind) -> (fg: Color, bg: Color) {
        switch kind {
        case .surge: (NKKX.up, NKKX.upFill)
        case .pullback: (NKKX.accent, NKKX.accentFill)
        case .divergence: (NKKX.divergenceInk, NKKX.divergenceFill)
        case .washout: (NKKX.washInk, NKKX.washFill)
        case .dead: (NKKX.deadInk, NKKX.deadFill)
        }
    }

    // MARK: 6 种变动徽章

    static func changeBadgeSpec(_ badge: KXChangeBadge) -> (text: String, fg: Color, bg: Color)? {
        switch badge {
        case .up: ("↑ 升龙头", NKKX.up, NKKX.upFill)
        case .new: ("新进", NKKX.accent, NKKX.accentFill)
        case .return: ("↺ 回流", NKKX.down, NKKX.downFill)
        case .elimOut: ("满1日·可移出", NKKX.amberInk, NKKX.amberBackground)
        case .elimNew: ("新入淘汰", NKKX.deadInk, NKKX.deadFill)
        case .stay: nil
        }
    }

    // MARK: 龙虎榜席位 5 种徽章

    static func seatBadgeColors(_ kind: KXSeatBadgeKind) -> (fg: Color, bg: Color) {
        switch kind {
        case .hot: (NKKX.up, NKKX.upFill)
        case .quant: (NKKX.purple, NKKX.purpleFill)
        case .lasa: (NKKX.amberInk, NKKX.amberBackground)
        case .inst: (NKKX.accent, NKKX.accentFill)
        case .north: (NKKX.down, NKKX.downFill)
        }
    }

    // MARK: 数值与日期格式化（nil 一律返回 “—”）

    static func toneColor(_ value: Double?) -> Color {
        guard let value else { return NKKX.flat }
        if value > 0 { return NKKX.up }
        if value < 0 { return NKKX.down }
        return NKKX.flat
    }

    static func fmtPct(_ value: Double?) -> String {
        guard let value else { return "—" }
        let sign = value > 0 ? "+" : ""
        return String(format: "%@%.2f%%", sign, value)
    }

    static func fmtPct1(_ value: Double?) -> String {
        guard let value else { return "—" }
        let sign = value > 0 ? "+" : ""
        return String(format: "%@%.1f%%", sign, value)
    }

    static func fmtPrice(_ value: Double?) -> String {
        guard let value else { return "—" }
        return String(format: "%.2f", value)
    }

    static func fmtTurnover(_ value: Double?, decimals: Int = 1) -> String {
        guard let value else { return "—" }
        return String(format: "%.\(decimals)f%%", value)
    }

    static func fmtRatioPct(_ value: Double?) -> String {
        guard let value else { return "—" }
        return "\(Int(value.rounded()))%"
    }

    static func fmtCircMV(_ value: Double?) -> String {
        guard let value else { return "—" }
        return String(format: "%.1f亿", value)
    }

    /// 带正负号的金额（万元输入，≥10000 显示亿）。
    static func fmtWanSigned(_ value: Double?) -> String {
        guard let value else { return "—" }
        let sign = value > 0 ? "+" : (value < 0 ? "-" : "")
        let absVal = abs(value)
        if absVal >= 10000 {
            return String(format: "%@%.2f亿", sign, absVal / 10000.0)
        }
        return "\(sign)\(Int(absVal.rounded()))万"
    }

    /// 无符号金额（万元输入，≥10000 显示亿）。
    static func fmtWanUnsigned(_ value: Double?) -> String {
        guard let value else { return "—" }
        let absVal = abs(value)
        if absVal >= 10000 {
            return String(format: "%.2f亿", absVal / 10000.0)
        }
        return "\(Int(absVal.rounded()))万"
    }

    static func monthDay(_ isoDate: String) -> String {
        let parts = isoDate.split(separator: "-")
        if parts.count == 3 {
            return "\(parts[1])-\(parts[2])"
        }
        return isoDate
    }

    static func generatedShortTime(_ isoTimestamp: String) -> String {
        // 形如 2026-11-20T20:03:00+08:00 -> 11-20 20:03
        guard let tIndex = isoTimestamp.firstIndex(of: "T") else { return isoTimestamp }
        let dayPart = String(isoTimestamp[..<tIndex])
        let timeStart = isoTimestamp.index(after: tIndex)
        let timePart = String(isoTimestamp[timeStart...].prefix(5))
        return "\(monthDay(dayPart)) \(timePart)"
    }

    static func turnoverHint(_ tr: Double?) -> String {
        guard let tr else { return "数据缺失" }
        if tr >= 15 { return "博弈活跃" }
        if tr <= 6 { return "低换手冷清" }
        return "中等换手"
    }

    static func volRatioHint(_ ratio: Double?) -> String {
        guard let ratio else { return "数据缺失" }
        if ratio <= 55 { return "已明显缩量" }
        if ratio >= 90 { return "接近峰值放量" }
        return "温和换手"
    }

    // MARK: 错误说明

    static func errorMessage(for error: KXBoardError) -> MorningMessage {
        switch error {
        case .missingToken:
            return MorningMessage(title: "未填访问令牌", detail: "请先在设置里填入访问令牌。", problem: true, retry: false, settings: true)
        case .unauthorized:
            return MorningMessage(title: "访问令牌无效", detail: "请到设置里核对访问令牌。", problem: true, retry: false, settings: true)
        case .notConfigured:
            return MorningMessage(title: "服务端未配置 V4 库", detail: nil, problem: true, retry: true)
        case .dbNotReady:
            return MorningMessage(title: "服务端库尚未就绪", detail: nil, problem: true, retry: true)
        case .unsupportedSchema(let detail):
            return MorningMessage(
                title: "KX 看板协议版本暂不支持",
                detail: detail ?? "上游返回了未适配的协议版本，未强行解析。",
                problem: true,
                retry: true,
                icon: "exclamationmark.triangle"
            )
        case .unavailable(let detail):
            return MorningMessage(
                title: "KX 看板暂时不可用",
                detail: detail ?? "稍后可手动刷新重试。",
                problem: true,
                retry: true
            )
        case .unreachable:
            return MorningMessage(title: "连不上 Neckline 服务", detail: "请检查网络或服务地址后重试。", problem: true, retry: true)
        case .unreadable:
            return MorningMessage(title: "看板数据无法解析", detail: "请稍后手动刷新重试。", problem: true, retry: true)
        }
    }
}
