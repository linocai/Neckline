//
//  AuctionModels.swift
//  Neckline — 客户端展示层数据模型 · V2.3.3-⑤ D1 集合竞价确认层(`GET /auction`,K8.md §二十)
//
//  ⚠ **V2.4.0 P3.7 纯机械拆分**:本文件与同目录另外五份 `*Models.swift` 是原
//  `Networking/Models.swift`(5633 行)**逐字**切出来的,⛔ 一个声明没改、没加、没删
//  (切点全在顶层 `// MARK:` 之前的空行上;拆分脚本对拼回来的全文做过逐字节比对)。
//  🔴 **守门单测不再按 `Models.swift` 这个文件名读客户端 DTO** —— 一律走
//  `tests/client_sources.py::networking_swift_text()`(把本目录全部 `.swift` 拼起来)。
//  ⛔ 新增 DTO 文件必须放在本目录下,否则那些「某字段已退役」的**缺席断言**会静默变成
//  真(读不到的文件里当然搜不到),看起来还全绿 —— 这是拆分引入的唯一新风险面,
//  `tests/client_sources.py` 里的哨兵断言就是为它立的。
//
//  ⚠ 加 / 移动 `.swift` 与新增一样,**必须 `xcodegen generate`**(pbxproj 是显式文件引用)。
//

import Foundation

// MARK: - V2.3.3-⑤ D1 集合竞价确认层(`GET /auction`,K8.md §二十)
//
//  **竞价小报告五块**:数据状态 / 市场与主线概览 / 篮子与逐票结论 / 异常与风险 /
//  APP 人工观察小纸条。
//
//  🔴 **服务端只发英文枚举码,中文在这里换算**(下面五个 `nkAuction*Label`,
//  沿 `nkBoardLabel` / `nkRegimeDimLabel` 先例,**未识别值原样透传**)——
//  ⛔ 见到服务端的码直连 `Text` 就停一下(V2.3.1 硬伤 2 连踩三次的那类 bug)。
//
//  🔴 **全部手写 `init(from:)` + 全字段 `decodeIfPresent`**(V2-⑮ 起硬要求):
//  `auction_verdicts` 的 json 列是**写入当时冻住**的 B 类快照,服务端升级永远不会给
//  老行补新键 —— 合成 `Decodable` 对非 Optional 属性「有默认值也不容忍缺键」,
//  一个新键就能让昨天那份报告整条解不出。
//
//  ⚠ **命名自检**:`AuctionPayload` / `AuctionDataStatus` / `AuctionMarketOverview` /
//  `AuctionVerdict` / `AuctionMemberRow` / `AuctionRiskItem` / `AuctionIndexGap`
//  **两两不互为前缀**(守门切块器按 `^struct <Name>\b` 定位,同前缀会切错块)。

/// **竞价结论码 → 中文**(唯一源 `neckline/auction/__init__.py` 的 `VERDICT_*`)。
///
/// ⚠ `pending_explanation`「待解释」**不是第四种结论**,它是「LLM 没给出解释」这件事
/// 本身(K8 §二十:LLM 暂时不可用时其余结论标记为「待解释」)—— ⛔ 别把它显示成
/// 「中性」,那会把系统缺席讲成一次实质判断。**未识别值原样透传**。
func nkAuctionVerdictLabel(_ raw: String) -> String {
    switch raw {
    case "confirm": return "确认"
    case "neutral": return "中性"
    case "veto": return "否决"
    case "pending_explanation": return "待解释"
    default: return raw
    }
}

/// **数据质量三态码 → 中文**(唯一源 `neckline/auction/__init__.py` 的 `DQ_*`)。
/// 判据是**结构性的**(全有 / 全无 / 其余),⛔ 不是百分比。**未识别值原样透传**。
func nkAuctionDataQualityLabel(_ raw: String) -> String {
    switch raw {
    case "ok": return "齐全"
    case "degraded": return "有缺失"
    case "insufficient": return "数据不足"
    default: return raw
    }
}

/// **机械夹逼闸命中码 → 中文**(唯一源 `neckline/auction/__init__.py` 的 `CLAMPED_BY_*`)。
///
/// 🔴 非空 = 模型给的结论**被系统改过** —— 必须当面说出口(⛔ 不许静默夹逼,
/// 同 V2.3.2 ⑧-0 路径 A 的裁定)。**未识别值原样透传**。
func nkAuctionClampLabel(_ raw: String) -> String {
    switch raw {
    case "clamped_by_data_quality": return "数据缺失只能形成中性"
    case "clamped_by_single_strong": return "只有一只竞价强股,保持中性"
    case "clamped_by_missing_strong_evidence": return "没给出竞价强股证据,保持中性"
    case "clamped_by_y1_low_weight": return "Y1 低权重:未达否决条件"
    default: return raw
    }
}

/// **开盘价与 D0 冻结预案的一致性五态 → 中文**(唯一源 `PLAN_FIT_*`)。
/// ⚠ `unknown` = 卡上没给区间 or 价拿不到 = **判不了**,⛔ 不是「不符合」。
/// **未识别值原样透传**。
func nkAuctionPlanFitLabel(_ raw: String) -> String {
    switch raw {
    case "in_zone": return "在建仓区间内"
    case "above_zone_below_chase": return "高于区间、未超最高追价"
    case "above_max_chase": return "已超最高追价"
    case "below_zone": return "低于建仓区间"
    case "unknown": return "判不了"
    default: return raw
    }
}

/// **异常与风险的种类码 → 中文**(唯一源 `neckline/auction/__init__.py` 的 `RISK_*`)。
/// **未识别值原样透传**。
func nkAuctionRiskKindLabel(_ raw: String) -> String {
    switch raw {
    case "data_missing": return "数据缺失"
    case "source_conflict": return "跨源冲突"
    case "quote_invalid": return "读数未通过校验"
    case "source_degraded": return "改用了备用源"
    case "single_strong": return "单只强势"
    case "gap_up_deviation": return "高开偏离计划"
    case "hit_invalidation": return "触发 D0 失效位"
    case "anchor_stale": return "冻结锚失效"
    case "invalidation_undetermined": return "失效判定没判"
    case "auction_volume_anomaly": return "竞价量能异常"
    case "evidence_conflict": return "证据矛盾"
    case "verdict_clamped": return "结论被机械夹逼"
    case "llm_unavailable": return "LLM 缺席"
    case "llm_note": return "模型补充"
    default: return raw
    }
}

/// **「没判」原因码 → 中文**(唯一源 `neckline/auction/__init__.py` 的 `UNDET_*`)。
///
/// 🔴 `hitInvalidation` / `gapUpDeviation` 的 `nil` = **一个字都没核对过**,
/// ⛔ 不是「看过了、没问题」—— 这个函数就是把那个 `nil` 讲成人话的地方。
/// **未识别值原样透传**;码为空(老行没冻这两个键)→ 「原因未记录」,⛔ 仍不许说成「无异常」。
func nkAuctionUndeterminedReasonLabel(_ raw: String?) -> String {
    switch raw ?? "" {
    case "no_quote": return "这只票本次没抓到报价"
    case "no_member_script": return "D0 卡上没有这只成员的冻结剧本"
    case "anchor_stale": return "冻结锚今日失效(疑似除权除息)"
    case "no_stop_line": return "卡上没冻结失效位价格"
    case "no_ref_close": return "卡上没冻结 D0 收盘锚"
    case "no_open_price": return "行情源还没发出开盘价"
    case "": return "原因未记录"
    default: return raw ?? "原因未记录"
    }
}

