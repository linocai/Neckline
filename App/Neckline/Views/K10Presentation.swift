import SwiftUI
import Foundation

func k10DisplayTime(_ raw: String) -> String {
    if raw.count == 10, raw.filter({ $0 == "-" }).count == 2 { let p = raw.split(separator: "-"); return "\(Int(p[1]) ?? 0)月\(Int(p[2]) ?? 0)日" }
    let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    guard let date = f.date(from: raw) ?? ISO8601DateFormatter().date(from: raw) else { return raw }
    let display = DateFormatter(); display.locale = Locale(identifier: "zh_CN"); display.timeZone = TimeZone(identifier: "Asia/Shanghai"); display.dateFormat = "M月d日 HH:mm"; return display.string(from: date)
}
func k10StatusText(_ value: String) -> String { [
    "primary":"主样本", "overlap":"重叠机会", "published":"已发布", "evidence_update":"依据更新", "risk":"风险提示", "withdrawal":"系统撤回", "expired":"已到期",
    "keep":"留下", "kept":"已留下", "skip":"明确略过", "skipped":"已略过", "restore":"找回", "withdraw":"取消关注", "unhandled":"未处理", "selected":"留下", "active":"进行中",
    "queued":"等待中", "running":"执行中", "configured":"已配置", "completed":"已完成", "failed":"失败", "not_configured":"未配置", "available":"可用", "pending":"未到期", "due":"已到期·待核", "incomplete":"资料不完整", "suspended":"停牌", "data_gap":"行情缺数", "anomaly":"行情异常", "complete":"完整", "partial":"部分完成", "morning":"晨间首发", "evening":"晚间首发", "late":"迟到", "alternative":"备选", "tied":"并列", "primary_recommendation":"主推"
][value] ?? value }
func k10CategoryText(_ value: String) -> String { ["primary":"主推", "alternative":"备选", "tied":"并列"][value] ?? k10StatusText(value) }
func k10SourceText(_ key: String) -> String { ["tushare-major-news":"TuShare 重要资讯", "tavily-object-review":"Tavily 定向核验", "market_snapshot":"行情快照", "synthetic":"合成来源（仅验收）"][key] ?? key }
func k10PublishedPrecisionText(_ value: String) -> String { ["exact":"精确到时刻", "date":"仅日期", "unknown":"日期精度待核"][value] ?? "日期精度待核" }
func k10PublicationMarkerText(_ value: String?) -> String { ["evening":"晚间", "morning":"晨间"][value ?? ""] ?? "来源待核" }
func k10TouchStatusText(_ value: String?) -> String { ["confirmed":"已确认", "not_touched":"两日未触板", "unknown_due_to_d1":"D1 未核，首次触板日未知", "unknown":"触板状态待核"][value ?? ""] ?? "触板状态待核" }
func k10ComparabilityText(_ value: String?) -> String { ["raw_comparable":"原始价格可比", "adjusted_comparable":"已按除权调整可比", "not_comparable":"跨日价格不可比", "unknown":"跨日价格可比性待核"][value ?? ""] ?? "价格可比性待核" }
func k10LimitStatusText(closeLimitUp: Bool?, touchedLimitUp: Bool?, firstTouchedAt: String?) -> String { guard let closeLimitUp, let touchedLimitUp else { return "涨停状态待核" }; if closeLimitUp { return "收盘封板" }; if touchedLimitUp { return "触板未封\(firstTouchedAt.map { " · \($0)" } ?? "")" }; return "未触板" }
