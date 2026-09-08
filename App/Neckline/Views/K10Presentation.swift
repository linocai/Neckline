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
    "queued":"等待中", "running":"执行中", "configured":"已配置", "completed":"已完成", "failed":"失败", "not_configured":"未配置", "notConfigured":"未配置", "ready":"已就绪", "paused":"已暂停", "retired":"已停用", "blocked":"待配置", "pending":"未到期", "due":"已到期·待核", "incomplete":"资料不完整", "suspended":"停牌", "data_gap":"行情缺数", "anomaly":"行情异常", "complete":"完整", "partial":"部分完成", "unavailable":"暂不可用", "verified":"已核验", "conflict":"数值冲突", "single_source":"单一来源", "field_unavailable":"字段待核", "morning":"晨间首发", "evening":"晚间首发", "late":"迟到", "alternative":"备选", "tied":"并列", "primary_recommendation":"主推"
][value] ?? value }
func k10CategoryText(_ value: String) -> String { ["primary":"主推", "alternative":"备选", "tied":"并列", "pending":"待核", "excluded":"排除"][value] ?? k10StatusText(value) }
func k10SourceText(_ key: String) -> String { ["tushare-major-news":"TuShare 重要资讯", "tavily-object-review":"Tavily 定向核验", "market_snapshot":"行情快照", "synthetic":"合成来源（仅验收）"][key] ?? key }
func k10PublishedPrecisionText(_ value: String) -> String { ["exact":"精确到时刻", "date":"仅日期", "unknown":"日期精度待核"][value] ?? "日期精度待核" }
func k10PublicationMarkerText(_ value: String?) -> String { ["evening":"晚间", "morning":"晨间"][value ?? ""] ?? "来源待核" }
func k10TouchStatusText(_ value: String?) -> String { ["confirmed":"已确认", "not_touched":"两日未触板", "unknown_due_to_d1":"D1 未核，首次触板日未知", "unknown":"触板状态待核"][value ?? ""] ?? "触板状态待核" }
func k10ComparabilityText(_ value: String?) -> String { ["raw_comparable":"原始价格可比", "adjusted_comparable":"已按除权调整可比", "not_comparable":"跨日价格不可比", "unknown":"跨日价格可比性待核"][value ?? ""] ?? "价格可比性待核" }
func k10LimitStatusText(closeLimitUp: Bool?, touchedLimitUp: Bool?, firstTouchedAt: String?) -> String { guard let closeLimitUp, let touchedLimitUp else { return "涨停状态待核" }; if closeLimitUp { return "收盘封板" }; if touchedLimitUp { return "触板未封\(firstTouchedAt.map { " · \($0)" } ?? "")" }; return "未触板" }
func k10HistoricalOutcomeText(_ value: String) -> String { ["success":"成功", "flat":"平淡", "failure":"失败", "unclassified":"结果待分类"][value] ?? "结果待分类" }
func k10AnalysisKindText(_ value: String) -> String { ["initial":"初始分析", "user_question":"用户追问", "evidence_update":"补充资料"][value] ?? "分析记录" }
func k10HistoricalOutcomeListText(_ values: [String]) -> String { values.map(k10HistoricalOutcomeText).joined(separator: "、") }
func k10FieldNameText(_ value: String) -> String { ["open":"开盘价", "high":"最高价", "low":"最低价", "close":"收盘价", "preClose":"前收盘价", "limitUpPrice":"涨停价", "adjFactor":"复权因子", "closeLimitUp":"收盘封板", "touchedLimitUp":"触板状态", "suspension":"停牌状态", "pre_close":"前收盘价", "limit_up_price":"涨停价", "close_limit_up":"收盘封板", "touched_limit_up":"触板状态"][value] ?? "行情字段" }
func k10MarketSourceText(_ value: String) -> String { ["tushare.daily":"TuShare 日行情", "tushare.stk_limit":"TuShare 涨停价", "tushare.adj_factor":"TuShare 复权因子", "tushare.suspend_d":"TuShare 停复牌", "realtime.sina":"新浪实时报价", "realtime.tencent":"腾讯实时报价", "tushare":"TuShare", "market_snapshot":"行情快照"][value] ?? "记录来源（\(value)）" }
func k10ReasonText(_ value: String?) -> String {
    guard let value, !value.isEmpty else { return "未记录具体原因，查看来源核对" }
    if value.contains(";") { return value.split(separator: ";").map { k10ReasonText(String($0)) }.joined(separator: "；") }
    if let separator = value.firstIndex(of: ":"), value[..<separator].hasPrefix("realtime.") || ["sina", "tencent"].contains(String(value[..<separator])) {
        return k10ReasonText(String(value[value.index(after: separator)...]))
    }
    let known = [
        "limit_data_unavailable":"涨停价或封板状态缺失", "evaluation_configuration_missing":"评价规则未配置", "coverage_incomplete":"资料覆盖不完整", "source_unavailable":"资料暂不可用", "search_failed":"资料检索未完成",
        "missing_outcomes":"所需历史结果不完整", "single_source_fallback":"仅单一来源，未能交叉核验",
        "quote_trade_date_unproven":"无法证明报价属于目标交易日", "source_conflict":"来源数值存在冲突",
        "field_unavailable":"字段暂不可用", "missing_data":"资料缺失", "same_day_post_close_sources_agree":"两个实时来源同日收市后数值一致",
        "same_day_post_close_sources_disagree":"两个实时来源同日收市后数值不一致", "second_realtime_source_unavailable":"第二个实时来源暂不可用",
        "realtime_dual_verification_not_requested":"未请求双实时来源核验", "source_identity_invalid":"来源身份无法确认",
        "source_timestamp_unparseable":"来源时间无法解析", "source_trade_date_mismatch":"来源日期与目标交易日不符",
        "source_timestamp_before_shanghai_close":"来源采集早于上海收市", "tushare_daily_field_unavailable":"TuShare 当日字段暂不可用",
        "tushare_daily_row_unavailable":"TuShare 当日日行情暂不可用", "suspension_not_confirmed":"停牌状态尚未确认",
        "tushare_suspend_confirmed_without_independent_quote_proof":"TuShare 已记录停牌，尚无独立报价证明",
        "exchange_limit_has_no_independent_realtime_equivalent":"涨停价没有对应的独立实时报价", "adjustment_factor_has_no_independent_realtime_equivalent":"复权因子没有对应的独立实时报价",
        "derived_from_tushare_daily_close_and_exchange_limit":"由日收盘价和交易所涨停价计算", "derived_from_tushare_daily_high_and_exchange_limit":"由日最高价和交易所涨停价计算",
        "price_or_limit_reference_unavailable":"价格或涨停价参考暂不可用", "not_applicable_without_daily_bar":"缺少日行情，暂不适用"
    ]
    if value.hasPrefix("realtime.sina_") || value.hasPrefix("realtime.tencent_") {
        let source = value.contains("sina") ? "新浪实时报价" : "腾讯实时报价"
        if value.hasSuffix("no_traded_price") { return "\(source)没有可核对的成交价" }
        if value.hasSuffix("field_unavailable") { return "\(source)该字段暂不可用" }
        if value.hasSuffix("not_comparable") { return "\(source)无法与目标日行情比较" }
    }
    if value.hasSuffix("_field_unavailable") && value.hasPrefix("tushare.") {
        return "\(k10MarketSourceText(String(value.dropLast("_field_unavailable".count))))该字段暂不可用"
    }
    if let translated = known[value] { return translated }
    if value.unicodeScalars.contains(where: { $0.value >= 0x4E00 && $0.value <= 0x9FFF }) { return value }
    return "已记录原因：\(value)（请结合来源核对）"
}
func k10AnomalyReasonText(_ value: String) -> String {
    guard value.hasPrefix("cross_source_conflict:") else { return k10ReasonText(value) }
    let rawFields = value.dropFirst("cross_source_conflict:".count).split(separator: ",").map(String.init)
    let fields = rawFields.map(k10FieldNameText).joined(separator: "、")
    return fields.isEmpty ? "不同来源数值存在冲突" : "不同来源的\(fields)数值存在冲突"
}
func k10CoverageGapText(_ value: String) -> String {
    let known = [
        "historical_success_case_missing":"历史成功案例缺失", "historical_flat_case_missing":"历史平淡案例缺失",
        "historical_failure_case_missing":"历史失败案例缺失", "independent_verification_missing":"独立核验资料缺失",
        "source_coverage_incomplete":"来源覆盖不完整", "morning_source_missing":"晨间资料缺失",
        "task_failed":"任务执行失败", "data_gap":"行情资料缺失"
    ]
    return known[value] ?? "存在未说明的资料缺口"
}