/// **相对强弱「没有这个读数」的原因码 → 中文**(唯一源 `neckline/auction/__init__.py`
/// 的 `REL_UNDETERMINED_CODES`)。**未识别值原样透传**(⛔ 不许把码直接印上屏)。
///
/// 🔴 每一条都在说「这个数**没有**」—— ⛔ 一条都不许被读成「持平」或渲染成 0
/// (用户裁定 P3-70 的红线:「没有」≠「不满足」≠「持平」)。
///
/// 🔴 **`sectorPeerMin` 必须由服务端下发**(定向复审 🔵-1):那个 3 是**用户裁定值**,
/// 单一源在服务端 `auction.SECTOR_PEER_MIN`、随 `relStrength.sector_peer_min` 一起发下来。
/// ⛔ 客户端不许再抄一份字面量 —— 裁定值改一次两边就打架,而且没有守门看得见。
/// 取不到时退回**不带数字**的说法(⛔ 不猜一个数)。
func nkAuctionRelReasonLabel(_ raw: String?, sectorPeerMin: Int? = nil) -> String {
    switch raw ?? "" {
    case "no_member_gap": return "这只票自己的竞价涨跌幅就算不出"
    case "board_excluded": return "科创板按 K8 基础股票池规则排除,不设市场指数对照"
    case "no_board_meta": return "查不到这只票的板块归属"
    case "no_industry": return "查不到这只票的行业口径,无从取板块对照股"
    case "industry_map_unavailable": return "本次整张行业表都没读到(系统缺席),不是这只票没有行业"
    case "data_insufficient":
        guard let n = sectorPeerMin else { return "有效板块对照股不足(下限服务端未下发)" }
        return "有效板块对照股不足 \(n) 只"
    case "": return "原因未记录"
    default: return raw ?? "原因未记录"
    }
}

/// **板块基准来源码 → 中文**(唯一源 `auction.SECTOR_BENCH_*`)。**未识别值原样透传**。
func nkAuctionSectorBenchSourceLabel(_ raw: String) -> String {
    switch raw {
    case "sector_index": return "板块指数"
    case "peer_median": return "同行业对照股中位"
    case "unavailable": return "未取得"
    default: return raw
    }
}

/// **逐票双源核验状态码 → 中文**(唯一源 `auction.QUOTE_FRESHNESS_CODES`,V2.4.0 P2.2)。
///
/// 🔴 空串 = **老快照没记这一位**(V2.4.0 之前冻的 `members_json`)——
/// ⛔ 绝不许渲染成「校验通过」:那是把「没看」讲成「看过了没事」。**未识别值原样透传**。
func nkAuctionQuoteFreshnessLabel(_ raw: String) -> String {
    switch raw {
    case "fresh": return "读数合格"
    case "degraded": return "读数可用但有缺项"
    case "insufficient": return "读数不可用"
    case "conflict": return "两源结论冲突"
    case "": return "本次未记录"
    default: return raw
    }
}

/// **七项校验的失败码 → 中文**(唯一源 `auction/quality.py::VALIDATION_ERROR_CODES`)。
/// **未识别值原样透传**(⛔ 不许把码直接印上屏)。
func nkAuctionValidationErrorLabel(_ raw: String) -> String {
    switch raw {
    case "code_mismatch": return "拿回来的代码与要的那一只对不上"
    case "wrong_trade_date": return "源日期不是今天(疑似上一交易日的缓存行情)"
    case "future_timestamp": return "源时间晚于本机抓取时刻"
    case "before_final_auction": return "源时间早于 9:25 最终撮合"
    case "timestamp_unparseable": return "源时间戳解不出来"
    case "required_field_missing": return "现价或前收盘价无效"
    case "open_price_missing": return "源还没发出开盘价"
    case "price_relation_inconsistent": return "价格关系自相矛盾"
    case "negative_volume": return "成交量为负"
    case "negative_amount": return "成交额为负"
    default: return raw
    }
}

/// **跨源结论性冲突码 → 中文**(唯一源 `auction.CONFLICT_CODES`,V2.4.0 P2.2)。
/// 🔴 出现任意一条 = 两个行情源对**同一件事**给出了相反的结论 → ⛔ 不能高置信输出。
/// **未识别值原样透传**。
func nkAuctionConflictLabel(_ raw: String) -> String {
    switch raw {
    case "direction_opposite": return "两源涨跌方向相反"
    case "invalidation_disagree": return "一源触发 D0 失效位、另一源不触发"
    case "plan_zone_disagree": return "一源进了预案区间、另一源没进"
    case "identity_mismatch": return "两源的代码 / 前收 / 交易日对不上"
    default: return raw
    }
}

/// **数据质量分域码 → 中文**(V2.4.0 P2.3)。
///
/// 🔴 `nil` = **旧版本未细分**(施工图 §五 P2.3 逐字:旧报告没有这些字段时显示
/// 「旧版本未细分」,⛔ **不得默认成正常**)—— 这个函数就是那条纪律的落点。
func nkAuctionDomainQualityLabel(_ raw: String?) -> String {
    guard let v = raw, !v.isEmpty else { return "旧版本未细分" }
    return nkAuctionDataQualityLabel(v)
}

/// **LLM 段状态码 → 中文**(唯一源 `neckline/auction/__init__.py` 的 `LLM_*`)。
///
/// ⚠ `pending_explanation` 是**设计内**的:9:29 硬截止到了模型还没回,迟到的结论
/// 一律丢弃 —— ⛔ 文案别写成"出错了"。`call_failed:<原因>` 带冒号后缀,故走前缀匹配。
/// **未识别值原样透传**。
func nkAuctionLlmStageLabel(_ raw: String) -> String {
    if raw.hasPrefix("call_failed") { return "调用失败" }
    switch raw {
    case "pending": return "等待解释"
    case "ok": return "已解释"
    case "pending_explanation": return "待解释(9:29 前未返回)"
    case "provider_none": return "未配置模型"
    case "parse_failed": return "输出解析失败"
    case "budget_exhausted": return "预算已耗尽"
    default: return raw
    }
}

/// **一源**对一只代码的原始读数 + 七项校验结果(V2.4.0 P2.1/P2.2)。
/// 🔴 两源都在(K8 §二十:「两个来源的原始读数全部留存」)—— 界面要能回答
/// 「备源当时说的是什么」。
struct AuctionQuoteCheck: Decodable, Equatable, Identifiable {
    var role: String = ""
    var source: String = ""
    var status: String = ""
    var errors: [String] = []
    var tsRaw: String = ""
    var tsParsed: String? = nil
    var price: Double? = nil
    var preClose: Double? = nil
    var open: Double? = nil
    var volume: Double? = nil
    var amount: Double? = nil

