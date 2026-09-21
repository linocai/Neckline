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
        "data_gap":"行情资料缺失", "historical_evidence_requires_investigation_path":"历史依据仍需定向核验",
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
    return "原因尚待说明，请结合来源核对"
}
func k10AnomalyReasonText(_ value: String) -> String {
    guard value.hasPrefix("cross_source_conflict:") else { return k10ReasonText(value) }
    let rawFields = value.dropFirst("cross_source_conflict:".count).split(separator: ",").map(String.init)
    let fields = rawFields.map(k10FieldNameText).joined(separator: "、")
    return fields.isEmpty ? "不同来源数值存在冲突" : "不同来源的\(fields)数值存在冲突"
}
func k10CoverageGapText(_ value: String) -> String {
    if value.hasPrefix("morning_source_") { return "晨间资料覆盖不完整，复核结论仍有资料缺口" }
    if value.hasPrefix("morning_review_") { return "部分晨间复核尚未完成，已完成内容保留" }
    let known = [
        "historical_success_case_missing":"历史成功案例缺失", "historical_flat_case_missing":"历史平淡案例缺失",
        "historical_failure_case_missing":"历史失败案例缺失", "independent_verification_missing":"独立核验资料缺失",
        "source_coverage_incomplete":"来源覆盖不完整", "morning_source_missing":"晨间资料缺失",
        "task_failed":"任务执行失败", "data_gap":"行情资料缺失"
    ]
    if let translated = known[value] { return translated }
    if value.unicodeScalars.contains(where: { $0.value >= 0x4E00 && $0.value <= 0x9FFF }) { return value }
    return "存在未说明的资料缺口"
}

func k10VisibleCoverageGapTexts(_ values: [String], responseReason: String?) -> [String] {
    let normalizedReason = responseReason?.trimmingCharacters(in: .whitespacesAndNewlines)
    var seen = Set<String>()
    return values.compactMap { value in
        let text = k10CoverageGapText(value)
        let repeatsReviewSummary = value.hasPrefix("morning_review_") && (normalizedReason?.contains("晨间复核") ?? false)
        guard text != normalizedReason, !repeatsReviewSummary, seen.insert(text).inserted else { return nil }
        return text
    }
}

struct K10IncompleteReviewGroup: Identifiable {
    let companyWindowId: String
    let companyCode: String
    var reviews: [K10IncompleteReview]
    var id: String { companyCode }
}

func k10IncompleteReviewGroups(_ reviews: [K10IncompleteReview]) -> [K10IncompleteReviewGroup] {
    var groups: [K10IncompleteReviewGroup] = []
    for review in reviews {
        if let index = groups.firstIndex(where: { $0.companyCode == review.companyCode }) {
            groups[index].reviews.append(review)
        } else {
            groups.append(K10IncompleteReviewGroup(
                companyWindowId: review.companyWindowId,
                companyCode: review.companyCode,
                reviews: [review]
            ))
        }
    }
    return groups
}

func k10DeliveryOutcomeText(_ value: String) -> String {
    ["complete": "完整交付", "partial": "部分完成", "failed": "整体失败"][value] ?? "交付状态待核"
}

struct K10DeliveryPresentation: Equatable {
    enum Tone: Equatable { case positive, caution, negative }

    let title: String
    let message: String
    let tone: Tone
}

struct K10OpportunityEmptyPresentation: Equatable {
    let title: String
    let message: String
}

