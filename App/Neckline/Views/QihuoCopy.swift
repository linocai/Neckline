// Q 期货看板格式与文案辅助：严格区分 0.0 与 nil，纯客观数字格式化，绝不出现买卖建议。

import SwiftUI

enum QihuoCopy {
    static let boardTitle = "期货基本面记录"
    static let expandAll = "全部展开"
    static let collapseAll = "全部收起"
    static let emptySpotTitle = "留空"
    static let emptySpotSub = "无自动日度源"
    static let emptySpotBanner = "暂无免费可自动采的日度来源，按约定留空（需要时由对话 Agent 查现期表）。"
    static let missingSpotBanner = "暂无最新参考现货记录。"
    static let dataFootnote = "来源：TuShare 期货日线（fut_daily）与仓单日报（fut_wsr，已统一换算为吨）。"
    static let holdingFootnote = "来源：TuShare 交易所会员持仓排名（fut_holding），主力合约前 20 名合计（含代客与产业套保）。仅列客观数字与增减，不作多空判断。"

    private static let intFormatter: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.maximumFractionDigits = 0
        f.locale = Locale(identifier: "zh_CN")
        return f
    }()

    private static let decimalFormatter: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.minimumFractionDigits = 0
        f.maximumFractionDigits = 2
        f.locale = Locale(identifier: "zh_CN")
        return f
    }()

    static func fmtNumber(_ value: Double?) -> String {
        guard let value else { return "—" }
        if value == floor(value) {
            return intFormatter.string(from: NSNumber(value: value)) ?? String(format: "%.0f", value)
        }
        return decimalFormatter.string(from: NSNumber(value: value)) ?? String(format: "%.2f", value)
    }

    static func fmtInt(_ value: Int?) -> String {
        guard let value else { return "—" }
        return intFormatter.string(from: NSNumber(value: value)) ?? "\(value)"
    }

    static func fmtSignedInt(_ value: Int?) -> String {
        guard let value else { return "—" }
        let mag = value.magnitude
        let absText = intFormatter.string(from: NSNumber(value: mag)) ?? "\(mag)"
        if value > 0 { return "+\(absText)" }
        if value < 0 { return "-\(absText)" }
        return "0"
    }

    static func fmtSignedDouble(_ value: Double?) -> String {
        guard let value else { return "—" }
        let absText = fmtNumber(abs(value))
        if value > 0 { return "+\(absText)" }
        if value < 0 { return "-\(absText)" }
        return "0"
    }

    static func fmtPct(_ value: Double?) -> String {
        guard let value else { return "—" }
        return (value > 0 ? "+" : "") + String(format: "%.2f%%", value)
    }

    static func fmtShortW(_ value: Int?) -> String {
        guard let value else { return "—" }
        let mag = value.magnitude
        let sign = value > 0 ? "+" : (value < 0 ? "-" : "")
        if mag >= 10_000 {
            return sign + String(format: "%.2f万", Double(mag) / 10_000.0)
        }
        return sign + (intFormatter.string(from: NSNumber(value: mag)) ?? "\(mag)")
    }

    static func shortDay(_ iso: String?) -> String {
        guard let iso, iso.count >= 10 else { return "—" }
        return String(iso.dropFirst(5).prefix(5))
    }

    static func signColor(_ value: Double?) -> Color {
        guard let value, value != 0 else { return NKQihuo.flat }
        return value > 0 ? NKQihuo.up : NKQihuo.down
    }

    static func signColor(_ value: Int?) -> Color {
        guard let value, value != 0 else { return NKQihuo.flat }
        return value > 0 ? NKQihuo.up : NKQihuo.down
    }

    static func message(for error: QihuoBoardError) -> MorningMessage {
        switch error {
        case .missingToken:
            return MorningMessage(title: "未配置访问令牌", detail: "请先在设置里填写 Neckline API 令牌。", problem: true, retry: false, settings: true)
        case .unauthorized:
            return MorningMessage(title: "访问令牌无效", detail: "服务端拒绝了当前令牌，请在设置里重新检查。", problem: true, retry: false, settings: true)
        case .notConfigured:
            return MorningMessage(title: "后端尚未配置", detail: "服务器未配置数据缓存库，暂时无法读取 Q 期货看板。", problem: true, retry: true)
        case .dbNotReady:
            return MorningMessage(title: "数据缓存尚未就绪", detail: "后端数据库正在初始化或版本未匹配，请稍后重试。", problem: true, retry: true)
        case .unsupportedSchema(let detail):
            return MorningMessage(
                title: "Q 期货协议版本不兼容",
                detail: detail ?? "当前 App 仅支持 schema 3，请升级客户端后再试。",
                problem: true,
                retry: true
            )
        case .unavailable(let detail):
            return MorningMessage(
                title: "Q 期货暂不可用",
                detail: detail ?? "Qihuo 数据源暂时没有返回可用快照，请稍后重试。",
                problem: true,
                retry: true
            )
        case .unreachable:
            return MorningMessage(title: "暂时连不上服务器", detail: "请检查网络连接后下拉重试。", problem: true, retry: true)
        case .unreadable:
            return MorningMessage(title: "Q 期货数据无法解析", detail: "服务端返回了无法识别的内容，请稍后重试。", problem: true, retry: true)
        }
    }
}