    var id: String { role + "|" + source }

    enum CodingKeys: String, CodingKey {
        case role, source, status, errors, tsRaw, tsParsed, price, preClose, open, volume, amount
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        role = try c.decodeIfPresent(String.self, forKey: .role) ?? ""
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? ""
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? ""
        errors = try c.decodeIfPresent([String].self, forKey: .errors) ?? []
        tsRaw = try c.decodeIfPresent(String.self, forKey: .tsRaw) ?? ""
        tsParsed = try c.decodeIfPresent(String.self, forKey: .tsParsed)
        price = try c.decodeIfPresent(Double.self, forKey: .price)
        preClose = try c.decodeIfPresent(Double.self, forKey: .preClose)
        open = try c.decodeIfPresent(Double.self, forKey: .open)
        volume = try c.decodeIfPresent(Double.self, forKey: .volume)
        amount = try c.decodeIfPresent(Double.self, forKey: .amount)
    }

    /// 「主源 / 备源」——⛔ 不印 `primary` / `backup` 这两个码。
    var roleLabel: String {
        switch role {
        case "primary": return "主源"
        case "backup": return "备源"
        default: return role
        }
    }
    /// 这一源当时说了什么 + 校验结果一句。**未通过的项逐条点名**,⛔ 不含糊。
    var summaryText: String {
        let ts = (tsParsed?.isEmpty == false) ? tsParsed! : (tsRaw.isEmpty ? "源时间未记录" : tsRaw)
        let verdict = errors.isEmpty
            ? "七项校验全过"
            : errors.map(nkAuctionValidationErrorLabel).joined(separator: ";")
        return "\(roleLabel)(\(source.isEmpty ? "未记录" : source))\(ts) —— \(verdict)"
    }
}

/// 逐票双源核验的一条账(V2.4.0 P2.2)。
/// 🔴 `sourceDegraded` = **主源不可用、本次用的是备源**(⛔ 不许静默换源);
/// `conflict` 非空 = 两源结论相反 → ⛔ 不能高置信输出。
struct AuctionQualityDetail: Decodable, Equatable, Identifiable {
    var tsCode: String = ""
    var freshness: String = ""
    var status: String = ""
    var chosenRole: String? = nil
    var chosenSource: String? = nil
    var sourceDegraded: Bool = false
    var conflict: String? = nil
    var errors: [String] = []
    var checks: [AuctionQuoteCheck] = []

    var id: String { tsCode }

    enum CodingKeys: String, CodingKey {
        case tsCode, freshness, status, chosenRole, chosenSource, sourceDegraded
        case conflict, errors, checks
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        freshness = try c.decodeIfPresent(String.self, forKey: .freshness) ?? ""
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? ""
        chosenRole = try c.decodeIfPresent(String.self, forKey: .chosenRole)
        chosenSource = try c.decodeIfPresent(String.self, forKey: .chosenSource)
        sourceDegraded = try c.decodeIfPresent(Bool.self, forKey: .sourceDegraded) ?? false
        conflict = try c.decodeIfPresent(String.self, forKey: .conflict)
        errors = try c.decodeIfPresent([String].self, forKey: .errors) ?? []
        checks = try c.decodeIfPresent([AuctionQuoteCheck].self, forKey: .checks) ?? []
    }

    var freshnessLabel: String { nkAuctionQuoteFreshnessLabel(freshness) }
    var conflictLabel: String? { conflict.map(nkAuctionConflictLabel) }
    /// 只有**值得说出口**的那些才画:不合格 / 冲突 / 换过源。全好的那些不占版面。
    var worthShowing: Bool { freshness != "fresh" || sourceDegraded || conflict != nil }
}

/// 小报告第 1 块「数据状态」。
///
/// 🔴 **V2.4.0 P2.2 起 `conflictCodes` 真的会有值**:双源批量核验已上线。
/// V2.3.3 时代「结构性恒空」的旧口径已被 K8 §二十「有界双源核验」推翻。
/// 🔴 **P2.3 分域**:`criticalDataQuality` / `contextDataQuality` 为 `nil` 时
/// 必须显示「旧版本未细分」,⛔ 不得默认成正常。
struct AuctionDataStatus: Decodable, Equatable {
    var source: String = "unknown"
    var capturedAt: String = ""
    var requestedCodes: Int = 0
    var fetchedCodes: Int = 0
    var missingCodes: [String] = []
    var invalidCodes: [String] = []
    var conflictCodes: [String] = []
    var dataQuality: String = "insufficient"
    var criticalDataQuality: String? = nil
    var contextDataQuality: String? = nil
    var qualityDetails: [AuctionQualityDetail] = []
    var validationErrors: [String] = []

    enum CodingKeys: String, CodingKey {
        case source, capturedAt, requestedCodes, fetchedCodes, missingCodes, conflictCodes, dataQuality
        case invalidCodes, criticalDataQuality, contextDataQuality, qualityDetails, validationErrors
    }

    init(source: String = "unknown", capturedAt: String = "", requestedCodes: Int = 0,
         fetchedCodes: Int = 0, missingCodes: [String] = [], conflictCodes: [String] = [],
         dataQuality: String = "insufficient", invalidCodes: [String] = [],
         criticalDataQuality: String? = nil, contextDataQuality: String? = nil,
         qualityDetails: [AuctionQualityDetail] = [], validationErrors: [String] = []) {
        self.source = source; self.capturedAt = capturedAt
        self.requestedCodes = requestedCodes; self.fetchedCodes = fetchedCodes
        self.missingCodes = missingCodes; self.conflictCodes = conflictCodes
        self.dataQuality = dataQuality; self.invalidCodes = invalidCodes
        self.criticalDataQuality = criticalDataQuality
        self.contextDataQuality = contextDataQuality
        self.qualityDetails = qualityDetails; self.validationErrors = validationErrors
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? "unknown"
        capturedAt = try c.decodeIfPresent(String.self, forKey: .capturedAt) ?? ""
        requestedCodes = try c.decodeIfPresent(Int.self, forKey: .requestedCodes) ?? 0
        fetchedCodes = try c.decodeIfPresent(Int.self, forKey: .fetchedCodes) ?? 0
        missingCodes = try c.decodeIfPresent([String].self, forKey: .missingCodes) ?? []
        invalidCodes = try c.decodeIfPresent([String].self, forKey: .invalidCodes) ?? []
        conflictCodes = try c.decodeIfPresent([String].self, forKey: .conflictCodes) ?? []
        dataQuality = try c.decodeIfPresent(String.self, forKey: .dataQuality) ?? "insufficient"
        criticalDataQuality = try c.decodeIfPresent(String.self, forKey: .criticalDataQuality)
        contextDataQuality = try c.decodeIfPresent(String.self, forKey: .contextDataQuality)
        qualityDetails = try c.decodeIfPresent([AuctionQualityDetail].self, forKey: .qualityDetails) ?? []
        validationErrors = try c.decodeIfPresent([String].self, forKey: .validationErrors) ?? []
    }