func k10OpportunityEmptyPresentation(
    responseState: String?,
    hasReportLoadError: Bool,
    reportStatus: String?,
    deliveryOutcome: String?,
    segment: String,
    currentMorningUpdateCount: Int,
    hasEndedRecommendations: Bool,
    responseReason: String?
) -> K10OpportunityEmptyPresentation {
    if responseState == "not_configured" {
        return K10OpportunityEmptyPresentation(
            title: "今天没跑成 · 参数未配置",
            message: "请到设置查看缺少的参数或公司资料，配置齐全后再运行。"
        )
    }
    if hasReportLoadError {
        return K10OpportunityEmptyPresentation(
            title: "暂时无法读取报告",
            message: "可以重新读取；读取失败不代表本轮没有机会。"
        )
    }
    guard let reportStatus else {
        return K10OpportunityEmptyPresentation(
            title: "等待首次报告",
            message: "正式报告完成后在这里逐张查看，未操作的公司记为未处理。"
        )
    }
    // B76 supplies an explicit delivery outcome.  Earlier published failure
    // reports predate that additive field, but their terminal report status
    // and envelope reason are still authoritative.
    if deliveryOutcome == "failed" || (deliveryOutcome == nil && reportStatus == "failed") {
        return K10OpportunityEmptyPresentation(
            title: "今天没跑成",
            message: responseReason ?? "本轮执行未能形成正式交付，没有新增机会卡。"
        )
    }
    // A published card that has reached D2 belongs to history even when the
    // report itself was partial. This is distinct from a partial report that
    // never published a card at all.
    if hasEndedRecommendations {
        return K10OpportunityEmptyPresentation(
            title: "暂无进行中的机会",
            message: "本轮已发布的公司已撤回或结束观察，可在下方历史记录中查看原报告与两日窗口。"
        )
    }
    if segment == "morning", currentMorningUpdateCount > 0 {
        return K10OpportunityEmptyPresentation(
            title: "本次没有新增卡片",
            message: "已有公司的晨间更新列在上方，原有选择继续保留。"
        )
    }
    if deliveryOutcome == "partial" {
        return K10OpportunityEmptyPresentation(
            title: "本轮未完成，不能判断是否没有机会",
            message: "存在执行缺口，本轮没有形成可发布公司，不能据此判断市场没有机会。"
        )
    }
    if ["completed", "published", "available"].contains(reportStatus) {
        return K10OpportunityEmptyPresentation(
            title: segment == "morning" ? "本晨没有新增公司" : "本轮未推荐公司",
            message: segment == "morning"
                ? "已有公司的变化列在晨间更新中，原有选择继续保留。"
                : "本轮比较已完成，没有形成正式推荐。"
        )
    }
    return K10OpportunityEmptyPresentation(
        title: "报告尚未完成",
        message: "正式报告完成后在这里逐张查看，未操作的公司记为未处理。"
    )
}

func k10ShowsOpportunityEmptyState(segment: String, currentMorningUpdateCount: Int) -> Bool {
    segment != "morning" || currentMorningUpdateCount == 0
}

func k10DeliveryPresentation(
    outcome: String,
    reportStatus: String,
    incompleteReviewCount: Int
) -> K10DeliveryPresentation {
    if outcome == "complete", reportStatus == "partial" {
        if incompleteReviewCount > 0 {
            return K10DeliveryPresentation(
                title: "部分完成",
                message: "本轮消息已全部处理；部分晨间复核未完成，以下展示已完成的发现结果。",
                tone: .caution
            )
        }
        return K10DeliveryPresentation(
            title: "部分完成",
            message: "本轮消息已全部处理，但报告仍有未完成部分，请结合缺口阅读。",
            tone: .caution
        )
    }
    switch outcome {
    case "complete":
        return K10DeliveryPresentation(
            title: "完整交付",
            message: "本轮消息已全部处理，结果按全部处理范围排序。",
            tone: .positive
        )
    case "partial":
        return K10DeliveryPresentation(
            title: "部分完成",
            message: "部分消息处理失败，以下仅展示不受影响的结果。",
            tone: .caution
        )
    case "failed":
        return K10DeliveryPresentation(
            title: "整体失败",
            message: "执行失败，未形成新的正式机会卡；详情可查看。",
            tone: .negative
        )
    default:
        return K10DeliveryPresentation(
            title: "交付状态待核",
            message: "服务返回了无法识别的交付状态，请结合缺口阅读。",
            tone: .caution
        )
    }
}

func k10DeliveryGapMessageText(_ value: String) -> String {
    if value.contains("供应商内容策略拒绝") || value.contains("不参与本轮聚合推荐") {
        return "这条消息未能完成资料处理，关联公司未纳入本轮结果。"
    }
    return value
}

func k10DeliveryGapReasonText(_ value: String) -> String {
    let known = [
        // `content_policy_refused` is the production B76 reason code.  Keep
        // the earlier provider-prefixed spelling readable for saved data.
        "content_policy_refused": "内容被供应商拒绝",
        "provider_content_policy_refused": "内容被供应商拒绝",
        "insufficient_balance": "模型服务余额不足",
        "provider_authorization_failed": "供应商授权未通过",
        "provider_call_failed": "模型服务未能完成本次处理",
        "morning_closeout_reserve": "为按时交付，未启动新的晨间复核",
        "rate_limited": "模型服务限流",
        "provider_http_402": "模型服务余额不足",
        "provider_http_429": "模型服务限流",
        "morning_review_failed": "部分晨间复核未完成",
        "morning_review_not_configured": "晨间复核参数未配置",
        "not_configured": "参数未配置",
        "model_output_invalid": "模型回复格式无效",
        "model_response_unreadable": "模型回复无法读取",
        "model_response_incomplete": "模型回复不完整",
        "network_retry_exhausted": "网络重试已用尽",
        "source_unavailable": "资料来源暂不可用",
        "dependency_unresolved": "依赖关系未能确认",
        "ranking_invalid": "最终排序无效",
        "input_manifest_invalid": "冻结输入无法核验"
    ]
    return known[value] ?? k10ReasonText(value)
}