func k10ExecutionStageText(_ value: String) -> String {
    ["pending": "等待处理", "queued": "等待处理", "created": "等待处理", "fetched": "已获取资料", "ingestion": "正在获取", "title_triage": "正在理解标题", "understanding": "正在理解", "understand": "正在理解", "full_text": "正在补充原文", "awaiting_verification": "等待核验", "verification": "正在核验", "verify": "正在核验", "comparison": "正在比较", "company_comparison": "正在比较", "prioritize": "正在确定发布顺序", "publication": "正在发布", "published": "已发布", "retired": "已停用", "failed_pending": "失败待恢复", "recovery": "等待恢复", "retry_scheduled": "等待恢复", "completed": "已完成" ][value] ?? "处理状态待核"
}

func k10ExecutionFailureText(_ value: String) -> String {
    let known = [
        "model_output_invalid": "模型返回格式待修复", "model_response_unreadable": "模型响应无法读取",
        "model_response_incomplete": "模型响应不完整", "network_retry_exhausted": "网络重试已用尽",
        "source_unavailable": "资料来源暂不可用", "credentials_missing": "推送凭证未配置",
        "key_unreadable": "推送凭证不可读取", "key_invalid": "推送凭证无效",
        "notification_schema_unavailable": "推送状态暂不可用"
    ]
    return known[value] ?? "已记录的安全错误"
}