    var dataQualityLabel: String { nkAuctionDataQualityLabel(dataQuality) }
    /// 🔴 两域各一句。`nil` → 「旧版本未细分」(⛔ 不得默认成正常)。
    var criticalQualityLabel: String { nkAuctionDomainQualityLabel(criticalDataQuality) }
    var contextQualityLabel: String { nkAuctionDomainQualityLabel(contextDataQuality) }
    /// 这份报告到底有没有分域信息(老报告恒 `false` → 界面要说「旧版本未细分」)。
    var hasDomainSplit: Bool { criticalDataQuality != nil || contextDataQuality != nil }
    /// 值得单独列出来的逐票账(不合格 / 冲突 / 换过源)。
    var notableQualityDetails: [AuctionQualityDetail] { qualityDetails.filter(\.worthShowing) }
    /// 「抓到几个 / 要几个」一句(⛔ 不省略分母 —— 覆盖率是这块的重点)。
    var coverageText: String { "\(fetchedCodes)/\(requestedCodes)" }
}

/// 指数 / 市场锚点一条(`tsCode` + 名 + 竞价涨跌幅)。
struct AuctionIndexGap: Decodable, Equatable, Identifiable {
    var tsCode: String = ""
    var name: String = ""
    var gapPct: Double? = nil

    var id: String { tsCode }

    enum CodingKeys: String, CodingKey { case tsCode, name, gapPct }

    init(tsCode: String = "", name: String = "", gapPct: Double? = nil) {
        self.tsCode = tsCode; self.name = name; self.gapPct = gapPct
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        gapPct = try c.decodeIfPresent(Double.self, forKey: .gapPct)
    }

    var displayName: String { name.isEmpty ? tsCode : name }
}

/// 小报告第 2 块「市场与主线概览」。`text == nil` = **LLM 没给**(⛔ 不是"没内容"),
/// 配 `textUnavailableReason` 说出口。`anchors` = 竞价强势股 = **市场锚点**:
/// K8 §二十「只解释资金方向,**不取得交易资格**」。
struct AuctionMarketOverview: Decodable, Equatable {
    var indexGaps: [AuctionIndexGap] = []
    var anchors: [AuctionIndexGap] = []
    var text: String? = nil
    var textUnavailableReason: String? = nil
    var anchorsNote: String? = nil

    enum CodingKeys: String, CodingKey {
        case indexGaps, anchors, text, textUnavailableReason, anchorsNote
    }

    init(indexGaps: [AuctionIndexGap] = [], anchors: [AuctionIndexGap] = [],
         text: String? = nil, textUnavailableReason: String? = nil, anchorsNote: String? = nil) {
        self.indexGaps = indexGaps; self.anchors = anchors; self.text = text
        self.textUnavailableReason = textUnavailableReason; self.anchorsNote = anchorsNote
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        indexGaps = try c.decodeIfPresent([AuctionIndexGap].self, forKey: .indexGaps) ?? []
        anchors = try c.decodeIfPresent([AuctionIndexGap].self, forKey: .anchors) ?? []
        text = try c.decodeIfPresent(String.self, forKey: .text)
        textUnavailableReason = try c.decodeIfPresent(String.self, forKey: .textUnavailableReason)
        anchorsNote = try c.decodeIfPresent(String.self, forKey: .anchorsNote)
    }
}

/// 逐票明细一行(键表 = 服务端 `members_json`)。
///
/// 🔴 `hitInvalidation` / `gapUpDeviation` 是**三态**:`true` 命中 / `false` 看过了
/// 没命中 / `nil` **没判**(冻结锚失效 / 卡上无冻结价位 / 开盘价未发布 / 有篮无卡 /
/// 没抓到)。⛔ `nil` **不是 `false`「没问题」** —— 界面上必须讲成两句不同的话,
/// ⛔ 更不许渲染成「过」或一片空白(那才是把"一个字没核对"讲成"核对过了")。
/// ⚠ 两个 `*UndeterminedReason` 是那个 `nil` 的可查原因码;老行(整改前冻的 B 类
/// 快照)没有这两个键 → `nil` → 照实说「原因未记录」。
struct AuctionMemberRow: Decodable, Equatable, Identifiable {
    var tsCode: String = ""
    var name: String = ""
    var role: String? = nil
    var auctionPrice: Double? = nil
    var preClose: Double? = nil
    var gapPct: Double? = nil
    var auctionVolume: Double? = nil
    var auctionAmount: Double? = nil
    var volVsPrev5Frac: Double? = nil
    /// 🔴 **两条独立路径**(用户裁定 P3-70,2026-08-12):`relToSector` 减的是**板块基准**
    /// (板块指数 → 取不到 → ≥3 只同行业对照股中位数)、`relToIndex` 减的是**市场指数**
    /// (沪主板 / 深主板 / 创业板 / 北证50;**科创板按 K8 §三 排除**)。⛔ 禁止同源同值。
    /// 🔴 `nil` = **没有这个读数**,⛔ 不是 0、⛔ 不是「持平」—— 界面必须画**第三态** + 原因。
    var relToSector: Double? = nil
    var relToIndex: Double? = nil
    var relToSectorSource: String = "unavailable"
    var relToSectorReason: String? = nil
    var sectorPeerCodes: [String] = []
    var sectorIndexCode: String? = nil
    var sectorBenchmarkGapPct: Double? = nil
    var industry: String? = nil
    var indexBenchmarkCode: String? = nil
    var indexBenchmarkGapPct: Double? = nil
    var relToIndexReason: String? = nil
    var hitInvalidation: Bool? = nil
    var gapUpDeviation: Bool? = nil
    var hitInvalidationUndeterminedReason: String? = nil
    var gapUpDeviationUndeterminedReason: String? = nil
    var anchorStale: Bool = false
    var planFit: String = "unknown"
    var dataQuality: String = "insufficient"
    var volumeNote: String? = nil
    /// 🔴 V2.4.0 P2.1/P2.2:这条读数**从哪来、是不是今天的、两源打不打架**。
    /// ⚠ 空串 / `nil` = **老快照没记这一位**,⛔ 不许渲染成「校验通过」。
    var quoteFreshness: String = ""
    var quoteStatus: String = ""
    var quoteSource: String? = nil
    var quoteTimestamp: String? = nil
    var sourceDegraded: Bool = false
    var sourceConflict: String? = nil
    var validationErrors: [String] = []

    var id: String { tsCode }