func k10ExecutionStateText(_ value: String?) -> String {
    switch value {
    case "accepting": return "允许新任务"
    case "draining": return "只结算在途请求"
    case "paused": return "处理已暂停"
    case "blocked": return "新任务已阻止"
    default: return "运行状态待核"
    }
}

func k10ExecutionStageText(_ value: String) -> String {
    ["pending": "等待处理", "queued": "等待处理", "created": "等待处理", "fetched": "已获取资料", "ingestion": "正在获取", "title_triage": "正在理解标题", "understanding": "正在理解", "understand": "正在理解", "full_text": "正在补充原文", "awaiting_verification": "等待核验", "verification": "正在核验", "verify": "正在核验", "comparison": "正在比较", "company_comparison": "正在比较", "prioritize": "正在确定发布顺序", "publication": "正在发布", "published": "已发布", "retired": "已停用", "failed_pending": "失败待恢复", "recovery": "等待恢复", "retry_scheduled": "等待恢复", "completed": "已完成" ][value] ?? "处理状态待核"
}

func k10ExecutionFailureText(_ value: String) -> String {
    let known = [
        "insufficient_balance": "模型服务余额不足，任务已停止并保留进度",
        "rate_limited": "模型服务限流，按重试安排继续",
        "provider_http_402": "模型服务余额不足，任务已停止并保留进度",
        "provider_http_429": "模型服务限流，按重试安排继续",
        "model_output_invalid": "模型返回格式待修复", "model_response_unreadable": "模型响应无法读取",
        "model_response_incomplete": "模型响应不完整", "network_retry_exhausted": "网络重试已用尽",
        "source_unavailable": "资料来源暂不可用", "credentials_missing": "推送凭证未配置",
        "key_unreadable": "推送凭证不可读取", "key_invalid": "推送凭证无效",
        "notification_schema_unavailable": "推送状态暂不可用"
    ]
    return known[value] ?? "已记录的安全错误"
}

func k10CatalystStageText(_ value: String) -> String {
    if value == "initial" { return "初始阶段" }
    if value.unicodeScalars.contains(where: { (0x4E00...0x9FFF).contains($0.value) }) { return value }
    return "催化阶段待说明"
}

func k10SettingsReadMessage(_ state: K10SettingsReadState) -> String? {
    switch state {
    case .idle: return "尚未读取配置"
    case .loading: return "正在读取配置…"
    case .failed: return "配置读取失败"
    case .loaded: return nil
    }
}

// Keep business facts visible; identifiers and the exact payload remain in diagnostics.
func k10LifecycleFactLines(_ content: [String: K10Value]) -> [String] {
    let labels = ["reasonStatus": "推荐依据", "sourceStatus": "资料状态", "observationStatus": "观察状态",
                  "materialContraryEvidence": "重大反证", "summary": "说明", "text": "内容", "reason": "原因",
                  "statement": "事实", "claim": "说明", "title": "标题", "evidenceDisclosure": "核验说明",
                  "conditionalAnalysis": "条件判断", "unverifiedReasons": "待核原因", "verificationStatus": "核验状态",
                  "isRumor": "是否传闻", "originStatus": "源头状态"]
    let states = ["current": "仍有效", "needs_review": "需要复核", "invalidated": "已失效", "unavailable": "暂不可用",
                  "complete": "完整", "partial": "部分完成", "expired": "已到期", "verified": "已核验",
                  "partially_supported": "部分支持", "contradicted": "存在反证", "unverified": "未核实",
                  "identified": "已识别", "unknown": "未知"]
    func lines(_ value: K10Value) -> [String] {
        switch value {
        case .string(let value): return [states[value] ?? k10ReasonText(value)]
        case .bool(let value): return [value ? "是" : "否"]
        case .number(let value): return [String(value)]
        case .array(let values): return values.flatMap(lines)
        case .object(let values):
            return values.keys.sorted().flatMap { key -> [String] in
                let metadata = ["sourceMarker", "cutoffAt", "sourceRefs", "independentVerificationRefs", "originEvidenceRef", "requiresReview", "automaticDebateStarted", "material"]
                guard !metadata.contains(key), !key.hasSuffix("Id"), !key.hasSuffix("Ids"), let value = values[key] else { return [] }
                let label = labels[key] ?? (key.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) } ? key : "补充资料")
                return lines(value).map { "\(label)：\($0)" }
            }
        case .null: return []
        }
    }
    return lines(.object(content))
}