    enum CodingKeys: String, CodingKey {
        case tsCode, name, role, auctionPrice, preClose, gapPct, auctionVolume, auctionAmount
        case volVsPrev5Frac, relToSector, relToIndex, hitInvalidation, gapUpDeviation
        case relToSectorSource, relToSectorReason, sectorPeerCodes, sectorIndexCode
        case sectorBenchmarkGapPct, industry, indexBenchmarkCode, indexBenchmarkGapPct
        case relToIndexReason
        case hitInvalidationUndeterminedReason, gapUpDeviationUndeterminedReason
        case anchorStale, planFit, dataQuality, volumeNote
        case quoteFreshness, quoteStatus, quoteSource, quoteTimestamp
        case sourceDegraded, sourceConflict, validationErrors
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        role = try c.decodeIfPresent(String.self, forKey: .role)
        auctionPrice = try c.decodeIfPresent(Double.self, forKey: .auctionPrice)
        preClose = try c.decodeIfPresent(Double.self, forKey: .preClose)
        gapPct = try c.decodeIfPresent(Double.self, forKey: .gapPct)
        auctionVolume = try c.decodeIfPresent(Double.self, forKey: .auctionVolume)
        auctionAmount = try c.decodeIfPresent(Double.self, forKey: .auctionAmount)
        volVsPrev5Frac = try c.decodeIfPresent(Double.self, forKey: .volVsPrev5Frac)
        relToSector = try c.decodeIfPresent(Double.self, forKey: .relToSector)
        relToIndex = try c.decodeIfPresent(Double.self, forKey: .relToIndex)
        relToSectorSource = try c.decodeIfPresent(String.self, forKey: .relToSectorSource) ?? "unavailable"
        relToSectorReason = try c.decodeIfPresent(String.self, forKey: .relToSectorReason)
        sectorPeerCodes = try c.decodeIfPresent([String].self, forKey: .sectorPeerCodes) ?? []
        sectorIndexCode = try c.decodeIfPresent(String.self, forKey: .sectorIndexCode)
        sectorBenchmarkGapPct = try c.decodeIfPresent(Double.self, forKey: .sectorBenchmarkGapPct)
        industry = try c.decodeIfPresent(String.self, forKey: .industry)
        indexBenchmarkCode = try c.decodeIfPresent(String.self, forKey: .indexBenchmarkCode)
        indexBenchmarkGapPct = try c.decodeIfPresent(Double.self, forKey: .indexBenchmarkGapPct)
        relToIndexReason = try c.decodeIfPresent(String.self, forKey: .relToIndexReason)
        hitInvalidation = try c.decodeIfPresent(Bool.self, forKey: .hitInvalidation)
        gapUpDeviation = try c.decodeIfPresent(Bool.self, forKey: .gapUpDeviation)
        hitInvalidationUndeterminedReason =
            try c.decodeIfPresent(String.self, forKey: .hitInvalidationUndeterminedReason)
        gapUpDeviationUndeterminedReason =
            try c.decodeIfPresent(String.self, forKey: .gapUpDeviationUndeterminedReason)
        anchorStale = try c.decodeIfPresent(Bool.self, forKey: .anchorStale) ?? false
        planFit = try c.decodeIfPresent(String.self, forKey: .planFit) ?? "unknown"
        dataQuality = try c.decodeIfPresent(String.self, forKey: .dataQuality) ?? "insufficient"
        volumeNote = try c.decodeIfPresent(String.self, forKey: .volumeNote)
        quoteFreshness = try c.decodeIfPresent(String.self, forKey: .quoteFreshness) ?? ""
        quoteStatus = try c.decodeIfPresent(String.self, forKey: .quoteStatus) ?? ""
        quoteSource = try c.decodeIfPresent(String.self, forKey: .quoteSource)
        quoteTimestamp = try c.decodeIfPresent(String.self, forKey: .quoteTimestamp)
        sourceDegraded = try c.decodeIfPresent(Bool.self, forKey: .sourceDegraded) ?? false
        sourceConflict = try c.decodeIfPresent(String.self, forKey: .sourceConflict)
        validationErrors = try c.decodeIfPresent([String].self, forKey: .validationErrors) ?? []
    }

    /// 🔴 这条读数的**来源与校验**一句(V2.4.0 P2.1/P2.2)。**没什么可说时返回 `nil`**
    /// (读数合格、没换源、没冲突)——⛔ 但只要有一样不对,就必须说出口。
    var quoteProvenanceNote: String? {
        var parts: [String] = []
        if !quoteFreshness.isEmpty && quoteFreshness != "fresh" {
            parts.append(nkAuctionQuoteFreshnessLabel(quoteFreshness))
        }
        if sourceDegraded {
            parts.append("主源不可用,本次用的是备源(\(quoteSource ?? "未记录"))")
        }
        if let cf = sourceConflict, !cf.isEmpty {
            parts.append(nkAuctionConflictLabel(cf))
        }
        if !validationErrors.isEmpty {
            parts.append("校验未过:" + validationErrors.map(nkAuctionValidationErrorLabel)
                .joined(separator: "、"))
        }
        guard !parts.isEmpty else { return nil }
        let ts = (quoteTimestamp?.isEmpty == false) ? "源时间 \(quoteTimestamp!);" : ""
        return ts + parts.joined(separator: ";")
    }

    /// 逐票行右端那枚判定徽标的文字。**关键字段缺失 → 「中性｜数据不足」**
    /// (K8 §二十 逐字);其余情况给 `planFit` 的中文。
    var statusText: String {
        dataQuality == "insufficient" ? "中性｜数据不足" : nkAuctionPlanFitLabel(planFit)
    }
    var displayName: String { name.isEmpty ? tsCode : name }
    var roleLabel: String? { role.map(nkRoleLabel) }

    /// 🔴 **第三态**:这两项里至少有一项**没判**。界面据此画第三态徽标 + 一句原因,
    /// ⛔ 绝不许渲染成「过」或空白(V2.3.3 复审 🔴-1)。
    var hasUndeterminedInvalidation: Bool { hitInvalidation == nil || gapUpDeviation == nil }

    /// 「没判」的那一句话(哪一项 + 为什么)。**判出来了就返回 `nil`**(不出这一行)。
    ///
    /// ⚠ 锚失效那一句由逐票行下方**另一条专门的文案**负责(它还要解释"疑似除权除息"
    /// 是什么意思),这里就不重复了 —— 但 `anchorStale == false` 时的每一种「没判」
    /// 都必须落在这里,⛔ 不许有哪一种悄悄消失。
    var undeterminedNote: String? {
        guard !anchorStale, hasUndeterminedInvalidation else { return nil }
        var parts: [String] = []
        if hitInvalidation == nil {
            parts.append("失效位「没判」(\(nkAuctionUndeterminedReasonLabel(hitInvalidationUndeterminedReason)))")
        }
        if gapUpDeviation == nil {
            parts.append("高开偏离「没判」(\(nkAuctionUndeterminedReasonLabel(gapUpDeviationUndeterminedReason)))")
        }
        return parts.joined(separator: ";") + " —— 这不是「无异常」,是一个字都没核对。"
    }

    // MARK: - 相对强弱两条独立读数(用户裁定 P3-70)

    /// 「相对板块」那一行的**完整一句话**:有值 → 数 + 减的是哪一组;
    /// `nil` → **第三态**「未取得 + 为什么」。
    /// 🔴 ⛔ 绝不返回 `0` 或空串:那是把「没有」讲成「持平」。
    ///
    /// 🔴 `sectorPeerMin` 由**服务端**下发(`relStrength.sector_peer_min`),
    /// ⛔ 客户端不许硬编那个裁定值(定向复审 🔵-1)。
    func relToSectorText(sectorPeerMin: Int?) -> String {
        guard let v = relToSector else {
            return "相对板块 未取得 —— "
                + nkAuctionRelReasonLabel(relToSectorReason, sectorPeerMin: sectorPeerMin)
                + "(不是「持平」)"
        }
        if relToSectorSource == "peer_median" {
            let ind = (industry?.isEmpty == false) ? industry! : "未记录"
            return "相对板块 \(NKFmt.ratioPct(v))(对照:同行业「\(ind)」\(sectorPeerCodes.count) 只中位)"
        }
        if relToSectorSource == "unavailable" {
            // 🔴 **有值却说「未取得」是自相矛盾**(定向复审 🔵-2):整改前冻的老行有
            // `rel_to_sector` 的值、没有 `rel_to_sector_source`,服务端补 `unavailable`
            // → 会印出「相对板块 +1.42%(对照:未取得 未记录)」。老值是**旧口径**
            // (那时 sector 与 index 同源同值)—— 如实说出来,⛔ 别装作取到了新口径。
            return "相对板块 \(NKFmt.ratioPct(v))(这条来自旧口径,当时未记录对照来源;"
                + "新口径的板块基准是同行业对照股中位)"
        }
        let src = nkAuctionSectorBenchSourceLabel(relToSectorSource)
        let code = (sectorIndexCode?.isEmpty == false) ? sectorIndexCode! : "未记录"
        return "相对板块 \(NKFmt.ratioPct(v))(对照:\(src) \(code))"
    }

    /// 兼容入口(不知道服务端下限时用)。⚠ 有 `relStrength` 可读的地方一律走上面那个
    /// 带参版本 —— 这里拿不到下限,只能说一句不带数字的话。
    var relToSectorText: String { relToSectorText(sectorPeerMin: nil) }

    /// 「相对市场」那一行的**完整一句话**。科创板恒是第三态(K8 §三 排除,⛔ 不 fallback)。
    var relToIndexText: String {
        guard let v = relToIndex else {
            return "相对市场 未取得 —— \(nkAuctionRelReasonLabel(relToIndexReason))(不是「持平」)"
        }
        let code = (indexBenchmarkCode?.isEmpty == false) ? indexBenchmarkCode! : "未记录"
        return "相对市场 \(NKFmt.ratioPct(v))(对照:市场指数 \(code))"
    }

    /// 这一行的相对强弱**有没有第三态**(界面据此把那一行画成琥珀色)。
    var relToSectorMissing: Bool { relToSector == nil }
    var relToIndexMissing: Bool { relToIndex == nil }
}

/// 小报告第 3 块「篮子与逐票结论」一条。
///
/// 🔴 `verdictRaw`(模型原话)与 `verdict`(夹逼后)**两者都在**:不同的那些行就是
/// 「模型说了什么 vs 系统最终讲了什么」的账。`clampedBy` 非空必须当面说出口。
struct AuctionVerdict: Decodable, Equatable, Identifiable {
    var basketId: Int = 0
    var basketKey: String = ""
    var name: String = ""
    var coveredTier: Int = 0
    var engineCode: String? = nil
    var engineVersion: String? = nil
    var skeletonVersion: String = ""
    var regimeAtD0: String? = nil
    var dataQuality: String = "insufficient"
    var verdict: String = "pending_explanation"
    var verdictRaw: String? = nil
    var clampedBy: String? = nil
    var reasons: [String] = []
    var members: [AuctionMemberRow] = []
    var sectorSync: NKJSON = .object([:])
    var relStrength: NKJSON = .object([:])
    var history: NKJSON = .object([:])
    var planConsistency: NKJSON = .object([:])
    var hitInvalidation: [String] = []
    var manualNoteAttached: Bool = false
    var llmStage: String = ""
    /// 🔴 V2.4.0 P2.3 分域质量。`nil` = **旧版本未细分**(⛔ 不得默认成正常)。
    /// ⚠ 只有**关键域**会把结论夹成中性;上下文域降级只降置信度 + 披露缺失。
    var criticalDataQuality: String? = nil
    var contextDataQuality: String? = nil
    var qualityDetail: NKJSON = .object([:])

    var id: Int { basketId }

    enum CodingKeys: String, CodingKey {
        case basketId, basketKey, name, coveredTier, engineCode, engineVersion, skeletonVersion
        case regimeAtD0, dataQuality, verdict, verdictRaw, clampedBy, reasons, members
        case sectorSync, relStrength, history, planConsistency, hitInvalidation
        case manualNoteAttached, llmStage
        case criticalDataQuality, contextDataQuality, qualityDetail
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId) ?? 0
        basketKey = try c.decodeIfPresent(String.self, forKey: .basketKey) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        coveredTier = try c.decodeIfPresent(Int.self, forKey: .coveredTier) ?? 0
        engineCode = try c.decodeIfPresent(String.self, forKey: .engineCode)
        engineVersion = try c.decodeIfPresent(String.self, forKey: .engineVersion)
        skeletonVersion = try c.decodeIfPresent(String.self, forKey: .skeletonVersion) ?? ""
        regimeAtD0 = try c.decodeIfPresent(String.self, forKey: .regimeAtD0)
        dataQuality = try c.decodeIfPresent(String.self, forKey: .dataQuality) ?? "insufficient"
        verdict = try c.decodeIfPresent(String.self, forKey: .verdict) ?? "pending_explanation"
        verdictRaw = try c.decodeIfPresent(String.self, forKey: .verdictRaw)
        clampedBy = try c.decodeIfPresent(String.self, forKey: .clampedBy)
        reasons = try c.decodeIfPresent([String].self, forKey: .reasons) ?? []
        members = try c.decodeIfPresent([AuctionMemberRow].self, forKey: .members) ?? []
        sectorSync = try c.decodeIfPresent(NKJSON.self, forKey: .sectorSync) ?? .object([:])
        relStrength = try c.decodeIfPresent(NKJSON.self, forKey: .relStrength) ?? .object([:])
        history = try c.decodeIfPresent(NKJSON.self, forKey: .history) ?? .object([:])
        planConsistency = try c.decodeIfPresent(NKJSON.self, forKey: .planConsistency) ?? .object([:])
        hitInvalidation = try c.decodeIfPresent([String].self, forKey: .hitInvalidation) ?? []
        manualNoteAttached = try c.decodeIfPresent(Bool.self, forKey: .manualNoteAttached) ?? false
        llmStage = try c.decodeIfPresent(String.self, forKey: .llmStage) ?? ""
        criticalDataQuality = try c.decodeIfPresent(String.self, forKey: .criticalDataQuality)
        contextDataQuality = try c.decodeIfPresent(String.self, forKey: .contextDataQuality)
        qualityDetail = try c.decodeIfPresent(NKJSON.self, forKey: .qualityDetail) ?? .object([:])
    }

    var verdictLabel: String { nkAuctionVerdictLabel(verdict) }
    var dataQualityLabel: String { nkAuctionDataQualityLabel(dataQuality) }
    /// 🔴 两域各一句。`nil` → 「旧版本未细分」——⛔ 绝不许当成「正常」
    /// (V2.3.3 及更早的行里 `dataQuality` 是**整体**质量,不是关键域)。
    var criticalQualityLabel: String { nkAuctionDomainQualityLabel(criticalDataQuality) }
    var contextQualityLabel: String { nkAuctionDomainQualityLabel(contextDataQuality) }
    var hasDomainSplit: Bool { criticalDataQuality != nil || contextDataQuality != nil }
    /// 关键域缺了哪些码(审计层展示用);没有分域信息 → 空数组。
    var criticalMissingCodes: [String] {
        (qualityDetail["critical"]?["missing"]?.arrayValue ?? []).compactMap(\.stringValue)
    }
    /// 上下文域缺了哪些码。🔴 它们**不改变结论**,但必须披露。
    var contextMissingCodes: [String] {
        (qualityDetail["context"]?["missing"]?.arrayValue ?? []).compactMap(\.stringValue)
    }
    /// 引擎归属一句(`engineVersion` 优先;老篮子两个都为空 → `nil`,如实不写)。
    var engineText: String? {
        if let v = engineVersion, !v.isEmpty { return v }
        if let c = engineCode, !c.isEmpty { return c }
        return nil
    }
    /// 「模型说了什么 vs 系统最终讲了什么」——**被夹逼过**才有这一句。
    var clampText: String? {
        guard let by = clampedBy, !by.isEmpty else { return nil }
        let raw = (verdictRaw?.isEmpty == false) ? nkAuctionVerdictLabel(verdictRaw!) : "未给"
        return "模型给的是「\(raw)」,经机械夹逼闸(\(nkAuctionClampLabel(by)))后系统记为「\(verdictLabel)」。"
    }
    /// 当期有效样本天数(**读数**)。
    /// 🔴 ⛔ 客户端**永远不许**在这个数上自己设「够不够」的门槛(V2.3.3 复审 🔴-2a 逮到过
    /// 一次 `days <= 5`)。够不够由**服务端按用户裁定的 15 天**判好后下发
    /// (`historySampleSufficient`),客户端只负责显示。
    var historyDaysAvailable: Int? { history["history_days_available"]?.intValue }
    /// 回看窗口的**交易日**数(用户裁定 P3-69:最近 20 个有效交易日)。
    var historyLookbackTradingDays: Int? { history["history_lookback_trading_days"]?.intValue }
    /// 回看的**自然日上界**(裁定 P3-69:最多向前回溯 60 个自然日补齐)。
    /// ⚠ 它封顶了上面那个天数 —— 不一起显示,读者会把"窗口内可得"读成"全史可得"。
    var historyLookbackDays: Int? { history["history_lookback_days"]?.intValue }
    /// 回看窗口的诚实披露(**服务端下发的文案单一源**,⛔ 客户端不许自己写这句)。
    var historyLookbackNote: String? {
        guard let s = history["history_lookback_note"]?.stringValue, !s.isEmpty else { return nil }
        return s
    }
    /// 🔴 「历史样本够不够」的**服务端判定**(裁定 P3-69:`n ≥ 15` 才允许形成历史比较)。
    /// ⚠ **老行没有这个键 → `nil`**:那时如实什么都不说(⛔ 不许默认成"够"或"不够")。
    var historySampleSufficient: Bool? { history["history_sample_sufficient"]?.boolValue }
    /// 「历史样本不足」的诚实披露(服务端文案单一源;`n ≥ 15` 时服务端不发这个键)。
    var historyInsufficientNote: String? {
        guard let s = history["history_insufficient_note"]?.stringValue, !s.isEmpty else { return nil }
        return s
    }
    /// 板块对照股取样域的诚实披露(服务端文案单一源,⛔ 客户端不许自己写这句)。
    var sectorPeerPoolNote: String? {
        guard let s = relStrength["sector_peer_pool_note"]?.stringValue, !s.isEmpty else { return nil }
        return s
    }

    /// 🔴 板块对照股的**下限**(用户裁定值,单一源在服务端)。⛔ 客户端不许硬编
    /// (定向复审 🔵-1);老行没这个键 → `nil` → 文案退回不带数字的说法。
    var sectorPeerMin: Int? { relStrength["sector_peer_min"]?.intValue }

    /// 🔴 **逐票的历史样本**(定向复审 🔴-1):`history_days_available` 从「全篮日期
    /// 并集」改成「逐票最小值」之后,**哪一只不够**只能在这里查 —— 篮级那一个数
    /// ⛔ 不足以说明问题(一只 20 天 + 一只 2 天,篮级只会说「2 天」,看不出是谁)。
    var historyPerMember: [AuctionMemberHistory] {
        let rows = history["per_member"]?.objectValue ?? [:]
        return (history["history_days_per_member"]?.arrayValue ?? []).compactMap {
            AuctionMemberHistory(json: $0, rows: rows)
        }
    }

    /// 某一只票的历史样本(逐票行下面那句话用)。查不到 → `nil` → **什么都不说**
    /// (⛔ 不许默认成"够"或"不够")。
    func historyFor(_ tsCode: String) -> AuctionMemberHistory? {
        historyPerMember.first { $0.tsCode == tsCode }
    }

    /// 样本不足的成员代码(服务端算好后下发;老行没这个键 → 空)。
    var historyInsufficientCodes: [String] {
        (history["history_insufficient_codes"]?.arrayValue ?? []).compactMap { $0.stringValue }
    }
}

/// 🔴 一只票的「自身历史竞价样本」(定向复审 🔴-1 / 🔵-3)。
///
/// 裁定 P3-69 原文:「`n ≥ 15`,允许形成历史比较;`n < 15`,标记『历史样本不足』,
/// **只展示原始值**」—— 后半句的落点就是这里(`rows`)。
/// 🔴 ⛔ 客户端不判「够不够」:`sampleSufficient` 是**服务端**按裁定值判好后下发的。
struct AuctionMemberHistory: Equatable, Identifiable {
    var tsCode: String
    var daysAvailable: Int
    var sampleSufficient: Bool
    /// 窗口内逐日原始值(样本不足时界面要**逐日列出来**)。
    var rows: [AuctionHistoryRow] = []
    /// 样本够时服务端给的对照读数(最低 / 中位 / 最高),按指标分组。
    var readings: [String: AuctionHistoryStat] = [:]

    var id: String { tsCode }

    init?(json: NKJSON, rows all: [String: NKJSON]) {
        guard let code = json["ts_code"]?.stringValue, !code.isEmpty else { return nil }
        tsCode = code
        daysAvailable = json["days_available"]?.intValue ?? 0
        sampleSufficient = json["sample_sufficient"]?.boolValue ?? false
        rows = (all[code]?.arrayValue ?? []).compactMap(AuctionHistoryRow.init(json:))
        for (k, v) in (json["comparison_readings"]?.objectValue ?? [:]) {
            if let s = AuctionHistoryStat(json: v) { readings[k] = s }
        }
    }

    /// 这只票历史样本那一句话。
    /// - 够 → 说清楚是几天 + 窗口内的对照读数(**有许可就得有证据**,同 prompt 那侧);
    /// - 不够 → 说清楚不够 + **逐日原始值**(裁定原文「只展示原始值」);
    /// - 一天都没有 → 说「一条都没有」,⛔ 不许写成「跟平时一样」。
    var noteText: String {
        if sampleSufficient {
            let body = ["auction_volume", "auction_amount", "gap_pct"].compactMap { key -> String? in
                guard let s = readings[key] else { return nil }
                return "\(AuctionMemberHistory.metricLabel(key)) \(s.text(key: key))"
            }.joined(separator: "；")
            let tail = body.isEmpty ? "(窗口内这几项读数整列都缺)" : ":\(body)"
            return "自身历史竞价样本 \(daysAvailable) 天,可作历史比较\(tail)"
        }
        if rows.isEmpty {
            return "自身历史竞价样本 0 天:窗口内一条历史竞价快照都没有 —— 没有可比的东西"
                + "(不是「跟平时一样」)。"
        }
        let body = rows.map { $0.text }.joined(separator: "；")
        return "自身历史竞价样本 \(daysAvailable) 天(样本不足,不作比较结论,只列原始值):\(body)"
    }

    static func metricLabel(_ key: String) -> String {
        switch key {
        case "auction_volume": return "量"
        case "auction_amount": return "额"
        case "gap_pct": return "涨跌"
        default: return key
        }
    }
}

/// 一天历史竞价快照的原始值。
struct AuctionHistoryRow: Equatable {
    var tradeDate: String
    var auctionVolume: Double?
    var auctionAmount: Double?
    var gapPct: Double?

    init?(json: NKJSON) {
        guard let d = json["trade_date"]?.stringValue, !d.isEmpty else { return nil }
        tradeDate = d
        auctionVolume = json["auction_volume"]?.doubleValue
        auctionAmount = json["auction_amount"]?.doubleValue
        gapPct = json["gap_pct"]?.doubleValue
    }

    /// 一天一段。**算不出就写「算不出」**,⛔ 不拿 0 顶。
    var text: String {
        let g = gapPct.map { NKFmt.ratioPct($0) } ?? "算不出"
        let v = auctionVolume.map { NKFmt.amount($0) } ?? "算不出"
        return "\(tradeDate) \(g) 量 \(v)"
    }
}

/// 窗口内某个指标的对照读数(最低 / 中位 / 最高)。**服务端算好后下发**。
struct AuctionHistoryStat: Equatable {
    var min: Double?
    var median: Double?
    var max: Double?
    var observed: Int?

    init?(json: NKJSON) {
        guard json.objectValue != nil else { return nil }
        min = json["min"]?.doubleValue
        median = json["median"]?.doubleValue
        max = json["max"]?.doubleValue
        observed = json["observed"]?.intValue
    }

    func text(key: String) -> String {
        let f: (Double?) -> String = { v in
            guard let v else { return "算不出" }
            return key == "gap_pct" ? NKFmt.ratioPct(v) : NKFmt.amount(v)
        }
        return "最低 \(f(min)) / 中位 \(f(median)) / 最高 \(f(max))"
    }
}

/// 小报告第 4 块「异常与风险」一条。`kind` 是枚举码,中文走 `nkAuctionRiskKindLabel`。
struct AuctionRiskItem: Decodable, Equatable, Identifiable {
    var kind: String = ""
    var text: String = ""

    var id: String { kind + "|" + text }

    enum CodingKeys: String, CodingKey { case kind, text }

    init(kind: String = "", text: String = "") { self.kind = kind; self.text = text }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? ""
        text = try c.decodeIfPresent(String.self, forKey: .text) ?? ""
    }

    var kindLabel: String { nkAuctionRiskKindLabel(kind) }
}

/// `GET /auction` 的整份响应 = **竞价小报告五块**。
///
/// 🔴 **端点 404 时客户端根本不画这张卡**(⛔ 不画一张空卡,那是噪声)——
/// 所以这里没有 `available` 位:拿到这个对象就意味着"今天真的跑过了"。
/// 🔴 `manualNote` 的文案本体由**服务端下发**(K8 §二十 固定文案),
/// ⛔ 客户端不许自己写那段字。
struct AuctionPayload: Decodable, Equatable {
    var tradeDate: String = ""
    var d0Date: String = ""
    var dataStatus: AuctionDataStatus = AuctionDataStatus()
    var marketOverview: AuctionMarketOverview = AuctionMarketOverview()
    var baskets: [AuctionVerdict] = []
    var basketsUnavailableReason: String? = nil
    var risks: [AuctionRiskItem] = []
    var manualNote: String? = nil
    var proxySampleNote: String = ""
    var llmStage: String = ""
    var notes: [String] = []

    enum CodingKeys: String, CodingKey {
        case tradeDate, d0Date, dataStatus, marketOverview, baskets, basketsUnavailableReason
        case risks, manualNote, proxySampleNote, llmStage, notes
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        d0Date = try c.decodeIfPresent(String.self, forKey: .d0Date) ?? ""
        dataStatus = try c.decodeIfPresent(AuctionDataStatus.self, forKey: .dataStatus) ?? AuctionDataStatus()
        marketOverview = try c.decodeIfPresent(AuctionMarketOverview.self, forKey: .marketOverview)
            ?? AuctionMarketOverview()
        baskets = try c.decodeIfPresent([AuctionVerdict].self, forKey: .baskets) ?? []
        basketsUnavailableReason = try c.decodeIfPresent(String.self, forKey: .basketsUnavailableReason)
        risks = try c.decodeIfPresent([AuctionRiskItem].self, forKey: .risks) ?? []
        manualNote = try c.decodeIfPresent(String.self, forKey: .manualNote)
        proxySampleNote = try c.decodeIfPresent(String.self, forKey: .proxySampleNote) ?? ""
        llmStage = try c.decodeIfPresent(String.self, forKey: .llmStage) ?? ""
        notes = try c.decodeIfPresent([String].self, forKey: .notes) ?? []
    }

    var confirmCount: Int { baskets.filter { $0.verdict == "confirm" }.count }
    var neutralCount: Int { baskets.filter { $0.verdict == "neutral" }.count }
    var vetoCount: Int { baskets.filter { $0.verdict == "veto" }.count }
    var pendingCount: Int { baskets.filter { $0.verdict == "pending_explanation" }.count }
    /// 命中 D0 冻结失效位的票(去重,**机械事实**,不受 LLM 缺席与夹逼影响)。
    var hitInvalidationCodes: [String] {
        Array(Set(baskets.flatMap(\.hitInvalidation))).sorted()
    }
    var llmStageLabel: String { nkAuctionLlmStageLabel(llmStage) }
}
